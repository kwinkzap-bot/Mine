"""The env file under concurrent writes.

`env/<user>.env` holds the broker credentials and every per-account flag,
and it is read without a lock by the algo threads, the Telegram listener and
every request. It is rewritten in full by each broker login and by the Algo
tabs' toggles.

A reader that caught that rewrite mid-flight used to parse the *prefix* and
cache it as the whole file — so every flag below the cut read as unset for
the life of the process, until some unrelated save cleared the cache. That
is what skipped four live Telegram calls with "no broker has
BROKER_N_TG_ACTIVE=true" on 2026-09-21 and 09-22: the prefix reached
BROKER_1_TYPE but stopped short of BROKER_1_TG_ACTIVE.

So: writes replace the file in one step, and a read that cannot account for
the whole file is never cached.
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.app.utils.user_env import UserEnvManager, _read_env_file  # noqa: E402

USER = 'envtest'

FULL = """# a comment
BROKER_1_TYPE=zerodha
BROKER_1_ACTIVE=true
BROKER_1_TG_ACTIVE=true
BROKER_1_TG_LOTS=2
TG_CALLS_ACTIVE=true
"""
# What a reader sees when it arrives after open(f,'w') has truncated the
# file and before the tail is written: a real prefix, ending mid-line.
TORN = "# a comment\nBROKER_1_TYPE=zerodha\nBROKER_1_ACT"


@pytest.fixture
def env_file(monkeypatch, tmp_path):
    path = tmp_path / f'{USER}.env'
    path.write_text(FULL)
    monkeypatch.setattr(UserEnvManager, 'get_user_env_file',
                        staticmethod(lambda u: str(path)))
    UserEnvManager.clear_cache()
    yield path
    UserEnvManager.clear_cache()


def test_a_whole_file_reads_and_caches(env_file):
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_ACTIVE') == 'true'
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_LOTS') == '2'
    assert UserEnvManager.get_user_var(USER, 'MISSING', 'fallback') == 'fallback'


def test_a_torn_read_is_retried_rather_than_cached(env_file, monkeypatch):
    """The read that catches the writer mid-truncate must not become the
    cached truth — a flag below the cut would read as unset for good. The
    size check spots it and the retry gets the settled file."""
    real_getsize = os.path.getsize
    attempts = {'n': 0}

    def flaky_getsize(path):
        attempts['n'] += 1
        # The first attempt's "before" size does not match what it reads:
        # exactly what a reader sees while the file is being replaced.
        return real_getsize(path) + 10 if attempts['n'] == 1 else real_getsize(path)

    monkeypatch.setattr('trading_app.app.utils.user_env.os.path.getsize', flaky_getsize)
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_ACTIVE') == 'true'
    assert attempts['n'] > 1                      # it really did retry
    # and the cache holds the whole file, not the prefix
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_LOTS') == '2'


def test_a_file_that_never_settles_returns_the_default_and_caches_nothing(env_file, monkeypatch):
    """Three torn reads in a row: answer with the default, but leave the
    cache empty so the next read tries the file again."""
    real_getsize = os.path.getsize
    torn = {'on': True}
    monkeypatch.setattr('trading_app.app.utils.user_env.os.path.getsize',
                        lambda path: real_getsize(path) + (10 if torn['on'] else 0))
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_ACTIVE', 'x') == 'x'
    assert UserEnvManager._user_env_cache.get(USER) in (None, {})
    # Nothing was cached, so the read that follows the writer settling down
    # gets the real file rather than a remembered lie.
    torn['on'] = False
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_ACTIVE') == 'true'


def test_read_env_file_reports_a_torn_file_rather_than_a_prefix(tmp_path, monkeypatch):
    path = tmp_path / 'x.env'
    path.write_text(FULL)
    real_getsize = os.path.getsize
    monkeypatch.setattr('trading_app.app.utils.user_env.os.path.getsize',
                        lambda p: real_getsize(p) + 50)
    assert _read_env_file(str(path)) is None


def test_a_save_replaces_the_file_in_one_step(env_file):
    """No window in which a reader can see a truncated file: the new content
    arrives by rename, so a concurrent read gets the old file or the new."""
    seen = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            text = env_file.read_text()
            # Every observation is a whole file, never a prefix.
            seen.append(text.endswith('\n') and 'TG_CALLS_ACTIVE' in text)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for i in range(30):
            UserEnvManager.save_user_var(USER, 'BROKER_1_TG_LOTS', str(i))
    finally:
        stop.set()
        t.join(timeout=5)

    assert seen and all(seen), 'a reader saw a partially written env file'
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_LOTS') == '29'
    assert not any(p.endswith('.tmp') for p in os.listdir(env_file.parent))


def test_a_save_drops_the_cache_so_the_next_read_is_the_new_value(env_file):
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_LOTS') == '2'
    UserEnvManager.save_user_vars(USER, {'BROKER_1_TG_LOTS': '7', 'NEW_ONE': 'yes'})
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_LOTS') == '7'
    assert UserEnvManager.get_user_var(USER, 'NEW_ONE') == 'yes'
    assert UserEnvManager.get_user_var(USER, 'BROKER_1_TG_ACTIVE') == 'true'
