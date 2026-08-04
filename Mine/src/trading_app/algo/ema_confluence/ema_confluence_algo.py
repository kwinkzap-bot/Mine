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
  no_setup      -> scanned, no order armed — either no signal candle is
                    outstanding, or the one that is has already broken out
                    (see below), or its direction isn't enabled for this
                    symbol (see EMA_SYMBOL_DEFAULTS)
  watching      -> an armed order: trigger/SL off the signal candle's own
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

Every watching/open symbol is marked to market on each tick (`ltp`, plus
`unrealized_pnl` while in a position) and a closed trade leaves its
`last_entry_*` / `last_exit_*` / `last_pnl` behind, so the Symbol Status grid
can present a paper trade the same way a live one would read: which monthly
contract it is on (`future_month`), entry/exit times, the future's current
value and the running P&L.

The daily scan (one per symbol per day, only for symbols not already
watching/in_position — "only one pending order at a time", same as the
backtest) runs once each morning against candles up to the most recently
COMPLETED one (to_date = yesterday, never today's still-forming candle — no
look-ahead). It REPLAYS the whole strategy over that history and adopts
whatever order the backtest would have resting right now, rather than asking
"is the newest candle a signal?". The difference matters: this strategy
routinely enters weeks after its signal candle, so the newest-candle test made
a signal visible for exactly one morning — anything that fired before the algo
first ran, or on a day whose scan came back empty, was invisible forever. The
replay makes a cold start, a missed day, and a bad data fetch all self-heal.
A breakout that already fired while nothing was watching is NOT entered late
(the fill price is gone); it's logged once and re-arms when that move ends.

Gating (see env/Mine.env):
  EMA_CONFLUENCE_ACTIVE = true/false   — gates entries (paper fills); the
                                          thread always scans/logs regardless
                                          (same convention as TMF/RTP).
  EMA_CONFLUENCE_MODE   = paper (default) | live (not implemented — falls
                                          back to paper).
  EMA_CONFLUENCE_LOTS   = 1             — paper lot count per entry.

This module is PAPER-TRADE ONLY — no real broker orders are ever placed.
"""
import json
import logging
import os
import re
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
# ~3.3 calendar years (≈820 trading bars). Must match the backtest's warm-up
# (_EMA_BT_WARMUP_DAYS in routes/api.py): a 200-day EMA keeps drifting toward
# its true value for hundreds of bars, so a shorter run-up here would have live
# scanning fire on different EMA values than the backtest that validated it.
# Only the FALLBACK direct-fetch path uses this — the daily scan normally reads
# the same full-history store the backtest does (see _daily_history).
_EMA_LOOKBACK_DAYS = 1200

# History depth requested from the per-stock daily store. Covers the backtest's
# own span (its form starts at 2017 and fetches a 1200-day warm-up before that),
# so the live simulation walks the same bars the backtest did.
_STORE_HISTORY_DAYS = 4800

# The date the live simulation starts trading from — everything before it is
# EMA warm-up only. Must match the backtest form's default Start Date: the
# armed order this algo adopts is the end state of a chain of simulated trades,
# and a different start date can produce a different chain (a position opened
# earlier blocks later signals), so the two would drift apart.
_LIVE_SIM_START = '2017-01-01'

# Index symbols resolve to their own instrument token (same map the backtest
# route uses); every other symbol is an F&O equity, 'NSE:{symbol}-EQ' (Fyers)
# or the bare tradingsymbol (Kite).
_FYERS_INDICES = {
    'NIFTY':     'NSE:NIFTY50-INDEX',
    'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
    'SENSEX':    'BSE:SENSEX-INDEX',
}
# Underlyings whose futures are BSE (BFO) contracts, not NFO ones. The Kite
# path here only ever resolves NFO futures (KiteService.get_nfo_instruments),
# so these are Fyers-only — see _resolve_future.
_BSE_UNDERLYINGS = {'SENSEX'}

_instances: Dict[str, 'EmaConfluenceAlgo'] = {}

# Monthly futures contract tail — 'NSE:NHPC26AUGFUT' / 'NFO:NHPC26AUGFUT'.
# Fyers tokens and Kite tradingsymbols share the {ROOT}{YY}{MMM}FUT shape.
_CONTRACT_RE = re.compile(r'(\d{2})([A-Z]{3})FUT$')


def _contract_month_label(token: Any) -> Optional[str]:
    """'NSE:NHPC26AUGFUT' -> 'AUG 2026' — which monthly contract the paper
    trade is on. None when the token isn't a monthly future (the UI then
    falls back to showing the raw token)."""
    m = _CONTRACT_RE.search(str(token or '').upper())
    return f"{m.group(2)} 20{m.group(1)}" if m else None


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
            # …and one DROPPED from the universe would otherwise linger forever,
            # skewing the status page's phase counts. Drop it — unless it still
            # has an armed trigger or an open paper position, which must be
            # seen through to its exit rather than abandoned mid-trade.
            for sym in [s for s in state['stocks'] if s not in EMA_SYMBOL_DEFAULTS]:
                if state['stocks'][sym].get('phase') in ('watching', 'in_position'):
                    self.log.info(f"{sym}: dropped from the universe but still "
                                  f"{state['stocks'][sym]['phase']} — keeping until it closes")
                    continue
                state['stocks'].pop(sym)
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
        kite_indices = {'NIFTY': 256265, 'BANKNIFTY': 260105, 'SENSEX': 265}
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
            if symbol in _BSE_UNDERLYINGS:
                # KiteService resolves futures out of the NFO master only, so a
                # BSE contract would silently come back as a wrong/absent token.
                # Better to skip the symbol outright than trade a bad one.
                self.log.warning(f"{symbol}: BSE futures aren't resolvable on the Kite path — skipping")
                return None, 1
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
        today_str = date.today().isoformat()
        # The cached token names one specific monthly contract, so it goes dead
        # at that month's expiry — re-resolve once a day so a symbol that is
        # merely watching always arms against the current front-month future.
        # An OPEN paper position stays pinned to the contract it was entered
        # on: rolling mid-trade would re-price the position against a different
        # instrument than the one the entry was filled on.
        fresh = s.get('future_resolved_on') == today_str or s.get('phase') == 'in_position'
        if s.get('future_token') and fresh:
            # Label a token resolved before this field existed (e.g. a position
            # already open when the app restarted) off the token itself — a
            # pinned contract must not be re-resolved just to name its month.
            if not s.get('future_month'):
                s['future_month'] = _contract_month_label(s['future_token'])
            return s['future_token']
        token, lot_size = self._resolve_future(provider, is_fyers, symbol)
        if token:
            s['future_token']        = token
            s['lot_size']            = lot_size
            s['future_month']        = _contract_month_label(token)
            s['future_resolved_on']  = today_str
        # A failed re-resolution keeps yesterday's token rather than going dark.
        return token or s.get('future_token')

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

    def _daily_history(self, provider: Any, is_fyers: bool, symbol: str,
                       to_date: str) -> Optional[pd.DataFrame]:
        """Daily bars up to and including `to_date` ('YYYY-MM-DD'), as a frame
        the engine can consume.

        Primary source is the per-stock daily store the BACKTEST reads
        (filters/candle_store) — same bars in, same EMAs out, so a signal the
        backtest sees is a signal this scan sees. It serves locally and only
        re-contacts the provider for the missing tail. Falls back to a direct
        _EMA_LOOKBACK_DAYS fetch if the store comes back empty, so a store
        problem degrades to the old behaviour instead of going dark.
        """
        token = self._underlying_token(symbol, is_fyers)
        end_dt = datetime.strptime(to_date, '%Y-%m-%d')
        try:
            from trading_app.filters.candle_store import get_daily_history
            df = get_daily_history(provider, token, symbol, _STORE_HISTORY_DAYS, end_dt)
            if df is not None and not df.empty:
                return df.rename_axis('date').reset_index()
            self.log.warning(f"{symbol}: daily store returned nothing up to {to_date} — trying a direct fetch")
        except Exception as e:
            self.log.warning(f"{symbol}: daily store read failed ({e}) — trying a direct fetch")

        from_date = (end_dt - timedelta(days=_EMA_LOOKBACK_DAYS)).date().isoformat()
        try:
            candles = provider.historical_data(
                instrument_token=token, from_date=from_date, to_date=to_date,
                interval='day', use_cache=True,
            )
        except Exception as e:
            self.log.warning(f"{symbol}: daily-candle fetch failed: {e}")
            return None
        if not candles:
            self.log.warning(f"{symbol}: provider returned no daily candles for {from_date}..{to_date}")
            return None
        return pd.DataFrame(candles)

    def _scan_one(self, provider: Any, is_fyers: bool, symbol: str, cfg: Dict[str, Any],
                  s: Dict[str, Any], to_date: str) -> Optional[str]:
        """Re-derive this symbol's CURRENT armed order by replaying the whole
        strategy over its history, exactly as the backtest does. Returns the
        date of the newest candle it judged on, or None if it had no data.

        Testing only the newest candle (what this used to do) meant a signal was
        visible for one morning and then gone forever — the backtest arms an
        order off ANY signal candle and holds it with no expiry, so it routinely
        enters weeks after the signal. Any signal candle that landed before this
        algo first ran, or on a day a scan came back empty, was invisible to it.
        Replaying instead makes a cold start (or a missed day) self-heal.

        A breakout that ALREADY fired while nothing was watching is deliberately
        not entered late — the fill price is gone. It's logged once and left to
        re-arm when the simulated trade closes.
        """
        EmaPullbackEngine = self._ema_engine()
        df = self._daily_history(provider, is_fyers, symbol, to_date)
        if df is None or df.empty:
            s['phase'] = 'no_setup'
            return None
        try:
            engine = EmaPullbackEngine(
                daily_df=df,
                enable_long=cfg['direction'] != 'short',
                enable_short=cfg['direction'] != 'long',
                target_pct=cfg['target_pct'],
                start_date=_LIVE_SIM_START,
            )
            engine.run()
        except Exception as e:
            self.log.warning(f"{symbol}: EMA replay failed: {e}")
            s['phase'] = 'no_setup'
            return None
        if engine.daily_df.empty:
            self.log.warning(f"{symbol}: no usable daily bars up to {to_date}")
            s['phase'] = 'no_setup'
            return None

        # What the scan actually looked at — without this, a scan that silently
        # ran on stale/short data is indistinguishable from one that genuinely
        # found nothing (which is exactly what made the 2026-07-27 KFINTECH
        # signal impossible to explain after the fact).
        scanned_candle = str(engine.daily_df.iloc[-1]['datetime'].date())
        s['scanned_candle'] = scanned_candle
        s['scanned_at']     = datetime.now().isoformat()

        pending = engine.pending_order
        if pending is None:
            # No order resting — drop any stale armed-signal marker so a later
            # re-arm is always treated (and logged) as new.
            s.pop('signal_date', None)
            open_trade = engine.open_trade
            if open_trade is None:
                s.pop('missed_signal_date', None)
            else:
                signal_date = str(pd.Timestamp(open_trade['signal_time']).date())
                # Log once per missed setup, not once per daily scan.
                if s.get('missed_signal_date') != signal_date:
                    s['missed_signal_date'] = signal_date
                    self.log.info(
                        f"{symbol}: {open_trade['direction'].upper()} setup from {signal_date} already "
                        f"broke out at {open_trade['entry_price']} while nothing was watching — "
                        f"not entering late; will re-arm once that move is done"
                    )
            s['phase'] = 'no_setup'
            return scanned_candle

        direction    = pending['direction']
        signal_date  = str(pd.Timestamp(pending['signal_time']).date())
        already_seen = s.get('signal_date') == signal_date

        s['phase']         = 'watching'
        s['direction']     = direction
        s['trigger_level'] = round(float(pending['trigger_level']), 2)
        s['sl_level']      = round(float(pending['sl_level']), 2)
        s['target_pct']    = cfg['target_pct']
        s['signal_date']   = signal_date
        s.pop('missed_signal_date', None)
        if not already_seen:
            age = (datetime.strptime(scanned_candle, '%Y-%m-%d')
                   - datetime.strptime(signal_date, '%Y-%m-%d')).days
            self.log.info(f"{symbol}: setup found — {direction.upper()} "
                          f"trigger={s['trigger_level']} sl={s['sl_level']} "
                          f"(signal candle {signal_date}, {age}d old; scanned up to {scanned_candle})")
        return scanned_candle

    def _run_daily_scan(self, provider: Any, is_fyers: bool, state: Dict[str, Any],
                        only_pending: bool = False) -> None:
        """The once-a-day sweep. only_pending=True is the catch-up pass for
        symbols added to EMA_SYMBOL_DEFAULTS after today's sweep already ran —
        it touches just those, leaving every already-scanned symbol alone."""
        from trading_app.Backtest.ema_symbol_universe import EMA_SYMBOL_DEFAULTS
        today = date.today()
        yesterday = today - timedelta(days=1)
        to_date = yesterday.isoformat()  # only fully-closed candles — no look-ahead

        stocks = state['stocks']
        scanned = 0
        armed = 0
        no_data: List[str] = []
        judged_on: Dict[str, int] = {}
        for symbol, cfg in EMA_SYMBOL_DEFAULTS.items():
            s = stocks.setdefault(symbol, {'phase': 'pending_scan'})
            if s.get('phase') in ('watching', 'in_position'):
                continue  # only one pending/open setup at a time, same as the backtest
            if only_pending and s.get('phase') != 'pending_scan':
                continue
            try:
                candle = self._scan_one(provider, is_fyers, symbol, cfg, s, to_date)
                scanned += 1
                if s.get('phase') == 'watching':
                    armed += 1
                if candle:
                    judged_on[candle] = judged_on.get(candle, 0) + 1
                else:
                    no_data.append(symbol)
            except Exception as e:
                self.log.warning(f"{symbol}: scan failed: {e}")
                s['phase'] = 'no_setup'
                no_data.append(symbol)
        # Which candle each symbol was actually judged on. A single date means a
        # clean sweep; a spread means some symbols were judged on older bars
        # than others, so "no setup" doesn't mean the same thing for all of them.
        spread = ', '.join(f"{d}×{n}" for d, n in sorted(judged_on.items()))
        label = 'Catch-up scan' if only_pending else 'Daily scan'
        self.log.info(
            f"{label} complete — {scanned} symbols scanned up to {to_date}, {armed} armed, "
            f"{len(no_data)} with no data (candles judged: {spread or 'none'})"
        )
        if no_data:
            self.log.warning(f"{label}: no usable candles for {len(no_data)} symbol(s) — "
                             f"NOT evaluated for {to_date}: {', '.join(sorted(no_data))}")

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
        s['ltp']            = entry_price   # marked to market from the next tick on
        s['unrealized_pnl'] = 0.0
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
        # Carried through the reset below so the Symbol Status grid can still
        # show the round trip (entry time / exit time / realised P&L) for a
        # symbol that has already been in and out.
        s['last_entry_time']  = s.get('entry_time')
        s['last_entry_price'] = entry_price
        s['last_exit_time']   = record['exit_time']
        s['last_exit_price']  = record['exit_price']
        s['last_exit_reason'] = reason
        s['last_pnl']         = record['pnl']
        self.log.info(f"[PAPER] {symbol}: EXIT ({reason}) @ {exit_price}, P&L ₹{record['pnl']}")

    # Survives _reset_for_next_scan: the resolved contract (which doesn't
    # change intraday, so a same-symbol re-arm needn't re-resolve it) and the
    # last closed paper trade's audit trail.
    _KEEP_ON_RESET = (
        'future_token', 'lot_size', 'future_month', 'future_resolved_on',
        'last_entry_time', 'last_entry_price', 'last_exit_time',
        'last_exit_price', 'last_exit_reason', 'last_pnl',
    )

    def _reset_for_next_scan(self, s: Dict[str, Any]) -> None:
        kept = {k: s[k] for k in self._KEEP_ON_RESET if k in s}
        s.clear()
        s['phase'] = 'pending_scan'
        s.update(kept)

    # ── Main loop ────────────────────────────────────────────────────────

    def _tick(self, provider: Any, is_fyers: bool, state: Dict[str, Any],
              algo_active: bool, lots: int) -> None:
        today_str = date.today().isoformat()
        if state.get('last_scan_date') != today_str:
            self._run_daily_scan(provider, is_fyers, state)
            state['last_scan_date'] = today_str
        elif any(s.get('phase') == 'pending_scan' for s in state['stocks'].values()):
            # Today's sweep already ran, but EMA_SYMBOL_DEFAULTS has since
            # grown (see _load_state's backfill) — scan the newcomers now
            # instead of leaving them dark until tomorrow morning.
            self._run_daily_scan(provider, is_fyers, state, only_pending=True)

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

        # Mark every armed/open symbol to market before acting on it, so the
        # status page can show the future's current value and — for an open
        # paper position — its running P&L, exactly like a live trade would.
        for symbol, s in {**watching, **inpos}.items():
            ltp = ltps.get(symbol)
            if ltp is None:
                continue
            s['ltp']      = round(float(ltp), 2)
            s['ltp_time'] = datetime.now().isoformat()
            if s.get('phase') == 'in_position' and s.get('entry_price') and s.get('qty'):
                move = (ltp - s['entry_price']) if s['direction'] == 'Long' else (s['entry_price'] - ltp)
                s['unrealized_pnl'] = round(move * s['qty'], 2)

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
