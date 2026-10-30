"""Simple Trader 1-7 — the decisions of the live algos, with no I/O.

Each Simple Trader strategy is a Backtest-page engine (the Quantman runs: ``StBbRsiEngine``,
``StEmaRsiCciEngine`` and the ``StRsiPpBb*`` family). The live algo calls the engine's own
``signals()`` on the closed candles so far — never a copy of the rule — and reads the LAST
candle, exactly as ``st_bb_rsi_live_engine`` does for ST + BB + RSI:

* a signal on the candle that JUST closed enters now (the backtest's "next bar's open");
* an open trade ends when a later candle's close ends it — SuperTrend against the trade
  (1, 3-7) or RSI back across its exit line (2) — or at the square-off minute;
* the OPTION's own stop-loss / trailing stop ends it between candles (``stop_level``, the
  backtest's ``_premium_stop`` driven by the live premium), after which the next candle
  that still signals re-enters ("Re-entry: Next Minute"), up to ``max_trades`` a day.

Everything takes plain dicts and a DataFrame of CLOSED candles, so it is tested without a broker.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import StBbRsiEngine, brokerage_for
from trading_app.Backtest.st_ema_rsi_cci_engine import StEmaRsiCciEngine
from trading_app.Backtest.st_rsi_cci_wr_pp_bb_fut_engine import StRsiCciWrPpBbFutEngine
from trading_app.Backtest.st_rsi_cpr_bb_sx_engine import StRsiCprBbSxEngine
from trading_app.Backtest.st_rsi_cpr_bb_wr_sx_engine import StRsiCprBbWrSxEngine
from trading_app.Backtest.st_rsi_pp_bb_engine import StRsiPpBbEngine
from trading_app.Backtest.st_rsi_pp_bb_fut_engine import StRsiPpBbFutEngine
from trading_app.Backtest.st_touch_break_engine import StTouchBreakEngine
from trading_app.service import st_touch_break_live_engine as tb


class Spec:
    """One Simple Trader strategy: the engine, the candles it reads, the option it buys and
    the numbers the matching Backtest form ships with (``params`` are engine arguments, each
    overridable by ``ST<n>_<NAME>`` in the env file)."""

    def __init__(self, n, name, cls, kind, symbol, data, bar, side, premium, entry_start,
                 sl_pct, tsl_x, tsl_y, max_trades, params, fixed=None, rule='', tab=''):
        self.n, self.name, self.cls, self.kind = n, name, cls, kind
        self.symbol, self.data, self.bar, self.side = symbol, data, bar, side
        self.premium, self.entry_start = premium, entry_start
        self.sl_pct, self.tsl_x, self.tsl_y, self.max_trades = sl_pct, tsl_x, tsl_y, max_trades
        self.params, self.fixed, self.rule = params, fixed or {}, rule
        self.tab = tab                      # the Mine group's sub-tab name ('' for Simple Trader 1-7)

    @property
    def option_type(self) -> str:
        return 'CE' if self.side == 'long' else 'PE'


# 'st' = StBbRsiEngine family (exit: SuperTrend turns against the trade); 'ema' = StEmaRsiCciEngine
# (exit: RSI back across its exit line); 'tb' = StTouchBreakEngine (ST Touch Break, the Mine group:
# a setup is ARMED on a touch candle and entered intrabar — st_touch_break_live_engine). 'data' is
# 'spot' (the index) or 'fut' (the NIFTY future).
SPECS: Dict[int, Spec] = {s.n: s for s in (
    Spec(1, 'BB PP ST RSI WR', StBbRsiEngine, 'st', 'NIFTY', 'spot', 3, 'long', 250, '09:15',
         13, 15, 10, 1,
         dict(rsi_period=14, rsi_level=70, st_period=10, st_mult=2, bb_period=15, bb_mult=3,
              wr_period=14, wr_level=-30),
         fixed=dict(use_rsi=True, use_bb=True, use_st=True, use_pivot=True, use_wr=True,
                    bb_offset=0, htf='off', require_green=False),
         rule='NIFTY 3-min: close above BB(15,3) upper and R1, RSI(14) > 70, %R(14) > -30, above SuperTrend(10,2)'
              ' → buy the ≈250 CE; exit on a close below SuperTrend'),
    Spec(2, 'ST EMA RSI CCI WR CPR BB', StEmaRsiCciEngine, 'ema', 'NIFTY', 'spot', 3, 'short', 250, '09:15',
         15, 5, 5, 5,
         dict(st_period=10, st_mult=2, ema_period=200, rsi_period=20, rsi_entry=30, rsi_exit=45,
              cci_period=20, cci_level=-200, wr_period=14, wr_level=-85, bb_period=20, bb_mult=2,
              cpr_narrow=0.2),
         fixed=dict(use_cpr_narrow=True),
         rule='NIFTY 3-min: below SuperTrend(10,2), EMA(200), S1 and BB(20,2) lower; RSI(20) < 30, CCI(20) < -200,'
              ' %R(14) < -85, narrow CPR → buy the ≈250 PE; exit when RSI(20) > 45'),
    Spec(3, 'ST RSI PP BB', StRsiPpBbEngine, 'st', 'NIFTY', 'spot', 3, 'long', 250, '09:31',
         15, 15, 10, 5,
         dict(st_period=10, st_mult=2.3, rsi_period=14, rsi_level=70, bb_period=15, bb_mult=3),
         rule='NIFTY 3-min from 09:31: close above SuperTrend(10,2.3), R1 and BB(15,3) upper, RSI(14) > 70'
              ' → buy the ≈250 CE; exit on a close below SuperTrend'),
    Spec(4, 'ST RSI PP BB Fut', StRsiPpBbFutEngine, 'st', 'NIFTY', 'fut', 5, 'long', 250, '09:15',
         13, 13, 10, 5,
         dict(st_period=10, st_mult=1.8, rsi_period=20, rsi_level=68, bb_period=20, bb_mult=3.3),
         fixed=dict(require_green=True),
         rule='NIFTY FUTURE 5-min: green candle closing above SuperTrend(10,1.8), R1 and BB(20,3.3) upper,'
              ' RSI(20) > 68 → buy the ≈250 CE; exit on a close below SuperTrend'),
    Spec(5, 'ST RSI CCI WR PP BB Fut', StRsiCciWrPpBbFutEngine, 'st', 'NIFTY', 'fut', 3, 'long', 250, '09:15',
         15, 0, 0, 5,
         dict(st_period=10, st_mult=2, rsi_period=14, rsi_level=78, cci_period=20, cci_level=280,
              wr_period=14, wr_level=-15, bb_period=20, bb_mult=3),
         rule='NIFTY FUTURE 3-min: close above SuperTrend(10,2), R1 and BB(20,3) upper; RSI(14) > 78,'
              ' CCI(20) > 280, %R(14) > -15 → buy the ≈250 CE; exit on a close below SuperTrend'),
    Spec(6, 'ST RSI CPR BB Sensex', StRsiCprBbSxEngine, 'st', 'SENSEX', 'spot', 5, 'long', 500, '09:15',
         17, 15, 10, 5,
         dict(st_period=10, st_mult=1.5, rsi_period=14, rsi_level=70, bb_period=20, bb_mult=2.5,
              cpr_wide=0.35),
         fixed=dict(use_cpr_wide=True),
         rule='SENSEX 5-min: close above SuperTrend(10,1.5), Demark R1 and BB(20,2.5) upper, RSI(14) > 70,'
              ' CPR not wide → buy the ≈500 CE; exit on a close below SuperTrend'),
    Spec(7, 'ST RSI CPR BB WR SX2', StRsiCprBbWrSxEngine, 'st', 'SENSEX', 'spot', 5, 'long', 500, '09:15',
         15, 15, 10, 5,
         dict(st_period=10, st_mult=1.5, rsi_period=14, rsi_level=68, rsi_cap=80, bb_period=20, bb_mult=2.5,
              wr_period=14, wr_level=-10, cpr_narrow=0.2),
         fixed=dict(use_cpr_narrow=True),
         rule='SENSEX 5-min: close above SuperTrend(10,1.5), traditional R1 and BB(20,2.5) upper, RSI(14) 68-80,'
              ' %R(14) > -10, narrow CPR → buy the ≈500 CE; exit on a close below SuperTrend'),
    # 8 is the Mine group's "NIFTY - Both - 3m" tab, not one of the seven in the Simple Trader group. Bounce
    # only (a Through setup is known only at the break candle's close); stop_touch 0 = exit on a close past the
    # line. min_body_pct / outside_bar 'close' read the break candle's close, so the entry is confirmed there
    # (st_touch_break_live_engine). The numbers are the Backtest form's of 2026-10-28; `fixed` holds the
    # defaults of the two non-numeric settings (ST8_DIRECTION, ST8_OUTSIDE_BAR).
    Spec(8, 'ST Touch Break', StTouchBreakEngine, 'tb', 'NIFTY', 'spot', 3, 'long', 250, '10:00',
         0, 0, 0, 5,
         dict(st_period=7, st_mult=1.5, stop_touch=1, min_wick_pct=40.0, min_body_pct=50.0, touch_tol_pct=0.0),
         fixed=dict(direction='both', outside_bar='close'),
         rule='NIFTY 3-min from 10:00: a candle whose wick (≥ 40 % of its range) touches the SuperTrend(7,1.5) line and closes'
              ' on the trend side arms the next candle; if that candle breaks the touch high (low) and closes the way it broke'
              ' with a body ≥ 50 % of its range (an outside bar goes the way it closes) the ≈250 CE (PE) is bought at its close'
              ' (Bounce, both directions); exit when the index trades through the previous candle\'s SuperTrend line',
         tab='NIFTY - Both - 3m'),
)}


def _touch_break(n, tab, bar, st_period, st_mult, modes, stop_touch, min_wick, touch_tol, min_body, outside_bar,
                 entry_start, entry_end, exit_cutoff, max_trades, direction):
    """One more Mine sub-tab: ST Touch Break with the numbers of one saved Find Best Params row (the
    Backtest page's ★ Saved list of 2026-10-30, in its order: timeframe · ST len / factor · setups ·
    stop · wick / tol / body · outside bar · entry from / until · square-off · trades a day). Premium
    250 and no premium SL / trail, as the run priced them. Each is its own thread, flags (ST<n>_*) and
    files, paper first."""
    side = {'both': 'CE (PE)', 'long': 'CE', 'short': 'PE'}[direction]
    dirs = {'both': 'both directions', 'long': 'BUY only', 'short': 'SELL only'}[direction]
    kind = {'bounce': 'Bounce', 'through': 'Through', 'both': 'Bounce + Through'}[modes]
    return Spec(n, 'ST Touch Break', StTouchBreakEngine, 'tb', 'NIFTY', 'spot', bar,
                'short' if direction == 'short' else 'long', 250, entry_start, 0, 0, 0, max_trades,
                dict(st_period=st_period, st_mult=st_mult, stop_touch=stop_touch, min_wick_pct=float(min_wick),
                     min_body_pct=float(min_body), touch_tol_pct=float(touch_tol)),
                fixed=dict(direction=direction, outside_bar=outside_bar, modes=modes, entry_end=entry_end,
                           exit_cutoff=exit_cutoff),
                rule=f'NIFTY {bar}-min, entries {entry_start}-{entry_end}: a wick (≥ {min_wick:g} % of the range) touching'
                     f' SuperTrend({st_period},{st_mult:g}) arms the next candle; a {kind} break of the touch high (low)'
                     + (f' with a body ≥ {min_body:g} %' if min_body else '') +
                     f' buys the ≈250 {side} ({dirs}, outside bar → {outside_bar}); exit when the index'
                     f' {"trades through" if stop_touch else "closes beyond"} the previous candle\'s SuperTrend line,'
                     f' square-off {exit_cutoff}; up to {max_trades} a day',
                tab=tab)


# The rest of the Mine group — 9-15 are the Backtest page's ★ Saved rows of 2026-10-30 (the eighth,
# Both ★ Default, is 8 above). They replaced the Combined ⚡ Optimize rows of 2026-10-29 under the same
# numbers (9-16): those rows' ST9-16 flags were deleted and their simple_trader_<n>_*.json archived as
# *.removed-20261030.json, so no new row inherits an old MODE=live or trade history. "#n" is the saved
# row's own label.
#   n   tab                          bar len fac  setups     stop wick tol body outside from     until    sq-off  /day dir
for _sp in (
    _touch_break(9,  'NIFTY - Sell - 5m - #15', 5,  10, 2.5, 'through', 1, 25, 0, 0,  'first', '09:15', '14:30', '15:00', 5, 'short'),
    _touch_break(10, 'NIFTY - Sell - 5m - #9',  5,  14, 2,   'bounce',  1, 0,  0, 25, 'close', '10:30', '14:30', '15:14', 5, 'short'),
    _touch_break(11, 'NIFTY - Sell - 5m - #8',  5,  14, 2,   'bounce',  1, 25, 0, 25, 'close', '10:30', '14:30', '15:14', 5, 'short'),
    _touch_break(12, 'NIFTY - Sell - 3m - #6',  3,  7,  3,   'both',    1, 0,  0, 50, 'first', '10:00', '13:00', '15:00', 5, 'short'),
    _touch_break(13, 'NIFTY - Sell - 3m - #2',  3,  7,  3,   'both',    1, 0,  0, 50, 'first', '10:30', '13:00', '15:00', 5, 'short'),
    _touch_break(14, 'NIFTY - Buy - 3m - #8',   3,  14, 3,   'through', 0, 25, 0, 0,  'first', '10:30', '15:14', '15:14', 1, 'long'),
    _touch_break(15, 'NIFTY - Buy - 5m - #2',   5,  10, 2,   'bounce',  1, 40, 0, 50, 'first', '09:30', '15:14', '15:14', 1, 'long'),
):
    SPECS[_sp.n] = _sp

# The Mine group's sub-tabs in order: (spec number, page key, DOM prefix). 8 keeps its original key.
MINE_TABS = [(n, 'mine-nifty' if n == 8 else f'mine-{n}', 'mnn' if n == 8 else f'mn{n}')
             for n, sp in SPECS.items() if sp.kind == 'tb']


def new_day(date_str: str) -> Dict[str, Any]:
    return {'date': date_str, 'trades': [], 'last_bar': None}


def tick_up(v: float) -> float:
    """Quantman's stop prices sit on the 0.05 tick, rounded UP (cpr_option_service._premium_stop)."""
    return math.ceil(round(v / 0.05, 6)) * 0.05


def stop_level(entry: float, peak: float, sl_pct: float, tsl_x: float, tsl_y: float) -> Dict[str, Any]:
    """The premium stop for a leg bought at `entry` whose premium has peaked at `peak`:
    `sl_pct` % under the entry and, for every `tsl_x` % the premium has risen from entry, `tsl_y`
    % of entry higher — the backtest's rule (``cpr_option_service._premium_stop``)."""
    base = tick_up(entry * (1.0 - sl_pct / 100.0))
    stop = base
    if tsl_x > 0 and tsl_y > 0 and entry > 0:
        steps = int(((peak / entry - 1.0) * 100.0) // tsl_x)
        if steps > 0:
            stop = max(stop, tick_up(base + steps * tsl_y / 100.0 * entry))
    return {'stop': round(stop, 2), 'kind': 'Trailing SL' if stop > base else 'Stop Loss', 'base': round(base, 2)}


def engine_kwargs(spec: Spec, s: Dict[str, Any]) -> Dict[str, Any]:
    kw = {k: s.get(k, v) for k, v in spec.params.items()}
    kw.update(spec.fixed)
    kw.update(entry_start=s.get('entry_start', spec.entry_start), entry_end=s.get('entry_end', '15:14'),
              exit_cutoff=s.get('exit_cutoff', '15:14'), max_trades_per_day=int(s.get('max_trades', spec.max_trades)),
              direction=spec.side)
    return kw


def _r(v, nd=2):
    try:
        v = float(v)
        return round(v, nd) if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


class _Read:
    """What the last closed candle says, whichever engine family produced it."""

    def __init__(self, spec: Spec, bars: pd.DataFrame, s: Dict[str, Any]):
        eng = spec.cls(bars, **engine_kwargs(spec, s))
        sg = eng.signals()
        self.engine, self.sg, self.spec = eng, sg, spec
        i = len(eng.df) - 1
        self.i = i
        if spec.kind == 'ema':
            idx = eng.df.index
            self.ts = idx[i].strftime('%Y-%m-%d %H:%M:%S')
            self.tmin = int(idx[i].hour * 60 + idx[i].minute)
            self.close = float(sg['c'][i])
            self.rsi = float(sg['rsi'][i])
            self.side = int(sg['signal'][i])
            self.entry_start, self.entry_end = eng.entry_start, eng.entry_end
            self.exit_cutoff, self.max_trades = eng.exit_cutoff, eng.max_trades_per_day
        else:
            b = eng._base()
            self.ts = str(b['ts'][i])
            self.tmin = int(b['tmin'][i])
            self.close = float(b['c'][i])
            self.rsi = float(sg['rsi'][i])
            self.side = int(sg['signal'][i])
            self.entry_start, self.entry_end = eng.entry_start, eng.entry_end
            self.exit_cutoff, self.max_trades = eng.exit_cutoff, eng.max_trades_per_day

    def exit_reason(self, long: bool) -> Optional[str]:
        """The reason this candle's close ends an open trade of that side, else None."""
        if self.spec.kind == 'ema':
            e = self.engine
            if (self.rsi < 100.0 - e.rsi_exit) if long else (self.rsi > e.rsi_exit):
                return 'RSI Exit'
            return None
        if int(self.sg['st_dir'][self.i]) == (-1 if long else 1):
            return 'SuperTrend Down' if long else 'SuperTrend Up'
        return None

    def signal_info(self, long: bool) -> Dict[str, Any]:
        i, sg = self.i, self.sg
        if self.spec.kind == 'ema':
            return {'rsi': _r(self.rsi, 1), 'close': _r(self.close), 'supertrend': _r(sg['st_line'][i]),
                    'bb_band': _r((sg['bb_up'] if long else sg['bb_lo'])[i]),
                    'pivot': 'R1' if long else 'S1', 'pivot_level': _r(sg['cpr']['r1' if long else 's1'][i]),
                    'cci': _r(sg['cci'][i], 1), 'wr': _r(sg['wr'][i], 1), 'ema': _r(sg['ema'][i])}
        lv = sg['lv']
        out = {'rsi': _r(self.rsi, 1), 'close': _r(self.close), 'supertrend': _r(sg['st_line'][i]),
               'bb_band': _r((lv['bb_up'] if long else lv['bb_lo'])[i]),
               'pivot': 'R1' if long else 'S1', 'pivot_level': _r((lv['r1'] if long else lv['s1'])[i])}
        if 'cci' in sg:
            out['cci'] = _r(sg['cci'][i], 1)
        if 'cpr_width' in sg:
            out['cpr_width'] = _r(sg['cpr_width'][i], 3)
        return out


def on_bar(day: Dict[str, Any], bars: pd.DataFrame, s: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read the LAST candle of `bars` (every candle in it is closed). `s['spec']` is the
    strategy number. Returns events: {'event': 'entry'|'exit', 'trade': t, ...}. Mutates `day`."""
    if bars is None or bars.empty:
        return []
    spec = SPECS[int(s['spec'])]
    if spec.kind == 'tb':
        return tb.on_bar(day, bars, dict(spec.fixed, **s), spec.bar)
    rd = _Read(spec, bars, s)
    if rd.ts[:10] != day['date'] or rd.ts == day.get('last_bar'):
        return []
    day['last_bar'] = rd.ts
    bar_end = rd.tmin + spec.bar
    events: List[Dict[str, Any]] = []

    open_t = next((t for t in day['trades'] if t['status'] == 'open'), None)
    if open_t is not None:
        # Only candles from the entry candle on can end the trade, as the backtest only looks from
        # the bar it filled on.
        if rd.tmin >= open_t['entry_bar_start']:
            reason = rd.exit_reason(open_t['type'] == 'Long')
            if reason:
                events.append({'event': 'exit', 'trade': open_t, 'reason': reason,
                               'bar': rd.ts, 'index_close': rd.close})
        return events

    entered = len(day['trades'])
    if (rd.side and entered < rd.max_trades
            and rd.entry_start <= rd.tmin < rd.entry_end and bar_end < rd.exit_cutoff):
        long = rd.side == 1
        t = {'n': entered + 1, 'type': 'Long' if long else 'Short', 'status': 'open',
             'signal_bar': rd.ts, 'entry_bar_start': bar_end, 'signal': rd.signal_info(long)}
        day['trades'].append(t)
        events.append({'event': 'entry', 'trade': t, 'bar': rd.ts})
    return events


def time_exits(day: Dict[str, Any], hhmm: str, cutoff: str) -> List[Dict[str, Any]]:
    """Square off whatever is open once the clock reaches the cutoff minute."""
    if hhmm < cutoff:
        return []
    return [{'event': 'exit', 'trade': t, 'reason': 'Time Exit', 'bar': None, 'index_close': None}
            for t in day['trades'] if t['status'] == 'open']


def option_type(t: Dict[str, Any]) -> str:
    return 'CE' if t['type'] == 'Long' else 'PE'


__all__ = ['SPECS', 'Spec', 'new_day', 'on_bar', 'time_exits', 'option_type', 'stop_level', 'tick_up',
           'engine_kwargs', 'brokerage_for']
