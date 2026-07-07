"""
RTP Railway Track Algo Trader
Monitors NIFTY candles every second during market hours.
On a BUY signal → buys the CE option with delta ~0.90.
On a SELL signal → buys the PE option with delta ~0.90.
Exits when NIFTY spot crosses the SL / Target (per-variant) from entry spot.
Multiple re-entries allowed per day. Auto-started at 9:15 AM by the scheduler.

Timeframe variants share this identical logic (see RTP_VARIANTS):
  '1m'  — 1-minute candles,  SL 30 / Target 90, ADX off
  '30s' — 30-second candles, SL 25 / Target 75, ADX on @ 30
  '3m'  — 3-minute candles,  SL 30 / Target 90, ADX off
  '5m'  — 5-minute candles,  SL 30 / Target 90, ADX off

Per-user env vars (VAR is 'RTP_1M' for 1m, 'RTP_30S' for 30s, etc.):
  EMA_RTP_1M_ACTIVE / EMA_RTP_30S_ACTIVE = true
  BROKER_N_<VAR>_ACTIVE = true/false   (alongside BROKER_N_ACTIVE)
  BROKER_N_<VAR>_LOTS   = 1            (per-broker lot count)
"""
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, date, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(__file__)

_SINGLE_LOT_QTY = 65     # NIFTY single lot — used for Opt P&L display
_DELTA_TARGET   = 0.90
_MAX_PREMIUM    = 500.0  # cap on option premium — if the ±0.90Δ strike costs more, shift to the strike priced nearest below this
_STRIKE_STEP    = 50.0
_SLOPE_BARS     = 8
_NIFTY_FYERS    = 'NSE:NIFTY50-INDEX'
_WARMUP_BARS    = 200  # Must match backtest warmup — EMA50 needs ~200 bars to converge
_LOOKBACK_DAYS  = 5    # Calendar days to look back for warmup candles (covers weekends/holidays)
_MAX_SPOT_FAILS = 60   # consecutive None returns before CRITICAL log (~1 minute of data gap)
_FALLBACK_IV    = 0.15  # assumed annual IV when ATM IV cannot be computed (NIFTY typical range 13–18%)
_RECONCILE_SECS = 25   # how often to reconcile active_trade against real broker positions

# Bar duration per provider interval — a bar stamped with its open time is only
# completed once open_time + duration <= now. Flooring "now" to the minute (the
# old cutoff) is equivalent for 1-min bars but treats in-progress 3m/5m bars as
# completed, firing live signals on partial candles the backtest never sees.
_INTERVAL_SECS = {'30second': 30, 'minute': 60, '3minute': 180, '5minute': 300}


@dataclass(frozen=True)
class RTPVariant:
    """Per-timeframe configuration for a live RTP Railway Track algo instance.

    Both variants share the exact same signal/entry/exit code path — only these
    values differ, so the 1m and 30s algos can never drift apart in logic.
    """
    key:              str    # variant id used in the instance registry ('1m' / '30s')
    interval:         str    # provider historical_data resolution
    sl_points:        float
    tgt_points:       float
    use_adx:          bool
    adx_thresh:       float
    state_file:       str
    history_file:     str
    all_history_file: str
    env_active:       str    # per-user "is this algo enabled" flag
    broker_tag:       str    # BROKER_{i}_<tag>_ACTIVE / _LOTS
    log_prefix:       str
    thread_name:      str


RTP_VARIANTS: Dict[str, RTPVariant] = {
    '1m': RTPVariant(
        key='1m',
        interval='minute',
        sl_points=30.0,
        tgt_points=90.0,
        use_adx=False,
        adx_thresh=25.0,
        state_file=os.path.join(_DIR, 'rtp_state.json'),
        history_file=os.path.join(_DIR, 'rtp_trades_history.json'),
        all_history_file=os.path.join(_DIR, 'rtp_trades_all_history.json'),
        env_active='EMA_RTP_1M_ACTIVE',
        broker_tag='RTP_1M',
        log_prefix='RTP',
        thread_name='rtp-algo-monitor',
    ),
    '30s': RTPVariant(
        key='30s',
        interval='30second',
        sl_points=25.0,
        tgt_points=75.0,
        use_adx=True,
        adx_thresh=30.0,
        state_file=os.path.join(_DIR, 'rtp_state_30s.json'),
        history_file=os.path.join(_DIR, 'rtp_trades_history_30s.json'),
        all_history_file=os.path.join(_DIR, 'rtp_trades_all_history_30s.json'),
        env_active='EMA_RTP_30S_ACTIVE',
        broker_tag='RTP_30S',
        log_prefix='RTP30s',
        thread_name='rtp-algo-monitor-30s',
    ),
    '3m': RTPVariant(
        key='3m',
        interval='3minute',
        sl_points=30.0,
        tgt_points=90.0,
        use_adx=False,
        adx_thresh=25.0,
        state_file=os.path.join(_DIR, 'rtp_state_3m.json'),
        history_file=os.path.join(_DIR, 'rtp_trades_history_3m.json'),
        all_history_file=os.path.join(_DIR, 'rtp_trades_all_history_3m.json'),
        env_active='EMA_RTP_3M_ACTIVE',
        broker_tag='RTP_3M',
        log_prefix='RTP3m',
        thread_name='rtp-algo-monitor-3m',
    ),
    '5m': RTPVariant(
        key='5m',
        interval='5minute',
        sl_points=30.0,
        tgt_points=90.0,
        use_adx=False,
        adx_thresh=25.0,
        state_file=os.path.join(_DIR, 'rtp_state_5m.json'),
        history_file=os.path.join(_DIR, 'rtp_trades_history_5m.json'),
        all_history_file=os.path.join(_DIR, 'rtp_trades_all_history_5m.json'),
        env_active='EMA_RTP_5M_ACTIVE',
        broker_tag='RTP_5M',
        log_prefix='RTP5m',
        thread_name='rtp-algo-monitor-5m',
    ),
}


class _PrefixLogger(logging.LoggerAdapter):
    """Prepends the variant tag (e.g. [RTP30s]) to every log line so the two
    parallel live algos are distinguishable in a shared log stream."""
    def process(self, msg, kwargs):
        return f"[{self.extra['lp']}] {msg}", kwargs


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via math.erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_delta(flag: str, S: float, K: float, t: float, r: float, sigma: float) -> float:
    """Black-Scholes delta. flag='c' → call; flag='p' → put.
    Returns 0.0 on bad inputs instead of raising."""
    if t <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
    nd1 = _norm_cdf(d1)
    return nd1 if flag == 'c' else nd1 - 1.0


def _time_to_expiry_years(expiry: Any) -> float:
    """Actual time remaining until 15:30 on expiry day, in years.

    GreeksCalculator.calculate_time_to_expiry floors T at 0.001y (~8.8h),
    which overstates T for the whole of expiry day and skews 0DTE deltas;
    floor at 2 minutes instead so delta stays honest up to the EOD cutoff."""
    if isinstance(expiry, datetime):
        expiry = expiry.date()
    exp_dt = datetime.combine(expiry, dt_time(15, 30))
    secs = (exp_dt - datetime.now()).total_seconds()
    return max(secs, 120.0) / (365.0 * 24.0 * 3600.0)


# Module-level registry so the API can reach the running instance without storing it in the scheduler.
# Keyed by (username, variant) so the 1m and 30s algos coexist per user.
_instances: Dict[Tuple[str, str], 'RTPAlgo'] = {}


def get_instance(username: str, variant: str = '1m') -> Optional['RTPAlgo']:
    return _instances.get((username, variant))


class RTPAlgo:
    """Live RTP Railway Track signal detector and option order executor."""

    def __init__(self, username: str, variant: str = '1m'):
        if variant not in RTP_VARIANTS:
            raise ValueError(f"Unknown RTP variant: {variant!r}")
        self.username = username
        self.variant  = variant
        self.cfg      = RTP_VARIANTS[variant]
        self.log      = _PrefixLogger(logger, {'lp': self.cfg.log_prefix})
        self._stop_event   = threading.Event()
        self._state_lock   = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        # Populated once at monitor-loop start; reused all day
        self._broker_map: Dict[int, Tuple[str, Any]] = {}  # idx -> (broker_type, svc)
        self._instruments: List[Dict] = []
        self._expiry: Optional[date] = None
        self._lot_size: int = 75
        self._spot_fail_count: int = 0
        self._bar_td = pd.Timedelta(seconds=_INTERVAL_SECS.get(self.cfg.interval, 60))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name=self.cfg.thread_name,
        )
        self._thread.start()
        _instances[(self.username, self.variant)] = self
        self.log.info("Monitoring thread started")

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop((self.username, self.variant), None)
        self.log.info("Stop requested")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── State ─────────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(self.cfg.state_file, 'r') as f:
                return json.load(f)
        except Exception:
            return {'active_trade': None, 'buy_needs_reset': False, 'sell_needs_reset': False}

    def _save_state(self, state: Dict[str, Any]) -> None:
        with self._state_lock:
            try:
                with open(self.cfg.state_file, 'w') as f:
                    json.dump(state, f, indent=2, default=str)
            except Exception as e:
                self.log.error(f"State save failed: {e}")

    def _append_history(self, trade: Dict[str, Any], exit_spot: float, reason: str,
                         opt_exit_price: Optional[float] = None) -> None:
        """Append a completed trade record to rtp_trades_history.json (today only, latest-first)."""
        try:
            today = date.today().isoformat()
            try:
                with open(self.cfg.history_file, 'r') as f:
                    history: list = json.load(f)
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []

            # Day rotation — discard yesterday's records when a new day starts
            if history and history[0].get('date') != today:
                history = []

            direction   = trade.get('direction', 'BUY')
            entry_spot  = float(trade.get('entry_spot', 0))
            pnl_pts     = round(exit_spot - entry_spot, 2) if direction == 'BUY' \
                          else round(entry_spot - exit_spot, 2)

            opt_entry_price = trade.get('opt_entry_price')
            opt_pnl_pts: Optional[float] = None
            opt_pnl_inr: Optional[float] = None
            if opt_entry_price is not None and opt_exit_price is not None:
                opt_pnl_pts = round(opt_exit_price - opt_entry_price, 2)
                opt_pnl_inr = round(opt_pnl_pts * _SINGLE_LOT_QTY, 2)

            record = {
                'date':             today,
                'direction':        direction,
                'entry_spot':       entry_spot,
                'exit_spot':        exit_spot,
                'pnl_pts':          pnl_pts,
                'reason':           reason,
                'entry_time':       trade.get('entry_time', ''),
                'exit_time':        datetime.now().isoformat(),
                'strike':           trade.get('strike'),
                'option_type':      trade.get('option_type', ''),
                'lot_size':         trade.get('lot_size', 75),
                'opt_entry_price':  opt_entry_price,
                'opt_exit_price':   opt_exit_price,
                'opt_pnl_pts':      opt_pnl_pts,
                'opt_pnl_inr':      opt_pnl_inr,
                'broker_entries':   trade.get('broker_entries', []),
            }
            history.insert(0, record)  # latest-first

            with open(self.cfg.history_file, 'w') as f:
                json.dump(history, f, indent=2, default=str)

            # Append to permanent all-time history (no day rotation)
            try:
                try:
                    with open(self.cfg.all_history_file, 'r') as f:
                        all_history: list = json.load(f)
                    if not isinstance(all_history, list):
                        all_history = []
                except Exception:
                    all_history = []
                all_history.insert(0, record)
                with open(self.cfg.all_history_file, 'w') as f:
                    json.dump(all_history, f, indent=2, default=str)
            except Exception as _ae:
                self.log.error(f"All-history append failed: {_ae}")
        except Exception as e:
            self.log.error(f"History append failed: {e}")

    # ── Env helpers ───────────────────────────────────────────────────────────

    def _uvar(self, key: str, default: str = '') -> str:
        from trading_app.app.utils.user_env import UserEnvManager
        return (UserEnvManager.get_user_var(self.username, key) or default).strip()

    # ── Data ──────────────────────────────────────────────────────────────────

    def _get_provider(self) -> Any:
        from trading_app.service.provider_logic import get_data_provider
        return get_data_provider(user=self.username)

    def _get_nifty_spot(self, provider: Any) -> Optional[float]:
        try:
            data = provider.ltp([_NIFTY_FYERS])
            ltp = data.get(_NIFTY_FYERS, {}).get('last_price', 0)
            return float(ltp) if ltp else None
        except Exception as e:
            self.log.warning(f"Spot fetch failed: {e}")
            return None

    def _fetch_today_1min_candles(self, provider: Any) -> Optional[pd.DataFrame]:
        """Fetch only today's 1-min candles — lightweight for in-trade H/L exit checking."""
        try:
            today = date.today().strftime('%Y-%m-%d')
            candles = provider.historical_data(
                instrument_token=_NIFTY_FYERS,
                from_date=today,
                to_date=today,
                interval=self.cfg.interval,
                use_cache=False,
            )
            if not candles:
                return None
            df = pd.DataFrame(candles).reset_index(drop=True)
            if df.empty:
                return None
            if 'date' in df.columns:
                df['datetime'] = pd.to_datetime(df['date'])
            else:
                df['datetime'] = pd.to_datetime(df.index)
            if df['datetime'].dt.tz is None:
                df['datetime'] = df['datetime'].dt.tz_localize(
                    'Asia/Kolkata', ambiguous='infer', nonexistent='shift_forward'
                )
            else:
                df['datetime'] = df['datetime'].dt.tz_convert('Asia/Kolkata')
            return df
        except Exception as e:
            self.log.warning(f"Today candle fetch failed: {e}")
            return None

    def _fetch_1min_candles(self, provider: Any) -> Optional[pd.DataFrame]:
        """Fetch 1-min candles with enough history for EMA50 to converge.
        Today-only data (~50 bars at 10 AM) gives a completely wrong EMA50,
        causing false signals that diverge from backtest results.
        """
        try:
            today     = date.today()
            from_date = (today - timedelta(days=_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
            candles   = provider.historical_data(
                instrument_token=_NIFTY_FYERS,
                from_date=from_date,
                to_date=today.strftime('%Y-%m-%d'),
                interval=self.cfg.interval,
                use_cache=False,
            )
            if not candles:
                return None
            return pd.DataFrame(candles).reset_index(drop=True)
        except Exception as e:
            self.log.warning(f"Candle fetch failed: {e}")
            return None

    # ── Signal detection ──────────────────────────────────────────────────────

    def _check_rtp_signal(
        self, df: pd.DataFrame, state: Dict[str, Any],
        last_bar_dt: Optional[pd.Timestamp] = None,
    ) -> Tuple[Optional[str], Dict[str, Any], Optional[pd.Timestamp], Optional[float]]:
        """
        Check for BUY/SELL signal on completed bars not yet examined.

        Processes ALL bars since last_bar_dt (not just the most recent one).
        This handles Fyers historical-data API delays: if the signal bar is
        delivered 1-2 minutes late, it may no longer be completed.iloc[-1] by
        the time we check, and would be missed entirely by a last-bar-only scan.

        Needs-reset flag updates run on EVERY bar (including pre/post-session)
        to mirror the backtest's sequential processing exactly.

        Returns (signal, updated_state, new_last_bar_dt, signal_bar_close).
        signal_bar_close is the CLOSE of the signal bar — used as the SL/Target
        reference so that live behaviour matches backtest exactly (backtest
        entries are always at bar close, not at real-time spot).
        """
        try:
            from trading_app.Backtest.rtp_backtest_engine import RTPBacktestEngine
            engine = RTPBacktestEngine(
                df=df,
                entry_mode='RTP(20 & 9)',
                interval_minutes=1,
                slope_bars=_SLOPE_BARS,
                use_adx=self.cfg.use_adx,
                adx_thresh=self.cfg.adx_thresh,
                sl_points=self.cfg.sl_points,
                tgt_points=self.cfg.tgt_points,
            )
            processed = engine.df
            if len(processed) < 2:
                return None, state, last_bar_dt, None

            # Exclude the current in-progress bar: a bar stamped with its open
            # time is completed only once open_time + bar duration <= now.
            # (floor('min') was only correct for 1-min bars — it let the live 3m/5m
            # variants evaluate partial candles and diverge from the backtest.)
            now_ist   = pd.Timestamp.now(tz='Asia/Kolkata')
            completed = processed[processed['datetime'] + self._bar_td <= now_ist]
            if len(completed) < 2:
                return None, state, last_bar_dt, None

            new_last_bar_dt = completed.iloc[-1]['datetime']

            # Which bars to examine:
            # • First run (last_bar_dt=None): only the most recent completed bar —
            #   earlier bars have already been processed by _replay_today_needs_reset.
            # • Subsequent runs: every bar that arrived since last_bar_dt, in order.
            #   This catches signals on bars delayed by the data provider (they may
            #   appear 1-2 minutes late and no longer be the last bar when we check).
            if last_bar_dt is not None:
                to_check = completed[completed['datetime'] > last_bar_dt]
            else:
                to_check = completed.iloc[-1:]

            if to_check.empty:
                return None, state, new_last_bar_dt, None

            buy_needs_reset  = bool(state.get('buy_needs_reset',  False))
            sell_needs_reset = bool(state.get('sell_needs_reset', False))
            signal: Optional[str] = None
            signal_bar_close: Optional[float] = None

            for _, row in to_check.iterrows():
                bar_dt = row['datetime']

                # Needs-reset clears on ALL bars — the backtest's sequential loop
                # does not gate this on session_ok, so neither should we.
                if sell_needs_reset and row['high'] < min(row['ema9'], row['ema20']):
                    sell_needs_reset = False
                    self.log.debug(f"sell_needs_reset cleared at {bar_dt}")
                if buy_needs_reset  and row['low']  > max(row['ema9'], row['ema20']):
                    buy_needs_reset  = False
                    self.log.debug(f"buy_needs_reset cleared at {bar_dt}")

                if not row.get('session_ok', False):
                    continue  # skip signal check; reset updates above still run

                buy_signal = bool(
                    row['rway_up']   and row['bull_stack'] and
                    row['buy_touch'] and row['buy_pat']    and not buy_needs_reset
                )
                sell_signal = bool(
                    row['rway_dn']    and row['bear_stack'] and
                    row['sell_touch'] and row['sell_pat']   and not sell_needs_reset
                )

                self.log.debug(
                    f"{bar_dt} | "
                    f"rway_up={row.get('rway_up')} bull={row.get('bull_stack')} "
                    f"buy_touch={row.get('buy_touch')} buy_pat={row.get('buy_pat')} "
                    f"buy_nr={buy_needs_reset} → buy={buy_signal} | "
                    f"rway_dn={row.get('rway_dn')} bear={row.get('bear_stack')} "
                    f"sell_touch={row.get('sell_touch')} sell_pat={row.get('sell_pat')} "
                    f"sell_nr={sell_needs_reset} → sell={sell_signal}"
                )

                if buy_signal:
                    buy_needs_reset  = True
                    new_last_bar_dt  = bar_dt
                    signal           = 'BUY'
                    signal_bar_close = float(row['close'])
                    break
                if sell_signal:
                    sell_needs_reset = True
                    new_last_bar_dt  = bar_dt
                    signal           = 'SELL'
                    signal_bar_close = float(row['close'])
                    break

            state['buy_needs_reset']  = buy_needs_reset
            state['sell_needs_reset'] = sell_needs_reset

            if signal:
                self.log.info(
                    f"✓ Signal={signal} on bar {new_last_bar_dt} "
                    f"bar_close={signal_bar_close} "
                    f"(checked {len(to_check)} bar(s) since {last_bar_dt})"
                )
            else:
                self.log.debug(
                    f"No signal — checked {len(to_check)} bar(s), "
                    f"last={new_last_bar_dt} buy_nr={buy_needs_reset} sell_nr={sell_needs_reset}"
                )

            return signal, state, new_last_bar_dt, signal_bar_close

        except Exception as e:
            self.log.error(f"Signal check error: {e}", exc_info=True)
            return None, state, last_bar_dt, None

    # ── Instruments ───────────────────────────────────────────────────────────

    def _get_instruments_and_expiry(
        self, provider: Any
    ) -> Tuple[List[Dict], date, int]:
        """Fetch NFO instruments, resolve nearest usable weekly expiry and lot size."""
        today = date.today()
        instruments: List[Dict] = provider.instruments('NFO')
        expiry_dates = sorted({
            inst['expiry'] for inst in instruments
            if (inst.get('name') or '').upper() == 'NIFTY'
            and inst.get('instrument_type') in ('CE', 'PE')
            and inst.get('expiry') is not None
            and inst['expiry'] >= today
        })
        if not expiry_dates:
            raise ValueError("No NIFTY expiry dates found in instruments")
        # Keep the nearest expiry even on expiry day: the Fyers option chain
        # used for delta selection and order symbols always trades the nearest
        # expiry, so T must be computed against that same expiry. (Bumping to
        # next week here made T≈7d while trading 0DTE options — on expiry-day
        # afternoons that mispriced delta and selected ~700-pt ITM strikes.)
        expiry = expiry_dates[0]
        lot_size = 75
        for inst in instruments:
            if (inst.get('name') or '').upper() == 'NIFTY' and inst.get('expiry') == expiry:
                lot_size = int(inst.get('lot_size') or 75)
                break
        return instruments, expiry, lot_size

    def _find_option(
        self,
        instruments: List[Dict],
        strike: float,
        opt_type: str,
        expiry: date,
    ) -> Optional[Dict]:
        for inst in instruments:
            if (
                (inst.get('name') or '').upper() == 'NIFTY'
                and inst.get('instrument_type', '').upper() == opt_type.upper()
                and inst.get('expiry') == expiry
                and abs((inst.get('strike') or 0) - strike) < 0.5
            ):
                return inst
        return None

    def _fetch_fyers_chain_deltas(
        self, provider: Any, spot: float, t: float
    ) -> Dict:
        """Fetch Fyers option chain and compute delta for every strike using Fyers' own IV.

        Flow:
          1. Call provider.get_option_chain_raw() — one API call gets all strikes.
          2. Determine ATM IV:
               a. Use the 'iv' field from the ATM option in the chain (Fyers' own IV).
               b. If missing, derive from ATM option LTP via py_vollib.
               c. Last resort: use _FALLBACK_IV (15%).
          3. Compute delta for EVERY strike in the chain via Black-Scholes using ATM IV.
             (Flat vol surface = same approach Fyers uses for their delta display.)
          4. Return {(strike_int, 'CE'|'PE'): {delta, ltp, symbol}}.

        Returns {} only if the API call itself fails — never silently falls back to a
        constant IV in the happy path.
        """
        fn = getattr(provider, 'get_option_chain_raw', None)
        if fn is None:
            self.log.warning("Provider has no get_option_chain_raw — cannot use Fyers chain")
            return {}

        try:
            raw_chain = fn(symbol=_NIFTY_FYERS, strikecount=50) or {}
        except Exception as e:
            self.log.error(f"Fyers option chain fetch failed: {e}")
            return {}

        if not raw_chain:
            self.log.warning("Fyers chain returned 0 rows — check debug dump at /tmp/fyers_chain_debug.json")
            return {}

        # ── ATM IV from chain ─────────────────────────────────────────────
        atm = round(spot / _STRIKE_STEP) * _STRIKE_STEP
        atm_sigma: float = _FALLBACK_IV
        atm_found = False

        # Prefer CE ATM iv (CE ATM is more liquid; use PE as backup)
        for ot_try in ('CE', 'PE'):
            entry = raw_chain.get((int(atm), ot_try))
            if not entry:
                continue
            iv = entry.get('iv')
            if iv and 0 < iv <= 5.0:
                atm_sigma = iv
                atm_found = True
                self.log.info(f"ATM IV from Fyers chain: {atm_sigma:.4f} ({int(atm)}{ot_try})")
                break

        if not atm_found:
            # Try computing from LTP of ATM CE if iv not in chain
            atm_entry = raw_chain.get((int(atm), 'CE')) or raw_chain.get((int(atm), 'PE'))
            if atm_entry:
                atm_ltp = atm_entry.get('ltp') or 0
                atm_ot  = 'CE' if (int(atm), 'CE') in raw_chain else 'PE'
                atm_flag = 'c' if atm_ot == 'CE' else 'p'
                if atm_ltp and atm_ltp > 0.5:
                    try:
                        from py_vollib.black_scholes.implied_volatility import implied_volatility as _iv_fn
                        _iv = _iv_fn(atm_ltp, spot, float(atm), t, 0.07, atm_flag)
                        if _iv and 0 < _iv <= 5.0:
                            atm_sigma = _iv
                            self.log.info(f"ATM IV from LTP: {atm_sigma:.4f} (ltp={atm_ltp})")
                    except Exception:
                        pass
                    if atm_sigma == _FALLBACK_IV and t > 0:
                        # py_vollib rejects prices at/below the carry floor (common
                        # on 0DTE); Brenner–Subrahmanyam ATM approximation still
                        # yields a usable vol from the same LTP.
                        _approx = atm_ltp / (0.4 * spot * math.sqrt(t))
                        if 0 < _approx <= 5.0:
                            atm_sigma = _approx
                            self.log.info(f"ATM IV approx from LTP: {atm_sigma:.4f} (ltp={atm_ltp})")

        if atm_sigma == _FALLBACK_IV:
            self.log.warning(f"Using fallback IV {atm_sigma:.2f} — check /tmp/fyers_chain_debug.json")

        # ── Compute delta for all chain strikes ───────────────────────────
        result: Dict = {}
        for (strike, ot), entry in raw_chain.items():
            flag  = 'c' if ot == 'CE' else 'p'
            delta = _bs_delta(flag, spot, float(strike), t, 0.07, atm_sigma)
            result[(strike, ot)] = {
                'delta':  delta,
                'ltp':    entry.get('ltp'),
                'symbol': entry.get('symbol', ''),
            }

        self.log.info(
            f"Chain deltas computed: {len(result)} strikes, "
            f"atm={int(atm)} atm_sigma={atm_sigma:.4f}"
        )
        return result

    def _select_delta_strike(
        self,
        opt_type: str,
        spot: float,
        provider: Any,
        chain_deltas: Optional[Dict] = None,
    ) -> Tuple[float, Optional[Dict]]:
        """Return the strike from the Fyers option chain whose delta is closest to ±0.90,
        subject to the premium cap: if that strike's LTP exceeds _MAX_PREMIUM, the
        same-type strike priced nearest below the cap is chosen instead.

        Uses chain_deltas exclusively — no instruments list, no Black-Scholes fallback.
        The instrument dict is constructed from the chain symbol so that order placement
        and LTP queries work even if the strike is not in NSE_FO.csv.

        chain_deltas: pass the pre-fetched result from _fetch_fyers_chain_deltas() when
          you need both CE and PE (avoids double API call). Pass None to fetch fresh.
        """
        atm           = round(spot / _STRIKE_STEP) * _STRIKE_STEP
        signed_target = _DELTA_TARGET if opt_type.upper() == 'CE' else -_DELTA_TARGET

        if self._expiry is None:
            self.log.error("Expiry not set — cannot select strike")
            return atm, None

        t = _time_to_expiry_years(self._expiry)

        if chain_deltas is None:
            chain_deltas = self._fetch_fyers_chain_deltas(provider, spot, t)

        if not chain_deltas:
            self.log.error("Fyers option chain empty — cannot select delta strike")
            return atm, None

        best_strike    = atm
        best_delta_val = 0.0
        best_diff      = float('inf')
        best_symbol    = ''
        best_ltp: Optional[float] = None

        for (strike, ot), entry in chain_deltas.items():
            if ot != opt_type.upper():
                continue
            d    = entry['delta']
            diff = abs(d - signed_target)
            self.log.debug(f"{ot} {strike}: delta={d:+.4f} diff={diff:.4f}")
            if diff < best_diff:
                best_diff      = diff
                best_strike    = float(strike)
                best_delta_val = d
                best_symbol    = entry.get('symbol', '')
                best_ltp       = entry.get('ltp')

        if not best_symbol:
            self.log.error(f"No {opt_type} strikes found in chain for target={signed_target:+.2f}")
            return best_strike, None

        # ── Premium cap: if the delta strike costs more than _MAX_PREMIUM, shift to the
        # same-type strike whose LTP is nearest to the cap from below (keeps the highest
        # delta still affordable) ──────────────────────────────────────────────────────
        if best_ltp is not None and best_ltp > _MAX_PREMIUM:
            cap_gap = float('inf')
            capped: Optional[Tuple[float, float, str, float]] = None  # strike, delta, symbol, ltp
            for (strike, ot), entry in chain_deltas.items():
                if ot != opt_type.upper():
                    continue
                ltp = entry.get('ltp')
                if ltp is None or ltp <= 0 or ltp > _MAX_PREMIUM:
                    continue
                gap = _MAX_PREMIUM - ltp
                if gap < cap_gap:
                    cap_gap = gap
                    capped  = (float(strike), entry['delta'], entry.get('symbol', ''), ltp)
            if capped and capped[2]:
                self.log.info(
                    f"Premium cap: {opt_type} {int(best_strike)} ltp={best_ltp} > {_MAX_PREMIUM:.0f} "
                    f"→ shifting to {int(capped[0])} ltp={capped[3]} delta={capped[1]:+.4f}"
                )
                best_strike, best_delta_val, best_symbol, best_ltp = capped
            else:
                self.log.warning(
                    f"Premium cap: no {opt_type} strike with LTP ≤ {_MAX_PREMIUM:.0f} in chain — "
                    f"keeping delta strike {int(best_strike)} ltp={best_ltp}"
                )

        # Build inst dict from chain symbol — works for Fyers orders and LTP calls
        # For Zerodha orders, _place_order uses symbol='NIFTY'+strike+opt_type directly
        inst: Dict = {
            'instrument_token': best_symbol,
            'tradingsymbol':    '',
            'name':             'NIFTY',
            'expiry':           self._expiry,
            'strike':           int(best_strike),
            'instrument_type':  opt_type.upper(),
        }

        self.log.info(
            f"Delta strike: {opt_type} {int(best_strike)} "
            f"delta={best_delta_val:+.4f} ltp={best_ltp} sym={best_symbol} "
            f"(target={signed_target:+.2f} diff={best_diff:.4f})"
        )
        return best_strike, inst

    # ── Broker management ─────────────────────────────────────────────────────

    def _get_active_brokers(self) -> List[Tuple[int, str, Any]]:
        """Return (idx, broker_type, service) for all active+RTP-enabled brokers."""
        result: List[Tuple[int, str, Any]] = []
        for i in range(1, 11):
            if self._uvar(f'BROKER_{i}_ACTIVE', 'false').lower() != 'true':
                continue
            if self._uvar(f'BROKER_{i}_{self.cfg.broker_tag}_ACTIVE', 'false').lower() != 'true':
                continue
            broker_type = self._uvar(f'BROKER_{i}_TYPE', '').lower()
            if not broker_type:
                continue
            try:
                svc = self._init_broker_svc(i, broker_type)
                if svc:
                    result.append((i, broker_type, svc))
            except Exception as e:
                self.log.error(f"Broker {i} ({broker_type}) init failed: {e}")
        return result

    def _init_broker_svc(self, idx: int, broker_type: str) -> Optional[Any]:
        if broker_type == 'zerodha':
            from trading_app.service.provider_logic import get_kite
            from trading_app.service.kite_order_services import KiteService
            kite = get_kite(user=self.username, instance=idx)
            return KiteService(kite_instance=kite) if kite else None

        if broker_type == 'fyers':
            from trading_app.service.fyers_order_services import FyersOrderService
            app_id = self._uvar(f'BROKER_{idx}_APP_ID')
            token  = self._uvar(f'BROKER_{idx}_ACCESS_TOKEN')
            if app_id and token:
                return FyersOrderService(app_id=app_id, access_token=token)

        if broker_type == 'kotak':
            from trading_app.service.kotak_order_services import KotakOrderService
            trading_token = self._uvar(f'BROKER_{idx}_TRADING_TOKEN')
            consumer_key  = self._uvar(f'BROKER_{idx}_CONSUMER_KEY')
            trading_sid   = self._uvar(f'BROKER_{idx}_TRADING_SID')
            base_url      = self._uvar(f'BROKER_{idx}_BASE_URL') or 'https://e21.kotaksecurities.com'
            if trading_token and consumer_key and trading_sid:
                svc = KotakOrderService()
                svc.base_url         = base_url
                svc._order_base_url  = base_url
                svc.trading_token    = trading_token
                svc.trading_sid      = trading_sid
                svc.consumer_key     = consumer_key
                return svc

        if broker_type == 'dhan':
            from trading_app.service.dhan_order_services import DhanOrderService
            access_token = self._uvar(f'BROKER_{idx}_ACCESS_TOKEN')
            client_id    = self._uvar(f'BROKER_{idx}_CLIENT_ID')
            if access_token and client_id:
                return DhanOrderService(access_token=access_token, client_id=client_id)

        return None

    # ── Order placement ───────────────────────────────────────────────────────

    def _place_order(
        self,
        idx: int,
        broker_type: str,
        svc: Any,
        opt_type: str,
        strike: float,
        kite_ts: str,
        fyers_sym: str,
        quantity: int,
        transaction_type: str,
        product: str,
    ) -> Optional[str]:
        """Route a single NIFTY option order to the correct broker service."""
        try:
            if broker_type == 'zerodha':
                kite_txn = (
                    svc.kite.TRANSACTION_TYPE_BUY
                    if transaction_type == 'BUY'
                    else svc.kite.TRANSACTION_TYPE_SELL
                )
                result = svc.place_option_order(
                    symbol='NIFTY', strike=int(strike), option_type=opt_type,
                    transaction_type=kite_txn, quantity=quantity, product=product,
                )
                return str(result['order_id']) if result.get('success') else None

            if broker_type == 'kotak':
                k_txn = 'B' if transaction_type == 'BUY' else 'S'
                result = svc.place_option_order(
                    symbol='NIFTY', strike=int(strike), option_type=opt_type,
                    transaction_type=k_txn, quantity=quantity, product_type=product,
                )
                return str(result['order_id']) if result and result.get('success') else None

            if broker_type == 'dhan':
                sec_id = svc.get_option_security_id('NIFTY', int(strike), opt_type)
                if sec_id:
                    result = svc.place_order(
                        security_id=sec_id,
                        transaction_type=transaction_type,
                        quantity=quantity,
                        order_type='MARKET',
                        product_type=product,
                        exchange_segment='NSE_FNO',
                        price=0,
                    )
                    return str(result.get('order_id', '')) if result else None

            if broker_type == 'fyers':
                if fyers_sym:
                    f_side = 1 if transaction_type == 'BUY' else -1
                    result = svc.place_order(
                        symbol=fyers_sym,
                        side=f_side,
                        quantity=quantity,
                        order_type=2,
                        product_type=product,
                    )
                    return str(result.get('order_id', '')) if result else None

        except Exception as e:
            self.log.error(
                f"[{broker_type.upper()}_{idx}] {transaction_type} order error: {e}"
            )
        return None

    # ── Delta-strike query (used by the UI "View Strikes" button) ────────────

    def get_delta_strikes(self, provider: Any) -> Dict[str, Any]:
        """Return the CE and PE strikes closest to ±0.90 delta using Fyers option chain.
        Response: {success, spot, expiry, CE: {strike, delta, ltp}, PE: {strike, delta, ltp}}
        """
        # ── Current spot ──────────────────────────────────────────────────────
        spot: Optional[float] = None
        try:
            data = provider.ltp([_NIFTY_FYERS])
            raw  = data.get(_NIFTY_FYERS, {}).get('last_price', 0)
            spot = float(raw) if raw else None
        except Exception as _e:
            self.log.warning(f"get_delta_strikes spot fetch: {_e}")

        if not spot:
            return {'success': False, 'error': 'Cannot fetch NIFTY spot price'}

        # ── Expiry (needed for T) ─────────────────────────────────────────────
        if self._expiry is None:
            try:
                self._instruments, self._expiry, self._lot_size = \
                    self._get_instruments_and_expiry(provider)
            except Exception as _e:
                return {'success': False, 'error': f'Cannot determine expiry: {_e}'}

        t = _time_to_expiry_years(self._expiry)

        # ── Fyers option chain: one call for CE + PE ──────────────────────────
        chain_deltas = self._fetch_fyers_chain_deltas(provider, spot, t)
        if not chain_deltas:
            return {'success': False, 'error': 'Fyers option chain unavailable — check /tmp/fyers_chain_debug.json'}

        result: Dict[str, Any] = {
            'success': True,
            'spot':    round(spot, 2),
            'expiry':  str(self._expiry),
        }

        for opt_type in ('CE', 'PE'):
            strike, inst = self._select_delta_strike(opt_type, spot, provider, chain_deltas)
            key = (int(strike), opt_type)
            entry     = chain_deltas.get(key) or {}
            result[opt_type] = {
                'strike': int(strike),
                'delta':  round(entry['delta'], 3) if entry.get('delta') is not None else None,
                'ltp':    entry.get('ltp'),
            }

        return result

    # ── Trade lifecycle ───────────────────────────────────────────────────────

    def _enter_trade(self, direction: str, spot: float, provider: Any) -> None:
        """Select delta strike, place BUY orders on all cached brokers, save state."""
        opt_type = 'CE' if direction == 'BUY' else 'PE'
        t = _time_to_expiry_years(self._expiry) if self._expiry else 0.01
        chain_deltas = self._fetch_fyers_chain_deltas(provider, spot, t)
        strike, inst = self._select_delta_strike(opt_type, spot, provider, chain_deltas)

        if inst is None:
            self.log.error(f"No delta strike found for {opt_type} near spot={spot}")
            return

        sl_level  = round(spot - self.cfg.sl_points,  1) if direction == 'BUY' else round(spot + self.cfg.sl_points,  1)
        tgt_level = round(spot + self.cfg.tgt_points, 1) if direction == 'BUY' else round(spot - self.cfg.tgt_points, 1)
        fyers_sym = inst.get('instrument_token', '')
        kite_ts   = inst.get('tradingsymbol', '')

        broker_entries: List[Dict] = []
        for idx, (broker_type, svc) in self._broker_map.items():
            lots     = max(1, int(self._uvar(f'BROKER_{idx}_{self.cfg.broker_tag}_LOTS', '1') or '1'))
            quantity = lots * self._lot_size
            product  = self._uvar(f'BROKER_{idx}_PRODUCT_TYPE', 'NRML').upper()
            order_id = self._place_order(
                idx, broker_type, svc, opt_type, strike,
                kite_ts, fyers_sym, quantity, 'BUY', product,
            )
            entry_success = bool(order_id)
            broker_entries.append({
                'broker_idx':    idx,
                'broker_type':   broker_type,
                'order_id':      str(order_id or ''),
                'entry_success': entry_success,
                'tradingsymbol': kite_ts,
                'fyers_sym':     fyers_sym,
                'lots':          lots,
                'quantity':      quantity,
            })
            if entry_success:
                self.log.info(
                    f"[{broker_type.upper()}_{idx}] BUY {opt_type} {int(strike)}"
                    f" qty={quantity} ({lots} lot(s)) order_id={order_id}"
                )
            else:
                self.log.error(
                    f"[{broker_type.upper()}_{idx}] BUY {opt_type} {int(strike)} FAILED"
                    f" — no exit will be placed for this broker"
                )

        # Capture option LTP just after order placement as proxy for fill price
        opt_entry_price: Optional[float] = None
        try:
            ltp_data = provider.ltp([fyers_sym])
            raw = ltp_data.get(fyers_sym, {}).get('last_price', 0)
            opt_entry_price = round(float(raw), 2) if raw else None
        except Exception as _e:
            self.log.warning(f"Could not fetch option entry LTP: {_e}")

        state = self._load_state()
        state['active_trade'] = {
            'direction':       direction,
            'entry_spot':      spot,
            'entry_time':      datetime.now().isoformat(),
            'sl_level':        sl_level,
            'target_level':    tgt_level,
            'strike':          int(strike),
            'option_type':     opt_type,
            'lot_size':        self._lot_size,
            'expiry':          str(self._expiry),
            'broker_entries':  broker_entries,
            'opt_entry_price': opt_entry_price,
        }
        self._save_state(state)
        self.log.info(
            f"ENTERED {direction}: spot={spot} {int(strike)}{opt_type}"
            f" opt_ltp={opt_entry_price}"
            f" sl={sl_level} tgt={tgt_level} expiry={self._expiry}"
            f" brokers={len(broker_entries)}"
        )

    def _exit_trade(self, reason: str, spot: float, square_off: bool = True) -> None:
        """Clear the active trade in state and (by default) square off on all brokers.

        square_off=False skips broker SELL orders — used when the position is
        already flat at the broker (closed manually/externally) so we only need to
        reconcile local state without firing a stray naked SELL.
        """
        state = self._load_state()
        trade = state.get('active_trade')
        if not trade:
            return

        opt_type = trade['option_type']
        strike   = trade['strike']

        # Capture option LTP before exit orders for option P&L calculation
        opt_exit_price: Optional[float] = None
        try:
            provider = getattr(self, '_provider', None)
            if provider:
                fyers_sym = trade.get('broker_entries', [{}])[0].get('fyers_sym', '') if trade.get('broker_entries') else ''
                if fyers_sym:
                    ltp_data = provider.ltp([fyers_sym])
                    raw = ltp_data.get(fyers_sym, {}).get('last_price', 0)
                    opt_exit_price = round(float(raw), 2) if raw else None
        except Exception as _e:
            self.log.warning(f"Could not fetch option exit LTP: {_e}")

        for entry in ([] if not square_off else trade.get('broker_entries', [])):
            idx         = entry['broker_idx']
            broker_type = entry.get('broker_type', 'zerodha')
            kite_ts     = entry.get('tradingsymbol', '')
            fyers_sym   = entry.get('fyers_sym', '')
            # Use stored per-broker quantity; fall back to lot_size from trade (not hardcoded 75)
            quantity    = entry.get('quantity', int(trade.get('lot_size', 75)))
            # Broker-specific guard: only square off where the entry BUY actually
            # succeeded on this broker. Fall back to order_id for older state files
            # written before entry_success was tracked.
            entry_ok = entry.get('entry_success', bool(entry.get('order_id')))
            if not entry_ok:
                self.log.warning(
                    f"[{broker_type.upper()}_{idx}] skipping exit — entry never succeeded"
                )
                continue
            try:
                # Prefer cached service; re-init as fallback if broker wasn't in map at start
                cached = self._broker_map.get(idx)
                if cached:
                    _, svc = cached
                else:
                    svc = self._init_broker_svc(idx, broker_type)
                if svc:
                    product  = self._uvar(f'BROKER_{idx}_PRODUCT_TYPE', 'NRML').upper()
                    order_id = self._place_order(
                        idx, broker_type, svc, opt_type, strike,
                        kite_ts, fyers_sym, quantity, 'SELL', product,
                    )
                    self.log.info(
                        f"[{broker_type.upper()}_{idx}] SELL {opt_type} {strike}"
                        f" qty={quantity} order_id={order_id}"
                    )
            except Exception as e:
                self.log.error(f"Exit order failed broker {idx}: {e}")

        state['active_trade'] = None
        state['last_exit'] = {
            'reason': reason,
            'spot':   spot,
            'time':   datetime.now().isoformat(),
        }
        self._append_history(trade, spot, reason, opt_exit_price=opt_exit_price)
        self._save_state(state)
        self._spot_fail_count = 0
        self.log.info(
            f"EXITED ({reason}): spot={spot} opt_exit_ltp={opt_exit_price}"
            f"{'' if square_off else ' (no square-off — already flat at broker)'}"
        )

    # ── Broker-position reconciliation ────────────────────────────────────────

    def _broker_net_qty(
        self, idx: int, broker_type: str, svc: Any, strike: Any, opt_type: str
    ) -> Optional[int]:
        """Net position quantity for one NIFTY strike+type on a single broker.

        Returns 0 when the broker holds nothing for this strike (position closed),
        a non-zero qty when it still holds it, or None when the position could not
        be read (auth/API error). The caller MUST treat None as 'unknown' and never
        as 'flat', so a transient query failure can never clear a live trade.
        """
        strike_s = str(int(strike))
        ot       = opt_type.upper()
        try:
            if broker_type == 'zerodha':
                kite = getattr(svc, 'kite', None)
                if kite is None:
                    return None
                nets = (kite.positions() or {}).get('net', []) or []
                total, matched = 0, False
                for p in nets:
                    ts = str(p.get('tradingsymbol', '')).upper()
                    if 'NIFTY' in ts and strike_s in ts and ts.endswith(ot):
                        total  += int(p.get('quantity', 0) or 0)
                        matched = True
                return total if matched else 0

            # fyers / dhan / kotak all expose get_positions() -> {success, positions:[...]}
            getter = getattr(svc, 'get_positions', None)
            if getter is None:
                return None
            resp = getter() or {}
            if not resp.get('success'):
                return None
            total, matched = 0, False
            for p in resp.get('positions', []) or []:
                if not isinstance(p, dict):
                    continue
                sym = ''
                for k in ('symbol', 'tradingSymbol', 'trdSym', 'tradingsymbol',
                          'sym', 'displaySymbol'):
                    if p.get(k):
                        sym = str(p[k]).upper()
                        break
                if not ('NIFTY' in sym and strike_s in sym and ot in sym):
                    continue
                for qk in ('netQty', 'net_quantity', 'quantity', 'netqty', 'netTrdQty'):
                    if p.get(qk) is not None:
                        try:
                            total  += int(float(p[qk]))
                            matched = True
                        except (TypeError, ValueError):
                            pass
                        break
            return total if matched else 0
        except Exception as e:
            self.log.warning(f"[{broker_type.upper()}_{idx}] position query failed: {e}")
            return None

    def _is_position_flat(self, trade: Dict[str, Any]) -> Optional[bool]:
        """Reconcile the active trade against real broker positions.

        Returns True  → every entered broker reports a flat (0) net position,
                False → at least one broker still holds the position,
                None  → could not determine (query error / no readable broker).
        None is returned if ANY entered broker can't be read, so we never conclude
        'flat' — and clear a live trade — on a partial or failed reconciliation.
        """
        strike   = trade.get('strike')
        opt_type = trade.get('option_type', '')
        if strike is None or not opt_type:
            return None

        readings: List[Optional[int]] = []
        for entry in trade.get('broker_entries', []):
            if not entry.get('entry_success', bool(entry.get('order_id'))):
                continue
            idx = entry.get('broker_idx')
            bt  = entry.get('broker_type', 'zerodha')
            cached = self._broker_map.get(idx)
            svc    = cached[1] if cached else None
            if svc is None:
                try:
                    svc = self._init_broker_svc(idx, bt)
                except Exception:
                    svc = None
            if svc is None:
                readings.append(None)
                continue
            readings.append(self._broker_net_qty(idx, bt, svc, strike, opt_type))

        if not readings:
            return None
        if any(r is None for r in readings):
            return None                       # unknown — never clear on a failed read
        if any((r or 0) != 0 for r in readings):
            return False                      # still holding somewhere
        return True                           # every entered broker flat

    # ── needs_reset replay ────────────────────────────────────────────────────

    def _replay_today_needs_reset(self, provider: Any) -> Tuple[bool, bool]:
        """Re-derive buy/sell_needs_reset by replaying today's bars, skipping bars
        that fall inside already-completed trades. Mirrors the backtest's bar-by-bar
        tracking so mid-day restarts don't get stuck with a stale True flag.
        """
        try:
            df = self._fetch_1min_candles(provider)
            if df is None or df.empty:
                return False, False

            from trading_app.Backtest.rtp_backtest_engine import RTPBacktestEngine
            engine = RTPBacktestEngine(
                df=df,
                entry_mode='RTP(20 & 9)',
                interval_minutes=1,
                slope_bars=_SLOPE_BARS,
                use_adx=self.cfg.use_adx,
                adx_thresh=self.cfg.adx_thresh,
                sl_points=self.cfg.sl_points,
                tgt_points=self.cfg.tgt_points,
            )
            processed = engine.df

            today = date.today()
            today_bars = processed[processed['datetime'].dt.date == today].copy()
            if today_bars.empty:
                return False, False

            # Build trade-period exclusion list from today's closed trade history
            trade_periods: List[Tuple] = []
            try:
                with open(self.cfg.history_file, 'r') as _f:
                    history = json.load(_f)
                today_str = today.isoformat()
                _IST = 'Asia/Kolkata'
                for t in history:
                    if t.get('date') != today_str:
                        continue
                    try:
                        _e = pd.Timestamp(t['entry_time'])
                        _x = pd.Timestamp(t['exit_time'])
                        # Localize to IST so comparison with IST-aware bar_dt works
                        e = _e.tz_localize(_IST) if _e.tzinfo is None else _e.tz_convert(_IST)
                        x = _x.tz_localize(_IST) if _x.tzinfo is None else _x.tz_convert(_IST)
                        trade_periods.append((e, x))
                    except Exception:
                        pass
            except Exception:
                pass

            buy_needs_reset  = False
            sell_needs_reset = False

            # Use the same completed-bar cutoff as _check_rtp_signal:
            # a bar is completed only once open_time + bar duration <= now.
            # Then further exclude the LAST completed bar so _check_rtp_signal
            # can process it fresh — if we replay the signal bar and set
            # buy/sell_needs_reset=True here, _check_rtp_signal won't fire on it.
            now_ist    = pd.Timestamp.now(tz='Asia/Kolkata')
            completed  = today_bars[today_bars['datetime'] + self._bar_td <= now_ist]
            replay_bars = completed.iloc[:-1] if len(completed) > 1 else completed.iloc[:0]

            for _, row in replay_bars.iterrows():
                bar_dt = row['datetime']

                # Needs-reset clears on ALL bars (the backtest's sequential loop
                # does not gate this on session_ok, so neither should we here).
                if sell_needs_reset and row['high'] < min(row['ema9'], row['ema20']):
                    sell_needs_reset = False
                if buy_needs_reset and row['low'] > max(row['ema9'], row['ema20']):
                    buy_needs_reset = False

                if not row.get('session_ok', False):
                    continue  # skip signal check; reset updates above still ran

                # Skip bars that occurred inside a past trade (mirrors backtest skip)
                if any(e <= bar_dt <= x for e, x in trade_periods):
                    continue

                buy_sig  = bool(row['rway_up'] and row['bull_stack'] and
                                row['buy_touch'] and row['buy_pat'] and not buy_needs_reset)
                sell_sig = bool(row['rway_dn'] and row['bear_stack'] and
                                row['sell_touch'] and row['sell_pat'] and not sell_needs_reset)
                if buy_sig:
                    buy_needs_reset = True
                if sell_sig:
                    sell_needs_reset = True

            self.log.info(
                f"needs_reset replayed — buy={buy_needs_reset} sell={sell_needs_reset}"
                f" ({len(replay_bars)} bars replayed, {len(trade_periods)} past trade(s) excluded)"
            )
            return buy_needs_reset, sell_needs_reset

        except Exception as e:
            self.log.error(f"needs_reset replay error: {e}", exc_info=True)
            return False, False

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        self.log.info("Monitor loop started — waiting for data provider")

        # ── Wait for provider (token may not be available immediately after restart) ──
        provider = None
        while not self._stop_event.is_set():
            try:
                provider = self._get_provider()
                if provider:
                    break
            except Exception as _e:
                self.log.warning(f"Provider init error: {_e}")
            self.log.info("Data provider not ready — retrying in 30s")
            self._stop_event.wait(30)

        if self._stop_event.is_set() or provider is None:
            self.log.info("Monitor loop stopped before provider became available")
            return

        self._provider = provider  # stored so _exit_trade can fetch option LTP
        self.log.info("Data provider ready")

        # ── Day-start / mid-day-restart initialisation ────────────────────────

        state = self._load_state()

        # Discard any active trade left over from a previous trading day
        # (server crash during live trade leaves stale state; monitoring it
        # tomorrow would use yesterday's SL/Target levels on today's prices)
        _today_str = date.today().isoformat()
        _stale     = state.get('active_trade')
        if _stale and not str(_stale.get('entry_time', '')).startswith(_today_str):
            self.log.warning("Stale active trade from previous day — clearing state")
            state = {'active_trade': None, 'buy_needs_reset': False, 'sell_needs_reset': False}
            self._save_state(state)

        if not state.get('active_trade'):
            now = datetime.now()
            if now.hour < 9 or (now.hour == 9 and now.minute < 20):
                # True day start: clear both flags unconditionally
                state['buy_needs_reset']  = False
                state['sell_needs_reset'] = False
                self._save_state(state)
                self.log.info("buy/sell_needs_reset cleared for new trading day")
            else:
                # Mid-day restart (watchdog / server recovery):
                # replay today's bars so we don't lose a reset that happened
                # while the thread was down, and don't falsely clear a live flag.
                buy_nr, sell_nr = self._replay_today_needs_reset(provider)
                state['buy_needs_reset']  = buy_nr
                state['sell_needs_reset'] = sell_nr
                self._save_state(state)
                self.log.info(
                    f"Mid-day restart — replayed needs_reset:"
                    f" buy={buy_nr} sell={sell_nr}"
                )

        # Cache broker services once — avoids repeated session re-initialisation per trade
        self.log.info("Initialising broker services...")
        self._broker_map = {
            idx: (btype, svc)
            for idx, btype, svc in self._get_active_brokers()
        }
        if self._broker_map:
            self.log.info(f"Active RTP brokers: {list(self._broker_map.keys())}")
        else:
            self.log.warning("No active RTP brokers — signals will be detected but no orders placed")

        # ── Wait for NFO instruments (may need a live market session) ─────────
        self.log.info("Fetching NFO instruments...")
        while not self._stop_event.is_set():
            try:
                self._instruments, self._expiry, self._lot_size = \
                    self._get_instruments_and_expiry(provider)
                self.log.info(
                    f"Instruments ready — expiry={self._expiry}"
                    f" lot_size={self._lot_size} count={len(self._instruments)}"
                )
                break
            except Exception as e:
                self.log.warning(f"Instrument fetch failed: {e} — retrying in 60s")
                self._stop_event.wait(60)

        if self._stop_event.is_set():
            self.log.info("Monitor loop stopped during instrument fetch")
            return

        # last_signal_minute: throttles the candle fetch to once per minute.
        # last_checked_bar_dt: tracks the datetime of the last bar we checked for
        #   a signal. On every fetch we process ALL bars after this pointer (not
        #   just the single last bar). This handles the Fyers historical-data API
        #   delay: a 1-2 min late bar would no longer be completed.iloc[-1] by
        #   the time we notice it, and would be missed by a last-bar-only scan.
        #   After a trade exits, we reset this to the current IST minute so that
        #   bars during the trade period are not re-evaluated as new signals.
        last_signal_minute:      Optional[int]            = None
        last_checked_bar_dt:     Optional[pd.Timestamp]  = None
        last_exit_candle_minute: int                     = -1
        last_exit_candle_dt:     Optional[pd.Timestamp]  = None
        tracked_trade_entry_time: Optional[str]          = None
        last_reconcile_ts:       float                   = 0.0

        while not self._stop_event.is_set():
            try:
                now = datetime.now()
                h, m, s = now.hour, now.minute, now.second

                # ── Before trading session: wait
                if (h < 9) or (h == 9 and m < 20):
                    time.sleep(1)
                    continue

                # ── Past EOD cutoff: exit any open trade and stop
                if (h > 15) or (h == 15 and m >= 28):
                    state = self._load_state()
                    if state.get('active_trade'):
                        spot = None
                        for _retry in range(5):
                            spot = self._get_nifty_spot(provider)
                            if spot:
                                break
                            if _retry < 4:
                                time.sleep(0.5)
                        # Don't square off a position that's already flat at the broker
                        _flat = self._is_position_flat(state['active_trade'])
                        self._exit_trade('EOD', spot or 0.0, square_off=(_flat is not True))
                    self.log.info("EOD cutoff reached — monitor loop ending")
                    break

                state = self._load_state()

                # ── In trade → check SL / Target every second regardless of kill-switch.
                # The kill-switch only prevents new entries; it must never block an exit.
                if state.get('active_trade'):
                    # Reset candle exit pointer when a new trade starts
                    _entry_now = str(state['active_trade'].get('entry_time', ''))
                    if _entry_now != tracked_trade_entry_time:
                        tracked_trade_entry_time = _entry_now
                        last_exit_candle_dt      = None
                        last_exit_candle_minute  = -1
                        # Grace period: give the broker fill time to appear before
                        # the first reconciliation so we don't false-flag a fresh trade.
                        last_reconcile_ts        = time.time()

                    spot = self._get_nifty_spot(provider)
                    if spot:
                        self._spot_fail_count = 0
                        trade     = state['active_trade']
                        direction = trade['direction']
                        sl_level  = trade['sl_level']
                        tgt_level = trade['target_level']

                        if direction == 'BUY':
                            if spot <= sl_level:
                                self._exit_trade('SL', spot)
                                # Advance bar pointer to now so post-trade signal
                                # check starts fresh (not from pre-entry bars).
                                last_checked_bar_dt = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')
                            elif spot >= tgt_level:
                                self._exit_trade('TARGET', spot)
                                last_checked_bar_dt = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')
                        else:  # SELL direction → PE bought
                            if spot >= sl_level:
                                self._exit_trade('SL', spot)
                                last_checked_bar_dt = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')
                            elif spot <= tgt_level:
                                self._exit_trade('TARGET', spot)
                                last_checked_bar_dt = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')
                    else:
                        self._spot_fail_count += 1
                        if self._spot_fail_count >= _MAX_SPOT_FAILS:
                            self.log.critical(
                                f"Spot fetch failed {self._spot_fail_count} consecutive times"
                                " — open trade UNMONITORED. Check provider connection."
                            )

                    # ── Per-minute candle H/L exit check ─────────────────────────────
                    # The 1-second spot poll can miss a momentary SL/Target touch if
                    # the price dips and recovers between polls.  Checking the completed
                    # 1-min candle's high/low once per minute mirrors the backtest
                    # exactly (backtest exits on c['low'] <= SL or c['high'] >= Target).
                    if m != last_exit_candle_minute:
                        last_exit_candle_minute = m
                        # Re-read state: the spot check above may have already exited.
                        _cs = self._load_state()
                        if _cs.get('active_trade'):
                            try:
                                _tdf = self._fetch_today_1min_candles(provider)
                                if _tdf is not None and not _tdf.empty:
                                    _now_ist  = pd.Timestamp.now(tz='Asia/Kolkata')
                                    _completed = _tdf[_tdf['datetime'] + self._bar_td <= _now_ist]
                                    if not _completed.empty:
                                        if last_exit_candle_dt is not None:
                                            _to_check = _completed[
                                                _completed['datetime'] > last_exit_candle_dt
                                            ]
                                        else:
                                            # First check — scan from trade entry time
                                            _ets = pd.Timestamp(tracked_trade_entry_time)
                                            if _ets.tzinfo is None:
                                                _ets = _ets.tz_localize('Asia/Kolkata')
                                            else:
                                                _ets = _ets.tz_convert('Asia/Kolkata')
                                            _to_check = _completed[
                                                _completed['datetime'] >= _ets.floor(self._bar_td)
                                            ]

                                        _at = _cs['active_trade']
                                        _dir = _at['direction']
                                        _sl  = float(_at['sl_level'])
                                        _tgt = float(_at['target_level'])

                                        for _, _c in _to_check.iterrows():
                                            last_exit_candle_dt = _c['datetime']
                                            # Guard: spot check may have exited mid-loop
                                            if not self._load_state().get('active_trade'):
                                                break
                                            if _dir == 'BUY':
                                                if _c['low'] <= _sl:
                                                    self.log.info(
                                                        f"Candle exit BUY SL: "
                                                        f"{_c['datetime']} low={_c['low']} <= sl={_sl}"
                                                    )
                                                    self._exit_trade('SL', _sl)
                                                    last_checked_bar_dt = _now_ist
                                                    break
                                                if _c['high'] >= _tgt:
                                                    self.log.info(
                                                        f"Candle exit BUY TARGET: "
                                                        f"{_c['datetime']} high={_c['high']} >= tgt={_tgt}"
                                                    )
                                                    self._exit_trade('TARGET', _tgt)
                                                    last_checked_bar_dt = _now_ist
                                                    break
                                            else:
                                                if _c['high'] >= _sl:
                                                    self.log.info(
                                                        f"Candle exit SELL SL: "
                                                        f"{_c['datetime']} high={_c['high']} >= sl={_sl}"
                                                    )
                                                    self._exit_trade('SL', _sl)
                                                    last_checked_bar_dt = _now_ist
                                                    break
                                                if _c['low'] <= _tgt:
                                                    self.log.info(
                                                        f"Candle exit SELL TARGET: "
                                                        f"{_c['datetime']} low={_c['low']} <= tgt={_tgt}"
                                                    )
                                                    self._exit_trade('TARGET', _tgt)
                                                    last_checked_bar_dt = _now_ist
                                                    break
                            except Exception as _ce:
                                self.log.warning(f"Candle exit check error: {_ce}")

                    # ── Broker-position reconciliation ───────────────────────────────
                    # SL/Target/candle checks only close a position the algo still
                    # believes it holds. If the position is closed by any other path
                    # (manual square-off, broker auto-close, an SL order the algo did
                    # not place, or a SELL whose state-write failed), nothing above
                    # would ever clear active_trade: the trade shows 'active' forever,
                    # blocks re-entries, and fires a stray SELL at EOD. Poll the real
                    # broker net qty periodically; if every entered broker is flat,
                    # close the trade locally without squaring off again.
                    if (time.time() - last_reconcile_ts) >= _RECONCILE_SECS:
                        last_reconcile_ts = time.time()
                        _rs = self._load_state()
                        _rt = _rs.get('active_trade')
                        if _rt and self._is_position_flat(_rt) is True:
                            self.log.warning(
                                "Broker position flat — trade closed outside the algo."
                                " Clearing active_trade (no square-off)."
                            )
                            _spot_now = self._get_nifty_spot(provider) or 0.0
                            self._exit_trade('BROKER_CLOSED', _spot_now, square_off=False)
                            last_checked_bar_dt = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')

                else:
                    # ── Runtime kill-switch — only blocks new signal entries
                    if self._uvar(self.cfg.env_active, 'false').lower() != 'true':
                        time.sleep(5)
                        continue

                    # ── No trade → check for signal once per new minute.
                    # Uses m != last_signal_minute (not s == 0) so that a slow
                    # network call at second 0 never causes the whole minute to be
                    # skipped.  Within the check we scan ALL bars since
                    # last_checked_bar_dt, not just the newest one.
                    if m != last_signal_minute:
                        last_signal_minute = m
                        df = self._fetch_1min_candles(provider)
                        if df is not None and len(df) >= _WARMUP_BARS:
                            signal, state, last_checked_bar_dt, sig_bar_close = \
                                self._check_rtp_signal(df, state, last_checked_bar_dt)
                            self._save_state(state)

                            if signal:
                                spot = None
                                for _retry in range(3):
                                    spot = self._get_nifty_spot(provider)
                                    if spot:
                                        break
                                    if _retry < 2:
                                        time.sleep(0.5)
                                if spot:
                                    # Use the signal bar's CLOSE as the SL/Target reference
                                    # so that live levels match the backtest exactly.
                                    # The backtest enters at bar close; using real-time spot
                                    # instead shifts the SL level and causes exit timing to
                                    # diverge (the 9:23 trade stayed open until 10:40 instead
                                    # of 9:37 because the 2.4pt entry difference moved the SL).
                                    # Fallback to current spot if bar close is unavailable.
                                    entry_ref = sig_bar_close if sig_bar_close else spot
                                    self.log.info(
                                        f"Entry ref: bar_close={sig_bar_close} "
                                        f"live_spot={spot} using={entry_ref}"
                                    )
                                    self._enter_trade(signal, entry_ref, provider)
                                else:
                                    self.log.error(
                                        f"Signal {signal} missed — spot unavailable"
                                        " after 3 retries"
                                    )
                        else:
                            self.log.info(
                                f"Insufficient candle data"
                                f" ({len(df) if df is not None else 0}/{_WARMUP_BARS} bars)"
                                " — skipping signal check"
                            )

            except Exception as e:
                self.log.error(f"Monitor error: {e}", exc_info=True)

            time.sleep(1)

        self.log.info("Monitor loop ended")
