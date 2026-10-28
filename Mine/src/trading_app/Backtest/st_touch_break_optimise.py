"""Find Best Params for ST Touch Break.

The sweep runs the engine on INDEX points over every option the rule leaves open: the SuperTrend
(length, factor), which setups are taken (Bounce / Through / both), how the stop works, the
minimum wick of the touch candle, the touch tolerance, the break candle's minimum body, which
side an outside bar takes, the entry window (from / until), the square-off and the trades a day —
and the TIMEFRAME the whole rule runs on.

That is far too many axes for one product (~100k combinations per timeframe), so each timeframe is
searched in two stages: the SuperTrend / setup / stop / wick grid in full, then the best few of
those are walked axis by axis through the remaining options (coordinate descent, a pass at a time
until nothing improves). Every combination simulated lands on the board, not only the ones the
walk kept, and the board ranks them all together.

`min_body_pct` reads the break candle's CLOSE and a Through setup is only known at that close, so
a row that uses either has no live twin (see st_touch_break_live_engine): fine for the Backtest,
not for the live tab.

Like the Simple Trader boards, the best index rows (and the page's own settings) are then
re-priced on the option exactly as BACKTEST prices them (`price_with_reentries` around
`attach_option_legs`, the page's premium and SL / TSL) and re-ranked by `rank_quality` on
those numbers, so a row's figures are what BACKTEST shows for the same values.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import (
    MIN_BOARD_TRADES, MIN_TRADES, attach_option_legs, rank_quality, rupee_card, summarise)
from trading_app.Backtest.st_touch_break_engine import StTouchBreakEngine, _parse_hhmm

ST_PERIOD = (7, 10, 14)
ST_MULT = (1.5, 2.0, 2.5, 3.0)
MODES = ('both', 'bounce', 'through')
STOP_MODE = ('close', 'touch')
MIN_WICK = (0.0, 25.0, 40.0)
# The rest of the Logic popup, walked one axis at a time from the best core rows.
EXTRA_AXES: Dict[str, Tuple[Any, ...]] = {
    'touch_tol_pct': (0.0, 0.02, 0.05, 0.1),
    'min_body_pct': (0.0, 25.0, 50.0),
    'outside_bar': ('close', 'first'),
    'entry_start': ('09:15', '09:30', '10:00', '10:30'),
    'entry_end': ('11:30', '13:00', '14:30', '15:14'),
    'exit_cutoff': ('15:00', '15:14'),
    'max_trades_per_day': (1, 2, 3, 5),
}
TIMEFRAMES = ('3minute', '5minute', '15minute')   # 1 and 2 minute cost 2-3x the simulation for little extra
DIRECTIONS = ('both', 'long', 'short')   # one board each: Both / BUY (CE) / SELL (PE)
CORE = ['st_period', 'st_mult', 'modes', 'stop_mode', 'min_wick_pct']
SWEPT = ['timeframe'] + CORE + list(EXTRA_AXES)
MIN_WIN = 25.0                     # the Simple Trader boards want 50 %; this rule rides trends and wins ~35 % of the time
SHORTLIST = 10                     # index rows per timeframe re-priced on the option (plus the current settings)
SEEDS = 4                          # best core rows the extra options are walked from
PASSES = 2                         # most walks over the extra axes per seed
TF_MIN = {'minute': 1, '2minute': 2, '3minute': 3, '5minute': 5, '15minute': 15}


def grid_size() -> int:
    """The core grid; the extra-option walk adds `walk_size()` more at most."""
    return len(ST_PERIOD) * len(ST_MULT) * len(MODES) * len(STOP_MODE) * len(MIN_WICK)


def walk_size() -> int:
    return (SEEDS + 1) * PASSES * sum(len(v) for v in EXTRA_AXES.values())


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


def _hhmm(minutes: int) -> str:
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _apply(eng: StTouchBreakEngine, row: Dict[str, Any]) -> None:
    """Put a board row's numbers on the engine; a key the row lacks keeps the engine's own."""
    g = row.get
    eng.st_period, eng.st_mult = int(row['st_period']), float(row['st_mult'])
    eng.modes, eng.stop_mode = str(row['modes']), str(row['stop_mode'])
    eng.min_wick = min(1.0, max(0.0, float(row['min_wick_pct']) / 100.0))
    if g('touch_tol_pct') is not None:
        eng.touch_tol = max(0.0, float(row['touch_tol_pct'])) / 100.0
    if g('min_body_pct') is not None:
        eng.min_body = min(1.0, max(0.0, float(row['min_body_pct']) / 100.0))
    if g('outside_bar') is not None:
        eng.outside_bar = str(row['outside_bar'])
    if g('entry_start') is not None:
        eng.entry_start = _parse_hhmm(row['entry_start'], 9 * 60 + 15)
    if g('entry_end') is not None:
        eng.entry_end = _parse_hhmm(row['entry_end'], 15 * 60 + 14)
    if g('exit_cutoff') is not None:
        eng.exit_cutoff = _parse_hhmm(row['exit_cutoff'], 15 * 60 + 14)
    if g('max_trades_per_day') is not None:
        n = int(row['max_trades_per_day'])
        eng.max_trades_per_day = n if n > 0 else 999


def _params(eng: StTouchBreakEngine) -> Dict[str, Any]:
    return dict(st_period=eng.st_period, st_mult=eng.st_mult, modes=eng.modes,
                stop_mode=eng.stop_mode, min_wick_pct=round(eng.min_wick * 100, 2),
                touch_tol_pct=round(eng.touch_tol * 100, 4), min_body_pct=round(eng.min_body * 100, 2),
                outside_bar=eng.outside_bar, entry_start=_hhmm(eng.entry_start),
                entry_end=_hhmm(eng.entry_end), exit_cutoff=_hhmm(eng.exit_cutoff),
                max_trades_per_day=eng.max_trades_per_day if eng.max_trades_per_day < 999 else 0)


def _trades(eng: StTouchBreakEngine, from_date: str) -> List[Dict[str, Any]]:
    return [t for t in eng.simulate() if t['entry_time'][:10] >= from_date]


def _key(p: Dict[str, Any]) -> tuple:
    return tuple(str(p[k]) for k in CORE + list(EXTRA_AXES))


def _score(sm: Dict[str, Any], recent: float) -> tuple:
    """What the extra-option walk climbs: a row the board would keep beats one it would not,
    then profit factor — the board's own first two sort keys."""
    ok = sm['win_rate'] >= MIN_WIN and recent > 0 and sm['total_trades'] >= MIN_BOARD_TRADES
    return (ok, sm['profit_factor'])


def optimise(df: pd.DataFrame, data: Dict[str, Any], from_date: str = '', progress: Optional[Callable] = None,
             top: int = 80, bar_minutes: int = 3) -> Dict[str, Any]:
    eng = build(df, data, bar_minutes=bar_minutes)     # no minutes: bar stamps are enough for index points
    days = sorted({ts.strftime('%Y-%m-%d') for ts in eng.df.index if ts.strftime('%Y-%m-%d') >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    recent = lambda ts: round(sum(t['pnl'] for t in ts if t['entry_time'][:10] >= cut), 2)   # noqa: E731

    cur_trades = _trades(eng, from_date)      # the page's own settings, as they stand
    current = summarise(cur_trades)
    page = _params(eng)
    current.update(page, recent_pnl=recent(cur_trades))

    rows: List[Dict[str, Any]] = []
    seen: Dict[tuple, tuple] = {}             # params -> score, for every combination simulated
    total, done = grid_size() + walk_size(), 0

    def run(params: Dict[str, Any]) -> tuple:
        """Simulate one full combination once; record it if it traded enough."""
        nonlocal done
        k = _key(params)
        if k in seen:
            return seen[k]
        _apply(eng, params)
        trades = _trades(eng, from_date)
        done += 1
        sm = summarise(trades)
        rec = recent(trades)
        score = _score(sm, rec) if len(trades) >= MIN_TRADES else (False, 0.0)
        seen[k] = score
        if len(trades) >= MIN_TRADES:
            sm.update(_params(eng), recent_pnl=rec)
            rows.append(sm)
        if progress and done % 10 == 0:
            progress(min(done, total), total)
        return score

    extras0 = {k: page[k] for k in EXTRA_AXES}
    for st_period, st_mult in itertools.product(ST_PERIOD, ST_MULT):
        for modes, stop_mode, wick in itertools.product(MODES, STOP_MODE, MIN_WICK):
            run(dict(extras0, st_period=st_period, st_mult=st_mult, modes=modes, stop_mode=stop_mode,
                     min_wick_pct=wick))
    # Stage 2: from the best core rows (and the page's own point), one extra option at a time.
    core_rows, _ = rank_quality(list(rows), None, min_win=MIN_WIN)
    seeds = [{k: r[k] for k in CORE + list(EXTRA_AXES)} for r in core_rows[:SEEDS]]
    seeds.append({k: page[k] for k in CORE + list(EXTRA_AXES)})
    for seed in seeds:
        here, best = dict(seed), run(seed)
        for _ in range(PASSES):
            moved = False
            for axis, values in EXTRA_AXES.items():
                for v in values:
                    if str(v) == str(here[axis]):
                        continue
                    cand = dict(here, **{axis: v})
                    sc = run(cand)
                    if sc > best:
                        here, best, moved = cand, sc, True
            if not moved:
                break
    _apply(eng, page)
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN)
    return {'total_combos_tested': len(seen), 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN, 'results': ordered[:top], 'best': ordered[0] if ordered else None,
            'recent_from': cut, 'min_trades': MIN_TRADES, 'mode': 'strategy',
            'strategy': 'st_touch_break', 'swept': list(SWEPT), 'current': current}


def optimise_timeframes(bars_for: Callable[[str], Optional[pd.DataFrame]], data: Dict[str, Any],
                        from_date: str = '', top: int = 80, progress: Optional[Callable] = None) -> Dict[str, Any]:
    """The search above over every timeframe at once, ranked together, each row carrying its
    `timeframe`. The page's own settings are `current`, on the page's own timeframe (scored even
    when it is not one of `TIMEFRAMES`)."""
    page_tf = str(data.get('interval') or '3minute')
    page_tf = {'1minute': 'minute'}.get(page_tf, page_tf)
    tfs = [tf for tf in TIMEFRAMES if bars_for(tf) is not None]
    each = grid_size() + walk_size()
    rows: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = {}
    tested = qualified = 0
    for n, tf in enumerate(tfs):
        tick = (lambda d, t, n=n: progress(n * each + d, len(tfs) * each, timeframe=tf)) if progress else None
        res = optimise(bars_for(tf), dict(data, interval=tf), from_date, tick, top=10 ** 6,
                       bar_minutes=TF_MIN.get(tf, 3))
        for r in res['results']:
            r['timeframe'] = tf
        rows += res['results']
        tested += res['total_combos_tested']
        qualified += res['qualified']
        meta = res
        if tf == page_tf:
            current = dict(res['current'], timeframe=tf)
    if current is None and page_tf not in tfs and bars_for(page_tf) is not None:
        only = optimise_current(bars_for(page_tf), dict(data, interval=page_tf), from_date, TF_MIN.get(page_tf, 3))
        current = dict(only, timeframe=page_tf)
    ordered, passed = rank_quality(rows, current, min_win=MIN_WIN)
    return {'total_combos_tested': tested, 'qualified': qualified, 'passed': passed, 'min_win': MIN_WIN,
            'results': ordered[:top], 'best': ordered[0] if ordered else None,
            'recent_from': meta.get('recent_from', ''), 'min_trades': MIN_TRADES, 'mode': 'strategy',
            'strategy': 'st_touch_break', 'swept': list(SWEPT), 'current': current, 'timeframes': tfs}


def optimise_current(df: pd.DataFrame, data: Dict[str, Any], from_date: str, bar_minutes: int) -> Dict[str, Any]:
    """Just the page's own settings, scored like a board row (its timeframe is not swept)."""
    eng = build(df, data, bar_minutes=bar_minutes)
    days = sorted({ts.strftime('%Y-%m-%d') for ts in eng.df.index if ts.strftime('%Y-%m-%d') >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    trades = _trades(eng, from_date)
    out = summarise(trades)
    out.update(_params(eng), recent_pnl=round(sum(t['pnl'] for t in trades if t['entry_time'][:10] >= cut), 2))
    return out


def verify_on_option(res: Dict[str, Any], df: pd.DataFrame, minutes: pd.DataFrame, data: Dict[str, Any],
                     symbol: str, interval: str, calendar, start_date: str,
                     progress: Optional[Callable] = None, shortlist: int = SHORTLIST) -> None:
    """Re-price the current settings and the best index rows on the option as the run does, and
    replace res['results'] / res['current'] with those figures, ranked by `rank_quality`."""
    from trading_app.Backtest.st_ema_rsi_cci_engine import price_with_reentries
    mpb = TF_MIN.get(str(interval)) or int(''.join(ch for ch in str(interval) if ch.isdigit()) or 3)
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
        out = {k: row[k] for k in SWEPT if k in row}
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


def verify_timeframes(board: Dict[str, Any], bars_for: Callable[[str], Optional[pd.DataFrame]],
                      minutes: pd.DataFrame, data: Dict[str, Any], symbol: str, calendar, start_date: str,
                      progress: Optional[Callable] = None) -> None:
    """`verify_on_option` for `optimise_timeframes`' board: each timeframe's best SHORTLIST index
    rows (and the page's settings, on the page's timeframe) are priced on that timeframe's own
    candles, then every priced row is ranked together. A board whose pricing fails (no ICICI
    login) keeps its index rows and says why in `option_error`."""
    rows, cur = board['results'], board.get('current')
    cur_tf = cur.get('timeframe') if cur else None
    work = []
    for tf in dict.fromkeys(list(board.get('timeframes') or []) + ([cur_tf] if cur_tf else [])):
        mine = [r for r in rows if r.get('timeframe') == tf]
        c = cur if cur_tf == tf else None
        n = min(SHORTLIST, len(mine))
        if n or c is not None:
            work.append((tf, mine, n, c))
    if not work:
        return
    total = sum(n + (c is not None) for _, _, n, c in work)
    priced: List[Dict[str, Any]] = []
    current, verified, done = None, 0, 0
    try:
        for tf, mine, n, c in work:
            res = {'results': mine, 'current': c, 'recent_from': board['recent_from']}
            tick = (lambda k, _t, base=done, tf=tf: progress(base + k, total, timeframe=tf)) if progress else None
            verify_on_option(res, bars_for(tf), minutes, dict(data, interval=tf), symbol, tf, calendar,
                             start_date, progress=tick, shortlist=n)
            done += n + (c is not None)
            priced += res['results']
            verified += res['verified']
            if c is not None:
                current = res['current']
    except Exception as e:      # noqa: BLE001 — no ICICI login: keep the index board
        board.update(option_error=str(e), results=rows[:25])
        return
    ordered, passed = rank_quality(priced, current, min_win=MIN_WIN)
    board.update(results=ordered, best=ordered[0] if ordered else None, current=current,
                 passed=passed, basis='option', verified=verified)
