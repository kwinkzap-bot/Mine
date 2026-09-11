"""Arming a signal — mostly, the ways it refuses to.

A signal commits to a whole trade at once, so almost everything worth testing
here is a refusal. An entry that goes out on a stale tip is a market order for
the full size; one armed on a broker that cannot hold a stop is a naked
position with some limit orders over it; one on a back-month tip is the wrong
contract entirely. Each of those has to be caught before a broker is touched,
and each test below asserts exactly that — the dispatcher is never reached.

No create_app(): the bare route app carries the same URL map and starts no
scheduler. Every broker call is patched, and MineOrderStore is an in-memory
list, so nothing here can place an order.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.app.routes.api as api  # noqa: E402
import trading_app.app.routes.order_placement_api as op  # noqa: E402
from trading_app.app.order_placement import op_signal_engine as engine  # noqa: E402
from trading_app.app.order_placement import op_signal_store as sig_store  # noqa: E402
from trading_app.app.order_placement.op_signal_store import OpSignalStore  # noqa: E402
from trading_app.app.utils.mine_order_store import MineOrderStore  # noqa: E402
from route_app import build_route_app  # noqa: E402


TIP = """NIFTY 23500 CE
BUY : 193
SL : 175
Target : 206, 213, 220"""

# Broker 1 is the only one a signal should ever reach: 2 is OP-active but has
# no signal size, 3 is signal-sized but not OP-active at all.
ENV = {
    'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true',
    'BROKER_1_OP_ACTIVE': 'true', 'BROKER_1_NAME': 'One (Kite)',
    'BROKER_1_OP_LOTS': '20', 'BROKER_1_OP_SIGNAL_LOTS': '1',

    'BROKER_2_TYPE': 'zerodha', 'BROKER_2_ACTIVE': 'true',
    'BROKER_2_OP_ACTIVE': 'true', 'BROKER_2_NAME': 'Two (Kite)',
    'BROKER_2_OP_LOTS': '4',

    'BROKER_3_TYPE': 'zerodha', 'BROKER_3_ACTIVE': 'true',
    'BROKER_3_OP_ACTIVE': 'false', 'BROKER_3_NAME': 'Three (Kite)',
    'BROKER_3_OP_SIGNAL_LOTS': '5',
}


@pytest.fixture
def env(monkeypatch):
    def get_user_var(username, var, default=''):
        return ENV.get(var, default)

    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                        staticmethod(get_user_var))
    monkeypatch.setattr(api, '_reconcile_open_orders', lambda *a, **k: 0)
    # The chain, and the premium. 193 is below the market, so a BUY stop there
    # is still waiting — which is what every test but the stale-tip one wants.
    monkeypatch.setattr(op, '_chain_meta',
                        lambda s: {'step': 50, 'lot_size': 75,
                                   'expiry': '2026-09-11', 'source': 'chain'})
    monkeypatch.setattr(op, 'option_ltp', lambda *a, **k: 150.0)
    monkeypatch.setattr(api, 'resolve_standard_lot', lambda s: 75)
    monkeypatch.setattr(engine, 'ensure_running', lambda *a, **k: False)
    return ENV


@pytest.fixture
def signals(monkeypatch, tmp_path):
    """The plan store, on a throwaway file."""
    monkeypatch.setattr(sig_store, '_STORAGE_FILE', str(tmp_path / 'op_signals.json'))
    return OpSignalStore


@pytest.fixture
def store(monkeypatch):
    rows = []

    def add_order(data):
        record = {**data, 'id': f'test-{len(rows) + 1}', 'created_at': 1000 + len(rows)}
        rows.append(record)
        return record

    def get_order(order_id):
        return next((o for o in rows if o['id'] == order_id), {})

    def update_order(order_id, updates):
        o = get_order(order_id)
        if o:
            o.update(updates)

    def cancel_order(order_id):
        o = get_order(order_id)
        if o:
            o['status'] = 'CANCELLED'
            return True
        return False

    monkeypatch.setattr(MineOrderStore, 'add_order', staticmethod(add_order))
    monkeypatch.setattr(MineOrderStore, 'get_order', staticmethod(get_order))
    monkeypatch.setattr(MineOrderStore, 'update_order', staticmethod(update_order))
    monkeypatch.setattr(MineOrderStore, 'cancel_order', staticmethod(cancel_order))
    monkeypatch.setattr(MineOrderStore, 'get_today_orders', staticmethod(lambda: list(rows)))
    monkeypatch.setattr(MineOrderStore, 'get_all_orders', staticmethod(lambda: list(rows)))
    return rows


@pytest.fixture
def stopped(monkeypatch):
    """Records the stop legs that would have been placed. None ever is."""
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        inst = next((i for i in range(1, 21) if kwargs['gate'](i, 'zerodha')), 1)
        return [{'broker': 'zerodha', 'instance': inst, 'success': True,
                 'order_id': f'sl-{len(calls)}',
                 'quantity': kwargs['lots_for'](inst) * kwargs['standard_lot']}]

    monkeypatch.setattr(api, 'dispatch_stop_to_brokers', fake)
    return calls


@pytest.fixture
def never_dispatched(monkeypatch):
    def boom(**kwargs):
        pytest.fail(f'a broker was reached: {kwargs}')

    monkeypatch.setattr(api, 'dispatch_stop_to_brokers', boom)
    monkeypatch.setattr(api, '_dispatch_order_to_brokers', boom)


@pytest.fixture
def client():
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


def arm(client, **body):
    return client.post('/api/order-placement/signal', json={'text': TIP, **body})


# ── the refusals ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('body, hint', [
    # The ladder has to make sense in the direction it trades.
    ({'stop': 200}, 'stop below the entry'),
    ({'targets': [206, 200, 220]}, 'rising targets'),
    ({'targets': [180, 213, 220]}, 'rising targets'),
    ({'targets': [206, 213]}, 'needs three targets'),
    ({'targets': [206, 213, 220, 230]}, 'needs three targets'),
    ({'entry': 0}, 'above zero'),
    # A strike off the chain's step is not a contract.
    ({'strike': 23530}, 'they step by 50'),
    ({'symbol': 'RELIANCE'}, 'This page trades'),
])
def test_a_bad_ladder_never_reaches_a_broker(client, env, signals, store,
                                             never_dispatched, body, hint):
    res = arm(client, **body)
    assert res.status_code == 400
    assert hint in res.get_json()['error']
    assert store == []
    assert signals.get_today() == []


def test_a_back_month_tip_is_refused_rather_than_traded_on_the_front_month(
        client, env, signals, store, never_dispatched):
    """The order path always resolves the nearest expiry and ignores the rest.

    So a September tip armed in front of an 11-SEP weekly would be traded on
    the weekly, at a completely different premium, and look correct doing it.
    """
    res = arm(client, expiry='2026-09-25')
    assert res.status_code == 400
    error = res.get_json()['error']
    assert '2026-09-25' in error and '2026-09-11' in error


def test_a_stale_tip_whose_entry_is_already_below_the_market_is_refused(
        client, env, signals, store, never_dispatched, monkeypatch):
    """A BUY stop at 193 with the premium at 200 fires instantly, at market.

    That is the exact thing not pressing MARKET was meant to avoid, and the
    broker will not refuse it — a triggered stop is a legitimate order.
    """
    monkeypatch.setattr(op, 'option_ltp', lambda *a, **k: 200.0)
    res = arm(client)
    assert res.status_code == 400
    assert 'BUY stop must sit ABOVE the market' in res.get_json()['error']


def test_a_broker_that_cannot_hold_a_stop_blocks_the_whole_signal(
        client, env, signals, store, never_dispatched, monkeypatch):
    """Dhan and Kotak have no SL-M path in the shared dispatcher.

    Arming anyway would leave a live position on that account with targets over
    it and nothing underneath — worse than not arming at all. So it refuses,
    naming the account, rather than half-arming.
    """
    monkeypatch.setitem(ENV, 'BROKER_2_TYPE', 'dhan')
    try:
        res = arm(client)
        assert res.status_code == 400
        error = res.get_json()['error']
        assert 'cannot hold a stop-loss' in error and 'Two (Kite)' in error
    finally:
        ENV['BROKER_2_TYPE'] = 'zerodha'


def test_a_signal_never_guesses_a_size_from_the_single_order_lots(
        client, env, signals, store, never_dispatched, monkeypatch):
    """BROKER_N_OP_LOTS sizes a whole order; a signal needs lots per target.

    Reading one as the other would treble the position, so an unsized broker
    simply takes no part — and if none is sized, nothing is armed.
    """
    monkeypatch.delitem(ENV, 'BROKER_1_OP_SIGNAL_LOTS')
    try:
        res = arm(client)
        assert res.status_code == 400
        error = res.get_json()['error']
        assert 'BROKER_N_OP_SIGNAL_LOTS' in error and 'will not guess' in error
    finally:
        ENV['BROKER_1_OP_SIGNAL_LOTS'] = '1'


def test_a_ladder_over_the_freeze_limit_is_split_not_refused(
        client, env, signals, store, stopped, monkeypatch):
    """10 lots a target is a 30-lot entry, over the exchange's 27-lot cap.

    Neither shared dispatcher splits by that cap, so the signal splits its own
    legs: 27 + 3, two orders at the one account, both on the same record.
    Refusing instead would cap this page at 9 lots a target for a limit the
    exchange does not actually place on the position.
    """
    monkeypatch.setitem(ENV, 'BROKER_1_OP_SIGNAL_LOTS', '10')
    try:
        res = arm(client)
        assert res.status_code == 200, res.get_json()
        assert [c['lots_for'](1) for c in stopped] == [27, 3]
        # One record, both chunks on it — the ladder is sized from their sum.
        assert len(store) == 1
        assert len(store[0]['broker_order_ids']) == 2
        assert sum(l['quantity'] for l in store[0]['broker_order_ids']) == 30 * 75
    finally:
        ENV['BROKER_1_OP_SIGNAL_LOTS'] = '1'


def test_a_size_that_is_obviously_a_typo_is_still_refused(
        client, env, signals, store, never_dispatched, monkeypatch):
    monkeypatch.setitem(ENV, 'BROKER_1_OP_SIGNAL_LOTS', '9999')
    try:
        res = arm(client)
        assert res.status_code == 400
        assert 'typo, not a position' in res.get_json()['error']
    finally:
        ENV['BROKER_1_OP_SIGNAL_LOTS'] = '1'


def test_a_normally_sized_signal_is_still_one_order_per_broker(
        client, env, signals, store, stopped):
    """The split must not turn every ordinary signal into several orders."""
    arm(client)
    assert [c['lots_for'](1) for c in stopped] == [3]
    assert len(store[0]['broker_order_ids']) == 1


def test_unreadable_text_is_refused_before_anything_else(
        client, env, signals, store, never_dispatched):
    res = client.post('/api/order-placement/signal', json={'text': 'hello'})
    assert res.status_code == 400
    assert 'No underlying' in res.get_json()['error']


# ── what arming actually does ────────────────────────────────────────────

def test_arming_places_one_entry_stop_per_sized_broker_and_nothing_else(
        client, env, signals, store, stopped, monkeypatch):
    """The stop and the targets are NOT placed now.

    Until the entry fills there is no position for them to protect, and a
    resting sell against nothing is a naked short.
    """
    def boom(**kwargs):
        pytest.fail('a target limit went out before the entry filled')

    monkeypatch.setattr(api, '_dispatch_order_to_brokers', boom)

    res = arm(client)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body['success'] is True

    # Broker 1 only: broker 2 is unsized, broker 3 is not OP-active.
    assert len(stopped) == 1
    call = stopped[0]
    assert call['action'] == 'BUY' and call['trigger_price'] == 193
    assert call['gate'](1, 'zerodha') is True
    assert call['gate'](2, 'zerodha') is False
    assert call['gate'](3, 'zerodha') is False
    # One lot per target means a three-lot entry.
    assert call['lots_for'](1) == 3

    assert len(store) == 1
    entry = store[0]
    assert entry['leg'] == 'ENTRY' and entry['order_type'] == 'SL-M'
    assert entry['strategy'] == 'op' and entry['status'] == 'OPEN'
    assert entry['trigger_price'] == 193


def test_an_armed_signal_starts_pending_and_knows_its_broker(
        client, env, signals, store, stopped):
    arm(client)
    signal = signals.get_today()[0]
    assert signal['phase'] == 'ENTRY_PENDING'
    slot = signal['brokers']['1']
    assert slot['stage'] == 'PENDING_ENTRY'
    assert slot['plan_lots'] == 1 and slot['entry_lots'] == 3
    assert slot['entry_record_id'] == store[0]['id']


def test_a_signal_no_broker_accepted_is_closed_not_left_armed(
        client, env, signals, store, monkeypatch):
    monkeypatch.setattr(api, 'dispatch_stop_to_brokers',
                        lambda **k: [{'broker': 'zerodha', 'instance': 1,
                                      'success': False, 'error': 'margin shortfall'}])
    res = arm(client)
    assert res.status_code == 400
    assert 'margin shortfall' in res.get_json()['error']
    # The plan exists (it is the record of what was tried) but nothing will
    # manage it, because there is nothing resting anywhere.
    assert signals.get_today()[0]['phase'] == 'FAILED'
    assert signals.get_active() == []


def test_a_sell_tip_arms_with_its_ladder_mirrored(client, env, signals, store,
                                                  stopped, monkeypatch):
    monkeypatch.setattr(op, 'option_ltp', lambda *a, **k: 250.0)
    res = client.post('/api/order-placement/signal', json={'text': """NIFTY 23500 CE
SELL BELOW 193
SL : 210
Target : 180, 170, 160"""})
    assert res.status_code == 200, res.get_json()
    assert stopped[0]['action'] == 'SELL'
    assert signals.get_today()[0]['targets'] == [180.0, 170.0, 160.0]


def test_a_sell_tip_with_a_buy_ladder_is_refused(client, env, signals, store,
                                                 never_dispatched, monkeypatch):
    monkeypatch.setattr(op, 'option_ltp', lambda *a, **k: 250.0)
    res = client.post('/api/order-placement/signal', json={'text': """NIFTY 23500 CE
SELL BELOW 193
SL : 175
Target : 206, 213, 220"""})
    assert res.status_code == 400
    assert 'stop above the entry' in res.get_json()['error']


# ── the preview ──────────────────────────────────────────────────────────

def test_the_preview_reads_the_tip_without_placing_anything(
        client, env, signals, store, never_dispatched):
    res = client.post('/api/order-placement/signal/parse', json={'text': TIP})
    body = res.get_json()
    assert body['success'] is True and body['armable'] is True
    assert body['plan']['strike'] == 23500
    assert body['plan']['targets'] == [206.0, 213.0, 220.0]
    # Said out loud, because it is the one leg with no order behind it.
    assert body['target_3_watched'] == 220.0
    assert body['targets_resting'] == [206.0, 213.0]
    assert store == [] and signals.get_today() == []


def test_the_preview_shows_both_what_it_read_and_why_it_will_not_arm(
        client, env, signals, store, never_dispatched):
    """The plan comes back beside the error, not instead of it — otherwise a
    refusal about the expiry is indistinguishable from one about the strike."""
    res = client.post('/api/order-placement/signal/parse',
                      json={'text': TIP, 'expiry': '2026-09-25'})
    body = res.get_json()
    assert body['success'] is True
    assert body['armable'] is False
    assert body['plan']['strike'] == 23500
    assert '2026-09-25' in body['error']
