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
