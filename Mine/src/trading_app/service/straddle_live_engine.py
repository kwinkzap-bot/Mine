"""The ATM straddle backtest's rule, one decision at a time — pure, no broker.

``Backtest/option_selling_engine.py`` owns the rule. This module drives the same
pieces live: the entry calendar (``entry_days``, ``weekly_expiry``,
``contract_expiry``), the adjustment (``adjust_action``), the ATM and the lot
size. ``algo/straddle/straddle_algo.py`` is the thread around it: the clock, the
quotes, the orders and the record.

What can only differ from the backtest because it is live:

* **Target / stop / lock are read on every poll** (the thread's 5 s), the live form
  of the backtest's fill "at the level, like a resting order on the P&L". The
  exit is a market order, so it fills near the level rather than on it, and a
  spike between two 5-minute closes can trip it where the backtest's closes would
  not see it.
* **A "bar close" is the first poll after a 5-minute boundary**: the lock arms and
  an adjustment fires there, on NIFTY's last price and the legs' last prices.
* **MTM is on the quotes the decision saw**, one number for every account; each
  account's own P&L is its own fills.
* **The contract is closed at 15:25 on its expiry day** (``EXPIRY_EXIT``), not on the
  15:30 settlement the backtest books: a short left to settle cannot be acted on.
* **One position at a time.** A monthly trade still open on the next entry day is
  not doubled, where the backtest would hold both. Weekly never overlaps.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

from trading_app.app.utils.trading_calendar import is_trading_day
from trading_app.Backtest import call_ratio_engine as cre
from trading_app.Backtest import option_selling_engine as ose

SYMBOL = 'NIFTY'
EXPIRY_EXIT = (15, 25)
ENTRY_WINDOW_MINS = 10                # past this after the entry time a day's entry is missed, not chased
MIN_DAYS_TO_MONTHLY = ose.DEFAULTS['min_days_to_expiry']
BROKERAGE_PER_LEG = 100.0             # the backtest's _OS_BROKERAGE_PER_LEG, so the two books compare


# ── the calendar ─────────────────────────────────────────────────────────────

def known_sessions(today: date, back: int = 70, ahead: int = 50) -> List[date]:
    """Sessions around today, the holiday list deciding the ones still to come."""
    days = (today + timedelta(days=i) for i in range(-back, ahead + 1))
    return [d for d in days if is_trading_day(d)]


def is_entry_day(today: date, rule: str, cadence: str) -> bool:
    """The backtest's entry_days asked about one date. Sessions still to come are
    in the list, so a holiday tomorrow moves its entry to today as it does there."""
    span = timedelta(days=40)       # the whole week / month is what picks its session
    return today in ose.entry_days(known_sessions(today), today - span, today + span, rule, cadence)


def expiry_for(entry: date, cadence: str, listed: Sequence[date] = ()) -> Optional[date]:
    """The contract an entry sells. The holiday list absorbs a holiday still to
    come (Tue 2026-11-10 is one: a Monday-the-9th entry sells the 17th, not a
    contract expiring that same afternoon), and the exchange's own dates have the
    last word: an expiry that is not listed moves to the listed one in its week
    after the entry, or None — refused, never guessed."""
    sessions = known_sessions(entry)
    exp = (ose.weekly_expiry(entry, sessions) if cadence == 'weekly'
           else cre.contract_expiry(entry, sessions, MIN_DAYS_TO_MONTHLY))
    if exp is None or not listed or exp in listed:
        return exp
    week = [d for d in listed if exp - timedelta(days=6) <= d <= exp and d > entry]
    return max(week) if week else None


def is_one_night(entry: date, expiry: date) -> bool:
    return ose._sessions_to(entry, expiry, known_sessions(entry)) <= 1


# ── the position ─────────────────────────────────────────────────────────────

def make_leg(right: str, strike: int, side: int, entry: float, tsym: str, opened_at: str) -> Dict[str, Any]:
    """side −1 = sold, +1 = bought. `exit` / `closed_at` are set when it closes."""
    return {'right': right, 'strike': int(strike), 'side': int(side), 'entry': float(entry),
            'tsym': tsym, 'opened_at': opened_at, 'closed_at': None, 'exit': None}


def new_position(pid: str, mode: str, params: Dict[str, Any], entry_date: date, expiry: date, spot: float,
                 strike: int, legs: List[Dict[str, Any]], opened_at: str, lot_size: int) -> Dict[str, Any]:
    sold = sum(lg['entry'] for lg in legs if lg['side'] < 0)
    bought = sum(lg['entry'] for lg in legs if lg['side'] > 0)
    return {
        'id': pid, 'mode': mode, 'params': dict(params),
        'entry_date': entry_date.isoformat(), 'expiry': expiry.isoformat(),
        'opened_at': opened_at, 'spot': float(spot), 'strike': int(strike), 'centre': int(strike),
        'lot_size': int(lot_size), 'legs': legs,
        'sold_premium': round(sold, 2), 'premium': round(sold - bought, 2),      # % are of the net credit
        'hedged': [], 'adjusts': [], 'lock_armed': False, 'last_bar': None,
        'accounts': {}, 'exit': None,
    }


def open_legs(pos: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [lg for lg in pos['legs'] if lg['closed_at'] is None]


def mtm(pos: Dict[str, Any], marks: Dict[str, float]) -> Optional[float]:
    """Points per unit: closed legs at their exit, open legs at `marks`. None
    when an open leg has no quote — nothing may be decided on half a position."""
    total = 0.0
    for lg in pos['legs']:
        px = lg['exit'] if lg['closed_at'] is not None else marks.get(lg['tsym'])
        if px is None:
            return None
        total += lg['side'] * (float(px) - lg['entry'])
    return total


def lock_applies(p: Dict[str, Any]) -> bool:
    return p['lock_at'] > 0 and p['lock_to'] < p['lock_at'] and p['lock_to'] > -p['stop_pct']


def exit_reason(pos: Dict[str, Any], m: float) -> Optional[str]:
    """First_exit's order on one reading: target before lock before stop."""
    p, prem = pos['params'], pos['premium']
    if m >= prem * p['target_pct'] / 100.0:
        return 'Target'
    if pos['lock_armed'] and lock_applies(p) and m <= prem * p['lock_to'] / 100.0:
        return 'Profit lock'
    if m <= -prem * p['stop_pct'] / 100.0:
        return 'Stop loss'
    return None


def bar_key(hour: int, minute: int) -> str:
    """The 5-minute boundary a moment belongs to, 'HH:MM'."""
    return f'{hour:02d}:{minute - minute % 5:02d}'


def on_bar(pos: Dict[str, Any], spot: float, m: float, today: date) -> Optional[Dict[str, Any]]:
    """One 5-minute close: arms the lock, and returns the adjustment it fires as
    {'up', 'atm', 'close': [leg], 'opens': [(right, side)]} — or None. Nothing is
    changed for an adjustment until apply_adjust; a new strike with no quote waits
    for the next bar, as the backtest does."""
    p = pos['params']
    if lock_applies(p) and not pos['lock_armed'] and m >= pos['premium'] * p['lock_at'] / 100.0:
        pos['lock_armed'] = True
    if (p['adjust'] == 'none' or p['adjust_pts'] <= 0 or len(pos['adjusts']) >= p['max_adjust']
            or today.isoformat() == pos['expiry'] or abs(spot - pos['centre']) < p['adjust_pts']):
        return None
    up, atm = spot > pos['centre'], ose.atm_strike(spot)
    held = [lg for lg in open_legs(pos) if lg['side'] < 0]
    close, opens = ose.adjust_action(p['adjust'], up, atm, held, set(pos['hedged']))
    if not opens:
        pos['centre'] = atm                    # nothing to do at this ATM; watch from it
        return None
    return {'up': up, 'atm': atm, 'close': close, 'opens': opens, 'spot': float(spot)}


def apply_adjust(pos: Dict[str, Any], plan: Dict[str, Any], marks: Dict[str, float],
                 tsym_of: Callable[[str, int], str], at: str) -> List[Dict[str, Any]]:
    """Move the decision's books through an adjustment; returns the new legs."""
    closed = []
    for lg in plan['close']:
        lg.update(exit=float(marks[lg['tsym']]), closed_at=at)
        closed.append([lg['right'], lg['strike']])
    added = []
    for right, side in plan['opens']:
        ts = tsym_of(right, plan['atm'])
        added.append(make_leg(right, plan['atm'], side, float(marks[ts]), ts, at))
        if side > 0:
            pos['hedged'].append(right)
    pos['legs'].extend(added)
    pos['adjusts'].append({'at': at, 'spot': plan['spot'], 'strike': plan['atm'], 'closed': closed,
                           'opened': [[lg['right'], lg['strike'], lg['side'], lg['entry']] for lg in added]})
    pos['centre'] = plan['atm']
    return added


def close_all(pos: Dict[str, Any], marks: Dict[str, float], at: str) -> None:
    for lg in open_legs(pos):
        lg.update(exit=marks.get(lg['tsym'], lg['entry']), closed_at=at)


# ── sizing ───────────────────────────────────────────────────────────────────

def lots_for(compounding: bool, fixed_lots: Optional[int], start_capital: float, booked: float,
             per_lot: float) -> int:
    if compounding:
        return ose.lots_for_capital(start_capital + booked, per_lot)
    return int(fixed_lots or 0)
