import requests
import pandas as pd
import io

url = "http://127.0.0.1:5000/api/export-csv?tf=15m&limit=10"

try:
    print(f"Requesting: {url}")
    resp = requests.get(url, timeout=20)
    print(f"Status Code: {resp.status_code}")
    print(f"Content Type: {resp.headers.get('Content-Type')}")
    
    if resp.status_code == 200:
        csv_data = resp.text
        df = pd.read_csv(io.StringIO(csv_data))
        print("\nCSV Head:")
        print(df.head())
        print("\nColumns found:")
        print(df.columns.tolist())
        
        # Check for Open_Interest column and if it's not all the same (if historical data was merged)
        if 'Open_Interest' in df.columns:
            oi_values = df['Open_Interest'].dropna().unique()
            print(f"\nUnique Open_Interest values found: {len(oi_values)}")
            if len(oi_values) > 1:
                print("✅ Success: Historical Open Interest data is being merged!")
            else:
                print("⚠️ Note: Only 1 unique Open Interest value found. This might be normal if the data hasn't changed much in the last 10 candles, but should be checked.")
        else:
            print("❌ Error: Open_Interest column missing!")
            
    else:
        print(f"Error Response: {resp.text}")
except Exception as e:
    print(f"Error: {e}")
