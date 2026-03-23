import pandas as pd
import pandas_ta as ta
import requests
from binance.client import Client
from datetime import datetime
import time
import os

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===========================================================
# KONFIGURASI
# ===========================================================
SYMBOL = "BTCUSDT"
INTERVAL = Client.KLINE_INTERVAL_4HOUR
START_STR = "4 years ago UTC"
FILENAME = "BTCUSDT_4H_4Years.csv"

print("⏳ Inisialisasi Binance Client...")
# Note: verify=False used to avoid SSL issues in some environments like previous scripts
client = Client(requests_params={"verify": False})

# ===========================================================
# STEP 1: Download Klines (4 Tahun)
# ===========================================================
print(f"\n📥 [1/3] Mendownload 4 tahun data historis {SYMBOL} ({INTERVAL})...")
print("Mohon tunggu sebentar...")

try:
    # Mengambil klines dari Spot (untuk harga dan volume)
    klines = client.get_historical_klines(SYMBOL, INTERVAL, START_STR)
except Exception as e:
    print(f"❌ Error mengambil klines dari Binance: {e}")
    exit(1)

print(f"✅ Berhasil mengambil {len(klines)} candles.")

df = pd.DataFrame(klines, columns=[
    'Timestamp', 'Open', 'High', 'Low', 'Close', 'Total_Volume',
    'Close_Time', 'Quote_Asset_Volume', 'Trades', 'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
])

# Konversi tipe data
numeric_cols = ['Open', 'High', 'Low', 'Close', 'Total_Volume', 'Taker_Buy_Base']
df[numeric_cols] = df[numeric_cols].astype(float)
df['Trades'] = df['Trades'].astype(int)
df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')

# Volume & CVD (Cumulative Volume Delta)
df['Buy_Volume'] = df['Taker_Buy_Base']
df['Sell_Volume'] = df['Total_Volume'] - df['Buy_Volume']
df['Volume_Delta'] = df['Buy_Volume'] - df['Sell_Volume']
df['CVD'] = df['Volume_Delta'].cumsum()

# Indikator Teknikal (Protocol 9.6 Standard)
print("🧮 Menghitung indikator (EMA 21/50/200, ATR 14, RSI 6)...")
df['EMA_21'] = ta.ema(df['Close'], length=21)
df['EMA_50'] = ta.ema(df['Close'], length=50)
df['EMA_200'] = ta.ema(df['Close'], length=200)
df['ATR_14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
df['RSI_6'] = ta.rsi(df['Close'], length=6)

# Liquiditas (Previous Day/Week High/Low)
# 1 hari = 6 candle 4H; 1 minggu = 42 candle 4H
print("🔍 Menghitung level Likuiditas (PDH, PDL, PWH, PWL)...")
df['PDH'] = df['High'].rolling(window=6).max().shift(1)
df['PDL'] = df['Low'].rolling(window=6).min().shift(1)
df['PWH'] = df['High'].rolling(window=42).max().shift(1)
df['PWL'] = df['Low'].rolling(window=42).min().shift(1)

# ===========================================================
# STEP 2: Download Open Interest (Data Terbatas ~30 hari)
# ===========================================================
print(f"\n📥 [2/3] Mengambil data Open Interest dari Binance Futures API...")
print("⚠️  Catatan: API Publik hanya menyediakan OI historis terbatas (~30 hari).")

def fetch_oi_history(symbol, period="4h", limit=500):
    url = "https://fapi.binance.com/futures/data/openInterestHist"
    all_oi = []
    end_time = None
    # Kita coba ambil sebanyak mungkin (max limit biasanya 500 per call)
    # Untuk 4 tahun data 4H, total ada ~8700 candles. Namun API futures dibatasi.
    max_requests = 10 # Batasi request agar tidak kena rate limit
    
    for _ in range(max_requests):
        params = {"symbol": symbol, "period": period, "limit": limit}
        if end_time:
            params["endTime"] = end_time
        try:
            resp = requests.get(url, params=params, timeout=10, verify=False)
            if resp.status_code != 200:
                break
            data = resp.json()
            if not data or len(data) == 0:
                break
            all_oi.extend(data)
            oldest_ts = data[0]['timestamp']
            if end_time and oldest_ts >= end_time:
                break
            end_time = oldest_ts - 1
            time.sleep(0.5)
            if len(data) < limit:
                break
        except Exception as e:
            print(f"   ⚠️  Error OI: {e}")
            break
    return all_oi

oi_data = fetch_oi_history(SYMBOL, period="4h", limit=500)

if oi_data:
    df_oi = pd.DataFrame(oi_data)
    df_oi['timestamp'] = pd.to_datetime(df_oi['timestamp'], unit='ms')
    df_oi['Open_Interest'] = df_oi['sumOpenInterest'].astype(float)
    df_oi = df_oi[['timestamp', 'Open_Interest']].rename(columns={'timestamp': 'Timestamp'})
    df_oi = df_oi.sort_values('Timestamp').reset_index(drop=True)
    
    # Merge ke dataframe utama menggunakan floor pembulatan waktu agar sinkron
    df['Timestamp_round'] = df['Timestamp'].dt.floor('4h')
    df_oi['Timestamp_round'] = df_oi['Timestamp'].dt.floor('4h')
    df = df.merge(df_oi[['Timestamp_round', 'Open_Interest']], on='Timestamp_round', how='left')
    df.drop(columns=['Timestamp_round'], inplace=True, errors='ignore')
    print(f"✅ Berhasil sinkronisasi {df['Open_Interest'].notna().sum()} baris OI data REAL.")
else:
    print("⚠️  Data OI tidak tersedia dari API. Akan menggunakan estimasi.")
    df['Open_Interest'] = None

# ===========================================================
# STEP 3: OI Sintetis untuk data lama (Institutional Proxy)
# ===========================================================
print(f"\n🔧 [3/3] Mengisi OI sintetis (Proxy) untuk data historis lama...")

if df['Open_Interest'].notna().any():
    # Gunakan korelasi Volume ke OI dari data yang ada
    known_oi = df.dropna(subset=['Open_Interest'])
    # Median ratio Volume : OI
    ratio = (known_oi['Open_Interest'] / known_oi['Total_Volume'].replace(0, 1)).median()
    # Forecast OI lama berbasis Moving Average Volume * ratio
    df['OI_Proxy'] = df['Total_Volume'].rolling(6, min_periods=1).mean() * ratio
    df['Open_Interest'] = df['Open_Interest'].fillna(df['OI_Proxy'])
    df.drop(columns=['OI_Proxy'], inplace=True, errors='ignore')
    print(f"   ✅ Menggunakan ratio {ratio:.4f} untuk estimasi data lama.")
else:
    # Default fallback jika tidak ada data OI sama sekali (ratio BTC umum ~10-15x Volume 4H)
    df['Open_Interest'] = df['Total_Volume'].rolling(6, min_periods=1).mean() * 12.0
    print("   ✅ Menggunakan default volume proxy untuk estimasi data.")

# Clean up
df.drop(columns=['Close_Time', 'Quote_Asset_Volume', 'Taker_Buy_Quote', 'Ignore'],
        inplace=True, errors='ignore')

# Filter baris yang tidak lengkap indikatornya (di awal periode rolling)
df.dropna(subset=['EMA_200', 'ATR_14', 'PDH'], inplace=True)
df.reset_index(drop=True, inplace=True)

# Simpan ke CSV
df.to_csv(FILENAME, index=False)

print(f"\n🚀 SELESAI!")
print(f"📂 File disimpan: {os.path.abspath(FILENAME)}")
print(f"📊 Total Data  : {len(df)} baris")
print(f"📅 Rentang     : {df['Timestamp'].iloc[0]} s/d {df['Timestamp'].iloc[-1]}")
print(f"✅ Siap digunakan untuk backtesting Protocol 9.6!")
