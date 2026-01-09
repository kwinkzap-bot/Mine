#!/usr/bin/env python
"""
Comprehensive test script to verify the 403 Forbidden error has been fixed.
Tests the Flask app startup, basic route access, and CORS configuration.
"""

import os
import sys
import json

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_app_creation():
    """Test that the Flask app creates successfully without errors."""
    print("=" * 70)
    print("TEST 1: Flask App Creation")
    print("=" * 70)
    try:
        from trading_app.app import create_app
        app = create_app()
        print("✓ Flask app created successfully")
        return app
    except Exception as e:
        print(f"✗ Failed to create Flask app: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def test_config(app):
    """Test app configuration."""
    print("\n" + "=" * 70)
    print("TEST 2: Flask Configuration")
    print("=" * 70)
    from trading_app.app.config import current_config
    
    config_attrs = {
        'WTF_CSRF_ENABLED': False,
        'WTF_CSRF_CHECK_DEFAULT': False,
        'SESSION_COOKIE_SECURE': False,
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'RATELIMIT_ENABLED': False
    }
    
    print("Configuration settings:")
    all_correct = True
    for attr, expected in config_attrs.items():
        actual = getattr(current_config, attr, 'NOT SET')
        if actual == expected:
            print(f"  ✓ {attr}: {actual}")
        else:
            print(f"  ✗ {attr}: {actual} (expected: {expected})")
            all_correct = False
    
    return all_correct

def test_routes(app):
    """Test basic routes with Flask test client."""
    print("\n" + "=" * 70)
    print("TEST 3: Route Access Tests")
    print("=" * 70)
    
    with app.test_client() as client:
        tests = [
            ('GET', '/', 200, 'index page (no auth required)'),
            ('GET', '/api/health', 200, 'health check endpoint'),
            ('OPTIONS', '/', 200, 'CORS preflight request'),
            ('GET', '/intraday-option', 302, 'redirect to login (requires auth)'),
        ]
        
        all_passed = True
        for method, path, expected_status, description in tests:
            try:
                if method == 'GET':
                    response = client.get(path)
                elif method == 'OPTIONS':
                    response = client.options(path)
                
                if response.status_code == expected_status:
                    print(f"  ✓ {method} {path} => {response.status_code} ({description})")
                else:
                    print(f"  ✗ {method} {path} => {response.status_code} (expected {expected_status})")
                    all_passed = False
            except Exception as e:
                print(f"  ✗ {method} {path} failed: {e}")
                all_passed = False
        
        return all_passed

def test_csrf_disabled():
    """Test that CSRF is properly disabled."""
    print("\n" + "=" * 70)
    print("TEST 4: CSRF Protection Status")
    print("=" * 70)
    
    from trading_app.app.config import current_config
    
    if not current_config.WTF_CSRF_ENABLED:
        print("✓ CSRF protection is disabled (as intended for development)")
        return True
    else:
        print("✗ CSRF protection is enabled (should be disabled)")
        return False

def test_cors_configuration(app):
    """Test CORS configuration."""
    print("\n" + "=" * 70)
    print("TEST 5: CORS Configuration")
    print("=" * 70)
    
    # Check if CORS is enabled
    with app.test_client() as client:
        # Test that OPTIONS requests work (CORS preflight)
        response = client.options('/', headers={
            'Origin': 'http://127.0.0.1:5000',
            'Access-Control-Request-Method': 'GET'
        })
        
        if response.status_code == 200:
            print("✓ CORS preflight requests are allowed (OPTIONS returns 200)")
            return True
        else:
            print(f"✗ CORS preflight failed: {response.status_code}")
            return False

def test_error_handlers(app):
    """Test that error handlers don't throw 403 unnecessarily."""
    print("\n" + "=" * 70)
    print("TEST 6: Error Handlers")
    print("=" * 70)
    
    with app.test_client() as client:
        # Test 404 handler
        response = client.get('/nonexistent-page')
        if response.status_code == 404:
            print(f"✓ 404 handler works correctly")
        else:
            print(f"✗ 404 handler returned {response.status_code}")
            return False
        
        # Verify it's JSON
        try:
            data = json.loads(response.data)
            if 'error' in data:
                print(f"  Response: {data}")
        except Exception as e:
            print(f"  Warning: Could not parse error response: {e}")
        
        return True

def main():
    """Run all tests."""
    print("\n")
    print("#" * 70)
    print("# 403 FORBIDDEN ERROR FIX - COMPREHENSIVE TEST SUITE")
    print("#" * 70)
    
    results = []
    
    try:
        # Test 1: App creation
        app = test_app_creation()
        results.append(("App Creation", True))
        
        # Test 2: Configuration
        config_ok = test_config(app)
        results.append(("Configuration", config_ok))
        
        # Test 3: Routes
        routes_ok = test_routes(app)
        results.append(("Routes", routes_ok))
        
        # Test 4: CSRF
        csrf_ok = test_csrf_disabled()
        results.append(("CSRF Disabled", csrf_ok))
        
        # Test 5: CORS
        cors_ok = test_cors_configuration(app)
        results.append(("CORS Config", cors_ok))
        
        # Test 6: Error handlers
        error_ok = test_error_handlers(app)
        results.append(("Error Handlers", error_ok))
        
    except Exception as e:
        print(f"\n✗ Test suite failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    
    for test_name, ok in results:
        status = "✓ PASS" if ok else "✗ FAIL"
        print(f"{status:8} | {test_name}")
    
    print("-" * 70)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n" + "=" * 70)
        print("✓ ALL TESTS PASSED - 403 FIX IS WORKING!")
        print("=" * 70)
        print("\nTo start the server, run:")
        print("  /Users/kavinkumar/Mine/.venv/bin/python main.py")
        print("\nThen visit: http://127.0.0.1:5000/")
        print("\nYou should see the home page WITHOUT any 403 errors.")
        print("=" * 70)
        return 0
    else:
        print("\n" + "=" * 70)
        print("✗ SOME TESTS FAILED - FIX NEEDED")
        print("=" * 70)
        return 1

if __name__ == '__main__':
    sys.exit(main())
