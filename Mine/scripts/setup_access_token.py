#!/usr/bin/env python3
"""
Get Zerodha ACCESS_TOKEN - Interactive setup script.

Steps:
1. This script will open a login URL
2. You'll log in with your Zerodha credentials
3. You'll be redirected with a REQUEST_TOKEN
4. This script will generate an ACCESS_TOKEN
5. The ACCESS_TOKEN will be saved to env/USERNAME.env

This script now supports the per-user environment configuration.
"""

import os
import sys
from dotenv import load_dotenv
from kiteconnect import KiteConnect

# Add src to path for UserEnvManager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from trading_app.app.utils.user_env import UserEnvManager

def get_access_token():
    """Interactive setup to get access token from Zerodha."""
    
    # Get username from user
    print("\n" + "="*70)
    print("ZERODHA ACCESS TOKEN SETUP")
    print("="*70)
    
    username = input("\nEnter your username (e.g., Mine, Kavin): ").strip()
    if not username:
        print("❌ Username is required")
        return False
    
    print(f"\nSetting up access token for user: {username}")
    
    # Load user's environment
    load_dotenv()
    UserEnvManager.load_user_env(username)
    
    api_key = UserEnvManager.get_user_var(username, "API_KEY")
    api_secret = UserEnvManager.get_user_var(username, "API_SECRET")
    
    if not api_key or not api_secret:
        print("❌ API_KEY and API_SECRET must be set in env/{username}.env")
        return False
    
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
        
        # Handle both dict and object responses
        if isinstance(session, dict):
            access_token = session.get('access_token', '')
        else:
            access_token = getattr(session, 'access_token', '')
        
        if not access_token:
            print("❌ Failed to extract access token from session")
            return False
        
        print(f"✓ Access token generated: {access_token[:20]}...")
        
        # Step 4: Save to user-specific .env
        print("\n" + "="*70)
        print("STEP 4: Saving to env/{username}.env")
        print("="*70)
        
        # Use UserEnvManager to save token
        success = UserEnvManager.save_user_var(username, 'ACCESS_TOKEN', access_token)
        
        if success:
            print(f"✓ ACCESS_TOKEN saved to env/{username}.env")
        else:
            print(f"❌ Failed to save ACCESS_TOKEN to env/{username}.env")
            return False
        
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
            
            # Handle both dict and object responses
            if isinstance(profile, dict):
                user_name = profile.get('user_name', 'N/A')
                broker = profile.get('broker', 'N/A')
            else:
                user_name = getattr(profile, 'user_name', 'N/A')
                broker = getattr(profile, 'broker', 'N/A')
            
            print(f"  User: {user_name}")
            print(f"  Broker: {broker}")
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
