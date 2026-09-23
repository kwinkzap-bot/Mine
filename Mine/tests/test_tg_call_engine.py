"""The Telegram-call state machine, driven a tick at a time.

Every broker call is patched, so what these test is the decision-making:
which brokers a call reaches and at what size, what is refused before a
broker is touched, and — the ones that matter most — that a filled entry is
covered by a stop for exactly what filled, and that touching the target
cancels that stop before anything is sold.

Every call places two legs per broker — T1 and T3 — so broker 1 here gets
two entries, two stops and two rows. ``slot``/``leg`` default to the T1 leg;
the T3 tests name theirs.

No create_app(), no Flask at all: take_call and tick are called directly, the
way the listener thread and the engine thread call them.
"""

import os
import sys

import time as _time_mod

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.app.routes.api as api  # noqa: E402
import trading_app.app.routes.order_placement_api as op  # noqa: E402
from trading_app.app.order_placement import tg_call_engine as engine  # noqa: E402
from trading_app.app.order_placement import tg_call_store as tg_store  # noqa: E402
from trading_app.app.order_placement.tg_call_store import TgCallStore  # noqa: E402
from trading_app.app.utils.mine_order_store import MineOrderStore  # noqa: E402

USER = 'test-user'
LOT = 75

# The env fixture pins ensure_running so take_call never starts a thread; the
# one test of the real thing takes it from here.
_REAL_ENSURE_RUNNING = engine.ensure_running
_real_time = _time_mod.time

ENV = {
    'TG_CALLS_ACTIVE': 'true', 'TG_ENTRY_LIMIT_PCT': '1',
    'TG_CALLS_NOTIFY': 'true', 'TG_CALLS_TELEGRAM': 'false',
    # 1 takes calls at 2 lots; 2 is OP-only and must never see one; 3 is a
    # Dhan slot someone flagged by mistake; 4 is flagged but has no size.
    'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true', 'BROKER_1_NAME': 'One',
    'BROKER_1_TG_ACTIVE': 'true', 'BROKER_1_TG_LOTS': '2',
    'BROKER_2_TYPE': 'zerodha', 'BROKER_2_ACTIVE': 'true', 'BROKER_2_NAME': 'Two',
    'BROKER_2_OP_ACTIVE': 'true', 'BROKER_2_OP_LOTS': '10',
    'BROKER_3_TYPE': 'dhan', 'BROKER_3_ACTIVE': 'true', 'BROKER_3_NAME': 'Three',
    'BROKER_3_TG_ACTIVE': 'true', 'BROKER_3_TG_LOTS': '1',
    'BROKER_4_TYPE': 'zerodha', 'BROKER_4_ACTIVE': 'true', 'BROKER_4_NAME': 'Four',
    'BROKER_4_TG_ACTIVE': 'true',
}

PLAN = {'symbol': 'NIFTY', 'strike': 23150, 'option_type': 'CE', 'action': 'BUY',
        'entry': 81.0, 'stop': 65.0, 'targets': [95.0, 101.0, 110.0], 'expiry': None}


class Broker:
    def __init__(self):
        self.calls = []
        self.stop_ok = True
        self.modify_ok = True
        self.entry_fills = {}

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
        b.log('stop', instance=inst, trigger=k['trigger_price'], limit=k.get('limit_price'),
              lots=k['lots_for'](inst), action=k['action'])
        if not b.stop_ok:
            return [{'broker': 'zerodha', 'instance': inst, 'success': False,
                     'error': 'margin shortfall'}]
        return [{'broker': 'zerodha', 'instance': inst, 'success': True,
                 'order_id': f'o{inst}-{len(b.calls)}', 'quantity': k['lots_for'](inst) * LOT}]

    def _ids(legs):
        return [str(l.get('order_id')) for l in legs or [] if l.get('order_id')]

    def cancel(legs, username, session_data):
        b.log('cancel', ids=_ids(legs))
        return {'success': True, 'summary': []}

    def modify(legs, username, session_data, price=None, quantity=None, trigger_price=None):
        b.log('modify', ids=_ids(legs), price=price, trigger=trigger_price, quantity=quantity)
        return ({'success': True, 'summary': []} if b.modify_ok
                else {'success': False, 'error': 'trigger price should be lower than LTP'})

    def flatten(username, session_data, select, log_tag='exit'):
        rows = select() or []
        b.log('flatten', tag=log_tag, ids=[o['id'] for o in rows])
        for o in rows:
            if o.get('status') in MineOrderStore.EDITABLE_STATUSES:
                o['status'] = 'CANCELLED'
        # Net of what the records say: bought minus sold, like the real one.
        net = sum((1 if o['action'] == 'BUY' else -1) * int(o.get('quantity') or 0)
                  for o in rows if o.get('status') == 'EXECUTED')
        exits = [{'broker': 'zerodha', 'instance': 1, 'symbol': 'NIFTY', 'strike': 23150,
                  'option_type': 'CE', 'side': 'SELL', 'quantity': net,
                  'order_ids': [f'x{len(b.calls)}']}] if net > 0 else []
        return {'success': True, 'cancelled_orders': len(rows), 'exited_positions': len(exits),
                'summary': [], 'errors': [], 'exits': exits}

    def leg_fills(order, username, session_data, books=None):
        out = {}
        for leg in (order.get('broker_order_ids') or []):
            inst = leg.get('instance')
            if inst in b.entry_fills:
                out[('zerodha', inst)] = b.entry_fills[inst]
        return out

    monkeypatch.setattr(api, 'dispatch_stop_to_brokers', stop)
    monkeypatch.setattr(api, '_dispatch_order_to_brokers',
                        lambda **k: pytest.fail('the call engine never places a LIMIT/MARKET itself'))
    monkeypatch.setattr(api, '_cancel_order_at_brokers', cancel)
    monkeypatch.setattr(api, '_modify_order_at_brokers', modify)
    monkeypatch.setattr(api, 'exit_selected_records', flatten)
    monkeypatch.setattr(api, 'leg_fills', leg_fills)
    monkeypatch.setattr(api, '_reconcile_open_orders',
                        lambda *a, **k: b.log('sweep', force=k.get('force')) or 0)
    monkeypatch.setattr(api, 'resolve_standard_lot', lambda s: LOT)
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
def alerts(monkeypatch):
    out = []
    monkeypatch.setattr('trading_app.service.notification_service.create_notification',
                        lambda **k: out.append(k) or 1)
    engine._alerted.clear()
    return out


@pytest.fixture
def env(monkeypatch, tmp_path, alerts):
    cfg = dict(ENV)
    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                        staticmethod(lambda u, v, d='': cfg.get(v, d)))
    monkeypatch.setattr(tg_store, '_STORAGE_FILE', str(tmp_path / 'tg_calls.json'))
    monkeypatch.setattr(tg_store, '_HISTORY_FILE', str(tmp_path / 'tg_trades_history.json'))
    monkeypatch.setattr(op, '_chain_meta', lambda s: {'step': 50, 'lot_size': LOT, 'expiry': None})
    monkeypatch.setattr(op, 'option_ltp', lambda *a, **k: 70.0)
    monkeypatch.setattr(engine, 'ensure_running', lambda *a, **k: False)
    monkeypatch.setattr(engine, '_in_hours', lambda u: True)
    monkeypatch.setattr(engine, '_now_mins', lambda: 11 * 60)
    return cfg


# ── harness ──────────────────────────────────────────────────────────────

def take(**overrides):
    return engine.take_call(USER, {**PLAN, **overrides}, {'message_id': 1, 'chat_id': 9})


def call(call_id):
    return TgCallStore.get(call_id)


def slot(call_id, instance=1, tg_leg='T1'):
    return call(call_id)['brokers'][engine.slot_key(instance, tg_leg)]


def leg(rows_, call_id, name, instance=1, tg_leg='T1'):
    return next((o for o in rows_ if o.get('signal_id') == call_id and o.get('leg') == name
                 and (o.get('tg_leg') or 'T1') == tg_leg
                 and any(l.get('instance') == instance for l in (o.get('broker_order_ids') or []))),
                None)


def tick(ltp=None):
    if ltp is not None:
        op.option_ltp = lambda *a, **k: ltp
    engine.tick(USER, {})


def taken(broker, rows_):
    """A call armed and both legs filled at broker 1: the state every LIVE
    test starts from."""
    cid = take()['call_id']
    broker.entry_fills[1] = ('EXECUTED', 81.5, 2 * LOT)
    tick()
    assert slot(cid)['stage'] == 'LIVE' and slot(cid, tg_leg='T3')['stage'] == 'LIVE'
    return cid


# ── taking the call ──────────────────────────────────────────────────────

def test_the_entry_is_a_stop_limit_at_every_tg_slot_and_nowhere_else(broker, rows, env):
    result = take()
    assert result['success'], result

    stops = broker.of('stop')
    assert [s['instance'] for s in stops] == [1, 1]      # T1 + T3; not the OP-only slot 2
    assert stops[0] == stops[1] == {'instance': 1, 'trigger': 81.0, 'limit': 81.85, 'lots': 2,
                                    'action': 'BUY'}

    cid = result['call_id']
    entry = leg(rows, cid, 'ENTRY')
    assert entry['order_type'] == 'SL'
    assert entry['trigger_price'] == 81.0 and entry['price'] == 81.85
    assert entry['strategy'] == 'op' and entry['source'] == 'telegram'
    assert entry['quantity'] == 2 * LOT
    far = leg(rows, cid, 'ENTRY', tg_leg='T3')
    assert far and far['id'] != entry['id'] and far['quantity'] == 2 * LOT
    assert set(call(cid)['brokers']) == {'1', '1:T3'}
    assert slot(cid)['stage'] == slot(cid, tg_leg='T3')['stage'] == 'PENDING_ENTRY'
    assert slot(cid)['leg'] == 'T1' and slot(cid, tg_leg='T3')['leg'] == 'T3'
    assert call(cid)['target'] == 95.0 and call(cid)['target_far'] == 110.0
    assert call(cid)['far_label'] == 'T3'
    assert call(cid)['phase'] == 'ENTRY_PENDING'


def test_the_limit_rounds_up_to_the_tick():
    assert engine.entry_limit(81, 1) == 81.85          # 81.81 → up
    assert engine.entry_limit(100, 1) == 101.0
    assert engine.entry_limit(193, 0) == 193.0


def test_a_mis_flagged_dhan_slot_and_an_unsized_slot_are_skipped_with_one_alert_each(
        broker, rows, env, alerts):
    take()
    assert [s['instance'] for s in broker.of('stop')] == [1, 1]
    cats = [(a['category'], a['title']) for a in alerts]
    assert ('tg_call_order_failed', 'Telegram calls: Three cannot hold a stop') in cats
    assert ('tg_call_order_failed', 'Telegram calls: Four has no size') in cats

    take()                                             # the second call of the day
    assert sum(1 for a in alerts if 'Three' in a['title']) == 1
    assert sum(1 for a in alerts if 'Four' in a['title']) == 1


def test_a_premium_already_through_the_entry_is_skipped_not_chased(broker, rows, env, alerts):
    op.option_ltp = lambda *a, **k: 84.0
    result = take()
    assert result['skipped'] and 'ABOVE the market' in result['error']
    assert broker.calls == []
    assert rows == []
    assert TgCallStore.get_today() == []
    assert alerts[-1]['category'] == 'tg_call_skipped'


def test_the_master_switch_off_parses_but_never_trades(broker, rows, env, alerts):
    env['TG_CALLS_ACTIVE'] = 'false'
    result = take()
    assert result['skipped'] and 'TG_CALLS_ACTIVE' in result['error']
    assert broker.calls == [] and rows == []
    assert alerts[-1]['data']['plan']['strike'] == 23150   # the dry-run alert carries the parse


def test_outside_hours_nothing_is_placed(broker, rows, env, monkeypatch):
    monkeypatch.setattr(engine, '_in_hours', lambda u: False)
    assert take()['skipped']
    assert broker.calls == []


@pytest.mark.parametrize('bad, hint', [
    ({'action': 'SELL'}, 'Only BUY'),
    ({'stop': 90.0}, 'not below the entry'),
    ({'targets': [80.0]}, 'not above the entry'),
    ({'targets': []}, 'no target'),
    ({'symbol': 'FINNIFTY'}, 'not FINNIFTY'),
    ({'strike': 23160}, 'step by 50'),
    ({'expiry': '2026-09-23'}, 'front expiry'),
])
def test_a_call_that_cannot_be_a_trade_never_reaches_a_broker(broker, rows, env, monkeypatch,
                                                              bad, hint):
    monkeypatch.setattr(op, '_chain_meta',
                        lambda s: {'step': 50, 'lot_size': LOT, 'expiry': '2026-09-16'})
    result = take(**bad)
    assert result['skipped'] and hint in result['error'], result
    assert broker.calls == []


def test_every_broker_refusing_fails_the_call_loudly(broker, rows, env, alerts):
    broker.stop_ok = False
    result = take()
    assert not result['success']
    assert call(result['call_id'])['phase'] == 'FAILED'
    assert slot(result['call_id'])['stage'] == 'DEAD'
    assert alerts[-1]['category'] == 'tg_call_order_failed'


def test_a_leg_over_the_freeze_limit_is_split(broker, rows, env):
    env['BROKER_1_TG_LOTS'] = '30'
    result = take()
    assert [s['lots'] for s in broker.of('stop')] == [27, 3, 27, 3]      # each leg on its own
    assert leg(rows, result['call_id'], 'ENTRY')['quantity'] == 30 * LOT
    assert leg(rows, result['call_id'], 'ENTRY', tg_leg='T3')['quantity'] == 30 * LOT


# ── the second leg: T3 ───────────────────────────────────────────────────

def test_both_legs_are_the_accounts_size(broker, rows, env):
    """One BROKER_N_TG_LOTS, two legs of it — 2 lots at T1 and 2 more at T3."""
    cid = take()['call_id']
    assert [(s['lots'], s['action']) for s in broker.of('stop')] == [(2, 'BUY'), (2, 'BUY')]
    assert slot(cid)['lots'] == slot(cid, tg_leg='T3')['lots'] == 2
    assert engine.tg_targets(USER)[0] == {'instance': 1, 'type': 'zerodha', 'name': 'One', 'lots': 2}


def test_a_call_with_one_target_places_only_the_t1_leg(broker, rows, env):
    cid = take(targets=[95.0])['call_id']
    assert set(call(cid)['brokers']) == {'1'}
    assert call(cid)['target_far'] is None and call(cid)['far_label'] is None


def test_a_call_with_two_targets_rides_the_far_leg_to_t2(broker, rows, env):
    cid = take(targets=[95.0, 101.0])['call_id']
    assert set(call(cid)['brokers']) == {'1', '1:T3'}
    assert call(cid)['target_far'] == 101.0 and call(cid)['far_label'] == 'T2'
    broker.entry_fills[1] = ('EXECUTED', 81.5, 2 * LOT)
    tick()
    tick(ltp=101.0)
    assert slot(cid)['exit_reason'] == 'T1 hit'
    assert slot(cid, tg_leg='T3')['exit_reason'] == 'T2 hit'


def test_far_target():
    assert engine.far_target([95, 101, 110]) == ('T3', 110.0)
    assert engine.far_target([95, 101]) == ('T2', 101.0)
    assert engine.far_target([95]) == (None, None)
    assert engine.far_target([]) == (None, None)


def test_the_t3_leg_rides_through_t1_and_exits_at_t3(broker, rows, env):
    cid = taken(broker, rows)
    tick(ltp=95.0)                                     # T1 out, T3 still in
    assert slot(cid)['stage'] == 'FLAT' and slot(cid, tg_leg='T3')['stage'] == 'LIVE'
    before = len(broker.calls)
    tick(ltp=109.95)
    assert broker.kinds()[before:] == ['sweep']         # below T3: nothing
    tick(ltp=110.0)
    kinds = broker.kinds()[before:]
    assert kinds.index('cancel') < kinds.index('flatten')
    far = slot(cid, tg_leg='T3')
    assert far['stage'] == 'FLAT' and far['exit_reason'] == 'T3 hit'
    assert broker.of('cancel')[-1]['ids'] == [
        leg(rows, cid, 'SL', tg_leg='T3')['broker_order_ids'][0]['order_id']]
    assert call(cid)['phase'] == 'DONE'


def test_the_t3_legs_stop_firing_leaves_the_t1_leg_riding(broker, rows, env):
    cid = taken(broker, rows)
    leg(rows, cid, 'SL', tg_leg='T3')['status'] = 'EXECUTED'
    tick(ltp=80.0)
    assert slot(cid, tg_leg='T3')['exit_reason'] == 'SL hit'
    assert slot(cid)['stage'] == 'LIVE' and leg(rows, cid, 'SL')['status'] == 'OPEN'


def test_each_leg_is_booked_on_its_own_row_from_its_own_sells(broker, rows, env):
    """Both legs sell the same contract at the same account. The T1 leg's
    market exit and the T3 leg's stop fill must not land on each other's
    row."""
    cid = taken(broker, rows)                          # 2 + 2 lots in at 81.5
    tick(ltp=95.0)                                     # T1 leg exits at market
    exit_leg(rows, cid).update({'status': 'EXECUTED', 'entry_price': 95.3})
    leg(rows, cid, 'SL', tg_leg='T3').update({'status': 'EXECUTED', 'entry_price': 64.8,
                                              'quantity': 2 * LOT})
    tick(ltp=64.5)                                     # T3 leg stopped out
    rows_ = sorted(engine.history(), key=lambda r: r['leg'])
    assert [r['leg'] for r in rows_] == ['T1', 'T3']
    t1, t3 = rows_
    assert t1['exit_reason'] == 'T1 hit' and t1['exit_price'] == 95.3 and t1['target'] == 95.0
    assert t1['sold_qty'] == 2 * LOT and t1['complete'] is True
    assert t3['exit_reason'] == 'SL hit' and t3['exit_price'] == 64.8 and t3['target'] == 110.0
    assert t3['sold_qty'] == 2 * LOT and t3['complete'] is True
    assert t3['pnl_per_lot'] == round((64.8 - 81.5) * LOT, 2)
    assert TgCallStore.get_unbooked() == []


def test_editing_the_targets_moves_the_t3_level_too(broker, rows, env):
    cid = taken(broker, rows)
    r = edited(cid, targets=[95.0, 101.0, 105.0])
    assert 'T3 110.0 → 105.0' in r['changes']
    assert call(cid)['target_far'] == 105.0
    tick(ltp=105.0)
    assert slot(cid, tg_leg='T3')['exit_reason'] == 'T3 hit'


def test_a_stop_moved_by_hand_on_the_t3_leg_is_that_legs_level_only(broker, rows, env):
    cid = taken(broker, rows)
    far_sl = leg(rows, cid, 'SL', tg_leg='T3')
    far_sl.update({'price': 72.0, 'trigger_price': 72.0})
    r = engine.note_manual_edit(far_sl, 72.0)
    assert r['slots'] == ['1:T3']
    assert slot(cid, tg_leg='T3')['stop_level'] == 72.0
    assert slot(cid).get('stop_level') is None
    assert engine.slot_stop(call(cid), slot(cid)) == 65.0
    assert engine.slot_stop(call(cid), slot(cid, tg_leg='T3')) == 72.0
    before = len(broker.calls)
    tick(ltp=80.0)
    assert 'modify' not in broker.kinds()[before:]     # neither leg's stop is moved


def test_eod_flattens_both_legs(broker, rows, env, monkeypatch):
    cid = taken(broker, rows)
    monkeypatch.setattr(engine, '_now_mins', lambda: 15 * 60 + 15)
    tick(ltp=80.0)
    assert slot(cid)['exit_reason'] == slot(cid, tg_leg='T3')['exit_reason'] == 'EOD square-off'
    assert call(cid)['phase'] == 'DONE'


# ── the fill and the stop ────────────────────────────────────────────────

def test_a_fill_is_covered_by_one_stop_for_exactly_what_filled(broker, rows, env, alerts):
    cid = take()['call_id']
    broker.entry_fills[1] = ('EXECUTED', 81.5, 2 * LOT)
    tick()

    stops = broker.of('stop')
    assert len(stops) == 4                                   # two entries, then a SL each
    assert stops[2] == stops[3] == {'instance': 1, 'trigger': 65.0, 'limit': None, 'lots': 2,
                                    'action': 'SELL'}
    sl = leg(rows, cid, 'SL')
    assert sl['order_type'] == 'SL-M' and sl['source'] == 'telegram' and sl['tg_leg'] == 'T1'
    far_sl = leg(rows, cid, 'SL', tg_leg='T3')
    assert far_sl and far_sl['id'] != sl['id'] and far_sl['quantity'] == 2 * LOT
    s = slot(cid)
    assert s['stage'] == 'LIVE' and s['open_qty'] == 2 * LOT and s['entry_fill'] == 81.5
    assert s['legs'] == {'SL': sl['id']} and slot(cid, tg_leg='T3')['legs'] == {'SL': far_sl['id']}
    assert leg(rows, cid, 'ENTRY')['status'] == 'EXECUTED'
    assert any(a['category'] == 'tg_call_fill' for a in alerts)


def test_a_partial_fill_is_settled_on_what_traded(broker, rows, env, monkeypatch):
    cid = take()['call_id']
    broker.entry_fills[1] = ('OPEN', 81.2, LOT)            # 1 of 2 lots
    now = [1000.0]
    monkeypatch.setattr(engine._time, 'time', lambda: now[0])
    tick()
    assert slot(cid)['stage'] == 'PENDING_ENTRY'           # given a moment
    now[0] += 25
    tick()
    assert 'cancel' in broker.kinds()                      # the remainder
    assert broker.of('stop')[-1]['lots'] == 1              # stop for the lot held
    assert slot(cid)['open_qty'] == LOT


def test_an_entry_that_never_traded_is_no_fill(broker, rows, env):
    cid = take()['call_id']
    broker.entry_fills[1] = ('CANCELLED', None, 0)
    tick()
    assert slot(cid)['stage'] == 'NO_FILL'
    assert call(cid)['phase'] == 'DONE'


def test_a_refused_stop_is_retried_next_tick_and_alerted_once(broker, rows, env, alerts):
    cid = take()['call_id']
    broker.entry_fills[1] = ('EXECUTED', 81.5, 2 * LOT)
    broker.stop_ok = False
    tick()
    assert slot(cid)['stage'] == 'LIVE' and slot(cid)['legs'] == {}
    assert sum(1 for a in alerts if 'NO STOP' in a['title']) == 2      # one per leg, once
    tick()
    assert sum(1 for a in alerts if 'NO STOP' in a['title']) == 2

    broker.stop_ok = True
    tick(ltp=80.0)
    assert slot(cid)['legs'].get('SL')
    assert broker.of('stop')[-1] == {'instance': 1, 'trigger': 65.0, 'limit': None,
                                     'lots': 2, 'action': 'SELL'}


# ── the exits ────────────────────────────────────────────────────────────

def test_the_stop_firing_closes_the_slot(broker, rows, env, alerts):
    cid = taken(broker, rows)
    leg(rows, cid, 'SL')['status'] = 'EXECUTED'
    tick(ltp=64.0)
    assert slot(cid)['stage'] == 'FLAT' and slot(cid)['exit_reason'] == 'SL hit'
    assert alerts[-1]['category'] == 'tg_call_exit'
    # The T3 leg's own stop is still resting; the call is not over.
    assert slot(cid, tg_leg='T3')['stage'] == 'LIVE'
    assert call(cid)['phase'] == 'ENTRY_PENDING'
    leg(rows, cid, 'SL', tg_leg='T3')['status'] = 'EXECUTED'
    tick(ltp=64.0)
    assert slot(cid, tg_leg='T3')['exit_reason'] == 'SL hit'
    assert call(cid)['phase'] == 'DONE'


def test_touching_the_target_cancels_the_stop_before_selling(broker, rows, env):
    cid = taken(broker, rows)
    before = len(broker.calls)
    tick(ltp=95.0)
    kinds = broker.kinds()[before:]
    assert kinds.index('cancel') < kinds.index('flatten')
    assert slot(cid)['stage'] == 'FLAT' and slot(cid)['exit_reason'] == 'T1 hit'
    # Only the T1 leg's own records went to the exit: its entry and its
    # stop. The T3 leg, its stop and its target are untouched.
    assert broker.of('flatten')[-1]['ids'] == sorted(
        [leg(rows, cid, 'ENTRY')['id'], leg(rows, cid, 'SL')['id']], key=lambda i: int(i[4:]))
    assert broker.of('cancel')[-1]['ids'] == [leg(rows, cid, 'SL')['broker_order_ids'][0]['order_id']]
    assert slot(cid, tg_leg='T3')['stage'] == 'LIVE'
    assert leg(rows, cid, 'SL', tg_leg='T3')['status'] == 'OPEN'


def test_below_the_target_nothing_happens(broker, rows, env):
    cid = taken(broker, rows)
    before = len(broker.calls)
    tick(ltp=94.95)
    assert broker.kinds()[before:] == ['sweep']
    assert slot(cid)['stage'] == 'LIVE'


def test_a_breached_stop_with_nothing_resting_exits_at_market(broker, rows, env):
    cid = taken(broker, rows)
    leg(rows, cid, 'SL')['status'] = 'CANCELLED'           # by hand on the strip
    tick(ltp=64.0)
    assert 'flatten' in broker.kinds()
    assert slot(cid)['exit_reason'] == 'stop breached, no order resting'


def test_a_hand_cancelled_stop_is_not_re_placed(broker, rows, env):
    cid = taken(broker, rows)
    leg(rows, cid, 'SL')['status'] = 'CANCELLED'
    before = len(broker.of('stop'))
    tick(ltp=80.0)
    assert len(broker.of('stop')) == before
    assert slot(cid)['stage'] == 'LIVE'


def test_eod_cancels_a_resting_entry_and_flattens_a_live_one(broker, rows, env, monkeypatch):
    cid_live = taken(broker, rows)
    cid_wait = take()['call_id']
    monkeypatch.setattr(engine, '_now_mins', lambda: 15 * 60 + 15)
    tick(ltp=80.0)
    assert slot(cid_wait)['stage'] == 'NO_FILL'
    assert leg(rows, cid_wait, 'ENTRY')['status'] == 'CANCELLED'
    assert slot(cid_live)['stage'] == 'FLAT' and slot(cid_live)['exit_reason'] == 'EOD square-off'
    assert call(cid_live)['phase'] == 'DONE' and call(cid_wait)['phase'] == 'DONE'


def test_exit_all_stands_every_call_down(broker, rows, env):
    cid = taken(broker, rows)
    assert engine.stop_all_calls(USER, {}) == 1
    assert call(cid)['phase'] == 'CANCELLED'
    assert TgCallStore.get_active() == []
    assert slot(cid)['exit_reason'] == 'exit-all'


def test_a_call_is_not_skipped_on_a_stale_read_of_the_env(broker, rows, env, monkeypatch,
                                                           alerts):
    """2026-09-21/22: four live calls were skipped with "no broker has
    BROKER_N_TG_ACTIVE=true" because a broker login had rewritten the env
    file while a reader was parsing it, and the prefix sat in the cache. The
    file is the truth, so a call that finds no broker asks it again before
    giving up."""
    cleared = []

    def clear_cache(username=None):
        cleared.append(username)
        env.update({'BROKER_1_TG_ACTIVE': 'true', 'BROKER_1_TG_LOTS': '2'})

    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.clear_cache',
                        staticmethod(clear_cache))
    # What the poisoned cache said: the flags below the cut are simply gone.
    env.pop('BROKER_1_TG_ACTIVE'); env.pop('BROKER_1_TG_LOTS')

    result = take()
    assert result['success'], result
    assert cleared == [USER]
    assert [s['instance'] for s in broker.of('stop')] == [1, 1]


def test_a_call_with_genuinely_no_broker_is_still_skipped(broker, rows, env, monkeypatch, alerts):
    """The re-read is a second opinion, not a way to trade anyway."""
    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.clear_cache',
                        staticmethod(lambda username=None: None))
    env['BROKER_1_TG_ACTIVE'] = 'false'
    result = take()
    assert result['skipped'] and 'no broker has' in result['error']
    assert broker.calls == []


# ── moving a target by hand ──────────────────────────────────────────────
# The stop is an order, so the strip's price box moves it. A target rests
# nowhere, so this is the only way to move one.

def test_a_hand_set_target_is_what_the_leg_is_watched_against(broker, rows, env):
    cid = taken(broker, rows)
    r = engine.note_manual_target(USER, cid, '1', 90.0)
    assert r['success'] and r['was'] == 95.0 and r['target'] == 90.0
    assert slot(cid)['target_level'] == 90.0 and slot(cid)['target_source'] == 'manual'
    assert call(cid)['target'] == 95.0                  # the call's plan is untouched
    assert slot(cid, tg_leg='T3').get('target_level') is None

    tick(ltp=90.0)                                      # the new level fires
    assert slot(cid)['stage'] == 'FLAT' and slot(cid)['exit_reason'] == 'T1 hit'
    assert slot(cid, tg_leg='T3')['stage'] == 'LIVE'    # the other leg is untouched


def test_the_t3_legs_target_moves_on_its_own(broker, rows, env):
    cid = taken(broker, rows)
    assert engine.note_manual_target(USER, cid, '1:T3', 105.0)['success']
    assert engine.slot_target(call(cid), slot(cid, tg_leg='T3')) == 105.0
    assert engine.slot_target(call(cid), slot(cid)) == 95.0
    tick(ltp=105.0)
    assert slot(cid, tg_leg='T3')['exit_reason'] == 'T3 hit'


def test_a_target_the_market_is_already_through_is_refused(broker, rows, env):
    cid = taken(broker, rows)
    op.option_ltp = lambda *a, **k: 92.0
    r = engine.note_manual_target(USER, cid, '1', 91.0)
    assert not r['success'] and 'already at 92.0' in r['error']
    assert slot(cid).get('target_level') is None       # nothing written


@pytest.mark.parametrize('level, hint', [
    (0, 'above zero'),
    (-5, 'above zero'),
    (60.0, 'not above the entry'),
    (81.0, 'not above the entry'),
    ('abc', 'must be a number'),
])
def test_a_target_that_is_not_a_target_is_refused(broker, rows, env, level, hint):
    cid = taken(broker, rows)
    r = engine.note_manual_target(USER, cid, '1', level)
    assert not r['success'] and hint in r['error'], r


def test_a_target_below_a_trailed_stop_is_refused(broker, rows, env):
    """Once the stop is trailed above the entry, it is the stop — not the
    entry — that a target has to clear."""
    cid = taken(broker, rows)
    engine.note_manual_edit(sl_row(rows, cid), 88.0)     # trailed into profit
    r = engine.note_manual_target(USER, cid, '1', 86.0)
    assert not r['success'] and 'at or below the stop 88.0' in r['error']
    assert engine.note_manual_target(USER, cid, '1', 92.0)['success']


def test_a_target_cannot_be_moved_on_a_leg_that_is_out(broker, rows, env):
    cid = taken(broker, rows)
    tick(ltp=95.0)                                      # the T1 leg is out
    r = engine.note_manual_target(USER, cid, '1', 120.0)
    assert not r['success'] and 'already out' in r['error']
    assert engine.note_manual_target(USER, 'tg-nope', '1', 120.0)['error'] == 'No such call'
    assert not engine.note_manual_target(USER, cid, '9', 120.0)['success']


def test_a_channel_target_edit_clears_a_hand_set_one(broker, rows, env):
    cid = taken(broker, rows)
    engine.note_manual_target(USER, cid, '1', 90.0)
    r = edited(cid, targets=[97.0, 101.0, 110.0])
    assert slot(cid)['target_level'] is None and slot(cid)['target_source'] == 'channel'
    assert engine.slot_target(call(cid), slot(cid)) == 97.0
    assert any('hand-set target cleared' in c for c in r['changes'])


def test_a_hand_set_target_survives_an_unrelated_edit(broker, rows, env):
    cid = taken(broker, rows)
    engine.note_manual_target(USER, cid, '1', 90.0)
    edited(cid, stop=70.0)                              # the stop moved, not the targets
    assert slot(cid)['target_level'] == 90.0


# ── Exit all ─────────────────────────────────────────────────────────────

def _exit_result(instance=1, qty=2 * LOT, errors=None):
    """What route_scoped_exit hands back: what it sold, and what it could not."""
    return {'success': not errors, 'cancelled_orders': 2, 'exited_positions': 1,
            'summary': [{'broker': 'zerodha', 'instance': instance,
                         'cancelled_orders': 2, 'exited_positions': 1,
                         'errors': errors or []}],
            'errors': errors or [],
            'exits': [{'broker': 'zerodha', 'instance': instance, 'symbol': 'NIFTY',
                       'strike': 23150, 'option_type': 'CE', 'side': 'SELL',
                       'quantity': qty, 'order_ids': ['x-exit']}]}


def test_exit_all_books_each_leg_on_what_the_page_actually_sold(broker, rows, env):
    """The page sells the net of both legs in one order. Without a record of
    it the fill can never be read back and both legs book at None."""
    cid = taken(broker, rows)                           # 2 + 2 lots in at 81.5
    assert engine.stop_all_calls(USER, {}, reason='Exit all',
                                 exit_result=_exit_result(qty=4 * LOT)) == 1
    t1 = exit_leg(rows, cid)
    t3 = exit_leg(rows, cid, tg_leg='T3')
    assert t1 and t3 and t1['id'] != t3['id']
    assert t1['quantity'] == t3['quantity'] == 2 * LOT  # the net split across the legs
    assert t1['leg'] == 'EXIT' and t1['source'] == 'telegram'

    for r in (t1, t3):
        r.update({'status': 'EXECUTED', 'entry_price': 88.0})
    tick(ltp=88.0)
    booked = engine.history()
    assert len(booked) == 2
    assert all(r['complete'] is True and r['exit_price'] == 88.0 for r in booked)
    assert all(r['pnl_per_lot'] == round(6.5 * LOT, 2) for r in booked)
    assert TgCallStore.get_unbooked() == []


def test_a_leg_the_broker_would_not_square_off_stays_live_and_managed(broker, rows, env, alerts):
    """2026-09-21: an account was short of margin, its exit failed, and the
    leg was marked FLAT anyway — a real position with its stop cancelled and
    nothing watching it."""
    cid = taken(broker, rows)
    stopped = engine.stop_all_calls(USER, {}, reason='Exit all',
                                    exit_result=_exit_result(errors=['margin shortfall']))
    assert stopped == 0                                 # the call is not finished
    assert slot(cid)['stage'] == 'LIVE' and slot(cid, tg_leg='T3')['stage'] == 'LIVE'
    assert call(cid)['phase'] == 'ENTRY_PENDING'
    assert any('still held' in a['title'] for a in alerts)

    # The page cancelled the stops on its way out; the tick puts them back
    # and keeps watching the target.
    for tg_leg in ('T1', 'T3'):
        leg(rows, cid, 'SL', tg_leg=tg_leg)['status'] = 'CANCELLED'
        slot_ = slot(cid, tg_leg=tg_leg)
        TgCallStore.update_broker(cid, engine.slot_key(1, tg_leg), {'legs': {}})
    tick(ltp=80.0)
    assert broker.of('stop')[-1]['trigger'] == 65.0
    tick(ltp=95.0)
    assert slot(cid)['exit_reason'] == 'T1 hit'


def test_exit_all_with_a_clean_result_stands_every_call_down(broker, rows, env):
    cid = taken(broker, rows)
    assert engine.stop_all_calls(USER, {}, reason='Exit all',
                                 exit_result=_exit_result(qty=4 * LOT)) == 1
    assert call(cid)['phase'] == 'CANCELLED'
    assert TgCallStore.get_active() == []


# ── the message was deleted ──────────────────────────────────────────────

def test_deleting_the_message_cancels_a_resting_entry(broker, rows, env, alerts):
    cid = take()['call_id']
    r = engine.retract_call(USER, cid, reason='message deleted')
    assert r == {'success': True, 'call_id': cid, 'cancelled': 2, 'flattened': 0}
    assert 'cancel' in broker.kinds() and 'flatten' not in broker.kinds()
    assert leg(rows, cid, 'ENTRY')['status'] == 'CANCELLED'
    assert leg(rows, cid, 'ENTRY', tg_leg='T3')['status'] == 'CANCELLED'
    assert slot(cid)['stage'] == 'NO_FILL' and slot(cid)['exit_reason'] == 'message deleted'
    assert call(cid)['phase'] == 'CANCELLED'
    assert any(a['title'].startswith('Telegram call withdrawn') for a in alerts)
    assert engine.history() == []                     # nothing traded, nothing booked


def test_deleting_the_message_squares_off_a_held_position_and_books_it(broker, rows, env):
    cid = taken(broker, rows)
    before = len(broker.calls)
    r = engine.retract_call(USER, cid)
    assert r['flattened'] == 2
    assert broker.kinds()[before:].count('flatten') == 2
    assert slot(cid)['stage'] == 'FLAT' and slot(cid)['exit_reason'] == 'message deleted'
    assert slot(cid, tg_leg='T3')['exit_reason'] == 'message deleted'
    assert call(cid)['phase'] == 'CANCELLED'
    exit_leg(rows, cid).update({'status': 'EXECUTED', 'entry_price': 83.0})
    exit_leg(rows, cid, tg_leg='T3').update({'status': 'EXECUTED', 'entry_price': 83.5})
    tick(ltp=83.0)
    t1, t3 = sorted(engine.history(), key=lambda r: r['leg'])
    assert t1['exit_reason'] == 'message deleted' and t1['pnl_per_lot'] == round(1.5 * LOT, 2)
    assert t3['leg'] == 'T3' and t3['pnl_per_lot'] == round(2.0 * LOT, 2)


def test_a_deleted_message_for_a_finished_call_does_nothing(broker, rows, env):
    cid = take()['call_id']
    broker.entry_fills[1] = ('CANCELLED', None, 0)
    tick()
    assert call(cid)['phase'] == 'DONE'
    before = len(broker.calls)
    assert engine.retract_call(USER, cid)['already'] == 'DONE'
    assert len(broker.calls) == before


# ── the message was edited ───────────────────────────────────────────────

def edited(cid, **over):
    plan = {**PLAN, **over, 'source_text': 'edited'}
    return engine.amend_call(USER, cid, plan, {'message_id': 1, 'chat_id': 9, 'text': 'edited'})


def test_editing_the_entry_moves_the_resting_stop_limit(broker, rows, env):
    cid = take()['call_id']
    r = edited(cid, entry=84.0)
    assert r['success'], r
    m = broker.of('modify')[-1]
    assert m['trigger'] == 84.0 and m['price'] == engine.entry_limit(84.0, 1) == 84.85
    e = leg(rows, cid, 'ENTRY')
    assert e['trigger_price'] == 84.0 and e['price'] == 84.85
    assert call(cid)['entry'] == 84.0 and call(cid)['limit'] == 84.85


def test_editing_the_entry_below_the_market_is_refused_and_the_order_left_alone(broker, rows, env):
    cid = take()['call_id']                            # LTP is 70
    r = edited(cid, entry=69.0, stop=60.0)
    assert not r['success'] and 'entry not moved' in r['problems'][0]
    assert 'modify' not in broker.kinds()
    assert call(cid)['entry'] == 81.0
    assert call(cid)['stop'] == 60.0                   # the stop edit still counts


def test_editing_the_stop_moves_the_live_sl_m(broker, rows, env):
    cid = taken(broker, rows)
    r = edited(cid, stop=70.0)
    assert r['success'], r
    m = broker.of('modify')[-1]
    assert m['trigger'] == 70.0 and m['price'] is None and m['quantity'] is None
    assert leg(rows, cid, 'SL')['trigger_price'] == 70.0
    assert call(cid)['stop'] == 70.0


def test_a_refused_stop_move_is_retried_by_the_tick_on_the_new_level(broker, rows, env):
    cid = taken(broker, rows)
    broker.modify_ok = False
    r = edited(cid, stop=70.0)
    assert not r['success'] and 'stop modify refused' in r['problems'][0]
    assert call(cid)['stop'] == 70.0                   # the plan moved anyway
    assert leg(rows, cid, 'SL')['trigger_price'] == 65.0

    broker.modify_ok = True
    tick(ltp=80.0)                                      # the tick tries again
    assert broker.of('modify')[-1]['trigger'] == 70.0
    assert leg(rows, cid, 'SL')['trigger_price'] == 70.0


def test_a_stale_stop_is_cancelled_and_the_position_sold_once_the_edited_level_is_through(
        broker, rows, env):
    cid = taken(broker, rows)
    broker.modify_ok = False
    edited(cid, stop=70.0)
    before = len(broker.calls)
    tick(ltp=69.0)                                      # through 70, old stop still at 65
    kinds = broker.kinds()[before:]
    assert 'cancel' in kinds and 'flatten' in kinds and kinds.index('cancel') < kinds.index('flatten')
    assert slot(cid)['exit_reason'] == 'edited stop breached'


def test_editing_the_target_moves_the_watched_level(broker, rows, env):
    cid = taken(broker, rows)
    edited(cid, targets=[90.0, 101.0, 110.0])
    assert call(cid)['target'] == 90.0
    tick(ltp=90.0)
    assert slot(cid)['stage'] == 'FLAT' and slot(cid)['exit_reason'] == 'T1 hit'


def test_editing_the_stop_before_the_fill_arms_the_stop_at_the_new_level(broker, rows, env):
    cid = take()['call_id']
    edited(cid, stop=70.0)
    assert 'modify' not in broker.kinds()             # nothing at the broker yet
    broker.entry_fills[1] = ('EXECUTED', 81.5, 2 * LOT)
    tick()
    assert broker.of('stop')[-1]['trigger'] == 70.0


def test_editing_to_another_contract_while_resting_replaces_the_call(broker, rows, env):
    cid = take()['call_id']
    r = edited(cid, strike=23200)
    assert call(cid)['phase'] == 'CANCELLED'
    assert leg(rows, cid, 'ENTRY')['status'] == 'CANCELLED'
    new = r['replaced_by']
    assert new and new != cid and call(new)['strike'] == 23200
    assert broker.of('stop')[-1]['trigger'] == 81.0    # the fresh entry


def test_editing_to_another_contract_after_the_fill_keeps_the_position(broker, rows, env, alerts):
    cid = taken(broker, rows)
    r = edited(cid, strike=23200, stop=70.0)
    assert 'contract changed after fill' in r['problems']
    assert call(cid)['phase'] == 'ENTRY_PENDING' and slot(cid)['stage'] == 'LIVE'
    assert call(cid)['strike'] == 23150 and call(cid)['stop'] == 70.0
    assert any('different contract' in a['title'] for a in alerts)


def test_an_edit_that_is_no_longer_a_call_leaves_the_trade_alone(broker, rows, env, alerts):
    cid = taken(broker, rows)
    before = len(broker.calls)
    r = engine.amend_call(USER, cid, {'error': 'No entry price'}, {})
    assert not r['success'] and len(broker.calls) == before
    assert call(cid)['stop'] == 65.0
    assert any('unreadable' in a['title'] for a in alerts)


# ── the P&L ledger ───────────────────────────────────────────────────────

def exit_leg(rows_, cid, instance=1, tg_leg='T1'):
    return next((o for o in rows_ if o.get('signal_id') == cid and o.get('leg', '').startswith('EXIT')
                 and (o.get('tg_leg') or 'T1') == tg_leg), None)


def test_a_target_exit_is_booked_per_lot_once_the_market_fill_is_known(broker, rows, env, monkeypatch):
    cid = taken(broker, rows)                       # 2 lots in at 81.5
    tick(ltp=95.0)
    ex = exit_leg(rows, cid)
    assert ex and ex['leg'] == 'EXIT' and ex['action'] == 'SELL' and ex['quantity'] == 2 * LOT
    assert ex['source'] == 'telegram' and ex['broker_order_ids'][0]['order_id'].startswith('x')
    assert slot(cid)['booked'] is False and engine.history() == []   # fill not read yet

    ex.update({'status': 'EXECUTED', 'entry_price': 95.3})           # the sweep reads it back
    tick(ltp=95.0)
    (row,) = engine.history()
    assert row['lots'] == 2 and row['qty'] == 2 * LOT
    assert row['entry_price'] == 81.5 and row['exit_price'] == 95.3
    assert row['points'] == 13.8
    assert row['pnl_per_lot'] == round(13.8 * LOT, 2) == 1035.0
    assert row['pnl'] == 2070.0
    assert row['exit_reason'] == 'T1 hit' and row['complete'] is True
    assert slot(cid)['booked'] is True and slot(cid)['pnl'] == 2070.0
    assert TgCallStore.get_unbooked() == []


def test_a_stop_hit_is_booked_from_the_stop_fill(broker, rows, env):
    cid = taken(broker, rows)
    leg(rows, cid, 'SL').update({'status': 'EXECUTED', 'entry_price': 64.6, 'quantity': 2 * LOT})
    tick(ltp=64.0)
    (row,) = engine.history()
    assert row['exit_reason'] == 'SL hit'
    assert row['exit_price'] == 64.6 and row['points'] == round(64.6 - 81.5, 2)
    assert row['pnl_per_lot'] == round((64.6 - 81.5) * LOT, 2)
    assert row['pnl'] == round((64.6 - 81.5) * LOT * 2, 2)
    assert exit_leg(rows, cid) is None                 # nothing else was sold


def test_a_stop_that_filled_during_the_target_exit_books_both_sells(broker, rows, env):
    cid = taken(broker, rows)
    sl = leg(rows, cid, 'SL')
    # Half the position went through the stop while the exit was on its way.
    sl.update({'status': 'EXECUTED', 'entry_price': 65.0, 'quantity': LOT})
    tick(ltp=95.0)                                   # SL hit → flatten; net still 1 lot
    ex = exit_leg(rows, cid)
    assert ex['quantity'] == LOT
    ex.update({'status': 'EXECUTED', 'entry_price': 95.0})
    tick(ltp=95.0)
    (row,) = engine.history()
    assert row['sold_qty'] == 2 * LOT and row['complete'] is True
    assert row['exit_price'] == 80.0                 # qty-weighted: (65 + 95) / 2
    assert row['pnl_per_lot'] == round((80.0 - 81.5) * LOT, 2)


def test_an_exit_fill_that_never_comes_is_booked_on_a_timer_and_flagged(broker, rows, env,
                                                                        monkeypatch, alerts):
    cid = taken(broker, rows)
    now = [_real_time()]
    monkeypatch.setattr(engine._time, 'time', lambda: now[0])
    tick(ltp=95.0)
    tick(ltp=95.0)
    assert engine.history() == []                     # waiting on the fill
    now[0] += engine._BOOK_SETTLE_SECS + 1
    tick(ltp=95.0)
    (row,) = engine.history()
    assert row['complete'] is False and row['exit_price'] is None and row['pnl'] is None
    assert any('without a full exit fill' in a['title'] for a in alerts)


def test_the_engine_keeps_running_until_the_last_slot_is_booked(broker, rows, env):
    cid = taken(broker, rows)
    tick(ltp=110.0)                                   # through T1 and T3: both legs out
    assert call(cid)['phase'] == 'DONE'
    assert TgCallStore.get_active() == [] and TgCallStore.get_unbooked() != []
    exit_leg(rows, cid).update({'status': 'EXECUTED', 'entry_price': 110.0})
    tick(ltp=110.0)
    assert TgCallStore.get_unbooked() != []           # the T3 leg's fill is still unread
    exit_leg(rows, cid, tg_leg='T3').update({'status': 'EXECUTED', 'entry_price': 110.2})
    tick(ltp=110.0)
    assert TgCallStore.get_unbooked() == []


def test_history_days_window(broker, rows, env):
    import json
    json.dump([{'id': 'old', 'pnl': 1, 'booked_at': 1_000}], open(tg_store._HISTORY_FILE, 'w'))
    tg_store.TgTradeHistory.append({'pnl': 2})
    assert [r['pnl'] for r in engine.history()] == [2, 1]        # newest first
    assert [r['pnl'] for r in engine.history(days=1)] == [2]


# ── the loop ─────────────────────────────────────────────────────────────

def test_the_status_sweep_is_forced_every_tick(broker, rows, env):
    taken(broker, rows)
    assert broker.of('sweep')[-1]['force'] is True


def test_the_engine_does_not_start_with_nothing_to_manage(broker, rows, env):
    assert TgCallStore.get_active() == []
    assert _REAL_ENSURE_RUNNING(USER, 'test') is False
    assert engine.is_running() is False


# ── speed: nothing cold on the call's path ───────────────────────────────

def test_an_alert_carrying_the_parsed_expiry_date_still_rings(broker, rows, env, alerts):
    """The plan's expiry is a date; the bell stores JSON. The first live call
    lost its bell alert to exactly this."""
    import datetime as dt
    op.option_ltp = lambda *a, **k: 90.0                # above the entry → skipped + alerted
    take(expiry=dt.date(2026, 9, 22))
    assert alerts and alerts[-1]['data']['plan']['expiry'] == '2026-09-22'


def test_every_tg_account_is_entered_at_once(broker, rows, env, monkeypatch):
    """Two accounts, two threads: the second must not wait for the first's
    broker round-trip."""
    env['BROKER_2_TG_ACTIVE'] = 'true'; env['BROKER_2_TG_LOTS'] = '1'
    names = set()
    orig = api.dispatch_stop_to_brokers

    def slow_stop(**k):
        import threading
        names.add(threading.current_thread().name)
        return orig(**k)
    monkeypatch.setattr(api, 'dispatch_stop_to_brokers', slow_stop)
    r = take()
    assert sorted((x['instance'], x['leg']) for x in r['summary']) == [
        (1, 'T1'), (1, 'T3'), (2, 'T1'), (2, 'T3')]
    assert all(x['success'] for x in r['summary'])
    assert names and all(n.startswith('TgCallEntry') for n in names)   # placed from the pool


def test_prewarm_touches_chain_lot_quote_and_the_kite_dump(broker, rows, env, monkeypatch):
    hits = []
    monkeypatch.setattr(op, '_chain_meta', lambda s: hits.append(('chain', s)) or {'step': 50, 'expiry': None})
    monkeypatch.setattr(op, '_spot', lambda s: hits.append(('spot', s)) or 23172.0)
    monkeypatch.setattr(op, 'option_ltp', lambda s, k, t: hits.append(('ltp', s, k, t)) or 80.0)
    monkeypatch.setattr(api, 'resolve_standard_lot', lambda s: hits.append(('lot', s)) or 75)

    class K:
        def __init__(self, kite_instance): pass
        def get_option_symbol(self, s, k, t): hits.append(('kite', s, k, t)); return 'X'
    monkeypatch.setattr('trading_app.service.kite_order_services.KiteService', K)
    monkeypatch.setattr(api, 'get_kite', lambda *a, **k: object())

    timings = engine.prewarm(USER, symbols=('NIFTY',))
    assert ('chain', 'NIFTY') in hits and ('lot', 'NIFTY') in hits
    assert ('ltp', 'NIFTY', 23150, 'CE') in hits            # the ATM strike, 23172 → 23150
    assert ('kite', 'NIFTY', 23000, 'CE') in hits            # slot 1 is the only Kite TG slot
    assert set(timings) == {'chain:NIFTY', 'lot:NIFTY', 'ltp:NIFTY', 'kite:1'}


def test_a_live_position_with_no_quote_is_alerted_once_and_kept(broker, rows, env, alerts):
    """SENSEX 74200PE on 2026-09-16: the quote was blank all day and the
    target watch silently never fired. Now it says so."""
    cid = taken(broker, rows)
    op.option_ltp = lambda *a, **k: None
    tick(); tick()
    noquote = [a for a in alerts if 'NO QUOTE' in a['title']]
    assert len(noquote) == 1 and 'T1' not in noquote[0]['title']
    assert slot(cid)['stage'] == 'LIVE'                 # nothing rash: the stop still rests


# ── a stop moved by hand on the strip ────────────────────────────────────

def sl_row(rows_, cid):
    return leg(rows_, cid, 'SL')


def test_a_stop_moved_by_hand_is_this_accounts_level_and_never_moved_back(broker, rows, env):
    cid = taken(broker, rows)
    sl = sl_row(rows, cid)
    # What the strip's price box does: the broker modify, then the record.
    sl.update({'price': 72.0, 'trigger_price': 72.0})
    r = engine.note_manual_edit(sl, 72.0)
    assert r == {'call_id': cid, 'stop_level': 72.0, 'instances': [1], 'slots': ['1']}
    assert slot(cid)['stop_level'] == 72.0 and slot(cid)['stop_source'] == 'manual'
    assert slot(cid, tg_leg='T3').get('stop_level') is None     # the other leg's stop is its own
    assert call(cid)['stop'] == 65.0                   # the plan is untouched

    before = len(broker.calls)
    tick(ltp=80.0); tick(ltp=80.0)
    assert 'modify' not in broker.kinds()[before:]     # not moved back to 65


def test_the_breach_guard_and_the_re_placement_use_the_hand_set_level(broker, rows, env):
    cid = taken(broker, rows)
    sl = sl_row(rows, cid)
    engine.note_manual_edit(sl, 72.0)
    sl['status'] = 'REJECTED'                          # gone in a way the user did not do
    tick(ltp=80.0)
    assert broker.of('stop')[-1]['trigger'] == 72.0    # re-placed at the hand-set level

    # The re-placed stop is a new record; cancel it by hand and breach 72.
    next(o for o in rows if o['id'] == slot(cid)['legs']['SL'])['status'] = 'CANCELLED'
    tick(ltp=71.0)                                     # through 72, nothing resting
    assert slot(cid)['exit_reason'] == 'stop breached, no order resting'


def test_a_channel_edit_of_the_stop_overrides_a_hand_set_level(broker, rows, env):
    cid = taken(broker, rows)
    engine.note_manual_edit(sl_row(rows, cid), 72.0)
    edited(cid, stop=70.0)
    assert slot(cid)['stop_level'] is None and slot(cid)['stop_source'] == 'channel'
    assert broker.of('modify')[-1]['trigger'] == 70.0
    assert engine.slot_stop(call(cid), slot(cid)) == 70.0


def test_an_entry_moved_by_hand_updates_the_plan(broker, rows, env):
    cid = take()['call_id']
    entry = leg(rows, cid, 'ENTRY')
    engine.note_manual_edit(entry, 84.0, 84.85)
    assert call(cid)['entry'] == 84.0 and call(cid)['limit'] == 84.85


def test_a_hand_edit_on_a_leg_that_is_not_a_call_is_ignored(broker, rows, env):
    assert engine.note_manual_edit({'signal_id': 'sig-old', 'leg': 'SL'}, 1.0) == {}
    assert engine.note_manual_edit({'leg': 'SL'}, 1.0) == {}
