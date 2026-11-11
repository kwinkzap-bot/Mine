"""Backfill the Candle Breakout 30-second store from ICICI Breeze, now.

The app does this by itself (api._sc_backfill_30s): after 15:45 on weekdays and
at weekends, newest first, back to 2018-04-02. This runs the same walk in the
foreground — e.g. to have a year in place tonight without restarting the app.
It never builds the app (no create_app, no scheduler) and shares the app's
day-spend book (data/second_candles/backfill_spent.json) and store file lock.

    PYTHONPATH=src ../.venv/bin/python scripts/backfill_30s.py --since 2025-11-01
    PYTHONPATH=src ../.venv/bin/python scripts/backfill_30s.py --since 2025-11-01 --budget 4500

Refuses 09:00-15:45 IST on weekdays: Breeze's ~5,000/day is the live algos' then.
"""
import argparse
import logging
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='NIFTY')
    ap.add_argument('--since', help='stop once the store reaches this date (YYYY-MM-DD); '
                                    'default: as far as Breeze goes (2018-04-02)')
    ap.add_argument('--budget', type=int, help="Breeze requests for today in all (the book's "
                                               "spend counts against it); default: the app's own")
    ap.add_argument('--user', default='Mine')
    a = ap.parse_args()

    now = datetime.now(ZoneInfo('Asia/Kolkata'))
    if now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) < (15, 45):
        sys.exit('Market hours (09:00-15:45 IST): Breeze belongs to the live algos. Run after 15:45.')

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    from trading_app.app.routes import api
    from trading_app.Backtest import seconds_store
    token = api._SC_INDEX_TOKENS.get(a.symbol, f'NSE:{a.symbol}-EQ')
    floor = date.fromisoformat(a.since) if a.since else None
    print('before:', seconds_store.span(a.symbol), '· spent today:', api._sc_spent_today())
    result = api._sc_backfill_walk(a.symbol, token, a.user, floor=floor, wait=False,
                                   allowance=a.budget)
    print('result:', result, '· after:', seconds_store.span(a.symbol),
          '· spent today:', api._sc_spent_today())


if __name__ == '__main__':
    main()
