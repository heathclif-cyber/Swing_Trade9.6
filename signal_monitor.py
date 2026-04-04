"""
Protocol 9.6 — Signal Monitor
Background thread yang berjalan setiap 15 menit untuk memantau sinyal buy/sell
dan mengirimkan notifikasi Telegram secara otomatis.
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
# CONFIG — dibaca dari env vars (sama seperti protocol_96_ui)
# ============================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
DATABASE_URL       = os.environ.get("DATABASE_URL", "")

POLL_INTERVAL_SECONDS = 15 * 60   # 15 menit
SIGNAL_THRESHOLD_FULL = 53        # ADJ score untuk FULL SIZE ENTRY
SIGNAL_THRESHOLD_HALF = 36        # ADJ score untuk HALF SIZE ENTRY

# Pairs yang selalu dipantau (bahkan jika belum ada trade entry)
MONITOR_PAIRS = [
    "SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "PENDLEUSDT", "DOGEUSDT", "LINKUSDT", "ETHUSDT"
]

# ============================================================
# ALERT STATE — mencegah pengiriman ulang sinyal yang sama
# ============================================================
_alert_state: dict = {}
# Format: { "SUIUSDT": { "last_signal": "LONG_FULL", "last_alert_ts": 1234567890 } }

_state_lock = threading.Lock()


# ============================================================
# HELPERS
# ============================================================
def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram belum dikonfigurasi — pesan tidak terkirim")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        if resp.status_code == 200:
            logger.info("✅ Telegram alert sent")
        else:
            logger.warning(f"Telegram error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Telegram request failed: {e}")


def _get_pg_conn():
    if not DATABASE_URL:
        return None
    try:
        import psycopg2  # type: ignore
        url_str = DATABASE_URL.replace("postgres://", "postgresql://", 1)
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
    """Load trade entries dari PostgreSQL atau JSON fallback."""
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
            if conn:
                conn.close()
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
            resp = requests.get(url, params=params, timeout=10, verify=False)
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
                df["Buy_Volume"]    = df["Taker_Buy_Base"]
                df["Sell_Volume"]   = df["Total_Volume"] - df["Buy_Volume"]
                df["Volume_Delta"]  = df["Buy_Volume"] - df["Sell_Volume"]
                return df
        except Exception as e:
            logger.debug(f"Kline fetch failed {url}: {e}")
    logger.warning(f"All kline endpoints failed for {symbol} {interval}")
    return pd.DataFrame()


def _apply_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["EMA_21"]  = ta.ema(df["Close"], length=21)
    df["EMA_50"]  = ta.ema(df["Close"], length=50)
    df["EMA_200"] = ta.ema(df["Close"], length=200)
    df["RSI_6"]   = ta.rsi(df["Close"], length=6)
    atr = ta.atr(df["High"], df["Low"], df["Close"], length=14)
    df["ATR_14"]  = atr
    return df


# ============================================================
# SIGNAL EVALUATION
# ============================================================
def _evaluate_pair(symbol: str, trade_entries: dict) -> None:
    """Ambil data, hitung skor, dan kirim alert jika sinyal actionable."""
    try:
        import algo_scoring  # lazy import agar tidak circular

        df = _fetch_klines(symbol, interval="4h", limit=250)
        if df is None or len(df) < 22:
            logger.warning(f"[{symbol}] Insufficient data ({len(df) if df is not None else 0} rows)")
            return

        df = _apply_indicators(df)

        # Ambil avg entry price jika ada posisi aktif
        coin_data   = trade_entries.get(symbol, {})
        entry_list  = coin_data.get("entries", [])
        sales_list  = coin_data.get("sales", [])

        total_cost = sum(e["price"] * e["qty"] for e in entry_list)
        total_qty  = sum(e["qty"] for e in entry_list)
        sold_qty   = sum(s["qty"] for s in sales_list)
        remaining_qty = max(0.0, total_qty - sold_qty)

        avg_entry = (total_cost / total_qty) if total_qty > 0 else None
        is_active = (avg_entry is not None) and (remaining_qty > 0)

        meta = {
            "Symbol":          symbol,
            "AVG_ENTRY_PRICE": avg_entry if is_active else None,
            "ENTRY_DATE":      entry_list[-1].get("date") if entry_list else None,
        }

        result = algo_scoring.calculate_71point_score(df, meta)
        if result is None:
            logger.warning(f"[{symbol}] Scoring returned None")
            return

        close_price = float(df.iloc[-1]["Close"])
        adj_L = result["long"]["total"]
        adj_S = result["short"]["total"]
        dec_L = result["long"]["decision"]
        dec_S = result["short"]["decision"]
        code_L = result["long"]["code"]
        code_S = result["short"]["code"]
        lvl_L  = result["long"]["levels"]
        lvl_S  = result["short"]["levels"]
        exit_r = result.get("exit", {})
        emerg  = result.get("emergency", {})

        now_ts = time.time()
        signal_key = f"{symbol}"

        with _state_lock:
            state = _alert_state.setdefault(signal_key, {
                "last_signal": None,
                "last_alert_ts": 0,
                "exit_alerted": False,
                "kill_alerted": False,
            })

            # ── Cooldown: jangan kirim sinyal yang sama dalam 4 jam ──
            cooldown = 4 * 3600
            time_since_last = now_ts - state["last_alert_ts"]

            # ── KILL SWITCH / EMERGENCY ──
            kill_switch = (
                not df.empty
                and len(df) >= 2
                and "EMA_21" in df.columns
                and float(df.iloc[-2]["Close"]) < float(df.iloc[-2].get("EMA_21", float("inf")))
            )
            if is_active and kill_switch and not state["kill_alerted"]:
                ema21_val = float(df.iloc[-2].get("EMA_21", 0))
                msg = (
                    f"💀 <b>KILL SWITCH — {symbol}</b>\n"
                    f"Candle H4 closed di bawah EMA21 (${ema21_val:.4f})\n"
                    f"Harga sekarang: <b>${close_price:.4f}</b>\n\n"
                    f"❌ <b>Instruksi:</b> PERTIMBANGKAN EXIT PENUH.\n"
                    f"Struktur bullish resmi batal.\n"
                    f"Avg Entry: ${avg_entry:.4f} | PnL: {((close_price/avg_entry)-1)*100:+.2f}%"
                    if avg_entry else ""
                )
                _send_telegram(msg)
                state["kill_alerted"] = True
                state["last_alert_ts"] = now_ts
                return

            # Reset kill alert jika harga naik kembali di atas EMA21
            if not kill_switch:
                state["kill_alerted"] = False

            # ── EXIT SIGNALS (jika ada posisi aktif) ──
            exit_signals = exit_r.get("signals", [])
            hard_exits   = [e for e in exit_signals if e[0] == "❌"]
            warn_exits   = [e for e in exit_signals if e[0] == "⚠️"]

            if is_active and hard_exits and not state["exit_alerted"]:
                exit_lines = "\n".join(
                    f"  {icon} {name}: {val} ({cond})"
                    for icon, name, val, cond in exit_signals[:5]
                )
                pnl_str = f"{((close_price/avg_entry)-1)*100:+.2f}%" if avg_entry else "N/A"
                msg = (
                    f"⚠️ <b>EXIT ALERT — {symbol}</b>\n"
                    f"{'─'*30}\n"
                    f"Harga: <b>${close_price:.6f}</b> | PnL: <b>{pnl_str}</b>\n\n"
                    f"<b>Sinyal keluar terdeteksi:</b>\n{exit_lines}\n\n"
                    f"📋 Rekomendasi: <b>{exit_r.get('recommendation', 'PARTIAL EXIT')}</b>\n"
                    f"{'─'*30}\n"
                    f"TP1: ${lvl_L['tp1']:.4f} | SL: ${lvl_L['sl_structure']:.4f}"
                )
                _send_telegram(msg)
                state["exit_alerted"] = True
                state["last_alert_ts"] = now_ts
                return

            # Reset exit alert jika sinyal menghilang
            if not hard_exits:
                state["exit_alerted"] = False

            # ── BUY (LONG) SIGNAL ──
            new_signal_L = None
            if code_L == "FULL" and adj_L >= SIGNAL_THRESHOLD_FULL:
                new_signal_L = "LONG_FULL"
            elif code_L == "HALF" and adj_L >= SIGNAL_THRESHOLD_HALF:
                new_signal_L = "LONG_HALF"

            if new_signal_L and (new_signal_L != state["last_signal"] or time_since_last > cooldown):
                size_label  = "FULL SIZE 🟢🟢" if new_signal_L == "LONG_FULL" else "HALF SIZE 🟡"
                rr1 = lvl_L.get("rr1", 0)
                rr_quality = "⭐⭐⭐ Excellent" if rr1 >= 3 else ("⭐⭐ Good" if rr1 >= 2 else "⭐ Acceptable")
                msg = (
                    f"🚀 <b>SINYAL LONG — {symbol}</b>\n"
                    f"{'─'*30}\n"
                    f"📊 Skor: <b>{adj_L}/71 pts</b> ({result['long']['pct']:.1f}%)\n"
                    f"🎯 Ukuran Posisi: <b>{size_label}</b>\n"
                    f"{'─'*30}\n"
                    f"💰 <b>ENTRY</b>: ${close_price:.6f}\n"
                    f"🛡️ <b>Stop Loss</b>: ${lvl_L['sl_structure']:.6f} "
                    f"({lvl_L['dist_sl']:+.2f}%) [{lvl_L['sl_label']}]\n\n"
                    f"🎯 <b>Target Profit:</b>\n"
                    f"  TP1: ${lvl_L['tp1']:.6f} (+{lvl_L['dist_tp1']:.2f}%)"
                    f" | R:R {lvl_L['rr1']}× [{lvl_L['tp1_label']}]\n"
                    f"  TP2: ${lvl_L['tp2']:.6f} (+{lvl_L['dist_tp2']:.2f}%)"
                    f" | R:R {lvl_L['rr2']}× [{lvl_L['tp2_label']}]\n"
                    f"  TP3: ${lvl_L['tp3']:.6f} (+{lvl_L['dist_tp3']:.2f}%)"
                    f" | R:R {lvl_L['rr3']}×\n\n"
                    f"📈 Kualitas R:R: {rr_quality}\n"
                    f"{'─'*30}\n"
                    f"🕐 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} WIB"
                )
                _send_telegram(msg)
                state["last_signal"]    = new_signal_L
                state["last_alert_ts"]  = now_ts
                return

            # ── SELL (SHORT) SIGNAL ──
            new_signal_S = None
            if code_S == "FULL" and adj_S >= SIGNAL_THRESHOLD_FULL:
                new_signal_S = "SHORT_FULL"
            elif code_S == "HALF" and adj_S >= SIGNAL_THRESHOLD_HALF:
                new_signal_S = "SHORT_HALF"

            if new_signal_S and (new_signal_S != state["last_signal"] or time_since_last > cooldown):
                size_label  = "FULL SIZE 🔴🔴" if new_signal_S == "SHORT_FULL" else "�半 SIZE 🟠"
                rr1 = lvl_S.get("rr1", 0)
                rr_quality = "⭐⭐⭐ Excellent" if rr1 >= 3 else ("⭐⭐ Good" if rr1 >= 2 else "⭐ Acceptable")
                msg = (
                    f"📉 <b>SINYAL SHORT — {symbol}</b>\n"
                    f"{'─'*30}\n"
                    f"📊 Skor: <b>{adj_S}/71 pts</b> ({result['short']['pct']:.1f}%)\n"
                    f"🎯 Ukuran Posisi: <b>{size_label}</b>\n"
                    f"{'─'*30}\n"
                    f"💰 <b>ENTRY</b>: ${close_price:.6f}\n"
                    f"🛡️ <b>Stop Loss</b>: ${lvl_S['sl_structure']:.6f} "
                    f"({lvl_S['dist_sl']:+.2f}%) [{lvl_S['sl_label']}]\n\n"
                    f"🎯 <b>Target Profit:</b>\n"
                    f"  TP1: ${lvl_S['tp1']:.6f} ({lvl_S['dist_tp1']:+.2f}%)"
                    f" | R:R {lvl_S['rr1']}× [{lvl_S['tp1_label']}]\n"
                    f"  TP2: ${lvl_S['tp2']:.6f} ({lvl_S['dist_tp2']:+.2f}%)"
                    f" | R:R {lvl_S['rr2']}× [{lvl_S['tp2_label']}]\n"
                    f"  TP3: ${lvl_S['tp3']:.6f} ({lvl_S['dist_tp3']:+.2f}%)"
                    f" | R:R {lvl_S['rr3']}×\n\n"
                    f"📉 Kualitas R:R: {rr_quality}\n"
                    f"{'─'*30}\n"
                    f"🕐 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} WIB"
                )
                _send_telegram(msg)
                state["last_signal"]    = new_signal_S
                state["last_alert_ts"]  = now_ts
                return

            # Jika tidak ada sinyal actionable, log saja
            logger.info(
                f"[{symbol}] L={adj_L:.0f} ({code_L}) | S={adj_S:.0f} ({code_S})"
                f" | Price=${close_price:.4f} — No new signal"
            )

    except Exception as e:
        logger.exception(f"[{symbol}] Evaluation error: {e}")


# ============================================================
# MAIN LOOP
# ============================================================
def _monitor_loop() -> None:
    logger.info("🔍 Signal Monitor started — polling every 15 minutes")

    # Kirim pesan startup ke Telegram
    _send_telegram(
        "🤖 <b>Protocol 9.6 Signal Monitor AKTIF</b>\n"
        f"Memantau {len(MONITOR_PAIRS)} pair setiap 15 menit.\n"
        f"Pairs: {', '.join(MONITOR_PAIRS)}\n"
        f"🕐 Start: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} WIB"
    )

    while True:
        try:
            cycle_start = time.time()
            logger.info(f"🔄 Signal Monitor cycle — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            trade_entries = _load_trade_entries()

            # Gabungkan MONITOR_PAIRS + pair dari trade_entries (yang mungkin baru ditambahkan)
            all_pairs = list(set(MONITOR_PAIRS) | set(trade_entries.keys()))

            for symbol in all_pairs:
                _evaluate_pair(symbol, trade_entries)
                time.sleep(2)  # jeda antar pair agar tidak rate-limit Binance

            elapsed = time.time() - cycle_start
            sleep_time = max(0, POLL_INTERVAL_SECONDS - elapsed)
            logger.info(f"✅ Cycle done in {elapsed:.1f}s — sleeping {sleep_time:.0f}s")
            time.sleep(sleep_time)

        except Exception as e:
            logger.exception(f"Monitor loop error: {e}")
            time.sleep(60)  # tunggu 1 menit lalu coba lagi


def start_background_monitor() -> threading.Thread:
    """Jalankan monitor sebagai background daemon thread.
    Dipanggil dari protocol_96_ui.py saat startup."""
    t = threading.Thread(target=_monitor_loop, name="SignalMonitor", daemon=True)
    t.start()
    logger.info("✅ SignalMonitor background thread started")
    return t
