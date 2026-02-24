import logging
import os
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List
from datetime import datetime
import requests
from neo_api_client import NeoAPI

load_dotenv()

class KotakOrderService:
    """
    Service for placing orders in Kotak Neo trading platform using neo_api_client.
    Handles order placement, execution, and tracking for options and futures.
    
    Official Docs: https://www.notion.so/Getting-started-15-min-28eda70d37e280a09158f091b369561e
    """
    
    def __init__(self, mobile_number: Optional[str] = None, ucc: Optional[str] = None, 
                 mpin: Optional[str] = None, totp_secret: Optional[str] = None,
                 access_token: Optional[str] = None):
        """
        Initialize KotakOrderService with Kotak Neo credentials.
        """
        self.mobile_number = mobile_number or os.getenv("KOTAK_MOBILE_NUMBER")
        self.ucc = ucc or os.getenv("KOTAK_UCC") or os.getenv("KOTAK_CLIENT_ID")
        self.mpin = mpin or os.getenv("KOTAK_MPIN") or os.getenv("KOTAK_PASSWORD")
        self.totp_secret = totp_secret or os.getenv("KOTAK_TOTP_SECRET")
        self.access_token = access_token or os.getenv("KOTAK_ACCESS_TOKEN")
        
        # Authentication tokens (from 2-step auth in env or generated)
        self.trading_token = os.getenv("KOTAK_TRADING_TOKEN")
        self.trading_sid = os.getenv("KOTAK_TRADING_SID")
        self.server_id = os.getenv("KOTAK_SERVER_ID")
        self.base_url = os.getenv("KOTAK_BASE_URL", "https://ngw-lo.kotaksecurities.com")
        
        self.last_error = None
        self.client = None
        
        logging.info("[KotakOrderService] Initialized with Kotak Neo credentials")
        
        # Initialize NeoAPI client if possible
        self._init_client()

        # Order type mappings for Kotak Neo
        self.ORDER_TYPE_MARKET = 'MKT'
        self.ORDER_TYPE_LIMIT = 'L'
        self.ORDER_TYPE_STOP_LOSS = 'SL'
        self.ORDER_TYPE_STOP_LOSS_MARKET = 'SL-M'
        
        # Transaction type mappings
        self.TRANSACTION_BUY = 'B'
        self.TRANSACTION_SELL = 'S'
        
        # Product type mappings for Kotak Neo
        self.PRODUCT_MIS = 'MIS'  # Margin Intraday Square Off
        self.PRODUCT_CNC = 'CNC'  # Cash and Carry
        self.PRODUCT_NRML = 'NRML'  # Normal (Futures/Options)
        
        # Exchange segments
        self.EXCHANGE_NSE = 'nse_cm'
        self.EXCHANGE_NFO = 'nse_fo'
        self.EXCHANGE_BSE = 'bse_cm'
        
    def _init_client(self):
        """Initialize NeoAPI client"""
        try:
            if self.access_token:
                # Initialize with access token
                self.client = NeoAPI(access_token=self.access_token, environment='prod')
                
                # Manually inject trading tokens if available
                if self.trading_token and self.trading_sid:
                    self.client.configuration.edit_token = self.trading_token
                    self.client.configuration.edit_sid = self.trading_sid
                    if self.server_id:
                        self.client.configuration.serverId = self.server_id
                    logging.info("[KotakOrderService] NeoAPI client initialized with injected tokens")
                else:
                    logging.warning("[KotakOrderService] NeoAPI initialized but missing TRADING_TOKEN/SID")
            else:
                logging.warning("[KotakOrderService] Missing ACCESS_TOKEN, cannot init NeoAPI")
        except Exception as e:
            logging.error(f"[KotakOrderService] Failed to init NeoAPI: {e}")

    def authenticate(self) -> bool:
        """
        Authenticate with Kotak Neo API.
        Currently relies on pre-generated tokens in env.
        TODO: Implement full login flow if needed.
        """
        if self.client and self.client.configuration.edit_token:
            return True
        return False
    
    def verify_credentials(self) -> Dict[str, Any]:
        """Verify credentials sufficiency"""
        result = {
            'valid': True,
            'missing': [],
            'validation_errors': [],
            'suggestions': []
        }
        
        if not self.access_token:
            result['missing'].append("KOTAK_ACCESS_TOKEN")
            result['valid'] = False
            
        if not self.trading_token:
            result['missing'].append("KOTAK_TRADING_TOKEN")
            result['valid'] = False
            
        return result

    def place_order(self, tradingsymbol: str, transaction_type: str, price: float,
                   quantity: int, order_type: str = 'MKT', product_type: str = 'MIS',
                   exchange_segment: str = 'nse_fo', trigger_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Place an order in Kotak Neo trading platform using neo_api_client.
        """
        try:
            if not self.client:
                self._init_client()
                if not self.client:
                    return {'success': False, 'error': 'NeoAPI client not initialized'}

            # Map transaction type: Kotak expects 'B'/'Buy' or 'S'/'Sell'
            neo_txn_type = 'B' if transaction_type.upper() in ['B', 'BUY'] else 'S'
            
            logging.info(f"[Kotak] Placing Order: {tradingsymbol} {neo_txn_type} {quantity} @ {price} (Trg: {trigger_price})")
            
            # Prepare arguments for NeoAPI
            order_params = {
                'exchange_segment': exchange_segment,
                'product': product_type,
                'price': str(price) if price else "0",
                'order_type': order_type,
                'quantity': str(quantity),
                'validity': 'DAY',
                'trading_symbol': tradingsymbol,
                'transaction_type': neo_txn_type
            }
            
            if trigger_price:
                order_params['trigger_price'] = str(trigger_price)
            
            response = self.client.place_order(**order_params)
            
            logging.info(f"[Kotak] Response: {response}")
            
            # NeoAPI returns a dict/response. Check for success.
            # Response format usually: {'nOrdNo': '...', 'stat': 'Ok'} or similar
            # Based on library code, it returns whatever `order_placing` returns.
            
            if isinstance(response, dict) and 'nOrdNo' in response:
                order_id = response.get('nOrdNo')
                return {
                    'success': True,
                    'order_id': order_id,
                    'response': str(response)
                }
            elif isinstance(response, dict) and 'Error' in response:
                 return {
                    'success': False,
                    'error': str(response.get('Error')),
                    'response': str(response)
                }
            elif isinstance(response, dict) and 'stat' in response and response['stat'] == 'Ok':
                 # Sometimes format might differ
                 return {
                    'success': True,
                    'order_id': response.get('nOrdNo', 'Unknown'),
                    'response': str(response)
                }
            elif isinstance(response, dict) and 'message' in response:
                # E.g., {'code': '900901', 'message': 'Invalid Credentials', 'description': '...'}
                return {
                    'success': False,
                    'error': response.get('description') or response.get('message', 'API Error'),
                    'response': str(response)
                }
            
            # If response is an Exception object (like ApiValueError), stringify it!
            if isinstance(response, Exception):
                return {'success': False, 'error': str(response), 'response': str(response)}
            
            return {'success': False, 'error': f'Unknown response format: {str(response)}', 'response': str(response)}

        except Exception as e:
            logging.error(f"❌ Exception placing order for {tradingsymbol}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': tradingsymbol,
                'exception': type(e).__name__
            }
    
    def place_option_order(self, symbol: str, strike: int, option_type: str,
                          transaction_type: str, quantity: int,
                          sl_price: Optional[float] = None,
                          target_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Place an options order in Kotak Neo platform.
        """
        try:
            # Construct Kotak Neo option symbol format
            # Example: NIFTY24JAN25000CE
            tradingsymbol = self._build_option_symbol(symbol, strike, option_type)
            
            if not tradingsymbol:
                return {'success': False, 'error': 'Symbol construction failed'}
            
            result = self.place_order(
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                price=0.0,
                quantity=quantity,
                order_type=self.ORDER_TYPE_MARKET,
                product_type=self.PRODUCT_NRML, # Using NRML as per previous default
                exchange_segment=self.EXCHANGE_NFO
            )
            
            return result
            
        except Exception as e:
            logging.error(f"[place_option_order] Error: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def modify_order(self, order_id: str, price: Optional[float] = None,
                    quantity: Optional[int] = None, trigger_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Modify an existing order in Kotak Neo.
        
        Args:
            order_id: Order ID to modify
            price: New price (for limit orders)
            quantity: New quantity
            trigger_price: New trigger price
            
        Returns:
            Dict with modification status
        """
        try:
            if not self.client:
                self._init_client()
                if not self.client:
                    return {'success': False, 'error': 'NeoAPI client not initialized'}
            
            # NeoAPI's modify_order expects arguments
            # Note: We need to pass order_type if we want to change it, or it might default
            # Assuming we are modifying price/trigger_price of existing order type
            
            modify_params = {
                'order_id': order_id,
                'order_type': 'MKT', # Default fallback, ideally should know original
                'validity': 'DAY'
            }
            
            if price is not None:
                modify_params['price'] = str(price)
                modify_params['order_type'] = 'L' # Ensure it's Limit if price is sent
            
            if quantity is not None:
                modify_params['quantity'] = str(quantity)
                
            if trigger_price is not None:
                modify_params['trigger_price'] = str(trigger_price)
                if not price:
                    # If trigger but no price, assume SL-M
                    modify_params['order_type'] = 'SL-M' 
            
            modify_response = self.client.modify_order(**modify_params)
            
            logging.info(f"[Kotak] Modify Order Response: {modify_response}")
            
            if isinstance(modify_response, dict) and ('nOrdNo' in modify_response or modify_response.get('stat') == 'Ok'):
                logging.info(f"✅ Order {order_id} modified successfully")
                return {
                    'success': True,
                    'order_id': order_id,
                    'message': 'Order modified successfully',
                    'data': modify_response
                }
            
            return {
                'success': False,
                'order_id': order_id,
                'error': 'Order modification failed',
                'data': modify_response
            }
            
        except Exception as e:
            logging.error(f"[modify_order] Error: {e}", exc_info=True)
            return {
                'success': False,
                'order_id': order_id,
                'error': str(e)
            }
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order"""
        try:
            if not self.client:
                self._init_client()
            
            response = self.client.cancel_order(order_id=order_id)
            logging.info(f"[Kotak] Cancel Response: {response}")
            
            if isinstance(response, dict) and ('nOrdNo' in response or response.get('stat') == 'Ok'):
                return {'success': True, 'response': response}
            
            return {'success': False, 'error': 'Cancellation failed', 'response': response}
        except Exception as e:
             return {'success': False, 'error': str(e)}

    def _build_option_symbol(self, symbol: str, strike: int, option_type: str) -> str:
        """
        Construct Kotak Neo option symbol.
        Format: SYMBOL + YY + MMM + STRIKE + TYPE
        Example: NIFTY24JAN21500CE
        """
        try:
            now = datetime.now()
            year = now.strftime('%y')     # 24
            month = now.strftime('%b').upper() # JAN
            
            return f"{symbol}{year}{month}{strike}{option_type}"
        except Exception as e:
            logging.error(f"Error building symbol: {e}")
            return ""
            
    def _get_option_price(self, symbol: str) -> float:
        """Mock/stub for price fetching - not critical for Market orders"""
        return 0.0

    def place_stoploss_order(self, symbol: str, trigger_price: float, 
                            quantity: int, product_type: str = 'MIS') -> Dict[str, Any]:
        """
        Place a Stop Loss (SL-M) order in Kotak Neo.
        This is a Sell order usually for exiting Long positions.
        """
        try:
            # We need to construct the trading symbol if 'symbol' is just "NIFTY" etc
            # But the caller (live_signal) passes 'NIFTY' and expects us to handle it?
            # Wait, live_signal passes `symbol=self.symbol` (e.g. NIFTY) and `strike`, `side`.
            # But THIS method signature `place_stoploss_order(symbol, ...)` usually expects the FULL TRADING SYMBOL
            # because Fyers/Dhan use unique IDs or full symbols.
            # However, looking at live_signal calls:
            # `service.place_stoploss_order(security_id=..., ...)` for Dhan
            # `service.place_stoploss_order(symbol=fyers_symbol, ...)` for Fyers
            
            # So Kotak service needs to match what live_signal will call, OR live_signal needs to adapt.
            # live_signal currently has a `pass` for Kotak.
            # I will implement `place_option_stoploss_order` helper or adapt `place_stoploss_order` to take strike/side if needed.
            
            # BUT: Consistent interface is better.
            # Dhan/Fyers services take specific ID/Symbol.
            # Kotak service `place_order` takes `tradingsymbol`.
            # So `place_stoploss_order` should probably take `tradingsymbol`?
            # Or I can add `place_option_stoploss_order` like `place_option_order`?
            
            # Let's stick to `place_stoploss_order` taking a `tradingsymbol`.
            # If the caller doesn't have it, they should use `place_option_stoploss_order` (which I will add).
            
            # Actually, let's just use `place_order` directly in the caller if we have the symbol.
            # But for now, let's implement `place_stoploss_order` to wrap `place_order` with SL-M.
            
            return self.place_order(
                tradingsymbol=symbol,
                transaction_type='S', # SL usually Sell
                price=0.0,
                quantity=quantity,
                order_type='SL-M',
                product_type=product_type,
                trigger_price=trigger_price,
                exchange_segment='nse_fo' # Defaulting to FO
            )
            
        except Exception as e:
            logging.error(f"[Kotak] Place SL Error: {e}")
            return {'success': False, 'error': str(e)}
            
    def place_option_stoploss_order(self, symbol: str, strike: int, option_type: str,
                                   trigger_price: float, quantity: int,
                                   transaction_type: str = 'S') -> Dict[str, Any]:
        """
        Helper to place SL-M for an option contract by constructing symbol.
        """
        try:
            tradingsymbol = self._build_option_symbol(symbol, strike, option_type)
            if not tradingsymbol:
                return {'success': False, 'error': 'Symbol build failed'}
                
            return self.place_order(
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                price=0.0,
                quantity=quantity,
                order_type='SL-M',
                product_type=self.PRODUCT_NRML, # Match place_option_order product
                trigger_price=trigger_price,
                exchange_segment=self.EXCHANGE_NFO
            )
        except Exception as e:
            return {'success': False, 'error': str(e)}
