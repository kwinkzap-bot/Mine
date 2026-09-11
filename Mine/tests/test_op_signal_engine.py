"""The state machine, driven a tick at a time.

Every broker call is patched, so what these test is the decision-making: which
leg is cancelled when, which way the stop moves, what happens when two accounts
disagree, and — the ones that matter most — what happens when something fails
in the middle.

The single most important property here is that the stop is shrunk before
anything else once a target books. Between the target filling and the stop
being resized, the stop covers more than is still held, and an SL-M that
triggers in that window sells what is not there. Several tests below exist only
to pin the ordering that keeps that window as short as it can be.
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

USER = 'test-user'
LOT = 75

ENV = {
    'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true',
    'BROKER_1_OP_ACTIVE': 'true', 'BROKER_1_NAME': 'One', 'BROKER_1_OP_SIGNAL_LOTS': '1',
    'BROKER_2_TYPE': 'zerodha', 'BROKER_2_ACTIVE': 'true',
    'BROKER_2_OP_ACTIVE': 'true', 'BROKER_2_NAME': 'Two', 'BROKER_2_OP_SIGNAL_LOTS': '1',
}

PLAN = {'symbol': 'NIFTY', 'strike': 23500, 'option_type': 'CE', 'action': 'BUY',
        'entry': 193.0, 'stop': 175.0, 'targets': [206.0, 213.0, 220.0]}


class Broker:
    """Every broker call the engine can make, recorded instead of made."""

    def __init__(self):
        self.calls = []              # ordered log: ('stop'|'target'|'modify'|…, detail)
        self.modify_ok = True
        self.stop_ok = True
        self.target_ok = True
        self.entry_fills = {}        # instance -> (status, avg_price, filled_qty)

    def log(self, kind, **detail):
        self.calls.append((kind, detail))

    def kinds(self):
        return [k for k, _ in self.calls]

    def of(self, kind):
        return [d for k, d in self.calls if k == kind]


@pytest.fixture
def broker(monkeypatch):
    b = Broker()

    def stop(**k):
        inst = next(i for i in range(1, 21) if k['gate'](i, 'zerodha'))
        b.log('stop', instance=inst, trigger=k['trigger_price'],
              lots=k['lots_for'](inst), action=k['action'])
        if not b.stop_ok:
            return [{'broker': 'zerodha', 'instance': inst, 'success': False,
                     'error': 'margin shortfall'}]
        return [{'broker': 'zerodha', 'instance': inst, 'success': True,
                 'order_id': f'sl{inst}-{len(b.calls)}',
                 'quantity': k['lots_for'](inst) * LOT}]

    def target(**k):
        inst = next(i for i in range(1, 21) if k['gate'](i, 'zerodha'))
        b.log('target', instance=inst, price=k['limit_price'],
              lots=k['lots_for'](inst), action=k['action'])
        if not b.target_ok:
            return {'success': False, 'error': 'refused', 'summary': []}
        return {'success': True, 'summary': [
            {'broker': 'zerodha', 'instance': inst, 'quantity': k['lots_for'](inst) * LOT,
             'result': {'success': True, 'order_id': f't{inst}-{len(b.calls)}'}}]}

    def _ids(legs):
        out = []
        for leg in legs or []:
            oid = leg.get('order_id') or (leg.get('result') or {}).get('order_id')
            if oid:
                out.append(str(oid))
        return out

    def modify(legs, username, session_data, price=None, quantity=None, trigger_price=None):
        b.log('modify', trigger=trigger_price, quantity=quantity, price=price,
              ids=_ids(legs))
        return ({'success': True, 'summary': []} if b.modify_ok
                else {'success': False, 'error': 'trigger price should be lower than LTP'})

    def cancel(legs, username, session_data):
        b.log('cancel', ids=_ids(legs))
        return {'success': True, 'summary': []}

    def flatten(username, session_data, select, log_tag='exit'):
        rows = select() or []
        b.log('flatten', tag=log_tag, ids=[o['id'] for o in rows])
        for o in rows:
            if o.get('status') in MineOrderStore.EDITABLE_STATUSES:
                o['status'] = 'CANCELLED'
        return {'success': True, 'cancelled_orders': len(rows), 'exited_positions': 1,
                'summary': [], 'errors': []}

    def leg_fills(order, username, session_data, books=None):
        out = {}
        for leg in (order.get('broker_order_ids') or []):
            inst = leg.get('instance')
            if inst in b.entry_fills:
                out[('zerodha', inst)] = b.entry_fills[inst]
        return out

    monkeypatch.setattr(api, 'dispatch_stop_to_brokers', stop)
    monkeypatch.setattr(api, '_dispatch_order_to_brokers', target)
    monkeypatch.setattr(api, '_modify_order_at_brokers', modify)
    monkeypatch.setattr(api, '_cancel_order_at_brokers', cancel)
    monkeypatch.setattr(api, 'exit_selected_records', flatten)
    monkeypatch.setattr(api, 'leg_fills', leg_fills)
    monkeypatch.setattr(api, '_reconcile_open_orders', lambda *a, **k: 0)
    monkeypatch.setattr(api, 'resolve_standard_lot', lambda s: LOT)
    monkeypatch.setattr(api, '_broker_positions', lambda kind, client: [])
    monkeypatch.setattr(api, '_broker_client', lambda *a, **k: (None, 'no client'))
    return b


@pytest.fixture
def rows(monkeypatch):
    data = []

    def add_order(d):
        rec = {**d, 'id': f'rec-{len(data) + 1}', 'created_at': 1000 + len(data)}
        data.append(rec)
        return rec

    def get_order(oid):
        return next((o for o in data if o['id'] == oid), {})

    def update_order(oid, updates):
        o = get_order(oid)
        if o:
            o.update(updates)

    def cancel_order(oid):
        o = get_order(oid)
        if o:
            o['status'] = 'CANCELLED'
            return True
        return False

    monkeypatch.setattr(MineOrderStore, 'add_order', staticmethod(add_order))
    monkeypatch.setattr(MineOrderStore, 'get_order', staticmethod(get_order))
    monkeypatch.setattr(MineOrderStore, 'update_order', staticmethod(update_order))
    monkeypatch.setattr(MineOrderStore, 'cancel_order', staticmethod(cancel_order))
    monkeypatch.setattr(MineOrderStore, 'get_today_orders', staticmethod(lambda: list(data)))
    return data


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                        staticmethod(lambda u, v, d='': ENV.get(v, d)))
    monkeypatch.setattr(sig_store, '_STORAGE_FILE', str(tmp_path / 'op_signals.json'))
    monkeypatch.setattr(op, '_chain_meta',
                        lambda s: {'step': 50, 'lot_size': LOT, 'expiry': None})
    monkeypatch.setattr(op, 'option_ltp', lambda *a, **k: 195.0)
    monkeypatch.setattr(engine, 'ensure_running', lambda *a, **k: False)
    # Well inside the session, so nothing trips the end-of-day sweep.
    monkeypatch.setattr(engine, '_now_mins', lambda: 11 * 60)
    return ENV


# ── a small harness ──────────────────────────────────────────────────────

def arm(brokers=(1, 2), **overrides):
    plan = {**PLAN, **overrides}
    result = engine.arm_signal(USER, {}, plan)
    assert result['success'], result
    return result['signal_id']


def sig(signal_id):
    return OpSignalStore.get(signal_id)


def slot(signal_id, instance=1):
    return sig(signal_id)['brokers'][str(instance)]


def leg(rows_, signal_id, name, instance=1):
    return next((o for o in rows_ if o.get('signal_id') == signal_id
                 and o.get('leg') == name
                 and any(l.get('instance') == instance
                         for l in (o.get('broker_order_ids') or []))), None)


def fill(rows_, signal_id, name, instance=1, qty=LOT, price=None):
    o = leg(rows_, signal_id, name, instance)
    assert o, f'no {name} leg at broker {instance}'
    o.update({'status': 'EXECUTED', 'quantity': qty,
              'entry_price': price if price is not None else o.get('price')})
    return o


def tick(ltp=None, monkeypatch=None):
    if ltp is not None:
        op.option_ltp = lambda *a, **k: ltp
    engine.tick(USER, {})


# ── the entry ────────────────────────────────────────────────────────────

def test_nothing_is_attached_while_the_entry_is_still_resting(env, rows, broker):
    sid = arm()
    broker.entry_fills = {1: ('OPEN', None, 0), 2: ('OPEN', None, 0)}
    tick()
    assert broker.of('stop') == [d for d in broker.of('stop') if d['trigger'] == 193]
    assert broker.of('target') == []
    assert slot(sid, 1)['stage'] == 'PENDING_ENTRY'


def test_a_filled_entry_attaches_the_stop_and_both_resting_targets(env, rows, broker):
    sid = arm()
    broker.entry_fills = {1: ('EXECUTED', 193.4, 3 * LOT), 2: ('OPEN', None, 0)}
    tick()

    # The stop is ONE leg's worth, not the whole position.
    stops = [d for d in broker.of('stop') if d['action'] == 'SELL']
    assert len(stops) == 1
    assert stops[0] == {'instance': 1, 'trigger': 175.0, 'lots': 1, 'action': 'SELL'}

    # Two targets rest; the third rides with the stop.
    assert [(d['price'], d['lots'], d['instance']) for d in broker.of('target')] == \
        [(206.0, 1, 1), (213.0, 1, 1)]

    s = slot(sid, 1)
    assert s['stage'] == 'LIVE' and s['entry_fill'] == 193.4
    assert s['open_qty'] == 3 * LOT
    assert s['sl_lots'] == 1 and s['alloc'] == [1, 1]


def test_resting_sell_quantity_never_exceeds_what_is_held(env, rows, broker):
    """The property the whole shape exists for.

    Three lots held, three resting sell orders of one lot each. Nothing here
    asks a broker for short-option margin, and no tick exists in which a
    triggering stop could sell a lot a target has already sold.
    """
    sid = arm()
    broker.entry_fills = {1: ('EXECUTED', 193.0, 3 * LOT)}
    tick()

    resting = sum(d['lots'] for d in broker.of('stop') if d['action'] == 'SELL')
    resting += sum(d['lots'] for d in broker.of('target'))
    assert resting == 3 == slot(sid, 1)['filled_lots']


def test_the_stop_is_placed_before_the_targets(env, rows, broker):
    """Between the fill and the stop resting the position is naked."""
    arm()
    broker.entry_fills = {1: ('EXECUTED', 193.0, 3 * LOT)}
    broker.calls.clear()                      # drop the two entry stops
    tick()
    assert [k for k in broker.kinds() if k in ('stop', 'target')] == \
        ['stop', 'target', 'target']


def test_a_partial_entry_fill_arms_a_shorter_ladder(env, rows, broker):
    """Two lots filled out of three: T1 and T2 only, and no runner for T3.

    Never a zero-lot leg, and never more sold than is held.
    """
    sid = arm()
    broker.entry_fills = {1: ('EXECUTED', 193.0, 2 * LOT)}
    tick()
    # The stop is allocated first, so a short fill loses a target rather than
    # its protection: stop 1 + T1 1 = the 2 lots actually held.
    assert slot(sid, 1)['sl_lots'] == 1
    assert slot(sid, 1)['alloc'] == [1, 0]
    assert [d['lots'] for d in broker.of('target')] == [1]
    assert [d['lots'] for d in broker.of('stop') if d['action'] == 'SELL'] == [1]


def test_a_single_lot_fill_arms_a_stop_and_nothing_else(env, rows, broker):
    """One lot cannot be both protected and targeted. Protection wins."""
    sid = arm()
    broker.entry_fills = {1: ('EXECUTED', 193.0, LOT)}
    tick()
    assert slot(sid, 1)['sl_lots'] == 1 and slot(sid, 1)['alloc'] == [0, 0]
    assert [d['lots'] for d in broker.of('stop') if d['action'] == 'SELL'] == [1]
    assert broker.of('target') == []


def test_an_entry_that_never_traded_arms_nothing(env, rows, broker):
    sid = arm()
    broker.entry_fills = {1: ('CANCELLED', None, 0)}
    tick()
    assert slot(sid, 1)['stage'] == 'NO_FILL'
    assert broker.of('target') == []


def test_the_two_accounts_are_settled_apart(env, rows, broker):
    """One fills, one does not. The one that did must not wait for the other."""
    sid = arm()
    broker.entry_fills = {1: ('EXECUTED', 193.0, 3 * LOT), 2: ('OPEN', None, 0)}
    tick()
    assert slot(sid, 1)['stage'] == 'LIVE'
    assert slot(sid, 2)['stage'] == 'PENDING_ENTRY'
    assert {d['instance'] for d in broker.of('target')} == {1}


# ── the ladder ───────────────────────────────────────────────────────────

def live(rows, broker, instances=(1,)):
    sid = arm()
    broker.entry_fills = {i: ('EXECUTED', 193.0, 3 * LOT) for i in instances}
    tick()
    broker.calls.clear()
    return sid


def test_target_one_lifts_the_stop_to_the_actual_entry_fill(env, rows, broker):
    sid = arm()
    broker.entry_fills = {1: ('EXECUTED', 193.4, 3 * LOT)}
    tick()
    broker.calls.clear()

    fill(rows, sid, 'T1', qty=LOT)
    tick()

    # 193.4, not the 193 the tip named: the stop follows what was really paid.
    # And the trigger moves ALONE — quantity is never sent, because the stop
    # was one leg's worth to begin with and still is.
    assert [d['trigger'] for d in broker.of('modify')] == [193.4]
    assert [d['quantity'] for d in broker.of('modify')] == [None]
    s = slot(sid, 1)
    assert s['stage'] == 'T1_DONE' and s['open_qty'] == 2 * LOT
    assert s['stop_level'] == 193.4
    assert leg(rows, sid, 'SL')['quantity'] == LOT      # unchanged, always


def test_target_two_lifts_the_stop_to_target_ones_own_fill(env, rows, broker):
    sid = live(rows, broker)
    fill(rows, sid, 'T1', qty=LOT)
    tick()
    broker.calls.clear()

    fill(rows, sid, 'T2', qty=LOT, price=213.25)
    tick()
    assert [d['trigger'] for d in broker.of('modify')] == [206.0]
    assert slot(sid, 1)['stage'] == 'T2_DONE'
    # One lot left — the runner — still covered by its own stop.
    assert slot(sid, 1)['open_qty'] == LOT
    assert leg(rows, sid, 'SL')['quantity'] == LOT


def test_a_target_moved_by_hand_is_the_price_the_stop_follows(env, rows, broker):
    """The engine reads leg prices back from the order records, so a price the
    user moved on the pending strip is the one the ladder goes on to use."""
    sid = live(rows, broker)
    t1 = leg(rows, sid, 'T1')
    t1['price'] = 210.0                       # moved on the strip
    fill(rows, sid, 'T1', qty=LOT, price=210.4)
    tick()
    broker.calls.clear()
    fill(rows, sid, 'T2', qty=LOT)
    tick()
    assert broker.of('modify')[0]['trigger'] == 210.4


def test_both_targets_filling_between_two_ticks_are_handled_in_one_pass(env, rows, broker):
    """A fast move fills T1 and T2 before the next tick. Treating them as
    alternatives would leave the stop a step behind the position."""
    sid = live(rows, broker)
    fill(rows, sid, 'T1', qty=LOT)
    fill(rows, sid, 'T2', qty=LOT, price=213.0)
    tick()
    assert slot(sid, 1)['stage'] == 'T2_DONE'
    assert slot(sid, 1)['open_qty'] == LOT
    # One modify, straight to the final level. The 193 step would never have
    # rested anywhere — the tick that would have placed it is the same tick
    # that already knows about T2.
    assert [d['trigger'] for d in broker.of('modify')] == [206.0]


def test_the_stop_filling_cancels_every_remaining_target_and_flattens(env, rows, broker):
    sid = live(rows, broker)
    fill(rows, sid, 'SL', qty=3 * LOT)
    tick()
    assert slot(sid, 1)['stage'] == 'FLAT'
    flat = broker.of('flatten')
    assert flat and 'SL hit' in flat[0]['tag']
    # Everything this broker placed went into the stand-down, targets included.
    assert leg(rows, sid, 'T1')['status'] == 'CANCELLED'
    assert leg(rows, sid, 'T2')['status'] == 'CANCELLED'


def test_the_stop_is_checked_before_the_targets(env, rows, broker):
    """Both filled between ticks. The stop is the terminal one: the position is
    already gone, and trailing anything would be acting on a ghost."""
    sid = live(rows, broker)
    fill(rows, sid, 'SL', qty=3 * LOT)
    fill(rows, sid, 'T1', qty=LOT)
    tick()
    assert slot(sid, 1)['stage'] == 'FLAT'
    assert broker.of('modify') == []


# ── target three, which rests nowhere ────────────────────────────────────

def test_target_three_being_touched_cancels_the_stop_and_exits(env, rows, broker):
    sid = live(rows, broker)
    fill(rows, sid, 'T1', qty=LOT)
    fill(rows, sid, 'T2', qty=LOT)
    tick()
    broker.calls.clear()

    tick(ltp=220.5)
    assert broker.kinds()[0] == 'cancel'          # the stop, first
    assert broker.of('flatten') and 'T3 hit' in broker.of('flatten')[0]['tag']
    assert slot(sid, 1)['stage'] == 'FLAT'


def test_target_three_is_not_acted_on_before_the_premium_gets_there(env, rows, broker):
    sid = live(rows, broker)
    tick(ltp=219.9)
    assert broker.of('flatten') == []
    assert slot(sid, 1)['stage'] == 'LIVE'


def test_a_gap_straight_through_every_target_still_exits(env, rows, broker):
    """The premium jumps past 220 without T1 or T2 trading. Nothing has booked,
    so the whole position leaves at market and the resting targets go with it."""
    sid = live(rows, broker)
    tick(ltp=225.0)
    assert slot(sid, 1)['stage'] == 'FLAT'
    assert leg(rows, sid, 'T1')['status'] == 'CANCELLED'


# ── when the broker says no ──────────────────────────────────────────────

def test_a_refused_trail_leaves_the_wider_stop_resting(env, rows, broker):
    """The modify is an improvement. Turning a failed improvement into a forced
    market exit would close a live trade over a rate limit."""
    sid = live(rows, broker)
    broker.modify_ok = False
    fill(rows, sid, 'T1', qty=LOT)
    tick(ltp=210.0)

    assert broker.of('flatten') == []
    assert leg(rows, sid, 'SL')['status'] == 'OPEN'
    assert leg(rows, sid, 'SL')['trigger_price'] == 175.0   # unmoved

    # stop_level is where the stop is MEANT to be, not where it is. Recording
    # the intent even though the broker refused is what makes the retry on the
    # next tick possible; forgetting it would strand the stop at 175 forever.
    assert slot(sid, 1)['stop_level'] == 193.0


def test_a_refused_trail_is_retried_on_the_next_tick(env, rows, broker):
    sid = live(rows, broker)
    broker.modify_ok = False
    fill(rows, sid, 'T1', qty=LOT)
    tick(ltp=210.0)
    broker.calls.clear()

    broker.modify_ok = True
    tick(ltp=210.0)
    assert [d['trigger'] for d in broker.of('modify')] == [193.0]
    assert slot(sid, 1)['stop_level'] == 193.0


def test_a_position_with_no_stop_resting_is_flattened_once_price_is_through_it(
        env, rows, broker):
    """The guard that does not depend on a broker answering: if the premium is
    at or through the stop level and nothing is resting there, nothing is
    protecting this position."""
    sid = live(rows, broker)
    leg(rows, sid, 'SL')['status'] = 'CANCELLED'     # cancelled by hand
    tick(ltp=174.0)
    assert slot(sid, 1)['stage'] == 'FLAT'
    assert 'no order resting' in broker.of('flatten')[0]['tag']


def test_a_position_with_a_stop_still_resting_is_left_to_the_stop(env, rows, broker):
    sid = live(rows, broker)
    tick(ltp=174.0)
    assert broker.of('flatten') == []
    assert slot(sid, 1)['stage'] == 'LIVE'


def test_a_stop_that_could_not_be_placed_is_loud_but_does_not_lose_the_position(
        env, rows, broker):
    broker.stop_ok = True
    sid = arm()
    broker.stop_ok = False
    broker.entry_fills = {1: ('EXECUTED', 193.0, 3 * LOT)}
    tick()
    s = slot(sid, 1)
    # The ladder is still armed and still being managed; the next tick's price
    # guard is what covers it until a stop lands.
    assert s['stage'] == 'LIVE' and s['open_qty'] == 3 * LOT
    assert 'SL' not in (s.get('legs') or {})


# ── end of day ───────────────────────────────────────────────────────────

def test_eod_cancels_an_entry_that_never_triggered(env, rows, broker, monkeypatch):
    sid = arm()
    broker.entry_fills = {1: ('OPEN', None, 0), 2: ('OPEN', None, 0)}
    monkeypatch.setattr(engine, '_now_mins', lambda: 15 * 60 + 15)
    tick()
    assert sig(sid)['phase'] == 'DONE'
    assert slot(sid, 1)['stage'] == 'NO_FILL'
    assert leg(rows, sid, 'ENTRY')['status'] == 'CANCELLED'


def test_eod_flattens_a_live_position(env, rows, broker, monkeypatch):
    sid = live(rows, broker)
    monkeypatch.setattr(engine, '_now_mins', lambda: 15 * 60 + 15)
    tick()
    assert sig(sid)['phase'] == 'DONE'
    assert slot(sid, 1)['stage'] == 'FLAT'
    assert 'EOD' in broker.of('flatten')[0]['tag']


def test_eod_is_scoped_to_the_signals_own_orders(env, rows, broker, monkeypatch):
    """A single-mode position on the same page is not this sweep's to close."""
    sid = live(rows, broker)
    rows.append({'id': 'hand-1', 'strategy': 'op', 'status': 'EXECUTED',
                 'symbol': 'NIFTY', 'strike': 23500, 'option_type': 'CE',
                 'action': 'BUY', 'created_at': 1})
    monkeypatch.setattr(engine, '_now_mins', lambda: 15 * 60 + 15)
    tick()
    assert 'hand-1' not in broker.of('flatten')[0]['ids']
    assert next(o for o in rows if o['id'] == 'hand-1')['status'] == 'EXECUTED'


def test_the_engine_leaves_a_finished_signal_alone(env, rows, broker):
    sid = live(rows, broker)
    broker.entry_fills[2] = ('CANCELLED', None, 0)   # the other account never filled
    fill(rows, sid, 'SL', qty=3 * LOT)
    tick()
    broker.calls.clear()
    tick()
    tick()
    assert broker.calls == []
    assert sig(sid)['phase'] == 'DONE'


def test_a_signal_stays_open_while_another_account_is_still_waiting(env, rows, broker):
    """Broker 1 is flat, broker 2's entry stop is still resting. The signal is
    not finished, and closing it here would abandon a live resting order."""
    sid = live(rows, broker)
    fill(rows, sid, 'SL', qty=3 * LOT)
    tick()
    assert slot(sid, 1)['stage'] == 'FLAT'
    assert slot(sid, 2)['stage'] == 'PENDING_ENTRY'
    assert sig(sid)['phase'] == 'ENTRY_PENDING'


# ── standing down ────────────────────────────────────────────────────────

def test_cancelling_a_signal_stands_only_that_signal_down(env, rows, broker):
    first = live(rows, broker)
    second = arm()
    engine.cancel_signal(first, USER, {})
    assert sig(first)['phase'] == 'CANCELLED'
    assert sig(second)['phase'] == 'ENTRY_PENDING'
    assert leg(rows, second, 'ENTRY')['status'] == 'OPEN'


def test_exit_all_stands_every_live_signal_down(env, rows, broker):
    live(rows, broker)
    arm()
    assert engine.stop_all_signals(USER, {}, reason='Exit all') == 2
    assert OpSignalStore.get_active() == []


# ── allocation, on its own ───────────────────────────────────────────────

@pytest.mark.parametrize('filled, per_target, want_stop, want_targets', [
    (3, 1, 1, [1, 1]),      # the whole plan: stop, T1, T2 — one lot each
    (2, 1, 1, [1, 0]),      # a lot short: the stop keeps its lot, T2 goes
    (1, 1, 1, [0, 0]),      # one lot: protected, not targeted
    (0, 1, 0, [0, 0]),
    (6, 2, 2, [2, 2]),
    (5, 2, 2, [2, 1]),
    (60, 20, 20, [20, 20]),
])
def test_the_stop_is_allocated_first_and_nothing_is_oversold(
        filled, per_target, want_stop, want_targets):
    stop_lots, targets = engine.allocate(filled, per_target)
    assert stop_lots == want_stop
    assert targets == want_targets
    # The invariant the whole ladder rests on: what is offered for sale is
    # never more than what is held.
    assert stop_lots + sum(targets) <= filled
    # And a held position is never left without a stop.
    assert (stop_lots > 0) == (filled > 0)


# ── the orphan backstop ──────────────────────────────────────────────────
# The one failure the design cannot remove: between a target filling and the
# stop being shrunk, the stop covers more than is held. Every other guard makes
# that window small. This is what notices when it was not small enough, and it
# is the reason the engine can be trusted to rest more sell quantity than it
# holds at all.

@pytest.fixture
def positions(monkeypatch):
    book = {'rows': [], 'exits': []}

    monkeypatch.setattr(api, '_broker_client', lambda *a, **k: ('kite', object()))
    monkeypatch.setattr(api, '_broker_positions', lambda kind, client: book['rows'])
    monkeypatch.setattr(api, '_position_matches',
                        lambda sym, u, strike, ot: sym == f'{u}{strike}{ot}')
    monkeypatch.setattr(api, '_place_exit_leg',
                        lambda kind, client, pos, side, qty: book['exits'].append((side, qty)))
    return book


def _force_orphan_sweep():
    engine._state['last_reconcile'] = 0.0


def test_an_account_left_short_is_closed_at_market(env, rows, broker, positions):
    sid = live(rows, broker)
    positions['rows'] = [{'symbol': 'NIFTY23500CE', 'net_qty': -LOT, 'product': 'NRML'}]
    _force_orphan_sweep()
    tick()

    # Not analysed, not attributed — closed. A BUY signal is never legitimately
    # net short the contract it is trading.
    assert positions['exits'] == [('BUY', LOT)]
    assert slot(sid, 1)['orphan_qty'] == -LOT


def test_a_healthy_long_position_is_left_alone(env, rows, broker, positions):
    live(rows, broker)
    positions['rows'] = [{'symbol': 'NIFTY23500CE', 'net_qty': 3 * LOT, 'product': 'NRML'}]
    _force_orphan_sweep()
    tick()
    assert positions['exits'] == []


def test_an_unreadable_position_book_is_never_acted_on(env, rows, broker, positions,
                                                       monkeypatch):
    """None is not an empty book. Guessing here would place a real order."""
    live(rows, broker)
    monkeypatch.setattr(api, '_broker_positions', lambda kind, client: None)
    _force_orphan_sweep()
    tick()
    assert positions['exits'] == []


def test_another_contract_at_the_same_broker_is_not_this_signals_business(
        env, rows, broker, positions):
    live(rows, broker)
    positions['rows'] = [{'symbol': 'NIFTY23600PE', 'net_qty': -LOT, 'product': 'NRML'}]
    _force_orphan_sweep()
    tick()
    assert positions['exits'] == []


def test_a_short_signal_is_flattened_when_it_goes_long(env, rows, broker, positions):
    """The mirror: a SELL tip should never be net LONG the contract."""
    sid = engine.arm_signal(USER, {}, {**PLAN, 'action': 'SELL', 'entry': 193.0,
                                       'stop': 210.0,
                                       'targets': [180.0, 170.0, 160.0]})['signal_id']
    broker.entry_fills = {1: ('EXECUTED', 193.0, 3 * LOT)}
    tick()
    positions['rows'] = [{'symbol': 'NIFTY23500CE', 'net_qty': LOT, 'product': 'NRML'}]
    _force_orphan_sweep()
    tick()
    assert positions['exits'] == [('SELL', LOT)]


def test_the_sweep_does_not_run_on_every_tick(env, rows, broker, positions):
    """It reads a position book per broker; at a 3s tick that is a lot of calls
    against an app-wide request budget shared with the chart feeds."""
    live(rows, broker)
    positions['rows'] = [{'symbol': 'NIFTY23500CE', 'net_qty': -LOT, 'product': 'NRML'}]
    _force_orphan_sweep()
    tick()
    positions['exits'].clear()
    tick()
    tick()
    assert positions['exits'] == []


# ── over the freeze limit ────────────────────────────────────────────────
# The exchange refuses a single F&O order above 27 lots and neither shared
# dispatcher splits by that, so a signal splits its own legs. The tests that
# matter are not that the split happens — it is arithmetic — but that the
# ladder afterwards still treats the chunks as one position.

@pytest.mark.parametrize('lots, want', [
    (1, [1]), (3, [3]), (27, [27]),
    (28, [27, 1]), (30, [27, 3]), (54, [27, 27]), (60, [27, 27, 6]),
    (0, []),
])
def test_a_leg_is_split_into_orders_the_exchange_will_take(lots, want):
    got = engine.lot_chunks(lots)
    assert got == want
    assert sum(got) == lots
    assert all(c <= 27 for c in got)


def big(rows_, broker, per_target=30, entry_lots=None):
    """A signal whose legs are large enough to test the freeze-limit split."""
    ENV['BROKER_1_OP_SIGNAL_LOTS'] = str(per_target)
    ENV['BROKER_2_OP_SIGNAL_LOTS'] = str(per_target)
    try:
        sid = arm()
    finally:
        ENV['BROKER_1_OP_SIGNAL_LOTS'] = '1'
        ENV['BROKER_2_OP_SIGNAL_LOTS'] = '1'
    filled = (entry_lots if entry_lots is not None else per_target * 3)
    broker.entry_fills = {1: ('EXECUTED', 193.0, filled * LOT)}
    tick()
    return sid


def test_the_stop_over_the_limit_is_placed_as_several_orders(env, rows, broker):
    """A stop is one leg's worth, so it only splits when a LEG is over the cap."""
    sid = big(rows, broker, per_target=30)
    stops = [d for d in broker.of('stop') if d['action'] == 'SELL']
    assert [d['lots'] for d in stops] == [27, 3]
    assert leg(rows, sid, 'SL')['quantity'] == 30 * LOT


def test_a_leg_under_the_limit_is_one_order_however_big_the_position(env, rows, broker):
    """90 lots held, but each leg is 30 — under the cap, so no leg splits."""
    sid = big(rows, broker, per_target=10, entry_lots=30)
    stops = [d for d in broker.of('stop') if d['action'] == 'SELL']
    assert [d['lots'] for d in stops] == [10]
    assert [(d['price'], d['lots']) for d in broker.of('target')] == \
        [(206.0, 10), (213.0, 10)]


def test_the_entry_over_the_limit_is_one_record_the_ladder_sizes_from(env, rows, broker):
    sid = big(rows, broker, per_target=10, entry_lots=30)
    entry = leg(rows, sid, 'ENTRY')
    assert len(entry['broker_order_ids']) == 2      # 27 + 3
    assert slot(sid, 1)['filled_lots'] == 30
    assert slot(sid, 1)['sl_lots'] == 10 and slot(sid, 1)['alloc'] == [10, 10]


def test_a_split_stop_moves_its_trigger_without_ever_being_resized(env, rows, broker):
    """Every chunk gets the same new trigger and keeps the size it was placed
    with. There is nothing to deal out, because the stop never covers more or
    less than the one leg it was always for."""
    sid = big(rows, broker, per_target=30)
    broker.calls.clear()

    fill(rows, sid, 'T1', qty=30 * LOT)
    tick()

    # One call, fanned out over both chunks by the shared modify helper.
    sl_ids = [l.get('order_id') or l['result']['order_id']
              for l in leg(rows, sid, 'SL')['broker_order_ids']]
    assert len(sl_ids) == 2
    moves = broker.of('modify')
    assert len(moves) == 1
    assert moves[0]['trigger'] == 193.0
    assert moves[0]['ids'] == sl_ids
    assert moves[0]['quantity'] is None      # size is never sent
    assert broker.of('cancel') == []         # no chunk is ever surplus
    assert leg(rows, sid, 'SL')['quantity'] == 30 * LOT


def test_a_refused_trail_on_a_split_stop_leaves_every_chunk_alone(env, rows, broker):
    sid = big(rows, broker, per_target=30)
    broker.modify_ok = False
    fill(rows, sid, 'T1', qty=30 * LOT)
    tick(ltp=210.0)

    assert broker.of('flatten') == []
    assert leg(rows, sid, 'SL')['status'] == 'OPEN'
    assert leg(rows, sid, 'SL')['trigger_price'] == 175.0   # unmoved
    assert slot(sid, 1)['stop_level'] == 193.0              # intent recorded




# ── the stop is one leg, so firing it is only the start of the exit ──────

def test_the_stop_firing_cancels_the_targets_and_sells_what_is_left(env, rows, broker):
    """The stop sells its own lot. The other two are the engine's job.

    This is the trade-off the shape buys: resting sells never exceed the
    position, and in return a stop hit is one exchange-side exit plus a market
    exit for the remainder rather than one exchange-side exit for all of it.
    """
    sid = live(rows, broker)
    assert leg(rows, sid, 'SL')['quantity'] == LOT      # one lot, of three held

    fill(rows, sid, 'SL', qty=LOT)
    tick()

    # Both targets pulled, and the two lots the stop did not cover squared off.
    assert leg(rows, sid, 'T1')['status'] == 'CANCELLED'
    assert leg(rows, sid, 'T2')['status'] == 'CANCELLED'
    flat = broker.of('flatten')
    assert flat and 'SL hit' in flat[0]['tag']
    assert slot(sid, 1)['stage'] == 'FLAT' and slot(sid, 1)['open_qty'] == 0


def test_a_stop_hit_after_a_target_still_takes_the_rest_out(env, rows, broker):
    sid = live(rows, broker)
    fill(rows, sid, 'T1', qty=LOT)
    tick()
    broker.calls.clear()

    fill(rows, sid, 'SL', qty=LOT)
    tick()
    assert leg(rows, sid, 'T2')['status'] == 'CANCELLED'
    assert broker.of('flatten') and slot(sid, 1)['stage'] == 'FLAT'
