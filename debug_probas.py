import sys, os, json
import numpy as np
import torch
import pandas as pd
import logging
logging.basicConfig(level=logging.ERROR)
import data_engine
from ml.ml_signal import MLSignalEngine

engine = MLSignalEngine()
coin = 'BTCUSDT'
df_raw = data_engine.get_klines_rest(coin, '1h', limit=300)

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

from ml.ml_feature_calculator import calculate_features_realtime
features_df = calculate_features_realtime(coin, df_m15, None, None, None)
features_df = features_df.ffill().fillna(0)
features_df['symbol'] = 0

X_lgbm = features_df.iloc[[-1]].copy()
lgbm_feat_cols = engine.lgbm_model.feature_name_
for col in lgbm_feat_cols:
    if col not in X_lgbm.columns:
        X_lgbm[col] = 0.0
lgbm_proba = engine.lgbm_model.predict_proba(X_lgbm[lgbm_feat_cols])

with open(engine.cfg.get('features', 'models/feature_cols_v2.json')) as f:
    feat_cols_canonical = json.load(f)
for c in feat_cols_canonical:
    if c not in features_df.columns:
        features_df[c] = 0.0

X_seq = features_df[feat_cols_canonical].iloc[-32:].values.astype(np.float32)
if len(X_seq) < 32:
    pad = np.zeros((32 - len(X_seq), X_seq.shape[1]), dtype=np.float32)
    X_seq = np.vstack([pad, X_seq])

X_seq_scaled = engine.lstm_scaler.transform(X_seq)
seq_tensor = torch.FloatTensor(X_seq_scaled).unsqueeze(0)
with torch.no_grad():
    logits = engine.lstm_model(seq_tensor)
    lstm_proba = torch.softmax(logits, dim=1).numpy()

meta_input = np.hstack([lgbm_proba, lstm_proba])
meta_proba = engine.meta_learner.predict_proba(meta_input)

if engine.calibrator is not None:
    cal_proba = engine.calibrator.transform(meta_proba)
    row_sum = cal_proba.sum(axis=1, keepdims=True)
    cal_proba = cal_proba / np.where(row_sum > 0, row_sum, 1)
else:
    cal_proba = meta_proba

print(f"LGBM Proba: {lgbm_proba}")
print(f"LSTM Proba: {lstm_proba}")
print(f"META Proba: {meta_proba}")
print(f"CAL  Proba: {cal_proba}")
