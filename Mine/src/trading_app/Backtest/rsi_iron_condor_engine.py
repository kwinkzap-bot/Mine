"""NIFTY monthly RSI(14) iron condor / 1:2:1 ratio — positional, judged at the daily close.

The rule (added 2026-11-01, from Uday Chandra's "Iron Condor" livestream, Sept 2026):

* **Signal** — RSI(14) on DAILY NIFTY, read at the close only. 40-60 is sideways,
  above 60 bullish, below 40 bearish.
* **The month** — every position is in the MONTHLY expiry. On each expiry day's
  close the next month's position is opened from that day's RSI zone (the first
  session of the range does the same):
    - 40-60 → a **1:1 iron condor**: sell a PE and a CE `wing` points apart from
      their hedges, short strikes pushed as far from ATM as they can go while the
      credit is still ≥ `credit_rr` × the max loss (wing − credit). There is no
      fixed delta or distance — "1:1" is the rule, so a high VIX puts the shorts
      further out by itself, which the video calls the point of it.
    - <40 → a **bearish 1:2:1** in PEs: BUY ATM−50, SELL 2× ATM−200, BUY ATM−250.
    - >60 → the **bullish 1:2:1** in CEs, the mirror way.
* **During the month**, at each close:
    - iron condor: RSI out of 40-60 → close it and open that side's 1:2:1. The
      index closing past a breakeven while RSI is still 40-60 → close it and open
      a fresh 1:1 condor round today's price (the roll);
    - 1:2:1: open profit ≥ `book_pct` (80) % of its max profit → book, and with
      `wait_after_book` sit out until the expiry ("4 % is enough, next month").
      RSI back in 40-60 → close and open an iron condor; RSI across the far band
      → flip to the other side;
    - month stop: the cycle's booked + open ₹ at −`month_sl_pct` (4) % of capital
      → close and sit out until the expiry.
* **Expiry** — whatever is open settles at intrinsic off the index close.

What the video does that this does not: a mid-day breakeven alert (here the
breach is read at the close, as everything else is), and the iron condor's
"profit lock" (pull the bought leg a strike in once ~2 % is made), which it
describes only in passing. Its "push the 1:2:1 out of the money again after a
book" is the variant it says it does not use, so it is left out.

Fills are each contract's own DAILY close (ICICI Breeze serves expired
contracts' daily candles, one request per contract for its whole life), ±
`slippage` points per leg — the video trades at 15:15-15:20, close enough to
the 15:30 close for a monthly position. A strike with no trade that day is
marked at its last close before it. Pure: the caller hands in the prices.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import NormalDist
from typing import Callable, Dict, List, Optional

import pandas as pd

from trading_app.Backtest.option_selling_engine import NIFTY_STRIKE_STEP, atm_strike
from trading_app.Backtest.st_bb_rsi_engine import rsi as wilder_rsi

RSI_PERIOD = 14
UPPER = 60.0
LOWER = 40.0
WING = 200
CREDIT_RR = 1.0
NEAR_OFF = 50
SHORT_OFF = 200
FAR_OFF = 250
BOOK_PCT = 80.0
MONTH_SL_PCT = 4.0
CAPITAL_PER_LOT = 110000.0
SLIPPAGE = 1.0
MAX_OFFSET_STRIKES = 40
MIN_SELL_PRICE = 0.05

KIND_LABEL = {'IC': 'Iron condor', 'BULL': 'Bull 1:2:1', 'BEAR': 'Bear 1:2:1'}

PriceOn = Callable[[date, int, str, date], Optional[float]]


def with_rsi(daily: pd.DataFrame, period: int = RSI_PERIOD) -> pd.DataFrame:
    out = daily.copy()
    out['rsi'] = wilder_rsi(out['close'].astype(float).to_numpy(), period)
    return out


def zone_of(r: float, upper: float = UPPER, lower: float = LOWER) -> str:
    return 'BULL' if r > upper else ('BEAR' if r < lower else 'IC')


def intrinsic(right: str, strike: float, spot: float) -> float:
    return max(spot - strike, 0.0) if right == 'CE' else max(strike - spot, 0.0)


def entry_fill(px: float, q: int, slip: float) -> float:
    return max(px + slip, 0.0) if q > 0 else max(px - slip, MIN_SELL_PRICE)


def exit_fill(px: float, q: int, slip: float) -> float:
    return max(px - slip, 0.0) if q > 0 else px + slip


def pick_condor(spot: float, wing: int, rr: float, price: Callable[[int, str], Optional[float]],
                slip: float = SLIPPAGE, step: int = NIFTY_STRIKE_STEP) -> Optional[Dict]:
    """The symmetric 1:1 condor: the largest short offset (in strikes from ATM)
    whose net credit, after slippage, is still ≥ `rr` × (wing − credit).

    Starts where the ATM straddle says the target sits (credit / wing ≈ the
    chance of finishing past a short, so each side ≈ half of rr / (1 + rr)) and
    walks a strike at a time, so it prices a dozen contracts, not a chain. When
    no offset reaches `rr` (very low vol, or days from expiry) the best-paying
    one is taken. Returns {'offset', 'ps', 'pb', 'cs', 'cb', 'prices', 'credit'}
    or None when nothing could be priced."""
    atm = atm_strike(spot, step)
    seen: Dict[int, Optional[Dict]] = {}

    def at(i: int) -> Optional[Dict]:
        if i not in seen:
            ps, cs = atm - i * step, atm + i * step
            pb, cb = ps - wing, cs + wing
            p = {(ps, 'PE'): price(ps, 'PE'), (pb, 'PE'): price(pb, 'PE'),
                 (cs, 'CE'): price(cs, 'CE'), (cb, 'CE'): price(cb, 'CE')}
            if any(v is None for v in p.values()):
                seen[i] = None
            else:
                credit = (entry_fill(p[(ps, 'PE')], -1, slip) - entry_fill(p[(pb, 'PE')], 1, slip)
                          + entry_fill(p[(cs, 'CE')], -1, slip) - entry_fill(p[(cb, 'CE')], 1, slip))
                seen[i] = {'offset': i, 'ps': ps, 'pb': pb, 'cs': cs, 'cb': cb,
                           'prices': p, 'credit': credit}
        return seen[i]

    def ok(c: Optional[Dict]) -> bool:
        return c is not None and c['credit'] > 0 and c['credit'] >= rr * (wing - c['credit'])

    ce, pe = price(atm, 'CE'), price(atm, 'PE')
    i0 = 1
    if ce and pe:
        frac = rr / (1.0 + rr)
        z = NormalDist().inv_cdf(min(0.999, 1.0 - frac / 2.0))
        move = (ce + pe) / 0.8                          # straddle ≈ 0.8 σ√T S
        i0 = max(1, min(MAX_OFFSET_STRIKES, int(round(z * move / step))))

    if ok(at(i0)):
        i = i0
        while i < MAX_OFFSET_STRIKES and ok(at(i + 1)):
            i += 1
        return at(i)
    for i in range(i0 - 1, 0, -1):
        if ok(at(i)):
            return at(i)
    priced = [c for c in seen.values() if c is not None and c['credit'] > 0]
    if not priced:
        return None
    return max(priced, key=lambda c: c['credit'] / max(wing - c['credit'], 0.01))


def run(daily: pd.DataFrame, start: date, end: date,
        expiry_after: Callable[[date], Optional[date]], price_on: PriceOn, *,
        upper: float = UPPER, lower: float = LOWER, wing: int = WING,
        credit_rr: float = CREDIT_RR, roll_on_breakeven: bool = True,
        near_off: int = NEAR_OFF, short_off: int = SHORT_OFF, far_off: int = FAR_OFF,
        book_pct: float = BOOK_PCT, wait_after_book: bool = True,
        month_sl_pct: float = MONTH_SL_PCT, capital_per_lot: float = CAPITAL_PER_LOT,
        lots: int = 1, lot_size: int = 65, brokerage_per_leg: float = 100.0,
        slippage: float = SLIPPAGE) -> Dict:
    """Every position the rule holds between `start` and `end`.

    `daily`: date-indexed NIFTY closes with an 'rsi' column (warm-up before
    `start` is fine). `expiry_after(day)` → the first monthly expiry strictly
    after `day` (the next month's on an expiry day). `price_on(expiry, strike,
    right, day)` → that contract's close on `day`, or its last before it.

    Returns {'trades': [...], 'cycles': [...], 'skipped': [...]}: a trade is one
    structure from open to close; a cycle is one monthly expiry."""
    capital = capital_per_lot * lots
    qty = lots * lot_size
    trades: List[Dict] = []
    cycles: List[Dict] = []
    skipped: List[Dict] = []
    pos: Optional[Dict] = None
    cycle: Optional[Dict] = None
    waiting = False

    def open_pos(kind: str, day: date, spot: float, r: float, why: str) -> Optional[Dict]:
        exp = cycle['expiry']
        price = lambda k, right: price_on(exp, int(k), right, day)
        legs = []
        if kind == 'IC':
            c = pick_condor(spot, wing, credit_rr, price, slippage)
            if c is None:
                skipped.append({'date': day.isoformat(), 'kind': kind, 'why': 'no premiums for a condor'})
                return None
            for k, right, q in ((c['pb'], 'PE', 1), (c['ps'], 'PE', -1),
                                (c['cs'], 'CE', -1), (c['cb'], 'CE', 1)):
                legs.append({'strike': k, 'right': right, 'qty': q,
                             'entry': entry_fill(c['prices'][(k, right)], q, slippage)})
        else:
            right = 'CE' if kind == 'BULL' else 'PE'
            sgn = 1 if kind == 'BULL' else -1
            atm = atm_strike(spot)
            for off, q in ((near_off, 1), (short_off, -2), (far_off, 1)):
                k = atm + sgn * off
                px = price(k, right)
                if px is None:
                    skipped.append({'date': day.isoformat(), 'kind': kind,
                                    'why': f'no premium for {k} {right}'})
                    return None
                legs.append({'strike': k, 'right': right, 'qty': q, 'entry': entry_fill(px, q, slippage)})
        cost = sum(l['qty'] * l['entry'] for l in legs)        # + debit / − credit
        p = {'kind': kind, 'expiry': exp, 'legs': legs, 'entry_date': day, 'spot': spot,
             'rsi': r, 'why': why, 'cost': cost}
        if kind == 'IC':
            credit = -cost
            ps = next(l['strike'] for l in legs if l['right'] == 'PE' and l['qty'] < 0)
            cs = next(l['strike'] for l in legs if l['right'] == 'CE' and l['qty'] < 0)
            p.update({'be_lo': ps - credit, 'be_hi': cs + credit,
                      'max_profit': credit, 'max_loss': wing - credit})
        else:
            p.update({'be_lo': None, 'be_hi': None,
                      'max_profit': (short_off - near_off) - cost, 'max_loss': cost})
        return p

    def mark(p: Dict, day: date, spot: float, final: bool) -> List[float]:
        out = []
        for l in p['legs']:
            if final:
                out.append(intrinsic(l['right'], l['strike'], spot))
                continue
            px = price_on(p['expiry'], int(l['strike']), l['right'], day)
            out.append(exit_fill(l['entry'] if px is None else px, l['qty'], slippage))
        return out

    def points(p: Dict, exits: List[float]) -> float:
        return sum(l['qty'] * (x - l['entry']) for l, x in zip(p['legs'], exits))

    def costs(p: Dict) -> float:
        return len(p['legs']) * brokerage_per_leg

    def close_pos(p: Dict, day: date, spot: float, r: float, reason: str, final: bool = False):
        exits = mark(p, day, spot, final)
        pts = points(p, exits)
        gross = pts * qty
        net = gross - costs(p)
        cycle['pnl'] += net
        cycle['trades'] += 1
        trades.append({
            'cycle': cycle['expiry'].isoformat(),
            'kind': p['kind'], 'structure': KIND_LABEL[p['kind']], 'signal': p['why'],
            'entry_date': p['entry_date'].isoformat(), 'exit_date': day.isoformat(),
            'days_held': (day - p['entry_date']).days, 'expiry': p['expiry'].isoformat(),
            'spot_in': round(p['spot'], 2), 'spot_out': round(spot, 2),
            'rsi_in': round(float(p['rsi']), 2), 'rsi_out': round(float(r), 2),
            'legs': '  '.join(f"{'+' if l['qty'] > 0 else ''}{l['qty']} {int(l['strike'])}{l['right']}"
                              for l in p['legs']),
            'entries': '  '.join(f"{l['entry']:.2f}" for l in p['legs']),
            'exits': '  '.join(f"{x:.2f}" for x in exits),
            'net_premium': round(-p['cost'], 2),
            'max_profit': round(p['max_profit'], 2), 'max_loss': round(p['max_loss'], 2),
            'be_lo': None if p['be_lo'] is None else round(p['be_lo'], 2),
            'be_hi': None if p['be_hi'] is None else round(p['be_hi'], 2),
            'exit_reason': reason, 'pnl': round(pts, 2), 'qty': qty, 'lots': lots,
            'lot_size': lot_size, 'gross_pnl_rupees': round(gross, 2), 'brokerage': costs(p),
            'pnl_rupees': round(net, 2), 'pct_of_capital': round(100.0 * net / capital, 2),
            'result': 'WIN' if net > 0 else ('LOSS' if net < 0 else 'SCRATCH'),
        })

    def finish_cycle(open_at_end: bool = False):
        cycles.append({'expiry': cycle['expiry'].isoformat(), 'month': cycle['expiry'].strftime('%Y-%m'),
                       'started': cycle['started'].isoformat(), 'trades': cycle['trades'],
                       'pnl_rupees': round(cycle['pnl'], 2),
                       'pct_of_capital': round(100.0 * cycle['pnl'] / capital, 2),
                       'ended': cycle['ended'] or ('open' if open_at_end else 'expiry'),
                       'complete': not open_at_end})

    in_range = daily[(daily.index.date >= start) & (daily.index.date <= end)]
    last_day = last_spot = last_r = None
    for ts, row in in_range.iterrows():
        day, spot, r = ts.date(), float(row['close']), float(row['rsi'])
        if math.isnan(r):
            continue
        last_day, last_spot, last_r = day, spot, r
        zone = zone_of(r, upper, lower)

        if cycle is not None and day >= cycle['expiry']:
            if pos is not None:
                close_pos(pos, day, spot, r, 'Expiry', final=True)
                pos = None
            finish_cycle()
            cycle, waiting = None, False

        if cycle is None:
            exp = expiry_after(day)
            if exp is None:
                skipped.append({'date': day.isoformat(), 'kind': zone, 'why': 'no monthly expiry'})
                continue
            cycle = {'expiry': exp, 'started': day, 'pnl': 0.0, 'trades': 0, 'ended': None}
            pos = open_pos(zone, day, spot, r, 'New month' if trades or cycles else 'Start')
            continue

        if pos is None:
            if not waiting:                     # an open that could not be priced: try again today
                pos = open_pos(zone, day, spot, r, 'Retry')
            continue

        exits = mark(pos, day, spot, False)
        pts = points(pos, exits)
        open_rs = pts * qty - costs(pos)
        if month_sl_pct > 0 and cycle['pnl'] + open_rs <= -month_sl_pct / 100.0 * capital:
            close_pos(pos, day, spot, r, 'Month stop')
            pos, waiting = None, True
            cycle['ended'] = 'month stop'
            continue

        if pos['kind'] == 'IC':
            if zone != 'IC':
                why = f'RSI > {upper:g}' if zone == 'BULL' else f'RSI < {lower:g}'
                close_pos(pos, day, spot, r, why)
                pos = open_pos(zone, day, spot, r, why)
            elif roll_on_breakeven and (spot < pos['be_lo'] or spot > pos['be_hi']):
                close_pos(pos, day, spot, r, 'Breakeven breached')
                pos = open_pos('IC', day, spot, r, 'Roll')
        else:
            if pos['max_profit'] > 0 and pts >= book_pct / 100.0 * pos['max_profit']:
                close_pos(pos, day, spot, r, f'Booked {book_pct:g}%')
                if wait_after_book:
                    pos, waiting = None, True
                    cycle['ended'] = 'booked'
                else:
                    pos = open_pos(zone, day, spot, r, 'After book')
            elif zone != pos['kind']:
                why = 'RSI back in band' if zone == 'IC' else 'RSI flipped'
                close_pos(pos, day, spot, r, why)
                pos = open_pos(zone, day, spot, r, why)

    if cycle is not None:
        if pos is not None:
            close_pos(pos, last_day, last_spot, last_r, 'Open (marked)')
        finish_cycle(open_at_end=True)
    return {'trades': trades, 'cycles': cycles, 'skipped': skipped}


def summarise(trades: List[Dict], cycles: List[Dict], capital: float) -> Dict:
    """Stats on net ₹, by trade and by monthly cycle, plus year and month tables."""
    net = [t['pnl_rupees'] for t in trades]
    peak = equity = max_dd = 0.0
    for x in net:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x < 0]
    gross_loss = abs(sum(losses))
    cyc_pnl = [c['pnl_rupees'] for c in cycles]
    years: Dict[str, Dict] = {}
    months: Dict[str, Dict] = {}
    for c in cycles:
        y, m = c['month'][:4], int(c['month'][5:7])
        mo = months.setdefault(y, {'year': y, 'total': 0.0})
        mo[f'm{m:02d}'] = c['pct_of_capital']
        mo['total'] = round(mo['total'] + c['pct_of_capital'], 2)
        yr = years.setdefault(y, {'year': y, 'cycles': 0, 'won': 0, 'trades': 0,
                                  'net_pnl_rupees': 0.0, 'pct_of_capital': 0.0})
        yr['cycles'] += 1
        yr['won'] += 1 if c['pnl_rupees'] > 0 else 0
        yr['trades'] += c['trades']
        yr['net_pnl_rupees'] = round(yr['net_pnl_rupees'] + c['pnl_rupees'], 2)
        yr['pct_of_capital'] = round(yr['pct_of_capital'] + c['pct_of_capital'], 2)
    reasons: Dict[str, int] = {}
    for t in trades:
        key = 'Booked' if t['exit_reason'].startswith('Booked') else t['exit_reason']
        reasons[key] = reasons.get(key, 0) + 1
    total = sum(net)
    return {
        'total_trades': len(trades),
        'cycles': len(cycles), 'cycles_won': sum(1 for x in cyc_pnl if x > 0),
        'cycles_lost': sum(1 for x in cyc_pnl if x < 0),
        'cycle_win_rate': round(100.0 * sum(1 for x in cyc_pnl if x > 0) / len(cycles), 1) if cycles else 0.0,
        'best_cycle_pct': round(100.0 * max(cyc_pnl) / capital, 2) if cycles else 0.0,
        'worst_cycle_pct': round(100.0 * min(cyc_pnl) / capital, 2) if cycles else 0.0,
        'avg_cycle_pct': round(100.0 * sum(cyc_pnl) / len(cycles) / capital, 2) if cycles else 0.0,
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
        'total_pnl_rupees': round(total, 2),
        'total_pct_of_capital': round(100.0 * total / capital, 2),
        'total_gross_pnl_rupees': round(sum(t['gross_pnl_rupees'] for t in trades), 2),
        'total_brokerage': round(sum(t['brokerage'] for t in trades), 2),
        'total_points': round(sum(t['pnl'] for t in trades), 2),
        'profit_factor': round(sum(wins) / gross_loss, 2) if gross_loss else (999.0 if wins else 0.0),
        'max_drawdown_rupees': round(max_dd, 2),
        'max_drawdown_pct': round(100.0 * max_dd / capital, 2),
        'iron_condors': sum(1 for t in trades if t['kind'] == 'IC'),
        'bull_trades': sum(1 for t in trades if t['kind'] == 'BULL'),
        'bear_trades': sum(1 for t in trades if t['kind'] == 'BEAR'),
        'exit_reasons': reasons,
        'by_year': [years[k] for k in sorted(years)],
        'by_month_pct': [months[k] for k in sorted(months)],
    }
