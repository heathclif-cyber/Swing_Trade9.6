import sys
sys.path.insert(0, r'D:\Apps-Dev\Swing_Trade9.6\ml')

import pandas as pd
import numpy as np
from ml_feature_calculator import calculate_features_realtime

# Load hasil feature_engineer (ground truth)
fe_path = r'D:\Apps-Dev\Pemodelan_swingtrade\data\labeled\SOLUSDT_features.parquet'
df_fe = pd.read_parquet(fe_path)

# Load raw M15 untuk input calculator
m15_path = r'D:\Apps-Dev\Pemodelan_swingtrade\data\raw\klines\SOLUSDT\15m_all.parquet'
df_m15 = pd.read_parquet(m15_path)

# Hitung fitur dengan calculator
features = calculate_features_realtime('SOLUSDT', df_m15.tail(300))

# Ambil 5 baris terakhir yang overlap
common_idx = df_fe.index.intersection(features.index)
if len(common_idx) == 0:
    print('Tidak ada index yang overlap!')
else:
    cols_to_check = ['ema_7_m15', 'ema_7_h4', 'rsi_6', 'cvd', 'PDH', 'PWL', 'atr_14_m15']
    print('Perbandingan 3 baris terakhir:')
    for col in cols_to_check:
        if col not in df_fe.columns or col not in features.columns:
            print(f'  {col}: kolom tidak ada di salah satu')
            continue
        fe_val  = df_fe.loc[common_idx[-3:], col]
        calc_val = features.loc[common_idx[-3:], col]
        diff = (fe_val - calc_val).abs().max()
        print(f'  {col}: max_diff={diff:.6f}')