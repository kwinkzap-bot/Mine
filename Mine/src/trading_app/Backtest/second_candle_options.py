"""Candle Breakout (2nd candle) trades priced on REAL option candles.

The signal, entry, SL, target and exit stay the index's
(second_candle_engine, unchanged). The order is the option the live algo buys:
a CE for a Long, a PE for a Short, the strike priced nearest ₹250
(`premium250`, the live default) on the nearest weekly expiry. Until
2026-11-11 the page's OPT P&L box priced that leg with Black-Scholes at a fixed
15 % IV; this reads the contract's own recorded 1-minute candles instead, via
the pricer every other option backtest shares
(st_bb_rsi_engine.attach_option_legs → cpr_option_service.legs_at: the strike
walk, the expiry calendar, Breeze for expired contracts, disk cache).

Which minute is priced:

* The entry is a stop order at the range level, so it fills INSIDE a bar: the
  minute in which the index traded the entry price, at that minute's
  open/close average (`intrabar_entry`). On 30-second and 1-minute bars that
  is the bar's own minute; on 2/3/5-minute bars it is found on the index's
  1-minute bars (the first minute inside the bar whose range holds the price)
  — pricing the bar's first minute would buy before the breakout.
* An SL / target hit is the same, on the exit price. An EOD exit is the last
  minute of the bar. A Time Exit fills at the cut-off bar's open: the close of
  the minute before it.

A trade whose leg cannot be priced (no candles for any strike tried, or past
the per-run cap) is left out of the totals and counted. Rows keep the index's
times and prices; the premiums, the leg and the index's own P&L ride beside.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

PREMIUM = 250.0          # the live algo's premium250 strike target


def _crossing_minute(minutes: Optional[pd.DataFrame], start: str, span_min: int,
                     price: float, last_if_untouched: bool = True) -> Optional[str]:
    """'YYYY-MM-DD HH:MM:00' of the first 1-minute bar inside [start, start+span)
    whose high/low holds `price`; the bar's last minute when none does (or the
    stamp itself without minutes)."""
    if minutes is None or minutes.empty or span_min <= 1:
        return None
    t0 = pd.Timestamp(start)
    win = minutes[(minutes.index >= t0) & (minutes.index < t0 + pd.Timedelta(minutes=span_min))]
    if win.empty:
        return None
    hit = win[(win['low'] - 1e-9 <= price) & (price <= win['high'] + 1e-9)]
    ts = hit.index[0] if not hit.empty else (win.index[-1] if last_if_untouched else None)
    return None if ts is None else ts.strftime('%Y-%m-%d %H:%M:00')


def _last_minute(start: str, span_min: int) -> str:
    t = datetime.strptime(start[:19], '%Y-%m-%d %H:%M:%S') + timedelta(minutes=max(1, span_min) - 1)
    return t.strftime('%Y-%m-%d %H:%M:00')


def leg_requests(trades: List[Dict[str, Any]], span_min: int,
                 minutes: Optional[pd.DataFrame] = None) -> List[Dict[str, Any]]:
    """Each index trade as attach_option_legs reads it: the minute it really
    entered and left on, flagged intrabar where the fill was inside the minute."""
    out = []
    for k, t in enumerate(trades):
        r = dict(t, _k=k, intrabar_entry=True)
        ent = _crossing_minute(minutes, t['entry_time'], span_min, t['entry_price'])
        if ent:
            r['entry_time'] = ent
        reason = t.get('exit_reason')
        if reason in ('SL Hit', 'TG Hit'):
            r['intrabar_exit'] = True
            ext = _crossing_minute(minutes, t['exit_time'], span_min, t['exit_price'])
            if ext:
                r['exit_time'] = ext
        elif reason == 'EOD Exit':
            r['intrabar_exit'] = True
            if span_min > 1:
                r['exit_time'] = _last_minute(t['exit_time'], span_min)
        # Time Exit: filled at the cut-off bar's OPEN — the close of the minute
        # before it, which is attach_option_legs' default.
        out.append(r)
    return out


def price_trades(symbol: str, trades: List[Dict[str, Any]], span_min: int,
                 minutes: Optional[pd.DataFrame] = None,
                 calendar: Optional[Iterable] = None, max_legs: Optional[int] = None,
                 premium: float = PREMIUM,
                 progress: Optional[Callable[[int, int], None]] = None,
                 attach=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """(priced trades, info). Newest `max_legs` trades are priced; the rest and
    any leg with no candles are dropped and counted (info['truncated'],
    info['unpriced'])."""
    if attach is None:
        from trading_app.Backtest.st_bb_rsi_engine import attach_option_legs as attach
    work = trades[-max_legs:] if max_legs else list(trades)
    truncated = len(trades) - len(work)
    reqs = leg_requests(work, span_min, minutes)
    legs, info = attach(symbol, reqs, premium, 1, progress,
                        calendar=sorted(set(calendar)) if calendar else None)
    priced = []
    for leg in legs:
        t = work[leg['_k']]
        priced.append(dict(
            t,                                       # the index's own times and prices
            # The minutes the premiums were read at (HH:MM).
            option_entry_time=leg['entry_time'][11:16],
            option_exit_time=(leg['exit_time'][11:16] if leg.get('intrabar_exit') else
                              (datetime.strptime(leg['exit_time'][11:16], '%H:%M')
                               - timedelta(minutes=1)).strftime('%H:%M')),
            index_pnl=t['pnl'], pnl=leg['pnl'], result=leg['result'],
            entry_premium=leg['entry_premium'], exit_premium=leg['exit_premium'],
            strike=leg['strike'], option_type=leg['option_type'], expiry=leg['expiry'],
        ))
    info = dict(info, index_trades=len(trades), truncated=truncated + info.get('truncated', 0))
    return priced, info
