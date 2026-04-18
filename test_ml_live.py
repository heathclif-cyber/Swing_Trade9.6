import sys, os
sys.path.insert(0, os.path.abspath('.'))

from ml.ml_signal import MLSignalEngine
from data_engine import get_klines_rest

engine = MLSignalEngine()

coins = ['SOLUSDT', 'SUIUSDT', 'XRPUSDT']

for symbol in coins:
    print(f'\n=== {symbol} ===')
    try:
        df_raw = get_klines_rest(symbol, '15m', limit=300)
        print(f'Raw columns: {list(df_raw.columns)}')
        print(f'Raw shape: {df_raw.shape}')

        col_map = {
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Total_Volume': 'volume',
            'Taker_Buy_Base': 'taker_buy_volume',
            'Sell_Volume': 'taker_sell_volume',
            'Open_Time': 'open_time',
        }
        df = df_raw.copy()
        df.columns = [col_map.get(c, c.lower()) for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated(keep='first')]
        print(f'Normalized columns: {list(df.columns)}')

        result = engine.predict(symbol, df)
        print(f'Signal: {result["signal"]} | Conf: {result["confidence"]} | Size: {result["size"]}')
    except Exception as e:
        import traceback
        print(f'ERROR: {e}')
        traceback.print_exc()