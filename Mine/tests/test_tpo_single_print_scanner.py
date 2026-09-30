"""Rule tests for the TPO single-print scanner.

Two halves.

The first is a GOLDEN test against ``data/tpo_profiles_from_mine_tpo.json``:
64 real NSE sessions (8 symbols x 8 days of 30-minute bars, taken off
/api/multichart/candles) together with what
``static/js/components/mine_tpo.js`` itself computed for each of them —
step, period count, POC, VAH, VAL and every single-print band. The scanner
is a port of that engine and the popup chart a scanner row opens IS that
engine, so a row in the grid and a band on the chart have to be the same
band. If this file ever disagrees, one of the two has drifted.

The second half is synthetic OHLC, so one rule can be moved at a time —
above all the tail cut, which is the distinction the scanner exists on: a
one-period run at the profile's own top or bottom is the session's buying
or selling tail, not a single print, and every session has one.
"""
from datetime import date, datetime, timedelta
import json
from pathlib import Path

import pandas as pd
import pytest

from trading_app.filters.tpo_single_print_scanner import (
    MIN_PERIODS, SESSION_OPEN_MIN, TPO_ROWS_TARGET, build_profile,
    filter_tpo_singles, get_tpo_row, nice_step, select_singles)

GOLDEN = Path(__file__).parent / 'data' / 'tpo_profiles_from_mine_tpo.json'

OPEN = datetime(2026, 9, 29, 9, 15)


def _session(highs, lows, closes=None, start=OPEN, step_mins=30):
    """A session of 30-minute bars, one per (high, low) pair."""
    closes = closes or lows
    idx = [pd.Timestamp(start) + timedelta(minutes=step_mins * i)
           for i in range(len(highs))]
    return pd.DataFrame({'high': highs, 'low': lows, 'close': closes}, index=idx)


def _profile(highs, lows, step=1.0, **kw):
    return build_profile(_session(highs, lows, **kw), pd.Timestamp(OPEN), step=step)


def _bands(profile):
    return [(s['low'], s['high']) for s in (profile or {}).get('singles', [])]


# ── the golden fixture: this port vs the chart's own engine ──────────────

def _golden_cases():
    with open(GOLDEN) as fh:
        data = json.load(fh)
    return sorted(data.items())


@pytest.mark.parametrize('name,case', _golden_cases())
def test_matches_mine_tpo_exactly(name, case):
    """Every level and every band, against what mine_tpo.js produced for the
    same 30-minute bars."""
    day = date.fromisoformat(name.split('|')[1])
    # The fixture's timestamps are the app's fake-IST epoch (IST wall clock
    # stored as UTC seconds), which read back as IST wall clock naive.
    idx = pd.to_datetime([row[0] for row in case['bars']], unit='s')
    frame = pd.DataFrame({'high':  [row[1] for row in case['bars']],
                          'low':   [row[2] for row in case['bars']],
                          'close': [row[3] for row in case['bars']]}, index=idx)
    want = case['expect']
    got = build_profile(frame, pd.Timestamp(day) + timedelta(minutes=SESSION_OPEN_MIN),
                        step=want['step'])

    assert got is not None, f'{name}: the chart built a profile and this did not'
    assert got['periods'] == want['periods']
    assert got['poc'] == pytest.approx(want['poc'])
    assert got['vah'] == pytest.approx(want['vah'])
    assert got['val'] == pytest.approx(want['val'])
    assert sorted(_bands(got)) == sorted(tuple(b) for b in want['singles'])


def test_golden_fixture_covers_both_answers():
    """A fixture of 64 sessions that all came back empty would pass the test
    above while proving nothing about the single-print rule."""
    cases = [case for _, case in _golden_cases()]
    with_band = [c for c in cases if c['expect']['singles']]
    assert len(cases) >= 50
    assert 5 <= len(with_band) <= len(cases) - 5


def test_auto_step_matches_the_charts(tmp_path):
    """The scanner derives its own row step for a one-session window; the
    chart derives the same one from that session's range. The fixture's step
    is the chart's, so deriving it here has to land on the same number."""
    for name, case in _golden_cases():
        highs = [row[1] for row in case['bars']]
        lows = [row[2] for row in case['bars']]
        derived = nice_step((max(highs) - min(lows)) / TPO_ROWS_TARGET)
        assert derived == pytest.approx(case['expect']['step']), name


# ── the tail cut, which is the whole rule ────────────────────────────────

def test_one_period_run_at_the_top_is_a_tail_not_a_single():
    """The last period spikes alone above everything else. Every session has
    a tail like this; calling it a single print would match every symbol."""
    profile = _profile(highs=[105, 105, 105, 105, 120],
                       lows=[100, 100, 100, 100, 105])
    assert _bands(profile) == []


def test_one_period_run_at_the_bottom_is_a_tail_too():
    profile = _profile(highs=[105, 105, 105, 105, 100],
                       lows=[100, 100, 100, 100, 85])
    assert _bands(profile) == []


def test_a_gap_through_the_body_is_a_single_print():
    """Four periods hold 100-105, one period runs alone through 105-110, and
    two more hold 110-115. The middle run is bounded on BOTH sides, so it is
    the gap the auction skipped."""
    profile = _profile(highs=[105, 105, 105, 105, 110, 115, 115],
                       lows=[100, 100, 100, 100, 105, 110, 110])
    assert _bands(profile) == [(106.0, 110.0)]


def test_two_separate_gaps_are_two_bands():
    profile = _profile(
        highs=[103, 103, 103, 106, 109, 109, 109, 112, 115, 115, 115],
        lows=[100, 100, 100, 104, 107, 107, 107, 110, 113, 113, 113])
    assert len(_bands(profile)) == 2


def test_a_session_every_period_traded_through_has_no_singles():
    profile = _profile(highs=[110] * 6, lows=[100] * 6)
    assert _bands(profile) == []


# ── shape of the reading ─────────────────────────────────────────────────

def test_too_few_periods_is_unreadable_not_empty():
    """Below MIN_PERIODS almost every row is reached by one period, so "only
    one period got here" stops meaning anything. Unreadable, not 'no match'."""
    assert _profile(highs=[105] * (MIN_PERIODS - 1),
                    lows=[100] * (MIN_PERIODS - 1)) is None


def test_a_flat_session_is_unreadable():
    assert _profile(highs=[100] * 8, lows=[100] * 8) is None


def test_periods_are_anchored_on_0915_not_on_the_first_bar():
    """A symbol that only starts printing at 11:15 keeps its real letters —
    six 30-minute periods, not six starting from A."""
    late = _session([105] * 6, [100] * 6, start=datetime(2026, 9, 29, 11, 15))
    profile = build_profile(late, pd.Timestamp(OPEN), step=1.0)
    assert profile['periods'] == 6


def test_bars_before_the_session_open_are_dropped():
    """A pre-open print would otherwise land in a negative period."""
    early = _session([105] * 7, [100] * 7, start=datetime(2026, 9, 29, 8, 45))
    profile = build_profile(early, pd.Timestamp(OPEN), step=1.0)
    assert profile['periods'] == 6      # 08:45 dropped, 09:15..14:15 kept


def test_nice_step_snaps_to_the_readable_ladder():
    assert nice_step(0.4) == pytest.approx(0.5)
    assert nice_step(4.83) == pytest.approx(5)
    assert nice_step(8.3) == pytest.approx(10)
    assert nice_step(0) == 1


# ── row assembly and ranking ─────────────────────────────────────────────

class _StubService:
    """The slice of CPRFilterService the scanner uses."""

    MAX_WORKERS = 2

    class _Kite:
        pass

    def __init__(self, frames):
        self.frames = frames
        self.kite = self._Kite()

    def get_hist_data(self, symbol, days=5, interval='30minute',
                      end_date=None, token=None):
        return self.frames.get(symbol)

    def get_fo_stocks(self):
        return [s for s in self.frames if not s.startswith('NIFTY')]


GAPPY = dict(highs=[105, 105, 105, 105, 110, 115, 115],
             lows=[100, 100, 100, 100, 105, 110, 110])
FLAT = dict(highs=[110] * 6, lows=[100] * 6)


def test_row_reports_the_band_and_ranks_by_its_height():
    """The row derives its own step (range 15 / 30 rows -> 0.5), so the band
    is reported on that grid rather than the whole points the bars happen to
    be written in — the same grid the chart is told to draw."""
    row = get_tpo_row(_StubService({'ACME': _session(**GAPPY)}), 'ACME',
                      datetime(2026, 9, 29))
    assert row['single_count'] == 1
    assert row['row_step'] == pytest.approx(0.5)
    assert row['bands'] == '105.5–110'
    assert row['tallest_pts'] == pytest.approx(4.5)
    assert row['session_high'] == 115 and row['session_low'] == 100


def test_a_readable_session_with_no_band_is_a_row_not_a_skip():
    """'214 read, 35 matched' needs the clean sessions counted, so a session
    that simply did not print a single still comes back as a row."""
    row = get_tpo_row(_StubService({'ACME': _session(**FLAT)}), 'ACME',
                      datetime(2026, 9, 29))
    assert row is not None and row['single_count'] == 0
    assert select_singles([row]) == []


def test_no_intraday_history_is_a_skip():
    assert get_tpo_row(_StubService({}), 'ACME', datetime(2026, 9, 29)) is None


def test_bars_from_another_day_are_not_this_session():
    """The window fetched spans several days; only the requested one counts."""
    other = _session(**GAPPY, start=datetime(2026, 9, 28, 9, 15))
    assert get_tpo_row(_StubService({'ACME': other}), 'ACME',
                       datetime(2026, 9, 29)) is None


def test_select_puts_indices_first_then_the_tallest_band():
    rows = [
        {'symbol': 'SMALL', 'is_index': False, 'single_count': 1, 'tallest_pct': 0.2},
        {'symbol': 'BIG', 'is_index': False, 'single_count': 1, 'tallest_pct': 2.0},
        {'symbol': 'NIFTY', 'is_index': True, 'single_count': 1, 'tallest_pct': 0.1},
        {'symbol': 'NONE', 'is_index': False, 'single_count': 0, 'tallest_pct': 0.0},
    ]
    assert [r['symbol'] for r in select_singles(rows)] == ['NIFTY', 'BIG', 'SMALL']


def test_scan_rolls_a_weekend_back_to_friday(monkeypatch):
    """A session only exists on a trading day. 2026-10-03 is a Saturday."""
    seen = {}

    def fake_row(service, symbol, root_date, token=None, is_index=False):
        seen['date'] = root_date.date()
        return None

    monkeypatch.setattr(
        'trading_app.filters.tpo_single_print_scanner.get_tpo_row', fake_row)
    monkeypatch.setattr(
        'trading_app.filters.cpr_camarilla_scanner._scan_universe',
        lambda svc: [('ACME', None, False)])

    result = filter_tpo_singles(_StubService({}), root_date=datetime(2026, 10, 3))
    assert seen['date'] == date(2026, 10, 2)
    assert result['date'] == '2026-10-02'
    assert result['scanned'] == 1 and result['skipped'] == 1
