"""NIFTY "Bi-weekly Iron Condor" — a Friday iron condor on the weekly after next, delta-adjusted.

The rule (added 2026-11-10, from a Hindi video by its author, who trades it on the
bi-weekly expiry because it decays faster than the weekly with less gamma):

* **When** — Friday at 15:15 (the week's last session when Friday is a holiday),
  only while India VIX is below `max_vix` (13): calm weeks, no big gaps.
* **Expiry** — the "bi-weekly": the second weekly expiry after the entry, so Tuesday
  11 days later under Tuesday expiries (Fri 2025-10-31 → Tue 11-11), Thursday 13 days
  later before 2025-09-01.
* **Legs**, one set: SELL 1 CE and 1 PE at ≈`target_delta` (0.20), 100-step strikes
  only; BUY 1 CE `hedge_gap` (200) above and 1 PE 200 below them.
* **Credit floor** — the net credit must be at least `min_credit_pct` (4 %) of the
  capital, or the week is not traded.
* **Exits**, on the position's mark-to-market: +`target_pct` / −`stop_pct` (1 % / 1 %)
  of the capital, or the next Friday at `exit_hm` (15:00) so the next trade can open.
  The author also closes at the day's end rather than carry a near-target profit
  (≥ `eod_profit_pct`, 0.8 %) or a near-stop loss (≥ `eod_loss_pct`, 0.9 %) overnight.
* **Adjustments** (the author's "adjustment trigger points"), on 5-minute closes. A
  side is its short and its hedge, closed and re-opened together:
    1. a short premium must not double (`double_x` 2 × what it was sold for);
    2. a short's delta must not reach `max_delta` (0.30);
    3. one side's delta must not exceed `skew_x` (2) × the other side's;
  each moves the TESTED side out to ≈0.20 delta again. And
    4. a short that has decayed `roll_decay_pct` (50 %) is rolled IN to ≈0.20 delta
       (the untested side, which is how the condor re-centres).

What the video leaves to judgement, and how it is pinned down here:

* **Delta.** Black-76 on the leg's own premium. The forward is taken from the ATM call
  and put at the entry (put-call parity, so no interest rate), and afterwards is NIFTY
  plus that entry basis, shrunk with the time left.
* **Balanced premiums.** At the entry each side takes its nearest-delta 100-step strike;
  when the two premiums are more than 20 % apart (the author's 38 against 58), the pair
  of bracketing strikes with the closest premiums is taken instead (he sells 0.23 over
  0.20 to stay near the other side). A side rolled in takes the strike whose premium is
  nearest the other side's; a side moved out takes its nearest delta.
* **Rule 3 when the high side is already at 0.20.** The author leaves it ("it is 0.2
  anyway") and fixes the low side. So the high side moves out only if a ≈0.20 strike
  is further out than it; otherwise the low side is rolled in.
* **Capital** is the page's ₹ per set: there is no historical SPAN here. The author's
  10 lots on ₹9.6 L make one set (1 lot) about ₹96,000.

Prices are the repo's 5-minute conventions: a price "at" a time is the close of the last
bar finished by then (the 15:10 bar, closing 15:15, for 15:15); exits and adjustments
are read on 5-minute closes and book that close's real prices, not the level, so a gap
books past it. The adjustments depend only on prices, never on the P&L, so one path per
structure serves every target / stop / day-end combination of the sweep.

The module is pure: callers hand in the bars, so it never touches a broker.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from trading_app.Backtest import call_ratio_engine as cre
from trading_app.Backtest import option_selling_engine as ose

STRIKE_STEP = 100
MAX_WALK = 12                   # strikes tried per side, from the first guess
EXPIRY_HM = (15, 30)
SIDES = ('CE', 'PE')
_SQRT2 = math.sqrt(2.0)

DEFAULTS = {
    'entry_hm': (15, 15),
    'exit_hm': (15, 0),         # on the next week's last session
    'max_vix': 13.0,            # India VIX at the entry must be below this; 0 = off
    'max_rv': 10.0,             # NIFTY's realised vol over the last 10 sessions (annualised %) must be below this; 0 = off
    'max_week_move': 2.0,       # … and its move over the last 5 sessions (|%|) below this; 0 = off
    'target_delta': 0.20,
    'hedge_gap': 200,
    'balance_premium': True,
    'min_credit_pct': 4.0,      # net credit ₹ as % of capital; 0 = off
    'capital_pct': 0.0,         # capital = this % of NIFTY at the entry × qty (the margin moves with the index); 0 = the fixed figure
    'target_pct': 1.0,
    'stop_pct': 1.0,
    'eod_profit_pct': 0.8,      # at `eod_hm`, a profit at least this is taken; 0 = off
    'eod_loss_pct': 0.9,        # … and a loss at least this is cut; 0 = off
    'eod_hm': (15, 15),
    'adjust': True,
    'double_x': 2.0,
    'max_delta': 0.30,
    'skew_x': 2.0,
    'roll_decay_pct': 50.0,
    'max_adjust': 10,           # side re-centres per trade
}


# Find Best Params. VIX and the credit floor filter the same trades; delta / gap / adjust
# build a path (adjustments read prices only), and target / stop / day-end replay it. The
# VIX ceiling has no "off": a week above every ceiling costs its 2 ATM contracts, not ~12.
SWEEP_GRID = {
    'max_vix': (12, 13, 14, 16),
    'target_delta': (0.15, 0.20, 0.25),
    'hedge_gap': (200, 300),
    'adjust': (True, False),
    'min_credit_pct': (0, 3, 4),
    'target_pct': (0.75, 1.0, 1.5, 2.0),
    'stop_pct': (0.75, 1.0, 1.5, 2.0),
    'day_end': (True, False),   # the 0.8 % / 0.9 % day-end exits, on or off together
    'calm': (True, False),      # the calm-market filter (max_rv / max_week_move), on or off together
}
SWEEP_KEYS = tuple(SWEEP_GRID)
MIN_SWEEP_TRADES = 5


# ── calendar ─────────────────────────────────────────────────────────────────

def regular_sessions(sessions: Sequence[date], spot5: pd.Series) -> List[date]:
    """The sessions that opened at 09:15 (a Muhurat or halted day is no entry or expiry day)."""
    opened = set(spot5.index[(spot5.index.hour == 9) & (spot5.index.minute == 15)].date)
    return [d for d in sessions if d in opened]


def entry_days(sessions: Sequence[date], start: date, end: date) -> List[date]:
    """Each week's last session if it is a Thursday or Friday (Friday, or Thursday on a Friday holiday)."""
    weeks: Dict[tuple, date] = {}
    for d in sorted(set(sessions)):
        weeks[d.isocalendar()[:2]] = d
    return [d for d in sorted(weeks.values()) if d.weekday() in (3, 4) and start <= d <= end]


def sold_expiry(entry: date, sessions: Sequence[date], listed: Sequence[date] = ()) -> Optional[date]:
    """The bi-weekly: the second weekly expiry after the entry session."""
    sessions = list(sessions)
    first = ose._weekly_on_or_after(entry + timedelta(days=1), sessions)
    exp = None if first is None else ose._weekly_on_or_after(first + timedelta(days=1), sessions)
    if exp is not None and sessions and exp > max(sessions):
        week = [d for d in listed if exp - timedelta(days=6) <= d <= exp]
        if week:
            return max(week)
    return exp


def exit_day(entry: date, expiry: date, sessions: Sequence[date]) -> Optional[date]:
    """The next week's last session (the day before the expiry at the latest), or None while
    the data stops inside that week."""
    sessions = sorted(set(sessions))
    bound = min(entry + timedelta(days=7), expiry - timedelta(days=1))
    later = [d for d in sessions if entry < d <= bound]
    if not later:
        return None
    if sessions[-1] < bound and later[-1] == sessions[-1]:
        return None
    return later[-1]


def contract_window(expiry: date) -> tuple:
    """The range a contract is fetched over. The shared window (cre.contract_window) where it
    reaches the entry; a Thursday weekly is sold 13 days out, past its 12-day window, so it
    gets 16 days. Deterministic per expiry, so a re-run hits the disk cache."""
    frm, to = cre.contract_window(expiry)
    if frm <= expiry - timedelta(days=13):
        return frm, to
    if expiry.weekday() in (0, 1):          # a Tuesday (or holiday Monday) weekly: Friday is 10-11 days back
        return frm, to
    return expiry - timedelta(days=16), expiry


# ── Black-76 ─────────────────────────────────────────────────────────────────

def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _d1(fwd: float, strike: float, t: float, vol: float) -> float:
    return (math.log(fwd / strike) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))


def black_price(fwd: float, strike: float, t: float, vol: float, right: str) -> float:
    d1 = _d1(fwd, strike, t, vol)
    d2 = d1 - vol * math.sqrt(t)
    if right == 'CE':
        return fwd * _ncdf(d1) - strike * _ncdf(d2)
    return strike * _ncdf(-d2) - fwd * _ncdf(-d1)


def implied_vol(price: float, fwd: float, strike: float, t: float, right: str) -> Optional[float]:
    """Annual vol (0.2 = 20 %) by bisection; None when the premium has no time value."""
    intrinsic = max(0.0, fwd - strike) if right == 'CE' else max(0.0, strike - fwd)
    if t <= 0 or price <= intrinsic or fwd <= 0:
        return None
    lo, hi = 0.005, 5.0
    if black_price(fwd, strike, t, hi, right) < price:
        return None
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if black_price(fwd, strike, t, mid, right) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def abs_delta(fwd: float, strike: float, t: float, vol: float, right: str) -> float:
    d1 = _d1(fwd, strike, t, vol)
    return _ncdf(d1) if right == 'CE' else _ncdf(-d1)


def delta_of(price: float, fwd: float, strike: float, t: float, right: str) -> Optional[float]:
    """|delta| off a premium. No time value left: 1 in the money, 0 out of it."""
    if t <= 0 or price is None:
        return None
    iv = implied_vol(price, fwd, strike, t, right)
    if iv is None:
        itm = fwd > strike if right == 'CE' else fwd < strike
        return 1.0 if itm else 0.0
    return abs_delta(fwd, strike, t, iv, right)


def strike_for_delta(fwd: float, t: float, vol: float, delta: float, right: str,
                     step: int = STRIKE_STEP) -> int:
    """The step strike nearest `delta` on a flat `vol`: the walk's first guess."""
    from statistics import NormalDist
    inv = NormalDist().inv_cdf(delta)
    d1 = inv if right == 'CE' else -inv
    raw = fwd * math.exp(-d1 * vol * math.sqrt(t) + 0.5 * vol * vol * t)
    return int(round(raw / step) * step)


def years_to(ts: pd.Timestamp, expiry: date) -> float:
    return (cre._at(expiry, EXPIRY_HM) - ts).total_seconds() / (365.0 * 86400.0)


# ── strike selection ─────────────────────────────────────────────────────────

ATM_FALLBACK = 2        # 100-step strikes each way tried when the ATM has no candle


def atm_read(spot: float, entry_ts: pd.Timestamp, expiry: date,
             frame_for: Callable[..., Optional[pd.DataFrame]]) -> Dict:
    """Forward and ATM IV off the 100-step ATM call and put of the expiry sold.

    A strike Breeze has no candle for at the entry (2025-12-05's 26200) falls back to the
    next 100-step pair out, up to ATM_FALLBACK each way: parity holds at any strike, and a
    near-ATM IV is all the strike walk needs as a starting guess."""
    atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
    ce = pe = None
    for off in [0] + [d * s * STRIKE_STEP for s in range(1, ATM_FALLBACK + 1) for d in (1, -1)]:
        k = atm + off
        ce = ose.price_at(frame_for(expiry, k, 'CE'), entry_ts)
        pe = ose.price_at(frame_for(expiry, k, 'PE'), entry_ts) if ce is not None else None
        if ce is not None and pe is not None:
            break
    if ce is None or pe is None:
        return {'ok': False, 'reason': f'No ICICI premium at entry for the {atm} ATM (or ±{ATM_FALLBACK * STRIKE_STEP})'}
    fwd = k + ce - pe
    t = years_to(entry_ts, expiry)
    ivs = [v for v in (implied_vol(ce, fwd, k, t, 'CE'), implied_vol(pe, fwd, k, t, 'PE')) if v]
    if not ivs:
        return {'ok': False, 'reason': f'No ATM IV from the {k} premiums {ce} / {pe}'}
    return {'ok': True, 'fwd': fwd, 't': t, 'vol': sum(ivs) / len(ivs), 'basis': fwd - spot}


def side_candidates(right: str, fwd: float, t: float, vol: float, target: float, ts: pd.Timestamp,
                    expiry: date, frame_for: Callable[..., Optional[pd.DataFrame]]) -> List[Dict]:
    """The 100-step OTM strikes either side of `target` delta at `ts`, each on its own IV.

    Starts from the flat-vol guess and walks at most MAX_WALK strikes, so a side costs
    2-3 contracts rather than the whole ladder from the ATM."""
    out_step = STRIKE_STEP if right == 'CE' else -STRIKE_STEP
    is_otm = (lambda s: s > fwd) if right == 'CE' else (lambda s: s < fwd)

    def read(k: int) -> Optional[Dict]:
        px = ose.price_at(frame_for(expiry, k, right), ts)
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
            if inside is not None:      # past the last strike with a price: take what we have
                break
            k += out_step
            continue
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


BALANCE_GAP = 0.20              # premiums further apart than this share of the larger are rebalanced


def choose_pair(ce: List[Dict], pe: List[Dict], target: float, balance: bool) -> tuple:
    """The (CE, PE) sold: each side's nearest delta — unless `balance` and their premiums are more
    than BALANCE_GAP apart. Then the side whose delta is nearer `target` stays put and the OTHER side
    takes its strike with the premium nearest that side's: the author kept his 0.20 PE at 58.2 and
    moved only the CE, from 0.19 at 38 to 0.23 at 57; in his second week he kept the 0.17 CE at 42.4
    and sold the PE nearest it (41), "not more than that".

    Until 2026-11-10 this took the pair with the closest premiums over all four strikes, which
    moved BOTH sides toward the money: 2026-06-19 sold 0.24 / 0.22 at 63.65 / 63.6 when the 0.18
    CE at 45.5 had a 0.17 PE at 47.9 beside it, and the 0.30 trigger then came within a session."""
    pair = nearest(ce, target), nearest(pe, target)
    gap = abs(pair[0]['price'] - pair[1]['price']) / max(pair[0]['price'], pair[1]['price'])
    if not balance or gap <= BALANCE_GAP:
        return pair
    anchor = 0 if abs(pair[0]['delta'] - target) <= abs(pair[1]['delta'] - target) else 1
    px = pair[anchor]['price']
    other = min((pe, ce)[anchor], key=lambda q: (abs(q['price'] - px), abs(q['delta'] - target)))
    return (pair[0], other) if anchor == 0 else (other, pair[1])


def nearest(side: List[Dict], target: float) -> Dict:
    return min(side, key=lambda q: abs(q['delta'] - target))


def hedge_strike(right: str, short: int, gap: int) -> int:
    return short + gap if right == 'CE' else short - gap


# ── the hold ─────────────────────────────────────────────────────────────────

CALM_SESSIONS = 10          # realised vol window; the week's move is over the last 5 of them


def calm_reading(entry: date, sessions: Sequence[date], spot5: pd.Series, entry_hm: tuple) -> Optional[Dict]:
    """NIFTY's realised vol (10 sessions, annualised %) and 5-session move (%) as known at the entry:
    each session's price at `entry_hm`, today's included — nothing after the entry is read. None
    when 11 sessions of prices are not there.

    Why a filter (2026-11-10, not the video's): over 2020-26 the median sweep combo LOST money
    (net ÷ DD −0.34), and the weeks that did it were the ones after a fast market. With rv10 < 10 and
    |5-day| < 2 the share of combos in profit rose in both halves (2020-23: 16 → 62 %; 2024-26:
    48 → 77 %), and the author's defaults went ₹30 k / DD ₹53 k → ₹1.11 L / DD ₹20 k. It is the
    author's own "skip an uncertain week" put as a number."""
    past = [d for d in sessions if d <= entry][-(CALM_SESSIONS + 1):]
    if len(past) < CALM_SESSIONS + 1 or past[-1] != entry:
        return None
    px = [ose.price_at(spot5, cre._at(d, entry_hm)) for d in past]
    if any(x is None or x <= 0 for x in px):
        return None
    rets = np.diff(np.log(np.asarray(px, dtype=float)))
    return {'rv': float(np.std(rets, ddof=1) * math.sqrt(252) * 100.0),
            'move': float(100.0 * (px[-1] / px[-6] - 1.0))}


def calm_skip(reading: Optional[Dict], p: Dict) -> Optional[str]:
    """Why the week is not calm enough under `p`, or None to trade it."""
    max_rv, max_move = float(p.get('max_rv') or 0), float(p.get('max_week_move') or 0)
    if not max_rv and not max_move:
        return None
    if reading is None:
        return 'No NIFTY history for the calm-market filter'
    if max_rv and reading['rv'] >= max_rv:
        return f"NIFTY realised vol {reading['rv']:.1f}% (10 sessions) not below {max_rv:g}%"
    if max_move and abs(reading['move']) >= max_move:
        return f"NIFTY moved {reading['move']:+.2f}% in 5 sessions, not inside ±{max_move:g}%"
    return None


def _open_week(entry: date, sessions: Sequence[date], spot5: pd.Series, vix5: Optional[pd.Series],
               frame_for: Callable[..., Optional[pd.DataFrame]], p: Dict) -> Dict:
    """Expiry, spot, VIX and the ATM read for one Friday, or {'skip': reason}. Both drivers start here."""
    listed = p.get('listed_expiries', ())
    if entry < ose.WEEKLY_SINCE:
        return {'skip': f'No NIFTY weekly contract before {ose.WEEKLY_SINCE.isoformat()}'}
    expiry = sold_expiry(entry, sessions, listed)
    if expiry is None:
        return {'skip': 'No expiry resolved'}
    out: Dict = {'expiry': expiry}
    entry_ts = cre._at(entry, p['entry_hm'])
    spot = ose.price_at(spot5, entry_ts)
    if spot is None:
        return {**out, 'skip': 'No NIFTY bar at the entry time'}
    out.update({'entry_ts': entry_ts, 'spot': spot})
    vix = ose.price_at(vix5, entry_ts) if vix5 is not None else None
    out['vix'] = vix
    if vix is None and p.get('max_vix'):
        return {**out, 'skip': 'No India VIX bar at the entry time'}
    if vix is not None and p.get('max_vix') and vix >= p['max_vix']:
        return {**out, 'skip': f"India VIX {vix:.2f} not below {p['max_vix']:g}", 'high_vix': True}
    calm = calm_reading(entry, sessions, spot5, p['entry_hm'])
    out['calm'] = calm
    why = calm_skip(calm, p)
    if why:
        return {**out, 'skip': why, 'not_calm': True}
    atm = atm_read(spot, entry_ts, expiry, frame_for)
    if not atm['ok']:
        return {**out, 'skip': atm['reason']}
    return {**out, 'atm': atm, 'iv': 100.0 * atm['vol']}


def _structure(m: Dict, delta: float, hedge_gap: int, balance: bool,
               frame_for: Callable[..., Optional[pd.DataFrame]]) -> Dict:
    """The four legs and their entry prices for one choice of strikes, or {'skip': reason}."""
    entry_ts, expiry, atm = m['entry_ts'], m['expiry'], m['atm']
    ce = side_candidates('CE', atm['fwd'], atm['t'], atm['vol'], delta, entry_ts, expiry, frame_for)
    pe = side_candidates('PE', atm['fwd'], atm['t'], atm['vol'], delta, entry_ts, expiry, frame_for)
    if not ce or not pe:
        return {'skip': f"No {delta:g}-delta {'CE' if not ce else 'PE'} strike on the {expiry}"}
    sold = dict(zip(SIDES, choose_pair(ce, pe, delta, balance)))
    sides = {}
    for right in SIDES:
        k = sold[right]['strike']
        h = hedge_strike(right, k, int(hedge_gap))
        hpx = ose.price_at(frame_for(expiry, h, right), entry_ts)
        if hpx is None:
            return {'skip': f'No ICICI premium at entry for the {h} {right} hedge'}
        sides[right] = {'short': k, 'hedge': h, 'short_px': sold[right]['price'], 'hedge_px': hpx,
                        'sold_px': sold[right]['price']}
    credit = sum(s['short_px'] - s['hedge_px'] for s in sides.values())
    return {'sides': sides, 'credit': credit,
            'deltas': {r: sold[r]['delta'] for r in SIDES}}


class _Closes:
    """Each contract's closes on the path's bars, read lazily (adjustments open new ones)."""

    def __init__(self, idx: pd.DatetimeIndex, first: pd.Timestamp, expiry: date,
                 frame_for: Callable[..., Optional[pd.DataFrame]]):
        self.idx, self.first, self.expiry, self.frame_for = idx, first, expiry, frame_for
        self.cols: Dict[tuple, np.ndarray] = {}

    def col(self, strike: int, right: str) -> np.ndarray:
        key = (strike, right)
        if key not in self.cols:
            f = self.frame_for(self.expiry, strike, right)
            if f is None or f.empty:
                c = np.full(len(self.idx), np.nan)
            else:
                s = f['close']
                s = s[s.index >= self.first]
                c = s.reindex(s.index.union(self.idx)).ffill().reindex(self.idx).to_numpy(dtype=float)
            self.cols[key] = c
        return self.cols[key]

    def at(self, strike: int, right: str, i: int, fallback: float) -> float:
        v = self.col(strike, right)[i]
        return fallback if np.isnan(v) else float(v)


def build_path(m: Dict, st: Dict, cut: Optional[pd.Timestamp], spot5: pd.Series,
               frame_for: Callable[..., Optional[pd.DataFrame]], p: Dict) -> Dict:
    """Every 5-minute bar closing after the entry and by `cut`, with the adjustments applied.

    Returns the per-unit MTM (realised + open) at each close, the legs opened by then (for
    brokerage), each side's strikes at each close, and the adjustment log. The adjustments
    read prices only, so the path does not depend on any exit rule."""
    entry_ts, expiry, atm = m['entry_ts'], m['expiry'], m['atm']
    first = entry_ts.floor('5min')
    idx = spot5.index[spot5.index >= first]
    if cut is not None:
        idx = idx[idx + ose.BAR <= cut]
    n = len(idx)
    closes = _Closes(idx, first, expiry, frame_for)
    sides = {r: dict(st['sides'][r]) for r in SIDES}
    spot = spot5.reindex(idx).to_numpy(dtype=float)
    t0 = atm['t']
    mtm = np.zeros(n)
    legs = np.zeros(n, dtype=int)
    strikes = {r: np.zeros(n, dtype=int) for r in SIDES}
    realised = 0.0
    opened = 4
    log: List[Dict] = []
    n_adj = 0
    last_vol = {r: atm['vol'] for r in SIDES}
    tgt = float(p['target_delta'])

    def px(side: Dict, right: str, i: int) -> tuple:
        return (closes.at(side['short'], right, i, side['short_px']),
                closes.at(side['hedge'], right, i, side['hedge_px']))

    for i in range(n):
        now = {r: px(sides[r], r, i) for r in SIDES}
        mtm[i] = realised + sum(-(now[r][0] - sides[r]['short_px']) + (now[r][1] - sides[r]['hedge_px'])
                                for r in SIDES)
        legs[i] = opened
        for r in SIDES:
            strikes[r][i] = sides[r]['short']
        if not p.get('adjust') or n_adj >= int(p['max_adjust']) or i >= n - 1:
            continue
        close_ts = idx[i] + ose.BAR
        t = years_to(close_ts, expiry)
        if t <= 0 or np.isnan(spot[i]):
            continue
        fwd = spot[i] + atm['basis'] * max(0.0, t / t0)
        delta = {r: delta_of(now[r][0], fwd, sides[r]['short'], t, r) for r in SIDES}
        if any(v is None for v in delta.values()):
            continue

        moves: List[tuple] = []         # (right, 'out' | 'in', reason)
        for r in SIDES:
            sold = sides[r]['sold_px']
            if now[r][0] >= p['double_x'] * sold:
                moves.append((r, 'out', f'premium {sold:.1f}→{now[r][0]:.1f}'))
            elif delta[r] >= p['max_delta']:
                moves.append((r, 'out', f'delta {delta[r]:.2f}'))
        if not moves:
            hi, lo = ('CE', 'PE') if delta['CE'] >= delta['PE'] else ('PE', 'CE')
            if delta[hi] > p['skew_x'] * delta[lo]:
                moves.append((hi, 'out', f'delta {delta[hi]:.2f} > {p["skew_x"]:g}× {delta[lo]:.2f}'))
                moves.append((lo, 'in', f'delta {delta[lo]:.2f} < {delta[hi]:.2f}/{p["skew_x"]:g}'))
        tested = {r for r, d, _ in moves if d == 'out'}
        for r in SIDES:
            if r in tested or any(m_[0] == r for m_ in moves):
                continue
            sold = sides[r]['sold_px']
            if now[r][0] <= (1.0 - p['roll_decay_pct'] / 100.0) * sold:
                moves.append((r, 'in', f'decayed {sold:.1f}→{now[r][0]:.1f}'))

        skew_out_done = False
        for r, direction, why in moves:
            if n_adj >= int(p['max_adjust']):
                break
            if direction == 'in' and why.startswith('delta') and skew_out_done:
                continue                # rule 3 fixed on the high side: the low side stays
            other = 'PE' if r == 'CE' else 'CE'
            vol0 = implied_vol(now[r][0], fwd, sides[r]['short'], t, r) or last_vol[r]
            last_vol[r] = vol0
            cands = side_candidates(r, fwd, t, vol0, tgt, close_ts, expiry, frame_for)
            cur = sides[r]['short']
            further = (lambda k: k > cur) if r == 'CE' else (lambda k: k < cur)
            if direction == 'out':
                cands = [c for c in cands if further(c['strike'])]
                pick = nearest(cands, tgt) if cands else None
                # Only a strike nearer the target than the side already is: a side sitting at 0.20
                # is not walked out to 0.17 by rule 3 (the author leaves it and fixes the other).
                if pick is not None and abs(pick['delta'] - tgt) >= abs(delta[r] - tgt):
                    pick = None
            else:
                cands = [c for c in cands if not further(c['strike']) and c['strike'] != cur]
                if cands and p.get('balance_premium'):
                    ref = now[other][0]           # the other side as it stands (moved this bar or not)
                    pick = min(cands, key=lambda c: (abs(c['price'] - ref), abs(c['delta'] - tgt)))
                else:
                    pick = nearest(cands, tgt) if cands else None
            if pick is None:
                continue
            h = hedge_strike(r, pick['strike'], int(p['hedge_gap']))
            hpx = ose.price_at(frame_for(expiry, h, r), close_ts)
            if hpx is None:
                continue
            # Close the side at this close (the MTM above already marks it there), open the new one.
            realised += -(now[r][0] - sides[r]['short_px']) + (now[r][1] - sides[r]['hedge_px'])
            log.append({'ts': close_ts.isoformat(), 'side': r, 'move': direction, 'why': why,
                        'from': cur, 'to': pick['strike'], 'closed_at': round(now[r][0], 2),
                        'sold_at': round(pick['price'], 2)})
            sides[r] = {'short': pick['strike'], 'hedge': h, 'short_px': pick['price'], 'hedge_px': hpx,
                        'sold_px': pick['price']}
            # Later rules this bar see the new side.
            now[r] = (pick['price'], hpx)
            opened += 2
            n_adj += 1
            if direction == 'out' and why.startswith('delta') and '×' in why:
                skew_out_done = True
    return {'idx': idx, 'mtm': mtm, 'legs': legs, 'strikes': strikes, 'log': log,
            'final': sides, 'closes': closes}


def first_exit(path: Dict, qty: int, target_rupees: float, stop_rupees: float,
               eod_profit_rupees: float, eod_loss_rupees: float, eod_hm: tuple,
               reached_cut: bool, memo: Optional[Dict] = None) -> tuple:
    """(bar position, reason). Position −1 = no bar after the entry at all.

    Target / stop on any 5-minute close; the day-end exits only on the close at `eod_hm`.
    `memo` keeps the first-hit positions, for a sweep asking one path many times."""
    seen = {} if memo is None else memo
    rupees = path['mtm'] * qty
    if 'eod_mask' not in seen:
        close = path['idx'] + ose.BAR
        seen['eod_mask'] = np.asarray((close.hour == eod_hm[0]) & (close.minute == eod_hm[1]))
    hits = []    # (position, rank, reason): on one close a target/stop outranks the day-end rule it also trips
    for rank, (tag, level, mask_fn, reason) in enumerate((
            ('t', target_rupees, lambda: rupees >= target_rupees, 'Target'),
            ('s', stop_rupees, lambda: rupees <= -stop_rupees, 'Stop loss'),
            ('ep', eod_profit_rupees, lambda: seen['eod_mask'] & (rupees >= eod_profit_rupees), 'Day-end profit'),
            ('el', eod_loss_rupees, lambda: seen['eod_mask'] & (rupees <= -eod_loss_rupees), 'Day-end loss'))):
        if level <= 0:
            continue
        if (tag, level) not in seen:
            seen[(tag, level)] = cre._first(mask_fn())
        pos = seen[(tag, level)]
        if pos >= 0:
            hits.append((pos, rank, reason))
    if hits:
        pos, _, reason = min(hits)
        return pos, reason
    n = len(path['mtm'])
    if not n:
        return -1, 'Open (data ends)'
    return n - 1, 'Time exit' if reached_cut else 'Open (data ends)'


def booked(path: Dict, pos: int, reason: str, qty: int, target_rupees: float,
           stop_rupees: float) -> tuple:
    """(points per unit, legs traded, reason) booked for the exit `first_exit` found at `pos`.

    A target or stop the MTM crossed between two closes of one session is booked AT its level,
    as the straddle's `exit_fill` does: the author watches a 1 % MTM, not a 5-minute close, and
    booking the close put 2025-12-05's stop at −1.18 % and 2026-07-10's at −1.23 %. The legs are
    those held before that close, since the exit comes first and an adjustment the close would
    have made never happens. A close that is the session's first one (an overnight gap through
    the level) has no price nearer the level than itself, so it is booked there, `(gap)`."""
    if pos < 0:
        return 0.0, 4, reason
    close_pts = float(path['mtm'][pos])
    if reason not in ('Target', 'Stop loss'):
        return close_pts, int(path['legs'][pos]), reason
    prev_legs = int(path['legs'][pos - 1]) if pos > 0 else 4
    idx = path['idx']
    same_session = pos == 0 or idx[pos - 1].date() == idx[pos].date()
    if not same_session:
        return close_pts, prev_legs, f'{reason} (gap)'
    level = (target_rupees if reason == 'Target' else -stop_rupees) / qty
    return level, prev_legs, reason


def _cut(entry: date, expiry: date, sessions: Sequence[date], p: Dict) -> Optional[pd.Timestamp]:
    d = exit_day(entry, expiry, sessions)
    return None if d is None else cre._at(d, p['exit_hm'])


def week_capital(m: Dict, qty: int, capital: float, p: Dict) -> float:
    """The capital one Friday is judged on. With `capital_pct` it is that % of NIFTY at the entry × qty:
    the author's ₹9.6 L for 10 lots of 75 is ≈5 % of NIFTY 25,700, and a margin is a share of the
    index's value, so at NIFTY 18,000 (2023) it was ~30 % smaller in ₹ — a fixed ₹ figure then made
    the 4 % credit floor skip 46 of 52 Fridays. Otherwise the caller's fixed `capital`."""
    pct = float(p.get('capital_pct') or 0)
    spot = m.get('spot')
    return pct / 100.0 * float(spot) * qty if pct > 0 and spot else capital


def run_trade(entry: date, sessions: Sequence[date], spot5: pd.Series, vix5: Optional[pd.Series],
              frame_for: Callable[..., Optional[pd.DataFrame]],
              qty: int, capital: float, params: Optional[Dict] = None) -> Dict:
    """One Friday from entry to exit; 'status' is traded | open | skipped.

    `frame_for(expiry, strike, right)` is that contract's 5-minute frame."""
    p = {**DEFAULTS, **(params or {})}
    m = _open_week(entry, sessions, spot5, vix5, frame_for, p)
    base: Dict = {'entry_date': entry, **{k: m[k] for k in ('expiry', 'spot', 'vix', 'iv') if k in m}}

    def skip(reason: str, **extra) -> Dict:
        return {**base, **extra, 'status': 'skipped', 'reason': reason}

    capital = week_capital(m, qty, capital, p)
    base['capital'] = capital
    if 'skip' in m:
        return skip(m['skip'], high_vix=bool(m.get('high_vix')), not_calm=bool(m.get('not_calm')))
    st = _structure(m, p['target_delta'], p['hedge_gap'], p['balance_premium'], frame_for)
    if 'skip' in st:
        return skip(st['skip'])
    credit_pct = 100.0 * st['credit'] * qty / capital
    if p['min_credit_pct'] and credit_pct < p['min_credit_pct']:
        return skip(f"Net credit {st['credit']:.1f} pts = {credit_pct:.2f}% of capital, under {p['min_credit_pct']:g}%",
                    low_credit=True, credit=st['credit'], credit_pct=credit_pct, entry_sides=st['sides'])

    cut = _cut(entry, m['expiry'], sessions, p)
    path = build_path(m, st, cut, spot5, frame_for, p)
    pos, reason = first_exit(path, qty, capital * p['target_pct'] / 100.0, capital * p['stop_pct'] / 100.0,
                             capital * p['eod_profit_pct'] / 100.0, capital * p['eod_loss_pct'] / 100.0,
                             p['eod_hm'], cut is not None)
    if pos < 0:
        pts, exit_ts, upto, legs, log = 0.0, m['entry_ts'], np.zeros(0), 4, []
        final = st['sides']
    else:
        pts, legs, reason = booked(path, pos, reason, qty, capital * p['target_pct'] / 100.0,
                                   capital * p['stop_pct'] / 100.0)
        exit_ts = path['idx'][pos] + ose.BAR
        upto = np.append(path['mtm'][:pos], pts) * qty    # the worst is what was booked, not the close past it
        log = [a for a in path['log'] if a['ts'] < exit_ts.isoformat()]
        held_at = pos - 1 if pos > 0 and reason.startswith(('Target', 'Stop loss')) else pos
        final = {r: int(path['strikes'][r][held_at]) for r in SIDES}
    held = sum(1 for d in set(sessions) if entry < d <= exit_ts.date())
    return {
        **base,
        'status': 'open' if reason.startswith('Open') else 'traded',
        'entry_ts': m['entry_ts'], 'exit_ts': exit_ts, 'reason': reason,
        'entry_sides': st['sides'], 'final_shorts': final if pos >= 0 else {r: final[r]['short'] for r in SIDES},
        'ce_delta': st['deltas']['CE'], 'pe_delta': st['deltas']['PE'],
        'credit': st['credit'], 'credit_pct': credit_pct,
        'pnl_points': pts, 'legs_traded': legs, 'adjustments': log,
        'peak_rupees': float(max(0.0, upto.max())) if len(upto) else 0.0,
        'trough_rupees': float(min(0.0, upto.min())) if len(upto) else 0.0,
        'sessions_held': held,
    }


_KEYS: Dict[tuple, tuple] = {}    # one tuple per combo for every Friday's dict: ~9,000 keys, not 9,000 a week


def sweep_base(params: Dict) -> Dict:
    """The page's params as the sweep reads them: Day-end "On" is the page's levels, or DEFAULTS'
    0.8 / 0.9 where a box is 0 — which is what applying a Day-end Off row leaves in it. The calm
    filter's "On" is the same: the page's two levels, or 10 / 2 where a box is 0."""
    out = dict(params)
    for k in ('eod_profit_pct', 'eod_loss_pct', 'max_rv', 'max_week_move'):
        out[k] = out.get(k) or DEFAULTS[k]
    return out


def score_week(entry: date, sessions: Sequence[date], spot5: pd.Series, vix5: Optional[pd.Series],
               frame_for: Callable[..., Optional[pd.DataFrame]], qty: int, capital: float,
               base: Dict, brokerage_per_leg: float, grid: Dict = SWEEP_GRID) -> Dict[tuple, tuple]:
    """Every combo on one Friday: {combo (SWEEP_KEYS order): (status, net ₹, % of capital, reason)}.

    A Friday whose VIX is at/above every ceiling has no entry at all; the caller counts it."""
    p = {**DEFAULTS, **base, 'max_vix': 0}
    calm_ok = calm_skip(calm_reading(entry, sessions, spot5, p['entry_hm']), p) is None
    m = _open_week(entry, sessions, spot5, vix5, frame_for, {**p, 'max_rv': 0, 'max_week_move': 0})
    vix = m.get('vix')
    ceilings = [c for c in grid['max_vix'] if vix is not None and vix < c]
    if not ceilings or 'atm' not in m:
        return {}
    capital = week_capital(m, qty, capital, p)
    cut = _cut(entry, m['expiry'], sessions, p)
    exits = [(tp, sp, de) for tp in grid['target_pct'] for sp in grid['stop_pct'] for de in grid['day_end']]
    out: Dict[tuple, tuple] = {}
    for delta in grid['target_delta']:
        for gap in grid['hedge_gap']:
            st = _structure(m, delta, gap, p['balance_premium'], frame_for)
            for adjust in grid['adjust']:
                if 'skip' in st:
                    per_floor = {f: {ex: ('skipped', 0.0, 0.0, st['skip']) for ex in exits}
                                 for f in grid['min_credit_pct']}
                else:
                    credit_pct = 100.0 * st['credit'] * qty / capital
                    path = None
                    memo: Dict = {}
                    per_floor = {}
                    for floor in grid['min_credit_pct']:
                        if floor and credit_pct < floor:
                            per_floor[floor] = {ex: ('skipped', 0.0, 0.0, 'Low credit') for ex in exits}
                            continue
                        if path is None:
                            path = build_path(m, st, cut, spot5, frame_for,
                                              {**p, 'target_delta': delta, 'hedge_gap': gap, 'adjust': adjust})
                            scored = {}
                            for tp, sp, de in exits:
                                ep = capital * p['eod_profit_pct'] / 100.0 if de else 0.0
                                el = capital * p['eod_loss_pct'] / 100.0 if de else 0.0
                                pos, reason = first_exit(path, qty, capital * tp / 100.0, capital * sp / 100.0,
                                                         ep, el, p['eod_hm'], cut is not None, memo)
                                pts, legs, reason = booked(path, pos, reason, qty, capital * tp / 100.0,
                                                           capital * sp / 100.0)
                                net = pts * qty - brokerage_per_leg * legs
                                scored[(tp, sp, de)] = ('open' if reason.startswith('Open') else 'traded',
                                                        net, 100.0 * net / capital, reason)
                        per_floor[floor] = scored
                calms = [cm for cm in grid['calm'] if calm_ok or not cm]   # a week the filter drops has no "on" row
                for c in ceilings:
                    for floor, scored in per_floor.items():
                        for (tp, sp, de), r in scored.items():
                            for cm in calms:
                                k = (c, delta, gap, adjust, floor, tp, sp, de, cm)
                                out[_KEYS.setdefault(k, k)] = r
    return out


def rank_board(per_week: List[Dict[tuple, tuple]], weeks: int, min_trades: int = MIN_SWEEP_TRADES) -> List[Dict]:
    """One row per combo, best net ₹ first. `per_week` is oldest first, for the drawdown; a Friday a
    combo has no result for was above its VIX ceiling, so `skipped` is every Friday it did not hold."""
    rows = cre.rank(per_week, min_trades=min_trades, keys=SWEEP_KEYS)
    for r in rows:
        r['skipped'] = weeks - r['total_trades'] - r['open']
    return rows


def _adj_text(log: List[Dict]) -> str:
    return '; '.join(f"{a['ts'][5:16].replace('T', ' ')} {a['side']} {a['from']}→{a['to']} ({a['why']})"
                     for a in log)


def trade_row(res: Dict, qty: int, sets: int, lot_size: int, capital: float,
              brokerage_per_leg: float) -> Dict:
    """Flatten a run_trade result into one grid row (on the week's own capital when it carries one)."""
    capital = res.get('capital') or capital
    row: Dict = {
        'date': res['entry_date'].isoformat(),
        'expiry': res['expiry'].isoformat() if res.get('expiry') else None,
        'spot': round(res.get('spot') or 0, 2) or None,
        'vix': round(res['vix'], 2) if res.get('vix') is not None else None,
        'iv': round(res['iv'], 1) if res.get('iv') is not None else None,
        'status': res['status'],
        'sets': sets, 'lot_size': lot_size, 'qty': qty, 'capital': round(capital, 2),
    }
    if res['status'] == 'skipped':
        row.update({'exit_reason': res['reason'], 'result': 'SKIP', 'high_vix': bool(res.get('high_vix')),
                    'not_calm': bool(res.get('not_calm')),
                    'low_credit': bool(res.get('low_credit')),
                    'credit': round(res['credit'], 2) if res.get('credit') is not None else None,
                    'credit_pct': round(res['credit_pct'], 2) if res.get('credit_pct') is not None else None,
                    'pnl': None, 'pnl_rupees': None, 'pnl_pct': None})
        for r in SIDES:
            s = (res.get('entry_sides') or {}).get(r)
            if s:
                row[f'sell_{r.lower()}_strike'] = s['short']
                row[f'sell_{r.lower()}_entry'] = round(s['short_px'], 2)
                row[f'hedge_{r.lower()}_strike'] = s['hedge']
        return row
    for r in SIDES:
        s = res['entry_sides'][r]
        row[f'sell_{r.lower()}_strike'] = s['short']
        row[f'sell_{r.lower()}_entry'] = round(s['short_px'], 2)
        row[f'hedge_{r.lower()}_strike'] = s['hedge']
        row[f'hedge_{r.lower()}_entry'] = round(s['hedge_px'], 2)
        row[f'final_{r.lower()}_strike'] = res['final_shorts'][r]
    gross = res['pnl_points'] * qty
    brokerage = brokerage_per_leg * res['legs_traded']
    net = gross - brokerage
    row.update({
        'entry_time': res['entry_ts'].isoformat(),
        'exit_time': res['exit_ts'].isoformat(),
        'sessions_held': res['sessions_held'],
        'ce_delta': round(res['ce_delta'], 3),
        'pe_delta': round(res['pe_delta'], 3),
        'credit': round(res['credit'], 2),
        'credit_pct': round(res['credit_pct'], 2),
        'adjustments': len(res['adjustments']),
        'adjust_log': _adj_text(res['adjustments']),
        'legs_traded': res['legs_traded'],
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
    closed = [r for r in rows if r['status'] == 'traded']
    out['skipped_high_vix'] = sum(1 for r in rows if r.get('high_vix'))
    out['skipped_not_calm'] = sum(1 for r in rows if r.get('not_calm'))
    # What the account must hold: the largest week's capital (it moves with NIFTY) plus the worst run of losses.
    caps = [r['capital'] for r in rows if r['status'] != 'skipped' and r.get('capital')]
    out['max_capital'] = round(max(caps), 2) if caps else 0.0
    out['amount_needed'] = round(out['max_capital'] + out['max_drawdown_rupees'], 2)
    out['max_trade_loss'] = round(min((r['pnl_rupees'] for r in closed), default=0.0), 2)
    # Per calendar month, every month the range covers (a month with no trade is 0 %), on each trade's own capital.
    months: Dict[str, float] = {}
    for r in rows:
        months.setdefault(r['date'][:7], 0.0)
    for r in closed:
        months[r['date'][:7]] += r['pnl_pct']
    m = list(months.values())
    out['months'] = len(m)
    out['avg_month_pct'] = round(sum(m) / len(m), 2) if m else 0.0
    out['best_month_pct'] = round(max(m), 2) if m else 0.0
    out['worst_month_pct'] = round(min(m), 2) if m else 0.0
    out['months_3pct'] = sum(1 for x in m if x >= 3.0)
    out['losing_months'] = sum(1 for x in m if x < 0)
    out['skipped_low_credit'] = sum(1 for r in rows if r.get('low_credit'))
    out['total_adjustments'] = sum(r.get('adjustments') or 0 for r in closed)
    out['by_year'] = by_year(rows)
    return out


def by_year(rows: List[Dict]) -> List[Dict]:
    """Every year's Fridays, oldest first, so a year with no trades shows why instead of being absent."""
    years: Dict[str, Dict] = {}
    for r in sorted(rows, key=lambda x: x['date']):
        y = years.setdefault(r['date'][:4], {'year': r['date'][:4], 'weeks': 0, 'high_vix': 0, 'not_calm': 0, 'low_credit': 0,
                                             'no_data': 0, 'open': 0, 'traded': 0, 'wins': 0, 'net': 0.0,
                                             'adjustments': 0, 'vixes': []})
        y['weeks'] += 1
        if r.get('vix') is not None:
            y['vixes'].append(r['vix'])
        if r['status'] == 'traded':
            y['traded'] += 1
            y['wins'] += r['pnl_rupees'] > 0
            y['net'] += r['pnl_rupees']
            y['adjustments'] += r.get('adjustments') or 0
        elif r['status'] == 'open':
            y['open'] += 1
        elif r.get('high_vix'):
            y['high_vix'] += 1
        elif r.get('not_calm'):
            y['not_calm'] += 1
        elif r.get('low_credit'):
            y['low_credit'] += 1
        else:
            y['no_data'] += 1
    out = []
    for y in years.values():
        v = y.pop('vixes')
        out.append({**y, 'net': round(y['net'], 2),
                    'vix_median': round(float(np.median(v)), 2) if v else None,
                    'vix_min': round(min(v), 2) if v else None})
    return out
