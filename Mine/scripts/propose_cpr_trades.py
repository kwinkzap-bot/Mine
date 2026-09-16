"""Fill the trade columns of the CPR sheet by rule, for sessions that have none.

The rule itself — trade the REJECTION, not the break, with every case
spelled out — lives in trading_app/service/cpr_trade_rule.py (`propose`);
the Trend page's Update button applies the same function. This script
writes its proposal into the workbook.

Rows are written only where H (Trade Type) is empty — or, with
--replace-rule-trades, also where column O says the trade came from this
script — so hand-entered trades are never touched.

    python scripts/propose_cpr_trades.py ~/Downloads/"Bactest Nifty CPR.xlsx" \\
        --sheet 2026 --from 2026-02-01 --to 2026-02-28 [--replace-rule-trades]
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta

import openpyxl
from openpyxl.styles import PatternFill

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.service import cpr_backtest_service as svc  # noqa: E402
from trading_app.service.cpr_trade_rule import RULE_TAG, propose  # noqa: E402

FILL = PatternFill('solid', fgColor='DDEBF7')   # light blue: trade written by rule


def bars_for(symbol: str, first: date, last: date):
    from trading_app.service.provider_logic import _get_fyers_adapter
    from trading_app.service import multichart_service as mc
    adapter = _get_fyers_adapter('Mine')
    if adapter is None:
        sys.exit('Fyers is not configured for user Mine (env/Mine.env)')
    fy = mc.resolve_symbol(symbol)
    daily = svc._daily_rows(adapter.historical_data(
        fy, (first - timedelta(days=120)).isoformat(), last.isoformat(), 'day', use_cache=False))
    intraday = svc._intraday_by_session(adapter.historical_data(
        fy, (first - timedelta(days=45)).isoformat(), last.isoformat(), '5minute', use_cache=False))
    return daily, intraday


def _clear_rule_trade(ws, rn):
    """Blank H-L and N so a re-run never leaves half of an older proposal behind;
    M goes back to the sheet's P&L formula if an EOD number replaced it."""
    for col in (8, 9, 10, 11, 12, 14, 16):
        ws.cell(rn, col).value = None
        ws.cell(rn, col).fill = PatternFill(fill_type=None)
    if not str(ws.cell(rn, 13).value or '').startswith('='):
        ws.cell(rn, 13).value = (f'=IF(OR(I{rn}="", J{rn}="", L{rn}=""), "", IF(L{rn}="Target", IF(J{rn}>I{rn}, J{rn}-I{rn}, I{rn}-J{rn}), '
                                 f'IF(AND(L{rn}="SL", K{rn}<>""), IF(J{rn}>I{rn}, K{rn}-I{rn}, I{rn}-K{rn}), "")))')


def _note(ws, rn, text):
    """Column O with every earlier rule note dropped, the chart-analysis note kept."""
    parts = [p for p in str(ws.cell(rn, 15).value or '').split(' | ') if p and RULE_TAG not in p]
    return ' | '.join(parts + [text])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default=None)
    ap.add_argument('--symbol', default='NIFTY')
    ap.add_argument('--from', dest='first', required=True)
    ap.add_argument('--to', dest='last', required=True)
    ap.add_argument('--dry-run', action='store_true', help='print, do not write')
    ap.add_argument('--replace-rule-trades', action='store_true',
                    help='also rewrite rows this script filled earlier (column O says so)')
    ap.add_argument('--replace-hand-trades', action='store_true',
                    help='rewrite hand-entered trades too; the hand trade is kept in column O')
    args = ap.parse_args(argv)
    first, last = date.fromisoformat(args.first), date.fromisoformat(args.last)

    wb = openpyxl.load_workbook(args.xlsx)
    ws = wb[args.sheet] if args.sheet else wb.worksheets[0]
    targets = {}                     # date -> row number of the first untraded row
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        d = row[0].value
        if not isinstance(d, datetime) or not (first <= d.date() <= last):
            continue
        by_rule = RULE_TAG in str(row[14].value or '')
        if row[7].value and not ((args.replace_rule_trades and by_rule) or (args.replace_hand_trades and not by_rule)):
            continue                 # H: hand-entered trade — leave it
        if row[7].value and not by_rule:
            # A hand trade is about to be replaced: keep it in the note.
            hand = f"hand trade was {row[7].value} {row[8].value:.0f} / T {row[9].value:.0f} / SL {row[10].value:.0f} -> {row[11].value}"
            if hand not in str(row[14].value or ''):
                row[14].value = ((row[14].value + ' | ') if row[14].value else '') + hand
        ds = d.date().isoformat()
        if ds in targets and row[7].value:
            # A second hand trade on the same day: the rule writes one trade
            # per session, so this row's trade columns are cleared (kept in O).
            for col in (8, 9, 10, 11, 12, 14):
                row[col - 1].value = None
            continue
        targets.setdefault(ds, row[0].row)
    if not targets:
        print('nothing to do: every session in range already has a trade')
        return
    if not ws.cell(1, 16).value:
        ws.cell(1, 16).value = 'Setup'

    daily, intraday = bars_for(args.symbol, first, last)
    res = svc.analyse([{'date': d} for d in sorted(targets)], daily, intraday)
    written = 0
    for r in res['rows']:
        c = r['chart']
        if not c:
            print(f"{r['date']}: {r.get('error')}")
            continue
        bars = intraday[r['date']]
        p, why, setup_i = propose(c, bars)
        rn = targets[r['date']]
        if not p:
            print(f"{r['date']}: {why} — left untraded")
            if not args.dry_run:
                _clear_rule_trade(ws, rn)
                ws.cell(rn, 15).value = _note(ws, rn, f"{RULE_TAG}: {why}")
            continue
        sim = svc.simulate_trade(bars[setup_i:], p['trade'], p['entry'], p['target'], p['sl'])
        result = sim['result']
        line = (f"{r['date']} {p['trade']} E {p['entry']:.0f} T {p['target']:.0f} SL {p['sl']:.0f} "
                f"-> {result} ({sim['pnl']}) {sim['entry_time'] or ''}{' -> ' + sim['exit_time'] if sim['exit_time'] else ''}")
        print(line)
        if args.dry_run:
            continue
        _clear_rule_trade(ws, rn)
        vals = {} if result == 'No fill' else {
            8: p['trade'], 9: p['entry'], 10: p['target'], 11: p['sl'], 12: result}
        if result == 'EOD':
            vals[13] = sim['pnl']                # no formula for a square-off: the number itself
        if vals:
            vals[14] = why                       # N: the reason, as the hand-written rows carry
            vals[16] = bars[setup_i]['time']     # P: the setup candle, so the grid's replay starts after it
        for col, v in vals.items():
            cell = ws.cell(rn, col)
            cell.value = v
            cell.fill = FILL
        rule = (f"{RULE_TAG}: {why}; chart replay {result}"
                + (f" {sim['entry_time']} -> {sim['exit_time']}" if sim['exit_time'] else '')
                + (f", squared off at {sim['exit_time']}, P&L {sim['pnl']:+.0f}" if result == 'EOD' and sim['pnl'] is not None else ''))
        ws.cell(rn, 15).value = _note(ws, rn, rule)
        written += bool(vals)
    if not args.dry_run:
        wb.save(args.xlsx)
        print(f'wrote {written} trades to {os.path.basename(args.xlsx)}')


if __name__ == '__main__':
    main()
