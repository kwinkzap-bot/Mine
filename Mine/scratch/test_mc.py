
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime

def get_fii_dii_moneycontrol():
    url = "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return {"error": f"Status code {resp.status_code}"}
            
        # We need to parse the HTML table
        # Since I don't have bs4 installed in the venv (maybe?), let's check.
        return {"content_length": len(resp.text), "sample": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print(json.dumps(get_fii_dii_moneycontrol(), indent=2))
