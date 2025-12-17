"""Routes package."""
from flask import Blueprint

def register_blueprints(app):
    """Register all blueprints with the Flask app."""
    # Import blueprints
    from app.routes.pages import pages_bp
    from app.routes.api import api_bp
    from app.routes.auth import auth_bp
    
    # Register blueprints
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(auth_bp, url_prefix='/auth')
