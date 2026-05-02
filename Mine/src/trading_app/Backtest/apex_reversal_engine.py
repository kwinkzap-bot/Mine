import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
print(f"🔄 [apex_reversal_engine] LOADED from: {__file__}")

class ApexReversalBacktester:
    """
    Python backtest engine that mirrors the Pine Script "Apex Reversal Engine"
    line-by-line. Every step in run() corresponds to a specific line range in
    the Pine Script so that both produce identical signals.
    """

    def __init__(self, df, pivot_strength=1, rsi_length=14, rsi_overbought=70,
                 rsi_oversold=30, rr_ratio=3.0, entry_buffer=10.0,
                 interval='5minute', intraday_only=True, sl_close_price=True,
                 trail_candles=0):
        self.df = df.copy()
        self.pivot_strength = pivot_strength
        self.rsi_length = rsi_length
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.rr_ratio = rr_ratio
        self.entry_buffer = entry_buffer
        self.interval = interval
        self.intraday_only = intraday_only
        self.sl_close_price = sl_close_price
        self.trail_candles = trail_candles
        self.is_daily = 'day' in self.interval

        # Pine L98-104: maxSL based on timeframe
        if self.is_daily:
            self.max_sl = 300.0
        elif '60' in self.interval or '30' in self.interval or '15' in self.interval or 'hour' in self.interval.lower():
            self.max_sl = 150.0
        elif '5' in self.interval:
            self.max_sl = 100.0
        elif '3' in self.interval:
            self.max_sl = 80.0
        elif self.interval in ('1minute', 'minute') or '1min' in self.interval:
            self.max_sl = 50.0
        else:
            self.max_sl = 150.0

        # Entry buffer based on timeframe
        if self.is_daily:
            self.entry_buffer = 10.0
        elif '60' in self.interval or '30' in self.interval or '15' in self.interval or 'hour' in self.interval.lower():
            self.entry_buffer = 10.0
        elif '5' in self.interval:
            self.entry_buffer = 5.0
        elif '3' in self.interval:
            self.entry_buffer = 3.0
        elif self.interval in ('1minute', 'minute') or '1min' in self.interval:
            self.entry_buffer = 1.0
        
        print(f"⚙️ Interval='{self.interval}' → MaxSL={self.max_sl}, Buffer={self.entry_buffer}")

        self.prepare_indicators()

    # ------------------------------------------------------------------
    # Indicators  (Pine L48-51)
    # ------------------------------------------------------------------
    def prepare_indicators(self):
        # --- RSI (Pine ta.rsi: SMA seed + Wilder's RMA) ---
        delta = self.df['close'].diff()
        gain = delta.where(delta > 0, 0).fillna(0)
        loss = (-delta.where(delta < 0, 0)).fillna(0)

        period = self.rsi_length
        n = len(self.df)
        avg_gain_arr = np.full(n, np.nan)
        avg_loss_arr = np.full(n, np.nan)

        # SMA seed for the first 'period' gains/losses (Pine behaviour)
        if n > period:
            avg_gain_arr[period] = gain.iloc[1:period + 1].mean()
            avg_loss_arr[period] = loss.iloc[1:period + 1].mean()

            # Wilder's RMA for the rest
            for idx in range(period + 1, n):
                avg_gain_arr[idx] = (avg_gain_arr[idx - 1] * (period - 1) + gain.iloc[idx]) / period
                avg_loss_arr[idx] = (avg_loss_arr[idx - 1] * (period - 1) + loss.iloc[idx]) / period

        rs = np.where(avg_loss_arr == 0, 100, avg_gain_arr / avg_loss_arr)
        rsi = np.where(avg_loss_arr == 0, 100, 100 - (100 / (1 + rs)))
        self.df['rsi'] = rsi

        # --- Pivot High / Low ---
        # Pine: ta.pivothigh(high, L, R)  —  confirmed R bars later
        highs = self.df['high'].values
        lows = self.df['low'].values
        L = R = self.pivot_strength

        # Pre-fill columns
        self.df['ph_val'] = np.nan
        self.df['pl_val'] = np.nan
        self.df['ph_found'] = False
        self.df['pl_found'] = False

        for i in range(L, len(self.df) - R):
            # Pivot High
            is_ph = True
            for j in range(i - L, i + R + 1):
                if j == i:
                    continue
                if highs[j] > highs[i]:
                    is_ph = False
                    break
            if is_ph:
                # Confirmed at bar i+R  (same as Pine "offset = -pivotStrength")
                self.df.iat[i + R, self.df.columns.get_loc('ph_val')] = highs[i]
                self.df.iat[i + R, self.df.columns.get_loc('ph_found')] = True

            # Pivot Low
            is_pl = True
            for j in range(i - L, i + R + 1):
                if j == i:
                    continue
                if lows[j] < lows[i]:
                    is_pl = False
                    break
            if is_pl:
                self.df.iat[i + R, self.df.columns.get_loc('pl_val')] = lows[i]
                self.df.iat[i + R, self.df.columns.get_loc('pl_found')] = True

    # ------------------------------------------------------------------
    # Main loop  —  mirrors Pine execution order exactly
    # ------------------------------------------------------------------
    def run(self):
        # Pine L85-95: var declarations
        buy_trigger_price = np.nan
        sell_trigger_price = np.nan
        waiting_for_buy = False
        waiting_for_sell = False
        sl_level = np.nan
        tp_level = np.nan
        entry_price_var = np.nan
        sl_moved = False
        active_pos = 0          # 1 = Long, -1 = Short
        signal_bar_idx = -1     # Pine L106: signalBarIndex
        bars_since_be = 0       # Counter for trailing candle frequency

        entry_time = None
        results = []

        n = len(self.df)
        for i in range(n):
            row = self.df.iloc[i]
            o, h, l, c = row['open'], row['high'], row['low'], row['close']
            prev_close = self.df.iloc[i - 1]['close'] if i > 0 else c
            current_time = row['date']

            # ============================================================
            # Pine L74-79: Signal detection
            # strictSell = not na(ph) and rsiValue[pivotStrength] >= rsiOB
            # strictBuy  = not na(pl) and rsiValue[pivotStrength] <= rsiOS
            # ============================================================
            strict_buy = False
            strict_sell = False

            if row.get('ph_found') == True:
                rsi_at_pivot = self.df.iloc[i - self.pivot_strength]['rsi']
                if not np.isnan(rsi_at_pivot) and rsi_at_pivot >= self.rsi_overbought:
                    strict_sell = True
                    print(f"📍 SELL SIGNAL @ {current_time} | PH={row['ph_val']:.2f} | RSI={rsi_at_pivot:.2f} | Candle: O={o} H={h} L={l} C={c}")

            if row.get('pl_found') == True:
                rsi_at_pivot = self.df.iloc[i - self.pivot_strength]['rsi']
                if not np.isnan(rsi_at_pivot) and rsi_at_pivot <= self.rsi_oversold:
                    strict_buy = True
                    print(f"📍 BUY SIGNAL  @ {current_time} | PL={row['pl_val']:.2f} | RSI={rsi_at_pivot:.2f} | Candle: O={o} H={h} L={l} C={c}")

            # ============================================================
            # Pine L108-126: Set triggers on signal
            #   buyTriggerPrice  := high + entryBuffer     (signal candle high)
            #   sellTriggerPrice := low  - entryBuffer     (signal candle low)
            #   slLevel := pl / ph
            # ============================================================
            if strict_buy:
                buy_trigger_price = h + self.entry_buffer
                waiting_for_buy = True
                waiting_for_sell = False
                sl_level = row['pl_val']
                signal_bar_idx = i
                if (buy_trigger_price - sl_level) > self.max_sl:
                    waiting_for_buy = False
                    print(f"   ❌ SKIPPED (MaxSL): Risk={buy_trigger_price - sl_level:.2f} > {self.max_sl}")
                else:
                    print(f"   ✅ Trigger set: Buy above {buy_trigger_price:.2f}, SL={sl_level:.2f}, Risk={buy_trigger_price - sl_level:.2f}")

            if strict_sell:
                sell_trigger_price = l - self.entry_buffer
                waiting_for_sell = True
                waiting_for_buy = False
                sl_level = row['ph_val']
                signal_bar_idx = i
                if (sl_level - sell_trigger_price) > self.max_sl:
                    waiting_for_sell = False
                    print(f"   ❌ SKIPPED (MaxSL): Risk={sl_level - sell_trigger_price:.2f} > {self.max_sl}")
                else:
                    print(f"   ✅ Trigger set: Sell below {sell_trigger_price:.2f}, SL={sl_level:.2f}, Risk={sl_level - sell_trigger_price:.2f}")

            # ============================================================
            # Pine L128-129: Entry cutoff
            # ============================================================
            is_after_cutoff = False
            if self.intraday_only and not self.is_daily:
                hh, mm = current_time.hour, current_time.minute
                is_after_cutoff = (hh > 15) or (hh == 15 and mm >= 0)
            can_enter = not is_after_cutoff

            # ============================================================
            # Pine L131-135: Invalidation — SL hit before entry
            # ============================================================
            if waiting_for_buy and l <= sl_level:
                waiting_for_buy = False
                print(f"   ⛔ INVALIDATED BUY @ {current_time} | Low={l:.2f} <= SL={sl_level:.2f}")
            if waiting_for_sell and h >= sl_level:
                waiting_for_sell = False
                print(f"   ⛔ INVALIDATED SELL @ {current_time} | High={h:.2f} >= SL={sl_level:.2f}")

            # ============================================================
            # Pine L137-147: Gap filter + entry determination
            # ============================================================
            gap_too_big = abs(o - prev_close) > 100

            trigger_hit_buy = (waiting_for_buy
                               and i > signal_bar_idx
                               and h > buy_trigger_price
                               and can_enter)
            trigger_hit_sell = (waiting_for_sell
                                and i > signal_bar_idx
                                and l < sell_trigger_price
                                and can_enter)

            if (trigger_hit_buy or trigger_hit_sell) and gap_too_big:
                waiting_for_buy = False
                waiting_for_sell = False

            entry_buy = trigger_hit_buy and not gap_too_big
            entry_sell = trigger_hit_sell and not gap_too_big

            # ============================================================
            # Pine L153-167: Execute entry
            # ============================================================
            if entry_buy:
                waiting_for_buy = False
                active_pos = 1
                entry_price_var = max(o, buy_trigger_price)
                tp_level = entry_price_var + (entry_price_var - sl_level) * self.rr_ratio
                sl_moved = False
                entry_time = current_time
                print(f"🟢 BUY ENTRY   @ {current_time} | Price={entry_price_var:.2f} | SL={sl_level:.2f} | TP={tp_level:.2f}")

            if entry_sell:
                waiting_for_sell = False
                active_pos = -1
                entry_price_var = min(o, sell_trigger_price)
                tp_level = entry_price_var - (sl_level - entry_price_var) * self.rr_ratio
                sl_moved = False
                entry_time = current_time
                print(f"🔴 SELL ENTRY  @ {current_time} | Price={entry_price_var:.2f} | SL={sl_level:.2f} | TP={tp_level:.2f}")

            # ============================================================
            # Exits: Square-off → SL → TP (checked BEFORE trailing SL)
            # ============================================================
            sl_hit = False
            be_hit = False
            sq_off = False
            tp_hit = False

            # --- Intraday Square-off ---
            if self.intraday_only and not self.is_daily and active_pos != 0:
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
                    sq_off = True

            # --- Stop Loss ---
            # sl_close_price=True  → SL hits only when candle CLOSES beyond SL
            # sl_close_price=False → SL hits when price TOUCHES SL (low/high)
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
                        'result': 'BE' if sl_moved else 'SL'
                    })
                    active_pos = 0

            if active_pos == -1:
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
                        'result': 'BE' if sl_moved else 'SL'
                    })
                    active_pos = 0

            # --- Take Profit ---
            if active_pos == 1 and h >= tp_level:
                pnl = tp_level - entry_price_var
                results.append({
                    'entry_time': entry_time,
                    'exit_time': current_time,
                    'type': 'BUY',
                    'entry_price': entry_price_var,
                    'exit_price': tp_level,
                    'pnl': pnl,
                    'result': 'TP'
                })
                active_pos = 0

            if active_pos == -1 and l <= tp_level:
                pnl = entry_price_var - tp_level
                results.append({
                    'entry_time': entry_time,
                    'exit_time': current_time,
                    'type': 'SELL',
                    'entry_price': entry_price_var,
                    'exit_price': tp_level,
                    'pnl': pnl,
                    'result': 'TP'
                })
                active_pos = 0

            # --- Trailing SL: Move to Break Even (1:1) ---
            # ALWAYS uses candle CLOSE, takes effect on the NEXT candle
            if active_pos == 1 and not sl_moved:
                initial_risk = entry_price_var - sl_level
                if initial_risk > 0 and c >= entry_price_var + initial_risk:
                    sl_level = entry_price_var
                    sl_moved = True
                    bars_since_be = 0

            if active_pos == -1 and not sl_moved:
                initial_risk = sl_level - entry_price_var
                if initial_risk > 0 and c <= entry_price_var - initial_risk:
                    sl_level = entry_price_var
                    sl_moved = True
                    bars_since_be = 0

            # --- Continuous trailing after BE ---
            if sl_moved and self.trail_candles > 0 and active_pos != 0:
                bars_since_be += 1
                if bars_since_be >= self.trail_candles:
                    bars_since_be = 0
                    if active_pos == 1:
                        # Trail to lowest low of last N candles
                        start_idx = max(0, i - self.trail_candles + 1)
                        trail_low = self.df.iloc[start_idx:i+1]['low'].min()
                        if trail_low > sl_level:
                            sl_level = trail_low
                    elif active_pos == -1:
                        # Trail to highest high of last N candles
                        start_idx = max(0, i - self.trail_candles + 1)
                        trail_high = self.df.iloc[start_idx:i+1]['high'].max()
                        if trail_high < sl_level:
                            sl_level = trail_high

        return results


def run_apex_backtest(df, params):
    backtester = ApexReversalBacktester(
        df,
        pivot_strength=params.get('pivot_strength', 1),
        rsi_length=params.get('rsi_length', 14),
        rsi_overbought=params.get('rsi_overbought', 70),
        rsi_oversold=params.get('rsi_oversold', 30),
        rr_ratio=params.get('rr_ratio', 3.0),
        entry_buffer=params.get('entry_buffer', 10.0),
        interval=params.get('interval', '5minute'),
        intraday_only=params.get('intraday_only', True),
        sl_close_price=params.get('sl_close_price', True),
        trail_candles=params.get('trail_candles', 0)
    )
    return backtester.run()
