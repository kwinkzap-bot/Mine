"""
Logging and utility functions.
"""
import logging
from app.config import current_config

def setup_logger(name):
    """Create and configure a logger."""
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(current_config.LOG_FORMAT)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(current_config.LOG_LEVEL)
    
    return logger

# Create app logger
logger = setup_logger(__name__)
