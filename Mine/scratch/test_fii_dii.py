
import requests
import json
from datetime import datetime

def get_fii_dii():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'application/json, text/plain, */*',
    }
    
    session = requests.Session()
    # Visit homepage to get cookies
    session.get("https://www.nseindia.com", headers=headers, timeout=10)
    
    # Fetch FII/DII data
    url = "https://www.nseindia.com/api/fiidiiTradeDetails"
    resp = session.get(url, headers=headers, timeout=10)
    
    if resp.status_code == 200:
        return resp.json()
    else:
        return {"error": f"Status code {resp.status_code}"}

if __name__ == "__main__":
    data = get_fii_dii()
    print(json.dumps(data, indent=2))
