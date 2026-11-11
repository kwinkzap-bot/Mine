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


# ── 30-second runs: premiums read at the SECOND the index crossed the level ──
# A 30-second trade often enters and leaves inside ONE minute (09:16:00 in,
# 09:16:30 out on an SL), and on 1-minute candles both legs read the same bar:
# Opt P&L 0.00 beside an index loss of -30 (2026-04-22/23, 2026-05-04 on the
# page, 2026-11-11), and an SL'd PE at +2.42. Averaging a 30-second bar was not
# enough either (-1.53 for that -30): a stop-entry PE is bought as the index
# falls THROUGH the level, at the bar's high premium, and sold as it bounces.
#
# So a 30-second run reads both sides on Breeze 1-second data — the index's and
# the contract's, one request per 15-minute slice each (usually one a trade),
# disk-cached once settled: the first second inside the trade's bar in which the
# index traded the entry (SL / target) price, and the option's last print at or
# before that second. A Time Exit is the option's last print before the cut-off,
# an EOD exit the bar's last print. Fallbacks, counted: no index seconds → the
# option's 30-second bar average; no option seconds (Breeze has holes:
# 2018-05-02, 2019-06-03, 2022-06-01) → the 1-minute premiums stay.

SLICE_SECONDS = 900          # icici_data_service._SECOND_WINDOW_SECONDS
_OPEN_MIN = 9 * 60 + 15


def _slice_of(ts: datetime) -> int:
    return int(((ts.hour * 60 + ts.minute - _OPEN_MIN) * 60 + ts.second) // SLICE_SECONDS)


def _by_second(rows: Iterable[Dict[str, Any]]) -> Dict[datetime, Dict[str, float]]:
    out = {}
    for b in rows or []:
        ts = pd.Timestamp(b['date'])
        if ts.tzinfo is not None:
            ts = ts.tz_convert('Asia/Kolkata').tz_localize(None)
        try:
            out[ts.to_pydatetime().replace(microsecond=0)] = {
                k: float(b[k]) for k in ('open', 'high', 'low', 'close')}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def refine_on_30s(priced: List[Dict[str, Any]],
                  fetch_option: Callable[[str, float, str, Any, int], List[Dict[str, Any]]],
                  fetch_index: Callable[[Any, int], List[Dict[str, Any]]]
                  ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """(trades re-priced on 1-second data, counts). fetch_option(expiry, strike,
    option_type, day, slice) and fetch_index(day, slice) return that 15-minute
    slice's 1-second bars. counts: 'second' (both sides at the crossing
    second), 'bar' (option 30-second average), 'minute' (1-minute kept)."""
    ocache: Dict[tuple, Dict[datetime, Dict[str, float]]] = {}
    icache: Dict[tuple, Dict[datetime, Dict[str, float]]] = {}

    def _get(cache, fetch, key):
        if key not in cache:
            try:
                cache[key] = _by_second(fetch(*key))
            except Exception:            # noqa: BLE001 — that leg falls back
                cache[key] = {}
        return cache[key]

    def opt_rows(t, ts):
        return _get(ocache, fetch_option,
                    (t['expiry'], t['strike'], t['option_type'], ts.date(), _slice_of(ts)))

    def idx_rows(ts):
        return _get(icache, fetch_index, (ts.date(), _slice_of(ts)))

    def crossing(bar: datetime, level: float, up: bool, after: Optional[datetime] = None):
        """First second in the 30-second `bar` (from `after`) the index traded `level`."""
        rows = idx_rows(bar)
        for k in range(30):
            ts = bar + timedelta(seconds=k)
            if after is not None and ts < after:
                continue
            r = rows.get(ts)
            if r and ((r['high'] >= level) if up else (r['low'] <= level)):
                return ts
        return None

    def print_at(t, ts: datetime, before: bool = False) -> Optional[float]:
        """Option's last print at (or strictly `before`) `ts`, else its next one in the bar."""
        rows = opt_rows(t, ts)
        start = 1 if before else 0
        for k in range(start, 31):
            r = rows.get(ts - timedelta(seconds=k))
            if r:
                return r['close']
        for k in range(1, 30):
            r = rows.get(ts + timedelta(seconds=k))
            if r:
                return r['close']
        return None

    def bar_mid(t, bar: datetime) -> Optional[float]:
        rows = opt_rows(t, bar)
        sec = [rows[bar + timedelta(seconds=k)] for k in range(30) if (bar + timedelta(seconds=k)) in rows]
        return (sec[0]['open'] + sec[-1]['close']) / 2 if sec else None

    counts = {'second': 0, 'bar': 0, 'minute': 0}
    out = []
    for t in priced:
        long_ = t.get('type') != 'Short'
        ent_bar = datetime.strptime(t['entry_time'][:19], '%Y-%m-%d %H:%M:%S')
        ext_bar = datetime.strptime(t['exit_time'][:19], '%Y-%m-%d %H:%M:%S')
        reason = t.get('exit_reason')
        # Entry: the index trading through the range level (up for a long).
        ent_s = crossing(ent_bar, t['entry_price'], up=long_)
        entry = print_at(t, ent_s) if ent_s else None
        exact = entry is not None
        if entry is None:
            entry = bar_mid(t, ent_bar)
        # Exit.
        ext_s, exit_ = None, None
        if reason == 'Time Exit':
            exit_ = print_at(t, ext_bar, before=True)
            ext_s = ext_bar
        elif reason == 'EOD Exit':
            exit_ = print_at(t, ext_bar + timedelta(seconds=29))
            ext_s = ext_bar + timedelta(seconds=29)
        else:
            # SL: against the trade; target: with it.
            up = long_ if reason == 'TG Hit' else not long_
            ext_s = crossing(ext_bar, t['exit_price'], up=up,
                             after=(ent_s + timedelta(seconds=1)) if (ent_s and ext_bar == ent_bar) else None)
            exit_ = print_at(t, ext_s) if ext_s else None
            if exit_ is None:
                exact, ext_s = False, None
                exit_ = bar_mid(t, ext_bar)
        if entry is None or exit_ is None:
            counts['minute'] += 1
            out.append(t)
            continue
        counts['second' if exact and ext_s else 'bar'] += 1
        entry, exit_ = round(entry, 2), round(exit_, 2)
        pnl = round(exit_ - entry, 2)
        out.append(dict(
            t, entry_premium=entry, exit_premium=exit_, pnl=pnl,
            result='WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
            option_entry_time=(ent_s or ent_bar).strftime('%H:%M:%S'),
            option_exit_time=(ext_s or ext_bar).strftime('%H:%M:%S'),
            option_bars='1s' if exact and ext_s else '30s'))
    return out, counts
