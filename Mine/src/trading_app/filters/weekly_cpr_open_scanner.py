"""Weekly CPR breakout scanner — opened inside the weekly CPR, broke out in hour one.

For the selected session date D, a symbol matches when ALL hold:

  1. D's OPEN is INSIDE the weekly CPR — BC <= open <= TC;
  2. D's FIRST 1-HOUR candle (09:15–10:15, two 30-minute bars) CLOSES OUTSIDE
     the weekly CPR — above TC or below BC, not on it;
  3. that candle is a DIRECTION candle agreeing with the break — a close
     above TC must be a BUY candle (close > open), a close below BC a SELL
     candle (close < open). A red hour that still closed above TC, or a
     green one below BC, is fading its own break and is out;
  4. and not near the weekly Camarilla level it is heading for: a close above
     TC must sit more than CAM_NEAR_PCT (0.5 %) below R3, a close below BC
     more than 0.5 % above S3. A close already at or past R3 / S3 is out too
     — the move the setup trades towards has already happened.

The weekly CPR and Camarilla are the ones IN FORCE on D: built from the high /
low / close of the last completed Mon..Sun week before D's week (R3/S3 =
C ± (H − L) × 1.1/4, as the chart popup draws them).

On 2026-10-12 the rule also had the previous close inside / within 0.5 % of
the CPR and the CPR very narrow (at most 0.5× its own 10-week average); both
were removed on request (the open-inside condition was removed with them and
then put back). The previous close and the CPR's width against its normal
(`w_ratio`) are still on each row, as information only.

The scan reads every symbol; select_matches() applies the conditions. Before
10:15 on the session day the first hour has not closed: such rows are
`pending` and never match.

Data comes from FYERS, not the session's DATA_PROVIDER: the route hands in a
CPRFilterService built on the Fyers adapter. It was ICICI Breeze until the
evening of 2026-10-12, when Breeze's equity candles turned out to carry stray
highs/lows — for the week of 10-05 14 of 20 large caps had a weekly high or
low more than 0.3 % off Fyers (RELIANCE / INFY / TCS / AXISBANK ~2.5 %; TITAN's
low 4239.7 vs 4335.6, TVSMOTOR's high 4133.3 vs 4043.6) while closes agreed.
CPR and Camarilla are built from those extremes, so every level moved and
the scan disagreed with the chart popup, which Fyers matches.

The history is read through the adapter directly rather than
CPRFilterService.get_hist_data(), whose cache is keyed without the provider
and never expires within a day — a window cached before 09:15 would have no
bar for today and silently drop every symbol. The session itself comes from
30-minute bars (see get_weekly_cpr_open_row): two requests per symbol, inside
Fyers' app-wide 200-a-minute budget, so a scan is a couple of minutes — the
route runs it as a background job and keeps the answer for the rest of the
day.

Universe is the Camarilla / Narrow CPR one: F&O futures stocks plus the indices.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

import pandas as pd

from trading_app.service.cpr_service import CPRService, cpr_width_pct

if TYPE_CHECKING:
    from trading_app.filters.cpr_filter import CPRFilterService

logger = logging.getLogger(__name__)

# How close to R3 / S3 the first hour may close and still leave room, in percent.
CAM_NEAR_PCT = 0.5

# Weekly CPRs the in-force one's width is compared with (the grid's ×avg
# column, information only), and the fewest that make an average.
_CONTEXT_WEEKS = 10
_MIN_CONTEXT = 4

# Camarilla's R3/S3 multiplier — CAMARILLA_R3 in candle-modal.js.
_CAMARILLA_R3 = 1.1 / 4

# Calendar days of daily history read per symbol: the in-force week plus ten
# weeks for the ×avg column is ~11 weeks; holidays and the week D is in need the rest.
# Still one request.
_LOOKBACK_DAYS = 120

# The first hour is the 09:15 and 09:45 thirty-minute bars; it has closed at 10:15.
_FIRST_HOUR_BARS = ((9, 15), (9, 45))
_FIRST_HOUR_CLOSE = (10, 15)

# The Fyers adapter paces itself app-wide (8 req/s, 200 a minute), so more
# threads only queue in its pacer — a few keep a slow response from stalling
# the next request.
_WORKERS = 4


def _weekly_cpr(df: pd.DataFrame, session: pd.Timestamp) -> Optional[Dict]:
    """The weekly CPR + Camarilla in force on `session`, from the last week
    with bars that ended before `session`'s week began, and how its width
    compares with the weekly CPRs before it. None without such a week."""
    week_start = (session - pd.Timedelta(days=session.weekday())).normalize()
    prior = df[df.index < week_start]
    if prior.empty:
        return None
    weeks = prior.groupby(prior.index.to_period('W-SUN')).agg(
        {'high': 'max', 'low': 'min', 'close': 'last'})
    widths = [cpr_width_pct(w.high, w.low, w.close) for w in weeks.itertuples()]
    last = weeks.iloc[-1]
    high, low, close = float(last['high']), float(last['low']), float(last['close'])
    pp, bc, tc = CPRService.calculate_cpr(high, low, close)

    width = widths[-1]
    context = [w for w in widths[-1 - _CONTEXT_WEEKS:-1] if w is not None]
    avg = sum(context) / len(context) if len(context) >= _MIN_CONTEXT else None
    return {'week': str(weeks.index[-1]), 'pp': pp, 'bc': bc, 'tc': tc,
            'r3': close + (high - low) * _CAMARILLA_R3,
            's3': close - (high - low) * _CAMARILLA_R3,
            'w_high': high, 'w_low': low, 'w_close': close,
            'width_pct': width, 'avg_width_pct': avg,
            'ratio': (width / avg) if (avg and width is not None) else None}


def _first_hour(intraday: Optional[pd.DataFrame], day: pd.Timestamp,
                now: datetime) -> Optional[Dict]:
    """D's first 1-hour candle from its 30-minute bars, or None when it has
    not closed yet (before 10:15 on the day) or its bars are missing."""
    if now < day.to_pydatetime().replace(hour=_FIRST_HOUR_CLOSE[0],
                                         minute=_FIRST_HOUR_CLOSE[1]):
        return None
    if intraday is None or intraday.empty:
        return None
    stamps = [day + pd.Timedelta(hours=h, minutes=m) for h, m in _FIRST_HOUR_BARS]
    bars = intraday[intraday.index.isin(stamps)].sort_index()
    if len(bars) != len(stamps):
        return None
    return {'open': float(bars['open'].iloc[0]), 'high': float(bars['high'].max()),
            'low': float(bars['low'].min()), 'close': float(bars['close'].iloc[-1])}


def _position(price: float, bc: float, tc: float) -> str:
    """'Inside', or which side of the band and by how much, for the grid."""
    if bc <= price <= tc:
        return 'Inside'
    if price > tc:
        return f'Above +{(price - tc) / tc * 100:.2f}%'
    return f'Below −{(bc - price) / bc * 100:.2f}%'


def evaluate(df: pd.DataFrame, session_date,
             intraday: Optional[pd.DataFrame] = None,
             now: Optional[datetime] = None) -> Optional[Dict]:
    """Read one symbol's daily bars (and D's 30-minute bars) for `session_date`.

    Returns the levels and every verdict, matched or not, or None when the
    bars cannot answer — no bar on the session date (holiday, not yet open,
    stale feed), no earlier bar, or no completed week before it.
    select_matches() applies the conditions.
    """
    if df is None or df.empty:
        return None
    df = df.sort_index()
    day = pd.Timestamp(session_date).normalize()
    dates = df.index.normalize()
    today = df[dates == day]
    if today.empty:
        return None
    before = df[dates < day]
    if before.empty:
        return None

    cpr = _weekly_cpr(df, day)
    if cpr is None:
        return None
    bc, tc = cpr['bc'], cpr['tc']

    prev_close = float(before['close'].iloc[-1])
    day_open = float(today['open'].iloc[0])
    # Condition 1.
    open_ok = bc <= day_open <= tc

    # Conditions 2–3: where the first hour closed against the CPR, and how
    # much room it left to the Camarilla level on that side.
    hour = _first_hour(intraday, day, now or datetime.now())
    r3, s3 = cpr['r3'], cpr['s3']
    fh_dir = None
    if hour is None:
        fh_side, fh_room, fh_ok = 'pending', None, False
    else:
        c1 = hour['close']
        # Condition 3: BUY = green hour, SELL = red; a doji (close == open)
        # is neither.
        if c1 > hour['open']:
            fh_dir = 'BUY'
        elif c1 < hour['open']:
            fh_dir = 'SELL'
        if c1 > tc:
            fh_side = 'above'
            fh_room = (r3 - c1) / c1 * 100
            fh_ok = fh_dir == 'BUY' and c1 < r3 * (1 - CAM_NEAR_PCT / 100)
        elif c1 < bc:
            fh_side = 'below'
            fh_room = (c1 - s3) / c1 * 100
            fh_ok = fh_dir == 'SELL' and c1 > s3 * (1 + CAM_NEAR_PCT / 100)
        else:
            fh_side, fh_room, fh_ok = 'inside', None, False

    return {
        'prev_date': before.index[-1].strftime('%Y-%m-%d'),
        'prev_close': round(prev_close, 2),
        'prev_position': _position(prev_close, bc, tc),
        'open': round(day_open, 2),
        'open_position': _position(day_open, bc, tc),
        'current_price': round(float(today['close'].iloc[-1]), 2),
        'day_change_pct': round((float(today['close'].iloc[-1]) - prev_close)
                                / prev_close * 100, 2) if prev_close else 0.0,
        'cpr_week': cpr['week'],
        'w_bc': round(bc, 2),
        'w_pp': round(cpr['pp'], 2),
        'w_tc': round(tc, 2),
        'w_width_pct': round(cpr['width_pct'], 3) if cpr['width_pct'] is not None else None,
        'w_avg_width_pct': round(cpr['avg_width_pct'], 3) if cpr['avg_width_pct'] else None,
        'w_ratio': round(cpr['ratio'], 2) if cpr['ratio'] is not None else None,
        'w_r3': round(r3, 2),
        'w_s3': round(s3, 2),
        'fh_close': round(hour['close'], 2) if hour else None,
        'fh_high': round(hour['high'], 2) if hour else None,
        'fh_low': round(hour['low'], 2) if hour else None,
        'fh_side': fh_side,
        'fh_open': round(hour['open'], 2) if hour else None,
        'fh_dir': fh_dir,
        'fh_room_pct': round(fh_room, 2) if fh_room is not None else None,
        'fh_ok': fh_ok,
        'open_ok': open_ok,
    }


def select_matches(rows: List[Dict]) -> List[Dict]:
    """Rows that opened inside the CPR (1) and whose first hour closed outside
    it (2) as a candle of that direction (3) with room left to R3 / S3 (4). Indices first, then the most room."""
    out = [r for r in rows if r.get('open_ok') and r.get('fh_ok')]
    return sorted(out, key=lambda r: (not r['is_index'], -(r.get('fh_room_pct') or 0.0),
                                      r['symbol']))


def _frame(data) -> Optional[pd.DataFrame]:
    """Kite-shaped candles -> an OHLC frame on naive IST timestamps."""
    if not data:
        return None
    df = pd.DataFrame(data).set_index('date')[['open', 'high', 'low', 'close']].astype(float)
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, 'tz', None) is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def get_weekly_cpr_open_row(cpr_service: "CPRFilterService", symbol: str,
                            root_date: datetime, token=None,
                            is_index: bool = False,
                            now: Optional[datetime] = None) -> Optional[Dict]:
    """One symbol's reading for `root_date`, matched or not; None if unreadable."""
    if token is None:
        token = cpr_service.get_token(symbol)
    if not token:
        return None
    start = root_date - timedelta(days=_LOOKBACK_DAYS)
    # Daily bars stop the day BEFORE the session, and the session comes from
    # its 30-minute bars below: the opening hour has to be read off them
    # anyway, and a daily bar for a session still trading is not final.
    df = _frame(cpr_service.kite.historical_data(
        token, start.strftime('%Y-%m-%d'),
        (root_date - timedelta(days=1)).strftime('%Y-%m-%d'), 'day'))
    if df is None:
        return None

    # The session itself, and any recent day the daily series lacks, comes
    # from 30-minute bars. (A provider can lag a session's DAILY bar until the
    # day is over — Breeze did — so a gap is filled, not trusted.) Older days
    # keep the daily bar, whose close is the official one, not the last trade.
    last = df.index.max().date()
    session_bars = None
    if last < root_date.date():
        fill_from = (last + timedelta(days=1)).strftime('%Y-%m-%d')
        intraday = _frame(cpr_service.kite.historical_data(
            token, fill_from, root_date.strftime('%Y-%m-%d'), '30minute'))
        if intraday is not None:
            session_bars = intraday[intraday.index.normalize() == pd.Timestamp(root_date.date())]
            daily = intraday.groupby(intraday.index.normalize()).agg(
                {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
            df = pd.concat([df, daily[daily.index.date > last]]).sort_index()

    reading = evaluate(df, root_date.date(), intraday=session_bars, now=now)
    if reading is None:
        return None
    return {'symbol': symbol, 'is_index': is_index, **reading}


def filter_weekly_cpr_open(cpr_service: "CPRFilterService",
                           root_date: Optional[datetime] = None,
                           progress_cb: Optional[Callable[[int, int], None]] = None,
                           now: Optional[datetime] = None) -> Dict:
    """Scan futures stocks + indices for `root_date`.

    Returns {'rows': every readable row (select_matches() applies the
    conditions), 'scanned': n, 'read': n, 'skipped': n, 'date': 'YYYY-MM-DD'}.
    A weekend date is rolled back to the Friday — the setup is about a
    session's open, and a weekend has none. `progress_cb(done, total)` is
    called after each symbol, for the page's progress line.
    """
    if root_date is None:
        root_date = datetime.now()
    root_date = root_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if root_date.weekday() == 5:
        root_date -= timedelta(days=1)
    elif root_date.weekday() == 6:
        root_date -= timedelta(days=2)

    from trading_app.filters.cpr_camarilla_scanner import _scan_universe
    universe = _scan_universe(cpr_service)
    base: List[Dict] = []
    read = 0
    skipped = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=_WORKERS) as executor:
        futures = {
            executor.submit(get_weekly_cpr_open_row, cpr_service, sym, root_date, tok, idx, now): sym
            for sym, tok, idx in universe
        }
        for n, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            if progress_cb:
                progress_cb(n, len(futures))
            try:
                row = future.result()
            except Exception as e:
                skipped += 1
                logger.error(f"Weekly-CPR-open scan failed for {symbol}: {e}")
                continue
            if row is None:
                skipped += 1
                continue
            read += 1
            base.append(row)

    logger.info(
        f"Weekly-CPR-open scan ({root_date.date()}) complete: {read} read, "
        f"{len(select_matches(base))} match, "
        f"{skipped} skipped, in {time.time() - start_time:.1f}s"
    )
    return {
        'rows': sorted(base, key=lambda r: (not r['is_index'], r['symbol'])),
        'scanned': len(universe), 'read': read, 'skipped': skipped,
        'date': root_date.strftime('%Y-%m-%d'),
    }
