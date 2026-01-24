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

# Global reference to live monitoring instances
live_monitors = {}

def start_intraday_9_20_monitoring():
    """Initialize and start the Intraday 9:20 live signal monitoring."""
    try:
        from trading_app.app.intraday_option.intraday_9_20_live_signal import Intraday920LiveSignal
        from trading_app.app.utils.token_manager import get_kite
        
        logger.info("🚀 Starting Intraday 9:20 Live Signal Monitoring...")
        
        kite = get_kite()
        if not kite:
            logger.warning("⚠️  Kite connection not available - skipping Intraday 9:20 monitoring")
            return
        
        # Create monitors for NIFTY only
        symbols = ['NIFTY']
        
        for symbol in symbols:
            try:
                monitor = Intraday920LiveSignal(kite, symbol=symbol)
                
                # Start monitoring if it's a market day and within hours
                if monitor.is_market_day():
                    if monitor.start_monitoring():
                        live_monitors[symbol] = monitor
                        logger.info(f"✅ Live monitoring started for {symbol}")
                    else:
                        logger.warning(f"⚠️  Could not start monitoring for {symbol}")
                else:
                    logger.info(f"ℹ️  Not a market day - {symbol} monitoring not started")
            except Exception as e:
                logger.error(f"Error starting monitoring for {symbol}: {str(e)}")
        
        if live_monitors:
            logger.info(f"✅ Intraday 9:20 Live Signal Monitoring active for: {', '.join(live_monitors.keys())}")
        else:
            logger.warning("⚠️  No Intraday 9:20 monitoring started")
            
    except ImportError as e:
        logger.warning(f"Could not import Intraday920LiveSignal: {str(e)}")
    except Exception as e:
        logger.error(f"Error in Intraday 9:20 monitoring initialization: {str(e)}", exc_info=True)

def start_live_monitoring():
    """Initialize and start the live signal monitoring in a separate thread."""
    try:
        # Only start live monitoring if ACCESS_TOKEN is set
        if not os.getenv('ACCESS_TOKEN'):
            logger.info("⚠️  ACCESS_TOKEN not set - skipping live monitoring")
            return
        
        logger.info("Initializing legacy live signal monitoring...")
        from trading_app.strategy.Live.HighLowLiveSignal import HighLowLiveSignal
        live_signal = HighLowLiveSignal(symbol='NIFTY')
        live_signal.start_live_monitoring()
    except Exception as e:
        logger.error(f"Error in live monitoring thread: {e}")

def main():
    """Run the Flask application and live monitoring."""
    app = create_app()

    # Start Intraday 9:20 monitoring in a background thread
    intraday_thread = threading.Thread(
        target=start_intraday_9_20_monitoring, 
        name="Intraday920Monitor",
        daemon=True
    )
    intraday_thread.start()

    # Start legacy live monitoring in a background thread (only if access token exists)
    if os.getenv('ACCESS_TOKEN'):
        monitoring_thread = threading.Thread(
            target=start_live_monitoring, 
            name="LegacyLiveMonitor",
            daemon=True
        )
        monitoring_thread.start()
    else:
        logger.info("ACCESS_TOKEN not configured - legacy live monitoring disabled")
    
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
