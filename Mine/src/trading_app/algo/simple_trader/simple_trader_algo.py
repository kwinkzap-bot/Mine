"""Simple Trader 1-7 (and the Mine group, 8-15) — the Backtest page's Simple Trader group (the Quantman runs) as live
index-options algos, one independent algo per strategy.

Each strategy is its own tab, thread, state and history files, Live/Paper toggle and flags:
``ST<n>_*`` settings and ``BROKER_N_ST<n>_ACTIVE`` / ``BROKER_N_ST<n>_LOTS`` per account (n = 1-7).
The rules, indicator numbers, candles, premium and stop-loss of each are the Backtest form's
(``service/simple_trader_live_engine.SPECS``); ``signals()`` is the backtest engine's own.

This is ``algo/st_bb_rsi`` with three differences, all of them what the backtest does and
the ST + BB + RSI tab does not:

* **A premium stop-loss and trailing stop.** The leg is stopped ``ST<n>_SL_PCT`` % under its
  fill and trails up ``ST<n>_TSL_Y`` % of entry for every ``ST<n>_TSL_X`` % the premium rises
  (``simple_trader_live_engine.stop_level``). As in the backtest, "entry" is the close of the
  option's minute BEFORE the entry minute and the stop is walked on its 1-minute candles (Kite,
  every 20 s) as well as on the polls — since 2026-11-02, when the fill and 5 s polls missed a
  trail step the backtest took. Live, an SL-M for the initial stop rests at the
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
import time as _time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from trading_app.algo.st_bb_rsi import st_bb_rsi_algo as base
from trading_app.algo.st_bb_rsi.st_bb_rsi_algo import (   # noqa: F401 — the route layer calls these by name
    StBbRsiAlgo, accounts, get_instance, load_history, mode, paper_lots, _flag, _hhmm, _now, _num, _uvar)
from trading_app.app.utils.logger import logger
from trading_app.service import simple_trader_live_engine as sim
from trading_app.service import st_touch_break_live_engine as tb

_DIR = os.path.dirname(__file__)
_NEAR_PCT = 0.1                    # ST Touch Break: within this % of the touch-stop line the index is polled every second
_OPTION_MINUTES_SECS = 20         # how often an open trade re-reads its option's 1-minute candles


def _minute_before(hhmm) -> Optional[str]:
    """'09:20' → '09:19': the minute whose close the backtest takes as the entry premium."""
    try:
        return (datetime.strptime(str(hhmm)[:5], '%H:%M') - timedelta(minutes=1)).strftime('%H:%M')
    except (TypeError, ValueError):
        return None


class SimpleProfile(base.Profile):
    """A Profile whose symbol is the option underlying and whose files are per strategy."""

    def __init__(self, spec: sim.Spec):
        super().__init__(spec.symbol, f'st{spec.n}', f'ST{spec.n}', spec.premium,
                         'BFO' if spec.symbol == 'SENSEX' else 'NFO',
                         f'ST{spec.n}', 'Mine · ST Touch Break' if spec.n == 8 else
                         (f'Mine · {spec.tab}' if spec.kind == 'tb' else f'Simple Trader {spec.n}'))
        self.spec = spec
        self.rule = spec.rule

    def state_file(self) -> str:
        return os.path.join(_DIR, f'simple_trader_{self.spec.n}_state.json')

    def history_file(self) -> str:
        return os.path.join(_DIR, f'simple_trader_{self.spec.n}_trades_history.json')


PROFILES: Dict[int, SimpleProfile] = {n: SimpleProfile(sp) for n, sp in sim.SPECS.items()}


def _direction(username, p: SimpleProfile) -> str:
    """Seven strategies are one-sided by their rule. ST Touch Break (8) is the Mine group's and takes
    the tab's choice — long (BUY), short or both (the default) — read at each setup."""
    if p.spec.kind != 'tb':
        return p.spec.side
    dflt = p.spec.fixed.get('direction', p.spec.side)
    v = _uvar(username, f'{p.pfx}_DIRECTION', dflt).lower()
    return v if v in ('long', 'short', 'both') else dflt


def settings(username, p: SimpleProfile) -> Dict[str, Any]:
    """Every setting, read where used and never cached. The indicator numbers are the
    Backtest form's defaults unless ``ST<n>_<NAME>`` (e.g. ST4_RSI_LEVEL) says otherwise."""
    sp = p.spec
    s: Dict[str, Any] = {
        'spec': sp.n,
        'mode': mode(username, p),
        'premium': _num(username, f'{p.pfx}_PREMIUM', sp.premium),
        'direction': _direction(username, p),
        'max_trades': max(1, int(_num(username, f'{p.pfx}_MAX_TRADES', sp.max_trades))),
        'sl_pct': max(0.0, _num(username, f'{p.pfx}_SL_PCT', sp.sl_pct)),
        'tsl_x': max(0.0, _num(username, f'{p.pfx}_TSL_X', sp.tsl_x)),
        'tsl_y': max(0.0, _num(username, f'{p.pfx}_TSL_Y', sp.tsl_y)),
        'enabled': _uvar(username, f'{p.pfx}_ENABLED', 'true').lower() != 'false',
    }
    s['backstop_pct'] = s['sl_pct']            # the broker-side SL-M rests at the initial stop
    if sp.kind == 'tb':                        # an outside break candle: the side it closes on, or the first crossed
        v = _uvar(username, f'{p.pfx}_OUTSIDE_BAR', sp.fixed.get('outside_bar', 'first')).lower()
        s['outside_bar'] = v if v in ('close', 'first') else sp.fixed.get('outside_bar', 'first')
        # what the tab's rule line shows: candles and the entry window are the row's, not settings
        s.update(bar=sp.bar, entry_start=sp.entry_start, entry_end=sp.fixed.get('entry_end', '15:14'),
                 exit_cutoff=sp.fixed.get('exit_cutoff', '15:14'), modes=sp.fixed.get('modes', 'bounce'), tab=sp.tab)
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
        f = self.spec.fixed                 # the Mine rows carry their own entry window
        return {'entry_start': self.spec.entry_start, 'entry_end': f.get('entry_end', '15:14'),
                'exit_cutoff': f.get('exit_cutoff', '15:14')}

    def _decide(self, day, bars, s):
        return sim.on_bar(day, bars, s)

    def _clock_exits(self, day, hhmm: str, cutoff: str):
        return sim.time_exits(day, hhmm, cutoff)

    def _price_events(self, day, now, s):
        """ST Touch Break (8) is entered INTRABAR: the index price is read only while a setup's break
        candle is running (or a 'touch' stop is armed), and never for the other seven strategies."""
        if self.spec.kind != 'tb' or not tb.watching(day, _hhmm(now), s, self.spec.bar):
            return []
        spot = self._spot()
        if spot is None:
            return []
        self.state['spot'] = spot
        return tb.on_price(day, spot, _hhmm(now), s, self.spec.bar)

    def _poll_wait(self, now) -> float:
        """ST Touch Break's touch stop (and an intrabar break level) is watched on the polled index:
        within _NEAR_PCT of the line the poll runs every second, not every 5 s. On 2 Nov 2026 (#4
        Short, line 23,247.81) the index went above it only by 2 pts, late in the 13:57 minute, and the
        exit's 239 fill came at 13:57:56 — a premium that fell 249 → 239.25 inside that minute."""
        wait = super()._poll_wait(now)
        if self.spec.kind != 'tb' or not self.state:
            return wait
        spot = self.state.get('spot')
        if not spot:
            return wait
        for t in self.state['day']['trades']:
            lvl = t.get('stop_line') if t['status'] == 'open' else (
                t.get('level') if t['status'] == 'armed' and not t.get('confirm') else None)
            if lvl and abs(float(spot) - float(lvl)) <= _NEAR_PCT / 100.0 * float(spot):
                return min(wait, 1.0)
        return wait

    def _act(self, ev, now, s) -> None:
        t, kind = ev['trade'], ev['event']
        if self.spec.kind == 'tb' and kind in ('armed', 'expired', 'missed'):
            sg = t.get('signal') or {}
            up = t['type'] == 'Long'
            if kind == 'armed':
                self._note(f"#{t['n']} {t['type']}: touch candle {t['signal_bar'][11:16]} — SuperTrend {sg.get('supertrend')}, "
                           + (f"waiting for the next candle to close {'above' if up else 'below'} {t['level']:,.1f} "
                              f"(body ≥ {s.get('min_body_pct', 0):g}% of its range, the way it broke)" if t.get('confirm') else
                              f"waiting for the index to trade {'above' if up else 'below'} {t['level']:,.1f} in the next candle"))
            elif kind == 'expired':
                self._note(f"#{t['n']} {t['type']}: {t['error']} — setup dropped" if t.get('error') else
                           f"#{t['n']} {t['type']}: the {t['level']:,.1f} level was not crossed in the break candle — setup dropped")
            else:
                self._note(f"#{t['n']} {t['type']}: {t.get('error')}")
            return
        if self.spec.kind == 'tb' and kind == 'entry' and ev.get('stale'):
            # confirmed at a close a restart is only now catching up on: the break is minutes old, not chased
            t.update(status='missed', legs={}, error='the break candle closed while the algo was not watching — not chased')
            self._note(f"#{t['n']} {t['type']}: {t['error']}")
            return
        super()._act(ev, now, s)

    def _signal_text(self, t) -> str:
        if self.spec.kind != 'tb':
            return super()._signal_text(t)
        sg = t.get('signal') or {}
        return f"touch {t['signal_bar'][11:16]}, level {sg.get('level')}"

    # ── the option's own stop ──────────────────────────────────────────

    def _after_mark(self, t: Dict[str, Any], px: float) -> bool:
        legs = t.get('legs') or {}
        fill = next((l['entry'] for l in legs.values() if l.get('open') and l.get('entry')), None)
        s = self._settings()
        if fill is None or s['sl_pct'] <= 0:
            return False
        changed = False
        # The backtest's stop and trail are measured from the close of the minute BEFORE the entry
        # minute and walked on the option's 1-minute candles (cpr_option_service._premium_stop).
        # Measured instead from the fill and from 5 s polls, 2 Nov 2026 (ST4, 23050 CE) never saw
        # the 312.00 high of 10:54 (polls peaked at 310.45) and, from the 249.6 fill, 312 was one
        # 13 % step, not two: the backtest's trail at 264.60 took it out at 11:43, live rode to the
        # SuperTrend exit at 255.25. So the same candles are read here, as the backtest reads them.
        bars = self._option_minutes(t)
        if t.get('ref_entry') is None and bars:
            before = _minute_before(t.get('entry_time'))
            ref = next((b['close'] for b in bars if b['time'] == before), None)
            if ref:
                t['ref_entry'] = round(float(ref), 2)
                changed = True
        entry = float(t.get('ref_entry') or fill)
        st, hit = self._candle_stop(t, bars, entry, s)
        # The stop is read against the peak BEFORE this poll's price, as the backtest checks a
        # bar's low against the stop the earlier bars' highs set, then lets this bar's high lift it.
        peak = max(float(t.get('peak') or entry), float(entry))
        polled = sim.stop_level(entry, peak, s['sl_pct'], s['tsl_x'], s['tsl_y'])
        if polled['stop'] > st['stop']:
            st = polled
        if t.get('stop') != st['stop']:
            t['stop'] = st['stop']
            changed = True
        if hit:
            # a closed minute's low went through the stop between two polls: the backtest's exit,
            # taken now at market
            self._note(f"#{t['n']} {t['type']}: the {hit['time']} candle's low reached the {hit['kind']} at "
                       f"{hit['price']} (entry {entry}) — exiting")
            self._leave(t, {'reason': hit['kind'], 'index_close': None}, _now())
            return True
        if px <= st['stop']:
            self._note(f"#{t['n']} {t['type']}: premium {px} through the {st['kind']} at {st['stop']} "
                       f"(entry {entry}, peak {peak}) — exiting")
            self._leave(t, {'reason': st['kind'], 'index_close': None}, _now())
            return True
        if px > peak:
            t['peak'] = px
            nxt = sim.stop_level(entry, px, s['sl_pct'], s['tsl_x'], s['tsl_y'])
            if nxt['stop'] > t['stop']:
                t['stop'] = nxt['stop']
            changed = True
        return changed

    def _candle_stop(self, t, bars, entry: float, s) -> tuple:
        """(stop, hit) from the option's closed 1-minute candles since the entry minute, walked
        exactly as the backtest walks them: hit is the first candle whose low reached the stop
        the earlier candles' highs had set, else None."""
        base_st = sim.stop_level(entry, entry, s['sl_pct'], s['tsl_x'], s['tsl_y'])
        before = _minute_before(t.get('entry_time'))
        if not bars or not before:
            return base_st, None
        from trading_app.service.cpr_option_service import _premium_stop
        carry: Dict[str, float] = {}
        hit = _premium_stop(bars, entry, before, '23:59', s['sl_pct'], s['tsl_x'], s['tsl_y'], carry=carry)
        if hit:
            return {'stop': hit['price'], 'kind': hit['kind'], 'base': base_st['base']}, hit
        st = round(float(carry.get('stop') or base_st['stop']), 2)
        peak = max([float(b['high']) for b in bars if b['time'] > before] or [entry])
        if peak > float(t.get('peak') or 0):
            t['peak'] = peak
        return {'stop': st, 'kind': 'Trailing SL' if st > base_st['base'] else 'Stop Loss',
                'base': base_st['base']}, None

    def _option_minutes(self, t) -> List[Dict[str, Any]]:
        """Today's CLOSED 1-minute candles of the trade's option, oldest first, as
        {'time': 'HH:MM', open, high, low, close} — from Kite, at most every 20 s per trade.
        [] when Kite cannot answer (the poll-only trail then still runs)."""
        opt = t.get('option') or {}
        if not opt.get('strike'):
            return []
        key = (t.get('n'), opt['strike'], opt['type'])
        cache = self.__dict__.setdefault('_om_cache', {})
        hit = cache.get(key)
        if hit and _time.time() - hit[0] < _OPTION_MINUTES_SECS:
            return hit[1]
        bars: List[Dict[str, Any]] = []
        kite = self._kite()
        if kite:
            try:
                token = opt.get('token')
                if not token:
                    from trading_app.service.kite_order_services import KiteService
                    sym = KiteService(kite_instance=kite).get_option_symbol(self.p.symbol, int(opt['strike']), opt['type'])
                    k = f"{'BFO' if self.p.symbol == 'SENSEX' else 'NFO'}:{sym}"
                    token = ((kite.ltp([k]) or {}).get(k) or {}).get('instrument_token') if sym else None
                    if token:
                        opt['token'] = int(token)
                if token:
                    now = _now()
                    raw = kite.historical_data(int(token), now.strftime('%Y-%m-%d 09:15:00'),
                                               now.strftime('%Y-%m-%d %H:%M:%S'), 'minute') or []
                    cut = _hhmm(now)                         # the minute still forming is not a candle yet
                    for b in raw:
                        hm = b['date'].strftime('%H:%M')
                        if hm < cut:
                            bars.append({'time': hm, 'open': float(b['open']), 'high': float(b['high']),
                                         'low': float(b['low']), 'close': float(b['close'])})
            except Exception as e:
                logger.debug(f'[{self.p.tag}] option minutes unavailable: {e}')
        cache[key] = (_time.time(), bars)
        return bars

    def _pick_strike(self, t, opt_type, s):
        """The backtest picks the strike nearest the premium on each strike's close of the minute
        BEFORE the entry minute (attach_option_legs → legs_at → pick_strike); the live ladder is a
        second later. On a near-tie that second flips it: 3 Nov 2026 (ST6, ≈500) the 09:19 closes
        had 76000 CE at 526.60 nearer than 76100 CE at 470.15, but at 09:20:01 76100 quoted 473.65
        and live bought it while the backtest bought 76000. So the live pick and its neighbour across
        the target are settled on those same minute closes. An intrabar entry (ST Touch Break with
        no close confirmation) is priced on the forming minute and keeps the live pick."""
        pick = super()._pick_strike(t, opt_type, s)
        if not pick or (self.spec.kind == 'tb' and not t.get('confirm')):
            return pick
        step = self._chain_step()
        cheaper = step if opt_type == 'CE' else -step
        other = pick['strike'] + (cheaper if pick['premium'] > s['premium'] else -cheaper)
        before = _minute_before(t.get('entry_time'))
        closes = self._minute_closes([pick['strike'], other], opt_type, before) if before else {}
        if len(closes) < 2:
            return pick
        best = min(closes, key=lambda k: abs(closes[k] - s['premium']))
        if best != pick['strike']:
            ladder = getattr(self, '_last_ladder', None) or {}
            quoted = ladder.get(best) or self._premium(best, opt_type)
            if quoted:
                self._note(f"#{t['n']}: {before} closes {pick['strike']} {closes[pick['strike']]} / "
                           f"{best} {closes[best]} — {best} {opt_type} is the ≈{s['premium']:g} the backtest takes")
                return {'strike': best, 'premium': quoted}
        return pick

    def _minute_closes(self, strikes, opt_type, hhmm, timeout: float = 1.5) -> Dict[int, float]:
        """{strike: close of today's ``hhmm`` 1-minute candle} from Kite, the strikes read in
        parallel and given up after ``timeout`` s (the order is waiting). Missing ones are left out."""
        kite = self._kite()
        if not kite:
            return {}
        try:
            from concurrent.futures import ThreadPoolExecutor, wait
            from trading_app.service.kite_order_services import KiteService
            svc = KiteService(kite_instance=kite)
            ex = 'BFO' if self.p.symbol == 'SENSEX' else 'NFO'
            keys = {}
            for k in strikes:
                sym = svc.get_option_symbol(self.p.symbol, int(k), opt_type)
                if sym:
                    keys[f'{ex}:{sym}'] = int(k)
            got = (kite.ltp(list(keys)) or {}) if keys else {}
            tokens = {k: (got.get(key) or {}).get('instrument_token') for key, k in keys.items()}
            day = _now().strftime('%Y-%m-%d')

            def close(token):
                raw = kite.historical_data(int(token), f'{day} {hhmm}:00', f'{day} {hhmm}:59', 'minute') or []
                hit = [b for b in raw if b['date'].strftime('%H:%M') == hhmm]
                return float(hit[0]['close']) if hit else None

            pool = ThreadPoolExecutor(max_workers=len(tokens) or 1)
            futs = {k: pool.submit(close, tok) for k, tok in tokens.items() if tok}
            wait(list(futs.values()), timeout=timeout)
            pool.shutdown(wait=False)
            out = {}
            for k, f in futs.items():
                if f.done() and not f.exception() and f.result():
                    out[k] = f.result()
            return out
        except Exception as e:
            logger.debug(f'[{self.p.tag}] option minute closes unavailable: {e}')
            return {}

    def _enter(self, t, now, s) -> None:
        super()._enter(t, now, s)
        # 4-5 decide on the NIFTY FUTURE, and the backtest's "Entry Price" is the future's (the signal
        # candle's close); the strike was picked off the index above, which is what the option prices on.
        if self.spec.data == 'fut' and t.get('index_entry') is not None:
            t['spot_entry'] = t['index_entry']
            t['index_entry'] = t['signal']['close']

    def _leave(self, t, ev, now) -> None:
        super()._leave(t, ev, now)
        if self.spec.data == 'fut':
            t['spot_exit'] = t.get('index_exit')
            t['index_exit'] = ev.get('index_close') or self._future_px() or t.get('index_exit')

    def _future_px(self) -> Optional[float]:
        """The front NIFTY future's last closed minute, for an exit no candle close decided."""
        try:
            from trading_app.service import multichart_service as mc
            rows = self._history_rows(mc.algo_provider(f'algo_{self.p.key}', self.username), _now().date())
            return round(float(rows[-1]['close']), 2) if rows else None
        except Exception as e:
            logger.debug(f'[{self.p.tag}] future price unavailable: {e}')
            return None

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
