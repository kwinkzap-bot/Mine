import pandas as pd
import numpy as np
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class CPRGapBacktester:
    """
    Backtest engine for CPR Gap up and down strategy.
    Logic:
    - If first candle closes below PDL or S1 -> wait for price to break the LOW of the first candle to enter SHORT. Stop Loss = High of first candle.
    - If first candle closes above PDH or R1 -> wait for price to break the HIGH of the first candle to enter LONG. Stop Loss = Low of first candle.
    """

    def __init__(self, df, interval='5minute', rr_ratio=2.0, intraday_only=True, entry_buffer=5.0, cpr_type='s1_r1', sl_close_price=True, entry_type='any', sl_type='both'):
        self.df = df.copy()
        self.interval = interval
        self.rr_ratio = rr_ratio
        self.intraday_only = intraday_only
        self.entry_buffer = entry_buffer
        self.cpr_type = cpr_type
        self.sl_close_price = sl_close_price
        self.entry_type = entry_type
        self.sl_type = sl_type
        self.prepare_data()

    def prepare_data(self):
        df = self.df
        if 'date' in df.columns:
            df['datetime'] = pd.to_datetime(df['date'])
        else:
            df['datetime'] = pd.to_datetime(df.index)
            
        df['date_only'] = df['datetime'].dt.date
        
        # Calculate daily high, low, close for PDH, PDL, R1, S1
        daily_df = df.groupby('date_only').agg(
            daily_high=('high', 'max'),
            daily_low=('low', 'min'),
            daily_close=('close', 'last')
        )
        
        # Shift to get previous day's values
        daily_df['pdh'] = daily_df['daily_high'].shift(1)
        daily_df['pdl'] = daily_df['daily_low'].shift(1)
        daily_df['pdc'] = daily_df['daily_close'].shift(1)
        
        # CPR Calculations
        daily_df['pivot'] = (daily_df['pdh'] + daily_df['pdl'] + daily_df['pdc']) / 3
        daily_df['r1'] = 2 * daily_df['pivot'] - daily_df['pdl']
        daily_df['s1'] = 2 * daily_df['pivot'] - daily_df['pdh']
        
        # Merge back to intraday dataframe
        df = df.merge(daily_df[['pdh', 'pdl', 'r1', 's1']], left_on='date_only', right_index=True, how='left')
        
        # Identify first candle of the day
        df['is_first_candle'] = df['date_only'] != df['date_only'].shift(1)
        
        self.df = df

    def run(self):
        results = []
        
        waiting_for_buy = False
        waiting_for_sell = False
        
        buy_trigger_price = np.nan
        sell_trigger_price = np.nan
        sl_level = np.nan
        
        active_pos = 0 # 1 for Long, -1 for Short
        entry_price_var = np.nan
        tp_level = np.nan
        entry_time = None
        
        n = len(self.df)
        
        for i in range(n):
            row = self.df.iloc[i]
            o, h, l, c = row['open'], row['high'], row['low'], row['close']
            current_time = row['datetime']
            
            # --- 1. Identify first candle signals ---
            if row['is_first_candle']:
                # Reset triggers from previous day
                waiting_for_buy = False
                waiting_for_sell = False
                
                # Branch based on selected type
                if self.cpr_type == 's1_r1':
                    # Check Gap Up / Breakout conditions
                    # Entry Logic based on entry_type
                    pdh_crossed = l <= row['pdh'] and c > row['pdh']
                    r1_crossed = l <= row['r1'] and c > row['r1']
                    
                    buy_condition = False
                    if self.entry_type == 'pdh_pdl':
                        buy_condition = pdh_crossed
                    elif self.entry_type == 's1_r1':
                        buy_condition = r1_crossed
                    elif self.entry_type == 'both':
                        buy_condition = pdh_crossed and r1_crossed
                    else: # 'any'
                        buy_condition = pdh_crossed or r1_crossed

                    if buy_condition:
                        waiting_for_buy = True
                        buy_trigger_price = h + self.entry_buffer  # Entry is high + buffer
                        
                        # Set SL Level based on sl_type
                        if self.sl_type == 'pdh_pdl':
                            sl_level = row['pdh']
                        elif self.sl_type == 's1_r1':
                            sl_level = row['r1']
                        elif self.sl_type == 'candle':
                            sl_level = l # First candle low
                        else: # 'both'
                            sl_level = min(row['r1'], row['pdh'])
                    
                    # Check Gap Down / Breakdown conditions
                    pdl_crossed = h >= row['pdl'] and c < row['pdl']
                    s1_crossed = h >= row['s1'] and c < row['s1']
                    
                    sell_condition = False
                    if self.entry_type == 'pdh_pdl':
                        sell_condition = pdl_crossed
                    elif self.entry_type == 's1_r1':
                        sell_condition = s1_crossed
                    elif self.entry_type == 'both':
                        sell_condition = pdl_crossed and s1_crossed
                    else: # 'any'
                        sell_condition = pdl_crossed or s1_crossed

                    if sell_condition:
                        waiting_for_sell = True
                        sell_trigger_price = l - self.entry_buffer # Entry is low - buffer
                        
                        # Set SL Level based on sl_type
                        if self.sl_type == 'pdh_pdl':
                            sl_level = row['pdl']
                        elif self.sl_type == 's1_r1':
                            sl_level = row['s1']
                        elif self.sl_type == 'candle':
                            sl_level = h # First candle high
                        else: # 'both'
                            sl_level = max(row['s1'], row['pdl'])
                        
                elif self.cpr_type == 'on_cpr':
                    # Placeholder for new logic
                    pass
                    
            # --- 2. Check for entry if waiting ---
            entered_this_candle = False
            if waiting_for_buy and active_pos == 0:
                if h >= buy_trigger_price:
                    active_pos = 1
                    entry_price_var = max(o, buy_trigger_price)
                    entry_time = current_time
                    waiting_for_buy = False
                    entered_this_candle = True
                    
            elif waiting_for_sell and active_pos == 0:
                if l <= sell_trigger_price:
                    active_pos = -1
                    entry_price_var = min(o, sell_trigger_price)
                    entry_time = current_time
                    waiting_for_sell = False
                    entered_this_candle = True

            # --- 3. Position Management ---
            if active_pos != 0 and not entered_this_candle:
                # Intraday Square-off at 15:20
                if self.intraday_only:
                    hh, mm = current_time.hour, current_time.minute
                    if (hh > 15) or (hh == 15 and mm >= 20):
                        pnl = (c - entry_price_var) if active_pos == 1 else (entry_price_var - c)
                        results.append({
                            'entry_time': entry_time,
                            'exit_time': current_time,
                            'type': 'BUY' if active_pos == 1 else 'SELL',
                            'entry_price': entry_price_var,
                            'exit_price': c,
                            'pnl': pnl,
                            'result': 'SQUARE-OFF'
                        })
                        active_pos = 0
                        continue
                
                # Check Stop Loss (No TP, just hold till 3:20 PM)
                if active_pos == 1:
                    sl_triggered = (c < sl_level) if self.sl_close_price else (l <= sl_level)
                    if sl_triggered:
                        exit_px = c if self.sl_close_price else min(o, sl_level)
                        pnl = exit_px - entry_price_var
                        results.append({
                            'entry_time': entry_time,
                            'exit_time': current_time,
                            'type': 'BUY',
                            'entry_price': entry_price_var,
                            'exit_price': exit_px,
                            'pnl': pnl,
                            'result': 'SL'
                        })
                        active_pos = 0
                        
                elif active_pos == -1:
                    sl_triggered = (c > sl_level) if self.sl_close_price else (h >= sl_level)
                    if sl_triggered:
                        exit_px = c if self.sl_close_price else max(o, sl_level)
                        pnl = entry_price_var - exit_px
                        results.append({
                            'entry_time': entry_time,
                            'exit_time': current_time,
                            'type': 'SELL',
                            'entry_price': entry_price_var,
                            'exit_price': exit_px,
                            'pnl': pnl,
                            'result': 'SL'
                        })
                        active_pos = 0

        return results

def run_cpr_gap_backtest(df, params):
    backtester = CPRGapBacktester(
        df,
        interval=params.get('interval', '5minute'),
        rr_ratio=params.get('rr_ratio', 2.0),
        intraday_only=params.get('intraday_only', True),
        cpr_type=params.get('cpr_type', 's1_r1'),
        sl_close_price=params.get('sl_close_price', True),
        entry_type=params.get('entry_type', 'any'),
        sl_type=params.get('sl_type', 'both')
    )
    return backtester.run()
