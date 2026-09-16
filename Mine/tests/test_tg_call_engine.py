"""The Telegram-call state machine, driven a tick at a time.

Every broker call is patched, so what these test is the decision-making:
which brokers a call reaches and at what size, what is refused before a
broker is touched, and — the ones that matter most — that a filled entry is
covered by a stop for exactly what filled, and that touching the target
cancels that stop before anything is sold.

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


def slot(call_id, instance=1):
    return call(call_id)['brokers'][str(instance)]


def leg(rows_, call_id, name, instance=1):
    return next((o for o in rows_ if o.get('signal_id') == call_id and o.get('leg') == name
                 and any(l.get('instance') == instance for l in (o.get('broker_order_ids') or []))),
                None)


def tick(ltp=None):
    if ltp is not None:
        op.option_ltp = lambda *a, **k: ltp
    engine.tick(USER, {})


def taken(broker, rows_):
    """A call armed and filled at broker 1: the state every LIVE test starts from."""
    cid = take()['call_id']
    broker.entry_fills[1] = ('EXECUTED', 81.5, 2 * LOT)
    tick()
    assert slot(cid)['stage'] == 'LIVE'
    return cid


# ── taking the call ──────────────────────────────────────────────────────

def test_the_entry_is_a_stop_limit_at_every_tg_slot_and_nowhere_else(broker, rows, env):
    result = take()
    assert result['success'], result

    stops = broker.of('stop')
    assert [s['instance'] for s in stops] == [1]         # not the OP-only slot 2
    assert stops[0] == {'instance': 1, 'trigger': 81.0, 'limit': 81.85, 'lots': 2,
                        'action': 'BUY'}

    entry = leg(rows, result['call_id'], 'ENTRY')
    assert entry['order_type'] == 'SL'
    assert entry['trigger_price'] == 81.0 and entry['price'] == 81.85
    assert entry['strategy'] == 'op' and entry['source'] == 'telegram'
    assert entry['quantity'] == 2 * LOT
    assert slot(result['call_id'])['stage'] == 'PENDING_ENTRY'
    assert call(result['call_id'])['phase'] == 'ENTRY_PENDING'


def test_the_limit_rounds_up_to_the_tick():
    assert engine.entry_limit(81, 1) == 81.85          # 81.81 → up
    assert engine.entry_limit(100, 1) == 101.0
    assert engine.entry_limit(193, 0) == 193.0


def test_a_mis_flagged_dhan_slot_and_an_unsized_slot_are_skipped_with_one_alert_each(
        broker, rows, env, alerts):
    take()
    assert [s['instance'] for s in broker.of('stop')] == [1]
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
    assert [s['lots'] for s in broker.of('stop')] == [27, 3]
    assert leg(rows, result['call_id'], 'ENTRY')['quantity'] == 30 * LOT


# ── the fill and the stop ────────────────────────────────────────────────

def test_a_fill_is_covered_by_one_stop_for_exactly_what_filled(broker, rows, env, alerts):
    cid = take()['call_id']
    broker.entry_fills[1] = ('EXECUTED', 81.5, 2 * LOT)
    tick()

    stops = broker.of('stop')
    assert len(stops) == 2                                   # entry, then the SL
    assert stops[1] == {'instance': 1, 'trigger': 65.0, 'limit': None, 'lots': 2,
                        'action': 'SELL'}
    sl = leg(rows, cid, 'SL')
    assert sl['order_type'] == 'SL-M' and sl['source'] == 'telegram'
    s = slot(cid)
    assert s['stage'] == 'LIVE' and s['open_qty'] == 2 * LOT and s['entry_fill'] == 81.5
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
    assert sum(1 for a in alerts if 'NO STOP' in a['title']) == 1

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
    assert call(cid)['phase'] == 'DONE'
    assert alerts[-1]['category'] == 'tg_call_exit'


def test_touching_the_target_cancels_the_stop_before_selling(broker, rows, env):
    cid = taken(broker, rows)
    before = len(broker.calls)
    tick(ltp=95.0)
    kinds = broker.kinds()[before:]
    assert kinds.index('cancel') < kinds.index('flatten')
    assert slot(cid)['stage'] == 'FLAT' and slot(cid)['exit_reason'] == 'T1 hit'


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


# ── the P&L ledger ───────────────────────────────────────────────────────

def exit_leg(rows_, cid, instance=1):
    return leg(rows_, cid, None, instance) or next(
        (o for o in rows_ if o.get('signal_id') == cid and o.get('leg', '').startswith('EXIT')), None)


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
    tick(ltp=95.0)
    assert call(cid)['phase'] == 'DONE'
    assert TgCallStore.get_active() == [] and TgCallStore.get_unbooked() != []
    exit_leg(rows, cid).update({'status': 'EXECUTED', 'entry_price': 95.0})
    tick(ltp=95.0)
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
