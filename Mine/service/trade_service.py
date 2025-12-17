from kiteconnect import KiteConnect
from datetime import datetime
import calendar
from typing import Dict, Any, Optional, Tuple

class TradeService:
    def __init__(self, kite_instance: KiteConnect):
        self.kite: KiteConnect = kite_instance
        self.active_orders: Dict[str, Dict[str, Any]] = {}
    
    def _get_current_month_future(self, symbol: str) -> Optional[str]:
        """Finds the next immediate future contract for a given symbol."""
        try:
            instruments = self.kite.instruments('NFO')
            now = datetime.now()
            
            # 1. Determine the relevant expiry month/year
            current_month_num = now.month
            current_year = now.year
            
            # Get last Thursday of current month
            last_day = calendar.monthrange(current_year, current_month_num)[1]
            last_thursday_day = max(day for day in range(last_day, 0, -1) 
                              if datetime(current_year, current_month_num, day).weekday() == 3)
            
            # If today is past the expiry date, roll to the next month
            if now.date() > datetime(current_year, current_month_num, last_thursday_day).date():
                current_month_num = current_month_num + 1 if current_month_num < 12 else 1
                current_year = current_year if now.month < 12 else current_year + 1

            # Format month and year for tradingsymbol matching
            target_month_abbr = datetime(current_year, current_month_num, 1).strftime('%b').upper()
            target_year_abbr = str(current_year)[-2:]
            
            # 2. Find matching future contract
            for inst in instruments:
                if (inst['instrument_type'] == 'FUT' and 
                    inst['name'] == symbol and
                    target_month_abbr in inst['tradingsymbol'] and
                    target_year_abbr in inst['tradingsymbol']):
                    return inst['tradingsymbol']
            
            return None
        except Exception as e:
            print(f"Error getting future symbol: {e}")
            return None
    
    def place_order(self, symbol: str, transaction_type: str, quantity: int, order_type: str = 'MARKET', product: str = 'MIS') -> Optional[str]:
        """Places a market order for the nearest future contract."""
        try:
            future_symbol = self._get_current_month_future(symbol)
            if not future_symbol:
                print(f"Could not find current month future for {symbol}")
                return None
            
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=future_symbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=product,
                order_type=order_type
            )
            self.active_orders[symbol] = {'order_id': order_id, 'type': transaction_type, 'future_symbol': future_symbol}
            print(f"Order placed: {transaction_type} {quantity} {future_symbol}, Order ID: {order_id}")
            return str(order_id) # Ensure order_id is returned as a string/int
        except Exception as e:
            print(f"Error placing order: {e}")
            return None
    
    def check_position_exists(self, symbol: str) -> Tuple[bool, int]:
        """Checks if an active position exists and returns quantity."""
        try:
            future_symbol = self._get_current_month_future(symbol)
            if not future_symbol:
                return False, 0
            
            positions = self.kite.positions()
            net_positions = positions.get('net', [])
            for pos in net_positions:
                if pos['tradingsymbol'] == future_symbol and pos['quantity'] != 0:
                    return True, pos['quantity']
            return False, 0
        except Exception as e:
            print(f"Error checking position: {e}")
            return False, 0
    
    def exit_position(self, symbol: str) -> Optional[str]:
        """Exits any open position for the symbol."""
        exists, quantity = self.check_position_exists(symbol)
        if not exists:
            print(f"No position exists for {symbol}")
            return None
        
        # Determine the opposite transaction type
        transaction_type = self.kite.TRANSACTION_TYPE_SELL if quantity > 0 else self.kite.TRANSACTION_TYPE_BUY
        
        # Place an order to square off the absolute quantity
        return self.place_order(symbol, transaction_type, abs(quantity))