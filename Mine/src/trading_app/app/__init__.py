"""
Application factory and initialization.
Creates and configures the Flask application.
"""
import os
from flask import Flask, jsonify, session
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
    
    # Register template helpers for safe CSRF token access
    @app.template_global()
    def csrf_token():
        """Safe CSRF token function that works whether CSRF is enabled or not."""
        if app.config.get('WTF_CSRF_ENABLED', False):
            try:
                from flask_wtf.csrf import generate_csrf
                return generate_csrf()
            except Exception:
                return ''
        return ''
    
    logger.info(f"Flask app created with config: {config.__name__}")
    logger.info(f"Templates: {template_path}")
    logger.info(f"Static: {static_path}")
    
    # Add after-request handler to persist access token to environment
    # This ensures token survives socket pool flushes and debug session resets
    @app.after_request
    def persist_access_token(response):
        """Persist access token from session to environment after each request.
        
        This provides continuity when socket pools are flushed during debugging.
        If the session has a valid access token, write it to .env and os.environ.
        """
        try:
            access_token = session.get('access_token')
            if access_token and response.status_code < 400:  # Only persist on successful responses
                # Update environment variable (runtime)
                os.environ['ACCESS_TOKEN'] = access_token
                logger.debug(f"Access token persisted to environment (token: {access_token[:20]}...)")
        except Exception as e:
            logger.warning(f"Could not persist access token to environment: {e}")
        
        return response
    
    # Register error handlers
    @app.errorhandler(400)
    def bad_request(e):
        """Handle 400 Bad Request."""
        return jsonify({'success': False, 'error': 'Bad Request'}), 400
    
    @app.errorhandler(401)
    def unauthorized(e):
        """Handle 401 Unauthorized."""
        return jsonify({
            'success': False,
            'error': 'Unauthorized - Please login first',
            'auth_error': True
        }), 401
    
    @app.errorhandler(403)
    def forbidden(e):
        """Handle 403 Forbidden - returns helpful message instead of generic error."""
        # Log the 403 error with details
        logger.warning(f"403 Forbidden Error - {str(e)}")
        logger.warning(f"  Error type: {type(e).__name__}")
        logger.warning(f"  This usually means:")
        logger.warning(f"    1. Access token has expired (expires at 3:20 PM IST)")
        logger.warning(f"    2. CSRF token is invalid")
        logger.warning(f"    3. Session has expired")
        
        # Return helpful JSON response
        return jsonify({
            'success': False,
            'error': 'Access Forbidden (403)',
            'message': 'This usually means your session has expired or access token is invalid',
            'solutions': [
                'Try refreshing the page',
                'If still getting 403, go to /auth/login to get a fresh token',
                'Zerodha tokens expire at 3:20 PM IST - you may need to re-login'
            ],
            'debug_url': '/debug/status'
        }), 403
    
    @app.errorhandler(404)
    def not_found(e):
        """Handle 404 Not Found."""
        return jsonify({'success': False, 'error': 'Not Found'}), 404
    
    @app.errorhandler(500)
    def internal_error(e):
        """Handle 500 Internal Server Error."""
        logger.error(f"Internal server error: {e}")
        return jsonify({
            'success': False,
            'error': 'Internal Server Error'
        }), 500
    
    # Inject PWA flag into every template
    @app.context_processor
    def inject_pwa():
        return {'pwa_enabled': os.getenv('PWA_ENABLED', 'false').lower() == 'true'}

    # Register blueprints
    from trading_app.app.routes import register_blueprints
    register_blueprints(app)

    return app

