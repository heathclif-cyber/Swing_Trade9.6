"""
Protocol 9.6 — Signal Monitor
Background thread yang berjalan setiap 15 menit untuk memantau sinyal buy/sell
dan mengirimkan notifikasi Telegram secara otomatis.

FIX: Semua env vars dibaca saat runtime (bukan saat import) agar kompatibel
     dengan Gunicorn worker fork di Railway.
"""
import threading
import time
import logging
import os
import json
import requests
import pandas as pd  # type: ignore
import pandas_ta as ta  # type: ignore
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

logger = logging.getLogger("SignalMonitor")

# ============================================================
# CONSTANTS — tidak bergantung env vars
# ============================================================
POLL_INTERVAL_SECONDS = 15 * 60   # 15 menit
# CATATAN: Threshold FULL/HALF kini bersifat adaptif (dari P7 result['variables'])
# Nilai di bawah hanya sebagai fallback jika variabel adaptif tidak tersedia.
SIGNAL_THRESHOLD_FULL = 48        # ADJ score default FULL SIZE ENTRY (bull mode)
SIGNAL_THRESHOLD_HALF = 33        # ADJ score default HALF SIZE ENTRY (bull mode)

MONITOR_PAIRS = [
    "SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "PENDLEUSDT", "DOGEUSDT", "LINKUSDT", "ETHUSDT"
]

# ── Thread safety ──────────────────────────────────────────
_alert_state: dict = {}
_state_lock   = threading.Lock()
_started_flag = threading.Event()   # mencegah double-start


# ============================================================
# ENV VARS — selalu dibaca runtime agar tidak terpengaruh fork
# ============================================================
def _tg_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")

def _tg_chat() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")

def _db_url() -> str:
    return os.environ.get("DATABASE_URL", "")


# ============================================================
# TELEGRAM
# ============================================================
def _send_telegram(text: str) -> bool:
    """Kirim Telegram message. Return True jika berhasil."""
    token = _tg_token()
    chat  = _tg_chat()
    if not token or not chat:
        logger.warning("Telegram belum dikonfigurasi (TOKEN/CHAT_ID kosong)")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=15
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info("✅ Telegram alert sent")
            return True
        else:
            logger.warning(f"Telegram error {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        logger.warning(f"Telegram request failed: {e}")
        return False


# ============================================================
# DATABASE
# ============================================================
def _get_pg_conn():
    db = _db_url()
    if not db:
        return None
    try:
        import psycopg2  # type: ignore
        url_str = db.replace("postgres://", "postgresql://", 1)
        p = urlparse(url_str)
        return psycopg2.connect(
            host=p.hostname, port=p.port or 5432,
            user=p.username, password=p.password,
            dbname=p.path.lstrip("/"), sslmode="require"
        )
    except Exception as e:
        logger.warning(f"PG conn failed: {e}")
        return None


def _load_trade_entries() -> dict:
    conn = _get_pg_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM kv_store WHERE key = 'trade_entries'")
                row = cur.fetchone()
            conn.close()
            return json.loads(row[0]) if row else {}
        except Exception as e:
            logger.warning(f"PG load failed: {e}")
            try: conn.close()
            except: pass
    # Fallback JSON
    path = os.environ.get(
        "TRADE_DATA_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_entries.json")
    )
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_alert_state(state: dict) -> None:
    """Simpan _alert_state ke file JSON agar TP tracking persist antar restart."""
    path = os.environ.get(
        "ALERT_STATE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_state.json")
    )
    try:
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"_save_alert_state failed: {e}")


# ============================================================
# DATA FETCHING
# ============================================================
BINANCE_KLINE_URLS = [
    "https://fapi.binance.com/fapi/v1/klines",
    "https://data-api.binance.vision/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
_last_working_url: str | None = None


def _fetch_klines(symbol: str, interval: str = "4h", limit: int = 250) -> pd.DataFrame:
    global _last_working_url
    urls = list(BINANCE_KLINE_URLS)
    if _last_working_url and _last_working_url in urls:
        urls.remove(_last_working_url)
        urls.insert(0, _last_working_url)

    for url in urls:
        try:
            params = {"symbol": symbol, "interval": interval, "limit": limit}
            resp = requests.get(url, params=params, timeout=12, verify=False)
            if resp.status_code == 200:
                data = resp.json()
                if not data or not isinstance(data, list):
                    continue
                _last_working_url = url
                df = pd.DataFrame(data, columns=[
                    "Open_Time", "Open", "High", "Low", "Close", "Total_Volume",
                    "Close_Time", "Quote_Asset_Volume", "Trades",
                    "Taker_Buy_Base", "Taker_Buy_Quote", "Ignore"
                ])
                df["Open_Time"] = pd.to_datetime(df["Open_Time"], unit="ms")
                for col in ["Open", "High", "Low", "Close", "Total_Volume", "Taker_Buy_Base"]:
                    df[col] = df[col].astype(float)
                df["Buy_Volume"]   = df["Taker_Buy_Base"]
                df["Sell_Volume"]  = df["Total_Volume"] - df["Buy_Volume"]
                df["Volume_Delta"] = df["Buy_Volume"] - df["Sell_Volume"]
                return df
        except Exception as e:
            logger.debug(f"Kline fetch failed {url}: {e}")
    logger.warning(f"All kline endpoints failed for {symbol} {interval}")
    return pd.DataFrame()


def _apply_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["EMA_7"]   = ta.ema(df["Close"], length=7)
    df["EMA_21"]  = ta.ema(df["Close"], length=21)
    df["EMA_50"]  = ta.ema(df["Close"], length=50)
    df["EMA_200"] = ta.ema(df["Close"], length=200)
    df["RSI_6"]   = ta.rsi(df["Close"], length=6)
    df["ATR_14"]  = ta.atr(df["High"], df["Low"], df["Close"], length=14)
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
    # CVD
    if "Buy_Volume" in df.columns and "Sell_Volume" in df.columns:
        df["Volume_Delta"] = df["Buy_Volume"] - df["Sell_Volume"]
        df["CVD"]          = df["Volume_Delta"].cumsum()
    return df


def _fetch_oi(symbol: str, limit: int = 500) -> pd.DataFrame:
    """Fetch Open Interest history dari Binance Futures."""
    try:
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        params = {"symbol": symbol, "period": "15m", "limit": limit}
        resp = requests.get(url, params=params, timeout=10, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                oi_df = pd.DataFrame(data)
                oi_df["Open_Time"]     = pd.to_datetime(oi_df["timestamp"], unit="ms")
                oi_df["Open_Interest"] = oi_df["sumOpenInterest"].astype(float)
                return oi_df[["Open_Time", "Open_Interest"]]
    except Exception as e:
        logger.debug(f"OI fetch failed for {symbol}: {e}")
    return pd.DataFrame()


def _fetch_funding_rate(symbol: str, limit: int = 200) -> pd.DataFrame:
    """Fetch Funding Rate dari Binance Futures."""
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        resp = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=10, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                fr_df = pd.DataFrame(data)
                fr_df["Open_Time"]    = pd.to_datetime(fr_df["fundingTime"], unit="ms")
                fr_df["Funding_Rate"] = fr_df["fundingRate"].astype(float)
                return fr_df[["Open_Time", "Funding_Rate"]]
    except Exception as e:
        logger.debug(f"Funding Rate fetch failed for {symbol}: {e}")
    return pd.DataFrame()


def _enrich_df(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Enrichment pipeline lengkap — OI + Funding Rate + CVD + Indicators +
    Macro Context (BTC_Dominance, Altcoin_Index) + Liquidity Walls (Buy_Liq, Sell_Liq).

    [SYNC FIX] Identik dengan api_data() di protocol_96_ui.py agar skor live
    konsisten 100% dengan yang ditampilkan di dashboard.
    """
    df = _apply_indicators(df)

    # ── Merge Open Interest ───────────────────────────────────────────────
    oi_df = _fetch_oi(symbol)
    if not oi_df.empty:
        try:
            df = pd.merge_asof(
                df.sort_values("Open_Time"),
                oi_df.sort_values("Open_Time"),
                on="Open_Time", direction="backward"
            )
        except Exception as e:
            logger.debug(f"OI merge failed: {e}")
            df["Open_Interest"] = 0.0
    else:
        df["Open_Interest"] = 0.0

    # ── Merge Funding Rate ────────────────────────────────────────────────
    fr_df = _fetch_funding_rate(symbol)
    if not fr_df.empty:
        try:
            df = pd.merge_asof(
                df.sort_values("Open_Time"),
                fr_df.sort_values("Open_Time"),
                on="Open_Time", direction="backward"
            )
        except Exception as e:
            logger.debug(f"FR merge failed: {e}")
            df["Funding_Rate"] = 0.0
    else:
        df["Funding_Rate"] = 0.0

    # ── [SYNC FIX] Macro Context: BTC Dominance & Altcoin Index (CMC) ────
    # Inline fetch agar tidak ada circular import dengan protocol_96_ui.py
    try:
        CMC_API_KEY = os.environ.get("CMC_API_KEY", "aa8eb4dd82974c308c5428e7c1be0121")
        cmc_url     = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
        cmc_headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
        r = requests.get(cmc_url, headers=cmc_headers, timeout=8, verify=False)
        if r.status_code == 200:
            d             = r.json()["data"]
            btc_dom_raw   = round(d["btc_dominance"] * 100, 1)
            total_mcap    = d["quote"]["USD"]["total_market_cap"]
            btc_dom_frac  = d["btc_dominance"] / 100
            altcoin_index = round(total_mcap * (1 - btc_dom_frac) / 1_000_000_000, 1)
            df["BTC_Dominance"] = btc_dom_raw
            df["Altcoin_Index"]  = altcoin_index
            logger.info(f"  [Macro] CMC OK — BTC_Dom={btc_dom_raw}%, AltIdx={altcoin_index}B")
        else:
            df["BTC_Dominance"] = None
            df["Altcoin_Index"]  = None
    except Exception as e:
        logger.warning(f"  [Macro] CMC fetch failed: {e}")
        df["BTC_Dominance"] = None
        df["Altcoin_Index"]  = None

    # ── [SYNC FIX] Liquidity Walls: Buy_Liq & Sell_Liq (Binance Orderbook) ──
    try:
        liq_url    = "https://fapi.binance.com/fapi/v1/depth"
        liq_params = {"symbol": symbol.upper(), "limit": 500}
        r_liq = requests.get(liq_url, params=liq_params, timeout=8, verify=False)
        if r_liq.status_code == 200:
            book       = r_liq.json()
            close_last = float(df["Close"].iloc[-1])
            bids = [(float(p), float(q)) for p, q in book.get("bids", []) if float(p) < close_last]
            asks = [(float(p), float(q)) for p, q in book.get("asks", []) if float(p) > close_last]
            if bids and asks:
                buy_wall  = max(bids, key=lambda x: x[1])[0]
                sell_wall = max(asks, key=lambda x: x[1])[0]
                df["Buy_Liq"]  = round(buy_wall, 6)
                df["Sell_Liq"] = round(sell_wall, 6)
                logger.info(f"  [Macro] Orderbook OK — Buy_Wall={buy_wall:.6f}, Sell_Wall={sell_wall:.6f}")
            else:
                df["Buy_Liq"]  = 0.0
                df["Sell_Liq"] = 0.0
        else:
            df["Buy_Liq"]  = 0.0
            df["Sell_Liq"] = 0.0
    except Exception as e:
        logger.warning(f"  [Macro] Orderbook fetch failed: {e}")
        df["Buy_Liq"]  = 0.0
        df["Sell_Liq"] = 0.0

    return df


# ============================================================
# SIGNAL EVALUATION
# ============================================================
def _evaluate_pair(symbol: str, trade_entries: dict) -> None:
    try:
        import algo_scoring  # lazy import

        df = _fetch_klines(symbol, interval="4h", limit=250)
        if df is None or len(df) < 22:
            logger.warning(f"[{symbol}] Insufficient data")
            return

        df = _enrich_df(df, symbol)

        coin_data     = trade_entries.get(symbol, {})
        entry_list    = coin_data.get("entries", [])
        sales_list    = coin_data.get("sales", [])
        total_cost    = sum(e["price"] * e["qty"] for e in entry_list)
        total_qty     = sum(e["qty"] for e in entry_list)
        sold_qty      = sum(s["qty"] for s in sales_list)
        remaining_qty = max(0.0, total_qty - sold_qty)
        avg_entry     = (total_cost / total_qty) if total_qty > 0 else None
        is_active     = (avg_entry is not None) and (remaining_qty > 0)

        meta   = {
            "Symbol":          symbol,
            "AVG_ENTRY_PRICE": avg_entry if is_active else None,
            "ENTRY_DATE":      entry_list[-1].get("date") if entry_list else None,
        }
        result = algo_scoring.calculate_71point_score(df, meta)
        if result is None:
            logger.warning(f"[{symbol}] Scoring returned None")
            return

        close_price = float(df.iloc[-1]["Close"])
        adj_L     = result["long"]["total"]
        adj_S     = result["short"]["total"]
        code_L    = result["long"]["code"]
        code_S    = result["short"]["code"]
        lvl_L     = result["long"]["levels"]
        lvl_S     = result["short"]["levels"]
        exit_r    = result.get("exit", {})
        mom_hold  = result.get("momentum_hold", {})
        sl_wick   = result.get("sl_wick", {})
        trailing  = result.get("trailing_sl", {})
        variables = result.get("variables", {})

        # [P7] Adaptive thresholds dari scoring engine
        thr_full = variables.get("thr_full", SIGNAL_THRESHOLD_FULL)
        thr_half = variables.get("thr_half", SIGNAL_THRESHOLD_HALF)
        macro_trend      = variables.get("macro_trend", "UNKNOWN")
        threshold_regime = variables.get("threshold_regime", "UNKNOWN")
        session_block_type = variables.get("session_block_type", "NONE")
        stoch_gk_reason  = variables.get("stoch_gatekeeper_reason", "")
        stoch_gk_ok      = variables.get("stoch_gatekeeper_ok", True)

        now_ts = time.time()

        with _state_lock:
            state = _alert_state.setdefault(symbol, {
                "last_signal": None,
                "last_alert_ts": 0,
                "exit_alerted": False,
                "kill_alerted": False,
                "tp1_alerted": False,
                "tp2_alerted": False,
                "tp3_alerted": False,
                # ── [EMA-50 SURFER] Runner Mode fields ──────────
                "runner_active": False,   # True setelah TP3 tercapai
                "runner_sl": None,        # Level SL dinamis runner (float)
                "runner_side": None,      # 'LONG' atau 'SHORT'
                "runner_closed_alerted": False,  # Sudah kirim notif close runner?
            })
            cooldown        = 4 * 3600
            time_since_last = now_ts - state["last_alert_ts"]

            # ── KILL SWITCH ────────────────────────────────
            kill_switch = (
                len(df) >= 2
                and "EMA_21" in df.columns
                and float(df.iloc[-2]["Close"]) < float(df.iloc[-2].get("EMA_21", float("inf")))
            )
            # ── SL WICK FAKEOUT ALERT (jika ada posisi aktif & wick menyentuh SL) ──
            if is_active and sl_wick.get("sl_touched_wick") and not state.get("wick_alerted"):
                verdict = sl_wick.get("verdict", "N/A")
                action  = sl_wick.get("action", "")
                conf    = sl_wick.get("confidence_pct", 0)
                pnl_str = f"{((close_price/avg_entry)-1)*100:+.2f}%" if avg_entry else "N/A"
                body_ok = "✅ Body di atas SL" if sl_wick.get("body_above_sl") else "❌ Body TEMBUS SL"
                cvd_ok  = "✅ CVD masih defend" if sl_wick.get("cvd_defending") else "❌ CVD memburuk"
                vol_ok  = "✅ Volume drop lemah" if sl_wick.get("low_volume_drop") else "⚠️ Volume normal"
                bull_ok = "✅ Candle berbalik hijau" if sl_wick.get("bullish_body") else "❌ Candle masih merah"
                _send_telegram(
                    f"⚠️ <b>SL WICK ALERT — {symbol}</b>\n"
                    f"{'─'*28}\n"
                    f"Harga: <b>${close_price:.6f}</b> | PnL: {pnl_str}\n"
                    f"SL Level: ${lvl_L['sl_structure']:.6f}\n\n"
                    f"<b>Analisis Wick:</b>\n"
                    f"  {body_ok}\n"
                    f"  {cvd_ok}\n"
                    f"  {vol_ok}\n"
                    f"  {bull_ok}\n\n"
                    f"🔍 Verdict: <b>{verdict}</b> ({conf}% yakin)\n"
                    f"📋 {action}"
                )
                state["wick_alerted"] = (verdict != "BREAKDOWN NYATA")
                if verdict == "BREAKDOWN NYATA":
                    state["last_alert_ts"] = now_ts
                    return

            # Reset wick alert jika harga kembali aman
            if not sl_wick.get("sl_touched_wick"):
                state["wick_alerted"] = False

            if is_active and kill_switch and not state["kill_alerted"]:
                ema21_val = float(df.iloc[-2].get("EMA_21", 0))
                pnl_str   = f"{((close_price/avg_entry)-1)*100:+.2f}%" if avg_entry else "N/A"
                _send_telegram(
                    f"🚨 <b>KILL SWITCH — {symbol}</b>\n"
                    f"H4 close di bawah EMA21 (${ema21_val:.4f})\n"
                    f"Harga: <b>${close_price:.4f}</b> | PnL: {pnl_str}\n\n"
                    f"❌ <b>Instruksi:</b> PERTIMBANGKAN EXIT PENUH.\n"
                    f"Struktur bullish resmi batal."
                )
                state["kill_alerted"] = True
                state["last_alert_ts"] = now_ts
                return

            if not kill_switch:
                state["kill_alerted"] = False

            # ── [EMA-50 SURFER] TRACKING TP + RUNNER MODE ────────────
            # Strategi: TP1/TP2 partial exit → TP3 aktifkan Runner Mode.
            # Runner Mode: posisi TIDAK di-close di TP3, SL dinamis ikuti EMA-50.
            # Close hanya jika harga menembus SL Runner.
            if is_active:
                high_price = float(df.iloc[-1]["High"])
                low_price  = float(df.iloc[-1]["Low"])
                ema50_abs  = float(df["EMA_50"].iloc[-1]) if "EMA_50" in df.columns else None

                tp1 = lvl_L['tp1']
                tp2 = lvl_L['tp2']
                tp3 = lvl_L['tp3']
                tp1_s = lvl_S['tp1']
                tp2_s = lvl_S['tp2']
                tp3_s = lvl_S['tp3']

                # ── Deteksi sisi posisi aktif (LONG / SHORT) ──────────
                is_long_trade  = (avg_entry is not None) and (tp1 > avg_entry)
                is_short_trade = (avg_entry is not None) and (tp1 < avg_entry)

                # ── RUNNER MODE CHECK (prioritas utama jika sudah TP3) ───
                if state.get("runner_active") and ema50_abs is not None:
                    runner_sl   = state.get("runner_sl")
                    runner_side = state.get("runner_side", "LONG")
                    pnl_str     = ""
                    if avg_entry:
                        pnl_pct = ((close_price / avg_entry) - 1) * 100
                        if runner_side == "SHORT":
                            pnl_pct = ((avg_entry / close_price) - 1) * 100
                        pnl_str = f"{pnl_pct:+.2f}%"

                    if runner_side == "LONG":
                        # Update SL runner: selalu naik, tidak boleh turun
                        new_runner_sl = max(runner_sl or 0.0, ema50_abs)
                        if new_runner_sl != runner_sl:
                            state["runner_sl"] = new_runner_sl
                            _save_alert_state(_alert_state)
                            _send_telegram(
                                f"🏄 <b>RUNNER UPDATE — {symbol} (LONG)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga: <b>${close_price:.6f}</b> | PnL: <b>{pnl_str}</b>\n"
                                f"SL Runner baru: <b>${new_runner_sl:.6f}</b> (EMA-50)\n"
                                f"📌 Posisi terus berjalan. Close hanya jika harga tembus SL."
                            )
                        # CLOSE jika harga <= SL runner
                        if low_price <= (state["runner_sl"] or 0.0) and not state.get("runner_closed_alerted"):
                            _send_telegram(
                                f"🔔 <b>CLOSE RUNNER — {symbol} (LONG)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga Low: <b>${low_price:.6f}</b>\n"
                                f"SL Runner: ${state['runner_sl']:.6f} (EMA-50)\n"
                                f"PnL: <b>{pnl_str}</b>\n\n"
                                f"✅ <b>Closed Runner at EMA-50.</b>\n"
                                f"Sisa posisi (30%) dapat di-close sekarang."
                            )
                            state["runner_closed_alerted"] = True
                            state["runner_active"] = False
                            _save_alert_state(_alert_state)

                    elif runner_side == "SHORT":
                        # Update SL runner: selalu turun, tidak boleh naik
                        new_runner_sl = min(runner_sl or float("inf"), ema50_abs)
                        if new_runner_sl != runner_sl:
                            state["runner_sl"] = new_runner_sl
                            _save_alert_state(_alert_state)
                            _send_telegram(
                                f"🏄 <b>RUNNER UPDATE — {symbol} (SHORT)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga: <b>${close_price:.6f}</b> | PnL: <b>{pnl_str}</b>\n"
                                f"SL Runner baru: <b>${new_runner_sl:.6f}</b> (EMA-50)\n"
                                f"📌 Posisi terus berjalan. Close hanya jika harga tembus SL."
                            )
                        # CLOSE jika harga >= SL runner
                        if high_price >= (state["runner_sl"] or float("inf")) and not state.get("runner_closed_alerted"):
                            _send_telegram(
                                f"🔔 <b>CLOSE RUNNER — {symbol} (SHORT)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga High: <b>${high_price:.6f}</b>\n"
                                f"SL Runner: ${state['runner_sl']:.6f} (EMA-50)\n"
                                f"PnL: <b>{pnl_str}</b>\n\n"
                                f"✅ <b>Closed Runner at EMA-50.</b>\n"
                                f"Sisa posisi (30%) dapat di-close sekarang."
                            )
                            state["runner_closed_alerted"] = True
                            state["runner_active"] = False
                            _save_alert_state(_alert_state)

                # ── TP TRACKING (hanya jika belum runner mode) ────────
                elif not state.get("runner_active"):

                    if is_long_trade:
                        # ── TP1 LONG ────────────────────────────────
                        if high_price >= tp1 and not state.get("tp1_alerted"):
                            _send_telegram(
                                f"🎯 <b>TP1 TERCAPAI — {symbol} (LONG)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga: <b>${high_price:.6f}</b> | TP1: ${tp1:.6f}\n\n"
                                f"Tindakan: <b>EXIT 30%</b>\n"
                                f"SL: Geser ke Entry ${avg_entry:.6f} (Breakeven)"
                            )
                            state["tp1_alerted"] = True
                            _save_alert_state(_alert_state)

                        # ── TP2 LONG ────────────────────────────────
                        if high_price >= tp2 and not state.get("tp2_alerted"):
                            _send_telegram(
                                f"🎯🎯 <b>TP2 TERCAPAI — {symbol} (LONG)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga: <b>${high_price:.6f}</b> | TP2: ${tp2:.6f}\n\n"
                                f"Tindakan: <b>EXIT 40%</b>\n"
                                f"SL: Geser ke TP1 ${tp1:.6f}"
                            )
                            state["tp2_alerted"] = True
                            _save_alert_state(_alert_state)

                        # ── TP3 LONG → AKTIFKAN RUNNER MODE ─────────
                        if high_price >= tp3 and not state.get("tp3_alerted"):
                            init_runner_sl = max(tp2, ema50_abs) if ema50_abs else tp2
                            state["tp3_alerted"]          = True
                            state["runner_active"]        = True
                            state["runner_sl"]            = init_runner_sl
                            state["runner_side"]          = "LONG"
                            state["runner_closed_alerted"] = False
                            _save_alert_state(_alert_state)
                            _send_telegram(
                                f"🚀 <b>TP3 + RUNNER MODE AKTIF — {symbol} (LONG)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga: <b>${high_price:.6f}</b> | TP3: ${tp3:.6f}\n\n"
                                f"Tindakan: <b>EXIT 40%</b> (sisa 30% jadi Runner)\n"
                                f"SL Runner awal: <b>${init_runner_sl:.6f}</b> (max TP2, EMA-50)\n\n"
                                f"🏄 <b>EMA-50 SURFER aktif.</b> Posisi terus berjalan.\n"
                                f"Bot akan alert saat SL Runner tertembus."
                            )

                    elif is_short_trade:
                        # ── TP1 SHORT ───────────────────────────────
                        if low_price <= tp1_s and not state.get("tp1_alerted"):
                            _send_telegram(
                                f"🎯 <b>TP1 TERCAPAI — {symbol} (SHORT)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga: <b>${low_price:.6f}</b> | TP1: ${tp1_s:.6f}\n\n"
                                f"Tindakan: <b>EXIT 30%</b>\n"
                                f"SL: Geser ke Entry ${avg_entry:.6f} (Breakeven)"
                            )
                            state["tp1_alerted"] = True
                            _save_alert_state(_alert_state)

                        # ── TP2 SHORT ───────────────────────────────
                        if low_price <= tp2_s and not state.get("tp2_alerted"):
                            _send_telegram(
                                f"🎯🎯 <b>TP2 TERCAPAI — {symbol} (SHORT)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga: <b>${low_price:.6f}</b> | TP2: ${tp2_s:.6f}\n\n"
                                f"Tindakan: <b>EXIT 40%</b>\n"
                                f"SL: Geser ke TP1 ${tp1_s:.6f}"
                            )
                            state["tp2_alerted"] = True
                            _save_alert_state(_alert_state)

                        # ── TP3 SHORT → AKTIFKAN RUNNER MODE ────────
                        if low_price <= tp3_s and not state.get("tp3_alerted"):
                            init_runner_sl = min(tp2_s, ema50_abs) if ema50_abs else tp2_s
                            state["tp3_alerted"]          = True
                            state["runner_active"]        = True
                            state["runner_sl"]            = init_runner_sl
                            state["runner_side"]          = "SHORT"
                            state["runner_closed_alerted"] = False
                            _save_alert_state(_alert_state)
                            _send_telegram(
                                f"🚀 <b>TP3 + RUNNER MODE AKTIF — {symbol} (SHORT)</b>\n"
                                f"{'─'*28}\n"
                                f"Harga: <b>${low_price:.6f}</b> | TP3: ${tp3_s:.6f}\n\n"
                                f"Tindakan: <b>EXIT 40%</b> (sisa 30% jadi Runner)\n"
                                f"SL Runner awal: <b>${init_runner_sl:.6f}</b> (min TP2, EMA-50)\n\n"
                                f"🏄 <b>EMA-50 SURFER aktif.</b> Posisi terus berjalan.\n"
                                f"Bot akan alert saat SL Runner tertembus."
                            )

            # Reset semua state TP & runner jika posisi sudah tidak aktif
            if not is_active and (state.get("tp1_alerted") or state.get("runner_active")):
                state["tp1_alerted"]           = False
                state["tp2_alerted"]           = False
                state["tp3_alerted"]           = False
                state["runner_active"]         = False
                state["runner_sl"]             = None
                state["runner_side"]           = None
                state["runner_closed_alerted"] = False
                _save_alert_state(_alert_state)

            # ── EXIT SIGNALS ───────────────────────────────
            # [HYBRID MODEL] Dynamic Exit Alert DIMATIKAN.
            # Bot fokus hold hingga TP1/TP2/TP3 atau Trailing SL secara natural.
            exit_signals = exit_r.get("signals", [])
            hard_exits   = [e for e in exit_signals if e[0] == "❌"]

            # if is_active and hard_exits and not state["exit_alerted"]:
            #     pnl_str   = f"{((close_price/avg_entry)-1)*100:+.2f}%" if avg_entry else "N/A"
            #     exit_lines = "\n".join(
            #         f"  {icon} {name}: {val} ({cond})"
            #         for icon, name, val, cond in exit_signals[:5]
            #     )
            #     _send_telegram(
            #         f"⚠️ <b>EXIT ALERT — {symbol}</b>\n"
            #         f"{'─'*28}\n"
            #         f"Harga: <b>${close_price:.6f}</b> | PnL: <b>{pnl_str}</b>\n\n"
            #         f"<b>Sinyal keluar:</b>\n{exit_lines}\n\n"
            #         f"📋 Rekomendasi: <b>{exit_r.get('recommendation','PARTIAL EXIT')}</b>"
            #     )
            #     state["exit_alerted"] = True
            #     state["last_alert_ts"] = now_ts
            #     return

            if not hard_exits:
                state["exit_alerted"] = False

            # ── TRAILING SL ALERT ─────────────────────────
            trailing_L = trailing.get("long", {})
            trailing_S = trailing.get("short", {})
            active_tsl = None
            tsl_side   = ""
            if trailing_L.get("applicable"):
                active_tsl = trailing_L
                tsl_side = "LONG"
            elif trailing_S.get("applicable"):
                active_tsl = trailing_S
                tsl_side = "SHORT"

            # Alert if trailing SL is recommended and not already alerted for this specific action
            if is_active and active_tsl:
                action_text = active_tsl.get("action", "")
                if state.get("last_trailing_action") != action_text:
                    pnl_str = f"{((close_price/avg_entry)-1)*100:+.2f}%" if avg_entry else "N/A"
                    # For short, reverse PnL
                    if tsl_side == "SHORT" and avg_entry:
                        pnl_str = f"{((avg_entry/close_price)-1)*100:+.2f}%"

                    # Tambahkan saran Momentum Hold jika harga masih kuat naik
                    hold_str = ""
                    if mom_hold.get("signal"):
                        reasons = " · ".join(mom_hold.get("reasons", [])[:3])
                        hold_str = (
                            f"\n\n🔥 <b>MOMENTUM MASIH BESAR ({mom_hold['strength']})</b>\n"
                            f"<i>Disarankan tahan posisi (partial TP).</i>\n"
                            f"Detail: {reasons}"
                        )

                    _send_telegram(
                        f"🛡️ <b>TRAILING SL AKTIF — {symbol}</b>\n"
                        f"{'─'*28}\n"
                        f"Arah Trade: <b>{tsl_side}</b> | PnL: <b>{pnl_str}</b>\n"
                        f"Harga: <b>${close_price:.6f}</b>\n\n"
                        f"✅ <b>Instruksi Sistem:</b>\n"
                        f"<b>{action_text}</b>\n\n"
                        f"💡 <i>{active_tsl.get('note', '')}</i>{hold_str}"
                    )
                    state["last_trailing_action"] = action_text
                    state["last_alert_ts"] = now_ts
                    return
            elif not active_tsl:
                state["last_trailing_action"] = None

            # ── LONG SIGNAL ────────────────────────────────
            new_signal_L = None
            if code_L == "FULL" and adj_L >= thr_full:
                new_signal_L = "LONG_FULL"
            elif code_L == "HALF" and adj_L >= thr_half:
                new_signal_L = "LONG_HALF"

            if new_signal_L and (new_signal_L != state["last_signal"] or time_since_last > cooldown):
                size_label = "FULL SIZE 🟢🟢" if new_signal_L == "LONG_FULL" else "HALF SIZE 🟡"
                rr1        = lvl_L.get("rr1", 0)
                rr_q       = "⭐⭐⭐" if rr1 >= 3 else ("⭐⭐" if rr1 >= 2 else "⭐")
                wib        = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
                # Momentum Hold advisory
                hold_str = ""
                if mom_hold.get("signal"):
                    reasons = " · ".join(mom_hold.get("reasons", [])[:3])
                    hold_str = (
                        f"\n💡 <b>MOMENTUM {mom_hold['strength']}</b> — Pertimbangkan TAHAN TP1\n"
                        f"   {reasons}\n"
                    )
                # [P6] Macro trend label
                macro_icon = "📈" if macro_trend == "UPTREND" else ("↔️" if macro_trend == "SIDEWAYS" else "📉")
                # [P4] StochRSI gatekeeper label
                stoch_str = f"✅ {stoch_gk_reason[:60]}" if stoch_gk_ok else f"⚠️ {stoch_gk_reason[:60]}"
                # [P5] Trailing SL hint
                trailing_long  = trailing.get("long", {})
                trailing_str   = trailing_long.get("action", "") if trailing_long.get("applicable") else ""
                _send_telegram(
                    f"🚀 <b>SINYAL LONG — {symbol}</b>\n"
                    f"{'─'*28}\n"
                    f"📊 Skor: <b>{adj_L:.0f}/71 pts</b> ({result['long']['pct']:.1f}%)\n"
                    f"🎯 Posisi: <b>{size_label}</b>\n"
                    f"{macro_icon} Tren Macro: <b>{macro_trend}</b> | Regime: {threshold_regime}\n"
                    f"🕐 Sesi: {variables.get('session', 'N/A')} (×{variables.get('SESSION_MULT',1.0):.2f})\n"
                    f"{'─'*28}\n"
                    f"💰 <b>ENTRY</b>: ${close_price:.6f}\n"
                    f"✅ <b>Status Entry: BOLEH ENTRY SEKARANG</b>\n"
                    f"🛡️ <b>Stop Loss</b>: ${lvl_L['sl_structure']:.6f} "
                    f"({lvl_L['dist_sl']:+.2f}%) [{lvl_L['sl_label']}]\n\n"
                    f"🎯 <b>Take Profit:</b>\n"
                    f"  TP1: ${lvl_L['tp1']:.6f} (+{lvl_L['dist_tp1']:.2f}%) | R:R {lvl_L['rr1']}× [{lvl_L['tp1_label']}]\n"
                    f"  TP2: ${lvl_L['tp2']:.6f} (+{lvl_L['dist_tp2']:.2f}%) | R:R {lvl_L['rr2']}×\n"
                    f"  TP3: ${lvl_L['tp3']:.6f} (+{lvl_L['dist_tp3']:.2f}%) | R:R {lvl_L['rr3']}×\n"
                    f"{hold_str}"
                    f"R:R Quality: {rr_q}\n"
                    + (f"📊 Trailing SL: {trailing_str}\n" if trailing_str else "")
                    + f"🔬 StochRSI: {stoch_str}\n"
                    f"🕐 {wib} WIB"
                )
                state["last_signal"]   = new_signal_L
                state["last_alert_ts"] = now_ts
                return

            # ── SHORT SIGNAL ───────────────────────────────
            new_signal_S = None
            if code_S == "FULL" and adj_S >= thr_full:
                new_signal_S = "SHORT_FULL"
            elif code_S == "HALF" and adj_S >= thr_half:
                new_signal_S = "SHORT_HALF"

            if new_signal_S and (new_signal_S != state["last_signal"] or time_since_last > cooldown):
                size_label = "FULL SIZE 🔴🔴" if new_signal_S == "SHORT_FULL" else "HALF SIZE 🟠"
                rr1        = lvl_S.get("rr1", 0)
                rr_q       = "⭐⭐⭐" if rr1 >= 3 else ("⭐⭐" if rr1 >= 2 else "⭐")
                wib        = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
                _send_telegram(
                    f"📉 <b>SINYAL SHORT — {symbol}</b>\n"
                    f"{'─'*28}\n"
                    f"📊 Skor: <b>{adj_S:.0f}/71 pts</b> ({result['short']['pct']:.1f}%)\n"
                    f"🎯 Posisi: <b>{size_label}</b>\n"
                    f"{'─'*28}\n"
                    f"💰 <b>ENTRY</b>: ${close_price:.6f}\n"
                    f"✅ <b>Status Entry: BOLEH ENTRY SEKARANG</b>\n"
                    f"🛡️ <b>Stop Loss</b>: ${lvl_S['sl_structure']:.6f} "
                    f"({lvl_S['dist_sl']:+.2f}%) [{lvl_S['sl_label']}]\n\n"
                    f"🎯 <b>Take Profit:</b>\n"
                    f"  TP1: ${lvl_S['tp1']:.6f} ({lvl_S['dist_tp1']:+.2f}%) | R:R {lvl_S['rr1']}× [{lvl_S['tp1_label']}]\n"
                    f"  TP2: ${lvl_S['tp2']:.6f} ({lvl_S['dist_tp2']:+.2f}%) | R:R {lvl_S['rr2']}×\n"
                    f"  TP3: ${lvl_S['tp3']:.6f} ({lvl_S['dist_tp3']:+.2f}%) | R:R {lvl_S['rr3']}×\n\n"
                    f"R:R Quality: {rr_q}\n"
                    f"🕐 {wib} WIB"
                )
                state["last_signal"]   = new_signal_S
                state["last_alert_ts"] = now_ts
                return

            logger.info(
                f"[{symbol}] L={adj_L:.0f} ({code_L}) | S={adj_S:.0f} ({code_S})"
                f" | ${close_price:.4f} — no signal"
            )

    except Exception as e:
        logger.exception(f"[{symbol}] Evaluation error: {e}")


# ============================================================
# MAIN LOOP
# ============================================================
def _monitor_loop() -> None:
    logger.info("🔍 Signal Monitor loop started")

    # Startup notification
    wib = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    _send_telegram(
        f"🤖 <b>Protocol 9.6 Signal Monitor AKTIF</b>\n"
        f"{'─'*28}\n"
        f"✅ Monitoring {len(MONITOR_PAIRS)} pair setiap 15 menit\n"
        f"Pairs: {', '.join(MONITOR_PAIRS)}\n\n"
        f"Notifikasi akan dikirim otomatis saat:\n"
        f"  🚀 LONG signal (skor ≥ 36/71)\n"
        f"  📉 SHORT signal (skor ≥ 36/71)\n"
        f"  ⚠️ EXIT ALERT\n"
        f"  🚨 KILL SWITCH\n\n"
        f"🕐 Start: {wib} WIB"
    )

    while True:
        try:
            t0 = time.time()
            logger.info(f"🔄 Cycle — {datetime.now().strftime('%H:%M:%S')}")
            entries   = _load_trade_entries()
            all_pairs = list(set(MONITOR_PAIRS) | set(entries.keys()))

            for sym in all_pairs:
                _evaluate_pair(sym, entries)
                time.sleep(3)  # anti rate-limit

            elapsed = time.time() - t0
            sleep_t = max(0, POLL_INTERVAL_SECONDS - elapsed)
            logger.info(f"✅ Cycle done {elapsed:.1f}s — sleep {sleep_t:.0f}s")
            time.sleep(sleep_t)

        except Exception as e:
            logger.exception(f"Monitor loop error: {e}")
            time.sleep(60)


# ============================================================
# PUBLIC API
# ============================================================
def start_background_monitor() -> threading.Thread | None:
    """Jalankan monitor sebagai background daemon thread — hanya sekali."""
    if _started_flag.is_set():
        logger.info("SignalMonitor already running — skipping")
        return None
    _started_flag.set()
    t = threading.Thread(target=_monitor_loop, name="SignalMonitor", daemon=True)
    t.start()
    logger.info(f"✅ SignalMonitor thread started (id={t.ident})")
    return t


# ============================================================
# TEST FUNCTION — Kirim notifikasi test PENDLE ke Telegram
# ============================================================
def test_send_pendle_notification() -> dict:
    """
    One-shot test: fetch PENDLE data, score, dan kirim notifikasi
    ke Telegram tanpa menjalankan full monitoring loop.
    Return dict dengan status dan detail.
    """
    import algo_scoring

    symbol = "PENDLEUSDT"
    logger.info(f"[TEST] Fetching data for {symbol}...")

    df = _fetch_klines(symbol, interval="4h", limit=250)
    if df is None or len(df) < 22:
        return {"ok": False, "error": "Insufficient kline data", "symbol": symbol}

    df = _enrich_df(df, symbol)
    close_price = float(df.iloc[-1]["Close"])

    meta   = {"Symbol": symbol, "AVG_ENTRY_PRICE": None, "ENTRY_DATE": None}
    result = algo_scoring.calculate_71point_score(df, meta)
    if result is None:
        return {"ok": False, "error": "Scoring returned None", "symbol": symbol}

    adj_L     = result["long"]["total"]
    adj_S     = result["short"]["total"]
    code_L    = result["long"]["code"]
    code_S    = result["short"]["code"]
    lvl_L     = result["long"]["levels"]
    lvl_S     = result["short"]["levels"]
    variables = result.get("variables", {})
    mom_hold  = result.get("momentum_hold", {})
    trailing  = result.get("trailing_sl", {})
    gate_L    = result["long"]["gate"]
    gate_S    = result["short"]["gate"]

    macro_trend      = variables.get("macro_trend", "UNKNOWN")
    macro_trend_rsn  = variables.get("macro_trend_reason", "")
    threshold_regime = variables.get("threshold_regime", "UNKNOWN")
    thr_full         = variables.get("thr_full", SIGNAL_THRESHOLD_FULL)
    thr_half         = variables.get("thr_half", SIGNAL_THRESHOLD_HALF)
    session          = variables.get("session", "N/A")
    sess_mult        = variables.get("SESSION_MULT", 1.0)
    sess_block_type  = variables.get("session_block_type", "NONE")
    dist_to_liq      = variables.get("dist_to_liq")
    l2_zone          = variables.get("l2_zone", "N/A")
    stoch_gk_ok      = variables.get("stoch_gatekeeper_ok", True)
    stoch_gk_reason  = variables.get("stoch_gatekeeper_reason", "N/A")
    stoch_bonus      = variables.get("stoch_bonus_points", 0)
    atr_pct          = variables.get("H_atr_pct", 0.0)
    atr_extreme      = variables.get("atr_extreme", False)
    atr_avg20        = variables.get("atr_avg_20")
    dyn_buy_liq      = variables.get("dyn_buy_liq")
    swing_low20      = variables.get("swing_low_20")

    wib = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    macro_icon = "📈" if macro_trend == "UPTREND" else ("↔️" if macro_trend == "SIDEWAYS" else "📉")

    # Gate summary
    def _gate_short_label(gate: dict) -> str:
        status = gate.get("status", "CLEAR")
        if status == "CLEAR":   return "✅ CLEAR"
        if status == "WARNING": return "⚠️ WARNING"
        return "❌ BLOCKED"

    # L2 zone label
    _l2_labels = {
        "SWEET_SPOT": "✅ Sweet Spot (1-5%)",
        "SKIP": "⚡ Terlalu Dekat (<1%)",
        "WARNING": "⚠️ Warning Zone (5-10%)",
        "GAGAL": "❌ Gagal (>10%)",
    }
    l2_label = _l2_labels.get(l2_zone, f"N/A")

    # Trailing SL hint (tidak aktif karena tidak ada posisi open)
    trailing_note = "Tidak ada posisi aktif — trailing SL belum relevan"

    # Momentum
    hold_str = ""
    if mom_hold.get("signal"):
        reasons = " · ".join(mom_hold.get("reasons", [])[:2])
        hold_str = f"\n💡 <b>MOMENTUM {mom_hold['strength']}</b>: {reasons}"

    dyn_liq_str  = f"${dyn_buy_liq:.4f}" if dyn_buy_liq is not None else "N/A"
    swing_20_str = f"${swing_low20:.4f}" if swing_low20 is not None else "N/A"

    msg = (
        f"🧪 <b>[TEST] ANALISIS PENDLE — {symbol}</b>\n"
        f"{'─'*30}\n"
        f"🕐 {wib} WIB\n"
        f"💰 Harga: <b>${close_price:.6f}</b>\n"
        f"\n"
        f"━━ 📊 SKOR ━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  LONG : <b>{adj_L:.0f}/71 pts</b> ({result['long']['pct']:.1f}%) → <b>{code_L}</b>\n"
        f"  SHORT: <b>{adj_S:.0f}/71 pts</b> ({result['short']['pct']:.1f}%) → <b>{code_S}</b>\n"
        f"  Threshold: FULL≥{thr_full} | HALF≥{thr_half} | Regime: {threshold_regime}\n"
        f"\n"
        f"━━ {macro_icon} TREN MACRO ━━━━━━━━━━━━━━━━━━━\n"
        f"  {macro_trend_rsn}\n"
        f"\n"
        f"━━ 🚦 GATE STATUS ━━━━━━━━━━━━━━━━━━━━━\n"
        f"  LONG  Gate: {_gate_short_label(gate_L)}\n"
        f"  SHORT Gate: {_gate_short_label(gate_S)}\n"
        f"  [P1] Dyn Buy_Liq: {dyn_liq_str} | SwingLow: {swing_20_str}\n"
        f"  [P1] L2 Zone: {l2_label}" + (f" (dist={dist_to_liq:.2f}%)" if dist_to_liq is not None else "") + "\n"
        f"\n"
        f"━━ ⏱️ SESI & FILTER ━━━━━━━━━━━━━━━━━━━\n"
        f"  Sesi: {session} (×{sess_mult:.2f}) | Tipe: {sess_block_type}\n"
        f"  [P4] StochRSI GK: {'✅ OK' if stoch_gk_ok else '❌ FAIL'}"
        + (f" (+{stoch_bonus}pts bonus)" if stoch_bonus else "") + "\n"
        f"  {stoch_gk_reason[:80]}\n"
        f"\n"
        f"━━ 📐 ATR VOLATILITAS ━━━━━━━━━━━━━━━━━━\n"
        f"  ATR%: {atr_pct:.2f}% | Avg-20c: {atr_avg20:.2f}%{' | ⚠️ EKSTREM' if atr_extreme else ''}{'N/A' if atr_avg20 is None else ''}\n"
        f"\n"
        f"━━ 🛡️ LEVEL LONG ━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  SL: ${lvl_L['sl_structure']:.6f} ({lvl_L['dist_sl']:+.2f}%) [{lvl_L['sl_label']}]\n"
        f"  TP1: ${lvl_L['tp1']:.6f} (+{lvl_L['dist_tp1']:.2f}%) | R:R {lvl_L['rr1']}× [{lvl_L['tp1_label']}]\n"
        f"  TP2: ${lvl_L['tp2']:.6f} (+{lvl_L['dist_tp2']:.2f}%) | R:R {lvl_L['rr2']}×\n"
        f"  TP3: ${lvl_L['tp3']:.6f} (+{lvl_L['dist_tp3']:.2f}%) | R:R {lvl_L['rr3']}×\n"
        f"{hold_str}\n"
        f"\n"
        f"[P5] Trailing SL: {trailing_note}"
    )

    ok = _send_telegram(msg)
    return {
        "ok": ok,
        "symbol": symbol,
        "close": close_price,
        "adj_L": adj_L, "code_L": code_L,
        "adj_S": adj_S, "code_S": code_S,
        "macro_trend": macro_trend,
        "threshold_regime": threshold_regime,
        "thr_full": thr_full, "thr_half": thr_half,
        "l2_zone": l2_zone,
        "stoch_gk_ok": stoch_gk_ok,
        "telegram_sent": ok,
    }
