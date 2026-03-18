import requests

url = "http://127.0.0.1:5000/api/export-csv?tf=15m&limit=20"
save_path = "d:/Apps Dev/Swing_Trade9.6/test_export.csv"

try:
    print(f"Requesting: {url}")
    resp = requests.get(url, timeout=30)
    if resp.status_code == 200:
        with open(save_path, "wb") as f:
            f.write(resp.content)
        print(f"✅ CSV saved to {save_path}")
    else:
        print(f"❌ Error: Status {resp.status_code}")
except Exception as e:
    print(f"Error: {e}")
