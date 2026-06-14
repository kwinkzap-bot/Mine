"""
Railway Track Pattern (RTP) Backtest Engine
Mirrors Mine CPR – Railway Track Strategy Pine Script exactly.

Entry Modes:
  RTP(20 & 9) — candle touches EMA 20, closes above/below EMA 9 & 20
  RTP(50)     — candle touches EMA 50, closes above/below EMA 50

Usage:
    from src.trading_app.Backtest.rtp_backtest_engine import fetch_and_run
    from src.trading_app.service.fyers_data_service import FyersDataServiceAdapter

    adapter = FyersDataServiceAdapter(app_id, access_token)
    results = fetch_and_run(adapter, from_date='2025-01-01')
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class RTPBacktestEngine:
    """
    Pure-Python equivalent of the Railway Track Pine Script strategy.
    Feed it a DataFrame with columns: date/datetime, open, high, low, close, volume.
    Call run() to get trades + summary.
    """

    _AUTO_LEVELS = {
        1: (10.0, 20.0),
        2: (15.0, 30.0),
        3: (20.0, 40.0),
        5: (25.0, 50.0),
    }

    def __init__(
        self,
        df: pd.DataFrame,
        entry_mode: str = 'RTP(20 & 9)',
        interval_minutes: int = 1,
        slope_bars: int = 8,
        use_adx: bool = True,
        adx_len: int = 14,
        adx_thresh: float = 25.0,
        sl_points: Optional[float] = None,
        tgt_points: Optional[float] = None,
        trail_points: Optional[float] = None,
    ):
        self.df = df.copy()
        self.entry_mode = entry_mode
        self.interval_minutes = interval_minutes
        self.slope_bars = slope_bars
        self.use_adx = use_adx
        self.adx_len = adx_len
        self.adx_thresh = adx_thresh

        auto_sl, auto_tgt = self._AUTO_LEVELS.get(interval_minutes, (25.0, 50.0))
        self.sl_points    = sl_points    if sl_points    is not None else auto_sl
        self.tgt_points   = tgt_points   if tgt_points   is not None else auto_tgt
        self.trail_points = trail_points  # None = fixed SL, float = trail step in points

        self._prepare()

    # ── Indicators ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        # Pine Script ta.ema() uses alpha = 2/(period+1) — matches ewm(span=period)
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _rma(series: pd.Series, period: int) -> pd.Series:
        # Pine Script RMA (Wilder's smoothing) uses alpha = 1/period — used by ATR and DMI
        return series.ewm(alpha=1.0 / period, adjust=False).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        # Pine ta.atr() uses Wilder's RMA, not standard EWM
        hl  = df['high'] - df['low']
        hpc = (df['high'] - df['close'].shift(1)).abs()
        lpc = (df['low']  - df['close'].shift(1)).abs()
        tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, adjust=False).mean()

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14):
        # Pine ta.dmi() uses Wilder's RMA for TR, DM+, DM-, and DX smoothing
        high, low, close = df['high'], df['low'], df['close']
        hl  = high - low
        hpc = (high - close.shift(1)).abs()
        lpc = (low  - close.shift(1)).abs()
        tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)

        up = high.diff()
        dn = -low.diff()
        dm_p = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
        dm_m = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)

        alpha   = 1.0 / period
        atr_s   = tr.ewm(alpha=alpha, adjust=False).mean()
        di_plus  = 100 * dm_p.ewm(alpha=alpha, adjust=False).mean() / atr_s
        di_minus = 100 * dm_m.ewm(alpha=alpha, adjust=False).mean() / atr_s

        dx  = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10))
        adx = dx.ewm(alpha=alpha, adjust=False).mean()
        return di_plus, di_minus, adx

    # ── Preparation ────────────────────────────────────────────────────────────

    def _prepare(self):
        df = self.df
        df.columns = [c.lower() for c in df.columns]

        # Parse and sort datetime
        if 'date' in df.columns:
            df['datetime'] = pd.to_datetime(df['date'])
        else:
            df['datetime'] = pd.to_datetime(df.index)

        df = df.sort_values('datetime').drop_duplicates(subset=['datetime']).reset_index(drop=True)

        # Localize to IST
        if df['datetime'].dt.tz is None:
            df['datetime'] = df['datetime'].dt.tz_localize(
                'Asia/Kolkata', ambiguous='infer', nonexistent='shift_forward'
            )
        else:
            df['datetime'] = df['datetime'].dt.tz_convert('Asia/Kolkata')

        df['_hour'] = df['datetime'].dt.hour
        df['_min']  = df['datetime'].dt.minute

        # ── EMAs
        df['ema9']  = self._ema(df['close'], 9)
        df['ema20'] = self._ema(df['close'], 20)
        df['ema50'] = self._ema(df['close'], 50)

        # ── ATR for parallel check
        atr14 = self._atr(df, 14)

        # ── Slope direction
        sb = self.slope_bars
        df['ema20_up']   = (df['ema20'] > df['ema20'].shift(sb)).fillna(False)
        df['ema50_up']   = (df['ema50'] > df['ema50'].shift(sb)).fillna(False)
        df['ema20_down'] = (df['ema20'] < df['ema20'].shift(sb)).fillna(False)
        df['ema50_down'] = (df['ema50'] < df['ema50'].shift(sb)).fillna(False)

        # ── Parallel check (gap must not expand too fast)
        gap        = (df['ema20'] - df['ema50']).abs()
        gap_change = (gap - gap.shift(5)).abs()
        df['parallel'] = (gap_change <= atr14 * 0.5).fillna(False)

        # ── ADX trend filter
        _, _, adx_vals = self._adx(df, self.adx_len)
        df['adx'] = adx_vals
        if self.use_adx:
            df['trending'] = (df['adx'] >= self.adx_thresh).fillna(False)
        else:
            df['trending'] = pd.Series(True, index=df.index)

        # ── Railway track
        if self.entry_mode == 'RTP(50)':
            df['rway_up'] = df['ema20_up']   & df['ema50_up']   & df['trending']
            df['rway_dn'] = df['ema20_down'] & df['ema50_down'] & df['trending']
        else:
            df['rway_up'] = df['ema20_up']   & df['ema50_up']   & df['parallel'] & df['trending']
            df['rway_dn'] = df['ema20_down'] & df['ema50_down'] & df['parallel'] & df['trending']

        # ── EMA stack
        if self.entry_mode == 'RTP(50)':
            df['bull_stack'] = df['ema50'] < df['ema20']
            df['bear_stack'] = df['ema50'] > df['ema20']
        else:
            df['bull_stack'] = (df['ema50'] < df['ema20']) & (df['ema20'] < df['ema9'])
            df['bear_stack'] = (df['ema50'] > df['ema20']) & (df['ema20'] > df['ema9'])

        # ── Session filter (9:20 AM – 3:28 PM IST)
        h, m = df['_hour'], df['_min']
        after_open    = (h > 9) | ((h == 9)  & (m >= 20))
        before_cutoff = (h < 15) | ((h == 15) & (m <= 28))
        df['session_ok'] = after_open & before_cutoff

        # ── Candlestick patterns
        body       = (df['close'] - df['open']).abs()
        rng        = df['high'] - df['low']
        upper_wick = df['high'] - df[['close', 'open']].max(axis=1)
        lower_wick = df[['close', 'open']].min(axis=1) - df['low']

        # Close quality: where the close sits within the candle range (0=bottom, 1=top)
        close_pos = (df['close'] - df['low']) / (rng + 0.001)

        # Generic buy/sell candle: body >= 50% of range AND close in strong position
        big_body      = body >= rng * 0.5
        buy_cls_ok    = close_pos >= 0.65   # close in top 35% of range
        sell_cls_ok   = close_pos <= 0.35   # close in bottom 35% of range

        is_hammer    = (rng > 0) & (body > 0) & (lower_wick >= 2.0 * body) & (upper_wick <= body)
        is_bull_eng  = (df['close'].shift(1) < df['open'].shift(1)) & (df['close'] > df['open']) & \
                       (df['open'] <= df['close'].shift(1)) & (df['close'] >= df['open'].shift(1))
        is_buy_cdl   = (df['close'] > df['open']) & big_body & buy_cls_ok

        is_shoot     = (rng > 0) & (body > 0) & (upper_wick >= 2.0 * body) & (lower_wick <= body)
        is_bear_eng  = (df['close'].shift(1) > df['open'].shift(1)) & (df['close'] < df['open']) & \
                       (df['open'] >= df['close'].shift(1)) & (df['close'] <= df['open'].shift(1))
        is_sell_cdl  = (df['close'] < df['open']) & big_body & sell_cls_ok

        is_doji = (rng > 0) & (body <= rng * 0.1)

        if self.entry_mode == 'RTP(50)':
            df['buy_pat']  = ((df['close'] > df['open']) | is_doji).fillna(False)
            df['sell_pat'] = ((df['close'] < df['open']) | is_doji).fillna(False)
        else:
            df['buy_pat']  = ((is_doji & df['rway_up']) | \
                             ((df['close'] > df['open']) & (is_hammer | is_bull_eng | is_buy_cdl))).fillna(False)
            df['sell_pat'] = ((is_doji & df['rway_dn']) | \
                             ((df['close'] < df['open']) & (is_shoot | is_bear_eng | is_sell_cdl))).fillna(False)

        # ── Touch conditions
        if self.entry_mode == 'RTP(50)':
            df['buy_touch']  = ((df['low'] <= df['ema50']) & (df['close'] > df['ema50'])).fillna(False)
            df['sell_touch'] = ((df['high'] >= df['ema50']) & (df['close'] < df['ema50'])).fillna(False)
        else:
            df['buy_touch']  = ((df['low'] <= df['ema20']) & \
                               (df['close'] > df['ema9']) & (df['close'] > df['ema20'])).fillna(False)
            df['sell_touch'] = ((df['high'] >= df['ema20']) & \
                               (df['close'] < df['ema9']) & (df['close'] < df['ema20'])).fillna(False)

        self.df = df.reset_index(drop=True)

    # ── Backtest loop ───────────────────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        df = self.df
        n  = len(df)
        trades: List[Dict] = []

        sell_needs_reset = False
        buy_needs_reset  = False

        i = 200  # Skip warmup: EMA50 needs ~200 bars to converge on 1-min data
        while i < n:
            row = df.iloc[i]

            # Reset candle check (mirrors Pine var bool logic)
            if sell_needs_reset:
                if self.entry_mode == 'RTP(50)':
                    if row['high'] < row['ema50']:
                        sell_needs_reset = False
                else:
                    if row['high'] < min(row['ema9'], row['ema20']):
                        sell_needs_reset = False

            if buy_needs_reset:
                if self.entry_mode == 'RTP(50)':
                    if row['low'] > row['ema50']:
                        buy_needs_reset = False
                else:
                    if row['low'] > max(row['ema9'], row['ema20']):
                        buy_needs_reset = False

            # Signal check
            buy_signal = bool(
                row['session_ok'] and row['rway_up'] and row['bull_stack'] and
                row['buy_touch']  and row['buy_pat'] and not buy_needs_reset
            )
            sell_signal = bool(
                row['session_ok'] and row['rway_dn'] and row['bear_stack'] and
                row['sell_touch'] and row['sell_pat'] and not sell_needs_reset
            )

            if sell_signal:
                sell_needs_reset = True
            if buy_signal:
                buy_needs_reset = True

            if not (buy_signal or sell_signal):
                i += 1
                continue

            # Entry fills at next bar open (process_orders_on_close=false)
            if i + 1 >= n:
                i += 1
                continue

            next_bar    = df.iloc[i + 1]
            entry_price = next_bar['open']
            entry_time  = next_bar['datetime']
            direction   = 'BUY' if buy_signal else 'SELL'

            # Scan forward for SL / Target
            exit_price  = None
            exit_time   = None
            exit_reason = None
            exit_idx    = i + 1

            # Trailing SL state
            trail        = self.trail_points
            best_price   = entry_price          # peak high (BUY) or trough low (SELL)
            current_sl   = (entry_price - self.sl_points) if direction == 'BUY' \
                           else (entry_price + self.sl_points)

            for j in range(i + 1, n):
                c = df.iloc[j]

                # Force-close at 3:28 PM or on next day (no overnight holds)
                eod_cutoff = (c['_hour'] == 15 and c['_min'] >= 28) or c['_hour'] > 15
                if c['datetime'].date() != entry_time.date() or eod_cutoff:
                    exit_price  = c['close']
                    exit_time   = c['datetime']
                    exit_reason = 'EOD'
                    exit_idx    = j
                    break

                if direction == 'BUY':
                    # Update trailing SL
                    if trail and c['high'] > best_price:
                        best_price = c['high']
                        steps = int((best_price - entry_price) / trail)
                        new_sl = (entry_price - self.sl_points) + steps * trail
                        current_sl = max(current_sl, new_sl)

                    if c['low'] <= current_sl:
                        exit_price  = current_sl
                        exit_time   = c['datetime']
                        exit_reason = 'TRAIL_SL' if trail else 'SL'
                        exit_idx    = j
                        break
                    if c['high'] >= entry_price + self.tgt_points:
                        exit_price  = entry_price + self.tgt_points
                        exit_time   = c['datetime']
                        exit_reason = 'TARGET'
                        exit_idx    = j
                        break
                else:
                    # Update trailing SL
                    if trail and c['low'] < best_price:
                        best_price = c['low']
                        steps = int((entry_price - best_price) / trail)
                        new_sl = (entry_price + self.sl_points) - steps * trail
                        current_sl = min(current_sl, new_sl)

                    if c['high'] >= current_sl:
                        exit_price  = current_sl
                        exit_time   = c['datetime']
                        exit_reason = 'TRAIL_SL' if trail else 'SL'
                        exit_idx    = j
                        break
                    if c['low'] <= entry_price - self.tgt_points:
                        exit_price  = entry_price - self.tgt_points
                        exit_time   = c['datetime']
                        exit_reason = 'TARGET'
                        exit_idx    = j
                        break

            if exit_price is None:
                last = df.iloc[-1]
                exit_price  = last['close']
                exit_time   = last['datetime']
                exit_reason = 'EOD'
                exit_idx    = n - 1

            pnl = (exit_price - entry_price) if direction == 'BUY' else (entry_price - exit_price)

            trades.append({
                'entry_time':  entry_time,
                'entry_price': round(entry_price, 2),
                'direction':   direction,
                'exit_time':   exit_time,
                'exit_price':  round(exit_price, 2),
                'exit_reason': exit_reason,
                'pnl':         round(pnl, 2),
            })

            # Jump to bar after exit so trades don't overlap
            i = exit_idx + 1

        return self._summarise(trades)

    # ── Summary ─────────────────────────────────────────────────────────────────

    def _summarise(self, trades: List[Dict]) -> Dict[str, Any]:
        if not trades:
            print("No trades generated.")
            return {
                'total_trades': 0, 'entry_mode': self.entry_mode,
                'sl_points': self.sl_points, 'tgt_points': self.tgt_points,
                'trades': [],
            }

        df_t = pd.DataFrame(trades)
        total  = len(df_t)
        wins   = int((df_t['pnl'] > 0).sum())
        losses = int((df_t['pnl'] < 0).sum())

        gross_profit = df_t[df_t['pnl'] > 0]['pnl'].sum()
        gross_loss   = df_t[df_t['pnl'] < 0]['pnl'].abs().sum()
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

        cumulative  = df_t['pnl'].cumsum()
        peak        = cumulative.cummax()
        drawdown    = cumulative - peak
        max_dd      = round(drawdown.min(), 2)

        # Max drawdown date range: peak → trough
        max_dd_start = max_dd_end = None
        if max_dd < 0:
            dd_end_pos   = int(drawdown.values.argmin())
            # last time cumulative equalled its max before the trough
            dd_start_pos = int(peak.iloc[:dd_end_pos + 1].values.argmax())
            max_dd_start = df_t.iloc[dd_start_pos]['exit_time']
            max_dd_end   = df_t.iloc[dd_end_pos]['exit_time']

        return {
            'total_trades':  total,
            'wins':          wins,
            'losses':        losses,
            'win_rate':      round(wins / total * 100, 1),
            'net_pnl':       round(df_t['pnl'].sum(), 2),
            'avg_win':       round(df_t[df_t['pnl'] > 0]['pnl'].mean(), 2) if wins   > 0 else 0.0,
            'avg_loss':      round(df_t[df_t['pnl'] < 0]['pnl'].mean(), 2) if losses > 0 else 0.0,
            'profit_factor': pf,
            'max_drawdown':  max_dd,
            'max_dd_start':  max_dd_start,
            'max_dd_end':    max_dd_end,
            'target_hits':   int((df_t['exit_reason'] == 'TARGET').sum()),
            'sl_hits':       int((df_t['exit_reason'] == 'SL').sum()),
            'trail_sl_hits': int((df_t['exit_reason'] == 'TRAIL_SL').sum()),
            'eod_exits':     int((df_t['exit_reason'] == 'EOD').sum()),
            'sl_points':     self.sl_points,
            'tgt_points':    self.tgt_points,
            'trail_points':  self.trail_points,
            'entry_mode':    self.entry_mode,
            'trades':        trades,
        }


# ── Parameter Optimiser ─────────────────────────────────────────────────────────

import math as _math

# Grid searched during optimisation
_ENTRY_MODES    = ['RTP(20 & 9)', 'RTP(50)']
_SL_TGT_PAIRS   = [
    (10, 20), (10, 30),
    (15, 30), (15, 45),
    (20, 40), (20, 60),
    (25, 50), (25, 75),
    (30, 60), (30, 90),
]
_ADX_THRESHOLDS = [20, 25, 30]
_USE_ADX_FLAGS  = [True, False]

_INTERVAL_MAP = {
    'minute': 1, '2minute': 2, '3minute': 3,
    '5minute': 5, '10minute': 10, '15minute': 15,
    '30minute': 30, '60minute': 60,
}


def _opt_score(r: Dict[str, Any]) -> float:
    """Score = net_pnl × sqrt(profit_factor) — rewards returns quality + size."""
    pf  = r.get('profit_factor') or 0
    pnl = r.get('net_pnl', 0)
    if r.get('total_trades', 0) < 15 or pf <= 0 or pnl <= 0:
        return -999.0
    return pnl * (pf ** 0.5)


def optimise_rtp(
    df: 'pd.DataFrame',
    interval: str = '5minute',
    min_trades: int = 15,
) -> List[Dict[str, Any]]:
    """
    Sweep all parameter combinations on a pre-fetched DataFrame.
    Returns results sorted best-first (highest _opt_score).
    """
    interval_minutes = _INTERVAL_MAP.get(interval, 1)
    results: List[Dict[str, Any]] = []

    for mode in _ENTRY_MODES:
        for sl, tgt in _SL_TGT_PAIRS:
            for use_adx in _USE_ADX_FLAGS:
                for adx_thresh in (_ADX_THRESHOLDS if use_adx else [25.0]):
                    try:
                        engine = RTPBacktestEngine(
                            df=df.copy(),
                            entry_mode=mode,
                            interval_minutes=interval_minutes,
                            use_adx=use_adx,
                            adx_thresh=adx_thresh,
                            sl_points=sl,
                            tgt_points=tgt,
                        )
                        r = engine.run()
                        if r['total_trades'] < min_trades:
                            continue
                        results.append({
                            'entry_mode':    mode,
                            'sl_points':     sl,
                            'tgt_points':    tgt,
                            'use_adx':       use_adx,
                            'adx_thresh':    adx_thresh if use_adx else None,
                            'total_trades':  r['total_trades'],
                            'wins':          r['wins'],
                            'losses':        r['losses'],
                            'win_rate':      r['win_rate'],
                            'net_pnl':       round(r['net_pnl'], 2),
                            'profit_factor': r['profit_factor'],
                            'max_drawdown':  round(r['max_drawdown'], 2),
                            'avg_win':       r.get('avg_win', 0),
                            'avg_loss':      r.get('avg_loss', 0),
                            'score':         round(_opt_score(r), 2),
                        })
                    except Exception as exc:
                        logger.debug(
                            "Optimise skip: %s SL=%s TGT=%s ADX=%s use_adx=%s — %s",
                            mode, sl, tgt, adx_thresh, use_adx, exc,
                        )

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


# ── Convenience runner ──────────────────────────────────────────────────────────

def fetch_and_run(
    fyers_adapter,
    symbol: str        = 'NSE:NIFTY50-INDEX',
    from_date: str     = '2025-01-01',
    to_date: str       = None,
    interval: str      = 'minute',
    entry_mode: str    = 'RTP(20 & 9)',
    use_adx: bool      = True,
    adx_thresh: float  = 25.0,
    sl_points: float   = None,
    tgt_points: float  = None,
) -> Dict[str, Any]:
    """
    Fetch NIFTY candles via Fyers and run the RTP backtest.

    Example:
        results = fetch_and_run(adapter, from_date='2025-01-01', to_date='2025-06-12')
    """
    if to_date is None:
        to_date = datetime.today().strftime('%Y-%m-%d')

    interval_minutes_map = {
        'minute': 1, '2minute': 2, '3minute': 3,
        '5minute': 5, '10minute': 10, '15minute': 15,
        '30minute': 30, '60minute': 60,
    }
    interval_minutes = interval_minutes_map.get(interval, 1)

    print(f"[RTP Backtest] Fetching {symbol} | {interval} | {from_date} → {to_date}")
    candles = fyers_adapter.historical_data(
        instrument_token=symbol,
        from_date=from_date,
        to_date=to_date,
        interval=interval,
        use_cache=False,
    )

    if not candles:
        print("[RTP Backtest] No data returned from Fyers.")
        return {}

    df = pd.DataFrame(candles)
    print(f"[RTP Backtest] {len(df)} candles loaded. Running …")

    engine = RTPBacktestEngine(
        df=df,
        entry_mode=entry_mode,
        interval_minutes=interval_minutes,
        use_adx=use_adx,
        adx_thresh=adx_thresh,
        sl_points=sl_points,
        tgt_points=tgt_points,
    )
    results = engine.run()
    _print_results(results)
    return results


def _print_results(r: Dict[str, Any]):
    sep = "=" * 54
    print(f"\n{sep}")
    print(f"  RTP Backtest Results  —  {r.get('entry_mode', '')}")
    print(sep)
    print(f"  Total Trades    : {r['total_trades']}")
    print(f"  Wins / Losses   : {r['wins']} / {r['losses']}")
    print(f"  Win Rate        : {r['win_rate']}%")
    print(f"  Net P&L         : {r['net_pnl']} pts")
    print(f"  Avg Win         : {r['avg_win']} pts")
    print(f"  Avg Loss        : {r['avg_loss']} pts")
    print(f"  Profit Factor   : {r['profit_factor']}")
    print(f"  Max Drawdown    : {r['max_drawdown']} pts")
    print(f"  Target / SL / Trail SL / EOD : {r['target_hits']} / {r['sl_hits']} / {r.get('trail_sl_hits',0)} / {r['eod_exits']}")
    print(f"  SL / Target     : {r['sl_points']} / {r['tgt_points']} pts")
    print(sep)

    trades = r.get('trades', [])
    if trades:
        df_t = pd.DataFrame(trades)
        df_t['entry_time'] = df_t['entry_time'].astype(str).str[:19]
        df_t['exit_time']  = df_t['exit_time'].astype(str).str[:19]
        print("\nTrade Log (last 20):")
        print(df_t.tail(20).to_string(index=False))
