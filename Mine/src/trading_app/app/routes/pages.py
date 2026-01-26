"""Page routes for rendering templates."""
from flask import Blueprint, render_template, session, redirect, url_for, jsonify
from functools import wraps
import os
from trading_app.app.utils.user_auth import require_user_auth

pages_bp = Blueprint('pages', __name__)

def login_required(f):
    """
    Decorator to require Zerodha API login for a page.
    This checks for broker API access token.
    Note: This is separate from user authentication (username/password).
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import request as flask_request
        # Check session first, then fallback to environment variable
        access_token = session.get('access_token') or os.getenv('ACCESS_TOKEN')
        
        if not access_token:
            # For API requests, return JSON error instead of redirect
            if flask_request.path.startswith('/api'):
                return jsonify({
                    'success': False,
                    'error': 'Zerodha API authentication required. Please login first.',
                    'auth_error': True
                }), 401
            # For page requests, redirect to Zerodha login
            return redirect(url_for('auth.login'))
        
        # Ensure session has the token for consistency
        if 'access_token' not in session:
            session['access_token'] = access_token
            session.permanent = True
        
        return f(*args, **kwargs)
    return decorated_function

@pages_bp.route('/')
@require_user_auth
def index():
    """Home page - requires user authentication."""
    return render_template('index.html')

@pages_bp.route('/debug/status')
@require_user_auth
def debug_status():
    """Debug status endpoint - requires user auth."""
    from trading_app.app.utils.logger import logger
    
    access_token = session.get('access_token') or os.getenv('ACCESS_TOKEN')
    env_token = os.getenv('ACCESS_TOKEN')
    
    return jsonify({
        'status': 'ok',
        'session_token': access_token[:20] + '...' if access_token else None,
        'env_token': env_token[:20] + '...' if env_token else None,
        'message': 'Flask app is running - use /auth/login if you see 403 errors'
    })

@pages_bp.route('/strategy')
@require_user_auth
@login_required
def strategy():
    """Strategy backtest page."""
    return render_template('strategy.html')

@pages_bp.route('/cpr-filter')
@require_user_auth
@login_required
def cpr_filter():
    """CPR filter page."""
    return render_template('cpr_filter.html')

@pages_bp.route('/historical')
@require_user_auth
@login_required
def historical():
    """Historical data page."""
    return render_template('historical.html')

@pages_bp.route('/options-chart')
@require_user_auth
def options_chart():
    """Options chart page."""
    return render_template('options_chart.html')

@pages_bp.route('/multi-strike')
@require_user_auth
@login_required
def multi_strike():
    """Multi-strike options page."""
    return render_template('multi_strike.html')

@pages_bp.route('/intraday-option')
@require_user_auth
@login_required
def intraday_option():
    """Intraday option trading page."""
    return render_template('intraday_option.html')

@pages_bp.route('/intraday-920')
@require_user_auth
def intraday_920():
    """Intraday 9:20 strategy page."""
    return render_template('intraday_920.html')

@pages_bp.route('/login')
def login():
    """Login page - redirects to user login."""
    from flask import redirect, url_for
    return redirect(url_for('auth.user_login'))
