"""API routes for the Watchlist page.

Its own blueprint rather than another block in api.py, for the same reason
oi_crossover_api.py is: one self-contained feature behind one service, and
api.py is already past 14k lines.

Every route is scoped to the logged-in user — the username from the session is
the only key the service ever sees, so one user's tabs are not addressable
from another's session even by guessing an id.
"""

from flask import Blueprint, jsonify, request, session

from trading_app.app.utils.logger import logger
from trading_app.app.utils.user_auth import require_user_auth
from trading_app.service import watchlist_service as svc

watchlist_bp = Blueprint('watchlist', __name__)


def _user() -> str:
    return session.get('username') or ''


def _fail(e: Exception, what: str):
    logger.error(f"[Watchlist API] {what} failed: {e}", exc_info=True)
    return jsonify({'success': False, 'error': str(e)}), 500


# ── tabs ─────────────────────────────────────────────────────────────────

@watchlist_bp.route('/tabs', methods=['GET'])
@require_user_auth
def list_tabs():
    try:
        return jsonify({'success': True, 'tabs': svc.list_tabs(_user())})
    except Exception as e:
        return _fail(e, 'list_tabs')


@watchlist_bp.route('/tabs', methods=['POST'])
@require_user_auth
def create_tab():
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.create_tab(_user(), payload.get('name', ''))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        return _fail(e, 'create_tab')


@watchlist_bp.route('/tabs/<int:tab_id>', methods=['PUT'])
@require_user_auth
def rename_tab(tab_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.rename_tab(_user(), tab_id, payload.get('name', ''))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        return _fail(e, 'rename_tab')


@watchlist_bp.route('/tabs/<int:tab_id>', methods=['DELETE'])
@require_user_auth
def delete_tab(tab_id: int):
    try:
        result = svc.delete_tab(_user(), tab_id)
        return jsonify(result), (200 if result.get('success') else 404)
    except Exception as e:
        return _fail(e, 'delete_tab')


# ── items ────────────────────────────────────────────────────────────────

@watchlist_bp.route('/tabs/<int:tab_id>/items', methods=['POST'])
@require_user_auth
def add_item(tab_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.add_item(_user(), tab_id, payload.get('symbol', ''))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        return _fail(e, 'add_item')


@watchlist_bp.route('/items/<int:item_id>', methods=['DELETE'])
@require_user_auth
def remove_item(item_id: int):
    try:
        result = svc.remove_item(_user(), item_id)
        return jsonify(result), (200 if result.get('success') else 404)
    except Exception as e:
        return _fail(e, 'remove_item')


@watchlist_bp.route('/items/<int:item_id>/tab', methods=['PUT'])
@require_user_auth
def move_item(item_id: int):
    """Move one symbol to another of this user's tabs."""
    payload = request.get_json(silent=True) or {}
    try:
        target = int(payload.get('tab_id') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'tab_id must be a number'}), 400
    if not target:
        return jsonify({'success': False, 'error': 'tab_id is required'}), 400
    try:
        result = svc.move_item(_user(), item_id, target)
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        return _fail(e, 'move_item')


# ── data ─────────────────────────────────────────────────────────────────

@watchlist_bp.route('/rows', methods=['GET'])
@require_user_auth
def rows():
    """The grid for one tab: live price plus cached fundamentals.

    ``refresh=1`` forces the fundamentals cache to re-fetch. The plain call
    already re-prices every row live — this is for the case where a result
    has just been published and the P/E on screen is the pre-result one.
    """
    try:
        tab_id = int(request.args.get('tab_id', 0))
    except ValueError:
        return jsonify({'success': False, 'error': 'tab_id must be a number'}), 400
    if not tab_id:
        return jsonify({'success': False, 'error': 'tab_id is required'}), 400

    force = request.args.get('refresh') in ('1', 'true', 'yes')
    try:
        result = svc.rows(_user(), tab_id, refresh=force)
        return jsonify(result), (200 if result.get('success') else 404)
    except Exception as e:
        return _fail(e, 'rows')


@watchlist_bp.route('/search', methods=['GET'])
@require_user_auth
def search():
    """Type-ahead over the NSE cash master — equities and indices."""
    try:
        return jsonify({'success': True,
                        'results': svc.search(request.args.get('q', ''))})
    except Exception as e:
        return _fail(e, 'search')


# ── orders ───────────────────────────────────────────────────────────────
#
# Regular NSE cash orders, placed through the same
# `_sm_place_equity_order` the Swing Momentum go-live uses — one tested
# multi-broker equity path rather than a second one written here. Nothing on
# this page reaches a broker except through /order, and the UI only calls it
# from the ticket's confirm button.

@watchlist_bp.route('/brokers', methods=['GET'])
@require_user_auth
def brokers():
    """The configured brokers the order ticket can send to."""
    from trading_app.app.utils.user_env import UserEnvManager
    user = _user()
    out = []
    for i in range(1, 11):
        def env(field, default=''):
            return (UserEnvManager.get_user_var(user, f'BROKER_{i}_{field}', default) or '').strip()
        if env('ACTIVE', 'false').lower() != 'true':
            continue
        broker_type = env('TYPE').lower()
        if not broker_type:
            continue
        out.append({'instance': i, 'type': broker_type,
                    'name': env('NAME') or broker_type.title()})
    return jsonify({'success': True, 'brokers': out})


@watchlist_bp.route('/order', methods=['POST'])
@require_user_auth
def place_order():
    """Place one regular NSE cash order for a symbol on the watchlist."""
    from trading_app.app.routes.api import _sm_build_order_service, _sm_place_equity_order
    from trading_app.app.utils.user_env import UserEnvManager

    payload = request.get_json(silent=True) or {}
    symbol = (payload.get('symbol') or '').upper().strip()
    side = (payload.get('side') or '').upper().strip()
    order_type = (payload.get('order_type') or 'MARKET').upper().strip()
    product = (payload.get('product') or 'CNC').upper().strip()

    if not symbol:
        return jsonify({'success': False, 'error': 'symbol is required'}), 400
    if side not in ('BUY', 'SELL'):
        return jsonify({'success': False, 'error': 'side must be BUY or SELL'}), 400
    if order_type not in ('MARKET', 'LIMIT'):
        return jsonify({'success': False, 'error': 'order_type must be MARKET or LIMIT'}), 400
    if product not in ('CNC', 'MIS'):
        return jsonify({'success': False, 'error': 'product must be CNC or MIS'}), 400
    try:
        qty = int(payload.get('qty') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'qty must be a whole number'}), 400
    if qty < 1:
        return jsonify({'success': False, 'error': 'qty must be at least 1'}), 400

    limit_price = payload.get('limit_price')
    if order_type == 'LIMIT':
        try:
            limit_price = float(limit_price)
        except (TypeError, ValueError):
            limit_price = 0.0
        if limit_price <= 0:
            return jsonify({'success': False,
                            'error': 'A LIMIT order needs a price above zero'}), 400
    else:
        limit_price = None

    # An index is not a tradable instrument on the cash market — the ticket
    # never offers one, and this is the guard that makes that true rather
    # than merely conventional.
    resolved = svc._resolve(symbol)
    if not resolved:
        return jsonify({'success': False, 'error': f'Unknown symbol "{symbol}"'}), 400
    if resolved['kind'] != 'EQ':
        return jsonify({'success': False,
                        'error': f'{symbol} is an index — it cannot be bought on the '
                                 f'cash market'}), 400

    user = _user()
    try:
        instance = int(payload.get('broker') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'broker must be a number'}), 400
    if not instance:
        return jsonify({'success': False, 'error': 'Choose a broker'}), 400

    broker_type = (UserEnvManager.get_user_var(user, f'BROKER_{instance}_TYPE', '') or '').lower()
    active = (UserEnvManager.get_user_var(user, f'BROKER_{instance}_ACTIVE', 'false') or '').lower()
    if not broker_type or active != 'true':
        return jsonify({'success': False, 'error': 'That broker is not active'}), 400

    broker_name = (UserEnvManager.get_user_var(user, f'BROKER_{instance}_NAME', '')
                   or broker_type.title())
    try:
        service = _sm_build_order_service(user, instance, broker_type)
        if not service:
            return jsonify({'success': False,
                            'error': f'{broker_name} is not logged in'}), 400

        # The price the ticket was looking at, used only to price the
        # padded-LIMIT fallback when a broker blocks bare MARKET orders.
        ref_price = None
        try:
            ref_price = float(payload.get('ltp') or 0) or None
        except (TypeError, ValueError):
            ref_price = None

        order_id, error = _sm_place_equity_order(
            broker_type, service, symbol, qty, side,
            price=ref_price, product=product,
            order_type=order_type, limit_price=limit_price)
    except Exception as e:
        return _fail(e, 'place_order')

    if error or not order_id:
        logger.error(f"[Watchlist API] {symbol} {side} x{qty} via {broker_name} "
                     f"rejected: {error}")
        return jsonify({'success': False, 'error': str(error or 'Order rejected')}), 400

    logger.info(f"[Watchlist API] {symbol} {side} x{qty} {order_type} {product} via "
                f"{broker_name} — order_id={order_id}")
    return jsonify({'success': True, 'order_id': str(order_id), 'broker': broker_name,
                    'symbol': symbol, 'side': side, 'qty': qty,
                    'order_type': order_type, 'product': product})


@watchlist_bp.route('/history', methods=['GET'])
@require_user_auth
def history():
    """Daily closes and the derived P/E line behind the drilldown chart."""
    symbol = (request.args.get('symbol') or '').strip()
    if not symbol:
        return jsonify({'success': False, 'error': 'symbol is required'}), 400
    try:
        result = svc.history(symbol, request.args.get('range', '1y'))
        return jsonify(result), (200 if result.get('success') else 404)
    except Exception as e:
        return _fail(e, 'history')
