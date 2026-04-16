import requests
import json
res = requests.get('http://127.0.0.1:5000/api/oi-profile/candles?symbol=NIFTY&interval=5minute&days=1')
data = res.json()
print("Intrinsic Data:")
print(json.dumps(data.get('intrinsic', {}), indent=2))
