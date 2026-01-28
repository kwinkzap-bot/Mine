import logging
import os
from dotenv import load_dotenv
from typing import Dict, Any, Optional, List
from datetime import datetime
import requests

load_dotenv()

class KotakOrderService:
    """
    Service for placing orders in Kotak Neo trading platform using REST API.
    Handles order placement, execution, and tracking for options and futures.
    
    Official Docs: https://www.notion.so/Getting-started-15-min-28eda70d37e280a09158f091b369561e
    """
    
    def __init__(self, mobile_number: Optional[str] = None, ucc: Optional[str] = None, 
                 mpin: Optional[str] = None, totp_secret: Optional[str] = None,
                 access_token: Optional[str] = None):
        """
        Initialize KotakOrderService with Kotak Neo credentials.
        
        Args:
            mobile_number: Registered mobile number (without +91)
            ucc: Unique Client Code (5 characters, e.g., XV5PK)
            mpin: 6-digit trading PIN
            totp_secret: 6-digit OTP from authenticator app
            access_token: Kotak Neo API access token (from Kotak Neo portal)
        """
        self.mobile_number = mobile_number or os.getenv("KOTAK_MOBILE_NUMBER")
        self.ucc = ucc or os.getenv("KOTAK_UCC") or os.getenv("KOTAK_CLIENT_ID")
        self.mpin = mpin or os.getenv("KOTAK_MPIN") or os.getenv("KOTAK_PASSWORD")
        self.totp_secret = totp_secret or os.getenv("KOTAK_TOTP_SECRET")
        self.access_token = access_token or os.getenv("KOTAK_ACCESS_TOKEN")
        
        # Authentication tokens (from 2-step auth)
        self.view_token = None
        self.view_sid = None
        self.trading_token = None
        self.trading_sid = None
        self.base_url = None
        
        self.last_error = None
        
        logging.info("[KotakOrderService] Initialized with Kotak Neo credentials")
        
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
    
    def authenticate(self) -> bool:
        """
        Authenticate with Kotak Neo API using REST API (direct HTTP calls).
        Follows official 2-step flow:
          Step 1: TOTP Login → Get VIEW_TOKEN and VIEW_SID
          Step 2: MPIN Validate → Get TRADING_TOKEN, TRADING_SID, BASE_URL
        
        Official Documentation: https://www.notion.so/Getting-started-15-min-28eda70d37e280a09158f091b369561e
        
        Returns:
            bool: True if authentication successful, False otherwise
        """
        try:
            logging.info("[authenticate] =================================")
            logging.info("[authenticate] Starting Kotak Neo authentication")
            logging.info("[authenticate] =================================")
            
            # Validate required credentials
            if not self.access_token:
                self.last_error = "ACCESS_TOKEN is missing - get from Kotak Neo API Dashboard"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            if not self.mobile_number:
                self.last_error = "MOBILE_NUMBER is missing"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            if not self.ucc:
                self.last_error = "UCC (Client Code) is missing"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            if not self.mpin:
                self.last_error = "MPIN/PASSWORD is missing"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            if not self.totp_secret:
                self.last_error = "TOTP is missing - enter 6-digit code from authenticator app"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            # Clean credentials
            access_token_str = str(self.access_token).strip()
            mobile_str = str(self.mobile_number).strip()
            ucc_str = str(self.ucc).strip()
            mpin_str = str(self.mpin).strip()
            totp_str = str(self.totp_secret).strip()
            
            # Validate TOTP length (must be 6 digits)
            if len(totp_str) != 6 or not totp_str.isdigit():
                self.last_error = f"TOTP must be 6 digits (got: {len(totp_str)} characters)"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            # Validate MPIN length (must be 6 digits)
            if len(mpin_str) != 6 or not mpin_str.isdigit():
                self.last_error = f"MPIN must be 6 digits (got: {len(mpin_str)} characters)"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            logging.info(f"[authenticate] Access Token: {access_token_str[:20]}...")
            logging.info(f"[authenticate] Mobile: +91{mobile_str}")
            logging.info(f"[authenticate] UCC: {ucc_str}")
            logging.info(f"[authenticate] MPIN: {'*' * len(mpin_str)}")
            logging.info(f"[authenticate] TOTP: {totp_str}")
            
            # STEP 1: TOTP Login → Get VIEW_TOKEN and VIEW_SID
            logging.info("[authenticate] Step 1: TOTP Login...")
            login_url = "https://mis.kotaksecurities.com/login/1.0/tradeApiLogin"
            login_headers = {
                "Authorization": access_token_str,
                "neo-fin-key": "neotradeapi",
                "Content-Type": "application/json"
            }
            login_payload = {
                "mobileNumber": f"+91{mobile_str}",
                "ucc": ucc_str,
                "totp": totp_str
            }
            
            login_response = None
            try:
                login_response = requests.post(login_url, headers=login_headers, json=login_payload, timeout=30)
                login_data = login_response.json()
                
                logging.info(f"[authenticate] Login status code: {login_response.status_code}")
                logging.info(f"[authenticate] Login response: {login_data}")
                
                if login_response.status_code != 200:
                    # Extract error message from various possible formats
                    error_msg = (
                        login_data.get('message') or 
                        login_data.get('errMsg') or 
                        login_data.get('error') or
                        login_data.get('msg') or
                        f"HTTP {login_response.status_code}: {login_data}"
                    )
                    self.last_error = f"Login failed: {error_msg}"
                    logging.error(f"[authenticate] {self.last_error}")
                    logging.error(f"[authenticate] Full response: {login_response.text}")
                    return False
                
                if 'data' not in login_data:
                    # Check if response has error field
                    if 'error' in login_data or 'errMsg' in login_data:
                        error_msg = login_data.get('error') or login_data.get('errMsg')
                        self.last_error = f"Login failed: {error_msg}"
                        logging.error(f"[authenticate] {self.last_error}")
                        return False
                    
                    self.last_error = f"Login response missing 'data': {login_data}"
                    logging.error(f"[authenticate] {self.last_error}")
                    return False
                
                data = login_data['data']
                self.view_token = data.get('token')
                self.view_sid = data.get('sid')
                
                logging.info(f"[authenticate] ✓ TOTP Login successful")
                logging.info(f"[authenticate] VIEW_TOKEN: {self.view_token[:20] if self.view_token else 'None'}...")
                logging.info(f"[authenticate] VIEW_SID: {self.view_sid}")
                
            except requests.exceptions.RequestException as req_error:
                self.last_error = f"TOTP Login request failed: {str(req_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                logging.error(f"[authenticate] Request details - URL: {login_url}")
                logging.error(f"[authenticate] Headers: {login_headers}")
                logging.error(f"[authenticate] Payload: {login_payload}")
                return False
            except ValueError as json_error:
                self.last_error = f"TOTP Login response is not valid JSON: {str(json_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                logging.error(f"[authenticate] Response text: {login_response.text if login_response else 'N/A'}")
                return False
            except Exception as login_error:
                self.last_error = f"TOTP Login failed: {str(login_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                import traceback
                logging.error(f"[authenticate] Traceback: {traceback.format_exc()}")
                return False
            
            # STEP 2: MPIN Validate → Get TRADING_TOKEN, TRADING_SID, BASE_URL
            logging.info("[authenticate] Step 2: MPIN Validate...")
            validate_url = "https://mis.kotaksecurities.com/login/1.0/tradeApiValidate"
            validate_headers = {
                "Authorization": access_token_str,
                "neo-fin-key": "neotradeapi",
                "sid": self.view_sid,
                "Auth": self.view_token,
                "Content-Type": "application/json"
            }
            validate_payload = {
                "mpin": mpin_str
            }
            
            try:
                validate_response = requests.post(validate_url, headers=validate_headers, json=validate_payload, timeout=30)
                validate_data = validate_response.json()
                
                logging.info(f"[authenticate] Validate status code: {validate_response.status_code}")
                logging.info(f"[authenticate] Validate response: {validate_data}")
                
                if validate_response.status_code != 200:
                    error_msg = (
                        validate_data.get('message') or 
                        validate_data.get('errMsg') or 
                        validate_data.get('error') or
                        validate_data.get('msg') or
                        f"HTTP {validate_response.status_code}: {validate_data}"
                    )
                    self.last_error = f"MPIN validation failed: {error_msg}"
                    logging.error(f"[authenticate] {self.last_error}")
                    logging.error(f"[authenticate] Full response: {validate_response.text}")
                    return False
                
                if 'data' not in validate_data:
                    if 'error' in validate_data or 'errMsg' in validate_data:
                        error_msg = validate_data.get('error') or validate_data.get('errMsg')
                        self.last_error = f"MPIN validation failed: {error_msg}"
                        logging.error(f"[authenticate] {self.last_error}")
                        return False
                    
                    self.last_error = f"Validate response missing 'data': {validate_data}"
                    logging.error(f"[authenticate] {self.last_error}")
                    return False
                
                data = validate_data['data']
                self.trading_token = data.get('token')
                self.trading_sid = data.get('sid')
                self.base_url = data.get('baseUrl')
                
                logging.info("[authenticate] ✓ MPIN Validation successful!")
                logging.info(f"[authenticate] TRADING_TOKEN: {self.trading_token[:20] if self.trading_token else 'None'}...")
                logging.info(f"[authenticate] TRADING_SID: {self.trading_sid}")
                logging.info(f"[authenticate] BASE_URL: {self.base_url}")
                logging.info("[authenticate] =================================")
                logging.info("[authenticate] Authentication completed successfully")
                logging.info("[authenticate] =================================")
                
                # Save tokens to environment
                self._save_trading_token()
                
                return True
                
            except requests.exceptions.RequestException as req_error:
                self.last_error = f"MPIN Validate request failed: {str(req_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            except Exception as validate_error:
                self.last_error = f"MPIN Validate failed: {str(validate_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            # Check if authentication was successful
            self.last_error = "Authentication completed but no token received"
            logging.error(f"[authenticate] {self.last_error}")
            return False
                
        except Exception as e:
            self.last_error = f"Authentication error: {str(e)}"
            logging.error(f"[authenticate] Unexpected error: {str(e)}")
            logging.error(f"[authenticate] Exception type: {type(e).__name__}")
            import traceback
            logging.error(f"[authenticate] Traceback: {traceback.format_exc()}")
            return False
    
    def _save_trading_token(self):
        """Save trading tokens to .env file for future use"""
        try:
            env_path = os.path.join(os.path.dirname(__file__), '../../../.env')
            
            # Read current .env
            with open(env_path, 'r') as f:
                content = f.read()
            
            # Update or append tokens
            updates = {
                'KOTAK_TRADING_TOKEN': self.trading_token,
                'KOTAK_TRADING_SID': self.trading_sid,
                'KOTAK_BASE_URL': self.base_url
            }
            
            for key, value in updates.items():
                if f'{key}=' in content:
                    # Update existing line
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if line.startswith(f'{key}='):
                            lines[i] = f'{key}={value}'
                    content = '\n'.join(lines)
                else:
                    # Append new line
                    content += f'\n{key}={value}'
            
            with open(env_path, 'w') as f:
                f.write(content)
            
            logging.info("[_save_trading_token] Saved trading credentials to .env")
        except Exception as e:
            logging.warning(f"[_save_trading_token] Failed to save: {e}")
    
    def place_order(self, tradingsymbol: str, transaction_type: str, price: float,
                   quantity: int, order_type: str = 'MKT', product_type: str = 'MIS',
                   exchange_segment: str = 'nse_fo') -> Dict[str, Any]:
        """
        Place an order in Kotak Neo trading platform using neo_api_client.
        
        Args:
            tradingsymbol: Trading symbol (e.g., 'NIFTY24JAN25000CE')
            transaction_type: 'B' (Buy) or 'S' (Sell)
            price: Order price (used for limit orders, "0" for market orders)
            quantity: Order quantity
            order_type: 'MKT' (Market), 'L' (Limit), 'SL' (Stop Loss)
            product_type: 'MIS' (Intraday), 'CNC' (Delivery), 'NRML' (Normal/F&O)
            exchange_segment: 'nse_cm', 'nse_fo', 'bse_cm', 'bse_fo'
            
        Returns:
            Dict with order details and success status
        """
        try:
            if not self.trading_token or not self.base_url:
                logging.error("[place_order] Not authenticated. Call authenticate() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing trading_token or base_url',
                    'symbol': tradingsymbol
                }
            
            order_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # TODO: Implement REST API order placement
            # For now, return error indicating this needs implementation
            logging.error("[place_order] REST API order placement not yet implemented")
            return {
                'success': False,
                'error': 'Order placement via REST API not yet implemented. Use Kotak Neo app for now.',
                'symbol': tradingsymbol,
                'note': 'REST API implementation pending'
            }
            
            # TODO: Implement REST API order placement
            # Example implementation (needs completion):
            # response = requests.post(
            #     f"{self.base_url}/quick/order/rule/ms/place",
            #     headers={
            #         'Auth': self.trading_token,
            #         'Sid': self.trading_sid,
            #         'neo-fin-key': 'neotradeapi',
            #         'Content-Type': 'application/x-www-form-urlencoded'
            #     },
            #     data={
            #         'jData': json.dumps({
            #             'es': exchange_segment,
            #             'pc': product_type,
            #             'pr': str(price) if order_type != 'MKT' else "0",
            #             'pt': order_type,
            #             'qt': str(quantity),
            #             'rt': "DAY",
            #             'ts': tradingsymbol,
            #             'tt': transaction_type,
            #             'am': "NO",
            #             'dq': "0",
            #             'mp': "0",
            #             'pf': "N",
            #             'tp': "0"
            #         })
            #     }
            # )
                
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
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' (Call) or 'PE' (Put)
            transaction_type: 'BUY' or 'SELL'
            quantity: Order quantity
            sl_price: Stop Loss price (optional)
            target_price: Target price (optional)
            
        Returns:
            Dict with order details and success status
        """
        try:
            # Construct Kotak Neo option symbol format
            # Example: NIFTY24JAN25000CE (for NIFTY Jan 2024 25000 Call)
            tradingsymbol = self._build_option_symbol(symbol, strike, option_type)
            
            if not tradingsymbol:
                return {
                    'success': False,
                    'error': f'Could not construct option symbol for {symbol} {strike} {option_type}',
                    'symbol': symbol,
                    'strike': strike,
                    'option_type': option_type
                }
            
            # Get current market price for the option
            price = self._get_option_price(tradingsymbol)
            
            if not price:
                logging.warning(f"[place_option_order] Could not fetch price for {tradingsymbol}, using default 0")
                price = 0.0
            
            # Place the order with product type NRML (Normal for F&O)
            result = self.place_order(
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                price=price,
                quantity=quantity,
                order_type=self.ORDER_TYPE_MARKET,
                product_type=self.PRODUCT_NRML
            )
            
            # Note: sl_price and target_price are not directly supported by neo_api_client.place_order()
            # They would need to be placed as separate stop-loss/target orders if required
            
            if result['success']:
                result['option_type'] = option_type
                result['strike'] = strike
                result['underlying'] = symbol
            
            return result
            
        except Exception as e:
            logging.error(f"[place_option_order] Error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol,
                'strike': strike,
                'option_type': option_type,
                'platform': 'KOTAK_NEO'
            }
    
    def modify_order(self, order_id: str, price: Optional[float] = None,
                    quantity: Optional[int] = None) -> Dict[str, Any]:
        """
        Modify an existing order in Kotak Neo.
        
        Args:
            order_id: Order ID to modify
            price: New price (for limit orders)
            quantity: New quantity
            
        Returns:
            Dict with modification status
        """
        try:
            if not self.trading_token or not self.base_url:
                logging.error("[modify_order] Not authenticated. Call authenticate() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing trading_token or base_url'
                }
            
            # TODO: Implement REST API order modification
            logging.error("[modify_order] REST API order modification not yet implemented")
            return {
                'success': False,
                'error': 'Order modification via REST API not yet implemented'
            }
            modify_response = self.client.modify_order(  # type: ignore[attr-defined]
                order_id=order_id,
                price=str(price) if price else None,
                quantity=quantity
            )
            
            logging.info(f"✅ Order {order_id} modified successfully")
            return {
                'success': True,
                'order_id': order_id,
                'message': 'Order modified successfully',
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
        """
        Cancel an existing order in Kotak Neo.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            Dict with cancellation status
        """
        try:
            if not self.trading_token or not self.base_url:
                logging.error("[cancel_order] Not authenticated. Call authenticate() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing trading_token or base_url'
                }
            
            # TODO: Implement REST API order cancellation
            logging.error("[cancel_order] REST API order cancellation not yet implemented")
            return {
                'success': False,
                'error': 'Order cancellation via REST API not yet implemented'
            }
            cancel_response = self.client.cancel_order(order_id=order_id)  # type: ignore[attr-defined]
            
            logging.info(f"✅ Order {order_id} cancelled successfully")
            return {
                'success': True,
                'order_id': order_id,
                'message': 'Order cancelled successfully',
                'data': cancel_response
            }
            
        except Exception as e:
            logging.error(f"[cancel_order] Error: {e}", exc_info=True)
            return {
                'success': False,
                'order_id': order_id,
                'error': str(e)
            }
    
    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """
        Get status of an order from Kotak Neo.
        
        Args:
            order_id: Order ID to check
            
        Returns:
            Dict with order status details
        """
        try:
            if not self.trading_token or not self.base_url:
                logging.error("[get_holdings] Not authenticated. Call authenticate() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing trading_token or base_url'
                }
            
            # TODO: Implement REST API holdings retrieval
            logging.error("[get_holdings] REST API holdings not yet implemented")
            return {
                'success': False,
                'error': 'Holdings retrieval via REST API not yet implemented'
            }
            order_status = self.client.order_report()  # type: ignore[attr-defined]
            
            # Filter for specific order_id
            if isinstance(order_status, list):
                for order in order_status:
                    if order.get('nOrdNo') == order_id or order.get('orderId') == order_id:
                        return {
                            'success': True,
                            'order_id': order_id,
                            'data': order
                        }
            
            return {
                'success': True,
                'order_id': order_id,
                'data': order_status
            }
                
        except Exception as e:
            logging.error(f"[get_order_status] Error: {e}", exc_info=True)
            return {
                'success': False,
                'order_id': order_id,
                'error': str(e)
            }
    
    def get_positions(self) -> Dict[str, Any]:
        """
        Get current positions from Kotak Neo.
        
        Returns:
            Dict with list of open positions
        """
        try:
            if not self.trading_token or not self.base_url:
                logging.error("[get_positions] Not authenticated. Call authenticate() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing trading_token or base_url'
                }
            
            # TODO: Implement REST API positions retrieval
            logging.error("[get_positions] REST API positions not yet implemented")
            return {
                'success': False,
                'error': 'Positions retrieval via REST API not yet implemented'
            }
            positions_data = self.client.positions()  # type: ignore[attr-defined]
            
            return {
                'success': True,
                'positions': positions_data if isinstance(positions_data, list) else [],
                'count': len(positions_data) if isinstance(positions_data, list) else 0,
                'data': positions_data
            }
                
        except Exception as e:
            logging.error(f"[get_positions] Error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_order_history(self, limit: int = 10) -> Dict[str, Any]:
        """
        Get order history from Kotak Neo.
        
        Args:
            limit: Number of recent orders to retrieve
            
        Returns:
            Dict with list of orders
        """
        try:
            if not self.trading_token or not self.base_url:
                logging.error("[get_order_book] Not authenticated. Call authenticate() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing trading_token or base_url'
                }
            
            # TODO: Implement REST API order book retrieval
            logging.error("[get_order_book] REST API order book not yet implemented")
            return {
                'success': False,
                'error': 'Order book retrieval via REST API not yet implemented'
            }
            order_history = self.client.order_report()  # type: ignore[attr-defined]
            
            # Limit results if needed
            if isinstance(order_history, list) and len(order_history) > limit:
                order_history = order_history[:limit]
            
            return {
                'success': True,
                'orders': order_history if isinstance(order_history, list) else [],
                'count': len(order_history) if isinstance(order_history, list) else 0,
                'data': order_history
            }
                
        except Exception as e:
            logging.error(f"[get_order_history] Error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def _build_option_symbol(self, symbol: str, strike: int, option_type: str) -> Optional[str]:
        """
        Build Kotak Neo option symbol from components.
        
        Example: NIFTY24JAN25000CE (NIFTY Jan 2024 25000 Call)
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            
        Returns:
            Formatted option symbol or None if invalid
        """
        try:
            if option_type not in ['CE', 'PE']:
                logging.error(f"[_build_option_symbol] Invalid option type: {option_type}")
                return None
            
            # Get current date for contract expiry (typically current month)
            now = datetime.now()
            year_suffix = str(now.year)[2:]  # Last 2 digits of year
            month = now.strftime('%b').upper()  # e.g., 'JAN', 'FEB', etc.
            
            # Build symbol in format: NIFTY24JAN25000CE
            option_symbol = f"{symbol}{year_suffix}{month}{strike:05d}{option_type}"
            
            logging.debug(f"[_build_option_symbol] Built symbol: {option_symbol}")
            return option_symbol
            
        except Exception as e:
            logging.error(f"[_build_option_symbol] Error: {e}", exc_info=True)
            return None
    
    def _get_option_price(self, tradingsymbol: str) -> Optional[float]:
        """
        Get current market price for an option symbol.
        
        Args:
            tradingsymbol: Option trading symbol (e.g., 'NIFTY24JAN25000CE')
            
        Returns:
            Current market price or None if unavailable
        """
        try:
            if not self.trading_token or not self.base_url:
                logging.warning("[_get_option_price] Not authenticated. Cannot fetch price.")
                return None
            
            # TODO: Implement REST API quote retrieval
            logging.warning("[_get_option_price] REST API quotes not yet implemented")
            return None
            
            if quote_data and isinstance(quote_data, dict):
                symbol_data = quote_data.get(tradingsymbol)
                if isinstance(symbol_data, dict):
                    price = symbol_data.get('ltp') or symbol_data.get('last_price')
                    
                    if price:
                        logging.debug(f"[_get_option_price] {tradingsymbol}: ₹{price}")
                        return float(price)
            
            logging.warning(f"[_get_option_price] Could not fetch price for {tradingsymbol}")
            return None
            
        except Exception as e:
            logging.warning(f"[_get_option_price] Error fetching price: {e}")
            return None
