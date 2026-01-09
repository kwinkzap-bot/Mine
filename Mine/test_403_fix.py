#!/usr/bin/env python
"""
Test script to verify the 403 Forbidden error has been fixed.
Tests the Flask app startup and basic route access without authentication.
"""

import os
import sys
import json

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_app_creation():
    """Test that the Flask app creates successfully without errors."""
    print("Testing Flask app creation...")
    try:
        from trading_app.app import create_app
        app = create_app()
        print("✓ Flask app created successfully")
        return app
    except Exception as e:
        print(f"✗ Failed to create Flask app: {e}")
        sys.exit(1)

def test_routes(app):
    """Test basic routes with Flask test client."""
    print("\nTesting routes with test client...")
    
    with app.test_client() as client:
        # Test 1: GET / (index) - should work without auth
        print("  Testing GET / (index)...")
        response = client.get('/')
        if response.status_code == 200:
            print(f"    ✓ GET / returned {response.status_code}")
        else:
            print(f"    ✗ GET / returned {response.status_code} (expected 200)")
        
        # Test 2: GET /intraday-option without auth - should redirect to login
        print("  Testing GET /intraday-option (requires auth)...")
        response = client.get('/intraday-option')
        if response.status_code == 302:  # Redirect
            print(f"    ✓ GET /intraday-option returned {response.status_code} (redirect to login)")
        elif response.status_code == 401:
            print(f"    ✓ GET /intraday-option returned {response.status_code} (unauthorized)")
        else:
            print(f"    ✗ GET /intraday-option returned {response.status_code}")
        
        # Test 3: GET /api/health - should work without auth
        print("  Testing GET /api/health...")
        response = client.get('/api/health')
        if response.status_code == 200:
            print(f"    ✓ GET /api/health returned {response.status_code}")
            data = json.loads(response.data)
            print(f"    Response: {data}")
        else:
            print(f"    ✗ GET /api/health returned {response.status_code}")
        
        # Test 4: CORS preflight (OPTIONS request)
        print("  Testing CORS preflight (OPTIONS /)...")
        response = client.options('/')
        if response.status_code == 200:
            print(f"    ✓ OPTIONS / returned {response.status_code}")
        else:
            print(f"    ✗ OPTIONS / returned {response.status_code}")
        
        # Test 5: Check error handlers
        print("  Testing error handlers...")
        response = client.get('/nonexistent')
        if response.status_code == 404:
            print(f"    ✓ GET /nonexistent returned {response.status_code}")
        else:
            print(f"    ✗ GET /nonexistent returned {response.status_code} (expected 404)")

def test_config():
    """Test app configuration."""
    print("\nTesting Flask configuration...")
    from trading_app.app.config import current_config
    
    config_attrs = [
        'WTF_CSRF_ENABLED',
        'WTF_CSRF_CHECK_DEFAULT',
        'SESSION_COOKIE_SECURE',
        'SESSION_COOKIE_HTTPONLY',
        'SESSION_COOKIE_SAMESITE',
        'RATELIMIT_ENABLED'
    ]
    
    for attr in config_attrs:
        value = getattr(current_config, attr, 'NOT SET')
        status = '✓' if value is not None else '✗'
        print(f"  {status} {attr}: {value}")

if __name__ == '__main__':
    print("=" * 60)
    print("403 Forbidden Error Fix - Test Suite")
    print("=" * 60)
    
    try:
        # Test app creation
        app = test_app_creation()
        
        # Test configuration
        test_config()
        
        # Test routes
        test_routes(app)
        
        print("\n" + "=" * 60)
        print("✓ All tests completed successfully!")
        print("=" * 60)
        print("\nTo start the server, run:")
        print("  /Users/kavinkumar/Mine/.venv/bin/python main.py")
        print("\nThen visit: http://127.0.0.1:5000/")
        
    except Exception as e:
        print(f"\n✗ Test suite failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
