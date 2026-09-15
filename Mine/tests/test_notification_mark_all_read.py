"""The bell's "Mark all read" — one call clears the badge, nothing else moves.

Runs against a throwaway sqlite file, never the real oi_data.db. Rows are
inserted through the raw connection because the rootdir conftest stubs
`create_notification` out for every test.
"""
import pytest

import trading_app.service.notification_service as ns


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, '_DB_PATH', str(tmp_path / 'notif.db'))
    ns._init_db()
    return ns


def test_mark_all_read_clears_the_badge_and_keeps_the_rows(store):
    with store._connect() as conn:
        for i in range(3):
            conn.execute("INSERT INTO notifications (category, title, data) VALUES ('t', ?, '{}')", (f'n{i}',))
    ids = [n['id'] for n in store.list_notifications()]
    store.mark_read(ids[0])
    assert store.unread_count() == 2

    assert store.mark_all_read() == 2          # only the two still unread flip
    assert store.unread_count() == 0
    assert len(store.list_notifications()) == 3  # history is untouched
    assert all(n['is_read'] for n in store.list_notifications())


def test_mark_all_read_on_a_clean_store_is_a_no_op(store):
    assert store.mark_all_read() == 0
    assert store.unread_count() == 0
