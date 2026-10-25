"""Find Best Params for the Simple Trader strategies 2-7 (the Quantman runs).

Each strategy keeps its own engine; this module only rebuilds that engine from the
same fields the page posts to its run route, sweeps a small grid of the numbers the
run picked by eye, and scores every combination on INDEX points. The premium stop-loss
and trailing stop need option candles (a Breeze read per trade), so they are not part
of the sweep: the board is a shortlist to confirm with a normal run on the option.

Strategy 1 (BB PP ST RSI WR) is `st_bb_rsi_engine.optimise_simple_trader`.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Tuple

import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import MIN_TRADES, _parse_hhmm, summarise

_ST_PERIOD = (7, 10, 14)
_ST_MULT = (1.5, 2.0, 2.5, 3.0)
_BB_PERIOD = (15, 20)
_BB_MULT = (2.0, 2.5, 3.0, 3.5)

# Knobs that StBbRsiEngine.simulate() takes as overrides; every other swept name is an
# attribute the engine's signals() reads on each call.
_OVERRIDES = {'st_period', 'st_mult', 'bb_period', 'bb_mult', 'rsi_level', 'wr_level'}


def _f(data: Dict[str, Any], key: str, default: float) -> float:
    v = data.get(key)
    return float(v) if v not in (None, '') else default


def _common(data: Dict[str, Any], default_start: str = '09:15') -> Dict[str, Any]:
    return dict(
        entry_start=data.get('entry_start', default_start),
        entry_end=data.get('entry_end', '15:14'),
        exit_cutoff=data.get('exit_cutoff', '15:14'),
        max_trades_per_day=int(_f(data, 'max_trades_per_day', 5)),
        direction=str(data.get('direction', 'long')),
    )


def _pp_bb(df, d):
    from trading_app.Backtest.st_rsi_pp_bb_engine import StRsiPpBbEngine
    return StRsiPpBbEngine(
        df, st_period=int(_f(d, 'st_period', 10)), st_mult=_f(d, 'st_mult', 2.3),
        rsi_period=int(_f(d, 'rsi_period', 14)), rsi_level=_f(d, 'rsi_level', 70),
        bb_period=int(_f(d, 'bb_period', 15)), bb_mult=_f(d, 'bb_mult', 3),
        **_common(d, '09:31'))


def _pp_bb_fut(df, d):
    from trading_app.Backtest.st_rsi_pp_bb_fut_engine import StRsiPpBbFutEngine
    return StRsiPpBbFutEngine(
        df, st_period=int(_f(d, 'st_period', 10)), st_mult=_f(d, 'st_mult', 1.8),
        rsi_period=int(_f(d, 'rsi_period', 20)), rsi_level=_f(d, 'rsi_level', 68),
        bb_period=int(_f(d, 'bb_period', 20)), bb_mult=_f(d, 'bb_mult', 3.3),
        require_green=bool(d.get('require_green', True)), **_common(d))


def _cci_wr_fut(df, d):
    from trading_app.Backtest.st_rsi_cci_wr_pp_bb_fut_engine import StRsiCciWrPpBbFutEngine
    return StRsiCciWrPpBbFutEngine(
        df, st_period=int(_f(d, 'st_period', 10)), st_mult=_f(d, 'st_mult', 2),
        rsi_period=int(_f(d, 'rsi_period', 14)), rsi_level=_f(d, 'rsi_level', 78),
        cci_period=int(_f(d, 'cci_period', 20)), cci_level=_f(d, 'cci_level', 280),
        wr_period=int(_f(d, 'wr_period', 14)), wr_level=_f(d, 'wr_level', -15),
        bb_period=int(_f(d, 'bb_period', 20)), bb_mult=_f(d, 'bb_mult', 3),
        require_green=bool(d.get('require_green', False)), **_common(d))


def _cpr_sx(df, d):
    from trading_app.Backtest.st_rsi_cpr_bb_sx_engine import StRsiCprBbSxEngine
    return StRsiCprBbSxEngine(
        df, st_period=int(_f(d, 'st_period', 10)), st_mult=_f(d, 'st_mult', 1.5),
        rsi_period=int(_f(d, 'rsi_period', 14)), rsi_level=_f(d, 'rsi_level', 70),
        bb_period=int(_f(d, 'bb_period', 20)), bb_mult=_f(d, 'bb_mult', 2.5),
        cpr_wide=_f(d, 'cpr_wide', 0.35), use_cpr_wide=_f(d, 'cpr_wide', 0.35) > 0, **_common(d))


def _cpr_wr_sx(df, d):
    from trading_app.Backtest.st_rsi_cpr_bb_wr_sx_engine import StRsiCprBbWrSxEngine
    return StRsiCprBbWrSxEngine(
        df, st_period=int(_f(d, 'st_period', 10)), st_mult=_f(d, 'st_mult', 1.5),
        rsi_period=int(_f(d, 'rsi_period', 14)), rsi_level=_f(d, 'rsi_level', 68),
        rsi_cap=_f(d, 'rsi_cap', 80), bb_period=int(_f(d, 'bb_period', 20)),
        bb_mult=_f(d, 'bb_mult', 2.5), wr_period=int(_f(d, 'wr_period', 14)),
        wr_level=_f(d, 'wr_level', -10), cpr_narrow=_f(d, 'cpr_narrow', 0.2),
        use_cpr_narrow=_f(d, 'cpr_narrow', 0.2) > 0, **_common(d))


def _ema_cci(df, d):
    from trading_app.Backtest.st_ema_rsi_cci_engine import StEmaRsiCciEngine
    return StEmaRsiCciEngine(
        df=df, st_period=int(_f(d, 'st_period', 10)), st_mult=_f(d, 'st_mult', 2),
        ema_period=int(_f(d, 'ema_period', 200)), rsi_period=int(_f(d, 'rsi_period', 20)),
        rsi_entry=_f(d, 'rsi_entry', 30), rsi_exit=_f(d, 'rsi_exit', 45),
        cci_period=int(_f(d, 'cci_period', 20)), cci_level=_f(d, 'cci_level', -200),
        wr_period=int(_f(d, 'wr_period', 14)), wr_level=_f(d, 'wr_level', -85),
        bb_period=int(_f(d, 'bb_period', 20)), bb_mult=_f(d, 'bb_mult', 2),
        cpr_narrow=_f(d, 'cpr_narrow', 0.2), use_cpr_narrow=bool(d.get('use_cpr_narrow', True)),
        entry_start=d.get('entry_start', '09:15'), entry_end=d.get('entry_end', '15:14'),
        exit_cutoff=d.get('exit_cutoff', '15:14'),
        max_trades_per_day=int(_f(d, 'max_trades_per_day', 5)),
        direction=str(d.get('direction', 'short')))


def _base_grid(rsi: Tuple[float, ...]) -> List[Tuple[str, tuple]]:
    return [('st_period', _ST_PERIOD), ('st_mult', _ST_MULT), ('bb_period', _BB_PERIOD),
            ('bb_mult', _BB_MULT), ('rsi_level', rsi)]


# strategy -> (engine builder, grid builder(data), runs on the NIFTY future)
SPECS: Dict[str, Tuple[Callable, Callable[[Dict[str, Any]], List[Tuple[str, tuple]]], bool]] = {
    'st_ema_rsi_cci': (_ema_cci, lambda d: [
        ('st_mult', (1.5, 2.0, 2.5)), ('rsi_entry', (25, 30, 35)), ('rsi_exit', (40, 45, 50)),
        ('cci_level', (-150, -200, -250)), ('wr_level', (-80, -85, -90))], True),
    'st_rsi_pp_bb': (_pp_bb, lambda d: _base_grid((65, 70, 75, 80)), False),
    'st_rsi_pp_bb_fut': (_pp_bb_fut, lambda d: _base_grid((62, 65, 68, 72)), True),
    'st_rsi_cci_wr_pp_bb_fut': (_cci_wr_fut, lambda d: _base_grid((72, 75, 78, 82)) + [
        ('cci_level', (200, 280, 350)), ('wr_level', (-10, -15, -25))], True),
    'st_rsi_cpr_bb_sx': (_cpr_sx, lambda d: _base_grid((65, 70, 75, 80)) + (
        [('cpr_wide', (0.25, 0.35, 0.5))] if _f(d, 'cpr_wide', 0.35) > 0 else []), False),
    'st_rsi_cpr_bb_wr_sx': (_cpr_wr_sx, lambda d: _base_grid((64, 68, 72, 76)) + [
        ('wr_level', (-5, -10, -20))] + (
        [('cpr_narrow', (0.15, 0.2, 0.3))] if _f(d, 'cpr_narrow', 0.2) > 0 else []), False),
}


def uses_future(strategy: str, symbol: str, data: Dict[str, Any]) -> bool:
    return SPECS[strategy][2] and symbol == 'NIFTY' and bool(data.get('use_future', True))


def grid_size(strategy: str, data: Dict[str, Any]) -> int:
    n = 1
    for _, vals in SPECS[strategy][1](data):
        n *= len(vals)
    return n


def optimise(strategy: str, df: pd.DataFrame, data: Dict[str, Any], from_date: str = '',
             top: int = 25, progress=None) -> Dict[str, Any]:
    build, grid_fn, _ = SPECS[strategy]
    eng = build(df, data)
    grid = grid_fn(data)
    names = [n for n, _ in grid]
    total = grid_size(strategy, data)
    is_sweep_engine = hasattr(eng, 'simulate') and hasattr(eng, '_base')      # StBbRsiEngine family
    base = eng._base() if is_sweep_engine else None
    days = sorted({str(t)[:10] for t in (base['ts'] if base else eng._context()['ts'])
                   if str(t)[:10] >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    rows: List[Dict[str, Any]] = []
    done = 0
    for combo in itertools.product(*[v for _, v in grid]):
        params = dict(zip(names, combo))
        if is_sweep_engine:
            over = {k: (float(v) if k in ('st_mult', 'bb_mult', 'rsi_level', 'wr_level') else v)
                    for k, v in params.items() if k in _OVERRIDES}
            for k, v in params.items():
                if k not in _OVERRIDES:
                    setattr(eng, k, float(v))
            trades = eng.simulate(**over)
        else:                                   # StEmaRsiCciEngine: no overrides, rebuild its signals
            for k, v in params.items():
                setattr(eng, k, float(v))
            eng._ctx = None
            trades = eng.simulate()
        if from_date:
            trades = [t for t in trades if t['entry_time'][:10] >= from_date]
        done += 1
        if progress and done % 20 == 0:
            progress(done, total)
        if len(trades) < MIN_TRADES:
            continue
        sm = summarise(trades)
        sm.update(params)
        sm['recent_pnl'] = round(sum(t['pnl'] for t in trades if t['entry_time'][:10] >= cut), 2)
        rows.append(sm)
    if progress:
        progress(total, total)
    rows.sort(key=lambda r: r['total_pnl'], reverse=True)
    return {'total_combos_tested': total, 'qualified': len(rows), 'results': rows[:top],
            'best': rows[0] if rows else None, 'recent_from': cut, 'min_trades': MIN_TRADES,
            'mode': 'strategy', 'strategy': strategy, 'swept': names}
