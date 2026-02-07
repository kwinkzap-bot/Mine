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
        access_token = data['access_token']  # type: ignore[index]
        
        # Store in session
        session['access_token'] = access_token
        session['request_token'] = request_token
        session.permanent = True
        
        logger.info("Session generated successfully, access_token stored")
        logger.info(f"User authenticated with access_token: {access_token[:20]}...")
        
        # Store in environment for immediate use
        os.environ['ACCESS_TOKEN'] = access_token
        os.environ['REQUEST_TOKEN'] = request_token
        
        # Store in persistent cache to survive socket pool flushes
        save_access_token(access_token, request_token)
        logger.info("Token saved to persistent cache")
        
        # Update .env file with new tokens
        _update_env_tokens(access_token, request_token)
        
        return redirect(url_for('pages.index'))
    
    except Exception as e:
        logger.error(f"Error during callback: {e}", exc_info=True)
        return jsonify({'error': f'Authentication failed: {str(e)}'}), 500


def _update_env_tokens(access_token: str, request_token: str) -> bool:
    """Update ACCESS_TOKEN and REQUEST_TOKEN in .env file.
    
    Args:
        access_token: New access token from Zerodha
        request_token: New request token from Zerodha
        
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
        
        # Update or add tokens
        updated_lines = []
        access_token_found = False
        request_token_found = False
        
        for line in lines:
            if line.startswith('ACCESS_TOKEN='):
                updated_lines.append(f'ACCESS_TOKEN={access_token}\n')
                access_token_found = True
            elif line.startswith('REQUEST_TOKEN='):
                updated_lines.append(f'REQUEST_TOKEN={request_token}\n')
                request_token_found = True
            else:
                updated_lines.append(line)
        
        # Add tokens if they don't exist
        if not access_token_found:
            updated_lines.append(f'\nACCESS_TOKEN={access_token}\n')
        
        if not request_token_found:
            updated_lines.append(f'REQUEST_TOKEN={request_token}\n')
        
        # Write updated .env file
        with open(env_file, 'w') as f:
            f.writelines(updated_lines)
        
        logger.info(f"✓ .env file updated with new tokens")
        logger.info(f"  ACCESS_TOKEN: {access_token[:20]}...")
        logger.info(f"  REQUEST_TOKEN: {request_token[:20]}...")
        
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
        
        # Extract credentials - only TOTP comes from request, rest from .env
        totp_code = data.get('totp_secret', '').strip() if data.get('totp_secret') else ''
        access_token = data.get('access_token', '').strip() if data.get('access_token') else os.getenv('KOTAK_ACCESS_TOKEN', '')
        mobile = data.get('mobile', '').strip() if data.get('mobile') else os.getenv('KOTAK_MOBILE_NUMBER', '')
        ucc = data.get('ucc', '').strip() if data.get('ucc') else os.getenv('KOTAK_UCC', '')
        mpin = data.get('mpin', '').strip() if data.get('mpin') else os.getenv('KOTAK_MPIN', '')
        
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
        
        # Extract credentials
        access_token = data.get('access_token', '').strip() if data.get('access_token') else os.getenv('DHAN_ACCESS_TOKEN', '')
        client_id = data.get('client_id', '').strip() if data.get('client_id') else os.getenv('DHAN_CLIENT_ID', '')
        
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


@auth_bp.route('/login/fyers/callback')
def fyers_oauth_callback():
    """
    Handle Fyers OAuth callback.
    Fyers redirects here after user authorizes.
    
    Query parameters:
    - code: Authorization code (use to get access token)
    - state: State parameter (for security)
    """
    auth_code = request.args.get('code', '').strip()
    state = request.args.get('state', '').strip()
    error = request.args.get('error', '').strip()
    error_description = request.args.get('error_description', '').strip()
    
    logger.info(f"[Fyers OAuth Callback] Received - code: {auth_code[:10] if auth_code else 'None'}..., state: {state}, error: {error}")
    
    if error:
        logger.error(f"[Fyers OAuth Callback] Error from Fyers: {error} - {error_description}")
        return jsonify({
            'success': False,
            'error': f'Fyers authorization failed: {error}',
            'error_description': error_description
        }), 400
    
    if not auth_code:
        logger.error("[Fyers OAuth Callback] No auth code received")
        return jsonify({
            'success': False,
            'error': 'No authorization code received from Fyers'
        }), 400
    
    try:
        from trading_app.service.fyers_order_services import FyersOrderService
        
        logger.info("[Fyers OAuth Callback] Exchanging auth code for access token...")
        
        fyers_service = FyersOrderService()
        if fyers_service.generate_access_token(auth_code):
            session['fyers_access_token'] = fyers_service.access_token
            session['fyers_authenticated'] = True
            session.permanent = True
            
            if fyers_service.access_token:
                os.environ['FYERS_ACCESS_TOKEN'] = fyers_service.access_token
            
            _update_fyers_env_credentials(access_token=fyers_service.access_token)
            
            logger.info("[Fyers OAuth Callback] ✅ Successfully authenticated")
            
            # Show success and redirect
            return '''
            <html>
                <head>
                    <title>Fyers Authentication Success</title>
                    <script>
                        window.opener.postMessage({
                            type: 'fyers_auth_success',
                            message: 'Successfully authenticated with Fyers'
                        }, '*');
                        setTimeout(() => window.close(), 1500);
                    </script>
                </head>
                <body style="display: flex; align-items: center; justify-content: center; height: 100vh; font-family: Arial;">
                    <div style="text-align: center;">
                        <h2 style="color: #28a745;">✓ Authentication Successful!</h2>
                        <p>You can close this window. Redirecting...</p>
                    </div>
                </body>
            </html>
            '''
        else:
            error_msg = fyers_service.last_error or 'Failed to generate access token'
            logger.error(f"[Fyers OAuth Callback] Token generation failed: {error_msg}")
            
            return jsonify({
                'success': False,
                'error': error_msg
            }), 401
            
    except Exception as e:
        logger.error(f"[Fyers OAuth Callback] Error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'OAuth callback processing failed: {str(e)}'
        }), 500


@auth_bp.route('/login/fyers', methods=['GET', 'POST'])
def login_fyers():
    """Authenticate with Fyers using OAuth flow or access token."""
    
    if request.method == 'GET':
        # Check if auth_code is in query params (OAuth callback)
        auth_code = request.args.get('auth_code')
        
        if auth_code:
            # Handle OAuth callback
            try:
                from trading_app.service.fyers_order_services import FyersOrderService
                fyers_service = FyersOrderService()
                
                if fyers_service.generate_access_token(auth_code):
                    session['fyers_access_token'] = fyers_service.access_token
                    session['fyers_authenticated'] = True
                    session.permanent = True
                    
                    if fyers_service.access_token:
                        os.environ['FYERS_ACCESS_TOKEN'] = fyers_service.access_token
                    
                    return redirect(url_for('pages.index'))
                else:
                    return jsonify({
                        'error': fyers_service.last_error or 'Failed to generate access token',
                        'success': False
                    }), 401
            except Exception as e:
                logger.error(f"Error in Fyers OAuth callback: {e}", exc_info=True)
                return jsonify({
                    'error': f'OAuth callback failed: {str(e)}',
                    'success': False
                }), 500
        else:
            # Return OAuth URL or manual token entry instructions
            return jsonify({
                'requires_oauth': True,
                'message': 'Fyers uses OAuth authentication',
                'instructions': [
                    '1. Generate authorization URL by sending POST with app_id',
                    '2. Open URL in browser and authorize',
                    '3. Get auth_code from callback',
                    '4. POST auth_code to generate access token'
                ],
                'alternative': 'Or manually enter access_token from Fyers dashboard'
            })
    
    # POST request - process authentication
    logger.info("Fyers login request received")
    
    try:
        data = request.get_json() or {}
        
        # Check if this is OAuth initiation or direct token entry
        access_token = data.get('access_token', '').strip()
        auth_code = data.get('auth_code', '').strip()
        
        from trading_app.service.fyers_order_services import FyersOrderService
        
        if access_token:
            # Direct token authentication
            logger.info("Attempting Fyers authentication with provided token...")
            
            fyers_service = FyersOrderService(access_token=access_token)
            
            if fyers_service.verify_token():
                session['fyers_access_token'] = fyers_service.access_token
                session['fyers_authenticated'] = True
                session.permanent = True
                
                logger.info("✅ Fyers authentication successful")
                
                if fyers_service.access_token:
                    os.environ['FYERS_ACCESS_TOKEN'] = fyers_service.access_token
                
                _update_fyers_env_credentials(access_token=fyers_service.access_token)
                
                return jsonify({
                    'success': True,
                    'message': 'Successfully authenticated with Fyers',
                    'authenticated': True
                })
            else:
                error_msg = fyers_service.last_error or 'Verification failed'
                logger.error(f"Fyers authentication failed: {error_msg}")
                
                return jsonify({
                    'error': f'Authentication failed: {error_msg}',
                    'success': False,
                    'help': 'Verify that your access token is valid'
                }), 401
        
        elif auth_code:
            # OAuth flow - exchange auth_code for access_token
            logger.info("Exchanging auth_code for access token...")
            
            fyers_service = FyersOrderService()
            
            if fyers_service.generate_access_token(auth_code):
                session['fyers_access_token'] = fyers_service.access_token
                session['fyers_authenticated'] = True
                session.permanent = True
                
                logger.info("✅ Fyers access token generated successfully")
                
                if fyers_service.access_token:
                    os.environ['FYERS_ACCESS_TOKEN'] = fyers_service.access_token
                
                _update_fyers_env_credentials(access_token=fyers_service.access_token)
                
                return jsonify({
                    'success': True,
                    'message': 'Successfully authenticated with Fyers',
                    'authenticated': True
                })
            else:
                error_msg = fyers_service.last_error or 'Token generation failed'
                logger.error(f"Fyers token generation failed: {error_msg}")
                
                return jsonify({
                    'error': f'Token generation failed: {error_msg}',
                    'success': False
                }), 401
        
        else:
            # Generate OAuth URL
            logger.info("Generating Fyers OAuth URL...")
            
            fyers_service = FyersOrderService()
            auth_url = fyers_service.generate_auth_code_url()
            
            if auth_url:
                return jsonify({
                    'success': True,
                    'auth_url': auth_url,
                    'message': 'Open this URL in browser to authorize',
                    'instructions': 'After authorization, you will get auth_code. POST it back to /login/fyers'
                })
            else:
                return jsonify({
                    'error': 'Failed to generate OAuth URL. Check APP_ID and SECRET_KEY in .env',
                    'success': False
                }), 400
        
    except Exception as e:
        logger.error(f"Error during Fyers login: {e}", exc_info=True)
        return jsonify({
            'error': f'Fyers login failed: {str(e)}',
            'success': False
        }), 500


def _update_fyers_env_credentials(access_token: Optional[str] = None) -> bool:
    """Update Fyers credentials in .env file.
    
    Args:
        access_token: Fyers access token
        
    Returns:
        True if successful, False otherwise
    """
    try:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
        
        if not os.path.exists(env_file):
            logger.warning(f"Environment file not found: {env_file}")
            return False
        
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
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error updating .env file with Fyers credentials: {e}", exc_info=True)
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
