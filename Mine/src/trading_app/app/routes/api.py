"""API routes for trading data endpoints."""
import json
import logging
import math
import os
import threading
import time as _time
import uuid
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

# RTP optimisation cache — persisted to disk so results survive restarts
_RTP_OPT_CACHE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'utils', 'rtp_opt_cache.json')
)

# RTP algo state and history files (read directly — no class import needed)
_RTP_STATE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_state.json')
)
_RTP_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_history.json')
)
_RTP_ALL_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_all_history.json')
)
# RTP 30s variant (same package, _30s-suffixed state/history files)
_RTP30S_STATE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_state_30s.json')
)
_RTP30S_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_history_30s.json')
)
_RTP30S_ALL_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_all_history_30s.json')
)
# RTP 2m variant (same package, _2m-suffixed state/history files)
_RTP2M_STATE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_state_2m.json')
)
_RTP2M_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_history_2m.json')
)
_RTP2M_ALL_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_all_history_2m.json')
)
# RTP 3m variant (same package, _3m-suffixed state/history files)
_RTP3M_STATE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_state_3m.json')
)
_RTP3M_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_history_3m.json')
)
_RTP3M_ALL_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_all_history_3m.json')
)
# RTP 5m variant (same package, _5m-suffixed state/history files)
_RTP5M_STATE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_state_5m.json')
)
_RTP5M_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_history_5m.json')
)
_RTP5M_ALL_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'rtp_railway_track', 'rtp_trades_all_history_5m.json')
)
_NIFTY_FYERS_IDX = 'NSE:NIFTY50-INDEX'

# 2nd 30-Sec Candle algo state and history files
_SC_STATE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'second_candle', 'sc_state.json')
)
_SC_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'second_candle', 'sc_trades_history.json')
)
_SC_ALL_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'second_candle', 'sc_trades_all_history.json')
)

# Intrinsic ATM Range Breakout algo (paper trade) state and history files
_IR_STATE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'intrinsic_range', 'intrinsic_range_state.json')
)
_IR_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'intrinsic_range', 'intrinsic_range_trades_history.json')
)
_IR_ALL_HISTORY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'intrinsic_range', 'intrinsic_range_trades_all_history.json')
)

def _algo_option_live(trade: dict, provider) -> dict:
    """Live option-premium P&L for an active algo trade.

    The algos always hold a long option (BUY CE/PE), so profit is simply
    current premium − entry premium. Fetches the option LTP via the traded
    fyers symbol and returns entry price, current price and premium-based
    profit in points and rupees. Returns partial/empty data when the symbol
    or LTP is unavailable.
    """
    result: dict = {'opt_entry_price': trade.get('opt_entry_price')}
    try:
        broker_entries = trade.get('broker_entries', []) or []
        fyers_sym = next((e['fyers_sym'] for e in broker_entries if e.get('fyers_sym')), '')
        if not fyers_sym:
            return result
        ltp_data = provider.ltp([fyers_sym])
        raw = ltp_data.get(fyers_sym, {}).get('last_price', 0)
        opt_cur = round(float(raw), 2) if raw else None
        result['opt_current_price'] = opt_cur
        opt_entry = result['opt_entry_price']
        if opt_entry is not None and opt_cur is not None:
            opt_pnl_pts = round(opt_cur - float(opt_entry), 2)
            total_qty   = sum(float(e.get('quantity', 0) or 0) for e in broker_entries)
            result['opt_pnl_pts'] = opt_pnl_pts
            result['opt_pnl_inr'] = round(opt_pnl_pts * total_qty, 2) if total_qty else None
    except Exception as _e:
        logger.warning(f'[algo] option live fetch failed: {_e}')
    return result

def _load_opt_cache() -> dict:
    try:
        if os.path.exists(_RTP_OPT_CACHE_PATH):
            with open(_RTP_OPT_CACHE_PATH, 'r') as _f:
                return json.load(_f)
    except Exception:
        pass
    return {}

def _save_opt_cache(cache: dict) -> None:
    try:
        with open(_RTP_OPT_CACHE_PATH, 'w') as _f:
            json.dump(cache, _f, indent=2, default=str)
    except Exception as _e:
        logger.warning(f"RTP opt cache write failed: {_e}")

# Swing Momentum optimisation cache
_SM_OPT_CACHE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'utils', 'sm_opt_cache.json')
)

def _load_sm_opt_cache() -> dict:
    try:
        if os.path.exists(_SM_OPT_CACHE_PATH):
            with open(_SM_OPT_CACHE_PATH, 'r') as _f:
                return json.load(_f)
    except Exception:
        pass
    return {}

def _save_sm_opt_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_SM_OPT_CACHE_PATH), exist_ok=True)
        with open(_SM_OPT_CACHE_PATH, 'w') as _f:
            json.dump(cache, _f, indent=2, default=str)
    except Exception as _e:
        logger.warning(f"SM opt cache write failed: {_e}")

# In-memory task store for long-running SM optimisation background jobs
_sm_opt_tasks: Dict[str, Dict] = {}
_sm_opt_tasks_lock = threading.Lock()

# In-memory task store for long-running RTP optimisation background jobs
_rtp_opt_tasks: Dict[str, Dict] = {}
_rtp_opt_tasks_lock = threading.Lock()

# In-memory task store for long-running 2nd-Candle optimisation background jobs
_sc_opt_tasks: Dict[str, Dict] = {}
_sc_opt_tasks_lock = threading.Lock()

# Rankings cache — keyed by index name, expires after 15 min
_sm_rankings_cache: Dict[str, tuple] = {}
_SM_RANKINGS_TTL = 900  # seconds



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
        # Only the access token is strictly required — the client_id is fetched
        # from the Dhan profile during verification (same as on placing an order).
        'required_fields': ['ACCESS_TOKEN'],
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
            env_updates = {}

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
                    # Mirror the place_order auth flow: only the access token is
                    # strictly required. verify_credentials() resolves the client_id
                    # from the profile, so a missing/stale client_id self-heals here.
                    if access_token:
                        from trading_app.service.dhan_order_services import DhanOrderService
                        dhan = DhanOrderService(access_token=access_token, client_id=client_id)
                        if dhan.verify_credentials():
                            is_logged_in = True
                            msg_status = 'Connected'
                            # Persist the client_id resolved from the profile if it
                            # was missing or has drifted from what's stored.
                            resolved_client_id = dhan.client_id
                            if resolved_client_id and resolved_client_id != client_id:
                                s_updates[f'dhan_{instance_num}_client_id'] = resolved_client_id
                                env_updates[f'BROKER_{instance_num}_CLIENT_ID'] = resolved_client_id
                            s_updates[f'dhan_{instance_num}_access_token'] = access_token
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

            return b_conf, is_logged_in, msg_status, s_updates, s_pops, env_updates

        # Run verification checks concurrently
        if broker_configs:
            with ThreadPoolExecutor(max_workers=min(10, len(broker_configs))) as executor:
                results = list(executor.map(verify_broker_status, broker_configs))
        else:
            results = []

        # Process results sequentially to build list and safely apply session mutations
        for b_conf, is_logged_in, msg_status, s_updates, s_pops, env_updates in results:
            instance_num = b_conf['instance_num']
            broker_type = b_conf['broker_type']
            type_config = b_conf['type_config']

            # Apply session changes safely in main thread (thread-safe, persists cookie securely)
            for k, v in s_updates.items(): session[k] = v
            for k in s_pops: session.pop(k, None)
            if s_updates: session.permanent = True

            # Persist any recovered credentials (e.g. Dhan client_id resolved from
            # the profile) back to the user's .env so the next check/order has them.
            if env_updates:
                try:
                    UserEnvManager.save_user_vars(username, env_updates)
                    logger.info(f"[available-brokers] Updated {username}.env for {broker_type} instance {instance_num}: {list(env_updates.keys())}")
                except Exception as e:
                    logger.error(f"[available-brokers] Failed to persist env updates for {broker_type} instance {instance_num}: {e}")
            
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


@api_bp.route('/cpr-filter/expiry-hl-breakout', methods=['GET'])
@limiter.exempt
def get_expiry_hl_breakout_results() -> EndpointResponse:
    """Scan F&O stocks for a monthly-expiry-cycle High/Low breakout on the
    selected timeframe (60minute default, or day) — same rule as the
    Monthly Expiry Breakout filter (touch-then-close-beyond the expiry
    level, close beyond every EMA 20/50/100/200, and touching at least
    one of them)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error

    current_kite = get_data_provider()
    if not current_kite:
        return jsonify({'success': False, 'error': 'Data Provider initialization failed.'}), 401

    date_str = request.args.get('date')
    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    timeframe = request.args.get('timeframe', '60minute')
    if timeframe not in ('60minute', 'day'):
        timeframe = '60minute'

    from trading_app.app.utils.cache import cpr_filter_cache
    cache_user = session.get('username', 'anonymous')
    cache_date = date_str or datetime.now().strftime('%Y-%m-%d')
    cache_key = f"cpr_filter_expiry_hl:{cache_user}:{cache_date}:{timeframe}"

    refresh = request.args.get('refresh', 'false').lower() == 'true'
    if not refresh:
        cached_response = cpr_filter_cache.get(cache_key)
        if cached_response is not None:
            return jsonify(cached_response)
    else:
        cpr_filter_cache.delete(cache_key)

    try:
        if not hasattr(current_kite, 'access_token') or not current_kite.access_token:
            logger.warning("Expiry H/L breakout request: KiteConnect instance has no access token")
            return jsonify({
                'success': False,
                'error': 'No valid access token on KiteConnect instance. Please login again.',
                'auth_error': True
            }), 401

        from trading_app.filters.expiry_hl_scanner import filter_expiry_hl_breakout
        cpr_service = _get_cpr_service(current_kite)
        results = filter_expiry_hl_breakout(cpr_service, root_date=target_date, timeframe=timeframe)

        payload = {
            'success': True,
            'buy': results.get('buy', []),
            'sell': results.get('sell', []),
            'timeframe': timeframe,
            'date': target_date.strftime('%Y-%m-%d') if target_date else datetime.now().strftime('%Y-%m-%d')
        }

        cpr_filter_cache.set(cache_key, payload, timeout=120)  # cache for 2 minutes
        return jsonify(payload)
    except Exception as e:
        logger.error(f"Error in Expiry H/L breakout scanner: {type(e).__name__}: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str or 'invalid' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': f'Expiry H/L breakout error: {str(e)}'}), 500


# ====================== NOTIFICATIONS ======================

@api_bp.route('/notifications', methods=['GET'])
@limiter.exempt
def list_notifications_route() -> EndpointResponse:
    """Latest notifications for the bell dropdown (no full payload)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        from trading_app.service.notification_service import list_notifications, unread_count
        limit = request.args.get('limit', 50, type=int)
        return jsonify({
            'success': True,
            'notifications': list_notifications(limit=limit),
            'unread_count': unread_count(),
        })
    except Exception as e:
        logger.error(f"Error listing notifications: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/notifications/<int:notification_id>', methods=['GET'])
@limiter.exempt
def get_notification_route(notification_id: int) -> EndpointResponse:
    """Full notification payload for the detail popup."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        from trading_app.service.notification_service import get_notification
        notification = get_notification(notification_id)
        if notification is None:
            return jsonify({'success': False, 'error': 'Notification not found'}), 404
        return jsonify({'success': True, 'notification': notification})
    except Exception as e:
        logger.error(f"Error fetching notification {notification_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/notifications/<int:notification_id>/read', methods=['POST'])
@csrf.exempt
@limiter.exempt
def mark_notification_read_route(notification_id: int) -> EndpointResponse:
    """Mark a notification as read (called when its detail popup is opened)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        from trading_app.service.notification_service import mark_read
        mark_read(notification_id)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error marking notification {notification_id} read: {e}", exc_info=True)
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


# EMA Narrow scans take minutes, so they run as background jobs: the endpoint
# returns immediately with 'running' + progress and the frontend polls until done.
_ema_narrow_jobs: dict = {}
_ema_narrow_jobs_lock = threading.Lock()


@api_bp.route('/ema-narrow-filter', methods=['GET'])
@limiter.exempt
def get_ema_narrow_filter_results() -> EndpointResponse:
    """Scan ALL NSE equity stocks where EMA 20/50/100/200 are compressed within a
    tight % band on every timeframe of the selected group (mwd / mw / wd).

    Long-running: the first call starts a background scan and returns
    {'status': 'running', 'progress': {...}}; poll the same URL until
    {'status': 'done'} arrives with results. Finished scans are cached for
    6 hours; pass refresh=1 to force a fresh scan."""
    auth_error = check_auth()
    if auth_error:
        return auth_error

    current_kite = get_data_provider()
    if not current_kite:
        return jsonify({'success': False, 'error': 'Data Provider initialization failed.'}), 401

    date_str = request.args.get('date')
    group    = request.args.get('group', 'wd')  # combos: mwd/mw/wd, singles: d/w/m
    if group not in ('mwd', 'mw', 'wd', 'd', 'w', 'm'):
        group = 'wd'
    ema_set  = request.args.get('emas', '20_50_100')
    if ema_set not in ('20_50_100_200', '20_50_100'):
        ema_set = '20_50_100'
    try:
        threshold = float(request.args.get('threshold', 3.0))
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid threshold. Use a number (percent).'}), 400
    threshold = max(0.1, min(threshold, 10.0))
    force_refresh = request.args.get('refresh') == '1'

    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    from trading_app.app.utils.cache import cpr_filter_cache  # reuse same cache backend
    cache_date = date_str or datetime.now().strftime('%Y-%m-%d')
    job_key    = f"{cache_date}:{group}:{threshold}:{ema_set}"
    cache_key  = f"ema_narrow_v5:{job_key}"  # v5: ETFs, liquid & mutual funds excluded

    with _ema_narrow_jobs_lock:
        job = _ema_narrow_jobs.get(job_key)

        # A scan for this exact key is already running — never start a duplicate.
        # Include the partial results gathered so far, so the UI fills up live.
        if job and job['status'] == 'running':
            partial = job.get('partial') or {}
            return jsonify({
                'success':    True,
                'status':     'running',
                'progress':   job.get('progress', {}),
                'started_at': job.get('started_at'),
                'results':    partial.get('results', []),
                'nearest':    [],
            })

        # Previous run failed — report it once, then allow a retry
        if job and job['status'] == 'error':
            _ema_narrow_jobs.pop(job_key, None)
            return jsonify({'success': False, 'status': 'error', 'error': job.get('error', 'Scan failed')}), 500

        if not force_refresh:
            cached = cpr_filter_cache.get(cache_key)
            if cached is not None:
                return jsonify(cached)
            if job and job['status'] == 'done' and job.get('result'):
                return jsonify(job['result'])

        # Start a new background scan
        job = {
            'status':     'running',
            'progress':   {'done': 0, 'total': 0},
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        _ema_narrow_jobs[job_key] = job

    def _run_scan(kite_ref, job_ref):
        try:
            from trading_app.filters.ema_rsi_filter import EmaRsiFilterService
            svc = EmaRsiFilterService(kite_instance=kite_ref)

            def on_progress(done, total, partial=None):
                job_ref['progress'] = {'done': done, 'total': total}
                if partial is not None:
                    job_ref['partial'] = partial

            result = svc.run_ema_narrow_filter(root_date=target_date, group=group,
                                               threshold_pct=threshold,
                                               ema_set=ema_set,
                                               progress_cb=on_progress)
            payload = {
                'success':      True,
                'status':       'done',
                'results':      result.get('results', []),
                'nearest':      result.get('nearest', []),
                'threshold':    threshold,
                'group':        group,
                'emas':         ema_set,
                'date':         cache_date,
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            cpr_filter_cache.set(cache_key, payload, timeout=6 * 3600)
            job_ref['result'] = payload
            job_ref['status'] = 'done'
        except Exception as e:
            logger.error(f"EMA Narrow scan error: {type(e).__name__}: {e}", exc_info=True)
            job_ref['error'] = str(e)
            job_ref['status'] = 'error'

    threading.Thread(target=_run_scan, args=(current_kite, job),
                     name=f"ema-narrow-{job_key}", daemon=True).start()

    return jsonify({
        'success':    True,
        'status':     'running',
        'progress':   job['progress'],
        'started_at': job['started_at'],
        'results':    [],
        'nearest':    [],
    })


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


@api_bp.route('/backtest/symbols', methods=['GET'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def get_backtest_symbols():
    """Fetch all unique future stocks and indices for backtesting, along
    with each symbol's current lot size (so the Backtest UI can update the
    lot-size field automatically when the symbol changes)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        import json
        import os
        from datetime import date as _date

        # Path to cached NFO instruments
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.cache', 'nfo_instruments.json')

        if not os.path.exists(cache_path):
            return jsonify({'success': False, 'error': 'NFO instruments cache not found. Please login to refresh.'}), 404

        with open(cache_path, 'r') as f:
            instruments = json.load(f)

        # Filter unique names for futures, and for each name pick the lot
        # size of its NEAREST expiry >= today (lot sizes get periodically
        # revised by the exchange — older/expired contracts still sitting
        # in the cache can carry a stale value, so a straight "any entry"
        # pick is unreliable; nearest-upcoming-expiry reflects what's
        # actually tradable right now). Falls back to the latest available
        # expiry if every cached contract for that name has already expired.
        today_str = _date.today().isoformat()
        by_name = {}   # name -> list of (expiry_str, lot_size)
        for inst in instruments:
            if inst.get('instrument_type') != 'FUT':
                continue
            name = inst.get('name')
            expiry = inst.get('expiry')
            lot_size = inst.get('lot_size')
            if not name or not lot_size:
                continue
            by_name.setdefault(name, []).append((expiry or '', lot_size))

        lot_sizes = {}
        for name, rows in by_name.items():
            rows.sort(key=lambda r: r[0])
            upcoming = [r for r in rows if r[0] >= today_str]
            lot_sizes[name] = (upcoming[0] if upcoming else rows[-1])[1]

        # Combine and sort
        all_symbols = sorted(by_name.keys())
        indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX']
        # Known index lot sizes not present in the NFO (equity F&O) cache —
        # SENSEX trades on BSE/BFO, not NFO, so it never appears above.
        for idx, fallback_lot in (('NIFTY', 65), ('BANKNIFTY', 30),
                                  ('FINNIFTY', 60), ('MIDCPNIFTY', 120),
                                  ('SENSEX', 20)):
            lot_sizes.setdefault(idx, fallback_lot)

        return jsonify({
            'success': True,
            'symbols': all_symbols,
            'indices': indices,
            'lot_sizes': lot_sizes,
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


@api_bp.route('/backtest/rtp', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_rtp_backtest_api():
    """Run Railway Track Pattern (RTP) backtest."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json()
        symbol         = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date')
        end_date_str   = data.get('end_date')
        interval       = data.get('interval', 'minute')
        entry_mode     = data.get('entry_mode', 'RTP(20 & 9)')
        use_adx        = bool(data.get('use_adx', True))
        adx_thresh     = float(data.get('adx_thresh', 25.0))
        sl_points      = float(data['sl_points'])     if data.get('sl_points')     else None
        tgt_points     = float(data['tgt_points'])    if data.get('tgt_points')    else None
        trail_points   = float(data['trail_points'])  if data.get('trail_points')  else None
        exit_on        = data.get('exit_on', 'value')
        confirm_bars       = int(data.get('confirm_bars', 0) or 0)
        strict_pattern     = bool(data.get('strict_pattern', False))
        min_rail_gap_atr   = float(data.get('min_rail_gap_atr', 0) or 0)
        max_trades_per_day = int(data['max_trades_per_day']) if data.get('max_trades_per_day') else None
        max_consec_sl      = int(data['max_consec_sl'])      if data.get('max_consec_sl')      else None

        if not symbol or not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400

        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401

        # Resolve Fyers symbol string
        fyers_indices = {
            'NIFTY':      'NSE:NIFTY50-INDEX',
            'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':   'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':     'BSE:SENSEX-INDEX',
        }
        kite_indices = {
            'NIFTY': 256265, 'BANKNIFTY': 260105,
            'FINNIFTY': 257801, 'MIDCPNIFTY': 288009,
        }

        if hasattr(current_kite, 'fyers'):
            instrument_token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
        else:
            instrument_token = kite_indices.get(symbol, symbol)

        candles = current_kite.historical_data(
            instrument_token=instrument_token,
            from_date=start_date_str,
            to_date=end_date_str,
            interval=interval,
            use_cache=False,
        )

        if not candles:
            return jsonify({'success': False, 'error': 'No historical data found for the given range'}), 404

        import pandas as pd
        from trading_app.Backtest.rtp_backtest_engine import RTPBacktestEngine

        interval_minutes_map = {
            '30second': 0.5,
            'minute': 1, '2minute': 2, '3minute': 3,
            '5minute': 5, '10minute': 10, '15minute': 15,
            '30minute': 30, '60minute': 60,
        }
        interval_minutes = interval_minutes_map.get(interval, 1)

        df = pd.DataFrame(candles)
        engine  = RTPBacktestEngine(
            df=df,
            entry_mode=entry_mode,
            interval_minutes=interval_minutes,
            use_adx=use_adx,
            adx_thresh=adx_thresh,
            sl_points=sl_points,
            tgt_points=tgt_points,
            trail_points=trail_points,
            exit_on=exit_on,
            confirm_bars=confirm_bars,
            strict_pattern=strict_pattern,
            min_rail_gap_atr=min_rail_gap_atr,
            max_trades_per_day=max_trades_per_day,
            max_consec_sl=max_consec_sl,
        )
        results = engine.run()
        trades  = results.get('trades', [])

        # Serialise datetime fields for JSON
        for t in trades:
            for k in ('entry_time', 'exit_time'):
                if hasattr(t.get(k), 'isoformat'):
                    t[k] = t[k].isoformat()
                elif t.get(k) is not None:
                    t[k] = str(t[k])

        def _fmt_dt(val):
            if val is None:
                return None
            s = str(val)
            return s[:16]  # "YYYY-MM-DD HH:MM"

        return jsonify({
            'success': True,
            'trades': trades,
            'summary': {
                'total_trades':  results['total_trades'],
                'wins':          results['wins'],
                'losses':        results['losses'],
                'total_pnl':     results['net_pnl'],
                'win_rate':      results['win_rate'],
                'profit_factor': results['profit_factor'],
                'max_drawdown':  results['max_drawdown'],
                'max_dd_start':  _fmt_dt(results.get('max_dd_start')),
                'max_dd_end':    _fmt_dt(results.get('max_dd_end')),
                'avg_win':       results.get('avg_win', 0),
                'avg_loss':      results.get('avg_loss', 0),
                'sl_points':     results['sl_points'],
                'tgt_points':    results['tgt_points'],
                'trail_points':  results.get('trail_points'),
                'exit_on':       results.get('exit_on', 'value'),
                'confirm_bars':        results.get('confirm_bars', 0),
                'strict_pattern':      results.get('strict_pattern', False),
                'min_rail_gap_atr':    results.get('min_rail_gap_atr', 0),
                'max_trades_per_day':  results.get('max_trades_per_day'),
                'max_consec_sl':       results.get('max_consec_sl'),
                'skipped_unconfirmed': results.get('skipped_unconfirmed', 0),
                'skipped_circuit':     results.get('skipped_circuit', 0),
            }
        })

    except Exception as e:
        logger.error(f"Error in RTP backtest API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def _fetch_1min_and_resample(provider, instrument_token, start_date_str, end_date_str, interval):
    """
    Fetch 1-minute bars (maximum Fyers historical depth — same as RTP) and
    resample to the requested interval in-process.

    Fyers stores 10+ years of 1-minute data for NSE indices but only ~1 year
    of pre-aggregated 5-minute data, which is why VWAP was returning only
    1 year while RTP (1-minute) returned 10 years.

    Uses floor+groupby instead of resample(offset=) for pandas-version safety.
    NSE opens at 9:15 IST which is a natural 5-minute boundary (9h15m % 5 = 0),
    so floor('5min') on IST-aware timestamps aligns correctly without offset.
    """
    import pandas as pd

    _interval_mins = {
        'minute': 1, '1minute': 1, '2minute': 2, '3minute': 3,
        '5minute': 5, '10minute': 10, '15minute': 15,
        '30minute': 30, '60minute': 60, 'hour': 60,
    }
    target_mins = _interval_mins.get(interval, 5)

    # Always pull raw 1-minute data for maximum historical depth
    candles = provider.historical_data(
        instrument_token=instrument_token,
        from_date=start_date_str,
        to_date=end_date_str,
        interval='minute',
        use_cache=False,
    )

    if not candles:
        return candles

    if target_mins <= 1:
        return candles

    df = pd.DataFrame(candles)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()

    # floor() on timezone-aware IST timestamps aligns to the correct
    # N-minute boundary (e.g. 09:15, 09:20, … for 5-minute)
    freq     = f'{target_mins}min'
    slot_key = df.index.floor(freq)

    resampled = df.groupby(slot_key).agg(
        open=('open',   'first'),
        high=('high',   'max'),
        low =('low',    'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
    )
    resampled = resampled.dropna(subset=['open', 'close'])
    resampled.index.name = 'date'
    resampled = resampled.reset_index()

    result = resampled.to_dict('records')
    logger.info(
        '[VWAP fetch] 1-min→%s resample: %d raw 1-min bars → %d %s bars  (%s → %s)',
        freq, len(df), len(result), interval,
        result[0]['date'] if result else '—',
        result[-1]['date'] if result else '—',
    )
    return result


@api_bp.route('/backtest/vwap', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_vwap_backtest_api():
    """Run Current & Previous VWAP (PL) backtest."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json()
        symbol         = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date')
        end_date_str   = data.get('end_date')
        interval       = data.get('interval', '5minute')
        min_gap        = float(data.get('min_gap',  30.0))
        tp_points      = float(data.get('tp_points', 150.0))
        sl_points      = float(data.get('sl_points', 50.0))

        if not symbol or not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400

        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401

        fyers_indices = {
            'NIFTY':      'NSE:NIFTY50-INDEX',
            'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':   'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':     'BSE:SENSEX-INDEX',
        }
        kite_indices = {
            'NIFTY': 256265, 'BANKNIFTY': 260105,
            'FINNIFTY': 257801, 'MIDCPNIFTY': 288009,
        }

        if hasattr(current_kite, 'fyers'):
            instrument_token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
        else:
            instrument_token = kite_indices.get(symbol, symbol)

        candles = _fetch_1min_and_resample(
            current_kite, instrument_token, start_date_str, end_date_str, interval
        )

        if not candles:
            return jsonify({'success': False, 'error': 'No historical data found for the given range'}), 404

        logger.info('[VWAP BT] %d bars received  first=%s  last=%s',
                    len(candles),
                    candles[0].get('date', '?'),
                    candles[-1].get('date', '?'))

        import pandas as pd
        import importlib
        import trading_app.Backtest.vwap_engine as _vwap_mod
        importlib.reload(_vwap_mod)
        from trading_app.Backtest.vwap_engine import VWAPBacktestEngine

        df = pd.DataFrame(candles)
        vol_sum = df['volume'].sum() if 'volume' in df.columns else -1
        logger.info('[VWAP BT] volume sum=%s  zero_vol_pct=%.1f%%',
                    vol_sum,
                    100.0 * (df['volume'] == 0).mean() if 'volume' in df.columns else -1)
        engine = VWAPBacktestEngine(
            df=df,
            min_gap_points=min_gap,
            tp_points=tp_points,
            sl_points=sl_points,
            interval=interval,
        )
        trades, summary = engine.run()

        logger.info('[VWAP BT] engine done: %d trades', len(trades))

        return jsonify({
            'success': True,
            'trades':  trades,
            '_debug': {
                'bars_fetched': len(candles),
                'first_bar':    str(candles[0].get('date', '?')),
                'last_bar':     str(candles[-1].get('date', '?')),
            },
            'summary': {
                'total_trades':  summary['total_trades'],
                'wins':          summary['wins'],
                'losses':        summary['losses'],
                'total_pnl':     summary['total_pnl'],
                'win_rate':      summary['win_rate'],
                'profit_factor': summary['profit_factor'],
                'max_drawdown':  summary['max_drawdown'],
                'avg_win':       summary['avg_win'],
                'avg_loss':      summary['avg_loss'],
            }
        })

    except Exception as e:
        logger.error(f"Error in VWAP backtest API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/second-candle', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_second_candle_backtest_api():
    """Run the 2nd 30-Sec Candle breakout backtest (Fyers 30-second data)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json()
        symbol         = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date')
        end_date_str   = data.get('end_date')
        # Base candle size. 30second has only ~1 month of Fyers history;
        # 'minute' and above retain ~10 years.
        interval       = data.get('interval', '30second')
        candle_index   = int(data.get('candle_index', 2))
        rr_ratio       = float(data.get('rr_ratio', 3.0))
        exit_hour      = int(data.get('exit_hour', 15))
        exit_minute    = int(data.get('exit_minute', 25))
        enable_long    = bool(data.get('enable_long', True))
        enable_short   = bool(data.get('enable_short', True))

        if not symbol or not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400

        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401

        fyers_indices = {
            'NIFTY':      'NSE:NIFTY50-INDEX',
            'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':   'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':     'BSE:SENSEX-INDEX',
        }
        kite_indices = {
            'NIFTY': 256265, 'BANKNIFTY': 260105,
            'FINNIFTY': 257801, 'MIDCPNIFTY': 288009,
        }

        if hasattr(current_kite, 'fyers'):
            instrument_token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
        else:
            instrument_token = kite_indices.get(symbol, symbol)

        # Fetch the chosen base interval (30second → Fyers "30S", minute → "1", etc.)
        candles = current_kite.historical_data(
            instrument_token=instrument_token,
            from_date=start_date_str,
            to_date=end_date_str,
            interval=interval,
            use_cache=False,
        )

        if not candles:
            return jsonify({'success': False, 'error': f'No {interval} data found for the given range'}), 404

        logger.info('[2ndCandle BT] %d %s bars received  first=%s  last=%s',
                    len(candles), interval,
                    candles[0].get('date', '?'),
                    candles[-1].get('date', '?'))

        import pandas as pd
        import importlib
        import trading_app.Backtest.second_candle_engine as _sc_mod
        importlib.reload(_sc_mod)
        from trading_app.Backtest.second_candle_engine import SecondCandleBacktestEngine

        df = pd.DataFrame(candles)
        engine = SecondCandleBacktestEngine(
            df=df,
            candle_index=candle_index,
            rr_ratio=rr_ratio,
            exit_hour=exit_hour,
            exit_minute=exit_minute,
            enable_long=enable_long,
            enable_short=enable_short,
        )
        trades, summary = engine.run()

        logger.info('[2ndCandle BT] engine done: %d trades', len(trades))

        # Warn if the data Fyers returned starts well after the requested range
        # (30-second history is only retained for ~1 month).
        warning = None
        try:
            import pandas as _pd
            req_start = _pd.to_datetime(start_date_str).date()
            got_start = _pd.to_datetime(str(candles[0].get('date'))).date()
            if (got_start - req_start).days > 5:
                warning = (
                    f"{interval} data is only available from {got_start} "
                    f"(requested {req_start}). Fyers retains ~1 month of 30-second "
                    f"history — switch to a 1-minute base for multi-year backtests."
                )
        except Exception:
            pass

        return jsonify({
            'success': True,
            'trades':  trades,
            'warning': warning,
            '_debug': {
                'bars_fetched': len(candles),
                'first_bar':    str(candles[0].get('date', '?')),
                'last_bar':     str(candles[-1].get('date', '?')),
            },
            'summary': {
                'total_trades':  summary['total_trades'],
                'wins':          summary['wins'],
                'losses':        summary['losses'],
                'total_pnl':     summary['total_pnl'],
                'win_rate':      summary['win_rate'],
                'profit_factor': summary['profit_factor'],
                'max_drawdown':  summary['max_drawdown'],
                'avg_win':       summary['avg_win'],
                'avg_loss':      summary['avg_loss'],
            }
        })

    except Exception as e:
        logger.error(f"Error in 2nd Candle backtest API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/second-candle/optimise', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_second_candle_optimise():
    """Sweep the 2nd-Candle (candle # × SL:Target × direction) grid across every
    intraday timeframe (30s–5 min) and return one leaderboard per timeframe —
    the same per-timeframe shape as /backtest/rtp/optimise."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json()
        symbol         = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date', '2017-01-01')
        end_date_str   = data.get('end_date')
        exit_hour      = int(data.get('exit_hour', 15))
        exit_minute    = int(data.get('exit_minute', 25))
        recalculate    = bool(data.get('recalculate', False))

        if not symbol or not start_date_str:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400
        if not end_date_str:
            end_date_str = datetime.today().strftime('%Y-%m-%d')

        # One run sweeps every intraday timeframe, so the cache is keyed by
        # symbol + exit-time alone. v2 adds Net P&L (₹) / Brokerage columns and
        # excludes combos with a negative Net P&L (₹).
        cache_key = f"{symbol}_sc_multiTF_{exit_hour:02d}{exit_minute:02d}_v2"

        # ── Serve from cache unless caller asked to recalculate ──────────────
        if not recalculate:
            cache = _load_opt_cache()
            if cache_key in cache:
                entry = cache[cache_key]
                return jsonify({
                    'success':            True,
                    'from_cache':         True,
                    'cached_at':          entry.get('cached_at'),
                    'symbol':             entry['symbol'],
                    'interval':           entry['interval'],
                    'total_combos_tested': entry['total_combos_tested'],
                    'best':               entry['best'],
                    'timeframes':         entry.get('timeframes', []),
                })

        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401

        task_id = str(uuid.uuid4())
        with _sc_opt_tasks_lock:
            _sc_opt_tasks[task_id] = {'status': 'running', 'started_at': _time.time()}

        def _run():
            try:
                fyers_indices = {
                    'NIFTY':      'NSE:NIFTY50-INDEX',
                    'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
                    'FINNIFTY':   'NSE:FINNIFTY-INDEX',
                    'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
                    'SENSEX':     'BSE:SENSEX-INDEX',
                }
                if hasattr(current_kite, 'fyers'):
                    instrument_token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
                else:
                    kite_indices = {'NIFTY': 256265, 'BANKNIFTY': 260105,
                                    'FINNIFTY': 257801, 'MIDCPNIFTY': 288009}
                    instrument_token = kite_indices.get(symbol, symbol)

                import pandas as pd
                import importlib
                import trading_app.Backtest.second_candle_engine as _sc_mod
                importlib.reload(_sc_mod)
                from trading_app.Backtest.second_candle_engine import optimise_second_candle

                grid_size = (len(_sc_mod._CANDLE_GRID) * len(_sc_mod._RR_GRID)
                             * len(_sc_mod._DIR_GRID))
                lot_value     = _rtp_lot_value(symbol)   # ₹/pt, shared with RTP
                tf_groups     = []   # one leaderboard per timeframe
                combos_tested = 0

                def _sweep(candles, interval_str, tf_label, tf_min):
                    """Run the full 2nd-candle sweep on one timeframe, keep its top 10."""
                    nonlocal combos_tested
                    if not candles:
                        logger.info("[2ndCandle OPT] timeframe %s: no data — skipped", interval_str)
                        return
                    tf_results = optimise_second_candle(
                        pd.DataFrame(candles), exit_hour=exit_hour, exit_minute=exit_minute
                    )
                    combos_tested += grid_size
                    for r in tf_results:
                        r['tf_label'] = tf_label
                        r['interval'] = interval_str
                        # ₹ net of brokerage (1 lot) — same economics as RTP. The
                        # 2nd-candle result's Net P&L field is `total_pnl`.
                        brok = _rtp_brokerage_per_trade(1) * (r.get('total_trades') or 0)
                        r['net_pnl_inr'] = round((r.get('total_pnl') or 0) * lot_value - brok, 2)
                    # Only combos still profitable after brokerage belong on the board.
                    profitable = [r for r in tf_results if r['net_pnl_inr'] > 0]
                    top_by_pnl = sorted(
                        profitable, key=lambda r: r.get('total_pnl', 0), reverse=True
                    )[:10]
                    tf_groups.append({
                        'tf_label': tf_label,
                        'tf_min':   tf_min,
                        'interval': interval_str,
                        'total':    len(tf_results),
                        'results':  top_by_pnl,
                    })

                # ── Minute timeframes: native fetch per interval ─────────────
                for minutes, interval_str, tf_label in [
                    (1, 'minute', '1m'), (2, '2minute', '2m'),
                    (3, '3minute', '3m'), (5, '5minute', '5m'),
                ]:
                    try:
                        _sweep(current_kite.historical_data(
                            instrument_token=instrument_token,
                            from_date=start_date_str, to_date=end_date_str,
                            interval=interval_str, use_cache=False,
                        ), interval_str, tf_label, minutes)
                    except Exception as tf_exc:
                        logger.warning(f"[2ndCandle OPT] timeframe {interval_str} failed: {tf_exc}")

                # ── 30-second: native fetch, capped to a recent window ───────
                # A full multi-year 30s pull is thousands of chunk calls, so cap
                # the range (best-effort; skip on any failure).
                try:
                    from datetime import timedelta as _td
                    _end_dt   = datetime.strptime(end_date_str, '%Y-%m-%d')
                    _start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
                    sec_from  = max(_start_dt, _end_dt - _td(days=90)).strftime('%Y-%m-%d')
                    _sweep(current_kite.historical_data(
                        instrument_token=instrument_token,
                        from_date=sec_from, to_date=end_date_str,
                        interval='30second', use_cache=False,
                    ), '30second', '30s', 0.5)
                except Exception as sec_exc:
                    logger.warning(f"[2ndCandle OPT] 30-second timeframe failed: {sec_exc}")

                if not tf_groups:
                    with _sc_opt_tasks_lock:
                        _sc_opt_tasks[task_id] = {'status': 'error', 'error': 'No historical data returned'}
                    return

                # Order the grids fastest→slowest (30s, 1m, 2m, 3m, 5m).
                tf_groups.sort(key=lambda g: g['tf_min'])

                # Overall best across every timeframe (highest Net P&L).
                all_top = [g['results'][0] for g in tf_groups if g['results']]
                best_overall = max(all_top, key=lambda r: r.get('total_pnl', 0), default=None)

                payload = {
                    'symbol':             symbol,
                    'interval':           'multi-TF (30s–5 min)',
                    'total_combos_tested': combos_tested,
                    'best':               best_overall,
                    'timeframes':         tf_groups,
                    'cached_at':          datetime.now().strftime('%Y-%m-%d %H:%M'),
                }

                disk_cache            = _load_opt_cache()
                disk_cache[cache_key] = payload
                _save_opt_cache(disk_cache)

                with _sc_opt_tasks_lock:
                    _sc_opt_tasks[task_id] = {'status': 'complete', 'payload': payload}
            except Exception as e:
                logger.error(f"[2ndCandle OPT] background error: {e}", exc_info=True)
                with _sc_opt_tasks_lock:
                    _sc_opt_tasks[task_id] = {'status': 'error', 'error': str(e)}

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'success': True, 'task_id': task_id, 'status': 'running'})

    except Exception as e:
        logger.error(f"Error in 2nd Candle optimise API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/second-candle/optimise/status/<task_id>', methods=['GET'])
@csrf.exempt
@require_user_auth
def run_second_candle_optimise_status(task_id):
    """Poll the status of a background 2nd-Candle optimisation job."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    with _sc_opt_tasks_lock:
        task = _sc_opt_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': 'Task not found'}), 404
    if task['status'] == 'running':
        return jsonify({'success': True, 'status': 'running'})
    if task['status'] == 'error':
        return jsonify({'success': False, 'status': 'error', 'error': task.get('error', 'Unknown error')}), 500
    return jsonify({'success': True, 'status': 'complete', 'from_cache': False, **task['payload']})


@api_bp.route('/backtest/expiry-breakout', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_expiry_breakout_backtest_api():
    """Run the Monthly Expiry Breakout backtest.

    Rule: take the High/Low of each monthly F&O expiry day's daily candle.
    The expiry day is auto-detected (last Thursday through Aug-2025, last
    Tuesday from Sep-2025 onward, per NSE's actual expiry-day change),
    snapped back to the latest trading day if that calendar day is a
    weekend/holiday. Whichever side breaks FIRST wins the one trade for
    that cycle (direction: 'both'|'long'|'short', default 'both'). The
    SIGNAL candle must satisfy the SAME touch-then-close pattern at TWO
    levels simultaneously:
      1. Expiry level — the candle's High/Low range must TOUCH the
         expiry-day High (Long) / Low (Short), i.e. a genuine touch, not
         a clean gap-through, AND its close must clear that level.
      2. EMA (ma_timeframe: '1hour'|'1day', default '1hour' — exactly one
         timeframe's 4 EMAs, never combined) — the candle must TOUCH at
         least one of that timeframe's EMA 20/50/100/200 AND its close
         must CLEAR EVERY one of them in the trade's direction (all
         above for Long, all below for Short).
    The actual entry is FILLED AT THE OPEN of the next hourly candle after
    the signal candle (not the signal candle's own close). Exit on
    whichever comes first: SL at entry × (1 ∓ sl_pct/100), TARGET at
    entry × (1 ± target_pct/100) — sl_pct (default 1.0) and target_pct
    (default 3.0) are independent percentages of the entry price — or
    force-exit before the next month's expiry day arrives.
    """
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json()
        symbol         = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date')
        end_date_str   = data.get('end_date')
        direction      = str(data.get('direction', 'both')).lower()
        enable_long    = direction != 'short'
        enable_short   = direction != 'long'
        sl_pct         = float(data.get('sl_pct', 1.0) or 1.0)
        target_pct     = float(data.get('target_pct', 3.0) or 3.0)
        ma_timeframe   = str(data.get('ma_timeframe', '1hour')).lower()

        if not symbol or not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400

        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401

        fyers_indices = {
            'NIFTY':      'NSE:NIFTY50-INDEX',
            'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':   'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':     'BSE:SENSEX-INDEX',
        }
        kite_indices = {
            'NIFTY': 256265, 'BANKNIFTY': 260105,
            'FINNIFTY': 257801, 'MIDCPNIFTY': 288009,
        }
        if hasattr(current_kite, 'fyers'):
            instrument_token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
        else:
            instrument_token = kite_indices.get(symbol, symbol)

        # Daily candle → expiry-day High/Low anchor; 60-minute candle → entry/exit scan.
        daily_candles = current_kite.historical_data(
            instrument_token=instrument_token,
            from_date=start_date_str,
            to_date=end_date_str,
            interval='day',
            use_cache=False,
        )
        if not daily_candles:
            return jsonify({'success': False, 'error': 'No daily data found for the given range'}), 404

        hourly_candles = current_kite.historical_data(
            instrument_token=instrument_token,
            from_date=start_date_str,
            to_date=end_date_str,
            interval='60minute',
            use_cache=False,
        )
        if not hourly_candles:
            return jsonify({'success': False, 'error': 'No 60-minute data found for the given range'}), 404

        import pandas as pd
        from trading_app.Backtest.expiry_breakout_engine import ExpiryBreakoutEngine

        engine = ExpiryBreakoutEngine(
            daily_df=pd.DataFrame(daily_candles),
            hourly_df=pd.DataFrame(hourly_candles),
            enable_long=enable_long,
            enable_short=enable_short,
            sl_pct=sl_pct,
            target_pct=target_pct,
            ma_timeframe=ma_timeframe,
        )
        trades, summary = engine.run()

        logger.info('[ExpiryBreakout BT] %d daily / %d hourly bars → %d trades',
                    len(daily_candles), len(hourly_candles), len(trades))

        return jsonify({
            'success': True,
            'trades':  trades,
            '_debug': {
                'daily_bars':  len(daily_candles),
                'hourly_bars': len(hourly_candles),
                'first_daily': str(daily_candles[0].get('date', '?')),
                'last_daily':  str(daily_candles[-1].get('date', '?')),
            },
            'summary': summary,
        })

    except Exception as e:
        logger.error(f"Error in Expiry Breakout backtest API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/expiry-breakout/levels', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_expiry_breakout_levels_api():
    """Preview the monthly expiry High/Low levels the Expiry Breakout engine
    would anchor on for the given range — daily data only, no hourly fetch
    or trade simulation, so this responds fast for the "Expiry Levels"
    popup button. Expiry day is auto-detected (see ExpiryBreakoutEngine)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json()
        symbol         = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date')
        end_date_str   = data.get('end_date')

        if not symbol or not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400

        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401

        fyers_indices = {
            'NIFTY':      'NSE:NIFTY50-INDEX',
            'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':   'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':     'BSE:SENSEX-INDEX',
        }
        kite_indices = {
            'NIFTY': 256265, 'BANKNIFTY': 260105,
            'FINNIFTY': 257801, 'MIDCPNIFTY': 288009,
        }
        if hasattr(current_kite, 'fyers'):
            instrument_token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
        else:
            instrument_token = kite_indices.get(symbol, symbol)

        daily_candles = current_kite.historical_data(
            instrument_token=instrument_token,
            from_date=start_date_str,
            to_date=end_date_str,
            interval='day',
            use_cache=False,
        )
        if not daily_candles:
            return jsonify({'success': False, 'error': 'No daily data found for the given range'}), 404

        import pandas as pd
        from trading_app.Backtest.expiry_breakout_engine import ExpiryBreakoutEngine

        engine = ExpiryBreakoutEngine(daily_df=pd.DataFrame(daily_candles))
        levels = engine.expiry_levels()

        return jsonify({'success': True, 'levels': levels})

    except Exception as e:
        logger.error(f"Error in Expiry Breakout levels API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/expiry-breakout/scan', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_expiry_breakout_scan_api():
    """Monthly Expiry Breakout — FILTER mode. Not a single-symbol backtest:
    scans every F&O stock (same universe as the live Expiry H/L scanner,
    /api/cpr-filter/expiry-hl-breakout) for every candle on the selected
    timeframe in [start_date, end_date] that touched-then-closed beyond
    that stock's current monthly-expiry-cycle High (BUY) or Low (SELL)
    AND whose close also clears every EMA 20/50/100/200 on the same
    timeframe (above all for BUY, below all for SELL). ema_touch
    additionally gates on whether the candle touched those EMAs:
    'touch' (default) requires touching at least one; 'not_touch'
    requires touching none; 'both' applies no touch condition. No
    SL/Target/Direction/Lots — defaults to Jan 1 of the current year
    through today.
    """
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json(silent=True) or {}
        start_date_str = data.get('start_date')
        end_date_str   = data.get('end_date')
        timeframe      = str(data.get('timeframe', '60minute')).lower()
        ema_touch      = str(data.get('ema_touch', 'touch')).lower()

        now = datetime.now()
        start_date = (datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str
                      else datetime(now.year, 1, 1))
        end_date   = (datetime.strptime(end_date_str, '%Y-%m-%d') if end_date_str
                      else now)

        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401
        if not hasattr(current_kite, 'access_token') or not current_kite.access_token:
            return jsonify({
                'success': False,
                'error': 'No valid access token on KiteConnect instance. Please login again.',
                'auth_error': True,
            }), 401

        from trading_app.filters.expiry_hl_scanner import filter_expiry_hl_breakout_range
        cpr_service = _get_cpr_service(current_kite)
        results = filter_expiry_hl_breakout_range(
            cpr_service, start_date=start_date, end_date=end_date,
            timeframe=timeframe, ema_touch=ema_touch)

        return jsonify({
            'success':    True,
            'buy':        results.get('buy', []),
            'sell':       results.get('sell', []),
            'timeframe':  timeframe,
            'start_date': start_date.strftime('%Y-%m-%d'),
            'end_date':   end_date.strftime('%Y-%m-%d'),
        })

    except Exception as e:
        logger.error(f"Error in Expiry Breakout scan API: {e}", exc_info=True)
        error_str = str(e).lower()
        if 'access_token' in error_str or 'unauthorized' in error_str or 'invalid' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True,
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/vwap/optimise', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_vwap_optimise():
    """Sweep VWAP (min_gap × tp × sl) parameter grid and return ranked results."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json()
        symbol         = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date', '2017-01-01')
        end_date_str   = data.get('end_date')
        interval       = data.get('interval', '5minute')
        recalculate    = bool(data.get('recalculate', False))

        if not end_date_str:
            end_date_str = datetime.today().strftime('%Y-%m-%d')

        cache_key = f"vwap_{symbol}_{interval}"

        if not recalculate:
            cache = _load_opt_cache()
            if cache_key in cache:
                entry = cache[cache_key]
                return jsonify({'success': True, 'from_cache': True, **entry})

        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401

        fyers_indices = {
            'NIFTY':      'NSE:NIFTY50-INDEX',
            'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':   'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':     'BSE:SENSEX-INDEX',
        }
        kite_indices = {
            'NIFTY': 256265, 'BANKNIFTY': 260105,
            'FINNIFTY': 257801, 'MIDCPNIFTY': 288009,
        }

        if hasattr(current_kite, 'fyers'):
            instrument_token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
        else:
            instrument_token = kite_indices.get(symbol, symbol)

        candles = _fetch_1min_and_resample(
            current_kite, instrument_token, start_date_str, end_date_str, interval
        )
        if not candles:
            return jsonify({'success': False, 'error': 'No historical data returned'}), 404

        import pandas as pd
        from trading_app.Backtest.vwap_engine import optimise_vwap

        df      = pd.DataFrame(candles)
        results = optimise_vwap(df, interval=interval)

        cached_at = datetime.now().strftime('%Y-%m-%d %H:%M')
        payload   = {
            'symbol':              symbol,
            'interval':            interval,
            'total_combos_tested': len(results),
            'best':                results[0] if results else None,
            'results':             results[:15],
            'cached_at':           cached_at,
        }

        cache           = _load_opt_cache()
        cache[cache_key] = payload
        _save_opt_cache(cache)

        return jsonify({'success': True, 'from_cache': False, **payload})

    except Exception as e:
        logger.error(f"Error in VWAP optimise API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Optimise-grid ₹ economics (mirrors the frontend so the leaderboard drops
# combos whose Net P&L is negative *after brokerage*, not just in points) ──────
_RTP_LOT_VALUE_BY_SYMBOL = {'NIFTY': 65, 'BANKNIFTY': 30, 'SENSEX': 20}

def _rtp_lot_value(symbol: str) -> float:
    """₹ per point for one lot, by symbol (defaults to NIFTY's 65)."""
    return _RTP_LOT_VALUE_BY_SYMBOL.get((symbol or '').upper(), 65)

def _rtp_brokerage_per_trade(lots: int = 1) -> float:
    """Round-trip brokerage per trade for NIFTY, by lot count (matches backtest.js)."""
    lookup = {1: 103, 2: 158, 3: 213, 4: 268, 5: 330}
    if lots <= 5:
        return lookup.get(max(1, int(lots)), 103)
    return 330 + (int(lots) - 5) * 62

def _rtp_net_inr(r: Dict[str, Any], lot_value: float, lots: int = 1) -> float:
    """Net P&L in ₹ = gross ₹ (pts × ₹/pt × lots) − round-trip brokerage."""
    brok = _rtp_brokerage_per_trade(lots) * (r.get('total_trades') or 0)
    return (r.get('net_pnl') or 0) * lot_value * lots - brok


@api_bp.route('/backtest/rtp/optimise', methods=['POST'], strict_slashes=False)
@csrf.exempt
@require_user_auth
def run_rtp_optimise():
    """Return RTP optimisation results, using cached data when available."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        data           = request.get_json()
        symbol         = data.get('symbol', 'NIFTY')
        start_date_str = data.get('start_date', '2017-01-01')
        end_date_str   = data.get('end_date')
        recalculate    = bool(data.get('recalculate', False))

        if not end_date_str:
            end_date_str = datetime.today().strftime('%Y-%m-%d')

        # We sweep every intraday timeframe (30s–5 min) in one run and return a
        # per-timeframe leaderboard, so the cache is keyed by symbol alone.
        # _v2 marks the per-timeframe payload shape (was a flat combined list).
        # v3: grids now use native per-timeframe candles (was 1-min resampled).
        # v4: leaderboard excludes combos with a negative Net P&L (₹, net of
        # brokerage). Bump the key to invalidate stale cache entries.
        # v5: grid gained confirm_bars and min_rail_gap_atr dimensions.
        cache_key = f"{symbol}_multiTF_v5"

        # ── Serve from cache unless caller asked to recalculate ──────────────
        if not recalculate:
            cache = _load_opt_cache()
            if cache_key in cache:
                entry = cache[cache_key]
                return jsonify({
                    'success':            True,
                    'from_cache':         True,
                    'cached_at':          entry.get('cached_at'),
                    'symbol':             entry['symbol'],
                    'interval':           entry['interval'],
                    'total_combos_tested': entry['total_combos_tested'],
                    'best':               entry['best'],
                    'timeframes':         entry.get('timeframes', []),
                })

        # ── Run optimisation ─────────────────────────────────────────────────
        # Fetching multi-year intraday data (chunked, rate-limited) plus the
        # full parameter sweep can take minutes, so run it in a background
        # thread and let the client poll /backtest/rtp/optimise/status/<task_id>.
        current_kite = get_data_provider()
        if not current_kite:
            return jsonify({'success': False, 'error': 'Data provider initialization failed'}), 401

        task_id = str(uuid.uuid4())
        with _rtp_opt_tasks_lock:
            _rtp_opt_tasks[task_id] = {'status': 'running', 'started_at': _time.time()}

        def _run():
            try:
                fyers_indices = {
                    'NIFTY':      'NSE:NIFTY50-INDEX',
                    'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
                    'FINNIFTY':   'NSE:FINNIFTY-INDEX',
                    'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
                    'SENSEX':     'BSE:SENSEX-INDEX',
                }

                if hasattr(current_kite, 'fyers'):
                    instrument_token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
                else:
                    kite_indices = {'NIFTY': 256265, 'BANKNIFTY': 260105,
                                    'FINNIFTY': 257801, 'MIDCPNIFTY': 288009}
                    instrument_token = kite_indices.get(symbol, symbol)

                from trading_app.Backtest.rtp_backtest_engine import optimise_rtp

                tf_groups     = []   # one leaderboard per timeframe
                combos_tested = 0

                lot_value = _rtp_lot_value(symbol)

                def _sweep(df_tf, interval_str, tf_label, tf_min):
                    """Run the full param sweep on one timeframe, keep its top 10 by Net P&L."""
                    nonlocal combos_tested
                    if df_tf is None or df_tf.empty:
                        return

                    def _progress(done, total):
                        with _rtp_opt_tasks_lock:
                            task = _rtp_opt_tasks.get(task_id)
                            if task is not None and task.get('status') == 'running':
                                task['progress'] = f"{tf_label} · {done}/{total}"

                    tf_results = optimise_rtp(df_tf, interval=interval_str, min_trades=15,
                                              progress_cb=_progress)
                    combos_tested += len(tf_results)
                    for r in tf_results:
                        r['timeframe_min'] = tf_min      # numeric (0.5 for 30s)
                        r['tf_label']      = tf_label     # display label
                        r['interval']      = interval_str
                        r['net_pnl_inr']   = round(_rtp_net_inr(r, lot_value), 2)  # ₹ net (1 lot)
                    # Only combos that are still profitable after brokerage belong on
                    # the leaderboard — drop any with a negative Net P&L (₹).
                    profitable = [r for r in tf_results if r['net_pnl_inr'] > 0]
                    # Rank the leaderboard by Net P&L (highest first) and keep the top 10.
                    top_by_pnl = sorted(
                        profitable, key=lambda r: r.get('net_pnl', 0), reverse=True
                    )[:10]
                    tf_groups.append({
                        'tf_label': tf_label,
                        'tf_min':   tf_min,
                        'interval': interval_str,
                        'total':    len(tf_results),      # combos that passed min_trades
                        'results':  top_by_pnl,           # top 10 profitable by Net P&L
                    })

                # ── Minute timeframes: native fetch per interval ─────────────────
                # Fetch each timeframe natively — the SAME data path the single
                # backtest uses — so an optimise grid row reproduces exactly what
                # a manual backtest at that timeframe produces. Deriving 2/3/5-min
                # by resampling 1-min drifted from the broker's native N-min
                # candles (different bar highs/lows → different SL/target hits),
                # which made the grid's Net P&L disagree with the backtest card.
                any_data = False
                for minutes, interval_str, tf_label in [
                    (1, 'minute', '1m'), (2, '2minute', '2m'),
                    (3, '3minute', '3m'), (5, '5minute', '5m'),
                ]:
                    try:
                        tf_candles = current_kite.historical_data(
                            instrument_token=instrument_token,
                            from_date=start_date_str,
                            to_date=end_date_str,
                            interval=interval_str,
                            use_cache=False,
                        )
                        if not tf_candles:
                            logger.info("[RTPOptimise] timeframe %s: no data returned — skipped", interval_str)
                            continue
                        any_data = True
                        _sweep(pd.DataFrame(tf_candles), interval_str, tf_label, minutes)
                    except Exception as tf_exc:
                        logger.warning(f"[RTPOptimise] timeframe {interval_str} failed: {tf_exc}")
                if not any_data:
                    with _rtp_opt_tasks_lock:
                        _rtp_opt_tasks[task_id] = {'status': 'error', 'error': 'No historical data returned'}
                    return

                # ── 30-second: native fetch, can't be derived from 1-min ─────────
                # Fyers only serves seconds-resolution history for a recent window,
                # so cap the range (a full multi-year 30s pull would be thousands
                # of mostly-empty chunk calls). Best-effort: skip on any failure.
                try:
                    from datetime import timedelta as _td
                    _end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
                    _cap_dt = _end_dt - _td(days=90)
                    _start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
                    sec_from = max(_start_dt, _cap_dt).strftime('%Y-%m-%d')
                    sec_candles = current_kite.historical_data(
                        instrument_token=instrument_token,
                        from_date=sec_from,
                        to_date=end_date_str,
                        interval='30second',
                        use_cache=False,
                    )
                    if sec_candles:
                        _sweep(pd.DataFrame(sec_candles), '30second', '30s', 0.5)
                    else:
                        logger.info("[RTPOptimise] 30-second timeframe: no data returned — skipped")
                except Exception as sec_exc:
                    logger.warning(f"[RTPOptimise] 30-second timeframe failed: {sec_exc}")

                # Order the grids fastest→slowest (30s, 1m, 2m, 3m, 5m).
                tf_groups.sort(key=lambda g: g['tf_min'])

                # Overall best across every timeframe — used to auto-apply the
                # top result to the form when the run completes. Each grid is now
                # ranked by Net P&L, so pick the highest Net P&L across timeframes.
                all_top = [g['results'][0] for g in tf_groups if g['results']]
                best_overall = max(all_top, key=lambda r: r.get('net_pnl', 0), default=None)

                payload = {
                    'symbol':             symbol,
                    'interval':           'multi-TF (30s–5 min)',
                    'total_combos_tested': combos_tested,
                    'best':               best_overall,
                    'timeframes':         tf_groups,
                    'cached_at':          datetime.now().strftime('%Y-%m-%d %H:%M'),
                }

                # Persist to disk
                disk_cache            = _load_opt_cache()
                disk_cache[cache_key] = payload
                _save_opt_cache(disk_cache)

                with _rtp_opt_tasks_lock:
                    _rtp_opt_tasks[task_id] = {'status': 'complete', 'payload': payload}
            except Exception as e:
                logger.error(f"[RTPOptimise] background error: {e}", exc_info=True)
                with _rtp_opt_tasks_lock:
                    _rtp_opt_tasks[task_id] = {'status': 'error', 'error': str(e)}

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'success': True, 'task_id': task_id, 'status': 'running'})

    except Exception as e:
        logger.error(f"Error in RTP optimise API: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/rtp/optimise/status/<task_id>', methods=['GET'])
@csrf.exempt
@require_user_auth
def run_rtp_optimise_status(task_id):
    """Poll the status of a background RTP optimisation job."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    with _rtp_opt_tasks_lock:
        task = _rtp_opt_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': 'Task not found'}), 404
    if task['status'] == 'running':
        return jsonify({'success': True, 'status': 'running', 'progress': task.get('progress')})
    if task['status'] == 'error':
        return jsonify({'success': False, 'status': 'error', 'error': task.get('error', 'Unknown error')}), 500
    # complete
    return jsonify({'success': True, 'status': 'complete', 'from_cache': False, **task['payload']})


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

    # Auto-exit (target = entry+10 monitor) is opt-in: manual orders from the OI Profile
    # screen must never be exited by the app unless the user enables INTRINSIC_AUTO_EXIT.
    _auto_exit_enabled = UserEnvManager.get_user_var(
        username, 'INTRINSIC_AUTO_EXIT', 'false'
    ).strip().lower() in ('true', '1', 'yes')

    # A manual SELL closes the position — kill any running auto-exit monitors for this
    # strike so an orphaned monitor can't fire another SELL later.
    if action == 'SELL' and strategy == 'intrinsic':
        from trading_app.app.intraday_option.intrinsic_order_manager import IntrinsicOrderManager
        IntrinsicOrderManager.stop_for_position(username, symbol, strike, option_type)

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
            return {'success': False, 'error': 'No active broker found. Set BROKER_N_ACTIVE=true in .env'}

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

                            if strategy == 'intrinsic' and _auto_exit_enabled:
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

                            if strategy == 'intrinsic' and _auto_exit_enabled:
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

                            if strategy == 'intrinsic' and _auto_exit_enabled:
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

                            if strategy == 'intrinsic' and _auto_exit_enabled:
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
            logger.info(f"[920/place-order] 'broker' field '{broker_input}' in payload is ignored — routing via active brokers in .env")

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
    Both are CURRENT (nearest) expiry — only the EOD historic recorder
    (dashboard/oi_historic_data.py) rolls to the next expiry.

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
            # CURRENT expiry, matching the scheduler recorder — this fallback
            # writes into the same oi_history table, so both must use the same
            # expiry basis or an expiry-day series would mix current/next-expiry
            # rows and corrupt the PCR/Vega charts.
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


@api_bp.route('/algo/rtp/status', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp_status() -> EndpointResponse:
    """Return current RTP active trade + live NIFTY spot and P&L."""
    try:
        from datetime import date as _date
        try:
            with open(_RTP_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {'active_trade': None, 'buy_needs_reset': False, 'sell_needs_reset': False}

        trade = state.get('active_trade')
        if not trade:
            return jsonify({'success': True, 'active': False, 'state': state, 'live': None})

        # Fetch live NIFTY spot for P&L calculation
        live = None
        try:
            provider = get_data_provider()
            if provider:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
                if spot:
                    direction  = trade.get('direction', 'BUY')
                    entry_spot = float(trade.get('entry_spot', 0))
                    pnl_pts    = spot - entry_spot if direction == 'BUY' else entry_spot - spot
                    pnl_pts    = round(pnl_pts, 2)
                    broker_entries = trade.get('broker_entries', [])
                    pnl_inr_total  = round(
                        sum(pnl_pts * 0.90 * float(e.get('quantity', 75)) for e in broker_entries), 2
                    )
                    live = {'spot': spot, 'pnl_pts': pnl_pts, 'pnl_inr_total': pnl_inr_total}
                    live.update(_algo_option_live(trade, provider))
        except Exception as _e:
            logger.warning(f'[rtp/status] live fetch failed: {_e}')

        return jsonify({'success': True, 'active': True, 'state': state, 'live': live})
    except Exception as e:
        logger.error(f'[rtp/status] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp_history() -> EndpointResponse:
    """Return all completed RTP trades from rtp_trades_all_history.json (latest-first)."""
    try:
        try:
            with open(_RTP_ALL_HISTORY_PATH, 'r') as _f:
                all_trades = json.load(_f)
            if not isinstance(all_trades, list):
                all_trades = []
        except Exception:
            all_trades = []

        # Strip broker_entries to keep payload lean
        trades = [
            {k: v for k, v in t.items() if k != 'broker_entries'}
            for t in all_trades
        ]
        return jsonify({'success': True, 'trades': trades, 'count': len(trades)})
    except Exception as e:
        logger.error(f'[rtp/history] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp/history', methods=['DELETE'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp_history_delete() -> EndpointResponse:
    """Delete a trade record by entry_time from both daily and all-time history files."""
    try:
        data       = request.get_json(silent=True) or {}
        entry_time = data.get('entry_time')
        delete_all = bool(data.get('all'))
        if not entry_time and not delete_all:
            return jsonify({'success': False, 'error': 'entry_time or all:true required'}), 400

        for path in [_RTP_HISTORY_PATH, _RTP_ALL_HISTORY_PATH]:
            try:
                with open(path, 'r') as _f:
                    records = json.load(_f)
                if isinstance(records, list):
                    records = [] if delete_all else \
                        [r for r in records if r.get('entry_time') != entry_time]
                    with open(path, 'w') as _f:
                        json.dump(records, _f, indent=2, default=str)
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'[rtp/history/delete] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Intrinsic ATM Range Breakout algo (paper trade) ──────────────────────────

@api_bp.route('/algo/intrinsic-range/status', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_intrinsic_range_status() -> EndpointResponse:
    """Return today's daily range setup, active paper trade, and live spot/P&L."""
    try:
        try:
            with open(_IR_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {'date': None, 'daily_setup': None, 'active_trade': None}

        trade = state.get('active_trade')
        if not trade:
            return jsonify({'success': True, 'active': False, 'state': state, 'live': None})

        live = None
        try:
            provider = get_data_provider()
            if provider:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
                if spot:
                    direction  = trade.get('direction', 'BUY')
                    entry_spot = float(trade.get('entry_spot', 0))
                    pnl_pts    = spot - entry_spot if direction == 'BUY' else entry_spot - spot
                    pnl_pts    = round(pnl_pts, 2)
                    qty        = trade.get('quantity', 75)
                    live = {'spot': spot, 'pnl_pts': pnl_pts}
                    entry_premium = trade.get('entry_premium')
                    fyers_sym = trade.get('fyers_sym', '')
                    if fyers_sym:
                        opt_ltp_data = provider.ltp([fyers_sym])
                        opt_raw = opt_ltp_data.get(fyers_sym, {}).get('last_price', 0)
                        current_premium = round(float(opt_raw), 2) if opt_raw else None
                        live['current_premium'] = current_premium
                        live['opt_entry_price']  = entry_premium
                        live['opt_current_price'] = current_premium
                        if entry_premium is not None and current_premium is not None:
                            live['premium_pnl_pts'] = round(current_premium - entry_premium, 2)
                            live['premium_pnl_inr'] = round(live['premium_pnl_pts'] * qty, 2)
                            live['opt_pnl_pts'] = live['premium_pnl_pts']
                            live['opt_pnl_inr'] = live['premium_pnl_inr']
        except Exception as _e:
            logger.warning(f'[intrinsic-range/status] live fetch failed: {_e}')

        return jsonify({'success': True, 'active': True, 'state': state, 'live': live})
    except Exception as e:
        logger.error(f'[intrinsic-range/status] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/intrinsic-range/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_intrinsic_range_history() -> EndpointResponse:
    """Return all completed Intrinsic Range paper trades (latest-first)."""
    try:
        try:
            with open(_IR_ALL_HISTORY_PATH, 'r') as _f:
                all_trades = json.load(_f)
            if not isinstance(all_trades, list):
                all_trades = []
        except Exception:
            all_trades = []
        return jsonify({'success': True, 'trades': all_trades, 'count': len(all_trades)})
    except Exception as e:
        logger.error(f'[intrinsic-range/history] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/intrinsic-range/history', methods=['DELETE'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_intrinsic_range_history_delete() -> EndpointResponse:
    """Delete a paper-trade record by entry_time from both daily and all-time history files."""
    try:
        data       = request.get_json(silent=True) or {}
        entry_time = data.get('entry_time')
        delete_all = bool(data.get('all'))
        if not entry_time and not delete_all:
            return jsonify({'success': False, 'error': 'entry_time or all:true required'}), 400

        for path in [_IR_HISTORY_PATH, _IR_ALL_HISTORY_PATH]:
            try:
                with open(path, 'r') as _f:
                    records = json.load(_f)
                if isinstance(records, list):
                    records = [] if delete_all else \
                        [r for r in records if r.get('entry_time') != entry_time]
                    with open(path, 'w') as _f:
                        json.dump(records, _f, indent=2, default=str)
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'[intrinsic-range/history/delete] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/intrinsic-range/start', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_intrinsic_range_start() -> EndpointResponse:
    """Start (or restart) the Intrinsic Range paper-trade monitoring thread."""
    try:
        from trading_app.algo.intrinsic_range.intrinsic_range_algo import IntrinsicRangeAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        existing = get_instance(username)
        if existing and existing.is_running():
            return jsonify({'success': False, 'error': 'Algo already running'}), 409

        algo = IntrinsicRangeAlgo(username=username)
        algo.start()
        return jsonify({'success': True, 'message': 'Intrinsic Range algo started (paper mode)'})
    except Exception as e:
        logger.error(f'[intrinsic-range/start] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/intrinsic-range/stop', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_intrinsic_range_stop() -> EndpointResponse:
    """Stop the Intrinsic Range paper-trade monitoring thread."""
    try:
        from trading_app.algo.intrinsic_range.intrinsic_range_algo import get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        existing = get_instance(username)
        if not existing or not existing.is_running():
            return jsonify({'success': False, 'error': 'Algo not running'}), 409

        existing.stop()
        return jsonify({'success': True, 'message': 'Intrinsic Range algo stopped'})
    except Exception as e:
        logger.error(f'[intrinsic-range/stop] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp/exit', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp_force_exit() -> EndpointResponse:
    """Manually close the active RTP trade on all brokers."""
    try:
        try:
            with open(_RTP_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {}

        if not state.get('active_trade'):
            return jsonify({'success': False, 'error': 'No active trade to exit'}), 400

        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        # Prefer the running instance so cached broker services are reused
        algo = get_instance(username)
        if algo is None:
            algo = RTPAlgo(username=username)

        provider = get_data_provider()
        spot = 0.0
        if provider:
            try:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
            except Exception:
                pass

        algo._exit_trade('MANUAL', spot)
        return jsonify({'success': True, 'exit_spot': spot})
    except Exception as e:
        logger.error(f'[rtp/exit] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp/delta-strikes', methods=['GET'])
@require_user_auth
def algo_rtp_delta_strikes() -> EndpointResponse:
    """Return the CE and PE strikes closest to ±0.90 delta at the current NIFTY spot."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider unavailable'}), 503

        # Prefer the running instance (has instruments already cached)
        algo = get_instance(username) or RTPAlgo(username=username)
        result = algo.get_delta_strikes(provider)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[rtp/delta-strikes] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp/start', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp_start() -> EndpointResponse:
    """Start (or restart) the RTP monitoring thread."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        existing = get_instance(username)
        if existing and existing.is_running():
            return jsonify({'success': False, 'error': 'Algo already running'}), 409

        algo = RTPAlgo(username=username)
        algo.start()
        return jsonify({'success': True, 'message': 'RTP algo started'})
    except Exception as e:
        logger.error(f'[rtp/start] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── EMA RTP 30s live algo (same RTP logic, 30-second candles) ────────────────

@api_bp.route('/algo/rtp30s/status', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp30s_status() -> EndpointResponse:
    """Return current RTP 30s active trade + live NIFTY spot and P&L."""
    try:
        try:
            with open(_RTP30S_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {'active_trade': None, 'buy_needs_reset': False, 'sell_needs_reset': False}

        trade = state.get('active_trade')
        if not trade:
            return jsonify({'success': True, 'active': False, 'state': state, 'live': None})

        # Fetch live NIFTY spot for P&L calculation
        live = None
        try:
            provider = get_data_provider()
            if provider:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
                if spot:
                    direction  = trade.get('direction', 'BUY')
                    entry_spot = float(trade.get('entry_spot', 0))
                    pnl_pts    = spot - entry_spot if direction == 'BUY' else entry_spot - spot
                    pnl_pts    = round(pnl_pts, 2)
                    broker_entries = trade.get('broker_entries', [])
                    pnl_inr_total  = round(
                        sum(pnl_pts * 0.90 * float(e.get('quantity', 75)) for e in broker_entries), 2
                    )
                    live = {'spot': spot, 'pnl_pts': pnl_pts, 'pnl_inr_total': pnl_inr_total}
                    live.update(_algo_option_live(trade, provider))
        except Exception as _e:
            logger.warning(f'[rtp30s/status] live fetch failed: {_e}')

        return jsonify({'success': True, 'active': True, 'state': state, 'live': live})
    except Exception as e:
        logger.error(f'[rtp30s/status] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp30s/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp30s_history() -> EndpointResponse:
    """Return all completed RTP 30s trades from rtp_trades_all_history_30s.json (latest-first)."""
    try:
        try:
            with open(_RTP30S_ALL_HISTORY_PATH, 'r') as _f:
                all_trades = json.load(_f)
            if not isinstance(all_trades, list):
                all_trades = []
        except Exception:
            all_trades = []

        # Strip broker_entries to keep payload lean
        trades = [
            {k: v for k, v in t.items() if k != 'broker_entries'}
            for t in all_trades
        ]
        return jsonify({'success': True, 'trades': trades, 'count': len(trades)})
    except Exception as e:
        logger.error(f'[rtp30s/history] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp30s/history', methods=['DELETE'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp30s_history_delete() -> EndpointResponse:
    """Delete a trade record by entry_time from both daily and all-time 30s history files."""
    try:
        data       = request.get_json(silent=True) or {}
        entry_time = data.get('entry_time')
        delete_all = bool(data.get('all'))
        if not entry_time and not delete_all:
            return jsonify({'success': False, 'error': 'entry_time or all:true required'}), 400

        for path in [_RTP30S_HISTORY_PATH, _RTP30S_ALL_HISTORY_PATH]:
            try:
                with open(path, 'r') as _f:
                    records = json.load(_f)
                if isinstance(records, list):
                    records = [] if delete_all else \
                        [r for r in records if r.get('entry_time') != entry_time]
                    with open(path, 'w') as _f:
                        json.dump(records, _f, indent=2, default=str)
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'[rtp30s/history/delete] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp30s/exit', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp30s_force_exit() -> EndpointResponse:
    """Manually close the active RTP 30s trade on all brokers."""
    try:
        try:
            with open(_RTP30S_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {}

        if not state.get('active_trade'):
            return jsonify({'success': False, 'error': 'No active trade to exit'}), 400

        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        # Prefer the running instance so cached broker services are reused
        algo = get_instance(username, '30s')
        if algo is None:
            algo = RTPAlgo(username=username, variant='30s')

        provider = get_data_provider()
        spot = 0.0
        if provider:
            try:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
            except Exception:
                pass

        algo._exit_trade('MANUAL', spot)
        return jsonify({'success': True, 'exit_spot': spot})
    except Exception as e:
        logger.error(f'[rtp30s/exit] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp30s/delta-strikes', methods=['GET'])
@require_user_auth
def algo_rtp30s_delta_strikes() -> EndpointResponse:
    """Return the CE and PE strikes closest to ±0.90 delta at the current NIFTY spot."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider unavailable'}), 503

        # Prefer the running instance (has instruments already cached)
        algo = get_instance(username, '30s') or RTPAlgo(username=username, variant='30s')
        result = algo.get_delta_strikes(provider)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[rtp30s/delta-strikes] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp30s/start', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp30s_start() -> EndpointResponse:
    """Start (or restart) the RTP 30s monitoring thread."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        existing = get_instance(username, '30s')
        if existing and existing.is_running():
            return jsonify({'success': False, 'error': 'Algo already running'}), 409

        algo = RTPAlgo(username=username, variant='30s')
        algo.start()
        return jsonify({'success': True, 'message': 'RTP 30s algo started'})
    except Exception as e:
        logger.error(f'[rtp30s/start] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── EMA RTP 3m live algo (same RTP logic, 3-minute candles) ──────────────────

@api_bp.route('/algo/rtp3m/status', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp3m_status() -> EndpointResponse:
    """Return current RTP 3m active trade + live NIFTY spot and P&L."""
    try:
        try:
            with open(_RTP3M_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {'active_trade': None, 'buy_needs_reset': False, 'sell_needs_reset': False}

        trade = state.get('active_trade')
        if not trade:
            return jsonify({'success': True, 'active': False, 'state': state, 'live': None})

        # Fetch live NIFTY spot for P&L calculation
        live = None
        try:
            provider = get_data_provider()
            if provider:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
                if spot:
                    direction  = trade.get('direction', 'BUY')
                    entry_spot = float(trade.get('entry_spot', 0))
                    pnl_pts    = spot - entry_spot if direction == 'BUY' else entry_spot - spot
                    pnl_pts    = round(pnl_pts, 2)
                    broker_entries = trade.get('broker_entries', [])
                    pnl_inr_total  = round(
                        sum(pnl_pts * 0.90 * float(e.get('quantity', 75)) for e in broker_entries), 2
                    )
                    live = {'spot': spot, 'pnl_pts': pnl_pts, 'pnl_inr_total': pnl_inr_total}
                    live.update(_algo_option_live(trade, provider))
        except Exception as _e:
            logger.warning(f'[rtp3m/status] live fetch failed: {_e}')

        return jsonify({'success': True, 'active': True, 'state': state, 'live': live})
    except Exception as e:
        logger.error(f'[rtp3m/status] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp3m/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp3m_history() -> EndpointResponse:
    """Return all completed RTP 3m trades from rtp_trades_all_history_3m.json (latest-first)."""
    try:
        try:
            with open(_RTP3M_ALL_HISTORY_PATH, 'r') as _f:
                all_trades = json.load(_f)
            if not isinstance(all_trades, list):
                all_trades = []
        except Exception:
            all_trades = []

        # Strip broker_entries to keep payload lean
        trades = [
            {k: v for k, v in t.items() if k != 'broker_entries'}
            for t in all_trades
        ]
        return jsonify({'success': True, 'trades': trades, 'count': len(trades)})
    except Exception as e:
        logger.error(f'[rtp3m/history] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp3m/history', methods=['DELETE'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp3m_history_delete() -> EndpointResponse:
    """Delete a trade record by entry_time from both daily and all-time 3m history files."""
    try:
        data       = request.get_json(silent=True) or {}
        entry_time = data.get('entry_time')
        delete_all = bool(data.get('all'))
        if not entry_time and not delete_all:
            return jsonify({'success': False, 'error': 'entry_time or all:true required'}), 400

        for path in [_RTP3M_HISTORY_PATH, _RTP3M_ALL_HISTORY_PATH]:
            try:
                with open(path, 'r') as _f:
                    records = json.load(_f)
                if isinstance(records, list):
                    records = [] if delete_all else \
                        [r for r in records if r.get('entry_time') != entry_time]
                    with open(path, 'w') as _f:
                        json.dump(records, _f, indent=2, default=str)
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'[rtp3m/history/delete] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp3m/exit', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp3m_force_exit() -> EndpointResponse:
    """Manually close the active RTP 3m trade on all brokers."""
    try:
        try:
            with open(_RTP3M_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {}

        if not state.get('active_trade'):
            return jsonify({'success': False, 'error': 'No active trade to exit'}), 400

        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        # Prefer the running instance so cached broker services are reused
        algo = get_instance(username, '3m')
        if algo is None:
            algo = RTPAlgo(username=username, variant='3m')

        provider = get_data_provider()
        spot = 0.0
        if provider:
            try:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
            except Exception:
                pass

        algo._exit_trade('MANUAL', spot)
        return jsonify({'success': True, 'exit_spot': spot})
    except Exception as e:
        logger.error(f'[rtp3m/exit] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp3m/delta-strikes', methods=['GET'])
@require_user_auth
def algo_rtp3m_delta_strikes() -> EndpointResponse:
    """Return the CE and PE strikes closest to ±0.90 delta at the current NIFTY spot."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider unavailable'}), 503

        # Prefer the running instance (has instruments already cached)
        algo = get_instance(username, '3m') or RTPAlgo(username=username, variant='3m')
        result = algo.get_delta_strikes(provider)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[rtp3m/delta-strikes] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp3m/start', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp3m_start() -> EndpointResponse:
    """Start (or restart) the RTP 3m monitoring thread."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        existing = get_instance(username, '3m')
        if existing and existing.is_running():
            return jsonify({'success': False, 'error': 'Algo already running'}), 409

        algo = RTPAlgo(username=username, variant='3m')
        algo.start()
        return jsonify({'success': True, 'message': 'RTP 3m algo started'})
    except Exception as e:
        logger.error(f'[rtp3m/start] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── EMA RTP 2m live algo (same RTP logic, 2-minute candles) ──────────────────

@api_bp.route('/algo/rtp2m/status', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp2m_status() -> EndpointResponse:
    """Return current RTP 2m active trade + live NIFTY spot and P&L."""
    try:
        try:
            with open(_RTP2M_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {'active_trade': None, 'buy_needs_reset': False, 'sell_needs_reset': False}

        trade = state.get('active_trade')
        if not trade:
            return jsonify({'success': True, 'active': False, 'state': state, 'live': None})

        # Fetch live NIFTY spot for P&L calculation
        live = None
        try:
            provider = get_data_provider()
            if provider:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
                if spot:
                    direction  = trade.get('direction', 'BUY')
                    entry_spot = float(trade.get('entry_spot', 0))
                    pnl_pts    = spot - entry_spot if direction == 'BUY' else entry_spot - spot
                    pnl_pts    = round(pnl_pts, 2)
                    broker_entries = trade.get('broker_entries', [])
                    pnl_inr_total  = round(
                        sum(pnl_pts * 0.90 * float(e.get('quantity', 75)) for e in broker_entries), 2
                    )
                    live = {'spot': spot, 'pnl_pts': pnl_pts, 'pnl_inr_total': pnl_inr_total}
                    live.update(_algo_option_live(trade, provider))
        except Exception as _e:
            logger.warning(f'[rtp2m/status] live fetch failed: {_e}')

        return jsonify({'success': True, 'active': True, 'state': state, 'live': live})
    except Exception as e:
        logger.error(f'[rtp2m/status] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp2m/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp2m_history() -> EndpointResponse:
    """Return all completed RTP 2m trades from rtp_trades_all_history_2m.json (latest-first)."""
    try:
        try:
            with open(_RTP2M_ALL_HISTORY_PATH, 'r') as _f:
                all_trades = json.load(_f)
            if not isinstance(all_trades, list):
                all_trades = []
        except Exception:
            all_trades = []

        # Strip broker_entries to keep payload lean
        trades = [
            {k: v for k, v in t.items() if k != 'broker_entries'}
            for t in all_trades
        ]
        return jsonify({'success': True, 'trades': trades, 'count': len(trades)})
    except Exception as e:
        logger.error(f'[rtp2m/history] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp2m/history', methods=['DELETE'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp2m_history_delete() -> EndpointResponse:
    """Delete a trade record by entry_time from both daily and all-time 2m history files."""
    try:
        data       = request.get_json(silent=True) or {}
        entry_time = data.get('entry_time')
        delete_all = bool(data.get('all'))
        if not entry_time and not delete_all:
            return jsonify({'success': False, 'error': 'entry_time or all:true required'}), 400

        for path in [_RTP2M_HISTORY_PATH, _RTP2M_ALL_HISTORY_PATH]:
            try:
                with open(path, 'r') as _f:
                    records = json.load(_f)
                if isinstance(records, list):
                    records = [] if delete_all else \
                        [r for r in records if r.get('entry_time') != entry_time]
                    with open(path, 'w') as _f:
                        json.dump(records, _f, indent=2, default=str)
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'[rtp2m/history/delete] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp2m/exit', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp2m_force_exit() -> EndpointResponse:
    """Manually close the active RTP 2m trade on all brokers."""
    try:
        try:
            with open(_RTP2M_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {}

        if not state.get('active_trade'):
            return jsonify({'success': False, 'error': 'No active trade to exit'}), 400

        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        # Prefer the running instance so cached broker services are reused
        algo = get_instance(username, '2m')
        if algo is None:
            algo = RTPAlgo(username=username, variant='2m')

        provider = get_data_provider()
        spot = 0.0
        if provider:
            try:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
            except Exception:
                pass

        algo._exit_trade('MANUAL', spot)
        return jsonify({'success': True, 'exit_spot': spot})
    except Exception as e:
        logger.error(f'[rtp2m/exit] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp2m/delta-strikes', methods=['GET'])
@require_user_auth
def algo_rtp2m_delta_strikes() -> EndpointResponse:
    """Return the CE and PE strikes closest to ±0.90 delta at the current NIFTY spot."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider unavailable'}), 503

        # Prefer the running instance (has instruments already cached)
        algo = get_instance(username, '2m') or RTPAlgo(username=username, variant='2m')
        result = algo.get_delta_strikes(provider)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[rtp2m/delta-strikes] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp2m/start', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp2m_start() -> EndpointResponse:
    """Start (or restart) the RTP 2m monitoring thread."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        existing = get_instance(username, '2m')
        if existing and existing.is_running():
            return jsonify({'success': False, 'error': 'Algo already running'}), 409

        algo = RTPAlgo(username=username, variant='2m')
        algo.start()
        return jsonify({'success': True, 'message': 'RTP 2m algo started'})
    except Exception as e:
        logger.error(f'[rtp2m/start] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500




# ── EMA RTP 5m live algo (same RTP logic, 5-minute candles) ──────────────────

@api_bp.route('/algo/rtp5m/status', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp5m_status() -> EndpointResponse:
    """Return current RTP 5m active trade + live NIFTY spot and P&L."""
    try:
        try:
            with open(_RTP5M_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {'active_trade': None, 'buy_needs_reset': False, 'sell_needs_reset': False}

        trade = state.get('active_trade')
        if not trade:
            return jsonify({'success': True, 'active': False, 'state': state, 'live': None})

        # Fetch live NIFTY spot for P&L calculation
        live = None
        try:
            provider = get_data_provider()
            if provider:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
                if spot:
                    direction  = trade.get('direction', 'BUY')
                    entry_spot = float(trade.get('entry_spot', 0))
                    pnl_pts    = spot - entry_spot if direction == 'BUY' else entry_spot - spot
                    pnl_pts    = round(pnl_pts, 2)
                    broker_entries = trade.get('broker_entries', [])
                    pnl_inr_total  = round(
                        sum(pnl_pts * 0.90 * float(e.get('quantity', 75)) for e in broker_entries), 2
                    )
                    live = {'spot': spot, 'pnl_pts': pnl_pts, 'pnl_inr_total': pnl_inr_total}
                    live.update(_algo_option_live(trade, provider))
        except Exception as _e:
            logger.warning(f'[rtp5m/status] live fetch failed: {_e}')

        return jsonify({'success': True, 'active': True, 'state': state, 'live': live})
    except Exception as e:
        logger.error(f'[rtp5m/status] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp5m/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp5m_history() -> EndpointResponse:
    """Return all completed RTP 5m trades from rtp_trades_all_history_5m.json (latest-first)."""
    try:
        try:
            with open(_RTP5M_ALL_HISTORY_PATH, 'r') as _f:
                all_trades = json.load(_f)
            if not isinstance(all_trades, list):
                all_trades = []
        except Exception:
            all_trades = []

        # Strip broker_entries to keep payload lean
        trades = [
            {k: v for k, v in t.items() if k != 'broker_entries'}
            for t in all_trades
        ]
        return jsonify({'success': True, 'trades': trades, 'count': len(trades)})
    except Exception as e:
        logger.error(f'[rtp5m/history] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp5m/history', methods=['DELETE'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp5m_history_delete() -> EndpointResponse:
    """Delete a trade record by entry_time from both daily and all-time 5m history files."""
    try:
        data       = request.get_json(silent=True) or {}
        entry_time = data.get('entry_time')
        delete_all = bool(data.get('all'))
        if not entry_time and not delete_all:
            return jsonify({'success': False, 'error': 'entry_time or all:true required'}), 400

        for path in [_RTP5M_HISTORY_PATH, _RTP5M_ALL_HISTORY_PATH]:
            try:
                with open(path, 'r') as _f:
                    records = json.load(_f)
                if isinstance(records, list):
                    records = [] if delete_all else \
                        [r for r in records if r.get('entry_time') != entry_time]
                    with open(path, 'w') as _f:
                        json.dump(records, _f, indent=2, default=str)
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'[rtp5m/history/delete] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp5m/exit', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp5m_force_exit() -> EndpointResponse:
    """Manually close the active RTP 5m trade on all brokers."""
    try:
        try:
            with open(_RTP5M_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {}

        if not state.get('active_trade'):
            return jsonify({'success': False, 'error': 'No active trade to exit'}), 400

        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        # Prefer the running instance so cached broker services are reused
        algo = get_instance(username, '5m')
        if algo is None:
            algo = RTPAlgo(username=username, variant='5m')

        provider = get_data_provider()
        spot = 0.0
        if provider:
            try:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
            except Exception:
                pass

        algo._exit_trade('MANUAL', spot)
        return jsonify({'success': True, 'exit_spot': spot})
    except Exception as e:
        logger.error(f'[rtp5m/exit] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp5m/delta-strikes', methods=['GET'])
@require_user_auth
def algo_rtp5m_delta_strikes() -> EndpointResponse:
    """Return the CE and PE strikes closest to ±0.90 delta at the current NIFTY spot."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider unavailable'}), 503

        # Prefer the running instance (has instruments already cached)
        algo = get_instance(username, '5m') or RTPAlgo(username=username, variant='5m')
        result = algo.get_delta_strikes(provider)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[rtp5m/delta-strikes] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/rtp5m/start', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_rtp5m_start() -> EndpointResponse:
    """Start (or restart) the RTP 5m monitoring thread."""
    try:
        from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        existing = get_instance(username, '5m')
        if existing and existing.is_running():
            return jsonify({'success': False, 'error': 'Algo already running'}), 409

        algo = RTPAlgo(username=username, variant='5m')
        algo.start()
        return jsonify({'success': True, 'message': 'RTP 5m algo started'})
    except Exception as e:
        logger.error(f'[rtp5m/start] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── 2nd 30-Sec Candle live algo ──────────────────────────────────────────────

@api_bp.route('/algo/sc/status', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_sc_status() -> EndpointResponse:
    """Return current 2nd-candle active trade + live NIFTY spot and P&L."""
    try:
        try:
            with open(_SC_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {'active_trade': None, 'traded_today': False}

        trade = state.get('active_trade')
        if not trade:
            return jsonify({'success': True, 'active': False, 'state': state, 'live': None})

        live = None
        try:
            provider = get_data_provider()
            if provider:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
                if spot:
                    direction  = trade.get('direction', 'BUY')
                    entry_spot = float(trade.get('entry_spot', 0))
                    pnl_pts    = spot - entry_spot if direction == 'BUY' else entry_spot - spot
                    pnl_pts    = round(pnl_pts, 2)
                    broker_entries = trade.get('broker_entries', [])
                    pnl_inr_total  = round(
                        sum(pnl_pts * 0.90 * float(e.get('quantity', 75)) for e in broker_entries), 2
                    )
                    live = {'spot': spot, 'pnl_pts': pnl_pts, 'pnl_inr_total': pnl_inr_total}
                    live.update(_algo_option_live(trade, provider))
        except Exception as _e:
            logger.warning(f'[sc/status] live fetch failed: {_e}')

        return jsonify({'success': True, 'active': True, 'state': state, 'live': live})
    except Exception as e:
        logger.error(f'[sc/status] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/sc/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_sc_history() -> EndpointResponse:
    """Return all completed 2nd-candle trades (latest-first)."""
    try:
        try:
            with open(_SC_ALL_HISTORY_PATH, 'r') as _f:
                all_trades = json.load(_f)
            if not isinstance(all_trades, list):
                all_trades = []
        except Exception:
            all_trades = []

        trades = [
            {k: v for k, v in t.items() if k != 'broker_entries'}
            for t in all_trades
        ]
        return jsonify({'success': True, 'trades': trades, 'count': len(trades)})
    except Exception as e:
        logger.error(f'[sc/history] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/sc/history', methods=['DELETE'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_sc_history_delete() -> EndpointResponse:
    """Delete a 2nd-candle trade record by entry_time from both history files."""
    try:
        data       = request.get_json(silent=True) or {}
        entry_time = data.get('entry_time')
        delete_all = bool(data.get('all'))
        if not entry_time and not delete_all:
            return jsonify({'success': False, 'error': 'entry_time or all:true required'}), 400

        for path in [_SC_HISTORY_PATH, _SC_ALL_HISTORY_PATH]:
            try:
                with open(path, 'r') as _f:
                    records = json.load(_f)
                if isinstance(records, list):
                    records = [] if delete_all else \
                        [r for r in records if r.get('entry_time') != entry_time]
                    with open(path, 'w') as _f:
                        json.dump(records, _f, indent=2, default=str)
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'[sc/history/delete] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/sc/exit', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_sc_force_exit() -> EndpointResponse:
    """Manually close the active 2nd-candle trade on all brokers."""
    try:
        try:
            with open(_SC_STATE_PATH, 'r') as _f:
                state = json.load(_f)
        except Exception:
            state = {}

        if not state.get('active_trade'):
            return jsonify({'success': False, 'error': 'No active trade to exit'}), 400

        from trading_app.algo.second_candle.second_candle_algo import SecondCandleAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        algo = get_instance(username)
        if algo is None:
            algo = SecondCandleAlgo(username=username)

        provider = get_data_provider()
        spot = 0.0
        if provider:
            try:
                ltp_data = provider.ltp([_NIFTY_FYERS_IDX])
                spot = float(ltp_data.get(_NIFTY_FYERS_IDX, {}).get('last_price', 0) or 0)
            except Exception:
                pass

        algo._exit_trade('MANUAL', spot)
        return jsonify({'success': True, 'exit_spot': spot})
    except Exception as e:
        logger.error(f'[sc/exit] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/sc/delta-strikes', methods=['GET'])
@require_user_auth
def algo_sc_delta_strikes() -> EndpointResponse:
    """Return the CE and PE strikes closest to ±0.90 delta at the current NIFTY spot."""
    try:
        from trading_app.algo.second_candle.second_candle_algo import SecondCandleAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')
        provider = get_data_provider()
        if not provider:
            return jsonify({'success': False, 'error': 'Data provider unavailable'}), 503

        algo = get_instance(username) or SecondCandleAlgo(username=username)
        result = algo.get_delta_strikes(provider)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[sc/delta-strikes] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Strike-selection mode (shared by all live algos) ─────────────────────────
# 'premium' (default): strike priced inside ₹300–350, nearest ₹300.
# 'delta':             classic ±0.90-delta strike with the ₹500 premium cap.

_ALGO_STRIKE_MODE_VARS = {
    'rtp':    'RTP_1M_STRIKE_MODE',
    'rtp30s': 'RTP_30S_STRIKE_MODE',
    'rtp2m':  'RTP_2M_STRIKE_MODE',
    'rtp3m':  'RTP_3M_STRIKE_MODE',
    'rtp5m':  'RTP_5M_STRIKE_MODE',
    'sc':     'SC_STRIKE_MODE',
}


@api_bp.route('/algo/<algo_key>/strike-mode', methods=['GET', 'POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_strike_mode(algo_key: str) -> EndpointResponse:
    """Get or set the strike-selection mode for one live algo."""
    var = _ALGO_STRIKE_MODE_VARS.get(algo_key)
    if not var:
        return jsonify({'success': False, 'error': f'Unknown algo: {algo_key}'}), 404
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            mode = str(data.get('mode', '')).strip().lower()
            if mode not in ('delta', 'premium', 'premium250'):
                return jsonify({'success': False,
                                'error': "mode must be 'delta', 'premium' or 'premium250'"}), 400
            if not UserEnvManager.save_user_var(username, var, mode):
                return jsonify({'success': False, 'error': 'Failed to save setting'}), 500
            return jsonify({'success': True, 'mode': mode})

        mode = (UserEnvManager.get_user_var(username, var) or 'premium250').strip().lower()
        if mode not in ('delta', 'premium', 'premium250'):
            mode = 'premium250'
        return jsonify({'success': True, 'mode': mode})
    except Exception as e:
        logger.error(f'[algo/strike-mode] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/sc/start', methods=['POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_sc_start() -> EndpointResponse:
    """Start (or restart) the 2nd-candle monitoring thread."""
    try:
        from trading_app.algo.second_candle.second_candle_algo import SecondCandleAlgo, get_instance
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        existing = get_instance(username)
        if existing and existing.is_running():
            return jsonify({'success': False, 'error': 'Algo already running'}), 409

        algo = SecondCandleAlgo(username=username)
        algo.start()
        return jsonify({'success': True, 'message': 'Candle Breakout algo started'})
    except Exception as e:
        logger.error(f'[sc/start] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/sc/settings', methods=['GET', 'POST'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_sc_settings() -> EndpointResponse:
    """Read or update the editable 2nd-candle params (persisted in sc_state.json)."""
    from trading_app.algo.second_candle.second_candle_algo import (
        normalise_params, _atomic_write_json)
    try:
        try:
            with open(_SC_STATE_PATH, 'r') as _f:
                state = json.load(_f)
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}

        if request.method == 'GET':
            return jsonify({'success': True, 'params': normalise_params(state.get('params'))})

        data = request.get_json(silent=True) or {}
        params = normalise_params({**(state.get('params') or {}), **data})
        state['params'] = params
        state.setdefault('active_trade', None)
        # Atomic write: a torn write here would let the algo's _load_state() fall
        # back to a default (traded_today=False) and take a second trade.
        _atomic_write_json(_SC_STATE_PATH, state)
        return jsonify({'success': True, 'params': params})
    except Exception as e:
        logger.error(f'[sc/settings] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/live-configs', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def algo_live_configs() -> EndpointResponse:
    """Return the param sets of every algo currently running live.

    The backtest page uses this to badge a backtest selection / Best Params
    grid row that is already running as a live algo."""
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        username = session.get('username') or os.getenv('MONITORING_USERNAME', 'Mine')

        def _flag_on(var: str) -> bool:
            # "Live" = the per-user kill-switch env flag is enabled. Thread
            # liveness is the wrong signal: monitor threads exit at 15:28 EOD
            # and are started even for disabled variants (the flag only gates
            # signal detection inside the loop).
            return (UserEnvManager.get_user_var(username, var, 'false') or 'false') \
                .strip().lower() == 'true'

        rtp_live = []
        try:
            from trading_app.algo.rtp_railway_track.rtp_algo import RTP_VARIANTS
            for key, v in RTP_VARIANTS.items():
                if _flag_on(v.env_active):
                    rtp_live.append({
                        'variant':    key,
                        'interval':   v.interval,
                        'entry_mode': 'RTP(20 & 9)',   # live signal uses EMA 9 & 20
                        'sl_points':  v.sl_points,
                        'tgt_points': v.tgt_points,
                        'use_adx':    v.use_adx,
                        'adx_thresh': v.adx_thresh,
                        # Entry-reduction filters (mirrored in the live loop, so
                        # live signals match a backtest with the same values).
                        'strict_pattern':   getattr(v, 'strict_pattern', False),
                        'min_rail_gap_atr': getattr(v, 'min_rail_gap_atr', 0.0),
                        'confirm_bars':     getattr(v, 'confirm_bars', 0),
                    })
        except Exception as _e:
            logger.warning(f'[live-configs] rtp: {_e}')

        sc_live = None
        try:
            from trading_app.algo.second_candle.second_candle_algo import (
                normalise_params as _sc_norm)
            if _flag_on('SC_ALGO_ACTIVE'):
                try:
                    with open(_SC_STATE_PATH, 'r') as _f:
                        _sc_state = json.load(_f)
                except Exception:
                    _sc_state = {}
                p = _sc_norm((_sc_state or {}).get('params'))
                direction = ('both' if (p['enable_long'] and p['enable_short'])
                             else 'long' if p['enable_long'] else 'short')
                sc_live = {
                    'interval':     '30second',   # live SC always runs 30-sec candles
                    'candle_index': p['candle_index'],
                    'rr_ratio':     p['rr_ratio'],
                    'direction':    direction,
                    'exit_hour':    p['exit_hour'],
                    'exit_minute':  p['exit_minute'],
                }
        except Exception as _e:
            logger.warning(f'[live-configs] sc: {_e}')

        return jsonify({'success': True, 'rtp': rtp_live, 'second_candle': sc_live})
    except Exception as e:
        logger.error(f'[live-configs] {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


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


@api_bp.route('/vega/history', methods=['GET'])
@csrf.exempt
@limiter.exempt
@require_user_auth
def vega_history() -> EndpointResponse:
    """Return intraday Call/Put Vega time-series computed from stored active_strikes IV data."""
    try:
        from datetime import date as _date
        symbol = request.args.get('symbol', 'NIFTY').upper()
        date_str = request.args.get('date', _date.today().isoformat())
        from trading_app.service.open_interest_service import OpenInterestService
        svc = OpenInterestService(None)
        data = svc.get_intraday_vega_history(symbol, date_str)
        return jsonify({'success': True, 'symbol': symbol, 'date': date_str, 'data': data})
    except Exception as e:
        logger.error(f'[vega/history] {e}', exc_info=True)
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


def _tiered_strike_diff(premium: float) -> int:
    """Escalating strike-diff ladder (100 -> 200 -> 300 -> ...) for gap-day premiums.

    A fixed step (e.g. 50) under-shoots on a gap day: a 240-premium option
    rounds to only 200 away, but the strike needs to sit at least as far out
    as its own premium. Ladder in multiples of 100, so the picked strike
    distance always covers the premium that produced it.
    """
    if not premium or premium <= 0:
        return 100
    return int(math.ceil(premium / 100.0) * 100)


@api_bp.route('/oi-profile/premium-strikes', methods=['GET'])
@csrf.exempt
@limiter.exempt
def oi_profile_premium_strikes() -> EndpointResponse:
    """
    Compute optimal CE/PE strikes for the 'Prem. Str.' mode in OI Profile.

    Algorithm:
    1. Get previous-day index close → round to nearest step = ATM.
    2. For ATM ± 2 steps (5 candidates), fetch previous-day CE and PE closes in parallel.
    3. Pick the candidate closest to ATM where |CE_close - PE_close| <= max_diff (default 25).
    4. CE_strike = round_to_step(base_strike - CE_close)
       PE_strike = round_to_step(base_strike + PE_close)
    5. Fetch prev-day closes for CE_strike{CE,PE} and PE_strike{CE,PE} in parallel.
    6. Return everything needed for the frontend to auto-select strikes and draw lines.
    """
    try:
        symbol   = request.args.get('symbol', 'NIFTY').upper()
        step     = request.args.get('step', 50, type=int) or 50
        max_diff = request.args.get('max_diff', 25, type=float)
        # Manual extra widen: dropdown value N adds N*100 to the total
        # strike_diff, split evenly across both legs (50 per leg per step).
        extra    = request.args.get('extra', 0, type=int) or 0
        extra_leg = max(0, extra) * 50

        kite = get_kite(instance=1)
        _data_provider = get_data_provider()
        if not kite and not _data_provider:
            return jsonify({'success': False, 'error': 'Data provider not connected. Please login.'}), 401

        from trading_app.service.fyers_data_service import FyersDataServiceAdapter
        _is_fyers = isinstance(_data_provider, FyersDataServiceAdapter)
        effective = _data_provider if _data_provider else kite
        kite_svc  = KiteService(kite_instance=effective) if effective else KiteService()

        now = datetime.now()

        # Helper: previous-day close for a single instrument token
        def _fetch_prev_close(token):
            try:
                from_dt = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
                if _is_fyers and _data_provider:
                    # Previous-day daily bars are immutable intraday, so allow the
                    # historical cache to serve them — avoids ~15 fresh /history
                    # calls per request that add to Fyers rate-limit (429) pressure.
                    raw = _data_provider.historical_data(
                        str(token), from_dt.strftime('%Y-%m-%d'),
                        now.strftime('%Y-%m-%d'), 'day', use_cache=True)
                elif kite:
                    raw = kite_svc._historical_with_retry(int(token), from_dt, now, 'day')
                else:
                    return None
                if not raw:
                    return None
                raw.sort(key=lambda x: x['date'])
                today = now.date()
                for bar in reversed(raw):
                    bar_date = bar['date'].date() if hasattr(bar['date'], 'date') else bar['date']
                    if bar_date < today:
                        return float(bar['close'])
            except Exception as exc:
                logger.warning(f'[PremStrikes] prev-close fetch failed for {token}: {exc}')
            return None

        # ── 1. Previous-day index close ────────────────────────────────
        # Kite resolves the index via integer instrument token; Fyers needs its
        # own index symbol (e.g. NSE:NIFTY50-INDEX), so branch on provider.
        # Well-known Kite index tokens (indices are not reliably resolvable via
        # KiteService.get_instrument_token, which returned None here and caused
        # the "Could not fetch previous-day close" error).
        _KITE_INDEX_TOKENS = {
            'NIFTY': 256265, 'BANKNIFTY': 260105, 'FINNIFTY': 257801,
            'MIDCPNIFTY': 288009, 'NIFTY MIDCAP 150': 266249, 'NIFTY AUTO': 263433,
            'NIFTY Smallcap 100': 267017, 'NIFTY SMLCAP 100': 267017,
            'NIFTY FMCG': 261897, 'NIFTY METAL': 263689, 'NIFTY PHARAMA': 262409,
            'NIFTY PHARMA': 262409, 'NIFTY PSU BANK': 262921, 'NIFTY IT': 259849,
        }
        idx_close = None
        if _is_fyers and _data_provider:
            idx_token = FYERS_INDEX_SYMBOLS.get(symbol) or f'NSE:{symbol}-INDEX'
            idx_close = _fetch_prev_close(idx_token)
        else:
            idx_close = kite_svc.get_previous_trading_day_close(symbol)
            if not idx_close:
                # Fallback: resolve the index via the known Kite token directly.
                kite_tok = _KITE_INDEX_TOKENS.get(symbol) or kite_svc.get_instrument_token(symbol)
                if kite_tok:
                    idx_close = _fetch_prev_close(kite_tok)

        logger.info(
            f'[PremStrikes] idx_close resolution: symbol={symbol}, '
            f'provider={"fyers" if _is_fyers else "kite"}, idx_close={idx_close}'
        )
        if not idx_close:
            # Previous-day close unavailable (e.g. Fyers /history rate-limited).
            # Degrade gracefully: derive ATM from the current spot and return the
            # ATM strike for both the CE and PE legs instead of erroring out.
            spot = None
            try:
                if _is_fyers and _data_provider:
                    fsym = FYERS_INDEX_SYMBOLS.get(symbol) or f'NSE:{symbol}-INDEX'
                    q = _data_provider.ltp([fsym]) or {}
                    spot = (q.get(fsym) or {}).get('last_price')
                else:
                    spot = kite_svc.get_current_ltp(symbol)
            except Exception as exc:
                logger.warning(f'[PremStrikes] spot fallback failed for {symbol}: {exc}')

            if not spot:
                return jsonify({'success': False, 'error': 'Could not fetch previous-day close for index'}), 500

            atm_fb = int(round(spot / step) * step)
            logger.info(
                f'[PremStrikes] {symbol}: prev-close unavailable, ATM fallback '
                f'from spot={spot} -> ATM={atm_fb} (returning ATM for both CE & PE)'
            )
            return jsonify({
                'success':        True,
                'symbol':         symbol,
                'atm_fallback':   True,
                'base_strike':    atm_fb,
                'base_ce_close':  None,
                'base_pe_close':  None,
                'ce_strike':      atm_fb,
                'pe_strike':      atm_fb,
                'strike_diff':    0,
                'ce_strike_data': {'ce_close': None, 'pe_close': None},
                'pe_strike_data': {'ce_close': None, 'pe_close': None},
            })

        atm = int(round(idx_close / step) * step)

        # ── 2. Candidate strikes (5) sorted by distance from idx_close ─
        candidates = sorted(
            [atm + i * step for i in range(-2, 3)],
            key=lambda s: abs(s - idx_close)
        )

        # ── 3. Resolve tokens and fetch CE/PE closes for all candidates in parallel ─
        tok_map = {}  # strike -> {ce: token, pe: token}
        for s in candidates:
            ce_tok, _ = _get_cached_strike_token(kite_svc, _data_provider, _is_fyers, symbol, s, 'CE')
            pe_tok, _ = _get_cached_strike_token(kite_svc, _data_provider, _is_fyers, symbol, s, 'PE')
            tok_map[s] = {'ce': ce_tok, 'pe': pe_tok}

        futures1 = {}
        for s, toks in tok_map.items():
            if toks['ce']: futures1[(s, 'ce')] = _api_executor.submit(_fetch_prev_close, toks['ce'])
            if toks['pe']: futures1[(s, 'pe')] = _api_executor.submit(_fetch_prev_close, toks['pe'])

        close1 = {}
        for key, fut in futures1.items():
            try:
                close1[key] = fut.result(timeout=12)
            except Exception:
                close1[key] = None

        # ── 4. Find base strike ────────────────────────────────────────
        base_strike = None
        base_ce_close = None
        base_pe_close = None
        for s in candidates:
            ce_c = close1.get((s, 'ce'))
            pe_c = close1.get((s, 'pe'))
            if ce_c is not None and pe_c is not None and abs(ce_c - pe_c) <= max_diff:
                base_strike, base_ce_close, base_pe_close = s, ce_c, pe_c
                break

        if base_strike is None:
            # Fallback: use ATM even if the condition is not met
            base_strike   = atm
            base_ce_close = close1.get((atm, 'ce'))
            base_pe_close = close1.get((atm, 'pe'))

        # ── 5. Compute CE/PE strikes ───────────────────────────────────
        ce_val   = base_ce_close or 0
        pe_val   = base_pe_close or 0
        # Strike distance from base is the tiered ladder value covering the
        # premium (200 -> 300 -> 400 -> ...), not a plain round to `step` —
        # a fixed step under-shoots on gap days when the premium itself is
        # already bigger than that step (see _tiered_strike_diff). `extra_leg`
        # is the manual widen from the UI dropdown, split evenly per leg.
        ce_strike = int(base_strike - _tiered_strike_diff(ce_val) - extra_leg)
        pe_strike = int(base_strike + _tiered_strike_diff(pe_val) + extra_leg)

        logger.info(
            f'[PremStrikes] {symbol}: idx_close={idx_close:.2f}, ATM={atm}, '
            f'base={base_strike}, CE_close={ce_val:.2f}, PE_close={pe_val:.2f} '
            f'-> CE_strike={ce_strike}, PE_strike={pe_strike}'
        )

        # ── 6. Fetch prev-day closes for CE_strike and PE_strike ───────
        ce_s_ce_tok, _ = _get_cached_strike_token(kite_svc, _data_provider, _is_fyers, symbol, ce_strike, 'CE')
        ce_s_pe_tok, _ = _get_cached_strike_token(kite_svc, _data_provider, _is_fyers, symbol, ce_strike, 'PE')
        pe_s_ce_tok, _ = _get_cached_strike_token(kite_svc, _data_provider, _is_fyers, symbol, pe_strike, 'CE')
        pe_s_pe_tok, _ = _get_cached_strike_token(kite_svc, _data_provider, _is_fyers, symbol, pe_strike, 'PE')

        futures2 = {}
        if ce_s_ce_tok: futures2['ce_s_ce'] = _api_executor.submit(_fetch_prev_close, ce_s_ce_tok)
        if ce_s_pe_tok: futures2['ce_s_pe'] = _api_executor.submit(_fetch_prev_close, ce_s_pe_tok)
        if pe_s_ce_tok: futures2['pe_s_ce'] = _api_executor.submit(_fetch_prev_close, pe_s_ce_tok)
        if pe_s_pe_tok: futures2['pe_s_pe'] = _api_executor.submit(_fetch_prev_close, pe_s_pe_tok)

        # Reuse already-fetched data when computed strike matches the base strike
        prefill = {}
        if ce_strike == base_strike:
            prefill['ce_s_ce'] = base_ce_close
            prefill['ce_s_pe'] = base_pe_close
        if pe_strike == base_strike:
            prefill['pe_s_ce'] = base_ce_close
            prefill['pe_s_pe'] = base_pe_close

        res2 = dict(prefill)
        for k, fut in futures2.items():
            if k in res2:
                continue  # already have it
            try:
                res2[k] = fut.result(timeout=12)
            except Exception:
                res2[k] = None

        return jsonify({
            'success':        True,
            'symbol':         symbol,
            'base_strike':    base_strike,
            'base_ce_close':  base_ce_close,
            'base_pe_close':  base_pe_close,
            'ce_strike':      ce_strike,
            'pe_strike':      pe_strike,
            'strike_diff':    abs(pe_strike - ce_strike),
            'ce_strike_data': {
                'ce_close': res2.get('ce_s_ce'),
                'pe_close': res2.get('ce_s_pe'),
            },
            'pe_strike_data': {
                'ce_close': res2.get('pe_s_ce'),
                'pe_close': res2.get('pe_s_pe'),
            },
        })

    except Exception as exc:
        logger.error(f'[PremStrikes] Error: {exc}', exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


@api_bp.route('/oi-profile/atm-ce-oi-strikes', methods=['GET'])
@csrf.exempt
@limiter.exempt
def oi_profile_atm_ce_oi_strikes() -> EndpointResponse:
    """
    Select two strikes from the ~09:18 OI snapshot for the 'ATM CE OI Lines'
    indicator in OI Profile.

    Finds the ATM strike from the snapshot price, then compares ATM CE OI with
    the adjacent strike CE OI: if the upper strike's CE OI is higher, returns
    [ATM, ATM+step], otherwise [ATM, ATM-step]. NIFTY only by design (the
    frontend only requests it for NIFTY).
    """
    try:
        symbol   = request.args.get('symbol', 'NIFTY').upper()
        step     = request.args.get('step', 50, type=int) or 50
        date_str = request.args.get('date')

        from trading_app.service.open_interest_service import OpenInterestService
        kite = get_kite(instance=1)
        provider = get_data_provider()
        svc = OpenInterestService(provider if provider else kite)
        return jsonify(svc.get_atm_ce_oi_strikes(symbol, step, date_str))

    except Exception as exc:
        logger.error(f'[ATM-CE-OI] Error: {exc}', exc_info=True)
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


@api_bp.route('/oi-historic/predict', methods=['GET'])
@require_user_auth
def oi_historic_predict():
    """Next-session outlook from 5-year conditional statistics of the historic OI data."""
    from trading_app.dashboard.oi_historic_data import analyze_and_predict
    return jsonify(analyze_and_predict('NIFTY'))


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


@api_bp.route('/oi-historic/refresh', methods=['POST'])
@require_user_auth
def oi_historic_refresh():
    """
    Refresh a single (date, symbol) row from every source in one call:
    bhavcopy (OI/OHLC/Fut OI) + NSE participant Fut OI + Moneycontrol FII flow.
    Body: { date: 'YYYY-MM-DD', symbol: 'NIFTY'|'BANKNIFTY' }
    """
    from trading_app.dashboard.oi_historic_data import refresh_record, get_all_records
    from trading_app.service.provider_logic import get_data_provider
    body   = request.get_json(silent=True) or {}
    date   = (body.get('date') or '').strip()
    symbol = (body.get('symbol') or '').strip()
    if not date or not symbol:
        return jsonify({'success': False, 'error': 'date and symbol are required'}), 400
    provider = get_data_provider(user='Mine')
    result   = refresh_record(date, symbol, provider=provider)
    result['records'] = get_all_records()
    return jsonify(result)


@api_bp.route('/oi-historic/load-all', methods=['POST'])
@require_user_auth
def oi_historic_load_all():
    """
    Consolidated load: add new records from NSE bhavcopy or SQLite,
    then patch any remaining records missing OHLC or Fut OI.
    Runs in background; poll /api/oi-historic/load-status for progress.
    Body: { source: 'nse'|'sqlite', from_date: 'YYYY-MM-DD', to_date: 'YYYY-MM-DD' }
    """
    from trading_app.dashboard.oi_historic_data import load_all
    from trading_app.service.provider_logic import get_data_provider
    provider  = get_data_provider(user='Mine')
    body        = request.get_json(silent=True) or {}
    source      = body.get('source', 'nse')
    from_date   = body.get('from_date', '')
    to_date     = body.get('to_date', '')
    symbols     = body.get('symbols') or None
    recalculate = bool(body.get('recalculate', False))
    result      = load_all(from_date, to_date, source=source, symbols=symbols,
                           provider=provider, force=recalculate)
    return jsonify(result)


@api_bp.route('/oi-historic/load-status', methods=['GET'])
@require_user_auth
def oi_historic_load_status():
    """Return progress of the background load-all job."""
    from trading_app.dashboard.oi_historic_data import get_backfill_status
    return jsonify(get_backfill_status())


@api_bp.route('/oi-historic/sync-fii', methods=['POST'])
@require_user_auth
def oi_historic_sync_fii():
    """Fetch last ~30 days of FII index futures flow from Moneycontrol and patch Historic OI records."""
    from trading_app.dashboard.oi_historic_data import sync_fii_from_moneycontrol
    return jsonify(sync_fii_from_moneycontrol())


@api_bp.route('/fii-sector-limits', methods=['GET'])
def get_fii_sector_limits():
    """Return sector-wise FPI data for a period (or latest). Also returns available periods list."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        from trading_app.service.fii_sector_service import FIISectorService
        period  = request.args.get('period')  # optional YYYY-MM-DD
        rows    = FIISectorService.get_sector_fpi_data(period=period)
        periods = FIISectorService.get_periods()
        return jsonify({
            'success': True,
            'data': rows,
            'periods': periods,
            'latest_period': periods[0] if periods else None,
        })
    except Exception as e:
        logger.error(f'FII sector limits error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/fii-sector-update', methods=['POST'])
def fii_sector_update():
    """Check for and fetch any new fortnightly periods since the last stored date."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        from trading_app.service.fii_sector_service import FIISectorService
        result = FIISectorService.check_and_fetch_latest()
        return jsonify({'success': True, **result})
    except Exception as e:
        logger.error(f'FII sector update error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/fii-sector-bulk-init', methods=['POST'])
def fii_sector_bulk_init():
    """Start a background bulk fetch of all historic CDSL periods not yet in DB."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        from trading_app.service.fii_sector_service import FIISectorService
        started = FIISectorService.start_bulk_fetch()
        return jsonify({'success': True, 'started': started})
    except Exception as e:
        logger.error(f'FII sector bulk-init error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/fii-sector-bulk-status', methods=['GET'])
def fii_sector_bulk_status():
    """Return progress of the ongoing (or last completed) bulk fetch."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    from trading_app.service.fii_sector_service import FIISectorService
    # Use thread-safe getter (avoids RuntimeError during dict iteration)
    return jsonify({'success': True, **FIISectorService.get_bulk_status()})


@api_bp.route('/fii-sector-trend', methods=['GET'])
def fii_sector_trend():
    """Return all periods × all sectors NI for the heat map (n= optional limit)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        from trading_app.service.fii_sector_service import FIISectorService
        raw_n = request.args.get('n')
        n = int(raw_n) if raw_n else None  # None = all periods
        data = FIISectorService.get_trend_data(n_periods=n)
        return jsonify({'success': True, **data})
    except Exception as e:
        logger.error(f'FII sector trend error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/fii-sector-all-data', methods=['GET'])
def fii_sector_all_data():
    """Return full rows for every stored period — used for one-shot IDB caching."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    try:
        from trading_app.service.fii_sector_service import FIISectorService
        data   = FIISectorService.get_all_data()    # {date: [rows...]}
        periods = sorted(data.keys(), reverse=True)
        return jsonify({'success': True, 'periods': periods, 'data': data})
    except Exception as e:
        logger.error(f'FII sector all-data error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/fii-sector-delete/<date_str>', methods=['DELETE'])
def fii_sector_delete(date_str):
    """Delete all rows for a specific period date from local SQLite."""
    import re
    auth_error = check_auth()
    if auth_error:
        return auth_error
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return jsonify({'success': False, 'error': 'invalid date format'}), 400
    try:
        from trading_app.service.fii_sector_service import FIISectorService
        ok = FIISectorService.delete_period(date_str)
        return jsonify({'success': ok})
    except Exception as e:
        logger.error(f'FII sector delete error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Swing Trade ───────────────────────────────────────────────────────────────

_SWING_TRADE_CACHE: Dict[str, Any] = {}
_SWING_TRADE_CACHE_TS: Dict[str, float] = {}
_SWING_TRADE_CACHE_TTL = 900  # 15 minutes

@api_bp.route('/swing-trade/stocks', methods=['GET'])
def swing_trade_stocks():
    auth_error = check_auth()
    if auth_error: return auth_error

    index_name = request.args.get('index', 'NIFTY').upper().strip()
    cache_key = f"swing_{index_name}"
    now_ts = _time.time()
    if cache_key in _SWING_TRADE_CACHE and (now_ts - _SWING_TRADE_CACHE_TS.get(cache_key, 0)) < _SWING_TRADE_CACHE_TTL:
        return jsonify(_SWING_TRADE_CACHE[cache_key])

    try:
        import yfinance as yf
        from trading_app.service.dynamic_constituents import DynamicConstituentsService

        stocks = DynamicConstituentsService.get_constituents(index_name)
        yf_symbols = [f"{s}.NS" for s in stocks]

        today = datetime.now().date()
        start = today - timedelta(days=310)

        raw = yf.download(yf_symbols, start=str(start), end=str(today + timedelta(days=1)),
                          interval='1d', auto_adjust=True, progress=False, threads=True)

        if raw.empty:
            return jsonify({'success': False, 'error': 'No data returned from yfinance'}), 502

        if len(yf_symbols) == 1:
            close_df = raw[['Close']].copy()
            close_df.columns = yf_symbols
        else:
            close_df = raw['Close'].copy()

        close_df = close_df.dropna(how='all')
        if close_df.empty:
            return jsonify({'success': False, 'error': 'Empty close data'}), 502

        def lookup(days_ago: int) -> pd.Series:
            target = pd.Timestamp(today - timedelta(days=days_ago))
            available = close_df.index[close_df.index <= target]
            if len(available) == 0:
                return pd.Series(dtype=float)
            return close_df.loc[available[-1]]

        latest     = close_df.iloc[-1]
        prev_close = close_df.iloc[-2] if len(close_df) >= 2 else latest
        wk_ago     = lookup(7)
        mo_ago     = lookup(30)
        mo3_ago    = lookup(91)
        mo6_ago    = lookup(182)
        mo9_ago    = lookup(273)

        def pct(base: pd.Series, sym: str) -> Optional[float]:
            if base.empty: return None
            b = base.get(sym)
            p = latest.get(sym)
            if b is None or p is None: return None
            try:
                bf, pf = float(b), float(p)
            except (TypeError, ValueError):
                return None
            if math.isnan(bf) or math.isnan(pf) or bf == 0:
                return None
            return round((pf - bf) / bf * 100, 2)

        results = []
        for sym, yf_sym in zip(stocks, yf_symbols):
            price_val = latest.get(yf_sym)
            if price_val is None:
                continue
            try:
                price_f = float(price_val)
            except (TypeError, ValueError):
                continue
            if math.isnan(price_f):
                continue
            results.append({
                'symbol':     sym,
                'price':      round(price_f, 2),
                'day_chng':   pct(prev_close, yf_sym),
                'week_chng':  pct(wk_ago,     yf_sym),
                'month_chng': pct(mo_ago,      yf_sym),
                'mo3_chng':   pct(mo3_ago,     yf_sym),
                'mo6_chng':   pct(mo6_ago,     yf_sym),
                'mo9_chng':   pct(mo9_ago,     yf_sym),
            })

        payload = {'success': True, 'stocks': results, 'index': index_name}
        _SWING_TRADE_CACHE[cache_key] = payload
        _SWING_TRADE_CACHE_TS[cache_key] = now_ts
        return jsonify(payload)

    except Exception as e:
        logger.error(f'[SwingTrade] error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Swing Momentum Backtest ───────────────────────────────────────────────────

@api_bp.route('/backtest/swing-momentum', methods=['POST'])
def backtest_swing_momentum():
    auth_error = check_auth()
    if auth_error:
        return auth_error
    body = request.get_json() or {}
    try:
        from trading_app.Backtest.swing_momentum_engine import SwingMomentumEngine
        engine = SwingMomentumEngine(
            index_name    = body.get('index', 'NIFTY 500'),
            start_date    = body['start_date'],
            end_date      = body['end_date'],
            rebalance_freq= body.get('rebalance_freq', 'monthly'),
            investment    = float(body.get('investment', 100000)),
            top_n         = int(body.get('top_n', 10)),
            exit_rank     = int(body.get('exit_rank', 50)),
            monthly_add   = float(body.get('monthly_add', 0)),
        )
        result = engine.run()
        return jsonify({'success': True, **result})
    except Exception as e:
        logger.error(f'[SwingMomentum] backtest error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/backtest/swing-momentum/optimise', methods=['POST'])
def backtest_sm_optimise():
    auth_error = check_auth()
    if auth_error:
        return auth_error
    body            = request.get_json() or {}
    start_date      = body.get('start_date', '2022-01-01')
    end_date        = body.get('end_date') or datetime.today().strftime('%Y-%m-%d')
    investment      = float(body.get('investment', 100000))
    rebalance_freq  = body.get('rebalance_freq', 'monthly')
    recalc          = bool(body.get('recalculate', False))
    cache_key       = f"full_{start_date}_{end_date}_{rebalance_freq}"

    # Serve from disk cache immediately (no background task needed)
    if not recalc:
        cache = _load_sm_opt_cache()
        if cache_key in cache:
            return jsonify({'success': True, 'from_cache': True, **cache[cache_key]})

    # Spawn background thread and return task_id to client immediately
    task_id = str(uuid.uuid4())
    with _sm_opt_tasks_lock:
        _sm_opt_tasks[task_id] = {'status': 'running', 'started_at': _time.time()}

    def _run():
        try:
            from trading_app.Backtest.swing_momentum_engine import optimise_swing_momentum_full
            results = optimise_swing_momentum_full(start_date, end_date, investment, rebalance_freq)
            payload = {
                'start_date':          start_date,
                'end_date':            end_date,
                'rebalance_freq':      rebalance_freq,
                'total_combos_tested': len(results),
                'best':                results[0] if results else None,
                'results':             results[:15],
                'cached_at':           datetime.now().strftime('%Y-%m-%d %H:%M'),
            }
            disk_cache = _load_sm_opt_cache()
            disk_cache[cache_key] = payload
            _save_sm_opt_cache(disk_cache)
            with _sm_opt_tasks_lock:
                _sm_opt_tasks[task_id] = {'status': 'complete', 'payload': payload}
        except Exception as e:
            logger.error(f'[SMOptimise] background error: {e}', exc_info=True)
            with _sm_opt_tasks_lock:
                _sm_opt_tasks[task_id] = {'status': 'error', 'error': str(e)}

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'success': True, 'task_id': task_id, 'status': 'running'})


@api_bp.route('/backtest/swing-momentum/optimise/status/<task_id>', methods=['GET'])
def backtest_sm_optimise_status(task_id):
    auth_error = check_auth()
    if auth_error:
        return auth_error
    with _sm_opt_tasks_lock:
        task = _sm_opt_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': 'Task not found'}), 404
    if task['status'] == 'running':
        return jsonify({'success': True, 'status': 'running'})
    if task['status'] == 'error':
        return jsonify({'success': False, 'status': 'error', 'error': task.get('error', 'Unknown error')}), 500
    # complete
    return jsonify({'success': True, 'status': 'complete', 'from_cache': False, **task['payload']})


def _analyze_contract_direction(contracts, spot):
    """Derive an UP/DOWN/SIDEWAYS market-direction read from the active contract list.

    Signals (weights): CE-vs-PE volume-weighted premium momentum (2),
    spot trend (1.5), volume PCR (1), spot vs max-volume strike levels (0.5).
    """
    ces = [c for c in contracts if c.get('option_type') == 'CE' and c.get('volume', 0) > 0]
    pes = [c for c in contracts if c.get('option_type') == 'PE' and c.get('volume', 0) > 0]
    if not ces or not pes:
        return {
            'available': False,
            'reason': 'Direction analysis needs live volume on both Calls and Puts '
                      '(clear the Option Type filter or wait for market data)',
        }

    def _vw_move(rows):
        total_vol = sum(r['volume'] for r in rows)
        if not total_vol:
            return 0.0
        return round(sum(r.get('pct_change', 0) * r['volume'] for r in rows) / total_vol, 2)

    ce_vol  = sum(c['volume'] for c in ces)
    pe_vol  = sum(c['volume'] for c in pes)
    ce_move = _vw_move(ces)
    pe_move = _vw_move(pes)
    volume_pcr = round(pe_vol / ce_vol, 2) if ce_vol else 0.0

    # Most-traded strikes act as intraday magnets: heavy CE strike ≈ resistance, heavy PE strike ≈ support
    resistance = max(ces, key=lambda c: c['volume']).get('strike')
    support    = max(pes, key=lambda c: c['volume']).get('strike')

    signals = []
    score = 0.0

    # 1. Premium momentum — calls gaining while puts bleed (or vice-versa) is the cleanest directional tell
    if ce_move > 0.5 and pe_move < -0.5:
        score += 2
        signals.append({'name': 'Premium Momentum', 'verdict': 'bullish',
                        'detail': f'Call premiums rising ({ce_move:+.1f}%) while put premiums fall ({pe_move:+.1f}%) — buyers positioned for upside'})
    elif pe_move > 0.5 and ce_move < -0.5:
        score -= 2
        signals.append({'name': 'Premium Momentum', 'verdict': 'bearish',
                        'detail': f'Put premiums rising ({pe_move:+.1f}%) while call premiums fall ({ce_move:+.1f}%) — buyers positioned for downside'})
    elif ce_move > 0.5 and pe_move > 0.5:
        signals.append({'name': 'Premium Momentum', 'verdict': 'neutral',
                        'detail': f'Both call ({ce_move:+.1f}%) and put ({pe_move:+.1f}%) premiums rising — volatility building, big move expected but direction unclear'})
    elif ce_move < -0.5 and pe_move < -0.5:
        signals.append({'name': 'Premium Momentum', 'verdict': 'neutral',
                        'detail': f'Both call ({ce_move:+.1f}%) and put ({pe_move:+.1f}%) premiums decaying — option writers in control, rangebound/sideways day'})
    else:
        signals.append({'name': 'Premium Momentum', 'verdict': 'neutral',
                        'detail': f'No decisive premium shift (CE {ce_move:+.1f}%, PE {pe_move:+.1f}%)'})

    # 2. Spot trend
    spot_pct = float(spot.get('pct_change', 0) or 0) if spot else 0.0
    if spot:
        if spot_pct >= 0.15:
            score += 1.5
            signals.append({'name': 'Spot Trend', 'verdict': 'bullish',
                            'detail': f'Index trading up {spot_pct:+.2f}% on the day'})
        elif spot_pct <= -0.15:
            score -= 1.5
            signals.append({'name': 'Spot Trend', 'verdict': 'bearish',
                            'detail': f'Index trading down {spot_pct:+.2f}% on the day'})
        else:
            signals.append({'name': 'Spot Trend', 'verdict': 'neutral',
                            'detail': f'Index flat ({spot_pct:+.2f}%) — no trend from spot yet'})

    # 3. Volume PCR (traded contracts, not OI)
    if volume_pcr <= 0.7:
        score += 1
        signals.append({'name': 'Volume PCR', 'verdict': 'bullish',
                        'detail': f'Call-heavy trading (PCR {volume_pcr}) — participants chasing upside'})
    elif volume_pcr >= 1.3:
        score -= 1
        signals.append({'name': 'Volume PCR', 'verdict': 'bearish',
                        'detail': f'Put-heavy trading (PCR {volume_pcr}) — hedging/downside bets dominate'})
    else:
        signals.append({'name': 'Volume PCR', 'verdict': 'neutral',
                        'detail': f'Balanced call/put volume (PCR {volume_pcr})'})

    # 4. Spot vs most-traded strikes (breakout / breakdown check)
    spot_last = float(spot.get('last', 0) or 0) if spot else 0.0
    if spot_last and support and resistance:
        if spot_last > resistance:
            score += 0.5
            signals.append({'name': 'Key Levels', 'verdict': 'bullish',
                            'detail': f'Spot ({spot_last:,.0f}) above the heaviest call strike {resistance:,.0f} — breakout territory'})
        elif spot_last < support:
            score -= 0.5
            signals.append({'name': 'Key Levels', 'verdict': 'bearish',
                            'detail': f'Spot ({spot_last:,.0f}) below the heaviest put strike {support:,.0f} — breakdown territory'})
        else:
            signals.append({'name': 'Key Levels', 'verdict': 'neutral',
                            'detail': f'Spot ({spot_last:,.0f}) inside the {support:,.0f} – {resistance:,.0f} high-volume range (support – resistance)'})

    max_score = 5.0
    direction = 'UP' if score >= 1.5 else 'DOWN' if score <= -1.5 else 'SIDEWAYS'
    return {
        'available':      True,
        'direction':      direction,
        'score':          round(score, 1),
        'max_score':      max_score,
        'confidence_pct': round(abs(score) / max_score * 100),
        'volume_pcr':     volume_pcr,
        'ce_move_pct':    ce_move,
        'pe_move_pct':    pe_move,
        'ce_volume':      ce_vol,
        'pe_volume':      pe_vol,
        'support':        support,
        'resistance':     resistance,
        'signals':        signals,
    }


@api_bp.route('/active-contracts', methods=['GET'])
def get_active_contracts():
    """Return active F&O contracts with live market data (OHLC, volume, change) from Fyers."""
    auth_error = check_auth()
    if auth_error:
        return auth_error

    underlying    = request.args.get('underlying', 'NIFTY').strip().upper()
    type_filter   = request.args.get('type', 'all').strip().upper()
    expiry_filter = request.args.get('expiry', '').strip()
    strike_filter = request.args.get('strike', '').strip()

    provider = get_data_provider()
    if not provider:
        return jsonify({'success': False, 'error': 'No data provider available'}), 401

    if not hasattr(provider, 'instruments'):
        from trading_app.service.fyers_data_service import FyersDataServiceAdapter
        provider = FyersDataServiceAdapter(fyers_instance=None)

    import datetime as _dt
    from itertools import groupby as _groupby
    today = _dt.date.today()

    try:
        all_instruments = provider.instruments('NFO')
    except Exception as e:
        logger.error(f'[active-contracts] instruments fetch error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

    # All available expiries for this underlying (for dropdown)
    all_expiries_raw = sorted(set(
        i['expiry'] for i in all_instruments
        if i.get('name', '').strip().upper() == underlying
        and i.get('expiry') is not None and i.get('expiry') >= today
    ))
    monthly_set = set()
    for _k, grp in _groupby(all_expiries_raw, key=lambda d: (d.year, d.month)):
        monthly_set.add(list(grp)[-1])
    expiry_options = [{
        'date':       e.isoformat(),
        'label':      ('Monthly ' if e in monthly_set else 'Weekly ') + e.strftime('%d %b %Y'),
        'is_monthly': e in monthly_set,
    } for e in all_expiries_raw]

    # Default to nearest expiry (keeps quote batch size small)
    active_expiry = expiry_filter or (all_expiries_raw[0].isoformat() if all_expiries_raw else '')

    filtered = [
        i for i in all_instruments
        if i.get('name', '').strip().upper() == underlying
        and i.get('expiry') is not None and i.get('expiry') >= today
        and i['expiry'].isoformat() == active_expiry
        and (type_filter == 'ALL' or i.get('instrument_type', '').upper() == type_filter)
        and (not strike_filter or str(int(float(i.get('strike', 0) or 0))) == strike_filter)
    ]
    filtered.sort(key=lambda x: (x.get('strike', 0) or 0))

    # Build base contract list
    contracts = []
    for inst in filtered:
        inst_type = inst.get('instrument_type', '')
        instrument_label = 'Index Futures' if inst_type == 'FUT' else 'Index Options'
        contracts.append({
            'instrument_token': inst.get('instrument_token', ''),
            'symbol':           inst.get('tradingsymbol', ''),
            'instrument_type':  instrument_label,
            'expiry':           inst['expiry'].isoformat(),
            'option_type':      inst_type if inst_type in ('CE', 'PE') else '',
            'strike':           inst.get('strike') if inst_type != 'FUT' else None,
            'lot_size':         inst.get('lot_size', 0),
            # market data defaults (populated below)
            'open': 0, 'high': 0, 'low': 0, 'close': 0,
            'prev_close': 0, 'last': 0, 'change': 0, 'pct_change': 0, 'volume': 0,
        })

    # Fetch live quotes from Fyers raw API (index spot rides along in the first batch)
    spot_symbol = FYERS_INDEX_SYMBOLS.get(underlying)
    quote_map = {}
    if contracts and hasattr(provider, 'fyers') and provider.fyers:
        try:
            from trading_app.service.fyers_data_service import _rate_limiter
            tokens = [c['instrument_token'] for c in contracts if c['instrument_token']]
            if spot_symbol:
                tokens.insert(0, spot_symbol)
            batch_size = 50
            for i in range(0, len(tokens), batch_size):
                batch = tokens[i:i + batch_size]
                _rate_limiter.wait()
                resp = provider.fyers.quotes(data={'symbols': ','.join(batch)})
                if resp and resp.get('s') == 'ok':
                    for item in resp.get('d', []):
                        n = item.get('n', '')
                        v = item.get('v', {})
                        lp         = float(v.get('lp', 0) or 0)
                        prev_close = float(v.get('prev_close_price', 0) or 0)
                        ch         = float(v.get('ch', lp - prev_close if prev_close else 0) or 0)
                        chp        = float(v.get('chp', round(ch / prev_close * 100, 2) if prev_close else 0) or 0)
                        quote_map[n] = {
                            'open':       float(v.get('open_price', 0) or 0),
                            'high':       float(v.get('high_price', 0) or 0),
                            'low':        float(v.get('low_price', 0) or 0),
                            'close':      float(v.get('close_price', prev_close) or prev_close),
                            'prev_close': prev_close,
                            'last':       lp,
                            'change':     round(ch, 2),
                            'pct_change': round(chp, 2),
                            'volume':     int(v.get('volume', 0) or 0),
                        }
            for c in contracts:
                q = quote_map.get(c['instrument_token'], {})
                if q:
                    c.update(q)
        except Exception as e:
            logger.warning(f'[active-contracts] quote fetch error: {e}')

    # Sort by volume desc (most active first)
    contracts.sort(key=lambda x: x.get('volume', 0), reverse=True)

    # Underlying spot snapshot + market-direction analysis from the contract list
    spot_q = quote_map.get(spot_symbol) if spot_symbol else None
    spot_payload = None
    if spot_q:
        spot_payload = {
            'symbol':     spot_symbol,
            'last':       spot_q.get('last', 0),
            'change':     spot_q.get('change', 0),
            'pct_change': spot_q.get('pct_change', 0),
        }

    return jsonify({
        'success':        True,
        'underlying':     underlying,
        'active_expiry':  active_expiry,
        'as_of':          today.isoformat(),
        'total':          len(contracts),
        'contracts':      contracts,
        'expiry_options': expiry_options,
        'spot':           spot_payload,
        'analysis':       _analyze_contract_direction(contracts, spot_payload),
    })


# ── Algo: Swing Momentum Live Configs ─────────────────────────────────────────

_SM_LIVE_CONFIGS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'algo', 'swing_momentum', 'sm_live_configs.json')
)


def _sm_load_live_configs() -> list:
    if not os.path.exists(_SM_LIVE_CONFIGS_PATH):
        return []
    try:
        with open(_SM_LIVE_CONFIGS_PATH, 'r') as f:
            return json.load(f).get('configs', [])
    except Exception:
        return []


def _sm_save_live_configs(configs: list) -> None:
    os.makedirs(os.path.dirname(_SM_LIVE_CONFIGS_PATH), exist_ok=True)
    with open(_SM_LIVE_CONFIGS_PATH, 'w') as f:
        json.dump({'configs': configs}, f, indent=2)


def _sm_compute_today_rankings(index_name: str):
    """Return today's momentum rankings using avg(3M, 6M, 9M) — same as Swing Trade tab.

    Returns (ranked_list, close_df).  ranked_list is sorted descending by score
    and each entry has: symbol, yf_sym, score, rank, price.
    Returns (None, None) on data failure.
    """
    import math
    import yfinance as yf
    from trading_app.service.dynamic_constituents import DynamicConstituentsService

    stocks     = DynamicConstituentsService.get_constituents(index_name)
    yf_symbols = [f"{s}.NS" for s in stocks]

    today = datetime.today().date()
    start = today - timedelta(days=310)

    raw = yf.download(yf_symbols, start=str(start),
                      end=str(today + timedelta(days=1)),
                      interval='1d', auto_adjust=True, progress=False, threads=False)
    if raw is None or raw.empty:
        return None, None

    close_df = (raw[['Close']].copy().rename(columns={'Close': yf_symbols[0]})
                if len(yf_symbols) == 1 else raw['Close'].copy())
    close_df = close_df.dropna(how='all')
    if close_df.empty:
        return None, None

    def _lookup(days_ago):
        tgt  = pd.Timestamp(today - timedelta(days=days_ago))
        avail = close_df.index[close_df.index <= tgt]
        return close_df.loc[avail[-1]] if len(avail) else pd.Series(dtype=float)

    latest  = close_df.iloc[-1]
    mo3_ago = _lookup(91)
    mo6_ago = _lookup(182)
    mo9_ago = _lookup(273)

    def _pct(base, yf_sym):
        if base.empty:
            return None
        b, p = base.get(yf_sym), latest.get(yf_sym)
        if b is None or p is None:
            return None
        try:
            bf, pf = float(b), float(p)
        except (TypeError, ValueError):
            return None
        if math.isnan(bf) or math.isnan(pf) or bf == 0:
            return None
        return (pf - bf) / bf * 100

    ranked = []
    for sym, yf_sym in zip(stocks, yf_symbols):
        price = latest.get(yf_sym)
        try:
            pf = float(price)
        except (TypeError, ValueError):
            continue
        if price is None or math.isnan(pf):
            continue
        if pf > 10_000:
            continue
        r3 = _pct(mo3_ago, yf_sym)
        r6 = _pct(mo6_ago, yf_sym)
        r9 = _pct(mo9_ago, yf_sym)
        # Use average of available periods (matches Swing Trade tab logic)
        # Require at least 2 of 3 periods so ranking stays meaningful
        avail = [r for r in (r3, r6, r9) if r is not None]
        if len(avail) < 2:
            continue
        ranked.append({'symbol': sym, 'yf_sym': yf_sym,
                       'score': sum(avail) / len(avail), 'price': round(pf, 2)})

    ranked.sort(key=lambda x: x['score'], reverse=True)
    for i, s in enumerate(ranked):
        s['rank'] = i + 1

    return ranked, close_df


def _sm_rankings_cached(index_name: str):
    """Return today's rankings, using a 15-min in-memory cache to avoid repeated full downloads."""
    now = _time.monotonic()
    entry = _sm_rankings_cache.get(index_name)
    if entry:
        ts, ranked = entry
        if now - ts < _SM_RANKINGS_TTL:
            return ranked
    ranked, _ = _sm_compute_today_rankings(index_name)
    if ranked:
        _sm_rankings_cache[index_name] = (now, ranked)
    return ranked


@api_bp.route('/algo/swing-momentum/configs', methods=['GET'])
def sm_live_configs_list():
    return jsonify({'success': True, 'configs': _sm_load_live_configs()})


# ── Broker order placement for Swing Momentum Go Live ─────────────────────────

def _sm_build_order_service(username: str, instance_num: int, broker_type: str):
    """Construct an order service for one configured broker instance, or None."""
    from trading_app.app.utils.user_env import UserEnvManager
    pfx = f'BROKER_{instance_num}_'

    def env(field):
        return (UserEnvManager.get_user_var(username, pfx + field) or '').strip()

    try:
        if broker_type == 'fyers':
            from trading_app.service.fyers_order_services import FyersOrderService
            at = session.get(f'fyers_{instance_num}_access_token') or env('ACCESS_TOKEN')
            if not at:
                return None
            return FyersOrderService(app_id=env('APP_ID'), access_token=at)

        if broker_type == 'dhan':
            from trading_app.service.dhan_order_services import DhanOrderService
            at = env('ACCESS_TOKEN')
            cid = env('CLIENT_ID')
            if not at or not cid:
                return None
            return DhanOrderService(access_token=at, client_id=cid)

        if broker_type == 'zerodha':
            # Use the canonical builder: it reads the fresh env access token
            # (source of truth, refreshed on login) and applies the SSH/SOCKS proxy.
            # Zerodha tokens expire daily, so preferring a possibly-stale session
            # token here silently broke order placement.
            return get_kite(user=username, instance=instance_num)

        if broker_type == 'kotak':
            from trading_app.service.kotak_order_services import KotakOrderService
            return KotakOrderService(consumer_key=env('CONSUMER_KEY'), ucc=env('UCC'))
    except Exception as e:
        logger.error(f'[sm-order] build service {broker_type}_{instance_num} failed: {e}')
    return None


def _sm_place_equity_order(broker_type: str, svc, symbol: str, qty: int, side: str = 'BUY',
                           price: Optional[float] = None):
    """Place a CNC MARKET BUY/SELL for one NSE equity. Returns (order_id, error).

    `price` is the last known price (from the data provider) used to price the
    padded-LIMIT fallback when a broker blocks bare MARKET orders.
    """
    side = side.upper()
    try:
        if broker_type == 'fyers':
            r = svc.place_order(symbol=f'NSE:{symbol}-EQ', side=(1 if side == 'BUY' else -1),
                                quantity=qty, order_type=2, product_type='CNC')
            return (r.get('order_id'), None) if r.get('success') else (None, r.get('error'))

        if broker_type == 'dhan':
            sec_id = (svc._symbol_master_data or {}).get(symbol) \
                  or (svc._symbol_master_data or {}).get(f'NSE:{symbol}')
            if not sec_id:
                return (None, f'No Dhan security_id for {symbol}')
            r = svc.place_order(security_id=str(sec_id), transaction_type=side, quantity=qty,
                                order_type='MARKET', product_type='CNC', exchange_segment='NSE_EQ')
            return (r.get('order_id'), None) if r.get('success') else (None, r.get('error'))

        if broker_type == 'zerodha':
            txn    = svc.TRANSACTION_TYPE_BUY if side == 'BUY' else svc.TRANSACTION_TYPE_SELL
            common = dict(variety=svc.VARIETY_REGULAR, exchange=svc.EXCHANGE_NSE,
                          tradingsymbol=symbol, transaction_type=txn,
                          quantity=qty, product=svc.PRODUCT_CNC)
            try:
                # Try a plain MARKET order first via the SDK (no special permission).
                oid = svc.place_order(order_type=svc.ORDER_TYPE_MARKET, **common)
                return (oid, None)
            except Exception as e:
                msg = str(e).lower()
                if all(s not in msg for s in ('market protection', 'limit order', 'market order')):
                    raise
                # Zerodha blocks bare MARKET orders on NSE equity via API. Fall back to
                # a padded LIMIT (buy +5% / sell -5%) that fills like a market order —
                # still a normal order, no market_protection / market-data permission.
                # Prefer the price passed in (data provider); only hit Kite ltp if
                # nothing was supplied (that call needs a market-data subscription).
                px = float(price) if price else 0
                if not px:
                    try:
                        ltp = svc.ltp(f'NSE:{symbol}')
                        px = (ltp.get(f'NSE:{symbol}') or {}).get('last_price', 0) if isinstance(ltp, dict) else 0
                    except Exception:
                        px = 0
                if not px:
                    return (None, 'Market order blocked and no price available for LIMIT fallback')
                # Place the LIMIT at the current price (snapped to a valid ₹0.05 tick:
                # round up for a BUY / down for a SELL so it still fills). This stays
                # within the circuit band by definition — no padding over the limit.
                import math
                tick = 0.05
                limit_price = round((math.ceil(px / tick) if side == 'BUY'
                                     else math.floor(px / tick)) * tick, 2)
                logger.warning(f'[sm-order] {symbol}: MARKET blocked, retrying as LIMIT @ {limit_price}')
                try:
                    oid = svc.place_order(order_type=svc.ORDER_TYPE_LIMIT, price=limit_price, **common)
                    return (oid, None)
                except Exception as e2:
                    # Padded price crossed the price band → cap to the circuit limit
                    # Zerodha reports in the error, then place within range.
                    import re, math
                    m = re.search(r'circuit limit of ([\d,]+\.?\d*)', str(e2))
                    if not m:
                        raise
                    band = float(m.group(1).replace(',', ''))
                    # Snap to a valid ₹0.05 tick on the safe side of the band:
                    # floor for a BUY (stay ≤ upper circuit), ceil for a SELL.
                    tick = 0.05
                    capped = (math.floor(band / tick) if side == 'BUY'
                              else math.ceil(band / tick)) * tick
                    capped = round(capped, 2)
                    logger.warning(f'[sm-order] {symbol}: LIMIT capped to circuit band {band} -> {capped}')
                    oid = svc.place_order(order_type=svc.ORDER_TYPE_LIMIT, price=capped, **common)
                    return (oid, None)

        if broker_type == 'kotak':
            r = svc.place_order(tradingsymbol=symbol, transaction_type=side, price=0.0,
                                quantity=qty, exchange_segment='nse_cm',
                                product='CNC', order_type='MKT')
            return (r.get('order_id'), None) if r.get('success') else (None, r.get('error'))
    except Exception as e:
        return (None, str(e))
    return (None, 'Unsupported broker')


def _sm_avg_fill_price(broker_type: str, svc, order_id) -> Optional[float]:
    """Read back the average traded price for a filled order, or None."""
    if not order_id:
        return None
    try:
        if broker_type == 'fyers':
            for o in (svc.get_orderbook().get('orders') or []):
                if str(o.get('id')) == str(order_id):
                    return float(o.get('tradedPrice') or o.get('avgPrice') or 0) or None
        elif broker_type == 'dhan':
            for o in (svc.get_order_book().get('orders') or []):
                if str(o.get('orderId')) == str(order_id):
                    return float(o.get('averageTradedPrice') or o.get('price') or 0) or None
        elif broker_type == 'zerodha':
            for o in svc.orders():
                if str(o.get('order_id')) == str(order_id):
                    return float(o.get('average_price') or 0) or None
    except Exception as e:
        logger.error(f'[sm-order] avg price read {broker_type} failed: {e}')
    return None


def _sm_record_exit(config: dict, symbol: str, qty: int, entry_price: float,
                    entry_date: str, exit_price: float, exit_date: Optional[str] = None):
    """Append a realized-exit record to the config's exit_history (per group)."""
    qty      = int(qty)
    invested = round(entry_price * qty, 2)
    final    = round(exit_price * qty, 2)
    config.setdefault('exit_history', []).append({
        'symbol':      symbol,
        'qty':         qty,
        'entry_date':  entry_date or '',
        'exit_date':   exit_date or datetime.today().strftime('%Y-%m-%d'),
        'entry_price': round(entry_price, 2),
        'exit_price':  round(exit_price, 2),
        'invested':    invested,
        'final_value': final,
        'pnl':         round(final - invested, 2),
        'pnl_pct':     round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0,
    })


def _sm_place_portfolio_orders(username: str, instance_num: int, broker_type: str,
                               broker_name: str, live_entries: list):
    """Place CNC MARKET BUY for every holding on the chosen broker.

    Mutates live_entries in place: sets entry_price to the avg fill price (when
    available) and attaches an 'order' record. Returns a summary dict.
    """
    import time as _t
    svc = _sm_build_order_service(username, instance_num, broker_type)
    if svc is None:
        return {'placed': 0, 'failed': len(live_entries),
                'error': f'{broker_name}: not connected / missing credentials'}

    # Fresh prices (via data provider) to price any padded-LIMIT fallback; fall
    # back to the stored entry_price if a quote is missing.
    prices = _sm_current_prices([e['symbol'] for e in live_entries])

    placed, failed, order_ids = 0, 0, []
    first_err = None
    for e in live_entries:
        px = prices.get(e['symbol']) or e.get('entry_price')
        oid, err = _sm_place_equity_order(broker_type, svc, e['symbol'], e['qty'], 'BUY', price=px)
        if oid:
            placed += 1
            order_ids.append((e, oid))
            e['order'] = {'broker': broker_name, 'broker_type': broker_type,
                          'instance': instance_num, 'order_id': str(oid), 'status': 'placed'}
        else:
            failed += 1
            first_err = first_err or err
            logger.error(f'[sm-order] {broker_name} BUY {e["qty"]} {e["symbol"]} failed: {err}')
            e['order'] = {'broker': broker_name, 'broker_type': broker_type,
                          'instance': instance_num, 'order_id': None,
                          'status': 'failed', 'error': err}

    # Give the exchange a moment to fill MARKET orders, then read avg prices
    if order_ids:
        _t.sleep(2.0)
        for e, oid in order_ids:
            avg = _sm_avg_fill_price(broker_type, svc, oid)
            if avg and avg > 0:
                e['entry_price'] = round(avg, 2)
                e['order']['avg_price'] = round(avg, 2)
                e['order']['status'] = 'filled'

    # Surface the real broker rejection (e.g. "Insufficient funds") rather than a
    # generic message, so the user can act on it.
    err_msg = None
    if not placed:
        err_msg = f'{broker_name}: {first_err}' if first_err else f'{broker_name}: no orders placed'
    return {'placed': placed, 'failed': failed, 'broker': broker_name, 'error': err_msg}


def _sm_current_prices(symbols: list) -> dict:
    """Return {symbol: last_price} via Fyers (preferred) or yfinance fallback."""
    prices = {}
    try:
        provider = get_data_provider()
        if provider is not None:
            quotes = provider.quote([f'NSE:{s}-EQ' for s in symbols])
            for s in symbols:
                q = quotes.get(f'NSE:{s}-EQ') or quotes.get(f'NSE:{s}')
                if q and q.get('last_price', 0) > 0:
                    prices[s] = round(float(q['last_price']), 2)
    except Exception:
        pass

    missing = [s for s in symbols if s not in prices]
    if missing:
        try:
            import yfinance as yf
            px = yf.download([f'{s}.NS' for s in missing], period='5d', interval='1d',
                             auto_adjust=True, progress=False, threads=False)
            closes = (px['Close'] if len(missing) > 1
                      else px[['Close']].rename(columns={'Close': f'{missing[0]}.NS'}))
            for s in missing:
                col = closes.get(f'{s}.NS')
                if col is not None:
                    clean = col.dropna()
                    if not clean.empty:
                        prices[s] = round(float(clean.iloc[-1]), 2)
        except Exception:
            pass
    return prices


@api_bp.route('/algo/swing-momentum/configs/<config_id>/sip-swp', methods=['POST'])
def sm_live_sip_swp(config_id):
    """Execute a SIP (buy more) or SWP (sell) split equally across all holdings.

    Body: {mode: 'sip'|'swp', amount: float, note?, allocations?: [{symbol, qty}],
           broker_instance?, broker_type?, broker_name?}

    - SIP: buy `qty` of each holding, update entry_price to the new weighted average.
    - SWP: sell `qty` of each holding (capped at held qty); avg entry unchanged.
    - With a broker: places CNC MARKET orders and uses the avg fill price.
    - Without a broker: uses the current market price as the fill price.
    Logs the flow in monthly_investment_log (amount > 0 for SIP, < 0 for SWP).
    """
    body    = request.get_json() or {}
    mode    = (body.get('mode') or 'sip').lower()
    amount  = float(body.get('amount', 0) or 0)
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    entries = config.get('live_entries') or []
    if not entries:
        return jsonify({'success': False, 'error': 'No holdings to transact'}), 400
    if mode not in ('sip', 'swp'):
        return jsonify({'success': False, 'error': 'mode must be sip or swp'}), 400

    by_sym = {e['symbol']: e for e in entries}

    # Resolve per-stock quantities: trust client allocations, else equal-₹ split
    prices = _sm_current_prices(list(by_sym.keys()))
    allocs = body.get('allocations') or []
    plan   = []  # (entry, qty, price)
    if allocs:
        for a in allocs:
            sym = a.get('symbol'); q = int(a.get('qty', 0) or 0)
            e   = by_sym.get(sym)
            if not e or q <= 0:
                continue
            plan.append((e, q, prices.get(sym, e['entry_price'])))
    else:
        per_stock = amount / len(entries) if entries else 0
        for e in entries:
            p = prices.get(e['symbol'], e['entry_price'])
            q = int(per_stock / p) if p > 0 else 0
            if mode == 'swp':
                q = min(q, int(e['qty']))
            if q > 0:
                plan.append((e, q, p))

    if not plan:
        return jsonify({'success': False, 'error': 'Amount too small for any whole share'}), 400

    # Optional broker order placement
    side          = 'BUY' if mode == 'sip' else 'SELL'
    broker_inst   = body.get('broker_instance')
    broker_summary = None
    fill_prices   = {}   # symbol -> avg fill price (broker) when available
    if broker_inst:
        try:
            import time as _t
            username    = session.get('username', 'Mine')
            broker_inst = int(broker_inst)
            broker_type = (body.get('broker_type') or '').strip().lower()
            broker_name = body.get('broker_name') or broker_type.title()
            svc = _sm_build_order_service(username, broker_inst, broker_type)
            if svc is None:
                broker_summary = {'placed': 0, 'failed': len(plan),
                                  'error': f'{broker_name}: not connected'}
            else:
                placed, failed, oids, first_err = 0, 0, [], None
                for e, q, _p in plan:
                    oid, err = _sm_place_equity_order(broker_type, svc, e['symbol'], q, side, price=_p)
                    if oid:
                        placed += 1; oids.append((e['symbol'], oid))
                    else:
                        failed += 1
                        first_err = first_err or err
                        logger.error(f'[sm-{mode}] {broker_name} {side} {q} {e["symbol"]} failed: {err}')
                if oids:
                    _t.sleep(2.0)
                    for sym, oid in oids:
                        avg = _sm_avg_fill_price(broker_type, svc, oid)
                        if avg and avg > 0:
                            fill_prices[sym] = round(avg, 2)
                broker_summary = {'placed': placed, 'failed': failed, 'broker': broker_name,
                                  'error': None if placed else
                                  (f'{broker_name}: {first_err}' if first_err else f'{broker_name}: no orders placed')}
        except Exception as ex:
            logger.error(f'[sm-{mode}] broker placement failed: {ex}', exc_info=True)
            broker_summary = {'placed': 0, 'failed': len(plan), 'error': str(ex)}

    # Apply to holdings
    deployed = 0.0
    for e, q, p in plan:
        fill = fill_prices.get(e['symbol'], p)
        if mode == 'sip':
            old_qty, old_entry = int(e['qty']), float(e['entry_price'])
            new_qty = old_qty + q
            e['entry_price'] = round((old_qty * old_entry + q * fill) / new_qty, 2)
            e['qty']         = new_qty
            deployed += q * fill
        else:  # swp
            sell_q = min(q, int(e['qty']))
            e['qty'] = int(e['qty']) - sell_q
            deployed += sell_q * fill
            if sell_q > 0:
                _sm_record_exit(config, e['symbol'], sell_q, float(e['entry_price']),
                                e.get('entry_date', ''), fill, body.get('date'))
    # Drop fully sold-out holdings
    config['live_entries'] = [e for e in entries if int(e['qty']) > 0]

    log = config.setdefault('monthly_investment_log', [])
    log.append({
        'date':   body.get('date', datetime.today().strftime('%Y-%m-%d')),
        'amount': round(deployed if mode == 'sip' else -deployed, 2),
        'note':   body.get('note', ''),
        'type':   mode,
    })

    # Remember the broker used as this config's default (so every order popup
    # preselects the group's broker next time).
    if broker_inst:
        config['broker'] = {'instance':    broker_inst,
                            'broker_type': body.get('broker_type'),
                            'broker_name': body.get('broker_name')}

    _sm_save_live_configs(configs)
    return jsonify({'success': True, 'mode': mode,
                    'deployed': round(deployed, 2),
                    'holdings': len(config['live_entries']),
                    'broker_summary': broker_summary})


@api_bp.route('/algo/swing-momentum/configs', methods=['POST'])
def sm_live_configs_add():
    body = request.get_json() or {}
    configs  = _sm_load_live_configs()
    today    = datetime.today().date()
    index    = body.get('index', 'NIFTY 500')
    top_n    = int(body.get('top_n', 10))
    investment = float(body.get('investment', 100000))

    # Immediately go live using today's momentum rankings (no historical simulation)
    ranked, _ = _sm_compute_today_rankings(index)
    cash         = investment
    live_entries = []
    for s in (ranked or []):
        if len(live_entries) >= top_n:
            break
        price = s['price']
        if not price or price <= 0:
            continue
        remaining_slots = top_n - len(live_entries)
        per_stock = cash / remaining_slots
        qty = int(per_stock / price)
        if qty <= 0:
            continue
        cash -= qty * price
        live_entries.append({
            'symbol':      s['symbol'],
            'entry_price': price,
            'qty':         qty,
            'entry_date':  str(today),
        })

    # Go Live only records the config to the Live Algo JSON. Real broker orders are
    # placed later, explicitly, from the Live Algo screen's "Place Orders" button
    # (POST .../place-orders). The chosen broker is stored here as the default.
    broker_summary = None
    broker_instance = body.get('broker_instance')
    if broker_instance:
        try:
            broker_instance = int(broker_instance)
        except (TypeError, ValueError):
            broker_instance = None

    config = {
        'id':                     str(uuid.uuid4())[:8],
        'label':                  body.get('label', ''),
        'index':                  index,
        'top_n':                  top_n,
        'exit_rank':              int(body.get('exit_rank', 50)),
        'rebalance_freq':         body.get('rebalance_freq', 'monthly'),
        'investment':             investment,
        'monthly_add':            float(body.get('monthly_add', 0)),
        'monthly_add_type':       body.get('monthly_add_type', 'static'),
        'monthly_investment_log': [],
        'start_date':             body.get('start_date', '2025-01-01'),
        'status':                 'watching',
        'added_at':               datetime.now().strftime('%Y-%m-%d %H:%M'),
        'live_since':             str(today),
        'live_entries':           live_entries,
    }
    if broker_instance:
        config['broker'] = {
            'instance':    broker_instance,
            'broker_type': body.get('broker_type'),
            'broker_name': body.get('broker_name'),
        }
    configs.append(config)
    _sm_save_live_configs(configs)
    return jsonify({'success': True, 'config': config, 'broker_summary': broker_summary})


@api_bp.route('/algo/swing-momentum/configs/<config_id>/place-orders', methods=['POST'])
def sm_live_place_orders(config_id):
    """Place real CNC MARKET BUY orders on the chosen broker for this config's
    current JSON holdings.

    - Order quantity matches the stored qty exactly (JSON is the source of truth).
    - NSE equity lot size is 1, so the JSON qty is a valid order quantity as-is.
    - On fill, each holding's entry_price is updated to the average fill price and
      an 'order' record (order_id, status, avg_price) is attached, then saved.
    - Idempotent: holdings that already carry a broker order_id are skipped unless
      the request passes force=true.

    Body: {broker_instance, broker_type, broker_name, force?}
    """
    body    = request.get_json() or {}
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    entries = config.get('live_entries') or []
    if not entries:
        return jsonify({'success': False, 'error': 'No holdings to place'}), 400

    broker_instance = body.get('broker_instance')
    if not broker_instance:
        return jsonify({'success': False, 'error': 'Select a broker to place orders'}), 400

    force = bool(body.get('force'))
    # Only place holdings that have a real quantity and are not already ordered.
    pending = [e for e in entries
               if int(e.get('qty', 0) or 0) > 0
               and (force or not (e.get('order') or {}).get('order_id'))]
    if not pending:
        return jsonify({'success': False,
                        'error': 'All holdings already have broker orders. Use force to re-place.'}), 400

    try:
        username        = session.get('username', 'Mine')
        broker_instance = int(broker_instance)
        broker_type     = (body.get('broker_type') or '').strip().lower()
        broker_name     = body.get('broker_name') or broker_type.title()
        # Mutates the pending entries (references into config['live_entries']) in
        # place: sets entry_price to the avg fill and attaches the order record.
        summary = _sm_place_portfolio_orders(
            username, broker_instance, broker_type, broker_name, pending)
    except Exception as e:
        logger.error(f'[sm-place] placement failed: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

    # Remember the broker as this config's default for next time.
    config['broker'] = {'instance':    broker_instance,
                        'broker_type': body.get('broker_type'),
                        'broker_name': body.get('broker_name')}
    _sm_save_live_configs(configs)

    return jsonify({'success': True, 'broker_summary': summary,
                    'placed': summary.get('placed', 0),
                    'failed': summary.get('failed', 0),
                    'attempted': len(pending)})


def _sm_compute_rebalance(config: dict):
    """Work out the rebalance plan for a live config from today's rankings.

    Returns (sells, buys, error) where:
      sells = [{symbol, qty, price, value, current_rank}]  (rank fell past exit_rank)
      buys  = [{symbol, price, qty, value, current_rank}]  (top replacements, sized
              by the proceeds split equally across the freed slots)
    """
    entries   = config.get('live_entries') or []
    exit_rank = config['exit_rank']

    ranked = _sm_rankings_cached(config['index'])
    if not ranked:
        return None, None, 'Rankings unavailable — try again in a moment'
    rank_by_sym = {s['symbol']: s for s in ranked}
    held        = {e['symbol'] for e in entries}

    sell_entries = [e for e in entries
                    if rank_by_sym.get(e['symbol'], {}).get('rank', 9999) > exit_rank]
    if not sell_entries:
        return [], [], None

    buy_cands = [s for s in ranked if s['symbol'] not in held][:len(sell_entries)]

    prices = _sm_current_prices([e['symbol'] for e in sell_entries]
                                + [b['symbol'] for b in buy_cands])

    sells, proceeds = [], 0.0
    for e in sell_entries:
        px  = prices.get(e['symbol']) or e['entry_price']
        val = int(e['qty']) * px
        proceeds += val
        sells.append({'symbol': e['symbol'], 'qty': int(e['qty']), 'price': round(px, 2),
                      'value': round(val, 2),
                      'current_rank': rank_by_sym.get(e['symbol'], {}).get('rank')})

    budget = proceeds / len(buy_cands) if buy_cands else 0
    buys = []
    for b in buy_cands:
        px  = prices.get(b['symbol']) or b['price']
        qty = int(budget / px) if px else 0
        if qty <= 0:
            continue
        buys.append({'symbol': b['symbol'], 'price': round(px, 2), 'qty': qty,
                     'value': round(qty * px, 2), 'current_rank': b['rank']})
    return sells, buys, None


@api_bp.route('/algo/swing-momentum/configs/<config_id>/rebalance/preview', methods=['GET'])
def sm_live_rebalance_preview(config_id):
    """Preview the rebalance: which stocks are sold, which replace them, and the
    quantities sized from the sale proceeds."""
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    sells, buys, err = _sm_compute_rebalance(config)
    if err:
        return jsonify({'success': False, 'error': err}), 503
    return jsonify({'success': True, 'sells': sells, 'buys': buys,
                    'proceeds': round(sum(s['value'] for s in sells), 2),
                    'deploy':   round(sum(b['value'] for b in buys), 2),
                    'broker':   config.get('broker')})


@api_bp.route('/algo/swing-momentum/configs/<config_id>/rebalance', methods=['POST'])
def sm_live_rebalance(config_id):
    """Execute the rebalance: SELL holdings that dropped past exit_rank, then BUY
    the top-ranked replacements sized by the proceeds. Places real orders on the
    config's broker and rewrites live_entries in the JSON."""
    import time as _t
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    sells, buys, err = _sm_compute_rebalance(config)
    if err:
        return jsonify({'success': False, 'error': err}), 503
    if not sells:
        return jsonify({'success': False, 'error': 'No holdings past exit rank — nothing to rebalance'}), 400

    broker = config.get('broker') or {}
    if not broker.get('instance'):
        return jsonify({'success': False, 'error': 'No broker set for this group. Assign one at Go Live / Place Orders.'}), 400
    username    = session.get('username', 'Mine')
    instance    = int(broker['instance'])
    broker_type = (broker.get('broker_type') or '').strip().lower()
    broker_name = broker.get('broker_name') or broker_type.title()
    svc = _sm_build_order_service(username, instance, broker_type)
    if svc is None:
        return jsonify({'success': False, 'error': f'{broker_name}: not connected'}), 400

    entries   = config.get('live_entries') or []
    by_sym    = {e['symbol']: e for e in entries}
    sold_syms, new_entries = [], []
    summary   = {'sold': 0, 'bought': 0, 'failed': 0, 'errors': []}

    def _order_rec(oid, status='placed', avg=None, error=None):
        rec = {'broker': broker_name, 'broker_type': broker_type, 'instance': instance,
               'order_id': str(oid) if oid else None, 'status': status}
        if avg is not None:   rec['avg_price'] = avg
        if error is not None: rec['error'] = error
        return rec

    # 1) SELL the exiting holdings
    for s in sells:
        oid, e = _sm_place_equity_order(broker_type, svc, s['symbol'], s['qty'], 'SELL', price=s['price'])
        if oid:
            summary['sold'] += 1
            sold_syms.append(s['symbol'])
            orig = by_sym.get(s['symbol'], {})
            _sm_record_exit(config, s['symbol'], s['qty'],
                            float(orig.get('entry_price', s['price'])),
                            orig.get('entry_date', ''), s['price'])
        else:
            summary['failed'] += 1
            summary['errors'].append(f"SELL {s['symbol']}: {e}")

    # 2) BUY the replacements sized by the proceeds
    buy_oids = []
    for b in buys:
        oid, e = _sm_place_equity_order(broker_type, svc, b['symbol'], b['qty'], 'BUY', price=b['price'])
        if oid:
            summary['bought'] += 1
            entry = {'symbol': b['symbol'], 'entry_price': b['price'], 'qty': b['qty'],
                     'entry_date': datetime.today().strftime('%Y-%m-%d'),
                     'order': _order_rec(oid)}
            new_entries.append(entry)
            buy_oids.append((entry, oid))
        else:
            summary['failed'] += 1
            summary['errors'].append(f"BUY {b['symbol']}: {e}")

    # Read back BUY fill prices for accurate entry price
    if buy_oids:
        _t.sleep(2.0)
        for entry, oid in buy_oids:
            avg = _sm_avg_fill_price(broker_type, svc, oid)
            if avg and avg > 0:
                entry['entry_price']       = round(avg, 2)
                entry['order']['avg_price'] = round(avg, 2)
                entry['order']['status']    = 'filled'

    # 3) Rewrite holdings: drop the sold, add the new
    config['live_entries'] = [e for e in entries if e['symbol'] not in sold_syms] + new_entries
    config.setdefault('monthly_investment_log', []).append({
        'date':   datetime.today().strftime('%Y-%m-%d'),
        'amount': 0.0,
        'note':   f"Rebalance: sold {summary['sold']}, bought {summary['bought']}",
        'type':   'rebalance',
    })
    _sm_save_live_configs(configs)

    return jsonify({'success': True, 'summary': summary,
                    'holdings': len(config['live_entries'])})


@api_bp.route('/algo/swing-momentum/configs/<config_id>/holdings/edit', methods=['POST'])
def sm_live_edit_holding(config_id):
    """Edit a single holding's fields in the JSON. Body: {symbol, qty?, entry_date?,
    entry_price?, invested?}. `invested` sets entry_price = invested / qty (qty kept).
    A qty of 0 removes the holding. Only the fields present in the body are changed."""
    body    = request.get_json() or {}
    symbol  = body.get('symbol')
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    entries = config.get('live_entries') or []
    entry   = next((e for e in entries if e['symbol'] == symbol), None)
    if not entry:
        return jsonify({'success': False, 'error': 'Holding not found'}), 404

    try:
        if body.get('qty') not in (None, ''):
            entry['qty'] = int(float(body['qty']))
        if body.get('entry_date'):
            entry['entry_date'] = str(body['entry_date'])
        # invested wins over entry_price when both are sent (derive avg cost)
        if body.get('invested') not in (None, ''):
            q = int(entry.get('qty', 0)) or 1
            entry['entry_price'] = round(float(body['invested']) / q, 2)
        elif body.get('entry_price') not in (None, ''):
            entry['entry_price'] = round(float(body['entry_price']), 2)
    except (TypeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Invalid value: {e}'}), 400

    # Remove the holding entirely if quantity was set to zero
    if int(entry.get('qty', 0)) <= 0:
        config['live_entries'] = [e for e in entries if e['symbol'] != symbol]

    _sm_save_live_configs(configs)
    return jsonify({'success': True, 'entry': entry})


@api_bp.route('/algo/swing-momentum/configs/<config_id>/exit-history', methods=['GET'])
def sm_live_exit_history(config_id):
    """Return the group's realized exit history (sold stocks with entry/exit P&L)."""
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    hist = config.get('exit_history', [])
    return jsonify({'success': True, 'exits': hist,
                    'realized_pnl': round(sum(h.get('pnl', 0) for h in hist), 2)})


@api_bp.route('/algo/swing-momentum/configs/<config_id>', methods=['DELETE'])
def sm_live_configs_delete(config_id):
    configs = _sm_load_live_configs()
    configs = [c for c in configs if c['id'] != config_id]
    _sm_save_live_configs(configs)
    return jsonify({'success': True})


@api_bp.route('/algo/swing-momentum/configs/<config_id>', methods=['PATCH'])
def sm_live_configs_update(config_id):
    """Update editable fields: investment, monthly_add, monthly_add_type."""
    body    = request.get_json() or {}
    configs = _sm_load_live_configs()
    for c in configs:
        if c['id'] == config_id:
            if 'investment'       in body: c['investment']       = float(body['investment'])
            if 'monthly_add'      in body: c['monthly_add']      = float(body['monthly_add'])
            if 'monthly_add_type' in body: c['monthly_add_type'] = body['monthly_add_type']
            break
    _sm_save_live_configs(configs)
    return jsonify({'success': True})


@api_bp.route('/algo/swing-momentum/configs/<config_id>/monthly-invest', methods=['POST'])
def sm_live_add_monthly_invest(config_id):
    """Record a manual monthly investment entry in the log."""
    body    = request.get_json() or {}
    configs = _sm_load_live_configs()
    for c in configs:
        if c['id'] == config_id:
            log = c.setdefault('monthly_investment_log', [])
            log.append({
                'date':   body.get('date', datetime.today().strftime('%Y-%m-%d')),
                'amount': float(body.get('amount', 0)),
                'note':   body.get('note', ''),
            })
            break
    _sm_save_live_configs(configs)
    return jsonify({'success': True})


@api_bp.route('/algo/swing-momentum/configs/<config_id>/toggle', methods=['POST'])
def sm_live_configs_toggle(config_id):
    configs = _sm_load_live_configs()
    for c in configs:
        if c['id'] == config_id:
            c['status'] = 'paused' if c['status'] == 'watching' else 'watching'
            break
    _sm_save_live_configs(configs)
    return jsonify({'success': True})


@api_bp.route('/algo/swing-momentum/configs/<config_id>/go-live', methods=['POST'])
def sm_live_go_live(config_id):
    """Snapshot TODAY's top-N momentum stocks as live entry prices (no historical simulation)."""
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    try:
        today   = datetime.today().date()
        top_n   = config['top_n']

        ranked, _ = _sm_compute_today_rankings(config['index'])
        if not ranked:
            return jsonify({'success': False, 'error': 'No price data returned for index'}), 502

        if not ranked:
            return jsonify({'success': False, 'error': 'No stocks ranked — check index name'}), 400

        cash         = config['investment']
        live_entries = []
        for s in ranked:
            if len(live_entries) >= top_n:
                break
            price = s['price']
            if not price or price <= 0:
                continue
            remaining_slots = top_n - len(live_entries)
            per_stock = cash / remaining_slots
            qty = int(per_stock / price)
            if qty <= 0:
                continue
            cash -= qty * price
            live_entries.append({
                'symbol':      s['symbol'],
                'entry_price': price,
                'qty':         qty,
                'entry_date':  str(today),
            })

        for c in configs:
            if c['id'] == config_id:
                c['live_since']   = str(today)
                c['live_entries'] = live_entries
                break
        _sm_save_live_configs(configs)

        return jsonify({'success': True, 'live_since': str(today), 'live_entries': live_entries})
    except Exception as e:
        logger.exception(f'SM go-live error for config {config_id}: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/swing-momentum/configs/<config_id>/reset-live', methods=['POST'])
def sm_live_reset(config_id):
    """Clear live entry prices so the config returns to strategy-view mode."""
    configs = _sm_load_live_configs()
    for c in configs:
        if c['id'] == config_id:
            c.pop('live_since',   None)
            c.pop('live_entries', None)
            break
    _sm_save_live_configs(configs)
    return jsonify({'success': True})


@api_bp.route('/algo/swing-momentum/signal/<config_id>', methods=['GET'])
def sm_live_signal(config_id):
    """Fast path — only downloads 5d prices for held stocks (~12). Returns in <2s."""
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    try:
        import yfinance as yf
        today        = datetime.today().date()
        live_entries = config.get('live_entries')

        if not live_entries:
            return jsonify({'success': False, 'error': 'No live entries — click Re-init first'}), 400

        # Next rebalance date (no download needed)
        if config['rebalance_freq'] == 'weekly':
            days_fwd = (7 - today.weekday()) % 7 or 7
            next_reb = str(today + timedelta(days=days_fwd))
        elif config['rebalance_freq'] == 'quarterly':
            qm = (((today.month - 1) // 3 + 1) * 3 % 12) + 1
            qy = today.year + (1 if qm <= today.month else 0)
            next_reb = str(today.replace(year=qy, month=qm, day=1))
        else:
            nm = today.month % 12 + 1
            ny = today.year + (1 if today.month == 12 else 0)
            next_reb = str(today.replace(year=ny, month=nm, day=1))

        # Fetch current prices — Fyers if connected (gives ltp + prev_close), else yfinance fallback
        # price_map: symbol → {'ltp': float, 'prev_close': float}
        price_map = {}
        provider  = get_data_provider()
        if provider is not None:
            fyers_syms = [f"NSE:{e['symbol']}-EQ" for e in live_entries]
            try:
                quotes = provider.quote(fyers_syms)
                for e in live_entries:
                    fsym = f"NSE:{e['symbol']}-EQ"
                    q    = quotes.get(fsym) or quotes.get(f"NSE:{e['symbol']}")
                    if q and q.get('last_price', 0) > 0:
                        ltp        = round(float(q['last_price']), 2)
                        prev_close = round(float(q.get('ohlc', {}).get('close') or ltp), 2)
                        price_map[e['symbol']] = {'ltp': ltp, 'prev_close': prev_close}
            except Exception:
                pass

        if not price_map:
            # yfinance fallback — prev_close = second-to-last close row
            import yfinance as yf
            yf_syms = [f"{e['symbol']}.NS" for e in live_entries]
            try:
                px     = yf.download(yf_syms, period='5d', interval='1d',
                                     auto_adjust=True, progress=False, threads=False)
                closes = (px['Close'] if len(yf_syms) > 1
                          else px[['Close']].rename(columns={'Close': yf_syms[0]}))
                for e in live_entries:
                    col = closes.get(f"{e['symbol']}.NS")
                    if col is not None:
                        clean = col.dropna()
                        if len(clean) >= 2:
                            price_map[e['symbol']] = {
                                'ltp':        round(float(clean.iloc[-1]), 2),
                                'prev_close': round(float(clean.iloc[-2]), 2),
                            }
                        elif len(clean) == 1:
                            ltp = round(float(clean.iloc[-1]), 2)
                            price_map[e['symbol']] = {'ltp': ltp, 'prev_close': ltp}
            except Exception:
                pass

        live_holdings   = []
        total_invested  = 0.0
        total_curr_val  = 0.0
        total_today_pnl = 0.0

        for e in live_entries:
            px_data    = price_map.get(e['symbol'], {})
            curr_price = px_data.get('ltp',        e['entry_price'])
            prev_close = px_data.get('prev_close', curr_price)

            qty        = e['qty']
            entry      = e['entry_price']
            buy_val    = round(entry * qty, 2)
            curr_val   = round(curr_price * qty, 2)
            pnl_abs    = round(curr_val - buy_val, 2)
            pnl_pct    = round((curr_price - entry) / entry * 100, 2) if entry else 0
            today_abs  = round((curr_price - prev_close) * qty, 2)
            today_pct  = round((curr_price - prev_close) / prev_close * 100, 2) if prev_close else 0
            total_invested  += buy_val
            total_curr_val  += curr_val
            total_today_pnl += today_abs
            live_holdings.append({
                'symbol':         e['symbol'],
                'qty':            qty,
                'entry_date':     e['entry_date'],
                'entry_price':    entry,
                'current_price':  curr_price,
                'prev_close':     prev_close,
                'buy_value':      buy_val,
                'current_value':  curr_val,
                'pnl_abs':        pnl_abs,
                'pnl_pct':        pnl_pct,
                'today_abs':      today_abs,
                'today_pct':      today_pct,
                'current_rank':   None,
                'momentum_score': None,
                'ordered':        bool((e.get('order') or {}).get('order_id')),
                'order_status':   (e.get('order') or {}).get('status'),
            })

        unrealised_pnl  = round(total_curr_val - total_invested, 2)
        unrealised_pct  = round(unrealised_pnl / total_invested * 100, 2) if total_invested else 0
        total_today_pnl = round(total_today_pnl, 2)
        total_today_pct = round(total_today_pnl / total_invested * 100, 2) if total_invested else 0

        # Simple annualized CAGR since go-live (cost basis = current invested incl. SIP/SWP)
        cagr_pct = 0.0
        try:
            live_since = config.get('live_since')
            if live_since and total_invested > 0 and total_curr_val > 0:
                d0    = datetime.strptime(live_since, '%Y-%m-%d').date()
                years = max((today - d0).days, 1) / 365.25
                if years >= 0.02:  # ~1 week min to avoid blow-up
                    cagr_pct = round(((total_curr_val / total_invested) ** (1 / years) - 1) * 100, 2)
                else:
                    cagr_pct = unrealised_pct  # too short to annualize meaningfully
        except Exception:
            cagr_pct = 0.0

        monthly_log = config.get('monthly_investment_log', [])
        total_sip   = sum(e.get('amount', 0) for e in monthly_log if e.get('amount', 0) > 0)
        total_swp   = sum(-e.get('amount', 0) for e in monthly_log if e.get('amount', 0) < 0)

        return jsonify({
            'success':                True,
            'live_mode':              True,
            'live_since':             config.get('live_since'),
            'live_holdings':          live_holdings,
            'holding_count':          len(live_holdings),
            'total_invested':         round(total_invested, 2),
            'current_port_val':       round(total_curr_val, 2),
            'unrealised_pnl':         unrealised_pnl,
            'unrealised_pct':         unrealised_pct,
            'today_pnl':              total_today_pnl,
            'today_pct':              total_today_pct,
            'sell_preview':           [],
            'buy_preview':            [],
            'next_rebalance':         next_reb,
            'last_rebalance':         config.get('live_since'),
            'rebalance_needed':       False,
            'configured_investment':  config.get('investment', 100000),
            'monthly_add':            config.get('monthly_add', 0),
            'monthly_add_type':       config.get('monthly_add_type', 'static'),
            'monthly_investment_log': monthly_log,
            'total_sip_added':        round(total_sip, 2),
            'total_swp_taken':        round(total_swp, 2),
            'cagr_pct':               cagr_pct,
            'broker':                 config.get('broker'),
            'rankings_pending':       True,
        })
    except Exception as e:
        logger.exception(f'SM live signal error for config {config_id}: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/algo/swing-momentum/signal/<config_id>/rankings', methods=['GET'])
def sm_live_rankings(config_id):
    """Slow path — downloads 310d × 500 stocks to compute momentum ranks.
    Uses a 15-min cache so repeated calls are instant after the first load."""
    configs = _sm_load_live_configs()
    config  = next((c for c in configs if c['id'] == config_id), None)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    try:
        live_entries = config.get('live_entries', [])
        top_n        = config['top_n']
        exit_rank    = config['exit_rank']

        ranked      = _sm_rankings_cached(config['index'])
        rank_by_sym = {s['symbol']: s for s in (ranked or [])}
        held_syms   = {e['symbol'] for e in live_entries}

        # Rank + momentum score per holding
        holding_ranks = {
            e['symbol']: {
                'current_rank':   rank_by_sym.get(e['symbol'], {}).get('rank'),
                'momentum_score': round(rank_by_sym.get(e['symbol'], {}).get('score', 0), 2)
                                  if e['symbol'] in rank_by_sym else None,
            }
            for e in live_entries
        }

        sell_preview = [
            {
                'symbol':       e['symbol'],
                'current_rank': rank_by_sym.get(e['symbol'], {}).get('rank', '?'),
                'score':        round(rank_by_sym.get(e['symbol'], {}).get('score', 0), 2),
                'qty':          e['qty'],
            }
            for e in live_entries
            if rank_by_sym.get(e['symbol'], {}).get('rank', 9999) > exit_rank
        ]
        buy_preview = [
            {
                'symbol':       s['symbol'],
                'current_rank': s['rank'],
                'score':        round(s['score'], 2),
                'price':        s['price'],
            }
            for s in (ranked or [])[:top_n + 5]
            if s['symbol'] not in held_syms
        ][:top_n]

        return jsonify({
            'success':          True,
            'holding_ranks':    holding_ranks,
            'sell_preview':     sell_preview,
            'buy_preview':      buy_preview,
            'rebalance_needed': bool(sell_preview or buy_preview),
        })
    except Exception as e:
        logger.exception(f'SM rankings error for config {config_id}: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


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
