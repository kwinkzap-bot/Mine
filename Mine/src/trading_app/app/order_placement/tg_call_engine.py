"""The Telegram-call state machine: a call arrives, a trade is run to its end.

The listener (``tg_calls_listener``) hands in a parsed tip; everything from
there to flat happens here, per broker:

    ONE stop-limit BUY for every leg rests  →  it fills  →  the fill is split
    into one leg per target in the plan, each with an SL-M SELL of its own
    and its own watched target  →  each leg's stop fires, or its target is
    touched and that leg alone is sold at market  →  flat.

**One order in, N legs out.** The entry is a single order for
``BROKER_N_TG_LOTS × legs`` — one line at the broker and one row on the
strip, not N orders competing for the same strike. Only once it has filled
does it become legs: ``brokers["1"]`` is broker 1's first leg and
``brokers["1:T3"]`` a later one, sharing that one ``entry_record_id`` and
holding ``BROKER_N_TG_LOTS`` each. A part fill fills them in the plan's
order, nearest target first, so the near legs go on the board whole rather
than every one of them short.

**How many legs, and where they get out, is the Legs dropdown on the page**
— ``TG_LEG_PLAN``, one of ``single`` / ``1-2`` / ``1-3`` / ``1-2-3``, read
at the moment a call is taken rather than cached, so it needs no restart.
``resolve_leg_plan`` settles the choice against the call that actually
arrived; see its docstring for the two rules that do that. A call already on
the board keeps the plan it was taken under: its legs are orders at a
broker, and a dropdown does not place or un-place one.

**Target 1 paying lifts every other leg to its own entry.** Once the nearest
leg has banked, the trade has paid for itself, and a leg that then runs back
through the entry is giving back a win rather than taking the loss the call
planned for. See ``_arm_breakeven`` and ``slot_stop``.

Because the two legs share an entry record, that record's quantity is the
pair's, not the leg's — so a leg's exit is capped at what the leg itself
holds (``exit_selected_records(max_qty=...)``). Without that cap the first
leg out would sell the other's lots and leave its stop resting over nothing.

Three things about the shape:

**Each stop covers the whole of its leg.** A leg trades one size and one
target, so its stop simply covers all of it — an exchange-side exit for
that leg, needing nothing from the app once it is resting. Two legs at one
broker are two stops for the two quantities, never one stop for the sum:
one stop for the pair would have to be re-sized the moment either leg
exits, and a stop being modified is a stop that is briefly not there.

**Targets rest nowhere.** Resting a LIMIT sell for a leg's quantity next to
its stop would be twice the held quantity working on the sell side: the
broker margins the second one as a fresh short, and a fast spike can fill
both. So a target is only a level this loop watches; when the premium
touches it the leg's stop is cancelled and the leg is sold at market — the
other leg, its stop and its target are untouched. That needs the app alive,
which the LaunchAgent sees to. The stops do not.

**A target is watched on the session high, not only on the last price.** A
last price is a point sample: a level watched with it alone is seen only if
the premium is still there when a poll lands, so a spike through a target
between two polls leaves no trace at all. NIFTY 22800 PE on 2026-10-06 is
the proof — T1 at 196 was wicked through, the polls all read below it, and
both legs rode down to the 167 stop. The session high is monotone, so a
level once traded through stays visible; a high STRICTLY ABOVE the one
recorded at the fill is proof of a trade since the fill at that price
(``touched_above``, and ``touched_below`` on the low for the stop-breach
guard). The high-water witness can only ever fire late, never falsely — so
near a target the loop drops to a one-second poll and stops paying for the
order sweep (``TG_TARGET_NEAR_PCT``, default 2 %), and the gap between the
touch and the market order that answers it is a second rather than ten.

**A call that cannot rest is skipped, not chased.** The entry is a stop BUY,
which must sit above the market. If the premium has already run past the
tip's price by the time the message lands, nothing is placed and an alert
says why. Target 2 is read and ignored.

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
    _record, _session, lot_chunks, reached, remember_session,
    signal_records)
from trading_app.app.order_placement.op_signal_store import (
    BROKER_DONE_STAGES, DONE_PHASES, STAGE_DEAD, STAGE_FLAT, STAGE_LIVE,
    STAGE_NO_FILL, STAGE_PENDING_ENTRY)
from trading_app.app.order_placement.tg_call_store import TgCallStore, TgTradeHistory
from trading_app.app.utils.logger import logger

_POLL_SECS = 3
# Within TG_TARGET_NEAR_PCT of a watched target the loop tightens to this and
# skips the order sweep: nothing about a resting stop can change in a second,
# but the premium crossing the target can.
_POLL_NEAR_SECS = 1
_PARTIAL_SETTLE_SECS = 20
# A market exit fills in seconds. A slot still without its exit fill this
# long after going flat is booked on what is known and flagged, rather than
# holding the engine up for a fill the broker is never going to report.
_BOOK_SETTLE_SECS = 90
_HARD_STOP_MIN = 15 * 60 + 29
_OPEN_MIN = 9 * 60 + 15

_DEFAULT_LIMIT_PCT = 1.0
_DEFAULT_NEAR_PCT = 2.0
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
    no fallback to the OP sizes, which size a different kind of order. It is
    the size of each of the two legs, not their sum."""
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


# ── the two legs ──────────────────────────────────────────────────────────

LEG_T1 = 'T1'
LEG_T3 = 'T3'

# What the dropdown on /orderplacement offers, and the target each choice
# rides to. The numbers are the call's targets COUNTING FROM ONE, so '1-3' is
# "a leg out at target 1 and a leg out at target 3".
LEG_PLANS = {
    'single': [1],
    '1-2': [1, 2],
    '1-3': [1, 3],
    '1-2-3': [1, 2, 3],
}
LEG_PLAN_LABELS = {
    'single': 'Single',
    '1-2': 'Double 1 2',
    '1-3': 'Double 1 3',
    '1-2-3': 'Thrice',
}
# The shape every call before the dropdown existed traded: one leg out at T1
# and one riding to the call's last target.
_DEFAULT_LEG_PLAN = '1-3'


def leg_plan_name(username) -> str:
    raw = str(_uvar(username, 'TG_LEG_PLAN', _DEFAULT_LEG_PLAN)).strip().lower()
    return raw if raw in LEG_PLANS else _DEFAULT_LEG_PLAN


def resolve_leg_plan(targets, plan_name=_DEFAULT_LEG_PLAN) -> list:
    """The legs a call actually runs: ``[{'leg': 'T1', 'target': 95.0}, ...]``.

    Two things the raw dropdown choice cannot decide on its own, because they
    depend on the call that arrived:

    * **An index past the end rides to the last target the call lists.** The
      channel usually posts three, but not always, and '1-3' on a two-target
      call means "out at 1, ride the rest to the end" — which is exactly what
      every call traded before the dropdown existed.
    * **Two indices that land on the same level are one leg.** A one-target
      call under 'Thrice' is one leg, not three identical ones stacked on the
      same strike at three times the size.

    The label is the target's real position in the call, so a '1-3' leg on a
    two-target call is labelled T2 — what it is, not what was asked for.
    """
    levels = [float(t) for t in (targets or [])]
    if not levels:
        return []
    out, seen = [], set()
    for want in LEG_PLANS.get(plan_name) or LEG_PLANS[_DEFAULT_LEG_PLAN]:
        n = min(int(want), len(levels))
        level = levels[n - 1]
        if level in seen:
            continue
        seen.add(level)
        out.append({'leg': f'T{n}', 'target': level})
    return out


def call_legs(call) -> list:
    """A stored call's leg plan. Calls taken before the dropdown existed carry
    ``target``/``target_far`` instead, so those are read back into the same
    shape — an in-flight call survives the restart that deploys this."""
    plan = call.get('leg_plan')
    if plan:
        return [{'leg': l['leg'], 'target': float(l['target'])} for l in plan]
    legs = [{'leg': LEG_T1, 'target': float(call.get('target') or 0)}]
    if call.get('target_far') is not None:
        legs.append({'leg': call.get('far_label') or LEG_T3,
                     'target': float(call['target_far'])})
    return legs


def far_target(targets) -> tuple:
    """``(label, level)`` of the last target a call lists, or ``(None, None)``
    when it lists one and there is nothing further to ride to. Still written
    onto every call for the P&L ledger and for anything reading a call taken
    before the leg plan existed."""
    levels = [float(t) for t in (targets or [])]
    if len(levels) < 2:
        return None, None
    return f'T{len(levels)}', levels[-1]


def slot_key(instance, leg=LEG_T1) -> str:
    """Where a leg lives in ``call['brokers']``: the T1 leg under the bare
    instance (the key every call before the T3 leg existed used), the T3
    leg under ``"<instance>:T3"``."""
    return str(instance) if leg == LEG_T1 else f'{instance}:{leg}'


def _key(slot) -> str:
    return slot_key(slot['instance'], slot.get('leg') or LEG_T1)


def slot_target(call, slot) -> float:
    """The level this leg is watched against: a level set by hand on the
    card wins, else the leg plan's own level for this leg. Read fresh every
    tick, so a channel edit or a hand edit moves it at once."""
    manual = slot.get('target_level')
    if manual:
        return float(manual)
    me = slot.get('leg') or LEG_T1
    for l in call_legs(call):
        if l['leg'] == me:
            return float(l['target'])
    return float(call.get('target') or 0)


def touched_above(level, last_price, day_high, high_at_fill):
    """Proof that the premium traded AT OR ABOVE ``level`` since the fill, or
    ``None``. The number returned is the price that proves it.

    Two independent witnesses, because one of them is not enough:

    * **The last price.** Only true while the premium is still at the level
      when a poll happens to land. The engine polls every few seconds, and a
      quote can take ten more when the provider is loaded, so a spike through
      the level between two polls leaves no trace in it at all. That is how
      NIFTY 22800 PE's T1 at 196 was missed on 2026-10-06: the premium wicked
      through it and was back below long before the next sample, and the leg
      rode on down to its 167 stop.
    * **The session high.** Monotone, so a level once traded through stays
      visible for the rest of the day however brief the trade was.

    The session high alone would be a liar: it may have been set long before
    this leg was filled — bought at 183 into a contract that opened at 250,
    every target below 250 would read as touched the instant the stop was
    armed. So it only counts as a witness when it is STRICTLY ABOVE the high
    at the moment of the fill, which is proof of a trade after the fill at
    that price. With no such baseline the high is not consulted at all, and
    the last price is left to do the watching on its own.
    """
    if last_price is not None and last_price >= level:
        return float(last_price)
    if (day_high is not None and high_at_fill is not None
            and day_high >= level and day_high > float(high_at_fill)):
        return float(day_high)
    return None


def touched_below(level, last_price, day_low, low_at_fill):
    """The mirror of :func:`touched_above` for a level under the market — the
    stop-breach guard, which only runs when nothing is resting at the broker
    to catch it. Same reasoning, same baseline rule."""
    if last_price is not None and last_price <= level:
        return float(last_price)
    if (day_low is not None and low_at_fill is not None
            and day_low <= level and day_low < float(low_at_fill)):
        return float(day_low)
    return None


def near_pct(username) -> float:
    """How close to a watched target counts as "approaching", as a percentage
    of the level. Inside it the loop drops to ``_POLL_NEAR_SECS`` and stops
    paying for the order sweep, so the gap between the touch and the market
    order that answers it is a second rather than ten."""
    try:
        v = float(_uvar(username, 'TG_TARGET_NEAR_PCT', _DEFAULT_NEAR_PCT))
    except (TypeError, ValueError):
        return _DEFAULT_NEAR_PCT
    return v if v > 0 else _DEFAULT_NEAR_PCT


def leg_share(call, slot, filled_lots: int) -> int:
    """This leg's cut of one entry order that bought for every leg.

    The legs are filled in the plan's order — nearest target first — so a
    part fill puts the nearer legs on the board whole rather than leaving
    every one of them short. Each takes at most the account's own ``lots``.
    """
    want = int(slot.get('lots') or 0)
    if want <= 0 or filled_lots <= 0:
        return 0
    me = slot.get('leg') or LEG_T1
    order = [l['leg'] for l in call_legs(call)]
    if me not in order:
        return min(filled_lots, want)
    siblings = {(sl.get('leg') or LEG_T1): sl for sl in (call.get('brokers') or {}).values()
                if int(sl.get('instance') or 0) == int(slot['instance'])}
    taken = sum(int((siblings.get(name) or {}).get('lots') or 0)
                for name in order[:order.index(me)])
    return max(min(filled_lots - taken, want), 0)


def _leg_label(call, slot) -> str:
    return slot.get('leg') or LEG_T1


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


def _eligible_targets(username) -> list:
    """The accounts a call can actually be placed at. The two refusals here
    are configuration mistakes, so each is alerted once a day."""
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
    return eligible


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
    far_label, target_far = far_target(plan['targets'])
    limit = entry_limit(entry, limit_pct(username))

    lot_size = resolve_standard_lot(symbol)
    if not lot_size:
        return skipped(f'could not resolve the {symbol} lot size — instrument list unavailable')

    eligible = _eligible_targets(username)
    if not eligible:
        # Nothing to trade with is nearly always a lie told by a stale read
        # of the env file: a broker login rewrites it, a reader catches the
        # prefix, and every flag below the cut reads as unset (see
        # UserEnvManager._read_env_file). That silently skipped four calls on
        # 2026-09-21/22. The file is authoritative, so ask it again before
        # giving up on a call that is otherwise good.
        from trading_app.app.utils.user_env import UserEnvManager
        UserEnvManager.clear_cache(username)
        eligible = _eligible_targets(username)
        if eligible:
            logger.warning(f"[TgCall] {label}: the cached env said no broker was configured; "
                           f"re-read the file and found {len(eligible)}")
    if not eligible:
        return skipped('no broker has BROKER_N_TG_ACTIVE=true with BROKER_N_TG_LOTS set',
                       once_key='no-brokers')

    # The legs this call runs at each account, from the plan chosen on the
    # page (Single / Double 1 2 / Double 1 3 / Thrice) resolved against the
    # targets this call actually lists. Each is the account's own size, and
    # they are bought in ONE order — the split happens on the fill.
    plan_name = leg_plan_name(username)
    legs = resolve_leg_plan(plan['targets'], plan_name)
    leg_names = [l['leg'] for l in legs]

    session_data = _session(username)
    call = TgCallStore.create({
        'username': username,
        'symbol': symbol, 'strike': strike, 'option_type': option_type,
        'action': 'BUY', 'entry': entry, 'stop': stop, 'targets': [float(t) for t in plan['targets']],
        'target': target, 'target_far': target_far, 'far_label': far_label, 'limit': limit,
        'leg_plan': legs, 'leg_plan_name': plan_name,
        'expiry': str(plan.get('expiry') or '') or None,
        'lot_size': lot_size, 'ltp_at_call': ltp,
        'phase': 'ARMED',
        'message_id': meta.get('message_id'), 'chat_id': meta.get('chat_id'),
        'source_text': text,
        'brokers': {},
    })
    call_id = call['id']

    def place_at(t):
        """This account's whole entry — every leg in one order, chunked by
        the freeze limit. Never raises."""
        instance = t['instance']
        results = []
        for chunk in lot_chunks(t['lots'] * len(leg_names)):
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
            # One order, both legs: the record carries the pair, and each
            # slot points back at it until the fill splits them.
            'signal_id': call_id, 'leg': 'ENTRY', 'tg_leg': '+'.join(leg_names),
            'broker_order_ids': results,
        })

        for leg_name in leg_names:
            TgCallStore.update_broker(call_id, slot_key(instance, leg_name), {
                'instance': instance, 'leg': leg_name,
                'broker': t['type'], 'name': t['name'],
                'lots': lots,
                'stage': STAGE_PENDING_ENTRY if ok else STAGE_DEAD,
                'entry_record_id': record['id'],
                'legs': {}, 'error': None if ok else error,
            })
        summary.append({'broker': t['type'], 'instance': instance, 'name': t['name'],
                        'legs': list(leg_names), 'lots': lots,
                        'total_lots': lots * len(leg_names),
                        'success': bool(ok), 'error': error, 'order_id': record['id']})

    accepted = [r for r in summary if r['success']]
    if not accepted:
        TgCallStore.update(call_id, {'phase': 'FAILED'})
        reason = next((r['error'] for r in summary if r['error']), 'every broker refused the entry')
        _alert(username, 'tg_call_order_failed', f'Telegram call — entry refused: {label}',
               reason, {'call_id': call_id, 'summary': summary})
        return {'success': False, 'error': reason, 'call_id': call_id, 'summary': summary}

    TgCallStore.update(call_id, {'phase': 'ENTRY_PENDING'})
    brokers = ', '.join(f"{r['name']} x{r['total_lots']} ({'+'.join(r['legs'])})"
                        for r in accepted)
    ladder = ' · '.join(f"{l['leg']} {l['target']}" for l in legs)
    _alert(username, 'tg_call_taken', f'Telegram call taken — {label}',
           f'SL-L BUY trigger {entry} limit {limit} · SL {stop} · {ladder} '
           f'({LEG_PLAN_LABELS.get(plan_name, plan_name)}) · {brokers}',
           {'call_id': call_id, 'plan': plan, 'limit': limit, 'summary': summary,
            'leg_plan': legs, 'leg_plan_name': plan_name})
    logger.info(f"[TgCall] {call_id} armed {label} trigger={entry} limit={limit} sl={stop} "
                f"plan={plan_name} ({ladder}) → {len(accepted)}/{len(summary)} "
                f"account(s), {'+'.join(leg_names)} in one order each, in "
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
            TgCallStore.update_broker(call_id, _key(slot), {'first_partial_at': _time.time()})
            return
        if (_time.time() - first) < _PARTIAL_SETTLE_SECS:
            return
        logger.warning(f"[TgCall] {call_id} {_key(slot)}: entry stuck part-filled at "
                       f"{filled_qty} — cancelling the remainder")
        _cancel_leg(entry, username, session_data)

    if filled_qty <= 0:
        TgCallStore.update_broker(call_id, _key(slot), {'stage': STAGE_NO_FILL,
                                                        'exit_reason': f'entry {status.lower()}'})
        logger.info(f"[TgCall] {call_id} {_key(slot)}: entry {status.lower()} with nothing filled")
        return

    lot_size = int(call.get('lot_size') or 0) or 1
    filled_lots = filled_qty // lot_size
    if filled_lots <= 0:
        TgCallStore.update_broker(call_id, _key(slot), {'stage': STAGE_NO_FILL})
        return

    # One order bought for both legs, so this leg takes its share of it.
    my_lots = leg_share(call, slot, filled_lots)
    if my_lots <= 0:
        TgCallStore.update_broker(call_id, _key(slot),
                                  {'stage': STAGE_NO_FILL,
                                   'exit_reason': f'only {filled_lots} lot(s) filled — '
                                                  f'none left for this leg'})
        logger.warning(f"[TgCall] {call_id} {_key(slot)}: {filled_lots} lot(s) filled, all of "
                       f"them the nearer leg's — this leg is not in the trade")
        return
    my_qty = my_lots * lot_size

    entry_fill = float(avg_price or entry.get('entry_price') or call['entry'])
    MineOrderStore.update_order(entry['id'], {'status': 'EXECUTED', 'entry_price': entry_fill,
                                              'quantity': filled_qty})
    _alert(username, 'tg_call_fill',
           f"Telegram call filled — {call['symbol']} {call['strike']} {call['option_type']}",
           f"{slot.get('name')} {_leg_label(call, slot)}: {my_lots} lot(s) at {entry_fill} "
           f"· placing SL-M {slot_stop(call, slot)}",
           {'call_id': call_id, 'instance': instance, 'leg': slot.get('leg') or LEG_T1,
            'qty': my_qty, 'price': entry_fill})
    _arm_stop(call, slot, my_lots, my_qty, entry_fill, username, session_data)


def _arm_stop(call, slot, filled_lots, filled_qty, entry_fill, username, session_data) -> None:
    """The SL-M for everything that filled. Stage LIVE either way: a refused
    stop is retried every tick, and the price guard covers the gap."""
    from trading_app.app.routes.order_placement_api import option_quote

    call_id, instance = call['id'], slot['instance']
    sl_id = _place_stop_leg(call, instance, filled_lots, slot_stop(call, slot),
                            username, session_data, source='telegram',
                            extra={'tg_leg': slot.get('leg') or LEG_T1})
    # The session's range AT THE FILL. Everything the leg's targets and its
    # stop-breach guard know about a spike they did not see live is measured
    # against this: a session high strictly above it is proof of a trade
    # after the fill. Taken here and not on the first tick because the first
    # tick is already seconds late, and a wick is not seconds long. If the
    # quote cannot answer, the baseline is taken on the first tick that can
    # (see _check_live) and until then only the last price watches.
    q = option_quote(call['symbol'], call['strike'], call['option_type'])
    TgCallStore.update_broker(call_id, _key(slot), {
        'stage': STAGE_LIVE, 'open_qty': filled_qty, 'open_lots': filled_lots,
        'entry_qty': filled_qty, 'entry_lots': filled_lots,
        'entry_fill': entry_fill, 'filled_at': _time.time(),
        'high_at_fill': q.get('high'), 'low_at_fill': q.get('low'),
        'target_watch': {'level': slot_target(call, slot), 'base': q.get('high')},
        'stop_watch': {'level': slot_stop(call, slot), 'base': q.get('low')},
        'legs': {'SL': sl_id} if sl_id else {},
    })
    if not sl_id:
        _alert(username, 'tg_call_order_failed',
               f"Telegram call — NO STOP RESTING: {call['symbol']} {call['strike']} {call['option_type']}",
               f"{slot.get('name')} {_leg_label(call, slot)}: the SL-M at {slot_stop(call, slot)} "
               f"was refused; retrying every tick",
               {'call_id': call_id, 'instance': instance, 'leg': slot.get('leg') or LEG_T1},
               once_key=f'nostop:{call_id}:{_key(slot)}')


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

    # What this leg still owns, and the only quantity this exit may sell.
    #
    # The records handed in cannot answer that on their own: the entry among
    # them bought for BOTH legs, so its quantity is the pair's and the net
    # it produces is too big by the other leg's holding. Worse, a leg whose
    # stop has already filled still nets positive against that shared entry
    # and would be sold a second time — a fresh short. So the leg's own
    # arithmetic decides: what it was given at the fill, less what it has
    # already sold.
    sold = sum(q for q, _ in _sell_fills(call_id, slot))
    remaining = max(int(slot.get('open_qty') or 0) - sold, 0)
    try:
        result = exit_selected_records(username, session_data, mine,
                                       log_tag=f'TgCall {call_id} {_key(slot)} {reason}',
                                       max_qty=remaining)
    except Exception as e:
        logger.error(f"[TgCall] {call_id} {_key(slot)}: flatten failed: {e}", exc_info=True)
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
            'signal_id': call_id, 'leg': 'EXIT', 'tg_leg': slot.get('leg') or LEG_T1,
            'broker_order_ids': [{'broker': ex.get('broker'), 'instance': instance,
                                  'order_id': oid, 'success': True,
                                  'quantity': int(ex.get('quantity') or 0)}
                                 for oid in (ex.get('order_ids') or [])],
        })
        legs[f"EXIT{len([k for k in legs if k.startswith('EXIT')]) + 1}"] = rec['id']

    TgCallStore.update_broker(call_id, _key(slot), {'stage': STAGE_FLAT, 'exit_reason': reason,
                                                    'open_qty': 0, 'legs': legs,
                                                    'booked': False, 'flat_at': _time.time()})
    _alert(username, 'tg_call_exit',
           f"Telegram call closed — {call['symbol']} {call['strike']} {call['option_type']}",
           f"{slot.get('name')} {_leg_label(call, slot)}: {reason} · "
           f"{result.get('cancelled_orders')} cancelled, {result.get('exited_positions')} squared off",
           {'call_id': call_id, 'instance': instance, 'leg': slot.get('leg') or LEG_T1,
            'reason': reason})
    logger.info(f"[TgCall] {call_id} {_key(slot)}: FLAT ({reason})")


def _watch_base(call, slot, field, level, extreme):
    """The session extreme that proves a trade through ``level`` happened
    while ``level`` was the level being watched.

    A session high or low is only evidence once it is measured against where
    the range stood when the level was set. ``field`` remembers both on the
    slot (``{'level': …, 'base': …}``) and is re-based the moment the level
    moves — by a channel edit, by the strip's price box, or by breakeven
    lifting the stop. Returns None while nothing can be read, which leaves
    the last price watching on its own.
    """
    watch = slot.get(field) or {}
    try:
        same = abs(float(watch.get('level')) - float(level)) < 0.005
    except (TypeError, ValueError):
        same = False
    base = watch.get('base') if same else None
    if base is not None:
        return float(base)
    # No usable baseline for this level: take one now if the range can be
    # read, and otherwise just remember the level, so that when a range does
    # arrive it is measured against THIS level and not the one before it.
    fresh = {'level': float(level), 'base': None if extreme is None else float(extreme)}
    if not same or fresh['base'] is not None:
        TgCallStore.update_broker(call['id'], _key(slot), {field: fresh})
        slot[field] = fresh
    return fresh['base']


def _arm_breakeven(call, slot, username) -> None:
    """Target 1 is banked, so every leg still in the trade stops out at its
    own entry instead of the call's stop.

    Armed on the CALL, not on the leg: the level each leg moves to is its own
    account's fill (``slot_stop``), but the event that arms it — the nearest
    target paying — is the whole call's. Only the plan's first leg arms it;
    a later leg reaching its own target has nothing left to protect behind
    it. No order is sent here: the tick reconciles every resting stop against
    ``slot_stop`` already, with its retry and its breach guard.
    """
    if not breakeven_on(username) or call.get('breakeven_at'):
        return
    legs = call_legs(call)
    if not legs or (slot.get('leg') or LEG_T1) != legs[0]['leg']:
        return
    others = [s for s in (call.get('brokers') or {}).values()
              if s.get('stage') == STAGE_LIVE and _key(s) != _key(slot)]
    TgCallStore.update(call['id'], {'breakeven_at': _time.time()})
    # And on the dict this tick is holding, so the legs still to be walked in
    # THIS pass already read the lifted stop out of slot_stop. Without it the
    # move waits for the next tick, with the premium running.
    call['breakeven_at'] = _time.time()
    if not others:
        return
    levels = ', '.join(f"{s.get('name')} {s.get('leg') or LEG_T1} → {s.get('entry_fill')}"
                       for s in others if s.get('entry_fill'))
    logger.info(f"[TgCall] {call['id']}: {legs[0]['leg']} banked — {len(others)} leg(s) "
                f"to breakeven ({levels})")
    _alert(username, 'tg_call_exit',
           f"Telegram call at breakeven — {call['symbol']} {call['strike']} {call['option_type']}",
           f"{legs[0]['leg']} paid, so the remaining leg(s) stop out at their own entry: {levels}",
           {'call_id': call['id'], 'legs': [_key(s) for s in others]})


def _check_live(call, slot, ltp, username, session_data,
                day_high=None, day_low=None) -> None:
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

    # The session extreme a level is read against, re-based whenever the
    # level itself moves. It has to be: the range since the FILL says
    # nothing about a level set later. A stop lifted to breakeven, or raised
    # by a channel edit, is almost always above a low the premium already
    # made — read against the fill's baseline it would look breached the
    # instant it was set, and the leg would be sold at market for nothing.
    base_high = _watch_base(call, slot, 'target_watch', slot_target(call, slot), day_high)
    base_low = _watch_base(call, slot, 'stop_watch', slot_stop(call, slot), day_low)

    target = slot_target(call, slot)
    at = touched_above(target, ltp, day_high, base_high)
    if at is not None:
        how = ('at the last price' if (ltp is not None and ltp >= target)
               else f'on the session high (last price {ltp})')
        logger.info(f"[TgCall] {call_id} {_key(slot)}: target {target} touched "
                    f"at {at} {how} — cancelling the stop and exiting")
        _cancel_leg(sl, username, session_data)
        _flatten(call, slot, username, session_data, f'{_leg_label(call, slot)} hit')
        _arm_breakeven(call, slot, username)
        return

    if not _is_resting(sl) and not (sl and sl.get('status') == 'CANCELLED'):
        # Refused at placement, or gone in a way nothing here did. A stop
        # CANCELLED by hand on the strip is left alone — that was a decision —
        # and the price guard below still covers the position.
        lots = int(slot.get('open_lots') or 0) or max(open_qty // (int(call.get('lot_size') or 1) or 1), 1)
        placed = _place_stop_leg(call, instance, lots, slot_stop(call, slot),
                                 username, session_data, source='telegram',
                                 extra={'tg_leg': slot.get('leg') or LEG_T1})
        if placed:
            TgCallStore.update_broker(call_id, _key(slot), {'legs': {**legs, 'SL': placed}})
            sl = _record(placed)

    # A resting stop that is not at this account's level — the channel edited
    # the SL and the broker refused the move — is retried here every tick. A
    # level the user set by hand on the strip IS this account's level (see
    # note_manual_edit), so it is never moved back. If the premium is already
    # through the wanted level, the stale order is not protection any more:
    # it is cancelled and the position sold at market.
    want = slot_stop(call, slot)
    if _is_resting(sl) and abs(float(sl.get('trigger_price') or 0) - want) >= 0.05:
        through = touched_below(want, ltp, day_low, base_low)
        if through is not None:
            logger.error(f"[TgCall] {call_id} {_key(slot)}: premium {through} is through the "
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
            logger.info(f"[TgCall] {call_id} {_key(slot)}: stop moved to {want}")

    if not _is_resting(sl):
        through = touched_below(want, ltp, day_low, base_low)
        if through is not None:
            logger.error(f"[TgCall] {call_id} {_key(slot)}: premium {through} is through the "
                         f"{want} stop with nothing resting — exiting at market")
            _flatten(call, slot, username, session_data, 'stop breached, no order resting')


def _handle_eod(call, username, session_data) -> None:
    for slot in list((call.get('brokers') or {}).values()):
        stage = slot.get('stage')
        if stage in BROKER_DONE_STAGES:
            continue
        if stage == STAGE_PENDING_ENTRY:
            _cancel_leg(_record(slot.get('entry_record_id')), username, session_data)
            TgCallStore.update_broker(call['id'], _key(slot),
                                      {'stage': STAGE_NO_FILL, 'exit_reason': 'EOD, never triggered'})
            continue
        _flatten(call, slot, username, session_data, 'EOD square-off')
    TgCallStore.update(call['id'], {'phase': 'DONE', 'eod_handled': True,
                                    'finished_at': int(_time.time() * 1000)})
    logger.info(f"[TgCall] {call['id']}: end-of-day sweep done")


def _exit_errors_by_instance(exit_result) -> set:
    """The broker slots whose square-off the page-wide exit could not do."""
    bad = set()
    for row in (exit_result or {}).get('summary') or []:
        if row.get('errors'):
            try:
                bad.add(int(row.get('instance') or 0))
            except (TypeError, ValueError):
                continue
    return bad


def _record_page_exits(call, slot, exit_result, username, claimed=None) -> dict:
    """Write an EXIT record for what the page-wide exit sold on this leg.

    ``route_scoped_exit`` sells the net position and returns the orders it
    sent, but writes no store record of its own — so without this the fill
    can never be read back and the leg books at ``exit_price: None`` with a
    "booked without a full exit fill" alert. That is what every Exit all did.

    The page sells the *net* of both legs in one market order, so the sale is
    split across the legs it belonged to, oldest leg first, capped at what
    each still holds.
    """
    from trading_app.app.routes.order_placement_api import OP_STRATEGY
    from trading_app.app.utils.mine_order_store import MineOrderStore

    legs = dict(slot.get('legs') or {})
    want = int(slot.get('open_qty') or 0)
    if want <= 0:
        return legs
    claimed = {} if claimed is None else claimed
    for n, ex in enumerate((exit_result or {}).get('exits') or []):
        if int(ex.get('instance') or 0) != int(slot['instance']):
            continue
        if (str(ex.get('symbol') or '').upper() != str(call['symbol']).upper()
                or int(ex.get('strike') or 0) != int(call['strike'])
                or str(ex.get('option_type') or '').upper() != str(call['option_type']).upper()):
            continue
        # What is left of this market order after the legs already booked
        # against it. Tracked here rather than on the caller's dict, which
        # is the route's JSON response.
        left = int(ex.get('quantity') or 0) - claimed.get(n, 0)
        take = min(left, want)
        if take <= 0:
            continue
        claimed[n] = claimed.get(n, 0) + take
        rec = MineOrderStore.add_order({
            'mode': 'broker',
            'symbol': call['symbol'], 'strike': call['strike'], 'option_type': call['option_type'],
            'action': ex.get('side') or 'SELL',
            'strategy': OP_STRATEGY, 'order_type': 'MARKET', 'type': 'MARKET',
            'instrument': 'BFO' if call['symbol'] == 'SENSEX' else 'NFO',
            'price': None, 'quantity': take,
            'status': 'OPEN', 'username': username, 'source': 'telegram',
            'signal_id': call['id'], 'leg': 'EXIT', 'tg_leg': slot.get('leg') or LEG_T1,
            'broker_order_ids': [{'broker': ex.get('broker'), 'instance': slot['instance'],
                                  'order_id': oid, 'success': True, 'quantity': take}
                                 for oid in (ex.get('order_ids') or [])],
        })
        legs[f"EXIT{len([k for k in legs if k.startswith('EXIT')]) + 1}"] = rec['id']
        want -= take
        if want <= 0:
            break
    return legs


def stop_all_calls(username, session_data, reason='exit-all', exit_result=None) -> int:
    """Mark every live call closed — the page-wide exit has already cancelled
    and flattened the orders; this stops the engine re-placing a stop over a
    position that is no longer there.

    Two things the caller's ``exit_result`` decides, and both are the
    difference between a clean stand-down and a lie:

    * **A leg the broker refused to square off stays LIVE.** On 2026-09-21 an
      account was short of margin, its exit failed, and the leg was marked
      FLAT anyway — so a real position sat there with its stop cancelled and
      nothing watching it. A refusal means the position is still held, so the
      engine keeps it: the tick re-places the stop and watches the target.
    * **What was sold is recorded**, so the sweep reads the fill back and the
      leg books at a real price instead of ``None``.
    """
    failed = _exit_errors_by_instance(exit_result)
    claimed = {}                    # exit index -> quantity already booked to a leg
    stopped = kept = 0
    for call in TgCallStore.get_active():
        held = []
        for slot in (call.get('brokers') or {}).values():
            if slot.get('stage') in BROKER_DONE_STAGES:
                continue
            if int(slot.get('instance') or 0) in failed and slot.get('stage') == STAGE_LIVE:
                logger.error(f"[TgCall] {call['id']} {_key(slot)}: the {reason} could not square "
                             f"this account off — the leg stays live and the tick keeps managing it")
                _alert(username, 'tg_call_order_failed',
                       f"Telegram call still held — {call['symbol']} {call['strike']} {call['option_type']}",
                       f"{slot.get('name')} {_leg_label(call, slot)}: {reason} failed at this "
                       f"account, so the position is still open and is still being managed. "
                       f"Check the margin and exit it by hand.",
                       {'call_id': call['id'], 'slot': _key(slot), 'reason': reason},
                       once_key=f"exitfailed:{call['id']}:{_key(slot)}")
                held.append(slot)
                kept += 1
                continue
            legs = _record_page_exits(call, slot, exit_result, username, claimed)
            TgCallStore.update_broker(call['id'], _key(slot),
                                      {'stage': STAGE_FLAT, 'exit_reason': reason, 'open_qty': 0,
                                       'legs': legs, 'booked': False, 'flat_at': _time.time()})
        if held:
            # Something is still open under this call, so it is not finished.
            continue
        TgCallStore.update(call['id'], {'phase': 'CANCELLED', 'cancel_reason': reason,
                                        'finished_at': int(_time.time() * 1000)})
        stopped += 1
    if stopped or kept:
        logger.info(f"[TgCall] {reason}: {stopped} call(s) stood down"
                    + (f", {kept} leg(s) kept live (the exit failed there)" if kept else ""))
    if kept:
        ensure_running(username, source=reason)
    return stopped


def call_records(call_id: str) -> list:
    return signal_records(call_id)


# ── the user changed their mind ───────────────────────────────────────────

def breakeven_on(username) -> bool:
    """Whether target 1 being hit lifts every remaining leg's stop to its own
    entry. On by default: once the first leg has banked its target the trade
    has paid for itself, and a leg that then runs back past the entry is
    giving back a win rather than taking the loss the call planned for."""
    return _flag(username, 'TG_BREAKEVEN_AFTER_T1', 'true')


def slot_stop(call, slot) -> float:
    """The level this account's stop is meant to rest at, in order:

    1. a level set by hand on the strip — that was a decision, and nothing
       here moves an order back off it;
    2. this leg's own entry fill, once the call's first leg has banked its
       target and breakeven is armed — never DOWN, so a call whose stop is
       already above the entry keeps the better level;
    3. the call's plan.

    Nothing places this: the tick already reconciles a resting stop that is
    not at ``slot_stop``, with the retry and the breach guard that come with
    it, so breakeven rides on the path that was already tested.
    """
    manual = slot.get('stop_level')
    if manual:
        return float(manual)
    want = float(call['stop'])
    if call.get('breakeven_at'):
        fill = slot.get('entry_fill')
        if fill:
            want = max(want, float(fill))
    return want


def note_manual_edit(order, new_price, new_limit=None) -> dict:
    """The strip's price box moved one of this engine's legs. Record it, so
    the tick manages the position on the number the user chose instead of
    moving the order back to the plan's.

    * SL leg   → that leg's ``stop_level``. Per leg, deliberately: a stop
                 moved by hand on one leg (or one account) says nothing about
                 the other. A later edit of the channel message overrides it.
    * ENTRY leg → the call's entry and limit (the plan follows it; the other
                 entry orders of the call are not moved — the strip moved
                 one order, and that is the one that moved).
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
        # The slot whose stop this record is. Two legs at one account each
        # have a stop of their own, so the record, not the instance, says
        # which level moved.
        owners = [sl for sl in (call.get('brokers') or {}).values()
                  if (sl.get('legs') or {}).get('SL') == order.get('id')]
        if not owners:
            owners = [sl for sl in (call.get('brokers') or {}).values()
                      if int(sl.get('instance') or 0) in instances]
        for sl in owners:
            TgCallStore.update_broker(call_id, _key(sl), {'stop_level': float(new_price),
                                                          'stop_source': 'manual'})
        keys = sorted(_key(sl) for sl in owners)
        logger.info(f"[TgCall] {call_id} {keys}: stop set by hand to {new_price}")
        return {'call_id': call_id, 'stop_level': float(new_price), 'instances': sorted(instances),
                'slots': keys}

    if leg == 'ENTRY':
        updates = {'entry': float(new_price)}
        if new_limit:
            updates['limit'] = float(new_limit)
        TgCallStore.update(call_id, updates)
        logger.info(f"[TgCall] {call_id}: entry set by hand to {new_price} (limit {new_limit})")
        return {'call_id': call_id, **updates}
    return {}


def note_manual_target(username, call_id, slot_key_or_instance, level) -> dict:
    """Move one leg's watched target by hand, from the call's card.

    The stop is an order at the broker, so moving it is a modify; the target
    rests nowhere (see the module docstring), so moving it is only this
    write — the tick reads ``slot_target`` fresh every three seconds and
    watches the new level from then on. Per leg, like ``stop_level``: moving
    the T1 leg's target says nothing about the T3 leg's.

    A level already through the market is refused rather than obeyed: it
    would fire the exit on the next tick, and "sell now" is the ✕ and Exit
    buttons, not a target edit.
    """
    from trading_app.app.routes.order_placement_api import option_ltp

    call = TgCallStore.get(call_id)
    if not call:
        return {'success': False, 'error': 'No such call'}
    if call.get('phase') in DONE_PHASES:
        return {'success': False, 'error': f"This call is already {str(call['phase']).lower()}"}

    key = str(slot_key_or_instance)
    slot = (call.get('brokers') or {}).get(key)
    if not slot:
        return {'success': False, 'error': f'No leg {key} on this call'}
    if slot.get('stage') in BROKER_DONE_STAGES:
        return {'success': False, 'error': f"That leg is already out ({slot.get('stage')})"}

    try:
        level = float(level)
    except (TypeError, ValueError):
        return {'success': False, 'error': 'The target must be a number'}
    if level <= 0:
        return {'success': False, 'error': 'The target must be above zero'}

    entry = float(slot.get('entry_fill') or call.get('entry') or 0)
    if entry and level <= entry:
        return {'success': False, 'error': f'A target at {level} is not above the entry {entry}'}
    want_stop = slot_stop(call, slot)
    if level <= want_stop:
        return {'success': False, 'error': f'A target at {level} is at or below the stop {want_stop}'}

    if slot.get('stage') == STAGE_LIVE:
        try:
            ltp = option_ltp(call['symbol'], call['strike'], call['option_type'])
        except Exception:
            ltp = None
        if ltp is not None and reached('BUY', ltp, level):
            return {'success': False,
                    'error': f'The premium is already at {ltp}; a target at {level} would sell '
                             f'on the next tick. Use ✕ or Exit to sell now.'}

    was = slot_target(call, slot)
    TgCallStore.update_broker(call_id, key, {'target_level': level, 'target_source': 'manual'})
    label = f"{call['symbol']} {call['strike']} {call['option_type']}"
    logger.info(f"[TgCall] {call_id} {key}: target set by hand {was} → {level}")
    _alert(username, 'tg_call_taken', f'Telegram call target moved — {label}',
           f"{slot.get('name')} {_leg_label(call, slot)}: target {was} → {level} (by hand)",
           {'call_id': call_id, 'slot': key, 'target': level})
    return {'success': True, 'call_id': call_id, 'slot': key, 'target': level, 'was': was}


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
    cancelled_records = set()
    for slot in list((call.get('brokers') or {}).values()):
        stage = slot.get('stage')
        if stage in BROKER_DONE_STAGES:
            continue
        if stage == STAGE_PENDING_ENTRY:
            # One entry order covers both legs of an account, so it is
            # cancelled — and counted — once.
            rec_id = slot.get('entry_record_id')
            if rec_id not in cancelled_records:
                _cancel_leg(_record(rec_id), username, session_data)
                cancelled_records.add(rec_id)
                cancelled += 1
            TgCallStore.update_broker(call_id, _key(slot),
                                      {'stage': STAGE_NO_FILL, 'exit_reason': reason})
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
        far_label, target_far = far_target(plan.get('targets'))
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
            seen_records = set()
            for slot in pending:
                # Both legs of an account share one entry order: modify it
                # once, not once per leg.
                if slot.get('entry_record_id') in seen_records:
                    continue
                seen_records.add(slot.get('entry_record_id'))
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
                TgCallStore.update_broker(call_id, _key(slot),
                                          {'stop_level': None, 'stop_source': 'channel'})
        if not live and pending:
            changes.append(f'stop {old_stop} → {stop} (for when the entry fills)')
        elif live and not moved_any and not any('stop modify' in p for p in problems):
            changes.append(f'stop {old_stop} → {stop}')

    # ── targets ───────────────────────────────────────────────────────
    # Every watched level moves; none of them is at a broker. The plan is
    # re-resolved against the edited targets, but only for the legs that are
    # already on the board — an edit does not place a leg or un-place one,
    # so a leg whose index the edit removed keeps watching the last level
    # the call still lists.
    old_legs = {l['leg']: l['target'] for l in call_legs(call)}
    new_levels = {l['leg']: l['target'] for l in
                  resolve_leg_plan(plan.get('targets') or [target],
                                   call.get('leg_plan_name') or leg_plan_name(username))}
    fallback = float((plan.get('targets') or [target])[-1])
    merged = [{'leg': name, 'target': new_levels.get(name, fallback)} for name in old_legs]
    if any(abs(l['target'] - old_legs[l['leg']]) >= 0.005 for l in merged):
        TgCallStore.update(call_id, {
            'target': target, 'target_far': target_far, 'far_label': far_label,
            'targets': [float(t) for t in plan.get('targets') or [target]],
            'leg_plan': merged})
        # The channel spoke later than the hand, so its numbers win — the
        # same rule the stop override follows.
        for slot in slots:
            if slot.get('target_level'):
                TgCallStore.update_broker(call_id, _key(slot),
                                          {'target_level': None, 'target_source': 'channel'})
                changes.append(f"{slot.get('name')} {_leg_label(call, slot)}: hand-set target cleared")
        for l in merged:
            if abs(l['target'] - old_legs[l['leg']]) >= 0.005:
                changes.append(f"{l['leg']} {old_legs[l['leg']]} → {l['target']}")

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

def _sell_fills(call_id, slot) -> list:
    """``(qty, price)`` of every executed sell leg this slot placed — the
    SL-M, the market exits, or both when a stop filled during a target exit.
    The slot's own legs only: the other leg at the same account sells the
    same contract, and its fills are its own row."""
    own = {v for v in (slot.get('legs') or {}).values() if v}
    out = []
    for o in signal_records(call_id):
        if o.get('status') != 'EXECUTED' or str(o.get('action') or '').upper() != 'SELL':
            continue
        if o.get('id') not in own:
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

    fills = _sell_fills(call['id'], slot)
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
        'leg': _leg_label(call, slot),
        'lots': lots, 'lot_size': lot_size, 'qty': entry_qty, 'sold_qty': sold_qty,
        'trigger': call['entry'], 'limit': call.get('limit'), 'sl': call['stop'],
        'target': slot_target(call, slot),
        'entry_price': entry_price, 'exit_price': round(exit_price, 2) if exit_price else None,
        'points': round(points, 2) if points is not None else None,
        'pnl_per_lot': pnl_per_lot, 'pnl': pnl,
        'exit_reason': slot.get('exit_reason'),
        'filled_at': int((slot.get('filled_at') or 0) * 1000) or None,
        'flat_at': int((slot.get('flat_at') or 0) * 1000) or None,
        'complete': complete,
    })
    TgCallStore.update_broker(call['id'], _key(slot), {'booked': True, 'trade_id': row['id'],
                                                       'pnl': pnl, 'pnl_per_lot': pnl_per_lot})
    logger.info(f"[TgCall] {call['id']} {_key(slot)}: booked {lots} lot(s) "
                f"{entry_price} → {exit_price} = {pnl_per_lot}/lot, {pnl} total"
                + ('' if complete else ' (INCOMPLETE — exit fill not fully known)'))
    if not complete:
        _alert(username, 'tg_call_order_failed',
               f"Telegram call booked without a full exit fill — {call['symbol']} {call['strike']} {call['option_type']}",
               f"{slot.get('name')} {_leg_label(call, slot)}: sold {sold_qty} of {entry_qty} known. "
               f"Check the account.",
               {'call_id': call['id'], 'instance': instance, 'leg': slot.get('leg') or LEG_T1,
                'trade_id': row['id']},
               once_key=f"incomplete:{call['id']}:{_key(slot)}")
    return row


def _book_pending(username) -> int:
    booked = 0
    for call in TgCallStore.get_unbooked():
        for slot in (call.get('brokers') or {}).values():
            if slot.get('stage') != STAGE_FLAT or slot.get('booked'):
                continue
            try:
                entry_qty = int(slot.get('entry_qty') or 0)
                sold = sum(q for q, _ in _sell_fills(call['id'], slot))
                waited = _time.time() - float(slot.get('flat_at') or 0)
                if entry_qty and sold < entry_qty and waited < _BOOK_SETTLE_SECS:
                    continue                 # the exit is still filling
                _book_slot(call, slot, username)
                booked += 1
            except Exception as e:
                logger.error(f"[TgCall] booking {call.get('id')} {_key(slot)} "
                             f"failed: {e}", exc_info=True)
    return booked


def history(days: int = 0) -> list:
    return TgTradeHistory.rows(days)


# ── the loop ──────────────────────────────────────────────────────────────

def tick(username, session_data=None, light=False) -> bool:
    """One pass over every live call. Returns True when some leg is close
    enough to its target that the loop should come back in a second.

    ``light`` skips the order sweep. The sweep is the expensive half of a
    tick — four brokers' order books, often seconds of it — and it answers
    only "has the stop filled", which the exchange will still be able to tell
    us one second later. The premium crossing a target will not wait, and it
    is the only thing this engine watches that has no exchange-side order
    behind it, so near a target the price is read and the sweep is not.
    """
    from trading_app.app.routes.api import _reconcile_open_orders
    from trading_app.app.routes.order_placement_api import option_quote

    session_data = session_data if session_data is not None else _session(username)
    calls = TgCallStore.get_active()
    if not calls and not TgCallStore.get_unbooked():
        return False

    # One status sweep for every leg of every call. Forced, because the
    # engine cannot wait out the 8-second throttle while a position is
    # unprotected — and the sweep stamps the throttle, so the page's own
    # polls ride on it.
    if not light:
        try:
            _reconcile_open_orders(username, session_data, force=True)
        except Exception as e:
            logger.warning(f"[TgCall] status sweep failed: {e}")

    cutoff = _exit_cutoff_mins(username)
    now_mins = _now_mins()
    books = {}
    band = near_pct(username) / 100.0
    approaching = False

    for call in calls:
        try:
            if now_mins >= cutoff and not call.get('eod_handled'):
                _handle_eod(call, username, session_data)
                continue

            slots = list((call.get('brokers') or {}).values())
            ltp = day_high = day_low = None
            if any(s.get('stage') == STAGE_LIVE for s in slots):
                q = option_quote(call['symbol'], call['strike'], call['option_type'])
                ltp, day_high, day_low = q.get('last_price'), q.get('high'), q.get('low')
                if ltp is None and day_high is None:
                    # Blind: the stop still rests at the exchange, but the
                    # target is watched here and cannot be seen. Say so
                    # loudly, once, so the position is managed by hand.
                    watched = ' / '.join(f"{lbl} {lvl}" for lbl, lvl in
                                         (('T1', call.get('target')),
                                          (call.get('far_label'), call.get('target_far')))
                                         if lvl is not None)
                    _alert(username, 'tg_call_order_failed',
                           f"Telegram call — NO QUOTE: {call['symbol']} {call['strike']} {call['option_type']}",
                           f"Cannot read the premium, so {watched} is NOT being watched. The "
                           f"stop at {call['stop']} still rests. Manage this one by hand.",
                           {'call_id': call['id']}, once_key=f"noquote:{call['id']}")

            for slot in slots:
                stage = slot.get('stage')
                if stage in BROKER_DONE_STAGES:
                    continue
                if stage == STAGE_PENDING_ENTRY:
                    _settle_entry(call, slot, username, session_data, books)
                else:
                    if ltp is not None:
                        level = slot_target(call, slot)
                        approaching = approaching or ltp >= level * (1 - band)
                    _check_live(call, slot, ltp, username, session_data,
                                day_high=day_high, day_low=day_low)

            TgCallStore.finish_if_all_brokers_done(call['id'])
        except Exception as e:
            logger.error(f"[TgCall] tick for {call.get('id')} failed: {e}", exc_info=True)

    # After the sweep above has read the exit fills back. A light tick did no
    # sweep, so there is no new fill to book on it.
    if not light:
        _book_pending(username)
    return approaching


def _loop(username) -> None:
    logger.info('[TgCall] engine thread started')
    near = False
    swept = 0.0
    while not _stop_event.is_set():
        try:
            if _now_mins() >= _HARD_STOP_MIN:
                logger.info('[TgCall] past the hard stop — engine standing down')
                break
            # Near a target the price is read every second and the order
            # sweep is skipped — but not skipped forever: a stop that filled
            # while the premium hovered under its target has to be seen, so a
            # full tick still runs at the ordinary cadence underneath.
            light = near and (_time.time() - swept) < _POLL_SECS
            if not light:
                swept = _time.time()
            near = tick(username, light=light)
            if not TgCallStore.get_active() and not TgCallStore.get_unbooked():
                logger.info('[TgCall] no live calls — engine standing down')
                break
        except Exception as e:
            logger.error(f"[TgCall] loop error: {e}", exc_info=True)
        _stop_event.wait(_POLL_NEAR_SECS if near else _POLL_SECS)
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
           'note_manual_edit', 'note_manual_target', 'slot_stop', 'slot_target', 'leg_share',
           'touched_above', 'touched_below', 'near_pct',
           'slot_key', 'far_target',
           'LEG_T1', 'LEG_T3']
