#!/usr/bin/env python3
"""Direct test of the get_previous_trading_day_close method"""

import sys
import os
sys.path.insert(0, '/Users/kavinkumar/Mine/Mine/src')

# Set up logging to stdout
import logging
logging.basicConfig(level=logging.DEBUG, format='%(name)s - %(levelname)s - %(message)s')

from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv('/Users/kavinkumar/Mine/.env')

# Initialize KiteConnect
from kiteconnect import KiteConnect

api_key = os.getenv("API_KEY")
access_token = os.getenv("ACCESS_TOKEN")

print(f"API_KEY: {api_key[:10]}..." if api_key else "API_KEY: Not set")
print(f"ACCESS_TOKEN: {access_token[:20]}..." if access_token else "ACCESS_TOKEN: Not set")

kite = KiteConnect(api_key=api_key)
if access_token:
    kite.set_access_token(access_token)

# Create KiteService
from trading_app.service.kite_service import KiteService
kite_service = KiteService(kite)

print("\n" + "="*60)
print("Testing get_previous_trading_day_close directly")
print("="*60)

# Test 1: Without date (current)
print("\nTest 1: get_previous_trading_day_close('NIFTY')")
price1 = kite_service.get_previous_trading_day_close('NIFTY')
print(f"Result: {price1}")

# Test 2: With date 2026-01-12
print("\nTest 2: get_previous_trading_day_close('NIFTY', target_date='2026-01-12')")
price2 = kite_service.get_previous_trading_day_close('NIFTY', target_date='2026-01-12')
print(f"Result: {price2}")

# Test 3: With date 2026-01-09
print("\nTest 3: get_previous_trading_day_close('NIFTY', target_date='2026-01-09')")
price3 = kite_service.get_previous_trading_day_close('NIFTY', target_date='2026-01-09')
print(f"Result: {price3}")

# Test get_instrument_token
print("\n" + "="*60)
print("Checking instrument token lookup")
print("="*60)
token = kite_service.get_instrument_token('NIFTY')
print(f"Token for NIFTY: {token}")

print(f"\nInstrument tokens by symbol (first 5): {list(kite_service._instrument_tokens_by_symbol.items())[:5]}")
print(f"Instrument tokens by name (first 5): {list(kite_service._instrument_tokens_by_name.items())[:5]}")
