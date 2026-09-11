"""Data for the Multichart page — one symbol, four timeframes, live.

Sits on the broker adapter configured by MULTICHART_DATA_PROVIDER, falling
back to DATA_PROVIDER (see provider_logic.get_data_provider). The adapter must
speak Fyers-style symbols — Fyers or ICICI both do; Kite is refused because
none of the configured Kite apps carry the historical-data subscription.

Fyers is the intended provider: it serves 1/2/3/5/10/15/30/60-minute bars
natively, one request per (symbol, interval, window), through the app-wide
8 req/s pacer with single-flight and a short TTL cache. ICICI Breeze has to
resample 3-minute and 60-minute bars from smaller ones in 2-day chunks at
1.5 req/s, and its daily quota is shared with the live algos.

Bars are on the 'Fake IST epoch' grid the rest of the app's charts use:
IST wall-clock values stored as UTC seconds (`time = epoch + 19800`), with
Lightweight Charts told `timezone: 'Etc/UTC'` so the axis reads 09:15 for the
open. Same convention as `_oip_format_candles` in routes/api.py.
"""

import re
import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from trading_app.app.utils.helpers import is_market_hours, is_trading_day
from trading_app.app.utils.logger import logger

IST_OFFSET = 19800          # +05:30 in seconds

# Fyers spot symbols for the index roots the F&O dropdown offers. Mirrors the
# first five rows of FYERS_INDEX_SYMBOLS in routes/api.py — kept local so a
# service does not import a routes module.
INDEX_SYMBOLS = {
    'NIFTY':      'NSE:NIFTY50-INDEX',
    'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
    'FINNIFTY':   'NSE:FINNIFTY-INDEX',
    'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
    'SENSEX':     'BSE:SENSEX-INDEX',
}
INDICES = list(INDEX_SYMBOLS.keys())

# app interval name -> (lookback calendar days, seconds per bar).
#
# Lookbacks are sized for the indicators, not the screen: EMA 200 needs 200
# bars, weekly CPR on the 1h chart needs the previous full week, monthly CPR
# on the daily chart needs the previous month. 99 days is the Fyers intraday
# per-request cap; wider ranges would be chunked into several calls.
INTERVALS: Dict[str, tuple] = {
    'minute':   (5, 60),
    '2minute':  (7, 120),
    '3minute':  (10, 180),
    '5minute':  (15, 300),
    '10minute': (20, 600),
    '15minute': (30, 900),
    '30minute': (60, 1800),
    '60minute': (99, 3600),
    'day':      (400, 86400),
}

DAILY_LOOKBACK_DAYS = 120   # ~80 sessions of daily bars for CPR anchors and PDH/PDL
LIVE_CACHE_TTL = 2.0        # seconds — the page polls every 2s in market hours
HISTORY_CACHE_TTL = 15.0

_SYMBOL_RE = re.compile(r'^[A-Z0-9&-]{1,20}$')
_SESSION_OPEN_MIN = 9 * 60 + 15
_SESSION_CLOSE_MIN = 15 * 60 + 30


class ProviderUnavailable(Exception):
    """No symbol-speaking data provider is logged in for this user."""


class BadRequest(ValueError):
    """Caller error — the route turns it into a 400."""


# ── provider ─────────────────────────────────────────────────────────────

def provider() -> Any:
    """The adapter the page reads from, or raise ProviderUnavailable."""
    from trading_app.service.provider_logic import get_data_provider
    adapter = get_data_provider(context='multichart')
    if adapter is None or not hasattr(adapter, 'historical_data') or not _speaks_symbols(adapter):
        raise ProviderUnavailable('Fyers login required — no symbol-based data provider is available.')
    return adapter


def _speaks_symbols(adapter: Any) -> bool:
    """Fyers and ICICI take 'NSE:SBIN-EQ'; Kite wants a numeric token."""
    name = adapter.__class__.__name__
    return 'Fyers' in name or 'Icici' in name or hasattr(adapter, 'fyers')


# ── symbols ──────────────────────────────────────────────────────────────

def resolve_symbol(symbol: str) -> str:
    """Display root ('NIFTY', 'RELIANCE') -> Fyers symbol string."""
    sym = (symbol or '').strip().upper()
    if not _SYMBOL_RE.match(sym):
        raise BadRequest(f'Invalid symbol: {symbol!r}')
    return INDEX_SYMBOLS.get(sym) or f'NSE:{sym}-EQ'


_symbols_cache: Dict[str, Any] = {'data': None, 'ts': 0.0}
_symbols_lock = threading.Lock()
_SYMBOLS_TTL = 3600.0


def symbols() -> List[Dict[str, str]]:
    """Indices first, then every NSE stock with a futures contract, sorted.

    The stock list comes from the 3-day file cache in stock_list_store (the
    Fyers NSE_FO symbol master behind it), so this costs one master download
    every few days and nothing per page load.
    """
    now = _time.time()
    with _symbols_lock:
        if _symbols_cache['data'] and now - _symbols_cache['ts'] < _SYMBOLS_TTL:
            return _symbols_cache['data']

    from trading_app.filters.stock_list_store import get_fo_stocks
    stocks = get_fo_stocks(provider())
    result = [{'symbol': s, 'kind': 'INDEX'} for s in INDICES] + \
             [{'symbol': s, 'kind': 'FUT'} for s in sorted(stocks)]
    if stocks:
        with _symbols_lock:
            _symbols_cache['data'] = result
            _symbols_cache['ts'] = now
    return result


# ── future volume ────────────────────────────────────────────────────────
# The index carries no traded volume, so the chart's volume histogram is the
# current-expiry FUTURE's — for a stock too, so every symbol reads the same
# way. The contract resolves once per root and holds until the close.

_future_cache: Dict[str, tuple] = {}
_future_lock = threading.Lock()


def future_symbol(adapter: Any, root: str) -> Optional[str]:
    now = _time.time()
    with _future_lock:
        hit = _future_cache.get(root)
        if hit and hit[1] > now:
            return hit[0]
    try:
        sym = adapter.find_future_symbol(root)
    except Exception as e:                      # a root with no listed future, or a master hiccup
        logger.warning(f"[Multichart] future for {root} unresolved: {e}")
        sym = None
    close = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0).timestamp()
    expires = close if now < close else now + 18 * 3600
    with _future_lock:
        _future_cache[root] = (sym, expires)
    return sym


def _volume_rows(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{'time': b['time'], 'volume': b['volume']} for b in bars]


def _future_volume(adapter: Any, root: str, start: str, end: str, interval: str,
                   cache_ttl: float) -> tuple:
    """(future_symbol, [{time, volume}]) — never raises; empty when unavailable."""
    sym = future_symbol(adapter, root)
    if not sym:
        return None, []
    try:
        raw = adapter.historical_data(sym, start, end, interval, use_cache=True,
                                      cache_ttl=cache_ttl, allow_synthetic=False)
    except Exception as e:
        logger.warning(f"[Multichart] future volume {sym} {interval} failed: {e}")
        return sym, []
    return sym, _volume_rows(_to_bars(raw, intraday=interval != 'day'))


# ── candles ──────────────────────────────────────────────────────────────

def _to_bars(raw: List[Dict[str, Any]], intraday: bool) -> List[Dict[str, Any]]:
    """Adapter rows -> chart bars on the fake-IST grid, session bars only."""
    bars = []
    for c in raw or []:
        if any(c.get(k) is None for k in ('open', 'high', 'low', 'close')):
            continue
        stamp = c['date']
        if intraday:
            mins = stamp.hour * 60 + stamp.minute
            if mins < _SESSION_OPEN_MIN or mins >= _SESSION_CLOSE_MIN:
                continue
        t = int(stamp.timestamp()) + IST_OFFSET
        # Fyers stamps a daily bar at midnight UTC — 05:30 on this grid. The
        # page builds today's daily bar from the minute feed at 00:00, and the
        # crosshair label drops the clock only for a bar exactly on midnight,
        # so daily bars are pinned there whichever broker served them.
        if not intraday:
            t -= t % 86400
        bar = {
            'time': t,
            'open': c['open'], 'high': c['high'], 'low': c['low'], 'close': c['close'],
            'volume': c.get('volume') or 0,
        }
        if c.get('synthetic'):
            bar['synthetic'] = True
        bars.append(bar)
    return bars


def _daily_rows(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {'date': c['date'].strftime('%Y-%m-%d'),
         'o': c['open'], 'h': c['high'], 'l': c['low'], 'c': c['close']}
        for c in raw or []
        if not any(c.get(k) is None for k in ('open', 'high', 'low', 'close'))
    ]


def _fetch_error(adapter: Any) -> Optional[str]:
    # Thread-local on the adapter: must be read on the thread that fetched.
    try:
        return adapter.last_history_error()
    except Exception:
        return None


def candles(symbol: str, interval: str) -> Dict[str, Any]:
    """History for one pane plus the daily bars its CPR anchors on."""
    if interval not in INTERVALS:
        raise BadRequest(f'Unsupported interval: {interval!r}')
    fy_symbol = resolve_symbol(symbol)
    lookback, _secs = INTERVALS[interval]
    adapter = provider()

    today = date.today()
    start = today - timedelta(days=lookback)
    raw = adapter.historical_data(
        fy_symbol, start.isoformat(), today.isoformat(), interval,
        use_cache=True, cache_ttl=HISTORY_CACHE_TTL, allow_synthetic=True,
    )
    fetch_error = _fetch_error(adapter) if not raw else None

    daily_raw = adapter.historical_data(
        fy_symbol, (today - timedelta(days=DAILY_LOOKBACK_DAYS)).isoformat(),
        today.isoformat(), 'day', use_cache=True, cache_ttl=300.0,
    ) if interval != 'day' else raw

    fut_symbol, fut_volume = _future_volume(adapter, symbol.upper(), start.isoformat(),
                                            today.isoformat(), interval, HISTORY_CACHE_TTL)

    return {
        'success': True,
        'symbol': symbol.upper(),
        'fy_symbol': fy_symbol,
        'interval': interval,
        'seconds': INTERVALS[interval][1],
        'candles': _to_bars(raw, intraday=interval != 'day'),
        'daily': _daily_rows(daily_raw),
        'future_symbol': fut_symbol,
        'future_volume': fut_volume,
        'fetch_error': fetch_error,
    }


def live(symbol: str) -> Dict[str, Any]:
    """Today's 1-minute bars — the page's poll target.

    Every pane re-buckets these into its own timeframe client-side (all NSE
    intraday buckets anchor on 09:15, so the aggregation is exact), which is
    what keeps four live charts at two broker requests per tick — the spot
    bars and the future's, for the volume histogram.
    """
    fy_symbol = resolve_symbol(symbol)
    adapter = provider()
    today = date.today().isoformat()
    raw = adapter.historical_data(
        fy_symbol, today, today, 'minute',
        use_cache=True, cache_ttl=LIVE_CACHE_TTL, allow_synthetic=True,
    )
    bars = _to_bars(raw, intraday=True)
    fut_symbol, fut_volume = _future_volume(adapter, symbol.upper(), today, today, 'minute', LIVE_CACHE_TTL)
    return {
        'success': True,
        'symbol': symbol.upper(),
        'fy_symbol': fy_symbol,
        'candles': bars,
        'future_symbol': fut_symbol,
        'future_volume': fut_volume,
        'ltp': bars[-1]['close'] if bars else None,
        'market_open': market_open(),
        'fetch_error': _fetch_error(adapter) if not raw else None,
        'ts': int(datetime.now().timestamp()),
    }


_mh_cache: Dict[str, Any] = {'value': None, 'ts': 0.0}


def market_open() -> bool:
    """is_market_hours() & is_trading_day(), 60-second TTL."""
    now = _time.time()
    if _mh_cache['value'] is not None and now - _mh_cache['ts'] < 60:
        return _mh_cache['value']
    value = bool(is_market_hours() and is_trading_day())
    _mh_cache.update(value=value, ts=now)
    return value
