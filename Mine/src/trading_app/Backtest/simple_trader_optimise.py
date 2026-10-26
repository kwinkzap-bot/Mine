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
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import MIN_BOARD_TRADES, MIN_TRADES, MIN_WIN_RATE, _parse_hhmm, attach_option_legs, rank_quality, rupee_card, summarise

_ST_PERIOD = (7, 10, 14)
_ST_MULT = (1.5, 2.0, 2.5, 3.0)
_BB_PERIOD = (15, 20)
_BB_MULT = (2.0, 2.5, 3.0, 3.5)

# Knobs that StBbRsiEngine.simulate() takes as overrides; every other swept name is an
# attribute the engine's signals() reads on each call.
_OVERRIDES = {'st_period', 'st_mult', 'bb_period', 'bb_mult', 'rsi_level', 'wr_level'}


# A carried (1-hour / 1-day) trade is a rarer event than an intraday one: a few years hold dozens, not hundreds.
POSITIONAL_MIN_TRADES, POSITIONAL_MIN_BOARD = 10, 30


def _thresholds(eng) -> Tuple[int, int]:
    """(rows kept in the sweep, trades a row needs to pass the board) for this engine."""
    return (POSITIONAL_MIN_TRADES, POSITIONAL_MIN_BOARD) if getattr(eng, 'positional', False) \
        else (MIN_TRADES, MIN_BOARD_TRADES)


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


def _all_ind(df, d):
    from trading_app.Backtest.st_all_indicators_engine import StAllIndicatorsEngine
    on = lambda k: bool(d.get(k, True))
    return StAllIndicatorsEngine(
        df, st_period=int(_f(d, 'st_period', 10)), st_mult=_f(d, 'st_mult', 2),
        rsi_period=int(_f(d, 'rsi_period', 14)), rsi_level=_f(d, 'rsi_level', 60),
        rsi_cap=_f(d, 'rsi_cap', 85), bb_period=int(_f(d, 'bb_period', 20)),
        bb_mult=_f(d, 'bb_mult', 2), wr_period=int(_f(d, 'wr_period', 14)),
        wr_level=_f(d, 'wr_level', -30), cci_period=int(_f(d, 'cci_period', 20)),
        cci_level=_f(d, 'cci_level', 100), ema_period=int(_f(d, 'ema_period', 200)),
        cpr_narrow=_f(d, 'cpr_narrow', 0.2), use_cpr_narrow=_f(d, 'cpr_narrow', 0.2) > 0,
        use_st=on('use_st'), use_rsi=on('use_rsi'), use_bb=on('use_bb'), use_wr=on('use_wr'),
        use_cci=on('use_cci'), use_ema=on('use_ema'), use_pivot=on('use_pivot'),
        positional=bool(d.get('positional', False)), **_common(d))


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
    'st_all_indicators': (_all_ind, lambda d: _base_grid((55, 60, 65, 70)) + [
        ('wr_level', (-20, -30, -40)), ('cci_level', (0, 100, 150))], False),
}


def uses_future(strategy: str, symbol: str, data: Dict[str, Any]) -> bool:
    return SPECS[strategy][2] and symbol == 'NIFTY' and bool(data.get('use_future', True))


def grid_size(strategy: str, data: Dict[str, Any]) -> int:
    n = 1
    for _, vals in SPECS[strategy][1](data):
        n *= len(vals)
    return n


# ALL Indicators: which gates are on is part of the search, not just their numbers.
GATE_LABEL = {'st': 'ST', 'rsi': 'RSI', 'bb': 'BB', 'wr': '%R', 'cci': 'CCI', 'ema': 'EMA', 'pivot': 'PP', 'cpr': 'CPR'}
_GATES = tuple(GATE_LABEL)
_GATE_PARAMS = {'st': ('st_period', 'st_mult'), 'bb': ('bb_period', 'bb_mult'), 'rsi': ('rsi_level',),
                'wr': ('wr_level',), 'cci': ('cci_level',)}
_DECIDERS = ('st', 'rsi', 'bb', 'wr', 'pivot')    # the base engine returns no signal unless one of these is on
GATE_SHORTLIST = 12                               # gate subsets (best on the page's own numbers) given the full number sweep


def _subset_label(on) -> str:
    return '+'.join(GATE_LABEL[g] for g in _GATES if g in on)


def _set_gates(eng, on, data: Dict[str, Any], cpr_narrow: float) -> None:
    for g in ('st', 'rsi', 'bb', 'wr', 'cci', 'ema', 'pivot'):
        setattr(eng, 'use_' + g, g in on)
    eng.use_cpr_narrow = 'cpr' in on
    eng.cpr_narrow = float(cpr_narrow)
    eng.rsi_cap = _f(data, 'rsi_cap', 85) if 'rsi' in on else 1e9      # RSI off = no band either


def _apply_row(eng, names, row: Dict[str, Any], data: Dict[str, Any]) -> None:
    """Put a board row's numbers (and, for ALL Indicators, its gate switches) on the engine."""
    for k in names:
        if k in row and k not in ('gates', 'timeframe'):
            setattr(eng, k, _num(k, row[k]))
    if 'use_st' in row:
        on = {g for g in _GATES if row.get('use_' + g)}
        _set_gates(eng, on, data, row.get('cpr_narrow') or 0.2)


def _simulate_one(eng, params: Dict[str, Any], from_date: str) -> List[Dict[str, Any]]:
    over = {k: (float(v) if k in ('st_mult', 'bb_mult', 'rsi_level', 'wr_level') else v)
            for k, v in params.items() if k in _OVERRIDES}
    for k, v in params.items():
        if k not in _OVERRIDES:
            setattr(eng, k, float(v))
    trades = eng.simulate(**over)
    return [t for t in trades if t['entry_time'][:10] >= from_date] if from_date else trades


def _optimise_gates(strategy: str, frames: List[Tuple[Optional[str], Dict[str, Any]]],
                    bars_for: Callable[[Optional[str]], Optional[pd.DataFrame]], from_date: str,
                    top: int, progress, floor: int = 0, page_tf: Optional[str] = None) -> Dict[str, Any]:
    """Find Best Params for ALL Indicators that also searches WHICH indicators to keep on —
    a rule with all eight gates is rarely the best one — and, given several frames, on which
    timeframe. `frames` is [(timeframe, fields)] and `bars_for(timeframe)` that frame's candles
    (one frame with timeframe None: the page's own run). Stage 1 scores every subset of the
    gates on every frame on the page's own numbers; stage 2 sweeps the numbers (only those of
    gates that are on) of the best GATE_SHORTLIST (frame, subset) pairs, every frame's best
    `floor`, and the page's own subset on the page's timeframe. One engine is held at a time:
    a 1-minute one carries hundreds of MB of indicator arrays."""
    build, grid_fn, _ = SPECS[strategy]
    named = frames[0][0] is not None            # rows say which timeframe they ran on
    subsets = [set(c) for r in range(1, len(_GATES) + 1) for c in itertools.combinations(_GATES, r)
               if set(c) & set(_DECIDERS)]
    held: Dict[int, Any] = {}

    def engine(i):
        if i not in held:
            held.clear()
            df = bars_for(frames[i][0])
            held[i] = build(df, frames[i][1]) if df is not None and not df.empty else None
        return held[i]

    def stamp(sm, i, on, params, trades):
        sm.update(params)
        sm.update(gates=_subset_label(on), recent_pnl=round(sum(t['pnl'] for t in trades if t['entry_time'][:10] >= cuts[i]), 2),
                  cpr_narrow=cpr_val if 'cpr' in on else 0.0, **{'use_' + g: g in on for g in _GATES})
        if named:
            sm['timeframe'] = frames[i][0]
        return sm

    def params_for(on):
        return {k: page[k] for g in on for k in _GATE_PARAMS.get(g, ())}

    def sweeps(on):
        ks = [k for g in _GATES if g in on for k in _GATE_PARAMS.get(g, ())]
        return [dict(zip(ks, v)) for v in itertools.product(*[grid[k] for k in ks])]

    def tick():
        if progress:
            progress(done, total, **({'timeframe': frames[i][0]} if named else {}))

    cuts: Dict[int, str] = {}
    grid: Dict[str, tuple] = {}
    page: Dict[str, Any] = {}
    page_on, cpr_val, page_i, current, positional = set(), 0.2, None, None, False
    stage1: List[Tuple[int, set, Dict[str, Any]]] = []
    total, done = len(subsets) * len(frames), 0
    for i, (tf, d) in enumerate(frames):
        eng = engine(i)
        if eng is None:                         # no candles for this timeframe
            total -= len(subsets)
            continue
        grid = dict(grid_fn(d))
        page = {n: getattr(eng, n) for n in grid}
        page_on = {g for g in _GATES[:-1] if getattr(eng, 'use_' + g)} | ({'cpr'} if eng.use_cpr_narrow else set())
        cpr_val = eng.cpr_narrow if eng.cpr_narrow > 0 else 0.2
        days = sorted({str(t)[:10] for t in eng._base()['ts'] if str(t)[:10] >= from_date})
        cuts[i] = days[int(len(days) * 0.7)] if days else ''
        min_trades, min_board = _thresholds(eng)
        positional = bool(getattr(eng, 'positional', False))
        if tf == page_tf:
            # the page's own settings, scored the same way
            page_i = i
            cur_trades = eng.simulate()
            if from_date:
                cur_trades = [t for t in cur_trades if t['entry_time'][:10] >= from_date]
            current = stamp(summarise(cur_trades), i, page_on, params_for(page_on), cur_trades)
        for on in subsets:
            _set_gates(eng, on, d, cpr_val)
            trades = _simulate_one(eng, params_for(on), from_date)
            done += 1
            if done % 10 == 0:
                tick()
            if len(trades) >= min_trades:
                stage1.append((i, on, stamp(summarise(trades), i, on, params_for(on), trades)))
        eng = None                              # `held` keeps the only reference, so the next frame frees it first
    if not cuts:                                # not one frame had candles
        return {'total_combos_tested': 0, 'qualified': 0, 'passed': 0, 'min_win': MIN_WIN_RATE,
                'results': [], 'best': None, 'recent_from': '', 'current': None, 'min_trades': 0,
                'min_board_trades': 0, 'positional': bool(frames[0][1].get('positional')),
                'mode': 'strategy', 'strategy': strategy, 'swept': [], 'timeframes': [],
                'gate_subsets': len(subsets), 'gate_shortlist': 0}
    keep = [x for x in stage1 if x[2]['total_pnl'] > 0]
    keep.sort(key=lambda x: (-x[2]['profit_factor'], x[2]['max_drawdown'], -x[2]['total_pnl']))
    chosen = [(i, on) for i, on, _ in keep[:GATE_SHORTLIST]]
    for f in cuts:
        chosen += [(i, on) for i, on, _ in [x for x in keep if x[0] == f][:floor] if (i, on) not in chosen]
    if page_i is not None and page_on and (page_i, page_on) not in chosen and page_on & set(_DECIDERS):
        chosen.append((page_i, page_on))
    total += sum(len(sweeps(on)) for _, on in chosen)
    rows: List[Dict[str, Any]] = []
    for i in sorted({i for i, _ in chosen}, reverse=True):     # last frame first: its engine is still held
        eng = engine(i)
        for on in [on for j, on in chosen if j == i]:
            _set_gates(eng, on, frames[i][1], cpr_val)
            for params in sweeps(on):
                trades = _simulate_one(eng, params, from_date)
                done += 1
                if done % 20 == 0:
                    tick()
                if len(trades) >= min_trades:
                    rows.append(stamp(summarise(trades), i, on, params, trades))
        eng = None
    held.clear()
    if progress:
        progress(total, total)
    ordered, passed = rank_quality(rows, current, min_board=min_board)
    return {'total_combos_tested': total, 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN_RATE, 'results': ordered[:top],
            'best': ordered[0] if ordered else None,
            'recent_from': cuts.get(page_i, next(iter(cuts.values()))), 'min_trades': min_trades,
            'min_board_trades': min_board, 'positional': positional,
            'mode': 'strategy', 'strategy': strategy,
            'swept': (['timeframe'] if named else []) + ['gates'] + list(grid) + ['cpr_narrow'],
            'current': current, 'timeframes': [frames[i][0] for i in cuts] if named else [],
            'gate_subsets': len(subsets), 'gate_shortlist': len(chosen)}


# ALL Indicators' Find Best Params also searches the timeframe, on two boards: intraday (in and
# out the same session, inside the page's window) and positional (carried across sessions until
# the SuperTrend turns or the premium stop is hit). The values are the Timeframe dropdown's own.
TIMEFRAMES = {'intraday': ('minute', '2minute', '3minute', '5minute', '15minute'),
              'positional': ('60minute', 'day')}
TF_FLOOR = 2        # every timeframe's best two go forward: subsets into the number sweep, rows onto the option


def optimise_timeframes(strategy: str, bars_for: Callable[[str], Optional[pd.DataFrame]],
                        data: Dict[str, Any], from_date: str = '', top: int = 80,
                        progress=None) -> Dict[str, Dict[str, Any]]:
    """{'intraday': board, 'positional': board}: the gate search above over every timeframe of
    each mode at once, ranked together, each row carrying its `timeframe`. The page's own
    settings are `current` on the board its Timeframe belongs to."""
    page_tf = str(data.get('interval') or '')
    boards = {}
    for mode, tfs in TIMEFRAMES.items():
        d = dict(data, positional=mode == 'positional')
        tick = (lambda done, total, mode=mode, **kw: progress(done, total, board=mode, **kw)) if progress else None
        boards[mode] = _optimise_gates(strategy, [(tf, dict(d, interval=tf)) for tf in tfs], bars_for,
                                       from_date, top, tick, floor=TF_FLOOR, page_tf=page_tf)
    return boards


def optimise(strategy: str, df: pd.DataFrame, data: Dict[str, Any], from_date: str = '',
             top: int = 25, progress=None) -> Dict[str, Any]:
    if strategy == 'st_all_indicators' and data.get('sweep_gates', True):
        return _optimise_gates(strategy, [(None, data)], lambda _: df, from_date, top, progress)
    build, grid_fn, _ = SPECS[strategy]
    eng = build(df, data)
    min_trades, min_board = _thresholds(eng)
    grid = grid_fn(data)
    names = [n for n, _ in grid]
    total = grid_size(strategy, data)
    is_sweep_engine = hasattr(eng, 'simulate') and hasattr(eng, '_base')      # StBbRsiEngine family
    base = eng._base() if is_sweep_engine else None
    days = sorted({str(t)[:10] for t in (base['ts'] if base else eng._context()['ts'])
                   if str(t)[:10] >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    # The page's own settings first (the sweep below overwrites the engine's attributes):
    # scored the same way, so the board can say where the current run sits on it.
    if not is_sweep_engine:
        eng._ctx = None
    cur_trades = eng.simulate()
    if from_date:
        cur_trades = [t for t in cur_trades if t['entry_time'][:10] >= from_date]
    current = summarise(cur_trades)
    current.update({n: getattr(eng, n) for n in names})
    current['recent_pnl'] = round(sum(t['pnl'] for t in cur_trades if t['entry_time'][:10] >= cut), 2)
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
        if len(trades) < min_trades:
            continue
        sm = summarise(trades)
        sm.update(params)
        sm['recent_pnl'] = round(sum(t['pnl'] for t in trades if t['entry_time'][:10] >= cut), 2)
        rows.append(sm)
    if progress:
        progress(total, total)
    ordered, passed = rank_quality(rows, current, min_board=min_board)
    return {'total_combos_tested': total, 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN_RATE, 'results': ordered[:top],
            'best': ordered[0] if ordered else None, 'recent_from': cut, 'min_trades': min_trades,
            'min_board_trades': min_board, 'positional': bool(getattr(eng, 'positional', False)),
            'mode': 'strategy', 'strategy': strategy, 'swept': names, 'current': current}


def _num(name: str, v):
    return int(v) if name.endswith('_period') else float(v)


SHORTLIST = 6        # index rows re-priced on the option, beside the current settings


def verify_on_option(strategy: str, res: Dict[str, Any], df: pd.DataFrame, data: Dict[str, Any],
                     symbol: str, interval: str, calendar, on_future: bool, start_date: str,
                     progress=None, shortlist: Optional[int] = None) -> None:
    """Re-price the current settings and the best index rows on the option, exactly as the
    strategy's own run does (`price_with_reentries` around `attach_option_legs`, the page's
    premium and SL / TSL, spot-level strikes on the future), and replace res['results'] /
    res['current'] with those figures, ranked by `rank_quality`. Each row keeps its index
    figures as index_pnl / index_pf. A priced row is what BACKTEST shows for the same values.
    `shortlist` = how many rows to price (default SHORTLIST / GATE_SHORTLIST); no current, none priced."""
    from trading_app.Backtest.st_ema_rsi_cci_engine import price_with_reentries, with_spot_level
    build, _, _ = SPECS[strategy]
    eng = build(df, data)
    names = res['swept']
    _p = data.get('premium')
    premium = float(250 if _p in (None, '') else _p) or None      # 0 = ATM (a stock), never the 250 default
    sl_pct = max(0.0, float(data.get('sl_pct') or 0))
    tsl_x = max(0.0, float(data.get('tsl_x') or 0))
    tsl_y = max(0.0, float(data.get('tsl_y') or 0))
    mpb = int(''.join(ch for ch in str(interval) if ch.isdigit()) or 3)
    cut = res.get('recent_from') or ''
    sweep_engine = hasattr(eng, '_base')
    positional = bool(getattr(eng, 'positional', False))

    def trades_for(row):
        _apply_row(eng, names, row, data)           # the row's numbers, as the run would have built it
        if not sweep_engine:
            eng._ctx = None
        return [t for t in eng.simulate() if t['entry_time'][:10] >= start_date]

    cur = res.get('current')
    todo = [(cur, trades_for(cur))] if cur is not None else []
    seen = {tuple(t['entry_time'] for t in todo[0][1])} if todo else set()
    limit = shortlist if shortlist is not None else (GATE_SHORTLIST if 'gates' in names else SHORTLIST)
    for row in res['results']:
        if len(todo) - (cur is not None) >= limit:
            break
        trades = trades_for(row)
        sig = tuple(t['entry_time'] for t in trades)
        if sig in seen:
            continue
        seen.add(sig)
        todo.append((row, trades))

    rows, current = [], None
    for n, (row, trades) in enumerate(todo, 1):
        if progress:
            progress(n - 1, len(todo))
        _apply_row(eng, names, row, data)           # trade_after() re-reads the engine's numbers
        if not sweep_engine:
            eng._ctx = None
        if positional:
            # One contract held (and rolled) until the index trade turns or the premium stop / trail
            # is hit, across as many sessions as that takes; no same-day re-entry.
            from trading_app.Backtest.hilega_milega_engine import attach_positional_legs
            priced, info = attach_positional_legs(
                symbol, with_spot_level(trades, symbol) if on_future else trades, premium or 0.0, None,
                calendar=calendar, roll=str(data.get('roll', 'same')),
                min_sessions=max(0, int(data.get('min_sessions', 5) or 0)),
                sl_pct=sl_pct, tsl_x=tsl_x, tsl_y=tsl_y)
        else:
            priced, info = price_with_reentries(
                eng, trades,
                lambda batch, prog: attach_option_legs(
                    symbol, with_spot_level(batch, symbol) if on_future else batch,
                    premium, mpb, prog, calendar=calendar, sl_pct=sl_pct, tsl_x=tsl_x, tsl_y=tsl_y))
        out = {k: row[k] for k in names if k in row}
        out.update({k: v for k, v in row.items() if k.startswith('use_')})
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
    ordered, passed = rank_quality(rows, current, min_board=res.get('min_board_trades', MIN_BOARD_TRADES))
    res.update(results=ordered, best=ordered[0] if ordered else None, current=current,
               passed=passed, basis='option', verified=len(todo))


def verify_timeframes(strategy: str, boards: Dict[str, Dict[str, Any]],
                      bars_for: Callable[[str], Optional[pd.DataFrame]], data: Dict[str, Any],
                      symbol: str, calendar, start_date: str, progress=None) -> None:
    """`verify_on_option` for `optimise_timeframes`' boards: each row is priced on its own
    timeframe's candles and engine — the board's best GATE_SHORTLIST index rows, every
    timeframe's best TF_FLOOR so none goes unseen, and the page's own settings — then the board
    is re-ranked on those figures. A board whose pricing fails (no ICICI login) keeps its index
    rows and says why in `option_error`."""
    for mode, board in boards.items():
        rows = board['results']
        pick = list(rows[:GATE_SHORTLIST])
        for tf in TIMEFRAMES[mode]:
            pick += [r for r in [r for r in rows if r.get('timeframe') == tf][:TF_FLOOR]
                     if not any(r is p for p in pick)]
        cur = board.get('current')
        # A timeframe's picks are the head of its own rows; verify_on_option walks those rows past
        # any that trade exactly like one already taken (gates that never bind) until it has as many.
        work = [(tf, [r for r in rows if r.get('timeframe') == tf], sum(1 for r in pick if r.get('timeframe') == tf),
                 cur if cur and cur.get('timeframe') == tf else None) for tf in TIMEFRAMES[mode]]
        work = [w for w in work if w[2] or w[3] is not None]
        if not work:
            continue
        total = sum(n + (c is not None) for _, _, n, c in work)
        priced: List[Dict[str, Any]] = []
        current, verified, done = None, 0, 0
        try:
            for tf, mine, n, c in work:
                res = {'results': mine, 'current': c, 'swept': board['swept'],
                       'recent_from': board['recent_from'], 'min_board_trades': board['min_board_trades']}
                tick = (lambda k, _t, base=done, tf=tf: progress(
                    base + k, total, stage='pricing', board=mode, timeframe=tf)) if progress else None
                verify_on_option(strategy, res, bars_for(tf), dict(data, interval=tf, positional=mode == 'positional'),
                                 symbol, tf, calendar, False, start_date, progress=tick, shortlist=n)
                done += n + (c is not None)
                priced += res['results']
                verified += res['verified']
                if c is not None:
                    current = res['current']
        except Exception as e:      # noqa: BLE001 — no ICICI login: keep the index board
            logging.getLogger(__name__).warning('[SIMPLE-TRADER OPT] %s %s option verification failed: %s',
                                                strategy, mode, e)
            board.update(option_error=str(e), results=rows[:25])
            continue
        ordered, passed = rank_quality(priced, current, min_board=board['min_board_trades'])
        board.update(results=ordered, best=ordered[0] if ordered else None, current=current,
                     passed=passed, basis='option', verified=verified)
