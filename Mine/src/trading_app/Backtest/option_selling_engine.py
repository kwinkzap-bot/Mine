"""NIFTY monthly ATM short straddle, held to a % of the premium collected.

The rule (the page's ⚖️ NIFTY ATM Straddle, Mine group; monthly since
2026-11-03 — it was a weekly 09:20 → 15:14 intraday straddle with a ₹ stop per
leg until then):

* **Entry** — once a month on `entry_day`: the last Friday (default) — or the
  last Monday … Thursday, or the first session after the month's own expiry
  ('after_expiry') — the session before it when that day is a holiday, at
  `entry_hm` (15:15): SELL the ATM CE and the ATM PE
  (50-step strike nearest the spot) of NEXT month's monthly expiry — the first
  monthly expiry at least `min_days_to_expiry` (14) days after the entry.
  The calendar is call_ratio_engine's (`entry_days`, `contract_expiry`).
* **Target / Stop** — on the whole straddle, both legs out together: the
  position's mark-to-market reaches +`target_pct` (5 %) / −`stop_pct` (5 %) of
  the premium collected (CE + PE sold).
* **No time exit** — a position trade, held day after day until the target or
  the stop. Only the contract running out ends it otherwise: still open on the
  expiry day, it is closed at that day's last 5-minute close (≈ settlement),
  reason 'Expiry'. (Until the evening of 2026-11-03 there was an exit time and
  an N-sessions-before-expiry box.)

Bars are ICICI Breeze 5-MINUTE candles, one fetch per contract over its life
(`call_ratio_engine.contract_window`), labelled by their start. The price "at"
a time is the close of the last bar that has FINISHED by then — the 15:10 bar's
close for 15:15 — so nothing is read before it happened. The ATM is NIFTY's
close of that same bar. The position is marked on every later bar's close
(each leg forward-filled across a quiet bar).

**Target and stop fill AT their level** (`exit_fill`), like a resting order on
the position's P&L: the legs are interpolated between the last mark and the
bar that crossed, so CE out + PE out books exactly ±N %. Until 2026-11-03 the
exit was the crossing bar's close, which booked −6 % to −9 % on a −5 % stop.
The one exception is a **gap**: a session whose first bar OPENS already past
the level (Friday's close → Monday's open) is filled at that open — no stop
can do better — and reads 'Stop loss (gap)' / 'Target (gap)'.

`run_trade` (BACKTEST) and `score_expiry` (Find Best Params) book through the
same `build_path` + `first_exit`, so a sweep row and the backtest of its
parameters agree.

The module is pure: callers hand in the bars, so it never touches a broker.
"""
from __future__ import annotations

import itertools
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from trading_app.Backtest import call_ratio_engine as cre

STRIKE_STEP = 50
BAR = pd.Timedelta(minutes=5)
MAX_LAG = pd.Timedelta(minutes=5)      # a "price at" bar may end at most this long before the time
RIGHTS = ('CE', 'PE')

ENTRY_DAYS = ('last_monday', 'last_tuesday', 'last_wednesday', 'last_thursday',
              'last_friday', 'after_expiry')

DEFAULTS = {
    'entry_day': 'last_friday',
    'entry_hm': (15, 15),
    'min_days_to_expiry': 14,
    'target_pct': 5.0,
    'stop_pct': 5.0,
    'adjust': 'none',          # ADJUST_TYPES
    'adjust_pts': 200,         # NIFTY points from the centre that fire it
    'max_adjust': 2,
}

_frame = cre._frame
_at = cre._at
entry_days = cre.entry_days


def atm_strike(spot: float, step: int = STRIKE_STEP) -> int:
    return int(round(spot / step) * step)


def price_at(frame, ts: pd.Timestamp) -> Optional[float]:
    """Close of the last 5-minute bar finished by `ts` (a frame or a close
    Series), or None when that bar is more than MAX_LAG stale."""
    if frame is None or len(frame) == 0:
        return None
    col = frame['close'] if isinstance(frame, pd.DataFrame) else frame
    last_start = ts - BAR
    pos = int(col.index.searchsorted(last_start, side='right')) - 1
    if pos < 0 or last_start - col.index[pos] > MAX_LAG:
        return None
    return float(col.iloc[pos])


EXPIRY_CLOSE = (15, 30)        # the expiry day's last 5-minute bar (15:25) finishes here


def cut_time(expiry: date) -> pd.Timestamp:
    return _at(expiry, EXPIRY_CLOSE)


def open_month(entry: date, sessions: Sequence[date], spot5: pd.Series,
               frame_for: Callable[[date, int, str], Optional[pd.DataFrame]], p: Dict) -> Dict:
    """Expiry, ATM and both legs' entry premiums for one entry day — or a skip."""
    expiry = cre.contract_expiry(entry, sessions, p['min_days_to_expiry'], p.get('listed_expiries', ()))
    out: Dict = {'entry_date': entry, 'expiry': expiry}
    if expiry is None:
        return {**out, 'ok': False, 'reason': 'No monthly expiry resolved'}
    entry_ts = _at(entry, p['entry_hm'])
    spot = price_at(spot5, entry_ts)
    if spot is None:
        return {**out, 'ok': False, 'reason': 'No NIFTY bar at the entry time'}
    strike = atm_strike(spot)
    out.update({'entry_ts': entry_ts, 'spot': spot, 'strike': strike})
    frames = {r: frame_for(expiry, strike, r) for r in RIGHTS}
    entry_px = {r: price_at(frames[r], entry_ts) for r in RIGHTS}
    missing = [f'{strike} {r}' for r in RIGHTS if entry_px[r] is None]
    if missing:
        return {**out, 'ok': False, 'reason': f"No ICICI premium at entry for {', '.join(missing)}"}
    return {**out, 'ok': True, 'frames': frames, 'entry_px': entry_px,
            'premium': entry_px['CE'] + entry_px['PE']}


# ── Adjustments ──────────────────────────────────────────────────────────────
# When NIFTY's 5-minute close has moved `adjust_pts` from the current centre
# (the sold strike at first), the position is adjusted at that bar's close and
# the centre moves to the new ATM — at most `max_adjust` times, never on the
# expiry day itself:
#   roll_untested — buy back the leg making money (the PE on a rise) and sell
#                   the same side at the new ATM: more premium, delta back to ~0;
#   recenter      — buy back both, sell a fresh straddle at the new ATM;
#   hedge         — BUY the losing side at the new ATM (once per side): the
#                   short leg becomes a spread, its loss capped from there.
# Target / stop stay % of the FIRST premium collected, on the whole position's
# P&L (closed legs' booked points + open legs' marks).
ADJUST_TYPES = ('none', 'roll_untested', 'recenter', 'hedge')


def _leg(right: str, strike: int, side: int, entry: float, opened: int) -> Dict:
    """side −1 = sold, +1 = bought; `opened` = the bar whose close it was
    traded at (−1 = the entry itself); `closed` None while held."""
    return {'right': right, 'strike': strike, 'side': side, 'entry': float(entry),
            'opened': opened, 'closed': None}


def _leg_arrays(frame, idx: pd.DatetimeIndex, start_ts: pd.Timestamp, leg: Dict) -> tuple:
    """(marks, opens) of one leg on the grid: its entry price up to the bar it
    was traded at, then its closes (forward-filled) / opens (a bar without a
    print: the last mark) while held, then frozen at its closing mark."""
    n = len(idx)
    entry = leg['entry']
    a = leg['opened']
    b = n - 1 if leg['closed'] is None else leg['closed']
    close = np.full(n, np.nan)
    op = np.full(n, np.nan)
    if frame is not None and not frame.empty:
        f = frame[frame.index >= start_ts]
        col = f['close']
        close = col.reindex(col.index.union(idx)).ffill().reindex(idx).to_numpy(dtype=float)
        op = f['open'].reindex(idx).to_numpy(dtype=float)
    marks = np.full(n, entry)
    if b > a:
        seg = pd.Series(close[a + 1:b + 1]).ffill().fillna(entry).to_numpy()
        marks[a + 1:b + 1] = seg
        marks[b + 1:] = marks[b]
    prev = np.r_[entry, marks[:-1]]
    opens = marks.copy()
    if b > a:
        o = op[a + 1:b + 1]
        opens[a + 1:b + 1] = np.where(np.isnan(o), prev[a + 1:b + 1], o)
    return marks, opens


def _assemble(idx, grid_spot, legs: List[Dict], frames: Dict[tuple, Optional[pd.DataFrame]],
              start_ts: pd.Timestamp, adjusts: List[Dict]) -> Dict:
    marks, opens = [], []
    for leg in legs:
        mk, op = _leg_arrays(frames.get((leg['right'], leg['strike'])), idx, start_ts, leg)
        marks.append(mk)
        opens.append(op)
    n = len(idx)
    mtm = np.zeros(n)
    open_mtm = np.zeros(n)
    for leg, mk, op in zip(legs, marks, opens):
        mtm += leg['side'] * (mk - leg['entry'])
        open_mtm += leg['side'] * (op - leg['entry'])
    days = np.array([ts.date() for ts in idx])
    return {'idx': idx, 'legs': legs, 'marks': marks, 'opens': opens, 'adjusts': adjusts,
            'mtm': mtm, 'open_mtm': open_mtm,
            'new_session': np.r_[False, days[1:] != days[:-1]] if len(days) else np.zeros(0, bool),
            'spot': grid_spot, 'days': days}


def build_path(entry_ts: pd.Timestamp, end_ts: pd.Timestamp, entry_px: Dict[str, float],
               frames: Dict[str, Optional[pd.DataFrame]], spot5: pd.Series,
               strike: int = 0, expiry: Optional[date] = None,
               frame_for: Optional[Callable] = None, adjust: str = 'none',
               adjust_pts: float = 0, max_adjust: int = 0) -> Dict:
    """Every 5-minute bar starting at/after the entry and finished by `end_ts`:
    each leg's marks (closes, forward-filled; its entry price before its first
    print) and opens, the position's per-unit MTM in points on both (closed
    legs at their closing mark), and which bars open a session. With an
    `adjust` rule the legs change at the bars it fires on (`adjusts`)."""
    grid = spot5[(spot5.index >= entry_ts) & (spot5.index <= end_ts - BAR)]
    idx = grid.index
    spot = grid.to_numpy()
    legs = [_leg(r, strike, -1, entry_px[r], -1) for r in RIGHTS]
    fr: Dict[tuple, Optional[pd.DataFrame]] = {(r, strike): frames.get(r) for r in RIGHTS}
    adjusts: List[Dict] = []
    if adjust != 'none' and adjust_pts > 0 and max_adjust > 0 and frame_for is not None and len(idx):
        _adjust(idx, spot, legs, fr, expiry, strike, frame_for, adjust, adjust_pts, max_adjust, adjusts)
    return _assemble(idx, spot, legs, fr, entry_ts, adjusts)


def _adjust(idx, spot, legs, fr, expiry, centre, frame_for, kind, pts, max_adjust, adjusts) -> None:
    """Walk the triggers in order, editing `legs` / `fr` / `adjusts` in place."""
    days = np.array([ts.date() for ts in idx])
    hedged: set = set()
    k = 0
    n = len(idx)
    while len(adjusts) < max_adjust and k < n:
        moved = np.abs(spot[k:] - centre) >= pts
        moved &= days[k:] != expiry
        if not moved.any():
            return
        t = k + int(moved.argmax())
        k = t + 1
        up = spot[t] > centre
        atm = atm_strike(spot[t])
        held = [lg for lg in legs if lg['closed'] is None and lg['side'] < 0]
        if kind == 'roll_untested':
            side_r = 'PE' if up else 'CE'
            close = [lg for lg in held if lg['right'] == side_r and lg['strike'] != atm]
            opens = [(side_r, -1)] if close else []
        elif kind == 'recenter':
            close = [lg for lg in held if lg['strike'] != atm]
            opens = [(lg['right'], -1) for lg in close]
        else:                                     # hedge: buy the tested side, once a side
            side_r = 'CE' if up else 'PE'
            close, opens = [], ([] if side_r in hedged else [(side_r, +1)])
        if not opens:
            centre = atm                          # nothing to do at this ATM; watch from it
            continue
        when = idx[t] + BAR
        prices = {}
        for r, _ in opens:
            if (r, atm) not in fr:
                fr[(r, atm)] = frame_for(expiry, atm, r)
            prices[r] = price_at(fr[(r, atm)], when)
        if any(v is None for v in prices.values()):
            continue                              # no premium on the new strike — try the next bar
        for lg in close:
            lg['closed'] = t
        for r, side in opens:
            legs.append(_leg(r, atm, side, prices[r], t))
            if side > 0:
                hedged.add(r)
        adjusts.append({'pos': t, 'ts': when, 'spot': float(spot[t]), 'kind': kind, 'strike': atm,
                        'closed': [(lg['right'], lg['strike']) for lg in close],
                        'opened': [(r, atm, side, prices[r]) for r, side in opens]})
        centre = atm


def _held_at(path: Dict, pos: int) -> List[int]:
    """Indices of the legs still held when bar `pos` trips the exit (a leg
    traded at that same bar's close comes after the exit)."""
    return [i for i, lg in enumerate(path['legs'])
            if lg['opened'] < pos and (lg['closed'] is None or lg['closed'] >= pos)]


def exit_fill(path: Dict, pos: int, level: float) -> tuple:
    """(per-unit P&L, [each leg's price], gap?) of a target (`level` > 0) or
    stop (< 0) that bar `pos` crossed. At the level, the legs interpolated
    between the last mark (the session's open on a session's first bar, else
    the previous close) and this close; a session that OPENS past the level
    fills at that open."""
    beyond = (lambda v: v >= level) if level > 0 else (lambda v: v <= level)
    n_legs = len(path['legs'])
    if path['new_session'][pos] and beyond(path['open_mtm'][pos]):
        return (float(path['open_mtm'][pos]),
                [float(path['opens'][i][pos]) for i in range(n_legs)], True)
    if path['new_session'][pos]:
        m0, a0 = path['open_mtm'][pos], [path['opens'][i][pos] for i in range(n_legs)]
    elif pos > 0:
        m0, a0 = path['mtm'][pos - 1], [path['marks'][i][pos - 1] for i in range(n_legs)]
    else:
        m0, a0 = 0.0, [lg['entry'] for lg in path['legs']]
    m1 = path['mtm'][pos]
    frac = 1.0 if m1 == m0 else min(1.0, max(0.0, (level - m0) / (m1 - m0)))
    px = [float(a0[i] + frac * (path['marks'][i][pos] - a0[i])) for i in range(n_legs)]
    return float(level), px, False


def exit_points(path: Dict, pos: int, reason: str, premium: float,
                target_pct: float, stop_pct: float) -> tuple:
    """(per-unit P&L, [each leg's price at the exit], reason) of a first_exit
    result — the one booking rule the backtest and the sweep share."""
    if pos < 0:
        return 0.0, [lg['entry'] for lg in path['legs']], reason
    if reason in ('Target', 'Stop loss'):
        level = premium * (target_pct if reason == 'Target' else -stop_pct) / 100.0
        pts, px, gap = exit_fill(path, pos, level)
        return pts, px, reason + (' (gap)' if gap else '')
    return float(path['mtm'][pos]), [float(m[pos]) for m in path['marks']], reason


def _first(mask: np.ndarray) -> int:
    return int(mask.argmax()) if mask.any() else -1


def first_exit(path: Dict, premium: float, target_pct: float, stop_pct: float,
               cut_ts: pd.Timestamp, hits: Optional[Dict] = None) -> tuple:
    """(bar position, reason). 'Open (data ends)' when the time exit was not
    reached and nothing tripped; position −1 = no bar after the entry at all.
    `hits` memoises first-hit positions for a sweep asking one path often."""
    idx = path['idx']
    n_cut = int(idx.searchsorted(cut_ts - BAR, side='right'))     # bars allowed: [0, n_cut)
    memo = {} if hits is None else hits
    if ('t', target_pct) not in memo:
        memo[('t', target_pct)] = _first(path['mtm'] >= premium * target_pct / 100.0)
    if ('s', stop_pct) not in memo:
        memo[('s', stop_pct)] = _first(path['mtm'] <= -premium * stop_pct / 100.0)
    cands = [(memo[('t', target_pct)], 0, 'Target'), (memo[('s', stop_pct)], 1, 'Stop loss')]
    hit = min(((pos, rank, r) for pos, rank, r in cands if 0 <= pos < n_cut), default=None)
    if hit:
        return hit[0], hit[2]
    if n_cut == 0:
        return -1, 'Open (data ends)'
    last = n_cut - 1
    if idx[last].date() == cut_ts.date():
        return last, 'Expiry'
    return last, 'Open (data ends)'


def month_path(m: Dict, spot5: pd.Series, frame_for: Callable, p: Dict) -> Dict:
    """The path of an opened month (open_month) under `p`'s adjustment rule."""
    return build_path(m['entry_ts'], cut_time(m['expiry']), m['entry_px'], m['frames'], spot5,
                      m['strike'], m['expiry'], frame_for, p.get('adjust', 'none'),
                      float(p.get('adjust_pts') or 0), int(p.get('max_adjust') or 0))


def run_trade(entry: date, sessions: Sequence[date], spot5: pd.Series,
              frame_for: Callable[[date, int, str], Optional[pd.DataFrame]],
              params: Optional[Dict] = None) -> Dict:
    """One month's straddle from entry to exit; 'status' in traded | open | skipped.

    `spot5` is NIFTY's 5-minute closes (indexed by bar start) over the run,
    `frame_for(expiry, strike, right)` that contract's 5-minute frame."""
    p = {**DEFAULTS, **(params or {})}
    m = open_month(entry, sessions, spot5, frame_for, p)
    base = {'entry_date': entry, 'expiry': m['expiry'], 'spot': m.get('spot'), 'strike': m.get('strike')}
    if not m['ok']:
        return {**base, 'status': 'skipped', 'reason': m['reason']}
    cut = cut_time(m['expiry'])
    path = month_path(m, spot5, frame_for, p)
    pos, reason = first_exit(path, m['premium'], p['target_pct'], p['stop_pct'], cut)
    pts, exit_px, reason = exit_points(path, pos, reason, m['premium'], p['target_pct'], p['stop_pct'])
    return {**base, **_booked(path, pos, reason, pts, exit_px, m)}


def legs_traded(path: Dict, pos: int) -> int:
    """Legs opened by the time bar `pos` exits — the brokerage count (each is
    one sold-and-bought-back, or bought-and-sold, leg)."""
    return sum(1 for lg in path['legs'] if lg['opened'] < pos)


def _booked(path: Dict, pos: int, reason: str, pts: float, exit_px: List[float], m: Dict) -> Dict:
    if pos < 0:
        exit_ts, upto = m['entry_ts'], np.zeros(0)
    else:
        # A level fill happened inside the bar; its close is when it is known.
        exit_ts = path['idx'][pos] + BAR
        upto = np.r_[path['mtm'][:pos], pts]
    held = _held_at(path, pos) if pos >= 0 else [0, 1]
    short = {path['legs'][i]['right']: i for i in held if path['legs'][i]['side'] < 0}
    done = [a for a in path['adjusts'] if a['pos'] < pos]
    legs = []
    for i, lg in enumerate(path['legs']):
        if pos >= 0 and lg['opened'] >= pos:
            continue
        closed = lg['closed'] is not None and lg['closed'] < pos
        out = float(path['marks'][i][lg['closed']]) if closed else float(exit_px[i])
        legs.append({'right': lg['right'], 'strike': lg['strike'],
                     'side': 'SELL' if lg['side'] < 0 else 'BUY', 'entry': lg['entry'], 'exit': out})
    return {
        'status': 'open' if reason.startswith('Open') else 'traded',
        'entry_ts': m['entry_ts'], 'exit_ts': exit_ts, 'reason': reason,
        'entry_px': m['entry_px'],
        'exit_px': {r: float(exit_px[i]) for r, i in short.items()},
        'exit_strikes': {r: path['legs'][i]['strike'] for r, i in short.items()},
        'premium': m['premium'],
        'pnl_points': pts,
        'peak_points': float(max(0.0, upto.max())) if len(upto) else 0.0,
        'trough_points': float(min(0.0, upto.min())) if len(upto) else 0.0,
        'sessions_held': len({m['entry_ts'].date(), *(path['days'][:pos + 1] if pos >= 0 else [])}),
        'adjustments': done, 'legs': legs,
        'legs_traded': legs_traded(path, pos) if pos >= 0 else len(RIGHTS),
    }


def _adjust_text(a: Dict) -> str:
    when = a['ts'].strftime('%d-%b %H:%M')
    parts = [f"out {r} {k}" for r, k in a['closed']]
    parts += [f"{'buy' if side > 0 else 'sell'} {r} {k} @{px:.1f}" for r, k, side, px in a['opened']]
    return f"{when} NIFTY {a['spot']:.0f}: " + ', '.join(parts)


def trade_row(res: Dict, qty: int, lots: int, lot_size: int, brokerage_per_leg: float) -> Dict:
    """Flatten a run_trade result into one grid row (₹ at `qty`). Every % is
    of the premium collected (CE + PE sold × qty)."""
    row: Dict = {
        'date': res['entry_date'].isoformat(),
        'expiry': res['expiry'].isoformat() if res.get('expiry') else None,
        'spot': round(res['spot'], 2) if res.get('spot') else None,
        'strike': res.get('strike'),
        'status': res['status'],
        'lots': lots, 'lot_size': lot_size, 'qty': qty,
    }
    if res['status'] == 'skipped':
        row.update({'exit_reason': res['reason'], 'result': 'SKIP',
                    'pnl': None, 'pnl_rupees': None, 'pnl_pct': None})
        return row
    for r in RIGHTS:
        k = r.lower()
        row[f'{k}_entry'] = round(res['entry_px'][r], 2)
        row[f'{k}_exit'] = round(res['exit_px'][r], 2) if r in res['exit_px'] else None
        row[f'{k}_exit_strike'] = res['exit_strikes'].get(r)
    prem = res['premium']
    collected = prem * qty
    gross = res['pnl_points'] * qty
    brokerage = brokerage_per_leg * res['legs_traded']
    net = gross - brokerage
    row.update({
        'premium': round(prem, 2),
        'premium_rupees': round(collected, 2),
        'entry_time': res['entry_ts'].isoformat(),
        'exit_time': res['exit_ts'].isoformat(),
        'sessions_held': res['sessions_held'],
        'exit_reason': res['reason'],
        'adjust_count': len(res['adjustments']),
        'adjustments': [_adjust_text(a) for a in res['adjustments']],
        'pnl': round(res['pnl_points'], 2),
        'gross_pnl_rupees': round(gross, 2),
        'brokerage': round(brokerage, 2),
        'pnl_rupees': round(net, 2),
        'pnl_pct': round(100.0 * net / collected, 2) if collected else 0.0,
        'peak_pct': round(100.0 * res['peak_points'] / prem, 2) if prem else 0.0,
        'trough_pct': round(100.0 * res['trough_points'] / prem, 2) if prem else 0.0,
        'result': 'OPEN' if res['status'] == 'open' else
                  ('WIN' if net > 0 else ('LOSS' if net < 0 else 'SCRATCH')),
    })
    return row


def summarise(rows: List[Dict]) -> Dict:
    """Stats on the CLOSED months (open and skipped ones are counted, not scored)."""
    return cre.summarise(rows)


# ── Find Best Params ─────────────────────────────────────────────────────────
# Entry day × adjustment (type, trigger points) × target × stop. The path
# depends on the entry and the adjustment only, so each (month, entry day,
# adjustment) is ONE path and every target/stop is a replay of it. The
# adjustments' strikes are new contracts (cached): a cold month is the two
# entry legs per entry day plus every ATM an adjustment reaches — roughly the
# 50-strikes NIFTY crossed that month, both sides.
EXIT_GRID = {
    'target_pct': (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50),
    'stop_pct': (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50),
}
EXIT_KEYS = tuple(EXIT_GRID)
ADJUST_PTS = (100, 150, 200, 250, 300, 400, 500)
ADJUST_VARIANTS = (('none', 0),) + tuple((k, pt) for k in ADJUST_TYPES[1:] for pt in ADJUST_PTS)
SWEEP_KEYS = ('entry_day', 'adjust', 'adjust_pts') + EXIT_KEYS


def entries_by_month(sessions: Sequence[date], start: date, end: date,
                     rules: Sequence[str] = ENTRY_DAYS) -> Dict[tuple, Dict[str, date]]:
    """{(year, month): {entry rule: entry date}} — what one sweep month scores."""
    out: Dict[tuple, Dict[str, date]] = {}
    for rule in rules:
        for d in entry_days(sessions, start, end, rule):
            out.setdefault((d.year, d.month), {})[rule] = d
    return out


def sweep_size() -> int:
    return len(ENTRY_DAYS) * len(ADJUST_VARIANTS) * cre.sweep_size(EXIT_GRID)


def score_expiry(entry: date, sessions: Sequence[date], spot5: pd.Series,
                 frame_for: Callable[[date, int, str], Optional[pd.DataFrame]],
                 qty: int, base: Dict, brokerage_per_leg: float,
                 grid: Dict = EXIT_GRID,
                 variants: Sequence[tuple] = ADJUST_VARIANTS) -> Dict[tuple, tuple]:
    """Every (adjust, adjust pts, target, stop) on ONE entry:
    {combo: (status, net ₹, % of premium, reason)}."""
    p0 = {**DEFAULTS, **base}
    exits = list(itertools.product(*(grid[k] for k in EXIT_KEYS)))
    m = open_month(entry, sessions, spot5, frame_for, p0)
    if not m['ok']:
        return {v + c: ('skipped', 0.0, 0.0, m['reason']) for v in variants for c in exits}
    cut = cut_time(m['expiry'])
    collected = m['premium'] * qty
    out: Dict[tuple, tuple] = {}
    for kind, pts_trigger in variants:
        path = month_path(m, spot5, frame_for, {**p0, 'adjust': kind, 'adjust_pts': pts_trigger})
        hits: Dict = {}
        for tp, sp in exits:
            pos, reason = first_exit(path, m['premium'], tp, sp, cut, hits)
            pts, _, reason = exit_points(path, pos, reason, m['premium'], tp, sp)
            n_legs = legs_traded(path, pos) if pos >= 0 else len(RIGHTS)
            net = pts * qty - brokerage_per_leg * n_legs
            status = 'open' if reason.startswith('Open') else 'traded'
            out[(kind, pts_trigger, tp, sp)] = (status, net, 100.0 * net / collected if collected else 0.0,
                                                 reason)
    return out


def score_month(entries: Dict[str, date], sessions: Sequence[date], spot5: pd.Series,
                frame_for: Callable[[date, int, str], Optional[pd.DataFrame]],
                qty: int, base: Dict, brokerage_per_leg: float,
                variants: Sequence[tuple] = ADJUST_VARIANTS) -> Dict[tuple, tuple]:
    """Every (entry day, adjust, adjust pts, target, stop) on ONE month;
    `entries` from entries_by_month."""
    out: Dict[tuple, tuple] = {}
    for rule, entry in entries.items():
        res = score_expiry(entry, sessions, spot5, frame_for, qty, {**base, 'entry_day': rule},
                           brokerage_per_leg, variants=variants)
        out.update({(rule,) + combo: r for combo, r in res.items()})
    return out


def rank(per_expiry: List[Dict[tuple, tuple]], min_trades: int = 1) -> List[Dict]:
    """One row per combo, best net ₹ first (call_ratio_engine.rank's scoring)."""
    return cre.rank(per_expiry, min_trades=min_trades, keys=SWEEP_KEYS)
