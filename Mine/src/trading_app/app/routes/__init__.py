"""Routes package."""
from flask import Blueprint

def register_blueprints(app):
    """Register all blueprints with the Flask app."""
    # Import blueprints
    from trading_app.app.routes.pages import pages_bp
    from trading_app.app.routes.api import api_bp
    from trading_app.app.routes.auth import auth_bp
    from trading_app.app.routes.oi_crossover_api import oi_crossover_bp
    from trading_app.app.routes.watchlist_api import watchlist_bp

    # Register blueprints
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(oi_crossover_bp, url_prefix='/api/oi-crossover')
    app.register_blueprint(watchlist_bp, url_prefix='/api/watchlist')
