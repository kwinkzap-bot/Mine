"""
Flask extensions initialization.
All Flask extensions are initialized here to avoid circular imports.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

# Initialize extensions (without app binding)
limiter = Limiter(key_func=get_remote_address)
csrf = CSRFProtect()

def init_extensions(app):
    """Initialize all Flask extensions with the app."""
    limiter.init_app(app)
    csrf.init_app(app)
    
    # Initialize scheduler for recurring tasks
    from app.scheduler import init_scheduler
    init_scheduler(app)
    
    return app
