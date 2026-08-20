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
