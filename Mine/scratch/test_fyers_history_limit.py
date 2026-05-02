import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

from trading_app.service.fyers_data_service import FyersDataServiceAdapter

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_long_history():
    # Load env vars
    env_path = os.path.join(os.getcwd(), 'env', 'Mine.env')
    load_dotenv(env_path)
    
    app_id = os.getenv('BROKER_4_APP_ID')
    access_token = os.getenv('BROKER_4_ACCESS_TOKEN')
    secret = os.getenv('BROKER_4_SECRET_KEY')
    
    if not app_id or not access_token:
        print("Missing credentials!")
        return

    # Initialize adapter
    adapter = FyersDataServiceAdapter(app_id, access_token, secret)
    
    # Test: Long Historical Data (1 year of daily data, or multiple chunks of minute data)
    print("\n--- Testing Long Historical Data ---")
    to_date = datetime.now()
    # 1 year ago
    from_date = to_date.replace(year=to_date.year - 1)
    
    print(f"Fetching 1 year of data for NSE:MARICO-EQ from {from_date.date()} to {to_date.date()}...")
    
    # Using 'day' resolution to test the 364 day limit / chunks
    hist = adapter.historical_data("NSE:MARICO-EQ", from_date, to_date, "day")
    print(f"Historical data count: {len(hist)}")
    
    # Test Cache
    print("\n--- Testing Cache (Second Call) ---")
    start_time = datetime.now()
    hist2 = adapter.historical_data("NSE:MARICO-EQ", from_date, to_date, "day")
    end_time = datetime.now()
    print(f"Second call count: {len(hist2)}")
    print(f"Cache hit duration: {(end_time - start_time).total_seconds():.4f}s")

if __name__ == "__main__":
    test_long_history()
