import sys, os
import logging
logging.basicConfig(level=logging.ERROR)
import data_engine
from ml.ml_signal import MLSignalEngine
import algo_scoring
import protocol_96_enrichment

engine = MLSignalEngine()
engine.cfg['type'] = 'lstm_only'
df_raw = data_engine.get_klines_rest('ETHUSDT', '1h', limit=500)
df_d1 = data_engine.get_klines_rest('ETHUSDT', '1d', limit=100)
df_w1 = data_engine.get_klines_rest('ETHUSDT', '1w', limit=100)

df_enriched = protocol_96_enrichment.enrich_dataset('ETHUSDT', df_raw, df_d1, df_w1)

score = algo_scoring._score(df_enriched, {'Symbol': 'ETHUSDT'}, df_m15=df_raw.copy(), ml_engine=engine)
if score:
    print(f"ML Signal L: {score['long']['ml_signal']} Size: {score['long']['ml_size']}")
    print(f"ML Signal S: {score['short']['ml_signal']} Size: {score['short']['ml_size']}")
    print(f"L Score: {score['long']['code']} S Score: {score['short']['code']}")
