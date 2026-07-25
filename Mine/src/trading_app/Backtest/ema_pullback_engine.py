"""
EMA 200 Trend Pullback Backtest Engine.

Daily-candle-only, single-symbol, long and/or short:
  1. Four DAILY EMAs: 20, 50, 100, 200 (close-based, same-bar — the signal
     is only ever acted on the FOLLOWING candle, so using the signal
     candle's own EMA values is not look-ahead bias).
  2. Trend alignment on the signal candle:
       - BUY  (Long):  EMA200 < EMA100 < EMA50  (i.e. EMA50 > EMA100 > EMA200)
       - SELL (Short): EMA200 > EMA100 > EMA50  (i.e. EMA50 < EMA100 < EMA200)
     EMA20 is tracked (and shown per-trade for reference) but is not part
     of the alignment check.
  3. The signal candle's DAILY range must TOUCH EMA200 (Low <= EMA200 <=
     High — a genuine pullback into the average, not a gap-through) AND
     its CLOSE must clear it in the trade's direction:
       - BUY:  close > EMA200
       - SELL: close < EMA200
     Optionally (require_candle_color, default True) the signal candle's
     own color must also agree with the trade direction:
       - BUY:  candle is GREEN (close > open)
       - SELL: candle is RED   (close < open)
  4. ENTRY is filled at the OPEN of the NEXT daily candle after the
     signal candle. If the signal candle is the last bar available, there
     is no next candle to fill on and that signal is dropped.
  5. Stop Loss is the signal candle's own High/Low (not a percentage):
       - Long:  sl_level = signal candle's Low
       - Short: sl_level = signal candle's High
     Target is a PERCENTAGE of the entry price (target_pct, default 5.0,
     floor 1.0):
       - Long:  tp_level = entry × (1 + target_pct/100)
       - Short: tp_level = entry × (1 − target_pct/100)
     Exit on whichever comes first, checked from the entry candle onward
     (SL checked before Target within the same bar):
       - Long:  LOW drops to/through sl_level   → 'SL'
                HIGH rises to/through tp_level  → 'TARGET'
       - Short: HIGH rises to/through sl_level  → 'SL'
                LOW drops to/through tp_level   → 'TARGET'
  6. If the data ends while still in a position, it is force-closed at
     the last available daily close → 'DATA_END'.
  Once a trade closes, scanning for the next signal resumes on the very
  next bar — any number of trades can occur across the requested range.
  Long and Short can each be toggled off (enable_long / enable_short).
"""
import logging

import pandas as pd

logger = logging.getLogger(__name__)

_MA_PERIODS = [20, 50, 100, 200]


class EmaPullbackEngine:

    def __init__(self, daily_df: pd.DataFrame,
                enable_long: bool = True, enable_short: bool = True,
                target_pct: float = 5.0, require_candle_color: bool = True):
        self.daily_df = daily_df.copy()
        self.enable_long  = bool(enable_long)
        self.enable_short = bool(enable_short)
        # Default 5%, floor 1% — never let target_pct go below the floor.
        self.target_pct = max(float(target_pct or 5.0), 1.0)
        # Signal candle must be Green (close>open) for BUY / Red (close<open)
        # for SELL — on by default, toggle off to drop this extra filter.
        self.require_candle_color = bool(require_candle_color)
        self._prepare()

    # ── Data prep ────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        if 'date' in df.columns:
            dt = pd.to_datetime(df['date'])
        elif 'datetime' in df.columns:
            dt = pd.to_datetime(df['datetime'])
        else:
            dt = pd.to_datetime(df.index)
        df['datetime'] = dt
        df = df.sort_values('datetime').drop_duplicates(subset=['datetime'])
        if df['datetime'].dt.tz is None:
            df['datetime'] = df['datetime'].dt.tz_localize(
                'Asia/Kolkata', ambiguous='infer', nonexistent='shift_forward')
        else:
            df['datetime'] = df['datetime'].dt.tz_convert('Asia/Kolkata')
        # Daily candles only — a DAILY bar has no meaningful time-of-day, but
        # the Fyers adapter anchors 'D' resolution epochs to UTC midnight,
        # which converts to 05:30:00 IST on every single bar (not a real
        # market time). Snap to local midnight so entry_time/exit_time show
        # a clean date instead of that artifact.
        df['datetime'] = df['datetime'].dt.normalize()
        return df.reset_index(drop=True)

    def _prepare(self):
        self.daily_df = self._normalise(self.daily_df)
        for n in _MA_PERIODS:
            self.daily_df[f'ema{n}'] = (self.daily_df['close']
                                         .ewm(span=n, adjust=False, min_periods=n).mean())

    # ── Signal check ─────────────────────────────────────────────────────────

    def _signal_direction(self, c) -> str:
        """'Long', 'Short', or None for a given daily candle row."""
        if pd.isna(c['ema20']) or pd.isna(c['ema50']) or pd.isna(c['ema100']) or pd.isna(c['ema200']):
            return None
        touches_200 = c['low'] <= c['ema200'] <= c['high']
        if not touches_200:
            return None
        aligned_long  = c['ema50'] > c['ema100'] and c['ema100'] > c['ema200']
        aligned_short = c['ema50'] < c['ema100'] and c['ema100'] < c['ema200']
        is_green = c['close'] > c['open']
        is_red   = c['close'] < c['open']
        if aligned_long and c['close'] > c['ema200']:
            if self.require_candle_color and not is_green:
                return None
            return 'Long'
        if aligned_short and c['close'] < c['ema200']:
            if self.require_candle_color and not is_red:
                return None
            return 'Short'
        return None

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        df = self.daily_df
        if df.empty:
            return [], _summarise([])

        trades = []
        entry = None
        pending = None   # {'direction', 'sl_level'} — signal fired, awaiting fill at next candle's open
        n = len(df)

        for pos in range(n):
            c = df.iloc[pos]

            if entry is None:
                if pending is not None:
                    entry_price = float(c['open'])
                    direction = pending['direction']
                    if direction == 'Long':
                        tp_level = entry_price * (1 + self.target_pct / 100)
                    else:
                        tp_level = entry_price * (1 - self.target_pct / 100)
                    # entry_price is the daily Open — NSE's real Open print
                    # happens at 09:15 IST, so stamp entry_time there rather
                    # than at the normalized midnight used internally.
                    entry_time = c['datetime'].replace(hour=9, minute=15)
                    entry = {
                        'entry_time': entry_time, 'entry_price': entry_price,
                        'direction': direction,
                        'sl_level': pending['sl_level'], 'tp_level': tp_level,
                        'signal_time': pending['signal_time'],
                        'ema20': pending['ema20'], 'ema50': pending['ema50'],
                        'ema100': pending['ema100'], 'ema200': pending['ema200'],
                    }
                    pending = None
                    continue

                direction = self._signal_direction(c)
                if direction == 'Long' and self.enable_long:
                    pending = {'direction': 'Long', 'sl_level': float(c['low']),
                               'signal_time': c['datetime'], 'ema20': float(c['ema20']),
                               'ema50': float(c['ema50']), 'ema100': float(c['ema100']),
                               'ema200': float(c['ema200'])}
                elif direction == 'Short' and self.enable_short:
                    pending = {'direction': 'Short', 'sl_level': float(c['high']),
                               'signal_time': c['datetime'], 'ema20': float(c['ema20']),
                               'ema50': float(c['ema50']), 'ema100': float(c['ema100']),
                               'ema200': float(c['ema200'])}
                continue

            # In position — SL checked before Target within the same bar.
            # Checked from the entry bar itself onward, since entry fills
            # at the open and the rest of that bar's range is still live.
            sl_level = entry['sl_level']
            tp_level = entry['tp_level']
            exit_reason = exit_price = None
            if entry['direction'] == 'Long':
                if c['low'] <= sl_level:
                    exit_reason, exit_price = 'SL', sl_level
                elif c['high'] >= tp_level:
                    exit_reason, exit_price = 'TARGET', tp_level
            else:
                if c['high'] >= sl_level:
                    exit_reason, exit_price = 'SL', sl_level
                elif c['low'] <= tp_level:
                    exit_reason, exit_price = 'TARGET', tp_level

            if exit_reason is not None:
                trades.append(_make_trade(entry, c['datetime'], exit_price, exit_reason))
                entry = None

        if entry is not None:
            # Still open at the end of the data — force-close at the last close.
            last = df.iloc[-1]
            trades.append(_make_trade(entry, last['datetime'], float(last['close']), 'DATA_END'))

        return trades, _summarise(trades)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_trade(entry, exit_dt, exit_price, exit_reason):
    entry_price = entry['entry_price']
    direction   = entry['direction']
    pnl = (exit_price - entry_price) if direction == 'Long' else (entry_price - exit_price)
    pnl_pct = (pnl / entry_price * 100) if entry_price else 0.0
    return {
        'entry_time':   entry['entry_time'].isoformat(),
        'signal_time':  entry['signal_time'].isoformat(),
        'exit_time':    exit_dt.isoformat(),
        'type':         direction,
        'entry_price':  round(entry_price, 2),
        'exit_price':   round(exit_price, 2),
        'sl_price':     round(entry['sl_level'], 2),
        'target_price': round(entry['tp_level'], 2),
        'pnl':          round(pnl, 2),
        'pnl_pct':      round(pnl_pct, 2),
        'result':       'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
        'exit_reason':  exit_reason,
        'ema20':  round(entry['ema20'], 2),
        'ema50':  round(entry['ema50'], 2),
        'ema100': round(entry['ema100'], 2),
        'ema200': round(entry['ema200'], 2),
    }


def _summarise(trades):
    if not trades:
        return {
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0.0, 'total_pnl': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0,
            'profit_factor': 0.0, 'max_drawdown': 0.0,
            'sl_exits': 0, 'target_exits': 0, 'data_end_exits': 0,
        }

    wins   = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']

    total_pnl    = sum(t['pnl'] for t in trades)
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss   = abs(sum(t['pnl'] for t in losses))

    avg_win  = gross_profit / len(wins)   if wins   else 0.0
    avg_loss = gross_loss   / len(losses) if losses else 0.0
    pf       = gross_profit / gross_loss  if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    running, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        running += t['pnl']
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    return {
        'total_trades':  len(trades),
        'wins':          len(wins),
        'losses':        len(losses),
        'win_rate':      round(len(wins) / len(trades) * 100, 1),
        'total_pnl':     round(total_pnl, 2),
        'avg_win':       round(avg_win, 2),
        'avg_loss':      round(avg_loss, 2),
        'profit_factor': round(pf, 2),
        'max_drawdown':  round(max_dd, 2),
        'sl_exits':       sum(1 for t in trades if t['exit_reason'] == 'SL'),
        'target_exits':   sum(1 for t in trades if t['exit_reason'] == 'TARGET'),
        'data_end_exits': sum(1 for t in trades if t['exit_reason'] == 'DATA_END'),
    }
