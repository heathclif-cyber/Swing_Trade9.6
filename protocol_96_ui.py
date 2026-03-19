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
from requests.packages import urllib3  # type: ignore
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# ⚙️ USER CONFIGURATION
# ==========================================
AVAILABLE_PAIRS = ["SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "PENDLEUSDT", "DOGEUSDT", "LINKUSDT", "WLFIUSDT", "ETHUSDT"]
COIN_PAIR = AVAILABLE_PAIRS[0]
ALLOCATED_CAPITAL = 200

# File path untuk menyimpan entry price per koin
TRADE_ENTRIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_entries.json')

BINANCE_API_KEY = ""
BINANCE_API_SECRET = ""

# ==========================================
# Flask App & Logger
# ==========================================
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Protocol_9.6_UI")

# Binance Client
try:
    binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET, requests_params={'verify': False})
    logger.info("Binance client initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Binance client: {e}")
    binance_client = None


# ==========================================
# 💾 TRADE ENTRIES (Persistent JSON Storage — Multi-Entry DCA)
# ==========================================
# Format baru: { "SUIUSDT": { "entries": [{"price": 1.05, "qty": 100, "date": "..."}, ...], "allocated_capital": 200 } }

def load_trade_entries() -> dict:  # type: ignore
    """Load entry prices & capital per coin dari file JSON."""
    if os.path.exists(TRADE_ENTRIES_FILE):
        try:
            with open(TRADE_ENTRIES_FILE, 'r') as f:
                data = json.load(f)
            # ── Migrate old format (single entry_price) to new multi-entry format ──
            for sym, val in data.items():
                if isinstance(val, dict) and 'entry_price' in val and 'entries' not in val:
                    old_price = float(val.get('entry_price', 0))
                    old_cap = float(val.get('allocated_capital', ALLOCATED_CAPITAL))
                    old_date = val.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    if old_price > 0:
                        data[sym] = {
                            'entries': [{'price': old_price, 'qty': old_cap / old_price if old_price else 0, 'date': old_date}],
                            'allocated_capital': old_cap,
                        }
                    else:
                        data[sym] = {'entries': [], 'allocated_capital': old_cap}
            return data
        except Exception as e:
            logger.warning(f"Failed to load trade entries: {e}")
    return {}


def save_trade_entries(entries: dict) -> None:  # type: ignore
    """Simpan entry prices & capital per coin ke file JSON."""
    try:
        with open(TRADE_ENTRIES_FILE, 'w') as f:
            json.dump(entries, f, indent=2)
        logger.info(f"💾 Trade entries saved to {TRADE_ENTRIES_FILE}")
    except Exception as e:
        logger.error(f"Failed to save trade entries: {e}")


def get_entry_summary(symbol: str) -> dict:  # type: ignore
    """Return entry summary: list of entries, average price, total qty, total cost."""
    entries = load_trade_entries()
    coin_data = entries.get(symbol, {})
    entry_list = coin_data.get('entries', [])

    if not entry_list:
        return {'entries': [], 'avg_price': 0.0, 'total_qty': 0.0, 'total_cost': 0.0, 'num_entries': 0}

    total_cost = sum(e['price'] * e['qty'] for e in entry_list)
    total_qty = sum(e['qty'] for e in entry_list)
    avg_price = total_cost / total_qty if total_qty > 0 else 0.0

    return {
        'entries': entry_list,
        'avg_price': round(avg_price, 8),
        'total_qty': round(total_qty, 6),
        'total_cost': round(total_cost, 4),
        'num_entries': len(entry_list),
    }


def get_entry_price(symbol: str) -> float:  # type: ignore
    """Dapatkan RATA-RATA entry price untuk koin tertentu. Return 0.0 jika belum di-set."""
    summary = get_entry_summary(symbol)
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
    """Fetch OHLCV data from Binance and process institutional volume structure."""
    if not binance_client:
        logger.error("Binance client not initialized. Cannot fetch data.")
        return pd.DataFrame()
    try:
        klines = binance_client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
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
        logger.error(f"Error fetching klines for {symbol} {interval}: {e}")
        return pd.DataFrame()

def get_klines_fapi(symbol: str, interval: str, limit: int = 1000) -> pd.DataFrame:
    """Fetch from Binance Futures for indices like BTCDOMUSDT and DEFIUSDT."""
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        resp = http_requests.get(url, params=params, timeout=10, verify=False)
        if resp.status_code == 200:
            df = pd.DataFrame(resp.json(), columns=[
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
        params = {"symbol": symbol, "period": "15m", "limit": limit}
        resp = http_requests.get(url, params=params, timeout=10, verify=False)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"OI fetch error: {e}")
    return []


def fetch_funding_rate(symbol: str = COIN_PAIR, limit: int = 100) -> list:
    """Fetch Funding Rate history from Binance Futures USDⓈ-M."""
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        params = {"symbol": symbol, "limit": limit}
        resp = http_requests.get(url, params=params, timeout=10, verify=False)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"Funding Rate fetch error: {e}")
    return []


def fetch_liquidation_data(symbol: str = COIN_PAIR, limit: int = 100) -> list:
    """Fetch recent Force Order (Liquidation) events from Binance Futures."""
    try:
        url = "https://fapi.binance.com/fapi/v1/allForceOrders"
        params = {"symbol": symbol, "limit": limit}
        resp = http_requests.get(url, params=params, timeout=10, verify=False)
        if resp.status_code == 200:
            return resp.json()
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


@app.route("/api/data")
def api_data():
    """Master endpoint: returns ALL data categories for the dashboard."""
    try:
        coin_pair = flask_request.args.get('pair', AVAILABLE_PAIRS[0]).upper()
        if coin_pair not in AVAILABLE_PAIRS:
            coin_pair = AVAILABLE_PAIRS[0]

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"📡 Fetching dashboard data for {coin_pair}...")

        # ── Section 1: Raw API Data ──
        # Only fetch the last 20 candles for raw display (lighter requests)
        raw_data = {}
        for label, interval in INTERVAL_MAP.items():
            logger.info(f"  Fetching {coin_pair} {label}...")
            df_coin = get_klines_df(coin_pair, interval, limit=20)
            logger.info(f"  Fetching BTCUSDT {label}...")
            df_btc = get_klines_df("BTCUSDT", interval, limit=20)
            raw_data[label] = {
                "coin": format_ohlcv_for_json(df_coin, last_n=10),
                "btc": format_ohlcv_for_json(df_btc, last_n=10),
            }
            time.sleep(0.1)  # Gentle rate limit

        # Futures OI Data
        logger.info("  Fetching OI data...")
        oi_raw = fetch_oi_data(limit=10, symbol=coin_pair)
        oi_formatted = []
        for item in oi_raw:
            ts = item.get('timestamp', 0)
            try:
                oi_formatted.append({
                    "timestamp": datetime.fromtimestamp(int(ts) / 1000).strftime('%Y-%m-%d %H:%M'),
                    "sumOpenInterest": float(item.get('sumOpenInterest', 0)),
                })
            except Exception:
                pass

        # ── Section 2: Computed Data ──
        # Fetch H1 & H4 with indicators (need more data for EMA 200)
        logger.info("  Computing H1 indicators...")
        df_1h = get_klines_df(coin_pair, Client.KLINE_INTERVAL_1HOUR, limit=250)
        df_1h = apply_full_indicators(df_1h)

        logger.info("  Computing H4 indicators...")
        df_4h = get_klines_df(coin_pair, Client.KLINE_INTERVAL_4HOUR, limit=250)
        df_4h = apply_full_indicators(df_4h)

        computed = {
            "indicators_1h": format_ohlcv_for_json(df_1h, last_n=10),
            "indicators_4h": format_ohlcv_for_json(df_4h, last_n=10),
        }

        # Liquidity Borders
        logger.info("  Fetching liquidity borders...")
        df_1d = get_klines_df(coin_pair, Client.KLINE_INTERVAL_1DAY, limit=3)
        df_1w = get_klines_df(coin_pair, Client.KLINE_INTERVAL_1WEEK, limit=3)

        liquidity: dict = {"PDH": 0.0, "PDL": 0.0, "PWH": 0.0, "PWL": 0.0}
        if len(df_1d) >= 2:
            liquidity["PDH"] = round(float(df_1d.iloc[-2]['High']), 6)  # type: ignore[call-overload]
            liquidity["PDL"] = round(float(df_1d.iloc[-2]['Low']), 6)  # type: ignore[call-overload]
        if len(df_1w) >= 2:
            liquidity["PWH"] = round(float(df_1w.iloc[-2]['High']), 6)  # type: ignore[call-overload]
            liquidity["PWL"] = round(float(df_1w.iloc[-2]['Low']), 6)  # type: ignore[call-overload]

        computed["liquidity_borders"] = liquidity  # type: ignore[assignment]

        # SMT Divergence
        logger.info("  Computing SMT divergence...")
        df_btc_h4 = get_klines_df("BTCUSDT", Client.KLINE_INTERVAL_4HOUR, limit=5)
        df_tgt_h4 = get_klines_df(coin_pair, Client.KLINE_INTERVAL_4HOUR, limit=5)

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

        # OI Delta
        oi_delta_pct = 0.0
        if len(oi_raw) >= 2:
            old_oi = float(oi_raw[0].get('sumOpenInterest', 1))
            new_oi = float(oi_raw[-1].get('sumOpenInterest', 1))
            if old_oi > 0:
                oi_delta_pct = round(((new_oi - old_oi) / old_oi) * 100, 4)  # type: ignore[call-overload]
        computed["oi_delta_pct"] = oi_delta_pct  # type: ignore[assignment]

        # ── Protocol 9.6 Battle Plan Computation (Wick Hunter Edition) ──
        # Uses ATR-based Structural SL and Liquidity-based TPs
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

            # TP1: nearest EMA wall strictly above current price (first structural resistance)
            emas_above = [(name, val) for name, val in ema_levels if val > price_h4]
            if emas_above:
                tp1_name, tp1_val = min(emas_above, key=lambda x: x[1])
            else:
                tp1_name, tp1_val = ('PDH', liquidity.get('PDH', price_h4 * 1.06))

            # TP2 = PDH (Previous Day High) — institutional daily run
            tp2_val = liquidity.get('PDH', 0.0)

            # TP3 = PWH (Previous Week High) — apex moonbag
            tp3_val = liquidity.get('PWH', 0.0)

            # Fibonacci safety net layers (Markdown phase entry zones)
            # Fib retracement from swing_high to swing_low
            fib_range = swing_high_h4 - swing_low_h4
            fib_786  = swing_high_h4 - (fib_range * 0.786)  # Layer 1 – 20%
            pdl_val  = liquidity.get('PDL', swing_low_h4)   # Layer 2 – 30%
            pwl_val  = liquidity.get('PWL', swing_low_h4 * 0.97)  # Layer 3 – 50%

            battle_plan = {
                "market_phase": market_phase,      # MARKUP | MARKDOWN
                "atr_h4": round(atr_h4, 8),
                "swing_low": round(swing_low_h4, 8),
                "swing_high": round(swing_high_h4, 8),
                "structural_sl": round(structural_sl, 8),  # SL = Swing Low - 2×ATR
                "tp1_val": round(tp1_val, 8),              # Nearest EMA above / PDH
                "tp1_label": tp1_name,
                "tp2_val": round(tp2_val, 8),              # PDH
                "tp3_val": round(tp3_val, 8),              # PWH
                "layer1_val": round(fib_786, 8),           # 20% – Fib 0.786
                "layer2_val": round(pdl_val, 8),           # 30% – PDL
                "layer3_val": round(pwl_val, 8),           # 50% – PWL
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
        entry_price = get_entry_price(coin_pair)
        allocated_capital = get_allocated_capital(coin_pair)

        pnl_pct = 0.0
        if entry_price > 0:
            pnl_pct = round(((current_price - entry_price) / entry_price) * 100, 4)  # type: ignore[call-overload]

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
            },
            "alerts_sent": coin_state["alerts_sent"],
        }

        logger.info("✅ Dashboard data ready!")
        return jsonify({
            "success": True,
            "timestamp": now_str,
            "raw_data": raw_data,
            "oi_data": oi_formatted,
            "computed": computed,
            "state": state,
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
    Body JSON: { "symbol": "SUIUSDT", "entry_price": 1.055, "qty": 190.47, "allocated_capital": 200 }
    Jika qty tidak disediakan, dihitung otomatis dari allocated_capital / entry_price.
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

        allocated_capital = float(data.get("allocated_capital", ALLOCATED_CAPITAL))
        qty = float(data.get("qty", 0))
        if qty <= 0:
            qty = allocated_capital / entry_price  # auto-calc qty

        entries = load_trade_entries()
        if symbol not in entries:
            entries[symbol] = {'entries': [], 'allocated_capital': allocated_capital}

        # Tambah entry baru ke list
        new_entry = {
            'price': entry_price,
            'qty': round(qty, 6),
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        entries[symbol]['entries'].append(new_entry)
        entries[symbol]['allocated_capital'] = allocated_capital
        save_trade_entries(entries)

        summary = get_entry_summary(symbol)
        logger.info(f"💰 Entry added: {symbol} @ ${entry_price} x {qty:.4f} (Avg: ${summary['avg_price']})")
        return jsonify({
            "success": True,
            "message": f"{symbol} entry #{summary['num_entries']} added",
            "summary": summary,
        })
    except Exception as e:
        logger.error(f"Save entry error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/trade-entries/delete", methods=["POST"])
def delete_trade_entry():
    """Hapus SEMUA entry untuk satu koin, atau hapus entry spesifik by index."""
    try:
        data = flask_request.get_json()  # type: ignore
        symbol = data.get("symbol", "").upper() if data else ""
        if not symbol:
            return jsonify({"success": False, "error": "No symbol provided"}), 400

        index = data.get("index", None)  # Optional: hapus entry spesifik

        entries = load_trade_entries()
        if symbol in entries:
            if index is not None and isinstance(entries[symbol].get('entries'), list):
                idx = int(index)
                if 0 <= idx < len(entries[symbol]['entries']):
                    removed = entries[symbol]['entries'].pop(idx)
                    logger.info(f"🗑️ Entry #{idx} deleted for {symbol}: ${removed['price']}")
                else:
                    return jsonify({"success": False, "error": f"Index {idx} out of range"}), 400
            else:
                del entries[symbol]  # type: ignore[attr-defined]
                logger.info(f"🗑️ All entries deleted: {symbol}")
            save_trade_entries(entries)

        return jsonify({"success": True, "message": f"{symbol} entry deleted"})
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
            df = pd.merge(df, df_btcd_slim, on='Open_Time', how='left')
        else:
            df['BTC_Dominance'] = None

        df_defi = get_klines_fapi("1000DEFIUSDT", interval, limit=limit) # DEFI index mapping
        if df_defi.empty: df_defi = get_klines_fapi("DEFIUSDT", interval, limit=limit)
        if not df_defi.empty:
            df_defi_slim = df_defi[['Open_Time', 'Close']].rename(columns={'Close': 'Altcoin_Index'})
            df = pd.merge(df, df_defi_slim, on='Open_Time', how='left')
        else:
            df['Altcoin_Index'] = None
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
# 🚀 MAIN
# ==========================================
if __name__ == "__main__":
    logger.info("🖥️  Protocol 9.6 Dashboard starting on http://127.0.0.1:5000")
    app.run(debug=True, port=5000, threaded=True)
