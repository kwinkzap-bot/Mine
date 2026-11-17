"""Today's live algo trades beside what the Backtest page's own logic makes of today.

The Active Trade tab's ⚖ Compare button asks, per live algo, "on today's candles, with this
algo's settings as they are now, which trades would the backtest have taken, and where did
live differ?". Nothing here re-states a rule: each algo is replayed through the engine its
Backtest-page run uses, built the way the live thread builds it —

* **Simple Trader 1-7 / ST Touch Break (specs 8-15)**: ``spec.cls`` with the live
  ``engine_kwargs`` (ST Touch Break: ``st_touch_break_live_engine.engine_for``'s numbers, plus
  the 1-minute frame the backtest walks), ``engine.run()``, then the option leg priced by
  ``attach_option_legs`` (through ``price_with_reentries`` where the engine re-enters after a
  premium stop) — the /backtest run routes' own steps.
* **CPR Logic**: ``cpr_trade_rule.propose_all`` on the session's 5-minute chart, as the
  /cpr-logic sheet's Update button replays a session (``cpr_backtest_service.chart_rows``).
  Index points only: CPR decides and exits on NIFTY levels.

The data loaders are the caller's (the route hands in the /backtest loaders), so this module
runs on whatever the Backtest page would read and is tested without a broker.

``match`` pairs the two lists by side and entry time; every pair says what differs.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Entry minutes further apart than this are two different trades, not one trade late.
MATCH_TOLERANCE_MIN = 15
# A difference under these is the same price read a second apart, not a difference.
INDEX_TOL = 3.0          # index points
PREMIUM_TOL = 2.0        # option premium points
TIME_TOL_MIN = 1         # minutes


def _hhmm(v: Any) -> Optional[str]:
    """'2026-11-17 11:57:00' / '2026-11-17T11:57:05' / '11:57' → '11:57'."""
    if not v:
        return None
    s = str(v)
    if len(s) >= 16 and s[10] in ' T':
        return s[11:16]
    return s[:5] if len(s) >= 5 and s[2] == ':' else None


def _mins(hhmm: Optional[str]) -> Optional[int]:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5]) if hhmm else None


def _side(v: Any) -> str:
    s = str(v or '').lower()
    return 'BUY' if s in ('buy', 'long') else 'SELL' if s in ('sell', 'short') else str(v or '')


def _avg(legs: List[Dict[str, Any]], key: str) -> Optional[float]:
    num = den = 0.0
    for l in legs:
        if l.get(key) is not None:
            q = float(l.get('qty') or l.get('lots') or 1)
            num += float(l[key]) * q
            den += q
    return round(num / den, 2) if den else None


# ── live side ────────────────────────────────────────────────────────────────

def live_simple(state: Dict[str, Any], date_str: str) -> List[Dict[str, Any]]:
    """Today's Simple Trader / ST Touch Break trades off the algo's day state. Trades that never
    reached an order (armed, not triggered) are not trades; missed / failed ones are kept, since
    a backtest trade beside one says why live has nothing."""
    day = (state or {}).get('day') or {}
    if day.get('date') != date_str:
        return []
    out = []
    for t in day.get('trades') or []:
        if t.get('status') not in ('open', 'closed', 'failed', 'missed'):
            continue
        legs = list((t.get('legs') or {}).values())
        opt = t.get('option') or {}
        out.append({
            'n': t.get('n'), 'status': t.get('status'), 'mode': t.get('mode'), 'side': _side(t.get('type')),
            'entry_time': _hhmm(t.get('entry_time')), 'exit_time': _hhmm(t.get('exit_time')),
            'index_entry': t.get('index_entry'), 'index_exit': t.get('index_exit'),
            'strike': opt.get('strike'), 'option_type': opt.get('type'),
            'entry_premium': _avg(legs, 'entry'), 'exit_premium': _avg(legs, 'exit'),
            'reason': t.get('result') or t.get('error'),
            'signal_bar': _hhmm(t.get('signal_bar')),
        })
    return out


def live_cpr(state: Dict[str, Any], date_str: str) -> List[Dict[str, Any]]:
    day = (state or {}).get('day') or {}
    if day.get('date') != date_str:
        return []
    out = []
    for t in day.get('trades') or []:
        if t.get('status') not in ('open', 'closed'):
            continue
        legs = list((t.get('legs') or {}).values())
        opt = t.get('option') or {}
        out.append({
            'n': t.get('n'), 'status': t.get('status'), 'mode': t.get('mode'), 'side': _side(t.get('trade')),
            'entry_time': _hhmm(t.get('fill_time')), 'exit_time': _hhmm(t.get('exit_time')),
            'index_entry': t.get('fill'), 'index_exit': t.get('exit'),
            'entry_level': t.get('entry'), 'sl': t.get('sl'), 'target': t.get('target'),
            'strike': opt.get('strike'), 'option_type': opt.get('type'),
            'entry_premium': _avg(legs, 'entry'), 'exit_premium': _avg(legs, 'exit'),
            'reason': t.get('result'), 'pts': t.get('pts'), 'setup_time': t.get('setup_time'),
        })
    return out


# ── backtest side ────────────────────────────────────────────────────────────

def backtest_simple(spec, s: Dict[str, Any], minutes, candles_df, date_str: str,
                    price: bool = True, symbol: Optional[str] = None) -> Dict[str, Any]:
    """Today's trades of Simple Trader spec `spec` with live settings `s` (``settings()`` plus the
    spec's window, as the thread hands them to ``on_bar``), on `candles_df` (closed candles of the
    spec's bar, 09:15-anchored, with run-in) and `minutes` (the 1-minute frame they came from).

    Returns {'trades': [...], 'priced': bool, 'warning': str|None}."""
    import pandas as pd
    from trading_app.service import simple_trader_live_engine as sim
    from trading_app.service import st_touch_break_live_engine as tb

    window = {'entry_start': spec.entry_start, 'entry_end': spec.fixed.get('entry_end', '15:14'),
              'exit_cutoff': spec.fixed.get('exit_cutoff', '15:14')}
    m = {**spec.fixed, **s, **window}
    if spec.kind == 'tb':
        # The live engine's numbers (tb.engine_for), plus the 1-minute frame the backtest walks
        # inside each candle — the /backtest/st-touch-break route's construction.
        from trading_app.Backtest.st_touch_break_engine import StTouchBreakEngine
        live = tb.engine_for(candles_df, m, spec.bar)
        eng = StTouchBreakEngine(
            candles_df, st_period=live.st_period, st_mult=live.st_mult,
            touch_tol_pct=float(m.get('touch_tol_pct', 0)), min_wick_pct=float(m.get('min_wick_pct', 0)),
            min_body_pct=float(m.get('min_body_pct', 0)), outside_bar=live.outside_bar, modes=live.modes,
            direction=live.direction, stop_mode=live.stop_mode,
            entry_start=m['entry_start'], entry_end=m['entry_end'], exit_cutoff=m['exit_cutoff'],
            max_trades_per_day=live.max_trades_per_day, minutes=minutes, bar_minutes=spec.bar)
    else:
        eng = spec.cls(candles_df, **sim.engine_kwargs(spec, m))
    trades, _ = eng.run()
    trades = [t for t in trades if str(t['entry_time'])[:10] == date_str]
    out = {'trades': trades, 'priced': False, 'warning': None}
    if not (price and trades):
        return out
    from trading_app.Backtest.st_bb_rsi_engine import attach_option_legs
    sessions = sorted(set(minutes.index.date))
    sl, tx, ty = float(s.get('sl_pct') or 0), float(s.get('tsl_x') or 0), float(s.get('tsl_y') or 0)
    premium = float(s.get('premium') or spec.premium)

    def price_fn(batch, prog=None):
        return attach_option_legs(symbol or spec.symbol, batch, premium, spec.bar, prog, calendar=sessions,
                                  sl_pct=sl, tsl_x=tx, tsl_y=ty, index_minutes=minutes)
    try:
        if hasattr(eng, 'trade_after'):
            from trading_app.Backtest.st_ema_rsi_cci_engine import price_with_reentries
            priced, info = price_with_reentries(eng, trades, price_fn)
        else:
            priced, info = price_fn(trades)
        priced = [t for t in priced if str(t['entry_time'])[:10] == date_str]
        out.update(trades=priced, priced=True)
        if info.get('unpriced'):
            out['warning'] = f"{info['unpriced']} backtest trade(s) had no option candles — shown on the index only"
            # keep the unpriced index trades visible rather than dropping them
            got = {t['entry_time'] for t in priced}
            out['trades'] = sorted(priced + [t for t in trades if t['entry_time'] not in got],
                                   key=lambda t: t['entry_time'])
    except Exception as e:      # no ICICI session: the index trade still compares
        logger.warning(f'[compare] option pricing failed for spec {spec.n}: {e}')
        out['warning'] = f'Option premiums unavailable ({e}); index only'
    _ = pd
    return out


def bt_rows_simple(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        'side': _side(t.get('type')),
        'entry_time': _hhmm(t.get('entry_time')), 'exit_time': _hhmm(t.get('exit_time')),
        'index_entry': t.get('entry_price'), 'index_exit': t.get('exit_price'),
        'strike': t.get('strike'), 'option_type': t.get('option_type'),
        'entry_premium': t.get('entry_premium'), 'exit_premium': t.get('exit_premium'),
        'reason': t.get('exit_reason'), 'signal_bar': _hhmm(t.get('signal_time')),
        'setup': t.get('setup'),
    } for t in trades]


def backtest_cpr(date_str: str, fetch_bars: Callable, symbol: str = 'NIFTY') -> List[Dict[str, Any]]:
    """The /cpr-logic sheet's replay of one session: what its Update button would write for it."""
    from datetime import date as _date
    from trading_app.service import cpr_backtest_service as svc
    from trading_app.service.cpr_trade_rule import propose_all
    d = _date.fromisoformat(date_str)
    daily, intraday = fetch_bars(symbol, d, d)
    bars = intraday.get(date_str) or []
    if not bars:
        raise LookupError(f'no 5-minute candles for {date_str}')
    res = svc.analyse([{'date': date_str}], daily, intraday)
    r = (res.get('rows') or [{}])[0]
    c = r.get('chart')
    if not c:
        raise LookupError(r.get('error') or 'the chart could not be read for the session')
    rows = []
    for p, why, setup_i, sim in propose_all(c, bars):
        if not p or not sim or sim.get('result') == 'No fill':
            continue
        fill = sim.get('fill', p.get('entry'))
        pnl = sim.get('pnl')
        out_px = None if fill is None or pnl is None else round(fill + pnl if p['trade'] == 'BUY' else fill - pnl, 2)
        rows.append({
            'side': _side(p['trade']), 'entry_time': _hhmm(sim.get('entry_time')),
            'exit_time': _hhmm(sim.get('exit_time')),
            'index_entry': fill, 'index_exit': out_px,
            'entry_level': p.get('entry'), 'sl': p.get('sl'), 'target': p.get('target'),
            'reason': sim.get('result'), 'pts': sim.get('pnl'),
            'setup_time': bars[setup_i]['time'] if setup_i is not None and setup_i < len(bars) else None,
            'why': why,
        })
    return rows


# ── pairing ──────────────────────────────────────────────────────────────────

_FIELDS = (  # (key, label, kind)
    ('entry_time', 'Entry time', 'time'), ('exit_time', 'Exit time', 'time'),
    ('index_entry', 'Index entry', 'index'), ('index_exit', 'Index exit', 'index'),
    ('strike', 'Strike', 'exact'), ('option_type', 'Option', 'exact'),
    ('entry_premium', 'Option entry', 'premium'), ('exit_premium', 'Option exit', 'premium'),
    ('reason', 'Exit reason', 'reason'),
)


def _norm_reason(r: Any) -> str:
    s = str(r or '').lower()
    if 'target' in s:
        return 'target'
    if 'time' in s or 'eod' in s or 'square' in s:
        return 'time'
    if 'supertrend' in s or 'st ' in s:
        return 'supertrend'
    if 'sl' in s or 'stop' in s:
        return 'stop'
    return s


def _diffs(lv: Dict[str, Any], bt: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for key, label, kind in _FIELDS:
        a, b = lv.get(key), bt.get(key)
        if a is None or b is None:
            continue
        if kind == 'time':
            gap = abs(_mins(a) - _mins(b))
            if gap > TIME_TOL_MIN:
                out.append({'field': label, 'live': a, 'backtest': b, 'gap': f'{gap} min'})
        elif kind in ('index', 'premium'):
            gap = float(a) - float(b)
            if abs(gap) > (INDEX_TOL if kind == 'index' else PREMIUM_TOL):
                out.append({'field': label, 'live': a, 'backtest': b, 'gap': round(gap, 2)})
        elif kind == 'reason':
            if _norm_reason(a) != _norm_reason(b):
                out.append({'field': label, 'live': a, 'backtest': b})
        elif str(a) != str(b):
            out.append({'field': label, 'live': a, 'backtest': b})
    return out


def match(live: List[Dict[str, Any]], bt: List[Dict[str, Any]],
          tol: int = MATCH_TOLERANCE_MIN) -> List[Dict[str, Any]]:
    """Pair each live trade with the backtest trade of the same side nearest its entry (signal
    candle for one that never filled), within `tol` minutes. Rows: {'live', 'backtest', 'status',
    'diffs'}; status is match / differs / live_only / backtest_only."""
    used = set()
    rows = []
    for lv in sorted(live, key=lambda t: t.get('entry_time') or t.get('signal_bar') or ''):
        t0 = _mins(lv.get('entry_time') or lv.get('signal_bar'))
        best, gap = None, None
        for j, b in enumerate(bt):
            if j in used or b['side'] != lv['side'] or t0 is None or not b.get('entry_time'):
                continue
            g = abs(_mins(b['entry_time']) - t0)
            if g <= tol and (gap is None or g < gap):
                best, gap = j, g
        if best is None:
            rows.append({'live': lv, 'backtest': None, 'status': 'live_only', 'diffs': []})
            continue
        used.add(best)
        b = bt[best]
        if lv.get('status') in ('missed', 'failed'):
            rows.append({'live': lv, 'backtest': b, 'status': 'differs',
                         'diffs': [{'field': 'Live', 'live': lv['status'], 'backtest': 'traded'}]})
            continue
        d = _diffs(lv, b)
        rows.append({'live': lv, 'backtest': b, 'status': 'differs' if d else 'match', 'diffs': d})
    for j, b in enumerate(bt):
        if j not in used:
            rows.append({'live': None, 'backtest': b, 'status': 'backtest_only', 'diffs': []})
    rows.sort(key=lambda r: (r['live'] or r['backtest']).get('entry_time') or '')
    return rows


def summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {'match': 0, 'differs': 0, 'live_only': 0, 'backtest_only': 0}
    for r in rows:
        out[r['status']] += 1
    return out


__all__ = ['live_simple', 'live_cpr', 'backtest_simple', 'bt_rows_simple', 'backtest_cpr', 'match', 'summary']
