"""Fill the monthly-futures candle store for EMA Confluence Breakout.

The backtest prices its fills on the contract that was actually trading on each
date, and only ICICI (Breeze) will serve an expired contract. Breeze allows 100
requests a minute against a daily budget the live algos share, so the backtest
routes cap how many contracts one run may fetch (EMA_BT_FUT_BUDGET_*) and price
whatever is left over on the spot scale, reporting how many. This script is the
offline way to fill the store instead: it runs the same spot engine, asks for
the same contracts, and takes as long as it takes.

Run it once per symbol universe; a settled contract is cached forever, so
subsequent backtests read it off disk for free.

    PYTHONPATH=src ../.venv/bin/python scripts/prewarm_ema_futures.py
    PYTHONPATH=src ../.venv/bin/python scripts/prewarm_ema_futures.py NIFTY SBIN

Deliberately does NOT build the Flask app: create_app() starts the scheduler and
restarts the live algos (see CLAUDE.md). It talks to the providers directly.
"""
import argparse
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger('prewarm-ema-futures')

# Mirrors the backtest form's defaults (see _EMA_BT_WARMUP_DAYS / _LIVE_SIM_START).
WARMUP_DAYS = 1200
START_DATE = '2017-01-01'
STORE_HISTORY_DAYS = 4800


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('symbols', nargs='*', help='symbols to warm (default: the whole EMA universe)')
    ap.add_argument('--start', default=START_DATE, help=f'trade-from date (default {START_DATE})')
    ap.add_argument('--end', default=None, help='end date (default: today)')
    args = ap.parse_args()

    from trading_app.Backtest.ema_futures_pricing import apply_futures_pricing
    from trading_app.Backtest.ema_pullback_engine import EmaPullbackEngine
    from trading_app.Backtest.ema_symbol_universe import EMA_SYMBOL_DEFAULTS
    from trading_app.filters import futures_candle_store
    from trading_app.filters.candle_store import get_daily_history
    from trading_app.service.provider_logic import get_data_provider, get_icici_adapter

    symbols = [s.strip().upper() for s in args.symbols] or list(EMA_SYMBOL_DEFAULTS)
    end_dt = datetime.strptime(args.end, '%Y-%m-%d') if args.end else datetime.now()

    provider = get_data_provider(user='Mine', context='backtest')
    if provider is None:
        log.error('No data provider — cannot read the daily spot history the signals come from.')
        return 1
    adapter = get_icici_adapter(user='Mine')
    if adapter is None:
        log.error('No live ICICI session. Futures history is Breeze-only (every other '
                  'instrument master drops a contract the moment it expires), so there '
                  'is nothing to warm without it — log in to ICICI and re-run.')
        return 1

    # Same spot-token map the backtest route uses.
    fyers_indices = {'NIFTY': 'NSE:NIFTY50-INDEX', 'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
                     'SENSEX': 'BSE:SENSEX-INDEX'}
    bfo = {'SENSEX', 'BANKEX', 'SENSEX50'}

    totals = {'trades': 0, 'rolls': 0, 'contracts': 0, 'missing': 0, 'unpriced': 0}
    for i, symbol in enumerate(symbols, 1):
        cfg = EMA_SYMBOL_DEFAULTS.get(symbol, {'direction': 'both', 'target_pct': 5})
        token = fyers_indices.get(symbol, f'NSE:{symbol}-EQ')
        try:
            df = get_daily_history(provider, token, symbol, STORE_HISTORY_DAYS, end_dt)
        except Exception as e:
            log.warning('%s: daily history failed (%s) — skipped', symbol, e)
            continue
        if df is None or df.empty:
            log.warning('%s: no daily history — skipped', symbol)
            continue

        engine = EmaPullbackEngine(
            daily_df=df.rename_axis('date').reset_index(),
            enable_long=cfg['direction'] != 'short',
            enable_short=cfg['direction'] != 'long',
            target_pct=cfg['target_pct'],
            start_date=args.start,
        )
        trades, _ = engine.run()
        if not trades:
            log.info('[%d/%d] %s: no trades — no contracts needed', i, len(symbols), symbol)
            continue

        fetcher = futures_candle_store.Fetcher(
            adapter, symbol, 'BFO' if symbol in bfo else 'NFO',
            futures_candle_store.Budget(None))     # no cap — that is the point
        stats = apply_futures_pricing(trades, engine.daily_df, fetcher,
                                      lots=1, lot_size=1)
        totals['trades'] += len(trades)
        totals['rolls'] += stats['rolls']
        totals['contracts'] += stats['contracts_used']
        totals['missing'] += len(stats['missing_contracts'])
        totals['unpriced'] += stats['spot_priced_trades']
        log.info('[%d/%d] %s: %d trades, %d rolls, %d contracts (%d served nothing, '
                 '%d trades left on the spot scale)', i, len(symbols), symbol, len(trades),
                 stats['rolls'], stats['contracts_used'], len(stats['missing_contracts']),
                 stats['spot_priced_trades'])

    log.info('Done — %d symbols, %d trades, %d rolls, %d contracts touched '
             '(%d served nothing, %d trades left on the spot scale)',
             len(symbols), totals['trades'], totals['rolls'], totals['contracts'],
             totals['missing'], totals['unpriced'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
