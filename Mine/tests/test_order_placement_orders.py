"""The Order Placement page's server side (/api/order-placement).

Real money, so the assertions that matter most here are the negative ones:

* a malformed ticket never reaches the dispatcher at all;
* an order only ever goes to a broker carrying BROKER_N_OP_ACTIVE=true —
  being merely active is not enough, and being OP-enabled while inactive is
  not enough either;
* the edit and cancel routes refuse an order this page did not place, so a
  strip on this page can never move an OI Profile or algo order.

The broker layer is patched in every test, and MineOrderStore is replaced with
an in-memory stand-in — nothing here can place an order or touch
app/utils/mine_orders.json.

No create_app(): the bare route app from route_app.py carries the same URL map
and starts no scheduler.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.app.routes.api as api  # noqa: E402
import trading_app.app.routes.order_placement_api as op  # noqa: E402
from trading_app.app.utils.mine_order_store import MineOrderStore  # noqa: E402
from route_app import build_route_app  # noqa: E402


# Broker 1 is the only one an order should ever reach: 2 is active but has not
# opted in, 3 has opted in but is switched off, 4 has no type at all.
ENV = {
    'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true',
    'BROKER_1_OP_ACTIVE': 'true', 'BROKER_1_NAME': 'Test (Kite)', 'BROKER_1_OP_LOTS': '3',

    'BROKER_2_TYPE': 'dhan', 'BROKER_2_ACTIVE': 'true', 'BROKER_2_OP_ACTIVE': 'false',
    'BROKER_3_TYPE': 'fyers', 'BROKER_3_ACTIVE': 'false', 'BROKER_3_OP_ACTIVE': 'true',
    'BROKER_4_ACTIVE': 'true', 'BROKER_4_OP_ACTIVE': 'true',
}


@pytest.fixture
def env(monkeypatch):
    """A fixed .env, and no reconciliation sweep against real brokers."""
    def get_user_var(username, var, default=''):
        return ENV.get(var, default)

    monkeypatch.setattr(
        'trading_app.app.utils.user_env.UserEnvManager.get_user_var',
        staticmethod(get_user_var))
    monkeypatch.setattr(api, '_reconcile_open_orders',
                        lambda *a, **k: 0)
    return ENV


@pytest.fixture
def store(monkeypatch):
    """MineOrderStore, in memory. The real one writes a file the app reads."""
    rows = []

    def add_order(data):
        record = {**data, 'id': f'test-{len(rows) + 1}', 'created_at': 1000 + len(rows)}
        rows.append(record)
        return record

    def get_order(order_id):
        return next((o for o in rows if o['id'] == order_id), {})

    def update_price(order_id, price):
        o = get_order(order_id)
        if o and o.get('status') in MineOrderStore.EDITABLE_STATUSES:
            o['price'] = price
            return True
        return False

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
    monkeypatch.setattr(MineOrderStore, 'update_price', staticmethod(update_price))
    monkeypatch.setattr(MineOrderStore, 'update_order', staticmethod(update_order))
    monkeypatch.setattr(MineOrderStore, 'cancel_order', staticmethod(cancel_order))
    monkeypatch.setattr(MineOrderStore, 'get_today_orders', staticmethod(lambda: list(rows)))
    monkeypatch.setattr(MineOrderStore, 'get_all_orders', staticmethod(lambda: list(rows)))
    return rows


@pytest.fixture
def dispatched(monkeypatch):
    """Records what would have been dispatched. Nothing ever is."""
    calls = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return {'success': True, 'brokers_targeted': 1,
                'summary': [{'broker': 'zerodha_1', 'instance': 1, 'quantity': 225,
                             'result': {'success': True, 'order_id': '1', 'price': 131.0}}]}

    monkeypatch.setattr(api, '_dispatch_order_to_brokers', fake_dispatch)
    return calls


@pytest.fixture
def client():
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


def order(client, **body):
    payload = {'symbol': 'NIFTY', 'strike': 24850, 'option_type': 'CE',
               'action': 'BUY', 'order_type': 'MARKET'}
    payload.update(body)
    return client.post('/api/order-placement/order', json=payload)


# ── nothing malformed reaches the dispatcher ─────────────────────────────

@pytest.mark.parametrize('body, hint', [
    ({'symbol': 'RELIANCE'}, 'symbol must be one of'),
    # The pad trades three index chains; the others were dropped, and a
    # payload naming one must not reach a broker on the strength of habit.
    ({'symbol': 'FINNIFTY', 'strike': 26000}, 'symbol must be one of'),
    ({'symbol': 'MIDCPNIFTY', 'strike': 12000}, 'symbol must be one of'),
    ({'option_type': 'XX'}, 'option_type must be CE or PE'),
    ({'action': 'HOLD'}, 'action must be BUY or SELL'),
    ({'order_type': 'OCO'}, 'order_type must be MARKET, LIMIT or STOP'),
    ({'order_type': 'STOP'}, 'needs a trigger price above zero'),
    ({'order_type': 'STOP', 'trigger_price': 0}, 'needs a trigger price above zero'),
    ({'strike': 0}, 'strike must be above zero'),
    ({'strike': 'abc'}, 'strike must be a whole number'),
    ({'order_type': 'LIMIT'}, 'needs a price above zero'),
    ({'order_type': 'LIMIT', 'limit_price': 0}, 'needs a price above zero'),
    # Size is not this page's to send. A payload carrying one is refused,
    # not quietly dropped: an ignored size is an order at the wrong size.
    ({'lots': 2}, 'does not size orders'),
    ({'lots': '2x'}, 'does not size orders'),
])
def test_a_malformed_ticket_never_reaches_a_broker(client, env, store, dispatched, body, hint):
    res = order(client, **body)
    assert res.status_code == 400
    assert hint in res.get_json()['error']
    assert dispatched == []
    assert store == []


def test_no_op_enabled_broker_refuses_before_dispatch(client, monkeypatch, store, dispatched):
    monkeypatch.setattr(
        'trading_app.app.utils.user_env.UserEnvManager.get_user_var',
        staticmethod(lambda username, var, default='':
                     {'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true'}.get(var, default)))
    res = order(client)
    assert res.status_code == 400
    assert 'BROKER_N_OP_ACTIVE' in res.get_json()['error']
    assert dispatched == []
    assert store == []


# ── what a good ticket does ──────────────────────────────────────────────

def test_a_market_order_is_dispatched_as_op_and_recorded_executed(client, env, store, dispatched):
    res = order(client)
    assert res.status_code == 200

    assert len(dispatched) == 1
    sent = dispatched[0]
    # The strategy key is what keeps the order on this page's own flag, off
    # the automatic stop-loss and out of the auto-exit monitor.
    assert sent['strategy'] == 'op'
    assert (sent['symbol'], sent['strike'], sent['option_type'], sent['action']) == \
           ('NIFTY', 24850, 'CE', 'BUY')
    # Never a size from the page: each broker resolves BROKER_N_OP_LOTS.
    assert sent['quantity'] is None
    assert sent['limit_price'] is None

    assert len(store) == 1
    record = store[0]
    assert record['strategy'] == 'op'
    assert record['source'] == 'orderplacement'
    assert record['status'] == 'EXECUTED'
    assert record['quantity'] == 225
    assert record['entry_price'] == 131.0


def test_a_limit_order_rests_and_carries_its_price(client, env, store, dispatched):
    res = order(client, order_type='LIMIT', limit_price=132.5)
    assert res.status_code == 200
    assert dispatched[0]['limit_price'] == 132.5
    assert dispatched[0]['quantity'] is None      # per-broker size, always
    assert store[0]['status'] == 'OPEN'
    assert store[0]['price'] == 132.5


def test_sensex_is_recorded_on_bfo(client, env, store, dispatched):
    order(client, symbol='SENSEX', strike=81000)
    assert store[0]['instrument'] == 'BFO'


def test_a_rejected_dispatch_is_recorded_rejected(client, env, store, monkeypatch):
    monkeypatch.setattr(api, '_dispatch_order_to_brokers',
                        lambda **k: {'success': False, 'error': 'Zerodha 1 not connected',
                                     'brokers_targeted': 1, 'summary': []})
    res = order(client)
    assert res.status_code == 400
    assert store[0]['status'] == 'REJECTED'
    assert store[0]['quantity'] == 0


# ── the page's book is its own ───────────────────────────────────────────

def test_the_listing_shows_only_this_pages_orders(client, env, store, dispatched):
    order(client, order_type='LIMIT', limit_price=100)          # ours, resting
    store.append({'id': 'other', 'strategy': 'intrinsic', 'status': 'OPEN',
                  'symbol': 'NIFTY', 'strike': 1, 'option_type': 'CE',
                  'action': 'BUY', 'created_at': 5})

    data = client.get('/api/order-placement/orders').get_json()
    assert [o['id'] for o in data['pending']] == ['test-1']
    assert data['done'] == []


@pytest.mark.parametrize('method, url_suffix, body', [
    ('put', '/price', {'price': 120}),
    ('delete', '', None),
])
def test_another_screens_order_cannot_be_touched(client, env, store, monkeypatch,
                                                 method, url_suffix, body):
    store.append({'id': 'other', 'strategy': 'intrinsic', 'status': 'OPEN',
                  'price': 100, 'broker_order_ids': [{'broker': 'zerodha_1',
                                                      'instance': 1, 'order_id': '9'}]})
    # If the guard were missing, these are what the request would reach.
    monkeypatch.setattr(api, '_modify_order_at_brokers',
                        lambda *a, **k: pytest.fail('modified another screen\'s order'))
    monkeypatch.setattr(api, '_cancel_order_at_brokers',
                        lambda *a, **k: pytest.fail('cancelled another screen\'s order'))

    res = getattr(client, method)(f'/api/order-placement/orders/other{url_suffix}', json=body)
    assert res.status_code == 404
    assert 'not placed from this page' in res.get_json()['error']
    assert store[0]['status'] == 'OPEN'
    assert store[0]['price'] == 100


def test_a_price_edit_fans_out_and_only_then_updates_the_record(client, env, store,
                                                                dispatched, monkeypatch):
    order(client, order_type='LIMIT', limit_price=100)
    seen = {}

    def fake_modify(legs, username, session_data, price=None, quantity=None,
                    trigger_price=None):
        seen.update({'legs': legs, 'price': price})
        return {'success': True, 'brokers_targeted': 1, 'summary': []}

    monkeypatch.setattr(api, '_modify_order_at_brokers', fake_modify)
    res = client.put('/api/order-placement/orders/test-1/price', json={'price': 111.5})
    assert res.status_code == 200
    assert seen['price'] == 111.5
    assert store[0]['price'] == 111.5


def test_a_refused_edit_leaves_the_stored_price_alone(client, env, store,
                                                      dispatched, monkeypatch):
    order(client, order_type='LIMIT', limit_price=100)
    monkeypatch.setattr(api, '_modify_order_at_brokers',
                        lambda *a, **k: {'success': False, 'error': 'Order is complete',
                                         'summary': []})
    res = client.put('/api/order-placement/orders/test-1/price', json={'price': 111.5})
    assert res.status_code == 400
    assert store[0]['price'] == 100


def test_a_cancel_fans_out_before_the_record_is_marked(client, env, store,
                                                       dispatched, monkeypatch):
    order(client, order_type='LIMIT', limit_price=100)
    calls = []
    monkeypatch.setattr(api, '_cancel_order_at_brokers',
                        lambda legs, u, s: calls.append(legs) or {'success': True, 'summary': []})
    res = client.delete('/api/order-placement/orders/test-1')
    assert res.status_code == 200
    assert calls, 'the broker legs were never cancelled'
    assert store[0]['status'] == 'CANCELLED'


def test_a_refused_cancel_leaves_the_order_open(client, env, store, dispatched, monkeypatch):
    order(client, order_type='LIMIT', limit_price=100)
    monkeypatch.setattr(api, '_cancel_order_at_brokers',
                        lambda *a, **k: {'success': False, 'error': 'Already complete',
                                         'summary': []})
    res = client.delete('/api/order-placement/orders/test-1')
    assert res.status_code == 400
    assert store[0]['status'] == 'OPEN'


# ── routing: the flag, and nothing but the flag ──────────────────────────

def test_config_lists_only_brokers_that_opted_in(client, env, store):
    data = client.get('/api/order-placement/config').get_json()
    assert [b['instance'] for b in data['brokers']] == [1]
    # The size the pad shows on the chip and repeats in the review bar — the
    # page has no other way to say how big the order will be.
    assert data['brokers'][0]['lots'] == 3
    assert data['enabled'] is True


def test_the_dispatcher_routes_op_on_its_own_flag(env, monkeypatch):
    """The real dispatcher, with the broker clients stubbed out.

    Broker 2 is active but never opted in and broker 3 opted in but is
    switched off, so exactly one leg may be attempted — and that leg must be
    broker 1, whatever the other two have set.
    """
    tried = []

    def fake_get_kite(instance=None, **kwargs):
        tried.append(instance)
        return None            # "not connected" — nothing is placed

    monkeypatch.setattr(api, 'get_kite', fake_get_kite)

    result = api._dispatch_order_to_brokers(
        symbol='NIFTY', strike=24850, option_type='CE', action='BUY',
        strategy='op', username='test-user', session_data={})

    assert result['brokers_targeted'] == 1
    assert tried == [1]
    assert result['success'] is False       # the stub broker is not connected


def test_op_orders_carry_no_automatic_stop_loss():
    """The flat 'entry - 20' stop belongs to the algos, not to a hand-fired
    pad. Read off the same tuple the dispatcher branches on."""
    import inspect
    src = inspect.getsource(api._dispatch_order_to_brokers)
    assert "_auto_sl = strategy not in ('intrinsic', 'oix', 'op')" in src


# ── the strike step comes from the chain, per underlying ─────────────────

def test_the_step_is_the_difference_near_the_money_not_in_the_wings():
    """A NIFTY chain: 50 apart around spot, 100 apart out in the wings.

    The mode of the whole chain here is 100, which is the wings' step and
    would move the ± buttons onto strikes that are not listed near the money.
    """
    wings_low = [23000 + 100 * i for i in range(10)]        # 23000 … 23900
    near = [24500 + 50 * i for i in range(21)]              # 24500 … 25500
    wings_high = [25600 + 100 * i for i in range(10)]
    chain = wings_low + near + wings_high

    assert op._modal_step(chain, spot=25000) == 50
    # BANKNIFTY-shaped chain: 100 throughout.
    assert op._modal_step([53000 + 100 * i for i in range(20)], spot=53450) == 100
    # SENSEX-shaped chain: 100, and the ± buttons must not read NIFTY's 50.
    assert op._modal_step([81000 + 100 * i for i in range(20)], spot=81450) == 100


def test_the_step_is_unknown_rather_than_guessed_from_a_thin_chain():
    assert op._modal_step([]) is None
    assert op._modal_step([24000]) is None
    assert op._modal_step([0, None]) is None


def test_the_step_centres_on_the_chain_when_there_is_no_spot():
    chain = [100 * i for i in range(1, 6)] + [500 + 50 * i for i in range(1, 22)]
    assert op._modal_step(chain, spot=None) == 50


class _Provider:
    """A data provider carrying one nearest-expiry chain."""

    def __init__(self, rows, ltp=None):
        self.rows = rows
        self._ltp = ltp or {}

    def instruments(self, exchange):
        return [r for r in self.rows if r['exchange'] == exchange]

    def ltp(self, keys):
        return {k: {'last_price': self._ltp.get(k, 0)} for k in keys}


def _chain(symbol, exchange, step, lot, count=21, base=None):
    from datetime import date, timedelta
    expiry = date.today() + timedelta(days=3)
    base = base or step * 100
    return [{'exchange': exchange, 'name': symbol, 'instrument_type': t,
             'strike': base + step * i, 'expiry': expiry, 'lot_size': lot}
            for i in range(count) for t in ('CE', 'PE')]


@pytest.fixture(autouse=True)
def _no_chain_cache():
    """The chain cache is process-wide; a test must not inherit another's."""
    op._chain_cache.clear()
    yield
    op._chain_cache.clear()


@pytest.mark.parametrize('symbol, exchange, step, lot', [
    ('NIFTY', 'NFO', 50, 75),
    ('BANKNIFTY', 'NFO', 100, 35),
    ('SENSEX', 'BFO', 100, 20),
])
def test_contract_reads_each_underlyings_own_chain(client, env, monkeypatch,
                                                   symbol, exchange, step, lot):
    # Every underlying's rows are present at both exchanges; only the right
    # symbol on the right exchange may be read.
    rows = (_chain('NIFTY', 'NFO', 50, 75) + _chain('BANKNIFTY', 'NFO', 100, 35)
            + _chain('SENSEX', 'BFO', 100, 20))
    monkeypatch.setattr(api, 'get_data_provider', lambda: _Provider(rows))
    monkeypatch.setattr(op, '_spot', lambda s: None)

    d = client.get(f'/api/order-placement/contract?symbol={symbol}').get_json()
    assert d['strike_step'] == step
    assert d['lot_size'] == lot
    assert d['step_source'] == 'chain'


def test_contract_prices_the_atm_off_the_step(client, env, monkeypatch):
    rows = _chain('BANKNIFTY', 'NFO', 100, 35)
    monkeypatch.setattr(api, 'get_data_provider', lambda: _Provider(rows))
    monkeypatch.setattr(op, '_spot', lambda s: 53_474.0)

    d = client.get('/api/order-placement/contract?symbol=BANKNIFTY').get_json()
    assert d['atm'] == 53500          # rounded on 100, not on 50
    assert d['spot'] == 53_474.0


def test_contract_falls_back_when_no_provider_can_answer(client, env, monkeypatch):
    monkeypatch.setattr(api, 'get_data_provider', lambda: None)
    d = client.get('/api/order-placement/contract?symbol=SENSEX').get_json()
    assert d['strike_step'] == 100
    assert d['step_source'] == 'fallback'
    assert d['lot_size'] is None      # not guessed


def test_contract_refuses_an_underlying_this_page_does_not_trade(client, env):
    res = client.get('/api/order-placement/contract?symbol=FINNIFTY')
    assert res.status_code == 400
    assert 'symbol must be one of' in res.get_json()['error']


# ── STOP (SL-M) orders ───────────────────────────────────────────────────

@pytest.fixture
def stopped(monkeypatch):
    """Records what would have been sent as a stop. Nothing ever is."""
    calls = []

    # No quote by default, which means the direction guard stands aside — the
    # tests that care about it set a premium of their own.
    monkeypatch.setattr(op, 'option_ltp', lambda *a: None)

    def fake_stop(**kwargs):
        calls.append(kwargs)
        return [{'broker': 'zerodha', 'instance': 1, 'quantity': 225,
                 'success': True, 'order_id': 'S1'}]

    monkeypatch.setattr(api, 'dispatch_stop_to_brokers', fake_stop)
    monkeypatch.setattr(api, 'resolve_standard_lot', lambda symbol: 75)
    return calls


def stop(client, **body):
    payload = {'symbol': 'NIFTY', 'strike': 24850, 'option_type': 'CE',
               'action': 'SELL', 'order_type': 'STOP', 'trigger_price': 110.0}
    payload.update(body)
    return client.post('/api/order-placement/order', json=payload)


def test_a_stop_rests_as_sl_m_and_carries_its_trigger(client, env, store, stopped):
    res = stop(client)
    assert res.status_code == 200

    sent = stopped[0]
    assert sent['trigger_price'] == 110.0
    assert sent['action'] == 'SELL'
    assert sent['standard_lot'] == 75

    record = store[0]
    assert record['order_type'] == 'SL-M'
    assert record['status'] == 'OPEN'          # resting on its trigger
    assert record['strategy'] == 'op'
    # Every screen reads 'price'; the trigger is carried there as well so the
    # pending strip edits the right number.
    assert record['price'] == 110.0
    assert record['trigger_price'] == 110.0
    assert record['quantity'] == 225


def test_a_buy_stop_is_an_entry_and_keeps_its_side(client, env, store, stopped):
    stop(client, action='BUY', trigger_price=160.0)
    assert stopped[0]['action'] == 'BUY'
    assert store[0]['action'] == 'BUY'


def test_a_stop_only_reaches_op_enabled_brokers(client, env, store, stopped):
    stop(client)
    # The gate the shared dispatcher is handed must admit broker 1 and refuse
    # the active-but-not-opted-in 2 and the opted-in-but-inactive 3.
    gate = stopped[0]['gate']
    assert gate(1, 'zerodha') is True
    assert gate(2, 'dhan') is False
    assert gate(3, 'fyers') is False
    # And the size is this page's, not the OI Profile panel's.
    assert stopped[0]['lots_for'](1) == 3


def test_a_stop_is_refused_when_the_lot_size_cannot_be_resolved(client, env, store, monkeypatch):
    monkeypatch.setattr(op, 'option_ltp', lambda *a: None)
    monkeypatch.setattr(api, 'resolve_standard_lot', lambda symbol: None)
    monkeypatch.setattr(api, 'dispatch_stop_to_brokers',
                        lambda **k: pytest.fail('placed a stop at a guessed size'))
    res = stop(client)
    assert res.status_code == 400
    assert 'lot size' in res.get_json()['error']
    assert store == []


def test_a_stop_no_broker_took_is_recorded_rejected(client, env, store, monkeypatch):
    monkeypatch.setattr(op, 'option_ltp', lambda *a: None)
    monkeypatch.setattr(api, 'resolve_standard_lot', lambda symbol: 75)
    monkeypatch.setattr(api, 'dispatch_stop_to_brokers',
                        lambda **k: [{'broker': 'zerodha', 'instance': 1,
                                      'success': False, 'error': 'Kite init failed'}])
    res = stop(client)
    assert res.status_code == 400
    assert store[0]['status'] == 'REJECTED'
    assert 'Kite init failed' in res.get_json()['error']


def test_editing_a_stop_moves_its_trigger_not_its_limit(client, env, store, stopped, monkeypatch):
    stop(client)
    seen = {}

    def fake_modify(legs, username, session_data, price=None, quantity=None,
                    trigger_price=None):
        seen.update({'price': price, 'trigger_price': trigger_price})
        return {'success': True, 'brokers_targeted': 1, 'summary': []}

    monkeypatch.setattr(api, '_modify_order_at_brokers', fake_modify)
    res = client.put('/api/order-placement/orders/test-1/price', json={'price': 95.5})
    assert res.status_code == 200
    assert res.get_json()['is_stop'] is True
    # A stop moved as a plain price would stop being a stop: the broker would
    # read it as a LIMIT resting there, and the protection would be gone.
    assert seen == {'price': None, 'trigger_price': 95.5}
    assert store[0]['price'] == 95.5
    assert store[0]['trigger_price'] == 95.5


def test_editing_a_limit_still_moves_its_price(client, env, store, dispatched, monkeypatch):
    order(client, order_type='LIMIT', limit_price=100)
    seen = {}
    monkeypatch.setattr(api, '_modify_order_at_brokers',
                        lambda legs, u, s, price=None, quantity=None, trigger_price=None:
                        seen.update({'price': price, 'trigger_price': trigger_price})
                        or {'success': True, 'summary': []})
    client.put('/api/order-placement/orders/test-1/price', json={'price': 111.5})
    assert seen == {'price': 111.5, 'trigger_price': None}


def test_the_shared_stop_dispatcher_reports_brokers_it_cannot_serve(monkeypatch):
    """Kotak and Dhan have no SL-M branch. A silent skip would read as
    "protected everywhere" on a screen that only lists what it heard back."""
    env = {'BROKER_1_TYPE': 'dhan', 'BROKER_1_ACTIVE': 'true'}
    monkeypatch.setattr(
        'trading_app.app.utils.user_env.UserEnvManager.get_user_var',
        staticmethod(lambda username, var, default='': env.get(var, default)))

    results = api.dispatch_stop_to_brokers(
        symbol='NIFTY', strike=24850, option_type='CE', trigger_price=100,
        action='SELL', username='u', session_data={}, standard_lot=75,
        gate=lambda i, b_type: True, lots_for=lambda i: 1)

    assert results == [{'broker': 'dhan', 'instance': 1, 'success': False,
                        'error': 'Stop orders are not supported for dhan yet'}]


def test_the_oi_profile_stop_route_still_gates_and_sizes_as_it_did(client, store, monkeypatch):
    """The OI Profile stop buttons share the dispatcher this page uses now.

    What must not have moved in that refactor: those stops still go only to
    BROKER_N_INTRINSIC_ACTIVE accounts, and are still sized by the intrinsic
    lots — a stop that lands on the wrong accounts, or covers the wrong
    fraction of a position, is worse than one that fails outright.
    """
    env = {
        'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true',
        'BROKER_1_INTRINSIC_ACTIVE': 'true', 'BROKER_1_INTRINSIC_LOTS': '20',
        'BROKER_2_TYPE': 'zerodha', 'BROKER_2_ACTIVE': 'true',
        'BROKER_2_INTRINSIC_ACTIVE': 'false',
        # This page's own flag must not let an account in here.
        'BROKER_3_TYPE': 'zerodha', 'BROKER_3_ACTIVE': 'true',
        'BROKER_3_OP_ACTIVE': 'true',
    }
    monkeypatch.setattr(
        'trading_app.app.utils.user_env.UserEnvManager.get_user_var',
        staticmethod(lambda username, var, default='': env.get(var, default)))
    monkeypatch.setattr(api, 'resolve_standard_lot', lambda symbol: 75)

    calls = []
    monkeypatch.setattr(api, 'dispatch_stop_to_brokers',
                        lambda **k: calls.append(k) or [{'broker': 'zerodha', 'instance': 1,
                                                         'quantity': 1500, 'success': True,
                                                         'order_id': 'S9'}])

    res = client.post('/api/order/place-sl', json={
        'symbol': 'NIFTY', 'strike': 24850, 'option_type': 'CE', 'trigger_price': 90})
    assert res.status_code == 200

    gate = calls[0]['gate']
    assert gate(1, 'zerodha') is True
    assert gate(2, 'zerodha') is False
    assert gate(3, 'zerodha') is False
    assert calls[0]['lots_for'](1) == 20
    assert calls[0]['action'] == 'SELL'          # the default is still the exit stop
    assert store[0]['strategy'] == 'intrinsic'   # not this page's orders


# ── a stop must not already be triggered ─────────────────────────────────

@pytest.mark.parametrize('action, trigger, ltp, hint', [
    # A BUY stop waits for a rise, so it has to sit above the market.
    ('BUY', 120, 130, 'must sit ABOVE the market'),
    ('BUY', 130, 130, 'must sit ABOVE the market'),
    # A SELL stop waits for a fall, so it has to sit below it.
    ('SELL', 140, 130, 'must sit BELOW the market'),
    ('SELL', 130, 130, 'must sit BELOW the market'),
])
def test_a_stop_on_the_wrong_side_of_the_market_never_reaches_a_broker(
        client, env, store, monkeypatch, action, trigger, ltp, hint):
    """The broker would accept it — a triggered stop is a legitimate order —
    and it would fire instantly at market for the full size. This check and
    its twin on the page are the only places that can catch it."""
    monkeypatch.setattr(op, 'option_ltp', lambda *a: ltp)
    monkeypatch.setattr(api, 'dispatch_stop_to_brokers',
                        lambda **k: pytest.fail('placed an already-triggered stop'))

    res = stop(client, action=action, trigger_price=trigger)
    assert res.status_code == 400
    body = res.get_json()
    assert hint in body['error']
    assert body['wrong_side'] is True
    assert store == []


@pytest.mark.parametrize('action, trigger, ltp', [
    ('BUY', 160, 130),      # waits for a rise
    ('SELL', 110, 130),     # waits for a fall
])
def test_a_stop_on_the_right_side_is_placed(client, env, store, stopped,
                                            monkeypatch, action, trigger, ltp):
    monkeypatch.setattr(op, 'option_ltp', lambda *a: ltp)
    res = stop(client, action=action, trigger_price=trigger)
    assert res.status_code == 200
    assert stopped[0]['trigger_price'] == trigger


def test_an_unknown_premium_means_no_check_rather_than_no_order(client, env, store,
                                                                stopped, monkeypatch):
    """The quote feed can be empty on a fresh load or after a provider hiccup.
    Refusing to place because we cannot see a price would be worse than
    placing — the broker still enforces its own tick and range rules."""
    monkeypatch.setattr(op, 'option_ltp', lambda *a: None)
    res = stop(client, action='SELL', trigger_price=110)
    assert res.status_code == 200
    assert store[0]['status'] == 'OPEN'


def test_the_direction_rule_itself(client=None):
    assert op.stop_direction_error('BUY', 160, 130) is None
    assert op.stop_direction_error('SELL', 110, 130) is None
    assert 'ABOVE' in op.stop_direction_error('BUY', 130, 130)
    assert 'BELOW' in op.stop_direction_error('SELL', 130, 130)
    # No price, no opinion.
    assert op.stop_direction_error('BUY', 130, None) is None
    assert op.stop_direction_error('BUY', 130, 0) is None
