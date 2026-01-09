#!/usr/bin/env python
"""
Quick verification that the 403 error is fixed for GET /
"""

import os
import sys

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_app.app import create_app

print("Creating Flask app...")
app = create_app()

print("Testing GET / with test client...")
with app.test_client() as client:
    response = client.get('/')
    print(f"Response status: {response.status_code}")
    
    if response.status_code == 200:
        print("✓ SUCCESS! GET / returns 200 (no 403 error)")
        print("\nThe 403 Forbidden error has been FIXED!")
        print("\nYou can now:")
        print("1. Start the server: python main.py")
        print("2. Visit: http://127.0.0.1:5000/")
        print("3. The home page should load without 403 errors")
    else:
        print(f"✗ FAILED! Got status {response.status_code} instead of 200")
        sys.exit(1)
