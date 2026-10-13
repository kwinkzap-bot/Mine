"""Weekly CPR Breakout, booked on the stock's OPTION.

The signal, the entry, the stop, the target and the exit are all the STOCK's
(Backtest/weekly_cpr_breakout_engine.py, unchanged). The order is the stock's
option: a BUY buys the ATM CALL, a SELL buys the ATM PUT, and this module
prices that leg off the contract's own recorded 1-minute candles at the
minutes the stock trade entered and exited.

* **Contract** — the stock's monthly expiry (NIFTY: its weekly) on or after the trade day
  (expiry_calendar.expiry_on_or_after, which knows NSE's Thursday → Tuesday
  move of 2025-09-01); on the expiry day itself the NEXT month's, since an
  expiry-day option is mostly decay and gamma. ATM = the strike nearest the
  stock's entry price on the stock's strike step.
* **Strike step** — the stock's CURRENT step is read off the Fyers symbol
  master (current_strike_steps). Steps are revised over the years, so a strike
  Breeze has no candles for is retried on half, double and 2.5× that step
  (each distinct STRIKE once — several steps often round to the same one)
  before the trade is given up as unpriced. A month where no strike had
  candles is remembered: the stock most likely had no options yet (stocks
  join F&O over time), and its other trades that month are not re-asked.
* **Prices** — entry: the option's close in the minute the stock broke out;
  exit: its close in the minute the stock hit SL / target, or its open in
  the cut-off minute for a time exit. An illiquid option often has no bar in
  a given minute; the last price at or before it is used, and a leg with no
  print at all before the entry is unpriced.

Only ICICI Breeze serves an EXPIRED contract (both instrument masters drop
them), so this needs an ICICI login. One Breeze request per contract-day,
disk-cached once the day has settled (historical_option_minutes) — the first
run over years of trades is a large share of Breeze's daily request budget
and belongs after market hours.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set

import pandas as pd

from trading_app.service.expiry_calendar import expiry_on_or_after

logger = logging.getLogger(__name__)

# Underlyings whose options are priced on their WEEKLY expiry, from the day
# weeklies were listed (cpr_option_service's WEEKLY_SINCE). Every stock — and
# BANKNIFTY / FINNIFTY / MIDCPNIFTY, monthly-only today and always carrying a
# monthly contract — is priced on the monthly one.
WEEKLY_SINCE = {'NIFTY': date(2019, 2, 11)}


def _cadence(symbol: str, day: date) -> str:
    since = WEEKLY_SINCE.get(symbol)
    return 'weekly' if since is not None and day >= since else 'monthly'


def current_strike_steps(nfo_rows: Iterable[Dict]) -> Dict[str, float]:
    """{stock: its most common strike gap} from the nearest expiry listed in
    an NFO instrument dump (the Fyers symbol master's shape)."""
    by_contract: Dict[tuple, Set[float]] = defaultdict(set)
    for row in nfo_rows:
        if row.get('instrument_type') != 'CE' or not row.get('name'):
            continue
        try:
            by_contract[(row['name'], str(row.get('expiry'))[:10])].add(float(row['strike']))
        except (TypeError, ValueError):
            continue
    nearest: Dict[str, tuple] = {}
    for name, expiry in by_contract:
        if name not in nearest or expiry < nearest[name][1]:
            nearest[name] = (name, expiry)
    steps: Dict[str, float] = {}
    for name, key in nearest.items():
        strikes = sorted(by_contract[key])
        gaps = Counter(round(b - a, 2) for a, b in zip(strikes, strikes[1:]) if b > a)
        if gaps:
            steps[name] = gaps.most_common(1)[0][0]
    return steps


def _price_at(bars: pd.DataFrame, ts: pd.Timestamp, field: str) -> Optional[float]:
    """`field` of the bar stamped `ts`, else the close of the last bar before it."""
    if ts in bars.index:
        return float(bars.at[ts, field])
    before = bars[bars.index < ts]
    return float(before['close'].iloc[-1]) if not before.empty else None


class OptionLegPricer:
    """Prices trades for one run. Holds the Breeze adapter, the steps, and a
    per-(stock, expiry) memory of the step that actually had candles, so a
    stock's other trades that month skip the misses."""

    def __init__(self, icici, strike_steps: Dict[str, float]):
        self.icici = icici
        self.steps = strike_steps
        self._good_step: Dict[tuple, float] = {}
        self._no_contract: Set[tuple] = set()

    def _bars(self, symbol: str, expiry: date, strike: float, right: str,
              day: date) -> Optional[pd.DataFrame]:
        raw = self.icici.historical_option_minutes(symbol, expiry, strike, right, day)
        if not raw:
            return None
        df = pd.DataFrame(raw).set_index('date')[['open', 'high', 'low', 'close']].astype(float)
        df.index = pd.to_datetime(df.index)
        if getattr(df.index, 'tz', None) is not None:
            df.index = df.index.tz_localize(None)
        return df.sort_index()

    def price(self, symbol: str, trade: Dict, trading_days: Iterable[date]) -> Optional[Dict]:
        """Option fields for one stock trade, or None when it cannot be priced."""
        step = self.steps.get(symbol)
        if not step:
            return None
        entry_ts = pd.Timestamp(trade['entry_time'])
        exit_ts = pd.Timestamp(trade['exit_time'])
        day = entry_ts.date()
        days = set(trading_days)
        cadence = _cadence(symbol, day)
        expiry = expiry_on_or_after(day, days, cadence=cadence)
        if expiry is not None and expiry == day:
            expiry = expiry_on_or_after(day + timedelta(days=1), days, cadence=cadence)
        if expiry is None:
            return None
        right = 'CE' if trade['direction'] == 'BUY' else 'PE'
        if (symbol, expiry) in self._no_contract:
            return None

        tried_strikes = set()
        known = self._good_step.get((symbol, expiry))
        for s in ([known] if known else []) + [step, step / 2, step * 2, step * 2.5]:
            if not s:
                continue
            strike = round(trade['entry_price'] / s) * s
            if strike <= 0 or strike != int(strike) or strike in tried_strikes:
                continue                    # whole rupees only; each strike once
            tried_strikes.add(strike)
            bars = self._bars(symbol, expiry, strike, right, day)
            if bars is None or bars.empty:
                continue
            self._good_step[(symbol, expiry)] = s
            entry_prem = _price_at(bars, entry_ts, 'close')
            exit_field = 'open' if trade['exit_reason'] == 'Time exit' else 'close'
            exit_prem = _price_at(bars, exit_ts, exit_field)
            if entry_prem is None or exit_prem is None or entry_prem <= 0:
                return None
            return {
                'option': f"{symbol} {expiry.strftime('%d-%b-%y')} {int(strike)} {right}",
                'option_type': right, 'strike': int(strike),
                'expiry': expiry.isoformat(),
                'entry_premium': round(entry_prem, 2),
                'exit_premium': round(exit_prem, 2),
            }
        if tried_strikes and (symbol, expiry) not in self._good_step:
            self._no_contract.add((symbol, expiry))
        return None


def trading_days_of(minutes: pd.DataFrame) -> List[date]:
    """The sessions in a stock's 1-minute history — what the expiry
    calendar snaps a holiday expiry back against."""
    return sorted({ts.date() for ts in minutes.index.normalize().unique()})
