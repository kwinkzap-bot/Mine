import sys
import os
import time

# Add workspace to path
sys.path.insert(0, '/Users/kavinkumar/Mine/Mine/src')

from dotenv import load_dotenv
# Load environment
load_dotenv('/Users/kavinkumar/Mine/Mine/env/Mine.env')

from trading_app.service.provider_logic import get_data_provider

provider = get_data_provider()
print(f"Provider: {provider}")
if provider:
    print(f"Fyers instance: {provider.fyers}")
    symbols = ["NSE:NIFTY50-INDEX", "NSE:RELIANCE-EQ"]
    resp = provider.fyers.quotes(data={"symbols": ",".join(symbols)})
    print(f"Quotes Response: {resp}")
