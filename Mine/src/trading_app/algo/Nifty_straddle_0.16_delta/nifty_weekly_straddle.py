"""NIFTY Weekly Straddle — delta 0.16 entry, combined SL, Tuesday 3:15 PM rollover."""
import json
import logging
import os
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from trading_app.service.greeks_calculator import GreeksCalculator

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(__file__), 'straddle_state.json')
_NIFTY_FYERS_SYMBOL = 'NSE:NIFTY50-INDEX'

_monitor_thread: Optional[threading.Thread] = None
_stop_event: threading.Event = threading.Event()
_state_lock = threading.Lock()


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
    try:
        with open(_STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {'active': False}


def _save_state(state: Dict[str, Any]) -> None:
    with _state_lock:
        try:
            with open(_STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"[Straddle] Failed to save state: {e}")


# ── Instruments helpers (all derived from broker data, nothing hardcoded) ─────

def _get_expiries(instruments: List[Dict]) -> Tuple[date, date]:
    today = date.today()
    expiry_dates = sorted({
        inst['expiry'] for inst in instruments
        if (inst.get('name') or '').upper() == 'NIFTY'
        and inst.get('instrument_type') in ('CE', 'PE')
        and inst.get('expiry') is not None
        and inst['expiry'] >= today
    })
    if len(expiry_dates) < 2:
        raise ValueError(f"Not enough NIFTY expiry dates: {expiry_dates}")
    return expiry_dates[0], expiry_dates[1]


def _get_lot_size(instruments: List[Dict], expiry: date) -> int:
    for inst in instruments:
        if (inst.get('name') or '').upper() == 'NIFTY' and inst.get('expiry') == expiry:
            return int(inst.get('lot_size') or 1)
    logger.warning("[Straddle] Lot size not found in instruments")
    return 1


def _get_strike_step(instruments: List[Dict], expiry: date) -> float:
    strikes = sorted({
        inst['strike'] for inst in instruments
        if (inst.get('name') or '').upper() == 'NIFTY'
        and inst.get('instrument_type') == 'CE'
        and inst.get('expiry') == expiry
        and (inst.get('strike') or 0) > 0
    })
    if len(strikes) >= 2:
        return strikes[1] - strikes[0]
    logger.warning("[Straddle] Strike step not derivable from instruments")
    return 50.0


def _find_option(
    instruments: List[Dict], strike: float, opt_type: str, expiry: date
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


# ── Delta selection ───────────────────────────────────────────────────────────

def _select_delta_strike(
    opt_type: str,
    underlying: float,
    expiry: date,
    provider: Any,
    instruments: List[Dict],
    delta_target: float,
    strike_step: float,
) -> Tuple[float, Optional[Dict]]:
    """Scan ±15 strikes from ATM; return the one whose abs(delta) is closest to delta_target."""
    atm = round(underlying / strike_step) * strike_step
    candidates = [atm + i * strike_step for i in range(-15, 16)]

    best_strike = atm
    best_inst: Optional[Dict] = None
    best_diff = float('inf')

    for strike in candidates:
        inst = _find_option(instruments, strike, opt_type, expiry)
        if not inst:
            continue
        fyers_sym = inst.get('instrument_token')
        if not fyers_sym:
            continue
        try:
            ltp_data = provider.ltp([fyers_sym])
            ltp = ltp_data.get(fyers_sym, {}).get('last_price', 0)
            if ltp < 0.5:
                continue
            greeks = GreeksCalculator.calculate_greeks(opt_type, ltp, underlying, strike, expiry)
            d = greeks.get('Delta', 0)
            # CE delta must be positive; PE delta must be negative
            if opt_type.upper() == 'CE' and d <= 0:
                continue
            if opt_type.upper() == 'PE' and d >= 0:
                continue
            signed_target = delta_target if opt_type.upper() == 'CE' else -delta_target
            diff = abs(d - signed_target)
            if diff < best_diff:
                best_diff = diff
                best_strike = strike
                best_inst = inst
        except Exception as e:
            logger.debug(f"[Straddle] Greeks failed for {opt_type} {strike}: {e}")

    return best_strike, best_inst


# ── SL monitor ────────────────────────────────────────────────────────────────

def _start_monitoring(provider: Any, broker_kite: Any, username: str, broker_instance: int) -> None:
    global _monitor_thread, _stop_event
    _stop_event.set()
    time.sleep(0.3)
    _stop_event = threading.Event()
    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=(provider, broker_kite, username, broker_instance),
        daemon=True,
        name='straddle-sl-monitor',
    )
    _monitor_thread.start()
    logger.info("[Straddle] SL monitor thread started")


def _monitor_loop(provider: Any, broker_kite: Any, username: str, broker_instance: int) -> None:
    logger.info("[Straddle] SL monitor running")
    while not _stop_event.is_set():
        try:
            state = _load_state()
            if not state.get('active'):
                logger.info("[Straddle] Monitor: straddle inactive — stopping")
                break
            ce_sym = state.get('ce_fyers_symbol')
            pe_sym = state.get('pe_fyers_symbol')
            if not ce_sym or not pe_sym:
                break
            ltps = provider.ltp([ce_sym, pe_sym])
            ce_ltp = ltps.get(ce_sym, {}).get('last_price', 0)
            pe_ltp = ltps.get(pe_sym, {}).get('last_price', 0)
            if ce_ltp <= 0 or pe_ltp <= 0:
                time.sleep(2)
                continue
            combined = ce_ltp + pe_ltp
            sl_trigger = state.get('sl_trigger', float('inf'))
            if combined >= sl_trigger:
                logger.warning(f"[Straddle] SL triggered! combined={combined:.2f} >= sl={sl_trigger:.2f}")
                _do_exit(broker_kite, username, broker_instance, 'SL_HIT')
                break
        except Exception as e:
            logger.error(f"[Straddle] Monitor error: {e}")
        time.sleep(2)
    logger.info("[Straddle] SL monitor stopped")


# ── Exit ──────────────────────────────────────────────────────────────────────

def _do_exit(broker_kite: Any, username: str, broker_instance: int, reason: str) -> Dict[str, Any]:
    global _stop_event
    _stop_event.set()

    state = _load_state()
    if not state.get('active'):
        return {'success': False, 'error': 'No active straddle'}

    try:
        from trading_app.app.utils.user_env import UserEnvManager
        product_type = (
            UserEnvManager.get_user_var(username, f'BROKER_{broker_instance}_PRODUCT_TYPE') or 'MIS'
        ).upper()

        quantity = state.get('lots', 1) * state.get('lot_size', 1)
        exit_orders: Dict[str, str] = {}

        for leg_ts in [state.get('ce_kite_tradingsymbol'), state.get('pe_kite_tradingsymbol')]:
            if not leg_ts:
                continue
            try:
                order_id = broker_kite.place_order(
                    variety='regular',
                    exchange='NFO',
                    tradingsymbol=leg_ts,
                    transaction_type='BUY',
                    quantity=quantity,
                    product=product_type,
                    order_type='MARKET',
                )
                exit_orders[leg_ts] = str(order_id)
                logger.info(f"[Straddle] Closed {leg_ts} → order_id={order_id}")
            except Exception as e:
                logger.error(f"[Straddle] Error closing {leg_ts}: {e}")
                exit_orders[leg_ts] = f'ERROR: {e}'

        state['active'] = False
        state['exit_time'] = datetime.now().isoformat()
        state['exit_reason'] = reason
        state['exit_orders'] = exit_orders
        _save_state(state)
        logger.info(f"[Straddle] Exited ({reason}) — {exit_orders}")
        return {'success': True, 'reason': reason, 'exit_orders': exit_orders}

    except Exception as e:
        logger.error(f"[Straddle] Exit error: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


# ── Main class ────────────────────────────────────────────────────────────────

class NiftyWeeklyStraddle:
    def __init__(self, provider: Any, broker_kite: Any, broker_instance: int, username: str):
        self.provider = provider
        self.broker_kite = broker_kite
        self.broker_instance = broker_instance
        self.username = username

    def _config(self) -> Tuple[float, float, int, str]:
        from trading_app.app.utils.user_env import UserEnvManager
        n = self.broker_instance
        u = self.username
        delta_target = float(UserEnvManager.get_user_var(u, f'BROKER_{n}_STRADDLE_DELTA') or 0.16)
        sl_cap = float(UserEnvManager.get_user_var(u, f'BROKER_{n}_STRADDLE_SL_CAP') or 8300)
        lots = int(UserEnvManager.get_user_var(u, f'BROKER_{n}_STRADDLE_LOTS') or 1)
        product_type = (UserEnvManager.get_user_var(u, f'BROKER_{n}_PRODUCT_TYPE') or 'MIS').upper()
        return delta_target, sl_cap, lots, product_type

    def preview(self) -> Dict[str, Any]:
        """Compute expected strikes and premium without placing orders."""
        try:
            delta_target, sl_cap, lots, _ = self._config()
            instruments = self.provider.instruments('NFO')
            current_expiry, next_expiry = _get_expiries(instruments)

            # On expiry day today's options have T≈0 — greeks are unstable.
            # The new straddle is always for the NEXT week's expiry.
            trade_expiry = next_expiry if current_expiry == date.today() else current_expiry

            lot_size = _get_lot_size(instruments, trade_expiry)
            strike_step = _get_strike_step(instruments, trade_expiry)

            underlying_data = self.provider.ltp([_NIFTY_FYERS_SYMBOL])
            underlying = underlying_data.get(_NIFTY_FYERS_SYMBOL, {}).get('last_price', 0)
            if not underlying:
                return {'success': False, 'error': 'Could not fetch NIFTY spot price'}

            ce_strike, ce_inst = _select_delta_strike(
                'CE', underlying, trade_expiry, self.provider, instruments, delta_target, strike_step)
            pe_strike, pe_inst = _select_delta_strike(
                'PE', underlying, trade_expiry, self.provider, instruments, delta_target, strike_step)

            ce_sym = ce_inst.get('instrument_token') if ce_inst else None
            pe_sym = pe_inst.get('instrument_token') if pe_inst else None

            ce_ltp = self.provider.ltp([ce_sym]).get(ce_sym, {}).get('last_price', 0) if ce_sym else 0
            pe_ltp = self.provider.ltp([pe_sym]).get(pe_sym, {}).get('last_price', 0) if pe_sym else 0
            combined = ce_ltp + pe_ltp
            sl_trigger = round(combined + min(combined, sl_cap / lot_size), 2) if combined > 0 else 0

            return {
                'success': True,
                'underlying': underlying,
                'current_expiry': str(trade_expiry),
                'next_expiry': str(next_expiry) if trade_expiry == current_expiry else '',
                'lot_size': lot_size,
                'lots': lots,
                'delta_target': delta_target,
                'sl_cap': sl_cap,
                'ce_strike': ce_strike,
                'pe_strike': pe_strike,
                'ce_symbol': ce_sym,
                'pe_symbol': pe_sym,
                'ce_ltp': ce_ltp,
                'pe_ltp': pe_ltp,
                'combined_premium': round(combined, 2),
                'sl_trigger': sl_trigger,
            }
        except Exception as e:
            logger.error(f"[Straddle] Preview error: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def enter_straddle(self, force: bool = False) -> Dict[str, Any]:
        """Select delta strikes, sell both legs, persist state, start SL monitor."""
        state = _load_state()
        if state.get('active') and not force:
            return {'success': False, 'error': 'Straddle already active. Use force=True to override.'}

        try:
            delta_target, sl_cap, lots, product_type = self._config()
            instruments = self.provider.instruments('NFO')
            current_expiry, next_expiry = _get_expiries(instruments)

            # On expiry day today's options have T≈0 — greeks are unstable.
            # The new straddle is always for the NEXT week's expiry.
            trade_expiry = next_expiry if current_expiry == date.today() else current_expiry

            lot_size = _get_lot_size(instruments, trade_expiry)
            strike_step = _get_strike_step(instruments, trade_expiry)

            underlying_data = self.provider.ltp([_NIFTY_FYERS_SYMBOL])
            underlying = underlying_data.get(_NIFTY_FYERS_SYMBOL, {}).get('last_price', 0)
            if not underlying:
                return {'success': False, 'error': 'Could not fetch NIFTY spot price'}

            ce_strike, ce_inst = _select_delta_strike(
                'CE', underlying, trade_expiry, self.provider, instruments, delta_target, strike_step)
            pe_strike, pe_inst = _select_delta_strike(
                'PE', underlying, trade_expiry, self.provider, instruments, delta_target, strike_step)

            if not ce_inst or not pe_inst:
                return {'success': False, 'error': 'Could not select delta strikes'}

            ce_fyers_sym = ce_inst['instrument_token']
            pe_fyers_sym = pe_inst['instrument_token']
            ce_kite_ts = ce_inst['tradingsymbol']
            pe_kite_ts = pe_inst['tradingsymbol']

            ltps = self.provider.ltp([ce_fyers_sym, pe_fyers_sym])
            ce_entry_ltp = ltps.get(ce_fyers_sym, {}).get('last_price', 0)
            pe_entry_ltp = ltps.get(pe_fyers_sym, {}).get('last_price', 0)
            quantity = lots * lot_size

            ce_order_id = self.broker_kite.place_order(
                variety='regular', exchange='NFO', tradingsymbol=ce_kite_ts,
                transaction_type='SELL', quantity=quantity,
                product=product_type, order_type='MARKET',
            )
            pe_order_id = self.broker_kite.place_order(
                variety='regular', exchange='NFO', tradingsymbol=pe_kite_ts,
                transaction_type='SELL', quantity=quantity,
                product=product_type, order_type='MARKET',
            )

            combined_entry = ce_entry_ltp + pe_entry_ltp
            sl_trigger = round(combined_entry + min(combined_entry, sl_cap / lot_size), 2)

            new_state: Dict[str, Any] = {
                'active': True,
                'broker_instance': self.broker_instance,
                'username': self.username,
                'ce_strike': ce_strike,
                'ce_fyers_symbol': ce_fyers_sym,
                'ce_kite_tradingsymbol': ce_kite_ts,
                'ce_order_id': str(ce_order_id),
                'pe_strike': pe_strike,
                'pe_fyers_symbol': pe_fyers_sym,
                'pe_kite_tradingsymbol': pe_kite_ts,
                'pe_order_id': str(pe_order_id),
                'current_expiry': str(trade_expiry),
                'next_expiry': str(next_expiry) if trade_expiry == current_expiry else '',
                'underlying_at_entry': underlying,
                'ce_entry_ltp': ce_entry_ltp,
                'pe_entry_ltp': pe_entry_ltp,
                'combined_entry': combined_entry,
                'sl_trigger': sl_trigger,
                'lots': lots,
                'lot_size': lot_size,
                'entry_time': datetime.now().isoformat(),
                'exit_time': None,
                'exit_reason': None,
            }
            _save_state(new_state)
            logger.info(
                f"[Straddle] Entered: CE {ce_kite_ts}@{ce_entry_ltp} "
                f"PE {pe_kite_ts}@{pe_entry_ltp} SL={sl_trigger}"
            )
            _start_monitoring(self.provider, self.broker_kite, self.username, self.broker_instance)
            return {'success': True, 'state': new_state}

        except Exception as e:
            logger.error(f"[Straddle] Entry error: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def exit_straddle(self, reason: str = 'MANUAL') -> Dict[str, Any]:
        return _do_exit(self.broker_kite, self.username, self.broker_instance, reason)

    def rollover_or_enter(self) -> Dict[str, Any]:
        """Called at 3:15 PM on expiry Tuesday: exit current week and enter next."""
        state = _load_state()
        if state.get('active'):
            logger.info("[Straddle] Rollover: closing current week")
            _do_exit(self.broker_kite, self.username, self.broker_instance, 'ROLLOVER')
        return self.enter_straddle(force=True)

    @staticmethod
    def get_status(provider: Any) -> Dict[str, Any]:
        """Return state + live LTPs and P&L."""
        state = _load_state()
        if not state.get('active'):
            return {'success': True, 'active': False, 'state': state}
        try:
            ce_sym = state.get('ce_fyers_symbol')
            pe_sym = state.get('pe_fyers_symbol')
            if ce_sym and pe_sym:
                ltps = provider.ltp([ce_sym, pe_sym])
                ce_ltp = ltps.get(ce_sym, {}).get('last_price', 0)
                pe_ltp = ltps.get(pe_sym, {}).get('last_price', 0)
                combined_current = ce_ltp + pe_ltp
                combined_entry = state.get('combined_entry', 0)
                lot_size = state.get('lot_size', 1)
                lots = state.get('lots', 1)
                pnl_per_lot = (combined_entry - combined_current) * lot_size
                return {
                    'success': True,
                    'active': True,
                    'state': state,
                    'live': {
                        'ce_ltp': ce_ltp,
                        'pe_ltp': pe_ltp,
                        'combined_current': round(combined_current, 2),
                        'pnl_per_lot': round(pnl_per_lot, 2),
                        'pnl_total': round(pnl_per_lot * lots, 2),
                        'sl_trigger': state.get('sl_trigger'),
                        'sl_distance': round(state.get('sl_trigger', 0) - combined_current, 2),
                    },
                }
        except Exception as e:
            logger.warning(f"[Straddle] Status LTP fetch failed: {e}")
        return {'success': True, 'active': True, 'state': state, 'live': None}
