"""
2nd 30-Sec Candle Live Algo Trader
Mirrors the EMA RTP Railway Track algo's shape (background thread, multi-broker
delta-~0.90 NIFTY option execution, state/history JSON), but the signal is the
2nd 30-Sec Candle breakout strategy (see Backtest/second_candle_engine.py).

Logic (NIFTY 30-second candles, ONE trade per day):
  • Read the Nth 30-sec candle of the day (default N=2) → its High/Low = the range.
  • First breakout above range High → BUY → buy CE (delta ~0.90).
  • First breakout below range Low  → SELL → buy PE (delta ~0.90).
  • SL = opposite end of the range candle; Target = entry ± rr × risk.
  • Exit on SL / Target / cut-off time / EOD. No re-entry after the first trade.

Params are editable from the UI and stored in sc_state.json['params'].

Env vars required in Mine.env:
  SC_ALGO_ACTIVE=true
  BROKER_N_SC_ACTIVE=true/false   (alongside BROKER_N_ACTIVE)
  BROKER_N_SC_LOTS=1              (per-broker lot count)
"""
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from trading_app.service.greeks_calculator import GreeksCalculator

logger = logging.getLogger(__name__)

_STATE_FILE       = os.path.join(os.path.dirname(__file__), 'sc_state.json')
_HISTORY_FILE     = os.path.join(os.path.dirname(__file__), 'sc_trades_history.json')
_ALL_HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'sc_trades_all_history.json')

_SINGLE_LOT_QTY = 65     # NIFTY single lot — used for Opt P&L display
_DELTA_TARGET   = 0.90
_STRIKE_STEP    = 50.0
_NIFTY_FYERS    = 'NSE:NIFTY50-INDEX'
_MAX_SPOT_FAILS = 60   # consecutive None returns before CRITICAL log (~1 minute of data gap)
_FALLBACK_IV    = 0.15  # assumed annual IV when ATM IV cannot be computed (NIFTY typical 13–18%)
_BREAKOUT_SCAN_SECS = 15  # cadence of the authoritative completed-candle first-breakout scan

# Defaults match the 2nd 30-Sec Candle backtest (second_candle_engine.py)
DEFAULT_PARAMS: Dict[str, Any] = {
    'candle_index': 2,
    'rr_ratio':     3.0,
    'exit_hour':    15,
    'exit_minute':  25,
    'enable_long':  True,
    'enable_short': True,
}


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


def normalise_params(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Coerce/validate a params dict into the canonical shape, filling defaults."""
    raw = raw or {}
    p = dict(DEFAULT_PARAMS)
    try:
        if 'candle_index' in raw:
            p['candle_index'] = max(1, int(raw['candle_index']))
        if 'rr_ratio' in raw:
            p['rr_ratio'] = max(0.1, float(raw['rr_ratio']))
        if 'exit_hour' in raw:
            p['exit_hour'] = min(23, max(0, int(raw['exit_hour'])))
        if 'exit_minute' in raw:
            p['exit_minute'] = min(59, max(0, int(raw['exit_minute'])))
        if 'enable_long' in raw:
            p['enable_long'] = bool(raw['enable_long'])
        if 'enable_short' in raw:
            p['enable_short'] = bool(raw['enable_short'])
    except (TypeError, ValueError):
        pass
    # At least one direction must stay enabled
    if not p['enable_long'] and not p['enable_short']:
        p['enable_long'] = True
        p['enable_short'] = True
    return p


# Module-level registry so the API can reach the running instance
_instances: Dict[str, 'SecondCandleAlgo'] = {}


def get_instance(username: str) -> Optional['SecondCandleAlgo']:
    return _instances.get(username)


class SecondCandleAlgo:
    """Live 2nd 30-Sec Candle breakout detector and option order executor."""

    def __init__(self, username: str):
        self.username = username
        self._stop_event   = threading.Event()
        self._state_lock   = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        # Populated once at monitor-loop start; reused all day
        self._broker_map: Dict[int, Tuple[str, Any]] = {}  # idx -> (broker_type, svc)
        self._instruments: List[Dict] = []
        self._expiry: Optional[date] = None
        self._lot_size: int = 75
        self._spot_fail_count: int = 0
        # Range candle (2nd 30-sec candle) High/Low, cached once per day so that
        # live entries can react to the spot tick-by-tick instead of waiting for
        # the next watch bar to close.
        self._range_high: Optional[float] = None
        self._range_low:  Optional[float] = None
        self._range_date: Optional[str]   = None
        # Throttle for the authoritative completed-candle breakout scan
        self._last_breakout_scan: Optional[pd.Timestamp] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name='second-candle-algo-monitor',
        )
        self._thread.start()
        _instances[self.username] = self
        logger.info("[SC] Monitoring thread started")

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop(self.username, None)
        logger.info("[SC] Stop requested")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── State ─────────────────────────────────────────────────────────────────

    def _default_state(self) -> Dict[str, Any]:
        return {
            'active_trade': None,
            'traded_today': False,
            'trade_date':   date.today().isoformat(),
            'params':       dict(DEFAULT_PARAMS),
        }

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(_STATE_FILE, 'r') as f:
                state = json.load(f)
            if not isinstance(state, dict):
                return self._default_state()
            state.setdefault('active_trade', None)
            state.setdefault('traded_today', False)
            state.setdefault('trade_date', date.today().isoformat())
            state['params'] = normalise_params(state.get('params'))
            return state
        except Exception:
            return self._default_state()

    def _save_state(self, state: Dict[str, Any]) -> None:
        with self._state_lock:
            try:
                with open(_STATE_FILE, 'w') as f:
                    json.dump(state, f, indent=2, default=str)
            except Exception as e:
                logger.error(f"[SC] State save failed: {e}")

    def _params(self) -> Dict[str, Any]:
        return normalise_params(self._load_state().get('params'))

    def _append_history(self, trade: Dict[str, Any], exit_spot: float, reason: str,
                        opt_exit_price: Optional[float] = None) -> None:
        """Append a completed trade record to sc_trades_history.json (today only, latest-first)."""
        try:
            today = date.today().isoformat()
            try:
                with open(_HISTORY_FILE, 'r') as f:
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
                'sl_level':         trade.get('sl_level'),
                'target_level':     trade.get('target_level'),
                'opt_entry_price':  opt_entry_price,
                'opt_exit_price':   opt_exit_price,
                'opt_pnl_pts':      opt_pnl_pts,
                'opt_pnl_inr':      opt_pnl_inr,
                'broker_entries':   trade.get('broker_entries', []),
            }
            history.insert(0, record)  # latest-first

            with open(_HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=2, default=str)

            # Append to permanent all-time history (no day rotation)
            try:
                try:
                    with open(_ALL_HISTORY_FILE, 'r') as f:
                        all_history: list = json.load(f)
                    if not isinstance(all_history, list):
                        all_history = []
                except Exception:
                    all_history = []
                all_history.insert(0, record)
                with open(_ALL_HISTORY_FILE, 'w') as f:
                    json.dump(all_history, f, indent=2, default=str)
            except Exception as _ae:
                logger.error(f"[SC] All-history append failed: {_ae}")
        except Exception as e:
            logger.error(f"[SC] History append failed: {e}")

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
            logger.warning(f"[SC] Spot fetch failed: {e}")
            return None

    def _fetch_today_30s_candles(self, provider: Any) -> Optional[pd.DataFrame]:
        """Fetch today's 30-second candles — used for both breakout entry and H/L exit checks."""
        try:
            today = date.today().strftime('%Y-%m-%d')
            candles = provider.historical_data(
                instrument_token=_NIFTY_FYERS,
                from_date=today,
                to_date=today,
                interval='30second',
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
            df = df.sort_values('datetime').reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"[SC] 30s candle fetch failed: {e}")
            return None

    # ── Signal detection (2nd 30-Sec Candle breakout) ─────────────────────────

    def _check_breakout(
        self, df: pd.DataFrame, params: Dict[str, Any],
        now_ist: Optional[pd.Timestamp] = None,
    ) -> Optional[Dict[str, Any]]:
        """Find the first breakout of the range candle among today's COMPLETED 30-sec bars.

        Mirrors Backtest/second_candle_engine._run_day level math:
          BUY:  entry = open if open > range_high else range_high; SL = range_low
          SELL: entry = open if open < range_low  else range_low;  SL = range_high
          Target = entry ± rr × risk
        Returns {direction, entry_ref, sl_level, target_level, entry_bar} or None.
        """
        if df is None or df.empty:
            return None

        candle_index = int(params['candle_index'])
        rr           = float(params['rr_ratio'])
        enable_long  = bool(params['enable_long'])
        enable_short = bool(params['enable_short'])
        cutoff       = int(params['exit_hour']) * 60 + int(params['exit_minute'])

        # Only act on completed 30-sec candles (a bar closes 30s after its start)
        if now_ist is None:
            now_ist = pd.Timestamp.now(tz='Asia/Kolkata')
        completed = df[df['datetime'] <= (now_ist - pd.Timedelta(seconds=30))].reset_index(drop=True)
        if len(completed) <= candle_index:   # need the range candle + ≥1 watch bar
            return None

        rc = completed.iloc[candle_index - 1]  # 1-based range candle
        range_high = float(rc['high'])
        range_low  = float(rc['low'])
        if not (range_high > range_low):
            return None

        # Scan the bars AFTER the range candle for the first breakout
        for k in range(candle_index, len(completed)):
            bar = completed.iloc[k]
            tmin = int(bar['datetime'].hour) * 60 + int(bar['datetime'].minute)
            if tmin >= cutoff:          # no new entries at/after the cut-off
                break

            o, h, l = float(bar['open']), float(bar['high']), float(bar['low'])
            long_break  = enable_long  and h >= range_high
            short_break = enable_short and l <= range_low
            if not long_break and not short_break:
                continue

            # Resolve direction when one bar breaches both sides
            go_long = long_break
            if long_break and short_break:
                if o >= range_high:
                    go_long = True
                elif o <= range_low:
                    go_long = False
                else:
                    go_long = (range_high - o) <= (o - range_low)

            if go_long:
                entry = o if o > range_high else range_high
                sl_level = range_low
                risk = entry - sl_level
                tgt_level = entry + rr * risk
                direction = 'BUY'
            else:
                entry = o if o < range_low else range_low
                sl_level = range_high
                risk = sl_level - entry
                tgt_level = entry - rr * risk
                direction = 'SELL'

            return {
                'direction':    direction,
                'entry_ref':    round(entry, 2),
                'sl_level':     round(sl_level, 2),
                'target_level': round(tgt_level, 2),
                'entry_bar':    str(bar['datetime']),
                'range_high':   round(range_high, 2),
                'range_low':    round(range_low, 2),
            }

        return None

    # ── Live breakout (tick-by-tick against the range candle) ──────────────────

    def _ensure_range(
        self, provider: Any, params: Dict[str, Any], now_ist: pd.Timestamp,
    ) -> bool:
        """Lock in the range candle's High/Low once it has closed.

        Unlike _check_breakout (which waits for a *watch* bar to close), this only
        needs the range candle itself to be complete. The range candle is the
        candle_index-th 30-sec candle of the session, which closes at
        09:15:00 + candle_index × 30s (09:16:00 for the default 2nd candle).
        Cached for the rest of the day so entries can run off the live spot.
        """
        today = now_ist.date().isoformat()
        if self._range_date != today:        # new day → reset
            self._range_high = None
            self._range_low  = None
            self._range_date = today

        if self._range_high is not None:      # already locked in
            return True

        candle_index = int(params['candle_index'])

        # Don't even fetch until the range candle could have closed
        session_start = now_ist.normalize() + pd.Timedelta(hours=9, minutes=15)
        range_ready   = session_start + pd.Timedelta(seconds=30 * candle_index)
        if now_ist < range_ready:
            return False

        df = self._fetch_today_30s_candles(provider)
        if df is None or df.empty:
            return False

        completed = df[df['datetime'] <= (now_ist - pd.Timedelta(seconds=30))].reset_index(drop=True)
        if len(completed) < candle_index:     # range candle not closed yet
            return False

        rc = completed.iloc[candle_index - 1]  # 1-based range candle
        rh, rl = float(rc['high']), float(rc['low'])
        if rh <= rl:
            return False

        self._range_high = rh
        self._range_low  = rl
        logger.info(f"[SC] Range candle locked: high={rh} low={rl} bar={rc['datetime']}")
        return True

    def _eval_live_breakout(
        self, spot: float, params: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Detect a breakout of the cached range using the live spot price.

        Level math mirrors Backtest/second_candle_engine._run_day:
          BUY  (spot crosses above range High): entry = range_high, SL = range_low
          SELL (spot crosses below range Low):  entry = range_low,  SL = range_high
          Target = entry ± rr × risk
        """
        if self._range_high is None or self._range_low is None:
            return None

        rr           = float(params['rr_ratio'])
        enable_long  = bool(params['enable_long'])
        enable_short = bool(params['enable_short'])
        rh, rl       = self._range_high, self._range_low

        long_break  = enable_long  and spot >= rh
        short_break = enable_short and spot <= rl
        if not long_break and not short_break:
            return None

        go_long = long_break
        if long_break and short_break:        # spot beyond both (rare) → nearer side
            go_long = (rh - spot) <= (spot - rl)

        if go_long:
            entry = rh
            sl_level = rl
            risk = entry - sl_level
            tgt_level = entry + rr * risk
            direction = 'BUY'
        else:
            entry = rl
            sl_level = rh
            risk = sl_level - entry
            tgt_level = entry - rr * risk
            direction = 'SELL'

        return {
            'direction':    direction,
            'entry_ref':    round(entry, 2),
            'sl_level':     round(sl_level, 2),
            'target_level': round(tgt_level, 2),
            'range_high':   round(rh, 2),
            'range_low':    round(rl, 2),
        }

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
            raise ValueError("[SC] No NIFTY expiry dates found in instruments")
        expiry = expiry_dates[0]
        # On expiry day options have near-zero T; use next week for stable greeks
        if expiry == today and len(expiry_dates) > 1:
            expiry = expiry_dates[1]
        lot_size = 75
        for inst in instruments:
            if (inst.get('name') or '').upper() == 'NIFTY' and inst.get('expiry') == expiry:
                lot_size = int(inst.get('lot_size') or 75)
                break
        return instruments, expiry, lot_size

    def _fetch_fyers_chain_deltas(
        self, provider: Any, spot: float, t: float
    ) -> Dict:
        """Fetch Fyers option chain and compute delta for every strike using Fyers' own IV."""
        fn = getattr(provider, 'get_option_chain_raw', None)
        if fn is None:
            logger.warning("[SC] Provider has no get_option_chain_raw — cannot use Fyers chain")
            return {}

        try:
            raw_chain = fn(symbol=_NIFTY_FYERS, strikecount=50) or {}
        except Exception as e:
            logger.error(f"[SC] Fyers option chain fetch failed: {e}")
            return {}

        if not raw_chain:
            logger.warning("[SC] Fyers chain returned 0 rows")
            return {}

        # ── ATM IV from chain ─────────────────────────────────────────────
        atm = round(spot / _STRIKE_STEP) * _STRIKE_STEP
        atm_sigma: float = _FALLBACK_IV
        atm_found = False

        for ot_try in ('CE', 'PE'):
            entry = raw_chain.get((int(atm), ot_try))
            if not entry:
                continue
            iv = entry.get('iv')
            if iv and 0 < iv <= 5.0:
                atm_sigma = iv
                atm_found = True
                logger.info(f"[SC] ATM IV from Fyers chain: {atm_sigma:.4f} ({int(atm)}{ot_try})")
                break

        if not atm_found:
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
                            logger.info(f"[SC] ATM IV from LTP: {atm_sigma:.4f} (ltp={atm_ltp})")
                    except Exception:
                        pass

        if atm_sigma == _FALLBACK_IV:
            logger.warning(f"[SC] Using fallback IV {atm_sigma:.2f}")

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

        logger.info(
            f"[SC] Chain deltas computed: {len(result)} strikes, "
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
        """Return the strike from the Fyers option chain whose delta is closest to ±0.90."""
        atm           = round(spot / _STRIKE_STEP) * _STRIKE_STEP
        signed_target = _DELTA_TARGET if opt_type.upper() == 'CE' else -_DELTA_TARGET

        if self._expiry is None:
            logger.error("[SC] Expiry not set — cannot select strike")
            return atm, None

        t = GreeksCalculator.calculate_time_to_expiry(self._expiry)

        if chain_deltas is None:
            chain_deltas = self._fetch_fyers_chain_deltas(provider, spot, t)

        if not chain_deltas:
            logger.error("[SC] Fyers option chain empty — cannot select delta strike")
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
            if diff < best_diff:
                best_diff      = diff
                best_strike    = float(strike)
                best_delta_val = d
                best_symbol    = entry.get('symbol', '')
                best_ltp       = entry.get('ltp')

        if not best_symbol:
            logger.error(f"[SC] No {opt_type} strikes found in chain for target={signed_target:+.2f}")
            return best_strike, None

        inst: Dict = {
            'instrument_token': best_symbol,
            'tradingsymbol':    '',
            'name':             'NIFTY',
            'expiry':           self._expiry,
            'strike':           int(best_strike),
            'instrument_type':  opt_type.upper(),
        }

        logger.info(
            f"[SC] Delta strike: {opt_type} {int(best_strike)} "
            f"delta={best_delta_val:+.4f} ltp={best_ltp} sym={best_symbol} "
            f"(target={signed_target:+.2f} diff={best_diff:.4f})"
        )
        return best_strike, inst

    # ── Broker management ─────────────────────────────────────────────────────

    def _get_active_brokers(self) -> List[Tuple[int, str, Any]]:
        """Return (idx, broker_type, service) for all active+SC-enabled brokers."""
        result: List[Tuple[int, str, Any]] = []
        for i in range(1, 11):
            if self._uvar(f'BROKER_{i}_ACTIVE', 'false').lower() != 'true':
                continue
            if self._uvar(f'BROKER_{i}_SC_ACTIVE', 'false').lower() != 'true':
                continue
            broker_type = self._uvar(f'BROKER_{i}_TYPE', '').lower()
            if not broker_type:
                continue
            try:
                svc = self._init_broker_svc(i, broker_type)
                if svc:
                    result.append((i, broker_type, svc))
            except Exception as e:
                logger.error(f"[SC] Broker {i} ({broker_type}) init failed: {e}")
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
            logger.error(
                f"[SC] [{broker_type.upper()}_{idx}] {transaction_type} order error: {e}"
            )
        return None

    # ── Delta-strike query (used by the UI "Δ Strikes" button) ───────────────

    def get_delta_strikes(self, provider: Any) -> Dict[str, Any]:
        """Return the CE and PE strikes closest to ±0.90 delta using Fyers option chain."""
        spot: Optional[float] = None
        try:
            data = provider.ltp([_NIFTY_FYERS])
            raw  = data.get(_NIFTY_FYERS, {}).get('last_price', 0)
            spot = float(raw) if raw else None
        except Exception as _e:
            logger.warning(f"[SC] get_delta_strikes spot fetch: {_e}")

        if not spot:
            return {'success': False, 'error': 'Cannot fetch NIFTY spot price'}

        if self._expiry is None:
            try:
                self._instruments, self._expiry, self._lot_size = \
                    self._get_instruments_and_expiry(provider)
            except Exception as _e:
                return {'success': False, 'error': f'Cannot determine expiry: {_e}'}

        t = GreeksCalculator.calculate_time_to_expiry(self._expiry)

        chain_deltas = self._fetch_fyers_chain_deltas(provider, spot, t)
        if not chain_deltas:
            return {'success': False, 'error': 'Fyers option chain unavailable'}

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

    def _enter_trade(
        self, direction: str, entry_ref: float, sl_level: float,
        target_level: float, provider: Any,
    ) -> None:
        """Select delta strike, place BUY orders on all cached brokers, save state.
        SL/Target are computed by the caller from the 2nd-candle range."""
        opt_type = 'CE' if direction == 'BUY' else 'PE'
        t = GreeksCalculator.calculate_time_to_expiry(self._expiry) if self._expiry else 0.01
        chain_deltas = self._fetch_fyers_chain_deltas(provider, entry_ref, t)
        strike, inst = self._select_delta_strike(opt_type, entry_ref, provider, chain_deltas)

        if inst is None:
            logger.error(f"[SC] No delta strike found for {opt_type} near spot={entry_ref}")
            return

        fyers_sym = inst.get('instrument_token', '')
        kite_ts   = inst.get('tradingsymbol', '')

        broker_entries: List[Dict] = []
        for idx, (broker_type, svc) in self._broker_map.items():
            lots     = max(1, int(self._uvar(f'BROKER_{idx}_SC_LOTS', '1') or '1'))
            quantity = lots * self._lot_size
            product  = self._uvar(f'BROKER_{idx}_PRODUCT_TYPE', 'NRML').upper()
            order_id = self._place_order(
                idx, broker_type, svc, opt_type, strike,
                kite_ts, fyers_sym, quantity, 'BUY', product,
            )
            broker_entries.append({
                'broker_idx':    idx,
                'broker_type':   broker_type,
                'order_id':      str(order_id or ''),
                'tradingsymbol': kite_ts,
                'fyers_sym':     fyers_sym,
                'lots':          lots,
                'quantity':      quantity,
            })
            logger.info(
                f"[SC] [{broker_type.upper()}_{idx}] BUY {opt_type} {int(strike)}"
                f" qty={quantity} ({lots} lot(s)) order_id={order_id}"
            )

        # Capture option LTP just after order placement as proxy for fill price
        opt_entry_price: Optional[float] = None
        try:
            ltp_data = provider.ltp([fyers_sym])
            raw = ltp_data.get(fyers_sym, {}).get('last_price', 0)
            opt_entry_price = round(float(raw), 2) if raw else None
        except Exception as _e:
            logger.warning(f"[SC] Could not fetch option entry LTP: {_e}")

        state = self._load_state()
        state['active_trade'] = {
            'direction':       direction,
            'entry_spot':      entry_ref,
            'entry_time':      datetime.now().isoformat(),
            'sl_level':        sl_level,
            'target_level':    target_level,
            'strike':          int(strike),
            'option_type':     opt_type,
            'lot_size':        self._lot_size,
            'expiry':          str(self._expiry),
            'broker_entries':  broker_entries,
            'opt_entry_price': opt_entry_price,
        }
        state['traded_today'] = True            # one trade per day
        state['trade_date']   = date.today().isoformat()
        self._save_state(state)
        logger.info(
            f"[SC] ENTERED {direction}: entry={entry_ref} {int(strike)}{opt_type}"
            f" opt_ltp={opt_entry_price}"
            f" sl={sl_level} tgt={target_level} expiry={self._expiry}"
            f" brokers={len(broker_entries)}"
        )

    def _exit_trade(self, reason: str, spot: float) -> None:
        """Square off option position on all brokers and clear active trade in state."""
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
            logger.warning(f"[SC] Could not fetch option exit LTP: {_e}")

        for entry in trade.get('broker_entries', []):
            idx         = entry['broker_idx']
            broker_type = entry.get('broker_type', 'zerodha')
            kite_ts     = entry.get('tradingsymbol', '')
            fyers_sym   = entry.get('fyers_sym', '')
            quantity    = entry.get('quantity', int(trade.get('lot_size', 75)))
            try:
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
                    logger.info(
                        f"[SC] [{broker_type.upper()}_{idx}] SELL {opt_type} {strike}"
                        f" qty={quantity} order_id={order_id}"
                    )
            except Exception as e:
                logger.error(f"[SC] Exit order failed broker {idx}: {e}")

        state['active_trade'] = None
        state['last_exit'] = {
            'reason': reason,
            'spot':   spot,
            'time':   datetime.now().isoformat(),
        }
        self._append_history(trade, spot, reason, opt_exit_price=opt_exit_price)
        self._save_state(state)
        self._spot_fail_count = 0
        logger.info(f"[SC] EXITED ({reason}): spot={spot} opt_exit_ltp={opt_exit_price}")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        logger.info("[SC] Monitor loop started — waiting for data provider")

        # ── Wait for provider (token may not be available immediately after restart) ──
        provider = None
        while not self._stop_event.is_set():
            try:
                provider = self._get_provider()
                if provider:
                    break
            except Exception as _e:
                logger.warning(f"[SC] Provider init error: {_e}")
            logger.info("[SC] Data provider not ready — retrying in 30s")
            self._stop_event.wait(30)

        if self._stop_event.is_set() or provider is None:
            logger.info("[SC] Monitor loop stopped before provider became available")
            return

        self._provider = provider  # stored so _exit_trade can fetch option LTP
        logger.info("[SC] Data provider ready")

        # ── Day-start / mid-day-restart initialisation ────────────────────────
        state = self._load_state()
        _today_str = date.today().isoformat()

        # Discard any active trade left over from a previous trading day
        _stale = state.get('active_trade')
        if _stale and not str(_stale.get('entry_time', '')).startswith(_today_str):
            logger.warning("[SC] Stale active trade from previous day — clearing")
            state['active_trade'] = None

        # Reset the one-trade-a-day flag at the start of a new trading day
        if state.get('trade_date') != _today_str:
            state['trade_date']   = _today_str
            state['traded_today'] = False
            logger.info("[SC] New trading day — traded_today reset")
        self._save_state(state)

        # Cache broker services once — avoids repeated session re-initialisation per trade
        logger.info("[SC] Initialising broker services...")
        self._broker_map = {
            idx: (btype, svc)
            for idx, btype, svc in self._get_active_brokers()
        }
        if self._broker_map:
            logger.info(f"[SC] Active SC brokers: {list(self._broker_map.keys())}")
        else:
            logger.warning("[SC] No active SC brokers — signals detected but no orders placed")

        # ── Wait for NFO instruments (may need a live market session) ─────────
        logger.info("[SC] Fetching NFO instruments...")
        while not self._stop_event.is_set():
            try:
                self._instruments, self._expiry, self._lot_size = \
                    self._get_instruments_and_expiry(provider)
                logger.info(
                    f"[SC] Instruments ready — expiry={self._expiry}"
                    f" lot_size={self._lot_size} count={len(self._instruments)}"
                )
                break
            except Exception as e:
                logger.warning(f"[SC] Instrument fetch failed: {e} — retrying in 60s")
                self._stop_event.wait(60)

        if self._stop_event.is_set():
            logger.info("[SC] Monitor loop stopped during instrument fetch")
            return

        last_signal_minute:      Optional[int] = None
        last_exit_candle_minute: int = -1

        while not self._stop_event.is_set():
            try:
                now = datetime.now()
                h, m = now.hour, now.minute

                # ── Before trading session: wait
                if (h < 9) or (h == 9 and m < 15):
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
                        self._exit_trade('EOD', spot or 0.0)
                    logger.info("[SC] EOD cutoff reached — monitor loop ending")
                    break

                state  = self._load_state()
                params = state.get('params') or dict(DEFAULT_PARAMS)

                # ── In trade → check SL / Target every second (kill-switch never blocks exits)
                if state.get('active_trade'):
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
                            elif spot >= tgt_level:
                                self._exit_trade('TARGET', spot)
                        else:  # SELL → PE bought
                            if spot >= sl_level:
                                self._exit_trade('SL', spot)
                            elif spot <= tgt_level:
                                self._exit_trade('TARGET', spot)
                    else:
                        self._spot_fail_count += 1
                        if self._spot_fail_count >= _MAX_SPOT_FAILS:
                            logger.critical(
                                f"[SC] Spot fetch failed {self._spot_fail_count} consecutive times"
                                " — open trade UNMONITORED. Check provider connection."
                            )

                    # ── Per-minute candle H/L exit check (mirrors backtest H/L touch) ──
                    if m != last_exit_candle_minute:
                        last_exit_candle_minute = m
                        _cs = self._load_state()
                        if _cs.get('active_trade'):
                            try:
                                _tdf = self._fetch_today_30s_candles(provider)
                                if _tdf is not None and not _tdf.empty:
                                    _now_ist   = pd.Timestamp.now(tz='Asia/Kolkata')
                                    _completed = _tdf[_tdf['datetime'] <= (_now_ist - pd.Timedelta(seconds=30))]
                                    _at  = _cs['active_trade']
                                    _ets = pd.Timestamp(_at.get('entry_time'))
                                    if _ets.tzinfo is None:
                                        _ets = _ets.tz_localize('Asia/Kolkata')
                                    else:
                                        _ets = _ets.tz_convert('Asia/Kolkata')
                                    _to_check = _completed[_completed['datetime'] >= _ets.floor('min')]
                                    _dir = _at['direction']
                                    _sl  = float(_at['sl_level'])
                                    _tgt = float(_at['target_level'])
                                    for _, _c in _to_check.iterrows():
                                        if not self._load_state().get('active_trade'):
                                            break
                                        if _dir == 'BUY':
                                            if _c['low'] <= _sl:
                                                logger.info(f"[SC] Candle exit BUY SL: {_c['datetime']} low={_c['low']} <= {_sl}")
                                                self._exit_trade('SL', _sl)
                                                break
                                            if _c['high'] >= _tgt:
                                                logger.info(f"[SC] Candle exit BUY TARGET: {_c['datetime']} high={_c['high']} >= {_tgt}")
                                                self._exit_trade('TARGET', _tgt)
                                                break
                                        else:
                                            if _c['high'] >= _sl:
                                                logger.info(f"[SC] Candle exit SELL SL: {_c['datetime']} high={_c['high']} >= {_sl}")
                                                self._exit_trade('SL', _sl)
                                                break
                                            if _c['low'] <= _tgt:
                                                logger.info(f"[SC] Candle exit SELL TARGET: {_c['datetime']} low={_c['low']} <= {_tgt}")
                                                self._exit_trade('TARGET', _tgt)
                                                break
                            except Exception as _ce:
                                logger.warning(f"[SC] Candle exit check error: {_ce}")

                else:
                    # ── Runtime kill-switch — only blocks new entries
                    if self._uvar('SC_ALGO_ACTIVE', 'false').lower() != 'true':
                        time.sleep(5)
                        continue

                    # ── One trade per day: stop scanning once we've traded
                    if state.get('traded_today'):
                        time.sleep(5)
                        continue

                    # ── No trade → detect the FIRST breakout of the range candle.
                    #
                    # A breakout on a COMPLETED 30-sec candle is authoritative: it
                    # mirrors the backtest's first-breakout scan (_check_breakout)
                    # and uses candle High/Low, so it catches fast wicks and any
                    # breakout that closed *before* the algo finished arming. The
                    # live-spot check (_eval_live_breakout) only reacts to the
                    # currently forming candle, so on its own it can miss the true
                    # first breakout and take the opposite side (see the 01-Jul case
                    # where the backtest went SHORT at 09:17 but live went LONG at 09:19).
                    #
                    # Strategy: keep the tick-fast live-spot trigger for latency, but
                    # confirm/override direction with the completed-candle scan whenever
                    # we're about to enter, plus a throttled periodic scan so a missed
                    # earlier breakout is caught even before the spot moves again.
                    _p       = normalise_params(params)
                    now_ist  = pd.Timestamp.now(tz='Asia/Kolkata')
                    cutoff   = int(_p['exit_hour']) * 60 + int(_p['exit_minute'])
                    if (now_ist.hour * 60 + now_ist.minute) < cutoff and \
                            self._ensure_range(provider, _p, now_ist):
                        spot     = self._get_nifty_spot(provider)
                        live_sig = self._eval_live_breakout(spot, _p) if spot else None

                        # Run the authoritative completed-candle scan when a live
                        # trigger wants to enter (to confirm direction) or when the
                        # periodic catch-up interval is due.
                        _due = (self._last_breakout_scan is None or
                                (now_ist - self._last_breakout_scan).total_seconds()
                                    >= _BREAKOUT_SCAN_SECS)
                        sig = None
                        if live_sig is not None or _due:
                            self._last_breakout_scan = now_ist
                            _bdf       = self._fetch_today_30s_candles(provider)
                            candle_sig = self._check_breakout(_bdf, _p, now_ist)
                            # Completed candle wins → matches the backtest direction
                            sig = candle_sig or live_sig
                            if (candle_sig is not None and live_sig is not None and
                                    candle_sig['direction'] != live_sig['direction']):
                                logger.info(
                                    f"[SC] Direction override: live spot said "
                                    f"{live_sig['direction']} but first completed "
                                    f"breakout was {candle_sig['direction']} — taking "
                                    f"the completed-candle breakout"
                                )

                        if sig:
                            _src = 'live-spot' if sig is live_sig else 'candle'
                            logger.info(
                                f"[SC] Breakout {sig['direction']} ({_src}) spot={spot}"
                                f" entry_ref={sig['entry_ref']} sl={sig['sl_level']}"
                                f" tgt={sig['target_level']}"
                                f" range=[{sig['range_low']}, {sig['range_high']}]"
                            )
                            self._enter_trade(
                                sig['direction'], sig['entry_ref'],
                                sig['sl_level'], sig['target_level'], provider,
                            )

            except Exception as e:
                logger.error(f"[SC] Monitor error: {e}", exc_info=True)

            time.sleep(1)

        logger.info("[SC] Monitor loop ended")
