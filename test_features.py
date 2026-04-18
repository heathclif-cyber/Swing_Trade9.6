import sys
sys.path.insert(0, r'D:\Apps-Dev\Swing_Trade9.6\ml')

import pandas as pd
from ml_feature_calculator import calculate_features_realtime

df_m15 = pd.read_parquet(r'D:\Apps-Dev\Pemodelan_swingtrade\data\raw\klines\SOLUSDT\15m_all.parquet')
features = calculate_features_realtime('SOLUSDT', df_m15.tail(300))
print(f'Kolom: {len(features.columns)}')
print(features.columns.tolist())
print(features.tail(1))