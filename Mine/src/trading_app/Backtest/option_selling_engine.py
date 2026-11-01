"""NIFTY weekly OTM short strangle with a per-leg stop and one re-entry.

The rule (added 2026-10-17):

* **Days** — Monday, Tuesday and Friday (configurable).
* **Entry** — at ENTRY (09:30) SELL one OTM CE and one OTM PE of the CURRENT
  weekly expiry, `otm_strikes` strikes away from the ATM on the 09:30 spot.
  The expiry day itself is traded on its own expiring contract.
* **Target** — none. Whatever is still open at EXIT (10:30) is bought back.
* **Stop** — each leg on its own: `sl_pct` % above ITS entry premium (60 % →
  stop at 1.6 × entry). One leg stopping does not touch the other. `sl_pct` 0
  is no stop at all.
* **Re-entry** — when a leg's stop is hit it is sold AGAIN, once, if the
  premium comes back down to the leg's original entry ("at cost" — the trade
  that stopped out is then back to breakeven on that level). The re-entered leg
  carries the same stop % off the re-entry price and is not re-entered again.

Fills are read off the contract's own 1-minute candles (ICICI Breeze):

* entry — the OPEN of the entry minute;
* stop — the stop level, or the bar's open if it gapped through it;
* re-entry — the original entry level, or the bar's open if it opened below it
  (a sell-stop fills at the gap), searched from the bar AFTER the stop's bar
  because the order of high and low inside one minute is not known;
* a re-entry bar that also reaches the new stop is stopped out in that bar
  (pessimistic, again because the order is unknown);
* time exit — the OPEN of the exit minute.

The **ATM Straddle** (Backtest page, Mine group, added 2026-10-31) is this
same rule with `otm_strikes=0`, `sl_pct=0`, no re-entry, every weekday, sold at
09:20 and bought back at 15:14 — the page posts those to the same endpoint —
plus a max ₹ loss on EACH leg on its own (simulate_leg_max_loss), 2,000 by
default, filled at the level like a resting stop order.

The module is pure: callers hand in the bars, so it never touches a broker.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

NIFTY_STRIKE_STEP = 50
DEFAULT_DAYS = (0, 1, 4)            # Monday, Tuesday, Friday
# An illiquid strike may print no bar in the entry minute; more than this late
# and the leg is not trusted as an entry.
MAX_ENTRY_LAG = pd.Timedelta(minutes=5)


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


def simulate_leg(bars: pd.DataFrame, entry_ts: pd.Timestamp, exit_ts: pd.Timestamp,
                 sl_pct: float, reentry: bool = True) -> Optional[Dict]:
    """One short leg through its session. None when it never had an entry.

    Returns {'fills': [{'entry', 'exit', 'entry_time', 'exit_time', 'reason'}],
    'pnl' (points, short), 'stops', 'reentered'}. Walks numpy arrays — the
    best-params sweep calls this tens of thousands of times."""
    idx = bars.index
    lo = int(idx.searchsorted(entry_ts, side='left'))
    hi = int(idx.searchsorted(exit_ts, side='left'))
    if hi <= lo or idx[lo] - entry_ts > MAX_ENTRY_LAG:
        return None
    op = bars['open'].to_numpy()
    hg = bars['high'].to_numpy()
    lw = bars['low'].to_numpy()
    cl = bars['close'].to_numpy()
    # sl_pct 0 = no stop: the leg is held to the time exit (the ATM Straddle).
    mult = 1.0 + sl_pct / 100.0 if sl_pct > 0 else float('inf')

    # The time-exit price: the exit minute's open, else the last print before.
    time_exit = float(op[hi]) if hi < len(idx) and idx[hi] == exit_ts else float(cl[hi - 1])

    def walk(start: int, entry_price: float) -> Dict:
        """Sell at `entry_price` and follow it from bar `start` on."""
        sl = entry_price * mult
        for pos in range(start, hi):
            if hg[pos] >= sl:
                return {'entry': entry_price, 'entry_time': idx[start],
                        'exit': max(sl, float(op[pos])),          # a gap fills at the open
                        'exit_time': idx[pos], 'reason': 'SL hit', 'pos': pos}
        return {'entry': entry_price, 'entry_time': idx[start], 'exit': time_exit,
                'exit_time': exit_ts, 'reason': 'Time exit', 'pos': None}

    first_entry = float(op[lo])
    first = walk(lo, first_entry)
    fills = [first]
    if reentry and first['reason'] == 'SL hit':
        # Back down to cost, strictly after the bar that stopped it.
        for pos in range(first['pos'] + 1, hi):
            if lw[pos] <= first_entry:
                fills.append(walk(pos, min(float(op[pos]), first_entry)))
                break
    for f in fills:
        f.pop('pos', None)
    return {
        'fills': fills,
        'pnl': sum(f['entry'] - f['exit'] for f in fills),
        'stops': sum(1 for f in fills if f['reason'] == 'SL hit'),
        'reentered': len(fills) > 1,
    }


def simulate_leg_max_loss(bars: pd.DataFrame, entry_ts: pd.Timestamp, exit_ts: pd.Timestamp,
                          max_loss_points: float) -> Optional[Dict]:
    """One short leg under a stop on ITS OWN ₹ loss (the ATM Straddle's), or
    None when it never had an entry.

    Sold at the entry minute's open, as in simulate_leg. The stop is an order
    resting at entry + `max_loss_points`: the first minute whose HIGH reaches
    it buys the leg back AT the level — or at that minute's open if it opened
    beyond it (a gap) — reason 'Max loss'; the other leg is not touched. So a
    leg loses the limit and no more, short of a gap. Until 2026-10-31 evening
    it was checked on minute closes and filled at the close, which booked
    ₹2,800+ on a ₹2,000 limit whenever the premium ran through it inside a
    minute. Otherwise it is bought back at the time exit. Same result shape as
    simulate_leg."""
    idx = bars.index
    lo = int(idx.searchsorted(entry_ts, side='left'))
    hi = int(idx.searchsorted(exit_ts, side='left'))
    if hi <= lo or idx[lo] - entry_ts > MAX_ENTRY_LAG:
        return None
    entry = float(bars['open'].iloc[lo])
    stop = entry + max_loss_points
    over = bars['high'].to_numpy()[lo:hi] >= stop
    if over.any():
        k = int(over.argmax())
        exit_px = max(stop, float(bars['open'].iloc[lo + k]))      # a gap fills at the open
        exit_time, reason = idx[lo + k], 'Max loss'
    else:
        exit_px = float(bars['open'].iloc[hi]) if hi < len(idx) and idx[hi] == exit_ts \
            else float(bars['close'].iloc[hi - 1])
        exit_time, reason = exit_ts, 'Time exit'
    return {
        'fills': [{'entry': entry, 'entry_time': idx[lo], 'exit': exit_px,
                   'exit_time': exit_time, 'reason': reason}],
        'pnl': entry - exit_px, 'stops': 0, 'reentered': False,
    }


def atm_strike(spot: float, step: int = NIFTY_STRIKE_STEP) -> int:
    return int(round(spot / step) * step)


def strikes_for(spot: float, otm_strikes: int, step: int = NIFTY_STRIKE_STEP) -> Dict[str, int]:
    atm = atm_strike(spot, step)
    return {'CE': atm + otm_strikes * step, 'PE': atm - otm_strikes * step}


def weekly_expiry(day: date, trading_days: Iterable[date]) -> Optional[date]:
    """The weekly expiry running on `day` — the day's own when it IS expiry day."""
    from trading_app.service.expiry_calendar import expiry_on_or_after
    return expiry_on_or_after(day, trading_days, cadence='weekly')


def run_day(day: date, spot: float, expiry: date,
            bars_for: Callable[[date, int, str], Optional[pd.DataFrame]],
            entry_hm=(9, 30), exit_hm=(10, 30), otm_strikes: int = 2,
            sl_pct: float = 60.0, reentry: bool = True,
            max_loss_points: float = 0.0) -> Optional[Dict]:
    """One session's strangle, or None when neither leg could be priced.

    `bars_for(expiry, strike, right)` returns that contract's minute frame.
    `max_loss_points` above 0 stops each leg on its own loss
    (simulate_leg_max_loss) — the ATM Straddle's max ₹ loss per strike; the
    `sl_pct` and `reentry` are then not used."""
    entry_ts = pd.Timestamp(datetime(day.year, day.month, day.day, *entry_hm))
    exit_ts = pd.Timestamp(datetime(day.year, day.month, day.day, *exit_hm))
    strikes = strikes_for(spot, otm_strikes)
    frames: Dict[str, pd.DataFrame] = {}
    for right, strike in strikes.items():
        bars = bars_for(expiry, strike, right)
        if bars is not None and not bars.empty:
            frames[right] = bars
    legs: Dict[str, Dict] = {}
    for right, bars in frames.items():
        res = (simulate_leg_max_loss(bars, entry_ts, exit_ts, max_loss_points) if max_loss_points > 0
               else simulate_leg(bars, entry_ts, exit_ts, sl_pct, reentry))
        if res is not None:
            res['strike'] = strikes[right]
            legs[right] = res
    if not legs:
        return None
    return {'day': day, 'spot': spot, 'expiry': expiry, 'legs': legs,
            'complete': len(legs) == 2}


def trade_row(res: Dict, qty: int, lots: int, lot_size: int, brokerage_per_leg: float,
              entry_hm, exit_hm) -> Dict:
    """Flatten a run_day result into one grid row (₹ at `qty`)."""
    legs = res['legs']
    row: Dict = {
        'date': res['day'].isoformat(),
        'entry_time': datetime.combine(res['day'], datetime.min.time()).replace(
            hour=entry_hm[0], minute=entry_hm[1]).isoformat(),
        'exit_time': datetime.combine(res['day'], datetime.min.time()).replace(
            hour=exit_hm[0], minute=exit_hm[1]).isoformat(),
        'weekday': res['day'].strftime('%a'),
        'spot': round(res['spot'], 2),
        'expiry': res['expiry'].isoformat(),
        'qty': qty, 'lots': lots, 'lot_size': lot_size,
        'complete': res['complete'],
    }
    points = stops = reentries = orders = 0
    reasons = []
    for right in ('CE', 'PE'):
        leg = legs.get(right)
        k = right.lower()
        if leg is None:
            row.update({f'{k}_strike': None, f'{k}_entry': None, f'{k}_exit': None, f'{k}_pnl': None})
            continue
        fills = leg['fills']
        row[f'{k}_strike'] = leg['strike']
        row[f'{k}_entry'] = round(fills[0]['entry'], 2)
        row[f'{k}_exit'] = round(fills[0]['exit'], 2)
        row[f'{k}_reentry'] = round(fills[1]['entry'], 2) if len(fills) > 1 else None
        row[f'{k}_reentry_exit'] = round(fills[1]['exit'], 2) if len(fills) > 1 else None
        row[f'{k}_pnl'] = round(leg['pnl'], 2)
        row[f'{k}_outcome'] = ' → '.join(f['reason'] for f in fills)
        points += leg['pnl']
        stops += leg['stops']
        reentries += 1 if leg['reentered'] else 0
        orders += len(fills)
        reasons.append(f"{right}: {row[f'{k}_outcome']}")
    row['pnl'] = round(points, 2)
    row['max_loss_legs'] = sum(1 for leg in legs.values() for f in leg['fills']
                               if f['reason'] == 'Max loss')
    row['max_loss_hit'] = row['max_loss_legs'] > 0
    row['stops'] = stops
    row['reentries'] = reentries
    row['exit_reason'] = ' | '.join(reasons)
    row['result'] = 'WIN' if points > 0 else ('LOSS' if points < 0 else 'SCRATCH')
    row['brokerage'] = round(brokerage_per_leg * orders, 2)
    row['gross_pnl_rupees'] = round(points * qty, 2)
    row['pnl_rupees'] = round(row['gross_pnl_rupees'] - row['brokerage'], 2)
    return row


def summarise(rows: List[Dict]) -> Dict:
    """Stats on the net ₹ P&L of the day rows."""
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
    return {
        'total_trades': n,
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(100.0 * len(wins) / n, 2) if n else 0.0,
        'total_pnl_rupees': round(sum(net), 2),
        'total_gross_pnl_rupees': round(sum(r['gross_pnl_rupees'] for r in rows), 2),
        'total_brokerage': round(sum(r['brokerage'] for r in rows), 2),
        'avg_win_rupees': round(sum(wins) / len(wins), 2) if wins else 0.0,
        'avg_loss_rupees': round(gross_loss / len(losses), 2) if losses else 0.0,
        'profit_factor': round(sum(wins) / gross_loss, 2) if gross_loss else (999.0 if wins else 0.0),
        'max_drawdown_rupees': round(max_dd, 2),
        'sl_hits': sum(r['stops'] for r in rows),
        'reentries': sum(r['reentries'] for r in rows),
        'one_leg_days': sum(1 for r in rows if not r['complete']),
        'max_loss_days': sum(1 for r in rows if r.get('max_loss_hit')),
        'max_loss_legs': sum(r.get('max_loss_legs', 0) for r in rows),
    }


# ── ATM Straddle: Find Best Params ───────────────────────────────────────────
STRADDLE_ENTRIES = ((9, 15), (9, 20), (9, 30), (9, 45), (10, 0))
STRADDLE_EXITS = ((13, 0), (14, 0), (14, 30), (15, 0), (15, 14))
STRADDLE_MAX_LOSS = (0, 1000, 1500, 2000, 2500, 3000, 4000, 5000)   # ₹ per leg at the run's lots; 0 = no stop


def sweep_straddle(sessions: List[Dict], lots: int, lot_size: int,
                   brokerage_per_leg: float = 100.0, min_trades: int = 10,
                   entry_grid=STRADDLE_ENTRIES, exit_grid=STRADDLE_EXITS,
                   loss_grid=STRADDLE_MAX_LOSS) -> List[Dict]:
    """Score every entry × exit × max ₹ loss per leg on already-fetched sessions.

    `sessions`: [{'day', 'expiry', 'spots': {entry_hm: spot}, 'bars':
    {(strike, right): frame}}] — the ATM strike moves with the entry time, so
    a session carries the spot at each entry and the bars of every strike
    those spots round to. Each combo is what run_day(otm_strikes=0, sl_pct=0,
    reentry=False, max_loss_points=₹ / qty) + trade_row would book, worked out
    here without them: each leg's closes are read once per (day, entry) and
    serve every exit and every loss, which keeps 200 combos over a year to
    seconds. Ranked by net ₹."""
    qty = lots * lot_size
    rows: Dict[tuple, List[Dict]] = {}

    for sess in sessions:
        d = sess['day']
        for entry_hm in entry_grid:
            spot = sess['spots'].get(entry_hm)
            if spot is None:
                continue
            strike = atm_strike(spot)
            entry_ts = pd.Timestamp(datetime(d.year, d.month, d.day, *entry_hm))
            legs = []
            for right in ('CE', 'PE'):          # trade_row's order, so the float sums agree
                bars = sess['bars'].get((strike, right))
                if bars is None or bars.empty:
                    continue
                idx = bars.index
                lo = int(idx.searchsorted(entry_ts, side='left'))
                if lo >= len(idx) or idx[lo] - entry_ts > MAX_ENTRY_LAG:
                    continue
                legs.append((idx, lo, bars['open'].to_numpy(), bars['high'].to_numpy(),
                             bars['close'].to_numpy()))
            if not legs:
                continue
            for exit_hm in exit_grid:
                exit_ts = pd.Timestamp(datetime(d.year, d.month, d.day, *exit_hm))
                if exit_ts <= entry_ts:
                    continue
                # Per leg: entry, its opens / highs up to the exit, its time-exit price.
                walks = []
                for idx, lo, op, hg, cl in legs:
                    hi = int(idx.searchsorted(exit_ts, side='left'))
                    if hi <= lo:
                        continue
                    entry = float(op[lo])
                    time_px = float(op[hi]) if hi < len(idx) and idx[hi] == exit_ts else float(cl[hi - 1])
                    walks.append((entry, op[lo:hi], hg[lo:hi], time_px))
                if not walks:
                    continue
                for limit in loss_grid:
                    points, hit_legs = 0.0, 0
                    for entry, opens, highs, time_px in walks:
                        exit_px = time_px
                        if limit > 0:
                            stop = entry + limit / qty
                            over = highs >= stop
                            if over.any():
                                exit_px = max(stop, float(opens[int(over.argmax())]))
                                hit_legs += 1
                        points += entry - exit_px
                    gross = round(points * qty, 2)
                    brokerage = round(brokerage_per_leg * len(walks), 2)
                    rows.setdefault((entry_hm, exit_hm, limit), []).append({
                        'pnl_rupees': round(gross - brokerage, 2), 'gross_pnl_rupees': gross,
                        'brokerage': brokerage, 'stops': 0, 'reentries': 0,
                        'complete': len(walks) == 2, 'max_loss_hit': hit_legs > 0,
                        'max_loss_legs': hit_legs})

    out: List[Dict] = []
    for (entry_hm, exit_hm, limit), rs in rows.items():
        if len(rs) < min_trades:
            continue
        s = summarise(rs)
        out.append({
            'entry_time': f'{entry_hm[0]:02d}:{entry_hm[1]:02d}',
            'exit_time': f'{exit_hm[0]:02d}:{exit_hm[1]:02d}',
            'max_loss_rupees': limit,
            'total_trades': s['total_trades'], 'win_rate': s['win_rate'],
            'gross_pnl_rupees': s['total_gross_pnl_rupees'],
            'net_pnl_rupees': s['total_pnl_rupees'],
            'avg_pnl_rupees': round(s['total_pnl_rupees'] / s['total_trades'], 2),
            'profit_factor': s['profit_factor'],
            'max_drawdown_rupees': s['max_drawdown_rupees'],
            'max_loss_days': s['max_loss_days'],
            'max_loss_legs': s['max_loss_legs'],
        })
    out.sort(key=lambda r: r['net_pnl_rupees'], reverse=True)
    return out
