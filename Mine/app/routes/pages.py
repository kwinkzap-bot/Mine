"""Page routes for rendering templates."""
from flask import Blueprint, render_template, session, redirect, url_for
from functools import wraps

pages_bp = Blueprint('pages', __name__)

def login_required(f):
    """Decorator to require login for a page."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'access_token' not in session or not session.get('access_token'):
            return redirect(url_for('auth.login'))
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
@login_required
def options_chart():
    """Options chart page."""
    return render_template('options_chart.html')

@pages_bp.route('/multi-strike')
@login_required
def multi_strike():
    """Multi-strike options page."""
    return render_template('multi_strike.html')

@pages_bp.route('/login')
def login():
    """Login page - redirects to /auth/login."""
    from flask import redirect, url_for
    return redirect(url_for('auth.login'))
