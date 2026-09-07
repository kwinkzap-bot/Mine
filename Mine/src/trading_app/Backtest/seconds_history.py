"""Sub-minute history for BACKTESTS, cheapest source first.

The two brokers that can serve 30-second bars serve them very differently, and
neither one alone answers "give me this date range":

  Fyers  — serves 30S natively, one request per chunk, but keeps only a
           ROLLING 30 CALENDAR DAYS of it. Measured 2026-09-06 on
           NSE:NIFTY50-INDEX: a 2026-07-20 → 09-06 request came back starting
           2026-08-07, and windows older than that answer 'no_data'.
  ICICI  — has no sub-minute interval of its own, so Breeze 1-second data is
           aggregated into it. That reaches back at least a year (2025-09-08
           fetched fine on the same day), but costs 25 Breeze requests per
           trading day out of an app-wide 5,000/day the LIVE ALGOS SHARE.

So this takes the recent tail from Fyers whenever Fyers can serve it, and asks
the configured provider only for the older head. A one-month backtest becomes
two Fyers requests instead of ~550 Breeze ones; a range older than Fyers'
window still gets fetched, at the Breeze cost it really carries.

Backtests only. The live algos keep calling their own configured provider
directly: they ask for a few days at a time (so the split saves nothing) and
switching which broker's bars they trade off is a live-behaviour change, not a
fetch optimisation.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Fyers' rolling 30-second window, in calendar days. Measured, not documented —
# see the module docstring. Kept a touch short of 30 so the boundary day, which
# may or may not still be served depending on the hour, is asked of the provider
# that definitely has it.
FYERS_SECONDS_DAYS = 28

# Only these need the split; a minute and above is one cheap request everywhere.
SUB_MINUTE = ('30second',)


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def fetch_seconds_history(provider, instrument_token, from_date, to_date,
                          interval: str = '30second', user: Optional[str] = None,
                          use_cache: bool = False) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Bars for [from_date, to_date], Fyers first and the configured provider
    beyond it.

    Returns (candles, note). `note` is a plain-language line about what the
    range actually returned — which source covered what, and where a
    budget-limited Breeze walk stopped — or None when there is nothing worth
    saying. Never raises: a failing leg contributes nothing and is described in
    the note.
    """
    fd, td = _as_date(from_date), _as_date(to_date)
    if interval not in SUB_MINUTE or fd is None or td is None or fd > td:
        return provider.historical_data(
            instrument_token=instrument_token, from_date=from_date,
            to_date=to_date, interval=interval, use_cache=use_cache), None

    # Which broker is already answering. Imported inside the call, not at
    # module level: this module is imported by the route layer and the adapters
    # pull in the broker SDKs.
    from trading_app.service.fyers_data_service import FyersDataServiceAdapter
    from trading_app.service.icici_data_service import IciciDataServiceAdapter
    if isinstance(provider, FyersDataServiceAdapter):
        tag = 'Fyers'
    elif isinstance(provider, IciciDataServiceAdapter):
        tag = 'ICICI'
    else:
        tag = 'Kite'

    # Already on Fyers: one call, and its own 30-day wall is the whole story.
    if tag == 'Fyers':
        candles = provider.historical_data(
            instrument_token=instrument_token, from_date=from_date,
            to_date=to_date, interval=interval, use_cache=use_cache)
        return candles, _coverage_note(candles, fd, td, 'Fyers')

    # Kite addresses instruments by numeric token; Fyers cannot be handed one.
    # ICICI speaks Fyers symbols, so its token passes straight through.
    fyers = None
    if isinstance(instrument_token, str):
        try:
            from trading_app.service.provider_logic import get_fyers_adapter
            fyers = get_fyers_adapter(user)
        except Exception as exc:            # noqa: BLE001 - never break the run
            logger.warning("[SecondsHistory] Fyers adapter unavailable: %s", exc)

    head_to = td
    fyers_candles: List[Dict[str, Any]] = []
    if fyers is not None:
        split = max(fd, td - timedelta(days=FYERS_SECONDS_DAYS))
        try:
            fyers_candles = fyers.historical_data(
                instrument_token=instrument_token,
                from_date=split.isoformat(), to_date=td.isoformat(),
                interval=interval, use_cache=use_cache) or []
        except Exception as exc:            # noqa: BLE001
            logger.warning("[SecondsHistory] Fyers %s leg failed: %s", interval, exc)
            fyers_candles = []
        first = _as_date(fyers_candles[0]['date']) if fyers_candles else None
        if first is not None:
            # Ask the expensive provider only for what Fyers did not answer —
            # Fyers' window moves, so trust the bars it returned over the
            # nominal split date.
            head_to = first - timedelta(days=1)
            logger.info("[SecondsHistory] Fyers served %d %s bars from %s",
                        len(fyers_candles), interval, first)

    # Fyers configured but answering nothing (a dead access token is the usual
    # reason — it needs a daily login) means the provider below is being billed
    # 25 Breeze requests a day for the window Fyers gives away. Worth saying.
    fyers_missing = fyers is not None and not fyers_candles

    head: List[Dict[str, Any]] = []
    if fd <= head_to:
        try:
            head = provider.historical_data(
                instrument_token=instrument_token, from_date=fd.isoformat(),
                to_date=head_to.isoformat(), interval=interval,
                use_cache=use_cache) or []
        except Exception as exc:            # noqa: BLE001
            logger.warning("[SecondsHistory] %s %s leg failed: %s", tag, interval, exc)
            head = []

    # Both legs are keyed by bar timestamp, so an overlap at the seam resolves
    # to one bar rather than two of the same minute from different brokers.
    merged: Dict[Any, Dict[str, Any]] = {}
    for candle in list(head) + list(fyers_candles):
        merged[candle['date']] = candle
    candles = [merged[k] for k in sorted(merged)]

    provider_note = None
    if fd <= head_to and hasattr(provider, 'last_history_error'):
        provider_note = provider.last_history_error()

    return candles, _coverage_note(candles, fd, td, tag, len(fyers_candles),
                                   provider_note, fyers_missing)


def _coverage_note(candles, fd: date, td: date, source: str,
                   fyers_bars: int = 0, provider_note: Optional[str] = None,
                   fyers_missing: bool = False) -> Optional[str]:
    """One line about what the range actually returned, or None if it all came
    back as asked."""
    if not candles:
        return provider_note or (f"No 30-second data returned for {fd} → {td}.")
    got_start = _as_date(candles[0]['date'])
    if got_start is None:
        return provider_note
    parts = []
    if (got_start - fd).days > 3:
        parts.append(f"30-second data starts at {got_start}, not the requested {fd}")
    if provider_note:
        parts.append(provider_note)
    elif fyers_bars and source != 'Fyers':
        parts.append(f"{fyers_bars} of these bars came from Fyers' rolling "
                     f"{FYERS_SECONDS_DAYS}-day window, the rest from {source}")
    if fyers_missing:
        parts.append(f"Fyers served nothing, so the last {FYERS_SECONDS_DAYS} days "
                     f"came from {source} too — check the Fyers login to make "
                     f"that stretch free again")
    return '  ·  '.join(parts) if parts else None
