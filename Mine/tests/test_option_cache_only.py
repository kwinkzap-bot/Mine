"""Option pricing with no Breeze session reads the disk cache, and a cache miss leaves the
trade unpriced instead of letting the strike walk move to another strike."""
from datetime import date, datetime, timedelta

import pytest

from trading_app.service import cpr_option_service as cos
from trading_app.service import icici_data_service as ids
from trading_app.service import provider_logic


def _bars(px):
    return [{'time': f'{h:02d}:{m:02d}', 'open': px, 'high': px, 'low': px, 'close': px}
            for h in range(9, 16) for m in range(60) if (9, 15) <= (h, m) <= (15, 29)]


@pytest.fixture
def no_breeze(monkeypatch):
    monkeypatch.setattr(provider_logic, 'get_icici_adapter', lambda *a, **k: None)
    monkeypatch.setattr(ids, 'option_cache_available', lambda: True)
    store = {}
    monkeypatch.setattr(ids, 'cached_option_minutes',
                        lambda root, expiry, strike, ot, day: store.get((int(strike), ot)))
    return store


def _raw(day, px):
    """The bars as the cache stores them: Breeze's, stamped with a datetime."""
    return [{'date': datetime(day.year, day.month, day.day, 9, 15) + timedelta(minutes=k),
             'open': px, 'high': px, 'low': px, 'close': px, 'volume': 1} for k in range(375)]


def test_no_session_reads_the_cache_and_a_miss_raises(no_breeze):
    day = date.today() - timedelta(days=31)
    no_breeze[(24000, 'CE')] = _raw(day, 250.0)
    fetch = cos._option_minutes_fetcher('NIFTY')
    assert getattr(fetch, 'cache_only', False)
    expiry = (date.today() - timedelta(days=30)).isoformat()
    con = {'expiry': expiry, 'strike': 24000, 'option_type': 'CE'}
    bars = fetch(con, day)
    assert len(bars) == 375 and bars[0]['time'] == '09:15' and bars[0]['close'] == 250.0
    with pytest.raises(cos.OptionCacheMiss):
        fetch(dict(con, strike=24100), day)


def test_a_cache_miss_is_not_skipped_by_the_strike_walk():
    def fetch(con, day):
        if con['strike'] == 24000:
            return _bars(300.0)
        raise cos.OptionCacheMiss('not cached')
    con = {'expiry': '2026-07-09', 'strike': 24000, 'option_type': 'CE'}
    with pytest.raises(cos.OptionCacheMiss):
        cos.pick_strike('NIFTY', con, date(2026, 7, 8), '10:00', 250.0, fetch)
    # an ordinary failure is still just a dead strike
    def flaky(con, day):
        if con['strike'] == 24000:
            return _bars(300.0)
        raise RuntimeError('Breeze hiccup')
    picked, _, _ = cos.pick_strike('NIFTY', con, date(2026, 7, 8), '10:00', 250.0, flaky)
    assert picked['strike'] == 24000
