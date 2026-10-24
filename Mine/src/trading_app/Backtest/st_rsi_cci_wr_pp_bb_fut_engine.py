"""
SuperTrend + RSI + CCI + Williams %R + Pivot Points + Bollinger backtest on the NIFTY
FUTURE ("ST RSI CCI WR PP BB With SL NIFTY Fut CE").

Mirrors the Quantman run "ST RSI CCI WR PP BB With SL NIFTY Fut CE": 3-minute candles of
the NIFTY future ("Fields: Future" on every indicator), trade window 09:15-15:14, at most
5 transactions a day, and a current-week CE bought at the premium nearest 250 with a 15 %
premium stop-loss (no trailing stop on this run). Re-entry is "Next Minute".

Long (CE) entry — ALL on one CLOSED candle:

  1. close is above SuperTrend(10, 2)
  2. RSI(14) is above 78
  3. CCI(20) is above 280
  4. Williams %R(14) is above -15
  5. close is above R1 of yesterday's traditional pivots (Day timeframe)
  6. close is above the upper Bollinger Band(20, 3)

Exit: a closed candle whose close is BELOW the SuperTrend line, the premium stop-loss
(priced in `attach_option_legs`), or the 15:14 square-off.

It is the "ST RSI PP BB" rule (`st_rsi_pp_bb_engine`, whose `signals()` already carries
the Williams %R gate) with this run's numbers plus a CCI gate; `trade_after` (same-day
re-entry after the option's own stop) is inherited.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np

from trading_app.Backtest.st_ema_rsi_cci_engine import cci
from trading_app.Backtest.st_rsi_pp_bb_engine import StRsiPpBbEngine


class StRsiCciWrPpBbFutEngine(StRsiPpBbEngine):
    def __init__(self, df, st_period: int = 10, st_mult: float = 2.0,
                 rsi_period: int = 14, rsi_level: float = 78.0,
                 cci_period: int = 20, cci_level: float = 280.0,
                 wr_period: int = 14, wr_level: float = -15.0,
                 bb_period: int = 20, bb_mult: float = 3.0,
                 entry_start: str = '09:15', max_trades_per_day: int = 5,
                 direction: str = 'long', **kw):
        self.cci_period, self.cci_level = max(2, int(cci_period)), float(cci_level)
        super().__init__(df, st_period=st_period, st_mult=st_mult,
                         rsi_period=rsi_period, rsi_level=rsi_level,
                         use_wr=True, wr_period=wr_period, wr_level=wr_level,
                         bb_period=bb_period, bb_mult=bb_mult,
                         entry_start=entry_start, max_trades_per_day=max_trades_per_day,
                         direction=direction, **kw)

    def signals(self, **over) -> Dict[str, Any]:
        sg = super().signals(**over)
        b = self._base()
        k = ('cci', self.cci_period)
        if k not in self._ind_cache:
            self._ind_cache[k] = cci(b['h'], b['l'], b['c'], self.cci_period)
        with np.errstate(invalid='ignore'):
            keep = self._ind_cache[k] > self.cci_level          # long side only: CE
        sg['signal'] = np.where(keep & (sg['signal'] == 1), 1, 0)
        sg['cci'] = self._ind_cache[k]
        return sg
