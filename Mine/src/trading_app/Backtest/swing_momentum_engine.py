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
        monthly_add: float = 0.0,
    ):
        self.index_name     = index_name.upper().strip()
        self.start_date     = datetime.strptime(start_date, '%Y-%m-%d').date()
        self.end_date       = datetime.strptime(end_date,   '%Y-%m-%d').date()
        self.rebalance_freq = rebalance_freq
        self.investment     = float(investment)
        self.top_n          = int(top_n)
        self.exit_rank      = int(exit_rank)
        self.monthly_add    = float(monthly_add)

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
            if price > 10_000:
                continue

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
        portfolio:    Dict[str, Dict] = {}
        cash          = self.investment
        cum_invested  = self.investment   # tracks total capital injected (initial + SIP adds)
        trades:  List[Dict] = []
        curve:   List[Dict] = []
        is_first = True

        for i, rd in enumerate(rdates):
            # Inject monthly SIP on every rebalance after the first
            if i > 0 and self.monthly_add > 0:
                cash         += self.monthly_add
                cum_invested += self.monthly_add
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
            curve.append({'date': str(rd), 'value': round(cash + held_val, 2), 'invested': round(cum_invested, 2)})

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
            curve.append({'date': str(self.end_date), 'value': round(cash, 2), 'invested': round(cum_invested, 2)})

        # ── Summary ───────────────────────────────────────────────────
        sv             = self.investment          # initial capital (used for peak/mdd baseline)
        total_invested = round(cum_invested, 2)   # initial + all SIP adds
        ev   = round(cash, 2)
        tr   = (ev - total_invested) / total_invested * 100 if total_invested else 0
        days = (self.end_date - self.start_date).days
        cagr = ((ev / total_invested) ** (365.0 / days) - 1) * 100 if days > 0 and total_invested > 0 and ev > 0 else 0

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
                'total_invested':    total_invested,
                'monthly_add':       self.monthly_add,
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


    # ── live portfolio state (for Algo → Swing Momentum tab) ─────────────────

    def live_state(self) -> Dict[str, Any]:
        """Return current portfolio state + next-rebalance preview without placing orders.

        Steps
        -----
        1. Fetch price history.
        2. Replay all past rebalance dates to build current holdings (no final closeout).
        3. Fetch today's closing price for each holding via the price DataFrame.
        4. Run today's momentum ranking → derive BUY/SELL candidates for next rebalance.
        5. Estimate next rebalance date.
        """
        import yfinance as yf

        today     = datetime.today().date()
        close_df, syms, yf_syms = self._fetch_data()
        td_sorted = sorted(d.date() for d in close_df.index)
        all_rdates = self._rebalance_dates(td_sorted)

        # Only replay rebalances that have already happened
        past_rdates = [rd for rd in all_rdates if rd <= today]

        portfolio: Dict[str, Dict] = {}   # sym → {yf, qty, buy_price, buy_date}
        cash      = self.investment
        all_trades: List[Dict] = []

        for rd in past_rdates:
            ranked   = self._rank(close_df, syms, yf_syms, rd)
            rank_map = {r['sym']: r for r in ranked}

            # EXIT: rotation out
            for sym in list(portfolio.keys()):
                info = rank_map.get(sym)
                rank = info['rank'] if info else len(ranked) + 1
                if rank > self.exit_rank:
                    hold  = portfolio.pop(sym)
                    row   = close_df.index[close_df.index <= pd.Timestamp(rd)]
                    price = float(close_df.loc[row[-1], hold['yf']]) if len(row) else hold['buy_price']
                    if pd.isna(price):
                        price = hold['buy_price']
                    pnl = (price - hold['buy_price']) * hold['qty']
                    all_trades.append({
                        'date': str(rd), 'symbol': sym, 'action': 'SELL',
                        'qty': hold['qty'], 'price': round(price, 2),
                        'reason': 'ROTATION_OUT', 'rank': rank,
                        'pnl': round(pnl, 2),
                    })
                    cash += price * hold['qty']

            # ENTER: fill to top_n
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
                all_trades.append({
                    'date': str(rd), 'symbol': r['sym'], 'action': 'BUY',
                    'qty': qty, 'price': round(r['price'], 2),
                    'reason': 'ROTATION_IN', 'rank': r['rank'],
                })
                cash -= cost
                portfolio[r['sym']] = {
                    'yf': r['yf'], 'qty': qty,
                    'buy_price': r['price'], 'buy_date': rd,
                }

        last_rebalance = str(past_rdates[-1]) if past_rdates else None

        # ── Fetch current prices for holdings ───────────────────────────────
        holdings: List[Dict] = []
        if portfolio:
            yf_syms_held = [h['yf'] for h in portfolio.values()]
            try:
                px = yf.download(
                    yf_syms_held, period='5d', interval='1d',
                    auto_adjust=True, progress=False, threads=True,
                )
                if not px.empty:
                    closes = px['Close'] if len(yf_syms_held) > 1 else px[['Close']].rename(columns={'Close': yf_syms_held[0]})
                else:
                    closes = None
            except Exception:
                closes = None

            for sym, h in portfolio.items():
                curr_price = h['buy_price']
                if closes is not None:
                    col = closes.get(h['yf'])
                    if col is not None:
                        col_clean = col.dropna()
                        if not col_clean.empty:
                            curr_price = round(float(col_clean.iloc[-1]), 2)

                buy_val  = round(h['buy_price'] * h['qty'], 2)
                curr_val = round(curr_price * h['qty'], 2)
                pnl_abs  = round(curr_val - buy_val, 2)
                pnl_pct  = round((curr_price - h['buy_price']) / h['buy_price'] * 100, 2) if h['buy_price'] else 0
                holdings.append({
                    'symbol':        sym,
                    'qty':           h['qty'],
                    'buy_date':      str(h['buy_date']),
                    'buy_price':     round(h['buy_price'], 2),
                    'current_price': curr_price,
                    'buy_value':     buy_val,
                    'current_value': curr_val,
                    'pnl_abs':       pnl_abs,
                    'pnl_pct':       pnl_pct,
                })

        # ── Today's rankings → next-rebalance preview ───────────────────────
        today_ranked = self._rank(close_df, syms, yf_syms, today)
        rank_map_today = {r['sym']: r for r in today_ranked}
        owned_now = set(portfolio.keys())

        # Stocks we hold that now rank > exit_rank → should SELL at next rebalance
        sell_preview: List[Dict] = []
        for sym, h in portfolio.items():
            info = rank_map_today.get(sym)
            rank = info['rank'] if info else len(today_ranked) + 1
            if rank > self.exit_rank:
                sell_preview.append({'symbol': sym, 'current_rank': rank, 'qty': h['qty']})

        # Slots freed by sells + any empty slots
        free_slots = len(sell_preview) + max(0, self.top_n - len(portfolio))
        sell_syms  = {s['symbol'] for s in sell_preview}

        # Top-ranked stocks NOT in portfolio (and not already being sold) → should BUY
        buy_preview: List[Dict] = []
        for r in today_ranked:
            if len(buy_preview) >= free_slots:
                break
            if r['sym'] not in owned_now or r['sym'] in sell_syms:
                if r['sym'] not in owned_now:
                    buy_preview.append({
                        'symbol':       r['sym'],
                        'current_rank': r['rank'],
                        'price':        round(r['price'], 2),
                    })

        # ── Estimate next rebalance date ────────────────────────────────────
        next_rebalance = None
        future_rdates  = [rd for rd in all_rdates if rd > today]
        if future_rdates:
            next_rebalance = str(future_rdates[0])
        elif past_rdates:
            # extrapolate one period forward from the last rebalance
            import calendar
            last_dt = past_rdates[-1]
            freq    = self.rebalance_freq
            if freq == 'weekly':
                next_rebalance = str(last_dt + timedelta(days=7))
            elif freq == 'monthly':
                m, y = last_dt.month + 1, last_dt.year
                if m > 12:
                    m, y = 1, y + 1
                d = min(last_dt.day, calendar.monthrange(y, m)[1])
                next_rebalance = str(last_dt.replace(year=y, month=m, day=d))
            else:
                m, y = last_dt.month + 3, last_dt.year
                if m > 12:
                    m, y = m - 12, y + 1
                d = min(last_dt.day, calendar.monthrange(y, m)[1])
                next_rebalance = str(last_dt.replace(year=y, month=m, day=d))

        # ── Portfolio-level stats ────────────────────────────────────────────
        curr_port_val  = sum(h['current_value'] for h in holdings)
        total_invested = sum(h['buy_value'] for h in holdings)
        unrealised_pnl = round(curr_port_val - total_invested, 2)
        unrealised_pct = round(unrealised_pnl / total_invested * 100, 2) if total_invested else 0

        return {
            'current_holdings':  sorted(holdings, key=lambda h: h['pnl_pct'], reverse=True),
            'holding_count':     len(holdings),
            'sell_preview':      sell_preview,
            'buy_preview':       buy_preview,
            'last_rebalance':    last_rebalance,
            'next_rebalance':    next_rebalance,
            'cash_remaining':    round(cash, 2),
            'current_port_val':  round(curr_port_val, 2),
            'total_invested':    round(total_invested, 2),
            'unrealised_pnl':    unrealised_pnl,
            'unrealised_pct':    unrealised_pct,
            'rebalance_needed':  len(sell_preview) > 0 or len(buy_preview) > 0,
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
