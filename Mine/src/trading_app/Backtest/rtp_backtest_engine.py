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
        0.5: (10.0, 20.0),
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
        exit_on: str = 'value',
        confirm_bars: int = 0,
        strict_pattern: bool = False,
        min_rail_gap_atr: float = 0.0,
        max_trades_per_day: Optional[int] = None,
        max_consec_sl: Optional[int] = None,
    ):
        self.df = df.copy()
        self.entry_mode = entry_mode
        self.interval_minutes = interval_minutes
        self.slope_bars = slope_bars
        self.use_adx = use_adx
        self.adx_len = adx_len
        self.adx_thresh = adx_thresh
        # Entry-reduction filters (all default off → behaviour identical to before):
        #   confirm_bars      — after a signal candle, only enter once a later candle
        #                       breaks its high (BUY) / low (SELL) within N bars.
        #   strict_pattern    — signal candle must be a big-body directional candle
        #                       (no doji / weak hammer-engulfing entries).
        #   min_rail_gap_atr  — |EMA20−EMA50| must be >= X × ATR14 (skips flat,
        #                       braided-rail chop where touches whipsaw into SL).
        #   max_trades_per_day / max_consec_sl — daily circuit breaker.
        self.confirm_bars = max(0, int(confirm_bars or 0))
        self.strict_pattern = bool(strict_pattern)
        self.min_rail_gap_atr = float(min_rail_gap_atr or 0.0)
        self.max_trades_per_day = max_trades_per_day
        self.max_consec_sl = max_consec_sl
        # Exit evaluation mode:
        #   'value' — SL/Target hit when the candle's high/low pierces the level
        #             (intrabar fill at the level). This is the original behaviour.
        #   'close' — SL/Target hit only when the candle CLOSE crosses the level,
        #             and the fill is taken at that close.
        self.exit_on = 'close' if str(exit_on).lower() == 'close' else 'value'

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

        # ── Rail spread filter: rails must be SEPARATED, not braided together.
        # The parallel check alone passes when both EMAs are flat and glued to
        # price — the whipsaw regime where every touch stops out.
        if self.min_rail_gap_atr > 0:
            df['rail_ok'] = (gap >= atr14 * self.min_rail_gap_atr).fillna(False)
        else:
            df['rail_ok'] = pd.Series(True, index=df.index)

        # ── ADX trend filter
        _, _, adx_vals = self._adx(df, self.adx_len)
        df['adx'] = adx_vals
        if self.use_adx:
            df['trending'] = (df['adx'] >= self.adx_thresh).fillna(False)
        else:
            df['trending'] = pd.Series(True, index=df.index)

        # ── Railway track
        if self.entry_mode == 'RTP(50)':
            df['rway_up'] = df['ema20_up']   & df['ema50_up']   & df['trending'] & df['rail_ok']
            df['rway_dn'] = df['ema20_down'] & df['ema50_down'] & df['trending'] & df['rail_ok']
        else:
            df['rway_up'] = df['ema20_up']   & df['ema50_up']   & df['parallel'] & df['trending'] & df['rail_ok']
            df['rway_dn'] = df['ema20_down'] & df['ema50_down'] & df['parallel'] & df['trending'] & df['rail_ok']

        # ── EMA stack
        if self.entry_mode == 'RTP(50)':
            df['bull_stack'] = df['ema50'] < df['ema20']
            df['bear_stack'] = df['ema50'] > df['ema20']
        else:
            df['bull_stack'] = (df['ema50'] < df['ema20']) & (df['ema20'] < df['ema9'])
            df['bear_stack'] = (df['ema50'] > df['ema20']) & (df['ema20'] > df['ema9'])

        # ── Session filter (9:20 AM onwards; interval-aware close cutoff)
        # A signal bar opening at minute M completes at M + interval_minutes, and
        # the live algo stops checking at 3:28 — so the last actionable signal bar
        # is the one that COMPLETES by 3:27. Allowing signal bars stamped up to
        # :28 regardless of interval let the backtest take late trades (and, on
        # 3m/5m, enter at the NEXT DAY's open) that live can never take.
        h, m = df['_hour'], df['_min']
        after_open    = (h > 9) | ((h == 9)  & (m >= 20))
        last_sig_min  = 27 - self.interval_minutes
        before_cutoff = (h < 15) | ((h == 15) & (m <= last_sig_min))
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
            if self.strict_pattern:
                df['buy_pat']  = (df['close'] > df['open']).fillna(False)
                df['sell_pat'] = (df['close'] < df['open']).fillna(False)
            else:
                df['buy_pat']  = ((df['close'] > df['open']) | is_doji).fillna(False)
                df['sell_pat'] = ((df['close'] < df['open']) | is_doji).fillna(False)
        elif self.strict_pattern:
            # Strict: only big-body directional candles with a strong close —
            # no doji entries, no weak hammers/engulfings without body quality.
            df['buy_pat']  = is_buy_cdl.fillna(False)
            df['sell_pat'] = is_sell_cdl.fillna(False)
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

        # Pre-extract hot-path columns into numpy arrays. Row-by-row df.iloc[]
        # access is ~100x slower and, across an 80-combo optimise sweep over
        # multi-year minute data, makes uncached runs effectively never finish.
        o   = df['open'].to_numpy()
        h   = df['high'].to_numpy()
        l   = df['low'].to_numpy()
        cl  = df['close'].to_numpy()
        ema9  = df['ema9'].to_numpy()
        ema20 = df['ema20'].to_numpy()
        ema50 = df['ema50'].to_numpy()
        session_ok = df['session_ok'].to_numpy()
        rway_up    = df['rway_up'].to_numpy()
        rway_dn    = df['rway_dn'].to_numpy()
        bull_stack = df['bull_stack'].to_numpy()
        bear_stack = df['bear_stack'].to_numpy()
        buy_touch  = df['buy_touch'].to_numpy()
        sell_touch = df['sell_touch'].to_numpy()
        buy_pat    = df['buy_pat'].to_numpy()
        sell_pat   = df['sell_pat'].to_numpy()
        hour = df['_hour'].to_numpy()
        minute = df['_min'].to_numpy()
        # Keep tz-aware Timestamps (tolist preserves tz; to_numpy() would drop it)
        dt_list = df['datetime'].tolist()
        date_list = [t.date() for t in dt_list]

        is_rtp50    = self.entry_mode == 'RTP(50)'
        close_exit  = self.exit_on == 'close'

        sell_needs_reset = False
        buy_needs_reset  = False

        # Confirmation-candle state: after a signal, wait for a later bar to
        # break the signal candle's high (BUY) / low (SELL) before entering.
        pending = None  # (direction, break_level, expiry_idx)
        skipped_unconfirmed = 0
        skipped_circuit     = 0

        # Daily circuit breaker state: date -> [trades_taken, consecutive_sl]
        day_state: Dict[Any, List[int]] = {}

        def day_blocked(d) -> bool:
            st = day_state.get(d)
            if st is None:
                return False
            if self.max_trades_per_day and st[0] >= self.max_trades_per_day:
                return True
            if self.max_consec_sl and st[1] >= self.max_consec_sl:
                return True
            return False

        def scan_exit(start_idx, entry_price, direction, entry_date):
            """Scan bars from start_idx for SL / Target / trailing SL / EOD.
            Returns (exit_price, exit_time, exit_reason, exit_idx)."""
            trail      = self.trail_points
            best_price = entry_price          # peak high (BUY) or trough low (SELL)
            current_sl = (entry_price - self.sl_points) if direction == 'BUY' \
                         else (entry_price + self.sl_points)

            for j in range(start_idx, n):
                # Force-close on the last bar that completes by ~3:28 PM, or on
                # the next day as a safety net. A bar opening at minute M spans
                # M..M+interval, so waiting for a bar stamped >= :28 on 3m/5m
                # grids (last bars 15:27 / 15:25) skipped to the NEXT DAY and
                # held overnight — the live algo hard-squares-off at 3:28.
                eod_cutoff = (hour[j] == 15 and minute[j] + self.interval_minutes > 28) \
                             or hour[j] > 15
                if date_list[j] != entry_date or eod_cutoff:
                    return cl[j], dt_list[j], 'EOD', j

                # In 'close' mode the SL/Target levels are tested against the bar
                # close only (and filled at the close); in 'value' mode they are
                # tested against the bar high/low (and filled at the level).
                test_hi = cl[j] if close_exit else h[j]
                test_lo = cl[j] if close_exit else l[j]

                if direction == 'BUY':
                    if trail and test_hi > best_price:
                        best_price = test_hi
                        steps = int((best_price - entry_price) / trail)
                        new_sl = (entry_price - self.sl_points) + steps * trail
                        current_sl = max(current_sl, new_sl)

                    if test_lo <= current_sl:
                        return (cl[j] if close_exit else current_sl), dt_list[j], \
                               ('TRAIL_SL' if trail else 'SL'), j
                    if test_hi >= entry_price + self.tgt_points:
                        return (cl[j] if close_exit else entry_price + self.tgt_points), \
                               dt_list[j], 'TARGET', j
                else:
                    if trail and test_lo < best_price:
                        best_price = test_lo
                        steps = int((entry_price - best_price) / trail)
                        new_sl = (entry_price + self.sl_points) - steps * trail
                        current_sl = min(current_sl, new_sl)

                    if test_hi >= current_sl:
                        return (cl[j] if close_exit else current_sl), dt_list[j], \
                               ('TRAIL_SL' if trail else 'SL'), j
                    if test_lo <= entry_price - self.tgt_points:
                        return (cl[j] if close_exit else entry_price - self.tgt_points), \
                               dt_list[j], 'TARGET', j

            return cl[-1], dt_list[-1], 'EOD', n - 1

        def record_trade(entry_time, entry_price, entry_date, direction,
                         exit_price, exit_time, exit_reason):
            pnl = (exit_price - entry_price) if direction == 'BUY' \
                  else (entry_price - exit_price)
            trades.append({
                'entry_time':  entry_time,
                'entry_price': round(entry_price, 2),
                'direction':   direction,
                'exit_time':   exit_time,
                'exit_price':  round(exit_price, 2),
                'exit_reason': exit_reason,
                'pnl':         round(pnl, 2),
            })
            st = day_state.setdefault(entry_date, [0, 0])
            st[0] += 1
            if pnl < 0 and exit_reason in ('SL', 'TRAIL_SL'):
                st[1] += 1
            elif pnl > 0:
                st[1] = 0

        i = 200  # Skip warmup: EMA50 needs ~200 bars to converge on 1-min data
        while i < n:
            # Reset candle check (mirrors Pine var bool logic)
            if sell_needs_reset:
                if is_rtp50:
                    if h[i] < ema50[i]:
                        sell_needs_reset = False
                else:
                    if h[i] < min(ema9[i], ema20[i]):
                        sell_needs_reset = False

            if buy_needs_reset:
                if is_rtp50:
                    if l[i] > ema50[i]:
                        buy_needs_reset = False
                else:
                    if l[i] > max(ema9[i], ema20[i]):
                        buy_needs_reset = False

            # Resolve a pending confirmation-candle signal first
            if pending is not None:
                p_dir, p_level, p_expiry = pending
                if i > p_expiry or not session_ok[i]:
                    pending = None
                    skipped_unconfirmed += 1
                elif day_blocked(date_list[i]):
                    pending = None
                    skipped_circuit += 1
                else:
                    filled = None
                    if p_dir == 'BUY':
                        if o[i] >= p_level:
                            filled = o[i]        # gapped past the level → fill at open
                        elif h[i] >= p_level:
                            filled = p_level     # broke intrabar → fill at the level
                    else:
                        if o[i] <= p_level:
                            filled = o[i]
                        elif l[i] <= p_level:
                            filled = p_level
                    if filled is not None:
                        pending = None
                        entry_date = date_list[i]
                        exit_price, exit_time, exit_reason, exit_idx = \
                            scan_exit(i, filled, p_dir, entry_date)
                        record_trade(dt_list[i], filled, entry_date, p_dir,
                                     exit_price, exit_time, exit_reason)
                        i = exit_idx + 1
                        continue

            # Signal check
            buy_signal = bool(
                session_ok[i] and rway_up[i] and bull_stack[i] and
                buy_touch[i]  and buy_pat[i] and not buy_needs_reset
            )
            sell_signal = bool(
                session_ok[i] and rway_dn[i] and bear_stack[i] and
                sell_touch[i] and sell_pat[i] and not sell_needs_reset
            )

            if sell_signal:
                sell_needs_reset = True
            if buy_signal:
                buy_needs_reset = True

            if not (buy_signal or sell_signal):
                i += 1
                continue

            direction = 'BUY' if buy_signal else 'SELL'

            # Daily circuit breaker: max trades/day or SL-streak reached →
            # skip the signal (the reset latch above still arms, so at most
            # one skip is counted per pullback).
            if day_blocked(date_list[i]):
                skipped_circuit += 1
                i += 1
                continue

            # Confirmation-candle mode: don't enter yet — wait for a later bar
            # to break the signal candle's high (BUY) / low (SELL).
            if self.confirm_bars > 0:
                level   = h[i] if buy_signal else l[i]
                pending = (direction, level, i + self.confirm_bars)
                i += 1
                continue

            # Entry fills at next bar open (process_orders_on_close=false).
            #
            # DELIBERATE divergence from the live algo, which fills at the
            # signal bar's close (rtp_algo.py, `signal_bar_close`). Do not
            # "unify" the two while deduping the RTP state machine — it would
            # change live fill prices. tests/test_rtp_live_vs_backtest.py
            # sidesteps it with gapless synthetic data so the two coincide.
            if i + 1 >= n:
                i += 1
                continue

            entry_price = o[i + 1]
            entry_time  = dt_list[i + 1]
            entry_date  = date_list[i + 1]

            exit_price, exit_time, exit_reason, exit_idx = \
                scan_exit(i + 1, entry_price, direction, entry_date)
            record_trade(entry_time, entry_price, entry_date, direction,
                         exit_price, exit_time, exit_reason)

            # Jump to bar after exit so trades don't overlap
            i = exit_idx + 1

        return self._summarise(trades, skipped_unconfirmed, skipped_circuit)

    # ── Summary ─────────────────────────────────────────────────────────────────

    def _summarise(self, trades: List[Dict],
                   skipped_unconfirmed: int = 0,
                   skipped_circuit: int = 0) -> Dict[str, Any]:
        filter_info = {
            'confirm_bars':        self.confirm_bars,
            'strict_pattern':      self.strict_pattern,
            'min_rail_gap_atr':    self.min_rail_gap_atr,
            'max_trades_per_day':  self.max_trades_per_day,
            'max_consec_sl':       self.max_consec_sl,
            'skipped_unconfirmed': skipped_unconfirmed,
            'skipped_circuit':     skipped_circuit,
        }
        if not trades:
            print("No trades generated.")
            return {
                'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0,
                'net_pnl': 0.0, 'avg_win': 0.0, 'avg_loss': 0.0,
                'profit_factor': 0.0, 'max_drawdown': 0.0,
                'max_dd_start': None, 'max_dd_end': None,
                'target_hits': 0, 'sl_hits': 0, 'trail_sl_hits': 0, 'eod_exits': 0,
                'entry_mode': self.entry_mode, 'exit_on': self.exit_on,
                'sl_points': self.sl_points, 'tgt_points': self.tgt_points,
                'trail_points': self.trail_points,
                'trades': [], **filter_info,
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
            'exit_on':       self.exit_on,
            'trades':        trades,
            **filter_info,
        }


# ── Parameter Optimiser ─────────────────────────────────────────────────────────

import math as _math

# Grid searched during optimisation.
# SL/TGT pairs trimmed to the 7 that actually reach the per-timeframe
# leaderboards (the dropped (10,20)/(15,45)/(20,40) held 3 of 50 slots in the
# v4 cache) — the grid gained confirm/rail-gap dimensions, so dead pairs are
# pure sweep time.
_ENTRY_MODES    = ['RTP(20 & 9)', 'RTP(50)']
_SL_TGT_PAIRS   = [
    (10, 30),
    (15, 30),
    (20, 60),
    (25, 50), (25, 75),
    (30, 60), (30, 90),
]
_ADX_THRESHOLDS = [20, 25, 30]
_USE_ADX_FLAGS  = [True, False]
_CONFIRM_BARS   = [0, 2]      # 0 = enter at next open; 2 = need break of signal bar hi/lo within 2 bars
_RAIL_GAPS_ATR  = [0.0, 0.2]  # 0 = off; 0.2 = |EMA20−EMA50| >= 0.2×ATR14 (skip braided rails)

_INTERVAL_MAP = {
    '30second': 0.5,
    'minute': 1, '2minute': 2, '3minute': 3, '4minute': 4,
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
    progress_cb=None,
) -> List[Dict[str, Any]]:
    """
    Sweep all parameter combinations on a pre-fetched DataFrame.
    Returns results sorted best-first (highest _opt_score).

    Indicator preparation (EMAs/ADX/patterns) only depends on the signal-side
    params (mode, ADX, rail gap), so the engine is built once per signal combo
    and reused across every SL/TGT/confirm variation — _prepare() on multi-year
    minute data is far more expensive than a run() pass.

    progress_cb(done, total) is invoked after every combo (for status display).
    """
    interval_minutes = _INTERVAL_MAP.get(interval, 1)
    results: List[Dict[str, Any]] = []

    signal_combos = [
        (mode, use_adx, adx_thresh, rail_gap)
        for mode in _ENTRY_MODES
        for use_adx in _USE_ADX_FLAGS
        for adx_thresh in (_ADX_THRESHOLDS if use_adx else [25.0])
        for rail_gap in _RAIL_GAPS_ATR
    ]
    runs_per_signal = len(_SL_TGT_PAIRS) * len(_CONFIRM_BARS)
    total = len(signal_combos) * runs_per_signal
    done = 0

    for mode, use_adx, adx_thresh, rail_gap in signal_combos:
        try:
            engine = RTPBacktestEngine(
                df=df,   # ctor copies
                entry_mode=mode,
                interval_minutes=interval_minutes,
                use_adx=use_adx,
                adx_thresh=adx_thresh,
                sl_points=_SL_TGT_PAIRS[0][0],
                tgt_points=_SL_TGT_PAIRS[0][1],
                min_rail_gap_atr=rail_gap,
            )
        except Exception as exc:
            logger.debug("Optimise prepare failed: %s use_adx=%s ADX=%s rail_gap=%s — %s",
                         mode, use_adx, adx_thresh, rail_gap, exc)
            done += runs_per_signal
            if progress_cb:
                progress_cb(done, total)
            continue

        for sl, tgt in _SL_TGT_PAIRS:
            for confirm_bars in _CONFIRM_BARS:
                done += 1
                try:
                    # run() only reads these — safe to mutate between passes
                    engine.sl_points    = float(sl)
                    engine.tgt_points   = float(tgt)
                    engine.confirm_bars = confirm_bars
                    r = engine.run()
                    if r['total_trades'] < min_trades:
                        continue
                    results.append({
                        'entry_mode':       mode,
                        'sl_points':        sl,
                        'tgt_points':       tgt,
                        'use_adx':          use_adx,
                        'adx_thresh':       adx_thresh if use_adx else None,
                        'confirm_bars':     confirm_bars,
                        'min_rail_gap_atr': rail_gap,
                        'total_trades':     r['total_trades'],
                        'wins':             r['wins'],
                        'losses':           r['losses'],
                        'win_rate':         r['win_rate'],
                        'net_pnl':          round(r['net_pnl'], 2),
                        'profit_factor':    r['profit_factor'],
                        'max_drawdown':     round(r['max_drawdown'], 2),
                        'avg_win':          r.get('avg_win', 0),
                        'avg_loss':         r.get('avg_loss', 0),
                        'score':            round(_opt_score(r), 2),
                    })
                except Exception as exc:
                    logger.debug(
                        "Optimise skip: %s SL=%s TGT=%s ADX=%s use_adx=%s "
                        "confirm=%s rail_gap=%s — %s",
                        mode, sl, tgt, adx_thresh, use_adx,
                        confirm_bars, rail_gap, exc,
                    )
                finally:
                    if progress_cb:
                        progress_cb(done, total)

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
        '30second': 0.5,
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
