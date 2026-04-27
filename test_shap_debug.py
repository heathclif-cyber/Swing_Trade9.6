import sys
import os
import logging
import traceback
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO)

try:
    from ml.ml_signal import MLSignalEngine
    import protocol_96_enrichment as enrichment
    
    engine = MLSignalEngine()
    
    print("Mengambil data real...")
    df_raw = enrichment.get_klines_rest("SUIUSDT", "1h", limit=500)
    
    def _normalize_m15_columns(df):
        import pandas as pd
        col_map = {
            'Open':           'open',
            'High':           'high',
            'Low':            'low',
            'Close':          'close',
            'Total_Volume':   'volume',
            'Taker_Buy_Base': 'taker_buy_volume',
            'Sell_Volume':    'taker_sell_volume',
            'Open_Time':      'open_time',
        }
        df = df.copy()
        df.columns = [col_map.get(c, c.lower()) for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated(keep='first')]
        if 'open_time' in df.columns:
            df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
            df = df.set_index('open_time')
            df.index.name = 'timestamp'
        return df
        
    df_m15_norm = _normalize_m15_columns(df_raw)
    
    print("Mencoba predict()...")
    res = engine.predict("SUIUSDT", df_m15_norm)
    
    print("\n--- HASIL ---")
    print("Keys in result:", res.keys())
    print("shap_top_features =", json.dumps(res.get("shap_top_features", []), indent=2))
except Exception as e:
    traceback.print_exc()
