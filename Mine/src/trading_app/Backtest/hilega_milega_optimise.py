"""Find Best Params for Hilega Milega (Backtest/hilega_milega_engine.py).

The engine is built from the same fields the page posts to /backtest/hilega-milega,
and the board is the Simple Trader one (`mode='strategy'`), so the page renders and
"uses" a row the same way. Two stages, both on INDEX points:

  1. the grid — every combination of the numbers the rule turns on (RSI length,
     red-line length, arm window, RSI/red gap, entry and exit rule, target);
  2. the walk — from the best WALK_SEEDS rows of the grid, each of the page's other
     fields (green EMA, pullback band, sideways filter, daily bias, stop on close,
     direction, session) is tried one at a time and a value is kept when it ranks
     better, for up to WALK_PASSES passes. A full grid over those as well would be
     ~200,000 runs, and the more a sweep tries the likelier its top row is luck.

Positional, roll, expiry and premium only change how a trade is priced, not which
trades the index gives, so they are held as set. `verify_on_option` then re-prices the
current settings and the best BOARD_ROWS rows on the option exactly as the run does
(`attach_positional_legs` for a carried trade, `attach_option_legs` intraday), so the
board shows what BACKTEST shows.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from trading_app.Backtest.hilega_milega_engine import HilegaMilegaEngine
from trading_app.Backtest.st_bb_rsi_engine import (
    MIN_BOARD_TRADES, MIN_TRADES, _parse_hhmm, attach_option_legs, rank_quality, return_dd, rupee_card,
    summarise)

GRID = [
    ('rsi_period', (7, 9, 14)),
    ('red_len', (14, 21, 28)),
    ('arm_bars', (3, 5, 8)),
    ('min_gap', (0.0, 2.0, 5.0)),
    ('entry_mode', ('breakout', 'pullback', 'either')),
    ('exit_mode', ('red', 'green')),
    ('rr', (0.0, 2.0, 3.0)),
]
NAMES = [n for n, _ in GRID]
# The walk's fields, in the order they are tried. The session ones are dropped where the run ignores
# them: a daily candle has no clock, and a positional trade has no entry cut-off or square-off.
WALK = [
    ('green_len', (2, 3, 5)),
    ('pb_band', (5.0, 10.0, 15.0)),
    ('chop_lookback', (0, 8, 12, 20)),
    ('htf', ('off', 'daily')),
    ('sl_on_close', (True, False)),
    ('direction', ('both', 'long', 'short')),
    ('entry_start', ('09:15', '10:15')),
    ('max_trades_per_day', (1, 2)),
    ('entry_end', ('13:00', '15:14')),
    ('exit_cutoff', ('15:00', '15:14')),
]
_CLOCK = {'entry_start', 'entry_end', 'exit_cutoff'}
_INT = {'rsi_period', 'red_len', 'arm_bars', 'green_len', 'chop_lookback', 'max_trades_per_day'}
_STR = {'entry_mode', 'exit_mode', 'htf', 'direction'}
_BOOL = {'sl_on_close'}
_MIN1 = {'rsi_period', 'red_len', 'arm_bars', 'green_len', 'max_trades_per_day'}
WALK_SEEDS = 8          # best grid rows the walk starts from
WALK_PASSES = 2         # most passes over the walk's fields per seed
BOARD_ROWS = 15         # rows on the board, each re-priced on the option beside the current settings
# A carried trade is rarer than an intraday one: four years of 1-hour candles hold ~200, not thousands.
POSITIONAL_MIN_TRADES, POSITIONAL_MIN_BOARD = 10, 30
MIN_WIN = 35.0          # a trend rule that lets winners run wins ~40 % of the time; 50 % would empty the board
RANK_BY = 'return_dd'   # profit factor favours a rule that hardly trades; this is profit for the drawdown taken


def grid_size() -> int:
    n = 1
    for _, vals in GRID:
        n *= len(vals)
    return n


def walk_axes(eng: HilegaMilegaEngine) -> List[Tuple[str, tuple]]:
    skip = set()
    if eng.bar_minutes >= 1440:
        skip |= _CLOCK
    if eng.positional:
        skip |= {'entry_end', 'exit_cutoff'}
    return [(k, v) for k, v in WALK if k not in skip]


def build(df: pd.DataFrame, d: Dict[str, Any], minutes_per_bar: int,
          minutes: Optional[pd.DataFrame] = None) -> HilegaMilegaEngine:
    """The engine the run route builds for the same payload (`minutes` only times an intrabar
    stop for the option leg; the index figures do not depend on it)."""
    daily = minutes_per_bar >= 1440
    return HilegaMilegaEngine(
        df=df,
        rsi_period=int(d.get('rsi_period', 9) or 9),
        green_len=int(d.get('green_len', 3) or 3),
        red_len=int(d.get('red_len', 21) or 21),
        mid=float(d.get('mid', 50) or 50),
        arm_bars=int(d.get('arm_bars', 5) or 5),
        min_gap=float(d.get('min_gap', 0) or 0),
        exit_mode=str(d.get('exit_mode', 'green')),
        entry_mode=str(d.get('entry_mode', 'either')),
        pb_band=float(d.get('pb_band', 10) or 10),
        chop_lookback=int(d.get('chop_lookback', 12) or 0),
        chop_max=int(d.get('chop_max', 2) or 2),
        htf=str(d.get('htf', 'off')),
        rr=float(d.get('rr', 0) or 0),
        sl_on_close=bool(d.get('sl_on_close', True)),
        direction=str(d.get('direction', 'both')),
        entry_start=d.get('entry_start', '09:15'),
        entry_end=d.get('entry_end', '15:14'),
        exit_cutoff=d.get('exit_cutoff', '15:14'),
        max_trades_per_day=int(d.get('max_trades_per_day', 2) or 2),
        bar_minutes=minutes_per_bar,
        positional=bool(d.get('positional', False)) or daily,
        minutes=minutes,
    )


def _memo_indicators(eng: HilegaMilegaEngine) -> None:
    """The three lines depend only on the lengths; the volume-weighted MA's rolling apply is
    the slow part of a run, so each (rsi, green, red) set is computed once for the sweep."""
    cache: Dict[tuple, Dict[str, Any]] = {}
    raw = eng.indicators

    def indicators():
        key = (eng.rsi_period, eng.green_len, eng.red_len)
        if key not in cache:
            cache[key] = raw()
        return cache[key]
    eng.indicators = indicators


def apply_row(eng: HilegaMilegaEngine, row: Dict[str, Any]) -> None:
    for k in NAMES + [n for n, _ in WALK]:
        if k not in row:
            continue
        v = row[k]
        if k in _CLOCK:
            if eng.bar_minutes < 1440:                  # a daily candle keeps the engine's 00:00-24:00
                setattr(eng, k, _parse_hhmm(v, getattr(eng, k)))
        elif k in _STR:
            setattr(eng, k, str(v))
        elif k in _BOOL:
            setattr(eng, k, bool(v))
        elif k in _INT:
            setattr(eng, k, max(1 if k in _MIN1 else 0, int(v)))
        else:
            setattr(eng, k, max(0.0, float(v)))


def _hhmm(minutes: int) -> str:
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _row_params(eng: HilegaMilegaEngine, axes) -> Dict[str, Any]:
    out = {k: getattr(eng, k) for k in NAMES}
    for k, _ in axes:
        out[k] = _hhmm(getattr(eng, k)) if k in _CLOCK else getattr(eng, k)
    return out


def _trades(eng: HilegaMilegaEngine, from_date: str) -> List[Dict[str, Any]]:
    trades = eng.simulate()
    return [t for t in trades if t['entry_time'][:10] >= from_date] if from_date else trades


def _signature(trades) -> int:
    return hash(tuple((t['entry_time'], t['exit_time'], t['type']) for t in trades))


def optimise(df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int, from_date: str = '',
             top: int = BOARD_ROWS, progress: Optional[Callable] = None) -> Dict[str, Any]:
    eng = build(df, data, minutes_per_bar)
    _memo_indicators(eng)
    axes = walk_axes(eng)
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
    total = grid_size() + walk_runs
    done = 0
    for combo in itertools.product(*[v for _, v in GRID]):
        score({**held, **dict(zip(NAMES, combo))})
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

    # One row per distinct set of trades: a field the rule ignores (the pullback band on a breakout
    # entry, say) would otherwise fill the board with copies of the same run.
    rows, seen = [], set()
    for r in sorted((r for r in scored.values() if r['total_trades'] >= min_trades), key=rank_key):
        if r['_sig'] in seen:
            continue
        seen.add(r['_sig'])
        rows.append({k: v for k, v in r.items() if k != '_sig'})
    current.pop('_sig', None)
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN, min_board=min_board, by=RANK_BY)
    swept = NAMES + [k for k, _ in axes]
    return {'total_combos_tested': len(scored), 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN, 'results': ordered[:top], 'best': ordered[0] if ordered else None,
            'recent_from': cut, 'min_trades': min_trades, 'min_board_trades': min_board,
            'positional': eng.positional, 'rank_by': RANK_BY,
            'mode': 'strategy', 'strategy': 'hilega_milega', 'swept': swept, 'current': current}


def verify_on_option(res: Dict[str, Any], df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int,
                     symbol: str, calendar, start_date: str, progress: Optional[Callable] = None,
                     shortlist: int = BOARD_ROWS, minutes: Optional[pd.DataFrame] = None) -> None:
    """Re-price the current settings and the best index rows on the option as the run does and
    replace res['results'] / res['current'] with those figures, re-ranked. Each row keeps its
    index figures as index_pnl / index_pf."""
    from trading_app.Backtest.hilega_milega_engine import attach_positional_legs
    from trading_app.Backtest.st_touch_break_optimise import book_entry
    eng = build(df, data, minutes_per_bar, minutes)
    _memo_indicators(eng)
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
