"""API routes for the Multichart page.

Its own blueprint rather than another block in api.py, like watchlist_api.py:
one self-contained feature behind one service. Three reads, nothing that
places or touches an order.
"""

from flask import Blueprint, jsonify, request

from trading_app.app.utils.logger import logger
from trading_app.app.utils.user_auth import require_user_auth
from trading_app.service import multichart_service as svc

multichart_bp = Blueprint('multichart', __name__)


def _fail(e: Exception, what: str):
    logger.error(f"[Multichart API] {what} failed: {e}", exc_info=True)
    return jsonify({'success': False, 'error': str(e)}), 500


def _run(what: str, fn):
    """Common status mapping: 400 caller error, 401 no broker, 500 otherwise."""
    try:
        return jsonify(fn())
    except svc.BadRequest as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except svc.ProviderUnavailable as e:
        return jsonify({'success': False, 'error': str(e), 'auth_required': True}), 401
    except Exception as e:
        return _fail(e, what)


@multichart_bp.route('/symbols', methods=['GET'])
@require_user_auth
def symbols():
    return _run('symbols', lambda: {'success': True, 'symbols': svc.symbols()})


@multichart_bp.route('/candles', methods=['GET'])
@require_user_auth
def candles():
    symbol = (request.args.get('symbol') or '').strip()
    interval = (request.args.get('interval') or '').strip()
    if not symbol or not interval:
        return jsonify({'success': False, 'error': 'symbol and interval are required'}), 400
    return _run('candles', lambda: svc.candles(symbol, interval))


@multichart_bp.route('/live', methods=['GET'])
@require_user_auth
def live():
    symbol = (request.args.get('symbol') or '').strip()
    if not symbol:
        return jsonify({'success': False, 'error': 'symbol is required'}), 400
    return _run('live', lambda: svc.live(symbol))
