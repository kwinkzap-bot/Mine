"""Authentication routes."""
from flask import Blueprint, redirect, request, session, url_for, jsonify
from kiteconnect import KiteConnect
import os
from app.utils.logger import logger
from app.config import current_config

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login')
def login():
    """Redirect to Zerodha Kite OAuth login."""
    logger.info("Login request received")
    
    api_key = os.getenv('API_KEY')
    if not api_key:
        logger.error("API_KEY not configured in environment")
        return jsonify({'error': 'API_KEY not configured'}), 500
    
    try:
        # Initialize KiteConnect
        kite = KiteConnect(api_key=api_key)
        
        # Get login URL for OAuth
        login_url = kite.login_url()
        logger.info(f"Redirecting to Zerodha login: {login_url}")
        
        # Store API key in session for use in callback
        session['api_key'] = api_key
        session.permanent = True
        
        return redirect(login_url)
    except Exception as e:
        logger.error(f"Error during login: {e}")
        return jsonify({'error': f'Login failed: {str(e)}'}), 500


@auth_bp.route('/callback')
def callback():
    """Handle Zerodha OAuth callback."""
    request_token = request.args.get('request_token')
    
    if not request_token:
        logger.warning("No request_token received in callback")
        return redirect(url_for('pages.index'))
    
    logger.info(f"Callback received with request_token: {request_token}")
    
    try:
        api_key = session.get('api_key') or os.getenv('API_KEY')
        api_secret = os.getenv('API_SECRET')
        
        if not api_key or not api_secret:
            logger.error("API credentials not configured")
            return jsonify({'error': 'API credentials not configured'}), 500
        
        # Initialize KiteConnect
        kite = KiteConnect(api_key=api_key)
        
        # Generate session (exchange request_token for access_token)
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data['access_token']
        
        # Store in session
        session['access_token'] = access_token
        session['request_token'] = request_token
        session.permanent = True
        
        logger.info("Session generated successfully, access_token stored")
        logger.info(f"User authenticated with access_token: {access_token[:20]}...")
        
        # Store in environment for later use
        os.environ['ACCESS_TOKEN'] = access_token
        
        return redirect(url_for('pages.index'))
    
    except Exception as e:
        logger.error(f"Error during callback: {e}", exc_info=True)
        return jsonify({'error': f'Authentication failed: {str(e)}'}), 500


@auth_bp.route('/logout')
def logout():
    """Logout user."""
    session.clear()
    logger.info("User logged out")
    return redirect(url_for('pages.index'))


@auth_bp.route('/status')
def status():
    """Check authentication status."""
    access_token = session.get('access_token') or os.getenv('ACCESS_TOKEN')
    is_authenticated = bool(access_token)
    
    return jsonify({
        'authenticated': is_authenticated,
        'has_access_token': is_authenticated,
        'has_request_token': bool(session.get('request_token'))
    })
