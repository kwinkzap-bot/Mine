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
  TMF_EXIT_HOUR / TMF_EXIT_MINUTE      — cutoff for the Time Exit square-off.
                                          Defaults to 15:05, EARLIER than the
                                          backtest's 15:18: Zerodha rejects
                                          fresh MIS orders after 15:10, so a
                                          15:18 square-off cannot be placed at
                                          all (2026-08-03: every one rejected).
                                          The few minutes of divergence from
                                          the backtest buy an exit that
                                          actually executes.
  TMF_USE_SL_RISK_FILTER=false         — skip a setup whose sized rupee stop
  TMF_MAX_SL_RISK=5000                    exceeds the cap. OFF by default,
                                          matching the Backtest page's own
                                          default (its "SL Risk Filter"
                                          checkbox loads unchecked), so live
                                          takes exactly the trade set the
                                          default backtest scores. Turning it
                                          on caps per-trade rupee risk but
                                          makes live SKIP setups the backtest
                                          counted.

Every stock with something live on it (watching / pending_entry /
in_position) is marked to market on each tick — `ltp` plus `unrealized_pnl`
across the open broker legs — and a closed leg leaves `exit_time`,
`exit_price`, `exit_reason` and a running `realized_pnl` on the stock's own
state, so the Stock Status grid can show a trade end to end. Those numbers
are display only: entries and exits are driven by the real broker orders,
never by them.

Known live/backtest divergences that remain by nature (not bugs):
  - The backtest checks a bar's High/Low for SL/Target/trigger crossings
    (catches any touch within the bar); the live watch loop instead polls
    last-traded-price every _POLL_SECS seconds — a brief intra-poll spike
    through a level can be missed, the same tolerance RTP already accepts
    for its own per-second spot polling.
  - The backtest's Target is the day's FULL session Low/High (hindsight);
    live can only use the session Low/High up to the entry fill, so live
    targets are nearer and its winners are smaller.
  - The backtest fills a gap straight through the trigger at the bar's
    OPEN; the live entry is a LIMIT priced _ENTRY_MARKETABLE_PAD through
    the last traded price, so it fills immediately at up to that much
    beyond the trigger — never a market order, but not the exact trigger
    price the backtest books either.
  - A late app start means the trigger cross has already happened, so the
    stock sits in `watching` and never enters — the backtest, replaying the
    whole day, always takes it.
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

# How long an unfilled entry LIMIT order is allowed to sit at the trigger
# price before the remainder is cancelled. The backtest assumes the entry
# fills the instant price crosses the trigger; a LIMIT order that rests for
# half an hour and then fills (KFINTECH, 2026-07-27: placed 11:08, filled
# 11:40) is a completely different trade from the one the backtest scored.
# Whatever filled inside the window is kept and managed; the rest is dropped.
_ENTRY_ORDER_TTL_SECS = 120

# How far THROUGH the last traded price the entry LIMIT is priced so it is
# immediately marketable. A LIMIT resting exactly AT the trigger is, by the
# very definition of the trigger, on the wrong side of the market: a short's
# trigger fires once price has already fallen below it, so a SELL LIMIT at
# the trigger sits above the market and only fills if price climbs back.
# That is why VOLTAS (2026-08-03, trigger 1334.4 with price at 1331.2 and
# falling) sat unfilled for its whole 120s life and was cancelled, and why
# JSWSTEEL the same day filled just 15 of the 392 shares it was sized for.
# Padding past the last price makes the order marketable — it fills at once,
# at the touch-or-better price the backtest assumes — while still being a
# LIMIT order (never a market order), so this pad is also the hard cap on
# how far through the book a single fill can slip.
_ENTRY_MARKETABLE_PAD = 0.002   # 0.2%

_DEFAULT_TICK_SIZE = 0.05  # fallback when the broker's tick map is unavailable


def _round_to_tick(price: float, tick: float, mode: str) -> float:
    """Snap a computed price to a valid exchange tick for that scrip. `tick`
    is the scrip's REAL tick size (see KiteService.get_tick_size) — assuming
    0.05 for everything is what got GVT&D (2026-07-28) and BDL (2026-07-31)
    rejected outright with "Tick size for this script is 0.10", losing two
    trades the backtest counted. mode='up' rounds away from zero to the next
    tick, mode='down' to the previous one; callers pick whichever direction
    keeps the price's meaning intact (a trigger rounds so it still clears
    the level by at least the buffer, an SL rounds so it can't sit tighter
    than the candle level it came from)."""
    if not tick or tick <= 0:
        tick = _DEFAULT_TICK_SIZE
    ticks = price / tick
    ticks = math.ceil(ticks) if mode == 'up' else math.floor(ticks)
    return round(ticks * tick, 2)


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

    def _tick_size_for(self, symbol: str) -> float:
        """The scrip's real NSE tick size, via whichever broker is wired up
        (the map is exchange-wide and day-cached inside KiteService, so this
        is a dict lookup after the first call). Falls back to 0.05 when no
        broker is configured — scan-only mode places no orders, so nothing
        can be rejected for it."""
        for _, svc in self._broker_list:
            try:
                return float(svc.get_tick_size(symbol))
            except Exception as e:
                logger.warning(f"[TMF] {symbol}: tick-size lookup failed: {e}")
        return _DEFAULT_TICK_SIZE

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

    def _scan_one(self, provider: Any, is_fyers: bool, symbol: str, s: Dict[str, Any],
                   capital_per_trade: float, max_sl_risk: Optional[float]) -> None:
        from trading_app.Backtest.tmf_symbol_universe import TMF_SYMBOL_DEFAULTS, TMF_EQUITY_LEVERAGE
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
        direction = setup['direction']
        tick = self._tick_size_for(symbol)
        # Trigger rounds away from the candle level (so it still means
        # "cleared the level by at least the buffer"); the SL rounds away
        # from the entry, so tick-snapping can never quietly tighten the
        # stop below the candle high/low the setup actually came from.
        trigger  = _round_to_tick(float(setup['trigger']), tick, 'up' if direction == 'long' else 'down')
        sl_level = _round_to_tick(float(setup['sl_level']), tick, 'up' if direction == 'short' else 'down')

        # The backtest's optional rupee stop-loss cap (use_sl_risk_filter /
        # sl_risk_max) — OFF by default on both sides, so max_sl_risk is
        # normally None and every valid setup is taken. When it IS enabled,
        # the setup is valid but the position at this capital risks more per
        # trade than the cap allows, so it's dropped. Note this sizes off the
        # TRIGGER, not a fill: the entry hasn't happened yet, so the check has
        # to run on the level the order will be placed at.
        qty = max(1, int((capital_per_trade * TMF_EQUITY_LEVERAGE) // trigger)) if trigger > 0 else 1
        sl_risk = abs(trigger - sl_level) * qty
        if max_sl_risk is not None and sl_risk > max_sl_risk:
            s['phase'] = 'done'
            s['direction'] = direction
            s['trigger'] = trigger
            s['sl_level'] = sl_level
            s['exit_reason'] = f'Skipped (SL risk ₹{sl_risk:,.0f} > ₹{max_sl_risk:,.0f})'
            logger.info(f"[TMF] {symbol}: setup found — {direction.upper()} trigger={trigger} "
                        f"sl={sl_level} — SKIPPED, sized SL risk ₹{sl_risk:,.0f} exceeds ₹{max_sl_risk:,.0f}")
            return

        s['phase']     = 'watching'
        s['direction'] = direction
        s['trigger']   = trigger
        s['sl_level']  = sl_level
        s['sl_risk']   = round(sl_risk, 2)
        logger.info(f"[TMF] {symbol}: setup found — {direction.upper()} "
                    f"trigger={trigger} sl={sl_level} (tick {tick}, SL risk ₹{sl_risk:,.0f})")

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
        tick = self._tick_size_for(symbol)
        # Rounded back toward the entry, never past the session extreme —
        # a target the market never actually reached is a target that never
        # fills. (Candle prices are real trades and so already tick-aligned;
        # this guards against a provider handing back a rounded float that
        # isn't, which the exchange would reject.)
        if direction == 'short':
            target = _round_to_tick(min(float(df['low'].min()), entry_price), tick, 'up')
            return target if target < entry_price else None
        target = _round_to_tick(max(float(df['high'].max()), entry_price), tick, 'down')
        return target if target > entry_price else None

    # ── Order placement ──────────────────────────────────────────────────

    def _fire_entry(self, symbol: str, s: Dict[str, Any], capital_per_trade: float, algo_active: bool) -> None:
        from trading_app.Backtest.tmf_symbol_universe import TMF_EQUITY_LEVERAGE
        direction = s['direction']
        trigger   = s['trigger']
        qty = max(1, int((capital_per_trade * TMF_EQUITY_LEVERAGE) // trigger)) if trigger > 0 else 1
        s['qty'] = qty
        s['entry_time'] = datetime.now().isoformat()
        # Start of the entry order's life — see _ENTRY_ORDER_TTL_SECS.
        s['entry_placed_ts'] = time.time()

        if not algo_active or not self._broker_list:
            s['phase'] = 'done'
            s['exit_reason'] = 'Signal only (TMF_ALGO_ACTIVE off or no broker configured)'
            logger.info(f"[TMF] {symbol}: {direction.upper()} trigger hit @ {trigger} — no order placed ({s['exit_reason']})")
            return

        transaction = 'SELL' if direction == 'short' else 'BUY'
        # Priced through the last traded price so the LIMIT is marketable —
        # see _ENTRY_MARKETABLE_PAD. `ltp` is the price this same tick just
        # marked (the crossing price); the trigger is only the fallback for
        # the rare tick where the batch LTP call came back empty.
        ref  = float(s.get('ltp') or trigger)
        tick = self._tick_size_for(symbol)
        if direction == 'short':
            limit_price = _round_to_tick(min(ref, trigger) * (1 - _ENTRY_MARKETABLE_PAD), tick, 'down')
        else:
            limit_price = _round_to_tick(max(ref, trigger) * (1 + _ENTRY_MARKETABLE_PAD), tick, 'up')
        s['entry_limit_price'] = limit_price

        broker_positions = []
        for idx, svc in self._broker_list:
            result = svc.place_equity_order(symbol, transaction, qty, price=limit_price, product='MIS')
            if result.get('success'):
                broker_positions.append({'broker_idx': idx, 'entry_order_id': str(result['order_id']), 'filled': False})
                logger.info(f"[TMF] {symbol}: entry {transaction} LIMIT x{qty} @ {limit_price} "
                            f"(marketable, trigger {trigger}, ltp {ref}) placed via broker {idx} — order_id={result['order_id']}")
            else:
                logger.error(f"[TMF] {symbol}: entry order FAILED via broker {idx}: {result.get('error')}")

        if not broker_positions:
            s['phase'] = 'done'
            s['exit_reason'] = 'Entry order placement failed'
            return
        s['broker_positions'] = broker_positions
        s['phase'] = 'pending_entry'

    def _entry_order_expired(self, s: Dict[str, Any], now_mins: int, cutoff_mins: int) -> Optional[str]:
        """Why the still-open entry LIMIT order should be given up on, or
        None to keep waiting. Three reasons, all of them cases where the
        backtest would no longer be in this trade:
          - price has reached the SL level while the entry is still unfilled
            (the backtest calls that setup invalidated — entering now means
            entering with the stop already hit),
          - the order has rested longer than _ENTRY_ORDER_TTL_SECS (the
            backtest fills at the trigger the moment it's crossed; a much
            later fill is a different trade),
          - the day's cutoff has arrived."""
        if now_mins >= cutoff_mins:
            return 'cutoff reached'
        ltp = s.get('ltp')
        if ltp is not None and s.get('sl_level') is not None:
            if (s['direction'] == 'short' and ltp >= s['sl_level']) or \
               (s['direction'] == 'long'  and ltp <= s['sl_level']):
                return 'SL level reached while unfilled'
        if time.time() - float(s.get('entry_placed_ts') or 0) >= _ENTRY_ORDER_TTL_SECS:
            return f'unfilled after {_ENTRY_ORDER_TTL_SECS}s'
        return None

    def _final_fill(self, svc: Any, order_id: str) -> Tuple[int, Optional[float]]:
        """(filled_quantity, average_price) for an order that has just been
        cancelled — a cancelled LIMIT can still have filled part of the way,
        and that part is a real position that must be managed, not written
        off as 'never filled'."""
        try:
            st = svc.get_order_status(order_id)
            if st.get('success'):
                return int(st.get('filled_quantity') or 0), (float(st['average_price']) if st.get('average_price') else None)
        except Exception as e:
            logger.warning(f"[TMF] order {order_id}: final fill lookup failed: {e}")
        return 0, None

    def _check_entry_fill(self, provider: Any, is_fyers: bool, symbol: str, s: Dict[str, Any],
                           orderbooks: Dict[int, Dict[str, Any]], now_mins: int, cutoff_mins: int) -> None:
        give_up = self._entry_order_expired(s, now_mins, cutoff_mins)

        for bp in s.get('broker_positions', []):
            if bp.get('filled') or bp.get('dead'):
                continue
            ob = orderbooks.get(bp['broker_idx'], {})
            order = ob.get(bp['entry_order_id'])
            if not order and not give_up:
                continue
            status = (order or {}).get('status')
            if status == 'COMPLETE':
                bp['filled']      = True
                bp['entry_price'] = float(order.get('average_price') or s['trigger'])
                bp['filled_qty']  = int(order.get('filled_quantity') or s['qty'])
            elif status in ('CANCELLED', 'REJECTED'):
                # Cancelled/rejected still leaves whatever already traded.
                part_qty = int(order.get('filled_quantity') or 0)
                if part_qty > 0:
                    bp['filled']      = True
                    bp['entry_price'] = float(order.get('average_price') or s['trigger'])
                    bp['filled_qty']  = part_qty
                else:
                    bp['dead'] = True
            elif give_up:
                # Still open past its life: cancel the remainder and keep
                # (and protect) whatever quantity actually filled. Leaving
                # it open is what left ICICIGI (2026-07-30) holding 44 of
                # the 307 shares it was sized for, with the other 263 still
                # resting at the broker unmanaged.
                svc = self._svc_for(bp['broker_idx'])
                self._cancel_broker_order(bp['broker_idx'], bp['entry_order_id'])
                part_qty, avg = (self._final_fill(svc, bp['entry_order_id']) if svc else (0, None))
                if part_qty > 0:
                    bp['filled']      = True
                    bp['entry_price'] = float(avg or s['trigger'])
                    bp['filled_qty']  = part_qty
                    logger.warning(f"[TMF] {symbol}: entry cancelled ({give_up}) after a PARTIAL fill — "
                                    f"managing {part_qty} of {s['qty']} shares @ {bp['entry_price']} "
                                    f"(broker {bp['broker_idx']})")
                else:
                    bp['dead'] = True
                    logger.info(f"[TMF] {symbol}: entry order cancelled unfilled ({give_up}), broker {bp['broker_idx']}")

        live_positions    = [bp for bp in s.get('broker_positions', []) if bp.get('filled')]
        pending_positions = [bp for bp in s.get('broker_positions', []) if not bp.get('filled') and not bp.get('dead')]

        if pending_positions:
            return  # still inside the entry order's life — keep waiting

        if not live_positions:
            s['phase'] = 'done'
            s['exit_reason'] = (f'Entry never filled ({give_up})' if give_up else 'Entry never filled')
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

    def _record_exit(self, symbol: str, s: Dict[str, Any], bp: Dict[str, Any], exit_price: float,
                      reason: str, qty: Optional[int] = None, pnl: Optional[float] = None) -> None:
        """Book one exit against a leg. `qty` defaults to the leg's whole
        filled quantity (the normal, all-at-once exit); pass it explicitly to
        book the portion of a leg that went out on its own — a partially
        filled SL/Target order — so that quantity's P&L reaches the history
        instead of being dropped when the leg is later resized.

        `pnl` overrides the derived entry/exit arithmetic, for the one case
        where the broker states the realised figure itself: its own average
        prices are rounded to 2 decimals, so recomputing from them lands a
        few rupees off what the account actually settled."""
        direction   = s['direction']
        entry_price = bp['entry_price']
        qty         = int(bp['filled_qty'] if qty is None else qty)
        if pnl is None:
            pnl = (entry_price - exit_price) * qty if direction == 'short' else (exit_price - entry_price) * qty
        record = {
            'symbol': symbol, 'direction': 'SELL' if direction == 'short' else 'BUY',
            'qty': qty, 'entry_price': entry_price, 'exit_price': exit_price,
            'sl_price': s.get('sl_level'), 'target_price': s.get('target_level'),
            'pnl': round(pnl, 2), 'reason': reason,
            'entry_time': s.get('entry_time', ''), 'exit_time': datetime.now().isoformat(),
            'broker_idx': bp['broker_idx'], 'entry_order_id': bp.get('entry_order_id'),
            'sl_order_id': bp.get('sl_order_id'), 'target_order_id': bp.get('target_order_id'),
        }
        self._append_history(record)
        # Mirror the closed leg onto the stock's own state too (the history file
        # is a separate grid) so Stock Status can show the exit time, the price
        # it went out at, and the realised P&L — summed, because a stock can be
        # traded across several broker slots and each leg closes on its own.
        s['exit_time']    = record['exit_time']
        s['exit_price']   = record['exit_price']
        s['exit_reason']  = reason
        s['realized_pnl'] = round(float(s.get('realized_pnl') or 0) + record['pnl'], 2)
        if not any(bp.get('filled') and not bp.get('closed')
                   for bp in s.get('broker_positions', [])):
            s.pop('unrealized_pnl', None)   # nothing left open to mark
        logger.info(f"[TMF] {symbol}: closed ({reason}) @ {exit_price}, P&L ₹{record['pnl']}")

    def _mark_to_market(self, s: Dict[str, Any], ltp: float) -> None:
        """Stamp the stock's current traded price on its state — and, while a
        position is actually open, the running P&L across every live broker leg
        — so the Stock Status grid can show an open trade the way the broker's
        own position book would. Display only: exits stay driven by the real
        SL/Target orders sitting at the broker, never by this number."""
        s['ltp']      = round(float(ltp), 2)
        s['ltp_time'] = datetime.now().isoformat()
        legs = [bp for bp in s.get('broker_positions', [])
                if bp.get('filled') and not bp.get('closed')]
        if s.get('phase') == 'in_position' and legs:
            sign = -1 if s.get('direction') == 'short' else 1
            s['unrealized_pnl'] = round(sum(
                sign * (ltp - bp['entry_price']) * bp['filled_qty'] for bp in legs
            ), 2)

    def _get_ltp_one(self, svc: Any, symbol: str) -> Optional[float]:
        try:
            ltp = svc.kite.ltp(f'NSE:{symbol}')
            return (ltp.get(f'NSE:{symbol}') or {}).get('last_price')
        except Exception as e:
            # Was silently swallowed, which is why every observed square-off
            # quietly priced itself off the SL level instead — log it so the
            # real cause (rate limit, token, proxy) is visible next time.
            logger.warning(f"[TMF] {symbol}: broker LTP fetch failed: {e}")
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
        being one, matching the user's 'LIMIT order only' requirement).

        A leg is only booked as exited if the broker ACCEPTED the closing
        order. If it was rejected the position is still open, and saying
        otherwise invents a trade: on 2026-08-03 the 15:18 cutoff fell past
        Zerodha's 15:10 deadline for fresh MIS orders, every square-off was
        rejected, and the requested (never-traded) padded price was booked
        anyway — DALBHARAT went into the history at -₹4,504 on a fill that
        never happened. Keep the cutoff before 15:10 (TMF_EXIT_MINUTE) so
        this stays theoretical."""
        direction = s['direction']
        exit_txn  = 'BUY' if direction == 'short' else 'SELL'
        all_closed = True
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
            # The padded limit has to be padded off the MARKET. Falling
            # straight back to the SL level when the broker LTP call fails
            # priced every observed square-off off the stop instead of the
            # live price (PERSISTENT/MAZDOCK, 2026-07-30 and -31, both
            # exactly sl x 1.01) — the tick loop already stamps a fresh
            # traded price on the stock, so use that before the stop.
            ltp = self._get_ltp_one(svc, symbol)
            if ltp is None:
                ltp = s.get('ltp') or s.get('sl_level')
                logger.warning(f"[TMF] {symbol}: broker LTP unavailable at square-off — "
                                f"pricing off last marked {ltp}")
            if not ltp:
                # No live price, no marked price and no SL to fall back on
                # (an orphan recovered with no setup behind it) — there is no
                # honest price to send a LIMIT at. Crashing here on a missing
                # 'sl_level' key is what aborted the whole 2026-08-03 EOD
                # sweep, leaving the rest of the day's positions unhandled.
                all_closed = False
                s['exit_reason'] = 'Square-off skipped — no price reference available'
                logger.error(f"[TMF] {symbol}: no usable price for the square-off LIMIT — "
                              f"position left OPEN at broker {bp['broker_idx']}")
                continue
            pad = 1.01 if exit_txn == 'BUY' else 0.99
            price = round(ltp * pad, 1)
            res = svc.place_equity_order(symbol, exit_txn, qty, price=price, product='MIS')
            if not res.get('success'):
                # The position is still open at the broker. Booking the
                # requested price as an exit would put a trade that never
                # happened into the P&L history.
                all_closed = False
                s['exit_reason'] = f"Square-off REJECTED — still open at broker ({res.get('error')})"
                logger.error(f"[TMF] {symbol}: EOD square-off FAILED (broker {bp['broker_idx']}): {res.get('error')} "
                              f"— leg left OPEN and NOT booked as exited")
                continue

            # This is a padded LIMIT order (fills at price-or-better, often
            # noticeably better) — recording the *requested* price instead
            # of the real fill silently corrupts the P&L history, so poll
            # for the actual average_price rather than assuming they match.
            exit_price = self._poll_fill_price(svc, res['order_id'], fallback=price)
            logger.info(f"[TMF] {symbol}: EOD square-off {exit_txn} x{qty} requested @ {price}, "
                        f"filled @ {exit_price} (broker {bp['broker_idx']}) order={res.get('order_id')}")
            bp['closed'] = True
            self._record_exit(symbol, s, bp, exit_price, 'Time Exit')
        if all_closed:
            s['phase'] = 'done'

    def _handle_eod(self, state: Dict[str, Any]) -> None:
        # Per-symbol isolation: one stock failing to square off must not
        # abandon every stock after it in the dict (2026-08-03, where a
        # KeyError on the first one skipped the rest of the sweep entirely).
        for symbol, s in state['stocks'].items():
            try:
                if s['phase'] == 'in_position':
                    self._square_off(symbol, s)
                elif s['phase'] == 'pending_entry':
                    for bp in s.get('broker_positions', []):
                        if not bp.get('filled') and not bp.get('dead'):
                            self._cancel_broker_order(bp['broker_idx'], bp['entry_order_id'])
                    s['phase'] = 'done'
                    s['exit_reason'] = 'Entry never filled (EOD)'
            except Exception as e:
                logger.error(f"[TMF] {symbol}: EOD handling failed: {e}", exc_info=True)
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
        tracking believes.

        Scoped to THIS strategy's own instruments: NSE cash-market scrips
        that are in TMF_STOCK_UNIVERSE. The broker's position book is
        account-wide, so without that scope the reconciliation sweep adopts
        every other strategy's position as a TMF "orphan" — on 2026-08-03 it
        took over the OI-profile algo's NIFTY2680424750PE option leg, showed
        it as a TMF position, re-armed it on every 30s tick as that strategy
        traded in and out, and (at EOD) tried to square off a position it
        does not own. Index options/futures are NFO/BFO, so the exchange
        check alone drops them; the universe check additionally leaves any
        manually-traded NSE stock alone."""
        from trading_app.Backtest.tmf_symbol_universe import TMF_STOCK_UNIVERSE
        try:
            positions = svc.kite.positions() or {}
            net = positions.get('net', []) or []
            result: Dict[str, Dict[str, Any]] = {}
            for p in net:
                if p.get('product') != 'MIS':
                    continue
                if (p.get('exchange') or '').upper() != 'NSE':
                    continue
                if p.get('tradingsymbol') not in TMF_STOCK_UNIVERSE:
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

    def _exit_leg_fills(self, svc: Any, bp: Dict[str, Any]) -> List[Tuple[str, int, Optional[float]]]:
        """(reason, filled_qty, average_price) for each of the leg's exit
        orders that has traded any quantity — including one that is still
        only PARTIALLY filled, which the orderbook status check
        (_check_position_exit, which waits for 'COMPLETE') never sees."""
        out: List[Tuple[str, int, Optional[float]]] = []
        for key, reason in (('sl_order_id', 'SL Hit'), ('target_order_id', 'Target Hit')):
            order_id = bp.get(key)
            if not order_id:
                continue
            try:
                st = svc.get_order_status(order_id)
            except Exception as e:
                logger.warning(f"[TMF] exit order {order_id}: status lookup failed: {e}")
                continue
            if not st.get('success'):
                continue
            filled = int(st.get('filled_quantity') or 0)
            if filled > 0:
                out.append((reason, filled, float(st['average_price']) if st.get('average_price') else None))
        return out

    def _record_partial_exit(self, symbol: str, s: Dict[str, Any], bp: Dict[str, Any], svc: Any,
                              idx: int, real_qty: int, tracked_qty: int) -> None:
        """Book the quantity that has left the position but was never
        recorded — the broker shows fewer shares open than this app has
        tracked as filled. The broker's own number is the ground truth for
        HOW MUCH went out; the exit orders' fills say at what price and
        why. Call this with the exit legs already cancelled, so the fills it
        reads are final."""
        exited = tracked_qty - real_qty
        fills  = self._exit_leg_fills(svc, bp)
        logger.error(
            f"[TMF] RECONCILE: {symbol} broker {idx} real qty ({real_qty}) < tracked qty "
            f"({tracked_qty}) — {exited} shares have already exited. Booking them "
            f"(exit-leg fills: {[(r, q, p) for r, q, p in fills]})."
        )
        for reason, filled_qty, avg in fills:
            if exited <= 0:
                break
            part  = min(filled_qty, exited)
            level = s.get('sl_level') if reason == 'SL Hit' else s.get('target_level')
            price = avg or level
            if price is None:
                continue
            self._record_exit(symbol, s, bp, float(price), f'{reason} (partial)', qty=part)
            exited -= part
        if exited > 0:
            # Nothing this app placed accounts for it — the position was
            # reduced outside the algo (closed by hand in Kite, or a broker
            # square-off). Book it at the last marked price rather than
            # letting the quantity vanish from the P&L entirely.
            price = s.get('ltp') or bp.get('entry_price')
            if price is not None:
                self._record_exit(symbol, s, bp, float(price), 'Closed outside the algo', qty=exited)
                logger.warning(f"[TMF] RECONCILE: {symbol} {exited} shares closed outside the algo — "
                                f"booked at the last marked price {price} (estimate, not a real fill)")
        bp['filled_qty'] = real_qty
        s['qty']         = real_qty
        if real_qty <= 0:
            bp['closed'] = True
            for key in ('sl_order_id', 'target_order_id'):
                if bp.get(key):
                    self._cancel_broker_order(idx, bp[key])
            if all(b.get('closed') for b in s.get('broker_positions', []) if b.get('filled')):
                s['phase'] = 'done'

    def _rearm_exit_legs(self, symbol: str, s: Dict[str, Any], bp: Dict[str, Any], svc: Any, idx: int,
                          qty: int, provider: Any = None, is_fyers: bool = False) -> None:
        """Cancel the leg's SL/Target orders and place them again for `qty` —
        used whenever the real open quantity has moved away from what the
        existing orders are sized for (entry still filling, or part of the
        position already exited), since an over-sized SL would flip the
        position the wrong way and an under-sized one leaves shares naked."""
        direction = s['direction']
        exit_txn  = 'BUY' if direction == 'short' else 'SELL'
        for key in ('sl_order_id', 'target_order_id'):
            if bp.get(key):
                self._cancel_broker_order(idx, bp[key])
        bp['filled_qty'] = qty
        s['qty']         = qty
        if s.get('sl_level'):
            sl_res = svc.place_equity_sl_order(symbol, exit_txn, qty, trigger_price=s['sl_level'], product='MIS')
            bp['sl_order_id'] = str(sl_res['order_id']) if sl_res.get('success') else None
        if s.get('target_level') is None and provider is not None:
            s['target_level'] = self._compute_target(provider, is_fyers, symbol, direction, bp['entry_price'])
        if s.get('target_level') is not None:
            tgt_res = svc.place_equity_order(symbol, exit_txn, qty, price=s['target_level'], product='MIS')
            bp['target_order_id'] = str(tgt_res['order_id']) if tgt_res.get('success') else None
        else:
            bp['target_order_id'] = None
        logger.info(f"[TMF] RECONCILE: {symbol} re-armed x{qty} — SL={s.get('sl_level')} order={bp.get('sl_order_id')}, "
                    f"Target={s.get('target_level')} order={bp.get('target_order_id')}")

    def _raw_broker_position(self, svc: Any, symbol: str) -> Optional[Dict[str, Any]]:
        """The broker's raw MIS position row for one scrip, INCLUDING a flat
        one (quantity 0). _get_broker_mis_positions drops those — it looks
        for what is still open — but a flat row is exactly what proves a
        position has been closed, and it carries the buy/sell averages and
        the realised P&L of that round trip."""
        try:
            for p in (svc.kite.positions() or {}).get('net', []) or []:
                if (p.get('tradingsymbol') == symbol and p.get('product') == 'MIS'
                        and (p.get('exchange') or '').upper() == 'NSE'):
                    return p
        except Exception as e:
            logger.warning(f"[TMF] {symbol}: positions() fetch failed: {e}")
        return None

    def _book_closed_at_broker(self, state: Dict[str, Any]) -> None:
        """Book any leg this app still holds open but the broker has already
        flattened — using the broker's own numbers.

        This is the other half of not inventing an exit when a square-off
        order is rejected. On 2026-08-03 the rejected square-offs left
        DALBHARAT and JSWSTEEL to Zerodha's own 15:20 MIS auto square-off:
        real closes, at real prices, that this app knew nothing about. The
        thread is still alive until 15:30, so it can watch the position go
        flat and record what actually settled (DALBHARAT: +₹35.90 on a buy
        back at 1,828.87, not the -₹4,504 the invented 1,845.50 exit had
        booked) instead of leaving the trade permanently open in history."""
        for symbol, s in state['stocks'].items():
            open_legs = [bp for bp in s.get('broker_positions', [])
                         if bp.get('filled') and not bp.get('closed')]
            if not open_legs:
                continue
            for bp in open_legs:
                svc = self._svc_for(bp['broker_idx'])
                if svc is None:
                    continue
                pos = self._raw_broker_position(svc, symbol)
                if pos is None or int(pos.get('quantity', 0) or 0) != 0:
                    continue   # still open at the broker — nothing to book yet
                buy_qty  = int(pos.get('buy_quantity', 0) or 0)
                sell_qty = int(pos.get('sell_quantity', 0) or 0)
                if buy_qty <= 0 or buy_qty != sell_qty:
                    continue   # flat but not a completed round trip
                # The closing side is the opposite of the entry.
                exit_price = float((pos.get('buy_price') if s['direction'] == 'short'
                                    else pos.get('sell_price')) or 0)
                if not exit_price:
                    continue
                bp['closed'] = True
                self._record_exit(symbol, s, bp, round(exit_price, 2),
                                   'Time Exit (broker auto square-off)',
                                   qty=bp['filled_qty'],
                                   pnl=float(pos.get('pnl')) if pos.get('pnl') is not None else None)
                logger.warning(f"[TMF] {symbol}: this app's square-off never went through — the broker "
                                f"closed the position itself. Booked from its position book: "
                                f"exit {exit_price}, realised ₹{pos.get('pnl')}")
            if all(bp.get('closed') for bp in s.get('broker_positions', []) if bp.get('filled')):
                s['phase'] = 'done'

    def _reconcile_orphaned_positions(self, state: Dict[str, Any], force_square_off: bool,
                                       provider: Any = None, is_fyers: bool = False,
                                       now_mins: int = 0, cutoff_mins: int = 0) -> None:
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

                # A stock whose entry LIMIT is still inside its life is not an
                # orphan — it's mid-fill, and _check_entry_fill owns it. Left
                # unguarded, this sweep grabbed every partially-filled entry a
                # few seconds after placement and armed SL/Target against a
                # stale partial quantity, then re-armed on each further fill
                # (MAZDOCK, 2026-07-31: three SL and three Target orders for
                # one position). If the entry order outlives its TTL without
                # _check_entry_fill resolving it, this sweep still takes over.
                if (not force_square_off and s is not None and s.get('phase') == 'pending_entry'
                        and self._entry_order_expired(s, now_mins, cutoff_mins) is None):
                    continue

                if tracked_bp is not None:
                    real_qty    = abs(pos['quantity'])
                    tracked_qty = int(tracked_bp.get('filled_qty') or 0)
                    if not force_square_off and real_qty > tracked_qty:
                        # The entry order was still filling incrementally when
                        # this sweep first caught it (a LIMIT order doesn't fill
                        # atomically), so the SL/Target armed back then are sized
                        # for a stale partial quantity while more of the position
                        # keeps filling. Re-sync rather than silently leaving the
                        # difference unprotected.
                        logger.error(
                            f"[TMF] RECONCILE: {symbol} broker {idx} real qty ({real_qty}) > "
                            f"tracked qty ({tracked_qty}) — entry order filled further after this app "
                            f"first caught it. Re-arming SL/Target for the corrected quantity."
                        )
                        tracked_bp['entry_price'] = pos['average_price']
                        s['entry_price']          = pos['average_price']
                        self._rearm_exit_legs(symbol, s, tracked_bp, svc, idx, real_qty,
                                               provider=provider, is_fyers=is_fyers)
                    elif not force_square_off and real_qty < tracked_qty:
                        # The opposite case, and it is NOT an entry problem: part
                        # of the position has already GONE OUT — an exit leg
                        # filled only partially (a LIMIT Target doesn't fill
                        # atomically either), or it was closed by hand in Kite.
                        # Reading that as "the entry filled further" is what cost
                        # COLPAL (2026-08-03) most of its trade: 164 of 237 shares
                        # had already exited on the Target leg, this sweep
                        # cancelled that partially-filled order, re-armed for the
                        # remaining 73 and never recorded the 164 — so the app
                        # booked ₹2,511 on a trade Kite shows as ₹8,152.80.
                        # Account for the portion that exited, then re-arm the
                        # legs (which are now over-sized) for what is still open.
                        # Cancel FIRST and only then re-read the position: a leg
                        # left live could fill further between the snapshot above
                        # and the re-arm, and re-arming against a stale quantity
                        # would leave orders standing for shares no longer held.
                        for key in ('sl_order_id', 'target_order_id'):
                            if tracked_bp.get(key):
                                self._cancel_broker_order(idx, tracked_bp[key])
                        fresh = self._get_broker_mis_positions(svc).get(symbol)
                        open_qty = abs(fresh['quantity']) if fresh else 0
                        self._record_partial_exit(symbol, s, tracked_bp, svc, idx,
                                                   open_qty, tracked_qty)
                        if open_qty > 0:
                            self._rearm_exit_legs(symbol, s, tracked_bp, svc, idx, open_qty,
                                                   provider=provider, is_fyers=is_fyers)
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

                # Keep the real entry order id when this app already had one
                # for that broker — overwriting it with 'RECONCILED' left the
                # original (possibly still-open) entry LIMIT untrackable and
                # so never cancelled.
                prior_order_id = next(
                    (b.get('entry_order_id') for b in (s.get('broker_positions') or [])
                     if b.get('broker_idx') == idx and b.get('entry_order_id')),
                    'RECONCILED',
                )
                bp = {
                    'broker_idx': idx, 'entry_order_id': prior_order_id, 'filled': True,
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
               cutoff_mins: int, algo_active: bool, now_mins: int,
               max_sl_risk: Optional[float] = None) -> None:
        stocks = state['stocks']
        _, _, _, _, third_candle_end_min = _thirty_min_engine()

        if now_mins >= third_candle_end_min:
            for symbol, s in stocks.items():
                if s['phase'] != 'pending_scan':
                    continue
                try:
                    self._scan_one(provider, is_fyers, symbol, s, capital_per_trade, max_sl_risk)
                except Exception as e:
                    logger.warning(f"[TMF] {symbol}: scan failed: {e}")

        watching_syms = [sym for sym, s in stocks.items() if s['phase'] == 'watching']
        pending_syms  = [sym for sym, s in stocks.items() if s['phase'] == 'pending_entry']
        inpos_syms    = [sym for sym, s in stocks.items() if s['phase'] == 'in_position']

        # One batched LTP call covering every stock with something live on it.
        # `watching` needs it for the trigger/SL checks below; pending_entry and
        # in_position need it purely so Stock Status can show the stock's current
        # value and the running P&L on an open position.
        mark_syms = watching_syms + pending_syms + inpos_syms
        ltps = self._get_ltp_batch(provider, mark_syms, is_fyers) if mark_syms else {}
        for symbol in mark_syms:
            ltp = ltps.get(symbol)
            if ltp is not None:
                try:
                    self._mark_to_market(stocks[symbol], ltp)
                except Exception as e:
                    logger.warning(f"[TMF] {symbol}: mark-to-market failed: {e}")

        if watching_syms and now_mins < cutoff_mins:
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

        # Recomputed, not reused from above: the trigger loop just moved any
        # stock that fired into pending_entry, and its fill has to be checked
        # in this same tick rather than only on the next one.
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
        # Not once the EOD sweep has run: past the cutoff there is nothing
        # left to protect, and a position the broker rejected the square-off
        # for would otherwise be re-armed with fresh (equally rejected)
        # MIS orders every 30 seconds until the thread stops.
        if self._broker_list and not state.get('eod_handled'):
            now_ts = time.time()
            if now_ts - self._last_reconcile_ts >= _RECONCILE_SECS:
                self._last_reconcile_ts = now_ts
                try:
                    self._reconcile_orphaned_positions(state, force_square_off=False,
                                                         provider=provider, is_fyers=is_fyers,
                                                         now_mins=now_mins, cutoff_mins=cutoff_mins)
                except Exception as e:
                    logger.error(f"[TMF] reconciliation failed: {e}", exc_info=True)

        if now_mins >= cutoff_mins and not state.get('eod_handled'):
            try:
                self._handle_eod(state)
            except Exception as e:
                logger.error(f"[TMF] EOD handling failed: {e}", exc_info=True)
            state['eod_handled'] = True
        elif state.get('eod_handled') and self._broker_list:
            # Anything the EOD sweep could not close (a rejected square-off
            # order) is still open at the broker, which will square it off
            # itself at 15:20. The thread lives until 15:30 precisely so it
            # can see that happen and book the real fill.
            try:
                self._book_closed_at_broker(state)
            except Exception as e:
                logger.error(f"[TMF] post-EOD booking failed: {e}", exc_info=True)

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
            # 15:05, not the backtest's 15:18 — Zerodha blocks fresh MIS
            # orders after 15:10, so a later cutoff cannot square off.
            exit_minute = int(self._uvar('TMF_EXIT_MINUTE', '5') or 5)
            cutoff_mins = exit_hour * 60 + exit_minute
            algo_active = self._uvar('TMF_ALGO_ACTIVE', 'false').lower() == 'true'
            # The backtest's use_sl_risk_filter / sl_risk_max. Default OFF to
            # match the Backtest page's unchecked "SL Risk Filter" checkbox —
            # set TMF_USE_SL_RISK_FILTER=true to cap the sized rupee stop at
            # TMF_MAX_SL_RISK, which then skips setups the backtest scored.
            max_sl_risk: Optional[float] = None
            if self._uvar('TMF_USE_SL_RISK_FILTER', 'false').lower() == 'true':
                max_sl_risk = float(self._uvar('TMF_MAX_SL_RISK', '5000') or 5000)
            logger.info(f"[TMF] Config — capital/trade ₹{capital_per_trade:,.0f}, cutoff {exit_hour:02d}:{exit_minute:02d}, "
                        f"orders {'ON' if algo_active else 'OFF'}, max SL risk "
                        f"{('₹%s' % format(max_sl_risk, ',.0f')) if max_sl_risk is not None else 'unlimited'}")

            while not self._stop_event.is_set():
                now = datetime.now()
                now_mins = now.hour * 60 + now.minute
                if now_mins >= _HARD_STOP_MIN:
                    break
                try:
                    self._tick(provider, is_fyers, state, capital_per_trade, cutoff_mins,
                                algo_active, now_mins, max_sl_risk)
                except Exception as e:
                    logger.error(f"[TMF] tick error: {e}", exc_info=True)
                self._save_state(state)
                self._stop_event.wait(_POLL_SECS)

            logger.info("[TMF] Monitor thread exiting for the day")
        except Exception as e:
            logger.error(f"[TMF] monitor loop crashed: {e}", exc_info=True)
