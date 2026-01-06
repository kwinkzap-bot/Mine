#!/usr/bin/env python3
"""
Test script to verify WhatsApp notification setup and test the /api/send-notification endpoint.
Usage: python test_notification.py
"""

import os
import sys
import requests
import json
from datetime import datetime

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def check_env_variables():
    """Check if required WhatsApp environment variables are set."""
    print(f"\n{BLUE}=== Checking Environment Variables ==={RESET}\n")
    
    required_vars = {
        'WHATSAPP_TOKEN': 'WhatsApp Cloud API Access Token',
        'WHATSAPP_PHONE_NUMBER_ID': 'WhatsApp Business Phone Number ID',
        'WHATSAPP_TO_NUMBER': 'Recipient Phone Number'
    }
    
    all_set = True
    for var, description in required_vars.items():
        value = os.getenv(var, '').strip()
        if value:
            # Show masked value for token
            if var == 'WHATSAPP_TOKEN':
                masked = value[:4] + '*' * (len(value) - 8) + value[-4:] if len(value) > 8 else '***'
                print(f"{GREEN}✓{RESET} {var}: {masked}")
            else:
                print(f"{GREEN}✓{RESET} {var}: {value}")
        else:
            print(f"{RED}✗{RESET} {var}: NOT SET")
            print(f"   Description: {description}")
            all_set = False
    
    if not all_set:
        print(f"\n{YELLOW}⚠ Missing environment variables!{RESET}")
        print("\nTo set them, run:")
        print("  export WHATSAPP_TOKEN='your_access_token'")
        print("  export WHATSAPP_PHONE_NUMBER_ID='your_phone_number_id'")
        print("  export WHATSAPP_TO_NUMBER='918880802168'")
        return False
    
    print(f"\n{GREEN}✓ All environment variables are set!{RESET}")
    return True

def test_backend_endpoint():
    """Test the /api/send-notification backend endpoint."""
    print(f"\n{BLUE}=== Testing Backend Endpoint ==={RESET}\n")
    
    # Test payload
    payload = {
        "type": "trend_alert",
        "message": "🚀 Trend Changed: BUY → SELL (Test Notification)",
        "timestamp": datetime.now().isoformat()
    }
    
    url = "http://127.0.0.1:5000/api/send-notification"
    
    try:
        print(f"Sending POST request to: {url}")
        print(f"Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(
            url,
            json=payload,
            timeout=10,
            headers={'Content-Type': 'application/json'}
        )
        
        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Body: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                print(f"\n{GREEN}✓ Notification sent successfully via {data.get('method', 'API')}!{RESET}")
                return True
            else:
                print(f"\n{RED}✗ Endpoint returned success=false{RESET}")
                print(f"   Error: {data.get('error', 'Unknown error')}")
                return False
        else:
            print(f"\n{RED}✗ Server error (Status {response.status_code}){RESET}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"\n{RED}✗ Cannot connect to server at {url}{RESET}")
        print("   Make sure Flask app is running: python run.py")
        return False
    except Exception as e:
        print(f"\n{RED}✗ Error: {e}{RESET}")
        return False

def main():
    """Main test runner."""
    print(f"\n{BLUE}{'=' * 50}")
    print("WhatsApp Notification Testing")
    print(f"{'=' * 50}{RESET}")
    
    # Step 1: Check environment variables
    env_ok = check_env_variables()
    
    # Step 2: Test backend endpoint (only if env vars are set)
    endpoint_ok = False
    if env_ok:
        print(f"\n{YELLOW}Note: Make sure your Flask app is running (python run.py){RESET}")
        input("Press Enter to continue with endpoint test...")
        endpoint_ok = test_backend_endpoint()
    
    # Summary
    print(f"\n{BLUE}=== Test Summary ==={RESET}\n")
    print(f"Environment Variables: {GREEN if env_ok else RED}{'✓ OK' if env_ok else '✗ FAILED'}{RESET}")
    print(f"Backend Endpoint: {GREEN if endpoint_ok else YELLOW}{'✓ OK' if endpoint_ok else '⏭ SKIPPED (env not set)'}{RESET}")
    
    if env_ok and endpoint_ok:
        print(f"\n{GREEN}All tests passed! Notifications should work.{RESET}")
        print(f"Check your phone for WhatsApp message: +91 8880802168")
    elif env_ok and not endpoint_ok:
        print(f"\n{YELLOW}Environment is set up, but endpoint test failed.{RESET}")
        print(f"Check the Flask logs for errors.")
    else:
        print(f"\n{RED}Setup incomplete. Set environment variables first.{RESET}")

if __name__ == "__main__":
    main()
