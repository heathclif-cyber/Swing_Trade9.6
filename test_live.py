import urllib.request
import json
req = urllib.request.Request('https://swingtrade96-production-42ac.up.railway.app/api/scanner')
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        for d in data.get('data', []):
            print(f"{d['pair']}: L={d.get('ml_signal')} {d.get('ml_size')} S={d.get('ml_signal_s')} {d.get('ml_size_s')} LC={d.get('long_code')} SC={d.get('short_code')} Err={d.get('error')}")
except Exception as e:
    print('Error:', e)
