import pandas as pd
import pandas_ta as ta
import requests
from binance.client import Client
from datetime import datetime
import time

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

print("⏳ Inisialisasi Binance Client...")
client = Client(requests_params={"verify": False})

SYMBOL = "DOGEUSDT"
INTERVAL = Client.KLINE_INTERVAL_4HOUR
START_STR = "4 years ago UTC"

# ===========================================================
# STEP 1: Download Klines (4 Tahun)
# ===========================================================
print(f"\n📥 [1/3] Mendownload 4 tahun data historis {SYMBOL} ({INTERVAL})...")
print("Mohon tunggu sekitar 15-30 detik...")

try:
    klines = client.get_historical_klines(SYMBOL, INTERVAL, START_STR)
except Exception as e:
    print(f"❌ Error mengambil klines dari Binance: {e}")
    exit(1)

print(f"✅ Berhasil mengambil {len(klines)} candles.")

df = pd.DataFrame(klines, columns=[
    'Timestamp', 'Open', 'High', 'Low', 'Close', 'Total_Volume',
    'Close_Time', 'Quote_Asset_Volume', 'Trades', 'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
])

numeric_cols = ['Open', 'High', 'Low', 'Close', 'Total_Volume', 'Taker_Buy_Base']
df[numeric_cols] = df[numeric_cols].astype(float)
df['Trades'] = df['Trades'].astype(int)
df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')

# Volume & CVD
df['Buy_Volume'] = df['Taker_Buy_Base']
df['Sell_Volume'] = df['Total_Volume'] - df['Buy_Volume']
df['Volume_Delta'] = df['Buy_Volume'] - df['Sell_Volume']
df['CVD'] = df['Volume_Delta'].cumsum()

# Indikator Teknikal
print("🧮 Menghitung indikator (EMA, ATR, RSI)...")
df['EMA_21'] = ta.ema(df['Close'], length=21)
df['EMA_50'] = ta.ema(df['Close'], length=50)
df['EMA_200'] = ta.ema(df['Close'], length=200)
df['ATR_14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
df['RSI_6'] = ta.rsi(df['Close'], length=6)

# Liquiditas (1 hari = 6 candle 4H; 1 minggu = 42 candle 4H)
df['PDH'] = df['High'].rolling(window=6).max().shift(1)
df['PDL'] = df['Low'].rolling(window=6).min().shift(1)
df['PWH'] = df['High'].rolling(window=42).max().shift(1)
df['PWL'] = df['Low'].rolling(window=42).min().shift(1)

# ===========================================================
# STEP 2: Download Open Interest (Maks ~30 hari dari Binance Futures)
# ===========================================================
print(f"\n📥 [2/3] Mengambil data Open Interest dari Binance Futures API...")
print("⚠️  Catatan: Binance hanya menyediakan OI historis untuk ~30 hari terakhir.")

def fetch_oi_history(symbol, period="4h", limit=500):
    url = "https://fapi.binance.com/futures/data/openInterestHist"
    all_oi = []
    end_time = None
    while True:
        params = {"symbol": symbol, "period": period, "limit": limit}
        if end_time:
            params["endTime"] = end_time
        try:
            resp = requests.get(url, params=params, timeout=10, verify=False)
            if resp.status_code != 200:
                print(f"   ⚠️  API Error: {resp.status_code}")
                break
            data = resp.json()
            if not data:
                break
            all_oi.extend(data)
            oldest_ts = data[0]['timestamp']
            if end_time and oldest_ts >= end_time:
                break
            end_time = oldest_ts - 1
            time.sleep(0.3)
            if len(data) < limit:
                break
        except Exception as e:
            print(f"   ⚠️  Error: {e}")
            break
    return all_oi

oi_data = fetch_oi_history(SYMBOL, period="4h", limit=500)

if oi_data:
    df_oi = pd.DataFrame(oi_data)
    df_oi['timestamp'] = pd.to_datetime(df_oi['timestamp'], unit='ms')
    df_oi['Open_Interest'] = df_oi['sumOpenInterest'].astype(float)
    df_oi = df_oi[['timestamp', 'Open_Interest']].rename(columns={'timestamp': 'Timestamp'})
    df_oi = df_oi.sort_values('Timestamp').reset_index(drop=True)
    
    oi_start = df_oi['Timestamp'].min().strftime('%Y-%m-%d')
    oi_end = df_oi['Timestamp'].max().strftime('%Y-%m-%d')
    print(f"✅ Berhasil mengambil {len(df_oi)} baris OI data REAL ({oi_start} s/d {oi_end})")
    
    # Merge ke klines
    df['Timestamp_round'] = df['Timestamp'].dt.floor('4h')
    df_oi['Timestamp_round'] = df_oi['Timestamp'].dt.floor('4h')
    df = df.merge(df_oi[['Timestamp_round', 'Open_Interest']], on='Timestamp_round', how='left')
    df.drop(columns=['Timestamp_round'], inplace=True, errors='ignore')
else:
    print("⚠️  Data OI tidak tersedia. Semua akan diisi dengan estimasi sintetis.")
    df['Open_Interest'] = None

# ===========================================================
# STEP 3: OI Sintetis untuk data historis > 30 hari
# ===========================================================
print(f"\n🔧 [3/3] Mengisi OI sintetis berbasis Volume untuk data historis lama...")

if df['Open_Interest'].notna().any():
    known_oi = df.dropna(subset=['Open_Interest'])
    ratio = (known_oi['Open_Interest'] / known_oi['Total_Volume'].replace(0, 1)).median()
    df['OI_Synthetic'] = df['Total_Volume'].rolling(6, min_periods=1).mean() * ratio
    df['Open_Interest'] = df['Open_Interest'].fillna(df['OI_Synthetic'])
    df.drop(columns=['OI_Synthetic'], inplace=True, errors='ignore')
    n_real = known_oi.shape[0]
    n_synth = len(df) - n_real
    print(f"   ✅ {n_real} baris OI REAL + {n_synth} baris OI SINTETIS")
else:
    df['Open_Interest'] = df['Total_Volume'].rolling(6, min_periods=1).mean() * 8.5
    print("   ✅ Semua menggunakan estimasi proxy volume.")

# Bersihkan kolom tidak perlu
df.drop(columns=['Close_Time', 'Quote_Asset_Volume', 'Taker_Buy_Quote', 'Ignore'],
        inplace=True, errors='ignore')

df.dropna(subset=['EMA_200', 'ATR_14'], inplace=True)
df.reset_index(drop=True, inplace=True)

filename = "DOGEUSDT_4H_4Years.csv"
df.to_csv(filename, index=False)

print(f"\n🎉 SELESAI! Data disimpan ke: {filename}")
print(f"   Total baris  : {len(df)}")
print(f"   Kolom        : {df.columns.tolist()}")
print(f"   Rentang Data : {df['Timestamp'].iloc[0]} s/d {df['Timestamp'].iloc[-1]}")
