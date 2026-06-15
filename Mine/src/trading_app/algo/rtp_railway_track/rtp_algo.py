"""
RTP Railway Track Algo Trader
Monitors NIFTY 1-min candles every second during market hours.
On a BUY signal → buys the CE option with delta ~0.90.
On a SELL signal → buys the PE option with delta ~0.90.
Exits when NIFTY spot crosses SL (-30 pts) or Target (+90 pts) from entry spot.
Multiple re-entries allowed per day. Auto-started at 9:15 AM by the scheduler.

Env vars required in Mine.env:
  EMA_RTP_ACTIVE=true
  BROKER_N_RTP_ACTIVE=true/false   (alongside BROKER_N_ACTIVE)
  BROKER_N_RTP_LOTS=1              (per-broker lot count)
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from trading_app.service.greeks_calculator import GreeksCalculator

logger = logging.getLogger(__name__)

_STATE_FILE     = os.path.join(os.path.dirname(__file__), 'rtp_state.json')
_HISTORY_FILE   = os.path.join(os.path.dirname(__file__), 'rtp_trades_history.json')

_SL_POINTS      = 30.0
_TGT_POINTS     = 90.0
_DELTA_TARGET   = 0.90
_STRIKE_STEP    = 50.0
_SLOPE_BARS     = 8
_NIFTY_FYERS    = 'NSE:NIFTY50-INDEX'
_WARMUP_BARS    = 200  # Must match backtest warmup — EMA50 needs ~200 1-min bars to converge
_LOOKBACK_DAYS  = 5    # Calendar days to look back for warmup candles (covers weekends/holidays)
_MAX_SPOT_FAILS = 60   # consecutive None returns before CRITICAL log (~1 minute of data gap)

# Module-level registry so the API can reach the running instance without storing it in the scheduler
_instances: Dict[str, 'RTPAlgo'] = {}


def get_instance(username: str) -> Optional['RTPAlgo']:
    return _instances.get(username)


class RTPAlgo:
    """Live RTP Railway Track signal detector and option order executor."""

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

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name='rtp-algo-monitor',
        )
        self._thread.start()
        _instances[self.username] = self
        logger.info("[RTP] Monitoring thread started")

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop(self.username, None)
        logger.info("[RTP] Stop requested")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── State ─────────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(_STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {'active_trade': None, 'buy_needs_reset': False, 'sell_needs_reset': False}

    def _save_state(self, state: Dict[str, Any]) -> None:
        with self._state_lock:
            try:
                with open(_STATE_FILE, 'w') as f:
                    json.dump(state, f, indent=2, default=str)
            except Exception as e:
                logger.error(f"[RTP] State save failed: {e}")

    def _append_history(self, trade: Dict[str, Any], exit_spot: float, reason: str,
                         opt_exit_price: Optional[float] = None) -> None:
        """Append a completed trade record to rtp_trades_history.json (today only, latest-first)."""
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
                total_qty = sum(
                    e.get('quantity', 0) for e in trade.get('broker_entries', [])
                )
                opt_pnl_inr = round(opt_pnl_pts * total_qty, 2) if total_qty else None

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

            with open(_HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"[RTP] History append failed: {e}")

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
            logger.warning(f"[RTP] Spot fetch failed: {e}")
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
                interval='minute',
                use_cache=False,
            )
            if not candles:
                return None
            return pd.DataFrame(candles).reset_index(drop=True)
        except Exception as e:
            logger.warning(f"[RTP] Candle fetch failed: {e}")
            return None

    # ── Signal detection ──────────────────────────────────────────────────────

    def _check_rtp_signal(
        self, df: pd.DataFrame, state: Dict[str, Any]
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Run indicator preparation on candles and check the last completed bar.
        Updates buy/sell_needs_reset in state and returns (signal, updated_state).
        Signal is 'BUY', 'SELL', or None.
        """
        try:
            from trading_app.Backtest.rtp_backtest_engine import RTPBacktestEngine
            engine = RTPBacktestEngine(
                df=df,
                entry_mode='RTP(20 & 9)',
                interval_minutes=1,
                slope_bars=_SLOPE_BARS,
                use_adx=False,
                sl_points=_SL_POINTS,
                tgt_points=_TGT_POINTS,
            )
            processed = engine.df
            if len(processed) < 2:
                return None, state

            row = processed.iloc[-1]
            buy_needs_reset  = bool(state.get('buy_needs_reset',  False))
            sell_needs_reset = bool(state.get('sell_needs_reset', False))

            # Clear reset flag when price exits the EMA zone
            if sell_needs_reset and row['high'] < min(row['ema9'], row['ema20']):
                sell_needs_reset = False
            if buy_needs_reset  and row['low']  > max(row['ema9'], row['ema20']):
                buy_needs_reset  = False

            buy_signal = bool(
                row['session_ok'] and row['rway_up']  and row['bull_stack'] and
                row['buy_touch']  and row['buy_pat']  and not buy_needs_reset
            )
            sell_signal = bool(
                row['session_ok'] and row['rway_dn']  and row['bear_stack'] and
                row['sell_touch'] and row['sell_pat'] and not sell_needs_reset
            )

            # Set reset flag to prevent re-entry on the same EMA touch
            if buy_signal:
                buy_needs_reset  = True
            if sell_signal:
                sell_needs_reset = True

            state['buy_needs_reset']  = buy_needs_reset
            state['sell_needs_reset'] = sell_needs_reset

            signal = 'BUY' if buy_signal else ('SELL' if sell_signal else None)
            return signal, state

        except Exception as e:
            logger.error(f"[RTP] Signal check error: {e}", exc_info=True)
            return None, state

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
            raise ValueError("[RTP] No NIFTY expiry dates found in instruments")
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

    def _select_delta_strike(
        self,
        opt_type: str,
        spot: float,
        provider: Any,
    ) -> Tuple[float, Optional[Dict]]:
        """Scan ATM ± 25 strikes; return the one closest to delta 0.90.
        Uses self._instruments and self._expiry cached at monitor start."""
        atm = round(spot / _STRIKE_STEP) * _STRIKE_STEP
        if self._expiry is None or not self._instruments:
            logger.error("[RTP] Instruments not cached — cannot select strike")
            return atm, None
        instruments = self._instruments
        expiry: date = self._expiry   # narrowed from Optional[date]
        candidates   = [atm + i * _STRIKE_STEP for i in range(-25, 26)]

        candidate_insts: List[Tuple[float, Dict]] = []
        for strike in candidates:
            inst = self._find_option(instruments, strike, opt_type, expiry)
            if inst and inst.get('instrument_token'):
                candidate_insts.append((strike, inst))

        if not candidate_insts:
            return atm, None

        all_syms = [inst['instrument_token'] for _, inst in candidate_insts]
        try:
            ltp_data = provider.ltp(all_syms)
        except Exception as e:
            logger.warning(f"[RTP] Delta batch LTP failed: {e}")
            return atm, None

        signed_target = _DELTA_TARGET if opt_type.upper() == 'CE' else -_DELTA_TARGET
        best_strike   = atm
        best_inst: Optional[Dict] = None
        best_diff     = float('inf')

        for strike, inst in candidate_insts:
            fyers_sym = inst['instrument_token']
            ltp = ltp_data.get(fyers_sym, {}).get('last_price', 0)
            if ltp < 0.5:
                continue
            try:
                greeks = GreeksCalculator.calculate_greeks(opt_type, ltp, spot, strike, expiry)
                d = greeks.get('Delta', 0)
                if opt_type.upper() == 'CE' and d <= 0:
                    continue
                if opt_type.upper() == 'PE' and d >= 0:
                    continue
                diff = abs(d - signed_target)
                if diff < best_diff:
                    best_diff   = diff
                    best_strike = strike
                    best_inst   = inst
            except Exception:
                pass

        return best_strike, best_inst

    # ── Broker management ─────────────────────────────────────────────────────

    def _get_active_brokers(self) -> List[Tuple[int, str, Any]]:
        """Return (idx, broker_type, service) for all active+RTP-enabled brokers."""
        result: List[Tuple[int, str, Any]] = []
        for i in range(1, 11):
            if self._uvar(f'BROKER_{i}_ACTIVE', 'false').lower() != 'true':
                continue
            if self._uvar(f'BROKER_{i}_RTP_ACTIVE', 'false').lower() != 'true':
                continue
            broker_type = self._uvar(f'BROKER_{i}_TYPE', '').lower()
            if not broker_type:
                continue
            try:
                svc = self._init_broker_svc(i, broker_type)
                if svc:
                    result.append((i, broker_type, svc))
            except Exception as e:
                logger.error(f"[RTP] Broker {i} ({broker_type}) init failed: {e}")
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
                f"[RTP] [{broker_type.upper()}_{idx}] {transaction_type} order error: {e}"
            )
        return None

    # ── Trade lifecycle ───────────────────────────────────────────────────────

    def _enter_trade(self, direction: str, spot: float, provider: Any) -> None:
        """Select delta strike, place BUY orders on all cached brokers, save state."""
        opt_type = 'CE' if direction == 'BUY' else 'PE'
        strike, inst = self._select_delta_strike(opt_type, spot, provider)

        if inst is None:
            logger.error(f"[RTP] No delta strike found for {opt_type} near spot={spot}")
            return

        sl_level  = round(spot - _SL_POINTS,  1) if direction == 'BUY' else round(spot + _SL_POINTS,  1)
        tgt_level = round(spot + _TGT_POINTS, 1) if direction == 'BUY' else round(spot - _TGT_POINTS, 1)
        fyers_sym = inst.get('instrument_token', '')
        kite_ts   = inst.get('tradingsymbol', '')

        broker_entries: List[Dict] = []
        for idx, (broker_type, svc) in self._broker_map.items():
            lots     = max(1, int(self._uvar(f'BROKER_{idx}_RTP_LOTS', '1') or '1'))
            quantity = lots * self._lot_size
            product  = self._uvar(f'BROKER_{idx}_PRODUCT_TYPE', 'MIS').upper()
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
                f"[RTP] [{broker_type.upper()}_{idx}] BUY {opt_type} {int(strike)}"
                f" qty={quantity} ({lots} lot(s)) order_id={order_id}"
            )

        # Capture option LTP just after order placement as proxy for fill price
        opt_entry_price: Optional[float] = None
        try:
            ltp_data = provider.ltp([fyers_sym])
            raw = ltp_data.get(fyers_sym, {}).get('last_price', 0)
            opt_entry_price = round(float(raw), 2) if raw else None
        except Exception as _e:
            logger.warning(f"[RTP] Could not fetch option entry LTP: {_e}")

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
        logger.info(
            f"[RTP] ENTERED {direction}: spot={spot} {int(strike)}{opt_type}"
            f" opt_ltp={opt_entry_price}"
            f" sl={sl_level} tgt={tgt_level} expiry={self._expiry}"
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
            logger.warning(f"[RTP] Could not fetch option exit LTP: {_e}")

        for entry in trade.get('broker_entries', []):
            idx         = entry['broker_idx']
            broker_type = entry.get('broker_type', 'zerodha')
            kite_ts     = entry.get('tradingsymbol', '')
            fyers_sym   = entry.get('fyers_sym', '')
            # Use stored per-broker quantity; fall back to lot_size from trade (not hardcoded 75)
            quantity    = entry.get('quantity', int(trade.get('lot_size', 75)))
            try:
                # Prefer cached service; re-init as fallback if broker wasn't in map at start
                cached = self._broker_map.get(idx)
                if cached:
                    _, svc = cached
                else:
                    svc = self._init_broker_svc(idx, broker_type)
                if svc:
                    product  = self._uvar(f'BROKER_{idx}_PRODUCT_TYPE', 'MIS').upper()
                    order_id = self._place_order(
                        idx, broker_type, svc, opt_type, strike,
                        kite_ts, fyers_sym, quantity, 'SELL', product,
                    )
                    logger.info(
                        f"[RTP] [{broker_type.upper()}_{idx}] SELL {opt_type} {strike}"
                        f" qty={quantity} order_id={order_id}"
                    )
            except Exception as e:
                logger.error(f"[RTP] Exit order failed broker {idx}: {e}")

        state['active_trade'] = None
        state['last_exit'] = {
            'reason': reason,
            'spot':   spot,
            'time':   datetime.now().isoformat(),
        }
        self._append_history(trade, spot, reason, opt_exit_price=opt_exit_price)
        self._save_state(state)
        self._spot_fail_count = 0
        logger.info(f"[RTP] EXITED ({reason}): spot={spot} opt_exit_ltp={opt_exit_price}")

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
                use_adx=False,
                sl_points=_SL_POINTS,
                tgt_points=_TGT_POINTS,
            )
            processed = engine.df

            today = date.today()
            today_bars = processed[processed['datetime'].dt.date == today].copy()
            if today_bars.empty:
                return False, False

            # Build trade-period exclusion list from today's closed trade history
            trade_periods: List[Tuple] = []
            try:
                with open(_HISTORY_FILE, 'r') as _f:
                    history = json.load(_f)
                today_str = today.isoformat()
                for t in history:
                    if t.get('date') != today_str:
                        continue
                    try:
                        e = pd.Timestamp(t['entry_time'])
                        x = pd.Timestamp(t['exit_time'])
                        trade_periods.append((e, x))
                    except Exception:
                        pass
            except Exception:
                pass

            buy_needs_reset  = False
            sell_needs_reset = False

            for _, row in today_bars.iterrows():
                if not row.get('session_ok', False):
                    continue
                bar_dt = row['datetime']
                # Skip bars that occurred inside a past trade (mirrors backtest skip)
                if any(e <= bar_dt <= x for e, x in trade_periods):
                    continue

                if sell_needs_reset and row['high'] < min(row['ema9'], row['ema20']):
                    sell_needs_reset = False
                if buy_needs_reset and row['low'] > max(row['ema9'], row['ema20']):
                    buy_needs_reset = False

                buy_sig  = bool(row['rway_up'] and row['bull_stack'] and
                                row['buy_touch'] and row['buy_pat'] and not buy_needs_reset)
                sell_sig = bool(row['rway_dn'] and row['bear_stack'] and
                                row['sell_touch'] and row['sell_pat'] and not sell_needs_reset)
                if buy_sig:
                    buy_needs_reset = True
                if sell_sig:
                    sell_needs_reset = True

            logger.info(
                f"[RTP] needs_reset replayed — buy={buy_needs_reset} sell={sell_needs_reset}"
                f" ({len(trade_periods)} past trade(s) excluded)"
            )
            return buy_needs_reset, sell_needs_reset

        except Exception as e:
            logger.error(f"[RTP] needs_reset replay error: {e}", exc_info=True)
            return False, False

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        logger.info("[RTP] Monitor loop started")
        provider = self._get_provider()
        if not provider:
            logger.error("[RTP] Could not get data provider — aborting")
            return
        self._provider = provider  # stored so _exit_trade can fetch option LTP

        # ── Day-start / mid-day-restart initialisation ────────────────────────

        state = self._load_state()
        if not state.get('active_trade'):
            now = datetime.now()
            if now.hour < 9 or (now.hour == 9 and now.minute < 20):
                # True day start: clear both flags unconditionally
                state['buy_needs_reset']  = False
                state['sell_needs_reset'] = False
                self._save_state(state)
                logger.info("[RTP] buy/sell_needs_reset cleared for new trading day")
            else:
                # Mid-day restart (watchdog / server recovery):
                # replay today's bars so we don't lose a reset that happened
                # while the thread was down, and don't falsely clear a live flag.
                buy_nr, sell_nr = self._replay_today_needs_reset(provider)
                state['buy_needs_reset']  = buy_nr
                state['sell_needs_reset'] = sell_nr
                self._save_state(state)
                logger.info(
                    f"[RTP] Mid-day restart — replayed needs_reset:"
                    f" buy={buy_nr} sell={sell_nr}"
                )

        # Cache broker services once — avoids repeated session re-initialisation per trade
        logger.info("[RTP] Initialising broker services...")
        self._broker_map = {
            idx: (btype, svc)
            for idx, btype, svc in self._get_active_brokers()
        }
        if self._broker_map:
            logger.info(f"[RTP] Active RTP brokers: {list(self._broker_map.keys())}")
        else:
            logger.warning("[RTP] No active RTP brokers — signals will be detected but no orders placed")

        # Pre-fetch NFO instruments once — avoids expensive download on every trade entry
        logger.info("[RTP] Pre-fetching NFO instruments...")
        try:
            self._instruments, self._expiry, self._lot_size = self._get_instruments_and_expiry(provider)
            logger.info(
                f"[RTP] Instruments cached — expiry={self._expiry} lot_size={self._lot_size}"
                f" count={len(self._instruments)}"
            )
        except Exception as e:
            logger.error(f"[RTP] Instrument fetch failed at startup: {e}")
            return

        last_signal_minute: Optional[int] = None

        while not self._stop_event.is_set():
            try:
                now = datetime.now()
                h, m, s = now.hour, now.minute, now.second

                # ── Before trading session: wait
                if (h < 9) or (h == 9 and m < 20):
                    time.sleep(1)
                    continue

                # ── Past EOD cutoff: exit any open trade and stop
                if (h > 15) or (h == 15 and m > 28):
                    state = self._load_state()
                    if state.get('active_trade'):
                        spot = self._get_nifty_spot(provider) or 0.0
                        self._exit_trade('EOD', spot)
                    logger.info("[RTP] EOD cutoff reached — monitor loop ending")
                    break

                state = self._load_state()

                # ── In trade → check SL / Target every second regardless of kill-switch.
                # The kill-switch only prevents new entries; it must never block an exit.
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
                        else:  # SELL direction → PE bought
                            if spot >= sl_level:
                                self._exit_trade('SL', spot)
                            elif spot <= tgt_level:
                                self._exit_trade('TARGET', spot)
                    else:
                        self._spot_fail_count += 1
                        if self._spot_fail_count >= _MAX_SPOT_FAILS:
                            logger.critical(
                                f"[RTP] Spot fetch failed {self._spot_fail_count} consecutive times"
                                " — open trade UNMONITORED. Check provider connection."
                            )

                else:
                    # ── Runtime kill-switch — only blocks new signal entries
                    if self._uvar('EMA_RTP_ACTIVE', 'false').lower() != 'true':
                        time.sleep(5)
                        continue

                    # ── No trade → check for signal once per new minute
                    # Compares m != last_signal_minute instead of s == 0 so a missed
                    # second-boundary (slow network call) never skips the whole minute.
                    if m != last_signal_minute:
                        last_signal_minute = m
                        df = self._fetch_1min_candles(provider)
                        if df is not None and len(df) >= _WARMUP_BARS:
                            signal, state = self._check_rtp_signal(df, state)
                            self._save_state(state)

                            if signal:
                                logger.info(
                                    f"[RTP] Signal: {signal} at {now.strftime('%H:%M:%S')}"
                                )
                                spot = self._get_nifty_spot(provider)
                                if spot:
                                    self._enter_trade(signal, spot, provider)
                        else:
                            logger.debug(
                                f"[RTP] Insufficient candle data"
                                f" ({len(df) if df is not None else 0}/{_WARMUP_BARS} bars)"
                            )

            except Exception as e:
                logger.error(f"[RTP] Monitor error: {e}", exc_info=True)

            time.sleep(1)

        logger.info("[RTP] Monitor loop ended")
