import protocol_96_ui
import json
import traceback

pairs = ['SUIUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'PENDLEUSDT', 'DOGEUSDT', 'LINKUSDT', 'WLFIUSDT', 'ETHUSDT']

test_client = protocol_96_ui.app.test_client()

for p in pairs:
    try:
        resp = test_client.get(f'/api/data?pair={p}')
        data = resp.get_json()
        qa = data.get('state', {}).get('quant_analysis')
        if qa is None:
            print(f'{p}: DATA INSUFFICIENT (quant_analysis is None)')
            # Is it because of df length?
        else:
            print(f'{p}: SUCCESS')
    except Exception as e:
        print(f'{p}: CRASH')
        traceback.print_exc()
