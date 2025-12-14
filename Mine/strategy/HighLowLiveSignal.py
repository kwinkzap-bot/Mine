import pandas as pd
import time
import schedule
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
import os
from dotenv import load_dotenv

import sys
import importlib.util

# Fix import issues
try:
    from strategy.HighLowSignal import HighLowSignal
except ImportError:
    try:
        from .HighLowSignal import HighLowSignal
    except ImportError:
        # Fallback import
        current_dir = os.path.dirname(os.path.abspath(__file__))
        signal_path = os.path.join(current_dir, 'HighLowSignal.py')
        spec = importlib.util.spec_from_file_location("HighLowSignal", signal_path)
        signal_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(signal_module)
        HighLowSignal = signal_module.HighLowSignal

load_dotenv()

class HighLowLiveSignal:
    def __init__(self, kite_instance=None, symbol='NIFTY'):
        if kite_instance:
            self.kite = kite_instance
        else:
            api_key = os.getenv("API_KEY")
            access_token = os.getenv("ACCESS_TOKEN")
            self.kite = KiteConnect(api_key=api_key)
            self.kite.set_access_token(access_token)
        
        self.symbol = symbol
        self.signal_detector = HighLowSignal()
        self.instruments = None
        self.active_position = None
        self.ce_strike = None
        self.pe_strike = None
        self.ce_prev_high = None
        self.ce_prev_low = None
        self.pe_prev_high = None
        self.pe_prev_low = None
        self.order_quantity = 50  # Default quantity
        self.live_trading = True  # Enable/disable live trading
        
    def get_strike_prices(self, close_price):
        """Calculate strike prices based on close price"""
        close_int = int(close_price)
        
        if self.symbol == 'BANKNIFTY':
            rounded_base = round(close_price / 100) * 100
            ce_strike = int(rounded_base - 300)
            pe_strike = int(rounded_base + 300)
        else:
            last_two_digits = close_int % 100
            
            if last_two_digits <= 25:
                rounded_base = close_int - last_two_digits
            elif last_two_digits <= 75:
                rounded_base = close_int - last_two_digits + 50
            else:
                rounded_base = close_int - last_two_digits + 100
                
            last_two_digits = rounded_base % 100
            
            if last_two_digits == 50:
                ce_strike = rounded_base - 150
                pe_strike = rounded_base + 150
            else:
                ce_strike = rounded_base - 200
                pe_strike = rounded_base + 200
            
        return ce_strike, pe_strike
    
    def get_option_data(self, strike, option_type, start_date, end_date):
        """Get option data for given parameters"""
        try:
            if self.instruments is None:
                self.instruments = self.kite.instruments('NFO')
            
            options = []
            for instrument in self.instruments:
                if (instrument['name'] == self.symbol and 
                    instrument['instrument_type'] == option_type and
                    instrument['strike'] == strike and
                    instrument['expiry']):
                    expiry_date = instrument['expiry']
                    if hasattr(expiry_date, 'date'):
                        expiry_date = expiry_date.date()
                    if expiry_date >= start_date.date():
                        options.append(instrument)
            
            if not options:
                return pd.DataFrame()
            
            options.sort(key=lambda x: x['expiry'])
            option = options[0]
            
            data = self.kite.historical_data(
                instrument_token=option['instrument_token'],
                from_date=start_date,
                to_date=end_date,
                interval='5minute'
            )
            df = pd.DataFrame(data)
            if not df.empty and 'date' in df.columns:
                df.set_index('date', inplace=True)
            return df
        except Exception as e:
            print(f"Error getting option data: {e}")
            return pd.DataFrame()
    
    def initialize_daily_data(self):
        """Initialize previous day data at market open"""
        try:
            today = datetime.now().date()
            prev_date = today - timedelta(days=1)
            
            # Get instrument token
            instrument_tokens = {
                'NIFTY': 256265,
                'BANKNIFTY': 260105,
                'FINNIFTY': 257801
            }
            
            # Get previous day close
            index_data_raw = self.kite.historical_data(
                instrument_token=instrument_tokens[self.symbol],
                from_date=prev_date,
                to_date=prev_date,
                interval='day'
            )
            
            if not index_data_raw:
                print(f"No {self.symbol} data for {prev_date}")
                return False
            
            index_close = index_data_raw[0]['close']
            self.ce_strike, self.pe_strike = self.get_strike_prices(index_close)
            
            # Get previous day option data
            prev_start = datetime.combine(prev_date, datetime.min.time())
            prev_end = prev_start + timedelta(days=1)
            
            ce_prev_data = self.get_option_data(self.ce_strike, 'CE', prev_start, prev_end)
            pe_prev_data = self.get_option_data(self.pe_strike, 'PE', prev_start, prev_end)
            
            if ce_prev_data.empty or pe_prev_data.empty:
                print("No previous day option data")
                return False
            
            self.ce_prev_high = ce_prev_data['high'].max()
            self.ce_prev_low = ce_prev_data['low'].min()
            self.pe_prev_high = pe_prev_data['high'].max()
            self.pe_prev_low = pe_prev_data['low'].min()
            
            print(f"Initialized: {self.symbol} CE:{self.ce_strike} PE:{self.pe_strike}")
            print(f"CE Prev H/L: {self.ce_prev_high:.2f}/{self.ce_prev_low:.2f}")
            print(f"PE Prev H/L: {self.pe_prev_high:.2f}/{self.pe_prev_low:.2f}")
            
            return True
        except Exception as e:
            print(f"Error initializing daily data: {e}")
            return False
    
    def check_signals(self):
        """Check for trading signals every 5 minutes"""
        try:
            now = datetime.now()
            
            # Skip if market closed or position already active
            if now.time() >= datetime.strptime('15:25:00', '%H:%M:%S').time():
                return
            
            if self.active_position:
                self.monitor_position()
                return
            
            # Check if data is initialized
            if not all([self.ce_strike, self.pe_strike, self.ce_prev_high, self.ce_prev_low, self.pe_prev_high, self.pe_prev_low]):
                print("Data not initialized, skipping signal check")
                return
            
            # Get current day data
            today_start = datetime.combine(now.date(), datetime.min.time())
            
            ce_current_data = self.get_option_data(self.ce_strike, 'CE', today_start, now)
            pe_current_data = self.get_option_data(self.pe_strike, 'PE', today_start, now)
            
            if ce_current_data.empty or pe_current_data.empty:
                print("No current option data")
                return
            
            # Check CE signal
            ce_signal, ce_entry = self.signal_detector.check_ce_buy_conditions(
                ce_current_data, pe_current_data, 
                self.ce_prev_high, self.ce_prev_low, 
                self.pe_prev_high, self.pe_prev_low
            )
            
            if ce_signal:
                print(f"CE BUY Signal at {now.strftime('%H:%M:%S')} @ {ce_entry['entry_price']:.2f}")
                
                # Place live order
                order_id = self.place_buy_order('CE', self.ce_strike, ce_entry['entry_price'])
                
                self.active_position = {
                    'type': 'CE',
                    'entry': ce_entry,
                    'data': ce_current_data,
                    'strike': self.ce_strike,
                    'order_id': order_id
                }
                return
            
            # Check PE signal
            pe_signal, pe_entry = self.signal_detector.check_pe_buy_conditions(
                pe_current_data, ce_current_data,
                self.pe_prev_high, self.pe_prev_low,
                self.ce_prev_high, self.ce_prev_low
            )
            
            if pe_signal:
                print(f"PE BUY Signal at {now.strftime('%H:%M:%S')} @ {pe_entry['entry_price']:.2f}")
                
                # Place live order
                order_id = self.place_buy_order('PE', self.pe_strike, pe_entry['entry_price'])
                
                self.active_position = {
                    'type': 'PE',
                    'entry': pe_entry,
                    'data': pe_current_data,
                    'strike': self.pe_strike,
                    'order_id': order_id
                }
        except Exception as e:
            print(f"Error checking signals: {e}")
    
    def get_option_symbol(self, option_type, strike):
        """Get option trading symbol"""
        try:
            if self.instruments is None:
                self.instruments = self.kite.instruments('NFO')
            
            for instrument in self.instruments:
                if (instrument['name'] == self.symbol and 
                    instrument['instrument_type'] == option_type and
                    instrument['strike'] == strike and
                    instrument['expiry']):
                    expiry_date = instrument['expiry']
                    if hasattr(expiry_date, 'date'):
                        expiry_date = expiry_date.date()
                    if expiry_date >= datetime.now().date():
                        return instrument['tradingsymbol']
            return None
        except Exception as e:
            print(f"Error getting option symbol: {e}")
            return None
    
    def place_buy_order(self, option_type, strike, price):
        """Place buy order for option"""
        try:
            if not self.live_trading:
                print(f"DEMO: BUY {option_type} {strike} @ {price:.2f}")
                return "DEMO_ORDER"
            
            symbol = self.get_option_symbol(option_type, strike)
            if not symbol:
                print(f"Could not find option symbol for {option_type} {strike}")
                return None
            
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                quantity=self.order_quantity,
                product=self.kite.PRODUCT_MIS,
                order_type=self.kite.ORDER_TYPE_LIMIT,
                price=price
            )
            
            print(f"BUY Order placed: {order_id} for {symbol} @ {price:.2f}")
            return order_id
            
        except Exception as e:
            print(f"Error placing buy order: {e}")
            return None
    
    def place_sell_order(self, option_type, strike, price):
        """Place sell order for option"""
        try:
            if not self.live_trading:
                print(f"DEMO: SELL {option_type} {strike} @ {price:.2f}")
                return "DEMO_ORDER"
            
            symbol = self.get_option_symbol(option_type, strike)
            if not symbol:
                print(f"Could not find option symbol for {option_type} {strike}")
                return None
            
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=self.order_quantity,
                product=self.kite.PRODUCT_MIS,
                order_type=self.kite.ORDER_TYPE_LIMIT,
                price=price
            )
            
            print(f"SELL Order placed: {order_id} for {symbol} @ {price:.2f}")
            return order_id
            
        except Exception as e:
            print(f"Error placing sell order: {e}")
            return None
    
    def monitor_position(self):
        """Monitor active position for exit"""
        if not self.active_position:
            return
        
        now = datetime.now()
        option_type = self.active_position['type']
        strike = self.active_position['strike']
        
        # Get latest option data
        today_start = datetime.combine(now.date(), datetime.min.time())
        current_data = self.get_option_data(strike, option_type, today_start, now)
        
        if current_data.empty:
            return
        
        # Get current price for monitoring
        current_price = current_data['close'].iloc[-1]
        entry_price = self.active_position['entry']['entry_price']
        target = self.active_position['entry']['target']
        
        # Simple exit logic - target hit or market close
        now_time = now.time()
        if current_price >= target:
            pnl = target - entry_price
            print(f"{option_type} EXIT: Target @ {target:.2f}, PnL: {pnl:.2f}")
            
            # Place sell order
            self.place_sell_order(option_type, strike, target)
            self.active_position = None
            
        elif now_time >= datetime.strptime('15:25:00', '%H:%M:%S').time():
            pnl = current_price - entry_price
            print(f"{option_type} EXIT: Market Close @ {current_price:.2f}, PnL: {pnl:.2f}")
            
            # Place sell order at market price
            self.place_sell_order(option_type, strike, current_price)
            self.active_position = Nonetarget - entry_price
            print(f"{option_type} EXIT: Target @ {target:.2f}, PnL: {pnl:.2f}")
            self.active_position = None
        elif now_time >= datetime.strptime('15:25:00', '%H:%M:%S').time():
            pnl = current_price - entry_price
            print(f"{option_type} EXIT: Market Close @ {current_price:.2f}, PnL: {pnl:.2f}")
            self.active_position = None
    
    def start_live_monitoring(self):
        """Start live signal monitoring"""
        try:
            print(f"Starting live monitoring for {self.symbol}")
            
            # Initialize immediately if during market hours
            now = datetime.now()
            if now.time() >= datetime.strptime('09:15:00', '%H:%M:%S').time() and now.time() <= datetime.strptime('15:30:00', '%H:%M:%S').time():
                self.initialize_daily_data()
            
            # Initialize at market open
            schedule.every().day.at("09:15").do(self.initialize_daily_data)
            
            # Check signals every 5 minutes during market hours
            for hour in range(9, 16):
                for minute in [20, 25, 30, 35, 40, 45, 50, 55]:
                    if hour == 15 and minute > 25:  # Stop at 3:25 PM
                        break
                    schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(self.check_signals)
            
            print("Live monitoring scheduled.")
            
            while True:
                try:
                    schedule.run_pending()
                    time.sleep(1)
                except KeyboardInterrupt:
                    print("Stopping live monitoring...")
                    break
                except Exception as e:
                    print(f"Error in monitoring loop: {e}")
                    time.sleep(5)  # Wait before retrying
        except Exception as e:
            print(f"Error starting live monitoring: {e}")

if __name__ == "__main__":
    live_signal = HighLowLiveSignal(symbol='NIFTY')
    live_signal.start_live_monitoring()