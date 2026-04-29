import sys, os
import logging
logging.basicConfig(level=logging.INFO)
import data_engine
from ml.ml_signal import MLSignalEngine
import algo_scoring

print('Fetching data...')
df_raw = data_engine.get_klines_rest('ETHUSDT', '1h', limit=300)

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

df_m15 = normalize_h1(df_raw)
print(f'Data shape: {df_m15.shape}')

engine = MLSignalEngine()
print(f"Engine active: {engine.cfg.get('type', 'ensemble')}")

res = engine.predict('ETHUSDT', df_m15)
print('MLSignalEngine predict result:')
print(res)

print('\nTesting algo_scoring...')
meta = {'Symbol': 'ETHUSDT'}
import protocol_96_enrichment
print('Enriching data...')
df_enriched = protocol_96_enrichment.enrich_data('ETHUSDT', df_raw)

score_res = algo_scoring.calculate_71point_score(df_enriched, meta, df_m15=df_m15, ml_engine=engine)
print('\nAlgo Scoring Result:')
if score_res:
    long_code = score_res.get('long', {}).get('code')
    short_code = score_res.get('short', {}).get('code')
    ml_error = score_res.get('validation', {}).get('issues')
    vcb_active = score_res.get('variables', {}).get('vcb_active')
    print(f'LONG Code: {long_code}')
    print(f'SHORT Code: {short_code}')
    print(f'ML Error: {ml_error}')
    print(f'VCB Active: {vcb_active}')
else:
    print('Failed to score.')
