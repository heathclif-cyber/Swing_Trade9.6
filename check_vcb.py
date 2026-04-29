import sys, os
import logging
logging.basicConfig(level=logging.ERROR)
import data_engine
import protocol_96_enrichment
import algo_scoring
from ml.ml_signal import MLSignalEngine

engine = MLSignalEngine()
engine.cfg['type'] = 'lstm_only'
df_raw = data_engine.get_klines_rest('ETHUSDT', '1h', limit=500)
df_d1 = data_engine.get_klines_rest('ETHUSDT', '1d', limit=100)
df_w1 = data_engine.get_klines_rest('ETHUSDT', '1w', limit=100)

def normalize_h1(df):
    col_map = {'Open':'open','High':'high','Low':'low','Close':'close','Total_Volume':'volume','Taker_Buy_Base':'taker_buy_volume','Sell_Volume':'taker_sell_volume','Open_Time':'open_time'}
    df = df.copy()
    df.columns = [col_map.get(c, c.lower()) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated(keep='first')]
    import pandas as pd
    if 'open_time' in df.columns:
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
        df = df.set_index('open_time')
    return df

df_m15 = normalize_h1(df_raw)
# Let's bypass enrichment if it's too much, but we need ATR_14 for VCB.
# Actually algo_scoring._score recalculates CVD and expects ATR_14.
# We will manually calculate ATR_14 just for testing VCB.

import pandas as pd
df = df_raw.copy()
def calc_atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    rma = tr.copy() * float('nan')
    rma.iloc[period - 1] = tr.iloc[:period].mean()
    for i in range(period, len(tr)):
        rma.iloc[i] = (rma.iloc[i - 1] * (period - 1) + tr.iloc[i]) / period
    return rma

df['ATR_14'] = calc_atr(df['High'], df['Low'], df['Close'], 14)
df['CVD'] = 0.0

score = algo_scoring._score(df, {'Symbol': 'ETHUSDT'}, df_m15=df_m15, ml_engine=engine)

if score:
    print(f"ML Signal L: {score['long']['ml_signal']} Size: {score['long']['ml_size']}")
    print(f"ML Signal S: {score['short']['ml_signal']} Size: {score['short']['ml_size']}")
    print(f"VCB Active: {score['variables'].get('vcb_active') if 'variables' in score else 'Unknown'}")
    print(f"Gate Reason L: {score['long']['gate']['reason']}")
    print(f"Gate Reason S: {score['short']['gate']['reason']}")
else:
    print("Score is None")
