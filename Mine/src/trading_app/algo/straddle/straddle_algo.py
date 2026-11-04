"""NIFTY ATM Straddle — the Backtest page's straddle as a live algo.

The rule is ``Backtest/option_selling_engine.py``, driven by
``service/straddle_live_engine.py`` (read that module's notes for where live
differs from the backtest). This is the thread around it: the clock, the quotes,
the orders and the record. The Algo tab's boxes are the Backtest form's.

**Paper by default** — ``STR_MODE=paper``: every decision is recorded at Kite's
real quotes and nothing is sent. ``STR_MODE=live`` (the tab's toggle, read at each
entry) SELLS at every account carrying ``BROKER_N_STR_ACTIVE=true``, **zerodha
only**. The position keeps the mode and the settings it was entered with.

**This is short options carried overnight**, so:

* **NRML on an exact contract.** It never goes through
  ``_dispatch_order_to_brokers``, which picks the nearest expiry; every order names
  its tradingsymbol. Its records are not ``MineOrderStore`` rows either, so the
  Order Placement strip's Exit (written around bought options) cannot reach them.
* **Nothing rests at the broker.** Target, stop and lock are read here, so a position
  is only managed while this app runs AND Kite is logged in — Kite's token dies every
  morning. No quote or no session on an open position raises
  ``straddle_order_failed``. An overnight gap cannot be stopped by anything: 2026-02-03
  opened +713 and a one-night straddle lost 4.6 × its premium.
* **The leg decides, not the flag.** ``held`` on each account is what is managed: Stop,
  a Paper toggle or a cleared ``BROKER_N_STR_ACTIVE`` never orphan a position. Stop only
  ends new entries; the thread keeps running until it is flat.
* **Buys go first.** A hedge or a buy-back before any sell, so no order needs more margin
  than the position it leaves. A short the broker refuses at entry unwinds that account.
* **An exit, once decided, is finished.** It is retried every poll until the account is
  flat, capped at what the broker says is really open (a square-off by hand must not
  be bought back a second time into a long, or sold into a fresh short).

State is ``straddle_state.json`` (survives restarts and nights); every closed trade is
a row in ``straddle_trades_history.json``.
"""

import json
import os
import threading
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading_app.app.utils.logger import logger
from trading_app.app.utils.trading_calendar import is_trading_day
from trading_app.Backtest import option_selling_engine as ose
from trading_app.service import straddle_live_engine as eng

_DIR = os.path.dirname(__file__)
STATE_FILE = os.path.join(_DIR, 'straddle_state.json')
HISTORY_FILE = os.path.join(_DIR, 'straddle_trades_history.json')

_IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = eng.SYMBOL
_POLL_SECS = 5
_OPEN = (9, 15, 5)                    # nothing is read off the pre-open's stale last prices
_CLOSE = (15, 30)                     # nothing can be filled from here; an unfinished exit resumes at the open
_HARD_STOP_MIN = 15 * 60 + 31
_FILL_POLLS = 10
_FILL_POLL_WAIT = 0.5
_RECONCILE_SECS = 60
_SVC_TTL_SECS = 300

DEFAULTS: Dict[str, Any] = {          # the form the strategy was chosen on
    'mode': 'paper', 'enabled': True,
    'cadence': 'weekly', 'entry_day': 'monday', 'skip_one_night': False, 'entry_time': '15:15',
    'target_pct': 50.0, 'stop_pct': 25.0,
    'adjust': 'recenter', 'adjust_pts': 200.0, 'max_adjust': 2,
    'wings': 0, 'lock_at': 40.0, 'lock_to': 30.0,
    'lots': 1, 'compounding': True, 'start_capital': 250000.0, 'per_lot': 250000.0,
}
_VARS = {'mode': 'STR_MODE', 'enabled': 'STR_ENABLED', 'cadence': 'STR_CADENCE', 'entry_day': 'STR_ENTRY_DAY',
         'skip_one_night': 'STR_SKIP_ONE_NIGHT', 'entry_time': 'STR_ENTRY_TIME',
         'target_pct': 'STR_TARGET_PCT', 'stop_pct': 'STR_STOP_PCT', 'adjust': 'STR_ADJUST',
         'adjust_pts': 'STR_ADJUST_PTS', 'max_adjust': 'STR_MAX_ADJUST', 'wings': 'STR_WINGS',
         'lock_at': 'STR_LOCK_AT', 'lock_to': 'STR_LOCK_TO', 'lots': 'STR_LOTS',
         'compounding': 'STR_COMPOUNDING', 'start_capital': 'STR_START_CAPITAL', 'per_lot': 'STR_PER_LOT'}
RULE_KEYS = ('target_pct', 'stop_pct', 'adjust', 'adjust_pts', 'max_adjust', 'wings', 'lock_at', 'lock_to')

_instances: Dict[str, 'StraddleAlgo'] = {}


def get_instance(username: str) -> Optional['StraddleAlgo']:
    return _instances.get(username)


def _now() -> datetime:
    return datetime.now(_IST)


def _stamp(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')


# ── settings (read where used, never cached) ──────────────────────────────

def _uvar(username, name, default=''):
    from trading_app.app.utils.user_env import UserEnvManager
    v = UserEnvManager.get_user_var(username, name, default)
    return default if v in (None, '') else str(v).strip()


def _truthy(v) -> bool:
    return str(v).strip().lower() in ('true', '1', 'yes', 'on')


def settings(username) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        raw = _uvar(username, _VARS[key], '')
        if raw == '':
            out[key] = default
        elif isinstance(default, bool):
            out[key] = _truthy(raw)
        elif isinstance(default, (int, float)):
            try:
                out[key] = type(default)(float(raw))
            except (TypeError, ValueError):
                out[key] = default
        else:
            out[key] = raw
    out['mode'] = 'live' if str(out['mode']).lower() == 'live' else 'paper'
    return out


def clean_settings(data: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, str]:
    """The tab's choices → {env var: value}, or ValueError naming the bad one."""
    merged = {**current, **data}
    saves: Dict[str, str] = {}

    def num(key, lo, hi, whole=False):
        try:
            v = float(data[key])
        except (TypeError, ValueError):
            raise ValueError(f'{key} must be a number')
        if not lo <= v <= hi:
            raise ValueError(f'{key} must be {lo}-{hi}')
        saves[_VARS[key]] = str(int(v) if whole else v)

    if 'mode' in data:
        if data['mode'] not in ('paper', 'live'):
            raise ValueError("mode must be 'paper' or 'live'")
        saves[_VARS['mode']] = data['mode']
    if merged['cadence'] not in ose.CADENCES:
        raise ValueError(f'cadence must be one of {", ".join(ose.CADENCES)}')
    if 'cadence' in data:
        saves[_VARS['cadence']] = data['cadence']
    if merged['entry_day'] not in ose.entry_rules(merged['cadence']):
        raise ValueError(f"entry_day must be one of {', '.join(ose.entry_rules(merged['cadence']))} "
                         f"for a {merged['cadence']} straddle")
    if 'entry_day' in data or 'cadence' in data:
        saves[_VARS['entry_day']] = merged['entry_day']
    if 'adjust' in data:
        if data['adjust'] not in ose.ADJUST_TYPES:
            raise ValueError(f'adjust must be one of {", ".join(ose.ADJUST_TYPES)}')
        saves[_VARS['adjust']] = data['adjust']
    if 'entry_time' in data:
        try:
            h, m = [int(x) for x in str(data['entry_time']).split(':')[:2]]
        except (TypeError, ValueError):
            raise ValueError('entry_time must be HH:MM')
        if not (9 * 60 + 16) <= h * 60 + m <= (15 * 60 + 15):
            raise ValueError('entry_time must be 09:16-15:15')
        saves[_VARS['entry_time']] = f'{h:02d}:{m:02d}'
    for key in ('skip_one_night', 'compounding'):
        if key in data:
            saves[_VARS[key]] = 'true' if _truthy(data[key]) else 'false'
    for key, lo, hi, whole in (('target_pct', 0.1, 500, False), ('stop_pct', 0.1, 500, False),
                               ('adjust_pts', 25, 3000, False), ('max_adjust', 1, 10, True),
                               ('wings', 0, 3000, True), ('lock_at', 0, 500, False), ('lock_to', 0, 500, False),
                               ('lots', 1, ose.MAX_LOTS, True), ('start_capital', 1000, 1e9, False),
                               ('per_lot', 1000, 1e9, False)):
        if key in data:
            num(key, lo, hi, whole)
    merged = {**current, **data}
    if float(merged['lock_at']) and float(merged['lock_to']) >= float(merged['lock_at']):
        raise ValueError('lock_to must be below lock_at')
    if int(float(merged['wings'])) % ose.STRIKE_STEP:
        raise ValueError(f'wings must be a multiple of {ose.STRIKE_STEP}')
    return saves


def accounts(username) -> List[Dict[str, Any]]:
    """The accounts a live entry reaches: typed, active and BROKER_N_STR_ACTIVE.
    ``tradable`` is False, with the reason, for one that is refused — a broker this
    path has no NRML contract order for, or (fixed size) no BROKER_N_STR_LOTS."""
    from trading_app.app.routes.api import is_broker_active
    out = []
    compounding = settings(username)['compounding']
    for i in range(1, 21):
        b_type = _uvar(username, f'BROKER_{i}_TYPE').lower()
        if not b_type or not is_broker_active(username, i) or not _truthy(_uvar(username, f'BROKER_{i}_STR_ACTIVE', 'false')):
            continue
        try:
            lots = int(_uvar(username, f'BROKER_{i}_STR_LOTS')) or None
        except (TypeError, ValueError):
            lots = None
        why = None
        if b_type not in ('zerodha', 'kite'):
            why = f'{b_type} — not supported (zerodha only)'
        elif not compounding and not lots:
            why = f'no BROKER_{i}_STR_LOTS'
        out.append({'instance': i, 'type': b_type, 'lots': lots, 'tradable': why is None, 'why': why,
                    'name': _uvar(username, f'BROKER_{i}_NAME', f'{b_type} {i}')})
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


def booked(account_key: str) -> float:
    """Net ₹ this account ('paper', or a broker slot) has booked — what compounds."""
    return sum(float(((r.get('accounts') or {}).get(account_key) or {}).get('pnl') or 0) for r in load_history())


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


class StraddleAlgo:
    """One thread per user; one position at a time, across as many days as it lasts."""

    def __init__(self, username: str):
        self.username = username
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._svcs: Dict[int, Tuple[float, Any]] = {}
        self._alerted: Dict[str, str] = {}
        self._manual_exit = False
        self._reconciled_at = 0.0
        self._entry_day_cache: Optional[Tuple[str, bool]] = None
        self.state: Dict[str, Any] = {}

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self.state = load_state()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='StraddleAlgoThread')
        self._thread.start()
        _instances[self.username] = self
        logger.info('[Straddle] thread started')

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop(self.username, None)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_square_off(self) -> bool:
        """The tab's Square off: the next poll exits the position, like a stop."""
        if not self.state.get('position'):
            return False
        self._manual_exit = True
        return True

    def _save_state(self) -> None:
        with self._lock:
            tmp = STATE_FILE + '.tmp'
            try:
                with open(tmp, 'w') as f:
                    json.dump(self.state, f, indent=2, default=str)
                os.replace(tmp, STATE_FILE)
            except Exception as e:
                logger.error(f'[Straddle] state save failed: {e}')

    def _note(self, text: str, now: Optional[datetime] = None) -> None:
        logger.info(f'[Straddle] {text}')
        self.state.setdefault('log', []).append({'t': (now or _now()).strftime('%d-%b %H:%M'), 'text': text})
        self.state['log'] = self.state['log'][-200:]

    def _alert(self, category: str, title: str, summary: str = '', once: Optional[str] = None) -> None:
        """`once` = at most one a day under that key: a dead login fails every poll."""
        if once:
            today = _now().date().isoformat()
            if self._alerted.get(once) == today:
                return
            self._alerted[once] = today
        self._note(f'{title}: {summary}' if summary else title)
        try:
            from trading_app.service.notification_service import create_notification
            create_notification(category=category, title=title, summary=(summary or '')[:200],
                                data={'position': (self.state.get('position') or {}).get('id')})
        except Exception as e:
            logger.error(f'[Straddle] notification failed: {e}')
        token, chat = _uvar(self.username, 'TELEGRAM_BOT_TOKEN'), _uvar(self.username, 'TELEGRAM_CHAT_ID')
        if not (token and chat):
            return

        def _send():
            try:
                from trading_app.service.telegram_service import TelegramService
                TelegramService(token=token, chat_id=chat).send_text(f'{title}\n{summary}')
            except Exception as e:
                logger.error(f'[Straddle] Telegram alert failed: {e}')
        threading.Thread(target=_send, daemon=True, name='StraddleNotify').start()

    # ── the broker (overridable in tests) ───────────────────────────────

    def _svc(self, instance: int) -> Optional[Any]:
        """That account's KiteService, rebuilt every few minutes so the morning's
        fresh token is picked up without a restart."""
        at, svc = self._svcs.get(instance, (0.0, None))
        if svc is not None and _time.time() - at < _SVC_TTL_SECS:
            return svc
        from trading_app.app.utils.user_env import UserEnvManager
        from trading_app.service.provider_logic import get_kite
        from trading_app.service.kite_order_services import KiteService, apply_kite_proxy
        UserEnvManager._user_env_cache.pop(self.username, None)
        try:
            kite = get_kite(user=self.username, instance=instance)
            if not kite:
                return None
            if os.getenv('STATIC_IP_KEY', '').strip():
                apply_kite_proxy(kite)
            svc = KiteService(kite_instance=kite)
        except Exception as e:
            logger.warning(f'[Straddle] no Kite session at broker {instance}: {e}')
            return None
        self._svcs[instance] = (_time.time(), svc)
        return svc

    def _quote_svc(self) -> Optional[Any]:
        """Quotes and the contract list work in paper too: any active Kite login."""
        ids = [a['instance'] for a in accounts(self.username) if a['type'] in ('zerodha', 'kite')]
        for i in range(1, 21):
            if i not in ids and _uvar(self.username, f'BROKER_{i}_TYPE').lower() == 'zerodha':
                ids.append(i)
        for i in ids:
            svc = self._svc(i)
            if svc is not None:
                return svc
        return None

    def _instruments(self) -> List[Dict[str, Any]]:
        svc = self._quote_svc()
        return (svc.get_nfo_instruments(SYMBOL) or []) if svc else []

    def _contract(self, expiry: date, strike: int, right: str) -> Optional[Dict[str, Any]]:
        for o in self._instruments():
            exp = o.get('expiry')
            exp = exp.date() if hasattr(exp, 'date') else exp
            if exp == expiry and o.get('strike') == strike and o.get('instrument_type') == right:
                return o
        return None

    def _listed(self) -> List[date]:
        out = set()
        for o in self._instruments():
            exp = o.get('expiry')
            if exp and o.get('instrument_type') in ose.RIGHTS:
                out.add(exp.date() if hasattr(exp, 'date') else exp)
        return sorted(out)

    def _quotes(self, tsyms: List[str]) -> Tuple[Optional[float], Dict[str, float]]:
        """(NIFTY, {tradingsymbol: last price}) in one Kite call; (None, {}) without one."""
        from trading_app.service.strategy_signal_service import SYMBOL_CONFIG
        svc = self._quote_svc()
        if svc is None:
            return None, {}
        spot_key = (SYMBOL_CONFIG.get(SYMBOL) or {}).get('kite_key')
        keys = {f'NFO:{t}': t for t in tsyms}
        try:
            got = svc.kite.ltp([spot_key] + list(keys)) or {}
        except Exception as e:
            self._svcs.clear()
            logger.warning(f'[Straddle] quotes unavailable: {e}')
            return None, {}
        marks = {t: float(got[k]['last_price']) for k, t in keys.items()
                 if float((got.get(k) or {}).get('last_price') or 0) > 0}
        spot = float((got.get(spot_key) or {}).get('last_price') or 0)
        return (spot or None), marks

    def _send_order(self, instance: int, tsym: str, action: str, qty: int) -> Dict[str, Any]:
        """One MARKET NRML order on an exact contract. Never raises."""
        svc = self._svc(instance)
        if svc is None:
            return {'success': False, 'error': f'no Kite session at broker {instance}'}
        try:
            return svc.place_option_order(symbol=SYMBOL, strike=0, option_type='', transaction_type=action,
                                          quantity=int(qty), product='NRML', tradingsymbol=tsym)
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _order_state(self, instance: int, order_id: str) -> Tuple[str, int, float]:
        """('FILLED' | 'REJECTED' | 'PENDING', filled qty, average price)."""
        svc = self._svc(instance)
        st = svc.get_order_status(order_id) if svc is not None else {'success': False}
        if not st.get('success'):
            return 'PENDING', 0, 0.0
        status = str(st.get('status') or '').upper()
        fq, ap = int(st.get('filled_quantity') or 0), float(st.get('average_price') or 0)
        if status == 'COMPLETE':
            return 'FILLED', fq, ap
        if status in ('REJECTED', 'CANCELLED'):
            return 'REJECTED', fq, ap
        return 'PENDING', fq, ap

    def _broker_nets(self, instance: int) -> Dict[str, int]:
        """{tradingsymbol: signed NRML quantity} at the broker. Raises when unreadable."""
        svc = self._svc(instance)
        if svc is None:
            raise RuntimeError(f'no Kite session at broker {instance}')
        rows = (svc.kite.positions() or {}).get('net') or []
        return {r['tradingsymbol']: int(r.get('quantity') or 0) for r in rows
                if r.get('exchange') == 'NFO' and r.get('product') == 'NRML'}

    def _wait(self, secs: float) -> None:
        _time.sleep(secs)

    # ── one account's orders ────────────────────────────────────────────

    def _trade(self, acct: Dict[str, Any], tsym: str, action: str, qty: int, mark: float, lot_size: int) -> int:
        """Buy or sell `qty` of one contract at one account and write it to that
        account's books (`held`, `cash`). Returns the quantity that traded. Paper fills
        at the mark. Live waits for the broker's answer: a short refused for margin
        is accepted first and rejected a moment later."""
        sign = 1 if action == 'BUY' else -1
        if acct['key'] == 'paper':
            filled, price = qty, mark
        else:
            from trading_app.app.order_placement.op_signal_engine import lot_chunks
            ids, err = [], None
            for lots in lot_chunks(qty // lot_size):
                r = self._send_order(acct['instance'], tsym, action, lots * lot_size)
                if r.get('success'):
                    ids.append((str(r.get('order_id')), lots * lot_size))
                else:
                    err = r.get('error') or 'refused'
                    break
            filled, notional, unread = 0, 0.0, False
            for oid, sent in ids:
                state, fq, ap = 'PENDING', 0, 0.0
                for attempt in range(_FILL_POLLS):
                    state, fq, ap = self._order_state(acct['instance'], oid)
                    if state != 'PENDING':
                        break
                    self._wait(_FILL_POLL_WAIT)
                if state == 'PENDING':
                    # Accepted and never confirmed: carried as filled at the mark, flagged;
                    # the reconcile sweep compares it with the broker's position.
                    unread = True
                    fq, ap = sent, mark
                elif state == 'REJECTED' and not fq:
                    err = f'order {oid} rejected at the broker'
                filled += fq
                notional += fq * (ap or mark)
            if unread:
                acct['unconfirmed'] = True
            if err:
                self._alert('straddle_order_failed', f"Straddle — {action} {tsym} FAILED at {acct['name']}", str(err),
                            once=f"order-{acct['key']}-{tsym}-{action}")
            price = notional / filled if filled else 0.0
            acct.setdefault('orders', []).append({'tsym': tsym, 'action': action, 'qty': qty, 'filled': filled,
                                                  'price': round(price, 2), 'ids': [i for i, _ in ids]})
        if filled:
            acct['held'][tsym] = int(acct['held'].get(tsym, 0)) + sign * filled
            acct['cash'] = float(acct.get('cash') or 0) - sign * filled * price
            self._save_state()
        return filled

    def _open_account(self, pos: Dict[str, Any], acct: Dict[str, Any]) -> bool:
        """The entry at one account: wings first, then the shorts. A short the
        broker refuses unwinds whatever this account already bought or sold."""
        qty = acct['qty']
        for lg in sorted(pos['legs'], key=lambda x: -x['side']):       # buys first
            action = 'BUY' if lg['side'] > 0 else 'SELL'
            acct['legs_traded'] = int(acct.get('legs_traded') or 0) + 1
            if self._trade(acct, lg['tsym'], action, qty, lg['entry'], pos['lot_size']) < qty:
                self._alert('straddle_order_failed', f"Straddle — entry incomplete at {acct['name']}",
                            f"{lg['tsym']} did not fill; unwinding that account")
                self._flatten(pos, acct, {x['tsym']: x['entry'] for x in pos['legs']})
                acct['failed'] = True
                return False
        return True

    def _flatten(self, pos: Dict[str, Any], acct: Dict[str, Any], marks: Dict[str, float]) -> bool:
        """Close everything this account holds, shorts first, capped at what the
        broker says is open. True when flat. Never raises; retried by the caller."""
        if acct['key'] == 'paper':
            nets = None
        else:
            try:
                nets = self._broker_nets(acct['instance'])
            except Exception as e:
                self._alert('straddle_order_failed', f"Straddle — cannot read positions at {acct['name']}",
                            f'{e} — the exit is retried; square off by hand if this continues',
                            once=f"nets-{acct['key']}")
                return False
        for tsym, h in sorted(acct['held'].items(), key=lambda kv: kv[1]):
            h = int(h)
            if not h:
                continue
            mark = float(marks.get(tsym) or 0)
            real = abs(h)
            if nets is not None:
                net = nets.get(tsym, 0)
                real = min(abs(h), max(0, -net if h < 0 else net))
            if real < abs(h):
                self._alert('straddle_order_failed', f"Straddle — {tsym} differs at {acct['name']}",
                            f'ours {h}, broker {nets.get(tsym, 0) if nets else 0} — closed elsewhere? '
                            f'only the overlap is traded', once=f"diff-{acct['key']}-{tsym}")
                acct['unconfirmed'] = True
                # What the broker no longer holds is gone: off the books at the mark.
                gone = abs(h) - real
                sign = 1 if h < 0 else -1
                acct['held'][tsym] = h + sign * gone
                acct['cash'] = float(acct.get('cash') or 0) - sign * gone * mark
            if real:
                self._trade(acct, tsym, 'BUY' if h < 0 else 'SELL', real, mark, pos['lot_size'])
        return not any(int(v) for v in acct['held'].values())

    def _adjust_account(self, pos: Dict[str, Any], acct: Dict[str, Any], plan: Dict[str, Any],
                        added: List[Dict[str, Any]], marks: Dict[str, float]) -> None:
        """Buy-backs, then hedges, then the new shorts. A buy-back that fails stops this
        account's new shorts; `held` stays the truth and the exit closes it all."""
        qty = acct['qty']
        for lg in plan['close']:
            have = -int(acct['held'].get(lg['tsym'], 0))
            if have > 0 and self._trade(acct, lg['tsym'], 'BUY', have, float(marks[lg['tsym']]), pos['lot_size']) < have:
                self._alert('straddle_order_failed', f"Straddle — adjustment stopped at {acct['name']}",
                            f"could not buy back {lg['tsym']}; its new short was not sold")
                return
        for lg in sorted(added, key=lambda x: -x['side']):
            acct['legs_traded'] = int(acct.get('legs_traded') or 0) + 1
            action = 'BUY' if lg['side'] > 0 else 'SELL'
            self._trade(acct, lg['tsym'], action, qty, lg['entry'], pos['lot_size'])

    # ── the loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                now = _now()
                mins = now.hour * 60 + now.minute
                if now.weekday() >= 5 or mins >= _HARD_STOP_MIN:
                    self._save_state()
                    break
                try:
                    self.tick(now)
                except Exception as e:
                    logger.error(f'[Straddle] tick failed: {e}', exc_info=True)
                self._stop_event.wait(_POLL_SECS)
        finally:
            _instances.pop(self.username, None)

    def tick(self, now: datetime) -> None:
        if (not is_trading_day(now.date()) or (now.hour, now.minute, now.second) < _OPEN
                or (now.hour, now.minute) >= _CLOSE):
            return
        pos = self.state.get('position')
        if pos:
            self._manage(pos, now)
            self._note_overlap(now)
        else:
            self._maybe_enter(now)

    # ── entering ────────────────────────────────────────────────────────

    def _is_entry_day(self, today: date, s: Dict[str, Any]) -> bool:
        key = f"{today}|{s['entry_day']}|{s['cadence']}"
        if not self._entry_day_cache or self._entry_day_cache[0] != key:
            self._entry_day_cache = (key, eng.is_entry_day(today, s['entry_day'], s['cadence']))
        return self._entry_day_cache[1]

    def _note_overlap(self, now: datetime) -> None:
        """An entry day that finds the last trade still open: one at a time, not doubled."""
        s = settings(self.username)
        today = now.date()
        if not s['enabled'] or self.state.get('last_entry_date') == today.isoformat():
            return
        h, m = [int(x) for x in s['entry_time'].split(':')[:2]]
        if (now.hour, now.minute) >= (h, m) and self._is_entry_day(today, s):
            self.state['last_entry_date'] = today.isoformat()
            self._note(f'{today}: entry day, but the last position is still open — not doubled', now)
            self._save_state()

    def _maybe_enter(self, now: datetime) -> None:
        s = settings(self.username)
        today = now.date()
        if not s['enabled'] or self.state.get('last_entry_date') == today.isoformat():
            return
        h, m = [int(x) for x in s['entry_time'].split(':')[:2]]
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < at or not self._is_entry_day(today, s):
            return
        if now > at + timedelta(minutes=eng.ENTRY_WINDOW_MINS):
            self.state['last_entry_date'] = today.isoformat()
            self._alert('straddle_order_failed', f"Straddle — {today} entry missed",
                        f"the algo was not able to enter by {s['entry_time']} + {eng.ENTRY_WINDOW_MINS} min — not chased")
            self._save_state()
            return
        self._enter(s, now)

    def _enter(self, s: Dict[str, Any], now: datetime) -> None:
        today = now.date()
        listed = self._listed()
        if not listed:
            self._alert('straddle_order_failed', 'Straddle — no contract list',
                        'no Kite login answered; retrying until the window ends', once='no-contracts')
            return
        expiry = eng.expiry_for(today, s['cadence'], listed)
        if expiry is None:
            self.state['last_entry_date'] = today.isoformat()
            self._alert('straddle_order_failed', 'Straddle — no listed expiry', f'{today} {s["cadence"]}: not traded')
            self._save_state()
            return
        if s['cadence'] == 'weekly' and s['skip_one_night'] and eng.is_one_night(today, expiry):
            self.state['last_entry_date'] = today.isoformat()
            self._note(f'{today}: expiry {expiry} is the next session — skipped (Skip one-night trades)', now)
            self._save_state()
            return
        spot, _ = self._quotes([])
        if not spot:
            self._alert('straddle_order_failed', 'Straddle — no NIFTY quote', 'retrying until the window ends', once='no-spot')
            return
        strike = ose.atm_strike(spot)
        wanted = [('CE', strike, -1), ('PE', strike, -1)]
        if s['wings']:
            wanted += [('CE', strike + s['wings'], 1), ('PE', strike - s['wings'], 1)]
        contracts = {(r, k): self._contract(expiry, k, r) for r, k, _ in wanted}
        missing = [f'{k} {r}' for (r, k), c in contracts.items() if not c]
        if missing:
            self._alert('straddle_order_failed', 'Straddle — contract not listed',
                        f"{', '.join(missing)} on {expiry}; retrying", once=f'no-contract-{expiry}')
            return
        _, marks = self._quotes([c['tradingsymbol'] for c in contracts.values()])
        if any(c['tradingsymbol'] not in marks for c in contracts.values()):
            self._alert('straddle_order_failed', 'Straddle — no option quote', 'retrying until the window ends', once='no-opt-quote')
            return
        lot_size = int(next(iter(contracts.values())).get('lot_size') or 0)
        if not lot_size:
            self._alert('straddle_order_failed', 'Straddle — lot size unknown', 'no order sent', once='no-lot')
            return
        stamp = _stamp(now)
        legs = [eng.make_leg(r, k, side, marks[contracts[(r, k)]['tradingsymbol']],
                             contracts[(r, k)]['tradingsymbol'], stamp) for r, k, side in wanted]
        params = {k: s[k] for k in RULE_KEYS}
        pos = eng.new_position(f'str-{today}', s['mode'], params, today, expiry, spot, strike, legs, stamp, lot_size)
        pos.update(cadence=s['cadence'], entry_day=s['entry_day'], entering=True, last_bar=f"{today} {eng.bar_key(now.hour, now.minute)}")
        if pos['premium'] <= 0:
            self.state['last_entry_date'] = today.isoformat()
            self._alert('straddle_order_failed', 'Straddle — no credit', f"premium {pos['premium']}: not traded")
            self._save_state()
            return
        book = self._accounts_for(pos, s)
        self.state['last_entry_date'] = today.isoformat()      # once a day, whatever follows
        if not book:
            self._alert('straddle_order_failed', 'Straddle — no account to trade',
                        'live needs a zerodha account with BROKER_N_STR_ACTIVE=true and lots above 0; no order sent')
            self._save_state()
            return
        pos['accounts'] = book
        self.state['position'] = pos
        self._save_state()                                      # before the first order
        live = [a for a in book.values() if self._open_account(pos, a)]
        pos['entering'] = False
        if not live:
            self._alert('straddle_order_failed', 'Straddle — entry FAILED', 'no account holds the position')
            if any(a.get('orders') and any(o['filled'] for o in a['orders']) for a in book.values()):
                # Something traded before the refusal: finished and booked like any exit.
                self._begin_exit(pos, 'Entry failed', marks, None, now)
            else:
                self.state['position'] = None
                self._save_state()
            return
        self._note(f"{pos['mode'].upper()} SOLD {strike} straddle exp {expiry} for {pos['premium']} "
                   f"(NIFTY {spot:,.2f}) — " + ', '.join(f"{a['name']} {a['lots']} lot(s)" for a in live), now)
        self._alert('straddle_entry', f"Straddle {pos['mode']} — SOLD NIFTY {strike}",
                    f"exp {expiry} · premium {pos['premium']} · " + ', '.join(f"{a['name']} {a['lots']}L" for a in live))
        self._save_state()

    def _accounts_for(self, pos: Dict[str, Any], s: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        def row(key, name, instance, lots):
            return {'key': key, 'name': name, 'instance': instance, 'lots': lots, 'qty': lots * pos['lot_size'],
                    'held': {}, 'cash': 0.0, 'legs_traded': 0, 'flat': False}
        if pos['mode'] == 'paper':
            lots = eng.lots_for(s['compounding'], s['lots'], s['start_capital'], booked('paper'), s['per_lot'])
            return {'paper': row('paper', 'Paper', 0, lots)} if lots else {}
        out = {}
        for a in accounts(self.username):
            key = str(a['instance'])
            if not a['tradable']:
                self._alert('straddle_order_failed', f"Straddle — {a['name']} refused", a['why'], once=f'refused-{key}')
                continue
            lots = eng.lots_for(s['compounding'], a['lots'], s['start_capital'], booked(key), s['per_lot'])
            if lots:
                out[key] = row(key, a['name'], a['instance'], lots)
        return out

    # ── managing ────────────────────────────────────────────────────────

    def _manage(self, pos: Dict[str, Any], now: datetime) -> None:
        today = now.date()
        expiry = date.fromisoformat(pos['expiry'])
        if pos.get('entering'):
            pos['entering'] = False
            self._alert('straddle_order_failed', 'Straddle — restarted in the middle of an entry',
                        'CHECK THE BROKER: only the orders already recorded are managed')
        if pos.get('exit'):
            self._finish_exit(pos, now)
            return
        for acct in pos['accounts'].values():      # an entry's unwind that did not finish
            if acct.get('failed') and any(int(v) for v in acct['held'].values()):
                self._flatten(pos, acct, self.state.get('marks') or {lg['tsym']: lg['entry'] for lg in pos['legs']})
        if today > expiry:
            self._expired_unattended(pos, now)
            return
        spot, marks = self._quotes([lg['tsym'] for lg in eng.open_legs(pos)])
        expiry_due = today == expiry and (now.hour, now.minute) >= eng.EXPIRY_EXIT
        m = eng.mtm(pos, marks)
        if m is None:
            self._alert('straddle_order_failed', 'Straddle — OPEN POSITION HAS NO QUOTES',
                        'target and stop are not being watched: is Kite logged in? Log in or square off by hand',
                        once='no-quotes')
            if expiry_due:
                self._begin_exit(pos, 'Expiry', marks, m, now)
            return
        self.state.update(spot=spot, marks=marks, mtm=round(m, 2), updated=now.strftime('%H:%M:%S'))
        reason = ('Manual' if self._manual_exit else None) or eng.exit_reason(pos, m) or ('Expiry' if expiry_due else None)
        if reason:
            self._manual_exit = False
            self._begin_exit(pos, reason, marks, m, now)
            return
        self._reconcile(pos)
        if not spot or now.hour * 60 + now.minute < 9 * 60 + 20:
            return
        key = f"{today} {eng.bar_key(now.hour, now.minute)}"
        if pos.get('last_bar') == key:
            return
        was_armed = pos['lock_armed']
        plan = eng.on_bar(pos, spot, m, today)
        if pos['lock_armed'] and not was_armed:
            p = pos['params']
            self._note(f"up {100 * m / pos['premium']:.1f}% — profit lock armed at {p['lock_to']}%", now)
        if plan and not self._do_adjust(pos, plan, marks, now):
            self._save_state()
            return                                 # a strike without a quote: the next poll tries again
        pos['last_bar'] = key
        self._save_state()

    def _do_adjust(self, pos: Dict[str, Any], plan: Dict[str, Any], marks: Dict[str, float], now: datetime) -> bool:
        expiry = date.fromisoformat(pos['expiry'])
        names = {}
        for right, _ in plan['opens']:
            c = self._contract(expiry, plan['atm'], right)
            if not c:
                self._alert('straddle_order_failed', 'Straddle — adjustment waits',
                            f"{plan['atm']} {right} is not listed", once=f"adj-{plan['atm']}{right}")
                return False
            names[right] = c['tradingsymbol']
        _, fresh = self._quotes(list(names.values()))
        if any(t not in fresh for t in names.values()):
            return False
        all_marks = {**marks, **fresh}
        added = eng.apply_adjust(pos, plan, all_marks, lambda r, _k: names[r], _stamp(now))
        self._save_state()
        for acct in pos['accounts'].values():
            if not acct.get('failed'):
                self._adjust_account(pos, acct, plan, added, all_marks)
        text = (f"NIFTY {plan['spot']:,.0f}: " + ', '.join(f'out {lg["right"]} {lg["strike"]}' for lg in plan['close'])
                + (' · ' if plan['close'] else '')
                + ', '.join(f"{'buy' if lg['side'] > 0 else 'sell'} {lg['right']} {lg['strike']} @{lg['entry']}" for lg in added))
        self._note(f"adjusted — {text}", now)
        self._alert('straddle_adjust', f"Straddle {pos['mode']} — adjusted", text)
        return True

    def _reconcile(self, pos: Dict[str, Any]) -> None:
        """Every minute, live: what we think each account holds against the broker."""
        if pos['mode'] != 'live' or _time.time() - self._reconciled_at < _RECONCILE_SECS:
            return
        self._reconciled_at = _time.time()
        for acct in pos['accounts'].values():
            if acct.get('failed') or acct['key'] == 'paper':
                continue
            try:
                nets = self._broker_nets(acct['instance'])
            except Exception as e:
                self._alert('straddle_order_failed', f"Straddle — cannot read positions at {acct['name']}", str(e),
                            once=f"nets-{acct['key']}")
                continue
            for tsym, h in acct['held'].items():
                if int(h) != nets.get(tsym, 0):
                    self._alert('straddle_order_failed', f"Straddle — {tsym} differs at {acct['name']}",
                                f'ours {h}, broker {nets.get(tsym, 0)}', once=f"diff-{acct['key']}-{tsym}")

    def _begin_exit(self, pos: Dict[str, Any], reason: str, marks: Dict[str, float], m: Optional[float], now: datetime) -> None:
        last = {**(self.state.get('marks') or {}), **marks}
        eng.close_all(pos, last, _stamp(now))
        pos['exit'] = {'reason': reason, 'at': _stamp(now), 'points': round(eng.mtm(pos, last) or 0.0, 2), 'marks': last,
                       'spot': self.state.get('spot')}
        self._note(f"exit: {reason} at {pos['exit']['points']:+.2f} pts", now)
        self._save_state()
        self._finish_exit(pos, now)

    def _finish_exit(self, pos: Dict[str, Any], now: datetime) -> None:
        """Retried every poll until every account is flat, then booked."""
        _, fresh = self._quotes([t for a in pos['accounts'].values() for t, h in a['held'].items() if int(h)])
        marks = {**pos['exit']['marks'], **fresh}
        flat = True
        for acct in pos['accounts'].values():
            if not self._flatten(pos, acct, marks):
                flat = False
                self._alert('straddle_order_failed', f"Straddle — EXIT NOT DONE at {acct['name']}",
                            'retrying every poll; square off by hand if this continues', once=f"exit-{acct['key']}")
            acct['flat'] = not any(int(v) for v in acct['held'].values())
        self._save_state()
        if flat:
            self._book(pos, now)

    def _expired_unattended(self, pos: Dict[str, Any], now: datetime) -> None:
        """The contract expired with the app down: the exchange settled it. Off the
        books at the last price seen, flagged."""
        self._alert('straddle_order_failed', 'Straddle — contract expired while not watched',
                    f"{pos['expiry']}: settled by the exchange; booked at the last quote seen — CHECK THE BROKER")
        last = self.state.get('marks') or {}
        eng.close_all(pos, last, _stamp(now))
        pos['exit'] = {'reason': 'Expired unattended', 'at': _stamp(now), 'points': round(eng.mtm(pos, last) or 0.0, 2),
                       'marks': last, 'spot': self.state.get('spot')}
        for acct in pos['accounts'].values():
            for tsym, h in list(acct['held'].items()):
                acct['cash'] = float(acct.get('cash') or 0) + int(h) * float(last.get(tsym) or 0)
                acct['held'][tsym] = 0
            acct['unconfirmed'] = True
        self._book(pos, now)

    def _book(self, pos: Dict[str, Any], now: datetime) -> None:
        rows, total = {}, 0.0
        for key, a in pos['accounts'].items():
            if a.get('failed') and not a.get('legs_traded'):
                continue
            gross = float(a.get('cash') or 0)
            brokerage = eng.BROKERAGE_PER_LEG * int(a.get('legs_traded') or 0)
            net = round(gross - brokerage, 2)
            total += net
            rows[key] = {'name': a['name'], 'lots': a['lots'], 'qty': a['qty'], 'gross': round(gross, 2),
                         'brokerage': brokerage, 'pnl': net, 'failed': a.get('failed') or None,
                         'unconfirmed': a.get('unconfirmed') or None}
        ex = pos['exit']
        row = {
            'id': pos['id'], 'mode': pos['mode'], 'cadence': pos.get('cadence'), 'entry_day': pos.get('entry_day'),
            'date': pos['entry_date'], 'entry_time': pos['opened_at'], 'expiry': pos['expiry'],
            'exit_time': ex['at'], 'reason': ex['reason'], 'spot': pos['spot'], 'strike': pos['strike'],
            'premium': pos['premium'], 'points': ex['points'],
            'pct': round(100.0 * ex['points'] / pos['premium'], 2) if pos['premium'] else 0.0,
            'params': pos['params'], 'adjusts': pos['adjusts'],
            'legs': [{k: lg[k] for k in ('right', 'strike', 'side', 'entry', 'exit', 'tsym')} for lg in pos['legs']],
            'accounts': rows, 'pnl': round(total, 2),
        }
        _append_history(row)
        self.state['position'] = None
        self.state.pop('marks', None)
        self.state.pop('mtm', None)
        self._note(f"booked {ex['reason']}: {ex['points']:+.2f} pts · ₹{total:,.0f}", now)
        self._alert('straddle_exit', f"Straddle {pos['mode']} — {ex['reason']}",
                    f"{pos['strike']} exp {pos['expiry']} · {ex['points']:+.2f} pts · ₹{total:,.0f}")
        self._save_state()


def ensure_running(username: str, source: str = '') -> bool:
    """Start the thread for the session if it is enabled — or holds a position,
    which is managed whatever the tab's Stop says."""
    now = _now()
    mins = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (9 * 60 <= mins < _HARD_STOP_MIN):
        return False
    if not settings(username)['enabled'] and not load_state().get('position'):
        return False
    inst = get_instance(username)
    if inst and inst.is_running():
        return False
    logger.info(f'[Straddle {source}] starting thread for {username}')
    StraddleAlgo(username).start()
    return True
