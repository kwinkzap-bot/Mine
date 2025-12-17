"""Authentication routes."""
from flask import Blueprint, redirect, request, session, url_for
from app.utils.logger import logger

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login')
def login():
    """Redirect to Zerodha login."""
    logger.info("Login request received")
    # Implement Zerodha OAuth flow here
    return redirect(url_for('pages.index'))

@auth_bp.route('/callback')
def callback():
    """Handle Zerodha callback."""
    request_token = request.args.get('request_token')
    if request_token:
        session['request_token'] = request_token
        logger.info("Request token received from Zerodha")
    return redirect(url_for('pages.index'))

@auth_bp.route('/logout')
def logout():
    """Logout user."""
    session.clear()
    logger.info("User logged out")
    return redirect(url_for('pages.index'))
