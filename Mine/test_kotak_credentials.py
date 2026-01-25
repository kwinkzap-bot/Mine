#!/usr/bin/env python3
"""
Kotak Neo API Credentials Test Script
Tests your consumer_key and consumer_secret before full authentication
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_credentials():
    """Test and validate Kotak Neo API credentials"""
    
    print("\n" + "="*60)
    print("KOTAK NEO API CREDENTIALS TEST")
    print("="*60 + "\n")
    
    # Load credentials
    consumer_key = os.getenv("KOTAK_CONSUMER_KEY")
    consumer_secret = os.getenv("KOTAK_CONSUMER_SECRET")
    mobile_number = os.getenv("KOTAK_MOBILE_NUMBER")
    mpin = os.getenv("KOTAK_MPIN")
    
    errors = []
    warnings = []
    
    # Test 1: Check if credentials exist
    print("📋 Test 1: Checking if credentials are set...")
    if not consumer_key:
        errors.append("❌ KOTAK_CONSUMER_KEY is not set in .env")
    else:
        print(f"   ✓ Consumer Key: {consumer_key[:10]}... (length: {len(consumer_key)})")
    
    if not consumer_secret:
        errors.append("❌ KOTAK_CONSUMER_SECRET is not set in .env")
    else:
        print(f"   ✓ Consumer Secret: {consumer_secret[:10]}... (length: {len(consumer_secret)})")
    
    if not mobile_number:
        errors.append("❌ KOTAK_MOBILE_NUMBER is not set in .env")
    else:
        print(f"   ✓ Mobile Number: {mobile_number}")
    
    if not mpin:
        errors.append("❌ KOTAK_MPIN is not set in .env")
    else:
        print(f"   ✓ MPIN: {'*' * len(mpin)}")
    
    # Test 2: Check if consumer_key and consumer_secret are different
    print("\n🔍 Test 2: Checking if credentials are valid...")
    if consumer_key and consumer_secret:
        if consumer_key == consumer_secret:
            errors.append("❌ CRITICAL: consumer_key and consumer_secret are IDENTICAL!")
            errors.append("   They MUST be different values from Kotak Neo API Settings")
            print(f"   ❌ consumer_key:    {consumer_key}")
            print(f"   ❌ consumer_secret: {consumer_secret}")
            print("   ⚠️  Both values are the same - this will NOT work!")
        else:
            print(f"   ✓ consumer_key and consumer_secret are different (GOOD)")
    
    # Test 3: Check for common issues
    print("\n🔧 Test 3: Checking for common issues...")
    
    if consumer_key and consumer_key.startswith(" ") or (consumer_key and consumer_key.endswith(" ")):
        warnings.append("⚠️  consumer_key has leading/trailing spaces")
        
    if consumer_secret and consumer_secret.startswith(" ") or (consumer_secret and consumer_secret.endswith(" ")):
        warnings.append("⚠️  consumer_secret has leading/trailing spaces")
    
    if consumer_secret and consumer_secret == "REPLACE_WITH_YOUR_ACTUAL_CONSUMER_SECRET_FROM_KOTAK_NEO":
        errors.append("❌ You haven't replaced the placeholder consumer_secret!")
        errors.append("   Go to Kotak Neo → Settings → API Configuration to get it")
    
    if mobile_number and len(mobile_number) != 10:
        warnings.append(f"⚠️  Mobile number length is {len(mobile_number)} (should be 10 digits)")
    
    if not warnings:
        print("   ✓ No common issues found")
    
    # Test 4: Try to initialize NeoAPI
    print("\n🚀 Test 4: Testing NeoAPI initialization...")
    try:
        from neo_api_client import NeoAPI
        print("   ✓ neo_api_client library is installed")
        
        if consumer_key and consumer_secret and consumer_key != consumer_secret:
            try:
                client = NeoAPI(
                    consumer_key=consumer_key.strip(),
                    consumer_secret=consumer_secret.strip(),
                    environment="prod"
                )
                print("   ✓ NeoAPI client initialized successfully!")
                print("   ✓ Credentials are syntactically valid")
            except Exception as init_error:
                errors.append(f"❌ Failed to initialize NeoAPI: {str(init_error)}")
        else:
            print("   ⏭️  Skipping initialization (credentials invalid)")
            
    except ImportError:
        warnings.append("⚠️  neo_api_client not installed")
        warnings.append("   Install: pip install neo-api-client")
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60 + "\n")
    
    if warnings:
        print("⚠️  WARNINGS:")
        for warning in warnings:
            print(f"   {warning}")
        print()
    
    if errors:
        print("❌ ERRORS FOUND:")
        for error in errors:
            print(f"   {error}")
        print("\n🔴 You must fix these errors before authentication will work!")
        print("\n📖 See KOTAK_NEO_SETUP.md for detailed instructions")
        return False
    else:
        print("✅ ALL TESTS PASSED!")
        print("\n🎉 Your credentials look good!")
        print("\nNext steps:")
        print("1. Start Flask app: python main.py")
        print("2. Open browser: http://localhost:5000")
        print("3. Click 'Login to Kotak Neo'")
        print("4. Enter your 6-digit OTP from authenticator app")
        return True

if __name__ == "__main__":
    success = test_credentials()
    sys.exit(0 if success else 1)
