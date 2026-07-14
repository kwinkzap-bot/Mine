"""
Intrinsic ATM Range Breakout Algo — Paper Trade

Derived from the "previous-close ATM intrinsic value" method used for manual
OI-Profile orders (strategy 'intrinsic'): every morning, find the strike where
CE and PE premiums (previous day's close) are closest to equal — the "common
ATM" — average those two premiums and round to the nearest strike step. That
rounded value becomes a symmetric range around the ATM strike (the previous
day's "intrinsic value zone" / expected-move band).

The range is anchored to the SYNTHETIC previous close, not the spot candle:
ref = atm_strike + (CE_prev - PE_prev). Options settle against the future, so
the CE/PE premium skew at the ATM strike locates where the future closed.
Each range boundary then gets an intrinsic value against that reference:
  ce_lower_intrinsic = ref - lower_bound   (lower CE holds prev close above this)
  pe_upper_intrinsic = upper_bound - ref   (upper PE holds prev close below this)

A breakout is only traded once FOUR things line up (mirrors the manual
read: raw level break isn't enough, it needs volatility-expansion confirmation):
  1. Spot trades outside the range (below lower_bound or above upper_bound).
  2. The option on the broken side already prices in intrinsic value >= the
     day's total range (the premium math "catches up" to the breakout).
  3. The option on the OPPOSITE side trades below its own intrinsic value —
     the "oncoming vehicle gives way": an up-move is only clean when the
     upper PE surrenders the premium that would pull price back to prev close
     (and vice versa for the lower CE on a down-move).
  4. Both India VIX and the live common-ATM premium (recomputed at the
     current ATM strike) are expanding vs. the day's opening reading — this
     is what separates a real trend day from range-bound noise.

An outer ring (atm_strike ± total_range, area = 2x total_range) is computed
for the dashboard as the trend-day confirmation level: the broken-side option
touching the outer area value marks a full range-expansion day.

Direction follows whichever side's premium is expanding (that's where option
sellers are getting squeezed), not the side that's collapsing.

Gap-day handling (13-Jul-2026 case study, youtube izLl5ccWKE0):
  * If the day OPENS outside the primary range, the ACTIVE trading range
    widens ring by ring until the open sits inside it (case study: open below
    24100 → trade the 24000 CE / 24400 PE ring, total range 400, intrinsics
    206 / 194 against the 24206 synthetic close). All breakout checks then
    run against the active range, not the primary one.
  * The active boundary options' intraday premium LOW is the "demand place".
    lower_strike + CE_day_low is the low-reclaim level (24000 + 81 = 24081 in
    the case study). Spot SUSTAINING above it means the day's low has lost
    its probability of being cut — sell-on-rise must be abandoned there.
    Mirror level for the upper PE: upper_strike - PE_day_low.
  * RECLAIM entry (gap days only): on a gap-down day, when spot sustains
    above the low-reclaim level AND the opposite-side upper PE fails to hold
    the active total range (24400 PE < 400 → the lower CE regains the power
    to go back ITM), that is a BUY. SL = the reclaim level itself (back below
    it, the buying effect is gone). Mirror for gap-up SELL. VIX / premium
    expansion filters do NOT apply to reclaim entries: a rising VIX means
    option sellers are in danger — on a covering rally it fuels the up-move,
    so direction must come from structure, not from the VIX sign.
  * Discipline: entries only before the morning cutoff and a max number of
    trades per day — one strategy, one disciplined trade, then stop.

This module is PAPER-TRADE ONLY. `self.mode` is scaffolded for a future
'live' path (real broker orders via the same _place_order-style hook used by
rtp_algo.py), but only 'paper' is implemented — 'live' currently logs a
warning and falls back to paper so this can be wired into the live algo
dashboard without risking real capital until the logic is validated.

Per-user env vars:
  EMA_INTRINSIC_RANGE_ACTIVE      = true/false   (gates entries; thread always runs during market hours)
  INTRINSIC_RANGE_MODE            = paper (default) | live (not yet implemented — falls back to paper)
  INTRINSIC_RANGE_LOTS            = 1             (paper lot count)
  INTRINSIC_RANGE_SL_POINTS       = ''            (default: day's range_half / 2)
  INTRINSIC_RANGE_TGT_POINTS      = ''            (default: day's range_half)
  INTRINSIC_RANGE_EXPANSION_MULT  = 1.3           (live common-ATM premium must be >= 1.3x the day's baseline; breakout entries only)
  INTRINSIC_RANGE_VIX_RISE_PCT    = 5.0           (India VIX must be >= +5% vs the day's opening reading; breakout entries only)
  INTRINSIC_RANGE_ENTRY_CUTOFF    = 10:00         (no new entries at/after this time — "one trade within 10 AM")
  INTRINSIC_RANGE_MAX_TRADES_PER_DAY = 1          (disciplined one-trade-per-day default)
  INTRINSIC_RANGE_SUSTAIN_POLLS   = 8             (consecutive 15s polls a level must hold to count as "sustained", ~2 min)
"""
import json
import logging
import math
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(__file__)

_STRIKE_STEP = 50.0
_NIFTY_FYERS = 'NSE:NIFTY50-INDEX'
_VIX_FYERS   = 'NSE:INDIAVIX-INDEX'
_DEFAULT_LOT_SIZE = 75


def _tiered_strike_diff(premium: float) -> float:
    """Escalating strike-diff ladder (100 -> 200 -> 300 -> ...) for gap-day premiums.

    A fixed step (50) under-shoots on a gap day: a 240-premium common-ATM
    rounds to only 200 of range-half, but the range needs to cover at least
    as much as the premium that produced it. Ladder in multiples of 100,
    so the picked distance always covers the premium.
    """
    if not premium or premium <= 0:
        return _STRIKE_STEP
    return math.ceil(premium / 100.0) * 100.0

_STATE_FILE       = os.path.join(_DIR, 'intrinsic_range_state.json')
_HISTORY_FILE     = os.path.join(_DIR, 'intrinsic_range_trades_history.json')
_ALL_HISTORY_FILE = os.path.join(_DIR, 'intrinsic_range_trades_all_history.json')

_instances: Dict[str, 'IntrinsicRangeAlgo'] = {}


def get_instance(username: str) -> Optional['IntrinsicRangeAlgo']:
    return _instances.get(username)


class _PrefixLogger(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[IntrinsicRange] {msg}", kwargs


class IntrinsicRangeAlgo:
    """Paper-trade signal detector + simulated executor for the intrinsic ATM range breakout."""

    def __init__(self, username: str):
        self.username = username
        self.log = _PrefixLogger(logger, {})
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        self._instruments: List[Dict] = []
        self._expiry: Optional[date] = None
        self._lot_size: int = _DEFAULT_LOT_SIZE
        self._daily_setup: Optional[Dict[str, Any]] = None
        self._poll_secs = 15

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name='intrinsic-range-algo-monitor',
        )
        self._thread.start()
        _instances[self.username] = self
        self.log.info("Monitoring thread started (paper mode)")

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop(self.username, None)
        self.log.info("Stop requested")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── State ────────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(_STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {
                'date': None, 'daily_setup': None, 'day_open_vix': None,
                'day_open_spot': None, 'active_range': None,
                'ce_boundary_day_low': None, 'pe_boundary_day_low': None,
                'low_reclaim_level': None, 'high_reclaim_level': None,
                'above_low_reclaim_polls': 0, 'below_high_reclaim_polls': 0,
                'buy_reclaim_done': False, 'sell_reclaim_done': False,
                'buy_needs_reset': False, 'sell_needs_reset': False,
                'active_trade': None,
            }

    def _save_state(self, state: Dict[str, Any]) -> None:
        with self._state_lock:
            try:
                with open(_STATE_FILE, 'w') as f:
                    json.dump(state, f, indent=2, default=str)
            except Exception as e:
                self.log.error(f"State save failed: {e}")

    def _append_history(self, trade: Dict[str, Any], exit_spot: float, reason: str,
                         exit_premium: Optional[float]) -> None:
        try:
            today = date.today().isoformat()
            try:
                with open(_HISTORY_FILE, 'r') as f:
                    history: list = json.load(f)
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []

            if history and history[0].get('date') != today:
                history = []

            direction  = trade.get('direction', 'BUY')
            entry_spot = float(trade.get('entry_spot', 0))
            pnl_pts    = round(exit_spot - entry_spot, 2) if direction == 'BUY' \
                         else round(entry_spot - exit_spot, 2)

            entry_premium = trade.get('entry_premium')
            prem_pnl_pts: Optional[float] = None
            prem_pnl_inr: Optional[float] = None
            if entry_premium is not None and exit_premium is not None:
                prem_pnl_pts = round(exit_premium - entry_premium, 2)
                prem_pnl_inr = round(prem_pnl_pts * trade.get('quantity', _DEFAULT_LOT_SIZE), 2)

            record = {
                'date':            today,
                'mode':            trade.get('mode', 'paper'),
                'direction':       direction,
                'entry_kind':      trade.get('entry_kind', 'BREAKOUT'),
                'entry_spot':      entry_spot,
                'exit_spot':       exit_spot,
                'pnl_pts':         pnl_pts,
                'reason':          reason,
                'entry_time':      trade.get('entry_time', ''),
                'exit_time':       datetime.now().isoformat(),
                'strike':          trade.get('strike'),
                'option_type':     trade.get('option_type', ''),
                'lot_size':        trade.get('lot_size', _DEFAULT_LOT_SIZE),
                'lots':            trade.get('lots', 1),
                'entry_premium':   entry_premium,
                'exit_premium':    exit_premium,
                'premium_pnl_pts': prem_pnl_pts,
                'premium_pnl_inr': prem_pnl_inr,
                'daily_setup':     trade.get('daily_setup'),
                # Aliases matching the RTP/SC history schema so this feeds the
                # shared "Active Trades — All Algos" tab without special-casing.
                'opt_entry_price': entry_premium,
                'opt_exit_price':  exit_premium,
                'opt_pnl_pts':     prem_pnl_pts,
                'opt_pnl_inr':     prem_pnl_inr,
            }
            history.insert(0, record)

            with open(_HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=2, default=str)

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
                self.log.error(f"All-history append failed: {_ae}")
        except Exception as e:
            self.log.error(f"History append failed: {e}")

    # ── Env helpers ──────────────────────────────────────────────────────────

    def _uvar(self, key: str, default: str = '') -> str:
        from trading_app.app.utils.user_env import UserEnvManager
        return (UserEnvManager.get_user_var(self.username, key) or default).strip()

    def _mode(self) -> str:
        mode = self._uvar('INTRINSIC_RANGE_MODE', 'paper').lower()
        if mode == 'live':
            self.log.warning("INTRINSIC_RANGE_MODE=live requested but live execution is not implemented yet — running in paper mode")
            return 'paper'
        return 'paper'

    # ── Data ─────────────────────────────────────────────────────────────────

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

    def _get_vix(self, provider: Any) -> Optional[float]:
        try:
            data = provider.ltp([_VIX_FYERS])
            v = data.get(_VIX_FYERS, {}).get('last_price', 0)
            return float(v) if v else None
        except Exception as e:
            self.log.warning(f"VIX fetch failed: {e}")
            return None

    def _get_live_chain(self, provider: Any) -> Dict:
        fn = getattr(provider, 'get_option_chain_raw', None)
        if fn is None:
            return {}
        try:
            return fn(symbol=_NIFTY_FYERS, strikecount=50) or {}
        except Exception as e:
            self.log.warning(f"Option chain fetch failed: {e}")
            return {}

    def _get_ltp_one(self, provider: Any, fyers_sym: str) -> Optional[float]:
        if not fyers_sym:
            return None
        try:
            data = provider.ltp([fyers_sym])
            raw = data.get(fyers_sym, {}).get('last_price', 0)
            return round(float(raw), 2) if raw else None
        except Exception as e:
            self.log.warning(f"LTP fetch failed for {fyers_sym}: {e}")
            return None

    # ── Instruments ──────────────────────────────────────────────────────────

    def _get_instruments_and_expiry(self, provider: Any) -> Tuple[List[Dict], date, int]:
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
        expiry = expiry_dates[0]
        lot_size = _DEFAULT_LOT_SIZE
        for inst in instruments:
            if (inst.get('name') or '').upper() == 'NIFTY' and inst.get('expiry') == expiry:
                lot_size = int(inst.get('lot_size') or _DEFAULT_LOT_SIZE)
                break
        return instruments, expiry, lot_size

    def _find_option(self, instruments: List[Dict], strike: float, opt_type: str, expiry: date) -> Optional[Dict]:
        for inst in instruments:
            if (
                (inst.get('name') or '').upper() == 'NIFTY'
                and inst.get('instrument_type', '').upper() == opt_type.upper()
                and inst.get('expiry') == expiry
                and abs((inst.get('strike') or 0) - strike) < 0.5
            ):
                return inst
        return None

    def _fetch_prev_close_premium(self, provider: Any, inst: Dict) -> Optional[float]:
        """Previous trading day's close premium for one option instrument (daily candle)."""
        token = inst.get('instrument_token')
        if not token:
            return None
        try:
            today = date.today()
            from_date = (today - timedelta(days=10)).isoformat()
            to_date = (today - timedelta(days=1)).isoformat()
            candles = provider.historical_data(
                instrument_token=token, from_date=from_date, to_date=to_date,
                interval='day', use_cache=True,
            )
            if not candles:
                return None
            return float(candles[-1]['close'])
        except Exception as e:
            self.log.warning(f"Prev-close fetch failed for {token}: {e}")
            return None

    # ── Daily range setup ────────────────────────────────────────────────────

    def _compute_daily_setup(self, provider: Any) -> Optional[Dict[str, Any]]:
        """Previous-close ATM strike + common-ATM premium + intrinsic range. Once per trading day."""
        try:
            today = date.today()
            from_date = (today - timedelta(days=10)).isoformat()
            to_date = (today - timedelta(days=1)).isoformat()
            spot_candles = provider.historical_data(
                instrument_token=_NIFTY_FYERS, from_date=from_date, to_date=to_date,
                interval='day', use_cache=True,
            )
            if not spot_candles:
                self.log.warning("No previous-close spot candle available yet")
                return None
            prev_close_spot = float(spot_candles[-1]['close'])

            if not self._instruments or self._expiry is None:
                self._instruments, self._expiry, self._lot_size = self._get_instruments_and_expiry(provider)

            base_strike = round(prev_close_spot / _STRIKE_STEP) * _STRIKE_STEP
            candidates = [base_strike - _STRIKE_STEP, base_strike, base_strike + _STRIKE_STEP]

            best: Optional[Dict[str, Any]] = None
            for cand in candidates:
                ce_inst = self._find_option(self._instruments, cand, 'CE', self._expiry)
                pe_inst = self._find_option(self._instruments, cand, 'PE', self._expiry)
                if not ce_inst or not pe_inst:
                    continue
                ce_prev = self._fetch_prev_close_premium(provider, ce_inst)
                pe_prev = self._fetch_prev_close_premium(provider, pe_inst)
                if ce_prev is None or pe_prev is None:
                    continue
                diff = abs(ce_prev - pe_prev)
                if best is None or diff < best['diff']:
                    best = {'strike': cand, 'ce_prev': ce_prev, 'pe_prev': pe_prev, 'diff': diff}

            if best is None:
                self.log.warning("Could not resolve previous-close ATM strike — no CE/PE premium data")
                return None

            # ATM definition: CE/PE prev-close premiums within half a strike
            # step of each other. Wider than that means the true ATM sits at a
            # strike we didn't scan — trade the day anyway but flag it.
            if best['diff'] >= _STRIKE_STEP / 2:
                self.log.warning(
                    f"ATM CE/PE prev-close diff {best['diff']:.2f} >= {_STRIKE_STEP / 2:.0f} "
                    f"at strike {int(best['strike'])} — synthetic close may be off"
                )

            atm_strike = best['strike']
            # Synthetic previous close: the CE/PE skew at the ATM strike locates
            # the future's close (options settle on the future, not spot).
            synthetic_prev_close = round(atm_strike + (best['ce_prev'] - best['pe_prev']), 2)

            common_atm = round((best['ce_prev'] + best['pe_prev']) / 2, 2)
            range_half = _tiered_strike_diff(common_atm)
            lower_bound = atm_strike - range_half
            upper_bound = atm_strike + range_half
            total_range = 2 * range_half

            setup = {
                'schema':         4,
                'date':           today.isoformat(),
                'prev_close_spot': prev_close_spot,
                'synthetic_prev_close': synthetic_prev_close,
                'atm_strike':      atm_strike,
                'ce_prev_close':   best['ce_prev'],
                'pe_prev_close':   best['pe_prev'],
                'common_atm':      common_atm,
                'range_half':      range_half,
                'lower_bound':     lower_bound,
                'upper_bound':     upper_bound,
                'total_range':     total_range,
                # Intrinsic value each boundary option must hold to keep the
                # market pinned to the synthetic previous close.
                'ce_lower_intrinsic': round(synthetic_prev_close - lower_bound, 2),
                'pe_upper_intrinsic': round(upper_bound - synthetic_prev_close, 2),
                # Outer ring: full range-expansion / trend-day confirmation.
                'outer_lower':       atm_strike - total_range,
                'outer_upper':       atm_strike + total_range,
                'outer_total_range': 2 * total_range,
            }
            self.log.info(
                f"Daily setup: synth_close={synthetic_prev_close} (spot {prev_close_spot}) "
                f"atm={int(atm_strike)} common_atm={common_atm} "
                f"range=[{int(lower_bound)}, {int(upper_bound)}] (total {int(total_range)}) "
                f"intrinsics CE{int(lower_bound)}={setup['ce_lower_intrinsic']} "
                f"PE{int(upper_bound)}={setup['pe_upper_intrinsic']} "
                f"outer=[{int(setup['outer_lower'])}, {int(setup['outer_upper'])}]"
            )
            return setup
        except Exception as e:
            self.log.error(f"Daily setup computation failed: {e}", exc_info=True)
            return None

    # ── Active range & per-poll tracking ─────────────────────────────────────

    def _select_active_range(self, setup: Dict[str, Any], open_spot: float, provider: Any) -> Dict[str, Any]:
        """Widen the primary ring until the day's open sits inside it, then
        widen further, 100 points of total range at a time, until each
        boundary option's own live opening premium fits within that width.

        13-Jul case study: open below the 24100 lower bound → the tradeable
        ring becomes 24000 CE / 24400 PE (total range 400), with each boundary
        option's intrinsic value recomputed against the synthetic prev close
        (206 / 194 vs 24206).

        Premium check: the range width picked at setup time is sized off
        YESTERDAY's close premium (see _tiered_strike_diff); if a boundary
        option's own live opening premium today already exceeds that width
        (e.g. 24100 CE / 24300 PE, 200-wide, but the CE opens at 240), the
        range under-priced today's move — bump the total range by 100
        (200 -> 300 -> 24050/24350, then 300 -> 400 if still exceeded, ...)
        and recheck against the new boundary strikes.
        """
        synth = float(setup['synthetic_prev_close'])
        atm   = float(setup['atm_strike'])
        half  = float(setup['range_half'])

        # 1) Ring-by-ring until today's open sits inside it (gap-day case).
        mult = 1
        while mult < 4 and not (atm - mult * half <= open_spot <= atm + mult * half):
            mult += 1
        total_range = 2 * mult * half
        gap_side: Optional[str] = 'down' if (mult > 1 and open_spot < atm) else ('up' if mult > 1 else None)

        # 2) 100-point ladder until each boundary's own live Open premium no
        #    longer exceeds the range width it sits in.
        for _ in range(6):
            lower = atm - total_range / 2
            upper = atm + total_range / 2
            chain = self._get_live_chain(provider)
            ce_open = (chain.get((int(lower), 'CE')) or {}).get('ltp')
            pe_open = (chain.get((int(upper), 'PE')) or {}).get('ltp')
            if (ce_open is not None and ce_open > total_range) or \
               (pe_open is not None and pe_open > total_range):
                self.log.info(
                    f"Active range [{int(lower)}, {int(upper)}] (total {int(total_range)}) "
                    f"under-priced by boundary premium (CE={ce_open}, PE={pe_open}) — widening to "
                    f"{int(total_range) + 100}"
                )
                total_range += 100
                continue
            break

        lower = atm - total_range / 2
        upper = atm + total_range / 2
        return {
            'lower_bound':        lower,
            'upper_bound':        upper,
            'total_range':        total_range,
            'ce_lower_intrinsic': round(synth - lower, 2),
            'pe_upper_intrinsic': round(upper - synth, 2),
            'range_mult':         mult,
            'gap_side':           gap_side,
        }

    def _update_tracking(self, provider: Any, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Per-poll market context. Mutates state: day open spot/VIX capture,
        active-range selection, boundary-option day lows ("the low is the
        demand place"), reclaim levels and their sustain counters."""
        setup = state['daily_setup']
        spot = self._get_nifty_spot(provider)
        if spot is None:
            return None

        if state.get('day_open_spot') is None:
            state['day_open_spot'] = spot

        vix = self._get_vix(provider)
        if state.get('day_open_vix') is None and vix is not None:
            state['day_open_vix'] = vix

        if not state.get('active_range'):
            state['active_range'] = self._select_active_range(setup, state['day_open_spot'], provider)
            rng0 = state['active_range']
            self.log.info(
                f"Active range: [{int(rng0['lower_bound'])}, {int(rng0['upper_bound'])}] "
                f"mult=x{rng0['range_mult']} gap_side={rng0['gap_side']} "
                f"intrinsics CE={rng0['ce_lower_intrinsic']} PE={rng0['pe_upper_intrinsic']} "
                f"(open {state['day_open_spot']})"
            )
        rng = state['active_range']

        chain = self._get_live_chain(provider)
        ce_lower = (chain.get((int(rng['lower_bound']), 'CE')) or {}).get('ltp') if chain else None
        pe_upper = (chain.get((int(rng['upper_bound']), 'PE')) or {}).get('ltp') if chain else None

        if ce_lower is not None:
            prev = state.get('ce_boundary_day_low')
            state['ce_boundary_day_low'] = ce_lower if prev is None else min(prev, ce_lower)
        if pe_upper is not None:
            prev = state.get('pe_boundary_day_low')
            state['pe_boundary_day_low'] = pe_upper if prev is None else min(prev, pe_upper)

        # Reclaim levels: strike ± the boundary option's day-low premium
        # (24000 + 81 = 24081 in the case study). Above the low-reclaim level
        # the day's low loses its probability of being cut; mirror for highs.
        ce_low = state.get('ce_boundary_day_low')
        pe_low = state.get('pe_boundary_day_low')
        state['low_reclaim_level']  = round(rng['lower_bound'] + ce_low, 2) if ce_low is not None else None
        state['high_reclaim_level'] = round(rng['upper_bound'] - pe_low, 2) if pe_low is not None else None

        sustain_polls = max(1, int(self._uvar('INTRINSIC_RANGE_SUSTAIN_POLLS', '8') or '8'))
        if state['low_reclaim_level'] is not None and spot > state['low_reclaim_level']:
            state['above_low_reclaim_polls'] = int(state.get('above_low_reclaim_polls') or 0) + 1
        else:
            state['above_low_reclaim_polls'] = 0
        if state['high_reclaim_level'] is not None and spot < state['high_reclaim_level']:
            state['below_high_reclaim_polls'] = int(state.get('below_high_reclaim_polls') or 0) + 1
        else:
            state['below_high_reclaim_polls'] = 0

        live_common_atm = None
        if chain:
            live_atm = round(spot / _STRIKE_STEP) * _STRIKE_STEP
            ce_live = (chain.get((int(live_atm), 'CE')) or {}).get('ltp')
            pe_live = (chain.get((int(live_atm), 'PE')) or {}).get('ltp')
            live_common_atm = (ce_live + pe_live) / 2 if ce_live and pe_live else None

        return {
            'spot':                 spot,
            'vix':                  vix,
            'chain_ok':             bool(chain),
            'rng':                  rng,
            'ce_lower':             ce_lower,
            'pe_upper':             pe_upper,
            'live_common_atm':      live_common_atm,
            'sustained_above_low':  int(state.get('above_low_reclaim_polls') or 0) >= sustain_polls,
            'sustained_below_high': int(state.get('below_high_reclaim_polls') or 0) >= sustain_polls,
        }

    # ── Signal detection ─────────────────────────────────────────────────────

    def _check_signal(
        self, ctx: Dict[str, Any], state: Dict[str, Any],
    ) -> Tuple[Optional[str], str, Optional[float]]:
        """Mutates state (needs-reset / reclaim-done flags) in place.
        Returns (signal, entry_kind, sl_level_override)."""
        setup = state['daily_setup']
        rng   = ctx['rng']
        spot  = ctx['spot']
        if not ctx['chain_ok']:
            return None, 'BREAKOUT', None

        expansion_mult = float(self._uvar('INTRINSIC_RANGE_EXPANSION_MULT', '1.3') or '1.3')
        vix_rise_pct   = float(self._uvar('INTRINSIC_RANGE_VIX_RISE_PCT', '5.0') or '5.0')

        expansion_ok = (
            ctx['live_common_atm'] is not None and setup['common_atm'] > 0
            and (ctx['live_common_atm'] / setup['common_atm']) >= expansion_mult
        )
        vix_ok = (
            ctx['vix'] is not None and state.get('day_open_vix')
            and ctx['vix'] >= state['day_open_vix'] * (1 + vix_rise_pct / 100.0)
        )

        buy_needs_reset  = bool(state.get('buy_needs_reset', False))
        sell_needs_reset = bool(state.get('sell_needs_reset', False))

        if sell_needs_reset and spot >= rng['lower_bound']:
            sell_needs_reset = False
        if buy_needs_reset and spot <= rng['upper_bound']:
            buy_needs_reset = False

        # Opposite side must surrender its intrinsic value ("the oncoming
        # vehicle gives way"): while it still prices in a return to the
        # synthetic previous close, the breakout isn't clean.
        ce_lower_intrinsic = float(rng.get('ce_lower_intrinsic', 0) or 0)
        pe_upper_intrinsic = float(rng.get('pe_upper_intrinsic', 0) or 0)
        ce_gave_way = ctx['ce_lower'] is not None and ctx['ce_lower'] < ce_lower_intrinsic
        pe_gave_way = ctx['pe_upper'] is not None and ctx['pe_upper'] < pe_upper_intrinsic

        signal: Optional[str] = None
        entry_kind = 'BREAKOUT'
        sl_override: Optional[float] = None

        if (spot < rng['lower_bound'] and ctx['pe_upper'] is not None
                and ctx['pe_upper'] >= rng['total_range'] and ce_gave_way
                and expansion_ok and vix_ok
                and not sell_needs_reset):
            signal = 'SELL'
            sell_needs_reset = True
        elif (spot > rng['upper_bound'] and ctx['ce_lower'] is not None
                and ctx['ce_lower'] >= rng['total_range'] and pe_gave_way
                and expansion_ok and vix_ok
                and not buy_needs_reset):
            signal = 'BUY'
            buy_needs_reset = True
        # Reclaim entries (gap days only). Gap-down: spot sustained above the
        # low-reclaim level while the opposite upper PE fails to hold the full
        # active range (24400 PE < 400 → 24000 CE regains the power to go back
        # ITM → appreciation). No VIX/expansion gate — on a covering rally a
        # rising VIX is seller panic fueling the move, not a short signal.
        elif (rng.get('gap_side') == 'down' and ctx['sustained_above_low']
                and ctx['pe_upper'] is not None and ctx['pe_upper'] < rng['total_range']
                and not state.get('buy_reclaim_done')):
            signal, entry_kind = 'BUY', 'RECLAIM'
            sl_override = state.get('low_reclaim_level')
            state['buy_reclaim_done'] = True
        elif (rng.get('gap_side') == 'up' and ctx['sustained_below_high']
                and ctx['ce_lower'] is not None and ctx['ce_lower'] < rng['total_range']
                and not state.get('sell_reclaim_done')):
            signal, entry_kind = 'SELL', 'RECLAIM'
            sl_override = state.get('high_reclaim_level')
            state['sell_reclaim_done'] = True

        state['buy_needs_reset']  = buy_needs_reset
        state['sell_needs_reset'] = sell_needs_reset

        if signal:
            self.log.info(
                f"✓ Signal={signal} kind={entry_kind} spot={spot} "
                f"range=[{int(rng['lower_bound'])}, {int(rng['upper_bound'])}] gap={rng.get('gap_side')} "
                f"live_common_atm={ctx['live_common_atm']} (baseline {setup['common_atm']}) "
                f"vix={ctx['vix']} (open {state.get('day_open_vix')}) "
                f"pe_upper={ctx['pe_upper']} (intrinsic {pe_upper_intrinsic}) "
                f"ce_lower={ctx['ce_lower']} (intrinsic {ce_lower_intrinsic}) "
                f"low_reclaim={state.get('low_reclaim_level')} high_reclaim={state.get('high_reclaim_level')}"
            )
        return signal, entry_kind, sl_override

    # ── Entry discipline ─────────────────────────────────────────────────────

    def _trades_today(self) -> int:
        try:
            with open(_HISTORY_FILE, 'r') as f:
                history = json.load(f)
            if not isinstance(history, list):
                return 0
            today = date.today().isoformat()
            return sum(1 for t in history if isinstance(t, dict) and t.get('date') == today)
        except Exception:
            return 0

    def _entries_allowed(self) -> bool:
        """One strategy, one disciplined trade, formed within the morning cutoff."""
        now = datetime.now()
        cutoff = self._uvar('INTRINSIC_RANGE_ENTRY_CUTOFF', '10:00') or '10:00'
        try:
            ch, cm = (int(x) for x in cutoff.split(':')[:2])
        except Exception:
            ch, cm = 10, 0
        if (now.hour, now.minute) >= (ch, cm):
            return False
        max_trades = max(1, int(self._uvar('INTRINSIC_RANGE_MAX_TRADES_PER_DAY', '1') or '1'))
        return self._trades_today() < max_trades

    # ── Trade lifecycle (paper) ──────────────────────────────────────────────

    def _enter_paper_trade(self, direction: str, spot: float, provider: Any,
                           entry_kind: str = 'BREAKOUT',
                           sl_level_override: Optional[float] = None) -> None:
        opt_type = 'CE' if direction == 'BUY' else 'PE'
        atm_strike = round(spot / _STRIKE_STEP) * _STRIKE_STEP
        inst = self._find_option(self._instruments, atm_strike, opt_type, self._expiry)
        if inst is None:
            self.log.error(f"No {opt_type} instrument found at strike {atm_strike} — skipping entry")
            return

        fyers_sym = inst.get('instrument_token', '')
        entry_premium = self._get_ltp_one(provider, fyers_sym)

        setup = self._daily_setup or {}
        range_half = float(setup.get('range_half', _STRIKE_STEP))
        sl_points  = float(self._uvar('INTRINSIC_RANGE_SL_POINTS', '') or (range_half / 2))
        tgt_points = float(self._uvar('INTRINSIC_RANGE_TGT_POINTS', '') or range_half)
        # Reclaim trades stop at the reclaim level itself: back below it the
        # buying (or above it the selling) effect is gone.
        if sl_level_override is not None:
            sl_level = round(float(sl_level_override), 2)
        else:
            sl_level = round(spot - sl_points, 2) if direction == 'BUY' else round(spot + sl_points, 2)
        tgt_level = round(spot + tgt_points, 2) if direction == 'BUY' else round(spot - tgt_points, 2)

        lots = max(1, int(self._uvar('INTRINSIC_RANGE_LOTS', '1') or '1'))
        qty  = lots * self._lot_size

        state = self._load_state()
        state['active_trade'] = {
            'mode':           self._mode(),
            'direction':      direction,
            'entry_kind':     entry_kind,
            'entry_spot':     spot,
            'entry_time':     datetime.now().isoformat(),
            'sl_level':       sl_level,
            'target_level':   tgt_level,
            'strike':         int(atm_strike),
            'option_type':    opt_type,
            'fyers_sym':      fyers_sym,
            'lot_size':       self._lot_size,
            'lots':           lots,
            'quantity':       qty,
            'entry_premium':  entry_premium,
            'opt_entry_price': entry_premium,  # alias: feeds the shared "Active Trades — All Algos" tab
            'daily_setup':    setup,
        }
        self._save_state(state)
        self.log.info(
            f"[PAPER] ENTERED {direction} ({entry_kind}): spot={spot} {int(atm_strike)}{opt_type} "
            f"premium={entry_premium} sl={sl_level} tgt={tgt_level} qty={qty}"
        )

    def _exit_paper_trade(self, reason: str, spot: float, provider: Any) -> None:
        state = self._load_state()
        trade = state.get('active_trade')
        if not trade:
            return
        exit_premium = self._get_ltp_one(provider, trade.get('fyers_sym', ''))
        self._append_history(trade, spot, reason, exit_premium)
        state['active_trade'] = None
        self._save_state(state)
        self.log.info(f"[PAPER] EXIT {trade.get('direction')} reason={reason} spot={spot} premium={exit_premium}")

    # ── Monitor loop ─────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = datetime.now()
                if now.weekday() >= 5:
                    time.sleep(60)
                    continue

                h, m = now.hour, now.minute
                if h < 9 or (h == 9 and m < 16):
                    time.sleep(5)
                    continue

                provider = self._get_provider()
                if not provider:
                    time.sleep(5)
                    continue

                if h > 15 or (h == 15 and m >= 20):
                    state = self._load_state()
                    if state.get('active_trade'):
                        spot = self._get_nifty_spot(provider) or state['active_trade']['entry_spot']
                        self._exit_paper_trade('EOD', spot, provider)
                    self.log.info("EOD reached — stopping monitor thread for today")
                    break

                if self._expiry is None or not self._instruments:
                    self._instruments, self._expiry, self._lot_size = self._get_instruments_and_expiry(provider)

                state = self._load_state()
                today_str = date.today().isoformat()
                stale_schema = bool(state.get('daily_setup')) and state['daily_setup'].get('schema') != 4
                if state.get('date') != today_str or not state.get('daily_setup') or stale_schema:
                    setup = self._compute_daily_setup(provider)
                    if not setup:
                        time.sleep(15)
                        continue
                    state['date']                     = today_str
                    state['daily_setup']              = setup
                    state['day_open_vix']             = None
                    state['day_open_spot']            = None
                    state['active_range']             = None
                    state['ce_boundary_day_low']      = None
                    state['pe_boundary_day_low']      = None
                    state['low_reclaim_level']        = None
                    state['high_reclaim_level']       = None
                    state['above_low_reclaim_polls']  = 0
                    state['below_high_reclaim_polls'] = 0
                    state['buy_reclaim_done']         = False
                    state['sell_reclaim_done']        = False
                    state['buy_needs_reset']          = False
                    state['sell_needs_reset']         = False
                    self._save_state(state)

                self._daily_setup = state['daily_setup']

                ctx = self._update_tracking(provider, state)
                if ctx is None:
                    self._save_state(state)
                    time.sleep(self._poll_secs)
                    continue

                trade = state.get('active_trade')
                if trade:
                    # Persist tracking before exit handling: _exit_paper_trade
                    # reloads state from disk, so an unsaved in-memory copy
                    # here would clobber the exit.
                    self._save_state(state)
                    spot = ctx['spot']
                    rng  = ctx['rng']
                    direction  = trade['direction']
                    entry_kind = trade.get('entry_kind', 'BREAKOUT')
                    hit_sl  = spot <= trade['sl_level']  if direction == 'BUY' else spot >= trade['sl_level']
                    hit_tgt = spot >= trade['target_level'] if direction == 'BUY' else spot <= trade['target_level']
                    # A reclaim trade lives inside the range by design — only
                    # breakout trades exit on returning into the range.
                    in_range = (entry_kind == 'BREAKOUT'
                                and rng['lower_bound'] <= spot <= rng['upper_bound'])
                    if hit_sl:
                        self._exit_paper_trade('SL', spot, provider)
                    elif hit_tgt:
                        self._exit_paper_trade('TARGET', spot, provider)
                    elif in_range:
                        self._exit_paper_trade('RANGE_RECLAIM', spot, provider)
                else:
                    active_enabled = self._uvar('EMA_INTRINSIC_RANGE_ACTIVE', 'false').lower() == 'true'
                    signal, entry_kind, sl_override = self._check_signal(ctx, state)
                    self._save_state(state)
                    if active_enabled and signal:
                        if self._entries_allowed():
                            self._enter_paper_trade(signal, ctx['spot'], provider,
                                                    entry_kind=entry_kind,
                                                    sl_level_override=sl_override)
                        else:
                            self.log.info(
                                f"Signal {signal} ({entry_kind}) skipped — entry cutoff passed "
                                f"or max trades/day reached"
                            )
            except Exception as e:
                self.log.error(f"Monitor loop error: {e}", exc_info=True)

            time.sleep(self._poll_secs)

        self.log.info("Monitor thread stopped")
