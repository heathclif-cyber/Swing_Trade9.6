import requests
import pandas as pd
import os
import glob
import io
import argparse

DEPTH_URL = "https://fapi.binance.com/fapi/v1/depth"

def extract_symbol(filepath):
    """Baca symbol dari baris komentar # Symbol: di dalam CSV."""
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("# Symbol:"):
                # Handle # Symbol: XRPUSDT format
                symbol = line.replace("# Symbol:", "").strip()
                return symbol
    return None

def read_csv_with_comments(filepath):
    """Load CSV sambil pisahkan baris komentar (#) dari data."""
    comment_lines = []
    data_lines    = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                comment_lines.append(line)
            else:
                data_lines.append(line)
    
    # Read the data block as CSV
    df = pd.read_csv(io.StringIO("".join(data_lines)))
    return df, comment_lines

def write_csv_with_comments(df, comment_lines, filepath):
    """Simpan CSV dengan baris komentar di atas tetap dipertahankan."""
    csv_content = df.to_csv(index=False)
    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(comment_lines)
        if comment_lines and not comment_lines[-1].endswith("\n"):
            f.write("\n")
        f.write(csv_content)

def fetch_liquidity(symbol, close_price):
    """Fetch order book dari Binance dan cari wall terbesar di atas/bawah close."""
    try:
        params = {"symbol": symbol.upper(), "limit": 500}
        r = requests.get(DEPTH_URL, params=params, timeout=10)
        r.raise_for_status()
        book = r.json()

        # Bids = order beli — ambil yang di bawah close
        bids = [(float(p), float(q)) for p, q in book["bids"] if float(p) < close_price]
        # Asks = order jual — ambil yang di atas close
        asks = [(float(p), float(q)) for p, q in book["asks"] if float(p) > close_price]

        if not bids or not asks:
            print(f"  ⚠️  Order book kosong untuk {symbol} di sekitar {close_price}")
            return 0.0, 0.0

        buy_liq  = max(bids, key=lambda x: x[1])[0]   # bid wall terbesar
        sell_liq = max(asks, key=lambda x: x[1])[0]   # ask wall terbesar

        return round(buy_liq, 6), round(sell_liq, 6)

    except Exception as e:
        print(f"  ❌ Gagal fetch {symbol}: {e}")
        return 0.0, 0.0

def process_file(filepath):
    """Proses satu file CSV: baca symbol, fetch liquidity, isi kolom kosong."""
    print(f"\n📄 Proses: {os.path.basename(filepath)}")

    # Ekstrak symbol dari komentar
    symbol = extract_symbol(filepath)
    if not symbol:
        print(f"  ⚠️  Symbol tidak ditemukan di baris metadata file — skip")
        return

    print(f"  Symbol   : {symbol}")

    # Load CSV
    df, comment_lines = read_csv_with_comments(filepath)

    # Pastikan kolom ada
    if "Buy_Liq" not in df.columns or "Sell_Liq" not in df.columns:
        print(f"  ⚠️  Kolom Buy_Liq / Sell_Liq tidak ada di file — skip")
        return

    # Cek berapa baris yang perlu diisi
    mask_buy  = df["Buy_Liq"].isna()  | (df["Buy_Liq"]  == 0.0) | (df["Buy_Liq"]  == 0)
    mask_sell = df["Sell_Liq"].isna() | (df["Sell_Liq"] == 0.0) | (df["Sell_Liq"] == 0)
    
    n_buy  = mask_buy.sum()
    n_sell = mask_sell.sum()

    if n_buy == 0 and n_sell == 0:
        print(f"  ✅ Sudah lengkap, tidak ada Buy_Liq/Sell_Liq 0 yang perlu diisi")
        return

    print(f"  Baris kosong/0: Buy_Liq={n_buy}, Sell_Liq={n_sell}")

    # Ambil Close candle terakhir sebagai referensi harga
    try:
        close_last = float(df["Close"].iloc[-1])
    except Exception as e:
        print(f"  ⚠️  Gagal mengambil kolom Close: {e}")
        return
        
    print(f"  Close terakhir: {close_last}")

    # Fetch dari Binance
    buy_val, sell_val = fetch_liquidity(symbol, close_last)
    print(f"  Hasil Orderbook API → Buy_Liq Wall={buy_val}, Sell_Liq Wall={sell_val}")

    if buy_val == 0.0 and sell_val == 0.0:
        print(f"  ⚠️  Fetch gagal atau orderbook sepi, file tidak diubah")
        return

    # Isi kolom yang kosong (hanya baris yang 0 atau NaN)
    df.loc[mask_buy,  "Buy_Liq"]  = buy_val
    df.loc[mask_sell, "Sell_Liq"] = sell_val

    # Simpan kembali dengan metadata yang utuh (overwrite aman)
    write_csv_with_comments(df, comment_lines, filepath)
    print(f"  ✅ Tersimpan — file '{os.path.basename(filepath)}' berhasil di-update.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Isi kolom Buy_Liq & Sell_Liq dengan data orderbook FAPI Binance")
    parser.add_argument("--folder", default=".", help="Folder yang berisi file CSV")
    args = parser.parse_args()
    
    folder = args.folder
    pattern = "*.csv"
    files = sorted(glob.glob(os.path.join(folder, pattern)))

    if not files:
        print(f"❌ Tidak ada file CSV ditemukan di folder: {folder}")
    else:
        print(f"🔍 Ditemukan {len(files)} file CSV di '{folder}'\n{'─'*50}")
        for filepath in files:
            process_file(filepath)
        print(f"\n{'─'*50}")
        print(f"✅ Selesai memproses batch {len(files)} file!")
