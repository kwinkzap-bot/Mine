"""API routes for trading data endpoints."""
import logging
import math
import os
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple, Union

import pandas as pd
from collections import OrderedDict
import gc
from flask import Blueprint, request, jsonify, session, Response
from trading_app.app.utils.logger import logger
from trading_app.app.extensions import csrf, limiter
from trading_app.app.utils.user_auth import require_user_auth
from trading_app.app.utils.helpers import is_market_hours, is_trading_day

# Request Coalescing Globals
_pending_request_locks: Dict[Any, threading.Lock] = {}
_pending_locks_manager = threading.Lock()

def _get_request_lock(key: Any) -> threading.Lock:
    with _pending_locks_manager:
        if key not in _pending_request_locks:
            _pending_request_locks[key] = threading.Lock()
        return _pending_request_locks[key]
from trading_app.service.fyers_data_service import FyersDataServiceAdapter
from trading_app.service.fyers_order_services import FyersOrderService
from trading_app.service.kite_order_services import KiteService, apply_kite_proxy


api_bp = Blueprint('api', __name__)

# LRU Cache to prevent memory growth (OOM 247 Fix)
class LruCache:
    def __init__(self, max_size=50):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.lock = threading.Lock()
    def __contains__(self, key):
        with self.lock: return key in self.cache
    def __getitem__(self, key):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            raise KeyError(key)
    def __setitem__(self, key, value):
        with self.lock:
            if key in self.cache: del self.cache[key]
            self.cache[key] = value
            if len(self.cache) > self.max_size: self.cache.popitem(last=False)
    def __len__(self):
        with self.lock: return len(self.cache)
    def clear(self):
        with self.lock: self.cache.clear()
    def pop(self, key, default=None):
        with self.lock: return self.cache.pop(key, default)
    def keys(self):
        with self.lock: return list(self.cache.keys())
# Limits for LRU Caches (OOM 247 Fix)
_MAX_CACHE_ENTRIES = 50

_candle_response_cache = LruCache(max_size=_MAX_CACHE_ENTRIES)
_daily_ohlc_cache = LruCache(max_size=_MAX_CACHE_ENTRIES)
_candle_cache_lock = threading.Lock()
_daily_5m_atm_cache = LruCache(max_size=20)

# Global executor for background data fetching (prevents thread-per-request OOM)
_api_executor = ThreadPoolExecutor(max_workers=10)

# ── Strike-token cache: (symbol, strike, opt_type) → (token, symbol_str, expires_ts) ──
# Eliminates repeated get_option_symbol + get_instrument_token calls within the same expiry day.
_strike_token_cache: Dict[Tuple, Any] = {}
_strike_token_cache_lock = threading.Lock()

def _get_cached_strike_token(kite_service, data_provider, is_fyers: bool, symbol: str, strike: int, opt_type: str):
    """Return (token, symbol_str) from cache; populate on miss. TTL = end of current trading day."""
    key = (symbol, strike, opt_type)
    now_ts = _time.time()
    with _strike_token_cache_lock:
        cached = _strike_token_cache.get(key)
        if cached and cached[2] > now_ts:
            return cached[0], cached[1]
    if is_fyers:
        sym = data_provider.find_option_symbol(symbol, strike, opt_type)
        tok = sym
    else:
        sym = kite_service.get_option_symbol(symbol, strike, opt_type)
        tok = kite_service.get_instrument_token(sym) if sym else None
    today = datetime.now()
    expire_ts = today.replace(hour=15, minute=30, second=0, microsecond=0).timestamp()
    if now_ts > expire_ts:
        expire_ts = now_ts + 18 * 3600
    with _strike_token_cache_lock:
        _strike_token_cache[key] = (tok, sym, expire_ts)
    return tok, sym

# ── Dhan security-ID async cache ─────────────────────────────────────────────
# Populated lazily in background; request path reads from dict (never blocks).
_dhan_secid_cache: Dict[str, str] = {}

def _trigger_dhan_secid_fetch(*symbols: Optional[str]) -> None:
    """Submit background task to populate Dhan security IDs for any cache-missing symbols."""
    missing = [s for s in symbols if s and s not in _dhan_secid_cache]
    if not missing:
        return
    def _do_fetch():
        try:
            from trading_app.service.dhan_order_services import DhanOrderService
            svc = DhanOrderService()
            for sym in missing:
                try:
                    result = svc.search_symbol(sym)
                    _dhan_secid_cache[sym] = result.get('security_id', sym)
                except Exception:
                    pass
        except Exception:
            pass
    _api_executor.submit(_do_fetch)

# ── Market-hours TTL cache (60 s) ─────────────────────────────────────────────
_market_hours_cache: Dict[str, Any] = {'value': None, 'ts': 0.0}
_market_hours_lock = threading.Lock()

def _cached_market_hours() -> bool:
    """Returns is_market_hours() & is_trading_day() with 60-second TTL."""
    now_ts = _time.time()
    with _market_hours_lock:
        if now_ts - _market_hours_cache['ts'] < 60 and _market_hours_cache['value'] is not None:
            return _market_hours_cache['value']
    result = is_market_hours() and is_trading_day()
    with _market_hours_lock:
        _market_hours_cache['value'] = result
        _market_hours_cache['ts'] = _time.time()
    return result

# Fyers index symbol map
FYERS_INDEX_SYMBOLS = {
    'NIFTY':      'NSE:NIFTY50-INDEX',
    'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
    'FINNIFTY':   'NSE:FINNIFTY-INDEX',
    'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
    'SENSEX':     'BSE:SENSEX-INDEX',
    'NIFTY MIDCAP 150': 'NSE:NIFTYMIDCAP150-INDEX',
    'NIFTY AUTO':      'NSE:NIFTYAUTO-INDEX',
    'NIFTY Smallcap 100': 'NSE:NIFTYSMLCAP100-INDEX',
    'NIFTY SMLCAP 100': 'NSE:NIFTYSMLCAP100-INDEX',
    'NIFTY FMCG':      'NSE:NIFTYFMCG-INDEX',
    'NIFTY METAL':     'NSE:NIFTYMETAL-INDEX',
    'NIFTY PHARAMA':   'NSE:NIFTYPHARMA-INDEX',
    'NIFTY PHARMA':    'NSE:NIFTYPHARMA-INDEX',
    'NIFTY PSU BANK':  'NSE:NIFTYPSUBANK-INDEX',
    'NIFTY IT':        'NSE:NIFTYIT-INDEX',
}

# Broker type configurations (icons, descriptions, required fields, login info)
BROKER_TYPE_CONFIGS = {
    'zerodha': {
        'icon': '🪁',
        'description': 'NSE/BSE stocks & F&O trading',
        'required_fields': ['API_KEY', 'API_SECRET'],
        'login_type': 'url',
        'login_url': '/auth/login'
    },
    'kotak': {
        'icon': '🏦',
        'description': 'Stocks, F&O & derivatives trading',
        'required_fields': ['CONSUMER_KEY', 'UCC'],
        'login_type': 'modal',
        'login_action': 'showKotakLoginModal()',
        'auth_endpoint': '/auth/login/kotak'
    },
    'dhan': {
        'icon': '📊',
        'description': 'F&O, options & commodity trading',
        'required_fields': ['ACCESS_TOKEN', 'CLIENT_ID'],
        'login_type': 'modal',
        'login_action': 'showDhanLoginModal()',
        'auth_endpoint': '/auth/login/dhan'
    },
    'fyers': {
        'icon': '⚡',
        'description': 'Options & index trading',
        'required_fields': ['APP_ID', 'SECRET_KEY'],
        'login_type': 'modal',
        'login_action': 'showFyersLoginModal()',
        'auth_endpoint': '/auth/login/fyers'
    }
}

# Type alias for API responses
# Flask's jsonify returns Response, optionally with status code tuple
EndpointResponse = Union[Response, tuple[Response, int]]

# Apply user authentication to all API routes
@api_bp.before_request
def check_user_authentication():
    """Require user authentication for all API routes."""
    from trading_app.app.utils.user_auth import is_user_authenticated
    
    # Allow test endpoints without authentication
    if request.path.endswith('/open-interest-test'):
        return None
    
    if not is_user_authenticated():
        return jsonify({
            'success': False,
            'error': 'User authentication required. Please login first.',
            'auth_required': True
        }), 401



def is_broker_active(username: str, instance_num: int) -> bool:
    """Check if a broker instance is active (BROKER_N_ACTIVE=true in .env).
    
    Defaults to True when the flag is missing so existing setups keep working.
    Returns False only when explicitly set to 'false', '0', or 'no'.
    """
    from trading_app.app.utils.user_env import UserEnvManager
    val = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_ACTIVE', 'true').strip().lower()
    return val not in ('false', '0', 'no')


def get_broker_lot_size(username: str, instance_num: int, standard_lot: int) -> int:
    """Return absolute order quantity for a broker by applying its lot-size multiplier.
    
    BROKER_N_LOT_SIZE in .env expresses how many **lots** (not shares) to trade.
    e.g.  BROKER_1_LOT_SIZE=1  => 1 × 65  = 65  units  (1 NIFTY lot)
          BROKER_2_LOT_SIZE=2  => 2 × 65  = 130 units  (2 NIFTY lots)
    
    Args:
        username: User whose .env to read
        instance_num: Broker instance number (1-20)
        standard_lot: The symbol's standard lot size (e.g. 65 for NIFTY)
    
    Returns:
        Quantity to order (lots × standard_lot). Minimum is standard_lot (1 lot).
    """
    from trading_app.app.utils.user_env import UserEnvManager
    try:
        raw = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_LOT_SIZE', '1').strip()
        lots = max(1, int(raw))
    except (ValueError, TypeError):
        lots = 1
    return lots * standard_lot


from trading_app.service.provider_logic import get_kite, get_data_provider



def check_auth() -> Optional[tuple]:
    """Check if user is authenticated via active data provider (Zerodha/Fyers)."""
    # Verify we can get a valid data provider (handles all token logic internally)
    provider = get_data_provider()
    
    if not provider:
        logger.warning("Authentication check failed: no data provider available")
        return jsonify({
            'success': False,
            'error': 'Authentication required. Please login first at /auth/login',
            'auth_error': True
        }), 401
    
    return None


def handle_kite_error(error: Exception) -> tuple:
    """Handle KiteConnect API errors and return appropriate HTTP response.
    
    Detects 403 "Access Denied" errors which typically mean:
    1. Access token has expired (Zerodha tokens expire daily at 3:20 PM IST)
    2. Invalid or revoked token
    3. Session timed out
    
    Args:
        error: The exception from KiteConnect API call
        
    Returns:
        Tuple of (jsonify response, HTTP status code)
    """
    error_str = str(error).lower()
    
    # Check for 403 Forbidden / Access Denied
    if 'access denied' in error_str or '403' in error_str or 'forbidden' in error_str:
        logger.warning(f"Access denied (403) error - token likely expired: {error}")
        return jsonify({
            'success': False,
            'error': 'Access token has expired. Please login again at /auth/login',
            'auth_error': True,
            'login_required': True,
            'details': 'Zerodha access tokens expire daily at 3:20 PM IST'
        }), 403
    
    # Check for other authentication errors
    if 'access_token' in error_str or 'unauthorized' in error_str or '401' in error_str:
        logger.warning(f"Authentication error: {error}")
        return jsonify({
            'success': False,
            'error': 'Authentication failed. Please login again.',
            'auth_error': True,
            'login_required': True
        }), 401
    
    # Generic error handling
    logger.error(f"KiteConnect API error: {error}")
    return jsonify({
        'success': False,
        'error': str(error),
        'details': 'Check /api/debug/token-status for more information'
    }), 500


def get_instrument_key(symbol: str) -> str:
    """Get the instrument key for a symbol."""
    symbol = symbol.upper()
    mapping = {
        'NIFTY': 'NSE:NIFTY 50'
    }
    return mapping.get(symbol, f'NSE:{symbol}')


@api_bp.route('/health', methods=['GET'])
def health() -> EndpointResponse:
    """Health check endpoint."""
    return jsonify({'status': 'healthy'}), 200


@api_bp.route('/available-brokers', methods=['GET'])
@csrf.exempt
@limiter.exempt
def get_available_brokers() -> EndpointResponse:
    """Get list of available broker instances based on object-based configuration
    
    Reads from user-specific .env file using BROKER_{N}_{FIELD} format.
    Each broker is an object with TYPE, NAME, and broker-specific credentials.
    """
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        
        # Get current username from session
        username = session.get('username')
        if not username:
            return jsonify({
                'success': False,
                'error': 'User not authenticated',
                'brokers': [],
                'total_configured': 0
            }), 401
        
        # Batch fetch all environment variables for the user to avoid repeated disk I/O
        user_vars = UserEnvManager.get_all_user_vars(username)
        
        brokers = []
        broker_configs = []
        
        # Scan for BROKER_{N}_TYPE entries (up to 20 brokers)
        for instance_num in range(1, 21):
            broker_prefix = f'BROKER_{instance_num}_'
            broker_type = user_vars.get(f'{broker_prefix}TYPE', '').strip().lower()
            if not broker_type:
                continue
            
            if broker_type not in BROKER_TYPE_CONFIGS:
                logger.warning(f"Unknown broker type: {broker_type} for BROKER_{instance_num}")
                continue
            
            type_config = BROKER_TYPE_CONFIGS[broker_type]
            
            broker_name = user_vars.get(f'{broker_prefix}NAME', '').strip()
            if not broker_name:
                broker_name = broker_type.title()
            
            # Check for required fields using the pre-fetched user_vars
            all_fields_present = True
            for field in type_config['required_fields']:
                if not user_vars.get(f'{broker_prefix}{field}', '').strip():
                    all_fields_present = False
                    break
            
            if not all_fields_present:
                logger.debug(f"BROKER_{instance_num} ({broker_type}) missing required fields")
                continue
            
            # Check if active using pre-fetched user_vars
            active_val = user_vars.get(f'{broker_prefix}ACTIVE', 'true').strip().lower()
            broker_active = active_val not in ('false', '0', 'no')
            
            config = {
                'instance_num': instance_num,
                'broker_type': broker_type,
                'broker_name': broker_name,
                'type_config': type_config,
                'broker_active': broker_active,
                'username': username,
                'lot_size': int(user_vars.get(f'{broker_prefix}LOT_SIZE', '1') or '1')
            }
            
            # Pre-extract session strings in MAIN THREAD to avoid Flask RequestContext issues
            if broker_type == 'zerodha':
                config['session_token'] = session.get(f'zerodha_{instance_num}_access_token')
                config['env_token'] = user_vars.get(f'{broker_prefix}ACCESS_TOKEN')
                config['env_api_key'] = user_vars.get(f'{broker_prefix}API_KEY')
                config['has_inst'] = bool(session.get('instance_num'))
            elif broker_type == 'kotak':
                config['trading_token'] = session.get(f'kotak_{instance_num}_trading_token') or user_vars.get(f'{broker_prefix}TRADING_TOKEN')
                config['consumer_key'] = user_vars.get(f'{broker_prefix}CONSUMER_KEY')
                config['trading_sid'] = session.get(f'kotak_{instance_num}_trading_sid') or user_vars.get(f'{broker_prefix}TRADING_SID')
                config['base_url'] = session.get(f'kotak_{instance_num}_base_url') or user_vars.get(f'{broker_prefix}BASE_URL') or "https://gw-napi.kotaksecurities.com"
            elif broker_type == 'dhan':
                config['access_token'] = session.get(f'dhan_{instance_num}_access_token') or user_vars.get(f'{broker_prefix}ACCESS_TOKEN')
                config['client_id'] = user_vars.get(f'{broker_prefix}CLIENT_ID')
            elif broker_type == 'fyers':
                config['access_token'] = session.get(f'fyers_{instance_num}_access_token') or user_vars.get(f'{broker_prefix}ACCESS_TOKEN')
                config['app_id'] = user_vars.get(f'{broker_prefix}APP_ID')
                config['secret'] = user_vars.get(f'{broker_prefix}SECRET_KEY')
            broker_configs.append(config)
            
        def verify_broker_status(b_conf):
            # Pure Python Thread worker without Flask proxy side-effects
            instance_num = b_conf['instance_num']
            broker_type = b_conf['broker_type']
            
            is_logged_in = False
            msg_status = 'Not connected'
            s_updates = {}
            s_pops = []

            try:
                if broker_type == 'zerodha':
                    instance_token = b_conf.get('session_token')
                    env_token = b_conf.get('env_token')
                    
                    if not instance_token and env_token:
                        instance_token = env_token
                        env_api_key = b_conf.get('env_api_key')
                        s_updates[f'zerodha_{instance_num}_authenticated'] = True
                        s_updates[f'zerodha_{instance_num}_access_token'] = env_token
                        if env_api_key: s_updates[f'zerodha_{instance_num}_api_key'] = env_api_key
                        if not b_conf.get('has_inst'):
                            s_updates['instance_num'] = instance_num
                            s_updates['access_token'] = env_token
                    
                    if instance_token:
                        try:
                            from kiteconnect import KiteConnect
                            api_key = b_conf.get('env_api_key') or os.getenv('API_KEY') or "dummy"
                            k = KiteConnect(api_key=api_key)
                            apply_kite_proxy(k)
                            k.set_access_token(instance_token)
                            k.profile()
                            is_logged_in = True
                            msg_status = 'Connected'
                        except Exception:
                            msg_status = 'Token expired'
                            s_pops.append(f'zerodha_{instance_num}_authenticated')
                            s_pops.append(f'zerodha_{instance_num}_access_token')
                            
                elif broker_type == 'kotak':
                    trading_token = b_conf.get('trading_token')
                    if trading_token:
                        try:
                            import requests
                            import json
                            consumer_key = b_conf.get('consumer_key')
                            trading_sid = b_conf.get('trading_sid')
                            base_url = b_conf.get('base_url')
                            
                            if consumer_key and trading_token and trading_sid:
                                url = base_url.rstrip('/') + "/quick/user/limits"
                                headers = {
                                    "Authorization": str(consumer_key),
                                    "Sid": str(trading_sid),
                                    "Auth": str(trading_token),
                                    "Content-Type": "application/x-www-form-urlencoded",
                                    "neo-fin-key": "neotradeapi"
                                }
                                jdata_payload = "jData=" + json.dumps({"seg": "CASH", "exch": "NSE"})
                                resp = requests.post(url, headers=headers, data=jdata_payload, timeout=5)
                                
                                if resp.status_code == 401 or 'invalid session token' in resp.text.lower():
                                    s_pops.append(f'kotak_{instance_num}_trading_token')
                                    msg_status = 'Token expired'
                                else:
                                    is_logged_in = True
                                    msg_status = 'Connected'
                        except Exception: 
                            msg_status = 'Connection error'
                            
                elif broker_type == 'dhan':
                    access_token = b_conf.get('access_token')
                    client_id = b_conf.get('client_id')
                    if access_token and client_id:
                        from trading_app.service.dhan_order_services import DhanOrderService
                        dhan = DhanOrderService(access_token=access_token, client_id=client_id)
                        if dhan.verify_credentials():
                            is_logged_in = True
                            msg_status = 'Connected'
                        else:
                            s_pops.append(f'dhan_{instance_num}_access_token')
                            msg_status = 'Token expired'
                            
                elif broker_type == 'fyers':
                    access_token = b_conf.get('access_token')
                    app_id = b_conf.get('app_id')
                    secret = b_conf.get('secret')
                    if access_token and app_id:
                        from trading_app.service.fyers_order_services import FyersOrderService
                        fyers = FyersOrderService(app_id=app_id, access_token=access_token, secret_key=secret)
                        if fyers.verify_token():
                            is_logged_in = True
                            msg_status = 'Connected'
                        else:
                            s_pops.append(f'fyers_{instance_num}_access_token')
                            msg_status = 'Token expired'
            except Exception as e:
                logger.error(f"Error checking broker {broker_type} instance {instance_num}: {e}")
                msg_status = 'Check error'
                
            return b_conf, is_logged_in, msg_status, s_updates, s_pops

        # Run verification checks concurrently
        if broker_configs:
            with ThreadPoolExecutor(max_workers=min(10, len(broker_configs))) as executor:
                results = list(executor.map(verify_broker_status, broker_configs))
        else:
            results = []

        # Process results sequentially to build list and safely apply session mutations
        for b_conf, is_logged_in, msg_status, s_updates, s_pops in results:
            instance_num = b_conf['instance_num']
            broker_type = b_conf['broker_type']
            type_config = b_conf['type_config']
            
            # Apply session changes safely in main thread (thread-safe, persists cookie securely)
            for k, v in s_updates.items(): session[k] = v
            for k in s_pops: session.pop(k, None)
            if s_updates: session.permanent = True
            
            # Build broker entry
            broker_entry = {
                'id': f"{broker_type}_{instance_num}",
                'instance_num': instance_num,
                'broker_type': broker_type,
                'name': b_conf['broker_name'],
                'icon': type_config['icon'],
                'description': type_config['description'],
                'configured': True,
                'active': b_conf['broker_active'],
                'lot_size': b_conf.get('lot_size', 1),
                'status': 'Configured and ready' if b_conf['broker_active'] else 'Inactive — orders disabled',
                'is_logged_in': is_logged_in,
                'msg_status': msg_status,
                'login_type': type_config['login_type']
            }
            
            if type_config['login_type'] == 'url':
                broker_entry['login_url'] = f"{type_config['login_url']}?broker_id={broker_entry['id']}"
            else:
                broker_entry['login_action'] = type_config.get('login_action')
                broker_entry['auth_endpoint'] = type_config.get('auth_endpoint')
            
            brokers.append(broker_entry)
            logger.info(f"[available-brokers] Found broker: {broker_entry['name']} ({broker_type}) - Instance {instance_num}")
        
        logger.info(f"[available-brokers] User: {username}, Total brokers found: {len(brokers)}")
        
        return jsonify({
            'success': True,
            'brokers': brokers,
            'total_configured': len(brokers),
            'message': f'{len(brokers)} broker(s) available for login'
        }), 200
        
    except Exception as e:
        logger.error(f"Error fetching available brokers: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'brokers': []
        }), 500


@api_bp.route('/token-status', methods=['GET'])
def token_status() -> EndpointResponse:
    """Check current token status and validity.
    
    Returns token availability from all sources without exposing the full token.
    """
    import os
    from trading_app.app.utils.token_manager import get_access_token
    
    session_token = session.get('access_token')
    env_token = os.getenv('ACCESS_TOKEN')
    cached_token = get_access_token()
    
    return jsonify({
        'status': 'ok',
        'token_sources': {
            'session': {
                'available': bool(session_token),
                'length': len(session_token) if session_token else 0
            },
            'environment': {
                'available': bool(env_token),
                'length': len(env_token) if env_token else 0
            },
            'cache': {
                'available': bool(cached_token),
                'length': len(cached_token) if cached_token else 0
            }
        },
        'kite_available': get_data_provider() is not None,
        'message': 'Token is available from all sources' if (session_token or env_token or cached_token) else 'No token found - login required'
    }), 200


@api_bp.route('/underlying-price', methods=['GET'])
def get_underlying_price() -> EndpointResponse:
    """Get the underlying price (LTP and Previous Close) of a symbol."""
    symbol = request.args.get('symbol')
    price_source = request.args.get('price_source', 'ltp')
    
    if not symbol:
        return jsonify({'success': False, 'error': 'Symbol is required'}), 400
    
    if price_source not in ['ltp', 'previous_close']:
        price_source = 'ltp'
    
    # Data fetch always uses Broker 1 (data account)
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        instrument_key = get_instrument_key(symbol)
        ltp = None
        previous_close = None
        
        try:
            ltp_data = current_provider.ltp([instrument_key])
            ltp = float(ltp_data.get(instrument_key, {}).get('last_price', 0.0))
        except Exception as e:
            logger.warning(f"Error fetching LTP for {symbol}: {e}")
        
        try:
            quote_data = current_provider.quote([instrument_key])
            previous_close = float(quote_data.get(instrument_key, {}).get('ohlc', {}).get('close', 0.0))
        except Exception as e:
            logger.warning(f"Error fetching previous close for {symbol}: {e}")
        
        requested_price = ltp if price_source == 'ltp' else previous_close
        if not requested_price and ltp:
            requested_price = ltp
        if not requested_price and previous_close:
            requested_price = previous_close
        
        return jsonify({
            'success': True,
            'symbol': symbol,
            'ltp': ltp,
            'previous_close': previous_close,
            'requested_price': requested_price,
            'price_source': price_source
        })
    except Exception as e:
        logger.error(f"Error fetching underlying price for {symbol}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/option-ltp', methods=['GET'])
@csrf.exempt
@limiter.exempt
def get_option_ltp() -> EndpointResponse:
    """Return the live LTP of a specific option strike.

    Query params: symbol (e.g. NIFTY), strike (int), option_type (CE|PE)
    Response: { success, ltp, opt_symbol }
    """
    symbol = request.args.get('symbol', '').upper()
    strike = request.args.get('strike', type=int)
    option_type = request.args.get('option_type', '').upper()

    if not symbol or not strike or option_type not in ('CE', 'PE'):
        return jsonify({'success': False, 'error': 'symbol, strike and option_type are required'}), 400

    try:
        kite = get_kite(instance=1)
        data_provider = get_data_provider()
        if not kite and not data_provider:
            return jsonify({'success': False, 'error': 'Data provider not connected'}), 401

        from trading_app.service.fyers_data_service import FyersDataServiceAdapter
        is_fyers = isinstance(data_provider, FyersDataServiceAdapter)
        effective = data_provider if data_provider else kite
        kite_service = KiteService(kite_instance=effective) if effective else KiteService()

        token, opt_sym = _get_cached_strike_token(kite_service, data_provider, is_fyers, symbol, strike, option_type)
        if not opt_sym:
            return jsonify({'success': False, 'error': f'Option symbol not found for {symbol} {strike} {option_type}'}), 404

        if is_fyers:
            ltp_data = data_provider.ltp([opt_sym])
            ltp = float(ltp_data.get(opt_sym, {}).get('last_price', 0) or 0)
        else:
            instrument_key = f'NFO:{opt_sym}'
            ltp_data = effective.ltp([instrument_key])
            ltp = float(ltp_data.get(instrument_key, {}).get('last_price', 0) or 0)

        return jsonify({'success': True, 'opt_symbol': opt_sym, 'ltp': ltp})
    except Exception as e:
        logger.error(f'[option-ltp] {symbol} {strike} {option_type}: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# Global cache for symbols list
_symbols_cache = {
    'data': [],
    'last_updated': 0
}

_INDEX_CPR_CACHE = {}
_STOCK_CPR_CACHE = {}

def calculate_cpr_from_ohlc(h, l, c):
    pp = (h + l + c) / 3
    bc = (h + l) / 2
    tc = (2 * pp) - bc
    return {
        'pp': round(pp, 2),
        'bc': round(min(bc, tc), 2),
        'tc': round(max(bc, tc), 2)
    }

def get_index_cpr_levels(provider, index_name="NIFTY"):
    global _INDEX_CPR_CACHE
    now = datetime.now()
    index_name = index_name.upper()
    
    # Return from cache if fresh (within 30 mins)
    if index_name in _INDEX_CPR_CACHE:
        cache = _INDEX_CPR_CACHE[index_name]
        if cache['levels'] and cache['timestamp']:
            if (now - cache['timestamp']).total_seconds() < 1800:
                return cache['levels']
                
    try:
        # Determine token for requested index
        provider_name = provider.__class__.__name__.lower()
        is_kite = 'kite' in provider_name
        
        index_map = {
            'NIFTY':            'NSE:NIFTY 50' if is_kite else 'NSE:NIFTY50-INDEX',
            'BANKNIFTY':        'NSE:NIFTY BANK' if is_kite else 'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':         'NSE:NIFTY FIN SERVICE' if is_kite else 'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY':       'NSE:NIFTY MID SELECT' if is_kite else 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':           'BSE:SENSEX' if is_kite else 'BSE:SENSEX-INDEX',
            'INDIAVIX':         'NSE:INDIA VIX' if is_kite else 'NSE:INDIAVIX-INDEX',
            'NIFTY IT':         'NSE:NIFTY IT' if is_kite else 'NSE:NIFTYIT-INDEX',
            'NIFTY AUTO':       'NSE:NIFTY AUTO' if is_kite else 'NSE:NIFTYAUTO-INDEX',
            'NIFTY FMCG':       'NSE:NIFTY FMCG' if is_kite else 'NSE:NIFTYFMCG-INDEX',
            'NIFTY METAL':      'NSE:NIFTY METAL' if is_kite else 'NSE:NIFTYMETAL-INDEX',
            'NIFTY PHARMA':     'NSE:NIFTY PHARMA' if is_kite else 'NSE:NIFTYPHARMA-INDEX',
            'NIFTY PSU BANK':   'NSE:NIFTY PSU BANK' if is_kite else 'NSE:NIFTYPSUBANK-INDEX',
            'NIFTY MIDCAP 150': 'NSE:NIFTY MIDCAP 150' if is_kite else 'NSE:NIFTYMIDCAP150-INDEX',
            'NIFTY SMLCAP 100': 'NSE:NIFTY SMALLCAP 100' if is_kite else 'NSE:NIFTYSMLCAP100-INDEX'
        }
        
        token = index_map.get(index_name, index_name)
        
        # 1. Fetch Hourly Data
        to_dt = now
        from_dt_hr = to_dt - timedelta(days=5)
        hr_candles = provider.historical_data(token, from_dt_hr, to_dt, '60minute')
        
        hourly_cpr = None
        if len(hr_candles) >= 2:
            last_c = hr_candles[-1]
            last_c_dt = last_c['date']
            # Remove timezone if aware for comparison
            if last_c_dt.tzinfo:
                last_c_dt = last_c_dt.replace(tzinfo=None)
            
            if last_c_dt.date() == now.date() and last_c_dt.hour == now.hour:
                target_candle = hr_candles[-2]
            else:
                target_candle = hr_candles[-1]
            
            hourly_cpr = calculate_cpr_from_ohlc(target_candle['high'], target_candle['low'], target_candle['close'])
            
        # 2. Fetch Daily Data
        from_dt_day = to_dt - timedelta(days=500)
        day_candles = provider.historical_data(token, from_dt_day, to_dt, 'day')
        
        daily_cpr = None
        weekly_cpr = None
        monthly_cpr = None
        half_yearly_cpr = None
        yearly_cpr = None
        
        if day_candles:
            df = pd.DataFrame(day_candles)
            df['date'] = pd.to_datetime(df['date'])
            # Ensure timezone naive for comparison
            if df['date'].dt.tz is not None:
                df['date'] = df['date'].dt.tz_localize(None)
            df.set_index('date', inplace=True)
            dti = pd.DatetimeIndex(df.index)

            # Daily CPR
            last_idx = -2 if dti[-1].date() == now.date() else -1
            if len(df) >= abs(last_idx):
                day_row = df.iloc[last_idx]
                daily_cpr = calculate_cpr_from_ohlc(day_row['high'], day_row['low'], day_row['close'])

            # Weekly CPR
            current_week_start = now - timedelta(days=now.weekday())
            prev_week_start = current_week_start - timedelta(days=7)
            prev_week_end = prev_week_start + timedelta(days=4)
            week_df = df[(dti.date >= prev_week_start.date()) & (dti.date <= prev_week_end.date())]
            if not week_df.empty:
                weekly_cpr = calculate_cpr_from_ohlc(week_df['high'].max(), week_df['low'].min(), week_df['close'].iloc[-1])

            # Monthly CPR
            first_current = now.replace(day=1)
            last_prev = first_current - timedelta(days=1)
            first_prev = last_prev.replace(day=1)
            month_df = df[(dti.date >= first_prev.date()) & (dti.date <= last_prev.date())]
            if not month_df.empty:
                monthly_cpr = calculate_cpr_from_ohlc(month_df['high'].max(), month_df['low'].min(), month_df['close'].iloc[-1])

            # 6-Month CPR
            year = now.year
            month = now.month
            if month <= 6:
                half_start = datetime(year - 1, 7, 1)
                half_end = datetime(year - 1, 12, 31)
            else:
                half_start = datetime(year, 1, 1)
                half_end = datetime(year, 6, 30)
            half_df = df[(dti.date >= half_start.date()) & (dti.date <= half_end.date())]
            if not half_df.empty:
                half_yearly_cpr = calculate_cpr_from_ohlc(half_df['high'].max(), half_df['low'].min(), half_df['close'].iloc[-1])

            # Yearly CPR
            year_start = datetime(now.year - 1, 1, 1)
            year_end = datetime(now.year - 1, 12, 31)
            year_df = df[(dti.date >= year_start.date()) & (dti.date <= year_end.date())]
            if not year_df.empty:
                yearly_cpr = calculate_cpr_from_ohlc(year_df['high'].max(), year_df['low'].min(), year_df['close'].iloc[-1])
                
        levels = {
            'Hourly': hourly_cpr,
            'Daily': daily_cpr,
            'Weekly': weekly_cpr,
            'Monthly': monthly_cpr,
            '6-Month': half_yearly_cpr,
            'Yearly': yearly_cpr
        }
        
        # Cache successful calculation
        _INDEX_CPR_CACHE[index_name] = {
            'levels': levels,
            'timestamp': now
        }
        return levels
        
    except Exception as e:
        logger.warning(f"Error calculating {index_name} CPR timeframes: {e}")
        # Return stale cache if available
        if index_name in _INDEX_CPR_CACHE and _INDEX_CPR_CACHE[index_name]['levels']:
            return _INDEX_CPR_CACHE[index_name]['levels']
        return {}

def get_nifty_cpr_levels(provider):
    """Wrapper for backward compatibility."""
    return get_index_cpr_levels(provider, "NIFTY")

@api_bp.route('/stock-cpr', methods=['GET'])
@limiter.exempt
def get_stock_cpr_endpoint():
    symbol = request.args.get('symbol', '').upper()
    if not symbol:
        return jsonify({'success': False, 'error': 'Symbol is required'}), 400
        
    global _STOCK_CPR_CACHE
    now = datetime.now()
    cache_key = symbol
    if cache_key in _STOCK_CPR_CACHE:
        cache = _STOCK_CPR_CACHE[cache_key]
        if (now - cache['timestamp']).total_seconds() < 300:  # 5 mins cache
            return jsonify(cache['data'])
            
    provider = get_data_provider()
    if not provider:
        return jsonify({'success': False, 'error': 'Data Provider failed'}), 500
        
    try:
        provider_name = provider.__class__.__name__.lower()
        is_kite = 'kite' in provider_name
        
        # Check if the symbol is an index
        index_map = {
            'NIFTY':            'NSE:NIFTY 50' if is_kite else 'NSE:NIFTY50-INDEX',
            'BANKNIFTY':        'NSE:NIFTY BANK' if is_kite else 'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':         'NSE:NIFTY FIN SERVICE' if is_kite else 'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY':       'NSE:NIFTY MID SELECT' if is_kite else 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':           'BSE:SENSEX' if is_kite else 'BSE:SENSEX-INDEX',
            'INDIAVIX':         'NSE:INDIA VIX' if is_kite else 'NSE:INDIAVIX-INDEX',
            'NIFTY IT':         'NSE:NIFTY IT' if is_kite else 'NSE:NIFTYIT-INDEX',
            'NIFTY AUTO':       'NSE:NIFTY AUTO' if is_kite else 'NSE:NIFTYAUTO-INDEX',
            'NIFTY FMCG':       'NSE:NIFTY FMCG' if is_kite else 'NSE:NIFTYFMCG-INDEX',
            'NIFTY METAL':      'NSE:NIFTY METAL' if is_kite else 'NSE:NIFTYMETAL-INDEX',
            'NIFTY PHARMA':     'NSE:NIFTY PHARMA' if is_kite else 'NSE:NIFTYPHARMA-INDEX',
            'NIFTY PSU BANK':   'NSE:NIFTY PSU BANK' if is_kite else 'NSE:NIFTYPSUBANK-INDEX',
            'NIFTY MIDCAP 150': 'NSE:NIFTY MIDCAP 150' if is_kite else 'NSE:NIFTYMIDCAP150-INDEX',
            'NIFTY SMLCAP 100': 'NSE:NIFTY SMALLCAP 100' if is_kite else 'NSE:NIFTYSMLCAP100-INDEX'
        }
        
        if symbol in index_map:
            token = index_map[symbol]
        else:
            token = f"NSE:{symbol}" if is_kite else f"NSE:{symbol}-EQ"
        
        # Fetch current price from quote
        raw_quotes = provider.quote([token])
        q = raw_quotes.get(token, {})
        price = q.get('last_price', 0)
        
        # Fetch multi-timeframe CPR levels using generic helper
        cpr_levels = get_index_cpr_levels(provider, token)
        
        cpr_results = {}
        if price > 0 and cpr_levels:
            for tf, lvl in cpr_levels.items():
                if lvl:
                    tc = lvl['tc']
                    bc = lvl['bc']
                    pp = lvl['pp']
                    
                    if price > tc:
                        status = 'ABOVE'
                    elif price < bc:
                        status = 'BELOW'
                    else:
                        status = 'IN CPR'
                        
                    cpr_width = abs(tc - bc)
                    cpr_width_pct = (cpr_width / pp * 100) if pp else 0
                    
                    if cpr_width_pct < 0.1:
                        cpr_type = 'NARROW'
                    elif cpr_width_pct > 0.25:
                        cpr_type = 'WIDE'
                    else:
                        cpr_type = 'AVERAGE'
                        
                    cpr_results[tf] = {
                        'tc': round(tc, 2),
                        'bc': round(bc, 2),
                        'pp': round(pp, 2),
                        'status': status,
                        'cpr_type': cpr_type,
                        'width_pct': round(cpr_width_pct, 2)
                    }
                    
        res_data = {
            'success': True,
            'symbol': symbol,
            'price': price,
            'cpr_timeframes': cpr_results
        }
        
        _STOCK_CPR_CACHE[cache_key] = {
            'data': res_data,
            'timestamp': now
        }
        
        return jsonify(res_data)
    except Exception as e:
        logger.error(f"Error calculating multi-timeframe CPR for {symbol}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/market-pulse', methods=['GET'])
def get_market_pulse() -> EndpointResponse:
    """
    Consolidated market overview endpoint for the 'Markets' dashboard.
    Returns:
    1. Indices: Live quotes for major Indian indices
    2. Institutional: Latest FII/DII activity data
    3. Heatmap: NIFTY 50 stock quotes
    4. CPR: NIFTY CPR levels
    """
    auth_error = check_auth()
    if auth_error: return auth_error
    
    provider = get_data_provider()
    if not provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401

    try:
        provider_name = provider.__class__.__name__.lower()
        is_kite = 'kite' in provider_name
        
        target_index = request.args.get('index', 'INDEX').upper()
        
        # Prepare Fyers index tokens by default
        index_map = {
            'NIFTY':            'NSE:NIFTY50-INDEX',
            'BANKNIFTY':        'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':         'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY':       'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':           'BSE:SENSEX-INDEX',
            'INDIAVIX':         'NSE:INDIAVIX-INDEX',
            'NIFTY IT':         'NSE:NIFTYIT-INDEX',
            'NIFTY AUTO':       'NSE:NIFTYAUTO-INDEX',
            'NIFTY FMCG':       'NSE:NIFTYFMCG-INDEX',
            'NIFTY METAL':      'NSE:NIFTYMETAL-INDEX',
            'NIFTY PHARMA':     'NSE:NIFTYPHARMA-INDEX',
            'NIFTY PSU BANK':   'NSE:NIFTYPSUBANK-INDEX',
            'NIFTY MIDCAP 150': 'NSE:NIFTYMIDCAP150-INDEX',
            'NIFTY SMLCAP 100': 'NSE:NIFTYSMLCAP100-INDEX'
        }
        if is_kite:
            index_map = {
                'NIFTY':            'NSE:NIFTY 50',
                'BANKNIFTY':        'NSE:NIFTY BANK',
                'FINNIFTY':         'NSE:NIFTY FIN SERVICE',
                'MIDCPNIFTY':       'NSE:NIFTY MID SELECT',
                'SENSEX':           'BSE:SENSEX',
                'INDIAVIX':         'NSE:INDIA VIX',
                'NIFTY IT':         'NSE:NIFTY IT',
                'NIFTY AUTO':       'NSE:NIFTY AUTO',
                'NIFTY FMCG':       'NSE:NIFTY FMCG',
                'NIFTY METAL':      'NSE:NIFTY METAL',
                'NIFTY PHARMA':     'NSE:NIFTY PHARMA',
                'NIFTY PSU BANK':   'NSE:NIFTY PSU BANK',
                'NIFTY MIDCAP 150': 'NSE:NIFTY MIDCAP 150',
                'NIFTY SMLCAP 100': 'NSE:NIFTY SMALLCAP 100'
            }

        from trading_app.service.dynamic_constituents import DynamicConstituentsService
        if target_index == 'INDEX':
            target_stocks = [k for k in index_map.keys() if k != 'INDIAVIX']
        else:
            target_stocks = DynamicConstituentsService.get_constituents(target_index)
            
        index_tokens = list(index_map.values())
        if target_index == 'INDEX':
            stock_tokens = []
        else:
            stock_tokens = [f"NSE:{sym}" if is_kite else f"NSE:{sym}-EQ" for sym in target_stocks]
        all_tokens = index_tokens + stock_tokens

        # Capture username in main thread before starting the workers
        from flask import has_request_context
        username = session.get('username') if has_request_context() else 'Mine'

        # Define individual fetching sub-tasks for parallel execution
        def fetch_quotes():
            try:
                raw = provider.quote(all_tokens)
                if not raw:
                    # Fallback to Kite
                    from trading_app.service.provider_logic import get_kite
                    fallback_provider = get_kite(user=username)
                    if fallback_provider:
                        fb_index_map = {
                            'NIFTY':      'NSE:NIFTY 50',
                            'BANKNIFTY':  'NSE:NIFTY BANK',
                            'FINNIFTY':   'NSE:NIFTY FIN SERVICE',
                            'MIDCPNIFTY': 'NSE:NIFTY MID SELECT',
                            'SENSEX':     'BSE:SENSEX',
                            'INDIAVIX':   'NSE:INDIA VIX'
                        }
                        fb_stock_tokens = [] if target_index == 'INDEX' else [f"NSE:{sym}" for sym in target_stocks]
                        fb_all_tokens = list(fb_index_map.values()) + fb_stock_tokens
                        raw = fallback_provider.quote(fb_all_tokens)
                return raw or {}
            except Exception as e:
                logger.warning(f"[MarketPulse] Error fetching quotes: {e}")
                return {}

        def fetch_institutional():
            from trading_app.service.institutional_service import InstitutionalService
            try:
                return InstitutionalService.get_latest_data()
            except Exception as e:
                logger.warning(f"[MarketPulse] Error getting institutional data: {e}")
                return {
                    'date': datetime.now().strftime('%a, %d %b %Y'),
                    'data': {
                        'fii-cash': 0.0, 'dii-cash': 0.0,
                        'fii-idx-fut': 0.0, 'fii-idx-opt': 0.0,
                        'fii-stk-fut': 0.0, 'fii-stk-opt': 0.0
                    }
                }

        def fetch_cpr():
            try:
                cpr_target = 'NIFTY' if target_index == 'INDEX' else target_index
                return get_index_cpr_levels(provider, cpr_target)
            except Exception as e:
                cpr_target = 'NIFTY' if target_index == 'INDEX' else target_index
                logger.warning(f"[MarketPulse] Error fetching {cpr_target} CPR: {e}")
                return {}

        def fetch_global_markets():
            from trading_app.service.global_market_service import GlobalMarketService
            try:
                return GlobalMarketService.get_latest_data()
            except Exception as e:
                logger.warning(f"[MarketPulse] Error getting global markets data: {e}")
                return []

        # Execute all 4 tasks in parallel using the global _api_executor
        future_quotes = _api_executor.submit(fetch_quotes)
        future_inst = _api_executor.submit(fetch_institutional)
        future_cpr = _api_executor.submit(fetch_cpr)
        future_global = _api_executor.submit(fetch_global_markets)

        raw_quotes = future_quotes.result()
        institutional = future_inst.result()
        cpr_levels = future_cpr.result()
        global_markets = future_global.result()

        # Dynamically determine if we actually got Kite or Fyers format in raw_quotes
        actual_is_kite = is_kite
        if raw_quotes:
            if 'NSE:NIFTY 50' in raw_quotes:
                actual_is_kite = True
            elif 'NSE:NIFTY50-INDEX' in raw_quotes:
                actual_is_kite = False

        parsed_index_map = {
            'NIFTY':            'NSE:NIFTY 50' if actual_is_kite else 'NSE:NIFTY50-INDEX',
            'BANKNIFTY':        'NSE:NIFTY BANK' if actual_is_kite else 'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':         'NSE:NIFTY FIN SERVICE' if actual_is_kite else 'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY':       'NSE:NIFTY MID SELECT' if actual_is_kite else 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':           'BSE:SENSEX' if actual_is_kite else 'BSE:SENSEX-INDEX',
            'INDIAVIX':         'NSE:INDIA VIX' if actual_is_kite else 'NSE:INDIAVIX-INDEX',
            'NIFTY IT':         'NSE:NIFTY IT' if actual_is_kite else 'NSE:NIFTYIT-INDEX',
            'NIFTY AUTO':       'NSE:NIFTY AUTO' if actual_is_kite else 'NSE:NIFTYAUTO-INDEX',
            'NIFTY FMCG':       'NSE:NIFTY FMCG' if actual_is_kite else 'NSE:NIFTYFMCG-INDEX',
            'NIFTY METAL':      'NSE:NIFTY METAL' if actual_is_kite else 'NSE:NIFTYMETAL-INDEX',
            'NIFTY PHARMA':     'NSE:NIFTY PHARMA' if actual_is_kite else 'NSE:NIFTYPHARMA-INDEX',
            'NIFTY PSU BANK':   'NSE:NIFTY PSU BANK' if actual_is_kite else 'NSE:NIFTYPSUBANK-INDEX',
            'NIFTY MIDCAP 150': 'NSE:NIFTY MIDCAP 150' if actual_is_kite else 'NSE:NIFTYMIDCAP150-INDEX',
            'NIFTY SMLCAP 100': 'NSE:NIFTY SMALLCAP 100' if actual_is_kite else 'NSE:NIFTYSMLCAP100-INDEX'
        }

        quotes = {}
        # Process Indices
        for key, token in parsed_index_map.items():
            q = raw_quotes.get(token, {})
            ohlc = q.get('ohlc', {})
            price = q.get('last_price', 0)
            prev_close = ohlc.get('close', 0)
            change = price - prev_close if prev_close else 0
            pchange = (change / prev_close * 100) if prev_close else 0
            
            quotes[key] = {
                'price': price,
                'change': change,
                'pChange': pchange
            }

        # Process Heatmap
        heatmap_data = []
        for sym in target_stocks:
            if target_index == 'INDEX':
                token = parsed_index_map.get(sym)
            else:
                token = f"NSE:{sym}" if actual_is_kite else f"NSE:{sym}-EQ"
            q = raw_quotes.get(token, {})
            ohlc = q.get('ohlc', {})
            price = q.get('last_price', 0)
            prev_close = ohlc.get('close', 0)
            change = price - prev_close if prev_close else 0
            pchange = (change / prev_close * 100) if prev_close else 0
            
            heatmap_data.append({
                'sym': sym,
                'price': price,
                'chg': change,
                'pct': pchange
            })

        # Process CPR Status
        cpr_target = 'NIFTY' if target_index == 'INDEX' else target_index
        index_price = quotes.get(cpr_target, {}).get('price', 0)
        cpr_data = {}
        if index_price > 0 and cpr_levels:
            for tf, lvl in cpr_levels.items():
                if lvl:
                    tc = lvl['tc']
                    bc = lvl['bc']
                    pp = lvl['pp']
                    
                    if index_price > tc:
                        status = 'ABOVE'
                    elif index_price < bc:
                        status = 'BELOW'
                    else:
                        status = 'IN CPR'
                    
                    # Calculate CPR width as a percentage of Pivot Point
                    cpr_width = abs(tc - bc)
                    cpr_width_pct = (cpr_width / pp * 100) if pp else 0
                    
                    if cpr_width_pct < 0.1:
                        cpr_type = 'NARROW'
                    elif cpr_width_pct > 0.25:
                        cpr_type = 'WIDE'
                    else:
                        cpr_type = 'AVERAGE'
                        
                    cpr_data[tf] = {
                        'pp': pp,
                        'bc': bc,
                        'tc': tc,
                        'status': status,
                        'cpr_type': cpr_type
                    }

        return jsonify({
            'success': True,
            'indices': quotes,
            'heatmap': heatmap_data,
            'institutional': institutional,
            'cpr': cpr_data,
            'global': global_markets,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error generating market pulse: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/symbols', methods=['GET'])
def get_symbols() -> EndpointResponse:
    """Get list of available symbols (Indices + F&O Stocks)."""
    global _symbols_cache
    
    # Check cache (valid for 1 hour)
    current_time = datetime.now().timestamp()
    if _symbols_cache['data'] and (current_time - _symbols_cache['last_updated'] < 3600):
        # logger.debug("Serving symbols from cache")
        return jsonify({
            'success': True,
            'symbols': _symbols_cache['data']
        })

    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    # Data fetch always uses Broker 1 (data account)
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        from trading_app.filters import CPRFilterService
        
        # Initialize service
        cpr_service = CPRFilterService(kite_instance=current_provider)
        
        # Get F&O Stocks
        fo_stocks = cpr_service.get_fo_stocks()
        
        # Define Indices - ensure these are always returned even if F&O fails
        from trading_app.service.dynamic_constituents import HARDCODED_CONSTITUENTS
        indices = list(HARDCODED_CONSTITUENTS.keys())
        
        if fo_stocks:
            # Combine and Sort
            # Indices first, then stocks alphabetically
            combined_symbols = indices + sorted(fo_stocks)
            
            # Update cache only if we successfully fetched stocks
            _symbols_cache = {
                'data': combined_symbols,
                'last_updated': current_time
            }
            logger.info(f"Fetched and cached {len(combined_symbols)} symbols (Indices: {len(indices)}, Stocks: {len(fo_stocks)})")
        else:
            # If F&O fetch failed/empty, just return indices but DO NOT CACHE
            # This allows retry on next request
            logger.warning("F&O stocks fetch returned empty list. Returning indices only (not caching).")
            combined_symbols = indices
        
        return jsonify({
            'success': True,
            'symbols': combined_symbols
        })
    except Exception as e:
        logger.error(f"Error fetching symbols: {e}")
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/symbol-metadata', methods=['GET'])
def get_symbol_metadata() -> EndpointResponse:
    """Get metadata for a symbol: lot size and strike step."""
    symbol = request.args.get('symbol', 'NIFTY').upper()
    
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        # Get lot size from provider
        lot_size = 1
        if hasattr(current_provider, 'get_lot_size'):
            lot_size = current_provider.get_lot_size(symbol)
        
        # Calculate strike step from instruments
        strike_step = 50 # Default fallback
        try:
            instruments = current_provider.instruments('NFO')
            symbol_instruments = [
                i for i in instruments 
                if i.get('name', '').upper() == symbol or 
                   i.get('tradingsymbol', '').upper().startswith(symbol)
            ]
            
            strikes = sorted(list(set([float(i.get('strike', 0)) for i in symbol_instruments if i.get('strike')])))
            if len(strikes) > 1:
                # Find most common difference between adjacent strikes
                diffs = []
                for i in range(len(strikes) - 1):
                    d = int(abs(strikes[i+1] - strikes[i]))
                    if d > 0:
                        diffs.append(d)
                
                if diffs:
                    from collections import Counter
                    strike_step = Counter(diffs).most_common(1)[0][0]
        except Exception as e:
            logger.warning(f"Error calculating strike step for {symbol}: {e}")
        
        # Fallback for common indices if step calculation fails
        if strike_step == 50 and (symbol == 'BANKNIFTY' or symbol == 'SENSEX'):
            strike_step = 100
        elif symbol == 'MIDCPNIFTY' and strike_step == 50:
            strike_step = 25
            
        return jsonify({
            'success': True,
            'symbol': symbol,
            'lot_size': lot_size,
            'strike_step': strike_step
        })
    except Exception as e:
        logger.error(f"Error fetching symbol metadata: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/fo-stocks', methods=['GET'])
def get_fo_stocks() -> EndpointResponse:
    """Get list of F&O stocks available for trading."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        from trading_app.filters import CPRFilterService
        
        cpr_service = CPRFilterService(kite_instance=current_provider)
        fo_stocks = cpr_service.get_fo_stocks()
        
        return jsonify({
            'success': True,
            'stocks': fo_stocks
        })
    except Exception as e:
        logger.error(f"Error fetching F&O stocks: {e}")
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/options-init', methods=['GET'])
@limiter.exempt  # Exempt from rate limiting - called on page load
def get_options_init() -> EndpointResponse:
    """
    FAST endpoint - returns strikes immediately using cached NFO instruments and disk cache.
    
    Query params:
        symbol (required): Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
        price_source (optional): 'previous_close' (default) or 'ltp'
        date (optional): Date in YYYY-MM-DD format to get previous day close for that date.
                        If not provided, uses current date's previous close.
                        Example: ?symbol=NIFTY&date=2026-01-12 → Returns 2026-01-11 close
    
    Performance optimizations:
    - Uses disk-cached NFO instruments (8-10s on first call, <500ms on cache hit)
    - Skips PDH/PDL and LTP on initial load (can be fetched separately)
    - Returns immediately with strikes for fast UI initialization
    
    Example URLs:
    - /api/options-init?symbol=NIFTY → Uses today's previous close
    - /api/options-init?symbol=NIFTY&date=2026-01-12 → Uses 11 Jan close (previous day to 12 Jan)
    - /api/options-init?symbol=NIFTY&price_source=ltp → Uses current LTP
    """
    import time as time_module
    start_time = time_module.time()
    
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    symbol = request.args.get('symbol')
    price_source = request.args.get('price_source', 'previous_close')
    target_date = request.args.get('date')  # Optional date in YYYY-MM-DD format
    
    if not symbol:
        return jsonify({'success': False, 'error': 'Symbol is required'}), 400
    
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        from trading_app.service.options_chart_service import OptionsChartService
        
        chart_service = OptionsChartService(current_provider)
        
        # Skip pricing in service - fetch it once here to avoid duplication
        result = chart_service.get_strikes_for_symbol(symbol, price_source, skip_pricing=True, target_date=target_date)
        
        if 'strikes' not in result:
            return jsonify({'success': False, 'error': 'Could not retrieve strike data.'}), 500
        
        strikes = result.get('strikes', [])
        default_ce_token = result.get('default_ce_token')
        default_pe_token = result.get('default_pe_token')
        default_ce_strike = result.get('default_ce_strike')
        default_pe_strike = result.get('default_pe_strike')
        base_price = result.get('base_price')
        
        # Use the base_price already calculated by the service (respects price_source and target_date)
        # For 'previous_close', base_price comes from historical data (get_previous_trading_day_close)
        # For 'ltp', base_price comes from get_current_ltp
        requested_price = base_price or 0.0
        requested_source_label = ' (Close)' if price_source == 'previous_close' else ' (LTP)'
        
        date_label = f" for {target_date}" if target_date else " (current date)"
        logger.info(f"[options-init] {symbol}{date_label}: price_source={price_source}, base_price={base_price}, requested_price={requested_price}, label={requested_source_label}")
        
        total_time = time_module.time() - start_time
        logger.info(f"✓ options-init({symbol}{date_label}) completed in {total_time:.2f}s")
        
        return jsonify({
            'success': True,
            'strikes': strikes,
            'default_ce_strike': default_ce_strike,
            'default_pe_strike': default_pe_strike,
            'default_ce_token': default_ce_token,
            'default_pe_token': default_pe_token,
            'underlying_price': {
                'requested_price': requested_price,
                'source_label': requested_source_label
            }
        })
    except Exception as e:
        logger.error(f"Error in options-init: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/options-strikes', methods=['GET'])
def get_options_strikes() -> EndpointResponse:
    """Legacy endpoint - redirects to /api/options-init"""
    return get_options_init()


@api_bp.route('/options-chart-data', methods=['POST'])
@csrf.exempt
@limiter.exempt  # Exempt from rate limiting - called frequently during trading
def get_options_chart_data() -> EndpointResponse:
    """
    Get historical chart data for CE and PE options.
    
    PRIMARY PATH (Best for Multi-Broker Support):
        POST /api/options-chart-data
        {
            "symbol": "NIFTY",
            "ce_strike": 25700,
            "pe_strike": 26000,
            "timeframe": "5minute",
            "live": true
        }
        This resolves the correct tokens/symbols for either Zerodha or Fyers automatically.

    FAST PATH (Requires provider-native tokens):
        POST /api/options-chart-data
        {
            "ce_token": 12345678,           # Use integer for Zerodha OR
            "pe_token": "NSE:NIFTY...",      # Use string symbol for Fyers
            "timeframe": "5minute",
            "live": true
        }
    """
    import time as time_module
    start_time = time_module.time()
    
    data = request.get_json(silent=True) or {}
    
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': 'Invalid request body format (must be JSON)'}), 400
    
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        from trading_app.service.options_chart_service import OptionsChartService
        chart_service = OptionsChartService(current_provider)
        
        symbol = data.get('symbol')
        ce_strike_str = data.get('ce_strike')
        pe_strike_str = data.get('pe_strike')
        ce_token = data.get('ce_token')
        pe_token = data.get('pe_token')
        timeframe = data.get('timeframe', '5minute')
        
        # Determine cache behavior from payload
        is_live = data.get('live', False)
        requested_cache = data.get('use_cache', True)
        should_use_cache = requested_cache and not is_live
        
        # RESOLUTION LOGIC:
        # 1. If strikes are provided, ALWAYS resolve tokens to ensure provider-native symbols are used
        if symbol and ce_strike_str and pe_strike_str:
            ce_strike = float(ce_strike_str)
            pe_strike = float(pe_strike_str)
            
            logger.info(f"[options-chart-data] Resolving tokens for {symbol} {ce_strike}C/{pe_strike}P for {current_provider.__class__.__name__}")
            ce_token, pe_token = chart_service.get_tokens_for_strikes(symbol, ce_strike, pe_strike)
            
        # 2. If no strikes, but tokens provided, use them as-is
        elif not ce_token or not pe_token:
            return jsonify({
                'success': False,
                'error': 'Provide either (symbol + ce_strike + pe_strike) OR provider-native (ce_token + pe_token)',
                'example': {
                    'symbol': 'NIFTY',
                    'ce_strike': 25700,
                    'pe_strike': 26000,
                    'timeframe': '5minute'
                }
            }), 400
        
        if not ce_token or not pe_token:
            return jsonify({
                'success': False,
                'error': f'Could not resolve tokens for {symbol}. Check if expiry has passed.'
            }), 404
        
        logger.info(f"[options-chart-data] Fetching for {ce_token} and {pe_token} (timeframe={timeframe}, cache={should_use_cache})")
        ce_data, pe_data = chart_service.get_chart_data(ce_token, pe_token, timeframe, use_cache=should_use_cache)
        
        combined_data = []
        for candle in ce_data:
            combined_data.append({**candle, 'type': 'CE'})
        for candle in pe_data:
            combined_data.append({**candle, 'type': 'PE'})
        
        combined_data.sort(key=lambda x: x['date'])
        
        elapsed = time_module.time() - start_time
        logger.info(f"✓ options-chart-data completed in {elapsed:.2f}s")
        
        return jsonify({
            'success': True,
            'data': combined_data,
            'response_time_ms': int(elapsed * 1000)
        })
    except Exception as e:
        logger.error(f"Error fetching chart data: {e}", exc_info=True)
        error_str = str(e).lower()
        
        # Handle token-related errors
        if any(x in error_str for x in ['access_token', 'unauthorized', '401', 'invalid token']):
            logger.warning(f"Token validation error: {e}")
            return jsonify({
                'success': False,
                'error': 'Invalid or expired access token. Please login again.',
                'auth_error': True,
                'details': 'Your session may have expired. Please refresh and try again.'
            }), 401
        
        # Handle 403 Forbidden (access denied)
        if any(x in error_str for x in ['access denied', '403', 'forbidden']):
            logger.warning(f"Access denied error: {e}")
            return jsonify({
                'success': False,
                'error': 'Access denied. Token may have expired. Please login again.',
                'auth_error': True
            }), 403
        
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        }), 500

@api_bp.route('/options-pdh-pdl', methods=['POST'])
@csrf.exempt
@limiter.exempt  # Exempt from rate limiting - called frequently during chart updates
def get_options_pdh_pdl() -> EndpointResponse:
    """
    Get previous day high/low for CE/PE options AND the underlying index.
    
    Accepts one of two input methods:
    1. Tokens: ce_token and pe_token (preferred - faster)
    2. Strikes: symbol, ce_strike, pe_strike (fallback - will resolve to tokens)
    
    Optional:
    - date: YYYY-MM-DD format to get PDH/PDL from the day before the specified date
    
    Returns:
    - pdh/pdl: Previous day high/low for the underlying index
    - ce_pdh/ce_pdl: Previous day high/low for CE option
    - pe_pdh/pe_pdl: Previous day high/low for PE option
    """
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    data = request.get_json(silent=True) or {}
    
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': 'Invalid request body format (must be JSON)'}), 400
    
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        from trading_app.service.options_chart_service import OptionsChartService
        
        chart_service = OptionsChartService(current_provider)
        
        # PREFERRED METHOD: Get tokens from request
        ce_token = data.get('ce_token')
        pe_token = data.get('pe_token')
        symbol = data.get('symbol')  # Optional for getting underlying PDH/PDL
        target_date = data.get('date')  # Optional date in YYYY-MM-DD format
        
        # FALLBACK METHOD: If tokens not provided, resolve them from strikes
        if not ce_token or not pe_token:
            symbol = data.get('symbol')
            ce_strike_str = data.get('ce_strike')
            pe_strike_str = data.get('pe_strike')
            
            if not symbol or not ce_strike_str or not pe_strike_str:
                return jsonify({
                    'success': False,
                    'error': 'Either provide ce_token & pe_token, or provide symbol & ce_strike & pe_strike'
                }), 400
            
            ce_strike = float(ce_strike_str)
            pe_strike = float(pe_strike_str)
            
            ce_token, pe_token = chart_service.get_tokens_for_strikes(symbol, ce_strike, pe_strike)
            
            if not ce_token or not pe_token:
                return jsonify({
                    'success': False,
                    'error': f'Could not find tokens for the given strikes: CE {ce_strike}, PE {pe_strike}'
                }), 404
        
        # Fetch PDH/PDL for options using tokens (with optional target_date)
        pdh_pdl = chart_service.get_pdh_pdl(ce_token, pe_token, target_date)
        
        # Fetch PDH/PDL for the underlying index if symbol is provided
        underlying_pdh = None
        underlying_pdl = None
        
        if symbol:
            try:
                # Get the underlying instrument token/symbol for the active provider
                underlying_token = chart_service.kite_service.get_instrument_token(symbol.upper())
                
                if underlying_token:
                    # Fetch previous day's OHLC for the underlying (with optional target_date)
                    underlying_ohlc = chart_service._fetch_prev_day_ohlc(underlying_token, target_date)
                    underlying_pdh = underlying_ohlc.get('high')
                    underlying_pdl = underlying_ohlc.get('low')
                    date_label = f" for {target_date}" if target_date else ""
                    logger.info(f"Underlying {symbol}{date_label} PDH/PDL: {underlying_pdh}/{underlying_pdl} (token/symbol: {underlying_token})")
                else:
                    logger.warning(f"Could not resolve underlying token for {symbol}")
            except Exception as e:
                logger.warning(f"Error fetching underlying PDH/PDL for {symbol}: {e}")
        
        # Return both options PDH/PDL and underlying PDH/PDL
        return jsonify({
            'success': True,
            'pdh_pdl': pdh_pdl,
            'pdh': underlying_pdh,  # Global index PDH
            'pdl': underlying_pdl,  # Global index PDL
            'ce_token': ce_token,
            'pe_token': pe_token,
            'symbol': symbol,
            'date': target_date
        })
    except Exception as e:
        logger.error(f"Error fetching PDH/PDL: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500


_cpr_service_cache: Dict[str, Tuple[Any, float]] = {}

def _get_cpr_service(kite_instance: Any) -> Any:
    key = str(id(kite_instance))
    cached = _cpr_service_cache.get(key)
    if cached and _time.time() - cached[1] < 3600:
        return cached[0]
    from trading_app.filters.cpr_filter import CPRFilterService
    svc = CPRFilterService(kite_instance=kite_instance)
    _cpr_service_cache[key] = (svc, _time.time())
    return svc


@api_bp.route('/cpr-filter', methods=['GET'])
@limiter.exempt
def get_cpr_filter_results() -> EndpointResponse:
    """Get stocks filtered by CPR strategy."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_kite = get_data_provider()
    if not current_kite:
        return jsonify({'success': False, 'error': 'Data Provider initialization failed.'}), 401
    
    # Get date parameter
    date_str = request.args.get('date')
    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    # Short-term cache to avoid repeated heavy CPR computations from rapid polling.
    from trading_app.app.utils.cache import cpr_filter_cache
    cache_user = session.get('username', 'anonymous')
    cache_date = date_str or datetime.now().strftime('%Y-%m-%d')
    cache_key = f"cpr_filter_v2:{cache_user}:{cache_date}"

    refresh = request.args.get('refresh', 'false').lower() == 'true'
    if not refresh:
        cached_response = cpr_filter_cache.get(cache_key)
        if cached_response is not None:
            return jsonify(cached_response)
    else:
        # If refreshing, clear existing cache entry
        cpr_filter_cache.delete(cache_key)

    try:
        # Verify kite has access token
        if not hasattr(current_kite, 'access_token') or not current_kite.access_token:
            logger.warning("CPR filter request: KiteConnect instance has no access token")
            return jsonify({
                'success': False,
                'error': 'No valid access token on KiteConnect instance. Please login again.',
                'auth_error': True
            }), 401
        
        cpr_service = _get_cpr_service(current_kite)
        
        # logger.info("Starting CPR filter stocks processing...")
        results = cpr_service.filter_cpr_stocks(root_date=target_date, skip_iv=True)
        
        camarilla_cpr_reversal = results.get('camarilla_cpr_reversal', {}) if isinstance(results, dict) else {}
        drsi_filter = results.get('drsi_filter', {}) if isinstance(results, dict) else {}
        
        # FILTER RESPONSE DATA before sending to frontend
        # Apply any data validation/filtering here if needed
        camarilla_cpr_reversal = camarilla_cpr_reversal if isinstance(camarilla_cpr_reversal, dict) else {}
        drsi_filter = drsi_filter if isinstance(drsi_filter, dict) else {}

        payload = {
            'success': True, 
            'camarilla_cpr_reversal': camarilla_cpr_reversal,
            'drsi_filter': drsi_filter,
            'date': target_date.strftime('%Y-%m-%d') if target_date else datetime.now().strftime('%Y-%m-%d')
        }

        cpr_filter_cache.set(cache_key, payload)
        return jsonify(payload)
    except Exception as e:
        logger.error(f"Error in CPR filter: {type(e).__name__}: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str or 'invalid' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': f'CPR filter error: {str(e)}'}), 500


@api_bp.route('/cpr-filter/high-iv', methods=['GET'])
@limiter.exempt
def get_cpr_high_iv_results() -> EndpointResponse:
    """Get F&O stocks with High IV Percentile (>80%)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_kite = get_data_provider()
    if not current_kite:
        return jsonify({'success': False, 'error': 'Data Provider initialization failed.'}), 401

    # Get date parameter
    date_str = request.args.get('date')
    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    # Short-term cache to avoid repeated heavy computations from rapid polling.
    from trading_app.app.utils.cache import cpr_filter_cache
    cache_user = session.get('username', 'anonymous')
    cache_date = date_str or datetime.now().strftime('%Y-%m-%d')
    cache_key = f"cpr_filter_high_iv:{cache_user}:{cache_date}"

    refresh = request.args.get('refresh', 'false').lower() == 'true'
    if not refresh:
        cached_response = cpr_filter_cache.get(cache_key)
        if cached_response is not None:
            return jsonify(cached_response)
    else:
        # If refreshing, clear existing cache entry
        cpr_filter_cache.delete(cache_key)

    try:
        # Verify kite has access token
        if not hasattr(current_kite, 'access_token') or not current_kite.access_token:
            logger.warning("High IV request: KiteConnect instance has no access token")
            return jsonify({
                'success': False,
                'error': 'No valid access token on KiteConnect instance. Please login again.',
                'auth_error': True
            }), 401
        
        cpr_service = _get_cpr_service(current_kite)
        stocks = cpr_service.get_fo_stocks()
        
        logger.info(f"Starting separate High IV scan for {len(stocks)} F&O stocks...")
        high_iv_stocks = cpr_service._batch_compute_iv_percentiles(stocks)
        
        payload = {
            'success': True,
            'high_iv_stocks': sorted(high_iv_stocks, key=lambda x: x['iv_percentile'], reverse=True),
            'date': target_date.strftime('%Y-%m-%d') if target_date else datetime.now().strftime('%Y-%m-%d')
        }

        cpr_filter_cache.set(cache_key, payload, timeout=120)  # cache for 2 minutes
        return jsonify(payload)
    except Exception as e:
        logger.error(f"Error in High IV scanner: {type(e).__name__}: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str or 'invalid' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': f'High IV filter error: {str(e)}'}), 500


# ====================== TREND DETECTION ======================

_TREND_INDEX_SYMBOLS = {'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'NIFTYNXT50'}


@api_bp.route('/trend-detection', methods=['GET'])
@limiter.exempt
def get_trend_detection() -> EndpointResponse:
    """Classify market regime (TREND_UP / TREND_DOWN / SIDEWAYS) and project next-day range."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    current_kite = get_data_provider()
    if not current_kite:
        return jsonify({'success': False, 'error': 'Data provider not ready'}), 401

    symbol = request.args.get('symbol', 'NIFTY').strip().upper()
    try:
        lookback = min(max(int(request.args.get('lookback', 20)), 10), 60)
    except (ValueError, TypeError):
        lookback = 20

    date_str = request.args.get('date')
    try:
        end_date = datetime.strptime(date_str, '%Y-%m-%d') if date_str else datetime.now()
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    cpr_svc = _get_cpr_service(current_kite)
    df = cpr_svc.get_hist_data(symbol, days=lookback + 80, end_date=end_date)
    if df is None or len(df) < lookback + 20:
        return jsonify({'success': False, 'error': 'Insufficient historical data for this symbol'}), 400

    vix_data = None
    oi_data  = None
    if symbol in _TREND_INDEX_SYMBOLS:
        try:
            from trading_app.service.open_interest_service import OpenInterestService
            oi_svc   = OpenInterestService(kite_instance=current_kite)
            vix_data = oi_svc._get_india_vix_data()
            db_snap  = oi_svc.get_latest_oi_from_db(symbol, max_age_mins=10)
            if db_snap:
                oi_data = {
                    'pcr_oi':        db_snap.get('pcr_oi'),
                    'max_pain':      db_snap.get('max_pain'),
                    'atm_iv':        db_snap.get('atm_iv'),
                    'iv_percentile': db_snap.get('iv_percentile'),
                }
        except Exception as ve:
            logger.warning(f"VIX/OI fetch skipped for {symbol}: {ve}")

    try:
        from trading_app.filters.trend_filter import TrendFilterService
        result = TrendFilterService().analyse(
            df, lookback=lookback, vix_data=vix_data, oi_data=oi_data, symbol=symbol)

        if vix_data:
            vix_range = max(vix_data['high'] - vix_data['low'], 0.01)
            result['india_vix'] = {
                'current':    round(float(vix_data['current']), 2),
                'year_high':  round(float(vix_data['high']), 2),
                'year_low':   round(float(vix_data['low']), 2),
                'percentile': round((vix_data['current'] - vix_data['low']) / vix_range * 100, 1),
            }
        if oi_data:
            result['oi_context'] = {
                'pcr':           round(float(oi_data.get('pcr_oi') or 0), 2),
                'max_pain':      oi_data.get('max_pain'),
                'atm_iv':        round(float(oi_data.get('atm_iv') or 0) * 100, 1),
                'iv_percentile': round(float(oi_data.get('iv_percentile') or 0), 1),
            }

        return jsonify({
            'success': True,
            'symbol': symbol,
            'date': end_date.strftime('%Y-%m-%d'),
            **result,
        })
    except Exception as e:
        logger.error(f"Trend detection failed for {symbol}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ====================== EMA/RSI 208 FILTER ======================

@api_bp.route('/ema-rsi-filter', methods=['GET'])
@limiter.exempt
def get_ema_rsi_filter_results() -> EndpointResponse:
    """Scan F&O stocks for Weekly EMA/RSI-208 and Daily EMA/RSI-88 touches."""
    auth_error = check_auth()
    if auth_error:
        return auth_error

    current_kite = get_data_provider()
    if not current_kite:
        return jsonify({'success': False, 'error': 'Data Provider initialization failed.'}), 401

    # ── Parse optional date param (same as CPR filter) ────────────────────────
    date_str    = request.args.get('date')
    timeframe_filter = request.args.get('timeframe', 'weekly')  # 'weekly' or 'monthly'
    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    # ── Cache key per date + timeframe + 10-min bucket ────────────────────────────────────
    from trading_app.app.utils.cache import cpr_filter_cache  # reuse same cache backend
    cache_date = date_str or datetime.now().strftime('%Y-%m-%d')
    cache_key  = f"ema_rsi_filter_v2:{cache_date}:{timeframe_filter}:{datetime.now().minute // 10}"

    cached = cpr_filter_cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    try:
        from trading_app.filters.ema_rsi_filter import EmaRsiFilterService
        svc    = EmaRsiFilterService(kite_instance=current_kite)
        result = svc.run_filter(root_date=target_date, timeframe=timeframe_filter)

        payload = {
            'success':         True,
            'results':         result.get('results', []),
            'nearest':         result.get('nearest', []),
            'date':            cache_date,
            'generated_at':    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        cpr_filter_cache.set(cache_key, payload)
        return jsonify(payload)
    except Exception as e:
        logger.error(f"EMA/RSI filter error: {type(e).__name__}: {e}", exc_info=True)
        err_str = str(e).lower()
        if 'access_token' in err_str or 'unauthorized' in err_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': f'EMA/RSI filter error: {str(e)}'}), 500


@api_bp.route('/notify-whatsapp', methods=['POST'])
@csrf.exempt
def notify_whatsapp() -> EndpointResponse:
    """Send a WhatsApp message using WhatsApp Cloud API credentials from env vars."""
    auth_error = check_auth()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    to_number = (data.get('to') or '').strip()

    if not message:
        return jsonify({'success': False, 'error': 'message is required'}), 400

    try:
        from trading_app.service.whatsapp_service import WhatsAppService

        wa_service = WhatsAppService()
        result = wa_service.send_text(message, to_number if to_number else None)

        if result.get('success'):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': result.get('error', 'WhatsApp send failed')}), 500
    except Exception as e:
        logger.error(f"WhatsApp notify error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/historical/instrument-token', methods=['GET'])
def get_instrument_token() -> EndpointResponse:
    """Get instrument token for a symbol."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        current_kite = get_kite()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Kite not connected. Please login.'}), 401
        symbol = request.args.get('symbol', '').upper()
        symbol_type = request.args.get('type', 'fno').lower()
        fno_type = request.args.get('fno_type', 'futures').lower()

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol parameter is required'}), 400

        instrument_token = None

        if symbol == 'NIFTY':
            try:
                kite_service = KiteService(kite_instance=current_kite)
                instrument_token = kite_service.get_instrument_token(symbol)
            except Exception as e:
                logger.error(f"Error getting index token for {symbol}: {e}")
                return jsonify({
                    'success': False,
                    'error': f'Error fetching token for index {symbol}: {str(e)}'
                }), 500
        else:
            if fno_type == 'futures':
                try:
                    instruments = current_kite.instruments('NFO')
                    for inst in instruments:
                        if inst.get('name') == symbol and inst.get('segment') == 'NFO-FUT':
                            instrument_token = inst.get('instrument_token')
                            break
                except Exception as e:
                    logger.error(f"Error fetching NFO instruments: {e}")
                    error_str = str(e).lower()
                    if 'access_token' in error_str or 'unauthorized' in error_str:
                        return jsonify({
                            'success': False,
                            'error': 'Authentication failed. Access token expired.',
                            'auth_error': True
                        }), 401
                    return jsonify({'success': False, 'error': f'Error fetching F&O instruments: {str(e)}'}), 500
            else:
                return jsonify({
                    'success': False,
                    'error': 'Options require expiry and strike parameters'
                }), 400
        
        if instrument_token:
            return jsonify({
                'success': True,
                'instrument_token': instrument_token,
                'symbol': symbol,
                'type': symbol_type
            })
        else:
            return jsonify({
                'success': False,
                'error': f'Instrument token not found for symbol: {symbol}'
            }), 404
    except Exception as e:
        logger.error(f"Error fetching instrument token: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/historical', methods=['POST'])
@csrf.exempt
def get_historical_data() -> EndpointResponse:
    """Fetch historical OHLC data."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_kite = get_data_provider()
    if not current_kite:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400
        
        instrument_token = data.get('instrument_token')
        from_date = data.get('from_date')
        to_date = data.get('to_date')
        interval = data.get('interval', '5minute')
        
        if not instrument_token or not from_date or not to_date:
            return jsonify({
                'success': False,
                'error': 'Missing required parameters: instrument_token, from_date, to_date'
            }), 400
        
        logger.info(f"Fetching historical data: token={instrument_token}, from={from_date}, to={to_date}, interval={interval}")
        
        try:
            candles = current_kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval
            )
        except Exception as kite_error:
            logger.error(f"KiteConnect historical_data error: {kite_error}")
            error_str = str(kite_error).lower()
            if 'access_token' in error_str or 'unauthorized' in error_str:
                return jsonify({
                    'success': False,
                    'error': 'Authentication failed. Access token expired.',
                    'auth_error': True
                }), 401
            raise kite_error
        
        if not candles:
            return jsonify({
                'success': True,
                'data': [],
                'message': 'No data available for the given parameters'
            })
        
        formatted_data = []
        for candle in candles:
            formatted_data.append({
                'date': candle['date'],
                'open': candle['open'],
                'high': candle['high'],
                'low': candle['low'],
                'close': candle['close'],
                'volume': candle['volume'],
                'oi': candle.get('oi', 0)
            })
        
        return jsonify({
            'success': True,
            'data': formatted_data,
            'count': len(formatted_data)
        })
    except Exception as e:
        logger.error(f"Error fetching historical data: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500


@csrf.exempt
@limiter.exempt
@api_bp.route('/strategy-backtest', methods=['POST'])
def run_strategy_backtest() -> EndpointResponse:
    """Run strategy backtest with given parameters."""
    try:
        current_kite = get_kite()
        if not current_kite:
            return jsonify({
                'status': 'error',
                'message': 'Failed to initialize KiteConnect. Check API keys or login status.'
            }), 401
        
        data = request.get_json(silent=True) or {}
        
        if not isinstance(data, dict):
            return jsonify({'status': 'error', 'message': 'Invalid request body format (must be JSON)'}), 400
        
        symbol = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')
        
        if not start_date_str or not end_date_str:
            return jsonify({'status': 'error', 'message': 'start_date and end_date are required'}), 400
        
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        
        from trading_app.strategy.backtest import OptionsStrategy
        strategy = OptionsStrategy(kite_instance=current_kite)
        strategy.backtest_strategy(start_date, end_date, symbol)
        
        return jsonify({
            'status': 'success',
            'data': strategy.entry_exit_log
        })
    except Exception as e:
        logger.error(f"Error running strategy backtest: {e}")
        if "token" in str(e).lower() or "auth" in str(e).lower():
            return jsonify({
                'status': 'error',
                'message': 'Authentication failed. Please check your login status.',
                'auth_error': True
            }), 401
        return jsonify({'status': 'error', 'message': str(e)}), 500


@api_bp.route('/backtest/symbols', methods=['GET'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def get_backtest_symbols():
    """Fetch all unique future stocks and indices for backtesting."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        import json
        import os
        
        # Path to cached NFO instruments
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.cache', 'nfo_instruments.json')
        
        if not os.path.exists(cache_path):
            return jsonify({'success': False, 'error': 'NFO instruments cache not found. Please login to refresh.'}), 404
            
        with open(cache_path, 'r') as f:
            instruments = json.load(f)
            
        # Filter unique names for futures
        futures = set()
        indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX']
        
        for inst in instruments:
            if inst.get('instrument_type') == 'FUT':
                name = inst.get('name')
                if name:
                    futures.add(name)
        
        # Combine and sort
        all_symbols = sorted(list(futures))
        
        return jsonify({
            'success': True,
            'symbols': all_symbols,
            'indices': indices
        })
    except Exception as e:
        logger.error(f"Error fetching backtest symbols: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/apex-reversal', methods=['GET', 'POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_apex_reversal_backtest():
    """Run Apex Reversal backtest for a specific symbol and date range."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data = request.get_json()
        symbol = data.get('symbol')
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')
        interval = data.get('interval', '5minute')
        
        # Strategy parameters
        params = {
            'pivot_strength': int(data.get('pivot_strength', 1)),
            'rsi_length': int(data.get('rsi_length', 14)),
            'rsi_overbought': int(data.get('rsi_overbought', 70)),
            'rsi_oversold': int(data.get('rsi_oversold', 30)),
            'rr_ratio': float(data.get('rr_ratio', 3.0)),
            'entry_buffer': float(data.get('entry_buffer', 10.0)),
            'interval': interval,
            'intraday_only': data.get('intraday_only', True),
            'sl_close_price': data.get('sl_close_price', True),
            'trail_candles': int(data.get('trail_candles', 0))
        }
        
        if not symbol or not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400
            
        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401
            
        # Get instrument token for historical data
        from trading_app.service.kite_order_services import KiteService
        kite_service = KiteService(kite_instance=current_kite)
        
        # We need the instrument token for the index or the FUT
        # For simplicity, we'll try to find the token in the cache
        import json
        import os
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.cache', 'nfo_instruments.json')
        
        instrument_token = None
        if os.path.exists(cache_path):
            with open(cache_path, 'r') as f:
                instruments = json.load(f)
                for inst in instruments:
                    if inst.get('name') == symbol and inst.get('instrument_type') == 'FUT':
                        # Prefer the near month future for price data, or we could use the index token
                        # Actually, index data is better for Apex Reversal logic if it's an index.
                        instrument_token = inst.get('instrument_token')
                        break
        
        if not instrument_token:
            # Fallback to index tokens if it's a major index
            index_tokens = {
                'NIFTY': 256265,
                'BANKNIFTY': 260105,
                'FINNIFTY': 257801,
                'MIDCPNIFTY': 288009,
                'NIFTY MIDCAP 150': 266249,
                'NIFTY AUTO':      263433,
                'NIFTY Smallcap 100': 267017,
                'NIFTY SMLCAP 100': 267017,
                'NIFTY FMCG':      261897,
                'NIFTY METAL':     263689,
                'NIFTY PHARAMA':   262409,
                'NIFTY PHARMA':    262409,
                'NIFTY PSU BANK':  262921,
                'NIFTY IT':        259849,
            }
            instrument_token = index_tokens.get(symbol)
            
        # Provider-specific adjustments (especially for Fyers)
        if hasattr(current_kite, 'fyers'):
            # Fyers expects symbol strings, not Kite tokens
            fyers_indices = {
                'NIFTY': 'NSE:NIFTY50-INDEX',
                'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
                'FINNIFTY': 'NSE:FINNIFTY-INDEX',
                'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
                'SENSEX': 'BSE:SENSEX-INDEX',
                'NIFTY MIDCAP 150': 'NSE:NIFTYMIDCAP150-INDEX',
                'NIFTY AUTO':      'NSE:NIFTYAUTO-INDEX',
                'NIFTY Smallcap 100': 'NSE:NIFTYSMLCAP100-INDEX',
                'NIFTY SMLCAP 100': 'NSE:NIFTYSMLCAP100-INDEX',
                'NIFTY FMCG':      'NSE:NIFTYFMCG-INDEX',
                'NIFTY METAL':     'NSE:NIFTYMETAL-INDEX',
                'NIFTY PHARAMA':   'NSE:NIFTYPHARMA-INDEX',
                'NIFTY PHARMA':    'NSE:NIFTYPHARMA-INDEX',
                'NIFTY PSU BANK':  'NSE:NIFTYPSUBANK-INDEX',
                'NIFTY IT':        'NSE:NIFTYIT-INDEX',
            }
            if symbol in fyers_indices:
                instrument_token = fyers_indices[symbol]
            else:
                # For stocks, try to find the Fyers symbol in the Fyers instruments list
                try:
                    fyers_inst = current_kite.instruments('NSE')
                    # Look for the -EQ symbol first as it's best for price history
                    for inst in fyers_inst:
                        if inst.get('name') == symbol and inst.get('instrument_type') == 'EQ':
                            instrument_token = inst.get('instrument_token')
                            break
                    
                    if not instrument_token or isinstance(instrument_token, int):
                        # Fallback to a guessed -EQ symbol
                        instrument_token = f"NSE:{symbol}-EQ"
                except Exception as e:
                    logger.error(f"Error fetching Fyers instruments for backtest: {e}")
                    instrument_token = f"NSE:{symbol}-EQ"
        
        if not instrument_token:
            return jsonify({'success': False, 'error': f'Could not find instrument token for {symbol}'}), 404
            
        # Fetch historical data
        candles = current_kite.historical_data(
            instrument_token=instrument_token,
            from_date=start_date_str,
            to_date=end_date_str,
            interval=interval
        )
        
        if not candles:
            return jsonify({'success': False, 'error': 'No historical data found for the given range'}), 404
            
        import pandas as pd
        df = pd.DataFrame(candles)
        
        import importlib
        import trading_app.Backtest.apex_reversal_engine as _apex_mod
        importlib.reload(_apex_mod)
        from trading_app.Backtest.apex_reversal_engine import run_apex_backtest
        trades = run_apex_backtest(df, params)
        
        return jsonify({
            'success': True,
            'trades': trades,
            'summary': {
                'total_trades': len(trades),
                'wins': len([t for t in trades if t['pnl'] > 0]),
                'losses': len([t for t in trades if t['pnl'] <= 0]),
                'total_pnl': sum([t['pnl'] for t in trades])
            }
        })
        
    except Exception as e:
        logger.error(f"Error in Apex Reversal backtest API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/cpr-gap', methods=['GET', 'POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_cpr_gap_backtest_api():
    """Run CPR Gap backtest for a specific symbol and date range."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data = request.get_json()
        symbol = data.get('symbol')
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')
        interval = data.get('interval', '5minute')
        
        # Strategy parameters
        params = {
            'interval': interval,
            'intraday_only': data.get('intraday_only', True),
            'rr_ratio': float(data.get('rr_ratio', 2.0)),
            'cpr_type': data.get('cpr_type', 's1_r1'),
            'sl_close_price': data.get('sl_close_price', True),
            'entry_type': data.get('entry_type', 'any'),
            'sl_type': data.get('sl_type', 'both')
        }
        
        if not symbol or not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400
            
        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401
            
        import json
        import os
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.cache', 'nfo_instruments.json')
        
        instrument_token = None
        if os.path.exists(cache_path):
            with open(cache_path, 'r') as f:
                instruments = json.load(f)
                for inst in instruments:
                    if inst.get('name') == symbol and inst.get('instrument_type') == 'FUT':
                        instrument_token = inst.get('instrument_token')
                        break
        
        if not instrument_token:
            index_tokens = {
                'NIFTY': 256265,
                'BANKNIFTY': 260105,
                'FINNIFTY': 257801,
                'MIDCPNIFTY': 288009,
                'NIFTY MIDCAP 150': 266249,
                'NIFTY AUTO':      263433,
                'NIFTY Smallcap 100': 267017,
                'NIFTY SMLCAP 100': 267017,
                'NIFTY FMCG':      261897,
                'NIFTY METAL':     263689,
                'NIFTY PHARAMA':   262409,
                'NIFTY PHARMA':    262409,
                'NIFTY PSU BANK':  262921,
                'NIFTY IT':        259849,
            }
            instrument_token = index_tokens.get(symbol)
            
        if hasattr(current_kite, 'fyers'):
            fyers_indices = {
                'NIFTY': 'NSE:NIFTY50-INDEX',
                'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
                'FINNIFTY': 'NSE:FINNIFTY-INDEX',
                'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
                'SENSEX': 'BSE:SENSEX-INDEX',
                'NIFTY MIDCAP 150': 'NSE:NIFTYMIDCAP150-INDEX',
                'NIFTY AUTO':      'NSE:NIFTYAUTO-INDEX',
                'NIFTY Smallcap 100': 'NSE:NIFTYSMLCAP100-INDEX',
                'NIFTY SMLCAP 100': 'NSE:NIFTYSMLCAP100-INDEX',
                'NIFTY FMCG':      'NSE:NIFTYFMCG-INDEX',
                'NIFTY METAL':     'NSE:NIFTYMETAL-INDEX',
                'NIFTY PHARAMA':   'NSE:NIFTYPHARMA-INDEX',
                'NIFTY PHARMA':    'NSE:NIFTYPHARMA-INDEX',
                'NIFTY PSU BANK':  'NSE:NIFTYPSUBANK-INDEX',
                'NIFTY IT':        'NSE:NIFTYIT-INDEX',
            }
            if symbol in fyers_indices:
                instrument_token = fyers_indices[symbol]
            else:
                try:
                    fyers_inst = current_kite.instruments('NSE')
                    for inst in fyers_inst:
                        if inst.get('name') == symbol and inst.get('instrument_type') == 'EQ':
                            instrument_token = inst.get('instrument_token')
                            break
                    if not instrument_token or isinstance(instrument_token, int):
                        instrument_token = f"NSE:{symbol}-EQ"
                except:
                    instrument_token = f"NSE:{symbol}-EQ"
        
        if not instrument_token:
            return jsonify({'success': False, 'error': f'Could not find instrument token for {symbol}'}), 404
            
        candles = current_kite.historical_data(
            instrument_token=instrument_token,
            from_date=start_date_str,
            to_date=end_date_str,
            interval=interval
        )
        
        if not candles:
            return jsonify({'success': False, 'error': 'No historical data found for the given range'}), 404
            
        import pandas as pd
        df = pd.DataFrame(candles)
        
        from trading_app.Backtest.cpr_gap_engine import run_cpr_gap_backtest
        trades = run_cpr_gap_backtest(df, params)
        
        return jsonify({
            'success': True,
            'trades': trades,
            'summary': {
                'total_trades': len(trades),
                'wins': len([t for t in trades if t['pnl'] > 0]),
                'losses': len([t for t in trades if t['pnl'] <= 0]),
                'total_pnl': sum([t['pnl'] for t in trades])
            }
        })
        
    except Exception as e:
        logger.error(f"Error in CPR Gap backtest API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/place-live-order', methods=['POST'])
@csrf.exempt
def place_live_order() -> EndpointResponse:
    """Place a live order for options in Zerodha Kite.
    
    Request JSON:
    {
        "option_type": "CE" or "PE",
        "strike": integer,
        "symbol": "NIFTY" (optional, defaults to NIFTY)
    }
    """
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    try:
        data = request.get_json()
        option_type = data.get('option_type')
        strike = data.get('strike')
        symbol = data.get('symbol', 'NIFTY')
        quantity = data.get('quantity')
        
        # Validation
        if not option_type or option_type not in ['CE', 'PE']:
            return jsonify({'success': False, 'error': 'Invalid option_type. Must be CE or PE'}), 400
        
        if not strike or not isinstance(strike, int):
            return jsonify({'success': False, 'error': 'Invalid strike. Must be an integer'}), 400
        
        kite = get_kite()
        if not kite:
            return jsonify({'success': False, 'error': 'Failed to initialize Kite API'}), 401
        
        # Use KiteService to place the order
        from trading_app.service.kite_order_services import KiteService
        kite_service = KiteService(kite_instance=kite)
        
        if not quantity:
            _lot_map = {'NIFTY': 25, 'BANKNIFTY': 15, 'FINNIFTY': 40, 'MIDCPNIFTY': 75, 'SENSEX': 20}
            quantity = _lot_map.get(symbol, 1)

        result = kite_service.place_option_order(
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=quantity,
        )
        
        if result['success']:
            logger.info(f"✅ Order placed via API: {option_type} {strike} | Order ID: {result['order_id']}")
            return jsonify(result), 200
        else:
            logger.error(f"❌ Order placement failed: {result['error']}")
            return jsonify(result), 400
    
    except Exception as e:
        logger.error(f"Error in place_live_order endpoint: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500


@api_bp.route('/multi-strike', methods=['GET'])
@limiter.exempt  # Exempt from rate limiting
def get_multi_strike() -> EndpointResponse:
    """
    Get multi-strike options data for a symbol.
    
    Query params:
    - symbol: Trading symbol (e.g., 'NIFTY', 'BANKNIFTY')
    - num_strikes: Number of strikes above/below ATM (default 3)
    
    Returns: Multi-strike data with PDH/PDL lines
    """
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    symbol = request.args.get('symbol', 'NIFTY')
    num_strikes = int(request.args.get('num_strikes', 3))
    
    current_provider = get_data_provider()
    if not current_provider:
        return jsonify({'success': False, 'error': 'Data provider initialization failed.'}), 401
    
    try:
        from trading_app.service.multi_strike_service import MultiStrikeService
        
        multi_strike_service = MultiStrikeService(current_provider)
        result = multi_strike_service.get_multi_strike_data(symbol, num_strikes)
        
        if not result.get('success'):
            return jsonify(result), 500
        
        return jsonify(result), 200
    
    except Exception as e:
        logger.error(f"Error in multi-strike endpoint: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/send-notification', methods=['POST'])
def send_notification() -> EndpointResponse:
    """
    Send trend alert notifications via WhatsApp or SMS.
    
    Request JSON:
    {
        "type": "trend_alert",
        "message": "🚀 Trend Changed: BUY → SELL",
        "timestamp": "2026-01-05T10:30:00"
    }
    
    Returns:
    {
        "success": true/false,
        "message": "Notification sent successfully",
        "method": "whatsapp" or "api"
    }
    """
    try:
        data = request.get_json() or {}
        alert_type = data.get('type', 'trend_alert')
        message = data.get('message', 'Trend Alert')
        timestamp = data.get('timestamp', '')
        
        # Format the message
        full_message = f"{message}"
        if timestamp:
            full_message += f" [{timestamp}]"
        
        # Add prefix for trend alerts
        if alert_type == 'trend_alert':
            full_message = f"📊 *Options Chart Alert*\n\n{full_message}"
        
        logger.info(f"Sending {alert_type} notification: {full_message}")
        
        # Try to send via WhatsApp
        try:
            from trading_app.service.whatsapp_service import WhatsAppService
            
            whatsapp = WhatsAppService()
            # Use the mobile number: 8880802168 (India: +91)
            response = whatsapp.send_text(
                message=full_message,
                to_number='918880802168'  # Format: country code + number
            )
            
            if response.get('success'):
                return jsonify({
                    'success': True,
                    'message': 'Notification sent via WhatsApp',
                    'method': 'whatsapp'
                }), 200
            else:
                logger.warning(f"WhatsApp send failed: {response.get('error')}")
                # Continue to fallback
        except Exception as e:
            logger.warning(f"WhatsApp service unavailable: {e}")
            # Continue to fallback
        
        # Fallback: Log notification (can be extended for SMS/Email later)
        logger.info(f"Notification logged (WhatsApp unavailable): {full_message}")
        return jsonify({
            'success': True,
            'message': 'Notification logged successfully',
            'method': 'log'
        }), 200
        
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500




@api_bp.route('/intraday-option/symbol-payload', methods=['GET'])
@csrf.exempt
@limiter.exempt
def get_symbol_payload() -> EndpointResponse:
    """
    Get symbol payload with current price, PDH, PDL, PDC
    Called before starting monitoring to display basic symbol data
    
    Query Parameters:
        symbol (str): Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
        
    Returns:
        JSON with current_price, pdh, pdl, pdc (previous day close)
    """
    try:
        auth_error = check_auth()
        if auth_error:
            return auth_error
        
        # Get parameters
        symbol = request.args.get('symbol', 'NIFTY').upper()
        
        # Validate symbol
        valid_symbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
        if symbol not in valid_symbols:
            return jsonify({
                'success': False,
                'error': f'Invalid symbol. Must be one of {valid_symbols}'
            }), 400
        
        # Get KiteConnect instance
        kite = get_kite()
        if not kite:
            return jsonify({
                'success': False,
                'error': 'Failed to initialize Kite connection'
            }), 500
        
        # Import trader
        from trading_app.app.intraday_option import IntradayOptionTrader
        trader = IntradayOptionTrader(kite)
        
        # Get symbol payload
        payload = trader.get_symbol_payload(symbol)
        
        return jsonify({
            'success': not payload.get('error'),
            'data': payload
        })
        
    except Exception as e:
        logger.error(f"Error in symbol_payload endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/intraday-option/debug-strikes', methods=['GET'])
@csrf.exempt
@limiter.exempt
def debug_available_strikes() -> EndpointResponse:
    """
    Debug endpoint to check available strike options for a symbol
    
    Query Parameters:
        symbol (str): Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
        
    Returns:
        JSON with available strikes and their tokens
    """
    try:
        auth_error = check_auth()
        if auth_error:
            return auth_error
        
        symbol = request.args.get('symbol', 'NIFTY').upper()
        
        # Validate symbol
        valid_symbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
        if symbol not in valid_symbols:
            return jsonify({
                'success': False,
                'error': f'Invalid symbol. Must be one of {valid_symbols}'
            }), 400
        
        # Get KiteConnect instance
        kite = get_kite()
        if not kite:
            return jsonify({
                'success': False,
                'error': 'Failed to initialize Kite connection'
            }), 500
        
        # Import trader
        from trading_app.app.intraday_option import IntradayOptionTrader
        trader = IntradayOptionTrader(kite)
        
        # Get underlying quote to determine ATM
        underlying_data = trader.data_service._get_underlying_quote(symbol)
        underlying_price = underlying_data.get('last_price', 0)
        
        # Get available strikes
        available_strikes = trader.data_service.get_available_strikes(symbol, range_size=10)
        
        if not available_strikes:
            # Provide diagnostic information
            return jsonify({
                'success': False,
                'symbol': symbol,
                'underlying_price': underlying_price,
                'available_strikes': [],
                'error': 'No available strikes found. Market may be closed or symbol not available.',
                'diagnostic_info': {
                    'message': 'This usually happens when:',
                    'reasons': [
                        '1. Market is closed (check if trading hours)',
                        '2. Symbol/expiry format is incorrect',
                        '3. No option contracts exist for this symbol',
                        '4. API token list is stale - try refreshing'
                    ],
                    'next_steps': [
                        'Check if NSE market is currently open',
                        'Verify the symbol exists',
                        'Check Kite Connect API credentials',
                        'Try a different symbol (BANKNIFTY, FINNIFTY)'
                    ]
                }
            })
        
        return jsonify({
            'success': True,
            'symbol': symbol,
            'underlying_price': underlying_price,
            'available_strikes_count': len(available_strikes),
            'available_strikes': available_strikes[:20],  # Return first 20
            'sample_ce_strikes': [s['strike'] for s in available_strikes],
            'recommended_strike': available_strikes[len(available_strikes)//2]['strike'] if available_strikes else None
        })
        
    except Exception as e:
        logger.error(f"Error in debug_strikes endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/intraday-option', methods=['GET', 'POST'])
@csrf.exempt
@limiter.exempt
def get_intraday_option_data() -> EndpointResponse:
    """
    Get intraday option data with real-time quotes and candlesticks
    
    GET Query Parameters:
        symbol (str): Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
        strike_price (float, optional): Strike price (auto-selected if omitted)
        timeframe (str): Candle interval (5minute, 15minute, 30minute, 60minute)
    
    POST Payload:
        {
            "symbol": "NIFTY",
            "timeframe": "5minute",
            "strikes": [23500, 23600, 23700]  // List of strike prices
        }
        
    Returns:
        JSON with CE and PE candlestick data, PDH/PDL, quotes, and trading signals for each strike
    """
    try:
        auth_error = check_auth()
        if auth_error:
            return auth_error
        
        # Determine if request is POST with payload or GET with query params
        if request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'error': 'Request body must contain JSON'
                }), 400
            
            symbol = data.get('symbol', 'NIFTY').upper()
            strikes = data.get('strikes', [])
            timeframe = data.get('timeframe', '5minute')
            
            # Validate strikes parameter
            if not strikes or not isinstance(strikes, list):
                return jsonify({
                    'success': False,
                    'error': 'strikes parameter is required and must be a list of strike prices'
                }), 400
            
            # Fetch data for multiple strikes
            kite = get_kite()
            if not kite:
                return jsonify({
                    'success': False,
                    'error': 'Failed to initialize Kite connection'
                }), 500
            
            from trading_app.app.intraday_option import IntradayOptionTrader
            trader = IntradayOptionTrader(kite)
            
            # Validate symbol
            valid_symbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
            if symbol not in valid_symbols:
                return jsonify({
                    'success': False,
                    'error': f'Invalid symbol. Must be one of {valid_symbols}'
                }), 400
            
            # Validate timeframe
            valid_timeframes = ['5minute', '15minute', '30minute', '60minute']
            if timeframe not in valid_timeframes:
                return jsonify({
                    'success': False,
                    'error': f'Invalid timeframe. Must be one of {valid_timeframes}'
                }), 400
            
            # Fetch data for each strike
            strikes_data = []
            for strike_price in strikes:
                try:
                    option_data = trader.get_option_data(symbol, float(strike_price), timeframe)
                    strikes_data.append(option_data)
                except Exception as e:
                    logger.error(f"Error fetching data for strike {strike_price}: {str(e)}")
                    strikes_data.append({
                        'strike': strike_price,
                        'symbol': symbol,
                        'success': False,
                        'error': str(e)
                    })
            
            return jsonify({
                'success': True,
                'symbol': symbol,
                'timeframe': timeframe,
                'strikes': strikes,
                'data': strikes_data
            })
        
        else:  # GET request (backward compatibility)
            # Get parameters
            symbol = request.args.get('symbol', 'NIFTY').upper()
            strike_price = request.args.get('strike_price', type=float, default=None)
            ce_strike = request.args.get('ce_strike', type=float, default=None)
            pe_strike = request.args.get('pe_strike', type=float, default=None)
            timeframe = request.args.get('timeframe', '5minute')
            days_back = request.args.get('days_back', type=int, default=None)  # New parameter for historical data
            
            # Validate symbol
            valid_symbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
            if symbol not in valid_symbols:
                return jsonify({
                    'success': False,
                    'error': f'Invalid symbol. Must be one of {valid_symbols}'
                }), 400
            
            # Validate timeframe
            valid_timeframes = ['5minute', '15minute', '30minute', '60minute']
            if timeframe not in valid_timeframes:
                return jsonify({
                    'success': False,
                    'error': f'Invalid timeframe. Must be one of {valid_timeframes}'
                }), 400
            
            # Validate days_back if provided
            if days_back is not None and days_back <= 0:
                return jsonify({
                    'success': False,
                    'error': 'days_back must be greater than 0'
                }), 400
            
            # Get KiteConnect instance
            kite = get_kite()
            if not kite:
                return jsonify({
                    'success': False,
                    'error': 'Failed to initialize Kite connection'
                }), 500
            
            # Import and initialize trader
            from trading_app.app.intraday_option import IntradayOptionTrader
            trader = IntradayOptionTrader(kite)
            
            # Get option data with CE and PE strikes if provided
            option_data = trader.get_option_data(
                symbol, 
                strike_price=strike_price, 
                timeframe=timeframe,
                ce_strike=ce_strike,
                pe_strike=pe_strike,
                days_back=days_back  # Pass days_back parameter
            )
            
            return jsonify({
                'success': option_data.get('success', True),
                'data': option_data
            })
        
    except Exception as e:
        logger.error(f"Error in intraday_option endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/intraday-option/positions', methods=['GET'])
@csrf.exempt
@limiter.exempt
def get_intraday_positions() -> EndpointResponse:
    """
    Get current intraday option positions and P&L
    
    Returns:
        JSON with open positions, total P&L, and unrealised gains/losses
    """
    try:
        auth_error = check_auth()
        if auth_error:
            return auth_error
        
        # Get KiteConnect instance
        kite = get_kite()
        if not kite:
            return jsonify({
                'success': False,
                'error': 'Failed to initialize Kite connection'
            }), 500
        
        # Import and initialize trader
        from trading_app.app.intraday_option import IntradayOptionTrader
        trader = IntradayOptionTrader(kite)
        
        # Get positions
        positions_data = trader.get_position_info()
        
        return jsonify({
            'success': not positions_data.get('error'),
            'data': positions_data
        })
        
    except Exception as e:
        logger.error(f"Error in intraday_positions endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/intraday-option/multiple-strikes', methods=['GET'])
@csrf.exempt
@limiter.exempt
def get_intraday_multiple_strikes() -> EndpointResponse:
    """
    Get intraday option data for multiple strikes
    
    Query Parameters:
        symbol (str): Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
        strike_price (float): Central strike price
        num_strikes (int, optional): Number of strikes above/below central strike (default: 5)
        timeframe (str): Candle interval (5minute, 15minute, 30minute, 60minute)
        
    Returns:
        JSON with data for multiple strikes
    """
    try:
        auth_error = check_auth()
        if auth_error:
            return auth_error
        
        # Get parameters
        symbol = request.args.get('symbol', 'NIFTY').upper()
        strike_price = request.args.get('strike_price', type=float)
        num_strikes = request.args.get('num_strikes', type=int, default=5)
        timeframe = request.args.get('timeframe', '5minute')
        
        if not strike_price:
            return jsonify({
                'success': False,
                'error': 'strike_price parameter is required'
            }), 400
        
        # Get KiteConnect instance
        kite = get_kite()
        if not kite:
            return jsonify({
                'success': False,
                'error': 'Failed to initialize Kite connection'
            }), 500
        
        # Import and initialize trader
        from trading_app.app.intraday_option import IntradayOptionTrader
        trader = IntradayOptionTrader(kite)
        
        # Get multiple strikes data
        strikes_data = trader.get_multiple_strikes(symbol, strike_price, num_strikes)
        
        return jsonify({
            'success': not strikes_data.get('error'),
            'data': strikes_data
        })
        
    except Exception as e:
        logger.error(f"Error in multiple_strikes endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== INTRADAY 9:20 STRATEGY ENDPOINTS ====================

@api_bp.route('/intraday-920/data', methods=['GET'])
@api_bp.route('/intraday-920/symbol-payload', methods=['GET'])
@csrf.exempt
@limiter.exempt
def get_intraday_920_symbol_payload() -> EndpointResponse:
    """
    Get first 5-minute candle data and calculate strikes for Intraday 9:20 strategy
    Called before starting monitoring to display 9:20 strategy data
    
    Query Parameters:
        symbol (str): Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
        date (str, optional): Date in YYYY-MM-DD format to fetch data for that specific date.
                             If not provided, uses last trading day's data.
        
    Returns:
        JSON with first_5min_high, first_5min_low, high_strike, low_strike data
    """
    import time as time_module
    start_time = time_module.time()
    
    try:
        auth_error = check_auth()
        if auth_error:
            return auth_error
        
        # Get parameters
        symbol = request.args.get('symbol', 'NIFTY').upper()
        date_str = request.args.get('date')  # Format: YYYY-MM-DD or None
        
        # Validate symbol
        valid_symbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
        if symbol not in valid_symbols:
            return jsonify({
                'success': False,
                'error': f'Invalid symbol. Must be one of {valid_symbols}'
            }), 400
        
        # Check cache first (60 second TTL)
        from trading_app.app.utils.cache import CacheManager
        _intraday_920_cache = getattr(get_intraday_920_symbol_payload, '_cache', None)
        if _intraday_920_cache is None:
            _intraday_920_cache = CacheManager(ttl=60)
            get_intraday_920_symbol_payload._cache = _intraday_920_cache  # type: ignore[attr-defined]
        
        cache_key = f"intraday920:{symbol}:{date_str or 'today'}"
        cached = _intraday_920_cache.get(cache_key)
        if cached:
            elapsed = time_module.time() - start_time
            logger.info(f"✓ intraday-920/data cache hit for {symbol} in {elapsed:.3f}s")
            return jsonify(cached)
        
        # Get Data Provider instance (Kite or Fyers based on .env)
        kite = get_data_provider()
        if not kite:
            return jsonify({
                'success': False,
                'error': 'Failed to initialize data provider connection'
            }), 500
        
        # Import strategy
        from trading_app.app.intraday_option.intraday_9_20 import Intraday920Strategy
        strategy = Intraday920Strategy(kite)
        
        # Get strategy data (pass date if provided)
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d')
                payload = strategy.get_intraday_920_data(symbol, target_date=target_date)
            except ValueError:
                return jsonify({
                    'success': False,
                    'error': f'Invalid date format. Use YYYY-MM-DD'
                }), 400
        else:
            payload = strategy.get_intraday_920_data(symbol)
        
        elapsed = time_module.time() - start_time
        logger.info(f"✓ intraday-920/data for {symbol} completed in {elapsed:.2f}s")
        
        response = {
            'success': payload.get('success', False),
            'data': payload,
            'timestamp': datetime.now().isoformat(),
            'response_time_ms': int(elapsed * 1000)
        }
        
        # Cache successful responses
        if payload.get('success'):
            _intraday_920_cache.set(cache_key, response)
        
        return jsonify(response), 200 if payload.get('success') else 400
        
    except Exception as e:
        logger.error(f"Error in intraday-920 data endpoint: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500




@api_bp.route('/intraday-920/candles', methods=['GET'])
@csrf.exempt
@limiter.exempt 
def intraday_920_candles() -> EndpointResponse:
    """
    Get candlestick data for a token (CE or PE).
    
    Used for charting the high and low strike options.
    """
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    token = request.args.get('token', type=int)
    interval = request.args.get('interval', '5minute')
    days_back = request.args.get('days_back', 1, type=int)
    
    if not token:
        return jsonify({
            'success': False,
            'error': 'Token is required'
        }), 400
    
    try:
        current_kite = get_kite()
        if not current_kite:
            return jsonify({
                'success': False,
                'error': 'KiteConnect initialization failed'
            }), 401
        
        from trading_app.app.intraday_option.intraday_9_20 import Intraday920Strategy
        strategy = Intraday920Strategy(current_kite)
        
        data = strategy.get_candle_data(token, interval, days_back)
        
        return jsonify({
            'success': data.get('success', False),
            'data': data,
            'timestamp': datetime.now().isoformat()
        }), 200 if data.get('success') else 400
        
    except Exception as e:
        logger.error(f"Error in intraday_920_candles: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/intraday-920/backtest-full-day', methods=['POST'])
@csrf.exempt
@limiter.exempt
def backtest_intraday_920_full_day() -> EndpointResponse:
    """
    Run comprehensive backtest for entire trading day (9:20 to 3:20).
    Checks all 5-minute candles for entry and exit conditions.
    
    Entry Modes:
    - "candle_open": Entry when low < PDH AND close > (PDH + 5 points)
    - "high_cross": Entry when 5min high + 5 crosses (penetrates) the reference level
    
    POST Payload:
        {
            "symbol": "NIFTY",
            "ce_token": 12345678,           # CE option token
            "pe_token": 87654321,           # PE option token
            "ce_high": 24500.50,            # NIFTY's Previous Day High (PDH)
            "pe_high": 24500.50,            # NIFTY's Previous Day High (PDH)
            "ce_strike_price": 25100,       # Optional - CE strike price
            "pe_strike_price": 25000,       # Optional - PE strike price
            "date": "2026-01-12",           # Optional - date to backtest
            "risk_reward_ratio": "1:2-trail", # Optional - default "1:2-trail"
            "entry_mode": "candle_open"     # Optional - "candle_open" or "high_cross", default "candle_open"
        }
    
    Returns:
        {
            "success": true,
            "ce_analysis": { ... },
            "pe_analysis": { ... }
        }
    """
    try:
        auth_error = check_auth()
        if auth_error:
            return auth_error
        
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body must contain JSON'
            }), 400
        
        # Extract parameters
        symbol = data.get('symbol', 'NIFTY').upper()
        ce_token = data.get('ce_token')
        pe_token = data.get('pe_token')
        ce_high = data.get('ce_high')
        pe_high = data.get('pe_high')
        ce_strike_price = data.get('ce_strike_price')
        pe_strike_price = data.get('pe_strike_price')
        date_str = data.get('date')
        risk_reward_ratio = data.get('risk_reward_ratio', '1:2-trail')  # Default to 1:2 with trail SL
        entry_mode = data.get('entry_mode', 'candle_open')  # Default to candle_open
        
        # Validate entry_mode
        if entry_mode not in ['candle_open', 'high_cross']:
            return jsonify({
                'success': False,
                'error': 'entry_mode must be "candle_open" or "high_cross"'
            }), 400
        
        # Validate required fields
        if not all([ce_token, pe_token, ce_high, pe_high]):
            return jsonify({
                'success': False,
                'error': 'ce_token, pe_token, ce_high, and pe_high are required'
            }), 400
        
        # Parse date if provided
        target_date = None
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d')
                logger.info(f"Backtest requested for date: {target_date.date()}")
            except ValueError:
                return jsonify({
                    'success': False,
                    'error': 'Invalid date format. Use YYYY-MM-DD'
                }), 400
        else:
            logger.info("Backtest requested for current date")
        
        kite = get_kite()
        if not kite:
            return jsonify({
                'success': False,
                'error': 'Failed to initialize Kite connection'
            }), 500
        
        from trading_app.app.intraday_option.intraday_9_20 import Intraday920Strategy
        strategy = Intraday920Strategy(kite)
        
        # Run full day backtest with selected risk/reward ratio and entry mode
        results = strategy.backtest_full_day(
            ce_token=ce_token,
            pe_token=pe_token,
            ce_high=ce_high,
            pe_high=pe_high,
            symbol=symbol,
            target_date=target_date,
            risk_reward_ratio=risk_reward_ratio,
            entry_mode=entry_mode
        )
        
        if not results.get('success'):
            return jsonify({
                'success': False,
                'error': results.get('error', 'Backtest failed')
            }), 400
        
        # Add strike prices to results if provided
        ce_analysis = results.get('ce_analysis', {})
        pe_analysis = results.get('pe_analysis', {})
        
        if ce_strike_price:
            ce_analysis['strike_price'] = ce_strike_price
        if pe_strike_price:
            pe_analysis['strike_price'] = pe_strike_price
        
        return jsonify({
            'success': True,
            'ce_analysis': ce_analysis,
            'pe_analysis': pe_analysis,
            'symbol': results.get('symbol'),
            'date': results.get('date'),
            'timestamp': datetime.now().isoformat(),
            'note': 'Times in entry_time/exit_time are raw UTC Unix timestamps in seconds. Frontend converts to IST using Intl.DateTimeFormat.'
        }), 200
        
    except Exception as e:
        logger.error(f"Error in backtest-full-day endpoint: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500


@api_bp.route('/debug/token-status', methods=['GET'])
def debug_token_status() -> EndpointResponse:
    """Debug endpoint to check token validity.
    
    Returns token status information for debugging 403 errors.
    Helps identify if the issue is an expired token that requires re-login.
    """
    try:
        from kiteconnect import KiteConnect
        
        api_key = os.getenv('API_KEY')
        access_token = session.get('access_token') or os.getenv('ACCESS_TOKEN')
        
        if not api_key:
            return jsonify({
                'success': False,
                'error': 'API_KEY not configured',
                'token_status': 'UNCONFIGURED'
            }), 500
        
        if not access_token:
            return jsonify({
                'success': False,
                'error': 'No access token available - please login',
                'token_status': 'MISSING',
                'login_required': True
            }), 401
        
        # Try to initialize KiteConnect and fetch user profile to verify token
        kite = KiteConnect(api_key=api_key)
        apply_kite_proxy(kite)
        kite.set_access_token(access_token)
        
        try:
            # This call will fail if token is expired
            profile = kite.profile()
            
            return jsonify({
                'success': True,
                'token_status': 'VALID',
                'user_name': profile.get('user_name', 'Unknown') if isinstance(profile, dict) else 'Unknown',
                'user_shortname': profile.get('user_shortname', 'Unknown') if isinstance(profile, dict) else 'Unknown',
                'broker': profile.get('broker', 'Unknown') if isinstance(profile, dict) else 'Unknown',
                'access_token': f"{access_token[:20]}...{access_token[-5:]}"
            }), 200
            
        except Exception as e:
            error_str = str(e).lower()
            
            # Check if error indicates token is expired/invalid
            if 'invalid' in error_str or 'denied' in error_str or 'unauthorized' in error_str or '403' in error_str:
                logger.warning(f"Token validation failed - token appears expired or invalid: {e}")
                
                return jsonify({
                    'success': False,
                    'token_status': 'EXPIRED_OR_INVALID',
                    'error': 'Access token is expired or invalid - please login again',
                    'login_required': True,
                    'details': str(e)
                }), 401
            else:
                logger.error(f"Unexpected error checking token: {e}")
                
                return jsonify({
                    'success': False,
                    'token_status': 'ERROR',
                    'error': 'Error validating token',
                    'details': str(e)
                }), 500
    
    except Exception as e:
        logger.error(f"Error in debug token check: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error during token check',
            'details': str(e)
        }), 500
def split_quantity_by_freeze_limit(symbol: str, total_qty: int, provider) -> list:
    """Splits the quantity (in units/shares) into chunks of at most 27 lots."""
    import re
    tsym_upper = symbol.upper()
    underlying = 'NIFTY'
    if tsym_upper.startswith('BANKNIFTY'):
        underlying = 'BANKNIFTY'
    elif tsym_upper.startswith('FINNIFTY'):
        underlying = 'FINNIFTY'
    elif tsym_upper.startswith('MIDCPNIFTY'):
        underlying = 'MIDCPNIFTY'
    elif tsym_upper.startswith('SENSEX'):
        underlying = 'SENSEX'
    elif tsym_upper.startswith('NIFTY'):
        underlying = 'NIFTY'
    else:
        match = re.match(r'^([A-Z]+)', tsym_upper)
        if match:
            underlying = match.group(1)

    lot_size = 1
    if provider and hasattr(provider, 'get_lot_size'):
        try:
            lot_size = provider.get_lot_size(underlying)
        except Exception as e:
            logger.error(f"Error getting lot size for split from provider: {e}")

    if not lot_size or lot_size <= 1:
        if underlying == 'NIFTY': lot_size = 25
        elif underlying == 'BANKNIFTY': lot_size = 15
        elif underlying == 'SENSEX': lot_size = 20
        elif underlying == 'FINNIFTY': lot_size = 40
        elif underlying == 'MIDCPNIFTY': lot_size = 75
        else: lot_size = 1

    max_lots = 27
    max_qty = max_lots * lot_size

    if total_qty <= max_qty:
        return [total_qty]

    chunks = []
    remaining = total_qty
    while remaining > 0:
        chunk = min(remaining, max_qty)
        chunks.append(chunk)
        remaining -= chunk
    return chunks

@api_bp.route('/order/exit-all', methods=['POST'])
def exit_all_orders() -> EndpointResponse:
    """Exit all executed orders and cancel all pending orders for all active brokers."""
    try:
        _username = session.get('username', 'Mine')
        from trading_app.app.utils.user_env import UserEnvManager
        from trading_app.app.utils.order_tracker import BotOrderTracker

        try:
            from trading_app.app.intraday_option.intrinsic_order_manager import IntrinsicOrderManager
            IntrinsicOrderManager.stop_all_for_user(_username)
        except Exception as _stop_err:
            logger.warning(f"[Exit-All] Could not stop intrinsic monitors: {_stop_err}")

        targets = []
        for i in range(1, 21):
            b_type = UserEnvManager.get_user_var(_username, f'BROKER_{i}_TYPE', '').strip().lower()
            if not b_type:
                continue  # slot not configured
            if not is_broker_active(_username, i):
                logger.info(f"[Exit-All] Skipping broker {i} ({b_type}): Active=False")
                continue
            
            # Special handling for 'kite' which is an alias for 'zerodha_1'
            if b_type == 'kite':
                targets.append({'type': 'zerodha', 'instance': i})
            else:
                targets.append({'type': b_type, 'instance': i})

        logger.info(f"[Exit-All] Targeting {len(targets)} active broker instances for user '{_username}': {targets}")

        if not targets:
            return jsonify({'success': False, 'error': 'No active brokers found for exit'}), 400

        # Get symbols tracked by the bot for this user (global across accounts)
        bot_symbols = BotOrderTracker.get_symbols(_username)
        normalized_bot_symbols = [str(s).strip().upper() for s in bot_symbols] if bot_symbols else []
        logger.info(f"[Exit-All] Bot Registry Symbols: {normalized_bot_symbols}")

        exit_results = []
        
        for target in targets:
            broker_type = target['type']
            instance = target['instance']
            broker_res = {'broker': broker_type, 'instance': instance, 'cancelled_orders': 0, 'exited_positions': 0, 'errors': []}
            
            try:
                # 1. Handle Zerodha
                if broker_type == 'zerodha' or broker_type.startswith('zerodha_'):
                    kite = get_kite(instance=instance)
                    if not kite:
                        err_msg = f"Failed to initialize Zerodha instance {instance}. Check if API Key/Token is expired."
                        logger.error(f"[Zerodha] {err_msg}")
                        broker_res['errors'].append(err_msg)
                    else:
                        logger.info(f"[Zerodha] Starting exit-all for instance {instance}")
                        
                        try:
                            orders = kite.orders()
                            pending_statuses = ['OPEN', 'OPEN PENDING', 'MODIFY PENDING', 'TRIGGER PENDING', 'AMO REQ RECEIVED']
                            for order in orders:
                                if order['status'] in pending_statuses:
                                    tsym = str(order.get('tradingsymbol', '')).strip().upper()
                                    is_bot_tracked = (not normalized_bot_symbols) or (tsym in normalized_bot_symbols)
                                    
                                    if not is_bot_tracked:
                                        logger.info(f"[Zerodha] Instance {instance} | SKIPPING Cancellation {tsym}: Not in Bot Registry.")
                                        continue
                                    
                                    try:
                                        kite.cancel_order(variety=order['variety'], order_id=order['order_id'])
                                        broker_res['cancelled_orders'] += 1
                                    except Exception as e:
                                        broker_res['errors'].append(f"Cancel {order['order_id']} failed: {e}")
                        except Exception as e:
                            logger.error(f"[Zerodha] Error fetching orders: {e}")
                            broker_res['errors'].append(f"Error fetching orders: {e}")
                        
                        # Identify account for logging
                        try:
                            profile = kite.profile()
                            kite_user_id = profile.get('user_id', 'Unknown')
                            active_segments = profile.get('segments', [])
                            logger.info(f"[Zerodha] Account Verified: Instance {instance} (User ID: {kite_user_id}) | Active Segments: {active_segments} | Bot symbols: {bot_symbols}")
                            
                            # Check if NFO is in segments
                            if 'nse_fo' not in [s.lower() for s in active_segments] and 'nfo' not in [s.lower() for s in active_segments]:
                                logger.warning(f"[Zerodha] WARNING: Instance {instance} (User {kite_user_id}) does not seem to have F&O (NFO) segment active for API trading!")
                        except Exception as profile_err:
                            err_msg = f"Could not verify profile for Zerodha instance {instance}: {profile_err}"
                            logger.error(f"[Zerodha] {err_msg}")
                            broker_res['errors'].append(err_msg)
                            # We continue anyway as we have the kite instance, but this is a red flag
                            logger.info(f"[Zerodha] Instance {instance} (Profile Failed) | Bot symbols: {bot_symbols}")
                        
                        # Exit Positions
                        try:
                            pos_response = kite.positions()
                            net_positions = pos_response.get('net', [])
                            day_positions = pos_response.get('day', [])
                            
                            # Combine positions to ensure nothing is missed (unique by tradingsymbol)
                            all_positions = {p['tradingsymbol']: p for p in (day_positions + net_positions)}.values()
                            
                            logger.info(f"[Zerodha] Instance {instance} | Found {len(all_positions)} total unique positions.")
                        
                            # Log all found symbols for deep analysis
                            raw_syms = [p.get('tradingsymbol') for p in all_positions]
                            logger.info(f"[Zerodha] Instance {instance} | RAW POSITIONS: {raw_syms}")
                            logger.info(f"[Zerodha] Instance {instance} | BOT REGISTRY: {normalized_bot_symbols}")

                            from trading_app.service.kite_order_services import KiteService
                            kite_svc = KiteService(kite_instance=kite)

                            for pos in all_positions:
                                tsym = str(pos.get('tradingsymbol', '')).strip().upper()
                                qty = pos.get('quantity', 0)
                                product = str(pos.get('product', '')).strip().upper()
                                exchange = str(pos.get('exchange', '')).strip().upper()

                                logger.info(f"[Zerodha] Instance {instance} | Analyzing: {tsym} | Qty: {qty} | Product: {product} | Exchange: {exchange}")

                                # 1. Bot Tracking Check
                                # If registry is enabled, we only exit what's in the registry.
                                # If registry is empty, we exit everything (full liquidation mode).
                                is_bot_tracked = (not normalized_bot_symbols) or (tsym in normalized_bot_symbols)

                                if not is_bot_tracked:
                                    logger.info(f"[Zerodha] Instance {instance} | SKIPPING {tsym}: Not in Bot Registry.")
                                    continue

                                # 2. CNC Check
                                if product == 'CNC':
                                    logger.info(f"[Zerodha] Instance {instance} | SKIPPING {tsym}: CNC/Delivery position.")
                                    continue

                                if qty == 0:
                                    logger.info(f"[Zerodha] Instance {instance} | SKIPPING {tsym}: Zero quantity.")
                                    continue

                                logger.info(f"[Zerodha] Instance {instance} | TARGET FOUND: {tsym} | Qty: {qty}")

                                try:
                                    side = 'SELL' if qty > 0 else 'BUY'
                                    abs_qty = abs(int(qty))
                                    qty_chunks = split_quantity_by_freeze_limit(pos['tradingsymbol'], abs_qty, kite)
                                    logger.info(f"[Zerodha] Exiting {pos['tradingsymbol']}: Total quantity {abs_qty} (Side: {side}, Product: {pos['product']}) split into chunks: {qty_chunks}")

                                    for chunk_qty in qty_chunks:
                                        order_id = kite_svc._safe_place_order(
                                            variety='regular',
                                            exchange=pos['exchange'],
                                            tradingsymbol=pos['tradingsymbol'],
                                            transaction_type=side,
                                            quantity=chunk_qty,
                                            order_type='MARKET',
                                            product=pos['product'],
                                            market_protection=-1
                                        )
                                        logger.info(f"[Zerodha] Instance {instance} | Exit MARKET order placed with protection: {order_id} (Qty: {chunk_qty})")
                                    broker_res['exited_positions'] += 1

                                except Exception as e:
                                    logger.error(f"[Zerodha] Position exit failed for {pos['tradingsymbol']}: {e}")
                                    broker_res['errors'].append(f"Exit {pos['tradingsymbol']} failed: {e}")
                            
                        except Exception as e:
                            logger.error(f"[Zerodha] Error fetching positions: {e}")
                            broker_res['errors'].append(f"Error fetching positions: {e}")

                # 2. Handle Fyers
                elif broker_type == 'fyers':
                    fyers_at = session.get(f'fyers_{instance}_access_token') or UserEnvManager.get_user_var(_username, f'BROKER_{instance}_ACCESS_TOKEN')
                    fyers_id = UserEnvManager.get_user_var(_username, f'BROKER_{instance}_APP_ID')
                    if fyers_at:
                        from trading_app.service.fyers_order_services import FyersOrderService
                        fyers_service = FyersOrderService(app_id=fyers_id, access_token=fyers_at)
                        
                        # Cancel Pending Orders
                        order_book = fyers_service.get_orderbook()
                        if order_book.get('success'):
                            # Fyers V3 Status: 6=Pending, 4=Transit, 1=Cancelled, 2=Filled, 5=Rejected
                            for order in order_book.get('orders', []):
                                if order.get('status') in [6, 4]: 
                                    fsym = str(order.get('symbol', '')).strip().upper()
                                    alt_sym = fsym.split(':')[-1] if ':' in fsym else fsym
                                    is_bot_tracked = (not normalized_bot_symbols) or (fsym in normalized_bot_symbols) or (alt_sym in normalized_bot_symbols)
                                    
                                    if not is_bot_tracked:
                                        logger.info(f"[Fyers] Skipping Cancellation for non-bot order: {fsym}")
                                        continue
                                        
                                    try:
                                        fyers_service.cancel_order(order['id'])
                                        broker_res['cancelled_orders'] += 1
                                    except Exception as e:
                                        broker_res['errors'].append(f"Cancel {order['id']} failed: {e}")
                        
                        # Exit Positions
                        pos_book = fyers_service.get_positions()
                        if pos_book.get('success'):
                            for pos in pos_book.get('positions', []):
                                fsym = str(pos.get('symbol', '')).strip().upper()
                                alt_sym = fsym.split(':')[-1] if ':' in fsym else fsym
                                net_qty = pos.get('netQty', 0)
                                product = pos.get('productType', '')

                                # Skip if not bot-tracked
                                is_bot_tracked = (not normalized_bot_symbols) or (fsym in normalized_bot_symbols) or (alt_sym in normalized_bot_symbols)
                                if not is_bot_tracked:
                                    logger.info(f"[Fyers] Skipping non-bot position: {fsym}")
                                    continue

                                # Skip CNC positions
                                if product == 'CNC':
                                    logger.info(f"[Fyers] Skipping CNC position {fsym}")
                                    continue

                                # Skip Equity positions (usually end with -EQ in Fyers)
                                if '-EQ' in fsym:
                                    logger.info(f"[Fyers] Skipping Equity position {fsym}")
                                    continue

                                if net_qty == 0:
                                    continue

                                try:
                                    # Use correct Fyers side: 1=BUY, 2=SELL
                                    side = fyers_service.SIDE_SELL if net_qty > 0 else fyers_service.SIDE_BUY

                                    abs_qty = abs(int(net_qty))
                                    qty_chunks = split_quantity_by_freeze_limit(fsym, abs_qty, fyers_service)
                                    logger.info(f"[Fyers] Exiting {fsym}: Total quantity {abs_qty} split into chunks: {qty_chunks}")

                                    for chunk_qty in qty_chunks:
                                        fyers_service.place_order(
                                            symbol=pos['symbol'],
                                            side=side,
                                            quantity=chunk_qty,
                                            order_type=2,
                                            product_type=pos['productType']
                                        )
                                    broker_res['exited_positions'] += 1
                                except Exception as e:
                                    broker_res['errors'].append(f"Exit {fsym} failed: {e}")

                # 3. Handle Kotak Neo
                elif broker_type == 'kotak':
                    kotak_at = session.get(f'kotak_{instance}_access_token') or UserEnvManager.get_user_var(_username, f'BROKER_{instance}_ACCESS_TOKEN')
                    if kotak_at:
                        from trading_app.service.kotak_order_services import KotakOrderService
                        kotak_service = KotakOrderService(access_token=kotak_at)
                        # Re-inject cached trading tokens
                        kotak_service.trading_token = UserEnvManager.get_user_var(_username, f'BROKER_{instance}_TRADING_TOKEN')
                        kotak_service.trading_sid = UserEnvManager.get_user_var(_username, f'BROKER_{instance}_TRADING_SID')
                        kotak_service.server_id = UserEnvManager.get_user_var(_username, f'BROKER_{instance}_SERVER_ID')
                        kotak_service.inject_trading_tokens()

                        # Cancel Pending Orders
                        order_book = kotak_service.get_orderbook()
                        if order_book.get('success'):
                            for order in order_book.get('orders', []):
                                if order.get('ordSt') in ['open', 'pending', 'modify', 'trigger_pending']:
                                    ksym = str(order.get('trdSym', '')).strip().upper()
                                    is_bot_tracked = (not normalized_bot_symbols) or (ksym in normalized_bot_symbols)
                                    
                                    if not is_bot_tracked:
                                        logger.info(f"[Kotak] Skipping Cancellation for non-bot order: {ksym}")
                                        continue
                                        
                                    try:
                                        kotak_service.cancel_order(order.get('nOrdNo'))
                                        broker_res['cancelled_orders'] += 1
                                    except Exception as e:
                                        broker_res['errors'].append(f"Cancel {order.get('nOrdNo')} failed: {e}")
                        
                        # Exit Positions
                        pos_book = kotak_service.get_positions()
                        if pos_book.get('success'):
                            for pos in pos_book.get('positions', []):
                                ksym = str(pos.get('trdSym', '')).strip().upper()
                                net_qty = float(pos.get('flNetQty', pos.get('netQty', 0)))
                                product = pos.get('prod', '')

                                # Skip if not bot-tracked
                                is_bot_tracked = (not normalized_bot_symbols) or (ksym in normalized_bot_symbols)
                                if not is_bot_tracked:
                                    logger.info(f"[Kotak] Skipping non-bot position: {ksym}")
                                    continue

                                # Skip CNC positions
                                if product == 'CNC':
                                    logger.info(f"[Kotak] Skipping CNC position {ksym}")
                                    continue

                                # Target only F&O segments (nse_fo, bse_fo, mcx_fo)
                                exseg = str(pos.get('exseg', '')).lower()
                                if 'fo' not in exseg:
                                    logger.info(f"[Kotak] Skipping non-F&O position {ksym} ({exseg})")
                                    continue

                                if net_qty == 0:
                                    continue

                                try:
                                    side = 'SELL' if net_qty > 0 else 'BUY'

                                    abs_qty = abs(int(net_qty))
                                    qty_chunks = split_quantity_by_freeze_limit(ksym, abs_qty, kotak_service)
                                    logger.info(f"[Kotak] Exiting {ksym}: Total quantity {abs_qty} split into chunks: {qty_chunks}")

                                    for chunk_qty in qty_chunks:
                                        kotak_service.place_order(
                                            tradingsymbol=pos['trdSym'],
                                            transaction_type=side,
                                            quantity=chunk_qty,
                                            price=0.0,
                                            order_type='MKT',
                                            product_type=pos['prod'],
                                            exchange_segment=pos['exseg']
                                        )
                                    broker_res['exited_positions'] += 1
                                except Exception as e:
                                    broker_res['errors'].append(f"Exit {ksym} failed: {e}")

                # 4. Handle Dhan
                elif broker_type == 'dhan':
                    dhan_at = session.get(f'dhan_{instance}_access_token') or UserEnvManager.get_user_var(_username, f'BROKER_{instance}_ACCESS_TOKEN')
                    dhan_cid = UserEnvManager.get_user_var(_username, f'BROKER_{instance}_CLIENT_ID')
                    if dhan_at:
                        from trading_app.service.dhan_order_services import DhanOrderService
                        dhan_service = DhanOrderService(access_token=dhan_at, client_id=dhan_cid)
                        
                        # Cancel Pending Orders
                        order_book = dhan_service.get_order_book()
                        
                        if order_book.get('success'):
                            for order in order_book.get('orders', []):
                                if order.get('orderStatus') in ['PENDING', 'TRANSIT', 'MODIFY_PENDING']:
                                    dsym = str(order.get('tradingSymbol', '')).strip().upper()
                                    is_bot_tracked = (not normalized_bot_symbols) or (dsym in normalized_bot_symbols)
                                    
                                    if not is_bot_tracked:
                                        logger.info(f"[Dhan] Skipping Cancellation for non-bot order: {dsym}")
                                        continue
                                        
                                    try:
                                        dhan_service.cancel_order(order['orderId'])
                                        broker_res['cancelled_orders'] += 1
                                    except Exception as e:
                                        broker_res['errors'].append(f"Cancel {order['orderId']} failed: {e}")
                        
                        # Exit Positions
                        pos_book = dhan_service.get_positions()
                        if pos_book.get('success'):
                            for pos in pos_book.get('positions', []):
                                dsym = str(pos.get('tradingSymbol', '')).strip().upper()
                                net_qty = pos.get('netQty', 0)
                                product = pos.get('productType', '')

                                # Skip if not bot-tracked
                                is_bot_tracked = (not normalized_bot_symbols) or (dsym in normalized_bot_symbols)
                                if not is_bot_tracked:
                                    logger.info(f"[Dhan] Skipping non-bot position: {dsym}")
                                    continue

                                # Skip CNC positions
                                if product == 'CNC':
                                    logger.info(f"[Dhan] Skipping CNC position {dsym}")
                                    continue

                                # Target only F&O segments
                                exseg = str(pos.get('exchangeSegment', '')).upper()
                                if 'FNO' not in exseg and 'COMM' not in exseg and 'CURR' not in exseg:
                                    logger.info(f"[Dhan] Skipping non-Derivatives position {dsym} ({exseg})")
                                    continue

                                if net_qty == 0:
                                    continue

                                try:
                                    side = 'SELL' if net_qty > 0 else 'BUY'

                                    abs_qty = abs(int(net_qty))
                                    qty_chunks = split_quantity_by_freeze_limit(dsym, abs_qty, dhan_service)
                                    logger.info(f"[Dhan] Exiting {dsym}: Total quantity {abs_qty} split into chunks: {qty_chunks}")

                                    for chunk_qty in qty_chunks:
                                        dhan_service.place_order(
                                            security_id=pos['securityId'],
                                            transaction_type=side,
                                            quantity=chunk_qty,
                                            order_type='MARKET',
                                            product_type=pos['productType'],
                                            exchange_segment=pos['exchangeSegment']
                                        )
                                    broker_res['exited_positions'] += 1
                                except Exception as e:
                                    broker_res['errors'].append(f"Exit {dsym} failed: {e}")

            except Exception as broker_err:
                broker_res['errors'].append(str(broker_err))
                logger.error(f"Exit-All failed for {broker_type}_{instance}: {broker_err}")
                
            exit_results.append({
                'broker': broker_type,
                'instance': instance,
                'cancelled_orders': broker_res['cancelled_orders'],
                'exited_positions': broker_res['exited_positions'],
                'errors': broker_res['errors']
            })

        return jsonify({
            'success': True,
            'summary': exit_results
        })

    except Exception as e:
        logger.error(f"Global Exit-All failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/order/place-sl', methods=['POST'])
def place_sl_order() -> EndpointResponse:
    """Place SL-Market order for a single option leg across all active brokers."""
    _LOT_MAP = {'NIFTY': 25, 'BANKNIFTY': 15, 'SENSEX': 20, 'FINNIFTY': 40, 'MIDCPNIFTY': 75}
    try:
        _username = session.get('username', 'Mine')
        data = request.get_json() or {}
        symbol        = (data.get('symbol') or 'NIFTY').strip().upper()
        strike        = int(data.get('strike', 0))
        option_type   = (data.get('option_type') or '').strip().upper()
        trigger_price = float(data.get('trigger_price', 0))

        if option_type not in ('CE', 'PE') or strike <= 0 or trigger_price <= 0:
            return jsonify({'success': False, 'error': 'Invalid parameters: need option_type CE/PE, strike > 0, trigger_price > 0'}), 400

        from trading_app.app.utils.user_env import UserEnvManager
        from trading_app.service.kite_order_services import KiteService

        standard_lot = _LOT_MAP.get(symbol, 1)
        results = []

        for i in range(1, 21):
            b_type = UserEnvManager.get_user_var(_username, f'BROKER_{i}_TYPE', '').strip().lower()
            if not b_type or not is_broker_active(_username, i):
                continue

            if b_type in ('kite', 'zerodha'):
                kite = get_kite(instance=i)
                if not kite:
                    results.append({'broker': 'zerodha', 'instance': i, 'success': False, 'error': 'Kite init failed'})
                    continue
                try:
                    svc = KiteService(kite_instance=kite)
                    lot_qty = get_broker_lot_size(_username, i, standard_lot)
                    tradingsymbol = svc.get_option_symbol(symbol, strike, option_type)
                    if not tradingsymbol:
                        results.append({'broker': 'zerodha', 'instance': i, 'success': False,
                                        'error': f'Could not resolve tradingsymbol for {symbol} {strike} {option_type}'})
                        continue
                    r = svc.place_stoploss_order(tradingsymbol=tradingsymbol,
                                                  trigger_price=trigger_price,
                                                  quantity=lot_qty,
                                                  transaction_type='SELL')
                    results.append({'broker': 'zerodha', 'instance': i, **r})
                except Exception as e:
                    logger.error(f'[place-sl] zerodha_{i} error: {e}')
                    results.append({'broker': 'zerodha', 'instance': i, 'success': False, 'error': str(e)})

            elif b_type == 'fyers':
                try:
                    from trading_app.service.fyers_order_services import FyersOrderService
                    fyers_at = session.get(f'fyers_{i}_access_token') or \
                               UserEnvManager.get_user_var(_username, f'BROKER_{i}_ACCESS_TOKEN')
                    fyers_id = UserEnvManager.get_user_var(_username, f'BROKER_{i}_APP_ID')
                    if not fyers_at:
                        results.append({'broker': 'fyers', 'instance': i, 'success': False, 'error': 'No access token'})
                        continue
                    fyers_svc = FyersOrderService(app_id=fyers_id, access_token=fyers_at)
                    lot_qty = get_broker_lot_size(_username, i, standard_lot)
                    fyers_sym = fyers_svc.get_option_symbol(symbol, strike, option_type) if hasattr(fyers_svc, 'get_option_symbol') else None
                    if not fyers_sym:
                        results.append({'broker': 'fyers', 'instance': i, 'success': False, 'error': 'Symbol resolution failed'})
                        continue
                    r = fyers_svc.place_stoploss_order(symbol=fyers_sym, trigger_price=trigger_price,
                                                        quantity=lot_qty, transaction_type='SELL')
                    results.append({'broker': 'fyers', 'instance': i, **r})
                except Exception as e:
                    logger.error(f'[place-sl] fyers_{i} error: {e}')
                    results.append({'broker': 'fyers', 'instance': i, 'success': False, 'error': str(e)})

        if not results:
            return jsonify({'success': False, 'error': 'No active brokers found'}), 400

        overall = any(r.get('success') for r in results)
        return jsonify({'success': overall, 'results': results})

    except Exception as e:
        logger.error(f'[place-sl] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/orders/place', methods=['POST'])
def place_order_unified() -> EndpointResponse:
    """Broker mode: dispatch order (MARKET or LIMIT) to all active brokers and save record to server JSON."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        required = ['symbol', 'strike', 'option_type', 'action']
        for field in required:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400

        symbol       = data['symbol'].upper()
        strike       = int(data['strike'])
        option_type  = data['option_type'].upper()
        action       = data['action'].upper()
        strategy     = data.get('strategy', '')
        order_type   = data.get('order_type', 'MARKET').upper()
        limit_price  = data.get('limit_price')
        username     = session.get('username', 'Mine')

        if order_type == 'LIMIT' and not limit_price:
            return jsonify({'success': False, 'error': 'limit_price required for LIMIT order'}), 400

        result = _dispatch_order_to_brokers(
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            action=action,
            strategy=strategy,
            username=username,
            session_data=dict(session),
            quantity=data.get('quantity'),
            tradingsymbol_override=data.get('tradingsymbol'),
            expiry_override=data.get('expiry'),
            limit_price=float(limit_price) if limit_price else None,
            sec_id=data.get('sec_id'),
        )

        from trading_app.app.utils.mine_order_store import MineOrderStore
        MineOrderStore.add_order({
            'mode': 'broker',
            'symbol': symbol,
            'strike': strike,
            'option_type': option_type,
            'action': action,
            'strategy': strategy,
            'order_type': order_type,
            'type': order_type,
            'instrument': 'NFO',
            'price': float(limit_price or 0),
            'status': 'EXECUTED' if result.get('success') else 'REJECTED',
            'username': username,
            'executed_at': int(_time.time() * 1000) if result.get('success') else None,
            'broker_order_ids': result.get('summary', []),
        })

        return jsonify(result), (200 if result.get('success') else 400)

    except Exception as e:
        logger.error(f"[orders/place] Error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/orders', methods=['GET'])
@csrf.exempt
def list_all_orders() -> EndpointResponse:
    """Return all orders (broker + mine) from the server JSON store."""
    try:
        from trading_app.app.utils.mine_order_store import MineOrderStore
        history = request.args.get('history', '0') == '1'
        orders = MineOrderStore.get_all_orders() if history else MineOrderStore.get_today_orders()
        return jsonify({'success': True, 'orders': orders})
    except Exception as e:
        logger.error(f"[orders/list] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/orders/<order_id>', methods=['DELETE'])
def delete_order_record(order_id: str) -> EndpointResponse:
    """Cancel/remove an order from the server JSON store.
    For pending Mine orders the backend monitor will stop tracking them.
    For placed Broker orders this removes them from our display only.
    """
    try:
        from trading_app.app.utils.mine_order_store import MineOrderStore
        found = MineOrderStore.cancel_order(order_id)
        if found:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Order not found'}), 404
    except Exception as e:
        logger.error(f"[orders/delete] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/orders/<order_id>/price', methods=['PUT'])
def update_order_price(order_id: str) -> EndpointResponse:
    """Update the limit price of a pending Mine order."""
    try:
        data = request.get_json()
        new_price = float(data.get('price', 0)) if data else 0
        if new_price <= 0:
            return jsonify({'success': False, 'error': 'Invalid price'}), 400
        from trading_app.app.utils.mine_order_store import MineOrderStore
        found = MineOrderStore.update_price(order_id, new_price)
        if found:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Order not found or not pending'}), 404
    except Exception as e:
        logger.error(f"[orders/price] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/orders/cancel', methods=['DELETE'])
def cancel_broker_order() -> EndpointResponse:
    """Cancel a broker order by order_id. For PENDING limit orders never sent to brokers, call with empty list."""
    try:
        data = request.get_json()
        broker_order_ids = data.get('broker_order_ids', []) if data else []
        _username = session.get('username', 'Mine')

        if not broker_order_ids:
            return jsonify({'success': True, 'cancelled': [], 'failed': []})

        from trading_app.app.utils.user_env import UserEnvManager

        cancelled = []
        failed = []

        for entry in broker_order_ids:
            broker_type = entry.get('broker_type', '')
            instance = entry.get('instance')
            order_id = entry.get('order_id')
            if not order_id:
                continue

            try:
                if broker_type.startswith('zerodha'):
                    kite = get_kite(instance=instance)
                    if not kite:
                        failed.append({'order_id': order_id, 'error': 'Kite not connected'})
                        continue
                    kite.cancel_order(variety='regular', order_id=str(order_id))
                    cancelled.append(order_id)

                elif broker_type == 'fyers':
                    fyers_at = session.get(f'fyers_{instance}_access_token') or UserEnvManager.get_user_var(_username, f'BROKER_{instance}_ACCESS_TOKEN')
                    fyers_id = UserEnvManager.get_user_var(_username, f'BROKER_{instance}_APP_ID')
                    if not fyers_at:
                        failed.append({'order_id': order_id, 'error': 'Fyers not authenticated'})
                        continue
                    from trading_app.service.fyers_order_services import FyersOrderService
                    fyers_service = FyersOrderService(app_id=fyers_id, access_token=fyers_at)
                    fyers_service.cancel_order(order_id=str(order_id))
                    cancelled.append(order_id)

                elif broker_type == 'dhan':
                    dhan_at = session.get(f'dhan_{instance}_access_token') or UserEnvManager.get_user_var(_username, f'BROKER_{instance}_ACCESS_TOKEN')
                    dhan_cid = UserEnvManager.get_user_var(_username, f'BROKER_{instance}_CLIENT_ID')
                    if not dhan_at:
                        failed.append({'order_id': order_id, 'error': 'Dhan not authenticated'})
                        continue
                    from trading_app.service.dhan_order_services import DhanOrderService
                    dhan_service = DhanOrderService(access_token=dhan_at, client_id=dhan_cid)
                    dhan_service.cancel_order(order_id=str(order_id))
                    cancelled.append(order_id)

                elif broker_type == 'kotak_neo':
                    trading_token = session.get(f'kotak_{instance}_trading_token') or UserEnvManager.get_user_var(_username, f'BROKER_{instance}_TRADING_TOKEN')
                    trading_sid = session.get(f'kotak_{instance}_trading_sid') or UserEnvManager.get_user_var(_username, f'BROKER_{instance}_TRADING_SID')
                    base_url = UserEnvManager.get_user_var(_username, f'BROKER_{instance}_BASE_URL') or 'https://gw-napi.kotaksecurities.com'
                    if not trading_token:
                        failed.append({'order_id': order_id, 'error': 'Kotak not authenticated'})
                        continue
                    from trading_app.service.kotak_order_services import KotakOrderService
                    kotak_service = KotakOrderService(access_token=trading_token)
                    kotak_service.trading_token = trading_token
                    kotak_service.trading_sid = trading_sid
                    kotak_service.base_url = base_url
                    kotak_service.inject_trading_tokens()
                    kotak_service.cancel_order(order_id=str(order_id))
                    cancelled.append(order_id)

                else:
                    failed.append({'order_id': order_id, 'error': f'Unknown broker type: {broker_type}'})

            except Exception as e:
                logger.error(f"[orders/cancel] Cancel failed for {order_id} on {broker_type}: {e}")
                failed.append({'order_id': order_id, 'error': str(e)})

        return jsonify({'success': True, 'cancelled': cancelled, 'failed': failed})

    except Exception as e:
        logger.error(f"[orders/cancel] Error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def _dispatch_order_to_brokers(symbol, strike, option_type, action, strategy, username, session_data,
                               quantity=None, tradingsymbol_override=None, expiry_override=None,
                               limit_price=None, sec_id=None):
    """Dispatch an order to all configured brokers. Safe to call from background threads.

    session_data: pass dict(session) from a route, or {} from a background thread (falls back to UserEnvManager).
    Returns a plain dict (not a Flask Response).
    """
    from trading_app.app.utils.user_env import UserEnvManager

    targets = []
    for i in range(1, 21):
        b_type = UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower()
        if not b_type:
            continue
        if not is_broker_active(username, i):
            logger.info(f"[order-dispatch] Skipping broker {i} ({b_type}): ACTIVE=false")
            continue

        if strategy == 'intrinsic':
            raw_active = UserEnvManager.get_user_var(username, f'BROKER_{i}_INTRINSIC_ACTIVE', 'false').strip().lower()
            if raw_active not in ('true', '1', 'yes'):
                logger.info(f"[order-dispatch] Skipping broker {i} ({b_type}): INTRINSIC_ACTIVE not enabled")
                continue
        else:
            raw_920 = UserEnvManager.get_user_var(username, f'BROKER_{i}_920_ACTIVE', 'false').strip().lower()
            if raw_920 not in ('true', '1', 'yes'):
                logger.info(f"[order-dispatch] Skipping broker {i} ({b_type}): 920_ACTIVE not enabled")
                continue

        if b_type == 'zerodha':
            targets.append({'type': f'zerodha_{i}', 'instance': i})
        elif b_type in ['kotak', 'kotak_neo']:
            targets.append({'type': 'kotak_neo', 'instance': i})
        elif b_type == 'dhan':
            targets.append({'type': 'dhan', 'instance': i})
        elif b_type == 'fyers':
            targets.append({'type': 'fyers', 'instance': i})

    logger.info(f"[order-dispatch] Routing to brokers: {[t['type'] for t in targets]} for user '{username}'")

    if not targets:
        if strategy == 'intrinsic':
            return {'success': False, 'error': 'No Intrinsic-enabled broker found. Set BROKER_N_INTRINSIC_ACTIVE=true in .env'}
        else:
            return {'success': False, 'error': 'No 9:20-enabled broker found. Set BROKER_N_920_ACTIVE=true in .env'}

    def _execute_single(broker, _active_instance):
        is_zerodha_instance = (broker == 'zerodha' or broker.startswith('zerodha_'))

        if _active_instance is not None and not is_broker_active(username, _active_instance):
            return {'success': False, 'error': f'Broker {broker} is INACTIVE'}, 403

        if strategy == 'intrinsic':
            intrinsic_active = UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_INTRINSIC_ACTIVE', 'true').lower().strip() == 'true'
            if not intrinsic_active:
                logger.info(f"[Intrinsic] Skipping broker {_active_instance} ({broker}) because BROKER_N_INTRINSIC_ACTIVE is FALSE")
                return {'success': False, 'error': f'Intrinsic Orders for {broker} is DISABLED in config'}, 403

        order_lots = quantity
        if strategy == 'intrinsic' and not order_lots:
            symbol_upper = symbol.upper()
            raw_sym_intrinsic = UserEnvManager.get_user_var(username, f'INTRINSIC_{symbol_upper}_LOTS')
            if raw_sym_intrinsic:
                try: order_lots = int(str(raw_sym_intrinsic))
                except: pass

            if not order_lots:
                raw_broker_intrinsic = UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_INTRINSIC_LOTS')
                if raw_broker_intrinsic:
                    try: order_lots = int(str(raw_broker_intrinsic))
                    except: pass

                if not order_lots:
                    raw_intrinsic = UserEnvManager.get_user_var(username, 'INTRINSIC_ORDER_LOTS')
                    if raw_intrinsic:
                        try: order_lots = int(str(raw_intrinsic))
                        except: pass

        if not order_lots:
            raw_broker_lot = UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_LOT_SIZE')
            if raw_broker_lot:
                try: order_lots = int(str(raw_broker_lot))
                except: order_lots = 1
            else:
                order_lots = 1

        lot_size = 1
        try:
            current_provider = get_data_provider()
            if current_provider:
                if hasattr(current_provider, 'get_lot_size'):
                    lot_size = current_provider.get_lot_size(symbol)
                else:
                    from trading_app.service.kite_order_services import KiteService
                    ks = KiteService(kite_instance=current_provider)
                    lot_size = ks.get_lot_size(symbol)

            if not lot_size or lot_size <= 1:
                s_upper = symbol.upper()
                if s_upper == 'NIFTY': lot_size = 25
                elif s_upper == 'BANKNIFTY': lot_size = 15
                elif s_upper == 'SENSEX': lot_size = 20
                elif s_upper == 'FINNIFTY': lot_size = 40
                elif s_upper == 'MIDCPNIFTY': lot_size = 75
                else: lot_size = 1
        except Exception as e:
            logger.error(f"[Order] Lot size resolution failed for {symbol}: {e}")
            lot_size = 1

        main_qty = order_lots * lot_size
        sl_qty = main_qty
        sl_transaction_type = 'SELL' if action == 'BUY' else 'BUY'

        if broker == 'kite' or is_zerodha_instance:
            zerodha_instance = _active_instance
            kite = get_kite(instance=zerodha_instance)
            if not kite:
                return {'success': False, 'error': f'Zerodha {zerodha_instance} not connected'}, 401
            # Apply static IP proxy if configured
            if os.getenv('STATIC_IP_KEY', '').strip():
                from trading_app.service.kite_order_services import apply_kite_proxy
                apply_kite_proxy(kite)

            from trading_app.service.kite_order_services import KiteService
            kite_service = KiteService(kite_instance=kite)
            transaction_type = kite.TRANSACTION_TYPE_BUY if action == 'BUY' else kite.TRANSACTION_TYPE_SELL

            result = kite_service.place_option_order(symbol=symbol, strike=strike, option_type=option_type, transaction_type=transaction_type, quantity=main_qty, price=limit_price)
            if result['success']:
                from trading_app.app.utils.order_tracker import BotOrderTracker
                opt_symbol = kite_service.get_option_symbol(symbol, strike, option_type)
                if opt_symbol:
                    BotOrderTracker.add_symbol(username, opt_symbol)

                if action == 'BUY':
                    try:
                        entry_price = result.get('price')
                        if entry_price and entry_price > 0:
                            option_symbol = kite_service.get_option_symbol(symbol, strike, option_type)
                            sl_order_ids = []
                            if strategy != 'intrinsic':
                                sl_price = entry_price - 20
                                if option_symbol:
                                    sl_res = kite_service.place_stoploss_order(tradingsymbol=option_symbol, trigger_price=sl_price, quantity=sl_qty, transaction_type=sl_transaction_type)
                                    if sl_res.get('success'):
                                        sl_order_ids = [sl_res.get('order_id')]
                                        result.update({'sl_order_id': sl_res.get('order_id'), 'sl_trigger_price': sl_price, 'sl_success': True})

                            if strategy == 'intrinsic':
                                from trading_app.app.intraday_option.intrinsic_order_manager import IntrinsicOrderManager
                                IntrinsicOrderManager.start_monitoring(broker, _active_instance, symbol, strike, option_type, entry_price, sl_order_ids, lot_size, order_lots, None, option_symbol, username, session_data)
                    except Exception as e: logger.error(f"[SL] Kite Err: {e}")
            return result, (200 if result['success'] else 400)

        elif broker == 'kotak_neo':
            from trading_app.service.kotak_order_services import KotakOrderService
            trading_token = session_data.get(f'kotak_{_active_instance}_trading_token') or UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_TRADING_TOKEN')
            trading_sid = session_data.get(f'kotak_{_active_instance}_trading_sid') or UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_TRADING_SID')
            base_url = session_data.get(f'kotak_{_active_instance}_base_url') or UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_BASE_URL') or "https://gw-napi.kotaksecurities.com"
            if not trading_token: return {'success': False, 'error': 'Kotak not authenticated'}, 401

            kotak_service = KotakOrderService(access_token=trading_token)
            kotak_service.trading_token = trading_token; kotak_service.trading_sid = trading_sid; kotak_service.base_url = base_url
            kotak_service.inject_trading_tokens()

            result = kotak_service.place_option_order(symbol=symbol, strike=strike, option_type=option_type, transaction_type=action, quantity=main_qty, tradingsymbol=tradingsymbol_override, target_expiry=expiry_override, limit_price=limit_price)
            if result['success']:
                from trading_app.app.utils.order_tracker import BotOrderTracker
                k_symbol = result.get('successful_symbol') or tradingsymbol_override
                if k_symbol:
                    BotOrderTracker.add_symbol(username, k_symbol)

                if action == 'BUY':
                    try:
                        entry_price = result.get('price', 0)
                        if not entry_price or entry_price == 0:
                            kite = get_kite()
                            try:
                                from trading_app.service.kite_order_services import KiteService
                                if kite:
                                    ks = KiteService(kite_instance=kite)
                                    kite_opt_sym = ks.get_option_symbol(symbol, strike, option_type)
                                    ltp_data = kite.ltp([f'NSE:{kite_opt_sym}'])
                                    entry_price = ltp_data.get(f'NSE:{kite_opt_sym}', {}).get('last_price', 0)
                            except: pass
                        if entry_price and entry_price > 0:
                            k_symbol = tradingsymbol_override
                            sl_order_ids = []
                            if strategy != 'intrinsic':
                                if k_symbol:
                                    sl_price = entry_price - 20
                                    sl_res = kotak_service.place_stoploss_order(symbol=k_symbol, trigger_price=sl_price, quantity=sl_qty, transaction_type=sl_transaction_type)
                                    if sl_res.get('success'):
                                        sl_order_ids = [sl_res.get('order_id')]
                                        result.update({'sl_order_id': sl_res.get('order_id'), 'sl_trigger_price': sl_price, 'sl_success': True})

                            if strategy == 'intrinsic':
                                from trading_app.app.intraday_option.intrinsic_order_manager import IntrinsicOrderManager
                                IntrinsicOrderManager.start_monitoring(broker, _active_instance, symbol, strike, option_type, entry_price, sl_order_ids, lot_size, order_lots, None, k_symbol, username, session_data)
                    except Exception as e: logger.error(f"[SL] Kotak Err: {e}")
            return result, (200 if result['success'] else 400)

        elif broker == 'dhan':
            from trading_app.service.dhan_order_services import DhanOrderService
            dhan_access_token = session_data.get(f'dhan_{_active_instance}_access_token') or UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_ACCESS_TOKEN')
            dhan_client_id = UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_CLIENT_ID')
            if not dhan_access_token: return {'success': False, 'error': 'Dhan not authenticated'}, 401

            dhan_service = DhanOrderService(access_token=dhan_access_token, client_id=dhan_client_id)
            kite_opt_sym = tradingsymbol_override
            _sec_id = sec_id

            if not _sec_id or not kite_opt_sym:
                kite = get_kite()
                if not kite: return {'success': False, 'error': 'Kite required for Dhan fallback resolution'}, 401
                from trading_app.service.kite_order_services import KiteService
                kite_service = KiteService(kite_instance=kite)
                kite_opt_sym = kite_service.get_option_symbol(symbol, strike, option_type)
                if not kite_opt_sym: return {'success': False, 'error': 'Option not found'}, 400
                _sec_id = str(dhan_service.search_symbol(kite_opt_sym).get('security_id', kite_opt_sym))

            exchange_seg = 'BSE_FNO' if symbol.upper() == 'SENSEX' else 'NSE_FNO'
            result = dhan_service.place_order(security_id=_sec_id, transaction_type=action, quantity=main_qty, order_type='LIMIT' if limit_price else 'MARKET', product_type='INTRADAY', exchange_segment=exchange_seg, price=limit_price if limit_price else 0.0)

            if result['success']:
                from trading_app.app.utils.order_tracker import BotOrderTracker
                if kite_opt_sym:
                    BotOrderTracker.add_symbol(username, kite_opt_sym)

                if action == 'BUY':
                    try:
                        entry = result.get('price', 0)
                        if not entry or entry == 0:
                            kite = get_kite()
                            ltp_data = kite.ltp([f'NSE:{kite_opt_sym}']) if kite and kite_opt_sym else {}
                            entry = ltp_data.get(f'NSE:{kite_opt_sym}', {}).get('last_price', strike)

                        if entry and entry > 0:
                            sl_order_ids = []
                            if strategy != 'intrinsic':
                                sl_p = entry - 20
                                exchange_seg = 'BSE_FNO' if symbol.upper() == 'SENSEX' else 'NSE_FNO'
                                sl_res = dhan_service.place_stoploss_order(security_id=_sec_id, trigger_price=sl_p, quantity=sl_qty, product_type='INTRADAY', exchange_segment=exchange_seg, entry_price=entry, transaction_type=sl_transaction_type)
                                if sl_res.get('success'):
                                    sl_order_ids = [sl_res.get('order_id')]
                                    result.update({'sl_order_id': sl_res.get('order_id'), 'sl_trigger_price': sl_p, 'sl_success': True})

                            if strategy == 'intrinsic':
                                from trading_app.app.intraday_option.intrinsic_order_manager import IntrinsicOrderManager
                                IntrinsicOrderManager.start_monitoring(broker, _active_instance, symbol, strike, option_type, entry, sl_order_ids, lot_size, order_lots, _sec_id, kite_opt_sym, username, session_data)
                    except Exception as e: logger.error(f"[SL] Dhan Err: {e}")
            return result, (200 if result['success'] else 400)

        elif broker == 'fyers':
            fyers_at = session_data.get(f'fyers_{_active_instance}_access_token') or UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_ACCESS_TOKEN')
            fyers_id = UserEnvManager.get_user_var(username, f'BROKER_{_active_instance}_APP_ID')
            if not fyers_at: return {'success': False, 'error': 'Fyers not authenticated'}, 401

            from trading_app.service.fyers_order_services import FyersOrderService
            fyers_service = FyersOrderService(app_id=fyers_id, access_token=fyers_at)
            kite = get_kite()
            if not kite: return {'success': False, 'error': 'Kite required for Fyers'}, 401
            from trading_app.service.kite_order_services import KiteService
            kite_service = KiteService(kite_instance=kite)
            kite_opt_sym = kite_service.get_option_symbol(symbol, strike, option_type)
            if not kite_opt_sym: return {'success': False, 'error': 'Option not found'}, 400

            fyers_side = fyers_service.SIDE_BUY if action == 'BUY' else fyers_service.SIDE_SELL
            fyers_order_type = 1 if limit_price else 2
            prefix = 'BSE' if symbol.upper() == 'SENSEX' else 'NSE'
            f_symbol = f'{prefix}:{kite_opt_sym}'

            result = fyers_service.place_order(symbol=f_symbol, side=fyers_side, quantity=main_qty, order_type=fyers_order_type, limit_price=limit_price if limit_price else 0.0, product_type='INTRADAY')

            if result['success']:
                from trading_app.app.utils.order_tracker import BotOrderTracker
                if kite_opt_sym:
                    BotOrderTracker.add_symbol(username, kite_opt_sym)
                    BotOrderTracker.add_symbol(username, f'NSE:{kite_opt_sym}')

                if action == 'BUY':
                    try:
                        entry = result.get('price', 0)
                        if not entry or entry == 0:
                            if kite and kite_opt_sym:
                                ltp_data = kite.ltp([f'NSE:{kite_opt_sym}'])
                                entry = ltp_data.get(f'NSE:{kite_opt_sym}', {}).get('last_price', strike)

                        if entry and entry > 0:
                            sl_order_ids = []
                            if strategy != 'intrinsic':
                                sl_p = entry - 20
                                prefix = 'BSE' if symbol.upper() == 'SENSEX' else 'NSE'
                                sl_res = fyers_service.place_stoploss_order(symbol=f'{prefix}:{kite_opt_sym}', trigger_price=sl_p, quantity=sl_qty, product_type='INTRADAY', transaction_type=sl_transaction_type)
                                if sl_res.get('success'):
                                    sl_order_ids = [sl_res.get('order_id')]
                                    result.update({'sl_order_id': sl_res.get('order_id'), 'sl_trigger_price': sl_p, 'sl_success': True})

                            if strategy == 'intrinsic':
                                from trading_app.app.intraday_option.intrinsic_order_manager import IntrinsicOrderManager
                                IntrinsicOrderManager.start_monitoring(broker, _active_instance, symbol, strike, option_type, entry, sl_order_ids, lot_size, order_lots, None, kite_opt_sym, username, session_data)
                    except Exception as e: logger.error(f"[SL] Fyers Err: {e}")
            return result, (200 if result['success'] else 400)

        return {'success': False, 'error': 'Invalid broker'}, 400

    final_responses = []
    for target in targets:
        res, code = _execute_single(target['type'], target['instance'])
        final_responses.append({'broker': target['type'], 'instance': target['instance'], 'result': res, 'status': code})

    any_success = any(r['result'].get('success') for r in final_responses)
    top_error = None
    if not any_success and final_responses:
        errors = [r['result'].get('error') for r in final_responses if r['result'].get('error')]
        top_error = errors[0] if errors else 'Order failed'
    return {
        'success': any_success,
        'error': top_error,
        'brokers_targeted': len(final_responses),
        'summary': final_responses,
    }


@api_bp.route('/intraday-920/place-order', methods=['POST'])
def place_intraday_920_order() -> EndpointResponse:
    """Place an option order for Intraday 9:20 strategy.

    If broker is None or missing, places on ALL active brokers.
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['symbol', 'strike', 'option_type', 'action']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400

        symbol = data['symbol']
        strike = int(data['strike'])
        option_type = data['option_type'].upper()
        action = data['action'].upper()
        broker_input = data.get('broker')
        quantity = data.get('quantity')
        tradingsymbol_override = data.get('tradingsymbol')
        expiry_override = data.get('expiry')
        strategy = data.get('strategy', '')
        limit_price = data.get('limit_price')
        _username = session.get('username', 'Mine')

        if broker_input:
            logger.info(f"[920/place-order] 'broker' field '{broker_input}' in payload is ignored — routing via .env 920_ACTIVE flags")

        result = _dispatch_order_to_brokers(
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            action=action,
            strategy=strategy,
            username=_username,
            session_data=dict(session),
            quantity=quantity,
            tradingsymbol_override=tradingsymbol_override,
            expiry_override=expiry_override,
            limit_price=limit_price,
            sec_id=data.get('sec_id'),
        )
        return jsonify(result), (200 if result['success'] else 400)

    except Exception as e:
        logger.error(f"Error in multi-order: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Error in multi-order: {str(e)}'
        }), 500


# ── Mine Orders API ──────────────────────────────────────────────────────────

@api_bp.route('/mine-orders', methods=['POST'])
def create_mine_order() -> EndpointResponse:
    """Store a 'Mine' mode order. LIMIT orders are queued and monitored by the backend;
    MARKET orders are dispatched immediately and stored as EXECUTED.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        for field in ['symbol', 'strike', 'option_type', 'action']:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'Missing: {field}'}), 400

        from trading_app.app.utils.mine_order_store import MineOrderStore

        username = session.get('username', 'Mine')
        order_type = data.get('order_type', 'MARKET').upper()
        limit_price = data.get('limit_price') or data.get('price') or 0

        record_data = {
            'mode': 'mine',
            'symbol': data['symbol'].upper(),
            'strike': int(data['strike']),
            'option_type': data['option_type'].upper(),
            'action': data['action'].upper(),
            'strategy': data.get('strategy', 'intrinsic'),
            'order_type': order_type,
            'type': order_type,
            'instrument': 'NFO',
            'price': float(limit_price),
            'username': username,
        }

        if order_type == 'LIMIT':
            if not limit_price or float(limit_price) <= 0:
                return jsonify({'success': False, 'error': 'limit_price required for LIMIT order'}), 400
            record = MineOrderStore.add_order({**record_data, 'status': 'PENDING'})
            return jsonify({'success': True, 'id': record['id'], 'status': 'PENDING', 'order_type': 'LIMIT'})

        # MARKET: dispatch immediately
        record = MineOrderStore.add_order({**record_data, 'status': 'EXECUTING'})
        result = _dispatch_order_to_brokers(
            symbol=record_data['symbol'],
            strike=record_data['strike'],
            option_type=record_data['option_type'],
            action=record_data['action'],
            strategy=record_data['strategy'],
            username=username,
            session_data=dict(session),
        )
        if result.get('success'):
            MineOrderStore.update_order(record['id'], {
                'status': 'EXECUTED',
                'executed_at': int(_time.time() * 1000),
                'broker_order_ids': result.get('summary', []),
            })
        else:
            MineOrderStore.update_order(record['id'], {'status': 'REJECTED', 'error': result.get('error', '')})

        return jsonify({**result, 'id': record['id']}), (200 if result.get('success') else 400)

    except Exception as e:
        logger.error(f"[mine-orders/create] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/mine-orders', methods=['GET'])
@csrf.exempt
def list_mine_orders() -> EndpointResponse:
    """Return today's Mine orders (or all recent if ?history=1)."""
    try:
        from trading_app.app.utils.mine_order_store import MineOrderStore
        history = request.args.get('history', '0') == '1'
        orders = MineOrderStore.get_all_orders() if history else MineOrderStore.get_today_orders()
        return jsonify({'success': True, 'orders': orders})
    except Exception as e:
        logger.error(f"[mine-orders/list] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/mine-orders/<order_id>', methods=['DELETE'])
def cancel_mine_order(order_id: str) -> EndpointResponse:
    """Cancel a pending Mine order."""
    try:
        from trading_app.app.utils.mine_order_store import MineOrderStore
        found = MineOrderStore.cancel_order(order_id)
        if found:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Order not found'}), 404
    except Exception as e:
        logger.error(f"[mine-orders/cancel] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/mine-orders/<order_id>/price', methods=['PUT'])
def update_mine_order_price(order_id: str) -> EndpointResponse:
    """Update the limit price of a pending Mine order."""
    try:
        data = request.get_json()
        new_price = float(data.get('price', 0)) if data else 0
        if new_price <= 0:
            return jsonify({'success': False, 'error': 'Invalid price'}), 400
        from trading_app.app.utils.mine_order_store import MineOrderStore
        found = MineOrderStore.update_price(order_id, new_price)
        if found:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Order not found or not pending'}), 404
    except Exception as e:
        logger.error(f"[mine-orders/price] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def _start_mine_order_monitor():
    """Start the background mine-order monitor (called once at app startup)."""
    try:
        from trading_app.app.intraday_option.mine_order_monitor import start_monitor
        start_monitor()
    except Exception as e:
        logger.warning(f"[MineOrderMonitor] Could not start: {e}")


# Start mine order monitor when blueprint is first used
import atexit as _atexit
_mine_monitor_started = False

@api_bp.before_app_request
def _ensure_mine_monitor():
    global _mine_monitor_started
    if not _mine_monitor_started:
        _mine_monitor_started = True
        _start_mine_order_monitor()


# ── Live Signal Monitors ──────────────────────────────────────────────────────

# Global registry for live signal monitors
# Key: username, Value: Intraday920LiveSignal instance
_live_signal_monitors = {}
import threading
_monitor_lock = threading.Lock()


@api_bp.route('/start-monitoring', methods=['POST'])
def start_monitoring() -> EndpointResponse:
    """Start live signal monitoring for the logged-in user.
    
    Creates per-user Excel log files and starts background monitoring thread.
    Uses username from session to ensure user-specific logging.
    
    Query Params:
        type (str): 'ENTRY', 'SL', or 'ALL' (default 'ALL')
    """
    try:
        from trading_app.app.intraday_option.intraday_9_20_live_signal import Intraday920LiveSignal, excel_logger
        from kiteconnect import KiteConnect
        import threading

        
        username = session.get('username')
        if not username:
            return jsonify({
                'success': False,
                'error': 'User not authenticated'
            }), 401
        
        # Get monitoring type and parameters
        monitor_type = request.args.get('type', 'ALL').upper()
        symbol = request.args.get('symbol', 'NIFTY').upper()
        ratio = request.args.get('ratio', '1:2-trail')
        
        if monitor_type not in ['ENTRY', 'SL', 'ALL']:
            return jsonify({
                'success': False,
                'error': 'Invalid type. Must be ENTRY, SL, or ALL'
            }), 400
            
        monitor_entries = monitor_type in ['ENTRY', 'ALL']
        monitor_sl = monitor_type in ['SL', 'ALL']
        
        # Get or create monitor instance
        global _live_signal_monitors
        
        # Use lock to prevent race conditions where multiple requests create multiple monitors
        with _monitor_lock:
            monitor = _live_signal_monitors.get(username)
            
            # If monitor exists but parameters changed, we might need to recreate or update it
            # For now, let's keep it simple: if monitor exists, use it. 
            # If we want to support changing symbol/ratio without restarting, we'd need more logic.
            
            if not monitor:
                # Get KiteConnect instance
                kite = get_kite()
                if not kite:
                    return jsonify({
                        'success': False,
                        'error': 'KiteConnect not initialized - please login with Zerodha first'
                    }), 400
                
                # Create new monitor
                logger.info(f"Creating new Intraday920LiveSignal instance for {username} (Symbol: {symbol}, Ratio: {ratio})")
                monitor = Intraday920LiveSignal(kite, symbol=symbol, username=username, risk_reward_ratio=ratio)
                _live_signal_monitors[username] = monitor
            else:
                logger.info(f"Using existing Intraday920LiveSignal instance for {username} (ID: {id(monitor)})")
                # Optional: Update existing monitor's ratio/symbol if we want
                # monitor.symbol = symbol
                # monitor.risk_reward_ratio = ratio

        
        # Check if it's a market day
        if not monitor.is_market_day():
            return jsonify({
                'success': False,
                'error': 'Not a market day - monitoring not started'
            }), 400
        
        # Start requested components
        start_result = monitor.start_monitoring(monitor_entries=monitor_entries, monitor_sl=monitor_sl)
        
        active_components = []
        if monitor.is_entry_monitoring: active_components.append("ENTRY")
        if monitor.is_sl_monitoring: active_components.append("SL")
        
        status_msg = f"Monitoring active: {', '.join(active_components)}"
        
        return jsonify({
            'success': True,
            'message': f'Live monitoring updated for {username}. {status_msg}',
            'username': username,
            'excel_file': excel_logger.file_path,
            'status': {
                'entry_monitoring': monitor.is_entry_monitoring,
                'sl_monitoring': monitor.is_sl_monitoring,
                'details': start_result
            }
        }), 200
            
    except Exception as e:
        logger.error(f"Error starting monitoring: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Error starting monitoring: {str(e)}'
        }), 500


@api_bp.route('/stop-monitoring', methods=['POST'])
def stop_monitoring() -> EndpointResponse:
    """Stop live signal monitoring for the logged-in user.
    
    Query Params:
        type (str): 'ENTRY', 'SL', or 'ALL' (default 'ALL')
    """
    try:
        username = session.get('username')
        if not username:
            return jsonify({
                'success': False,
                'error': 'User not authenticated'
            }), 401
            
        # Get monitoring type
        monitor_type = request.args.get('type', 'ALL').upper()
        if monitor_type not in ['ENTRY', 'SL', 'ALL']:
            return jsonify({
                'success': False,
                'error': 'Invalid type. Must be ENTRY, SL, or ALL'
            }), 400
            
        stop_entries = monitor_type in ['ENTRY', 'ALL']
        stop_sl = monitor_type in ['SL', 'ALL']
        
        global _live_signal_monitors
        monitor = _live_signal_monitors.get(username)
        
        if not monitor:
            return jsonify({
                'success': False,
                'error': 'No active monitoring session found'
            }), 404
            
        # Stop requested components
        stop_result = monitor.stop_monitoring(stop_entries=stop_entries, stop_sl=stop_sl)
        
        active_components = []
        if monitor.is_entry_monitoring: active_components.append("ENTRY")
        if monitor.is_sl_monitoring: active_components.append("SL")
        
        status_msg = f"Monitoring status: {', '.join(active_components) if active_components else 'STOPPED'}"
        
        # If everything stopped, we could optionally remove from registry
        # But keeping it allows restarting with same state/trades
        
        return jsonify({
            'success': True,
            'message': f'Monitoring stopped for {username}. {status_msg}',
            'status': {
                'entry_monitoring': monitor.is_entry_monitoring,
                'sl_monitoring': monitor.is_sl_monitoring,
                'details': stop_result
            }
        }), 200
            
    except Exception as e:
        logger.error(f"Error stopping monitoring: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Error stopping monitoring: {str(e)}'
        }), 500


@api_bp.route('/open-interest-test', methods=['POST'])
def get_open_interest_test() -> EndpointResponse:
    """
    Test endpoint for open interest data (no auth required).
    
    Returns:
        JSON with open interest data for CE and PE strikes
    """
    try:
        data = request.get_json() or {}
        symbol = data.get('symbol', 'NIFTY')
        
        logger.info(f"[TEST] Fetching open interest data for {symbol}")
        
        from trading_app.service.open_interest_service import OpenInterestService
        
        kite = get_kite()
        if not kite:
            return jsonify({
                'success': False,
                'error': 'Kite connection not available'
            }), 400
        
        oi_service = OpenInterestService(kite)
        oi_data = oi_service.get_open_interest_data(symbol)
        
        if not oi_data.get('success'):
            return jsonify(oi_data), 400
        
        return jsonify(oi_data), 200
        
    except Exception as e:
        logger.error(f"Error fetching open interest: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/debug-symbol', methods=['GET'])
def debug_symbol():
    try:
        symbol = request.args.get('symbol', 'ABB').strip().upper()
        kite = get_kite()
        if not kite:
            return jsonify({'error': 'No kite session'}), 400
            
        instruments = kite.instruments('NFO')
        
        # 1. Exact Name Match
        exact_matches = [i for i in instruments if i['name'] == symbol]
        
        # 2. Case-Insensitive Name Match
        case_matches = [i for i in instruments if i['name'].strip().upper() == symbol]
        
        # 3. Contains Match
        contains_matches = [i for i in instruments if symbol in i['name'].strip().upper()]
        
        # 4. Option Types in Exact/Case Matches
        options = [i for i in case_matches if i['instrument_type'] in ['CE', 'PE']]
        
        # 5. Expiries
        expiries = sorted(list(set(str(i['expiry']) for i in options))) if options else []
        
        # 6. Test OpenInterestService logic directly
        from trading_app.service.open_interest_service import OpenInterestService
        service = OpenInterestService(kite)
        
        # Create config like the service does
        config = {'name': symbol, 'exchange': 'NFO', 'instrument_key': f'NSE:{symbol}'}
        
        # Call private method (for debug)
        strikes_debug = service._get_available_strikes(instruments, symbol, 0, config)
        service_found_count = len(strikes_debug) if strikes_debug else 0
        
        response = {
            'symbol_requested': symbol,
            'total_nfo_instruments': len(instruments),
            'exact_matches_count': len(exact_matches),
            'case_matches_count': len(case_matches),
            'contains_matches_sample': [i['name'] for i in contains_matches[:10]],
            'options_found_count': len(options),
            'option_expiries': expiries,
            'sample_instrument': options[0] if options else (case_matches[0] if case_matches else None),
            'SERVICE_TEST_RESULT': {
                'found_count': service_found_count,
                'success': bool(strikes_debug)
            }
        }
        return jsonify(response)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/open-interest', methods=['POST'])
def get_open_interest() -> EndpointResponse:
    """
    Get open interest data for options strikes.
    
    Prioritizes reading from local DB (populated by background scheduler).
    Falls back to live fetch if DB data is stale or missing.
    
    Returns:
        JSON with open interest data for CE and PE strikes
    """
    try:
        data = request.get_json() or {}
        symbol = data.get('symbol', 'NIFTY')
        
        logger.info(f"Fetching open interest data for {symbol}")
        
        from trading_app.service.open_interest_service import OpenInterestService
        
        # Data fetch always uses Broker 1 (Zerodha Kite - data account)
        provider = get_data_provider()
        if not provider:
            return jsonify({
                'success': False,
                'error': 'Data provider (Kite/Fyers) is not connected.'
            }), 400
        
        oi_service = OpenInterestService(provider)
        
        # Request Coalescing: Only one thread fetches live for this symbol at a time
        req_lock = _get_request_lock(f"OI_{symbol}")
        with req_lock:
            # 1. Try to get data from DB first
            # Reduce max_age to 1 minute to ensure fresh data
            db_data = oi_service.get_latest_oi_from_db(symbol, max_age_minutes=1)
            
            if db_data:
                logger.info(f"✅ Serving OI data from DB for {symbol} (Timestamp: {db_data.get('timestamp')})")
                db_data['server_timestamp'] = datetime.now().isoformat()
                db_data['data_source'] = 'DATABASE'
                return jsonify(db_data)
            
            # 2. Fallback: Fetch Live if DB is empty or stale
            # We no longer refuse live fetches based on the clock here, 
            # as the UI handles the frequency logic. 
            # If the UI specifically asks, we try to fetch.

            logger.info(f"⚠️ DB data missing or stale for {symbol}. Fetching live...")
            oi_data = oi_service.get_open_interest_data(symbol)
            
            if not oi_data.get('success'):
                return jsonify(oi_data), 400
                
            # Save this live fetch to DB so next call is fast
            try:
                oi_service.save_oi_snapshot(symbol, oi_data)
                logger.info(f"Saved fallback OI snapshot for {symbol}")
            except Exception as save_e:
                logger.error(f"Failed to save fallback snapshot: {save_e}")
            
            # Add server timestamp to verify data freshness
            oi_data['server_timestamp'] = datetime.now().isoformat()
            oi_data['data_source'] = 'LIVE_FALLBACK'
            
            logger.info(f"API response server_timestamp: {oi_data['server_timestamp']}")
            
            response = jsonify(oi_data)
            # Disable caching for real-time data
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            return response
        
    except Exception as e:
        logger.error(f"Error fetching open interest: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def resolve_itm_strikes(kite_service, symbol, spot_high, spot_low, step_value, offset_multiplier=3, data_provider=None):
    """Refactored helper for robust strike discovery. Now provider-aware (Kite/Fyers)."""
    offset = offset_multiplier * step_value
    itm_ce_strike = int(math.ceil((spot_low - offset) / step_value) * step_value)
    itm_pe_strike = int(math.floor((spot_high + offset) / step_value) * step_value)

    is_fyers = data_provider is not None and hasattr(data_provider, 'find_option_symbol')

    ce_token, ce_symbol = _get_cached_strike_token(kite_service, data_provider, is_fyers, symbol, itm_ce_strike, 'CE')
    pe_token, pe_symbol = _get_cached_strike_token(kite_service, data_provider, is_fyers, symbol, itm_pe_strike, 'PE')

    # Fallback: nearest available strike (Kite only)
    if (not ce_token or not pe_token) and not is_fyers:
        all_options = kite_service.get_nfo_instruments(symbol)
        available_strikes = sorted(list(set([i['strike'] for i in all_options if i.get('strike') is not None])))
        if available_strikes:
            if not ce_token:
                itm_ce_strike = min(available_strikes, key=lambda x: abs(x - (spot_low - offset)))
                ce_token, ce_symbol = _get_cached_strike_token(kite_service, None, False, symbol, itm_ce_strike, 'CE')
            if not pe_token:
                itm_pe_strike = min(available_strikes, key=lambda x: abs(x - (spot_high + offset)))
                pe_token, pe_symbol = _get_cached_strike_token(kite_service, None, False, symbol, itm_pe_strike, 'PE')

    return itm_ce_strike, itm_pe_strike, ce_symbol, pe_symbol, ce_token, pe_token

@api_bp.route('/strategy-signal', methods=['GET'])
@csrf.exempt
@limiter.exempt
def get_strategy_signal() -> EndpointResponse:
    """Evaluate PE vs CE market signals and return a spread recommendation."""
    auth_error = check_auth()
    if auth_error:
        return auth_error

    symbol = request.args.get('symbol', 'NIFTY').upper()
    try:
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider not connected'}), 401

        from trading_app.service.strategy_signal_service import StrategySignalService
        result = StrategySignalService(provider).evaluate(symbol)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[strategy-signal] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/straddle/status', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_straddle_status() -> EndpointResponse:
    """Return current straddle state + live LTPs."""
    try:
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider not connected'}), 401
        from trading_app.algo.nifty_weekly_straddle import NiftyWeeklyStraddle
        return jsonify(NiftyWeeklyStraddle.get_status(provider))
    except Exception as e:
        logger.error(f'[straddle/status] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/straddle/preview', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_straddle_preview() -> EndpointResponse:
    """Compute expected delta strikes and premium without placing orders."""
    try:
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider not connected'}), 401
        username = session.get('username') or 'Mine'
        broker_instance = int(request.args.get('broker_instance', _get_straddle_broker(username) or 1))
        from trading_app.service.provider_logic import get_kite
        kite = get_kite(user=username, instance=broker_instance)
        from trading_app.algo.nifty_weekly_straddle import NiftyWeeklyStraddle
        return jsonify(NiftyWeeklyStraddle(provider, kite, broker_instance, username).preview())
    except Exception as e:
        logger.error(f'[straddle/preview] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/straddle/enter', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_straddle_enter() -> EndpointResponse:
    """Manually enter the straddle (overrides scheduler timing)."""
    try:
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider not connected'}), 401
        username = session.get('username') or 'Mine'
        data = request.get_json(silent=True) or {}
        broker_instance = int(data.get('broker_instance') or _get_straddle_broker(username) or 1)
        from trading_app.service.provider_logic import get_kite
        kite = get_kite(user=username, instance=broker_instance)
        if not kite:
            return jsonify({'success': False, 'error': f'Broker {broker_instance} not connected'}), 401
        from trading_app.algo.nifty_weekly_straddle import NiftyWeeklyStraddle
        result = NiftyWeeklyStraddle(provider, kite, broker_instance, username).enter_straddle(force=True)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[straddle/enter] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/straddle/exit', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_straddle_exit() -> EndpointResponse:
    """Manually exit the active straddle (emergency square-off)."""
    try:
        username = session.get('username') or 'Mine'
        data = request.get_json(silent=True) or {}
        broker_instance = int(data.get('broker_instance') or _get_straddle_broker(username) or 1)
        from trading_app.service.provider_logic import get_kite, get_data_provider as _gdp
        kite = get_kite(user=username, instance=broker_instance)
        if not kite:
            return jsonify({'success': False, 'error': f'Broker {broker_instance} not connected'}), 401
        from trading_app.algo.nifty_weekly_straddle import NiftyWeeklyStraddle
        provider = _gdp(user=username)
        result = NiftyWeeklyStraddle(provider, kite, broker_instance, username).exit_straddle('MANUAL')
        return jsonify(result)
    except Exception as e:
        logger.error(f'[straddle/exit] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def _get_straddle_broker(username: str) -> Optional[int]:
    """Return the first broker instance with STRADDLE_ACTIVE=true for the user."""
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        for i in range(1, 11):
            active = UserEnvManager.get_user_var(username, f'BROKER_{i}_STRADDLE_ACTIVE', '')
            if str(active).lower().strip() == 'true':
                return i
    except Exception:
        pass
    return None


@api_bp.route('/pcr/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def pcr_history() -> EndpointResponse:
    """Return intraday PCR + OI change time-series from oi_history DB."""
    try:
        from datetime import date as _date
        symbol = request.args.get('symbol', 'NIFTY').upper()
        date_str = request.args.get('date', _date.today().isoformat())
        from trading_app.service.open_interest_service import OpenInterestService
        svc = OpenInterestService(None)
        data = svc.get_intraday_pcr_history(symbol, date_str)
        return jsonify({'success': True, 'symbol': symbol, 'date': date_str, 'data': data})
    except Exception as e:
        logger.error(f'[pcr/history] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/oi-profile/candles', methods=['GET'])
@csrf.exempt
@limiter.exempt
def oi_profile_candles() -> EndpointResponse:
    """
    Fetch intraday OHLC candle data for a symbol (default NIFTY).
    Optimized version with parallel execution and caching.
    """
    NSE_INDEX_TOKENS = {
        'NIFTY':      256265,   # NSE:NIFTY 50
        'BANKNIFTY':  260105,   # NSE:NIFTY BANK
        'FINNIFTY':   257801,   # NSE:NIFTY FIN SERVICE
        'MIDCPNIFTY': 288009,   # NSE:NIFTY MIDCAP SELECT
        'NIFTY MIDCAP 150': 266249,
        'NIFTY AUTO':      263433,
        'NIFTY Smallcap 100': 267017,
        'NIFTY SMLCAP 100': 267017,
        'NIFTY FMCG':      261897,
        'NIFTY METAL':     263689,
        'NIFTY PHARAMA':   262409,
        'NIFTY PHARMA':    262409,
        'NIFTY PSU BANK':  262921,
        'NIFTY IT':        259849,
    }

    try:
        symbol   = request.args.get('symbol',   'NIFTY').upper()
        interval = request.args.get('interval', '5minute')
        days     = request.args.get('days', 1, type=int)
        opt_days = request.args.get('opt_days', 5, type=int)
        spot_high  = request.args.get('spot_high', type=float)
        spot_low   = request.args.get('spot_low',  type=float)
        step_value = request.args.get('step', 50,  type=int)
        multiplier = request.args.get('multiplier', 2, type=int)
        auto_hl    = request.args.get('auto_hl', 'false').lower() == 'true'
        first_5m_atm = request.args.get('first_5m_atm', 'false').lower() == 'true'
        custom_strike = request.args.get('custom_strike', type=int)
        ce_strike = request.args.get('ce_strike', type=int)
        pe_strike = request.args.get('pe_strike', type=int)
        start_date_str = request.args.get('start_date')
        end_date_str   = request.args.get('end_date')

        # Sentinel Check: Ignore UI placeholders (20000 for Nifty, 50000 for BankNifty)
        # and fallback to ATM calculation to prevent fetching wrong data on first load.
        if custom_strike:
            is_placeholder = (symbol == 'NIFTY' and custom_strike == 20000) or \
                             (symbol == 'BANKNIFTY' and custom_strike == 50000)
            if is_placeholder:
                custom_strike = None

        # ── 1. Check Response Cache & Coalesce Requests ──────────────
        # Use request parameters as cache key (ignore _t timestamp)
        # Include start_date/end_date so historical range requests are cached independently
        cache_key = (symbol, interval, days, opt_days, spot_high, spot_low, auto_hl, first_5m_atm, custom_strike, ce_strike, pe_strike, start_date_str, end_date_str)
        
        # Request Coalescing: Only one thread fetches for this key at a time
        req_lock = _get_request_lock(cache_key)
        with req_lock:
            with _candle_cache_lock:
                if cache_key in _candle_response_cache:
                    data, ts = _candle_response_cache[cache_key]
                    # Reduced cache to 0.5s to allow for high-frequency (1s) price updates
                    if datetime.now().timestamp() - ts < 0.5:
                        return jsonify(data)
                
                # Prune cache if it exceeds max size
                if len(_candle_response_cache) > _MAX_CACHE_ENTRIES:
                    entries_to_remove = len(_candle_response_cache) - (_MAX_CACHE_ENTRIES // 2)
                    sorted_keys = sorted(_candle_response_cache.keys(), key=lambda k: _candle_response_cache[k][1])
                    for k in sorted_keys[:entries_to_remove]:
                        _candle_response_cache.pop(k, None)

        # ── 2. Market Hours Check ──────────────
        market_is_open = _cached_market_hours()
        
        # If market is closed, we can use a much longer cache (1 hour)
        if not market_is_open:
            with _candle_cache_lock:
                if cache_key in _candle_response_cache:
                    data, ts = _candle_response_cache[cache_key]
                    if datetime.now().timestamp() - ts < 3600: # 1 hour cache when market closed
                        return jsonify(data)

        valid_intervals = ['30second', 'minute', '2minute', '3minute', '5minute', '10minute',
                           '15minute', '30minute', '60minute', 'day', 'week', 'month']

        # Allow all symbols (indices + F&O stocks)
        # Validation happens during token resolution below
        if interval not in valid_intervals:
            return jsonify({'success': False, 'error': f'Invalid interval. Use one of {valid_intervals}'}), 400
        
        # Increase max days for week/month intervals to allow long-term analysis (minimum 200 candles)
        max_allowed_days = 10000 if interval in ['week', 'month', 'day'] else 500
        days = min(max(int(days), 1), max_allowed_days)
        kite = get_kite(instance=1)
        # Get configured data provider (Kite or Fyers based on DATA_PROVIDER env flag)
        _data_provider = get_data_provider()
        if not kite and not _data_provider:
            return jsonify({'success': False, 'error': 'Data provider not connected. Please login.'}), 401
        
        # Detect if using Fyers as data provider
        from trading_app.service.fyers_data_service import FyersDataServiceAdapter
        _is_fyers_provider = isinstance(_data_provider, FyersDataServiceAdapter)
        
        # Initialize KiteService with the active data provider to ensure symbol/token 
        # lookups match the data source (crucial for Fyers vs Kite compatibility)
        effective_instance = _data_provider if _data_provider else kite
        kite_service = KiteService(kite_instance=effective_instance) if effective_instance else KiteService()
        
        ist_offset = int(5.5 * 3600)  # 19 800 s
        now = datetime.now()
        
        if start_date_str and end_date_str:
            try:
                from_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(hour=9, minute=0, second=0, microsecond=0)
                to_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=15, minute=30, second=0, microsecond=0)
                # Calculate days range for filtering later in the function
                days = (to_date.date() - from_date.date()).days + 1
                if days > 100: days = 100 # Safety limit
                fetch_back = days
            except Exception as e:
                logger.error(f"Date parse error: {e}")
                fetch_back = days + 5
                from_date = (now - timedelta(days=fetch_back)).replace(hour=9, minute=0, second=0, microsecond=0)
                to_date = now
            # When explicit date range given, option candles use same window
            opt_from_date = from_date
        else:
            fetch_back = days + 5
            from_date = (now - timedelta(days=fetch_back)).replace(hour=9, minute=0, second=0, microsecond=0)
            to_date = now
            # Option candles use their own (shorter) window
            opt_days = min(max(int(opt_days), 1), days)
            opt_from_date = (now - timedelta(days=opt_days + 5)).replace(hour=9, minute=0, second=0, microsecond=0)

        fetch_interval = 'minute' if interval == '2minute' else interval

        # ── Resolve Token ──────────────────────────────────────────
        if _is_fyers_provider:
            # For Fyers: use Fyers symbol strings
            token = FYERS_INDEX_SYMBOLS.get(symbol)
            if not token:
                # For non-index stocks: build Fyers-style symbol
                token = f'NSE:{symbol}-EQ'
        else:
            # For Kite: use integer instrument tokens
            token = NSE_INDEX_TOKENS.get(symbol)
            if not token:
                token = kite_service.get_instrument_token(symbol)
            
        if not token:
            return jsonify({'success': False, 'error': f'Invalid or unknown symbol: {symbol}'}), 400

        logger.info(f"[OI-Profile/Candles] Provider={'Fyers' if _is_fyers_provider else 'Kite'}, token={token}")

        # Shared aggregation/formatting logic
        def format_candles(raw_data, ist_offset, requested_interval):
            if not raw_data: return []
            
            temp = []
            for c in raw_data:
                # Lightweight Charts (candlestick) throws "Value is null" if any OHLC is None/NaN
                if any(x is None for x in [c.get('open'), c.get('high'), c.get('low'), c.get('close')]):
                    continue
                temp.append({
                    'time':   int(c['date'].timestamp()) + ist_offset,
                    'open':   c['open'], 'high':   c['high'], 'low':    c['low'],
                    'close':  c['close'], 'volume': c.get('volume', 0)
                })
            
            if requested_interval == '2minute':
                def merge_batch(batch):
                    if not batch: return None
                    return {
                        'time':   batch[0]['time'],
                        'open':   batch[0]['open'],
                        'high':   max(x['high'] for x in batch),
                        'low':    min(x['low'] for x in batch),
                        'close':  batch[-1]['close'],
                        'volume': sum(x['volume'] for x in batch)
                    }
                
                aggregated = []
                batch = []
                last_day = None
                for c in temp:
                    current_day = datetime.fromtimestamp(c['time'] - ist_offset).date()
                    if last_day and current_day != last_day and batch:
                        aggregated.append(merge_batch(batch)); batch = []
                    last_day = current_day
                    batch.append(c)
                    if len(batch) == 2:
                        aggregated.append(merge_batch(batch)); batch = []
                if batch: aggregated.append(merge_batch(batch))
                return aggregated
            return temp

        _fetch_errors = []

        def fetch_task(token, from_dt, to_dt, inter):
            try:
                if _is_fyers_provider and _data_provider:
                    # Use Fyers data provider for historical data
                    from_str = from_dt.strftime('%Y-%m-%d')
                    to_str = to_dt.strftime('%Y-%m-%d')
                    res = _data_provider.historical_data(str(token), from_str, to_str, inter, use_cache=False)
                elif kite:
                    # Use KiteService's retry logic and rate limiting
                    res = kite_service._historical_with_retry(instrument_token=int(token), from_date=from_dt, to_date=to_dt, interval=inter)
                else:
                    return []
                return res
            except Exception as e:
                msg = str(e)
                logger.error(f"[OI-Profile] Fetch error for token {token}: {msg}")
                _fetch_errors.append(msg)
                return []

        # ── Parallel execution ──────────────────────────────────────
        executor = _api_executor # Use shared global executor
        # 1. Start fetching index intraday and index daily
        future_index = executor.submit(fetch_task, token, from_date, to_date, fetch_interval)
        # For 1-minute interval, also fetch 30-second candles in parallel so the
        # "2nd 30-second candle" box indicator can use accurate H/L.
        future_index_30s = executor.submit(fetch_task, token, from_date, to_date, '30second') if interval == 'minute' else None
        
        # Use daily OHLC cache if available (TTL 5 mins)
        daily_cache_key = (symbol, days)
        cached_daily = None
        with _candle_cache_lock:
            if daily_cache_key in _daily_ohlc_cache:
                _d_data, _d_ts = _daily_ohlc_cache[daily_cache_key]
                if datetime.now().timestamp() - _d_ts < 300:
                    cached_daily = _d_data

        future_daily = None
        if not cached_daily:
            d_from = (now - timedelta(days=fetch_back + 10)).replace(hour=0, minute=0, second=0, microsecond=0)
            future_daily = executor.submit(fetch_task, token, d_from, to_date, 'day')
        
        # 2. Identify strikes if spot provided, otherwise wait for index
        itm_ce_strike, itm_pe_strike = None, None
        ce_symbol, pe_symbol, ce_token, pe_token = None, None, None, None
        future_ce_30s, future_pe_30s = None, None
        
        today_str = now.strftime('%Y-%m-%d')
        atm_cache_key = (symbol, today_str)
        
        if ce_strike or pe_strike:
            # Separate CE / PE strikes selected from the UI
            itm_ce_strike = ce_strike if ce_strike else None
            itm_pe_strike = pe_strike if pe_strike else None
            if itm_ce_strike:
                ce_token, ce_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_ce_strike, 'CE')
            if itm_pe_strike:
                pe_token, pe_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_pe_strike, 'PE')
        elif custom_strike:
            # Strike is known upfront — resolve tokens and submit CE/PE in parallel with index,
            # regardless of auto_hl mode (auto_hl still computes spot_high/low for intrinsic calc).
            itm_ce_strike = custom_strike
            itm_pe_strike = custom_strike
            ce_token, ce_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_ce_strike, 'CE')
            pe_token, pe_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_pe_strike, 'PE')
        elif not auto_hl and not first_5m_atm and spot_high is not None and spot_low is not None:
            itm_ce_strike, itm_pe_strike, ce_symbol, pe_symbol, ce_token, pe_token = resolve_itm_strikes(
                kite_service, symbol, spot_high, spot_low, step_value, data_provider=(_data_provider if _is_fyers_provider else None)
            )
        elif first_5m_atm and atm_cache_key in _daily_5m_atm_cache:
            # FAST-LANE: 5m ATM is static after 9:20 — use cached strike to concurrently fetch options.
            atm_strike = _daily_5m_atm_cache[atm_cache_key]
            itm_ce_strike, itm_pe_strike = atm_strike, atm_strike
            ce_token, ce_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_ce_strike, 'CE')
            pe_token, pe_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_pe_strike, 'PE')

        # 3. Fetch Option Candles if tokens known (use shorter opt_from_date window)
        future_ce = None
        future_pe = None
        if ce_token: future_ce = executor.submit(fetch_task, ce_token, opt_from_date, to_date, fetch_interval)
        if pe_token: future_pe = executor.submit(fetch_task, pe_token, opt_from_date, to_date, fetch_interval)
        future_ce_30s = executor.submit(fetch_task, ce_token, opt_from_date, to_date, '30second') if interval == 'minute' and ce_token else None
        future_pe_30s = executor.submit(fetch_task, pe_token, opt_from_date, to_date, '30second') if interval == 'minute' and pe_token else None

        # 4. Wait for Index to finish if auto_hl is true
        index_raw = future_index.result()
        
        # Filter strictly by the last N actual trading days
        target_dates: list = []
        if index_raw:
            unique_dates = sorted(list(set(c['date'].date() for c in index_raw)))
            target_dates = unique_dates[-days:] if len(unique_dates) >= days else unique_dates
            index_raw = [c for c in index_raw if c['date'].date() in target_dates]
            
        
        candles = format_candles(index_raw, ist_offset, interval)

        # Submit DB max_pain query now so it runs in parallel with CE/PE candle collection.
        def _fetch_max_pain_rows(_symbol, _days, _now, _target_dates):
            _ist = int(5.5 * 3600)
            result = []
            try:
                import sqlite3
                from trading_app.service.open_interest_service import OpenInterestService
                db_path = OpenInterestService().db_path
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.cursor()
                    days_ago = (_now - timedelta(days=_days+5)).strftime('%Y-%m-%d')
                    cursor.execute('''
                        SELECT timestamp, max_pain FROM oi_history
                        WHERE symbol = ? AND max_pain IS NOT NULL AND max_pain > 0
                        AND timestamp >= ?
                        ORDER BY timestamp ASC
                        LIMIT 2000
                    ''', (_symbol, days_ago))
                    rows = cursor.fetchall()
                for ts_str, mp in rows:
                    try:
                        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                        m_time = dt.time()
                        if not (datetime.strptime("09:15", "%H:%M").time() <= m_time <= datetime.strptime("15:30", "%H:%M").time()):
                            continue
                        if _target_dates and dt.date() not in _target_dates:
                            continue
                        result.append({'time': int(dt.timestamp()) + _ist, 'value': float(mp)})
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"[OI-Profile] Error fetching max_pain_history: {e}")
            return result

        future_maxpain = executor.submit(_fetch_max_pain_rows, symbol, days, now, target_dates)

        if (auto_hl or first_5m_atm) and candles and not (ce_token and pe_token):
            last_candle_date = datetime.fromtimestamp(candles[-1]['time'] - ist_offset).date()
            subset = [c for c in candles if datetime.fromtimestamp(c['time'] - ist_offset).date() == last_candle_date]
            
            if subset:
                spot_high = round(max(c['high'] for c in subset), 2)
                spot_low = round(min(c['low'] for c in subset), 2)
            else:
                spot_high = round(max(c['high'] for c in candles[-10:]), 2)
                spot_low = round(min(c['low'] for c in candles[-10:]), 2)
            
            # Re-fetch subset for first candle calculation if needed
            if not subset: subset = candles[-10:]

            if first_5m_atm and subset:
                # Find the 5-minute close specifically
                # Market starts at 09:15. First 5m candle ends at 09:20.
                five_m_close_candle = None
                for c in subset:
                    dt = datetime.fromtimestamp(c['time'] - ist_offset)
                    # For 1m interval, 09:20 is the 5th candle (starts 09:19 ends 09:20 usually, or labeled 09:19)
                    # Kite labels candles by their start time. So 09:15 1m is 09:15-09:16.
                    # 09:19 1m is 09:19-09:20.
                    # 09:15 5m is 09:15-09:20.
                    if dt.hour == 9 and dt.minute == 19 and interval == 'minute':
                        five_m_close_candle = c
                        break
                    if dt.hour == 9 and dt.minute == 15 and interval == '5minute':
                        five_m_close_candle = c
                        break
                
                # Fallback to first available if not found exactly
                if not five_m_close_candle: five_m_close_candle = subset[0]
                
                close_p = five_m_close_candle['close']
                # Round to nearest 100
                atm_strike = int(round(close_p / 100.0) * 100)
                _daily_5m_atm_cache[atm_cache_key] = atm_strike
                
                itm_ce_strike, itm_pe_strike = atm_strike, atm_strike
                ce_token, ce_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_ce_strike, 'CE')
                pe_token, pe_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_pe_strike, 'PE')
                logger.info(f"[OI-Profile] First 5m ATM: 5m Mark close {close_p} (at {datetime.fromtimestamp(five_m_close_candle['time'] - ist_offset).strftime('%H:%M')}) -> Strike {atm_strike}, CE_token={ce_token}, PE_token={pe_token}")
            else:
                if ce_strike or pe_strike:
                    itm_ce_strike = ce_strike if ce_strike else None
                    itm_pe_strike = pe_strike if pe_strike else None
                    if itm_ce_strike:
                        ce_token, ce_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_ce_strike, 'CE')
                    if itm_pe_strike:
                        pe_token, pe_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_pe_strike, 'PE')
                elif custom_strike:
                    itm_ce_strike, itm_pe_strike = custom_strike, custom_strike
                    ce_token, ce_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_ce_strike, 'CE')
                    pe_token, pe_symbol = _get_cached_strike_token(kite_service, _data_provider, _is_fyers_provider, symbol, itm_pe_strike, 'PE')
                else:
                    itm_ce_strike, itm_pe_strike, ce_symbol, pe_symbol, ce_token, pe_token = resolve_itm_strikes(
                        kite_service, symbol, spot_high, spot_low, step_value, data_provider=(_data_provider if _is_fyers_provider else None)
                    )
            
            if ce_token: future_ce = executor.submit(fetch_task, ce_token, opt_from_date, to_date, fetch_interval)
            if pe_token: future_pe = executor.submit(fetch_task, pe_token, opt_from_date, to_date, fetch_interval)
            future_ce_30s = executor.submit(fetch_task, ce_token, opt_from_date, to_date, '30second') if interval == 'minute' and ce_token else None
            future_pe_30s = executor.submit(fetch_task, pe_token, opt_from_date, to_date, '30second') if interval == 'minute' and pe_token else None

        # 5. Collect remaining results
        daily_raw = cached_daily if cached_daily else (future_daily.result() if future_daily else [])
        if not cached_daily and daily_raw:
            with _candle_cache_lock:
                _daily_ohlc_cache[daily_cache_key] = (daily_raw, datetime.now().timestamp())
        
        ce_raw = future_ce.result() if future_ce else []
        pe_raw = future_pe.result() if future_pe else []

        # Filter option candles to their own opt_days trading-day window
        if start_date_str and end_date_str:
            # Explicit range: align options with index dates
            if target_dates:
                if ce_raw: ce_raw = [c for c in ce_raw if c['date'].date() in target_dates]
                if pe_raw: pe_raw = [c for c in pe_raw if c['date'].date() in target_dates]
        else:
            # Default mode: options get their own shorter window (opt_days trading days)
            opt_all_dates = sorted(set(
                c['date'].date() for c in (ce_raw or []) + (pe_raw or [])
            ))
            opt_target_dates = set(opt_all_dates[-opt_days:]) if opt_all_dates else set()
            if opt_target_dates:
                if ce_raw: ce_raw = [c for c in ce_raw if c['date'].date() in opt_target_dates]
                if pe_raw: pe_raw = [c for c in pe_raw if c['date'].date() in opt_target_dates]

        # Collect 30-second results (only present when interval == 'minute')
        def _extract_second_30s_candles(raw_30s):
            """From raw 30-second candle list return one entry per day: the 2nd 30s candle."""
            if not raw_30s:
                return []
            day_map = {}
            for c in raw_30s:
                if any(x is None for x in [c.get('open'), c.get('high'), c.get('low'), c.get('close')]):
                    continue
                dk = (c['date'] + timedelta(seconds=ist_offset)).strftime('%Y-%m-%d')
                if dk not in day_map:
                    day_map[dk] = []
                day_map[dk].append(c)
            result = []
            for dk in sorted(day_map.keys()):
                day_candles = sorted(day_map[dk], key=lambda x: x['date'])
                if len(day_candles) >= 2:
                    c = day_candles[1]
                    result.append({'time': int(c['date'].timestamp()) + ist_offset,
                                   'high': c['high'], 'low': c['low'],
                                   'open': c['open'], 'close': c['close']})
            return result

        second_30s_oi  = _extract_second_30s_candles(future_index_30s.result() if future_index_30s else [])
        second_30s_ce  = _extract_second_30s_candles(future_ce_30s.result() if future_ce_30s else [])
        second_30s_pe  = _extract_second_30s_candles(future_pe_30s.result() if future_pe_30s else [])

        # ── Data Formatting ──────────────────────────────────────────
        daily_ohlc = {}
        if daily_raw:
            for d in daily_raw:
                dt_str = (d['date'] + timedelta(seconds=ist_offset)).strftime('%Y-%m-%d')
                daily_ohlc[dt_str] = {'close': d['close'], 'high': d['high'], 'low': d['low']}

        ce_candles = format_candles(ce_raw, ist_offset, interval)
        pe_candles = format_candles(pe_raw, ist_offset, interval)

        # ── Intrinsic Levels ─────────────────────────────────────────
        intrinsic_data = None
        if spot_high is not None and spot_low is not None and itm_ce_strike is not None and itm_pe_strike is not None:
            ce_intrinsic = max(spot_high - itm_ce_strike, 0)
            pe_intrinsic = max(itm_pe_strike - spot_low, 0)
            ce_levels = [ce_intrinsic + (step_value * i) for i in range(1, multiplier + 1)]
            pe_levels = [pe_intrinsic + (step_value * i) for i in range(1, multiplier + 1)]
            
            # Serve Dhan security IDs from async-populated cache (never blocks request path).
            ce_sec_id = _dhan_secid_cache.get(ce_symbol) if ce_symbol else None
            pe_sec_id = _dhan_secid_cache.get(pe_symbol) if pe_symbol else None
            _trigger_dhan_secid_fetch(ce_symbol, pe_symbol)
            
            intrinsic_data = {
                'spot_high': spot_high, 'spot_low': spot_low,
                'itm_ce_strike': itm_ce_strike, 'itm_pe_strike': itm_pe_strike,
                'ce_intrinsic': ce_intrinsic, 'pe_intrinsic': pe_intrinsic,
                'ce_levels': ce_levels, 'pe_levels': pe_levels,
                'ce_symbol': ce_symbol, 'pe_symbol': pe_symbol,
                'ce_sec_id': ce_sec_id, 'pe_sec_id': pe_sec_id,
                'multiplier': multiplier
            }

        # ── Collect Historical Max Pain (submitted earlier in parallel) ──
        max_pain_history = future_maxpain.result()

        # Fetch available strikes for custom strike dropdown (needed for Replay mode)
        strikes_list = []
        try:
            # We can use Kite's instrument list for strike discovery even if Fyers is the data provider
            all_inst = kite_service.get_nfo_instruments(symbol)
            if all_inst:
                unique_strikes = sorted(list(set(float(i['strike']) for i in all_inst if i.get('strike') is not None)))
                strikes_list = [{'strike': s} for s in unique_strikes]
        except Exception as e:
            logger.warn(f"[OI-Profile] Strike fetch failed: {e}")

        # ── 6. Update Cache and Return ────────────────────────────────
        fetch_error_msg = _fetch_errors[0] if _fetch_errors and not candles else None
        response_data = {
            'success': True,
            'symbol': symbol,
            'interval': interval,
            'candles': candles,
            'ce_opt_candles': ce_candles,
            'pe_opt_candles': pe_candles,
            'second_30s_candle_oi': second_30s_oi,
            'second_30s_candle_ce': second_30s_ce,
            'second_30s_candle_pe': second_30s_pe,
            'count': len(candles),
            'intrinsic': intrinsic_data,
            'daily_ohlc': daily_ohlc,
            'max_pain_history': max_pain_history,
            'strikes': strikes_list,
            'current_price': candles[-1]['close'] if candles else 0,
            'timestamp': datetime.now().isoformat(),
            'optimized': True,
            'cached': False,
            'fetch_error': fetch_error_msg,
        }
        
        with _candle_cache_lock:
            _candle_response_cache[cache_key] = (response_data, datetime.now().timestamp())
            # Basic cleanup: if cache somehow grows too large, clear it (extra OOM protection)
            if len(_candle_response_cache) > (_MAX_CACHE_ENTRIES * 2): _candle_response_cache.clear()

        return jsonify(response_data)

    except Exception as exc:
        logger.error(f'[OI-Profile] Optimized fetch error: {exc}', exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


def _build_portfolio_broker_configs(username: str) -> list:
    """Build list of active broker configs for portfolio endpoints."""
    from trading_app.app.utils.user_env import UserEnvManager
    user_vars = UserEnvManager.get_all_user_vars(username)
    broker_configs = []

    for instance_num in range(1, 21):
        broker_prefix = f'BROKER_{instance_num}_'
        broker_type = user_vars.get(f'{broker_prefix}TYPE', '').strip().lower()
        if not broker_type or broker_type not in BROKER_TYPE_CONFIGS:
            continue

        type_config = BROKER_TYPE_CONFIGS[broker_type]
        broker_name = user_vars.get(f'{broker_prefix}NAME', '').strip() or broker_type.title()

        all_fields_present = all(
            user_vars.get(f'{broker_prefix}{f}', '').strip()
            for f in type_config['required_fields']
        )
        if not all_fields_present:
            continue

        active_val = user_vars.get(f'{broker_prefix}ACTIVE', 'true').strip().lower()
        if active_val in ('false', '0', 'no'):
            continue

        config = {
            'instance_num': instance_num,
            'broker_type': broker_type,
            'broker_name': broker_name,
            'icon': type_config['icon'],
        }

        if broker_type == 'zerodha':
            config['access_token'] = session.get(f'zerodha_{instance_num}_access_token') or user_vars.get(f'{broker_prefix}ACCESS_TOKEN')
            config['api_key'] = user_vars.get(f'{broker_prefix}API_KEY')
        elif broker_type == 'kotak':
            config['trading_token'] = session.get(f'kotak_{instance_num}_trading_token') or user_vars.get(f'{broker_prefix}TRADING_TOKEN')
            config['consumer_key'] = user_vars.get(f'{broker_prefix}CONSUMER_KEY')
            config['trading_sid'] = session.get(f'kotak_{instance_num}_trading_sid') or user_vars.get(f'{broker_prefix}TRADING_SID')
            config['base_url'] = session.get(f'kotak_{instance_num}_base_url') or user_vars.get(f'{broker_prefix}BASE_URL') or "https://gw-napi.kotaksecurities.com"
        elif broker_type == 'dhan':
            config['access_token'] = session.get(f'dhan_{instance_num}_access_token') or user_vars.get(f'{broker_prefix}ACCESS_TOKEN')
            config['client_id'] = user_vars.get(f'{broker_prefix}CLIENT_ID')
        elif broker_type == 'fyers':
            config['access_token'] = session.get(f'fyers_{instance_num}_access_token') or user_vars.get(f'{broker_prefix}ACCESS_TOKEN')
            config['app_id'] = user_vars.get(f'{broker_prefix}APP_ID')
            config['secret'] = user_vars.get(f'{broker_prefix}SECRET_KEY')

        broker_configs.append(config)

    return broker_configs


@api_bp.route('/portfolio/brokers', methods=['GET'])
@require_user_auth
def get_portfolio_brokers() -> EndpointResponse:
    """Return list of active broker configurations without fetching portfolio data."""
    try:
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'error': 'User not authenticated'}), 401

        broker_configs = _build_portfolio_broker_configs(username)
        brokers = [
            {
                'broker_id': f"{c['broker_type']}_{c['instance_num']}",
                'broker_name': c['broker_name'],
                'broker_type': c['broker_type'],
                'icon': c['icon'],
            }
            for c in broker_configs
        ]
        return jsonify({'success': True, 'brokers': brokers})
    except Exception as e:
        logger.error(f"[portfolio/brokers] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/portfolio/all', methods=['GET'])
@require_user_auth
def get_portfolio_all() -> EndpointResponse:
    """Fetch positions and holdings from active brokers. Optional ?broker_id= to filter to one broker."""
    try:
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'error': 'User not authenticated'}), 401

        broker_configs = _build_portfolio_broker_configs(username)

        # Filter to a single broker when requested
        filter_id = request.args.get('broker_id', '').strip()
        if filter_id:
            broker_configs = [
                c for c in broker_configs
                if f"{c['broker_type']}_{c['instance_num']}" == filter_id
            ]

    except Exception as e:
        logger.error(f"[portfolio/all] Config load error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

    def _normalize_position(raw: dict, broker_type: str) -> dict:
        if broker_type == 'zerodha':
            qty = raw.get('quantity', 0)
            avg = raw.get('average_price', 0)
            ltp = raw.get('last_price', 0)
            return {
                'symbol': raw.get('tradingsymbol', ''),
                'qty': qty,
                'avg_price': avg,
                'ltp': ltp,
                'pnl': raw.get('pnl', 0),
                'product': raw.get('product', ''),
                'exchange': raw.get('exchange', ''),
            }
        elif broker_type == 'dhan':
            qty = raw.get('netQty', 0)
            avg = raw.get('avgCostPrice', 0)
            ltp = raw.get('lastTradedPrice', 0)
            return {
                'symbol': raw.get('tradingSymbol', ''),
                'qty': qty,
                'avg_price': avg,
                'ltp': ltp,
                'pnl': raw.get('unrealizedProfit', 0),
                'product': raw.get('productType', ''),
                'exchange': raw.get('exchangeSegment', ''),
            }
        elif broker_type == 'fyers':
            qty = raw.get('netQty', 0)
            avg = raw.get('avgPrice', 0)
            ltp = raw.get('ltp', 0)
            return {
                'symbol': raw.get('symbol', ''),
                'qty': qty,
                'avg_price': avg,
                'ltp': ltp,
                'pnl': raw.get('unrealizedProfit', 0),
                'product': raw.get('productType', ''),
                'exchange': raw.get('exchange', ''),
            }
        elif broker_type == 'kotak':
            qty = raw.get('flQty', raw.get('netQty', 0))
            avg = raw.get('avgPrice', 0)
            ltp = raw.get('ltp', 0)
            return {
                'symbol': raw.get('trdSym', raw.get('tradingSymbol', '')),
                'qty': qty,
                'avg_price': avg,
                'ltp': ltp,
                'pnl': raw.get('rpnl', 0),
                'product': raw.get('prod', ''),
                'exchange': raw.get('exSeg', ''),
            }
        return raw

    def _normalize_holding(raw: dict, broker_type: str) -> dict:
        exchange = 'NSE'
        sec_id   = ''
        if broker_type == 'zerodha':
            qty      = raw.get('quantity', 0)
            avg      = raw.get('average_price', 0)
            ltp      = raw.get('last_price', 0)
            close    = raw.get('close_price', 0)
            symbol   = raw.get('tradingsymbol', '')
            exchange = raw.get('exchange', 'NSE')
        elif broker_type == 'dhan':
            qty      = raw.get('totalQty', 0)
            avg      = raw.get('avgCostPrice', 0)
            ltp      = raw.get('lastTradedPrice', 0)
            close    = raw.get('closingPrice', 0)
            symbol   = raw.get('tradingSymbol', '')
            sec_id   = str(raw.get('securityId', ''))
            exch_seg = raw.get('exchangeSegment', 'NSE_EQ')
            exchange = 'BSE' if 'BSE' in exch_seg.upper() else 'NSE'
        elif broker_type == 'fyers':
            qty      = raw.get('quantity', 0)
            avg      = raw.get('costPrice', 0)
            ltp      = raw.get('ltp', 0)
            close    = raw.get('close_price', raw.get('closePrice', 0))
            symbol   = raw.get('symbol', '')  # format: NSE:RELIANCE-EQ
            exchange = 'BSE' if symbol.startswith('BSE:') else 'NSE'
        else:
            qty, avg, ltp, close, symbol = 0, 0, 0, 0, ''

        current_value  = round(ltp * qty, 2)
        pnl            = round((ltp - avg) * qty, 2) if avg else 0
        pnl_pct        = round((ltp - avg) / avg * 100, 2) if avg else 0
        day_change     = round(ltp - close, 2) if close else 0
        day_change_pct = round((ltp - close) / close * 100, 2) if close else 0

        return {
            'symbol': symbol,
            'exchange': exchange,
            'sec_id': sec_id,
            'qty': qty,
            'avg_price': avg,
            'ltp': ltp,
            'close_price': close,
            'current_value': current_value,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'day_change': day_change,
            'day_change_pct': day_change_pct,
        }

    def _fetch_broker_portfolio(b_conf: dict) -> dict:
        broker_type = b_conf['broker_type']
        instance_num = b_conf['instance_num']
        broker_id = f"{broker_type}_{instance_num}"
        result = {
            'broker_id': broker_id,
            'broker_name': b_conf['broker_name'],
            'broker_type': broker_type,
            'icon': b_conf['icon'],
            'positions': [],
            'holdings': [],
            'positions_error': None,
            'holdings_error': None,
        }

        try:
            if broker_type == 'zerodha':
                token = b_conf.get('access_token')
                api_key = b_conf.get('api_key') or os.getenv('API_KEY') or 'dummy'
                if not token:
                    result['positions_error'] = 'Not connected'
                    result['holdings_error'] = 'Not connected'
                    return result
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key=api_key)
                apply_kite_proxy(kite)
                kite.set_access_token(token)
                try:
                    pos_resp = kite.positions()
                    net = pos_resp.get('net', [])
                    day = pos_resp.get('day', [])
                    seen = {}
                    for p in day + net:
                        sym = p.get('tradingsymbol')
                        if sym not in seen:
                            seen[sym] = p
                    result['positions'] = [
                        _normalize_position(p, 'zerodha')
                        for p in seen.values()
                        if p.get('quantity', 0) != 0
                    ]
                except Exception as e:
                    result['positions_error'] = str(e)
                try:
                    result['holdings'] = [
                        _normalize_holding(h, 'zerodha')
                        for h in kite.holdings()
                        if h.get('quantity', 0) != 0
                    ]
                except Exception as e:
                    result['holdings_error'] = str(e)

            elif broker_type == 'dhan':
                from trading_app.service.dhan_order_services import DhanOrderService
                svc = DhanOrderService(
                    access_token=b_conf.get('access_token'),
                    client_id=b_conf.get('client_id'),
                )
                pos = svc.get_positions()
                if pos.get('success'):
                    raw_list = pos.get('positions', [])
                    if isinstance(raw_list, list):
                        result['positions'] = [
                            _normalize_position(p, 'dhan')
                            for p in raw_list
                            if p.get('netQty', 0) != 0
                        ]
                    else:
                        result['positions_error'] = 'Unexpected positions format'
                else:
                    result['positions_error'] = pos.get('error', 'Unknown error')
                hld = svc.get_holdings()
                if hld.get('success'):
                    raw_list = hld.get('holdings', [])
                    if isinstance(raw_list, list):
                        result['holdings'] = [
                            _normalize_holding(h, 'dhan')
                            for h in raw_list
                            if h.get('totalQty', 0) != 0
                        ]
                    else:
                        result['holdings_error'] = 'Unexpected holdings format'
                else:
                    result['holdings_error'] = hld.get('error', 'Unknown error')

            elif broker_type == 'fyers':
                from trading_app.service.fyers_order_services import FyersOrderService
                svc = FyersOrderService(
                    app_id=b_conf.get('app_id'),
                    access_token=b_conf.get('access_token'),
                    secret_key=b_conf.get('secret'),
                )
                pos = svc.get_positions()
                if pos.get('success'):
                    result['positions'] = [
                        _normalize_position(p, 'fyers')
                        for p in pos.get('positions', [])
                        if p.get('netQty', 0) != 0
                    ]
                else:
                    result['positions_error'] = pos.get('error', 'Unknown error')
                hld = svc.get_holdings()
                if hld.get('success'):
                    result['holdings'] = [
                        _normalize_holding(h, 'fyers')
                        for h in hld.get('holdings', [])
                        if h.get('quantity', 0) != 0
                    ]
                else:
                    result['holdings_error'] = hld.get('error', 'Unknown error')

            elif broker_type == 'kotak':
                from trading_app.service.kotak_order_services import KotakOrderService
                svc = KotakOrderService(consumer_key=b_conf.get('consumer_key'))
                svc.trading_token = b_conf.get('trading_token')
                svc.trading_sid = b_conf.get('trading_sid')
                if b_conf.get('base_url'):
                    svc._order_base_url = b_conf.get('base_url')
                pos = svc.get_positions()
                if pos.get('success'):
                    result['positions'] = [
                        _normalize_position(p, 'kotak')
                        for p in pos.get('positions', [])
                        if p.get('flQty', p.get('netQty', 0)) != 0
                    ]
                else:
                    result['positions_error'] = pos.get('error', 'Unknown error')
                result['holdings_error'] = 'Not supported by Kotak'

        except Exception as e:
            logger.error(f"[portfolio/all] Broker {broker_id} fetch error: {e}", exc_info=True)
            result['positions_error'] = str(e)
            result['holdings_error'] = str(e)

        return result

    try:
        if broker_configs:
            with ThreadPoolExecutor(max_workers=min(10, len(broker_configs))) as executor:
                broker_results = list(executor.map(_fetch_broker_portfolio, broker_configs))
        else:
            broker_results = []

        positions_by_broker = []
        holdings_by_broker = []
        for r in broker_results:
            positions_by_broker.append({
                'broker_id': r['broker_id'],
                'broker_name': r['broker_name'],
                'broker_type': r['broker_type'],
                'icon': r['icon'],
                'data': r['positions'],
                'error': r['positions_error'],
            })
            holdings_by_broker.append({
                'broker_id': r['broker_id'],
                'broker_name': r['broker_name'],
                'broker_type': r['broker_type'],
                'icon': r['icon'],
                'data': r['holdings'],
                'error': r['holdings_error'],
            })

        return jsonify({
            'success': True,
            'positions': positions_by_broker,
            'holdings': holdings_by_broker,
        })

    except Exception as e:
        logger.error(f"[portfolio/all] Aggregation error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/portfolio/cnc-order', methods=['POST'])
@require_user_auth
def place_portfolio_cnc_order() -> EndpointResponse:
    """Place a CNC (Cash & Carry / Delivery) buy order for a holding on a specific broker."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        broker_id  = data.get('broker_id', '').strip()
        symbol     = data.get('symbol', '').strip().upper()
        exchange   = data.get('exchange', 'NSE').strip().upper()
        qty        = int(data.get('qty', 0))
        price      = float(data.get('price', 0) or 0)
        order_type = data.get('order_type', 'LIMIT').upper()  # LIMIT or MARKET
        sec_id     = data.get('sec_id', '').strip()

        if not broker_id or not symbol or qty <= 0:
            return jsonify({'success': False, 'error': 'broker_id, symbol and qty are required'}), 400
        if order_type == 'LIMIT' and price <= 0:
            return jsonify({'success': False, 'error': 'price required for LIMIT order'}), 400

        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        broker_configs = _build_portfolio_broker_configs(username)
        matched = [c for c in broker_configs if f"{c['broker_type']}_{c['instance_num']}" == broker_id]
        if not matched:
            return jsonify({'success': False, 'error': f'Broker {broker_id} not found or not active'}), 404

        conf        = matched[0]
        broker_type = conf['broker_type']
        order_id    = None
        error_msg   = None

        if broker_type == 'zerodha':
            from kiteconnect import KiteConnect
            from trading_app.service.kite_order_services import KiteService
            token   = conf.get('access_token')
            api_key = conf.get('api_key') or os.getenv('API_KEY') or 'dummy'
            if not token:
                return jsonify({'success': False, 'error': 'Zerodha not connected'}), 400
            kite = KiteConnect(api_key=api_key)
            apply_kite_proxy(kite)
            kite.set_access_token(token)
            kite_svc = KiteService(kite_instance=kite)
            kite_order_type = kite.ORDER_TYPE_LIMIT if order_type == 'LIMIT' else kite.ORDER_TYPE_MARKET
            try:
                order_id = kite_svc._safe_place_order(
                    variety=kite.VARIETY_REGULAR,
                    exchange=exchange,
                    tradingsymbol=symbol,
                    transaction_type=kite.TRANSACTION_TYPE_BUY,
                    quantity=qty,
                    product=kite.PRODUCT_CNC,
                    order_type=kite_order_type,
                    price=price if order_type == 'LIMIT' else None,
                    market_protection=1 if order_type == 'MARKET' else None,
                )
            except Exception as e:
                error_msg = str(e)

        elif broker_type == 'dhan':
            from trading_app.service.dhan_order_services import DhanOrderService
            svc = DhanOrderService(
                access_token=conf.get('access_token', ''),
                client_id=conf.get('client_id', ''),
            )
            if not sec_id:
                sec_id = svc.get_security_id(symbol) or ''
            if not sec_id:
                return jsonify({'success': False, 'error': f'Could not resolve Dhan security ID for {symbol}'}), 400
            exch_seg = 'BSE_EQ' if exchange == 'BSE' else 'NSE_EQ'
            result   = svc.place_order(
                security_id=sec_id,
                transaction_type='BUY',
                quantity=qty,
                order_type=order_type,
                product_type='CNC',
                exchange_segment=exch_seg,
                price=price if order_type == 'LIMIT' else 0.0,
            )
            if result.get('success'):
                order_id = result.get('order_id')
            else:
                error_msg = result.get('error', 'Dhan order failed')

        elif broker_type == 'fyers':
            from trading_app.service.fyers_order_services import FyersOrderService
            svc = FyersOrderService(
                access_token=conf.get('access_token', ''),
                app_id=conf.get('app_id', ''),
                secret=conf.get('secret', ''),
            )
            exch_prefix = 'BSE' if exchange == 'BSE' else 'NSE'
            # Strip existing exchange prefix if symbol already has one
            bare_sym = symbol.split(':', 1)[-1].split('-')[0] if ':' in symbol or '-' in symbol else symbol
            fyers_sym = f"{exch_prefix}:{bare_sym}-EQ"
            fyers_order_type = 1 if order_type == 'LIMIT' else 2
            result = svc.place_order(
                symbol=fyers_sym,
                side=1,
                quantity=qty,
                order_type=fyers_order_type,
                product_type='CNC',
                limit_price=price if order_type == 'LIMIT' else 0.0,
            )
            if result.get('success'):
                order_id = result.get('order_id')
            else:
                error_msg = result.get('error', 'Fyers order failed')

        elif broker_type == 'kotak':
            from trading_app.service.kotak_order_services import KotakOrderService
            svc = KotakOrderService(
                consumer_key=conf.get('consumer_key', ''),
                trading_token=conf.get('trading_token', ''),
                trading_sid=conf.get('trading_sid', ''),
                base_url=conf.get('base_url', 'https://gw-napi.kotaksecurities.com'),
            )
            exch_seg = 'bse_cm' if exchange == 'BSE' else 'nse_cm'
            kotak_order_type = 'LMT' if order_type == 'LIMIT' else 'MKT'
            result = svc.place_order(
                tradingsymbol=symbol,
                transaction_type='BUY',
                price=price if order_type == 'LIMIT' else 0.0,
                quantity=qty,
                order_type=kotak_order_type,
                product_type='CNC',
                exchange_segment=exch_seg,
            )
            if result.get('success'):
                order_id = result.get('order_id')
            else:
                error_msg = result.get('error', 'Kotak order failed')

        else:
            return jsonify({'success': False, 'error': f'Unsupported broker type: {broker_type}'}), 400

        if error_msg:
            logger.error(f"[portfolio/cnc-order] {broker_type}: {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 400

        logger.info(f"[portfolio/cnc-order] {broker_type} CNC BUY {symbol} x{qty} @ {price} → order_id={order_id}")
        return jsonify({'success': True, 'order_id': str(order_id)})

    except Exception as e:
        logger.error(f"[portfolio/cnc-order] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/proxy-status', methods=['GET'])
def proxy_status():
    """Returns static IP proxy reachability status."""
    host = os.getenv('STATIC_IP_HOST', '').strip()
    if not host or not os.getenv('STATIC_IP_KEY', '').strip():
        return jsonify({'proxy_required': False, 'tunnel_up': True})
    try:
        import socket
        parts = host.rsplit(':', 1)
        hostname = parts[0]
        port = int(parts[1]) if len(parts) == 2 else 443
        s = socket.create_connection((hostname, port), timeout=3)
        s.close()
        up = True
    except Exception:
        up = False
    return jsonify({'proxy_required': True, 'tunnel_up': up})


# ── Historic OI endpoints ─────────────────────────────────────────────────────

@api_bp.route('/oi-historic', methods=['GET'])
@require_user_auth
def get_oi_historic():
    """Return all historic OI records sorted newest-first."""
    from trading_app.dashboard.oi_historic_data import get_all_records
    records = get_all_records()
    return jsonify({'success': True, 'records': records})


@api_bp.route('/oi-historic/record', methods=['POST'])
@require_user_auth
def record_oi_historic():
    """Manually trigger OI fetch for all symbols and store today's record."""
    from trading_app.dashboard.oi_historic_data import fetch_and_store_all, get_all_records
    from trading_app.service.provider_logic import get_data_provider
    provider = get_data_provider(user='Mine')
    results = fetch_and_store_all(provider=provider)
    errors = [r for r in results if not r.get('success')]
    records = get_all_records()
    return jsonify({
        'success': True,
        'results': results,
        'errors': errors,
        'records': records,
    })


@api_bp.route('/oi-historic/<date>/<symbol>', methods=['DELETE'])
@require_user_auth
def delete_oi_historic(date: str, symbol: str):
    """Delete a specific historic OI record by date and symbol."""
    from trading_app.dashboard.oi_historic_data import delete_record
    deleted = delete_record(date, symbol.upper())
    if not deleted:
        return jsonify({'success': False, 'error': 'Record not found'}), 404
    return jsonify({'success': True})


# ── Error handlers ────────────────────────────────────────────────────────────

@api_bp.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({'error': 'Endpoint not found'}), 404


@api_bp.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    logger.error(f"Server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500
