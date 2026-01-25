import logging
import os
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List
from datetime import datetime
import requests
import hashlib
import urllib.parse

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
        self.redirect_uri = os.getenv("FYERS_REDIRECT_URI", "https://127.0.0.1")
        
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
        
        logging.info("[FyersOrderService] Initialized with Fyers credentials")
        
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
    
    def generate_auth_code_url(self) -> str:
        """
        Generate authorization URL for OAuth flow.
        User needs to open this URL in browser and login to get auth_code.
        
        Returns:
            str: Authorization URL
        """
        try:
            if not self.app_id:
                logging.error("[generate_auth_code_url] APP_ID is missing")
                return ""
            
            state = "sample_state"
            response_type = "code"
            grant_type = "authorization_code"
            
            # Construct auth URL
            auth_url = (
                f"https://api-t1.fyers.in/api/v3/generate-authcode?"
                f"client_id={self.app_id}&"
                f"redirect_uri={urllib.parse.quote(self.redirect_uri)}&"
                f"response_type={response_type}&"
                f"state={state}"
            )
            
            logging.info(f"[generate_auth_code_url] Generated auth URL")
            return auth_url
            
        except Exception as e:
            logging.error(f"[generate_auth_code_url] Error: {e}")
            return ""
    
    def generate_access_token(self, auth_code: str) -> bool:
        """
        Generate access token from authorization code.
        
        Args:
            auth_code: Authorization code from OAuth callback
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.app_id or not self.secret_key:
                self.last_error = "APP_ID or SECRET_KEY is missing"
                logging.error(f"[generate_access_token] {self.last_error}")
                return False
            
            if not auth_code:
                self.last_error = "Authorization code is missing"
                logging.error(f"[generate_access_token] {self.last_error}")
                return False
            
            logging.info("[generate_access_token] Generating access token...")
            
            url = "https://api-t1.fyers.in/api/v3/validate-authcode"
            
            payload = {
                "grant_type": "authorization_code",
                "appIdHash": hashlib.sha256(f"{self.app_id}:{self.secret_key}".encode()).hexdigest(),
                "code": auth_code
            }
            
            response = requests.post(url, json=payload, timeout=30)
            data = response.json()
            
            logging.info(f"[generate_access_token] Response: {data}")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                access_token = data.get('access_token')
                if access_token:
                    self.access_token = f"{self.app_id}:{access_token}"
                    logging.info("[generate_access_token] ✅ Access token generated successfully!")
                    
                    # Save to .env
                    self._save_token(access_token)
                    return True
                else:
                    self.last_error = "No access token in response"
                    logging.error(f"[generate_access_token] {self.last_error}")
                    return False
            else:
                error_msg = data.get('message') or 'Unknown error'
                self.last_error = f"Token generation failed: {error_msg}"
                logging.error(f"[generate_access_token] {self.last_error}")
                return False
                
        except Exception as e:
            self.last_error = f"Token generation error: {str(e)}"
            logging.error(f"[generate_access_token] {self.last_error}")
            import traceback
            logging.error(f"[generate_access_token] Traceback: {traceback.format_exc()}")
            return False
    
    def verify_token(self) -> bool:
        """
        Verify access token by fetching user profile.
        
        Returns:
            bool: True if token is valid, False otherwise
        """
        try:
            if not self.access_token:
                self.last_error = "Access token is missing"
                logging.error(f"[verify_token] {self.last_error}")
                return False
            
            logging.info("[verify_token] Verifying Fyers credentials...")
            
            url = f"{self.base_url}/profile"
            headers = {
                "Authorization": self.access_token
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            data = response.json()
            
            logging.info(f"[verify_token] Response: {data}")
            
            if response.status_code == 200 and data.get('s') == 'ok':
                profile_data = data.get('data', {})
                client_id = profile_data.get('fy_id')
                name = profile_data.get('name')
                email = profile_data.get('email_id')
                
                logging.info("[verify_token] ✅ Credentials verified!")
                logging.info(f"[verify_token] Client ID: {client_id}")
                logging.info(f"[verify_token] Name: {name}")
                logging.info(f"[verify_token] Email: {email}")
                
                return True
            else:
                error_msg = data.get('message') or 'Unknown error'
                self.last_error = f"Verification failed: {error_msg}"
                logging.error(f"[verify_token] {self.last_error}")
                return False
                
        except Exception as e:
            self.last_error = f"Verification error: {str(e)}"
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
            
            logging.info(f"[place_order] Placing order: {payload}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            data = response.json()
            
            logging.info(f"[place_order] Response ({response.status_code}): {data}")
            
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
                error_msg = data.get('message') or 'Unknown error'
                logging.error(f"❌ {order_time} Order placement failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'symbol': symbol,
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
            data = response.json()
            
            logging.info(f"[place_basket_orders] Response ({response.status_code}): {data}")
            
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
            data = response.json()
            
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
            data = response.json()
            
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
            data = response.json()
            
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
            data = response.json()
            
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
            data = response.json()
            
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
            data = response.json()
            
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
    
    def _save_token(self, access_token: str):
        """Save access token to .env file"""
        try:
            env_path = os.path.join(os.path.dirname(__file__), '../../../.env')
            
            with open(env_path, 'r') as f:
                content = f.read()
            
            # Update access token
            if 'FYERS_ACCESS_TOKEN=' in content:
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if line.startswith('FYERS_ACCESS_TOKEN='):
                        lines[i] = f'FYERS_ACCESS_TOKEN={access_token}'
                content = '\n'.join(lines)
            else:
                content += f'\nFYERS_ACCESS_TOKEN={access_token}'
            
            with open(env_path, 'w') as f:
                f.write(content)
            
            logging.info("[_save_token] Saved access token to .env")
        except Exception as e:
            logging.warning(f"[_save_token] Failed to save: {e}")
