"""
Camarilla-inside-CPR Touch Scanner.

Two modes, and the mode picks BOTH the candle being judged and the
timeframe the levels come from — the levels always sit two steps above
the candle, which is what stops them from being a restatement of the bar
they are drawn on:

    mode='daily'   ->  DAILY candle   judged against MONTHLY CPR + Camarilla
    mode='weekly'  ->  WEEKLY candle  judged against YEARLY  CPR + Camarilla

The rule, identical in both modes:

  1. GATE — the Camarilla S3 (bullish side) or R3 (bearish side) must sit
     INSIDE the CPR band, i.e. between BC and TC. At most one of the two
     ever can; see get_cpr_camarilla_signal() for why.
  2. TOUCH — the candle for the selected date must then touch the Pivot
     (PP) or that same inside Camarilla line. A touch is the candle's
     range straddling the level (low <= level <= high); a wick counts and
     there is NO close condition, so this reports levels being TESTED,
     not confirmed reversals. That is the deliberate difference from
     CPRFilterService.detect_camarilla_cpr_reversal(), which additionally
     demands a close back beyond the level.

Universe is F&O futures stocks PLUS the indices — unlike
CPRFilterService.get_fo_stocks(), which strips indices out on purpose.

Reuses CPRFilterService for historical-data fetching/caching, instrument
tokens and rate limiting rather than duplicating that infrastructure.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from trading_app.filters.cpr_filter import CPRFilterService

logger = logging.getLogger(__name__)

MODES = ('daily', 'weekly')

# Camarilla's third level multiplier — the only one this scan cares about.
_CAMARILLA_R3 = 1.1 / 4.0

# Indices scanned alongside the futures stocks. Their short names do not
# match a tradingsymbol in the NSE dump, so the token has to be passed to
# get_hist_data() explicitly (see its docstring). Mirrors
# NSE_INDEX_TOKENS_CPR in app/routes/api.py.
INDEX_TOKENS = {
    'NIFTY':      256265,
    'BANKNIFTY':  260105,
    'FINNIFTY':   257801,
    'MIDCPNIFTY': 288009,
}

# How far back the daily history must reach for the level period to be
# fully covered. Monthly levels need the previous calendar month (~62
# days); yearly levels need the whole previous calendar year, which is
# ~730 days back when the root date sits in December.
_LOOKBACK_DAYS = {'daily': 400, 'weekly': 800}


def _touches(level: float, low: float, high: float) -> bool:
    """A genuine touch: the candle's range straddles the level. Wicks count."""
    return low <= level <= high


def _levels_from(high: float, low: float, close: float) -> Dict[str, float]:
    """CPR (PP/BC/TC) and Camarilla R3/S3 from one period's OHLC."""
    from trading_app.service.cpr_service import CPRService

    pp, bc, tc = CPRService.calculate_cpr(high, low, close)
    rng = high - low
    return {
        'pp': pp, 'bc': bc, 'tc': tc,
        'cam_r3': close + rng * _CAMARILLA_R3,
        'cam_s3': close - rng * _CAMARILLA_R3,
    }


def _level_period(cpr_service: "CPRFilterService", mode: str,
                  root_date: datetime) -> Tuple[datetime, datetime]:
    """The period the levels are derived from: the PREVIOUS month for a
    daily candle, the PREVIOUS year for a weekly one."""
    if mode == 'weekly':
        return cpr_service.get_prev_year_range(root_date)
    return cpr_service.get_prev_month_range(root_date)


def _candle_for_date(df: pd.DataFrame, mode: str, root_date: datetime) -> Optional[Dict]:
    """The one candle being judged, built from daily bars.

    'daily' is the bar stamped with root_date itself — absent (holiday,
    or a date with no trade) means no candle to judge, and the symbol is
    skipped rather than silently falling back to a neighbouring day.
    'weekly' aggregates Mon..Sun of the week CONTAINING root_date, which
    mid-week is a partial candle; it is still reported, flagged.
    """
    dates = pd.to_datetime(df.index).date

    if mode == 'weekly':
        week_start = (root_date - timedelta(days=root_date.weekday())).date()
        week_end = week_start + timedelta(days=6)
        bars = df[(dates >= week_start) & (dates <= week_end)]  # type: ignore
        if bars.empty:
            return None
        last_bar = pd.to_datetime(bars.index[-1]).date()
        return {
            'date': week_start.isoformat(),
            'open':  float(bars['open'].iloc[0]),
            'high':  float(bars['high'].max()),
            'low':   float(bars['low'].min()),
            'close': float(bars['close'].iloc[-1]),
            # The week is only done once Friday is in. Scanning on a
            # Wednesday judges three days of it and says so. A weekend scan
            # of the week just gone is NOT partial — Friday's bar closed it,
            # even though week_end (Sunday) has not passed yet.
            'partial': (datetime.now().date() <= week_end
                        and last_bar.weekday() < 4),
        }

    bars = df[dates == root_date.date()]  # type: ignore
    if bars.empty:
        return None
    return {
        'date': root_date.date().isoformat(),
        'open':  float(bars['open'].iloc[0]),
        'high':  float(bars['high'].iloc[0]),
        'low':   float(bars['low'].iloc[0]),
        'close': float(bars['close'].iloc[0]),
        'partial': root_date.date() == datetime.now().date(),
    }


def get_cpr_camarilla_signal(cpr_service: "CPRFilterService", symbol: str, root_date: datetime,
                             mode: str = 'daily', token: Optional[int] = None,
                             is_index: bool = False) -> List[Dict]:
    """Signals for one symbol on `root_date`. Returns a list for shape, but
    in practice never more than one entry: with the standard CPR and
    Camarilla formulas the gate is mutually exclusive. Writing the band as
    BC=mid, TC=mid+(2/3)(close-mid) and the level as close±0.275·range,
    S3 lands inside only when the period closed in the TOP of its range
    (close-mid >= 0.275·range) and R3 only when it closed in the BOTTOM —
    so a period closing mid-range yields no signal at all, whatever the
    candle does. That is the gate doing most of the filtering.

    Empty when the gate fails, nothing is touched, or history is missing."""
    mode = mode if mode in MODES else 'daily'

    df = cpr_service.get_hist_data(symbol, days=_LOOKBACK_DAYS[mode], interval='day',
                                   end_date=root_date, token=token)
    if df is None or df.empty:
        return []

    period_start, period_end = _level_period(cpr_service, mode, root_date)
    dates = pd.to_datetime(df.index).date
    period_df = df[(dates >= period_start.date()) & (dates <= period_end.date())]  # type: ignore
    if period_df.empty:
        return []

    lv = _levels_from(float(period_df['high'].max()),
                      float(period_df['low'].min()),
                      float(period_df['close'].iloc[-1]))

    candle = _candle_for_date(df, mode, root_date)
    if candle is None:
        return []

    low, high = candle['low'], candle['high']
    cpr_min, cpr_max = min(lv['bc'], lv['tc']), max(lv['bc'], lv['tc'])
    level_tf = 'Yearly' if mode == 'weekly' else 'Monthly'

    out: List[Dict] = []
    for side, cam_key, cam_name in (('BUY', 'cam_s3', 'S3'), ('SELL', 'cam_r3', 'R3')):
        cam = lv[cam_key]
        if not (cpr_min <= cam <= cpr_max):
            continue                      # gate: Camarilla line not inside the CPR
        touched = []
        if _touches(lv['pp'], low, high):
            touched.append('Pivot')
        if _touches(cam, low, high):
            touched.append(cam_name)
        if not touched:
            continue                      # gate passed, but nothing was tested
        out.append({
            'symbol': symbol,
            'is_index': is_index,
            'direction': side,
            'level_timeframe': level_tf,
            'candle_timeframe': 'Weekly' if mode == 'weekly' else 'Daily',
            'candle_date': candle['date'],
            'partial_candle': candle['partial'],
            'current_price': round(candle['close'], 2),
            'candle_high': round(high, 2),
            'candle_low': round(low, 2),
            'pivot': round(lv['pp'], 2),
            'cpr_bc': round(cpr_min, 2),
            'cpr_tc': round(cpr_max, 2),
            'camarilla': round(cam, 2),
            'camarilla_line': cam_name,
            'touched': ' + '.join(touched),
        })
    return out


def _scan_universe(cpr_service: "CPRFilterService") -> List[Tuple[str, Optional[int], bool]]:
    """(symbol, token, is_index) for every futures stock plus the indices.
    get_fo_stocks() strips indices out deliberately, so they are added back
    here with their explicit tokens."""
    universe: List[Tuple[str, Optional[int], bool]] = [
        (sym, None, False) for sym in cpr_service.get_fo_stocks()
    ]
    universe.extend((sym, tok, True) for sym, tok in INDEX_TOKENS.items())
    return universe


def filter_cpr_camarilla_touch(cpr_service: "CPRFilterService", root_date: Optional[datetime] = None,
                               mode: str = 'daily') -> Dict:
    """Scans futures stocks + indices for a Camarilla-inside-CPR level being
    touched by the candle on `root_date` (see the module docstring for the
    rule). Returns {'buy': [...], 'sell': [...]}."""
    mode = mode if mode in MODES else 'daily'
    if root_date is None:
        root_date = datetime.now()
    # A daily candle only exists on a trading day; roll a weekend date back
    # to Friday. Weekly mode aggregates the whole week either way, but the
    # roll-back keeps it on the week that actually has bars.
    if root_date.weekday() == 5:
        root_date -= timedelta(days=1)
    elif root_date.weekday() == 6:
        root_date -= timedelta(days=2)

    universe = _scan_universe(cpr_service)
    buy: List[Dict] = []
    sell: List[Dict] = []
    start_time = time.time()

    workers_count = cpr_service.MAX_WORKERS
    if cpr_service.kite.__class__.__name__ == 'FyersDataServiceAdapter':
        workers_count = 15

    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        futures = {
            executor.submit(get_cpr_camarilla_signal, cpr_service, sym, root_date, mode, tok, idx): sym
            for sym, tok, idx in universe
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                for sig in future.result(timeout=25):
                    (buy if sig['direction'] == 'BUY' else sell).append(sig)
            except Exception as e:
                logger.error(f"Camarilla-CPR touch scan failed for {symbol}: {e}")

    logger.info(
        f"Camarilla-CPR touch scan ({mode} candle vs "
        f"{'yearly' if mode == 'weekly' else 'monthly'} levels, {root_date.date()}) complete: "
        f"{len(buy)} BUY, {len(sell)} SELL in {time.time() - start_time:.1f}s"
    )
    # Indices first, then alphabetical — the handful of indices are the
    # rows worth seeing before a 180-row stock list.
    sort_key = lambda x: (not x['is_index'], x['symbol'])
    return {'buy': sorted(buy, key=sort_key), 'sell': sorted(sell, key=sort_key)}
