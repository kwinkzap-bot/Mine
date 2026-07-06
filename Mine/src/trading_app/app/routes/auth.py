"""Authentication routes."""
from flask import Blueprint, redirect, request, session, url_for, jsonify, render_template, render_template_string
from kiteconnect import KiteConnect
import os
from typing import Optional
from trading_app.app.utils.logger import logger
from trading_app.app.config import current_config
from trading_app.app.utils.token_manager import save_access_token, clear_access_token
from trading_app.app.utils.user_auth import (
    verify_user, is_user_authenticated, login_user, logout_user
)
from trading_app.service.kite_order_services import apply_kite_proxy, generate_session_with_proxy_fallback

# Server-side store for pending Zerodha OAuth logins, keyed by username.
# Using a server-side dict instead of Flask's cookie-based session, because
# the session cookie set during /login may not survive the cross-site redirect
# to kite.zerodha.com and back — leading to a stale or wrong instance being
# picked up in /callback, causing "Token is invalid or has expired".
_pending_zerodha_logins: dict = {}

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


@auth_bp.route('/kite-logout-login')
def kite_logout_login():
    """Intermediate page that logs out from Kite first, then redirects to login.
    
    This enables multiple Zerodha accounts to login in the same browser
    by clearing the previous Kite session before starting a new login.
    
    Usage: /auth/kite-logout-login?broker_id=zerodha_5
    """
    broker_id = request.args.get('broker_id', 'zerodha_1')
    
    # Build the login URL that will be called after logout
    login_url = url_for('auth.login', broker_id=broker_id, _external=True)
    
    # Kite web logout URL (just clears cookies, no redirect needed)
    kite_logout_url = 'https://kite.zerodha.com/api/logout'
    
    return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Switching Kite Account...</title>
            <style>
                body { 
                    font-family: Arial, sans-serif; 
                    text-align: center; 
                    padding: 50px; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    min-height: 100vh;
                    margin: 0;
                }
                .container {
                    background: rgba(255,255,255,0.95);
                    color: #333;
                    padding: 40px;
                    border-radius: 12px;
                    max-width: 400px;
                    margin: 0 auto;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.3);
                }
                .spinner {
                    border: 4px solid #f3f3f3;
                    border-top: 4px solid #667eea;
                    border-radius: 50%;
                    width: 50px;
                    height: 50px;
                    animation: spin 1s linear infinite;
                    margin: 20px auto;
                }
                @keyframes spin {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }
                .broker-name { color: #FF8C00; font-weight: bold; }
                #status { margin-top: 15px; font-size: 14px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>🔄 Switching Kite Account</h2>
                <div class="spinner"></div>
                <p id="status">Clearing previous Kite session...</p>
                <p>Logging in with <span class="broker-name">{{ broker_id }}</span></p>
            </div>
            <script>
                // Try to logout from Kite first using fetch (will likely fail due to CORS, but that's OK)
                // The main purpose is to show a transition and let user know what's happening
                
                const loginUrl = '{{ login_url }}';
                
                // Method 1: Try fetch to logout endpoint (may fail due to CORS)
                fetch('https://kite.zerodha.com/api/logout', {
                    method: 'DELETE',
                    credentials: 'include',
                    mode: 'no-cors'
                }).catch(() => {
                    // Expected to fail due to CORS, continue anyway
                });
                
                // After a brief delay, redirect to login
                // The user will need to manually logout from Kite if already logged in
                setTimeout(() => {
                    document.getElementById('status').textContent = 'Redirecting to Kite login...';
                    setTimeout(() => {
                        window.location.href = loginUrl;
                    }, 500);
                }, 1500);
            </script>
        </body>
        </html>
    ''', broker_id=broker_id, login_url=login_url)


@auth_bp.route('/login')
def login():
    """Redirect to Zerodha Kite OAuth login.
    
    Supports multiple Zerodha instances via broker_id parameter.
    Uses user-specific credentials from BROKER_{N}_{FIELD} format.
    Usage: /auth/login or /auth/login?broker_id=zerodha_1
    
    Session keys are stored with instance-specific prefixes to support
    multiple simultaneous logins in the same browser.
    """
    from trading_app.app.utils.user_env import UserEnvManager
    
    logger.info("Login request received")
    
    # Get current username from session
    username = session.get('username')
    if not username:
        logger.error("No user authenticated for login")
        return jsonify({'error': 'User not authenticated'}), 401
    
    # Get broker_id from query parameter (defaults to zerodha_1)
    broker_id = request.args.get('broker_id', 'zerodha_1').lower()
    
    # Extract instance number from broker_id (e.g., 'zerodha_1' -> instance 1)
    instance_num = 1
    if '_' in broker_id:
        try:
            instance_num = int(broker_id.split('_')[-1])
        except (ValueError, IndexError):
            instance_num = 1
    
    # Get API credentials using the new BROKER_{N}_{FIELD} format
    api_key = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_API_KEY')
    api_secret = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_API_SECRET')
    
    # Verify this is a zerodha broker
    broker_type = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_TYPE', '').strip().lower()
    if broker_type != 'zerodha':
        # Try to find zerodha broker by scanning
        found_instance = None
        for i in range(1, 21):
            t = UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower()
            if t == 'zerodha':
                found_instance = i
                break
        
        if found_instance:
            instance_num = found_instance
            api_key = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_API_KEY')
            api_secret = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_API_SECRET')
        else:
            logger.error(f"No Zerodha broker configured for {username}")
            return jsonify({'error': 'No Zerodha broker configured'}), 500
    
    if not api_key:
        logger.error(f"BROKER_{instance_num}_API_KEY not configured for {username}")
        return jsonify({'error': f'API_KEY not configured for broker instance {instance_num}'}), 500
    
    try:
        # Initialize KiteConnect
        kite = KiteConnect(api_key=api_key)
        apply_kite_proxy(kite)

        # Get login URL for OAuth
        login_url = kite.login_url()
        logger.info(f"Redirecting to Zerodha login for {username} - instance {instance_num}: {login_url}")
        
        # Store credentials in session with instance-specific keys
        # This allows multiple Zerodha logins to coexist in the same browser
        session[f'zerodha_{instance_num}_api_key'] = api_key
        session[f'zerodha_{instance_num}_api_secret'] = api_secret
        session[f'zerodha_{instance_num}_broker_id'] = broker_id
        
        # Store pending login server-side (by username) so the callback can find
        # the right instance regardless of browser session cookie state.
        _pending_zerodha_logins[username] = {
            'instance_num': instance_num,
            'api_key': api_key,
            'api_secret': api_secret,
            'broker_id': broker_id,
        }
        logger.info(f"Stored pending login for {username}: instance {instance_num}")

        # Also keep session entries for fallback / backward-compat
        session[f'zerodha_{instance_num}_api_key'] = api_key
        session[f'zerodha_{instance_num}_api_secret'] = api_secret
        session[f'zerodha_{instance_num}_broker_id'] = broker_id
        session[f'pending_zerodha_{api_key}'] = instance_num
        session['zerodha_latest_pending_instance'] = instance_num
        session.permanent = True

        return redirect(login_url)
    except Exception as e:
        logger.error(f"Error during login for {username} - instance {instance_num}: {e}")
        return jsonify({'error': f'Login failed: {str(e)}'}), 500


@auth_bp.route('/callback')
def callback():
    """Handle Zerodha OAuth callback for any broker instance.
    
    Handles callbacks from multiple Kite instances (kite_1, kite_2, etc.)
    Uses api_key from callback URL to identify which broker instance is returning.
    Saves tokens to user-specific .env file
    """
    from trading_app.app.utils.user_env import UserEnvManager
    
    request_token = request.args.get('request_token')
    # Zerodha callback includes api_key in the status parameter or we can extract from session
    callback_status = request.args.get('status', '')
    
    if not request_token:
        logger.warning("No request_token received in callback")
        return redirect(url_for('pages.index'))
    
    logger.info(f"Callback received with request_token: {request_token[:20]}...")
    
    try:
        # Get current username from session
        username = session.get('username')
        if not username:
            logger.error("No user authenticated in callback")
            return jsonify({'error': 'User not authenticated'}), 401
        
        instance_num = None
        api_key = None
        api_secret = None
        broker_id = None
        access_token = None  # Initialize here

        # PRIMARY: Read pending login from the server-side store (immune to cookie issues)
        pending = _pending_zerodha_logins.pop(username, None)
        if pending:
            instance_num = pending['instance_num']
            api_key = pending['api_key']
            api_secret = pending['api_secret']
            broker_id = pending['broker_id']

            logger.info(f"[server-store] Identified callback for {username}: instance {instance_num} (api_key {api_key[:10]}...)")

            # Single generate_session call — request_token is single-use.
            # A proxy CONNECT failure does NOT consume the token (Kite is never
            # reached), so the direct-fallback retry inside the helper is safe.
            kite_obj = KiteConnect(api_key=api_key)
            session_data = generate_session_with_proxy_fallback(kite_obj, request_token, api_secret)
            access_token = session_data['access_token']  # type: ignore[index]

            # Clean up stale session entries too
            session.pop('zerodha_latest_pending_instance', None)
            for key in list(session.keys()):
                if key.startswith('pending_zerodha_'):
                    session.pop(key, None)
        else:
            logger.warning(f"No server-side pending login found for {username}, trying session fallback")
            # FALLBACK: try session-based approach
            latest_instance = session.pop('zerodha_latest_pending_instance', None)
            if latest_instance is not None:
                stored_api_key = session.get(f'zerodha_{latest_instance}_api_key')
                stored_api_secret = session.get(f'zerodha_{latest_instance}_api_secret')

                if stored_api_key and stored_api_secret:
                    instance_num = latest_instance
                    api_key = stored_api_key
                    api_secret = stored_api_secret
                    broker_id = session.get(f'zerodha_{latest_instance}_broker_id', f'zerodha_{latest_instance}')

                    logger.info(f"[session-fallback] Identified callback for instance {instance_num} (api_key {api_key[:10]}...)")

                    kite_obj = KiteConnect(api_key=api_key)
                    session_data = generate_session_with_proxy_fallback(kite_obj, request_token, api_secret)
                    access_token = session_data['access_token']  # type: ignore[index]
        
        if not instance_num or not api_key or not access_token:
            # Fallback: try the old method (single login scenario)
            broker_id = session.get('broker_id', 'zerodha_1')
            instance_num = session.get('instance_num', 1)
            api_key = session.get('api_key') or session.get(f'zerodha_{instance_num}_api_key')
            api_secret = session.get('api_secret') or session.get(f'zerodha_{instance_num}_api_secret')
            
            if not api_key or not api_secret:
                logger.error(f"API credentials not found in session for {username}")
                return jsonify({'error': 'API credentials not configured. Please try logging in again.'}), 500
            
            # Generate session
            kite = KiteConnect(api_key=api_key)
            data = generate_session_with_proxy_fallback(kite, request_token, api_secret)
            access_token = data['access_token']  # type: ignore[index]
        
        # Validate access_token before proceeding
        if not access_token:
            logger.error("Failed to obtain access token")
            return jsonify({'error': 'Failed to obtain access token'}), 500
        
        # Ensure broker_id is a string
        if not broker_id:
            broker_id = f'zerodha_{instance_num}'
        
        # Store in session with broker_id context - INSTANCE-SPECIFIC to support multiple Zerodha logins
        if instance_num == 1:
            session['access_token'] = access_token  # Keep for backward compatibility (last logged-in)
            session['broker_id'] = broker_id
            session['instance_num'] = instance_num
            
            # Store in environment for immediate use (with broker context)
            os.environ['ACCESS_TOKEN'] = access_token
            os.environ['ACTIVE_BROKER_ID'] = broker_id
            os.environ['ACTIVE_INSTANCE'] = str(instance_num)
            os.environ['ACTIVE_USER'] = username
            
            # Store in persistent cache to survive socket pool flushes
            save_access_token(access_token)
            logger.info(f"Token saved to persistent cache for {username} - {broker_id}")

        session[f'zerodha_{instance_num}_authenticated'] = True  # Mark this specific instance as authenticated
        session[f'zerodha_{instance_num}_access_token'] = access_token  # Store per-instance token
        session[f'zerodha_{instance_num}_api_key'] = api_key  # Keep api_key for this instance
        session.permanent = True
        
        logger.info(f"Session generated successfully for {username} - {broker_id}, access_token stored")
        logger.info(f"User authenticated with access_token: {access_token[:20]}...")
        
        os.environ[f'ZERODHA_{instance_num}_ACCESS_TOKEN'] = access_token  # Per-instance env var
        
        # Update user-specific .env file with new token (instance-specific if needed)
        logger.info(f"Calling _update_user_env_tokens with username={username}, broker_id={broker_id}, instance_num={instance_num}")
        update_result = _update_user_env_tokens(username, access_token, broker_id, instance_num)
        logger.info(f"_update_user_env_tokens returned: {update_result}")
        if instance_num == 1:
            os.environ['ACCESS_TOKEN'] = access_token
        
        # Check if this is a popup window login (has opener)
        # Return a page that closes the popup and refreshes the parent
        return render_template_string('''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Login Successful</title>
                <style>
                    body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f5f5f5; }
                    .success { color: #155724; background: #d4edda; padding: 20px; border-radius: 8px; display: inline-block; }
                </style>
            </head>
            <body>
                <div class="success">
                    <h2>✓ Login Successful!</h2>
                    <p>You are now authenticated. This window will close automatically...</p>
                </div>
                <script>
                    // If opened as popup, close it and refresh parent
                    if (window.opener) {
                        window.opener.postMessage({ type: 'LOGIN_SUCCESS', broker: '{{ broker_id }}' }, '*');
                        setTimeout(() => window.close(), 1500);
                    } else {
                        // Not a popup, redirect normally
                        setTimeout(() => window.location.href = '/', 1500);
                    }
                </script>
            </body>
            </html>
        ''', broker_id=broker_id)
    
    except Exception as e:
        logger.error(f"Error during callback: {e}", exc_info=True)
        return jsonify({'error': f'Authentication failed: {str(e)}'}), 500


def _update_user_env_tokens(username: str, access_token: str, broker_id: str = 'zerodha_1', instance_num: int = 1) -> bool:
    """Update tokens in user-specific .env file using BROKER_{N}_{FIELD} format.
    
    Saves access token to the user's .env file (e.g., Mine.env).
    Uses the new BROKER_{N}_{FIELD} format for token storage.
    
    Note: Request tokens are not saved as they are single-use only.
    
    Args:
        username: Username whose .env file to update
        access_token: New access token from broker
        broker_id: Broker identifier (e.g., 'zerodha_1', 'zerodha_2')
        instance_num: Instance number (1 for primary, 2+ for additional)
        
    Returns:
        True if successful, False otherwise
    """
    from trading_app.app.utils.user_env import UserEnvManager
    
    try:
        # Use BROKER_{N}_{FIELD} format for tokens
        access_token_var = f'BROKER_{instance_num}_ACCESS_TOKEN'
        
        # Save access token to user-specific .env
        tokens_dict = {
            access_token_var: access_token
        }
        
        success = UserEnvManager.save_user_vars(username, tokens_dict)
        
        if success:
            logger.info(f"✓ {username}.env updated with new token for {broker_id} (instance {instance_num})")
            logger.info(f"  {access_token_var}: {access_token[:20]}...")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Error updating tokens in {username}.env: {e}", exc_info=True)
        return False


def _update_env_tokens(access_token: str, broker_id: str = 'zerodha_1', instance_num: int = 1, username: Optional[str] = None) -> bool:
    """Update ACCESS_TOKEN in .env file using BROKER_{N}_{FIELD} format.
    
    Wrapper function - always uses user-specific .env files with new format.
    
    Args:
        access_token: New access token from broker
        broker_id: Broker identifier (e.g., 'zerodha_1', 'zerodha_2')
        instance_num: Instance number (1 for primary, 2+ for additional)
        username: Username (required for user-specific .env)
        
    Returns:
        True if successful, False otherwise
    """
    if username:
        return _update_user_env_tokens(username, access_token, broker_id, instance_num)
    
    logger.warning("No username provided for token update - skipping .env update")
    return False


@auth_bp.route('/login/kotak', methods=['GET', 'POST'])
def login_kotak():
    """Authenticate with Kotak Neo using credentials and TOTP secret.
    
    Uses BROKER_{N}_{FIELD} format to lookup credentials.
    """
    
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
        
        # Find the Kotak broker instance number
        kotak_instance = None
        if username:
            for i in range(1, 21):
                broker_type = UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower()
                if broker_type == 'kotak':
                    kotak_instance = i
                    break
        
        # Helper to get variable from request, then user env (with auto-translation)
        def get_var(key, legacy_env_key):
            val = data.get(key, '').strip()
            if val: return val
            if username:
                # UserEnvManager automatically translates legacy names like KOTAK_CONSUMER_KEY
                val = UserEnvManager.get_user_var(username, legacy_env_key)
                if val: return val
            return ''
        
        # Extract credentials
        totp_code = data.get('totp_secret', '').strip()
        consumer_key = get_var('consumer_key', 'KOTAK_CONSUMER_KEY')
        mobile = get_var('mobile', 'KOTAK_MOBILE_NUMBER')
        ucc = get_var('ucc', 'KOTAK_UCC')
        mpin = get_var('mpin', 'KOTAK_MPIN')
        
        # Validate required fields
        missing_fields = []
        help_messages = {}
        
        if not totp_code:
            missing_fields.append('totp_code')
            help_messages['totp_code'] = 'Enter the current 6-digit code from your authenticator app (Google/Microsoft Authenticator)'
        
        if not consumer_key:
            missing_fields.append('consumer_key')
            help_messages['consumer_key'] = 'Go to Kotak Neo App/Web → Invest → Trade API → Your Application to get your Consumer Key.'
        
        if not mobile:
            missing_fields.append('mobile')
            help_messages['mobile'] = 'Your registered mobile number (10 digits, without +91)'
        
        if not ucc:
            missing_fields.append('ucc')
            help_messages['ucc'] = 'Your Unique Client Code (find in Kotak Neo App → Profile)'
        
        if not mpin:
            missing_fields.append('mpin')
            help_messages['mpin'] = 'Your Kotak Neo web password'
        
        if missing_fields:
            logger.warning(f"Kotak Neo credentials missing: {', '.join(missing_fields)}")
            return jsonify({
                'error': 'Missing required credentials',
                'missing_fields': missing_fields,
                'help': help_messages,
                'message': f'Please provide: {", ".join(missing_fields)}',
                'success': False,
                'documentation': 'See https://github.com/Kotak-Neo/Kotak-neo-api-v2 for setup instructions'
            }), 400
        
        # Import the service
        from trading_app.service.kotak_order_services import KotakOrderService
        
        # Create service instance with consumer_key only (v2 API — no consumer_secret needed)
        kotak_service = KotakOrderService(
            consumer_key=consumer_key,
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
            session['kotak_instance_num'] = kotak_instance
            session.permanent = True
            
            logger.info("✅ Kotak Neo authentication successful")
            
            # Update .env file with tokens using new format
            _update_kotak_env_credentials(
                trading_token=trading_token,
                trading_sid=trading_sid,
                base_url=base_url,
                username=username,
                instance_num=kotak_instance
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
                                  base_url: Optional[str] = None,
                                  username: Optional[str] = None,
                                  instance_num: Optional[int] = None) -> bool:
    """Update Kotak Neo trading tokens in user's .env file using BROKER_{N}_{FIELD} format.
    
    Args:
        trading_token: Trading token from authentication
        trading_sid: Trading session ID
        base_url: Base URL for API calls
        username: Username for user-specific .env file
        instance_num: Broker instance number
        
    Returns:
        True if successful, False otherwise
    """
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        
        if not username:
            logger.warning("No username provided for Kotak credential update")
            return False
        
        # Find Kotak instance if not provided
        if not instance_num:
            for i in range(1, 21):
                broker_type = UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower()
                if broker_type == 'kotak':
                    instance_num = i
                    break
        
        if not instance_num:
            logger.warning(f"No Kotak broker configured for {username}")
            return False
        
        # Build updates using BROKER_{N}_{FIELD} format
        updates = {}
        
        if trading_token:
            updates[f'BROKER_{instance_num}_TRADING_TOKEN'] = trading_token
        if trading_sid:
            updates[f'BROKER_{instance_num}_TRADING_SID'] = trading_sid
        if base_url:
            updates[f'BROKER_{instance_num}_BASE_URL'] = base_url
        
        if not updates:
            return True  # Nothing to update
        
        success = UserEnvManager.save_user_vars(username, updates)
        
        if success:
            logger.info(f"✓ {username}.env updated with Kotak Neo trading tokens (instance {instance_num})")
            if trading_token:
                logger.info(f"  BROKER_{instance_num}_TRADING_TOKEN: {trading_token[:20]}...")
            if trading_sid:
                logger.info(f"  BROKER_{instance_num}_TRADING_SID: {trading_sid}")
            if base_url:
                logger.info(f"  BROKER_{instance_num}_BASE_URL: {base_url}")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Error updating .env file with Kotak credentials: {e}", exc_info=True)
        return False


@auth_bp.route('/login/dhan', methods=['GET', 'POST'])
def login_dhan():
    """Authenticate with Dhan using access token.
    
    Uses BROKER_{N}_{FIELD} format to lookup credentials.
    """
    
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
        
        # Find the Dhan broker instance number
        dhan_instance = None
        if username:
            for i in range(1, 21):
                broker_type = UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower()
                if broker_type == 'dhan':
                    dhan_instance = i
                    break
        
        # Helper to get variable from request, then user env (with auto-translation)
        def get_var(key, legacy_env_key):
            val = data.get(key, '').strip()
            if val: return val
            if username:
                # UserEnvManager automatically translates legacy names
                val = UserEnvManager.get_user_var(username, legacy_env_key)
                if val: return val
            return ''
        
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
            session['dhan_instance_num'] = dhan_instance
            session.permanent = True
            
            logger.info(f"✅ Dhan authentication successful for Client ID: {dhan_service.client_id}")
            
            # Update .env file with new format
            _update_dhan_env_credentials(
                access_token=dhan_service.access_token,
                client_id=dhan_service.client_id,
                username=username,
                instance_num=dhan_instance
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
                                 client_id: Optional[str] = None,
                                 username: Optional[str] = None,
                                 instance_num: Optional[int] = None) -> bool:
    """Update Dhan credentials in user's .env file using BROKER_{N}_{FIELD} format.
    
    Args:
        access_token: Dhan access token
        client_id: Dhan client ID
        username: Username for user-specific .env file
        instance_num: Broker instance number
        
    Returns:
        True if successful, False otherwise
    """
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        
        if not username:
            logger.warning("No username provided for Dhan credential update")
            return False
        
        # Find Dhan instance if not provided
        if not instance_num:
            for i in range(1, 21):
                broker_type = UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower()
                if broker_type == 'dhan':
                    instance_num = i
                    break
        
        if not instance_num:
            logger.warning(f"No Dhan broker configured for {username}")
            return False
        
        # Build updates using BROKER_{N}_{FIELD} format
        updates = {}
        
        if access_token:
            updates[f'BROKER_{instance_num}_ACCESS_TOKEN'] = access_token
        if client_id:
            updates[f'BROKER_{instance_num}_CLIENT_ID'] = client_id
        
        if not updates:
            return True  # Nothing to update
        
        success = UserEnvManager.save_user_vars(username, updates)
        
        if success:
            logger.info(f"✓ {username}.env updated with Dhan credentials (instance {instance_num})")
            if access_token:
                logger.info(f"  BROKER_{instance_num}_ACCESS_TOKEN: {access_token[:20]}...")
            if client_id:
                logger.info(f"  BROKER_{instance_num}_CLIENT_ID: {client_id}")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Error updating .env file with Dhan credentials: {e}", exc_info=True)
        return False


@auth_bp.route('/login/fyers')
def fyers_login():
    """
    Redirect to Fyers OAuth login (similar to Kite login).
    Initiates OAuth 2.0 flow with automatic token management.
    Uses new BROKER_{N}_{FIELD} format with auto-translation from legacy names.
    """
    logger.info("[Fyers Login] Login request received")
    
    # Get current username
    username = session.get('username')
    from trading_app.app.utils.user_env import UserEnvManager
    
    # Find the fyers broker instance
    fyers_instance = None
    if username:
        for i in range(1, 21):
            broker_type = UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower()
            if broker_type == 'fyers':
                fyers_instance = i
                break
    
    if not fyers_instance:
        logger.warning(f"[Fyers Login] No fyers broker instance found for user {username}")
    
    # Helper to get variable - uses auto-translation from legacy names
    def get_var(env_key):
        if username:
            val = UserEnvManager.get_user_var(username, env_key)
            if val: return val
        return os.getenv(env_key, '')
    
    # Get credentials - UserEnvManager auto-translates FYERS_APP_ID -> BROKER_{N}_APP_ID
    app_id = get_var('FYERS_APP_ID')
    secret_key = get_var('FYERS_SECRET_KEY')
    
    if not app_id or not secret_key:
        error_msg = "Fyers credentials not configured. "
        if not app_id:
            error_msg += "Missing FYERS_APP_ID (or BROKER_{N}_APP_ID). "
        if not secret_key:
            error_msg += "Missing FYERS_SECRET_KEY (or BROKER_{N}_SECRET_KEY). "
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
        
        # Store app_id and instance in session for use in callback
        session['fyers_app_id'] = app_id
        session['fyers_instance_num'] = fyers_instance
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
        logger.info(f"[Fyers Callback] Auth code: {auth_code[:30]}...")
        
        # Get username from session
        username = session.get('username')
        fyers_instance = session.get('fyers_instance_num')
        
        logger.info(f"[Fyers Callback] Username: {username}, Instance: {fyers_instance}")
        
        # Import UserEnvManager
        from trading_app.app.utils.user_env import UserEnvManager
        
        # If no username in session, try to get credentials from os.environ directly
        if not username:
            logger.warning("[Fyers Callback] No username in session, trying os.environ")
            app_id = os.getenv('FYERS_APP_ID')
            secret_key = os.getenv('FYERS_SECRET_KEY')
        else:
            # Ensure user env is loaded (sets legacy variable names in os.environ)
            UserEnvManager.load_user_env(username)
            
            # Get credentials using UserEnvManager (auto-translation from legacy names)
            app_id = UserEnvManager.get_user_var(username, 'FYERS_APP_ID')
            secret_key = UserEnvManager.get_user_var(username, 'FYERS_SECRET_KEY')
        
        logger.info(f"[Fyers Callback] Credentials - app_id: {app_id}, secret_key: {secret_key[:5] if secret_key else 'None'}...")
        
        if not app_id or not secret_key:
            # Try fallback: read directly from env file for user 'Mine'
            fallback_username = 'Mine'
            logger.warning(f"[Fyers Callback] Trying fallback with username: {fallback_username}")
            UserEnvManager.load_user_env(fallback_username)
            app_id = UserEnvManager.get_user_var(fallback_username, 'FYERS_APP_ID')
            secret_key = UserEnvManager.get_user_var(fallback_username, 'FYERS_SECRET_KEY')
            logger.info(f"[Fyers Callback] Fallback credentials - app_id: {app_id}, secret_key: {secret_key[:5] if secret_key else 'None'}...")
            
            if not app_id or not secret_key:
                error_msg = f"Missing credentials - app_id: {bool(app_id)}, secret_key: {bool(secret_key)}, username: {username}"
                logger.error(f"[Fyers Callback] {error_msg}")
                return jsonify({'error': error_msg, 'success': False}), 500
        
        # Initialize service with explicit credentials
        fyers_service = FyersOrderService(app_id=app_id, secret_key=secret_key)
        
        logger.info(f"[Fyers Callback] FyersOrderService initialized, calling generate_access_token...")
        
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
                
                # Update user's env file using new BROKER_{N}_{FIELD} format
                _update_fyers_env_credentials(
                    access_token=access_token, 
                    username=username,
                    instance_num=fyers_instance
                )
                
                logger.info(f"[Fyers Callback] ✅ Authentication successful")
                logger.info(f"[Fyers Callback] Access token: {access_token[:30]}...")
            
            # Redirect to home page (like Kite callback)
            return redirect(url_for('pages.index'))
        else:
            error_msg = fyers_service.last_error or 'Failed to generate access token'
            logger.error(f"[Fyers Callback] Token generation failed: {error_msg}")
            
            # Return JSON error for debugging
            return jsonify({
                'error': 'Token generation failed',
                'details': error_msg,
                'success': False
            }), 401
            
    except Exception as e:
        logger.error(f"[Fyers Callback] Exception: {e}", exc_info=True)
        import traceback
        return jsonify({
            'error': 'OAuth callback processing failed',
            'details': str(e),
            'traceback': traceback.format_exc(),
            'success': False
        }), 500


def _update_fyers_env_credentials(access_token: Optional[str] = None, username: Optional[str] = None, instance_num: Optional[int] = None) -> bool:
    """Update Fyers credentials in user's env file using BROKER_{N}_{FIELD} format.
    
    Args:
        access_token: Fyers access token
        username: Username for user-specific env file
        instance_num: Broker instance number (e.g., 4 for BROKER_4_*)
        
    Returns:
        True if update was successful, False if there was an error
    """
    try:
        # Use UserEnvManager if username is available
        if username:
            from trading_app.app.utils.user_env import UserEnvManager
            
            # If we don't have instance_num, find the fyers broker instance
            if not instance_num:
                for i in range(1, 21):
                    broker_type = UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower()
                    if broker_type == 'fyers':
                        instance_num = i
                        break
            
            if not instance_num:
                logger.warning(f"[_update_fyers_env_credentials] No fyers broker instance found for user {username}")
                # Fallback to legacy format
                if access_token:
                    success = UserEnvManager.save_user_vars(username, {'FYERS_ACCESS_TOKEN': access_token})
                    if success:
                        logger.info(f"✓ {username}.env updated with Fyers credentials (legacy format)")
                    return success
                return True  # Nothing to update
            
            # Build updates using new BROKER_{N}_{FIELD} format
            updates = {}
            if access_token:
                updates[f'BROKER_{instance_num}_ACCESS_TOKEN'] = access_token
            
            if not updates:
                return True  # Nothing to update
            
            success = UserEnvManager.save_user_vars(username, updates)
            
            if success:
                logger.info(f"✓ {username}.env updated with Fyers credentials (instance {instance_num})")
                if access_token:
                    logger.info(f"  BROKER_{instance_num}_ACCESS_TOKEN: {access_token[:20]}...")
            
            return success
        
        # Fallback to updating system .env if no username
        logger.warning("[_update_fyers_env_credentials] No username provided, skipping env update")
        return False
        
    except Exception as e:
        logger.error(f"❌ Error updating Fyers credentials: {e}", exc_info=True)
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
    kotak_token = session.get('kotak_access_token') or os.getenv('KOTAK_CONSUMER_KEY')
    dhan_token = session.get('dhan_access_token') or os.getenv('DHAN_ACCESS_TOKEN')
    fyers_token = session.get('fyers_access_token') or os.getenv('FYERS_ACCESS_TOKEN')
    
    is_authenticated = bool(access_token)
    kotak_authenticated = bool(kotak_token and kotak_token != 'your_kotak_access_token')
    dhan_authenticated = bool(dhan_token)
    fyers_authenticated = bool(fyers_token)
    
    return jsonify({
        'authenticated': is_authenticated,
        'has_access_token': is_authenticated,
        'kotak_authenticated': kotak_authenticated,
        'kotak_has_access_token': kotak_authenticated,
        'dhan_authenticated': dhan_authenticated,
        'dhan_has_access_token': dhan_authenticated,
        'fyers_authenticated': fyers_authenticated,
        'fyers_has_access_token': fyers_authenticated
    })
