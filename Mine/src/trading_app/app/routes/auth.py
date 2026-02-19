"""Authentication routes."""
from flask import Blueprint, redirect, request, session, url_for, jsonify, render_template
from kiteconnect import KiteConnect
import os
from typing import Optional
from trading_app.app.utils.logger import logger
from trading_app.app.config import current_config
from trading_app.app.utils.token_manager import save_access_token, clear_access_token
from trading_app.app.utils.user_auth import (
    verify_user, is_user_authenticated, login_user, logout_user
)

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/user-login', methods=['GET', 'POST'])
def user_login():
    """
    User login page with username/password authentication.
    This is separate from broker API authentication.
    """
    if request.method == 'GET':
        # If already logged in, redirect to home
        if is_user_authenticated():
            return redirect(url_for('pages.index'))
        
        # Render login page
        return render_template('login.html')
    
    # POST request - process login
    try:
        data = request.get_json() or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username or not password:
            return jsonify({
                'success': False,
                'error': 'Please provide both username and password'
            }), 400
        
        # Verify credentials
        if verify_user(username, password):
            login_user(username)
            logger.info(f"✅ User '{username}' logged in successfully")
            
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'redirect': url_for('pages.index')
            })
        else:
            logger.warning(f"❌ Failed login attempt for username: {username}")
            return jsonify({
                'success': False,
                'error': 'Invalid username or password'
            }), 401
            
    except Exception as e:
        logger.error(f"Error during user login: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Login failed. Please try again.'
        }), 500

@auth_bp.route('/user-logout')
def user_logout():
    """Log out the current user."""
    logout_user()
    return redirect(url_for('auth.user_login'))

@auth_bp.route('/login')
def login():
    """Redirect to Zerodha Kite OAuth login.
    
    Supports multiple Kite instances via broker_id parameter.
    Uses user-specific credentials from username.env file.
    Usage: /auth/login or /auth/login?broker_id=kite_2
    """
    from trading_app.app.utils.user_env import UserEnvManager
    
    logger.info("Login request received")
    
    # Get current username from session
    username = session.get('username')
    if not username:
        logger.error("No user authenticated for login")
        return jsonify({'error': 'User not authenticated'}), 401
    
    # Get broker_id from query parameter (defaults to kite_1 for Zerodha)
    broker_id = request.args.get('broker_id', 'kite_1').lower()
    
    # Extract instance number from broker_id (e.g., 'kite_2' -> instance 2)
    instance_num = 1
    if '_' in broker_id:
        try:
            instance_num = int(broker_id.split('_')[-1])
        except (ValueError, IndexError):
            instance_num = 1
    
    # Get API credentials from user-specific .env file
    if instance_num == 1:
        # First instance uses original env var names
        api_key = UserEnvManager.get_user_var(username, 'API_KEY')
        api_secret = UserEnvManager.get_user_var(username, 'API_SECRET')
    else:
        # Additional instances use prefixed names
        api_key = UserEnvManager.get_user_var(username, f'KITE_{instance_num}_API_KEY')
        api_secret = UserEnvManager.get_user_var(username, f'KITE_{instance_num}_API_SECRET')
    
    if not api_key:
        logger.error(f"API_KEY not configured for {username} - broker_id: {broker_id}")
        return jsonify({'error': f'API_KEY not configured for {broker_id}'}), 500
    
    try:
        # Initialize KiteConnect
        kite = KiteConnect(api_key=api_key)
        
        # Get login URL for OAuth
        login_url = kite.login_url()
        logger.info(f"Redirecting to Zerodha login for {username} - {broker_id}: {login_url}")
        
        # Store credentials in session for use in callback
        session['broker_id'] = broker_id
        session['instance_num'] = instance_num
        session['api_key'] = api_key
        session['api_secret'] = api_secret
        session.permanent = True
        
        return redirect(login_url)
    except Exception as e:
        logger.error(f"Error during login for {username} - {broker_id}: {e}")
        return jsonify({'error': f'Login failed: {str(e)}'}), 500


@auth_bp.route('/callback')
def callback():
    """Handle Zerodha OAuth callback for any broker instance.
    
    Handles callbacks from multiple Kite instances (kite_1, kite_2, etc.)
    Saves tokens to user-specific .env file
    """
    from trading_app.app.utils.user_env import UserEnvManager
    
    request_token = request.args.get('request_token')
    
    if not request_token:
        logger.warning("No request_token received in callback")
        return redirect(url_for('pages.index'))
    
    logger.info(f"Callback received with request_token: {request_token}")
    
    try:
        # Get current username from session
        username = session.get('username')
        if not username:
            logger.error("No user authenticated in callback")
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Get credentials from session (set during login)
        broker_id = session.get('broker_id', 'kite_1')
        instance_num = session.get('instance_num', 1)
        api_key = session.get('api_key')
        api_secret = session.get('api_secret')
        
        if not api_key or not api_secret:
            logger.error(f"API credentials not found in session for {username} - {broker_id}")
            return jsonify({'error': 'API credentials not configured'}), 500
        
        # Initialize KiteConnect
        kite = KiteConnect(api_key=api_key)
        
        # Generate session (exchange request_token for access_token)
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data['access_token']  # type: ignore[index]
        
        # Store in session with broker_id context
        session['access_token'] = access_token
        session['request_token'] = request_token
        session['broker_id'] = broker_id
        session['instance_num'] = instance_num
        session.permanent = True
        
        logger.info(f"Session generated successfully for {username} - {broker_id}, access_token stored")
        logger.info(f"User authenticated with access_token: {access_token[:20]}...")
        
        # Store in environment for immediate use (with broker context)
        os.environ['ACCESS_TOKEN'] = access_token
        os.environ['REQUEST_TOKEN'] = request_token
        os.environ['ACTIVE_BROKER_ID'] = broker_id
        os.environ['ACTIVE_INSTANCE'] = str(instance_num)
        os.environ['ACTIVE_USER'] = username
        
        # Store in persistent cache to survive socket pool flushes
        save_access_token(access_token, request_token)
        logger.info(f"Token saved to persistent cache for {username} - {broker_id}")
        
        # Update user-specific .env file with new tokens (instance-specific if needed)
        _update_user_env_tokens(username, access_token, request_token, broker_id, instance_num)
        os.environ['ACCESS_TOKEN'] = access_token
        os.environ['REQUEST_TOKEN'] = request_token
        
        # Store in persistent cache to survive socket pool flushes
        save_access_token(access_token, request_token)
        logger.info(f"Token saved to persistent cache for {broker_id}")
        
        # Update .env file with new tokens (instance-specific if needed)
        _update_env_tokens(access_token, request_token, broker_id, instance_num)
        
        # Auto-start live monitoring after successful Kite authentication
        try:
            from trading_app.app.intraday_option.intraday_9_20_live_signal import Intraday920LiveSignal
            import threading
            
            def start_monitoring_async():
                """Start monitoring in background thread."""
                try:
                    kite_instance = KiteConnect(api_key=api_key)
                    kite_instance.set_access_token(access_token)
                    
                    monitor = Intraday920LiveSignal(kite_instance, symbol='NIFTY', username=username)
                    
                    # Check if it's a market day and start monitoring
                    if monitor.is_market_day():
                        if monitor.start_monitoring():
                            logger.info(f"✅ Auto-started live monitoring for {username}")
                        else:
                            logger.warning(f"Could not auto-start monitoring for {username}")
                    else:
                        logger.info(f"Not a market day - skipping monitoring for {username}")
                except Exception as e:
                    logger.error(f"Error auto-starting monitoring: {e}")
            
            # Start monitoring in background thread
            monitor_thread = threading.Thread(target=start_monitoring_async, daemon=True)
            monitor_thread.start()
            
        except Exception as e:
            logger.warning(f"Could not auto-start monitoring: {e}")
        
        return redirect(url_for('pages.index', login_success=True))
    
    except Exception as e:
        logger.error(f"Error during callback: {e}", exc_info=True)
        return jsonify({'error': f'Authentication failed: {str(e)}'}), 500


def _update_user_env_tokens(username: str, access_token: str, request_token: str, broker_id: str = 'kite_1', instance_num: int = 1) -> bool:
    """Update tokens in user-specific .env file.
    
    Saves tokens to the user's .env file (e.g., Kavin.env).
    For instance_num=1, updates the original ACCESS_TOKEN and REQUEST_TOKEN.
    For instance_num>1, updates instance-specific variables.
    
    Args:
        username: Username whose .env file to update
        access_token: New access token from broker
        request_token: New request token from broker
        broker_id: Broker identifier (e.g., 'kite_1', 'kite_2')
        instance_num: Instance number (1 for primary, 2+ for additional)
        
    Returns:
        True if successful, False otherwise
    """
    from trading_app.app.utils.user_env import UserEnvManager
    
    try:
        # Determine the token variable names based on instance number
        if instance_num == 1:
            access_token_var = 'ACCESS_TOKEN'
            request_token_var = 'REQUEST_TOKEN'
        else:
            access_token_var = f'ACCESS_TOKEN_{instance_num}'
            request_token_var = f'REQUEST_TOKEN_{instance_num}'
        
        # Save tokens to user-specific .env
        tokens_dict = {
            access_token_var: access_token,
            request_token_var: request_token
        }
        
        success = UserEnvManager.save_user_vars(username, tokens_dict)
        
        if success:
            logger.info(f"✓ {username}.env updated with new tokens for {broker_id} (instance {instance_num})")
            logger.info(f"  {access_token_var}: {access_token[:20]}...")
            logger.info(f"  {request_token_var}: {request_token[:20]}...")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Error updating tokens in {username}.env: {e}", exc_info=True)
        return False


def _update_env_tokens(username: str, access_token: str, request_token: str, broker_id: str = 'kite_1', instance_num: int = 1) -> bool:
    """Update ACCESS_TOKEN and REQUEST_TOKEN in .env file.
    
    Legacy function - kept for backward compatibility.
    Supports multiple broker instances. For instance_num=1, updates the original
    ACCESS_TOKEN and REQUEST_TOKEN. For instance_num>1, updates instance-specific
    variables like ACCESS_TOKEN_2, REQUEST_TOKEN_2, etc.
    
    Args:
        username: Username (or empty string for system .env)
        access_token: New access token from broker
        request_token: New request token from broker
        broker_id: Broker identifier (e.g., 'kite_1', 'kite_2')
        instance_num: Instance number (1 for primary, 2+ for additional)
        
    Returns:
        True if successful, False otherwise
    """
    if username:
        return _update_user_env_tokens(username, access_token, request_token, broker_id, instance_num)
    
    try:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
        
        if not os.path.exists(env_file):
            logger.warning(f"Environment file not found: {env_file}")
            return False
        
        # Read current .env file
        with open(env_file, 'r') as f:
            lines = f.readlines()
        
        # Determine the token variable names based on instance number
        if instance_num == 1:
            access_token_var = 'ACCESS_TOKEN'
            request_token_var = 'REQUEST_TOKEN'
        else:
            access_token_var = f'ACCESS_TOKEN_{instance_num}'
            request_token_var = f'REQUEST_TOKEN_{instance_num}'
        
        # Update or add tokens
        updated_lines = []
        access_token_found = False
        request_token_found = False
        
        for line in lines:
            if line.startswith(f'{access_token_var}='):
                updated_lines.append(f'{access_token_var}={access_token}\n')
                access_token_found = True
            elif line.startswith(f'{request_token_var}='):
                updated_lines.append(f'{request_token_var}={request_token}\n')
                request_token_found = True
            else:
                updated_lines.append(line)
        
        # Add tokens if they don't exist
        if not access_token_found:
            updated_lines.append(f'{access_token_var}={access_token}\n')
        
        if not request_token_found:
            updated_lines.append(f'{request_token_var}={request_token}\n')
        
        # Write updated .env file
        with open(env_file, 'w') as f:
            f.writelines(updated_lines)
        
        logger.info(f"✓ .env file updated with new tokens for {broker_id} (instance {instance_num})")
        logger.info(f"  {access_token_var}: {access_token[:20]}...")
        logger.info(f"  {request_token_var}: {request_token[:20]}...")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error updating .env file: {e}", exc_info=True)
        return False


@auth_bp.route('/login/kotak', methods=['GET', 'POST'])
def login_kotak():
    """Authenticate with Kotak Neo using credentials and TOTP secret."""
    
    if request.method == 'GET':
        # Return a form to collect 6-digit TOTP
        return jsonify({
            'requires_totp': True,
            'message': 'Please provide your 6-digit OTP from authenticator app',
            'fields': {
                'totp_secret': '6-Digit OTP (from Google/Microsoft Authenticator)',
                'mobile': 'Mobile Number (optional, will use from .env)',
                'ucc': 'UCC/Client Code (optional, will use from .env)',
                'mpin': 'MPIN (optional, will use from .env)'
            },
            'note': 'All fields except TOTP are optional and will be loaded from .env if not provided'
        })
    
    # POST request - process authentication
    logger.info("Kotak Neo login request received")
    
    try:
        # Get credentials from request body
        data = request.get_json() or {}
        
        from trading_app.app.utils.user_env import UserEnvManager
        
        # Get current username
        username = session.get('username')
        
        # Helper to get variable from request, then user env, then os env
        def get_var(key, env_key):
            val = data.get(key, '').strip()
            if val: return val
            if username:
                val = UserEnvManager.get_user_var(username, env_key)
                if val: return val
            return os.getenv(env_key, '')
        
        # Extract credentials
        totp_code = data.get('totp_secret', '').strip()
        access_token = get_var('access_token', 'KOTAK_ACCESS_TOKEN')
        mobile = get_var('mobile', 'KOTAK_MOBILE_NUMBER')
        ucc = get_var('ucc', 'KOTAK_UCC')
        mpin = get_var('mpin', 'KOTAK_MPIN')
        
        # Validate required fields
        missing_fields = []
        help_messages = {}
        
        if not totp_code:
            missing_fields.append('totp_code')
            help_messages['totp_code'] = 'Enter the current 6-digit code from your authenticator app (Google/Microsoft Authenticator)'
        
        if not access_token:
            missing_fields.append('access_token')
            help_messages['access_token'] = 'Go to Kotak Neo API Dashboard to get your ACCESS_TOKEN. See GET_ACCESS_TOKEN.md for instructions.'
        
        if not mobile:
            missing_fields.append('mobile')
            help_messages['mobile'] = 'Your registered mobile number (10 digits, without +91)'
        
        if not ucc:
            missing_fields.append('ucc')
            help_messages['ucc'] = 'Your Unique Client Code (find in Kotak Neo App → Profile)'
        
        if not mpin:
            missing_fields.append('mpin')
            help_messages['mpin'] = 'Your 6-digit trading PIN (used in Kotak Neo app to authorize orders)'
        
        if missing_fields:
            logger.warning(f"Kotak Neo credentials missing: {', '.join(missing_fields)}")
            return jsonify({
                'error': 'Missing required credentials',
                'missing_fields': missing_fields,
                'help': help_messages,
                'message': f'Please provide: {", ".join(missing_fields)}',
                'success': False,
                'documentation': 'See GET_ACCESS_TOKEN.md for setup instructions'
            }), 400
        
        # Import the service
        from trading_app.service.kotak_order_services import KotakOrderService
        
        # Create service instance with REST API credentials
        kotak_service = KotakOrderService(
            access_token=access_token,
            mobile_number=mobile,
            ucc=ucc,
            mpin=mpin,
            totp_secret=totp_code
        )
        
        # Authenticate
        logger.info(f"Attempting Kotak Neo authentication with UCC: {ucc[:3] if ucc and len(ucc) >= 3 else ucc}...")
        
        auth_result = kotak_service.authenticate()
        
        if auth_result:
            # Get trading tokens from successful authentication
            trading_token = kotak_service.trading_token or "authenticated"
            trading_sid = kotak_service.trading_sid
            base_url = kotak_service.base_url
            
            # Store in session
            session['kotak_trading_token'] = trading_token
            session['kotak_trading_sid'] = trading_sid
            session['kotak_base_url'] = base_url
            session['kotak_authenticated'] = True
            session['kotak_client_id'] = ucc
            session.permanent = True
            
            logger.info("✅ Kotak Neo authentication successful")
            
            # Store in environment
            os.environ['KOTAK_TRADING_TOKEN'] = trading_token
            if trading_sid:
                os.environ['KOTAK_TRADING_SID'] = trading_sid
            if base_url:
                os.environ['KOTAK_BASE_URL'] = base_url
            
            # Update .env file with tokens
            _update_kotak_env_credentials(
                trading_token=trading_token,
                trading_sid=trading_sid,
                base_url=base_url
            )
            
            return jsonify({
                'success': True,
                'message': 'Successfully authenticated with Kotak Neo',
                'authenticated': True
            })
        else:
            error_msg = getattr(kotak_service, 'last_error', None)
            logger.error(f"Kotak Neo authentication failed: {error_msg}")
            
            # Provide more specific error message
            user_msg = 'Authentication failed. '
            if error_msg:
                user_msg += str(error_msg)
            else:
                user_msg += 'Please verify: 1) 6-digit OTP is correct and not expired, 2) Mobile number, UCC, and MPIN in .env are accurate, 3) System clock is synchronized.'
            
            return jsonify({
                'error': user_msg,
                'success': False,
                'debug_info': str(error_msg) if error_msg else None
            }), 401
        
    except Exception as e:
        logger.error(f"Error during Kotak Neo login: {e}", exc_info=True)
        return jsonify({
            'error': f'Kotak Neo login failed: {str(e)}',
            'success': False
        }), 500


def _update_kotak_env_credentials(trading_token: Optional[str] = None,
                                  trading_sid: Optional[str] = None,
                                  base_url: Optional[str] = None) -> bool:
    """Update Kotak Neo trading tokens in .env file.
    
    Args:
        trading_token: Trading token from authentication
        trading_sid: Trading session ID
        base_url: Base URL for API calls
        
    Returns:
        True if successful, False otherwise
    """
    try:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
        
        if not os.path.exists(env_file):
            logger.warning(f"Environment file not found: {env_file}")
            return False
        
        # Read current .env file
        with open(env_file, 'r') as f:
            lines = f.readlines()
        
        # Update or add credentials
        updated_lines = []
        found_keys = set()
        
        updates = {}
        
        if trading_token:
            updates['KOTAK_TRADING_TOKEN'] = trading_token
        if trading_sid:
            updates['KOTAK_TRADING_SID'] = trading_sid
        if base_url:
            updates['KOTAK_BASE_URL'] = base_url
        
        for line in lines:
            updated = False
            for key, value in updates.items():
                if line.startswith(f'{key}='):
                    updated_lines.append(f'{key}={value}\n')
                    found_keys.add(key)
                    updated = True
                    break
            
            if not updated:
                updated_lines.append(line)
        
        # Add missing keys
        for key, value in updates.items():
            if key not in found_keys:
                updated_lines.append(f'\n{key}={value}\n')
        
        # Write updated .env file
        with open(env_file, 'w') as f:
            f.writelines(updated_lines)
        
        logger.info(f"✓ .env file updated with Kotak Neo trading tokens")
        if trading_token:
            logger.info(f"  TRADING_TOKEN: {trading_token[:20]}...")
        if trading_sid:
            logger.info(f"  TRADING_SID: {trading_sid}")
        if base_url:
            logger.info(f"  BASE_URL: {base_url}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error updating .env file with Kotak credentials: {e}", exc_info=True)
        return False


@auth_bp.route('/login/dhan', methods=['GET', 'POST'])
def login_dhan():
    """Authenticate with Dhan using access token."""
    
    if request.method == 'GET':
        # Return a form to collect access token
        return jsonify({
            'requires_token': True,
            'message': 'Please provide your Dhan Access Token',
            'fields': {
                'access_token': 'Access Token (from web.dhan.co → My Profile → Access DhanHQ APIs)',
                'client_id': 'Client ID (optional, will be fetched from profile)'
            },
            'note': 'Get Access Token from web.dhan.co → My Profile → Access DhanHQ APIs → Generate Access Token (24-hour validity)'
        })
    
    # POST request - process authentication
    logger.info("Dhan login request received")
    
    try:
        # Get credentials from request body
        data = request.get_json() or {}
        
        # Get current username
        username = session.get('username')
        from trading_app.app.utils.user_env import UserEnvManager
        
        # Helper to get variable from request, then user env, then os env
        def get_var(key, env_key):
            val = data.get(key, '').strip()
            if val: return val
            if username:
                val = UserEnvManager.get_user_var(username, env_key)
                if val: return val
            return os.getenv(env_key, '')
        
        # Extract credentials
        access_token = get_var('access_token', 'DHAN_ACCESS_TOKEN')
        client_id = get_var('client_id', 'DHAN_CLIENT_ID')
        
        # Validate required fields
        if not access_token:
            logger.warning("Dhan access token missing")
            return jsonify({
                'error': 'Missing access token',
                'help': 'Get Access Token from: web.dhan.co → My Profile → Access DhanHQ APIs → Generate Access Token',
                'success': False
            }), 400
        
        # Import the service
        from trading_app.service.dhan_order_services import DhanOrderService
        
        # Create service instance
        dhan_service = DhanOrderService(
            access_token=access_token,
            client_id=client_id
        )
        
        # Verify credentials
        logger.info("Attempting Dhan authentication...")
        
        if dhan_service.verify_credentials():
            # Store in session
            session['dhan_access_token'] = dhan_service.access_token
            session['dhan_client_id'] = dhan_service.client_id
            session['dhan_authenticated'] = True
            session.permanent = True
            
            logger.info(f"✅ Dhan authentication successful for Client ID: {dhan_service.client_id}")
            
            # Store in environment (ensure values are not None)
            if dhan_service.access_token:
                os.environ['DHAN_ACCESS_TOKEN'] = dhan_service.access_token
            if dhan_service.client_id:
                os.environ['DHAN_CLIENT_ID'] = dhan_service.client_id
            
            # Update .env file
            _update_dhan_env_credentials(
                access_token=dhan_service.access_token,
                client_id=dhan_service.client_id
            )
            
            return jsonify({
                'success': True,
                'message': 'Successfully authenticated with Dhan',
                'authenticated': True,
                'client_id': dhan_service.client_id
            })
        else:
            error_msg = dhan_service.last_error or 'Verification failed'
            logger.error(f"Dhan authentication failed: {error_msg}")
            
            return jsonify({
                'error': f'Authentication failed: {error_msg}',
                'success': False,
                'help': 'Verify that your access token is valid and not expired (24-hour validity)'
            }), 401
        
    except Exception as e:
        logger.error(f"Error during Dhan login: {e}", exc_info=True)
        return jsonify({
            'error': f'Dhan login failed: {str(e)}',
            'success': False
        }), 500


def _update_dhan_env_credentials(access_token: Optional[str] = None,
                                 client_id: Optional[str] = None) -> bool:
    """Update Dhan credentials in .env file.
    
    Args:
        access_token: Dhan access token
        client_id: Dhan client ID
        
    Returns:
        True if successful, False otherwise
    """
    try:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
        
        if not os.path.exists(env_file):
            logger.warning(f"Environment file not found: {env_file}")
            return False
        
        # Read current .env file
        with open(env_file, 'r') as f:
            lines = f.readlines()
        
        # Update or add credentials
        updated_lines = []
        found_keys = set()
        
        updates = {}
        
        if access_token:
            updates['DHAN_ACCESS_TOKEN'] = access_token
        if client_id:
            updates['DHAN_CLIENT_ID'] = client_id
        
        for line in lines:
            updated = False
            for key, value in updates.items():
                if line.startswith(f'{key}='):
                    updated_lines.append(f'{key}={value}\n')
                    found_keys.add(key)
                    updated = True
                    break
            
            if not updated:
                updated_lines.append(line)
        
        # Add missing keys
        for key, value in updates.items():
            if key not in found_keys:
                updated_lines.append(f'\n{key}={value}\n')
        
        # Write updated .env file
        with open(env_file, 'w') as f:
            f.writelines(updated_lines)
        
        logger.info(f"✓ .env file updated with Dhan credentials")
        if access_token:
            logger.info(f"  ACCESS_TOKEN: {access_token[:20]}...")
        if client_id:
            logger.info(f"  CLIENT_ID: {client_id}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error updating .env file with Dhan credentials: {e}", exc_info=True)
        return False


@auth_bp.route('/login/fyers')
def fyers_login():
    """
    Redirect to Fyers OAuth login (similar to Kite login).
    Initiates OAuth 2.0 flow with automatic token management.
    """
    logger.info("[Fyers Login] Login request received")
    
    # Get current username
    username = session.get('username')
    from trading_app.app.utils.user_env import UserEnvManager
    
    # Helper to get variable from user env, then os env
    def get_var(env_key):
        if username:
            val = UserEnvManager.get_user_var(username, env_key)
            if val: return val
        return os.getenv(env_key, '')
    
    app_id = get_var('FYERS_APP_ID')
    secret_key = get_var('FYERS_SECRET_KEY')
    
    if not app_id or not secret_key:
        error_msg = "Fyers credentials not configured. "
        if not app_id:
            error_msg += "Missing FYERS_APP_ID. "
        if not secret_key:
            error_msg += "Missing FYERS_SECRET_KEY. "
        error_msg += "Please add these to your user's env file (e.g., Mine/env/Kavin.env). "
        error_msg += "Get credentials from: https://myapi.fyers.in/dashboard/"
        
        logger.error(f"[Fyers Login] {error_msg}")
        return jsonify({
            'error': error_msg,
            'details': 'FYERS_APP_ID and FYERS_SECRET_KEY must be configured in user environment'
        }), 500
    
    try:
        from trading_app.service.fyers_order_services import FyersOrderService
        
        # Initialize Fyers service
        fyers_service = FyersOrderService(app_id=app_id)
        
        # Generate OAuth URL
        auth_url = fyers_service.generate_auth_code_url()
        
        if not auth_url:
            logger.error("[Fyers Login] Failed to generate OAuth URL")
            return jsonify({'error': 'Failed to generate OAuth URL'}), 500
        
        logger.info(f"[Fyers Login] Redirecting to Fyers OAuth: {auth_url[:50]}...")
        
        # Store app_id in session for use in callback
        session['fyers_app_id'] = app_id
        session.permanent = True
        
        return redirect(auth_url)
        
    except Exception as e:
        logger.error(f"[Fyers Login] Error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/callback/fyers')
@auth_bp.route('/login/fyers/callback')
def fyers_oauth_callback():
    """
    Handle Fyers OAuth callback (similar to Kite callback).
    Fyers redirects here after user authorizes with auth code.
    Automatically exchanges code for access token and stores in .env.
    
    Query parameters:
    - auth_code: Authorization code (required) - Fyers uses 'auth_code'
    - code: HTTP status code (Fyers v3)
    - s: Status field (Fyers v3)
    - state: State parameter (for security validation)
    """
    # Fyers sends auth_code, not code
    auth_code = request.args.get('auth_code', '').strip()
    
    # Also try 'code' parameter as fallback (for compatibility)
    if not auth_code:
        auth_code = request.args.get('code', '').strip()
    
    state = request.args.get('state', '').strip()
    error = request.args.get('error', '').strip()
    error_description = request.args.get('error_description', '').strip()
    
    logger.info(f"[Fyers Callback] Received OAuth callback")
    logger.info(f"[Fyers Callback] auth_code present: {bool(auth_code)}, error: {error if error else 'None'}")
    
    # Handle OAuth errors
    if error:
        logger.error(f"[Fyers Callback] Fyers OAuth error: {error} - {error_description}")
        return render_template('auth_error.html', 
                             error=f'Fyers authorization failed: {error}',
                             error_description=error_description), 400
    
    if not auth_code:
        logger.error("[Fyers Callback] No auth code received from Fyers")
        logger.error(f"[Fyers Callback] Request args: {dict(request.args)}")
        return render_template('auth_error.html', 
                             error='No authorization code received from Fyers'), 400
    
    try:
        from trading_app.service.fyers_order_services import FyersOrderService
        
        logger.info(f"[Fyers Callback] Exchanging auth code for access token...")
        
        # Initialize Fyers service (will read credentials from env)
        # We need to manually pass credentials because FyersOrderService might rely on os.getenv
        # which doesn't see UserEnvManager values unless explicitly passed or patched
        
        # Get username from session
        username = session.get('username')
        
        # Get credentials using UserEnvManager
        from trading_app.app.utils.user_env import UserEnvManager
        app_id = UserEnvManager.get_user_var(username, 'FYERS_APP_ID') if username else os.getenv('FYERS_APP_ID')
        
        # Initialize service with explicit app_id
        fyers_service = FyersOrderService(app_id=app_id)
        
        # Exchange auth code for access token
        if fyers_service.generate_access_token(auth_code):
            access_token = fyers_service.access_token
            
            # Store in session
            session['fyers_access_token'] = access_token
            session['fyers_authenticated'] = True
            session.permanent = True
            
            # Store in environment
            if access_token:
                os.environ['FYERS_ACCESS_TOKEN'] = access_token
                
                # Update .env file (like Kite callback does)
                _update_fyers_env_credentials(access_token=access_token, username=session.get('username'))
                
                logger.info(f"[Fyers Callback] ✅ Authentication successful")
                logger.info(f"[Fyers Callback] Access token: {access_token[:30]}...")
            
            # Redirect to home page (like Kite callback)
            return redirect(url_for('pages.index'))
        else:
            error_msg = fyers_service.last_error or 'Failed to generate access token'
            logger.error(f"[Fyers Callback] Token generation failed: {error_msg}")
            
            return render_template('auth_error.html', 
                                 error='Token generation failed',
                                 error_description=error_msg), 401
            
    except Exception as e:
        logger.error(f"[Fyers Callback] Exception: {e}", exc_info=True)
        return render_template('auth_error.html', 
                             error='OAuth callback processing failed',
                             error_description=str(e)), 500


def _update_fyers_env_credentials(access_token: Optional[str] = None, username: Optional[str] = None) -> bool:
    """Update Fyers credentials in .env file (async/non-blocking).
    
    Args:
        access_token: Fyers access token
        
    Returns:
        True if update was queued, False if there was an error
    """
    try:
        import threading
        
        # Use UserEnvManager if username is available
        if username:
            from trading_app.app.utils.user_env import UserEnvManager
            success = UserEnvManager.save_user_vars(username, {'FYERS_ACCESS_TOKEN': access_token})
            if success:
                logger.info(f"✓ {username}.env updated with Fyers credentials")
                return True
        
        # Fallback to updating system .env if no username or UserEnvManager failed
        def _async_update():
            try:
                env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
                
                if not os.path.exists(env_file):
                    logger.warning(f"Environment file not found: {env_file}")
                    return
                
                with open(env_file, 'r') as f:
                    lines = f.readlines()
                
                updated_lines = []
                found_token = False
                
                for line in lines:
                    if access_token and line.startswith('FYERS_ACCESS_TOKEN='):
                        updated_lines.append(f'FYERS_ACCESS_TOKEN={access_token}\n')
                        found_token = True
                    else:
                        updated_lines.append(line)
                
                if access_token and not found_token:
                    updated_lines.append(f'\nFYERS_ACCESS_TOKEN={access_token}\n')
                
                with open(env_file, 'w') as f:
                    f.writelines(updated_lines)
                
                logger.info(f"✓ .env file updated with Fyers credentials")
                if access_token:
                    logger.info(f"  ACCESS_TOKEN: {access_token[:20]}...")
                    
            except Exception as e:
                logger.error(f"❌ Error updating .env file: {e}", exc_info=True)
        
        # Run update in background thread to avoid blocking HTTP response
        update_thread = threading.Thread(target=_async_update, daemon=True)
        update_thread.start()
        
        logger.info("[_update_fyers_env_credentials] Queued .env update in background")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error queuing .env update: {e}", exc_info=True)
        return False


@auth_bp.route('/kotak/callback')
def kotak_callback():
    """Handle Kotak Neo callback (kept for compatibility but may not be used)."""
    # This route is kept for compatibility but Kotak Neo uses direct authentication
    logger.info("Kotak callback route called (using direct auth instead)")
    return redirect(url_for('pages.index'))


@auth_bp.route('/logout')
def logout():
    """Logout user."""
    session.clear()
    # Also clear the persistent token cache
    clear_access_token()
    logger.info("User logged out, token cache cleared")
    return redirect(url_for('pages.index'))


@auth_bp.route('/status')
def status():
    """Check authentication status."""
    access_token = session.get('access_token') or os.getenv('ACCESS_TOKEN')
    kotak_token = session.get('kotak_access_token') or os.getenv('KOTAK_ACCESS_TOKEN')
    dhan_token = session.get('dhan_access_token') or os.getenv('DHAN_ACCESS_TOKEN')
    fyers_token = session.get('fyers_access_token') or os.getenv('FYERS_ACCESS_TOKEN')
    
    is_authenticated = bool(access_token)
    kotak_authenticated = bool(kotak_token and kotak_token != 'your_kotak_access_token')
    dhan_authenticated = bool(dhan_token)
    fyers_authenticated = bool(fyers_token)
    
    return jsonify({
        'authenticated': is_authenticated,
        'has_access_token': is_authenticated,
        'has_request_token': bool(session.get('request_token')),
        'kotak_authenticated': kotak_authenticated,
        'kotak_has_access_token': kotak_authenticated,
        'dhan_authenticated': dhan_authenticated,
        'dhan_has_access_token': dhan_authenticated,
        'fyers_authenticated': fyers_authenticated,
        'fyers_has_access_token': fyers_authenticated
    })
