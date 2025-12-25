import logging
from kiteconnect import KiteConnect
import pandas as pd
from datetime import datetime
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
        if self.instruments is None:
            self._load_instruments()
    
    def _load_instruments(self):
        """Loads and processes instruments into lookup dictionaries. Added try/except for robustness."""
        try:
            self.instruments = self.kite.instruments('NSE')
            for instrument in self.instruments:
                symbol = instrument.get('tradingsymbol')
                name = instrument.get('name')
                token = instrument.get('instrument_token')
                if symbol and token:
                    self._instrument_tokens_by_symbol[symbol] = token
                if name and token:
                    self._instrument_tokens_by_name[name.lower()] = token
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
                ohlc = quote_data[instrument_key].get('ohlc', {})
                pdc = ohlc.get('close')
                if pdc:
                    return float(pdc)
            
            logging.warning(f"Could not fetch previous close for {symbol}")
            return None
        except Exception as e:
            logging.error(f"Error getting previous close for {symbol}: {e}")
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
                     df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
                return df
            return None
        except Exception as e:
            logging.error(f"Error fetching data for {symbol}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_lot_size(self, symbol: str, exchange: str = 'NFO') -> int:
        """Get the lot size (quantity multiplier) for a symbol.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, FINNIFTY, etc.)
            exchange: Exchange (default: 'NFO')
            
        Returns:
            Lot size (default: 1 if not found)
        """
        try:
            instruments = self.kite.instruments(exchange)
            
            for inst in instruments:
                if inst.get('name') == symbol and inst.get('instrument_type') in ['OPTIDX', 'OPTSTK']:
                    lot_size = inst.get('lot_size')
                    if lot_size and lot_size > 0:
                        logging.debug(f"Lot size for {symbol}: {lot_size}")
                        return int(lot_size)
            
            # Default lot sizes if not found in instruments
            default_lots = {
                'NIFTY': 75,
                'BANKNIFTY': 25,
                'FINNIFTY': 40
            }
            
            lot_size = default_lots.get(symbol, 1)
            logging.warning(f"Using default lot size {lot_size} for {symbol}")
            return lot_size
            
        except Exception as e:
            logging.error(f"Error getting lot size for {symbol}: {e}")
            return 1  # Fallback to 1 if error
    
    def get_option_symbol(self, symbol: str, strike: int, option_type: str, exchange: str = 'NFO') -> Optional[str]:
        """Get the trading symbol for an option.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            exchange: Exchange (default: 'NFO')
            
        Returns:
            Trading symbol or None if not found
        """
        try:
            nfo_instruments = self.kite.instruments(exchange)
            
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
                logging.debug(f"Found option symbol: {tradingsymbol} for {symbol} {option_type} {strike}")
                return tradingsymbol
            
            logging.warning(f"No {option_type} option found for {symbol} strike {strike}")
            return None
            
        except Exception as e:
            logging.error(f"Error getting option symbol for {symbol} {option_type} {strike}: {e}", exc_info=True)
            return None
    
    def place_order(self, tradingsymbol: str, transaction_type: str, price: float, 
                   quantity: int = 75, product: str = 'NRML', order_type: str = 'MARKET',
                   exchange: str = 'NFO') -> Dict[str, Any]:
        """Place an order in Zerodha Kite.
        
        Args:
            tradingsymbol: Trading symbol (e.g., 'NIFTY25D26C25000')
            transaction_type: BUY or SELL (use kite.TRANSACTION_TYPE_BUY/SELL)
            price: Order price (ignored for MARKET orders)
            quantity: Order quantity (default: 75)
            product: Product type - NRML (normal/default), MIS (intraday), CNC (delivery)
            order_type: ORDER_TYPE_MARKET (default - normal/market order) or ORDER_TYPE_LIMIT
            exchange: Exchange - NFO (options), NSE (stocks)
            
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
            
            order_id = self.kite.place_order(
                variety=variety,
                exchange=exchange_const,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=product_type,
                order_type=order_type_const,
                price=price
            )
            
            logging.info(f"✅ {order_time} Order placed successfully. Order ID: {order_id} | {tradingsymbol} @ ₹{price:.2f}")
            
            return {
                'success': True,
                'order_id': order_id,
                'symbol': tradingsymbol,
                'price': price,
                'quantity': quantity,
                'transaction_type': transaction_type
            }
            
        except Exception as e:
            logging.error(f"❌ Error placing order for {tradingsymbol}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': tradingsymbol
            }
    
    def place_option_order(self, symbol: str, strike: int, option_type: str, 
                          transaction_type: str, quantity: Optional[int] = None) -> Dict[str, Any]:
        """Place an order for an option contract.
        
        Convenience method that combines option symbol lookup and order placement.
        
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
            # Use dynamic lot size if quantity not provided
            if quantity is None:
                quantity = self.get_lot_size(symbol)
            # Get the option trading symbol
            tradingsymbol = self.get_option_symbol(symbol, strike, option_type)
            if not tradingsymbol:
                return {
                    'success': False,
                    'error': f'Could not find {option_type} option for {symbol} strike {strike}',
                    'symbol': symbol,
                    'strike': strike,
                    'option_type': option_type
                }
            
            # Get current market price
            try:
                instrument_key = f'NFO:{tradingsymbol}'
                quote = self.kite.quote(instrument_key)
                price = quote[instrument_key].get('last_price')
                if not price:
                    price = quote[instrument_key].get('close')
            except Exception as e:
                logging.warning(f"Could not fetch price for {tradingsymbol}: {e}")
                return {
                    'success': False,
                    'error': f'Could not determine price for {tradingsymbol}',
                    'symbol': tradingsymbol
                }
            
            if not price:
                return {
                    'success': False,
                    'error': f'Invalid price for {tradingsymbol}',
                    'symbol': tradingsymbol
                }
            
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