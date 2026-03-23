import requests

try:
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": "BTCDOMUSDT", "interval": "1d", "limit": 1}
    r = requests.get(url, params=params, verify=False)
    print("BTCDOMUSDT:", r.json())
except Exception as e:
    print(e)
