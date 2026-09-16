"""The listener's decision layer, without Telethon.

``handle_text`` is what the event handler calls with each message; these pin
which messages become calls and which are dropped — edits, stale replays,
duplicates on a reconnect, and the channel's ordinary chatter.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.app.order_placement import tg_call_engine as engine  # noqa: E402
from trading_app.app.order_placement import tg_call_store as tg_store  # noqa: E402
from trading_app.app.order_placement import tg_calls_listener as listener  # noqa: E402

USER = 'test-user'
CHAT = -1003942647299
CALL = "NIFTY 23150 CE (15-SEP-2026)\nBUY : 81\nSL : 65\nTarget  :95,101,110\n\nFor Stock Market Risks..."
NOW = datetime(2026, 9, 15, 9, 38, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch, tmp_path):
    cfg = {'TG_CALLS_MAX_AGE_SECS': '90', 'TG_API_ID': '12345', 'TG_API_HASH': 'abc',
           'TG_CALLS_CHANNEL_ID': '-1003942647299'}
    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                        staticmethod(lambda u, v, d='': cfg.get(v, d)))
    monkeypatch.setattr(tg_store, '_STORAGE_FILE', str(tmp_path / 'tg_calls.json'))
    taken = []
    monkeypatch.setattr(engine, 'take_call', lambda u, plan, meta: taken.append((plan, meta)))
    return taken


def handle(msg_id=1, text=CALL, age=5, edited=False, spawn=False):
    return listener.handle_text(USER, CHAT, msg_id, text,
                                msg_date=NOW - timedelta(seconds=age),
                                edit_date=NOW if edited else None, now=NOW, spawn=spawn)


def test_a_fresh_call_is_a_call(env):
    assert handle() == 'call'


def test_an_edited_message_is_never_a_call(env):
    assert handle(edited=True) == 'edited'
    assert handle(msg_id=1) == 'call'          # the id was not consumed by the edit


def test_a_stale_message_is_dropped(env):
    assert handle(age=120) == 'stale'
    assert handle(age=89) == 'call'


def test_the_same_message_twice_is_a_duplicate(env):
    assert handle(msg_id=7) == 'call'
    assert handle(msg_id=7) == 'duplicate'      # a reconnect's catch-up


def test_the_ledger_survives_a_restart(env):
    assert handle(msg_id=7) == 'call'
    assert listener.handle_text(USER, CHAT, 7, CALL, now=NOW, spawn=False) == 'duplicate'


@pytest.mark.parametrize('chatter', [
    'at 81 now you can exit at cost',
    'the ce too hit 114 all 3 target done',
    'Hit 111',
    'Updated Chart',
    '',
])
def test_chatter_is_not_a_call(env, chatter):
    assert handle(msg_id=hash(chatter) % 10000 + 1, text=chatter) in ('not a call', 'no text')


def test_a_call_reaches_the_engine_on_a_thread_with_its_provenance(env, monkeypatch):
    started = []

    class T:                                    # runs the worker inline
        def __init__(self, target, args, name, daemon):
            started.append(name)
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(listener.threading, 'Thread', T)
    assert handle(msg_id=9, spawn=True) == 'call'
    (plan, meta), = env
    assert plan['strike'] == 23150 and plan['entry'] == 81.0 and plan['targets'] == [95.0, 101.0, 110.0]
    assert meta['message_id'] == 9 and meta['chat_id'] == CHAT
    assert 'BUY : 81' in meta['text']
    assert started == ['TgCall-9']


def test_the_channel_id_is_read_the_way_the_web_app_shows_it(env):
    (api_id, api_hash, channel), err = listener.credentials(USER)
    assert err is None
    assert (api_id, api_hash, channel) == (12345, 'abc', 3942647299)


def test_a_missing_credential_is_named(monkeypatch):
    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                        staticmethod(lambda u, v, d='': {'TG_API_ID': '1'}.get(v, d)))
    assert listener.credentials(USER) == (None, 'TG_API_HASH is not set')


def test_the_listener_does_not_start_outside_the_window(env, monkeypatch):
    monkeypatch.setattr(listener, '_in_window', lambda: False)
    assert listener.ensure_running(USER, 'test') is False
    assert listener.is_running() is False


# ── edits and deletions follow a call that is already in ─────────────────

@pytest.fixture
def followups(monkeypatch):
    calls = {'amend': [], 'retract': []}
    monkeypatch.setattr(engine, 'amend_call',
                        lambda u, cid, plan, meta=None: calls['amend'].append((cid, plan, meta)))
    monkeypatch.setattr(engine, 'retract_call',
                        lambda u, cid, reason='': calls['retract'].append((cid, reason)))
    monkeypatch.setattr(listener, '_FOLLOW_UP_WAIT_SECS', 0)
    return calls


def _taken_call(chat=CHAT, msg_id=5):
    from trading_app.app.order_placement.tg_call_store import TgCallStore
    return TgCallStore.create({'chat_id': chat, 'message_id': msg_id, 'phase': 'ENTRY_PENDING',
                               'brokers': {}})


def test_an_edit_to_a_taken_call_amends_it(env, followups):
    c = _taken_call()
    text = CALL.replace('SL : 65', 'SL : 70')
    assert listener.handle_edited(USER, CHAT, 5, text, spawn=False) == 'edit handled'
    (cid, plan, meta), = followups['amend']
    assert cid == c['id'] and plan['stop'] == 70.0 and meta['message_id'] == 5


def test_an_edit_to_a_message_that_was_never_a_call_is_ignored(env, followups):
    _taken_call(msg_id=5)
    listener.handle_edited(USER, CHAT, 6, CALL, spawn=False)      # a different message
    assert followups['amend'] == []


def test_an_edit_that_no_longer_parses_still_reaches_the_engine_to_say_so(env, followups):
    c = _taken_call()
    listener.handle_edited(USER, CHAT, 5, 'exit at cost', spawn=False)
    (cid, plan, _), = followups['amend']
    assert cid == c['id'] and 'error' in plan


def test_deleting_a_taken_call_withdraws_it(env, followups):
    c = _taken_call()
    _taken_call(msg_id=6)
    listener.handle_deleted(USER, CHAT, [5, 99], spawn=False)
    assert followups['retract'] == [(c['id'], 'message deleted')]


def test_a_deletion_without_a_chat_matches_on_the_message_id(env, followups):
    c = _taken_call()
    listener.handle_deleted(USER, None, [5], spawn=False)
    assert followups['retract'] == [(c['id'], 'message deleted')]


def test_a_follow_up_waits_for_a_call_still_being_placed(env, followups, monkeypatch):
    """The channel edits seconds after posting; the entry may still be on
    its way to the broker. The follow-up waits for the call to appear."""
    monkeypatch.setattr(listener, '_FOLLOW_UP_WAIT_SECS', 5)
    ticks = {'n': 0}

    def fake_sleep(_):
        ticks['n'] += 1
        if ticks['n'] == 2:
            _taken_call()                                  # placement lands now
    monkeypatch.setattr(listener.time, 'sleep', fake_sleep)
    listener.handle_edited(USER, CHAT, 5, CALL, spawn=False)
    assert len(followups['amend']) == 1 and ticks['n'] == 2


# ── the read-only status route ───────────────────────────────────────────

def test_the_status_route_reports_listener_engine_and_targets(env, monkeypatch, tmp_path):
    sys.path.insert(0, os.path.dirname(__file__))
    from route_app import build_route_app
    from trading_app.app.order_placement.tg_call_store import TgCallStore
    from trading_app.app.utils.mine_order_store import MineOrderStore

    cfg = {'TG_CALLS_ACTIVE': 'true',
           'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true', 'BROKER_1_NAME': 'One',
           'BROKER_1_TG_ACTIVE': 'true', 'BROKER_1_TG_LOTS': '2'}
    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                        staticmethod(lambda u, v, d='': cfg.get(v, d)))
    monkeypatch.setattr(MineOrderStore, 'get_today_orders',
                        staticmethod(lambda: [{'id': 'r1', 'signal_id': 'tg-x', 'leg': 'ENTRY',
                                               'created_at': 1}]))
    monkeypatch.setattr(TgCallStore, 'get_today',
                        staticmethod(lambda: [{'id': 'tg-x', 'phase': 'ENTRY_PENDING',
                                               'created_at': 1, 'brokers': {}}]))

    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = USER
        res = c.get('/api/order-placement/tg-calls')

    assert res.status_code == 200
    body = res.get_json()
    assert body['active'] is True
    assert body['listener']['running'] is False
    assert body['engine_running'] is False
    assert body['targets'] == [{'instance': 1, 'type': 'zerodha', 'name': 'One', 'lots': 2}]
    assert body['calls'][0]['legs'] == [{'id': 'r1', 'signal_id': 'tg-x', 'leg': 'ENTRY',
                                        'created_at': 1}]


def test_the_history_route_totals_per_lot_and_per_account(env, monkeypatch):
    sys.path.insert(0, os.path.dirname(__file__))
    from route_app import build_route_app
    from trading_app.app.order_placement import tg_call_store as tg_store

    rows = [
        {'id': 't1', 'date': '2026-09-15', 'lots': 2, 'pnl_per_lot': 1035.0, 'pnl': 2070.0,
         'complete': True, 'booked_at': 3},
        {'id': 't2', 'date': '2026-09-15', 'lots': 1, 'pnl_per_lot': 780.0, 'pnl': 780.0,
         'complete': True, 'booked_at': 2},
        {'id': 't3', 'date': '2026-09-12', 'lots': 2, 'pnl_per_lot': -1267.5, 'pnl': -2535.0,
         'complete': True, 'booked_at': 1},
        {'id': 't4', 'date': '2026-09-12', 'lots': 1, 'pnl_per_lot': None, 'pnl': None,
         'complete': False, 'booked_at': 0},
    ]
    monkeypatch.setattr(tg_store.TgTradeHistory, 'rows', staticmethod(lambda days=0: rows))

    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = USER
        res = c.get('/api/order-placement/tg-calls/history?days=30')

    assert res.status_code == 200
    body = res.get_json()
    t = body['totals']
    assert (t['trades'], t['booked'], t['wins'], t['losses'], t['lots']) == (4, 3, 2, 1, 6)
    assert t['pnl'] == 315.0 and t['pnl_per_lot'] == 547.5
    assert t['avg_per_lot'] == round(547.5 / 3, 2)
    assert t['incomplete'] == 1
    assert [d['date'] for d in body['by_day']] == ['2026-09-15', '2026-09-12']
    assert body['by_day'][0] == {'date': '2026-09-15', 'trades': 2, 'pnl': 2850.0,
                                 'pnl_per_lot': 1815.0}
