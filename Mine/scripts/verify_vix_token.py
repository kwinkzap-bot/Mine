import os
import sys
import logging
from kiteconnect import KiteConnect

# Add src to path
# Add src to path
# Script is in Mine/scripts/, need to go up to Mine/ to find src
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, 'src'))

from trading_app.app.utils.token_manager import get_access_token

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_vix_token():
    try:
        # Get credentials using the project's utility
        access_token = get_access_token()
        api_key = os.getenv('API_KEY')  # Changed from KITE_API_KEY to API_KEY to match main.py
        
        if not api_key:
            logger.error("API_KEY environment variable not set")
            # Try to read from a local .env file
            env_files = ['.env', 'env/Mine.env']
            for env_name in env_files:
                try:
                    env_path = os.path.join(project_root, env_name)
                    if os.path.exists(env_path):
                        with open(env_path, 'r') as f:
                            for line in f:
                                if line.startswith('API_KEY='):
                                    api_key = line.split('=')[1].strip()
                                    break
                    if api_key:
                        logger.info(f"Loaded API_KEY from {env_name}")
                        break
                except:
                    pass

        if not api_key or not access_token:
            logger.error(f"Missing credentials. API_KEY found: {bool(api_key)}, Access Token found: {bool(access_token)}")
            return

        print(f"Using API Key: {api_key}")
        print(f"Using Access Token: {access_token[:10]}...")

        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        logger.info("Fetching instruments...")
        instruments = kite.instruments('NSE')
        
        vix_instrument = None
        for inst in instruments:
            if inst['name'] == 'INDIA VIX':
                vix_instrument = inst
                break
        
        if vix_instrument:
            logger.info("="*50)
            logger.info(f"FOUND INDIA VIX")
            logger.info(f"Instrument Token: {vix_instrument['instrument_token']}")
            logger.info(f"Trading Symbol: {vix_instrument['tradingsymbol']}")
            logger.info("="*50)
        else:
            logger.error("INDIA VIX not found in NSE instruments")
            
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    verify_vix_token()
