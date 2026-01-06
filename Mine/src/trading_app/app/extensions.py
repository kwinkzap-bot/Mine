"""
Flask extensions initialization.
All Flask extensions are initialized here to avoid circular imports.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_cors import CORS

# Initialize extensions (without app binding)
# Note: For localhost development, we use a permissive key function that treats all localhost as same
def localhost_key_func():
    """Custom key function for rate limiting that allows localhost to bypass limits."""
    from flask import request
    # Allow localhost with very high limits
    if request.remote_addr in ('127.0.0.1', 'localhost'):
        return 'localhost'
    return get_remote_address()

limiter = Limiter(key_func=localhost_key_func, default_limits=[])
csrf = CSRFProtect()

def init_extensions(app):
    """Initialize all Flask extensions with the app."""
    limiter.init_app(app)
    csrf.init_app(app)
    
    # Enable CORS for localhost development
    CORS(app, resources={
        r"/*": {
            "origins": ["http://localhost:*", "http://127.0.0.1:*", "http://localhost", "http://127.0.0.1"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
            "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
            "expose_headers": ["Content-Type"],
            "supports_credentials": True,
            "max_age": 3600
        }
    })
    
    # Initialize scheduler for recurring tasks
    from trading_app.app.scheduler import init_scheduler
    init_scheduler(app)
    
    return app
