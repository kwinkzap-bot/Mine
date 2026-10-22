"""CPR Logic — the /cpr-logic rule as a live NIFTY options algo.

The rule is ``service/cpr_trade_rule.py``, the same code the sheet on
/cpr-logic is replayed with, driven one closed 5-minute candle at a time by
``service/cpr_live_engine.py`` (which owns every decision and is where the
backtest-vs-live arithmetic lives). This module is the thread around it: the
clock, the prices, the option leg, the orders and the record.

**Paper by default** — ``CPR_LOGIC_MODE=paper``. Every setup, fill and exit is
recorded at the option's real premium at that moment and no order is sent.
``CPR_LOGIC_MODE=live`` (the Live/Paper toggle on the Algo tab, read at each
entry) sends real orders at every account carrying
``BROKER_N_CPR_ACTIVE=true`` — zerodha/fyers only, as for the Telegram calls.

**The trade is on the index; the order is an option.** The entry, stop and
target are NIFTY levels, watched here on the index price. When the index
crosses the entry, the ≈``CPR_PREMIUM`` (default 200) strike of the front
weekly expiry is BOUGHT — CE for a BUY setup, PE for a SELL — at market. When
the index reaches the stop, or the 5-minute close settles the target, or it is
15:15, that option is sold at market. Live, an SL-M also rests at the broker
on the premium, ``CPR_BACKSTOP_X`` (1.5) × the index risk below the fill — not
the exit, which is the index's, but the floor if this app is down when the
market runs (0 = none). It is set that far on purpose: on 13 Oct 2026 it sat at
0.75× and an in-the-money CE, which moves point for point with NIFTY and
loses time value besides, reached it at 11:15 while NIFTY was still well clear
of its stop — the broker sold at 148.05, and the trade then ran to +175 pts.

**The books are the broker's.** A live leg's entry and exit prices are its
real average fills, read back from the order book (``leg_fills``) — not the
quote at the moment. A leg the broker closes on its own (the backstop firing,
a square-off by hand) is seen on the next sweep and booked at that fill. A
trade is written to history only once every leg's fills are in (or 2 minutes
after its exit, flagged).

**Sizing** — ``CPR_LOT_MODE``: ``fixed`` trades ``BROKER_N_CPR_LOTS`` per
account (``CPR_LOTS`` in paper, default 1); ``compound`` trades one lot per
``CPR_PER_LOT`` (₹20,000) of ``CPR_START_CAPITAL`` (₹20,000, so it starts
at 1 lot) plus the mode's booked P&L, never over ``CPR_MAX_LOTS`` (27, the
freeze limit) — the Performance popup's Compound lots, run forward.

State is ``cpr_state.json`` (today, rebuilt after a restart) and every
closed trade is a row in ``cpr_trades_history.json``.
"""

import json
import os
import threading
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from trading_app.app.utils.logger import logger
from trading_app.service import cpr_backtest_service as svc
from trading_app.service import cpr_live_engine as eng

_DIR = os.path.dirname(__file__)
STATE_FILE = os.path.join(_DIR, 'cpr_state.json')
HISTORY_FILE = os.path.join(_DIR, 'cpr_trades_history.json')

_IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = 'NIFTY'
_POLL_SECS = 5                       # a price poll, only while a trade is armed or open (Fyers' 200/min budget)
_BAR_LAG = timedelta(minutes=5, seconds=5)    # a candle is read 5 s after it closes, once the feed has it
_OPEN_MIN = 9 * 60 + 15
_HARD_STOP_MIN = 15 * 60 + 30
_DAILY_SINCE = date(2017, 7, 1)      # the virgin CPRs look back over every session since
_PRIOR_DAYS = 45                     # 5-minute history for the first-candle average (20 sessions)
_STOPPABLE = ('zerodha', 'kite', 'fyers')
BACKSTOP_X = 1.5                     # premium SL-M: fill - CPR_BACKSTOP_X (this) x the index risk; 0 = none
_SYNC_SECS = 15                      # how often a live leg's fills / broker-side close are read back
_BOOK_WAIT_SECS = 120                # a fill the order book never reports: book on what is known

_instances: Dict[str, 'CPRAlgo'] = {}


def get_instance(username: str) -> Optional['CPRAlgo']:
    return _instances.get(username)


def _now() -> datetime:
    return datetime.now(_IST)


def _hhmm(dt: datetime) -> str:
    return dt.strftime('%H:%M')


# ── settings (read where used, never cached) ──────────────────────────────

def _uvar(username, name, default=''):
    from trading_app.app.utils.user_env import UserEnvManager
    v = UserEnvManager.get_user_var(username, name, default)
    return default if v in (None, '') else str(v).strip()


def _flag(username, name, default='false') -> bool:
    return _uvar(username, name, default).lower() in ('true', '1', 'yes')


def _num(username, name, default: float) -> float:
    try:
        return float(_uvar(username, name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def mode(username) -> str:
    return 'live' if _uvar(username, 'CPR_LOGIC_MODE', 'paper').lower() == 'live' else 'paper'


def lot_mode(username) -> str:
    return 'compound' if _uvar(username, 'CPR_LOT_MODE', 'fixed').lower() == 'compound' else 'fixed'


def settings(username) -> Dict[str, Any]:
    return {
        'mode': mode(username),
        'lot_mode': lot_mode(username),
        'lots': int(_num(username, 'CPR_LOTS', 1)),
        'start_capital': _num(username, 'CPR_START_CAPITAL', 20000),     # 1 lot to start
        'per_lot': _num(username, 'CPR_PER_LOT', 20000),
        'max_lots': int(_num(username, 'CPR_MAX_LOTS', 27)),
        'premium': _num(username, 'CPR_PREMIUM', 200),
        'enabled': _uvar(username, 'CPR_LOGIC_ENABLED', 'true').lower() != 'false',
    }


def cpr_accounts(username) -> List[Dict[str, Any]]:
    """The accounts a live entry reaches: typed, active, BROKER_N_CPR_ACTIVE,
    and a broker this app can hold a stop at. ``lots`` is BROKER_N_CPR_LOTS
    (None when unset — such an account is refused, never guessed)."""
    from trading_app.app.routes.api import is_broker_active
    out = []
    for i in range(1, 21):
        b_type = _uvar(username, f'BROKER_{i}_TYPE').lower()
        if not b_type or not is_broker_active(username, i):
            continue
        if not _flag(username, f'BROKER_{i}_CPR_ACTIVE'):
            continue
        try:
            lots = int(_uvar(username, f'BROKER_{i}_CPR_LOTS')) or None
        except (TypeError, ValueError):
            lots = None
        out.append({'instance': i, 'type': b_type, 'lots': lots,
                    'name': _uvar(username, f'BROKER_{i}_NAME', f'{b_type} {i}'),
                    'stoppable': b_type in _STOPPABLE})
    return out


# ── the record ────────────────────────────────────────────────────────────

def load_history() -> List[Dict[str, Any]]:
    try:
        with open(HISTORY_FILE) as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _append_history(row: Dict[str, Any]) -> None:
    rows = load_history()
    rows.append(row)
    tmp = HISTORY_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(rows, f, indent=2, default=str)
    os.replace(tmp, HISTORY_FILE)


def booked_pnl(mode_name: str) -> float:
    return sum(float(r.get('pnl') or 0) for r in load_history() if r.get('mode') == mode_name)


def lots_for_trade(username, mode_name: str, fixed_lots: int) -> int:
    s = settings(username)
    if s['lot_mode'] == 'compound':
        return eng.compound_lots(s['start_capital'], booked_pnl(mode_name), s['per_lot'], s['max_lots'])
    return max(1, int(fixed_lots or 1))


class CPRAlgo:
    """One thread per user; one NIFTY day at a time."""

    def __init__(self, username: str):
        self.username = username
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._ctx: Optional[Dict[str, Any]] = None       # today's history + chart
        self._last_bar: Optional[str] = None
        self.state: Dict[str, Any] = {}

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='CPRAlgoThread')
        self._thread.start()
        _instances[self.username] = self
        logger.info('[CPR] thread started')

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop(self.username, None)
        logger.info('[CPR] stop requested')

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── state ───────────────────────────────────────────────────────────

    def _load_state(self, today: str) -> Dict[str, Any]:
        try:
            with open(STATE_FILE) as f:
                st = json.load(f)
            if st.get('day', {}).get('date') == today:
                return st
        except Exception:
            pass
        return {'day': eng.new_day(today), 'last_bar': None, 'log': [], 'chart': None}

    def _save_state(self) -> None:
        with self._lock:
            tmp = STATE_FILE + '.tmp'
            try:
                with open(tmp, 'w') as f:
                    json.dump(self.state, f, indent=2, default=str)
                os.replace(tmp, STATE_FILE)
            except Exception as e:
                logger.error(f'[CPR] state save failed: {e}')

    def _note(self, text: str) -> None:
        logger.info(f'[CPR] {text}')
        self.state.setdefault('log', []).append({'t': _hhmm(_now()), 'text': text})
        self.state['log'] = self.state['log'][-200:]

    # ── market data (overridable in tests) ──────────────────────────────

    def _provider(self):
        from trading_app.service import multichart_service as mc
        return mc.algo_provider(), mc.resolve_symbol(SYMBOL)

    def _daily_history(self, today: date) -> List[Dict[str, Any]]:
        """Every complete session since _DAILY_SINCE, in year-sized requests
        (one long daily request comes back truncated)."""
        adapter, fy = self._provider()
        raw: List[Dict[str, Any]] = []
        start = _DAILY_SINCE
        while start < today:
            end = min(start + timedelta(days=364), today - timedelta(days=1))
            raw += adapter.historical_data(fy, start.isoformat(), end.isoformat(), 'day',
                                           use_cache=True, cache_ttl=6 * 3600.0) or []
            start = end + timedelta(days=1)
        rows = {b['date']: b for b in svc._daily_rows(raw) if b['date'] < today}
        return [rows[k] for k in sorted(rows)]

    def _prior_intraday(self, today: date) -> Dict[str, List[Dict[str, Any]]]:
        adapter, fy = self._provider()
        raw = adapter.historical_data(fy, (today - timedelta(days=_PRIOR_DAYS)).isoformat(),
                                      (today - timedelta(days=1)).isoformat(), '5minute',
                                      use_cache=True, cache_ttl=6 * 3600.0)
        return svc._intraday_by_session(raw or [])

    def _today_bars(self, today: date, now: datetime) -> List[Dict[str, Any]]:
        """Today's CLOSED 5-minute candles — the one still forming is dropped."""
        adapter, fy = self._provider()
        raw = adapter.historical_data(fy, today.isoformat(), today.isoformat(), '5minute',
                                      use_cache=False)
        bars = svc._intraday_by_session(raw or []).get(today.isoformat()) or []
        cut = _hhmm(now - _BAR_LAG)
        return [b for b in bars if b['time'] <= cut]

    def _kite(self):
        """The Kite session the orders go through — the first flagged Zerodha account's,
        else instance 1. Strike pick and booked premium stay on the broker that fills:
        the data provider (Fyers over its daily cap) served stale quotes — 2026-10-22 a
        23100 PE was picked at "≈204" and filled at 481, against the ≈200 the sheet buys."""
        try:
            from trading_app.app.routes.api import get_kite
            for a in cpr_accounts(self.username):
                if a['type'] in ('zerodha', 'kite'):
                    k = get_kite(user=self.username, instance=a['instance'])
                    if k:
                        return k
            return get_kite(user=self.username, instance=1)
        except Exception as e:
            logger.debug(f'[CPR] no Kite session: {e}')
            return None

    def _spot(self) -> Optional[float]:
        from trading_app.service.strategy_signal_service import SYMBOL_CONFIG
        kite = self._kite()
        key = (SYMBOL_CONFIG.get(SYMBOL) or {}).get('kite_key')
        if kite and key:
            try:
                v = float(((kite.ltp([key]) or {}).get(key) or {}).get('last_price') or 0)
                if v:
                    return v
            except Exception as e:
                logger.debug(f'[CPR] Kite spot unavailable: {e}')
        from trading_app.app.routes.order_placement_api import _spot
        try:
            v = _spot(SYMBOL)
            return float(v) if v else None
        except Exception as e:
            logger.debug(f'[CPR] spot unavailable: {e}')
            return None

    def _kite_premiums(self, strikes: List[int], option_type: str) -> Dict[int, float]:
        """One Kite ltp call for a whole ladder of strikes: {strike: last price}.
        Empty when Kite cannot answer."""
        kite = self._kite()
        if not kite:
            return {}
        try:
            from trading_app.service.kite_order_services import KiteService
            ks = KiteService(kite_instance=kite)
            keys = {}
            for k in strikes:
                sym = ks.get_option_symbol(SYMBOL, k, option_type)
                if sym:
                    keys[f'NFO:{sym}'] = k
            if not keys:
                return {}
            got = kite.ltp(list(keys)) or {}
            return {k: float(got[key]['last_price']) for key, k in keys.items()
                    if float((got.get(key) or {}).get('last_price') or 0) > 0}
        except Exception as e:
            logger.warning(f'[CPR] Kite option quotes unavailable: {e}')
            return {}

    def _premium(self, strike: int, option_type: str) -> Optional[float]:
        got = self._kite_premiums([int(strike)], option_type)
        if got:
            return got[int(strike)]
        from trading_app.app.routes.order_placement_api import option_ltp
        try:
            v = option_ltp(SYMBOL, int(strike), option_type)
            return float(v) if v else None
        except Exception:
            return None

    def _chain_step(self) -> int:
        from trading_app.app.routes.order_placement_api import _chain_meta
        try:
            return int((_chain_meta(SYMBOL) or {}).get('step') or 50)
        except Exception:
            return 50

    # ── the day's chart ─────────────────────────────────────────────────

    def _chart(self, today: date, bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The sheet's chart for today — every level from earlier sessions,
        the first-candle reading from today's 09:15, built by the very
        function the sheet uses (svc.analyse). Today's daily row carries the
        open; the rule reads nothing else of it."""
        if self._ctx is None or self._ctx.get('date') != today.isoformat():
            self._ctx = {'date': today.isoformat(), 'daily': self._daily_history(today),
                         'prior': self._prior_intraday(today), 'chart': None}
        if self._ctx['chart'] is None and bars:
            row = {'date': today, 'open': bars[0]['open'], 'high': max(b['high'] for b in bars),
                   'low': min(b['low'] for b in bars), 'close': bars[-1]['close']}
            res = svc.analyse([{'date': today.isoformat()}], self._ctx['daily'] + [row],
                              dict(self._ctx['prior'], **{today.isoformat(): bars}))
            chart = (res['rows'] or [{}])[0].get('chart')
            if not chart:
                logger.error(f"[CPR] no chart for {today}: {(res['rows'] or [{}])[0].get('error')}")
                return None
            self._ctx['chart'] = chart
            lv = chart['levels']
            self._note(f"levels: CPR {lv['bc']:,.0f}-{lv['tc']:,.0f} ({chart['cpr_type']}), "
                       f"PDH {lv['pdh']:,.0f} R1 {lv['r1']:,.0f} · PDL {lv['pdl']:,.0f} S1 {lv['s1']:,.0f}, "
                       f"boxes {chart['boxes']}, 09:15 {chart['first_candle'].get('colour')}")
            self.state['chart'] = {'levels': lv, 'cpr_type': chart['cpr_type'], 'boxes': chart['boxes'],
                                   'weekly': chart.get('weekly'), 'first_candle': chart['first_candle']}
        return self._ctx['chart']

    # ── the loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                now = _now()
                mins = now.hour * 60 + now.minute
                if now.weekday() >= 5 or mins >= _HARD_STOP_MIN:
                    if self.state:
                        self._note('session over — thread exits')
                        self._save_state()
                    break
                if mins < _OPEN_MIN + 5:            # the 09:15 candle closes at 09:20
                    self._stop_event.wait(10)
                    continue
                try:
                    self.tick(now)
                except Exception as e:
                    logger.error(f'[CPR] tick failed: {e}', exc_info=True)
                self._stop_event.wait(_POLL_SECS)
        finally:
            _instances.pop(self.username, None)

    def tick(self, now: datetime) -> None:
        today = now.date()
        if not self.state or self.state['day']['date'] != today.isoformat():
            self.state = self._load_state(today.isoformat())
        day = self.state['day']
        if day.get('done') and not self._open_legs():
            return
        events: List[Dict[str, Any]] = []

        # Closed candles first: a candle that closed since the last look is
        # settled before the price poll is read against it.
        last = self.state.get('last_bar')
        if last is None or _hhmm(now - _BAR_LAG) >= self._next_bar(last):
            bars = self._today_bars(today, now)
            if bars:
                chart = self._chart(today, bars)
                if chart:
                    for k in range(len(bars)):
                        if last is not None and bars[k]['time'] <= last:
                            continue
                        stale = k < len(bars) - 1      # not the candle that just closed
                        for ev in eng.on_bar(day, chart, bars[:k + 1]):
                            ev['stale'] = stale
                            events.append(ev)
                        self.state['last_bar'] = last = bars[k]['time']

        # The price is only worth a request while something is waiting on it;
        # the rest of the day the closed candle is all the rule reads.
        live = any(t['status'] in ('armed', 'open') for t in day['trades'])
        spot = self._spot() if live or any(ev['event'] == 'armed' for ev in events) else None
        if spot is not None:
            self.state['spot'] = spot
            events += eng.on_price(day, spot, _hhmm(now))

        for ev in events:
            self._act(ev, now)
        synced = self._sync_live_legs(now)
        if events or synced:
            self._save_state()
        else:
            self.state['updated'] = _hhmm(now)

    @staticmethod
    def _next_bar(hhmm: str) -> str:
        h, m = int(hhmm[:2]), int(hhmm[3:]) + 5
        return f'{h + m // 60:02d}:{m % 60:02d}'

    def _open_legs(self) -> bool:
        return any(leg.get('open') or (not t.get('booked') and t.get('option'))
                   for t in self.state['day']['trades'] for leg in (t.get('legs') or {}).values())

    # ── the broker's side of a live leg ─────────────────────────────────

    def _fills(self, record_id) -> Optional[tuple]:
        """(status, avg, qty) of one stored order at its broker, or None."""
        if not record_id:
            return None
        from trading_app.app.routes.api import leg_fills
        from trading_app.app.order_placement.op_signal_engine import _session
        from trading_app.app.utils.mine_order_store import MineOrderStore
        rec = MineOrderStore.get_order(record_id)
        if not rec:
            return None
        try:
            rows = leg_fills(rec, self.username, _session(self.username))
        except Exception as e:
            logger.debug(f'[CPR] fills of {record_id} unavailable: {e}')
            return None
        if not rows:
            return None
        status = 'OPEN' if any(r[0] == 'OPEN' for r in rows.values()) else next(iter(rows.values()))[0]
        qty = sum(int(r[2] or 0) for r in rows.values())
        px = [r[1] for r in rows.values() if r[1]]
        return status, (round(sum(px) / len(px), 2) if px else None), qty

    def _sync_live_legs(self, now: datetime) -> bool:
        """Read every live leg's real fills back, notice a leg the broker closed
        on its own, and book a trade whose legs are all settled. True when
        anything changed."""
        if _time.time() - self.state.get('_synced_at', 0) < _SYNC_SECS:
            return False
        self.state['_synced_at'] = _time.time()
        changed = False
        for t in self.state['day']['trades']:
            for key, leg in (t.get('legs') or {}).items():
                if key == 'paper':
                    continue
                if leg.get('entry_record') and not leg.get('entry_confirmed'):
                    f = self._fills(leg['entry_record'])
                    if f and f[0] == 'EXECUTED' and f[1]:
                        leg.update(entry=f[1], entry_confirmed=True)
                        changed = True
                if leg.get('open') and leg.get('sl_record'):
                    f = self._fills(leg['sl_record'])
                    if f and f[0] == 'EXECUTED' and f[2] > 0:
                        leg.update(open=False, exit=f[1], exit_confirmed=True, exit_time=_hhmm(now),
                                   exit_reason='backstop SL-M filled at the broker')
                        self._note(f"#{t['n']} {leg['name']}: the backstop SL-M at {leg.get('backstop')} "
                                   f"FILLED at the broker ({f[1]}) — the position is flat; the index "
                                   f"trade is followed on paper from here")
                        self._alert('cpr_exit', f"CPR — backstop filled at {leg['name']}",
                                    f"sold at {f[1]}; NIFTY {self.state.get('spot')}", t)
                        changed = True
                if not leg.get('open') and leg.get('exit_record') and not leg.get('exit_confirmed'):
                    f = self._fills(leg['exit_record'])
                    if f and f[0] == 'EXECUTED' and f[1]:
                        leg.update(exit=f[1], exit_confirmed=True)
                        changed = True
                if leg.get('entry') is not None and leg.get('exit') is not None:
                    pnl = round((leg['exit'] - leg['entry']) * self._qty(leg), 2)
                    if pnl != leg.get('pnl'):
                        leg['pnl'] = pnl
                        changed = True
            if self._book_if_settled(t):
                changed = True
        return changed

    def _book_if_settled(self, t) -> bool:
        """Write the trade's history row once: when its index trade is over or
        every leg is already flat at the broker, and every leg's fills are in
        (or _BOOK_WAIT_SECS have passed since the last exit)."""
        if t.get('booked') or not t.get('option'):
            return False
        legs = t.get('legs') or {}
        if any(l.get('open') for l in legs.values()):
            return False
        flat_at = t.get('flat_at') or _time.time()
        t.setdefault('flat_at', flat_at)
        pending = [l for k, l in legs.items() if k != 'paper' and l.get('qty')
                   and not (l.get('entry_confirmed') and l.get('exit_confirmed'))]
        if pending and _time.time() - flat_at < _BOOK_WAIT_SECS:
            return False
        t['booked'] = True
        pnl = sum(float(l.get('pnl') or 0) for l in legs.values())
        _append_history({
            'date': self.state['day']['date'], 'n': t['n'], 'mode': t.get('mode'),
            'strategy': t['strategy'], 'side': t['trade'], 'setup_time': t['setup_time'],
            'entry': t['entry'], 'sl': t['sl'], 'target': t['target'],
            'index_fill': t['fill'], 'fill_time': t['fill_time'], 'index_exit': t.get('exit'),
            'exit_time': t.get('exit_time') or min((l.get('exit_time') or '99:99') for l in legs.values()),
            'result': t.get('result') or 'Backstop', 'pts': t.get('pts'),
            'locked_at': t.get('locked_at'),            # when a ridden target was reached
            'strike': t['option']['strike'], 'option_type': t['option']['type'],
            'legs': {k: {x: v.get(x) for x in ('name', 'lots', 'qty', 'entry', 'exit', 'pnl', 'exit_reason')}
                     for k, v in legs.items()},
            'pnl': round(pnl, 2),
            'unconfirmed': bool(pending) or None,
        })
        self._note(f"#{t['n']}: booked ₹{pnl:,.0f}" + (' (fills not all confirmed)' if pending else ''))
        return True

    # ── acting on the engine ────────────────────────────────────────────

    def _act(self, ev: Dict[str, Any], now: datetime) -> None:
        t, kind = ev['trade'], ev['event']
        label = f"#{t['n']} {t['trade']} {t['strategy']}"
        if kind == 'fill' and ev.get('stale'):
            # A fill read off a candle that closed minutes ago — the thread was
            # down, or this is a restart catching up the morning. That move has
            # happened; an order now would buy it late. Recorded, not traded.
            t['missed'] = True
            t['legs'] = {}
            self._note(f"{label}: entry {t['entry']:,.0f} was crossed at {t['fill_time']} while the "
                       f"algo was not watching — missed, not traded")
            self._alert('cpr_order_failed', 'CPR — entry missed', f"{label}: crossed at "
                        f"{t['fill_time']} while the algo was down", t)
            return
        if kind == 'armed':
            self._note(f"{label}: set up at {t['setup_time']} — entry {t['entry']:,.0f}, "
                       f"SL {t['sl']:,.0f}, target {t['target']:,.0f}")
            self._alert('cpr_setup', f"CPR setup — {t['trade']} NIFTY", f"{t['strategy']}: entry "
                        f"{t['entry']:,.0f} · SL {t['sl']:,.0f} · target {t['target']:,.0f}", t)
        elif kind == 'fill':
            self._enter(t, now)
        elif kind == 'exit':
            self._leave(t, now)
        elif kind == 'locked':
            self._note(f"{label}: target {t['target']:,.0f} closed through at {t.get('locked_at')} — "
                       f"stop moved up to it, riding")
        elif kind == 'extended':
            self._note(f"{label}: strong break through the CPR — target extended to {t['target']:,.0f}")
        elif kind == 'expired':
            self._note(f"{label}: entry {t['entry']:,.0f} never reached by 15:15 — no trade")

    def _enter(self, t: Dict[str, Any], now: datetime) -> None:
        t['mode'] = mode(self.username)                  # the toggle as it stands at the entry
        option_type = eng.option_side(t)
        spot = self.state.get('spot') or t['fill']
        prem = settings(self.username)['premium']
        step = self._chain_step()
        atm = int(round(spot / step) * step)
        ladder = self._kite_premiums([atm + i * step for i in range(-13, 14)], option_type)
        pick = eng.pick_strike(lambda k: ladder.get(k) if ladder else self._premium(k, option_type),
                               spot, step, option_type, prem)
        if not pick:
            t['legs'] = {}
            t['error'] = 'no option quote — the leg could not be priced'
            self._note(f"#{t['n']}: index filled at {t['fill']:,.0f} but NO OPTION QUOTE — no leg")
            self._alert('cpr_order_failed', 'CPR — no option quote', t['error'], t)
            return
        t['option'] = {'symbol': SYMBOL, 'strike': pick['strike'], 'type': option_type,
                       'quoted': pick['premium']}
        live = t['mode'] == 'live'
        t['legs'] = self._enter_live(t) if live else self._enter_paper(t)
        filled = [l for l in t['legs'].values() if l.get('open')]
        self._note(f"#{t['n']} {t['trade']}: index {t['fill']:,.0f} at {t['fill_time']} → BUY "
                   f"NIFTY {pick['strike']} {option_type} ≈{pick['premium']} — "
                   + (', '.join(f"{l['name']} {l['lots']} lot(s) @ {l.get('entry')}" for l in filled)
                      or 'NOTHING FILLED'))
        self._alert('cpr_fill' if filled else 'cpr_order_failed',
                    f"CPR {'entry' if filled else 'entry FAILED'} — NIFTY {pick['strike']} {option_type}",
                    f"{t['strategy']} · index {t['fill']:,.0f} · SL {t['stop']:,.0f} · target {t['target']:,.0f}",
                    t)

    def _enter_paper(self, t) -> Dict[str, Any]:
        lots = lots_for_trade(self.username, 'paper', settings(self.username)['lots'])
        px = self._premium(t['option']['strike'], t['option']['type']) or t['option']['quoted']
        return {'paper': {'name': 'Paper', 'instance': 0, 'lots': lots, 'entry': px, 'open': True}}

    def _enter_live(self, t) -> Dict[str, Any]:
        from trading_app.app.routes.api import _dispatch_order_to_brokers, resolve_standard_lot
        from trading_app.app.order_placement.op_signal_engine import _place_stop_leg, _session, lot_chunks
        from trading_app.app.routes.order_placement_api import OP_STRATEGY
        from trading_app.app.utils.mine_order_store import MineOrderStore

        legs: Dict[str, Any] = {}
        lot_size = resolve_standard_lot(SYMBOL)
        if not lot_size:
            t['error'] = 'NIFTY lot size unavailable — no order sent'
            self._alert('cpr_order_failed', 'CPR — entry refused', t['error'], t)
            return legs
        opt = t['option']
        session_data = _session(self.username)
        accounts = cpr_accounts(self.username)
        if not accounts:
            t['error'] = 'no account has BROKER_N_CPR_ACTIVE=true'
            self._alert('cpr_order_failed', 'CPR — no account to trade', t['error'], t)
        for a in accounts:
            key = str(a['instance'])
            if not a['stoppable'] or not a['lots']:
                legs[key] = {'name': a['name'], 'instance': a['instance'], 'open': False,
                             'error': 'not zerodha/fyers' if not a['stoppable'] else 'BROKER_N_CPR_LOTS unset'}
                continue
            lots = lots_for_trade(self.username, 'live', a['lots'])
            summary, qty, prices = [], 0, []
            for chunk in lot_chunks(lots):
                try:
                    res = _dispatch_order_to_brokers(
                        symbol=SYMBOL, strike=opt['strike'], option_type=opt['type'], action='BUY',
                        strategy='cpr', username=self.username, session_data=session_data,
                        lots_for=lambda _i, _n=chunk: _n,
                        gate=lambda i, _b, _w=a['instance']: i == _w)
                except Exception as e:
                    logger.error(f"[CPR] entry at {a['name']} failed: {e}", exc_info=True)
                    res = {'success': False, 'summary': [], 'error': str(e)}
                for b in res.get('summary') or []:
                    summary.append(b)
                    r = b.get('result') or {}
                    if r.get('success'):
                        qty += int(b.get('quantity') or 0)
                        if float(r.get('price') or 0) > 0:
                            prices.append(float(r['price']))
            entry = round(sum(prices) / len(prices), 2) if prices else self._premium(opt['strike'], opt['type'])
            rec = MineOrderStore.add_order({
                'mode': 'broker', 'symbol': SYMBOL, 'strike': opt['strike'], 'option_type': opt['type'],
                'action': 'BUY', 'strategy': OP_STRATEGY, 'order_type': 'MARKET', 'type': 'MARKET',
                'instrument': 'NFO', 'price': 0.0, 'quantity': qty, 'entry_price': entry,
                'status': 'EXECUTED' if qty else 'REJECTED', 'username': self.username,
                'source': 'cpr', 'signal_id': self._signal_id(t), 'leg': 'ENTRY',
                'executed_at': int(_time.time() * 1000) if qty else None,
                'broker_order_ids': summary,
            })
            leg = {'name': a['name'], 'instance': a['instance'], 'lots': qty // lot_size if lot_size else lots,
                   'qty': qty, 'entry': entry, 'open': qty > 0, 'entry_record': rec['id']}
            if qty:
                # The floor if the app is down when the market runs: not the
                # exit (that is the index stop), so it sits a step beyond it.
                risk = abs(t['fill'] - t['sl'])
                x = _num(self.username, 'CPR_BACKSTOP_X', BACKSTOP_X)
                trigger = round(entry - x * risk, 1) if x > 0 else None
                sl_id = None
                if trigger and trigger > 0.05:
                    signal = {'id': self._signal_id(t), 'symbol': SYMBOL, 'strike': opt['strike'],
                              'option_type': opt['type'], 'action': 'BUY', 'lot_size': lot_size}
                    sl_id = _place_stop_leg(signal, a['instance'], qty // lot_size, trigger,
                                            self.username, session_data, source='cpr')
                leg.update(backstop=trigger, sl_record=sl_id)
                if trigger and trigger > 0.05 and not sl_id:
                    self._alert('cpr_order_failed', f"CPR — no backstop SL at {a['name']}",
                                f'the SL-M at {trigger} was refused; the index stop still exits', t)
            else:
                leg['error'] = next(((b.get('result') or {}).get('error') for b in summary
                                     if not (b.get('result') or {}).get('success')), 'refused')
            legs[key] = leg
        return legs

    def _leave(self, t: Dict[str, Any], now: datetime) -> None:
        opt = t.get('option')
        legs = t.get('legs') or {}
        exit_px = self._premium(opt['strike'], opt['type']) if opt else None
        for key, leg in legs.items():
            if not leg.get('open'):
                continue
            if key == 'paper':
                leg.update(exit=exit_px, open=False, exit_time=t['exit_time'])
            else:
                leg.update(self._exit_live(t, leg, exit_px), exit_time=t['exit_time'])
            leg['pnl'] = (round((leg['exit'] - leg['entry']) * self._qty(leg), 2)
                          if leg.get('exit') is not None and leg.get('entry') is not None else None)
        self._note(f"#{t['n']} {t['trade']}: {t['result']} at {t['exit_time']} — index {t['exit']:,.0f} "
                   f"({t['pts']:+.1f} pts)" + (f", premium {exit_px}" if exit_px else ''))
        pnl = sum(float(l.get('pnl') or 0) for l in legs.values())
        self._alert('cpr_exit', f"CPR exit — {t['result']} {t['pts']:+.0f} pts",
                    f"{t['strategy']} · NIFTY {opt['strike'] if opt else '—'} {opt['type'] if opt else ''} · "
                    f"₹{pnl:,.0f}", t)
        t['flat_at'] = _time.time()
        if 'paper' in legs:
            self._book_if_settled(t)

    def _qty(self, leg) -> int:
        if leg.get('qty'):
            return int(leg['qty'])
        from trading_app.app.routes.api import resolve_standard_lot
        return int(leg.get('lots') or 1) * int(resolve_standard_lot(SYMBOL) or 65)

    def _exit_live(self, t, leg, exit_px) -> Dict[str, Any]:
        """Cancel the backstop and sell what this account still holds — the
        Telegram engine's flatten, scoped to this trade's records at this
        account. Never raises."""
        from trading_app.app.routes.api import exit_selected_records
        from trading_app.app.order_placement.op_signal_engine import _session
        from trading_app.app.utils.mine_order_store import MineOrderStore
        ids = {leg.get('entry_record'), leg.get('sl_record')} - {None}
        try:
            res = exit_selected_records(
                self.username, _session(self.username),
                lambda: [o for o in MineOrderStore.get_today_orders() if o.get('id') in ids],
                log_tag=f"CPR {self._signal_id(t)} {leg['name']}", max_qty=int(leg.get('qty') or 0))
        except Exception as e:
            logger.error(f"[CPR] exit at {leg['name']} failed: {e}", exc_info=True)
            self._alert('cpr_order_failed', f"CPR — EXIT FAILED at {leg['name']}",
                        f'{e} — square it off by hand', t)
            return {'exit_error': str(e)}
        errs = res.get('errors') or []
        if errs:
            self._alert('cpr_order_failed', f"CPR — exit refused at {leg['name']}", str(errs[0]), t)
            return {'exit_error': str(errs[0])}
        exits = [ex for ex in (res.get('exits') or []) if int(ex.get('instance') or 0) == int(leg['instance'])]
        if not exits:
            # Nothing left to sell: the broker had already closed it (the
            # backstop, or a hand square-off) — book that fill, not this quote.
            f = self._fills(leg.get('sl_record'))
            if f and f[0] == 'EXECUTED' and f[2] > 0:
                return {'exit': f[1], 'open': False, 'exit_confirmed': True,
                        'exit_reason': 'backstop SL-M filled at the broker'}
            return {'exit': exit_px, 'open': False, 'exit_reason': 'already flat at the broker — fill unknown'}
        from trading_app.app.routes.order_placement_api import OP_STRATEGY
        ex = exits[0]
        rec = MineOrderStore.add_order({
            'mode': 'broker', 'symbol': SYMBOL, 'strike': t['option']['strike'],
            'option_type': t['option']['type'], 'action': ex.get('side') or 'SELL',
            'strategy': OP_STRATEGY, 'order_type': 'MARKET', 'type': 'MARKET', 'instrument': 'NFO',
            'price': None, 'quantity': sum(int(e.get('quantity') or 0) for e in exits),
            'status': 'OPEN', 'username': self.username, 'source': 'cpr',
            'signal_id': self._signal_id(t), 'leg': 'EXIT',
            'broker_order_ids': [{'broker': e.get('broker'), 'instance': leg['instance'], 'order_id': oid,
                                  'success': True, 'quantity': int(e.get('quantity') or 0)}
                                 for e in exits for oid in (e.get('order_ids') or [])],
        })
        return {'exit': exit_px, 'open': False, 'exit_record': rec['id']}

    def _signal_id(self, t) -> str:
        return f"cpr-{self.state['day']['date']}-{t['n']}"

    def _alert(self, category, title, summary, t=None) -> None:
        if not _flag(self.username, 'CPR_LOGIC_NOTIFY', 'true'):
            return
        try:
            from trading_app.service.notification_service import create_notification
            data = json.loads(json.dumps({k: (t or {}).get(k) for k in
                                          ('n', 'trade', 'entry', 'sl', 'target', 'strategy', 'mode')},
                                         default=str))
            create_notification(category=category, title=title, summary=(summary or '')[:200], data=data)
        except Exception as e:
            logger.error(f'[CPR] notification failed: {e}')
        if not _flag(self.username, 'CPR_LOGIC_TELEGRAM', 'true'):
            return
        token, chat = _uvar(self.username, 'TELEGRAM_BOT_TOKEN'), _uvar(self.username, 'TELEGRAM_CHAT_ID')
        if not (token and chat):
            return

        def _send():
            try:
                from trading_app.service.telegram_service import TelegramService
                TelegramService(token=token, chat_id=chat).send_text(f'{title}\n{summary}')
            except Exception as e:
                logger.error(f'[CPR] Telegram alert failed: {e}')
        threading.Thread(target=_send, daemon=True, name='CPRNotify').start()


def ensure_running(username: str, source: str = '') -> bool:
    """Start the thread for the session if it is enabled and not running."""
    if not settings(username)['enabled']:
        return False
    now = _now()
    mins = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (9 * 60 <= mins < _HARD_STOP_MIN):
        return False
    inst = get_instance(username)
    if inst and inst.is_running():
        return False
    logger.info(f'[CPR {source}] starting thread for {username}')
    CPRAlgo(username).start()
    return True
