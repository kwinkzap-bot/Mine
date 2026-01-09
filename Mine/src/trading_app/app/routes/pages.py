"""Page routes for rendering templates."""
from flask import Blueprint, render_template, session, redirect, url_for, jsonify
from functools import wraps
import os

pages_bp = Blueprint('pages', __name__)

def login_required(f):
    """Decorator to require login for a page."""
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
                    'error': 'Authentication required. Please login first.',
                    'auth_error': True
                }), 401
            # For page requests, redirect to login
            return redirect(url_for('auth.login'))
        
        # Ensure session has the token for consistency
        if 'access_token' not in session:
            session['access_token'] = access_token
            session.permanent = True
        
        return f(*args, **kwargs)
    return decorated_function

@pages_bp.route('/')
def index():
    """Home page."""
    return render_template('index.html')

@pages_bp.route('/strategy')
@login_required
def strategy():
    """Strategy backtest page."""
    return render_template('strategy.html')

@pages_bp.route('/cpr-filter')
@login_required
def cpr_filter():
    """CPR filter page."""
    return render_template('cpr_filter.html')

@pages_bp.route('/historical')
@login_required
def historical():
    """Historical data page."""
    return render_template('historical.html')

@pages_bp.route('/options-chart')
def options_chart():
    """Options chart page."""
    return render_template('options_chart.html')

@pages_bp.route('/multi-strike')
@login_required
def multi_strike():
    """Multi-strike options page."""
    return render_template('multi_strike.html')

@pages_bp.route('/intraday-option')
@login_required
def intraday_option():
    """Intraday option trading page."""
    return render_template('intraday_option.html')

@pages_bp.route('/login')
def login():
    """Login page - redirects to /auth/login."""
    from flask import redirect, url_for
    return redirect(url_for('auth.login'))
