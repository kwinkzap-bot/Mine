"""EMA Confluence — retiring an armed setup the market has left behind.

The backtest engine holds a pending breakout order with no expiry, which is
right for a continuous chain of simulated trades. Live, that pinned 12 of 133
symbols on levels years out of reach (ADANIPORTS was watching a Short at 703
off a 2023-06-23 signal candle with spot at 1785 on 2026-09-28) — and because
a `watching` symbol is never re-scanned, those names could never arm a newer
setup either.

These pin both halves of the gate: _scan_one must not re-arm a setup past the
age limit, and _run_daily_scan must unfreeze one that is already watching so
it gets scanned again. An OPEN position is never touched by either.
"""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from trading_app.algo.ema_confluence import ema_confluence_algo as eca
from trading_app.algo.ema_confluence.ema_confluence_algo import EmaConfluenceAlgo

CFG = {'direction': 'both', 'target_pct': 8}
JUDGED = '2026-09-25'          # newest candle every fake scan sees
TO_DATE = '2026-09-27'


def _days_before(n, ref=JUDGED):
    return (datetime.strptime(ref, '%Y-%m-%d') - timedelta(days=n)).date().isoformat()


def _engine(pending, last_candle=JUDGED):
    """EmaPullbackEngine as _scan_one uses it: construct, run(), then read
    daily_df / pending_order / open_trade."""
    class _E:
        def __init__(self, **kw):
            self.kwargs = kw
            self.daily_df = pd.DataFrame({'datetime': [pd.Timestamp(last_candle)]})
            self.pending_order = None
            self.open_trade = None

        def run(self):
            self.pending_order = dict(pending) if pending else None
    return _E


def _pending(signal_date, direction='Short', trigger=703.0, sl=741.0):
    return {'direction': direction, 'trigger_level': trigger, 'sl_level': sl,
            'signal_time': pd.Timestamp(signal_date)}


@pytest.fixture(autouse=True)
def _keep_out_of_the_production_log(monkeypatch):
    """The module attaches a RotatingFileHandler to the REAL algo log at import
    time, so a test that isn't muzzled writes invented trades into the log the
    live algo is debugged from. Detach that sink for the duration; caplog still
    sees everything, since it works off propagation to the root logger."""
    monkeypatch.setattr(
        eca.logger, 'handlers',
        [h for h in eca.logger.handlers if not getattr(h, '_emac_sink', False)])


@pytest.fixture
def algo(tmp_path, monkeypatch):
    monkeypatch.setattr(eca, 'STATE_FILE', str(tmp_path / 'state.json'))
    monkeypatch.setattr(eca, 'HISTORY_FILE', str(tmp_path / 'history.json'))
    monkeypatch.setattr(eca, 'ALL_HISTORY_FILE', str(tmp_path / 'all_history.json'))
    a = EmaConfluenceAlgo(username='test-user')
    a.env = {}
    monkeypatch.setattr(a, '_uvar', lambda key, default='': a.env.get(key, default))
    monkeypatch.setattr(a, '_daily_history',
                        lambda *A, **K: pd.DataFrame({'datetime': [pd.Timestamp(JUDGED)]}))
    a.signalled = []
    monkeypatch.setattr(a, '_notify_signal', lambda sym, s, age: a.signalled.append(sym))
    return a


def _scan(algo, monkeypatch, pending, s=None):
    monkeypatch.setattr(algo, '_ema_engine', lambda: _engine(pending))
    s = {'phase': 'pending_scan'} if s is None else s
    algo._scan_one(object(), True, 'ADANIPORTS', CFG, s, TO_DATE)
    return s


# ── _scan_one ────────────────────────────────────────────────────────────

def test_setup_past_the_age_limit_is_not_armed(algo, monkeypatch):
    s = _scan(algo, monkeypatch, _pending('2023-06-23'))
    assert s['phase'] == 'no_setup'
    assert 'signal_date' not in s
    assert 'trigger_level' not in s          # the dead level is cleared, not left on show
    assert s['stale_signal_date'] == '2023-06-23'
    assert s['scanned_candle'] == JUDGED     # the scan still records what it judged
    assert algo.signalled == []              # and no Telegram alert for a zombie


def test_setup_inside_the_age_limit_is_armed_as_before(algo, monkeypatch):
    signal = _days_before(eca._MAX_SETUP_AGE_DAYS - 1)
    s = _scan(algo, monkeypatch, _pending(signal))
    assert s['phase'] == 'watching'
    assert s['signal_date'] == signal
    assert s['trigger_level'] == 703.0
    assert algo.signalled == ['ADANIPORTS']


def test_the_limit_is_inclusive_at_exactly_max_age(algo, monkeypatch):
    s = _scan(algo, monkeypatch, _pending(_days_before(eca._MAX_SETUP_AGE_DAYS)))
    assert s['phase'] == 'watching'


def test_retirement_is_logged_once_not_every_scan(algo, monkeypatch, caplog):
    s = {'phase': 'pending_scan'}
    with caplog.at_level('INFO'):
        _scan(algo, monkeypatch, _pending('2023-06-23'), s)
        _scan(algo, monkeypatch, _pending('2023-06-23'), s)
    assert sum('retired, not re-armed' in r.message for r in caplog.records) == 1


def test_zero_max_age_restores_hold_forever(algo, monkeypatch):
    algo.env['EMA_CONFLUENCE_MAX_SETUP_AGE_DAYS'] = '0'
    s = _scan(algo, monkeypatch, _pending('2023-06-23'))
    assert s['phase'] == 'watching'
    assert s['signal_date'] == '2023-06-23'


def test_junk_max_age_falls_back_to_the_default(algo, monkeypatch):
    algo.env['EMA_CONFLUENCE_MAX_SETUP_AGE_DAYS'] = 'soon'
    assert algo._max_setup_age() == eca._MAX_SETUP_AGE_DAYS


def test_env_can_tighten_the_limit(algo, monkeypatch):
    algo.env['EMA_CONFLUENCE_MAX_SETUP_AGE_DAYS'] = '5'
    assert _scan(algo, monkeypatch, _pending(_days_before(6)))['phase'] == 'no_setup'
    assert _scan(algo, monkeypatch, _pending(_days_before(4)))['phase'] == 'watching'


def test_an_undateable_signal_is_left_alone(algo, monkeypatch):
    """A corrupt signal_time reads as "age unknown" — arm it rather than
    retire a setup the gate cannot date."""
    s = _scan(algo, monkeypatch, _pending(JUDGED))
    assert s['phase'] == 'watching'
    assert eca._setup_age_days(None, JUDGED) is None
    assert eca._setup_age_days('not-a-date', JUDGED) is None


# ── _run_daily_scan ──────────────────────────────────────────────────────

def _universe(monkeypatch, *symbols):
    from trading_app.Backtest import ema_symbol_universe as u
    monkeypatch.setattr(u, 'EMA_SYMBOL_DEFAULTS', {s: CFG for s in symbols})


def _sweep(algo, monkeypatch, stocks, rescan_phase='no_setup'):
    """Run the daily sweep with _scan_one stubbed, and report which symbols it
    was actually asked to scan."""
    seen = []

    def fake_scan_one(provider, is_symbol_provider, symbol, cfg, s, to_date):
        seen.append(symbol)
        s['phase'] = rescan_phase
        return JUDGED

    monkeypatch.setattr(algo, '_scan_one', fake_scan_one)
    algo._run_daily_scan(object(), True, {'stocks': stocks})
    return seen


def test_a_stale_watching_symbol_is_unfrozen_and_rescanned(algo, monkeypatch):
    _universe(monkeypatch, 'ADANIPORTS')
    stocks = {'ADANIPORTS': {'phase': 'watching', 'direction': 'Short',
                             'signal_date': '2023-06-23', 'trigger_level': 703.0,
                             'sl_level': 741.0, 'lot_size': 475}}
    assert _sweep(algo, monkeypatch, stocks) == ['ADANIPORTS']
    assert stocks['ADANIPORTS']['phase'] == 'no_setup'
    assert stocks['ADANIPORTS']['lot_size'] == 475      # _KEEP_ON_RESET survives


def test_a_fresh_watching_symbol_is_still_skipped(algo, monkeypatch):
    _universe(monkeypatch, 'PETRONET')
    stocks = {'PETRONET': {'phase': 'watching', 'direction': 'Short',
                           'signal_date': date.today().isoformat(),
                           'trigger_level': 280.3, 'sl_level': 288.25}}
    assert _sweep(algo, monkeypatch, stocks) == []
    assert stocks['PETRONET']['phase'] == 'watching'
    assert stocks['PETRONET']['trigger_level'] == 280.3


def test_an_open_position_is_never_retired_by_age(algo, monkeypatch):
    """The age gate retires setups, never trades — an open position is closed
    by SL/Target/roll or by hand, never behind the user's back."""
    _universe(monkeypatch, 'BAJAJFINSV')
    held = {'phase': 'in_position', 'direction': 'Short', 'signal_date': '2023-06-23',
            'trigger_level': 1881.4, 'entry_price': 1784.2, 'qty': 300}
    stocks = {'BAJAJFINSV': dict(held)}
    assert _sweep(algo, monkeypatch, stocks) == []
    assert stocks['BAJAJFINSV'] == held


def test_zero_max_age_leaves_the_sweep_as_it_was(algo, monkeypatch):
    algo.env['EMA_CONFLUENCE_MAX_SETUP_AGE_DAYS'] = '0'
    _universe(monkeypatch, 'ADANIPORTS')
    stocks = {'ADANIPORTS': {'phase': 'watching', 'signal_date': '2023-06-23',
                             'trigger_level': 703.0}}
    assert _sweep(algo, monkeypatch, stocks) == []
    assert stocks['ADANIPORTS']['phase'] == 'watching'


def test_a_rescanned_stale_symbol_can_arm_a_fresh_setup(algo, monkeypatch):
    """The point of unfreezing it: the name is back in play, not just cleared."""
    _universe(monkeypatch, 'ADANIPORTS')
    stocks = {'ADANIPORTS': {'phase': 'watching', 'signal_date': '2023-06-23',
                             'trigger_level': 703.0}}
    assert _sweep(algo, monkeypatch, stocks, rescan_phase='watching') == ['ADANIPORTS']
    assert stocks['ADANIPORTS']['phase'] == 'watching'
