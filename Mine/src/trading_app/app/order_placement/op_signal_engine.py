"""The signal state machine: everything that happens after a tip is armed.

One pasted tip becomes, per broker, a five-step life:

    entry stop rests  →  it fills  →  stop + T1 + T2 attached  →  targets book,
    the stop ratchets up behind them  →  flat.

Three things about the shape are worth knowing before reading the code.

**Everything is per broker.** A signal reaches two Kite accounts sized 20 lots
and 4 lots. They fill at different prices, at different moments, and sometimes
one fills and the other does not. A single "the signal has reached target 1"
flag would trail the second account's stop up to an entry it has not made. So
each broker gets its own slot in the plan, its own stage, and its own legs, and
they are advanced independently.

**The stop is one leg's worth, not the whole position.** A three-lot signal
rests three one-lot orders: the stop, target 1, target 2. Sell quantity working
therefore equals quantity held, exactly — nothing here asks a broker for
short-option margin on a long position, and there is no instant at which a
triggering stop could sell a lot a target has already sold. That race is the
one an emulated OCO ladder normally has to be defended against; this shape does
not have it.

What it costs is that a stop hit is not the whole exit. The stop sells its own
lot at the exchange, and the engine then cancels the remaining targets and
sells the rest at market — a tick later, at whatever the market is by then. The
alternative was a full-size stop, which exits everything at the exchange but
rests twice the held quantity to do it.

**Target 3 has no order either.** Its lot is the one the stop is holding: the
runner rides with the stop trailing behind it, and when the premium touches
target 3 the stop is cancelled and that lot is sold at market. So T3 needs the
app alive; the stop does not.

**The stop is allocated before the targets.** On a short fill that is the
difference between a protected position with fewer targets and an unprotected
one with more: two lots filled arms a stop and target 1, one lot arms a stop
and nothing else.
"""

import threading
import time as _time
from datetime import datetime, timedelta, timezone

from trading_app.app.order_placement.op_signal_store import (
    BROKER_DONE_STAGES, STAGE_DEAD, STAGE_FLAT, STAGE_LIVE, STAGE_NO_FILL,
    STAGE_PENDING_ENTRY, STAGE_T1_DONE, STAGE_T2_DONE, OpSignalStore)
from trading_app.app.utils.logger import logger

_IST = timezone(timedelta(hours=5, minutes=30))

_POLL_SECS = 3
_RECONCILE_SECS = 30

# A triggered SL-M is a market order; it is done in seconds. Still part-filled
# after this long means the rest is not coming, so the remainder is cancelled
# and the ladder is armed on what actually traded rather than waiting forever
# with an unmanaged position.
_PARTIAL_SETTLE_SECS = 20

# The loop stops here whatever else is going on. 15:29 mirrors the algos.
_HARD_STOP_MIN = 15 * 60 + 29

_DEFAULT_EXIT_HHMM = (15, 15)

_thread = None
_stop_event = threading.Event()
_thread_lock = threading.Lock()
_state = {'last_reconcile': 0.0}

# Broker tokens live in the Flask session for Fyers and Kotak, and this engine
# runs on a thread that has none. The last session to arm or view a signal is
# kept here, in memory only — never written to the plan file, which would put
# live access tokens on disk. After a restart it is empty and every broker call
# falls back to UserEnvManager, which is enough for Zerodha (the only broker
# type that can hold a stop today) and is why that fallback matters.
_session_cache = {}


# ── small shared helpers ──────────────────────────────────────────────────

def remember_session(username, session_data):
    if session_data:
        _session_cache[username] = dict(session_data)


def _session(username):
    return _session_cache.get(username) or {}


def _exit_cutoff_mins(username) -> int:
    from trading_app.app.utils.user_env import UserEnvManager
    hh, mm = _DEFAULT_EXIT_HHMM
    try:
        hh = int(UserEnvManager.get_user_var(username, 'OP_SIGNAL_EXIT_HOUR') or hh)
        mm = int(UserEnvManager.get_user_var(username, 'OP_SIGNAL_EXIT_MINUTE') or mm)
    except (TypeError, ValueError):
        hh, mm = _DEFAULT_EXIT_HHMM
    return hh * 60 + mm


def _now_mins() -> int:
    now = datetime.now(_IST)
    return now.hour * 60 + now.minute


def exit_side(action: str) -> str:
    """The side that closes a position opened with ``action``."""
    return 'SELL' if str(action).upper() == 'BUY' else 'BUY'


def reached(action: str, price: float, level: float) -> bool:
    """Has the premium got to ``level``, in the direction the tip is trading?

    A long option's targets are above the entry and a short option's are below,
    so "reached" cannot be a bare comparison.
    """
    if price is None or level is None:
        return False
    return price >= level if str(action).upper() == 'BUY' else price <= level


def _leg_tag(signal_id: str, leg: str) -> str:
    """Kite's order tag, used as this engine's idempotency key.

    Max 20 characters, alphanumeric. ``sig-1a2b3c4d5e6f`` + ``T1`` fits with
    room to spare, and it is what lets a restart tell a leg it placed but never
    managed to record from a hand-placed order at the same strike.
    """
    return f"{signal_id.replace('-', '')[:16]}{leg}"[:20]


def _record(order_id):
    from trading_app.app.utils.mine_order_store import MineOrderStore
    return MineOrderStore.get_order(order_id) if order_id else {}


def _is_resting(order) -> bool:
    from trading_app.app.utils.mine_order_store import MineOrderStore
    return bool(order) and order.get('status') in MineOrderStore.EDITABLE_STATUSES


def signal_records(signal_id: str) -> list:
    """Every stored order this signal placed, at any broker."""
    from trading_app.app.utils.mine_order_store import MineOrderStore
    return [o for o in MineOrderStore.get_today_orders()
            if o.get('signal_id') == signal_id]


# ── sizing ────────────────────────────────────────────────────────────────

def op_signal_lots(username, instance):
    """Lots one broker trades **per target leg**, or None if it has none set.

    ``BROKER_N_OP_SIGNAL_LOTS=1`` means one lot at each of the three targets,
    so a three-lot entry. There is deliberately no fallback to
    ``BROKER_N_OP_LOTS``: that number sizes a whole single-mode order, and
    silently reading it as a per-target size would treble the position.
    """
    from trading_app.app.utils.user_env import UserEnvManager
    raw = UserEnvManager.get_user_var(username, f'BROKER_{instance}_OP_SIGNAL_LOTS')
    if raw in (None, ''):
        raw = UserEnvManager.get_user_var(username, 'OP_SIGNAL_LOTS')
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


# The exchange refuses a single F&O order above this many lots, and neither
# shared dispatcher splits by it — split_quantity_by_freeze_limit exists but is
# only reached on the way out, by _place_exit_leg. So a signal splits its own
# legs on the way in. Kept as a lot count rather than borrowing that helper,
# whose lot-size fallbacks are stale (NIFTY 25) and would over-split a leg it
# could not price.
_FREEZE_LOTS = 27


def lot_chunks(lots: int) -> list:
    """One leg's lots, split into orders the exchange will accept.

    30 lots is 27 + 3. At or below the limit it is one order and nothing
    changes, which is every leg on a normally-sized signal.
    """
    out, left = [], int(lots)
    while left > 0:
        take = min(left, _FREEZE_LOTS)
        out.append(take)
        left -= take
    return out


def allocate(filled_lots: int, per_target: int):
    """``(stop_lots, [t1_lots, t2_lots])`` out of what actually filled.

    The three lots of a one-per-target signal become three resting orders of
    one lot each: the stop, target 1, target 2. Resting sell quantity is then
    exactly the quantity held — never more — which is what makes the whole
    ladder placeable on a long option without asking for short-option margin.

    Target 3 gets no order. Its lot is the one the STOP is holding: the runner
    rides with the stop trailing behind it until either the stop fires or the
    premium touches target 3, at which point the stop is cancelled and the lot
    is sold at market.

    The stop is allocated FIRST. On a short fill that is the difference between
    a protected position with fewer targets and an unprotected one with more:
    two lots filled arms a stop and target 1, and one lot filled arms a stop
    and nothing else.
    """
    left = int(filled_lots)
    stop_lots = min(int(per_target), max(left, 0))
    left -= stop_lots

    targets = []
    for _ in range(2):                 # only T1 and T2 ever rest
        take = min(int(per_target), max(left, 0))
        targets.append(take)
        left -= take
    return stop_lots, targets


# ── arming ────────────────────────────────────────────────────────────────

def validate_plan(username, plan, chain_meta=None, ltp=None) -> str:
    """Every refusal that can be made before a broker is touched, or None.

    Refusing here rather than at the broker is the whole point: four brokers
    would each refuse a malformed ticket separately, and some of them would
    accept the parts of it that are merely wrong rather than invalid.
    """
    from trading_app.app.routes.order_placement_api import (OP_SYMBOLS, op_targets,
                                                            stop_direction_error)

    symbol = str(plan.get('symbol') or '').upper()
    if symbol not in OP_SYMBOLS:
        return f'This page trades {", ".join(OP_SYMBOLS)} — not {symbol or "that"}'
    if str(plan.get('option_type') or '').upper() not in ('CE', 'PE'):
        return 'option_type must be CE or PE'

    action = str(plan.get('action') or 'BUY').upper()
    if action not in ('BUY', 'SELL'):
        return 'action must be BUY or SELL'

    try:
        strike = int(plan.get('strike') or 0)
        entry = float(plan.get('entry') or 0)
        stop = float(plan.get('stop') or 0)
        targets = [float(t) for t in (plan.get('targets') or [])]
    except (TypeError, ValueError):
        return 'The strike, entry, stop and targets must all be numbers'

    if strike <= 0 or entry <= 0 or stop <= 0:
        return 'The strike, entry and stop must all be above zero'
    if len(targets) != 3:
        return f'Signal mode needs three targets — {len(targets)} given'
    if any(t <= 0 for t in targets):
        return 'Every target must be above zero'

    # The ladder, in the direction the tip trades. A stop on the wrong side of
    # the entry is not a stop, and targets out of order would book the furthest
    # one first and trail the stop backwards.
    if action == 'BUY':
        if stop >= entry:
            return f'A BUY signal needs its stop below the entry — SL {stop} is not below {entry}'
        if not (entry < targets[0] < targets[1] < targets[2]):
            return (f'A BUY signal needs rising targets above the entry — got '
                    f'{entry} then {", ".join(str(t) for t in targets)}')
    else:
        if stop <= entry:
            return f'A SELL signal needs its stop above the entry — SL {stop} is not above {entry}'
        if not (entry > targets[0] > targets[1] > targets[2]):
            return (f'A SELL signal needs falling targets below the entry — got '
                    f'{entry} then {", ".join(str(t) for t in targets)}')

    meta = chain_meta or {}
    step = int(meta.get('step') or 0)
    if step and strike % step:
        return f'{strike} is not a {symbol} strike — they step by {step}'

    # The order path always resolves the nearest expiry and ignores anything
    # passed to it, so a back-month tip would be traded on the front month at a
    # completely different premium. Say so rather than trade the wrong contract.
    want = plan.get('expiry')
    have = meta.get('expiry')
    if want and have and str(want) != str(have):
        return (f'This tip is for the {want} expiry, but this page trades the front '
                f'expiry ({have}). Arm it after the roll, or edit the tip.')

    # The entry is a stop: it must still be waiting when it reaches the
    # exchange, or it fires at market for the full size the instant it lands.
    wrong_side = stop_direction_error(action, entry, ltp)
    if wrong_side:
        return wrong_side

    targets_env = op_targets(username)
    if not targets_env:
        return ('No broker is enabled for this page. Set BROKER_N_OP_ACTIVE=true '
                'in env/Mine.env')

    sized, unsized, unstoppable = [], [], []
    for t in targets_env:
        # Only Zerodha and Fyers can hold an SL-M, and only they can have its
        # trigger moved afterwards. A signal is a stop plus a ladder; on a
        # broker that cannot hold the stop it is a naked position with some
        # limit orders over it, which is worse than not arming at all.
        if t['type'] not in ('zerodha', 'kite', 'fyers'):
            unstoppable.append(f"{t['name']} ({t['type']})")
            continue
        if op_signal_lots(username, t['instance']) is None:
            unsized.append(t['name'])
            continue
        sized.append(t)

    if unstoppable:
        return (f'{", ".join(unstoppable)} cannot hold a stop-loss order, so a signal '
                f'would run there unprotected. Turn BROKER_N_OP_ACTIVE off for it, or '
                f'arm this from the single-order pad instead.')
    if not sized:
        return (f'No broker has BROKER_N_OP_SIGNAL_LOTS set'
                + (f' ({", ".join(unsized)} are enabled but unsized)' if unsized else '')
                + '. Signal mode will not guess a size from BROKER_N_OP_LOTS.')

    # No freeze-limit refusal: a leg over it is split into orders the exchange
    # will take (see lot_chunks). What is still worth refusing is a size that
    # cannot be a ladder at all.
    for t in sized:
        lots = op_signal_lots(username, t['instance'])
        if lots > 500:
            return (f"BROKER_{t['instance']}_OP_SIGNAL_LOTS is {lots}, which would enter "
                    f"{lots * 3} lots at {t['name']}. That is a typo, not a position.")
    return None


def arm_signal(username, session_data, plan) -> dict:
    """Place the entry stop at every sized broker and record the plan.

    One entry order per broker, not one order fanned out: each account's ladder
    is armed off its own fill, so each needs a record of its own to watch. The
    partial-success contract is the pad's — a broker that refuses is a row
    saying so, and only zero acceptances is a failure.
    """
    from trading_app.app.routes.api import dispatch_stop_to_brokers, resolve_standard_lot
    from trading_app.app.routes.order_placement_api import OP_STRATEGY, op_targets
    from trading_app.app.utils.mine_order_store import MineOrderStore

    remember_session(username, session_data)

    symbol = str(plan['symbol']).upper()
    strike = int(plan['strike'])
    option_type = str(plan['option_type']).upper()
    action = str(plan.get('action') or 'BUY').upper()
    entry, stop = float(plan['entry']), float(plan['stop'])
    targets = [float(t) for t in plan['targets']]

    # A guessed lot size is worse than no signal: it sizes the stop against a
    # position that is not the size it thinks.
    lot_size = resolve_standard_lot(symbol)
    if not lot_size:
        return {'success': False,
                'error': f'Could not resolve the lot size for {symbol} — the broker '
                         f'instrument list is unavailable. Reconnect the data provider '
                         f'and retry.'}

    eligible = [t for t in op_targets(username)
                if op_signal_lots(username, t['instance']) is not None]

    signal = OpSignalStore.create({
        'username': username,
        'symbol': symbol, 'strike': strike, 'option_type': option_type,
        'action': action, 'entry': entry, 'stop': stop, 'targets': targets,
        'expiry': str(plan.get('expiry') or '') or None,
        'lot_size': lot_size,
        'phase': 'ARMED',
        'source_text': (plan.get('source_text') or '')[:2000],
        'brokers': {},
    })
    signal_id = signal['id']

    summary = []
    for t in eligible:
        instance = t['instance']
        lots = op_signal_lots(username, instance)
        entry_lots = lots * 3

        # Over the freeze limit this is more than one order at the same
        # account — 30 lots is 27 + 3 — and every chunk is recorded on the one
        # entry record, which leg_fills sums back together.
        results = []
        for chunk in lot_chunks(entry_lots):
            try:
                results += dispatch_stop_to_brokers(
                    symbol=symbol, strike=strike, option_type=option_type,
                    trigger_price=entry, action=action,
                    username=username, session_data=session_data,
                    standard_lot=lot_size,
                    gate=lambda i, _b, _want=instance: i == _want,
                    lots_for=lambda _i, _n=chunk: _n,
                    log_tag=f'OpSignal {signal_id} entry',
                )
            except Exception as e:
                logger.error(f"[OpSignal] {signal_id} entry at broker {instance} "
                             f"failed: {e}", exc_info=True)
                results.append({'broker': t['type'], 'instance': instance,
                                'success': False, 'error': str(e)})

        ok = [r for r in results if r.get('success')]
        error = next((r.get('error') for r in results if not r.get('success')), None)

        record = MineOrderStore.add_order({
            'mode': 'broker',
            'symbol': symbol, 'strike': strike, 'option_type': option_type,
            'action': action,
            'strategy': OP_STRATEGY,
            'order_type': 'SL-M', 'type': 'SL-M',
            'instrument': 'BFO' if symbol == 'SENSEX' else 'NFO',
            'price': entry, 'trigger_price': entry,
            'quantity': sum(int(r.get('quantity') or 0) for r in ok),
            'status': 'OPEN' if ok else 'REJECTED',
            'username': username,
            'source': 'orderplacement',
            'signal_id': signal_id, 'leg': 'ENTRY',
            'broker_order_ids': results,
        })

        OpSignalStore.update_broker(signal_id, instance, {
            'instance': instance, 'broker': t['type'], 'name': t['name'],
            'plan_lots': lots, 'entry_lots': entry_lots,
            'stage': STAGE_PENDING_ENTRY if ok else STAGE_DEAD,
            'entry_record_id': record['id'],
            'legs': {}, 'error': None if ok else error,
        })

        summary.append({'broker': t['type'], 'instance': instance, 'name': t['name'],
                        'lots': entry_lots, 'success': bool(ok), 'error': error,
                        'order_id': record['id']})

    accepted = [r for r in summary if r['success']]
    if not accepted:
        # Nothing is resting anywhere, so there is no plan to manage. Closing
        # it here keeps a dead signal off the engine's list and off the page.
        OpSignalStore.update(signal_id, {'phase': 'FAILED'})
        return {'success': False,
                'error': next((r['error'] for r in summary if r['error']),
                              'Every broker refused the entry'),
                'signal_id': signal_id, 'summary': summary}

    OpSignalStore.update(signal_id, {'phase': 'ENTRY_PENDING'})
    logger.info(f"[OpSignal] {signal_id} armed {action} {symbol} {strike}{option_type} "
                f"entry={entry} sl={stop} targets={targets} → "
                f"{len(accepted)}/{len(summary)} brokers")

    # Tick at once rather than waiting up to five minutes for the watchdog: a
    # signal armed at 11:03 can fill at 11:03.
    ensure_running(username, source='arm')
    return {'success': True, 'signal_id': signal_id, 'summary': summary,
            'signal': OpSignalStore.get(signal_id)}


# ── settling the entry ────────────────────────────────────────────────────

def _settle_entry(signal, slot, username, session_data, books) -> None:
    """Has this broker's entry finished, and with how much?

    ``_reconcile_open_orders`` collapses every broker's fill onto the record;
    this reads the one leg that belongs to this broker, so an account that
    filled 3 lots and an account that filled none are not averaged into each
    other.
    """
    from trading_app.app.routes.api import leg_fills
    from trading_app.app.utils.mine_order_store import MineOrderStore

    signal_id, instance = signal['id'], slot['instance']
    entry = _record(slot.get('entry_record_id'))
    if not entry:
        return

    fills = leg_fills(entry, username, session_data, books)
    row = next((v for (_b, i), v in fills.items() if i == instance), None)
    if row is None:
        return                       # the book could not answer — not "gone"

    status, avg_price, filled_qty = row
    filled_qty = int(filled_qty or 0)

    if status == 'OPEN' and filled_qty <= 0:
        return                       # still waiting on the trigger

    if status == 'OPEN':
        # Part-filled and still working. Give it a moment — a triggered SL-M
        # is a market order — then take what traded and cancel the rest, so
        # the position is never left larger than the ladder covering it.
        first = slot.get('first_partial_at')
        if not first:
            OpSignalStore.update_broker(signal_id, instance,
                                        {'first_partial_at': _time.time()})
            return
        if (_time.time() - first) < _PARTIAL_SETTLE_SECS:
            return
        logger.warning(f"[OpSignal] {signal_id} broker {instance}: entry stuck part-filled "
                       f"at {filled_qty} — cancelling the remainder")
        _cancel_leg(entry, username, session_data)

    if filled_qty <= 0:
        OpSignalStore.update_broker(signal_id, instance, {'stage': STAGE_NO_FILL})
        logger.info(f"[OpSignal] {signal_id} broker {instance}: entry {status.lower()} "
                    f"with nothing filled")
        return

    lot_size = int(signal.get('lot_size') or 0) or 1
    if filled_qty % lot_size:
        # Should be impossible — F&O trades in lot multiples — so say it
        # loudly rather than floor it into a ladder that is quietly short.
        logger.error(f"[OpSignal] {signal_id} broker {instance}: filled {filled_qty} is "
                     f"not a multiple of the {lot_size} lot — sizing the ladder on "
                     f"{filled_qty // lot_size} whole lots and leaving the rest to the stop")
    filled_lots = filled_qty // lot_size
    if filled_lots <= 0:
        OpSignalStore.update_broker(signal_id, instance, {'stage': STAGE_NO_FILL})
        return

    entry_fill = float(avg_price or entry.get('entry_price') or signal['entry'])
    MineOrderStore.update_order(entry['id'], {'status': 'EXECUTED',
                                              'entry_price': entry_fill,
                                              'quantity': filled_qty})
    _arm_ladder(signal, slot, filled_lots, filled_qty, entry_fill,
                username, session_data)


def _arm_ladder(signal, slot, filled_lots, filled_qty, entry_fill,
                username, session_data) -> None:
    """Attach the stop and the two resting targets to a filled entry.

    Three lots become three orders of one lot: stop, target 1, target 2. What
    is resting to sell is exactly what is held, so nothing here asks the broker
    for short-option margin, and no window exists in which the stop covers more
    than the position — the race that makes most emulated OCO ladders unsafe
    simply cannot arise when the stop is one leg's worth.
    """
    signal_id, instance = signal['id'], slot['instance']
    lot_size = int(signal.get('lot_size') or 1) or 1
    stop_lots, alloc = allocate(filled_lots, slot['plan_lots'])

    OpSignalStore.update_broker(signal_id, instance, {
        'filled_lots': filled_lots, 'filled_qty': filled_qty,
        'entry_fill': entry_fill, 'open_qty': filled_qty,
        'sl_lots': stop_lots, 'sl_qty': stop_lots * lot_size,
        'alloc': alloc, 'stop_level': float(signal['stop']),
        'stage': STAGE_LIVE,
    })
    logger.info(f"[OpSignal] {signal_id} broker {instance}: entry filled {filled_qty} "
                f"({filled_lots} lots) at {entry_fill} — arming stop {signal['stop']} "
                f"x{stop_lots}, T1 {signal['targets'][0]} x{alloc[0]}, "
                f"T2 {signal['targets'][1]} x{alloc[1]}; target 3 "
                f"{signal['targets'][2]} rides with the stop")

    legs = dict(slot.get('legs') or {})

    # The stop first, always. Between the fill and the stop resting, the
    # position is naked; the targets can wait three more milliseconds.
    if stop_lots > 0:
        sl_id = _place_stop_leg(signal, instance, stop_lots, float(signal['stop']),
                                username, session_data)
        if sl_id:
            legs['SL'] = sl_id

    for name, idx in (('T1', 0), ('T2', 1)):
        if alloc[idx] <= 0:
            continue               # a short fill armed fewer targets
        leg_id = _place_target_leg(signal, instance, name, alloc[idx],
                                   float(signal['targets'][idx]),
                                   username, session_data)
        if leg_id:
            legs[name] = leg_id

    OpSignalStore.update_broker(signal_id, instance, {'legs': legs})


def _place_stop_leg(signal, instance, lots, trigger, username, session_data):
    from trading_app.app.routes.api import dispatch_stop_to_brokers
    from trading_app.app.routes.order_placement_api import OP_STRATEGY
    from trading_app.app.utils.mine_order_store import MineOrderStore

    side = exit_side(signal['action'])
    results = []
    for chunk in lot_chunks(lots):
        try:
            results += dispatch_stop_to_brokers(
                symbol=signal['symbol'], strike=signal['strike'],
                option_type=signal['option_type'],
                trigger_price=trigger, action=side,
                username=username, session_data=session_data,
                standard_lot=int(signal['lot_size']),
                gate=lambda i, _b, _want=instance: i == _want,
                lots_for=lambda _i, _n=chunk: _n,
                log_tag=f"OpSignal {signal['id']} stop",
            )
        except Exception as e:
            logger.error(f"[OpSignal] {signal['id']} broker {instance}: stop placement "
                         f"failed: {e}", exc_info=True)
            results.append({'broker': signal.get('broker') or 'unknown',
                            'instance': instance, 'success': False, 'error': str(e)})

    ok = [r for r in results if r.get('success')]
    if not ok:
        error = next((r.get('error') for r in results if r.get('error')), 'refused')
        # Loud: this is a live position with no stop behind it. The orphan
        # sweep will keep watching, and the next tick tries again.
        logger.error(f"[OpSignal] {signal['id']} broker {instance}: NO STOP RESTING — "
                     f"{error}")
        return None

    record = MineOrderStore.add_order({
        'mode': 'broker',
        'symbol': signal['symbol'], 'strike': signal['strike'],
        'option_type': signal['option_type'], 'action': side,
        'strategy': OP_STRATEGY, 'order_type': 'SL-M', 'type': 'SL-M',
        'instrument': 'BFO' if signal['symbol'] == 'SENSEX' else 'NFO',
        'price': trigger, 'trigger_price': trigger,
        'quantity': sum(int(r.get('quantity') or 0) for r in ok),
        'status': 'OPEN', 'username': username, 'source': 'orderplacement',
        'signal_id': signal['id'], 'leg': 'SL',
        'broker_order_ids': results,
    })
    return record['id']


def _place_target_leg(signal, instance, name, lots, limit_price, username, session_data):
    from trading_app.app.routes.api import _dispatch_order_to_brokers
    from trading_app.app.routes.order_placement_api import OP_STRATEGY
    from trading_app.app.utils.mine_order_store import MineOrderStore

    side = exit_side(signal['action'])
    summary, last_error = [], None
    for chunk in lot_chunks(lots):
        try:
            result = _dispatch_order_to_brokers(
                symbol=signal['symbol'], strike=signal['strike'],
                option_type=signal['option_type'], action=side,
                strategy=OP_STRATEGY, username=username, session_data=session_data,
                limit_price=limit_price,
                gate=lambda i, _b, _want=instance: i == _want,
                lots_for=lambda _i, _n=chunk: _n,
            )
            summary += result.get('summary') or []
            last_error = result.get('error') or last_error
        except Exception as e:
            logger.error(f"[OpSignal] {signal['id']} broker {instance}: {name} placement "
                         f"failed: {e}", exc_info=True)
            last_error = str(e)

    placed = [b for b in summary if (b.get('result') or {}).get('success')]
    if not placed:
        logger.warning(f"[OpSignal] {signal['id']} broker {instance}: {name} at "
                       f"{limit_price} refused — {last_error}")
        return None

    record = MineOrderStore.add_order({
        'mode': 'broker',
        'symbol': signal['symbol'], 'strike': signal['strike'],
        'option_type': signal['option_type'], 'action': side,
        'strategy': OP_STRATEGY, 'order_type': 'LIMIT', 'type': 'LIMIT',
        'instrument': 'BFO' if signal['symbol'] == 'SENSEX' else 'NFO',
        'price': limit_price,
        'quantity': sum(int(b.get('quantity') or 0) for b in placed),
        'status': 'OPEN', 'username': username, 'source': 'orderplacement',
        'signal_id': signal['id'], 'leg': name,
        'broker_order_ids': summary,
    })
    return record['id']


# ── managing a live ladder ────────────────────────────────────────────────

def _cancel_leg(order, username, session_data) -> bool:
    """Cancel one resting leg at its broker and mark the record. Never raises."""
    from trading_app.app.routes.api import _cancel_order_at_brokers
    from trading_app.app.utils.mine_order_store import MineOrderStore

    if not _is_resting(order):
        return True
    try:
        result = _cancel_order_at_brokers(order.get('broker_order_ids'),
                                          username, session_data)
    except Exception as e:
        logger.error(f"[OpSignal] cancel of {order.get('id')} failed: {e}")
        return False
    if result.get('success'):
        MineOrderStore.cancel_order(order['id'])
        return True
    # A refused cancel is usually a leg that filled while we were reading it.
    # The next tick's status sweep picks that up as a fill, which is the right
    # answer; it is not an error worth flattening over.
    logger.warning(f"[OpSignal] cancel of {order.get('id')} refused: {result.get('error')}")
    return False


def _stop_needs_sync(sl, level) -> bool:
    """Is the resting stop's trigger already where it should be?

    Only the trigger is ever asked to move. The stop is one leg's worth from
    the moment it is placed, so a target booking does not change what it has
    to cover — which is what removes the resize, and with it the window in
    which a stop covers more than is held.
    """
    if not _is_resting(sl):
        return False
    try:
        return abs(float(sl.get('trigger_price') or 0) - float(level)) > 1e-9
    except (TypeError, ValueError):
        return True


def _trail_stop(signal, slot, new_level, username, session_data) -> None:
    """Move the stop's trigger up behind a booked target.

    Trigger only — never quantity. The stop was placed at one leg's worth and
    stays there, so a target booking leaves it covering exactly the lot it
    always covered. That is the whole reason this ladder can rest at a broker
    at all: sell orders never add up to more than the position, so there is no
    moment at which a triggering stop could sell something already sold.

    A refused move deliberately does **not** flatten. The stop is still resting
    at the old, wider level and the position is still protected; the move is an
    improvement, and turning a failed improvement into a forced market exit
    would close a live trade over a rate limit.

    The retry is real, not aspirational: ``stop_level`` on the slot is the
    level the stop is MEANT to be at, written the moment a target books whether
    or not the broker accepted the move. Every tick compares it with what is
    actually resting and calls this again, so a stop that could not be moved at
    11:04 is moved at 11:04:03.
    """
    from trading_app.app.routes.api import _modify_order_at_brokers
    from trading_app.app.utils.mine_order_store import MineOrderStore

    signal_id, instance = signal['id'], slot['instance']
    sl = _record((slot.get('legs') or {}).get('SL'))
    if not _stop_needs_sync(sl, new_level):
        return

    # One call for every chunk of the stop: they all carry the same trigger and
    # keep the size they were placed with, so there is nothing to deal out.
    try:
        result = _modify_order_at_brokers(sl.get('broker_order_ids'), username,
                                          session_data,
                                          trigger_price=float(new_level))
    except Exception as e:
        logger.error(f"[OpSignal] {signal_id} broker {instance}: stop trail raised {e}")
        return

    if not result.get('success'):
        logger.warning(f"[OpSignal] {signal_id} broker {instance}: stop trail to "
                       f"{new_level} refused ({result.get('error')}) — the previous "
                       f"stop is still resting, retrying next tick")
        return

    MineOrderStore.update_order(sl['id'], {'price': new_level,
                                           'trigger_price': new_level})
    logger.info(f"[OpSignal] {signal_id} broker {instance}: stop trigger → {new_level}")


def _flatten_broker(signal, slot, username, session_data, reason) -> None:
    """Cancel everything this broker still has resting, then square it off."""
    from trading_app.app.routes.api import exit_selected_records
    from trading_app.app.utils.mine_order_store import MineOrderStore

    signal_id, instance = signal['id'], slot['instance']
    ids = {v for v in (slot.get('legs') or {}).values() if v}
    if slot.get('entry_record_id'):
        ids.add(slot['entry_record_id'])

    def mine():
        return [o for o in MineOrderStore.get_today_orders() if o.get('id') in ids]

    try:
        result = exit_selected_records(username, session_data, mine,
                                       log_tag=f'OpSignal {signal_id} {reason}')
    except Exception as e:
        logger.error(f"[OpSignal] {signal_id} broker {instance}: flatten failed: {e}",
                     exc_info=True)
        return

    OpSignalStore.update_broker(signal_id, instance,
                                {'stage': STAGE_FLAT, 'exit_reason': reason,
                                 'open_qty': 0})
    logger.info(f"[OpSignal] {signal_id} broker {instance}: FLAT ({reason}) — "
                f"{result.get('cancelled_orders')} cancelled, "
                f"{result.get('exited_positions')} squared off")


def _check_ladder(signal, slot, ltp, username, session_data) -> None:
    """Advance one broker's live ladder by whatever has happened since.

    The order of the checks is the design:

    1. The stop, before anything else. If it filled, the position is gone and
       every target left resting is a fresh short waiting to happen.
    2. Target 1, then target 2, both in this one pass — a fast move can fill
       both between two ticks, and treating them as alternatives would leave
       the stop trailing one step behind the position.
    3. Target 3, which rests nowhere and is only this level being watched.
    """
    signal_id, instance = signal['id'], slot['instance']
    legs = slot.get('legs') or {}
    lot_size = int(signal.get('lot_size') or 1) or 1
    targets = signal['targets']

    sl = _record(legs.get('SL'))
    if sl and sl.get('status') == 'EXECUTED':
        _flatten_broker(signal, slot, username, session_data, 'SL hit')
        return

    stage = slot.get('stage')
    open_qty = int(slot.get('open_qty') or 0)
    alloc = slot.get('alloc') or []

    # ── the resting targets ───────────────────────────────────────────
    for name, idx, done_stage, prior in (
            ('T1', 0, STAGE_T1_DONE, (STAGE_LIVE,)),
            ('T2', 1, STAGE_T2_DONE, (STAGE_LIVE, STAGE_T1_DONE))):
        if stage not in prior:
            continue
        leg = _record(legs.get(name))
        if not leg or leg.get('status') != 'EXECUTED':
            continue

        booked = int(leg.get('quantity') or 0) or (alloc[idx] * lot_size if idx < len(alloc) else 0)
        open_qty = max(open_qty - booked, 0)

        # Target 1 puts the stop at the entry; target 2 puts it at target 1's
        # own fill. Read from the leg record, so a price moved by hand on the
        # strip — or a fill a few paise away from the limit — is what the stop
        # follows, rather than the number the tip originally carried.
        if idx == 0:
            new_level = float(slot.get('entry_fill') or signal['entry'])
        else:
            t1 = _record(legs.get('T1'))
            new_level = float((t1 or {}).get('entry_price') or (t1 or {}).get('price')
                              or targets[0])

        # stop_level is written now, before the broker is asked, because it is
        # where the stop is MEANT to be. A refused modify then shows up as a
        # difference from what is resting, which is what the retry below keys
        # off — rather than being forgotten the moment the call fails.
        OpSignalStore.update_broker(signal_id, instance,
                                    {'open_qty': open_qty, 'stage': done_stage,
                                     'stop_level': float(new_level)})
        stage = done_stage
        slot['stage'], slot['open_qty'] = stage, open_qty
        slot['stop_level'] = float(new_level)

        if open_qty <= 0:
            # Nothing left to protect — the stop is the only sell order still
            # working, and it would open a short if it triggered.
            _cancel_leg(_record(legs.get('SL')), username, session_data)
            _flatten_broker(signal, slot, username, session_data,
                            f'{name} completed the ladder')
            return
        logger.info(f"[OpSignal] {signal_id} broker {instance}: {name} booked {booked} "
                    f"— {open_qty} left, stop to {new_level}")

    # The stop is moved once, below, rather than inside the loop. When a fast
    # move fills T1 and T2 between two ticks the loop records both and the sync
    # sends one modify straight to the final level — the intermediate trigger
    # would never have rested anywhere anyway, and asking the broker for it
    # costs a call and a chance to be refused.

    if open_qty <= 0:
        _flatten_broker(signal, slot, username, session_data, 'ladder complete')
        return

    # ── keep the stop where it is meant to be ─────────────────────────
    # This is both the move a target has just earned and the retry of one an
    # earlier tick could not make, and it is a no-op when the resting stop is
    # already right. A stop CANCELLED by hand is deliberately not re-placed —
    # that was the user's decision, and the price guard below still covers the
    # position.
    stop_level = slot.get('stop_level') or float(signal['stop'])
    sl = _record(legs.get('SL'))
    if _stop_needs_sync(sl, stop_level):
        _trail_stop(signal, slot, stop_level, username, session_data)
    elif not sl and open_qty > 0:
        lots_left = min(int(slot.get('sl_lots') or 0) or lot_chunks(1)[0],
                        max(open_qty // lot_size, 1))
        logger.warning(f"[OpSignal] {signal_id} broker {instance}: no stop on a live "
                       f"position — trying again")
        placed = _place_stop_leg(signal, instance, lots_left, float(stop_level),
                                 username, session_data)
        if placed:
            legs = {**legs, 'SL': placed}
            OpSignalStore.update_broker(signal_id, instance, {'legs': legs})

    # ── target 3, watched rather than rested ──────────────────────────
    if reached(signal['action'], ltp, float(targets[2])):
        logger.info(f"[OpSignal] {signal_id} broker {instance}: target 3 "
                    f"{targets[2]} touched at {ltp} — cancelling the stop and exiting")
        _cancel_leg(_record(legs.get('SL')), username, session_data)
        _flatten_broker(signal, slot, username, session_data, 'T3 hit')
        return

    # ── the stop's own price guard ────────────────────────────────────
    # If the premium is already at or through where the stop is meant to be and
    # no stop is actually resting there — refused modify, refused placement,
    # cancelled by hand — then nothing is protecting this position and waiting
    # for a broker to act is waiting for something that will not happen.
    stop_level = slot.get('stop_level')
    if ltp is not None and stop_level and reached(exit_side(signal['action']), ltp, float(stop_level)):
        sl = _record(legs.get('SL'))
        if not _is_resting(sl):
            logger.error(f"[OpSignal] {signal_id} broker {instance}: premium {ltp} is "
                         f"through the {stop_level} stop with nothing resting — "
                         f"exiting at market")
            _flatten_broker(signal, slot, username, session_data, 'stop breached, no order resting')


# ── the orphan backstop ───────────────────────────────────────────────────

def _reconcile_orphans(signals, username, session_data) -> None:
    """Flatten any account this signal has left SHORT the contract.

    The one failure this whole design cannot design away: between a target
    filling and the stop being shrunk, the stop covers more than is held, and a
    stop that triggers inside that window sells what is not there. Every other
    guard makes the window small. This is the one that notices when it was not
    small enough, and it is the reason the engine can be trusted to rest more
    sell quantity than it holds at all.

    A negative net in a contract this signal is trading is never legitimate:
    signal mode only ever buys an option and sells it back (or the mirror for a
    short tip). So this does not try to work out whose short it is — it closes
    it and says so.
    """
    from trading_app.app.routes.api import (_broker_client, _broker_positions,
                                            _place_exit_leg, _position_matches)

    for signal in signals:
        action = str(signal.get('action') or 'BUY').upper()
        for slot in (signal.get('brokers') or {}).values():
            if slot.get('stage') in BROKER_DONE_STAGES or not slot.get('filled_qty'):
                continue
            instance, broker = slot.get('instance'), slot.get('broker')
            try:
                kind, client = _broker_client(broker, instance, username, session_data)
                if not kind:
                    continue
                positions = _broker_positions(kind, client)
                if positions is None:
                    continue           # unreadable book: never act on a guess
                match = next((p for p in positions
                              if _position_matches(p.get('symbol'), signal['symbol'],
                                                   signal['strike'], signal['option_type'])), None)
                if not match:
                    continue
                held = int(match.get('net_qty') or 0)
                # "Wrong side" is relative to the tip: a BUY signal should
                # never be net short, a SELL signal never net long.
                wrong_side = held < 0 if action == 'BUY' else held > 0
                if not wrong_side:
                    continue

                side = 'BUY' if held < 0 else 'SELL'
                logger.error(f"[OpSignal] {signal['id']} broker {instance}: NET {held} in "
                             f"{signal['symbol']} {signal['strike']}{signal['option_type']} "
                             f"— wrong side for a {action} signal. Closing at market.")
                _place_exit_leg(kind, client, match, side, abs(held))
                OpSignalStore.update_broker(signal['id'], instance,
                                            {'orphan_closed_at': int(_time.time() * 1000),
                                             'orphan_qty': held})
            except Exception as e:
                logger.error(f"[OpSignal] orphan sweep for {signal.get('id')} "
                             f"broker {instance} failed: {e}", exc_info=True)


# ── end of day ────────────────────────────────────────────────────────────

def _handle_eod(signal, username, session_data) -> None:
    """Cancel what never triggered; square off what did.

    Run from inside the loop rather than from its own cron, so a restart after
    the cutoff still squares off on the thread's first tick instead of waiting
    for tomorrow.
    """
    for slot in list((signal.get('brokers') or {}).values()):
        stage = slot.get('stage')
        if stage in BROKER_DONE_STAGES:
            continue
        if stage == STAGE_PENDING_ENTRY:
            entry = _record(slot.get('entry_record_id'))
            _cancel_leg(entry, username, session_data)
            OpSignalStore.update_broker(signal['id'], slot['instance'],
                                        {'stage': STAGE_NO_FILL,
                                         'exit_reason': 'EOD, never triggered'})
            continue
        _flatten_broker(signal, slot, username, session_data, 'EOD square-off')

    OpSignalStore.update(signal['id'], {'phase': 'DONE', 'eod_handled': True,
                                        'finished_at': int(_time.time() * 1000)})
    logger.info(f"[OpSignal] {signal['id']}: end-of-day sweep done")


def cancel_signal(signal_id, username, session_data, reason='cancelled by hand') -> dict:
    """Stop managing one signal: cancel its legs, square off what it holds.

    Scoped to this signal's own records, so the same page's single-mode
    positions — and any other signal — are untouched.
    """
    from trading_app.app.routes.api import exit_selected_records

    remember_session(username, session_data)
    signal = OpSignalStore.get(signal_id)
    if not signal:
        return {'success': False, 'error': 'No such signal'}

    result = exit_selected_records(username, session_data,
                                   lambda: signal_records(signal_id),
                                   log_tag=f'OpSignal {signal_id} cancel')
    for slot in (signal.get('brokers') or {}).values():
        if slot.get('stage') not in BROKER_DONE_STAGES:
            OpSignalStore.update_broker(signal_id, slot['instance'],
                                        {'stage': STAGE_FLAT, 'exit_reason': reason,
                                         'open_qty': 0})
    OpSignalStore.update(signal_id, {'phase': 'CANCELLED', 'cancel_reason': reason,
                                     'finished_at': int(_time.time() * 1000)})
    logger.info(f"[OpSignal] {signal_id}: {reason} — {result.get('cancelled_orders')} "
                f"cancelled, {result.get('exited_positions')} squared off")
    return {**result, 'success': result.get('success', True), 'signal_id': signal_id}


def stop_all_signals(username, session_data, reason='exit-all') -> int:
    """Mark every live signal closed. Used by the page's Exit all button.

    The orders themselves have already been cancelled and flattened by the
    page-wide exit; this stops the engine from carrying on managing legs that
    are no longer there, and from re-arming a ladder over a position that has
    just been squared off.
    """
    stopped = 0
    for signal in OpSignalStore.get_active():
        for slot in (signal.get('brokers') or {}).values():
            if slot.get('stage') not in BROKER_DONE_STAGES:
                OpSignalStore.update_broker(signal['id'], slot['instance'],
                                            {'stage': STAGE_FLAT,
                                             'exit_reason': reason, 'open_qty': 0})
        OpSignalStore.update(signal['id'], {'phase': 'CANCELLED',
                                            'cancel_reason': reason,
                                            'finished_at': int(_time.time() * 1000)})
        stopped += 1
    if stopped:
        logger.info(f"[OpSignal] {reason}: {stopped} signal(s) stood down")
    return stopped


# ── the loop ──────────────────────────────────────────────────────────────

def tick(username, session_data=None) -> None:
    """One pass over every live signal. Safe to call directly from a test."""
    from trading_app.app.routes.api import _reconcile_open_orders
    from trading_app.app.routes.order_placement_api import option_ltp

    session_data = session_data if session_data is not None else _session(username)
    signals = OpSignalStore.get_active()
    if not signals:
        return

    # One status sweep for every leg of every signal. Forced, because the
    # engine cannot wait out the 8-second throttle while a stop is unshrunk —
    # and the sweep stamps the throttle, so the page's own polls ride on it.
    try:
        _reconcile_open_orders(username, session_data, force=True)
    except Exception as e:
        logger.warning(f"[OpSignal] status sweep failed: {e}")

    cutoff = _exit_cutoff_mins(username)
    now_mins = _now_mins()
    books = {}

    for signal in signals:
        try:
            if now_mins >= cutoff and not signal.get('eod_handled'):
                _handle_eod(signal, username, session_data)
                continue

            ltp = None
            slots = list((signal.get('brokers') or {}).values())
            if any(s.get('stage') in (STAGE_LIVE, STAGE_T1_DONE, STAGE_T2_DONE)
                   for s in slots):
                # One quote per signal per tick, and only when a position is
                # actually live — the brokers share one app-wide request
                # budget with the chart feeds.
                ltp = option_ltp(signal['symbol'], signal['strike'],
                                 signal['option_type'])

            for slot in slots:
                stage = slot.get('stage')
                if stage in BROKER_DONE_STAGES:
                    continue
                if stage == STAGE_PENDING_ENTRY:
                    _settle_entry(signal, slot, username, session_data, books)
                else:
                    _check_ladder(signal, slot, ltp, username, session_data)

            OpSignalStore.finish_if_all_brokers_done(signal['id'])
        except Exception as e:
            logger.error(f"[OpSignal] tick for {signal.get('id')} failed: {e}",
                         exc_info=True)

    if (_time.time() - _state['last_reconcile']) >= _RECONCILE_SECS:
        _state['last_reconcile'] = _time.time()
        try:
            _reconcile_orphans(OpSignalStore.get_active(), username, session_data)
        except Exception as e:
            logger.error(f"[OpSignal] orphan sweep failed: {e}", exc_info=True)


def _loop(username) -> None:
    logger.info('[OpSignal] engine thread started')
    while not _stop_event.is_set():
        try:
            if _now_mins() >= _HARD_STOP_MIN:
                logger.info('[OpSignal] past the hard stop — engine standing down')
                break
            tick(username)
            if not OpSignalStore.get_active():
                # Nothing left to manage. The thread ends rather than spinning;
                # arming another signal starts it again, and so does the
                # five-minute watchdog.
                logger.info('[OpSignal] no live signals — engine standing down')
                break
        except Exception as e:
            logger.error(f"[OpSignal] loop error: {e}", exc_info=True)
        _stop_event.wait(_POLL_SECS)
    logger.info('[OpSignal] engine thread stopped')


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def ensure_running(username, source: str = '') -> bool:
    """Start the engine if there is anything to manage. Idempotent.

    Called from the arm route (so a signal ticks at once), from the scheduler's
    start job, and from its five-minute watchdog — the house pattern, one
    idempotent function behind all three.
    """
    global _thread
    with _thread_lock:
        if is_running():
            return True
        if not OpSignalStore.get_active():
            return False
        if _now_mins() >= _HARD_STOP_MIN:
            return False
        _stop_event.clear()
        _state['last_reconcile'] = 0.0
        _thread = threading.Thread(target=_loop, args=(username,),
                                   name='op-signal-engine', daemon=True)
        _thread.start()
        logger.info(f"[OpSignal] engine started ({source or 'manual'})")
        return True


def stop() -> None:
    _stop_event.set()
