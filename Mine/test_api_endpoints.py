#!/usr/bin/env python3
"""
Test script to verify the intraday option API endpoints are working correctly.
"""

import requests
import json
from datetime import datetime

# Configuration
BASE_URL = "http://127.0.0.1:5000"
SYMBOL = "NIFTY"
TIMEFRAME = "5minute"
CE_STRIKE = 26050
PE_STRIKE = 26100

print("=" * 80)
print("INTRADAY OPTION API TEST")
print("=" * 80)
print(f"\nTest Date/Time: {datetime.now().strftime('%A, %B %d, %Y at %H:%M:%S')}")
print(f"\nTest Parameters:")
print(f"  Symbol: {SYMBOL}")
print(f"  Timeframe: {TIMEFRAME}")
print(f"  CE Strike: {CE_STRIKE}")
print(f"  PE Strike: {PE_STRIKE}")

print("\n" + "=" * 80)
print("STEP 1: Check Authentication")
print("=" * 80)

# Try to call an endpoint that requires authentication
response = requests.get(f"{BASE_URL}/api/intraday-option/debug-strikes?symbol={SYMBOL}")
result = response.json()

if result.get('auth_error'):
    print(f"❌ Authentication Error: {result.get('error')}")
    print("\nYou need to be logged in to test the API.")
    print("Please:")
    print("  1. Go to http://127.0.0.1:5000/auth/login")
    print("  2. Login with your credentials")
    print("  3. Then the API will work")
    exit(1)
else:
    print("✓ Authentication successful")

print("\n" + "=" * 80)
print("STEP 2: Check Available Strikes (Debug Endpoint)")
print("=" * 80)

response = requests.get(f"{BASE_URL}/api/intraday-option/debug-strikes?symbol={SYMBOL}")
if response.status_code == 200:
    result = response.json()
    if result.get('success'):
        print(f"✓ Debug endpoint working")
        print(f"\nUnderlying Price: {result.get('underlying_price')}")
        print(f"ATM CE Strike: {result.get('atm_ce_strike')}")
        print(f"ATM PE Strike: {result.get('atm_pe_strike')}")
        
        available = result.get('available_strikes', [])
        ce_available = [s for s in available if s.get('ce_available')]
        pe_available = [s for s in available if s.get('pe_available')]
        
        print(f"\nAvailable CE Strikes ({len(ce_available)}):")
        ce_strikes = [s['strike'] for s in ce_available]
        print(f"  {ce_strikes}")
        
        print(f"\nAvailable PE Strikes ({len(pe_available)}):")
        pe_strikes = [s['strike'] for s in pe_available]
        print(f"  {pe_strikes}")
        
        # Check if our test strikes are available
        print(f"\n{'Status of Test Strikes:':−^80}")
        print(f"  CE Strike {CE_STRIKE}: {'✓ Available' if CE_STRIKE in ce_strikes else '✗ NOT Available'}")
        print(f"  PE Strike {PE_STRIKE}: {'✓ Available' if PE_STRIKE in pe_strikes else '✗ NOT Available'}")
        
        if CE_STRIKE not in ce_strikes or PE_STRIKE not in pe_strikes:
            print(f"\n⚠ One or both test strikes are not available!")
            if ce_available:
                print(f"  Try using CE strike: {ce_available[0]['strike']}")
            if pe_available:
                print(f"  Try using PE strike: {pe_available[0]['strike']}")
    else:
        print(f"✗ Debug endpoint error: {result.get('error')}")
else:
    print(f"✗ Failed to call debug endpoint: HTTP {response.status_code}")

print("\n" + "=" * 80)
print("STEP 3: Try to Fetch Option Data")
print("=" * 80)

api_url = f"{BASE_URL}/api/intraday-option?symbol={SYMBOL}&timeframe={TIMEFRAME}&ce_strike={CE_STRIKE}&pe_strike={PE_STRIKE}"
print(f"\nAPI URL:")
print(f"  {api_url}\n")

response = requests.get(api_url)
result = response.json()

if result.get('success'):
    print(f"✓ Option data fetched successfully!")
    data = result.get('data', {})
    
    print(f"\nCE Data:")
    print(f"  Strike: {data.get('ce_strike')}")
    print(f"  Symbol: {data.get('ce_symbol')}")
    print(f"  Current Price: {data.get('ce_data', {}).get('current_price')}")
    print(f"  PDH: {data.get('ce_data', {}).get('pdh')}")
    print(f"  PDL: {data.get('ce_data', {}).get('pdl')}")
    
    print(f"\nPE Data:")
    print(f"  Strike: {data.get('pe_strike')}")
    print(f"  Symbol: {data.get('pe_symbol')}")
    print(f"  Current Price: {data.get('pe_data', {}).get('current_price')}")
    print(f"  PDH: {data.get('pe_data', {}).get('pdh')}")
    print(f"  PDL: {data.get('pe_data', {}).get('pdl')}")
else:
    print(f"✗ Failed to fetch option data")
    error = result.get('error') or result.get('data', {}).get('error', 'Unknown error')
    print(f"\nError: {error}")
    
    # Provide recommendations
    print(f"\n📝 Recommendations:")
    print(f"  1. Check the debug endpoint to see available strikes")
    print(f"  2. Use only strikes that show as 'Available'")
    print(f"  3. Ensure the Kite market is open (9:15 AM - 3:30 PM IST)")
    print(f"  4. Check server logs for detailed symbol matching information")

print("\n" + "=" * 80)
