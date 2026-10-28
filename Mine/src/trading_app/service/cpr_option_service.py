"""The option leg behind every trade on the CPR Manual vs Chart grid.

The sheet's trades are written in NIFTY points, but NIFTY itself cannot be
bought — the order that would actually be placed is an option: the ATM CALL
for a BUY, the ATM PUT for a SELL, on the weekly expiry running that day.
This module prices that leg from the contract's own recorded 1-minute
candles so the grid can show, beside the index numbers, what the premium
did — entry, the level that closed the trade, and the P&L in premium
points and rupees per lot.

The strike is ATM to the entry by default. With a **premium target** (the
page's 150 / 200 / 250 / 300 dropdown) it is instead the strike of that
side whose premium at the entry minute was nearest the target — a deeper
ITM contract for a bigger premium — found by walking the ladder from ATM
one strike at a time until the target is bracketed (`pick_strike`). Every
strike looked at is one Breeze request, so a premium target's first read
is several times the ATM one; both are disk-cached afterwards.

Two things are load-bearing:

* **The times come from the index, the prices from the option.** The trade
  is still the sheet's index trade; it is replayed on the index's 1-minute
  bars (the same fill-then-first-of-target/SL walk as the 5-minute grid,
  one minute finer) and the option is read at those minutes — the close of
  the minute the index crossed the entry, the close of the minute it
  reached the target or stop, the 15:15 open for a square-off.
* **A level the index never reached has no recorded premium.** For that
  one the cell carries an estimate, marked ≈: the entry premium moved by
  the day's own delta, a least-squares slope of the option's close against
  the index's close over the session's minutes. It is there so the stop
  and target can be placed as premiums; it is not a fill.

Only Breeze serves an already-expired contract (see
icici_data_service.historical_option), so the leg needs an ICICI login;
without one the grid says so and shows the index numbers alone. Settled
sessions are disk-cached there, so the first read of a long sheet is the
slow one (one Breeze request per trade at 1.5/s).
"""

import math
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from trading_app.app.utils.logger import logger
from trading_app.service import cpr_backtest_service as cpr_bt
from trading_app.service.expiry_calendar import expiry_on_or_after

STRIKE_STEP = {'NIFTY': 50, 'BANKNIFTY': 100, 'FINNIFTY': 50, 'MIDCPNIFTY': 25, 'SENSEX': 100}
BROKERAGE = 100.0   # ₹ per trade, taken off every ₹/lot figure; `pnl` in points stays gross
LOT_SIZE = {'NIFTY': 65, 'BANKNIFTY': 30, 'FINNIFTY': 65, 'MIDCPNIFTY': 120, 'SENSEX': 20}
EXPIRY_CADENCE = {'NIFTY': 'weekly', 'SENSEX': 'weekly'}      # the rest are monthly-only
WEEKLY_SINCE = {'NIFTY': date(2019, 2, 11), 'SENSEX': date(2023, 5, 15)}   # NIFTY weekly options were listed from here; before it only the monthly contract existed
OPTION_EXCHANGE = {'SENSEX': 'BFO'}
# BSE runs its own expiry weekday, never NSE's: Friday, then Tuesday from 2025-01-01,
# then Thursday from 2025-09-01 (the live BFO master lists Thursdays). Reading SENSEX
# off NSE's Tuesday regime asked Breeze for contracts that never existed — every leg
# came back unpriced and the backtest returned zero trades.
_BSE_WEEKDAY_REGIMES = [(date(1900, 1, 1), 4), (date(2025, 1, 1), 1), (date(2025, 9, 1), 3)]


def expiry_weekday(symbol: str, day: date) -> Optional[int]:
    """The symbol's own expiry weekday on `day`; None = NSE's regime table."""
    if (symbol or '').upper() != 'SENSEX':
        return None
    wd = _BSE_WEEKDAY_REGIMES[0][1]
    for cutover, w in _BSE_WEEKDAY_REGIMES:
        if cutover <= day:
            wd = w
    return wd

MIN_PREMIUM = 0.05            # an estimate never goes below the tick
PREMIUM_CHOICES = (150, 200, 250, 300)   # the page's premium targets
PICK_DELTA_GUESS = 0.6        # premium change per strike step, as a share of the step, for the first jump
PICK_MAX_STRIKES = 10         # strikes read per trade before the walk gives up
DELTA_MIN_POINTS = 20         # minutes needed before a slope is trusted
SETUP_BAR_MINUTES = 5         # the sheet's setup candle is a 5-minute bar


LEGS_CACHE_VERSION = 5        # bump when how a leg is priced changes: every saved leg is then re-read (5: a locked leg's target_time)
LEG_RETRY_DAYS = 7            # a leg that found no option data is looked up again after this long
_LEGS_LOCK = threading.Lock()


class OptionDataUnavailable(Exception):
    """No Breeze session to read option candles from."""


_stock_specs: Dict[str, Any] = {}


def stock_spec(symbol: str) -> Optional[Dict[str, Any]]:
    """{'lot': int, 'step': float} for an F&O stock, read off the Fyers NFO master
    (the contracts still listed): the lot size is the listed one and the strike step
    is the smallest gap between the strikes of its nearest expiry. None when the
    symbol has no listed options (not an F&O stock, or no Fyers session). The
    master only shows TODAY's lot and strike ladder, so an old trade is priced on
    those — an approximation the backtest says so about."""
    root = (symbol or '').upper()
    hit = _stock_specs.get(root)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    spec = None
    try:
        from trading_app.service.provider_logic import get_fyers_adapter
        fy = get_fyers_adapter()
        if fy is not None:
            rows = [i for i in fy.instruments('NFO')
                    if str(i.get('name') or '').strip().upper() == root
                    and i.get('instrument_type') in ('CE', 'PE') and i.get('expiry')]
            if rows:
                nearest = min(i['expiry'] for i in rows)
                strikes = sorted({float(i['strike']) for i in rows if i['expiry'] == nearest and i.get('strike')})
                gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b - a > 0]
                spec = {'lot': int(rows[0].get('lot_size') or 1),
                        'step': min(gaps) if gaps else 1.0}
    except Exception as e:      # noqa: BLE001 — no spec just means "not an F&O stock" to the caller
        logger.warning(f'[CPR options] stock spec for {root} unavailable: {e}')
    if spec:
        _stock_specs[root] = (time.time(), spec)
    return spec


def is_index(symbol: str) -> bool:
    return (symbol or '').upper() in STRIKE_STEP


def strike_step(symbol: str) -> float:
    root = (symbol or '').upper()
    if root not in STRIKE_STEP:
        spec = stock_spec(root)
        if spec:
            return spec['step']
    return STRIKE_STEP.get(root, 50)


def lot_size(symbol: str) -> int:
    root = (symbol or '').upper()
    if root not in LOT_SIZE:
        spec = stock_spec(root)
        if spec:
            return spec['lot']
    return LOT_SIZE.get(root, 65)


def _live_expiry(symbol: str, day: date) -> Optional[date]:
    """The nearest expiry on or after `day` that Zerodha still lists for the
    symbol's options, or None (no session, or nothing listed)."""
    try:
        from trading_app.service.provider_logic import get_kite
        kite = get_kite(instance=1)
        if kite is None:
            return None
        exchange = OPTION_EXCHANGE.get(symbol.upper(), 'NFO')
        hit = _kite_nfo.get(exchange)
        if not hit or time.time() - hit[0] > 6 * 3600:
            hit = (time.time(), kite.instruments(exchange))
            _kite_nfo[exchange] = hit
        root = symbol.upper()
        dates = {i['expiry'] for i in hit[1]
                 if str(i.get('name') or '').strip().upper() == root and i.get('instrument_type') in ('CE', 'PE')
                 and i.get('expiry') and i['expiry'] >= day}
        return min(dates) if dates else None
    except Exception as e:      # noqa: BLE001 — the derived date stands
        logger.warning(f'[CPR options] live expiry list unavailable: {e}')
        return None


def contract(symbol: str, day: date, trade: str, entry: float,
             trading_days: List[date]) -> Optional[Dict[str, Any]]:
    """The contract the trade would be placed on: ATM to the entry level,
    CALL for a BUY / PUT for a SELL, the nearest expiry on or after the day."""
    if trade not in ('BUY', 'SELL') or entry is None:
        return None
    step = strike_step(symbol)
    cadence = EXPIRY_CADENCE.get(symbol.upper(), 'monthly')
    if cadence == 'weekly' and day < WEEKLY_SINCE.get(symbol.upper(), date.min):
        cadence = 'monthly'                    # 2018: there was no weekly contract to buy
    expiry = expiry_on_or_after(day, trading_days, cadence, expiry_weekday(symbol, day))
    if expiry is None:
        return None
    if trading_days and expiry > max(trading_days):
        # A date past the last session cannot be snapped for holidays — nothing after it is
        # known. The exchange's own list can: 2026-10-20 (Dussehra) was derived as the weekly
        # expiry and the real one was Mon 19 Oct, so the leg looked up a contract that
        # does not exist.
        expiry = _live_expiry(symbol, day) or expiry
    return {'strike': int(round(entry / step) * step),
            'option_type': 'CE' if trade == 'BUY' else 'PE',
            'expiry': expiry.isoformat()}


def minute_setup(setup_time: Optional[str]) -> str:
    """The 5-minute setup candle's last minute, so the 1-minute replay fills
    from the same bar the 5-minute one does (09:15 setup -> fills from 09:20)."""
    t = datetime.strptime(setup_time or '09:15', '%H:%M') + timedelta(minutes=SETUP_BAR_MINUTES - 1)
    return t.strftime('%H:%M')


def bar_at(bars: List[Dict[str, Any]], time: Optional[str]) -> Optional[Dict[str, Any]]:
    """The bar stamped `time`, else the last one before it — an illiquid
    strike can miss a minute."""
    if not time:
        return None
    found = None
    for b in bars:
        if b['time'] > time:
            break
        found = b
    return found


def day_delta(spot: List[Dict[str, Any]], opt: List[Dict[str, Any]]) -> Optional[float]:
    """Least-squares slope of the option's close on the index's close over the
    minutes both have — the day's own delta, signed (a PUT's is negative)."""
    by_time = {b['time']: b['close'] for b in opt}
    pairs = [(b['close'], by_time[b['time']]) for b in spot if b['time'] in by_time]
    if len(pairs) < DELTA_MIN_POINTS:
        return None
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    var = sum((x - mx) ** 2 for x, _ in pairs)
    if var <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    return round(cov / var, 3)


def _est(entry_px: float, delta: Optional[float], entry: float, level: float) -> Optional[float]:
    if delta is None:
        return None
    return round(max(MIN_PREMIUM, entry_px + delta * (level - entry)), 2)


def replay(tm: Dict[str, Any], spot: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The index trade on the index's 1-minute bars."""
    return cpr_bt.simulate_trade(spot, tm['trade'], tm['entry'], tm['target'], tm['sl'],
                                 minute_setup(tm.get('setup_time')))


def pick_strike(symbol: str, con: Dict[str, Any], day: date, entry_time: str, premium: float,
                fetch_option: Callable[[Dict[str, Any], date], List[Dict[str, Any]]]
                ) -> tuple:
    """The strike of `con`'s side whose premium at `entry_time` was nearest
    `premium`, and its bars. Walks the ladder from the ATM contract: a first
    jump sized by PICK_DELTA_GUESS, then one strike at a time towards the
    target until two neighbours bracket it. Strikes already read are kept,
    so the walk costs one fetch per strike it looks at.

    Returns (contract, bars, looked_at); the ATM contract with no bars when
    nothing on the ladder priced."""
    step = strike_step(symbol)
    itm = -1 if con['option_type'] == 'CE' else 1          # deeper ITM is a lower CALL strike, a higher PUT strike
    seen: Dict[int, tuple] = {}
    # SENSEX's ladder is 100 apart on an index ~3x NIFTY's, so a given premium sits
    # about twice as many strikes from the ATM: on 2024-06-03 (a gap day) the ≈500 CE
    # was 12 strikes out and a 10-strike walk settled on a 1,274 premium.
    max_strikes = PICK_MAX_STRIKES * (2 if (symbol or '').upper() == 'SENSEX' else 1)

    def price(strike: int) -> Optional[float]:
        if strike not in seen:
            c = dict(con, strike=strike)
            try:
                bars = fetch_option(c, day)
            except Exception as e:      # noqa: BLE001 — a dead strike is just skipped
                logger.warning(f"[CPR options] {day} {strike}{con['option_type']}: {e}")
                bars = []
            b = bar_at(bars, entry_time)
            seen[strike] = (c, bars, b['close'] if b else None)
        return seen[strike][2]

    atm = con['strike']
    p0 = price(atm)
    if p0 is None:
        return con, [], 1
    cur = atm
    if p0 != premium:
        jump = int(round((premium - p0) / (PICK_DELTA_GUESS * step)))
        way = itm if jump > 0 else -itm
        cur = atm + way * max(1, min(abs(jump), max_strikes - 2)) * step
        while cur != atm and price(cur) is None and len(seen) < max_strikes // 2 + 2:
            cur -= way * step                  # the jump landed on a strike with no print: come back towards the ATM
        if price(cur) is None:
            cur = atm
    # One strike at a time towards the target until it sits between two neighbours.
    while len(seen) < max_strikes:
        pc = price(cur)
        if pc is None:
            break
        towards = itm if premium > pc else -itm
        nxt = cur + towards * step
        pn = price(nxt)
        # An illiquid strike has no print at the entry minute (3 Jun 2024: 77200-77700 had
        # none, 78000 did, at 680): look past up to 10 such gaps for the next one that does.
        for _ in range(10):
            if pn is not None or len(seen) >= max_strikes:
                break
            nxt += towards * step
            pn = price(nxt)
        if pn is None:
            break
        if (premium - pc) * (premium - pn) <= 0:     # bracketed (or exact)
            cur = nxt if abs(pn - premium) < abs(pc - premium) else cur
            break
        if abs(pn - premium) > abs(pc - premium):    # moved away: the ladder is not monotonic here
            break
        cur = nxt
    best = min((k for k in seen if seen[k][2] is not None), key=lambda k: abs(seen[k][2] - premium))
    c, bars, _ = seen[best]
    return c, bars, len(seen)


def option_leg(tm: Dict[str, Any], con: Dict[str, Any], spot: List[Dict[str, Any]],
               opt: List[Dict[str, Any]], lot: int, sim: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The premium the index trade `tm` would have paid and collected on
    contract `con`, from the index's and the option's 1-minute bars."""
    leg: Dict[str, Any] = dict(con, entry=None, target=None, sl=None, exit=None,
                               entry_time=None, exit_time=None, target_time=None, result=None, pnl=None,
                               pnl_lot=None, delta=None, estimated=[], error=None)
    if not spot:
        leg['error'] = 'no 1-minute index bars'
        return leg
    if not opt:
        leg['error'] = 'no option bars'
        return leg
    sim = sim or replay(tm, spot)
    leg.update(result=sim['result'], entry_time=sim['entry_time'], exit_time=sim['exit_time'],
               target_time=sim.get('target_time'))
    if sim['result'] in (None, 'No fill'):
        return leg

    entry_bar = bar_at(opt, sim['entry_time'])
    if entry_bar is None:
        leg['error'] = f"no option bar at {sim['entry_time']}"
        return leg
    entry_px = entry_bar['close']
    leg['entry'] = entry_px
    delta = day_delta(spot, opt)
    leg['delta'] = delta

    exit_px = None
    if sim['result'] in ('Target', 'SL'):
        exit_bar = bar_at(opt, sim['exit_time'])
        exit_px = exit_bar['close'] if exit_bar else None
        if sim.get('target_time') and sim['exit_time'] >= cpr_bt.SQUARE_OFF and exit_bar \
                and exit_bar['time'] == sim['exit_time']:
            exit_px = exit_bar['open']          # a ridden target squared off at 15:15: that bar's open
    elif sim['result'] == 'EOD':
        exit_bar = bar_at(opt, sim['exit_time'])
        # The square-off is at that bar's open, as the index replay's is.
        exit_px = exit_bar['open'] if exit_bar and exit_bar['time'] == sim['exit_time'] \
            else (exit_bar['close'] if exit_bar else None)
    leg['exit'] = exit_px

    # The level that closed the trade is a real print; the other is the
    # entry premium moved by the day's delta, and says so.
    leg['target'] = exit_px if sim['result'] == 'Target' else _est(entry_px, delta, tm['entry'], tm['target'])
    if sim.get('target_time'):
        # Locked: the target printed at target_time and the trade rode on to
        # exit_time — the target's premium is THAT bar's, the exit's is later.
        tbar = bar_at(opt, sim['target_time'])
        leg['target'] = tbar['close'] if tbar else _est(entry_px, delta, tm['entry'], tm['target'])
    leg['sl'] = exit_px if sim['result'] == 'SL' else _est(entry_px, delta, tm['entry'], tm['sl'])
    if sim['result'] != 'Target' and leg['target'] is not None:
        leg['estimated'].append('target')
    if sim['result'] != 'SL' and leg['sl'] is not None:
        leg['estimated'].append('sl')

    if exit_px is not None:
        leg['pnl'] = round(exit_px - entry_px, 2)          # always a long option
        leg['pnl_lot'] = round(leg['pnl'] * lot - BROKERAGE, 2)
    return leg


def _trading_days(symbol: str, first: date, last: date) -> List[date]:
    """The sessions around the sheet, for the expiry calendar to snap
    holidays against — a week either side is enough. Read a year at a time:
    one request over seven years comes back cut short, and every trade past
    the cut then has no expiry to snap to ("no contract")."""
    from trading_app.service import multichart_service as mc
    # The local 1-minute store holds every closed session: no request at all. Until 2026-10-19
    # this read Fyers' daily candles only, so once Fyers' 5,000-requests/day cap was gone the
    # list came back empty, no expiry could be snapped to, and every leg said "no contract".
    try:
        got = cpr_bt._bars_from_store(symbol, first, min(last + timedelta(days=7), date.today()))
    except Exception as e:      # noqa: BLE001 — the provider below is the fallback
        logger.warning(f'[CPR options] trading days from the store failed: {e}')
        got = None
    if got and got[0]:
        return sorted({b['date'] for b in got[0]})
    adapter = mc.provider()
    fy = mc.resolve_symbol(symbol)
    start, end = first - timedelta(days=7), min(last + timedelta(days=7), date.today())
    days: Dict[date, None] = {}
    while start <= end:
        stop = min(start + timedelta(days=359), end)
        raw = adapter.historical_data(fy, start.isoformat(), stop.isoformat(), 'day',
                                      use_cache=True, cache_ttl=3600.0)
        for d in (b['date'] for b in cpr_bt._daily_rows(raw)):
            days[d] = None
        start = stop + timedelta(days=1)
    return sorted(days)


def _spot_minutes(symbol: str, first: date, last: date) -> Dict[str, List[Dict[str, Any]]]:
    """1-minute index bars by session: the local store for every session it holds,
    the charts' provider for the rest (Zerodha first — Fyers' daily cap, 5,000 a
    day, is gone by the afternoon and took every leg of an unread session with it)."""
    from trading_app.service import multichart_service as mc
    out: Dict[str, List[Dict[str, Any]]] = {}
    stored_last = None
    try:
        import pandas as pd
        from trading_app.Backtest import minute_candle_store as store
        key = store._cache_key('fyers', symbol.upper())
        entry = store._mem.get(key) or store._load_disk(key)
        if entry and not entry['df'].empty:
            m = entry['df']
            stored_last = m.index[-1].date()
            m = m[(m.index >= pd.Timestamp(first)) & (m.index < pd.Timestamp(last) + pd.Timedelta(days=1))]
            mins = m.index.hour * 60 + m.index.minute
            m = m[(mins >= 9 * 60 + 15) & (mins < 15 * 60 + 30)]
            for ts, o_, h_, l_, c_, v_ in zip(m.index, m['open'], m['high'], m['low'], m['close'], m['volume']):
                out.setdefault(ts.strftime('%Y-%m-%d'), []).append(
                    {'time': ts.strftime('%H:%M'), 'open': float(o_), 'high': float(h_), 'low': float(l_),
                     'close': float(c_), 'volume': float(v_ or 0)})
    except Exception as e:      # noqa: BLE001 — the provider below is the fallback
        logger.warning(f'[CPR options] index minutes from the store failed: {e}')
        out, stored_last = {}, None
    if stored_last is not None and stored_last >= last:
        return out
    if stored_last is not None:
        first = max(first, stored_last)
        out.pop(stored_last.isoformat(), None)              # re-read whole: the store's last day may be partial
    try:
        adapter = mc.algo_provider()
    except Exception:       # noqa: BLE001
        adapter = mc.provider()
    fy_symbol = mc.resolve_symbol(symbol)
    start = first
    while start <= last:
        end = min(start + timedelta(days=cpr_bt.INTRADAY_CHUNK_DAYS - 1), last)
        raw = adapter.historical_data(fy_symbol, start.isoformat(), end.isoformat(),
                                      'minute', use_cache=True, cache_ttl=3600.0)
        out.update(cpr_bt._intraday_by_session(raw))
        start = end + timedelta(days=1)
    return out


def _option_minutes_fetcher(symbol: str) -> Callable[[Dict[str, Any], date], List[Dict[str, Any]]]:
    from trading_app.service.provider_logic import get_icici_adapter
    adapter = get_icici_adapter()
    if adapter is None or not hasattr(adapter, 'historical_option_minutes'):
        raise OptionDataUnavailable(
            'Option premiums come from the contract\'s own recorded candles, and Breeze is '
            'the only provider that serves an expired contract. Log in at /auth/login/icici.')
    exchange = OPTION_EXCHANGE.get(symbol.upper(), 'NFO')

    def fetch(con: Dict[str, Any], day: date) -> List[Dict[str, Any]]:
        raw = adapter.historical_option_minutes(
            symbol, date.fromisoformat(con['expiry']), con['strike'], con['option_type'],
            day, exchange_code=exchange)
        bars = cpr_bt._intraday_by_session(raw).get(day.isoformat(), [])
        if not bars:
            # Breeze serves a session's option candles only after the day has been
            # loaded overnight — the Update button's own session (2026-10-14) always
            # came back empty, "no option bars" — and it serves NO contract that is still
            # listed (2026-10-19: every 20-Oct weekly came back empty, the 13-Oct one
            # had 375 bars). A listed contract is on Zerodha, then Fyers (capped at
            # 5,000 requests a day, spent by the afternoon).
            bars = _kite_option_minutes(symbol, con, day, exchange) or _fyers_option_minutes(symbol, con, day, exchange)
        return bars

    return fetch


_kite_nfo: Dict[str, Any] = {}


def _kite_option_minutes(symbol: str, con: Dict[str, Any], day: date,
                         exchange: str) -> List[Dict[str, Any]]:
    """One session of 1-minute bars for a still-listed option from Zerodha, or
    [] when Kite has no such contract (expired — Breeze serves those) or no
    history. Breeze serves a contract only after it has expired, so for the
    weekly still running Kite is the source that needs no daily quota."""
    try:
        from trading_app.service.provider_logic import get_kite, _kite_serves_history
        kite = get_kite(instance=1)
        if kite is None or not _kite_serves_history(kite):
            return []
        hit = _kite_nfo.get(exchange)
        if not hit or time.time() - hit[0] > 6 * 3600:
            hit = (time.time(), kite.instruments(exchange))
            _kite_nfo[exchange] = hit
        expiry = date.fromisoformat(con['expiry'])
        want, root = str(con['option_type']).upper(), symbol.upper()
        token = next((i['instrument_token'] for i in hit[1]
                      if i.get('instrument_type') == want and i.get('expiry') == expiry
                      and str(i.get('name') or '').strip().upper() == root
                      and abs(float(i.get('strike', -1)) - float(con['strike'])) < 0.5), None)
        if not token:
            return []
        raw = kite.historical_data(token, f'{day.isoformat()} 09:00:00', f'{day.isoformat()} 15:35:00', 'minute')
        rows = [{**r, 'date': r['date'].replace(tzinfo=None) if getattr(r.get('date'), 'tzinfo', None) else r['date']}
                for r in raw or []]
        return cpr_bt._intraday_by_session(rows).get(day.isoformat(), [])
    except Exception as e:      # noqa: BLE001 — a fallback must never break the grid
        logger.warning(f"[CPR options] Kite fallback {con.get('strike')}{con.get('option_type')} {day}: {e}")
        return []


def _fyers_option_minutes(symbol: str, con: Dict[str, Any], day: date,
                          exchange: str) -> List[Dict[str, Any]]:
    """One session of 1-minute bars for a still-listed option from Fyers, or
    [] when Fyers has no such contract (expired) or no session."""
    try:
        from trading_app.service.provider_logic import get_fyers_adapter
        fy = get_fyers_adapter()
        if fy is None:
            return []
        expiry = date.fromisoformat(con['expiry'])
        want = str(con['option_type']).upper()
        root = symbol.upper()
        token = None
        for inst in fy.instruments(exchange):
            if (str(inst.get('instrument_type') or '').upper() == want
                    and inst.get('expiry') == expiry
                    and abs(float(inst.get('strike', -1)) - float(con['strike'])) < 0.5
                    and (str(inst.get('name') or '').strip().upper() == root
                         or (str(inst.get('tradingsymbol') or '').upper().startswith(root)
                             and not str(inst.get('tradingsymbol') or '').upper()[len(root):][:1].isalpha()))):
                token = inst.get('instrument_token')
                break
        if not token:
            return []
        raw = fy.historical_data(token, day.isoformat(), day.isoformat(), 'minute', use_cache=False)
        return cpr_bt._intraday_by_session(raw).get(day.isoformat(), [])
    except Exception as e:      # noqa: BLE001 — a fallback must never break the grid
        logger.warning(f"[CPR options] Fyers fallback {con.get('strike')}{con.get('option_type')} {day}: {e}")
        return []


def legs_for(symbol: str, rows: List[Dict[str, Any]], trading_days: List[date],
             spot: Dict[str, List[Dict[str, Any]]],
             fetch_option: Callable[[Dict[str, Any], date], List[Dict[str, Any]]],
             premium: Optional[float] = None) -> Dict[str, Any]:
    """One option leg per trade, keyed by date -> [leg per trade in sheet
    order], plus the tally. ATM strikes, or with `premium` the strike whose
    premium at the entry minute was nearest it. Pure given the fetchers."""
    lot = lot_size(symbol)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    pnl = 0.0
    wins = booked = missing = 0
    for r in rows:
        day = date.fromisoformat(r['date'])
        legs = []
        for t in r['trades']:
            tm = t['manual']
            con = contract(symbol, day, tm.get('trade'), tm.get('entry'), trading_days)
            if con is None:
                legs.append({'error': 'no contract', 'result': None, 'pnl': None})
                missing += 1
                continue
            day_spot = spot.get(r['date']) or []
            sim = replay(tm, day_spot) if day_spot else None
            atm = con['strike']
            if premium and sim and sim['entry_time']:
                con, opt, _ = pick_strike(symbol, con, day, sim['entry_time'], premium, fetch_option)
            else:
                try:
                    opt = fetch_option(con, day)
                except Exception as e:      # noqa: BLE001 — one dead contract, not a dead grid
                    logger.warning(f"[CPR options] {r['date']} {con['strike']}{con['option_type']}: {e}")
                    opt = []
            leg = option_leg(tm, con, day_spot, opt, lot, sim)
            leg['atm'] = atm
            legs.append(leg)
            if leg['pnl'] is not None:
                pnl += leg['pnl']
                booked += 1
                wins += leg['pnl'] > 0
            elif leg.get('error'):
                missing += 1
        by_date[r['date']] = legs
    return {'legs': by_date, 'premium': premium,
            'summary': {'pnl': round(pnl, 2), 'pnl_lot': round(pnl * lot - BROKERAGE * booked, 2), 'lot': lot,
                        'trades': booked, 'wins': wins, 'missing': missing}}


def options(symbol: str, premium: Optional[float] = None, year: Optional[str] = None) -> Dict[str, Any]:
    """The CPR Logic page's second call: the option leg behind every trade
    of the sheet, computed on the same rows /cpr-backtest serves. `premium`
    picks strikes by entry premium instead of ATM. `year` ('2025') prices only
    that year's trades: the sheet now spans 2020 on, a trade costs Breeze
    requests the first time, and that budget is shared with the live algos —
    so the page asks for the year on screen, and for every year only when the
    person picks "All years"."""
    doc = cpr_bt.load_manual(symbol)
    rows = doc.get('rows') or []
    traded = [r for r in cpr_bt.sessions_from(rows) if r['trades'] and (not year or r['date'].startswith(year))]
    if not traded:
        return {'success': True, 'symbol': symbol.upper(), 'legs': {}, 'premium': premium, 'year': year or None,
                'summary': {'pnl': 0.0, 'pnl_lot': 0.0, 'lot': lot_size(symbol),
                            'trades': 0, 'wins': 0, 'missing': 0}}
    today = datetime.now(cpr_bt.IST).date()
    cache = _load_legs_cache(symbol)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    todo: List[Dict[str, Any]] = []
    for r in traded:
        keys = [_leg_key(premium, r['date'], i, t) for i, t in enumerate(r['trades'])]
        saved = [cache['legs'].get(k) for k in keys]
        # A settled session whose every leg was read before, for these very trade numbers, is
        # taken from the file — no Breeze, no index history, no strike walk.
        if date.fromisoformat(r['date']) < today and all(_leg_is_fresh(e) for e in saved):
            by_date[r['date']] = [e['leg'] for e in saved]
        else:
            todo.append(r)
    if todo:
        fetch_option = _option_minutes_fetcher(symbol)     # fail before any fetch
        todo_dates = sorted(date.fromisoformat(r['date']) for r in todo)
        trading_days = _trading_days(symbol, todo_dates[0], todo_dates[-1])
        spot = _spot_minutes_for(symbol, todo_dates)
        rows_in = [{'date': r['date'], 'trades': [{'manual': t} for t in r['trades']]} for r in todo]
        out = legs_for(symbol, rows_in, trading_days, spot, fetch_option, premium)
        by_date.update(out['legs'])
        _save_legs(symbol, cache, todo, out['legs'], premium, today)
    lot = lot_size(symbol)
    pnl = 0.0
    wins = booked = missing = 0
    for legs in by_date.values():
        for leg in legs:
            if leg.get('pnl') is not None:
                pnl += leg['pnl']
                booked += 1
                wins += leg['pnl'] > 0
            elif leg.get('error'):
                missing += 1
    logger.info(f"[CPR options] {symbol.upper()} {'≈' + str(premium) if premium else 'ATM'}{' ' + year if year else ''}: "
                f"{len(traded) - len(todo)} sessions from the legs file, {len(todo)} read; {booked} legs priced, {missing} without data")
    return {'success': True, 'symbol': symbol.upper(), 'year': year or None, 'legs': by_date, 'premium': premium,
            'summary': {'pnl': round(pnl, 2), 'pnl_lot': round(pnl * lot - BROKERAGE * booked, 2), 'lot': lot,
                        'trades': booked, 'wins': wins, 'missing': missing}}


# ── The legs file ─────────────────────────────────────────────────────────
# The option minute bars are already disk-cached, but the leg built on them was
# not: every page open re-fetched the index's 1-minute history for the whole
# range, re-walked the strike ladder and re-ran every replay. A priced leg of a
# settled session never changes while its trade is the same, so it is kept in
# .cache/cpr_option_legs_<SYMBOL>.json, keyed by premium choice + date + trade
# number + the trade's own numbers (a re-written trade is a different key), and
# a read of a year the file covers touches neither Breeze nor Fyers. Today's
# session is never saved (its option is still moving). A leg that found no
# option data is kept too, but only for LEG_RETRY_DAYS, so a contract Breeze
# does not hold is not asked for on every page open.

def _legs_cache_path(symbol: str) -> str:
    from trading_app.service import icici_data_service as ids
    return os.path.join(ids._SLICE_CACHE_DIR, f'cpr_option_legs_{symbol.upper()}.json')


def _leg_key(premium: Optional[float], day: str, i: int, t: Dict[str, Any]) -> str:
    return (f"{int(premium) if premium else 'ATM'}|{day}|{i}|{t.get('trade')}|{t.get('entry')}|{t.get('target')}|"
            f"{t.get('sl')}|{t.get('setup_time')}")


def _leg_is_fresh(e: Optional[Dict[str, Any]]) -> bool:
    if not e or 'leg' not in e:
        return False
    leg = e['leg']
    if leg.get('pnl') is not None or (leg.get('result') in (None, 'No fill') and not leg.get('error')):
        return True
    saved = e.get('saved', 0)
    try:
        # Breeze serves no contract that is still listed: an error saved before the expiry
        # passed is asked again, on the corrected expiry if the calendar had it wrong.
        if leg.get('error') and leg.get('expiry') and datetime.fromtimestamp(saved, cpr_bt.IST).date() <= date.fromisoformat(leg['expiry']):
            return False
    except (TypeError, ValueError):
        pass
    return time.time() - saved < LEG_RETRY_DAYS * 86400


def _load_legs_cache(symbol: str) -> Dict[str, Any]:
    with _LEGS_LOCK:
        try:
            with open(_legs_cache_path(symbol)) as fh:
                data = json.load(fh)
            if data.get('version') == LEGS_CACHE_VERSION and isinstance(data.get('legs'), dict):
                return data
        except (OSError, ValueError):
            pass
    return {'version': LEGS_CACHE_VERSION, 'legs': {}}


def _save_legs(symbol: str, cache: Dict[str, Any], rows: List[Dict[str, Any]],
               legs_by_date: Dict[str, List[Dict[str, Any]]], premium: Optional[float], today: date) -> None:
    now = time.time()
    for r in rows:
        if date.fromisoformat(r['date']) >= today:
            continue                                   # today's option is still moving
        legs = legs_by_date.get(r['date']) or []
        for i, t in enumerate(r['trades']):
            # "no contract" is not kept: it costs nothing to work out again, and a short
            # trading-day list once made it look permanent for every trade past its end.
            if i < len(legs) and legs[i].get('error') != 'no contract':
                cache['legs'][_leg_key(premium, r['date'], i, t)] = {'leg': legs[i], 'saved': now}
    path = _legs_cache_path(symbol)
    with _LEGS_LOCK:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w') as fh:
                json.dump(cache, fh, separators=(',', ':'))
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning(f"[CPR options] legs file not written: {exc}")


def _spot_minutes_for(symbol: str, days: List[date]) -> Dict[str, List[Dict[str, Any]]]:
    """1-minute index bars for just the sessions asked for: nearby days are read
    as one window (at most ~85 days across), far-apart ones as their own."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    days = sorted(days)
    start = prev = days[0]
    for d in days[1:] + [None]:
        if d is None or (d - prev).days > 30 or (d - start).days > 85:
            out.update(_spot_minutes(symbol, start, prev))
            start = d
        prev = d if d is not None else prev
    return out


# ── Pricing an arbitrary list of trades ──────────────────────────────────
# The CPR sheet is not the only thing that wants "what would the option leg
# have done": the Replay page's ΔX Trades panel asks the same question of the
# signals the footprint engine found. Everything it needs is already here —
# the contract, the strike walk, the Breeze fetcher and its disk cache — so
# this is a second entry point onto the same machinery, not a second copy.
#
# The difference from options() above is that the caller brings its own
# trades. It hands over entry and exit MINUTES that it already knows (the ΔX
# engine has decided them on the index), and this prices the premium at those
# two minutes. There is no index replay here and no target/SL placement: the
# trade has already happened, this only says what the option did over it.
#
# Cost: pick_strike walks the ladder, so a trade costs a handful of Breeze
# requests the first time and nothing afterwards (settled sessions are disk
# cached). The caller is expected to ask on demand, not on every redraw, and
# MAX_TRADES is the backstop.
MAX_TRADES = 60               # per request — the Breeze budget is shared with the live algos


def _index_minutes(symbol: str, days: List[date], source: str) -> Dict[str, List[Dict[str, Any]]]:
    """1-minute bars of the series the caller's CHART is on, by session — the
    path the crossing search below walks. `source='future'` charts the
    current-expiry future, which sits a basis above the index, so a level
    taken off a futures chart has to be matched against futures minutes."""
    from trading_app.service import multichart_service as mc
    adapter = mc.provider()
    chart_sym, _ = mc.chart_symbol(adapter, symbol, source)
    out: Dict[str, List[Dict[str, Any]]] = {}
    first, last = days[0], days[-1]
    start = first
    while start <= last:
        end = min(start + timedelta(days=cpr_bt.INTRADAY_CHUNK_DAYS - 1), last)
        raw = adapter.historical_data(chart_sym, start.isoformat(), end.isoformat(),
                                      'minute', use_cache=True, cache_ttl=3600.0)
        out.update(cpr_bt._intraday_by_session(raw))
        start = end + timedelta(days=1)
    return out


def crossing_minute(minutes: List[Dict[str, Any]], bar_time: str, span: int,
                    price: Optional[float]) -> Optional[str]:
    """The minute inside `bar_time`'s own bar at which the series traded
    `price` — the minute the trade actually happened.

    This exists because of a real and SYSTEMATIC error. A ΔX trade is decided
    on, say, 5-minute bars: it enters when the index breaks a level somewhere
    inside the trigger bar and leaves when the stop or target is hit somewhere
    inside the exit bar. Pricing its option at those bars' STAMPS — their first
    minute — buys before the breakout and sells before the hit. Both errors
    favour the option leg, and over a few hundred trades they turned a +917
    index-point session set into +1,818 premium points. Measured on 30 Sep:
    the 12:20 BUY priced 12:30→12:55 came out +31.95, and the same leg over
    12:34→12:59 came out +15.90.

    Falls back to the LAST minute of the bar when the price is never touched in
    it — which is what a close-based exit (the 15:15 cut-off, a session end)
    is — and to the stamp when there are no minutes at all.
    """
    if not bar_time:
        return None
    window = [b for b in minutes
              if bar_time <= b['time'] < _plus_minutes(bar_time, max(1, span))]
    if not window:
        return bar_time
    if price is not None:
        for b in window:
            if b['low'] - 1e-9 <= price <= b['high'] + 1e-9:
                return b['time']
    return window[-1]['time']


def _plus_minutes(hhmm: str, mins: int) -> str:
    t = datetime.strptime(hhmm, '%H:%M') + timedelta(minutes=mins)
    return t.strftime('%H:%M')


def _premium_stop(bars: List[Dict[str, Any]], entry: float, t_in: str, t_out: str,
                  sl_pct: float, tsl_x: float, tsl_y: float,
                  carry: Optional[Dict[str, float]] = None) -> Optional[Dict[str, Any]]:
    """First bar after `t_in` and up to `t_out` whose low reaches the stop — see legs_at.
    `carry` lets a position held over several sessions keep its (trailed) stop from one
    session's call to the next: it is read at the start and written back at the end."""
    def tick_up(v: float) -> float:        # Quantman's stop prices sit on the 0.05 tick, rounded UP
        return math.ceil(round(v / 0.05, 6)) * 0.05

    base = tick_up(entry * (1.0 - sl_pct / 100.0))
    stop = max(base, carry['stop']) if carry and 'stop' in carry else base
    try:
        for b in bars:
            if b['time'] <= t_in:
                continue
            if b['time'] > t_out:
                break
            if b['low'] <= stop:
                return {'price': round(min(b['open'], stop), 2), 'time': b['time'],
                        'kind': 'Trailing SL' if stop > base else 'Stop Loss'}
            if tsl_x > 0 and tsl_y > 0:
                steps = int(((b['high'] / entry - 1.0) * 100.0) // tsl_x)
                if steps > 0:
                    stop = max(stop, tick_up(base + steps * tsl_y / 100.0 * entry))
        return None
    finally:
        if carry is not None:
            carry['stop'] = stop


def legs_at(symbol: str, trades: List[Dict[str, Any]],
            premium: Optional[float] = None, span_min: int = 1,
            source: str = 'spot', trading_days: Optional[List[date]] = None,
            sl_pct: float = 0.0, tsl_x: float = 0.0, tsl_y: float = 0.0) -> Dict[str, Any]:
    """Price one option leg per trade in `trades`.

    `sl_pct` / `tsl_x` / `tsl_y` put a stop on the PREMIUM (Quantman's leg stop-loss and
    trailing SL): the stop rests `sl_pct` % under the entry premium and, for every
    `tsl_x` % the premium has risen from entry, moves up `tsl_y` % of entry. A bar's low
    through the stop sells at the stop (at the open if it gapped past) and the leg is
    marked `stop`; the stop is tested before the bar's high can lift it. 0 = no stop.

    Each trade: {'date': 'YYYY-MM-DD', 'side': 'BUY'|'SELL', 'level': float,
                 'entry_time': 'HH:MM', 'exit_time': 'HH:MM' | None,
                 'exit_price': float | None}.

    `entry_time` / `exit_time` are the caller's BAR stamps and `span_min` is
    how long one of its bars is, so the premium can be taken at the minute the
    trade actually happened rather than at the start of the bar it happened in
    — see crossing_minute(), which is there to kill a look-ahead bias, not to
    be precise for its own sake.

    A BUY is a CALL and a SELL is a PUT, both BOUGHT — the leg is long premium
    either way, so its P&L is exit minus entry whichever way the index trade
    faced. `level` is the index price the strike is measured from (ATM), and
    `premium` then walks the ladder to the strike nearest that premium at the
    entry minute.
    """
    if not trades:
        return {'success': True, 'symbol': symbol.upper(), 'legs': [], 'premium': premium,
                'lot': lot_size(symbol)}
    if len(trades) > MAX_TRADES:
        raise ValueError(f'{len(trades)} trades asked for; {MAX_TRADES} is the limit per request')

    fetch_option = _option_minutes_fetcher(symbol)        # fail before any fetch
    days = sorted({date.fromisoformat(t['date']) for t in trades})
    # A caller pricing many chunks reads the calendar ONCE and hands it in: every
    # call otherwise re-reads it from Fyers, and parallel chunks tripped its
    # 50-requests-a-minute cap, leaving whole stretches of trades unpriced.
    if trading_days is None:
        trading_days = _trading_days(symbol, days[0], days[-1])
    lot = lot_size(symbol)
    # The chart's own 1-minute path, for placing each leg at the minute the
    # trade happened. One fetch for the whole span, cached like everything
    # else here; a failure is not fatal — the legs are then priced at their
    # bar stamps, the way they were before, and say so.
    minutes: Dict[str, List[Dict[str, Any]]] = {}
    align_error = None
    if span_min > 1:
        try:
            minutes = _index_minutes(symbol, days, source)
        except Exception as e:                            # noqa: BLE001
            align_error = str(e)
            logger.warning(f"[ΔX options] no {source} minutes for the crossing search: {e}")

    legs: List[Dict[str, Any]] = []
    priced = 0
    for t in trades:
        leg: Dict[str, Any] = {'key': t.get('key'), 'date': t['date'], 'strike': None,
                               'option_type': None, 'expiry': None, 'entry': None,
                               'exit': None, 'pnl': None, 'pnl_inr': None, 'error': None,
                               'entry_time': None, 'exit_time': None}
        try:
            day = date.fromisoformat(t['date'])
            con = contract(symbol, day, (t.get('side') or '').upper(), t.get('level'), trading_days)
            if con is None:
                leg['error'] = 'no expiry for that session'
                legs.append(leg)
                continue
            # The minutes the trade really entered and left on.
            mins = minutes.get(t['date']) or []
            t_in = crossing_minute(mins, t.get('entry_time'), span_min,
                                   t.get('level')) if mins else t.get('entry_time')
            t_out = crossing_minute(mins, t.get('exit_time'), span_min,
                                    t.get('exit_price')) if mins else t.get('exit_time')
            leg.update(entry_time=t_in, exit_time=t_out)
            if premium:
                con, bars, _ = pick_strike(symbol, con, day, t_in, premium, fetch_option)
            else:
                bars = fetch_option(con, day)
            leg.update(strike=con['strike'], option_type=con['option_type'], expiry=con['expiry'])
            b_in = bar_at(bars, t_in)
            b_out = bar_at(bars, t_out)
            def _px(b, mid):
                # `mid`: the trade filled somewhere inside this minute — its open/close average
                return (b['open'] + b['close']) / 2 if mid and b.get('open') is not None else b['close']
            if b_in:
                leg['entry'] = round(_px(b_in, t.get('entry_mid')), 2)
            if b_out:
                leg['exit'] = round(_px(b_out, t.get('exit_mid')), 2)
            if sl_pct > 0 and leg['entry'] is not None and t_out:
                hit = _premium_stop(bars, leg['entry'], t_in, t_out, sl_pct, tsl_x, tsl_y)
                if hit:
                    leg.update(exit=hit['price'], exit_time=hit['time'], stop=hit['kind'])
            if leg['entry'] is not None and leg['exit'] is not None:
                # Long premium on both sides: a CALL for a BUY, a PUT for a
                # SELL. Exit minus entry is the move either way.
                leg['pnl'] = round(leg['exit'] - leg['entry'], 2)
                leg['pnl_inr'] = round(leg['pnl'] * lot - BROKERAGE, 2)
                priced += 1
            elif not bars:
                leg['error'] = 'no candles for that contract'
            else:
                leg['error'] = 'no candle at that minute'
        except OptionDataUnavailable:
            raise
        except Exception as e:                            # noqa: BLE001 — one dead strike is not the request
            logger.warning(f"[ΔX options] {t.get('date')} {t.get('entry_time')}: {e}")
            leg['error'] = str(e)
        legs.append(leg)

    logger.info(f"[ΔX options] {symbol.upper()} {'≈' + str(premium) if premium else 'ATM'}: "
                f"{priced}/{len(trades)} legs priced")
    return {'success': True, 'symbol': symbol.upper(), 'legs': legs,
            'premium': premium, 'lot': lot,
            'aligned': bool(minutes) or span_min <= 1,
            'align_error': align_error}
