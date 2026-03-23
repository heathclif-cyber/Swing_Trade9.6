import argparse
import os
import requests
import pandas as pd

CMC_URL = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"

def fetch_cmc_data(api_key):
    headers = {"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"}
    try:
        r = requests.get(CMC_URL, headers=headers, timeout=10)
        r.raise_for_status()
        d = r.json()["data"]
        btc_dom_raw = round(d["btc_dominance"] * 100, 1)          # ×100 sesuai format CSV
        total_mcap = d["quote"]["USD"]["total_market_cap"]
        btc_dom_frac = d["btc_dominance"] / 100
        altcoin_index = round(total_mcap * (1 - btc_dom_frac) / 1_000_000_000, 1)
        return btc_dom_raw, altcoin_index
    except Exception as e:
        print(f"❌ Error fetching CMC data: {e}")
        return None, None

def fill_csv(filepath, api_key="aa8eb4dd82974c308c5428e7c1be0121"):
    if not os.path.exists(filepath):
        print(f"❌ File not found: {filepath}")
        return
    
    if api_key == "ISI_API_KEY_KAMU_DI_SINI":
        print("❌ Error: Harap masukkan API Key CoinMarketCap yang valid.")
        print("Gunakan flag --api-key <API_KEY_ANDA> saat menjalankan script.")
        return

    print("Mengambil data terbaru dari CoinMarketCap...")
    btc_dom_val, altcoin_val = fetch_cmc_data(api_key)
    if btc_dom_val is None or altcoin_val is None:
        print("❌ Gagal mengambil data dari CMC. Skrip dihentikan.")
        return

    print(f"✅ Data diterima: BTC_Dom={btc_dom_val}, Altcoin_Index={altcoin_val}")

    # Extract comments
    comments = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('#'):
                comments.append(line)
            else:
                break
                
    # Load CSV skipping comment rows
    print(f"Membaca file: {filepath}")
    df = pd.read_csv(filepath, comment="#")

    if "BTC_Dominance" not in df.columns or "Altcoin_Index" not in df.columns:
        print("❌ Kolom BTC_Dominance atau Altcoin_Index tidak ditemukan di CSV.")
        return

    # Fill NaN or 0
    mask_btc = df["BTC_Dominance"].isna() | (df["BTC_Dominance"] == "") | (df["BTC_Dominance"] == 0)
    mask_alt = df["Altcoin_Index"].isna() | (df["Altcoin_Index"] == "") | (df["Altcoin_Index"] == 0)

    try:
        df.loc[mask_btc, "BTC_Dominance"] = btc_dom_val
        df.loc[mask_alt, "Altcoin_Index"] = altcoin_val
    except Exception as e:
        print(f"❌ Error saat mengupdate dataframe: {e}")
        return

    # Save to a new file
    dirname, basename = os.path.split(filepath)
    name, ext = os.path.splitext(basename)
    out_filepath = os.path.join(dirname, f"{name}_filled{ext}")
    
    print("Menyimpan file hasil...")
    with open(out_filepath, 'w', encoding='utf-8') as f:
        f.writelines(comments)
        # Ensure there is exactly one newline before CSV data starts
        if comments and not comments[-1].endswith('\n'):
            f.write('\n')
        
    df.to_csv(out_filepath, mode='a', index=False)
    
    print("-" * 50)
    print(f"✅ Baris diperbarui: BTC_Dominance ({mask_btc.sum()} baris), Altcoin_Index ({mask_alt.sum()} baris)")
    print(f"✅ File berhasil disimpan sebagai: {out_filepath}")
    print("-" * 50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Isi kolom CMC yang kosong di CSV Trading (BTC_Dominance & Altcoin_Index)")
    parser.add_argument("csv_file", help="Path ke file CSV")
    parser.add_argument("--api-key", help="API Key CoinMarketCap", default="aa8eb4dd82974c308c5428e7c1be0121")
    args = parser.parse_args()
    
    fill_csv(args.csv_file, args.api_key)
