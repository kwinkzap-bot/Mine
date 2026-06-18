import logging
import math
from datetime import date, timedelta, datetime
from typing import Optional, List, Dict, Any

import pandas as pd

_logger = logging.getLogger(__name__)

# Grid for optimisation
_SM_TOP_N_VALUES = [5, 8, 10, 12, 15, 20]           # 6 values
_SM_EXIT_RANKS   = [25, 35, 50, 75, 100]             # 5 values
_SM_REBAL_FREQS  = ['weekly', 'monthly', 'quarterly'] # 3 values

# Indices swept during optimisation.
# NIFTY SMALLCAP 500 is excluded — its NSE CSV URL returns 404, leaving only a 50-stock
# fallback list which produces misleading results.
_SM_OPT_INDICES = [
    'NIFTY 500',
    'NIFTY SMALLCAP 250',
    'NIFTY MIDSMALLCAP 400',
]


def _sm_opt_score(summary: dict) -> float:
    """Calmar ratio — rewards CAGR, penalises drawdown. Returns -999 when CAGR ≤ 0."""
    cagr = summary.get('cagr_pct', 0)
    mdd  = abs(summary.get('max_drawdown_pct', 0))
    if cagr <= 0:
        return -999.0
    return cagr / max(1.0, mdd)


class SwingMomentumEngine:
    def __init__(
        self,
        index_name: str,
        start_date: str,
        end_date: str,
        rebalance_freq: str = 'monthly',
        investment: float = 100_000.0,
        top_n: int = 10,
        exit_rank: int = 50,
    ):
        self.index_name     = index_name.upper().strip()
        self.start_date     = datetime.strptime(start_date, '%Y-%m-%d').date()
        self.end_date       = datetime.strptime(end_date,   '%Y-%m-%d').date()
        self.rebalance_freq = rebalance_freq
        self.investment     = float(investment)
        self.top_n          = int(top_n)
        self.exit_rank      = int(exit_rank)

    # ── data fetching ─────────────────────────────────────────────────────

    def _fetch_data(self):
        """Download price history and constituent list. Returns (close_df, syms, yf_syms)."""
        import yfinance as yf
        from trading_app.service.dynamic_constituents import DynamicConstituentsService

        syms    = DynamicConstituentsService.get_constituents(self.index_name)
        yf_syms = [f'{s}.NS' for s in syms]

        padded_start = self.start_date - timedelta(days=310)
        raw = yf.download(
            yf_syms,
            start=str(padded_start),
            end=str(self.end_date + timedelta(days=1)),
            interval='1d',
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if raw.empty:
            raise ValueError('No price data returned from yfinance')

        if len(yf_syms) == 1:
            close_df = raw[['Close']].copy()
            close_df.columns = yf_syms
        else:
            close_df = raw['Close'].copy()
        return close_df.dropna(how='all'), syms, yf_syms

    # ── ranking (vectorized) ──────────────────────────────────────────────

    def _rank(
        self,
        close_df: pd.DataFrame,
        syms: List[str],
        yf_syms: List[str],
        as_of: date,
    ) -> List[Dict]:
        """Rank stocks by avg(3M, 6M, 9M) momentum.

        Vectorized: fetches 4 price rows once per date (not per-stock),
        cutting per-call pandas overhead by ~stocks×4.
        """
        as_of_ts = pd.Timestamp(as_of)

        def _get_row(cutoff: pd.Timestamp):
            avail = close_df.index[close_df.index <= cutoff]
            return close_df.loc[avail[-1]] if len(avail) else None

        row_cur = _get_row(as_of_ts)
        if row_cur is None:
            return []
        row_91  = _get_row(as_of_ts - timedelta(days=91))
        row_182 = _get_row(as_of_ts - timedelta(days=182))
        row_273 = _get_row(as_of_ts - timedelta(days=273))

        rows: List[Dict] = []
        for sym, yf_sym in zip(syms, yf_syms):
            pv = row_cur.get(yf_sym)
            if pv is None or pd.isna(pv):
                continue
            price = float(pv)

            vals = []
            for base_row in (row_91, row_182, row_273):
                if base_row is not None:
                    bv = base_row.get(yf_sym)
                    if bv is not None and not pd.isna(bv) and float(bv) != 0:
                        vals.append((price - float(bv)) / float(bv) * 100)

            avg = sum(vals) / len(vals) if vals else None
            rows.append({'sym': sym, 'yf': yf_sym, 'price': price, 'avg': avg})

        rows.sort(key=lambda r: (r['avg'] is None, -(r['avg'] or 0)))
        for i, r in enumerate(rows):
            r['rank'] = i + 1
        return rows

    # ── ranking pre-computation (for optimiser) ───────────────────────────

    def _precompute_rankings(
        self,
        close_df: pd.DataFrame,
        syms: List[str],
        yf_syms: List[str],
        rdates: List[date],
    ) -> Dict[date, List[Dict]]:
        """Compute rankings for every rebalance date once and cache the result.
        The optimiser passes this dict to run() so rankings are never recomputed
        across the 30 top_n × exit_rank combos for the same index/freq."""
        return {rd: self._rank(close_df, syms, yf_syms, rd) for rd in rdates}

    # ── rebalance date generation ──────────────────────────────────────────

    def _rebalance_dates(self, td_sorted: List[date]) -> List[date]:
        result: List[date] = []
        freq = self.rebalance_freq

        if freq == 'weekly':
            cur = self.start_date
            while cur <= self.end_date:
                days_to_mon = (7 - cur.weekday()) % 7
                mon = cur + timedelta(days=days_to_mon)
                if mon > self.end_date:
                    break
                td = next((d for d in td_sorted if d >= mon), None)
                if td and td <= self.end_date and td not in result:
                    result.append(td)
                cur = mon + timedelta(days=7)

        elif freq == 'quarterly':
            for year in range(self.start_date.year, self.end_date.year + 1):
                for month in (1, 4, 7, 10):
                    ms = date(year, month, 1)
                    if ms < self.start_date or ms > self.end_date:
                        continue
                    td = next((d for d in td_sorted if d >= ms), None)
                    if td and td <= self.end_date and td not in result:
                        result.append(td)

        else:  # monthly
            for year in range(self.start_date.year, self.end_date.year + 1):
                for month in range(1, 13):
                    ms = date(year, month, 1)
                    if ms < self.start_date or ms > self.end_date:
                        continue
                    td = next((d for d in td_sorted if d >= ms), None)
                    if td and td <= self.end_date and td not in result:
                        result.append(td)

        return sorted(result)

    # ── main simulation ───────────────────────────────────────────────────

    def run(
        self,
        _close_df=None,
        _syms=None,
        _yf_syms=None,
        _rankings: Optional[Dict[date, List[Dict]]] = None,
    ) -> Dict[str, Any]:
        if _close_df is not None:
            close_df, syms, yf_syms = _close_df, _syms, _yf_syms
        else:
            close_df, syms, yf_syms = self._fetch_data()

        td_sorted = sorted(d.date() for d in close_df.index)
        rdates    = self._rebalance_dates(td_sorted)
        if not rdates:
            raise ValueError('No rebalance dates found in the given range')

        # portfolio: sym → {yf, qty, buy_price, buy_date}
        portfolio: Dict[str, Dict] = {}
        cash     = self.investment
        trades:  List[Dict] = []
        curve:   List[Dict] = []
        is_first = True

        for rd in rdates:
            # Use pre-computed rankings when available (optimiser path)
            if _rankings is not None and rd in _rankings:
                ranked = _rankings[rd]
            else:
                ranked = self._rank(close_df, syms, yf_syms, rd)
            rank_map = {r['sym']: r for r in ranked}

            # ── EXIT: held stocks that dropped below exit_rank ────────
            for sym in list(portfolio.keys()):
                info = rank_map.get(sym)
                rank = info['rank'] if info else len(ranked) + 1
                if rank > self.exit_rank:
                    hold  = portfolio.pop(sym)
                    row   = close_df.index[close_df.index <= pd.Timestamp(rd)]
                    price = float(close_df.loc[row[-1], hold['yf']]) if len(row) else hold['buy_price']
                    if pd.isna(price):
                        price = hold['buy_price']
                    pnl   = (price - hold['buy_price']) * hold['qty']
                    trades.append({
                        'date':         str(rd),
                        'symbol':       sym,
                        'action':       'SELL',
                        'qty':          hold['qty'],
                        'price':        round(price, 2),
                        'investment':   round(price * hold['qty'], 2),
                        'reason':       'ROTATION_OUT',
                        'rank':         rank,
                        'pnl':          round(pnl, 2),
                        'holding_days': (rd - hold['buy_date']).days,
                    })
                    cash += price * hold['qty']

            # ── ENTER: fill up to top_n with highest-ranked unowned ───
            owned      = set(portfolio.keys())
            candidates = [r for r in ranked if r['sym'] not in owned]
            for r in candidates:
                if len(portfolio) >= self.top_n:
                    break
                remaining = self.top_n - len(portfolio)
                per_stock = cash / remaining
                qty       = int(per_stock / r['price'])
                if qty <= 0:
                    continue
                cost = qty * r['price']
                trades.append({
                    'date':         str(rd),
                    'symbol':       r['sym'],
                    'action':       'BUY',
                    'qty':          qty,
                    'price':        round(r['price'], 2),
                    'investment':   round(cost, 2),
                    'reason':       'INITIAL' if is_first else 'ROTATION_IN',
                    'rank':         r['rank'],
                    'pnl':          None,
                    'holding_days': None,
                })
                cash -= cost
                portfolio[r['sym']] = {
                    'yf': r['yf'], 'qty': qty,
                    'buy_price': r['price'], 'buy_date': rd,
                }

            is_first = False

            # Record portfolio value at this rebalance date
            row = close_df.index[close_df.index <= pd.Timestamp(rd)]
            if len(row):
                price_row = close_df.loc[row[-1]]
                held_val = sum(
                    h['qty'] * (float(price_row.get(h['yf'], h['buy_price'])) if not pd.isna(price_row.get(h['yf'], float('nan'))) else h['buy_price'])
                    for h in portfolio.values()
                )
            else:
                held_val = sum(h['qty'] * h['buy_price'] for h in portfolio.values())
            curve.append({'date': str(rd), 'value': round(cash + held_val, 2)})

        # ── FINAL CLOSEOUT at end_date ────────────────────────────────
        end_row = close_df.index[close_df.index <= pd.Timestamp(self.end_date)]
        price_row_end = close_df.loc[end_row[-1]] if len(end_row) else None

        for sym, hold in list(portfolio.items()):
            if price_row_end is not None:
                pv = price_row_end.get(hold['yf'])
                price = float(pv) if pv is not None and not pd.isna(pv) else hold['buy_price']
            else:
                price = hold['buy_price']
            pnl = (price - hold['buy_price']) * hold['qty']
            trades.append({
                'date':         str(self.end_date),
                'symbol':       sym,
                'action':       'SELL',
                'qty':          hold['qty'],
                'price':        round(price, 2),
                'investment':   round(price * hold['qty'], 2),
                'reason':       'FINAL_EXIT',
                'rank':         None,
                'pnl':          round(pnl, 2),
                'holding_days': (self.end_date - hold['buy_date']).days,
            })
            cash += price * hold['qty']

        if not curve or curve[-1]['date'] != str(self.end_date):
            curve.append({'date': str(self.end_date), 'value': round(cash, 2)})

        # ── Summary ───────────────────────────────────────────────────
        sv   = self.investment
        ev   = round(cash, 2)
        tr   = (ev - sv) / sv * 100 if sv else 0
        days = (self.end_date - self.start_date).days
        cagr = ((ev / sv) ** (365.0 / days) - 1) * 100 if days > 0 and sv > 0 and ev > 0 else 0

        peak, mdd = sv, 0.0
        for pt in curve:
            v = pt['value']
            if v > peak:
                peak = v
            if peak > 0:
                dd = (v - peak) / peak * 100
                if dd < mdd:
                    mdd = dd

        sells    = [t for t in trades if t['action'] == 'SELL']
        avg_hold = sum(t.get('holding_days') or 0 for t in sells) / len(sells) if sells else 0

        return {
            'trades':          trades,
            'portfolio_curve': curve,
            'summary': {
                'start_value':       round(sv, 2),
                'end_value':         ev,
                'total_return_pct':  round(tr, 2),
                'cagr_pct':          round(cagr, 2),
                'max_drawdown_pct':  round(mdd, 2),
                'total_rotations':   len([t for t in sells if t['reason'] == 'ROTATION_OUT']),
                'avg_holding_days':  round(avg_hold, 1),
                'total_buy_trades':  len([t for t in trades if t['action'] == 'BUY']),
                'total_sell_trades': len(sells),
                'rebalance_count':   len(rdates),
            },
        }


def optimise_swing_momentum(
    index_name: str,
    start_date: str,
    end_date: str,
    investment: float = 100_000.0,
) -> List[Dict]:
    """Sweep 90 param combos for a single index. Downloads price data once."""
    base = SwingMomentumEngine(index_name, start_date, end_date)
    close_df, syms, yf_syms = base._fetch_data()

    results: List[Dict] = []
    for top_n in _SM_TOP_N_VALUES:
        for exit_rank in _SM_EXIT_RANKS:
            for freq in _SM_REBAL_FREQS:
                try:
                    eng = SwingMomentumEngine(
                        index_name, start_date, end_date,
                        rebalance_freq=freq,
                        investment=investment,
                        top_n=top_n,
                        exit_rank=exit_rank,
                    )
                    r = eng.run(_close_df=close_df, _syms=syms, _yf_syms=yf_syms)
                    s = r['summary']
                    results.append({
                        'top_n':            top_n,
                        'exit_rank':        exit_rank,
                        'rebalance_freq':   freq,
                        'total_return_pct': s['total_return_pct'],
                        'cagr_pct':         s['cagr_pct'],
                        'max_drawdown_pct': s['max_drawdown_pct'],
                        'total_rotations':  s['total_rotations'],
                        'rebalance_count':  s['rebalance_count'],
                        'score':            round(_sm_opt_score(s), 4),
                    })
                except Exception as exc:
                    _logger.debug(
                        'SM opt skip top_n=%s exit=%s freq=%s — %s',
                        top_n, exit_rank, freq, exc,
                    )

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def optimise_swing_momentum_full(
    start_date: str,
    end_date: str,
    investment: float = 100_000.0,
    rebalance_freq: str = 'monthly',
) -> List[Dict]:
    """Sweep 3 indices × 30 inner combos (top_n × exit_rank) = 90 backtests.

    Key optimisation: rankings are pre-computed ONCE per index (not 30× per combo),
    cutting the work from O(combos × dates × stocks) to O(dates × stocks + combos × dates × top_n).
    """
    results: List[Dict] = []

    for idx_name in _SM_OPT_INDICES:
        # ── Download price data once ──────────────────────────────────
        try:
            base = SwingMomentumEngine(idx_name, start_date, end_date,
                                       rebalance_freq=rebalance_freq)
            close_df, syms, yf_syms = base._fetch_data()
        except Exception as exc:
            _logger.warning('SM full-opt: skipping index %s — %s', idx_name, exc)
            continue

        # ── Pre-compute rankings for all rebalance dates (shared across combos) ──
        try:
            td_sorted = sorted(d.date() for d in close_df.index)
            rdates    = base._rebalance_dates(td_sorted)
            if not rdates:
                _logger.warning('SM full-opt: no rebalance dates for %s, skipping', idx_name)
                continue
            rankings = base._precompute_rankings(close_df, syms, yf_syms, rdates)
            _logger.info('SM full-opt: %s — %d stocks, %d rebalance dates',
                         idx_name, len(syms), len(rdates))
        except Exception as exc:
            _logger.warning('SM full-opt: ranking precompute failed for %s — %s', idx_name, exc)
            continue

        # ── Sweep top_n × exit_rank (30 combos, rankings already computed) ──
        for top_n in _SM_TOP_N_VALUES:
            for exit_rank in _SM_EXIT_RANKS:
                try:
                    eng = SwingMomentumEngine(
                        idx_name, start_date, end_date,
                        rebalance_freq=rebalance_freq,
                        investment=investment,
                        top_n=top_n,
                        exit_rank=exit_rank,
                    )
                    r     = eng.run(_close_df=close_df, _syms=syms,
                                    _yf_syms=yf_syms, _rankings=rankings)
                    s     = r['summary']
                    score = _sm_opt_score(s)
                    results.append({
                        'index':            idx_name,
                        'top_n':            top_n,
                        'exit_rank':        exit_rank,
                        'rebalance_freq':   rebalance_freq,
                        'total_return_pct': s['total_return_pct'],
                        'cagr_pct':         s['cagr_pct'],
                        'max_drawdown_pct': s['max_drawdown_pct'],
                        'total_rotations':  s['total_rotations'],
                        'rebalance_count':  s['rebalance_count'],
                        'score':            round(score, 4),
                    })
                except Exception as exc:
                    _logger.debug(
                        'SM full-opt skip %s top_n=%s exit=%s — %s',
                        idx_name, top_n, exit_rank, exc,
                    )

    results.sort(key=lambda x: x['score'], reverse=True)
    return results
