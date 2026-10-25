"""Simple Trader 1-7 — the Backtest page's Simple Trader group (the Quantman runs) as live
index-options algos, one independent algo per strategy.

Each strategy is its own tab, thread, state and history files, Live/Paper toggle and flags:
``ST<n>_*`` settings and ``BROKER_N_ST<n>_ACTIVE`` / ``BROKER_N_ST<n>_LOTS`` per account (n = 1-7).
The rules, indicator numbers, candles, premium and stop-loss of each are the Backtest form's
(``service/simple_trader_live_engine.SPECS``); ``signals()`` is the backtest engine's own.

This is ``algo/st_bb_rsi`` with three differences, all of them what the backtest does and
the ST + BB + RSI tab does not:

* **A premium stop-loss and trailing stop.** The leg is stopped ``ST<n>_SL_PCT`` % under its
  fill and trails up ``ST<n>_TSL_Y`` % of entry for every ``ST<n>_TSL_X`` % the premium rises
  (``simple_trader_live_engine.stop_level``). Live, an SL-M for the initial stop rests at the
  broker (the floor if this app is down); the trail is watched here every poll and exits at
  market. After a stop the next candle that still signals re-enters, up to ``ST<n>_MAX_TRADES``.
* **Other candles.** 3- or 5-minute bars of the index (SENSEX for 6-7) or of the NIFTY FUTURE
  (4-5, as the backtest reads "Fields: Future").
* **Other exits.** SuperTrend against the trade (1, 3-7) or RSI back across 45 (2).

Everything about real money is the ST + BB + RSI algo's: paper by default (``ST<n>_MODE``,
read at each entry), zerodha/fyers only, an account without ``BROKER_N_ST<n>_LOTS`` is refused,
the books are the broker's fills. Paper day 1 of every strategy is the proof of the path.
"""

import os
from datetime import date
from typing import Any, Dict, List

from trading_app.algo.st_bb_rsi import st_bb_rsi_algo as base
from trading_app.algo.st_bb_rsi.st_bb_rsi_algo import (   # noqa: F401 — the route layer calls these by name
    StBbRsiAlgo, accounts, get_instance, load_history, mode, paper_lots, _flag, _hhmm, _now, _num, _uvar)
from trading_app.app.utils.logger import logger
from trading_app.service import simple_trader_live_engine as sim

_DIR = os.path.dirname(__file__)


class SimpleProfile(base.Profile):
    """A Profile whose symbol is the option underlying and whose files are per strategy."""

    def __init__(self, spec: sim.Spec):
        super().__init__(spec.symbol, f'st{spec.n}', f'ST{spec.n}', spec.premium,
                         'BFO' if spec.symbol == 'SENSEX' else 'NFO',
                         f'ST{spec.n}', f'Simple Trader {spec.n}')
        self.spec = spec
        self.rule = spec.rule

    def state_file(self) -> str:
        return os.path.join(_DIR, f'simple_trader_{self.spec.n}_state.json')

    def history_file(self) -> str:
        return os.path.join(_DIR, f'simple_trader_{self.spec.n}_trades_history.json')


PROFILES: Dict[int, SimpleProfile] = {n: SimpleProfile(sp) for n, sp in sim.SPECS.items()}


def settings(username, p: SimpleProfile) -> Dict[str, Any]:
    """Every setting, read where used and never cached. The indicator numbers are the
    Backtest form's defaults unless ``ST<n>_<NAME>`` (e.g. ST4_RSI_LEVEL) says otherwise."""
    sp = p.spec
    s: Dict[str, Any] = {
        'spec': sp.n,
        'mode': mode(username, p),
        'premium': _num(username, f'{p.pfx}_PREMIUM', sp.premium),
        'direction': sp.side,
        'max_trades': max(1, int(_num(username, f'{p.pfx}_MAX_TRADES', sp.max_trades))),
        'sl_pct': max(0.0, _num(username, f'{p.pfx}_SL_PCT', sp.sl_pct)),
        'tsl_x': max(0.0, _num(username, f'{p.pfx}_TSL_X', sp.tsl_x)),
        'tsl_y': max(0.0, _num(username, f'{p.pfx}_TSL_Y', sp.tsl_y)),
        'enabled': _uvar(username, f'{p.pfx}_ENABLED', 'true').lower() != 'false',
    }
    s['backstop_pct'] = s['sl_pct']            # the broker-side SL-M rests at the initial stop
    for k, v in sp.params.items():
        n = _num(username, f'{p.pfx}_{k.upper()}', v)
        s[k] = int(n) if isinstance(v, int) and not isinstance(v, bool) else n
    return s


class SimpleTraderAlgo(StBbRsiAlgo):
    """One thread per user per strategy."""

    def __init__(self, username: str, profile: SimpleProfile):
        super().__init__(username, profile)
        self.spec = profile.spec

    # ── the hooks ──────────────────────────────────────────────────────

    def _history_rows(self, adapter, today: date) -> List[Dict[str, Any]]:
        if self.spec.data != 'fut':
            return super()._history_rows(adapter, today)
        from datetime import timedelta
        from trading_app.service import multichart_service as mc
        sym = mc.future_symbol(adapter, self.spec.symbol)          # the front-month NIFTY future
        if not sym:
            logger.warning(f'[{self.p.tag}] no futures contract resolved for {self.spec.symbol}')
            return []
        return adapter.historical_data(sym, (today - timedelta(days=base._PRIOR_DAYS)).isoformat(),
                                       today.isoformat(), 'minute', use_cache=False) or []

    def _bar_minutes(self) -> int:
        return self.spec.bar

    def _settings(self) -> Dict[str, Any]:
        return settings(self.username, self.p)

    def _window(self) -> Dict[str, str]:
        return {'entry_start': self.spec.entry_start, 'entry_end': '15:14', 'exit_cutoff': '15:14'}

    def _decide(self, day, bars, s):
        return sim.on_bar(day, bars, s)

    def _clock_exits(self, day, hhmm: str, cutoff: str):
        return sim.time_exits(day, hhmm, cutoff)

    # ── the option's own stop ──────────────────────────────────────────

    def _after_mark(self, t: Dict[str, Any], px: float) -> bool:
        legs = t.get('legs') or {}
        entry = next((l['entry'] for l in legs.values() if l.get('open') and l.get('entry')), None)
        s = self._settings()
        if entry is None or s['sl_pct'] <= 0:
            return False
        # The stop is read against the peak BEFORE this poll's price, as the backtest checks a
        # bar's low against the stop the earlier bars' highs set, then lets this bar's high lift it.
        peak = max(float(t.get('peak') or entry), float(entry))
        st = sim.stop_level(entry, peak, s['sl_pct'], s['tsl_x'], s['tsl_y'])
        changed = False
        if t.get('stop') != st['stop']:
            t['stop'] = st['stop']
            changed = True
        if px <= st['stop']:
            self._note(f"#{t['n']} {t['type']}: premium {px} through the {st['kind']} at {st['stop']} "
                       f"(entry {entry}, peak {peak}) — exiting")
            self._leave(t, {'reason': st['kind'], 'index_close': None}, _now())
            return True
        if px > peak:
            t['peak'] = px
            nxt = sim.stop_level(entry, px, s['sl_pct'], s['tsl_x'], s['tsl_y'])
            t['stop'] = nxt['stop']
            changed = True
        return changed

    def _book_if_settled(self, t) -> bool:
        # A live leg whose broker SL-M filled leaves the trade 'open' with every leg flat: that is
        # the stop-loss, and the trade is over so the next candle's signal can re-enter.
        if t['status'] == 'open' and self._all_flat(t) and 'paper' not in (t.get('legs') or {}):
            t.update(status='closed', result=t.get('result') or 'Stop Loss', exit_time=_hhmm(_now()),
                     index_exit=self._spot() or t.get('index_exit'), pending_entry=False)
            self._note(f"#{t['n']}: flat at the broker (stop-loss) — the trade is over")
        return super()._book_if_settled(t)


def ensure_running(username: str, source: str = '', p: SimpleProfile = None) -> bool:
    """Start the thread for the session if it is enabled and not running."""
    if not settings(username, p)['enabled']:
        return False
    now = _now()
    mins = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (9 * 60 <= mins < base._HARD_STOP_MIN):
        return False
    inst = get_instance(username, p)
    if inst and inst.is_running():
        return False
    logger.info(f'[{p.tag} {source}] starting thread for {username}')
    SimpleTraderAlgo(username, p).start()
    return True
