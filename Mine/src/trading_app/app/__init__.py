"""
Application factory and initialization.
Creates and configures the Flask application.
"""
import os
from flask import Flask
from trading_app.app.config import current_config
from trading_app.app.extensions import init_extensions
from trading_app.app.utils.logger import logger

def create_app(config=None):
    """Application factory function."""
    # Get paths for static and template folders (in root, not in src)
    # Go up from src/trading_app/app to root, then to templates/static
    basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
    static_path = os.path.join(basedir, 'static')
    template_path = os.path.join(basedir, 'templates')
    
    app = Flask(__name__, static_folder=static_path, template_folder=template_path)
    
    # Load configuration
    if config is None:
        config = current_config
    app.config.from_object(config)
    
    # Initialize extensions
    init_extensions(app)
    
    logger.info(f"Flask app created with config: {config.__name__}")
    logger.info(f"Templates: {template_path}")
    logger.info(f"Static: {static_path}")
    
    # Register blueprints
    from trading_app.app.routes import register_blueprints
    register_blueprints(app)
    
    return app

