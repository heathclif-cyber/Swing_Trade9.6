import requests
import pandas as pd
import io

url = "http://127.0.0.1:5000/api/export-csv?tf=15m&limit=50"
save_path = "d:/Apps Dev/Swing_Trade9.6/enriched_export.csv"

try:
    print(f"Requesting: {url}")
    resp = requests.get(url, timeout=60)
    if resp.status_code == 200:
        with open(save_path, "wb") as f:
            f.write(resp.content)
        print(f"✅ CSV saved to {save_path}")
        
        df = pd.read_csv(io.StringIO(resp.text))
        print("\nColumns and sample values:")
        enriched_cols = [
            'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4',
            'ATR_14', 'ATR_14_H4',
            'PDH', 'PDL', 'PWH', 'PWL',
            'FVG_Up_Top', 'FVG_Up_Bottom', 'FVG_Down_Top', 'FVG_Down_Bottom',
            'OB_Price', 'SFP_Sweep',
            'Fib_0.618', 'Fib_0.786'
        ]
        
        found_cols = [c for c in enriched_cols if c in df.columns]
        missing_cols = [c for c in enriched_cols if c not in df.columns]
        
        print(f"Found {len(found_cols)}/{len(enriched_cols)} enriched columns.")
        if missing_cols:
            print(f"❌ Missing columns: {missing_cols}")
        else:
            print("✅ All enrichment columns present!")
            
        print("\nSample of enriched data (last 5 rows):")
        print(df[found_cols].tail(5))
        
        # Check for Nulls in the last row
        last_row_nulls = df[found_cols].iloc[-1].isnull().sum()
        if last_row_nulls == 0:
            print("\n✅ Verification passed: No Null values in the last row.")
        else:
            print(f"\n⚠️ Warning: Found {last_row_nulls} Null values in the last row.")
            print(df[found_cols].iloc[-1])
            
    else:
        print(f"❌ Error: Status {resp.status_code}")
except Exception as e:
    print(f"Error: {e}")
