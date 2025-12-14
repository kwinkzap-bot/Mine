import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, jsonify, session, Response 
from dotenv import load_dotenv
from kiteconnect import KiteConnect
# NOTE: The strategy_backtest.py file must be modified locally to use the correct import:
# from kiteconnect.exceptions import KiteException
from strategy_backtest import OptionsStrategy
from service.options_chart_service import OptionsChartService
from service.cpr_filter_service import CPRFilterService
import logging
from typing import Dict, Any, Union # Added Union for more accurate return type in get_kite

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

# Adjusted return type to include exception case possibility
def get_kite() -> KiteConnect | None:
    """
    Retrieves a KiteConnect instance.
    """
    api_key: str | None = os.getenv("API_KEY")
    if not api_key:
        logging.error("API_KEY environment variable not set.")
        return None

    try:
        if 'access_token' in session and session['access_token']:
            # Initialize KiteConnect with access token for authenticated session
            kite_instance: KiteConnect = KiteConnect(api_key=api_key)
            kite_instance.set_access_token(session['access_token'])
            return kite_instance
        else:
            # Initialize KiteConnect with just API key for unauthenticated session
            return KiteConnect(api_key=api_key)
    except Exception as e:
        logging.error(f"Error initializing KiteConnect: {e}")
        return None

@app.route('/')
def index() -> str:
    """
    Renders the main index page of the application.
    """
    return render_template('index.html')

@app.route('/strategy')
def strategy() -> str:
    """
    Renders the strategy page.
    """
    return render_template('strategy.html')

@app.route('/cpr-filter')
def cpr_filter() -> str:
    """
    Renders the CPR filter page.
    """
    return render_template('cpr_filter.html')

@app.route('/historical')
def historical() -> str:
    """
    Renders the historical data page.
    """
    return render_template('historical.html')

@app.route('/multi-cpr-backtest')
def multi_cpr_backtest() -> str:
    """
    Renders the multi-CPR backtest page.
    """
    return render_template('multi_cpr_backtest.html')

@app.route('/options-chart')
def options_chart() -> str:
    """
    Renders the options chart page, fetching the current NIFTY value.
    """
    nifty_val: float = 0.0
    try:
        current_kite: KiteConnect | None = get_kite()
        if current_kite:
            instrument: str = 'NSE:NIFTY 50'
            ltp_data: dict = current_kite.ltp([instrument]) # ltp expects a list of instruments
            nifty_val = ltp_data.get(instrument, {}).get('last_price', 0.0)
    except Exception as e:
        logging.error(f"Could not fetch NIFTY value: {e}")

    return render_template('options_chart.html', nifty_val=nifty_val)

@app.route('/login')
def login() -> Response | str:
    """
    Initiates the login process by redirecting the user to the Kite Connect login page.
    """
    try:
        api_key: str | None = os.getenv('API_KEY')
        if not api_key:
            return "Error: API_KEY environment variable not set. Please check your .env file"

        redirect_url: str = request.url_root + 'callback'
        login_url: str = f"https://kite.trade/connect/login?api_key={api_key}&redirect_uri={redirect_url}"
        return redirect(login_url)
    except Exception as e:
        return f"Error: {e}. Please check your API_KEY in .env file"

@app.route('/callback')
def callback() -> Response:
    """
    Handles the callback from Kite Connect after a user logs in.
    """
    request_token: str | None = request.args.get('request_token')
    
    api_key: str | None = os.getenv("API_KEY")
    api_secret: str | None = os.getenv("API_SECRET")

    if not api_key or not api_secret:
        return jsonify({'status': 'error', 'message': 'API_KEY or API_SECRET environment variables not set.'})

    if request_token:
        try:
            current_kite: KiteConnect = KiteConnect(api_key=api_key)
            data: dict = current_kite.generate_session(request_token, api_secret=api_secret)
            access_token: str | None = data.get("access_token") if isinstance(data, dict) else None
            if not access_token:
                return jsonify({'status': 'error', 'message': 'Failed to get access token from session generation'})
            
            session['access_token'] = access_token
            return redirect('/')
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'Session generation failed: {str(e)}'
            })
    return jsonify({'status': 'error', 'message': 'No request token received'})

@app.route('/api/strategy-backtest', methods=['POST'])
def run_strategy_backtest() -> Response:
    """
    Handles the API endpoint for running a strategy backtest.
    """
    try:
        current_kite: KiteConnect | None = get_kite()
        if not current_kite:
            return jsonify({
                'status': 'error',
                'message': 'Failed to initialize KiteConnect. Check API keys or login status.'
            })
        
        # Use request.get_json() for safer parsing and better type inference
        # This fixes the original type error by avoiding raw request.data/request.json ambiguities.
        data: Dict[str, Any] = request.get_json(silent=True) or {} # <-- FIX APPLIED
        
        # Ensure data is a dictionary before proceeding
        if not isinstance(data, dict):
             return jsonify({'status': 'error', 'message': 'Invalid request body format (must be JSON)'})
             
        symbol: str = data.get('symbol', 'NIFTY')
        start_date_str: str | None = data.get('start_date')
        end_date_str: str | None = data.get('end_date')
        
        if not start_date_str or not end_date_str:
            return jsonify({'status': 'error', 'message': 'start_date and end_date are required'})
        
        start_date: datetime = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date: datetime = datetime.strptime(end_date_str, '%Y-%m-%d')
        
        strategy: OptionsStrategy = OptionsStrategy(kite_instance=current_kite)
        strategy.backtest_strategy(start_date, end_date, symbol)
        
        return jsonify({
            'status': 'success',
            'data': strategy.entry_exit_log
        })
    except Exception as e:
        # Check for specific authentication error that might happen during a backtest run
        if "token" in str(e).lower() or "auth" in str(e).lower():
            return jsonify({
                'status': 'error',
                'message': 'Authentication failed. Please check your login status.',
                'auth_error': True
            })
        logging.error(f"Error running strategy backtest: {e}")
        return jsonify({'status': 'error', 'message': str(e)})



@app.route('/api/options-strikes')
def get_options_strikes() -> Response:
    """
    API endpoint to get available options strikes for a given symbol.
    """
    symbol: str | None = request.args.get('symbol')
    if not symbol:
        return jsonify({'success': False, 'error': 'Symbol is required'})
    
    current_kite: KiteConnect | None = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'})
        
    chart_service: OptionsChartService = OptionsChartService(current_kite)
    result: Dict[str, Any] = chart_service.get_strikes_for_symbol(symbol) 
    
    # Check for success in the result dictionary before returning
    if 'strikes' in result:
        return jsonify({'success': True, 'strikes': result['strikes']})
    else:
        return jsonify({'success': False, 'error': 'Could not retrieve strike data.'})

@app.route('/api/options-chart-data', methods=['POST'])
def get_options_chart_data() -> Response:
    """
    API endpoint to get historical chart data for specified CE and PE option tokens or strikes.
    """
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': 'Invalid request body format (must be JSON)'})

    current_kite: KiteConnect | None = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed.'})

    chart_service: OptionsChartService = OptionsChartService(current_kite)

    ce_token: str | None = data.get('ce_token')
    pe_token: str | None = data.get('pe_token')
    timeframe: str = data.get('timeframe', '5minute')

    try:
        # If tokens are not provided, try to get them from strikes
        if not ce_token or not pe_token:
            symbol: str | None = data.get('symbol')
            ce_strike_str: str | None = data.get('ce_strike')
            pe_strike_str: str | None = data.get('pe_strike')
            
            if not symbol or not ce_strike_str or not pe_strike_str:
                return jsonify({'success': False, 'error': 'Symbol, CE strike, and PE strike are required when tokens are not provided'})

            ce_strike = float(ce_strike_str)
            pe_strike = float(pe_strike_str)

            ce_token, pe_token = chart_service.get_tokens_for_strikes(symbol, ce_strike, pe_strike)

            if not ce_token or not pe_token:
                return jsonify({'success': False, 'error': f'Could not find tokens for the given strikes: CE {ce_strike}, PE {pe_strike}'})

        # Fetch chart data using tokens
        ce_data, pe_data = chart_service.get_chart_data(ce_token, pe_token, timeframe)
        
        return jsonify({
            'success': True,
            'ce_data': ce_data,
            'pe_data': pe_data,
            'ce_token': ce_token,
            'pe_token': pe_token
        })
    except Exception as e:
        logging.error(f"Error fetching chart data: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cpr-filter', methods=['GET'])
def get_cpr_filter_results():
    """
    API endpoint to get filtered stocks based on CPR strategy.
    """
    current_kite: KiteConnect | None = get_kite()
    if not current_kite:
        return jsonify({'success': False, 'error': 'KiteConnect initialization failed. Please login.'})

    try:
        cpr_service = CPRFilterService(kite_instance=current_kite)
        results = cpr_service.filter_cpr_stocks()
        return jsonify({'success': True, 'data': results})
    except Exception as e:
        logging.error(f"Error in CPR filter: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_mode, port=5000)