import numpy as np
import pandas as pd

from trading_app.Backtest.st_rsi_cpr_bb_wr_sx_engine import StRsiCprBbWrSxEngine, traditional_cpr


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


def _two_days():
    return _frame([('2025-01-01', [100.0] * 75), ('2025-01-02', _rally_day())])


def test_defaults_are_the_quantman_run():
    e = StRsiCprBbWrSxEngine(_frame([('2025-01-01', [100.0] * 75)]))
    assert (e.st_period, e.st_mult) == (10, 1.5)
    assert (e.rsi_period, e.rsi_level, e.rsi_cap) == (14, 68.0, 80.0)
    assert (e.wr_period, e.wr_level, e.use_wr) == (14, -10.0, True)
    assert (e.bb_period, e.bb_mult) == (20, 2.5)
    assert (e.cpr_narrow, e.use_cpr_narrow) == (0.2, True)
    assert e.entry_start == 9 * 60 + 15 and e.max_trades_per_day == 5 and e.direction == 'long'


def test_traditional_r1_and_cpr_width():
    df = pd.DataFrame({'open': [100.0, 100.0], 'high': [110.0, 110.0], 'low': [90.0, 90.0],
                       'close': [104.0, 104.0]},
                      index=pd.to_datetime(['2025-01-01 09:15', '2025-01-02 09:15']))
    cpr = traditional_cpr(df)
    p = (110 + 90 + 104) / 3.0
    assert cpr['r1'][1] == 2 * p - 90 and cpr['s1'][1] == 2 * p - 110
    bc = 100.0
    assert abs(cpr['width'][1] - abs(2 * p - bc - bc) / p * 100) < 1e-9
    assert np.isnan(cpr['r1'][0])


def test_first_day_has_no_cpr_so_no_signal():
    assert StRsiCprBbWrSxEngine(_frame([('2025-01-01', _rally_day())])).run()[0] == []


def test_every_gate_holds_on_the_signal_candle():
    e = StRsiCprBbWrSxEngine(_two_days(), use_cpr_narrow=False, rsi_cap=101.0, wr_level=-100.0 + 1e-9,
                             rsi_level=60.0)
    trades, _ = e.run()
    assert trades and all(t['type'] == 'Long' for t in trades)
    sg, b = e.signals(), e._base()
    wr = e._ind('wr', 14)
    for i in np.flatnonzero(sg['signal'] == 1):
        assert b['c'][i] > b['r1'][i] and sg['rsi'][i] > 60 and wr[i] > -100.0


def test_rsi_cap_and_wr_and_narrow_each_veto():
    base = dict(use_cpr_narrow=False, rsi_level=60.0)
    assert StRsiCprBbWrSxEngine(_two_days(), rsi_cap=101.0, wr_level=-100.0 + 1e-9, **base).run()[0]
    # RSI must also be under the cap: a cap at the floor leaves no candle.
    assert StRsiCprBbWrSxEngine(_two_days(), rsi_cap=1.0, wr_level=-100.0 + 1e-9, **base).run()[0] == []
    # %R can never be above 1.
    assert StRsiCprBbWrSxEngine(_two_days(), rsi_cap=101.0, wr_level=1.0, **base).run()[0] == []
    # No CPR is narrower than a negative limit.
    assert StRsiCprBbWrSxEngine(_two_days(), rsi_cap=101.0, wr_level=-100.0 + 1e-9, rsi_level=60.0,
                                cpr_narrow=-1.0).run()[0] == []


def test_trade_after_is_inherited_and_capped():
    e = StRsiCprBbWrSxEngine(_two_days(), use_cpr_narrow=False)
    assert e.trade_after('2025-01-02', '2025-01-02 09:00:00', taken=5) is None
