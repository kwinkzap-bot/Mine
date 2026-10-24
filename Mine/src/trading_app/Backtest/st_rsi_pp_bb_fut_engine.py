"""
SuperTrend + RSI + Pivot Points + Bollinger backtest on the NIFTY FUTURE
("ST RSI PP BB with SL NIFTY Fut CE").

Mirrors the Quantman run "ST RSI PP BB with SL NIFTY Fut CE": 5-minute candles of the
NIFTY future ("Fields: Future" on every indicator), trade window 09:15-15:14, at most 5
transactions a day, and a current-week CE bought at the premium nearest 250 with a 13 %
premium stop-loss and a trailing stop X(13 %), Y(10 %). Re-entry is "Next Minute".

Long (CE) entry — ALL on one CLOSED candle:

  1. close is above SuperTrend(10, 1.8)
  2. RSI(20) is above 68
  3. close is above R1 of yesterday's traditional pivots (Day timeframe)
  4. close is above the upper Bollinger Band(20, 3.3)
  5. close is above the candle's own open (a green candle)

Exit: a closed candle whose close is BELOW the SuperTrend line, the premium
stop-loss / trailing stop (priced in `attach_option_legs`), or the 15:14 square-off.

It is the "ST RSI PP BB" rule (`st_rsi_pp_bb_engine`) with this run's numbers, the
green-candle gate and the future's candles instead of the index's. Checked against the
193 transactions of the run over 2019-2026, all five conditions hold on the candle
before the entry for 181 of them on the future's 5-minute candles with exactly these
settings, and 180 entries land on the same minute; the other 25 index signals the
engine finds are the ones the premium stop-loss / re-entry bookkeeping decides
(`st_ema_rsi_cci_engine.price_with_reentries`).
"""
from __future__ import annotations

from trading_app.Backtest.st_rsi_pp_bb_engine import StRsiPpBbEngine


class StRsiPpBbFutEngine(StRsiPpBbEngine):
    def __init__(self, df, st_period: int = 10, st_mult: float = 1.8,
                 rsi_period: int = 20, rsi_level: float = 68.0,
                 bb_period: int = 20, bb_mult: float = 3.3,
                 entry_start: str = '09:15', max_trades_per_day: int = 5,
                 direction: str = 'long', require_green: bool = True, **kw):
        super().__init__(df, st_period=st_period, st_mult=st_mult,
                         rsi_period=rsi_period, rsi_level=rsi_level,
                         bb_period=bb_period, bb_mult=bb_mult,
                         entry_start=entry_start, max_trades_per_day=max_trades_per_day,
                         direction=direction, require_green=require_green, **kw)
