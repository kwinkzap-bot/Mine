#!/usr/bin/env python3
"""
Kotak Neo Authentication Diagnostic & Test Script
Helps identify and troubleshoot login issues
"""

import sys
import os
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent / "Mine"
sys.path.insert(0, str(project_root))

def test_kotak_authentication():
    """Test Kotak authentication with diagnostic info"""
    
    print("\n" + "="*70)
    print("KOTAK NEO AUTHENTICATION DIAGNOSTIC TOOL")
    print("="*70 + "\n")
    
    try:
        from trading_app.service.kotak_order_services import KotakOrderService
        print("✓ Successfully imported KotakOrderService\n")
    except ImportError as e:
        print(f"✗ Failed to import KotakOrderService: {e}")
        return
    
    # Create service instance
    try:
        kotak = KotakOrderService()
        print("✓ Successfully initialized KotakOrderService\n")
    except Exception as e:
        print(f"✗ Failed to initialize: {e}")
        return
    
    # Run diagnostic
    print("-" * 70)
    print("CHECKING CREDENTIALS...")
    print("-" * 70 + "\n")
    
    diagnostics = kotak.diagnose_authentication()
    
    # Display credentials status
    print("Credentials Present:")
    for name, present in diagnostics['credentials_present'].items():
        status = "✓ YES" if present else "✗ MISSING"
        print(f"  {name:20s} : {status}")
    
    print()
    
    # Display validation errors
    if diagnostics['validation_errors']:
        print("⚠️  Validation Errors:")
        for error in diagnostics['validation_errors']:
            print(f"  • {error}")
        print()
    else:
        print("✓ All credentials validated successfully\n")
    
    # Display suggestions
    if diagnostics['suggestions']:
        print("💡 Suggestions:")
        for suggestion in diagnostics['suggestions']:
            print(f"  • {suggestion}")
        print()
    
    # Display connectivity
    print("-" * 70)
    print("CHECKING CONNECTIVITY...")
    print("-" * 70 + "\n")
    
    if 'endpoint_reachable' in diagnostics:
        if diagnostics['endpoint_reachable']:
            print("✓ Kotak API endpoint is reachable\n")
        else:
            print("✗ Cannot reach Kotak API endpoint")
            print("  • Check internet connection")
            print("  • Check firewall settings")
            print("  • Try from different network\n")
    
    # Attempt authentication if credentials present
    all_present = all(diagnostics['credentials_present'].values())
    all_valid = not diagnostics['validation_errors']
    
    if all_present and all_valid:
        print("-" * 70)
        print("ATTEMPTING AUTHENTICATION...")
        print("-" * 70 + "\n")
        
        print("Sending authentication request (this may take 10-30 seconds)...\n")
        
        success = kotak.authenticate()
        
        if success:
            print("="*70)
            print("✅ AUTHENTICATION SUCCESSFUL!")
            print("="*70 + "\n")
            
            print("Session Details:")
            print(f"  Base URL        : {kotak.base_url}")
            print(f"  Trading Token   : {kotak.trading_token[:30]}..." if kotak.trading_token else "  Trading Token   : None")
            print(f"  Trading SID     : {kotak.trading_sid}")
            print()
            print("✓ You can now place orders on Kotak Neo!")
            print()
        else:
            print("="*70)
            print("❌ AUTHENTICATION FAILED")
            print("="*70 + "\n")
            
            error_msg = kotak.last_error
            print(f"Error: {error_msg}\n")
            
            # Provide specific help based on error
            if "OTP" in error_msg:
                print("💡 Solution:")
                print("  1. Get fresh 6-digit OTP from your authenticator app")
                print("  2. OTP is valid for only 30 seconds")
                print("  3. Run this test again immediately with new OTP\n")
            
            elif "MPIN" in error_msg:
                print("💡 Solution:")
                print("  1. Verify MPIN in .env is exactly 6 digits")
                print("  2. Check MPIN is correct (from Kotak account setup)")
                print("  3. Try again with correct MPIN\n")
            
            elif "Mobile" in error_msg:
                print("💡 Solution:")
                print("  1. KOTAK_MOBILE_NUMBER should be 10 digits WITHOUT +91")
                print("  2. Remove any +91 or 91 prefix from .env")
                print("  3. Example: 9876543210 (not +919876543210)\n")
            
            elif "UCC" in error_msg:
                print("💡 Solution:")
                print("  1. Find your UCC in Kotak Neo Dashboard → Account → Client Details")
                print("  2. UCC is typically 5 characters like 'XV5PK'")
                print("  3. Update KOTAK_UCC in .env\n")
            
            elif "Token" in error_msg:
                print("💡 Solution:")
                print("  1. Go to Kotak Neo Dashboard → Settings → API Keys")
                print("  2. Copy your Trade API Key")
                print("  3. Update KOTAK_ACCESS_TOKEN in .env\n")
            
            elif "timeout" in error_msg.lower() or "connect" in error_msg.lower():
                print("💡 Solution:")
                print("  1. Check your internet connection")
                print("  2. Check if Kotak website is accessible")
                print("  3. Check firewall/proxy settings")
                print("  4. Try again after 30 seconds\n")
    
    else:
        print("\n⚠️  Cannot attempt authentication - fix validation errors first\n")

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )
    
    test_kotak_authentication()
    
    print("-" * 70)
    print("For detailed help, see: KOTAK_LOGIN_FIX.md")
    print("-" * 70 + "\n")
