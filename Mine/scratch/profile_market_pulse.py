import sys
import os
import time

# Add workspace to path
sys.path.insert(0, '/Users/kavinkumar/Mine/Mine/src')

from dotenv import load_dotenv
# Load environment
load_dotenv('/Users/kavinkumar/Mine/Mine/env/Mine.env')

from trading_app.service.provider_logic import get_data_provider, get_kite
from trading_app.service.institutional_service import InstitutionalService
from trading_app.app.routes.api import get_nifty_cpr_levels

def run_profile():
    print("Initializing Data Provider...")
    t0 = time.time()
    provider = get_data_provider()
    print(f"Provider initialized in {time.time() - t0:.2f}s: {provider.__class__.__name__ if provider else 'None'}")
    
    if not provider:
        print("Data provider is None. Trying fallback Kite...")
        provider = get_kite(user='Mine')
        print(f"Fallback provider: {provider.__class__.__name__ if provider else 'None'}")
        
    if not provider:
        print("No provider available.")
        return
        
    provider_name = provider.__class__.__name__.lower()
    is_kite = 'kite' in provider_name
    
    if is_kite:
        index_map = {
            'NIFTY':      'NSE:NIFTY 50',
            'BANKNIFTY':  'NSE:NIFTY BANK',
            'FINNIFTY':   'NSE:NIFTY FIN SERVICE',
            'MIDCPNIFTY': 'NSE:NIFTY MID SELECT',
            'SENSEX':     'BSE:SENSEX',
            'INDIAVIX':   'NSE:INDIA VIX'
        }
    else:
        index_map = {
            'NIFTY':      'NSE:NIFTY50-INDEX',
            'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':   'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':     'BSE:SENSEX-INDEX',
            'INDIAVIX':   'NSE:INDIAVIX-INDEX'
        }
    
    nifty50_symbols = [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC', 'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT', 'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL', 'ONGC', 'TECHM', 'DRREDDY', 'COALINDIA', 'ADANIPOWER', 'CIPLA', 'BPCL', 'HINDUNILVR', 'BRITANNIA', 'NESTLEIND', 'TATAMOTORS', 'EICHERMOT', 'HEROMOTOCO', 'APOLLOHOSP', 'DIVISLAB', 'UPL', 'BAJAJ-AUTO', 'LTIM', 'SBILIFE', 'HDFCLIFE'
    ]
    
    index_tokens = list(index_map.values())
    if not is_kite:
        stock_tokens = [f"NSE:{sym}-EQ" for sym in nifty50_symbols]
    else:
        stock_tokens = [f"NSE:{sym}" for sym in nifty50_symbols]
        
    all_tokens = index_tokens + stock_tokens
    
    # 1. Profile Quotes
    print(f"Fetching quotes for {len(all_tokens)} tokens...")
    t_start = time.time()
    try:
        raw_quotes = provider.quote(all_tokens)
        print(f"Quotes fetched successfully in {time.time() - t_start:.2f}s. Total quotes: {len(raw_quotes)}")
    except Exception as e:
        print(f"Error fetching quotes: {e}")
        
    # 2. Profile Institutional Flow
    print("Fetching institutional flow data...")
    t_start = time.time()
    try:
        inst = InstitutionalService.get_latest_data()
        print(f"Institutional flow fetched in {time.time() - t_start:.2f}s: {inst.get('date')}")
    except Exception as e:
        print(f"Error fetching institutional: {e}")
        
    # 3. Profile CPR levels
    print("Fetching NIFTY CPR levels...")
    t_start = time.time()
    try:
        cpr = get_nifty_cpr_levels(provider)
        print(f"CPR levels fetched in {time.time() - t_start:.2f}s: {cpr.keys() if cpr else 'None'}")
    except Exception as e:
        print(f"Error fetching CPR: {e}")

if __name__ == '__main__':
    run_profile()
