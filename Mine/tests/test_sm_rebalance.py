"""Swing Momentum rebalance — the sell/buy ordering, and the manual variant.

Real money: the buys are funded by the sells, so the invariant worth pinning is
that a BUY never goes out until the SELL that pays for it is confirmed filled.
Every broker call here is patched — nothing can reach a broker even if the
route were wrong.

No create_app(): the bare route app from route_app.py carries the same URL map
and starts no scheduler.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.app.routes.api as api
from route_app import build_route_app


def cfg():
    return {
        'id': 'g1', 'index': 'NIFTY500', 'exit_rank': 30, 'investment': 100000,
        'cash_balance': 0.0, 'live_since': '2026-07-01',
        'broker': {'instance': 1, 'broker_type': 'zerodha', 'broker_name': 'Kavin (Kite)'},
        'live_entries': [
            {'symbol': 'SCHNEIDER', 'qty': 7, 'entry_price': 1100.0, 'entry_date': '2026-07-01'},
            {'symbol': 'LLOYDSME', 'qty': 4, 'entry_price': 1700.0, 'entry_date': '2026-07-01'},
        ],
    }


@pytest.fixture
def store(monkeypatch):
    """The live-configs JSON, in memory. Nothing touches the real file."""
    state = {'configs': [cfg()]}
    monkeypatch.setattr(api, '_sm_load_live_configs', lambda: state['configs'])
    monkeypatch.setattr(api, '_sm_save_live_configs', lambda c: state.update(configs=c))
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


# ── _sm_wait_for_fills ────────────────────────────────────────────────────

def test_wait_returns_when_every_order_is_terminal(monkeypatch):
    books = iter([{}, {'1': ('OPEN', None, 0)},
                  {'1': ('EXECUTED', 101.5, 4), '2': ('REJECTED', None, 0)}])
    monkeypatch.setattr(api, '_broker_order_book', lambda k, s: next(books))
    out = api._sm_wait_for_fills('zerodha', None, ['1', '2'], timeout_s=5, poll_s=0)
    assert out['1'] == ('EXECUTED', 101.5, 4)
    assert out['2'][0] == 'REJECTED'


def test_an_order_the_book_never_shows_is_unknown_not_filled(monkeypatch):
    """A book we could not read must never be mistaken for a fill — the caller
    spends the proceeds of whatever this calls EXECUTED."""
    monkeypatch.setattr(api, '_broker_order_book', lambda k, s: {})
    out = api._sm_wait_for_fills('fyers', None, ['9'], timeout_s=0.05, poll_s=0.01)
    assert out['9'][0] == 'UNKNOWN'


def test_zerodha_reads_the_book_as_kite(monkeypatch):
    seen = []
    monkeypatch.setattr(api, '_broker_order_book',
                        lambda k, s: seen.append(k) or {'1': ('EXECUTED', 10.0, 1)})
    api._sm_wait_for_fills('zerodha', None, ['1'], timeout_s=1, poll_s=0)
    assert seen == ['kite']


# ── the automatic path: sells first, buys only after they fill ────────────

@pytest.fixture
def broker(monkeypatch):
    """Records the order sequence; controls what the order book reports."""
    log, fills = [], {}

    def place(broker_type, svc, symbol, qty, side, price=None, **kw):
        oid = f'o{len(log) + 1}'
        log.append((side, symbol, qty, oid))
        return oid, None

    monkeypatch.setattr(api, '_sm_build_order_service', lambda *a, **k: object())
    monkeypatch.setattr(api, '_sm_place_equity_order', place)
    monkeypatch.setattr(api, '_sm_avg_fill_price', lambda *a, **k: None)
    monkeypatch.setattr(api, '_broker_order_book', lambda k, s: fills)
    return {'log': log, 'fills': fills}


def plan(monkeypatch, sells, buys):
    monkeypatch.setattr(api, '_sm_compute_rebalance', lambda c: (sells, buys, None))


SELL_ONE = [{'symbol': 'SCHNEIDER', 'qty': 7, 'price': 1183.0, 'value': 8281.0, 'current_rank': 40}]
BUY_ONE = [{'symbol': 'JINDALSAW', 'qty': 27, 'price': 302.4, 'value': 8164.8, 'current_rank': 9}]


def test_buy_waits_for_the_sell_to_fill(store, client, broker, monkeypatch):
    plan(monkeypatch, SELL_ONE, BUY_ONE)
    broker['fills']['o1'] = ('EXECUTED', 1190.0, 7)
    r = client.post('/api/algo/swing-momentum/configs/g1/rebalance', json={}).get_json()
    assert r['success'] and r['summary'] == {'sold': 1, 'bought': 1, 'failed': 0, 'errors': []}
    assert [x[0] for x in broker['log']] == ['SELL', 'BUY']
    held = {e['symbol'] for e in store['configs'][0]['live_entries']}
    assert held == {'LLOYDSME', 'JINDALSAW'}


def test_the_real_fill_price_is_what_gets_booked(store, client, broker, monkeypatch):
    plan(monkeypatch, SELL_ONE, BUY_ONE)
    broker['fills']['o1'] = ('EXECUTED', 1200.0, 7)   # 17 above the previewed 1183
    client.post('/api/algo/swing-momentum/configs/g1/rebalance', json={})
    exit_rec = store['configs'][0]['exit_history'][-1]
    assert exit_rec['exit_price'] == 1200.0
    assert exit_rec['final_value'] == 8400.0


def test_a_rejected_sell_buys_nothing_and_keeps_the_holding(store, client, broker, monkeypatch):
    plan(monkeypatch, SELL_ONE, BUY_ONE)
    broker['fills']['o1'] = ('REJECTED', None, 0)
    r = client.post('/api/algo/swing-momentum/configs/g1/rebalance', json={}).get_json()
    assert [x[0] for x in broker['log']] == ['SELL']          # no BUY was placed
    assert r['summary']['sold'] == 0 and r['summary']['bought'] == 0
    held = {e['symbol'] for e in store['configs'][0]['live_entries']}
    assert held == {'SCHNEIDER', 'LLOYDSME'}


def test_an_unfilled_sell_drops_only_its_own_replacement(store, client, broker, monkeypatch):
    sells = SELL_ONE + [{'symbol': 'LLOYDSME', 'qty': 4, 'price': 1778.1,
                         'value': 7112.4, 'current_rank': 54}]
    buys = BUY_ONE + [{'symbol': 'GLAND', 'qty': 4, 'price': 1700.0,
                       'value': 6800.0, 'current_rank': 10}]
    plan(monkeypatch, sells, buys)
    broker['fills']['o1'] = ('EXECUTED', 1183.0, 7)
    broker['fills']['o2'] = ('OPEN', None, 0)      # still resting at the timeout
    monkeypatch.setattr(api, '_sm_wait_for_fills',
                        lambda bt, svc, oids, **kw: {o: broker['fills'].get(o, ('UNKNOWN', None, None))
                                                     for o in oids})
    r = client.post('/api/algo/swing-momentum/configs/g1/rebalance', json={}).get_json()
    assert [x[0] for x in broker['log']] == ['SELL', 'SELL', 'BUY']
    assert r['summary']['sold'] == 1 and r['summary']['bought'] == 1
    assert any('LLOYDSME' in e for e in r['summary']['errors'])
    held = {e['symbol'] for e in store['configs'][0]['live_entries']}
    assert held == {'LLOYDSME', 'JINDALSAW'}


# ── the manual path: no orders, just the books ────────────────────────────

def post_manual(client, **body):
    return client.post('/api/algo/swing-momentum/configs/g1/rebalance/manual',
                       json=body).get_json()


def test_manual_records_the_trade_and_places_nothing(store, client, monkeypatch):
    called = []
    monkeypatch.setattr(api, '_sm_place_equity_order',
                        lambda *a, **k: called.append(a) or (None, 'must not be called'))
    r = post_manual(client,
                    sells=[{'symbol': 'SCHNEIDER', 'qty': 7, 'price': 1190.0}],
                    buys=[{'symbol': 'JINDALSAW', 'qty': 27, 'price': 300.0}],
                    date='2026-08-31')
    assert called == []
    assert r['success'] and r['summary']['sold'] == 1 and r['summary']['bought'] == 1
    assert r['proceeds'] == 8330.0 and r['deployed'] == 8100.0
    assert r['cash'] == 230.0

    c = store['configs'][0]
    assert {e['symbol'] for e in c['live_entries']} == {'LLOYDSME', 'JINDALSAW'}
    new = next(e for e in c['live_entries'] if e['symbol'] == 'JINDALSAW')
    assert new['entry_price'] == 300.0 and new['qty'] == 27
    assert new['entry_date'] == '2026-08-31'
    assert c['exit_history'][-1]['exit_price'] == 1190.0
    assert c['monthly_investment_log'][-1]['type'] == 'rebalance'


def test_manual_partial_sell_trims_the_holding(store, client):
    r = post_manual(client, sells=[{'symbol': 'SCHNEIDER', 'qty': 3, 'price': 1200.0}])
    assert r['success']
    kept = next(e for e in store['configs'][0]['live_entries'] if e['symbol'] == 'SCHNEIDER')
    assert kept['qty'] == 4
    assert store['configs'][0]['exit_history'][-1]['qty'] == 3


def test_manual_buy_of_a_held_symbol_averages_in(store, client):
    r = post_manual(client, buys=[{'symbol': 'LLOYDSME', 'qty': 4, 'price': 1900.0}])
    assert r['success']
    e = next(x for x in store['configs'][0]['live_entries'] if x['symbol'] == 'LLOYDSME')
    assert e['qty'] == 8 and e['entry_price'] == 1800.0     # (4*1700 + 4*1900) / 8


def test_manual_rejects_selling_more_than_is_held(store, client):
    r = post_manual(client, sells=[{'symbol': 'SCHNEIDER', 'qty': 9, 'price': 1200.0}])
    assert not r['success'] and 'only 7 held' in r['error']
    # nothing was written
    assert store['configs'][0]['live_entries'][0]['qty'] == 7
    assert 'exit_history' not in store['configs'][0]


def test_manual_rejects_an_unheld_symbol_and_junk_numbers(store, client):
    assert 'not a holding' in post_manual(
        client, sells=[{'symbol': 'INFY', 'qty': 1, 'price': 1.0}])['error']
    assert 'above zero' in post_manual(
        client, sells=[{'symbol': 'SCHNEIDER', 'qty': 7, 'price': 0}])['error']
    assert 'at least one row' in post_manual(client, sells=[], buys=[])['error']


# ── adding a stock to the holdings list ───────────────────────────────────

def post_add(client, **body):
    return client.post('/api/algo/swing-momentum/configs/g1/holdings/add',
                       json=body).get_json()


@pytest.fixture
def prices(monkeypatch):
    monkeypatch.setattr(api, '_sm_current_prices', lambda syms: {'JINDALSAW': 302.55})


def test_add_records_the_stock_without_placing_anything(store, client, prices, monkeypatch):
    called = []
    monkeypatch.setattr(api, '_sm_place_equity_order',
                        lambda *a, **k: called.append(a) or (None, 'must not be called'))
    store['configs'][0]['cash_balance'] = 10000.0
    r = post_add(client, symbol='jindalsaw', qty=28, entry_price=302.76, entry_date='2026-08-31')
    assert called == []
    assert r['success'] and r['deployed'] == 8477.28
    assert r['cash'] == 1522.72

    c = store['configs'][0]
    e = next(x for x in c['live_entries'] if x['symbol'] == 'JINDALSAW')
    assert (e['qty'], e['entry_price'], e['entry_date']) == (28, 302.76, '2026-08-31')
    assert e['order']['status'] == 'manual'
    assert c['monthly_investment_log'][-1]['type'] == 'add'
    assert c['monthly_investment_log'][-1]['amount'] == 0.0   # internal, not a new deposit


def test_add_without_a_price_uses_the_live_one(store, client, prices):
    r = post_add(client, symbol='JINDALSAW', qty=2)
    assert r['success'] and r['entry']['entry_price'] == 302.55


def test_add_refuses_a_symbol_already_held(store, client, prices):
    r = post_add(client, symbol='SCHNEIDER', qty=1, entry_price=1200.0)
    assert not r['success'] and 'already held' in r['error']
    assert len(store['configs'][0]['live_entries']) == 2


def test_add_refuses_junk(store, client, prices):
    assert 'Symbol is required' in post_add(client, qty=1)['error']
    assert 'above zero' in post_add(client, symbol='JINDALSAW', qty=0)['error']
    assert 'No price available' in post_add(client, symbol='UNKNOWNCO', qty=1)['error']


def test_add_with_an_order_books_the_fill_price(store, client, prices, broker):
    broker['fills']['o1'] = ('EXECUTED', 305.10, 28)
    r = post_add(client, symbol='JINDALSAW', qty=28, place_order=True)
    assert [x[0] for x in broker['log']] == ['BUY']
    assert r['success'] and not r['warning']
    e = r['entry']
    assert e['entry_price'] == 305.10 and e['order']['status'] == 'filled'


def test_a_rejected_buy_records_nothing(store, client, prices, broker):
    broker['fills']['o1'] = ('REJECTED', None, 0)
    r = post_add(client, symbol='JINDALSAW', qty=28, place_order=True)
    assert not r['success'] and 'rejected' in r['error']
    assert {e['symbol'] for e in store['configs'][0]['live_entries']} == {'SCHNEIDER', 'LLOYDSME'}


def test_an_unconfirmed_buy_is_recorded_provisionally(store, client, prices, broker, monkeypatch):
    """Still resting at the broker when the wait times out: keep the position,
    but say the price is the quote rather than a fill."""
    monkeypatch.setattr(api, '_sm_wait_for_fills',
                        lambda bt, svc, oids, **kw: {str(o): ('OPEN', None, 0) for o in oids})
    r = post_add(client, symbol='JINDALSAW', qty=28, place_order=True, entry_price=302.55)
    assert r['success'] and 'not confirmed filled' in r['warning']
    assert r['entry']['entry_price'] == 302.55
    assert r['entry']['order']['status'] == 'placed'


# ── what "Invested" means on the card ─────────────────────────────────────

@pytest.fixture
def quotes(monkeypatch):
    """Live prices for the two holdings, so /signal needs no network."""
    class Provider:
        def quote(self, syms):
            px = {'SCHNEIDER': 1200.0, 'LLOYDSME': 1800.0}
            return {f'NSE:{s}-EQ': {'last_price': p, 'ohlc': {'close': p}}
                    for s, p in px.items()}
    monkeypatch.setattr(api, 'get_data_provider', lambda *a, **k: Provider())


def test_total_investment_is_money_in_not_cost_basis(store, client, quotes):
    """The card's Deployed is the cost of stock held now; Invested is what the
    user actually paid in. A SIP and a SWP move only the second one."""
    c = store['configs'][0]
    c['rebalance_freq'] = 'monthly'
    c['investment']     = 100000
    c['monthly_investment_log'] = [
        {'date': '2026-08-10', 'amount': 20000.0, 'type': 'sip'},
        {'date': '2026-08-20', 'amount': -5000.0, 'type': 'swp'},
        {'date': '2026-08-25', 'amount': 0.0, 'type': 'rebalance'},   # internal
    ]
    d = client.get('/api/algo/swing-momentum/signal/g1').get_json()
    assert d['success']
    assert d['total_investment'] == 115000.0        # 100000 + 20000 - 5000
    assert d['configured_investment'] == 100000
    # cost basis of the two holdings: 7*1100 + 4*1700
    assert d['total_invested'] == 14500.0
    assert d['total_investment'] != d['total_invested']


def test_a_rebalance_does_not_change_total_investment(store, client, quotes):
    c = store['configs'][0]
    c['rebalance_freq'] = 'monthly'
    c['investment']     = 100000
    c['monthly_investment_log'] = [{'date': '2026-08-25', 'amount': 0.0, 'type': 'rebalance'}]
    d = client.get('/api/algo/swing-momentum/signal/g1').get_json()
    assert d['total_investment'] == 100000.0
