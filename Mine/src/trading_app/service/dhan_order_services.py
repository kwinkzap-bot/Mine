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
                    
                    # CRITICAL: Filter for WEEKLY expiries only (W), exclude MONTHLY (M)
                    # This uses Dhan's official EXPIRY_FLAG column
                    # Reference: https://dhanhq.co/docs/v2/instruments/#column-description
                    expiry_flag = row.get('SEM_EXPIRY_FLAG', '').strip()  # W for Weekly, M for Monthly
                    if expiry_flag != 'W':  # Only accept Weekly expiries
                        continue
                    
                    # Get trading symbols
                    dhan_symbol = row.get('SEM_TRADING_SYMBOL', '').strip()  # NIFTY-Mar2026-25550-CE
                    display_name = row.get('SEM_CUSTOM_SYMBOL', '').strip()  # NIFTY 02 MAR 25550 CALL
                    
                    if not dhan_symbol:
                        continue
                    
                    # Extract underlying symbol (e.g., "NIFTY" from "NIFTY-Mar2026-25550-CE")
                    # Get from SM_SYMBOL_NAME or extract from dhan_symbol
                    symbol_name = row.get('SM_SYMBOL_NAME', '').strip()
                    underlying = symbol_name.split()[0] if symbol_name else 'NIFTY'  # Default to NIFTY
                    
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
                            
                            # CRITICAL: Also store with proper spacing format used in CSV lookups
                            # Dhan CSV format: "NIFTY 02MAR26 25550 CE" (with spaces)
                            strike_clean = strike.split('.')[0] if strike else ''
                            # Build: NIFTY 02MAR26 25550 CE
                            spaced_format = f"{underlying} {day}{month_short}{year_short} {strike_clean} {opt_type}"
                            if spaced_format:
                                symbol_dict[spaced_format] = security_id
                                logging.debug(f"Stored spaced format: {spaced_format} -> {security_id}")
                                    
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
            # Validate credentials FIRST before any API call
            if not self.access_token:
                logging.error("[place_order] Missing access_token. Set DHAN_ACCESS_TOKEN in environment.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing access_token'
                }
            
            if not self.client_id:
                logging.error("[place_order] Missing client_id. Set DHAN_CLIENT_ID in environment.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing client_id'
                }
            
            # Validate token format (should be non-empty string)
            if not isinstance(self.access_token, str) or len(self.access_token.strip()) < 10:
                logging.error(f"[place_order] Invalid access_token format: {type(self.access_token)}")
                return {
                    'success': False,
                    'error': 'Invalid access_token format'
                }
            
            # Validate client_id format
            if not isinstance(self.client_id, str) or len(self.client_id.strip()) == 0:
                logging.error(f"[place_order] Invalid client_id format: {type(self.client_id)}")
                return {
                    'success': False,
                    'error': 'Invalid client_id format'
                }
            
            # Validate SL order has trigger_price
            order_type_upper = order_type.upper()
            if 'STOP_LOSS' in order_type_upper and (not trigger_price or float(trigger_price) <= 0):
                logging.error(f"[place_order] SL order requires trigger_price > 0, got: {trigger_price}")
                return {
                    'success': False,
                    'error': f'Stop loss order requires trigger_price > 0, got: {trigger_price}'
                }
            
            order_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            url = f"{self.base_url}/orders"
            headers = {
                "access-token": self.access_token.strip(),
                "Content-Type": "application/json"
            }
            
            # Normalize order type for consistency
            is_stop_loss = 'STOP_LOSS' in order_type_upper
            is_stop_loss_market = order_type_upper == 'STOP_LOSS_MARKET'
            is_limit = order_type_upper == 'LIMIT'
            
            # Determine price field:
            # - LIMIT: use provided price
            # - STOP_LOSS: use trigger_price as limit price (or provided price if given)
            # - STOP_LOSS_MARKET: price should be 0
            # - MARKET: price should be 0
            if is_limit:
                order_price = str(float(price)) if price and float(price) > 0 else "0"
            elif is_stop_loss and not is_stop_loss_market:
                # For STOP_LOSS (limit), use price if provided, otherwise use trigger_price
                order_price = str(float(price)) if price and float(price) > 0 else str(float(trigger_price))
            else:
                order_price = "0"
            
            # Determine trigger price (required for all SL orders)
            order_trigger_price = str(float(trigger_price)) if is_stop_loss and trigger_price and float(trigger_price) > 0 else "0"
            
            payload = {
                "dhanClientId": self.client_id.strip(),
                "transactionType": transaction_type.upper(),
                "exchangeSegment": exchange_segment,
                "productType": product_type,
                "orderType": order_type_upper,
                "validity": validity,
                "securityId": str(security_id),
                "quantity": str(int(quantity)),
                "price": order_price,
                "triggerPrice": order_trigger_price,
                "disclosedQuantity": str(int(disclosed_quantity)) if disclosed_quantity > 0 else "0",
                "afterMarketOrder": False
            }
            
            if correlation_id:
                payload["correlationId"] = correlation_id.strip()
            
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

                # Auto-renew and retry once for token-expiry/auth failures
                auth_error = (
                    response.status_code in (401, 403)
                    or any(k in str(error_msg).lower() for k in ['invalid', 'expired', 'token', 'unauthorized'])
                )
                if auth_error:
                    # First try syncing client_id from profile if token is valid but client_id is stale
                    logging.warning(f"[place_order] Auth error detected ({error_msg}). Trying profile verification/client sync...")
                    if self.verify_credentials():
                        headers["access-token"] = self.access_token.strip()
                        payload["dhanClientId"] = self.client_id.strip() if isinstance(self.client_id, str) else payload.get("dhanClientId", "")
                        retry_resp = requests.post(url, headers=headers, json=payload, timeout=30)
                        retry_data = retry_resp.json()
                        logging.info(f"[place_order] Verify+retry response ({retry_resp.status_code}): {retry_data}")

                        if retry_resp.status_code in (200, 201):
                            order_id = retry_data.get('orderId')
                            order_status = retry_data.get('orderStatus')
                            logging.info(f"✅ {order_time} Dhan Order placed successfully after client sync. Order ID: {order_id}")
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

                        error_msg = retry_data.get('errorMessage') or retry_data.get('message') or error_msg
                        data = retry_data

                    logging.warning(f"[place_order] Auth error detected ({error_msg}). Trying token renewal...")
                    if self.renew_token():
                        headers["access-token"] = self.access_token.strip()
                        payload["dhanClientId"] = self.client_id.strip() if isinstance(self.client_id, str) else payload.get("dhanClientId", "")
                        retry_resp = requests.post(url, headers=headers, json=payload, timeout=30)
                        retry_data = retry_resp.json()
                        logging.info(f"[place_order] Retry response ({retry_resp.status_code}): {retry_data}")

                        if retry_resp.status_code in (200, 201):
                            order_id = retry_data.get('orderId')
                            order_status = retry_data.get('orderStatus')
                            logging.info(f"✅ {order_time} Dhan Order placed successfully after token renewal. Order ID: {order_id}")
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

                        error_msg = retry_data.get('errorMessage') or retry_data.get('message') or error_msg
                        data = retry_data

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
                            exchange_segment: str = 'NSE_FNO', price: float = 0.0,
                            entry_price: float = 0.0) -> Dict[str, Any]:
        """
        Place a stop loss (sell) order on Dhan platform.
        
        Creates a sell order with a trigger price that automatically executes
        when the price drops to the trigger level.
        
        Args:
            security_id: Exchange standard ID for the scrip
            trigger_price: SL trigger price (price at which SL is activated)
            quantity: Order quantity
            product_type: 'INTRADAY', 'CNC', 'MARGIN', etc. (default: INTRADAY)
            exchange_segment: 'NSE_EQ', 'NSE_FNO', 'BSE_EQ', 'BSE_FNO' (default: NSE_FNO)
            price: Execution price (optional, used for STOP_LOSS limit orders)
            entry_price: Entry price for calculating SL limit price
            
        Returns:
            Dict with success status, order_id, and details
        """
        try:
            # Validate credentials FIRST before any API call
            if not self.access_token:
                logging.error("[place_stoploss_order] Missing access_token. Set DHAN_ACCESS_TOKEN in environment.")
                return {'success': False, 'error': 'Missing access_token - SL order cannot be placed'}
            
            if not self.client_id:
                logging.error("[place_stoploss_order] Missing client_id. Set DHAN_CLIENT_ID in environment.")
                return {'success': False, 'error': 'Missing client_id - SL order cannot be placed'}
            
            # Validate token format
            if not isinstance(self.access_token, str) or len(self.access_token.strip()) < 10:
                logging.error(f"[place_stoploss_order] Invalid access_token format")
                return {'success': False, 'error': 'Invalid access_token format'}
            
            order_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            url = f"{self.base_url}/orders"
            headers = {
                "access-token": self.access_token.strip(),
                "Content-Type": "application/json"
            }
            
            # Use STOP_LOSS with limit price equal to trigger price
            # When price drops to trigger_price, sell at trigger_price or better
            # Example: Entry 200 → SL trigger 180 → sell at 180 or better
            limit_price = trigger_price
            
            payload = {
                "dhanClientId": self.client_id.strip(),
                "transactionType": self.TRANSACTION_SELL,
                "exchangeSegment": exchange_segment,
                "productType": product_type,
                "orderType": self.ORDER_TYPE_STOP_LOSS,  # STOP_LOSS with limit price
                "validity": "DAY",
                "securityId": str(security_id),
                "quantity": str(int(quantity)),
                "price": str(float(limit_price)),  # Limit price when trigger activates
                "triggerPrice": str(float(trigger_price)),  # Trigger price to activate the order
                "disclosedQuantity": "0",
                "afterMarketOrder": False
            }
            
            logging.info(f"[place_stoploss_order] Placing SL order: security_id={security_id}, "
                        f"trigger_price={trigger_price:.2f}, limit_price={limit_price:.4f}, "
                        f"qty={quantity}, order_type=STOP_LOSS")
            logging.info(f"[place_stoploss_order] Full payload: {payload}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            data = response.json()
            
            logging.info(f"[place_stoploss_order] Response status: {response.status_code}, data: {data}")
            
            if response.status_code == 200 or response.status_code == 201:
                # Check both possible response structures
                order_id = data.get('orderId') or data.get('data', {}).get('orderId')
                if order_id:
                    logging.info(f"✅ SL Order placed: {order_id} | {security_id} @ Trigger {trigger_price:.2f}, Limit {limit_price:.4f}")
                    return {
                        'success': True,
                        'order_id': order_id,
                        'symbol': security_id,
                        'trigger_price': trigger_price,
                        'limit_price': limit_price,
                        'quantity': quantity,
                        'order_type': 'STOP_LOSS'
                    }
            
            error_msg = data.get('errorMessage') or data.get('message') or data.get('error') or 'Unknown error'

            # Auto-renew and retry once for token-expiry/auth failures
            auth_error = (
                response.status_code in (401, 403)
                or any(k in str(error_msg).lower() for k in ['invalid', 'expired', 'token', 'unauthorized'])
            )
            if auth_error:
                # First try syncing client_id from profile if token is valid but client_id is stale
                logging.warning(f"[place_stoploss_order] Auth error detected ({error_msg}). Trying profile verification/client sync...")
                if self.verify_credentials():
                    headers["access-token"] = self.access_token.strip()
                    payload["dhanClientId"] = self.client_id.strip() if isinstance(self.client_id, str) else payload.get("dhanClientId", "")
                    retry_resp = requests.post(url, headers=headers, json=payload, timeout=30)
                    retry_data = retry_resp.json()
                    logging.info(f"[place_stoploss_order] Verify+retry response ({retry_resp.status_code}): {retry_data}")

                    if retry_resp.status_code in (200, 201):
                        order_id = retry_data.get('orderId') or retry_data.get('data', {}).get('orderId')
                        if order_id:
                            logging.info(f"✅ SL Order placed after client sync: {order_id}")
                            return {
                                'success': True,
                                'order_id': order_id,
                                'symbol': security_id,
                                'trigger_price': trigger_price,
                                'limit_price': limit_price,
                                'quantity': quantity,
                                'order_type': 'STOP_LOSS'
                            }

                    error_msg = retry_data.get('errorMessage') or retry_data.get('message') or retry_data.get('error') or error_msg
                    data = retry_data

                logging.warning(f"[place_stoploss_order] Auth error detected ({error_msg}). Trying token renewal...")
                if self.renew_token():
                    headers["access-token"] = self.access_token.strip()
                    payload["dhanClientId"] = self.client_id.strip() if isinstance(self.client_id, str) else payload.get("dhanClientId", "")
                    retry_resp = requests.post(url, headers=headers, json=payload, timeout=30)
                    retry_data = retry_resp.json()
                    logging.info(f"[place_stoploss_order] Retry response ({retry_resp.status_code}): {retry_data}")

                    if retry_resp.status_code in (200, 201):
                        order_id = retry_data.get('orderId') or retry_data.get('data', {}).get('orderId')
                        if order_id:
                            logging.info(f"✅ SL Order placed after token renewal: {order_id}")
                            return {
                                'success': True,
                                'order_id': order_id,
                                'symbol': security_id,
                                'trigger_price': trigger_price,
                                'limit_price': limit_price,
                                'quantity': quantity,
                                'order_type': 'STOP_LOSS'
                            }

                    error_msg = retry_data.get('errorMessage') or retry_data.get('message') or retry_data.get('error') or error_msg
                    data = retry_data

            logging.error(f"❌ SL Order failed: {error_msg}")
            logging.error(f"Full response: {data}")
            return {'success': False, 'error': error_msg, 'symbol': security_id, 'response': data}
            
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
    
    def get_nearest_weekly_expiry(self, symbol: str = 'NIFTY') -> 'datetime':
        """
        Get the current active expiry date for NSE index options using Kite API.
        
        Fetches the actual available expiry from Kite's instruments data rather than hardcoding.
        This ensures we always get the correct current/next expiry date dynamically.
        
        Args:
            symbol: Symbol like 'NIFTY', 'BANKNIFTY', etc. (default: 'NIFTY')
        
        Returns:
            datetime object of the active expiry for trading
        """
        try:
            from datetime import datetime, timedelta
            import pandas as pd
            
            # Try to use Kite API if available
            try:
                from trading_app.service.kite_order_services import KiteService
                kite_service = KiteService()
                kite = kite_service.kite
                
                if kite:
                    # Fetch all instruments
                    instruments = kite.instruments()
                    df = pd.DataFrame(instruments)
                    
                    # Filter for the specified symbol options
                    symbol_options = df[
                        (df['name'] == symbol) & 
                        (df['segment'] == 'NFO-OPT')
                    ]
                    
                    if not symbol_options.empty:
                        # Get the NEXT expiry (skip current day if it's expiry day)
                        today = datetime.now().date()
                        future_expiries = symbol_options[
                            symbol_options['expiry'].apply(
                                lambda x: (x.date() if hasattr(x, 'date') else x) > today
                            )
                        ]
                        
                        if not future_expiries.empty:
                            nearest_expiry = future_expiries['expiry'].min()
                            if hasattr(nearest_expiry, 'date'):
                                nearest_expiry = nearest_expiry.date()
                            logging.info(f"[get_nearest_weekly_expiry] Fetched from Kite API - Symbol: {symbol}, Expiry: {nearest_expiry}")
                            return datetime.combine(nearest_expiry, datetime.min.time())
                        else:
                            # No future expiries, use minimum available
                            nearest_expiry = symbol_options['expiry'].min()
                            if hasattr(nearest_expiry, 'date'):
                                nearest_expiry = nearest_expiry.date()
                            logging.info(f"[get_nearest_weekly_expiry] Using current expiry (no future) - Expiry: {nearest_expiry}")
                            return datetime.combine(nearest_expiry, datetime.min.time())
                    
            except Exception as e:
                logging.debug(f"[get_nearest_weekly_expiry] Could not fetch from Kite API: {e}. Using fallback.")
            
            # Fallback: use hardcoded expiry calendar
            today = datetime.now()
            current_date = today.date()
            
            special_expiries = [
                datetime(2026, 2, 26).date(),
                datetime(2026, 3, 2).date(),
                datetime(2026, 3, 5).date(),
                datetime(2026, 3, 12).date(),
                datetime(2026, 3, 19).date(),
                datetime(2026, 3, 26).date(),
            ]
            
            # Prefer weekly expiries (exclude 24th which is monthly)
            weekly_expiries = [exp for exp in special_expiries if exp.day != 24]
            
            nearest_expiry = None
            for exp_date in weekly_expiries:
                if exp_date > current_date:
                    nearest_expiry = exp_date
                    break
            
            # If no weekly expiry found, fall back to all expiries (including monthly)
            if not nearest_expiry:
                for exp_date in special_expiries:
                    if exp_date > current_date:
                        nearest_expiry = exp_date
                        break
            
            if nearest_expiry:
                result = datetime.combine(nearest_expiry, datetime.min.time())
                logging.info(f"[get_nearest_weekly_expiry] Using hardcoded expiry: {nearest_expiry}")
                return result
            
            # Last fallback: next Thursday
            days_until_thursday = (3 - today.weekday()) % 7
            if days_until_thursday == 0:
                days_until_thursday = 7
            
            return today + timedelta(days=days_until_thursday)
            
        except Exception as e:
            logging.error(f"[get_nearest_weekly_expiry] Error: {e}. Using today as fallback.")
            from datetime import datetime
            return datetime.now()
    
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
                # Format: YYMMM (e.g., "26FEB" for Feb 2026, "26MAR" for Mar 2026)
                year_yy = expiry_date.strftime('%y')
                month_text = expiry_date.strftime('%b').upper()
                expiry_str = f"{year_yy}{month_text}"
            else:
                # Get nearest weekly expiry (current or next Thursday)
                nearest_expiry = self.get_nearest_weekly_expiry(symbol)
                if nearest_expiry:
                    # Format: YYMMM (e.g., "26FEB" for Feb 2026, "26MAR" for Mar 2026)
                    year_yy = nearest_expiry.strftime('%y')
                    month_text = nearest_expiry.strftime('%b').upper()
                    expiry_str = f"{year_yy}{month_text}"
                else:
                    # Fallback to current date if calculation fails
                    from datetime import datetime
                    now = datetime.now()
                    year_yy = now.strftime('%y')
                    month_text = now.strftime('%b').upper()
                    expiry_str = f"{year_yy}{month_text}"
                    logging.warning(f"[get_option_security_id] Failed to get nearest expiry, using current date: {expiry_str}")
            
            # Try using trading symbol format as security ID
            # Format: NIFTY26FEB25500CE (SYMBOL + YYMMM + STRIKE + TYPE)
            # This matches the Kite trading symbol format exactly
            trading_symbol = f"{symbol}{expiry_str}{strike}{option_type}"
            
            logging.info(f"[get_option_security_id] Using weekly expiry format - Symbol: {symbol}, Expiry: {expiry_str}, Strike: {strike}, Type: {option_type} -> {trading_symbol}")
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
                year_yy = year  # e.g., '26'
                month_abbr = month_text.upper()  # e.g., 'FEB' -> 'FEB'
                # Need to get the day from this expiry month to format correctly
                # First get the date from our expiry calculation
                nearest_expiry = self.get_nearest_weekly_expiry(underlying)
                day = nearest_expiry.strftime('%d')  # e.g., '02'
                # Correct Dhan format: NIFTY02MAR2625550CE (DDMMMYY format)
                dhan_symbol = f"{underlying}{day}{month_abbr}{year_yy}{strike}{opt_type}"
                # Also create spaced format for CSV lookup: NIFTY 02MAR26 25550 CE
                dhan_symbol_spaced = f"{underlying} {day}{month_abbr}{year_yy} {strike} {opt_type}"
                
                logging.info(f"[search_symbol] Format1 (TextMonth): {symbol} → {dhan_symbol_spaced}")
                
                # Try to find in master (use spaced format which matches CSV)
                if self._symbol_master_data and dhan_symbol_spaced in self._symbol_master_data:
                    security_id = self._symbol_master_data[dhan_symbol_spaced]
                    return {'success': True, 'security_id': security_id, 'symbol': dhan_symbol_spaced}
                
                # Also try non-spaced format
                if self._symbol_master_data and dhan_symbol in self._symbol_master_data:
                    security_id = self._symbol_master_data[dhan_symbol]
                    return {'success': True, 'security_id': security_id, 'symbol': dhan_symbol}
                
                return {'success': False, 'security_id': dhan_symbol_spaced, 'symbol': dhan_symbol_spaced}
            
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
                    # Use get_nearest_weekly_expiry to get the correct expiry
                    nearest_expiry = self.get_nearest_weekly_expiry(underlying)
                    day = nearest_expiry.strftime('%d')  # e.g., '02'
                    month_abbr = nearest_expiry.strftime('%b').upper()  # e.g., 'MAR'
                    year_yy = nearest_expiry.strftime('%y')  # e.g., '26'
                    # Correct Dhan format with spaces: NIFTY 02MAR26 25550 CE
                    dhan_symbol = f"{underlying} {day}{month_abbr}{year_yy} {strike} {opt_type}"
                    
                    logging.info(f"[search_symbol] Format2 (NumericDate): {symbol}")
                    logging.info(f"  Extracted: underlying={underlying}, year={year}, strike={strike}, type={opt_type}")
                    logging.info(f"  Using dynamic expiry: {day}{month_abbr}{year_yy}")
                    logging.info(f"  Converted to Dhan format: {dhan_symbol}")
                    
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
