"""ST + BB + RSI + R1/S1 — the decisions of the live algo, with no I/O.

The rule is the Backtest page's (Backtest/st_bb_rsi_engine.py): the signals come
from the SAME ``StBbRsiEngine.signals`` the backtest simulates with, so the two
cannot drift. What lives here is the part a backtest does not have to think
about — one closed candle at a time, with a clock:

* a signal on the candle that JUST closed enters now (the backtest's "next bar's
  open"); a signal read off an older candle is stale and is never traded;
* an open trade exits when a later candle's close puts SuperTrend against it, or
  at the square-off minute — there is no target and no stop;
* at most ``max_trades`` a day, one open at a time.

Everything here takes plain dicts and a DataFrame of CLOSED candles, so it is
tested without a broker.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import StBbRsiEngine, brokerage_for

BAR_MINUTES = 3


def new_day(date_str: str) -> Dict[str, Any]:
    return {'date': date_str, 'trades': [], 'last_bar': None}


def engine_kwargs(s: Dict[str, Any]) -> Dict[str, Any]:
    """The StBbRsiEngine arguments the settings dict stands for."""
    return dict(rsi_period=s['rsi_len'], rsi_level=s['rsi_level'], bb_period=s['bb_len'],
                bb_mult=s['bb_mult'], bb_offset=0, st_period=s['st_len'], st_mult=s['st_mult'],
                entry_start=s.get('entry_start', '09:15'), entry_end=s.get('entry_end', '15:14'),
                exit_cutoff=s.get('exit_cutoff', '15:14'), max_trades_per_day=s['max_trades'],
                require_green=bool(s.get('require_green', False)), direction=s['direction'])


def option_type(t: Dict[str, Any]) -> str:
    """A long signal buys a CALL, a short signal buys a PUT."""
    return 'CE' if t['type'] == 'Long' else 'PE'


def on_bar(day: Dict[str, Any], bars: pd.DataFrame, s: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read the LAST candle of `bars` (every candle in it is closed). Returns
    events: {'event': 'entry'|'exit', 'trade': t, ...}. Mutates `day`."""
    if bars is None or bars.empty:
        return []
    kw = engine_kwargs(s)
    eng = StBbRsiEngine(bars, **kw)
    sg = eng.signals()
    b = eng._base()
    i = len(eng.df) - 1
    ts = str(b['ts'][i])
    if ts[:10] != day['date'] or ts == day.get('last_bar'):
        return []
    day['last_bar'] = ts
    tmin = int(b['tmin'][i])
    bar_end = tmin + BAR_MINUTES
    events: List[Dict[str, Any]] = []

    open_t = next((t for t in day['trades'] if t['status'] == 'open'), None)
    if open_t is not None:
        # Only candles from the entry candle on can end the trade, exactly as the
        # backtest only looks from the bar it filled on.
        if tmin >= open_t['entry_bar_start']:
            against = -1 if open_t['type'] == 'Long' else 1
            if int(sg['st_dir'][i]) == against:
                name = 'SuperTrend Down' if open_t['type'] == 'Long' else 'SuperTrend Up'
                events.append({'event': 'exit', 'trade': open_t, 'reason': name,
                               'bar': ts, 'index_close': float(b['c'][i])})
        return events

    entered = len(day['trades'])
    side = int(sg['signal'][i])
    if (side and entered < eng.max_trades_per_day
            and eng.entry_start <= tmin < eng.entry_end and bar_end < eng.exit_cutoff):
        long = side == 1
        lv = sg['lv']
        t = {'n': entered + 1, 'type': 'Long' if long else 'Short',
             'status': 'open', 'signal_bar': ts, 'entry_bar_start': bar_end,
             'signal': {'rsi': round(float(sg['rsi'][i]), 1), 'close': round(float(b['c'][i]), 2),
                        'bb_band': round(float((lv['bb_up'] if long else lv['bb_lo'])[i]), 2),
                        'supertrend': round(float(sg['st_line'][i]), 2),
                        'pivot_level': round(float((lv['r1'] if long else lv['s1'])[i]), 2),
                        'pivot': 'R1' if long else 'S1'}}
        day['trades'].append(t)
        events.append({'event': 'entry', 'trade': t, 'bar': ts})
    return events


def time_exits(day: Dict[str, Any], hhmm: str, cutoff: str) -> List[Dict[str, Any]]:
    """Square off whatever is open once the clock reaches the cutoff minute."""
    if hhmm < cutoff:
        return []
    return [{'event': 'exit', 'trade': t, 'reason': 'Time Exit', 'bar': None, 'index_close': None}
            for t in day['trades'] if t['status'] == 'open']


__all__ = ['new_day', 'engine_kwargs', 'option_type', 'on_bar', 'time_exits', 'brokerage_for', 'BAR_MINUTES']
