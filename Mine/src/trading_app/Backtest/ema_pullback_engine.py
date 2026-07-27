"""
EMA Confluence Breakout Backtest Engine.

Daily-candle-only, single-symbol, long and/or short:
  1. Four DAILY EMAs: 20, 50, 100, 200 (close-based, same-bar — the signal
     is only ever acted on FOLLOWING candles, so using the signal candle's
     own EMA values is not look-ahead bias).
  2. Signal candle: its DAILY range must TOUCH ALL FOUR EMAs at once
     (Low <= EMA <= High, for EMA20, EMA50, EMA100 and EMA200 each).
     Its own color then fixes the trade direction:
       - RED   candle (close < open): SELL setup — watch for a break of
               this candle's LOW.
       - GREEN candle (close > open): BUY setup  — watch for a break of
               this candle's HIGH.
     A doji (close == open) produces no signal.
  3. ENTRY is a breakout order armed off the signal candle's own High/Low,
     watched on every candle AFTER the signal candle until it fills (no
     expiry — if price never breaks out, that signal never fills):
       - BUY:  fills once a later candle's High >= signal candle's High.
       - SELL: fills once a later candle's Low  <= signal candle's Low.
     Fill price is the trigger level itself, or that candle's Open if it
     already gapped through the level (realistic slippage on a gap).
     While a breakout order is armed, new signal candles are ignored —
     only one pending order at a time.
  4. Stop Loss is the signal candle's OPPOSITE extreme (not a percentage):
       - BUY:  sl_level = signal candle's Low
       - SELL: sl_level = signal candle's High
     Target is a PERCENTAGE of the entry price (target_pct, default 5.0,
     floor 1.0):
       - BUY:  tp_level = entry × (1 + target_pct/100)
       - SELL: tp_level = entry × (1 − target_pct/100)
     Exit on whichever comes first, checked from the entry (breakout) candle
     onward (SL checked before Target within the same bar):
       - BUY:  LOW drops to/through sl_level   → 'SL'
               HIGH rises to/through tp_level  → 'TARGET'
       - SELL: HIGH rises to/through sl_level  → 'SL'
               LOW drops to/through tp_level   → 'TARGET'
  5. If the data ends while still in a position, it is force-closed at
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
                target_pct: float = 5.0):
        self.daily_df = daily_df.copy()
        self.enable_long  = bool(enable_long)
        self.enable_short = bool(enable_short)
        # Default 5%, floor 1% — never let target_pct go below the floor.
        self.target_pct = max(float(target_pct or 5.0), 1.0)
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
        """'Long', 'Short', or None for a given daily candle row.

        Signal requires the candle's range to touch ALL FOUR EMAs at once;
        the candle's own color (Green/Red) then fixes the direction.
        """
        if pd.isna(c['ema20']) or pd.isna(c['ema50']) or pd.isna(c['ema100']) or pd.isna(c['ema200']):
            return None
        touches_all = (c['low'] <= c['ema20']  <= c['high'] and
                       c['low'] <= c['ema50']  <= c['high'] and
                       c['low'] <= c['ema100'] <= c['high'] and
                       c['low'] <= c['ema200'] <= c['high'])
        if not touches_all:
            return None
        if c['close'] > c['open']:
            return 'Long'
        if c['close'] < c['open']:
            return 'Short'
        return None

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        df = self.daily_df
        if df.empty:
            return [], _summarise([])

        trades = []
        entry = None
        # {'direction','trigger_level','sl_level',...} — signal fired, armed
        # as a breakout order on the candle's own High (Long) / Low (Short),
        # watched on every subsequent candle until it fills.
        pending = None
        n = len(df)

        for pos in range(n):
            c = df.iloc[pos]

            if entry is None:
                if pending is not None:
                    direction = pending['direction']
                    trigger = pending['trigger_level']
                    filled = False
                    if direction == 'Long' and c['high'] >= trigger:
                        entry_price = float(c['open']) if c['open'] > trigger else trigger
                        filled = True
                    elif direction == 'Short' and c['low'] <= trigger:
                        entry_price = float(c['open']) if c['open'] < trigger else trigger
                        filled = True

                    if filled:
                        if direction == 'Long':
                            tp_level = entry_price * (1 + self.target_pct / 100)
                        else:
                            tp_level = entry_price * (1 - self.target_pct / 100)
                        # NSE's real Open print happens at 09:15 IST, so stamp
                        # entry_time there rather than at the normalized
                        # midnight used internally.
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
                        # Fall through — the breakout candle's own range is
                        # still live, so check SL/Target against it below.
                    else:
                        continue

                if entry is None:
                    direction = self._signal_direction(c)
                    if direction == 'Long' and self.enable_long:
                        pending = {'direction': 'Long', 'trigger_level': float(c['high']),
                                   'sl_level': float(c['low']),
                                   'signal_time': c['datetime'], 'ema20': float(c['ema20']),
                                   'ema50': float(c['ema50']), 'ema100': float(c['ema100']),
                                   'ema200': float(c['ema200'])}
                    elif direction == 'Short' and self.enable_short:
                        pending = {'direction': 'Short', 'trigger_level': float(c['low']),
                                   'sl_level': float(c['high']),
                                   'signal_time': c['datetime'], 'ema20': float(c['ema20']),
                                   'ema50': float(c['ema50']), 'ema100': float(c['ema100']),
                                   'ema200': float(c['ema200'])}
                    continue

            # In position — SL checked before Target within the same bar.
            # Checked from the entry (breakout) candle itself onward, since
            # the rest of that bar's range past the fill point is still live.
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


# ── Optimiser (Find Best Params) ───────────────────────────────────────────
# The only real free params left are Target % and Direction — SL/entry are
# fixed by the signal candle itself. Sweep both and rank by a score that
# rewards P&L but only once there's enough sample size and a positive edge.
_TARGET_PCT_GRID = [1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 7, 8, 10, 12, 15]
_DIRECTION_GRID  = ['both', 'long', 'short']


def _opt_score(s: dict) -> float:
    pf  = s.get('profit_factor') or 0
    pnl = s.get('total_pnl', 0)
    if s.get('total_trades', 0) < 3 or pf <= 0 or pnl <= 0:
        return -999.0
    return pnl * (pf ** 0.5)


def optimise_ema_pullback(daily_df: pd.DataFrame, min_trades: int = 3) -> list:
    """Sweep (direction × target_pct) and return results sorted best-first."""
    results = []
    for direction in _DIRECTION_GRID:
        enable_long  = direction != 'short'
        enable_short = direction != 'long'
        for tp in _TARGET_PCT_GRID:
            try:
                engine = EmaPullbackEngine(
                    daily_df=daily_df, enable_long=enable_long,
                    enable_short=enable_short, target_pct=tp,
                )
                _, summary = engine.run()
                if summary['total_trades'] < min_trades:
                    continue
                results.append({
                    'direction':     direction,
                    'target_pct':    tp,
                    'total_trades':  summary['total_trades'],
                    'wins':          summary['wins'],
                    'losses':        summary['losses'],
                    'win_rate':      summary['win_rate'],
                    'total_pnl':     summary['total_pnl'],
                    'profit_factor': summary['profit_factor'],
                    'max_drawdown':  summary['max_drawdown'],
                    'score':         round(_opt_score(summary), 2),
                })
            except Exception as exc:
                logger.debug('EMA Confluence Breakout optimise skip: dir=%s tp=%s — %s', direction, tp, exc)

    results.sort(key=lambda x: x['score'], reverse=True)
    return results
