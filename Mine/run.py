"""
Application entry point.
Run the Flask application.
"""
import os
import sys
from app import create_app
from app.utils.logger import logger

def main():
    """Run the Flask application."""
    app = create_app()
    
    # Get host and port from environment
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'
    
    logger.info(f"Starting Flask app on {host}:{port} (Debug: {debug})")
    
    try:
        app.run(host=host, port=port, debug=debug)
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
