"""
Protocol 9.6: Structural Guardian — Dashboard UI Server
Flask backend serving all raw, computed, and state data for the web dashboard.
"""
import time
import logging
import requests as http_requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
import io
from flask import Flask, render_template, jsonify, Response, request as flask_request
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
import protocol_96_enrichment as enrichment

# ==========================================
# ⚙️ USER CONFIGURATION
# ==========================================
COIN_PAIR = "SUIUSDT"
ENTRY_PRICE = 1.055
ALLOCATED_CAPITAL = 200

BINANCE_API_KEY = ""
BINANCE_API_SECRET = ""

# ==========================================
# Flask App & Logger
# ==========================================
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Protocol_9.6_UI")

# Binance Client
binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# ==========================================
# Bot State (in-memory)
# ==========================================
class BotState:
    INITIALIZED = False
    ACTIVE_SL = 0.0
    INITIAL_SL = 0.0
    STATUS = "ACTIVE"
    ALERTS_SENT = {
        "TP_1": False,
        "SL_BE": False,
        "VOL_FAKEOUT": False,
        "KILL_SWITCH": False
    }

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


def apply_full_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Apply EMA, RSI, and StochRSI indicators."""
    if df.empty:
        return df

    df['EMA_7'] = ta.ema(df['Close'], length=7)
    df['EMA_21'] = ta.ema(df['Close'], length=21)
    df['EMA_50'] = ta.ema(df['Close'], length=50)
    df['EMA_200'] = ta.ema(df['Close'], length=200)

    df['RSI_6'] = ta.rsi(df['Close'], length=6)

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


def fetch_oi_data(limit: int = 4) -> list:
    """Fetch Open Interest history from Binance Futures."""
    try:
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        params = {"symbol": COIN_PAIR, "period": "15m", "limit": limit}
        resp = http_requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"OI fetch error: {e}")
    return []


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
            "open": round(float(row['Open']), 6),
            "high": round(float(row['High']), 6),
            "low": round(float(row['Low']), 6),
            "close": round(float(row['Close']), 6),
            "total_vol": round(float(row['Total_Volume']), 2),
            "buy_vol": round(float(row['Buy_Volume']), 2),
            "sell_vol": round(float(row['Sell_Volume']), 2),
            "vol_delta": round(float(row['Volume_Delta']), 2),
        }
        # Add indicators if present
        for col in ['EMA_7', 'EMA_21', 'EMA_50', 'EMA_200', 'RSI_6', 'StochRSI_K', 'StochRSI_D']:
            if col in row.index and pd.notna(row[col]):
                entry[col.lower()] = round(float(row[col]), 4)
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
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info("📡 Fetching dashboard data from Binance...")

        # ── Section 1: Raw API Data ──
        # Only fetch the last 20 candles for raw display (lighter requests)
        raw_data = {}
        for label, interval in INTERVAL_MAP.items():
            logger.info(f"  Fetching {COIN_PAIR} {label}...")
            df_coin = get_klines_df(COIN_PAIR, interval, limit=20)
            logger.info(f"  Fetching BTCUSDT {label}...")
            df_btc = get_klines_df("BTCUSDT", interval, limit=20)
            raw_data[label] = {
                "coin": format_ohlcv_for_json(df_coin, last_n=10),
                "btc": format_ohlcv_for_json(df_btc, last_n=10),
            }
            time.sleep(0.1)  # Gentle rate limit

        # Futures OI Data
        logger.info("  Fetching OI data...")
        oi_raw = fetch_oi_data()
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
        df_1h = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1HOUR, limit=250)
        df_1h = apply_full_indicators(df_1h)

        logger.info("  Computing H4 indicators...")
        df_4h = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_4HOUR, limit=250)
        df_4h = apply_full_indicators(df_4h)

        computed = {
            "indicators_1h": format_ohlcv_for_json(df_1h, last_n=10),
            "indicators_4h": format_ohlcv_for_json(df_4h, last_n=10),
        }

        # Liquidity Borders
        logger.info("  Fetching liquidity borders...")
        df_1d = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1DAY, limit=3)
        df_1w = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1WEEK, limit=3)

        liquidity = {"PDH": 0.0, "PDL": 0.0, "PWH": 0.0, "PWL": 0.0}
        if len(df_1d) >= 2:
            liquidity["PDH"] = round(float(df_1d.iloc[-2]['High']), 6)
            liquidity["PDL"] = round(float(df_1d.iloc[-2]['Low']), 6)
        if len(df_1w) >= 2:
            liquidity["PWH"] = round(float(df_1w.iloc[-2]['High']), 6)
            liquidity["PWL"] = round(float(df_1w.iloc[-2]['Low']), 6)

        computed["liquidity_borders"] = liquidity

        # SMT Divergence
        logger.info("  Computing SMT divergence...")
        df_btc_h4 = get_klines_df("BTCUSDT", Client.KLINE_INTERVAL_4HOUR, limit=5)
        df_tgt_h4 = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_4HOUR, limit=5)

        smt = {"btc_trend_12h": "N/A", "coin_trend_12h": "N/A", "bearish_smt": False}
        if not df_btc_h4.empty and not df_tgt_h4.empty and len(df_btc_h4) >= 3 and len(df_tgt_h4) >= 3:
            btc_highs = df_btc_h4['High'].iloc[-3:].values
            tgt_highs = df_tgt_h4['High'].iloc[-3:].values
            btc_hh = btc_highs[2] > btc_highs[1] > btc_highs[0]
            tgt_lh = tgt_highs[2] < tgt_highs[1]
            smt["btc_trend_12h"] = "Higher High ↑" if btc_hh else "No HH"
            smt["coin_trend_12h"] = "Lower High ↓" if tgt_lh else "No LH"
            smt["bearish_smt"] = bool(btc_hh and tgt_lh)
        computed["smt_divergence"] = smt

        # OI Delta
        oi_delta_pct = 0.0
        if len(oi_raw) >= 2:
            old_oi = float(oi_raw[0].get('sumOpenInterest', 1))
            new_oi = float(oi_raw[-1].get('sumOpenInterest', 1))
            if old_oi > 0:
                oi_delta_pct = round(((new_oi - old_oi) / old_oi) * 100, 4)
        computed["oi_delta_pct"] = oi_delta_pct

        # ── Section 3: User & System State ──
        current_price = 0.0
        if not df_1h.empty:
            current_price = float(df_1h.iloc[-1]['Close'])

        # Initialize bot state
        if not BotState.INITIALIZED and not df_4h.empty:
            ema21_val = df_4h.iloc[-1].get('EMA_21')
            ema21_4h = float(ema21_val) if pd.notna(ema21_val) else current_price
            BotState.ACTIVE_SL = round(ema21_4h * 0.99, 6)
            BotState.INITIAL_SL = BotState.ACTIVE_SL
            BotState.INITIALIZED = True

        pnl_pct = 0.0
        if ENTRY_PRICE > 0:
            pnl_pct = round(((current_price - ENTRY_PRICE) / ENTRY_PRICE) * 100, 4)

        # Check for kill switch trigger
        if not df_4h.empty and len(df_4h) >= 2:
            ema21_check = df_4h.iloc[-2].get('EMA_21')
            if pd.notna(ema21_check):
                if float(df_4h.iloc[-2]['Close']) < float(ema21_check):
                    BotState.STATUS = "KILL_SWITCH"

        state = {
            "user_input": {
                "coin_pair": COIN_PAIR,
                "entry_price": ENTRY_PRICE,
                "allocated_capital": ALLOCATED_CAPITAL,
                "status": BotState.STATUS,
            },
            "active_tracker": {
                "current_price": round(current_price, 6),
                "initial_sl": round(BotState.INITIAL_SL, 6),
                "active_sl": round(BotState.ACTIVE_SL, 6),
                "current_pnl_pct": pnl_pct,
            },
            "alerts_sent": BotState.ALERTS_SENT,
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
# 📥 EXPORT HELPER
# ==========================================
def build_export_dataframe(timeframe: str = "1h", limit: int = 250) -> pd.DataFrame:
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
    logger.info(f"  [Export] Fetching {COIN_PAIR} {timeframe} ({limit} candles)...")
    df = get_klines_df(COIN_PAIR, interval, limit=limit)
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
    oi_raw = fetch_oi_data(limit=limit)

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
        df_h4 = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_4HOUR, limit=h4_limit)
        df_d1 = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1DAY, limit=10)
        df_w1 = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1WEEK, limit=10)
        
        # Enrich dataset
        df = enrichment.enrich_dataset(df, df_h4, df_d1, df_w1)
    except Exception as e:
        logger.error(f"Enrichment error: {e}")
        import traceback
        traceback.print_exc()

    # ── Pilih dan urutkan kolom sesuai spesifikasi ──
    col_order = [
        'Timestamp',
        'Market_Session',
        'Open', 'High', 'Low', 'Close',
        'Total_Volume',
        'Buy_Volume',
        'Sell_Volume',
        'EMA_7', 'EMA_21', 'EMA_50', 'EMA_200',
        'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4',
        'RSI_6',
        'StochRSI_K', 'StochRSI_D',
        'ATR_14', 'ATR_14_H4',
        'PDH', 'PDL', 'PWH', 'PWL',
        'FVG_Up_Top', 'FVG_Up_Bottom', 'FVG_Down_Top', 'FVG_Down_Bottom',
        'OB_Price', 'SFP_Sweep',
        'Fib_0.618', 'Fib_0.786',
        'Open_Interest',
        'BTC_Price',
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

    df_export = df[col_order].copy()

    # Bulatkan float ke 6 desimal agar file rapi
    float_cols = [
        'Open', 'High', 'Low', 'Close', 'Total_Volume',
        'Buy_Volume', 'Sell_Volume',
        'EMA_7', 'EMA_21', 'EMA_50', 'EMA_200',
        'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4',
        'RSI_6', 'StochRSI_K', 'StochRSI_D',
        'ATR_14', 'ATR_14_H4',
        'PDH', 'PDL', 'PWH', 'PWL',
        'FVG_Up_Top', 'FVG_Up_Bottom', 'FVG_Down_Top', 'FVG_Down_Bottom',
        'OB_Price',
        'Fib_0.618', 'Fib_0.786',
        'Open_Interest', 'BTC_Price',
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
    """
    Export data Protocol 9.6 ke format CSV.
    Query params:
      ?tf=1h    — timeframe (15m/1h/4h/1d/1w), default: 1h
      ?limit=250 — jumlah candle, default: 250
    """
    timeframe = flask_request.args.get('tf', '1h')
    limit     = int(flask_request.args.get('limit', 250))

    try:
        logger.info(f"📥 Export CSV requested: {COIN_PAIR} {timeframe} {limit} candles")
        df_export = build_export_dataframe(timeframe=timeframe, limit=limit)

        if df_export.empty:
            return jsonify({"success": False, "error": "No data available"}), 500

        # ── Build dynamic filename ──
        # Format: Data_Track_9.6_{COIN_PAIR}_{YYYYMMDD_HHmm}.csv
        now = datetime.now()
        ts_str = now.strftime('%Y%m%d_%H%M')
        filename = f"Data_Track_9.6_{COIN_PAIR}_{timeframe.upper()}_{ts_str}.csv"

        # ── Tulis ke in-memory buffer (tanpa index baris) ──
        buf = io.StringIO()
        df_export.to_csv(buf, index=False, encoding='utf-8')
        buf.seek(0)

        logger.info(f"✅ CSV ready: {filename} ({len(df_export)} rows)")

        # ── Kirim ke browser dengan header yang benar ──
        return Response(
            buf.getvalue(),
            mimetype='text/csv',
            headers={
                # Content-Disposition: attachment memaksa browser download (bukan buka inline)
                # filename= memastikan nama yang informatif, bukan UUID
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
    """
    Export data Protocol 9.6 ke format Excel (.xlsx).
    Query params:
      ?tf=1h    — timeframe (15m/1h/4h/1d/1w), default: 1h
      ?limit=250 — jumlah candle, default: 250
    """
    timeframe = flask_request.args.get('tf', '1h')
    limit     = int(flask_request.args.get('limit', 250))

    try:
        logger.info(f"📥 Export Excel requested: {COIN_PAIR} {timeframe} {limit} candles")
        df_export = build_export_dataframe(timeframe=timeframe, limit=limit)

        if df_export.empty:
            return jsonify({"success": False, "error": "No data available"}), 500

        now = datetime.now()
        ts_str = now.strftime('%Y%m%d_%H%M')
        filename = f"Data_Track_9.6_{COIN_PAIR}_{timeframe.upper()}_{ts_str}.xlsx"

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
    app.run(debug=False, port=5000, threaded=True)
