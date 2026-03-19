import time
import logging
import requests  # type: ignore
import schedule  # type: ignore
import pandas as pd  # type: ignore
import pandas_ta as ta  # type: ignore
from binance.client import Client  # type: ignore
from binance.exceptions import BinanceAPIException, BinanceRequestException  # type: ignore

# ==========================================
# ⚙️ USER CONFIGURATION (State Variables)
# ==========================================
COIN_PAIR = "SUIUSDT"
ENTRY_PRICE = 1.055
ALLOCATED_CAPITAL = 200

# Pengaturan Telegram
TELEGRAM_BOT_TOKEN = "8728046864:AAEaLD5c1yJRuTjoNKRLbyzkBII2AJKV9hE"
TELEGRAM_CHAT_ID = "982913105"

# Binance API Keys (Opsional untuk data market publik, wajib untuk fungsi private)
BINANCE_API_KEY = ""
BINANCE_API_SECRET = ""

# ==========================================
# 📊 BOT INTERNAL STATE
# ==========================================
class BotState:
    INITIALIZED = False
    ACTIVE_SL = 0.0
    ALERTS_SENT = {
        "TP_1": False,
        "SL_BE": False,
        "VOL_FAKEOUT": False,
        "KILL_SWITCH": False
    }

# Setup Logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Protocol_9.6")

# Inisialisasi Binance Client
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

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
        "parse_mode": "Markdown"
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
    """Menambahkan indikator EMA dan RSI menggunakan pandas_ta."""
    if df.empty: return df
    
    # Exponential Moving Averages
    df['EMA_7'] = ta.ema(df['Close'], length=7)
    df['EMA_21'] = ta.ema(df['Close'], length=21)
    df['EMA_50'] = ta.ema(df['Close'], length=50)
    df['EMA_200'] = ta.ema(df['Close'], length=200)
    
    # Momentum RSI
    df['RSI_6'] = ta.rsi(df['Close'], length=6)
    return df

def check_intermarket_macro() -> dict:
    """Akses batas makro D1/W1, Open Interest (OI) Delta, dan SMT Divergence."""
    res = {"PDH": 0.0, "PDL": 0.0, "PWH": 0.0, "Delta_OI": 0.0, "Bearish_SMT": False, "BTC_Dom_Change": 0.0}
    
    try:
        # Macro Liquidity Borders (D1 / W1)
        df_1d = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1DAY, limit=2)
        df_1w = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1WEEK, limit=2)
        
        if len(df_1d) >= 2: res["PDH"], res["PDL"] = df_1d.iloc[-2]['High'], df_1d.iloc[-2]['Low']
        if len(df_1w) >= 2: res["PWH"] = df_1w.iloc[-2]['High']
            
        # Delta Open Interest (M15 Futures API) - 4 last candles
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        params = {"symbol": COIN_PAIR, "period": "15m", "limit": 4}
        oi_resp = requests.get(url, params=params, timeout=10)
        if oi_resp.status_code == 200:
            data = oi_resp.json()
            if len(data) >= 2:
                old_oi = float(data[0]['sumOpenInterest'])
                new_oi = float(data[-1]['sumOpenInterest'])
                res["Delta_OI"] = ((new_oi - old_oi) / old_oi) * 100

        # SMT Divergence (Korelasi BTC) H4
        df_btc = get_klines_df("BTCUSDT", Client.KLINE_INTERVAL_4HOUR, limit=4)
        df_tgt = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_4HOUR, limit=4)
        
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
def evaluate_market_conditions():
    """Fungsi utama yang memanggil siklus evaluasi setiap candle target."""
    try:
        # 1. Tarik Struktur Data Frame (Swing Macro Adjustments: H1, H4, D1)
        df_1h = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1HOUR, limit=250)
        df_4h = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_4HOUR, limit=500)
        df_1d = get_klines_df(COIN_PAIR, Client.KLINE_INTERVAL_1DAY, limit=100)
        
        if df_1h.empty or df_4h.empty or df_1d.empty:
            return

        # 2. Kalkulasi Indikator
        df_1h = apply_indicators(df_1h)
        df_4h = apply_indicators(df_4h)
        
        current_price = df_1h.iloc[-1]['Close']
        current_ema21_4h = df_4h.iloc[-1]['EMA_21']
        
        # 3. Inisialisasi Bootstrapping Protocol
        if not BotState.INITIALIZED:
            BotState.ACTIVE_SL = current_ema21_4h * 0.99
            BotState.INITIALIZED = True
            
            logger.info("=== 🏗️ PROTOCOL 9.6: STRUCTURAL GUARDIAN INITIALIZED ===")
            logger.info(f"Target Coin: {COIN_PAIR} | Entry Price: ${ENTRY_PRICE}")
            logger.info(f"Initial Active SL: ${BotState.ACTIVE_SL:.4f} (EMA 21 H4 - 1%)")
            logger.info(f"Current EMA 21 (H4): ${current_ema21_4h:.4f}")
            logger.info(f"Current EMA 200 (H4): ${df_4h.iloc[-1]['EMA_200']:.4f}")
            logger.info("=========================================================")

        # 4. Ambil Intermarket Macro State
        macro_state = check_intermarket_macro()

        # =========================================================
        # 🚨 TRIGGER 1: THE PROFIT GUARDIAN (Take Profit Parsial)
        # =========================================================
        rsi_1h = df_1h.iloc[-1]['RSI_6']
        rsi_4h = df_4h.iloc[-1]['RSI_6']
        
        is_momentum_overheated = (rsi_1h > 85) or (rsi_4h > 85)
        is_touching_macro = (current_price >= macro_state["PDH"] and macro_state["PDH"] > 0) or \
                            (current_price >= macro_state["PWH"] and macro_state["PWH"] > 0)
        
        if (is_momentum_overheated or is_touching_macro) and not BotState.ALERTS_SENT["TP_1"]:
            rsi_val = rsi_1h if rsi_1h > 85 else rsi_4h
            msg = (
                f"🟢 *TACTICAL TP ALERT:* {COIN_PAIR}\n"
                f"Momentum Overheated! RSI H1/H4: {rsi_val:.2f}. Harga: ${current_price:.4f}.\n"
                f"🎯 *Instruksi:* Jual 30% posisi sekarang. Profit diamankan!"
            )
            send_telegram_alert(msg)
            BotState.ALERTS_SENT["TP_1"] = True

        # =========================================================
        # 🚨 TRIGGER 2: STRUCTURAL SL & BREAK-EVEN PROTECTOR
        # =========================================================
        sl_msg = ""
        # Break-even Trigger
        if current_price > (ENTRY_PRICE * 1.03) and BotState.ACTIVE_SL < ENTRY_PRICE:
            BotState.ACTIVE_SL = ENTRY_PRICE * 1.005 # Breakeven +0.5% buffer
            sl_msg = f"Posisi profit +3%. Break-Even Terkunci."
            
        # Trailing SL Trigger
        if current_ema21_4h * 0.99 > BotState.ACTIVE_SL:
            BotState.ACTIVE_SL = current_ema21_4h * 0.99
            sl_msg = f"Tren naik mendominasi. EMA Support dinaikkan."

        if sl_msg and not BotState.ALERTS_SENT["SL_BE"]:
            msg = (
                f"🛡️ *SL UPDATED:* {COIN_PAIR}\n"
                f"{sl_msg} Stop Loss dinaikkan ke ${BotState.ACTIVE_SL:.4f}. Modal 100% aman!"
            )
            send_telegram_alert(msg)
            BotState.ALERTS_SENT["SL_BE"] = True # Reset logic needed if we want multiple trail pings

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
        
        if (vol_3h_sell > vol_3h_buy) and no_higher_high and not BotState.ALERTS_SENT["VOL_FAKEOUT"]:
            msg = (
                f"⚠️ *VOLUME DANGER:* {COIN_PAIR}\n"
                f"Heavy Distribution terdeteksi. 3 Candle terakhir didominasi Sell Volume institusi (Fakeout).\n"
                f"🔪 *Instruksi:* Exit Immediate / Perketat SL sekarang!"
            )
            send_telegram_alert(msg)
            BotState.ALERTS_SENT["VOL_FAKEOUT"] = True
            
        # Reset Volume Fakeout logic if buying resumes
        if vol_3h_buy > vol_3h_sell:
            BotState.ALERTS_SENT["VOL_FAKEOUT"] = False

        # =========================================================
        # 🚨 TRIGGER 4: THE KILL SWITCH (Darurat)
        # =========================================================
        # Kondisi A: Candle H4 CLOSED di bawah EMA_21 H4 (Ambil indeks -2 karena tutup penuh)
        last_closed_h4 = df_4h.iloc[-2]
        ema_kill_switch = last_closed_h4['Close'] < last_closed_h4['EMA_21']
        
        # Kondisi B: BTC Dominance Siphon (Altcoin Filter Siphon)
        ticker_tgt = client.get_ticker(symbol=COIN_PAIR)
        tgt_change_24h = float(ticker_tgt['priceChangePercent'])
        
        # Siphon terjadi jika BTC Dominance naik tinggi tapi Altcoin hancur (Liquidity dialirkan ke BTC doang)
        btc_siphon = (macro_state.get("BTC_Dom_Change", 0.0) >= 1.5) and (tgt_change_24h <= -1.0)
        
        if (ema_kill_switch or btc_siphon) and not BotState.ALERTS_SENT["KILL_SWITCH"]:
            msg = f"💀 *KILL SWITCH ACTIVATED:* {COIN_PAIR}\n"
            
            if ema_kill_switch:
                msg += f"Structural Breakdown! H4 Close di bawah EMA 21 (${last_closed_h4['EMA_21']:.4f}).\n"
            else:
                msg += f"Korelasi Berbahaya (BTC.D Siphon terdeteksi. BTC.DOM naik {macro_state.get('BTC_Dom_Change', 0):.2f}%).\n"
                
            msg += f"❌ *Instruksi:* BUANG SEMUA POSISI. Tren Bullish resmi batal."
            
            send_telegram_alert(msg)
            BotState.ALERTS_SENT["KILL_SWITCH"] = True

    except (BinanceAPIException, BinanceRequestException) as bae:
        logger.error(f"Binance API Network Error: {bae}")
    except Exception as e:
        logger.error(f"Kalkulasi Tracker Gagal: {e}")

# ==========================================
# ⏱️ SCHEDULER & MAIN LOOP
# ==========================================
def main():
    logger.info("Bot siap dijalankan. Memulai Tracker...")
    evaluate_market_conditions() # Jalankan pertama kali segera setelah distart

    # Jalankan pemeriksaan setiap 1 menit mengikuti standar responsivitas Protocol
    schedule.every(1).minutes.do(evaluate_market_conditions)
    
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

