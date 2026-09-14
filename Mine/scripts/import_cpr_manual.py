"""Import a hand-analysed CPR sheet into the Trend page's comparison grid.

The Trend page's "CPR Manual vs Chart" grid puts a manual, chart-by-eye
reading of each session (the "Bactest Nifty CPR.xlsx" workbook) next to
what the app computes from the same session's bars. The manual side is
this JSON:

    src/trading_app/Backtest/cpr_manual/<SYMBOL>.json

Re-run this whenever the workbook grows a few more days:

    python scripts/import_cpr_manual.py ~/Downloads/"Bactest Nifty CPR.xlsx"
    python scripts/import_cpr_manual.py sheet.xlsx --symbol BANKNIFTY --sheet 2026

Column order is the workbook's, by POSITION (its headers carry typos —
"Traget", "CPR Dirction" — so names are not relied on):

    Date | Price vs Daily CPR | Price vs Hourly CPR | CPR Type | CPR Direction
    | 1st 5min candle | Boxes | Trade Type | Entry | Target | SL | SL or Tar
    | P&L (formula, recomputed here) | Reason

A row is kept only when it carries an analysis (column B filled) — the
workbook pre-fills a Date and a P&L formula on every future row, and those
are not observations. One row per trade, so a day with three trades is
three rows sharing the same analysis columns, exactly as in the sheet.
"""

import argparse
import json
import os
import sys
from datetime import datetime

import openpyxl

OUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'src', 'trading_app',
                       'Backtest', 'cpr_manual')

# "Price Above" / "Price Below" -> "Above" / "Below"; the rest pass through.
_SIDE = {'price above': 'Above', 'price below': 'Below',
         'above': 'Above', 'below': 'Below'}


def _side(v):
    return _SIDE.get(str(v or '').strip().lower(), _clean(v))


def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def manual_pnl(trade, entry, target, sl, result):
    """The sheet's P&L formula, signed: a Target is +|target - entry|, an SL
    is -|sl - entry|; anything else (no trade, still open) is None."""
    if entry is None or target is None or not result:
        return None
    is_buy = target > entry
    if result == 'Target':
        return round(target - entry if is_buy else entry - target, 2)
    if result == 'SL' and sl is not None:
        return round(sl - entry if is_buy else entry - sl, 2)
    return None


def import_sheet(path, symbol, sheet=None):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        r = list(r) + [None] * (14 - len(r))
        d = r[0]
        if not isinstance(d, datetime) or not _clean(r[1]):
            continue
        entry, target, sl = _num(r[8]), _num(r[9]), _num(r[10])
        result = _clean(r[11])
        trade = (_clean(r[7]) or '').upper() or None
        rows.append({
            'date': d.strftime('%Y-%m-%d'),
            'price_vs_daily': _side(r[1]),
            'price_vs_hourly': _side(r[2]),
            'cpr_type': _clean(r[3]),
            'cpr_direction': _clean(r[4]),
            'first_candle': _clean(r[5]),
            'boxes': _clean(r[6]),
            'trade': trade,
            'entry': entry,
            'target': target,
            'sl': sl,
            'result': result,
            'pnl': manual_pnl(trade, entry, target, sl, result),
            'reason': _clean(r[13]),
        })
    return {
        'symbol': symbol,
        'source': f'{os.path.basename(path)} / sheet {ws.title}',
        'imported_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'rows': rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('xlsx')
    ap.add_argument('--symbol', default='NIFTY')
    ap.add_argument('--sheet', default=None, help='worksheet name (default: first)')
    args = ap.parse_args(argv)

    doc = import_sheet(args.xlsx, args.symbol.upper(), args.sheet)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f'{doc["symbol"]}.json')
    with open(out, 'w') as fh:
        json.dump(doc, fh, indent=2)
    print(f'wrote {os.path.relpath(out)} ({len(doc["rows"])} rows, '
          f'{len({r["date"] for r in doc["rows"]})} sessions)')


if __name__ == '__main__':
    sys.exit(main())
