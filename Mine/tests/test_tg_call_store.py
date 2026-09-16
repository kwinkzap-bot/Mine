"""TgCallStore: the plan file behind the Telegram-call engine, and the
message-id ledger that keeps a reconnect from firing a call twice."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.app.order_placement import tg_call_store as mod  # noqa: E402
from trading_app.app.order_placement.tg_call_store import TgCallStore  # noqa: E402


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, '_STORAGE_FILE', str(tmp_path / 'tg_calls.json'))
    return TgCallStore


def test_a_message_id_is_seen_once_and_survives_a_reload(store):
    assert store.mark_seen(-100123, 42) is True
    assert store.mark_seen(-100123, 42) is False        # the same message again
    assert store.mark_seen(-100123, 43) is True         # a different one
    assert store.mark_seen(-100999, 42) is True         # same id, another chat
    # A fresh read of the file — what a restart does — still refuses it.
    assert store._load()['seen'].keys() >= {'-100123:42', '-100123:43'}
    assert store.mark_seen(-100123, 42) is False


def test_old_entries_are_pruned_on_save(store):
    store.mark_seen(1, 1)
    data = store._load()
    stale = int(time.time() * 1000) - 4 * 24 * 60 * 60 * 1000
    data['seen']['1:0'] = stale
    data['calls'].append({'id': 'tg-old', 'created_at': stale})
    store._save(data)
    again = store._load()
    assert '1:0' not in again['seen'] and '1:1' in again['seen']
    assert again['calls'] == []


def test_update_broker_merges_one_slot_without_touching_another(store):
    c = store.create({'phase': 'ARMED', 'brokers': {}})
    store.update_broker(c['id'], 1, {'stage': 'PENDING_ENTRY', 'lots': 2})
    store.update_broker(c['id'], 2, {'stage': 'DEAD'})
    store.update_broker(c['id'], 1, {'stage': 'LIVE'})
    got = store.get(c['id'])['brokers']
    assert got['1'] == {'stage': 'LIVE', 'lots': 2}
    assert got['2'] == {'stage': 'DEAD'}


def test_a_call_finishes_when_every_slot_is_done(store):
    c = store.create({'phase': 'ENTRY_PENDING', 'brokers': {}})
    store.update_broker(c['id'], 1, {'stage': 'LIVE'})
    store.update_broker(c['id'], 2, {'stage': 'NO_FILL'})
    assert store.finish_if_all_brokers_done(c['id'])['phase'] == 'ENTRY_PENDING'
    assert store.get_active() and store.get_active()[0]['id'] == c['id']
    store.update_broker(c['id'], 1, {'stage': 'FLAT'})
    assert store.finish_if_all_brokers_done(c['id'])['phase'] == 'DONE'
    assert store.get_active() == []


def test_a_torn_file_reads_as_empty_not_as_a_crash(store, tmp_path):
    (tmp_path / 'tg_calls.json').write_text('{"calls": [')
    assert store._load() == {'calls': [], 'seen': {}}
    assert store.mark_seen(1, 1) is True
