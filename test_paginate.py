from binance.client import Client
import requests

def get_huge_klines(symbol, interval, limit):
    client = Client()
    total = []
    end_time = None
    while len(total) < limit:
        req = min(1000, limit - len(total))
        pr = {'symbol':symbol, 'interval':interval, 'limit':req}
        if end_time: pr['endTime'] = end_time
        res = client.get_klines(**pr)
        if not res: break
        total = res + total
        if len(res) < req: break
        end_time = res[0][0] - 1
    return len(total)
print(get_huge_klines("BTCUSDT", "4h", 3000))
