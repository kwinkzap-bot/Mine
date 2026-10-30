"""ST Touch Break — the decisions of the live algo, with no I/O.

The rule is the Backtest page's (``Backtest/st_touch_break_engine.StTouchBreakEngine``): the touch
candles and the SuperTrend line come from the SAME ``indicators()`` / ``touches()`` the backtest
simulates with, so the two cannot drift. What lives here is what a backtest does not have to think
about — one closed candle at a time, a price poll in between, and a clock:

* a TOUCH candle that has just closed ARMS the setup: a buy above its high (a short below its low,
  with the direction on Both). Nothing is ordered yet;
* the setup lives for the NEXT candle only. The moment the index trades through the level inside it
  the order goes out — the backtest's "fills at the break level" — and if that candle closes
  without the level being crossed the setup is dropped (``expired``);
* an open trade ends when a later candle CLOSES with the SuperTrend against it (``stop_mode``
  ``close``), or — with ``stop_mode`` ``touch`` — as soon as the index trades through the line of
  the candle before, or at the square-off minute. There is no target. The touch is watched on a
  5 s price poll, which can miss a spike that reverses between two reads; the closed candle's
  low (high for a short) is the net for it — through the line, the trade is out at that close
  (since 2026-10-30), so live takes every exit the backtest takes, if a few seconds later;
* at most ``max_trades`` a day, one at a time.

**Bounce and Through** (``modes``, a row's fixed setting; Through since 2026-10-30). A Bounce breaks
AWAY from the line (an uptrend touch, then the touch high); a Through breaks THROUGH it (an uptrend
touch, then the touch LOW — a short against the trend; mirror for a downtrend). The backtest does not
need the SuperTrend to flip for a Through: it fills at the level and, if the break candle's close
leaves the trend still against the trade, exits at that close (``SuperTrend flip``) — which is what
step 2 of ``on_bar`` does here too. Only one side is armed per touch: every Mine row is one-sided
(BUY only / SELL only) or Bounce only, so that is always the one the backtest can take. With
``modes='both'`` and the direction on Both the bounce side is armed.

**Confirmed at the close** (since 2026-10-28, the "NIFTY - Both - 3m" tab). ``min_body_pct`` (the
break candle's body share, closing the way it broke) and ``outside_bar='close'`` (an outside bar
goes with the side it closes on) both read the break candle's CLOSE, so with either one on the
order cannot go out intrabar: the setup is judged when the break candle closes and, if it broke
the level, closed the way it broke with the body asked for and the SuperTrend still with it, the
order goes out THEN — at that close, not at the level. Since 2026-10-30 the backtest fills these
at the same close (it used to fill at the level with the close already known, a look-ahead the live
tab could never earn). With both off (``min_body_pct`` 0, ``outside_bar`` 'first')
the intrabar entry above is unchanged.

Everything takes plain dicts and a DataFrame of CLOSED candles, so it is tested without a broker.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trading_app.Backtest.st_touch_break_engine import StTouchBreakEngine

# An order is not sent if the index is already this far past the level, in units of the setup's
# risk (level to the SuperTrend line): the trade the backtest takes is AT the level, and chasing a
# run is a different — worse — trade. A gap open is a first-poll event and stays inside it.
MAX_CHASE_RISK_FRAC = 0.5


def new_day(date_str: str) -> Dict[str, Any]:
    return {'date': date_str, 'trades': [], 'last_bar': None}


def option_type(t: Dict[str, Any]) -> str:
    """A long setup buys a CALL, a short setup buys a PUT."""
    return 'CE' if t['type'] == 'Long' else 'PE'


def _hm(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _r(v, nd=2):
    try:
        v = float(v)
        return round(v, nd) if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def engine_for(bars: pd.DataFrame, s: Dict[str, Any], bar_minutes: int) -> StTouchBreakEngine:
    return StTouchBreakEngine(
        bars, st_period=int(s.get('st_period', 10)), st_mult=float(s.get('st_mult', 2)),
        touch_tol_pct=float(s.get('touch_tol_pct', 0)), min_wick_pct=float(s.get('min_wick_pct', 0)),
        min_body_pct=float(s.get('min_body_pct', 0)), outside_bar=s.get('outside_bar', 'first'),
        modes=s.get('modes', 'bounce'), direction=s.get('direction', 'long'),
        stop_mode='touch' if s.get('stop_touch') else 'close',
        entry_start=s.get('entry_start', '09:15'), entry_end=s.get('entry_end', '15:14'),
        exit_cutoff=s.get('exit_cutoff', '15:14'), max_trades_per_day=int(s.get('max_trades', 5)),
        bar_minutes=bar_minutes)


def confirm_at_close(s: Dict[str, Any]) -> bool:
    """Is the break judged at the break candle's close (see the module note) rather than intrabar."""
    return float(s.get('min_body_pct') or 0) > 0 or s.get('outside_bar', 'first') == 'close'


def _confirm(t: Dict[str, Any], eng: StTouchBreakEngine, df: pd.DataFrame, i: int, trend) -> Optional[str]:
    """The break candle `i` has closed: None if it is the trade, else why not. The backtest's own
    tests, in its order — which side broke (an outside bar goes the way it closed), then the body."""
    long = t['type'] == 'Long'
    o, h, l, c = (float(df[k].iloc[i]) for k in ('open', 'high', 'low', 'close'))
    th, tl = t['signal']['touch_high'], t['signal']['touch_low']
    up, dn = h > th, l < tl
    if up and dn:
        if eng.outside_bar == 'close' and c != o:
            up = c > o
        else:                                   # no minutes here: the backtest's tie-break, nearer level first
            up = abs(th - o) <= abs(o - tl)
        dn = not up
    if not (up if long else dn):
        return 'the break candle did not break the ' + (f"{th:,.1f} high" if long else f"{tl:,.1f} low") \
            + (' (an outside bar, closed the other way)' if (h > th and l < tl) else '')
    rng, body = h - l, abs(c - o)
    if eng.min_body and (rng <= 0 or body / rng < eng.min_body or (c > o) != long):
        pct = body / rng * 100 if rng > 0 else 0
        return f"the break candle's body was {pct:.0f}% of its range{'' if (c > o) == long else ', closing the other way'}" \
               f" — {eng.min_body * 100:.0f}% asked"
    if int(trend[i]) == (-1 if long else 1):
        return 'the SuperTrend turned against it on the break candle'
    return None


def _taken(day: Dict[str, Any]) -> int:
    """Trades that reached an order (or tried to) — what ``max_trades`` counts."""
    return sum(1 for t in day['trades'] if t['status'] in ('open', 'closed', 'failed'))


def on_bar(day: Dict[str, Any], bars: pd.DataFrame, s: Dict[str, Any], bar_minutes: int = 3) -> List[Dict[str, Any]]:
    """Read the LAST candle of `bars` (every candle in it is closed). Returns events:
    {'event': 'armed'|'expired'|'exit', 'trade': t, ...}. Mutates `day`."""
    if bars is None or len(bars) < int(s.get('st_period', 10)) + 3:
        return []
    eng = engine_for(bars, s, bar_minutes)
    ind = eng.indicators()
    touch = eng.touches(ind)
    line, trend = ind['line'], ind['trend']
    df = eng.df
    i = len(df) - 1
    ts = df.index[i]
    ts_s = ts.strftime('%Y-%m-%d %H:%M:%S')
    if ts_s[:10] != day['date'] or ts_s == day.get('last_bar'):
        return []
    day['last_bar'] = ts_s
    tmin = int(ts.hour * 60 + ts.minute)
    bar_end = tmin + bar_minutes
    close = float(df['close'].iloc[i])
    events: List[Dict[str, Any]] = []

    # 1. the break candle has closed: a setup that never crossed its level is over — or, confirmed
    #    at the close, the setup is judged on that close and entered at it
    for t in day['trades']:
        if t['status'] == 'armed' and t.get('confirm') and tmin == t['break_start']:
            why = _confirm(t, eng, df, i, trend)
            if why is None:
                t.update(status='open', index_cross=round(close, 2))
                events.append({'event': 'entry', 'trade': t, 'bar': ts_s})
            else:
                t.update(status='expired', error=why)
                events.append({'event': 'expired', 'trade': t, 'bar': ts_s})
        elif t['status'] == 'armed' and tmin >= t['break_start']:
            t['status'] = 'expired'
            events.append({'event': 'expired', 'trade': t, 'bar': ts_s})

    # 2. an open trade: its candle traded through the stop line (a wick the 5 s price poll missed), or
    #    closed with the SuperTrend against it
    open_t = next((t for t in day['trades'] if t['status'] == 'open'), None)
    if open_t is not None:
        side = 1 if open_t['type'] == 'Long' else -1
        if tmin >= open_t['entry_bar_start']:
            sl = open_t.get('stop_line')
            hi, lo = float(df['high'].iloc[i]), float(df['low'].iloc[i])
            if (s.get('stop_touch') and sl and tmin >= open_t['entry_bar_start'] + bar_minutes
                    and ((lo <= sl) if side == 1 else (hi >= sl))):
                # the backtest exits on this candle's low/high (`stop_mode='touch'`); a spike shorter than
                # the poll went unseen, so the trade is out now, at market, a few seconds after the close
                events.append({'event': 'exit', 'trade': open_t, 'bar': ts_s, 'index_close': close,
                               'reason': 'SuperTrend stop'})
            elif int(trend[i]) == -side:
                events.append({'event': 'exit', 'trade': open_t, 'bar': ts_s, 'index_close': close,
                               'reason': 'SuperTrend Down' if side == 1 else 'SuperTrend Up'})
            else:
                # the line a 'touch' stop reads on the next candle: this one's, while it is still with the trade
                open_t['stop_line'] = _r(line[i]) if int(trend[i]) == side and np.isfinite(line[i]) else None
        return events

    # 3. a touch candle that has just closed arms the next candle's break
    if any(t['status'] == 'armed' for t in day['trades']) or not touch[i]:
        return events
    up_touch = int(touch[i]) == 1              # the wick is on an UPtrend's line: a bounce breaks up, a through down
    modes = s.get('modes', 'bounce')
    sides = [lg for lg, ok in ((up_touch, modes in ('bounce', 'both')), (not up_touch, modes in ('through', 'both')))
             if ok and not (s.get('direction') == 'long' and not lg) and not (s.get('direction') == 'short' and lg)]
    if not sides:
        return events
    long = sides[0]
    setup = 'Bounce' if long == up_touch else 'Through'
    entry_start = eng.entry_start
    if (_taken(day) >= eng.max_trades_per_day or not entry_start <= bar_end <= eng.entry_end
            or bar_end >= eng.exit_cutoff):
        return events
    h, l = float(df['high'].iloc[i]), float(df['low'].iloc[i])
    level = h if long else l
    # how far past the level is still "at" it: a bounce's risk runs to the line under it; a through's
    # level sits ON the line, so its touch candle's range is the yardstick
    risk = abs(level - line[i]) if setup == 'Bounce' and np.isfinite(line[i]) else h - l
    t = {'n': len(day['trades']) + 1, 'type': 'Long' if long else 'Short', 'status': 'armed', 'setup': setup,
         'risk': _r(risk),
         'signal_bar': ts_s, 'break_start': bar_end, 'entry_bar_start': bar_end, 'level': round(level, 2),
         'line': _r(line[i]), 'confirm': confirm_at_close(s),
         'signal': {'rsi': None, 'close': round(close, 2), 'level': round(level, 2), 'touch_high': round(h, 2),
                    'touch_low': round(l, 2), 'supertrend': _r(line[i])}}
    day['trades'].append(t)
    events.append({'event': 'armed', 'trade': t, 'bar': ts_s})
    return events


def on_price(day: Dict[str, Any], ltp: Optional[float], hhmm: str, s: Dict[str, Any],
             bar_minutes: int = 3) -> List[Dict[str, Any]]:
    """The index price between candles: an armed setup whose level is crossed inside its break
    candle becomes an entry; with the 'touch' stop an open trade ends the moment the index trades
    through the candle-before line. Mutates `day`."""
    if not ltp:
        return []
    now = _hm(hhmm)
    events: List[Dict[str, Any]] = []
    for t in day['trades']:
        long = t['type'] == 'Long'
        if t['status'] == 'armed':
            if t.get('confirm') or not t['break_start'] <= now < t['break_start'] + bar_minutes:
                continue
            crossed = ltp > t['level'] if long else ltp < t['level']
            if not crossed:
                continue
            risk = t['risk'] if t.get('risk') is not None else abs(t['level'] - (t.get('line') or t['level']))
            over = (ltp - t['level']) if long else (t['level'] - ltp)
            if risk > 0 and over > MAX_CHASE_RISK_FRAC * risk:
                t.update(status='missed', error=f"the index was already {over:,.1f} pts past the "
                                                f"{t['level']:,.1f} level when it was read — not chased")
                events.append({'event': 'missed', 'trade': t})
                continue
            t['status'] = 'open'
            t['index_cross'] = round(float(ltp), 2)
            events.append({'event': 'entry', 'trade': t, 'bar': t['signal_bar'], 'stale': False})
        elif t['status'] == 'open' and s.get('stop_touch') and t.get('stop_line'):
            # not on the entry candle itself — the backtest's `k > e`
            if now >= t['entry_bar_start'] + bar_minutes and ((ltp <= t['stop_line']) if long else (ltp >= t['stop_line'])):
                events.append({'event': 'exit', 'trade': t, 'bar': None, 'index_close': float(ltp),
                               'reason': 'SuperTrend stop'})
    return events


def watching(day: Dict[str, Any], hhmm: str, s: Dict[str, Any], bar_minutes: int = 3) -> bool:
    """Is a price poll worth its request right now: a setup whose break candle is running, or an
    open trade under a 'touch' stop."""
    now = _hm(hhmm)
    for t in day['trades']:
        if t['status'] == 'armed' and not t.get('confirm') and t['break_start'] <= now < t['break_start'] + bar_minutes:
            return True
        if t['status'] == 'open' and s.get('stop_touch') and t.get('stop_line'):
            return True
    return False


__all__ = ['new_day', 'option_type', 'on_bar', 'on_price', 'watching', 'engine_for', 'confirm_at_close',
           'MAX_CHASE_RISK_FRAC']
