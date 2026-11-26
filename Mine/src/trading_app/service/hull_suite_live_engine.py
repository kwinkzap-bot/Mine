"""Hull Suite — the decisions of the live algo, with no I/O.

The rule is the Backtest page's (``Backtest/hull_suite_engine.HullSuiteEngine``): the signal candles come
from the SAME ``signals()`` the backtest simulates with, so the two cannot drift. HMA is a finite-window
average, so with enough closed candles (``warmup_days``) the lines are the backtest's to the paisa. What
lives here is what a backtest does not have to think about — one closed candle at a time, the price in
between, and a clock. The backtest's ``simulate`` in live terms:

* a SIGNAL candle that has just closed (the first to close above all three lines, the lines agreeing, a
  pullback to the medium line in the last ``pullback_bars``) ARMS a stop order at its high (low for a
  short). Nothing is ordered yet. Its stop is the signal candle's low (``two_bar``: the lower of it and
  the candle before), less ``stop_buffer``; risk = level − stop; ``max_risk`` > 0 skips a wider one.
* the setup lives for the next ``max_wait`` candles. The moment the index trades through the level the
  order goes out (the backtest's fill at the level); if it trades through the STOP first the setup is
  ``invalidated``; if the window passes, ``expired``. A level the index is already more than
  ``MAX_CHASE_RISK_FRAC`` × risk past when it is read is ``missed`` — not chased.
* an open trade ends at the index stop, at the target (``rr`` × risk from the entry; 0 = none), at cost
  once it has gone ``breakeven_rr`` R (the stop moves to the entry, from the next read), or at the
  square-off. Exits are on the index price, sold at market.
* one trade at a time (a signal closing while a trade is open is ``busy``, as the backtest's
  ``busy_until``), at most ``max_trades`` a day.

Prices are read three ways, the cheapest first: the ~1-5 s index poll (``on_price``), each closed
1-minute bar while a setup is armed or a trade open (``on_minute`` — the backtest walks these very
minutes, so a wick between two polls is still seen), and the closed candle itself (``on_bar``, the net
when the minute feed is late). Inside one read the backtest's order holds: an armed level before its
stop, an open stop before its target.

Everything takes plain dicts and DataFrames of CLOSED candles, so it is tested without a broker.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trading_app.Backtest.hull_suite_engine import HullSuiteEngine

MAX_CHASE_RISK_FRAC = 0.5

# The engine's own arguments, as a spec's ``params`` / ``fixed`` and the settings carry them.
ENGINE_KEYS = ('slow_len', 'medium_len', 'fast_len', 'slope_bars', 'slow_slope', 'pullback_bars', 'pullback_pct',
               'max_wait', 'stop_mode', 'stop_buffer', 'max_risk', 'rr', 'breakeven_rr', 'direction',
               'entry_start', 'entry_end', 'exit_cutoff')


def warmup_days(s: Dict[str, Any], bar_minutes: int) -> int:
    """Calendar days of 1-minute history the slow HMA needs to be exact on today's first candle:
    n + √n candles of the chart (375 minutes a session), ×1.6 for weekends and holidays, +3."""
    n = int(s.get('slow_len', 200))
    bars = n + int(np.sqrt(n)) + 5
    return int(bars * bar_minutes / 375 * 1.6) + 4


def new_day(date_str: str) -> Dict[str, Any]:
    return {'date': date_str, 'trades': [], 'last_bar': None}


def option_type(t: Dict[str, Any]) -> str:
    return 'CE' if t['type'] == 'Long' else 'PE'


def _hm(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _r(v, nd=2):
    try:
        v = float(v)
        return round(v, nd) if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def engine_for(bars: pd.DataFrame, s: Dict[str, Any], bar_minutes: int) -> HullSuiteEngine:
    kw = {k: s[k] for k in ENGINE_KEYS if k in s}
    return HullSuiteEngine(bars, max_trades_per_day=int(s.get('max_trades', 2)), bar_minutes=bar_minutes, **kw)


def _taken(day: Dict[str, Any]) -> int:
    """Trades that reached an order (or tried to) — what ``max_trades`` counts (the backtest's per-day fills)."""
    return sum(1 for t in day['trades'] if t['status'] in ('open', 'closed', 'failed'))


def _enter(day, t, px: float, fill: float, how: str, events, now_min: int) -> None:
    """The level was crossed: an entry, unless the price now (`px`) is already a run past it. `fill` is the
    backtest's index fill: the level, or the open of a minute that gapped through it."""
    long = t['type'] == 'Long'
    over = (px - t['level']) if long else (t['level'] - px)
    if t['risk'] > 0 and over > MAX_CHASE_RISK_FRAC * t['risk']:
        t.update(status='missed', error=f"the index was already {over:,.1f} pts past the {t['level']:,.1f} level "
                                        f"when it was read ({how}) — not chased")
        events.append({'event': 'missed', 'trade': t})
        return
    sign = 1.0 if long else -1.0
    entry = float(fill)          # the stop and target stay where the signal candle put them, as the backtest's
    t.update(status='open', fill_how=how, index_cross=round(float(px), 2), index_level_fill=entry,
             entry_bar_start=now_min, cur_stop=t['stop'],
             # full precision, as the backtest compares them (a rounded target can be touched where its is not)
             target=entry + sign * t['rr'] * t['risk'] if t['rr'] > 0 else None,
             be_at=t['level'] + sign * t['be_rr'] * t['risk'] if t['be_rr'] > 0 else None)
    events.append({'event': 'entry', 'trade': t, 'bar': t['signal_bar'], 'stale': False})
    # one trade at a time: another setup still waiting is the backtest's `busy`
    for o in day['trades']:
        if o is not t and o['status'] == 'armed':
            o.update(status='expired', error='a trade opened while it waited — one at a time')
            events.append({'event': 'expired', 'trade': o})


def _walk(day, hi: float, lo: float, last: float, now_min: int, cutoff: int, how: str,
          events: List[Dict[str, Any]], s: Dict[str, Any], gap_open: Optional[float] = None) -> None:
    """One read of the index (a poll: hi = lo = last; a closed minute or candle: its range and close).
    Armed: level first, then the stop (the backtest's order inside a minute). Open: stop, target, then
    the breakeven lift (it counts from the next read, as the backtest's `cur_stop = entry` does)."""
    for t in list(day['trades']):
        long = t['type'] == 'Long'
        if t['status'] == 'armed':
            if not t['break_start'] <= now_min < t['break_end'] or now_min >= cutoff:
                continue
            if _taken(day) >= int(s.get('max_trades', 2)):
                t.update(status='expired', error='the day\'s trades are taken')
                events.append({'event': 'expired', 'trade': t})
                continue
            if (hi >= t['level']) if long else (lo <= t['level']):
                # a minute that OPENS past a level still armed went through it in the gap: the backtest's fill at the open
                gap = gap_open is not None and ((gap_open > t['level']) if long else (gap_open < t['level']))
                _enter(day, t, last, gap_open if gap else t['level'], how, events, now_min)
            elif (lo <= t['stop']) if long else (hi >= t['stop']):
                t.update(status='invalidated', error=f"the index traded through the {t['stop']:,.1f} stop "
                                                     f"before the {t['level']:,.1f} level — setup dropped")
                events.append({'event': 'expired', 'trade': t})
        elif t['status'] == 'open' and t.get('cur_stop') is not None:
            # a candle is judged only if the trade was open from its start; a minute from the entry minute on
            # (the backtest checks the entry minute's own range too)
            if how != 'poll' and now_min < t['entry_bar_start']:
                continue
            if (how == 'minute' and now_min == t['entry_bar_start'] and not t.get('fill_read') and gap_open is not None
                    and t.get('fill_how') == 'poll'):
                # entered on a poll: the entry minute's own open says where the backtest filled — past the level
                # (a gap) it is the open, and the target / breakeven are measured from there
                t['fill_read'] = True
                if (gap_open > t['level']) if long else (gap_open < t['level']):
                    sign = 1.0 if long else -1.0
                    t['index_level_fill'] = float(gap_open)
                    if t['rr'] > 0:
                        t['target'] = gap_open + sign * t['rr'] * t['risk']
            stop = t['cur_stop']
            if (lo <= stop) if long else (hi >= stop):
                reason = 'Breakeven' if t.get('be_done') else 'Stop Loss'
                events.append({'event': 'exit', 'trade': t, 'bar': None, 'index_close': float(last), 'reason': reason,
                               'level': stop})
                t['cur_stop'] = None                      # decided: no second exit from a later read
                continue
            tg = t.get('target')
            if tg is not None and ((hi >= tg) if long else (lo <= tg)):
                events.append({'event': 'exit', 'trade': t, 'bar': None, 'index_close': float(last), 'reason': 'Target',
                               'level': tg})
                t['cur_stop'] = None
                continue
            be = t.get('be_at')
            if be is not None and not t.get('be_done') and ((hi >= be) if long else (lo <= be)):
                t.update(cur_stop=t['index_level_fill'], be_done=True)
                events.append({'event': 'breakeven', 'trade': t})


def on_bar(day: Dict[str, Any], bars: pd.DataFrame, s: Dict[str, Any], bar_minutes: int = 5) -> List[Dict[str, Any]]:
    """Read the LAST candle of `bars` (every candle in it is closed). Returns events:
    {'event': 'armed'|'expired'|'missed'|'entry'|'exit'|'breakeven', 'trade': t, ...}. Mutates `day`."""
    if bars is None or len(bars) < 10:
        return []
    eng = engine_for(bars, s, bar_minutes)
    df = eng.df
    i = len(df) - 1
    ts = df.index[i]
    ts_s = ts.strftime('%Y-%m-%d %H:%M:%S')
    if ts_s[:10] != day['date'] or ts_s == day.get('last_bar'):
        return []
    day['last_bar'] = ts_s
    tmin = int(ts.hour * 60 + ts.minute)
    bar_end = tmin + bar_minutes
    o, h, l, c = (float(df[k].iloc[i]) for k in ('open', 'high', 'low', 'close'))
    events: List[Dict[str, Any]] = []

    # 1. the candle as a whole — only when its minutes were not read one by one (a late feed, a restart
    #    catching up): the net for a level / stop / target. Its minutes count as read from here on.
    last_min = (ts + pd.Timedelta(minutes=bar_minutes - 1)).strftime('%Y-%m-%d %H:%M:%S')
    if (day.get('last_minute') or '') < last_min:
        _walk(day, h, l, c, tmin, eng.exit_cutoff, 'candle', events, s)
        day['last_minute'] = last_min
    for t in day['trades']:                       # a window that has run out
        if t['status'] == 'armed' and bar_end >= t['break_end']:
            t.update(status='expired', error=f"the {t['level']:,.1f} level was not crossed in "
                                             f"{eng.max_wait} candle(s) — setup dropped")
            events.append({'event': 'expired', 'trade': t})

    # 2. a signal candle that has just closed arms the next candles' break
    sg = eng.signals()
    side = int(sg['signal'][i])
    if not side:
        return events
    long = side == 1
    if any(t['status'] == 'open' and t.get('cur_stop') is not None for t in day['trades']):
        day['trades'].append({'n': len(day['trades']) + 1, 'type': 'Long' if long else 'Short', 'status': 'expired',
                              'signal_bar': ts_s, 'error': 'a trade was open when the signal closed (busy)'})
        events.append({'event': 'expired', 'trade': day['trades'][-1]})
        return events
    if _taken(day) >= eng.max_trades_per_day or bar_end >= eng.exit_cutoff:
        return events
    level = h if long else l
    if eng.stop_mode == 'two_bar' and i > 0 and df.index[i - 1].date() == ts.date():
        base = min(l, float(df['low'].iloc[i - 1])) if long else max(h, float(df['high'].iloc[i - 1]))
    else:
        base = l if long else h
    stop = base - (1 if long else -1) * eng.stop_buffer
    risk = (level - stop) if long else (stop - level)
    t = {'n': len(day['trades']) + 1, 'type': 'Long' if long else 'Short', 'status': 'armed',
         'signal_bar': ts_s, 'break_start': bar_end, 'break_end': bar_end + eng.max_wait * bar_minutes,
         'level': float(level), 'stop': float(stop), 'risk': float(risk),
         'rr': eng.rr, 'be_rr': eng.breakeven_rr,
         'signal': {'rsi': None, 'close': round(c, 2), 'level': round(level, 2), 'stop': round(stop, 2),
                    'slow': _r(sg['slow'][i]), 'medium': _r(sg['medium'][i]), 'fast': _r(sg['fast'][i])}}
    day['trades'].append(t)
    if risk <= 0:
        t.update(status='expired', error='no risk between the level and the stop')
        events.append({'event': 'expired', 'trade': t})
    elif eng.max_risk > 0 and risk > eng.max_risk:
        t.update(status='expired', error=f'risk {risk:,.1f} pts is over the {eng.max_risk:g} max — skipped')
        events.append({'event': 'expired', 'trade': t})
    else:
        events.append({'event': 'armed', 'trade': t, 'bar': ts_s})
    return events


def on_price(day: Dict[str, Any], ltp: Optional[float], hhmm: str, s: Dict[str, Any],
             bar_minutes: int = 5) -> List[Dict[str, Any]]:
    """The index price between candles. Mutates `day`."""
    if not ltp:
        return []
    events: List[Dict[str, Any]] = []
    _walk(day, float(ltp), float(ltp), float(ltp), _hm(hhmm), _hm(s.get('exit_cutoff', '15:15')), 'poll', events, s)
    return events


def on_minute(day: Dict[str, Any], minute: Dict[str, Any], s: Dict[str, Any],
              bar_minutes: int = 5) -> List[Dict[str, Any]]:
    """A CLOSED 1-minute index bar (`ts` 'YYYY-MM-DD HH:MM:SS', open/high/low/close): the backtest walks
    exactly these, so a level, stop or target touched between two polls is still taken — a few seconds
    after the minute, at market. Each minute is read once (`last_minute`; a candle judged whole marks its
    minutes read), and the algo reads the newest minute BEFORE the candle it closes. Mutates `day`."""
    ts = str(minute.get('ts') or '')[:19]
    if ts[:10] != day['date'] or ts <= (day.get('last_minute') or ''):
        return []
    day['last_minute'] = ts
    m = _hm(ts[11:16])
    hi, lo, cl, op = (minute.get(k) for k in ('high', 'low', 'close', 'open'))
    if hi is None or lo is None or cl is None:
        return []
    events: List[Dict[str, Any]] = []
    # the window's first minute OPENING past an armed level is the backtest's gap fill at that open
    _walk(day, float(hi), float(lo), float(cl), m, _hm(s.get('exit_cutoff', '15:15')), 'minute', events, s,
          gap_open=float(op) if op is not None else None)
    return events


def watching(day: Dict[str, Any], hhmm: str, s: Dict[str, Any], bar_minutes: int = 5) -> bool:
    """Is a price read worth its request: a setup whose window is running, or an open trade."""
    now = _hm(hhmm)
    return any((t['status'] == 'armed' and t['break_start'] <= now < t['break_end'])
               or (t['status'] == 'open' and t.get('cur_stop') is not None) for t in day['trades'])


def levels(day: Dict[str, Any]) -> List[float]:
    """The index levels a read is waiting on (for the 1 s poll near one)."""
    out = []
    for t in day['trades']:
        if t['status'] == 'armed':
            out += [t['level'], t['stop']]
        elif t['status'] == 'open' and t.get('cur_stop') is not None:
            out += [v for v in (t['cur_stop'], t.get('target'), t.get('be_at') if not t.get('be_done') else None) if v]
    return out


__all__ = ['new_day', 'option_type', 'on_bar', 'on_price', 'on_minute', 'watching', 'levels', 'engine_for',
           'warmup_days', 'ENGINE_KEYS', 'MAX_CHASE_RISK_FRAC']
