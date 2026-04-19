"""
Protocol 9.6 — Signal Monitor
Background thread yang berjalan setiap 15 menit untuk memantau sinyal buy/sell
dan mengirimkan notifikasi Telegram secara otomatis.

[REFACTOR] Single Source of Truth:
  Semua penarikan data dilakukan oleh protocol_96_enrichment.get_fully_enriched_data().
  Signal monitor TIDAK boleh merakit data sendiri.
"""
import threading
import time
import logging
import os
import json
import requests
import pandas as pd  # type: ignore
import protocol_96_enrichment as enrichment
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

# ML Signal Engine
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ml.ml_signal import MLSignalEngine
_ml_engine = MLSignalEngine()

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
    'SOLUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT',
    'TONUSDT', 'ADAUSDT', 'TRXUSDT', 'SHIBUSDT', 'AVAXUSDT',
    'LINKUSDT', 'DOTUSDT', 'SUIUSDT', 'POLUSDT', 'NEARUSDT',
    'PEPEUSDT', 'TAOUSDT', 'APTOSUSDT', 'ARBUSDT', 'WLFIUSDT'
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
    """Load trade entries dari PostgreSQL.

    STRICT MODE (DATABASE_URL aktif): baca HANYA dari PostgreSQL.
    Jika koneksi atau query gagal, log DATABASE ERROR dan return {}.
    JANGAN fallback ke JSON agar kondisi database rusak langsung terdeteksi.

    DEVELOPMENT (tanpa DATABASE_URL): fallback ke file JSON lokal.
    """
    db = _db_url()
    if db:
        # ── STRICT: PostgreSQL only ──
        conn = _get_pg_conn()
        if conn is None:
            logger.error(
                "[DB ERROR] _load_trade_entries: Tidak dapat terhubung ke PostgreSQL. "
                "Data dikembalikan kosong {}. Periksa DATABASE_URL / koneksi Supabase."
            )
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM kv_store WHERE key = 'trade_entries'")
                row = cur.fetchone()
            conn.close()
            return json.loads(row[0]) if row else {}
        except Exception as e:
            logger.error(
                f"[DB ERROR] _load_trade_entries: Query PostgreSQL gagal — {e}. "
                "Data dikembalikan kosong {}."
            )
            try: conn.close()
            except: pass
            return {}
    # ── DEVELOPMENT: fallback ke file JSON lokal ──
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

def _load_alert_state() -> dict:
    """Load alert state dari PostgreSQL.

    STRICT MODE (DATABASE_URL aktif): baca HANYA dari PostgreSQL.
    Jika gagal, log DATABASE ERROR dan return {}.
    JANGAN fallback ke JSON.

    DEVELOPMENT (tanpa DATABASE_URL): return {} (state ephemeral di memory).
    """
    db = _db_url()
    if db:
        # ── STRICT: PostgreSQL only ──
        conn = _get_pg_conn()
        if conn is None:
            logger.error(
                "[DB ERROR] _load_alert_state: Tidak dapat terhubung ke PostgreSQL. "
                "Alert state dikembalikan kosong."
            )
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM kv_store WHERE key = 'alert_state'")
                row = cur.fetchone()
            conn.close()
            return json.loads(row[0]) if row else {}
        except Exception as e:
            logger.error(
                f"[DB ERROR] _load_alert_state: Query PostgreSQL gagal — {e}. "
                "Alert state dikembalikan kosong."
            )
            try: conn.close()
            except: pass
    return {}

def _save_alert_state(state: dict) -> None:
    """Simpan _alert_state ke PostgreSQL.

    STRICT MODE (DATABASE_URL aktif): tulis HANYA ke PostgreSQL.
    Jika gagal, log DATABASE ERROR. JANGAN fallback ke JSON.

    DEVELOPMENT (tanpa DATABASE_URL): fallback ke file JSON lokal.
    """
    db = _db_url()
    if db:
        # ── STRICT: PostgreSQL only ──
        conn = _get_pg_conn()
        if conn is None:
            logger.error(
                "[DB ERROR] _save_alert_state: Tidak dapat terhubung ke PostgreSQL. "
                "Alert state TIDAK disimpan."
            )
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kv_store (key, value, updated_at)
                    VALUES ('alert_state', %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                      SET value = EXCLUDED.value, updated_at = NOW()
                """, (json.dumps(state),))
            conn.commit()
            conn.close()
            return
        except Exception as e:
            logger.error(f"[DB ERROR] _save_alert_state: PostgreSQL write gagal — {e}. State TIDAK disimpan.")
            try: conn.close()
            except: pass
            return
    # ── DEVELOPMENT: fallback ke file JSON lokal ──
    path = os.environ.get(
        "ALERT_STATE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_state.json")
    )
    try:
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"_save_alert_state JSON failed: {e}")


# ============================================================
# SIGNAL EVALUATION
# ============================================================
def _normalize_m15_columns(df):
    import pandas as pd
    col_map = {
        'Open':           'open',
        'High':           'high',
        'Low':            'low',
        'Close':          'close',
        'Total_Volume':   'volume',
        'Taker_Buy_Base': 'taker_buy_volume',
        'Sell_Volume':    'taker_sell_volume',
        'Open_Time':      'open_time',
    }
    df = df.copy()
    df.columns = [col_map.get(c, c.lower()) for c in df.columns]
    # Hapus kolom duplikat — pertahankan yang pertama
    df = df.loc[:, ~df.columns.duplicated(keep='first')]
    # Set DatetimeIndex dari open_time (Unix ms → UTC datetime)
    if 'open_time' in df.columns:
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
        df = df.set_index('open_time')
        df.index.name = 'timestamp'
    elif not isinstance(df.index, pd.DatetimeIndex):
        # Fallback: coba konversi index yang ada
        try:
            df.index = pd.to_datetime(df.index, unit='ms', utc=True)
            df.index.name = 'timestamp'
        except Exception:
            pass
    return df

# ============================================================
# SIGNAL STABILITY — Anti-flip & Confirmation Buffer
# ============================================================
# Threshold confidence minimum untuk mengirim sinyal baru / flip.
# Sinyal < threshold → SKIP, tidak dikirim ke Telegram.
SIGNAL_CONF_MIN      = 0.55   # 55% — minimum entry signal pertama
SIGNAL_FLIP_CONF_MIN = 0.65   # 65% — minimum untuk flip arah (lebih ketat)

# Jumlah bar M15 berurutan yang harus konsisten sebelum flip diterima.
# 1 bar = 15 menit. Default 1 bar = 15 menit lock-in.
FLIP_CONFIRM_BARS    = 1

# Cooldown setelah flip expired — minimal 1 jam sebelum entry baru
FLIP_COOLDOWN_SECS   = 3600


def _check_signal_stability(state: dict, new_direction: str, confidence: float, now_ts: float) -> str:
    """
    Gate sebelum sinyal dikirim ke Telegram.
    Mengembalikan:
      'SEND'    — sinyal stabil, boleh dikirim
      'SKIP'    — confidence terlalu rendah, abaikan
      'HOLD'    — belum cukup bar konfirmasi, tahan dulu
      'COOLDOWN'— baru saja flip, tunggu cooldown

    State keys yang digunakan/diubah:
      pending_direction, pending_conf_count, last_flip_ts
    """
    last_direction = state.get("last_signal_direction")  # LONG / SHORT / None

    # --- Case 1: Tidak ada perubahan arah → langsung SEND (update strength)
    if last_direction == new_direction:
        state["pending_direction"]  = None
        state["pending_conf_count"] = 0
        return "SEND"

    # --- Case 2: Arah baru (flip atau entry pertama)
    is_flip = last_direction is not None

    # Cek confidence minimum
    min_conf = SIGNAL_FLIP_CONF_MIN if is_flip else SIGNAL_CONF_MIN
    if confidence < min_conf:
        logger.info(
            f"  [StabilityGate] SKIP — conf {confidence*100:.1f}% < {min_conf*100:.0f}% "
            f"({'flip' if is_flip else 'entry'})"
        )
        state["pending_direction"]  = None
        state["pending_conf_count"] = 0
        return "SKIP"

    # Cek cooldown setelah flip
    last_flip_ts = state.get("last_flip_ts")
    if is_flip and last_flip_ts and (now_ts - last_flip_ts) < FLIP_COOLDOWN_SECS:
        remaining = int((FLIP_COOLDOWN_SECS - (now_ts - last_flip_ts)) / 60)
        logger.info(f"  [StabilityGate] COOLDOWN — {remaining} mnt tersisa setelah flip terakhir")
        return "COOLDOWN"

    # Cek confirmation buffer (hanya untuk flip)
    if is_flip and FLIP_CONFIRM_BARS > 1:
        pending = state.get("pending_direction")
        if pending != new_direction:
            # Reset counter — arah baru mulai akumulasi
            state["pending_direction"]  = new_direction
            state["pending_conf_count"] = 1
            logger.info(
                f"  [StabilityGate] HOLD — flip {last_direction}→{new_direction} "
                f"bar 1/{FLIP_CONFIRM_BARS} (conf {confidence*100:.1f}%)"
            )
            return "HOLD"
        else:
            count = state.get("pending_conf_count", 0) + 1
            state["pending_conf_count"] = count
            if count < FLIP_CONFIRM_BARS:
                logger.info(
                    f"  [StabilityGate] HOLD — flip {last_direction}→{new_direction} "
                    f"bar {count}/{FLIP_CONFIRM_BARS}"
                )
                return "HOLD"
            # Cukup bar konfirmasi → izinkan SEND
            logger.info(
                f"  [StabilityGate] SEND — flip {last_direction}→{new_direction} "
                f"CONFIRMED setelah {count} bar, conf {confidence*100:.1f}%"
            )
            state["pending_direction"]  = None
            state["pending_conf_count"] = 0
            state["last_flip_ts"]       = now_ts
            return "SEND"

    # Entry pertama (no flip) → langsung SEND
    return "SEND"


def _evaluate_pair(symbol: str, trade_entries: dict) -> None:
    try:
        import algo_scoring  # lazy import

        # ── [SSOT] Semua data dari satu sumber ──────────────
        df, data_meta = enrichment.get_fully_enriched_data(symbol, interval="4h", limit=250)
        if df is None or df.empty or len(df) < 22:
            logger.warning(f"[{symbol}] Insufficient data")
            return

        # Fetch M15 untuk ML engine
        import data_engine
        try:
            df_m15 = data_engine.get_klines_rest(symbol, '15m', limit=300)
            if df_m15 is not None:
                df_m15 = _normalize_m15_columns(df_m15)
        except Exception as e:
            logger.warning(f"Failed to fetch M15 for {symbol}: {e}")
            df_m15 = None

        # ── [SYSTEM HEALTH] Data tidak lengkap → catat di state, BUKAN Telegram ──
        with _state_lock:
            sys_errors = _alert_state.setdefault("system_errors", {})
            if data_meta.get("data_incomplete"):
                missing = data_meta.get("missing_data", [])
                sys_errors[symbol] = {
                    "message": f"Data tidak tersedia: {', '.join(missing)}",
                    "time": datetime.now().strftime("%H:%M"),
                    "missing": missing,
                }
                _save_alert_state(_alert_state)
                logger.warning(f"[{symbol}] Data incomplete: {missing} — dicatat ke system_errors, skip evaluasi ML")
                return  # Skip evaluasi jika data tidak lengkap
            else:
                # Clear error jika data sudah kembali normal
                if symbol in sys_errors:
                    del sys_errors[symbol]
                    _save_alert_state(_alert_state)

        coin_data     = trade_entries.get(symbol, {})
        pos_side      = str(coin_data.get("position_side", "LONG")).upper()  # ✅ FIX: key adalah "position_side"
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
        result = algo_scoring.calculate_71point_score(df, meta, df_m15=df_m15, ml_engine=_ml_engine)
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
        thr_full   = variables.get("thr_full",   SIGNAL_THRESHOLD_FULL)
        thr_half   = variables.get("thr_half",   SIGNAL_THRESHOLD_HALF)
        thr_full_S = variables.get("thr_full_S", SIGNAL_THRESHOLD_FULL)   # Threshold SHORT (bisa berbeda)
        thr_half_S = variables.get("thr_half_S", SIGNAL_THRESHOLD_HALF)
        macro_trend      = variables.get("macro_trend", "UNKNOWN")
        threshold_regime = variables.get("threshold_regime", "UNKNOWN")
        session_block_type = variables.get("session_block_type", "NONE")
        stoch_gk_reason  = variables.get("stoch_gatekeeper_reason", "")
        stoch_gk_ok      = variables.get("stoch_gatekeeper_ok", True)

        now_ts = time.time()

        with _state_lock:
            state = _alert_state.setdefault(symbol, {
                "last_long_signal":    None,
                "last_short_signal":   None,
                "last_signal":         None,
                "last_signal_direction": None,  # [StabilityGate] arah terakhir yang DIKIRIM
                "pending_direction":   None,    # [StabilityGate] arah yang sedang diakumulasi
                "pending_conf_count":  0,       # [StabilityGate] jumlah bar konfirmasi
                "last_flip_ts":        None,    # [StabilityGate] ts terakhir flip berhasil
                "exit_alerted":        False,
                "tp1_alerted":         False,
                "tp2_alerted":         False,
                "tp3_alerted":         False,
                "entry_recorded_ts":   None,
                "entry_reminder_12h":  False,
                "entry_expired_sent":  False,
                "entry_pos_side":      None,
            })

            # Helper untuk PnL dinamis
            def get_pnl():
                if not avg_entry: return "N/A"
                if pos_side == "SHORT":
                    return f"{((avg_entry/close_price)-1)*100:+.2f}%"
                return f"{((close_price/avg_entry)-1)*100:+.2f}%"

            # ── [ENTRY TRACKER] Deteksi posisi baru / reset saat posisi tutup ──
            #  Saat user mencatat entry (is_active baru menjadi True), catat timestamp.
            if is_active and state.get("entry_recorded_ts") is None:
                # Gunakan tanggal entry terbaru dari data aktual user (lebih akurat dari now_ts)
                _last_entry_date = entry_list[-1].get("date") if entry_list else None
                try:
                    from datetime import datetime as _dt
                    _entry_ts = _dt.strptime(_last_entry_date, "%Y-%m-%d %H:%M:%S").timestamp() \
                        if _last_entry_date else now_ts
                except Exception:
                    _entry_ts = now_ts
                state["entry_recorded_ts"]  = _entry_ts
                state["entry_reminder_12h"] = False
                state["entry_expired_sent"] = False
                state["entry_pos_side"]     = pos_side
                _save_alert_state(_alert_state)
                logger.info(f"[{symbol}] Entry dicatat — pos_side={pos_side} entry_ts={_entry_ts}")
            elif not is_active and state.get("entry_recorded_ts") is not None:
                # Posisi ditutup — reset tracker
                state["entry_recorded_ts"]  = None
                state["entry_reminder_12h"] = False
                state["entry_expired_sent"] = False
                state["entry_pos_side"]     = None
                _save_alert_state(_alert_state)

            # ── [12-JAM REMINDER] Kirim update status 12 jam setelah entry ──
            ENTRY_REMINDER_HOURS = 12
            ENTRY_REMINDER_SECS  = ENTRY_REMINDER_HOURS * 3600
            entry_ts = state.get("entry_recorded_ts")
            if (is_active and entry_ts is not None
                    and not state.get("entry_expired_sent")
                    and (now_ts - entry_ts) >= ENTRY_REMINDER_SECS):

                # Tentukan current ML signal berdasarkan posisi user
                cur_ml_signal = variables.get("ml_signal", "FLAT")
                cur_ml_size   = variables.get("ml_size",   "SKIP")

                # Deteksi apakah sinyal berbalik arah dari posisi user
                recorded_side  = state.get("entry_pos_side", pos_side)
                signal_reversed = (
                    (recorded_side == "LONG"  and cur_ml_signal == "SHORT") or
                    (recorded_side == "SHORT" and cur_ml_signal == "LONG")
                )

                elapsed_h = (now_ts - entry_ts) / 3600
                wib_now   = datetime.now(timezone(timedelta(hours=8))).strftime("%d %b %Y %H:%M WITA")

                if signal_reversed:
                    # === SIGNAL EXPIRED ===
                    lvl_pos = lvl_S if recorded_side == "SHORT" else lvl_L
                    _send_telegram(
                        f"🔄 <b>SIGNAL EXPIRED — {symbol}</b>\n"
                        f"{'─'*30}\n"
                        f"⏱️ {wib_now}\n"
                        f"⌛ Posisi dibuka <b>{elapsed_h:.1f} jam</b> lalu\n"
                        f"{'─'*30}\n"
                        f"📌 Posisi Anda : <b>{recorded_side}</b>\n"
                        f"🤖 Signal ML kini: <b>{cur_ml_signal}</b> ({cur_ml_size}) — BERLAWANAN ARAH\n\n"
                        f"⚠️ <b>Signal dinyatakan EXPIRED</b>\n"
                        f"Harga saat ini : <b>${close_price:.6f}</b>\n"
                        f"PnL Floating   : <b>{get_pnl()}</b>\n"
                        f"SL : ${lvl_pos['sl_structure']:.6f} [{lvl_pos['sl_label']}]\n\n"
                        f"📋 <b>Rekomendasi:</b> Tinjau ulang posisi. "
                        f"Pertimbangkan close atau cut loss jika SL sudah tersentuh."
                    )
                    state["entry_expired_sent"] = True
                    _save_alert_state(_alert_state)

                elif not state.get("entry_reminder_12h"):
                    # === REMINDER 12 JAM — Sinyal masih searah (HALF/FULL) ===
                    lvl_pos    = lvl_S if pos_side == "SHORT" else lvl_L
                    signal_ok  = cur_ml_signal in ("LONG", "SHORT") and cur_ml_size in ("HALF", "FULL")
                    status_str = (
                        f"✅ <b>Signal MASIH AKTIF</b> — {cur_ml_signal} {cur_ml_size}"
                        if signal_ok else
                        f"⚠️ <b>Signal MELEMAH</b> — ML: {cur_ml_signal} ({cur_ml_size})"
                    )
                    tp1 = lvl_pos['tp1']
                    sl  = lvl_pos['sl_structure']
                    _send_telegram(
                        f"⏰ <b>REMINDER 12 JAM — {symbol} ({pos_side})</b>\n"
                        f"{'─'*30}\n"
                        f"📅 {wib_now}\n"
                        f"⌛ Posisi dibuka <b>{elapsed_h:.1f} jam</b> lalu\n"
                        f"{'─'*30}\n"
                        f"💲 Harga saat ini: <b>${close_price:.6f}</b>\n"
                        f"💰 PnL Floating  : <b>{get_pnl()}</b>\n"
                        f"{'─'*30}\n"
                        f"{status_str}\n\n"
                        f"🎯 TP1 : ${tp1:.6f} (+{lvl_pos['dist_tp1']:.2f}%)\n"
                        f"🛡️ SL  : ${sl:.6f} | R:R {lvl_pos['rr1']:.2f}×\n\n"
                        f"📊 ML Conf: {variables.get('ml_confidence', 0)*100:.1f}% "
                        f"| Size: {cur_ml_size}\n"
                        f"{'─'*30}\n"
                        f"<i>Cek kembali posisi dan pertimbangkan management risiko.</i>"
                    )
                    state["entry_reminder_12h"] = True
                    # Geser window ke 12 jam berikutnya — reset flag agar siklus 12h berulang
                    state["entry_recorded_ts"]  = now_ts
                    state["entry_reminder_12h"] = False  # reset agar 12 jam berikutnya bisa kirim lagi
                    _save_alert_state(_alert_state)

            # ── SL WICK FAKEOUT ALERT ──
            if is_active and sl_wick.get("sl_touched_wick") and not state.get("wick_alerted"):
                verdict = sl_wick.get("verdict", "N/A")
                action  = sl_wick.get("action", "")
                conf    = sl_wick.get("confidence_pct", 0)
                sl_level = lvl_S['sl_structure'] if pos_side == "SHORT" else lvl_L['sl_structure']

                body_ok = "✅ Body di atas SL" if sl_wick.get("body_above_sl") else "❌ Body TEMBUS SL"
                if pos_side == "SHORT":
                    body_ok = "✅ Body di bawah SL" if sl_wick.get("body_above_sl") else "❌ Body TEMBUS SL"

                cvd_ok  = "✅ CVD masih defend" if sl_wick.get("cvd_defending") else "❌ CVD memburuk"
                vol_ok  = "✅ Volume drop lemah" if sl_wick.get("low_volume_drop") else "⚠️ Volume normal"
                bull_ok = "✅ Candle aman" if sl_wick.get("bullish_body") else "❌ Candle berlawanan"

                _send_telegram(
                    f"🕯️ <b>SL WICK ALERT — {symbol} ({pos_side})</b>\n"
                    f"{'─'*28}\n"
                    f"Harga: <b>${close_price:.6f}</b> | PnL: {get_pnl()}\n"
                    f"SL Level: ${sl_level:.6f}\n\n"
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
                    _save_alert_state(_alert_state)
                    return

            if not sl_wick.get("sl_touched_wick") and state.get("wick_alerted", False):
                state["wick_alerted"] = False
                _save_alert_state(_alert_state)

            # ── TRACKING TAKE PROFIT 1, 2, 3 ─────────────────
            if is_active:
                high_price = float(df.iloc[-1]["High"])
                low_price  = float(df.iloc[-1]["Low"])

                mom_signal  = mom_hold.get("signal", False)
                mom_reasons = " · ".join(mom_hold.get("reasons", [])[:2])
                hold_reco = (
                    f"✅ <b>REKOMENDASI HOLD</b> (Momentum masih kuat: {mom_reasons})"
                    if mom_signal else "⚠️ <b>REKOMENDASI EXIT</b> (Momentum melemah, segera Take Profit)"
                )

                if pos_side == "SHORT":
                    tp1, tp2, tp3 = lvl_S['tp1'], lvl_S['tp2'], lvl_S['tp3']
                    tp1_hit, tp2_hit, tp3_hit = (low_price <= tp1), (low_price <= tp2), (low_price <= tp3)
                    hit_price = low_price
                else:
                    tp1, tp2, tp3 = lvl_L['tp1'], lvl_L['tp2'], lvl_L['tp3']
                    tp1_hit, tp2_hit, tp3_hit = (high_price >= tp1), (high_price >= tp2), (high_price >= tp3)
                    hit_price = high_price

                if tp1_hit and not state.get("tp1_alerted"):
                    _send_telegram(
                        f"🎯 <b>TP1 TERCAPAI — {symbol} ({pos_side})</b>\n"
                        f"{'\u2500'*28}\n"
                        f"Harga Menyentuh: <b>${hit_price:.6f}</b>\n"
                        f"Level TP1: ${tp1:.6f}\n\n"
                        f"Tindakan: <b>EXIT 30%</b> & GESER SL KE ENTRY (Breakeven)\n\n"
                        f"{hold_reco}"
                    )
                    state["tp1_alerted"] = True
                    _save_alert_state(_alert_state)

                if tp2_hit and not state.get("tp2_alerted"):
                    _send_telegram(
                        f"🎯🎯 <b>TP2 TERCAPAI — {symbol} ({pos_side})</b>\n"
                        f"{'\u2500'*28}\n"
                        f"Harga Menyentuh: <b>${hit_price:.6f}</b>\n"
                        f"Level TP2: ${tp2:.6f}\n\n"
                        f"Tindakan: <b>EXIT 40%</b> & GESER SL KE TP1\n\n"
                        f"{hold_reco}"
                    )
                    state["tp2_alerted"] = True
                    _save_alert_state(_alert_state)

                if tp3_hit and not state.get("tp3_alerted"):
                    _send_telegram(
                        f"🚀 <b>TP3 TERCAPAI (FINAL) — {symbol} ({pos_side})</b>\n"
                        f"{'\u2500'*28}\n"
                        f"Harga Menyentuh: <b>${hit_price:.6f}</b>\n"
                        f"Tindakan: <b>EXIT SISA 30%</b>. Trade Selesai."
                    )
                    state["tp3_alerted"] = True
                    _save_alert_state(_alert_state)

            if not is_active and state.get("tp1_alerted"):
                state["tp1_alerted"], state["tp2_alerted"], state["tp3_alerted"] = False, False, False
                _save_alert_state(_alert_state)

            # ── EXIT SIGNALS ───────────────────────────────
            exit_signals = exit_r.get("signals", [])
            hard_exits   = [e for e in exit_signals if e[0] == "❌"]

            if is_active and hard_exits and not state["exit_alerted"]:
                pnl_str   = get_pnl()  # Gunakan fungsi PnL dinamis SSOT
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
                _save_alert_state(_alert_state)
                return

            if not hard_exits and state.get("exit_alerted", False):
                state["exit_alerted"] = False
                _save_alert_state(_alert_state)

            # ── TRAILING SL — Update state untuk UI (tanpa Telegram) ──
            # Trailing SL info tersedia di UI via /api/system_health & /api/data
            # Telegram hanya untuk TP hits (sudah di atas) dan SL hit (di bawah)
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
            if active_tsl:
                state["last_trailing_stage"] = active_tsl.get("stage", "NONE")
                state["trailing_sl_action"]  = active_tsl.get("action", "")
            else:
                state["last_trailing_stage"] = "NONE"

            # ── LONG SIGNAL ────────────────────────────────
            new_signal_L = None
            ml_size_L    = result['long'].get('ml_size', 'SKIP')
            ml_conf_L    = result['long'].get('ml_confidence', 0.0)
            if ml_size_L in ('FULL', 'HALF') and result['long'].get('ml_signal') == 'LONG':
                new_signal_L = f"LONG_{ml_size_L}"

            if new_signal_L and new_signal_L != state.get("last_long_signal"):
                gate = _check_signal_stability(state, "LONG", ml_conf_L, now_ts)
                if gate != "SEND":
                    _save_alert_state(_alert_state)
                else:
                    size_label = "FULL SIZE 🟢🟢" if new_signal_L == "LONG_FULL" else "HALF SIZE 🟡"
                    rr1        = lvl_L.get("rr1", 0)
                    rr_q       = "⭐⭐⭐" if rr1 >= 3 else ("⭐⭐" if rr1 >= 2 else "⭐")
                    hold_str   = ""
                    if mom_hold.get("signal"):
                        reasons  = " · ".join(mom_hold.get("reasons", [])[:3])
                        hold_str = (
                            f"\n💡 <b>MOMENTUM {mom_hold['strength']}</b> — Pertimbangkan TAHAN TP1\n"
                            f"   {reasons}\n"
                        )
                    macro_icon   = "📈" if macro_trend == "UPTREND" else ("↔️" if macro_trend == "SIDEWAYS" else "📉")
                    stoch_str    = f"✅ {stoch_gk_reason[:60]}" if stoch_gk_ok else f"⚠️ {stoch_gk_reason[:60]}"
                    trailing_long = trailing.get("long", {})
                    trailing_str  = trailing_long.get("action", "") if trailing_long.get("applicable") else ""
                    wib = datetime.now(timezone(timedelta(hours=8))).strftime("%d %b %Y — %H:%M:%S WITA")
                    _send_telegram(
                        f"🚀 <b>SINYAL LONG — {symbol}</b>\n"
                        f"{'─'*28}\n"
                        f"⏳ <b>Waktu Sinyal:</b> {wib}\n"
                        f"💲 <b>Harga Entry:</b> <b>${close_price:.6f}</b>\n"
                        f"{'─'*28}\n"
                        f"🤖 ML: <b>{result['long'].get('ml_signal','?')}</b> | Conf: <b>{ml_conf_L*100:.1f}%</b> | Size: <b>{size_label}</b>\n"
                        f"📊 Proba — LONG: {result['long'].get('ml_proba',{}).get('LONG',0)*100:.1f}% | SHORT: {result['long'].get('ml_proba',{}).get('SHORT',0)*100:.1f}% | FLAT: {result['long'].get('ml_proba',{}).get('FLAT',0)*100:.1f}%\n"
                        f"{macro_icon} Tren Macro: <b>{macro_trend}</b> | Regime: {threshold_regime}\n"
                        f"🕐 Sesi: {variables.get('session', 'N/A')} (×{variables.get('SESSION_MULT',1.0):.2f})\n"
                        f"{'─'*28}\n"
                        f"🛡️ <b>Stop Loss</b>: ${lvl_L['sl_structure']:.6f} ({lvl_L['dist_sl']:+.2f}%) [{lvl_L['sl_label']}]\n\n"
                        f"🎯 <b>Take Profit:</b>\n"
                        f"  TP1: ${lvl_L['tp1']:.6f} (+{lvl_L['dist_tp1']:.2f}%) | R:R {lvl_L['rr1']}× [{lvl_L['tp1_label']}]\n"
                        f"  TP2: ${lvl_L['tp2']:.6f} (+{lvl_L['dist_tp2']:.2f}%) | R:R {lvl_L['rr2']}×\n"
                        f"  TP3: ${lvl_L['tp3']:.6f} (+{lvl_L['dist_tp3']:.2f}%) | R:R {lvl_L['rr3']}×\n"
                        f"{hold_str}"
                        f"R:R Quality: {rr_q}\n"
                        + (f"📊 Trailing SL: {trailing_str}\n" if trailing_str else "")
                        + f"🔬 StochRSI: {stoch_str}\n"
                        f"{'─'*28}\n"
                        f"⚠️ <i>Validasi harga sebelum entry! Sinyal ini valid di <b>${close_price:.6f}</b>. "
                        f"Jangan entry jika harga saat ini sudah jauh dari level tersebut.</i>"
                    )
                    state["last_long_signal"]      = new_signal_L
                    state["last_signal"]           = new_signal_L
                    state["last_signal_direction"] = "LONG"
                    state["last_signal_ts"]        = time.time()
                    state["last_signal_conf"]      = ml_conf_L
                    _save_alert_state(_alert_state)
                    return

            # ── SHORT SIGNAL ───────────────────────────────
            new_signal_S = None
            ml_size_S    = result['short'].get('ml_size', 'SKIP')
            ml_conf_S    = result['short'].get('ml_confidence', 0.0)
            if ml_size_S in ('FULL', 'HALF') and result['short'].get('ml_signal') == 'SHORT':
                new_signal_S = f"SHORT_{ml_size_S}"

            if new_signal_S and new_signal_S != state.get("last_short_signal"):
                gate = _check_signal_stability(state, "SHORT", ml_conf_S, now_ts)
                if gate != "SEND":
                    _save_alert_state(_alert_state)
                else:
                    size_label = "FULL SIZE 🔴🔴" if new_signal_S == "SHORT_FULL" else "HALF SIZE 🟠"
                    rr1        = lvl_S.get("rr1", 0)
                    rr_q       = "⭐⭐⭐" if rr1 >= 3 else ("⭐⭐" if rr1 >= 2 else "⭐")
                    macro_icon = "📈" if macro_trend == "UPTREND" else ("↔️" if macro_trend == "SIDEWAYS" else "📉")
                    wib = datetime.now(timezone(timedelta(hours=8))).strftime("%d %b %Y — %H:%M:%S WITA")
                    _send_telegram(
                        f"📉 <b>SINYAL SHORT — {symbol}</b>\n"
                        f"{'─'*28}\n"
                        f"⏳ <b>Waktu Sinyal:</b> {wib}\n"
                        f"💲 <b>Harga Entry:</b> <b>${close_price:.6f}</b>\n"
                        f"{'─'*28}\n"
                        f"🤖 ML: <b>{result['short'].get('ml_signal','?')}</b> | Conf: <b>{ml_conf_S*100:.1f}%</b> | Size: <b>{size_label}</b>\n"
                        f"📊 Proba — LONG: {result['short'].get('ml_proba',{}).get('LONG',0)*100:.1f}% | SHORT: {result['short'].get('ml_proba',{}).get('SHORT',0)*100:.1f}% | FLAT: {result['short'].get('ml_proba',{}).get('FLAT',0)*100:.1f}%\n"
                        f"{macro_icon} Tren Macro: <b>{macro_trend}</b> | Regime: {threshold_regime}\n"
                        f"🕐 Sesi: {variables.get('session', 'N/A')} (×{variables.get('SESSION_MULT',1.0):.2f})\n"
                        f"{'─'*28}\n"
                        f"🛡️ <b>Stop Loss</b>: ${lvl_S['sl_structure']:.6f} ({lvl_S['dist_sl']:+.2f}%) [{lvl_S['sl_label']}]\n\n"
                        f"🎯 <b>Take Profit:</b>\n"
                        f"  TP1: ${lvl_S['tp1']:.6f} ({lvl_S['dist_tp1']:+.2f}%) | R:R {lvl_S['rr1']}× [{lvl_S['tp1_label']}]\n"
                        f"  TP2: ${lvl_S['tp2']:.6f} ({lvl_S['dist_tp2']:+.2f}%) | R:R {lvl_S['rr2']}×\n"
                        f"  TP3: ${lvl_S['tp3']:.6f} ({lvl_S['dist_tp3']:+.2f}%) | R:R {lvl_S['rr3']}×\n\n"
                        f"R:R Quality: {rr_q}\n"
                        f"{'─'*28}\n"
                        f"⚠️ <i>Validasi harga sebelum entry! Sinyal ini valid di <b>${close_price:.6f}</b>. "
                        f"Jangan entry jika harga saat ini sudah jauh dari level tersebut.</i>"
                    )
                    state["last_short_signal"]     = new_signal_S
                    state["last_signal"]           = new_signal_S
                    state["last_signal_direction"] = "SHORT"
                    state["last_signal_ts"]        = time.time()
                    state["last_signal_conf"]      = ml_conf_S
                    _save_alert_state(_alert_state)
                    return

            # ── [CONFIDENCE HISTORY] Catat confidence score tiap siklus ──
            # Simpan hanya 12 jam (48 titik @ 15 menit). Auto-purge data lama.
            _HIST_TTL_SECS = 12 * 3600  # 12 jam
            _hist_all = _alert_state.setdefault("confidence_history", {})
            _hist_sym = _hist_all.setdefault(symbol, [])
            _hist_sym.append({
                "ts":         now_ts,
                "ml_signal":  variables.get("ml_signal",     "FLAT"),
                "ml_conf":    variables.get("ml_confidence", 0.0),
                "ml_size":    variables.get("ml_size",       "SKIP"),
                "close":      close_price,
            })
            # Purge data yang lebih lama dari 24 jam
            cutoff = now_ts - _HIST_TTL_SECS
            _hist_all[symbol] = [p for p in _hist_sym if p["ts"] >= cutoff]
            _save_alert_state(_alert_state)

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
    
    global _alert_state
    _alert_state = _load_alert_state()

    # Startup notification
    wib = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    _send_telegram(
        f"🤖 <b>Protocol 9.6 Signal Monitor AKTIF</b>\n"
        f"{'─'*28}\n"
        f"✅ Monitoring {len(MONITOR_PAIRS)} pair setiap 15 menit\n"
        f"Pairs: {', '.join(MONITOR_PAIRS)}\n\n"
        f"Notifikasi akan dikirim otomatis saat:\n"
        f"  🚀 Sinyal LONG (ML Size: FULL/HALF)\n"
        f"  📉 Sinyal SHORT (ML Size: FULL/HALF)\n"
        f"  ⚠️ EXIT ALERT\n\n"
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


def get_confidence_history(symbol: str, hours: int = 12) -> list:
    """
    Kembalikan history confidence score untuk symbol tertentu.
    Data sudah otomatis dipurge tiap siklus — hanya 12 jam terakhir.
    hours: window waktu yang diminta (default 12, max 12).
    """
    cutoff = time.time() - (min(hours, 12) * 3600)
    with _state_lock:
        hist_all = _alert_state.get("confidence_history", {})
        hist = hist_all.get(symbol.upper(), [])
        return [p for p in hist if p.get("ts", 0) >= cutoff]


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

    # [SSOT] Gunakan enrichment terpusat
    df, data_meta = enrichment.get_fully_enriched_data(symbol, interval="4h", limit=250)
    if df is None or len(df) < 22:
        return {"ok": False, "error": "Insufficient kline data", "symbol": symbol}

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
    thr_full         = variables.get("thr_full",   SIGNAL_THRESHOLD_FULL)
    thr_half         = variables.get("thr_half",   SIGNAL_THRESHOLD_HALF)
    thr_full_S       = variables.get("thr_full_S", SIGNAL_THRESHOLD_FULL)
    thr_half_S       = variables.get("thr_half_S", SIGNAL_THRESHOLD_HALF)
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
        f"  LONG : <b>{adj_L:.0f}/78 pts</b> ({result['long']['pct']:.1f}%) → <b>{code_L}</b>\n"  # [FIX v2.0]
        f"  SHORT: <b>{adj_S:.0f}/78 pts</b> ({result['short']['pct']:.1f}%) → <b>{code_S}</b>\n"  # [FIX v2.0]
        f"  Threshold LONG : FULL≥{thr_full} | HALF≥{thr_half}\n"
        f"  Threshold SHORT: FULL≥{thr_full_S} | HALF≥{thr_half_S} | Regime: {threshold_regime}\n"
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
