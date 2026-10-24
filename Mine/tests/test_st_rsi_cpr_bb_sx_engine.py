import numpy as np
import pandas as pd

from trading_app.Backtest.st_rsi_cpr_bb_sx_engine import StRsiCprBbSxEngine, demark_cpr


def _frame(days, step=5):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=step * k), 'open': cl, 'high': cl + 0.5,
                         'low': cl - 0.5, 'close': cl, 'volume': 0})
    return pd.DataFrame(rows)


def _rally_day():
    return [100.0] * 30 + list(np.linspace(100, 140, 14)) + [140.0] * 31


def test_defaults_are_the_quantman_run():
    e = StRsiCprBbSxEngine(_frame([('2025-01-01', [100.0] * 75)]))
    assert (e.st_period, e.st_mult) == (10, 1.5)
    assert (e.rsi_period, e.rsi_level) == (14, 70.0)
    assert (e.bb_period, e.bb_mult) == (20, 2.5)
    assert (e.cpr_wide, e.use_cpr_wide) == (0.35, True)
    assert e.entry_start == 9 * 60 + 15 and e.max_trades_per_day == 5 and e.direction == 'long'


def test_demark_r1_follows_the_prior_close_vs_open():
    # Prior day: open 100, high 110, low 90; close 95 (down), 105 (up), 100 (flat).
    def r1(close):
        df = pd.DataFrame({'open': [100.0, 100.0], 'high': [110.0, 110.0], 'low': [90.0, 90.0],
                           'close': [close, close]},
                          index=pd.to_datetime(['2025-01-01 09:15', '2025-01-02 09:15']))
        return demark_cpr(df)['r1'][1]
    assert r1(95.0) == (110 + 2 * 90 + 95) / 2 - 90
    assert r1(105.0) == (2 * 110 + 90 + 105) / 2 - 90
    assert r1(100.0) == (110 + 90 + 2 * 100) / 2 - 90


def test_first_day_has_no_cpr_so_no_signal():
    e = StRsiCprBbSxEngine(_frame([('2025-01-01', _rally_day())]))
    assert e.run()[0] == []


def test_signal_needs_the_close_above_demark_r1_and_a_narrow_cpr():
    df = _frame([('2025-01-01', [100.0] * 75), ('2025-01-02', _rally_day())])
    e = StRsiCprBbSxEngine(df, use_cpr_wide=False)
    trades, _ = e.run()
    assert trades and all(t['type'] == 'Long' for t in trades)
    sg, b = e.signals(), e._base()
    for i in np.flatnonzero(sg['signal'] == 1):
        assert b['c'][i] > b['r1'][i] and sg['rsi'][i] > 70
    # A limit no width can be under calls every CPR wide: nothing is left to trade.
    assert StRsiCprBbSxEngine(df, cpr_wide=-1.0).run()[0] == []


def test_trade_after_is_inherited_and_capped():
    df = _frame([('2025-01-01', [100.0] * 75), ('2025-01-02', _rally_day())])
    e = StRsiCprBbSxEngine(df, use_cpr_wide=False)
    assert e.trade_after('2025-01-02', '2025-01-02 09:00:00', taken=5) is None
