"""The Telegram-call state machine: a call arrives, a trade is run to its end.

The listener (``tg_calls_listener``) hands in a parsed tip; everything from
there to flat happens here, per broker:

    stop-limit BUY rests  →  it fills  →  SL-M SELL for the filled qty rests,
    target 1 is watched  →  the stop fires, or T1 is touched and the position
    is sold at market  →  flat.

Three things about the shape:

**The stop covers the whole position.** A call trades one size and one
target, so the stop simply covers all of it — an exchange-side exit for the
full position, needing nothing from the app once it is resting.

**Target 1 rests nowhere.** Resting a LIMIT sell for the full quantity next to
a full-quantity stop would be twice the held quantity working on the sell
side: the broker margins the second one as a fresh short, and a fast spike
can fill both. So T1 is only a level this loop watches; when the premium
touches it the stop is cancelled and the lot is sold at market. That needs
the app alive, which the LaunchAgent sees to. The stop does not.

**A call that cannot rest is skipped, not chased.** The entry is a stop BUY,
which must sit above the market. If the premium has already run past the
tip's price by the time the message lands, nothing is placed and an alert
says why. Targets 2 and 3 are read and ignored.

Every leg is an ordinary ``MineOrderStore`` record with ``strategy='op'`` and
``source='telegram'``, so the Order Placement strip's price box, ✕,
reconciliation sweep and Exit all work on them with no special case.
"""

import math
import threading
import time as _time
from datetime import date, datetime

from trading_app.app.order_placement import op_signal_engine as _ops
from trading_app.app.order_placement.op_signal_engine import (
    _cancel_leg, _exit_cutoff_mins, _is_resting, _now_mins, _place_stop_leg,
    _record, _session, exit_side, lot_chunks, reached, remember_session,
    signal_records)
from trading_app.app.order_placement.op_signal_store import (
    BROKER_DONE_STAGES, STAGE_DEAD, STAGE_FLAT, STAGE_LIVE, STAGE_NO_FILL,
    STAGE_PENDING_ENTRY)
from trading_app.app.order_placement.tg_call_store import TgCallStore, TgTradeHistory
from trading_app.app.utils.logger import logger

_POLL_SECS = 3
_PARTIAL_SETTLE_SECS = 20
# A market exit fills in seconds. A slot still without its exit fill this
# long after going flat is booked on what is known and flagged, rather than
# holding the engine up for a fill the broker is never going to report.
_BOOK_SETTLE_SECS = 90
_HARD_STOP_MIN = 15 * 60 + 29
_OPEN_MIN = 9 * 60 + 15

_DEFAULT_LIMIT_PCT = 1.0
_STOPPABLE = ('zerodha', 'kite', 'fyers')

_thread = None
_stop_event = threading.Event()
_thread_lock = threading.Lock()

# Once-per-day alert keys, so a slot that is mis-flagged does not ring the
# bell on every call of the day. The log carries every occurrence.
_alerted = {}


def _uvar(username, name, default=''):
    from trading_app.app.utils.user_env import UserEnvManager
    v = UserEnvManager.get_user_var(username, name, default)
    return default if v in (None, '') else v


def _flag(username, name, default='false') -> bool:
    return str(_uvar(username, name, default)).strip().lower() in ('true', '1', 'yes')


def _int(username, name):
    try:
        raw = _uvar(username, name)
        return int(str(raw).strip()) if raw not in (None, '') else None
    except (TypeError, ValueError):
        return None


def is_active(username) -> bool:
    """The master switch. Read on every call, never cached, so the one way to
    stop the next call from trading is guaranteed to work."""
    return _flag(username, 'TG_CALLS_ACTIVE')


# ── alerts ────────────────────────────────────────────────────────────────

def _alert(username, category, title, summary, data=None, once_key=None) -> None:
    """In-app bell plus a Telegram message, each behind its own flag.

    ``once_key`` collapses repeats: the same key alerts once per day.
    """
    if once_key:
        today = date.today().isoformat()
        if _alerted.get((category, once_key)) == today:
            return
        _alerted[(category, once_key)] = today

    if _flag(username, 'TG_CALLS_NOTIFY', 'true'):
        try:
            import json
            from trading_app.service.notification_service import create_notification
            # The parsed plan carries a date (the expiry); the bell stores JSON.
            safe = json.loads(json.dumps(data or {}, default=str))
            create_notification(category=category, title=title,
                                summary=(summary or '')[:200], data=safe)
        except Exception as e:
            logger.error(f"[TgCall] notification failed: {e}")

    if not _flag(username, 'TG_CALLS_TELEGRAM', 'true'):
        return
    token = _uvar(username, 'TELEGRAM_BOT_TOKEN')
    chat_id = _uvar(username, 'TELEGRAM_CHAT_ID')
    if not (token and chat_id):
        return

    def _send():
        try:
            from trading_app.service.telegram_service import TelegramService
            r = TelegramService(token=token, chat_id=chat_id).send_text(f"{title}\n{summary}")
            if not r.get('success'):
                logger.error(f"[TgCall] Telegram alert failed: {r.get('error')}")
        except Exception as e:
            logger.error(f"[TgCall] Telegram alert failed: {e}")

    threading.Thread(target=_send, daemon=True, name='TgCallNotify').start()


# ── sizing ────────────────────────────────────────────────────────────────

def tg_targets(username) -> list:
    """The brokers a call would reach: typed, active, and opted in with
    ``BROKER_N_TG_ACTIVE``. ``lots`` is ``BROKER_N_TG_LOTS`` or None — there is
    no fallback to the OP sizes, which size a different kind of order."""
    from trading_app.app.routes.api import is_broker_active
    out = []
    for i in range(1, 21):
        b_type = str(_uvar(username, f'BROKER_{i}_TYPE')).strip().lower()
        if not b_type or not is_broker_active(username, i):
            continue
        if not _flag(username, f'BROKER_{i}_TG_ACTIVE'):
            continue
        lots = _int(username, f'BROKER_{i}_TG_LOTS')
        out.append({'instance': i, 'type': b_type,
                    'name': _uvar(username, f'BROKER_{i}_NAME', f'Broker {i}'),
                    'lots': lots if lots and lots > 0 else None})
    return out


def entry_limit(trigger: float, pct: float) -> float:
    """The limit that rides above the trigger, rounded UP to the 5-paise tick.
    Up, because a limit rounded below the trigger is not a valid stop BUY."""
    raw = float(trigger) * (1 + float(pct) / 100.0)
    return math.ceil(raw * 20 - 1e-9) / 20


def limit_pct(username) -> float:
    try:
        return float(_uvar(username, 'TG_ENTRY_LIMIT_PCT', _DEFAULT_LIMIT_PCT))
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT_PCT


# ── keeping the road clear ────────────────────────────────────────────────

def prewarm(username, symbols=('NIFTY', 'BANKNIFTY', 'SENSEX')) -> dict:
    """Pay every cold-cache cost before a call arrives, not on it.

    The first call of the day was answered twelve seconds late: the option
    chain (a full Fyers symbol-master download), the strike-token cache and
    the Kite instrument dump were all cold, and the entry the channel named
    at 227 was read against a premium already at 236. Everything below is
    cached process-wide with a TTL of 30 minutes or the day, so this is
    called when the listener connects and again from the five-minute
    watchdog — near-free once warm, and a call then costs one live quote and
    the order itself.
    """
    from trading_app.app.routes.api import get_kite, resolve_standard_lot
    from trading_app.app.routes.order_placement_api import _chain_meta, _spot, option_ltp
    from trading_app.service.kite_order_services import KiteService

    timings = {}

    def timed(label, fn):
        t = _time.time()
        try:
            fn()
        except Exception as e:
            logger.debug(f"[TgCall] prewarm {label} failed: {e}")
        timings[label] = round(_time.time() - t, 2)

    for sym in symbols:
        timed(f'chain:{sym}', lambda s=sym: _chain_meta(s))
        timed(f'lot:{sym}', lambda s=sym: resolve_standard_lot(s))
        # One quote at the money loads the symbol master behind the
        # strike-token cache; the strike a call names is then a local lookup.
        def _atm(s=sym):
            meta = _chain_meta(s) or {}
            spot = _spot(s)
            step = int(meta.get('step') or 50)
            if spot and step:
                option_ltp(s, int(round(spot / step) * step), 'CE')
        timed(f'ltp:{sym}', _atm)

    # The Kite instrument dump for every account a call would reach, so the
    # tradingsymbol lookup at placement time reads the file, not the API.
    for t in tg_targets(username):
        if t['type'] in ('zerodha', 'kite') and t['lots']:
            def _dump(i=t['instance']):
                kite = get_kite(instance=i)
                if kite:
                    KiteService(kite_instance=kite).get_option_symbol('NIFTY', 23000, 'CE')
            timed(f'kite:{t["instance"]}', _dump)

    slow = {k: v for k, v in timings.items() if v >= 0.5}
    logger.info(f"[TgCall] prewarm done in {round(sum(timings.values()), 2)}s"
                + (f" — cold: {slow}" if slow else ""))
    return timings


# ── taking a call ─────────────────────────────────────────────────────────

def validate_call(username, plan, chain_meta=None, ltp=None):
    """Every refusal that can be made before a broker is touched, or None."""
    from trading_app.app.routes.order_placement_api import OP_SYMBOLS, stop_direction_error

    symbol = str(plan.get('symbol') or '').upper()
    if symbol not in OP_SYMBOLS:
        return f'Only {", ".join(OP_SYMBOLS)} calls are traded — not {symbol or "that"}'
    if str(plan.get('option_type') or '').upper() not in ('CE', 'PE'):
        return 'option_type must be CE or PE'
    if str(plan.get('action') or 'BUY').upper() != 'BUY':
        return 'Only BUY calls are traded — a SELL would need short-option margin'

    try:
        strike = int(plan.get('strike') or 0)
        entry = float(plan.get('entry') or 0)
        stop = float(plan.get('stop') or 0)
        targets = [float(t) for t in (plan.get('targets') or [])]
    except (TypeError, ValueError):
        return 'The strike, entry, stop and targets must all be numbers'
    if strike <= 0 or entry <= 0 or stop <= 0:
        return 'The strike, entry and stop must all be above zero'
    if not targets:
        return 'The call has no target'
    if stop >= entry:
        return f'The stop {stop} is not below the entry {entry}'
    if targets[0] <= entry:
        return f'Target 1 {targets[0]} is not above the entry {entry}'

    meta = chain_meta or {}
    step = int(meta.get('step') or 0)
    if step and strike % step:
        return f'{strike} is not a {symbol} strike — they step by {step}'
    want, have = plan.get('expiry'), meta.get('expiry')
    if want and have and str(want) != str(have):
        return (f'The call is for the {want} expiry but the order path trades the '
                f'front expiry ({have})')

    return stop_direction_error('BUY', entry, ltp)


def _in_hours(username) -> bool:
    now = datetime.now(_ops._IST)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return _OPEN_MIN <= mins < _exit_cutoff_mins(username)


def take_call(username, plan, meta=None) -> dict:
    """Place the entry at every sized broker and record the call.

    Runs on a worker thread the listener starts — never on its asyncio loop,
    since every broker call below is blocking HTTP. Returns a dict for the
    tests; the caller ignores it.
    """
    from trading_app.app.routes.api import dispatch_stop_to_brokers, resolve_standard_lot
    from trading_app.app.routes.order_placement_api import (OP_STRATEGY, _chain_meta,
                                                            option_ltp)
    from trading_app.app.utils.mine_order_store import MineOrderStore

    t_start = _time.time()
    meta = meta or {}
    symbol = str(plan.get('symbol') or '').upper()
    strike = int(plan.get('strike') or 0)
    option_type = str(plan.get('option_type') or '').upper()
    label = f'{symbol} {strike} {option_type}'
    text = (meta.get('text') or plan.get('source_text') or '')[:2000]

    def skipped(reason, once_key=None):
        logger.warning(f"[TgCall] skipped {label}: {reason}")
        _alert(username, 'tg_call_skipped', f'Telegram call skipped — {label}', reason,
               {'plan': plan, 'reason': reason, 'text': text}, once_key=once_key)
        return {'success': False, 'skipped': True, 'error': reason}

    if not is_active(username):
        return skipped('TG_CALLS_ACTIVE is off — call parsed but not traded')
    if not _in_hours(username):
        return skipped('outside trading hours')

    try:
        chain = _chain_meta(symbol) if symbol else {}
    except Exception as e:
        logger.warning(f"[TgCall] chain meta unavailable: {e}")
        chain = {}
    t_chain = _time.time()
    try:
        ltp = option_ltp(symbol, strike, option_type) if strike else None
    except Exception as e:
        logger.warning(f"[TgCall] quote unavailable: {e}")
        ltp = None
    t_ltp = _time.time()
    logger.info(f"[TgCall] {label}: chain {t_chain - t_start:.2f}s, quote {t_ltp - t_chain:.2f}s "
                f"→ ltp {ltp}, entry {plan.get('entry')}")

    error = validate_call(username, plan, chain, ltp)
    if error:
        return skipped(error)

    entry, stop = float(plan['entry']), float(plan['stop'])
    target = float(plan['targets'][0])
    limit = entry_limit(entry, limit_pct(username))

    lot_size = resolve_standard_lot(symbol)
    if not lot_size:
        return skipped(f'could not resolve the {symbol} lot size — instrument list unavailable')

    eligible = []
    for t in tg_targets(username):
        if t['type'] not in _STOPPABLE:
            _alert(username, 'tg_call_order_failed',
                   f"Telegram calls: {t['name']} cannot hold a stop",
                   f"BROKER_{t['instance']}_TG_ACTIVE is on but {t['type']} has no "
                   f"stop-order path here. Turn it off; this slot is skipped.",
                   {'instance': t['instance'], 'type': t['type']},
                   once_key=f"unstoppable:{t['instance']}")
            continue
        if t['lots'] is None:
            _alert(username, 'tg_call_order_failed',
                   f"Telegram calls: {t['name']} has no size",
                   f"BROKER_{t['instance']}_TG_LOTS is unset — this slot is skipped.",
                   {'instance': t['instance']}, once_key=f"unsized:{t['instance']}")
            continue
        eligible.append(t)
    if not eligible:
        return skipped('no broker has BROKER_N_TG_ACTIVE=true with BROKER_N_TG_LOTS set',
                       once_key='no-brokers')

    session_data = _session(username)
    call = TgCallStore.create({
        'username': username,
        'symbol': symbol, 'strike': strike, 'option_type': option_type,
        'action': 'BUY', 'entry': entry, 'stop': stop, 'targets': [float(t) for t in plan['targets']],
        'target': target, 'limit': limit,
        'expiry': str(plan.get('expiry') or '') or None,
        'lot_size': lot_size, 'ltp_at_call': ltp,
        'phase': 'ARMED',
        'message_id': meta.get('message_id'), 'chat_id': meta.get('chat_id'),
        'source_text': text,
        'brokers': {},
    })
    call_id = call['id']

    def place_at(t):
        """This account's entry, chunked by the freeze limit. Never raises."""
        instance, lots = t['instance'], t['lots']
        results = []
        for chunk in lot_chunks(lots):
            try:
                results += dispatch_stop_to_brokers(
                    symbol=symbol, strike=strike, option_type=option_type,
                    trigger_price=entry, action='BUY',
                    username=username, session_data=session_data,
                    standard_lot=lot_size,
                    gate=lambda i, _b, _want=instance: i == _want,
                    lots_for=lambda _i, _n=chunk: _n,
                    log_tag=f'TgCall {call_id} entry', limit_price=limit,
                )
            except Exception as e:
                logger.error(f"[TgCall] {call_id} entry at broker {instance} failed: {e}",
                             exc_info=True)
                results.append({'broker': t['type'], 'instance': instance,
                                'success': False, 'error': str(e)})
        return results

    # Every account at once, not one after another: each placement is a
    # broker round-trip, and the second account should not enter a second
    # later than the first because of it.
    if len(eligible) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(eligible), thread_name_prefix='TgCallEntry') as pool:
            placed = list(pool.map(place_at, eligible))
    else:
        placed = [place_at(t) for t in eligible]

    summary = []
    for t, results in zip(eligible, placed):
        instance, lots = t['instance'], t['lots']
        ok = [r for r in results if r.get('success')]
        error = next((r.get('error') for r in results if not r.get('success')), None)

        record = MineOrderStore.add_order({
            'mode': 'broker',
            'symbol': symbol, 'strike': strike, 'option_type': option_type,
            'action': 'BUY',
            'strategy': OP_STRATEGY,
            'order_type': 'SL', 'type': 'SL',
            'instrument': 'BFO' if symbol == 'SENSEX' else 'NFO',
            'price': limit, 'trigger_price': entry,
            'quantity': sum(int(r.get('quantity') or 0) for r in ok),
            'status': 'OPEN' if ok else 'REJECTED',
            'username': username,
            'source': 'telegram',
            'signal_id': call_id, 'leg': 'ENTRY',
            'broker_order_ids': results,
        })

        TgCallStore.update_broker(call_id, instance, {
            'instance': instance, 'broker': t['type'], 'name': t['name'],
            'lots': lots,
            'stage': STAGE_PENDING_ENTRY if ok else STAGE_DEAD,
            'entry_record_id': record['id'],
            'legs': {}, 'error': None if ok else error,
        })
        summary.append({'broker': t['type'], 'instance': instance, 'name': t['name'],
                        'lots': lots, 'success': bool(ok), 'error': error,
                        'order_id': record['id']})

    accepted = [r for r in summary if r['success']]
    if not accepted:
        TgCallStore.update(call_id, {'phase': 'FAILED'})
        reason = next((r['error'] for r in summary if r['error']), 'every broker refused the entry')
        _alert(username, 'tg_call_order_failed', f'Telegram call — entry refused: {label}',
               reason, {'call_id': call_id, 'summary': summary})
        return {'success': False, 'error': reason, 'call_id': call_id, 'summary': summary}

    TgCallStore.update(call_id, {'phase': 'ENTRY_PENDING'})
    brokers = ', '.join(f"{r['name']} x{r['lots']}" for r in accepted)
    _alert(username, 'tg_call_taken', f'Telegram call taken — {label}',
           f'SL-L BUY trigger {entry} limit {limit} · SL {stop} · T1 {target} · {brokers}',
           {'call_id': call_id, 'plan': plan, 'limit': limit, 'summary': summary})
    logger.info(f"[TgCall] {call_id} armed {label} trigger={entry} limit={limit} sl={stop} "
                f"t1={target} → {len(accepted)}/{len(summary)} brokers in "
                f"{_time.time() - t_start:.2f}s from receipt")
    ensure_running(username, source='call')
    return {'success': True, 'call_id': call_id, 'summary': summary}


# ── settling the entry ────────────────────────────────────────────────────

def _settle_entry(call, slot, username, session_data, books) -> None:
    from trading_app.app.routes.api import leg_fills
    from trading_app.app.utils.mine_order_store import MineOrderStore

    call_id, instance = call['id'], slot['instance']
    entry = _record(slot.get('entry_record_id'))
    if not entry:
        return

    fills = leg_fills(entry, username, session_data, books)
    row = next((v for (_b, i), v in fills.items() if i == instance), None)
    if row is None:
        return

    status, avg_price, filled_qty = row
    filled_qty = int(filled_qty or 0)
    if status == 'OPEN' and filled_qty <= 0:
        return

    if status == 'OPEN':
        # Part-filled and still working: a triggered stop-limit can rest on
        # its limit. Give it a moment, then take what traded and cancel the
        # rest, so the stop covers exactly what is held.
        first = slot.get('first_partial_at')
        if not first:
            TgCallStore.update_broker(call_id, instance, {'first_partial_at': _time.time()})
            return
        if (_time.time() - first) < _PARTIAL_SETTLE_SECS:
            return
        logger.warning(f"[TgCall] {call_id} broker {instance}: entry stuck part-filled at "
                       f"{filled_qty} — cancelling the remainder")
        _cancel_leg(entry, username, session_data)

    if filled_qty <= 0:
        TgCallStore.update_broker(call_id, instance, {'stage': STAGE_NO_FILL,
                                                      'exit_reason': f'entry {status.lower()}'})
        logger.info(f"[TgCall] {call_id} broker {instance}: entry {status.lower()} with nothing filled")
        return

    lot_size = int(call.get('lot_size') or 0) or 1
    filled_lots = filled_qty // lot_size
    if filled_lots <= 0:
        TgCallStore.update_broker(call_id, instance, {'stage': STAGE_NO_FILL})
        return

    entry_fill = float(avg_price or entry.get('entry_price') or call['entry'])
    MineOrderStore.update_order(entry['id'], {'status': 'EXECUTED', 'entry_price': entry_fill,
                                              'quantity': filled_qty})
    _alert(username, 'tg_call_fill',
           f"Telegram call filled — {call['symbol']} {call['strike']} {call['option_type']}",
           f"{slot.get('name')}: {filled_lots} lot(s) at {entry_fill} · placing SL-M {call['stop']}",
           {'call_id': call_id, 'instance': instance, 'qty': filled_qty, 'price': entry_fill})
    _arm_stop(call, slot, filled_lots, filled_qty, entry_fill, username, session_data)


def _arm_stop(call, slot, filled_lots, filled_qty, entry_fill, username, session_data) -> None:
    """The SL-M for everything that filled. Stage LIVE either way: a refused
    stop is retried every tick, and the price guard covers the gap."""
    call_id, instance = call['id'], slot['instance']
    sl_id = _place_stop_leg(call, instance, filled_lots, float(call['stop']),
                            username, session_data, source='telegram')
    TgCallStore.update_broker(call_id, instance, {
        'stage': STAGE_LIVE, 'open_qty': filled_qty, 'open_lots': filled_lots,
        'entry_qty': filled_qty, 'entry_lots': filled_lots,
        'entry_fill': entry_fill, 'filled_at': _time.time(),
        'legs': {'SL': sl_id} if sl_id else {},
    })
    if not sl_id:
        _alert(username, 'tg_call_order_failed',
               f"Telegram call — NO STOP RESTING: {call['symbol']} {call['strike']} {call['option_type']}",
               f"{slot.get('name')}: the SL-M at {call['stop']} was refused; retrying every tick",
               {'call_id': call_id, 'instance': instance}, once_key=f'nostop:{call_id}:{instance}')


# ── managing the position ─────────────────────────────────────────────────

def _flatten(call, slot, username, session_data, reason) -> None:
    from trading_app.app.routes.api import exit_selected_records
    from trading_app.app.utils.mine_order_store import MineOrderStore

    call_id, instance = call['id'], slot['instance']
    ids = {v for v in (slot.get('legs') or {}).values() if v}
    if slot.get('entry_record_id'):
        ids.add(slot['entry_record_id'])

    def mine():
        return [o for o in MineOrderStore.get_today_orders() if o.get('id') in ids]

    try:
        result = exit_selected_records(username, session_data, mine,
                                       log_tag=f'TgCall {call_id} {reason}')
    except Exception as e:
        logger.error(f"[TgCall] {call_id} broker {instance}: flatten failed: {e}", exc_info=True)
        return

    # The market exit gets a record of its own, so the status sweep reads
    # its fill back and the P&L can be booked on real prices.
    from trading_app.app.routes.order_placement_api import OP_STRATEGY
    legs = dict(slot.get('legs') or {})
    for ex in result.get('exits') or []:
        if int(ex.get('instance') or 0) != int(instance):
            continue
        rec = MineOrderStore.add_order({
            'mode': 'broker',
            'symbol': call['symbol'], 'strike': call['strike'], 'option_type': call['option_type'],
            'action': ex.get('side') or 'SELL',
            'strategy': OP_STRATEGY, 'order_type': 'MARKET', 'type': 'MARKET',
            'instrument': 'BFO' if call['symbol'] == 'SENSEX' else 'NFO',
            'price': None, 'quantity': int(ex.get('quantity') or 0),
            'status': 'OPEN', 'username': username, 'source': 'telegram',
            'signal_id': call_id, 'leg': 'EXIT',
            'broker_order_ids': [{'broker': ex.get('broker'), 'instance': instance,
                                  'order_id': oid, 'success': True,
                                  'quantity': int(ex.get('quantity') or 0)}
                                 for oid in (ex.get('order_ids') or [])],
        })
        legs[f"EXIT{len([k for k in legs if k.startswith('EXIT')]) + 1}"] = rec['id']

    TgCallStore.update_broker(call_id, instance, {'stage': STAGE_FLAT, 'exit_reason': reason,
                                                  'open_qty': 0, 'legs': legs,
                                                  'booked': False, 'flat_at': _time.time()})
    _alert(username, 'tg_call_exit',
           f"Telegram call closed — {call['symbol']} {call['strike']} {call['option_type']}",
           f"{slot.get('name')}: {reason} · {result.get('cancelled_orders')} cancelled, "
           f"{result.get('exited_positions')} squared off",
           {'call_id': call_id, 'instance': instance, 'reason': reason})
    logger.info(f"[TgCall] {call_id} broker {instance}: FLAT ({reason})")


def _check_live(call, slot, ltp, username, session_data) -> None:
    """One broker's live position: the stop first, then the watched target,
    then the guard for a position nothing is protecting."""
    call_id, instance = call['id'], slot['instance']
    legs = slot.get('legs') or {}
    open_qty = int(slot.get('open_qty') or 0)

    sl = _record(legs.get('SL'))
    if sl and sl.get('status') == 'EXECUTED':
        # The exchange sold the whole position. Nothing else is resting, so
        # this is bookkeeping — exit_selected_records finds nothing to do.
        _flatten(call, slot, username, session_data, 'SL hit')
        return

    if open_qty <= 0:
        _flatten(call, slot, username, session_data, 'nothing held')
        return

    if reached('BUY', ltp, float(call['target'])):
        logger.info(f"[TgCall] {call_id} broker {instance}: target {call['target']} touched "
                    f"at {ltp} — cancelling the stop and exiting")
        _cancel_leg(sl, username, session_data)
        _flatten(call, slot, username, session_data, 'T1 hit')
        return

    if not _is_resting(sl) and not (sl and sl.get('status') == 'CANCELLED'):
        # Refused at placement, or gone in a way nothing here did. A stop
        # CANCELLED by hand on the strip is left alone — that was a decision —
        # and the price guard below still covers the position.
        lots = int(slot.get('open_lots') or 0) or max(open_qty // (int(call.get('lot_size') or 1) or 1), 1)
        placed = _place_stop_leg(call, instance, lots, slot_stop(call, slot),
                                 username, session_data, source='telegram')
        if placed:
            TgCallStore.update_broker(call_id, instance, {'legs': {**legs, 'SL': placed}})
            sl = _record(placed)

    # A resting stop that is not at this account's level — the channel edited
    # the SL and the broker refused the move — is retried here every tick. A
    # level the user set by hand on the strip IS this account's level (see
    # note_manual_edit), so it is never moved back. If the premium is already
    # through the wanted level, the stale order is not protection any more:
    # it is cancelled and the position sold at market.
    want = slot_stop(call, slot)
    if _is_resting(sl) and abs(float(sl.get('trigger_price') or 0) - want) >= 0.05:
        if ltp is not None and reached(exit_side('BUY'), ltp, want):
            logger.error(f"[TgCall] {call_id} broker {instance}: premium {ltp} is through the "
                         f"edited stop {want} while the old one rests at {sl.get('trigger_price')} "
                         f"— exiting at market")
            _cancel_leg(sl, username, session_data)
            _flatten(call, slot, username, session_data, 'edited stop breached')
            return
        from trading_app.app.routes.api import _modify_order_at_brokers
        from trading_app.app.utils.mine_order_store import MineOrderStore
        r = _modify_order_at_brokers(sl.get('broker_order_ids'), username, session_data,
                                     trigger_price=want)
        if r.get('success'):
            MineOrderStore.update_order(sl['id'], {'price': want, 'trigger_price': want})
            logger.info(f"[TgCall] {call_id} broker {instance}: stop moved to {want}")

    if ltp is not None and reached(exit_side('BUY'), ltp, want) and not _is_resting(sl):
        logger.error(f"[TgCall] {call_id} broker {instance}: premium {ltp} is through the "
                     f"{want} stop with nothing resting — exiting at market")
        _flatten(call, slot, username, session_data, 'stop breached, no order resting')


def _handle_eod(call, username, session_data) -> None:
    for slot in list((call.get('brokers') or {}).values()):
        stage = slot.get('stage')
        if stage in BROKER_DONE_STAGES:
            continue
        if stage == STAGE_PENDING_ENTRY:
            _cancel_leg(_record(slot.get('entry_record_id')), username, session_data)
            TgCallStore.update_broker(call['id'], slot['instance'],
                                      {'stage': STAGE_NO_FILL, 'exit_reason': 'EOD, never triggered'})
            continue
        _flatten(call, slot, username, session_data, 'EOD square-off')
    TgCallStore.update(call['id'], {'phase': 'DONE', 'eod_handled': True,
                                    'finished_at': int(_time.time() * 1000)})
    logger.info(f"[TgCall] {call['id']}: end-of-day sweep done")


def stop_all_calls(username, session_data, reason='exit-all') -> int:
    """Mark every live call closed — the page-wide exit has already cancelled
    and flattened the orders; this stops the engine re-placing a stop over a
    position that is no longer there."""
    stopped = 0
    for call in TgCallStore.get_active():
        for slot in (call.get('brokers') or {}).values():
            if slot.get('stage') not in BROKER_DONE_STAGES:
                TgCallStore.update_broker(call['id'], slot['instance'],
                                          {'stage': STAGE_FLAT, 'exit_reason': reason, 'open_qty': 0})
        TgCallStore.update(call['id'], {'phase': 'CANCELLED', 'cancel_reason': reason,
                                        'finished_at': int(_time.time() * 1000)})
        stopped += 1
    if stopped:
        logger.info(f"[TgCall] {reason}: {stopped} call(s) stood down")
    return stopped


def call_records(call_id: str) -> list:
    return signal_records(call_id)


# ── the user changed their mind ───────────────────────────────────────────

def slot_stop(call, slot) -> float:
    """The level this account's stop is meant to rest at: a hand-set level
    on the slot wins over the call's plan."""
    return float(slot.get('stop_level') or call['stop'])


def note_manual_edit(order, new_price, new_limit=None) -> dict:
    """The strip's price box moved one of this engine's legs. Record it, so
    the tick manages the position on the number the user chose instead of
    moving the order back to the plan's.

    * SL leg   → that account's ``stop_level``. Per account, deliberately:
                 a stop moved by hand on one account says nothing about the
                 other. A later edit of the channel message overrides it.
    * ENTRY leg → the call's entry and limit (still resting, so it is one
                 order per account anyway; the plan follows it).
    """
    call_id = str(order.get('signal_id') or '')
    if not call_id.startswith('tg-'):
        return {}
    call = TgCallStore.get(call_id)
    if not call:
        return {}
    leg = str(order.get('leg') or '').upper()
    instances = {int(l.get('instance') or 0) for l in (order.get('broker_order_ids') or [])}

    if leg == 'SL':
        for i in instances:
            TgCallStore.update_broker(call_id, i, {'stop_level': float(new_price),
                                                   'stop_source': 'manual'})
        logger.info(f"[TgCall] {call_id} broker(s) {sorted(instances)}: stop set by hand to {new_price}")
        return {'call_id': call_id, 'stop_level': float(new_price), 'instances': sorted(instances)}

    if leg == 'ENTRY':
        updates = {'entry': float(new_price)}
        if new_limit:
            updates['limit'] = float(new_limit)
        TgCallStore.update(call_id, updates)
        logger.info(f"[TgCall] {call_id}: entry set by hand to {new_price} (limit {new_limit})")
        return {'call_id': call_id, **updates}
    return {}


# ── the channel changed its mind ──────────────────────────────────────────
# A call is only as good as the message it came from. The channel deletes a
# call it regrets and edits one it mistyped, and the trade has to follow —
# an entry resting on a number nobody stands behind any more is the wrong
# trade, and a stop at the old level after the caller moved it is the wrong
# stop.

def retract_call(username, call_id, reason='message deleted') -> dict:
    """The message is gone, so the trade goes with it: cancel an entry still
    resting, square off a position already held. Booked like any other exit."""
    call = TgCallStore.get(call_id)
    if not call:
        return {'success': False, 'error': 'No such call'}
    if call.get('phase') in ('DONE', 'CANCELLED', 'FAILED', 'SKIPPED'):
        return {'success': True, 'call_id': call_id, 'already': call.get('phase')}

    session_data = _session(username)
    cancelled = flattened = 0
    for slot in list((call.get('brokers') or {}).values()):
        stage = slot.get('stage')
        if stage in BROKER_DONE_STAGES:
            continue
        if stage == STAGE_PENDING_ENTRY:
            _cancel_leg(_record(slot.get('entry_record_id')), username, session_data)
            TgCallStore.update_broker(call_id, slot['instance'],
                                      {'stage': STAGE_NO_FILL, 'exit_reason': reason})
            cancelled += 1
            continue
        _flatten(call, slot, username, session_data, reason)
        flattened += 1

    TgCallStore.update(call_id, {'phase': 'CANCELLED', 'cancel_reason': reason,
                                 'finished_at': int(_time.time() * 1000)})
    label = f"{call['symbol']} {call['strike']} {call['option_type']}"
    _alert(username, 'tg_call_exit', f'Telegram call withdrawn — {label}',
           f'{reason}: {cancelled} entry order(s) cancelled, {flattened} position(s) squared off',
           {'call_id': call_id, 'reason': reason})
    logger.info(f"[TgCall] {call_id} withdrawn ({reason}): {cancelled} cancelled, {flattened} flattened")
    ensure_running(username, source='retract')          # to book what was flattened
    return {'success': True, 'call_id': call_id, 'cancelled': cancelled, 'flattened': flattened}


def _same_contract(call, plan) -> bool:
    return (str(call.get('symbol')).upper() == str(plan.get('symbol')).upper()
            and int(call.get('strike') or 0) == int(plan.get('strike') or 0)
            and str(call.get('option_type')).upper() == str(plan.get('option_type')).upper())


def amend_call(username, call_id, plan, meta=None) -> dict:
    """The message was edited: move the trade to the new numbers.

    * entry changed, entry still resting  → modify the stop-limit (trigger + limit)
    * stop changed, position live         → modify the SL-M's trigger
    * target changed                      → the watched level moves; nothing at a broker
    * contract changed, entry resting     → the old entry is withdrawn and the
                                            new text is taken as a fresh call
    * contract changed, position live     → the position is kept and managed on
                                            the new stop/target; alerted

    An edit that no longer parses as a call, or that puts a number on the
    wrong side of the market, leaves the trade exactly as it was and says so.
    """
    from trading_app.app.routes.api import _modify_order_at_brokers
    from trading_app.app.routes.order_placement_api import option_ltp, stop_direction_error
    from trading_app.app.utils.mine_order_store import MineOrderStore

    call = TgCallStore.get(call_id)
    if not call:
        return {'success': False, 'error': 'No such call'}
    label = f"{call['symbol']} {call['strike']} {call['option_type']}"
    if call.get('phase') in ('DONE', 'CANCELLED', 'FAILED', 'SKIPPED'):
        return {'success': True, 'call_id': call_id, 'already': call.get('phase')}

    if 'error' in plan:
        _alert(username, 'tg_call_skipped', f'Telegram call edited into something unreadable — {label}',
               f"Trade left as it was. {plan['error']}", {'call_id': call_id})
        return {'success': False, 'error': plan['error']}

    try:
        entry, stop = float(plan['entry']), float(plan['stop'])
        target = float((plan.get('targets') or [None])[0])
    except (TypeError, ValueError):
        return {'success': False, 'error': 'the edited call has no usable numbers'}
    if not (stop < entry < target):
        _alert(username, 'tg_call_skipped', f'Telegram call edit ignored — {label}',
               f'Edited numbers do not make a trade (SL {stop}, entry {entry}, T1 {target}). Left as it was.',
               {'call_id': call_id})
        return {'success': False, 'error': 'edited numbers are not a ladder'}

    session_data = _session(username)
    slots = list((call.get('brokers') or {}).values())
    pending = [s for s in slots if s.get('stage') == STAGE_PENDING_ENTRY]
    live = [s for s in slots if s.get('stage') == STAGE_LIVE]
    changes, problems = [], []

    # ── the contract itself changed ───────────────────────────────────
    if not _same_contract(call, plan):
        if pending and not live:
            retract_call(username, call_id, reason='message edited to another contract')
            new_meta = {**(meta or {}), 'text': (meta or {}).get('text') or plan.get('source_text')}
            result = take_call(username, plan, new_meta)
            return {'success': True, 'call_id': call_id, 'replaced_by': result.get('call_id'),
                    'result': result}
        _alert(username, 'tg_call_order_failed',
               f'Telegram call edited to a different contract — {label}',
               f"The position is already held in {label}; it stays, managed on the edited "
               f"SL {stop} / T1 {target}. Check the channel.",
               {'call_id': call_id, 'plan': plan})
        problems.append('contract changed after fill')

    # ── entry ─────────────────────────────────────────────────────────
    old_entry = float(call.get('entry') or 0)
    if pending and entry != old_entry:
        try:
            ltp = option_ltp(call['symbol'], call['strike'], call['option_type'])
        except Exception:
            ltp = None
        wrong = stop_direction_error('BUY', entry, ltp)
        if wrong:
            problems.append(f'entry not moved: {wrong}')
        else:
            limit = entry_limit(entry, limit_pct(username))
            for slot in pending:
                rec = _record(slot.get('entry_record_id'))
                if not _is_resting(rec):
                    continue
                r = _modify_order_at_brokers(rec.get('broker_order_ids'), username, session_data,
                                             price=limit, trigger_price=entry)
                if r.get('success'):
                    MineOrderStore.update_order(rec['id'], {'price': limit, 'trigger_price': entry})
                    changes.append(f"{slot.get('name')}: entry {old_entry} → {entry} (limit {limit})")
                else:
                    problems.append(f"{slot.get('name')}: entry modify refused — {r.get('error')}")
            TgCallStore.update(call_id, {'entry': entry, 'limit': limit})
    elif live and entry != old_entry:
        problems.append(f'entry {old_entry} → {entry} ignored: already filled')

    # ── stop ──────────────────────────────────────────────────────────
    old_stop = float(call.get('stop') or 0)
    if stop != old_stop:
        moved_any = False
        for slot in live:
            sl = _record((slot.get('legs') or {}).get('SL'))
            if not _is_resting(sl):
                continue                          # the tick re-places it at call['stop']
            r = _modify_order_at_brokers(sl.get('broker_order_ids'), username, session_data,
                                         trigger_price=stop)
            if r.get('success'):
                MineOrderStore.update_order(sl['id'], {'price': stop, 'trigger_price': stop})
                changes.append(f"{slot.get('name')}: stop {old_stop} → {stop}")
                moved_any = True
            else:
                problems.append(f"{slot.get('name')}: stop modify refused — {r.get('error')}")
        # The plan's number moves regardless: a pending entry arms its stop
        # from it, and a refused modify is retried against it by the tick. A
        # level set by hand earlier is superseded — the channel spoke later.
        TgCallStore.update(call_id, {'stop': stop})
        for slot in slots:
            if slot.get('stop_level'):
                TgCallStore.update_broker(call_id, slot['instance'],
                                          {'stop_level': None, 'stop_source': 'channel'})
        if not live and pending:
            changes.append(f'stop {old_stop} → {stop} (for when the entry fills)')
        elif live and not moved_any and not any('stop modify' in p for p in problems):
            changes.append(f'stop {old_stop} → {stop}')

    # ── target ────────────────────────────────────────────────────────
    old_target = float(call.get('target') or 0)
    if target != old_target:
        TgCallStore.update(call_id, {'target': target,
                                     'targets': [float(t) for t in plan.get('targets') or [target]]})
        changes.append(f'T1 {old_target} → {target}')

    TgCallStore.update(call_id, {'edited_at': int(_time.time() * 1000),
                                 'source_text': (plan.get('source_text') or (meta or {}).get('text') or '')[:2000]})
    if changes or problems:
        _alert(username, 'tg_call_taken' if not problems else 'tg_call_order_failed',
               f'Telegram call edited — {label}',
               ' · '.join(changes + problems) or 'no change',
               {'call_id': call_id, 'changes': changes, 'problems': problems, 'plan': plan})
    logger.info(f"[TgCall] {call_id} amended: {changes} problems={problems}")
    return {'success': not problems, 'call_id': call_id, 'changes': changes, 'problems': problems}


# ── booking the P&L ───────────────────────────────────────────────────────

def _sell_fills(call_id, instance) -> list:
    """``(qty, price)`` of every executed sell leg this slot placed — the
    SL-M, the market exits, or both when a stop filled during a T1 exit."""
    out = []
    for o in signal_records(call_id):
        if o.get('status') != 'EXECUTED' or str(o.get('action') or '').upper() != 'SELL':
            continue
        if not any(int(l.get('instance') or 0) == int(instance)
                   for l in (o.get('broker_order_ids') or [])):
            continue
        qty = int(o.get('quantity') or 0)
        price = o.get('entry_price') or o.get('price')
        if qty > 0 and price:
            out.append((qty, float(price)))
    return out


def _book_slot(call, slot, username) -> dict:
    """Write this slot's row to the ledger. Called once the sells cover the
    entry, or once the wait for them has run out."""
    instance = slot['instance']
    lot_size = int(call.get('lot_size') or 0) or 1
    entry_qty = int(slot.get('entry_qty') or slot.get('open_lots', 0) * lot_size or 0)
    lots = int(slot.get('entry_lots') or (entry_qty // lot_size) or 0)
    entry_price = float(slot.get('entry_fill') or call['entry'])

    fills = _sell_fills(call['id'], instance)
    sold_qty = sum(q for q, _ in fills)
    exit_price = (sum(q * p for q, p in fills) / sold_qty) if sold_qty else None
    complete = bool(entry_qty) and sold_qty >= entry_qty

    points = (exit_price - entry_price) if exit_price is not None else None
    pnl_per_lot = round(points * lot_size, 2) if points is not None else None
    pnl = round(pnl_per_lot * lots, 2) if pnl_per_lot is not None else None

    row = TgTradeHistory.append({
        'date': datetime.now(_ops._IST).strftime('%Y-%m-%d'),
        'call_id': call['id'], 'message_id': call.get('message_id'),
        'symbol': call['symbol'], 'strike': call['strike'], 'option_type': call['option_type'],
        'expiry': call.get('expiry'),
        'instance': instance, 'broker': slot.get('broker'), 'name': slot.get('name'),
        'lots': lots, 'lot_size': lot_size, 'qty': entry_qty, 'sold_qty': sold_qty,
        'trigger': call['entry'], 'limit': call.get('limit'), 'sl': call['stop'],
        'target': call.get('target'),
        'entry_price': entry_price, 'exit_price': round(exit_price, 2) if exit_price else None,
        'points': round(points, 2) if points is not None else None,
        'pnl_per_lot': pnl_per_lot, 'pnl': pnl,
        'exit_reason': slot.get('exit_reason'),
        'filled_at': int((slot.get('filled_at') or 0) * 1000) or None,
        'flat_at': int((slot.get('flat_at') or 0) * 1000) or None,
        'complete': complete,
    })
    TgCallStore.update_broker(call['id'], instance, {'booked': True, 'trade_id': row['id'],
                                                     'pnl': pnl, 'pnl_per_lot': pnl_per_lot})
    logger.info(f"[TgCall] {call['id']} broker {instance}: booked {lots} lot(s) "
                f"{entry_price} → {exit_price} = {pnl_per_lot}/lot, {pnl} total"
                + ('' if complete else ' (INCOMPLETE — exit fill not fully known)'))
    if not complete:
        _alert(username, 'tg_call_order_failed',
               f"Telegram call booked without a full exit fill — {call['symbol']} {call['strike']} {call['option_type']}",
               f"{slot.get('name')}: sold {sold_qty} of {entry_qty} known. Check the account.",
               {'call_id': call['id'], 'instance': instance, 'trade_id': row['id']},
               once_key=f"incomplete:{call['id']}:{instance}")
    return row


def _book_pending(username) -> int:
    booked = 0
    for call in TgCallStore.get_unbooked():
        for slot in (call.get('brokers') or {}).values():
            if slot.get('stage') != STAGE_FLAT or slot.get('booked'):
                continue
            try:
                lot_size = int(call.get('lot_size') or 0) or 1
                entry_qty = int(slot.get('entry_qty') or 0)
                sold = sum(q for q, _ in _sell_fills(call['id'], slot['instance']))
                waited = _time.time() - float(slot.get('flat_at') or 0)
                if entry_qty and sold < entry_qty and waited < _BOOK_SETTLE_SECS:
                    continue                 # the exit is still filling
                _book_slot(call, slot, username)
                booked += 1
            except Exception as e:
                logger.error(f"[TgCall] booking {call.get('id')} broker {slot.get('instance')} "
                             f"failed: {e}", exc_info=True)
    return booked


def history(days: int = 0) -> list:
    return TgTradeHistory.rows(days)


# ── the loop ──────────────────────────────────────────────────────────────

def tick(username, session_data=None) -> None:
    from trading_app.app.routes.api import _reconcile_open_orders
    from trading_app.app.routes.order_placement_api import option_ltp

    session_data = session_data if session_data is not None else _session(username)
    calls = TgCallStore.get_active()
    if not calls and not TgCallStore.get_unbooked():
        return

    # One status sweep for every leg of every call. Forced, because the
    # engine cannot wait out the 8-second throttle while a position is
    # unprotected — and the sweep stamps the throttle, so the page's own
    # polls ride on it.
    try:
        _reconcile_open_orders(username, session_data, force=True)
    except Exception as e:
        logger.warning(f"[TgCall] status sweep failed: {e}")

    cutoff = _exit_cutoff_mins(username)
    now_mins = _now_mins()
    books = {}

    for call in calls:
        try:
            if now_mins >= cutoff and not call.get('eod_handled'):
                _handle_eod(call, username, session_data)
                continue

            slots = list((call.get('brokers') or {}).values())
            ltp = None
            if any(s.get('stage') == STAGE_LIVE for s in slots):
                ltp = option_ltp(call['symbol'], call['strike'], call['option_type'])
                if ltp is None:
                    # Blind: the stop still rests at the exchange, but the
                    # target is watched here and cannot be seen. Say so
                    # loudly, once, so the position is managed by hand.
                    _alert(username, 'tg_call_order_failed',
                           f"Telegram call — NO QUOTE: {call['symbol']} {call['strike']} {call['option_type']}",
                           f"Cannot read the premium, so target {call.get('target')} is NOT being "
                           f"watched. The stop at {call['stop']} still rests. Manage this one by hand.",
                           {'call_id': call['id']}, once_key=f"noquote:{call['id']}")

            for slot in slots:
                stage = slot.get('stage')
                if stage in BROKER_DONE_STAGES:
                    continue
                if stage == STAGE_PENDING_ENTRY:
                    _settle_entry(call, slot, username, session_data, books)
                else:
                    _check_live(call, slot, ltp, username, session_data)

            TgCallStore.finish_if_all_brokers_done(call['id'])
        except Exception as e:
            logger.error(f"[TgCall] tick for {call.get('id')} failed: {e}", exc_info=True)

    # After the sweep above has read the exit fills back.
    _book_pending(username)


def _loop(username) -> None:
    logger.info('[TgCall] engine thread started')
    while not _stop_event.is_set():
        try:
            if _now_mins() >= _HARD_STOP_MIN:
                logger.info('[TgCall] past the hard stop — engine standing down')
                break
            tick(username)
            if not TgCallStore.get_active() and not TgCallStore.get_unbooked():
                logger.info('[TgCall] no live calls — engine standing down')
                break
        except Exception as e:
            logger.error(f"[TgCall] loop error: {e}", exc_info=True)
        _stop_event.wait(_POLL_SECS)
    logger.info('[TgCall] engine thread stopped')


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def ensure_running(username, source: str = '') -> bool:
    """Start the engine if there is anything to manage. Idempotent — called
    when a call is taken, from the scheduler's start job, and from its
    watchdog."""
    global _thread
    with _thread_lock:
        if is_running():
            return True
        if not TgCallStore.get_active() and not TgCallStore.get_unbooked():
            return False
        if _now_mins() >= _HARD_STOP_MIN:
            return False
        _stop_event.clear()
        _thread = threading.Thread(target=_loop, args=(username,),
                                   name='tg-call-engine', daemon=True)
        _thread.start()
        logger.info(f"[TgCall] engine started ({source or 'manual'})")
        return True


def stop() -> None:
    _stop_event.set()


__all__ = ['take_call', 'validate_call', 'tg_targets', 'entry_limit', 'tick',
           'ensure_running', 'is_running', 'stop', 'stop_all_calls', 'call_records',
           'remember_session', 'is_active', 'history', 'retract_call', 'amend_call', 'prewarm',
           'note_manual_edit', 'slot_stop']
