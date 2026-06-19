"""
Current & Previous VWAP (PL) Backtest Engine
Mirrors the Pine Script strategy exactly.

Entry (PL):
  Long : prevVWAP < currentVWAP, gap >= minGap, low <= prevVWAP,
         close > prevVWAP, high < currentVWAP
  Short: prevVWAP > currentVWAP, gap >= minGap, high >= prevVWAP,
         close < prevVWAP, low > currentVWAP

Filters:
  - Bar close time must be before 15:00 IST (mirrors time_close check)
  - One trade per day
  - No entry at/after exit time (15:25)

Exits (first trigger wins):
  TP       : entry ± tp_points  (checked against bar high/low)
  SL       : entry ∓ sl_points  (checked against bar high/low)
  Time     : 15:25 IST force close (at bar close)
  New day  : 9:15 IST safety close (at bar open)
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

_INTERVAL_MINUTES = {
    'minute': 1, '1minute': 1,
    '2minute': 2, '3minute': 3, '4minute': 4,
    '5minute': 5, '10minute': 10, '15minute': 15,
    '30minute': 30, '60minute': 60, 'hour': 60,
    'day': 375,
}


class VWAPBacktestEngine:

    def __init__(
        self,
        df: pd.DataFrame,
        min_gap_points: float = 30.0,
        tp_points: float = 150.0,
        sl_points: float = 50.0,
        exit_hour: int = 15,
        exit_minute: int = 25,
        interval: str = '5minute',
    ):
        self.df = df.copy()
        self.min_gap = min_gap_points
        self.tp = tp_points
        self.sl = sl_points
        self.exit_hour = exit_hour
        self.exit_minute = exit_minute
        self.interval = interval
        self.interval_mins = _INTERVAL_MINUTES.get(interval, 5)
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
        self.df.columns = [c.lower() for c in self.df.columns]

    # ── VWAP ───────────────────────────────────────────────────────────────────

    def _calc_vwap(self):
        """Vectorized VWAP computation using groupby-cumsum (no Python loops)."""
        df = self.df
        vol = df['volume'].where(df['volume'] > 0, 1.0)
        typical = (df['high'] + df['low'] + df['close']) / 3.0
        pv = typical * vol

        date_key = df.index.normalize()
        df['current_vwap'] = pv.groupby(date_key).cumsum() / vol.groupby(date_key).cumsum()

        # prev_vwap = last current_vwap of the previous session
        day_end = df.groupby(date_key)['current_vwap'].last()
        df['prev_vwap'] = date_key.map(day_end.shift(1))

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        self._calc_vwap()
        df = self.df

        highs  = df['high'].to_numpy(dtype=float)
        lows   = df['low'].to_numpy(dtype=float)
        opens  = df['open'].to_numpy(dtype=float)
        closes = df['close'].to_numpy(dtype=float)
        cv_arr = df['current_vwap'].to_numpy(dtype=float)
        pv_arr = df['prev_vwap'].to_numpy(dtype=float)
        idx    = df.index
        n      = len(df)

        hours_arr = idx.hour
        mins_arr  = idx.minute
        date_ids  = idx.year * 10000 + idx.month * 100 + idx.day
        ts_strs   = idx.strftime('%Y-%m-%d %H:%M').to_numpy()

        trades = _run_trades_numpy(
            highs, lows, opens, closes, cv_arr, pv_arr,
            hours_arr, mins_arr, date_ids, ts_strs, n,
            self.min_gap, self.tp, self.sl, self.interval_mins,
            self.exit_hour, self.exit_minute,
        )
        return trades, _summary(trades)


# ── Numpy inner-loop simulation ────────────────────────────────────────────────

def _run_trades_numpy(
    highs, lows, opens, closes, cv_arr, pv_arr,
    hours_arr, mins_arr, date_ids, ts_strs, n,
    min_gap, tp, sl, interval_mins,
    exit_hour=15, exit_minute=25,
):
    """
    Trade simulation with all time/date data pre-extracted as numpy arrays.
    Avoids per-bar Timestamp object creation.
    """
    exit_total = exit_hour * 60 + exit_minute
    trades = []
    position = None
    trade_done_today = False
    last_date = -1

    for i in range(n):
        bh       = hours_arr[i]
        bm       = mins_arr[i]
        date_id  = date_ids[i]

        # ── Daily reset ────────────────────────────────────────────────
        if date_id != last_date:
            trade_done_today = False
            last_date = date_id

            if position is not None:
                xp  = opens[i]
                ep  = position['entry_price']
                pnl = (xp - ep) if position['long'] else (ep - xp)
                trades.append(_make_trade(position, ts_strs[i], xp, pnl, '9:15 AM Exit'))
                position = None
                trade_done_today = False

        cv = cv_arr[i]
        pv = pv_arr[i]

        if cv != cv or pv != pv:   # fast NaN check
            continue

        # ── Exit processing ────────────────────────────────────────────
        if position is not None:
            ep        = position['entry_price']
            is_long   = position['long']
            tp_level  = position['tp_level']
            sl_level  = position['sl_level']
            time_exit = (bh == exit_hour and bm == exit_minute)
            h, l, c   = highs[i], lows[i], closes[i]

            if is_long:
                if h >= tp_level:
                    trades.append(_make_trade(position, ts_strs[i], tp_level, tp_level - ep, 'TP Hit'))
                    position = None
                elif l <= sl_level:
                    trades.append(_make_trade(position, ts_strs[i], sl_level, sl_level - ep, 'SL Hit'))
                    position = None
                elif time_exit:
                    trades.append(_make_trade(position, ts_strs[i], c, c - ep, '3:25 PM Exit'))
                    position = None
            else:
                if l <= tp_level:
                    trades.append(_make_trade(position, ts_strs[i], tp_level, ep - tp_level, 'TP Hit'))
                    position = None
                elif h >= sl_level:
                    trades.append(_make_trade(position, ts_strs[i], sl_level, ep - sl_level, 'SL Hit'))
                    position = None
                elif time_exit:
                    trades.append(_make_trade(position, ts_strs[i], c, ep - c, '3:25 PM Exit'))
                    position = None
            continue

        # ── Entry processing ───────────────────────────────────────────
        if trade_done_today:
            continue

        if bh * 60 + bm + interval_mins >= 900:   # bar must close before 15:00
            continue
        if bh * 60 + bm >= exit_total:
            continue

        gap = abs(cv - pv)
        if gap < min_gap:
            continue

        h, l, c = highs[i], lows[i], closes[i]

        if pv < cv and l <= pv and c > pv and h < cv:
            position = {'long': True,  'entry_price': c, 'entry_ts': ts_strs[i],
                        'tp_level': c + tp, 'sl_level': c - sl}
            trade_done_today = True
        elif pv > cv and h >= pv and c < pv and l > cv:
            position = {'long': False, 'entry_price': c, 'entry_ts': ts_strs[i],
                        'tp_level': c - tp, 'sl_level': c + sl}
            trade_done_today = True

    # Close any open position at end of data
    if position is not None:
        xp  = closes[-1]
        ep  = position['entry_price']
        pnl = (xp - ep) if position['long'] else (ep - xp)
        trades.append(_make_trade(position, ts_strs[-1], xp, pnl, 'End of Data'))

    return trades


# ── Fully vectorized optimiser inner loop ──────────────────────────────────────

def _optimise_combo(
    highs, lows, opens, closes, cv_arr, pv_arr,
    hours_arr, mins_arr, date_ids, day_end_idx, ts_strs, n,
    time_valid,        # bool array: time filters that don't depend on params
    min_gap, tp, sl,
    exit_hour=15, exit_minute=25,
):
    """
    Vectorized entry detection + compact exit loop.

    1. Compute all entry signals with numpy — O(n) vectorized.
    2. Reduce to first-signal-per-day with a tiny loop over signal indices only.
    3. Find each trade's exit with a vectorized forward scan over the day slice.
    """
    gap = np.abs(cv_arr - pv_arr)
    gap_ok = gap >= min_gap

    long_cond  = time_valid & gap_ok & (pv_arr < cv_arr) & (lows <= pv_arr) & (closes > pv_arr) & (highs < cv_arr)
    short_cond = time_valid & gap_ok & (pv_arr > cv_arr) & (highs >= pv_arr) & (closes < pv_arr) & (lows > cv_arr)

    # signal[i] = 1 (long), -1 (short), 0 (nothing)
    signal = np.where(long_cond, np.int8(1), np.where(short_cond, np.int8(-1), np.int8(0)))

    # Keep only the first signal per calendar day
    entry_indices = []
    entry_dirs    = []
    last_d = -1
    for i in np.nonzero(signal)[0]:        # iterate only signal bars (~few hundred)
        d = date_ids[i]
        if d != last_d:
            entry_indices.append(i)
            entry_dirs.append(signal[i])
            last_d = d

    # Simulate each trade's exit
    exit_total_mins = exit_hour * 60 + exit_minute
    trades = []

    for k in range(len(entry_indices)):
        i         = entry_indices[k]
        is_long   = entry_dirs[k] == 1
        ep        = closes[i]
        tp_level  = ep + tp if is_long else ep - tp
        sl_level  = ep - sl if is_long else ep + sl

        # Day slice: bars after entry up to end of day
        day_last  = day_end_idx[date_ids[i]]
        j_start   = i + 1
        j_end     = day_last + 1         # exclusive

        if j_start >= j_end:
            # Entry was the last bar of the day — check next bar (overnight exit)
            if j_end < n:
                xp  = opens[j_end]
                pnl = (xp - ep) if is_long else (ep - xp)
                trades.append(_make_trade2(
                    ts_strs[i], ts_strs[j_end],
                    is_long, ep, xp, pnl, '9:15 AM Exit',
                ))
            continue

        fwd_h  = highs[j_start:j_end]
        fwd_l  = lows[j_start:j_end]
        fwd_c  = closes[j_start:j_end]
        fwd_hm = hours_arr[j_start:j_end] * 60 + mins_arr[j_start:j_end]

        if is_long:
            tp_mask   = fwd_h >= tp_level
            sl_mask   = fwd_l <= sl_level
        else:
            tp_mask   = fwd_l <= tp_level
            sl_mask   = fwd_h >= sl_level
        time_mask = fwd_hm == exit_total_mins

        exit_mask = tp_mask | sl_mask | time_mask
        if not exit_mask.any():
            # Position still open at end of day → check next bar (overnight exit)
            next_bar = j_end
            if next_bar < n:
                xp  = opens[next_bar]
                pnl = (xp - ep) if is_long else (ep - xp)
                trades.append(_make_trade2(
                    ts_strs[i], ts_strs[next_bar],
                    is_long, ep, xp, pnl, '9:15 AM Exit',
                ))
            continue

        rel = int(np.argmax(exit_mask))
        abs_j = j_start + rel

        if tp_mask[rel]:
            xp      = tp_level
            pnl     = (tp_level - ep) if is_long else (ep - tp_level)
            reason  = 'TP Hit'
        elif sl_mask[rel]:
            xp      = sl_level
            pnl     = (sl_level - ep) if is_long else (ep - sl_level)
            reason  = 'SL Hit'
        else:
            xp      = fwd_c[rel]
            pnl     = (xp - ep) if is_long else (ep - xp)
            reason  = '3:25 PM Exit'

        trades.append(_make_trade2(ts_strs[i], ts_strs[abs_j], is_long, ep, xp, pnl, reason))

    return trades


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_trade(position, exit_ts, exit_price, pnl, exit_reason):
    return {
        'entry_time':  position['entry_ts'],
        'exit_time':   exit_ts,
        'type':        'Long' if position['long'] else 'Short',
        'entry_price': round(position['entry_price'], 2),
        'exit_price':  round(exit_price, 2),
        'pnl':         round(pnl, 2),
        'result':      'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
        'exit_reason': exit_reason,
    }


def _make_trade2(entry_ts, exit_ts, is_long, ep, xp, pnl, reason):
    return {
        'entry_time':  entry_ts,
        'exit_time':   exit_ts,
        'type':        'Long' if is_long else 'Short',
        'entry_price': round(ep, 2),
        'exit_price':  round(xp, 2),
        'pnl':         round(pnl, 2),
        'result':      'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
        'exit_reason': reason,
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

_MIN_GAP_GRID = [10, 20, 30, 40, 50, 75]
_TP_GRID      = [80, 100, 120, 150, 180, 200]
_SL_GRID      = [25, 30, 40, 50, 60, 75]


def _opt_score(s: dict) -> float:
    pf  = s.get('profit_factor') or 0
    pnl = s.get('total_pnl', 0)
    if s.get('total_trades', 0) < 5 or pf <= 0 or pnl <= 0:
        return -999.0
    return pnl * (pf ** 0.5)


def optimise_vwap(
    df: 'pd.DataFrame',
    interval: str = '5minute',
    min_trades: int = 5,
) -> list:
    """
    Sweep all (min_gap, tp, sl) combinations on a pre-fetched DataFrame.

    Strategy:
    - Vectorize VWAP computation once (groupby-cumsum).
    - Pre-extract all numpy arrays once (shared across all 216 combinations).
    - Per combination: detect entries with numpy masking (O(n)), then only
      iterate over actual entries (~hundreds) to simulate exits.

    Returns results sorted best-first (highest _opt_score).
    """
    # ── Prepare DataFrame once ─────────────────────────────────────────────────
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if not pd.api.types.is_datetime64_any_dtype(df.index):
        if 'date' in df.columns:
            df = df.set_index('date')
        elif 'datetime' in df.columns:
            df = df.set_index('datetime')
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    # ── Compute VWAP once (vectorised) ────────────────────────────────────────
    vol     = df['volume'].where(df['volume'] > 0, 1.0)
    typical = (df['high'] + df['low'] + df['close']) / 3.0
    pv      = typical * vol

    date_key = df.index.normalize()
    df['current_vwap'] = pv.groupby(date_key).cumsum() / vol.groupby(date_key).cumsum()
    day_end_vwap = df.groupby(date_key)['current_vwap'].last()
    df['prev_vwap'] = date_key.map(day_end_vwap.shift(1))

    # ── Extract numpy arrays once ─────────────────────────────────────────────
    idx        = df.index
    n          = len(df)
    highs      = df['high'].to_numpy(dtype=float)
    lows       = df['low'].to_numpy(dtype=float)
    opens      = df['open'].to_numpy(dtype=float)
    closes     = df['close'].to_numpy(dtype=float)
    cv_arr     = df['current_vwap'].to_numpy(dtype=float)
    pv_arr     = df['prev_vwap'].to_numpy(dtype=float)
    hours_arr  = idx.hour
    mins_arr   = idx.minute
    date_ids   = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy()
    ts_strs    = idx.strftime('%Y-%m-%d %H:%M').to_numpy()

    interval_mins = _INTERVAL_MINUTES.get(interval, 5)

    # ── Pre-compute parameter-independent time filters ────────────────────────
    bar_close_mins = hours_arr * 60 + mins_arr + interval_mins
    time_mins      = hours_arr * 60 + mins_arr
    exit_total     = 15 * 60 + 25   # 15:25 IST
    valid_vwap     = ~np.isnan(cv_arr) & ~np.isnan(pv_arr)
    time_valid     = valid_vwap & (bar_close_mins < 900) & (time_mins < exit_total)

    # ── Pre-compute day-end index map: date_id → last bar index of that day ───
    # Used by _optimise_combo to slice forward within a day.
    unique_dates, last_occurrence = np.unique(date_ids, return_index=True)
    # last_occurrence gives the FIRST index of each unique date; we need the LAST.
    # Compute last index of each day using the next day's first index - 1.
    first_of_day = np.concatenate([last_occurrence, [n]])
    last_of_day  = first_of_day[1:] - 1               # shape: (n_days,)
    day_end_map  = dict(zip(unique_dates, last_of_day))

    logger.info('[VWAP optimise] %d bars, %d combos', n, len(_MIN_GAP_GRID) * len(_TP_GRID) * len(_SL_GRID))

    # ── Grid sweep ────────────────────────────────────────────────────────────
    results = []
    for mg in _MIN_GAP_GRID:
        for tp in _TP_GRID:
            for sl in _SL_GRID:
                try:
                    trades = _optimise_combo(
                        highs, lows, opens, closes, cv_arr, pv_arr,
                        hours_arr, mins_arr, date_ids, day_end_map, ts_strs, n,
                        time_valid, mg, tp, sl,
                    )
                    s = _summary(trades)
                    if s['total_trades'] < min_trades:
                        continue
                    results.append({
                        'min_gap':       mg,
                        'tp_points':     tp,
                        'sl_points':     sl,
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
                except Exception as exc:
                    logger.debug('VWAP optimise skip: gap=%s tp=%s sl=%s — %s', mg, tp, sl, exc)

    results.sort(key=lambda x: x['score'], reverse=True)
    return results
