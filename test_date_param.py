#!/usr/bin/env python3
"""Test script to verify date parameter is working in options-init endpoint"""

import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://127.0.0.1:5000"

def test_endpoint(symbol="NIFTY", date_param=None):
    """Test the /api/options-init endpoint with optional date parameter"""
    url = f"{BASE_URL}/api/options-init?symbol={symbol}&price_source=previous_close"
    
    if date_param:
        url += f"&date={date_param}"
    
    print(f"\n{'='*60}")
    print(f"Testing: {url}")
    print(f"{'='*60}")
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            requested_price = data.get('underlying_price', {}).get('requested_price')
            print(f"✓ Status: {response.status_code}")
            print(f"  requested_price: {requested_price}")
            return requested_price
        else:
            print(f"✗ Status: {response.status_code}")
            print(f"  Response: {response.text}")
            return None
    except Exception as e:
        print(f"✗ Error: {e}")
        return None

# Test dates around Jan 14, 2026 (current date)
today = datetime(2026, 1, 14)
print(f"\nCurrent date: {today.strftime('%Y-%m-%d')} ({today.strftime('%A')})")

prices = {}

# Test without date (should use today's previous close, which is Jan 13 close)
print("\n" + "="*60)
print("TEST 1: No date parameter (should use current date)")
prices['no_date'] = test_endpoint()

# Test with today's date (Jan 14) - should get Jan 13 close
print("\n" + "="*60)
print("TEST 2: Today (Jan 14) - should get Jan 13 close")
prices['jan_14'] = test_endpoint(date_param="2026-01-14")

# Test with Jan 13 (Monday) - should get Jan 12 close (Friday)
print("\n" + "="*60)
print("TEST 3: Jan 13 (Monday) - should get Jan 12 close")
prices['jan_13'] = test_endpoint(date_param="2026-01-13")

# Test with Jan 12 (Friday) - should get Jan 09 close (Thursday) 
print("\n" + "="*60)
print("TEST 4: Jan 12 (Friday) - should get Jan 09 close")
prices['jan_12'] = test_endpoint(date_param="2026-01-12")

# Test with Jan 09 (Thursday) - should get Jan 08 close (Wednesday)
print("\n" + "="*60)
print("TEST 5: Jan 09 (Thursday) - should get Jan 08 close")
prices['jan_09'] = test_endpoint(date_param="2026-01-09")

# Test with older date - Jan 02 (Friday) - should get Jan 01 or Dec 31 close
print("\n" + "="*60)
print("TEST 6: Jan 02 (Friday) - should get Dec 31 close")
prices['jan_02'] = test_endpoint(date_param="2026-01-02")

# Summary
print(f"\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
print(f"No date parameter:   {prices.get('no_date')}")
print(f"Jan 14 (2026-01-14): {prices.get('jan_14')}")
print(f"Jan 13 (2026-01-13): {prices.get('jan_13')}")
print(f"Jan 12 (2026-01-12): {prices.get('jan_12')}")
print(f"Jan 09 (2026-01-09): {prices.get('jan_09')}")
print(f"Jan 02 (2026-01-02): {prices.get('jan_02')}")

# Check if prices are changing
all_same = all(p == prices.get('no_date') for p in prices.values())
if all_same:
    print(f"\n❌ PROBLEM: All prices are the same ({prices.get('no_date')}) - date parameter not working!")
else:
    print(f"\n✓ SUCCESS: Prices are different - date parameter is working!")
