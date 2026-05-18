import sys
import os
import time

# Add workspace to path
sys.path.insert(0, '/Users/kavinkumar/Mine/Mine/src')

from dotenv import load_dotenv
# Load environment
load_dotenv('/Users/kavinkumar/Mine/Mine/env/Mine.env')

# Initialize flask app context
from trading_app.app import create_app
app = create_app()

from trading_app.app.routes.api import get_market_pulse

with app.test_request_context():
    from flask import session
    session['username'] = 'Mine'
    
    print("--- FIRST CALL (Cache Miss) ---")
    t0 = time.time()
    try:
        response = get_market_pulse()
        duration = time.time() - t0
        print(f"Status Code: {response.status_code}")
        print(f"First Call Duration: {duration:.3f} seconds")
    except Exception as e:
        print(f"Error during first call: {e}")
        
    print("\n--- SECOND CALL (Cache Hit) ---")
    t0 = time.time()
    try:
        response = get_market_pulse()
        duration = time.time() - t0
        print(f"Status Code: {response.status_code}")
        print(f"Second Call Duration: {duration:.3f} seconds")
    except Exception as e:
        print(f"Error during second call: {e}")
