import sys
sys.path.insert(0, r'D:\Apps-Dev\Swing_Trade9.6')
sys.path.insert(0, r'D:\Apps-Dev\Swing_Trade9.6\ml')

import pandas as pd
from ml.ml_signal import MLSignalEngine

# Init engine
engine = MLSignalEngine()

# Load data test
df_m15 = pd.read_parquet(r'D:\Apps-Dev\Pemodelan_swingtrade\data\raw\klines\SOLUSDT\15m_all.parquet')

# Test predict
result = engine.predict('SOLUSDT', df_m15.tail(300))

print('\n=== HASIL PREDIKSI ===')
print(f"Signal    : {result['signal']}")
print(f"Confidence: {result['confidence']}")
print(f"Size      : {result['size']}")
print(f"Proba     : {result['proba']}")
print(f"Model type: {result['model_type']}")
print(f"Symbol    : {result['symbol']}")