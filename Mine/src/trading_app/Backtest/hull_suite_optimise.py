"""Find Best Params for the Hull Suite System (Backtest/hull_suite_engine.py).

Built like MA Cross + HTF's sweep (ma_htf_optimise.py), and its board is the same Simple
Trader one (`mode='strategy'`), so the page renders and "uses" a row the same way. Two
stages, both on INDEX points:

  1. the grid — every combination of the numbers the rule turns on: the three Hull lengths,
     the pullback lookback, how many candles the break may take, and the target;
  2. the walk — from the best WALK_SEEDS rows of the grid, every other box on the page
     (slope bars, slow-line agreement, pullback nearness, stop placement and buffer, max
     risk, breakeven, direction, session, trades a day) is tried one at a time and a value is
     kept when it ranks better, for up to WALK_PASSES passes.

`optimise_timeframes` runs both stages once per chart in TIMEFRAMES (3, 5 and 15 minutes; a
15-minute chart is a different set of candles and the Hull lengths mean something else on it,
so each timeframe gets its own grid and walk) and ranks every row together, each carrying its
`timeframe`. Premium and the capital boxes only change how a trade is priced, so they are held
as set. `verify_on_option`
then re-prices the current settings and the best BOARD_ROWS rows on the option exactly as
the run does, so the board shows what BACKTEST shows.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from trading_app.Backtest.hull_suite_engine import STOP_MODES, HullSuiteEngine
from trading_app.Backtest.st_bb_rsi_engine import (
    MIN_BOARD_TRADES, MIN_TRADES, _parse_hhmm, attach_option_legs, rank_quality, return_dd, rupee_card,
    summarise)

GRID = [
    ('slow_len', (100, 200, 300)),
    ('medium_len', (50, 100)),
    ('fast_len', (20, 50)),
    ('pullback_bars', (0, 12, 24)),
    ('max_wait', (2, 3, 5)),
    ('rr', (1.0, 1.5, 2.0, 0.0)),
]
NAMES = [n for n, _ in GRID]
WALK = [
    ('slope_bars', (1, 2, 3)),
    ('slow_slope', (True, False)),
    ('pullback_pct', (0.05, 0.1, 0.2)),
    ('stop_mode', STOP_MODES),
    ('stop_buffer', (0.0, 5.0, 10.0)),
    ('max_risk', (0.0, 40.0, 60.0)),
    ('breakeven_rr', (0.0, 1.0)),
    ('direction', ('both', 'long', 'short')),
    ('entry_start', ('09:20', '09:30', '09:45')),
    ('entry_end', ('13:30', '14:30')),
    ('exit_cutoff', ('15:00', '15:15')),
    ('max_trades_per_day', (1, 2, 3)),
]
_CLOCK = {'entry_start', 'entry_end', 'exit_cutoff'}
_INT = {'slow_len', 'medium_len', 'fast_len', 'pullback_bars', 'max_wait', 'slope_bars', 'max_trades_per_day'}
_MIN = {'slow_len': 2, 'medium_len': 2, 'fast_len': 2, 'max_wait': 1, 'slope_bars': 1, 'max_trades_per_day': 1}
_STR = {'stop_mode', 'direction'}
_BOOL = {'slow_slope'}
# What `signals()` reads: a run that only changes the exit reuses the entries of the last one.
_SIGNAL_KEYS = ('slow_len', 'medium_len', 'fast_len', 'slope_bars', 'slow_slope', 'pullback_bars',
                'pullback_pct', 'direction', 'entry_start', 'entry_end')
WALK_SEEDS = 8          # best grid rows the walk starts from
WALK_PASSES = 2         # most passes over the walk's fields per seed
BOARD_ROWS = 15         # rows on the board, each re-priced on the option beside the current settings
MIN_WIN = 30.0          # a 1:1.5-2 breakout wins ~35-45 % of the time
RANK_BY = 'return_dd'   # profit for the drawdown taken; profit factor favours a rule that hardly trades
TIMEFRAMES = ('3minute', '5minute', '15minute')   # 1 / 2 minute cost 3-5x the simulation; the video's is 5


def tf_minutes(tf: str) -> int:
    """Minutes per candle of an interval value ('minute' is 1, as on the Timeframe box)."""
    digits = ''.join(ch for ch in str(tf) if ch.isdigit())
    return int(digits) if digits else (1 if str(tf) in ('minute', '1minute') else 5)


def walk_size() -> int:
    return WALK_SEEDS * WALK_PASSES * sum(len(v) - 1 for _, v in WALK)


def grid_size() -> int:
    n = 1
    for _, vals in GRID:
        n *= len(vals)
    return n


def build(df: pd.DataFrame, d: Dict[str, Any], minutes_per_bar: int,
          minutes: Optional[pd.DataFrame] = None) -> HullSuiteEngine:
    """The engine the run route builds for the same payload."""
    return HullSuiteEngine(
        df=df,
        minutes=minutes if minutes_per_bar > 1 else None,
        slow_len=int(d.get('slow_len', 200) or 200),
        medium_len=int(d.get('medium_len', 100) or 100),
        fast_len=int(d.get('fast_len', 50) or 50),
        slope_bars=int(d.get('slope_bars', 2) or 2),
        slow_slope=bool(d.get('slow_slope', True)),
        pullback_bars=int(d.get('pullback_bars', 12) or 0),
        pullback_pct=float(d.get('pullback_pct', 0.1) or 0),
        max_wait=int(d.get('max_wait', 3) or 3),
        stop_mode=str(d.get('stop_mode', 'signal')),
        stop_buffer=float(d.get('stop_buffer', 0) or 0),
        max_risk=float(d.get('max_risk', 0) or 0),
        rr=float(d.get('rr', 1.5) or 0),
        breakeven_rr=float(d.get('breakeven_rr', 0) or 0),
        direction=str(d.get('direction', 'both')),
        entry_start=d.get('entry_start', '09:20'),
        entry_end=d.get('entry_end', '14:30'),
        exit_cutoff=d.get('exit_cutoff', '15:15'),
        max_trades_per_day=int(d.get('max_trades_per_day', 2) or 2),
        bar_minutes=minutes_per_bar,
    )


def _memo(eng: HullSuiteEngine) -> None:
    """The lines depend only on the three lengths, the entries only on `_SIGNAL_KEYS`: each set
    is computed once for the sweep."""
    lines: Dict[tuple, Dict[str, Any]] = {}
    sigs: Dict[tuple, Dict[str, Any]] = {}
    raw_ind, raw_sig = eng.indicators, eng.signals

    def indicators():
        key = (eng.slow_len, eng.medium_len, eng.fast_len)
        if key not in lines:
            lines[key] = raw_ind()
        return lines[key]

    def signals():
        key = tuple(getattr(eng, k) for k in _SIGNAL_KEYS)
        if key not in sigs:
            sigs[key] = raw_sig()
        return sigs[key]
    eng.indicators, eng.signals = indicators, signals


def apply_row(eng: HullSuiteEngine, row: Dict[str, Any]) -> None:
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
        elif k in _INT:
            setattr(eng, k, max(_MIN.get(k, 0), int(v)))
        else:
            setattr(eng, k, max(0.0, float(v)))
    if eng.stop_mode not in STOP_MODES:
        eng.stop_mode = 'signal'


def _hhmm(minutes: int) -> str:
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _row_params(eng: HullSuiteEngine) -> Dict[str, Any]:
    out = {k: getattr(eng, k) for k in NAMES}
    for k, _ in WALK:
        out[k] = _hhmm(getattr(eng, k)) if k in _CLOCK else getattr(eng, k)
    return out


def _trades(eng: HullSuiteEngine, from_date: str) -> List[Dict[str, Any]]:
    trades = eng.simulate()
    return [t for t in trades if t['entry_time'][:10] >= from_date] if from_date else trades


def _signature(trades) -> int:
    return hash(tuple((t['entry_time'], t['exit_time'], t['type'], t['pnl']) for t in trades))


def optimise(df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int, from_date: str = '',
             minutes: Optional[pd.DataFrame] = None, top: int = BOARD_ROWS,
             progress: Optional[Callable] = None) -> Dict[str, Any]:
    eng = build(df, data, minutes_per_bar, minutes)
    _memo(eng)
    days = sorted({str(t)[:10] for t in eng.df.index if str(t)[:10] >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    held = _row_params(eng)                             # the page's own settings

    def passes(r):
        return (r['win_rate'] >= MIN_WIN and r['recent_pnl'] > 0 and r['total_pnl'] > 0
                and r['total_trades'] >= MIN_BOARD_TRADES)

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
    total = grid_size() + walk_size()
    done = 0
    for combo in itertools.product(*[v for _, v in GRID]):
        score({**held, **dict(zip(NAMES, combo))})
        done += 1
        if progress and done % 10 == 0:
            progress(done, total)

    # The walk: one field at a time from the best grid rows, keeping a value that ranks better.
    grid_rows = sorted((r for r in scored.values() if r['total_trades'] >= MIN_TRADES), key=rank_key)
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
            for k, vals in WALK:
                for v in vals:
                    if v == best[k]:
                        continue
                    cand = score({**{n: best[n] for n in held}, k: v})
                    done += 1
                    if progress and done % 10 == 0:
                        progress(min(done, total), total)
                    if cand['total_trades'] >= MIN_TRADES and rank_key(cand) < rank_key(best):
                        best, moved = cand, True
            if not moved:
                break
    if progress:
        progress(total, total)

    # One row per distinct set of trades: a box the rule ignores (pullback nearness with the
    # lookback off, say) would otherwise fill the board with copies of the same run.
    rows, seen = [], set()
    for r in sorted((r for r in scored.values() if r['total_trades'] >= MIN_TRADES), key=rank_key):
        if r['_sig'] in seen:
            continue
        seen.add(r['_sig'])
        rows.append({k: v for k, v in r.items() if k != '_sig'})
    current.pop('_sig', None)
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN, min_board=MIN_BOARD_TRADES, by=RANK_BY)
    swept = NAMES + [k for k, _ in WALK]
    return {'total_combos_tested': len(scored), 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN, 'results': ordered[:top], 'best': ordered[0] if ordered else None,
            'recent_from': cut, 'min_trades': MIN_TRADES, 'min_board_trades': MIN_BOARD_TRADES,
            'positional': False, 'rank_by': RANK_BY,
            'mode': 'strategy', 'strategy': 'hull_suite', 'swept': swept, 'current': current}


def score_current(df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int, from_date: str = '',
                  minutes: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Just the page's own settings, scored like a board row (for a timeframe not swept)."""
    eng = build(df, data, minutes_per_bar, minutes)
    days = sorted({str(t)[:10] for t in eng.df.index if str(t)[:10] >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    trades = _trades(eng, from_date)
    out = summarise(trades)
    out.update(_row_params(eng))
    out['recent_pnl'] = round(sum(t['pnl'] for t in trades if t['entry_time'][:10] >= cut), 2)
    out['ret_dd'] = return_dd(out)
    return out


TF_ROWS = 10            # rows per timeframe board, each re-priced on the option


def optimise_timeframes(bars_for: Callable[[str], Optional[pd.DataFrame]], data: Dict[str, Any],
                        from_date: str = '', minutes: Optional[pd.DataFrame] = None, top: int = TF_ROWS,
                        progress: Optional[Callable] = None,
                        timeframes=TIMEFRAMES) -> Dict[str, Any]:
    """`optimise` on every chart in `timeframes`, each with its own grid and walk and its own board
    (`tf_boards`, Candle Breakout's layout: best `top` of each timeframe). Every row carries its
    `timeframe`; each board's `current` is the page's settings on that chart. `results` is every
    board's rows together and `current` the page's settings on the page's own timeframe (scored
    even when it is not one of `timeframes`)."""
    page_tf = str(data.get('interval') or '5minute')
    page_tf = {'1minute': 'minute'}.get(page_tf, page_tf)
    tfs = [tf for tf in timeframes if bars_for(tf) is not None]
    each = grid_size() + walk_size()
    boards: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for n, tf in enumerate(tfs):
        tick = ((lambda d, t, n=n, tf=tf: progress(n * each + min(d, each), len(tfs) * each, timeframe=tf))
                if progress else None)
        res = optimise(bars_for(tf), dict(data, interval=tf), tf_minutes(tf), from_date, minutes,
                       top=top, progress=tick)
        for r in res['results'] + ([res['current']] if res.get('current') else []):
            r['timeframe'] = tf
        res['timeframe'] = tf
        res['swept'] = ['timeframe'] + res['swept']
        boards.append(res)
        if tf == page_tf:
            current = res['current']
    if current is None and bars_for(page_tf) is not None:
        current = dict(score_current(bars_for(page_tf), data, tf_minutes(page_tf), from_date, minutes),
                       timeframe=page_tf)
    return _combined(boards, current, page_tf)


def _combined(boards: List[Dict[str, Any]], current: Optional[Dict[str, Any]], page_tf: str) -> Dict[str, Any]:
    rows = [r for b in boards for r in b['results']]
    return {'total_combos_tested': sum(b['total_combos_tested'] for b in boards),
            'qualified': sum(b['qualified'] for b in boards), 'passed': sum(b['passed'] for b in boards),
            'min_win': MIN_WIN, 'results': rows, 'best': None,
            'recent_from': boards[-1]['recent_from'] if boards else '', 'min_trades': MIN_TRADES,
            'min_board_trades': MIN_BOARD_TRADES, 'positional': False, 'rank_by': RANK_BY,
            'mode': 'strategy', 'strategy': 'hull_suite',
            'swept': ['timeframe'] + NAMES + [k for k, _ in WALK], 'current': current,
            'page_timeframe': page_tf, 'timeframes': [b['timeframe'] for b in boards], 'tf_boards': boards}


def verify_timeframes(res: Dict[str, Any], bars_for: Callable[[str], Optional[pd.DataFrame]],
                      data: Dict[str, Any], symbol: str, calendar, start_date: str,
                      minutes: Optional[pd.DataFrame] = None, progress: Optional[Callable] = None) -> None:
    """`verify_on_option` on each timeframe's board, on its own chart, then the combined rows rebuilt.
    The page's settings on a timeframe outside the sweep are priced on their own."""
    boards = res['tf_boards']
    cur = res.get('current')
    lone = cur is not None and not any(b.get('current') is cur for b in boards)
    steps = len(boards) + (1 if lone else 0)
    for n, b in enumerate(boards):
        tick = ((lambda k, m, n=n, tf=b['timeframe']: progress(n * 100 + int(100 * k / max(1, m)),
                                                                 steps * 100, timeframe=tf))
                if progress else None)
        verify_on_option(b, bars_for(b['timeframe']), data, tf_minutes(b['timeframe']), symbol, calendar,
                         start_date, minutes=minutes, progress=tick, shortlist=len(b['results']),
                         bars_for=bars_for)
    if lone:
        tf = cur['timeframe']
        one = {'results': [], 'current': cur, 'swept': res['swept'], 'recent_from': res.get('recent_from', '')}
        verify_on_option(one, bars_for(tf), data, tf_minutes(tf), symbol, calendar, start_date,
                         minutes=minutes, bars_for=bars_for)
        cur = one['current']
    else:
        cur = next((b['current'] for b in boards if b['timeframe'] == res.get('page_timeframe')), None)
    page_tf = res.get('page_timeframe', '')
    res.update(_combined(boards, cur, page_tf), basis='option',
               verified=sum(b.get('verified', 0) for b in boards))


def verify_on_option(res: Dict[str, Any], df: pd.DataFrame, data: Dict[str, Any], minutes_per_bar: int,
                     symbol: str, calendar, start_date: str, minutes: Optional[pd.DataFrame] = None,
                     progress: Optional[Callable] = None, shortlist: int = BOARD_ROWS,
                     bars_for: Optional[Callable[[str], Optional[pd.DataFrame]]] = None) -> None:
    """Re-price the current settings and the best index rows on the option as the run does and
    replace res['results'] / res['current'] with those figures, re-ranked. Each row keeps its
    index figures as index_pnl / index_pf. With `bars_for`, a row carrying a `timeframe` is
    booked on that timeframe's candles (`df` / `minutes_per_bar` are then only the fallback)."""
    from trading_app.Backtest.st_touch_break_optimise import book_entry
    engines: Dict[Any, HullSuiteEngine] = {}

    def engine_for(row):
        tf = row.get('timeframe') if bars_for is not None else None
        if tf not in engines:
            e = (build(bars_for(tf), data, tf_minutes(tf), minutes) if tf
                 else build(df, data, minutes_per_bar, minutes))
            _memo(e)
            engines[tf] = e
        return engines[tf], (tf_minutes(tf) if tf else minutes_per_bar)

    premium = float(data.get('premium') or 0)       # 0 = a stock, bought ATM intraday (as the run)
    cut = res.get('recent_from') or ''
    names = res['swept']

    def trades_for(row):
        eng, _ = engine_for(row)
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
        priced, info = attach_option_legs(symbol, trades, premium or None, engine_for(row)[1], None,
                                          calendar=calendar, index_minutes=minutes)
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
