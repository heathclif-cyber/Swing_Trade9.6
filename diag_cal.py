import warnings, traceback
warnings.filterwarnings('ignore')

try:
    import numpy as np
    import pandas as pd
    import torch
    import joblib
    from pathlib import Path
    import data_engine
    from ml.ml_feature_calculator import calculate_features_realtime
    from ml.ml_signal import TradingLSTM
    from core.helpers import load_inference_config

    print("Step 1: Load models...")
    ML_DIR = Path('models')
    lgbm   = joblib.load(ML_DIR / 'lgbm_baseline.pkl')
    scaler = joblib.load(ML_DIR / 'lstm_scaler.pkl')
    meta_l = joblib.load(ML_DIR / 'ensemble_meta.pkl')
    cal    = joblib.load(ML_DIR / 'calibrator.pkl')
    print("  Models loaded OK")

    cfg     = load_inference_config()
    n_feat  = cfg['model_architecture']['n_features']
    seq_len = cfg['inference']['seq_len']
    print(f"  n_features={n_feat}, seq_len={seq_len}")

    print("Step 2: Load LSTM...")
    lstm = TradingLSTM(n_features=n_feat)
    state = torch.load(ML_DIR / 'lstm_best.pt', map_location='cpu', weights_only=True)
    lstm.load_state_dict(state)
    lstm.eval()
    print("  LSTM loaded OK")

    print("Step 3: Fetch H1 data...")
    col_map = {
        'Open':'open','High':'high','Low':'low','Close':'close',
        'Total_Volume':'volume','Taker_Buy_Base':'taker_buy_volume',
    }
    df_raw = data_engine.get_klines_rest('ETHUSDT', '1h', limit=500)
    print(f"  Raw shape: {df_raw.shape}")
    df = df_raw.copy()
    df.columns = [col_map.get(c, c.lower()) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated(keep='first')]
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('open_time')
    df['open_interest'] = 500000000.0
    df['funding_rate']  = 0.0001
    df['btc_dominance'] = 62.0
    df['fear_greed']    = 45.0
    print("  Normalize OK")

    print("Step 4: Calculate features...")
    feat = calculate_features_realtime(
        'ETHUSDT', df, funding_rate=0.0001, btc_dominance=62.0, fear_greed=45.0
    )
    feat = feat.ffill().fillna(0)
    feat['symbol'] = 1
    print(f"  Features shape: {feat.shape}")

    print("Step 5: LGBM predict...")
    X_lgbm = feat.iloc[[-1]].copy()
    for col in lgbm.feature_name_:
        if col not in X_lgbm.columns:
            X_lgbm[col] = 0.0
    lgbm_proba = lgbm.predict_proba(X_lgbm[lgbm.feature_name_])
    print(f"  LGBM: SHORT={lgbm_proba[0,0]:.4f} FLAT={lgbm_proba[0,1]:.4f} LONG={lgbm_proba[0,2]:.4f}")

    print("Step 6: LSTM predict...")
    X_seq = feat[feat.columns[:n_feat]].iloc[-seq_len:].values.astype(np.float32)
    if len(X_seq) < seq_len:
        pad = np.zeros((seq_len - len(X_seq), X_seq.shape[1]), dtype=np.float32)
        X_seq = np.vstack([pad, X_seq])
    X_scaled = scaler.transform(X_seq)
    tensor = torch.FloatTensor(X_scaled).unsqueeze(0)
    with torch.no_grad():
        logits = lstm(tensor)
        lstm_proba = torch.softmax(logits, dim=1).numpy()
    print(f"  LSTM: SHORT={lstm_proba[0,0]:.4f} FLAT={lstm_proba[0,1]:.4f} LONG={lstm_proba[0,2]:.4f}")

    print("Step 7: Meta-learner...")
    meta_input = np.hstack([lgbm_proba, lstm_proba])
    meta_proba = meta_l.predict_proba(meta_input)
    print(f"  Meta: SHORT={meta_proba[0,0]:.4f} FLAT={meta_proba[0,1]:.4f} LONG={meta_proba[0,2]:.4f}")

    print("Step 8: Calibrator...")
    cal_proba = cal.transform(meta_proba)
    row_sum = cal_proba.sum(axis=1, keepdims=True)
    cal_proba = cal_proba / np.where(row_sum > 0, row_sum, 1)
    print(f"  Cal : SHORT={cal_proba[0,0]:.4f} FLAT={cal_proba[0,1]:.4f} LONG={cal_proba[0,2]:.4f}")

    label_map = {0:'SHORT', 1:'FLAT', 2:'LONG'}
    print(f"\nFinal (no cal): {label_map[int(np.argmax(meta_proba[0]))]} conf={max(meta_proba[0]):.4f}")
    print(f"Final (w/ cal): {label_map[int(np.argmax(cal_proba[0]))]} conf={max(cal_proba[0]):.4f}")

except Exception as e:
    print(f"\nERROR: {e}")
    traceback.print_exc()