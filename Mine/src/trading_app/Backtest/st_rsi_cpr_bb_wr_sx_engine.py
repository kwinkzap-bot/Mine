"""
SuperTrend + RSI + CPR + Bollinger + Williams %R backtest on SENSEX
("ST RSI CPR BB WR with SL SX2 CE").

Mirrors the Quantman run "ST RSI CPR BB WR with SL SX2 CE": 5-minute candles of the SENSEX
INDEX ("Fields: Equity" on every indicator), trade window 09:15-15:14, at most 5
transactions a day, and a current-week SENSEX CE bought at the premium nearest 500 with a
15 % premium stop-loss and a trailing stop X(15 %), Y(10 %). Re-entry is "Next Minute".

Long (CE) entry — ALL on one CLOSED candle:

  1. close is above SuperTrend(10, 1.5)
  2. RSI(14) is above 68 AND below 80
  3. Williams %R(14) is above -10
  4. close is above R1 of today's CPR, category "Traditional" (day timeframe)
  5. today's CPR is NARROW
  6. close is above the upper Bollinger Band(20, 2.5)

Exit: a closed candle whose close is BELOW the SuperTrend line, the premium stop-loss /
trailing stop (priced in `attach_option_legs`), or the 15:14 square-off.

It is the "ST RSI CPR BB Sensex" rule (`st_rsi_cpr_bb_sx_engine`) with this run's numbers
and three differences: the CPR category is Traditional (floor pivots) instead of Demark's,
the CPR must be NARROW instead of merely not wide, and RSI is a band (68-80) with a
Williams %R gate on top. The same-day re-entry (`trade_after`) is inherited.

Quantman lists the CPR as Narrow 0.2 / Moderately 0.5 / Wide 0.35. The CPR width is read as
|TC - BC| / P x 100 and "narrow" as that width BELOW `cpr_narrow` (0.2 %); this is INFERRED
from the settings alone, and `tests/` pins it to the run's own entries.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from trading_app.Backtest.st_rsi_cpr_bb_sx_engine import StRsiCprBbSxEngine
from trading_app.Backtest.st_rsi_pp_bb_engine import StRsiPpBbEngine


def traditional_cpr(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Per bar: today's traditional R1 / S1 and CPR width % from yesterday's H/L/C."""
    idx = df.index
    date_key = pd.Series(idx.date, index=idx)
    daily = df.groupby(date_key).agg(h=('high', 'max'), l=('low', 'min'), c=('close', 'last'))
    h, l, c = (daily[k].shift(1) for k in ('h', 'l', 'c'))
    p = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = 2.0 * p - bc
    out = {'r1': 2.0 * p - l, 's1': 2.0 * p - h, 'width': (tc - bc).abs() / p * 100.0}
    return {k: date_key.map(v).to_numpy(dtype=float) for k, v in out.items()}


class StRsiCprBbWrSxEngine(StRsiCprBbSxEngine):
    def __init__(self, df, st_period: int = 10, st_mult: float = 1.5,
                 rsi_period: int = 14, rsi_level: float = 68.0, rsi_cap: float = 80.0,
                 bb_period: int = 20, bb_mult: float = 2.5,
                 wr_period: int = 14, wr_level: float = -10.0,
                 cpr_narrow: float = 0.2, use_cpr_narrow: bool = True,
                 entry_start: str = '09:15', max_trades_per_day: int = 5,
                 direction: str = 'long', **kw):
        self.rsi_cap = float(rsi_cap)
        self.cpr_narrow, self.use_cpr_narrow = float(cpr_narrow), bool(use_cpr_narrow)
        super().__init__(df, st_period=st_period, st_mult=st_mult,
                         rsi_period=rsi_period, rsi_level=rsi_level,
                         bb_period=bb_period, bb_mult=bb_mult,
                         use_cpr_wide=False,
                         entry_start=entry_start, max_trades_per_day=max_trades_per_day,
                         direction=direction, use_wr=True, wr_period=wr_period,
                         wr_level=wr_level, **kw)

    def _base(self) -> Dict[str, Any]:
        fresh = self._base_cache is None
        b = StRsiPpBbEngine._base(self)       # skip Demark's override: this run's pivots are Traditional
        if fresh:
            cpr = traditional_cpr(self.df)
            b['r1'], b['s1'], b['cpr_width'] = cpr['r1'], cpr['s1'], cpr['width']
        return b

    def signals(self, **over) -> Dict[str, Any]:
        sg = super().signals(**over)
        width = self._base()['cpr_width']
        with np.errstate(invalid='ignore'):
            ok = sg['rsi'] < self.rsi_cap
            if self.use_cpr_narrow:
                ok &= width < self.cpr_narrow
            sg['signal'] = np.where(ok, sg['signal'], 0)
        return sg
