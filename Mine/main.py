#!/usr/bin/env python
"""
Main entry point for the trading application.
This is the recommended way to run the application.
"""
import socket as _socket

# Force IPv4 for all outbound connections.
# Zerodha Kite API rejects dynamic IPv6 addresses that aren't whitelisted.
_orig_getaddrinfo = _socket.getaddrinfo
def _ipv4_only(host, port, _family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)
_socket.getaddrinfo = _ipv4_only

import os
import sys
import threading

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_app.app import create_app
from trading_app.app.utils.logger import logger
from dotenv import load_dotenv
import glob

# Load environment variables from env/*.env if available
# This ensures local config (like Mine.env) is loaded when running main.py directly
def load_local_env():
    """Load the first .env file found in the env/ directory."""
    try:
        # Path to env directory: current_dir/env
        env_dir = os.path.join(os.path.dirname(__file__), 'env')
        if os.path.exists(env_dir):
            env_files = glob.glob(os.path.join(env_dir, '*.env'))
            if env_files:
                # Prioritize Mine.env if it exists, otherwise take the first one
                target_env = next((f for f in env_files if 'Mine.env' in f), env_files[0])
                load_dotenv(target_env)
                print(f"✅ Loaded environment from {os.path.basename(target_env)}")
                return True
    except Exception as e:
        print(f"⚠️  Failed to load local env: {e}")
    return False

# Load env before importing other modules that might use os.getenv
load_local_env()

def ensure_ssh_tunnel():
    """Delegate to the shared implementation in kite_order_services."""
    from trading_app.service.kite_order_services import ensure_ssh_tunnel as _ensure
    ok = _ensure()
    if ok:
        logger.info('[Proxy] SSH tunnel is up')
    else:
        logger.warning('[Proxy] SSH tunnel could not be started — orders requiring proxy will fail')

# Global reference to live monitoring instances
live_monitors = {}

def start_intraday_9_20_monitoring():
    """Initialize and start the Intraday 9:20 live signal monitoring."""
    try:
        from trading_app.app.intraday_option.intraday_9_20_live_signal import Intraday920LiveSignal
        from trading_app.app.utils.token_manager import get_access_token
        from kiteconnect import KiteConnect
        
        logger.info("🚀 Starting Intraday 9:20 Live Signal Monitoring...")
        
        # Get access token and create Kite instance
        access_token = get_access_token()
        api_key = os.getenv('API_KEY')
        
        if not access_token or not api_key:
            logger.warning("⚠️  Kite connection not available - missing credentials")
            return
        
        try:
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(access_token)
        except Exception as e:
            logger.error(f"Error creating KiteConnect instance: {e}")
            return
        
        # Create monitors for NIFTY only
        symbols = ['NIFTY']
        
        # Get username from environment (set by Flask session or use default)
        username = os.getenv('MONITORING_USERNAME', 'default_user')
        
        for symbol in symbols:
            try:
                # Pass username for per-user Excel logging
                monitor = Intraday920LiveSignal(kite, symbol=symbol, username=username)
                
                # Start monitoring if it's a market day and within hours
                if monitor.is_market_day():
                    if monitor.start_monitoring():
                        live_monitors[symbol] = monitor
                        logger.info(f"✅ Live monitoring started for {symbol} (user: {username})")
                    else:
                        logger.warning(f"⚠️  Could not start monitoring for {symbol}")
                else:
                    logger.info(f"ℹ️  Not a market day - {symbol} monitoring not started")
            except Exception as e:
                logger.error(f"Error starting monitoring for {symbol}: {str(e)}", exc_info=True)
        
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
        
        # Get username from environment (loaded by UserEnvManager during login)
        username = os.getenv('USERNAME', 'default')
        
        live_signal = HighLowLiveSignal(symbol='NIFTY', username=username)
        live_signal.start_live_monitoring()
    except Exception as e:
        logger.error(f"Error in live monitoring thread: {e}")

def main():
    """Run the Flask application and live monitoring."""
    ensure_ssh_tunnel()
    from trading_app.service.kite_order_services import start_tunnel_watchdog
    start_tunnel_watchdog(interval=30)
    app = create_app()

    # Note: Live monitoring will be started via API endpoint after user login
    # This ensures per-user credentials and Excel logging
    logger.info("ℹ️  Live monitoring will start after user login via /api/start-monitoring")
    
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
