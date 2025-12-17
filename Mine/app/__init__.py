"""
Application factory and initialization.
Creates and configures the Flask application.
"""
import os
from flask import Flask
from app.config import current_config
from app.extensions import init_extensions
from app.utils.logger import logger

def create_app(config=None):
    """Application factory function."""
    # Get absolute paths for static and template folders
    basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
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
    from app.routes import register_blueprints
    register_blueprints(app)
    
    return app

