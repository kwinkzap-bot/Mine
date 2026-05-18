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
if provider:
    nifty50_symbols = [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC', 'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT', 'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL', 'ONGC', 'TECHM', 'DRREDDY', 'COALINDIA', 'ADANIPOWER', 'CIPLA', 'BPCL', 'HINDUNILVR', 'BRITANNIA', 'NESTLEIND', 'TATAMOTORS', 'EICHERMOT', 'HEROMOTOCO', 'APOLLOHOSP', 'DIVISLAB', 'UPL', 'BAJAJ-AUTO', 'LTIM', 'SBILIFE', 'HDFCLIFE'
    ]
    index_map = {
        'NIFTY':      'NSE:NIFTY50-INDEX',
        'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
        'FINNIFTY':   'NSE:FINNIFTY-INDEX',
        'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
        'SENSEX':     'BSE:SENSEX-INDEX',
        'INDIAVIX':   'NSE:INDIAVIX-INDEX'
    }
    
    all_tokens = list(index_map.values()) + [f"NSE:{s}-EQ" for s in nifty50_symbols]
    print(f"Calling quote() for {len(all_tokens)} symbols...")
    
    # We will bypass the cache to force the API call
    from trading_app.service.fyers_data_service import _FYERS_SYMBOL_CACHE
    _FYERS_SYMBOL_CACHE.clear()
    
    t0 = time.time()
    try:
        res = provider.quote(all_tokens)
        print(f"Result: {len(res)} quotes returned in {time.time() - t0:.2f}s")
        if len(res) == 0:
            print("Zero quotes returned! Let's see why.")
    except Exception as e:
        print(f"Exception during quote: {e}")
        import traceback
        traceback.print_exc()
