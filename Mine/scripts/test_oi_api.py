import sys
import os
import json
from unittest.mock import MagicMock, patch
from flask import Flask, session

# Add src to path
sys.path.append(os.path.join(os.getcwd(), 'src'))

# 1. Pre-mock the dependencies BEFORE importing the module under test
mock_kite_module = MagicMock()
sys.modules['kiteconnect'] = mock_kite_module

mock_service_module = MagicMock()
sys.modules['trading_app.service'] = MagicMock()
sys.modules['trading_app.service.open_interest_service'] = mock_service_module

# Now import the API, which will use the mocked modules
from trading_app.app.routes.api import api_bp, get_open_interest

app = Flask(__name__)
app.register_blueprint(api_bp)
app.secret_key = 'test_secret'

def test_api_logic():
    print("Testing /open-interest logic...")
    
    with app.test_request_context('/open-interest', method='POST', json={'symbol': 'NIFTY'}):
        # Mock session
        session['username'] = 'test_user'
        session['access_token'] = 'test_token'
        
        # Access the mocked OpenInterestService from the pre-mocked module
        # Note: OpenInterestService is a class in the module, so we access it as an attribute
        MockServiceClass = sys.modules['trading_app.service.open_interest_service'].OpenInterestService
        
        # Reset mocks between tests if needed, but here we just set return values
        mock_instance = MockServiceClass.return_value
        
        # --- Case 1: DB has fresh data ---
        mock_instance.get_latest_oi_from_db.return_value = {
            'success': True, 
            'timestamp': '2025-01-01T12:00:00',
            'pcr_oi': 0.95
        }
        
        # Mock get_kite to return something
        with patch('trading_app.app.routes.api.get_kite', return_value=MagicMock()):
            response = get_open_interest()
            data = response.get_json()
            
            print(f"\nCase 1 (Fresh DB):")
            if data.get('data_source') == 'DATABASE':
                print("   ✅ Served from DATABASE")
            else:
                print(f"   ❌ Served from {data.get('data_source')}")

        # --- Case 2: DB Stale/Empty ---
        mock_instance.get_latest_oi_from_db.return_value = None
        mock_instance.get_open_interest_data.return_value = {
            'success': True,
            'ce_summary': {'total_oi': 100},
            'pe_summary': {'total_oi': 200}
        }
        
        with patch('trading_app.app.routes.api.get_kite', return_value=MagicMock()):
            response = get_open_interest()
            data = response.get_json()
            
            print(f"\nCase 2 (Stale DB -> Live):")
            # The API should set data_source to LIVE_FALLBACK
            if data.get('data_source') == 'LIVE_FALLBACK':
                print("   ✅ Served from LIVE_FALLBACK")
                # Check if save was called
                if mock_instance.save_oi_snapshot.called:
                    print("   ✅ Saved to DB")
                else:
                    print("   ❌ Did not save to DB")
            else:
                print(f"   ❌ Served from {data.get('data_source')}")

if __name__ == "__main__":
    test_api_logic()
