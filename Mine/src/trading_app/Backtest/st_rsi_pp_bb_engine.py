"""
SuperTrend + RSI + Pivot Points + Bollinger backtest ("ST RSI PP BB with SL").

Mirrors the Quantman run "ST RSI PP BB with SL NIFTY CE": 3-minute candles on the
NIFTY INDEX, trade window 09:31-15:14, at most 5 transactions a day, and a
current-week CE bought at the premium nearest 250 with a 15 % premium stop-loss and
a trailing stop X(15 %), Y(10 %). Re-entry is "Next Minute".

Long (CE) entry — ALL on one CLOSED candle:

  1. close is above SuperTrend(10, 2.3)
  2. RSI(14) is above 70
  3. close is above R1 of yesterday's traditional pivots (Day timeframe)
  4. close is above the upper Bollinger Band(15, 3)

Exit: a closed candle whose close is BELOW the SuperTrend line, the premium
stop-loss / trailing stop (priced in `attach_option_legs`), or the 15:14 square-off.

Every indicator of the run says "Fields: Equity" (the index), unlike the ST EMA RSI
CCI run's "Future". Checked against its 75 entries over 2019-2026, the four
conditions hold on the candle before the entry for 71 of them on the index candles
(and 48 on the future's) with exactly these settings.

It is the ST + BB + RSI + R1 rule with this run's numbers, so the signals are the
parent engine's `signals()`; what this adds is the same-day RE-ENTRY: when the
option's own stop ends a trade before the SuperTrend does, Quantman buys again on
the next candle that still signals (`trade_after`, used by
`st_ema_rsi_cci_engine.price_with_reentries`).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from trading_app.Backtest.st_bb_rsi_engine import StBbRsiEngine


class StRsiPpBbEngine(StBbRsiEngine):
    def __init__(self, df, st_mult: float = 2.3, bb_period: int = 15, bb_mult: float = 3.0,
                 entry_start: str = '09:31', max_trades_per_day: int = 5,
                 direction: str = 'long', **kw):
        super().__init__(df, st_mult=st_mult, bb_period=bb_period, bb_mult=bb_mult,
                         entry_start=entry_start, max_trades_per_day=max_trades_per_day,
                         direction=direction, **kw)

    def trade_after(self, day: str, after_ts: str, taken: int) -> Optional[Dict[str, Any]]:
        """The next trade of `day` entered STRICTLY after `after_ts`
        ('YYYY-MM-DD HH:MM:SS'): a stop at 09:24:29 is followed by the 09:27 candle,
        never the 09:24 one it happened inside. None once `max_trades_per_day` is
        used up or no signal is left."""
        if taken >= self.max_trades_per_day:
            return None
        P = self._params({})
        b = self._base()
        sg = self.signals()
        signal = sg['signal'] * ((b['tmin'] >= P['entry_start']) & (b['tmin'] < P['entry_end']))
        ts = b['ts']
        for d, a in enumerate(b['starts']):
            if ts[a][:10] != day:
                continue
            end = int(b['ends'][d])
            idxs = [i for i in np.flatnonzero(signal) if a <= i < end and ts[i + 1] > after_ts]
            out = self._day(int(a), end, idxs, b['o'], b['c'], signal, sg['st_dir'], sg['st_line'],
                            sg['rsi'], sg['lv'], b['tmin'], ts, dict(P, max_trades_per_day=1))
            return out[0] if out else None
        return None
