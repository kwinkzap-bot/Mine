"""API routes for the Trend page.

Its own blueprint rather than another block in api.py, like multichart_api.py:
one read behind one service, nothing that touches an order.
"""

from flask import Blueprint, jsonify, request

from trading_app.app.utils.logger import logger
from trading_app.app.utils.user_auth import require_user_auth
from trading_app.service import cpr_backtest_service as cpr_bt
from trading_app.service import cpr_option_service as cpr_opt
from trading_app.service import multichart_service as mc

trend_bp = Blueprint('trend', __name__)


@trend_bp.route('/cpr-backtest', methods=['GET'])
@require_user_auth
def cpr_backtest():
    """The manual CPR sheet for a symbol beside the chart's own reading of
    every session it covers. 404 when no sheet has been imported for the
    symbol, 401 when no Fyers-style data provider is logged in."""
    symbol = (request.args.get('symbol') or 'NIFTY').strip().upper()
    try:
        mc.resolve_symbol(symbol)
        return jsonify(cpr_bt.compare(symbol))
    except mc.BadRequest as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except cpr_bt.NoManualData as e:
        return jsonify({'success': False, 'error': str(e), 'no_manual': True}), 404
    except mc.ProviderUnavailable as e:
        return jsonify({'success': False, 'error': str(e), 'auth_required': True}), 401
    except Exception as e:
        logger.error(f"[Trend API] cpr-backtest {symbol} failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@trend_bp.route('/cpr-backtest/update', methods=['POST'])
@require_user_auth
def cpr_backtest_update():
    """The grid's Update button: append every complete session after the
    sheet's last date to the symbol's manual JSON, read from the chart the
    way scripts/add_cpr_sessions.py and propose_cpr_trades.py would have
    written it into the workbook. Idempotent — a second click adds nothing."""
    body = request.get_json(silent=True) or {}
    symbol = (body.get('symbol') or request.args.get('symbol') or 'NIFTY').strip().upper()
    try:
        mc.resolve_symbol(symbol)
        return jsonify(cpr_bt.extend(symbol))
    except mc.BadRequest as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except cpr_bt.NoManualData as e:
        return jsonify({'success': False, 'error': str(e), 'no_manual': True}), 404
    except mc.ProviderUnavailable as e:
        return jsonify({'success': False, 'error': str(e), 'auth_required': True}), 401
    except Exception as e:
        logger.error(f"[Trend API] cpr-backtest update {symbol} failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@trend_bp.route('/cpr-backtest/options', methods=['GET'])
@require_user_auth
def cpr_backtest_options():
    """The option leg behind every trade on the sheet — CE for a BUY, PE for
    a SELL, ATM or (`premium=150|200|250|300`) the strike whose premium at
    the entry minute was nearest that number — priced off the contract's own
    1-minute candles. A second call because it reads Breeze one contract at
    a time; the grid fills its Option columns in when it lands. 401 with
    `icici_required` when no Breeze session is logged in."""
    symbol = (request.args.get('symbol') or 'NIFTY').strip().upper()
    raw = (request.args.get('premium') or '').strip()
    try:
        premium = float(raw) if raw else None
        if premium is not None and premium not in cpr_opt.PREMIUM_CHOICES:
            return jsonify({'success': False,
                            'error': f'premium must be one of {list(cpr_opt.PREMIUM_CHOICES)}'}), 400
    except ValueError:
        return jsonify({'success': False, 'error': f'bad premium {raw!r}'}), 400
    try:
        mc.resolve_symbol(symbol)
        return jsonify(cpr_opt.options(symbol, premium))
    except mc.BadRequest as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except cpr_bt.NoManualData as e:
        return jsonify({'success': False, 'error': str(e), 'no_manual': True}), 404
    except cpr_opt.OptionDataUnavailable as e:
        return jsonify({'success': False, 'error': str(e), 'icici_required': True}), 401
    except mc.ProviderUnavailable as e:
        return jsonify({'success': False, 'error': str(e), 'auth_required': True}), 401
    except Exception as e:
        logger.error(f"[Trend API] cpr-backtest options {symbol} failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
