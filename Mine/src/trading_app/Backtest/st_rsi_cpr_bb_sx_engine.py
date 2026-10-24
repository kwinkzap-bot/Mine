"""
SuperTrend + RSI + CPR (Demark) + Bollinger backtest on SENSEX ("ST RSI CPR BB with SL Sensex CE").

Mirrors the Quantman run "ST RSI CPR BB with SL Sensex CE": 5-minute candles of the SENSEX
INDEX ("Fields: Equity" on every indicator), trade window 09:15-15:14, at most 5
transactions a day, and a current-week SENSEX CE bought at the premium nearest 500 with a
17 % premium stop-loss and a trailing stop X(15 %), Y(10 %). Re-entry is "Next Minute".

Long (CE) entry — ALL on one CLOSED candle:

  1. close is above SuperTrend(10, 1.5)
  2. RSI(14) is above 70
  3. close is above R1 of today's CPR, category "Demarks" (day timeframe)
  4. today's CPR is NOT wide
  5. close is above the upper Bollinger Band(20, 2.5)

Exit: a closed candle whose close is BELOW the SuperTrend line, the premium stop-loss /
trailing stop (priced in `attach_option_legs`), or the 15:14 square-off.

It is the "ST RSI PP BB" rule (`st_rsi_pp_bb_engine`) with this run's numbers, whose pivot
gate is Demark's instead of the traditional floor pivots, and with the CPR-width gate; the
same-day re-entry (`trade_after`) is inherited.

Two readings are INFERRED from the screenshot, which shows only the settings:
  * Demark's pivots from yesterday's O/H/L/C: X = H + 2L + C if C < O, 2H + L + C if C > O,
    H + L + 2C if C = O; P = X / 4, R1 = X / 2 - L, S1 = X / 2 - H. The CPR around it is
    BC = (H + L) / 2, TC = 2P - BC;
  * CPR width % = |TC - BC| / P x 100. Quantman lists Narrow 0.2 / Moderately 0.5 /
    Wide 0.35: the CPR is "wide" at `cpr_wide` (0.35 %) or more, and the entry wants it not wide.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from trading_app.Backtest.st_rsi_pp_bb_engine import StRsiPpBbEngine


def demark_cpr(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Per bar: today's Demark R1 / S1 and CPR width % from yesterday's O/H/L/C."""
    idx = df.index
    date_key = pd.Series(idx.date, index=idx)
    daily = df.groupby(date_key).agg(o=('open', 'first'), h=('high', 'max'),
                                     l=('low', 'min'), c=('close', 'last'))
    o, h, l, c = (daily[k].shift(1) for k in ('o', 'h', 'l', 'c'))
    x = pd.Series(np.where(c < o, h + 2 * l + c, np.where(c > o, 2 * h + l + c, h + l + 2 * c)),
                  index=daily.index).where(c.notna())
    p = x / 4.0
    bc = (h + l) / 2.0
    tc = 2.0 * p - bc
    out = {'r1': x / 2.0 - l, 's1': x / 2.0 - h, 'width': (tc - bc).abs() / p * 100.0}
    return {k: date_key.map(v).to_numpy(dtype=float) for k, v in out.items()}


class StRsiCprBbSxEngine(StRsiPpBbEngine):
    def __init__(self, df, st_period: int = 10, st_mult: float = 1.5,
                 rsi_period: int = 14, rsi_level: float = 70.0,
                 bb_period: int = 20, bb_mult: float = 2.5,
                 cpr_wide: float = 0.35, use_cpr_wide: bool = True,
                 entry_start: str = '09:15', max_trades_per_day: int = 5,
                 direction: str = 'long', **kw):
        self.cpr_wide, self.use_cpr_wide = float(cpr_wide), bool(use_cpr_wide)
        super().__init__(df, st_period=st_period, st_mult=st_mult,
                         rsi_period=rsi_period, rsi_level=rsi_level,
                         bb_period=bb_period, bb_mult=bb_mult,
                         entry_start=entry_start, max_trades_per_day=max_trades_per_day,
                         direction=direction, **kw)

    def _base(self) -> Dict[str, Any]:
        fresh = self._base_cache is None
        b = super()._base()
        if fresh:                       # the parent's traditional pivots give way to Demark's
            cpr = demark_cpr(self.df)
            b['r1'], b['s1'], b['cpr_width'] = cpr['r1'], cpr['s1'], cpr['width']
        return b

    def signals(self, **over) -> Dict[str, Any]:
        sg = super().signals(**over)
        width = self._base()['cpr_width']
        if self.use_cpr_wide:
            with np.errstate(invalid='ignore'):
                sg['signal'] = np.where(width < self.cpr_wide, sg['signal'], 0)
        sg['cpr_width'] = width
        return sg
