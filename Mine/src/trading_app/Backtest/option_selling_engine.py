"""NIFTY ATM short straddle — monthly or weekly — held to a % of the premium collected.

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
  **Weekly** (`cadence='weekly'`, added 2026-11-03): once a WEEK instead, on
  `entry_day` Monday … Friday (the session before it in the same week when it
  is a holiday) or the session after each weekly expiry ('after_expiry'),
  selling the ATM CE + PE of the first weekly expiry AFTER the entry session
  (`weekly_expiry`). NIFTY weeklies were listed from 2019-02-11; a week before
  that is skipped. Everything below is the same for both.
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

**Every second, where it matters** (`fine_exit`, since 2026-11-05). A close
every 5 minutes cannot see a target or stop touched and left inside the bar, so
BACKTEST (`run_trade` given `fine_for`) walks the position's MTM on the legs'
1-second closes. Only where it could matter: a bar's highs and lows bound what
the MTM can have reached in it (`mtm_bounds`), and a bar that cannot reach any
level needs no second data at all. There the exit books at that second's real
prices, not at the level — the position's P&L is not an order that can rest, and
live reads it and sells at market. `exit_check` picks the grid (`CHECK_SECS`:
1 second, 30 seconds, 1 minute, 3 minutes), each a close at the END of its span
(`check_steps`), or '5m' for the closes alone. A session's first `open_settle_secs` (60) are
not read at all: its first prints are wild, and a gap past a level books at the
prices once they have settled. A bar whose seconds Breeze did not serve
falls back to its 5-minute close and is counted (`bars_unchecked`).

`score_expiry` (Find Best Params, 289,710 combos) sweeps every Exit Check on its
own chart: the 5-minute closes through `first_exits`, and 1 and 3 minutes with
`fine_for` through `fine_sweep`, each deciding every target/stop/lock combo the
way `run_trade` does on that chart. So a sweep row and the backtest of its
parameters agree at every interval (a test holds them equal, combo for combo).

The module is pure: callers hand in the bars, so it never touches a broker.
"""
from __future__ import annotations

import itertools
from datetime import date, timedelta
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
WEEKLY_ENTRY_DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'after_expiry')
CADENCES = ('monthly', 'weekly')
WEEKLY_SINCE = date(2019, 2, 11)       # NIFTY weekly options were listed from here

DEFAULTS = {
    'cadence': 'monthly',      # CADENCES
    'skip_one_night': False,   # weekly: no trade in a week whose expiry is the very next session
    'entry_day': 'last_friday',
    'entry_hm': (15, 15),
    'min_days_to_expiry': 14,
    'target_pct': 5.0,
    'stop_pct': 5.0,
    'adjust': 'none',          # ADJUST_TYPES
    'adjust_pts': 200,         # NIFTY points from the centre that fire it
    'max_adjust': 2,
    'wings': 0,                # >0: BUY the CE this far above / the PE this far below at entry (iron fly)
    'lock_at': 0,              # >0: once the MTM reaches this % of the premium …
    'lock_to': 0,              # … a stop rests at this % (0 = break-even) — 'Profit lock'
    'open_settle_secs': 60,    # every-second exit: a session's first prints are not read for this long
}

_frame = cre._frame
_at = cre._at


def entry_rules(cadence: str = 'monthly') -> tuple:
    return WEEKLY_ENTRY_DAYS if cadence == 'weekly' else ENTRY_DAYS


def _weekly_on_or_after(day: date, sessions: Sequence[date]) -> Optional[date]:
    """The first weekly expiry on/after `day`, across a change of expiry weekday.

    expiry_on_or_after picks the weekday from the day it starts at, so from Fri
    2025-08-29 (Thursday regime) it answered Thu 09-04 — but Tuesday expiries
    began 09-01 and the contract was Tue 09-02; Breeze has nothing for the 4th.
    Starting again from each cutover inside the gap and taking the earliest fixes it."""
    from trading_app.service.expiry_calendar import _EXPIRY_WEEKDAY_REGIMES, expiry_on_or_after
    exp = expiry_on_or_after(day, sessions, cadence='weekly')
    if exp is None:
        return None
    for cutover, _ in _EXPIRY_WEEKDAY_REGIMES:
        if day < cutover <= exp:
            alt = expiry_on_or_after(cutover, sessions, cadence='weekly')
            if alt is not None and day <= alt < exp:
                exp = alt
    return exp


def _weekly_expiries(sessions: Sequence[date]) -> List[date]:
    out, seen = [], set()
    for d in sorted(set(sessions)):
        if d in seen:
            continue
        e = _weekly_on_or_after(d, sessions)
        if e is None:
            continue
        out.append(e)
        seen.update(x for x in sessions if d <= x <= e)
    return sorted(set(out))


def weekly_entry_days(sessions: Sequence[date], start: date, end: date, rule: str = 'friday') -> List[date]:
    """One entry session per week inside [start, end]: that weekday (the
    session before it in the same week when it is a holiday), or the first
    session after each weekly expiry."""
    trading = sorted(set(sessions))
    if rule == 'after_expiry':
        out = []
        for e in _weekly_expiries(trading):
            after = [d for d in trading if d > e][:1]
            if after and start <= after[0] <= end:
                out.append(after[0])
        return out
    wd = WEEKLY_ENTRY_DAYS.index(rule)
    picks: Dict[date, date] = {}
    for d in trading:
        if not start <= d <= end:
            continue
        monday = d - timedelta(days=d.weekday())
        if d.weekday() <= wd:
            picks[monday] = d                  # the latest session on/before the weekday, this week
    return sorted(picks.values())


def entry_days(sessions: Sequence[date], start: date, end: date,
               rule: str = 'last_friday', cadence: str = 'monthly') -> List[date]:
    if cadence == 'weekly':
        return weekly_entry_days(sessions, start, end, rule)
    return cre.entry_days(sessions, start, end, rule)


def _sessions_to(entry: date, expiry: date, sessions: Sequence[date]) -> int:
    """Sessions after `entry` up to and including `expiry` — weekdays standing
    in past the last session we hold."""
    held = [d for d in sessions if entry < d <= expiry]
    last = max(sessions) if sessions else entry
    probe = max(last, entry) + timedelta(days=1)
    while probe <= expiry:
        if probe.weekday() < 5:
            held.append(probe)
        probe += timedelta(days=1)
    return len(held)


def weekly_expiry(entry: date, sessions: Sequence[date], listed: Sequence[date] = ()) -> Optional[date]:
    """The first weekly expiry after the entry session. One still to come is
    snapped to the exchange's listed date in its week, as contract_expiry does."""
    sessions = list(sessions)
    exp = _weekly_on_or_after(entry + timedelta(days=1), sessions)
    if exp is not None and sessions and exp > max(sessions):
        week = [d for d in listed if exp - timedelta(days=6) <= d <= exp]
        if week:
            return max(week)
    return exp


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
    weekly = p.get('cadence') == 'weekly'
    if weekly and entry < WEEKLY_SINCE:
        return {'entry_date': entry, 'expiry': None, 'ok': False,
                'reason': f'No NIFTY weekly contract before {WEEKLY_SINCE.isoformat()}'}
    expiry = (weekly_expiry(entry, sessions, p.get('listed_expiries', ())) if weekly else
              cre.contract_expiry(entry, sessions, p['min_days_to_expiry'], p.get('listed_expiries', ())))
    out: Dict = {'entry_date': entry, 'expiry': expiry}
    if expiry is None:
        return {**out, 'ok': False, 'reason': f"No {'weekly' if weekly else 'monthly'} expiry resolved"}
    if weekly and p.get('skip_one_night') and _sessions_to(entry, expiry, sessions) <= 1:
        return {**out, 'ok': False, 'reason': 'Skipped: expiry is the next session (one-night straddle)'}
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
    sold = entry_px['CE'] + entry_px['PE']
    wings = []
    w = int(p.get('wings') or 0)
    if w > 0:
        for r, k in (('CE', strike + w), ('PE', strike - w)):
            f = frame_for(expiry, k, r)
            px = price_at(f, entry_ts)
            if px is None:
                return {**out, 'ok': False, 'reason': f'No ICICI premium at entry for the {k} {r} wing'}
            wings.append((r, k, px, f))
    # With wings the premium the % are of is the NET credit — what the fly can earn at most.
    return {**out, 'ok': True, 'frames': frames, 'entry_px': entry_px, 'wings': wings,
            'sold_premium': sold, 'premium': sold - sum(x[2] for x in wings)}


# ── Adjustments ──────────────────────────────────────────────────────────────
# When NIFTY's 5-minute close has moved `adjust_pts` from the current centre
# (the sold strike at first), the position is adjusted at that bar's close and
# the centre moves to the new ATM — at most `max_adjust` times, never on the
# expiry day itself:
#   roll_untested — buy back the leg making money (the PE on a rise) and sell
#                   the same side at the new ATM: more premium, delta back to ~0;
#   recenter      — buy back both, sell a fresh straddle at the new ATM;
#   hedge         — BUY the losing side at the new ATM (once per side): the
#                   short leg becomes a spread, its loss capped from there;
#   hedge_roll    — both at once: buy the losing side at the new ATM (once per
#                   side) AND roll the profit leg there — the loss is capped
#                   and fresh premium is collected on the side the market left.
# Target / stop stay % of the FIRST premium collected, on the whole position's
# P&L (closed legs' booked points + open legs' marks).
ADJUST_TYPES = ('none', 'roll_untested', 'recenter', 'hedge', 'hedge_roll')


def _leg(right: str, strike: int, side: int, entry: float, opened: int) -> Dict:
    """side −1 = sold, +1 = bought; `opened` = the bar whose close it was
    traded at (−1 = the entry itself); `closed` None while held."""
    return {'right': right, 'strike': strike, 'side': side, 'entry': float(entry),
            'opened': opened, 'closed': None}


def _on_grid(frame, idx: pd.DatetimeIndex, start_ts: pd.Timestamp) -> tuple:
    """A contract's (closes forward-filled, opens) on the bar grid from `start_ts`."""
    n = len(idx)
    if frame is None or frame.empty:
        return np.full(n, np.nan), np.full(n, np.nan)
    f = frame[frame.index >= start_ts]
    col = f['close']
    close = col.reindex(col.index.union(idx)).ffill().reindex(idx).to_numpy(dtype=float)
    return close, f['open'].reindex(idx).to_numpy(dtype=float)


def _leg_arrays(frame, idx: pd.DatetimeIndex, start_ts: pd.Timestamp, leg: Dict,
                grid: Optional[tuple] = None) -> tuple:
    """(marks, opens) of one leg on the grid: its entry price up to the bar it
    was traded at, then its closes (forward-filled) / opens (a bar without a
    print: the last mark) while held, then frozen at its closing mark.
    `grid` = _on_grid of its contract, when the caller already has it."""
    n = len(idx)
    entry = leg['entry']
    a = leg['opened']
    b = n - 1 if leg['closed'] is None else leg['closed']
    close, op = grid if grid is not None else _on_grid(frame, idx, start_ts)
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
              start_ts: pd.Timestamp, adjusts: List[Dict], cache: Optional[Dict] = None,
              days: Optional[np.ndarray] = None) -> Dict:
    marks, opens = [], []
    memo = cache.setdefault('legs', {}) if cache is not None else {}
    on_grid = cache.setdefault('contracts', {}) if cache is not None else {}
    for leg in legs:
        key = (leg['right'], leg['strike'], leg['entry'], leg['opened'], leg['closed'])
        if key in memo:
            mk, op = memo[key]
        else:
            ck = (leg['right'], leg['strike'])
            if ck not in on_grid:
                on_grid[ck] = _on_grid(frames.get(ck), idx, start_ts)
            mk, op = memo[key] = _leg_arrays(frames.get(ck), idx, start_ts, leg, on_grid[ck])
        marks.append(mk)
        opens.append(op)
    n = len(idx)
    mtm = np.zeros(n)
    open_mtm = np.zeros(n)
    for leg, mk, op in zip(legs, marks, opens):
        mtm += leg['side'] * (mk - leg['entry'])
        open_mtm += leg['side'] * (op - leg['entry'])
    if days is None:
        days = _days(idx)
    return {'idx': idx, 'legs': legs, 'marks': marks, 'opens': opens, 'adjusts': adjusts,
            'mtm': mtm, 'open_mtm': open_mtm, 'frames': frames,
            'new_session': np.r_[False, days[1:] != days[:-1]] if len(days) else np.zeros(0, bool),
            'spot': grid_spot, 'days': days}


def build_path(entry_ts: pd.Timestamp, end_ts: pd.Timestamp, entry_px: Dict[str, float],
               frames: Dict[str, Optional[pd.DataFrame]], spot5: pd.Series,
               strike: int = 0, expiry: Optional[date] = None,
               frame_for: Optional[Callable] = None, adjust: str = 'none',
               adjust_pts: float = 0, max_adjust: int = 0, wings: Sequence[tuple] = (),
               cache: Optional[Dict] = None) -> Dict:
    """Every 5-minute bar starting at/after the entry and finished by `end_ts`:
    each leg's marks (closes, forward-filled; its entry price before its first
    print) and opens, the position's per-unit MTM in points on both (closed
    legs at their closing mark), and which bars open a session. With an
    `adjust` rule the legs change at the bars it fires on (`adjusts`); `wings`
    — (right, strike, price, frame) — are bought at the entry and held.

    `cache` (a dict per entry, from the sweep) keeps the bar grid and each
    leg's arrays across the adjustment variants of one entry — they share the
    entry legs and most of the strikes — so a variant costs only what differs."""
    if cache is not None and 'grid' in cache:
        idx, spot, days = cache['grid']
    else:
        lo, hi = spot5.index.searchsorted(entry_ts), spot5.index.searchsorted(end_ts - BAR, side='right')
        grid = spot5.iloc[lo:hi]
        idx, spot = grid.index, grid.to_numpy()
        days = _days(idx)
        if cache is not None:
            cache['grid'] = (idx, spot, days)
    legs = [_leg(r, strike, -1, entry_px[r], -1) for r in RIGHTS]
    fr: Dict[tuple, Optional[pd.DataFrame]] = {(r, strike): frames.get(r) for r in RIGHTS}
    for r, k, px, f in wings:
        legs.append(_leg(r, k, +1, px, -1))
        fr[(r, k)] = f
    adjusts: List[Dict] = []
    if adjust != 'none' and adjust_pts > 0 and max_adjust > 0 and frame_for is not None and len(idx):
        _adjust(idx, spot, legs, fr, expiry, strike, frame_for, adjust, adjust_pts, max_adjust, adjusts, days)
    return _assemble(idx, spot, legs, fr, entry_ts, adjusts, cache, days)


def _days(idx: pd.DatetimeIndex) -> np.ndarray:
    """Each bar's session date (datetime.date objects)."""
    return np.array(idx.normalize().date) if len(idx) else np.array([], dtype=object)


def adjust_action(kind: str, up: bool, atm: int, held: List[Dict], hedged) -> tuple:
    """What one adjustment does — (short legs to close, [(right, side)] to open) —
    the rule the backtest and the live algo share. `up`: NIFTY rose from the
    centre; `held`: the short legs still open; `hedged`: rights already bought."""
    if kind == 'roll_untested':
        side_r = 'PE' if up else 'CE'
        close = [lg for lg in held if lg['right'] == side_r and lg['strike'] != atm]
        return close, ([(side_r, -1)] if close else [])
    if kind == 'recenter':
        close = [lg for lg in held if lg['strike'] != atm]
        return close, [(lg['right'], -1) for lg in close]
    if kind == 'hedge':                           # buy the tested side, once a side
        side_r = 'CE' if up else 'PE'
        return [], ([] if side_r in hedged else [(side_r, +1)])
    tested, profit = ('CE', 'PE') if up else ('PE', 'CE')   # hedge_roll: both of the above
    close = [lg for lg in held if lg['right'] == profit and lg['strike'] != atm]
    return close, ([(profit, -1)] if close else []) + ([] if tested in hedged else [(tested, +1)])


def _adjust(idx, spot, legs, fr, expiry, centre, frame_for, kind, pts, max_adjust, adjusts,
            days: Optional[np.ndarray] = None) -> None:
    """Walk the triggers in order, editing `legs` / `fr` / `adjusts` in place."""
    if days is None:
        days = _days(idx)
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
        close, opens = adjust_action(kind, up, atm, held, hedged)
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


def exit_fill(path: Dict, pos: int, level: float, falling: Optional[bool] = None) -> tuple:
    """(per-unit P&L, [each leg's price], gap?) of a target (`level` > 0) or
    stop (< 0) that bar `pos` crossed. At the level, the legs interpolated
    between the last mark (the session's open on a session's first bar, else
    the previous close) and this close; a session that OPENS past the level
    fills at that open. `falling` — the MTM is coming DOWN to the level (a stop,
    or a profit lock above zero); by default a level below zero."""
    if falling is None:
        falling = level < 0
    beyond = (lambda v: v <= level) if falling else (lambda v: v >= level)
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
                target_pct: float, stop_pct: float, lock_to: float = 0) -> tuple:
    """(per-unit P&L, [each leg's price at the exit], reason) of a first_exit
    result — the one booking rule the backtest and the sweep share."""
    if pos < 0:
        return 0.0, [lg['entry'] for lg in path['legs']], reason
    if reason in ('Target', 'Stop loss'):
        level = premium * (target_pct if reason == 'Target' else -stop_pct) / 100.0
        pts, px, gap = exit_fill(path, pos, level)
        return pts, px, reason + (' (gap)' if gap else '')
    if reason == 'Profit lock':
        pts, px, gap = exit_fill(path, pos, premium * lock_to / 100.0, falling=True)
        return pts, px, reason + (' (gap)' if gap else '')
    return float(path['mtm'][pos]), [float(m[pos]) for m in path['marks']], reason


def _first(mask: np.ndarray) -> int:
    return int(mask.argmax()) if mask.any() else -1


def first_exit(path: Dict, premium: float, target_pct: float, stop_pct: float,
               cut_ts: pd.Timestamp, hits: Optional[Dict] = None,
               lock_at: float = 0, lock_to: float = 0) -> tuple:
    """(bar position, reason). 'Open (data ends)' when the time exit was not
    reached and nothing tripped; position −1 = no bar after the entry at all.
    `hits` memoises first-hit positions for a sweep asking one path often.

    Profit lock (`lock_at` > 0): from the bar after a CLOSE at/above `lock_at` %
    of the premium, a stop rests at `lock_to` % — it moves up, never down, so it
    only counts while above the ordinary stop."""
    idx = path['idx']
    memo = {} if hits is None else hits
    if ('cut', cut_ts) not in memo:
        memo[('cut', cut_ts)] = int(idx.searchsorted(cut_ts - BAR, side='right'))   # bars allowed: [0, n_cut)
    n_cut = memo[('cut', cut_ts)]
    if ('t', target_pct) not in memo:
        memo[('t', target_pct)] = _first(path['mtm'] >= premium * target_pct / 100.0)
    if ('s', stop_pct) not in memo:
        memo[('s', stop_pct)] = _first(path['mtm'] <= -premium * stop_pct / 100.0)
    cands = [(memo[('t', target_pct)], 0, 'Target'), (memo[('s', stop_pct)], 2, 'Stop loss')]
    if lock_at > 0 and lock_to < lock_at and lock_to > -stop_pct:
        if ('l', lock_at, lock_to) not in memo:
            armed = _first(path['mtm'] >= premium * lock_at / 100.0)
            hit = -1
            if armed >= 0:
                after = _first(path['mtm'][armed + 1:] <= premium * lock_to / 100.0)
                hit = armed + 1 + after if after >= 0 else -1
            memo[('l', lock_at, lock_to)] = hit
        cands.append((memo[('l', lock_at, lock_to)], 1, 'Profit lock'))
    hit = min(((pos, rank, r) for pos, rank, r in cands if 0 <= pos < n_cut), default=None)
    if hit:
        return hit[0], hit[2]
    if n_cut == 0:
        return -1, 'Open (data ends)'
    last = n_cut - 1
    if idx[last].date() == cut_ts.date():
        return last, 'Expiry'
    return last, 'Open (data ends)'


_REASONS = ('Target', 'Profit lock', 'Stop loss', 'Expiry', 'Open (data ends)')


def first_exits(path: Dict, premium: float, ex: Dict[str, np.ndarray],
                cut_ts: pd.Timestamp) -> tuple:
    """first_exit for MANY (target, stop, lock at, lock to) at once — `ex`
    holds them as equal-length arrays. Returns (positions, reason indices into
    _REASONS). The same decision as first_exit, combo for combo: the earliest
    bar inside the cut, a target before a lock before a stop on one bar (a test
    holds the two equal). A target's first hit is where the running MAX of the
    MTM first reaches it, a stop's where the running MIN does — one
    searchsorted per level instead of a scan per combo."""
    mtm = path['mtm']
    n_cut = int(path['idx'].searchsorted(cut_ts - BAR, side='right'))
    m = len(ex['tp'])
    if len(mtm) == 0:
        return np.full(m, -1), np.full(m, 4)
    pos, why = _first_hits(mtm, mtm, n_cut, premium, ex)
    none = pos >= n_cut
    if none.any():
        if n_cut == 0:
            pos[none], why[none] = -1, 4
        else:
            last = n_cut - 1
            pos[none] = last
            why[none] = 3 if path['idx'][last].date() == cut_ts.date() else 4
    return pos, why


def _first_hits(up_mtm: np.ndarray, down_mtm: np.ndarray, n_cut: int, premium: float,
                ex: Dict[str, np.ndarray]) -> tuple:
    """Where each combo's target, lock or stop first hits among the first `n_cut`
    marks: (positions, 0 target | 1 lock | 2 stop); position >= n_cut = nothing did.
    `up_mtm` is what a target and a lock's arming read, `down_mtm` what a stop and a
    lock's fill read (the same array on 5-minute closes)."""
    m = len(ex['tp'])
    up = np.maximum.accumulate(up_mtm)
    down = -np.minimum.accumulate(down_mtm)

    def first(run, levels):
        """First mark whose running value reaches each level; n_cut+ = never (inside the cut)."""
        u, inv = np.unique(levels, return_inverse=True)
        pos = np.searchsorted(run, u, side='left')
        return np.minimum(pos, n_cut)[inv]

    t = first(up, premium * ex['tp'] / 100.0)
    st = first(down, premium * ex['sp'] / 100.0)
    lk = np.full(m, n_cut)
    on = (ex['la'] > 0) & (ex['lt'] < ex['la']) & (ex['lt'] > -ex['sp'])
    if on.any():
        pairs = {}
        for la, lt in set(zip(ex['la'][on].tolist(), ex['lt'][on].tolist())):
            armed = _first(up_mtm >= premium * la / 100.0)
            hit = n_cut
            if armed >= 0:
                after = _first(down_mtm[armed + 1:] <= premium * lt / 100.0)
                if after >= 0:
                    hit = min(armed + 1 + after, n_cut)
            pairs[(la, lt)] = hit
        lk[on] = [pairs[k] for k in zip(ex['la'][on].tolist(), ex['lt'][on].tolist())]
    # earliest mark, ties by rank: target 0, lock 1, stop 2
    best = np.minimum(np.minimum(t * 3, lk * 3 + 1), st * 3 + 2)
    return best // 3, best % 3


# ── The exit on finer closes ────────────────────────────────────────────────
CHECK_SECS = {'1m': 60, '3m': 180}     # '5m' = first_exit, on the closes
# What the box and Saved rows used to send. 1-second data was never there for more than part of a
# run, so those intervals went on 2026-11-05 and their rows now read 1 minute.
OLD_CHECKS = {'second': '1m', 'bar': '5m', '1s': '1m', '30s': '1m'}
# Inside the engine a close is stamped its span's last second, which keeps it in its own
# 5-minute bar. The time it happened, and the time a trade shows, is one second on: the
# 09:15 candle closes at 09:16:00, as the 5-minute chart's first close shows 09:20:00.
_STAMP_TO_CLOSE = pd.Timedelta(seconds=1)


def check_steps(closes: pd.Series, secs: int, origin: pd.Timestamp) -> pd.Series:
    """Finer closes → one per `secs`, counted from `origin` (the 09:15 open),
    each stamped the last second of its span: a 3-minute close is not known
    before it, and 09:20:59 belongs to the 09:20 bar, after that bar's opening
    adjustment. It happens at the end of that second (_STAMP_TO_CLOSE). A span
    with no print holds the last close."""
    if secs <= 1 or closes.empty:
        return closes
    spans = closes.resample(f'{secs}s', origin=origin).last().ffill().dropna()
    spans.index = spans.index + pd.Timedelta(seconds=secs - 1)
    return spans



def mtm_bounds(path: Dict, cache: Optional[Dict] = None) -> tuple:
    """(lowest, highest) the position's MTM can have touched in each bar: every
    held leg at the worst / best of its bar's low, high, close and the previous
    mark. A bar that does not reach a level on these cannot have crossed it."""
    idx = path['idx']
    n = len(idx)
    lo, hi = np.zeros(n), np.zeros(n)
    for leg, mk in zip(path['legs'], path['marks']):
        px_lo, px_hi = mk.copy(), mk.copy()
        a = leg['opened']
        b = n - 1 if leg['closed'] is None else leg['closed']
        if b > a:
            prev = np.r_[leg['entry'], mk[:-1]]
            key = (leg['right'], leg['strike'])
            ranges = cache.setdefault('ranges', {}) if cache is not None else {}
            if key not in ranges:
                frame = path['frames'].get(key)
                if frame is None or frame.empty:
                    ranges[key] = (np.full(n, np.nan), np.full(n, np.nan))
                else:
                    ranges[key] = (frame['high'].reindex(idx).to_numpy(dtype=float),
                                   frame['low'].reindex(idx).to_numpy(dtype=float))
            bar_hi, bar_lo = ranges[key]
            seg = slice(a + 1, b + 1)
            px_hi[seg] = np.fmax(np.maximum(mk[seg], prev[seg]), bar_hi[seg])
            px_lo[seg] = np.fmin(np.minimum(mk[seg], prev[seg]), bar_lo[seg])
        worst, best = (px_hi, px_lo) if leg['side'] < 0 else (px_lo, px_hi)
        lo += leg['side'] * (worst - leg['entry'])
        hi += leg['side'] * (best - leg['entry'])
    return lo, hi


def second_marks(path: Dict, pos: int, expiry: date, entry_ts: pd.Timestamp,
                 fine_for: Callable, settle_secs: float = 0) -> Optional[tuple]:
    """Bar `pos` second by second: (stamps, MTM at each, {held leg: its prices}),
    every leg forward-filled from its own last print. None when a held leg
    printed in this bar and its seconds are not to be had."""
    start = path['idx'][pos]
    end, first = start + BAR, max(start, entry_ts)
    held = _held_at(path, pos)
    parts = {}
    for i in held:
        lg = path['legs'][i]
        s = fine_for(expiry, lg['strike'], lg['right'], start)
        if s is None:
            return None
        lo, hi = s.index.searchsorted(first), s.index.searchsorted(end)   # a contract's closes are in time order
        times = s.index.values[lo:hi].astype('datetime64[ns]').astype('int64')
        frame = path['frames'].get((lg['right'], lg['strike']))
        if not len(times) and frame is not None and start in frame.index:
            return None
        parts[i] = (times, s.to_numpy(dtype=float)[lo:hi])
    stamps = np.unique(np.concatenate([t for t, _ in parts.values()])) if parts else np.zeros(0, dtype='int64')
    base = sum(lg['side'] * (path['marks'][i][pos] - lg['entry'])
               for i, lg in enumerate(path['legs']) if i not in parts)
    mtm = np.full(len(stamps), float(base))
    prices = {}
    for i, (times, closes) in parts.items():
        lg = path['legs'][i]
        prev = path['marks'][i][pos - 1] if pos > 0 else lg['entry']
        # Each leg at its own last print by then; its previous mark before its first.
        at = np.searchsorted(times, stamps, side='right') - 1
        px = np.where(at >= 0, closes[at.clip(0)] if len(times) else prev, prev)
        prices[i] = px
        mtm += lg['side'] * (px - lg['entry'])
    if path['new_session'][pos] and len(stamps):
        # A leg yet to print today still shows yesterday's close: on a gap the
        # other leg's first print against it is an MTM that never existed. And
        # the first prints are not prices (2026-02-03, 25100 CE at 09:15:00-05:
        # 1049, 1242, 921, 800, 732, 780 with ~700 of intrinsic), so nothing is
        # read until `settle_secs` in, on each leg's last print by then.
        # A close is stamped its span's last second and is the price at the END of that
        # second, so the first minute's close is the price 60 s in: readable at a 60 s settle.
        ready = max([t[0] for t, _ in parts.values() if len(t)]
                    + [(start + pd.Timedelta(seconds=settle_secs) - _STAMP_TO_CLOSE).value])
        keep = stamps >= ready
        stamps, mtm = stamps[keep], mtm[keep]
        prices = {i: px[keep] for i, px in prices.items()}
    return pd.DatetimeIndex(stamps.astype('datetime64[ns]')), mtm, prices


def fine_exit(path: Dict, premium: float, target_pct: float, stop_pct: float, cut_ts: pd.Timestamp,
              expiry: date, entry_ts: pd.Timestamp, fine_for: Callable,
              lock_at: float = 0, lock_to: float = 0, settle_secs: float = 0) -> Dict:
    """first_exit read on every second of the bars that could hold an exit.
    A target beats a lock beats a stop inside one second, as on one bar; the
    lock arms on the second the MTM reaches `lock_at`, and can only fire after it.
    `fine_for(expiry, strike, right, bar start)` is that contract's 1-second
    closes around the bar (a Series), or None when it cannot be had; such a bar
    is read on its 5-minute close, exactly as first_exit would have."""
    idx = path['idx']
    n_cut = int(idx.searchsorted(cut_ts - BAR, side='right'))
    tp = premium * target_pct / 100.0
    sp = -premium * stop_pct / 100.0
    lock_on = lock_at > 0 and lock_to < lock_at and lock_to > -stop_pct
    arm, lk = premium * lock_at / 100.0, premium * lock_to / 100.0
    armed = False
    lo, hi = mtm_bounds(path)
    checked = unchecked = 0

    def read(t):
        return second_marks(path, t, expiry, entry_ts, fine_for, settle_secs)

    def done(pos, reason, pts=None, exit_px=None, ts=None):
        if pts is None:
            pts, exit_px, reason = exit_points(path, pos, reason, premium, target_pct, stop_pct, lock_to)
        return {'pos': pos, 'reason': reason, 'pts': pts, 'exit_px': exit_px, 'exit_ts': ts,
                'bars_checked': checked, 'bars_unchecked': unchecked}

    # Only a bar that reaches a level under EITHER state of the lock can hold anything.
    up_at = min(arm, tp) if lock_on else tp
    down_at = max(lk, sp) if lock_on else sp
    for t in np.flatnonzero((hi[:n_cut] >= up_at) | (lo[:n_cut] <= down_at)).tolist():
        up = hi[t] >= (arm if lock_on and not armed and arm < tp else tp)
        down = lo[t] <= (lk if armed and lk > sp else sp)
        if not up and not down:
            continue
        fine = read(t)
        if fine is None:                       # no seconds: the 5-minute close decides this bar
            unchecked += 1
            m = path['mtm'][t]
            if m >= tp:
                return done(t, 'Target')
            if armed and m <= lk:
                return done(t, 'Profit lock')
            if m <= sp:
                return done(t, 'Stop loss')
            armed = armed or (lock_on and m >= arm)
            continue
        checked += 1
        stamps, mtm, prices = fine
        if not len(stamps):
            continue
        start = 0
        if lock_on and not armed:
            k = _first(mtm >= arm)
            exit_before = _first((mtm >= tp) | (mtm <= sp))
            if k >= 0 and (exit_before < 0 or k < exit_before):
                armed, start = True, k + 1
        hits = [(_first(mtm >= tp), 0, 'Target'), (_first(mtm <= sp), 2, 'Stop loss')]
        if armed:
            after = _first(mtm[start:] <= lk)
            hits.append((start + after if after >= 0 else -1, 1, 'Profit lock'))
        hit = min(((k, rank, r) for k, rank, r in hits if k >= 0), default=None)
        if hit is None:
            continue
        k, _, reason = hit
        exit_px = [float(prices[i][k]) if i in prices else float(path['marks'][i][t])
                   for i in range(len(path['legs']))]
        if k == 0 and path['new_session'][t]:
            reason += ' (gap)'
        return done(t, reason, float(mtm[k]), exit_px, stamps[k] + _STAMP_TO_CLOSE)
    if n_cut == 0:
        return done(-1, 'Open (data ends)')
    return done(n_cut - 1, 'Expiry' if idx[n_cut - 1].date() == cut_ts.date() else 'Open (data ends)')


def month_path(m: Dict, spot5: pd.Series, frame_for: Callable, p: Dict,
               cache: Optional[Dict] = None) -> Dict:
    """The path of an opened month (open_month) under `p`'s adjustment rule."""
    return build_path(m['entry_ts'], cut_time(m['expiry']), m['entry_px'], m['frames'], spot5,
                      m['strike'], m['expiry'], frame_for, p.get('adjust', 'none'),
                      float(p.get('adjust_pts') or 0), int(p.get('max_adjust') or 0), m.get('wings', ()),
                      cache)


def run_trade(entry: date, sessions: Sequence[date], spot5: pd.Series,
              frame_for: Callable[[date, int, str], Optional[pd.DataFrame]],
              params: Optional[Dict] = None, fine_for: Optional[Callable] = None) -> Dict:
    """One month's straddle from entry to exit; 'status' in traded | open | skipped.

    `spot5` is NIFTY's 5-minute closes (indexed by bar start) over the run,
    `frame_for(expiry, strike, right)` that contract's 5-minute frame. With
    `fine_for` the exit is read on those finer closes (fine_exit), else on the 5-minute ones."""
    p = {**DEFAULTS, **(params or {})}
    m = open_month(entry, sessions, spot5, frame_for, p)
    base = {'entry_date': entry, 'expiry': m['expiry'], 'spot': m.get('spot'), 'strike': m.get('strike')}
    if not m['ok']:
        return {**base, 'status': 'skipped', 'reason': m['reason']}
    cut = cut_time(m['expiry'])
    path = month_path(m, spot5, frame_for, p)
    lock = (float(p.get('lock_at') or 0), float(p.get('lock_to') or 0))
    if fine_for is not None:
        r = fine_exit(path, m['premium'], p['target_pct'], p['stop_pct'], cut, m['expiry'],
                      m['entry_ts'], fine_for, *lock, settle_secs=float(p.get('open_settle_secs') or 0))
        return {**base, **_booked(path, r['pos'], r['reason'], r['pts'], r['exit_px'], m, r['exit_ts']),
                'bars_checked': r['bars_checked'], 'bars_unchecked': r['bars_unchecked']}
    pos, reason = first_exit(path, m['premium'], p['target_pct'], p['stop_pct'], cut, None, *lock)
    pts, exit_px, reason = exit_points(path, pos, reason, m['premium'], p['target_pct'], p['stop_pct'],
                                       lock[1])
    return {**base, **_booked(path, pos, reason, pts, exit_px, m)}


def legs_traded(path: Dict, pos: int) -> int:
    """Legs opened by the time bar `pos` exits — the brokerage count (each is
    one sold-and-bought-back, or bought-and-sold, leg)."""
    return sum(1 for lg in path['legs'] if lg['opened'] < pos)


def _booked(path: Dict, pos: int, reason: str, pts: float, exit_px: List[float], m: Dict,
            exit_ts: Optional[pd.Timestamp] = None) -> Dict:
    if pos < 0:
        exit_ts, upto = m['entry_ts'], np.zeros(0)
    else:
        # A level fill happened inside the bar; its close is when it is known.
        exit_ts = exit_ts if exit_ts is not None else path['idx'][pos] + BAR
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
        'sold_premium': m.get('sold_premium', m['premium']),
        'wings': [(r, k, px) for r, k, px, _ in m.get('wings', ())],
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
    of the premium collected (CE + PE sold, less the wings bought, × qty)."""
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
    if res.get('wings'):
        row['wings'] = ' / '.join(f"{r} {k} @{px:.1f}" for r, k, px in res['wings'])
        row['sold_premium'] = round(res['sold_premium'], 2)
    gross = res['pnl_points'] * qty
    brokerage = brokerage_per_leg * res['legs_traded']
    net = gross - brokerage
    if 'bars_unchecked' in res:
        row['bars_checked'] = res['bars_checked']
        row['bars_unchecked'] = res['bars_unchecked']
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


MAX_LOTS = 27          # NIFTY's freeze quantity in lots — one order's worth


def lots_for_capital(capital: float, per_lot: float, max_lots: int = MAX_LOTS) -> int:
    """1 lot per `per_lot` ₹ of the balance, at least 1 and at most `max_lots`;
    0 when the balance is at/below zero. The backtest and the live algo share it."""
    if capital <= 0:
        return 0
    return int(max(1, min(max_lots, capital // per_lot)))


def compound(rows: List[Dict], start_capital: float, per_lot: float,
             max_lots: int = MAX_LOTS) -> List[Dict]:
    """Re-size every trade from the running balance: 1 lot per `per_lot` ₹
    (₹2.5 L start, ₹2.5 L a lot → 1 lot; at ₹5 L → 2 lots), at least 1 and at most
    `max_lots`. The balance at an entry is the start plus the net ₹ of every
    trade already CLOSED by then — a monthly trade still open when the next one
    is sold has not paid yet. A balance at/below zero stops trading
    ('Capital exhausted'). Brokerage is per leg traded, so it does not scale;
    the points do. Rows in, rows out, oldest first; skipped rows untouched."""
    out: List[Dict] = []
    closed: List[tuple] = []                  # (exit time, net ₹) of trades sized so far
    for row in sorted(rows, key=lambda r: r.get('entry_time') or r['date']):
        r = dict(row)
        if r['status'] == 'skipped':
            out.append(r)
            continue
        at = r['entry_time']
        capital = start_capital + sum(net for t, net in closed if t is not None and t <= at)
        r['capital'] = round(capital, 2)
        lots = lots_for_capital(capital, per_lot, max_lots)
        if not lots:
            r.update({'status': 'skipped', 'result': 'SKIP', 'exit_reason': 'Capital exhausted',
                      'pnl': None, 'pnl_rupees': None, 'pnl_pct': None})
            out.append(r)
            continue
        per_unit = r['gross_pnl_rupees'] / r['qty'] if r['qty'] else 0.0
        qty = lots * r['lot_size']
        gross = per_unit * qty
        net = gross - r['brokerage']
        collected = r['premium'] * qty
        r.update({'lots': lots, 'qty': qty,
                  'premium_rupees': round(collected, 2),
                  'gross_pnl_rupees': round(gross, 2), 'pnl_rupees': round(net, 2),
                  'pnl_pct': round(100.0 * net / collected, 2) if collected else 0.0})
        if r['status'] == 'traded':
            r['result'] = 'WIN' if net > 0 else ('LOSS' if net < 0 else 'SCRATCH')
            closed.append((r['exit_time'], net))
        out.append(r)
    return out


def summarise(rows: List[Dict]) -> Dict:
    """Stats on the CLOSED months (open and skipped ones are counted, not scored)."""
    return cre.summarise(rows)


# ── Find Best Params ─────────────────────────────────────────────────────────
# Expiry (monthly | weekly) × entry day × wings × adjustment × target × stop ×
# profit lock. The path depends on the entry, the wings and the adjustment
# only, so each (month, entry day, wings, adjustment) is ONE path and every
# target/stop/lock is a replay of it. The adjustments' strikes and the wings
# are new contracts (cached): a cold month is the two entry legs per entry day,
# the wings, and every ATM an adjustment reaches — roughly the 50-strikes NIFTY
# crossed that month, both sides.
EXIT_GRID = {
    'target_pct': (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50),
    'stop_pct': (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50),
}
EXIT_KEYS = tuple(EXIT_GRID)
ADJUST_PTS = (100, 150, 200, 250, 300, 400, 500)
ADJUST_VARIANTS = (('none', 0),) + tuple((k, pt) for k in ADJUST_TYPES[1:] for pt in ADJUST_PTS)
# Wings are a backtest box only: over Dec 2019 – Nov 2026 ±300 / ±500 wings never
# beat the same setups without them on net or on net ÷ drawdown, and each value
# costs two more contracts per entry day.
WINGS = (0,)
# (lock at %, lock to %) — off, plus the levels that cut drawdown most in the
# 2019-26 sweep without costing much net.
LOCKS = ((0, 0), (15, 0), (15, 5), (20, 10), (25, 10), (30, 10), (30, 20), (40, 30))
SWEEP_KEYS = ('cadence', 'entry_day', 'skip_one_night', 'wings', 'adjust', 'adjust_pts') + EXIT_KEYS + ('lock_at', 'lock_to')


def entries_by_month(sessions: Sequence[date], start: date, end: date,
                     rules: Optional[Sequence[str]] = None,
                     cadence: str = 'monthly') -> Dict[tuple, Dict[str, date]]:
    """{period: {entry rule: entry date}} — what one sweep period scores. The
    period is (year, month), or under the weekly cadence the ISO (year, week)
    of the week the entries open."""
    out: Dict[tuple, Dict[str, date]] = {}
    for rule in (rules or entry_rules(cadence)):
        for d in entry_days(sessions, start, end, rule, cadence):
            if cadence == 'weekly' and d < WEEKLY_SINCE:
                continue                       # no weekly contract yet — not a week to score
            key = tuple(d.isocalendar()[:2]) if cadence == 'weekly' else (d.year, d.month)
            out.setdefault(key, {})[rule] = d
    return out


def exit_combos(grid: Dict = EXIT_GRID, locks: Sequence[tuple] = LOCKS) -> List[tuple]:
    """(target, stop, lock at, lock to) — a lock that arms at/above the target
    could never act, so it is not a combo of its own."""
    return [(tp, sp, la, lt) for tp, sp in itertools.product(*(grid[k] for k in EXIT_KEYS))
            for la, lt in locks if la == 0 or la < tp]


def sweep_size(grid: Dict = EXIT_GRID, variants: Sequence[tuple] = ADJUST_VARIANTS,
               wings: Sequence[int] = WINGS, locks: Sequence[tuple] = LOCKS,
               cadences: Sequence[str] = CADENCES) -> int:
    per = len(wings) * len(variants) * len(exit_combos(grid, locks))
    # weekly is scored with and without skipping one-night weeks
    return sum(len(entry_rules(c)) * (2 if c == 'weekly' else 1) for c in cadences) * per


def _step_grid(idx: pd.DatetimeIndex, secs: int) -> tuple:
    """Every close of a `secs` chart inside the 5-minute bars `idx`: (stamps in ns,
    each one's bar, each bar's first step, len(idx)+1 long). A stamp is its span's
    last second, the spans counted from 09:15 (check_steps)."""
    start = idx.values.astype('datetime64[ns]').astype('int64')
    sec, step = 10 ** 9, secs * 10 ** 9
    origin = start - start % (86400 * sec) + (9 * 3600 + 15 * 60) * sec
    lo = -((origin - start - sec) // step)              # ceil((start + 1 s - origin) / step)
    hi = -((origin - start - 300 * sec - sec) // step)
    counts = hi - lo
    bar_of = np.repeat(np.arange(len(idx)), counts)
    first = np.r_[0, np.cumsum(counts)]
    k = lo[bar_of] + (np.arange(first[-1]) - first[bar_of])
    return origin[bar_of] + k * step - sec, bar_of, first


class _ContractSteps:
    """One contract's closes on an entry's step grid, read a session at a time and
    only for the sessions a leg holds it, so a strike held for a day costs that day."""

    def __init__(self, n_steps: int, n_bars: int, frame):
        self.px = np.full(n_steps, np.nan)
        self.stamped = np.zeros(n_steps, bool)
        self.no_data = np.zeros(n_bars, bool)
        self.frame = frame
        self.days_read = set()

    def read(self, a: int, b: int, days: np.ndarray, idx, stamps, first, expiry, strike, right, fine_for) -> None:
        """Have the sessions of bars [a, b) been read? Read those that have not."""
        for day in pd.unique(days[a:b]).tolist():
            if day in self.days_read:
                continue
            self.days_read.add(day)
            t0 = int(np.searchsorted(days, day, side='left'))
            t1 = int(np.searchsorted(days, day, side='right'))
            closes = fine_for(expiry, strike, right, idx[t0])
            if closes is None:
                self.no_data[t0:t1] = True
                continue
            s0, s1 = first[t0], first[t1]
            times = closes.index.values.astype('datetime64[ns]').astype('int64')
            at = np.searchsorted(stamps[s0:s1], times)
            ok = at < s1 - s0
            ok[ok] = stamps[s0:s1][at[ok]] == times[ok]
            vals = closes.to_numpy(dtype=float)
            self.px[s0 + at[ok]] = vals[ok]
            self.stamped[s0 + at[ok]] = True
            # A bar the 5-minute chart printed in, with no close here: this chart cannot read it.
            if self.frame is not None and not self.frame.empty:
                printed = idx[t0:t1].isin(self.frame.index)
                seen = np.logical_or.reduceat(self.stamped, first[t0:t1]) if s1 > s0 else np.zeros(t1 - t0, bool)
                self.no_data[t0:t1] = printed & ~seen


def fine_sweep(path: Dict, premium: float, ex: Dict[str, np.ndarray], cut_ts: pd.Timestamp, expiry: date,
               fine_for: Callable, secs: int, settle_secs: float, cache: Dict) -> Dict:
    """fine_exit for MANY (target, stop, lock at, lock to) at once. The path's MTM on
    every close of the chart, in order, and each combo's first hit on it; bars with no
    data take one mark, the 5-minute close, as in fine_exit. A level a bar's own
    high/low cannot reach is not read inside that bar (fine_exit skips the bar), which
    is what the capped `up` / `down` arrays do. A test holds it equal to fine_exit."""
    idx, legs = path['idx'], path['legs']
    n_cut = int(idx.searchsorted(cut_ts - BAR, side='right'))
    if 'steps' not in cache:
        cache['steps'] = _step_grid(idx, secs)
        cache['contracts_fine'] = {}
    stamps, bar_of, first = cache['steps']
    days = path['days']
    n_steps = int(first[n_cut])
    mtm = np.zeros(n_steps)
    valid = np.zeros(n_steps, bool)
    missing = np.zeros(n_cut, bool)
    bars = bar_of[:n_steps]
    fresh = np.flatnonzero(path['new_session'][:n_cut])
    ready_at = np.zeros(len(fresh), dtype='int64')
    for i, leg in enumerate(legs):
        mk = path['marks'][i]
        side, entry = leg['side'], leg['entry']
        mtm += side * (mk[bars] - entry)
        a = leg['opened'] + 1
        b = min(n_cut, len(idx) if leg['closed'] is None else leg['closed'] + 1)
        if b <= a:
            continue
        key = (leg['right'], leg['strike'])
        steps = cache['contracts_fine'].get(key)
        if steps is None:
            steps = cache['contracts_fine'][key] = _ContractSteps(len(stamps), len(idx), path['frames'].get(key))
        steps.read(a, b, days, idx, stamps, first, expiry, leg['strike'], leg['right'], fine_for)
        lo, hi = first[a], first[b]
        seg = bars[lo:hi]
        prev = np.r_[entry, mk[:-1]][seg]
        here = steps.stamped[lo:hi]
        mtm[lo:hi] += side * (np.where(here, steps.px[lo:hi], prev) - mk[seg])
        valid[lo:hi] |= here
        missing[a:b] |= steps.no_data[a:b]
        for j, t in enumerate(fresh.tolist()):
            if a <= t < b:
                got = steps.stamped[first[t]:first[t + 1]]
                if got.any():
                    ready_at[j] = max(ready_at[j], first[t] + int(got.argmax()))
    # A session's first prints are not read (second_marks): nothing before every leg has
    # printed today, nor inside the settle.
    gap = np.zeros(n_steps, bool)
    for j, t in enumerate(fresh.tolist()):
        s0, s1 = int(first[t]), int(first[t + 1])
        settled = int(np.searchsorted(stamps[s0:s1], idx[t].value + int(settle_secs * 10 ** 9)
                                      - _STAMP_TO_CLOSE.value, side='left'))
        keep_from = max(s0 + settled, int(ready_at[j]))
        valid[s0:keep_from] = False
        on = np.flatnonzero(valid[keep_from:s1])
        if len(on):
            gap[keep_from + on[0]] = True
    # No data for a bar: its 5-minute close, once.
    fallback = np.zeros(n_steps, bool)
    for t in np.flatnonzero(missing).tolist():
        s0, s1 = int(first[t]), int(first[t + 1])
        valid[s0:s1] = False
        gap[s0:s1] = False
        if s1 > s0:
            valid[s1 - 1] = fallback[s1 - 1] = True
            mtm[s1 - 1] = path['mtm'][t]
    keep = np.flatnonzero(valid)
    kept_bars = bars[keep]
    lo_b, hi_b = mtm_bounds(path, cache)
    marks = mtm[keep]
    if len(keep):
        pos, why = _first_hits(np.minimum(marks, hi_b[kept_bars]), np.maximum(marks, lo_b[kept_bars]),
                               len(keep), premium, ex)
    else:
        pos, why = np.zeros(len(ex['tp']), dtype=int), np.zeros(len(ex['tp']), dtype=int)
    return {'pos': pos, 'why': why, 'n': len(keep), 'marks': marks, 'bars': kept_bars,
            'gap': gap[keep], 'fallback': fallback[keep], 'n_cut': n_cut,
            'unchecked': np.cumsum(missing)}


def _book_fine(path: Dict, f: Dict, premium: float, ex: Dict[str, np.ndarray], cut_ts: pd.Timestamp,
               qty: int, collected: float, brokerage_per_leg: float) -> tuple:
    """Each combo's booked row from fine_sweep: (rows, index of each combo's row).
    Most combos share a (mark, reason, level) and each distinct one is booked once."""
    pos, why, n = f['pos'], f['why'], f['n']
    level = np.select([why == 0, why == 1, why == 2], [ex['tp'], ex['lt'], ex['sp']], -1.0)
    none = pos >= n
    level = np.where(none | ~f['fallback'][np.minimum(pos, max(n - 1, 0))] if n else np.ones(len(pos), bool), -1.0, level)
    code = np.where(none, 3, why)
    keys, inv = np.unique(np.stack([np.where(none, -1, pos), code, level], axis=1), axis=0, return_inverse=True)
    rows = []
    n_cut, unchecked = f['n_cut'], f['unchecked']
    for kpos, kcode, klevel in keys:
        kpos, kcode = int(kpos), int(kcode)
        if kcode == 3:                         # nothing tripped: the expiry, or the data's end
            if n_cut == 0:
                t, reason = -1, _REASONS[4]
            else:
                t = n_cut - 1
                reason = _REASONS[3] if path['idx'][t].date() == cut_ts.date() else _REASONS[4]
            pts, _, reason = exit_points(path, t, reason, premium, 0, 0, 0)
        else:
            t, reason = int(f['bars'][kpos]), _REASONS[kcode]
            if f['fallback'][kpos]:
                pts, _, reason = exit_points(path, t, reason, premium, klevel, klevel, klevel)
            else:
                pts = float(f['marks'][kpos])
                if f['gap'][kpos]:
                    reason += ' (gap)'
        n_legs = legs_traded(path, t) if t >= 0 else len(path['legs'])
        net = pts * qty - brokerage_per_leg * n_legs
        rows.append(('open' if reason.startswith('Open') else 'traded', net,
                     100.0 * net / collected if collected else 0.0, reason,
                     int(unchecked[t]) if t >= 0 else 0))
    return rows, inv.ravel()


def score_expiry(entry: date, sessions: Sequence[date], spot5: pd.Series,
                 frame_for: Callable[[date, int, str], Optional[pd.DataFrame]],
                 qty: int, base: Dict, brokerage_per_leg: float,
                 grid: Dict = EXIT_GRID,
                 variants: Sequence[tuple] = ADJUST_VARIANTS,
                 wings: Sequence[int] = WINGS, locks: Sequence[tuple] = LOCKS,
                 fine_for: Optional[Callable] = None, secs: int = 0) -> Dict[tuple, tuple]:
    """Every (wings, adjust, adjust pts, target, stop, lock at, lock to) on ONE
    entry: {combo: (status, net ₹, % of premium, reason[, bars read on the 5-minute
    close for want of data])}. With `fine_for` the exits are read on that `secs`
    chart (fine_sweep), else on the 5-minute closes."""
    exits = exit_combos(grid, locks)
    ex = {k: np.array([e[i] for e in exits], dtype=float) for i, k in enumerate(('tp', 'sp', 'la', 'lt'))}
    out: Dict[tuple, tuple] = {}
    for w in wings:
        p0 = {**DEFAULTS, **base, 'wings': w}
        m = open_month(entry, sessions, spot5, frame_for, p0)
        if not m['ok']:
            out.update({(w,) + v + c: ('skipped', 0.0, 0.0, m['reason']) for v in variants for c in exits})
            continue
        cut = cut_time(m['expiry'])
        collected = m['premium'] * qty
        cache: Dict = {}                       # the grid and leg arrays, shared by every variant
        for kind, pts_trigger in variants:
            path = month_path(m, spot5, frame_for, {**p0, 'adjust': kind, 'adjust_pts': pts_trigger}, cache)
            head = (w, kind, pts_trigger)
            if fine_for is not None:
                f = fine_sweep(path, m['premium'], ex, cut, m['expiry'], fine_for, secs,
                               float(p0.get('open_settle_secs') or 0), cache)
                rows, inv = _book_fine(path, f, m['premium'], ex, cut, qty, collected, brokerage_per_leg)
                out.update(zip([head + e for e in exits], [rows[i] for i in inv]))
                continue
            pos, why = first_exits(path, m['premium'], ex, cut)
            # Most combos share a (bar, reason, level) — each distinct one is booked once.
            level = np.select([why == 0, why == 1, why == 2], [ex['tp'], ex['lt'], ex['sp']], -1.0)
            keys, inv = np.unique(np.stack([pos, why, level], axis=1), axis=0, return_inverse=True)
            rows = []
            for kpos, kwhy, klevel in keys:
                kpos, reason = int(kpos), _REASONS[int(kwhy)]
                tp = sp = lt = klevel
                pts, _, why_s = exit_points(path, kpos, reason, m['premium'], tp, sp, lt)
                n_legs = legs_traded(path, kpos) if kpos >= 0 else len(path['legs'])
                net = pts * qty - brokerage_per_leg * n_legs
                rows.append(('open' if why_s.startswith('Open') else 'traded', net,
                             100.0 * net / collected if collected else 0.0, why_s))
            out.update(zip([head + e for e in exits], [rows[i] for i in inv.ravel()]))
    return out


def score_month(entries: Dict[str, date], sessions: Sequence[date], spot5: pd.Series,
                frame_for: Callable[[date, int, str], Optional[pd.DataFrame]],
                qty: int, base: Dict, brokerage_per_leg: float, **sweep) -> Dict[tuple, tuple]:
    """Every SWEEP_KEYS combo on ONE period (month, or week under base's
    cadence) — the key starts with the cadence; `entries` from entries_by_month,
    `sweep` score_expiry's grid / variants / wings / locks."""
    out: Dict[tuple, tuple] = {}
    cadence = base.get('cadence', 'monthly')
    for rule, entry in entries.items():
        p = {**base, 'entry_day': rule, 'skip_one_night': False}
        res = score_expiry(entry, sessions, spot5, frame_for, qty, p, brokerage_per_leg, **sweep)
        out.update({(cadence, rule, False) + combo: r for combo, r in res.items()})
        if cadence != 'weekly':
            continue
        # Skip one-night: the same rows, except a week whose expiry is the next
        # session is not traded at all — no second path, no extra contract.
        exp = weekly_expiry(entry, sessions, base.get('listed_expiries', ()))
        if exp is not None and _sessions_to(entry, exp, sessions) <= 1 and entry >= WEEKLY_SINCE:
            gone = ('skipped', 0.0, 0.0, 'Skipped: expiry is the next session (one-night straddle)')
            out.update({(cadence, rule, True) + combo: gone for combo in res})
        else:
            out.update({(cadence, rule, True) + combo: r for combo, r in res.items()})
    return out


class Fold:
    """call_ratio_engine.rank's scoring, one month at a time (oldest first), so
    a sweep of a few hundred thousand combos over years never holds every
    month's results at once."""

    def __init__(self):
        self.acc: Dict[tuple, list] = {}

    def add(self, month: Dict[tuple, tuple]) -> None:
        acc = self.acc
        for combo, res in month.items():
            status, net, pct, reason = res[:4]
            a = acc.get(combo)
            if a is None:
                # n, wins, losses, net, pct, worst, won, lost, eq, peak, dd, skipped, open, exits, bars on closes
                a = acc[combo] = [0, 0, 0, 0.0, 0.0, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, {}, 0]
            if len(res) > 4:
                a[14] += res[4]
            if status == 'skipped':
                a[11] += 1
                continue
            if status == 'open':
                a[12] += 1
                continue
            a[0] += 1
            a[3] += net
            a[4] += pct
            a[5] = pct if a[5] is None else min(a[5], pct)
            if net > 0:
                a[1] += 1
                a[6] += net
            elif net < 0:
                a[2] += 1
                a[7] -= net
            a[8] += net
            a[9] = max(a[9], a[8])
            a[10] = max(a[10], a[9] - a[8])
            a[13][reason] = a[13].get(reason, 0) + 1

    def rows(self, min_trades=1) -> List[Dict]:
        """One row per combo, best net ₹ first; `net_dd` = net ₹ ÷ max drawdown ₹.
        `min_trades` an int, or {cadence: int} — a month and a week are
        different periods, so each cadence needs its own half."""
        out = []
        for combo, a in self.acc.items():
            n = a[0]
            need = min_trades.get(combo[0], 1) if isinstance(min_trades, dict) else min_trades
            if n < max(1, need):
                continue
            out.append({
                **dict(zip(SWEEP_KEYS, combo)),
                'total_trades': n, 'skipped': a[11], 'open': a[12],
                'win_rate': round(100.0 * a[1] / n, 1),
                'net_pnl_rupees': round(a[3], 2),
                'total_pct': round(a[4], 2), 'avg_pct': round(a[4] / n, 2),
                'worst_pct': round(a[5], 2),
                'profit_factor': round(a[6] / a[7], 2) if a[7] else (999.0 if a[6] else 0.0),
                'max_drawdown_rupees': round(a[10], 2),
                'net_dd': round(a[3] / a[10], 2) if a[10] else (999.0 if a[3] > 0 else 0.0),
                'exits': a[13],
                'bars_unchecked': a[14], 'complete': a[14] == 0,
            })
        out.sort(key=lambda r: (r['net_pnl_rupees'], -r['max_drawdown_rupees']), reverse=True)
        return out


def rank(per_expiry: List[Dict[tuple, tuple]], min_trades: int = 1) -> List[Dict]:
    """One row per combo, best net ₹ first (oldest month first in `per_expiry`)."""
    f = Fold()
    for res in per_expiry:
        f.add(res)
    return f.rows(min_trades)


def board(rows: List[Dict], n: int = 100) -> List[Dict]:
    """What Find Best Params shows: the top `n` by net ₹ and the top `n` by
    net ÷ drawdown among combos earning at least a quarter of the best net —
    a big net with a deep drawdown and a smaller, much steadier one both make
    the list. Best net first."""
    if not rows:
        return []
    floor = 0.25 * rows[0]['net_pnl_rupees']
    steady = sorted((r for r in rows if r['net_pnl_rupees'] >= floor and r['net_pnl_rupees'] > 0),
                    key=lambda r: r['net_dd'], reverse=True)[:n]
    seen, out = set(), []
    for r in rows[:n] + steady:
        key = tuple(r.get(k) for k in SWEEP_KEYS)
        if key not in seen:
            seen.add(key)
            out.append(r)
    out.sort(key=lambda r: (r['net_pnl_rupees'], -r['max_drawdown_rupees']), reverse=True)
    return out


# ── Re-checking the board every second ───────────────────────────────────────
# Exit Check as a swept setting: each its own full sweep on its own chart, 5 minutes
# first (nothing to fetch), then 1 minute, whose data 3 minutes re-uses.
SWEEP_CHECKS = ('5m', '1m', '3m')


def rank_board(rows: List[Dict]) -> List[Dict]:
    """The board's order: rows with all their data by net, then the part-read (≈),
    then the 5-minute rows. Those last go last whatever they earn: closes cannot see
    a stop touched inside a bar, so on their own net they would lead the board for
    hiding losses, not for trading better. `board_order` carries the order to the
    page (higher = earlier)."""
    fine = [r for r in rows if r.get('exit_check') != '5m']
    by_net = lambda r: (r['net_pnl_rupees'], -r['max_drawdown_rupees'])     # noqa: E731
    out = (sorted((r for r in fine if r.get('complete')), key=by_net, reverse=True)
           + sorted((r for r in fine if not r.get('complete')), key=by_net, reverse=True)
           + sorted((r for r in rows if r.get('exit_check') == '5m'), key=by_net, reverse=True))
    for i, r in enumerate(out):
        r['board_order'] = len(out) - i
    return out
