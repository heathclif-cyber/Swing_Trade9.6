import pandas as pd
import numpy as np
import algo_scoring
import json
from datetime import datetime

def generate_mock_data(n=300):
    """Generate mock 4H data for testing."""
    idx = pd.date_range(end=datetime.now(), periods=n, freq='4h')
    df = pd.DataFrame({
        'Timestamp': idx,
        'Open': np.random.uniform(50000, 60000, n),
        'High': np.random.uniform(60000, 61000, n),
        'Low': np.random.uniform(49000, 50000, n),
        'Close': np.random.uniform(50000, 60000, n),
        'Total_Volume': np.random.uniform(1000, 5000, n),
        'Buy_Volume': np.random.uniform(500, 2500, n),
        'Open_Interest': np.random.uniform(100000, 200000, n),
        'EMA_21': np.random.uniform(50000, 60000, n),
        'EMA_50': np.random.uniform(50000, 60000, n),
        'EMA_200': np.random.uniform(50000, 60000, n),
        'RSI_6': np.random.uniform(20, 80, n),
        'ATR_14': np.random.uniform(500, 1500, n),
    }, index=idx)
    df['CVD'] = (df['Buy_Volume'] - (df['Total_Volume'] - df['Buy_Volume'])).cumsum()
    return df

def test_scoring_live():
    print("🚀 Running Local Scoring Health Check (Agent Mode)...")
    df = generate_mock_data()
    meta = {'Symbol': 'BTCUSDT', 'AVG_ENTRY_PRICE': 55000}
    
    try:
        results = algo_scoring.calculate_71point_score(df, meta)
        if results:
            print("✅ Scoring Algorithm: OK")
            print(f"📊 LONG Score: {results['long']['total']}/71 ({results['long']['decision']})")
            print(f"📊 SHORT Score: {results['short']['total']}/71 ({results['short']['decision']})")
            # print(json.dumps(results, indent=2))
        else:
            print("❌ Scoring Algorithm: FAILED (Returned None)")
            exit(1)
    except Exception as e:
        print(f"❌ Scoring Algorithm: CRASHED - {e}")
        import traceback
        traceback.print_exc()
        exit(1)

if __name__ == "__main__":
    test_scoring_live()
