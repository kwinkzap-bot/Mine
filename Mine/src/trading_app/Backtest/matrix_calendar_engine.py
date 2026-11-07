"""NIFTY "Matrix Calendar" — a weekly ratio calendar, zero adjustment, held 1-2 days.

The rule (added 2026-11-06, from a Hindi video by its author, who calls it half
iron condor, half diagonal calendar; net vega positive, so an IV spike does not
hurt it the way it hurts a condor):

* **When** — Monday at 15:16, only when ATM IV is above `min_iv` (20). A Monday
  holiday is no trade that week.
* **Weekly expiry** — not the next day's: the first weekly expiry at least
  `MIN_WEEKLY_DTE` (7) calendar days out, so Tuesday 8 days later under Tuesday
  expiries, Thursday 10 days later before 2025-09-01.
* **Monthly expiry** — the first monthly expiry after that weekly.
* **Legs** (4 sold, 4 bought), one set:
      SELL 2 × CE and 2 × PE at ≈`target_delta` (0.23), weekly, 100-step strikes only
      BUY  1 × CE `hedge_gap` (500) above and 1 × PE 500 below them, same weekly
      BUY  1 × CE and 1 × PE at the SOLD strikes, monthly
* **Exits**, on the position's mark-to-market: +`target_pct` (1.5 %) / −`stop_pct`
  (2 %) of the capital, or `max_hold_sessions` (2) sessions after the entry at
  `exit_hm` (15:15). Nothing is adjusted.

What the video leaves to judgement, and how it is pinned down here:

* **Delta.** Black-76 on the leg's own premium, with the forward taken from the
  ATM call and put at the entry bar (put-call parity), so no interest rate is
  assumed. ATM IV is the mean of that call's and put's, on the weekly sold.
* **"Not too big a premium difference."** The author skips a closer-to-0.23
  strike when it leaves the two sides' premiums far apart (0.26 over 0.21). So
  each side offers the 100-step strikes either side of the target delta, and
  `balance_premium` takes the pair whose premiums are closest; off, each side
  simply takes its nearest delta.
* **Capital** is the page's ₹ per set: there is no historical SPAN here. The
  author's "max loss ≈ ₹44,000 ≈ 19.5 %" makes one set about ₹2.25 L.

Prices are the repo's 5-minute conventions: a price "at" a time is the close of
the last bar finished by then (15:10 bar, closing 15:15, for 15:16); the exit is
read on 5-minute closes and books that close's real prices, not the level, so a
gap books past it. Bars come one frame per contract (`contract_window`), cached.

The module is pure: callers hand in the bars, so it never touches a broker.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import NormalDist
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from trading_app.Backtest import call_ratio_engine as cre
from trading_app.Backtest import option_selling_engine as ose

STRIKE_STEP = 100
MIN_WEEKLY_DTE = 7
MAX_WALK = 12                   # strikes tried per side, from the first guess
EXPIRY_HM = (15, 30)
_N = NormalDist()

DEFAULTS = {
    'entry_hm': (15, 16),
    'exit_hm': (15, 15),
    'min_iv': 20.0,             # % ATM IV; 0 = trade every Monday
    'target_delta': 0.23,
    'hedge_gap': 500,
    'balance_premium': True,
    'target_pct': 1.5,
    'stop_pct': 2.0,
    'max_hold_sessions': 2,
}

# (key, right, side, lots, book): the sold strike decides every other leg's.
LEGS = (
    ('sell_ce', 'CE', -1, 2, 'weekly'), ('sell_pe', 'PE', -1, 2, 'weekly'),
    ('hedge_ce', 'CE', 1, 1, 'weekly'), ('hedge_pe', 'PE', 1, 1, 'weekly'),
    ('cal_ce', 'CE', 1, 1, 'monthly'), ('cal_pe', 'PE', 1, 1, 'monthly'),
)


# Find Best Params. IV is a filter on the same trades, the deltas / gap / balance pick the
# strikes, and target / stop / hold replay one path. The floor starts at 18, not 0: a Monday
# below every floor costs 2 contracts rather than ~36, and IV 0 lost money in the backtest.
SWEEP_GRID = {
    'min_iv': (18, 20, 22, 25, 30),
    'target_delta': (0.20, 0.23, 0.26, 0.30),
    'hedge_gap': (300, 500, 700),
    'balance_premium': (True, False),
    'target_pct': (1.0, 1.5, 2.0, 2.5, 3.0),
    'stop_pct': (1.5, 2.0, 2.5, 3.0, 4.0),
    'max_hold_sessions': (1, 2, 3),
}
SWEEP_KEYS = tuple(SWEEP_GRID)
MIN_SWEEP_TRADES = 5


# ── calendar ─────────────────────────────────────────────────────────────────

def regular_sessions(sessions: Sequence[date], spot5: pd.Series) -> List[date]:
    """The sessions that opened at 09:15. Diwali's Muhurat session does not, and is no expiry day:
    Tue 2025-10-21 was one, and that week's contract expired on the 20th, so counting it asked Breeze
    for a contract that never existed. Opening is the test because an outage day is still a session:
    NSE halted from 11:40 on 2021-02-24, and asking for a 15:10 bar instead lost that day and held
    the 02-22 trade a session too long, into a stop."""
    opened = set(spot5.index[(spot5.index.hour == 9) & (spot5.index.minute == 15)].date)
    return [d for d in sessions if d in opened]


def entry_days(sessions: Sequence[date], start: date, end: date) -> List[date]:
    """Every Monday that is a session (a holiday Monday is no trade that week)."""
    return [d for d in sorted(set(sessions)) if d.weekday() == 0 and start <= d <= end]


def sold_expiry(entry: date, sessions: Sequence[date], listed: Sequence[date] = ()) -> Optional[date]:
    """The weekly expiry sold: the first at least MIN_WEEKLY_DTE days out."""
    sessions = list(sessions)
    exp = ose._weekly_on_or_after(entry + timedelta(days=MIN_WEEKLY_DTE), sessions)
    if exp is not None and sessions and exp > max(sessions):
        week = [d for d in listed if exp - timedelta(days=6) <= d <= exp]
        if week:
            return max(week)
    return exp


def monthly_expiry(weekly: date, sessions: Sequence[date], listed: Sequence[date] = ()) -> Optional[date]:
    """The monthly bought: strictly after the weekly, which may itself be a monthly."""
    return cre.contract_expiry(weekly, sessions, 1, listed)


def exit_day(entry: date, sessions: Sequence[date], hold: int) -> Optional[date]:
    """The session `hold` after the entry, or None when the data ends before it."""
    after = [d for d in sorted(set(sessions)) if d > entry]
    return after[hold - 1] if len(after) >= hold else None


# ── Black-76 ─────────────────────────────────────────────────────────────────

def _d1(fwd: float, strike: float, t: float, vol: float) -> float:
    return (math.log(fwd / strike) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))


def black_price(fwd: float, strike: float, t: float, vol: float, right: str) -> float:
    d1 = _d1(fwd, strike, t, vol)
    d2 = d1 - vol * math.sqrt(t)
    if right == 'CE':
        return fwd * _N.cdf(d1) - strike * _N.cdf(d2)
    return strike * _N.cdf(-d2) - fwd * _N.cdf(-d1)


def implied_vol(price: float, fwd: float, strike: float, t: float, right: str) -> Optional[float]:
    """Annual vol (0.2 = 20 %) by bisection; None when the premium has no time value."""
    intrinsic = max(0.0, fwd - strike) if right == 'CE' else max(0.0, strike - fwd)
    if t <= 0 or price <= intrinsic:
        return None
    lo, hi = 0.005, 5.0
    if black_price(fwd, strike, t, hi, right) < price:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if black_price(fwd, strike, t, mid, right) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def abs_delta(fwd: float, strike: float, t: float, vol: float, right: str) -> float:
    d1 = _d1(fwd, strike, t, vol)
    return _N.cdf(d1) if right == 'CE' else _N.cdf(-d1)


def strike_for_delta(fwd: float, t: float, vol: float, delta: float, right: str,
                     step: int = STRIKE_STEP) -> int:
    """The step strike nearest `delta` on a flat `vol`: the walk's first guess."""
    d1 = _N.inv_cdf(delta) if right == 'CE' else -_N.inv_cdf(delta)
    raw = fwd * math.exp(-d1 * vol * math.sqrt(t) + 0.5 * vol * vol * t)
    return int(round(raw / step) * step)


def years_to(entry_ts: pd.Timestamp, expiry: date) -> float:
    return (cre._at(expiry, EXPIRY_HM) - entry_ts).total_seconds() / (365.0 * 86400.0)


# ── strike selection ─────────────────────────────────────────────────────────

def atm_read(spot: float, entry_ts: pd.Timestamp, expiry: date,
             frame_for: Callable[..., Optional[pd.DataFrame]]) -> Dict:
    """Forward and ATM IV off the 100-step ATM call and put of the weekly sold."""
    k = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
    ce = ose.price_at(frame_for(expiry, k, 'CE'), entry_ts)
    pe = ose.price_at(frame_for(expiry, k, 'PE'), entry_ts)
    if ce is None or pe is None:
        return {'ok': False, 'reason': f'No ICICI premium at entry for the {k} ATM'}
    fwd = k + ce - pe
    t = years_to(entry_ts, expiry)
    ivs = [implied_vol(ce, fwd, k, t, 'CE'), implied_vol(pe, fwd, k, t, 'PE')]
    ivs = [v for v in ivs if v]
    if not ivs:
        return {'ok': False, 'reason': f'No ATM IV from the {k} premiums {ce} / {pe}'}
    return {'ok': True, 'fwd': fwd, 't': t, 'vol': sum(ivs) / len(ivs)}


def side_candidates(right: str, atm: Dict, target: float, entry_ts: pd.Timestamp, expiry: date,
                    frame_for: Callable[..., Optional[pd.DataFrame]]) -> List[Dict]:
    """The 100-step strikes either side of `target` delta, on each one's own IV.

    Starts from the flat-vol guess and walks at most MAX_WALK strikes, so a side
    costs 2-3 contracts rather than the whole ladder from the ATM."""
    fwd, t, vol = atm['fwd'], atm['t'], atm['vol']
    out_step = STRIKE_STEP if right == 'CE' else -STRIKE_STEP
    is_otm = (lambda s: s > fwd) if right == 'CE' else (lambda s: s < fwd)

    def read(k: int) -> Optional[Dict]:
        px = ose.price_at(frame_for(expiry, k, right), entry_ts)
        iv = None if px is None else implied_vol(px, fwd, k, t, right)
        return None if iv is None else {'strike': k, 'price': px, 'delta': abs_delta(fwd, k, t, iv, right)}

    k = strike_for_delta(fwd, t, vol, target, right)
    inside: Optional[Dict] = None       # delta at/above the target: nearer the money
    outside: Optional[Dict] = None      # below it: further out
    for _ in range(MAX_WALK):
        if not is_otm(k):
            k += out_step
            continue
        q = read(k)
        if q is None:
            break
        if q['delta'] >= target:
            inside = q
            if outside is not None:
                break
            k += out_step
        else:
            outside = q
            if inside is not None:
                break
            k -= out_step
    return [q for q in (inside, outside) if q is not None]


def choose_pair(ce: List[Dict], pe: List[Dict], target: float, balance: bool) -> tuple:
    """The (CE, PE) sold: closest premiums, or each side's nearest delta."""
    if balance:
        pairs = [(c, p) for c in ce for p in pe]
        return min(pairs, key=lambda x: (abs(x[0]['price'] - x[1]['price']),
                                         abs(x[0]['delta'] - target) + abs(x[1]['delta'] - target)))
    near = lambda side: min(side, key=lambda q: abs(q['delta'] - target))
    return near(ce), near(pe)


def build_legs(ce_strike: int, pe_strike: int, hedge_gap: int) -> Dict[str, Dict]:
    strikes = {'sell_ce': ce_strike, 'sell_pe': pe_strike,
               'hedge_ce': ce_strike + hedge_gap, 'hedge_pe': pe_strike - hedge_gap,
               'cal_ce': ce_strike, 'cal_pe': pe_strike}
    return {key: {'strike': strikes[key], 'right': right, 'weight': side * lots, 'book': book}
            for key, right, side, lots, book in LEGS}


# ── the hold ─────────────────────────────────────────────────────────────────

def build_path(entry_ts: pd.Timestamp, end_ts: Optional[pd.Timestamp], legs: Dict[str, Dict],
               entry_px: Dict[str, float], frames: Dict[str, Optional[pd.DataFrame]],
               spot5: pd.Series) -> Dict:
    """Every 5-minute bar closing after the entry and by `end_ts`: each leg's close
    (forward-filled, the entry price before its first print) and the per-unit MTM."""
    first = entry_ts.floor('5min')
    idx = spot5.index[spot5.index >= first]
    if end_ts is not None:
        idx = idx[idx + ose.BAR <= end_ts]
    closes: Dict[str, np.ndarray] = {}
    mtm = np.zeros(len(idx))
    for key, leg in legs.items():
        f = frames.get(key)
        if f is None or f.empty:
            col = np.full(len(idx), entry_px[key])
        else:
            c = f['close']
            c = c[c.index >= first]
            col = c.reindex(c.index.union(idx)).ffill().reindex(idx).fillna(entry_px[key]).to_numpy()
        closes[key] = col
        mtm += leg['weight'] * (col - entry_px[key])
    return {'idx': idx, 'closes': closes, 'mtm': mtm}


def first_exit(path: Dict, target_rupees: float, stop_rupees: float, qty: int,
               reached_cut: bool, n_bars: Optional[int] = None, memo: Optional[Dict] = None) -> tuple:
    """(bar position, reason). Position −1 = no bar after the entry at all.

    `n_bars` reads only the path's first bars (a shorter hold on a sweep's longest path) and
    `memo` keeps the first-hit positions, for a sweep asking one path many times."""
    n = len(path['mtm']) if n_bars is None else n_bars
    seen = {} if memo is None else memo
    if ('t', target_rupees) not in seen:
        seen[('t', target_rupees)] = cre._first(path['mtm'] * qty >= target_rupees)
    if ('s', stop_rupees) not in seen:
        seen[('s', stop_rupees)] = cre._first(path['mtm'] * qty <= -stop_rupees)
    hits = [(p, r) for p, r in ((seen[('t', target_rupees)], 'Target'), (seen[('s', stop_rupees)], 'Stop loss'))
            if 0 <= p < n]
    if hits:
        return min(hits)
    if not n:
        return -1, 'Open (data ends)'
    return n - 1, 'Time exit' if reached_cut else 'Open (data ends)'


def _open_monday(entry: date, sessions: Sequence[date], spot5: pd.Series,
                 frame_for: Callable[..., Optional[pd.DataFrame]], p: Dict) -> Dict:
    """Expiries, spot and the ATM read for one Monday, or {'skip': reason}. Both drivers start here."""
    listed = p.get('listed_expiries', ())
    if entry < ose.WEEKLY_SINCE:
        return {'skip': f'No NIFTY weekly contract before {ose.WEEKLY_SINCE.isoformat()}'}
    weekly = sold_expiry(entry, sessions, listed)
    monthly = monthly_expiry(weekly, sessions, listed) if weekly else None
    if weekly is None or monthly is None:
        return {'skip': 'No expiry resolved'}
    out: Dict = {'weekly': weekly, 'monthly': monthly}
    entry_ts = cre._at(entry, p['entry_hm'])
    spot = ose.price_at(spot5, entry_ts)
    if spot is None:
        return {**out, 'skip': 'No NIFTY bar at the entry time'}
    out.update({'entry_ts': entry_ts, 'spot': spot})
    atm = atm_read(spot, entry_ts, weekly, frame_for)
    if not atm['ok']:
        return {**out, 'skip': atm['reason']}
    return {**out, 'atm': atm, 'iv': 100.0 * atm['vol']}


def _structure(m: Dict, delta: float, hedge_gap: int, balance: bool,
               frame_for: Callable[..., Optional[pd.DataFrame]]) -> Dict:
    """The six legs and their entry prices for one choice of strikes, or {'skip': reason}."""
    entry_ts, weekly, monthly = m['entry_ts'], m['weekly'], m['monthly']
    ce = side_candidates('CE', m['atm'], delta, entry_ts, weekly, frame_for)
    pe = side_candidates('PE', m['atm'], delta, entry_ts, weekly, frame_for)
    if not ce or not pe:
        return {'skip': f"No {delta:g}-delta {'CE' if not ce else 'PE'} strike on the weekly"}
    sold_ce, sold_pe = choose_pair(ce, pe, delta, balance)
    legs = build_legs(sold_ce['strike'], sold_pe['strike'], int(hedge_gap))
    frames = {key: frame_for(weekly if leg['book'] == 'weekly' else monthly, leg['strike'], leg['right'])
              for key, leg in legs.items()}
    entry_px = {key: ose.price_at(frames[key], entry_ts) for key in legs}
    missing = [f"{legs[k]['strike']} {legs[k]['right']} {legs[k]['book']}" for k, v in entry_px.items() if v is None]
    if missing:
        return {'skip': f"No ICICI premium at entry for {', '.join(missing)}"}
    return {'legs': legs, 'frames': frames, 'entry_px': entry_px, 'sold_ce': sold_ce, 'sold_pe': sold_pe}


def run_trade(entry: date, sessions: Sequence[date], spot5: pd.Series,
              frame_for: Callable[..., Optional[pd.DataFrame]],
              qty: int, capital: float, params: Optional[Dict] = None) -> Dict:
    """One Monday from entry to exit; 'status' is traded | open | skipped.

    `frame_for(expiry, strike, right)` is that contract's 5-minute frame."""
    p = {**DEFAULTS, **(params or {})}
    m = _open_monday(entry, sessions, spot5, frame_for, p)
    base: Dict = {'entry_date': entry, **{k: m[k] for k in ('weekly', 'monthly', 'spot', 'iv') if k in m}}

    def skip(reason: str, **extra) -> Dict:
        return {**base, **extra, 'status': 'skipped', 'reason': reason}

    if 'skip' in m:
        return skip(m['skip'])
    if m['iv'] < p['min_iv']:
        return skip(f"ATM IV {m['iv']:.1f} below {p['min_iv']:g}", low_iv=True)
    st = _structure(m, p['target_delta'], p['hedge_gap'], p['balance_premium'], frame_for)
    if 'skip' in st:
        return skip(st['skip'])
    entry_ts, legs, frames, entry_px = m['entry_ts'], st['legs'], st['frames'], st['entry_px']
    sold_ce, sold_pe = st['sold_ce'], st['sold_pe']

    cut_day = exit_day(entry, sessions, int(p['max_hold_sessions']))
    cut = None if cut_day is None else cre._at(cut_day, p['exit_hm'])
    path = build_path(entry_ts, cut, legs, entry_px, frames, spot5)
    pos, reason = first_exit(path, capital * p['target_pct'] / 100.0, capital * p['stop_pct'] / 100.0,
                             qty, cut is not None)

    if pos < 0:
        exit_px, pts, exit_ts, upto = dict(entry_px), 0.0, entry_ts, np.zeros(0)
    else:
        exit_px = {k: float(path['closes'][k][pos]) for k in legs}
        pts = float(path['mtm'][pos])
        exit_ts = path['idx'][pos] + ose.BAR         # the close's real time
        upto = path['mtm'][:pos + 1] * qty
    held = sum(1 for d in set(sessions) if entry < d <= exit_ts.date())
    return {
        **base,
        'status': 'open' if reason.startswith('Open') else 'traded',
        'entry_ts': entry_ts, 'exit_ts': exit_ts, 'reason': reason,
        'legs': legs, 'entry_px': entry_px, 'exit_px': exit_px,
        'ce_delta': sold_ce['delta'], 'pe_delta': sold_pe['delta'],
        'net_points': -sum(legs[k]['weight'] * entry_px[k] for k in legs),
        'pnl_points': pts,
        'peak_rupees': float(max(0.0, upto.max())) if len(upto) else 0.0,
        'trough_rupees': float(min(0.0, upto.min())) if len(upto) else 0.0,
        'sessions_held': held,
    }


def score_monday(entry: date, sessions: Sequence[date], spot5: pd.Series,
                 frame_for: Callable[..., Optional[pd.DataFrame]], qty: int, capital: float,
                 base: Dict, brokerage_per_leg: float, grid: Dict = SWEEP_GRID) -> Dict[tuple, tuple]:
    """Every combo on one Monday: {combo (SWEEP_KEYS order): (status, net ₹, % of capital, reason)}.

    A floor above this Monday's IV has no entry at all, so a quiet Monday costs its two ATM
    contracts and the board's memory nothing; the caller counts it as skipped (`rank_board`)."""
    p = {**DEFAULTS, **base}
    m = _open_monday(entry, sessions, spot5, frame_for, p)
    floors = [f for f in grid['min_iv'] if 'iv' in m and m['iv'] >= f]
    if not floors:
        return {}
    exits = [(tp, sp, h) for tp in grid['target_pct'] for sp in grid['stop_pct'] for h in grid['max_hold_sessions']]
    cuts = {h: exit_day(entry, sessions, h) for h in grid['max_hold_sessions']}
    cuts = {h: (None if d is None else cre._at(d, p['exit_hm'])) for h, d in cuts.items()}
    longest = cuts[max(grid['max_hold_sessions'])]
    paths: Dict[tuple, tuple] = {}
    out: Dict[tuple, tuple] = {}
    charge = brokerage_per_leg * len(LEGS)
    for delta in grid['target_delta']:
        for gap in grid['hedge_gap']:
            for balance in grid['balance_premium']:
                st = _structure(m, delta, gap, balance, frame_for)
                if 'skip' in st:
                    scored = {ex: ('skipped', 0.0, 0.0, st['skip']) for ex in exits}
                else:
                    key = (st['legs']['sell_ce']['strike'], st['legs']['sell_pe']['strike'], gap)
                    if key not in paths:
                        paths[key] = (build_path(m['entry_ts'], longest, st['legs'], st['entry_px'],
                                                 st['frames'], spot5), {}, {})
                    path, memo, bars = paths[key]
                    scored = {}
                    for tp, sp, h in exits:
                        if h not in bars:
                            bars[h] = (len(path['idx']) if cuts[h] is None else
                                       int((path['idx'] + ose.BAR).searchsorted(cuts[h], side='right')))
                        pos, reason = first_exit(path, capital * tp / 100.0, capital * sp / 100.0, qty,
                                                 cuts[h] is not None, bars[h], memo)
                        pts = float(path['mtm'][pos]) if pos >= 0 else 0.0
                        net = pts * qty - charge
                        scored[(tp, sp, h)] = ('open' if reason.startswith('Open') else 'traded',
                                               net, 100.0 * net / capital, reason)
                for floor in floors:
                    for ex, r in scored.items():
                        out[(floor, delta, gap, balance) + ex] = r
    return out


def rank_board(per_monday: List[Dict[tuple, tuple]], mondays: int, min_trades: int = MIN_SWEEP_TRADES) -> List[Dict]:
    """One row per combo, best net ₹ first. `per_monday` is oldest first, for the drawdown; a Monday a
    combo has no result for was below its floor, so `skipped` is every Monday it did not hold."""
    rows = cre.rank(per_monday, min_trades=min_trades, keys=SWEEP_KEYS)
    for r in rows:
        r['skipped'] = mondays - r['total_trades'] - r['open']
    return rows


def trade_row(res: Dict, qty: int, sets: int, lot_size: int, capital: float,
              brokerage_per_leg: float) -> Dict:
    """Flatten a run_trade result into one grid row."""
    row: Dict = {
        'date': res['entry_date'].isoformat(),
        'expiry': res['weekly'].isoformat() if res.get('weekly') else None,
        'monthly': res['monthly'].isoformat() if res.get('monthly') else None,
        'spot': round(res.get('spot') or 0, 2) or None,
        'iv': round(res['iv'], 1) if res.get('iv') is not None else None,
        'status': res['status'],
        'sets': sets, 'lot_size': lot_size, 'qty': qty, 'capital': round(capital, 2),
    }
    if res['status'] == 'skipped':
        row.update({'exit_reason': res['reason'], 'result': 'SKIP', 'low_iv': bool(res.get('low_iv')),
                    'pnl': None, 'pnl_rupees': None, 'pnl_pct': None})
        return row
    for key, leg in res['legs'].items():
        row[f'{key}_strike'] = leg['strike']
        row[f'{key}_entry'] = round(res['entry_px'][key], 2)
        row[f'{key}_exit'] = round(res['exit_px'][key], 2)
    gross = res['pnl_points'] * qty
    brokerage = brokerage_per_leg * len(LEGS)
    net = gross - brokerage
    row.update({
        'entry_time': res['entry_ts'].isoformat(),
        'exit_time': res['exit_ts'].isoformat(),
        'sessions_held': res['sessions_held'],
        'ce_delta': round(res['ce_delta'], 3),
        'pe_delta': round(res['pe_delta'], 3),
        'net_points': round(res['net_points'], 2),
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
    """The call ratio's stats, oldest first so the drawdown is in time order."""
    out = cre.summarise(sorted(rows, key=lambda r: r['date']))
    out['skipped_low_iv'] = sum(1 for r in rows if r.get('low_iv'))
    out['by_year'] = by_year(rows)
    return out


def by_year(rows: List[Dict]) -> List[Dict]:
    """Every year's Mondays, oldest first, so a year with no trades shows why instead of being absent."""
    years: Dict[str, Dict] = {}
    for r in sorted(rows, key=lambda x: x['date']):
        y = years.setdefault(r['date'][:4], {'year': r['date'][:4], 'mondays': 0, 'low_iv': 0, 'no_data': 0, 'open': 0,
                                             'traded': 0, 'wins': 0, 'net': 0.0, 'ivs': []})
        y['mondays'] += 1
        if r.get('iv') is not None:
            y['ivs'].append(r['iv'])
        if r['status'] == 'traded':
            y['traded'] += 1
            y['wins'] += r['pnl_rupees'] > 0
            y['net'] += r['pnl_rupees']
        elif r['status'] == 'open':
            y['open'] += 1
        elif r.get('low_iv'):
            y['low_iv'] += 1
        else:
            y['no_data'] += 1
    out = []
    for y in years.values():
        ivs = y.pop('ivs')
        out.append({**y, 'net': round(y['net'], 2), 'iv_median': round(float(np.median(ivs)), 1) if ivs else None,
                    'iv_max': round(max(ivs), 1) if ivs else None})
    return out
