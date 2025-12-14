import os
import logging

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import MultiCPRBacktest exactly like web_app.py does
MultiCPRBacktest = None  # type: ignore
try:
    multi_cpr_path = os.path.join(os.path.dirname(__file__), "strategy", "back-test", "multi-cpr", "multi_cpr_backtest.py")
    logger.info(f"Attempting to load MultiCPRBacktest from: {multi_cpr_path}")
    
    if os.path.exists(multi_cpr_path):
        logger.info("File exists, creating spec...")
        spec = importlib.util.spec_from_file_location("multi_cpr_backtest", multi_cpr_path)
        if spec and spec.loader:
            logger.info("Spec created, loading module...")
            multi_cpr_module = importlib.util.module_from_spec(spec)
            logger.info("Module created, executing...")
            spec.loader.exec_module(multi_cpr_module)
            logger.info("Module executed, getting class...")
            MultiCPRBacktest = multi_cpr_module.MultiCPRBacktest  # type: ignore
            logger.info(f"MultiCPRBacktest loaded successfully: {MultiCPRBacktest}")
        else:
            logger.error("Failed to create spec for MultiCPRBacktest")
    else:
        logger.error(f"MultiCPRBacktest file not found at: {multi_cpr_path}")
except Exception as e:
    logger.error(f"Error importing MultiCPRBacktest: {e}")
    import traceback
    traceback.print_exc()

logger.info(f"Final MultiCPRBacktest value: {MultiCPRBacktest}")

# Test if we can create an instance
if MultiCPRBacktest is not None:
    try:
        # Test without kite instance (will use env vars)
        backtest = MultiCPRBacktest()
        logger.info("MultiCPRBacktest instance created successfully")
        
        # Test get_fo_stocks method
        stocks = backtest.get_fo_stocks()
        logger.info(f"F&O stocks method works: {len(stocks)} stocks returned")
    except Exception as e:
        logger.error(f"Error creating MultiCPRBacktest instance: {e}")
else:
    logger.error("MultiCPRBacktest is None - cannot test instance creation")