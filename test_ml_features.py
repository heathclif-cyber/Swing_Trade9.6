import joblib, json, torch
import numpy as np
import pandas as pd
from pathlib import Path

ML_DIR = Path("models")

# Test 1: Cek n_features di LGBM
lgbm = joblib.load(ML_DIR / "lgbm_baseline.pkl")
print(f"LGBM n_features: {lgbm.n_features_in_}")

# Test 2: Cek shape scaler
scaler = joblib.load(ML_DIR / "lstm_scaler.pkl")
print(f"Scaler n_features: {scaler.n_features_in_}")

# Test 3: Cek feature_cols_v2.json
with open(ML_DIR / "feature_cols_v2.json") as f:
    feat_cols = json.load(f)
print(f"feature_cols_v2 count: {len(feat_cols)}")

# Test 4: Cek LSTM input size
state = torch.load(ML_DIR / "lstm_best.pt", map_location="cpu", weights_only=True)
for k, v in state.items():
    if "weight_ih_l0" in k:
        print(f"LSTM input size: {v.shape[1]}")
        break

# Test 5: Cek meta learner
meta = joblib.load(ML_DIR / "ensemble_meta.pkl")
print(f"Meta learner n_features: {meta.n_features_in_}")

# Test 6: Cek apakah ml_feature_calculator menghasilkan 65 fitur
import sys
sys.path.insert(0, '.')
try:
    from ml.ml_feature_calculator import calculate_features_realtime, FEATURE_COLS
    print(f"ml_feature_calculator FEATURE_COLS count: {len(FEATURE_COLS)}")
    
    # Mock dataframe untuk tes kalkulasi
    df_mock = pd.DataFrame({
        'open': np.random.rand(300),
        'high': np.random.rand(300),
        'low': np.random.rand(300),
        'close': np.random.rand(300),
        'volume': np.random.rand(300),
    })
    df_mock.index = pd.date_range('2023-01-01', periods=300, freq='15min', tz='UTC')
    out_df = calculate_features_realtime('BTCUSDT', df_mock)
    print(f"Generated features count: {len(out_df.columns)}")
except Exception as e:
    print(f"Test 6 failed: {e}")
