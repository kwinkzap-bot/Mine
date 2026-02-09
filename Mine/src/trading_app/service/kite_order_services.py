import logging
from kiteconnect import KiteConnect
import pandas as pd
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional # Added typing imports
import re # FIX: Moved 'import re' to the top for style and efficiency

load_dotenv()

class KiteService:
    def __init__(self, kite_instance: Optional[KiteConnect] = None) -> None:
        """
        Initializes the KiteService.
        """
        self.kite: KiteConnect = kite_instance or self._create_kite_instance()
        self.instruments: Optional[List[Dict[str, Any]]] = None
        self._instrument_tokens_by_symbol: Dict[str, int] = {}
        self._instrument_tokens_by_name: Dict[str, int] = {}
        self._nfo_instruments_cache: Optional[List[Dict[str, Any]]] = None
        self._nfo_option_symbol_cache: Dict[str, str] = {}  # Cache for option symbol lookups
        self._quote_cache: Dict[str, tuple] = {}  # Cache for quotes: {key: (price, timestamp)}
        self._quote_cache_ttl = 5  # Quote cache TTL in seconds
        if self.instruments is None:
            self._load_instruments()
    
    def _load_instruments(self):
        """Loads and processes instruments into lookup dictionaries. Includes both NSE and NFO."""
        try:
            # Load NSE instruments (for indices like NIFTY, BANKNIFTY)
            nse_instruments = self.kite.instruments('NSE')
            logging.info(f"[_load_instruments] Loaded {len(nse_instruments) if nse_instruments else 0} NSE instruments")
            
            # Load NFO instruments (for futures and options) - also cache separately for option lookups
            nfo_instruments = self.kite.instruments('NFO')
            logging.info(f"[_load_instruments] Loaded {len(nfo_instruments) if nfo_instruments else 0} NFO instruments")
            self._nfo_instruments_cache = nfo_instruments  # Cache for fast option symbol lookups
            
            # Combine both
            all_instruments = (nse_instruments or []) + (nfo_instruments or [])
            self.instruments = all_instruments
            
            # Build lookup dictionaries
            for instrument in all_instruments:
                symbol = instrument.get('tradingsymbol')
                name = instrument.get('name')
                token = instrument.get('instrument_token')
                if symbol and token:
                    self._instrument_tokens_by_symbol[symbol] = token
                if name and token:
                    self._instrument_tokens_by_name[name.lower()] = token
            
            logging.info(f"[_load_instruments] Built lookup: {len(self._instrument_tokens_by_symbol)} symbols, {len(self._instrument_tokens_by_name)} names")
        except Exception as e:
            logging.error(f"Error loading instruments: {e}")
    

    
    def _create_kite_instance(self) -> KiteConnect:
        """Creates and configures the KiteConnect instance."""
        api_key = os.getenv("API_KEY")
        access_token = os.getenv("ACCESS_TOKEN")
        kite = KiteConnect(api_key=api_key)
        
        if access_token and isinstance(access_token, str) and access_token.strip():
            kite.set_access_token(access_token)
        else:
            logging.error("ACCESS_TOKEN not found or empty. Kite access may be restricted.")
            
        return kite
    
    def get_instrument_token(self, symbol: str) -> Optional[int]:
        """Get instrument token for NSE equity or indices, including FINNIFTY."""
        try:
            # Hardcoded tokens for indices (in case lookup fails)
            HARDCODED_TOKENS = {
                'NIFTY': 256265.0,      # NIFTY 50
                'BANKNIFTY': 260105.0,  # NIFTY Bank
                'FINNIFTY': 257801.0    # NIFTY FIN SERVICE
            }
            
            # First try to get from loaded instruments
            token = self._instrument_tokens_by_symbol.get(symbol)
            if token:
                return token

            # Improved index lookup including FINNIFTY
            if symbol in ['NIFTY', 'BANKNIFTY', 'FINNIFTY']:
                search_name = symbol.lower().replace('nifty', 'nifty ').strip()
                if symbol == 'NIFTY': search_name = 'nifty 50'
                elif symbol == 'BANKNIFTY': search_name = 'nifty bank'
                elif symbol == 'FINNIFTY': search_name = 'nifty fin service'
                
                token = self._instrument_tokens_by_name.get(search_name)
                if token:
                    return token
                
                # Fallback to hardcoded token for known indices
                if symbol in HARDCODED_TOKENS:
                    hardcoded_token = HARDCODED_TOKENS[symbol]
                    logging.warning(f"[get_instrument_token] {symbol}: Lookup failed, using hardcoded token: {hardcoded_token}")
                    return int(hardcoded_token)
            
            logging.warning(f"No instrument found for {symbol}")
            return None
        except Exception as e:
            logging.error(f"Error getting instrument token for {symbol}: {e}")
            return None
    
    def get_current_ltp(self, symbol: str) -> Optional[float]:
        """Get current Last Traded Price (LTP) for a symbol."""
        try:
            # Map symbol to NSE instrument key
            if symbol == 'NIFTY':
                instrument_key = 'NSE:NIFTY 50'
            elif symbol == 'BANKNIFTY':
                instrument_key = 'NSE:NIFTY BANK'
            elif symbol == 'FINNIFTY':
                instrument_key = 'NSE:NIFTY FIN SERVICE'
            else:
                instrument_key = f'NSE:{symbol}'
            
            # Fetch LTP data
            ltp_data = self.kite.ltp([instrument_key])
            if ltp_data and isinstance(ltp_data, dict) and instrument_key in ltp_data:
                ltp = ltp_data[instrument_key].get('last_price')
                if ltp:
                    return float(ltp)
            
            logging.warning(f"Could not fetch LTP for {symbol}")
            return None
        except Exception as e:
            logging.error(f"Error getting current LTP for {symbol}: {e}")
            return None
    
    def get_previous_close(self, symbol: str) -> Optional[float]:
        """Get previous day's close price (PDC) for a symbol."""
        try:
            # Map symbol to NSE instrument key
            if symbol == 'NIFTY':
                instrument_key = 'NSE:NIFTY 50'
            elif symbol == 'BANKNIFTY':
                instrument_key = 'NSE:NIFTY BANK'
            elif symbol == 'FINNIFTY':
                instrument_key = 'NSE:NIFTY FIN SERVICE'
            else:
                instrument_key = f'NSE:{symbol}'
            
            # Fetch quote which contains previous close
            quote_data = self.kite.quote([instrument_key])
            if quote_data and isinstance(quote_data, dict) and instrument_key in quote_data:
                quote_item = quote_data[instrument_key]
                if isinstance(quote_item, dict):
                    ohlc = quote_item.get('ohlc', {})
                    pdc = ohlc.get('close')
                    if pdc:
                        return float(pdc)
            
            logging.warning(f"Could not fetch previous close for {symbol}")
            return None
        except Exception as e:
            logging.error(f"Error getting previous close for {symbol}: {e}")
            return None
    
    def get_previous_trading_day_close(self, symbol: str, target_date: Optional[str] = None) -> Optional[float]:
        """
        Get the previous trading day's close price using historical data.
        
        Handles weekends and market holidays by going back in time until finding a trading day.
        Excludes today to ensure we only get COMPLETE trading days.
        
        Args:
            symbol: Trading symbol (e.g., 'NIFTY', 'BANKNIFTY')
                target_date: Optional date string in YYYY-MM-DD format. If provided, returns close price from 
                        the PREVIOUS trading day (before the target_date).
        
        Returns:
            Previous trading day's close price, or None if not available
        """
        try:
            # Get the instrument token for the symbol
            instrument_token = self.get_instrument_token(symbol)
            
            # If target_date is provided, MUST use historical data (quote API only has current price)
            if target_date:
                if not instrument_token:
                    logging.error(f"[get_previous_trading_day_close] Cannot fetch historical data for {target_date}: no instrument token found for {symbol}")
                    logging.error(f"[get_previous_trading_day_close] Quote API fallback NOT available for historical dates (only returns current price)")
                    return None
                
                try:
                    # Parse target_date (format: YYYY-MM-DD)
                    target_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
                    
                    # IMPORTANT: Fetch data BEFORE the target date to get previous day's close
                    # Fetch from 30 days before target to 1 day before target (covers weekends/holidays)
                    from_date = datetime.combine(target_dt - timedelta(days=30), datetime.min.time())
                    to_date = datetime.combine(target_dt - timedelta(days=1), datetime.max.time())
                    
                    logging.info(f"[get_previous_trading_day_close] {symbol} (target_date={target_date})")
                    logging.info(f"[get_previous_trading_day_close]   Fetching from {from_date.date()} to {to_date.date()}")
                    
                    historical_data = self.kite.historical_data(
                        instrument_token=int(instrument_token),
                        from_date=from_date,
                        to_date=to_date,
                        interval='day'
                    )
                    
                    if historical_data:
                        # Sort by date in descending order to get the most recent trading day before target_date
                        sorted_data = sorted(historical_data, key=lambda x: x['date'], reverse=True)
                        
                        # Log available dates for debugging
                        logging.info(f"[get_previous_trading_day_close]   Found {len(sorted_data)} trading days")
                        for i, data in enumerate(sorted_data[:3]):
                            data_date = data['date'].date() if hasattr(data['date'], 'date') else data['date']
                            logging.info(f"[get_previous_trading_day_close]     [{i}] {data_date}: {data['close']:.2f}")
                        
                        # Get the first entry (most recent) which should be the previous trading day
                        most_recent_data = sorted_data[0]
                        most_recent_date = most_recent_data['date'].date() if hasattr(most_recent_data['date'], 'date') else most_recent_data['date']
                        close_price = float(most_recent_data['close'])
                        
                        logging.info(f"[get_previous_trading_day_close] ✓ target_date={target_date} → using {most_recent_date} close: {close_price:.2f}")
                        return close_price
                    else:
                        logging.warning(f"[get_previous_trading_day_close] No historical data found for target_date {target_date}")
                        return None
                except Exception as e:
                    logging.error(f"[get_previous_trading_day_close] Error fetching historical data for target_date {target_date}: {e}", exc_info=True)
                    return None
            
            # If NO target_date provided and NO instrument token, return None
            if not instrument_token:
                logging.error(f"[get_previous_trading_day_close] Could not find token for {symbol}")
                return None
            
            # Original logic for current date (when target_date is None and we have an instrument token)
            # IMPORTANT: Exclude today's incomplete data
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            
            # Fetch from 20 days ago to yesterday (covers weekends/holidays)
            to_date = datetime.combine(yesterday, datetime.max.time())
            from_date = to_date - timedelta(days=20)
            
            logging.info(f"[get_previous_trading_day_close] {symbol} (current date)")
            logging.info(f"[get_previous_trading_day_close]   Today: {today}, Yesterday: {yesterday}")
            logging.info(f"[get_previous_trading_day_close]   Fetching from {from_date.date()} to {to_date.date()}")
            
            try:
                historical_data = self.kite.historical_data(
                    instrument_token=int(instrument_token),
                    from_date=from_date,
                    to_date=to_date,
                    interval='day'
                )
                logging.info(f"[get_previous_trading_day_close]   Retrieved {len(historical_data) if historical_data else 0} days of data")
                
                if historical_data:
                    logging.info(f"[get_previous_trading_day_close]   === RAW DATA ===")
                    for i, data in enumerate(historical_data[-3:]):  # Show last 3
                        data_date = data['date'].date() if hasattr(data['date'], 'date') else data['date']
                        logging.info(f"[get_previous_trading_day_close]     {data_date}: {data['close']:.2f}")
            except Exception as api_err:
                logging.error(f"[get_previous_trading_day_close]   Error fetching: {api_err}", exc_info=True)
                return None
            
            if not historical_data:
                logging.warning(f"[get_previous_trading_day_close]   No historical data returned for {symbol}")
                return None
            
            # Sort by date in DESCENDING order to get the most recent
            sorted_data = sorted(historical_data, key=lambda x: x['date'], reverse=True)
            
            logging.info(f"[get_previous_trading_day_close]   === AFTER SORTING ===")
            for i, data in enumerate(sorted_data[:3]):
                data_date = data['date'].date() if hasattr(data['date'], 'date') else data['date']
                logging.info(f"[get_previous_trading_day_close]     [{i}] {data_date}: {data['close']:.2f}")
            
            if len(sorted_data) > 0:
                # Get the most recent trading day's close
                previous_day_data = sorted_data[0]
                previous_close = float(previous_day_data['close'])
                previous_date = previous_day_data['date'].date() if hasattr(previous_day_data['date'], 'date') else previous_day_data['date']
                
                logging.info(f"[get_previous_trading_day_close]   Selected: {previous_date}, Close={previous_close:.2f}")
                logging.info(f"[get_previous_trading_day_close]   Today={today}, Previous={previous_date}, Same? {previous_date == today}")
                
                # Verify we're not getting today's data
                if previous_date == today:
                    logging.warning(f"[get_previous_trading_day_close] ⚠️  Got today's data! Using second-most-recent")
                    if len(sorted_data) > 1:
                        previous_day_data = sorted_data[1]
                        previous_close = float(previous_day_data['close'])
                        previous_date = previous_day_data['date'].date() if hasattr(previous_day_data['date'], 'date') else previous_day_data['date']
                        logging.info(f"[get_previous_trading_day_close]   Fallback to: {previous_date}, Close={previous_close:.2f}")
                    else:
                        logging.error(f"[get_previous_trading_day_close] ❌ Only have today's data")
                        return None
                
                logging.info(f"[get_previous_trading_day_close] ✓ FINAL: {symbol} on {previous_date}: {previous_close:.2f}")
                return previous_close
            else:
                logging.warning(f"[get_previous_trading_day_close]   No data after sorting")
                return None
        except Exception as e:
            logging.error(f"[get_previous_trading_day_close] Exception: {e}", exc_info=True)
            return None

    def get_fo_stocks(self) -> List[str]:
        """Get list of F&O underlying stocks, including FUTURES and OPTIONS."""
        try:
            nfo_instruments = self.kite.instruments('NFO')
            fo_symbols = set()
            
            for inst in nfo_instruments:
                # FIX: Check for both FUTURES and OPTIONS for comprehensive underlying list
                if inst.get('instrument_type') in ['FUT', 'OPT']: 
                    tsymbol = inst.get('tradingsymbol', '')
                    if tsymbol:
                        # 're' is now imported at the top
                        match = re.match(r'^([A-Z]+)', tsymbol) 
                        if match and len(match.group(1)) > 1:
                            fo_symbols.add(match.group(1))
            
            fo_list = sorted(list(fo_symbols))
            
            # Ensure indices are at the top and avoid duplicates
            indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY'] # FINNIFTY added
            result = [s for s in indices if s in fo_list or s in indices] 
            for symbol in fo_list:
                if symbol not in result:
                    result.append(symbol)
            
            return result

        except Exception as e:
            logging.error(f"Error getting F&O stocks: {e}")
            return []
    
    def get_historical_data(self, symbol: str, from_date: datetime, to_date: datetime, interval: str = 'day') -> Optional[pd.DataFrame]:
        """Fetches historical data, ensuring 'date' column is timezone-naive datetime."""
        try:
            token = self.get_instrument_token(symbol)
            logging.debug(f"Token for {symbol}: {token}")
            if not token:
                return None
            
            # Removed redundant datetime conversion checks since type hints suggest datetime objects
            # Assuming callers pass datetime objects, or adding the check back if needed:
            if isinstance(from_date, str):
                from_date = datetime.strptime(from_date, '%Y-%m-%d')
            if isinstance(to_date, str):
                to_date = datetime.strptime(to_date, '%Y-%m-%d')
            
            data = self.kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval=interval
            )
            
            if data:
                df = pd.DataFrame(data)
                # FIX: Ensure 'date' column is a timezone-naive datetime for consistency
                if 'date' in df.columns:
                     try:
                         df['date'] = pd.to_datetime(df['date'])
                         # Use type: ignore to suppress false positive from Pylance
                         if hasattr(df['date'], 'dt'):
                             df['date'] = df['date'].dt.tz_localize(None)  # type: ignore
                     except Exception:
                         pass  # Keep original date format if conversion fails
                return df
            return None
        except Exception as e:
            logging.error(f"Error fetching data for {symbol}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_lot_size(self, symbol: str, exchange: str = 'NFO') -> int:
        """Get the lot size (quantity multiplier) for a symbol.
        
        Uses cached NFO instruments for fast lookup instead of fetching from API.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, FINNIFTY, etc.)
            exchange: Exchange (default: 'NFO')
            
        Returns:
            Lot size (default: 1 if not found)
        """
        try:
            # Use cached NFO instruments instead of fetching from API
            instruments = self._nfo_instruments_cache
            
            # If cache is empty, load it once
            if not instruments:
                logging.warning("NFO instruments cache empty in get_lot_size, loading...")
                instruments = self.kite.instruments(exchange)
                self._nfo_instruments_cache = instruments
            
            # Look for any option instrument with the given symbol to get lot size
            # All options for the same underlying have the same lot size
            for inst in instruments:
                # Check if this is an option for our symbol
                if (inst.get('name') == symbol and 
                    inst.get('instrument_type') in ['CE', 'PE', 'OPTIDX', 'OPTSTK']):
                    lot_size = inst.get('lot_size')
                    if lot_size and lot_size > 0:
                        logging.info(f"✓ Lot size for {symbol}: {lot_size} (from instruments)")
                        return int(lot_size)
            
            # If not found in loaded instruments, try using hardcoded fallback
            logging.warning(f"⚠️  Lot size not found in instruments for {symbol}, using fallback")
            
            # Default lot sizes (as of Jan 2026) - fallback only
            default_lots = {
                'NIFTY': 65,
                'BANKNIFTY': 25,
                'FINNIFTY': 40,
                'MIDCPNIFTY': 50,
                'SENSEX': 10,
                'BANKEX': 15
            }
            
            lot_size = default_lots.get(symbol, 1)
            logging.warning(f"Using fallback lot size {lot_size} for {symbol}")
            return lot_size
            
        except Exception as e:
            logging.error(f"Error getting lot size for {symbol}: {e}")
            # Emergency fallback
            return {'NIFTY': 65, 'BANKNIFTY': 25, 'FINNIFTY': 40}.get(symbol, 1)
    
    def get_option_symbol(self, symbol: str, strike: int, option_type: str, exchange: str = 'NFO') -> Optional[str]:
        """Get the trading symbol for an option.
        
        Uses cached NFO instruments for fast lookup instead of fetching from API.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            exchange: Exchange (default: 'NFO')
            
        Returns:
            Trading symbol or None if not found
        """
        try:
            # Create cache key for this lookup
            cache_key = f"{symbol}_{strike}_{option_type}"
            
            # Check if already cached
            if cache_key in self._nfo_option_symbol_cache:
                return self._nfo_option_symbol_cache[cache_key]
            
            # Use cached NFO instruments instead of fetching from API (saves ~10-15 seconds)
            nfo_instruments = self._nfo_instruments_cache
            
            # If cache is empty, load it once
            if not nfo_instruments:
                logging.warning("NFO instruments cache empty, loading...")
                nfo_instruments = self.kite.instruments(exchange)
                self._nfo_instruments_cache = nfo_instruments
            
            matching_instruments = []
            for inst in nfo_instruments:
                if (inst.get('name') == symbol and
                    inst.get('instrument_type') == option_type and
                    inst.get('strike') == strike and
                    inst.get('expiry')):
                    
                    expiry_date = inst['expiry']
                    if hasattr(expiry_date, 'date'):
                        expiry_date = expiry_date.date()
                    
                    from datetime import date
                    # Skip today's expiry (expires at 3:30 PM)
                    # Only include contracts expiring tomorrow or later
                    if expiry_date > date.today():
                        matching_instruments.append(inst)
            
            if matching_instruments:
                # Sort by expiry and get the nearest
                matching_instruments.sort(key=lambda x: x['expiry'])
                tradingsymbol = matching_instruments[0]['tradingsymbol']
                # Cache the result for future lookups
                self._nfo_option_symbol_cache[cache_key] = tradingsymbol
                logging.debug(f"Found option symbol: {tradingsymbol} for {symbol} {option_type} {strike}")
                return tradingsymbol
            
            logging.warning(f"No {option_type} option found for {symbol} strike {strike}")
            return None
            
        except Exception as e:
            logging.error(f"Error getting option symbol for {symbol} {option_type} {strike}: {e}", exc_info=True)
            return None
    
    def place_order(self, tradingsymbol: str, transaction_type: str, price: float, 
                   quantity: int = 65, product: str = 'NRML', order_type: str = 'MARKET',
                   exchange: str = 'NFO', trigger_price: Optional[float] = None) -> Dict[str, Any]:
        """Place an order in Zerodha Kite.
        
        Args:
            tradingsymbol: Trading symbol (e.g., 'NIFTY25D26C25000')
            transaction_type: BUY or SELL (use kite.TRANSACTION_TYPE_BUY/SELL)
            price: Order price (execution price for LIMIT/stoploss orders)
            quantity: Order quantity (default: 75)
            product: Product type - NRML (normal/default), MIS (intraday), CNC (delivery)
            order_type: ORDER_TYPE_MARKET (default) or ORDER_TYPE_LIMIT
            exchange: Exchange - NFO (options), NSE (stocks)
            trigger_price: Trigger price for stoploss orders (optional)
            
        Returns:
            Dict with success status, order_id, and details
        """
        try:
            # Check if market is open (9:15 AM to 3:30 PM IST)
            from datetime import time
            now = datetime.now().time()
            market_open = time(9, 15)
            market_close = time(15, 30)
            
            # Determine order variety based on market hours
            if market_open <= now <= market_close:
                variety = self.kite.VARIETY_REGULAR
                order_time = "REGULAR"
            else:
                variety = self.kite.VARIETY_AMO
                order_time = "AMO"
            
            # Log with trigger_price info if provided
            if trigger_price:
                logging.info(f"Placing {order_time} {transaction_type} order: {tradingsymbol} @ ₹{price:.2f} (execute) with trigger @ ₹{trigger_price:.2f} x {quantity}")
            else:
                logging.info(f"Placing {order_time} {transaction_type} order: {tradingsymbol} @ ₹{price:.2f} x {quantity}")
            
            # Map product string to Kite constant
            product_map = {
                'MIS': self.kite.PRODUCT_MIS,
                'CNC': self.kite.PRODUCT_CNC,
                'NRML': self.kite.PRODUCT_NRML
            }
            product_type = product_map.get(product, self.kite.PRODUCT_MIS)
            
            # Map order type string to Kite constant
            order_type_map = {
                'LIMIT': self.kite.ORDER_TYPE_LIMIT,
                'MARKET': self.kite.ORDER_TYPE_MARKET
            }
            order_type_const = order_type_map.get(order_type, self.kite.ORDER_TYPE_LIMIT)
            
            # Map exchange string to Kite constant
            exchange_map = {
                'NFO': self.kite.EXCHANGE_NFO,
                'NSE': self.kite.EXCHANGE_NSE,
                'BSE': self.kite.EXCHANGE_BSE
            }
            exchange_const = exchange_map.get(exchange, self.kite.EXCHANGE_NFO)
            
            # Build order parameters
            order_params = {
                'variety': variety,
                'exchange': exchange_const,
                'tradingsymbol': tradingsymbol,
                'transaction_type': transaction_type,
                'quantity': quantity,
                'product': product_type,
                'order_type': order_type_const,
                'price': price
            }
            
            # Add trigger_price if provided (for stoploss orders)
            if trigger_price is not None and trigger_price > 0:
                order_params['trigger_price'] = trigger_price
                logging.info(f"[Stoploss] Adding trigger_price={trigger_price:.2f} to order parameters")
            
            # Place the order with all parameters
            order_id = self.kite.place_order(**order_params)
            
            if trigger_price:
                logging.info(f"✅ {order_time} Stoploss Order placed successfully. Order ID: {order_id} | {tradingsymbol} @ Execute: ₹{price:.2f}, Trigger: ₹{trigger_price:.2f}")
            else:
                logging.info(f"✅ {order_time} Order placed successfully. Order ID: {order_id} | {tradingsymbol} @ ₹{price:.2f}")
            
            return {
                'success': True,
                'order_id': order_id,
                'symbol': tradingsymbol,
                'price': price,
                'quantity': quantity,
                'transaction_type': transaction_type,
                'trigger_price': trigger_price if trigger_price else None
            }
            
        except Exception as e:
            logging.error(f"❌ Error placing order for {tradingsymbol}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': tradingsymbol
            }
    
    def get_cached_quote(self, instrument_key: str, use_cache: bool = True) -> Optional[float]:
        """
        Fetch quote for an instrument with caching to reduce API calls.
        
        Args:
            instrument_key: Instrument key (e.g., 'NFO:NIFTY23JAN19100CE')
            use_cache: Whether to use cached price if available
            
        Returns:
            Last price as float, or None if unable to fetch
        """
        import time
        
        # Check cache if enabled
        if use_cache and instrument_key in self._quote_cache:
            price, timestamp = self._quote_cache[instrument_key]
            if time.time() - timestamp < self._quote_cache_ttl:
                logging.debug(f"Using cached price for {instrument_key}: {price}")
                return price
        
        try:
            quote = self.kite.quote(instrument_key)
            if isinstance(quote, dict) and instrument_key in quote:
                quote_item = quote[instrument_key]
                if isinstance(quote_item, dict):
                    price = quote_item.get('last_price')
                    if not price:
                        price = quote_item.get('close')
                    
                    if price:
                        # Cache the price
                        self._quote_cache[instrument_key] = (price, time.time())
                        return price
        except Exception as e:
            logging.warning(f"Error fetching quote for {instrument_key}: {e}")
            # Try to return cached price even if expired
            if instrument_key in self._quote_cache:
                price, _ = self._quote_cache[instrument_key]
                logging.warning(f"Using expired cached price for {instrument_key}: {price}")
                return price
        
        return None
    
    def place_option_order(self, symbol: str, strike: int, option_type: str, 
                          transaction_type: str, quantity: Optional[int] = None) -> Dict[str, Any]:
        """Place an order for an option contract.
        
        Convenience method that combines option symbol lookup and order placement.
        Uses parallel operations to get lot size, option symbol, and price simultaneously.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            transaction_type: BUY or SELL
            quantity: Order quantity (default: None - uses lot size from Kite)
            
        Returns:
            Dict with success status and order details
        """
        try:
            # Parallel operation: Get lot size, option symbol, and price at the same time
            from concurrent.futures import ThreadPoolExecutor
            
            def get_tradingsymbol():
                return self.get_option_symbol(symbol, strike, option_type)
            
            def get_price_for_symbol(tradingsymbol: str) -> Optional[float]:
                """Helper to get price with caching"""
                instrument_key = f'NFO:{tradingsymbol}'
                return self.get_cached_quote(instrument_key, use_cache=True)
            
            # Get lot size and trading symbol in parallel
            with ThreadPoolExecutor(max_workers=2) as executor:
                lot_size_future = executor.submit(self.get_lot_size, symbol) if quantity is None else None
                tradingsymbol_future = executor.submit(get_tradingsymbol)
                
                if lot_size_future:
                    quantity = lot_size_future.result()
                tradingsymbol = tradingsymbol_future.result()
            
            if not tradingsymbol:
                return {
                    'success': False,
                    'error': f'Could not find {option_type} option for {symbol} strike {strike}',
                    'symbol': symbol,
                    'strike': strike,
                    'option_type': option_type
                }
            
            # Get price with caching (fast if cached)
            price = get_price_for_symbol(tradingsymbol)
            
            if not price:
                return {
                    'success': False,
                    'error': f'Could not determine price for {tradingsymbol}',
                    'symbol': tradingsymbol
                }
            
            # Ensure quantity is set
            if quantity is None:
                quantity = 1  # Fallback default
            
            # Place the order
            result = self.place_order(
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                price=price,
                quantity=quantity,
                product='NRML',
                order_type='MARKET',
                exchange='NFO'
            )
            
            if result['success']:
                result['option_type'] = option_type
                result['strike'] = strike
                result['underlying'] = symbol
            
            return result
            
        except Exception as e:
            logging.error(f"Error in place_option_order: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol,
                'strike': strike,
                'option_type': option_type
            }
    
    def place_stoploss_order(self, tradingsymbol: str, trigger_price: float, 
                            quantity: int, product: str = 'NRML') -> Dict[str, Any]:
        """Place a stop loss (sell) order on Zerodha Kite.
        
        Creates a sell order with a trigger price that automatically executes
        when the price drops to the trigger level. Uses REGULAR variety with trigger.
        
        Args:
            tradingsymbol: Trading symbol (e.g., 'NIFTY25D26C25000')
            trigger_price: SL trigger price (when to activate the order)
            quantity: Order quantity (default: 75)
            product: Product type - NRML (normal/default), MIS (intraday)
            
        Returns:
            Dict with success status, order_id, and details
        """
        try:
            from datetime import time
            now = datetime.now().time()
            market_open = time(9, 15)
            market_close = time(15, 30)
            
            if market_open <= now <= market_close:
                variety = self.kite.VARIETY_REGULAR
            else:
                variety = self.kite.VARIETY_AMO
            
            product_map = {
                'MIS': self.kite.PRODUCT_MIS,
                'CNC': self.kite.PRODUCT_CNC,
                'NRML': self.kite.PRODUCT_NRML
            }
            product_type = product_map.get(product, self.kite.PRODUCT_MIS)
            
            # For Zerodha stoploss orders:
            # - trigger_price: Price at which order becomes active
            # - price: Execution limit price (should be AT or VERY CLOSE to trigger)
            # 
            # CRITICAL: If execution price is too far below trigger, and current price 
            # is between execution and trigger, the order executes IMMEDIATELY!
            # 
            # Solution: Set execution price = trigger price (sell at trigger level)
            # This ensures the order ONLY executes when price hits the trigger level
            
            TICK_SIZE = 0.05
            
            # Round trigger_price to proper tick size
            trigger_price = round(int(trigger_price / TICK_SIZE) * TICK_SIZE, 2)
            
            # For SELL stoploss: execution_price = trigger_price
            # This ensures order executes AT the trigger level, not immediately
            execution_price = trigger_price
            
            logging.info(f"Placing SL order: {tradingsymbol} @ ₹{trigger_price:.2f} (trigger & execute) x {quantity}")
            
            # Place stop loss order with trigger price
            # For Zerodha Kite: Use SL order type (not LIMIT)
            # SL = Stop Loss order that waits for trigger_price before executing
            # The trigger_price parameter is critical - it tells Kite when to activate
            order_id = self.kite.place_order(
                variety=variety,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=tradingsymbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                product=product_type,
                order_type=self.kite.ORDER_TYPE_SL,  # SL (Stop Loss) order type - CRITICAL!
                price=execution_price,  # Execution price (limit price when triggered)
                trigger_price=trigger_price  # Trigger price (when to activate the order)
            )
            
            logging.info(f"✅ SL Order placed successfully. Order ID: {order_id} | {tradingsymbol} @ ₹{trigger_price:.2f} (stoploss trigger)")
            
            return {
                'success': True,
                'order_id': order_id,
                'symbol': tradingsymbol,
                'trigger_price': trigger_price,
                'execution_price': execution_price,
                'quantity': quantity,
                'order_type': 'STOPLOSS'
            }
            
        except Exception as e:
            logging.error(f"❌ Error placing SL order for {tradingsymbol}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': tradingsymbol
            }
    
    def modify_stoploss_order(self, order_id: str, new_trigger_price: float, 
                             quantity: Optional[int] = None) -> Dict[str, Any]:
        """Modify an existing stop loss order with a new trigger price.
        
        Used for trailing SL - updates the trigger price as price moves favorably.
        
        Args:
            order_id: Order ID to modify
            new_trigger_price: New SL trigger price
            quantity: Optional quantity update
            
        Returns:
            Dict with success status and details
        """
        try:
            from datetime import time
            now = datetime.now().time()
            market_open = time(9, 15)
            market_close = time(15, 30)
            
            if market_open <= now <= market_close:
                variety = self.kite.VARIETY_REGULAR
            else:
                variety = self.kite.VARIETY_AMO
            
            logging.info(f"Modifying SL order {order_id} to trigger price: ₹{new_trigger_price:.2f}")
            
            # For modifying a LIMIT order with trigger, both price and trigger_price should match
            params = {
                'variety': variety,
                'order_id': order_id,
                'trigger_price': new_trigger_price,
                'price': new_trigger_price  # Use same price as trigger for SL orders
            }
            
            if quantity:
                params['quantity'] = quantity
            
            result_order_id = self.kite.modify_order(**params)
            
            logging.info(f"✅ SL Order modified successfully. Order ID: {result_order_id}")
            
            return {
                'success': True,
                'order_id': result_order_id,
                'new_trigger_price': new_trigger_price,
                'order_type': 'STOPLOSS_MODIFIED'
            }
            
        except Exception as e:
            logging.error(f"❌ Error modifying SL order {order_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'order_id': order_id
            }
    
    def cancel_order(self, order_id: str, variety: str = 'regular') -> Dict[str, Any]:
        """Cancel an existing order.
        
        Args:
            order_id: Order ID to cancel
            variety: Order variety (regular, amo, etc.)
            
        Returns:
            Dict with success status and details
        """
        try:
            variety_map = {
                'regular': self.kite.VARIETY_REGULAR,
                'amo': self.kite.VARIETY_AMO
            }
            variety_const = variety_map.get(variety, self.kite.VARIETY_REGULAR)
            
            logging.info(f"Canceling order: {order_id}")
            
            result_order_id = self.kite.cancel_order(
                variety=variety_const,
                order_id=order_id
            )
            
            logging.info(f"✅ Order canceled successfully. Order ID: {result_order_id}")
            
            return {
                'success': True,
                'order_id': result_order_id,
                'action': 'CANCELLED'
            }
            
        except Exception as e:
            logging.error(f"❌ Error canceling order {order_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'order_id': order_id
            }