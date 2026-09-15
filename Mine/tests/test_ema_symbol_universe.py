"""EMA_SYMBOL_DEFAULTS is the strategy's WHOLE universe, not a set of overrides.

The property these tests protect: nothing anywhere invents Direction/Target for
a symbol outside the table. It used to fall back to Both/5%, which meant a
typo'd or delisted ticker ran a backtest on made-up parameters and came back
looking like a real result for a stock outside the strategy's universe.

No create_app(): the bare route app from route_app.py carries the same URL map
and starts no scheduler (see CLAUDE.md).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.app.routes.api as api
from route_app import build_route_app
from trading_app.Backtest.ema_symbol_universe import EMA_SYMBOL_DEFAULTS


@pytest.fixture
def client(monkeypatch):
    # check_auth would build a data provider; the gate under test must reject
    # before any of that, so it is stubbed and get_data_provider is booby-
    # trapped — a rejected symbol must never reach a broker at all.
    monkeypatch.setattr(api, 'check_auth', lambda: None)
    monkeypatch.setattr(api, 'get_data_provider',
                        lambda *a, **k: pytest.fail('provider built for a rejected symbol'))
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


# ── The table itself ─────────────────────────────────────────────────────


def test_every_row_is_well_formed():
    assert EMA_SYMBOL_DEFAULTS, 'the universe must not be empty'
    for symbol, cfg in EMA_SYMBOL_DEFAULTS.items():
        assert symbol == symbol.strip().upper(), f'{symbol}: not a clean upper-case ticker'
        assert cfg['direction'] in ('long', 'short', 'both'), f'{symbol}: bad direction'
        # EmaPullbackEngine floors target_pct at 1.0 — a row below that would
        # silently trade a different target than the table claims.
        assert float(cfg['target_pct']) >= 1.0, f'{symbol}: target_pct under the engine floor'


# ── The gate ─────────────────────────────────────────────────────────────


def test_backtest_refuses_a_symbol_outside_the_universe(client):
    resp = client.post('/api/backtest/ema-pullback', json={
        'symbol': 'NOTALISTEDSTOCK', 'start_date': '2017-01-01', 'end_date': '2026-01-01'})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body['success'] is False
    assert 'not an EMA Confluence Breakout symbol' in body['error']


def test_optimise_refuses_a_symbol_outside_the_universe(client):
    resp = client.post('/api/backtest/ema-pullback/optimise', json={
        'symbol': 'NOTALISTEDSTOCK', 'start_date': '2017-01-01', 'end_date': '2026-01-01'})
    assert resp.status_code == 400
    assert 'not an EMA Confluence Breakout symbol' in resp.get_json()['error']


def test_a_listed_symbol_gets_past_the_gate(client, monkeypatch):
    """The gate must reject only what is outside the table — a listed symbol
    proceeds (and then fails on the stubbed provider, which is how we know it
    got past)."""
    monkeypatch.setattr(api, 'get_data_provider', lambda *a, **k: None)
    resp = client.post('/api/backtest/ema-pullback', json={
        'symbol': next(iter(EMA_SYMBOL_DEFAULTS)),
        'start_date': '2017-01-01', 'end_date': '2026-01-01'})
    assert resp.status_code == 401          # provider failure, not the gate
    assert 'not an EMA Confluence Breakout symbol' not in resp.get_json()['error']


def test_all_stocks_is_still_accepted(client, monkeypatch):
    monkeypatch.setattr(api, 'get_data_provider', lambda *a, **k: None)
    for spelling in ('ALL', 'ALL STOCKS', 'ALL_STOCKS'):
        resp = client.post('/api/backtest/ema-pullback', json={
            'symbol': spelling, 'start_date': '2017-01-01', 'end_date': '2026-01-01'})
        assert resp.status_code == 401, f'{spelling} was rejected by the symbol gate'


def test_symbol_defaults_endpoint_serves_the_whole_table(client):
    """The form prefills from this, and now also decides from it whether a
    symbol is in the universe at all."""
    resp = client.get('/api/backtest/ema-pullback/symbol-defaults')
    assert resp.status_code == 200
    assert resp.get_json()['defaults'] == EMA_SYMBOL_DEFAULTS


# ── The backtest's Target comes from the table, never from a guess ───────
# The strategy's Target is a % of the entry price, so an invented one is an
# invented exit. This pins the route that used to fall back to a bare 5.0 —
# a number belonging to no symbol in the table.


def test_the_backtest_route_defaults_to_the_symbols_own_direction_and_target(client, monkeypatch):
    """A request that omits Direction/Target runs the symbol on its own row of
    the table, rather than on a blanket Both/5%."""
    seen = {}

    def _capture(daily_df, enable_long, enable_short, target_pct, require_rr, start_date):
        seen.update(enable_long=enable_long, enable_short=enable_short, target_pct=target_pct)
        raise RuntimeError('stop here — the parameters are what this test is about')

    import trading_app.Backtest.ema_pullback_engine as eng
    monkeypatch.setattr(api, 'get_data_provider',
                        lambda *a, **k: type('P', (), {
                            'historical_data': lambda self, **kw: [{'date': '2020-01-01',
                                                                   'open': 1, 'high': 1,
                                                                   'low': 1, 'close': 1}]})())
    monkeypatch.setattr(eng, 'EmaPullbackEngine',
                        lambda **kw: _capture(**kw))

    # SBIN is 'long' / 3% in the table; the payload names neither.
    resp = client.post('/api/backtest/ema-pullback', json={
        'symbol': 'SBIN', 'start_date': '2017-01-01', 'end_date': '2026-01-01'})
    assert resp.status_code == 500          # our deliberate stop
    assert seen['target_pct'] == float(EMA_SYMBOL_DEFAULTS['SBIN']['target_pct'])
    assert seen['enable_long'] is True and seen['enable_short'] is False
