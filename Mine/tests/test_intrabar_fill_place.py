"""An intrabar fill's option price is read where the index crossed the level inside its minute.

13 Nov 2026, ST Touch Break tab 3 (NIFTY - Sell - 5m - #9): the 14:31 SuperTrend stop came at the
minute's END — the index went 25,092.95 → 25,104.00 (its high) and the line was 25,101.48. The 25300 PE
minute was 254.65 → 243.50, so mid-minute read 249.08 while the live sell filled at 244.75."""
from datetime import date

import pandas as pd

from trading_app.Backtest import st_bb_rsi_engine as sbr
from trading_app.service import cpr_option_service as cos

IDX = pd.DataFrame([{'date': pd.Timestamp('2026-11-13 14:31'), 'open': 25092.95, 'high': 25104.0,
                     'low': 25090.6, 'close': 25104.0}]).set_index('date')
PE = [{'time': '13:29', 'open': 232.8, 'high': 242.5, 'low': 232.8, 'close': 242.45},
      {'time': '14:31', 'open': 254.65, 'high': 258.55, 'low': 243.5, 'close': 243.5}]


def test_cross_frac_places_the_level_on_the_minutes_open_to_close_path():
    f = sbr._cross_frac(IDX, '2026-11-13', '14:31', 25101.48)
    assert abs(f - (25101.48 - 25092.95) / (25104.0 - 25092.95)) < 1e-9
    assert sbr._cross_frac(IDX, '2026-11-13', '14:31', 25089.0) is None      # a wick, not the path: mid
    assert sbr._cross_frac(IDX, '2026-11-13', '14:32', 25101.48) is None     # no such minute
    assert sbr._cross_frac(None, '2026-11-13', '14:31', 25101.48) is None


def test_the_stop_exit_is_priced_where_the_index_reached_the_line(monkeypatch):
    monkeypatch.setattr(cos, '_option_minutes_fetcher', lambda symbol: (lambda con, day: PE))
    monkeypatch.setattr(cos, 'contract', lambda *a, **k: {'strike': 25300, 'option_type': 'PE',
                                                         'expiry': '2026-11-17'})
    trade = {'entry_time': '2026-11-13 13:30:00', 'exit_time': '2026-11-13 14:31:00', 'type': 'Short',
             'entry_price': 25118.6, 'exit_price': 25101.48, 'pnl': 17.12,
             'intrabar_entry': False, 'intrabar_exit': True}
    priced, _ = sbr.attach_option_legs('NIFTY', [dict(trade)], premium=None, bar_minutes=5,
                                       calendar=[date(2026, 11, 13)], index_minutes=IDX)
    t = priced[0]
    assert t['entry_premium'] == 242.45                           # a close entry: the minute before's close
    frac = (25101.48 - 25092.95) / (25104.0 - 25092.95)
    assert t['exit_premium'] == round(254.65 + frac * (243.5 - 254.65), 2)     # 246.04, not mid 249.08
    # with no index minutes the leg falls back to the minute's mid, as before
    priced, _ = sbr.attach_option_legs('NIFTY', [dict(trade)], premium=None, bar_minutes=5,
                                       calendar=[date(2026, 11, 13)], index_minutes=None)
    assert priced[0]['exit_premium'] == round((254.65 + 243.5) / 2, 2)
