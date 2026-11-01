"""Find Best Params for MA Cross + HTF (Backtest/ma_htf_engine.py).

The engine is built from the same fields the page posts to /backtest/ma-htf, and the board
is the Simple Trader one (`mode='strategy'`), so the page renders and "uses" a row the same
way. Two stages, both on INDEX points:

  1. the grid — every combination of the numbers the rule turns on (fast / slow SMMA, HTF
     timeframe and length, confirm candles, entry window, stop);
  2. the walk — from the best WALK_SEEDS rows of the grid, every other field on the page
     (HTF type and value, opposite-cross exit, trailing stop, partial booking, ADX, whipsaw
     window and limit, day bias, direction, session, trades a day) is tried one at a time and
     a value is kept when it ranks better, for up to WALK_PASSES passes. A full grid over
     those as well would be millions of runs, and the more a sweep tries the likelier its
     top row is luck.

Positional, roll, expiry, premium and the capital boxes only change how a trade is priced,
not which trades the index gives, so they are held as set. Partial booking is index-only
(the option pricing books one exit per trade), so it is swept only when Price on options is
off. `verify_on_option` then re-prices the current settings and the best BOARD_ROWS rows on
the option exactly as the run does, so the board shows what BACKTEST shows.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from trading_app.Backtest.ma_htf_engine import MaHtfEngine
from trading_app.Backtest.st_bb_rsi_engine import (
    MIN_BOARD_TRADES, MIN_TRADES, _parse_hhmm, attach_option_legs, rank_quality, return_dd, rupee_card,
    summarise)

HTF_CHOICES = (15, 30, 60, 120)     # the page's HTF dropdown, less 5 min; the grid takes the 3 above the chart's
GRID = [
    ('fast_len', (9, 11, 14)),
    ('slow_len', (21, 30)),
    ('htf_minutes', None),           # filled per chart timeframe by `grid_axes`
    ('htf_len', (21, 50)),
    ('confirm_bars', (0, 1, 2)),
    ('max_wait', (3, 5)),
    ('sl_points', (25.0, 35.0, 50.0, 0.0)),
]
NAMES = [n for n, _ in GRID]
# The walk's fields, in the order they are tried. Dropped where the run ignores them: a positional
# trade has no square-off, and partial booking cannot be priced on the option.
WALK = [
    ('htf_type', ('sma', 'ema', 'smma')),
    ('htf_calc', ('live', 'closed')),
    ('exit_on_cross', (True, False)),
    ('trail_points', (0.0, 20.0, 40.0)),
    ('partial_pct', (0.0, 50.0)),
    ('adx_min', (0.0, 15.0, 20.0, 25.0)),
    ('chop_lookback', (0, 10, 20)),
    ('chop_max', (1, 2, 3)),
    ('day_bias', (False, True)),
    ('direction', ('both', 'long', 'short')),
    ('entry_start', ('09:30', '09:45', '10:00')),
    ('entry_end', ('13:30', '14:30', '15:00')),
    ('exit_cutoff', ('15:00', '15:14')),
    ('max_trades_per_day', (1, 2, 3)),
]
_CLOCK = {'entry_start', 'entry_end', 'exit_cutoff'}
_INT = {'fast_len', 'slow_len', 'htf_minutes', 'htf_len', 'confirm_bars', 'max_wait', 'chop_lookback',
        'chop_max', 'max_trades_per_day'}
_STR = {'htf_type', 'htf_calc', 'direction'}
_BOOL = {'exit_on_cross', 'day_bias'}
_MIN1 = {'fast_len', 'max_trades_per_day', 'max_wait'}
# What `signals()` reads: a run that only changes the exit reuses the entries of the last one.
_SIGNAL_KEYS = ('fast_len', 'slow_len', 'htf_len', 'htf_minutes', 'htf_type', 'htf_calc', 'confirm_bars',
                'max_wait', 'entry_start', 'entry_end', 'adx_min', 'adx_len', 'chop_lookback', 'chop_max',
                'day_bias', 'direction')
WALK_SEEDS = 8          # best grid rows the walk starts from
WALK_PASSES = 2         # most passes over the walk's fields per seed
BOARD_ROWS = 15         # rows on the board, each re-priced on the option beside the current settings
POSITIONAL_MIN_TRADES, POSITIONAL_MIN_BOARD = 10, 30
MIN_WIN = 30.0          # a cross that lets winners run with a tight stop wins ~30 % of the time
RANK_BY = 'return_dd'   # profit factor favours a rule that hardly trades; this is profit for the drawdown taken


def htf_values(bar_minutes: int) -> Tuple[int, ...]:
    """Three higher timeframes above the chart's own (a 3-min chart: 15 / 30 / 60)."""
    above = [m for m in HTF_CHOICES if m > bar_minutes]
    return tuple(above[:3]) or (HTF_CHOICES[-1],)


def grid_axes(eng: MaHtfEngine) -> List[Tuple[str, tuple]]:
    return [(k, htf_values(eng.bar_minutes) if v is None else v) for k, v in GRID]


def grid_size(eng: MaHtfEngine) -> int:
    n = 1
    for _, vals in grid_axes(eng):
        n *= len(vals)
    return n


def walk_axes(eng: MaHtfEngine, price_options: bool) -> List[Tuple[str, tuple]]:
    skip = set()
    if eng.positional:
        skip.add('exit_cutoff')
    if price_options:
        skip.add('partial_pct')
    return [(k, v) for k, v in WALK if k not in skip]


def build(df: pd.DataFrame, d: Dict[str, Any], minutes_per_bar: int) -> MaHtfEngine:
    """The engine the run route builds for the same payload."""
    eng = MaHtfEngine(
        df=df,
        fast_len=int(d.get('fast_len', 11) or 11),
        slow_len=int(d.get('slow_len', 21) or 21),
        htf_len=int(d.get('htf_len', 21) or 21),
        htf_minutes=int(d.get('htf_minutes', 15) or 15),
        htf_type=str(d.get('htf_type', 'sma')),
        htf_calc=str(d.get('htf_calc', 'live')),
        confirm_bars=int(d.get('confirm_bars', 1) or 0),
        max_wait=int(d.get('max_wait', 5) or 5),
        sl_points=float(d.get('sl_points', 35) or 0),
        partial_pct=min(100.0, max(0.0, float(d.get('partial_pct', 0) or 0))) / 100.0,
        partial_points=float(d.get('partial_points', 0) or 0),
        exit_on_cross=bool(d.get('exit_on_cross', True)),
        direction=str(d.get('direction', 'both')),
        entry_start=d.get('entry_start', '09:30'),
        entry_end=d.get('entry_end', '14:30'),
        exit_cutoff=d.get('exit_cutoff', '15:14'),
        max_trades_per_day=int(d.get('max_trades_per_day', 2) or 2),
        bar_minutes=minutes_per_bar,
        positional=bool(d.get('positional', False)),
        trail_points=float(d.get('trail_points', 0) or 0),
        trail_start=float(d.get('trail_start', 0) or 0),
        adx_min=float(d.get('adx_min', 0) or 0),
        chop_lookback=int(d.get('chop_lookback', 0) or 0),
        chop_max=int(d.get('chop_max', 2) or 0),
        day_bias=bool(d.get('day_bias', False)),
    )
    # What the page typed, before the constructor's defaults: re-applied when a row changes the stop.
    eng._page_partial_points = max(0.0, float(d.get('partial_points', 0) or 0))
    eng._page_trail_start = max(0.0, float(d.get('trail_start', 0) or 0))
    return eng


def _memo(eng: MaHtfEngine) -> None:
    """The lines depend only on the lengths and the HTF, the entries only on `_SIGNAL_KEYS`; both
    are pure-Python loops over every candle, so each set is computed once for the sweep."""
    lines: Dict[tuple, Dict[str, Any]] = {}
    sigs: Dict[tuple, Dict[str, Any]] = {}
    raw_ind, raw_sig = eng.indicators, eng.signals

    def indicators():
        key = (eng.fast_len, eng.slow_len, eng.htf_len, eng.htf_minutes, eng.htf_type, eng.htf_calc)
        if key not in lines:
            lines[key] = raw_ind()
        return lines[key]

    def signals():
        key = tuple(getattr(eng, k) for k in _SIGNAL_KEYS)
        if key not in sigs:
            sigs[key] = raw_sig()
        return sigs[key]
    eng.indicators, eng.signals = indicators, signals


def apply_row(eng: MaHtfEngine, row: Dict[str, Any]) -> None:
    for k in NAMES + [n for n, _ in WALK]:
        if k not in row:
            continue
        v = row[k]
        if k in _CLOCK:
            setattr(eng, k, _parse_hhmm(v, getattr(eng, k)))
        elif k in _STR:
            setattr(eng, k, str(v))
        elif k in _BOOL:
            setattr(eng, k, bool(v))
        elif k == 'partial_pct':                        # the page's percent; the engine's fraction
            eng.partial_pct = min(1.0, max(0.0, float(v) / 100.0))
        elif k in _INT:
            setattr(eng, k, max(1 if k in _MIN1 else 0, int(v)))
        else:
            setattr(eng, k, max(0.0, float(v)))
    # The constructor's derived values, as it would set them for these numbers.
    eng.slow_len = max(2, eng.slow_len)
    eng.htf_len = max(2, eng.htf_len)
    eng.max_wait = max(eng.confirm_bars, eng.max_wait)
    eng.htf_minutes = max(eng.htf_minutes, eng.bar_minutes)
    eng.partial_points = getattr(eng, '_page_partial_points', 0.0) or eng.sl_points
    if eng.partial_points <= 0:
        eng.partial_pct = 0.0
    eng.trail_start = getattr(eng, '_page_trail_start', 0.0) or eng.trail_points


def _hhmm(minutes: int) -> str:
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _row_params(eng: MaHtfEngine, axes) -> Dict[str, Any]:
    out = {k: getattr(eng, k) for k in NAMES}
    for k, _ in axes:
        if k in _CLOCK:
            out[k] = _hhmm(getattr(eng, k))
        elif k == 'partial_pct':
            out[k] = round(eng.partial_pct * 100.0, 2)
        else:
            out[k] = getattr(eng, k)
    return out


def _trades(eng: MaHtfEngine, from_date: str) -> List[Dict[str, Any]]:
    trades = eng.simulate()
    return [t for t in trades if t['entry_time'][:10] >= from_date] if from_date else trades


def _signature(trades) -> int:
    return hash(tuple((t['entry_time'], t['exit_time'], t['type'], t['pnl']) for t in trades))


def optimise(df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int, from_date: str = '',
             top: int = BOARD_ROWS, progress: Optional[Callable] = None) -> Dict[str, Any]:
    eng = build(df, data, minutes_per_bar)
    _memo(eng)
    price_options = bool(data.get('price_options', True))
    grid = grid_axes(eng)
    names = [k for k, _ in grid]
    axes = walk_axes(eng, price_options)
    min_trades, min_board = ((POSITIONAL_MIN_TRADES, POSITIONAL_MIN_BOARD) if eng.positional
                             else (MIN_TRADES, MIN_BOARD_TRADES))
    days = sorted({str(t)[:10] for t in eng.df.index if str(t)[:10] >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    held = _row_params(eng, axes)                       # the page's own settings

    def passes(r):
        return (r['win_rate'] >= MIN_WIN and r['recent_pnl'] > 0 and r['total_pnl'] > 0
                and r['total_trades'] >= min_board)

    def rank_key(r):                                    # rank_quality's order, passing rows first
        return (not passes(r), -r['ret_dd'], -r['total_pnl'], r['max_drawdown'])

    scored: Dict[tuple, Dict[str, Any]] = {}

    def score(params):
        key = tuple(params[k] for k in sorted(params))
        if key in scored:
            return scored[key]
        apply_row(eng, params)
        trades = _trades(eng, from_date)
        sm = summarise(trades)
        sm.update(params)
        sm['recent_pnl'] = round(sum(t['pnl'] for t in trades if t['entry_time'][:10] >= cut), 2)
        sm['ret_dd'] = return_dd(sm)
        sm['_sig'] = _signature(trades)
        scored[key] = sm
        return sm

    current = dict(score(held))
    walk_runs = WALK_SEEDS * WALK_PASSES * sum(len(v) - 1 for _, v in axes)
    total = grid_size(eng) + walk_runs
    done = 0
    for combo in itertools.product(*[v for _, v in grid]):
        score({**held, **dict(zip(names, combo))})
        done += 1
        if progress and done % 20 == 0:
            progress(done, total)

    # The walk: one field at a time from the best grid rows, keeping a value that ranks better.
    grid_rows = sorted((r for r in scored.values() if r['total_trades'] >= min_trades), key=rank_key)
    seeds, seen = [], set()
    for r in grid_rows:
        if r['_sig'] not in seen:
            seen.add(r['_sig'])
            seeds.append(r)
        if len(seeds) >= WALK_SEEDS:
            break
    for seed in seeds:
        best = seed
        for _ in range(WALK_PASSES):
            moved = False
            for k, vals in axes:
                for v in vals:
                    if v == best[k]:
                        continue
                    cand = score({**{n: best[n] for n in held}, k: v})
                    done += 1
                    if progress and done % 20 == 0:
                        progress(min(done, total), total)
                    if cand['total_trades'] >= min_trades and rank_key(cand) < rank_key(best):
                        best, moved = cand, True
            if not moved:
                break
    if progress:
        progress(total, total)

    # One row per distinct set of trades: a field the rule ignores (the whipsaw limit with the
    # window off, say) would otherwise fill the board with copies of the same run.
    rows, seen = [], set()
    for r in sorted((r for r in scored.values() if r['total_trades'] >= min_trades), key=rank_key):
        if r['_sig'] in seen:
            continue
        seen.add(r['_sig'])
        rows.append({k: v for k, v in r.items() if k != '_sig'})
    current.pop('_sig', None)
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN, min_board=min_board, by=RANK_BY)
    swept = names + [k for k, _ in axes]
    return {'total_combos_tested': len(scored), 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN, 'results': ordered[:top], 'best': ordered[0] if ordered else None,
            'recent_from': cut, 'min_trades': min_trades, 'min_board_trades': min_board,
            'positional': eng.positional, 'rank_by': RANK_BY,
            'mode': 'strategy', 'strategy': 'ma_htf', 'swept': swept, 'current': current}


def verify_on_option(res: Dict[str, Any], df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int,
                     symbol: str, calendar, start_date: str, progress: Optional[Callable] = None,
                     shortlist: int = BOARD_ROWS) -> None:
    """Re-price the current settings and the best index rows on the option as the run does and
    replace res['results'] / res['current'] with those figures, re-ranked. Each row keeps its
    index figures as index_pnl / index_pf."""
    from trading_app.Backtest.hilega_milega_engine import attach_positional_legs
    from trading_app.Backtest.st_touch_break_optimise import book_entry
    eng = build(df, data, minutes_per_bar)
    _memo(eng)
    premium = float(data.get('premium') or 0)       # 0 = a stock, bought ATM intraday (as the run)
    cut = res.get('recent_from') or ''
    names = res['swept']

    def trades_for(row):
        apply_row(eng, row)
        return _trades(eng, start_date)

    cur = res.get('current')
    todo = [(cur, trades_for(cur))] if cur is not None else []
    for row in res['results'][:shortlist]:
        todo.append((row, trades_for(row)))

    rows, current = [], None
    for n, (row, trades) in enumerate(todo, 1):
        if progress:
            progress(n - 1, len(todo))
        if eng.positional:
            priced, info = attach_positional_legs(
                symbol, trades, premium or 250.0, None, calendar=calendar, roll=str(data.get('roll', 'same')),
                min_sessions=max(0, int(data.get('min_sessions', 5) or 0)))
        else:
            priced, info = attach_option_legs(symbol, trades, premium or None, minutes_per_bar, None,
                                              calendar=calendar)
        out = {k: row[k] for k in names if k in row}
        out.update(summarise(priced))
        out.update(rupee_card(priced, data, info.get('lot')))
        out.update(index_pnl=row['total_pnl'], index_pf=row['profit_factor'],
                   unpriced=info.get('unpriced', 0),
                   recent_pnl=round(sum(t['pnl'] for t in priced if t['entry_time'][:10] >= cut), 2))
        out['book'] = [book_entry(t) for t in priced if t.get('pnl_rupees') is not None]
        if row is cur:
            current = out
        else:
            rows.append(out)
    if progress:
        progress(len(todo), len(todo))
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN,
                                   min_board=res.get('min_board_trades', MIN_BOARD_TRADES), by=RANK_BY)
    res.update(results=ordered, best=ordered[0] if ordered else None, current=current,
               passed=passed, basis='option', verified=len(todo))
