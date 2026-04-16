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
import threading

load_dotenv()

_global_dhan_sym_cache = {
    'data': None,
    'date': None,
    'lock': threading.Lock()
}

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
        Download and cache Dhan's symbol master per segment using DhanHQ API.
        Creates a combined dict mapping trading_symbol -> security_id for fast lookups.
        
        Uses segment-wise API (as per DhanHQ docs):
        https://api.dhan.co/v2/instrument/{exchangeSegment}
        
        Valid Segments: NSE_FNO, NSE_EQ, IDX_I, MCX_COMM, BSE_FNO
        """
        try:
            global _global_dhan_sym_cache
            today_date = datetime.now().date()
            
            with _global_dhan_sym_cache['lock']:
                if _global_dhan_sym_cache['data'] is not None and _global_dhan_sym_cache['date'] == today_date:
                    self._symbol_master_data = _global_dhan_sym_cache['data']
                    return
            
            # We primarily need FNO for options, IDX_I for indices, and NSE_EQ for equities
            segments = ['NSE_FNO', 'IDX_I', 'NSE_EQ']
            self._symbol_master_data = {}
            cache_dir = Path(tempfile.gettempdir())
            
            for segment in segments:
                cache_file = cache_dir / f"dhan_scrip_master_{segment.lower()}.csv"
                
                # Check if cached file is recent (less than 24 hours old)
                recent_cache = False
                if cache_file.exists():
                    file_age = datetime.now().timestamp() - cache_file.stat().st_mtime
                    if file_age < 86400:  # 24 hours
                        recent_cache = True
                
                if recent_cache:
                    logging.info(f"[_build_symbol_master_dict] Using cached Dhan scrip master for {segment}")
                    segment_data = self._load_csv_to_dict(cache_file, segment)
                    if segment_data:
                        self._symbol_master_data.update(segment_data)
                    continue

                # Fetch from DhanHQ API (requires access_token)
                if not self.access_token:
                    logging.warning(f"[_build_symbol_master_dict] No access token for {segment} fetch, checking old cache...")
                    if cache_file.exists():
                        segment_data = self._load_csv_to_dict(cache_file, segment)
                        self._symbol_master_data.update(segment_data)
                    continue

                logging.info(f"[_build_symbol_master_dict] Fetching {segment} from DhanHQ API...")
                url = f"https://api.dhan.co/v2/instrument/{segment}"
                headers = {
                    "access-token": self.access_token,
                    "Content-Type": "application/json"
                }
                
                try:
                    response = requests.get(url, headers=headers, timeout=60)
                    if response.status_code == 200:
                        # The response is CSV text
                        with open(cache_file, 'w', encoding='utf-8') as f:
                            f.write(response.text)
                        
                        segment_data = self._load_csv_to_dict(cache_file, segment)
                        if segment_data:
                            self._symbol_master_data.update(segment_data)
                            logging.info(f"[_build_symbol_master_dict] ✓ Loaded {len(segment_data)} symbols from {segment}")
                    else:
                        logging.warning(f"[_build_symbol_master_dict] API {segment} failed ({response.status_code}). Using old cache if exists.")
                        if cache_file.exists():
                            segment_data = self._load_csv_to_dict(cache_file, segment)
                            self._symbol_master_data.update(segment_data)
                except Exception as api_err:
                    logging.error(f"[_build_symbol_master_dict] API Error for {segment}: {api_err}")
                    if cache_file.exists():
                        segment_data = self._load_csv_to_dict(cache_file, segment)
                        self._symbol_master_data.update(segment_data)
            
            # Set memory cache after successfully building the dictionary
            with _global_dhan_sym_cache['lock']:
                _global_dhan_sym_cache['data'] = self._symbol_master_data
                _global_dhan_sym_cache['date'] = today_date
            
            logging.info(f"[_build_symbol_master_dict] Total symbols in master: {len(self._symbol_master_data)}")
                
        except Exception as e:
            logging.warning(f"[_build_symbol_master_dict] Unexpected error building symbol master: {e}")
            if getattr(self, '_symbol_master_data', None) is None:
                self._symbol_master_data = {}
    
    def _load_csv_to_dict(self, csv_file: Path, segment_name: str = "") -> Dict[str, str]:
        """
        Load CSV segment file and create lookup dicts for symbol mapping.
        
        Dhan CSV columns typically include:
            SEM_EXM_EXCH_ID: Exchange (NSE, BSE, MCX)
            SEM_SEGMENT: Segment (C=Currency, D=Derivatives, E=Equity, M=Commodity, I=Index)
            SEM_SMST_SECURITY_ID: Numeric security ID
            SEM_TRADING_SYMBOL: Trading symbol (e.g., NIFTY-Mar2026-25550-CE)
            ...
        """
        try:
            symbol_dict = {}
            is_derivative_segment = segment_name == "NSE_FNO" or "fno" in str(csv_file).lower()
            
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Extract security ID
                    security_id = row.get('SEM_SMST_SECURITY_ID') or row.get('SECURITY_ID', '')
                    security_id = security_id.strip()
                    if not security_id or not security_id.isdigit():
                        continue
                    
                    # Process Trading Symbol (Primary Key)
                    dhan_symbol = row.get('SEM_TRADING_SYMBOL') or row.get('SYMBOL_NAME', '')
                    dhan_symbol = dhan_symbol.strip()
                    if dhan_symbol:
                        symbol_dict[dhan_symbol] = security_id
                    
                    # Process Custom/Display Symbol
                    display_name = row.get('SEM_CUSTOM_SYMBOL') or row.get('DISPLAY_NAME', '')
                    display_name = display_name.strip()
                    if display_name:
                        symbol_dict[display_name] = security_id

                    # If no trading symbol, try Instrument Name (some API returns this)
                    if not dhan_symbol:
                        instrument_name = row.get('SEM_INSTRUMENT_NAME') or row.get('INSTRUMENT', '')
                        instrument_name = instrument_name.strip()
                        if instrument_name:
                            symbol_dict[instrument_name] = security_id
                            dhan_symbol = instrument_name

                    # For NSE Derivatives (Options), generate Kite-style formats for fallback
                    seg = row.get('SEM_SEGMENT') or row.get('SEGMENT', '')
                    seg = seg.strip()
                    
                    # If it's the FNO segment file, assume it's derivative even if SEM_SEGMENT flag is missing
                    if seg == 'D' or is_derivative_segment:
                        # Expiry flag filter for weekly/monthly (skip unknown if flag exists)
                        expiry_flag = row.get('SEM_EXPIRY_FLAG') or row.get('EXPIRY_FLAG', '')
                        expiry_flag = expiry_flag.strip()
                        if expiry_flag and expiry_flag not in ('W', 'M', 'D'): # D for Derivatives
                            pass # Still continue if it looks like an option

                        # Extract underlying and option info
                        symbol_name = row.get('SM_SYMBOL_NAME') or row.get('UNDERLYING_SYMBOL') or row.get('SEM_INSTRUMENT_NAME') or row.get('INSTRUMENT', '')
                        symbol_name = symbol_name.strip()
                        underlying = symbol_name.split()[0] if symbol_name else 'NIFTY'
                        if '-' in underlying: # Handle NIFTY-MAR-2026
                            underlying = underlying.split('-')[0]
                            
                        strike = row.get('SEM_STRIKE_PRICE') or row.get('STRIKE_PRICE', '')
                        strike = strike.strip()
                        
                        opt_type = row.get('SEM_OPTION_TYPE') or row.get('OPTION_TYPE', '')
                        opt_type = opt_type.strip()
                        
                        expiry_date = row.get('SEM_EXPIRY_DATE') or row.get('SM_EXPIRY_DATE', '')
                        expiry_date = expiry_date.strip()

                        if strike and opt_type and expiry_date:
                            try:
                                # Parse date format: 2026-03-02 or 02-MAR-2026
                                date_str = expiry_date.split()[0]
                                if '-' in date_str:
                                    parts = date_str.split('-')
                                    if len(parts[0]) == 4: # YYYY-MM-DD
                                        exp_dt = datetime.strptime(date_str, '%Y-%m-%d')
                                    else: # DD-MM-YYYY or DD-MON-YYYY
                                        try:
                                            exp_dt = datetime.strptime(date_str, '%d-%b-%Y')
                                        except:
                                            exp_dt = datetime.strptime(date_str, '%d-%m-%Y')
                                else:
                                    exp_dt = datetime.strptime(date_str, '%Y%m%d')

                                year_short = exp_dt.strftime('%y')
                                month_short = exp_dt.strftime('%b').upper()
                                day = exp_dt.strftime('%d')
                                strike_clean = strike.split('.')[0]

                                # 1. Kite short text: NIFTY26FEB25550CE (Overwrites weeklies, used for monthlies/guess)
                                kite_text = f"{underlying}{year_short}{month_short}{strike_clean}{opt_type}"
                                symbol_dict[kite_text] = security_id

                                # 2. Spaced: NIFTY 02MAR26 25550 CE
                                spaced = f"{underlying} {day}{month_short}{year_short} {strike_clean} {opt_type}"
                                symbol_dict[spaced] = security_id
                                
                                # 3. Full Kite exact: NIFTY02MAR2625550CE
                                full_kite = f"{underlying}{day}{month_short}{year_short}{strike_clean}{opt_type}"
                                symbol_dict[full_kite] = security_id
                                
                            except Exception:
                                pass
            
            return symbol_dict
            
        except Exception as e:
            logging.error(f"[_load_csv_to_dict] Error parsing {csv_file}: {e}")
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
            # - STOP_LOSS_MARKET: price should be 0.0
            # - MARKET: price should be 0.0
            if is_limit:
                order_price = float(price) if price and float(price) > 0 else 0.0
            elif is_stop_loss and not is_stop_loss_market:
                # For STOP_LOSS (limit), use price if provided, otherwise use trigger_price
                order_price = float(price) if price and float(price) > 0 else float(trigger_price)
            else:
                order_price = 0.0
            
            # Determine trigger price (required for all SL orders)
            order_trigger_price = float(trigger_price) if is_stop_loss and trigger_price and float(trigger_price) > 0 else 0.0
            
            # CRITICAL: Dhan V2 API requires specific string formatting.
            # From official docs (https://dhanhq.co/docs/v2/orders/):
            #   - quantity, price, triggerPrice, disclosedQuantity are ALL strings
            #   - Empty string "" for fields that are not applicable
            #   - MARKET orders: price="" and triggerPrice=""
            #   - STOP_LOSS_MARKET: price="0", triggerPrice="<value>"
            #   - STOP_LOSS (limit): price="<limit>", triggerPrice="<trigger>"
            #   - LIMIT: price="<value>", triggerPrice=""
            
            # Format price string based on order type
            if is_stop_loss_market:
                price_str = "0"
            elif is_limit or (is_stop_loss and not is_stop_loss_market):
                price_str = str(order_price)
            else:
                # MARKET order
                price_str = ""
            
            # Format trigger price string
            trigger_str = str(order_trigger_price) if is_stop_loss and order_trigger_price > 0 else ""
            
            payload = {
                "dhanClientId": str(self.client_id).strip(),
                "transactionType": transaction_type.upper(),
                "exchangeSegment": exchange_segment,
                "productType": product_type,
                "orderType": order_type_upper,
                "validity": validity,
                "securityId": str(security_id),
                "quantity": str(int(quantity)),
                "price": price_str,
                "triggerPrice": trigger_str,
                "disclosedQuantity": "",
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
                "dhanClientId": str(self.client_id).strip(),
                "orderId": order_id,
                "validity": validity
            }
            
            if quantity is not None:
                payload["quantity"] = str(int(quantity))
            if price is not None:
                payload["price"] = str(float(price))
            if order_type is not None:
                payload["orderType"] = order_type
            if trigger_price is not None:
                payload["triggerPrice"] = str(float(trigger_price))
            
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
        Wraps place_order with specific SL logic.
        """
        try:
            # Determine best SL order type:
            # If price is 0, use STOP_LOSS_MARKET (safer for fast moves)
            # If price > 0, use STOP_LOSS (limit)
            if price and float(price) > 0:
                final_order_type = self.ORDER_TYPE_STOP_LOSS
                final_price = float(price)
            else:
                # Default to SL-Market for better reliability in options
                final_order_type = self.ORDER_TYPE_STOP_LOSS_MARKET
                final_price = 0.0
                
            logging.info(f"[place_stoploss_order] Preparing {final_order_type}: sec_id={security_id}, "
                        f"trigger={trigger_price:.2f}, qty={quantity}")
            
            # Delegate to main place_order for full auth/retry support
            return self.place_order(
                security_id=str(security_id),
                transaction_type=self.TRANSACTION_SELL,
                quantity=int(quantity),
                order_type=final_order_type,
                product_type=product_type,
                exchange_segment=exchange_segment,
                price=final_price,
                trigger_price=float(trigger_price)
            )
                
        except Exception as e:
            logging.error(f"❌ Exception in place_stoploss_order wrapper: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
    
    
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
                "dhanClientId": str(self.client_id).strip(),
                "orderId": order_id,
                "triggerPrice": str(float(new_trigger_price))
            }
            
            if quantity is not None:
                payload["quantity"] = str(int(quantity))
            
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
                
                symbol_options = kite_service.get_nfo_instruments(symbol)
                
                if symbol_options:
                    today = datetime.now().date()
                    future_expiries = []
                    
                    for opt in symbol_options:
                        exp = opt.get('expiry')
                        if exp:
                            if hasattr(exp, 'date'):
                                exp_date = exp.date()
                            elif isinstance(exp, str):
                                try:
                                    exp_date_str = exp.split('T')[0]
                                    exp_date = datetime.fromisoformat(exp_date_str).date()
                                except Exception: continue
                            else:
                                continue
                                
                            if exp_date > today:
                                future_expiries.append(exp_date)
                                
                    if future_expiries:
                        nearest_expiry = min(future_expiries)
                        logging.info(f"[get_nearest_weekly_expiry] Fetched from Kite Memory - Symbol: {symbol}, Expiry: {nearest_expiry}")
                        return datetime.combine(nearest_expiry, datetime.min.time())
                    
                    # If no strictly future expiries, try to find today's expiry
                    today_expiries = []
                    for opt in symbol_options:
                        exp = opt.get('expiry')
                        if exp:
                            if hasattr(exp, 'date'): exp_date = exp.date()
                            elif isinstance(exp, str):
                                try: exp_date = datetime.fromisoformat(exp.split('T')[0]).date()
                                except Exception: continue
                            else: continue
                            if exp_date == today:
                                today_expiries.append(exp_date)
                    if today_expiries:
                        logging.info(f"[get_nearest_weekly_expiry] Using current expiry - Expiry: {today}")
                        return datetime.combine(today, datetime.min.time())
            except Exception as e:
                logging.debug(f"[get_nearest_weekly_expiry] Could not fetch from Kite Memory: {e}. Using fallback.")
            
            # Fallback: use hardcoded expiry calendar
            today = datetime.now()
            current_date = today.date()
            
            # March 2026 Expiries (including Tuesdays for FINNIFTY)
            special_expiries = [
                datetime(2026, 2, 26).date(), # Thu
                datetime(2026, 3, 3).date(),  # Tue
                datetime(2026, 3, 5).date(),  # Thu
                datetime(2026, 3, 10).date(), # Tue
                datetime(2026, 3, 12).date(), # Thu
                datetime(2026, 3, 17).date(), # Tue
                datetime(2026, 3, 19).date(), # Thu
                datetime(2026, 3, 24).date(), # Tue
                datetime(2026, 3, 26).date(), # Thu
            ]
            
            # Select relevant expiries based on index type
            is_tuesday_index = any(s in symbol.upper() for s in ['FINNIFTY', 'FIN SERVICE'])
            
            # Target day: 1=Tuesday, 3=Thursday
            target_weekday = 1 if is_tuesday_index else 3
            
            # Filter the list for candidates
            candidates = [exp for exp in special_expiries if exp.weekday() == target_weekday]
            if not candidates:
                candidates = special_expiries
                
            nearest_expiry = None
            for exp_date in sorted(candidates):
                if exp_date >= current_date: # Include today if market hours
                    nearest_expiry = exp_date
                    break
            
            if nearest_expiry:
                result = datetime.combine(nearest_expiry, datetime.min.time())
                logging.info(f"[get_nearest_weekly_expiry] Using hardcoded expiry for {symbol}: {nearest_expiry}")
                return result
            
            # Last fallback: calculate next target day
            days_until = (target_weekday - today.weekday()) % 7
            if days_until == 0 and today.hour >= 15: # If expiry day after 3 PM, move to next week
                days_until = 7
            
            return today + timedelta(days=days_until)
            
        except Exception as e:
            logging.error(f"[get_nearest_weekly_expiry] Error: {e}. Using today as fallback.")
            from datetime import datetime
            return datetime.now()
    
    def get_option_security_id(self, symbol: str, strike: int, option_type: str, expiry_date=None) -> str:
        """
        Get the numeric security ID for an option on Dhan.

        Looks up the Dhan scrip master CSV (downloaded at init) using the
        Dhan trading symbol format: SYMBOL-MonthYEAR-STRIKE-TYPE
        (e.g., NIFTY-Mar2026-25500-CE) and returns the numeric SEM_SMST_SECURITY_ID.

        Falls back to search_symbol() which also queries the CSV, then last-resort
        returns an empty string so the caller can log the failure cleanly.
        """
        try:
            from datetime import datetime

            # ---- Step 1: determine the target expiry date ----
            if expiry_date is None:
                expiry_date = self.get_nearest_weekly_expiry(symbol)

            if expiry_date is None:
                logging.warning("[get_option_security_id] Could not determine expiry, giving up.")
                return ""

            # ---- Step 2: build Dhan CSV trading symbol format ----
            # Weekly format:  NIFTY-11Mar26-23950-PE
            # Monthly format: NIFTY-Mar2026-23950-PE
            day_str    = expiry_date.strftime('%d')          # '10'
            month_name = expiry_date.strftime('%b')          # 'Mar'
            year_full  = expiry_date.strftime('%Y')          # '2026'
            year_short = expiry_date.strftime('%y')          # '26'
            strike_str = str(int(strike))                    # '25500'
            
            # Construct all possible formats used by Dhan in their CSV masters
            # 1. SYMBOL-DDMMMYY-STRIKE-TYPE (Weekly Hyphenated)
            dhan_sym_weekly  = f"{symbol}-{day_str}{month_name}{year_short}-{strike_str}-{option_type}"
            # 2. SYMBOL-MMMYYYY-STRIKE-TYPE (Monthly Hyphenated)
            dhan_sym_monthly = f"{symbol}-{month_name}{year_full}-{strike_str}-{option_type}"
            # 3. SYMBOL DDMMMYY STRIKE OPTION (Standard Spaced)
            dhan_sym_spaced = f"{symbol} {day_str}{month_name.upper()}{year_short} {strike_str} {option_type}"
            # 4. SYMBOL MMMYY STRIKE OPTION (Monthly Spaced)
            dhan_sym_monthly_spaced = f"{symbol} {month_name.upper()}{year_short} {strike_str} {option_type}"
            # 5. SYMBOL DD-MMM-YY STRIKE OPTION (Alternative Spaced)
            dhan_sym_alt = f"{symbol} {day_str}-{month_name}-{year_short} {strike_str} {option_type}"

            logging.info(f"[get_option_security_id] Looking up: {dhan_sym_weekly} or {dhan_sym_monthly}")

            # ---- Step 3: direct lookup in master dict ----
            if self._symbol_master_data:
                # Try all common formats
                sec_id = (self._symbol_master_data.get(dhan_sym_weekly) or 
                         self._symbol_master_data.get(dhan_sym_monthly) or
                         self._symbol_master_data.get(dhan_sym_spaced) or
                         self._symbol_master_data.get(dhan_sym_monthly_spaced) or
                         self._symbol_master_data.get(dhan_sym_alt))
                
                if sec_id:
                    logging.info(f"[get_option_security_id] Match found in master dict: {sec_id}")
                    return sec_id

                # Brute-force search on key fields in case format slightly differs
                # MUST include day check to avoid matching wrong weekly/monthly contract
                for key, val in self._symbol_master_data.items():
                    # Check if all components match
                    if (key.startswith(symbol) and
                            str(int(strike)) in key and
                            option_type in key and
                            month_name in key and
                            day_str in key):
                        logging.info(f"[get_option_security_id] Fallback match: {key} -> {val}")
                        return val

            # ---- Step 4: try search_symbol() which also does CSV lookup ----
            # Build a Kite-format symbol to pass to search_symbol
            year_yy     = expiry_date.strftime('%y')
            month_upper = month_name.upper()
            kite_sym    = f"{symbol}{year_yy}{month_upper}{strike_str}{option_type}"
            result = self.search_symbol(kite_sym)
            if result.get('success') and result.get('security_id', '').isdigit():
                sec_id = result['security_id']
                logging.info(f"[get_option_security_id] search_symbol found: {kite_sym} -> {sec_id}")
                return sec_id

            logging.error(
                f"[get_option_security_id] Could not find security_id for "
                f"{symbol} {strike} {option_type} expiry={expiry_date}. "
                f"Master size={len(self._symbol_master_data) if self._symbol_master_data else 0}"
            )
            return ""

        except Exception as e:
            logging.error(f"[get_option_security_id] Error: {e}", exc_info=True)
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
