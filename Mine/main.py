#!/usr/bin/env python
"""
Main entry point for the trading application.
This is the recommended way to run the application.
"""
import os
import sys
import threading

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_app.app import create_app
from trading_app.app.utils.logger import logger

def start_live_monitoring():
    """Initialize and start the live signal monitoring in a separate thread."""
    try:
        # Only start live monitoring if ACCESS_TOKEN is set
        if not os.getenv('ACCESS_TOKEN'):
            logger.info("⚠️  ACCESS_TOKEN not set - skipping live monitoring")
            return
        
        logger.info("Initializing live signal monitoring...")
        from trading_app.strategy.Live.HighLowLiveSignal import HighLowLiveSignal
        live_signal = HighLowLiveSignal(symbol='NIFTY')
        live_signal.start_live_monitoring()
    except Exception as e:
        logger.error(f"Error in live monitoring thread: {e}")

def main():
    """Run the Flask application and live monitoring."""
    app = create_app()

    # Start live monitoring in a background thread (only if access token exists)
    if os.getenv('ACCESS_TOKEN'):
        monitoring_thread = threading.Thread(target=start_live_monitoring, daemon=True)
        monitoring_thread.start()
    else:
        logger.info("ACCESS_TOKEN not configured - live monitoring disabled")
    
    # Get host and port from environment
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'
    
    logger.info(f"Starting Flask app on {host}:{port} (Debug: {debug})")
    
    try:
        app.run(host=host, port=port, debug=debug, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
