#!/usr/bin/env python3
"""
Get Zerodha ACCESS_TOKEN - Interactive setup script.

Steps:
1. This script will open a login URL
2. You'll log in with your Zerodha credentials
3. You'll be redirected with a REQUEST_TOKEN
4. This script will generate an ACCESS_TOKEN
5. The ACCESS_TOKEN will be saved to .env
"""

import os
import sys
from dotenv import load_dotenv
from kiteconnect import KiteConnect

def get_access_token():
    """Interactive setup to get access token from Zerodha."""
    
    load_dotenv()
    
    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    
    if not api_key or not api_secret:
        print("❌ API_KEY and API_SECRET must be set in .env")
        return False
    
    print("\n" + "="*70)
    print("ZERODHA ACCESS TOKEN SETUP")
    print("="*70)
    
    print(f"\n✓ API_KEY found: {api_key[:10]}...")
    print(f"✓ API_SECRET found: {api_secret[:10]}...")
    
    # Create KiteConnect instance
    kite = KiteConnect(api_key=api_key)
    
    # Step 1: Generate login URL
    print("\n" + "="*70)
    print("STEP 1: Login to Zerodha")
    print("="*70)
    
    login_url = kite.login_url()
    print(f"\n🔗 Open this URL in your browser:\n{login_url}\n")
    print("Log in with your Zerodha account credentials.")
    print("After login, you'll be redirected to a URL.")
    print("Look for the 'request_token' parameter in the URL.")
    print("Example: https://...?request_token=abc123xyz&status=success\n")
    
    # Step 2: Get request token
    print("="*70)
    print("STEP 2: Enter Request Token")
    print("="*70)
    
    request_token = input("\nPaste the REQUEST_TOKEN (or just the token part): ").strip()
    
    if not request_token:
        print("❌ No request token provided")
        return False
    
    # Extract just the token if full URL was pasted
    if "request_token=" in request_token:
        request_token = request_token.split("request_token=")[1].split("&")[0]
    
    print(f"\n✓ Request token: {request_token}")
    
    # Step 3: Generate session
    print("\n" + "="*70)
    print("STEP 3: Generating Access Token...")
    print("="*70)
    
    try:
        session = kite.generate_session(request_token, api_secret)
        access_token = session['access_token']
        
        print(f"✓ Access token generated: {access_token[:20]}...")
        
        # Step 4: Save to .env
        print("\n" + "="*70)
        print("STEP 4: Saving to .env")
        print("="*70)
        
        env_file = "/Users/kavinkumar/Mine/Mine/.env"
        
        # Read current .env
        with open(env_file, 'r') as f:
            content = f.read()
        
        # Replace ACCESS_TOKEN
        lines = content.split('\n')
        new_lines = []
        for line in lines:
            if line.startswith('ACCESS_TOKEN='):
                new_lines.append(f'ACCESS_TOKEN={access_token}')
            else:
                new_lines.append(line)
        
        # Write back
        with open(env_file, 'w') as f:
            f.write('\n'.join(new_lines))
        
        print(f"✓ ACCESS_TOKEN saved to {env_file}")
        
        # Step 5: Verify
        print("\n" + "="*70)
        print("STEP 5: Verifying...")
        print("="*70)
        
        os.environ['ACCESS_TOKEN'] = access_token
        
        # Reload and test
        kite2 = KiteConnect(api_key=api_key)
        kite2.set_access_token(access_token)
        
        # Try a simple API call
        try:
            profile = kite2.profile()
            print(f"✓ API connection successful!")
            print(f"  User: {profile['user_name']}")
            print(f"  Broker: {profile['broker']}")
        except Exception as e:
            print(f"⚠️  Could not verify (API may be rate-limited): {e}")
        
        print("\n" + "="*70)
        print("✅ SETUP COMPLETE!")
        print("="*70)
        print("\nYou can now run:")
        print("  python run.py")
        print("\nThe system will check signals every 5 minutes (9:15 AM - 3:25 PM IST)")
        print("="*70 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error generating session: {e}")
        print("\nTroubleshooting:")
        print("1. Make sure the request token is correct and fresh (< 10 minutes)")
        print("2. Check that API_KEY and API_SECRET are correct in .env")
        print("3. Verify you're using a Zerodha account (not paper trading)")
        print("4. Try again: python setup_access_token.py")
        return False

if __name__ == "__main__":
    success = get_access_token()
    sys.exit(0 if success else 1)
