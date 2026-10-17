"""NIFTY weekly OTM short strangle with a per-leg stop and one re-entry.

The rule (added 2026-10-17):

* **Days** — Monday, Tuesday and Friday (configurable).
* **Entry** — at ENTRY (09:30) SELL one OTM CE and one OTM PE of the CURRENT
  weekly expiry, `otm_strikes` strikes away from the ATM on the 09:30 spot.
  The expiry day itself is traded on its own expiring contract.
* **Target** — none. Whatever is still open at EXIT (10:30) is bought back.
* **Stop** — each leg on its own: `sl_pct` % above ITS entry premium (60 % →
  stop at 1.6 × entry). One leg stopping does not touch the other.
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
    'pnl' (points, short), 'stops', 'reentered'}.
    """
    window = bars[(bars.index >= entry_ts) & (bars.index < exit_ts)]
    if window.empty or window.index[0] - entry_ts > MAX_ENTRY_LAG:
        return None
    mult = 1.0 + sl_pct / 100.0

    # The time-exit price: the exit minute's open, else the last print before.
    if exit_ts in bars.index:
        time_exit = float(bars.at[exit_ts, 'open'])
    else:
        before = bars[bars.index < exit_ts]
        time_exit = float(before['close'].iloc[-1])

    def walk(start_pos: int, entry_price: float, entry_time) -> Dict:
        """Sell at `entry_price` and follow it from bar `start_pos` on."""
        sl = entry_price * mult
        for pos in range(start_pos, len(window)):
            row = window.iloc[pos]
            if row['high'] >= sl:
                return {'entry': entry_price, 'entry_time': entry_time,
                        'exit': max(sl, float(row['open'])),     # a gap fills at the open
                        'exit_time': window.index[pos], 'reason': 'SL hit', 'pos': pos}
        return {'entry': entry_price, 'entry_time': entry_time, 'exit': time_exit,
                'exit_time': exit_ts, 'reason': 'Time exit', 'pos': None}

    first_entry = float(window.iloc[0]['open'])
    first = walk(0, first_entry, window.index[0])
    fills = [first]
    if reentry and first['reason'] == 'SL hit':
        # Back down to cost, strictly after the bar that stopped it.
        for pos in range(first['pos'] + 1, len(window)):
            row = window.iloc[pos]
            if row['low'] <= first_entry:
                fill = min(float(row['open']), first_entry)
                second = walk(pos, fill, window.index[pos])
                fills.append(second)
                break
    for f in fills:
        f.pop('pos', None)
    return {
        'fills': fills,
        'pnl': sum(f['entry'] - f['exit'] for f in fills),
        'stops': sum(1 for f in fills if f['reason'] == 'SL hit'),
        'reentered': len(fills) > 1,
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
            sl_pct: float = 60.0, reentry: bool = True) -> Optional[Dict]:
    """One session's strangle, or None when neither leg could be priced.

    `bars_for(expiry, strike, right)` returns that contract's minute frame."""
    entry_ts = pd.Timestamp(datetime(day.year, day.month, day.day, *entry_hm))
    exit_ts = pd.Timestamp(datetime(day.year, day.month, day.day, *exit_hm))
    strikes = strikes_for(spot, otm_strikes)
    legs: Dict[str, Dict] = {}
    for right, strike in strikes.items():
        bars = bars_for(expiry, strike, right)
        if bars is None or bars.empty:
            continue
        res = simulate_leg(bars, entry_ts, exit_ts, sl_pct, reentry)
        if res is not None:
            res['strike'] = strike
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
    }
