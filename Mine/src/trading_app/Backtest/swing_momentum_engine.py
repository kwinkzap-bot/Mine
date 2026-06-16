import logging
import math
from datetime import date, timedelta, datetime
from typing import Optional, List, Dict, Any

import pandas as pd

_logger = logging.getLogger(__name__)

# Grid for optimisation (6 × 5 × 3 = 90 combos)
_SM_TOP_N_VALUES = [5, 8, 10, 12, 15, 20]
_SM_EXIT_RANKS   = [25, 35, 50, 75, 100]
_SM_REBAL_FREQS  = ['weekly', 'monthly', 'quarterly']


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

    # ── price helpers ─────────────────────────────────────────────────────

    def _lookup(self, close_df: pd.DataFrame, col: str, as_of: date) -> Optional[float]:
        available = close_df.index[close_df.index <= pd.Timestamp(as_of)]
        if not len(available):
            return None
        try:
            val = float(close_df.loc[available[-1], col])
            return None if math.isnan(val) else val
        except (TypeError, ValueError, KeyError):
            return None

    def _pct(self, close_df: pd.DataFrame, col: str, as_of: date, days_ago: int) -> Optional[float]:
        target = pd.Timestamp(as_of) - timedelta(days=days_ago)
        available = close_df.index[close_df.index <= target]
        if not len(available):
            return None
        base = self._lookup(close_df, col, available[-1].date())
        curr = self._lookup(close_df, col, as_of)
        if base is None or curr is None or base == 0:
            return None
        return (curr - base) / base * 100

    # ── ranking ───────────────────────────────────────────────────────────

    def _rank(
        self,
        close_df: pd.DataFrame,
        syms: List[str],
        yf_syms: List[str],
        as_of: date,
    ) -> List[Dict]:
        rows: List[Dict] = []
        for sym, yf in zip(syms, yf_syms):
            price = self._lookup(close_df, yf, as_of)
            if price is None:
                continue
            vals = [
                v for v in (
                    self._pct(close_df, yf, as_of, 91),
                    self._pct(close_df, yf, as_of, 182),
                    self._pct(close_df, yf, as_of, 273),
                )
                if v is not None
            ]
            avg = sum(vals) / len(vals) if vals else None
            rows.append({'sym': sym, 'yf': yf, 'price': price, 'avg': avg})

        # descending by avg; stocks with no avg go to the bottom
        rows.sort(key=lambda r: (r['avg'] is None, -(r['avg'] or 0)))
        for i, r in enumerate(rows):
            r['rank'] = i + 1
        return rows

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

    def run(self, _close_df=None, _syms=None, _yf_syms=None) -> Dict[str, Any]:
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
            ranked   = self._rank(close_df, syms, yf_syms, rd)
            rank_map = {r['sym']: r for r in ranked}

            # ── EXIT: held stocks that dropped below exit_rank ────────
            for sym in list(portfolio.keys()):
                info = rank_map.get(sym)
                rank = info['rank'] if info else len(ranked) + 1
                if rank > self.exit_rank:
                    hold  = portfolio.pop(sym)
                    price = self._lookup(close_df, hold['yf'], rd) or hold['buy_price']
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
            held_val = sum(
                h['qty'] * (self._lookup(close_df, h['yf'], rd) or h['buy_price'])
                for h in portfolio.values()
            )
            curve.append({'date': str(rd), 'value': round(cash + held_val, 2)})

        # ── FINAL CLOSEOUT at end_date ────────────────────────────────
        for sym, hold in list(portfolio.items()):
            price = self._lookup(close_df, hold['yf'], self.end_date) or hold['buy_price']
            pnl   = (price - hold['buy_price']) * hold['qty']
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
        sv  = self.investment
        ev  = round(cash, 2)
        tr  = (ev - sv) / sv * 100 if sv else 0
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

        sells     = [t for t in trades if t['action'] == 'SELL']
        avg_hold  = sum(t.get('holding_days') or 0 for t in sells) / len(sells) if sells else 0

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
    """Sweep 90 param combos. Downloads price data once and reuses across all combos."""
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
