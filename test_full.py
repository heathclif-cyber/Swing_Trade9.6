import sys, os
sys.path.insert(0, r'D:\Apps-Dev\Swing_Trade9.6')
os.chdir(r'D:\Apps-Dev\Swing_Trade9.6')

import pandas as pd
import algo_scoring
import protocol_96_enrichment as enrichment
from ml.ml_signal import MLSignalEngine
from protocol_96_ui import get_klines_rest, _normalize_m15_columns

engine = MLSignalEngine()
pair = 'SOLUSDT'

print('Step 1: Fetch 4h...')
df_quant, meta_info = enrichment.get_fully_enriched_data(pair, interval='4h', limit=250)
print(f'df_quant shape: {df_quant.shape}')

print('Step 2: Fetch M15...')
df_m15_raw = get_klines_rest(pair, '15m', limit=300)
df_m15 = _normalize_m15_columns(df_m15_raw)
print(f'df_m15 shape: {df_m15.shape}')
print(f'df_m15 index type: {type(df_m15.index).__name__}')

print('Step 3: ML predict direct...')
result = engine.predict(pair, df_m15)
print(f'signal={result["signal"]} conf={result["confidence"]} size={result["size"]}')

print('Step 4: algo_scoring dengan ml_engine...')
meta = {'Symbol': pair, 'AVG_ENTRY_PRICE': None, 'ENTRY_DATE': None}
score_res = algo_scoring.calculate_71point_score(df_quant, meta, df_m15=df_m15, ml_engine=engine)
print(f'ml_signal: {score_res["long"].get("ml_signal")}')
print(f'ml_confidence: {score_res["long"].get("ml_confidence")}')
print(f'ml_size: {score_res["long"].get("ml_size")}')