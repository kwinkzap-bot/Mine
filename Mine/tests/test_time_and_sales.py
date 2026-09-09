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
def clean_tape():
    """A fresh store per test, pinned to today so the rollover never fires."""
    tas.reset()
    tas._tape_day = datetime.now(IST).date()
    with mock.patch.object(tas, '_market_is_open', return_value=True):
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


def test_a_restored_snapshot_recomputes_its_own_coverage(tmp_path):
    """An older snapshot carries backfill volume in its stored total, which
    would keep reporting ~100% all day. Deriving it from the rows heals it."""
    push(last_traded_time=1001, last_traded_qty=100, vol_traded_today=1000)
    push(last_traded_time=1002, last_traded_qty=100, vol_traded_today=6000)
    with mock.patch.object(tas, '_TAPE_DIR', str(tmp_path)):
        tas.save_snapshot(force=True)
        rows_before = len(rows())
        # A stale, inflated total of the kind the old code wrote.
        with open(tas._snapshot_path(tas._today()), 'rb') as fh:
            import pickle
            blob = pickle.load(fh)
        blob['coverage'] = {SYMBOL: [999999, 1000000]}
        with open(tas._snapshot_path(tas._today()), 'wb') as fh:
            pickle.dump(blob, fh)
        tas.reset()
        tas.load_snapshot()
    assert len(rows()) == rows_before
    assert tas.coverage(SYMBOL) == pytest.approx(100 / 5000, rel=1e-3)


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
