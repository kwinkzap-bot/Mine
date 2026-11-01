"""Find Best Params for Hilega Milega (Backtest/hilega_milega_engine.py).

The engine is built from the same fields the page posts to /backtest/hilega-milega,
the numbers the run picks by hand (RSI length, red-line length, arm window, RSI/red
gap, entry and exit rule, target) are swept on INDEX points, and the board is the
Simple Trader one (`mode='strategy'`), so the page renders and "uses" a row the same
way. The page's other settings — direction, positional, chop filter, daily bias,
session — are held as set.

`verify_on_option` then re-prices the current settings and the best few rows on the
option exactly as the run does (`attach_positional_legs` for a carried trade,
`attach_option_legs` intraday), so the board's top rows show what BACKTEST shows.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from trading_app.Backtest.hilega_milega_engine import HilegaMilegaEngine
from trading_app.Backtest.st_bb_rsi_engine import (
    MIN_BOARD_TRADES, MIN_TRADES, attach_option_legs, rank_quality, rupee_card, summarise)

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
_INT = {'rsi_period', 'red_len', 'arm_bars'}
_STR = {'entry_mode', 'exit_mode'}
# A carried trade is rarer than an intraday one: four years of 1-hour candles hold ~200, not thousands.
POSITIONAL_MIN_TRADES, POSITIONAL_MIN_BOARD = 10, 30
MIN_WIN = 35.0          # a trend rule that lets winners run wins ~40 % of the time; 50 % would empty the board
SHORTLIST = 6           # index rows re-priced on the option, beside the current settings
RANK_BY = 'return_dd'   # profit factor favours a rule that hardly trades; this is profit for the drawdown taken


def grid_size() -> int:
    n = 1
    for _, vals in GRID:
        n *= len(vals)
    return n


def build(df: pd.DataFrame, d: Dict[str, Any], minutes_per_bar: int) -> HilegaMilegaEngine:
    """The engine the run route builds for the same payload."""
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
    for k in NAMES:
        if k not in row:
            continue
        v = row[k]
        setattr(eng, k, str(v) if k in _STR else (max(1, int(v)) if k in _INT else max(0.0, float(v))))


def _row_params(eng: HilegaMilegaEngine) -> Dict[str, Any]:
    return {k: getattr(eng, k) for k in NAMES}


def _trades(eng: HilegaMilegaEngine, from_date: str) -> List[Dict[str, Any]]:
    trades = eng.simulate()
    return [t for t in trades if t['entry_time'][:10] >= from_date] if from_date else trades


def optimise(df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int, from_date: str = '',
             top: int = 25, progress: Optional[Callable] = None) -> Dict[str, Any]:
    eng = build(df, data, minutes_per_bar)
    _memo_indicators(eng)
    min_trades, min_board = ((POSITIONAL_MIN_TRADES, POSITIONAL_MIN_BOARD) if eng.positional
                             else (MIN_TRADES, MIN_BOARD_TRADES))
    days = sorted({str(t)[:10] for t in eng.df.index if str(t)[:10] >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''

    def score(trades, params):
        sm = summarise(trades)
        sm.update(params)
        sm['recent_pnl'] = round(sum(t['pnl'] for t in trades if t['entry_time'][:10] >= cut), 2)
        return sm

    # The page's own settings first, scored the same way, so the board says where the run sits.
    current = score(_trades(eng, from_date), _row_params(eng))
    total = grid_size()
    rows: List[Dict[str, Any]] = []
    for done, combo in enumerate(itertools.product(*[v for _, v in GRID]), 1):
        params = dict(zip(NAMES, combo))
        apply_row(eng, params)
        trades = _trades(eng, from_date)
        if progress and done % 20 == 0:
            progress(done, total)
        if len(trades) >= min_trades:
            rows.append(score(trades, params))
    if progress:
        progress(total, total)
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN, min_board=min_board, by=RANK_BY)
    return {'total_combos_tested': total, 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN, 'results': ordered[:top], 'best': ordered[0] if ordered else None,
            'recent_from': cut, 'min_trades': min_trades, 'min_board_trades': min_board,
            'positional': eng.positional, 'rank_by': RANK_BY,
            'mode': 'strategy', 'strategy': 'hilega_milega', 'swept': list(NAMES), 'current': current}


def verify_on_option(res: Dict[str, Any], df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int,
                     symbol: str, calendar, start_date: str, progress: Optional[Callable] = None,
                     shortlist: int = SHORTLIST) -> None:
    """Re-price the current settings and the best index rows on the option as the run does and
    replace res['results'] / res['current'] with those figures, re-ranked. Each row keeps its
    index figures as index_pnl / index_pf."""
    from trading_app.Backtest.hilega_milega_engine import attach_positional_legs
    from trading_app.Backtest.st_touch_break_optimise import book_entry
    eng = build(df, data, minutes_per_bar)
    _memo_indicators(eng)
    premium = float(data.get('premium') or 0)       # 0 = a stock, bought ATM intraday (as the run)
    cut = res.get('recent_from') or ''

    def trades_for(row):
        apply_row(eng, row)
        return _trades(eng, start_date)

    cur = res.get('current')
    todo = [(cur, trades_for(cur))] if cur is not None else []
    seen = {tuple(t['entry_time'] for t in todo[0][1])} if todo else set()
    for row in res['results']:
        if len(todo) - (cur is not None) >= shortlist:
            break
        trades = trades_for(row)
        sig = tuple((t['entry_time'], t['exit_time']) for t in trades)
        if sig in seen:
            continue
        seen.add(sig)
        todo.append((row, trades))

    rows, current = [], None
    for n, (row, trades) in enumerate(todo, 1):
        if progress:
            progress(n - 1, len(todo))
        if eng.positional:
            priced, info = attach_positional_legs(
                symbol, trades, premium or 250.0, None, calendar=calendar, roll=str(data.get('roll', 'same')),
                min_sessions=max(0, int(data.get('min_sessions', 5) or 0)))
        else:
            priced, info = attach_option_legs(symbol, trades, premium or None, minutes_per_bar, None, calendar=calendar)
        out = {k: row[k] for k in NAMES if k in row}
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
