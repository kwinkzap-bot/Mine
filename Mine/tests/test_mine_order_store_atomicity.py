"""The order book survives a save that dies halfway through.

mine_orders.json is the whole app's record of what it has placed — every
screen's listing, every reconciliation, and the exit-all scoping all read it.
It is rewritten in full on every mutation, and until this was made atomic a
crash mid-write truncated it: _load() met a parse error and answered with an
empty list, which reads as "nothing was ever placed".

That was survivable while one click meant one rewrite. The Order Placement
signal engine rewrites this file from a background thread on a timer, which is
what makes the window worth closing.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.app.utils import mine_order_store as store  # noqa: E402
from trading_app.app.utils.mine_order_store import MineOrderStore  # noqa: E402


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A real file on disk — the point of these tests is the filesystem."""
    path = tmp_path / 'mine_orders.json'
    monkeypatch.setattr(store, '_STORAGE_FILE', str(path))
    return path


def test_a_normal_save_round_trips(book):
    MineOrderStore._save([{'id': 'a', 'status': 'OPEN'}])
    assert json.loads(book.read_text()) == [{'id': 'a', 'status': 'OPEN'}]


def test_a_save_that_dies_halfway_leaves_the_previous_book_intact(book, monkeypatch):
    MineOrderStore._save([{'id': 'a', 'status': 'OPEN'},
                          {'id': 'b', 'status': 'EXECUTED'}])

    def die(*_a, **_k):
        raise OSError('disk full')

    monkeypatch.setattr(store.json, 'dump', die)
    MineOrderStore._save([{'id': 'c'}])          # swallowed, as every save is

    # Not "the file exists" — the file still parses, and still holds both of
    # the orders that were really placed.
    assert json.loads(book.read_text()) == [{'id': 'a', 'status': 'OPEN'},
                                            {'id': 'b', 'status': 'EXECUTED'}]


def test_a_failed_save_leaves_no_temp_file_behind(book, monkeypatch):
    MineOrderStore._save([{'id': 'a'}])

    def die(*_a, **_k):
        raise OSError('disk full')

    monkeypatch.setattr(store.json, 'dump', die)
    MineOrderStore._save([{'id': 'b'}])

    leftovers = [p for p in os.listdir(book.parent) if p != book.name]
    assert leftovers == []


def test_the_book_is_never_truncated_in_place(book, monkeypatch):
    """The real file is never opened for writing — only the temp one is.

    This is the property that makes the save atomic, and it is the one a
    well-meaning refactor back to open(_STORAGE_FILE,'w') would break while
    every other test here still passed.
    """
    opened = []
    real_open = store.open if hasattr(store, 'open') else open

    def spy(path, mode='r', *a, **k):
        opened.append((str(path), mode))
        return real_open(path, mode, *a, **k)

    MineOrderStore._save([{'id': 'a'}])
    monkeypatch.setitem(store.__builtins__ if isinstance(store.__builtins__, dict)
                        else vars(store.__builtins__), 'open', spy)
    MineOrderStore._save([{'id': 'b'}])

    writes = [p for p, m in opened if 'w' in m]
    assert writes, 'the save did not write anything'
    assert all(p != str(book) for p in writes), f'wrote the book in place: {writes}'
