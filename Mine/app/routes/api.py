"""API routes for trading data endpoints."""
from flask import Blueprint, request, jsonify, session, Response
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Any, Optional, Union

from app.utils.logger import logger
from app.extensions import csrf, limiter

api_bp = Blueprint('api', __name__)

# Type alias for API responses
# Flask's jsonify returns Response, optionally with status code tuple
EndpointResponse = Union[Response, tuple[Response, int]]


def get_kite() -> Optional[Any]:
    """Get authenticated KiteConnect instance from session or create new one."""
    try:
        # Don't store KiteConnect in session as it's not JSON serializable
        # Instead, create a new instance each time from credentials in session
        from kiteconnect import KiteConnect
        import os
        
        api_key = os.getenv('API_KEY')
        access_token = session.get('access_token')
        
        if not api_key or not access_token:
            return None
        
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        return kite
    except Exception as e:
        logger.error(f"Failed to initialize KiteConnect: {e}")
        return None


def check_auth() -> Optional[tuple]:
    """Check if user is authenticated. Returns error tuple if not."""
    if 'access_token' not in session or not session.get('access_token'):
        return jsonify({
            'success': False,
            'error': 'Authentication required. Please login first at /auth/login',
            'auth_error': True
        }), 401
    return None


def get_instrument_key(symbol: str) -> str:
    """Get the instrument key for a symbol."""
    symbol = symbol.upper()
    mapping = {
        'NIFTY': 'NSE:NIFTY 50',
        'BANKNIFTY': 'NSE:NIFTY BANK',
        'FINNIFTY': 'NSE:NIFTY FIN SERVICE'
    }
    return mapping.get(symbol, f'NSE:{symbol}')


@api_bp.route('/health', methods=['GET'])
def health() -> EndpointResponse:
    """Health check endpoint."""
    return jsonify({'status': 'healthy'}), 200


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
    """Get list of available symbols (F&O or indices)."""
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        from cpr_filter_service import CPRFilterService
        
        symbol_type = request.args.get('type', 'fno').lower()
        symbols = []
        
        if symbol_type == 'indices':
            symbols = sorted(['NIFTY', 'BANKNIFTY', 'FINNIFTY'])
        else:
            try:
                cpr_service = CPRFilterService(kite_instance=current_kite)
                fo_symbols = cpr_service.get_fo_stocks()
                indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
                symbols = sorted(list(set(fo_symbols + indices)))
            except Exception as e:
                logger.warning(f"Failed to fetch F&O stocks: {e}, returning indices only")
                symbols = sorted(['NIFTY', 'BANKNIFTY', 'FINNIFTY'])
        
        return jsonify({
            'success': True,
            'symbols': symbols,
            'type': symbol_type
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
        from cpr_filter_service import CPRFilterService
        
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
    Query params: symbol, price_source
    
    Performance optimizations:
    - Uses disk-cached NFO instruments (8-10s on first call, <500ms on cache hit)
    - Skips PDH/PDL and LTP on initial load (can be fetched separately)
    - Returns immediately with strikes for fast UI initialization
    """
    import time as time_module
    start_time = time_module.time()
    
    auth_error = check_auth()
    if auth_error:
        return auth_error
    
    symbol = request.args.get('symbol')
    price_source = request.args.get('price_source', 'previous_close')
    
    if not symbol:
        return jsonify({'success': False, 'error': 'Symbol is required'}), 400
    
    current_kite = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'}), 401
    
    try:
        from service.options_chart_service import OptionsChartService
        
        chart_service = OptionsChartService(current_kite)
        
        # Skip pricing for faster response - get strikes only
        result = chart_service.get_strikes_for_symbol(symbol, price_source, skip_pricing=False)
        
        if 'strikes' not in result:
            return jsonify({'success': False, 'error': 'Could not retrieve strike data.'}), 500
        
        strikes = result.get('strikes', [])
        default_ce_token = result.get('default_ce_token')
        default_pe_token = result.get('default_pe_token')
        base_price = result.get('base_price')
        
        # Extract strike prices
        default_ce_strike = None
        default_pe_strike = None
        
        for strike_info in strikes:
            if strike_info.get('ce_token') == default_ce_token:
                default_ce_strike = strike_info.get('strike')
            if strike_info.get('pe_token') == default_pe_token:
                default_pe_strike = strike_info.get('strike')
        
        # Fetch the requested price based on price_source parameter
        requested_price = base_price or 0.0
        requested_source_label = ' (Close)'
        
        try:
            instrument_key = get_instrument_key(symbol)
            
            if price_source == 'ltp':
                try:
                    ltp_data = current_kite.ltp([instrument_key])
                    requested_price = float(ltp_data.get(instrument_key, {}).get('last_price', base_price or 0.0))
                    requested_source_label = ' (LTP)'
                except Exception as e:
                    logger.warning(f"Error fetching LTP for {symbol}: {e}")
                    requested_price = base_price or 0.0
                    requested_source_label = ' (LTP)'
            else:  # previous_close
                try:
                    quote_data = current_kite.quote([instrument_key])
                    requested_price = float(quote_data.get(instrument_key, {}).get('ohlc', {}).get('close', base_price or 0.0))
                    requested_source_label = ' (Close)'
                except Exception as e:
                    logger.warning(f"Error fetching previous close for {symbol}: {e}")
                    requested_price = base_price or 0.0
                    requested_source_label = ' (Close)'
        except Exception as e:
            logger.warning(f"Error fetching price data for {symbol}: {e}")
            requested_price = base_price or 0.0
        
        total_time = time_module.time() - start_time
        logger.info(f"✓ options-init({symbol}) completed in {total_time:.2f}s")
        
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
        from service.options_chart_service import OptionsChartService
        
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
        if 'access_token' in error_str or 'unauthorized' in error_str:
            return jsonify({
                'success': False,
                'error': 'Authentication failed. Please login again.',
                'auth_error': True
            }), 401
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/options-pdh-pdl', methods=['POST'])
@csrf.exempt
@limiter.exempt  # Exempt from rate limiting - called frequently during chart updates
def get_options_pdh_pdl() -> EndpointResponse:
    """Get previous day high/low for CE/PE options."""
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
        from service.options_chart_service import OptionsChartService
        
        chart_service = OptionsChartService(current_kite)
        
        ce_token = data.get('ce_token')
        pe_token = data.get('pe_token')
        
        if not ce_token or not pe_token:
            symbol = data.get('symbol')
            ce_strike_str = data.get('ce_strike')
            pe_strike_str = data.get('pe_strike')
            
            if not symbol or not ce_strike_str or not pe_strike_str:
                return jsonify({
                    'success': False,
                    'error': 'Symbol, CE strike, and PE strike are required when tokens are not provided'
                }), 400
            
            ce_strike = float(ce_strike_str)
            pe_strike = float(pe_strike_str)
            
            ce_token, pe_token = chart_service.get_tokens_for_strikes(symbol, ce_strike, pe_strike)
            
            if not ce_token or not pe_token:
                return jsonify({
                    'success': False,
                    'error': f'Could not find tokens for the given strikes: CE {ce_strike}, PE {pe_strike}'
                }), 404
        
        pdh_pdl = chart_service.get_pdh_pdl(ce_token, pe_token)
        
        return jsonify({
            'success': True,
            'pdh_pdl': pdh_pdl,
            'ce_token': ce_token,
            'pe_token': pe_token
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
        
        from cpr_filter_service import CPRFilterService
        
        logger.info("Initializing CPRFilterService...")
        cpr_service = CPRFilterService(kite_instance=current_kite)
        
        logger.info("Starting CPR filter stocks processing...")
        results = cpr_service.filter_cpr_stocks()
        
        logger.info(f"CPR filter completed. Found {len(results)} stocks.")
        return jsonify({'success': True, 'data': results})
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
        from service.kite_service import KiteService
        
        symbol = request.args.get('symbol', '').upper()
        symbol_type = request.args.get('type', 'fno').lower()
        fno_type = request.args.get('fno_type', 'futures').lower()
        
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol parameter is required'}), 400
        
        instrument_token = None
        
        if symbol_type == 'indices' or symbol in ['NIFTY', 'BANKNIFTY', 'FINNIFTY']:
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
        
        from strategy_backtest import OptionsStrategy
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


@api_bp.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({'error': 'Endpoint not found'}), 404


@api_bp.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    logger.error(f"Server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500
