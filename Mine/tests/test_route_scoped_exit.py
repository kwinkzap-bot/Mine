"""EXIT, per screen: /api/order-placement/exit-all and /api/order/exit-all.

Both buttons used to be one account-wide liquidation. They are now scoped by
the strategy key every record carries — 'op' for the Order Placement pad,
'intrinsic' for OI Profile — so the assertions that matter are the ones about
what each button does NOT touch:

* the other screen's position, including on the same strike in the same
  account, where only the quantity tells them apart;
* more than the broker actually holds, which would open a naked short on a
  position already closed by hand;
* anything at all when the position book cannot be read, because "no
  positions" and "the position request failed" must not mean the same thing.

No broker is reachable from here: _broker_client, _place_exit_leg and
_cancel_order_at_brokers are all replaced, and MineOrderStore is in memory.

No create_app(): the bare route app from route_app.py carries the same URL map
and starts no scheduler.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.app.routes.api as api  # noqa: E402
from trading_app.app.utils.mine_order_store import MineOrderStore  # noqa: E402
from route_app import build_route_app  # noqa: E402


def leg(quantity, order_id='X', broker='zerodha_1', instance=1, success=True):
    return {'broker': broker, 'instance': instance, 'quantity': quantity,
            'result': {'success': success, 'order_id': order_id}}


def record(strategy, action, strike, option_type, status, legs, symbol='NIFTY', **extra):
    return {'id': f'{strategy}-{strike}{option_type}-{action}-{status}',
            'strategy': strategy, 'symbol': symbol, 'strike': strike,
            'option_type': option_type, 'action': action, 'status': status,
            'quantity': sum(l['quantity'] for l in legs),
            'broker_order_ids': legs, **extra}


def kite_position(tradingsymbol, quantity, product='NRML', exchange='NFO'):
    return {'tradingsymbol': tradingsymbol, 'quantity': quantity,
            'product': product, 'exchange': exchange}


@pytest.fixture
def store(monkeypatch):
    """MineOrderStore, in memory. Tests append the records they need."""
    rows = []

    def get_order(order_id):
        return next((o for o in rows if o['id'] == order_id), {})

    def cancel_order(order_id):
        o = get_order(order_id)
        if o:
            o['status'] = 'CANCELLED'
            return True
        return False

    def update_order(order_id, updates):
        o = get_order(order_id)
        if o:
            o.update(updates)

    monkeypatch.setattr(MineOrderStore, 'get_today_orders', staticmethod(lambda: list(rows)))
    monkeypatch.setattr(MineOrderStore, 'get_order', staticmethod(get_order))
    monkeypatch.setattr(MineOrderStore, 'cancel_order', staticmethod(cancel_order))
    monkeypatch.setattr(MineOrderStore, 'update_order', staticmethod(update_order))
    monkeypatch.setattr(api, '_reconcile_open_orders', lambda *a, **k: 0)
    return rows


@pytest.fixture
def broker(monkeypatch):
    """One Zerodha instance holding whatever a test puts in `positions`."""
    state = {'positions': [], 'exits': [], 'cancels': [], 'fail_book': False}

    class FakeKite:
        def positions(self):
            if state['fail_book']:
                raise RuntimeError('token expired')
            return {'day': [], 'net': list(state['positions'])}

    monkeypatch.setattr(api, '_broker_client',
                        lambda b, i, u, s: ('kite', FakeKite()))

    def fake_exit(kind, client, pos, side, qty):
        state['exits'].append((pos['symbol'], side, qty))
        return ['exit-1']

    def fake_cancel(legs, username, session_data):
        state['cancels'].extend(l.get('result', {}).get('order_id') for l in legs)
        return {'success': True, 'summary': [
            {'broker': l['broker'], 'instance': l['instance'],
             'result': {'success': True}} for l in legs]}

    monkeypatch.setattr(api, '_place_exit_leg', fake_exit)
    monkeypatch.setattr(api, '_cancel_order_at_brokers', fake_cancel)
    return state


@pytest.fixture
def client():
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


# ── the two screens do not reach each other ──────────────────────────────

def test_op_exit_squares_off_only_what_the_pad_bought(client, store, broker):
    """One strike, one account, two screens. Only the pad's 150 goes out."""
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED', [leg(150)]))
    store.append(record('intrinsic', 'BUY', 24800, 'CE', 'EXECUTED', [leg(300)]))
    broker['positions'] = [kite_position('NIFTY25SEP24800CE', 450)]

    res = client.post('/api/order-placement/exit-all')
    assert res.status_code == 200
    body = res.get_json()
    assert body['success'] is True
    assert body['strategy'] == 'op'
    assert body['exited_positions'] == 1
    assert broker['exits'] == [('NIFTY25SEP24800CE', 'SELL', 150)]


def test_oi_profile_exit_squares_off_only_its_own(client, store, broker):
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED', [leg(150)]))
    store.append(record('intrinsic', 'BUY', 24800, 'CE', 'EXECUTED', [leg(300)]))
    broker['positions'] = [kite_position('NIFTY25SEP24800CE', 450)]

    res = client.post('/api/order/exit-all')
    assert res.status_code == 200
    body = res.get_json()
    assert body['strategy'] == 'intrinsic'
    assert broker['exits'] == [('NIFTY25SEP24800CE', 'SELL', 300)]


def test_op_exit_cancels_only_the_pads_resting_orders(client, store, broker):
    store.append(record('op', 'BUY', 24900, 'PE', 'OPEN', [leg(75, 'PAD')]))
    store.append(record('intrinsic', 'BUY', 24900, 'PE', 'OPEN', [leg(75, 'OIP')]))

    body = client.post('/api/order-placement/exit-all').get_json()
    assert broker['cancels'] == ['PAD']
    assert body['cancelled_orders'] == 1
    # And the record it cancelled is the pad's, not the other screen's.
    assert [o['status'] for o in store] == ['CANCELLED', 'OPEN']


# ── never more than the broker actually holds ────────────────────────────

def test_a_position_closed_by_hand_is_not_sold_again(client, store, broker):
    """The store still says 150 open; the broker says flat. Nothing goes out —
    a SELL here would be a naked short, not an exit."""
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED', [leg(150)]))
    broker['positions'] = [kite_position('NIFTY25SEP24800CE', 0)]

    body = client.post('/api/order-placement/exit-all').get_json()
    assert broker['exits'] == []
    assert body['exited_positions'] == 0
    assert body['success'] is True


def test_a_partly_closed_position_exits_only_the_remainder(client, store, broker):
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED', [leg(150)]))
    broker['positions'] = [kite_position('NIFTY25SEP24800CE', 75)]

    client.post('/api/order-placement/exit-all')
    assert broker['exits'] == [('NIFTY25SEP24800CE', 'SELL', 75)]


def test_a_contract_the_broker_never_heard_of_is_left_alone(client, store, broker):
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED', [leg(150)]))
    broker['positions'] = [kite_position('BANKNIFTY25SEP54000CE', 300)]

    body = client.post('/api/order-placement/exit-all').get_json()
    assert broker['exits'] == []
    assert body['exited_positions'] == 0


def test_an_unreadable_position_book_exits_nothing_and_says_so(client, store, broker):
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED', [leg(150)]))
    broker['fail_book'] = True

    body = client.post('/api/order-placement/exit-all').get_json()
    assert broker['exits'] == []
    assert body['success'] is False
    assert 'position book unavailable' in body['error'].lower()


# ── the net, not the gross ───────────────────────────────────────────────

def test_a_sell_that_already_closed_the_entry_leaves_nothing_to_exit(client, store, broker):
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED', [leg(150)]))
    store.append(record('op', 'SELL', 24800, 'CE', 'EXECUTED', [leg(150)]))
    broker['positions'] = [kite_position('NIFTY25SEP24800CE', 150)]  # someone else's

    body = client.post('/api/order-placement/exit-all').get_json()
    assert broker['exits'] == []
    assert body['exited_positions'] == 0


def test_a_short_from_this_page_is_bought_back(client, store, broker):
    store.append(record('op', 'SELL', 24800, 'CE', 'EXECUTED', [leg(75)]))
    broker['positions'] = [kite_position('NIFTY25SEP24800CE', -75)]

    client.post('/api/order-placement/exit-all')
    assert broker['exits'] == [('NIFTY25SEP24800CE', 'BUY', 75)]


def test_a_rejected_leg_adds_no_exposure(client, store, broker):
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED',
                        [leg(150), leg(150, 'DEAD', broker='fyers', instance=2, success=False)]))
    broker['positions'] = [kite_position('NIFTY25SEP24800CE', 150)]

    client.post('/api/order-placement/exit-all')
    assert broker['exits'] == [('NIFTY25SEP24800CE', 'SELL', 150)]


# ── symbol matching, the part four brokers spell four ways ───────────────

@pytest.mark.parametrize('symbol', [
    'NIFTY25SEP24800CE',            # Kite
    'NSE:NIFTY25SEP24800CE',        # Fyers
    'NIFTY-Sep2025-24800-CE',       # Dhan
    'NIFTY 25SEP 24800 CE',         # Kotak
])
def test_every_brokers_spelling_of_one_contract_matches(symbol):
    assert api._position_matches(symbol, 'NIFTY', 24800, 'CE')


@pytest.mark.parametrize('symbol', [
    'BANKNIFTY25SEP24800CE',   # a different chain that ends the same way
    'NIFTY25SEP24800PE',       # the other side
    'NIFTY25SEP24850CE',       # a neighbouring strike
    '',
])
def test_a_different_contract_never_matches(symbol):
    assert not api._position_matches(symbol, 'NIFTY', 24800, 'CE')


def test_sensex_keeps_its_bse_prefix_out_of_the_underlying():
    assert api._position_matches('BSE:SENSEX25SEP81000PE', 'SENSEX', 81000, 'PE')


# ── the order that actually goes out ─────────────────────────────────────

def test_the_kite_exit_is_a_market_order_off_the_position_row(monkeypatch):
    """Product, exchange and symbol all come from the broker's own row, so the
    exit can only land on the contract the broker says is open. Size is split
    by the exchange freeze limit — 27 NIFTY lots, 675 — like every other exit
    path in the app."""
    placed = []

    class FakeKiteService:
        def __init__(self, kite_instance=None):
            pass

        def _safe_place_order(self, **kwargs):
            placed.append(kwargs)
            return f'oid-{len(placed)}'

    monkeypatch.setattr('trading_app.service.kite_order_services.KiteService',
                        FakeKiteService)

    row = {'symbol': 'NIFTY25SEP24800CE', 'net_qty': 900, 'product': 'NRML',
           'raw': kite_position('NIFTY25SEP24800CE', 900)}
    ids = api._place_exit_leg('kite', object(), row, 'SELL', 900)

    assert ids == ['oid-1', 'oid-2']
    assert [p['quantity'] for p in placed] == [675, 225]
    assert all(p['order_type'] == 'MARKET' for p in placed)
    assert all(p['transaction_type'] == 'SELL' for p in placed)
    assert all(p['tradingsymbol'] == 'NIFTY25SEP24800CE' for p in placed)
    assert all(p['product'] == 'NRML' and p['exchange'] == 'NFO' for p in placed)
    # Zerodha rejects an F&O market exit without it.
    assert all(p['market_protection'] == -1 for p in placed)


# ── reading four position books ──────────────────────────────────────────

class _Book:
    def __init__(self, payload):
        self._payload = payload

    positions = property(lambda self: lambda: self._payload)
    get_positions = property(lambda self: lambda: self._payload)


@pytest.mark.parametrize('kind, payload', [
    ('kite', {'net': [kite_position('NIFTY25SEP24800CE', 150)], 'day': []}),
    ('fyers', {'success': True, 'positions': [
        {'symbol': 'NSE:NIFTY25SEP24800CE', 'netQty': 150, 'productType': 'INTRADAY'}]}),
    ('dhan', {'success': True, 'positions': [
        {'tradingSymbol': 'NIFTY-Sep2025-24800-CE', 'netQty': 150,
         'productType': 'INTRADAY', 'exchangeSegment': 'NSE_FNO', 'securityId': '43911'}]}),
    ('kotak', {'success': True, 'positions': [
        {'trdSym': 'NIFTY25SEP24800CE', 'flNetQty': '150', 'prod': 'NRML', 'exseg': 'nse_fo'}]}),
])
def test_each_brokers_position_book_reads_as_one_shape(kind, payload):
    rows = api._broker_positions(kind, _Book(payload))
    assert len(rows) == 1
    assert rows[0]['net_qty'] == 150
    assert api._position_matches(rows[0]['symbol'], 'NIFTY', 24800, 'CE')


@pytest.mark.parametrize('kind, payload', [
    ('fyers', {'success': False, 'error': 'token expired'}),
    ('dhan', {'success': False}),
    ('kotak', {'success': False}),
])
def test_a_refused_position_book_is_none_not_empty(kind, payload):
    """None means "cannot see"; [] would mean "nothing held", and the exit
    would take that as licence to do nothing and report success."""
    assert api._broker_positions(kind, _Book(payload)) is None


def test_a_cnc_holding_is_never_squared_off(client, store, broker):
    store.append(record('op', 'BUY', 24800, 'CE', 'EXECUTED', [leg(150)]))
    broker['positions'] = [kite_position('NIFTY25SEP24800CE', 150, product='CNC')]

    body = client.post('/api/order-placement/exit-all').get_json()
    assert broker['exits'] == []
    assert body['exited_positions'] == 0


def test_a_leg_that_refused_the_cancel_is_reported(client, store, broker, monkeypatch):
    """Cancelled at one broker and refused at another is not a clean exit —
    that leg is still resting, and the screen has to be told."""
    store.append(record('op', 'BUY', 24900, 'PE', 'OPEN',
                        [leg(75, 'A'), leg(75, 'B', broker='fyers', instance=2)]))

    monkeypatch.setattr(api, '_cancel_order_at_brokers', lambda *a, **k: {
        'success': True, 'summary': [
            {'broker': 'zerodha_1', 'instance': 1, 'result': {'success': True}},
            {'broker': 'fyers', 'instance': 2,
             'result': {'success': False, 'error': 'order not found'}}]})

    body = client.post('/api/order-placement/exit-all').get_json()
    assert body['success'] is False
    assert 'order not found' in body['error']
    assert body['cancelled_orders'] == 1
