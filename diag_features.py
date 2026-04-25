import warnings
warnings.filterwarnings('ignore')
import pandas as pd
import data_engine
from ml.ml_feature_calculator import calculate_features_realtime

# Normalize columns (inline, tanpa import private function)
def normalize_h1(df):
    col_map = {
        'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close',
        'Total_Volume': 'volume', 'Taker_Buy_Base': 'taker_buy_volume',
        'Sell_Volume': 'taker_sell_volume', 'Open_Time': 'open_time',
    }
    df = df.copy()
    df.columns = [col_map.get(c, c.lower()) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated(keep='first')]
    if 'open_time' in df.columns:
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
        df = df.set_index('open_time')
        df.index.name = 'timestamp'
    return df

df_raw = data_engine.get_klines_rest('ETHUSDT', '1h', limit=500)
df_h1  = normalize_h1(df_raw)
print('H1 shape:', df_h1.shape)
print('Index type:', type(df_h1.index))

feat = calculate_features_realtime('ETHUSDT', df_h1)
print('Features shape:', feat.shape)

nan_counts = feat.isnull().sum()
print('\nNaN count per fitur (yang ada NaN):')
print(nan_counts[nan_counts > 0])

last = feat.iloc[-1]
print('\nLast row sample:')
for col in ['close','rsi_6','cvd','ema_7_h1','wyckoff_phase','spring_upthrust','absorption_z','vol_efficiency']:
    print(f'  {col}: {last[col]:.6f}')