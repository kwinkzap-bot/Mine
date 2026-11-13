"""Monthly RSI Breakout backtest — a long-term stock-picking rule from a video
(Darshan Rathod, "the hedge fund / mutual fund strategy"), run on every NSE stock.

The rule, on MONTHLY candles built from daily bars (the video's). `timeframe`
runs the same rule on WEEKLY (Mon-Sun) or DAILY candles instead — read every
"month" below as "candle"; the RSI, EMA and NIFTY filter are then that
timeframe's, while the 5-a-month cap and the equity curve stay per calendar month:

  * scan   — on the last day of a month, after the close: the month's RSI(14)
             is between 60 and 65 (both bounds kept; above 65 or below 60 is not
             taken). The RSI bands are 60/40 instead of 70/30.
  * entry  — the NEXT month only: a buy order at that month's HIGH. Filled the
             first day the high trades at or above it, at that level, or at the
             day's open when the day gaps straight over it. Not broken that
             month = no trade.
  * stop   — the 20-period monthly EMA. It sizes the position (risk = entry −
             EMA at the scan month), and drives the exit:
  * exit   — a monthly candle CLOSES below its 20 EMA, and a later candle
             trades below that candle's LOW. Filled at that low, or at the open
             on a gap. A month closing back above the EMA disarms it; a second
             close below re-arms at the newer candle's low (the video says
             "mark the low of that candle" and does not say which one when two
             close below in a row — the latest is the tighter reading).
             The month a position is entered in can arm it.
  * limits — at most `max_new` (5) new stocks a month; no new entries while
             NIFTY's monthly RSI at the scan is below 40 (holdings are kept).
  * size   — the video's calculator: qty = capital × risk % ÷ (entry − stop).
  * costs  — `cost_pct` of the value each side (STT, stamp, exchange, GST, DP)
             plus a flat `brokerage` ₹ per order, each buy and each sell.

Two things the video does not give, so they are boxes here:

  * a portfolio: capital is real cash. Each entry is sized on the current
    equity (cash + holdings at the last month-end close), never more than
    `max_pos_pct` of it; an entry the cash left cannot pay for in full
    is skipped (not bought smaller). Without that, 5 new stocks a month held for years
    would need an unbounded amount of money.
  * a liquidity floor (`min_turnover_cr`, average daily ₹ traded in the scan
    month): the NSE list carries thousands of illiquid names.

Survivorship: the universe is TODAY's NSE list, so stocks delisted since are
missing — the result is flattered by that, as every backtest on a current list is.

Pure: daily DataFrames in, trades and an equity curve out. Loading the
per-stock files lives in the route (app/routes/api.py).
"""
from __future__ import annotations

import heapq
import itertools
import math
from datetime import date
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

RSI_LEN = 14
EMA_LEN = 20
# The page's defaults. Since 2026-11-13 they are the swept best, not the video's (60-65 band,
# 20 EMA, 1 % risk, all stocks): F&O only, RSI 65-75, a 10 EMA, 0.5 % risk — better in 10 of
# 12 years 2015-26 at ₹1 L (CAGR 20.4 % vs 9.75 %, max DD 19 % vs 35 %); see CLAUDE.md.
# size_mode 'fixed' (added the same day, at the user's ask) buys the same ₹ amount of every
# stock instead of sizing each on its own risk; risk_pct/max_pos_pct only apply under 'risk'.
DEFAULTS = {
    'universe': 'fno',
    'rsi_lo': 65.0,
    'rsi_hi': 75.0,
    'ema_len': 10,
    'exit_rule': 'low_break',
    'nifty_ema': 0,             # 0 = off; else no new buys while NIFTY closes under its this-length EMA
    'max_new': 5,
    'nifty_floor': 40.0,        # 0 = off
    'capital': 100_000.0,         # ₹1 L: every buy is paid from this pot and what it has earned, nothing added
    'size_mode': 'fixed',        # 'fixed' = same ₹ per stock (fixed_amount); 'risk' = the old risk_pct sizing
    'fixed_amount': 10_000.0,    # ₹ invested in each new stock, under 'fixed'
    'compounding': False,        # 'fixed' only: scale fixed_amount with the realised (closed-trade) balance
    'max_positions': 10,         # at most this many stocks held AT ONCE, whatever cash/new-stocks-a-month allow; 0 = off
    'risk_pct': 0.5,
    'max_pos_pct': 20.0,
    'cost_pct': 0.15,           # each side: STT 0.1 % + stamp, exchange, GST, DP
    'brokerage': 20.0,          # ₹ per order (each buy, each sell), on top of cost_pct
    'min_turnover_cr': 1.0,     # 0 = off
    'min_price': 0.0,
}
# A day that opens this far from the previous close is a split, bonus or
# demerger the stored history was never adjusted for (or bad data): the
# stock's history is only used from that day on.
BREAK_DOWN = 0.6
BREAK_UP = 1.7


def wilder_rsi(close: np.ndarray, n: int = RSI_LEN) -> np.ndarray:
    """TradingView's ta.rsi: Wilder's RMA seeded with the SMA of the first n changes."""
    close = np.asarray(close, dtype=float)
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return out
    d = np.diff(close)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[:n].mean(), dn[:n].mean()

    def _rsi(u, v):
        if v == 0:
            return 100.0 if u > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + u / v)

    out[n] = _rsi(au, ad)
    for i in range(n, len(d)):
        au = (au * (n - 1) + up[i]) / n
        ad = (ad * (n - 1) + dn[i]) / n
        out[i + 1] = _rsi(au, ad)
    return out


def ema(close: np.ndarray, n: int = EMA_LEN) -> np.ndarray:
    """TradingView's ta.ema (seeded on the first bar); NaN until n bars exist."""
    s = pd.Series(np.asarray(close, dtype=float)).ewm(span=n, adjust=False).mean().to_numpy()
    s[:n - 1] = np.nan
    return s


def clean_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Sorted, de-duplicated, date-normalised daily OHLCV, cut to start after
    the last unadjusted break (see BREAK_DOWN)."""
    df = daily[['open', 'high', 'low', 'close'] + (['volume'] if 'volume' in daily else [])].astype(float)
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index)).normalize()
    df = df[~df.index.duplicated(keep='last')].sort_index()
    df = df[(df['close'] > 0) & (df['open'] > 0)]
    if 'volume' not in df:
        df['volume'] = 0.0
    if len(df) < 2:
        return df
    ratio = (df['open'] / df['close'].shift()).to_numpy()
    brk = np.where((ratio < BREAK_DOWN) | (ratio > BREAK_UP))[0]
    if len(brk):
        df = df.iloc[brk[-1]:]
    return df


TIMEFRAMES = {'day': 'D', 'week': 'W', 'month': 'M'}


def period_label(p: pd.Period, tf: str) -> str:
    """'2026-10' for a month, the Monday for a week, the date for a day."""
    if tf == 'week':
        return p.start_time.date().isoformat()
    return str(p)


def period_bars(daily: pd.DataFrame, tf: str = 'month', ema_len: int = EMA_LEN) -> pd.DataFrame:
    """One row per candle of `tf` (calendar day / Mon-Sun week / calendar month):
    OHLC, the average daily ₹ turnover, RSI, EMA, and the first/last positions
    of its days in `daily`."""
    per = daily.index.to_period(TIMEFRAMES[tf])
    pos = np.arange(len(daily))
    g = pd.DataFrame({'open': daily['open'].to_numpy(), 'high': daily['high'].to_numpy(),
                      'low': daily['low'].to_numpy(), 'close': daily['close'].to_numpy(),
                      'turnover': (daily['close'] * daily['volume']).to_numpy(),
                      'pos': pos}, index=per).groupby(level=0)
    m = pd.DataFrame({
        'open': g['open'].first(), 'high': g['high'].max(), 'low': g['low'].min(),
        'close': g['close'].last(), 'turnover': g['turnover'].mean(),
        'first': g['pos'].min(), 'last': g['pos'].max(),
    })
    m['rsi'] = wilder_rsi(m['close'].to_numpy())
    m['ema'] = ema(m['close'].to_numpy(), ema_len)
    return m


def monthly_bars(daily: pd.DataFrame) -> pd.DataFrame:
    return period_bars(daily, 'month')


def candidates(symbol: str, daily: pd.DataFrame, start: date, end: date,
               rsi_lo: float = DEFAULTS['rsi_lo'], rsi_hi: float = DEFAULTS['rsi_hi'],
               min_turnover_cr: float = DEFAULTS['min_turnover_cr'],
               min_price: float = DEFAULTS['min_price'],
               timeframe: str = 'month', ema_len: int = EMA_LEN, exit_rule: str = 'low_break',
               cleaned: bool = False, keep_raw: bool = False) -> List[dict]:
    """Every trade the rule would take in `symbol` on `timeframe` candles with
    an entry in [start, end], each walked to its own exit — whether the
    portfolio can take it is simulate()'s call. A trade still held at `end` is
    marked at that close.

    exit_rule 'low_break' (the video's): a candle closes under its EMA and a later
    candle trades under that candle's low. 'close': out at the next candle's
    open as soon as one closes under its EMA.

    keep_raw: each candidate also carries its unrounded RSI as `_rsi`, so the
    sweep can cut one wide-band list into narrower bands exactly as a run on
    that band would have."""
    df = daily if cleaned else clean_daily(daily)
    df = df[df.index <= pd.Timestamp(end)]
    if len(df) < 40:
        return []
    m = period_bars(df, timeframe, ema_len)
    n = len(m)
    warm = max(EMA_LEN, ema_len)
    if n < warm + 1:
        return []
    o, h, lo_, c = (df[k].to_numpy() for k in ('open', 'high', 'low', 'close'))
    dates = df.index
    mh, ml, mc = m['high'].to_numpy(), m['low'].to_numpy(), m['close'].to_numpy()
    me, mr, mt = m['ema'].to_numpy(), m['rsi'].to_numpy(), m['turnover'].to_numpy()
    first, last = m['first'].to_numpy(), m['last'].to_numpy()
    periods = m.index
    # Candle j can exit a position: candle j-1 closed below its EMA and j trades under j-1's low.
    armed = mc < me
    brk_bar = np.zeros(n, dtype=bool)
    if exit_rule == 'close':
        brk_bar[1:] = armed[:-1]
    else:
        brk_bar[1:] = armed[:-1] & (ml[1:] < ml[:-1])
    brk_idx = np.flatnonzero(brk_bar)
    min_turn = (min_turnover_cr or 0) * 1e7

    # Signals: the last candle is still forming unless a later one exists, so i < n - 1.
    idx = np.arange(warm - 1, n - 1)
    with np.errstate(invalid='ignore'):
        ok = (mr[idx] >= rsi_lo) & (mr[idx] <= rsi_hi) & ~np.isnan(me[idx]) \
            & (mc[idx] >= min_price) & (mt[idx] >= min_turn)
    if timeframe != 'day':
        # A week / month with no trading at all is not "the next" one.
        ords = periods.asi8
        ok &= (ords[idx + 1] - ords[idx]) == 1
    idx = idx[ok]
    if not len(idx):
        return []
    k = idx + 1
    trigger = mh[idx]
    # Entry: the first day of candle k trading at or above the trigger.
    if timeframe == 'day':
        e = first[k]
        hit = h[e] >= trigger
    else:
        e = np.zeros(len(idx), dtype=int)
        hit = np.zeros(len(idx), dtype=bool)
        for q, kk in enumerate(k):
            a, b = first[kk], last[kk]
            hits = np.flatnonzero(h[a:b + 1] >= trigger[q])
            if len(hits):
                e[q], hit[q] = a + hits[0], True
    hit &= dates[e] >= pd.Timestamp(start)
    idx, k, trigger, e = idx[hit], k[hit], trigger[hit], e[hit]
    entry = np.maximum(o[e], trigger)
    # Exit: the first armed break from the candle after the entry candle on.
    jp = np.searchsorted(brk_idx, k + 1)
    has = jp < len(brk_idx)
    xi = np.full(len(idx), len(df) - 1)
    exit_px = np.full(len(idx), c[-1])
    for q in np.flatnonzero(has):
        j = brk_idx[jp[q]]
        fa, fb = first[j], last[j]
        if exit_rule == 'close':
            xi[q], exit_px[q] = fa, o[fa]
            continue
        arm = ml[j - 1]
        x = fa + np.flatnonzero(lo_[fa:fb + 1] < arm)[0]
        xi[q], exit_px[q] = x, min(o[x], arm)

    entry_dates = dates[e].strftime('%Y-%m-%d')
    exit_dates = dates[xi].strftime('%Y-%m-%d')
    out = [{
        'symbol': symbol,
        'signal_period': period_label(periods[i], timeframe),
        'rsi': round(float(mr[i]), 2),
        'trigger': round(float(trigger[q]), 2),
        'stop': round(float(me[i]), 2),
        'turnover_cr': round(float(mt[i]) / 1e7, 2),
        'entry_date': entry_dates[q],
        'entry_price': round(float(entry[q]), 2),
        'exit_date': exit_dates[q],
        'exit_price': round(float(exit_px[q]), 2),
        'exit_reason': (('Closed below EMA' if exit_rule == 'close' else f'Below {ema_len} EMA, low broken')
                        if has[q] else 'Open'),
    } for q, i in enumerate(idx)]
    if keep_raw:
        for c, i in zip(out, idx):
            c['_rsi'] = float(mr[i])
    return out


def month_closes(daily: pd.DataFrame) -> pd.Series:
    """Month-end closes on a PeriodIndex, for marking holdings."""
    return daily['close'].groupby(daily.index.to_period('M')).last()


def nifty_rsi_by_period(daily: Optional[pd.DataFrame], tf: str = 'month') -> Dict[str, float]:
    """NIFTY's RSI per candle of `tf`, keyed like a candidate's `signal_period`."""
    if daily is None or daily.empty:
        return {}
    m = period_bars(clean_daily(daily), tf)
    return {period_label(p, tf): float(v) for p, v in m['rsi'].items() if not math.isnan(v)}


def nifty_above_ema_by_period(daily: Optional[pd.DataFrame], tf: str = 'month',
                              ema_len: int = EMA_LEN) -> Dict[str, bool]:
    """Whether NIFTY closed at or above its `ema_len` EMA, per candle of `tf`."""
    if daily is None or daily.empty:
        return {}
    m = period_bars(clean_daily(daily), tf, ema_len)
    return {period_label(p, tf): bool(c >= e) for p, c, e in zip(m.index, m['close'], m['ema']) if not math.isnan(e)}


def nifty_rsi_by_month(daily: Optional[pd.DataFrame]) -> Dict[str, float]:
    return nifty_rsi_by_period(daily, 'month')


def simulate(cands: Iterable[dict], closes: Dict[str, pd.Series], start: date, end: date,
             nifty_rsi: Optional[Dict[str, float]] = None, nifty_closes: Optional[pd.Series] = None,
             capital: float = DEFAULTS['capital'], size_mode: str = DEFAULTS['size_mode'],
             fixed_amount: float = DEFAULTS['fixed_amount'], compounding: bool = DEFAULTS['compounding'],
             max_positions: int = DEFAULTS['max_positions'], risk_pct: float = DEFAULTS['risk_pct'],
             max_pos_pct: float = DEFAULTS['max_pos_pct'], max_new: int = DEFAULTS['max_new'],
             nifty_floor: float = DEFAULTS['nifty_floor'], cost_pct: float = DEFAULTS['cost_pct'],
             brokerage: float = DEFAULTS['brokerage'],
             nifty_above_ema: Optional[Dict[str, bool]] = None) -> dict:
    """Run the candidates as one cash portfolio. Same-day entries go most-liquid
    first; exits on a day are paid out before that day's entries. With
    `nifty_above_ema` given, a stock is only bought while NIFTY closed at or
    above its EMA at the scan (on top of the RSI floor).

    `max_positions` (0 = off) is a HARD cap on how many stocks are held at once —
    on top of whatever cash and `max_new` already allow, so it can bind even when
    neither of those would. size_mode 'fixed' (the default): every new stock is
    bought with the same `fixed_amount`, so every position starts the same size
    regardless of its own stop distance — unless `compounding` is on, which scales
    it with the REALISED balance (capital + the net ₹ of trades already closed,
    never an open position's paper gain — the same rule the straddle/option-selling
    backtests use). With BOTH `compounding` and a `max_positions` cap on, the size
    is that balance split evenly across the `max_positions` slots instead of scaled
    off `fixed_amount` — so a stock that exits with a profit directly enlarges the
    NEXT stock bought into its freed slot ("compounding using the other stocks"),
    rather than every slot drifting by the same capital-wide ratio. 'risk': the old
    sizing — qty = equity * risk_pct / (entry - stop), capped at max_pos_pct of
    equity; this one already scales with mark-to-market equity on its own, so
    `compounding` does not apply to it. Either way a buy the cash on hand cannot
    pay for in full is skipped, never bought smaller."""
    nifty_rsi = nifty_rsi or {}
    cost = (cost_pct or 0) / 100.0
    brk = float(brokerage or 0)
    queue = sorted(cands, key=lambda t: (t['entry_date'], -t['turnover_cr'], t['symbol']))
    cash = float(capital)
    min_cash = cash                      # never below 0: every buy is paid from cash in hand
    realized = 0.0                       # net ₹ of trades closed so far — compounding's base, capital + this
    held: Dict[str, dict] = {}
    exits: list = []                     # heap of (exit_date, symbol)
    taken: List[dict] = []
    new_in_month: Dict[str, int] = {}
    skipped = {'held': 0, 'month_cap': 0, 'nifty': 0, 'cash': 0, 'slots': 0}
    nifty_unknown = 0
    curve = []

    marks: Dict[str, tuple] = {}

    def mark(sym, period):
        """The stock's last month-end close at or before `period` (its entry price before any)."""
        m = marks.get(sym)
        if m is None:
            ser = closes.get(sym)
            m = marks[sym] = ((ser.index.asi8, ser.to_numpy(dtype=float)) if ser is not None and len(ser)
                              else (np.array([], dtype=np.int64), np.array([])))
        i = np.searchsorted(m[0], period.ordinal, side='right') - 1
        return float(m[1][i]) if i >= 0 else held[sym]['entry_price']

    def equity_at(period):
        return cash + sum(p['qty'] * mark(s, period) for s, p in held.items())

    def close_out(sym):
        nonlocal cash, realized
        p = held.pop(sym)
        sell = p['qty'] * p['exit_price'] * (1 - cost) - brk
        cash += sell
        realized += sell - p['buy_cost']

    periods = pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq='M')
    qi = 0
    for per in periods:
        month_end = per.end_time.date().isoformat()
        while True:
            next_entry = queue[qi]['entry_date'] if qi < len(queue) else None
            if next_entry is not None and next_entry > month_end:
                next_entry = None
            next_exit = exits[0][0] if exits and exits[0][0] <= month_end else None
            if next_entry is None and next_exit is None:
                break
            if next_exit is not None and (next_entry is None or next_exit <= next_entry):
                d, sym = heapq.heappop(exits)
                if sym in held and held[sym]['exit_reason'] != 'Open':
                    close_out(sym)
                continue
            t = queue[qi]
            qi += 1
            if t['symbol'] in held:
                skipped['held'] += 1
                continue
            nr = nifty_rsi.get(t['signal_period'])
            if nifty_floor and nr is None:
                nifty_unknown += 1
            if nifty_floor and nr is not None and nr < nifty_floor:
                skipped['nifty'] += 1
                continue
            if nifty_above_ema is not None and nifty_above_ema.get(t['signal_period']) is False:
                skipped['nifty'] += 1
                continue
            mkey = t['entry_date'][:7]
            if new_in_month.get(mkey, 0) >= max_new:
                skipped['month_cap'] += 1
                continue
            if max_positions and len(held) >= max_positions:
                skipped['slots'] += 1
                continue
            px = t['entry_price']
            if size_mode == 'fixed':
                if compounding:
                    balance = capital + realized
                    amount = balance / max_positions if max_positions else fixed_amount * balance / capital
                else:
                    amount = fixed_amount
                q_target = amount / px
            else:
                eq = equity_at(per - 1)
                risk = px - t['stop']
                q_risk = eq * risk_pct / 100.0 / risk if risk > 0 else math.inf
                q_cap = eq * max_pos_pct / 100.0 / px if max_pos_pct else math.inf
                q_target = min(q_risk, q_cap)
            q_cash = (cash - brk) / (px * (1 + cost))
            qty = int(math.floor(q_target))
            # Cash short of the full position = skipped, not a token buy of what is left.
            if qty < 1 or qty > q_cash:
                skipped['cash'] += 1
                continue
            new_in_month[mkey] = new_in_month.get(mkey, 0) + 1
            buy_cost = qty * px * (1 + cost) + brk
            cash -= buy_cost
            min_cash = min(min_cash, cash)
            pos = dict(t, qty=qty, nifty_rsi=None if nr is None else round(nr, 2), buy_cost=buy_cost)
            held[t['symbol']] = pos
            taken.append(pos)
            if t['exit_reason'] != 'Open':
                heapq.heappush(exits, (t['exit_date'], t['symbol']))
        eq = equity_at(per)
        curve.append({'month': str(per), 'equity': round(eq, 2), 'cash': round(cash, 2),
                      'holdings': len(held),
                      'nifty': (None if nifty_closes is None or per not in nifty_closes.index
                                else round(float(nifty_closes.loc[per]), 2))})

    for p in taken:
        p['invested'] = round(p['qty'] * p['entry_price'], 2)
        # A trade still held is charged its sell too, so its P&L is what selling now would book.
        buy = p['qty'] * p['entry_price'] * (1 + cost) + brk
        sell = p['qty'] * p['exit_price'] * (1 - cost) - brk
        p['brokerage'] = round(2 * brk, 2)
        p['charges'] = round(buy - sell - p['qty'] * (p['entry_price'] - p['exit_price']), 2)   # % charges + brokerage
        p['pnl_rupees'] = round(sell - buy, 2)
        p['return_pct'] = round((p['exit_price'] / p['entry_price'] - 1) * 100, 2)
        p['months_held'] = _months_between(p['entry_date'], p['exit_date'])
        p['result'] = 'OPEN' if p['exit_reason'] == 'Open' else ('WIN' if p['pnl_rupees'] > 0 else 'LOSS')
    return {'trades': taken, 'curve': curve, 'skipped': skipped, 'nifty_unknown': nifty_unknown,
            'final_cash': round(cash, 2), 'min_cash': round(min_cash, 2), 'capital': capital}


def _months_between(a: str, b: str) -> float:
    da, db = date.fromisoformat(a), date.fromisoformat(b)
    return round((db - da).days / 30.44, 1)


def _max_drawdown(values: List[float]) -> tuple:
    peak, dd, dd_pct = -math.inf, 0.0, 0.0
    for v in values:
        peak = max(peak, v)
        dd = max(dd, peak - v)
        if peak > 0:
            dd_pct = max(dd_pct, (peak - v) / peak * 100)
    return dd, dd_pct


def _cagr(first: float, last: float, years: float) -> Optional[float]:
    if years <= 0 or first <= 0 or last <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 2)


def summarise(result: dict, start: date, end: date, candidates_found: int) -> dict:
    trades, curve, capital = result['trades'], result['curve'], result['capital']
    closed = [t for t in trades if t['result'] != 'OPEN']
    wins = [t for t in closed if t['result'] == 'WIN']
    losses = [t for t in closed if t['result'] == 'LOSS']
    gp = sum(t['pnl_rupees'] for t in wins)
    gl = -sum(t['pnl_rupees'] for t in losses)
    final = curve[-1]['equity'] if curve else capital
    years = max((date.fromisoformat(min(end.isoformat(), date.today().isoformat())) - start).days / 365.25, 0)
    dd, dd_pct = _max_drawdown([capital] + [p['equity'] for p in curve])
    nifty_pts = [p['nifty'] for p in curve if p['nifty']]
    nifty_dd = _max_drawdown(nifty_pts)[1] if nifty_pts else None

    by_year: Dict[int, dict] = {}
    prev_eq, prev_nifty = capital, None
    for p in curve:
        y = int(p['month'][:4])
        row = by_year.setdefault(y, {'year': y, 'start_equity': prev_eq, 'nifty_start': prev_nifty})
        row['end_equity'] = p['equity']
        row['holdings'] = p['holdings']
        if p['nifty']:
            row['nifty_end'] = p['nifty']
            if row['nifty_start'] is None:
                row['nifty_start'] = p['nifty']
        prev_eq = p['equity']
        prev_nifty = p['nifty'] or prev_nifty
    for y, row in by_year.items():
        row['return_pct'] = round((row['end_equity'] / row['start_equity'] - 1) * 100, 2) if row['start_equity'] else None
        ns, ne = row.get('nifty_start'), row.get('nifty_end')
        row['nifty_pct'] = round((ne / ns - 1) * 100, 2) if ns and ne else None
        row['entries'] = sum(1 for t in trades if t['entry_date'][:4] == str(y))
        ex = [t for t in closed if t['exit_date'][:4] == str(y)]
        row['exits'] = len(ex)
        row['wins'] = sum(1 for t in ex if t['result'] == 'WIN')
        row['realised'] = round(sum(t['pnl_rupees'] for t in ex), 2)
    return {
        'total_trades': len(trades), 'closed_trades': len(closed), 'open_trades': len(trades) - len(closed),
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        'profit_factor': round(gp / gl, 2) if gl else (None if not gp else 999.0),
        'avg_win_pct': round(float(np.mean([t['return_pct'] for t in wins])), 2) if wins else 0.0,
        'avg_loss_pct': round(float(np.mean([t['return_pct'] for t in losses])), 2) if losses else 0.0,
        'avg_months_held': round(float(np.mean([t['months_held'] for t in closed])), 1) if closed else 0.0,
        'best_trade_pct': max((t['return_pct'] for t in trades), default=0.0),
        'worst_trade_pct': min((t['return_pct'] for t in trades), default=0.0),
        'multibaggers': sum(1 for t in trades if t['return_pct'] >= 100),
        'capital': capital, 'final_equity': round(final, 2),
        'total_pnl_rupees': round(final - capital, 2),
        'realised_pnl_rupees': round(sum(t['pnl_rupees'] for t in closed), 2),
        'open_pnl_rupees': round(sum(t['pnl_rupees'] for t in trades if t['result'] == 'OPEN'), 2),
        'total_charges': round(sum(t['charges'] for t in trades), 2),
        'total_brokerage': round(sum(t['brokerage'] for t in trades), 2),
        'cagr_pct': _cagr(capital, final, years),
        'nifty_cagr_pct': _cagr(nifty_pts[0], nifty_pts[-1], years) if len(nifty_pts) > 1 else None,
        'max_drawdown_rupees': round(dd, 2), 'max_drawdown_pct': round(dd_pct, 2),
        'nifty_max_drawdown_pct': None if nifty_dd is None else round(nifty_dd, 2),
        'candidates': candidates_found,
        'skipped_held': result['skipped']['held'], 'skipped_month_cap': result['skipped']['month_cap'],
        'skipped_nifty': result['skipped']['nifty'], 'skipped_cash': result['skipped']['cash'],
        'skipped_slots': result['skipped'].get('slots', 0),
        'nifty_unknown': result['nifty_unknown'],
        'final_cash': result['final_cash'], 'min_cash': result['min_cash'],
        # The deepest the cash pot ever dipped, i.e. the most that was ever actually tied up in
        # stock at one time — the real amount this run needed, which can be well under `capital`
        # (every buy is paid from cash in hand and nothing is added, so this can never exceed it).
        'max_invested': round(capital - result['min_cash'], 2),
        'max_holdings': max((p['holdings'] for p in curve), default=0),
        'by_year': [by_year[y] for y in sorted(by_year)],
        'curve': curve,
    }


# ── Find Best Params ─────────────────────────────────────────────────────────
# One candidate list per (EMA length, exit rule), built on the widest RSI band and
# cut into each band by the raw RSI, so a sweep row is exactly the backtest of its
# settings. Every combo is run on SWEEP_PATHS (the capital nudged by ±0.1 %) and
# scored on the median: a portfolio this cash-bound forks on a few rupees (which
# breakout the cash can pay for), so a single run is one draw, not the setting's worth.
SWEEP_GRID = {
    'rsi_band': [(55, 65), (60, 65), (60, 70), (60, 75), (65, 70), (65, 75), (65, 80), (70, 80)],
    'ema_len': [5, 10, 20, 30],
    'exit_rule': ['low_break', 'close'],
    'risk_pct': [0.25, 0.5, 1.0],
    'max_new': [3, 5, 10],
    'nifty_floor': [0.0, 40.0],
}
SWEEP_PATHS = (0.0, 0.001, -0.001)
SWEEP_MIN_TRADES = 10
SWEEP_BOARD = 150


def sweep_combos(grid: Optional[dict] = None) -> List[dict]:
    g = grid or SWEEP_GRID
    keys = list(g)
    out = []
    for vals in itertools.product(*(g[k] for k in keys)):
        c = dict(zip(keys, vals))
        c['rsi_lo'], c['rsi_hi'] = c.pop('rsi_band')
        out.append(c)
    return out


def candidate_sets(grid: Optional[dict] = None) -> List[tuple]:
    """The (ema_len, exit_rule) pairs a sweep needs a candidate list for."""
    g = grid or SWEEP_GRID
    return list(itertools.product(g['ema_len'], g['exit_rule']))


def wide_band(grid: Optional[dict] = None) -> tuple:
    bands = (grid or SWEEP_GRID)['rsi_band']
    return min(b[0] for b in bands), max(b[1] for b in bands)


def in_band(cands: List[dict], lo: float, hi: float) -> List[dict]:
    return [c for c in cands if lo <= c.get('_rsi', c['rsi']) <= hi]


def _half_cagrs(curve: List[dict], capital: float, start: date, end: date) -> tuple:
    """Annualised return of the first and second half of the range (split by months)."""
    if len(curve) < 2:
        return None, None
    mid = len(curve) // 2
    years = len(curve) / 12.0
    mid_eq = curve[mid - 1]['equity']
    return (_cagr(capital, mid_eq, mid / 12.0),
            _cagr(mid_eq, curve[-1]['equity'], years - mid / 12.0))


def score(result: dict, start: date, end: date) -> dict:
    s = summarise(result, start, end, 0)
    years = [y['return_pct'] for y in s['by_year'] if y['return_pct'] is not None]
    h1, h2 = _half_cagrs(result['curve'], result['capital'], start, end)
    return {
        'total_trades': s['total_trades'], 'win_rate': s['win_rate'],
        'profit_factor': s['profit_factor'] or 0.0,
        'cagr_pct': s['cagr_pct'] if s['cagr_pct'] is not None else -100.0,
        'max_drawdown_pct': s['max_drawdown_pct'],
        'net_pnl_rupees': s['total_pnl_rupees'], 'final_equity': s['final_equity'],
        'worst_year_pct': min(years) if years else 0.0,
        'losing_years': sum(1 for y in years if y < 0),
        'first_half_cagr': h1 if h1 is not None else -100.0,
        'second_half_cagr': h2 if h2 is not None else -100.0,
        'max_holdings': s['max_holdings'],
    }


def sweep(cand_sets: Dict[tuple, List[dict]], closes: Dict[str, pd.Series], start: date, end: date,
          nifty_rsi: Optional[Dict[str, float]] = None, nifty_closes: Optional[pd.Series] = None,
          capital: float = DEFAULTS['capital'], size_mode: str = DEFAULTS['size_mode'],
          fixed_amount: float = DEFAULTS['fixed_amount'], compounding: bool = DEFAULTS['compounding'],
          max_pos_pct: float = DEFAULTS['max_pos_pct'],
          cost_pct: float = DEFAULTS['cost_pct'], brokerage: float = DEFAULTS['brokerage'],
          nifty_above_ema: Optional[Dict[str, bool]] = None, grid: Optional[dict] = None,
          paths: tuple = SWEEP_PATHS, progress=None) -> List[dict]:
    """Every combo of `grid`, each the median over `paths` of score(). `cand_sets` maps
    (ema_len, exit_rule) to that pair's candidates on the wide band (keep_raw=True).
    size_mode/fixed_amount/compounding are NOT swept (same for every combo) — under
    'fixed', risk_pct in the grid makes no difference to the result, so callers should
    collapse it to one value before sweeping to avoid paying for combos that can only
    repeat each other."""
    combos = sweep_combos(grid)
    rows = []
    sliced: Dict[tuple, List[dict]] = {}
    for n, c in enumerate(combos):
        key = (c['ema_len'], c['exit_rule'], c['rsi_lo'], c['rsi_hi'])
        if key not in sliced:
            sliced[key] = in_band(cand_sets[(c['ema_len'], c['exit_rule'])], c['rsi_lo'], c['rsi_hi'])
        runs = []
        for nudge in paths:
            res = simulate(sliced[key], closes, start, end, nifty_rsi=nifty_rsi, nifty_closes=nifty_closes,
                           capital=capital * (1 + nudge), size_mode=size_mode, fixed_amount=fixed_amount,
                           compounding=compounding, risk_pct=c['risk_pct'], max_pos_pct=max_pos_pct,
                           max_new=c['max_new'], nifty_floor=c['nifty_floor'], cost_pct=cost_pct,
                           brokerage=brokerage, nifty_above_ema=nifty_above_ema)
            runs.append(score(res, start, end))
        row = dict(c)
        for k in runs[0]:
            row[k] = round(float(np.median([r[k] for r in runs])), 2)
        row['total_trades'] = int(row['total_trades'])
        row['losing_years'] = int(row['losing_years'])
        row['max_holdings'] = int(row['max_holdings'])
        row['paths'] = len(paths)
        rows.append(row)
        if progress:
            progress(n + 1, len(combos))
    return rows


def board(rows: List[dict], min_trades: int = SWEEP_MIN_TRADES, size: int = SWEEP_BOARD) -> List[dict]:
    """The rows worth showing: the top `size` by CAGR, by the weaker half's CAGR (steadiness)
    and by CAGR ÷ max drawdown, among combos with at least `min_trades` trades; best CAGR first."""
    ok = [r for r in rows if r['total_trades'] >= min_trades]
    for r in ok:
        r['cagr_dd'] = round(r['cagr_pct'] / r['max_drawdown_pct'], 2) if r['max_drawdown_pct'] > 0 else 0.0
        r['worse_half_cagr'] = min(r['first_half_cagr'], r['second_half_cagr'])
    keep: Dict[int, dict] = {}
    for key in ('cagr_pct', 'worse_half_cagr', 'cagr_dd'):
        for r in sorted(ok, key=lambda r: r[key], reverse=True)[:size]:
            keep[id(r)] = r
    return sorted(keep.values(), key=lambda r: r['cagr_pct'], reverse=True)
