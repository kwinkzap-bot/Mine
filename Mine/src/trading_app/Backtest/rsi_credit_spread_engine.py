"""NIFTY intraday RSI(9) credit spread — a weekly 20-delta short hedged 1000 points out.

The rule (added 2026-11-01, from a Lemon "RSI intraday option selling" video):

* **Signal** — RSI(9) on NIFTY 15-minute candles, bands 60 / 40, read on each
  candle's CLOSE. Crossing above 60 is bullish → a **bull put spread**: SELL the
  ~20-delta PE of the current weekly, BUY the PE 1000 points further out.
  Crossing below 40 is bearish → a **bear call spread** the mirror way.
  The long leg is for the margin benefit, not a hedge the rule manages.
* **First entry** — nothing before 09:30 (the first 15-minute candle has to
  close). At 09:30, if the 09:15 candle gave no crossing, the PREVIOUS session's
  last RSI decides: above 60 → put spread, below 40 → call spread, between →
  wait for a crossing.
* **Window** — no new position after 14:45; whatever is open is squared off at
  15:15. Nothing is carried overnight.
* **Stop** — `sl_pct` (30) % above the SHORT leg's entry premium; the spread is
  closed as a whole when it is hit.
* **Stop and reverse** — RSI closing on the far side of the other band (below 40
  while in a put spread, above 60 in a call spread) closes the spread and opens
  the opposite one at the same moment. RSI between the bands changes nothing.
* **Re-entry** — once per trade: after its stop, the same short strike is sold
  again if its premium comes back down to the original entry, with a fresh
  hedge and a fresh stop. A crossing either way while waiting supersedes it.

Strike choice (the video picks "20 delta, or the nearest to it" off a live
chain): each candidate's own implied vol is solved from its premium at the
entry minute (Black-Scholes, spot, `RISK_FREE`), its delta taken from that, and
the strike whose |delta| is nearest `target_delta` is sold. The search starts
from the strike ATM vol says is 20-delta and walks one strike at a time until
it has bracketed the target, so it costs a few contracts, not a chain.

Fills are read off each contract's own 1-minute candles (ICICI Breeze), the
conventions of option_selling_engine:

* entry — the OPEN of the entry minute (the 15-minute candle boundary);
* stop — the stop level, or the bar's open if it gapped through; the long leg
  is sold at that minute's close (the order inside a minute is not known);
* re-entry — the original level, or the bar's open if it opened below it,
  searched from the bar AFTER the stop's; a re-entry bar that also reaches the
  new stop is stopped in that bar (pessimistic);
* RSI reversal / time exit — the OPEN of that minute, else the last print.

The module is pure: callers hand in the bars, so it never touches a broker.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from statistics import NormalDist
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from trading_app.Backtest.option_selling_engine import NIFTY_STRIKE_STEP, MAX_ENTRY_LAG, atm_strike
from trading_app.Backtest.second_candle_engine import _bs_delta, _bs_price
from trading_app.Backtest.st_bb_rsi_engine import rsi as wilder_rsi

RSI_PERIOD = 9
UPPER = 60.0
LOWER = 40.0
TARGET_DELTA = 0.20
HEDGE_POINTS = 1000
SL_PCT = 30.0
ENTRY_START = (9, 30)
ENTRY_END = (14, 45)
EXIT_HM = (15, 15)
RISK_FREE = 0.065
EXPIRY_HM = (15, 30)
MIN_T_YEARS = 15 / (365 * 24 * 60)       # 15 minutes: an expiry-day 15:10 entry still has a delta
MAX_STRIKE_WALK = 10
CANDLE = pd.Timedelta(minutes=15)
MINUTE = pd.Timedelta(minutes=1)


# ── Signal side ──────────────────────────────────────────────────────────────
def candles_15m(minutes: pd.DataFrame) -> pd.DataFrame:
    """1-minute index bars → 15-minute OHLC candles from 09:15 (09:15 is a
    multiple of 15 minutes, so a plain floor lines up with NSE's candles)."""
    df = minutes[['open', 'high', 'low', 'close']].astype(float)
    t = df.index
    df = df[(t.hour * 60 + t.minute >= 9 * 60 + 15) & (t.hour * 60 + t.minute < 15 * 60 + 30)]
    g = df.groupby(df.index.floor('15min'))
    return pd.DataFrame({'open': g['open'].first(), 'high': g['high'].max(),
                         'low': g['low'].min(), 'close': g['close'].last()})


def with_rsi(c15: pd.DataFrame, period: int = RSI_PERIOD) -> pd.DataFrame:
    """Wilder RSI on the closes, continuous across sessions (as a chart draws it)."""
    out = c15.copy()
    out['rsi'] = wilder_rsi(out['close'].to_numpy(), period)
    return out


def decisions(day_rsi: List[Tuple[pd.Timestamp, float]], prev_rsi: Optional[float],
              upper: float = UPPER, lower: float = LOWER,
              prev_day_bias: bool = True) -> List[Dict]:
    """One decision per closed candle of the session.

    `day_rsi`: [(candle start, RSI at its close)] in order. Each decision is at
    the candle's close (= the next candle's open): {'ts', 'rsi', 'signal'
    ('bull' | 'bear' | None), 'why'}. A signal is a CROSSING of a band; the
    first candle of the day also takes the previous session's side when it
    did not cross itself (`prev_day_bias`)."""
    out = []
    prev = prev_rsi
    for k, (start, r) in enumerate(day_rsi):
        sig = why = None
        if r is not None and not math.isnan(r):
            if prev is not None and not math.isnan(prev):
                if prev <= upper < r:
                    sig, why = 'bull', f'RSI crossed above {upper:g}'
                elif prev >= lower > r:
                    sig, why = 'bear', f'RSI crossed below {lower:g}'
            if sig is None and k == 0 and prev_day_bias and prev is not None and not math.isnan(prev):
                if prev > upper:
                    sig, why = 'bull', f'Prev-day RSI above {upper:g}'
                elif prev < lower:
                    sig, why = 'bear', f'Prev-day RSI below {lower:g}'
        out.append({'ts': start + CANDLE, 'rsi': r, 'signal': sig, 'why': why})
        prev = r
    return out


# ── Option maths ─────────────────────────────────────────────────────────────
def years_to_expiry(ts: pd.Timestamp, expiry: date) -> float:
    end = datetime(expiry.year, expiry.month, expiry.day, *EXPIRY_HM)
    return max(MIN_T_YEARS, (end - ts.to_pydatetime()).total_seconds() / (365 * 86400))


def implied_vol(price: float, right: str, s: float, k: float, t: float,
                r: float = RISK_FREE) -> Optional[float]:
    """Black-Scholes IV by bisection; None when the premium is under the
    model's floor (an intrinsic-only or bad print)."""
    flag = 'c' if right == 'CE' else 'p'
    lo, hi = 0.005, 5.0
    if price <= 0 or price < _bs_price(flag, s, k, t, r, lo) or price > _bs_price(flag, s, k, t, r, hi):
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _bs_price(flag, s, k, t, r, mid) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def delta_of(price: float, right: str, s: float, k: float, t: float,
             r: float = RISK_FREE) -> Optional[Tuple[float, float]]:
    """(|delta|, iv) of an option from its own premium, or None."""
    iv = implied_vol(price, right, s, k, t, r)
    if iv is None:
        return None
    return abs(_bs_delta('c' if right == 'CE' else 'p', s, k, t, r, iv)), iv


def pick_short_strike(spot: float, t: float, right: str,
                      price_of: Callable[[int], Optional[float]],
                      target: float = TARGET_DELTA, step: int = NIFTY_STRIKE_STEP,
                      r: float = RISK_FREE) -> Optional[Dict]:
    """The OTM strike whose |delta| is nearest `target`, or None.

    `price_of(strike)` is that contract's premium at the entry minute. Starts at
    the strike ATM vol puts at `target` (skew moves the real one, mostly
    further out for puts), then walks a strike at a time until the target is
    bracketed. Returns {'strike', 'price', 'delta', 'iv'}."""
    atm = atm_strike(spot, step)
    out_dir = step if right == 'CE' else -step       # away from the money
    seen: Dict[int, Optional[Dict]] = {}

    def at(k: int) -> Optional[Dict]:
        if k not in seen:
            p = price_of(k)
            d = delta_of(p, right, spot, k, t, r) if p else None
            seen[k] = None if d is None else {'strike': k, 'price': p, 'delta': d[0], 'iv': d[1]}
        return seen[k]

    base = at(atm)
    iv = base['iv'] if base else 0.15
    z = NormalDist().inv_cdf(target if right == 'CE' else 1.0 - target)
    vt = iv * math.sqrt(t)
    guess = spot * math.exp(-z * vt + (r + 0.5 * iv * iv) * t)
    k = int(round(guess / step) * step)
    k = max(k, atm) if right == 'CE' else min(k, atm)

    cur = at(k)
    if cur is None:                                  # an unprinted strike: try its neighbours
        for nk in (k + out_dir, k - out_dir, k + 2 * out_dir):
            if (right == 'CE' and nk < atm) or (right == 'PE' and nk > atm):
                continue
            cur = at(nk)
            if cur is not None:
                k = nk
                break
    if cur is None:
        return None
    best = cur
    direction = out_dir if cur['delta'] > target else -out_dir
    for _ in range(MAX_STRIKE_WALK):
        nk = k + direction
        if (right == 'CE' and nk < atm) or (right == 'PE' and nk > atm):
            break
        nxt = at(nk)
        if nxt is None:
            break
        if abs(nxt['delta'] - target) < abs(best['delta'] - target):
            best = nxt
        crossed = (cur['delta'] - target) * (nxt['delta'] - target) <= 0
        k, cur = nk, nxt
        if crossed:
            break
    return best


# ── Bars ─────────────────────────────────────────────────────────────────────
def price_at(bars: Optional[pd.DataFrame], ts: pd.Timestamp) -> Optional[float]:
    """An order at `ts`: the open of the first bar at/after it, if not stale."""
    if bars is None or bars.empty:
        return None
    i = int(bars.index.searchsorted(ts, side='left'))
    if i >= len(bars) or bars.index[i] - ts > MAX_ENTRY_LAG:
        return None
    return float(bars['open'].iloc[i])


def mark_at(bars: pd.DataFrame, ts: pd.Timestamp, fallback: float) -> float:
    """A market exit at `ts`: that minute's open, else the last print before."""
    i = int(bars.index.searchsorted(ts, side='left'))
    if i < len(bars) and bars.index[i] == ts:
        return float(bars['open'].iloc[i])
    return float(bars['close'].iloc[i - 1]) if i > 0 else fallback


def close_at(bars: pd.DataFrame, ts: pd.Timestamp, fallback: float) -> float:
    """The close of the minute at `ts`, else the last print before it."""
    i = int(bars.index.searchsorted(ts, side='right'))
    return float(bars['close'].iloc[i - 1]) if i > 0 else fallback


# ── One session ──────────────────────────────────────────────────────────────
def run_day(day: date, day_rsi: List[Tuple[pd.Timestamp, float]], prev_rsi: Optional[float],
            spot_at: Callable[[pd.Timestamp], Optional[float]], expiry: date,
            bars_for: Callable[[int, str], Optional[pd.DataFrame]],
            upper: float = UPPER, lower: float = LOWER, target_delta: float = TARGET_DELTA,
            hedge_points: int = HEDGE_POINTS, sl_pct: float = SL_PCT, reentry: bool = True,
            prev_day_bias: bool = True, entry_start=ENTRY_START, entry_end=ENTRY_END,
            exit_hm=EXIT_HM) -> Dict:
    """Every spread the rule trades in one session.

    `bars_for(strike, right)` → that contract's minute frame for the day (the
    expiry is the caller's). Returns {'trades': [...], 'skipped': [{'ts',
    'why'}]} — a trade is one spread from its entry to its exit."""
    ts_of = lambda hm: pd.Timestamp(datetime(day.year, day.month, day.day, *hm))
    start_ts, end_ts, exit_ts = ts_of(entry_start), ts_of(entry_end), ts_of(exit_hm)
    mult = 1.0 + sl_pct / 100.0 if sl_pct > 0 else float('inf')
    trades: List[Dict] = []
    skipped: List[Dict] = []
    pos: Optional[Dict] = None
    wait: Optional[Dict] = None

    def open_spread(side: str, ts: pd.Timestamp, why: str, rsi_val) -> Optional[Dict]:
        spot = spot_at(ts)
        if spot is None:
            skipped.append({'ts': ts, 'why': 'no index price'})
            return None
        right = 'PE' if side == 'bull' else 'CE'
        t = years_to_expiry(ts, expiry)
        short = pick_short_strike(spot, t, right, lambda k: price_at(bars_for(k, right), ts),
                                  target_delta)
        if short is None:
            skipped.append({'ts': ts, 'why': f'no {right} premiums to find the {target_delta:g} delta'})
            return None
        out_dir = NIFTY_STRIKE_STEP if right == 'CE' else -NIFTY_STRIKE_STEP
        hedge = None
        # The 1000-away strike, else the nearest printed one inside it (4 strikes at most).
        for i in range(5):
            hk = short['strike'] + (hedge_points // NIFTY_STRIKE_STEP - i) * out_dir
            hb = bars_for(hk, right)
            hp = price_at(hb, ts)
            if hp is not None:
                hedge = {'strike': hk, 'price': hp, 'bars': hb}
                break
        if hedge is None:
            skipped.append({'ts': ts, 'why': f'no {right} premium {hedge_points} points out for the hedge'})
            return None
        return {'side': side, 'right': right, 'spot': spot, 'why': why, 'rsi': rsi_val,
                'short_strike': short['strike'], 'short_delta': short['delta'], 'short_iv': short['iv'],
                'short_bars': bars_for(short['strike'], right), 'short_entry': short['price'],
                'hedge_strike': hedge['strike'], 'hedge_bars': hedge['bars'], 'hedge_entry': hedge['price'],
                'entry_time': ts, 'sl': short['price'] * mult, 'orig_entry': short['price'],
                'can_reenter': reentry, 'reentry': False}

    def close(p: Dict, ts: pd.Timestamp, short_exit: float, hedge_exit: float, reason: str):
        trades.append({
            'date': day, 'side': p['side'], 'right': p['right'], 'signal': p['why'],
            'rsi_at_entry': None if p['rsi'] is None else round(float(p['rsi']), 2),
            'spot': p['spot'], 'expiry': expiry,
            'entry_time': p['entry_time'], 'exit_time': ts, 'reason': reason, 'reentry': p['reentry'],
            'short_strike': p['short_strike'], 'short_delta': p['short_delta'], 'short_iv': p['short_iv'],
            'short_entry': p['short_entry'], 'short_exit': short_exit,
            'hedge_strike': p['hedge_strike'], 'hedge_entry': p['hedge_entry'], 'hedge_exit': hedge_exit,
            'points': (p['short_entry'] - short_exit) + (hedge_exit - p['hedge_entry']),
        })

    def close_at_market(p: Dict, ts: pd.Timestamp, reason: str):
        close(p, ts, mark_at(p['short_bars'], ts, p['short_entry']),
              mark_at(p['hedge_bars'], ts, p['hedge_entry']), reason)

    def walk(a: pd.Timestamp, b: pd.Timestamp):
        """Stops and re-entries on the minutes in [a, b)."""
        nonlocal pos, wait
        while True:
            if pos is not None:
                sb = pos['short_bars']
                lo = int(sb.index.searchsorted(a, side='left'))
                hi = int(sb.index.searchsorted(b, side='left'))
                over = sb['high'].to_numpy()[lo:hi] >= pos['sl']
                if not over.any():
                    return
                i = lo + int(over.argmax())
                hit_ts = sb.index[i]
                close(pos, hit_ts, max(pos['sl'], float(sb['open'].iloc[i])),
                      close_at(pos['hedge_bars'], hit_ts, pos['hedge_entry']), 'SL hit')
                if pos['can_reenter']:
                    wait = {**pos, 'from': hit_ts + MINUTE}
                pos = None
                a = hit_ts + MINUTE
            elif wait is not None:
                sb = wait['short_bars']
                lo = int(sb.index.searchsorted(max(a, wait['from']), side='left'))
                hi = int(sb.index.searchsorted(min(b, end_ts + MINUTE), side='left'))
                under = sb['low'].to_numpy()[lo:hi] <= wait['orig_entry']
                if not under.any():
                    return
                i = lo + int(under.argmax())
                ts = sb.index[i]
                hedge_px = price_at(wait['hedge_bars'], ts)
                if hedge_px is None:
                    skipped.append({'ts': ts, 'why': 'no hedge premium for the re-entry'})
                    wait = None
                    return
                entry = min(float(sb['open'].iloc[i]), wait['orig_entry'])
                pos = {**wait, 'entry_time': ts, 'short_entry': entry, 'hedge_entry': hedge_px,
                       'sl': entry * mult, 'can_reenter': False, 'reentry': True,
                       'why': 'Re-entry at cost', 'spot': spot_at(ts) or wait['spot']}
                pos.pop('from', None)
                wait = None
                a = ts                                # the re-entry bar is checked for the stop too
            else:
                return

    last = None
    for d in decisions(day_rsi, prev_rsi, upper, lower, prev_day_bias):
        ts, r, sig = d['ts'], d['rsi'], d['signal']
        if ts < start_ts:
            last = ts
            continue
        if last is not None:
            walk(last, min(ts, exit_ts))
        last = ts
        if ts >= exit_ts:
            break
        bad = r is None or math.isnan(r)
        # The far band closes a spread and turns it round.
        if pos is not None and not bad and ((pos['side'] == 'bull' and r < lower)
                                            or (pos['side'] == 'bear' and r > upper)):
            close_at_market(pos, ts, 'RSI reversal')
            sig = sig or ('bear' if pos['side'] == 'bull' else 'bull')
            d['why'] = d['why'] or 'Reversal'
            pos = None
        if wait is not None and not bad and ((wait['side'] == 'bull' and r < lower)
                                             or (wait['side'] == 'bear' and r > upper)):
            wait = None
        if pos is None and sig is not None and ts <= end_ts:
            new = open_spread(sig, ts, d['why'], r)
            if new is not None:
                pos, wait = new, None
    if last is None:
        last = start_ts
    if pos is not None or wait is not None:
        walk(last, exit_ts)
    if pos is not None:
        close_at_market(pos, exit_ts, 'Time exit')
    return {'trades': trades, 'skipped': skipped}


# ── Rows and stats ───────────────────────────────────────────────────────────
def trade_row(t: Dict, qty: int, lots: int, lot_size: int, brokerage_per_leg: float) -> Dict:
    """One spread → one grid row (₹ at `qty`); brokerage is per leg, so two a spread."""
    gross = t['points'] * qty
    brokerage = 2 * brokerage_per_leg
    r2 = lambda v: None if v is None else round(float(v), 2)
    return {
        'date': t['date'].isoformat(), 'weekday': t['date'].strftime('%a'),
        'side': 'Bull put' if t['side'] == 'bull' else 'Bear call',
        'signal': t['signal'], 'rsi': t['rsi_at_entry'], 'spot': r2(t['spot']),
        'expiry': t['expiry'].isoformat(),
        'entry_time': t['entry_time'].isoformat(), 'exit_time': t['exit_time'].isoformat(),
        'short_strike': t['short_strike'], 'right': t['right'],
        'short_delta': round(t['short_delta'], 3), 'short_iv': round(100 * t['short_iv'], 1),
        'short_entry': r2(t['short_entry']), 'short_exit': r2(t['short_exit']),
        'hedge_strike': t['hedge_strike'], 'hedge_entry': r2(t['hedge_entry']),
        'hedge_exit': r2(t['hedge_exit']),
        'credit': r2(t['short_entry'] - t['hedge_entry']),
        'exit_reason': t['reason'], 'reentry': t['reentry'],
        'pnl': r2(t['points']), 'qty': qty, 'lots': lots, 'lot_size': lot_size,
        'gross_pnl_rupees': round(gross, 2), 'brokerage': brokerage,
        'pnl_rupees': round(gross - brokerage, 2),
        'result': 'WIN' if t['points'] > 0 else ('LOSS' if t['points'] < 0 else 'SCRATCH'),
    }


def summarise(rows: List[Dict]) -> Dict:
    """Stats on the net ₹ of the spread rows, plus a per-year split."""
    n = len(rows)
    net = [r['pnl_rupees'] for r in rows]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x < 0]
    peak = equity = max_dd = 0.0
    for x in net:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    gross_loss = abs(sum(losses))
    years: Dict[str, Dict] = {}
    months: Dict[str, Dict] = {}
    for r in rows:
        mo = months.setdefault(r['date'][:4], {'year': r['date'][:4], 'total': 0.0})
        key = f"m{int(r['date'][5:7]):02d}"
        mo[key] = round(mo.get(key, 0.0) + r['pnl_rupees'], 2)
        mo['total'] = round(mo['total'] + r['pnl_rupees'], 2)
        y = years.setdefault(r['date'][:4], {'year': r['date'][:4], 'trades': 0, 'wins': 0,
                                            'net_pnl_rupees': 0.0, 'points': 0.0})
        y['trades'] += 1
        y['wins'] += 1 if r['pnl_rupees'] > 0 else 0
        y['net_pnl_rupees'] = round(y['net_pnl_rupees'] + r['pnl_rupees'], 2)
        y['points'] = round(y['points'] + (r['pnl'] or 0), 2)
    for y in years.values():
        y['win_rate'] = round(100.0 * y['wins'] / y['trades'], 1)
    return {
        'total_trades': n,
        'trading_days': len({r['date'] for r in rows}),
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(100.0 * len(wins) / n, 2) if n else 0.0,
        'total_pnl_rupees': round(sum(net), 2),
        'total_gross_pnl_rupees': round(sum(r['gross_pnl_rupees'] for r in rows), 2),
        'total_brokerage': round(sum(r['brokerage'] for r in rows), 2),
        'total_points': round(sum(r['pnl'] or 0 for r in rows), 2),
        'avg_win_rupees': round(sum(wins) / len(wins), 2) if wins else 0.0,
        'avg_loss_rupees': round(gross_loss / len(losses), 2) if losses else 0.0,
        'profit_factor': round(sum(wins) / gross_loss, 2) if gross_loss else (999.0 if wins else 0.0),
        'max_drawdown_rupees': round(max_dd, 2),
        'sl_hits': sum(1 for r in rows if r['exit_reason'] == 'SL hit'),
        'reversals': sum(1 for r in rows if r['exit_reason'] == 'RSI reversal'),
        'time_exits': sum(1 for r in rows if r['exit_reason'] == 'Time exit'),
        'reentries': sum(1 for r in rows if r['reentry']),
        'bull_trades': sum(1 for r in rows if r['side'] == 'Bull put'),
        'bear_trades': sum(1 for r in rows if r['side'] == 'Bear call'),
        'avg_short_delta': round(float(np.mean([r['short_delta'] for r in rows])), 3) if rows else 0.0,
        'by_year': [years[k] for k in sorted(years)],
        'by_month': [months[k] for k in sorted(months)],        # net ₹ per month, the video's table
    }
