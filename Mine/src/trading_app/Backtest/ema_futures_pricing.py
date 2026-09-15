"""EMA Confluence Breakout — futures pricing / carry-forward layer.

The live algo (algo/ema_confluence/ema_confluence_algo.py) decides on the
UNDERLYING and trades the monthly FUTURE. Until this module existed the
backtest did neither half of that: it decided AND booked on the underlying, so
its P&L described a cash-equity trade nobody places. This layer closes the gap
by taking the trade list the spot engine produced and re-pricing every fill on
the contract the live algo would actually have been holding on that date.

The split is deliberately the same one the live algo makes, and it is the
single most important property here:

  * The UNDERLYING supplies every DECISION — which candle is a signal, where
    the trigger/SL/Target sit, and therefore which trades happen and on which
    dates. None of that is touched. `EmaPullbackEngine` still runs exactly as
    before, on exactly the same bars, so a backtest keeps matching the live
    algo's daily replay (`_scan_one` adopts that engine's pending order).
  * The FUTURE supplies only the MONEY — which contract, the lot size, the
    fill price, the roll spread and the brokerage.

So this module NEVER changes which trades occur or when. If a contract's
history cannot be fetched at all it degrades to a zero basis (the spot price)
for that one leg and reports it, rather than silently dropping the trade — a
data hole must show up as a slightly wrong price, never as a different
strategy.

Carry-forward
-------------
A monthly future dies but a multi-day swing does not, so an open position is
ROLLED, mirroring `ema_confluence_algo._roll_position`: `_ROLL_SESSIONS`
trading days before the held contract's expiry the near leg is booked out and
the same side is re-entered on the next month. On daily bars both legs are
priced at that day's CLOSE of their own contract, which is the daily-bar
equivalent of the live algo's 12:00 roll and — because both closes are real
quotes off real contracts — makes the roll spread a real cost rather than an
assumed one. An exit that lands ON the roll day wins over the roll, same
precedence as the live tick (SL/Target are evaluated before the roll there).

Expiry dates are reconstructed from the symbol's own trading days via
`service/expiry_calendar` (the app has no historical expiry calendar and no
broker will serve one — every instrument master drops a contract the moment it
expires). Holidays are absorbed by snapping back to the last real session.

Costs
-----
Brokerage is `BROKERAGE_PER_ORDER` per ORDER, not per round trip: an entry and
an exit are two orders, and every roll adds two more (book the near leg, open
the far one). A position carried across three expiries therefore pays eight
orders' worth, which is the whole reason a long carry has to be charged
honestly — it is the cost that decides whether carrying is worth it at all.

Price model
-----------
A daily bar cannot say what the future was quoting at the instant the spot
trigger broke, so a fill is priced as

    futures fill = spot decision price + basis(contract, day)
    basis        = futures close − spot close, on that day, on that contract

i.e. the exact price the strategy decided on, moved onto the contract's own
scale by that contract's own observed basis. Note what this gives for free:
at a roll, the decision price IS the spot close, so both legs collapse to the
contracts' actual closes and nothing is modelled at all. The approximation is
confined to entry/exit fills, where it assumes the basis did not move between
the trigger and the close — worth a few hundredths of a percent, against the
~0.5% error that pricing the whole trade on the spot scale carries.
"""
import logging
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd

from trading_app.service.expiry_calendar import expiry_weekday_for

logger = logging.getLogger(__name__)

# Flat brokerage per ORDER. Entry, exit and each of a roll's two legs are one
# order each — see the module docstring.
BROKERAGE_PER_ORDER = 1000

# Trading days before expiry at which the position moves to the next month.
# Mirrors _ROLL_SESSIONS_BEFORE_EXPIRY in
# algo/ema_confluence/ema_confluence_algo.py — the live algo's own roll
# window, kept in step by tests/test_ema_futures_pricing.py rather than by an
# import, so the backtest layer never pulls the live algo module in.
ROLL_SESSIONS_BEFORE_EXPIRY = 3

# A holiday moves an expiry by a day or two, never by a week.
_SNAP_LIMIT_DAYS = 6

# How far back a contract's own series may be searched for a basis when it has
# no bar on the day being priced (a thin far month simply did not trade).
_BASIS_LOOKBACK_DAYS = 7

# A monthly future's basis is bounded by its cost of carry — at Indian rates a
# three-month contract runs under ~2% of spot, and dividends routinely make it
# negative. Anything past this is not a basis at all: it is the two series
# disagreeing about a CORPORATE ACTION, because the daily spot store is
# split/bonus-ADJUSTED and Breeze's futures history is not. Verified
# 2026-09-05 on RELIANCE's 1:1 bonus (ex-date 2024-10-28): spot showed 1344.03
# on 2024-10-15 against the Oct-2024 contract's 2700.50, a "basis" of 101%,
# which fabricated ~1,400 points of profit on a trade that made 204.
# Rejecting it sends the trade to the spot scale, which is the series that
# stays self-consistent across the action.
_BASIS_SANITY_PCT = 5.0


# ── Contract calendar ────────────────────────────────────────────────────

def _snap(day: date, trading: set) -> date:
    """The last trading day on or before `day` — how holidays are absorbed.

    Falls back to `day` itself for a month that reaches past the data we hold
    (the current/next contract), where there is nothing to snap to yet.
    """
    probe = day
    stop = day - timedelta(days=_SNAP_LIMIT_DAYS)
    while probe >= stop:
        if probe in trading:
            return probe
        probe -= timedelta(days=1)
    return day


def monthly_expiries(trading_days: Sequence[date],
                     weekday: Optional[int] = None) -> List[date]:
    """Monthly futures expiry for every month the data spans, plus the next
    one, ascending.

    `weekday` overrides the NSE regime table (Thursday until 2025-09-01,
    Tuesday after); None means "whatever the regime says for that date", which
    is the only way to span the change correctly on a multi-year backtest.
    """
    days = sorted({d for d in trading_days if d})
    if not days:
        return []
    trading = set(days)
    cursor = days[0].replace(day=1)
    # One month PAST the data, so a position still open on the last bar always
    # has a contract to be on (and a roll near the end has a destination).
    stop = (days[-1].replace(day=28) + timedelta(days=4)).replace(day=1)
    out: List[date] = []
    while cursor <= stop:
        nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        probe = nxt - timedelta(days=1)          # last day of `cursor`'s month
        wd = expiry_weekday_for(probe) if weekday is None else weekday
        while probe.weekday() != wd:
            probe -= timedelta(days=1)
        out.append(_snap(probe, trading))
        cursor = nxt
    return out


def contract_timeline(trading_days: Sequence[date],
                      roll_sessions: int = ROLL_SESSIONS_BEFORE_EXPIRY,
                      weekday: Optional[int] = None) -> List[Dict[str, Any]]:
    """[{'expiry': date, 'roll_day': date|None}, ...] ascending by expiry.

    `roll_day` is the session `roll_sessions` trading days before expiry — the
    day the live algo stops trading that contract. None for an expiry that
    reaches past the data, which therefore never triggers a roll: we cannot
    know that its roll day has passed, and inventing one would book a roll at a
    price that was never quoted.
    """
    days = sorted({d for d in trading_days if d})
    if not days:
        return []
    out: List[Dict[str, Any]] = []
    for expiry in monthly_expiries(days, weekday):
        if expiry > days[-1]:
            out.append({'expiry': expiry, 'roll_day': None})
            continue
        i = bisect_right(days, expiry) - 1       # last session on or before it
        out.append({'expiry': expiry, 'roll_day': days[max(0, i - roll_sessions)]})
    return out


def select_contract(day: date, timeline: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The contract this strategy is on for `day`: the nearest one whose roll
    moment has not passed.

    Same ABSOLUTE choice `ema_confluence_algo.select_contract` makes, and for
    the same reason — it answers "what does a new entry open on?" and "has the
    held contract rolled?" with one rule, so a position opened inside a roll
    window cannot roll twice.
    """
    for c in timeline:
        if c['roll_day'] is None or day < c['roll_day']:
            return c
    return timeline[-1] if timeline else None


def contract_label(expiry: date) -> str:
    """'AUG 2026' — the same month label the live algo shows on a paper trade."""
    return expiry.strftime('%b %Y').upper()


# ── Pricing ──────────────────────────────────────────────────────────────

class _ContractPrices:
    """Lazily-fetched daily closes for one root's monthly contracts.

    Each contract is asked for at most once per run — including the ones that
    come back empty, which is what keeps a symbol whose futures history the
    broker cannot serve from re-requesting the same dead contract on every
    trade that touches it.
    """

    def __init__(self, fetch_close: Callable[[date], Optional[pd.Series]]):
        self._fetch = fetch_close
        self._cache: Dict[date, Optional[tuple]] = {}
        self.missing: set = set()          # contracts that served no data at all

    def _bars(self, expiry: date) -> Optional[tuple]:
        """(days, closes) as plain ascending lists, or None."""
        if expiry not in self._cache:
            bars = None
            try:
                series = self._fetch(expiry)
            except Exception as exc:       # a broker hiccup must not kill the run
                logger.warning('[EMA futures] %s history failed: %s', expiry, exc)
                series = None
            if series is not None and len(series):
                series = series.sort_index()
                days = [pd.Timestamp(d).date() for d in series.index]
                bars = (days, [float(v) for v in series.to_numpy()])
            self._cache[expiry] = bars
            if bars is None:
                self.missing.add(expiry)
        return self._cache[expiry]

    def close_on(self, expiry: date, day: date) -> Optional[float]:
        """That contract's close on `day`, or on the last day it traded within
        _BASIS_LOOKBACK_DAYS before it (a thin far month skips sessions)."""
        bars = self._bars(expiry)
        if bars is None:
            return None
        days, closes = bars
        i = bisect_right(days, day) - 1
        if i < 0 or (day - days[i]).days > _BASIS_LOOKBACK_DAYS:
            return None
        return closes[i]


def _as_date(value: Any) -> Optional[date]:
    try:
        return pd.Timestamp(value).date()
    except (ValueError, TypeError):
        return None


def _result_for(pnl: float) -> str:
    return 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH')


def apply_futures_pricing(trades: List[Dict[str, Any]], daily_df: pd.DataFrame,
                          fetch_close: Callable[[date], Optional[pd.Series]],
                          *, lots: int = 1, lot_size: int = 1,
                          roll_sessions: int = ROLL_SESSIONS_BEFORE_EXPIRY,
                          brokerage_per_order: int = BROKERAGE_PER_ORDER,
                          expiry_weekday: Optional[int] = None) -> Dict[str, Any]:
    """Re-price `trades` (in place) on the monthly futures, rolling as needed.

    `daily_df` is the engine's own prepared frame — its 'datetime'/'close'
    columns give both the trading-day calendar the expiries are reconstructed
    from and the spot closes the basis is measured against.

    `fetch_close(expiry)` returns that contract's daily closes as a Series
    indexed by `date` (or None when the contract cannot be served).

    Each trade keeps its spot-scale decision record under `spot_*` and gains
    the futures-scale one: `entry_price`/`exit_price`/`pnl` become the
    CONTRACT's, and `legs` holds one entry per contract the position was
    carried on. Returns run-level counters for the caller's _debug block.
    """
    stats = {'rolls': 0, 'orders': 0, 'brokerage': 0, 'legs': 0,
             'unpriced_legs': 0, 'spot_priced_trades': 0, 'missing_contracts': [],
             'implausible_basis': [], 'contracts_used': 0, 'qty': 0,
             'lot_size': int(lot_size or 1)}
    if not trades:
        return stats

    frame = daily_df
    if 'datetime' not in frame.columns or frame.empty:
        return stats
    trading_days = [ts.date() for ts in pd.DatetimeIndex(frame['datetime'])]
    spot_close = dict(zip(trading_days, (float(c) for c in frame['close'])))
    timeline = contract_timeline(trading_days, roll_sessions, expiry_weekday)
    if not timeline:
        return stats

    prices = _ContractPrices(fetch_close)
    qty = max(1, int(lots or 1)) * max(1, int(lot_size or 1))
    stats['qty'] = qty
    used: set = set()
    implausible: set = set()   # contracts rejected by the basis sanity check

    def basis(expiry: date, day: date) -> Optional[float]:
        """That contract's premium over spot on `day`, or None if unknowable.

        None is not a zero: it means this fill cannot be moved onto the
        contract's scale at all, which is a fact about the whole trade (see
        below), not a basis of nothing.
        """
        fut = prices.close_on(expiry, day)
        spot = spot_close.get(day)
        if fut is None or spot is None:
            return None
        gap = fut - spot
        if spot and abs(gap) > spot * _BASIS_SANITY_PCT / 100:
            if expiry not in implausible:
                implausible.add(expiry)
                logger.warning(
                    '[EMA futures] %s basis is %.1f%% of spot on %s (fut %.2f vs spot %.2f) — '
                    'not a carry, almost certainly a corporate action the daily spot store is '
                    'adjusted for and this contract is not. Trades touching it are priced on '
                    'the spot scale.', contract_label(expiry), gap / spot * 100, day, fut, spot)
            return None
        return gap

    for trade in trades:
        entry_day = _as_date(trade.get('entry_time'))
        exit_day  = _as_date(trade.get('exit_time'))
        if entry_day is None or exit_day is None:
            continue
        long_side = str(trade.get('type', 'Long')) == 'Long'
        spot_entry = float(trade['entry_price'])
        spot_exit  = float(trade['exit_price'])

        contract = select_contract(entry_day, timeline)
        if contract is None:
            continue
        # Legs are collected on the SPOT scale first, each carrying the basis
        # that will move it onto its contract — see the fold below for why the
        # two steps are separate.
        legs: List[Dict[str, Any]] = []
        leg_entry_day    = entry_day
        leg_entry_level  = spot_entry
        leg_entry_basis  = basis(contract['expiry'], entry_day)
        used.add(contract['expiry'])

        # Walk the sessions the position is held over, rolling whenever the
        # held contract reaches its roll day. The exit day itself is never a
        # roll day: the strategy's own exit wins, exactly as it does on a live
        # tick (rolls run last there, and only for symbols still in position).
        i = bisect_left(trading_days, entry_day)
        while i < len(trading_days):
            day = trading_days[i]
            if day >= exit_day:
                break
            roll_day = contract['roll_day']
            if roll_day is not None and day >= roll_day:
                nxt = select_contract(day, timeline)
                if nxt is None or nxt['expiry'] == contract['expiry']:
                    break              # nothing further listed — carry as is
                # Both legs of a roll are decided at that session's spot close,
                # so each collapses to its own contract's actual close.
                level = spot_close.get(day, spot_entry)
                legs.append({
                    'contract':    contract_label(contract['expiry']),
                    'expiry':      contract['expiry'].isoformat(),
                    'entry_date':  leg_entry_day.isoformat(),
                    'entry_level': leg_entry_level, 'entry_basis': leg_entry_basis,
                    'exit_date':   day.isoformat(),
                    'exit_level':  level,
                    'exit_basis':  basis(contract['expiry'], day),
                    'reason':      'ROLL',
                })
                leg_entry_basis = basis(nxt['expiry'], day)
                contract = nxt
                used.add(nxt['expiry'])
                leg_entry_day, leg_entry_level = day, level
                continue               # a single day may cross two roll days
            i += 1

        legs.append({
            'contract':    contract_label(contract['expiry']),
            'expiry':      contract['expiry'].isoformat(),
            'entry_date':  leg_entry_day.isoformat(),
            'entry_level': leg_entry_level, 'entry_basis': leg_entry_basis,
            'exit_date':   exit_day.isoformat(),
            'exit_level':  spot_exit,
            'exit_basis':  basis(contract['expiry'], exit_day),
            'reason':      trade.get('exit_reason', 'EXIT'),
        })

        # A trade is priced ALL on the futures scale or ALL on the spot one —
        # never half of each. Breeze's history for older contracts has genuine
        # holes (see filters/futures_candle_store), and pricing one end of a
        # trade with a basis and the other without invents a whole basis of
        # P&L out of the gap: the Mar-2019 NIFTY carry came out 150 points
        # better than the spot move it was made of, purely from the missing
        # exit quote. Falling back wholesale costs accuracy and nothing else —
        # the trade, its dates and its rolls are untouched, and the fallback is
        # counted so a run says how much of it was approximated.
        unpriced = sum(1 for leg in legs
                       for k in ('entry_basis', 'exit_basis') if leg[k] is None)
        if unpriced:
            stats['unpriced_legs'] += len(legs)
            stats['spot_priced_trades'] += 1
        for leg in legs:
            for side in ('entry', 'exit'):
                adj = 0.0 if unpriced else leg[f'{side}_basis']
                leg[f'{side}_price'] = round(leg[f'{side}_level'] + adj, 2)
            for k in ('entry_level', 'entry_basis', 'exit_level', 'exit_basis'):
                leg.pop(k)

        sign = 1.0 if long_side else -1.0
        for leg in legs:
            leg['pnl'] = round(sign * (leg['exit_price'] - leg['entry_price']), 2)
            leg['pnl_rupees'] = round(leg['pnl'] * qty, 2)
        points = round(sum(leg['pnl'] for leg in legs), 2)
        rolls  = len(legs) - 1
        orders = 2 * len(legs)          # every leg is an entry AND an exit order
        brokerage = orders * brokerage_per_order

        # Spot-scale record kept intact — it is what the strategy actually
        # decided on, and the SL/Target columns are quoted on that scale.
        trade['spot_entry_price'] = round(spot_entry, 2)
        trade['spot_exit_price']  = round(spot_exit, 2)
        trade['spot_pnl']         = trade.get('pnl')
        trade['spot_pnl_pct']     = trade.get('pnl_pct')

        entry_fut = legs[0]['entry_price']
        trade['entry_price'] = entry_fut
        trade['exit_price']  = legs[-1]['exit_price']
        trade['pnl']         = points
        trade['pnl_pct']     = round(points / entry_fut * 100, 2) if entry_fut else 0.0
        trade['result']      = _result_for(points)
        trade['futures_priced'] = not unpriced
        trade['contract']       = legs[0]['contract']
        trade['exit_contract']  = legs[-1]['contract']
        trade['rolls']          = rolls
        trade['orders']         = orders
        trade['legs']           = legs
        trade['lot_size']       = int(lot_size or 1)
        trade['qty']            = qty
        trade['brokerage']      = brokerage
        trade['capital']        = round(entry_fut * qty, 2)
        trade['sl_risk_rupees'] = round(abs(spot_entry - float(trade.get('sl_price', spot_entry))) * qty, 2)
        trade['gross_pnl_rupees'] = round(points * qty, 2)
        trade['pnl_rupees']       = round(points * qty - brokerage, 2)

        stats['rolls']  += rolls
        stats['orders'] += orders
        stats['legs']   += len(legs)
        stats['brokerage'] += brokerage

    stats['contracts_used'] = len(used)
    stats['missing_contracts'] = sorted(e.isoformat() for e in prices.missing)
    stats['implausible_basis'] = sorted(e.isoformat() for e in implausible)
    return stats


def rupee_view(trade: Dict[str, Any]) -> Dict[str, Any]:
    """A trade re-keyed for a ₹ summary: net P&L in the 'pnl' slot, and the
    WIN/LOSS verdict recomputed on that net figure.

    `_summarise` classifies off the precomputed `result`, so without the
    recompute a trade that made points but lost money to brokerage would be
    counted a win in the very summary whose job is to say otherwise.
    """
    net = trade.get('pnl_rupees', 0.0)
    return {**trade, 'pnl': net, 'result': _result_for(net)}
