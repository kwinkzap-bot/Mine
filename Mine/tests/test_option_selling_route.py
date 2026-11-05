"""The straddle BACKTEST route's second-by-second exit, on fakes: nothing here reaches a broker."""
import threading
import threading
import time
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from tests.route_app import build_route_app
from trading_app.Backtest import option_selling_engine as ose
from trading_app.app.routes import api as api_mod


class FakeIcici:
    """Both legs flat at 100, the CE wicking to 160 in the 10:00 bar of Mon 2025-02-03's next session."""

    def __init__(self):
        self.calls = 0

    def historical_option_range(self, root, expiry, strike, right, frm, to, *a, **k):
        out = []
        day = frm
        while day <= to:
            if day.weekday() < 5:
                for ts in pd.date_range(datetime(day.year, day.month, day.day, 9, 15),
                                        datetime(day.year, day.month, day.day, 15, 25), freq='5min'):
                    wick = right == 'CE' and ts == pd.Timestamp('2025-02-04 10:00')
                    out.append({'date': ts.to_pydatetime(), 'open': 100.0, 'low': 100.0, 'close': 100.0,
                                'high': 160.0 if wick else 100.0})
            day += timedelta(days=1)
        return out

    def last_history_error(self):
        return None

    def historical_option_seconds(self, root, expiry, strike, right, day, index, may_fetch=None):
        if may_fetch is not None and not may_fetch():
            return None
        self.calls += 1
        assert (day, index) == (date(2025, 2, 4), 3)             # 10:00 sits in the slice from 10:00
        t, c = ([137, 138], [160.0, 100.0]) if right == 'CE' else ([1], [100.0])
        return {'start': datetime(2025, 2, 4, 10, 0), 't': t, 'c': c}


@pytest.fixture
def run(monkeypatch):
    days = pd.bdate_range('2025-01-27', '2025-02-28')
    minutes = pd.DataFrame({'close': 23010.0}, index=pd.DatetimeIndex(
        [ts for d in days for ts in pd.date_range(d + pd.Timedelta('9h15min'), d + pd.Timedelta('15h29min'), freq='1min')]))
    icici = FakeIcici()
    monkeypatch.setattr(api_mod, 'check_auth', lambda: None)
    monkeypatch.setattr(api_mod, 'get_fyers_adapter', lambda *a, **k: object())
    monkeypatch.setattr(api_mod, 'get_icici_adapter', lambda *a, **k: icici)
    monkeypatch.setattr(api_mod, '_provider_tag', lambda p: 'fake')
    monkeypatch.setattr(api_mod, '_fno_lot_sizes', lambda: {'NIFTY': 65})
    monkeypatch.setattr('trading_app.Backtest.minute_candle_store.get_minute_history', lambda *a, **k: minutes)
    monkeypatch.setattr('trading_app.service.icici_symbol_master.expiries', lambda *a, **k: [])
    app = build_route_app()

    def go(**extra):
        body = {'start_date': '2025-02-03', 'end_date': '2025-02-03', 'cadence': 'weekly', 'entry_day': 'monday',
                'target_pct': 50, 'stop_pct': 25, **extra}
        with app.test_request_context(json=body):
            api_mod._cr_tasks.clear()
            resp = api_mod.run_option_selling_backtest_api.__wrapped__()
            task_id = resp.get_json()['task_id']
        for _ in range(200):
            task = api_mod._cr_tasks[task_id]
            if task['status'] != 'running':
                break
            time.sleep(0.02)
        assert task['status'] == 'complete', task.get('error')
        return task['payload'], icici
    go.app = app
    return go


def test_every_second_catches_the_wick(run):
    payload, icici = run()
    row, s = payload['trades'][0], payload['summary']
    assert row['exit_reason'] == 'Stop loss' and row['exit_time'] == '2025-02-04T10:02:17'
    assert s['exit_check'] == '1s' and s['bars_checked'] == 1 and s['trades_unchecked'] == 0
    assert s['second_requests'] == 2 and icici.calls == 2


def test_five_minute_close_asks_for_no_seconds(run):
    payload, icici = run(exit_check='5m')
    assert payload['trades'][0]['exit_reason'] == 'Expiry' and icici.calls == 0
    assert payload['summary']['second_requests'] == 0


def test_a_spent_budget_falls_back_and_says_so(run, monkeypatch):
    monkeypatch.setattr(api_mod, '_cr_second_budget', lambda: 0)
    payload, _ = run()
    assert payload['trades'][0]['exit_reason'] == 'Expiry'
    assert payload['summary']['trades_unchecked'] == 1 and payload['summary']['bars_unchecked'] == 1


def _wick_minutes(self, root, expiry, strike, right, day, may_fetch=None):
    # CE: 160 for the 10:01 minute only, back to 100 at 10:02 — the 3-minute span 10:00-10:02 closes back at 100.
    if may_fetch is not None and not may_fetch():
        return None
    self.calls += 1
    t, c = ([2760, 2820], [160.0, 100.0]) if right == 'CE' else ([2700], [100.0])
    return {'start': datetime(day.year, day.month, day.day, 9, 15), 't': t, 'c': c}


@pytest.mark.parametrize('check,reason,when', [('1m', 'Stop loss', '2025-02-04T10:01:59'),
                                                ('3m', 'Expiry', None)])
def test_minutes_fetch_minute_data_and_exit_at_the_span_close(run, monkeypatch, check, reason, when):
    monkeypatch.setattr(FakeIcici, 'historical_option_minute_closes', _wick_minutes, raising=False)
    monkeypatch.setattr(FakeIcici, 'historical_option_seconds',
                        lambda *a, **k: pytest.fail('minute checks must not fetch seconds'))
    payload, icici = run(exit_check=check)
    row = payload['trades'][0]
    assert payload['summary']['exit_check'] == check and icici.calls == 2
    assert row['exit_reason'] == reason
    if when:
        assert row['exit_time'] == when


def test_thirty_seconds_uses_the_same_second_data(run):
    payload, icici = run(exit_check='30s')
    row = payload['trades'][0]
    assert row['exit_reason'] == 'Expiry'         # 160 at 10:02:17 is out by that span's close (100)
    assert icici.calls == 2


def test_an_unknown_exit_check_is_refused(run):
    app = run.app
    with app.test_request_context(json={'start_date': '2025-02-03', 'end_date': '2025-02-03', 'exit_check': '2m'}):
        resp = api_mod.run_option_selling_backtest_api.__wrapped__()
    assert resp[1] == 400


def _optimise(run_fixture_app, **extra):
    body = {'start_date': '2025-02-03', 'end_date': '2025-02-03', 'cadence': 'weekly', 'entry_day': 'monday',
            'target_pct': 50, 'stop_pct': 25, **extra}
    with run_fixture_app.test_request_context(json=body):
        api_mod._cr_tasks.clear()
        task_id = api_mod.run_option_selling_optimise_api.__wrapped__().get_json()['task_id']
    for _ in range(3000):
        task = api_mod._cr_tasks[task_id]
        if task['status'] != 'running':
            break
        time.sleep(0.02)
    assert task['status'] == 'complete', task.get('error')
    return task['payload']


def test_find_best_params_re_checks_its_leaders(run, monkeypatch):
    # The stub's seconds assert one slice only, so let any slice be asked for here.
    monkeypatch.setattr(FakeIcici, 'historical_option_seconds',
                        lambda self, root, expiry, strike, right, day, index, may_fetch=None:
                        {'start': datetime(day.year, day.month, day.day, 9, 15), 't': [], 'c': []})
    monkeypatch.setattr(FakeIcici, 'historical_option_minute_closes',
                        lambda self, root, expiry, strike, right, day, may_fetch=None:
                        {'start': datetime(day.year, day.month, day.day, 9, 15), 't': [], 'c': []}, raising=False)
    payload = _optimise(run.app)
    shown = payload['results']
    combos = {tuple(r[k] for k in ose.SWEEP_KEYS) for r in shown}
    assert len(combos) > 20 and len(shown) == 5 * len(combos)      # every row at every Exit Check
    assert payload['rechecked'] == 4 * len(combos)
    assert all('sec_net_pnl_rupees' in r for r in shown)           # no row is left as a dash
    assert {r['exit_check'] for r in shown} == {'1s', '30s', '1m', '3m', '5m'}
    assert shown[-1]['exit_check'] == '5m' and payload['best']['exit_check'] != '5m'
    assert payload['best'] is shown[0] and shown[0]['board_order'] == len(shown)
    assert 'sec_net_pnl_rupees' in shown[0]


def test_five_minute_exit_check_skips_the_re_check(run):
    payload = _optimise(run.app, exit_check='5m')
    assert payload['rechecked'] == 0 and all('sec_complete' not in r for r in payload['results'])


def test_a_sweep_holds_its_breeze_requests_while_a_backtest_runs(monkeypatch):
    monkeypatch.setattr(api_mod, '_cr_foreground_runs', 0)
    released = threading.Event()

    def sweep():
        api_mod._cr_yield_to_foreground()
        released.set()

    api_mod._cr_foreground_change(1)
    threading.Thread(target=sweep, daemon=True).start()
    assert not released.wait(0.1)
    api_mod._cr_foreground_change(-1)
    assert released.wait(2)


def test_a_stuck_backtest_cannot_hold_a_sweep_for_ever(monkeypatch):
    monkeypatch.setattr(api_mod, '_cr_foreground_runs', 1)
    monkeypatch.setattr(api_mod, '_CR_YIELD_MAX_SECS', 0.01)
    api_mod._cr_yield_to_foreground()


def test_only_a_sweep_passes_the_yield_and_a_backtest_counts_until_it_ends(run, monkeypatch):
    seen, running = [], []
    real = FakeIcici.historical_option_range

    def spy(self, *a, **k):
        seen.append(k.get('before_request'))
        running.append(api_mod._cr_foreground_runs)
        return real(self, *a, **k)

    monkeypatch.setattr(FakeIcici, 'historical_option_range', spy)
    monkeypatch.setattr(api_mod, '_cr_foreground_runs', 0)
    run()
    assert seen and all(x is None for x in seen)        # a backtest never waits
    assert set(running) == {1}
    for _ in range(100):
        if api_mod._cr_foreground_runs == 0:
            break
        time.sleep(0.01)
    assert api_mod._cr_foreground_runs == 0


class _Gate:
    """Holds a run at its first ICICI range until the test lets it go."""

    def __init__(self):
        self.release = threading.Event()

    def __call__(self, *a, **k):
        self.release.wait(20)
        return []


def _start(app, **extra):
    body = {'start_date': '2025-02-03', 'end_date': '2025-02-03', 'cadence': 'weekly', 'entry_day': 'monday',
            'target_pct': 50, 'stop_pct': 25, **extra}
    with app.test_request_context(json=body):
        return api_mod.run_option_selling_optimise_api.__wrapped__().get_json()['task_id']


def _settled(task_id):
    for _ in range(3000):
        if api_mod._cr_tasks[task_id]['status'] != 'running':
            return api_mod._cr_tasks[task_id]['status']
        time.sleep(0.01)
    raise AssertionError('still running')


def test_the_same_sweep_re_attaches_and_another_stops_it(run, monkeypatch):
    gate = _Gate()
    monkeypatch.setattr(FakeIcici, 'historical_option_range', gate)
    api_mod._cr_tasks.clear()
    first = _start(run.app, exit_check='5m')
    assert _start(run.app, exit_check='5m') == first         # a reloaded page, not a second run
    other = _start(run.app, exit_check='5m', end_date='2025-02-04')
    assert other != first and api_mod._cr_tasks[first]['stop'] and not api_mod._cr_tasks[other]['stop']
    gate.release.set()
    assert _settled(first) == 'stopped'
    assert _settled(other) == 'complete'


def test_a_sweep_nobody_polls_stops(run, monkeypatch):
    gate = _Gate()
    monkeypatch.setattr(FakeIcici, 'historical_option_range', gate)
    monkeypatch.setattr(api_mod, '_CR_ABANDONED_SECS', 0)
    api_mod._cr_tasks.clear()
    task_id = _start(run.app, exit_check='5m')
    task = api_mod._cr_tasks[task_id]
    gate.release.set()
    for _ in range(3000):
        if task['status'] != 'running':
            break
        time.sleep(0.01)
    assert task['status'] == 'stopped' and task_id not in api_mod._cr_tasks


def test_the_board_so_far_is_served_while_the_re_check_runs(run, monkeypatch):
    held = threading.Event()
    seen = []

    def minutes(self, root, expiry, strike, right, day, may_fetch=None):
        seen.append(day)
        held.wait(20)
        return {'start': datetime(day.year, day.month, day.day, 9, 15), 't': [], 'c': []}

    monkeypatch.setattr(FakeIcici, 'historical_option_minute_closes', minutes, raising=False)
    monkeypatch.setattr(FakeIcici, 'historical_option_seconds',
                        lambda self, root, expiry, strike, right, day, index, may_fetch=None:
                        {'start': datetime(day.year, day.month, day.day, 9, 15), 't': [], 'c': []})
    api_mod._cr_tasks.clear()
    task_id = _start(run.app)
    task = api_mod._cr_tasks[task_id]
    for _ in range(3000):
        if task.get('partial'):
            break
        time.sleep(0.01)
    with run.app.test_request_context(f'/?seen=0'):
        body = api_mod.call_ratio_status.__wrapped__(task_id).get_json()
    assert body['status'] == 'running' and body['partial']['partial'] and body['partial']['rechecked'] == 0
    assert {r['exit_check'] for r in body['partial']['results']} == {'5m'}
    seq = body['progress']['partial_seq']
    with run.app.test_request_context(f'/?seen={seq}'):
        assert 'partial' not in api_mod.call_ratio_status.__wrapped__(task_id).get_json()   # not sent again
    held.set()
    assert _settled(task_id) == 'complete'
