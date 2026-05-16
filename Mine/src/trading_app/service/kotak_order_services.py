import logging
import os
import json as json_lib
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List, TYPE_CHECKING
from datetime import datetime, date
import requests
import pyotp
from neo_api_client import NeoAPI

if TYPE_CHECKING:
    # Hint Pylance for import resolution during type checking
    from trading_app.app.utils.kite_helper import get_kite  # type: ignore

load_dotenv()

class KotakOrderService:
    """
    Service for placing orders in Kotak Neo trading platform using neo_api_client.
    Handles order placement, execution, and tracking for options and futures.
    
    Official Docs: https://www.notion.so/Getting-started-15-min-28eda70d37e280a09158f091b369561e
    """
    
    def __init__(self, mobile_number: Optional[str] = None, ucc: Optional[str] = None, 
                 mpin: Optional[str] = None, totp_secret: Optional[str] = None,
                 consumer_key: Optional[str] = None, consumer_secret: Optional[str] = None,
                 access_token: Optional[str] = None):
        """
        Initialize KotakOrderService with Kotak Neo credentials.
        
        Official Docs: https://github.com/Kotak-Neo/Kotak-neo-api-v2
        
        v2 flow (consumer_key only, no consumer_secret needed):
        1. Initialize with consumer_key (no session_init)
        2. totp_login(mobile, ucc, totp) → view_token
        3. totp_validate(mpin) → edit_token (trading token)
        """
        self.mobile_number = mobile_number or os.getenv("KOTAK_MOBILE_NUMBER")
        self.ucc = ucc or os.getenv("KOTAK_UCC") or os.getenv("KOTAK_CLIENT_ID")
        self.mpin = mpin or os.getenv("KOTAK_MPIN") or os.getenv("KOTAK_PASSWORD")
        self.totp_secret = totp_secret or os.getenv("KOTAK_TOTP_SECRET")
        
        # Consumer key for v2 auth (preferred method — no consumer_secret needed per v2 docs)
        self.consumer_key = consumer_key or os.getenv("KOTAK_CONSUMER_KEY")
        
        # Legacy: consumer_secret / access_token (kept for backward compatibility)
        self.consumer_secret = consumer_secret or os.getenv("KOTAK_CONSUMER_SECRET")
        self.access_token = access_token or os.getenv("KOTAK_ACCESS_TOKEN")
        
        # Authentication tokens (from 2-step auth in env or generated)
        self.trading_token = os.getenv("KOTAK_TRADING_TOKEN")
        self.trading_sid = os.getenv("KOTAK_TRADING_SID")
        self.server_id = os.getenv("KOTAK_SERVER_ID")
        self.base_url = os.getenv("KOTAK_BASE_URL", "")
        self._order_base_url = self.base_url or None  # Used by place_order() for direct HTTP
        
        # v2 auth intermediate tokens
        self._view_token = None
        self._sid = None
        
        # Kotak API gateway base URL for auth (per v2 SDK source: urls.py BASE_URL)
        self._auth_base_url = "https://mis.kotaksecurities.com/"
        self._neo_fin_key = "neotradeapi"
        
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
        """Initialize NeoAPI client for order operations.
        
        Per the official Kotak Neo API v2 docs (https://github.com/Kotak-Neo/Kotak-neo-api-v2):
        - Only consumer_key is needed (no consumer_secret)
        - session_init() is NOT called during initialization
        - Auth uses totp_login + totp_validate via direct HTTP
        - The old SDK is kept for order operations (place_order, modify_order, etc.)
        """
        try:
            if self.consumer_key:
                # v2 method: Initialize without session_init — auth done separately via HTTP
                logging.info("[KotakOrderService] Initializing NeoAPI client (v2 mode, consumer_key only)...")
                # Use access_token=consumer_key to bypass old SDK's validate_configuration
                # which would require consumer_secret. This just creates the client shell.
                self.client = NeoAPI(access_token=self.consumer_key, environment='prod')
                # Store consumer_key in configuration for use during order placement
                config = self.client.configuration  # type: ignore[attr-defined]
                config.consumer_key = self.consumer_key
                
                # Inject cached trading tokens if available
                if self.trading_token and self.trading_sid:
                    config.edit_token = self.trading_token  # type: ignore[attr-defined]
                    config.edit_sid = self.trading_sid      # type: ignore[attr-defined]
                    if self.server_id:
                        config.serverId = self.server_id    # type: ignore[attr-defined]
                    logging.info(f"[KotakOrderService] NeoAPI client initialized with tokens: token={self.trading_token[:20]}..., sid={self.trading_sid[:20] if self.trading_sid else 'None'}...")
                else:
                    logging.info("[KotakOrderService] NeoAPI client initialized (needs authentication - no cached tokens)")
                    
            elif self.access_token:
                # Legacy fallback
                logging.warning("[KotakOrderService] Using legacy access_token mode")
                self.client = NeoAPI(access_token=self.access_token, environment='prod')
                
                if self.trading_token and self.trading_sid:
                    config = self.client.configuration  # type: ignore[attr-defined]
                    config.edit_token = self.trading_token  # type: ignore[attr-defined]
                    config.edit_sid = self.trading_sid      # type: ignore[attr-defined]
                    if self.server_id:
                        config.serverId = self.server_id    # type: ignore[attr-defined]
            else:
                logging.warning("[KotakOrderService] Missing consumer_key and access_token, cannot init NeoAPI")
        except Exception as e:
            logging.error(f"[KotakOrderService] Failed to init NeoAPI: {e}")

    def inject_trading_tokens(self) -> bool:
        """Re-inject trading tokens into the SDK client configuration.
        
        Call this after manually setting trading_token and trading_sid
        to ensure they're propagated to the SDK client.
        """
        if not self.client:
            logging.warning("[KotakOrderService] inject_trading_tokens: No client initialized")
            return False
            
        if not self.trading_token or not self.trading_sid:
            logging.warning("[KotakOrderService] inject_trading_tokens: Missing tokens (token={}, sid={})".format(
                bool(self.trading_token), bool(self.trading_sid)))
            return False
        
        try:
            config = self.client.configuration  # type: ignore[attr-defined]
            config.edit_token = self.trading_token  # type: ignore[attr-defined]
            config.edit_sid = self.trading_sid      # type: ignore[attr-defined]
            if self.server_id:
                config.serverId = self.server_id    # type: ignore[attr-defined]
            if self.base_url:
                config.base_url = self.base_url     # type: ignore[attr-defined]
            logging.info(f"[KotakOrderService] ✅ Trading tokens injected: token={self.trading_token[:20]}..., sid={self.trading_sid[:20]}...")
            return True
        except Exception as e:
            logging.error(f"[KotakOrderService] Failed to inject tokens: {e}")
            return False

    def authenticate(self) -> bool:
        """
        Authenticate with Kotak Neo API using v2 TOTP flow.
        
        Per official docs (https://github.com/Kotak-Neo/Kotak-neo-api-v2):
        Step 1: totp_login(mobile_number, ucc, totp) → view_token + sid
        Step 2: totp_validate(mpin) → edit_token (trading token)
        
        Uses direct HTTP requests since the installed SDK is older and doesn't have
        totp_login/totp_validate methods. Tokens are injected into the SDK client
        after successful auth for order operations.
        """
        try:
            if not self.client:
                self._init_client()
                if not self.client:
                    self.last_error = "NeoAPI client initialization failed (missing consumer_key?)"
                    return False
            
            if not self.consumer_key:
                self.last_error = "Missing KOTAK_CONSUMER_KEY. Get it from Kotak Neo App/Web → Invest → Trade API."
                return False
            
            # If we don't have the required creds, we can't do a full login
            if not self.mobile_number or not self.mpin or not self.totp_secret:
                self.last_error = "Missing mobile number, password/mpin, or TOTP secret."
                logging.warning(f"[KotakOrderService] Cannot authenticate fully: {self.last_error}")
                # Fallback: if edit_token is cached, assume it's valid
                if self.client.configuration.edit_token:
                    return True
                return False
                
            logging.info("[KotakOrderService] Authenticating with Kotak Neo v2 TOTP flow...")
            
            # Generate TOTP
            try:
                if len(self.totp_secret) == 6 and self.totp_secret.isdigit():
                    totp = self.totp_secret
                else:
                    totp = pyotp.TOTP(self.totp_secret).now()
                logging.info(f"[KotakOrderService] Generated TOTP (length: {len(totp)})")
            except Exception as e:
                self.last_error = f"Failed to generate TOTP: {e}"
                logging.error(f"[KotakOrderService] {self.last_error}")
                return False
            
            # ═══════════════════════════════════════════
            # Step 1: totp_login → POST /login/1.0/tradeApiLogin
            # ═══════════════════════════════════════════
            import requests
            
            login_url = self._auth_base_url + "login/1.0/tradeApiLogin"
            login_headers = {
                'Authorization': self.consumer_key,
                'neo-fin-key': self._neo_fin_key,
                'Content-Type': 'application/json'
            }
            # Ensure mobile number has country code prefix (required by v2 API)
            mobile_with_code = self.mobile_number
            if mobile_with_code and not mobile_with_code.startswith('+'):
                mobile_with_code = '+91' + mobile_with_code
            
            login_body = {
                "mobileNumber": mobile_with_code,
                "ucc": self.ucc,
                "totp": totp
            }
            
            logging.info(f"[KotakOrderService] Step 1: POST {login_url}")
            try:
                login_resp = requests.post(login_url, json=login_body, headers=login_headers, timeout=30)
                try:
                    login_data = login_resp.json()
                except Exception:
                    self.last_error = f"totp_login returned non-JSON response [{login_resp.status_code}]: {login_resp.text[:200]}"
                    logging.error(f"[KotakOrderService] {self.last_error}")
                    return False
                logging.info(f"[KotakOrderService] Step 1 response [{login_resp.status_code}]: {login_data}")
            except Exception as e:
                self.last_error = f"totp_login HTTP request failed: {e}"
                logging.error(f"[KotakOrderService] {self.last_error}")
                return False
            
            if login_resp.status_code < 200 or login_resp.status_code >= 300:
                error_msg = login_data.get('error', login_data.get('message', str(login_data)))
                self.last_error = f"totp_login failed [{login_resp.status_code}]: {error_msg}"
                logging.error(f"[KotakOrderService] {self.last_error}")
                return False
            
            # Extract view_token and sid from response
            resp_data = login_data.get('data', {})
            if isinstance(resp_data, str):
                import json
                try:
                    resp_data = json_lib.loads(resp_data)
                except Exception:
                    pass
            
            self._view_token = resp_data.get('token') if isinstance(resp_data, dict) else None
            self._sid = resp_data.get('sid') if isinstance(resp_data, dict) else None
            
            if not self._view_token:
                self.last_error = f"totp_login succeeded but no view_token returned. Response: {login_data}"
                logging.error(f"[KotakOrderService] {self.last_error}")
                return False
            
            logging.info("[KotakOrderService] ✅ Step 1 OK — view_token obtained")
            
            # ═══════════════════════════════════════════
            # Step 2: totp_validate → POST /login/1.0/tradeApiValidate
            # ═══════════════════════════════════════════
            validate_url = self._auth_base_url + "login/1.0/tradeApiValidate"
            validate_headers = {
                'Authorization': self.consumer_key,
                'sid': self._sid or '',
                'Auth': self._view_token,
                'neo-fin-key': self._neo_fin_key,
                'Content-Type': 'application/json'
            }
            validate_body = {
                "mpin": self.mpin
            }
            
            logging.info(f"[KotakOrderService] Step 2: POST {validate_url}")
            try:
                validate_resp = requests.post(validate_url, json=validate_body, headers=validate_headers, timeout=30)
                try:
                    validate_data = validate_resp.json()
                except Exception:
                    self.last_error = f"totp_validate returned non-JSON response [{validate_resp.status_code}]: {validate_resp.text[:200]}"
                    logging.error(f"[KotakOrderService] {self.last_error}")
                    return False
                logging.info(f"[KotakOrderService] Step 2 response [{validate_resp.status_code}]: {validate_data}")
            except Exception as e:
                self.last_error = f"totp_validate HTTP request failed: {e}"
                logging.error(f"[KotakOrderService] {self.last_error}")
                return False
            
            if validate_resp.status_code < 200 or validate_resp.status_code >= 300:
                error_msg = validate_data.get('error', validate_data.get('message', str(validate_data)))
                self.last_error = f"totp_validate failed [{validate_resp.status_code}]: {error_msg}"
                logging.error(f"[KotakOrderService] {self.last_error}")
                return False
            
            # Extract edit_token (trading token) from response
            v2_base_url = None
            v2_data_center = None
            val_data = validate_data.get('data', {})
            if isinstance(val_data, str):
                try:
                    val_data = json_lib.loads(val_data)
                except Exception:
                    pass
            
            if isinstance(val_data, dict):
                self.trading_token = val_data.get('token')
                self.trading_sid = val_data.get('sid')
                self.server_id = val_data.get('hsServerId', self.server_id)
                # v2: base_url is returned from totp_validate for order routing
                v2_base_url = val_data.get('baseUrl')
                v2_data_center = val_data.get('dataCenter')
            
            if self.trading_token:
                # Inject tokens into the SDK client for order operations
                config = self.client.configuration  # type: ignore[attr-defined]
                config.edit_token = self.trading_token  # type: ignore[attr-defined]
                config.edit_sid = self.trading_sid      # type: ignore[attr-defined]
                if self.server_id:
                    config.serverId = self.server_id    # type: ignore[attr-defined]
                
                # VERIFY tokens were actually set
                logging.info(f"[KotakOrderService] ✅ Step 2 OK — tokens injected into client")
                logging.info(f"[KotakOrderService]    → edit_token: {config.edit_token[:20] if config.edit_token else 'EMPTY'}...")
                logging.info(f"[KotakOrderService]    → edit_sid: {config.edit_sid[:20] if config.edit_sid else 'EMPTY'}...")
                logging.info(f"[KotakOrderService]    → serverId: {config.serverId if hasattr(config, 'serverId') else 'NOT SET'}")
                
                # ═══════════════════════════════════════════
                # Save base_url for direct HTTP order placement
                # ═══════════════════════════════════════════
                if v2_base_url:
                    self._order_base_url = v2_base_url
                    self.base_url = v2_base_url  # auth.py reads this to save to env
                    if self.client:
                        self.client.configuration.base_url = v2_base_url  # type: ignore[attr-defined]
                    logging.info(f"[KotakOrderService] Set order base_url from totp_validate: {v2_base_url}")
                
                if v2_data_center:
                    self.client.configuration.data_center = v2_data_center  # type: ignore[attr-defined]
                
                # 2. Patch get_domain() to return v2 base_url for order routing
                #    Old SDK returns hardcoded PROD_BASE_URL, but v2 uses dynamic base_url
                config = self.client.configuration  # type: ignore[attr-defined]
                actual_base_url = getattr(config, 'base_url', None)
                def patched_get_domain(session_init: bool = False):
                    if session_init:
                        return "https://mis.kotaksecurities.com"
                    return actual_base_url if actual_base_url else "https://mis.kotaksecurities.com"
                config.get_domain = patched_get_domain  # type: ignore[attr-defined]
                
                # 3. Patch get_url_details() to use v2 PROD_URL paths
                #    Old SDK: "Orders/2.0/quick/order/..." (goes through gateway, needs OAuth2)
                #    v2 SDK:  "quick/order/..." (direct to trading server, no OAuth2 needed)
                V2_PROD_URL = {
                    "place_order": "quick/order/rule/ms/place",
                    "cancel_order": "quick/order/cancel",
                    "modify_order": "quick/order/vr/modify",
                    "order_history": "quick/order/history",
                    "order_book": "quick/user/orders",
                    "trade_report": "quick/user/trades",
                    "positions": "quick/user/positions",
                    "holdings": "portfolio/v1/holdings",
                    "margin": "quick/user/check-margin",
                    "limits": "quick/user/limits",
                    "scrip_master": "script-details/1.0/masterscrip/file-paths",
                    "logout": "logout",
                }
                def patched_get_url_details(api_info):
                    base = patched_get_domain()
                    path = V2_PROD_URL.get(api_info, '')
                    if base and not base.endswith('/'):
                        base += '/'
                    return base + path
                config.get_url_details = patched_get_url_details
                
                # 4. Monkey-patch OrderAPI to NOT send Bearer token (v2 removed it)
                #    Old SDK: Authorization: Bearer {bearer_token}  ← causes 900901
                #    v2 SDK: only Sid + Auth headers
                self._patch_order_api_headers()
                
                logging.info("[KotakOrderService] ✅ Successfully authenticated with Kotak Neo. Trading tokens generated.")
                return True
            else:
                self.last_error = "totp_validate succeeded but no trading token was returned."
                logging.error(f"[KotakOrderService] {self.last_error}")
                return False
                
        except Exception as e:
            self.last_error = str(e)
            logging.error(f"[KotakOrderService] Authentication exception: {e}", exc_info=True)
            return False
    
    def _patch_order_api_headers(self):
        """Monkey-patch the old SDK's OrderAPI to NOT send Bearer token.
        
        The v2 API removed `Authorization: Bearer {token}` from order headers.
        Old SDK sends it, causing 900901 Invalid Credentials.
        v2 SDK only uses Sid + Auth + neo-fin-key headers.
        """
        import neo_api_client
        from neo_api_client.api.order_api import OrderAPI
        
        # Save original methods
        _orig_order_placing = OrderAPI.order_placing
        _orig_order_cancelling = OrderAPI.order_cancelling
        
        def _make_v2_headers(api_client):
            """Build v2-style headers (consumer_key as Authorization, no Bearer prefix)."""
            return {
                "Authorization": api_client.configuration.consumer_key,
                "Sid": api_client.configuration.edit_sid,
                "Auth": api_client.configuration.edit_token,
                "neo-fin-key": api_client.configuration.get_neo_fin_key(),
                "Content-Type": "application/x-www-form-urlencoded"
            }
        
        def patched_order_placing(self, exchange_segment, product, price, order_type, 
                                   quantity, validity, trading_symbol, transaction_type,
                                   amo=None, disclosed_quantity=None, market_protection=None,
                                   pf=None, trigger_price=None, tag=None, **kwargs):
            try:
                header_params = _make_v2_headers(self.api_client)
                body_params = {"am": amo, "dq": disclosed_quantity, "es": exchange_segment, 
                              "mp": market_protection, "pc": product, "pf": pf, "pr": price,
                              "pt": order_type, "qt": quantity, "rt": validity,
                              "tp": trigger_price, "ts": trading_symbol, "tt": transaction_type, 
                              "ig": tag}
                query_params = {"sId": self.api_client.configuration.serverId}
                URL = self.api_client.configuration.get_url_details("place_order")
                logging.info(f"[KotakOrderService] v2 order URL: {URL}")
                orders_resp = self.rest_client.request(
                    url=URL, method='POST', query_params=query_params,
                    headers=header_params, body=body_params
                )
                return orders_resp.json()
            except Exception as ex:
                return {"error": str(ex)}
        
        def patched_order_cancelling(self, order_id, isVerify=False, amo=None):
            if isVerify:
                order_book_resp = neo_api_client.OrderReportAPI(self.api_client).ordered_books()
                if isinstance(order_book_resp, dict) and "data" in order_book_resp and isinstance(order_book_resp.get("data"), list):
                    for item in order_book_resp["data"]:
                        if item.get("nOrdNo") == order_id.strip():
                            if item.get("ordSt") in ["rejected", "cancelled", "complete", "traded"]:
                                return {"Error": "The Given Order Status is " + str(item.get("ordSt")),
                                        "Reason": item.get("rejRsn")}
            
            header_params = _make_v2_headers(self.api_client)
            body_params = {"on": order_id, "am": amo}
            query_params = {"sId": self.api_client.configuration.serverId}
            URL = self.api_client.configuration.get_url_details("cancel_order")
            try:
                cancel_resp = self.rest_client.request(
                    url=URL, method='POST', query_params=query_params,
                    headers=header_params, body=body_params
                )
                return cancel_resp.json()
            except Exception as ex:
                return {"error": str(ex)}
        
        # Apply patches at class level
        OrderAPI.order_placing = patched_order_placing  # type: ignore[assignment]
        OrderAPI.order_cancelling = patched_order_cancelling  # type: ignore[assignment]
        logging.info("[KotakOrderService] ✅ Patched OrderAPI to use v2 headers (no Bearer token)")
    
    def verify_credentials(self) -> Dict[str, Any]:
        """Verify credentials sufficiency"""
        result = {
            'valid': True,
            'missing': [],
            'validation_errors': [],
            'suggestions': []
        }
        
        if not self.consumer_key:
            result['missing'].append("KOTAK_CONSUMER_KEY")
            result['valid'] = False
            
        if not self.trading_token:
            result['missing'].append("KOTAK_TRADING_TOKEN")
            result['valid'] = False
            
        return result

    def place_order(self, tradingsymbol: str, transaction_type: str, price: float,
                   quantity: int, order_type: str = 'MKT', product_type: str = 'MIS',
                   exchange_segment: str = 'nse_fo', trigger_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Place an order in Kotak Neo - tries SDK first, then falls back to HTTP v2 API.
        """
        import requests as http_requests
        
        # Map transaction type to SDK format
        neo_txn_type = 'B' if transaction_type.upper() in ['BUY', 'B'] else 'S'
        
        # TRY SDK FIRST (it handles the request internally)
        if self.client and hasattr(self.client, 'place_order'):
            # Check if SDK has valid credentials before trying
            config = getattr(self.client, 'configuration', None)
            has_edit_token = config and getattr(config, 'edit_token', None)
            has_edit_sid = config and getattr(config, 'edit_sid', None)
            
            if has_edit_token and has_edit_sid:
                try:
                    logging.info(f"[Kotak] Attempting SDK order: {tradingsymbol} {neo_txn_type} {quantity} @ {price}, trigger_price={trigger_price}")
                    logging.info(f"[Kotak] SDK credentials present: token={has_edit_token[:20] if isinstance(has_edit_token, str) else 'non-string'}..., sid={has_edit_sid[:20] if isinstance(has_edit_sid, str) else 'non-string'}...")
                    
                    # Build SDK order params
                    sdk_params = {
                        'transaction_type': neo_txn_type,
                        'order_type': order_type,
                        'price': str(float(price)) if price else "0",  # Price MUST be a string for SDK
                        'product': product_type,
                        'quantity': str(int(quantity)),  # Quantity MUST also be a string for SDK
                        'trading_symbol': tradingsymbol,
                        'validity': 'DAY',
                        'exchange_segment': exchange_segment
                    }
                    
                    # Add trigger_price for SL orders
                    if trigger_price and float(trigger_price) > 0:
                        sdk_params['trigger_price'] = str(float(trigger_price))
                    
                    sdk_result = self.client.place_order(**sdk_params)
                    logging.info(f"[Kotak] SDK order response: {sdk_result}")
                    if sdk_result and isinstance(sdk_result, dict):
                        if 'nOrdNo' in sdk_result or sdk_result.get('stat') == 'Ok':
                            return {
                                'success': True,
                                'order_id': sdk_result.get('nOrdNo', 'Unknown'),
                                'response': str(sdk_result),
                                'note': 'Placed via SDK'
                            }
                        elif 'Error' in sdk_result:
                            logging.warning(f"[Kotak] SDK returned error: {sdk_result.get('Error')}")
                        else:
                            logging.warning(f"[Kotak] SDK returned unexpected response: {sdk_result}")
                except Exception as sdk_e:
                    logging.warning(f"[Kotak] SDK order failed: {sdk_e}, falling back to HTTP")
            else:
                logging.warning(f"[Kotak] SDK credentials missing: token={bool(has_edit_token)}, sid={bool(has_edit_sid)}, skipping SDK try")
        
        # FALLBACK TO HTTP v2 API
        logging.info(f"[Kotak] Falling back to HTTP v2 API for order: {tradingsymbol}")
        try:
            # Validate we have trading tokens from authentication
            if not self.trading_token or not self.trading_sid:
                return {'success': False, 'error': 'Not authenticated. Please login to Kotak Neo first.'}
            
            if not self.consumer_key:
                return {'success': False, 'error': 'Missing consumer_key. Check KOTAK_CONSUMER_KEY in env.'}
            
            # Log authentication status
            logging.info(f"[Kotak] HTTP order placement - Auth status:")
            logging.info(f"[Kotak]   → trading_token: {self.trading_token[:30] if self.trading_token else 'EMPTY'}...")
            logging.info(f"[Kotak]   → trading_sid: {self.trading_sid[:30] if self.trading_sid else 'EMPTY'}...")
            logging.info(f"[Kotak]   → consumer_key: {self.consumer_key[:30] if self.consumer_key else 'EMPTY'}...")
            logging.info(f"[Kotak]   → server_id: {self.server_id if hasattr(self, 'server_id') else 'NOT SET'}")
            
            # Determine base URL for orders (from totp_validate response or default)
            order_base_url = getattr(self, '_order_base_url', None)
            if not order_base_url:
                # Try to get from SDK client if available (use getattr — old SDK lacks base_url)
                if self.client and hasattr(self.client, 'configuration'):
                    order_base_url = getattr(self.client.configuration, 'base_url', None)
                if not order_base_url:
                    order_base_url = "https://gw-napi.kotaksecurities.com"
            
            # Map transaction type
            neo_txn_type = 'B' if transaction_type.upper() in ['B', 'BUY'] else 'S'
            
            # Detect if using napi gateway vs direct server
            # gw-napi/napi gateways need Orders/2.0/ prefix; direct servers (e21, e22, mnapi, cnapi) don't
            is_gateway = 'gw-napi' in order_base_url or order_base_url.rstrip('/').endswith('napi.kotaksecurities.com')
            
            if is_gateway:
                order_path = '/Orders/2.0/quick/order/rule/ms/place'
            else:
                order_path = '/quick/order/rule/ms/place'
            
            order_url = order_base_url.rstrip('/') + order_path
            
            # Build headers per v2 API
            neo_fin_key = self._neo_fin_key
            if self.client and hasattr(self.client, 'configuration'):
                try:
                    conf = self.client.configuration
                    if hasattr(conf, 'get_neo_fin_key'):
                        neo_fin_key = conf.get_neo_fin_key()
                except Exception:
                    pass

            order_headers = {
                "Authorization": str(self.consumer_key or ""),
                "Sid": self.trading_sid,
                "Auth": self.trading_token,
                "Content-Type": "application/x-www-form-urlencoded",
                "neo-fin-key": neo_fin_key,
            }

            # Body uses short keys; v2 API sends directly (no jData wrapper)
            # All values must be strings per x-www-form-urlencoded spec
            # Build body matching SDK defaults (from neo_api_client/api/order_api.py)
            
            # Normalize order_type for Kotak Neo
            order_type_upper = str(order_type).upper()
            
            # For SL-M orders, Kotak requires:
            # - pt = 'SL-M'
            # - pr = '0' (no limit price)
            # - tp = trigger price (MUST be > 0)
            is_sl_market = order_type_upper in ['SL-M', 'SLM', 'STOP_LOSS_MARKET']
            is_sl_limit = order_type_upper in ['SL', 'SL-L', 'STOP_LOSS']
            is_limit = order_type_upper in ['L', 'LIMIT']
            
            # Validate trigger price for SL orders
            if (is_sl_market or is_sl_limit) and (not trigger_price or float(trigger_price) <= 0):
                logging.error(f"[place_order] SL order requires trigger_price > 0, got: {trigger_price}")
                return {'success': False, 'error': f'Stop loss order requires trigger_price > 0, got: {trigger_price}'}
            
            order_body = {
                "es": str(exchange_segment).lower(),  # nse_fo
                "pc": str(product_type).upper(),      # NRML, MIS, CNC
                "pt": 'SL-M' if is_sl_market else ('SL' if is_sl_limit else order_type_upper),  # MKT, L, SL, SL-M
                "qt": str(int(quantity)),             # quantity as string
                "rt": "DAY",                          # return type (validity)
                "ts": str(tradingsymbol),             # symbol AS-IS (don't uppercase - Kite format may be case-sensitive)
                "tt": str(neo_txn_type),              # B or S
                "am": "NO",                           # after market - ALWAYS include
                "dq": "0",                            # disclosed quantity - ALWAYS include (SDK default)
                "mp": "0",                            # market protection - ALWAYS include (SDK default)
                "pf": "N",                            # partial fill - ALWAYS include
                "pr": str(float(price)) if (is_limit or is_sl_limit) and price and float(price) > 0 else "0",  # price (required for LIMIT/SL, 0 for SL-M)
                "tp": str(float(trigger_price)) if (is_sl_market or is_sl_limit) and trigger_price and float(trigger_price) > 0 else "0",  # trigger price (for SL orders)
            }
            
            # Validate critical fields
            if order_body.get("qt") and int(float(order_body["qt"])) == 0:
                logging.error(f"[place_order] Invalid quantity: {quantity}")
                return {'success': False, 'error': 'Invalid quantity - must be > 0'}
            
            if not order_body.get("ts"):
                logging.error(f"[place_order] Invalid symbol: {tradingsymbol}")
                return {'success': False, 'error': 'Invalid trading symbol'}
            
            # Add query params
            query_params = {}
            if self.server_id:
                query_params['sId'] = str(self.server_id)
            
            logging.info(f"[Kotak] Placing Order via HTTP: {tradingsymbol} {neo_txn_type} {quantity} @ {price}")
            logging.info(f"[Kotak] _order_base_url: {getattr(self, '_order_base_url', 'NOT SET')}")
            logging.info(f"[Kotak] Order URL: {order_url} (is_gateway={is_gateway})")
            logging.info(f"[Kotak] Exchange: {exchange_segment}, Product: {product_type}, Order Type: {order_type}")
            logging.info(f"[Kotak] Order body params: es={order_body.get('es')}, pc={order_body.get('pc')}, pt={order_body.get('pt')}, qt={order_body.get('qt')}, ts={order_body.get('ts')}")
            logging.info(f"[Kotak] Trading token present: {'YES' if self.trading_token else 'NO'}, Sid present: {'YES' if self.trading_sid else 'NO'}")
            logging.info(f"[Kotak] Full order body: {order_body}")
            
            # Use PreparedRequest to ensure proper form encoding
            import json
            jdata_payload = json.dumps(order_body)
            # Kotak REST uses a single custom field 'jData' with the stringified JSON payload
            # MUST NOT be urlencoded! Send as raw string body.
            raw_body = 'jData=' + jdata_payload
            
            req = http_requests.Request(
                'POST',
                order_url,
                data=raw_body,
                headers=order_headers,
                params=query_params
            )
            prepared = req.prepare()
            
            # Log the actual body that will be sent
            body_str = prepared.body if prepared.body else 'EMPTY'
            logging.info(f"[Kotak] Prepared request body: {body_str}")
            logging.info(f"[Kotak] Prepared Content-Type: {prepared.headers.get('Content-Type', 'NOT SET')}")
            logging.info(f"[Kotak] Prepared Sid: {prepared.headers.get('Sid', 'NOT SET')[:20]}...")
            logging.info(f"[Kotak] Prepared Auth: {prepared.headers.get('Auth', 'NOT SET')[:20]}...")
            
            session = http_requests.Session()
            resp = session.send(prepared, timeout=30)
            
            try:
                response = resp.json()
            except Exception:
                logging.error(f"[Kotak] Non-JSON response [{resp.status_code}]: {resp.text[:300]}")
                return {'success': False, 'error': f'Non-JSON response from Kotak: {resp.text[:200]}'}
            
            logging.info(f"[Kotak] Order response [{resp.status_code}]: {response}")
            
            # Parse response
            if isinstance(response, dict) and 'nOrdNo' in response:
                order_id = response.get('nOrdNo')
                return {
                    'success': True,
                    'order_id': order_id,
                    'response': str(response)
                }
            elif isinstance(response, dict) and 'stat' in response and response['stat'] == 'Ok':
                return {
                    'success': True,
                    'order_id': response.get('nOrdNo', 'Unknown'),
                    'response': str(response)
                }
            elif isinstance(response, dict) and response.get('stat') in ['Not_Ok', 'bad request', 'Not_OK']:
                error_msg = response.get('errMsg') or response.get('Error') or 'Order failed'
                logging.warning(f"[Kotak] HTTP request failed: {error_msg}")
                
                return {
                    'success': False,
                    'error': error_msg,
                    'response': str(response)
                }
            elif isinstance(response, dict) and ('Error' in response or 'error' in response):
                error_msg = response.get('Error') or response.get('error') or response.get('description', 'Unknown error')
                # If it's a list of errors
                if isinstance(error_msg, list) and len(error_msg) > 0:
                    error_msg = error_msg[0].get('message', str(error_msg)) if isinstance(error_msg[0], dict) else str(error_msg)
                return {
                    'success': False,
                    'error': str(error_msg),
                    'response': str(response)
                }
            elif isinstance(response, dict) and 'message' in response:
                return {
                    'success': False,
                    'error': response.get('description') or response.get('message', 'API Error'),
                    'response': str(response)
                }
            
            return {'success': False, 'error': f'Unknown response format: {str(response)}', 'response': str(response)}

        except Exception as e:
            logging.error(f"❌ Exception placing order for {tradingsymbol}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': tradingsymbol,
                'exception': type(e).__name__
            }
    
    def _format_kotak_symbol(self, default_ts: str, symbol: str, expiry: Optional[date], strike: int, option_type: str) -> str:
        """Return a Kotak-friendly symbol; fall back to default_ts if formatting fails."""
        try:
            if not expiry:
                return default_ts
            expiry_str = expiry.strftime('%d%b%y').upper()  # e.g., 26FEB26
            # Kotak scrip master often uses OPTIDX <SYMBOL> <DDMMMYY> <STRIKE> <CE/PE>
            formatted = f"OPTIDX {symbol.upper()} {expiry_str} {strike} {option_type.upper()}"
            logging.info(f"[KotakOrderService] Formatted Kotak symbol: {formatted}")
            return formatted
        except Exception as fmt_e:
            logging.warning(f"[KotakOrderService] Failed to format Kotak symbol: {fmt_e}")
            return default_ts

    def _get_kite_symbol(self, symbol: str, strike: int, option_type: str) -> Optional[str]:
        """Get Kite's option symbol format for fallback candidate."""
        try:
            from trading_app.service.kite_order_services import KiteService
            kite = KiteService()
            kite_symbol = kite.get_option_symbol(symbol, strike, option_type)
            return kite_symbol
        except Exception as e:
            logging.debug(f"Could not get Kite symbol: {e}")
            return None
    
    def _search_kotak_scrip(self, symbol: str, expiry: Optional[date], strike: int, option_type: str) -> Optional[str]:
        """Search Kotak scrip master for the exact trading symbol (pTrdSymbol)."""
        try:
            if not self.client:
                logging.warning("[KotakOrderService] No NeoAPI client; skipping scrip search")
                return None
            
            exp_date_str = expiry.strftime('%d%b%y').upper() if expiry else None
            logging.info(f"[KotakOrderService] Searching Kotak scrip master for {symbol} {exp_date_str} {strike} {option_type}")
            
            # Try to search using Kotak's search_scrip API
            try:
                result = self.client.search_scrip(
                    exchange_segment='nse_fo',
                    symbol=symbol.upper(),
                    expiry=exp_date_str,
                    option_type=option_type.upper(),
                    strike_price=str(strike)
                )
                logging.info(f"[KotakOrderService] Scrip search result type: {type(result)}, value: {result}")
            except AttributeError as attr_e:
                logging.warning(f"[KotakOrderService] search_scrip method not available: {attr_e}. Client type: {type(self.client)}")
                return None
            except Exception as api_e:
                logging.warning(f"[KotakOrderService] Scrip search API error: {api_e}")
                return None
            
            if isinstance(result, list) and len(result) > 0:
                found_symbol = result[0].get('pTrdSymbol') or result[0].get('tradingSymbol')
                if found_symbol:
                    logging.info(f"[KotakOrderService] ✅ Found Kotak scrip: {found_symbol}")
                    return str(found_symbol)
            elif isinstance(result, dict) and 'pTrdSymbol' in result:
                logging.info(f"[KotakOrderService] ✅ Found Kotak scrip: {result['pTrdSymbol']}")
                return str(result['pTrdSymbol'])
            else:
                logging.warning(f"[KotakOrderService] No valid scrip found in result: {result}")
        except Exception as scrip_e:
            logging.warning(f"[KotakOrderService] Scrip search exception: {scrip_e}", exc_info=True)
        
        return None

    def _candidate_symbols(self, default_ts: str, symbol: str, expiry: Optional[date], strike: int, option_type: str) -> List[str]:
        """Build a list of symbol candidates to try for Kotak order placement.
        
        Priority:
        1. Use Kite-provided symbol (verified from instruments)
        2. Try uppercase variant
        3. Build simple compact format (NIFTY26FEB2625550CE)
        """
        candidates: List[str] = []
        
        # First priority: Our built symbol (text month format)
        if default_ts:
            candidates.append(str(default_ts).strip())

        # Kotak scrip-master style symbol (often accepted by Neo APIs)
        try:
            optidx_format = self._format_kotak_symbol(default_ts, symbol, expiry, strike, option_type)
            if optidx_format and optidx_format not in candidates:
                candidates.append(optidx_format)
        except Exception:
            pass
        
        # Second priority: Kite symbol (numeric date format) - try if text format fails
        # Get Kite symbol if we can
        try:
            if hasattr(self, '_get_kite_symbol'):
                kite_symbol = self._get_kite_symbol(symbol, strike, option_type)
                if kite_symbol and kite_symbol not in candidates:
                    candidates.append(kite_symbol)
                    logging.info(f"[KotakOrderService] Added Kite symbol format as fallback: {kite_symbol}")
        except Exception:
            pass
        
        # Third priority: compact format with text month
        if expiry:
            try:
                exp_ddmmmyy = expiry.strftime('%d%b%y').upper()  # 26FEB26
                compact = f"{symbol.upper()}{exp_ddmmmyy}{strike}{option_type.upper()}"
                if compact not in candidates:
                    candidates.append(compact)
            except Exception as compact_e:
                logging.warning(f"[KotakOrderService] Failed to build compact symbol: {compact_e}")
        
        logging.info(f"[KotakOrderService] Candidate symbols (in order): {candidates}")
        return candidates

    def place_option_order(self, symbol: str, strike: int, option_type: str,
                          transaction_type: str, quantity: Optional[int] = None,
                          sl_price: Optional[float] = None,
                          target_price: Optional[float] = None,
                          limit_price: Optional[float] = None,
                          tradingsymbol: Optional[str] = None,
                          target_expiry: Optional[str] = None,
                          product_type: str = 'NRML') -> Dict[str, Any]:
        """
        Place an options order in Kotak Neo platform.
        
        IMPORTANT: tradingsymbol parameter is IGNORED if provided.
        Always rebuilds from current Kite instruments to ensure correct expiry.
        """
        # LOG AUTHENTICATION STATUS AT START
        logging.warning(f"[KotakOrderService] ===== place_option_order CALLED WITH: symbol={symbol}, strike={strike}, option_type={option_type}, tradingsymbol_param={tradingsymbol}, target_expiry={target_expiry} =====")
        logging.info(f"[KotakOrderService] Auth state at order start:")
        logging.info(f"[KotakOrderService]   → self.trading_token: {self.trading_token[:30] if self.trading_token else 'NONE'}...")
        logging.info(f"[KotakOrderService]   → self.trading_sid: {self.trading_sid[:30] if self.trading_sid else 'NONE'}...")
        logging.info(f"[KotakOrderService]   → self.consumer_key: {self.consumer_key[:30] if self.consumer_key else 'NONE'}...")
        if self.client:
            config = getattr(self.client, 'configuration', None)
            if config:
                logging.info(f"[KotakOrderService]   → SDK client.config.edit_token: {getattr(config, 'edit_token', 'NOT SET')[:30] if getattr(config, 'edit_token', None) else 'NONE'}...")
                logging.info(f"[KotakOrderService]   → SDK client.config.edit_sid: {getattr(config, 'edit_sid', 'NOT SET')[:30] if getattr(config, 'edit_sid', None) else 'NONE'}...")
        
        # Use the passed-in tradingsymbol if provided, otherwise fallback to rebuild later
        resolved_expiry = None
        resolved_quantity = quantity
        kite_option_symbol = tradingsymbol
        kite_expiry_date = None
        kite = None
        resolver_service = None  # type: ignore
        from datetime import date as _date
        today = _date.today()
        
        # Initialize variables
        default_expiry_str: Optional[str] = None
        
        # Only initialize Kite if we absolutely need to resolve tradingsymbol, expiry, or quantity
        if not tradingsymbol or not quantity or not target_expiry:
            # PRIORITY: Always try to get nearest expiry from Kite first (most reliable)
            # This handles past expiries, market hours, and gets exact date
            try:
                # Try to create a fresh KiteService instance from env tokens
                from trading_app.service.kite_order_services import KiteService
                try:
                    kite_service = KiteService()  # Creates Kite from env tokens
                    resolver_service = kite_service
                    # Get nearest valid expiry directly from Kite
                    resolved_expiry = kite_service.get_nearest_option_expiry(symbol, strike, option_type)
                    if resolved_expiry:
                        logging.info(f"[KotakOrderService] Got nearest valid expiry from Kite: {resolved_expiry} (weekday={resolved_expiry.weekday()})")
                    else:
                        logging.warning(f"[KotakOrderService] Kite returned no expiry for {symbol} {strike} {option_type}")
                except Exception as kite_e:
                    logging.warning(f"[KotakOrderService] Failed to get expiry from fresh KiteService: {kite_e}")
                    resolved_expiry = None
            except Exception as import_e:
                logging.warning(f"[KotakOrderService] Could not import KiteService: {import_e}")
                resolved_expiry = None
            
            # Fallback to env only if Kite lookup failed
            if not resolved_expiry:
                default_expiry_str = os.getenv("KOTAK_DEFAULT_EXPIRY") or os.getenv("DEFAULT_OPTION_EXPIRY")
                if target_expiry:
                    try:
                        env_expiry = date.fromisoformat(target_expiry)
                        if env_expiry >= today:  # Only use if not in past
                            resolved_expiry = env_expiry
                            logging.info(f"[KotakOrderService] Using target expiry: {resolved_expiry}")
                    except Exception:
                        pass
                elif default_expiry_str:
                    try:
                        env_expiry = date.fromisoformat(default_expiry_str)
                        if env_expiry >= today:
                            resolved_expiry = env_expiry
                            logging.info(f"[KotakOrderService] Using default expiry from env: {resolved_expiry}")
                    except Exception:
                        pass
            
            # Further attempt: if we still don't have resolver_service, try fresh KiteService
            if not resolver_service:
                try:
                    from trading_app.service.kite_order_services import KiteService
                    kite_service = KiteService()  # Fresh instance from env tokens
                    resolver_service = kite_service
                    try:
                        # Ensure fresh instruments
                        kite_service._refresh_nfo_cache_if_stale()
                    except Exception:
                        pass
                except Exception as ks_e:
                    logging.warning(f"[KotakOrderService] Could not create fresh KiteService: {ks_e}")
            
            if resolver_service:
                try:
                    kite_service = resolver_service
                    if not quantity:
                        quantity = kite_service.get_lot_size(symbol)
                        resolved_quantity = quantity
                        logging.info(f"[KotakOrderService] Fetched dynamic lot size for {symbol} from Kite: {quantity}")
                    
                    # Fetch exact expiry if not already resolved
                    if not resolved_expiry:
                        kite_expiry_date = kite_service.get_nearest_option_expiry(symbol, strike, option_type)
                        if kite_expiry_date:
                            logging.info(f"[KotakOrderService] Fetched exact expiry from Kite for {symbol}: {kite_expiry_date}")
                            resolved_expiry = kite_expiry_date
                    
                    # Fetch trading symbol if not already set
                    if not tradingsymbol:
                        kite_option_symbol = kite_service.get_option_symbol(symbol, strike, option_type)
                        if kite_option_symbol:
                            # Log it for reference but DON'T use it for Kotak
                            logging.info(f"[KotakOrderService] Kite has option symbol: {kite_option_symbol} (but building our own for Kotak)")
                except Exception as e:
                    logging.warning(f"[KotakOrderService] Failed to fetch from resolver_service: {e}")
        else:
            logging.info(f"[KotakOrderService] Bypassed KiteService init - using Provided tradingsymbol: {tradingsymbol}, qty: {quantity}")
            if target_expiry:
                try:
                    resolved_expiry = date.fromisoformat(target_expiry)
                except Exception:
                    pass
        
        # Ultimate fallback if Kite fails - set quantity if not already set
        if not quantity:
            logging.warning(f"[KotakOrderService] Using fallback hardcoded lot size for {symbol}")
            symbol_upper = symbol.upper()
            if symbol_upper == 'NIFTY': quantity = 65
            elif symbol_upper == 'BANKNIFTY': quantity = 15
            elif symbol_upper == 'FINNIFTY': quantity = 40
            elif symbol_upper == 'MIDCPNIFTY': quantity = 50
            elif symbol_upper == 'SENSEX': quantity = 20
            elif symbol_upper == 'BANKEX': quantity = 15
            else: quantity = 1
            resolved_quantity = quantity

        # As a last resort, pick nearest expiry from the loaded instruments cache and build symbol
        if not resolved_expiry and resolver_service and getattr(resolver_service, '_nfo_instruments_cache', None):
            try:
                instruments = resolver_service._nfo_instruments_cache or []
                from datetime import date as _date
                today = _date.today()
                expiries = []
                for inst in instruments:
                    if inst.get('name') == symbol and inst.get('expiry'):
                        exp_val = inst['expiry']
                        if hasattr(exp_val, 'date'):
                            exp_val = exp_val.date()
                        expiries.append(exp_val)
                if expiries:
                    expiries = [e for e in expiries if e >= today]
                    if expiries:
                        nearest = min(expiries)
                        resolved_expiry = nearest
                        logging.info(f"[KotakOrderService] Nearest expiry fallback from instruments: {resolved_expiry}")
            except Exception as exp_fallback_e:
                logging.warning(f"[KotakOrderService] Nearest expiry fallback failed: {exp_fallback_e}")

        # If we have a resolved expiry (from caller, env, Kite, or nearest fallback), build the symbol from it when missing
        if not tradingsymbol and resolved_expiry:
            try:
                tradingsymbol = self._build_option_symbol(symbol, strike, option_type, target_expiry=resolved_expiry)
                logging.info(f"[KotakOrderService] Built option symbol from resolved expiry: {tradingsymbol}")
            except Exception as build_e:
                logging.warning(f"[KotakOrderService] Failed to build symbol from resolved expiry: {build_e}")

        # Absolute last resort: synthesize next weekly expiry if nothing resolved yet
        if not resolved_expiry and not tradingsymbol:
            try:
                from datetime import timedelta
                now_dt = datetime.now()
                days_until_thursday = (3 - now_dt.weekday()) % 7
                if days_until_thursday == 0 and now_dt.hour >= 15:
                    days_until_thursday = 7
                resolved_expiry = (now_dt + timedelta(days=days_until_thursday)).date()
                tradingsymbol = self._build_option_symbol(symbol, strike, option_type, target_expiry=resolved_expiry)
                logging.info(f"[KotakOrderService] Synthesized expiry fallback (next Thursday): {resolved_expiry}, symbol: {tradingsymbol}")
            except Exception as synth_e:
                logging.warning(f"[KotakOrderService] Synthetic expiry fallback failed: {synth_e}")

        # If we still don't have an expiry and no Kite, stop early and ask for explicit expiry/tradingsymbol
        if not resolved_expiry and not tradingsymbol:
            return {
                'success': False,
                'error': 'Could not resolve option symbol — no expiry available',
                'hint': 'Provide expiry (YYYY-MM-DD) or tradingsymbol, or login to Kite for discovery',
                'symbol': symbol,
                'strike': strike,
                'option_type': option_type,
                'quantity': resolved_quantity,
                'needs_kite_login': kite is None and resolver_service is None,
                'env_expiry': default_expiry_str
            }

        # If we have a resolver, prefer its exact option symbol before sending to Kotak
        # NOTE: DISABLED - Kite symbols are in a different format than Kotak expects
        # We already built the correct format above, don't override it
        # if resolver_service and hasattr(resolver_service, 'get_option_symbol'):
        #     try:
        #         resolved_ts = resolver_service.get_option_symbol(symbol, strike, option_type)
        #         if resolved_ts:
        #             tradingsymbol = resolved_ts
        #             logging.info(f"[KotakOrderService] Using resolver option symbol before Kotak order: {tradingsymbol}")
        #     except Exception as res_sym_e:
        #         logging.warning(f"[KotakOrderService] Resolver symbol check failed: {res_sym_e}")

        # If Kite is not logged in or the option is not found, stop and ask the user
        if not tradingsymbol:
            return {
                'success': False,
                'error': 'Could not resolve option symbol for current expiry',
                'hint': 'Login to Kite (for symbol discovery) or provide the exact tradingsymbol',
                'symbol': symbol,
                'strike': strike,
                'option_type': option_type,
                'expiry': resolved_expiry.isoformat() if resolved_expiry else None,
                'quantity': resolved_quantity,
                'needs_kite_login': kite is None and resolver_service is None
            }
        
        # Try to search for exact Kotak scrip symbol before attempting candidates
        # NOTE: Disabled for now - search_scrip() requires additional auth that SDK doesn't handle correctly
        # Instead, rely on our built symbol format which is correct for Kotak v2 API
        kotak_scrip_symbol = None
        # if resolved_expiry:
        #     kotak_scrip_symbol = self._search_kotak_scrip(symbol, resolved_expiry, strike, option_type)
        #     if kotak_scrip_symbol:
        #         logging.info(f"[KotakOrderService] Found exact Kotak scrip: {kotak_scrip_symbol}")
        #         tradingsymbol = kotak_scrip_symbol
        
        logging.info(f"[KotakOrderService] ===== PRE-ATTEMPT DEBUG =====")
        logging.info(f"[KotakOrderService] tradingsymbol: {tradingsymbol}")
        logging.info(f"[KotakOrderService] resolved_expiry: {resolved_expiry} (type: {type(resolved_expiry)})")
        logging.info(f"[KotakOrderService] symbol/strike/option_type: {symbol}/{strike}/{option_type}")
        
        # Debug: show what candidates will be generated
        candidates_preview = self._candidate_symbols(tradingsymbol, symbol, resolved_expiry, strike, option_type)
        logging.info(f"[KotakOrderService] Generated candidates: {candidates_preview}")
        
        # Try multiple symbol formats; stop at first success
        last_result: Dict[str, Any] = {}
        all_attempts: List[str] = []
        for candidate_ts in candidates_preview:
            all_attempts.append(candidate_ts)
            logging.info(f"[KotakOrderService] ===== Attempt #{len(all_attempts)} ===== Symbol: '{candidate_ts}' (expiry={resolved_expiry})")
            exch_seg = 'bse_fo' if symbol.upper() == 'SENSEX' else self.EXCHANGE_NFO
            result = self.place_order(
                tradingsymbol=candidate_ts,
                transaction_type=transaction_type,
                price=limit_price if limit_price else 0.0,
                quantity=quantity,
                order_type='L' if limit_price else self.ORDER_TYPE_MARKET,
                product_type=product_type,
                exchange_segment=exch_seg
            )
            
            # Weekend/Illiquid option fallback: If Kotak rejects MARKET order due to no LTP, retry as LIMIT
            if isinstance(result, dict) and not result.get('success') and 'Last Traded Price' in str(result.get('error', '')):
                logging.warning(f"[KotakOrderService] Market order rejected due to missing LTP. Retrying '{candidate_ts}' as LIMIT order...")
                result = self.place_order(
                    tradingsymbol=candidate_ts,
                    transaction_type=transaction_type,
                    price=0.1,  # Minimum tick size fallback
                    quantity=quantity,
                    order_type='L',  # Limit order
                    product_type=product_type,
                    exchange_segment=self.EXCHANGE_NFO
                )
            
            last_result = result if isinstance(result, dict) else {'success': False, 'error': 'Unknown error'}
            logging.info(f"[KotakOrderService] Result: success={last_result.get('success')}, error={last_result.get('error')}")
            if isinstance(result, dict) and result.get('success'):
                logging.info(f"[KotakOrderService] ✅ SUCCESS with symbol: {candidate_ts}")
                last_result['tried_symbols'] = all_attempts
                last_result['successful_symbol'] = candidate_ts
                break
            else:
                logging.warning(f"[KotakOrderService] ❌ Failed attempt #{len(all_attempts)} ({candidate_ts}): {last_result.get('error', 'Unknown error')}")
        
        # If all attempts failed with "body invalid", try with alternative strikes and product types
        if not last_result.get('success') and 'body invalid' in str(last_result.get('error', '')).lower():
            logging.warning(f"[KotakOrderService] All symbol formats failed with 'body invalid'. Trying alternative strikes and product types...")
            
            # Try nearby strikes (25500, 25000, 25600) which are more standard
            alternative_strikes = []
            for alt_strike in [25500, 25000, 25600, 25100]:
                if alt_strike != strike:  # Don't repeat the original strike
                    alternative_strikes.append(alt_strike)
            
            # Also try different product types
            product_types = [self.PRODUCT_NRML, self.PRODUCT_MIS] if hasattr(self, 'PRODUCT_MIS') else [self.PRODUCT_NRML]
            
            for alt_strike in alternative_strikes[:2]:  # Try up to 2 alternative strikes
                for alt_product in product_types:
                    if alt_product == self.PRODUCT_NRML and alt_strike == alternative_strikes[0]:
                        continue  # Skip first combination as it was already tried
                    
                    logging.info(f"[KotakOrderService] Attempting with strike={alt_strike}, product={alt_product}")
                    alt_symbol = self._build_option_symbol(symbol, alt_strike, option_type, target_expiry=resolved_expiry)
                    
                    alt_result = self.place_order(
                        tradingsymbol=alt_symbol,
                        transaction_type=transaction_type,
                        price=limit_price if limit_price else 0.0,
                        quantity=quantity,
                        order_type='L' if limit_price else self.ORDER_TYPE_MARKET,
                        product_type=alt_product,
                        exchange_segment=self.EXCHANGE_NFO
                    )
                    
                    if alt_result and isinstance(alt_result, dict) and alt_result.get('success'):
                        logging.info(f"[KotakOrderService] ✅ SUCCESS with alternative settings: strike={alt_strike}, product={alt_product}")
                        alt_result['note'] = f'Placed with strike={alt_strike}, product={alt_product}'
                        alt_result['original_strike'] = strike
                        alt_result['actual_strike'] = alt_strike
                        return alt_result
                    else:
                        logging.debug(f"[KotakOrderService] Failed with strike={alt_strike}, product={alt_product}")
            
            logging.warning(f"[KotakOrderService] All attempts failed. Exchange segment 'NSE_FO' may not be available in Kotak test environment.")
            last_result['diagnostic'] = {
                'issue': 'Symbol/exchange not found in Kotak master data',
                'tried_symbols': all_attempts,
                'tried_strikes': [strike] + alternative_strikes[:2],
                'tried_products': product_types,
                'expiry': resolved_expiry.isoformat() if resolved_expiry else None,
                'strike': strike,
                'symbol': symbol,
                'exchange': 'NSE_FO (NFO)',
                'suggestion': 'Verify NSE_FO segment is available in Kotak test account. Check if account has derivative trading enabled.',
                'error_analysis': 'Persistent "body invalid" on all strikes suggests exchange segment not configured in test environment'
            }
        
        # Always return result (should be a dict at this point)
        if isinstance(last_result, dict):
            last_result.setdefault('tradingsymbol', tradingsymbol)
            last_result.setdefault('expiry', resolved_expiry.isoformat() if resolved_expiry else None)
            last_result.setdefault('quantity', resolved_quantity)
            last_result.setdefault('tried_symbols', all_attempts)
            return last_result
        else:
            # If last_result is not a dict, return error
            logging.error(f"[KotakOrderService] last_result is not a dict: {type(last_result)}")
            return {
                'success': False,
                'error': 'No valid result from order attempts',
                'symbol': symbol,
                'strike': strike,
                'option_type': option_type,
                'tried_symbols': all_attempts
            }

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
                'validity': 'DAY'
            }
            
            if price is not None:
                modify_params['price'] = str(price)
                if trigger_price is not None:
                    modify_params['order_type'] = 'SL'  # Stop Loss Limit (price + trigger)
                else:
                    modify_params['order_type'] = 'L'  # Limit order
            elif trigger_price is not None:
                modify_params['trigger_price'] = str(trigger_price)
                modify_params['order_type'] = 'SL-M'  # Stop Loss Market (trigger only)
            else:
                # Only quantity change - don't override order_type
                pass
            
            if quantity is not None:
                modify_params['quantity'] = str(quantity) 
            
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
            if not self.client:
                return {'success': False, 'error': 'NeoAPI client not initialized'}
            
            response = self.client.cancel_order(order_id=order_id)  # type: ignore[attr-defined]
            logging.info(f"[Kotak] Cancel Response: {response}")
            
            if isinstance(response, dict) and ('nOrdNo' in response or response.get('stat') == 'Ok'):
                return {'success': True, 'response': response}
            
            return {'success': False, 'error': 'Cancellation failed', 'response': response}
        except Exception as e:
             return {'success': False, 'error': str(e)}

    def _build_option_symbol(self, symbol: str, strike: int, option_type: str, target_expiry: Optional[date] = None) -> str:
        """
        Construct Kotak Neo option symbol.
        Format used by Kotak Neo: SYMBOL + DD + MMM + YY + STRIKE + CE/PE
        Example: NIFTY26FEB2625550CE (Day 26, Month FEB, Year 26)
        """
        try:
            from datetime import timedelta
            now = datetime.now()
            
            if target_expiry:
                expiry_date = target_expiry
            else:
                # Find the nearest upcoming expiry
                # Generic fallback if exact date is not provided
                days_until_thursday = (3 - now.weekday()) % 7
                if days_until_thursday == 0 and now.hour >= 15:
                    # If it's already past 3 PM on expiry day, roll to next week
                    days_until_thursday = 7
                
                expiry_date = (now + timedelta(days=days_until_thursday)).date()
            
            # Extract components: DD, MMM (uppercase), YY
            day_str = expiry_date.strftime('%d')            # e.g., 26
            month_str = expiry_date.strftime('%b').upper()  # e.g., FEB
            year_2_digit = expiry_date.strftime('%y')       # e.g., 26
            
            # Format: NIFTY26FEB2625550CE (NIFTY + 26 + FEB + 26 + 25550 + CE)
            tradingsymbol = f"{symbol}{day_str}{month_str}{year_2_digit}{strike}{option_type}"
            logging.info(f"[KotakOrderService] Built option symbol: {tradingsymbol} (expiry: {expiry_date.strftime('%Y-%m-%d')})")
            return tradingsymbol
        except Exception as e:
            logging.error(f"Error building symbol: {e}")
            return ""
            
    def _get_option_price(self, symbol: str) -> float:
        """Mock/stub for price fetching - not critical for Market orders"""
        return 0.0

    def place_stoploss_order(self, symbol: str, trigger_price: float, 
                            quantity: int, product_type: str = 'MIS', transaction_type: str = 'SELL') -> Dict[str, Any]:
        """
        Place a Stop Loss (SL-M) order in Kotak Neo.
        """
        try:
            # Map transaction type
            neo_txn_type = 'S'
            if transaction_type.upper() in ['BUY', 'B']: neo_txn_type = 'B'
            elif transaction_type.upper() in ['SELL', 'S']: neo_txn_type = 'S'

            exch_seg = 'bse_fo' if symbol.upper().startswith('SENSEX') else 'nse_fo'
            return self.place_order(
                tradingsymbol=symbol,
                transaction_type=neo_txn_type,
                price=0.0,
                quantity=quantity,
                order_type='SL-M',
                product_type=product_type,
                trigger_price=trigger_price,
                exchange_segment=exch_seg
            )
            
        except Exception as e:
            logging.error(f"[Kotak] Place SL Error: {e}")
            return {'success': False, 'error': str(e)}
            
    def place_option_stoploss_order(self, symbol: str, strike: int, option_type: str,
                                   trigger_price: float, quantity: int,
                                   transaction_type: str = 'S',
                                   product_type: str = 'NRML') -> Dict[str, Any]:
        """
        Helper to place SL-M for an option contract by constructing symbol.
        """
        try:
            resolved_expiry: Optional[date] = None
            resolver_service = None

            # Resolve nearest valid expiry (same strategy as place_option_order)
            try:
                from trading_app.service.kite_order_services import KiteService
                resolver_service = KiteService()
                resolved_expiry = resolver_service.get_nearest_option_expiry(symbol, strike, option_type)
            except Exception:
                resolved_expiry = None

            tradingsymbol = self._build_option_symbol(symbol, strike, option_type, target_expiry=resolved_expiry)
            candidates = self._candidate_symbols(tradingsymbol, symbol, resolved_expiry, strike, option_type)

            # Also try exact Kite symbol as an extra fallback
            try:
                if resolver_service:
                    kite_symbol = resolver_service.get_option_symbol(symbol, strike, option_type)
                    if kite_symbol and kite_symbol not in candidates:
                        candidates.append(kite_symbol)
            except Exception:
                pass

            last_result: Dict[str, Any] = {}
            for candidate_ts in candidates:
                last_result = self.place_order(
                    tradingsymbol=candidate_ts,
                    transaction_type=transaction_type,
                    price=0.0,
                    quantity=quantity,
                    order_type='SL-M',
                    product_type=product_type,
                    trigger_price=trigger_price,
                    exchange_segment=self.EXCHANGE_NFO
                )
                if isinstance(last_result, dict) and last_result.get('success'):
                    last_result['successful_symbol'] = candidate_ts
                    last_result['tried_symbols'] = candidates
                    return last_result

            if isinstance(last_result, dict):
                last_result.setdefault('tried_symbols', candidates)
                return last_result

            return {'success': False, 'error': 'SL-M order failed for all symbol formats', 'tried_symbols': candidates}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_positions(self) -> Dict[str, Any]:
        """
        Get all positions for the day.
        
        Returns:
            Dict with list of positions
        """
        try:
            # Validate credentials
            if not self.trading_token or not self.trading_sid:
                return {'success': False, 'error': 'Not authenticated. Please login to Kotak Neo first.'}
            
            # Determine base URL
            order_base_url = getattr(self, '_order_base_url', None)
            if not order_base_url:
                if self.client and hasattr(self.client, 'configuration'):
                    order_base_url = getattr(self.client.configuration, 'base_url', None)
                if not order_base_url:
                    order_base_url = "https://gw-napi.kotaksecurities.com"
            
            is_gateway = 'gw-napi' in order_base_url or order_base_url.rstrip('/').endswith('napi.kotaksecurities.com')
            path = '/Orders/2.0/quick/user/positions' if is_gateway else '/quick/user/positions'
            url = order_base_url.rstrip('/') + path
            
            headers = {
                "Authorization": str(self.consumer_key or ""),
                "Sid": self.trading_sid,
                "Auth": self.trading_token,
                "neo-fin-key": self._neo_fin_key,
                "Content-Type": "application/json"
            }
            
            query_params = {}
            if self.server_id:
                query_params['sId'] = str(self.server_id)
            
            logging.info(f"[Kotak] Fetching positions from: {url}")
            response = requests.get(url, headers=headers, params=query_params, timeout=30)
            
            try:
                data = response.json()
            except Exception:
                return {'success': False, 'error': f'Non-JSON response: {response.text[:200]}'}
            
            if response.status_code == 200 and isinstance(data, dict):
                # Kotak Neo returns positions in 'data' field
                positions = data.get('data', [])
                if isinstance(positions, str): # Sometimes it's a JSON string
                    try: import json; positions = json.loads(positions)
                    except: pass
                
                logging.info(f"✅ Retrieved {len(positions) if isinstance(positions, list) else 0} positions from Kotak")
                return {
                    'success': True,
                    'positions': positions if isinstance(positions, list) else []
                }
            else:
                error_msg = data.get('errMsg') or data.get('message') or 'Unknown error'
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

    def get_orderbook(self) -> Dict[str, Any]:
        """
        Get all orders for the day.
        
        Returns:
            Dict with list of orders
        """
        try:
            if not self.trading_token or not self.trading_sid:
                return {'success': False, 'error': 'Not authenticated'}
            
            order_base_url = getattr(self, '_order_base_url', None)
            if not order_base_url:
                if self.client and hasattr(self.client, 'configuration'):
                    order_base_url = getattr(self.client.configuration, 'base_url', None)
                if not order_base_url:
                    order_base_url = "https://gw-napi.kotaksecurities.com"
            
            is_gateway = 'gw-napi' in order_base_url or order_base_url.rstrip('/').endswith('napi.kotaksecurities.com')
            path = '/Orders/2.0/quick/user/orders' if is_gateway else '/quick/user/orders'
            url = order_base_url.rstrip('/') + path
            
            headers = {
                "Authorization": str(self.consumer_key or ""),
                "Sid": self.trading_sid,
                "Auth": self.trading_token,
                "neo-fin-key": self._neo_fin_key,
                "Content-Type": "application/json"
            }
            
            query_params = {}
            if self.server_id:
                query_params['sId'] = str(self.server_id)
            
            response = requests.get(url, headers=headers, params=query_params, timeout=30)
            try: data = response.json()
            except: return {'success': False, 'error': f'Non-JSON: {response.text[:100]}'}
            
            if response.status_code == 200:
                orders = data.get('data', [])
                if isinstance(orders, str):
                    try: import json; orders = json.loads(orders)
                    except: pass
                
                return {
                    'success': True,
                    'orders': orders if isinstance(orders, list) else []
                }
            else:
                return {'success': False, 'error': data.get('errMsg') or 'API Error'}
                
        except Exception as e:
            logging.error(f"❌ Exception getting orderbook: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
