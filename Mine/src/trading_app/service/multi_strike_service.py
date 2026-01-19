"""Service for handling multi-strike options data."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional
from trading_app.service.kite_service import KiteService

logger = logging.getLogger(__name__)


class MultiStrikeService:
    """Service to fetch and process multi-strike options data."""
    
    def __init__(self, kite_instance):
        """Initialize with KiteConnect instance."""
        self.kite_service = KiteService(kite_instance)
    
    def get_multi_strike_data(
        self, 
        symbol: str,
        num_strikes: int = 3
    ) -> Dict[str, Any]:
        """
        Get multi-strike options data for a given symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'NIFTY')
            num_strikes: Number of strikes above and below (default 3)
        
        Returns:
            Dictionary with:
            - nifty_close: Previous day's close price
            - pdh: Previous day high
            - pdl: Previous day low
            - strikes_data: List of strike data with CE/PE prices
        """
        try:
            # Get the previous trading day's close price
            nifty_close = self._get_previous_trading_day_close(symbol)
            if not nifty_close:
                return {
                    'success': False,
                    'error': f'Could not fetch price for {symbol}'
                }
            
            # Get instruments for the symbol
            from trading_app.service.options_chart_service import OptionsChartService
            chart_service = OptionsChartService(self.kite_service.kite)
            
            result = chart_service.get_strikes_for_symbol(symbol, 'previous_close', skip_pricing=True)
            strikes = result.get('strikes', [])
            
            if not strikes:
                return {
                    'success': False,
                    'error': f'Could not fetch strikes for {symbol}'
                }
            
            # Find the strike closest to nifty_close
            closest_strike = min(strikes, key=lambda x: abs(x['strike'] - nifty_close))
            closest_strike_price = closest_strike['strike']
            
            # Get upper and lower strikes
            all_strike_prices = sorted([s['strike'] for s in strikes])
            closest_index = all_strike_prices.index(closest_strike_price)
            
            # Select num_strikes above and below
            lower_strikes = all_strike_prices[max(0, closest_index - num_strikes):closest_index]
            upper_strikes = all_strike_prices[closest_index + 1:min(len(all_strike_prices), closest_index + 1 + num_strikes)]
            
            # Reverse lower strikes to have them in ascending order when displayed
            selected_strikes = sorted(lower_strikes + [closest_strike_price] + upper_strikes)
            
            # Build strike data with pricing
            strikes_dict = {s['strike']: s for s in strikes}
            
            strikes_data = []
            tokens_to_fetch = []
            
            for strike_price in selected_strikes:
                if strike_price in strikes_dict:
                    strike_info = strikes_dict[strike_price]
                    tokens_to_fetch.append({
                        'strike': strike_price,
                        'ce_token': strike_info['ce_token'],
                        'pe_token': strike_info['pe_token']
                    })
            
            # Fetch quote data for all tokens
            if tokens_to_fetch:
                ce_tokens = [t['ce_token'] for t in tokens_to_fetch]
                pe_tokens = [t['pe_token'] for t in tokens_to_fetch]
                all_tokens = ce_tokens + pe_tokens
                
                try:
                    # Convert tokens to integers and fetch quotes (use integer tokens, not "NFO:" prefix)
                    int_tokens = [int(token) for token in all_tokens]
                    logger.info(f"Fetching quotes for integer tokens: {int_tokens}")
                    quote_data = self.kite_service.kite.quote(int_tokens)
                    logger.info(f"Quote data received with keys: {list(quote_data.keys())}")
                    
                    # Log full quote data for debugging
                    if quote_data:
                        first_key = list(quote_data.keys())[0]
                        logger.info(f"Sample quote data structure for {first_key}: {quote_data[first_key]}")
                    else:
                        logger.warning("Quote data is empty!")
                    
                    # Process the quote data
                    for i, token_info in enumerate(tokens_to_fetch):
                        strike_price = token_info['strike']
                        ce_token_int = int(token_info['ce_token'])
                        pe_token_int = int(token_info['pe_token'])
                        
                        ce_price = 0.0
                        pe_price = 0.0
                        
                        # Try fetching by integer token first, then by string
                        if ce_token_int in quote_data:
                            ce_price = quote_data[ce_token_int].get('last_price', 0.0)
                            logger.debug(f"CE found by int token {ce_token_int}: {ce_price}")
                        elif str(ce_token_int) in quote_data:
                            ce_price = quote_data[str(ce_token_int)].get('last_price', 0.0)
                            logger.debug(f"CE found by str token {ce_token_int}: {ce_price}")
                        else:
                            logger.debug(f"CE token not found in quote_data: {ce_token_int}. Available keys: {list(quote_data.keys())}")
                        
                        if pe_token_int in quote_data:
                            pe_price = quote_data[pe_token_int].get('last_price', 0.0)
                            logger.debug(f"PE found by int token {pe_token_int}: {pe_price}")
                        elif str(pe_token_int) in quote_data:
                            pe_price = quote_data[str(pe_token_int)].get('last_price', 0.0)
                            logger.debug(f"PE found by str token {pe_token_int}: {pe_price}")
                        else:
                            logger.debug(f"PE token not found in quote_data: {pe_token_int}. Available keys: {list(quote_data.keys())}")
                        
                        logger.debug(f"Strike {strike_price}: CE={ce_price}, PE={pe_price}")
                        
                        strikes_data.append({
                            'strike': strike_price,
                            'ce_price': float(ce_price),
                            'pe_price': float(pe_price),
                            'ce_token': token_info['ce_token'],
                            'pe_token': token_info['pe_token'],
                            'is_atm': strike_price == closest_strike_price
                        })
                except Exception as e:
                    logger.warning(f"Error fetching quote data: {e}")
                    # Return strikes without pricing
                    for token_info in tokens_to_fetch:
                        strikes_data.append({
                            'strike': token_info['strike'],
                            'ce_price': 0.0,
                            'pe_price': 0.0,
                            'ce_token': token_info['ce_token'],
                            'pe_token': token_info['pe_token'],
                            'is_atm': token_info['strike'] == closest_strike_price
                        })
            
            # Get previous day high and low
            pdh = nifty_close  # Default to close
            pdl = nifty_close  # Default to close
            
            try:
                # Try to get PDH/PDL from historical data
                from trading_app.service.options_chart_service import OptionsChartService
                chart_service = OptionsChartService(self.kite_service.kite)
                
                # Get the underlying instrument token for PDH/PDL
                instrument_key = self._get_instrument_key(symbol)
                if instrument_key:
                    quote_result = self.kite_service.kite.quote([instrument_key])
                    if instrument_key in quote_result:
                        ohlc = quote_result[instrument_key].get('ohlc', {})
                        pdh = ohlc.get('high', pdh)
                        pdl = ohlc.get('low', pdl)
            except Exception as e:
                logger.warning(f"Error fetching PDH/PDL: {e}")
            
            # Fetch 5-minute chart data for each strike
            
            # Fetch 5-minute chart data for each strike
            logger.info("Skipping chart data fetch - will be fetched on frontend via /api/options-chart-data")
            
            return {
                'success': True,
                'symbol': symbol,
                'nifty_close': float(nifty_close),
                'pdh': float(pdh),
                'pdl': float(pdl),
                'closest_strike': float(closest_strike_price),
                'strikes_data': strikes_data,
                'chart_data': {
                    'ce_prices': [s['ce_price'] for s in strikes_data],
                    'pe_prices': [s['pe_price'] for s in strikes_data],
                    'strikes': [s['strike'] for s in strikes_data],
                },
                'timestamp': datetime.now().isoformat()
            }
        
        except Exception as e:
            logger.error(f"Error in get_multi_strike_data: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_previous_trading_day_close(self, symbol: str) -> Optional[float]:
        """
        Get the previous trading day's close price.
        
        Handles weekends and market holidays by going back in time until finding a trading day.
        
        Args:
            symbol: Trading symbol (e.g., 'NIFTY')
        
        Returns:
            Previous trading day's close price, or None if not available
        """
        try:
            # Get the instrument token for the symbol
            instrument_token = self.kite_service.get_instrument_token(symbol)
            if not instrument_token:
                logger.error(f"Could not find instrument token for {symbol}")
                return None
            
            # Fetch daily OHLC data for the last 5-10 days to find a previous trading day
            # Go back up to 10 days to account for weekends and holidays
            to_date = datetime.now()
            from_date = to_date - timedelta(days=10)
            
            logger.info(f"Fetching daily data for {symbol} (token: {instrument_token}) from {from_date.date()} to {to_date.date()}")
            
            try:
                historical_data = self.kite_service.kite.historical_data(
                    instrument_token=int(instrument_token),
                    from_date=from_date,
                    to_date=to_date,
                    interval='day'
                )
                logger.info(f"Daily historical data retrieved: {len(historical_data) if historical_data else 0} days")
            except Exception as api_err:
                logger.error(f"Error fetching daily historical data: {api_err}", exc_info=True)
                return None
            
            if not historical_data:
                logger.warning(f"No daily historical data returned for {symbol}")
                return None
            
            # Sort by date in descending order and get the first (most recent trading day before today)
            sorted_data = sorted(historical_data, key=lambda x: x['date'], reverse=True)
            
            if len(sorted_data) > 0:
                # Get the most recent trading day's close
                previous_day_data = sorted_data[0]
                previous_close = float(previous_day_data['close'])
                previous_date = previous_day_data['date']
                
                logger.info(f"Previous trading day close for {symbol} on {previous_date}: {previous_close}")
                return previous_close
            else:
                logger.warning(f"No trading day data found for {symbol}")
                return None
        
        except Exception as e:
            logger.error(f"Error in _get_previous_trading_day_close: {e}", exc_info=True)
            return None
    
    def _get_instrument_key(self, symbol: str) -> Optional[str]:
        """Get the instrument key for a symbol."""
        symbol_map = {
            'NIFTY': 'NSE:NIFTY 50',
            'BANKNIFTY': 'NSE:NIFTY BANK',
            'FINNIFTY': 'NSE:NIFTY FIN SERVICE'
        }
        return symbol_map.get(symbol)
    
    def _fetch_5min_chart_data(self, token: int, days_back: int = 60) -> List[Dict[str, Any]]:
        """
        Fetch 5-minute OHLC data for a token.
        
        Args:
            token: Instrument token
            days_back: Number of days back to fetch (default 60 = last 60 trading days)
        
        Returns:
            List of OHLC candles
        """
        try:
            # Calculate date range (last N days)
            to_date = datetime.now()
            from_date = to_date - timedelta(days=days_back)
            
            logger.info(f"Fetching 5min data for token {token} from {from_date.date()} to {to_date.date()}")
            
            try:
                historical_data = self.kite_service.kite.historical_data(
                    instrument_token=int(token),
                    from_date=from_date,
                    to_date=to_date,
                    interval='5minute'
                )
                logger.info(f"Historical data API call succeeded for token {token}")
            except Exception as api_err:
                logger.error(f"Historical data API error for token {token}: {api_err}", exc_info=True)
                return []
            
            logger.info(f"Historical data response for token {token}: type={type(historical_data)}, length={len(historical_data) if historical_data else 0}")
            
            if not historical_data:
                logger.warning(f"No historical data returned for token {token} - returned {type(historical_data)}")
                return []
            
            if not isinstance(historical_data, (list, tuple)):
                logger.error(f"Historical data is not a list/tuple for token {token}, got {type(historical_data)}: {historical_data}")
                return []
            
            logger.info(f"Processing {len(historical_data)} historical records for token {token}")
            
            # Convert to chart format
            chart_data = []
            for idx, candle in enumerate(historical_data):
                try:
                    # Parse the date - it could be a datetime object or a string
                    candle_date = candle.get('date')
                    if isinstance(candle_date, str):
                        from dateutil import parser
                        candle_datetime = parser.parse(candle_date)
                    else:
                        candle_datetime = candle_date
                    
                    if not candle_datetime:
                        logger.debug(f"Skipping candle {idx} with null date for token {token}")
                        continue
                    
                    chart_data.append({
                        'time': int(candle_datetime.timestamp()),
                        'open': float(candle['open']),
                        'high': float(candle['high']),
                        'low': float(candle['low']),
                        'close': float(candle['close']),
                        'volume': int(candle.get('volume', 0))
                    })
                except Exception as candle_err:
                    logger.warning(f"Error processing candle {idx} for token {token}: {candle_err}")
            
            logger.info(f"Successfully converted {len(chart_data)} candles for token {token}")
            if len(chart_data) == 0:
                logger.warning(f"Conversion resulted in 0 candles for token {token} from {len(historical_data)} historical records")
            
            return chart_data
        
        except Exception as e:
            logger.error(f"Error fetching 5min data for token {token}: {e}", exc_info=True)
            return []
