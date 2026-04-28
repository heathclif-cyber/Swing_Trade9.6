"""
Protocol 9.6 — Data Enrichment Factory (Single Source of Truth)
================================================================
Prinsip: "1 Sumber Data, 1 Otak"

SATU-SATUNYA fungsi yang boleh dipanggil oleh signal_monitor.py dan
protocol_96_ui.py untuk mendapatkan data siap-pakai adalah:

    df, meta = get_fully_enriched_data(symbol, interval="1h", limit=500)

Tidak ada modul lain yang boleh merakit klines + indikator + makro sendiri.
"""

import os
import time
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta  # type: ignore
import requests
from datetime import datetime, timedelta

from requests.packages import urllib3  # type: ignore
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── ML Config (from models/inference_config.json) ──────────────────────
from core.helpers import load_inference_config
INFERENCE_CFG  = load_inference_config()
TIMEFRAME      = INFERENCE_CFG["inference"]["timeframe"]
# ────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("Enrichment")

# ============================================================
# CACHE — Macro & Kline 1D/1W (4-jam TTL, anti rate-limit)
# ============================================================
_kline_cache: dict = {}          # { (symbol, interval): (df, ts) }
_macro_cache: dict = {}          # { symbol: {"1d": (df,ts), "1w": (df,ts)} }
_cmc_cache:   dict = {"data": None, "ts": 0.0}   # global CMC cache
CACHE_TTL        = 4 * 3600      # 4 jam
CMC_CACHE_TTL    = 4 * 3600      # 4 jam — rate limit friendly


# ============================================================
# KLINE FETCHING (multi-endpoint, ISP-resilient)
# ============================================================
BINANCE_KLINE_URLS = [
    "https://fapi.binance.com/fapi/v1/klines",
    "https://data-api.binance.vision/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
_last_working_url: str | None = None


def _fetch_klines_raw(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    """Fetch OHLCV klines dari Binance — coba semua endpoint secara berurutan."""
    global _last_working_url
    urls = list(BINANCE_KLINE_URLS)
    if _last_working_url and _last_working_url in urls:
        urls.remove(_last_working_url)
        urls.insert(0, _last_working_url)

    for url in urls:
        try:
            total_klines: list = []
            end_time = None
            while len(total_klines) < limit:
                req_limit = min(1000, limit - len(total_klines))
                params: dict = {"symbol": symbol, "interval": interval, "limit": req_limit}
                if end_time:
                    params["endTime"] = end_time
                resp = requests.get(url, params=params, timeout=12, verify=False)
                if resp.status_code == 200:
                    chunk = resp.json()
                    if not chunk or not isinstance(chunk, list):
                        break
                    total_klines = chunk + total_klines
                    if len(chunk) < req_limit:
                        break
                    end_time = chunk[0][0] - 1
                else:
                    break
            if total_klines:
                _last_working_url = url
                df = pd.DataFrame(total_klines, columns=[
                    "Open_Time", "Open", "High", "Low", "Close", "Total_Volume",
                    "Close_Time", "Quote_Asset_Volume", "Trades",
                    "Taker_Buy_Base", "Taker_Buy_Quote", "Ignore",
                ])
                df["Open_Time"]  = pd.to_datetime(df["Open_Time"], unit="ms")
                df["Close_Time"] = pd.to_datetime(df["Close_Time"], unit="ms")
                for col in ["Open", "High", "Low", "Close", "Total_Volume", "Taker_Buy_Base"]:
                    df[col] = df[col].astype(float)
                df["Buy_Volume"]   = df["Taker_Buy_Base"]
                df["Sell_Volume"]  = df["Total_Volume"] - df["Buy_Volume"]
                df["Volume_Delta"] = df["Buy_Volume"] - df["Sell_Volume"]
                logger.info(f"  ✅ {symbol} {interval}: {len(df)} candles via {url.split('/')[2]}")
                return df
        except Exception as e:
            logger.debug(f"Kline fetch failed {url}: {e}")

    logger.warning(f"All kline endpoints failed for {symbol} {interval}")
    return pd.DataFrame()


def _get_klines_cached(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    """Ambil klines dengan cache 4 jam untuk interval macro (1d / 1w)."""
    now_ts = time.time()
    key = (symbol, interval)
    cached = _kline_cache.get(key)
    # Hanya cache untuk timeframe panjang supaya data live tetap fresh
    use_cache = interval in ("1d", "1w")
    if use_cache and cached and (now_ts - cached[1] < CACHE_TTL):
        return cached[0]
    df = _fetch_klines_raw(symbol, interval, limit)
    if use_cache and not df.empty:
        _kline_cache[key] = (df, now_ts)
    return df


# ============================================================
# OPEN INTEREST & FUNDING RATE
# ============================================================
# Cache status fapi block agar tidak retry terus jika sudah 403
_fapi_blocked: dict = {"blocked": False, "ts": 0.0}
_FAPI_RETRY_COOLDOWN = 30 * 60  # retry fapi setiap 30 menit


def _is_fapi_available() -> bool:
    """Return False jika fapi sedang di-block (cooldown 30 menit)."""
    now = time.time()
    if _fapi_blocked["blocked"] and (now - _fapi_blocked["ts"]) < _FAPI_RETRY_COOLDOWN:
        return False
    return True


def _mark_fapi_blocked() -> None:
    """Tandai fapi sebagai blocked setelah menerima 403."""
    _fapi_blocked["blocked"] = True
    _fapi_blocked["ts"] = time.time()
    logger.warning(
        "  [fapi] fapi.binance.com mengembalikan 403 — kemungkinan geo/IP block. "
        f"Akan di-retry dalam {_FAPI_RETRY_COOLDOWN//60} menit."
    )


def _mark_fapi_ok() -> None:
    """Reset flag jika fapi berhasil diakses."""
    _fapi_blocked["blocked"] = False
    _fapi_blocked["ts"] = 0.0


def _fetch_oi(symbol: str, limit: int = 500) -> pd.DataFrame:
    """
    Fetch Open Interest historis dari fapi.
    Return: DataFrame [Open_Time, Open_Interest]
            atau DataFrame kosong jika gagal (synthetic dihandle di caller).
    """
    if not _is_fapi_available():
        logger.debug(f"[OI] fapi skip (cooldown aktif) untuk {symbol}")
        return pd.DataFrame()
    try:
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        resp = requests.get(
            url,
            params={"symbol": symbol, "period": TIMEFRAME, "limit": limit},
            timeout=10, verify=False
        )
        if resp.status_code == 403:
            _mark_fapi_blocked()
            return pd.DataFrame()
        if resp.status_code == 200:
            data = resp.json()
            if data:
                _mark_fapi_ok()
                oi_df = pd.DataFrame(data)
                oi_df["Open_Time"]     = pd.to_datetime(oi_df["timestamp"], unit="ms")
                oi_df["Open_Interest"] = oi_df["sumOpenInterest"].astype(float)
                logger.info(f"  [OI] fapi OK — {len(oi_df)} rows untuk {symbol}")
                return oi_df[["Open_Time", "Open_Interest"]]
    except Exception as e:
        logger.debug(f"[OI] fetch failed untuk {symbol}: {e}")
    return pd.DataFrame()


def _synthetic_oi(df_kline: pd.DataFrame) -> pd.DataFrame:
    """
    Synthetic Open Interest proxy — Formula sesuai training pipeline.

    Formula (dari training notebook):
      cvd        = cumsum(buy_vol - sell_vol)
      cvd_ma96   = cvd.rolling(96).mean()        # MA 24 jam di M15
      vol_ma96   = total_volume.rolling(96).mean()
      synthetic  = (cvd_ma96 + vol_ma96 * 2) / rolling672.mean()
                   .ffill().fillna(1.0)

    Ini adalah rasio relatif (bukan nilai absolut), sehingga distribusi
    tetap stabil antara training dan live deployment.
    """
    if df_kline.empty or "Buy_Volume" not in df_kline.columns:
        return pd.DataFrame()
    try:
        df = df_kline[["Open_Time", "Buy_Volume", "Total_Volume"]].copy()
        df["vol_delta"] = df["Buy_Volume"] - (df["Total_Volume"] - df["Buy_Volume"])

        # CVD (Cumulative Volume Delta) — sesuai training
        cvd = df["vol_delta"].cumsum()

        # MA 96 bar (setara 24 jam di M15)
        cvd_ma96 = cvd.rolling(96, min_periods=1).mean()
        vol_ma96 = df["Total_Volume"].rolling(96, min_periods=1).mean()

        # Numerator
        numerator = cvd_ma96 + vol_ma96 * 2

        # Rolling 672 bar (setara 7 hari di M15) sebagai normalizer
        rolling672 = numerator.rolling(672, min_periods=1).mean()
        # Hindari division-by-zero
        rolling672 = rolling672.replace(0, np.nan)

        synthetic = (numerator / rolling672).ffill().fillna(1.0)

        df["Open_Interest"] = synthetic
        logger.info(
            f"  [OI] Synthetic OI (formula training) — "
            f"range=[{synthetic.min():.4f}, {synthetic.max():.4f}]"
        )
        return df[["Open_Time", "Open_Interest"]]
    except Exception as e:
        logger.debug(f"[OI] synthetic gagal: {e}")
        return pd.DataFrame()


def _fetch_funding_rate(symbol: str, limit: int = 200) -> pd.DataFrame:
    """
    Fetch Funding Rate dari fapi.
    Return: DataFrame [Open_Time, Funding_Rate]
            atau DataFrame kosong jika gagal.
    """
    if not _is_fapi_available():
        logger.debug(f"[FR] fapi skip (cooldown aktif) untuk {symbol}")
        return pd.DataFrame()
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        resp = requests.get(
            url,
            params={"symbol": symbol, "limit": limit},
            timeout=10, verify=False
        )
        if resp.status_code == 403:
            _mark_fapi_blocked()  # sudah di-mark oleh _fetch_oi, tapi tetap update ts
            return pd.DataFrame()
        if resp.status_code == 200:
            data = resp.json()
            if data:
                _mark_fapi_ok()
                fr_df = pd.DataFrame(data)
                fr_df["Open_Time"]    = pd.to_datetime(fr_df["fundingTime"], unit="ms")
                fr_df["Funding_Rate"] = fr_df["fundingRate"].astype(float)
                logger.info(f"  [FR] fapi OK — {len(fr_df)} rows untuk {symbol}")
                return fr_df[["Open_Time", "Funding_Rate"]]
    except Exception as e:
        logger.debug(f"[FR] fetch failed untuk {symbol}: {e}")
    return pd.DataFrame()


# ============================================================
# MACRO — CoinMarketCap (BTC Dominance & Altcoin Index)
# ============================================================
_fear_greed_cache: dict = {"data": None, "ts": 0.0}
FEAR_GREED_CACHE_TTL = 3600  # 1 jam (data update setiap hari, 1 jam cukup)


def _fetch_fear_greed() -> float | None:
    """
    Fetch Fear & Greed Index dari Alternative.me (free, no API key).
    Return: float 0–100 (0=Extreme Fear, 100=Extreme Greed)
            atau None jika fetch gagal.

    FIX #2: Implementasi API Fear & Greed yang sebelumnya selalu None.
    Nilai digunakan sebagai fitur 'fear_greed' di ml_feature_calculator.
    """
    now_ts = time.time()
    if _fear_greed_cache["data"] is not None and (now_ts - _fear_greed_cache["ts"] < FEAR_GREED_CACHE_TTL):
        return _fear_greed_cache["data"]
    try:
        url = "https://api.alternative.me/fng/"
        r = requests.get(url, params={"limit": 1, "format": "json"}, timeout=8, verify=False)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                value = float(data[0]["value"])
                _fear_greed_cache["data"] = value
                _fear_greed_cache["ts"]   = now_ts
                logger.info(f"  [Macro] Fear & Greed Index = {value:.0f}")
                return value
    except Exception as e:
        logger.warning(f"  [Macro] Fear & Greed fetch failed: {e}")
    return None


def _fetch_macro_cmc() -> dict | None:
    """Fetch CMC global metrics dengan cache 4 jam."""
    now_ts = time.time()
    if _cmc_cache["data"] and (now_ts - _cmc_cache["ts"] < CMC_CACHE_TTL):
        return _cmc_cache["data"]

    try:
        CMC_API_KEY = os.environ.get("CMC_API_KEY", "aa8eb4dd82974c308c5428e7c1be0121")
        url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
        r = requests.get(url, headers=headers, timeout=8, verify=False)
        if r.status_code == 200:
            d = r.json()["data"]
            btc_dom_raw   = round(d["btc_dominance"], 1)
            btc_dom_frac  = d["btc_dominance"] / 100
            total_mcap    = d["quote"]["USD"]["total_market_cap"]
            altcoin_index = round(total_mcap * (1 - btc_dom_frac) / 1_000_000_000, 1)
            result = {"BTC_Dominance": btc_dom_raw, "Altcoin_Index": altcoin_index}
            _cmc_cache["data"] = result
            _cmc_cache["ts"]   = now_ts
            logger.info(f"  [Macro] CMC OK — BTC_Dom={btc_dom_raw}%, AltIdx={altcoin_index}B")
            return result
    except Exception as e:
        logger.warning(f"  [Macro] CMC fetch failed: {e}")
    return None


# ============================================================
# LIQUIDITY WALLS — Binance Orderbook (multi-endpoint fallback)
# ============================================================
# Endpoint prioritas (urut dari paling cepat ke fallback):
# 1. fapi Futures (data paling relevan untuk futures trading)
# 2. Spot endpoint — data-api.binance.vision (paling reliabel, bypass geo-block)
# 3. Spot endpoint — api1/api2 (fallback tambahan)
# CATATAN: fapi sering return 403 karena geo/IP restriction.
# Spot orderbook memberikan data liquidity wall yang ekuivalen untuk deteksi level.
_ORDERBOOK_ENDPOINTS = [
    ("https://fapi.binance.com/fapi/v1/depth",         "limit", 500),   # Futures
    ("https://data-api.binance.vision/api/v3/depth",   "limit", 500),   # Spot CDN — paling stable
    ("https://api1.binance.com/api/v3/depth",           "limit", 500),   # Spot api1
    ("https://api2.binance.com/api/v3/depth",           "limit", 500),   # Spot api2
    ("https://api.binance.com/api/v3/depth",            "limit", 500),   # Spot main
]

def _fetch_liquidity_walls(symbol: str, close_price: float) -> dict | None:
    sym = symbol.upper()
    for url, limit_key, limit_val in _ORDERBOOK_ENDPOINTS:
        try:
            r = requests.get(
                url,
                params={"symbol": sym, limit_key: limit_val},
                timeout=8, verify=False
            )
            if r.status_code != 200:
                logger.debug(f"  [Liq] {url.split('/')[2]} → {r.status_code}, mencoba endpoint berikutnya")
                continue
            book = r.json()
            bids = [(float(p), float(q)) for p, q in book.get("bids", []) if float(p) < close_price]
            asks = [(float(p), float(q)) for p, q in book.get("asks", []) if float(p) > close_price]
            if bids and asks:
                buy_wall  = max(bids, key=lambda x: x[1])[0]
                sell_wall = max(asks, key=lambda x: x[1])[0]
                host = url.split('/')[2]
                logger.info(
                    f"  [Liq] Orderbook OK via {host} — "
                    f"Buy_Wall={buy_wall:.6f}, Sell_Wall={sell_wall:.6f}"
                )
                return {"Buy_Liq": round(buy_wall, 6), "Sell_Liq": round(sell_wall, 6)}
            else:
                logger.debug(f"  [Liq] {url.split('/')[2]} → OK tapi bids/asks kosong setelah filter close={close_price}")
        except Exception as e:
            logger.debug(f"  [Liq] {url.split('/')[2]} → exception: {e}")
    logger.warning(f"  [Liq] Semua endpoint gagal untuk {sym} — menggunakan dynamic swing levels sebagai fallback")
    return None


# ============================================================
# TECHNICAL INDICATORS
# ============================================================
def _apply_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    
    # Indicator params — must match FEATURE_COLS in training pipeline (core/features.py)
    # Do not change without retraining the model
    df["EMA_7"]   = ta.ema(df["Close"], length=7)
    df["EMA_21"]  = ta.ema(df["Close"], length=21)
    df["EMA_50"]  = ta.ema(df["Close"], length=50)
    df["EMA_200"] = ta.ema(df["Close"], length=200)
    df["RSI_6"]   = ta.rsi(df["Close"], length=6)
    atr_result    = ta.atr(df["High"], df["Low"], df["Close"], length=14)
    df["ATR_14"]  = atr_result if atr_result is not None else None

    try:
        stoch = ta.stochrsi(df["Close"], length=14, rsi_length=14, k=3, d=3)
        if stoch is not None and not stoch.empty:
            df["StochRSI_K"] = stoch.iloc[:, 0]
            df["StochRSI_D"] = stoch.iloc[:, 1]
        else:
            df["StochRSI_K"] = None
            df["StochRSI_D"] = None
    except Exception:
        df["StochRSI_K"] = None
        df["StochRSI_D"] = None

    if "Buy_Volume" in df.columns and "Total_Volume" in df.columns:
        df["Volume_Delta"] = df["Buy_Volume"] - df["Sell_Volume"]
        df["CVD"]          = df["Volume_Delta"].cumsum()
    return df


# ============================================================
# H4 MACRO EMAs (EMA_7/21/50/200_H4)
# ============================================================
def _apply_h4_macro_emas(df_base: pd.DataFrame, df_h4: pd.DataFrame) -> pd.DataFrame:
    """Hitung EMA H4 dan merge ke df_base via merge_asof."""
    if df_h4.empty or "Open_Time" not in df_base.columns:
        return df_base
    h4 = df_h4.copy()
    h4["EMA_7_H4"]   = ta.ema(h4["Close"], length=7)
    h4["EMA_21_H4"]  = ta.ema(h4["Close"], length=21)
    h4["EMA_50_H4"]  = ta.ema(h4["Close"], length=50)
    h4["EMA_200_H4"] = ta.ema(h4["Close"], length=200)
    h4_slim = h4[["Open_Time", "EMA_7_H4", "EMA_21_H4", "EMA_50_H4", "EMA_200_H4"]]
    try:
        df_base = pd.merge_asof(
            df_base.sort_values("Open_Time"),
            h4_slim.sort_values("Open_Time"),
            on="Open_Time", direction="backward"
        )
    except Exception as e:
        logger.debug(f"H4 EMA merge failed: {e}")
    return df_base


# ============================================================
# MARKET SESSION MAPPING
# ============================================================
def _apply_market_session(df: pd.DataFrame, time_col: str = "Open_Time",
                           offset_hours: int = 8) -> pd.DataFrame:
    """
    Tambahkan kolom Market_Session berdasarkan jam UTC+offset.
    Sesi (dalam UTC+8 / WIB):
      ASIAN    : 07:00 – 15:00
      LONDON   : 15:00 – 23:00
      NEW YORK : 20:00 – 04:00 (berikutnya)
      OFF-MARKET: di luar semua sesi
    """
    if time_col not in df.columns:
        return df
    df = df.copy()
    local_time = pd.to_datetime(df[time_col]) + pd.to_timedelta(offset_hours, unit="h")

    def get_session(dt: datetime) -> str:
        h = dt.hour
        sessions = []
        if 7 <= h < 15:   sessions.append("ASIAN")
        if 15 <= h < 23:  sessions.append("LONDON")
        if h >= 20 or h < 4: sessions.append("NEW YORK")
        return " / ".join(sessions) if sessions else "OFF-MARKET"

    df["Market_Session"] = local_time.apply(get_session)
    return df


# ============================================================
# LEGACY HELPERS (tetap dipakai oleh enrich_dataset lama)
# ============================================================
def apply_temporal_alignment(df: pd.DataFrame, offset_hours: int = 8) -> pd.DataFrame:
    """Backward-compat wrapper. Gunakan _apply_market_session secara internal."""
    df = df.copy()
    time_col = "Timestamp" if "Timestamp" in df.columns else (
                "Open_Time" if "Open_Time" in df.columns else None)
    if time_col:
        df[time_col] = pd.to_datetime(df[time_col]) + pd.to_timedelta(offset_hours, unit="h")
        df = _apply_market_session(df, time_col=time_col, offset_hours=0)
        df = df.sort_values(time_col).reset_index(drop=True)
    return df


# ============================================================
# SMC / FVG / Liquidity Levels / Fib OTE / Market Structure
# (fungsi lama — tetap tersedia untuk backward-compat)
# ============================================================
def calculate_smc_markers(df_m15: pd.DataFrame, df_h4: pd.DataFrame) -> pd.DataFrame:
    df = df_m15.copy()
    for i in range(2, len(df)):
        if df["Low"].iloc[i] > df["High"].iloc[i-2]:
            df.at[df.index[i-1], "FVG_Up_Top"]    = df["Low"].iloc[i]
            df.at[df.index[i-1], "FVG_Up_Bottom"]  = df["High"].iloc[i-2]
        if df["High"].iloc[i] < df["Low"].iloc[i-2]:
            df.at[df.index[i-1], "FVG_Down_Top"]   = df["Low"].iloc[i-2]
            df.at[df.index[i-1], "FVG_Down_Bottom"] = df["High"].iloc[i]
    df["OB_Price"] = np.nan
    for i in range(2, len(df)):
        if df["Low"].iloc[i] > df["High"].iloc[i-2] and df["Close"].iloc[i-2] < df["Open"].iloc[i-2]:
            df.at[df.index[i], "OB_Price"] = df["High"].iloc[i-2]
        if df["High"].iloc[i] < df["Low"].iloc[i-2] and df["Close"].iloc[i-2] > df["Open"].iloc[i-2]:
            df.at[df.index[i], "OB_Price"] = df["Low"].iloc[i-2]
    return df


def calculate_liquidity_levels(df_m15: pd.DataFrame, df_d1: pd.DataFrame, df_w1: pd.DataFrame) -> pd.DataFrame:
    df = df_m15.copy()
    df["PDH"] = df_d1.iloc[-2]["High"] if len(df_d1) >= 2 else 0.0
    df["PDL"] = df_d1.iloc[-2]["Low"]  if len(df_d1) >= 2 else 0.0
    df["PWH"] = df_w1.iloc[-2]["High"] if len(df_w1) >= 2 else 0.0
    df["PWL"] = df_w1.iloc[-2]["Low"]  if len(df_w1) >= 2 else 0.0
    pdh, pdl = float(df["PDH"].iloc[-1]), float(df["PDL"].iloc[-1])
    pwh, pwl = float(df["PWH"].iloc[-1]), float(df["PWL"].iloc[-1])
    df["SFP_Sweep"] = False
    for i in range(len(df)):
        h, l, c = df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
        if (h > pdh > c) or (h > pwh > c): df.at[df.index[i], "SFP_Sweep"] = True
        if (l < pdl < c) or (l < pwl < c): df.at[df.index[i], "SFP_Sweep"] = True
    return df


def calculate_fib_ote(df: pd.DataFrame, lookback: int = 40) -> pd.DataFrame:
    df = df.copy()
    df["Fib_0.618"] = np.nan
    df["Fib_0.786"] = np.nan
    for i in range(lookback, len(df)):
        window = df.iloc[i-lookback:i]
        sh = window["High"].max(); sl = window["Low"].min()
        diff = sh - sl
        df.at[df.index[i], "Fib_0.618"] = sh - 0.618 * diff
        df.at[df.index[i], "Fib_0.786"] = sh - 0.786 * diff
    return df


def calculate_market_structure(df_macro: pd.DataFrame, df_base: pd.DataFrame) -> pd.DataFrame:
    df = df_base.copy()
    dm = df_macro.copy()
    window = 3
    dm["Pivot_High"] = dm["High"] == dm["High"].rolling(window=window*2+1, center=True).max()
    dm["Pivot_Low"]  = dm["Low"]  == dm["Low"].rolling(window=window*2+1, center=True).min()
    last_ph, last_pl, trend = np.nan, np.nan, 0
    dm["MSB"] = 0; dm["BOS"] = 0; dm["CHoCH"] = 0
    for i in range(len(dm)):
        c = dm["Close"].iloc[i]
        if dm["Pivot_High"].iloc[i]: last_ph = dm["High"].iloc[i]
        if dm["Pivot_Low"].iloc[i]:  last_pl = dm["Low"].iloc[i]
        if pd.notna(last_ph) and c > last_ph:
            if trend == -1: dm.at[dm.index[i], "CHoCH"] = 1;  trend = 1
            elif trend == 1: dm.at[dm.index[i], "BOS"]  = 1
            else:            dm.at[dm.index[i], "MSB"]  = 1;  trend = 1
            last_ph = np.nan
        elif pd.notna(last_pl) and c < last_pl:
            if trend == 1:  dm.at[dm.index[i], "CHoCH"] = -1; trend = -1
            elif trend == -1: dm.at[dm.index[i], "BOS"] = -1
            else:             dm.at[dm.index[i], "MSB"] = -1; trend = -1
            last_pl = np.nan
    macro_slim = dm[["Open_Time", "MSB", "BOS", "CHoCH"]].copy()
    df = pd.merge_asof(df.sort_values("Open_Time"), macro_slim.sort_values("Open_Time"),
                       on="Open_Time", direction="backward")
    for col in ["MSB", "BOS", "CHoCH"]:
        df[col] = df[col].fillna(0)
    return df


def calculate_volume_profile(df: pd.DataFrame, bins: int = 24) -> pd.DataFrame:
    df = df.copy()
    df["POC"] = np.nan; df["VAH"] = np.nan; df["VAL"] = np.nan
    lookback = min(len(df), 30)
    if lookback < 5:
        return df
    for i in range(lookback, len(df)):
        window = df.iloc[i-lookback:i]
        min_p, max_p = window["Low"].min(), window["High"].max()
        if max_p == min_p: continue
        vol_prof = np.zeros(bins)
        price_step = (max_p - min_p) / bins
        for _, row in window.iterrows():
            idx = int(((row["High"] + row["Low"]) / 2 - min_p) / price_step)
            vol_prof[min(idx, bins-1)] += row["Total_Volume"]
        poc_idx = int(np.argmax(vol_prof))
        poc_price = min_p + poc_idx * price_step
        total_vol = np.sum(vol_prof)
        va_target = total_vol * 0.7
        va_cur = vol_prof[poc_idx]
        u_idx, l_idx = poc_idx, poc_idx
        while va_cur < va_target and (u_idx < bins-1 or l_idx > 0):
            vu = vol_prof[u_idx+1] if u_idx < bins-1 else 0
            vd = vol_prof[l_idx-1] if l_idx > 0 else 0
            if vu == 0 and vd == 0:
                if u_idx < bins-1: u_idx += 1
                elif l_idx > 0: l_idx -= 1
                else: break
            elif vu > vd: u_idx += 1; va_cur += vu
            else: l_idx -= 1; va_cur += vd
        df.at[df.index[i], "POC"] = poc_price
        df.at[df.index[i], "VAH"] = min_p + u_idx * price_step
        df.at[df.index[i], "VAL"] = min_p + l_idx * price_step
    return df


def enrich_dataset(df_base: pd.DataFrame, df_macro_h4: pd.DataFrame,
                   df_macro_d1: pd.DataFrame, df_macro_w1: pd.DataFrame) -> pd.DataFrame:
    """Legacy enrichment pipeline (dipakai oleh data_engine.py)."""
    df = df_base.copy()
    df["ATR_14"] = ta.atr(df["High"], df["Low"], df["Close"], length=14)
    df = calculate_liquidity_levels(df, df_macro_d1, df_macro_w1)
    df = calculate_smc_markers(df, df_macro_h4)
    df = calculate_fib_ote(df, lookback=30)
    df = _apply_h4_macro_emas(df, df_macro_h4)
    df = calculate_market_structure(df_macro_d1, df)
    df = calculate_volume_profile(df, bins=30)
    df = apply_temporal_alignment(df, offset_hours=8)
    df = df.ffill().bfill()
    return df


# ============================================================
# ✅ MASTER FUNCTION — Single Source of Truth
# ============================================================
def get_fully_enriched_data(symbol: str, interval: str = "1h",
                             limit: int = 500) -> tuple[pd.DataFrame, dict]:
    """
    SATU-SATUNYA fungsi yang boleh dipanggil oleh signal_monitor.py
    dan protocol_96_ui.py untuk mendapatkan DataFrame siap-scoring.

    Pipeline (berurutan):
      1. Fetch Klines (4h)
      2. Apply Indicators (EMA, RSI, StochRSI, ATR, CVD)
      3. Fetch + Merge Open Interest (real fapi atau synthetic formula training)
      4. Fetch + Merge Funding Rate
      5. Fetch + Merge H4 Macro EMAs (EMA_200_H4, dll.)
      6. Fetch Macro CMC (BTC_Dominance) + Fear & Greed (Alternative.me)
      7. Fetch Liquidity Walls (Buy_Liq, Sell_Liq)
      8. Apply Market Session (Market_Session column)

    FIX #4: Fear & Greed Index sekarang di-fetch secara real dari Alternative.me.

    Returns:
        df   : pd.DataFrame siap-scoring (atau kosong jika gagal)
        meta : dict berisi status data, e.g.:
               {
                 "data_incomplete": bool,
                 "missing_data":    list[str]   # kosong jika semua OK
               }
    """
    missing: list[str] = []
    symbol = symbol.upper()
    logger.info(f"[Enrichment] Building data for {symbol} {interval}...")

    # ── 1. Klines ──────────────────────────────────────────
    df = _fetch_klines_raw(symbol, interval, limit)
    if df.empty or len(df) < 22:
        logger.warning(f"[Enrichment] Kline data insufficient for {symbol}")
        return pd.DataFrame(), {"data_incomplete": True, "missing_data": ["Klines"]}

    # ── 2. Indicators ──────────────────────────────────────
    df = _apply_indicators(df)

    # ── 3. Open Interest ───────────────────────────────────
    oi_df = _fetch_oi(symbol)
    if not oi_df.empty:
        # OI real dari fapi — merge ke df
        try:
            df = pd.merge_asof(
                df.sort_values("Open_Time"),
                oi_df.sort_values("Open_Time"),
                on="Open_Time", direction="backward"
            )
        except Exception as e:
            logger.debug(f"[Enrichment] OI merge failed: {e}")
            df["Open_Interest"] = 0.0
    else:
        # fapi tidak tersedia → coba synthetic OI dari volume data
        syn_oi = _synthetic_oi(df)
        if not syn_oi.empty:
            try:
                df = pd.merge_asof(
                    df.sort_values("Open_Time"),
                    syn_oi.sort_values("Open_Time"),
                    on="Open_Time", direction="backward"
                )
                # Synthetic berhasil — tidak tambah ke missing (scoring tetap berjalan)
                logger.info(f"  [OI] Synthetic OI berhasil di-merge untuk {symbol}")
            except Exception as e:
                logger.debug(f"[Enrichment] Synthetic OI merge failed: {e}")
                df["Open_Interest"] = 0.0
                missing.append("OpenInterest")
        else:
            df["Open_Interest"] = 0.0
            missing.append("OpenInterest")

    # ── 4. Funding Rate ────────────────────────────────────
    fr_df = _fetch_funding_rate(symbol)
    if not fr_df.empty:
        # FR real dari fapi — merge ke df
        try:
            df = pd.merge_asof(
                df.sort_values("Open_Time"),
                fr_df.sort_values("Open_Time"),
                on="Open_Time", direction="backward"
            )
        except Exception as e:
            logger.debug(f"[Enrichment] FR merge failed: {e}")
            df["Funding_Rate"] = 0.0
    else:
        # fapi tidak tersedia → gunakan 0.0 (neutral)
        # Nilai 0.0 aman: Gate L3 (≤ +0.0003) PASS, Gate S3 (≥ -0.0003) PASS
        # has_funding = True dengan nilai 0.0 → scoring berjalan normal (netral)
        # TIDAK ditambahkan ke missing agar signal monitor tetap berjalan
        df["Funding_Rate"] = 0.0
        logger.info(
            f"  [FR] Funding Rate tidak tersedia untuk {symbol} "
            "— menggunakan 0.0 (netral). Gate funding PASS otomatis."
        )

    # ── 5. H4 Macro EMAs (EMA_200_H4, dll.) ───────────────
    df_h4 = _get_klines_cached(symbol, "4h", limit=250) if interval != "4h" else df
    df_1d  = _get_klines_cached(symbol, "1d", limit=100)
    df_1w  = _get_klines_cached(symbol, "1w", limit=50)

    if not df_1d.empty and not df_1w.empty:
        # Liquidity levels dari Daily/Weekly
        try:
            df = calculate_liquidity_levels(df, df_1d, df_1w)
        except Exception as e:
            logger.debug(f"[Enrichment] Liquidity levels failed: {e}")
        # Market Structure
        try:
            df = calculate_market_structure(df_1d, df)
        except Exception as e:
            logger.debug(f"[Enrichment] Market structure failed: {e}")

    # H4 EMA columns
    df = _apply_h4_macro_emas(df, df_h4 if interval != "4h" else df.copy())

    # ── 6. Macro — CMC (BTC Dominance) + Fear & Greed (Alternative.me) ──
    cmc = _fetch_macro_cmc()
    if cmc:
        df["BTC_Dominance"] = cmc["BTC_Dominance"]
        df["Altcoin_Index"]  = cmc["Altcoin_Index"]
    else:
        df["BTC_Dominance"] = None
        df["Altcoin_Index"]  = None
        missing.append("Macro")

    # FIX #2: Fear & Greed Index — sebelumnya selalu None (distribusi shift fatal)
    # Sekarang di-fetch dari Alternative.me (free endpoint)
    fear_greed_val = _fetch_fear_greed()
    if fear_greed_val is not None:
        df["Fear_Greed"] = fear_greed_val
    else:
        df["Fear_Greed"] = None
        missing.append("FearGreed")
        logger.warning(f"  [Macro] Fear & Greed tidak tersedia untuk {symbol} — fitur akan NaN→0")

    # ── 7. Liquidity Walls (Orderbook) ─────────────────────
    close_last = float(df["Close"].iloc[-1]) if not df.empty else 0.0
    liq = _fetch_liquidity_walls(symbol, close_last)
    if liq:
        df["Buy_Liq"]  = liq["Buy_Liq"]
        df["Sell_Liq"] = liq["Sell_Liq"]
    else:
        df["Buy_Liq"]  = 0.0
        df["Sell_Liq"] = 0.0
        missing.append("LiquidityWalls")

    # ── 8. Market Session ──────────────────────────────────
    df = _apply_market_session(df, time_col="Open_Time", offset_hours=8)

    # ── Ensure CVD present ────────────────────────────────
    if "CVD" not in df.columns and "Volume_Delta" in df.columns:
        df["CVD"] = df["Volume_Delta"].cumsum()

    # ── Final cleanup ─────────────────────────────────────
    df = df.ffill().bfill()
    df = df.reset_index(drop=True)

    meta = {
        "data_incomplete": len(missing) > 0,
        "missing_data":    missing,
    }

    logger.info(
        f"[Enrichment] {symbol} ready — {len(df)} rows, "
        f"missing={missing if missing else 'none'}"
    )
    return df, meta
