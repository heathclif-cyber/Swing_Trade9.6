import sys, os
import logging
logging.basicConfig(level=logging.ERROR)
import data_engine
from ml.ml_signal import MLSignalEngine

engine = MLSignalEngine()

coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
print(f"Engine active: {engine.cfg.get('type', 'ensemble')}")

def normalize_h1(df):
    col_map = {
        'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close',
        'Total_Volume': 'volume', 'Taker_Buy_Base': 'taker_buy_volume',
        'Sell_Volume': 'taker_sell_volume', 'Open_Time': 'open_time',
    }
    df = df.copy()
    df.columns = [col_map.get(c, c.lower()) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated(keep='first')]
    import pandas as pd
    if 'open_time' in df.columns:
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
        df = df.set_index('open_time')
        df.index.name = 'timestamp'
    return df

for coin in coins:
    df_raw = data_engine.get_klines_rest(coin, '1h', limit=300)
    df_m15 = normalize_h1(df_raw)
    res = engine.predict(coin, df_m15)
    print(f"[{coin}] {res['signal']} (Conf: {res['confidence']:.2%}) -> Size: {res['size']}")
