"""EMA_SYMBOL_DEFAULTS is the strategy's WHOLE universe, not a set of overrides.

The property these tests protect: nothing anywhere invents Direction/Target for
a symbol outside the table. It used to fall back to Both/5%, which meant a
typo'd or delisted ticker ran a backtest on made-up parameters and came back
looking like a real result for a stock the live algo will never trade.

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


def test_the_live_algo_scans_exactly_this_table():
    """The backtest and the live algo must not disagree about the universe."""
    from trading_app.algo.ema_confluence import ema_confluence_algo as algo
    fresh = algo.EmaConfluenceAlgo.__new__(algo.EmaConfluenceAlgo)._fresh_state()
    assert set(fresh['stocks']) == set(EMA_SYMBOL_DEFAULTS)


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


# ── The entry's Target comes from the table, never from a guess ──────────
# The strategy's Target is a % of the entry price, so an invented one is an
# invented exit. These pin the two paths that used to fall back to a bare 5.0
# — a number belonging to no symbol in the table.


@pytest.fixture
def algo():
    from trading_app.algo.ema_confluence import ema_confluence_algo as mod
    # __new__, not the constructor: no thread, no state file, no scheduler.
    a = mod.EmaConfluenceAlgo.__new__(mod.EmaConfluenceAlgo)
    a.log = mod._PrefixLogger(mod.logger, {})
    return a


def test_target_comes_from_the_armed_setup_when_it_has_one(algo):
    assert algo._configured_target_pct('SBIN', {'target_pct': 3}) == 3.0


def test_target_falls_back_to_the_symbols_own_row_not_a_blanket_five(algo):
    """A state row that lost its target_pct is rebuilt from the universe."""
    symbol, cfg = next(iter(EMA_SYMBOL_DEFAULTS.items()))
    assert algo._configured_target_pct(symbol, {}) == float(cfg['target_pct'])


def test_a_symbol_outside_the_universe_has_no_target_at_all(algo):
    assert algo._configured_target_pct('NOTALISTEDSTOCK', {}) is None


def test_an_entry_is_refused_rather_than_opened_on_a_guessed_target(algo):
    """The old code armed a 5% target here and looked normal doing it."""
    state = {'phase': 'watching', 'direction': 'Long', 'trigger_level': 100.0,
             'sl_level': 95.0, 'lot_size': 1}
    algo._fire_paper_entry('NOTALISTEDSTOCK', state, spot=101.0, fut_ltp=101.5, lots=1)
    assert state['phase'] == 'watching', 'a position was opened without a configured Target'
    assert 'target_level' not in state and 'entry_price' not in state


def test_a_listed_symbol_still_enters_normally(algo, monkeypatch):
    """The guard must refuse only the unconfigured case."""
    monkeypatch.setattr(algo, '_notify_new_entry', lambda *a, **k: None)
    symbol, cfg = next(iter(EMA_SYMBOL_DEFAULTS.items()))
    state = {'phase': 'watching', 'direction': 'Long', 'trigger_level': 100.0,
             'sl_level': 95.0, 'lot_size': 1}          # no target_pct stored
    algo._fire_paper_entry(symbol, state, spot=100.0, fut_ltp=101.0, lots=1)
    assert state['phase'] == 'in_position'
    # Target is that symbol's own %, off the SPOT fill (see _fire_paper_entry).
    assert state['target_level'] == pytest.approx(
        round(100.0 * (1 + float(cfg['target_pct']) / 100), 2))


def test_the_backtest_route_defaults_to_the_symbols_own_direction_and_target(client, monkeypatch):
    """A request that omits Direction/Target runs the symbol the way the live
    algo runs it, rather than on a blanket Both/5%."""
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


# ── A symbol removed from the table is retired from the live state ───────
# EMA_SYMBOL_DEFAULTS is the universe, so the algo's state file must not
# outlive it. The `watching` case is the one with teeth: the tick loop drives
# armed symbols off the STATE file, not off the table, so a removed stock left
# behind could still open a position.


@pytest.fixture
def retire(algo, monkeypatch):
    """_retire_dropped_symbols with its exits captured instead of written."""
    booked = []
    monkeypatch.setattr(algo, '_record_exit',
                        lambda sym, s, px, reason: booked.append((sym, px, reason)))
    return algo, booked


def _state(**rows):
    return {'stocks': {sym: dict(row) for sym, row in rows.items()}}


def test_a_dropped_symbol_with_no_setup_is_removed(retire):
    algo, _ = retire
    state = _state(GONE={'phase': 'no_setup'})
    algo._retire_dropped_symbols(state, {})
    assert state['stocks'] == {}


def test_a_dropped_symbols_armed_trigger_is_discarded(retire):
    """Left behind it could still fire an entry in a stock we removed."""
    algo, booked = retire
    state = _state(GONE={'phase': 'watching', 'direction': 'Short',
                         'trigger_level': 100.0, 'sl_level': 110.0})
    algo._retire_dropped_symbols(state, {})
    assert state['stocks'] == {}
    assert booked == [], 'nothing to book — a watching symbol holds no position'


def test_a_dropped_symbols_open_position_is_booked_out_before_removal(retire):
    """It must reach the trade history with its P&L, not vanish unrecorded."""
    algo, booked = retire
    state = _state(GONE={'phase': 'in_position', 'direction': 'Short', 'qty': 1425,
                         'entry_price': 406.65, 'ltp': 413.25})
    algo._retire_dropped_symbols(state, {})
    assert booked == [('GONE', 413.25, 'UNIVERSE_DROP')]
    assert state['stocks'] == {}


def test_an_open_position_with_no_mark_is_kept_rather_than_booked_at_a_made_up_price(retire):
    algo, booked = retire
    state = _state(GONE={'phase': 'in_position', 'direction': 'Short', 'qty': 1,
                         'entry_price': 100.0})           # never quoted
    algo._retire_dropped_symbols(state, {})
    assert booked == []
    assert 'GONE' in state['stocks'], 'a position was dropped without being booked'


def test_symbols_still_in_the_universe_are_untouched(retire):
    algo, booked = retire
    keep = next(iter(EMA_SYMBOL_DEFAULTS))
    state = _state(**{keep: {'phase': 'in_position', 'direction': 'Long', 'qty': 1,
                             'entry_price': 10.0, 'ltp': 11.0}})
    algo._retire_dropped_symbols(state, EMA_SYMBOL_DEFAULTS)
    assert set(state['stocks']) == {keep}
    assert booked == []


def test_a_failure_while_retiring_never_costs_the_whole_state(algo, monkeypatch, tmp_path):
    """_load_state's outer except returns a FRESH state, which would abandon
    every open position — the retirement pass must not be able to reach it."""
    from trading_app.algo.ema_confluence import ema_confluence_algo as mod
    state_file = tmp_path / 'state.json'
    keep = next(iter(EMA_SYMBOL_DEFAULTS))
    state_file.write_text(json.dumps({'stocks': {
        keep: {'phase': 'in_position', 'direction': 'Long', 'qty': 1, 'entry_price': 10.0},
        'GONE': {'phase': 'in_position', 'direction': 'Long', 'qty': 1, 'ltp': 5.0},
    }}))
    monkeypatch.setattr(mod, 'STATE_FILE', str(state_file))
    monkeypatch.setattr(algo, '_retire_dropped_symbols',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    monkeypatch.setattr(algo, '_migrate_levels_to_spot_scale', lambda *a, **k: None)

    loaded = algo._load_state()
    assert loaded['stocks'][keep]['phase'] == 'in_position', 'the open position was abandoned'
    assert 'GONE' in loaded['stocks'], 'state was reset instead of left unretired'
