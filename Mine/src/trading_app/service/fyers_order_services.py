import logging
import os
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List
from datetime import datetime
import requests
import hashlib
import urllib.parse
from fyers_apiv3 import fyersModel

load_dotenv()

class FyersOrderService:
    """
    Service for placing orders in Fyers trading platform using REST API v3.
    Handles order placement, execution, and tracking for options and futures.
    
    Official Docs: https://myapi.fyers.in/docsv3
    """
    
    def __init__(self, app_id: Optional[str] = None, access_token: Optional[str] = None):
        """
        Initialize FyersOrderService with Fyers credentials.
        
        Args:
            app_id: Fyers App ID (format: XXXXXXXXX-100)
            access_token: Access token (format: appid:accesstoken or just the token part)
        
        How to get credentials:
            1. Create APP at: https://myapi.fyers.in/dashboard/
            2. Get APP_ID and SECRET_KEY
            3. Generate access_token via OAuth flow or direct login
        """
        self.app_id = app_id or os.getenv("FYERS_APP_ID")
        self.secret_key = os.getenv("FYERS_SECRET_KEY")
        
        # Get redirect URI - use app's callback endpoint
        self.redirect_uri = os.getenv("FYERS_REDIRECT_URI") or "http://localhost:5000/auth/login/fyers/callback"
        
        # Handle access token format
        if access_token:
            # If format is "appid:token", use as-is
            if ":" in access_token:
                self.access_token = access_token
            # If just token, prepend app_id
            else:
                self.access_token = f"{self.app_id}:{access_token}"
        else:
            token_from_env = os.getenv("FYERS_ACCESS_TOKEN", "")
            if token_from_env:
                if ":" in token_from_env:
                    self.access_token = token_from_env
                else:
                    self.access_token = f"{self.app_id}:{token_from_env}"
            else:
                self.access_token = None
        
        self.base_url = "https://api-t1.fyers.in/api/v3"
        self.last_error = None
        
        # Initialize Fyers SDK client if we have access token
        self.fyers_client = None
        if self.access_token and self.app_id:
            try:
                # Validate token format before SDK init
                if ':' not in str(self.access_token):
                    logging.warning(f"[FyersOrderService] Access token missing app_id prefix. Expected format: 'appid:token'")
                    # Don't fail - REST API will handle it
                else:
                    # Extract token part (SDK expects just the token, not app_id:token format)
                    token_only = self.access_token.split(':')[-1] if ':' in self.access_token else self.access_token
                    if not token_only or len(token_only.strip()) < 10:
                        logging.warning(f"[FyersOrderService] Invalid token format extracted: {token_only[:20] if token_only else 'empty'}")
                    else:
                        self.fyers_client = fyersModel.FyersModel(
                            token=token_only,
                            is_async=False,
                            client_id=self.app_id,
                            log_path=""
                        )
                        logging.info("[FyersOrderService] ✓ Fyers SDK client initialized with token format: extracted")
            except Exception as e:
                logging.warning(f"[FyersOrderService] Failed to init SDK client: {e}")
                self.fyers_client = None
        
        logging.info("[FyersOrderService] Initialized with Fyers credentials")
        logging.debug(f"[FyersOrderService] Redirect URI: {self.redirect_uri}")
        
        # Order type mappings for Fyers
        self.ORDER_TYPE_LIMIT = 1
        self.ORDER_TYPE_MARKET = 2
        self.ORDER_TYPE_STOP_LOSS = 3
        self.ORDER_TYPE_STOP_LOSS_MARKET = 4
        
        # Transaction type (side) mappings
        self.SIDE_BUY = 1
        self.SIDE_SELL = -1
        
        # Product type mappings for Fyers
        self.PRODUCT_INTRADAY = 'INTRADAY'
        self.PRODUCT_CNC = 'CNC'  # Cash and Carry (Delivery)
        self.PRODUCT_MARGIN = 'MARGIN'  # Margin (F&O)
        self.PRODUCT_CO = 'CO'  # Cover Order
        self.PRODUCT_BO = 'BO'  # Bracket Order
        
        # Validity
        self.VALIDITY_DAY = 'DAY'
        self.VALIDITY_IOC = 'IOC'
    
    def _parse_response(self, response: requests.Response, method_name: str = "API") -> Dict[str, Any]:
        """
        Safely parse JSON response from Fyers API.
        
        Args:
            response: requests.Response object
            method_name: Name of the calling method (for logging)
            
        Returns:
            Dict with parsed data or error dict
        """
        import json
        try:
            text = response.text.strip()
            decoder = json.JSONDecoder()

            # Fast path: clean JSON body
            try:
                data, end_idx = decoder.raw_decode(text)
                if end_idx < len(text):
                    logging.debug(f"[{method_name}] Ignoring trailing response data after index {end_idx}")
                return data if isinstance(data, dict) else {'s': 'ok', 'data': data}
            except json.JSONDecodeError:
                # Recover if API prepends noise and then a valid JSON object/array
                for idx, ch in enumerate(text):
                    if ch not in '{[':
                        continue
                    try:
                        data, end_idx = decoder.raw_decode(text[idx:])
                        if idx > 0 or end_idx < len(text[idx:]):
                            logging.debug(f"[{method_name}] Recovered JSON from noisy response (offset={idx})")
                        return data if isinstance(data, dict) else {'s': 'ok', 'data': data}
                    except json.JSONDecodeError:
                        continue
                raise
        except (ValueError, json.JSONDecodeError) as json_err:
            logging.error(f"[{method_name}] Failed to parse JSON response: {json_err}")
            logging.error(f"[{method_name}] Response status: {response.status_code}")
            logging.error(f"[{method_name}] Response headers: {dict(response.headers)}")
            logging.error(f"[{method_name}] Raw response: {response.text[:500]}")
            
            # Return a structured error response
            return {
                's': 'error',
                'message': f'Invalid JSON response from Fyers: {str(json_err)}',
                'error': str(json_err),
                'status_code': response.status_code,
                'raw_response': response.text[:200],
                'parse_error': True
            }

    def _place_order_via_sdk(self, payload: Dict[str, Any], method_name: str = "place_order") -> Dict[str, Any]:
        """Fallback path using FYERS SDK client when REST returns auth/404/parse errors."""
        if not self.fyers_client:
            return {'success': False, 'error': 'Fyers SDK client not initialized'}

        try:
            # Different SDK versions accept either positional payload or data=payload
            try:
                sdk_resp = self.fyers_client.place_order(payload)  # type: ignore[misc]
            except TypeError:
                sdk_resp = self.fyers_client.place_order(data=payload)  # type: ignore[misc]

            logging.info(f"[{method_name}] SDK response: {sdk_resp}")
            if isinstance(sdk_resp, dict):
                ok = str(sdk_resp.get('s', '')).lower() == 'ok' or bool(sdk_resp.get('id'))
                if ok:
                    return {
                        'success': True,
                        'order_id': sdk_resp.get('id') or sdk_resp.get('data', {}).get('id'),
                        'response': sdk_resp,
                        'source': 'sdk'
                    }
                return {
                    'success': False,
                    'error': sdk_resp.get('message') or sdk_resp.get('error') or str(sdk_resp),
                    'response': sdk_resp,
                    'source': 'sdk'
                }

            return {'success': False, 'error': f'Unexpected SDK response type: {type(sdk_resp).__name__}', 'source': 'sdk'}
        except Exception as e:
            logging.error(f"[{method_name}] SDK fallback failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e), 'source': 'sdk'}
    
    def generate_auth_code_url(self) -> str:
        """
        Generate authorization URL for OAuth flow.
        User needs to open this URL in browser and login to get auth_code.
        
        Fyers OAuth 2.0 Flow:
        1. User clicks "Login with Fyers"
        2. Opens this auth URL in new window
        3. User logs in and grants permission
        4. Fyers redirects to our callback URL with auth code
        5. We exchange code for access token
        
        Returns:
            str: Authorization URL
        """
        try:
            if not self.app_id:
                logging.error("[generate_auth_code_url] APP_ID is missing")
                self.last_error = "APP_ID not configured"
                return ""
            
            # OAuth 2.0 parameters per Fyers API v3 spec
            state = "security_state_token"  # Should be random, but kept simple for now
            response_type = "code"
            
            # Construct auth URL - Fyers OAuth 2.0 endpoint
            # Reference: https://myapi.fyers.in/docsv3 -> Authentication section
            auth_url = (
                f"https://api-t1.fyers.in/api/v3/generate-authcode?"
                f"client_id={urllib.parse.quote(self.app_id)}&"
                f"redirect_uri={urllib.parse.quote(self.redirect_uri)}&"
                f"response_type={response_type}&"
                f"state={state}"
            )
            
            logging.info(f"[generate_auth_code_url] Generated auth URL for {self.app_id}")
            logging.debug(f"[generate_auth_code_url] Redirect URI: {self.redirect_uri}")
            
            return auth_url
            
        except Exception as e:
            logging.error(f"[generate_auth_code_url] Error: {e}")
            self.last_error = f"Failed to generate auth URL: {str(e)}"
            return ""
    
    def generate_access_token(self, auth_code: str) -> bool:
        """
        Generate access token from authorization code.
        
        Per Fyers API v3 spec: https://myapi.fyers.in/docsv3
        
        Args:
            auth_code: Authorization code from OAuth callback
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.app_id or not self.secret_key:
                self.last_error = "APP_ID or SECRET_KEY is missing from environment"
                logging.error(f"[generate_access_token] {self.last_error}")
                return False
            
            if not auth_code:
                self.last_error = "Authorization code is missing"
                logging.error(f"[generate_access_token] {self.last_error}")
                return False
            
            logging.info("[generate_access_token] Generating access token from auth code...")
            
            # Per Fyers API v3 docs: POST to /validate-authcode with appIdHash
            url = "https://api-t1.fyers.in/api/v3/validate-authcode"
            
            # Create appIdHash: SHA256(APP_ID:SECRET_KEY)
            app_id_hash = hashlib.sha256(f"{self.app_id}:{self.secret_key}".encode()).hexdigest()
            
            payload = {
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash,
                "code": auth_code
            }
            
            logging.debug(f"[generate_access_token] Request to {url}")
            logging.debug(f"[generate_access_token] Payload keys: {list(payload.keys())}")
            
            response = requests.post(url, json=payload, timeout=30)
            
            logging.info(f"[generate_access_token] Response status: {response.status_code}")
            
            data = self._parse_response(response, "generate_access_token")
            logging.debug(f"[generate_access_token] Response: {data}")
            
            # Fyers API returns status in 's' field: 'ok' or 'error'
            if response.status_code == 200 and data.get('s') == 'ok':
                access_token = data.get('access_token')
                
                if access_token:
                    # Store in format: APP_ID:token
                    self.access_token = f"{self.app_id}:{access_token}"
                    logging.info("[generate_access_token] ✅ Access token generated successfully!")
                    logging.debug(f"[generate_access_token] Token format: {self.app_id}:***")
                    
                    # Save to .env
                    self._save_token(access_token)
                    return True
                else:
                    self.last_error = "No access token in response from Fyers"
                    logging.error(f"[generate_access_token] {self.last_error}")
                    return False
            else:
                # Extract error message from response
                error_msg = data.get('message') or data.get('error') or 'Unknown error'
                
                # Handle specific Fyers error codes
                error_code = data.get('error_code')
                if error_code:
                    error_msg = f"(Code: {error_code}) {error_msg}"
                
                self.last_error = f"Token generation failed: {error_msg}"
                logging.error(f"[generate_access_token] {self.last_error}")
                logging.error(f"[generate_access_token] Full response: {data}")
                return False
                
        except requests.exceptions.Timeout:
            self.last_error = "Token generation request timed out (30s)"
            logging.error(f"[generate_access_token] {self.last_error}")
            return False
        except requests.exceptions.RequestException as e:
            self.last_error = f"Network error during token generation: {str(e)}"
            logging.error(f"[generate_access_token] {self.last_error}")
            return False
        except Exception as e:
            self.last_error = f"Unexpected error: {str(e)}"
            logging.error(f"[generate_access_token] {self.last_error}")
            import traceback
            logging.error(f"[generate_access_token] Traceback: {traceback.format_exc()}")
            return False
    
    def verify_token(self) -> bool:
        """
        Verify access token by fetching user profile.
        Tests that the token is valid and has proper permissions.
        
        Returns:
            bool: True if token is valid, False otherwise
        """
        try:
            if not self.access_token:
                self.last_error = "Access token is missing"
                logging.error(f"[verify_token] {self.last_error}")
                return False
            
            logging.info("[verify_token] Verifying Fyers access token...")
            
            # API endpoint to get user profile - validates token
            url = f"{self.base_url}/profile"
            headers = {
                "Authorization": self.access_token,
                "Content-Type": "application/json"
            }
            
            logging.debug(f"[verify_token] Request to {url}")
            
            response = requests.get(url, headers=headers, timeout=30)
            
            logging.info(f"[verify_token] Response status: {response.status_code}")
            
            data = self._parse_response(response, "verify_token")
            logging.debug(f"[verify_token] Response: {data}")
            
            # Fyers returns status in 's' field: 'ok' or 'error'
            if response.status_code == 200 and data.get('s') == 'ok':
                profile_data = data.get('data', {})
                client_id = profile_data.get('fy_id')
                name = profile_data.get('name')
                email = profile_data.get('email_id')
                account_type = profile_data.get('account_type')
                
                logging.info("[verify_token] ✅ Token verified successfully!")
                logging.info(f"[verify_token] Client ID: {client_id}")
                logging.info(f"[verify_token] Name: {name}")
                logging.info(f"[verify_token] Email: {email}")
                logging.info(f"[verify_token] Account Type: {account_type}")
                
                return True
            else:
                error_msg = data.get('message') or data.get('error') or 'Unknown error'
                error_code = data.get('error_code')
                
                if error_code:
                    error_msg = f"(Code: {error_code}) {error_msg}"
                
                self.last_error = f"Token verification failed: {error_msg}"
                logging.error(f"[verify_token] {self.last_error}")
                
                # Common error codes
                if error_code == 401:
                    self.last_error = "Unauthorized: Token may have expired or is invalid"
                elif error_code == 403:
                    self.last_error = "Forbidden: Token doesn't have required permissions"
                
                return False
                
        except requests.exceptions.Timeout:
            self.last_error = "Token verification timed out (30s)"
            logging.error(f"[verify_token] {self.last_error}")
            return False
        except requests.exceptions.RequestException as e:
            self.last_error = f"Network error during verification: {str(e)}"
            logging.error(f"[verify_token] {self.last_error}")
            return False
        except Exception as e:
            self.last_error = f"Verification error: {str(e)}"
            logging.error(f"[verify_token] {self.last_error}")
            return False
            logging.error(f"[verify_token] {self.last_error}")
            import traceback
            logging.error(f"[verify_token] Traceback: {traceback.format_exc()}")
            return False
    
    def place_order(self, symbol: str, side: int, quantity: int,
                   order_type: int = 2, product_type: str = 'INTRADAY',
                   limit_price: float = 0, stop_price: float = 0,
                   validity: str = 'DAY', disclosed_qty: int = 0,
                   offline_order: bool = False, stop_loss: float = 0,
                   take_profit: float = 0) -> Dict[str, Any]:
        """
        Place an order on Fyers platform.
        
        Args:
            symbol: Trading symbol (e.g., "NSE:SBIN-EQ", "NSE:NIFTY24JAN21000CE")
            side: 1 for BUY, -1 for SELL
            quantity: Number of shares/lots
            order_type: 1=LIMIT, 2=MARKET, 3=STOP_LOSS, 4=STOP_LOSS_MARKET
            product_type: 'INTRADAY', 'CNC', 'MARGIN', 'CO', 'BO'
            limit_price: Price for LIMIT orders
            stop_price: Trigger price for STOP_LOSS orders
            validity: 'DAY' or 'IOC'
            disclosed_qty: Disclosed quantity
            offline_order: True for after-market orders
            stop_loss: Stop loss price (for CO/BO)
            take_profit: Take profit price (for BO)
            
        Returns:
            Dict with order details and success status
        """
        try:
            if not self.access_token:
                logging.error("[place_order] Not authenticated. Call verify_token() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing access_token'
                }
            
            order_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Use REST API directly (more reliable than SDK for authentication)
            logging.info(f"[place_order] Using REST API to place order for symbol: {symbol}")
            
            url = f"{self.base_url}/orders"
            headers = {
                "Authorization": self.access_token,
                "Content-Type": "application/json"
            }
            
            payload = {
                "symbol": symbol,
                "qty": quantity,
                "type": order_type,
                "side": side,
                "productType": product_type,
                "limitPrice": limit_price,
                "stopPrice": stop_price,
                "validity": validity,
                "disclosedQty": disclosed_qty,
                "offlineOrder": offline_order,
                "stopLoss": stop_loss,
                "takeProfit": take_profit
            }
            
            logging.debug(f"[place_order] REST URL: {url}")
            logging.debug(f"[place_order] REST Headers: Authorization={self.access_token[:30]}..., Content-Type=application/json")
            logging.debug(f"[place_order] REST Payload: {payload}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            logging.info(f"[place_order] REST Response status: {response.status_code}")
            logging.debug(f"[place_order] REST Response body: {response.text}")
            
            data = self._parse_response(response, "place_order")
            
            logging.info(f"[place_order] REST Parsed response: {data}")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                order_id = data.get('id')
                
                logging.info(f"✅ {order_time} Fyers Order placed successfully. Order ID: {order_id} | "
                           f"Symbol: {symbol} @ ₹{limit_price} | Qty: {quantity}")
                
                return {
                    'success': True,
                    'order_id': order_id,
                    'symbol': symbol,
                    'price': limit_price,
                    'quantity': quantity,
                    'side': 'BUY' if side == 1 else 'SELL',
                    'order_type': order_type,
                    'product_type': product_type,
                    'timestamp': order_time,
                    'platform': 'FYERS'
                }
            else:
                # Handle error responses
                if response.status_code == 404:
                    error_msg = f"404 Not Found - Check API endpoint or authentication. Response: {data.get('raw_response', response.text)}"
                    logging.error(f"[place_order] 404 Error - Endpoint might be wrong or auth failed")
                    logging.error(f"[place_order] URL used: {url}")
                    logging.error(f"[place_order] Response: {response.text[:200]}")
                else:
                    error_msg = data.get('message') or data.get('error') or 'Unknown error'

                # Fallback to SDK on likely REST/auth compatibility issues
                should_try_sdk = (
                    response.status_code in (401, 403, 404)
                    or data.get('parse_error') is True
                    or 'authenticate' in str(error_msg).lower()
                    or 'unauthor' in str(error_msg).lower()
                )
                if should_try_sdk:
                    logging.warning(f"[place_order] REST failed ({error_msg}). Trying SDK fallback...")
                    sdk_result = self._place_order_via_sdk(payload, method_name="place_order")
                    if sdk_result.get('success'):
                        order_id = sdk_result.get('order_id')
                        logging.info(f"✅ {order_time} Fyers Order placed successfully via SDK. Order ID: {order_id}")
                        return {
                            'success': True,
                            'order_id': order_id,
                            'symbol': symbol,
                            'price': limit_price,
                            'quantity': quantity,
                            'side': 'BUY' if side == 1 else 'SELL',
                            'order_type': order_type,
                            'product_type': product_type,
                            'timestamp': order_time,
                            'platform': 'FYERS'
                        }
                    error_msg = sdk_result.get('error') or error_msg
                
                logging.error(f"❌ {order_time} Order placement failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'symbol': symbol,
                    'status_code': response.status_code,
                    'response': data
                }
                
        except Exception as e:
            logging.error(f"❌ Exception placing order for {symbol}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol,
                'exception': type(e).__name__
            }
    
    def place_basket_orders(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Place multiple orders in a basket (max 10 orders).
        
        Args:
            orders: List of order dictionaries (same format as place_order)
            
        Returns:
            Dict with basket order status
        """
        try:
            if not self.access_token:
                logging.error("[place_basket_orders] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing access_token'
                }
            
            if len(orders) > 10:
                return {
                    'success': False,
                    'error': 'Maximum 10 orders allowed in basket'
                }
            
            url = f"{self.base_url}/orders/multi"
            headers = {
                "Authorization": self.access_token,
                "Content-Type": "application/json"
            }
            
            logging.info(f"[place_basket_orders] Placing {len(orders)} orders")
            
            response = requests.post(url, headers=headers, json=orders, timeout=30)
            
            logging.info(f"[place_basket_orders] Response status: {response.status_code}")
            logging.debug(f"[place_basket_orders] Response body: {response.text}")
            
            data = self._parse_response(response, "place_basket_orders")
            
            logging.info(f"[place_basket_orders] Parsed response: {data}")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                logging.info(f"✅ Basket orders placed successfully")
                return {
                    'success': True,
                    'response': data
                }
            else:
                error_msg = data.get('message') or 'Unknown error'
                logging.error(f"❌ Basket order failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'response': data
                }
                
        except Exception as e:
            logging.error(f"❌ Exception placing basket orders: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def modify_order(self, order_id: str, order_type: Optional[int] = None,
                    limit_price: Optional[float] = None, quantity: Optional[int] = None) -> Dict[str, Any]:
        """
        Modify a pending order.
        
        Args:
            order_id: Order ID to modify
            order_type: New order type (optional)
            limit_price: New limit price (optional)
            quantity: New quantity (optional)
            
        Returns:
            Dict with modification status
        """
        try:
            if not self.access_token:
                logging.error("[modify_order] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing access_token'
                }
            
            url = f"{self.base_url}/orders"
            headers = {
                "Authorization": self.access_token,
                "Content-Type": "application/json"
            }
            
            payload: Dict[str, Any] = {"id": order_id}
            
            if order_type is not None:
                payload["type"] = order_type
            if limit_price is not None:
                payload["limitPrice"] = limit_price
            if quantity is not None:
                payload["qty"] = quantity
            
            logging.info(f"[modify_order] Modifying order {order_id}: {payload}")
            
            response = requests.put(url, headers=headers, json=payload, timeout=30)
            data = self._parse_response(response, "modify_order")
            
            logging.info(f"[modify_order] Response ({response.status_code}): {data}")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                logging.info(f"✅ Order {order_id} modified successfully")
                return {
                    'success': True,
                    'order_id': order_id,
                    'response': data
                }
            else:
                error_msg = data.get('message') or 'Unknown error'
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
            
            url = f"{self.base_url}/orders"
            headers = {
                "Authorization": self.access_token,
                "Content-Type": "application/json"
            }
            
            payload = {"id": order_id}
            
            logging.info(f"[cancel_order] Cancelling order {order_id}")
            
            response = requests.delete(url, headers=headers, json=payload, timeout=30)
            data = self._parse_response(response, "cancel_order")
            
            logging.info(f"[cancel_order] Response ({response.status_code}): {data}")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                logging.info(f"✅ Order {order_id} cancelled successfully")
                return {
                    'success': True,
                    'order_id': order_id,
                    'response': data
                }
            else:
                error_msg = data.get('message') or 'Unknown error'
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
    
    def place_stoploss_order(self, symbol: str, trigger_price: float, 
                            quantity: int, product_type: str = 'INTRADAY') -> Dict[str, Any]:
        """
        Place a stop loss (sell) order on Fyers platform.
        
        Creates a sell order with a trigger price that automatically executes
        when the price drops to the trigger level.
        
        Args:
            symbol: Trading symbol (e.g., "NSE:NIFTY24JAN21000CE")
            trigger_price: SL trigger price
            quantity: Order quantity
            product_type: 'INTRADAY', 'CNC', 'MARGIN', etc.
            
        Returns:
            Dict with success status, order_id, and details
        """
        try:
            # Validate credentials FIRST
            if not self.access_token:
                logging.error("[place_stoploss_order] Not authenticated. Missing access_token.")
                return {'success': False, 'error': 'Not authenticated - missing access_token'}
            
            if not isinstance(self.access_token, str) or len(self.access_token.strip()) < 10:
                logging.error(f"[place_stoploss_order] Invalid access_token format")
                return {'success': False, 'error': 'Invalid access_token format'}
            
            url = f"{self.base_url}/orders"
            headers = {
                "Authorization": self.access_token.strip(),
                "Content-Type": "application/json"
            }
            
            payload = {
                "symbol": symbol,
                "qty": int(quantity),
                "type": 4,  # 4 = STOP_LOSS_MARKET
                "side": self.SIDE_SELL,  # -1 = SELL
                "productType": product_type,
                "limitPrice": 0,
                "stopPrice": float(trigger_price),
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False
            }
            
            logging.info(f"[place_stoploss_order] Placing SL order: {symbol} @ {trigger_price:.2f}, Qty: {quantity}")
            logging.debug(f"[place_stoploss_order] Payload: {payload}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            data = self._parse_response(response, "place_stoploss_order")
            
            logging.debug(f"[place_stoploss_order] Response: {data}")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                order_id = data.get('data', {}).get('id') if isinstance(data.get('data'), dict) else data.get('id')
                if order_id:
                    logging.info(f"✅ SL Order placed: {order_id} | {symbol} @ {trigger_price:.2f}")
                    return {
                        'success': True,
                        'order_id': order_id,
                        'symbol': symbol,
                        'trigger_price': trigger_price,
                        'quantity': quantity,
                        'order_type': 'STOPLOSS'
                    }
            
            error_msg = data.get('message') or data.get('error') or 'Order placement failed'

            # Fallback to SDK on likely REST/auth compatibility issues
            should_try_sdk = (
                response.status_code in (401, 403, 404)
                or data.get('parse_error') is True
                or 'authenticate' in str(error_msg).lower()
                or 'unauthor' in str(error_msg).lower()
            )
            if should_try_sdk:
                logging.warning(f"[place_stoploss_order] REST failed ({error_msg}). Trying SDK fallback...")
                sdk_result = self._place_order_via_sdk(payload, method_name="place_stoploss_order")
                if sdk_result.get('success'):
                    order_id = sdk_result.get('order_id')
                    return {
                        'success': True,
                        'order_id': order_id,
                        'symbol': symbol,
                        'trigger_price': trigger_price,
                        'quantity': quantity,
                        'order_type': 'STOPLOSS'
                    }
                error_msg = sdk_result.get('error') or error_msg

            logging.error(f"❌ SL Order failed: {error_msg}")
            return {'success': False, 'error': error_msg, 'symbol': symbol}
            
        except Exception as e:
            logging.error(f"❌ Exception placing SL order: {e}", exc_info=True)
            return {'success': False, 'error': str(e), 'symbol': symbol}
    
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
            if not self.access_token:
                return {'success': False, 'error': 'Access token not available'}
            
            url = f"{self.base_url}/orders"
            headers = {
                "Authorization": self.access_token,
                "Content-Type": "application/json"
            }
            
            payload: Dict[str, Any] = {"id": order_id, "stopPrice": new_trigger_price}
            
            if quantity is not None:
                payload["qty"] = quantity
            
            logging.info(f"[modify_stoploss_order] Modifying SL order {order_id} to {new_trigger_price:.2f}")
            
            response = requests.put(url, headers=headers, json=payload, timeout=30)
            data = self._parse_response(response, "modify_stoploss_order")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                logging.info(f"✅ SL Order modified: {order_id} -> Trigger: {new_trigger_price:.2f}")
                return {
                    'success': True,
                    'order_id': order_id,
                    'new_trigger_price': new_trigger_price
                }
            
            error_msg = data.get('message') or 'Modification failed'
            logging.error(f"❌ Modify failed: {error_msg}")
            return {'success': False, 'error': error_msg, 'order_id': order_id}
            
        except Exception as e:
            logging.error(f"❌ Exception modifying SL order: {e}", exc_info=True)
            return {'success': False, 'error': str(e), 'order_id': order_id}
    
    def get_orderbook(self) -> Dict[str, Any]:
        """
        Get all orders for the day.
        
        Returns:
            Dict with list of orders
        """
        try:
            if not self.access_token:
                logging.error("[get_orderbook] Not authenticated.")
                return {
                    'success': False,
                    'error': 'Not authenticated'
                }
            
            url = f"{self.base_url}/orders"
            headers = {
                "Authorization": self.access_token
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = self._parse_response(response, "get_orderbook")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                orders = data.get('orderBook', [])
                logging.info(f"✅ Retrieved {len(orders)} orders")
                return {
                    'success': True,
                    'orders': orders
                }
            else:
                error_msg = data.get('message') or 'Unknown error'
                logging.error(f"❌ Failed to retrieve orders: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            logging.error(f"❌ Exception getting orderbook: {e}", exc_info=True)
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
                "Authorization": self.access_token
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = self._parse_response(response, "get_positions")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                positions = data.get('netPositions', [])
                logging.info(f"✅ Retrieved positions")
                return {
                    'success': True,
                    'positions': positions
                }
            else:
                error_msg = data.get('message') or 'Unknown error'
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
                "Authorization": self.access_token
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = self._parse_response(response, "get_holdings")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                holdings = data.get('holdings', [])
                logging.info(f"✅ Retrieved holdings")
                return {
                    'success': True,
                    'holdings': holdings
                }
            else:
                error_msg = data.get('message') or 'Unknown error'
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
            
            url = f"{self.base_url}/funds"
            headers = {
                "Authorization": self.access_token
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = self._parse_response(response, "get_funds")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                funds = data.get('fund_limit', [])
                logging.info(f"✅ Retrieved fund limits")
                return {
                    'success': True,
                    'funds': funds
                }
            else:
                error_msg = data.get('message') or 'Unknown error'
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
    
    def get_option_symbol(self, symbol: str, strike: int, option_type: str) -> str:
        """
        Get the trading symbol for an option on Fyers.
        
        Fyers uses format: NSE:NIFTY24JAN25650CE
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            
        Returns:
            Trading symbol or empty string if not found
        """
        try:
            # Fyers format: NSE:NIFTY24JAN25650CE
            # For now, use current month - in real implementation you'd fetch from API
            from datetime import datetime
            
            # Get current month and year
            now = datetime.now()
            month = now.strftime('%b').upper()
            year = now.strftime('%y')
            
            trading_symbol = f"NSE:{symbol}{year}{month}{strike}{option_type}"
            logging.info(f"✓ Option symbol: {trading_symbol}")
            return trading_symbol
            
        except Exception as e:
            logging.error(f"Error getting option symbol: {e}")
            return ""
    
    def verify_credentials(self) -> bool:
        """
        Verify that access token is available.
        
        Returns:
            True if credentials are valid, False otherwise
        """
        return bool(self.access_token)
    
    def _save_token(self, access_token: str):
        """Save access token to .env file (async/non-blocking)"""
        try:
            import threading
            
            def _async_save():
                try:
                    env_path = os.path.join(os.path.dirname(__file__), '../../../.env')
                    
                    if not os.path.exists(env_path):
                        logging.warning(f"[_save_token] .env file not found: {env_path}")
                        return
                    
                    with open(env_path, 'r') as f:
                        content = f.read()
                    
                    if 'FYERS_ACCESS_TOKEN=' in content:
                        lines = content.split('\n')
                        for i, line in enumerate(lines):
                            if line.startswith('FYERS_ACCESS_TOKEN='):
                                lines[i] = f'FYERS_ACCESS_TOKEN={access_token}'
                                break
                        content = '\n'.join(lines)
                    else:
                        content += f'\nFYERS_ACCESS_TOKEN={access_token}'
                    
                    with open(env_path, 'w') as f:
                        f.write(content)
                    
                    logging.info("[_save_token] ✓ Access token saved to .env")
                except Exception as e:
                    logging.warning(f"[_save_token] Failed to save: {e}")
            
            save_thread = threading.Thread(target=_async_save, daemon=True)
            save_thread.start()
            
        except Exception as e:
            logging.warning(f"[_save_token] Failed to start save thread: {e}")
