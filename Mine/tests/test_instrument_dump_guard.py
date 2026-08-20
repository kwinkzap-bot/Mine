"""The truncated-instrument-dump guard in KiteService.

On 2026-08-20 the Fyers NFO master downloaded with 4,125 rows against a normal
~76,000. It was cached under that day's date, so NIFTY spent the session holding
three futures rows and no strikes at all: the Round Strike block could not fill
its dropdowns, never asked for option legs, and drew an empty chart.

These tests never touch the real `.cache/*_instruments_v3.pkl` — every path here
is a tmp_path — and never reach the network. `_load_instruments` itself computes
its cache path from `__file__`, which is exactly why the decision lives in the
three helpers exercised below.
"""

import pickle

from trading_app.service.kite_order_services import KiteService, _INSTRUMENT_MIN_RATIO

# What a healthy download looked like on 2026-08-20.
GOOD = {'NSE': 9950, 'NFO': 79722, 'BSE': 12773, 'BFO': 7513}


def test_healthy_dump_is_accepted():
    assert KiteService._truncated_segments(GOOD, GOOD) == []


def test_the_incident_is_rejected():
    """NFO(4125) against a stored NFO(79722) — 5%, the case that broke the block."""
    incident = dict(GOOD, NFO=4125)
    short = KiteService._truncated_segments(incident, GOOD)
    assert len(short) == 1
    assert 'NFO 4125 vs 79722' in short[0]


def test_a_short_bfo_is_caught_too():
    """The mirror case: NFO fine, BSE options short, which would break SENSEX."""
    assert KiteService._truncated_segments(dict(GOOD, BFO=200), GOOD)


def test_ordinary_expiry_churn_is_not_rejected():
    """Contract counts move with the expiry cycle; that must not trip the guard."""
    churn = {seg: int(n * 0.8) for seg, n in GOOD.items()}
    assert KiteService._truncated_segments(churn, GOOD) == []
    # ...and the boundary itself is the documented ratio, not an accident.
    at_floor = {seg: int(n * _INSTRUMENT_MIN_RATIO) + 1 for seg, n in GOOD.items()}
    assert KiteService._truncated_segments(at_floor, GOOD) == []


def test_first_run_has_nothing_to_compare_against():
    """No previous counts (fresh install) — accept, or nothing would ever load."""
    assert KiteService._truncated_segments({'NFO': 12}, {}) == []
    # A segment that was empty before says nothing about what it should be now.
    assert KiteService._truncated_segments({'NFO': 12}, {'NFO': 0}) == []


def test_segment_counts_round_trip(tmp_path):
    cache = tmp_path / 'fyers_instruments_v3.pkl'
    cache.write_bytes(pickle.dumps({'tokens_by_symbol': {}, 'tokens_by_name': {},
                                    'nfo_by_name': {}, 'segment_counts': GOOD}))
    assert KiteService._cached_segment_counts(str(cache)) == GOOD


def test_missing_or_unreadable_cache_reads_as_no_counts(tmp_path):
    assert KiteService._cached_segment_counts(str(tmp_path / 'nope.pkl')) == {}
    junk = tmp_path / 'junk.pkl'
    junk.write_bytes(b'not a pickle')
    assert KiteService._cached_segment_counts(str(junk)) == {}
    # A pickle written before this guard existed has no counts and must not raise.
    old = tmp_path / 'old.pkl'
    old.write_bytes(pickle.dumps({'tokens_by_symbol': {}, 'nfo_by_name': {}}))
    assert KiteService._cached_segment_counts(str(old)) == {}


def test_rejection_falls_back_to_the_previous_cache(tmp_path):
    """The point of the guard: yesterday's complete master keeps serving."""
    nifty = [{'tradingsymbol': 'NIFTY26AUG24100CE', 'name': 'NIFTY', 'strike': 24100.0,
              'instrument_type': 'CE', 'expiry': None, 'instrument_token': 't1'}]
    cache = tmp_path / 'fyers_instruments_v3.pkl'
    cache.write_bytes(pickle.dumps({'tokens_by_symbol': {'NIFTY26AUG24100CE': 't1'},
                                    'tokens_by_name': {'nifty': 'n1'},
                                    'nfo_by_name': {'nifty': nifty},
                                    'segment_counts': GOOD}))

    svc = KiteService.__new__(KiteService)          # no __init__: no network, no real cache
    assert svc._adopt_previous_cache(str(cache), 'fyers-test') is True
    # The strikes the Round Strike block needs are there, which is the whole point.
    assert svc.get_nfo_instruments('NIFTY') == nifty
    assert svc._instrument_tokens_by_symbol == {'NIFTY26AUG24100CE': 't1'}


def test_fallback_reports_failure_when_there_is_no_cache(tmp_path):
    svc = KiteService.__new__(KiteService)
    assert svc._adopt_previous_cache(str(tmp_path / 'absent.pkl'), 'fyers-test') is False
