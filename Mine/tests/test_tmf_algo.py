"""Regression tests for the 30-Min Opening Fakeout live algo.

These pin down the three defects seen on the 2026-08-03 live day:
  1. the reconciliation sweep adopted another strategy's NIFTY option leg,
  2. the entry LIMIT rested at the trigger — the wrong side of the market —
     so VOLTAS never filled and JSWSTEEL filled 15 of 392 shares, and
  3. a partially-filled Target leg was misread as an under-filled entry, so
     COLPAL booked +₹2,511 on a trade Kite settled at +₹8,152.80.
"""
import pytest

from trading_app.algo.thirty_min_fakeout.tmf_algo import TMFAlgo


class FakeKite:
    """Just enough of kite.positions() for the reconciliation sweep."""

    def __init__(self, net):
        self._net = net

    def positions(self):
        return {'net': self._net}


class FakeService:
    """Records every order the algo places, and answers status lookups from
    a scripted {order_id: (status, filled_qty, avg_price)} table."""

    def __init__(self, net=(), fills=None):
        self.kite = FakeKite(list(net))
        self.fills = fills or {}
        self.placed = []
        self.cancelled = []
        self._next_id = 9000

    def _new_id(self):
        self._next_id += 1
        return str(self._next_id)

    def get_tick_size(self, symbol, exchange='NSE'):
        return 0.05

    def place_equity_order(self, tradingsymbol, transaction_type, quantity, price, product='MIS'):
        self.placed.append({'kind': 'LIMIT', 'symbol': tradingsymbol, 'txn': transaction_type,
                            'qty': quantity, 'price': price})
        return {'success': True, 'order_id': self._new_id()}

    def place_equity_sl_order(self, tradingsymbol, transaction_type, quantity, trigger_price, product='MIS'):
        self.placed.append({'kind': 'SL', 'symbol': tradingsymbol, 'txn': transaction_type,
                            'qty': quantity, 'price': trigger_price})
        return {'success': True, 'order_id': self._new_id()}

    def cancel_order(self, order_id, variety=None):
        self.cancelled.append(str(order_id))
        return {'success': True}

    def get_order_status(self, order_id):
        entry = self.fills.get(str(order_id))
        if entry is None:
            return {'success': True, 'status': 'OPEN', 'filled_quantity': 0, 'average_price': 0}
        status, filled, avg = entry
        return {'success': True, 'status': status, 'filled_quantity': filled, 'average_price': avg}


@pytest.fixture
def algo(tmp_path, monkeypatch):
    a = TMFAlgo('test-user')
    # History/state writes must not touch the real live-algo files.
    monkeypatch.setattr('trading_app.algo.thirty_min_fakeout.tmf_algo.HISTORY_FILE',
                        str(tmp_path / 'hist.json'))
    monkeypatch.setattr('trading_app.algo.thirty_min_fakeout.tmf_algo.ALL_HISTORY_FILE',
                        str(tmp_path / 'all.json'))
    return a


# ── 1. Universe scope: no options, no indices, no other strategies ──────────

def test_reconcile_ignores_non_tmf_positions(algo):
    """The broker's position book is account-wide. Only NSE cash-market
    scrips inside TMF_STOCK_UNIVERSE belong to this strategy."""
    svc = FakeService(net=[
        {'product': 'MIS', 'exchange': 'NFO', 'tradingsymbol': 'NIFTY2680424750PE',
         'quantity': 2600, 'average_price': 170.3},
        {'product': 'MIS', 'exchange': 'NSE', 'tradingsymbol': 'RELIANCE',   # not in the universe
         'quantity': 100, 'average_price': 1400.0},
        {'product': 'MIS', 'exchange': 'NSE', 'tradingsymbol': 'COLPAL',
         'quantity': -237, 'average_price': 2105.0},
        {'product': 'CNC', 'exchange': 'NSE', 'tradingsymbol': 'VOLTAS',     # not intraday
         'quantity': 50, 'average_price': 1334.0},
    ])
    assert set(algo._get_broker_mis_positions(svc)) == {'COLPAL'}


def test_reconcile_leaves_option_position_untouched(algo):
    """The option leg must not be adopted into TMF state, re-armed, or
    squared off by this algo's EOD sweep."""
    svc = FakeService(net=[{'product': 'MIS', 'exchange': 'NFO',
                            'tradingsymbol': 'NIFTY2680424750PE',
                            'quantity': 2600, 'average_price': 170.3}])
    algo._broker_list = [(1, svc)]
    state = {'date': '2026-08-03', 'stocks': {}}
    algo._reconcile_orphaned_positions(state, force_square_off=True)
    assert state['stocks'] == {}
    assert svc.placed == [] and svc.cancelled == []


# ── 2. Entry LIMIT must be marketable (VOLTAS) ──────────────────────────────

def test_short_entry_limit_is_below_the_market(algo, monkeypatch):
    """VOLTAS 2026-08-03: SHORT trigger 1334.4 fires once price is already
    at 1331.2, so a SELL LIMIT at 1334.4 sits ABOVE the market and can only
    fill if price climbs back — it never did, and the order was cancelled
    unfilled. The limit has to be priced through the market instead."""
    svc = FakeService()
    algo._broker_list = [(1, svc)]
    s = {'direction': 'short', 'trigger': 1334.4, 'sl_level': 1343.6, 'ltp': 1331.2}
    algo._fire_entry('VOLTAS', s, capital_per_trade=100000, algo_active=True)

    order = svc.placed[0]
    assert order['txn'] == 'SELL'
    assert order['price'] < s['ltp'], 'a SELL LIMIT at or above the market is not marketable'
    assert order['price'] < s['trigger']
    assert order['price'] >= s['ltp'] * 0.99, 'slippage cap is too loose'
    assert s['phase'] == 'pending_entry'


def test_long_entry_limit_is_above_the_market(algo):
    svc = FakeService()
    algo._broker_list = [(1, svc)]
    s = {'direction': 'long', 'trigger': 1274.8, 'sl_level': 1268.7, 'ltp': 1276.0}
    algo._fire_entry('JSWSTEEL', s, capital_per_trade=100000, algo_active=True)

    order = svc.placed[0]
    assert order['txn'] == 'BUY'
    assert order['price'] > s['ltp'], 'a BUY LIMIT at or below the market is not marketable'
    assert order['price'] <= s['ltp'] * 1.01


def test_entry_limit_falls_back_to_the_trigger_without_an_ltp(algo):
    svc = FakeService()
    algo._broker_list = [(1, svc)]
    s = {'direction': 'short', 'trigger': 2105.0, 'sl_level': 2116.6}
    algo._fire_entry('COLPAL', s, capital_per_trade=100000, algo_active=True)
    assert svc.placed[0]['price'] < 2105.0


# ── 3. A partially filled exit leg is not an under-filled entry (COLPAL) ────

def test_partially_filled_target_is_booked_not_dropped(algo):
    """COLPAL 2026-08-03: 164 of 237 shares had already exited on the Target
    leg when the sweep ran. Reading that as 'the entry filled further' threw
    the 164 away — the app booked ₹2,511 where Kite settled ₹8,152.80."""
    svc = FakeService(
        net=[{'product': 'MIS', 'exchange': 'NSE', 'tradingsymbol': 'COLPAL',
              'quantity': -73, 'average_price': 2105.0}],
        fills={'TGT1': ('OPEN', 164, 2070.6), 'SL1': ('OPEN', 0, 0)},
    )
    algo._broker_list = [(1, svc)]
    state = {'date': '2026-08-03', 'stocks': {'COLPAL': {
        'phase': 'in_position', 'direction': 'short', 'trigger': 2105.0,
        'sl_level': 2116.6, 'target_level': 2070.6, 'entry_price': 2105.0,
        'qty': 237, 'ltp': 2070.6,
        'broker_positions': [{'broker_idx': 1, 'entry_order_id': 'E1', 'filled': True,
                              'entry_price': 2105.0, 'filled_qty': 237,
                              'sl_order_id': 'SL1', 'target_order_id': 'TGT1'}],
    }}}

    algo._reconcile_orphaned_positions(state, force_square_off=False)

    s = state['stocks']['COLPAL']
    # The 164 that already exited are booked at the Target price.
    assert s['realized_pnl'] == pytest.approx((2105.0 - 2070.6) * 164, abs=0.01)
    # ...and the remaining 73 are still protected, sized to what is open —
    # an SL still standing for 237 would flip the position 164 the wrong way.
    assert s['broker_positions'][0]['filled_qty'] == 73
    assert {o['kind'] for o in svc.placed if o['qty'] == 73} == {'SL', 'LIMIT'}
    assert {'SL1', 'TGT1'} <= set(svc.cancelled), 'the stale, over-sized legs must be pulled'
    # Together with the 73 the re-armed Target will book on its own fill, the
    # full 237 reaches the P&L — Kite's ₹8,152.80, not the old ₹2,511.
    final = (2105.0 - 2070.6) * 73
    assert s['realized_pnl'] + final == pytest.approx(8152.8, abs=0.01)


def test_entry_still_filling_rearms_upward(algo):
    """The original case the sweep was written for still works: the broker
    holds MORE than this app tracked, because the entry kept filling."""
    svc = FakeService(
        net=[{'product': 'MIS', 'exchange': 'NSE', 'tradingsymbol': 'MAZDOCK',
              'quantity': -210, 'average_price': 2378.8}],
    )
    algo._broker_list = [(1, svc)]
    state = {'date': '2026-08-03', 'stocks': {'MAZDOCK': {
        'phase': 'in_position', 'direction': 'short', 'sl_level': 2400.0,
        'target_level': 2350.0, 'entry_price': 2378.8, 'qty': 50, 'ltp': 2378.0,
        'broker_positions': [{'broker_idx': 1, 'entry_order_id': 'E1', 'filled': True,
                              'entry_price': 2378.8, 'filled_qty': 50,
                              'sl_order_id': 'SL1', 'target_order_id': 'TGT1'}],
    }}}

    algo._reconcile_orphaned_positions(state, force_square_off=False)

    s = state['stocks']['MAZDOCK']
    assert s['broker_positions'][0]['filled_qty'] == 210
    assert s.get('realized_pnl') is None, 'nothing exited — nothing to book'
    assert {o['qty'] for o in svc.placed} == {210}


# ── 4. A rejected square-off is not an exit ─────────────────────────────────

def test_rejected_square_off_is_not_booked_as_a_fill(algo):
    """2026-08-03: the 15:18 cutoff fell past Zerodha's 15:10 MIS deadline,
    every square-off was rejected, and the requested (never-traded) padded
    price was booked anyway — DALBHARAT went in at -₹4,504 on no fill."""
    class RejectingService(FakeService):
        def place_equity_order(self, *a, **kw):
            return {'success': False, 'error': 'Intraday orders (MIS) are allowed only till 3:10 PM.'}

    svc = RejectingService()
    algo._broker_list = [(1, svc)]
    s = {'phase': 'in_position', 'direction': 'short', 'sl_level': 1846.6,
         'target_level': 1795.0, 'entry_price': 1829.0, 'qty': 273, 'ltp': 1827.2,
         'broker_positions': [{'broker_idx': 1, 'entry_order_id': 'E1', 'filled': True,
                               'entry_price': 1829.0, 'filled_qty': 273,
                               'sl_order_id': 'SL1', 'target_order_id': 'TGT1'}]}

    algo._square_off('DALBHARAT', s)

    assert s.get('realized_pnl') is None, 'a rejected order must not book a P&L'
    assert s['broker_positions'][0].get('closed') is not True
    assert s['phase'] == 'in_position'
    assert 'REJECTED' in s['exit_reason']


def test_broker_auto_square_off_is_booked_from_the_position_book(algo):
    """The other half of not inventing an exit: once the app's own square-off
    is rejected, Zerodha closes the position itself at 15:20. Those are the
    real DALBHARAT numbers from Kite — sold 273 @ 1,829.00, bought back @
    1,828.87, +₹35.90 — against the -₹4,504.50 the invented exit booked."""
    svc = FakeService(net=[{
        'product': 'MIS', 'exchange': 'NSE', 'tradingsymbol': 'DALBHARAT',
        'quantity': 0, 'average_price': 0.0,
        'buy_quantity': 273, 'buy_price': 1828.87, 'buy_value': 499281.10,
        'sell_quantity': 273, 'sell_price': 1829.00, 'sell_value': 499317.00,
        'pnl': 35.90,
    }])
    algo._broker_list = [(1, svc)]
    state = {'date': '2026-08-03', 'stocks': {'DALBHARAT': {
        'phase': 'in_position', 'direction': 'short', 'sl_level': 1846.6,
        'target_level': 1795.0, 'entry_price': 1829.0, 'qty': 273, 'ltp': 1827.2,
        'exit_reason': 'Square-off REJECTED — still open at broker (...)',
        'broker_positions': [{'broker_idx': 1, 'entry_order_id': 'E1', 'filled': True,
                              'entry_price': 1829.0, 'filled_qty': 273}],
    }}}

    algo._book_closed_at_broker(state)

    s = state['stocks']['DALBHARAT']
    assert s['exit_price'] == 1828.87
    assert s['realized_pnl'] == 35.90, 'the broker states the realised figure — use it'
    assert s['phase'] == 'done'
    assert s['exit_reason'] == 'Time Exit (broker auto square-off)'


def test_long_auto_square_off_books_the_sell_side(algo):
    """JSWSTEEL from Kite: bought 15 @ 1,274.80, sold @ 1,281.50, +₹100.50 —
    the app had booked -₹112.50 at an exit price that never traded."""
    svc = FakeService(net=[{
        'product': 'MIS', 'exchange': 'NSE', 'tradingsymbol': 'JSWSTEEL',
        'quantity': 0, 'average_price': 0.0,
        'buy_quantity': 15, 'buy_price': 1274.80, 'buy_value': 19122.00,
        'sell_quantity': 15, 'sell_price': 1281.50, 'sell_value': 19222.50,
        'pnl': 100.50,
    }])
    algo._broker_list = [(1, svc)]
    state = {'date': '2026-08-03', 'stocks': {'JSWSTEEL': {
        'phase': 'in_position', 'direction': 'long', 'sl_level': 1268.7,
        'target_level': 1294.7, 'entry_price': 1274.8, 'qty': 15, 'ltp': 1280.1,
        'broker_positions': [{'broker_idx': 1, 'entry_order_id': 'E1', 'filled': True,
                              'entry_price': 1274.8, 'filled_qty': 15}],
    }}}

    algo._book_closed_at_broker(state)

    s = state['stocks']['JSWSTEEL']
    assert s['exit_price'] == 1281.50
    assert s['realized_pnl'] == 100.50
    assert s['phase'] == 'done'


def test_still_open_position_is_not_booked_early(algo):
    """A position the broker has NOT flattened yet must be left alone — the
    sweep runs every tick between the cutoff and 15:30."""
    svc = FakeService(net=[{
        'product': 'MIS', 'exchange': 'NSE', 'tradingsymbol': 'DALBHARAT',
        'quantity': -273, 'average_price': 1829.0,
        'buy_quantity': 0, 'buy_price': 0, 'sell_quantity': 273, 'sell_price': 1829.0,
        'pnl': -1200.0,
    }])
    algo._broker_list = [(1, svc)]
    state = {'date': '2026-08-03', 'stocks': {'DALBHARAT': {
        'phase': 'in_position', 'direction': 'short', 'entry_price': 1829.0, 'qty': 273,
        'broker_positions': [{'broker_idx': 1, 'entry_order_id': 'E1', 'filled': True,
                              'entry_price': 1829.0, 'filled_qty': 273}],
    }}}

    algo._book_closed_at_broker(state)

    s = state['stocks']['DALBHARAT']
    assert s.get('realized_pnl') is None
    assert s['phase'] == 'in_position'


def test_square_off_without_any_price_reference_does_not_crash(algo):
    """An orphan recovered with no setup behind it has no sl_level — reading
    it as a dict key aborted the whole 2026-08-03 EOD sweep."""
    svc = FakeService()
    algo._broker_list = [(1, svc)]
    s = {'phase': 'in_position', 'direction': 'long', 'entry_price': 170.3, 'qty': 2600,
         'broker_positions': [{'broker_idx': 1, 'entry_order_id': 'RECONCILED', 'filled': True,
                               'entry_price': 170.3, 'filled_qty': 2600}]}

    algo._square_off('SOMETHING', s)   # must not raise

    assert svc.placed == []
    assert s['phase'] == 'in_position'
