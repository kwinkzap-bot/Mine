"""
30-Min Opening Fakeout — Live Algo
Scans every stock in TMF_STOCK_UNIVERSE (algo/../Backtest/tmf_symbol_universe.py)
for the same Candle 1/2/3 pattern the backtest engine checks, each stock using
its OWN default Direction/filter combo from TMF_SYMBOL_DEFAULTS. Auto-started
at 9:15 AM by the scheduler; runs once per day per stock (one setup per day,
same as the backtest).

Per-stock lifecycle for the day:
  pending_scan   -> not yet checked (candles 1-3 aren't closed yet, or just
                     hasn't been reached this tick)
  no_setup       -> checked at/after 10:45, pattern didn't fire — done for
                     the day
  watching       -> pattern fired; trigger/SL known, waiting for price to
                     cross the trigger (or for SL to be hit first, which
                     invalidates the setup before any entry)
  pending_entry  -> trigger crossed, an Intraday LIMIT entry order was
                     placed at the exact trigger price; waiting for it to
                     fill
  in_position    -> entry filled; a real SL-Market order and a real LIMIT
                     Target order are both live at the broker (OCO managed
                     in software — whichever fills first, the other is
                     cancelled)
  done           -> exited (SL/Target/Time Exit) or invalidated/never
                     filled; nothing more happens for this stock today

Target is NOT the backtest's hindsight "day's session Low/High" (physically
unknowable at entry time) — it's the day's session Low/High *up to the
entry fill*, clamped to never be less favourable than the entry price
itself (skipped if that clamp leaves no room). Quantity is computed once
at trigger time (capital_per_trade x TMF_EQUITY_LEVERAGE / trigger price)
and that same quantity is used for the entry, SL, and Target orders.

Gating (see env/Mine.env):
  TMF_ALGO_ACTIVE=true                 — global kill-switch. The monitor
                                          thread always scans and logs
                                          signals regardless of this flag
                                          (so watching it run costs
                                          nothing); only ORDER PLACEMENT is
                                          gated by it, mirroring RTP/SC.
  BROKER_<N>_TMF_ACTIVE=true           — per broker index, alongside the
                                          existing BROKER_<N>_ACTIVE and
                                          BROKER_<N>_TYPE=zerodha. Only
                                          Zerodha is wired up for TMF right
                                          now (fresh order-placement code,
                                          not yet exercised on other
                                          brokers).
  TMF_CAPITAL_PER_TRADE=100000         — capital per trade (same default as
                                          the backtest).
  TMF_EXIT_HOUR / TMF_EXIT_MINUTE      — cutoff for the Time Exit square-off
                                          (default 15 / 18, same as backtest).

Known live/backtest divergence: the backtest checks a bar's High/Low for
SL/Target/trigger crossings (catches any touch within the bar); the live
watch loop instead polls last-traded-price every _POLL_SECS seconds — a
brief intra-poll spike through a level could be missed, the same tolerance
RTP already accepts for its own per-second spot polling.
"""
import json
import logging
import math
import os
import threading
import time
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(__file__)
STATE_FILE       = os.path.join(_DIR, 'tmf_state.json')
HISTORY_FILE     = os.path.join(_DIR, 'tmf_trades_history.json')
ALL_HISTORY_FILE = os.path.join(_DIR, 'tmf_trades_all_history.json')
LOG_FILE         = os.path.join(_DIR, 'tmf_algo.log')

# Dedicated file sink for every [TMF] log line — setup detection, order
# placement/fills/exits, and (crucially) the exact broker error text on a
# failed placement — so a failure can be diagnosed later without digging
# through the app's general console output. Guarded so re-importing this
# module (e.g. Flask's reloader) doesn't stack duplicate handlers.
if not any(isinstance(h, RotatingFileHandler) and getattr(h, '_tmf_sink', False) for h in logger.handlers):
    _file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    _file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    _file_handler._tmf_sink = True
    logger.addHandler(_file_handler)
    logger.setLevel(logging.INFO)

_POLL_SECS = 5           # how often the monitor loop ticks
_HARD_STOP_MIN = 15 * 60 + 30   # thread exits for the day at/after 15:30 IST
_RECONCILE_SECS = 30     # how often to cross-check real broker positions against state

_NSE_TICK_SIZE = 0.05    # NSE cash-market tick size — LIMIT order prices not a
                         # multiple of this are rejected by the exchange


def _round_to_tick(price: float, direction: str) -> float:
    """Snap a computed (buffer-adjusted) price to a valid NSE tick, rounding
    away from the raw candle level so the trigger keeps meaning "cleared the
    level by at least the buffer" — up for a long (buy-side) trigger, down
    for a short (sell-side) one. Raw candle prices (sl_level, entry fills,
    session high/low) are already exchange-tick-aligned and don't need this."""
    ticks = price / _NSE_TICK_SIZE
    ticks = math.ceil(ticks) if direction == 'long' else math.floor(ticks)
    return round(ticks * _NSE_TICK_SIZE, 2)


def _thirty_min_engine():
    """Deferred import — the engine module has no Flask/app dependency, but
    importing lazily here still avoids any import-order surprises at
    process startup (mirrors every other algo module's import style)."""
    from trading_app.Backtest.thirty_min_fakeout_engine import (
        _prepare_df, _resample_30min, _detect_setup, _SESSION_START_MIN, _THIRD_CANDLE_END_MIN,
    )
    return _prepare_df, _resample_30min, _detect_setup, _SESSION_START_MIN, _THIRD_CANDLE_END_MIN


_instances: Dict[str, 'TMFAlgo'] = {}


def get_instance(username: str) -> Optional['TMFAlgo']:
    return _instances.get(username)


class TMFAlgo:
    """Live 30-Min Opening Fakeout signal detector and equity order executor."""

    def __init__(self, username: str):
        self.username = username
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._broker_list: List[Tuple[int, Any]] = []  # [(broker_idx, KiteService), ...]
        self._last_reconcile_ts: float = 0.0

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name='TMFAlgoThread')
        self._thread.start()
        _instances[self.username] = self
        logger.info("[TMF] Monitoring thread started")

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop(self.username, None)
        logger.info("[TMF] Stop requested")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── State ────────────────────────────────────────────────────────────

    def _fresh_state_for_today(self) -> Dict[str, Any]:
        from trading_app.Backtest.tmf_symbol_universe import TMF_STOCK_UNIVERSE
        return {
            'date': date.today().isoformat(),
            'eod_handled': False,
            'stocks': {sym: {'phase': 'pending_scan'} for sym in TMF_STOCK_UNIVERSE},
        }

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return self._fresh_state_for_today()

    def _save_state(self, state: Dict[str, Any]) -> None:
        with self._state_lock:
            try:
                with open(STATE_FILE, 'w') as f:
                    json.dump(state, f, indent=2, default=str)
            except Exception as e:
                logger.error(f"[TMF] State save failed: {e}")

    def _append_history(self, record: Dict[str, Any]) -> None:
        """Append a completed trade to tmf_trades_history.json (today only,
        latest-first, day-rotated) and the permanent all-time history."""
        try:
            today = date.today().isoformat()
            record['date'] = today
            try:
                with open(HISTORY_FILE, 'r') as f:
                    history: list = json.load(f)
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []
            if history and history[0].get('date') != today:
                history = []
            history.insert(0, record)
            with open(HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=2, default=str)

            try:
                with open(ALL_HISTORY_FILE, 'r') as f:
                    all_history: list = json.load(f)
                if not isinstance(all_history, list):
                    all_history = []
            except Exception:
                all_history = []
            all_history.insert(0, record)
            with open(ALL_HISTORY_FILE, 'w') as f:
                json.dump(all_history, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"[TMF] History append failed: {e}")

    # ── Env helpers ──────────────────────────────────────────────────────

    def _uvar(self, key: str, default: str = '') -> str:
        from trading_app.app.utils.user_env import UserEnvManager
        return (UserEnvManager.get_user_var(self.username, key) or default).strip()

    # ── Broker ───────────────────────────────────────────────────────────

    def _get_active_brokers(self) -> List[Tuple[int, Any]]:
        """(broker_idx, KiteService) for every active+TMF-enabled Zerodha
        broker slot. Only 'zerodha' is wired up for TMF right now — other
        broker types are silently skipped (fresh equity order-placement
        code, only exercised against Zerodha so far)."""
        result: List[Tuple[int, Any]] = []
        for i in range(1, 11):
            if self._uvar(f'BROKER_{i}_ACTIVE', 'false').lower() != 'true':
                continue
            if self._uvar(f'BROKER_{i}_TMF_ACTIVE', 'false').lower() != 'true':
                continue
            if self._uvar(f'BROKER_{i}_TYPE', '').lower() != 'zerodha':
                continue
            try:
                from trading_app.service.provider_logic import get_kite
                from trading_app.service.kite_order_services import KiteService
                kite = get_kite(user=self.username, instance=i)
                if kite:
                    result.append((i, KiteService(kite_instance=kite)))
            except Exception as e:
                logger.error(f"[TMF] Broker {i} (zerodha) init failed: {e}")
        return result

    def _svc_for(self, idx: int) -> Optional[Any]:
        for i, svc in self._broker_list:
            if i == idx:
                return svc
        return None

    def _cancel_broker_order(self, idx: int, order_id: Optional[str]) -> None:
        svc = self._svc_for(idx)
        if svc is None or not order_id:
            return
        svc.cancel_order(order_id)

    # ── Data ─────────────────────────────────────────────────────────────

    def _get_provider(self) -> Any:
        from trading_app.service.provider_logic import get_data_provider
        return get_data_provider(user=self.username)

    def _fetch_today_candles(self, provider: Any, is_fyers: bool, symbol: str) -> Optional[pd.DataFrame]:
        from trading_app.Backtest.minute_candle_store import get_minute_history
        today_str = date.today().isoformat()
        spot_token = f'NSE:{symbol}-EQ' if is_fyers else symbol
        provider_tag = 'fyers' if is_fyers else 'kite'
        try:
            return get_minute_history(provider, spot_token, symbol, today_str, today_str, provider_tag=provider_tag)
        except Exception as e:
            logger.warning(f"[TMF] {symbol}: today-candle fetch failed: {e}")
            return None

    def _get_ltp_batch(self, provider: Any, symbols: List[str], is_fyers: bool) -> Dict[str, float]:
        tokens = [f'NSE:{s}-EQ' if is_fyers else s for s in symbols]
        try:
            data = provider.ltp(tokens) or {}
        except Exception as e:
            logger.warning(f"[TMF] batch LTP fetch failed: {e}")
            return {}
        result = {}
        for sym, tok in zip(symbols, tokens):
            v = data.get(tok, {})
            lp = v.get('last_price') if isinstance(v, dict) else None
            if lp:
                result[sym] = float(lp)
        return result

    # ── Signal detection ─────────────────────────────────────────────────

    def _scan_one(self, provider: Any, is_fyers: bool, symbol: str, s: Dict[str, Any]) -> None:
        from trading_app.Backtest.tmf_symbol_universe import TMF_SYMBOL_DEFAULTS
        _prepare_df, _resample_30min, _detect_setup, _SESSION_START_MIN, _ = _thirty_min_engine()

        df = self._fetch_today_candles(provider, is_fyers, symbol)
        if df is None or df.empty:
            s['phase'] = 'no_setup'
            return
        prepared = _prepare_df(df.copy())
        thirty = _resample_30min(prepared)
        if not all(b in thirty.index for b in (0, 1, 2)):
            s['phase'] = 'no_setup'
            return
        combo = TMF_SYMBOL_DEFAULTS.get(symbol)
        if not combo:
            s['phase'] = 'no_setup'
            return
        enable_long  = combo['direction'] != 'short'
        enable_short = combo['direction'] != 'long'
        setup = _detect_setup(
            thirty.loc[0], thirty.loc[1], thirty.loc[2], enable_long, enable_short,
            combo['use_entry_buffer'], combo['use_body_filter'], combo['use_c2_close_filter'],
        )
        if setup is None:
            s['phase'] = 'no_setup'
            return
        s['phase']    = 'watching'
        s['direction'] = setup['direction']
        s['trigger']   = _round_to_tick(float(setup['trigger']), setup['direction'])
        s['sl_level']  = round(float(setup['sl_level']), 2)
        logger.info(f"[TMF] {symbol}: setup found — {setup['direction'].upper()} "
                    f"trigger={s['trigger']} sl={s['sl_level']}")

    def _compute_target(self, provider: Any, is_fyers: bool, symbol: str,
                         direction: str, entry_price: float) -> Optional[float]:
        """Target = the day's session Low/High *up to the entry fill* —
        forward-computable (unlike the backtest's full-day hindsight
        target), clamped to never be less favourable than the entry price
        itself. Returns None if that clamp leaves no usable room (skip the
        Target leg for this trade; SL + Time Exit still apply)."""
        df = self._fetch_today_candles(provider, is_fyers, symbol)
        if df is None or df.empty:
            return None
        if direction == 'short':
            target = min(float(df['low'].min()), entry_price)
            return round(target, 2) if target < entry_price else None
        target = max(float(df['high'].max()), entry_price)
        return round(target, 2) if target > entry_price else None

    # ── Order placement ──────────────────────────────────────────────────

    def _fire_entry(self, symbol: str, s: Dict[str, Any], capital_per_trade: float, algo_active: bool) -> None:
        from trading_app.Backtest.tmf_symbol_universe import TMF_EQUITY_LEVERAGE
        direction = s['direction']
        trigger   = s['trigger']
        qty = max(1, int((capital_per_trade * TMF_EQUITY_LEVERAGE) // trigger)) if trigger > 0 else 1
        s['qty'] = qty
        s['entry_time'] = datetime.now().isoformat()

        if not algo_active or not self._broker_list:
            s['phase'] = 'done'
            s['exit_reason'] = 'Signal only (TMF_ALGO_ACTIVE off or no broker configured)'
            logger.info(f"[TMF] {symbol}: {direction.upper()} trigger hit @ {trigger} — no order placed ({s['exit_reason']})")
            return

        transaction = 'SELL' if direction == 'short' else 'BUY'
        broker_positions = []
        for idx, svc in self._broker_list:
            result = svc.place_equity_order(symbol, transaction, qty, price=trigger, product='MIS')
            if result.get('success'):
                broker_positions.append({'broker_idx': idx, 'entry_order_id': str(result['order_id']), 'filled': False})
                logger.info(f"[TMF] {symbol}: entry {transaction} LIMIT x{qty} @ {trigger} placed via broker {idx} — order_id={result['order_id']}")
            else:
                logger.error(f"[TMF] {symbol}: entry order FAILED via broker {idx}: {result.get('error')}")

        if not broker_positions:
            s['phase'] = 'done'
            s['exit_reason'] = 'Entry order placement failed'
            return
        s['broker_positions'] = broker_positions
        s['phase'] = 'pending_entry'

    def _check_entry_fill(self, provider: Any, is_fyers: bool, symbol: str, s: Dict[str, Any],
                           orderbooks: Dict[int, Dict[str, Any]], now_mins: int, cutoff_mins: int) -> None:
        for bp in s.get('broker_positions', []):
            if bp.get('filled') or bp.get('dead'):
                continue
            ob = orderbooks.get(bp['broker_idx'], {})
            order = ob.get(bp['entry_order_id'])
            if not order:
                continue
            status = order.get('status')
            if status == 'COMPLETE':
                bp['filled']      = True
                bp['entry_price'] = float(order.get('average_price') or s['trigger'])
                bp['filled_qty']  = int(order.get('filled_quantity') or s['qty'])
            elif status in ('CANCELLED', 'REJECTED'):
                bp['dead'] = True

        live_positions    = [bp for bp in s.get('broker_positions', []) if bp.get('filled')]
        pending_positions = [bp for bp in s.get('broker_positions', []) if not bp.get('filled') and not bp.get('dead')]

        if pending_positions and now_mins < cutoff_mins:
            return  # still waiting for a fill

        # Cutoff reached, or every leg has resolved one way or another —
        # cancel any entry order still sitting open.
        for bp in pending_positions:
            self._cancel_broker_order(bp['broker_idx'], bp['entry_order_id'])
            bp['dead'] = True

        if not live_positions:
            s['phase'] = 'done'
            s['exit_reason'] = 'Entry never filled'
            return

        direction   = s['direction']
        entry_price = live_positions[0]['entry_price']
        target_level = self._compute_target(provider, is_fyers, symbol, direction, entry_price)
        s['entry_price']  = entry_price
        s['target_level'] = target_level

        exit_txn = 'BUY' if direction == 'short' else 'SELL'
        for bp in live_positions:
            svc = self._svc_for(bp['broker_idx'])
            if svc is None:
                continue
            qty = bp['filled_qty']
            sl_res = svc.place_equity_sl_order(symbol, exit_txn, qty, trigger_price=s['sl_level'], product='MIS')
            bp['sl_order_id'] = str(sl_res['order_id']) if sl_res.get('success') else None
            if target_level is not None:
                tgt_res = svc.place_equity_order(symbol, exit_txn, qty, price=target_level, product='MIS')
                bp['target_order_id'] = str(tgt_res['order_id']) if tgt_res.get('success') else None
            else:
                bp['target_order_id'] = None
            logger.info(f"[TMF] {symbol}: filled @ {entry_price} (broker {bp['broker_idx']}) — "
                        f"SL {s['sl_level']} order={bp.get('sl_order_id')}, "
                        f"Target {target_level} order={bp.get('target_order_id')}")
        s['phase'] = 'in_position'

    def _check_position_exit(self, symbol: str, s: Dict[str, Any], orderbooks: Dict[int, Dict[str, Any]]) -> None:
        for bp in s.get('broker_positions', []):
            if not bp.get('filled') or bp.get('closed'):
                continue
            ob = orderbooks.get(bp['broker_idx'], {})
            sl_order  = ob.get(bp.get('sl_order_id') or '')
            tgt_order = ob.get(bp.get('target_order_id') or '')
            sl_done  = bool(sl_order and sl_order.get('status') == 'COMPLETE')
            tgt_done = bool(tgt_order and tgt_order.get('status') == 'COMPLETE')
            # SL checked before Target — same convention as the backtest.
            if sl_done:
                exit_price = float(sl_order.get('average_price') or s['sl_level'])
                self._close_leg(symbol, s, bp, exit_price, 'SL Hit', cancel_target=True)
            elif tgt_done:
                exit_price = float(tgt_order.get('average_price') or s['target_level'])
                self._close_leg(symbol, s, bp, exit_price, 'Target Hit', cancel_sl=True)

        if s.get('broker_positions') and all(
            bp.get('closed') for bp in s['broker_positions'] if bp.get('filled')
        ):
            s['phase'] = 'done'

    def _close_leg(self, symbol: str, s: Dict[str, Any], bp: Dict[str, Any], exit_price: float,
                    reason: str, cancel_sl: bool = False, cancel_target: bool = False) -> None:
        if cancel_sl and bp.get('sl_order_id'):
            self._cancel_broker_order(bp['broker_idx'], bp['sl_order_id'])
        if cancel_target and bp.get('target_order_id'):
            self._cancel_broker_order(bp['broker_idx'], bp['target_order_id'])
        bp['closed'] = True
        self._record_exit(symbol, s, bp, exit_price, reason)

    def _record_exit(self, symbol: str, s: Dict[str, Any], bp: Dict[str, Any], exit_price: float, reason: str) -> None:
        direction   = s['direction']
        entry_price = bp['entry_price']
        qty         = bp['filled_qty']
        pnl = (entry_price - exit_price) * qty if direction == 'short' else (exit_price - entry_price) * qty
        record = {
            'symbol': symbol, 'direction': 'SELL' if direction == 'short' else 'BUY',
            'qty': qty, 'entry_price': entry_price, 'exit_price': exit_price,
            'sl_price': s['sl_level'], 'target_price': s.get('target_level'),
            'pnl': round(pnl, 2), 'reason': reason,
            'entry_time': s.get('entry_time', ''), 'exit_time': datetime.now().isoformat(),
            'broker_idx': bp['broker_idx'], 'entry_order_id': bp.get('entry_order_id'),
            'sl_order_id': bp.get('sl_order_id'), 'target_order_id': bp.get('target_order_id'),
        }
        self._append_history(record)
        logger.info(f"[TMF] {symbol}: closed ({reason}) @ {exit_price}, P&L ₹{record['pnl']}")

    def _get_ltp_one(self, svc: Any, symbol: str) -> Optional[float]:
        try:
            ltp = svc.kite.ltp(f'NSE:{symbol}')
            return (ltp.get(f'NSE:{symbol}') or {}).get('last_price')
        except Exception:
            return None

    def _poll_fill_price(self, svc: Any, order_id: str, fallback: float,
                          attempts: int = 6, delay_secs: float = 0.5) -> float:
        """Poll a just-placed marketable order for its real average_price
        instead of assuming the requested limit price is what it filled at.
        Falls back to the requested price (logged, so the gap is visible)
        only if the order hasn't reported COMPLETE with a fill price yet."""
        for _ in range(attempts):
            status = svc.get_order_status(order_id)
            if status.get('success') and status.get('status') == 'COMPLETE' and status.get('average_price'):
                return float(status['average_price'])
            time.sleep(delay_secs)
        logger.warning(f"[TMF] order {order_id}: fill price not confirmed after polling — "
                        f"recording requested price {fallback} (may not match the real fill)")
        return fallback

    def _square_off(self, symbol: str, s: Dict[str, Any]) -> None:
        """Time Exit: cancel the still-open SL/Target legs, then close the
        position with a marketable Intraday LIMIT (padded off the last
        traded price so it fills like a market order without actually
        being one, matching the user's 'LIMIT order only' requirement)."""
        direction = s['direction']
        exit_txn  = 'BUY' if direction == 'short' else 'SELL'
        for bp in s.get('broker_positions', []):
            if not bp.get('filled') or bp.get('closed'):
                continue
            svc = self._svc_for(bp['broker_idx'])
            if svc is None:
                continue
            if bp.get('sl_order_id'):
                self._cancel_broker_order(bp['broker_idx'], bp['sl_order_id'])
            if bp.get('target_order_id'):
                self._cancel_broker_order(bp['broker_idx'], bp['target_order_id'])

            qty = bp['filled_qty']
            ltp = self._get_ltp_one(svc, symbol) or s['sl_level']
            pad = 1.01 if exit_txn == 'BUY' else 0.99
            price = round(ltp * pad, 1)
            res = svc.place_equity_order(symbol, exit_txn, qty, price=price, product='MIS')
            exit_price = price
            if res.get('success'):
                # This is a padded LIMIT order (fills at price-or-better, often
                # noticeably better) — recording the *requested* price instead
                # of the real fill silently corrupts the P&L history, so poll
                # for the actual average_price rather than assuming they match.
                exit_price = self._poll_fill_price(svc, res['order_id'], fallback=price)
                logger.info(f"[TMF] {symbol}: EOD square-off {exit_txn} x{qty} requested @ {price}, "
                            f"filled @ {exit_price} (broker {bp['broker_idx']}) order={res.get('order_id')}")
            else:
                logger.error(f"[TMF] {symbol}: EOD square-off FAILED (broker {bp['broker_idx']}): {res.get('error')}")

            bp['closed'] = True
            self._record_exit(symbol, s, bp, exit_price, 'Time Exit')
        s['phase'] = 'done'

    def _handle_eod(self, state: Dict[str, Any]) -> None:
        for symbol, s in state['stocks'].items():
            if s['phase'] == 'in_position':
                self._square_off(symbol, s)
            elif s['phase'] == 'pending_entry':
                for bp in s.get('broker_positions', []):
                    if not bp.get('filled') and not bp.get('dead'):
                        self._cancel_broker_order(bp['broker_idx'], bp['entry_order_id'])
                s['phase'] = 'done'
                s['exit_reason'] = 'Entry never filled (EOD)'
        logger.info("[TMF] EOD handling complete — open positions squared off, pending orders cancelled")

        # Final safety sweep: cross-check every broker's REAL open MIS
        # positions against what the loop above just did. This is what
        # would have caught the 2026-07-23 incident (HAVELLS/ONGC filled
        # for real while an order-ID bug made this app believe the entry
        # never filled, so the loop above silently wrote them off with no
        # exit order at all) — belt-and-braces even with that bug fixed,
        # since any future fill-tracking failure could reproduce it.
        try:
            self._reconcile_orphaned_positions(state, force_square_off=True)
        except Exception as e:
            logger.error(f"[TMF] EOD reconciliation sweep failed: {e}", exc_info=True)

    # ── Broker-position reconciliation (safety net) ─────────────────────────

    def _get_broker_mis_positions(self, svc: Any) -> Dict[str, Dict[str, Any]]:
        """Real open MIS (intraday) equity positions at the broker right
        now — ground truth, independent of anything this app's own state
        tracking believes."""
        try:
            positions = svc.kite.positions() or {}
            net = positions.get('net', []) or []
            result: Dict[str, Dict[str, Any]] = {}
            for p in net:
                if p.get('product') != 'MIS':
                    continue
                qty = int(p.get('quantity', 0) or 0)
                if qty == 0:
                    continue
                result[p.get('tradingsymbol')] = {
                    'quantity': qty,   # positive = net long, negative = net short
                    'average_price': float(p.get('average_price', 0) or 0),
                }
            return result
        except Exception as e:
            logger.warning(f"[TMF] positions() fetch failed: {e}")
            return {}

    def _reconcile_orphaned_positions(self, state: Dict[str, Any], force_square_off: bool,
                                       provider: Any = None, is_fyers: bool = False) -> None:
        """Cross-check every active broker's REAL open MIS positions
        against this app's own state. Anything the broker shows as open
        that state has NOT marked in_position (with a live, unclosed
        broker_positions leg for that broker) is an orphan — recovered
        using whatever direction/SL/Target this app already knows for
        that symbol (or, lacking that, squared off immediately rather
        than left unprotected). force_square_off=True (used at EOD) closes
        every orphan found instead of just arming protection for it."""
        stocks = state['stocks']
        for idx, svc in self._broker_list:
            broker_positions = self._get_broker_mis_positions(svc)
            for symbol, pos in broker_positions.items():
                s = stocks.get(symbol)
                tracked_bp = None
                if s and s.get('phase') == 'in_position':
                    tracked_bp = next(
                        (bp for bp in s.get('broker_positions', [])
                         if bp.get('broker_idx') == idx and bp.get('filled') and not bp.get('closed')),
                        None,
                    )

                if tracked_bp is not None:
                    # Already tracked — but if the entry order was still filling
                    # incrementally when this sweep first caught it (a LIMIT
                    # order doesn't fill atomically), the SL/Target armed back
                    # then are sized for a stale partial quantity while more of
                    # the position keeps filling at the broker. Re-sync now
                    # rather than silently leaving the difference unprotected.
                    real_qty = abs(pos['quantity'])
                    if not force_square_off and real_qty != tracked_bp.get('filled_qty'):
                        old_qty = tracked_bp.get('filled_qty')
                        logger.error(
                            f"[TMF] RECONCILE: {symbol} broker {idx} real qty ({real_qty}) != "
                            f"tracked qty ({old_qty}) — entry order filled further after this app "
                            f"first caught it. Re-arming SL/Target for the corrected quantity."
                        )
                        direction = s['direction']
                        exit_txn = 'BUY' if direction == 'short' else 'SELL'
                        if tracked_bp.get('sl_order_id'):
                            self._cancel_broker_order(idx, tracked_bp['sl_order_id'])
                        if tracked_bp.get('target_order_id'):
                            self._cancel_broker_order(idx, tracked_bp['target_order_id'])
                        tracked_bp['filled_qty']  = real_qty
                        tracked_bp['entry_price'] = pos['average_price']
                        s['qty']         = real_qty
                        s['entry_price'] = pos['average_price']
                        if s.get('sl_level'):
                            sl_res = svc.place_equity_sl_order(symbol, exit_txn, real_qty, trigger_price=s['sl_level'], product='MIS')
                            tracked_bp['sl_order_id'] = str(sl_res['order_id']) if sl_res.get('success') else None
                        if s.get('target_level') is None and provider is not None:
                            s['target_level'] = self._compute_target(provider, is_fyers, symbol, direction, pos['average_price'])
                        if s.get('target_level') is not None:
                            tgt_res = svc.place_equity_order(symbol, exit_txn, real_qty, price=s['target_level'], product='MIS')
                            tracked_bp['target_order_id'] = str(tgt_res['order_id']) if tgt_res.get('success') else None
                        logger.info(f"[TMF] RECONCILE: {symbol} re-armed SL={s.get('sl_level')} order={tracked_bp.get('sl_order_id')}, "
                                    f"Target={s.get('target_level')} order={tracked_bp.get('target_order_id')}")
                    continue

                qty = abs(pos['quantity'])
                direction = 'short' if pos['quantity'] < 0 else 'long'
                logger.error(
                    f"[TMF] RECONCILE: {symbol} has a real open MIS position at broker {idx} "
                    f"(qty={pos['quantity']}, avg={pos['average_price']}) this app's own state had "
                    f"NOT marked in_position — a tracking failure orphaned it. "
                    f"{'Squaring off now.' if force_square_off else 'Recovering it into managed tracking.'}"
                )

                if s is None:
                    s = {'direction': direction, 'entry_time': datetime.now().isoformat()}
                    stocks[symbol] = s

                bp = {
                    'broker_idx': idx, 'entry_order_id': 'RECONCILED', 'filled': True,
                    'entry_price': pos['average_price'], 'filled_qty': qty, 'closed': False,
                }
                s['broker_positions'] = [b for b in s.get('broker_positions', [])
                                          if b.get('broker_idx') != idx] + [bp]
                s['direction']   = direction
                s['entry_price'] = pos['average_price']
                s['qty']         = qty
                s['phase']       = 'in_position'

                if force_square_off:
                    self._square_off(symbol, s)
                elif s.get('sl_level'):
                    exit_txn = 'BUY' if direction == 'short' else 'SELL'
                    sl_res = svc.place_equity_sl_order(symbol, exit_txn, qty, trigger_price=s['sl_level'], product='MIS')
                    bp['sl_order_id'] = str(sl_res['order_id']) if sl_res.get('success') else None
                    # target_level is normally computed once, inline, when the
                    # entry fill is first observed (_check_entry_fill) — an
                    # orphan recovered here never went through that path, so
                    # it has to be computed now or the position is left with
                    # only an SL leg and no Target order at the broker.
                    if s.get('target_level') is None and provider is not None:
                        s['target_level'] = self._compute_target(provider, is_fyers, symbol, direction, pos['average_price'])
                    if s.get('target_level') is not None:
                        tgt_res = svc.place_equity_order(symbol, exit_txn, qty, price=s['target_level'], product='MIS')
                        bp['target_order_id'] = str(tgt_res['order_id']) if tgt_res.get('success') else None
                    logger.info(f"[TMF] RECONCILE: {symbol} armed SL={s['sl_level']} order={bp.get('sl_order_id')}, "
                                f"Target={s.get('target_level')} order={bp.get('target_order_id')}")
                else:
                    logger.warning(f"[TMF] RECONCILE: {symbol} has no known SL reference — "
                                    f"squaring off immediately rather than running unprotected")
                    self._square_off(symbol, s)

    # ── Main loop ────────────────────────────────────────────────────────

    def _tick(self, provider: Any, is_fyers: bool, state: Dict[str, Any], capital_per_trade: float,
               cutoff_mins: int, algo_active: bool, now_mins: int) -> None:
        stocks = state['stocks']
        _, _, _, _, third_candle_end_min = _thirty_min_engine()

        if now_mins >= third_candle_end_min:
            for symbol, s in stocks.items():
                if s['phase'] != 'pending_scan':
                    continue
                try:
                    self._scan_one(provider, is_fyers, symbol, s)
                except Exception as e:
                    logger.warning(f"[TMF] {symbol}: scan failed: {e}")

        watching_syms = [sym for sym, s in stocks.items() if s['phase'] == 'watching']
        if watching_syms and now_mins < cutoff_mins:
            ltps = self._get_ltp_batch(provider, watching_syms, is_fyers)
            for symbol in watching_syms:
                ltp = ltps.get(symbol)
                if ltp is None:
                    continue
                s = stocks[symbol]
                try:
                    if s['direction'] == 'short':
                        if ltp >= s['sl_level']:
                            s['phase'] = 'done'; s['exit_reason'] = 'Invalidated (SL hit before entry)'
                        elif ltp <= s['trigger']:
                            self._fire_entry(symbol, s, capital_per_trade, algo_active)
                    else:
                        if ltp <= s['sl_level']:
                            s['phase'] = 'done'; s['exit_reason'] = 'Invalidated (SL hit before entry)'
                        elif ltp >= s['trigger']:
                            self._fire_entry(symbol, s, capital_per_trade, algo_active)
                except Exception as e:
                    logger.warning(f"[TMF] {symbol}: trigger check failed: {e}")

        pending_syms = [sym for sym, s in stocks.items() if s['phase'] == 'pending_entry']
        inpos_syms   = [sym for sym, s in stocks.items() if s['phase'] == 'in_position']
        if (pending_syms or inpos_syms) and self._broker_list:
            orderbooks = {idx: svc.get_orderbook_by_id() for idx, svc in self._broker_list}
            for symbol in pending_syms:
                try:
                    self._check_entry_fill(provider, is_fyers, symbol, stocks[symbol], orderbooks, now_mins, cutoff_mins)
                except Exception as e:
                    logger.warning(f"[TMF] {symbol}: entry-fill check failed: {e}")
            for symbol in inpos_syms:
                try:
                    self._check_position_exit(symbol, stocks[symbol], orderbooks)
                except Exception as e:
                    logger.warning(f"[TMF] {symbol}: position-exit check failed: {e}")

        # Periodic broker-position reconciliation — catches any real fill
        # this app's own tracking missed (whatever the cause) long before
        # EOD, instead of only finding out at the cutoff with no time left
        # to react. See _reconcile_orphaned_positions' docstring.
        if self._broker_list:
            now_ts = time.time()
            if now_ts - self._last_reconcile_ts >= _RECONCILE_SECS:
                self._last_reconcile_ts = now_ts
                try:
                    self._reconcile_orphaned_positions(state, force_square_off=False,
                                                         provider=provider, is_fyers=is_fyers)
                except Exception as e:
                    logger.error(f"[TMF] reconciliation failed: {e}", exc_info=True)

        if now_mins >= cutoff_mins and not state.get('eod_handled'):
            try:
                self._handle_eod(state)
            except Exception as e:
                logger.error(f"[TMF] EOD handling failed: {e}", exc_info=True)
            state['eod_handled'] = True

    def _monitor_loop(self) -> None:
        try:
            logger.info(f"[TMF] Monitor thread started for {self.username}")
            provider = None
            for _ in range(30):
                if self._stop_event.is_set():
                    return
                provider = self._get_provider()
                if provider:
                    break
                time.sleep(2)
            if not provider:
                logger.error("[TMF] Data provider unavailable — aborting for today")
                return

            is_fyers = hasattr(provider, 'fyers')

            state = self._load_state()
            if state.get('date') != date.today().isoformat():
                state = self._fresh_state_for_today()
            self._save_state(state)

            self._broker_list = self._get_active_brokers()
            if not self._broker_list:
                logger.warning("[TMF] No active Zerodha broker configured for TMF — scanning only, no orders will be placed")

            capital_per_trade = float(self._uvar('TMF_CAPITAL_PER_TRADE', '100000') or 100000)
            exit_hour   = int(self._uvar('TMF_EXIT_HOUR', '15') or 15)
            exit_minute = int(self._uvar('TMF_EXIT_MINUTE', '18') or 18)
            cutoff_mins = exit_hour * 60 + exit_minute
            algo_active = self._uvar('TMF_ALGO_ACTIVE', 'false').lower() == 'true'

            while not self._stop_event.is_set():
                now = datetime.now()
                now_mins = now.hour * 60 + now.minute
                if now_mins >= _HARD_STOP_MIN:
                    break
                try:
                    self._tick(provider, is_fyers, state, capital_per_trade, cutoff_mins, algo_active, now_mins)
                except Exception as e:
                    logger.error(f"[TMF] tick error: {e}", exc_info=True)
                self._save_state(state)
                self._stop_event.wait(_POLL_SECS)

            logger.info("[TMF] Monitor thread exiting for the day")
        except Exception as e:
            logger.error(f"[TMF] monitor loop crashed: {e}", exc_info=True)
