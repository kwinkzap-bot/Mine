import logging
import os
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List
from datetime import datetime
import requests
import csv
from io import StringIO
import tempfile
from pathlib import Path

load_dotenv()

class DhanOrderService:
    """
    Service for placing orders in Dhan trading platform using REST API.
    Handles order placement, execution, and tracking for options and futures.
    
    Official Docs: https://dhanhq.co/docs/v2/
    """
    
    def __init__(self, access_token: Optional[str] = None, client_id: Optional[str] = None):
        """
        Initialize DhanOrderService with Dhan credentials.
        
        Args:
            access_token: Dhan API access token (JWT token, 24 hour validity)
            client_id: Dhan Client ID (User specific identification)
        
        How to get credentials:
            1. Login to web.dhan.co
            2. Click on My Profile → 'Access DhanHQ APIs'
            3. Generate "Access Token" (24 hour validity)
            4. Client ID is your Dhan user ID
        """
        self.access_token = access_token or os.getenv("DHAN_ACCESS_TOKEN")
        self.client_id = client_id or os.getenv("DHAN_CLIENT_ID")
        
        self.base_url = "https://api.dhan.co/v2"
        self.last_error = None
        
        # CSV master data cache
        self._symbol_master_data = None
        self._symbol_master_cache_path = None
        self._build_symbol_master_dict()
        
        logging.info("[DhanOrderService] Initialized with Dhan credentials")
        
        # Order type mappings for Dhan
        self.ORDER_TYPE_MARKET = 'MARKET'
        self.ORDER_TYPE_LIMIT = 'LIMIT'
        self.ORDER_TYPE_STOP_LOSS = 'STOP_LOSS'
        self.ORDER_TYPE_STOP_LOSS_MARKET = 'STOP_LOSS_MARKET'
        
        # Transaction type mappings
        self.TRANSACTION_BUY = 'BUY'
        self.TRANSACTION_SELL = 'SELL'
        
        # Product type mappings for Dhan
        self.PRODUCT_CNC = 'CNC'  # Cash and Carry (Delivery)
        self.PRODUCT_INTRADAY = 'INTRADAY'  # Intraday
        self.PRODUCT_MARGIN = 'MARGIN'  # Margin (F&O)
        self.PRODUCT_MTF = 'MTF'  # Margin Trading Facility
        self.PRODUCT_CO = 'CO'  # Cover Order
        self.PRODUCT_BO = 'BO'  # Bracket Order
        
        # Exchange segments
        self.EXCHANGE_NSE_EQ = 'NSE_EQ'  # NSE Equity
        self.EXCHANGE_NSE_FNO = 'NSE_FNO'  # NSE F&O
        self.EXCHANGE_BSE_EQ = 'BSE_EQ'  # BSE Equity
        self.EXCHANGE_BSE_FNO = 'BSE_FNO'  # BSE F&O
        self.EXCHANGE_MCX = 'MCX'  # MCX Commodity
        self.EXCHANGE_NSE_CURRENCY = 'NSE_CURRENCY'  # NSE Currency
    
    def _build_symbol_master_dict(self):
        """
        Download and cache Dhan's symbol master CSV file.
        Creates a dict mapping trading_symbol -> security_id for fast lookups.
        
        Dhan provides static CSV master files at:
        - Compact: https://images.dhan.co/api-data/api-scrip-master.csv
        - Detailed: https://images.dhan.co/api-data/api-scrip-master-detailed.csv
        """
        try:
            # Use temp directory for cache file
            cache_dir = Path(tempfile.gettempdir())
            cache_file = cache_dir / "dhan_scrip_master.csv"
            self._symbol_master_cache_path = cache_file
            
            # Check if cached file is recent (less than 24 hours old)
            if cache_file.exists():
                file_age = datetime.now().timestamp() - cache_file.stat().st_mtime
                if file_age < 86400:  # 24 hours
                    logging.info("[_build_symbol_master_dict] Using cached Dhan scrip master")
                    self._symbol_master_data = self._load_csv_to_dict(cache_file)
                    return
            
            # Download fresh CSV from Dhan
            logging.info("[_build_symbol_master_dict] Downloading Dhan scrip master CSV...")
            csv_url = "https://images.dhan.co/api-data/api-scrip-master.csv"
            
            response = requests.get(csv_url, timeout=30)
            if response.status_code == 200:
                # Save to cache file
                with open(cache_file, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                
                # Parse CSV into dict
                self._symbol_master_data = self._load_csv_to_dict(cache_file)
                logging.info(f"[_build_symbol_master_dict] ✓ Cached {len(self._symbol_master_data)} symbols from Dhan")
            else:
                logging.warning(f"[_build_symbol_master_dict] Failed to download CSV: {response.status_code}")
                self._symbol_master_data = {}
                
        except Exception as e:
            logging.warning(f"[_build_symbol_master_dict] Error building symbol master: {e}")
            self._symbol_master_data = {}
    
    def _load_csv_to_dict(self, csv_file: Path) -> Dict[str, Dict[str, str]]:
        """
        Load CSV file and create lookup dicts for symbol mapping.
        
        Dhan CSV columns:
            SEM_EXM_EXCH_ID: Exchange (NSE, BSE, MCX)
            SEM_SEGMENT: Segment (C=Currency, D=Derivatives, E=Equity, M=Commodity)
            SEM_SMST_SECURITY_ID: Numeric security ID (what we need!)
            SEM_INSTRUMENT_NAME: Instrument type (OPTIDX, FUTIDX, etc)
            SEM_TRADING_SYMBOL: Trading symbol (e.g., NIFTY-Mar2026-25550-CE)
            SEM_CUSTOM_SYMBOL: Display name (e.g., NIFTY 02 MAR 25550 CALL)
            SEM_STRIKE_PRICE: Strike price
            SEM_OPTION_TYPE: CE/PE
            
        Returns:
            Dict mapping Kite-format symbols -> security_id
        """
        try:
            symbol_dict = {}
            kite_format_dict = {}  # Maps Kite format -> Dhan format + security_id
            
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Only process NSE derivatives (options)
                    exch = row.get('SEM_EXM_EXCH_ID', '').strip()
                    segment = row.get('SEM_SEGMENT', '').strip()
                    
                    if exch != 'NSE' or segment != 'D':
                        continue
                    
                    # Extract security ID (numeric)
                    security_id = row.get('SEM_SMST_SECURITY_ID', '').strip()
                    if not security_id or not security_id.isdigit():
                        continue
                    
                    # Get trading symbols
                    dhan_symbol = row.get('SEM_TRADING_SYMBOL', '').strip()  # NIFTY-Mar2026-25550-CE
                    display_name = row.get('SEM_CUSTOM_SYMBOL', '').strip()  # NIFTY 02 MAR 25550 CALL
                    
                    if not dhan_symbol:
                        continue
                    
                    # Extract option details for Kite format conversion
                    strike = row.get('SEM_STRIKE_PRICE', '0').strip()
                    opt_type = row.get('SEM_OPTION_TYPE', '').strip()  # CE or PE
                    expiry_date = row.get('SEM_EXPIRY_DATE', '').strip()
                    
                    # Try to build Kite format: NIFTY26FEB25550CE, NIFTY2630225550CE, etc
                    if strike and opt_type and expiry_date:
                        # Parse expiry date: "2026-03-02 14:30:00" -> "26FEB" or "2630225"
                        try:
                            exp_dt = datetime.strptime(expiry_date.split()[0], '%Y-%m-%d')
                            # Two common Kite formats:
                            # 1. Text month: NIFTY26FEB25550CE (year+month+strike+type)
                            # 2. Numeric: NIFTY2630225550CE (year+month+date+strike+type)
                            
                            year_short = exp_dt.strftime('%y')
                            month_short = exp_dt.strftime('%b').upper()
                            day = exp_dt.strftime('%d')
                            
                            kite_text_format = f"NIFTY{year_short}{month_short}{strike.split('.')[0]}{opt_type}"
                            kite_numeric_format = f"NIFTY{year_short}{month_short.replace('JAN','01').replace('FEB','02').replace('MAR','03').replace('APR','04').replace('MAY','05').replace('JUN','06').replace('JUL','07').replace('AUG','08').replace('SEP','09').replace('OCT','10').replace('NOV','11').replace('DEC','12')}{day}{strike.split('.')[0]}{opt_type}"
                            
                            # Store both formats
                            for fmt in [kite_text_format, kite_numeric_format, dhan_symbol]:
                                if fmt:
                                    symbol_dict[fmt] = security_id
                                    
                        except Exception as e:
                            logging.debug(f"Could not convert expiry for {dhan_symbol}: {e}")
                    
                    # Also store by direct Dhan symbol format
                    symbol_dict[dhan_symbol] = security_id
                    
                    # Store by display name (e.g., "NIFTY 02 MAR 25550 CALL")
                    if display_name:
                        symbol_dict[display_name] = security_id
            
            logging.info(f"[_load_csv_to_dict] Loaded {len(symbol_dict)} symbol mappings from CSV")
            return symbol_dict
            
        except Exception as e:
            logging.error(f"[_load_csv_to_dict] Error parsing CSV: {e}")
            return {}
    
    def verify_credentials(self) -> bool:
        """
        Verify access token and get user profile.
        This is a test API to validate credentials.
        
        Returns:
            bool: True if credentials are valid, False otherwise
        """
        try:
            if not self.access_token:
                self.last_error = "ACCESS_TOKEN is missing"
                logging.error(f"[verify_credentials] {self.last_error}")
                return False
            
            logging.info("[verify_credentials] Verifying Dhan credentials...")
            
            url = f"{self.base_url}/profile"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = response.json()
            
            logging.info(f"[verify_credentials] Response: {data}")
            
            if response.status_code == 200:
                self.client_id = data.get('dhanClientId')
                token_validity = data.get('tokenValidity')
                active_segments = data.get('activeSegment')
                
                logging.info("[verify_credentials] ✅ Credentials verified!")
                logging.info(f"[verify_credentials] Client ID: {self.client_id}")
                logging.info(f"[verify_credentials] Token Valid Until: {token_validity}")
                logging.info(f"[verify_credentials] Active Segments: {active_segments}")
                
                return True
            else:
                error_msg = data.get('errorMessage') or data.get('message') or 'Unknown error'
                self.last_error = f"Verification failed: {error_msg}"
                logging.error(f"[verify_credentials] {self.last_error}")
                return False
                
        except Exception as e:
            self.last_error = f"Verification error: {str(e)}"
            logging.error(f"[verify_credentials] {self.last_error}")
            import traceback
            logging.error(f"[verify_credentials] Traceback: {traceback.format_exc()}")
            return False
    
    def renew_token(self) -> bool:
        """
        Refresh access token for another 24 hours.
        Only works for tokens generated from Dhan Web.
        
        Returns:
            bool: True if token renewed successfully, False otherwise
        """
        try:
            if not self.access_token or not self.client_id:
                self.last_error = "Missing access_token or client_id"
                logging.error(f"[renew_token] {self.last_error}")
                return False
            
            logging.info("[renew_token] Renewing access token...")
            
            url = f"{self.base_url}/RenewToken"
            headers = {
                "access-token": self.access_token,
                "dhanClientId": self.client_id
            }
            
            response = requests.post(url, headers=headers, timeout=30)
            data = response.json()
            
            logging.info(f"[renew_token] Response: {data}")
            
            if response.status_code == 200:
                new_token = data.get('accessToken')
                if new_token:
                    self.access_token = new_token
                    logging.info("[renew_token] ✅ Token renewed successfully!")
                    logging.info(f"[renew_token] New token: {new_token[:20]}...")
                    
                    # Update .env file
                    self._save_token()
                    return True
                else:
                    self.last_error = "Token renewal failed: No new token received"
                    logging.error(f"[renew_token] {self.last_error}")
                    return False
            else:
                error_msg = data.get('errorMessage') or data.get('message') or 'Unknown error'
                self.last_error = f"Token renewal failed: {error_msg}"
                logging.error(f"[renew_token] {self.last_error}")
                return False
                
        except Exception as e:
            self.last_error = f"Token renewal error: {str(e)}"
            logging.error(f"[renew_token] {self.last_error}")
            return False
    
    def place_order(self, security_id: str, transaction_type: str, quantity: int,
                   order_type: str = 'MARKET', product_type: str = 'INTRADAY',
                   exchange_segment: str = 'NSE_FNO', price: float = 0.0,
                   trigger_price: float = 0.0, validity: str = 'DAY',
                   disclosed_quantity: int = 0, correlation_id: str = '') -> Dict[str, Any]:
        """
        Place an order on Dhan platform.
        
        Args:
            security_id: Exchange standard ID for each scrip (e.g., "11536" for TCS)
            transaction_type: 'BUY' or 'SELL'
            quantity: Number of shares/lots
            order_type: 'MARKET', 'LIMIT', 'STOP_LOSS', 'STOP_LOSS_MARKET'
            product_type: 'CNC', 'INTRADAY', 'MARGIN', 'MTF', 'CO', 'BO'
            exchange_segment: 'NSE_EQ', 'NSE_FNO', 'BSE_EQ', 'BSE_FNO', 'MCX', 'NSE_CURRENCY'
            price: Price at which order is placed (for LIMIT orders)
            trigger_price: Price at which order is triggered (for SL orders)
            validity: 'DAY' or 'IOC'
            disclosed_quantity: Number of shares visible (>30% of quantity if used)
            correlation_id: User-generated ID for tracking
            
        Returns:
            Dict with order details and success status
        """
        try:
            if not self.access_token or not self.client_id:
                logging.error("[place_order] Not authenticated. Call verify_credentials() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing access_token or client_id'
                }
            
            order_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            url = f"{self.base_url}/orders"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            payload = {
                "dhanClientId": self.client_id,
                "transactionType": transaction_type,
                "exchangeSegment": exchange_segment,
                "productType": product_type,
                "orderType": order_type,
                "validity": validity,
                "securityId": security_id,
                "quantity": str(quantity),
                "price": str(price) if order_type == 'LIMIT' else "",
                "triggerPrice": str(trigger_price) if 'STOP_LOSS' in order_type else "",
                "disclosedQuantity": str(disclosed_quantity) if disclosed_quantity > 0 else "",
                "afterMarketOrder": False
            }
            
            if correlation_id:
                payload["correlationId"] = correlation_id
            
            logging.info(f"[place_order] Placing order: {payload}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            data = response.json()
            
            logging.info(f"[place_order] Response ({response.status_code}): {data}")
            
            if response.status_code == 200 or response.status_code == 201:
                order_id = data.get('orderId')
                order_status = data.get('orderStatus')
                
                logging.info(f"✅ {order_time} Dhan Order placed successfully. Order ID: {order_id} | "
                           f"Security ID: {security_id} @ ₹{price} | Qty: {quantity}")
                
                return {
                    'success': True,
                    'order_id': order_id,
                    'order_status': order_status,
                    'security_id': security_id,
                    'price': price,
                    'quantity': quantity,
                    'transaction_type': transaction_type,
                    'order_type': order_type,
                    'product_type': product_type,
                    'timestamp': order_time,
                    'exchange': exchange_segment,
                    'platform': 'DHAN'
                }
            else:
                error_msg = data.get('errorMessage') or data.get('message') or 'Unknown error'
                logging.error(f"❌ {order_time} Order placement failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'security_id': security_id,
                    'response': data
                }
                
        except Exception as e:
            logging.error(f"❌ Exception placing order for {security_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'security_id': security_id,
                'exception': type(e).__name__
            }
    
    def modify_order(self, order_id: str, quantity: Optional[int] = None,
                    price: Optional[float] = None, order_type: Optional[str] = None,
                    trigger_price: Optional[float] = None, validity: str = 'DAY') -> Dict[str, Any]:
        """
        Modify a pending order.
        
        Args:
            order_id: Order ID to modify
            quantity: New quantity (optional)
            price: New price (optional)
            order_type: New order type (optional)
            trigger_price: New trigger price (optional)
            validity: Order validity ('DAY' or 'IOC')
            
        Returns:
            Dict with modification status
        """
        try:
            if not self.access_token or not self.client_id:
                logging.error("[modify_order] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing access_token or client_id'
                }
            
            url = f"{self.base_url}/orders/{order_id}"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            payload = {
                "dhanClientId": self.client_id,
                "orderId": order_id,
                "validity": validity
            }
            
            if quantity is not None:
                payload["quantity"] = str(quantity)
            if price is not None:
                payload["price"] = str(price)
            if order_type is not None:
                payload["orderType"] = order_type
            if trigger_price is not None:
                payload["triggerPrice"] = str(trigger_price)
            
            logging.info(f"[modify_order] Modifying order {order_id}: {payload}")
            
            response = requests.put(url, headers=headers, json=payload, timeout=30)
            data = response.json()
            
            logging.info(f"[modify_order] Response ({response.status_code}): {data}")
            
            if response.status_code == 200:
                logging.info(f"✅ Order {order_id} modified successfully")
                return {
                    'success': True,
                    'order_id': order_id,
                    'order_status': data.get('orderStatus'),
                    'response': data
                }
            else:
                error_msg = data.get('errorMessage') or 'Unknown error'
                logging.error(f"❌ Order modification failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'order_id': order_id,
                    'response': data
                }
                
        except Exception as e:
            logging.error(f"❌ Exception modifying order {order_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'order_id': order_id
            }
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel a pending order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            Dict with cancellation status
        """
        try:
            if not self.access_token:
                logging.error("[cancel_order] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing access_token'
                }
            
            url = f"{self.base_url}/orders/{order_id}"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            logging.info(f"[cancel_order] Cancelling order {order_id}")
            
            response = requests.delete(url, headers=headers, timeout=30)
            data = response.json()
            
            logging.info(f"[cancel_order] Response ({response.status_code}): {data}")
            
            if response.status_code == 200 or response.status_code == 202:
                logging.info(f"✅ Order {order_id} cancelled successfully")
                return {
                    'success': True,
                    'order_id': order_id,
                    'order_status': data.get('orderStatus'),
                    'response': data
                }
            else:
                error_msg = data.get('errorMessage') or 'Unknown error'
                logging.error(f"❌ Order cancellation failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'order_id': order_id
                }
                
        except Exception as e:
            logging.error(f"❌ Exception cancelling order {order_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'order_id': order_id
            }
    
    def place_stoploss_order(self, security_id: str, trigger_price: float, 
                            quantity: int, product_type: str = 'INTRADAY',
                            exchange_segment: str = 'NSE_FNO') -> Dict[str, Any]:
        """
        Place a stop loss (sell) order on Dhan platform.
        
        Creates a sell order with a trigger price that automatically executes
        when the price drops to the trigger level.
        
        Args:
            security_id: Exchange standard ID for the scrip
            trigger_price: SL trigger price
            quantity: Order quantity
            product_type: 'INTRADAY', 'CNC', 'MARGIN', etc.
            exchange_segment: 'NSE_EQ', 'NSE_FNO', 'BSE_EQ', 'BSE_FNO'
            
        Returns:
            Dict with success status, order_id, and details
        """
        try:
            if not self.access_token or not self.client_id:
                self.last_error = "Missing access_token or client_id"
                return {'success': False, 'error': self.last_error}
            
            order_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            url = f"{self.base_url}/orders"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            payload = {
                "dhanClientId": self.client_id,
                "transactionType": self.TRANSACTION_SELL,
                "exchangeSegment": exchange_segment,
                "productType": product_type,
                "orderType": self.ORDER_TYPE_STOP_LOSS,
                "validity": "DAY",
                "securityId": security_id,
                "quantity": str(quantity),
                "price": "",
                "triggerPrice": str(trigger_price),
                "disclosedQuantity": "",
                "afterMarketOrder": False
            }
            
            logging.info(f"[place_stoploss_order] Placing SL order: {security_id} @ {trigger_price:.2f}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            data = response.json()
            
            if response.status_code == 200 or response.status_code == 201:
                order_id = data.get('data', {}).get('orderId')
                if order_id:
                    logging.info(f"✅ SL Order placed: {order_id} | {security_id} @ {trigger_price:.2f}")
                    return {
                        'success': True,
                        'order_id': order_id,
                        'symbol': security_id,
                        'trigger_price': trigger_price,
                        'quantity': quantity,
                        'order_type': 'STOPLOSS'
                    }
            
            error_msg = data.get('errorMessage') or data.get('message') or 'Unknown error'
            logging.error(f"❌ SL Order failed: {error_msg}")
            return {'success': False, 'error': error_msg, 'symbol': security_id}
            
        except Exception as e:
            logging.error(f"❌ Exception placing SL order: {e}", exc_info=True)
            return {'success': False, 'error': str(e), 'symbol': security_id}
    
    def modify_stoploss_order(self, order_id: str, new_trigger_price: float,
                             quantity: Optional[int] = None) -> Dict[str, Any]:
        """
        Modify an existing stop loss order with a new trigger price.
        
        Used for trailing SL - updates the trigger price as price moves favorably.
        
        Args:
            order_id: Order ID to modify
            new_trigger_price: New SL trigger price
            quantity: Optional quantity update
            
        Returns:
            Dict with success status and details
        """
        try:
            if not self.access_token or not self.client_id:
                self.last_error = "Missing access_token or client_id"
                return {'success': False, 'error': self.last_error}
            
            url = f"{self.base_url}/orders/{order_id}"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            payload = {
                "dhanClientId": self.client_id,
                "orderId": order_id,
                "triggerPrice": str(new_trigger_price)
            }
            
            if quantity is not None:
                payload["quantity"] = str(quantity)
            
            logging.info(f"[modify_stoploss_order] Modifying SL order {order_id} to {new_trigger_price:.2f}")
            
            response = requests.put(url, headers=headers, json=payload, timeout=30)
            data = response.json()
            
            if response.status_code == 200:
                logging.info(f"✅ SL Order modified: {order_id} -> Trigger: {new_trigger_price:.2f}")
                return {
                    'success': True,
                    'order_id': order_id,
                    'new_trigger_price': new_trigger_price
                }
            
            error_msg = data.get('errorMessage') or 'Modification failed'
            logging.error(f"❌ Modify failed: {error_msg}")
            return {'success': False, 'error': error_msg, 'order_id': order_id}
            
        except Exception as e:
            logging.error(f"❌ Exception modifying SL order: {e}", exc_info=True)
            return {'success': False, 'error': str(e), 'order_id': order_id}
    
    def get_order_book(self) -> Dict[str, Any]:
        """
        Get all orders for the day.
        
        Returns:
            Dict with list of orders
        """
        try:
            if not self.access_token:
                logging.error("[get_order_book] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated'
                }
            
            url = f"{self.base_url}/orders"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = response.json()
            
            if response.status_code == 200:
                logging.info(f"✅ Retrieved {len(data)} orders")
                return {
                    'success': True,
                    'orders': data
                }
            else:
                error_msg = data.get('errorMessage') or 'Unknown error'
                logging.error(f"❌ Failed to retrieve orders: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            logging.error(f"❌ Exception getting order book: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_positions(self) -> Dict[str, Any]:
        """
        Get all positions for the day.
        
        Returns:
            Dict with list of positions
        """
        try:
            if not self.access_token:
                logging.error("[get_positions] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated'
                }
            
            url = f"{self.base_url}/positions"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = response.json()
            
            if response.status_code == 200:
                logging.info(f"✅ Retrieved positions")
                return {
                    'success': True,
                    'positions': data
                }
            else:
                error_msg = data.get('errorMessage') or 'Unknown error'
                logging.error(f"❌ Failed to retrieve positions: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            logging.error(f"❌ Exception getting positions: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_holdings(self) -> Dict[str, Any]:
        """
        Get all holdings.
        
        Returns:
            Dict with list of holdings
        """
        try:
            if not self.access_token:
                logging.error("[get_holdings] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated'
                }
            
            url = f"{self.base_url}/holdings"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = response.json()
            
            if response.status_code == 200:
                logging.info(f"✅ Retrieved holdings")
                return {
                    'success': True,
                    'holdings': data
                }
            else:
                error_msg = data.get('errorMessage') or 'Unknown error'
                logging.error(f"❌ Failed to retrieve holdings: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            logging.error(f"❌ Exception getting holdings: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_funds(self) -> Dict[str, Any]:
        """
        Get fund limits.
        
        Returns:
            Dict with fund information
        """
        try:
            if not self.access_token:
                logging.error("[get_funds] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated'
                }
            
            url = f"{self.base_url}/fundlimit"
            headers = {
                "access-token": self.access_token,
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = response.json()
            
            if response.status_code == 200:
                logging.info(f"✅ Retrieved fund limits")
                return {
                    'success': True,
                    'funds': data
                }
            else:
                error_msg = data.get('errorMessage') or 'Unknown error'
                logging.error(f"❌ Failed to retrieve funds: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            logging.error(f"❌ Exception getting funds: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_lot_size(self, symbol: str) -> int:
        """
        Get the lot size (quantity multiplier) for a symbol.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            
        Returns:
            Lot size (default: 1 if not found)
        """
        # Default lot sizes for Indian options as of Jan 2026
        default_lots = {
            'NIFTY': 65,
            'BANKNIFTY': 25,
            'FINNIFTY': 40,
            'MIDCPNIFTY': 50,
            'SENSEX': 10,
            'BANKEX': 15
        }
        
        try:
            lot_size = default_lots.get(symbol, 1)
            logging.info(f"✓ Lot size for {symbol}: {lot_size}")
            return lot_size
            
        except Exception as e:
            logging.error(f"Error getting lot size for {symbol}: {e}")
            return default_lots.get(symbol, 1)
    
    def get_option_security_id(self, symbol: str, strike: int, option_type: str, expiry_date=None) -> str:
        """
        Get the numeric security ID for an option on Dhan.
        
        Dhan API requires numeric security IDs for options. This method either:
        1. Fetches from Dhan's symbol master API (if available), or
        2. Constructs security ID based on known mappings
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            expiry_date: Optional expiry date object
            
        Returns:
            Numeric security ID as string (e.g., "29926000" for NIFTY options)
        """
        try:
            # Dhan symbol master mapping for current expiry options
            # These are base security IDs; actual ID depends on expiry and strike
            # Format: For options, the security ID is constructed from base + strike offset
            
            symbol_base_map = {
                'NIFTY': 29926000,      # NIFTY options base ID
                'BANKNIFTY': 29945008,  # BANKNIFTY options base ID
                'FINNIFTY': 29974001,   # FINNIFTY options base ID
                'MIDCPNIFTY': 99926000, # MIDCPNIFTY options base ID
                'SENSEX': 99900000,     # SENSEX options base ID
                'BANKEX': 99900100,     # BANKEX options base ID
            }
            
            if symbol not in symbol_base_map:
                logging.warning(f"[get_option_security_id] Unknown symbol: {symbol}")
                # Fallback: use symbol name + strike
                return f"{symbol}{strike}{option_type}"
            
            # Get base ID for the symbol
            base_id = symbol_base_map[symbol]
            
            # For Dhan, we may need to query their symbol master
            # For now, we'll use the trading symbol format as it's more reliable
            # Dhan sometimes accepts the symbol string directly
            from datetime import datetime
            
            if expiry_date:
                expiry_str = expiry_date.strftime('%y%b').upper()
            else:
                now = datetime.now()
                expiry_str = now.strftime('%y%b').upper()
            
            # Try using trading symbol format as security ID
            trading_symbol = f"{symbol}{expiry_str}{strike}{option_type}"
            
            logging.info(f"[get_option_security_id] Mapping {symbol} {strike} {option_type} -> {trading_symbol}")
            return trading_symbol
            
        except Exception as e:
            logging.error(f"[get_option_security_id] Error: {e}")
            return ""
    
    def search_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Convert Kite symbol to Dhan format.
        
        Handles multiple Kite symbol formats:
        - NIFTY26FEB25550CE (UNDERLYING + YY + MONTH_TEXT + STRIKE + TYPE)
        - NIFTY2630225550CE (UNDERLYING + YY + ?? + STRIKE + TYPE)
        
        Converts to Dhan format: NIFTY-Mar2026-25550-CE
        
        Args:
            symbol: Kite trading symbol
            
        Returns:
            Dict with dhan_symbol (formatted for Dhan API)
        """
        try:
            logging.info(f"[search_symbol] Converting Kite symbol: {symbol}")
            
            # Try CSV lookup first if available
            if self._symbol_master_data and symbol in self._symbol_master_data:
                security_id = self._symbol_master_data[symbol]
                logging.info(f"✓ Found in CSV: {symbol} -> {security_id}")
                return {
                    'success': True,
                    'security_id': security_id,
                    'symbol': symbol
                }
            
            import re
            
            # Format 1: NIFTY26FEB25550CE (UNDERLYING + YY + MONTH_TEXT + STRIKE + TYPE)
            match1 = re.match(r'([A-Z]+?)(\d{2})([A-Z]{3})(\d+)([CP]E)', symbol)
            if match1:
                underlying, year, month_text, strike, opt_type = match1.groups()
                year_full = f"20{year}"
                month_abbr = month_text.capitalize()
                dhan_symbol = f"{underlying}-{month_abbr}{year_full}-{strike}-{opt_type}"
                
                logging.info(f"[search_symbol] Format1 (TextMonth): {symbol} → {dhan_symbol}")
                
                # Try to find in master
                if self._symbol_master_data and dhan_symbol in self._symbol_master_data:
                    security_id = self._symbol_master_data[dhan_symbol]
                    return {'success': True, 'security_id': security_id, 'symbol': dhan_symbol}
                
                return {'success': False, 'security_id': dhan_symbol, 'symbol': dhan_symbol}
            
            # Format 2: NIFTY2630225550CE (UNDERLYING + MYSTERY_DIGITS + STRIKE + TYPE)
            # Extract: UNDERLYING + YY at start, TYPE at end (CE/PE)
            # For the middle part, try to intelligently extract strike
            match2 = re.match(r'([A-Z]+?)(\d{2})(\d+)([CP]E)', symbol)
            if match2:
                underlying, year, middle_and_strike, opt_type = match2.groups()
                year_full = f"20{year}"
                
                # Try to extract strike - assume last 4 or 5 digits
                # For NIFTY2630225550CE: try 25550 (5 digits)
                strike = None
                for strike_len in [5, 4]:
                    if len(middle_and_strike) > strike_len:
                        candidate = middle_and_strike[-strike_len:]
                        if candidate.isdigit() and int(candidate) >= 100:  # Valid strike
                            strike = candidate
                            break
                
                if strike:
                    # Default to March (most liquid monthly expiry)
                    month_abbr = "Mar"
                    dhan_symbol = f"{underlying}-{month_abbr}{year_full}-{strike}-{opt_type}"
                    
                    logging.info(f"[search_symbol] Format2 (NumericDate): {symbol}")
                    logging.info(f"  Extracted: underlying={underlying}, year={year}, strike={strike}, type={opt_type}")
                    logging.info(f"  Converted (Mar assumed): {dhan_symbol}")
                    
                    # Try to find in master
                    if self._symbol_master_data and dhan_symbol in self._symbol_master_data:
                        security_id = self._symbol_master_data[dhan_symbol]
                        return {'success': True, 'security_id': security_id, 'symbol': dhan_symbol}
                    
                    return {'success': False, 'security_id': dhan_symbol, 'symbol': dhan_symbol}
            
            # Last resort: Return as-is with dashes added
            logging.warning(f"[search_symbol] Could not parse {symbol}, returning as-is")
            return {
                'success': False,
                'security_id': symbol,
                'symbol': symbol
            }
            
        except Exception as e:
            logging.error(f"[search_symbol] Error: {e}", exc_info=True)
            return {'success': False, 'error': str(e), 'symbol': symbol}
    
    def _symbols_match(self, symbol1: str, symbol2: str) -> bool:
        """
        Check if two trading symbols represent the same instrument.
        Handles different formatting variations (NIFTY26FEB25600CE vs NIFTY-26-FEB-25600CE).
        """
        # Normalize both symbols by removing common separators
        norm1 = symbol1.replace('-', '').replace('_', '').upper()
        norm2 = symbol2.replace('-', '').replace('_', '').upper()
        
        # Exact match after normalization
        if norm1 == norm2:
            return True
        
        # Partial match for base components
        # Extract: underlying (NIFTY/BANKNIFTY), expiry (26FEB), strike (25600), type (CE/PE)
        import re
        match1 = re.match(r'([A-Z]+?)(\d{2}[A-Z]{3})(\d+)([CP]E)', norm1)
        match2 = re.match(r'([A-Z]+?)(\d{2}[A-Z]{3})(\d+)([CP]E)', norm2)
        
        if match1 and match2:
            # Compare all components (underlying, expiry, strike, type)
            return match1.groups() == match2.groups()
        
        return False
    
    def get_option_symbol(self, symbol: str, strike: int, option_type: str) -> str:
        """
        Get the trading symbol for an option (legacy method).
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            
        Returns:
            Trading symbol string
        """
        try:
            # Get current month and year
            from datetime import datetime
            now = datetime.now()
            year = now.strftime('%y')
            month = now.strftime('%b').upper()
            
            # Format for reference: NIFTY26FEB25600CE
            option_symbol = f"{symbol}{year}{month}{strike}{option_type}"
            logging.info(f"✓ Option symbol: {option_symbol}")
            return option_symbol
            
        except Exception as e:
            logging.error(f"Error getting option symbol: {e}")
            return ""
    
    def _get_lot_size_fallback(self) -> Dict[str, int]:
        """Fallback lot sizes"""
        return {
            'NIFTY': 65,
            'BANKNIFTY': 25,
            'FINNIFTY': 40,
            'MIDCPNIFTY': 50,
            'SENSEX': 10,
            'BANKEX': 15
        }
    
    def _save_token(self):
        """Save access token to .env file"""
        try:
            env_path = os.path.join(os.path.dirname(__file__), '../../../.env')
            
            with open(env_path, 'r') as f:
                content = f.read()
            
            # Update access token
            if 'DHAN_ACCESS_TOKEN=' in content:
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if line.startswith('DHAN_ACCESS_TOKEN='):
                        lines[i] = f'DHAN_ACCESS_TOKEN={self.access_token}'
                content = '\n'.join(lines)
            else:
                content += f'\nDHAN_ACCESS_TOKEN={self.access_token}'
            
            with open(env_path, 'w') as f:
                f.write(content)
            
            logging.info("[_save_token] Saved access token to .env")
        except Exception as e:
            logging.warning(f"[_save_token] Failed to save: {e}")
