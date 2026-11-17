"""Sunshine Straddle — intraday ATM straddles on NIFTY and SENSEX, two windows a day.

From a video (Darshan Rathod, Lemon, "Sunshine & Sunset trading system", youtube
6qpMm393xKg, Hindi). His reading of 3-4 years of data: option premiums decay most in the
first hour and the last hour, so a short straddle is held only then.

* **Days.** NIFTY on Monday and Tuesday, SENSEX on Wednesday and Thursday, the weekly
  expiry running that day; Friday is not traded. Since NSE's Tuesday expiry (2025-09-01)
  and BSE's Thursday one (the same day) that is each index's 1 DTE and 0 DTE. Before
  then the same weekdays were further from expiry, which is what his "from 2024" run on
  fixed weekdays would also have traded. `schedule='dte'` trades each index on its own
  expiry day and the session before it instead, whatever weekday they fall on.
* **Windows.** One minute before each entry a hedge CE and PE are bought `hedge_strikes`
  strikes out (the video: OTM 20 or the ~₹5 option, for margin only); at the entry the ATM
  CE and PE are sold. Each short leg carries its own stop at +`sl_pct` (50 %) of its own
  premium; a stopped leg closes alone. Everything left is closed at the window's exit.
  NIFTY 09:22 → 10:16 and 14:27 → 15:14; SENSEX 09:22 → 10:30 and 13:35 → 15:14. The
  video says 15:35 / 15:30 for the last exit; 15:14 is his own "if you do not want to sit
  through the cash settlement" alternative and the one that is reachable on 1-minute bars.
* **SENSEX profit lock** (the video's "advanced rule"): once the window's mark-to-market
  reaches ₹2,000 a lot, ₹750 is locked; every further ₹20 lifts the lock by ₹10. The
  window is closed at the first minute close at or under the lock. Off for NIFTY by
  default (a box).

Prices are the contract's own 1-minute bars (Breeze, disk-cached per contract-session).
A price "at" HH:MM is the close of the minute that ends then (the bar labelled one minute
earlier). A stop is filled at its level the first minute whose high reaches it, or at that
minute's open when it opens through it. Lot sizes are today's (NIFTY 65, SENSEX 20) for
every year, so ₹ thresholds mean the same thing across the run.

The engine is pure: the route hands it the sessions, the index minutes and a `fetch(con,
day)` that returns a contract-session's bars.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

INDICES = {
    'NIFTY':  {'step': 50,  'lot': 65, 'exchange': 'NFO', 'txn_pct': 0.03503},
    'SENSEX': {'step': 100, 'lot': 20, 'exchange': 'BFO', 'txn_pct': 0.0325},
}
WEEKDAYS = ('mon', 'tue', 'wed', 'thu', 'fri')

DEFAULTS: Dict[str, Any] = {
    'schedule': 'weekdays',            # weekdays | dte
    'nf_days': 'mon,tue', 'sx_days': 'wed,thu',
    'nf_am_in': '09:22', 'nf_am_out': '10:16', 'nf_pm_in': '14:27', 'nf_pm_out': '15:14',
    'sx_am_in': '09:22', 'sx_am_out': '10:30', 'sx_pm_in': '13:35', 'sx_pm_out': '15:14',
    'sl_pct': 50.0,
    'nf_hedge_strikes': 20, 'sx_hedge_strikes': 20,
    'nf_lots': 1, 'sx_lots': 1,
    'nf_trail': False, 'sx_trail': True,
    'trail_at': 2000.0, 'trail_lock': 750.0, 'trail_step': 20.0, 'trail_by': 10.0,
    'brokerage': 20.0, 'taxes': True,
    'capital': 300000.0,
}

WINDOWS = (('NIFTY', 'am', 'nf'), ('NIFTY', 'pm', 'nf'), ('SENSEX', 'am', 'sx'), ('SENSEX', 'pm', 'sx'))


# ── Clock helpers ────────────────────────────────────────────────────────────

def _mins(hhmm: str) -> int:
    h, m = str(hhmm).split(':')[:2]
    return int(h) * 60 + int(m)


def _hhmm(m: int) -> str:
    return f'{m // 60:02d}:{m % 60:02d}'


def bar_close_at(bars: Dict[str, Dict], hhmm: str) -> Optional[float]:
    """The price at HH:MM: the close of the minute labelled one minute earlier."""
    b = bars.get(_hhmm(_mins(hhmm) - 1))
    return float(b['close']) if b else None


def last_close_by(bars: Dict[str, Dict], hhmm: str, since: str) -> Optional[float]:
    """The latest close at or before HH:MM, not earlier than `since` (a thin contract can
    skip a minute; its last trade still stands)."""
    lo, m = _mins(since), _mins(hhmm) - 1
    while m >= lo - 1:
        b = bars.get(_hhmm(m))
        if b:
            return float(b['close'])
        m -= 1
    return None


def index_bars(bars) -> Dict[str, Dict]:
    if isinstance(bars, dict):                 # already keyed by HH:MM (the sweep's memo)
        return bars
    return {b['time']: b for b in bars or [] if b.get('time')}


# ── Which index trades which day ─────────────────────────────────────────────

def parse_days(text) -> Tuple[int, ...]:
    if isinstance(text, (list, tuple)):
        items = text
    else:
        items = [s for s in str(text or '').replace(' ', '').lower().split(',') if s]
    out = []
    for s in items:
        s = str(s).lower()[:3]
        if s in WEEKDAYS:
            out.append(WEEKDAYS.index(s))
    return tuple(sorted(set(out)))


def plan_days(sessions: Sequence[date], start: date, end: date, p: Dict,
              expiry_for: Callable[[str, date], Optional[date]]) -> List[Tuple[date, str, date]]:
    """[(day, index, expiry)] in date order. `expiry_for(index, day)` is the weekly expiry
    running that day (holidays already absorbed)."""
    days = sorted(d for d in sessions if start <= d <= end)
    ordered = sorted(set(sessions))
    nxt = {d: ordered[i + 1] for i, d in enumerate(ordered[:-1])}
    out = []
    for d in days:
        for idx, key in (('NIFTY', 'nf'), ('SENSEX', 'sx')):
            if not p.get(f'{key}_on', True):
                continue
            exp = expiry_for(idx, d)
            if exp is None:
                continue
            if p.get('schedule') == 'dte':
                if d == exp or nxt.get(d) == exp:
                    out.append((d, idx, exp))
            elif d.weekday() in parse_days(p.get(f'{key}_days', '')):
                out.append((d, idx, exp))
    return out


# ── One window ───────────────────────────────────────────────────────────────

def lock_level(peak: float, p: Dict) -> Optional[float]:
    """₹ a lot locked once the peak mark-to-market reached trail_at; None before."""
    at, lock, step, by = p['trail_at'], p['trail_lock'], p['trail_step'], p['trail_by']
    if at <= 0 or peak < at:
        return None
    steps = math.floor((peak - at) / step) if step > 0 else 0
    return lock + steps * by


def charges(legs: List[Dict], idx: str, lots: int, day: date, p: Dict) -> float:
    """₹ of brokerage and statutory charges on every order the window sent."""
    lot = INDICES[idx]['lot'] * lots
    orders = 2 * len(legs)
    total = p['brokerage'] * orders
    if not p.get('taxes', True):
        return round(total, 2)
    buy_t = sum((g['entry'] if g['side'] == 'buy' else g['exit']) * lot for g in legs)
    sell_t = sum((g['exit'] if g['side'] == 'buy' else g['entry']) * lot for g in legs)
    stt = sell_t * (0.001 if day >= date(2024, 10, 1) else 0.000625)
    txn = (buy_t + sell_t) * INDICES[idx]['txn_pct'] / 100
    sebi = (buy_t + sell_t) * 1e-6
    stamp = buy_t * 0.00003
    gst = 0.18 * (total + txn + sebi)
    return round(total + stt + txn + sebi + stamp + gst, 2)


def run_window(day: date, idx: str, expiry: date, t_in: str, t_out: str,
               spot: Dict[str, Dict], fetch: Callable[[Dict, date], List[Dict]],
               p: Dict, trail: bool, hedge_strikes: int, lots: int) -> Dict[str, Any]:
    """One straddle window. Returns a trade row, or {'skip': reason}."""
    spec = INDICES[idx]
    s = bar_close_at(spot, t_in)
    if s is None:
        return {'skip': 'no index price at entry'}
    step = spec['step']
    k = int(round(s / step) * step)
    legs = [{'name': 'CE', 'side': 'sell', 'strike': k, 'type': 'CE'},
            {'name': 'PE', 'side': 'sell', 'strike': k, 'type': 'PE'}]
    if hedge_strikes > 0:
        legs += [{'name': 'Hedge CE', 'side': 'buy', 'strike': k + hedge_strikes * step, 'type': 'CE'},
                 {'name': 'Hedge PE', 'side': 'buy', 'strike': k - hedge_strikes * step, 'type': 'PE'}]
    t_hedge = _hhmm(_mins(t_in) - 1)
    for g in legs:
        con = {'expiry': expiry.isoformat(), 'strike': g['strike'], 'option_type': g['type']}
        g['bars'] = index_bars(fetch(con, day))
        t0 = t_hedge if g['side'] == 'buy' else t_in
        px = bar_close_at(g['bars'], t0) if g['bars'] else None
        if px is None and g['side'] == 'buy':
            px = last_close_by(g['bars'], t0, '09:15')     # a deep wing may not print every minute
        if px is None:
            return {'skip': f"no premium for {g['name']} {g['strike']} at {t0}", 'strike': k}
        g['entry'] = px
        g['exit'] = None
        g['exit_time'] = None
        g['why'] = None
        if g['side'] == 'sell':
            g['stop'] = px * (1 + p['sl_pct'] / 100) if p['sl_pct'] > 0 else None
    lot = spec['lot']
    last = {g['name']: g['entry'] for g in legs}
    peak, lock, reason = -1e18, None, 'Time exit'
    end = _mins(t_out)
    for m in range(_mins(t_in), end):                     # bars t_in … t_out-1
        hm = _hhmm(m)
        for g in legs:
            b = g['bars'].get(hm)
            if g['exit'] is not None or not b:
                continue
            if g['side'] == 'sell' and g['stop'] is not None:
                if b['open'] >= g['stop']:
                    g['exit'], g['why'] = float(b['open']), 'Stop (gap)'
                elif b['high'] >= g['stop']:
                    g['exit'], g['why'] = g['stop'], 'Stop'
                if g['exit'] is not None:
                    g['exit_time'] = _hhmm(m + 1)
                    continue
            last[g['name']] = float(b['close'])
        if not trail:
            continue
        mtm = sum(((g['exit'] if g['exit'] is not None else last[g['name']]) - g['entry'])
                  * (1 if g['side'] == 'buy' else -1) for g in legs) * lot
        peak = max(peak, mtm)
        lv = lock_level(peak, p)
        if lv is not None:
            lock = lv
            if mtm <= lv:
                reason = 'Profit lock'
                for g in legs:
                    if g['exit'] is None:
                        g['exit'], g['exit_time'], g['why'] = last[g['name']], _hhmm(m + 1), 'Profit lock'
                break
    for g in legs:
        if g['exit'] is None:
            px = last_close_by(g['bars'], t_out, t_in)
            g['exit'] = px if px is not None else last[g['name']]
            g['exit_time'], g['why'] = t_out, 'Time exit'
    shorts = [g for g in legs if g['side'] == 'sell']
    stops = sum(1 for g in shorts if (g['why'] or '').startswith('Stop'))
    if reason != 'Profit lock' and stops:
        reason = 'Both stopped' if stops == 2 else f"{'CE' if shorts[0]['why'].startswith('Stop') else 'PE'} stopped"
    pts = sum((g['exit'] - g['entry']) * (1 if g['side'] == 'buy' else -1) for g in legs)
    gross = pts * lot * lots
    cost = charges(legs, idx, lots, day, p)
    leg_rows = [{'name': g['name'], 'strike': g['strike'], 'side': g['side'],
                 'entry': round(g['entry'], 2), 'exit': round(g['exit'], 2),
                 'exit_time': g['exit_time'], 'why': g['why']} for g in legs]
    return {
        'date': day.isoformat(), 'index': idx, 'expiry': expiry.isoformat(),
        'dte': None, 'window': None, 'entry_time': t_in, 'exit_time': t_out,
        'spot': round(s, 2), 'strike': k,
        'ce_entry': round(shorts[0]['entry'], 2), 'pe_entry': round(shorts[1]['entry'], 2),
        'ce_exit': round(shorts[0]['exit'], 2), 'pe_exit': round(shorts[1]['exit'], 2),
        'credit': round(shorts[0]['entry'] + shorts[1]['entry'], 2),
        'hedge_cost': round(sum(g['entry'] for g in legs if g['side'] == 'buy'), 2),
        'legs': leg_rows, 'stops': stops, 'reason': reason,
        'peak_lot': round(peak, 2) if trail and peak > -1e17 else None,
        'lock_lot': round(lock, 2) if lock is not None else None,
        'points': round(pts, 2), 'lots': lots, 'qty': lot * lots,
        'gross': round(gross, 2), 'charges': cost, 'pnl_rupees': round(gross - cost, 2),
    }


# ── The run ──────────────────────────────────────────────────────────────────

def simulate(plan: List[Tuple[date, str, date]], sessions: Sequence[date],
             spot_for: Callable[[str, date], Dict[str, Dict]],
             fetch_for: Callable[[str], Callable[[Dict, date], List[Dict]]],
             p: Dict, progress=None) -> Dict[str, Any]:
    ordered = sorted(set(sessions))
    pos = {d: i for i, d in enumerate(ordered)}
    trades, skipped = [], []
    for n, (day, idx, exp) in enumerate(plan):
        key = 'nf' if idx == 'NIFTY' else 'sx'
        spot = spot_for(idx, day)
        dte = (pos[exp] - pos[day]) if exp in pos and day in pos else (exp - day).days
        for win in ('am', 'pm'):
            t_in, t_out = p.get(f'{key}_{win}_in') or '', p.get(f'{key}_{win}_out') or ''
            if not t_in or not t_out or _mins(t_out) <= _mins(t_in):
                continue
            try:
                row = run_window(day, idx, exp, t_in, t_out, spot, fetch_for(idx), p,
                                 bool(p.get(f'{key}_trail')), int(p.get(f'{key}_hedge_strikes') or 0),
                                 int(p.get(f'{key}_lots') or 1))
            except Exception as e:                       # noqa: BLE001 — a cache miss etc. skips the window
                row = {'skip': f'{type(e).__name__}: {e}'}
            if 'skip' in row:
                skipped.append({'date': day.isoformat(), 'index': idx, 'window': win.upper(),
                                'reason': row['skip']})
                continue
            row['window'] = 'AM' if win == 'am' else 'PM'
            row['dte'] = dte
            trades.append(row)
        if progress:
            progress(n + 1, len(plan))
    return {'trades': trades, 'skipped': skipped}


def summarise(trades: List[Dict], capital: float, start: date, end: date) -> Dict[str, Any]:
    trades = sorted(trades, key=lambda t: (t['date'], t['entry_time'], t['index']))
    pnl = [t['pnl_rupees'] for t in trades]
    wins = [v for v in pnl if v > 0]
    losses = [v for v in pnl if v <= 0]
    eq, peak, dd, dd_date = 0.0, 0.0, 0.0, None
    days: Dict[str, float] = {}
    for t in trades:
        days[t['date']] = days.get(t['date'], 0.0) + t['pnl_rupees']
    for d in sorted(days):
        eq += days[d]
        peak = max(peak, eq)
        if peak - eq > dd:
            dd, dd_date = peak - eq, d
    months: Dict[str, float] = {}
    years: Dict[str, Dict] = {}
    for t in trades:
        months[t['date'][:7]] = months.get(t['date'][:7], 0.0) + t['pnl_rupees']
        y = years.setdefault(t['date'][:4], {'year': t['date'][:4], 'trades': 0, 'wins': 0,
                                             'net': 0.0, 'charges': 0.0, 'nifty': 0.0, 'sensex': 0.0})
        y['trades'] += 1
        y['wins'] += t['pnl_rupees'] > 0
        y['net'] += t['pnl_rupees']
        y['charges'] += t['charges']
        y['nifty' if t['index'] == 'NIFTY' else 'sensex'] += t['pnl_rupees']
    for y in years.values():
        y['losing_months'] = sum(1 for m, v in months.items() if m[:4] == y['year'] and v < 0)
        y['return_pct'] = round(100 * y['net'] / capital, 2) if capital else None
        for k in ('net', 'charges', 'nifty', 'sensex'):
            y[k] = round(y[k], 2)
    by: Dict[str, Dict] = {}
    for t in trades:
        k = f"{t['index']} {t['window']}"
        b = by.setdefault(k, {'window': k, 'trades': 0, 'wins': 0, 'net': 0.0, 'stops': 0, 'locks': 0})
        b['trades'] += 1
        b['wins'] += t['pnl_rupees'] > 0
        b['net'] = round(b['net'] + t['pnl_rupees'], 2)
        b['stops'] += t['stops']
        b['locks'] += t['reason'] == 'Profit lock'
    yrs = max((end - start).days / 365.25, 1 / 365.25)
    total = sum(pnl)
    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t['reason']] = reasons.get(t['reason'], 0) + 1
    return {
        'total_trades': len(trades), 'wins': len(wins), 'losses': len(losses),
        'win_rate': round(100 * len(wins) / len(trades), 2) if trades else 0.0,
        'total_pnl_rupees': round(total, 2), 'gross': round(sum(t['gross'] for t in trades), 2),
        'total_charges': round(sum(t['charges'] for t in trades), 2),
        'avg_win': round(sum(wins) / len(wins), 2) if wins else 0.0,
        'avg_loss': round(sum(losses) / len(losses), 2) if losses else 0.0,
        'best_trade': round(max(pnl), 2) if pnl else 0.0, 'worst_trade': round(min(pnl), 2) if pnl else 0.0,
        'profit_factor': round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        'max_drawdown_rupees': round(dd, 2), 'max_drawdown_date': dd_date,
        'capital': capital, 'return_pct': round(100 * total / capital, 2) if capital else None,
        'annual_pct': round(100 * total / capital / yrs, 2) if capital else None,
        'calmar': round((total / yrs) / dd, 2) if dd > 0 else None,
        'months': len(months), 'losing_months': sum(1 for v in months.values() if v < 0),
        'worst_month': round(min(months.values()), 2) if months else 0.0,
        'leg_stops': sum(t['stops'] for t in trades), 'by_reason': reasons,
        'by_year': [years[k] for k in sorted(years)],
        'by_window': [by[k] for k in sorted(by)],
        'by_month': [{'month': k, 'net': round(v, 2)} for k, v in sorted(months.items())],
    }


# ── Find Best Params ─────────────────────────────────────────────────────────
# Only what the contracts already read can answer: the stop, both exits of every
# window, the profit lock (its level, and which index uses it) and which of the four
# windows trade. Entry times and hedge distance pick other strikes — other contracts —
# so they come from the page. A window's P&L depends only on (stop, its exit, its lock),
# so each window instance is run once per such key and a combo is a SUM of four
# windows' daily vectors: one call to run_window per key, never per combo.

SWEEP_GRID: Dict[str, Any] = {
    'sl_pct': [0.0, 30.0, 40.0, 50.0, 60.0, 75.0, 100.0],
    'am_out': ['10:00', '10:16', '10:30', '11:00'],
    'pm_out': ['15:00', '15:14', '15:25'],
    'lock': [(1000.0, 400.0), (1500.0, 600.0), (2000.0, 750.0), (3000.0, 1200.0)],   # (lock from, lock at) ₹/lot
}
WIN_KEYS = (('NIFTY', 'am', 'nf'), ('NIFTY', 'pm', 'nf'), ('SENSEX', 'am', 'sx'), ('SENSEX', 'pm', 'sx'))


def sweep_combos(grid: Dict = SWEEP_GRID) -> List[Dict[str, Any]]:
    """Every distinct combo: a window that is off has no exit, a lock no window uses has no
    level, so those are not multiplied out."""
    out = []
    import itertools
    opts = [[None] + list(grid['am_out' if w == 'am' else 'pm_out']) for _, w, _ in WIN_KEYS]
    for sl in grid['sl_pct']:
        for exits in itertools.product(*opts):
            if all(e is None for e in exits):
                continue
            nf_any = exits[0] is not None or exits[1] is not None
            sx_any = exits[2] is not None or exits[3] is not None
            for nf_t in ((False, True) if nf_any else (False,)):
                for sx_t in ((False, True) if sx_any else (False,)):
                    for li, lock in enumerate(grid['lock'] if (nf_t or sx_t) else [None]):
                        out.append({'sl_pct': sl, 'exits': exits, 'nf_trail': nf_t, 'sx_trail': sx_t,
                                    'lock': lock})
    return out


def sweep(plan: List[Tuple[date, str, date]], sessions: Sequence[date],
          spot_for: Callable[[str, date], Dict[str, Dict]],
          fetch_for: Callable[[str], Callable[[Dict, date], List[Dict]]],
          p: Dict, grid: Dict = SWEEP_GRID, progress=None) -> Dict[str, Any]:
    """Score every combo of `grid` on the plan. Returns {'rows': [...], 'windows': n,
    'unpriced': n}. Each row carries the settings (as page values) and its metrics."""
    import numpy as np
    ordered = sorted(set(sessions))
    pos = {d: i for i, d in enumerate(ordered)}
    memo: Dict[tuple, Dict] = {}

    def memo_fetch(idx):
        base = fetch_for(idx)

        def f(con, day):
            k = (idx, con['expiry'], con['strike'], con['option_type'], day)
            if k not in memo:
                memo[k] = index_bars(base(con, day))
            return memo[k]
        return f

    # The window instances the page's entry times give, each read once (a miss skips it).
    insts = []          # (win_no, day, idx, exp, t_in, spot)
    unpriced = 0
    for day, idx, exp in plan:
        key = 'nf' if idx == 'NIFTY' else 'sx'
        spot = spot_for(idx, day)
        for wn, (widx, win, wkey) in enumerate(WIN_KEYS):
            if widx != idx:
                continue
            t_in = p.get(f'{key}_{win}_in') or DEFAULTS[f'{key}_{win}_in']
            try:
                row = run_window(day, idx, exp, t_in, DEFAULTS[f'{key}_{win}_out'], spot, memo_fetch(idx),
                                 {**p, 'sl_pct': 0.0}, False, int(p.get(f'{key}_hedge_strikes') or 0),
                                 int(p.get(f'{key}_lots') or 1))
            except Exception:                            # noqa: BLE001
                row = {'skip': 'error'}
            if 'skip' in row:
                unpriced += 1
                continue
            insts.append((wn, day, idx, exp, t_in, spot))
    days = sorted({d for _, d, *_ in insts})
    nd = max(len(days), 1)
    day_ix = {d: i for i, d in enumerate(days)}
    months = sorted({d.strftime('%Y-%m') for d in days})
    month_of = np.array([months.index(d.strftime('%Y-%m')) for d in days], dtype=int) if days else np.zeros(0, int)

    # Per window type and key: (daily vector, n, wins, +sum, −sum).
    keys_by_win: Dict[int, Dict[tuple, Any]] = {}
    total = len(insts)
    done = 0
    for wn, (widx, win, wkey) in enumerate(WIN_KEYS):
        mine = [x for x in insts if x[0] == wn]
        if not mine:
            continue
        outs = grid['am_out' if win == 'am' else 'pm_out']
        acc: Dict[tuple, list] = {}
        for _, day, idx, exp, t_in, spot in mine:
            for sl in grid['sl_pct']:
                for out_t in outs:
                    if _mins(out_t) <= _mins(t_in):
                        continue
                    for lock in [None] + list(grid['lock']):
                        q = {**p, 'sl_pct': sl}
                        if lock is not None:
                            q['trail_at'], q['trail_lock'] = lock
                        r = run_window(day, idx, exp, t_in, out_t, spot, memo_fetch(idx), q,
                                       lock is not None, int(p.get(f'{wkey}_hedge_strikes') or 0),
                                       int(p.get(f'{wkey}_lots') or 1))
                        acc.setdefault((sl, out_t, lock), []).append((day_ix[day], r['pnl_rupees']))
            done += 1
            if progress:
                progress(done, total)
        keys_by_win[wn] = {}
        for k, vals in acc.items():
            ix = np.array([a for a, _ in vals], dtype=int)
            pn = np.array([b for _, b in vals], dtype=float)
            keys_by_win[wn][k] = (np.bincount(ix, weights=pn, minlength=nd), len(pn), int((pn > 0).sum()),
                                  float(pn[pn > 0].sum()), float(pn[pn <= 0].sum()), float(pn.min()),
                                  np.bincount(ix, minlength=nd))

    rows = []
    mid = nd // 2
    for c in sweep_combos(grid):
        parts = []
        ok = True
        for wn, (widx, win, wkey) in enumerate(WIN_KEYS):
            e = c['exits'][wn]
            if e is None:
                continue
            trail = c['nf_trail'] if wkey == 'nf' else c['sx_trail']
            got = keys_by_win.get(wn, {}).get((c['sl_pct'], e, c['lock'] if trail else None))
            if got is None:
                ok = False
                break
            parts.append(got)
        if not ok or not parts:
            continue
        vec = sum(x[0] for x in parts)
        n = sum(x[1] for x in parts)
        if n == 0:
            continue
        cum = np.cumsum(vec)
        peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
        dd = float((peak - cum).max()) if len(cum) else 0.0
        net = float(cum[-1]) if len(cum) else 0.0
        h1 = float(cum[mid - 1]) if mid else 0.0
        traded = sum(x[6] for x in parts)
        mon = np.bincount(month_of, weights=vec, minlength=len(months)) if len(months) else np.zeros(0)
        live = np.bincount(month_of, weights=traded, minlength=len(months)) > 0 if len(months) else np.zeros(0, bool)
        mon = mon[live]                                   # months it traded, as summarise counts them
        pos_s, neg_s = sum(x[3] for x in parts), sum(x[4] for x in parts)
        page = {}
        for wn, (widx, win, wkey) in enumerate(WIN_KEYS):
            e = c['exits'][wn]
            t_in = p.get(f'{wkey}_{win}_in') or DEFAULTS[f'{wkey}_{win}_in']
            page[f'{wkey}_{win}'] = f'{t_in}-{e}' if e else ''
        rows.append({
            **page, 'sl_pct': c['sl_pct'], 'nf_trail': c['nf_trail'], 'sx_trail': c['sx_trail'],
            'trail_at': c['lock'][0] if c['lock'] else None, 'trail_lock': c['lock'][1] if c['lock'] else None,
            'total_trades': n, 'wins': sum(x[2] for x in parts),
            'win_rate': round(100 * sum(x[2] for x in parts) / n, 1),
            'net_pnl_rupees': round(net, 2), 'max_drawdown_rupees': round(dd, 2),
            'net_dd': round(net / dd, 2) if dd > 0 else None,
            'profit_factor': round(pos_s / -neg_s, 2) if neg_s < 0 else None,
            'first_half': round(h1, 2), 'second_half': round(net - h1, 2),
            'losing_months': int((mon < 0).sum()), 'months': int(len(mon)),
            'worst_month': round(float(mon.min()), 2) if len(mon) else 0.0,
            'worst_trade': round(min(x[5] for x in parts), 2),
        })
    return {'rows': rows, 'windows': len(insts), 'unpriced': unpriced, 'days': len(days),
            'first_day': days[0].isoformat() if days else None,
            'mid_day': days[mid].isoformat() if days and mid < len(days) else None,
            'last_day': days[-1].isoformat() if days else None}


def board(rows: List[Dict], min_trades: int = 20, size: int = 100) -> List[Dict]:
    """Top `size` by net, by net ÷ drawdown (among rows earning at least a quarter of the
    best net) and by the weaker half, ranked by net ÷ drawdown."""
    rows = [r for r in rows if r['total_trades'] >= min_trades]
    if not rows:
        return []
    best = max(r['net_pnl_rupees'] for r in rows)
    keyf = lambda r: tuple((k, r[k]) for k in ('nf_am', 'nf_pm', 'sx_am', 'sx_pm', 'sl_pct', 'nf_trail',
                                               'sx_trail', 'trail_at'))
    pick: Dict[tuple, Dict] = {}
    for sel in (sorted(rows, key=lambda r: -r['net_pnl_rupees'])[:size],
                sorted((r for r in rows if r['net_pnl_rupees'] >= best / 4 and r['net_dd'] is not None),
                       key=lambda r: -r['net_dd'])[:size],
                sorted(rows, key=lambda r: -min(r['first_half'], r['second_half']))[:size]):
        for r in sel:
            pick[keyf(r)] = r
    # A lock that never armed leaves the row identical to the one without it: keep the
    # plainest (fewest locks, then the lowest level).
    same: Dict[tuple, Dict] = {}
    for r in sorted(pick.values(), key=lambda r: (int(r['nf_trail']) + int(r['sx_trail']), r['trail_at'] or 0)):
        k = (r['nf_am'], r['nf_pm'], r['sx_am'], r['sx_pm'], r['sl_pct'], r['total_trades'],
             r['net_pnl_rupees'], r['max_drawdown_rupees'], r['wins'])
        same.setdefault(k, r)
    return sorted(same.values(), key=lambda r: (-(r['net_dd'] or -1e9), -r['net_pnl_rupees']))
