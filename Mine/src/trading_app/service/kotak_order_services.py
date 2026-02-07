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
            ucc_str = str(self.ucc).strip().upper()  # UCC is typically uppercase
            mpin_str = str(self.mpin).strip()
            totp_str = str(self.totp_secret).strip()
            
            # Remove +91 from mobile if already present
            if mobile_str.startswith('+91'):
                mobile_str = mobile_str[3:]
            elif mobile_str.startswith('91'):
                mobile_str = mobile_str[2:]
            
            # Validate mobile number (10 digits)
            if len(mobile_str) != 10 or not mobile_str.isdigit():
                self.last_error = f"Mobile must be 10 digits (got: {len(mobile_str)} characters: {mobile_str})"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            # Validate TOTP/OTP length (must be 6 digits)
            if len(totp_str) != 6 or not totp_str.isdigit():
                self.last_error = f"OTP must be 6 digits (got: {len(totp_str)} characters). OTP expires after 30 seconds."
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            # Validate MPIN length (must be 6 digits)
            if len(mpin_str) != 6 or not mpin_str.isdigit():
                self.last_error = f"MPIN must be 6 digits (got: {len(mpin_str)} characters: {mpin_str})"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            # Validate UCC length (typically 5 characters)
            if len(ucc_str) < 3 or len(ucc_str) > 10:
                self.last_error = f"UCC must be 3-10 characters (got: {len(ucc_str)} characters: {ucc_str})"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            logging.info(f"[authenticate] Access Token: {access_token_str[:20]}...")
            logging.info(f"[authenticate] Mobile: +91{mobile_str}")
            logging.info(f"[authenticate] UCC: {ucc_str}")
            logging.info(f"[authenticate] MPIN: {'*' * len(mpin_str)}")
            logging.info(f"[authenticate] OTP: {totp_str[:2]}****")
            
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
                        f"HTTP {login_response.status_code}"
                    )
                    self.last_error = f"Step 1 Login failed: {error_msg}"
                    logging.error(f"[authenticate] {self.last_error}")
                    logging.error(f"[authenticate] Full response: {login_response.text}")
                    return False
                
                # Check if successful (status 200)
                if login_response.status_code == 200:
                    # Response structure: {"success": true, "data": {"token": "...", "sid": "..."}}
                    if 'data' in login_data:
                        data = login_data['data']
                        self.view_token = data.get('token') or data.get('Authorization')
                        self.view_sid = data.get('sid')
                        
                        if not self.view_token or not self.view_sid:
                            self.last_error = f"Step 1: Token/SID missing from response: {data}"
                            logging.error(f"[authenticate] {self.last_error}")
                            return False
                        
                        logging.info(f"[authenticate] ✓ Step 1 successful")
                        logging.info(f"[authenticate] VIEW_TOKEN: {self.view_token[:20]}...")
                        logging.info(f"[authenticate] VIEW_SID: {self.view_sid}")
                    else:
                        # Check for error in response
                        if 'error' in login_data or 'errMsg' in login_data or 'message' in login_data:
                            error_msg = (login_data.get('error') or 
                                       login_data.get('errMsg') or 
                                       login_data.get('message', 'Unknown error'))
                            self.last_error = f"Step 1: {error_msg}"
                            logging.error(f"[authenticate] {self.last_error}")
                            return False
                        
                        self.last_error = f"Step 1: Invalid response structure: {login_data}"
                        logging.error(f"[authenticate] {self.last_error}")
                        return False
                else:
                    self.last_error = f"Step 1: HTTP {login_response.status_code}"
                    logging.error(f"[authenticate] {self.last_error}")
                    return False
                
            except requests.exceptions.Timeout:
                self.last_error = f"Step 1: Request timeout (30s) - Kotak API not responding"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            except requests.exceptions.ConnectionError as conn_error:
                self.last_error = f"Step 1: Connection error - {str(conn_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            except requests.exceptions.RequestException as req_error:
                self.last_error = f"Step 1: Request failed - {str(req_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                logging.error(f"[authenticate] URL: {login_url}")
                return False
            except ValueError as json_error:
                self.last_error = f"Step 1: Invalid JSON response - {str(json_error)}"
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
            
            # Check if Step 1 tokens are available
            if not self.view_token or not self.view_sid:
                self.last_error = "Step 2: Cannot proceed - Step 1 tokens missing"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            
            validate_url = "https://mis.kotaksecurities.com/login/1.0/tradeApiValidate"
            validate_headers = {
                "Authorization": access_token_str,
                "neo-fin-key": "neotradeapi",
                "sid": str(self.view_sid),
                "Auth": str(self.view_token),
                "Content-Type": "application/json"
            }
            validate_payload = {
                "mpin": mpin_str
            }
            
            logging.debug(f"[authenticate] Step 2 headers: {validate_headers}")
            logging.debug(f"[authenticate] Step 2 payload: {validate_payload}")
            
            validate_response = None
            try:
                validate_response = requests.post(validate_url, headers=validate_headers, json=validate_payload, timeout=30)
                validate_data = validate_response.json()
                
                logging.info(f"[authenticate] Step 2 status code: {validate_response.status_code}")
                logging.info(f"[authenticate] Step 2 response: {validate_data}")
                
                if validate_response.status_code != 200:
                    error_msg = (
                        validate_data.get('message') or 
                        validate_data.get('errMsg') or 
                        validate_data.get('error') or
                        validate_data.get('msg') or
                        f"HTTP {validate_response.status_code}"
                    )
                    self.last_error = f"Step 2: {error_msg}"
                    logging.error(f"[authenticate] {self.last_error}")
                    logging.error(f"[authenticate] Full response: {validate_response.text}")
                    return False
                
                # Check response structure
                if validate_response.status_code == 200:
                    if 'data' in validate_data:
                        data = validate_data['data']
                        self.trading_token = data.get('token') or data.get('Authorization')
                        self.trading_sid = data.get('sid')
                        self.base_url = data.get('baseUrl') or data.get('base_url')
                        
                        # Validate that all required tokens were received
                        if not self.trading_token:
                            self.last_error = f"Step 2: TRADING_TOKEN missing from response"
                            logging.error(f"[authenticate] {self.last_error}")
                            return False
                        
                        if not self.trading_sid:
                            self.last_error = f"Step 2: TRADING_SID missing from response"
                            logging.error(f"[authenticate] {self.last_error}")
                            return False
                        
                        if not self.base_url:
                            self.last_error = f"Step 2: BASE_URL missing from response"
                            logging.error(f"[authenticate] {self.last_error}")
                            return False
                        
                        logging.info("[authenticate] ✓ Step 2 successful!")
                        logging.info(f"[authenticate] TRADING_TOKEN: {self.trading_token[:20]}...")
                        logging.info(f"[authenticate] TRADING_SID: {self.trading_sid}")
                        logging.info(f"[authenticate] BASE_URL: {self.base_url}")
                        logging.info("[authenticate] =================================")
                        logging.info("[authenticate] ✅ Authentication SUCCESSFUL!")
                        logging.info("[authenticate] =================================")
                        
                        # Save tokens to environment
                        self._save_trading_token()
                        
                        return True
                    else:
                        # Check for error in response
                        if 'error' in validate_data or 'errMsg' in validate_data or 'message' in validate_data:
                            error_msg = (validate_data.get('error') or 
                                       validate_data.get('errMsg') or 
                                       validate_data.get('message', 'Unknown error'))
                            self.last_error = f"Step 2: {error_msg}"
                            logging.error(f"[authenticate] {self.last_error}")
                            return False
                        
                        self.last_error = f"Step 2: Invalid response structure: {validate_data}"
                        logging.error(f"[authenticate] {self.last_error}")
                        return False
                else:
                    self.last_error = f"Step 2: HTTP {validate_response.status_code}"
                    logging.error(f"[authenticate] {self.last_error}")
                    return False
                
            except requests.exceptions.Timeout:
                self.last_error = f"Step 2: Request timeout (30s) - Kotak API not responding"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            except requests.exceptions.ConnectionError as conn_error:
                self.last_error = f"Step 2: Connection error - {str(conn_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            except requests.exceptions.RequestException as req_error:
                self.last_error = f"Step 2: Request failed - {str(req_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                return False
            except ValueError as json_error:
                self.last_error = f"Step 2: Invalid JSON response - {str(json_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                logging.error(f"[authenticate] Response text: {validate_response.text if validate_response else 'N/A'}")
                return False
            except Exception as validate_error:
                self.last_error = f"Step 2: Unexpected error - {str(validate_error)}"
                logging.error(f"[authenticate] {self.last_error}")
                import traceback
                logging.error(f"[authenticate] Traceback: {traceback.format_exc()}")
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
    
    def diagnose_authentication(self) -> Dict[str, Any]:
        """
        Diagnose authentication issues by checking credentials and endpoints.
        
        Returns:
            Dict with diagnostic information
        """
        logging.info("[diagnose] Starting authentication diagnostic...")
        
        result = {
            'credentials_present': {},
            'validation_errors': [],
            'suggestions': []
        }
        
        # Check each required credential
        creds = {
            'ACCESS_TOKEN': (self.access_token, 'API access token from Kotak Neo portal'),
            'MOBILE_NUMBER': (self.mobile_number, '10-digit phone number without +91'),
            'UCC': (self.ucc, 'Unique Client Code (5 characters)'),
            'MPIN': (self.mpin, '6-digit trading PIN'),
            'TOTP/OTP': (self.totp_secret, '6-digit OTP from authenticator (expires in 30 sec)')
        }
        
        for name, (value, desc) in creds.items():
            present = bool(value)
            result['credentials_present'][name] = present
            
            if not present:
                result['validation_errors'].append(f"{name} is missing: {desc}")
                result['suggestions'].append(f"1) Add {name} to .env file, 2) Or pass it when creating KotakOrderService")
        
        # Validate format of each credential
        if self.mobile_number:
            mobile_clean = str(self.mobile_number).strip()
            if mobile_clean.startswith(('+91', '91')):
                mobile_clean = mobile_clean[-10:] if len(mobile_clean) >= 10 else mobile_clean
            
            if len(mobile_clean) != 10 or not mobile_clean.isdigit():
                result['validation_errors'].append(f"MOBILE_NUMBER format invalid: must be 10 digits, got: {mobile_clean}")
        
        if self.mpin:
            mpin_clean = str(self.mpin).strip()
            if len(mpin_clean) != 6 or not mpin_clean.isdigit():
                result['validation_errors'].append(f"MPIN format invalid: must be 6 digits, got {len(mpin_clean)} chars")
        
        if self.totp_secret:
            totp_clean = str(self.totp_secret).strip()
            if len(totp_clean) != 6 or not totp_clean.isdigit():
                result['validation_errors'].append(f"OTP format invalid: must be 6 digits, got {len(totp_clean)} chars. OTP expires after 30 seconds!")
            else:
                result['suggestions'].append("OTP is valid format but may have expired - use fresh OTP from authenticator app")
        
        # Test connectivity
        try:
            response = requests.get("https://mis.kotaksecurities.com/", timeout=5)
            result['endpoint_reachable'] = response.status_code < 500
            logging.info(f"[diagnose] Kotak API endpoint: {response.status_code}")
        except Exception as e:
            result['endpoint_reachable'] = False
            result['validation_errors'].append(f"Cannot reach Kotak API: {str(e)}")
            result['suggestions'].append("Check internet connection or firewall settings")
        
        logging.info(f"[diagnose] Diagnostic results: {result}")
        return result
    
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
    
    def place_stoploss_order(self, symbol: str, trigger_price: float, 
                            quantity: int, product_type: str = 'MIS') -> Dict[str, Any]:
        """
        Place a stop loss (sell) order on Kotak Neo platform.
        
        Creates a sell order with a trigger price that automatically executes
        when the price drops to the trigger level.
        
        Args:
            symbol: Trading symbol (e.g., "NIFTY24JAN21000CE")
            trigger_price: SL trigger price
            quantity: Order quantity
            product_type: 'MIS', 'CNC', 'NRML'
            
        Returns:
            Dict with success status, order_id, and details
        """
        try:
            if not self.trading_token or not self.base_url:
                logging.error("[place_stoploss_order] Not authenticated. Call authenticate() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing trading_token'
                }
            
            url = f"{self.base_url}/orders"
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": self.trading_token
            }
            
            payload = {
                "symbol": symbol,
                "quantity": quantity,
                "orderType": "SL",  # Stop Loss order
                "transactionType": "SELL",
                "productType": product_type,
                "triggerPrice": trigger_price,
                "validity": "DAY"
            }
            
            logging.info(f"[place_stoploss_order] Placing SL order: {symbol} @ {trigger_price:.2f}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if response.status_code in [200, 201]:
                # Kotak typically returns order ID in response
                order_id = data.get('orderId') or data.get('data', {}).get('orderId')
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
            
            error_msg = data.get('message') or 'Order placement failed'
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
            if not self.trading_token or not self.base_url:
                logging.error("[modify_stoploss_order] Not authenticated. Call authenticate() first.")
                return {
                    'success': False,
                    'error': 'Not authenticated - missing trading_token'
                }
            
            url = f"{self.base_url}/orders/{order_id}"
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": self.trading_token
            }
            
            payload: Dict[str, Any] = {
                "orderId": order_id,
                "triggerPrice": new_trigger_price
            }
            
            if quantity is not None:
                payload["quantity"] = quantity
            
            logging.info(f"[modify_stoploss_order] Modifying SL order {order_id} to {new_trigger_price:.2f}")
            
            response = requests.put(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if response.status_code in [200, 201]:
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
        Get the trading symbol for an option on Kotak Neo.
        
        Kotak format: NIFTY24JAN25650CE (without exchange prefix)
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            
        Returns:
            Trading symbol or empty string if not found
        """
        try:
            # Get current month and year
            from datetime import datetime
            now = datetime.now()
            year = now.strftime('%y')
            month = now.strftime('%b').upper()
            
            # Kotak format: NIFTY24JAN25650CE
            trading_symbol = f"{symbol}{year}{month}{strike}{option_type}"
            logging.info(f"✓ Option symbol: {trading_symbol}")
            return trading_symbol
            
        except Exception as e:
            logging.error(f"Error getting option symbol: {e}")
            return ""
    
    def verify_credentials(self) -> bool:
        """
        Verify that access token and authentication are valid.
        
        Returns:
            True if authenticated, False otherwise
        """
        return bool(self.trading_token and self.base_url)
