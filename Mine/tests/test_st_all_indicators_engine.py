import numpy as np
import pandas as pd

from trading_app.Backtest.st_all_indicators_engine import StAllIndicatorsEngine
from trading_app.Backtest.st_ema_rsi_cci_engine import cci, ema


def _frame(days, step=3):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=step * k), 'open': cl, 'high': cl + 0.5,
                         'low': cl - 0.5, 'close': cl, 'volume': 0})
    return pd.DataFrame(rows)


def _rally_day():
    return [100.0] * 30 + list(np.linspace(100, 140, 14)) + [140.0] * 31


def _df():
    return _frame([('2025-01-01', [100.0] * 75), ('2025-01-02', _rally_day())])


LOOSE = dict(ema_period=20, rsi_level=55, rsi_cap=101, wr_level=-40, cpr_narrow=5.0, cci_level=0.0)


def test_defaults_ask_for_every_indicator():
    e = StAllIndicatorsEngine(_frame([('2025-01-01', [100.0] * 75)]))
    assert (e.st_period, e.st_mult) == (10, 2.0)
    assert (e.rsi_level, e.rsi_cap, e.wr_level, e.cci_level, e.ema_period) == (60.0, 85.0, -30.0, 100.0, 200)
    assert all((e.use_st, e.use_rsi, e.use_bb, e.use_wr, e.use_cci, e.use_ema, e.use_pivot))
    assert e.direction == 'long'


def test_trades_and_labels_the_pattern():
    trades, _ = StAllIndicatorsEngine(_df(), **LOOSE).run()
    assert trades and all(t['pattern'] == 'ST+EMA+RSI+CCI+WR+CPR+PP+BB' for t in trades)
    assert all(t['type'] == 'Long' for t in trades)


def test_cci_and_ema_gates_only_narrow_the_rule():
    on = StAllIndicatorsEngine(_df(), **LOOSE).signals()['signal']
    off = StAllIndicatorsEngine(_df(), use_cci=False, use_ema=False, **LOOSE).signals()['signal']
    assert (on != 0).sum() <= (off != 0).sum() and not ((on != 0) & (off == 0)).any()


def test_unreachable_cci_blocks_and_its_switch_restores():
    kw = dict(LOOSE, cci_level=1e6)
    assert StAllIndicatorsEngine(_df(), **kw).run()[0] == []
    assert StAllIndicatorsEngine(_df(), use_cci=False, **kw).run()[0]


def test_ema_gate_requires_close_above_the_ema():
    e = StAllIndicatorsEngine(_df(), **LOOSE)
    sg = e.signals()
    c = e._base()['c']
    hit = sg['signal'] == 1
    assert hit.any() and (c[hit] > ema(c, 20)[hit]).all()
    b = e._base()
    assert (sg['cci'][hit] > 0).all() and np.allclose(sg['cci'][hit], cci(b['h'], b['l'], b['c'], 20)[hit])


def test_rsi_off_drops_the_band_too():
    e = StAllIndicatorsEngine(_df(), use_rsi=False, rsi_cap=1.0)
    assert e.rsi_cap > 100
