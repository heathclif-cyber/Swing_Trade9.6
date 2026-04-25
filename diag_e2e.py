import warnings
warnings.filterwarnings('ignore')
import protocol_96_enrichment as enrichment
import algo_scoring
from ml.ml_signal import MLSignalEngine

ml_engine = MLSignalEngine()

symbol = 'ETHUSDT'
print(f"Testing {symbol}...")

# 1. Enrichment (sama persis dengan production path)
df, meta = enrichment.get_fully_enriched_data(symbol, interval='1h', limit=500)
print(f"Enriched shape: {df.shape} | missing: {meta.get('missing_data')}")

# 2. Normalize untuk ML (sama dengan signal_monitor)
import pandas as pd, data_engine
col_map = {
    'Open':'open','High':'high','Low':'low','Close':'close',
    'Total_Volume':'volume','Taker_Buy_Base':'taker_buy_volume',
    'Sell_Volume':'taker_sell_volume','Open_Time':'open_time',
}
df_raw = data_engine.get_klines_rest(symbol, '1h', limit=500)
df_m15 = df_raw.copy()
df_m15.columns = [col_map.get(c, c.lower()) for c in df_m15.columns]
df_m15 = df_m15.loc[:, ~df_m15.columns.duplicated(keep='first')]
if 'open_time' in df_m15.columns:
    df_m15['open_time'] = pd.to_datetime(df_m15['open_time'], unit='ms', utc=True)
    df_m15 = df_m15.set_index('open_time')
    df_m15.index.name = 'timestamp'

# 3. Scoring (akan inject fitur dari df enrichment ke df_m15)
meta_score = {
    'Symbol': symbol,
    'AVG_ENTRY_PRICE': None,
    'ENTRY_DATE': None,
}
result = algo_scoring.calculate_71point_score(df, meta_score, df_m15=df_m15, ml_engine=ml_engine)

if result:
    print(f"\nML Signal : {result['long'].get('ml_signal')}")
    print(f"ML Size   : {result['long'].get('ml_size')}")
    print(f"Confidence: {result['long'].get('ml_confidence')*100:.1f}%")
    print(f"Proba     : {result['long'].get('ml_proba')}")
    print(f"ML Error  : {result['variables'].get('ml_error')}")
else:
    print("Scoring returned None")