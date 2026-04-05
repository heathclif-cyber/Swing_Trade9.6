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
SIGNAL_THRESHOLD_FULL = 53        # ADJ score untuk FULL SIZE ENTRY
SIGNAL_THRESHOLD_HALF = 36        # ADJ score untuk HALF SIZE ENTRY

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
    """Enrichment pipeline lengkap — OI + Funding Rate + CVD + Indicators.
    Identik dengan yang digunakan web dashboard agar skor konsisten."""
    df = _apply_indicators(df)

    # Merge Open Interest
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

    # Merge Funding Rate
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

        now_ts = time.time()

        with _state_lock:
            state = _alert_state.setdefault(symbol, {
                "last_signal": None,
                "last_alert_ts": 0,
                "exit_alerted": False,
                "kill_alerted": False,
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
                    f"🕯️ <b>SL WICK ALERT — {symbol}</b>\n"
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
                    f"💀 <b>KILL SWITCH — {symbol}</b>\n"
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

            # ── EXIT SIGNALS ───────────────────────────────
            exit_signals = exit_r.get("signals", [])
            hard_exits   = [e for e in exit_signals if e[0] == "❌"]

            if is_active and hard_exits and not state["exit_alerted"]:
                pnl_str   = f"{((close_price/avg_entry)-1)*100:+.2f}%" if avg_entry else "N/A"
                exit_lines = "\n".join(
                    f"  {icon} {name}: {val} ({cond})"
                    for icon, name, val, cond in exit_signals[:5]
                )
                _send_telegram(
                    f"⚠️ <b>EXIT ALERT — {symbol}</b>\n"
                    f"{'─'*28}\n"
                    f"Harga: <b>${close_price:.6f}</b> | PnL: <b>{pnl_str}</b>\n\n"
                    f"<b>Sinyal keluar:</b>\n{exit_lines}\n\n"
                    f"📋 Rekomendasi: <b>{exit_r.get('recommendation','PARTIAL EXIT')}</b>"
                )
                state["exit_alerted"] = True
                state["last_alert_ts"] = now_ts
                return

            if not hard_exits:
                state["exit_alerted"] = False

            # ── LONG SIGNAL ────────────────────────────────
            new_signal_L = None
            if code_L == "FULL" and adj_L >= SIGNAL_THRESHOLD_FULL:
                new_signal_L = "LONG_FULL"
            elif code_L == "HALF" and adj_L >= SIGNAL_THRESHOLD_HALF:
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
                _send_telegram(
                    f"🚀 <b>SINYAL LONG — {symbol}</b>\n"
                    f"{'─'*28}\n"
                    f"📊 Skor: <b>{adj_L:.0f}/71 pts</b> ({result['long']['pct']:.1f}%)\n"
                    f"🎯 Posisi: <b>{size_label}</b>\n"
                    f"{'─'*28}\n"
                    f"💰 <b>ENTRY</b>: ${close_price:.6f}\n"
                    f"🛡️ <b>Stop Loss</b>: ${lvl_L['sl_structure']:.6f} "
                    f"({lvl_L['dist_sl']:+.2f}%) [{lvl_L['sl_label']}]\n\n"
                    f"🎯 <b>Take Profit:</b>\n"
                    f"  TP1: ${lvl_L['tp1']:.6f} (+{lvl_L['dist_tp1']:.2f}%) | R:R {lvl_L['rr1']}× [{lvl_L['tp1_label']}]\n"
                    f"  TP2: ${lvl_L['tp2']:.6f} (+{lvl_L['dist_tp2']:.2f}%) | R:R {lvl_L['rr2']}×\n"
                    f"  TP3: ${lvl_L['tp3']:.6f} (+{lvl_L['dist_tp3']:.2f}%) | R:R {lvl_L['rr3']}×\n"
                    f"{hold_str}"
                    f"R:R Quality: {rr_q}\n"
                    f"🕐 {wib} WIB"
                )
                state["last_signal"]   = new_signal_L
                state["last_alert_ts"] = now_ts
                return

            # ── SHORT SIGNAL ───────────────────────────────
            new_signal_S = None
            if code_S == "FULL" and adj_S >= SIGNAL_THRESHOLD_FULL:
                new_signal_S = "SHORT_FULL"
            elif code_S == "HALF" and adj_S >= SIGNAL_THRESHOLD_HALF:
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
        f"  💀 KILL SWITCH\n\n"
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
