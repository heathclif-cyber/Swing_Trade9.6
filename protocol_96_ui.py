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
from flask import Flask, render_template, jsonify, Response, request as flask_request  # type: ignore
from binance.client import Client  # type: ignore
from binance.exceptions import BinanceAPIException, BinanceRequestException  # type: ignore
import protocol_96_enrichment as enrichment  # type: ignore
import algo_scoring  # type: ignore
import signal_monitor  # type: ignore
import data_engine
from requests.packages import urllib3  # type: ignore
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# ⚙️ USER CONFIGURATION
# ==========================================
AVAILABLE_PAIRS = ["SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "PENDLEUSDT", "DOGEUSDT", "LINKUSDT", "WLFIUSDT", "ETHUSDT"]
COIN_PAIR = AVAILABLE_PAIRS[0]
ALLOCATED_CAPITAL = 200

# File path untuk menyimpan entry price per koin
# Railway: set env var TRADE_DATA_PATH=/app/data/trade_entries.json dan mount Volume ke /app/data
_default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_entries.json')
TRADE_ENTRIES_FILE = os.environ.get('TRADE_DATA_PATH', _default_path)
# Auto-create direktori jika belum ada (penting untuk Railway Volume)
os.makedirs(os.path.dirname(os.path.abspath(TRADE_ENTRIES_FILE)), exist_ok=True)

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
    """Buat koneksi PostgreSQL. Return None jika tidak tersedia."""
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
        logger.warning(f"PostgreSQL connection failed (fallback ke file): {e}")
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
    """Load trade entries — PostgreSQL jika DATABASE_URL ada, fallback ke file JSON."""
    conn = _get_pg_conn()
    if conn:
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
            logger.warning(f"PostgreSQL load failed (fallback ke file): {e}")
            if conn: conn.close()
    # ── Fallback: local JSON file (development) ──
    if os.path.exists(TRADE_ENTRIES_FILE):
        try:
            with open(TRADE_ENTRIES_FILE, 'r') as f:
                return _migrate_format(json.load(f))
        except Exception as e:
            logger.warning(f"Failed to load trade entries from file: {e}")
    return {}

def save_trade_entries(entries: dict) -> None:  # type: ignore
    """Simpan trade entries — PostgreSQL jika DATABASE_URL ada, fallback ke file JSON.
    
    [DATA GUARD] Jika DATABASE_URL aktif dan data kosong {}, tolak penyimpanan
    untuk mencegah penghapusan (wipe) data existing di database.
    """
    # ── DATA GUARD: blokir save dict kosong ke PostgreSQL ──
    if DATABASE_URL and not entries:
        logger.critical(
            "[DATA GUARD] DITOLAK: Mencoba save data kosong {} ke PostgreSQL. "
            "Save diabaikan untuk mencegah data loss."
        )
        return
    conn = _get_pg_conn()
    if conn:
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
            return
        except Exception as e:
            logger.warning(f"PostgreSQL save failed (fallback ke file): {e}")
            if conn: conn.close()
    # ── Fallback: local JSON file ──
    try:
        with open(TRADE_ENTRIES_FILE, 'w') as f:
            json.dump(entries, f, indent=2)
        logger.info(f"💾 Trade entries saved to {TRADE_ENTRIES_FILE}")
    except Exception as e:
        logger.error(f"Failed to save trade entries: {e}")


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

def get_klines_fapi(symbol: str, interval: str, limit: int = 1000) -> pd.DataFrame:
    """Fetch from Binance Futures for indices like BTCDOMUSDT and DEFIUSDT."""
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        total_klines = []
        end_time = None
        while len(total_klines) < limit:
            req_limit = min(1500, limit - len(total_klines))
            params = {"symbol": symbol, "interval": interval, "limit": req_limit}
            if end_time:
                params['endTime'] = end_time
            resp = http_requests.get(url, params=params, timeout=10, verify=False)
            if resp.status_code == 200:
                chunk = resp.json()
                if not chunk: break
                total_klines = chunk + total_klines
                if len(chunk) < req_limit: break
                end_time = chunk[0][0] - 1
            else:
                break
        if total_klines:
            df = pd.DataFrame(total_klines, columns=[
                'Open_Time', 'Open', 'High', 'Low', 'Close', 'Total_Volume',
                'Close_Time', 'Quote_Asset_Volume', 'Trades', 'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
            ])
            df['Open_Time'] = pd.to_datetime(df['Open_Time'], unit='ms')
            for col in ['Open', 'High', 'Low', 'Close', 'Total_Volume']:
                df[col] = df[col].astype(float)
            return df
    except Exception as e:
        logger.warning(f"Error fetching fapi klines for {symbol} {interval}: {e}")
    return pd.DataFrame()


def apply_full_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Apply EMA, RSI, StochRSI, and ATR indicators."""
    if df.empty:
        return df

    df['EMA_7']   = ta.ema(df['Close'], length=7)
    df['EMA_21']  = ta.ema(df['Close'], length=21)
    df['EMA_50']  = ta.ema(df['Close'], length=50)
    df['EMA_200'] = ta.ema(df['Close'], length=200)
    df['RSI_6']   = ta.rsi(df['Close'], length=6)

    # ATR-14 for structural SL calculation
    atr_result = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    df['ATR_14'] = atr_result if atr_result is not None else None

    try:
        stoch_rsi = ta.stochrsi(df['Close'], length=14, rsi_length=14, k=3, d=3)
        if stoch_rsi is not None and not stoch_rsi.empty:
            df['StochRSI_K'] = stoch_rsi.iloc[:, 0]
            df['StochRSI_D'] = stoch_rsi.iloc[:, 1]
        else:
            df['StochRSI_K'] = None
            df['StochRSI_D'] = None
    except Exception:
        df['StochRSI_K'] = None
        df['StochRSI_D'] = None

    return df


def fetch_oi_data(symbol: str = COIN_PAIR, limit: int = 4) -> list:
    """Fetch Open Interest history from Binance Futures."""
    try:
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        total_data = []
        end_time = None
        while len(total_data) < limit:
            req_limit = min(500, limit - len(total_data))
            params = {"symbol": symbol, "period": "15m", "limit": req_limit}
            if end_time:
                params['endTime'] = end_time
            resp = http_requests.get(url, params=params, timeout=10, verify=False)
            if resp.status_code == 200:
                chunk = resp.json()
                if not chunk: break
                total_data = chunk + total_data
                if len(chunk) < req_limit: break
                end_time = chunk[0]['timestamp'] - 1
            else:
                break
        return total_data
    except Exception as e:
        logger.warning(f"OI fetch error: {e}")
    return []


def fetch_funding_rate(symbol: str = COIN_PAIR, limit: int = 100) -> list:
    """Fetch Funding Rate history from Binance Futures USDⓈ-M."""
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        total_data = []
        end_time = None
        while len(total_data) < limit:
            req_limit = min(1000, limit - len(total_data))
            params = {"symbol": symbol, "limit": req_limit}
            if end_time:
                params['endTime'] = end_time
            resp = http_requests.get(url, params=params, timeout=10, verify=False)
            if resp.status_code == 200:
                chunk = resp.json()
                if not chunk: break
                total_data = chunk + total_data
                if len(chunk) < req_limit: break
                end_time = chunk[0]['fundingTime'] - 1
            else:
                break
        return total_data
    except Exception as e:
        logger.warning(f"Funding Rate fetch error: {e}")
    return []


def fetch_liquidation_data(symbol: str = COIN_PAIR, limit: int = 100) -> list:
    """Fetch recent Force Order (Liquidation) events from Binance Futures."""
    try:
        url = "https://fapi.binance.com/fapi/v1/allForceOrders"
        total_data = []
        end_time = None
        while len(total_data) < limit:
            req_limit = min(100, limit - len(total_data))
            params = {"symbol": symbol, "limit": req_limit}
            if end_time:
                params['endTime'] = end_time
            resp = http_requests.get(url, params=params, timeout=10, verify=False)
            if resp.status_code == 200:
                chunk = resp.json()
                if not chunk: break
                total_data = chunk + total_data
                if len(chunk) < req_limit: break
                end_time = chunk[0]['time'] - 1
            else:
                break
        return total_data
    except Exception as e:
        logger.warning(f"Liquidation data fetch error: {e}")
    return []


def apply_cvd(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate per-bar Volume_Delta and Cumulative Volume Delta (CVD).
    Volume_Delta = Buy_Volume - Sell_Volume  (already exists, recalculated for safety)
    CVD = cumsum(Volume_Delta) over the dataset window.
    """
    df = df.copy()
    if 'Buy_Volume' in df.columns and 'Sell_Volume' in df.columns:
        df['Volume_Delta'] = df['Buy_Volume'] - df['Sell_Volume']
        df['CVD'] = df['Volume_Delta'].cumsum()
    else:
        df['Volume_Delta'] = 0.0
        df['CVD'] = 0.0
    return df


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

            import algo_scoring as _as
            meta   = {"Symbol": pair, "AVG_ENTRY_PRICE": avg_entry, "ENTRY_DATE": None}
            result = _as.calculate_71point_score(df, meta)
            close  = float(df.iloc[-1]["Close"])

            if result:
                adj_L  = result["long"]["total"]
                adj_S  = result["short"]["total"]
                code_L = result["long"]["code"]
                code_S = result["short"]["code"]
                lvl_L  = result["long"]["levels"]
                lvl_S  = result["short"]["levels"]

                pnl_str = ""
                if avg_entry:
                    pnl = (close / avg_entry - 1) * 100
                    pnl_str = f"\n💼 Avg Entry: ${avg_entry:.4f} | PnL: {pnl:+.2f}%"

                msg = (
                    f"🧪 <b>TEST SIGNAL — {pair}</b>\n"
                    f"{'─'*28}\n"
                    f"💰 Harga: <b>${close:.6f}</b>{pnl_str}\n\n"
                    f"📊 <b>LONG</b>: {adj_L:.0f}/71 pts → <b>{code_L}</b>\n"
                    f"  SL: ${lvl_L['sl_structure']:.4f} | TP1: ${lvl_L['tp1']:.4f} (R:R {lvl_L['rr1']}×)\n\n"
                    f"📊 <b>SHORT</b>: {adj_S:.0f}/71 pts → <b>{code_S}</b>\n"
                    f"  SL: ${lvl_S['sl_structure']:.4f} | TP1: ${lvl_S['tp1']:.4f} (R:R {lvl_S['rr1']}×)\n\n"
                    f"{'─'*28}\n"
                    f"✅ Signal Monitor berjalan normal di Railway!"
                )
                if data_meta.get("data_incomplete"):
                    msg += f"\n⚠️ Data tidak lengkap: {', '.join(data_meta.get('missing_data', []))}"
                signal_monitor._send_telegram(msg)
                return jsonify({
                    "ok": True, "pair": pair,
                    "long": {"score": adj_L, "code": code_L},
                    "short": {"score": adj_S, "code": code_S},
                    "close": close,
                    "telegram": "sent",
                    "data_warning": data_meta.get("missing_data", []),
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


def fetch_live_macro_and_liq(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    Helper mandiri: tarik data Makro (BTC Dominance, Altcoin Index via CMC)
    dan Likuiditas (Buy_Wall, Sell_Wall via Binance Orderbook), lalu inject
    ke kolom df sebagai konstanta pada semua baris.

    Dipanggil dari api_data() DAN _enrich_df() di signal_monitor.py
    agar keduanya punya context makro yang sama saat menghitung skor.
    """
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

        # ── ⚡ Optimized Data Fetching ──
        # Fetch each timeframe ONCE to reduce API calls and latency
        df_cache: dict[str, pd.DataFrame] = {}
        btc_cache: dict[str, pd.DataFrame] = {}
        
        for label, interval in INTERVAL_MAP.items():
            req_limit = 250 if label in ['1h', '4h'] else 20
            
            logger.info(f"  Fetching {coin_pair} {label} ({req_limit} candles)...")
            if label in ['1d', '1w']:
                df = data_engine.get_macro_data(coin_pair, interval)
            else:
                df = get_klines_df(coin_pair, interval, limit=req_limit)
            
            # Apply base indicators for UI display consistency
            if label in ['1h', '4h'] and not df.empty:
                df = data_engine.apply_base_indicators(df)
            df_cache[label] = df
            
            logger.info(f"  Fetching BTCUSDT {label} (20 candles)...")
            if label in ['1d', '1w']:
                btc_df = data_engine.get_macro_data("BTCUSDT", interval)
            else:
                btc_df = get_klines_df("BTCUSDT", interval, limit=20)
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

        # ── Protocol 9.6 Battle Plan Computation (Wick Hunter Edition) ──
        # Uses ATR-based Structural SL and Liquidity-based TPs
        df_1h = df_cache.get('1h', pd.DataFrame())
        df_4h = df_cache.get('4h', pd.DataFrame())
        
        battle_plan: dict = {}
        if not df_4h.empty and len(df_4h) >= 20:
            last4h  = df_4h.iloc[-1]
            atr_val = last4h.get('ATR_14')
            atr_h4  = float(atr_val) if pd.notna(atr_val) else 0.0

            # Swing Low macro: lowest Low in last 20 H4 bars (≈ 80 hours)
            swing_low_h4 = float(df_4h['Low'].iloc[-20:].min())
            swing_high_h4 = float(df_4h['High'].iloc[-20:].max())

            # Structural SL = Swing Low - (2.0 × ATR_H4) — stop-hunt proof
            structural_sl = swing_low_h4 - (2.0 * atr_h4) if atr_h4 > 0 else swing_low_h4 * 0.985

            # EMA walls above current price (for TP1 in Markdown phase)
            ema_levels = []
            for col in ['EMA_7', 'EMA_21', 'EMA_50', 'EMA_200']:
                v = last4h.get(col)
                if pd.notna(v):
                    ema_levels.append((col, float(v)))

            # Determine market phase: Markup if price > EMA_21 H4, else Markdown
            price_h4 = float(last4h['Close'])
            ema21_h4 = float(last4h.get('EMA_21', price_h4))
            market_phase = "MARKUP" if price_h4 > ema21_h4 else "MARKDOWN"

            # ── TP Candidate Pool: all meaningful levels ABOVE current price ──
            tp_candidates: list[tuple[str, float]] = []
            for name, val in ema_levels:
                if val > price_h4:
                    tp_candidates.append((name, val))
            pdh = liquidity.get('PDH', 0.0)
            pwh = liquidity.get('PWH', 0.0)
            if pdh > price_h4:
                tp_candidates.append(('PDH', pdh))
            if pwh > price_h4:
                tp_candidates.append(('PWH', pwh))
            # Sort ascending, deduplicate by label
            tp_candidates.sort(key=lambda x: x[1])
            seen_lbls: dict[str, float] = {}
            deduped_candidates: list[tuple[str, float]] = []
            for lbl, v in tp_candidates:
                if lbl not in seen_lbls:
                    seen_lbls[lbl] = v
                    deduped_candidates.append((lbl, v))
            tp_candidates = deduped_candidates

            # ── FIX 1: TP Validation Against Entry Price ──
            # We need the avg_entry_price here. Fetch it early from trade_entries.
            avg_entry_for_tp = get_entry_price(coin_pair)  # 0.0 if not set

            # Re-classify candidates: only count as "TP" if they are ABOVE avg_entry_price
            # If below entry → classify as "Relief Exit / Cut Minor"
            classified_tps = []
            relief_exits = []
            for lbl, v in tp_candidates:
                if avg_entry_for_tp > 0 and v <= avg_entry_for_tp:
                    relief_exits.append((lbl, v, "RELIEF_EXIT"))
                else:
                    classified_tps.append((lbl, v, "TP"))

            # Assign TP1/2/3 only from validated TP levels (above entry)
            # If insufficient valid TPs, fallback to escalated projections
            def _tp_fallback(n: int, base: float) -> tuple[str, float]:
                multiples = [1.06, 1.10, 1.15]
                return (f'Proj+{int(multiples[n]*100-100)}%', base * multiples[n])

            if len(classified_tps) >= 1:
                tp1_name, tp1_val, _ = classified_tps[0]
            else:
                tp1_name, tp1_val = _tp_fallback(0, max(avg_entry_for_tp, price_h4))
            if len(classified_tps) >= 2:
                tp2_name, tp2_val, _ = classified_tps[1]
            else:
                tp2_name, tp2_val = _tp_fallback(1, max(avg_entry_for_tp, price_h4))
            if len(classified_tps) >= 3:
                tp3_name, tp3_val, _ = classified_tps[2]
            else:
                tp3_name, tp3_val = _tp_fallback(2, max(avg_entry_for_tp, price_h4))

            # ── FIX 2: Safety Net Layer Validation (must be BELOW current price) ──
            # A buy-limit order above current price executes as market immediately → INVALID
            current_price_for_layers = float(df_1h.iloc[-1]['Close']) if not df_1h.empty else price_h4
            fib_range = swing_high_h4 - swing_low_h4
            fib_786  = swing_high_h4 - (fib_range * 0.786)
            pdl_val  = liquidity.get('PDL', swing_low_h4)
            pwl_val  = liquidity.get('PWL', swing_low_h4 * 0.97)

            def _validate_layer(val: float, current: float) -> tuple[float, bool]:
                """Returns (val, is_valid). Invalid if >= current price."""
                return (val, val < current)

            layer1_val, layer1_valid = _validate_layer(fib_786, current_price_for_layers)
            layer2_val, layer2_valid = _validate_layer(pdl_val, current_price_for_layers)
            layer3_val, layer3_valid = _validate_layer(pwl_val, current_price_for_layers)

            # ── FIX 3: Kill Switch Emergency Override Flag ──
            # Detect kill switch condition here so battle_plan can react
            kill_switch_now = False
            abort_note = ""
            relief_label = ""
            if not df_4h.empty and len(df_4h) >= 2:
                last_closed_h4 = df_4h.iloc[-2]
                ema21_ks = last_closed_h4.get('EMA_21')
                if pd.notna(ema21_ks) and float(last_closed_h4['Close']) < float(ema21_ks):
                    kill_switch_now = True
                    # Find nearest EMA above price for abort target
                    relief_candidates = [(n, v) for n, v in ema_levels if v > current_price_for_layers]
                    relief_candidates.sort(key=lambda x: x[1])
                    if relief_candidates:
                        relief_name, relief_price = relief_candidates[0]
                        relief_label = f"{relief_name} (${relief_price:.4f})"
                    abort_note = (
                        f"🚨 ABORT PROCEDURE AKTIF. Posisi dalam Death Spiral. "
                        f"JANGAN menunggu TP. Eksekusi Surgical Cut saat relief bounce pertama "
                        f"ke {relief_label or 'resistance terdekat'}. "
                        f"Proteksi modal adalah prioritas utama."
                    )

            # Relief exits info for frontend labeling
            relief_exit_levels = [{"label": f"{lbl} (Relief)", "val": round(v, 8)} for lbl, v, _ in relief_exits]

            battle_plan = {
                "market_phase": market_phase,
                "atr_h4": round(atr_h4, 8),
                "swing_low": round(swing_low_h4, 8),
                "swing_high": round(swing_high_h4, 8),
                "structural_sl": round(structural_sl, 8),
                # TP (validated above entry)
                "tp1_val": round(tp1_val, 8),
                "tp1_label": tp1_name,
                "tp2_val": round(tp2_val, 8),
                "tp2_label": tp2_name,
                "tp3_val": round(tp3_val, 8),
                "tp3_label": tp3_name,
                "relief_exits": relief_exit_levels,   # EMAs below entry (Cut Loss targets)
                # Layers (validated below current price)
                "layer1_val": round(layer1_val, 8),
                "layer1_valid": layer1_valid,
                "layer2_val": round(layer2_val, 8),
                "layer2_valid": layer2_valid,
                "layer3_val": round(layer3_val, 8),
                "layer3_valid": layer3_valid,
                # Kill Switch Emergency Override
                "kill_switch_active": kill_switch_now,
                "abort_note": abort_note,
            }

        computed["battle_plan"] = battle_plan  # type: ignore[assignment]

        # ── Section 3: User & System State ──
        current_price = 0.0
        if not df_1h.empty:
            current_price = float(df_1h.iloc[-1]['Close'])

        # --- Dynamic Structural SL: Swing Low - 2×ATR_H4 ---
        coin_state = BotState.get(coin_pair)
        bp = computed.get("battle_plan", {})
        if bp and bp.get("structural_sl", 0) > 0:
            new_sl = bp["structural_sl"]
            # Trailing: SL only moves UP (locks in profit)
            if new_sl > coin_state["active_sl"] or coin_state["active_sl"] == 0:
                coin_state["active_sl"] = new_sl
            if coin_state["initial_sl"] == 0:
                coin_state["initial_sl"] = coin_state["active_sl"]

        # Check for kill switch trigger
        if not df_4h.empty and len(df_4h) >= 2:
            ema21_check = df_4h.iloc[-2].get('EMA_21')
            if pd.notna(ema21_check):
                if float(df_4h.iloc[-2]['Close']) < float(ema21_check):
                    coin_state["status"] = "KILL_SWITCH"
                    coin_state["alerts_sent"]["KILL_SWITCH"] = True
                else:
                    # Reset kill switch if recovered above EMA21
                    if coin_state["status"] == "KILL_SWITCH":
                        coin_state["status"] = "ACTIVE"

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
            df_quant, data_meta = enrichment.get_fully_enriched_data(coin_pair, interval="4h", limit=250)

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
                quant_results = algo_scoring.calculate_71point_score(df_quant, meta)

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
    Bangun DataFrame lengkap berisi semua kolom yang dibutuhkan untuk upload ke Gemini:
    Timestamp, OHLCV, Volume breakdown, EMA 7/21/50/200, RSI 6,
    StochRSI K/D, Open_Interest, dan BTC_Price untuk korelasi SMT.
    """
    interval_map = {
        "15m": Client.KLINE_INTERVAL_15MINUTE,
        "1h":  Client.KLINE_INTERVAL_1HOUR,
        "4h":  Client.KLINE_INTERVAL_4HOUR,
        "1d":  Client.KLINE_INTERVAL_1DAY,
        "1w":  Client.KLINE_INTERVAL_1WEEK,
    }
    interval = str(interval_map.get(timeframe, Client.KLINE_INTERVAL_1HOUR))

    # ── Fetch target coin OHLCV + indicators ──
    logger.info(f"  [Export] Fetching {symbol} {timeframe} ({limit} candles)...")
    df = get_klines_df(symbol, interval, limit=limit)
    if df.empty:
        return pd.DataFrame()
    df = apply_full_indicators(df)

    # ── Fetch BTC_Price sebagai kolom korelasi SMT ──
    logger.info(f"  [Export] Fetching BTCUSDT {timeframe} for SMT...")
    df_btc = get_klines_df("BTCUSDT", interval, limit=limit)
    if not df_btc.empty:
        # Align berdasarkan Open_Time — merge outer lalu forward fill
        df_btc_slim = df_btc[['Open_Time', 'Close']].rename(columns={'Close': 'BTC_Price'})
        df = pd.merge(df, df_btc_slim, on='Open_Time', how='left')
    else:
        df['BTC_Price'] = None

    # ── Fetch Open Interest (M15 Futures) ──
    # Ambil history OI lebih banyak (misal 100 titik) untuk merge
    logger.info(f"  [Export] Fetching OI history (limit={limit})...")
    oi_raw = fetch_oi_data(symbol=symbol, limit=limit)

    if oi_raw:
        oi_df = pd.DataFrame(oi_raw)
        oi_df['Open_Time'] = pd.to_datetime(oi_df['timestamp'], unit='ms')
        oi_df = oi_df[['Open_Time', 'sumOpenInterest']].rename(columns={'sumOpenInterest': 'Open_Interest'})
        oi_df['Open_Interest'] = oi_df['Open_Interest'].astype(float)
        
        # Merge dengan df utama berdasarkan Open_Time paling dekat (nearest match)
        df = pd.merge_asof(
            df.sort_values('Open_Time'), 
            oi_df.sort_values('Open_Time'), 
            on='Open_Time', 
            direction='backward'
        )
    else:
        df['Open_Interest'] = None

    # ── [NEW] ENRICHMENT: Protocol 9.6 Anti-Inducement ──
    logger.info("  [Export] Applying Protocol 9.6 Data Enrichment...")
    try:
        # Fetch data for enrichment - Use more H4 data for stable EMA 200
        h4_limit = max(limit, 300)
        d1_limit = max(int(limit / 6), 50) # approximate D1 candles needed for the timeframe window
        w1_limit = max(int(limit / 42), 10)
        df_h4 = get_klines_df(symbol, Client.KLINE_INTERVAL_4HOUR, limit=h4_limit)
        df_d1 = get_klines_df(symbol, Client.KLINE_INTERVAL_1DAY, limit=d1_limit)
        df_w1 = get_klines_df(symbol, Client.KLINE_INTERVAL_1WEEK, limit=w1_limit)
        
        # Enrich dataset
        df = enrichment.enrich_dataset(df, df_h4, df_d1, df_w1)
    except Exception as e:
        logger.error(f"Enrichment error: {e}")
        import traceback
        traceback.print_exc()

    # ── [APEX] MODULE 1: Cumulative Volume Delta (CVD) ──
    logger.info("  [Export] Computing CVD (Cumulative Volume Delta)...")
    df = apply_cvd(df)

    # ── [APEX] MODULE 2: Funding Rate ──
    logger.info("  [Export] Fetching Funding Rate history...")
    try:
        fr_raw = fetch_funding_rate(symbol=symbol, limit=limit)
        if fr_raw:
            fr_df = pd.DataFrame(fr_raw)
            fr_df['Open_Time'] = pd.to_datetime(fr_df['fundingTime'], unit='ms')
            fr_df['Funding_Rate'] = fr_df['fundingRate'].astype(float)
            fr_df = fr_df[['Open_Time', 'Funding_Rate']]
            df = pd.merge_asof(
                df.sort_values('Open_Time'),
                fr_df.sort_values('Open_Time'),
                on='Open_Time',
                direction='backward'
            )
        else:
            df['Funding_Rate'] = None
    except Exception as e:
        logger.warning(f"Funding Rate merge error: {e}")
        df['Funding_Rate'] = None

    # ── [APEX] MODULE 3: Aggregated Liquidation Data ──
    logger.info("  [Export] Fetching Liquidation events...")
    try:
        liq_raw = fetch_liquidation_data(symbol=symbol, limit=limit)
        if liq_raw:
            liq_df = pd.DataFrame(liq_raw)
            # Each record has: symbol, side (BUY/SELL), price, origQty, time
            liq_df['liq_time'] = pd.to_datetime(liq_df['time'], unit='ms')
            liq_df['liq_value'] = liq_df['price'].astype(float) * liq_df['origQty'].astype(float)

            # Aggregate by matching to candle Open_Time using merge_asof
            # BUY side = Short liquidations (forced buy), SELL side = Long liquidations (forced sell)
            buy_liq = liq_df[liq_df['side'] == 'BUY'][['liq_time', 'liq_value']].rename(
                columns={'liq_time': 'Open_Time', 'liq_value': 'Buy_Liq'})
            sell_liq = liq_df[liq_df['side'] == 'SELL'][['liq_time', 'liq_value']].rename(
                columns={'liq_time': 'Open_Time', 'liq_value': 'Sell_Liq'})

            # Group by Open_Time (sum within same ms)
            if not buy_liq.empty:
                buy_liq = buy_liq.groupby('Open_Time', as_index=False).sum()
                df = pd.merge_asof(
                    df.sort_values('Open_Time'),
                    buy_liq.sort_values('Open_Time'),
                    on='Open_Time', direction='backward'
                )
            else:
                df['Buy_Liq'] = 0.0

            if not sell_liq.empty:
                sell_liq = sell_liq.groupby('Open_Time', as_index=False).sum()
                df = pd.merge_asof(
                    df.sort_values('Open_Time'),
                    sell_liq.sort_values('Open_Time'),
                    on='Open_Time', direction='backward'
                )
            else:
                df['Sell_Liq'] = 0.0
        else:
            df['Buy_Liq'] = 0.0
            df['Sell_Liq'] = 0.0
    except Exception as e:
        logger.warning(f"Liquidation merge error: {e}")
        df['Buy_Liq'] = 0.0
        df['Sell_Liq'] = 0.0

    # ── [APEX] MODULE 4: MACRO ALTCOIN CONTEXT (BTC.D & TOTAL3/DEFI) ──
    logger.info("  [Export] Fetching Crypto Macro Context (BTC.D & Altcoin Index)...")
    try:
        df_btcd = get_klines_fapi("BTCDOMUSDT", interval, limit=limit)
        if not df_btcd.empty:
            df_btcd_slim = df_btcd[['Open_Time', 'Close']].rename(columns={'Close': 'BTC_Dominance'})
            df = pd.merge_asof(
                df.sort_values('Open_Time'),
                df_btcd_slim.sort_values('Open_Time'),
                on='Open_Time',
                direction='backward'
            )
        else:
            df['BTC_Dominance'] = None

        df_defi = get_klines_fapi("1000DEFIUSDT", interval, limit=limit) # DEFI index mapping
        if df_defi.empty: df_defi = get_klines_fapi("DEFIUSDT", interval, limit=limit)
        if not df_defi.empty:
            df_defi_slim = df_defi[['Open_Time', 'Close']].rename(columns={'Close': 'Altcoin_Index'})
            df = pd.merge_asof(
                df.sort_values('Open_Time'),
                df_defi_slim.sort_values('Open_Time'),
                on='Open_Time',
                direction='backward'
            )
        else:
            df['Altcoin_Index'] = None
            
        # ── [APEX] LATEST MACRO FIX WITH CMC API ──
        # Binance FAPI indices drop the latest candles due to computation lag. We fill these gaps using precise live CMC API.
        try:
            CMC_API_KEY = "aa8eb4dd82974c308c5428e7c1be0121"
            cmc_url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
            cmc_headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
            r = http_requests.get(cmc_url, headers=cmc_headers, timeout=10)
            if r.status_code == 200:
                d = r.json()["data"]
                btc_dom_raw = round(d["btc_dominance"] * 100, 1)
                total_mcap = d["quote"]["USD"]["total_market_cap"]
                btc_dom_frac = d["btc_dominance"] / 100
                altcoin_index = round(total_mcap * (1 - btc_dom_frac) / 1_000_000_000, 1)
                
                mask_btc = df['BTC_Dominance'].isna() | (df['BTC_Dominance'] == 0)
                mask_alt = df['Altcoin_Index'].isna() | (df['Altcoin_Index'] == 0)
                df.loc[mask_btc, 'BTC_Dominance'] = btc_dom_raw
                df.loc[mask_alt, 'Altcoin_Index'] = altcoin_index
                logger.info(f"  [Export] Filled missing macro with CMC: BTC_Dom={btc_dom_raw}, AltIndex={altcoin_index}")
        except Exception as e:
            logger.warning(f"CMC API fetch error in export: {e}")
            
        # ── [APEX] LIVE LIQUIDITY WALL FIX (Orderbook) ──
        # Fetch current Bid/Ask walls from Binance Futures to fill missing Buy_Liq and Sell_Liq
        try:
            liq_url = "https://fapi.binance.com/fapi/v1/depth"
            liq_params = {"symbol": symbol.upper(), "limit": 500}
            r_liq = http_requests.get(liq_url, params=liq_params, timeout=10)
            if r_liq.status_code == 200:
                book = r_liq.json()
                close_last = float(df['Close'].iloc[-1])
                bids = [(float(p), float(q)) for p, q in book.get("bids", []) if float(p) < close_last]
                asks = [(float(p), float(q)) for p, q in book.get("asks", []) if float(p) > close_last]
                if bids and asks:
                    buy_wall = max(bids, key=lambda x: x[1])[0]
                    sell_wall = max(asks, key=lambda x: x[1])[0]
                    
                    mask_buy = df['Buy_Liq'].isna() | (df['Buy_Liq'] == 0)
                    mask_sell = df['Sell_Liq'].isna() | (df['Sell_Liq'] == 0)
                    
                    df.loc[mask_buy, 'Buy_Liq'] = round(buy_wall, 6)
                    df.loc[mask_sell, 'Sell_Liq'] = round(sell_wall, 6)
                    logger.info(f"  [Export] Filled missing Liquidity with Orderbook: Buy_Wall={buy_wall}, Sell_Wall={sell_wall}")
        except Exception as e:
            logger.warning(f"Orderbook API fetch error in export: {e}")
            
    except Exception as e:
        logger.warning(f"Macro context merge error: {e}")
        df['BTC_Dominance'] = None
        df['Altcoin_Index'] = None

    # ── Pilih dan urutkan kolom sesuai spesifikasi ──
    col_order = [
        'Timestamp',
        'Market_Session',
        'Open', 'High', 'Low', 'Close',
        'Total_Volume',
        'Buy_Volume',
        'Sell_Volume',
        'Volume_Delta', 'CVD',
        'EMA_7', 'EMA_21', 'EMA_50', 'EMA_200',
        'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4',
        'RSI_6',
        'StochRSI_K', 'StochRSI_D',
        'ATR_14', 'ATR_14_H4',
        'PDH', 'PDL', 'PWH', 'PWL',
        'FVG_Up_Top', 'FVG_Up_Bottom', 'FVG_Down_Top', 'FVG_Down_Bottom',
        'OB_Price', 'SFP_Sweep',
        'Fib_0.618', 'Fib_0.786',
        'MSB', 'BOS', 'CHoCH',
        'POC', 'VAH', 'VAL',
        'Open_Interest',
        'Funding_Rate',
        'Buy_Liq', 'Sell_Liq',
        'BTC_Price',
        'BTC_Dominance', 'Altcoin_Index'
    ]

    # Rename Close_Time → Timestamp (waktu close candle) jika belum ada (merging mungkin merubah nama)
    if 'Timestamp' not in df.columns and 'Close_Time' in df.columns:
        df = df.rename(columns={'Close_Time': 'Timestamp'})

    # Format Timestamp sebagai string ISO agar mudah dibaca AI
    if 'Timestamp' in df.columns:
        df['Timestamp'] = pd.to_datetime(df['Timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')

    # Pastikan semua kolom ada (isi None jika tidak tersedia)
    for col in col_order:
        if col not in df.columns:
            df[col] = None

    # Forward fill NaN values for institutional data columns
    apex_cols = ['CVD', 'Funding_Rate', 'Buy_Liq', 'Sell_Liq']
    for col in apex_cols:
        if col in df.columns:
            df[col] = df[col].ffill().fillna(0.0)
            
    # Fallback ffill untuk BTC_Dominance dan Altcoin_Index barangkali request CMC gagal
    for col in ['BTC_Dominance', 'Altcoin_Index']:
        if col in df.columns:
            df[col] = df[col].ffill()

    df_export = df[col_order].copy()

    # Bulatkan float ke 6 desimal agar file rapi
    float_cols = [
        'Open', 'High', 'Low', 'Close', 'Total_Volume',
        'Buy_Volume', 'Sell_Volume', 'Volume_Delta', 'CVD',
        'EMA_7', 'EMA_21', 'EMA_50', 'EMA_200',
        'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4',
        'RSI_6', 'StochRSI_K', 'StochRSI_D',
        'ATR_14', 'ATR_14_H4',
        'PDH', 'PDL', 'PWH', 'PWL',
        'FVG_Up_Top', 'FVG_Up_Bottom', 'FVG_Down_Top', 'FVG_Down_Bottom',
        'OB_Price',
        'Fib_0.618', 'Fib_0.786',
        'MSB', 'BOS', 'CHoCH',
        'POC', 'VAH', 'VAL',
        'Open_Interest', 'Funding_Rate',
        'Buy_Liq', 'Sell_Liq',
        'BTC_Price', 'BTC_Dominance', 'Altcoin_Index'
    ]
    for col in float_cols:
        if col in df_export.columns:
            df_export[col] = pd.to_numeric(df_export[col], errors='coerce').round(6)

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


# ==========================================
# 🚀 MAIN
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🖥️  Protocol 9.6 Dashboard starting on http://0.0.0.0:{port}")
    # ── Start background signal monitor (15-min polling + Telegram alerts) ──
    signal_monitor.start_background_monitor()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
