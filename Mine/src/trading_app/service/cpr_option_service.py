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

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from trading_app.app.utils.logger import logger
from trading_app.service import cpr_backtest_service as cpr_bt
from trading_app.service.expiry_calendar import expiry_on_or_after

STRIKE_STEP = {'NIFTY': 50, 'BANKNIFTY': 100, 'FINNIFTY': 50, 'MIDCPNIFTY': 25, 'SENSEX': 100}
LOT_SIZE = {'NIFTY': 65, 'BANKNIFTY': 30, 'FINNIFTY': 65, 'MIDCPNIFTY': 120, 'SENSEX': 20}
EXPIRY_CADENCE = {'NIFTY': 'weekly', 'SENSEX': 'weekly'}      # the rest are monthly-only
OPTION_EXCHANGE = {'SENSEX': 'BFO'}

MIN_PREMIUM = 0.05            # an estimate never goes below the tick
PREMIUM_CHOICES = (150, 200, 250, 300)   # the page's premium targets
PICK_DELTA_GUESS = 0.6        # premium change per strike step, as a share of the step, for the first jump
PICK_MAX_STRIKES = 10         # strikes read per trade before the walk gives up
DELTA_MIN_POINTS = 20         # minutes needed before a slope is trusted
SETUP_BAR_MINUTES = 5         # the sheet's setup candle is a 5-minute bar


class OptionDataUnavailable(Exception):
    """No Breeze session to read option candles from."""


def strike_step(symbol: str) -> float:
    return STRIKE_STEP.get((symbol or '').upper(), 50)


def lot_size(symbol: str) -> int:
    return LOT_SIZE.get((symbol or '').upper(), 65)


def contract(symbol: str, day: date, trade: str, entry: float,
             trading_days: List[date]) -> Optional[Dict[str, Any]]:
    """The contract the trade would be placed on: ATM to the entry level,
    CALL for a BUY / PUT for a SELL, the nearest expiry on or after the day."""
    if trade not in ('BUY', 'SELL') or entry is None:
        return None
    step = strike_step(symbol)
    expiry = expiry_on_or_after(day, trading_days, EXPIRY_CADENCE.get(symbol.upper(), 'monthly'))
    if expiry is None:
        return None
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
        cur = atm + (itm if jump > 0 else -itm) * max(1, min(abs(jump), PICK_MAX_STRIKES - 2)) * step
        if price(cur) is None:
            cur = atm
    # One strike at a time towards the target until it sits between two neighbours.
    while len(seen) < PICK_MAX_STRIKES:
        pc = price(cur)
        if pc is None:
            break
        towards = itm if premium > pc else -itm
        nxt = cur + towards * step
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
                               entry_time=None, exit_time=None, result=None, pnl=None,
                               pnl_lot=None, delta=None, estimated=[], error=None)
    if not spot:
        leg['error'] = 'no 1-minute index bars'
        return leg
    if not opt:
        leg['error'] = 'no option bars'
        return leg
    sim = sim or replay(tm, spot)
    leg.update(result=sim['result'], entry_time=sim['entry_time'], exit_time=sim['exit_time'])
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
    elif sim['result'] == 'EOD':
        exit_bar = bar_at(opt, sim['exit_time'])
        # The square-off is at that bar's open, as the index replay's is.
        exit_px = exit_bar['open'] if exit_bar and exit_bar['time'] == sim['exit_time'] \
            else (exit_bar['close'] if exit_bar else None)
    leg['exit'] = exit_px

    # The level that closed the trade is a real print; the other is the
    # entry premium moved by the day's delta, and says so.
    leg['target'] = exit_px if sim['result'] == 'Target' else _est(entry_px, delta, tm['entry'], tm['target'])
    leg['sl'] = exit_px if sim['result'] == 'SL' else _est(entry_px, delta, tm['entry'], tm['sl'])
    if sim['result'] != 'Target' and leg['target'] is not None:
        leg['estimated'].append('target')
    if sim['result'] != 'SL' and leg['sl'] is not None:
        leg['estimated'].append('sl')

    if exit_px is not None:
        leg['pnl'] = round(exit_px - entry_px, 2)          # always a long option
        leg['pnl_lot'] = round(leg['pnl'] * lot, 2)
    return leg


def _trading_days(symbol: str, first: date, last: date) -> List[date]:
    """The sessions around the sheet, for the expiry calendar to snap
    holidays against — a week either side is enough."""
    from trading_app.service import multichart_service as mc
    adapter = mc.provider()
    raw = adapter.historical_data(mc.resolve_symbol(symbol), (first - timedelta(days=7)).isoformat(),
                                  min(last + timedelta(days=7), date.today()).isoformat(),
                                  'day', use_cache=True, cache_ttl=3600.0)
    return [b['date'] for b in cpr_bt._daily_rows(raw)]


def _spot_minutes(symbol: str, first: date, last: date) -> Dict[str, List[Dict[str, Any]]]:
    """1-minute index bars by session, from the charts' provider."""
    from trading_app.service import multichart_service as mc
    adapter = mc.provider()
    fy_symbol = mc.resolve_symbol(symbol)
    out: Dict[str, List[Dict[str, Any]]] = {}
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
        return cpr_bt._intraday_by_session(raw).get(day.isoformat(), [])

    return fetch


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
            'summary': {'pnl': round(pnl, 2), 'pnl_lot': round(pnl * lot, 2), 'lot': lot,
                        'trades': booked, 'wins': wins, 'missing': missing}}


def options(symbol: str, premium: Optional[float] = None) -> Dict[str, Any]:
    """The CPR Logic page's second call: the option leg behind every trade
    of the sheet, computed on the same rows /cpr-backtest serves. `premium`
    picks strikes by entry premium instead of ATM."""
    doc = cpr_bt.load_manual(symbol)
    rows = doc.get('rows') or []
    traded = [r for r in cpr_bt.sessions_from(rows) if r['trades']]
    if not traded:
        return {'success': True, 'symbol': symbol.upper(), 'legs': {}, 'premium': premium,
                'summary': {'pnl': 0.0, 'pnl_lot': 0.0, 'lot': lot_size(symbol),
                            'trades': 0, 'wins': 0, 'missing': 0}}
    fetch_option = _option_minutes_fetcher(symbol)     # fail before any fetch
    dates = sorted(date.fromisoformat(r['date']) for r in traded)
    trading_days = _trading_days(symbol, dates[0], dates[-1])
    spot = _spot_minutes(symbol, dates[0], dates[-1])
    rows_in = [{'date': r['date'], 'trades': [{'manual': t} for t in r['trades']]} for r in traded]
    out = legs_for(symbol, rows_in, trading_days, spot, fetch_option, premium)
    logger.info(f"[CPR options] {symbol.upper()} {'≈' + str(premium) if premium else 'ATM'}: "
                f"{out['summary']['trades']} legs priced, {out['summary']['missing']} without data")
    return {'success': True, 'symbol': symbol.upper(), **out}
