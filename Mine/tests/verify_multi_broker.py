import sys
import os
import logging
from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))
# Also add root for module resolution if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Mocking internal imports if necessary or relying on python path
try:
    from trading_app.service.kotak_order_services import KotakOrderService
    from trading_app.service.dhan_order_services import DhanOrderService
    from trading_app.service.fyers_order_services import FyersOrderService
except ImportError as e:
    logging.error(f"Import Error: {e}")
    # Try alternate path if running from root
    try:
        from src.trading_app.service.kotak_order_services import KotakOrderService
        from src.trading_app.service.dhan_order_services import DhanOrderService
        from src.trading_app.service.fyers_order_services import FyersOrderService
    except ImportError as e2:
        logging.error(f"Import Error 2: {e2}")
        sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MultiBrokerVerify")

def verify_kotak():
    logger.info("--- Verifying Kotak ---")
    try:
        kotak = KotakOrderService()
        creds = kotak.verify_credentials()
        logger.info(f"Credentials Check: {creds}")
        if kotak.client:
            logger.info("✅ NeoAPI client initialized")
            # Try to build a symbol
            sym = kotak._build_option_symbol("NIFTY", 22000, "CE")
            logger.info(f"Symbol Builder Test: {sym}")
        else:
            logger.warning("❌ NeoAPI client NOT initialized")
    except Exception as e:
        logger.error(f"❌ Kotak Init Failed: {e}", exc_info=True)

def verify_dhan():
    logger.info("--- Verifying Dhan ---")
    try:
        dhan = DhanOrderService()
        logger.info("✅ Dhan Service Initialized")
    except Exception as e:
        logger.error(f"❌ Dhan Init Failed: {e}", exc_info=True)

def verify_fyers():
    logger.info("--- Verifying Fyers ---")
    try:
        fyers = FyersOrderService()
        logger.info("✅ Fyers Service Initialized")
    except Exception as e:
        logger.error(f"❌ Fyers Init Failed: {e}", exc_info=True)

if __name__ == "__main__":
    # improved env loading
    env_path = os.path.join(os.path.dirname(__file__), '../env/Mine.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
        logger.info(f"Loaded env from {env_path}")
    else:
        load_dotenv() # Fallback
        logger.warning("Loaded default .env (Mine.env not found)")
        
    verify_kotak()
    verify_dhan()
    verify_fyers()
