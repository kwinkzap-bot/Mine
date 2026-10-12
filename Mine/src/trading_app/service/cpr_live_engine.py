"""The CPR rule, run forward one 5-minute candle at a time — the live algo's brain.

The sheet on /cpr-logic is the rule replayed over whole sessions. A live
algo has only the candles that have closed, so this module is that same rule
(``cpr_trade_rule``) driven candle by candle, with every decision FINAL once
made — the order that went out cannot be un-sent because a later candle would
have changed the rule's mind. Replayed this way over 2018-26 it earns what the
sheet does: 595 first trades, 55.0 % won, +10,647 index pts, against the
sheet's 589 / 54.5 % / +10,618.

Nothing here touches a broker, a clock or a file. The algo thread
(``algo/cpr_logic/cpr_algo.py``) owns the loop and the orders; it calls

    on_bar(day, chart, bars)     after each 5-minute close, bars = closed candles
    on_price(day, ltp, hhmm)     on every price poll in between

and acts on the events they return:

    {'event': 'armed',  'trade': t}     a setup: watch for its entry
    {'event': 'fill',   'trade': t}     the index crossed the entry — buy now
    {'event': 'exit',   'trade': t}     stopped / target / squared off — sell now
    {'event': 'locked', 'trade': t}     the target closed through: stop is now the target
    {'event': 'expired','trade': t}     never reached its entry by 15:15

A day's state is a plain JSON-able dict (``new_day``), so the algo saves it
after every event and a restart picks up exactly where it was.

Exits are the sheet's (``cpr_backtest_service.simulate_trade``):

* the stop is watched on every price — a touch is out at the stop;
* the target is decided on the 5-minute CLOSE: closed through it, the stop
  moves up to the target and the trade rides (out on the next touch of the
  target, or at 15:15); tagged and back, out at that close. A trade carrying
  an ``extend`` whose target candle breaks strongly through the CPR swaps to
  the extended target instead (``cpr_trade_rule.simulate``);
* still open at 15:15 — out at the price then.
"""

from typing import Any, Callable, Dict, List, Optional

from trading_app.service import cpr_backtest_service as svc
from trading_app.service import cpr_trade_rule as rule

SQUARE_OFF = svc.SQUARE_OFF             # '15:15'


def new_day(date_str: str) -> Dict[str, Any]:
    return {'date': date_str, 'trades': [], 'first_done': False, 'done': False, 'notes': []}


def _ev(kind: str, t: Dict[str, Any], **kw) -> Dict[str, Any]:
    return dict({'event': kind, 'trade': t}, **kw)


def _is_buy(t: Dict[str, Any]) -> bool:
    return t['trade'] == 'BUY'


def _pts(t: Dict[str, Any], exit_px: float) -> float:
    return round((exit_px - t['fill']) if _is_buy(t) else (t['fill'] - exit_px), 2)


def _arm(day, p, why, setup_i, bars, kind) -> Dict[str, Any]:
    """A setup becomes a trade waiting for its entry. ``p`` is the trade AS
    PLANNED — a target the replay already extended in hindsight is put back to
    the planned one; the extension is earned live on the target candle."""
    target = p.get('planned_target', p['target'])
    t = {
        'n': len(day['trades']) + 1, 'kind': kind,
        'trade': p['trade'], 'entry': float(p['entry']), 'target': float(target), 'sl': float(p['sl']),
        'extend': p.get('extend'), 'why': why, 'strategy': svc.strategy_of(why),
        'setup_time': bars[setup_i]['time'], 'setup_i': setup_i,
        'armed_after': bars[-1]['time'],          # fills only from the candle after this one
        'status': 'armed', 'stop': float(p['sl']), 'locked': False,
        'fill': None, 'fill_time': None, 'exit': None, 'exit_time': None, 'exit_bar': None,
        'result': None, 'pts': None,
    }
    day['trades'].append(t)
    return t


def _first_setup(chart, bars):
    """The rule's first trade on the candles so far, as (p, why, setup_i) —
    the trade the sheet would hold for this day if it ended now. A first
    refused for the VWAP but followed by the late virgin-CPR rejection is
    that rejection (it IS the day's first order)."""
    for k, (p, why, i, _sim) in enumerate(rule.propose_all(chart, bars)):
        if p is not None and i is not None and i <= len(bars) - 1:
            return p, why, i, k > 0
    return None


def on_bar(day: Dict[str, Any], chart: Dict[str, Any], bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One 5-minute candle has closed: ``bars`` is every closed candle of the
    session, 09:15 first. Settles the target on the close, squares off at
    15:15, and asks the rule for a setup. Returns the events, in order."""
    events: List[Dict[str, Any]] = []
    if day.get('done') or not bars:
        return events
    b = bars[-1]

    for t in day['trades']:
        if t['status'] == 'armed' and b['time'] > t['armed_after'] and b['time'] < SQUARE_OFF:
            # A candle that crossed the entry while the price polls missed it
            # (a restart, a slow quote) still fills — at the entry, or at the
            # candle's open when it opened past it, as a stop order would.
            crossed = (b['high'] >= t['entry']) if _is_buy(t) else (b['low'] <= t['entry'])
            if crossed:
                px = max(t['entry'], b['open']) if _is_buy(t) else min(t['entry'], b['open'])
                events += _fill(t, px, b['time'])
        if t['status'] == 'open':
            events += _settle_close(t, b)

    if b['time'] >= SQUARE_OFF:
        events += square_off(day, b['open'], b['time'])
        return events

    events += _decide(day, chart, bars)
    return events


def _fill(t, px, hhmm, src='bar') -> List[Dict[str, Any]]:
    t.update(status='open', fill=float(px), fill_time=hhmm, fill_src=src)
    return [_ev('fill', t)]


def _exit(t, px, hhmm, result) -> List[Dict[str, Any]]:
    t.update(status='closed', exit=float(px), exit_time=hhmm, exit_bar=_bar_of(hhmm),
             result=result, pts=_pts(t, px))
    return [_ev('exit', t)]


def _bar_of(hhmm: str) -> str:
    """The 5-minute candle a moment belongs to: 10:37 -> 10:35."""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    m -= m % 5
    return f'{h:02d}:{m:02d}'


def _settle_close(t, b) -> List[Dict[str, Any]]:
    """The candle's close against an open trade's target (the stop is the
    price polls' job, and was checked on this candle's extremes by
    ``on_price`` or the caller's catch-up)."""
    buy = _is_buy(t)
    # The stop first, on the candle's extreme: polls can miss a wick, a
    # candle cannot. The fill candle that OPENED past the stop has not hit it
    # (simulate_trade's rule: its far extreme came before the entry).
    fill_bar = bool(t['fill_time']) and _bar_of(t['fill_time']) == b['time']
    hit_s = (b['low'] <= t['stop']) if buy else (b['high'] >= t['stop'])
    if fill_bar and (t.get('fill_src') == 'tick'
                     or ((b['open'] <= t['stop']) if buy else (b['open'] >= t['stop']))):
        # Filled inside this candle: its extreme may have come before the fill.
        # A price-poll fill leaves the stop to the polls for this candle; a
        # candle that opened past the stop had its far end before the entry.
        hit_s = False
    if hit_s:
        if t['locked']:                           # riding: out at the target, or the open past it
            px = min(t['stop'], b['open']) if buy else max(t['stop'], b['open'])
            return _exit(t, px, b['time'], 'Target')
        return _exit(t, t['stop'], b['time'], 'SL')
    if t['locked']:
        return []
    hit_t = (b['high'] >= t['target']) if buy else (b['low'] <= t['target'])
    if not hit_t:
        return []
    ext = t.get('extend')
    if ext and not t.get('extended'):
        rng = b['high'] - b['low']
        broke = (rng > 0 and abs(b['close'] - b['open']) / rng >= rule.STRONG_BODY
                 and ((b['close'] > ext['through']) if buy else (b['close'] < ext['through'])))
        if broke:
            t.update(target=float(ext['level']), extended=True)
            return [_ev('extended', t)]
    if t.get('extended'):
        # The extended run has no lock (cpr_trade_rule.simulate): out at it.
        return _exit(t, t['target'], b['time'], 'Target')
    if (b['close'] > t['target']) if buy else (b['close'] < t['target']):
        t.update(locked=True, stop=t['target'], locked_at=b['time'])
        return [_ev('locked', t)]
    return _exit(t, b['close'], b['time'], 'Target')


def on_price(day: Dict[str, Any], ltp: Optional[float], hhmm: str) -> List[Dict[str, Any]]:
    """A price poll between closes: fills an armed trade whose entry the index
    has crossed, and stops out an open one whose stop it has touched. The
    target is not decided here — that is the 5-minute close's job."""
    events: List[Dict[str, Any]] = []
    if ltp is None or day.get('done'):
        return events
    if hhmm >= SQUARE_OFF:
        return square_off(day, ltp, hhmm)
    for t in day['trades']:
        buy = _is_buy(t)
        if t['status'] == 'armed' and _bar_of(hhmm) > t['armed_after']:
            if (ltp >= t['entry']) if buy else (ltp <= t['entry']):
                events += _fill(t, max(t['entry'], ltp) if buy else min(t['entry'], ltp), hhmm, 'tick')
        elif t['status'] == 'open':
            if (ltp <= t['stop']) if buy else (ltp >= t['stop']):
                events += _exit(t, ltp, hhmm, 'Target' if t['locked'] else 'SL')
    return events


def square_off(day: Dict[str, Any], px: float, hhmm: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for t in day['trades']:
        if t['status'] == 'open':
            events += _exit(t, px, hhmm, 'Target' if t['locked'] else 'EOD')
        elif t['status'] == 'armed':
            t['status'] = 'expired'
            events.append(_ev('expired', t))
    day['done'] = True
    return events


def _decide(day, chart, bars) -> List[Dict[str, Any]]:
    """Ask the rule for the day's next trade, if it is owed one."""
    trades = day['trades']
    if not day['first_done']:
        found = _first_setup(chart, bars)
        if not found:
            return []
        p, why, i, late = found
        day['first_done'] = True
        t = _arm(day, p, why, i, bars, 'first')
        if late:
            t['second_done'] = True              # the late rejection after a refusal is the day's last
        return [_ev('armed', t)]
    if len(trades) != 1 or rule.MAX_TRADES_PER_DAY < 2:
        return []
    first = trades[0]
    if first['status'] != 'closed' or first['result'] not in ('SL', 'Target') or first.get('second_done'):
        return []
    # The first trade's REAL end, in the shape second_trade reads: its result
    # and the candle it happened in.
    sim = {'result': first['result'], 'exit_time': first['exit_bar'], 'fill': first['fill']}
    used = {k: first[k] for k in ('trade', 'entry', 'target', 'sl')}
    nxt = rule.second_trade(chart, bars, sim, used, first['setup_i'])
    if not nxt:
        if bars[-1]['time'] >= rule.LATE_SETUP_UNTIL:
            first['second_done'] = True          # no second trade can start this late
        return []
    p2, why2, i2 = nxt
    first['second_done'] = True
    return [_ev('armed', _arm(day, rule.tune_exit(p2, why2), why2, i2, bars, 'second'))]


# ── the option leg ─────────────────────────────────────────────────────────

def option_side(t: Dict[str, Any]) -> str:
    """A BUY on the index is a CE bought; a SELL is a PE bought."""
    return 'CE' if _is_buy(t) else 'PE'


def pick_strike(quote: Callable[[int], Optional[float]], spot: float, step: int,
                option_type: str, premium: float = 200.0, max_steps: int = 12) -> Optional[Dict[str, Any]]:
    """The strike whose premium is nearest ``premium`` — the sheet's ≈200
    leg (cpr_option_service.pick_strike), walked live from the money:
    out-of-the-money cheapens the premium, in-the-money raises it, and the
    walk stops once two neighbours bracket the target. ``quote(strike)`` is
    the live premium or None. Returns {'strike', 'premium'} or None."""
    atm = int(round(spot / step) * step)
    seen: Dict[int, float] = {}

    def px(k):
        if k not in seen:
            v = quote(k)
            if v:
                seen[k] = float(v)
        return seen.get(k)

    p0 = px(atm)
    if p0 is None:
        return None
    # Towards cheaper when the ATM premium is over the target: CE strikes up, PE strikes down.
    cheaper = step if option_type == 'CE' else -step
    direction = cheaper if p0 > premium else -cheaper
    k = atm
    for _ in range(max_steps):
        nk = k + direction
        pn = px(nk)
        if pn is None:
            break
        if (pn - premium) * (seen[k] - premium) <= 0:
            k = nk
            break
        k = nk
    best = min(seen, key=lambda s: (abs(seen[s] - premium), abs(s - atm)))
    return {'strike': best, 'premium': seen[best]}


# ── sizing ─────────────────────────────────────────────────────────────────

def compound_lots(capital: float, booked_pnl: float, per_lot: float = 20000.0,
                  max_lots: int = 27) -> int:
    """The Performance popup's Compound lots, for the next trade: one lot per
    ``per_lot`` of (starting capital + everything booked so far), never under
    one and never over ``max_lots``."""
    eq = float(capital) + float(booked_pnl or 0)
    return int(min(max_lots, max(1, eq // per_lot)))
