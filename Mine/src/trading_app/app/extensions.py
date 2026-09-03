"""
Flask extensions initialization.
All Flask extensions are initialized here to avoid circular imports.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_cors import CORS
from flask import request

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
    
    # Completely disable CSRF protection to prevent 403 errors
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['WTF_CSRF_CHECK_DEFAULT'] = False
    
    # Enable CORS for all requests with permissive settings for localhost
    CORS(app, 
         origins='*',  # Allow all origins for localhost development
         methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'HEAD', 'PATCH'],
         allow_headers=['Content-Type', 'Authorization', 'X-Requested-With', 'X-CSRF-Token', 'Cache-Control'],
         expose_headers=['Content-Type', 'X-Total-Count'],
         supports_credentials=False,  # Must be False when origins='*'
         max_age=86400)
    
    # Add a before_request handler to allow OPTIONS requests globally and prevent 403 errors
    @app.before_request
    def handle_preflight():
        """Handle CORS preflight requests and prevent 403 errors."""
        from flask import request, make_response
        if request.method == 'OPTIONS':
            response = make_response('', 200)
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, X-CSRF-Token, Cache-Control'
            return response
    
    # Refresh session timeout on each request to prevent 403 errors during active trading
    @app.before_request
    def refresh_session_timeout():
        """Refresh session timeout on every request to keep user logged in during trading."""
        from flask import request, session

        # A request that arrives with no session cookie has no session to
        # refresh — and marking one permanent here makes an EMPTY session
        # non-empty, so Flask writes a fresh empty cookie that overwrites the
        # real login. That is not hypothetical: ICICI Direct posts its login
        # callback cross-site, a SameSite=Lax cookie is never sent on a
        # cross-site POST, and this hook logged the user out every single time
        # they connected the broker. Any third-party callback hits the same
        # trap, so the guard belongs here rather than in one route.
        if app.config.get('SESSION_COOKIE_NAME', 'session') not in request.cookies:
            return

        session.permanent = True
        # The session expiration is automatically extended on each request
    
    # Add error handler for 403 errors to provide better debugging
    @app.errorhandler(403)
    def handle_forbidden(e):
        """Handle 403 Forbidden errors with detailed logging."""
        from flask import request, jsonify
        from trading_app.app.utils.logger import logger
        
        logger.warning(f"403 Forbidden: {request.method} {request.url} from {request.remote_addr}")
        logger.warning(f"Headers: {dict(request.headers)}")
        
        return jsonify({
            'success': False,
            'error': 'Access forbidden - this is likely a temporary issue',
            'message': 'Please refresh the page and try again',
            'debug_info': {
                'method': request.method,
                'path': request.path,
                'remote_addr': request.remote_addr
            }
        }), 403
    
    # Initialize scheduler for recurring tasks
    from trading_app.app.scheduler import init_scheduler
    init_scheduler(app)
    
    return app
