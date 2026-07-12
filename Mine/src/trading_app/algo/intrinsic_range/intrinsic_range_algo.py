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
  INTRINSIC_RANGE_EXPANSION_MULT  = 1.3           (live common-ATM premium must be >= 1.3x the day's baseline)
  INTRINSIC_RANGE_VIX_RISE_PCT    = 5.0           (India VIX must be >= +5% vs the day's opening reading)
"""
import json
import logging
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
            range_half = max(_STRIKE_STEP, round(common_atm / _STRIKE_STEP) * _STRIKE_STEP)
            lower_bound = atm_strike - range_half
            upper_bound = atm_strike + range_half
            total_range = 2 * range_half

            setup = {
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

    # ── Signal detection ─────────────────────────────────────────────────────

    def _check_signal(
        self, provider: Any, setup: Dict[str, Any], state: Dict[str, Any],
    ) -> Tuple[Optional[str], Optional[float]]:
        """Mutates state (needs-reset flags, day_open_vix) in place. Returns (signal, spot)."""
        spot = self._get_nifty_spot(provider)
        if spot is None:
            return None, None

        chain = self._get_live_chain(provider)
        if not chain:
            return None, spot

        vix = self._get_vix(provider)
        if state.get('day_open_vix') is None and vix is not None:
            state['day_open_vix'] = vix

        live_atm = round(spot / _STRIKE_STEP) * _STRIKE_STEP
        ce_live = (chain.get((int(live_atm), 'CE')) or {}).get('ltp')
        pe_live = (chain.get((int(live_atm), 'PE')) or {}).get('ltp')
        live_common_atm = (ce_live + pe_live) / 2 if ce_live and pe_live else None

        pe_upper = (chain.get((int(setup['upper_bound']), 'PE')) or {}).get('ltp')
        ce_lower = (chain.get((int(setup['lower_bound']), 'CE')) or {}).get('ltp')

        expansion_mult = float(self._uvar('INTRINSIC_RANGE_EXPANSION_MULT', '1.3') or '1.3')
        vix_rise_pct   = float(self._uvar('INTRINSIC_RANGE_VIX_RISE_PCT', '5.0') or '5.0')

        expansion_ok = (
            live_common_atm is not None and setup['common_atm'] > 0
            and (live_common_atm / setup['common_atm']) >= expansion_mult
        )
        vix_ok = (
            vix is not None and state.get('day_open_vix')
            and vix >= state['day_open_vix'] * (1 + vix_rise_pct / 100.0)
        )

        buy_needs_reset  = bool(state.get('buy_needs_reset', False))
        sell_needs_reset = bool(state.get('sell_needs_reset', False))

        if sell_needs_reset and spot >= setup['lower_bound']:
            sell_needs_reset = False
        if buy_needs_reset and spot <= setup['upper_bound']:
            buy_needs_reset = False

        # Opposite side must surrender its intrinsic value ("the oncoming
        # vehicle gives way"): while it still prices in a return to the
        # synthetic previous close, the breakout isn't clean.
        ce_lower_intrinsic = float(setup.get('ce_lower_intrinsic', 0) or 0)
        pe_upper_intrinsic = float(setup.get('pe_upper_intrinsic', 0) or 0)
        ce_gave_way = ce_lower is not None and ce_lower < ce_lower_intrinsic
        pe_gave_way = pe_upper is not None and pe_upper < pe_upper_intrinsic

        signal: Optional[str] = None
        if (spot < setup['lower_bound'] and pe_upper is not None
                and pe_upper >= setup['total_range'] and ce_gave_way
                and expansion_ok and vix_ok
                and not sell_needs_reset):
            signal = 'SELL'
            sell_needs_reset = True
        elif (spot > setup['upper_bound'] and ce_lower is not None
                and ce_lower >= setup['total_range'] and pe_gave_way
                and expansion_ok and vix_ok
                and not buy_needs_reset):
            signal = 'BUY'
            buy_needs_reset = True

        state['buy_needs_reset']  = buy_needs_reset
        state['sell_needs_reset'] = sell_needs_reset

        if signal:
            self.log.info(
                f"✓ Signal={signal} spot={spot} live_common_atm={live_common_atm} "
                f"(baseline {setup['common_atm']}) vix={vix} (open {state.get('day_open_vix')}) "
                f"pe_upper={pe_upper} (intrinsic {pe_upper_intrinsic}) "
                f"ce_lower={ce_lower} (intrinsic {ce_lower_intrinsic})"
            )
        return signal, spot

    # ── Trade lifecycle (paper) ──────────────────────────────────────────────

    def _enter_paper_trade(self, direction: str, spot: float, provider: Any) -> None:
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
        sl_level  = round(spot - sl_points, 2)  if direction == 'BUY' else round(spot + sl_points, 2)
        tgt_level = round(spot + tgt_points, 2) if direction == 'BUY' else round(spot - tgt_points, 2)

        lots = max(1, int(self._uvar('INTRINSIC_RANGE_LOTS', '1') or '1'))
        qty  = lots * self._lot_size

        state = self._load_state()
        state['active_trade'] = {
            'mode':           self._mode(),
            'direction':      direction,
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
            f"[PAPER] ENTERED {direction}: spot={spot} {int(atm_strike)}{opt_type} "
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
                stale_schema = bool(state.get('daily_setup')) and 'ce_lower_intrinsic' not in state['daily_setup']
                if state.get('date') != today_str or not state.get('daily_setup') or stale_schema:
                    setup = self._compute_daily_setup(provider)
                    if not setup:
                        time.sleep(15)
                        continue
                    state['date']             = today_str
                    state['daily_setup']      = setup
                    state['day_open_vix']     = None
                    state['buy_needs_reset']  = False
                    state['sell_needs_reset'] = False
                    self._save_state(state)

                self._daily_setup = state['daily_setup']

                trade = state.get('active_trade')
                if trade:
                    spot = self._get_nifty_spot(provider)
                    if spot is not None:
                        direction = trade['direction']
                        hit_sl  = spot <= trade['sl_level']  if direction == 'BUY' else spot >= trade['sl_level']
                        hit_tgt = spot >= trade['target_level'] if direction == 'BUY' else spot <= trade['target_level']
                        in_range = self._daily_setup['lower_bound'] <= spot <= self._daily_setup['upper_bound']
                        if hit_sl:
                            self._exit_paper_trade('SL', spot, provider)
                        elif hit_tgt:
                            self._exit_paper_trade('TARGET', spot, provider)
                        elif in_range:
                            self._exit_paper_trade('RANGE_RECLAIM', spot, provider)
                else:
                    active_enabled = self._uvar('EMA_INTRINSIC_RANGE_ACTIVE', 'false').lower() == 'true'
                    signal, _spot = self._check_signal(provider, self._daily_setup, state)
                    self._save_state(state)
                    if active_enabled and signal and _spot is not None:
                        self._enter_paper_trade(signal, _spot, provider)
            except Exception as e:
                self.log.error(f"Monitor loop error: {e}", exc_info=True)

            time.sleep(self._poll_secs)

        self.log.info("Monitor thread stopped")
