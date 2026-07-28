"""
EMA Confluence Breakout — Live Algo (PAPER TRADE, Futures)

Live counterpart of the backtest in Backtest/ema_pullback_engine.py, scanning
every symbol in EMA_SYMBOL_DEFAULTS (Backtest/ema_symbol_universe.py) — each
using its OWN default Direction/Target% combo, same as the backtest form's
per-symbol prefill. Signal detection reuses EmaPullbackEngine directly (the
exact same EMA-touch/breakout/SL logic as the backtest) against the
underlying's own continuous daily history (index/equity — futures contracts
roll monthly and don't carry enough history for a 200-day EMA); the paper
ORDER itself is filled and marked-to-market on that symbol's own FUTURES
contract, per this task's requirement to trade the future, not the equity.
Known approximation: the trigger/SL/Target price levels are computed on the
equity/index scale and compared directly against the future's LTP — no
equity/future basis adjustment is applied (mirrors the approximations already
accepted elsewhere in this codebase, e.g. TMF_EQUITY_LEVERAGE).

Per-symbol lifecycle (state persists across days — unlike TMF, this is a
multi-day SWING strategy, not an intraday one; there is no EOD square-off):
  pending_scan  -> not yet scanned today
  no_setup      -> scanned, no signal on the most recent completed daily
                    candle (or the signal's direction isn't enabled for this
                    symbol — see EMA_SYMBOL_DEFAULTS)
  watching      -> signal found; trigger/SL armed off the signal candle's own
                    High/Low (exactly like the backtest's "pending" breakout
                    order) — stays armed with NO EXPIRY across any number of
                    days until price breaks the trigger (matches the
                    backtest: "if price never breaks out, that signal never
                    fills")
  in_position   -> paper position open on the future; watching for SL (the
                    signal candle's opposite extreme) or Target (target_pct
                    of entry price) — SL checked before Target, same
                    convention as the backtest
Once a position closes (or a trigger crosses while EMA_CONFLUENCE_ACTIVE is
off), the symbol returns to pending_scan so the next morning's scan can find
a fresh signal — mirrors the backtest's "scanning for the next signal
resumes on the very next bar".

The daily scan (one per symbol per day, only for symbols not already
watching/in_position — "only one pending order at a time", same as the
backtest) runs once each morning off the most recently COMPLETED daily
candle (to_date = yesterday, never today's still-forming candle — no
look-ahead).

Gating (see env/Mine.env):
  EMA_CONFLUENCE_ACTIVE = true/false   — gates entries (paper fills); the
                                          thread always scans/logs regardless
                                          (same convention as TMF/RTP).
  EMA_CONFLUENCE_MODE   = paper (default) | live (not implemented — falls
                                          back to paper, same scaffold as
                                          intrinsic_range_algo.py).
  EMA_CONFLUENCE_LOTS   = 1             — paper lot count per entry.

This module is PAPER-TRADE ONLY — no real broker orders are ever placed.
"""
import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(__file__)
STATE_FILE       = os.path.join(_DIR, 'ema_confluence_state.json')
HISTORY_FILE     = os.path.join(_DIR, 'ema_confluence_trades_history.json')
ALL_HISTORY_FILE = os.path.join(_DIR, 'ema_confluence_trades_all_history.json')
LOG_FILE         = os.path.join(_DIR, 'ema_confluence_algo.log')

if not any(isinstance(h, RotatingFileHandler) and getattr(h, '_emac_sink', False) for h in logger.handlers):
    _file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    _file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    _file_handler._emac_sink = True
    logger.addHandler(_file_handler)
    logger.setLevel(logging.INFO)

_POLL_SECS = 15          # daily-swing strategy — no need for an aggressive poll
_HARD_STOP_MIN = 15 * 60 + 30   # thread exits for the day at/after 15:30 IST
_EMA_LOOKBACK_DAYS = 800  # ~2.2 calendar years — comfortably covers the 200-day EMA

# Index symbols resolve to their own instrument token (same map the backtest
# route uses); every other symbol is an F&O equity, 'NSE:{symbol}-EQ' (Fyers)
# or the bare tradingsymbol (Kite).
_FYERS_INDICES = {'NIFTY': 'NSE:NIFTY50-INDEX', 'BANKNIFTY': 'NSE:NIFTYBANK-INDEX'}

_instances: Dict[str, 'EmaConfluenceAlgo'] = {}


def get_instance(username: str) -> Optional['EmaConfluenceAlgo']:
    return _instances.get(username)


class _PrefixLogger(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[EmaConfluence] {msg}", kwargs


class EmaConfluenceAlgo:
    """Live EMA Confluence Breakout signal detector + simulated futures executor."""

    def __init__(self, username: str):
        self.username = username
        self.log = _PrefixLogger(logger, {})
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name='EmaConfluenceAlgoThread')
        self._thread.start()
        _instances[self.username] = self
        self.log.info("Monitoring thread started (paper mode)")

    def stop(self) -> None:
        self._stop_event.set()
        _instances.pop(self.username, None)
        self.log.info("Stop requested")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── State ────────────────────────────────────────────────────────────

    def _fresh_state(self) -> Dict[str, Any]:
        from trading_app.Backtest.ema_symbol_universe import EMA_SYMBOL_DEFAULTS
        return {
            'last_scan_date': None,
            'stocks': {sym: {'phase': 'pending_scan'} for sym in EMA_SYMBOL_DEFAULTS},
        }

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
            if not isinstance(state, dict) or 'stocks' not in state:
                return self._fresh_state()
            # A symbol added to EMA_SYMBOL_DEFAULTS after this state file was
            # first written wouldn't otherwise get scanned — backfill it.
            from trading_app.Backtest.ema_symbol_universe import EMA_SYMBOL_DEFAULTS
            for sym in EMA_SYMBOL_DEFAULTS:
                state['stocks'].setdefault(sym, {'phase': 'pending_scan'})
            return state
        except Exception:
            return self._fresh_state()

    def _save_state(self, state: Dict[str, Any]) -> None:
        with self._state_lock:
            try:
                with open(STATE_FILE, 'w') as f:
                    json.dump(state, f, indent=2, default=str)
            except Exception as e:
                self.log.error(f"State save failed: {e}")

    def _append_history(self, record: Dict[str, Any]) -> None:
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
            self.log.error(f"History append failed: {e}")

    # ── Env helpers ──────────────────────────────────────────────────────

    def _uvar(self, key: str, default: str = '') -> str:
        from trading_app.app.utils.user_env import UserEnvManager
        return (UserEnvManager.get_user_var(self.username, key) or default).strip()

    def _mode(self) -> str:
        mode = self._uvar('EMA_CONFLUENCE_MODE', 'paper').lower()
        if mode == 'live':
            self.log.warning("EMA_CONFLUENCE_MODE=live requested but live execution is not implemented yet — running in paper mode")
            return 'paper'
        return 'paper'

    # ── Data ─────────────────────────────────────────────────────────────

    def _get_provider(self) -> Any:
        from trading_app.service.provider_logic import get_data_provider
        return get_data_provider(user=self.username)

    def _underlying_token(self, symbol: str, is_fyers: bool) -> Any:
        if is_fyers:
            return _FYERS_INDICES.get(symbol, f'NSE:{symbol}-EQ')
        kite_indices = {'NIFTY': 256265, 'BANKNIFTY': 260105}
        return kite_indices.get(symbol, symbol)

    def _resolve_future(self, provider: Any, is_fyers: bool, symbol: str) -> Tuple[Optional[Any], int]:
        """(ltp_token, lot_size) for symbol's nearest-expiry FUTURES contract."""
        try:
            if is_fyers:
                token = provider.find_future_symbol(symbol)
                if not token:
                    return None, 1
                lot_size = int(provider.get_lot_size(symbol) or 1)
                return token, lot_size
            from trading_app.service.kite_order_services import KiteService
            svc = KiteService(kite_instance=provider)
            ts = svc.get_future_symbol(symbol)
            if not ts:
                return None, 1
            lot_size = int(svc.get_lot_size(symbol) or 1)
            return f'NFO:{ts}', lot_size
        except Exception as e:
            self.log.warning(f"{symbol}: future resolution failed: {e}")
            return None, 1

    def _ensure_future_token(self, provider: Any, is_fyers: bool, symbol: str, s: Dict[str, Any]) -> Optional[Any]:
        if s.get('future_token'):
            return s['future_token']
        token, lot_size = self._resolve_future(provider, is_fyers, symbol)
        if token:
            s['future_token'] = token
            s['lot_size'] = lot_size
        return token

    def _get_future_ltp_batch(self, provider: Any, tokens: Dict[str, Any]) -> Dict[str, float]:
        uniq_tokens = list({t for t in tokens.values() if t})
        if not uniq_tokens:
            return {}
        try:
            data = provider.ltp(uniq_tokens) or {}
        except Exception as e:
            self.log.warning(f"batch future LTP fetch failed: {e}")
            return {}
        result: Dict[str, float] = {}
        for symbol, token in tokens.items():
            if not token:
                continue
            v = data.get(token, {})
            lp = v.get('last_price') if isinstance(v, dict) else None
            if lp:
                result[symbol] = float(lp)
        return result

    # ── Daily signal scan (once per symbol per day, off yesterday's close) ─

    def _ema_engine(self):
        from trading_app.Backtest.ema_pullback_engine import EmaPullbackEngine
        return EmaPullbackEngine

    def _scan_one(self, provider: Any, is_fyers: bool, symbol: str, cfg: Dict[str, Any],
                  s: Dict[str, Any], from_date: str, to_date: str) -> None:
        EmaPullbackEngine = self._ema_engine()
        token = self._underlying_token(symbol, is_fyers)
        try:
            candles = provider.historical_data(
                instrument_token=token, from_date=from_date, to_date=to_date,
                interval='day', use_cache=True,
            )
        except Exception as e:
            self.log.warning(f"{symbol}: daily-candle fetch failed: {e}")
            s['phase'] = 'no_setup'
            return
        if not candles:
            s['phase'] = 'no_setup'
            return
        try:
            engine = EmaPullbackEngine(daily_df=pd.DataFrame(candles), target_pct=cfg['target_pct'])
        except Exception as e:
            self.log.warning(f"{symbol}: EMA prep failed: {e}")
            s['phase'] = 'no_setup'
            return
        if engine.daily_df.empty:
            s['phase'] = 'no_setup'
            return
        last = engine.daily_df.iloc[-1]
        direction = engine._signal_direction(last)
        if direction is None:
            s['phase'] = 'no_setup'
            return

        want_long  = cfg['direction'] != 'short'
        want_short = cfg['direction'] != 'long'
        if (direction == 'Long' and not want_long) or (direction == 'Short' and not want_short):
            s['phase'] = 'no_setup'
            return

        s['phase']        = 'watching'
        s['direction']     = direction
        s['trigger_level'] = round(float(last['high'] if direction == 'Long' else last['low']), 2)
        s['sl_level']      = round(float(last['low']  if direction == 'Long' else last['high']), 2)
        s['target_pct']    = cfg['target_pct']
        s['signal_date']   = str(last['datetime'].date())
        self.log.info(f"{symbol}: setup found — {direction.upper()} "
                       f"trigger={s['trigger_level']} sl={s['sl_level']} (signal candle {s['signal_date']})")

    def _run_daily_scan(self, provider: Any, is_fyers: bool, state: Dict[str, Any]) -> None:
        from trading_app.Backtest.ema_symbol_universe import EMA_SYMBOL_DEFAULTS
        today = date.today()
        yesterday = today - timedelta(days=1)
        from_date = (today - timedelta(days=_EMA_LOOKBACK_DAYS)).isoformat()
        to_date = yesterday.isoformat()  # only fully-closed candles — no look-ahead

        stocks = state['stocks']
        scanned = 0
        for symbol, cfg in EMA_SYMBOL_DEFAULTS.items():
            s = stocks.setdefault(symbol, {'phase': 'pending_scan'})
            if s.get('phase') in ('watching', 'in_position'):
                continue  # only one pending/open setup at a time, same as the backtest
            try:
                self._scan_one(provider, is_fyers, symbol, cfg, s, from_date, to_date)
                scanned += 1
            except Exception as e:
                self.log.warning(f"{symbol}: scan failed: {e}")
                s['phase'] = 'no_setup'
        self.log.info(f"Daily scan complete — {scanned} symbols scanned for {to_date}")

    # ── Paper trade lifecycle ───────────────────────────────────────────

    def _fire_paper_entry(self, symbol: str, s: Dict[str, Any], ltp: float, lots: int) -> None:
        direction   = s['direction']
        target_pct  = float(s.get('target_pct', 5.0))
        entry_price = round(float(ltp), 2)
        target_level = round(entry_price * (1 + target_pct / 100), 2) if direction == 'Long' \
            else round(entry_price * (1 - target_pct / 100), 2)
        lot_size = int(s.get('lot_size', 1) or 1)
        qty = max(1, lots) * lot_size

        s['entry_price']  = entry_price
        s['target_level'] = target_level
        s['qty']           = qty
        s['entry_time']    = datetime.now().isoformat()
        s['phase']         = 'in_position'
        self.log.info(
            f"[PAPER] {symbol}: ENTERED {direction.upper()} future @ {entry_price} "
            f"sl={s['sl_level']} tgt={target_level} qty={qty}"
        )

    def _record_exit(self, symbol: str, s: Dict[str, Any], exit_price: float, reason: str) -> None:
        direction   = s['direction']
        entry_price = s['entry_price']
        qty         = s['qty']
        pnl = (exit_price - entry_price) * qty if direction == 'Long' else (entry_price - exit_price) * qty
        record = {
            'symbol': symbol, 'direction': 'BUY' if direction == 'Long' else 'SELL',
            'mode': 'paper', 'qty': qty, 'lot_size': s.get('lot_size'),
            'entry_price': entry_price, 'exit_price': round(exit_price, 2),
            'sl_price': s.get('sl_level'), 'target_price': s.get('target_level'),
            'pnl': round(pnl, 2), 'reason': reason,
            'signal_date': s.get('signal_date'),
            'entry_time': s.get('entry_time', ''), 'exit_time': datetime.now().isoformat(),
        }
        self._append_history(record)
        self.log.info(f"[PAPER] {symbol}: EXIT ({reason}) @ {exit_price}, P&L ₹{record['pnl']}")

    def _reset_for_next_scan(self, s: Dict[str, Any]) -> None:
        future_token = s.get('future_token')
        lot_size = s.get('lot_size')
        s.clear()
        s['phase'] = 'pending_scan'
        # Future resolution doesn't change intraday — keep it cached across
        # a same-symbol re-arm so it's not re-resolved on every new setup.
        if future_token:
            s['future_token'] = future_token
            s['lot_size'] = lot_size

    # ── Main loop ────────────────────────────────────────────────────────

    def _tick(self, provider: Any, is_fyers: bool, state: Dict[str, Any],
              algo_active: bool, lots: int) -> None:
        today_str = date.today().isoformat()
        if state.get('last_scan_date') != today_str:
            self._run_daily_scan(provider, is_fyers, state)
            state['last_scan_date'] = today_str

        stocks = state['stocks']
        watching = {sym: s for sym, s in stocks.items() if s.get('phase') == 'watching'}
        inpos    = {sym: s for sym, s in stocks.items() if s.get('phase') == 'in_position'}
        if not watching and not inpos:
            return

        tokens: Dict[str, Any] = {}
        for symbol, s in {**watching, **inpos}.items():
            token = self._ensure_future_token(provider, is_fyers, symbol, s)
            if token:
                tokens[symbol] = token

        ltps = self._get_future_ltp_batch(provider, tokens)

        for symbol, s in watching.items():
            ltp = ltps.get(symbol)
            if ltp is None:
                continue
            direction = s['direction']
            trigger = s['trigger_level']
            crossed = (direction == 'Long' and ltp >= trigger) or (direction == 'Short' and ltp <= trigger)
            if not crossed:
                continue
            if algo_active:
                self._fire_paper_entry(symbol, s, ltp, lots)
            else:
                self.log.info(f"{symbol}: {direction.upper()} trigger hit @ {ltp} — "
                               f"no paper entry (EMA_CONFLUENCE_ACTIVE off)")
                self._reset_for_next_scan(s)

        for symbol, s in inpos.items():
            ltp = ltps.get(symbol)
            if ltp is None:
                continue
            direction = s['direction']
            sl, tgt = s['sl_level'], s['target_level']
            exit_reason = exit_price = None
            if direction == 'Long':
                if ltp <= sl:
                    exit_reason, exit_price = 'SL', sl
                elif ltp >= tgt:
                    exit_reason, exit_price = 'TARGET', tgt
            else:
                if ltp >= sl:
                    exit_reason, exit_price = 'SL', sl
                elif ltp <= tgt:
                    exit_reason, exit_price = 'TARGET', tgt
            if exit_reason is not None:
                self._record_exit(symbol, s, exit_price, exit_reason)
                self._reset_for_next_scan(s)

    def _monitor_loop(self) -> None:
        try:
            self.log.info(f"Monitor thread started for {self.username}")
            provider = None
            for _ in range(30):
                if self._stop_event.is_set():
                    return
                provider = self._get_provider()
                if provider:
                    break
                time.sleep(2)
            if not provider:
                self.log.error("Data provider unavailable — aborting for today")
                return

            is_fyers = hasattr(provider, 'fyers')
            state = self._load_state()
            self._save_state(state)

            lots = max(1, int(self._uvar('EMA_CONFLUENCE_LOTS', '1') or 1))
            algo_active = self._uvar('EMA_CONFLUENCE_ACTIVE', 'false').lower() == 'true'
            self._mode()  # logs the paper-only fallback warning once, if MODE=live was requested

            while not self._stop_event.is_set():
                now = datetime.now()
                now_mins = now.hour * 60 + now.minute
                if now_mins >= _HARD_STOP_MIN:
                    break
                try:
                    self._tick(provider, is_fyers, state, algo_active, lots)
                except Exception as e:
                    self.log.error(f"tick error: {e}", exc_info=True)
                self._save_state(state)
                self._stop_event.wait(_POLL_SECS)

            self.log.info("Monitor thread exiting for the day")
        except Exception as e:
            self.log.error(f"monitor loop crashed: {e}", exc_info=True)
