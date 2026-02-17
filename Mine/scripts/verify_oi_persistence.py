
import sys
import os
import logging
from datetime import datetime

# Add project root to path
# We are in Mine/Mine/scripts
# We need to add Mine/Mine/src to path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir) # Mine/Mine
src_path = os.path.join(project_root, 'src')
sys.path.append(src_path)
print(f"Added {src_path} to sys.path")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock Flask app context if needed, but we want to test SANS context behavior mostly
from flask import Flask
app = Flask(__name__)

def test_get_kite_context():
    logger.info("Testing get_kite() outside request context...")
    try:
        from trading_app.app.routes.api import get_kite
        kite = get_kite()
        if kite:
            logger.info("✅ get_kite() returned instance")
        else:
            logger.warning("⚠️ get_kite() returned None")
    except RuntimeError as e:
        logger.error(f"❌ get_kite() failed with RuntimeError: {e}")
        logger.info("This confirms that clean calling of get_kite() in scheduler (background thread) will fail.")
    except Exception as e:
        logger.error(f"❌ get_kite() failed with {type(e).__name__}: {e}")

def force_snapshot_save():
    logger.info("Testing manual OI snapshot save...")
    try:
        # We need a kite instance. 
        # Attempt to get one manually or mock it if we just want to test DB write
        from trading_app.app.utils.token_manager import get_access_token
        from kiteconnect import KiteConnect
        
        token = get_access_token()
        api_key = os.getenv("API_KEY") 
        # Note: Environment variables might need to be loaded from .env if not present
        
        if not token or not api_key:
            logger.warning("Cannot initialize real KiteConnect (missing token/key).")
            # For DB testing, valid Object structure might be enough if logic doesn't call API
            # But fetch_open_interest_data DOES call API.
            logger.info("Skipping full integration test, checking DB connection only.")
            return

        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(token)
        
        from trading_app.service.open_interest_service import OpenInterestService
        service = OpenInterestService(kite)
        
        logger.info("Fetching NIFTY data...")
        # This will fail if market is closed or token expired, but let's try
        try:
            data = service.get_open_interest_data('NIFTY')
            if data and data.get('success'):
                logger.info("Got data, attempting save...")
                service.save_oi_snapshot('NIFTY', data)
                logger.info("✅ Save called.")
                
                # Verify DB
                import sqlite3
                conn = sqlite3.connect('oi_data.db')
                cursor = conn.cursor()
                cursor.execute("SELECT count(*) FROM oi_history")
                count = cursor.fetchone()[0]
                logger.info(f"DB Row Count: {count}")
                conn.close()
            else:
                logger.warning(f"Failed to get data: {data.get('error')}")
        except Exception as e:
             logger.error(f"Service call failed: {e}")

    except Exception as e:
        logger.error(f"Snapshot test failed: {e}")

if __name__ == "__main__":
    # Load env
    from dotenv import load_dotenv
    load_dotenv('Mine/env/Mine.env')
    
    test_get_kite_context()
    force_snapshot_save() 
