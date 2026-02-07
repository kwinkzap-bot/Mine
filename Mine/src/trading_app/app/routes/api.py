"""API routes for trading data endpoints."""
from flask import Blueprint, request, jsonify, session, Response
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Any, Optional, Union
import os

from trading_app.app.utils.logger import logger
from trading_app.app.extensions import csrf, limiter
from trading_app.app.utils.user_auth import require_user_auth


api_bp = Blueprint('api', __name__)

# Type alias for API responses
# Flask's jsonify returns Response, optionally with status code tuple
EndpointResponse = Union[Response, tuple[Response, int]]

# Apply user authentication to all API routes
@api_bp.before_request
def check_user_authentication():
    """Require user authentication for all API routes."""
    from trading_app.app.utils.user_auth import is_user_authenticated
    
    if not is_user_authenticated():
        return jsonify({
            'success': False,
            'error': 'User authentication required. Please login first.',
            'auth_required': True
        }), 401


def get_kite() -> Optional[Any]:
    """Get authenticated KiteConnect instance from session or create new one.
    
    Provides multiple fallback layers to handle socket pool flushes and 
    debug session resets that clear Flask session data:
    1. Session storage (immediate access)
    2. Environment variable (restored after socket pool flush)
    3. Persistent token cache file (survives process restart)
    """
    try:
        from kiteconnect import KiteConnect
        from trading_app.app.utils.token_manager import get_access_token
        import os
        
        api_key = os.getenv('API_KEY')
        
        if not api_key:
            logger.warning("API_KEY not found in environment")
            return None
        
        # Get access token with multiple fallback layers
        # 1. Check session first
        access_token = session.get('access_token')
        
        # 2. Check environment variable (restored after socket pool flush)
        if not access_token:
            access_token = os.getenv('ACCESS_TOKEN')
            if access_token:
                logger.info("Access token restored from environment variable (socket pool flush recovery)")
        
        # 3. Check persistent token cache
        if not access_token:
            access_token = get_access_token()
            if access_token:
                logger.info("Access token restored from persistent cache")
                # Update session and environment for future requests
                session['access_token'] = access_token
                os.environ['ACCESS_TOKEN'] = access_token
                session.permanent = True
        
        if not access_token:
            logger.warning("No access token available from any source")
            return None
        
        # Ensure session is in sync (for future requests)
        if 'access_token' not in session:
            session['access_token'] = access_token
            session.permanent = True
        
        # Initialize KiteConnect with the access token
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        
        logger.debug(f"KiteConnect initialized successfully (token: {access_token[:20]}...)")
        return kite
        
    except Exception as e:
        logger.error(f"Failed to initialize KiteConnect: {e}", exc_info=True)
        return None


def check_auth() -> Optional[tuple]:
    """Check if user is authenticated. Returns error tuple if not.
    
    Falls back to environment variable to handle socket pool flushes
    and debug session resets that clear Flask session data.
    """
    # Check session first, then fallback to environment variable
    # This provides continuity when socket pools are flushed during debugging
    access_token = session.get('access_token') or os.getenv('ACCESS_TOKEN')
    
    if not access_token:
        logger.warning("Authentication check failed: no access token available")
        return jsonify({
            'success': False,
            'error': 'Authentication required. Please login first at /auth/login',
            'auth_error': True
        }), 401
    
    # Ensure session has the token for consistency
    # This restores the session from environment after socket pool flush
    if 'access_token' not in session and access_token:
        session['access_token'] = access_token
        session.permanent = True
        logger.info("Session token restored from environment")
    
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
        'kite_available': get_kite() is not None,
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
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        instrument_key = get_instrument_key(symbol)
        ltp = None
        previous_close = None
        
        try:
            ltp_data = current_kite.ltp([instrument_key])
            ltp = float(ltp_data.get(instrument_key, {}).get('last_price', 0.0))
        except Exception as e:
            logger.warning(f"Error fetching LTP for {symbol}: {e}")
        
        try:
            quote_data = current_kite.quote([instrument_key])
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


@api_bp.route('/symbols', methods=['GET'])
def get_symbols() -> EndpointResponse:
    """Get list of available symbols."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        # Return only NIFTY 50
        symbols = ['NIFTY']
        
        return jsonify({
            'success': True,
            'symbols': symbols
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


@api_bp.route('/fo-stocks', methods=['GET'])
def get_fo_stocks() -> EndpointResponse:
    """Get list of F&O stocks available for trading."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        from trading_app.filters import CPRFilterService
        
        cpr_service = CPRFilterService(kite_instance=current_kite)
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
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        from trading_app.service.options_chart_service import OptionsChartService
        
        chart_service = OptionsChartService(current_kite)
        
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
    
    FAST PATH (Recommended):
        POST /api/options-chart-data
        {
            "ce_token": 12345678,
            "pe_token": 87654321,
            "timeframe": "5minute"
        }
        Response time: <2 seconds (direct token access, no lookups)
    
    LEGACY PATH (Still supported):
        POST /api/options-chart-data
        {
            "symbol": "NIFTY",
            "ce_strike": 25700,
            "pe_strike": 26000,
            "timeframe": "5minute"
        }
        Response time: 3-5 seconds (needs token lookup from NFO cache)
    """
    import time as time_module
    start_time = time_module.time()
    
    # NOTE: Authentication check removed to allow real-time chart updates
    # from /options-chart page without login. Chart data endpoint is public.
    # This matches the /options-chart page route which is also accessible
    # without authentication. For protected operations, use ACCESS_TOKEN
    # environment variable or handle auth in the service layer.
    
    data = request.get_json(silent=True) or {}
    
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': 'Invalid request body format (must be JSON)'}), 400
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        from trading_app.service.options_chart_service import OptionsChartService
        
        chart_service = OptionsChartService(current_kite)
        
        # Prefer tokens (FAST PATH - no lookups needed)
        ce_token = data.get('ce_token')
        pe_token = data.get('pe_token')
        timeframe = data.get('timeframe', '5minute')
        
        if not ce_token or not pe_token:
            # Fall back to symbol + strikes (LEGACY PATH)
            symbol = data.get('symbol')
            ce_strike_str = data.get('ce_strike')
            pe_strike_str = data.get('pe_strike')
            
            if not symbol or not ce_strike_str or not pe_strike_str:
                return jsonify({
                    'success': False,
                    'error': 'Provide either (ce_token + pe_token) OR (symbol + ce_strike + pe_strike)',
                    'fast_path': {
                        'description': 'For faster responses, use tokens instead of strikes',
                        'example': {
                            'ce_token': 12345678,
                            'pe_token': 87654321,
                            'timeframe': '5minute'
                        }
                    }
                }), 400
            
            ce_strike = float(ce_strike_str)
            pe_strike = float(pe_strike_str)
            
            lookup_start = time_module.time()
            ce_token, pe_token = chart_service.get_tokens_for_strikes(symbol, ce_strike, pe_strike)
            lookup_time = time_module.time() - lookup_start
            logger.info(f"Token lookup for {symbol} {ce_strike}C/{pe_strike}P took {lookup_time:.2f}s")
            
            if not ce_token or not pe_token:
                return jsonify({
                    'success': False,
                    'error': f'Could not find tokens for the given strikes: CE {ce_strike}, PE {pe_strike}'
                }), 404
        
        ce_data, pe_data = chart_service.get_chart_data(ce_token, pe_token, timeframe, use_cache=True)
        
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
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        from trading_app.service.options_chart_service import OptionsChartService
        
        chart_service = OptionsChartService(current_kite)
        
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
                # Get the underlying instrument token for the symbol
                symbol_map = {
                    'NIFTY': 256265985,      # NSE:NIFTY 50
                    'BANKNIFTY': 260105729,  # NSE:NIFTY BANK
                    'FINNIFTY': 257356037    # NSE:NIFTY FIN SERVICE
                }
                
                underlying_token = symbol_map.get(symbol.upper())
                if underlying_token:
                    # Fetch previous day's OHLC for the underlying (with optional target_date)
                    underlying_ohlc = chart_service._fetch_prev_day_ohlc(underlying_token, target_date)
                    underlying_pdh = underlying_ohlc.get('high')
                    underlying_pdl = underlying_ohlc.get('low')
                    date_label = f" for {target_date}" if target_date else ""
                    logger.info(f"Underlying {symbol}{date_label} PDH/PDL: {underlying_pdh}/{underlying_pdl}")
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


@api_bp.route('/cpr-filter', methods=['GET'])
@limiter.exempt
def get_cpr_filter_results() -> EndpointResponse:
    """Get stocks filtered by CPR strategy."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        # Verify kite has access token
        if not hasattr(current_kite, 'access_token') or not current_kite.access_token:
            logger.warning("CPR filter request: KiteConnect instance has no access token")
            return jsonify({
                'success': False,
                'error': 'No valid access token on KiteConnect instance. Please login again.',
                'auth_error': True
            }), 401
        
        from trading_app.filters.cpr_filter import CPRFilterService
        
        logger.info("Initializing CPRFilterService...")
        cpr_service = CPRFilterService(kite_instance=current_kite)
        
        logger.info("Starting CPR filter stocks processing...")
        results = cpr_service.filter_cpr_stocks()
        
        signals = results.get('signals', []) if isinstance(results, dict) else []
        weekly_cross = results.get('weekly_cross', {}) if isinstance(results, dict) else {}

        logger.info(
            "CPR filter completed. "
            f"Found {len(signals)} primary signals, "
            f"{len(weekly_cross.get('crossed_above', [])) if isinstance(weekly_cross, dict) else 0} crossed above weekly CPR, "
            f"{len(weekly_cross.get('crossed_below', [])) if isinstance(weekly_cross, dict) else 0} crossed below weekly CPR."
        )
        return jsonify({'success': True, 'data': signals, 'weekly_cross': weekly_cross})
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
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        from trading_app.service.kite_order_services import KiteService
        
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
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
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
        
        result = kite_service.place_option_order(
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            transaction_type=kite.TRANSACTION_TYPE_BUY
            # quantity: None uses dynamic lot size from Kite
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
    
    try:
        current_kite = get_kite()
        if not current_kite:
            return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
        
        from trading_app.service.multi_strike_service import MultiStrikeService
        
        multi_strike_service = MultiStrikeService(current_kite)
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
        
        # Get KiteConnect instance
        kite = get_kite()
        if not kite:
            return jsonify({
                'success': False,
                'error': 'Failed to initialize Kite connection'
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
        
        return jsonify({
            'success': payload.get('success', False),
            'data': payload,
            'timestamp': datetime.now().isoformat()
        }), 200 if payload.get('success') else 400
        
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


@api_bp.route('/intraday-920/place-order', methods=['POST'])
def place_intraday_920_order() -> EndpointResponse:
    """Place an option order for Intraday 9:20 strategy.
    
    Request JSON:
        symbol: str - Trading symbol (NIFTY, BANKNIFTY, etc.)
        strike: int - Strike price
        option_type: str - 'CE' or 'PE'
        action: str - 'BUY' or 'SELL'
        broker: str (optional) - 'kite', 'kotak_neo', 'dhan', 'fyers' (default: 'kite')
        quantity: int (optional) - Order quantity (default: lot size)
    
    Returns:
        JSON response with order details
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['symbol', 'strike', 'option_type', 'action']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        symbol = data['symbol']
        strike = int(data['strike'])
        option_type = data['option_type'].upper()
        action = data['action'].upper()
        broker = data.get('broker', 'kite').lower()
        quantity = data.get('quantity')
        
        # Validate option_type
        if option_type not in ['CE', 'PE']:
            return jsonify({
                'success': False,
                'error': 'Invalid option_type. Must be CE or PE'
            }), 400
        
        # Validate action
        if action not in ['BUY', 'SELL']:
            return jsonify({
                'success': False,
                'error': 'Invalid action. Must be BUY or SELL'
            }), 400
        
        # Validate broker
        valid_brokers = ['kite', 'kotak_neo', 'dhan', 'fyers']
        if broker not in valid_brokers:
            return jsonify({
                'success': False,
                'error': f'Invalid broker. Must be one of: {", ".join(valid_brokers)}'
            }), 400
        
        # Route to appropriate broker service
        if broker == 'kite':
            # Get Kite instance
            kite = get_kite()
            if not kite:
                return jsonify({
                    'success': False,
                    'error': 'Kite connection not available. Please login.'
                }), 401
            
            # Place order using KiteService
            from trading_app.service.kite_order_services import KiteService
            kite_service = KiteService(kite_instance=kite)
            
            # Map action to transaction type
            transaction_type = kite.TRANSACTION_TYPE_BUY if action == 'BUY' else kite.TRANSACTION_TYPE_SELL
            
            result = kite_service.place_option_order(
                symbol=symbol,
                strike=strike,
                option_type=option_type,
                transaction_type=transaction_type,
                quantity=quantity
            )
            
            if result['success']:
                logger.info(f"Kite - Order placed successfully: {action} {option_type} {symbol} {strike} - Order ID: {result.get('order_id')}")
                return jsonify(result), 200
            else:
                logger.warning(f"Kite - Order placement failed: {result.get('error')}")
                return jsonify(result), 400
        
        elif broker == 'kotak_neo':
            from trading_app.service.kotak_order_services import KotakOrderService
            
            # Get stored credentials from environment (set during login)
            trading_token = os.getenv('KOTAK_TRADING_TOKEN')
            trading_sid = os.getenv('KOTAK_TRADING_SID')
            base_url = os.getenv('KOTAK_BASE_URL')
            
            # Check if authenticated
            if not trading_token or not base_url:
                logger.warning("[Kotak Neo] Not authenticated - trading_token or base_url missing")
                return jsonify({
                    'success': False,
                    'error': 'Kotak Neo not authenticated. Please login via the Kotak Neo login page first (Settings > Brokers > Kotak Neo).'
                }), 401
            
            # Create service with authenticated credentials
            kotak_service = KotakOrderService(access_token=trading_token)
            kotak_service.trading_token = trading_token
            kotak_service.trading_sid = trading_sid
            kotak_service.base_url = base_url
            
            # Map action to transaction_type for Kotak
            transaction_type = 'BUY' if action == 'BUY' else 'SELL'
            
            result = kotak_service.place_option_order(
                symbol=symbol,
                strike=strike,
                option_type=option_type,
                transaction_type=transaction_type,
                quantity=quantity
            )
            
            if result['success']:
                logger.info(f"Kotak Neo - Order placed successfully: {action} {option_type} {symbol} {strike} - Order ID: {result.get('order_id')}")
                return jsonify(result), 200
            else:
                logger.warning(f"Kotak Neo - Order placement failed: {result.get('error')}")
                return jsonify(result), 400
        
        elif broker == 'dhan':
            from trading_app.service.dhan_order_services import DhanOrderService
            
            # Get stored Dhan credentials from environment
            dhan_access_token = os.getenv('DHAN_ACCESS_TOKEN')
            dhan_client_id = os.getenv('DHAN_CLIENT_ID')
            
            # Check if authenticated
            if not dhan_access_token or not dhan_client_id:
                logger.warning("[Dhan] Credentials not found - user must login first")
                return jsonify({
                    'success': False,
                    'error': 'Dhan not authenticated. Please login via Settings > Brokers > Dhan first.'
                }), 401
            
            try:
                # Create Dhan service with credentials
                dhan_service = DhanOrderService(
                    access_token=dhan_access_token,
                    client_id=dhan_client_id
                )
                
                # Build option symbol (e.g., NIFTY24JAN25000CE)
                from datetime import datetime
                now = datetime.now()
                year = now.strftime('%y')
                month = now.strftime('%b').upper()
                option_symbol = f"{symbol}{year}{month}{strike}{option_type}"
                
                logger.info(f"[Dhan] Placing order for {option_symbol}")
                
                lot_size = dhan_service.get_lot_size(symbol)
                order_quantity = (quantity or 1) * lot_size
                
                # Map action to transaction type
                transaction_type = 'BUY' if action == 'BUY' else 'SELL'
                
                # Place order using the option symbol
                result = dhan_service.place_order(
                    security_id=option_symbol,
                    transaction_type=transaction_type,
                    quantity=order_quantity,
                    order_type='MARKET',
                    product_type='INTRADAY'
                )
                
                if result['success']:
                    logger.info(f"Dhan - Order placed successfully: {action} {option_type} {symbol} {strike} - Order ID: {result.get('order_id')}")
                    return jsonify(result), 200
                else:
                    logger.warning(f"Dhan - Order placement failed: {result.get('error')}")
                    return jsonify(result), 400
                    
            except Exception as e:
                logger.error(f"[Dhan] Error during order placement: {e}", exc_info=True)
                return jsonify({
                    'success': False,
                    'error': f'Dhan order error: {str(e)}'
                }), 400
        
        elif broker == 'fyers':
            # Get stored Fyers credentials from environment
            fyers_app_id = os.getenv('FYERS_APP_ID')
            fyers_access_token = os.getenv('FYERS_ACCESS_TOKEN')
            
            # Check if authenticated
            if not fyers_app_id or not fyers_access_token:
                logger.warning("[Fyers] Credentials not found - user must login first")
                return jsonify({
                    'success': False,
                    'error': 'Fyers not authenticated. Please login via Settings > Brokers > Fyers first.'
                }), 401
            
            try:
                from trading_app.service.fyers_order_services import FyersOrderService
                
                # Create Fyers service with credentials
                fyers_service = FyersOrderService(app_id=fyers_app_id, access_token=fyers_access_token)
                
                # Get the Kite service to look up option symbol
                kite = get_kite()
                if not kite:
                    return jsonify({
                        'success': False,
                        'error': 'Kite connection required for Fyers symbol lookup'
                    }), 401
                
                from trading_app.service.kite_order_services import KiteService
                kite_service = KiteService(kite_instance=kite)
                
                # Get option symbol using Kite (trading symbol like NIFTY24JAN25000CE)
                option_symbol = kite_service.get_option_symbol(symbol, strike, option_type)
                
                if not option_symbol:
                    return jsonify({
                        'success': False,
                        'error': f'Could not find option symbol for {symbol} {strike} {option_type}'
                    }), 400
                
                # Convert to Fyers format (e.g., NSE:NIFTY24JAN25000CE)
                fyers_symbol = f"NSE:{option_symbol}"
                
                lot_size = fyers_service.get_lot_size(symbol)
                order_quantity = (quantity or 1) * lot_size
                
                # Map action to side (1=BUY, -1=SELL)
                side = 1 if action == 'BUY' else -1
                
                logger.info(f"[Fyers] Placing order for {fyers_symbol} with side={side}, quantity={order_quantity}")
                
                # Use place_order with the Fyers symbol format
                result = fyers_service.place_order(
                    symbol=fyers_symbol,
                    side=side,
                    quantity=order_quantity,
                    order_type=2,  # 2=MARKET
                    product_type='INTRADAY'
                )
                
                if result['success']:
                    logger.info(f"Fyers - Order placed successfully: {action} {option_type} {symbol} {strike} - Order ID: {result.get('order_id')}")
                    return jsonify(result), 200
                else:
                    logger.warning(f"Fyers - Order placement failed: {result.get('error')}")
                    return jsonify(result), 400
                    
            except Exception as e:
                logger.error(f"[Fyers] Error during order placement: {e}", exc_info=True)
                return jsonify({
                    'success': False,
                    'error': f'Fyers order error: {str(e)}'
                }), 400
        
        # Should never reach here if validation is correct
        return jsonify({
            'success': False,
            'error': 'Invalid broker selection'
        }), 400
    
    except Exception as e:
        logger.error(f"Error placing intraday 920 order: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Error placing order: {str(e)}'
        }), 500


@api_bp.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({'error': 'Endpoint not found'}), 404


@api_bp.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    logger.error(f"Server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500
