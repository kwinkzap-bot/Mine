"""Smart Wheel backtest — sell a put on a strong stock; if the stock falls through it,
own the stock (as a future) and sell calls against it. From a video (Kundan
Prajapati on Upsurge, youtube TVTvKDJ44Vs, Hindi; transcript via youtube-transcript-api).

The video's rule, run as one portfolio on monthly stock options:

  * watchlist — NIFTY 50 stocks that have NOT fallen more than 30 % peak-to-trough
                in the last 5 years (`max_dd_pct` over `dd_years`), and are now at
                least 15 % below their all-time high (`min_off_high_pct`). Five at a
                time (`max_positions`), each from a different sector (`one_per_sector`).
  * step 1    — SELL a monthly PUT under support, at least 4-5 % below spot
                (`min_dist_pct`). Only if its premium × qty is at least 1-1.5 % of
                the capital set aside for the stock (`min_prem_pct` of
                `capital_per_stock`, ₹2 L). Expires worthless → sell the next one.
  * step 2    — the stock falls to the put's breakeven (strike − premium): buy the
                put back, BUY the stock future at once and SELL the ATM call
                (next month's when this one is under `min_call_days` sessions away).
  * step 3    — then one of three things:
                  up      the stock is above the call's breakeven (strike +
                          premium) and the call is deep enough in the money to
                          move with the future (≤ `up_tv_pct`, 10 %, of its premium
                          left as time value), or it expires there: close both;
                  sideways the call expires: keep the future, sell the next
                          month's ATM call (each premium lowers the breakeven);
                  down    the stock closes under the future's breakeven (entry −
                          every call premium kept): sell the future, buy the
                          call back — about breakeven, the put's loss is the cost.
                The video's last resort — take delivery of the shares and wait for
                a recovery — is not modelled: a stop is a stop here.

What the video leaves to the eye, made into boxes:

  * "support"  — the lowest close of the last `support_days` sessions (0 = off). The
                 put strike is the highest listed strike at or under BOTH that low
                 and spot × (1 − min_dist).
  * timing     — every decision is on a DAILY CLOSE (an end-of-day trader). The put
                 is sold on the first session after a monthly expiry, on that month's
                 contract; a stop or breakout is acted on at the close that crosses it.
  * quantity   — a lot is `contract_value` ÷ spot shares (₹5 L, the video's L&T: 150 ×
                 ₹3,329), so a 2015 lot and a 2026 lot carry the same money. NSE's
                 real lot sizes changed many times and are not on disk.
  * the future — priced as the stock itself, less `carry_pct` a month (the futures
                 premium paid away on the roll; the video: "₹6-7 a month").
  * premiums   — `prices='breeze'`: the contract's real daily close from ICICI
                 (expired contracts are only there); `prices='model'`: Black-Scholes
                 on the stock's 20-session realised volatility × `iv_mult`. The model
                 is an estimate, the Breeze run is the real test.

Survivorship and hindsight: the universe is TODAY's NIFTY 50, which is the list
of stocks that did well enough to be in it.

Pure: daily DataFrames and a price source in, trades and a daily equity curve out.
Loading files and talking to Breeze lives in the route (app/routes/api.py).
"""
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

# Today's NIFTY 50 with a sector each — the video's universe, and its "five
# different sectors" rule. TMPV is Tata Motors (passenger vehicles) since the
# 2025 demerger; ETERNAL was Zomato. A stock typed into the page that is not here
# is its own sector.
NIFTY50_SECTORS: Dict[str, str] = {
    'ADANIENT': 'Conglomerate', 'ADANIPORTS': 'Infrastructure', 'APOLLOHOSP': 'Healthcare',
    'ASIANPAINT': 'Consumer', 'AXISBANK': 'Banks', 'BAJAJ-AUTO': 'Auto', 'BAJFINANCE': 'Finance',
    'BAJAJFINSV': 'Finance', 'BEL': 'Capital Goods', 'BHARTIARTL': 'Telecom', 'CIPLA': 'Pharma',
    'COALINDIA': 'Mining', 'DRREDDY': 'Pharma', 'EICHERMOT': 'Auto', 'ETERNAL': 'Consumer Services',
    'GRASIM': 'Cement', 'HCLTECH': 'IT', 'HDFCBANK': 'Banks', 'HDFCLIFE': 'Insurance',
    'HINDALCO': 'Metals', 'HINDUNILVR': 'FMCG', 'ICICIBANK': 'Banks', 'INDIGO': 'Aviation',
    'INFY': 'IT', 'ITC': 'FMCG', 'JIOFIN': 'Finance', 'JSWSTEEL': 'Metals', 'KOTAKBANK': 'Banks',
    'LT': 'Construction', 'M&M': 'Auto', 'MARUTI': 'Auto', 'MAXHEALTH': 'Healthcare',
    'NESTLEIND': 'FMCG', 'NTPC': 'Power', 'ONGC': 'Oil & Gas', 'POWERGRID': 'Power',
    'RELIANCE': 'Oil & Gas', 'SBILIFE': 'Insurance', 'SBIN': 'Banks', 'SHRIRAMFIN': 'Finance',
    'SUNPHARMA': 'Pharma', 'TATACONSUM': 'FMCG', 'TMPV': 'Auto', 'TATASTEEL': 'Metals',
    'TCS': 'IT', 'TECHM': 'IT', 'TITAN': 'Consumer', 'TRENT': 'Retail', 'ULTRACEMCO': 'Cement',
    'WIPRO': 'IT',
}

DEFAULTS = {
    'max_dd_pct': 30.0,          # the 5-year fall a stock must NOT have had
    'dd_years': 5.0,
    'min_off_high_pct': 15.0,    # now at least this far under its all-time high
    'min_dist_pct': 5.0,         # put strike at least this far under spot
    'support_days': 60,          # the put also sits under the lowest close of this many sessions; 0 = off
    'min_prem_pct': 1.0,         # put premium × qty ≥ this % of capital_per_stock
    'capital_per_stock': 200_000.0,
    'contract_value': 500_000.0,  # ₹ of stock in one lot
    'max_positions': 5,
    'one_per_sector': True,
    'min_call_days': 10,         # fewer sessions than this to expiry → sell next month's call
    'carry_pct': 0.5,            # futures cost a month, % of the future's value
    'brokerage': 20.0,           # ₹ per order
    'iv_mult': 1.2,              # model only: IV = 20-session realised vol × this
    'up_tv_pct': 10.0,           # up-exit closes both once the call keeps ≤ this % of its premium as time value
    'prices': 'breeze',
}

RV_DAYS = 20
RATE = 0.065
MIN_SIGMA = 0.12
QUOTE_STALE_DAYS = 5             # a Breeze close older than this is no quote


# ── calendar ─────────────────────────────────────────────────────────────────

def monthly_expiries(sessions: Sequence[date], extra_months: int = 2) -> List[date]:
    """Every monthly stock-option expiry over the sessions, holiday-adjusted (the
    session on or before the nominal last Thursday — Tuesday from Sep 2025), and
    `extra_months` nominal ones past the last session for positions still open."""
    from trading_app.service.expiry_calendar import _month_expiry, expiry_weekday_for
    if not sessions:
        return []
    ss = sorted(set(sessions))
    out: List[date] = []
    month = ss[0].replace(day=1)
    last = ss[-1]
    while True:
        nominal = _month_expiry(month, expiry_weekday_for)
        if nominal > last:
            out.append(nominal)
            extra_months -= 1
            if extra_months < 0:
                break
        else:
            i = bisect.bisect_right(ss, nominal) - 1
            if i >= 0 and (nominal - ss[i]).days <= 4:
                out.append(ss[i])
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def expiry_for(day: date, expiries: Sequence[date]) -> Optional[date]:
    for e in expiries:
        if e >= day:
            return e
    return None


# ── strikes and prices ──────────────────────────────────────────────────────

# Whole-rupee steps only: Breeze takes a strike as an integer, so a 2.5-step
# strike (97.5) cannot be asked for; the ladder's coarser steps cover those stocks.
_NICE = (1.0, 2.0, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0, 250.0, 500.0)


def strike_step(price: float) -> float:
    """A strike interval like NSE's for a stock at `price` (L&T at ₹3,329 → 20,
    Reliance at ₹1,400 → 10): the largest 'nice' step under 0.6 % of the price."""
    target = price * 0.006
    step = _NICE[0]
    for s in _NICE:
        if s <= target:
            step = s
    return step


def strike_ladder(level: float, price: float, mode: str = 'below', n: int = 4) -> List[float]:
    """Candidate strikes for `level`, finest step first, then coarser ones — a
    coarser multiple is listed whenever a finer interval is, so a probe that finds
    no contract at the fine strike falls back to one that exists."""
    i = _NICE.index(strike_step(price))
    out: List[float] = []
    for s in _NICE[i:i + n]:
        k = math.floor(level / s) * s if mode == 'below' else round(level / s) * s
        if k > 0 and k not in out:
            out.append(float(k))
    return out


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, years: float, sigma: float, right: str,
             rate: float = RATE) -> float:
    intrinsic = max(spot - strike, 0.0) if right == 'CE' else max(strike - spot, 0.0)
    if years <= 0 or sigma <= 0:
        return intrinsic
    sq = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / sq
    d2 = d1 - sq
    disc = math.exp(-rate * years)
    if right == 'CE':
        v = spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    else:
        v = strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return max(v, intrinsic)


class Stock:
    """One stock's daily closes, indexed by date, with the reads the rule needs."""

    def __init__(self, symbol: str, daily: pd.DataFrame, sector: Optional[str] = None):
        self.symbol = symbol
        self.sector = sector or NIFTY50_SECTORS.get(symbol, symbol)
        d = daily.copy()
        d.index = pd.to_datetime(d.index).normalize()
        d = d[~d.index.duplicated(keep='last')].sort_index()
        self.dates = np.array(d.index.date)
        self.close = d['close'].astype(float).to_numpy()
        self.ath = np.maximum.accumulate(self.close)
        rets = np.diff(np.log(self.close), prepend=np.nan)
        rv = pd.Series(rets).rolling(RV_DAYS).std().to_numpy() * math.sqrt(252)
        self.rv = rv
        self._pos = {dd: i for i, dd in enumerate(self.dates)}
        self._dd: Dict[tuple, Optional[float]] = {}     # Find Best Params asks the same reads per combo

    def idx(self, day: date) -> Optional[int]:
        return self._pos.get(day)

    def close_on(self, day: date) -> Optional[float]:
        i = self._pos.get(day)
        return None if i is None else float(self.close[i])

    def last_close(self, day: date) -> Optional[float]:
        i = int(np.searchsorted(self.dates, day, side='right')) - 1
        return None if i < 0 else float(self.close[i])

    def max_drawdown(self, i: int, years: float) -> Optional[float]:
        """The deepest peak-to-trough fall of the closes over the `years` up to i, %.
        None when the stock has less history than that."""
        key = (i, years)
        if key not in self._dd:
            self._dd[key] = self._max_drawdown(i, years)
        return self._dd[key]

    def _max_drawdown(self, i: int, years: float) -> Optional[float]:
        start_day = self.dates[i] - timedelta(days=int(round(years * 365.25)))
        a = int(np.searchsorted(self.dates, start_day))
        if a == 0 and self.dates[0] > start_day + timedelta(days=10):
            return None
        seg = self.close[a:i + 1]
        peak = np.maximum.accumulate(seg)
        return float(np.max(1.0 - seg / peak) * 100.0)

    def support(self, i: int, days: int) -> Optional[float]:
        if days <= 0:
            return None
        a = max(0, i - days + 1)
        return float(np.min(self.close[a:i + 1]))


class ModelPrices:
    """Black-Scholes on realised volatility — an estimate, for a run without Breeze."""

    name = 'model'

    def __init__(self, stocks: Dict[str, Stock], iv_mult: float = 1.2):
        self.stocks = stocks
        self.iv_mult = iv_mult
        self._memo: Dict[tuple, Optional[float]] = {}

    def strike(self, sym: str, expiry: date, right: str, level: float, mode: str,
               day: date, spot: float) -> Optional[float]:
        return strike_ladder(level, spot, mode, n=1)[0]

    def close(self, sym: str, expiry: date, strike: float, right: str, day: date) -> Optional[float]:
        key = (sym, expiry, strike, right, day)
        if key not in self._memo:
            self._memo[key] = self._close(sym, expiry, strike, right, day)
        return self._memo[key]

    def _close(self, sym: str, expiry: date, strike: float, right: str, day: date) -> Optional[float]:
        st = self.stocks[sym]
        i = st.idx(day)
        if i is None:
            return None
        spot = float(st.close[i])
        sigma = st.rv[i]
        sigma = MIN_SIGMA if not np.isfinite(sigma) else max(MIN_SIGMA, float(sigma) * self.iv_mult)
        years = max((expiry - day).days, 0) / 365.0
        return round(bs_price(spot, strike, years, sigma, right), 2)


def _bar_day(row) -> Optional[date]:
    d = row.get('date')
    if d is None:
        return None
    try:
        return pd.Timestamp(d).date()
    except (TypeError, ValueError):
        return None


class BreezePrices:
    """Real daily closes of the expired contracts, from ICICI Breeze.

    The stock files on disk are split/bonus ADJUSTED (Reliance reads ₹655 in Feb
    2020; it traded near ₹1,300 and its strikes were there), while a contract's
    strike is the price of its day. So the stock's own unadjusted closes are read
    too (`fetch_cash`, one Breeze request a calendar year), and every level is
    moved onto the traded scale before a strike is asked for, and every premium
    back onto the file's scale after. The engine only ever sees adjusted numbers.

    `fetch_option(sym, expiry, strike, right, from_day, to_day)` and
    `fetch_cash(sym, from_day, to_day)` return Breeze bar dicts; both are the
    route's, so this class stays free of the network. `budget` caps the real
    requests a run may make (`spent()` reads them); past it, a contract not
    already cached is 'no quote', and a re-run carries on from the cache."""

    name = 'breeze'

    def __init__(self, stocks: Dict[str, 'Stock'], fetch_option, fetch_cash,
                 expiries: Sequence[date] = ()):
        self.stocks = stocks
        self._fetch_option = fetch_option
        self._fetch_cash = fetch_cash
        self._expiries = sorted(expiries)
        self._series: Dict[tuple, Dict[date, float]] = {}
        self._cash: Dict[tuple, Dict[date, float]] = {}
        self._raw_strike: Dict[tuple, float] = {}
        self.contracts = 0
        self.empty = 0

    def set_expiries(self, expiries: Sequence[date]) -> None:
        self._expiries = sorted(expiries)

    # The unadjusted ÷ adjusted ratio on a day (1.0 when Breeze has no close).
    def factor(self, sym: str, day: date) -> float:
        key = (sym, day.year)
        if key not in self._cash:
            bars = self._fetch_cash(sym, date(day.year, 1, 1), date(day.year, 12, 31)) or []
            self._cash[key] = {d: float(b['close']) for b in bars
                               if (d := _bar_day(b)) is not None and b.get('close')}
        raw = self._cash[key]
        adj = self.stocks[sym].close_on(day)
        if not raw or adj is None or adj <= 0:
            return 1.0
        r = raw.get(day)
        if r is None:
            prior = [d for d in raw if d <= day and (day - d).days <= QUOTE_STALE_DAYS]
            if not prior:
                return 1.0
            r = raw[max(prior)]
        f = r / adj
        return 1.0 if abs(f - 1.0) < 0.03 else f

    def _window(self, expiry: date) -> tuple:
        prev = [e for e in self._expiries if e < expiry]
        start = (prev[-1] if prev else expiry - timedelta(days=35)) - timedelta(days=25)
        return start, expiry

    def _contract(self, sym: str, expiry: date, strike_raw: float, right: str) -> Dict[date, float]:
        key = (sym, expiry, strike_raw, right)
        if key not in self._series:
            a, b = self._window(expiry)
            bars = self._fetch_option(sym, expiry, strike_raw, right, a, b) or []
            self.contracts += 1
            series = {d: float(x['close']) for x in bars
                      if (d := _bar_day(x)) is not None and x.get('close')}
            self.empty += not series
            self._series[key] = series
        return self._series[key]

    @staticmethod
    def _close_on(series: Dict[date, float], day: date) -> Optional[float]:
        if day in series:
            return series[day]
        prior = [d for d in series if d <= day and (day - d).days <= QUOTE_STALE_DAYS]
        return series[max(prior)] if prior else None

    def strike(self, sym: str, expiry: date, right: str, level: float, mode: str,
               day: date, spot: float) -> Optional[float]:
        f = self.factor(sym, day)
        for k_raw in strike_ladder(level * f, spot * f, mode):
            series = self._contract(sym, expiry, k_raw, right)
            if self._close_on(series, day) is not None:
                k = k_raw / f
                self._raw_strike[(sym, expiry, round(k, 6), right)] = k_raw
                return k
        return None

    def close(self, sym: str, expiry: date, strike: float, right: str, day: date) -> Optional[float]:
        f = self.factor(sym, day)
        k_raw = self._raw_strike.get((sym, expiry, round(strike, 6), right))
        if k_raw is None:
            return None
        q = self._close_on(self._contract(sym, expiry, k_raw, right), day)
        return None if q is None else round(q / f, 4)


# ── the walk ────────────────────────────────────────────────────────────────

def _intrinsic(spot: float, strike: float, right: str) -> float:
    return max(spot - strike, 0.0) if right == 'CE' else max(strike - spot, 0.0)


def simulate(stocks: Dict[str, Stock], prices, start: date, end: date,
             progress: Optional[Callable[[int, int], None]] = None, **params) -> dict:
    """Walk every session in [start, end]; returns {'trades', 'curve', 'skipped'}.

    `prices` answers strike(sym, expiry, right, level, mode, day, spot) — the
    listed strike for a level ('below': highest at or under it; 'nearest': ATM) —
    and close(sym, expiry, strike, right, day) — that contract's close, or None."""
    p = {**DEFAULTS, **params}
    sessions = sorted({d for st in stocks.values() for d in st.dates if start <= d <= end})
    all_sessions = sorted({d for st in stocks.values() for d in st.dates})
    expiries = monthly_expiries(all_sessions)
    if hasattr(prices, 'set_expiries'):
        prices.set_expiries(expiries)
    entry_days = set()
    for e in expiries:
        j = bisect.bisect_right(all_sessions, e)
        if j < len(all_sessions):
            entry_days.add(all_sessions[j])
    if sessions and sessions[0] not in entry_days:
        entry_days.add(sessions[0])          # a run starting mid-month still trades that month

    cap = float(p['capital_per_stock'])
    slots = int(p['max_positions'])
    total_capital = cap * slots
    brokerage = float(p['brokerage'])
    carry = float(p['carry_pct']) / 100.0
    session_idx = {d: i for i, d in enumerate(all_sessions)}

    open_pos: Dict[str, dict] = {}
    trades: List[dict] = []
    curve: List[dict] = []
    realised = 0.0
    skipped = {'premium': 0, 'no_quote': 0, 'slots': 0, 'sector': 0}
    filtered = {'drawdown': 0, 'near_high': 0, 'history': 0}

    def quote(pos_or_sym, expiry, strike, right, day, spot):
        q = prices.close(pos_or_sym, expiry, strike, right, day)
        return q

    def sessions_left(day: date, expiry: date) -> int:
        a, b = session_idx.get(day), session_idx.get(expiry)
        if a is None:
            return 0
        if b is None:   # expiry past the data: count weekdays
            n, d = 0, day
            while d < expiry:
                d += timedelta(days=1)
                n += d.weekday() < 5
            return n
        return b - a

    def sell_call(pos: dict, day: date, spot: float) -> bool:
        exp = expiry_for(day + timedelta(days=1), expiries)
        if exp is not None and sessions_left(day, exp) < int(p['min_call_days']):
            exp = expiry_for(exp + timedelta(days=1), expiries)
        if exp is None:
            return False
        k = prices.strike(pos['symbol'], exp, 'CE', spot, 'nearest', day, spot)
        prem = None if k is None else quote(pos['symbol'], exp, k, 'CE', day, spot)
        if prem is None or prem <= 0:
            return False
        pos['call'] = {'strike': k, 'expiry': exp, 'premium': prem, 'sold': day.isoformat()}
        pos['calls'].append({'strike': k, 'expiry': exp.isoformat(), 'premium': prem,
                             'sold': day.isoformat()})
        pos['orders'] += 1
        return True

    def close_call(pos: dict, day: date, spot: float, settle: bool) -> None:
        c = pos['call']
        if settle:
            px = _intrinsic(spot, c['strike'], 'CE')
        else:
            q = quote(pos['symbol'], c['expiry'], c['strike'], 'CE', day, spot)
            px = q if q is not None else _intrinsic(spot, c['strike'], 'CE')
            pos['orders'] += 1
        pts = c['premium'] - px
        pos['call_pnl_pts'] += pts
        pos['calls'][-1].update({'closed': day.isoformat(), 'exit': round(px, 2), 'pts': round(pts, 2)})
        pos['call'] = None

    def finish(pos: dict, day: date, reason: str, spot: float) -> None:
        nonlocal realised
        qty = pos['qty']
        fut_pts = 0.0
        carry_rs = 0.0
        if pos.get('fut_entry') is not None:
            fut_pts = spot - pos['fut_entry']
            held_days = (day - date.fromisoformat(pos['fut_date'])).days
            carry_rs = pos['fut_entry'] * qty * carry * held_days / 30.0
        gross = (pos['put_pnl_pts'] + pos['call_pnl_pts'] + fut_pts) * qty
        costs = pos['orders'] * brokerage + carry_rs
        net = gross - costs
        realised += net
        trades.append({
            'symbol': pos['symbol'], 'sector': pos['sector'],
            'entry_date': pos['entry_date'], 'spot': round(pos['spot'], 2),
            'off_high_pct': pos['off_high_pct'], 'dd5y_pct': pos['dd5y_pct'],
            'put_expiry': pos['put_expiry'], 'put_strike': pos['put_strike'],
            'put_premium': pos['put_premium'], 'put_dist_pct': pos['put_dist_pct'],
            'put_exit': pos['put_exit'], 'qty': qty,
            'assigned': pos.get('fut_entry') is not None,
            'fut_date': pos.get('fut_date'), 'fut_entry': None if pos.get('fut_entry') is None else round(pos['fut_entry'], 2),
            'fut_exit': round(spot, 2) if pos.get('fut_entry') is not None else None,
            'calls': pos['calls'], 'n_calls': len(pos['calls']),
            'put_pnl': round(pos['put_pnl_pts'] * qty, 2),
            'call_pnl': round(pos['call_pnl_pts'] * qty, 2),
            'fut_pnl': round(fut_pts * qty, 2),
            'charges': round(costs, 2), 'carry': round(carry_rs, 2),
            'pnl_rupees': round(net, 2),
            'return_pct': round(net / cap * 100.0, 2),
            'exit_date': day.isoformat(), 'reason': reason,
            'result': 'OPEN' if reason == 'Open at end' else ('WIN' if net > 0 else 'LOSS'),
            'days': (day - date.fromisoformat(pos['entry_date'])).days,
        })

    def mtm(pos: dict, day: date) -> float:
        st = stocks[pos['symbol']]
        spot = st.last_close(day)
        if spot is None:
            return 0.0
        pts = pos['put_pnl_pts'] + pos['call_pnl_pts']
        if pos['stage'] == 'put':
            q = quote(pos['symbol'], date.fromisoformat(pos['put_expiry']), pos['put_strike'], 'PE', day, spot)
            q = q if q is not None else pos.get('put_last', _intrinsic(spot, pos['put_strike'], 'PE'))
            pos['put_last'] = q
            pts += pos['put_premium'] - q
        else:
            pts += spot - pos['fut_entry']
            c = pos.get('call')
            if c:
                q = quote(pos['symbol'], c['expiry'], c['strike'], 'CE', day, spot)
                q = q if q is not None else c.get('last', _intrinsic(spot, c['strike'], 'CE'))
                c['last'] = q
                pts += c['premium'] - q
        return pts * pos['qty']

    for n, day in enumerate(sessions):
        # ── manage what is open ─────────────────────────────────────────────
        for sym in list(open_pos):
            pos = open_pos[sym]
            st = stocks[sym]
            spot = st.close_on(day)
            if spot is None:
                continue
            if pos['stage'] == 'put':
                pexp = date.fromisoformat(pos['put_expiry'])
                k, prem = pos['put_strike'], pos['put_premium']
                if day >= pexp:
                    px = _intrinsic(spot, k, 'PE')
                    pos['put_pnl_pts'] = prem - px
                    pos['put_exit'] = round(px, 2)
                    finish(pos, day, 'Put expired worthless' if px == 0 else 'Put settled in the money', spot)
                    del open_pos[sym]
                elif spot <= k - prem:
                    q = quote(sym, pexp, k, 'PE', day, spot)
                    px = q if q is not None else _intrinsic(spot, k, 'PE')
                    pos['put_pnl_pts'] = prem - px
                    pos['put_exit'] = round(px, 2)
                    pos['orders'] += 2                         # buy the put back, buy the future
                    pos['stage'] = 'covered'
                    pos['fut_entry'] = spot
                    pos['fut_date'] = day.isoformat()
                    pos['call'] = None
                    sell_call(pos, day, spot)
                continue
            # covered: future long + short call
            c = pos.get('call')
            stop = pos['fut_entry'] - pos['call_pnl_pts'] - (c['premium'] if c else 0.0)
            above = c is not None and spot >= c['strike'] + c['premium']
            if above and day < c['expiry']:
                # "Deep in the money, the call's delta goes to 1 like the future's —
                # cut both": only once little time value is left to give back.
                q = quote(sym, c['expiry'], c['strike'], 'CE', day, spot)
                above = q is not None and q - _intrinsic(spot, c['strike'], 'CE') <= p['up_tv_pct'] / 100.0 * c['premium']
            if above:
                close_call(pos, day, spot, settle=day >= c['expiry'])
                pos['orders'] += 1
                finish(pos, day, 'Breakout above the call — both closed', spot)
                del open_pos[sym]
            elif spot <= stop:
                if c:
                    close_call(pos, day, spot, settle=day >= c['expiry'])
                pos['orders'] += 1
                finish(pos, day, 'Stop — future under its breakeven', spot)
                del open_pos[sym]
            else:
                if c and day >= c['expiry']:
                    close_call(pos, day, spot, settle=True)
                if pos.get('call') is None:
                    sell_call(pos, day, spot)

        # ── new puts on the first session after a monthly expiry ────────────
        if day in entry_days and len(open_pos) < slots:
            pexp = expiry_for(day + timedelta(days=1), expiries)
            cands = []
            if pexp is not None and sessions_left(day, pexp) >= 5:
                for sym, st in stocks.items():
                    if sym in open_pos:
                        continue
                    i = st.idx(day)
                    if i is None:
                        continue
                    spot = float(st.close[i])
                    off = (1.0 - spot / st.ath[i]) * 100.0
                    if off < p['min_off_high_pct']:
                        filtered['near_high'] += 1
                        continue
                    dd = st.max_drawdown(i, p['dd_years'])
                    if dd is None:
                        filtered['history'] += 1
                        continue
                    if dd > p['max_dd_pct']:
                        filtered['drawdown'] += 1
                        continue
                    cands.append((sym, st, i, spot, off, dd))
            picks = []
            for sym, st, i, spot, off, dd in cands:
                level = spot * (1.0 - p['min_dist_pct'] / 100.0)
                sup = st.support(i, int(p['support_days']))
                if sup is not None:
                    level = min(level, sup)
                k = prices.strike(sym, pexp, 'PE', level, 'below', day, spot)
                prem = None if k is None else quote(sym, pexp, k, 'PE', day, spot)
                if prem is None:
                    skipped['no_quote'] += 1
                    continue
                qty = max(1, int(round(p['contract_value'] / spot)))
                if prem * qty < p['min_prem_pct'] / 100.0 * cap:
                    skipped['premium'] += 1
                    continue
                picks.append((prem * qty / cap, sym, st, spot, off, dd, k, prem, qty))
            picks.sort(key=lambda r: -r[0])
            sectors = {pos['sector'] for pos in open_pos.values()}
            for _, sym, st, spot, off, dd, k, prem, qty in picks:
                if len(open_pos) >= slots:
                    skipped['slots'] += 1
                    continue
                if p['one_per_sector'] and st.sector in sectors:
                    skipped['sector'] += 1
                    continue
                sectors.add(st.sector)
                open_pos[sym] = {
                    'symbol': sym, 'sector': st.sector, 'stage': 'put',
                    'entry_date': day.isoformat(), 'spot': spot,
                    'off_high_pct': round(off, 1), 'dd5y_pct': round(dd, 1),
                    'put_expiry': pexp.isoformat(), 'put_strike': k, 'put_premium': prem,
                    'put_dist_pct': round((1.0 - k / spot) * 100.0, 2), 'put_exit': None,
                    'qty': qty, 'put_pnl_pts': 0.0, 'call_pnl_pts': 0.0, 'calls': [],
                    'orders': 1, 'fut_entry': None,
                }

        open_val = sum(mtm(pos, day) for pos in open_pos.values())
        curve.append({'date': day.isoformat(), 'equity': round(total_capital + realised + open_val, 2),
                      'realised': round(realised, 2), 'open': len(open_pos),
                      'covered': sum(1 for x in open_pos.values() if x['stage'] == 'covered')})
        if progress and n % 20 == 0:
            progress(n + 1, len(sessions))

    # Whatever is still open is marked at the last close, booked as 'Open at end'.
    last = sessions[-1] if sessions else end
    for sym, pos in list(open_pos.items()):
        spot = stocks[sym].last_close(last)
        if spot is None:
            continue
        mtm(pos, last)                       # leaves each leg's last price on the position
        if pos['stage'] == 'put':
            pos['put_pnl_pts'] = pos['put_premium'] - pos.get('put_last', 0.0)
            pos['put_exit'] = round(pos.get('put_last', 0.0), 2)
        else:
            c = pos.get('call')
            if c:
                pos['call_pnl_pts'] += c['premium'] - c.get('last', 0.0)
                pos['calls'][-1].update({'closed': None, 'exit': round(c.get('last', 0.0), 2)})
        finish(pos, last, 'Open at end', spot)
    return {'trades': trades, 'curve': curve, 'skipped': skipped, 'filtered': filtered,
            'capital': total_capital}


# ── summary ─────────────────────────────────────────────────────────────────

def summarise(result: dict, start: date, end: date) -> dict:
    trades = result['trades']
    curve = result['curve']
    capital = result['capital']
    closed = [t for t in trades if t['result'] != 'OPEN']
    wins = [t for t in closed if t['pnl_rupees'] > 0]
    losses = [t for t in closed if t['pnl_rupees'] <= 0]
    net = sum(t['pnl_rupees'] for t in trades)
    gw = sum(t['pnl_rupees'] for t in wins)
    gl = -sum(t['pnl_rupees'] for t in losses)
    eq = np.array([c['equity'] for c in curve]) if curve else np.array([capital])
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    di = int(np.argmax(dd)) if len(dd) else 0
    max_dd = float(dd[di]) if len(dd) else 0.0
    max_dd_pct = float(dd[di] / peak[di] * 100.0) if len(dd) and peak[di] else 0.0
    years = max((end - start).days / 365.25, 1e-9)
    final = float(eq[-1]) if len(eq) else capital
    cagr = ((final / capital) ** (1.0 / years) - 1.0) * 100.0 if final > 0 and years >= 0.5 else None

    by_reason: Dict[str, int] = {}
    for t in trades:
        by_reason[t['reason']] = by_reason.get(t['reason'], 0) + 1

    # Per year, on the daily equity: return on the year-start equity.
    by_year = []
    if curve:
        df = pd.DataFrame(curve)
        df['year'] = df['date'].str[:4]
        prev_end = capital
        for y, g in df.groupby('year'):
            ye = float(g['equity'].iloc[-1])
            ty = [t for t in trades if t['entry_date'][:4] == y]
            ex = [t for t in trades if t['exit_date'][:4] == y and t['result'] != 'OPEN']
            by_year.append({
                'year': y, 'puts': len(ty), 'assigned': sum(1 for t in ty if t['assigned']),
                'closed': len(ex), 'wins': sum(1 for t in ex if t['pnl_rupees'] > 0),
                'booked': round(sum(t['pnl_rupees'] for t in ex), 2),
                'end_equity': round(ye, 2),
                'return_pct': round((ye / prev_end - 1.0) * 100.0, 2) if prev_end else None,
                'max_dd_pct': round(float(((np.maximum.accumulate(g['equity']) - g['equity'])
                                           / np.maximum.accumulate(g['equity'])).max() * 100.0), 2),
            })
            prev_end = ye
    # Months: booked P&L by the month a trade closed.
    months: Dict[str, float] = {}
    for t in closed:
        months[t['exit_date'][:7]] = months.get(t['exit_date'][:7], 0.0) + t['pnl_rupees']
    n_months = max(1, int(round(years * 12)))
    return {
        'total_trades': len(trades), 'closed_trades': len(closed), 'open_trades': len(trades) - len(closed),
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0,
        'total_pnl_rupees': round(net, 2), 'capital': capital, 'final_equity': round(final, 2),
        'return_pct': round(net / capital * 100.0, 2) if capital else 0.0,
        'cagr_pct': None if cagr is None else round(cagr, 2),
        'avg_month_pct': round(net / capital * 100.0 / n_months, 2) if capital else 0.0,
        'losing_months': sum(1 for v in months.values() if v < 0),
        'max_drawdown_rupees': round(max_dd, 2), 'max_drawdown_pct': round(max_dd_pct, 2),
        'max_drawdown_date': curve[di]['date'] if curve else None,
        'profit_factor': round(gw / gl, 2) if gl > 0 else None,
        'avg_win': round(gw / len(wins), 2) if wins else 0.0,
        'avg_loss': round(-gl / len(losses), 2) if losses else 0.0,
        'best_trade': max((t['pnl_rupees'] for t in trades), default=0.0),
        'worst_trade': min((t['pnl_rupees'] for t in trades), default=0.0),
        'assigned': sum(1 for t in trades if t['assigned']),
        'calls_sold': sum(t['n_calls'] for t in trades),
        'by_reason': by_reason,
        'total_charges': round(sum(t['charges'] for t in trades), 2),
        'total_carry': round(sum(t['carry'] for t in trades), 2),
        'max_open': max((c['open'] for c in curve), default=0),
        'max_covered': max((c['covered'] for c in curve), default=0),
        'skipped': result['skipped'], 'filtered': result['filtered'],
        'by_year': by_year,
        'curve': [{'date': c['date'], 'equity': c['equity']} for c in curve[::5]] + ([{'date': curve[-1]['date'], 'equity': curve[-1]['equity']}] if curve else []),
    }


# ── Find Best Params ────────────────────────────────────────────────────────
# The boxes the video gives only roughly ("15 % off the high, 18-19 will do",
# "4-5 % away at least", "1-1.5 % premium", support by eye) and the one this
# engine adds (how deep in the money before an up-move closes both legs). Money
# (capital, lot value, slots, carry, brokerage) and the premium source come from
# the page and are not swept. 4 × 4 × 4 × 4 × 3 × 3 = 2,304 combos; ~0.05 s each on
# model premiums, so a few minutes for 2015-26.
SWEEP_GRID = {
    'max_dd_pct': [25.0, 30.0, 35.0, 40.0],
    'min_off_high_pct': [10.0, 15.0, 20.0, 25.0],
    'min_dist_pct': [3.0, 5.0, 7.0, 10.0],
    'support_days': [0, 20, 60, 120],
    'min_prem_pct': [0.5, 1.0, 1.5],
    'up_tv_pct': [10.0, 25.0, 50.0],
}
SWEEP_KEYS = tuple(SWEEP_GRID)
SWEEP_MIN_TRADES = 10
SWEEP_BOARD = 60          # rows kept from each ranking; the board is their union


def sweep_combos(grid: Optional[dict] = None) -> List[dict]:
    import itertools
    g = grid or SWEEP_GRID
    keys = list(g)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(g[k] for k in keys))]


def _half_cagr(curve: List[dict], a: int, b: int) -> Optional[float]:
    if b - a < 60:
        return None
    e0, e1 = curve[a]['equity'], curve[b - 1]['equity']
    yrs = (date.fromisoformat(curve[b - 1]['date']) - date.fromisoformat(curve[a]['date'])).days / 365.25
    if e0 <= 0 or e1 <= 0 or yrs <= 0:
        return None
    return round(((e1 / e0) ** (1.0 / yrs) - 1.0) * 100.0, 2)


def score(result: dict, start: date, end: date) -> dict:
    """One combo's board row: the summary's headline numbers plus how it did in each
    half of the range and its worst calendar year, so a row that only wins because of
    one lucky stretch can be told from a steady one."""
    s = summarise(result, start, end)
    curve = result['curve']
    mid = len(curve) // 2
    first, second = _half_cagr(curve, 0, mid), _half_cagr(curve, mid, len(curve))
    years = [y['return_pct'] for y in s['by_year'] if y['return_pct'] is not None]
    cagr = s['cagr_pct'] or 0.0
    dd = s['max_drawdown_pct'] or 0.0
    return {
        'total_trades': s['total_trades'], 'win_rate': s['win_rate'],
        'net_pnl_rupees': s['total_pnl_rupees'], 'cagr_pct': s['cagr_pct'],
        'max_drawdown_pct': s['max_drawdown_pct'], 'max_drawdown_rupees': s['max_drawdown_rupees'],
        'profit_factor': s['profit_factor'], 'assigned': s['assigned'],
        'stops': s['by_reason'].get('Stop — future under its breakeven', 0),
        'worst_trade': s['worst_trade'],
        'worst_year_pct': min(years) if years else None,
        'losing_years': sum(1 for y in years if y < 0),
        'first_half_cagr': first, 'second_half_cagr': second,
        'weaker_half_cagr': None if first is None or second is None else min(first, second),
        'cagr_dd': round(cagr / dd, 2) if dd > 0 else (round(cagr, 2) if cagr > 0 else 0.0),
    }


def sweep(stocks: Dict[str, 'Stock'], prices, start: date, end: date, base: Optional[dict] = None,
          grid: Optional[dict] = None, progress: Optional[Callable[[int, int], None]] = None) -> List[dict]:
    """Every combo of `grid` over `base` (the page's other boxes); one row each."""
    combos = sweep_combos(grid)
    rows = []
    for n, combo in enumerate(combos):
        res = simulate(stocks, prices, start, end, **{**(base or {}), **combo})
        rows.append({**combo, **score(res, start, end)})
        if progress and (n % 25 == 0 or n == len(combos) - 1):
            progress(n + 1, len(combos))
    return rows


def board(rows: List[dict], min_trades: int = SWEEP_MIN_TRADES, size: int = SWEEP_BOARD) -> List[dict]:
    """The union of the top `size` by CAGR ÷ max drawdown, by CAGR, and by the weaker
    half's CAGR (steadiness), among combos with at least `min_trades` puts; sorted
    by CAGR ÷ DD."""
    ok = [r for r in rows if r['total_trades'] >= min_trades]
    keep: Dict[tuple, dict] = {}
    for key in ('cagr_dd', 'cagr_pct', 'weaker_half_cagr'):
        for r in sorted(ok, key=lambda r: -(r[key] if r[key] is not None else -1e9))[:size]:
            keep[tuple(r[k] for k in SWEEP_KEYS)] = r
    return sorted(keep.values(), key=lambda r: -(r['cagr_dd'] or 0))
