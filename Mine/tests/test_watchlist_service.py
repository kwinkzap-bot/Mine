"""Watchlist service: storage scoping, row shaping, and the PE derivation.

No network and no create_app(): the symbol master, Yahoo fundamentals and the
broker quote are all stubbed, so this exercises the logic that is actually
ours — who owns which tab, what the 52-week columns compute, and when a
reported EPS series is trustworthy enough to draw a PE line from.
"""

import os
import sys
import tempfile
from datetime import datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.service.watchlist_service as wl


UNIVERSE = [
    {'symbol': 'RELIANCE', 'fy_symbol': 'NSE:RELIANCE-EQ', 'kind': 'EQ',
     'company': 'RELIANCE INDUSTRIES LTD', 'yf_symbol': 'RELIANCE.NS'},
    {'symbol': 'RELIGARE', 'fy_symbol': 'NSE:RELIGARE-EQ', 'kind': 'EQ',
     'company': 'RELIGARE ENTERPRISES LTD', 'yf_symbol': 'RELIGARE.NS'},
    {'symbol': 'NIFTY', 'fy_symbol': 'NSE:NIFTY50-INDEX', 'kind': 'INDEX',
     'company': 'NIFTY50-INDEX', 'yf_symbol': '^NSEI'},
    {'symbol': 'NIFTYBEES', 'fy_symbol': 'NSE:NIFTYBEES-EQ', 'kind': 'EQ',
     'company': 'NIPPON INDIA ETF NIFTY 50', 'yf_symbol': 'NIFTYBEES.NS'},
]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """A temp DB and a stubbed universe — never the live oi_data.db."""
    monkeypatch.setattr(wl, 'DB_PATH', str(tmp_path / 'watchlist.db'))
    monkeypatch.setattr(wl, '_schema_ready', False)
    monkeypatch.setattr(wl, '_load_universe', lambda: UNIVERSE)
    # Nothing in this module may reach the broker or Yahoo.
    monkeypatch.setattr(wl, '_live_quotes', lambda syms: {})
    monkeypatch.setattr(wl, '_fetch_fundamentals',
                        lambda sym: pytest.fail(f'unexpected Yahoo fetch for {sym}'))


def _fundamentals(**rows):
    """Seed the fundamentals cache so rows() reads it instead of fetching."""
    wl._ensure_schema()
    records = []
    for yf_symbol, values in rows.items():
        record = {k: None for k in (
            'company', 'sector', 'industry', 'pe', 'eps', 'high52', 'low52',
            'market_cap', 'pb', 'div_yield', 'yf_price', 'yf_prev')}
        record.update(values)
        record['yf_symbol'] = yf_symbol
        record['fetched_at'] = wl.time.time()
        records.append(record)
    wl._store_fundamentals(records)


# ── tabs and items ───────────────────────────────────────────────────────

def test_tabs_are_scoped_to_their_owner():
    mine = wl.create_tab('Mine', 'Core')['tab']['id']

    # A second user sees none of it, and cannot reach it by guessing the id.
    assert wl.list_tabs('Kavin') == []
    assert wl.rename_tab('Kavin', mine, 'Hijacked')['success'] is False
    assert wl.delete_tab('Kavin', mine)['success'] is False
    assert wl.add_item('Kavin', mine, 'RELIANCE')['success'] is False
    assert wl.rows('Kavin', mine)['success'] is False

    assert [t['name'] for t in wl.list_tabs('Mine')] == ['Core']


def test_duplicate_tab_name_is_rejected_case_insensitively():
    wl.create_tab('Mine', 'Core')
    result = wl.create_tab('Mine', 'core')
    assert result['success'] is False
    assert 'already exists' in result['error']


def test_item_add_remove_and_duplicate():
    tab = wl.create_tab('Mine', 'Core')['tab']['id']

    added = wl.add_item('Mine', tab, 'RELIANCE')
    assert added['success'] is True
    assert wl.add_item('Mine', tab, 'RELIANCE')['success'] is False
    assert wl.add_item('Mine', tab, 'NOTALISTEDCO')['success'] is False
    assert wl.list_tabs('Mine')[0]['count'] == 1

    # An item is only removable through its owner's session.
    assert wl.remove_item('Kavin', added['item']['id'])['success'] is False
    assert wl.remove_item('Mine', added['item']['id'])['success'] is True
    assert wl.list_tabs('Mine')[0]['count'] == 0


def test_deleting_a_tab_takes_its_items_with_it():
    tab = wl.create_tab('Mine', 'Core')['tab']['id']
    wl.add_item('Mine', tab, 'RELIANCE')
    wl.delete_tab('Mine', tab)

    with wl._connect() as conn:
        left = conn.execute("SELECT COUNT(*) FROM watchlist_items").fetchone()[0]
    assert left == 0


# ── search ───────────────────────────────────────────────────────────────

def test_search_ranks_symbol_prefix_first_and_indices_above_equities():
    assert [r['symbol'] for r in wl.search('RELI')] == ['RELIANCE', 'RELIGARE']
    # NIFTY the index must beat NIFTYBEES the ETF, which merely starts the same.
    assert wl.search('NIFTY')[0]['symbol'] == 'NIFTY'
    # Company-name matches are found too, just after the symbol matches.
    assert [r['symbol'] for r in wl.search('NIPPON')] == ['NIFTYBEES']
    assert wl.search('') == []


# ── grid rows ────────────────────────────────────────────────────────────

def test_row_derives_the_52_week_position_and_falls_back_to_yahoo():
    tab = wl.create_tab('Mine', 'Core')['tab']['id']
    wl.add_item('Mine', tab, 'RELIANCE')
    _fundamentals(**{'RELIANCE.NS': {
        'company': 'Reliance Industries Limited', 'sector': 'Energy',
        'pe': 24.0, 'eps': 55.0, 'high52': 1600.0, 'low52': 1200.0,
        'market_cap': 1.77e13, 'yf_price': 1300.0, 'yf_prev': 1310.0,
    }})

    row = wl.rows('Mine', tab)['rows'][0]

    # No broker session, so the price and previous close come from Yahoo and
    # the row is flagged as not live.
    assert row['live'] is False
    assert row['ltp'] == 1300.0
    assert row['change'] == pytest.approx(-10.0)
    assert row['change_pct'] == pytest.approx(-0.7633, abs=1e-3)

    assert row['from_high'] == pytest.approx(-18.75)
    assert row['from_low'] == pytest.approx(8.333, abs=1e-3)
    assert row['band52'] == pytest.approx(25.0)


def test_broker_quote_wins_over_the_cached_yahoo_price(monkeypatch):
    tab = wl.create_tab('Mine', 'Core')['tab']['id']
    wl.add_item('Mine', tab, 'RELIANCE')
    _fundamentals(**{'RELIANCE.NS': {'yf_price': 1300.0, 'yf_prev': 1310.0,
                                     'high52': 1600.0, 'low52': 1200.0}})
    monkeypatch.setattr(wl, '_live_quotes',
                        lambda syms: {'NSE:RELIANCE-EQ': {'ltp': 1350.0, 'prev_close': 1340.0}})

    row = wl.rows('Mine', tab)['rows'][0]
    assert (row['live'], row['ltp'], row['prev_close']) == (True, 1350.0, 1340.0)


def test_missing_fundamentals_leave_blanks_rather_than_zeros():
    tab = wl.create_tab('Mine', 'Core')['tab']['id']
    wl.add_item('Mine', tab, 'NIFTY')
    _fundamentals(**{'^NSEI': {'high52': 26000.0, 'low52': 22000.0, 'yf_price': 24000.0}})

    row = wl.rows('Mine', tab)['rows'][0]
    # An index has no PE or market cap. None, not 0 — a zero PE would sort to
    # the top of the column as if it were the cheapest thing on the list.
    assert row['pe'] is None and row['market_cap'] is None
    assert row['kind'] == 'INDEX'
    assert row['band52'] == pytest.approx(50.0)


# ── EPS series behind the PE line ────────────────────────────────────────

class _FakeTicker:
    def __init__(self, quarterly=None, annual=None):
        self.quarterly_income_stmt = quarterly
        self.income_stmt = annual


def _stmt(label, pairs):
    return pd.DataFrame({pd.Timestamp(d): {label: v} for d, v in pairs})


def test_quarterly_windows_spanning_a_gap_are_skipped():
    # Four clean quarters, then a two-quarter hole, then two more. Only the
    # first window is four consecutive quarters.
    quarterly = _stmt('Diluted EPS', [
        ('2024-03-31', 10.0), ('2024-06-30', 11.0),
        ('2024-09-30', 12.0), ('2024-12-31', 13.0),
        ('2025-09-30', 14.0), ('2025-12-31', 15.0),
    ])
    steps = wl._eps_steps(_FakeTicker(quarterly=quarterly), current_eps=46.0)

    assert [(d.date().isoformat(), v) for d, v in steps] == [('2024-12-31', 46.0)]


def test_annual_statement_is_the_fallback_when_no_clean_quarter_window():
    quarterly = _stmt('Diluted EPS', [('2026-03-31', 12.0), ('2026-06-30', 13.0)])
    annual = _stmt('Diluted EPS', [('2025-03-31', 51.0), ('2026-03-31', 59.0)])

    steps = wl._eps_steps(_FakeTicker(quarterly=quarterly, annual=annual), current_eps=55.0)
    assert [v for _, v in steps] == [51.0, 59.0]


def test_a_reported_series_that_disagrees_with_the_quote_is_discarded():
    """INFY.NS ships 'Diluted EPS 0.8' against a trailing EPS near 77.

    Drawing a PE line off that puts the whole series two orders of magnitude
    out, so the series is dropped and the caller falls back to a flat
    current-EPS basis.
    """
    annual = _stmt('Diluted EPS', [('2025-03-31', 0.76), ('2026-03-31', 0.80)])
    assert wl._eps_steps(_FakeTicker(annual=annual), current_eps=77.27) == []

    # ... and the same shape of series is kept when it does agree.
    annual = _stmt('Diluted EPS', [('2025-03-31', 70.0), ('2026-03-31', 77.0)])
    assert wl._eps_steps(_FakeTicker(annual=annual), current_eps=77.27) != []


def test_finite_rejects_the_nan_yahoo_ships_freely():
    # A NaN reaching jsonify becomes a bare `NaN` token that JSON.parse
    # rejects, blanking the whole grid over one missing field.
    assert wl._finite(float('nan')) is None
    assert wl._finite(float('inf')) is None
    assert wl._finite(None) is None
    assert wl._finite('') is None
    assert wl._finite('12.5') == 12.5


# ── moving a symbol between tabs ──────────────────────────────────────────

def test_move_carries_the_row_across_rather_than_re_adding_it():
    """A move must not go back through the symbol master.

    Re-adding would: NSE retired TATAMOTORS for TMCV/TMPV mid-2026, so a
    symbol that has since been delisted or renamed could not be put back and
    the row would simply vanish on its way between two tabs.
    """
    core = wl.create_tab('Mine', 'Core')['tab']['id']
    swing = wl.create_tab('Mine', 'Swing')['tab']['id']
    item = wl.add_item('Mine', core, 'RELIANCE')['item']['id']

    # The symbol leaves the universe after it was added.
    monkey_universe = [r for r in UNIVERSE if r['symbol'] != 'RELIANCE']
    original = wl._load_universe
    wl._load_universe = lambda: monkey_universe
    try:
        assert wl.move_item('Mine', item, swing) == {'success': True, 'moved': True,
                                                     'symbol': 'RELIANCE'}
    finally:
        wl._load_universe = original

    counts = {t['name']: t['count'] for t in wl.list_tabs('Mine')}
    assert counts == {'Core': 0, 'Swing': 1}


def test_move_is_refused_across_users_and_into_a_tab_that_has_it():
    core = wl.create_tab('Mine', 'Core')['tab']['id']
    swing = wl.create_tab('Mine', 'Swing')['tab']['id']
    theirs = wl.create_tab('Kavin', 'Theirs')['tab']['id']
    item = wl.add_item('Mine', core, 'RELIANCE')['item']['id']
    wl.add_item('Mine', swing, 'RELIANCE')

    # Neither end of the move is reachable from another user's session.
    assert wl.move_item('Kavin', item, theirs)['success'] is False
    assert wl.move_item('Mine', item, theirs)['success'] is False

    clash = wl.move_item('Mine', item, swing)
    assert clash['success'] is False and 'already in that tab' in clash['error']
    # The refusal left it where it was.
    assert {t['name']: t['count'] for t in wl.list_tabs('Mine')} == {'Core': 1, 'Swing': 1}


def test_moving_into_its_own_tab_is_a_no_op():
    core = wl.create_tab('Mine', 'Core')['tab']['id']
    item = wl.add_item('Mine', core, 'RELIANCE')['item']['id']
    assert wl.move_item('Mine', item, core) == {'success': True, 'moved': False}
    assert wl.list_tabs('Mine')[0]['count'] == 1


def test_move_respects_the_target_tabs_capacity(monkeypatch):
    monkeypatch.setattr(wl, 'MAX_ITEMS_PER_TAB', 1)
    core = wl.create_tab('Mine', 'Core')['tab']['id']
    full = wl.create_tab('Mine', 'Full')['tab']['id']
    item = wl.add_item('Mine', core, 'RELIANCE')['item']['id']
    wl.add_item('Mine', full, 'NIFTY')

    result = wl.move_item('Mine', item, full)
    assert result['success'] is False and 'already holds' in result['error']


def test_a_moved_symbol_lands_at_the_end_of_the_target_tab():
    core = wl.create_tab('Mine', 'Core')['tab']['id']
    swing = wl.create_tab('Mine', 'Swing')['tab']['id']
    wl.add_item('Mine', swing, 'NIFTY')
    item = wl.add_item('Mine', core, 'RELIANCE')['item']['id']
    wl.move_item('Mine', item, swing)

    _fundamentals(**{'^NSEI': {'yf_price': 24000.0}, 'RELIANCE.NS': {'yf_price': 1300.0}})
    assert [r['symbol'] for r in wl.rows('Mine', swing)['rows']] == ['NIFTY', 'RELIANCE']


def test_annual_and_quarterly_steps_are_merged_to_span_a_long_chart():
    """Yahoo's quarterly statement reaches back ~5 quarters, so on its own a
    5-year chart draws a P/E line over its last year and nothing before it
    (KTKBANK, 2026-08-20). The annual statement covers the earlier years.
    """
    quarterly = _stmt('Diluted EPS', [
        ('2025-09-30', 10.0), ('2025-12-31', 10.0),
        ('2026-03-31', 10.0), ('2026-06-30', 10.0),
    ])
    annual = _stmt('Diluted EPS', [
        ('2023-03-31', 30.0), ('2024-03-31', 34.0), ('2026-03-31', 38.0),
    ])

    steps = wl._eps_steps(_FakeTicker(quarterly=quarterly, annual=annual), current_eps=40.0)
    dated = [(d.date().isoformat(), v) for d, v in steps]

    # Every annual year dated before the first quarterly window, then the
    # quarterly TTM figure. The FY26 annual is kept rather than superseded:
    # between April and June 2026 it really was the latest reported TTM, and
    # the quarterly window ending 2026-06-30 only takes over once it exists.
    assert dated == [('2023-03-31', 30.0), ('2024-03-31', 34.0),
                     ('2026-03-31', 38.0), ('2026-06-30', 40.0)]
    # Ascending, because eps_at() walks the list and stops at the first step
    # dated after the day it is pricing.
    assert dated == sorted(dated)


# ── CPR + Camarilla overlay ──────────────────────────────────────────────

def _daily_frame(rows):
    """A daily OHLC frame indexed by date, as yfinance hands one back."""
    index = pd.DatetimeIndex([pd.Timestamp(d) for d, *_ in rows])
    return pd.DataFrame(
        {'High':  [r[1] for r in rows],
         'Low':   [r[2] for r in rows],
         'Close': [r[3] for r in rows]},
        index=index)


def test_levels_come_from_the_previous_period_not_the_current_one():
    """A level you can trade against has to be knowable before the period
    starts, which means it is derived from the period before it."""
    frame = _daily_frame([
        # Week 1 (Mon-Wed): high 120, low 80, last close 100.
        ('2026-01-05', 110, 90, 95), ('2026-01-06', 120, 80, 105), ('2026-01-07', 115, 95, 100),
        # Week 2 — its levels must come from week 1's numbers above.
        ('2026-01-12', 200, 150, 180), ('2026-01-13', 210, 160, 190),
    ])
    levels = wl._period_levels(frame, 'W')

    # Only the second week gets levels; the first has nothing before it.
    assert len(levels) == 1
    got = levels[0]
    assert got['from'] == '2026-01-12', 'levels apply from the first day of their period'

    high, low, close = 120, 80, 100
    pivot = (high + low + close) / 3          # 100.0
    bc = (high + low) / 2                     # 100.0
    tc = 2 * pivot - bc                       # 100.0
    assert got['p'] == pytest.approx(round(pivot, 2))
    assert got['bc'] == pytest.approx(round(min(bc, tc), 2))
    assert got['tc'] == pytest.approx(round(max(bc, tc), 2))
    # Camarilla's third level: close ± (high - low) x 1.1/4.
    assert got['r3'] == pytest.approx(round(close + (high - low) * 1.1 / 4, 2))
    assert got['s3'] == pytest.approx(round(close - (high - low) * 1.1 / 4, 2))


def test_tc_and_bc_are_returned_high_side_first():
    """The raw formulas can put TC below BC. Whichever way round they come
    out, TC has to be the upper edge of the band or the chart draws it
    inside out."""
    frame = _daily_frame([
        ('2026-02-02', 100, 50, 95),      # close near the high -> TC above BC
        ('2026-02-09', 120, 110, 115),
        ('2026-02-16', 100, 50, 55),      # close near the low  -> raw TC below BC
        ('2026-02-23', 120, 110, 115),
    ])
    for got in wl._period_levels(frame, 'W'):
        assert got['tc'] >= got['bc']
        assert got['s3'] <= got['r3']


def test_a_single_period_yields_no_levels():
    frame = _daily_frame([('2026-03-02', 100, 90, 95), ('2026-03-03', 101, 91, 96)])
    assert wl._period_levels(frame, 'W') == []
    assert wl._period_levels(None, 'W') == []


def test_month_alias_works_on_either_pandas():
    """Month-end is 'M' before pandas 2.2 and 'ME' after; this app pins
    neither, so the caller passes both and the first one that parses wins."""
    frame = _daily_frame([
        ('2026-01-05', 110, 90, 100), ('2026-01-20', 120, 80, 105),
        ('2026-02-03', 130, 100, 120), ('2026-02-17', 140, 110, 130),
        ('2026-03-03', 150, 120, 140),
    ])
    levels = wl._period_levels(frame, ('ME', 'M'))
    assert [r['from'] for r in levels] == ['2026-02-03', '2026-03-03']


# ── timeframe -> CPR period ──────────────────────────────────────────────

def test_each_timeframe_reads_against_the_one_above_it():
    """The rule the chart is built on: intraday reads against the day,
    hourly against the week, daily against the month, weekly and monthly
    against the year. Derived, never picked — a hand-picked period is how a
    5-minute chart ends up carrying yearly pivots.
    """
    expected = {
        '1m': 'Daily', '5m': 'Daily', '15m': 'Daily', '30m': 'Daily',
        '1h': 'Weekly',
        '1d': 'Monthly',
        '1wk': 'Yearly', '1mo': 'Yearly',
    }
    assert {tf: spec['cpr_label'] for tf, spec in wl.INTERVALS.items()} == expected


def test_intraday_timeframes_stay_inside_yahoos_history_caps():
    """Yahoo answers a too-wide intraday window with an error, not a
    truncation: 1m is capped at the last 7 days and 5m/15m/30m at 60, and
    asking 30m for '2mo' returns nothing at all rather than 60 days of bars.
    """
    caps = {'1m': 7, '5m': 60, '15m': 60, '30m': 60}
    days = {'5d': 5, '1mo': 31, '2mo': 62, '6mo': 183}
    for tf, cap in caps.items():
        requested = days[wl.INTERVALS[tf]['period']]
        assert requested <= cap, f'{tf} asks for {requested}d against a {cap}d cap'

    # Only the intraday timeframes carry a time on each bar.
    assert [tf for tf, s in wl.INTERVALS.items() if s['intraday']] == \
           ['1m', '5m', '15m', '30m', '1h']


def test_an_unknown_timeframe_is_refused_without_a_fetch(monkeypatch):
    monkeypatch.setattr(wl, '_resolve',
                        lambda s: pytest.fail('resolved a symbol for a bad timeframe'))
    result = wl.candles('RELIANCE', '4h')     # Yahoo has no 4h
    assert result['success'] is False
    assert 'Unsupported timeframe' in result['error']


def test_yearly_levels_come_from_the_previous_year():
    frame = _daily_frame([
        ('2024-06-03', 100, 60, 80), ('2024-11-01', 120, 50, 90),
        ('2025-06-02', 200, 150, 180),
        ('2026-06-01', 300, 250, 280),
    ])
    levels = wl._period_levels(frame, ('YE', 'A'))
    assert [r['from'] for r in levels] == ['2025-06-02', '2026-06-01']

    # 2025's levels are built from 2024's high 120 / low 50 / close 90.
    got = levels[0]
    assert got['p'] == pytest.approx(round((120 + 50 + 90) / 3, 2))
    assert got['r3'] == pytest.approx(round(90 + (120 - 50) * 1.1 / 4, 2))


# ── bulk add (the holdings import) ───────────────────────────────────────

def test_bulk_add_reports_each_symbol_and_never_removes():
    """The import is additive. A watchlist is not a position report — a
    symbol added by hand, or one since sold, must survive an import.
    """
    tab = wl.create_tab('Mine', 'Devanai Kite')['tab']['id']
    wl.add_item('Mine', tab, 'NIFTY')          # added by hand, not a holding
    wl.add_item('Mine', tab, 'RELIANCE')       # already held

    result = wl.add_items('Mine', tab, ['RELIANCE', 'RELIGARE', 'NOTALISTEDCO'])

    assert result['added'] == ['RELIGARE']
    assert result['already'] == ['RELIANCE']
    assert [s['symbol'] for s in result['skipped']] == ['NOTALISTEDCO']

    # Nothing was dropped, including the hand-added index.
    _fundamentals(**{'^NSEI': {'yf_price': 24000.0}, 'RELIANCE.NS': {'yf_price': 1300.0},
                     'RELIGARE.NS': {'yf_price': 250.0}})
    assert {r['symbol'] for r in wl.rows('Mine', tab)['rows']} == \
           {'NIFTY', 'RELIANCE', 'RELIGARE'}


def test_bulk_add_stops_once_the_tab_is_full(monkeypatch):
    """A full tab rejects every remaining symbol for the same reason, so it
    is reported once rather than forty times."""
    monkeypatch.setattr(wl, 'MAX_ITEMS_PER_TAB', 2)
    tab = wl.create_tab('Mine', 'Core')['tab']['id']

    result = wl.add_items('Mine', tab, ['RELIANCE', 'RELIGARE', 'NIFTY', 'NIFTYBEES'])

    assert result['added'] == ['RELIANCE', 'RELIGARE']
    assert len(result['skipped']) == 1
    assert 'At most' in result['skipped'][0]['error']


def test_bulk_add_is_scoped_to_the_owner():
    tab = wl.create_tab('Mine', 'Core')['tab']['id']
    result = wl.add_items('Kavin', tab, ['RELIANCE'])
    assert result['added'] == []
    assert result['skipped'][0]['error'] == 'Tab not found'


# ── broker-owned tabs ────────────────────────────────────────────────────

BROKER_ENV = {
    'BROKER_1_ACTIVE': 'true', 'BROKER_1_TYPE': 'zerodha', 'BROKER_1_NAME': 'Saranya (Kite)',
    'BROKER_2_ACTIVE': 'true', 'BROKER_2_TYPE': 'zerodha', 'BROKER_2_NAME': 'Devanai (Kite)',
    'BROKER_4_ACTIVE': 'false', 'BROKER_4_TYPE': 'kotak', 'BROKER_4_NAME': 'Kavin (Kotak Neo)',
}


@pytest.fixture
def brokers(monkeypatch):
    monkeypatch.setattr(
        'trading_app.app.utils.user_env.UserEnvManager.get_user_var',
        staticmethod(lambda username, var, default='': BROKER_ENV.get(var, default)))


def test_a_tab_named_for_a_broker_is_recognised(brokers):
    for name, expected in [('Devanai Kite', 'Devanai (Kite)'),
                           ('Saranya Kite', 'Saranya (Kite)'),
                           ('devanai kite', 'Devanai (Kite)'),   # case and spacing
                           ('Devanai', 'Devanai (Kite)')]:       # unique partial
        match = wl.broker_for_tab('Mine', name)
        assert match and match['name'] == expected, name

    # Names that belong to no one account.
    assert wl.broker_for_tab('Mine', 'BEES') is None
    assert wl.broker_for_tab('Mine', 'Kite') is None       # matches two — ambiguous
    assert wl.broker_for_tab('Mine', '') is None
    # An inactive slot is not a broker as far as this is concerned.
    assert wl.broker_for_tab('Mine', 'Kavin Kotak Neo') is None


def test_a_broker_tab_and_its_rows_cannot_be_deleted(brokers):
    tab = wl.create_tab('Mine', 'Devanai Kite')['tab']['id']
    item = wl.add_item('Mine', tab, 'RELIANCE')['item']['id']

    dropped = wl.remove_item('Mine', item)
    assert dropped['success'] is False
    assert 'Devanai (Kite)' in dropped['error']

    deleted = wl.delete_tab('Mine', tab)
    assert deleted['success'] is False
    assert 'rename it first' in deleted['error']

    # Both are still there.
    assert wl.list_tabs('Mine')[0]['count'] == 1


def test_renaming_away_from_the_broker_makes_a_tab_deletable_again(brokers):
    """The link is the name, so renaming is the deliberate way out — the
    alternative is a tab nobody can ever remove."""
    tab = wl.create_tab('Mine', 'Devanai Kite')['tab']['id']
    wl.add_item('Mine', tab, 'RELIANCE')
    assert wl.delete_tab('Mine', tab)['success'] is False

    wl.rename_tab('Mine', tab, 'Old positions')
    assert wl.delete_tab('Mine', tab)['success'] is True


def test_manual_tabs_stay_deletable(brokers):
    tab = wl.create_tab('Mine', 'BEES')['tab']['id']
    item = wl.add_item('Mine', tab, 'NIFTYBEES')['item']['id']
    assert wl.remove_item('Mine', item)['success'] is True
    assert wl.delete_tab('Mine', tab)['success'] is True


def test_list_tabs_says_which_broker_each_tab_follows(brokers):
    wl.create_tab('Mine', 'Devanai Kite')
    wl.create_tab('Mine', 'BEES')
    by_name = {t['name']: t['broker'] for t in wl.list_tabs('Mine')}

    assert by_name['Devanai Kite']['name'] == 'Devanai (Kite)'
    assert by_name['Devanai Kite']['instance'] == 2
    assert by_name['BEES'] is None


def test_an_explicit_binding_beats_an_ambiguous_name(brokers):
    """"Saran" matches both Saranya (Kite) and Saranya (Dhan), so the name
    alone names neither. Saying which account outright is the way out."""
    ambiguous = {**BROKER_ENV,
                 'BROKER_5_ACTIVE': 'true', 'BROKER_5_TYPE': 'dhan',
                 'BROKER_5_NAME': 'Saranya (Dhan)'}
    import trading_app.app.utils.user_env as ue
    ue.UserEnvManager.get_user_var = staticmethod(
        lambda username, var, default='': ambiguous.get(var, default))

    tab = wl.create_tab('Mine', 'Saran')['tab']['id']
    assert wl.list_tabs('Mine')[0]['broker'] is None      # cannot be guessed

    assert wl.set_tab_broker('Mine', tab, 1)['success'] is True
    bound = wl.list_tabs('Mine')[0]['broker']
    assert bound['name'] == 'Saranya (Kite)' and bound['instance'] == 1

    # And a bound tab is protected exactly like a name-matched one.
    item = wl.add_item('Mine', tab, 'RELIANCE')['item']['id']
    assert wl.remove_item('Mine', item)['success'] is False
    assert wl.delete_tab('Mine', tab)['success'] is False

    # Unbinding hands it back.
    assert wl.set_tab_broker('Mine', tab, None)['success'] is True
    assert wl.list_tabs('Mine')[0]['broker'] is None
    assert wl.remove_item('Mine', item)['success'] is True


def test_binding_to_an_inactive_broker_is_refused(brokers):
    tab = wl.create_tab('Mine', 'Whatever')['tab']['id']
    result = wl.set_tab_broker('Mine', tab, 9)
    assert result['success'] is False and 'not active' in result['error']
