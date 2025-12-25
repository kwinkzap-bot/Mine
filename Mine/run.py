"""
Application entry point.
Run the Flask application.
"""
import os
import sys
import threading
from app import create_app
from app.utils.logger import logger
from strategy.HighLowLiveSignal import HighLowLiveSignal

def start_live_monitoring():
    """Initialize and start the live signal monitoring in a separate thread."""
    try:
        logger.info("Initializing live signal monitoring...")
        live_signal = HighLowLiveSignal(symbol='NIFTY')
        live_signal.start_live_monitoring()
    except Exception as e:
        logger.error(f"Error in live monitoring thread: {e}")

def main():
    """Run the Flask application and live monitoring."""
    app = create_app()

    # Start live monitoring in a background thread
    monitoring_thread = threading.Thread(target=start_live_monitoring, daemon=True)
    monitoring_thread.start()
    
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
