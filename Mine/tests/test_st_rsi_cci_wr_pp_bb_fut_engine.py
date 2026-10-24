import numpy as np
import pandas as pd

from trading_app.Backtest.st_rsi_cci_wr_pp_bb_fut_engine import StRsiCciWrPpBbFutEngine


def _frame(days):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=3 * k), 'open': cl, 'high': cl + 0.5,
                         'low': cl - 0.5, 'close': cl, 'volume': 0})
    return pd.DataFrame(rows)


def _rally_day():
    return [100.0] * 30 + list(np.linspace(100, 140, 14)) + [140.0] * 56


def test_defaults_are_the_quantman_run():
    e = StRsiCciWrPpBbFutEngine(_frame([('2025-01-01', [100.0] * 100)]))
    assert (e.st_period, e.st_mult) == (10, 2.0)
    assert (e.rsi_period, e.rsi_level) == (14, 78.0)
    assert (e.cci_period, e.cci_level) == (20, 280.0)
    assert (e.wr_period, e.wr_level, e.use_wr) == (14, -15.0, True)
    assert (e.bb_period, e.bb_mult) == (20, 3.0)
    assert e.entry_start == 9 * 60 + 15 and e.max_trades_per_day == 5
    assert e.direction == 'long' and e.require_green is False


def test_every_gate_holds_on_the_signal_candle():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _rally_day())])
    e = StRsiCciWrPpBbFutEngine(df)
    trades, _ = e.run()
    assert trades and all(t['type'] == 'Long' for t in trades)
    sg = e.signals()
    for i in np.flatnonzero(sg['signal'] == 1):
        assert sg['rsi'][i] > 78 and sg['cci'][i] > 280
        assert e._ind('wr', 14)[i] > -15


def test_cci_gate_removes_signals():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _rally_day())])
    assert StRsiCciWrPpBbFutEngine(df, cci_level=-1e9).run()[0]
    assert StRsiCciWrPpBbFutEngine(df, cci_level=1e9).run()[0] == []


def test_trade_after_is_inherited_and_capped():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _rally_day())])
    e = StRsiCciWrPpBbFutEngine(df, cci_level=-1e9)
    assert e.trade_after('2025-01-02', '2025-01-02 09:00:00', taken=5) is None
