"""The fast Find Best Params sweep books exactly what the bar-by-bar engine does.

optimise_second_candle finds each day's entry once per (candle #, direction)
and reads every SL:Target ratio's exit off it with running highs/lows
(_exits_for_ratios). SecondCandleBacktestEngine.run walks every bar. Every
row of the sweep must equal a run of its settings — trades, P&L on both bases,
win/loss, drawdown — including gap opens past the range, bars that hit SL and
target together, the cut-off, and a missing bar.
"""
import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest import second_candle_engine as sc


def _random_days(seed, days=40, bar='1min', gaps=True, vol=6.0):
    rng = np.random.default_rng(seed)
    frames = []
    for d in pd.bdate_range('2026-06-01', periods=days):
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=15),
                            d + pd.Timedelta(hours=15, minutes=29), freq=bar)
        steps = rng.normal(0, vol, len(idx))
        if gaps:
            steps[rng.integers(1, len(idx), 3)] += rng.choice([-60, 60], 3)   # gaps past the range
        close = 24000 + np.cumsum(steps)
        opn = np.r_[close[0] - steps[0], close[:-1]]
        spread = np.abs(rng.normal(0, 4, len(idx)))
        df = pd.DataFrame({'open': opn, 'close': close,
                           'high': np.maximum(opn, close) + spread,
                           'low': np.minimum(opn, close) - spread, 'volume': 0}, index=idx)
        if gaps:
            df = df.drop(df.index[rng.integers(5, len(idx) - 5)])            # a missing bar
        frames.append(df)
    return pd.concat(frames)


@pytest.mark.parametrize('seed,bar,cut', [(1, '1min', (15, 25)), (2, '5min', (15, 25)),
                                          (3, '30s', (11, 0)), (4, '3min', (10, 7)),
                                          (5, '1min', (16, 0))])
def test_every_sweep_row_equals_a_bar_by_bar_run(seed, bar, cut):
    # seed 5 is a quiet market with no cut-off before the close: EOD exits
    df = _random_days(seed, bar=bar, vol=0.3 if seed == 5 else 6.0)
    rows = sc.optimise_second_candle(df, *cut, min_trades=0)
    assert len(rows) == len(sc._CANDLE_GRID) * len(sc._RR_GRID) * len(sc._DIR_GRID)
    for row in rows:
        _, el, es = next(d for d in sc._DIR_GRID if d[0] == row['direction'])
        trades, s = sc.SecondCandleBacktestEngine(
            df=df, candle_index=row['candle_index'], rr_ratio=row['rr_ratio'],
            exit_hour=cut[0], exit_minute=cut[1], enable_long=el, enable_short=es).run()
        assert row['total_trades'] == s['total_trades']
        assert row['index_pnl'] == s['total_pnl']
        assert row['total_pnl'] == s['total_pnl']
        assert (row['wins'], row['losses'], row['max_drawdown']) == (s['wins'], s['losses'], s['max_drawdown'])
        _, so = sc.SecondCandleBacktestEngine(
            df=df, candle_index=row['candle_index'], rr_ratio=row['rr_ratio'],
            exit_hour=cut[0], exit_minute=cut[1], enable_long=el, enable_short=es,
            option_pnl=True).run()
        assert row['option_pnl'] == so['total_pnl']


def test_the_reasons_cover_every_exit_kind():
    """The data above really exercises SL, target, cut-off and EOD exits."""
    seen = set()
    for seed, cut, vol in ((1, (15, 25), 6.0), (4, (10, 7), 6.0), (5, (16, 0), 0.3)):
        df = _random_days(seed, gaps=False, vol=vol)
        for ci in sc._CANDLE_GRID:
            for rr in sc._RR_GRID:
                trades, _ = sc.SecondCandleBacktestEngine(df=df, candle_index=ci, rr_ratio=rr,
                                                          exit_hour=cut[0], exit_minute=cut[1]).run()
                seen |= {t['exit_reason'] for t in trades}
    assert {'SL Hit', 'TG Hit', 'Time Exit', 'EOD Exit'} <= seen


def test_strike_search_matches_the_full_ladder():
    """The binary-searched ≈₹250 strike is the one min() over all 81 would pick."""
    rng = np.random.default_rng(7)
    for _ in range(3000):
        flag = 'c' if rng.random() < .5 else 'p'
        spot = round(rng.uniform(8000, 85000) / 5) * 5
        t = round(rng.uniform(60 / 31536000, 14 / 365), 5)
        iv = float(rng.choice([0.08, 0.15, 0.3, 0.6]))
        step = float(rng.choice([50.0, 100.0]))
        atm = round(spot / step) * step
        ladder = [k for k in (atm + i * step for i in range(-sc._STRIKE_WINDOW, sc._STRIKE_WINDOW + 1)) if k > 0]
        full = min(ladder, key=lambda k: abs(sc._bs_price(flag, spot, k, t, sc._RISK_FREE, iv) - sc._PREM250_TARGET))
        assert sc._select_strike(flag, 'premium250', spot, t, iv, step) == full
