import time
import logging
import requests  # type: ignore
import schedule  # type: ignore
import json
import os
import pandas as pd  # type: ignore
import pandas_ta as ta  # type: ignore
from datetime import datetime
from binance.client import Client  # type: ignore
from binance.exceptions import BinanceAPIException, BinanceRequestException  # type: ignore

# Pengaturan Sesi & Lokasi Data
# Railway: set env var TRADE_DATA_PATH=/app/data/trade_entries.json dan mount Volume ke /app/data
_default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_entries.json')
TRADE_ENTRIES_FILE = os.environ.get('TRADE_DATA_PATH', _default_path)
# Auto-create direktori jika belum ada (penting untuk Railway Volume)
os.makedirs(os.path.dirname(os.path.abspath(TRADE_ENTRIES_FILE)), exist_ok=True)
# Default pair if JSON is empty (for bootstrapping)
DEFAULT_PAIR = "SUIUSDT"

# Pengaturan Telegram
TELEGRAM_BOT_TOKEN = "8728046864:AAEaLD5c1yJRuTjoNKRLbyzkBII2AJKV9hE"
TELEGRAM_CHAT_ID = "982913105"

# Binance API Keys
BINANCE_API_KEY = ""
BINANCE_API_SECRET = ""

# ==========================================
# 📊 BOT INTERNAL STATE
# ==========================================
class BotState:
    INITIALIZED = {}  # {symbol: bool}
    ACTIVE_SL = {}   # {symbol: float}
    ALERTS_SENT = {} # {symbol: {alert_name: bool}}
    BATTLE_PLAN = {} # {symbol: {...}}
    
    @classmethod
    def get_state(cls, symbol):
        if symbol not in cls.INITIALIZED:
            cls.INITIALIZED[symbol] = False
            cls.ACTIVE_SL[symbol] = 0.0
            cls.ALERTS_SENT[symbol] = {
                "TP_1": False, "TP_2": False, "TP_3": False,
                "PROXIMITY_TP1": False, "PROXIMITY_TP2": False, "PROXIMITY_TP3": False,
                "LAYER_1": False, "LAYER_2": False, "LAYER_3": False,
                "PROXIMITY_LAYER1": False, "PROXIMITY_LAYER2": False, "PROXIMITY_LAYER3": False,
                "PROXIMITY_SL": False,
                "SL_BE": False,
                "VOL_FAKEOUT": False,
                "KILL_SWITCH": False
            }
            cls.BATTLE_PLAN[symbol] = {}
        return {
            "init": cls.INITIALIZED[symbol],
            "sl": cls.ACTIVE_SL[symbol],
            "alerts": cls.ALERTS_SENT[symbol]
        }

# Setup Logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Protocol_9.6")

# Inisialisasi Binance Client
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# ==========================================
# 📂 DATA PERSISTENCE HELPERS
# ==========================================
def load_trade_entries() -> dict:
    if os.path.exists(TRADE_ENTRIES_FILE):
        try:
            with open(TRADE_ENTRIES_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load trade entries: {e}")
    return {}

def get_entry_summary(symbol: str) -> dict:
    entries = load_trade_entries()
    coin_data = entries.get(symbol, {})
    entry_list = coin_data.get('entries', [])
    if not entry_list:
        # Support legacy single entry format
        legacy_price = coin_data.get('entry_price', 0.0)
        return {
            'avg_price': legacy_price, 
            'num_entries': 1 if legacy_price > 0 else 0,
            'allocated_capital': coin_data.get('allocated_capital', 200)
        }
    total_cost = sum(e['price'] * e['qty'] for e in entry_list)
    total_qty = sum(e['qty'] for e in entry_list)
    return {
        'avg_price': total_cost / total_qty if total_qty > 0 else 0.0,
        'num_entries': len(entry_list),
        'allocated_capital': coin_data.get('allocated_capital', 200)
    }

# ==========================================
# 🛠️ HELPER FUNCTIONS
# ==========================================
def send_telegram_alert(message: str):
    """Mengirim pesan notifikasi ke Telegram dengan penanganan error."""
    if TELEGRAM_BOT_TOKEN == "your_bot_token" or not TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram tidak dikonfigurasi. Mengabaikan pengiriman pesan.")
        logger.info(f"PESAN DRAF:\n{message}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("✅ Telegram alert sent successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram alert: {e}")

def get_klines_df(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    """Mengambil OHLCV data dari Binance dan memproses struktur volume Institutional Flow."""
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'Open_Time', 'Open', 'High', 'Low', 'Close', 'Total_Volume',
            'Close_Time', 'Quote_Asset_Volume', 'Trades', 'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
        ])
        
        # Ekstraksi nilai sebagai float
        df['Open'] = df['Open'].astype(float)
        df['High'] = df['High'].astype(float)
        df['Low'] = df['Low'].astype(float)
        df['Close'] = df['Close'].astype(float)
        df['Total_Volume'] = df['Total_Volume'].astype(float)
        df['Taker_Buy_Base'] = df['Taker_Buy_Base'].astype(float) # index 9
        
        # Kalkulasi Volume Transaksi
        df['Buy_Volume'] = df['Taker_Buy_Base']
        df['Sell_Volume'] = df['Total_Volume'] - df['Buy_Volume']

        # ── TEMPORAL ALIGNMENT (UTC+8) ──
        df['Open_Time'] = pd.to_datetime(df['Open_Time'], unit='ms') + pd.to_timedelta(8, unit='h')
        df['Close_Time'] = pd.to_datetime(df['Close_Time'], unit='ms') + pd.to_timedelta(8, unit='h')
        
        # Market Session Mapping
        def get_session(dt):
            h = dt.hour
            sessions = []
            if 7 <= h < 15: sessions.append("ASIAN")
            if 15 <= h < 23: sessions.append("LONDON")
            if h >= 20 or h < 4: sessions.append("NEW YORK")
            return " / ".join(sessions) if sessions else "OFF-MARKET"
        
        df['Market_Session'] = df['Open_Time'].apply(get_session)
        
        return df
    except Exception as e:
        logger.error(f"Error fetching klines for {interval}: {e}")
        return pd.DataFrame()

def apply_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Menambahkan indikator EMA, RSI, dan ATR menggunakan pandas_ta."""
    if df.empty: return df
    
    # Structural Indicators
    df['EMA_7'] = ta.ema(df['Close'], length=7)
    df['EMA_21'] = ta.ema(df['Close'], length=21)
    df['EMA_50'] = ta.ema(df['Close'], length=50)
    df['EMA_200'] = ta.ema(df['Close'], length=200)
    df['RSI_6'] = ta.rsi(df['Close'], length=6)
    
    # ATR for Structural SL
    df['ATR_14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    
    return df

def check_intermarket_macro(symbol: str) -> dict:
    """Akses batas makro D1/W1, Open Interest (OI) Delta, dan SMT Divergence."""
    res = {"PDH": 0.0, "PDL": 0.0, "PWH": 0.0, "PWL": 0.0, "Delta_OI": 0.0, "Bearish_SMT": False, "BTC_Dom_Change": 0.0}
    
    try:
        # Macro Liquidity Borders (D1 / W1)
        df_1d = get_klines_df(symbol, Client.KLINE_INTERVAL_1DAY, limit=2)
        df_1w = get_klines_df(symbol, Client.KLINE_INTERVAL_1WEEK, limit=2)
        
        if len(df_1d) >= 2: 
            res["PDH"], res["PDL"] = df_1d.iloc[-2]['High'], df_1d.iloc[-2]['Low']
        if len(df_1w) >= 2: 
            res["PWH"], res["PWL"] = df_1w.iloc[-2]['High'], df_1w.iloc[-2]['Low']
            
        # Delta Open Interest (M15 Futures API) - 4 last candles
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        params = {"symbol": symbol, "period": "15m", "limit": 4}
        oi_resp = requests.get(url, params=params, timeout=10)
        if oi_resp.status_code == 200:
            data = oi_resp.json()
            if len(data) >= 2:
                old_oi = float(data[0]['sumOpenInterest'])
                new_oi = float(data[-1]['sumOpenInterest'])
                res["Delta_OI"] = ((new_oi - old_oi) / old_oi) * 100

        # SMT Divergence (Korelasi BTC) H4
        df_btc = get_klines_df("BTCUSDT", Client.KLINE_INTERVAL_4HOUR, limit=4)
        df_tgt = get_klines_df(symbol, Client.KLINE_INTERVAL_4HOUR, limit=4)
        
        if not df_btc.empty and not df_tgt.empty:
            btc_highs = df_btc['High'].iloc[-4:-1].values
            tgt_highs = df_tgt['High'].iloc[-4:-1].values
            if len(btc_highs) == 3 and len(tgt_highs) == 3:
                btc_is_hh = btc_highs[2] > btc_highs[1] > btc_highs[0]
                tgt_is_lh = tgt_highs[2] < tgt_highs[1]
                res["Bearish_SMT"] = bool(btc_is_hh and tgt_is_lh)

        # BTC Dominance Siphon Check
        fapi_url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        btcd_resp = requests.get(fapi_url, params={"symbol": "BTCDOMUSDT"}, timeout=5)
        if btcd_resp.status_code == 200:
            res["BTC_Dom_Change"] = float(btcd_resp.json().get('priceChangePercent', 0))

    except Exception as e:
        logger.warning(f"Error processing Intermarket Logic: {e}")
        
    return res

# ==========================================
# 🧠 CORE EVALUATION LOGIC
# ==========================================
def evaluate_market_conditions(symbol: str):
    """Fungsi utama yang memanggil siklus evaluasi setiap candle target."""
    try:
        # 1. Tarik Struktur Data Frame (Swing Macro Adjustments: H1, H4, D1)
        df_1h = get_klines_df(symbol, Client.KLINE_INTERVAL_1HOUR, limit=250)
        df_4h = get_klines_df(symbol, Client.KLINE_INTERVAL_4HOUR, limit=500)
        df_1d = get_klines_df(symbol, Client.KLINE_INTERVAL_1DAY, limit=100)
        
        if df_1h.empty or df_4h.empty or df_1d.empty:
            return

        # 2. Kalkulasi Indikator
        df_1h = apply_indicators(df_1h)
        df_4h = apply_indicators(df_4h)
        
        current_price = df_1h.iloc[-1]['Close']
        current_ema21_4h = df_4h.iloc[-1]['EMA_21']
        
        summary = get_entry_summary(symbol)
        avg_entry = summary['avg_price']
        
        # Get/Init persistence for this symbol
        BotState.get_state(symbol) 
        
        # 3. Inisialisasi Bootstrapping Protocol
        if not BotState.INITIALIZED[symbol]:
            BotState.ACTIVE_SL[symbol] = current_ema21_4h * 0.99
            BotState.INITIALIZED[symbol] = True
            
            logger.info(f"=== 🏗️ PROTOCOL 9.6: [{symbol}] GUARDIAN INITIALIZED ===")
            logger.info(f"Entry Price: ${avg_entry}")
            logger.info(f"Initial Active SL: ${BotState.ACTIVE_SL[symbol]:.4f} (EMA 21 H4 - 1%)")
            logger.info(f"Current EMA 21 (H4): ${current_ema21_4h:.4f}")
            logger.info(f"Current EMA 200 (H4): ${df_4h.iloc[-1]['EMA_200']:.4f}")
            logger.info("=========================================================")

        # 4. Ambil Intermarket Macro State
        macro_state = check_intermarket_macro(symbol)
        
        # ── Build Tactical Battle Plan (Protocol 9.6) ──
        last_h4 = df_4h.iloc[-1]
        atr_h4 = float(last_h4['ATR_14']) if pd.notna(last_h4['ATR_14']) else 0.0
        swing_low_h4 = float(df_4h['Low'].iloc[-20:].min())
        swing_high_h4 = float(df_4h['High'].iloc[-20:].max())
        
        # SL = Swing Low - 2xATR
        structural_sl = swing_low_h4 - (2.0 * atr_h4) if atr_h4 > 0 else swing_low_h4 * 0.985
        
        # TPs: collect ALL levels above current price, sort ascending, assign TP1/2/3 in order
        tp_candidates = []
        for col in ['EMA_7', 'EMA_21', 'EMA_50', 'EMA_200']:
            if pd.notna(last_h4.get(col)):
                val = float(last_h4[col])
                if val > price_h4:
                    tp_candidates.append((col, val))
        pdh = macro_state.get('PDH', 0.0)
        pwh = macro_state.get('PWH', 0.0)
        if pdh and pdh > price_h4:
            tp_candidates.append(('PDH', pdh))
        if pwh and pwh > price_h4:
            tp_candidates.append(('PWH', pwh))
        
        # Sort ascending and deduplicate by label
        tp_candidates.sort(key=lambda x: x[1])
        seen: dict = {}
        deduped = []
        for lbl, v in tp_candidates:
            if lbl not in seen:
                seen[lbl] = v
                deduped.append((lbl, v))
        tp_candidates = deduped
        
        tp1_val = tp_candidates[0][1] if len(tp_candidates) >= 1 else price_h4 * 1.06
        tp2_val = tp_candidates[1][1] if len(tp_candidates) >= 2 else tp1_val * 1.04
        tp3_val = tp_candidates[2][1] if len(tp_candidates) >= 3 else tp2_val * 1.08
        
        # Layers (Safety Net)
        fib_range = swing_high_h4 - swing_low_h4
        layer1_val = swing_high_h4 - (fib_range * 0.786)
        layer2_val = macro_state.get('PDL', swing_low_h4)
        layer3_val = macro_state.get('PWL', swing_low_h4 * 0.97)
        
        BotState.BATTLE_PLAN[symbol] = {
            "TP1": tp1_val, "TP2": tp2_val, "TP3": tp3_val,
            "L1": layer1_val, "L2": layer2_val, "L3": layer3_val,
            "SL": structural_sl
        }

        # =========================================================
        # 🚨 PROXIMITY ALERTS (Toleransi 1%)
        # =========================================================
        TOLERANCE = 0.01  # 1%
        targets = [
            ("TP1", tp1_val, "PROXIMITY_TP1", "🎯 Mendekati TP 1"),
            ("TP2", tp2_val, "PROXIMITY_TP2", "🎯 Mendekati TP 2"),
            ("TP3", tp3_val, "PROXIMITY_TP3", "🎯 Mendekati TP 3"),
            ("L1", layer1_val, "PROXIMITY_LAYER1", "🪤 Mendekati Layer 1 (Safety Net)"),
            ("L2", layer2_val, "PROXIMITY_LAYER2", "🪤 Mendekati Layer 2 (Macro Support)"),
            ("L3", layer3_val, "PROXIMITY_LAYER3", "🪤 Mendekati Layer 3 (Extreme Wick)"),
            ("SL", structural_sl, "PROXIMITY_SL", "🛑 Mendekati Structural SL"),
        ]
        
        for name, val, flag, msg_prefix in targets:
            if not val or val == 0: continue
            dist = abs(current_price - val) / val
            if dist <= TOLERANCE:
                if not BotState.ALERTS_SENT[symbol][flag]:
                    msg = (
                        f"🔔 <b>PROXIMITY ALERT:</b> {symbol}\n"
                        f"{msg_prefix} — Jarak: {dist*100:.2f}%\n"
                        f"Current: ${current_price:.6f} | Target: ${val:.6f}\n"
                        f"💡 <b>Saran:</b> Monitor order book / siapkan eksekusi."
                    )
                    send_telegram_alert(msg)
                    BotState.ALERTS_SENT[symbol][flag] = True
            else:
                # Reset proximity flag if price moves away (> 2%) to re-alert later if it re-enters
                if dist > 0.02:
                    BotState.ALERTS_SENT[symbol][flag] = False

        # =========================================================
        # 🚨 TRIGGER 1: THE PROFIT GUARDIAN (Take Profit Parsial)
        # =========================================================
        rsi_1h = df_1h.iloc[-1]['RSI_6']
        rsi_4h = df_4h.iloc[-1]['RSI_6']
        
        is_momentum_overheated = (rsi_1h > 85) or (rsi_4h > 85)
        is_touching_macro = (current_price >= macro_state["PDH"] and macro_state["PDH"] > 0) or \
                            (current_price >= macro_state["PWH"] and macro_state["PWH"] > 0)
        
        if (is_momentum_overheated or is_touching_macro) and not BotState.ALERTS_SENT[symbol]["TP_1"]:
            rsi_val = rsi_1h if rsi_1h > 85 else rsi_4h
            msg = (
                f"🟢 <b>TACTICAL TP ALERT:</b> {symbol}\n"
                f"Momentum Overheated! RSI H1/H4: {rsi_val:.2f}. Harga: ${current_price:.4f}.\n"
                f"🎯 <b>Instruksi:</b> Jual 30% posisi sekarang. Profit diamankan!"
            )
            send_telegram_alert(msg)
            BotState.ALERTS_SENT[symbol]["TP_1"] = True

        # =========================================================
        # 🚨 TRIGGER 2: STRUCTURAL SL & BREAK-EVEN PROTECTOR
        # =========================================================
        sl_msg = ""
        # Break-even logic (if avg_entry exists)
        if avg_entry > 0:
            if current_price > (avg_entry * 1.03) and BotState.ACTIVE_SL[symbol] < avg_entry:
                BotState.ACTIVE_SL[symbol] = avg_entry * 1.005 # Breakeven +0.5% buffer
                sl_msg = f"Posisi profit +3%. Break-Even Terkunci."
        
        # Update using Structural SL
        if structural_sl > BotState.ACTIVE_SL[symbol]:
            BotState.ACTIVE_SL[symbol] = structural_sl
            if BotState.INITIALIZED[symbol]: # Only ping SL update if not the first init
                sl_msg = f"Structural SL diperbarui (Swing Low - 2xATR)."

        if sl_msg and not BotState.ALERTS_SENT[symbol]["SL_BE"]:
            msg = (
                f"🛡️ <b>SL UPDATED:</b> {symbol}\n"
                f"{sl_msg} Stop Loss dinaikkan ke ${BotState.ACTIVE_SL[symbol]:.4f}. Modal 100% aman!"
            )
            send_telegram_alert(msg)
            BotState.ALERTS_SENT[symbol]["SL_BE"] = True 

        # =========================================================
        # 🚨 TRIGGER 3: VOLUME FAKEOUT / DISTRIBUSI
        # =========================================================
        # Evaluasi 3 candle *CLOSED* terakhir H1 (Index -4 ke -2, krn -1 adalah current active)
        last_3h_klines = df_1h.iloc[-4:-1]
        vol_3h_sell = last_3h_klines['Sell_Volume'].sum()
        vol_3h_buy = last_3h_klines['Buy_Volume'].sum()
        
        # Validasi Higher High (Dibandingkan candle H1 ke-3 kebelakang)
        highest_price_3_candles_ago = df_1h.iloc[-4]['High']
        no_higher_high = current_price < highest_price_3_candles_ago
        
        if (vol_3h_sell > vol_3h_buy) and no_higher_high and not BotState.ALERTS_SENT[symbol]["VOL_FAKEOUT"]:
            msg = (
                f"⚠️ <b>VOLUME DANGER:</b> {symbol}\n"
                f"Heavy Distribution terdeteksi. 3 Candle terakhir didominasi Sell Volume institusi (Fakeout).\n"
                f"🔪 <b>Instruksi:</b> Exit Immediate / Perketat SL sekarang!"
            )
            send_telegram_alert(msg)
            BotState.ALERTS_SENT[symbol]["VOL_FAKEOUT"] = True
            
        # Reset Volume Fakeout logic if buying resumes
        if vol_3h_buy > vol_3h_sell:
            BotState.ALERTS_SENT[symbol]["VOL_FAKEOUT"] = False

        # =========================================================
        # 🚨 TRIGGER 4: THE KILL SWITCH (Darurat)
        # =========================================================
        # Kondisi A: Candle H4 CLOSED di bawah EMA_21 H4 (Ambil indeks -2 karena tutup penuh)
        last_closed_h4 = df_4h.iloc[-2]
        ema_kill_switch = last_closed_h4['Close'] < last_closed_h4['EMA_21']
        
        # Kondisi B: BTC Dominance Siphon (Altcoin Filter Siphon)
        ticker_tgt = client.get_ticker(symbol=symbol)
        tgt_change_24h = float(ticker_tgt['priceChangePercent'])
        
        # Siphon terjadi jika BTC Dominance naik tinggi tapi Altcoin hancur (Liquidity dialirkan ke BTC doang)
        btc_siphon = (macro_state.get("BTC_Dom_Change", 0.0) >= 1.5) and (tgt_change_24h <= -1.0)
        
        if (ema_kill_switch or btc_siphon) and not BotState.ALERTS_SENT[symbol]["KILL_SWITCH"]:
            msg = f"💀 <b>KILL SWITCH ACTIVATED:</b> {symbol}\n"
            
            if ema_kill_switch:
                msg += f"Structural Breakdown! H4 Close di bawah EMA 21 (${last_closed_h4['EMA_21']:.4f}).\n"
            else:
                msg += f"Korelasi Berbahaya (BTC.D Siphon terdeteksi. BTC.DOM naik {macro_state.get('BTC_Dom_Change', 0):.2f}%).\n"
                
            msg += f"❌ <b>Instruksi:</b> BUANG SEMUA POSISI. Tren Bullish resmi batal."
            
            send_telegram_alert(msg)
            BotState.ALERTS_SENT[symbol]["KILL_SWITCH"] = True

    except (BinanceAPIException, BinanceRequestException) as bae:
        logger.error(f"Binance API Network Error: {bae}")
    except Exception as e:
        logger.error(f"Kalkulasi Tracker Gagal untuk {symbol}: {e}")

def run_cycle():
    """Mengambil semua coin aktif dan menjalankan evaluasi."""
    entries = load_trade_entries()
    active_symbols = list(entries.keys())
    
    if not active_symbols:
        # Fallback to default if no entries saved yet
        active_symbols = [DEFAULT_PAIR]
        
    logger.info(f"🔄 Menjalankan siklus evaluasi untuk {len(active_symbols)} koin: {active_symbols}")
    for symbol in active_symbols:
        evaluate_market_conditions(symbol)

# ==========================================
# ⏱️ SCHEDULER & MAIN LOOP
# ==========================================
def main():
    logger.info("Bot siap dijalankan. Memulai Multi-Coin Tracker...")
    run_cycle() # Jalankan pertama kali segera setelah distart

    # Jalankan pemeriksaan setiap 1 menit mengikuti standar responsivitas Protocol
    schedule.every(1).minutes.do(run_cycle)
    
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Tracker dihentikan secara manual oleh pengguna.")
            break
        except Exception as e:
            logger.error(f"Scheduler menghadapi rintangan kritis: {e}")
            time.sleep(10) # Cooldown sebelum mencoba ulang untuk mencegah max CPU

if __name__ == "__main__":
    main()
