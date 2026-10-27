"""Find Best Params for ST Touch Break.

The sweep runs the engine on INDEX points over a small grid of the numbers the rule leaves
open — the SuperTrend (length, factor), which setups are taken (Bounce / Through / both), how
the stop works (a closing flip or an intrabar touch) and the minimum wick of the touch candle.
Deliberately NOT swept: the break candle's body filter and `outside_bar` — both read the break
candle's close, so a row that wins on them is a result a live order could not have had.

Like the Simple Trader boards, the best index rows (and the page's own settings) are then
re-priced on the option exactly as BACKTEST prices them (`price_with_reentries` around
`attach_option_legs`, the page's premium and SL / TSL) and re-ranked by `rank_quality` on
those numbers, so a row's figures are what BACKTEST shows for the same values.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import (
    MIN_BOARD_TRADES, MIN_TRADES, attach_option_legs, rank_quality, rupee_card, summarise)
from trading_app.Backtest.st_touch_break_engine import StTouchBreakEngine

ST_PERIOD = (7, 10, 14)
ST_MULT = (1.5, 2.0, 2.5, 3.0)
MODES = ('both', 'bounce', 'through')
STOP_MODE = ('close', 'touch')
MIN_WICK = (0.0, 25.0, 40.0)
SWEPT = ['st_period', 'st_mult', 'modes', 'stop_mode', 'min_wick_pct']
MIN_WIN = 25.0                     # the Simple Trader boards want 50 %; this rule rides trends and wins ~35 % of the time
SHORTLIST = 10                     # index rows re-priced on the option (plus the current settings)


def grid_size() -> int:
    return len(ST_PERIOD) * len(ST_MULT) * len(MODES) * len(STOP_MODE) * len(MIN_WICK)


def _f(data: Dict[str, Any], key: str, default: float) -> float:
    v = data.get(key)
    return float(v) if v not in (None, '') else default


def build(df: pd.DataFrame, data: Dict[str, Any], minutes: Optional[pd.DataFrame] = None,
          bar_minutes: int = 3) -> StTouchBreakEngine:
    """The engine from the same fields the run route takes."""
    return StTouchBreakEngine(
        df, st_period=int(_f(data, 'st_period', 10)), st_mult=_f(data, 'st_mult', 2),
        touch_tol_pct=_f(data, 'touch_tol_pct', 0), min_wick_pct=_f(data, 'min_wick_pct', 0),
        min_body_pct=_f(data, 'min_body_pct', 0),
        modes=str(data.get('modes', 'both')), direction=str(data.get('direction', 'both')),
        stop_mode=str(data.get('stop_mode', 'close')), outside_bar=str(data.get('outside_bar', 'close')),
        entry_start=data.get('entry_start', '09:15'), entry_end=data.get('entry_end', '15:14'),
        exit_cutoff=data.get('exit_cutoff', '15:14'),
        max_trades_per_day=int(_f(data, 'max_trades_per_day', 5)),
        minutes=minutes, bar_minutes=bar_minutes)


def _apply(eng: StTouchBreakEngine, row: Dict[str, Any]) -> None:
    eng.st_period, eng.st_mult = int(row['st_period']), float(row['st_mult'])
    eng.modes, eng.stop_mode = str(row['modes']), str(row['stop_mode'])
    eng.min_wick = min(1.0, max(0.0, float(row['min_wick_pct']) / 100.0))


def _params(eng: StTouchBreakEngine) -> Dict[str, Any]:
    return dict(st_period=eng.st_period, st_mult=eng.st_mult, modes=eng.modes,
                stop_mode=eng.stop_mode, min_wick_pct=round(eng.min_wick * 100, 2))


def _trades(eng: StTouchBreakEngine, from_date: str) -> List[Dict[str, Any]]:
    return [t for t in eng.simulate() if t['entry_time'][:10] >= from_date]


def optimise(df: pd.DataFrame, data: Dict[str, Any], from_date: str = '', progress: Optional[Callable] = None,
             top: int = 80) -> Dict[str, Any]:
    eng = build(df, data)                     # no minutes: bar stamps are enough for index points
    days = sorted({ts.strftime('%Y-%m-%d') for ts in eng.df.index if ts.strftime('%Y-%m-%d') >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    recent = lambda ts: round(sum(t['pnl'] for t in ts if t['entry_time'][:10] >= cut), 2)   # noqa: E731

    cur_trades = _trades(eng, from_date)      # the page's own settings, as they stand
    current = summarise(cur_trades)
    current.update(_params(eng), recent_pnl=recent(cur_trades))
    page = _params(eng)

    rows: List[Dict[str, Any]] = []
    total, done = grid_size(), 0
    for st_period, st_mult in itertools.product(ST_PERIOD, ST_MULT):
        for modes, stop_mode, wick in itertools.product(MODES, STOP_MODE, MIN_WICK):
            _apply(eng, dict(st_period=st_period, st_mult=st_mult, modes=modes, stop_mode=stop_mode,
                             min_wick_pct=wick))
            trades = _trades(eng, from_date)
            done += 1
            if len(trades) < MIN_TRADES:
                continue
            sm = summarise(trades)
            sm.update(_params(eng), recent_pnl=recent(trades))
            rows.append(sm)
        if progress:
            progress(done, total)
    _apply(eng, page)
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN)
    return {'total_combos_tested': total, 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN, 'results': ordered[:top], 'best': ordered[0] if ordered else None,
            'recent_from': cut, 'min_trades': MIN_TRADES, 'mode': 'strategy',
            'strategy': 'st_touch_break', 'swept': list(SWEPT), 'current': current}


def verify_on_option(res: Dict[str, Any], df: pd.DataFrame, minutes: pd.DataFrame, data: Dict[str, Any],
                     symbol: str, interval: str, calendar, start_date: str,
                     progress: Optional[Callable] = None, shortlist: int = SHORTLIST) -> None:
    """Re-price the current settings and the best index rows on the option as the run does, and
    replace res['results'] / res['current'] with those figures, ranked by `rank_quality`."""
    from trading_app.Backtest.st_ema_rsi_cci_engine import price_with_reentries
    mpb = int(''.join(ch for ch in str(interval) if ch.isdigit()) or 3)
    eng = build(df, data, minutes=minutes, bar_minutes=mpb)     # with minutes: entries at the crossing minute
    premium = float(_f(data, 'premium', 250)) or None
    sl_pct, tsl_x, tsl_y = (max(0.0, _f(data, k, 0.0)) for k in ('sl_pct', 'tsl_x', 'tsl_y'))
    cut = res.get('recent_from') or ''
    cur = res.get('current')

    todo, seen = [], set()
    for row in ([cur] if cur is not None else []) + res['results']:
        if row is not cur and len(todo) - (cur is not None) >= shortlist:
            break
        _apply(eng, row)
        trades = _trades(eng, start_date)
        sig = tuple(t['entry_time'] for t in trades)
        if row is not cur and sig in seen:
            continue
        seen.add(sig)
        todo.append((row, trades))

    rows, current = [], None
    for n, (row, trades) in enumerate(todo, 1):
        if progress:
            progress(n - 1, len(todo))
        _apply(eng, row)                         # trade_after() re-reads the engine's numbers
        priced, info = price_with_reentries(
            eng, trades,
            lambda batch, prog: attach_option_legs(symbol, batch, premium, mpb, prog, calendar=calendar,
                                                   sl_pct=sl_pct, tsl_x=tsl_x, tsl_y=tsl_y))
        out = {k: row[k] for k in SWEPT}
        out.update(summarise(priced))
        out.update(rupee_card(priced, data, info.get('lot')))
        out.update(index_pnl=row['total_pnl'], index_pf=row['profit_factor'],
                   unpriced=info.get('unpriced', 0), reentries=info.get('reentries', 0),
                   recent_pnl=round(sum(t['pnl'] for t in priced if t['entry_time'][:10] >= cut), 2))
        if row is cur:
            current = out
        else:
            rows.append(out)
    if progress:
        progress(len(todo), len(todo))
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN,
                                   min_board=res.get('min_board_trades', MIN_BOARD_TRADES))
    res.update(results=ordered, best=ordered[0] if ordered else None, current=current,
               passed=passed, basis='option', verified=len(todo))
