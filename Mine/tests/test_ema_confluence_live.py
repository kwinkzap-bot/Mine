"""EMA Confluence — LIVE execution (EMA_CONFLUENCE_MODE=live).

The strategy is untouched by the mode; these pin what "act" means once real
orders are involved: which brokers are asked, what they are asked for, how
the fills become the position, and — above all — that a position is closed
the way it was opened. The leg decides, never the flag.

Everything here drives the real _tick with fake broker services that record
each order they are handed. No env file, no network, no thread.
"""
import json
from datetime import date, datetime

import pytest

from trading_app.algo.ema_confluence import ema_confluence_algo as eca
from trading_app.algo.ema_confluence.ema_confluence_algo import EmaConfluenceAlgo

SEP = {'symbol': 'NSE:NHPC26SEPFUT', 'expiry': date(2026, 9, 29), 'lot_size': 5400}
OCT = {'symbol': 'NSE:NHPC26OCTFUT', 'expiry': date(2026, 10, 27), 'lot_size': 5400}
SPOT = 'NSE:NHPC-EQ'
CHAIN = [SEP, OCT]

NOW      = datetime(2026, 9, 8, 11, 0)     # Tuesday, mid-session, no roll due
ROLL_NOW = datetime(2026, 9, 24, 12, 0)    # SEP rolls: Thu 24 (Tue 29 expiry, back 3 sessions)


class FakeProvider:
    def __init__(self, prices, chain=CHAIN):
        self.fyers = object()
        self.prices = prices
        self.chain = chain

    def list_future_contracts(self, symbol):
        return list(self.chain)

    def ltp(self, tokens):
        return {t: {'last_price': p} for t, p in self.prices.items() if t in tokens}


# ── fake broker services ─────────────────────────────────────────────────

class FakeKite:
    """KiteService as this module uses it: place_option_order with a
    tradingsymbol override, and get_order_status read back by id."""

    def __init__(self, fill=None, reject=False, accept_then_reject=False, never_confirm=False):
        self.fill = fill
        self.reject = reject
        self.accept_then_reject = accept_then_reject
        self.never_confirm = never_confirm
        self.orders = []          # every order placed, in order
        self._n = 0

    def place_option_order(self, **kw):
        self.orders.append(kw)
        if self.reject:
            return {'success': False, 'error': 'Insufficient funds'}
        self._n += 1
        return {'success': True, 'order_id': f'K{self._n}', 'price': 0}

    def get_order_status(self, order_id):
        if self.never_confirm:
            return {'success': False, 'error': 'timeout'}
        if self.accept_then_reject:
            return {'success': True, 'status': 'REJECTED', 'filled_quantity': 0, 'average_price': 0}
        qty = next(o['quantity'] for o in self.orders if f"K{self.orders.index(o) + 1}" == order_id)
        return {'success': True, 'status': 'COMPLETE', 'filled_quantity': qty, 'average_price': self.fill}


class FakeFyers:
    def __init__(self, fill=None, reject=False):
        self.fill = fill
        self.reject = reject
        self.orders = []
        self._n = 0

    def place_order(self, **kw):
        self.orders.append(kw)
        if self.reject:
            return {'success': False, 'error': 'Not authenticated - missing access_token'}
        self._n += 1
        return {'success': True, 'order_id': f'F{self._n}', 'price': 0}

    def get_orderbook(self):
        return {'success': True, 'orders': [
            {'id': f'F{i + 1}', 'status': 2, 'filledQty': o['quantity'], 'tradedPrice': self.fill}
            for i, o in enumerate(self.orders)]}


def _brokers(*specs):
    return [{'idx': idx, 'kind': kind, 'name': f'b{idx}', 'svc': svc, 'lots': lots}
            for idx, kind, svc, lots in specs]


@pytest.fixture
def algo(tmp_path, monkeypatch):
    monkeypatch.setattr(eca, 'STATE_FILE', str(tmp_path / 'state.json'))
    monkeypatch.setattr(eca, 'HISTORY_FILE', str(tmp_path / 'history.json'))
    monkeypatch.setattr(eca, 'ALL_HISTORY_FILE', str(tmp_path / 'all_history.json'))
    monkeypatch.setattr(eca, '_FILL_POLL_WAIT_SECS', 0)
    a = EmaConfluenceAlgo(username='test-user')
    monkeypatch.setattr(a, '_run_daily_scan', lambda *A, **K: None)
    monkeypatch.setattr(a, '_uvar', lambda key, default='': default)
    monkeypatch.setattr(a, '_notify_new_entry', lambda *A, **K: None)
    monkeypatch.setattr(a, '_notify_exit', lambda *A, **K: None)
    monkeypatch.setattr(a, '_notify_roll', lambda *A, **K: None)
    a.alerts = []
    monkeypatch.setattr(a, '_alert_order_failure', lambda sym, what, detail: a.alerts.append((sym, what, detail)))
    # Sessions are injected per test; a refresh must never reach the env.
    monkeypatch.setattr(a, '_get_live_brokers', lambda: list(a._broker_list))
    a._live = True
    a._brokers_refreshed_on = date.today().isoformat()
    a._history_path = tmp_path / 'history.json'
    return a


def _state(stocks):
    return {'last_scan_date': date.today().isoformat(), 'stocks': stocks}


def _history(algo):
    try:
        with open(algo._history_path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def _watching(**over):
    s = {'phase': 'watching', 'direction': 'Long', 'trigger_level': 80.0,
         'sl_level': 74.0, 'target_pct': 8.0, 'signal_date': '2026-09-01',
         'future_token': SEP['symbol'], 'future_month': 'SEP 2026',
         'future_expiry': '2026-09-29', 'lot_size': 5400}
    s.update(over)
    return s


def _live_position(**over):
    s = {'phase': 'in_position', 'direction': 'Long', 'trigger_level': 80.0,
         'sl_level': 74.0, 'target_level': 86.4, 'target_pct': 8.0,
         'signal_date': '2026-09-01', 'spot_entry_price': 80.0,
         'entry_price': 80.4, 'entry_time': '2026-09-02T10:15:00', 'qty': 5400,
         'ltp': 80.4, 'future_token': SEP['symbol'], 'future_month': 'SEP 2026',
         'future_expiry': '2026-09-29', 'lot_size': 5400, 'mode': 'live',
         'broker_legs': [{'broker_idx': 1, 'kind': 'zerodha', 'lots': 1, 'qty': 5400,
                          'tradingsymbol': 'NHPC26SEPFUT', 'token': SEP['symbol'],
                          'entry_order_ids': ['K1'], 'entry_status': 'FILLED',
                          'entry_qty': 5400, 'entry_price': 80.4}]}
    s.update(over)
    return s


# ── entry ────────────────────────────────────────────────────────────────

def test_live_entry_goes_to_every_flagged_broker_as_an_nrml_market_order(algo):
    kite, fyers = FakeKite(fill=80.5), FakeFyers(fill=80.7)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1), (3, 'fyers', fyers, 2))
    s = _watching()
    algo._tick(FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1}), True, _state({'NHPC': s}), True, 1, NOW)

    # Kite: bare tradingsymbol, carry-forward product, no price = MARKET.
    assert kite.orders == [dict(symbol='NHPC', strike=0, option_type='FUT', transaction_type='BUY',
                                quantity=5400, product='NRML', tradingsymbol='NHPC26SEPFUT')]
    # Fyers: exchange-prefixed, side 1 = BUY, type 2 = MARKET, MARGIN carries.
    assert fyers.orders == [dict(symbol='NSE:NHPC26SEPFUT', side=1, quantity=10800,
                                 order_type=2, product_type='MARGIN')]

    assert s['phase'] == 'in_position'
    assert s['mode'] == 'live'
    assert s['qty'] == 16200                                   # 1 lot + 2 lots
    # The position's entry is the qty-weighted average of the ACTUAL fills,
    # not the quote the trigger was seen at.
    assert s['entry_price'] == round((5400 * 80.5 + 10800 * 80.7) / 16200, 2)
    assert s['spot_entry_price'] == 80.1
    assert s['target_level'] == round(80.1 * 1.08, 2)          # still off the spot fill
    assert [(l['broker_idx'], l['entry_order_ids'], l['entry_price']) for l in s['broker_legs']] == \
        [(1, ['K1'], 80.5), (3, ['F1'], 80.7)]


def test_a_short_entry_sells_and_the_fyers_side_is_minus_one(algo):
    fyers = FakeFyers(fill=79.9)
    algo._broker_list = _brokers((3, 'fyers', fyers, 1))
    s = _watching(direction='Short', trigger_level=80.0, sl_level=86.0)
    algo._tick(FakeProvider({SEP['symbol']: 79.95, SPOT: 79.9}), True, _state({'NHPC': s}), True, 1, NOW)
    assert fyers.orders[0]['side'] == -1
    assert s['phase'] == 'in_position'


def test_every_broker_refusing_drops_the_setup_instead_of_opening_a_position(algo):
    kite = FakeKite(reject=True)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _watching()
    algo._tick(FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1}), True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'pending_scan'                        # not left armed to re-fire every 15s
    assert 'entry_price' not in s and 'broker_legs' not in s
    assert _history(algo) == []
    assert [a[:2] for a in algo.alerts] == [('NHPC', 'entry')]


def test_one_broker_refusing_opens_the_position_on_the_others_only(algo):
    kite, fyers = FakeKite(reject=True), FakeFyers(fill=80.7)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1), (3, 'fyers', fyers, 1))
    s = _watching()
    algo._tick(FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1}), True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'in_position'
    assert [l['broker_idx'] for l in s['broker_legs']] == [3]
    assert s['qty'] == 5400 and s['entry_price'] == 80.7


def test_an_accepted_order_that_the_broker_then_rejects_holds_nothing(algo):
    kite = FakeKite(accept_then_reject=True)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _watching()
    algo._tick(FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1}), True, _state({'NHPC': s}), True, 1, NOW)
    assert s['phase'] == 'pending_scan'


def test_an_unconfirmed_fill_is_carried_at_the_mark_and_flagged(algo):
    kite = FakeKite(never_confirm=True)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _watching()
    algo._tick(FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1}), True, _state({'NHPC': s}), True, 1, NOW)
    assert s['phase'] == 'in_position'
    assert s['entry_price'] == 80.55                           # the mark, since no fill came back
    assert s['broker_legs'][0]['entry_status'] == 'UNCONFIRMED'


def test_the_kill_switch_still_gates_live_entries(algo):
    kite = FakeKite(fill=80.5)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _watching()
    algo._tick(FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1}), True, _state({'NHPC': s}),
               False, 1, NOW)                                  # EMA_CONFLUENCE_ACTIVE=false
    assert kite.orders == []
    assert s['phase'] == 'pending_scan'


def test_live_flag_without_a_broker_places_nothing_and_says_so(algo):
    algo._broker_list = []
    s = _watching()
    algo._tick(FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1}), True, _state({'NHPC': s}), True, 1, NOW)
    assert s['phase'] == 'pending_scan'
    assert algo.alerts and algo.alerts[0][1] == 'entry'


def test_a_leg_over_the_freeze_limit_is_split_into_orders(algo):
    kite = FakeKite(fill=80.5)
    algo._broker_list = _brokers((1, 'zerodha', kite, 30))
    s = _watching()
    algo._tick(FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1}), True, _state({'NHPC': s}), True, 1, NOW)
    assert [o['quantity'] for o in kite.orders] == [27 * 5400, 3 * 5400]
    assert s['broker_legs'][0]['entry_order_ids'] == ['K1', 'K2']
    assert s['qty'] == 30 * 5400


# ── exit ─────────────────────────────────────────────────────────────────

def test_sl_on_a_live_position_sells_at_the_broker_and_books_the_real_fill(algo):
    kite = FakeKite(fill=74.9)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _live_position()
    algo._tick(FakeProvider({SEP['symbol']: 74.6, SPOT: 73.9}), True, _state({'NHPC': s}), True, 1, NOW)

    assert kite.orders == [dict(symbol='NHPC', strike=0, option_type='FUT', transaction_type='SELL',
                                quantity=5400, product='NRML', tradingsymbol='NHPC26SEPFUT')]
    hist = _history(algo)
    assert len(hist) == 1
    assert hist[0]['reason'] == 'SL'
    assert hist[0]['mode'] == 'live'
    assert hist[0]['exit_price'] == 74.9                       # the broker's fill, not the 74.6 quote
    assert hist[0]['spot_exit_price'] == 73.9
    assert hist[0]['pnl'] == round((74.9 - 80.4) * 5400, 2)
    assert hist[0]['broker_legs'][0]['exit_order_ids'] == ['K1']
    assert s['phase'] == 'pending_scan'
    assert 'broker_legs' not in s and 'exit_pending' not in s


def test_a_refused_exit_keeps_the_position_and_retries_whatever_spot_does_next(algo):
    kite = FakeKite(fill=74.9, reject=True)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _live_position()
    algo._tick(FakeProvider({SEP['symbol']: 74.6, SPOT: 73.9}), True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'in_position'                         # still held — the broker said no
    assert s['exit_pending'] == 'SL'
    assert _history(algo) == []
    assert [a[:2] for a in algo.alerts] == [('NHPC', 'exit')]

    # Next tick: spot has bounced back above the SL. The strategy already
    # spoke, so that changes nothing — the exit is retried, and now goes.
    kite.reject = False
    algo._tick(FakeProvider({SEP['symbol']: 75.6, SPOT: 75.2}), True, _state({'NHPC': s}), True, 1, NOW)
    assert len(kite.orders) == 2 and kite.orders[-1]['transaction_type'] == 'SELL'
    assert _history(algo)[0]['reason'] == 'SL'
    assert _history(algo)[0]['spot_exit_price'] == 73.9        # the spot that DECIDED it
    assert s['phase'] == 'pending_scan'


def test_two_legs_exit_at_their_own_brokers_and_book_the_weighted_fill(algo):
    kite, fyers = FakeKite(fill=86.9), FakeFyers(fill=87.1)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1), (3, 'fyers', fyers, 1))
    legs = [{'broker_idx': 1, 'kind': 'zerodha', 'lots': 1, 'qty': 5400, 'tradingsymbol': 'NHPC26SEPFUT',
             'token': SEP['symbol'], 'entry_order_ids': ['K1'], 'entry_status': 'FILLED',
             'entry_qty': 5400, 'entry_price': 80.4},
            {'broker_idx': 3, 'kind': 'fyers', 'lots': 1, 'qty': 5400, 'tradingsymbol': 'NSE:NHPC26SEPFUT',
             'token': SEP['symbol'], 'entry_order_ids': ['F1'], 'entry_status': 'FILLED',
             'entry_qty': 5400, 'entry_price': 80.4}]
    s = _live_position(qty=10800, broker_legs=legs)
    algo._tick(FakeProvider({SEP['symbol']: 87.0, SPOT: 86.5}), True, _state({'NHPC': s}), True, 1, NOW)

    assert kite.orders[0]['transaction_type'] == 'SELL' and kite.orders[0]['tradingsymbol'] == 'NHPC26SEPFUT'
    assert fyers.orders[0]['side'] == -1 and fyers.orders[0]['symbol'] == 'NSE:NHPC26SEPFUT'
    assert _history(algo)[0]['reason'] == 'TARGET'
    assert _history(algo)[0]['exit_price'] == 87.0             # (86.9 + 87.1) / 2, equal qty


# ── the leg decides, not the flag ────────────────────────────────────────

def test_a_paper_position_under_the_live_flag_is_never_sold_at_a_broker(algo):
    kite = FakeKite(fill=74.9)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _live_position()
    del s['broker_legs']                                       # opened on paper, before the flag
    s['mode'] = 'paper'
    algo._tick(FakeProvider({SEP['symbol']: 74.6, SPOT: 73.9}), True, _state({'NHPC': s}), True, 1, NOW)

    assert kite.orders == []
    assert _history(algo)[0]['mode'] == 'paper'
    assert _history(algo)[0]['exit_price'] == 74.6             # booked at the quote, as paper always was


def test_a_live_position_is_flattened_at_the_broker_even_after_the_flag_goes_back_to_paper(algo):
    kite = FakeKite(fill=74.9)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    algo._live = False                                         # EMA_CONFLUENCE_MODE back to paper
    s = _live_position()
    algo._tick(FakeProvider({SEP['symbol']: 74.6, SPOT: 73.9}), True, _state({'NHPC': s}), True, 1, NOW)

    assert len(kite.orders) == 1 and kite.orders[0]['transaction_type'] == 'SELL'
    assert _history(algo)[0]['mode'] == 'live'
    assert _history(algo)[0]['exit_price'] == 74.9


def test_a_broker_that_holds_a_leg_is_reached_for_the_exit_even_with_its_flag_off(algo, monkeypatch):
    kite = FakeKite(fill=74.9)
    algo._broker_list = []                                     # BROKER_1_EMA_ACTIVE turned off
    env = {'BROKER_1_TYPE': 'zerodha', 'BROKER_1_NAME': 'Saranya (Kite)'}
    monkeypatch.setattr(algo, '_uvar', lambda key, default='': env.get(key, default))
    built = []
    monkeypatch.setattr(algo, '_build_broker', lambda i, kind, name: (built.append((i, kind)) or
                        {'idx': i, 'kind': kind, 'name': name, 'svc': kite, 'lots': 1}))
    s = _live_position()
    algo._tick(FakeProvider({SEP['symbol']: 74.6, SPOT: 73.9}), True, _state({'NHPC': s}), True, 1, NOW)

    assert built == [(1, 'zerodha')]
    assert kite.orders[0]['transaction_type'] == 'SELL'
    assert s['phase'] == 'pending_scan'


# ── roll ─────────────────────────────────────────────────────────────────

def test_a_live_position_rolls_at_the_broker_near_out_far_in(algo):
    kite = FakeKite(fill=81.0)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _live_position()
    algo._tick(FakeProvider({SEP['symbol']: 80.9, OCT['symbol']: 81.4, SPOT: 80.6}), True,
               _state({'NHPC': s}), True, 1, ROLL_NOW)

    assert [(o['transaction_type'], o['tradingsymbol']) for o in kite.orders] == \
        [('SELL', 'NHPC26SEPFUT'), ('BUY', 'NHPC26OCTFUT')]
    hist = _history(algo)
    assert len(hist) == 1 and hist[0]['reason'] == 'ROLL' and hist[0]['mode'] == 'live'
    assert hist[0]['exit_price'] == 81.0                       # the near leg's real fill
    assert s['phase'] == 'in_position'
    assert s['future_token'] == OCT['symbol']
    assert s['entry_price'] == 81.0                            # the far leg's real fill
    assert s['broker_legs'][0]['tradingsymbol'] == 'NHPC26OCTFUT'
    assert s['broker_legs'][0]['entry_order_ids'] == ['K2']
    assert s['sl_level'] == 74.0 and s['target_level'] == 86.4  # untouched, as ever


def test_a_roll_whose_near_exit_is_refused_stays_on_the_near_month(algo):
    kite = FakeKite(fill=81.0, reject=True)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _live_position()
    algo._tick(FakeProvider({SEP['symbol']: 80.9, OCT['symbol']: 81.4, SPOT: 80.6}), True,
               _state({'NHPC': s}), True, 1, ROLL_NOW)

    assert _history(algo) == []
    assert s['phase'] == 'in_position'
    assert s['future_token'] == SEP['symbol']
    assert s['roll_to_token'] == OCT['symbol']                 # still pending, retried next tick
    assert s['roll_pending'] is True
    assert len(kite.orders) == 1                               # no far-month BUY without the near SELL


def test_a_roll_whose_far_entry_fails_leaves_the_account_flat_and_says_so(algo):
    class SellOnlyKite(FakeKite):
        def place_option_order(self, **kw):
            if kw['transaction_type'] == 'BUY':
                self.orders.append(kw)
                return {'success': False, 'error': 'Margin exceeds'}
            return super().place_option_order(**kw)

    kite = SellOnlyKite(fill=81.0)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    s = _live_position()
    algo._tick(FakeProvider({SEP['symbol']: 80.9, OCT['symbol']: 81.4, SPOT: 80.6}), True,
               _state({'NHPC': s}), True, 1, ROLL_NOW)

    assert _history(algo)[0]['reason'] == 'ROLL'               # the near leg WAS closed
    assert s['phase'] == 'pending_scan'                        # nothing is held any more
    assert 'broker_legs' not in s
    assert ('NHPC', 'roll') in [a[:2] for a in algo.alerts]


# ── universe drop ────────────────────────────────────────────────────────

def test_a_dropped_symbols_live_position_is_flattened_at_the_broker_not_booked_on_paper(algo):
    kite = FakeKite(fill=80.2)
    algo._broker_list = _brokers((1, 'zerodha', kite, 1))
    state = _state({'NOTINUNIVERSE': _live_position()})
    algo._retire_dropped_symbols(state, {'SOMETHINGELSE': {}})

    s = state['stocks']['NOTINUNIVERSE']                       # kept, not popped
    assert s['phase'] == 'in_position' and s['exit_pending'] == 'UNIVERSE_DROP'
    assert _history(algo) == []                                # nothing booked at 08:30

    algo._tick(FakeProvider({SEP['symbol']: 80.3, SPOT: 80.0}), True, state, True, 1, NOW)
    assert kite.orders[0]['transaction_type'] == 'SELL'
    assert _history(algo)[0]['reason'] == 'UNIVERSE_DROP' and _history(algo)[0]['exit_price'] == 80.2
    assert 'NOTINUNIVERSE' not in state['stocks']              # gone once flat


# ── broker selection ─────────────────────────────────────────────────────

def test_only_active_ema_flagged_zerodha_or_fyers_slots_become_live_brokers(monkeypatch):
    a = EmaConfluenceAlgo(username='test-user')
    env = {
        'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true',  'BROKER_1_EMA_ACTIVE': 'true',
        'BROKER_2_TYPE': 'zerodha', 'BROKER_2_ACTIVE': 'true',  'BROKER_2_EMA_ACTIVE': 'false',
        'BROKER_3_TYPE': 'fyers',   'BROKER_3_ACTIVE': 'false', 'BROKER_3_EMA_ACTIVE': 'true',
        'BROKER_5_TYPE': 'kotak',   'BROKER_5_ACTIVE': 'true',  'BROKER_5_EMA_ACTIVE': 'true',
        'BROKER_6_TYPE': 'dhan',    'BROKER_6_ACTIVE': 'true',  'BROKER_6_EMA_ACTIVE': 'true',
        'BROKER_7_TYPE': 'fyers',   'BROKER_7_ACTIVE': 'true',  'BROKER_7_EMA_ACTIVE': 'true',
        'BROKER_7_EMA_LOTS': '3',
        'EMA_CONFLUENCE_LOTS': '2',
    }
    monkeypatch.setattr(a, '_uvar', lambda key, default='': env.get(key, default))
    monkeypatch.setattr(a, '_build_broker', lambda i, kind, name: {'idx': i, 'kind': kind, 'name': name,
                                                                   'svc': object(), 'lots': a._broker_lots(i)})
    from trading_app.app.utils.user_env import UserEnvManager
    monkeypatch.setattr(UserEnvManager, '_user_env_cache', {}, raising=False)

    brokers = a._get_live_brokers()
    # 1: yes. 2: EMA flag off. 3: broker inactive. 5/6: kotak/dhan cannot carry
    # an NRML future here — refused. 7: yes, sized by its own lots.
    assert [(b['idx'], b['kind'], b['lots']) for b in brokers] == [(1, 'zerodha', 2), (7, 'fyers', 3)]


def test_mode_is_live_only_on_the_explicit_flag(monkeypatch):
    a = EmaConfluenceAlgo(username='test-user')
    for raw, expected in (('', 'paper'), ('paper', 'paper'), ('LIVE', 'live'), ('live', 'live'), ('yes', 'paper')):
        monkeypatch.setattr(a, '_uvar', lambda key, default='', raw=raw: raw if key == 'EMA_CONFLUENCE_MODE' else default)
        assert a._mode() == expected, raw


# ── the status route tells the page which mode and which accounts ────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import trading_app.app.routes.api as api
    from route_app import build_route_app
    monkeypatch.setattr(api, 'check_auth', lambda: None)
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'last_scan_date': None, 'stocks': {
        'NHPC': _live_position(), 'SBIN': {'phase': 'no_setup'}}}))
    monkeypatch.setattr(api, '_EMAC_STATE_PATH', str(state))
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


def _env(monkeypatch, values):
    from trading_app.app.utils.user_env import UserEnvManager
    monkeypatch.setattr(UserEnvManager, 'get_user_var',
                        staticmethod(lambda user, key, default='': values.get(key, default)))


def test_status_reports_live_mode_and_the_flagged_accounts(client, monkeypatch):
    _env(monkeypatch, {
        'EMA_CONFLUENCE_MODE': 'live', 'EMA_CONFLUENCE_LOTS': '2',
        'BROKER_1_TYPE': 'zerodha', 'BROKER_1_NAME': 'Saranya (Kite)', 'BROKER_1_ACTIVE': 'true',
        'BROKER_1_EMA_ACTIVE': 'true', 'BROKER_1_EMA_LOTS': '1',
        'BROKER_2_TYPE': 'zerodha', 'BROKER_2_ACTIVE': 'true', 'BROKER_2_EMA_ACTIVE': 'false',
        'BROKER_6_TYPE': 'dhan', 'BROKER_6_NAME': 'Saranya (Dhan)', 'BROKER_6_ACTIVE': 'true',
        'BROKER_6_EMA_ACTIVE': 'true',
    })
    data = client.get('/api/algo/ema-confluence/status').get_json()
    assert data['success'] and data['mode'] == 'live'
    assert data['live_brokers'] == [
        {'idx': 1, 'type': 'zerodha', 'name': 'Saranya (Kite)', 'lots': 1, 'supported': True},
        {'idx': 6, 'type': 'dhan', 'name': 'Saranya (Dhan)', 'lots': 2, 'supported': False},
    ]
    assert data['live_positions'] == ['NHPC']


def test_status_defaults_to_paper_with_no_flag(client, monkeypatch):
    _env(monkeypatch, {})
    data = client.get('/api/algo/ema-confluence/status').get_json()
    assert data['mode'] == 'paper' and data['live_brokers'] == []
    assert data['live_positions'] == ['NHPC']          # a held live leg is shown whatever the flag
