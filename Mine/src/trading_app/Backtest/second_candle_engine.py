"""
2nd 30-Sec Candle Breakout Backtest Engine.

Designed for 30-second candles. Every trading day the 2nd candle of the
session (09:15:30–09:16:00 for a 09:15 open) defines the breakout range:

    • Price crosses ABOVE the 2nd candle High  → BUY  (stop entry at High)
        SL     = 2nd candle Low
        Target = Entry + (SL:Target ratio) × risk     (risk = Entry − Low)
    • Price crosses BELOW the 2nd candle Low    → SELL (stop entry at Low)
        SL     = 2nd candle High
        Target = Entry − (SL:Target ratio) × risk     (risk = High − Entry)

SL : Target is 1 : 3 by default (rr_ratio = 3.0).

Rules:
  - Only the FIRST breakout of the day is taken (one trade per day).
  - No new entries at/after the cut-off time (default 15:25 IST).
  - Any open position is force-closed at the cut-off time, else at EOD.
  - When a bar gaps past the trigger, entry fills at the bar open.

Exits (first trigger wins, SL checked before Target within a bar):
  SL Hit   : price hits the opposite end of the range candle
  TG Hit   : price hits the 1:2 target
  Time Exit: cut-off time force close (at bar open)
  EOD Exit : last bar of the day force close (at bar close)

P&L basis (option_pnl flag)
---------------------------
Signals, SL and Target are ALWAYS decided on the index — that is what the live
algo does too. `option_pnl` only changes what the booked P&L is measured on:

  option_pnl=False (default)  P&L = index points  (the historical behaviour)
  option_pnl=True             P&L = OPTION PREMIUM points of the leg the live
                              algo would have bought: a CE for a Long signal,
                              a PE for a Short one, single lot, always bought.

The option leg is *modelled*, not fetched — this app has no historical option
chain to backtest against. Each trade's leg is priced with Black-Scholes on the
nearest weekly expiry (expiry weekday from the same NSE regime table the expiry
engine uses, 15:30 IST), at a fixed assumed IV, and the strike is chosen the way
the live algo chooses it (`strike_mode`: 'premium250' = strike priced nearest
₹250, the live default; 'delta' = |delta| nearest 0.90, capped at ₹500).

₹ conversion does not change with the basis — the screen's Lot Value (₹/pt)
multiplies whichever points the run booked, so the flag moves the points scale
and nothing else.

So an option-basis run carries the model's assumptions: constant IV, no smile,
no spread, no slippage. Its value is that theta and delta drift are in the
numbers — a 1 : 3 index trade is NOT 1 : 3 on the premium — not that the
premiums match any particular day's real chain.
"""

import functools
import logging
import math
from datetime import datetime, date, time as dt_time, timedelta

import pandas as pd
import numpy as np

from trading_app.Backtest.expiry_breakout_engine import _expiry_weekday_for

logger = logging.getLogger(__name__)

# ── Option-leg model (only used when option_pnl=True) ─────────────────────────
# Deliberately mirrors the live algo's own numbers — see
# algo/second_candle/second_candle_algo.py: _SINGLE_LOT_QTY, _DELTA_TARGET,
# _MAX_PREMIUM, _PREM250_TARGET, _STRIKE_STEP, _FALLBACK_IV.
_STRIKE_STEP    = 50.0    # NIFTY's — other indices pass their own
_DELTA_TARGET   = 0.90    # 'delta' strike mode
_MAX_PREMIUM    = 500.0   # 'delta' mode premium cap
_PREM250_TARGET = 250.0   # 'premium250' strike mode (the live default)
_DEFAULT_IV     = 0.15    # assumed annual IV — no historical chain to read one from
_RISK_FREE      = 0.07
_STRIKE_WINDOW  = 40      # strikes scanned either side of ATM (±2000 pts on a 50 step)
_YEAR_SECS      = 365.0 * 24 * 3600
_MIN_T_SECS     = 60.0    # never price at T=0 — the last minute is still worth something
_EXPIRY_TIME    = dt_time(15, 30)


class SecondCandleBacktestEngine:

    def __init__(
        self,
        df: pd.DataFrame,
        candle_index: int = 2,       # 1-based: which session candle defines the range
        rr_ratio: float = 3.0,       # Target = rr_ratio × risk  (1:3 → 3.0)
        exit_hour: int = 15,
        exit_minute: int = 25,
        enable_long: bool = True,
        enable_short: bool = True,
        option_pnl: bool = False,    # book P&L on the option premium, not index points
        option_iv: float = _DEFAULT_IV,
        strike_mode: str = 'premium250',
        strike_step: float = _STRIKE_STEP,
    ):
        self.df = df.copy()
        self.candle_index = max(1, int(candle_index))
        self.rr = float(rr_ratio)
        self.exit_hour = exit_hour
        self.exit_minute = exit_minute
        self.enable_long = enable_long
        self.enable_short = enable_short
        self.option_pnl = bool(option_pnl)
        self.option_cfg = _option_cfg(option_iv, strike_mode, strike_step)
        self._prepare()

    # ── Data prep ──────────────────────────────────────────────────────────────

    def _prepare(self):
        if not pd.api.types.is_datetime64_any_dtype(self.df.index):
            if 'date' in self.df.columns:
                self.df = self.df.set_index('date')
            elif 'datetime' in self.df.columns:
                self.df = self.df.set_index('datetime')
            self.df.index = pd.to_datetime(self.df.index)
        self.df = self.df.sort_index()
        # Guard against duplicate bars (Fyers can return the current day's
        # candles twice) — a duplicated first bar would silently become the
        # "2nd candle" and corrupt the breakout range.
        self.df = self.df[~self.df.index.duplicated(keep='last')]
        self.df.columns = [c.lower() for c in self.df.columns]

    # ── Array prep (shared by run + optimiser) ─────────────────────────────────

    def _arrays(self):
        """Extract numpy arrays once and split bar indices into contiguous days."""
        df = self.df
        opens  = df['open'].to_numpy(dtype=float)
        highs  = df['high'].to_numpy(dtype=float)
        lows   = df['low'].to_numpy(dtype=float)
        closes = df['close'].to_numpy(dtype=float)
        idx    = df.index

        day_ids   = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy()
        time_mins = (idx.hour * 60 + idx.minute).to_numpy()
        ts_strs   = idx.strftime('%Y-%m-%d %H:%M:%S').to_numpy()

        # df is sorted by index, so day_ids is non-decreasing → contiguous groups
        day_positions = []
        n = len(day_ids)
        if n:
            start = 0
            for i in range(1, n):
                if day_ids[i] != day_ids[start]:
                    day_positions.append(np.arange(start, i))
                    start = i
            day_positions.append(np.arange(start, n))

        return opens, highs, lows, closes, time_mins, ts_strs, day_positions

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        if self.df.empty:
            return [], _summary([])

        opens, highs, lows, closes, time_mins, ts_strs, day_positions = self._arrays()
        cutoff = self.exit_hour * 60 + self.exit_minute

        trades = []
        for day_pos in day_positions:
            t = _run_day(
                day_pos, opens, highs, lows, closes, time_mins, ts_strs,
                self.candle_index, self.rr, cutoff,
                self.enable_long, self.enable_short,
            )
            if t is not None:
                trades.append(t)

        if self.option_pnl:
            trades = [_apply_option_pnl(t, self.option_cfg) for t in trades]

        summary = _summary(trades)
        summary['pnl_basis'] = 'option' if self.option_pnl else 'index'
        if self.option_pnl:
            summary['option_iv']   = self.option_cfg['iv']
            summary['strike_mode'] = self.option_cfg['strike_mode']
        return trades, summary


# ── Single-day simulation ──────────────────────────────────────────────────────

def _run_day(day_pos, opens, highs, lows, closes, time_mins, ts_strs,
             candle_index, rr, cutoff, enable_long, enable_short):
    """Simulate one trading day. Returns a single trade dict or None."""
    n = len(day_pos)
    if n <= candle_index:          # need the range candle + at least one watch bar
        return None

    rc = day_pos[candle_index - 1]  # range candle (0-based position within the day)
    range_high = highs[rc]
    range_low  = lows[rc]
    if not np.isfinite(range_high) or not np.isfinite(range_low) or range_high <= range_low:
        return None

    # ── Find the first breakout, starting on the bar AFTER the range candle ──
    position = None
    for k in range(candle_index, n):
        i = day_pos[k]
        tmin = time_mins[i]
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]

        # No more new entries at/after the cut-off
        if tmin >= cutoff:
            break

        long_break  = enable_long  and h >= range_high
        short_break = enable_short and l <= range_low

        if not long_break and not short_break:
            continue

        # Resolve direction when a single bar breaches both sides
        go_long = long_break
        if long_break and short_break:
            if o >= range_high:
                go_long = True
            elif o <= range_low:
                go_long = False
            else:
                # open inside the range — assume the nearer level triggered first
                go_long = (range_high - o) <= (o - range_low)

        if go_long:
            entry = o if o > range_high else range_high   # gap-up fills at open
            sl_level = range_low
            risk = entry - sl_level
            tp_level = entry + rr * risk
            position = {'long': True, 'entry': entry, 'entry_ts': ts_strs[i],
                        'sl': sl_level, 'tp': tp_level, 'entry_pos': i}
        else:
            entry = o if o < range_low else range_low     # gap-down fills at open
            sl_level = range_high
            risk = sl_level - entry
            tp_level = entry - rr * risk
            position = {'long': False, 'entry': entry, 'entry_ts': ts_strs[i],
                        'sl': sl_level, 'tp': tp_level, 'entry_pos': i}
        break

    if position is None:
        return None

    # ── Manage the position from the entry bar onward ──
    is_long  = position['long']
    entry    = position['entry']
    sl_level = position['sl']
    tp_level = position['tp']
    start_k  = int(np.nonzero(day_pos == position['entry_pos'])[0][0])

    for k in range(start_k, n):
        i = day_pos[k]
        tmin = time_mins[i]
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]

        # Force square-off at the cut-off time (skip on the entry bar itself)
        if tmin >= cutoff and k > start_k:
            pnl = (o - entry) if is_long else (entry - o)
            return _make_trade(position, ts_strs[i], o, pnl, 'Time Exit')

        # SL is checked before Target (conservative) within the same bar
        if is_long:
            if l <= sl_level:
                return _make_trade(position, ts_strs[i], sl_level, sl_level - entry, 'SL Hit')
            if h >= tp_level:
                return _make_trade(position, ts_strs[i], tp_level, tp_level - entry, 'TG Hit')
        else:
            if h >= sl_level:
                return _make_trade(position, ts_strs[i], sl_level, entry - sl_level, 'SL Hit')
            if l <= tp_level:
                return _make_trade(position, ts_strs[i], tp_level, entry - tp_level, 'TG Hit')

    # Still open at the last bar of the day → close at EOD
    last_i = day_pos[-1]
    c = closes[last_i]
    pnl = (c - entry) if is_long else (entry - c)
    return _make_trade(position, ts_strs[last_i], c, pnl, 'EOD Exit')


# ── Option leg model ───────────────────────────────────────────────────────────

def _option_cfg(iv: float, strike_mode: str, strike_step: float) -> dict:
    """The option-leg model's settings. ₹ conversion is deliberately NOT in
    here: the screen's own Lot Value (₹/pt) box multiplies whichever basis is
    running, so index points and premium points are worth the same ₹/pt — the
    flag changes the points, nothing else."""
    mode = strike_mode if strike_mode in ('premium250', 'delta') else 'premium250'
    return {
        'iv':          max(0.01, float(iv or _DEFAULT_IV)),
        'strike_mode': mode,
        'strike_step': float(strike_step or _STRIKE_STEP),
    }


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via math.erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(s: float, k: float, t: float, r: float, sigma: float):
    vt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vt
    return d1, d1 - vt


def _bs_price(flag: str, s: float, k: float, t: float, r: float, sigma: float) -> float:
    """Black-Scholes price of a European CE ('c') / PE ('p')."""
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return max(0.0, (s - k) if flag == 'c' else (k - s))
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    disc = k * math.exp(-r * t)
    if flag == 'c':
        return s * _norm_cdf(d1) - disc * _norm_cdf(d2)
    return disc * _norm_cdf(-d2) - s * _norm_cdf(-d1)


def _bs_delta(flag: str, s: float, k: float, t: float, r: float, sigma: float) -> float:
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        itm = (s > k) if flag == 'c' else (s < k)
        return (1.0 if flag == 'c' else -1.0) if itm else 0.0
    d1, _ = _d1_d2(s, k, t, r, sigma)
    return _norm_cdf(d1) if flag == 'c' else _norm_cdf(d1) - 1.0


def _weekly_expiry_on_or_after(day: date) -> date:
    """Nearest weekly expiry on/after `day`, using the same NSE expiry-weekday
    regime table the expiry-breakout engine uses (Thursday up to Aug-2025,
    Tuesday from Sep-2025). No holiday calendar exists in this app, so an
    expiry that fell on a holiday is off by a day — worth a few paise of theta,
    not a different trade."""
    wd = _expiry_weekday_for(day)
    return day + timedelta(days=(wd - day.weekday()) % 7)


@functools.lru_cache(maxsize=100_000)
def _select_strike(flag: str, mode: str, spot_q: float, t_q: float, iv: float,
                   step: float = _STRIKE_STEP) -> float:
    """Strike the live algo would have bought, on quantised (spot, T) so the
    optimiser's repeated sweeps hit the cache instead of re-scanning the chain.

    'premium250' → strike priced nearest ₹250.
    'delta'      → |delta| nearest 0.90, and if that leg costs more than ₹500,
                   the same-type strike priced nearest below the cap instead.
    """
    atm = round(spot_q / step) * step
    strikes = [atm + i * step for i in range(-_STRIKE_WINDOW, _STRIKE_WINDOW + 1)]
    strikes = [k for k in strikes if k > 0]
    price = lambda k: _bs_price(flag, spot_q, k, t_q, _RISK_FREE, iv)

    if mode == 'delta':
        best = min(strikes, key=lambda k: abs(abs(_bs_delta(flag, spot_q, k, t_q, _RISK_FREE, iv))
                                              - _DELTA_TARGET))
        if price(best) > _MAX_PREMIUM:
            under = [k for k in strikes if price(k) <= _MAX_PREMIUM]
            if under:
                best = max(under, key=price)
        return best

    return min(strikes, key=lambda k: abs(price(k) - _PREM250_TARGET))


def _apply_option_pnl(trade: dict, cfg: dict) -> dict:
    """Re-book one index trade as the option leg the live algo would have bought.

    The index entry/exit/SL/target on the row are untouched — only `pnl` (and
    with it `result`) moves onto the premium scale, with the index figure kept
    as `index_pnl` so a row can still be read against the chart.
    """
    iv   = cfg['iv']
    flag = 'c' if trade['type'] == 'Long' else 'p'

    try:
        entry_dt = datetime.strptime(trade['entry_time'], '%Y-%m-%d %H:%M:%S')
        exit_dt  = datetime.strptime(trade['exit_time'],  '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        logger.warning("[2ndCandle] un-parseable trade timestamps — left on the index basis")
        return trade

    expiry_dt = datetime.combine(_weekly_expiry_on_or_after(entry_dt.date()), _EXPIRY_TIME)
    t_entry = max((expiry_dt - entry_dt).total_seconds(), _MIN_T_SECS) / _YEAR_SECS
    t_exit  = max((expiry_dt - exit_dt).total_seconds(),  _MIN_T_SECS) / _YEAR_SECS

    spot_in  = float(trade['entry_price'])
    spot_out = float(trade['exit_price'])

    # Quantised only for the strike CHOICE (5 spot points, ~5 minutes of T);
    # both premiums are then priced at the trade's exact spot and time.
    strike = _select_strike(flag, cfg['strike_mode'],
                            round(spot_in / 5.0) * 5.0, round(t_entry, 5), iv,
                            cfg['strike_step'])

    prem_in  = _bs_price(flag, spot_in,  strike, t_entry, _RISK_FREE, iv)
    prem_out = _bs_price(flag, spot_out, strike, t_exit,  _RISK_FREE, iv)
    # A bought option is worth nothing less than zero, and never below tick.
    prem_in  = max(prem_in,  0.05)
    prem_out = max(prem_out, 0.0)

    pnl = prem_out - prem_in          # always a BUY — CE for Long, PE for Short
    out = dict(trade)
    out['index_pnl']     = trade['pnl']
    out['option_type']   = 'CE' if flag == 'c' else 'PE'
    out['strike']        = int(strike)
    out['entry_premium'] = round(prem_in, 2)
    out['exit_premium']  = round(prem_out, 2)
    out['pnl']           = round(pnl, 2)
    out['result']        = 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH')
    return out


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_trade(position, exit_ts, exit_price, pnl, exit_reason):
    return {
        'entry_time':  position['entry_ts'],
        'exit_time':   exit_ts,
        'type':        'Long' if position['long'] else 'Short',
        'entry_price': round(position['entry'], 2),
        'exit_price':  round(exit_price, 2),
        'sl_price':    round(position['sl'], 2),
        'target_price': round(position['tp'], 2),
        'pnl':         round(pnl, 2),
        'result':      'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
        'exit_reason': exit_reason,
    }


def _summary(trades):
    if not trades:
        return {
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0.0, 'total_pnl': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0,
            'profit_factor': 0.0, 'max_drawdown': 0.0,
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
    }


# ── Optimiser ──────────────────────────────────────────────────────────────────

_CANDLE_GRID = [1, 2, 3, 4]
_RR_GRID     = [1.0, 1.5, 2.0, 2.5, 3.0]
_DIR_GRID    = [('both', True, True), ('long', True, False), ('short', False, True)]


def _opt_score(s: dict) -> float:
    pf  = s.get('profit_factor') or 0
    pnl = s.get('total_pnl', 0)
    if s.get('total_trades', 0) < 5 or pf <= 0 or pnl <= 0:
        return -999.0
    return pnl * (pf ** 0.5)


def optimise_second_candle(df, exit_hour: int = 15, exit_minute: int = 25,
                           min_trades: int = 5, option_pnl: bool = False,
                           option_iv: float = _DEFAULT_IV,
                           strike_mode: str = 'premium250',
                           strike_step: float = _STRIKE_STEP) -> list:
    """
    Sweep (range candle #, SL:Target ratio, direction) and rank the results.

    Arrays are extracted once; each combination only re-runs the per-day
    simulation. Returns results sorted best-first by _opt_score.

    `option_pnl` ranks the grid on the option premium instead of index points —
    the same basis flag the run takes, so the leaderboard is scored on whatever
    the screen is showing. It changes the ranking, not the signals: a wide 1:5
    target that looks best on the index can lose to theta on the premium.
    """
    cfg = _option_cfg(option_iv, strike_mode, strike_step)
    base = SecondCandleBacktestEngine(df=df)
    if base.df.empty:
        return []

    opens, highs, lows, closes, time_mins, ts_strs, day_positions = base._arrays()
    cutoff = exit_hour * 60 + exit_minute

    results = []
    for ci in _CANDLE_GRID:
        for rr in _RR_GRID:
            for dlabel, el, es in _DIR_GRID:
                trades = []
                for day_pos in day_positions:
                    t = _run_day(day_pos, opens, highs, lows, closes, time_mins,
                                 ts_strs, ci, rr, cutoff, el, es)
                    if t is not None:
                        trades.append(_apply_option_pnl(t, cfg) if option_pnl else t)
                s = _summary(trades)
                if s['total_trades'] < min_trades:
                    continue
                results.append({
                    'candle_index':  ci,
                    'rr_ratio':      rr,
                    'direction':     dlabel,
                    'total_trades':  s['total_trades'],
                    'wins':          s['wins'],
                    'losses':        s['losses'],
                    'win_rate':      s['win_rate'],
                    'total_pnl':     s['total_pnl'],
                    'profit_factor': s['profit_factor'],
                    'max_drawdown':  s['max_drawdown'],
                    'avg_win':       s['avg_win'],
                    'avg_loss':      s['avg_loss'],
                    'score':         round(_opt_score(s), 2),
                })

    results.sort(key=lambda x: x['score'], reverse=True)
    return results
