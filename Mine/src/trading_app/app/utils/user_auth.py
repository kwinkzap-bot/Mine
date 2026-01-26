"""
User authentication utilities.
Handles username/password authentication for application access.
"""
import os
import hashlib
import json
from typing import Optional, Dict
from functools import wraps
from flask import session, redirect, url_for, jsonify, request
from trading_app.app.utils.logger import logger

# User database file
USER_DB_FILE = os.path.join(os.path.dirname(__file__), '../../../.users.json')

def hash_password(password: str) -> str:
    """
    Hash a password using SHA-256.
    
    Args:
        password: Plain text password
        
    Returns:
        Hashed password
    """
    return hashlib.sha256(password.encode()).hexdigest()

def load_users() -> Dict[str, str]:
    """
    Load users from the users database file.
    
    Returns:
        Dictionary of username: hashed_password pairs
    """
    if not os.path.exists(USER_DB_FILE):
        # Create default user on first run
        default_users = {
            'Mine': hash_password('Mine')
        }
        save_users(default_users)
        logger.info("Created default user database with Mine:Mine")
        return default_users
    
    try:
        with open(USER_DB_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading users: {e}")
        return {}

def save_users(users: Dict[str, str]) -> bool:
    """
    Save users to the users database file.
    
    Args:
        users: Dictionary of username: hashed_password pairs
        
    Returns:
        True if successful, False otherwise
    """
    try:
        with open(USER_DB_FILE, 'w') as f:
            json.dump(users, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving users: {e}")
        return False

def verify_user(username: str, password: str) -> bool:
    """
    Verify username and password.
    
    Args:
        username: Username
        password: Plain text password
        
    Returns:
        True if credentials are valid, False otherwise
    """
    users = load_users()
    if username not in users:
        return False
    
    hashed_password = hash_password(password)
    return users[username] == hashed_password

def add_user(username: str, password: str) -> bool:
    """
    Add a new user.
    
    Args:
        username: Username
        password: Plain text password
        
    Returns:
        True if user added successfully, False if user already exists
    """
    users = load_users()
    if username in users:
        return False
    
    users[username] = hash_password(password)
    return save_users(users)

def change_password(username: str, old_password: str, new_password: str) -> bool:
    """
    Change user password.
    
    Args:
        username: Username
        old_password: Current plain text password
        new_password: New plain text password
        
    Returns:
        True if password changed successfully, False otherwise
    """
    if not verify_user(username, old_password):
        return False
    
    users = load_users()
    users[username] = hash_password(new_password)
    return save_users(users)

def is_user_authenticated() -> bool:
    """
    Check if user is authenticated.
    
    Returns:
        True if user is logged in, False otherwise
    """
    return session.get('user_authenticated', False) and session.get('username') is not None

def login_user(username: str) -> None:
    """
    Log in a user by setting session variables.
    
    Args:
        username: Username to log in
    """
    session['user_authenticated'] = True
    session['username'] = username
    session.permanent = True
    logger.info(f"User '{username}' logged in successfully")

def logout_user() -> None:
    """
    Log out the current user by clearing session variables.
    """
    username = session.get('username', 'Unknown')
    session.pop('user_authenticated', None)
    session.pop('username', None)
    logger.info(f"User '{username}' logged out")

def require_user_auth(f):
    """
    Decorator to require user authentication for routes.
    This is separate from broker API authentication.
    
    Usage:
        @app.route('/protected')
        @require_user_auth
        def protected_route():
            return 'Protected content'
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_user_authenticated():
            # For API requests, return JSON error
            if request.path.startswith('/api'):
                return jsonify({
                    'success': False,
                    'error': 'User authentication required. Please login first.',
                    'auth_required': True
                }), 401
            # For page requests, redirect to login
            return redirect(url_for('auth.user_login'))
        
        return f(*args, **kwargs)
    return decorated_function
