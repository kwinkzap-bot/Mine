"""
Every Simple Trader indicator in one backtest ("ST ALL: ST + EMA + RSI + CCI + WR + CPR + PP + BB").

The seven Simple Trader strategies each use a subset of the same indicators. This one asks
for the union — a CE is bought only when ALL of them agree on one CLOSED candle:

  1. close is above SuperTrend(10, 2)                         (every strategy)
  2. RSI(14) is above `rsi_level` and below `rsi_cap`         (every strategy; 6/7 use the band)
  3. close is above the upper Bollinger Band(20, 2)           (every strategy)
  4. Williams %R(14) is above `wr_level`                      (1, 5, 7 and the ST EMA run)
  5. CCI(20) is above `cci_level`                             (2, 5)
  6. close is above EMA(200)                                  (2)
  7. close is above R1 of today's Traditional pivots          (1, 3, 4, 5, 7; 6 uses Demark's)
  8. today's CPR is NARROW (width under `cpr_narrow` %)       (2, 7)

Exit: a closed candle whose close is BELOW the SuperTrend line, the premium stop-loss /
trailing stop (priced in `attach_option_legs`), or the 15:14 square-off. Same-day re-entry
after the option's own stop is `trade_after`, inherited.

Each gate has its own switch (`use_*`) — off, it is simply not asked — so the strategy can be
narrowed to any subset of the group, and Find Best Params can see which gates earn their place.
All eight on is a very selective rule; the defaults are therefore looser than any single run
(RSI 60-85, CCI above 100, %R above -30) so that it still trades.

It is the "ST RSI CPR BB WR" engine (`st_rsi_cpr_bb_wr_sx_engine`: ST, RSI band, %R, Traditional
narrow CPR, BB) with the CCI and EMA gates added, so the signal rule of the group's pieces is
reused, never copied.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np

from trading_app.Backtest.st_ema_rsi_cci_engine import cci, ema
from trading_app.Backtest.st_rsi_cpr_bb_wr_sx_engine import StRsiCprBbWrSxEngine


class StAllIndicatorsEngine(StRsiCprBbWrSxEngine):
    def __init__(self, df, st_period: int = 10, st_mult: float = 2.0,
                 rsi_period: int = 14, rsi_level: float = 60.0, rsi_cap: float = 85.0,
                 bb_period: int = 20, bb_mult: float = 2.0,
                 wr_period: int = 14, wr_level: float = -30.0,
                 cci_period: int = 20, cci_level: float = 100.0,
                 ema_period: int = 200,
                 cpr_narrow: float = 0.2, use_cpr_narrow: bool = True,
                 use_rsi: bool = True, use_bb: bool = True, use_st: bool = True,
                 use_pivot: bool = True, use_wr: bool = True,
                 use_cci: bool = True, use_ema: bool = True,
                 entry_start: str = '09:15', max_trades_per_day: int = 5,
                 direction: str = 'long', **kw):
        self.cci_period, self.cci_level = max(2, int(cci_period)), float(cci_level)
        self.ema_period = max(2, int(ema_period))
        self.use_cci, self.use_ema = bool(use_cci), bool(use_ema)
        if not use_rsi:
            rsi_cap = 1e9                   # the parent applies the cap on its own; RSI off means no band either
        super().__init__(df, st_period=st_period, st_mult=st_mult,
                         rsi_period=rsi_period, rsi_level=rsi_level, rsi_cap=rsi_cap,
                         bb_period=bb_period, bb_mult=bb_mult,
                         wr_period=wr_period, wr_level=wr_level,
                         cpr_narrow=cpr_narrow, use_cpr_narrow=use_cpr_narrow,
                         entry_start=entry_start, max_trades_per_day=max_trades_per_day,
                         direction='long', use_rsi=use_rsi, use_bb=use_bb, use_st=use_st,
                         use_pivot=use_pivot, **kw)
        self.use_wr = bool(use_wr)          # the WR parent always switches %R on; this one lets it go

    def simulate(self, **over):
        trades = super().simulate(**over)
        for t in trades:
            t['pattern'] = 'ST+EMA+RSI+CCI+WR+CPR+PP+BB'
        return trades

    def signals(self, **over) -> Dict[str, Any]:
        sg = super().signals(**over)
        b = self._base()
        k_cci, k_ema = ('cci', self.cci_period), ('ema', self.ema_period)
        if k_cci not in self._ind_cache:
            self._ind_cache[k_cci] = cci(b['h'], b['l'], b['c'], self.cci_period)
        if k_ema not in self._ind_cache:
            self._ind_cache[k_ema] = ema(b['c'], self.ema_period)
        cci_v, ema_v = self._ind_cache[k_cci], self._ind_cache[k_ema]
        keep = np.ones(len(b['c']), dtype=bool)
        with np.errstate(invalid='ignore'):
            if self.use_cci:
                keep &= cci_v > self.cci_level
            if self.use_ema:
                keep &= b['c'] > ema_v
        sg['signal'] = np.where(keep & (sg['signal'] == 1), 1, 0)
        sg['cci'], sg['ema'] = cci_v, ema_v
        return sg
