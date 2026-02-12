"""
Logging and utility functions.
"""
import logging
from trading_app.app.config import current_config

def setup_logger(name):
    """Create and configure a logger."""
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        # Enhanced format with more detail
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        # Handle both string and logging level constants
        log_level = current_config.LOG_LEVEL
        if isinstance(log_level, str):
            log_level = getattr(logging, log_level.upper(), logging.INFO)
        logger.setLevel(log_level)
    
    return logger

# Create app logger
logger = setup_logger(__name__)

