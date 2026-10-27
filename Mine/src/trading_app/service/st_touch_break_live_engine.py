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
  the candle before, or at the square-off minute. There is no target;
* at most ``max_trades`` a day, one at a time.

**Bounce only.** A Through setup is known only once the break candle has CLOSED (it is the one the
SuperTrend flips on), by which time the level was crossed minutes ago, so it cannot be traded live
and the backtest's Through rows have no live twin. The same goes for ``min_body_pct`` (it reads the
break candle's close) and ``outside_bar='close'`` (an outside bar goes with the side it closes on):
live takes whichever side the index crosses first, which is the backtest's ``outside_bar='first'``.

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
        modes='bounce', direction=s.get('direction', 'long'),
        stop_mode='touch' if s.get('stop_touch') else 'close',
        entry_start=s.get('entry_start', '09:15'), entry_end=s.get('entry_end', '15:14'),
        exit_cutoff=s.get('exit_cutoff', '15:14'), max_trades_per_day=int(s.get('max_trades', 5)),
        bar_minutes=bar_minutes)


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

    # 1. the break candle has closed: a setup that never crossed its level is over
    for t in day['trades']:
        if t['status'] == 'armed' and tmin >= t['break_start']:
            t['status'] = 'expired'
            events.append({'event': 'expired', 'trade': t, 'bar': ts_s})

    # 2. an open trade: its candle closed with the SuperTrend against it
    open_t = next((t for t in day['trades'] if t['status'] == 'open'), None)
    if open_t is not None:
        side = 1 if open_t['type'] == 'Long' else -1
        if tmin >= open_t['entry_bar_start']:
            if int(trend[i]) == -side:
                events.append({'event': 'exit', 'trade': open_t, 'bar': ts_s, 'index_close': close,
                               'reason': 'SuperTrend Down' if side == 1 else 'SuperTrend Up'})
            else:
                # the line a 'touch' stop reads on the next candle: this one's, while it is still with the trade
                open_t['stop_line'] = _r(line[i]) if int(trend[i]) == side and np.isfinite(line[i]) else None
        return events

    # 3. a touch candle that has just closed arms the next candle's break
    if any(t['status'] == 'armed' for t in day['trades']) or not touch[i]:
        return events
    long = int(touch[i]) == 1                  # the wick is on an UPtrend's line: the break away is up
    if (s.get('direction') == 'long' and not long) or (s.get('direction') == 'short' and long):
        return events
    entry_start = eng.entry_start
    if (_taken(day) >= eng.max_trades_per_day or not entry_start <= bar_end <= eng.entry_end
            or bar_end >= eng.exit_cutoff):
        return events
    h, l = float(df['high'].iloc[i]), float(df['low'].iloc[i])
    level = h if long else l
    t = {'n': len(day['trades']) + 1, 'type': 'Long' if long else 'Short', 'status': 'armed',
         'signal_bar': ts_s, 'break_start': bar_end, 'entry_bar_start': bar_end, 'level': round(level, 2),
         'line': _r(line[i]),
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
            if not t['break_start'] <= now < t['break_start'] + bar_minutes:
                continue
            crossed = ltp > t['level'] if long else ltp < t['level']
            if not crossed:
                continue
            risk = abs(t['level'] - (t.get('line') or t['level']))
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
        if t['status'] == 'armed' and t['break_start'] <= now < t['break_start'] + bar_minutes:
            return True
        if t['status'] == 'open' and s.get('stop_touch') and t.get('stop_line'):
            return True
    return False


__all__ = ['new_day', 'option_type', 'on_bar', 'on_price', 'watching', 'engine_for', 'MAX_CHASE_RISK_FRAC']
