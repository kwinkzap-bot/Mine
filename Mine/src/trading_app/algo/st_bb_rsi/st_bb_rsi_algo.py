"""ST + BB + RSI + R1/S1 — the Backtest page's strategy as a live index-options algo,
one instance per index (NIFTY, BANKNIFTY, SENSEX — see ``PROFILES``).

Each index is its OWN algo: its own tab, thread, state and history files, mode toggle
and flags (``STBB_*`` for NIFTY, ``STBB_BN_*`` BANKNIFTY, ``STBB_SX_*`` SENSEX, and
``BROKER_N_<prefix>_ACTIVE`` / ``_LOTS`` per account). They differ only in the symbol and
the premium bought (≈250 / 500 / 800, the Backtest page's choice per symbol). Below,
"STBB_*" is the NIFTY spelling of every setting.

The rule is ``Backtest/st_bb_rsi_engine.py`` (its ``signals`` is called as-is),
decided one closed 3-minute candle at a time by ``service/st_bb_rsi_live_engine.py``.
This module is the thread around it: the clock, the candles, the option leg, the
orders and the record. It is modelled on ``algo/cpr_logic/cpr_algo.py`` and keeps
the same rules about real money:

**Paper by default** — ``STBB_MODE=paper``. Every entry and exit is recorded at
the option's real premium at that moment and no order is sent. ``STBB_MODE=live``
(the Live/Paper toggle on the Algo tab, read at EACH ENTRY, never cached) buys
and sells at market at every account carrying ``BROKER_N_STBB_ACTIVE=true`` —
zerodha/fyers only. An account without ``BROKER_N_STBB_LOTS`` is refused, never
guessed. A position already open keeps the mode it was entered with.

**The trade is on the index; the order is an option.** Long (RSI above the level,
close above the upper Bollinger band and R1, SuperTrend up) buys the ≈``STBB_PREMIUM``
(250) CALL of the front weekly expiry; short (the mirror, through S1) buys the PUT.
There is no target and no stop: the trade ends when a later candle's close puts
SuperTrend against it, or at the 15:14 square-off. Live, an optional SL-M may rest
at the broker as a floor in case THIS APP is down when the market runs —
``STBB_BACKSTOP_PCT`` (percent of the entry premium; **0 = none, the default**).

**Candles** — today's and the previous sessions' 1-minute bars from Fyers, resampled
to 3 minutes anchored on 09:15 with the same closing-minute junk filter as the
backtest, so the live decision reads the numbers the backtest read.

**The books are the broker's.** A live leg's entry and exit are its real average
fills, read back from the order book. A trade is written to history once every
leg's fills are in (or two minutes after its exit, flagged).

**Sizing** is the flag, nothing else: a live order is ``BROKER_N_STBB_LOTS`` lots at each
account carrying ``BROKER_N_STBB_ACTIVE=true`` (an account without it is refused);
a paper trade takes the first flagged account's lots, or 1. There is no compounding.

State is ``st_bb_rsi_state.json`` (today; rebuilt after a restart) and every closed
trade is a row in ``st_bb_rsi_trades_history.json``.
"""

import json
import os
import threading
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from trading_app.app.utils.logger import logger
from trading_app.service import st_bb_rsi_live_engine as eng

_DIR = os.path.dirname(__file__)
STATE_FILE = os.path.join(_DIR, 'st_bb_rsi_state.json')
HISTORY_FILE = os.path.join(_DIR, 'st_bb_rsi_trades_history.json')

_IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = 'NIFTY'


class Profile:
    """What differs between the three algos: the index, the env-variable prefix, the
    premium default, the files, the exchange the options trade on."""

    def __init__(self, symbol, key, pfx, premium, instrument, tag, label):
        self.symbol, self.key, self.pfx = symbol, key, pfx
        self.premium, self.instrument = premium, instrument
        self.tag, self.label = tag, label                  # log tag, human title

    def state_file(self) -> str:
        return STATE_FILE if self.key == 'stbb' else os.path.join(_DIR, f'st_bb_rsi_{self.symbol.lower()}_state.json')

    def history_file(self) -> str:
        return HISTORY_FILE if self.key == 'stbb' else os.path.join(_DIR, f'st_bb_rsi_{self.symbol.lower()}_trades_history.json')


NIFTY = Profile('NIFTY', 'stbb', 'STBB', 250, 'NFO', 'STBB', 'ST+BB+RSI NIFTY')
BANKNIFTY = Profile('BANKNIFTY', 'stbb_bn', 'STBB_BN', 500, 'NFO', 'STBB-BN', 'ST+BB+RSI BANKNIFTY')
SENSEX = Profile('SENSEX', 'stbb_sx', 'STBB_SX', 800, 'BFO', 'STBB-SX', 'ST+BB+RSI SENSEX')
PROFILES = {pr.symbol: pr for pr in (NIFTY, BANKNIFTY, SENSEX)}
_POLL_SECS = 5
_BAR_LAG = timedelta(minutes=eng.BAR_MINUTES, seconds=5)     # a candle is read 5 s after it closes
_OPEN_MIN = 9 * 60 + 15
_HARD_STOP_MIN = 15 * 60 + 30
_PRIOR_DAYS = 14                      # calendar days of 1-minute history: indicators' run-in + yesterday's pivots
_STOPPABLE = ('zerodha', 'kite', 'fyers')
_SYNC_SECS = 15
_BOOK_WAIT_SECS = 120
_STALE_SECS = 150                     # an entry candle older than this at the moment of reading is a LATE entry
_ENTRY_TRIES = 6                      # a refused / unpriced entry is retried this many times (every _RETRY_SECS)
_RETRY_SECS = 10

_instances: Dict[str, 'StBbRsiAlgo'] = {}


def get_instance(username: str, p: Profile = NIFTY) -> Optional['StBbRsiAlgo']:
    return _instances.get(f'{p.key}:{username}')


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


def mode(username, p: Profile = NIFTY) -> str:
    return 'live' if _uvar(username, f'{p.pfx}_MODE', 'paper').lower() == 'live' else 'paper'


def direction(username, p: Profile = NIFTY) -> str:
    v = _uvar(username, f'{p.pfx}_DIRECTION', 'both').lower()
    return v if v in ('long', 'short', 'both') else 'both'


def settings(username, p: Profile = NIFTY) -> Dict[str, Any]:
    return {
        'mode': mode(username, p),
        'premium': _num(username, f'{p.pfx}_PREMIUM', p.premium),
        'direction': direction(username, p),
        'max_trades': max(1, int(_num(username, f'{p.pfx}_MAX_TRADES', 2))),
        'rsi_level': _num(username, f'{p.pfx}_RSI_LEVEL', 70),
        'rsi_len': int(_num(username, f'{p.pfx}_RSI_LEN', 14)),
        'st_len': int(_num(username, f'{p.pfx}_ST_LEN', 10)),
        'st_mult': _num(username, f'{p.pfx}_ST_MULT', 2),
        'bb_len': int(_num(username, f'{p.pfx}_BB_LEN', 20)),
        'bb_mult': _num(username, f'{p.pfx}_BB_MULT', 2),
        'require_green': _flag(username, f'{p.pfx}_REQUIRE_GREEN', 'false'),
        'backstop_pct': _num(username, f'{p.pfx}_BACKSTOP_PCT', 0),
        'enabled': _uvar(username, f'{p.pfx}_ENABLED', 'true').lower() != 'false',
    }


def accounts(username, p: Profile = NIFTY) -> List[Dict[str, Any]]:
    """The accounts a live entry reaches: typed, active, BROKER_N_STBB_ACTIVE, and a
    broker this app can hold a stop at. ``lots`` is BROKER_N_STBB_LOTS (None when
    unset — such an account is refused, never guessed)."""
    from trading_app.app.routes.api import is_broker_active
    out = []
    for i in range(1, 21):
        b_type = _uvar(username, f'BROKER_{i}_TYPE').lower()
        if not b_type or not is_broker_active(username, i):
            continue
        if not _flag(username, f'BROKER_{i}_{p.pfx}_ACTIVE'):
            continue
        try:
            lots = int(_uvar(username, f'BROKER_{i}_{p.pfx}_LOTS')) or None
        except (TypeError, ValueError):
            lots = None
        out.append({'instance': i, 'type': b_type, 'lots': lots,
                    'name': _uvar(username, f'BROKER_{i}_NAME', f'{b_type} {i}'),
                    'stoppable': b_type in _STOPPABLE})
    return out


# ── the record ────────────────────────────────────────────────────────────

def load_history(p: Profile = NIFTY) -> List[Dict[str, Any]]:
    try:
        with open(p.history_file()) as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _append_history(row: Dict[str, Any], p: Profile = NIFTY) -> None:
    rows = load_history(p)
    rows.append(row)
    hf = p.history_file()
    tmp = hf + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(rows, f, indent=2, default=str)
    os.replace(tmp, hf)


def _accts(username, p: Profile = NIFTY) -> List[Dict[str, Any]]:
    return accounts(username) if p is NIFTY else accounts(username, p)


def paper_lots(username, p: Profile = NIFTY) -> int:
    """A paper trade's size: the lots flag (BROKER_N_STBB_LOTS) of the first account
    flagged for this algo, so a paper day is sized like the live one would be — and
    1 lot when no account is flagged. Paper has no sizing settings of its own."""
    for a in _accts(username, p):
        if a.get('lots'):
            return int(a['lots'])
    return 1


class StBbRsiAlgo:
    """One thread per user per index; one day at a time."""

    def __init__(self, username: str, profile: Profile = NIFTY):
        self.username = username
        self.p = profile
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.state: Dict[str, Any] = {}

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f'StBbRsiAlgoThread-{self.p.symbol}')
        self._thread.start()
        _instances[f'{self.p.key}:{self.username}'] = self
        logger.info(f'[{self.p.tag}] thread started')

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop(f'{self.p.key}:{self.username}', None)
        logger.info(f'[{self.p.tag}] stop requested')

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── state ───────────────────────────────────────────────────────────

    def _load_state(self, today: str) -> Dict[str, Any]:
        try:
            with open(self.p.state_file()) as f:
                st = json.load(f)
            if st.get('day', {}).get('date') == today:
                return st
        except Exception:
            pass
        return {'day': eng.new_day(today), 'log': []}

    def _save_state(self) -> None:
        with self._lock:
            sf = self.p.state_file()
            tmp = sf + '.tmp'
            try:
                with open(tmp, 'w') as f:
                    json.dump(self.state, f, indent=2, default=str)
                os.replace(tmp, sf)
            except Exception as e:
                logger.error(f'[{self.p.tag}] state save failed: {e}')

    def _note(self, text: str) -> None:
        logger.info(f'[{self.p.tag}] {text}')
        self.state.setdefault('log', []).append({'t': _hhmm(_now()), 'text': text})
        self.state['log'] = self.state['log'][-200:]

    # ── market data (overridable in tests) ──────────────────────────────

    def _bars(self, today: date, now: datetime) -> pd.DataFrame:
        """Closed 3-minute candles: the last _PRIOR_DAYS days of 1-minute Fyers bars,
        the junk-filtered and 09:15-anchored resample the backtest uses, with the
        candle still forming dropped."""
        from trading_app.service import multichart_service as mc
        adapter, fy = mc.algo_provider(), mc.resolve_symbol(self.p.symbol)
        raw = adapter.historical_data(fy, (today - timedelta(days=_PRIOR_DAYS)).isoformat(),
                                      today.isoformat(), 'minute', use_cache=False) or []
        if not raw:
            return pd.DataFrame()
        m = pd.DataFrame(raw)
        m['date'] = pd.to_datetime(m['date'])
        m = m.set_index('date').sort_index()
        if getattr(m.index, 'tz', None) is not None:
            m.index = m.index.tz_localize(None)
        m = m[~m.index.duplicated(keep='last')]
        # Closing-minute junk Fyers sometimes serves for the newest sessions (see
        # api._sbr_load_bars): a bar after 15:15 spanning > 0.3 % is not real.
        late = (m.index.hour * 60 + m.index.minute) >= 15 * 60 + 15
        m = m[~(late & ((m['high'] - m['low']) > 0.003 * m['close']))]
        anchor = pd.Timedelta(hours=9, minutes=15)
        key = (m.index - anchor).floor(f'{eng.BAR_MINUTES}min') + anchor
        bars = m.groupby(key).agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
                                  close=('close', 'last'), volume=('volume', 'sum')).dropna(subset=['open', 'close'])
        bars.index.name = 'date'
        # A candle stamped s closes at s + 3 min; _BAR_LAG is that plus 5 s for the feed to have it.
        cutoff = pd.Timestamp(now.replace(tzinfo=None) - _BAR_LAG)
        return bars[bars.index <= cutoff]

    def _kite(self):
        """The Kite session the orders go through — the first flagged account's, else
        instance 1. Pricing off the broker that fills the order keeps the strike picked
        and the premium booked on one contract and one feed: DATA_PROVIDER=FYERS served
        stale spot and option quotes once its 5,000/day cap was hit (2026-10-21: NIFTY spot
        22,971 against a real ~22,750, a 23200 PE quoted 263 that filled at 503; SENSEX
        got no quote at all)."""
        try:
            from trading_app.app.routes.api import get_kite
            for a in _accts(self.username, self.p):
                if a.get('stoppable'):
                    k = get_kite(user=self.username, instance=a['instance'])
                    if k:
                        return k
            return get_kite(user=self.username, instance=1)
        except Exception as e:
            logger.debug(f'[{self.p.tag}] no Kite session: {e}')
            return None

    def _spot(self) -> Optional[float]:
        from trading_app.service.strategy_signal_service import SYMBOL_CONFIG
        kite = self._kite()
        key = (SYMBOL_CONFIG.get(self.p.symbol) or {}).get('kite_key')
        if kite and key:
            try:
                v = float(((kite.ltp([key]) or {}).get(key) or {}).get('last_price') or 0)
                if v:
                    return v
            except Exception as e:
                logger.debug(f'[{self.p.tag}] Kite spot unavailable: {e}')
        from trading_app.app.routes.order_placement_api import _spot
        try:
            v = _spot(self.p.symbol)
            return float(v) if v else None
        except Exception as e:
            logger.debug(f'[{self.p.tag}] spot unavailable: {e}')
            return None

    def _kite_premiums(self, strikes: List[int], option_type: str) -> Dict[int, float]:
        """One Kite ltp call for a whole ladder of strikes: {strike: last price}.
        BFO for SENSEX, NFO otherwise. Empty when Kite cannot answer."""
        kite = self._kite()
        if not kite:
            return {}
        try:
            from trading_app.service.kite_order_services import KiteService
            svc = KiteService(kite_instance=kite)
            ex = 'BFO' if self.p.symbol == 'SENSEX' else 'NFO'
            keys = {}
            for k in strikes:
                sym = svc.get_option_symbol(self.p.symbol, k, option_type)
                if sym:
                    keys[f'{ex}:{sym}'] = k
            if not keys:
                return {}
            got = kite.ltp(list(keys)) or {}
            return {k: float(got[key]['last_price']) for key, k in keys.items()
                    if float((got.get(key) or {}).get('last_price') or 0) > 0}
        except Exception as e:
            logger.warning(f'[{self.p.tag}] Kite option quotes unavailable: {e}')
            return {}

    def _premium(self, strike: int, option_type: str) -> Optional[float]:
        got = self._kite_premiums([int(strike)], option_type)
        if got:
            return got[int(strike)]
        from trading_app.app.routes.order_placement_api import option_ltp
        try:
            v = option_ltp(self.p.symbol, int(strike), option_type)
            return float(v) if v else None
        except Exception:
            return None

    def _chain_step(self) -> int:
        from trading_app.app.routes.order_placement_api import _chain_meta
        try:
            return int((_chain_meta(self.p.symbol) or {}).get('step') or 50)
        except Exception:
            return 50

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
                if mins < _OPEN_MIN + eng.BAR_MINUTES:      # the 09:15 candle closes at 09:18
                    self._stop_event.wait(10)
                    continue
                try:
                    self.tick(now)
                except Exception as e:
                    logger.error(f'[{self.p.tag}] tick failed: {e}', exc_info=True)
                self._stop_event.wait(_POLL_SECS)
        finally:
            _instances.pop(f'{self.p.key}:{self.username}', None)

    def tick(self, now: datetime) -> None:
        today = now.date()
        if not self.state or self.state['day']['date'] != today.isoformat():
            self.state = self._load_state(today.isoformat())
        day = self.state['day']
        s = settings(self.username, self.p)
        events: List[Dict[str, Any]] = []

        # Closed candles: any that closed since the last look, oldest first, so a
        # restart catches the morning up. Only the newest can be traded; an older
        # entry signal is stale and is recorded, never traded.
        last = day.get('last_bar')
        due = last is None or now.strftime('%Y-%m-%d %H:%M:%S') >= self._next_bar_ready(last)
        if due:
            bars = self._bars(today, now)
            todays = [k for k, ts in enumerate(bars.index) if ts.date() == today]
            if todays:
                newest = todays[-1]
                for k in todays:
                    ts = str(bars.index[k])
                    if last is not None and ts <= last:
                        continue
                    # Tradeable only if this is the candle that JUST closed: an older one,
                    # or a feed that is minutes late, would buy the move late.
                    age = (now.replace(tzinfo=None) - (bars.index[k] + pd.Timedelta(minutes=eng.BAR_MINUTES))).total_seconds()
                    stale = k < newest or age > _STALE_SECS
                    for ev in eng.on_bar(day, bars.iloc[:k + 1], dict(s, **self._window())):
                        ev['stale'] = stale
                        events.append(ev)
        events = self._drop_round_trips(events)
        events += eng.time_exits(day, _hhmm(now), self._window()['exit_cutoff'])

        for ev in events:
            self._act(ev, now, s)
        if self._retry_entries(now, s):
            events.append({'event': 'retry'})
        synced = self._sync_live_legs(now)
        if any(t['status'] == 'open' for t in day['trades']):
            self.state['spot'] = self._spot() or self.state.get('spot')     # a quote only while something is open
            marked = self._mark_open(day)
        else:
            marked = False
        if events or synced or marked:
            self._save_state()
        else:
            self.state['updated'] = _hhmm(now)

    def _mark_open(self, day: Dict[str, Any]) -> bool:
        """The running P&L of every open leg, so the Today table has a number before the
        exit: ``ltp`` is the premium now and ``mtm`` is (ltp − entry) × qty. True when a
        mark moved."""
        changed = False
        for t in day['trades']:
            opt = t.get('option')
            if t['status'] != 'open' or not opt:
                continue
            px = self._premium(opt['strike'], opt['type'])
            if not px:
                continue
            for leg in (t.get('legs') or {}).values():
                if leg.get('open') and leg.get('entry') is not None:
                    mtm = round((px - leg['entry']) * self._qty(leg), 2)
                    if leg.get('ltp') != px or leg.get('mtm') != mtm:
                        leg.update(ltp=px, mtm=mtm)
                        changed = True
        return changed

    @staticmethod
    def _drop_round_trips(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """A late entry is still taken — unless the very same catch-up also carries
        that trade's exit (SuperTrend already turned back): then the whole move is
        history, so it is recorded as missed and no order goes out."""
        out = list(events)
        for ev in events:
            if ev['event'] == 'entry' and ev.get('stale'):
                t = ev['trade']
                if any(x['event'] == 'exit' and x['trade'] is t for x in events):
                    t.update(status='missed', legs={})
                    out = [x for x in out if x['trade'] is not t]
        return out

    def _retry_entries(self, now: datetime, s: Dict[str, Any]) -> bool:
        """An entry that was refused or could not be priced is not given up on: the
        trade stays 'open' without legs and the order is tried again, until it fills,
        the tries run out, or SuperTrend ends the trade (the normal exit)."""
        if _hhmm(now) >= self._window()['exit_cutoff']:
            return False
        did = False
        for t in self.state['day']['trades']:
            if t['status'] != 'open' or not t.get('pending_entry'):
                continue
            if _time.time() - t.get('last_try', 0) < _RETRY_SECS:
                continue
            self._enter(t, now, s)
            did = True
        return did

    @staticmethod
    def _window() -> Dict[str, str]:
        return {'entry_start': '09:15', 'entry_end': '15:14', 'exit_cutoff': '15:14'}

    @staticmethod
    def _next_bar_ready(last_ts: str) -> str:
        """Wall-clock time at which the candle after `last_ts` has closed and the feed has it."""
        t = datetime.strptime(last_ts[:19], '%Y-%m-%d %H:%M:%S') + timedelta(minutes=2 * eng.BAR_MINUTES, seconds=5)
        return t.strftime('%Y-%m-%d %H:%M:%S')

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
            logger.debug(f'[{self.p.tag}] fills of {record_id} unavailable: {e}')
            return None
        if not rows:
            return None
        status = 'OPEN' if any(r[0] == 'OPEN' for r in rows.values()) else next(iter(rows.values()))[0]
        qty = sum(int(r[2] or 0) for r in rows.values())
        px = [r[1] for r in rows.values() if r[1]]
        return status, (round(sum(px) / len(px), 2) if px else None), qty

    def _sync_live_legs(self, now: datetime) -> bool:
        """Read live legs' real fills back, notice a leg the broker closed on its
        own, and book a trade whose legs are all settled. True when anything changed."""
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
                        self._note(f"#{t['n']} {leg['name']}: the backstop SL-M FILLED at the broker "
                                   f"({f[1]}) — the position is flat")
                        self._alert('stbb_exit', f"{self.p.label} — backstop filled at {leg['name']}",
                                    f'sold at {f[1]}', t)
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
        """Write the trade's history row once: when it is over (or every leg is flat
        at the broker) and every leg's fills are in (or _BOOK_WAIT_SECS have passed)."""
        if t.get('booked') or not t.get('option') or t['status'] == 'open' and not self._all_flat(t):
            return False
        legs = t.get('legs') or {}
        if any(l.get('open') for l in legs.values()):
            return False
        if not any(l.get('entry') is not None for l in legs.values()):
            t['booked'] = True              # nothing was ever bought (refused / no quote): no P&L row
            return False
        flat_at = t.get('flat_at') or _time.time()
        t.setdefault('flat_at', flat_at)
        pending = [l for k, l in legs.items() if k != 'paper' and l.get('qty')
                   and not (l.get('entry_confirmed') and l.get('exit_confirmed'))]
        if pending and _time.time() - flat_at < _BOOK_WAIT_SECS:
            return False
        t['booked'] = True
        gross = sum(float(l.get('pnl') or 0) for l in legs.values())
        charges = sum(eng.brokerage_for(int(l.get('lots') or 1)) for l in legs.values() if l.get('entry') is not None)
        _append_history({
            'date': self.state['day']['date'], 'n': t['n'], 'mode': t.get('mode'),
            'side': t['type'], 'signal_bar': t['signal_bar'], 'signal': t.get('signal'),
            'index_entry': t.get('index_entry'), 'entry_time': t.get('entry_time'),
            'index_exit': t.get('index_exit'), 'exit_time': t.get('exit_time'),
            'result': t.get('result') or 'Backstop',
            'strike': t['option']['strike'], 'option_type': t['option']['type'],
            'legs': {k: {x: v.get(x) for x in ('name', 'lots', 'qty', 'entry', 'exit', 'pnl', 'exit_reason')}
                     for k, v in legs.items()},
            'pnl': round(gross, 2), 'charges': round(charges, 2), 'net': round(gross - charges, 2),
            'unconfirmed': bool(pending) or None,
        }, self.p)
        self._note(f"#{t['n']}: booked ₹{gross - charges:,.0f} net (₹{gross:,.0f} − ₹{charges:,.0f} charges)"
                   + (' (fills not all confirmed)' if pending else ''))
        return True

    @staticmethod
    def _all_flat(t) -> bool:
        legs = t.get('legs') or {}
        return bool(legs) and not any(l.get('open') for l in legs.values())

    # ── acting on the engine ────────────────────────────────────────────

    def _act(self, ev: Dict[str, Any], now: datetime, s: Dict[str, Any]) -> None:
        t, kind = ev['trade'], ev['event']
        label = f"#{t['n']} {t['type']}"
        if kind == 'entry':
            if ev.get('stale'):
                # Found late (the thread was down, or a restart is catching up). The
                # strategy has no target or stop — it rides until SuperTrend turns — so
                # a signal whose trend is still intact is still the trade: taken now.
                t['late'] = True
                self._note(f"{label}: signal at {t['signal_bar'][11:16]} was found late — "
                           f"SuperTrend still with it, entering now")
            self._enter(t, now, s)
        elif kind == 'exit' and t['status'] == 'open':
            self._leave(t, ev, now)

    def _enter(self, t: Dict[str, Any], now: datetime, s: Dict[str, Any]) -> None:
        t['mode'] = mode(self.username, self.p)                  # the toggle as it stands at the entry
        opt_type = eng.option_type(t)
        spot = self._spot()
        t['index_entry'] = spot or t['signal']['close']
        t['entry_time'] = _hhmm(now)
        t['attempts'] = int(t.get('attempts') or 0) + 1
        t['last_try'] = _time.time()
        last_try = t['attempts'] >= _ENTRY_TRIES
        from trading_app.service import cpr_live_engine as cpr_eng
        step = self._chain_step()
        atm = int(round(t['index_entry'] / step) * step)
        ladder = self._kite_premiums([atm + i * step for i in range(-13, 14)], opt_type)
        pick = cpr_eng.pick_strike(lambda k: ladder.get(k) if ladder else self._premium(k, opt_type),
                                   t['index_entry'], step, opt_type, s['premium'])
        if not pick:
            t.update(legs={}, error='no option quote — the leg could not be priced')
            if last_try:
                t.update(status='failed', pending_entry=False)
                self._alert('stbb_order_failed', f'{self.p.label} — no option quote', t['error'], t)
            else:
                t['pending_entry'] = True
            self._note(f"#{t['n']}: {t['type']} signal but NO OPTION QUOTE — "
                       + ('gave up' if last_try else f"retrying (try {t['attempts']}/{_ENTRY_TRIES})"))
            return
        t['option'] = {'symbol': self.p.symbol, 'strike': pick['strike'], 'type': opt_type, 'quoted': pick['premium']}
        t['legs'] = self._enter_live(t, s) if t['mode'] == 'live' else self._enter_paper(t, s)
        filled = [l for l in t['legs'].values() if l.get('open')]
        if not filled:
            if last_try or any(l.get('qty') for l in t['legs'].values()):
                t.update(status='failed', pending_entry=False)
            else:
                t['pending_entry'] = True           # nothing reached a broker: try again next round
                self._note(f"#{t['n']}: entry not placed — retrying (try {t['attempts']}/{_ENTRY_TRIES})")
                return
        else:
            t['pending_entry'] = False
            t.pop('error', None)
        self._note(f"#{t['n']} {t['type']}: RSI {t['signal']['rsi']} → BUY {self.p.symbol} {pick['strike']} {opt_type} "
                   f"≈{pick['premium']} — "
                   + (', '.join(f"{l['name']} {l['lots']} lot(s) @ {l.get('entry')}" for l in filled)
                      or 'NOTHING FILLED'))
        self._alert('stbb_fill' if filled else 'stbb_order_failed',
                    f"{self.p.label} {'entry' if filled else 'entry FAILED'} — {self.p.symbol} {pick['strike']} {opt_type}",
                    f"{t['type']} · {t['mode']} · {self.p.symbol} {t['index_entry']:,.0f}", t)

    def _enter_paper(self, t, s) -> Dict[str, Any]:
        lots = paper_lots(self.username, self.p)
        px = self._premium(t['option']['strike'], t['option']['type']) or t['option']['quoted']
        return {'paper': {'name': 'Paper', 'instance': 0, 'lots': lots, 'entry': px, 'open': True}}

    def _enter_live(self, t, s) -> Dict[str, Any]:
        from trading_app.app.routes.api import _dispatch_order_to_brokers, resolve_standard_lot
        from trading_app.app.order_placement.op_signal_engine import _place_stop_leg, _session, lot_chunks
        from trading_app.app.routes.order_placement_api import OP_STRATEGY
        from trading_app.app.utils.mine_order_store import MineOrderStore

        legs: Dict[str, Any] = {}
        lot_size = resolve_standard_lot(self.p.symbol)
        if not lot_size:
            t['error'] = f'{self.p.symbol} lot size unavailable — no order sent'
            self._alert('stbb_order_failed', f'{self.p.label} — entry refused', t['error'], t)
            return legs
        opt = t['option']
        session_data = _session(self.username)
        accts = _accts(self.username, self.p)
        if not accts:
            t['error'] = f'no account has BROKER_N_{self.p.pfx}_ACTIVE=true'
            self._alert('stbb_order_failed', f'{self.p.label} — no account to trade', t['error'], t)
        for a in accts:
            key = str(a['instance'])
            if not a['stoppable'] or not a['lots']:
                legs[key] = {'name': a['name'], 'instance': a['instance'], 'open': False,
                             'error': 'not zerodha/fyers' if not a['stoppable'] else f'BROKER_N_{self.p.pfx}_LOTS unset'}
                continue
            lots = a['lots']            # BROKER_N_STBB_LOTS — the account's own flag, nothing computed
            summary, qty, prices = [], 0, []
            for chunk in lot_chunks(lots):
                try:
                    res = _dispatch_order_to_brokers(
                        symbol=self.p.symbol, strike=opt['strike'], option_type=opt['type'], action='BUY',
                        strategy=self.p.key, username=self.username, session_data=session_data,
                        lots_for=lambda _i, _n=chunk: _n,
                        gate=lambda i, _b, _w=a['instance']: i == _w)
                except Exception as e:
                    logger.error(f"[{self.p.tag}] entry at {a['name']} failed: {e}", exc_info=True)
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
                'mode': 'broker', 'symbol': self.p.symbol, 'strike': opt['strike'], 'option_type': opt['type'],
                'action': 'BUY', 'strategy': OP_STRATEGY, 'order_type': 'MARKET', 'type': 'MARKET',
                'instrument': self.p.instrument, 'price': 0.0, 'quantity': qty, 'entry_price': entry,
                'status': 'EXECUTED' if qty else 'REJECTED', 'username': self.username,
                'source': self.p.key, 'signal_id': self._signal_id(t), 'leg': 'ENTRY',
                'executed_at': int(_time.time() * 1000) if qty else None,
                'broker_order_ids': summary,
            })
            leg = {'name': a['name'], 'instance': a['instance'], 'lots': qty // lot_size if lot_size else lots,
                   'qty': qty, 'entry': entry, 'open': qty > 0, 'entry_record': rec['id']}
            if qty:
                # Optional floor in case THIS APP is down when the market runs: an SL-M
                # at `backstop_pct` % below the fill. Off (0) by default — the strategy
                # has no stop; its exit is the SuperTrend.
                pct = float(s.get('backstop_pct') or 0)
                trigger = round(entry * (1 - pct / 100.0), 1) if pct > 0 and entry else None
                sl_id = None
                if trigger and trigger > 0.05:
                    signal = {'id': self._signal_id(t), 'symbol': self.p.symbol, 'strike': opt['strike'],
                              'option_type': opt['type'], 'action': 'BUY', 'lot_size': lot_size}
                    sl_id = _place_stop_leg(signal, a['instance'], qty // lot_size, trigger,
                                            self.username, session_data, source=self.p.key)
                leg.update(backstop=trigger, sl_record=sl_id)
                if trigger and trigger > 0.05 and not sl_id:
                    self._alert('stbb_order_failed', f"{self.p.label} — no backstop SL at {a['name']}",
                                f'the SL-M at {trigger} was refused; the SuperTrend exit still applies', t)
            else:
                leg['error'] = next(((b.get('result') or {}).get('error') for b in summary
                                     if not (b.get('result') or {}).get('success')), 'refused')
            legs[key] = leg
        return legs

    def _leave(self, t: Dict[str, Any], ev: Dict[str, Any], now: datetime) -> None:
        opt = t.get('option')
        legs = t.get('legs') or {}
        t.update(status='closed', result=ev['reason'], exit_time=_hhmm(now),
                 index_exit=self._spot() or ev.get('index_close'))
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
        pnl = sum(float(l.get('pnl') or 0) for l in legs.values())
        self._note(f"#{t['n']} {t['type']}: {ev['reason']} at {t['exit_time']} — index "
                   f"{t.get('index_exit') or 0:,.0f}" + (f", premium {exit_px}" if exit_px else ''))
        if legs:
            self._alert('stbb_exit', f"ST+BB+RSI exit — {ev['reason']}",
                        f"{t['type']} · {self.p.symbol} {opt['strike'] if opt else '—'} {opt['type'] if opt else ''} · "
                        f"₹{pnl:,.0f}", t)
        t['pending_entry'] = False
        t['flat_at'] = _time.time()
        if 'paper' in legs:
            self._book_if_settled(t)

    def _qty(self, leg) -> int:
        if leg.get('qty'):
            return int(leg['qty'])
        from trading_app.app.routes.api import resolve_standard_lot
        return int(leg.get('lots') or 1) * int(resolve_standard_lot(self.p.symbol) or 65)

    def _exit_live(self, t, leg, exit_px) -> Dict[str, Any]:
        """Cancel the backstop (if any) and sell what this account still holds, scoped
        to this trade's records at this account. Never raises."""
        from trading_app.app.routes.api import exit_selected_records
        from trading_app.app.order_placement.op_signal_engine import _session
        from trading_app.app.utils.mine_order_store import MineOrderStore
        ids = {leg.get('entry_record'), leg.get('sl_record')} - {None}
        try:
            res = exit_selected_records(
                self.username, _session(self.username),
                lambda: [o for o in MineOrderStore.get_today_orders() if o.get('id') in ids],
                log_tag=f"STBB {self._signal_id(t)} {leg['name']}", max_qty=int(leg.get('qty') or 0))
        except Exception as e:
            logger.error(f"[{self.p.tag}] exit at {leg['name']} failed: {e}", exc_info=True)
            self._alert('stbb_order_failed', f"{self.p.label} — EXIT FAILED at {leg['name']}",
                        f'{e} — square it off by hand', t)
            return {'exit_error': str(e)}
        errs = res.get('errors') or []
        if errs:
            self._alert('stbb_order_failed', f"{self.p.label} — exit refused at {leg['name']}", str(errs[0]), t)
            return {'exit_error': str(errs[0])}
        exits = [ex for ex in (res.get('exits') or []) if int(ex.get('instance') or 0) == int(leg['instance'])]
        if not exits:
            f = self._fills(leg.get('sl_record'))
            if f and f[0] == 'EXECUTED' and f[2] > 0:
                return {'exit': f[1], 'open': False, 'exit_confirmed': True,
                        'exit_reason': 'backstop SL-M filled at the broker'}
            return {'exit': exit_px, 'open': False, 'exit_reason': 'already flat at the broker — fill unknown'}
        from trading_app.app.routes.order_placement_api import OP_STRATEGY
        ex = exits[0]
        rec = MineOrderStore.add_order({
            'mode': 'broker', 'symbol': self.p.symbol, 'strike': t['option']['strike'],
            'option_type': t['option']['type'], 'action': ex.get('side') or 'SELL',
            'strategy': OP_STRATEGY, 'order_type': 'MARKET', 'type': 'MARKET', 'instrument': self.p.instrument,
            'price': None, 'quantity': sum(int(e.get('quantity') or 0) for e in exits),
            'status': 'OPEN', 'username': self.username, 'source': self.p.key,
            'signal_id': self._signal_id(t), 'leg': 'EXIT',
            'broker_order_ids': [{'broker': e.get('broker'), 'instance': leg['instance'], 'order_id': oid,
                                  'success': True, 'quantity': int(e.get('quantity') or 0)}
                                 for e in exits for oid in (e.get('order_ids') or [])],
        })
        return {'exit': exit_px, 'open': False, 'exit_record': rec['id']}

    def _signal_id(self, t) -> str:
        return f"{self.p.key}-{self.state['day']['date']}-{t['n']}"

    def _alert(self, category, title, summary, t=None) -> None:
        if not _flag(self.username, 'STBB_NOTIFY', 'true'):
            return
        try:
            from trading_app.service.notification_service import create_notification
            data = json.loads(json.dumps({k: (t or {}).get(k) for k in ('n', 'type', 'signal', 'mode')},
                                         default=str))
            create_notification(category=category, title=title, summary=(summary or '')[:200], data=data)
        except Exception as e:
            logger.error(f'[{self.p.tag}] notification failed: {e}')
        if not _flag(self.username, 'STBB_TELEGRAM', 'true'):
            return
        token, chat = _uvar(self.username, 'TELEGRAM_BOT_TOKEN'), _uvar(self.username, 'TELEGRAM_CHAT_ID')
        if not (token and chat):
            return

        def _send():
            try:
                from trading_app.service.telegram_service import TelegramService
                TelegramService(token=token, chat_id=chat).send_text(f'{title}\n{summary}')
            except Exception as e:
                logger.error(f'[{self.p.tag}] Telegram alert failed: {e}')
        threading.Thread(target=_send, daemon=True, name='StBbRsiNotify').start()


def ensure_running(username: str, source: str = '', p: Profile = NIFTY) -> bool:
    """Start the thread for the session if it is enabled and not running."""
    if not (settings(username) if p is NIFTY else settings(username, p))['enabled']:
        return False
    now = _now()
    mins = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (9 * 60 <= mins < _HARD_STOP_MIN):
        return False
    inst = get_instance(username, p)
    if inst and inst.is_running():
        return False
    logger.info(f'[{p.tag} {source}] starting thread for {username}')
    StBbRsiAlgo(username, p).start()
    return True
