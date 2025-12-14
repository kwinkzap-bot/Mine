import os
import importlib.util
import logging

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Test the exact same import logic as web_app.py
try:
    multi_cpr_path = os.path.join(os.path.dirname(__file__), "strategy", "back-test", "multi-cpr", "multi_cpr_backtest.py")
    logger.info(f"Attempting to load MultiCPRBacktest from: {multi_cpr_path}")
    
    if os.path.exists(multi_cpr_path):
        spec = importlib.util.spec_from_file_location("multi_cpr_backtest", multi_cpr_path)
        if spec and spec.loader:
            multi_cpr_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(multi_cpr_module)
            MultiCPRBacktest = multi_cpr_module.MultiCPRBacktest
            logger.info("MultiCPRBacktest loaded successfully")
            logger.info(f"MultiCPRBacktest is None: {MultiCPRBacktest is None}")
        else:
            logger.error("Failed to create spec for MultiCPRBacktest")
            MultiCPRBacktest = None
    else:
        logger.error(f"MultiCPRBacktest file not found at: {multi_cpr_path}")
        MultiCPRBacktest = None
except Exception as e:
    logger.error(f"Error importing MultiCPRBacktest: {e}")
    MultiCPRBacktest = None

logger.info(f"Final MultiCPRBacktest value: {MultiCPRBacktest}")