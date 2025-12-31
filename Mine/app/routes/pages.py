"""Page routes for rendering templates."""
from flask import Blueprint, render_template

pages_bp = Blueprint('pages', __name__)

@pages_bp.route('/')
def index():
    """Home page."""
    return render_template('index.html')

@pages_bp.route('/strategy')
def strategy():
    """Strategy backtest page."""
    return render_template('strategy.html')

@pages_bp.route('/cpr-filter')
def cpr_filter():
    """CPR filter page."""
    return render_template('cpr_filter.html')

@pages_bp.route('/historical')
def historical():
    """Historical data page."""
    return render_template('historical.html')

@pages_bp.route('/options-chart')
def options_chart():
    """Options chart page."""
    return render_template('options_chart.html')

@pages_bp.route('/login')
def login():
    """Login page - redirects to /auth/login."""
    from flask import redirect, url_for
    return redirect(url_for('auth.login'))
