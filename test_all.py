import protocol_96_ui
import algo_scoring
import traceback

pairs = ['SUIUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'PENDLEUSDT', 'DOGEUSDT', 'LINKUSDT', 'WLFIUSDT', 'ETHUSDT']

for p in pairs:
    try:
        df = protocol_96_ui.get_klines_df(p, '4h', 250)
        if len(df) == 0:
            print(p, 'NO DATA FROM API')
            continue
        df = protocol_96_ui.apply_full_indicators(df)
        df = protocol_96_ui.enrichment.apply_temporal_alignment(df)
        meta = {'Symbol': p, 'AVG_ENTRY_PRICE': None}
        res = algo_scoring.calculate_71point_score(df, meta)
        print(p, 'SUCCESS' if res else 'NO RESULT')
    except Exception as e:
        print(p, 'ERROR')
        traceback.print_exc()
