"""Broker-side helpers shared by the Order Placement engines.

This module used to be the pasted-tip "signal mode" state machine — a
three-lot ladder armed by hand from the page. That mode was removed on
2026-09-15; what stayed is the small set of helpers the Telegram-call engine
(``tg_call_engine.py``) was built on, kept here rather than moved so that
the load-bearing code paths did not change on the same day the page did:

* the in-memory session cache (``remember_session`` / ``_session``) that lets
  an engine thread reach Fyers and Kotak, which read their tokens from the
  Flask session;
* the end-of-day cutoff (``OP_SIGNAL_EXIT_HOUR/MINUTE``, default 15:15);
* ``_place_stop_leg`` — one SL-M at one broker, split by the 27-lot freeze
  limit, recorded as an ordinary ``strategy='op'`` row with ``signal_id`` and
  ``leg='SL'``;
* ``_cancel_leg`` — cancel one resting leg at its broker and mark the record.

Nothing here runs on its own: there is no thread, no tick and no scheduler
job. The engine that calls these is the one that owns the loop.
"""

from datetime import datetime, timedelta, timezone

from trading_app.app.utils.logger import logger

_IST = timezone(timedelta(hours=5, minutes=30))

_DEFAULT_EXIT_HHMM = (15, 15)

# Broker tokens live in the Flask session for Fyers and Kotak, and an engine
# runs on a thread that has none. The last session to view the page is kept
# here, in memory only — never written to a plan file, which would put live
# access tokens on disk. After a restart it is empty and every broker call
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

# The exchange refuses a single F&O order above this many lots, and neither
# shared dispatcher splits by it — split_quantity_by_freeze_limit exists but is
# only reached on the way out, by _place_exit_leg. So a leg splits itself on
# the way in. Kept as a lot count rather than borrowing that helper, whose
# lot-size fallbacks are stale (NIFTY 25) and would over-split a leg it could
# not price.
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


# ── the legs ──────────────────────────────────────────────────────────────

def _place_stop_leg(signal, instance, lots, trigger, username, session_data,
                    source='orderplacement', extra=None):
    """One SL-M at one broker. ``extra`` is merged into the stored record —
    the Telegram engine tags which of its two legs the stop covers."""
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
                log_tag=f"OpStop {signal['id']}",
            )
        except Exception as e:
            logger.error(f"[OpStop] {signal['id']} broker {instance}: stop placement "
                         f"failed: {e}", exc_info=True)
            results.append({'broker': signal.get('broker') or 'unknown',
                            'instance': instance, 'success': False, 'error': str(e)})

    ok = [r for r in results if r.get('success')]
    if not ok:
        error = next((r.get('error') for r in results if r.get('error')), 'refused')
        # Loud: this is a live position with no stop behind it. The orphan
        # sweep will keep watching, and the next tick tries again.
        logger.error(f"[OpStop] {signal['id']} broker {instance}: NO STOP RESTING — "
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
        'status': 'OPEN', 'username': username, 'source': source,
        'signal_id': signal['id'], 'leg': 'SL',
        'broker_order_ids': results,
        **(extra or {}),
    })
    return record['id']


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
        logger.error(f"[OpStop] cancel of {order.get('id')} failed: {e}")
        return False
    if result.get('success'):
        MineOrderStore.cancel_order(order['id'])
        return True
    # A refused cancel is usually a leg that filled while we were reading it.
    # The next tick's status sweep picks that up as a fill, which is the right
    # answer; it is not an error worth flattening over.
    logger.warning(f"[OpStop] cancel of {order.get('id')} refused: {result.get('error')}")
    return False
