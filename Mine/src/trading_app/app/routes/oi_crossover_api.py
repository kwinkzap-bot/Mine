"""API routes for the OI Crossover scanner.

Its own blueprint rather than another block in api.py: the scanner is a
self-contained feature with one service behind it, and api.py is already
past 13k lines.
"""

import csv
import io
from datetime import datetime

from flask import Blueprint, Response, jsonify, request

from trading_app.app.extensions import csrf, limiter
from trading_app.app.utils.logger import logger

oi_crossover_bp = Blueprint('oi_crossover', __name__)


def _service():
    from trading_app.service.oi_crossover_service import OICrossoverService
    return OICrossoverService()


@oi_crossover_bp.route('/snapshot', methods=['GET'])
@limiter.exempt
def snapshot():
    """Table rows for a session. Defaults to today (the Live view)."""
    trade_date = request.args.get('date') or None
    try:
        return jsonify(_service().snapshot(trade_date))
    except Exception as e:
        logger.error(f"[OIX API] snapshot failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@oi_crossover_bp.route('/series', methods=['GET'])
@limiter.exempt
def series():
    """Both change-lines plus every cross for one symbol — row drilldown."""
    symbol = (request.args.get('symbol') or '').upper().strip()
    if not symbol:
        return jsonify({'success': False, 'error': 'symbol is required'}), 400
    try:
        return jsonify(_service().series(symbol, request.args.get('date') or None))
    except Exception as e:
        logger.error(f"[OIX API] series failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@oi_crossover_bp.route('/meta', methods=['GET'])
@limiter.exempt
def meta():
    """Sector options and the dates Historical mode can offer."""
    try:
        svc = _service()
        return jsonify({'success': True,
                        'sectors': svc.sector_options(),
                        'dates': svc.available_dates()})
    except Exception as e:
        logger.error(f"[OIX API] meta failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@oi_crossover_bp.route('/export', methods=['GET'])
@limiter.exempt
def export():
    """CSV of the current session's crossovers."""
    trade_date = request.args.get('date') or None
    try:
        data = _service().snapshot(trade_date)
    except Exception as e:
        logger.error(f"[OIX API] export failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

    if not data.get('success'):
        return jsonify(data), 500

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Symbol', 'Crossover', 'Call OI', 'Put OI', 'PCR',
                     'Call OI Chg', 'Put OI Chg', 'OI Chg %', 'Quality',
                     'Crossover Time', 'Cross Count'])
    for r in data.get('rows', []):
        writer.writerow([r['symbol'], r['direction'], r['ce_oi'], r['pe_oi'], r['pcr'],
                         r['ce_chg'], r['pe_chg'], r['oi_chg_pct'], r['quality'],
                         r['cross_time'][11:16], r['cross_count']])

    day = data.get('trade_date') or datetime.now().strftime('%Y-%m-%d')
    return Response(buf.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': f'attachment; filename=oi-crossover-{day}.csv'
    })


@oi_crossover_bp.route('/backfill', methods=['POST'])
@csrf.exempt
@limiter.exempt
def backfill():
    """Rebuild past sessions from oi_history so Historical mode isn't empty
    on a fresh install. Safe to re-run — days already covered are skipped."""
    days = int((request.get_json(silent=True) or {}).get('days', 30))
    try:
        result = _service().backfill_from_oi_history(days)
        return jsonify(result), (200 if result.get('success') else 500)
    except Exception as e:
        logger.error(f"[OIX API] backfill failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@oi_crossover_bp.route('/backfill-futures', methods=['POST'])
@csrf.exempt
@limiter.exempt
def backfill_futures():
    """Recover the futures price line for sessions recorded before futures
    capture. Only reaches sessions whose contract is still listed."""
    try:
        result = _service().backfill_futures_prices()
        return jsonify(result), (200 if result.get('success') else 500)
    except Exception as e:
        logger.error(f"[OIX API] futures backfill failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@oi_crossover_bp.route('/scan', methods=['POST'])
@csrf.exempt
@limiter.exempt
def scan():
    """Run a scan now. The scheduler owns the regular cadence — this is for
    seeding the table or debugging a symbol.

    Outside market hours this returns a skip rather than writing frozen rows.
    Pass ``{"force": true}`` to override the clock deliberately; the frozen
    feed check still applies, so a shut market cannot be forced into the
    series either way.
    """
    payload = request.get_json(silent=True) or {}
    symbols = payload.get('symbols') or None
    if symbols:
        symbols = [s.upper().strip() for s in symbols]
    try:
        result = _service().scan(symbols, force=bool(payload.get('force')))
        if result.get('skipped'):
            return jsonify(result), 200
        return jsonify(result), (200 if result.get('success') else 500)
    except Exception as e:
        logger.error(f"[OIX API] scan failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
