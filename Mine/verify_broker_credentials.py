#!/usr/bin/env python3
"""
Verify broker credentials are properly loaded and valid.
Run this before starting live trading to ensure all brokers are ready.
"""

import os
import sys
from dotenv import load_dotenv
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

load_dotenv()

def check_credentials():
    """Check if all broker credentials are properly loaded."""
    print("\n" + "="*70)
    print("BROKER CREDENTIAL VERIFICATION")
    print("="*70 + "\n")
    
    results = {
        'KITE': False,
        'DHAN': False,
        'FYERS': False,
        'KOTAK': False
    }
    
    # Check KITE (main broker)
    print("📊 CHECKING KITE CREDENTIALS...")
    kite_api_key = os.getenv("KITE_API_KEY")
    kite_access_token = os.getenv("KITE_ACCESS_TOKEN")
    if kite_api_key and kite_access_token:
        print(f"  ✓ KITE_API_KEY: {kite_api_key[:20]}...")
        print(f"  ✓ KITE_ACCESS_TOKEN: {kite_access_token[:30]}...")
        results['KITE'] = True
    else:
        print(f"  ✗ Missing KITE_API_KEY or KITE_ACCESS_TOKEN")
    
    # Check DHAN
    print("\n📊 CHECKING DHAN CREDENTIALS...")
    dhan_access_token = os.getenv("DHAN_ACCESS_TOKEN")
    dhan_client_id = os.getenv("DHAN_CLIENT_ID")
    if dhan_access_token and dhan_client_id:
        print(f"  ✓ DHAN_ACCESS_TOKEN: {dhan_access_token[:30]}...")
        print(f"  ✓ DHAN_CLIENT_ID: {dhan_client_id}")
        print(f"  → Token length: {len(dhan_access_token)} chars (should be 50-100)")
        if len(dhan_access_token) < 20:
            print(f"  ⚠️  WARNING: Token seems too short!")
        results['DHAN'] = True
    else:
        print(f"  ✗ Missing DHAN_ACCESS_TOKEN or DHAN_CLIENT_ID")
        print(f"     DHAN_ACCESS_TOKEN: {bool(dhan_access_token)}")
        print(f"     DHAN_CLIENT_ID: {bool(dhan_client_id)}")
    
    # Check FYERS
    print("\n📊 CHECKING FYERS CREDENTIALS...")
    fyers_app_id = os.getenv("FYERS_APP_ID")
    fyers_access_token = os.getenv("FYERS_ACCESS_TOKEN")
    fyers_secret_key = os.getenv("FYERS_SECRET_KEY")
    if fyers_app_id and fyers_access_token:
        print(f"  ✓ FYERS_APP_ID: {fyers_app_id}")
        print(f"  ✓ FYERS_ACCESS_TOKEN: {fyers_access_token[:30]}...")
        print(f"  ✓ FYERS_SECRET_KEY: {'SET' if fyers_secret_key else 'NOT SET'}")
        # Check token format
        if ':' in fyers_access_token:
            print(f"  → Token format: appid:token (correct)")
        else:
            print(f"  ⚠️  WARNING: Token missing app_id: prefix. Should be 'appid:token'")
        results['FYERS'] = True
    else:
        print(f"  ✗ Missing FYERS_APP_ID or FYERS_ACCESS_TOKEN")
        print(f"     FYERS_APP_ID: {bool(fyers_app_id)}")
        print(f"     FYERS_ACCESS_TOKEN: {bool(fyers_access_token)}")
    
    # Check KOTAK
    print("\n📊 CHECKING KOTAK CREDENTIALS...")
    kotak_consumer_key = os.getenv("KOTAK_CONSUMER_KEY")
    kotak_mobile_number = os.getenv("KOTAK_MOBILE_NUMBER")
    kotak_trading_token = os.getenv("KOTAK_TRADING_TOKEN")
    kotak_trading_sid = os.getenv("KOTAK_TRADING_SID")
    
    if kotak_consumer_key and kotak_mobile_number:
        print(f"  ✓ KOTAK_CONSUMER_KEY: {kotak_consumer_key[:20]}...")
        print(f"  ✓ KOTAK_MOBILE_NUMBER: {kotak_mobile_number}")
        
        if kotak_trading_token and kotak_trading_sid:
            print(f"  ✓ KOTAK_TRADING_TOKEN: {kotak_trading_token[:30]}...")
            print(f"  ✓ KOTAK_TRADING_SID: {kotak_trading_sid[:30]}...")
            print(f"  → Auth tokens pre-loaded (from manual login)")
        else:
            print(f"  ⚠️  WARNING: Missing KOTAK_TRADING_TOKEN/SID (will need OAuth login)")
        
        results['KOTAK'] = True
    else:
        print(f"  ✗ Missing KOTAK_CONSUMER_KEY or KOTAK_MOBILE_NUMBER")
        print(f"     KOTAK_CONSUMER_KEY: {bool(kotak_consumer_key)}")
        print(f"     KOTAK_MOBILE_NUMBER: {bool(kotak_mobile_number)}")
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    for broker, status in results.items():
        symbol = "✓" if status else "✗"
        print(f"  {symbol} {broker}: {'READY' if status else 'NOT CONFIGURED'}")
    
    print("\n" + "="*70)
    if all(results.values()):
        print("✅ ALL BROKERS CONFIGURED - READY FOR LIVE TRADING")
    else:
        print("❌ SOME BROKERS NOT CONFIGURED - CHECK ENV VARIABLES")
    print("="*70 + "\n")
    
    return all(results.values())

def test_broker_connections():
    """Try to initialize broker services and test connections."""
    print("\n" + "="*70)
    print("TESTING BROKER CONNECTIONS")
    print("="*70 + "\n")
    
    try:
        from trading_app.service.dhan_order_services import DhanOrderService
        print("Testing DHAN...")
        dhan = DhanOrderService()
        print(f"  ✓ DhanOrderService initialized")
        print(f"    - Access Token: {bool(dhan.access_token)}")
        print(f"    - Client ID: {bool(dhan.client_id)}")
    except Exception as e:
        print(f"  ✗ DhanOrderService failed: {e}")
    
    try:
        from trading_app.service.fyers_order_services import FyersOrderService
        print("\nTesting FYERS...")
        fyers = FyersOrderService()
        print(f"  ✓ FyersOrderService initialized")
        print(f"    - Access Token: {bool(fyers.access_token)}")
        print(f"    - App ID: {bool(fyers.app_id)}")
        print(f"    - SDK Client: {bool(fyers.fyers_client)}")
    except Exception as e:
        print(f"  ✗ FyersOrderService failed: {e}")
    
    try:
        from trading_app.service.kotak_order_services import KotakOrderService
        print("\nTesting KOTAK...")
        kotak = KotakOrderService()
        print(f"  ✓ KotakOrderService initialized")
        print(f"    - Consumer Key: {bool(kotak.consumer_key)}")
        print(f"    - Trading Token: {bool(kotak.trading_token)}")
    except Exception as e:
        print(f"  ✗ KotakOrderService failed: {e}")

if __name__ == '__main__':
    # Check credentials
    all_ready = check_credentials()
    
    # Test connections
    try:
        test_broker_connections()
    except Exception as e:
        print(f"Could not test connections: {e}")
    
    sys.exit(0 if all_ready else 1)
