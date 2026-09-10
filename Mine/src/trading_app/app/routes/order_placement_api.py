"""API routes for the Order Placement page (``/orderplacement``).

Its own blueprint, its own flag family and its own files, so nothing here can
change what the OI Profile panel or the algos do.

**Routing.** An order fired from this page goes to every broker carrying
``BROKER_N_OP_ACTIVE=true`` and to no other — the same shape as the OI Profile
panel routing on ``BROKER_N_INTRINSIC_ACTIVE``, with its own flag so opting a
broker in to one panel never opts it in to the other. Default is false: a
broker opts in, it is never opted in by having been left active.

**Placement only.** This page attaches no stop-loss and starts no auto-exit
monitor. ``_dispatch_order_to_brokers`` gates both of those on the strategy
key, and ``op`` is excluded from both — what this page places, it places, and
the only things that can move or remove that order afterwards are the price and
cancel buttons on the page itself.

**Its own orders only.** Every record written here carries ``strategy='op'``,
and the listing, edit, cancel and exit routes below all filter on it. The
pending strip on the page is therefore this page's own book: an order placed
from OI Profile or by an algo is neither listed nor editable from here, and the
generic ``/api/orders`` grid still shows everything as before.

**Its own exit.** ``/exit-all`` cancels what this page has resting and squares
off what its own records add up to holding — the pad's EXIT button. It is the
counterpart of the OI Profile screen's ``/api/order/exit-all``, which is scoped
the same way to its own ``strategy='intrinsic'`` records; neither button
reaches the other panel's positions, and neither is an account-wide
liquidation.
"""

import time as _time

from flask import Blueprint, jsonify, request, session

from trading_app.app.utils.logger import logger
from trading_app.app.utils.user_auth import require_user_auth

order_placement_bp = Blueprint('order_placement', __name__)

# The strategy key stamped on every order this page places. It is what
# _dispatch_order_to_brokers gates the BROKER_N_OP_ACTIVE routing on, what
# keeps the auto stop-loss off, and what every route below filters by.
OP_STRATEGY = 'op'

# The three index options this page trades. Order matters — the pad's dropdown
# follows it. Anything else is refused here rather than at the broker.
OP_SYMBOLS = ('NIFTY', 'BANKNIFTY', 'SENSEX')

# SENSEX options are a BSE contract; the other two are NSE.
OP_EXCHANGE = {'SENSEX': 'BFO'}

# Last resort only, when the instrument dump cannot be read at all. The live
# chain is always preferred: the exchange revises these, and a stale step puts
# the ± buttons on strikes that do not exist.
OP_FALLBACK_STEP = {'NIFTY': 50, 'BANKNIFTY': 100, 'SENSEX': 100}

# The chain is a slow read (a Kite dump, or a Fyers CSV download), and the step
# of a live chain does not change during a session. Spot is never cached.
_STEP_TTL_S = 1800
_chain_cache = {}          # symbol -> (fetched_at, {step, lot_size, expiry})

_TRUE = ('true', '1', 'yes')


def _user() -> str:
    return session.get('username') or 'Mine'


def _fail(e: Exception, what: str):
    logger.error(f"[OrderPlacement API] {what} failed: {e}", exc_info=True)
    return jsonify({'success': False, 'error': str(e)}), 500


def _flag(username: str, name: str, default: str = 'false') -> bool:
    from trading_app.app.utils.user_env import UserEnvManager
    return (UserEnvManager.get_user_var(username, name, default) or '').strip().lower() in _TRUE


def _int_var(username: str, name: str):
    from trading_app.app.utils.user_env import UserEnvManager
    raw = UserEnvManager.get_user_var(username, name)
    try:
        return int(str(raw)) if raw not in (None, '') else None
    except (TypeError, ValueError):
        return None


def op_targets(username: str) -> list:
    """The brokers an order from this page would reach, in instance order.

    Two flags, both of which must be on: ``BROKER_N_ACTIVE`` (the account is in
    use at all) and ``BROKER_N_OP_ACTIVE`` (that account accepts orders from
    this page). Kept here so the pad can show exactly the list the dispatcher
    will build, rather than a hopeful guess at it.
    """
    from trading_app.app.routes.api import is_broker_active
    from trading_app.app.utils.user_env import UserEnvManager

    out = []
    for i in range(1, 21):
        b_type = (UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '') or '').strip().lower()
        if not b_type or not is_broker_active(username, i):
            continue
        if not _flag(username, f'BROKER_{i}_OP_ACTIVE'):
            continue
        out.append({
            'instance': i,
            'type': b_type,
            'name': (UserEnvManager.get_user_var(username, f'BROKER_{i}_NAME', '') or '').strip()
                    or b_type.title(),
            # What this broker would trade when the pad leaves Lots blank.
            'lots': _int_var(username, f'BROKER_{i}_OP_LOTS')
                    or _int_var(username, 'OP_ORDER_LOTS'),
        })
    return out


def op_order_lots(username: str, instance: int, symbol: str) -> int:
    """Lots one broker trades from this page.

    Mirrors the precedence _dispatch_order_to_brokers uses for strategy 'op',
    so a stop is sized like the entry it protects. If you change one, change
    the other — a mismatch is a stop that covers part of a position.
    """
    for var in (f'BROKER_{instance}_OP_LOTS', 'OP_ORDER_LOTS',
                f'BROKER_{instance}_LOT_SIZE'):
        lots = _int_var(username, var)
        if lots and lots > 0:
            return lots
    return 1


def _is_ours(order: dict) -> bool:
    return (order or {}).get('strategy') == OP_STRATEGY


def _our_orders(history: bool = False) -> list:
    from trading_app.app.utils.mine_order_store import MineOrderStore
    src = MineOrderStore.get_all_orders() if history else MineOrderStore.get_today_orders()
    return [o for o in src if _is_ours(o)]


# ── the contract: strike step, lot size, spot ────────────────────────────


def _modal_step(strikes, spot=None, window=21):
    """The strike difference actually traded, from the chain's own strikes.

    The modal gap between adjacent strikes, taken over the strikes nearest the
    money rather than the whole chain: an index chain is dense at the money and
    sparse in the wings (NIFTY runs 50 apart near spot and 100 apart far from
    it), so the whole-chain mode answers for the wings and moves the ± buttons
    onto strikes nobody trades. Without a spot to centre on, the middle of the
    chain is the best available stand-in for the money.
    """
    strikes = sorted({float(s) for s in strikes if s and float(s) > 0})
    if len(strikes) < 2:
        return None

    if spot:
        strikes.sort(key=lambda s: abs(s - float(spot)))
        near = sorted(strikes[:window])
    else:
        mid = len(strikes) // 2
        half = window // 2
        near = strikes[max(0, mid - half):mid + half + 1]
    if len(near) < 2:
        return None

    gaps = [round(b - a) for a, b in zip(near, near[1:]) if round(b - a) > 0]
    if not gaps:
        return None
    return max(set(gaps), key=gaps.count)


def _nearest_expiry_rows(rows):
    """The option rows of the nearest expiry that has not passed."""
    from datetime import date as _date, datetime as _datetime

    def as_date(value):
        if isinstance(value, _datetime):
            return value.date()
        if isinstance(value, _date):
            return value
        try:
            return _datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return None

    today = _date.today()
    dated = [(as_date(r.get('expiry')), r) for r in rows]
    live = sorted({d for d, _ in dated if d and d >= today})
    if not live:
        return [], None
    front = live[0]
    return [r for d, r in dated if d == front], front


def _chain_meta(symbol: str) -> dict:
    """{step, lot_size, expiry} read off the live option chain, TTL-cached.

    Returns the fallback step and no lot size when no provider can answer —
    the pad still works, and the lot size it does not print is one it does not
    have to guess at.
    """
    import time as _t

    cached = _chain_cache.get(symbol)
    if cached and (_t.time() - cached[0]) < _STEP_TTL_S:
        return cached[1]

    meta = {'step': OP_FALLBACK_STEP.get(symbol), 'lot_size': None,
            'expiry': None, 'source': 'fallback'}
    try:
        from trading_app.app.routes.api import get_data_provider

        provider = get_data_provider()
        if provider:
            exchange = OP_EXCHANGE.get(symbol, 'NFO')
            rows = [r for r in (provider.instruments(exchange) or [])
                    if (r.get('name') or '').strip().upper() == symbol
                    and r.get('instrument_type') in ('CE', 'PE')]
            front, expiry = _nearest_expiry_rows(rows)
            step = _modal_step([r.get('strike') for r in front], _spot(symbol))
            if step:
                lots = {int(r['lot_size']) for r in front
                        if r.get('lot_size') and int(r['lot_size']) > 0}
                meta = {'step': step,
                        # One expiry quotes one lot size; anything else means
                        # the filter caught two contracts and neither is safe
                        # to print as this one's size.
                        'lot_size': lots.pop() if len(lots) == 1 else None,
                        'expiry': expiry.isoformat() if expiry else None,
                        'source': 'chain'}
    except Exception as e:
        logger.warning(f"[OrderPlacement API] {symbol} chain step unavailable: {e}")

    _chain_cache[symbol] = (_t.time(), meta)
    return meta


def _spot(symbol: str):
    """The underlying's last price, or None. Never cached — it prices the ATM."""
    try:
        from trading_app.app.routes.api import get_data_provider
        from trading_app.service.strategy_signal_service import SYMBOL_CONFIG

        provider = get_data_provider()
        config = SYMBOL_CONFIG.get(symbol)
        if not provider or not config:
            return None
        # The index key differs per provider — 'NSE:NIFTY BANK' at Kite is
        # 'NSE:NIFTYBANK-INDEX' at Fyers, and get_instrument_key answers
        # 'NSE:BANKNIFTY' for both, which is not a tradable key anywhere.
        from trading_app.service.fyers_data_service import FyersDataServiceAdapter
        key = config['fyers_key'] if isinstance(provider, FyersDataServiceAdapter) \
            else config['kite_key']
        quote = provider.ltp([key]) or {}
        return float(quote.get(key, {}).get('last_price') or 0) or None
    except Exception as e:
        logger.warning(f"[OrderPlacement API] {symbol} spot unavailable: {e}")
        return None


@order_placement_bp.route('/contract', methods=['GET'])
@require_user_auth
def contract():
    """Everything the pad needs to build a contract for one underlying.

    One call rather than the page stitching together /api/symbol-metadata and
    /api/underlying-price: those answer for equities as well and take the
    strike step as the mode of the entire NFO dump, which is not this
    underlying's traded difference and is not answerable at all for SENSEX,
    whose options are on BFO.
    """
    symbol = (request.args.get('symbol') or '').upper().strip()
    if symbol not in OP_SYMBOLS:
        return jsonify({'success': False,
                        'error': f'symbol must be one of {", ".join(OP_SYMBOLS)}'}), 400
    try:
        meta = _chain_meta(symbol)
        spot = _spot(symbol)
        step = meta['step'] or OP_FALLBACK_STEP.get(symbol) or 50
        return jsonify({
            'success': True, 'symbol': symbol, 'spot': spot,
            'strike_step': step, 'lot_size': meta['lot_size'],
            'expiry': meta['expiry'], 'step_source': meta['source'],
            'atm': int(round(spot / step) * step) if spot else None,
        })
    except Exception as e:
        return _fail(e, 'contract')


# ── config ───────────────────────────────────────────────────────────────

@order_placement_bp.route('/config', methods=['GET'])
@require_user_auth
def config():
    """Which brokers this page can reach, and how each of them is sized."""
    try:
        from trading_app.app.order_placement.op_signal_engine import op_signal_lots

        user = _user()
        targets = op_targets(user)

        # Signal mode is sized and gated separately, and the pad says so before
        # anything is armed: a broker with no BROKER_N_OP_SIGNAL_LOTS, or one
        # whose type cannot hold a stop, takes no part in a signal even though
        # it takes every single order this page places.
        for t in targets:
            lots = op_signal_lots(user, t['instance'])
            can_stop = t['type'] in ('zerodha', 'kite', 'fyers')
            t['signal_lots'] = lots
            t['signal_entry_lots'] = (lots * 3) if lots else None
            t['signal_ready'] = bool(lots) and can_stop
            t['signal_blocked'] = (
                None if t['signal_ready']
                else (f"{t['type']} cannot hold a stop-loss order" if not can_stop
                      else f"BROKER_{t['instance']}_OP_SIGNAL_LOTS is not set"))

        return jsonify({
            'success': True,
            'enabled': bool(targets),
            'brokers': targets,
            'symbols': list(OP_SYMBOLS),
            'flag': 'BROKER_N_OP_ACTIVE',
            'signal_enabled': any(t['signal_ready'] for t in targets),
            'signal_flag': 'BROKER_N_OP_SIGNAL_LOTS',
        })
    except Exception as e:
        return _fail(e, 'config')


# ── orders ───────────────────────────────────────────────────────────────

@order_placement_bp.route('/orders', methods=['GET'])
@require_user_auth
def list_orders():
    """This page's own book for today: what is still resting, and what is done.

    ``sync=1`` reconciles the resting ones against the broker order books
    first, so the strip never offers edit or cancel on an order the broker has
    already filled. The sweep is the shared, TTL-throttled one — it costs
    nothing on a day with nothing resting.
    """
    from trading_app.app.utils.mine_order_store import MineOrderStore

    try:
        if request.args.get('sync') == '1':
            try:
                from trading_app.app.routes.api import _reconcile_open_orders
                _reconcile_open_orders(_user(), dict(session))
            except Exception as e:
                # An improvement on the listing, never a precondition for it.
                logger.warning(f"[OrderPlacement API] status sync skipped: {e}")

        orders = _our_orders()
        pending = [o for o in orders if o.get('status') in MineOrderStore.EDITABLE_STATUSES]
        done = [o for o in orders if o.get('status') not in MineOrderStore.EDITABLE_STATUSES]
        pending.sort(key=lambda o: o.get('created_at', 0), reverse=True)
        done.sort(key=lambda o: o.get('created_at', 0), reverse=True)
        return jsonify({'success': True, 'pending': pending, 'done': done[:25]})
    except Exception as e:
        return _fail(e, 'list_orders')


def stop_direction_error(action, trigger, last_price):
    """Refuse a stop that is already triggered. Returns a message, or None.

    A stop rests inactive until the premium TOUCHES its trigger and then goes to
    market. So a BUY stop only waits if its trigger is ABOVE the current
    premium, and a SELL stop only waits if it is BELOW. The wrong way round, the
    trigger is satisfied the moment the order reaches the exchange: it fires
    instantly, at market, for the full size — the exact thing not pressing
    MARKET was meant to avoid.

    The broker will not refuse it (a triggered stop is a legitimate order), so
    this check is the only thing standing between a typo and a market order.
    Same rule and same wording as the OI Profile panel's oipStopDirectionError.

    An unknown last price means no check: the quote feed can be empty on a fresh
    load or after a provider hiccup, and refusing to place because we cannot see
    a price would be worse than placing.
    """
    if not last_price or last_price <= 0:
        return None
    if action == 'BUY' and trigger <= last_price:
        return (f'A BUY stop must sit ABOVE the market — ₹{trigger} is at or below the '
                f'last price of ₹{last_price}, so it would trigger instantly. Use MARKET '
                f'to buy now, or raise the trigger.')
    if action == 'SELL' and trigger >= last_price:
        return (f'A SELL stop must sit BELOW the market — ₹{trigger} is at or above the '
                f'last price of ₹{last_price}, so it would trigger instantly. Use MARKET '
                f'to sell now, or lower the trigger.')
    return None


def option_ltp(symbol: str, strike: int, option_type: str):
    """The contract's last traded premium, or None if nothing can answer.

    None is a real answer here, not a failure: every caller treats "no quote"
    as "no check" rather than as a reason to refuse an order.
    """
    try:
        from trading_app.app.routes.api import _get_cached_strike_token, get_data_provider, get_kite
        from trading_app.service.fyers_data_service import FyersDataServiceAdapter
        from trading_app.service.kite_order_services import KiteService

        provider = get_data_provider()
        effective = provider or get_kite(instance=1)
        if not effective:
            return None
        is_fyers = isinstance(provider, FyersDataServiceAdapter)
        service = KiteService(kite_instance=effective)
        _token, opt_sym = _get_cached_strike_token(service, provider, is_fyers,
                                                   symbol, strike, option_type)
        if not opt_sym:
            return None
        key = opt_sym if is_fyers else f'NFO:{opt_sym}'
        quote = (provider if is_fyers else effective).ltp([key]) or {}
        return float(quote.get(key, {}).get('last_price') or 0) or None
    except Exception as e:
        logger.warning(f"[OrderPlacement API] {symbol} {strike}{option_type} LTP unavailable: {e}")
        return None


def _place_stop(user, targets, symbol, strike, option_type, action, trigger_price):
    """Place one SL-M leg at every OP-enabled broker and record it.

    Both directions are the same order and both are offered:

    * SELL — the exit stop, capping the loss on a position already held.
    * BUY  — a stop *entry*. A BUY LIMIT above the market fills at once, so a
      limit can only ever wait for a fall; waiting for a rise needs a trigger.

    The broker work is the same tested loop the OI Profile stop buttons use
    (``dispatch_stop_to_brokers``), handed this page's own gate and sizing —
    one path to a broker rather than a second copy of four broker SDKs.
    """
    from trading_app.app.routes.api import (dispatch_stop_to_brokers,
                                            resolve_standard_lot)
    from trading_app.app.utils.mine_order_store import MineOrderStore

    # Checked here and not only on the page: the page is one caller of this
    # route, and a stop on the wrong side of the market is a market order
    # wearing a stop's name.
    wrong_side = stop_direction_error(action, trigger_price,
                                      option_ltp(symbol, strike, option_type))
    if wrong_side:
        return jsonify({'success': False, 'error': wrong_side, 'wrong_side': True}), 400

    # A guessed quantity is worse than no stop at all: too small leaves the
    # position part-naked, too large flips it short the moment it triggers.
    standard_lot = resolve_standard_lot(symbol)
    if not standard_lot:
        return jsonify({'success': False,
                        'error': f'Could not resolve the lot size for {symbol} — the broker '
                                 f'instrument list is unavailable. Reconnect the data '
                                 f'provider and retry.'}), 400

    enabled = {t['instance'] for t in targets}
    try:
        results = dispatch_stop_to_brokers(
            symbol=symbol, strike=strike, option_type=option_type,
            trigger_price=trigger_price, action=action,
            username=user, session_data=dict(session),
            standard_lot=standard_lot,
            gate=lambda i, b_type: i in enabled,
            lots_for=lambda i: op_order_lots(user, i, symbol),
            log_tag='OrderPlacement stop',
        )
    except Exception as e:
        return _fail(e, 'place_stop')

    placed = [r for r in results if r.get('success')]
    overall = bool(placed)

    record = MineOrderStore.add_order({
        'mode': 'broker',
        'symbol': symbol,
        'strike': strike,
        'option_type': option_type,
        'action': action,
        'strategy': OP_STRATEGY,
        'order_type': 'SL-M',
        'type': 'SL-M',
        'instrument': 'BFO' if symbol == 'SENSEX' else 'NFO',
        # 'price' carries the trigger because every screen reads 'price'; the
        # edit path keys off the SL-M order type to send a new value as a
        # trigger rather than as a limit.
        'price': trigger_price,
        'trigger_price': trigger_price,
        'quantity': sum(int(r.get('quantity') or 0) for r in placed),
        'status': 'OPEN' if overall else 'REJECTED',
        'username': user,
        'source': 'orderplacement',
        'broker_order_ids': results,
    })

    logger.info(f"[OrderPlacement API] STOP {action} {symbol} {strike}{option_type} "
                f"trigger={trigger_price} → {len(placed)}/{len(results)} brokers")

    # Same shape the entry path returns, so the page reads one reply.
    summary = [{'broker': r.get('broker'), 'instance': r.get('instance'),
                'quantity': r.get('quantity'), 'result': r} for r in results]
    errors = [r.get('error') for r in results if not r.get('success') and r.get('error')]
    return jsonify({'success': overall,
                    'error': None if overall else (errors[0] if errors else 'Stop rejected'),
                    'brokers_targeted': len(results), 'summary': summary,
                    'order_id': record.get('id'), 'status': record['status'],
                    'quantity': record['quantity']}), (200 if overall else 400)


@order_placement_bp.route('/order', methods=['POST'])
@require_user_auth
def place_order():
    """Place one option order at every OP-enabled broker.

    Everything is validated here before a broker is touched: a strike that is
    not a positive whole number, a LIMIT without a price, or a lot count typed
    as "2x" all have to fail on this side rather than four times over at four
    brokers.
    """
    from trading_app.app.routes.api import _dispatch_order_to_brokers
    from trading_app.app.utils.mine_order_store import MineOrderStore

    payload = request.get_json(silent=True) or {}
    symbol = (payload.get('symbol') or '').upper().strip()
    option_type = (payload.get('option_type') or '').upper().strip()
    action = (payload.get('action') or '').upper().strip()
    order_type = (payload.get('order_type') or 'MARKET').upper().strip()
    # 'STOP' is what the pad's button says; 'SL-M' is what the store, the
    # Orders grid and the edit path already call it. One name reaches the
    # record, so the screens that read it need no case of their own.
    if order_type in ('STOP', 'SL', 'SLM', 'SL-M'):
        order_type = 'SL-M'

    if symbol not in OP_SYMBOLS:
        return jsonify({'success': False,
                        'error': f'symbol must be one of {", ".join(OP_SYMBOLS)}'}), 400
    if option_type not in ('CE', 'PE'):
        return jsonify({'success': False, 'error': 'option_type must be CE or PE'}), 400
    if action not in ('BUY', 'SELL'):
        return jsonify({'success': False, 'error': 'action must be BUY or SELL'}), 400
    if order_type not in ('MARKET', 'LIMIT', 'SL-M'):
        return jsonify({'success': False,
                        'error': 'order_type must be MARKET, LIMIT or STOP'}), 400

    try:
        strike = int(payload.get('strike') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'strike must be a whole number'}), 400
    if strike <= 0:
        return jsonify({'success': False, 'error': 'strike must be above zero'}), 400

    limit_price = None
    if order_type == 'LIMIT':
        try:
            limit_price = float(payload.get('limit_price') or 0)
        except (TypeError, ValueError):
            limit_price = 0.0
        if limit_price <= 0:
            return jsonify({'success': False,
                            'error': 'A LIMIT order needs a price above zero'}), 400

    trigger_price = None
    if order_type == 'SL-M':
        try:
            trigger_price = float(payload.get('trigger_price') or 0)
        except (TypeError, ValueError):
            trigger_price = 0.0
        if trigger_price <= 0:
            return jsonify({'success': False,
                            'error': 'A STOP order needs a trigger price above zero'}), 400

    # Size is deliberately not a parameter. One number would have to serve
    # every broker at once, and the accounts are not sized alike; the
    # dispatcher reads BROKER_N_OP_LOTS per broker, which is the only sizing
    # that can be. A payload carrying 'lots' is refused rather than ignored —
    # silently dropping a size is how an order goes out at the wrong one.
    if payload.get('lots') not in (None, ''):
        return jsonify({'success': False,
                        'error': 'This page does not size orders — each broker trades its '
                                 'own BROKER_N_OP_LOTS'}), 400

    user = _user()
    targets = op_targets(user)
    if not targets:
        return jsonify({'success': False,
                        'error': 'No broker is enabled for this page. Set '
                                 'BROKER_N_OP_ACTIVE=true in env/Mine.env'}), 400

    if order_type == 'SL-M':
        return _place_stop(user, targets, symbol, strike, option_type, action,
                           trigger_price)

    try:
        result = _dispatch_order_to_brokers(
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            action=action,
            strategy=OP_STRATEGY,
            username=user,
            session_data=dict(session),
            # None: every broker sizes itself from BROKER_N_OP_LOTS.
            quantity=None,
            limit_price=limit_price,
        )
    except Exception as e:
        return _fail(e, 'place_order')

    # Only legs a broker accepted count towards the position — a rejected
    # broker contributes no quantity and no fill price.
    placed = [b for b in (result.get('summary') or [])
              if (b.get('result') or {}).get('success')]
    total_qty = sum(int(b.get('quantity') or 0) for b in placed)
    fills = [float((b.get('result') or {}).get('price') or 0) for b in placed]
    fills = [p for p in fills if p > 0]
    avg_fill = round(sum(fills) / len(fills), 2) if fills else float(limit_price or 0)

    # A MARKET order any broker took is done. A LIMIT is only resting, so it is
    # OPEN — which is exactly what the pending strip on the page keys off.
    if not result.get('success'):
        status = 'REJECTED'
    elif order_type == 'LIMIT':
        status = 'OPEN'
    else:
        status = 'EXECUTED'

    record = MineOrderStore.add_order({
        'mode': 'broker',
        'symbol': symbol,
        'strike': strike,
        'option_type': option_type,
        'action': action,
        'strategy': OP_STRATEGY,
        'order_type': order_type,
        'type': order_type,
        'instrument': 'BFO' if symbol == 'SENSEX' else 'NFO',
        'price': float(limit_price or 0),
        'quantity': total_qty,
        'entry_price': avg_fill,
        'status': status,
        'username': user,
        'source': 'orderplacement',
        'executed_at': int(_time.time() * 1000) if status == 'EXECUTED' else None,
        'broker_order_ids': result.get('summary', []),
    })

    logger.info(f"[OrderPlacement API] {action} {symbol} {strike}{option_type} "
                f"{order_type} lots=per-broker → "
                f"{len(placed)}/{len(targets)} brokers, status={status}")

    return jsonify({**result, 'order_id': record.get('id'), 'status': status,
                    'quantity': total_qty}), (200 if result.get('success') else 400)


@order_placement_bp.route('/orders/<order_id>/price', methods=['PUT'])
@require_user_auth
def update_price(order_id: str):
    """Move a resting order's price at every broker it was placed to.

    The store is only updated when a broker accepted the new price — otherwise
    the strip would show a price nobody is working.
    """
    from trading_app.app.routes.api import _modify_order_at_brokers, _reconcile_open_orders
    from trading_app.app.utils.mine_order_store import MineOrderStore

    payload = request.get_json(silent=True) or {}
    try:
        new_price = float(payload.get('price') or 0)
    except (TypeError, ValueError):
        new_price = 0.0
    if new_price <= 0:
        return jsonify({'success': False, 'error': 'Enter a price above zero'}), 400

    order = MineOrderStore.get_order(order_id)
    if not _is_ours(order):
        # Not "not found" as a euphemism: an order from another screen really
        # is not this page's to move, and saying so is clearer than pretending
        # the record does not exist.
        return jsonify({'success': False,
                        'error': 'That order was not placed from this page'}), 404
    if order.get('status') not in MineOrderStore.EDITABLE_STATUSES:
        return jsonify({'success': False, 'gone': True, 'status': order.get('status'),
                        'error': f"Order is {order.get('status')} — nothing left to modify"}), 409

    try:
        if not order.get('broker_order_ids'):
            MineOrderStore.update_price(order_id, new_price)
            return jsonify({'success': True, 'price': new_price, 'summary': []})

        # On an SL-M the edited number is the stop's trigger, not a limit.
        # Sending it as a price would convert the stop into a LIMIT resting
        # there — silently removing the protection the order exists for.
        is_stop = str(order.get('order_type') or order.get('type') or '').upper().startswith('SL')
        result = _modify_order_at_brokers(
            order.get('broker_order_ids'), _user(), dict(session),
            price=None if is_stop else new_price,
            trigger_price=new_price if is_stop else None)
        if not result.get('success'):
            # A refused modify usually means the order filled while the strip
            # was still showing it — ask the order book, and correct the record
            # rather than leaving a button that can only keep failing.
            _reconcile_open_orders(_user(), dict(session), order_id=order_id, force=True)
            settled = MineOrderStore.get_order(order_id).get('status')
            if settled not in MineOrderStore.EDITABLE_STATUSES:
                return jsonify({'success': False, 'gone': True, 'status': settled,
                                'error': f'Already {settled.lower()} at the broker — '
                                         f'removed from the open list',
                                'summary': result.get('summary', [])}), 409
            return jsonify({'success': False, 'error': result.get('error'),
                            'summary': result.get('summary', [])}), 400

        MineOrderStore.update_price(order_id, new_price)
        if is_stop:
            MineOrderStore.update_order(order_id, {'trigger_price': new_price})
        return jsonify({'success': True, 'price': new_price, 'is_stop': is_stop,
                        'brokers_targeted': result.get('brokers_targeted'),
                        'summary': result.get('summary', [])})
    except Exception as e:
        return _fail(e, 'update_price')


@order_placement_bp.route('/orders/<order_id>', methods=['DELETE'])
@require_user_auth
def cancel_order(order_id: str):
    """Cancel one resting order at every broker it reached."""
    from trading_app.app.routes.api import _cancel_order_at_brokers, _reconcile_open_orders
    from trading_app.app.utils.mine_order_store import MineOrderStore

    order = MineOrderStore.get_order(order_id)
    if not _is_ours(order):
        return jsonify({'success': False,
                        'error': 'That order was not placed from this page'}), 404

    try:
        broker_result = None
        if order.get('status') in MineOrderStore.EDITABLE_STATUSES and order.get('broker_order_ids'):
            broker_result = _cancel_order_at_brokers(order.get('broker_order_ids'),
                                                     _user(), dict(session))
            if not broker_result.get('success'):
                _reconcile_open_orders(_user(), dict(session), order_id=order_id, force=True)
                settled = MineOrderStore.get_order(order_id).get('status')
                if settled not in MineOrderStore.EDITABLE_STATUSES:
                    return jsonify({'success': False, 'gone': True, 'status': settled,
                                    'error': f'Already {settled.lower()} at the broker — '
                                             f'removed from the open list',
                                    'summary': broker_result.get('summary', [])}), 409
                return jsonify({'success': False,
                                'error': broker_result.get('error') or 'Broker cancel failed',
                                'summary': broker_result.get('summary', [])}), 400

        MineOrderStore.cancel_order(order_id)
        return jsonify({'success': True, 'summary': (broker_result or {}).get('summary', [])})
    except Exception as e:
        return _fail(e, 'cancel_order')


@order_placement_bp.route('/exit-all', methods=['POST'])
@require_user_auth
def exit_all():
    """Close this page's book: cancel what is resting, flatten what is held.

    Scoped to this page and nothing else. The shared ``route_scoped_exit``
    reads the store for records carrying ``strategy='op'`` — which is only ever
    written here — cancels the ones still resting at their brokers, and then
    squares off the net position those records add up to, per broker instance
    and per contract.

    An OI Profile position, an algo's position, or one opened by hand at the
    terminal is untouched, even when it is the same strike on the same account:
    the quantity exited is what this page's own records account for, capped by
    what the broker actually shows open.
    """
    from trading_app.app.order_placement.op_signal_engine import stop_all_signals
    from trading_app.app.routes.api import route_scoped_exit

    try:
        result = route_scoped_exit(_user(), dict(session), OP_STRATEGY,
                                   log_tag='OrderPlacement exit')
        # The orders are gone; the engine has to be told, or it keeps managing
        # legs that are no longer there and re-places a stop over a position
        # that has just been squared off.
        result['signals_stopped'] = stop_all_signals(_user(), dict(session),
                                                     reason='Exit all')
        # Always 200: a partial exit is a real outcome, and the page reads the
        # per-broker summary either way rather than a status code.
        return jsonify(result)
    except Exception as e:
        return _fail(e, 'exit_all')


# ── signal mode ──────────────────────────────────────────────────────────
# The pad's second mode. Where /order places one order and walks away, a signal
# is a whole trade handed over at once: an entry stop, and a plan for what to
# do when it fills. The engine in app/order_placement owns everything after
# arming; these routes are the way in and the way out.
#
# What a signal places is still ordinary orders. Every leg is a MineOrderStore
# record carrying strategy='op' — plus a signal_id and a leg name — so the
# price box, the ✕, the reconciliation sweep and Exit all on this page all
# reach a signal's legs without knowing signals exist.


def _signal_plan_from_payload(payload: dict) -> dict:
    """A plan from pasted text, from typed fields, or from text the user edited.

    The text is the normal way in and the fields are what the review bar shows;
    when both arrive the fields win, because they are what was on screen when
    the button was pressed.
    """
    from trading_app.app.order_placement.signal_text import parse_signal

    plan, text = {}, (payload.get('text') or '').strip()
    if text:
        plan = parse_signal(text)
        if 'error' in plan:
            return plan
        plan['source_text'] = text

    for key in ('symbol', 'option_type', 'action'):
        if payload.get(key):
            plan[key] = str(payload[key]).upper().strip()
    if payload.get('strike') not in (None, ''):
        plan['strike'] = payload['strike']
    for key in ('entry', 'stop'):
        if payload.get(key) not in (None, ''):
            plan[key] = payload[key]
    if payload.get('targets'):
        plan['targets'] = payload['targets']
    if payload.get('expiry'):
        plan['expiry'] = payload['expiry']

    if not plan:
        return {'error': 'Paste the signal text, or fill the fields in'}

    try:
        plan['strike'] = int(plan.get('strike') or 0)
        plan['entry'] = float(plan.get('entry') or 0)
        plan['stop'] = float(plan.get('stop') or 0)
        plan['targets'] = [float(t) for t in (plan.get('targets') or [])]
    except (TypeError, ValueError):
        return {'error': 'The strike, entry, stop and targets must all be numbers'}

    plan.setdefault('action', 'BUY')
    return plan


def _describe_signal(plan: dict, user: str) -> dict:
    """What arming this plan would send, per broker, before anything is sent."""
    from trading_app.app.order_placement.op_signal_engine import op_signal_lots

    out = []
    for t in op_targets(user):
        lots = op_signal_lots(user, t['instance'])
        ready = bool(lots) and t['type'] in ('zerodha', 'kite', 'fyers')
        out.append({**t, 'signal_lots': lots,
                    'signal_entry_lots': (lots * 3) if lots else None,
                    'signal_ready': ready})
    return {'brokers': out,
            'targets_resting': plan.get('targets', [])[:2],
            # Said plainly because it is the one part of the plan that is not
            # protected by an order at the exchange.
            'target_3_watched': (plan.get('targets') or [None, None, None])[2]}


@order_placement_bp.route('/signal/parse', methods=['POST'])
@require_user_auth
def parse_signal_text():
    """Read a pasted tip and say what would happen — without placing anything.

    The validation runs here too, and its message comes back beside the plan
    rather than instead of it: the page shows what it read AND why it will not
    arm, which is the only way to see that a refusal is about the expiry and
    not about the strike.
    """
    from trading_app.app.order_placement.op_signal_engine import validate_plan

    try:
        payload = request.get_json(silent=True) or {}
        plan = _signal_plan_from_payload(payload)
        if 'error' in plan:
            return jsonify({'success': False, 'error': plan['error']}), 200

        user = _user()
        symbol = str(plan.get('symbol') or '').upper()
        meta = _chain_meta(symbol) if symbol in OP_SYMBOLS else {}
        ltp = (option_ltp(symbol, plan['strike'], plan['option_type'])
               if symbol in OP_SYMBOLS and plan.get('strike') else None)

        problem = validate_plan(user, plan, chain_meta=meta, ltp=ltp)
        return jsonify({
            'success': True,
            'plan': {**plan, 'expiry': str(plan['expiry']) if plan.get('expiry') else None},
            'chain': meta, 'ltp': ltp,
            'armable': problem is None,
            'error': problem,
            **_describe_signal(plan, user),
        })
    except Exception as e:
        return _fail(e, 'parse_signal_text')


@order_placement_bp.route('/signal', methods=['POST'])
@require_user_auth
def arm_signal_route():
    """Arm a signal: place the entry stop at every sized broker.

    Nothing but the entry goes out now. The stop and the targets are attached
    by the engine when — and only when — the entry actually fills, because
    until then there is no position for them to protect and a resting sell
    would be a naked short.
    """
    from trading_app.app.order_placement.op_signal_engine import arm_signal, validate_plan

    try:
        payload = request.get_json(silent=True) or {}
        plan = _signal_plan_from_payload(payload)
        if 'error' in plan:
            return jsonify({'success': False, 'error': plan['error']}), 400

        user = _user()
        symbol = str(plan.get('symbol') or '').upper()
        meta = _chain_meta(symbol) if symbol in OP_SYMBOLS else {}
        ltp = (option_ltp(symbol, plan['strike'], plan['option_type'])
               if symbol in OP_SYMBOLS and plan.get('strike') else None)

        # Checked here and not only on the page, for the same reason /order is:
        # the page is one caller of this route, and a ladder that is wrong is
        # wrong at four brokers at once.
        problem = validate_plan(user, plan, chain_meta=meta, ltp=ltp)
        if problem:
            return jsonify({'success': False, 'error': problem}), 400

        result = arm_signal(user, dict(session), plan)
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        return _fail(e, 'arm_signal')


@order_placement_bp.route('/signals', methods=['GET'])
@require_user_auth
def list_signals():
    """Today's signals, each with the leg records it placed.

    The legs are the same records the pending strip lists, handed back grouped
    by signal so the page can show a ladder as one thing without having to
    guess which orders belong together.
    """
    from trading_app.app.order_placement.op_signal_engine import (is_running,
                                                                  remember_session,
                                                                  signal_records)
    from trading_app.app.order_placement.op_signal_store import OpSignalStore

    try:
        # Kept so the engine's thread can reach Fyers and Kotak, which read
        # their tokens from the session. In memory only — never written to the
        # plan file.
        remember_session(_user(), dict(session))

        signals = sorted(OpSignalStore.get_today(),
                         key=lambda s: s.get('created_at', 0), reverse=True)
        for s in signals:
            s['legs'] = sorted(signal_records(s['id']),
                               key=lambda o: o.get('created_at', 0))
        return jsonify({'success': True, 'signals': signals,
                        'engine_running': is_running()})
    except Exception as e:
        return _fail(e, 'list_signals')


@order_placement_bp.route('/signals/<signal_id>/cancel', methods=['POST'])
@require_user_auth
def cancel_signal_route(signal_id: str):
    """Stand one signal down: cancel its resting legs, square off what it holds.

    Scoped to this signal's own records. Another signal on the same strike, and
    anything the single-order pad placed, is untouched — which is what makes
    this different from Exit all.
    """
    from trading_app.app.order_placement.op_signal_engine import cancel_signal
    from trading_app.app.order_placement.op_signal_store import OpSignalStore

    try:
        if not OpSignalStore.get(signal_id):
            return jsonify({'success': False, 'error': 'No such signal'}), 404
        result = cancel_signal(signal_id, _user(), dict(session))
        # Always 200: a partial stand-down is a real outcome, and the page
        # reads the summary either way.
        return jsonify(result)
    except Exception as e:
        return _fail(e, 'cancel_signal')
