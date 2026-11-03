"""NIFTY "Monthly No-Brainer" — a 1:2 call ratio with a far hedge, held for weeks.

The rule (added 2026-11-03, from a Hindi interview — the strategy's author
trades it discretionarily, "zero adjustment"):

* **Entry day** — the last Friday of each month (the session on or before it
  when that Friday is a holiday), at 15:16. `entry_day='after_expiry'` takes
  the first session after the month's own monthly expiry instead (the same
  day under the old Thursday expiry).
* **Expiry** — NEXT month's monthly expiry: the first monthly expiry at least
  `min_days_to_expiry` (14) calendar days after the entry.
* **Legs** — on the entry spot, ATM rounded to the 100-step (the author avoids
  the 50-step strikes as illiquid):
      BUY  1 × CE at ATM + D
      SELL 2 × CE at ATM + D + `ratio_gap` (300)
      BUY  1 × CE at that sold strike + `hedge_gap` (1000)
  D starts at `min_distance` (300) and walks out in 100s while the position's
  DOWNSIDE credit — what it books if NIFTY falls and every leg expires
  worthless, 2·sell − buy − hedge — is above `max_credit_pct` (1 %) of the
  capital: a bigger credit means the strikes sit too close to the money. A
  downside DEBIT above `max_debit_pct` (1 %) is not traded at all.
* **Exits**, on the position's mark-to-market, read on 5-minute closes:
      Target  — MTM ≥ +`target_pct` (2.5 %) of the capital
      Stop    — MTM ≤ −`stop_pct` (3 %)
      Spot    — NIFTY closes a 5-minute bar at/above the bought strike (above it
                the ratio's naked short call dominates)
      Time    — `exit_sessions_before_expiry` (5) sessions before the expiry at
                `exit_hm` (15:15): the position never rides into the expiry
                week's gamma.
  Nothing is adjusted in between.

**Capital** is `capital_per_set` ₹ × sets (one set = 1 : 2 : 1 lots). There is
no historical SPAN here, so it is the page's number: the author's live trade
was 5 sets on ~₹6.6 L of margin. Every % above is on it.

Bars are 5-MINUTE (ICICI Breeze), one frame per contract over its whole life
(`contract_window`) — 2-3 Breeze requests a contract, where 1-minute bars cost
one per session and made a month ~60 requests (2026-11-03). Each leg's entry is
the CLOSE of the 5-minute bar holding the entry minute (15:15 bar for 15:16),
the spot for the ATM is NIFTY's close of that same bar, and every exit is at the
close of the bar that tripped it, all three legs forward-filled across a quiet
bar. A monthly far strike can be thin; a quote that would not fill at its print
is the slippage the author mentions (0.1-0.2 % of capital per trade).

`run_trade` (the BACKTEST button) and `score_expiry` (Find Best Params) book a
trade through the same `build_path` + `first_exit`, so a sweep row and the
backtest of its parameters agree.

The module is pure: callers hand in the bars, so it never touches a broker.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

STRIKE_STEP = 100
MAX_DISTANCE = 2000          # D never walks further than this from the ATM
MAX_ENTRY_LAG = pd.Timedelta(minutes=5)

DEFAULTS = {
    'entry_day': 'last_friday',          # | 'after_expiry'
    'entry_hm': (15, 16),
    'exit_hm': (15, 15),
    'min_days_to_expiry': 14,
    'min_distance': 300,
    'ratio_gap': 300,
    'hedge_gap': 1000,
    'max_credit_pct': 1.0,
    'max_debit_pct': 1.0,
    'target_pct': 2.5,
    'stop_pct': 3.0,
    'exit_sessions_before_expiry': 5,
    'spot_exit': True,
}

# (key, side): BUY +1, SELL −1 per unit; 'sell' is two lots for every one set.
LEGS = (('buy', 1, 1), ('sell', -1, 2), ('hedge', 1, 1))


def _frame(raw) -> Optional[pd.DataFrame]:
    """Bars as the Breeze adapter returns them → a minute-indexed OHLC frame."""
    if raw is None:
        return None
    if isinstance(raw, pd.DataFrame):
        df = raw
    else:
        if not raw:
            return None
        df = pd.DataFrame(raw).set_index('date')
    df = df[['open', 'high', 'low', 'close']].astype(float)
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, 'tz', None) is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def _at(day: date, hm) -> pd.Timestamp:
    return pd.Timestamp(datetime(day.year, day.month, day.day, *hm))


# ── calendar ─────────────────────────────────────────────────────────────────

def entry_days(sessions: Sequence[date], start: date, end: date,
               rule: str = 'last_friday') -> List[date]:
    """One entry session per calendar month inside [start, end]."""
    from trading_app.service.expiry_calendar import _month_expiry, expiry_weekday_for
    trading = sorted(set(sessions))
    out: List[date] = []
    month = start.replace(day=1)
    while month <= end:
        nxt = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        in_month = [d for d in trading if month <= d < nxt]
        pick: Optional[date] = None
        if in_month:
            if rule == 'after_expiry':
                exp = _month_expiry(month, expiry_weekday_for)
                settled = [d for d in in_month if d <= exp]
                after = [d for d in trading if d > (settled[-1] if settled else exp)]
                pick = after[0] if after and after[0] < nxt else None
            else:
                last_friday = nxt - timedelta(days=1)
                while last_friday.weekday() != 4:
                    last_friday -= timedelta(days=1)
                on_or_before = [d for d in in_month if d <= last_friday]
                # A holiday Friday moves to the session before it — not weeks back.
                if on_or_before and (last_friday - on_or_before[-1]).days <= 4:
                    pick = on_or_before[-1]
        if pick is not None and start <= pick <= end:
            out.append(pick)
        month = nxt
    return out


def contract_expiry(entry: date, sessions: Iterable[date],
                    min_days_to_expiry: int = 14,
                    listed: Sequence[date] = ()) -> Optional[date]:
    """The monthly expiry the position is opened in — next month's.

    A holiday is absorbed by the sessions, which only reach today, so an expiry
    still to come is snapped to the exchange's listed one in that week
    (`listed`, the broker master's dates): 2026-11-24 is Guru Nanak Jayanti and
    the November contract expires on the 23rd — asking Breeze for the 24th
    came back empty for every strike."""
    from trading_app.service.expiry_calendar import expiry_on_or_after
    sessions = list(sessions)
    exp = expiry_on_or_after(entry + timedelta(days=min_days_to_expiry), sessions,
                             cadence='monthly')
    if exp is not None and sessions and exp > max(sessions):
        week = [d for d in listed if exp - timedelta(days=6) <= d <= exp]
        if week:
            return max(week)
    return exp


def exit_day(expiry: date, sessions: Sequence[date], n_before: int) -> date:
    """The session `n_before` sessions ahead of the expiry. Past the sessions we
    hold, weekdays stand in for them (a run reaching up to today)."""
    days = sorted(d for d in set(sessions) if d < expiry)
    probe = (days[-1] if days else expiry - timedelta(days=40)) + timedelta(days=1)
    while probe < expiry:
        if probe.weekday() < 5:
            days.append(probe)
        probe += timedelta(days=1)
    if n_before <= 0 or not days:
        return expiry
    return days[-n_before] if len(days) >= n_before else days[0]


# ── strike selection ─────────────────────────────────────────────────────────

def atm(spot: float, step: int = STRIKE_STEP) -> int:
    return int(round(spot / step) * step)


def strikes_at(spot: float, distance: int, ratio_gap: int, hedge_gap: int) -> Dict[str, int]:
    buy = atm(spot) + distance
    return {'buy': buy, 'sell': buy + ratio_gap, 'hedge': buy + ratio_gap + hedge_gap}


def downside_points(prices: Dict[str, float]) -> float:
    """Per unit, what the position books if every leg expires worthless:
    positive = credit, negative = debit."""
    return 2 * prices['sell'] - prices['buy'] - prices['hedge']


def contract_window(expiry: date) -> tuple:
    """The range every contract of `expiry` is fetched over — from a few days
    before the PREVIOUS monthly expiry (the earliest any entry rule can open
    it) to the expiry itself. Canonical, so the backtest and the sweep share
    one disk-cache entry per contract."""
    from trading_app.service.expiry_calendar import _month_expiry, expiry_weekday_for
    prev = _month_expiry(expiry.replace(day=1) - timedelta(days=1), expiry_weekday_for)
    return prev - timedelta(days=3), expiry


def bar_at(frame: Optional[pd.DataFrame], ts: pd.Timestamp) -> Optional[int]:
    """Position of the 5-minute bar holding `ts` (or the last one before it,
    if no more than MAX_ENTRY_LAG old)."""
    if frame is None or frame.empty:
        return None
    pos = int(frame.index.searchsorted(ts, side='right')) - 1
    if pos < 0 or ts - frame.index[pos] > MAX_ENTRY_LAG:
        return None
    return pos


def _entry_price(frame: Optional[pd.DataFrame], entry_ts: pd.Timestamp) -> Optional[float]:
    pos = bar_at(frame, entry_ts)
    return None if pos is None else float(frame['close'].iloc[pos])     # type: ignore[index]


def choose_strikes(spot: float, entry_ts: pd.Timestamp,
                   frame_for: Callable[[int], Optional[pd.DataFrame]],
                   qty: int, capital: float, p: Dict,
                   price_cache: Optional[Dict[int, Optional[float]]] = None) -> Dict:
    """Walk D out from `min_distance` until the downside credit fits.

    `frame_for(strike)` is that CE's frame. Returns {'ok': True, 'distance',
    'strikes', 'prices', 'net_points', 'net_pct', 'tried'} or {'ok': False,
    'reason', ...}."""
    cache = {} if price_cache is None else price_cache

    def price(strike: int) -> Optional[float]:
        if strike not in cache:
            cache[strike] = _entry_price(frame_for(strike), entry_ts)
        return cache[strike]

    tried = []
    distance = max(STRIKE_STEP, int(p['min_distance']) // STRIKE_STEP * STRIKE_STEP)
    while distance <= MAX_DISTANCE:
        strikes = strikes_at(spot, distance, int(p['ratio_gap']), int(p['hedge_gap']))
        prices = {k: price(s) for k, s in strikes.items()}
        if any(v is None for v in prices.values()):
            missing = ', '.join(f"{strikes[k]} CE" for k, v in prices.items() if v is None)
            return {'ok': False, 'reason': f'No ICICI premium at entry for {missing}',
                    'distance': distance, 'strikes': strikes, 'tried': tried}
        net = downside_points(prices)                       # type: ignore[arg-type]
        net_pct = 100.0 * net * qty / capital
        tried.append({'distance': distance, 'net_pct': round(net_pct, 2)})
        if net_pct <= p['max_credit_pct']:
            if -net_pct > p['max_debit_pct']:
                return {'ok': False,
                        'reason': f'Downside debit {-net_pct:.2f}% > {p["max_debit_pct"]}% — not deployed',
                        'distance': distance, 'strikes': strikes, 'prices': prices, 'tried': tried}
            return {'ok': True, 'distance': distance, 'strikes': strikes, 'prices': prices,
                    'net_points': net, 'net_pct': net_pct, 'tried': tried}
        distance += STRIKE_STEP
    return {'ok': False, 'reason': f'Credit still above {p["max_credit_pct"]}% at {MAX_DISTANCE} pts out',
            'tried': tried}


# ── the hold ─────────────────────────────────────────────────────────────────

def open_month(entry: date, sessions: Sequence[date], spot5: pd.Series, p: Dict) -> Dict:
    """Expiry, entry bar and entry spot for one entry day — or a skip."""
    expiry = contract_expiry(entry, sessions, p['min_days_to_expiry'], p.get('listed_expiries', ()))
    out = {'entry_date': entry, 'expiry': expiry}
    if expiry is None:
        return {**out, 'ok': False, 'reason': 'No monthly expiry resolved'}
    entry_ts = _at(entry, p['entry_hm'])
    pos = bar_at(spot5.to_frame('close') if len(spot5) else None, entry_ts)
    if pos is None:
        return {**out, 'ok': False, 'reason': 'No NIFTY bar at the entry time'}
    return {**out, 'ok': True, 'entry_ts': entry_ts, 'spot': float(spot5.iloc[pos])}


def build_path(entry_ts: pd.Timestamp, end_ts: pd.Timestamp, strikes: Dict[str, int],
               entry_px: Dict[str, float], frames: Dict[str, Optional[pd.DataFrame]],
               spot5: pd.Series) -> Dict:
    """Every 5-minute bar AFTER the entry bar up to `end_ts`: the three legs'
    closes (forward-filled, the entry price before a leg's first print), the
    per-unit MTM in points and NIFTY's close."""
    grid = spot5[(spot5.index > entry_ts.floor('5min')) & (spot5.index <= end_ts)]
    idx = grid.index
    legs = {}
    for k in strikes:
        f = frames.get(k)
        if f is None or f.empty:
            legs[k] = np.full(len(idx), entry_px[k])
        else:
            col = f['close']
            col = col[col.index > entry_ts.floor('5min')]
            legs[k] = col.reindex(col.index.union(idx)).ffill().reindex(idx).fillna(entry_px[k]).to_numpy()
    weights = {k: side * lots for k, side, lots in LEGS}
    mtm = np.zeros(len(idx))
    for k, w in weights.items():
        mtm += w * (legs[k] - entry_px[k])
    return {'idx': idx, 'legs': legs, 'mtm': mtm, 'spot': grid.to_numpy(),
            'days': np.array([ts.date() for ts in idx])}


def _first(mask: np.ndarray) -> int:
    return int(mask.argmax()) if mask.any() else -1


def first_exit(path: Dict, buy_strike: int, target_rupees: float, stop_rupees: float,
               qty: int, spot_exit: bool, cut_ts: pd.Timestamp,
               hits: Optional[Dict] = None) -> tuple:
    """(bar position, reason) of the exit — or (last position, 'Open (data
    ends)') when the time exit was not reached and nothing tripped. Position
    −1 means no bar after the entry at all. `hits` memoises the first-hit
    positions for a sweep that asks the same path many times."""
    idx = path['idx']
    n_cut = int(idx.searchsorted(cut_ts, side='right'))      # bars allowed: [0, n_cut)
    memo = {} if hits is None else hits
    key_t, key_s = ('t', target_rupees), ('s', stop_rupees)
    if key_t not in memo:
        memo[key_t] = _first(path['mtm'] * qty >= target_rupees)
    if key_s not in memo:
        memo[key_s] = _first(path['mtm'] * qty <= -stop_rupees)
    if ('x',) not in memo:
        memo[('x',)] = _first(path['spot'] >= buy_strike)
    cands = [(memo[key_t], 0, 'Target'), (memo[key_s], 1, 'Stop loss')]
    if spot_exit:
        cands.append((memo[('x',)], 2, 'Spot crossed buy strike'))
    hit = min(((pos, rank, r) for pos, rank, r in cands if 0 <= pos < n_cut), default=None)
    if hit:
        return hit[0], hit[2]
    if n_cut == 0:
        return -1, 'Open (data ends)'
    last = n_cut - 1
    if idx[last].date() == cut_ts.date():
        return last, 'Time exit'
    return last, 'Open (data ends)'


def cut_time(expiry: date, sessions: Sequence[date], p: Dict) -> pd.Timestamp:
    return _at(exit_day(expiry, sessions, int(p['exit_sessions_before_expiry'])), p['exit_hm'])


def run_trade(entry: date, sessions: Sequence[date], spot5: pd.Series,
              frame_for: Callable[[date, int], Optional[pd.DataFrame]],
              qty: int, capital: float, params: Optional[Dict] = None) -> Dict:
    """One month's position from entry to exit.

    `spot5` is NIFTY's 5-minute closes over the whole run, `frame_for(expiry,
    strike)` that CE's 5-minute frame over `contract_window(expiry)`. Returns
    a dict with 'status' in traded | open | skipped."""
    p = {**DEFAULTS, **(params or {})}
    m = open_month(entry, sessions, spot5, p)
    base = {'entry_date': entry, 'expiry': m['expiry']}
    if not m['ok']:
        return {**base, 'status': 'skipped', 'reason': m['reason']}
    expiry, entry_ts, spot = m['expiry'], m['entry_ts'], m['spot']
    pick = choose_strikes(spot, entry_ts, lambda k: frame_for(expiry, k), qty, capital, p)
    base.update({'spot': spot, 'tried': pick.get('tried', [])})
    if not pick['ok']:
        return {**base, 'status': 'skipped', 'reason': pick['reason'],
                'strikes': pick.get('strikes')}
    strikes, entry_px = pick['strikes'], pick['prices']
    cut = cut_time(expiry, sessions, p)
    path = build_path(entry_ts, cut, strikes, entry_px,
                      {k: frame_for(expiry, s) for k, s in strikes.items()}, spot5)
    pos, reason = first_exit(path, strikes['buy'], capital * p['target_pct'] / 100.0,
                             capital * p['stop_pct'] / 100.0, qty, p['spot_exit'], cut)
    return {**base, **_booked(path, pos, reason, entry_ts, strikes, entry_px, pick, qty)}


def _booked(path: Dict, pos: int, reason: str, entry_ts: pd.Timestamp, strikes: Dict,
            entry_px: Dict, pick: Dict, qty: int) -> Dict:
    if pos < 0:
        exit_px, pts, exit_ts, upto = dict(entry_px), 0.0, entry_ts, np.zeros(0)
    else:
        exit_px = {k: float(path['legs'][k][pos]) for k in strikes}
        pts = float(path['mtm'][pos])
        exit_ts = path['idx'][pos]
        upto = path['mtm'][:pos + 1] * qty
    return {
        'status': 'open' if reason.startswith('Open') else 'traded',
        'entry_ts': entry_ts, 'exit_ts': exit_ts, 'reason': reason,
        'distance': pick['distance'], 'strikes': strikes,
        'entry_px': entry_px, 'exit_px': exit_px,
        'net_points': pick['net_points'], 'net_pct': pick['net_pct'],
        'pnl_points': pts,
        'peak_rupees': float(max(0.0, upto.max())) if len(upto) else 0.0,
        'trough_rupees': float(min(0.0, upto.min())) if len(upto) else 0.0,
        'sessions_held': len({entry_ts.date(), *(path['days'][:pos + 1] if pos >= 0 else [])}),
    }


# ── Find Best Params ─────────────────────────────────────────────────────────
# Every box on the page but the times, sets, capital and max debit. Strikes
# depend only on (entry day, min distance, sell gap, hedge gap, max credit), so
# each month walks those 216 structures, prices each distinct strike set once,
# and replays the 160 exit combos on that one path.
SWEEP_GRID = {
    'entry_day': ('last_friday', 'after_expiry'),
    'min_distance': (200, 300, 400, 500),
    'ratio_gap': (200, 300, 400),
    'hedge_gap': (700, 1000, 1500),
    'max_credit_pct': (0.5, 1.0, 1.5),
    'target_pct': (1.5, 2.0, 2.5, 3.0, 4.0),
    'stop_pct': (2.0, 3.0, 4.0, 5.0),
    'exit_sessions_before_expiry': (0, 3, 5, 8),
    'spot_exit': (True, False),
}
STRUCTURE_KEYS = ('entry_day', 'min_distance', 'ratio_gap', 'hedge_gap', 'max_credit_pct')
EXIT_KEYS = ('target_pct', 'stop_pct', 'exit_sessions_before_expiry', 'spot_exit')


def sweep_size(grid: Dict = SWEEP_GRID) -> int:
    n = 1
    for v in grid.values():
        n *= len(v)
    return n


def score_expiry(entries: Dict[str, date], sessions: Sequence[date], spot5: pd.Series,
                 frame_for: Callable[[date, int], Optional[pd.DataFrame]],
                 qty: int, capital: float, base: Dict, brokerage: float,
                 grid: Dict = SWEEP_GRID) -> Dict[tuple, tuple]:
    """Every combo's result on ONE expiry: {combo: (status, net ₹, % of capital, reason)}.

    `entries` maps each entry-day rule to its entry date for this expiry."""
    import itertools
    p0 = {**DEFAULTS, **base}
    out: Dict[tuple, tuple] = {}
    exit_combos = list(itertools.product(*(grid[k] for k in EXIT_KEYS)))
    for rule in grid['entry_day']:
        entry = entries.get(rule)
        if entry is None:
            continue
        m = open_month(entry, sessions, spot5, p0)
        cuts = {}
        prices: Dict[int, Optional[float]] = {}
        paths: Dict[tuple, tuple] = {}
        for md, rg, hg, mc in itertools.product(grid['min_distance'], grid['ratio_gap'],
                                                grid['hedge_gap'], grid['max_credit_pct']):
            skey = (rule, md, rg, hg, mc)
            if not m['ok']:
                for ex in exit_combos:
                    out[skey + ex] = ('skipped', 0.0, 0.0, m['reason'])
                continue
            expiry = m['expiry']
            p = {**p0, 'min_distance': md, 'ratio_gap': rg, 'hedge_gap': hg, 'max_credit_pct': mc}
            pick = choose_strikes(m['spot'], m['entry_ts'], lambda k: frame_for(expiry, k),
                                  qty, capital, p, price_cache=prices)
            if not pick['ok']:
                for ex in exit_combos:
                    out[skey + ex] = ('skipped', 0.0, 0.0, pick['reason'])
                continue
            strikes = pick['strikes']
            tkey = (strikes['buy'], strikes['sell'], strikes['hedge'])
            if tkey not in paths:
                end = _at(expiry, p0['exit_hm'])
                paths[tkey] = (build_path(m['entry_ts'], end, strikes, pick['prices'],
                                          {k: frame_for(expiry, s) for k, s in strikes.items()},
                                          spot5), {})
            path, hits = paths[tkey]
            for ex in exit_combos:
                tp, sp, nb, sx = ex
                if nb not in cuts:
                    cuts[nb] = cut_time(expiry, sessions, {**p0, 'exit_sessions_before_expiry': nb})
                pos, reason = first_exit(path, strikes['buy'], capital * tp / 100.0,
                                         capital * sp / 100.0, qty, sx, cuts[nb], hits)
                pts = float(path['mtm'][pos]) if pos >= 0 else 0.0
                net = pts * qty - brokerage * len(LEGS)
                status = 'open' if reason.startswith('Open') else 'traded'
                out[skey + ex] = (status, net, 100.0 * net / capital, reason)
    return out


def rank(per_expiry: List[Dict[tuple, tuple]], min_trades: int = 1,
         grid: Dict = SWEEP_GRID) -> List[Dict]:
    """Fold every expiry's results into one row per combo, best net ₹ first.
    Only closed trades are scored; open and skipped months are counted."""
    keys = STRUCTURE_KEYS + EXIT_KEYS
    combos: Dict[tuple, List[tuple]] = {}
    for res in per_expiry:                     # oldest expiry first, for the drawdown
        for combo, r in res.items():
            combos.setdefault(combo, []).append(r)
    rows = []
    for combo, rs in combos.items():
        closed = [r for r in rs if r[0] == 'traded']
        if len(closed) < min_trades:
            continue
        net = [r[1] for r in closed]
        wins = [x for x in net if x > 0]
        losses = [x for x in net if x < 0]
        peak = eq = dd = 0.0
        for x in net:
            eq += x
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        gl = abs(sum(losses))
        exits: Dict[str, int] = {}
        for r in closed:
            exits[r[3]] = exits.get(r[3], 0) + 1
        rows.append({
            **dict(zip(keys, combo)),
            'total_trades': len(closed),
            'skipped': sum(1 for r in rs if r[0] == 'skipped'),
            'open': sum(1 for r in rs if r[0] == 'open'),
            'win_rate': round(100.0 * len(wins) / len(closed), 1),
            'net_pnl_rupees': round(sum(net), 2),
            'total_pct': round(sum(r[2] for r in closed), 2),
            'avg_pct': round(sum(r[2] for r in closed) / len(closed), 2),
            'worst_pct': round(min(r[2] for r in closed), 2),
            'profit_factor': round(sum(wins) / gl, 2) if gl else (999.0 if wins else 0.0),
            'max_drawdown_rupees': round(dd, 2),
            'exits': exits,
        })
    rows.sort(key=lambda r: (r['net_pnl_rupees'], -r['max_drawdown_rupees']), reverse=True)
    return rows


def trade_row(res: Dict, qty: int, sets: int, lot_size: int, capital: float,
              brokerage_per_leg: float) -> Dict:
    """Flatten a run_trade result into one grid row (₹ at `qty` per set-unit)."""
    row: Dict = {
        'date': res['entry_date'].isoformat(),
        'expiry': res['expiry'].isoformat() if res.get('expiry') else None,
        'spot': round(res.get('spot') or 0, 2) or None,
        'status': res['status'],
        'sets': sets, 'lot_size': lot_size, 'qty': qty, 'capital': round(capital, 2),
    }
    if res['status'] == 'skipped':
        strikes = res.get('strikes') or {}
        row.update({'exit_reason': res['reason'], 'result': 'SKIP',
                    'buy_strike': strikes.get('buy'), 'sell_strike': strikes.get('sell'),
                    'hedge_strike': strikes.get('hedge'),
                    'pnl': None, 'pnl_rupees': None, 'pnl_pct': None})
        return row
    for k, _, _ in LEGS:
        row[f'{k}_strike'] = res['strikes'][k]
        row[f'{k}_entry'] = round(res['entry_px'][k], 2)
        row[f'{k}_exit'] = round(res['exit_px'][k], 2)
    gross = res['pnl_points'] * qty
    brokerage = brokerage_per_leg * len(LEGS)
    net = gross - brokerage
    row.update({
        'distance': res['distance'],
        'entry_time': res['entry_ts'].isoformat(),
        'exit_time': res['exit_ts'].isoformat(),
        'sessions_held': res['sessions_held'],
        'credit_pct': round(res['net_pct'], 2),
        'exit_reason': res['reason'],
        'pnl': round(res['pnl_points'], 2),
        'gross_pnl_rupees': round(gross, 2),
        'brokerage': round(brokerage, 2),
        'pnl_rupees': round(net, 2),
        'pnl_pct': round(100.0 * net / capital, 2),
        'peak_pct': round(100.0 * res['peak_rupees'] / capital, 2),
        'trough_pct': round(100.0 * res['trough_rupees'] / capital, 2),
        'result': 'OPEN' if res['status'] == 'open' else
                  ('WIN' if net > 0 else ('LOSS' if net < 0 else 'SCRATCH')),
    })
    return row


def summarise(rows: List[Dict]) -> Dict:
    """Stats on the CLOSED trades' net ₹ (open and skipped months are counted, not scored)."""
    closed = [r for r in rows if r['status'] == 'traded']
    net = [r['pnl_rupees'] for r in closed]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x < 0]
    peak = equity = max_dd = 0.0
    for x in net:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    gross_loss = abs(sum(losses))
    n = len(closed)
    reasons: Dict[str, int] = {}
    for r in closed:
        reasons[r['exit_reason']] = reasons.get(r['exit_reason'], 0) + 1
    return {
        'total_trades': n,
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(100.0 * len(wins) / n, 2) if n else 0.0,
        'total_pnl_rupees': round(sum(net), 2),
        'total_gross_pnl_rupees': round(sum(r['gross_pnl_rupees'] for r in closed), 2),
        'total_brokerage': round(sum(r['brokerage'] for r in closed), 2),
        'total_pnl_pct': round(sum(r['pnl_pct'] for r in closed), 2),
        'avg_pnl_pct': round(sum(r['pnl_pct'] for r in closed) / n, 2) if n else 0.0,
        'avg_win_rupees': round(sum(wins) / len(wins), 2) if wins else 0.0,
        'avg_loss_rupees': round(gross_loss / len(losses), 2) if losses else 0.0,
        'profit_factor': round(sum(wins) / gross_loss, 2) if gross_loss else (999.0 if wins else 0.0),
        'max_drawdown_rupees': round(max_dd, 2),
        'avg_sessions_held': round(sum(r['sessions_held'] for r in closed) / n, 1) if n else 0.0,
        'exit_reasons': reasons,
        'open_trades': sum(1 for r in rows if r['status'] == 'open'),
        'skipped_months': sum(1 for r in rows if r['status'] == 'skipped'),
    }
