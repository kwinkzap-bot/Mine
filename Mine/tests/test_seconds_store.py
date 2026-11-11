"""Backtest/seconds_store: the 30-second bars the Candle Breakout backtest and
its board both read."""
from datetime import datetime

import pandas as pd
import pytest

from trading_app.Backtest import seconds_store as ss


@pytest.fixture(autouse=True)
def tmp_store(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, 'DATA_DIR', str(tmp_path))


def _day(day, n=4, base=100.0):
    t0 = pd.Timestamp(f'{day} 09:15')
    return [{'date': (t0 + pd.Timedelta(seconds=30 * i)).strftime('%Y-%m-%d %H:%M:%S+05:30'),
             'open': base + i, 'high': base + i + 1, 'low': base + i - 1, 'close': base + i,
             'volume': 0} for i in range(n)]


NOON = datetime(2026, 11, 11, 12, 0, tzinfo=ss.IST)
EVENING = datetime(2026, 11, 11, 16, 0, tzinfo=ss.IST)


def test_sessions_are_kept_and_read_back_by_range():
    ss.merge_and_read('NIFTY', _day('2026-11-05') + _day('2026-11-06'), '2026-11-01', '2026-11-30', now=NOON)
    df = ss.merge_and_read('NIFTY', [], '2026-11-06', '2026-11-06', now=NOON)
    assert len(df) == 4 and str(df.index[0]) == '2026-11-06 09:15:00'
    assert ss.span('NIFTY') == ('2026-11-05', '2026-11-06', 2)


def test_a_fetch_of_a_stored_day_replaces_it_whole():
    ss.merge_and_read('NIFTY', _day('2026-11-05', n=4), '2026-11-01', '2026-11-30', now=NOON)
    df = ss.merge_and_read('NIFTY', _day('2026-11-05', n=2, base=200), '2026-11-01', '2026-11-30', now=NOON)
    assert list(df['close']) == [200.0, 201.0]


def test_todays_forming_session_is_never_stored_or_returned_until_it_closes():
    df = ss.merge_and_read('NIFTY', _day('2026-11-10') + _day('2026-11-11'), '2026-11-01', '2026-11-30', now=NOON)
    assert set(df.index.normalize().astype(str)) == {'2026-11-10'}
    assert ss.span('NIFTY')[2] == 1
    df = ss.merge_and_read('NIFTY', _day('2026-11-11'), '2026-11-01', '2026-11-30', now=EVENING)
    assert ss.span('NIFTY') == ('2026-11-10', '2026-11-11', 2) and len(df) == 8


def test_drop_forming_on_minutes():
    idx = pd.to_datetime(['2026-11-10 15:29', '2026-11-11 09:15'])
    df = pd.DataFrame({'close': [1.0, 2.0]}, index=idx)
    assert len(ss.drop_forming(df, NOON)) == 1 and len(ss.drop_forming(df, EVENING)) == 2
