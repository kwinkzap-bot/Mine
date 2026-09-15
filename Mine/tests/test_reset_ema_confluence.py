"""scripts/reset_ema_confluence.py — the freshly-deployed reset.

Two things worth pinning: the guard (a state file the monitor thread wrote
seconds ago means the thread is alive and would overwrite the reset), and the
shape it leaves behind (exactly `_fresh_state()`, empty histories).
File paths are redirected into tmp_path — the real state is never touched.
"""
import importlib.util
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.algo.ema_confluence.ema_confluence_algo as eca

_SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'reset_ema_confluence.py')
_spec = importlib.util.spec_from_file_location('reset_ema_confluence', _SCRIPT)
reset = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reset)


@pytest.fixture
def files(tmp_path, monkeypatch):
    state, hist, allhist = (tmp_path / n for n in
                            ('state.json', 'hist.json', 'all.json'))
    state.write_text(json.dumps({'last_scan_date': '2026-09-15', 'stocks': {
        'SBIN': {'phase': 'in_position', 'entry_price': 800},
        'LT':   {'phase': 'watching', 'trigger_level': 3600},
        'TCS':  {'phase': 'no_setup'},
    }}))
    hist.write_text(json.dumps([{'symbol': 'SBIN'}]))
    allhist.write_text(json.dumps([{'symbol': 'SBIN'}, {'symbol': 'LT'}]))
    monkeypatch.setattr(eca, 'STATE_FILE', str(state))
    monkeypatch.setattr(eca, 'HISTORY_FILE', str(hist))
    monkeypatch.setattr(eca, 'ALL_HISTORY_FILE', str(allhist))
    monkeypatch.setattr(sys, 'argv', ['reset_ema_confluence.py'])
    return state, hist, allhist


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_refuses_while_the_thread_is_writing(files, capsys):
    state, hist, _ = files
    _age(state, 5)                       # written a moment ago → thread alive
    assert reset.main() == 2
    assert 'REFUSED' in capsys.readouterr().out
    assert json.loads(hist.read_text()) == [{'symbol': 'SBIN'}]   # nothing touched


def test_resets_to_fresh_state_once_the_thread_is_quiet(files, capsys):
    state, hist, allhist = files
    _age(state, 120)
    assert reset.main() == 0
    fresh = eca.EmaConfluenceAlgo.__new__(eca.EmaConfluenceAlgo)._fresh_state()
    assert json.loads(state.read_text()) == fresh
    assert json.loads(hist.read_text()) == []
    assert json.loads(allhist.read_text()) == []
    out = capsys.readouterr().out
    assert 'dropped 1 open paper position(s) (SBIN), 1 armed setup(s), 1 today / 2 all-time' in out


def test_force_overrides_the_guard(files, monkeypatch):
    state, hist, _ = files
    _age(state, 5)
    monkeypatch.setattr(sys, 'argv', ['reset_ema_confluence.py', '--force'])
    assert reset.main() == 0
    assert json.loads(hist.read_text()) == []
