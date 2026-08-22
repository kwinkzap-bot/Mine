"""The Watchlist order ticket's server side.

Real money: the assertion that matters most in this file is the *negative*
one — that a malformed or ineligible ticket never reaches a broker at all.
Every test here patches the broker layer, so nothing can be placed even if
the route were wrong.

No create_app(): the bare route app from route_app.py carries the same URL
map and starts no scheduler.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.app.routes.api as api
import trading_app.service.watchlist_service as wl
from route_app import build_route_app


UNIVERSE = [
    {'symbol': 'RELIANCE', 'fy_symbol': 'NSE:RELIANCE-EQ', 'kind': 'EQ',
     'company': 'RELIANCE INDUSTRIES LTD', 'yf_symbol': 'RELIANCE.NS'},
    {'symbol': 'NIFTY', 'fy_symbol': 'NSE:NIFTY50-INDEX', 'kind': 'INDEX',
     'company': 'NIFTY50-INDEX', 'yf_symbol': '^NSEI'},
]

ENV = {
    'BROKER_1_ACTIVE': 'true', 'BROKER_1_TYPE': 'zerodha', 'BROKER_1_NAME': 'Test (Kite)',
    'BROKER_2_ACTIVE': 'false', 'BROKER_2_TYPE': 'zerodha', 'BROKER_2_NAME': 'Off (Kite)',
}


@pytest.fixture
def placed(monkeypatch):
    """Records what would have been sent to a broker. Nothing ever is."""
    calls = []

    def fake_place(broker_type, svc, symbol, qty, side='BUY', price=None,
                   product='CNC', order_type='MARKET', limit_price=None):
        calls.append({'broker_type': broker_type, 'symbol': symbol, 'qty': qty,
                      'side': side, 'price': price, 'product': product,
                      'order_type': order_type, 'limit_price': limit_price})
        return ('2508201234', None)

    monkeypatch.setattr(api, '_sm_place_equity_order', fake_place)
    monkeypatch.setattr(api, '_sm_build_order_service',
                        lambda user, instance, broker_type: object())
    monkeypatch.setattr(wl, '_load_universe', lambda: UNIVERSE)
    monkeypatch.setattr(
        'trading_app.app.utils.user_env.UserEnvManager.get_user_var',
        staticmethod(lambda username, var, default='': ENV.get(var, default)))
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
    payload = {'symbol': 'RELIANCE', 'side': 'BUY', 'qty': 1, 'order_type': 'MARKET',
               'product': 'CNC', 'broker': 1}
    payload.update(body)
    return client.post('/api/watchlist/order', json=payload)


# ── nothing malformed reaches a broker ───────────────────────────────────

@pytest.mark.parametrize('body, hint', [
    ({'symbol': ''}, 'symbol is required'),
    ({'side': 'HOLD'}, 'side must be BUY or SELL'),
    ({'qty': 0}, 'at least 1'),
    ({'qty': -5}, 'at least 1'),
    ({'qty': 'many'}, 'whole number'),
    ({'order_type': 'STOPLOSS'}, 'MARKET or LIMIT'),
    ({'product': 'CO'}, 'CNC or MIS'),
    ({'order_type': 'LIMIT'}, 'needs a price'),                 # no limit_price
    ({'order_type': 'LIMIT', 'limit_price': 0}, 'needs a price'),
    ({'symbol': 'NOTALISTEDCO'}, 'Unknown symbol'),
    ({'symbol': 'NIFTY'}, 'index'),                             # not a cash instrument
    ({'broker': 0}, 'Choose a broker'),
    ({'broker': 2}, 'not active'),                              # BROKER_2_ACTIVE=false
])
def test_a_bad_ticket_is_refused_before_any_broker_call(client, placed, body, hint):
    res = order(client, **body)
    assert res.status_code == 400
    assert hint.lower() in res.get_json()['error'].lower()
    assert placed == [], 'a rejected ticket must not reach a broker'


def test_an_unauthenticated_session_cannot_place_anything(placed):
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as anon:
        res = anon.post('/api/watchlist/order',
                        json={'symbol': 'RELIANCE', 'side': 'BUY', 'qty': 1, 'broker': 1})
    assert res.status_code == 401
    assert placed == []


def test_a_broker_that_is_not_logged_in_is_reported_not_retried(client, placed, monkeypatch):
    monkeypatch.setattr(api, '_sm_build_order_service',
                        lambda user, instance, broker_type: None)
    res = order(client)
    assert res.status_code == 400
    assert 'not logged in' in res.get_json()['error']
    assert placed == []


def test_a_broker_rejection_is_surfaced_verbatim(client, placed, monkeypatch):
    monkeypatch.setattr(api, '_sm_place_equity_order',
                        lambda *a, **k: (None, 'Insufficient funds'))
    res = order(client)
    assert res.status_code == 400
    assert res.get_json()['error'] == 'Insufficient funds'


# ── what a good ticket sends ─────────────────────────────────────────────

def test_a_market_order_reaches_the_broker_exactly_as_ticketed(client, placed):
    res = order(client, side='SELL', qty=12, product='MIS', ltp=1311.5)
    assert res.status_code == 200

    body = res.get_json()
    assert body['success'] is True and body['order_id'] == '2508201234'
    assert placed == [{'broker_type': 'zerodha', 'symbol': 'RELIANCE', 'qty': 12,
                       'side': 'SELL', 'price': 1311.5, 'product': 'MIS',
                       'order_type': 'MARKET', 'limit_price': None}]


def test_a_limit_order_carries_its_price(client, placed):
    res = order(client, order_type='LIMIT', limit_price=1300.25, qty=3)
    assert res.status_code == 200
    assert placed[0]['order_type'] == 'LIMIT'
    assert placed[0]['limit_price'] == 1300.25
    assert placed[0]['product'] == 'CNC'


def test_one_confirm_places_exactly_one_order(client, placed):
    order(client, qty=7)
    assert len(placed) == 1, 'the ticket must not fan out across brokers'


# ── the picker only offers brokers that can actually take an order ───────

def test_broker_list_offers_only_active_slots(client, placed):
    brokers = client.get('/api/watchlist/brokers').get_json()['brokers']
    assert [b['instance'] for b in brokers] == [1]
    assert brokers[0]['name'] == 'Test (Kite)'
