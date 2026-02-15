
import sys
import os
import json
import logging

# Add src to path
sys.path.append(os.path.join(os.getcwd(), 'src'))

from trading_app.service.open_interest_service import OpenInterestService

# Mock Kite Class
class MockKite:
    def __init__(self):
        pass

def test_oi_persistence():
    print("Testing OI Persistence...")
    
    # Initialize service with mock kite
    service = OpenInterestService(MockKite())
    
    # Check if DB init worked (attribute should exist)
    if not hasattr(service, 'db_path'):
        print("❌ DB Path not set in service")
        return
    
    print(f"DB Path: {service.db_path}")
    
    # Mock Data
    mock_data = {
        'current_price': 24500.0,
        'ce_summary': {
            'total_oi': 1000000,
            'change_in_oi': 50000
        },
        'pe_summary': {
            'total_oi': 800000,
            'change_in_oi': -20000
        },
        'pcr_oi': 0.8,
        'max_pain': 24500,
        'iv_percentile': 45.5,
        'strikes': [
            {'strike': 24500, 'ce_oi': 50000, 'pe_oi': 40000, 'ce_change_in_oi': 1000, 'pe_change_in_oi': -500, 'ce_iv': 12, 'pe_iv': 14},
            {'strike': 24600, 'ce_oi': 60000, 'pe_oi': 30000, 'ce_change_in_oi': 2000, 'pe_change_in_oi': -100, 'ce_iv': 11, 'pe_iv': 15}
        ]
    }
    
    # Save Snapshot
    print("Saving snapshot...")
    service.save_oi_snapshot('TEST_SYMBOL', mock_data)
    
    # Retrieve History
    print("Retrieving history...")
    history = service.get_oi_history('TEST_SYMBOL', limit=5)
    
    if len(history) > 0:
        print(f"✅ Success! Retrieved {len(history)} records.")
        latest = history[0]
        print(f"Latest Record Timestamp: {latest['timestamp']}")
        print(f"Latest Record Symbol: {latest['symbol']}")
        print(f"Latest Record PCR: {latest['pcr']}")
        print(f"Active Strikes (Parsed): {latest['active_strikes']}")
    else:
        print("❌ Failed to retrieve history.")

if __name__ == "__main__":
    test_oi_persistence()
