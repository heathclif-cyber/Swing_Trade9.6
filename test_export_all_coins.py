import sys
import pandas as pd
from protocol_96_ui import build_export_dataframe, AVAILABLE_PAIRS

def test_all_coins():
    print("Mulai pengecekan struktur koin dalam AVAILABLE_PAIRS...")
    print(f"Total koin: {len(AVAILABLE_PAIRS)}\n")
    
    # Check limit 100 to make it fast
    limit = 100
    interval = '4h'
    
    results = []
    for pair in AVAILABLE_PAIRS:
        print(f"Mengambil data {pair}...")
        try:
            df = build_export_dataframe(pair, interval, limit=limit)
            
            # Cek kolom target
            target_cols = ['BTC_Dominance', 'Altcoin_Index', 'Buy_Liq', 'Sell_Liq']
            
            missing_cols = [c for c in target_cols if c not in df.columns]
            if missing_cols:
                results.append((pair, "GAGAL", f"Kolom hilang: {missing_cols}"))
                continue
                
            # Cek baris kosong / nol khusus 5 row terakhir
            tail_df = df.tail(5)
            
            issues = []
            for col in target_cols:
                null_count = tail_df[col].isna().sum()
                zero_count = (tail_df[col] == 0).sum()
                if null_count > 0:
                    issues.append(f"{col}: {null_count} NaN")
                if zero_count > 0:
                    issues.append(f"{col}: {zero_count} nol")
                    
            if issues:
                results.append((pair, "PERINGATAN", " | ".join(issues)))
            else:
                results.append((pair, "OK", "Data lengkap"))
                
        except Exception as e:
            results.append((pair, "GAGAL", str(e)))
            
    print("\n=== HASIL PENGECEKAN ===")
    print(f"{'COIN':<15} {'STATUS':<15} {'KETERANGAN'}")
    print("-" * 50)
    for pair, status, info in results:
        print(f"{pair:<15} {status:<15} {info}")

if __name__ == '__main__':
    test_all_coins()
