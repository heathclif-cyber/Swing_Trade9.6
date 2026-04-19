"""
Protocol 9.6: Structural Guardian — Dashboard UI Server
Flask backend serving all raw, computed, and state data for the web dashboard.
"""
import time
import logging
import json
import os
import requests as http_requests  # type: ignore
import pandas as pd  # type: ignore
import pandas_ta as ta  # type: ignore
from datetime import datetime, timezone, timedelta
import io
import concurrent.futures
from flask import Flask, render_template, jsonify, Response, request as flask_request  # type: ignore
from binance.client import Client  # type: ignore
from binance.exceptions import BinanceAPIException, BinanceRequestException  # type: ignore
import protocol_96_enrichment as enrichment  # type: ignore
import algo_scoring  # type: ignore
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from ml.ml_signal import MLSignalEngine as _MLSignalEngine
_ui_ml_engine = _MLSignalEngine()

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
import signal_monitor  # type: ignore
from requests.packages import urllib3  # type: ignore
# NOTE: data_engine.py telah dipensiun — semua fetch data melalui protocol_96_enrichment (SSOT)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# ⚙️ USER CONFIGURATION
# ==========================================
AVAILABLE_PAIRS = [
    'SOLUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT',
    'TONUSDT', 'ADAUSDT', 'TRXUSDT', 'SHIBUSDT', 'AVAXUSDT',
    'LINKUSDT', 'DOTUSDT', 'SUIUSDT', 'POLUSDT', 'NEARUSDT',
    'PEPEUSDT', 'TAOUSDT', 'APTOSUSDT', 'ARBUSDT', 'WLFIUSDT'
]
COIN_PAIR = AVAILABLE_PAIRS[0]
ALLOCATED_CAPITAL = 200

# File path untuk fallback lokal (DEVELOPMENT ONLY — tidak dipakai jika DATABASE_URL aktif)
_default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_entries.json')
TRADE_ENTRIES_FILE = os.environ.get('TRADE_DATA_PATH', _default_path)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ==========================================
# Flask App & Logger
# ==========================================
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Protocol_9.6_UI")

# ── Shared enrichment cache (TTL 5 menit) — scanner & detail pakai snapshot yang sama ──
_enrichment_cache: dict = {}   # {pair: {"df": df_quant, "meta": data_meta, "ts": float, "m15": df_m15}}
_CACHE_TTL = 300               # detik (5 menit)

def _get_enriched_data(pair: str, force_refresh: bool = False):
    """Return (df_quant, data_meta, df_m15) dari cache atau fetch baru.
    Menjamin scanner dan detail selalu pakai snapshot yang sama."""
    now = time.time()
    cached = _enrichment_cache.get(pair)
    if not force_refresh and cached and (now - cached["ts"]) < _CACHE_TTL:
        return cached["df"], cached["meta"], cached["m15"]
    # Fetch baru
    df_quant, data_meta = enrichment.get_fully_enriched_data(pair, interval="4h", limit=250)
    df_m15 = None
    try:
        _raw = get_klines_rest(pair, '15m', limit=300)
        df_m15 = _normalize_m15_columns(_raw)
    except Exception as _em:
        logger.warning(f'[Cache] Gagal fetch M15 untuk {pair}: {_em}')
    _enrichment_cache[pair] = {"df": df_quant, "meta": data_meta, "m15": df_m15, "ts": now}
    return df_quant, data_meta, df_m15

# ── Auto-start Signal Monitor on first request ──────────────
# Teknik ini lebih andal daripada Gunicorn post_fork hook karena
# berjalan di dalam worker process setelah fork selesai.
@app.before_request
def _auto_start_signal_monitor():
    """Pastikan Signal Monitor thread berjalan setelah worker Gunicorn siap."""
    if not signal_monitor._started_flag.is_set():
        logger.info("🚀 Starting Signal Monitor via before_request hook...")
        signal_monitor.start_background_monitor()


# Binance Client — resilient initialization (non-blocking)
binance_client = None
try:
    binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET,
                            requests_params={'verify': False, 'timeout': 5},
                            tld='com')
    logger.info("Binance client initialized successfully.")
except Exception as e:
    logger.warning(f"Binance Client init failed (will use REST fallback): {e}")
    binance_client = None


def send_telegram_message(text: str):
    """Kirim notifikasi Telegram menggunakan bot token & chat id dari env vars."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        http_requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")


# ── REST-based API endpoint list (ordered by ISP accessibility) ──
# fapi.binance.com is NOT blocked by Internet Positif (Indonesia ISP filter)
# api.binance.com IS typically blocked → put it last
BINANCE_KLINE_URLS = [
    "https://fapi.binance.com/fapi/v1/klines",
    "https://data-api.binance.vision/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api3.binance.com/api/v3/klines",
    "https://api4.binance.com/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
# Cache the last working URL for faster subsequent requests
_last_working_url: str | None = None


def get_klines_rest(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    """REST klines fetcher — tries multiple Binance endpoints for ISP resilience."""
    global _last_working_url
    # Try last working URL first for speed
    urls = list(BINANCE_KLINE_URLS)
    if _last_working_url and _last_working_url in urls:
        urls.remove(_last_working_url)
        urls.insert(0, _last_working_url)

    for url in urls:
        try:
            total_klines: list = []
            end_time = None
            ok = True
            while len(total_klines) < limit:
                req_limit = min(1000, limit - len(total_klines))
                params: dict = {"symbol": symbol, "interval": interval, "limit": req_limit}
                if end_time:
                    params['endTime'] = end_time
                resp = http_requests.get(url, params=params, timeout=8, verify=False)
                if resp.status_code == 200:
                    try:
                        chunk = resp.json()
                    except Exception:
                        ok = False; break
                    if not chunk or not isinstance(chunk, list):
                        break
                    total_klines = chunk + total_klines
                    if len(chunk) < req_limit:
                        break
                    end_time = chunk[0][0] - 1
                else:
                    ok = False; break

            if ok and total_klines:
                _last_working_url = url
                logger.info(f"  ✅ {symbol} {interval}: {len(total_klines)} candles via {url.split('/')[2]}")
                df = pd.DataFrame(total_klines, columns=[
                    'Open_Time', 'Open', 'High', 'Low', 'Close', 'Total_Volume',
                    'Close_Time', 'Quote_Asset_Volume', 'Trades', 'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
                ])
                df['Open_Time'] = pd.to_datetime(df['Open_Time'], unit='ms')
                df['Close_Time'] = pd.to_datetime(df['Close_Time'], unit='ms')
                for col in ['Open', 'High', 'Low', 'Close', 'Total_Volume', 'Taker_Buy_Base']:
                    df[col] = df[col].astype(float)
                df['Buy_Volume'] = df['Taker_Buy_Base']
                df['Sell_Volume'] = df['Total_Volume'] - df['Buy_Volume']
                df['Volume_Delta'] = df['Buy_Volume'] - df['Sell_Volume']
                return df
        except Exception as e:
            logger.debug(f"REST {url.split('/')[2]} failed: {e}")
            continue

    logger.warning(f"All REST endpoints failed for {symbol} {interval}")
    return pd.DataFrame()


# ==========================================
# 💾 TRADE ENTRIES — Hybrid Storage (PostgreSQL on Railway, JSON locally)
# ==========================================
# Format: { "SUIUSDT": { "entries": [{"price":1.05, "qty":100, "date":"..."}, ...], "sales":[...], "allocated_capital": 200 } }

DATABASE_URL = os.environ.get('DATABASE_URL')  # Set automatically by Railway PostgreSQL plugin

def _get_pg_conn():  # type: ignore
    """Buat koneksi PostgreSQL. Return None jika DATABASE_URL tidak ada."""
    if not DATABASE_URL:
        return None
    try:
        import psycopg2  # type: ignore
        from urllib.parse import urlparse
        # Railway kadang pakai postgres:// prefix, psycopg2 butuh postgresql://
        url_str = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        parsed = urlparse(url_str)
        return psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            dbname=parsed.path.lstrip('/'),
            sslmode='require'
        )
    except Exception as e:
        logger.error(f"[DB ERROR] PostgreSQL connection failed: {e}")
        return None

def _ensure_pg_table(conn) -> None:  # type: ignore
    """Buat tabel kv_store jika belum ada."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to create kv_store table: {e}")

def _migrate_format(data: dict) -> dict:  # type: ignore
    """Migrate format lama (single entry_price) ke multi-entry.
    Tambahkan field baru: position_side, market_type, leverage jika belum ada.
    """
    for sym, val in data.items():
        if isinstance(val, dict):
            # Defaults untuk field baru
            if 'position_side' not in val:
                val['position_side'] = 'LONG'       # LONG | SHORT
            if 'market_type' not in val:
                val['market_type'] = 'SPOT'          # SPOT | FUTURES
            if 'leverage' not in val:
                val['leverage'] = 1                  # 1x untuk SPOT, >1 untuk FUTURES
            if 'sales' not in val:
                val['sales'] = []
            if 'entry_price' in val and 'entries' not in val:
                old_price = float(val.get('entry_price', 0))
                old_cap   = float(val.get('allocated_capital', ALLOCATED_CAPITAL))
                old_date  = val.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                if old_price > 0:
                    val['entries'] = [{'price': old_price, 'qty': old_cap / old_price, 'date': old_date}]
                    val['allocated_capital'] = old_cap
                else:
                    val['entries'] = []
                    val['allocated_capital'] = old_cap
    return data

def load_trade_entries() -> dict:  # type: ignore
    """Load trade entries.

    STRICT MODE (DATABASE_URL aktif): baca HANYA dari PostgreSQL.
    Jika koneksi atau query gagal, log DATABASE ERROR dan return {}.
    JANGAN fallback ke file JSON agar kondisi database rusak langsung terdeteksi.

    DEVELOPMENT (tanpa DATABASE_URL): fallback ke file JSON lokal.
    """
    if DATABASE_URL:
        # ── STRICT: PostgreSQL only ──
        conn = _get_pg_conn()
        if conn is None:
            logger.error(
                "[DB ERROR] load_trade_entries: Tidak dapat terhubung ke PostgreSQL. "
                "Data dikembalikan kosong. Periksa DATABASE_URL / koneksi Supabase."
            )
            return {}
        try:
            _ensure_pg_table(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM kv_store WHERE key = 'trade_entries'")
                row = cur.fetchone()
            conn.close()
            if row:
                return _migrate_format(json.loads(row[0]))
            return {}
        except Exception as e:
            logger.error(
                f"[DB ERROR] load_trade_entries: Query PostgreSQL gagal — {e}. "
                "Data dikembalikan kosong."
            )
            try: conn.close()
            except: pass
            return {}
    # ── DEVELOPMENT: fallback ke file JSON lokal ──
    if os.path.exists(TRADE_ENTRIES_FILE):
        try:
            with open(TRADE_ENTRIES_FILE, 'r') as f:
                return _migrate_format(json.load(f))
        except Exception as e:
            logger.warning(f"Failed to load trade entries from file: {e}")
    return {}

def save_trade_entries(entries: dict) -> bool:  # type: ignore
    """Simpan trade entries.

    STRICT MODE (DATABASE_URL aktif): tulis HANYA ke PostgreSQL.
    - Jika data kosong {}, tolak penyimpanan (DATA GUARD) dan return False.
    - Jika koneksi gagal, log DATABASE ERROR dan raise RuntimeError
      sehingga caller (endpoint Flask) mengembalikan HTTP 503 ke UI.
    JANGAN fallback ke JSON agar data mismatch tidak terjadi secara diam-diam.

    DEVELOPMENT (tanpa DATABASE_URL): fallback ke file JSON lokal.
    Returns True jika berhasil, False jika ditolak DATA GUARD.
    """
    # ── DATA GUARD: blokir save dict kosong ke PostgreSQL ──
    if DATABASE_URL and not entries:
        logger.critical(
            "[DATA GUARD] DITOLAK: Mencoba save data kosong {} ke PostgreSQL. "
            "Save diabaikan untuk mencegah data loss."
        )
        return False

    if DATABASE_URL:
        # ── STRICT: PostgreSQL only ──
        conn = _get_pg_conn()
        if conn is None:
            msg = (
                "[DB ERROR] save_trade_entries: Tidak dapat terhubung ke PostgreSQL. "
                "Data TIDAK disimpan. Periksa DATABASE_URL / koneksi Supabase."
            )
            logger.error(msg)
            raise RuntimeError(msg)
        try:
            _ensure_pg_table(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kv_store (key, value, updated_at)
                    VALUES ('trade_entries', %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """, (json.dumps(entries),))
            conn.commit()
            conn.close()
            logger.info("💾 Trade entries saved to PostgreSQL")
            return True
        except RuntimeError:
            raise
        except Exception as e:
            msg = f"[DB ERROR] save_trade_entries: PostgreSQL write gagal — {e}. Data TIDAK disimpan."
            logger.error(msg)
            try: conn.close()
            except: pass
            raise RuntimeError(msg)

    # ── DEVELOPMENT: fallback ke file JSON lokal ──
    try:
        with open(TRADE_ENTRIES_FILE, 'w') as f:
            json.dump(entries, f, indent=2)
        logger.info(f"💾 Trade entries saved to {TRADE_ENTRIES_FILE}")
        return True
    except Exception as e:
        logger.error(f"Failed to save trade entries to file: {e}")
        return False


def get_entry_summary(symbol: str) -> dict:  # type: ignore
    """Return entry & sales summary dengan dukungan penuh LONG/SHORT, Spot/Futures, Leverage.
    
    - position_side: 'LONG' | 'SHORT' — membalik arah PnL untuk SHORT.
    - market_type:   'SPOT' | 'FUTURES'
    - leverage:      int, default 1 (SPOT). Digunakan untuk hitung leveraged PnL.
    """
    data = load_trade_entries()
    coin_data = data.get(symbol, {})
    entry_list    = coin_data.get('entries', [])
    sales_list    = coin_data.get('sales', [])
    position_side = coin_data.get('position_side', 'LONG')
    market_type   = coin_data.get('market_type', 'SPOT')
    leverage      = int(coin_data.get('leverage', 1))

    total_entry_cost = sum(e['price'] * e['qty'] for e in entry_list)
    total_entry_qty  = sum(e['qty'] for e in entry_list)
    avg_entry_price  = total_entry_cost / total_entry_qty if total_entry_qty > 0 else 0.0

    total_sold_qty     = sum(s['qty'] for s in sales_list)
    total_sold_revenue = sum(s['price'] * s['qty'] for s in sales_list)

    # Realized PnL: chronological rolling-average, direction-aware
    events = []
    for e in entry_list:
        events.append(('open', e.get('date', ''), e.get('price', 0), e.get('qty', 0)))
    for s in sales_list:
        events.append(('close', s.get('date', ''), s.get('price', 0), s.get('qty', 0)))
    events.sort(key=lambda x: x[1])

    current_qty = 0.0
    current_cost = 0.0
    realized_pnl = 0.0
    for type_, date_, price, qty in events:
        if type_ == 'open':
            current_cost += price * qty
            current_qty  += qty
        elif type_ == 'close' and current_qty > 0:
            rolling_avg   = current_cost / current_qty
            raw_pnl_per_unit = (rolling_avg - price) if position_side == 'SHORT' else (price - rolling_avg)
            realized_pnl  += raw_pnl_per_unit * qty * leverage
            current_cost  -= rolling_avg * qty
            current_qty   -= qty
            current_qty    = max(0.0, current_qty)
            current_cost   = max(0.0, current_cost)

    rolling_avg_cost = current_cost / current_qty if current_qty > 0 else avg_entry_price
    remaining_qty    = max(0.0, current_qty)
    remaining_cost   = max(0.0, current_cost)

    return {
        'entries': entry_list, 'sales': sales_list,
        'position_side': position_side,
        'market_type':   market_type,
        'leverage':      leverage,
        'avg_price':          round(avg_entry_price, 8),
        'rolling_avg_cost':   round(rolling_avg_cost, 8),
        'total_qty':          round(total_entry_qty, 6),
        'remaining_qty':      round(remaining_qty, 6),
        'remaining_cost':     round(remaining_cost, 4),
        'total_cost':         round(total_entry_cost, 4),
        'num_entries':        len(entry_list),
        'num_sales':          len(sales_list),
        'total_sold_qty':     round(total_sold_qty, 6),
        'total_sold_revenue': round(total_sold_revenue, 4),
        'realized_pnl':       round(realized_pnl, 4),
    }


def get_entry_price(symbol: str) -> float:  # type: ignore
    """Dapatkan rolling avg cost basis (accounting for sales). Return 0.0 jika belum di-set."""
    summary = get_entry_summary(symbol)
    # If there are sales, use remaining cost / remaining qty as the effective entry price
    if summary['remaining_qty'] > 0:
        remaining_cost = summary['total_cost'] - (summary['avg_price'] * summary['total_sold_qty'])
        # More accurate: use the rolling cost tracked in get_entry_summary
        return summary['rolling_avg_cost']
    elif summary['num_entries'] > 0 and summary['remaining_qty'] <= 0:
        # All sold — no active position
        return 0.0
    return summary['avg_price']


def get_allocated_capital(symbol: str) -> float:  # type: ignore
    """Dapatkan allocated capital untuk koin tertentu."""
    entries = load_trade_entries()
    coin_data = entries.get(symbol, {})
    return float(coin_data.get('allocated_capital', ALLOCATED_CAPITAL))

# ==========================================
# Bot State (in-memory)
# ==========================================
class BotState:
    # Per-coin state stored as dict {coin_pair: {...}}
    _states: dict = {}
    
    @classmethod
    def get(cls, coin_pair: str) -> dict:
        if coin_pair not in cls._states:
            cls._states[coin_pair] = {
                "active_sl": 0.0,
                "initial_sl": 0.0,
                "status": "ACTIVE",
                "alerts_sent": {
                    "TP_1": False,
                    "SL_BE": False,
                    "VOL_FAKEOUT": False,
                    "KILL_SWITCH": False,
                },
            }
        return cls._states[coin_pair]

# ==========================================
# Interval Map
# ==========================================
INTERVAL_MAP = {
    "15m": Client.KLINE_INTERVAL_15MINUTE,
    "1h":  Client.KLINE_INTERVAL_1HOUR,
    "4h":  Client.KLINE_INTERVAL_4HOUR,
    "1d":  Client.KLINE_INTERVAL_1DAY,
    "1w":  Client.KLINE_INTERVAL_1WEEK,
}


# ==========================================
# 🛠️ DATA FETCHING HELPERS
# ==========================================
def get_klines_df(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """Fetch OHLCV data from Binance and process institutional volume structure.
    Uses python-binance Client first, falls back to direct REST API if unavailable."""
    # Try python-binance client first
    if binance_client:
        try:
            total_klines = []
            end_time = None
            while len(total_klines) < limit:
                req_limit = min(1000, limit - len(total_klines))
                params = {'symbol': symbol, 'interval': interval, 'limit': req_limit}
                if end_time:
                    params['endTime'] = end_time
                chunk = binance_client.get_klines(**params)
                if not chunk:
                    break
                total_klines = chunk + total_klines
                if len(chunk) < req_limit:
                    break
                end_time = chunk[0][0] - 1
            
            df = pd.DataFrame(total_klines, columns=[
                'Open_Time', 'Open', 'High', 'Low', 'Close', 'Total_Volume',
                'Close_Time', 'Quote_Asset_Volume', 'Trades', 'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
            ])

            df['Open_Time'] = pd.to_datetime(df['Open_Time'], unit='ms')
            df['Close_Time'] = pd.to_datetime(df['Close_Time'], unit='ms')
            for col in ['Open', 'High', 'Low', 'Close', 'Total_Volume', 'Taker_Buy_Base']:
                df[col] = df[col].astype(float)

            df['Buy_Volume'] = df['Taker_Buy_Base']
            df['Sell_Volume'] = df['Total_Volume'] - df['Buy_Volume']
            df['Volume_Delta'] = df['Buy_Volume'] - df['Sell_Volume']

            return df
        except Exception as e:
            logger.warning(f"Client klines failed for {symbol} {interval}: {e}, trying REST fallback...")

    # Fallback: use direct REST API
    logger.info(f"  Using REST fallback for {symbol} {interval}...")
    return get_klines_rest(symbol, interval, limit)

# ── Dead code removed ────────────────────────────────────────────────────────
# get_klines_fapi, apply_full_indicators, fetch_oi_data, fetch_funding_rate,
# fetch_liquidation_data, apply_cvd — semua telah dihapus.
# Semua fetch data kini dilakukan oleh protocol_96_enrichment (SSOT).
# ─────────────────────────────────────────────────────────────────────────────


def format_ohlcv_for_json(df: pd.DataFrame, last_n: int = 10) -> list:
    """Convert the last N rows of a DataFrame to JSON-safe dicts for the UI."""
    if df.empty:
        return []
    subset = df.tail(last_n).copy()
    result = []
    for _, row in subset.iterrows():
        # Apply UTC+8 Offset for display
        local_time = row['Open_Time'] + timedelta(hours=8) if pd.notna(row['Open_Time']) else None
        
        entry = {
            "time": local_time.strftime('%Y-%m-%d %H:%M') if local_time else "",
            "open": round(float(row['Open']), 6),  # type: ignore[call-overload]
            "high": round(float(row['High']), 6),  # type: ignore[call-overload]
            "low": round(float(row['Low']), 6),  # type: ignore[call-overload]
            "close": round(float(row['Close']), 6),  # type: ignore[call-overload]
            "total_vol": round(float(row['Total_Volume']), 2),  # type: ignore[call-overload]
            "buy_vol": round(float(row['Buy_Volume']), 2),  # type: ignore[call-overload]
            "sell_vol": round(float(row['Sell_Volume']), 2),  # type: ignore[call-overload]
            "vol_delta": round(float(row['Volume_Delta']), 2),  # type: ignore[call-overload]
        }
        # Add indicators if present
        for col in ['EMA_7', 'EMA_21', 'EMA_50', 'EMA_200', 'RSI_6', 'StochRSI_K', 'StochRSI_D', 'ATR_14']:
            if col in row.index and pd.notna(row[col]):
                entry[col.lower()] = round(float(row[col]), 6)  # type: ignore[call-overload]
        result.append(entry)
    return result


# ==========================================
# 📡 API ENDPOINTS
# ==========================================
@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/test-signal")
def api_test_signal():
    """Trigger manual test: evaluasi 1 pair dan kirim hasil ke Telegram."""
    pair = flask_request.args.get("pair", "SUIUSDT").upper()
    try:
        trade_entries = load_trade_entries()
        signal_monitor._evaluate_pair(pair, trade_entries)

        # Ambil data via SSOT — tidak lagi pakai private function signal_monitor
        df, data_meta = enrichment.get_fully_enriched_data(pair, interval="4h", limit=250)
        if len(df) >= 22:
            coin_data  = trade_entries.get(pair, {})
            entry_list = coin_data.get("entries", [])
            total_cost = sum(e["price"] * e["qty"] for e in entry_list)
            total_qty  = sum(e["qty"] for e in entry_list)
            avg_entry  = (total_cost / total_qty) if total_qty > 0 else None

            df_m15_raw = enrichment.get_klines_rest(pair, '15m', limit=300) if hasattr(enrichment, 'get_klines_rest') else None
            if df_m15_raw is None:
                from data_engine import DataEngine as _DE
                _de = _DE()
                df_m15_raw = _de.get_klines_rest(pair, '15m', limit=300)
            df_m15_norm = _normalize_m15_columns(df_m15_raw)
            ml_result = _ui_ml_engine.predict(symbol=pair, df_m15=df_m15_norm)

            msg = (
                f"🧪 <b>TEST SIGNAL — {pair}</b>\n"
                f"{'─'*28}\n"
                f"🤖 ML Signal: <b>{ml_result['signal']}</b>\n"
                f"📊 Confidence: <b>{ml_result['confidence']*100:.1f}%</b>\n"
                f"📦 Size: <b>{ml_result['size']}</b>\n"
                f"📈 Proba LONG: {ml_result['proba'].get('LONG',0)*100:.1f}% | "
                f"SHORT: {ml_result['proba'].get('SHORT',0)*100:.1f}% | "
                f"FLAT: {ml_result['proba'].get('FLAT',0)*100:.1f}%\n"
                f"🔧 Model: {ml_result['model_type']}"
            )
            if data_meta.get("data_incomplete"):
                msg += f"\n⚠️ Data tidak lengkap: {', '.join(data_meta.get('missing_data', []))}"
            signal_monitor._send_telegram(msg)
            return jsonify({
                "ok": True,
                "symbol": pair,
                "ml_signal": ml_result['signal'],
                "ml_confidence": ml_result['confidence'],
                "ml_size": ml_result['size'],
                "ml_proba": ml_result['proba'],
                "model_type": ml_result['model_type'],
            })

        return jsonify({"ok": False, "error": "Insufficient data", "pair": pair}), 400

    except Exception as e:
        logger.exception(f"test-signal error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ping-telegram")
def api_ping_telegram():
    """Debug: baca env var real-time dan langsung kirim ke Telegram, tampilkan response API."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")

    # Mask token untuk security
    token_masked = f"{token[:10]}...{token[-5:]}" if len(token) > 15 else f"[len={len(token)}]"

    if not token or not chat:
        return jsonify({
            "ok": False,
            "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set",
            "token_masked": token_masked,
            "chat_id": chat or "(empty)"
        }), 400

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": (
            "✅ <b>Protocol 9.6 — PING TEST</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🟢 Koneksi Railway → Telegram: OK!\n"
            "🟢 Signal Monitor aktif di Railway\n"
            "🟢 Database Supabase terhubung\n\n"
            "⏱ Update setiap 15 menit otomatis.\n"
            "Notifikasi akan muncul saat ada\n"
            "sinyal LONG/SHORT/EXIT/KILL SWITCH."
        ),
        "parse_mode": "HTML"
    }
    try:
        resp = http_requests.post(url, json=payload, timeout=15)
        tg_json = resp.json()
        return jsonify({
            "ok": True,
            "token_masked": token_masked,
            "chat_id": chat,
            "telegram_status": resp.status_code,
            "telegram_ok": tg_json.get("ok"),
            "telegram_message_id": tg_json.get("result", {}).get("message_id"),
            "telegram_error": tg_json.get("description") if not tg_json.get("ok") else None,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "token_masked": token_masked}), 500


@app.route("/api/test-pendle")
def api_test_pendle():
    """Trigger manual test khusus PENDLE untuk validasi 7 Prioritas (termasuk P7 adaptive dan P6)."""
    try:
        res = signal_monitor.test_send_pendle_notification()
        return jsonify(res), 200 if res.get("ok") else 400
    except Exception as e:
        logger.exception(f"test-pendle error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# fetch_live_macro_and_liq() — DIHAPUS.
# Fungsi ini tidak lagi dipanggil. Semua data Macro + Liquiditas
# diambil oleh enrichment.get_fully_enriched_data() (SSOT).
def _removed_placeholder():
    if df.empty:
        return df
    df = df.copy()

    # ── 1. BTC Dominance & Altcoin Index via CMC API ──────────────────────
    try:
        CMC_API_KEY = os.environ.get("CMC_API_KEY", "aa8eb4dd82974c308c5428e7c1be0121")
        cmc_url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
        cmc_headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
        r = http_requests.get(cmc_url, headers=cmc_headers, timeout=8, verify=False)
        if r.status_code == 200:
            d = r.json()["data"]
            btc_dom_raw   = round(d["btc_dominance"] * 100, 1)
            total_mcap    = d["quote"]["USD"]["total_market_cap"]
            btc_dom_frac  = d["btc_dominance"] / 100
            altcoin_index = round(total_mcap * (1 - btc_dom_frac) / 1_000_000_000, 1)
            df["BTC_Dominance"] = btc_dom_raw
            df["Altcoin_Index"]  = altcoin_index
            logger.info(f"  [Macro] CMC OK — BTC_Dom={btc_dom_raw}%, AltIdx={altcoin_index}B")
        else:
            df.setdefault("BTC_Dominance", None)
            df.setdefault("Altcoin_Index", None)
    except Exception as e:
        logger.warning(f"  [Macro] CMC fetch failed: {e}")
        df["BTC_Dominance"] = None
        df["Altcoin_Index"]  = None

    # ── 2. Binance Futures Orderbook — Buy Wall & Sell Wall ──────────────
    try:
        liq_url    = "https://fapi.binance.com/fapi/v1/depth"
        liq_params = {"symbol": symbol.upper(), "limit": 500}
        r_liq = http_requests.get(liq_url, params=liq_params, timeout=8, verify=False)
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
                df.setdefault("Buy_Liq", 0.0)
                df.setdefault("Sell_Liq", 0.0)
        else:
            df.setdefault("Buy_Liq", 0.0)
            df.setdefault("Sell_Liq", 0.0)
    except Exception as e:
        logger.warning(f"  [Macro] Orderbook fetch failed: {e}")
        df.setdefault("Buy_Liq", 0.0)
        df.setdefault("Sell_Liq", 0.0)

    return df


@app.route("/api/data")
def api_data():
    """Master endpoint: returns ALL data categories for the dashboard."""
    try:
        coin_pair = flask_request.args.get('pair', AVAILABLE_PAIRS[0]).upper()
        if coin_pair not in AVAILABLE_PAIRS:
            coin_pair = AVAILABLE_PAIRS[0]

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"📡 Fetching dashboard data for {coin_pair}...")

        # ── ⚡ Data Fetching — Pakai enrichment SSOT untuk 4h, get_klines_df untuk display ──
        df_cache: dict[str, pd.DataFrame] = {}
        btc_cache: dict[str, pd.DataFrame] = {}

        DISPLAY_INTERVALS = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}
        for label in DISPLAY_INTERVALS:
            req_limit = 250 if label in ['1h', '4h'] else 20
            interval_str = label  # string interval untuk get_klines_df
            logger.info(f"  Fetching {coin_pair} {label} ({req_limit} candles)...")
            df_raw = get_klines_df(coin_pair, interval_str, limit=req_limit)
            # Apply base indicators (EMA/RSI) hanya untuk panel display 1h/4h
            if label in ['1h', '4h'] and not df_raw.empty:
                df_raw['EMA_7']   = ta.ema(df_raw['Close'], length=7)
                df_raw['EMA_21']  = ta.ema(df_raw['Close'], length=21)
                df_raw['EMA_50']  = ta.ema(df_raw['Close'], length=50)
                df_raw['EMA_200'] = ta.ema(df_raw['Close'], length=200)
                df_raw['RSI_6']   = ta.rsi(df_raw['Close'], length=6)
                try:
                    stoch = ta.stochrsi(df_raw['Close'], length=14, rsi_length=14, k=3, d=3)
                    if stoch is not None and not stoch.empty:
                        df_raw['StochRSI_K'] = stoch.iloc[:, 0]
                        df_raw['StochRSI_D'] = stoch.iloc[:, 1]
                except Exception:
                    pass
            df_cache[label] = df_raw

            logger.info(f"  Fetching BTCUSDT {label} (20 candles)...")
            btc_df = get_klines_df("BTCUSDT", interval_str, limit=20)
            btc_cache[label] = btc_df

        # ── Section 1: Raw API Data ──
        raw_data = {}
        for label in INTERVAL_MAP.keys():
            raw_data[label] = {
                "coin": format_ohlcv_for_json(df_cache[label], last_n=10),
                "btc": format_ohlcv_for_json(btc_cache[label], last_n=10),
            }

        # ── Section 2: Computed Data ──
        computed = {
            "indicators_1h": format_ohlcv_for_json(df_cache['1h'], last_n=10),
            "indicators_4h": format_ohlcv_for_json(df_cache['4h'], last_n=10),
        }

        # Liquidity Borders
        df_1d = df_cache['1d']
        df_1w = df_cache['1w']
        liquidity: dict = {"PDH": 0.0, "PDL": 0.0, "PWH": 0.0, "PWL": 0.0}
        if len(df_1d) >= 2:
            liquidity["PDH"] = round(float(df_1d.iloc[-2]['High']), 6)  # type: ignore[call-overload]
            liquidity["PDL"] = round(float(df_1d.iloc[-2]['Low']), 6)  # type: ignore[call-overload]
        if len(df_1w) >= 2:
            liquidity["PWH"] = round(float(df_1w.iloc[-2]['High']), 6)  # type: ignore[call-overload]
            liquidity["PWL"] = round(float(df_1w.iloc[-2]['Low']), 6)  # type: ignore[call-overload]

        computed["liquidity_borders"] = liquidity  # type: ignore[assignment]

        # SMT Divergence
        df_btc_h4 = btc_cache['4h']
        df_tgt_h4 = df_cache['4h']
        smt: dict = {"btc_trend_12h": "N/A", "coin_trend_12h": "N/A", "bearish_smt": False}
        if not df_btc_h4.empty and not df_tgt_h4.empty and len(df_btc_h4) >= 3 and len(df_tgt_h4) >= 3:
            btc_highs = df_btc_h4['High'].iloc[-3:].values
            tgt_highs = df_tgt_h4['High'].iloc[-3:].values
            btc_hh = btc_highs[2] > btc_highs[1] > btc_highs[0]
            tgt_lh = tgt_highs[2] < tgt_highs[1]
            smt["btc_trend_12h"] = "Higher High ↑" if btc_hh else "No HH"
            smt["coin_trend_12h"] = "Lower High ↓" if tgt_lh else "No LH"
            smt["bearish_smt"] = bool(btc_hh and tgt_lh)
        computed["smt_divergence"] = smt  # type: ignore[assignment]

        # OI Delta (DEAD CODE REMOVED for Spot optimization)
        computed["oi_delta_pct"] = 0.0
        oi_formatted = []

        # ── Battle Plan & BotState: diisi dari quant_results setelah scoring selesai ──
        df_1h = df_cache.get('1h', pd.DataFrame())
        df_4h = df_cache.get('4h', pd.DataFrame())
        computed["battle_plan"] = {}  # Inisialisasi aman, akan diisi SSOT di bawah

        coin_state = BotState.get(coin_pair)

        # ── Current price dari candle 1h terakhir ──
        current_price = 0.0
        if not df_1h.empty:
            current_price = float(df_1h.iloc[-1]['Close'])

        # ── Lookup dynamic entry price for this coin ──
        entry_summary = get_entry_summary(coin_pair)
        entry_price = entry_summary['rolling_avg_cost']  # Uses rolling cost basis after sales
        allocated_capital = get_allocated_capital(coin_pair)
        remaining_qty = entry_summary['remaining_qty']
        remaining_cost = entry_summary['remaining_cost']
        realized_pnl = entry_summary['realized_pnl']
        total_sold_qty = entry_summary['total_sold_qty']
        total_sold_revenue = entry_summary['total_sold_revenue']

        # PnL calculation — direction-aware (LONG/SHORT) + leverage
        position_side = entry_summary.get('position_side', 'LONG')
        market_type   = entry_summary.get('market_type', 'SPOT')
        leverage      = entry_summary.get('leverage', 1)
        pnl_pct = 0.0
        floating_pnl_usd = 0.0
        if entry_price > 0 and remaining_qty > 0:
            if position_side == 'SHORT':
                pnl_pct = round(((entry_price - current_price) / entry_price) * 100 * leverage, 4)  # type: ignore[call-overload]
                floating_pnl_usd = round((entry_price - current_price) * remaining_qty * leverage, 4)
            else:  # LONG / SPOT
                pnl_pct = round(((current_price - entry_price) / entry_price) * 100 * leverage, 4)  # type: ignore[call-overload]
                floating_pnl_usd = round((current_price - entry_price) * remaining_qty * leverage, 4)

        state = {
            "user_input": {
                "coin_pair": coin_pair,
                "available_pairs": AVAILABLE_PAIRS,
                "entry_price": entry_price,
                "allocated_capital": allocated_capital,
                "status": coin_state["status"],
            },
            "active_tracker": {
                "current_price": round(current_price, 8),
                "initial_sl": round(coin_state["initial_sl"], 8),
                "active_sl": round(coin_state["active_sl"], 8),
                "current_pnl_pct": pnl_pct,
                "floating_pnl_usd": floating_pnl_usd,
            },
            "position": {
                "remaining_qty":     remaining_qty,
                "remaining_cost":    remaining_cost,
                "realized_pnl":      realized_pnl,
                "total_sold_qty":    total_sold_qty,
                "total_sold_revenue":total_sold_revenue,
                "avg_entry_price":   entry_summary['avg_price'],
                "rolling_avg_cost":  entry_summary['rolling_avg_cost'],
                "num_entries":       entry_summary['num_entries'],
                "num_sales":         entry_summary['num_sales'],
                "is_closed":         remaining_qty <= 0 and entry_summary['num_entries'] > 0,
                "side":              position_side,
                "market_type":       market_type,
                "leverage":          leverage,
            },
            "alerts_sent": coin_state["alerts_sent"],
        }

        # ── [APEX] MODULE 5: 71-Point Quantitative Analyst ──
        # [REFACTOR — SSOT] Gunakan protocol_96_enrichment.get_fully_enriched_data()
        # sebagai satu-satunya sumber data. Tidak ada lagi perakitan data manual.
        logger.info("  [APEX] Executing 71-Point Quantitative Algorithm (SSOT)...")
        _data_warning: dict = {}
        try:
            df_quant, data_meta, _df_m15_quant = _get_enriched_data(coin_pair)

            # Ekspos data warning ke response JSON agar UI bisa menampilkannya
            if data_meta.get("data_incomplete"):
                _data_warning = {
                    "incomplete": True,
                    "missing":    data_meta.get("missing_data", []),
                    "message":    f"Data tidak lengkap: {', '.join(data_meta.get('missing_data', []))}. Skor mungkin kurang akurat.",
                }
                logger.warning(f"  [APEX] Data incomplete for {coin_pair}: {data_meta.get('missing_data')}")

            if df_quant is not None and not df_quant.empty and len(df_quant) >= 22:
                meta = {
                    'Symbol': coin_pair,
                    'AVG_ENTRY_PRICE': entry_summary.get('rolling_avg_cost') if entry_summary.get('remaining_qty', 0) > 0 else None,
                    'ENTRY_DATE': None,
                }
                quant_results = algo_scoring.calculate_71point_score(
                    df_quant, meta, df_m15=_df_m15_quant, ml_engine=_ui_ml_engine
                )



                # ── Build live market context from enriched 4H data ──
                if quant_results:
                    last_q = df_quant.iloc[-1]
                    live_ctx = {}
                    ctx_map = {
                        'StochRSI_K': 'StochRSI_K', 'StochRSI_D': 'StochRSI_D',
                        'Funding_Rate': 'Funding_Rate', 'Open_Interest': 'Open_Interest',
                        'BTC_Dominance': 'BTC_Dominance', 'Altcoin_Index': 'Altcoin_Index',
                        'Buy_Liq': 'Buy_Liq', 'Sell_Liq': 'Sell_Liq',
                    }
                    for col, key in ctx_map.items():
                        if col in df_quant.columns:
                            v = last_q.get(col)
                            try:
                                if pd.notna(v): live_ctx[key] = round(float(v), 6)
                            except Exception:
                                pass
                    # Add liquidity borders dari D1/W1
                    for lk in ['PDH', 'PDL', 'PWH', 'PWL']:
                        lv = liquidity.get(lk, 0)
                        if lv: live_ctx[lk] = round(lv, 6)
                    quant_results['market_context'] = live_ctx

                    # ── Battle Plan dari quant_results (SSOT — sinkron dengan Telegram) ──
                    lvl_L = quant_results["long"]["levels"]
                    lvl_S = quant_results["short"]["levels"]
                    em    = quant_results.get("emergency", {})
                    sl_val = lvl_L.get("sl_structure") or 0.0
                    computed["battle_plan"] = {
                        # LONG levels (dari algo_scoring — sama persis dengan notif Telegram)
                        "structural_sl":       sl_val,
                        "tp1_val":             lvl_L.get("tp1"),
                        "tp1_label":           lvl_L.get("tp1_label", "TP1"),
                        "tp2_val":             lvl_L.get("tp2"),
                        "tp2_label":           lvl_L.get("tp2_label", "TP2"),
                        "tp3_val":             lvl_L.get("tp3"),
                        "tp3_label":           lvl_L.get("tp3_label", "TP3"),
                        "dist_sl":             lvl_L.get("dist_sl"),
                        "dist_tp1":            lvl_L.get("dist_tp1"),
                        "dist_tp2":            lvl_L.get("dist_tp2"),
                        "dist_tp3":            lvl_L.get("dist_tp3"),
                        "rr1":                 lvl_L.get("rr1"),
                        "rr2":                 lvl_L.get("rr2"),
                        "rr3":                 lvl_L.get("rr3"),
                        # SHORT levels
                        "structural_sl_short": lvl_S.get("sl_structure"),
                        "tp1_val_short":        lvl_S.get("tp1"),
                        "tp2_val_short":        lvl_S.get("tp2"),
                        "tp3_val_short":        lvl_S.get("tp3"),
                        "kill_switch_active":  em.get("sl_touched", False),
                        "ml_signal":      quant_results['long'].get('ml_signal', 'FLAT'),
                        "ml_confidence":  quant_results['long'].get('ml_confidence', 0.0),
                        "ml_size":        quant_results['long'].get('ml_size', 'SKIP'),
                        "ml_proba":       quant_results['long'].get('ml_proba', {}),
                        "ml_signal_s":    quant_results['short'].get('ml_signal', 'FLAT'),
                        "ml_confidence_s":quant_results['short'].get('ml_confidence', 0.0),
                        "ml_size_s":      quant_results['short'].get('ml_size', 'SKIP'),
                        "ml_narrative":   quant_results['long'].get('narrative', ''),
                        # Untuk kompatibilitas UI lama yang mungkin baca long_score:
                        "long_score":     round(quant_results['long'].get('ml_confidence', 0.0) * 100, 1),
                        "short_score":    round(quant_results['short'].get('ml_confidence', 0.0) * 100, 1),
                        "source": "algo_scoring_ssot",
                    }

                    # ── Update BotState tracking SL (Trailing — hanya naik) ──
                    if sl_val > 0:
                        if sl_val > coin_state["active_sl"] or coin_state["active_sl"] == 0:
                            coin_state["active_sl"] = sl_val
                        if coin_state["initial_sl"] == 0:
                            coin_state["initial_sl"] = coin_state["active_sl"]

                    # ── Update Kill Switch status dari algo_scoring ──
                    if em.get("sl_touched", False):
                        coin_state["status"] = "KILL_SWITCH"
                        coin_state["alerts_sent"]["KILL_SWITCH"] = True
                    elif coin_state["status"] == "KILL_SWITCH" and not em.get("sl_touched", False):
                        coin_state["status"] = "ACTIVE"

                    # Notifikasi dikelola sepenuhnya oleh signal_monitor.py (background worker)
                    # Tidak ada Telegram alert dari sini untuk mencegah spam ganda.

                state["quant_analysis"] = quant_results
            else:
                state["quant_analysis"] = None
        except Exception as e:
            logger.error(f"Quant Analysis Error: {e}")
            import traceback; traceback.print_exc()
            state["quant_analysis"] = None

        logger.info("✅ Dashboard data ready!")
        return jsonify({
            "success":      True,
            "timestamp":    now_str,
            "raw_data":     raw_data,
            "oi_data":      oi_formatted,
            "computed":     computed,
            "state":        state,
            "data_warning": _data_warning,   # UI bisa tampilkan banner peringatan
        })

    except (BinanceAPIException, BinanceRequestException) as bae:
        logger.error(f"Binance API Error: {bae}")
        return jsonify({"success": False, "error": f"Binance API Error: {bae}"}), 500
    except Exception as e:
        logger.error(f"Dashboard data error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ==========================================
# 💰 TRADE ENTRY MANAGEMENT ENDPOINTS
# ==========================================
@app.route("/api/trade-entries", methods=["GET"])
def api_get_trade_entries():
    """Ambil semua entry prices & capital per koin (multi-entry format)."""
    try:
        entries = load_trade_entries()
        # Build summaries for each coin
        summaries = {}
        for sym in entries:
            summaries[sym] = get_entry_summary(sym)
            summaries[sym]['allocated_capital'] = entries[sym].get('allocated_capital', ALLOCATED_CAPITAL)
        return jsonify({"success": True, "entries": entries, "summaries": summaries})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/trade-entries", methods=["POST"])
def set_trade_entry():
    """
    TAMBAH satu entry baru untuk koin (DCA / Scaling-In).
    Body JSON: { "symbol": "SUIUSDT", "entry_price": 1.055, "qty": 190.47,
                 "allocated_capital": 200, "side": "LONG",
                 "market_type": "SPOT", "leverage": 1 }
    - side:        LONG | SHORT  (default: LONG)
    - market_type: SPOT | FUTURES (default: SPOT)
    - leverage:    integer >= 1  (default: 1, wajib > 1 untuk FUTURES)
    - qty optional: dihitung dari allocated_capital / entry_price jika tidak dikirim.
    - DCA diperbolehkan jika arah (side) sama dengan posisi yang sudah ada.
    """
    try:
        data = flask_request.get_json()  # type: ignore
        if not data:
            return jsonify({"success": False, "error": "No JSON body provided"}), 400

        symbol = data.get("symbol", "").upper()
        if not symbol or symbol not in AVAILABLE_PAIRS:
            return jsonify({"success": False, "error": f"Invalid symbol: {symbol}"}), 400

        entry_price = float(data.get("entry_price", 0))
        if entry_price <= 0:
            return jsonify({"success": False, "error": "Entry price must be > 0"}), 400

        # ── Validasi parameter baru ──
        side = data.get("side", "LONG").upper()
        if side not in ("LONG", "SHORT"):
            return jsonify({"success": False, "error": "side harus LONG atau SHORT"}), 400

        market_type = data.get("market_type", "SPOT").upper()
        if market_type not in ("SPOT", "FUTURES"):
            return jsonify({"success": False, "error": "market_type harus SPOT atau FUTURES"}), 400

        leverage = int(data.get("leverage", 1))
        if leverage < 1:
            return jsonify({"success": False, "error": "leverage minimal 1"}), 400
        if market_type == "SPOT" and leverage > 1:
            leverage = 1  # SPOT tidak support leverage—reset ke 1

        allocated_capital = float(data.get("allocated_capital", ALLOCATED_CAPITAL))
        qty = float(data.get("qty", 0))
        if qty <= 0:
            qty = allocated_capital / entry_price  # auto-calc qty

        entries = load_trade_entries()

        if symbol not in entries:
            # Posisi baru
            entries[symbol] = {
                'entries': [], 'sales': [],
                'allocated_capital': allocated_capital,
                'position_side': side,
                'market_type':   market_type,
                'leverage':      leverage,
            }
        else:
            # Cegah DCA ke arah berlawanan tanpa clear dulu
            existing_side = entries[symbol].get('position_side', 'LONG')
            existing_entries = entries[symbol].get('entries', [])
            if existing_entries and existing_side != side:
                return jsonify({
                    "success": False,
                    "error": (
                        f"Posisi {existing_side} masih aktif. "
                        f"Clear posisi lama dulu sebelum membuka {side}."
                    )
                }), 409
            # Update metadata posisi (leverage, market_type bisa diperbarui)
            entries[symbol]['position_side'] = side
            entries[symbol]['market_type']   = market_type
            entries[symbol]['leverage']       = leverage

        # Tambah entry baru ke list
        if 'entries' not in entries[symbol]:
            entries[symbol]['entries'] = []
        new_entry = {
            'price': entry_price,
            'qty':   round(qty, 6),
            'date':  datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        entries[symbol]['entries'].append(new_entry)
        entries[symbol]['allocated_capital'] = allocated_capital
        save_trade_entries(entries)

        summary = get_entry_summary(symbol)
        logger.info(
            f"💰 Entry: {symbol} {side} {market_type} x{leverage} "
            f"@ ${entry_price} qty={qty:.4f} (Avg: ${summary['avg_price']})"
        )
        return jsonify({
            "success": True,
            "message": f"{symbol} {side} entry #{summary['num_entries']} added",
            "summary": summary,
        })
    except RuntimeError as e:
        # DATABASE ERROR — jangan fallback, beri tahu UI secara eksplisit
        logger.error(f"[DB] set_trade_entry database error: {e}")
        return jsonify({"success": False, "error": str(e), "db_error": True}), 503
    except Exception as e:
        logger.error(f"Save entry error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/trade-entries/delete", methods=["POST"])
def delete_trade_entry():
    """Hapus entry spesifik by index atau hapus koin."""
    try:
        data = flask_request.get_json()  # type: ignore
        symbol = data.get("symbol", "").upper() if data else ""
        if not symbol:
            return jsonify({"success": False, "error": "No symbol provided"}), 400

        index = data.get("index", None)
        entries = load_trade_entries()
        if symbol in entries:
            if index is not None:
                idx = int(index)
                if 0 <= idx < len(entries[symbol]['entries']):
                    entries[symbol]['entries'].pop(idx)
                    save_trade_entries(entries)
                else:
                    return jsonify({"success": False, "error": "Index out of range"}), 400
            else:
                del entries[symbol]
                save_trade_entries(entries)
        return jsonify({"success": True})
    except RuntimeError as e:
        logger.error(f"[DB] delete_trade_entry database error: {e}")
        return jsonify({"success": False, "error": str(e), "db_error": True}), 503
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/trade-sales", methods=["POST"])
def set_trade_sale():
    """Catat penjualan aset."""
    try:
        data = flask_request.get_json()  # type: ignore
        symbol = data.get("symbol", "").upper()
        if not symbol or symbol not in AVAILABLE_PAIRS:
            return jsonify({"success": False, "error": "Invalid symbol"}), 400

        sell_price = float(data.get("sell_price", 0))
        qty = float(data.get("qty", 0))
        if sell_price <= 0 or qty <= 0:
            return jsonify({"success": False, "error": "Price and Qty must be > 0"}), 400

        entries = load_trade_entries()
        if symbol not in entries:
            entries[symbol] = {'entries': [], 'sales': [], 'allocated_capital': ALLOCATED_CAPITAL}
        if 'sales' not in entries[symbol]:
            entries[symbol]['sales'] = []

        new_sale = {
            'price': sell_price,
            'qty': round(qty, 6),
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        entries[symbol]['sales'].append(new_sale)
        save_trade_entries(entries)

        summary = get_entry_summary(symbol)
        return jsonify({"success": True, "summary": summary})
    except RuntimeError as e:
        logger.error(f"[DB] set_trade_sale database error: {e}")
        return jsonify({"success": False, "error": str(e), "db_error": True}), 503
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/trade-sales/delete", methods=["POST"])
def delete_trade_sale():
    """Hapus catatan penjualan spesifik."""
    try:
        data = flask_request.get_json()  # type: ignore
        symbol = data.get("symbol", "").upper()
        index = data.get("index")
        if not symbol or index is None:
            return jsonify({"success": False, "error": "Missing params"}), 400

        entries = load_trade_entries()
        if symbol in entries and 'sales' in entries[symbol]:
            idx = int(index)
            if 0 <= idx < len(entries[symbol]['sales']):
                entries[symbol]['sales'].pop(idx)
                save_trade_entries(entries)
                return jsonify({"success": True})
        return jsonify({"success": False, "error": "Not found"}), 404
    except RuntimeError as e:
        logger.error(f"[DB] delete_trade_sale database error: {e}")
        return jsonify({"success": False, "error": str(e), "db_error": True}), 503
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/entry-summary")
def api_entry_summary():
    """Get entry summary for a specific coin."""
    symbol = flask_request.args.get('symbol', AVAILABLE_PAIRS[0]).upper()
    summary = get_entry_summary(symbol)
    summary['allocated_capital'] = get_allocated_capital(symbol)
    return jsonify({"success": True, "symbol": symbol, "summary": summary})


# ==========================================
# 📥 EXPORT HELPER
# ==========================================
def build_export_dataframe(symbol: str = COIN_PAIR, timeframe: str = "4h", limit: int = 1000) -> pd.DataFrame:
    """
    [SSOT REFACTOR] Bangun DataFrame export menggunakan enrichment.get_fully_enriched_data().
    Semua indikator, OI, Funding Rate, Macro, dan Liquidity Walls sudah di-handle oleh SSOT.
    Fungsi ini hanya bertugas: memilih kolom, menambah BTC_Price, dan memformat output.
    """
    logger.info(f"  [Export] Building {symbol} {timeframe} ({limit} candles) via SSOT...")
    df, meta = enrichment.get_fully_enriched_data(symbol, interval=timeframe, limit=limit)
    if df is None or df.empty:
        logger.warning(f"  [Export] SSOT returned empty data for {symbol} {timeframe}")
        return pd.DataFrame()

    if meta.get("data_incomplete"):
        logger.warning(f"  [Export] Data not fully complete: {meta.get('missing_data')}")

    # ── Tambah BTC_Price untuk korelasi SMT ──
    logger.info(f"  [Export] Fetching BTCUSDT {timeframe} for SMT correlation...")
    df_btc = get_klines_df("BTCUSDT", timeframe, limit=limit)
    if not df_btc.empty:
        df_btc_slim = df_btc[['Open_Time', 'Close']].rename(columns={'Close': 'BTC_Price'})
        df = pd.merge_asof(
            df.sort_values('Open_Time'),
            df_btc_slim.sort_values('Open_Time'),
            on='Open_Time', direction='backward'
        )
    else:
        df['BTC_Price'] = None

    # ── Rename Open_Time → Timestamp untuk format export ──
    if 'Timestamp' not in df.columns and 'Open_Time' in df.columns:
        df = df.rename(columns={'Open_Time': 'Timestamp'})
    if 'Timestamp' in df.columns:
        df['Timestamp'] = pd.to_datetime(df['Timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')

    # ── Pilih dan urutkan kolom ──
    col_order = [
        'Timestamp', 'Market_Session',
        'Open', 'High', 'Low', 'Close',
        'Total_Volume', 'Buy_Volume', 'Sell_Volume', 'Volume_Delta', 'CVD',
        'EMA_7', 'EMA_21', 'EMA_50', 'EMA_200',
        'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4',
        'RSI_6', 'StochRSI_K', 'StochRSI_D',
        'ATR_14', 'ATR_14_H4',
        'PDH', 'PDL', 'PWH', 'PWL',
        'FVG_Up_Top', 'FVG_Up_Bottom', 'FVG_Down_Top', 'FVG_Down_Bottom',
        'OB_Price', 'SFP_Sweep',
        'Fib_0.618', 'Fib_0.786',
        'MSB', 'BOS', 'CHoCH',
        'POC', 'VAH', 'VAL',
        'Open_Interest', 'Funding_Rate',
        'Buy_Liq', 'Sell_Liq',
        'BTC_Price', 'BTC_Dominance', 'Altcoin_Index',
    ]

    # Pastikan semua kolom ada (isi None jika tidak tersedia)
    for col in col_order:
        if col not in df.columns:
            df[col] = None

    # Forward fill untuk kolom institusional
    ffill_cols = ['CVD', 'Funding_Rate', 'Buy_Liq', 'Sell_Liq', 'BTC_Dominance', 'Altcoin_Index']
    for col in ffill_cols:
        if col in df.columns:
            df[col] = df[col].ffill().fillna(0.0)

    df_export = df[col_order].copy()

    # Bulatkan float ke 6 desimal
    float_cols = [c for c in col_order if c not in ('Timestamp', 'Market_Session', 'SFP_Sweep')]
    for col in float_cols:
        if col in df_export.columns:
            df_export[col] = pd.to_numeric(df_export[col], errors='coerce').round(6)

    logger.info(f"  [Export] Done — {len(df_export)} rows, cols={len(df_export.columns)}")
    return df_export


# ==========================================
# 📥 EXPORT ENDPOINTS
# ==========================================
@app.route("/api/export-csv")
def export_csv():
    timeframe = flask_request.args.get('tf', '4h')
    limit     = int(flask_request.args.get('limit', 1000))
    coin_pair = flask_request.args.get('pair', AVAILABLE_PAIRS[0]).upper()

    try:
        logger.info(f"📥 Export CSV requested: {coin_pair} {timeframe} {limit} candles")
        df_export = build_export_dataframe(symbol=coin_pair, timeframe=timeframe, limit=limit)

        if df_export.empty:
            return jsonify({"success": False, "error": "No data available"}), 500

        # ── Build dynamic filename ──
        now = datetime.now()
        ts_str = now.strftime('%Y%m%d_%H%M')
        filename = f"Data_Track_9.6_{coin_pair}_{timeframe.upper()}_{ts_str}.csv"

        # ── Build entry price header block ──
        entry_summary = get_entry_summary(coin_pair)
        header_lines = []
        header_lines.append(f"# ═══════════════════════════════════════════════════")
        header_lines.append(f"# PROTOCOL 9.6 — TRADE ENTRY SUMMARY")
        header_lines.append(f"# Symbol: {coin_pair}")
        header_lines.append(f"# Export Time: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC+8")
        header_lines.append(f"# Timeframe: {timeframe.upper()} | Candles: {len(df_export)}")
        header_lines.append(f"# ───────────────────────────────────────────────────")

        if entry_summary['num_entries'] > 0:
            header_lines.append(f"# ENTRIES ({entry_summary['num_entries']} positions):")
            for i, e in enumerate(entry_summary['entries'], 1):
                header_lines.append(f"#   Entry #{i}: Price=${e['price']}  Qty={e['qty']}  Date={e['date']}")
            header_lines.append(f"# ───────────────────────────────────────────────────")
            header_lines.append(f"# AVG ENTRY PRICE: ${entry_summary['avg_price']}")
            header_lines.append(f"# TOTAL QTY: {entry_summary['total_qty']}")
            header_lines.append(f"# TOTAL COST: ${entry_summary['total_cost']}")
            header_lines.append(f"# ALLOCATED CAPITAL: ${get_allocated_capital(coin_pair)}")
        else:
            header_lines.append(f"# NO ENTRY PRICES SET — Use dashboard to add entries")

        header_lines.append(f"# ═══════════════════════════════════════════════════")
        header_lines.append("")  # blank line before CSV data

        # ── Tulis ke in-memory buffer ──
        buf = io.StringIO()
        # Write header block
        buf.write("\n".join(header_lines) + "\n")
        # Write CSV data
        df_export.to_csv(buf, index=False, encoding='utf-8')
        buf.seek(0)

        logger.info(f"✅ CSV ready: {filename} ({len(df_export)} rows, {entry_summary['num_entries']} entries)")

        return Response(
            buf.getvalue(),
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'text/csv; charset=utf-8',
            }
        )

    except Exception as e:
        logger.error(f"Export CSV failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/export-excel")
def export_excel():
    timeframe = flask_request.args.get('tf', '4h')
    limit     = int(flask_request.args.get('limit', 1000))
    coin_pair = flask_request.args.get('pair', AVAILABLE_PAIRS[0]).upper()

    try:
        logger.info(f"📥 Export Excel requested: {coin_pair} {timeframe} {limit} candles")
        df_export = build_export_dataframe(symbol=coin_pair, timeframe=timeframe, limit=limit)

        if df_export.empty:
            return jsonify({"success": False, "error": "No data available"}), 500

        now = datetime.now()
        ts_str = now.strftime('%Y%m%d_%H%M')
        filename = f"Data_Track_9.6_{coin_pair}_{timeframe.upper()}_{ts_str}.xlsx"

        # ── Tulis ke in-memory bytes buffer ──
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            df_export.to_excel(
                writer,
                index=False,           # tanpa index baris
                sheet_name='Protocol_9.6',
            )
        buf.seek(0)

        logger.info(f"✅ Excel ready: {filename} ({len(df_export)} rows)")

        return Response(
            buf.getvalue(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
            }
        )

    except ImportError:
        return jsonify({"success": False, "error": "openpyxl not installed. Run: pip install openpyxl"}), 500
    except Exception as e:
        logger.error(f"Export Excel failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==========================================
# 📤 CSV ANALYSIS ENDPOINT
# ==========================================
@app.route("/api/analyze-csv", methods=["POST"])
def analyze_csv():
    """
    Upload a CSV file and run the 71-point quantitative scoring on it.
    Returns JSON with the full analysis result.
    """
    try:
        import re
        from io import StringIO

        if 'file' not in flask_request.files:
            return jsonify({"success": False, "error": "No file uploaded"}), 400

        f = flask_request.files['file']
        raw_text = f.read().decode('utf-8', errors='replace')

        # ── Parse '#' comment header for metadata ──
        metadata = {'Symbol': 'UNKNOWN', 'Timeframe': '4H', 'AVG_ENTRY_PRICE': None,
                    'TOTAL_QTY': None, 'TOTAL_COST': None, 'Export_Time': None}
        data_lines = []
        for line in raw_text.splitlines():
            ls = line.strip()
            if ls.startswith('#'):
                if 'Symbol' in ls:
                    parts = ls.split('Symbol')
                    if len(parts) > 1: metadata['Symbol'] = parts[1].replace(':', '').replace('=', '').strip()
                elif 'Timeframe' in ls:
                    parts = ls.split('Timeframe')
                    if len(parts) > 1: metadata['Timeframe'] = parts[1].replace(':', '').replace('=', '').strip()
                elif 'AVG ENTRY PRICE' in ls or 'AVG_ENTRY_PRICE' in ls:
                    m = re.search(r'[\d\.]+', ls.split('PRICE')[-1])
                    if m: metadata['AVG_ENTRY_PRICE'] = float(m.group())
                elif 'Entry #1: Price=' in ls:
                    m = re.search(r'Price=([\d\.]+)', ls)
                    if m and metadata['AVG_ENTRY_PRICE'] is None: metadata['AVG_ENTRY_PRICE'] = float(m.group(1))
                elif 'TOTAL QTY' in ls:
                    m = re.search(r'[\d\.]+', ls.split('QTY')[-1])
                    if m: metadata['TOTAL_QTY'] = float(m.group())
                elif 'TOTAL COST' in ls:
                    m = re.search(r'[\d\.]+', ls.split('COST')[-1])
                    if m: metadata['TOTAL_COST'] = float(m.group())
            else:
                data_lines.append(ls)

        if not data_lines:
            return jsonify({"success": False, "error": "CSV has no data rows"}), 400

        csv_str = '\n'.join(data_lines)
        df = pd.read_csv(StringIO(csv_str))

        if len(df) < 22:
            return jsonify({"success": False, "error": f"Need at least 22 rows, got {len(df)}"}), 400

        # ── Apply indicators if missing ──
        if 'EMA_21' not in df.columns:
            import pandas_ta as ta2
            df['EMA_21']  = ta2.ema(df['Close'], length=21)
            df['EMA_50']  = ta2.ema(df['Close'], length=50)
            df['EMA_200'] = ta2.ema(df['Close'], length=200)
            df['RSI_6']   = ta2.rsi(df['Close'], length=6)
            atr_res = ta2.atr(df['High'], df['Low'], df['Close'], length=14)
            df['ATR_14'] = atr_res

        result = algo_scoring.calculate_71point_score(df, metadata)
        if result is None:
            return jsonify({"success": False, "error": "Scoring returned None — insufficient indicators"}), 400

        # Build market_context dict from last candle
        ctx_cols = ['MSB','BOS','CHoCH','SFP_Sweep','FVG_Up_Top','FVG_Up_Bottom',
                    'FVG_Down_Top','FVG_Down_Bottom','OB_Price','Fib_0.618','Fib_0.786',
                    'POC','VAH','VAL','Buy_Liq','Sell_Liq','PDH','PDL','PWH','PWL',
                    'EMA_7','EMA_7_H4','EMA_21_H4','EMA_50_H4','EMA_200_H4',
                    'StochRSI_K','StochRSI_D','Funding_Rate','BTC_Price','BTC_Dominance','Altcoin_Index']
        last = df.iloc[-1]
        market_ctx = {}
        for col in ctx_cols:
            if col in df.columns:
                v = last.get(col)
                try:
                    if pd.notna(v) and str(v) != '':
                        market_ctx[col] = round(float(v), 6)
                except Exception:
                    pass

        return jsonify({
            "success":        True,
            "metadata":       metadata,
            "current_price":  float(last.get('Close', 0)),
            "timestamp":      str(last.get('Timestamp', '')),
            "long":           result['long'],
            "short":          result['short'],
            "emergency":      result['emergency'],
            "exit":           result.get('exit', {}),
            "variables":      result.get('variables', {}),
            "market_context": market_ctx,
            "rows":           len(df),
        })

    except Exception as e:
        logger.error(f"CSV Analysis Error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/system_health")
def api_system_health():
    """
    System Health — dibaca UI setiap 30 detik.
    Berisi daftar koin dengan data API tidak tersedia.
    Error ini sebelumnya dikirim via Telegram, sekarang dialihkan ke UI banner.
    """
    try:
        with signal_monitor._state_lock:
            errors = dict(signal_monitor._alert_state.get("system_errors", {}))
        return jsonify({
            "status":      "warning" if errors else "ok",
            "errors":      errors,
            "error_count": len(errors),
            "checked_at":  datetime.now().strftime("%H:%M:%S"),
        })
    except Exception as e:
        logger.warning(f"system_health error: {e}")
        return jsonify({"status": "unknown", "errors": {}, "error_count": 0}), 500


@app.route("/api/scanner")
def api_scanner():
    """Market Scanner: Evaluasi skor LONG dan SHORT untuk semua koin secara paralel."""

    results = []

    def analyze_coin(pair):
        try:
            # Gunakan shared cache agar data konsisten dengan /api/data (detail view)
            df_quant, data_meta, _df_m15_scan = _get_enriched_data(pair)
            if df_quant is not None and not df_quant.empty and len(df_quant) >= 22:
                # Sertakan entry price aktif jika ada posisi — konsisten dengan detail view
                _entry_sum = get_entry_summary(pair)
                _avg_cost  = _entry_sum.get('rolling_avg_cost') if _entry_sum.get('remaining_qty', 0) > 0 else None
                meta = {'Symbol': pair, 'AVG_ENTRY_PRICE': _avg_cost, 'ENTRY_DATE': None}
                score_res = algo_scoring.calculate_71point_score(
                    df_quant, meta, df_m15=_df_m15_scan, ml_engine=_ui_ml_engine
                )
                if score_res:
                    with signal_monitor._state_lock:
                        pair_state = signal_monitor._alert_state.get(pair, {})
                    return {
                        "pair":             pair,
                        "close":            float(df_quant.iloc[-1]["Close"]),
                        "long_code":        score_res["long"]["code"],
                        "short_code":       score_res["short"]["code"],
                        "incomplete":       data_meta.get("data_incomplete", False),
                        "ml_signal":        score_res['long'].get('ml_signal', 'FLAT'),
                        "ml_confidence":    score_res['long'].get('ml_confidence', 0.0),
                        "ml_size":          score_res['long'].get('ml_size', 'SKIP'),
                        "ml_proba":         score_res['long'].get('ml_proba', {}),
                        "ml_signal_s":      score_res['short'].get('ml_signal', 'FLAT'),
                        "ml_confidence_s":  score_res['short'].get('ml_confidence', 0.0),
                        "ml_size_s":        score_res['short'].get('ml_size', 'SKIP'),
                        "long_score":       round(score_res['long'].get('total', 0.0), 1),
                        "short_score":      round(score_res['short'].get('total', 0.0), 1),
                        # Historical Signal State (dari Telegram alert terakhir)
                        "last_signal_type": pair_state.get("last_signal", None),
                        "last_signal_ts":   pair_state.get("last_signal_ts", None),
                        "last_signal_conf": pair_state.get("last_signal_conf", None),
                    }
        except Exception as e:
            logger.error(f"[Scanner] Error analyzing {pair}: {e}")
        return {"pair": pair, "error": True}


    # Eksekusi paralel agar tidak memblokir UI dan menghemat waktu
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(analyze_coin, pair) for pair in AVAILABLE_PAIRS]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                
    # Urutkan berdasarkan skor LONG tertinggi sebagai default
    results.sort(key=lambda x: x.get("ml_confidence", 0), reverse=True)
    
    return jsonify({"success": True, "data": results})


# ==========================================
# 🚀 MAIN
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🖥️  Protocol 9.6 Dashboard starting on http://0.0.0.0:{port}")
    # ── Start background signal monitor (15-min polling + Telegram alerts) ──
    signal_monitor.start_background_monitor()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)