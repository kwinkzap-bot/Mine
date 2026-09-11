"""The Time and Sales tape — the rules that decide what counts as a trade.

The Fyers websocket cannot be exercised in a test, so what is asserted here is
everything that shapes the tape once a push has arrived: which pushes become
rows, which are duplicates, which side of the spread each print is attributed
to, and the seam where the ICICI 1-second backfill meets the live feed.

Getting these wrong is silent and damaging in specific ways. A weak dedupe rule
inflates the tape with trades that never happened. A wrong aggressor rule
paints the colour that a trader reads direction from. And a backfill that runs
twice, or that fails to cede to the live feed at the boundary, double-counts
volume the user is trying to reason about.
"""

from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from trading_app.service import time_and_sales as tas

IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = 'NSE:TESTFUT'


@pytest.fixture(autouse=True)
def clean_tape(tmp_path):
    """A fresh store per test, pinned to today so the rollover never fires.

    The snapshot dir and the archive are pointed at tmp_path for EVERY test:
    a top-up that stands down for the day now writes both, and the real
    data/tape/ holds the only copy of days nobody can re-record.
    """
    tas.reset()
    tas._tape_day = datetime.now(IST).date()
    tape_dir = tmp_path / 'tape'
    with mock.patch.object(tas, '_market_is_open', return_value=True), \
         mock.patch.object(tas, '_TAPE_DIR', str(tape_dir)), \
         mock.patch.object(tas, '_ARCHIVE_DB', str(tape_dir / 'tape.db')):
        yield
    tas.reset()


def push(**kw):
    """One socket push, with sensible defaults for the fields a tape reads."""
    msg = {'symbol': SYMBOL, 'ltp': 100.0, 'vol_traded_today': 0,
           'last_traded_time': 1000, 'last_traded_qty': 65,
           'bid_price': 99.9, 'ask_price': 100.1}
    msg.update(kw)
    tas._on_message(msg)


def rows():
    return tas.rows_since(SYMBOL, 0, 5000)[0]


# ── the emit rule ──────────────────────────────────────────────────────────

def test_a_print_is_emitted_only_when_the_exchange_stamp_advances():
    push(last_traded_time=1000)
    push(last_traded_time=1000)          # the same trade, pushed again
    assert len(rows()) == 1


def test_a_push_older_than_the_last_one_is_ignored():
    push(last_traded_time=1005)
    push(last_traded_time=1004)
    assert [r['ts'] for r in rows()] == [1005]


def test_without_an_exchange_stamp_a_volume_move_is_what_counts():
    push(last_traded_time=None, vol_traded_today=100)
    push(last_traded_time=None, vol_traded_today=100)   # nothing traded
    push(last_traded_time=None, vol_traded_today=165)
    out = rows()
    assert len(out) == 2
    # No per-trade size is knowable on this path, so it must not invent one.
    assert all(r['qty'] is None for r in out)


def test_nothing_is_recorded_while_the_market_is_closed():
    # A pre-open push still carries the previous close; recording it would
    # plant a fake print at yesterday's price.
    with mock.patch.object(tas, '_market_is_open', return_value=False):
        push(last_traded_time=2000)
    assert rows() == []


def test_a_malformed_push_never_escapes():
    tas._on_message({'garbage': True})
    tas._on_message(None)
    tas._on_message({'symbol': SYMBOL})          # no ltp
    assert rows() == []


# ── aggressor classification ───────────────────────────────────────────────

def test_a_trade_at_the_ask_is_a_buy_and_at_the_bid_a_sell():
    push(last_traded_time=1001, ltp=100.1, bid_price=99.9, ask_price=100.1)
    push(last_traded_time=1002, ltp=99.9, bid_price=99.9, ask_price=100.1)
    out = rows()
    assert [r['side'] for r in out] == ['buy', 'sell']
    assert all(r['side_rule'] == 'quote' for r in out)


def test_inside_the_spread_it_falls_back_to_the_tick_test():
    push(last_traded_time=1001, ltp=100.00, bid_price=99.0, ask_price=101.0)
    push(last_traded_time=1002, ltp=100.05, bid_price=99.0, ask_price=101.0)
    push(last_traded_time=1003, ltp=100.01, bid_price=99.0, ask_price=101.0)
    out = rows()
    assert [r['side'] for r in out[1:]] == ['buy', 'sell']
    assert all(r['side_rule'] == 'tick' for r in out)


def test_an_unchanged_price_inherits_the_previous_side():
    # The zero-tick rule: a flat run of prints must not flicker green/red.
    # Establish a direction first — the very first print of a session has no
    # previous trade to compare against and is legitimately directionless.
    push(last_traded_time=1001, ltp=100.0, bid_price=99.0, ask_price=101.0)
    push(last_traded_time=1002, ltp=100.5, bid_price=99.0, ask_price=101.0)
    push(last_traded_time=1003, ltp=100.5, bid_price=99.0, ask_price=101.0)
    out = rows()
    assert out[0]['side'] == 'flat'
    assert out[1]['side'] == 'buy'
    assert out[2]['side'] == 'buy'          # inherited, not re-derived


def test_a_missing_book_still_classifies_by_tick():
    push(last_traded_time=1001, ltp=100.0, bid_price=None, ask_price=None)
    push(last_traded_time=1002, ltp=100.9, bid_price=None, ask_price=None)
    assert rows()[1] == pytest.approx(rows()[1])       # sanity
    assert rows()[1]['side'] == 'buy'
    assert rows()[1]['side_rule'] == 'tick'


# ── sequencing, coverage, bounds ───────────────────────────────────────────

def test_seq_is_dense_and_monotonic():
    for i in range(5):
        push(last_traded_time=1000 + i)
    assert [r['seq'] for r in rows()] == [1, 2, 3, 4, 5]


def test_rows_since_returns_only_what_the_client_lacks():
    for i in range(5):
        push(last_traded_time=1000 + i)
    out, truncated = tas.rows_since(SYMBOL, 3, 500)
    assert [r['seq'] for r in out] == [4, 5]
    assert truncated is False


def test_rows_since_truncates_to_the_newest_when_over_limit():
    for i in range(10):
        push(last_traded_time=1000 + i)
    out, truncated = tas.rows_since(SYMBOL, 0, 3)
    assert truncated is True
    assert [r['seq'] for r in out] == [8, 9, 10]


def test_vol_delta_records_what_the_tape_could_not_attribute():
    # The feed is throttled, so most volume moves between the prints we see.
    # Recording the delta is what makes the gap visible instead of missing.
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=65, vol_traded_today=6000)
    assert rows()[1]['vol_delta'] == 5000
    assert tas.coverage(SYMBOL) == pytest.approx(65 / 5000, rel=1e-3)


def test_coverage_is_not_inflated_by_backfilled_bars():
    """A 1-second bar accounts for its own volume by construction.

    Counting those would have the tape trivially "cover" itself: a panel
    showing mostly backfill reported 98% when the live feed's real capture is
    around 42%. Only live prints may count toward the figure.
    """
    fake = mock.Mock()
    fake.historical_data.return_value = [
        {'date': datetime.fromtimestamp(1000 + i, IST), 'open': 10, 'close': 11,
         'volume': 5000} for i in range(3)
    ]
    with mock.patch('trading_app.service.provider_logic.get_icici_adapter',
                    return_value=fake):
        tas._backfill(SYMBOL)
    assert len(rows()) == 3
    assert tas.coverage(SYMBOL) is None       # 15,000 of backfill volume, still None


def test_coverage_stays_quiet_until_it_has_a_real_sample():
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=100)
    push(last_traded_time=1002, last_traded_qty=65, vol_traded_today=200)
    assert tas.coverage(SYMBOL) is None


def test_the_store_is_bounded():
    with mock.patch.object(tas, 'MAX_ROWS_PER_SYMBOL', 10):
        tas.reset(SYMBOL)
        for i in range(25):
            push(last_traded_time=1000 + i)
        held = rows()
    assert len(held) <= 10
    assert held[-1]['ts'] == 1024          # the newest survive, not the oldest


# ── filtering and session stats ────────────────────────────────────────────

def test_min_qty_filters_over_the_whole_tape_not_a_page_of_it():
    """The browser only ever holds a window, so the threshold must apply here.

    Filtering client-side reported "0 rows" for a session that held 28 matching
    ones: the answer depended on how long the tab had been open, which is not a
    filter.
    """
    for i, qty in enumerate([65, 9000, 130, 5200, 65]):
        push(last_traded_time=1000 + i, last_traded_qty=qty,
             vol_traded_today=1000 * (i + 1))
    out, _ = tas.rows_since(SYMBOL, 0, 500, min_qty=5000)
    assert [r['qty'] for r in out] == [9000, 5200]


def test_min_qty_and_since_compose():
    for i, qty in enumerate([9000, 130, 8000]):
        push(last_traded_time=1000 + i, last_traded_qty=qty,
             vol_traded_today=1000 * (i + 1))
    out, _ = tas.rows_since(SYMBOL, 1, 500, min_qty=5000)
    assert [r['qty'] for r in out] == [8000]        # the first is behind the cursor


def test_stats_cover_the_whole_tape():
    """The rail used to derive these from the client's capped row window, so
    the print count simply reported the cap."""
    push(last_traded_time=1001, ltp=100.5, last_traded_qty=65,
         bid_price=99.0, ask_price=100.0, vol_traded_today=1000)     # buy
    push(last_traded_time=1002, ltp=99.0, last_traded_qty=130,
         bid_price=99.0, ask_price=100.0, vol_traded_today=2000)     # sell
    push(last_traded_time=1003, ltp=100.5, last_traded_qty=1365,
         bid_price=99.0, ask_price=100.0, vol_traded_today=3000)     # buy
    st = tas.stats(SYMBOL)
    assert st['prints'] == 3
    assert st['flow_buy'] == 65 + 1365
    assert st['flow_sell'] == 130
    assert st['max_tick_qty'] == 1365
    assert st['last_price'] == 100.5


def test_stats_separate_single_prints_from_aggregated_bars():
    """A single exchange print is a few lots; only an aggregated 1-second bar
    reaches the thousands. The UI needs both to explain an empty filter."""
    fake = mock.Mock()
    fake.historical_data.return_value = [
        {'date': datetime.fromtimestamp(900 + i, IST), 'open': 10, 'close': 11,
         'volume': v} for i, v in enumerate([300, 31785])
    ]
    with mock.patch('trading_app.service.provider_logic.get_icici_adapter',
                    return_value=fake):
        tas._backfill(SYMBOL)
    push(last_traded_time=2000, last_traded_qty=65, vol_traded_today=5000)
    st = tas.stats(SYMBOL)
    assert st['bars'] == 2 and st['prints'] == 1
    assert st['max_bar_qty'] == 31785
    assert st['max_tick_qty'] == 65          # never conflated with the bar


def _snapshot_roundtrip(tmp_path, mutate=None):
    """Save, optionally corrupt the file the way an older build wrote it, restore."""
    import pickle
    with mock.patch.object(tas, '_TAPE_DIR', str(tmp_path)):
        tas.save_snapshot(force=True)
        path = tas._snapshot_path(tas._today())
        if mutate is not None:
            with open(path, 'rb') as fh:
                blob = pickle.load(fh)
            mutate(blob)
            with open(path, 'wb') as fh:
                pickle.dump(blob, fh)
        tas.reset()
        tas.load_snapshot()


def test_a_pre_versioned_snapshot_recomputes_its_own_coverage(tmp_path):
    """An older snapshot carries backfill volume in its stored total, which
    would keep reporting ~100% all day. Deriving it from the rows heals it.

    'cov_v' is what says a snapshot's totals already exclude bars. Dropping it
    is what makes this file an old one — the totals alone cannot be told apart.
    """
    push(last_traded_time=1001, last_traded_qty=100, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=100, vol_traded_today=6000)
    rows_before = len(rows())

    def age_it(blob):
        blob.pop('cov_v', None)
        blob.pop('seen', None)
        blob['coverage'] = {SYMBOL: [999999, 1000000]}   # the inflated total

    _snapshot_roundtrip(tmp_path, age_it)
    assert len(rows()) == rows_before
    assert tas.coverage(SYMBOL) == pytest.approx(100 / 5000, rel=1e-3)
    # The session counters are gone from an old file too, and are rebuilt from
    # the rows — right for a tape no top-up has rebuilt, which is what it holds.
    assert tas.stats(SYMBOL)['prints'] == 2


def test_a_current_snapshot_keeps_the_totals_it_stored(tmp_path):
    """After a top-up the live prints are gone from the rows, so rederiving
    would measure the last minute of feed and call it the session. A snapshot
    that says its totals are already bar-free is believed instead."""
    push(last_traded_time=1001, last_traded_qty=100, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=100, vol_traded_today=6000)
    before = tas.coverage(SYMBOL)
    seen_before = tas.stats(SYMBOL)['prints']

    # A top-up settles both prints into one Σ bar covering their seconds.
    tas._rebuild(SYMBOL, tas._bar_rows(
        [{'date': 1002, 'open': 10, 'close': 11, 'volume': 5000}]), 1002)
    assert [r['src'] for r in rows()] == ['bar']

    _snapshot_roundtrip(tmp_path)
    assert tas.coverage(SYMBOL) == before
    assert tas.stats(SYMBOL)['prints'] == seen_before == 2


def test_a_client_can_ask_for_the_whole_session_in_one_call():
    """The tape is served over loopback, where a full session is ~850KB and
    costs milliseconds. Paging it would buy only round-trips and a spinner
    halfway down a scroll, so the limit ceiling is the store's own cap."""
    for i in range(1200):
        push(last_traded_time=1000 + i, last_traded_qty=65,
             vol_traded_today=100 * (i + 1))
    out, truncated = tas.rows_since(SYMBOL, 0, tas.MAX_ROWS_PER_SYMBOL)
    assert len(out) == 1200
    assert truncated is False
    # and a small limit still truncates to the NEWEST rows, never the oldest
    out2, truncated2 = tas.rows_since(SYMBOL, 0, 50)
    assert truncated2 is True
    assert out2[-1]['seq'] == 1200


# ── the backfill seam ──────────────────────────────────────────────────────

def test_a_live_print_the_backfill_already_covered_is_dropped():
    tas._BACKFILL_HIGH_TS[SYMBOL] = 5000
    push(last_traded_time=4999)
    push(last_traded_time=5000)
    push(last_traded_time=5001)
    assert [r['ts'] for r in rows()] == [5001]


def test_live_pushes_are_held_off_while_the_backfill_runs():
    # Otherwise history and live rows interleave and the seam is unreadable.
    tas._BACKFILL[SYMBOL] = 'running'
    push(last_traded_time=1001)
    assert rows() == []


def test_backfill_is_skipped_when_the_tape_already_has_rows():
    """A reopened tab, or a snapshot restore, must not re-lay history."""
    push(last_traded_time=1001)
    fake = mock.Mock()
    fake.historical_data.return_value = [
        {'date': datetime.fromtimestamp(1002, IST), 'open': 1, 'close': 2, 'volume': 50},
    ]
    with mock.patch('trading_app.service.provider_logic.get_icici_adapter',
                    return_value=fake):
        tas._backfill(SYMBOL)
    assert len(rows()) == 1
    assert tas._BACKFILL[SYMBOL] == 'ready'


def test_backfilled_rows_are_marked_as_aggregated():
    """A 1-second bar's volume is many trades; it must never look like one."""
    fake = mock.Mock()
    fake.historical_data.return_value = [
        {'date': datetime.fromtimestamp(1000, IST), 'open': 10, 'close': 11, 'volume': 300},
        {'date': datetime.fromtimestamp(1001, IST), 'open': 11, 'close': 10, 'volume': 150},
        {'date': datetime.fromtimestamp(1002, IST), 'open': 10, 'close': 10, 'volume': 0},
    ]
    with mock.patch('trading_app.service.provider_logic.get_icici_adapter',
                    return_value=fake):
        tas._backfill(SYMBOL)
    out = rows()
    assert [r['src'] for r in out] == ['bar', 'bar']      # the 0-volume bar is dropped
    assert [r['side'] for r in out] == ['buy', 'sell']
    assert [r['qty'] for r in out] == [300, 150]
    assert tas._BACKFILL_HIGH_TS[SYMBOL] == 1001


def test_backfill_reports_why_it_could_not_run():
    with mock.patch('trading_app.service.provider_logic.get_icici_adapter',
                    return_value=None):
        tas._backfill(SYMBOL)
    assert tas._BACKFILL[SYMBOL].startswith('unavailable:')
    assert rows() == []


# ── lifecycle ──────────────────────────────────────────────────────────────

def test_registering_beyond_the_cap_evicts_the_coldest():
    with mock.patch.object(tas, '_ensure_supervisor'), \
         mock.patch.object(tas, '_backfill'):
        for i in range(tas.MAX_TAPED_SYMBOLS + 2):
            tas.register(f'SYM{i}')
    assert len(tas._HOT) == tas.MAX_TAPED_SYMBOLS
    assert 'SYM0' not in tas._HOT


def test_the_day_rollover_clears_yesterdays_tape():
    # The app runs for days under the LaunchAgent; without this, Monday opens
    # showing Friday's prints.
    push(last_traded_time=1001)
    assert rows()
    tas._tape_day = datetime.now(IST).date() - timedelta(days=1)
    tas._roll_day_if_needed()
    assert rows() == []


# ── history catching up over the live prints ───────────────────────────────
#
# The seam the backfill leaves is not a gap in TIME — the socket covers every
# second after it — it is a change of UNIT. A backfilled row is one second of
# the exchange's whole volume; a live row is one throttled snapshot's
# last-traded-quantity. Measured on 2026-09-10 (NIFTY26SEPFUT) the medians were
# 260 and 65, and of 4,084 live prints exactly one cleared a 1,000 filter that
# 56 of 464 bars cleared. So the tape read as a wall of Σ rows from the open and
# then almost nothing, and the fix is that history keeps catching up.

def _topup_with(bars, complete=True):
    """Run one top-up against a fake Breeze that answers with `bars`."""
    fake = mock.Mock()
    fake.historical_seconds_range.return_value = (bars, complete)
    with mock.patch('trading_app.service.provider_logic.get_icici_adapter',
                    return_value=fake):
        tas._topup(SYMBOL)
    return fake


def _bar(ts, volume, open_=10, close=11):
    return {'date': ts, 'open': open_, 'close': close, 'volume': volume}


def test_a_topup_replaces_the_prints_whose_seconds_it_now_covers():
    """Both would otherwise stand for the same trades — the print as itself,
    and again inside the Σ bar. Appending is a double count, not a merge."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=65, vol_traded_today=2000)
    push(last_traded_time=1900, last_traded_qty=65, vol_traded_today=3000)

    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([_bar(1001, 500), _bar(1002, 700)])

    out = rows()
    assert [(r['ts'], r['src'], r['qty']) for r in out] == [
        (1001, 'bar', 500), (1002, 'bar', 700), (1900, 'tick', 65)]
    # and the tape is still dense and ascending in BOTH seq and time
    assert [r['seq'] for r in out] == [1, 2, 3]
    assert out == sorted(out, key=lambda r: r['ts'])


def test_a_print_ahead_of_the_frontier_is_left_alone():
    """It is the only thing that can answer for a second history has not
    reached, so the leading edge must survive every pass."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    push(last_traded_time=1500, last_traded_qty=65, vol_traded_today=1000)
    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([_bar(1001, 500)])
    assert [(r['ts'], r['src']) for r in rows()] == [(1001, 'bar'), (1500, 'tick')]


def test_a_topup_that_comes_back_empty_moves_nothing():
    """Advancing the frontier on an empty answer would delete the live prints
    covering those seconds and put nothing in their place — a real hole, made
    by the code meant to close a cosmetic one."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000)
    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([])
    assert [(r['ts'], r['src']) for r in rows()] == [(1001, 'tick')]
    assert tas._BACKFILL_HIGH_TS[SYMBOL] == 1000
    assert tas._EPOCH.get(SYMBOL, 0) == 0


@pytest.mark.parametrize('complete', [True, False])
def test_the_frontier_only_claims_the_ground_it_actually_fetched(complete):
    """_rebuild deletes the live prints below the frontier, so a window Breeze
    answered short of would delete prints and leave those seconds empty — a
    real hole, made by the code closing a cosmetic one. True of a
    budget-limited pass and of a short answer to a whole one alike."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([_bar(1001, 500), _bar(1002, 700)], complete=complete)
    assert tas._BACKFILL_HIGH_TS[SYMBOL] == 1002


def test_a_partial_catchup_comes_straight_back_for_the_rest():
    """A tab opened at 14:20 is five hours behind and catches up over several
    passes; idling a minute between them would take until the close."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([_bar(1001, 500)], complete=False)
    assert tas._TOPUP_AT[SYMBOL] <= tas.monotonic() + 1


def test_the_frontier_never_reaches_the_second_still_forming():
    """Its bar is half-built, and no later pass would correct a partial volume
    frozen into the tape."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    with mock.patch.object(tas, '_tape_day', tas._today()):
        fake = _topup_with([_bar(1001, 500)])
    _, end = fake.historical_seconds_range.call_args[0][1:3]
    now = datetime.now(IST)
    assert (now - end).total_seconds() >= tas._TOPUP_LAG_SEC


def test_a_rebuild_tells_the_client_its_cursor_has_moved():
    """Seqs are renumbered from 1, so a cursor from before the rebuild points
    at different rows now. Stitching onto it silently corrupts the tape."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=65, vol_traded_today=2000)
    held = tas.view(SYMBOL, 0, 500)
    assert held['truncated'] is False

    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([_bar(1001, 500), _bar(1002, 700)])

    stale = tas.view(SYMBOL, held['next_seq'], 500, 0, held['epoch'])
    assert stale['truncated'] is True
    assert stale['epoch'] != held['epoch']
    assert len(stale['rows']) == 2                # the whole tape, not a delta

    fresh = tas.view(SYMBOL, stale['next_seq'], 500, 0, stale['epoch'])
    assert fresh['truncated'] is False and fresh['rows'] == []


def test_the_print_count_survives_the_prints_being_replaced():
    """It describes what the LIVE FEED saw all session — the number the Feed
    percentage is about. Derived from the rows it would fall toward zero as
    history caught up and report the last minute as the day."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=910, vol_traded_today=2000)
    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([_bar(1001, 500), _bar(1002, 700)])
    st = tas.stats(SYMBOL)
    assert st['prints'] == 2                 # both, though neither is a row now
    assert st['max_tick_qty'] == 910         # still explains an empty filter
    assert st['bars'] == 2


def test_flow_counts_every_row_exactly_once():
    """Flow describes what the tape is SHOWING. Summing prints as well as the
    Σ bars that now stand for their seconds would count those trades twice."""
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000,
         bid_price=99.0, ask_price=100.0)                        # at the ask: a buy
    push(last_traded_time=1900, last_traded_qty=65, vol_traded_today=2000,
         bid_price=99.0, ask_price=100.0)
    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([_bar(1001, 500, open_=10, close=11)])       # a buy bar
    st = tas.stats(SYMBOL)
    assert st['flow_buy'] + st['flow_sell'] == 500 + 65


def test_a_topup_stops_asking_once_the_session_is_over():
    """The tab stays open after the close. At the retry rate that is a Breeze
    request every five minutes, all evening, for a tape that cannot change."""
    tas._BACKFILL[SYMBOL] = 'ready'
    close_ts = tas._session_bounds(tas._today())[1]
    tas._BACKFILL_HIGH_TS[SYMBOL] = close_ts - 60          # the last trade of the day

    after_close = datetime.fromtimestamp(close_ts + 3600, IST)
    with mock.patch.object(tas, '_tape_day', tas._today()), \
         mock.patch.object(tas, 'datetime') as clock:
        clock.now.return_value = after_close
        clock.fromtimestamp = datetime.fromtimestamp
        clock.combine = datetime.combine               # _session_bounds needs the real one
        _topup_with([])                                    # nothing left to fetch
    assert tas._TOPUP_AT[SYMBOL] >= tas.monotonic() + tas._TOPUP_BACKOFF_MAX


def test_a_topup_stands_down_while_the_first_pass_owns_the_tape():
    """The backfill lays down history from an empty store; a top-up merging
    into one halfway built would race it."""
    tas._BACKFILL[SYMBOL] = 'running'
    with mock.patch.object(tas, '_topup') as spy:
        tas._maybe_topup(SYMBOL)
    spy.assert_not_called()


# ── the archive ────────────────────────────────────────────────────────────

def _archive_today():
    tas.save_snapshot(force=True)
    return tas.archive_snapshots()


def _big(min_qty=1000, symbol=SYMBOL, days=None):
    today = tas._today()
    lo, hi = days or (today - timedelta(days=30), today)
    return tas.archived_large_prints(symbol, min_qty, lo, hi)


def test_the_days_tape_is_archived_for_good():
    """What the pickle holds today, the archive holds forever — same rows,
    keyed on the day the pickle was named for."""
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=6500, vol_traded_today=8000)
    push(last_traded_time=1003, last_traded_qty=12000, vol_traded_today=20000)
    assert _archive_today() == 3
    out = _big(min_qty=1000)
    assert [(r['ts'], r['qty']) for r in out] == [(1002, 6500), (1003, 12000)]
    assert set(out[0]) == {'ts', 'price', 'qty', 'side', 'src'}   # same rows as large_prints
    assert _big(min_qty=1000, days=(tas._today() + timedelta(days=1),) * 2) == []


def test_an_unchanged_pickle_is_not_archived_twice():
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000)
    assert _archive_today() == 1
    gen = tas.archive_generation()
    assert tas.archive_snapshots() == 0
    assert tas.archive_generation() == gen                      # nothing written, no bump


def test_a_rebuilt_tape_replaces_its_archived_day_rather_than_adding_to_it():
    """A top-up renumbers every seq after its seam, so the same trades come
    back under new keys. The archive must end up with the tape, not both."""
    import os
    tas._BACKFILL[SYMBOL] = 'ready'
    tas._BACKFILL_HIGH_TS[SYMBOL] = 1000
    push(last_traded_time=1001, last_traded_qty=65, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=65, vol_traded_today=2000)
    push(last_traded_time=1900, last_traded_qty=9000, vol_traded_today=12000)
    assert _archive_today() == 3
    gen = tas.archive_generation()

    with mock.patch.object(tas, '_tape_day', tas._today()):
        _topup_with([_bar(1001, 500), _bar(1002, 7000)])
    tas.save_snapshot(force=True)
    path = tas._snapshot_path(tas._today())
    os.utime(path, (os.path.getmtime(path) + 5,) * 2)           # newer than the ledger
    assert tas.archive_snapshots() == 3
    assert tas.archive_generation() == gen + 1
    assert [(r['ts'], r['qty'], r['src']) for r in _big(min_qty=1000)] == [
        (1002, 7000, 'bar'), (1900, 9000, 'tick')]


def test_an_old_format_pickle_archives_too(tmp_path):
    """tape_2026-09-09.pkl predates cov_v/seen/epoch. Its rows are what matter."""
    import pickle
    day = tas._today() - timedelta(days=2)
    tape_dir = tmp_path / 'tape'
    tape_dir.mkdir(exist_ok=True)
    blob = {'prints': {SYMBOL: [
        {'ts': 1001, 'price': 100.0, 'qty': 9000, 'side': 'buy', 'side_rule': 'quote',
         'src': 'tick', 'vol_delta': 9000, 'seq': 1}]},
        'seq': {SYMBOL: 2}, 'last': {}, 'backfill': {}, 'high_ts': {}, 'coverage': {}}
    with open(tape_dir / f'tape_{day.isoformat()}.pkl', 'wb') as fh:
        pickle.dump(blob, fh)
    assert tas.archive_snapshots() == 1
    assert [r['ts'] for r in _big(min_qty=8000, days=(day, day))] == [1001]


def test_prune_keeps_a_day_the_archive_has_not_taken(tmp_path):
    """The pickle is the only copy of its day until the archive has read it."""
    import pickle
    tape_dir = tmp_path / 'tape'
    tape_dir.mkdir(exist_ok=True)
    row = {'ts': 1001, 'price': 100.0, 'qty': 65, 'side': 'buy', 'side_rule': 'quote',
           'src': 'tick', 'vol_delta': 65, 'seq': 1}
    old = tas._today() - timedelta(days=10)
    older = tas._today() - timedelta(days=11)
    for day in (old, older):
        with open(tape_dir / f'tape_{day.isoformat()}.pkl', 'wb') as fh:
            pickle.dump({'prints': {SYMBOL: [row]}}, fh)

    tas.prune_snapshots(keep_days=5)
    assert sorted(p.name for p in tape_dir.glob('tape_*.pkl')) == [
        f'tape_{older.isoformat()}.pkl', f'tape_{old.isoformat()}.pkl']

    tas.archive_snapshots()
    tas.prune_snapshots(keep_days=5)
    assert list(tape_dir.glob('tape_*.pkl')) == []
    assert _big(min_qty=1, days=(older, old)) and len(_big(min_qty=1, days=(older, old))) == 2


def test_standing_down_for_the_day_archives_the_tape():
    """The moment the top-up knows the session is over is the moment the tape
    is final — and the one trigger that does not need the app alive later."""
    tas._BACKFILL[SYMBOL] = 'ready'
    close_ts = tas._session_bounds(tas._today())[1]
    tas._BACKFILL_HIGH_TS[SYMBOL] = close_ts - 60
    push(last_traded_time=close_ts - 30, last_traded_qty=9000, vol_traded_today=9000)

    after_close = datetime.fromtimestamp(close_ts + 3600, IST)
    with mock.patch.object(tas, '_tape_day', tas._today()), \
         mock.patch.object(tas, 'datetime') as clock:
        clock.now.return_value = after_close
        clock.fromtimestamp = datetime.fromtimestamp
        clock.combine = datetime.combine
        _topup_with([])
    assert [r['qty'] for r in _big(min_qty=8000)] == [9000]


def test_an_unreadable_archive_answers_empty_not_broken(tmp_path):
    with mock.patch.object(tas, '_ARCHIVE_DB', str(tmp_path / 'nope' / 'tape.db')):
        assert _big() == []


# ── Round Strike's use of it ───────────────────────────────────────────────

def test_round_strike_tags_the_bar_a_big_print_landed_in():
    from trading_app.app.routes.api import _rs_tag_big_prints
    ist = 19800
    bars = [{'time': 1000 + ist, 'volume': 1}, {'time': 1060 + ist, 'volume': 1},
            {'time': 1120 + ist, 'volume': 1}]
    prints = [{'ts': 1005, 'qty': 8000}, {'ts': 1010, 'qty': 9500},   # same bar: max wins
              {'ts': 1119, 'qty': 8100},                              # last second of bar 2
              {'ts': 1200, 'qty': 8200}]                              # past the last bar
    out = _rs_tag_big_prints(bars, prints, 'minute', ist)
    assert [(b.get('big'), b.get('big_qty')) for b in out] == [
        (True, 9500), (True, 8100), (None, None)]
    assert _rs_tag_big_prints(bars, [], 'minute', ist) is bars      # untouched with nothing to tag


def test_round_strike_builds_the_fyers_symbol_the_archive_is_keyed_on():
    from trading_app.app.routes.api import _rs_fyers_future_symbol
    from datetime import date
    assert _rs_fyers_future_symbol('NIFTY', date(2026, 9, 29)) == 'NSE:NIFTY26SEPFUT'
    assert _rs_fyers_future_symbol('BANKNIFTY', date(2027, 1, 28)) == 'NSE:BANKNIFTY27JANFUT'
    assert _rs_fyers_future_symbol('SENSEX', date(2026, 12, 31)) == 'BSE:SENSEX26DECFUT'
