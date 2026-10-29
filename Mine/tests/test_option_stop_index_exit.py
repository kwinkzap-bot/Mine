"""A premium stop that ends a trade early moves the INDEX exit with it (29 Oct 2026)."""
import pandas as pd

from trading_app.Backtest import st_bb_rsi_engine as sbr
from trading_app.service import cpr_option_service as cos


def _run(monkeypatch, leg, index_minutes):
    monkeypatch.setattr(sbr, '_listed_expiries', lambda *a, **k: [])
    monkeypatch.setattr(cos, 'legs_at', lambda *a, **k: {'legs': [dict(leg)], 'lot': 20})
    trade = {'entry_time': '2026-10-29 09:40:00', 'exit_time': '2026-10-29 10:55:00',
             'type': 'Long', 'entry_price': 73490.26, 'exit_price': 73689.24, 'pnl': 198.98}
    priced, _ = sbr.attach_option_legs('SENSEX', [trade], 500, 5, calendar=[pd.Timestamp('2026-10-29').date()],
                                       sl_pct=17, index_minutes=index_minutes)
    return priced[0]


MINUTES = pd.DataFrame({'close': [73414.69, 73415.41, 73432.37]},
                       index=pd.to_datetime(['2026-10-29 09:42', '2026-10-29 09:43', '2026-10-29 09:44']))
LEG = {'pnl': -81.8, 'entry': 481.3, 'exit': 399.5, 'strike': 73100, 'option_type': 'CE',
       'expiry': '2026-10-29', 'exit_time': '09:43', 'stop': 'Stop Loss'}


def test_stopped_leg_reads_index_at_the_stop_minute(monkeypatch):
    t = _run(monkeypatch, LEG, MINUTES)
    assert t['exit_time'] == '2026-10-29 09:43:00' and t['exit_reason'] == 'Stop Loss'
    assert t['exit_price'] == 73415.41
    assert t['index_pnl'] == round(73415.41 - 73490.26, 2)


def test_unstopped_leg_keeps_the_index_exit(monkeypatch):
    leg = {k: v for k, v in LEG.items() if k != 'stop'}
    t = _run(monkeypatch, leg, MINUTES)
    assert t['exit_price'] == 73689.24 and t['index_pnl'] == 198.98


def test_no_minutes_leaves_the_row_as_it_was(monkeypatch):
    t = _run(monkeypatch, LEG, None)
    assert t['exit_price'] == 73689.24 and t['index_pnl'] == 198.98
