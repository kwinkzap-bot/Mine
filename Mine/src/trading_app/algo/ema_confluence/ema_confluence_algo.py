"""
EMA Confluence Breakout — Live Algo (Futures; paper by default, LIVE by flag)

Live counterpart of the backtest in Backtest/ema_pullback_engine.py, scanning
every symbol in EMA_SYMBOL_DEFAULTS (Backtest/ema_symbol_universe.py) — each
using its OWN default Direction/Target% combo, same as the backtest form's
per-symbol prefill. Signal detection reuses EmaPullbackEngine directly (the
exact same EMA-touch/breakout/SL logic as the backtest) against the
underlying's own continuous daily history (index/equity — futures contracts
roll monthly and don't carry enough history for a 200-day EMA); the paper
ORDER itself is filled and marked-to-market on that symbol's own FUTURES
contract, per this task's requirement to trade the future, not the equity.

The two price scales are kept strictly apart, and this is the single most
important thing about this module. EVERY strategy decision — the trigger cross
that opens a trade, the SL and the Target that close it — is judged on the
UNDERLYING's live price, the same scale the signal candle's High/Low came from,
so the live algo reads the market exactly as the backtest that validated it
does. The FUTURE supplies only the money: which contract, the lot size, the
fill price the P&L is computed from, and the mark-to-market. Each tick
therefore carries TWO prices per symbol — `spot_ltp` for decisions, `ltp` for
money — and one is never substituted for the other (see _decision_price).

This replaces the earlier behaviour, where equity-scale levels were compared
directly against the future's LTP with no basis adjustment. The basis runs
~0.5% on a typical name in this universe, which on a 3% target is a fifth of
the whole trade; it filled longs late and stopped shorts out early, and every
monthly roll re-derived the Target off the new contract's price, compounding
the drift (OIL was left holding a 4.65% target configured as 8%).

A monthly future dies but a multi-day swing doesn't, so the position is CARRIED
FORWARD: three trading days before the held contract's expiry, at 12:00, the
open leg is booked out on that contract (history reason 'ROLL') and the same
side is immediately re-entered on the next month's — keeping the SL/Target it
was armed with, since both come from the signal candle on the underlying and so
are contract-independent. Anything merely armed just switches which contract it
quotes against, and a new entry inside that window opens on the far month to
begin with. Expiry dates are the broker's real ones off the instrument master
(list_future_contracts), not a calendar guess; the roll moment is counted in
TRADING days via app/utils/trading_calendar, and because a passed roll moment
stays due until the algo next ticks in a real session, a holiday simply defers
the roll to the following session.

The universe itself is EMA_SYMBOL_DEFAULTS and nothing else: a symbol removed
from that table is retired at the next start-up rather than left lingering on
the status grid with no Direction/Target to show — and an armed one really has
to go, because the tick loop drives `watching` symbols off the STATE file, so a
removed stock would otherwise still be able to open a position. An open
position is booked out at its last mark first, so it lands in the trade history
with its P&L instead of vanishing. See _retire_dropped_symbols.

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
  MARKET HOURS                        — every price-driven action (trigger
                                          fills, SL/Target exits, mark-to-
                                          market) happens only between 09:15
                                          and 15:30 IST on a weekday, see
                                          _in_session. The thread still starts
                                          at 08:30 and the daily scan still
                                          runs pre-open — it reads only closed
                                          daily candles — so setups are armed
                                          and logged before the bell, they just
                                          cannot fill until the market opens.
  EMA_CONFLUENCE_ACTIVE = true/false   — gates entries (paper fills); the
                                          thread always scans/logs regardless
                                          (same convention as TMF/RTP).
  EMA_CONFLUENCE_MODE   = paper (default) | live
                                        paper: fills are simulated at the
                                          future's LTP, nothing reaches a
                                          broker.
                                        live:  every entry, exit and roll is
                                          a real MARKET order, product NRML
                                          (carried overnight), at every
                                          broker flagged below. See "Live
                                          execution".
  BROKER_<N>_EMA_ACTIVE = true          — per broker slot, alongside the
                                          existing BROKER_<N>_ACTIVE and
                                          BROKER_<N>_TYPE. Opts THAT account
                                          in to this algo's live orders and
                                          nothing else — it does not follow
                                          from TMF_ACTIVE / OP_ACTIVE. Only
                                          zerodha and fyers can carry an
                                          NRML futures position here; a
                                          flagged dhan/kotak/icici slot is
                                          refused with an error at start-up.
  BROKER_<N>_EMA_LOTS   = 1             — lots per entry for THAT broker;
                                          falls back to EMA_CONFLUENCE_LOTS.
  EMA_CONFLUENCE_LOTS   = 1             — lot count per entry (paper, and
                                          the live default per broker).
  EMA_CONFLUENCE_ROLL_DAYS = 3          — trading days before expiry at which
                                          the contract roll fires (12:00 that
                                          day). Raising it far above 3 opens
                                          the window immediately, which is how
                                          a roll is exercised off-hours.

Live execution (EMA_CONFLUENCE_MODE=live):
  The strategy is unchanged — the same spot-scale trigger/SL/Target decide
  when to act; only what "act" means differs. On a trigger cross the algo
  places a MARKET NRML order for the resolved futures contract at every
  BROKER_<N>_EMA_ACTIVE broker, polls each order to its fill, and opens the
  position on the qty-weighted average of the fills that came back (a broker
  that rejects simply takes no part; if NONE holds anything the setup is
  dropped to the next scan, exactly as a trigger that crossed with
  EMA_CONFLUENCE_ACTIVE off). Every broker's holding is a `broker_legs` entry
  on the symbol's state — broker slot, tradingsymbol, order ids, filled qty
  and price — and it is THE LEG, not the current MODE flag, that decides how
  a position is closed: a live position is flattened at the broker even if
  the flag has since been turned back to paper, and a paper position is
  never "sold" at a broker it was never bought at.

  An SL/Target exit places the opposite MARKET order per leg. A leg whose
  exit order is rejected stays open and is retried every tick under
  `exit_pending` — the strategy has already spoken, so the spot price no
  longer has a say — and the trade is booked to history only once every leg
  is flat. A roll does the same on the near contract and then re-enters the
  far one at the same brokers; a broker that cannot re-enter is flat and is
  said so, loudly.

  Order failures raise an in-app notification (ema_confluence_order_failed)
  once per symbol per day, and an auth-shaped failure rebuilds the broker
  sessions from env/Mine.env before retrying — the daily Kite login usually
  lands after the 08:30 thread start, so the token cached at start is often
  already dead (same lesson as TMF).
"""
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, time as dt_time, timedelta
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
# ...and for the same reason the marks it polls need not be a second old. On
# ICICI every non-option quote is one Breeze request, so sweeping ~18 armed
# futures AND their underlyings every _POLL_SECS demanded ~144 req/min on its
# own — more than Breeze's whole published 100/min budget, which starved RTP
# and Second Candle of the NIFTY spot and burned the daily quota before noon.
# A minute-old mark is far inside the tolerance of a strategy whose levels come
# off DAILY candles. Providers that don't accept the argument are unaffected.
_QUOTE_MAX_AGE_SECS = 60
_MARKET_OPEN_MIN = 9 * 60 + 15  # 09:15 IST — first tick that can be a real trade
_HARD_STOP_MIN = 15 * 60 + 30   # thread exits for the day at/after 15:30 IST

# ── Contract roll ────────────────────────────────────────────────────────
# A monthly future dies; a multi-day swing doesn't. Three trading days before
# expiry, at noon, this algo stops trading a contract and moves to the next
# month — an open position is exited on the near month and immediately
# re-entered on the same side in the far month, and anything merely armed just
# switches which contract it quotes against.
_ROLL_SESSIONS_BEFORE_EXPIRY = 3     # trading days; EMA_CONFLUENCE_ROLL_DAYS overrides
_ROLL_HOUR, _ROLL_MINUTE = 12, 0     # 12:00, server-local like every other clock here
# The rolled leg keeps the SL/Target it was armed with — not as a choice but by
# construction: both live on the UNDERLYING's price scale, which has no
# contracts and therefore nothing to roll. Only the futures fill price is
# re-stamped. (This used to be a _ROLL_REPRICE_TARGET flag, because the Target
# was derived from the future's entry price and so every roll shifted it by the
# roll spread. With the Target on the spot scale the question disappears.)
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
# route uses); every other symbol is an F&O equity, 'NSE:{symbol}-EQ' on the
# symbol-addressed providers (Fyers, ICICI) or the bare tradingsymbol (Kite).
_FYERS_INDICES = {
    'NIFTY':     'NSE:NIFTY50-INDEX',
    'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
    'SENSEX':    'BSE:SENSEX-INDEX',
}
# Underlyings whose futures are BSE (BFO) contracts, not NFO ones. The Kite
# path here only ever resolves NFO futures (KiteService.get_nfo_instruments),
# so these are symbol-provider-only (Fyers/ICICI) — see _resolve_future.
_BSE_UNDERLYINGS = {'SENSEX'}

# ── Live execution ───────────────────────────────────────────────────────
# Only these broker types can carry an NRML futures position through this
# module: both take a plain tradingsymbol and a carry-forward product, and
# both expose an order book the fill can be read back from. The others are
# refused at start-up rather than silently skipped — a flag that says "trade
# here" and does nothing is how an account ends up unhedged without anyone
# noticing.
_LIVE_BROKER_TYPES = ('zerodha', 'fyers')
# Same hints TMF uses: an error shaped like this means the cached session is
# stale, and the fix is a rebuild from env, not a retry on the dead token.
_AUTH_ERROR_HINTS = ('api_key', 'access_token', 'unauthor', 'authenticat',
                     'invalid session', 'session expired', 'token')
# A MARKET order fills within a second or so; poll a few times before giving
# up on confirming it this tick. Module-level so tests can zero the wait.
_FILL_POLL_ATTEMPTS = 4
_FILL_POLL_WAIT_SECS = 1.0
# How many ticks an exit order may sit unconfirmed (order book unreachable,
# say) before the leg is assumed filled at the mark. ~2 minutes at 15s.
_EXIT_UNCONFIRMED_TICKS = 8
# Passive rebuilds of the broker sessions are rate-limited so a dead login
# cannot turn every tick into an env re-read; the entry path forces one.
_BROKER_REFRESH_MIN_GAP_SECS = 60
# The app-wide convention for the exchange freeze quantity: no single order
# over 27 lots (split_quantity_by_freeze_limit in routes/api.py). Stock
# futures have per-scrip freeze quantities of their own, but nothing in this
# universe is sized anywhere near either cap at the default 1 lot.
_FREEZE_LOTS = 27


def _is_auth_error(err: Any) -> bool:
    msg = str(err or '').lower()
    return any(hint in msg for hint in _AUTH_ERROR_HINTS)


def _bare_tradingsymbol(token: Any) -> str:
    """'NSE:NHPC26SEPFUT' / 'NFO:NHPC26SEPFUT' -> 'NHPC26SEPFUT' — the
    exchange tradingsymbol, which is the same string at every broker."""
    return str(token or '').split(':', 1)[-1]


def _lot_chunks(qty: int, lot_size: int) -> List[int]:
    """Split a quantity (units) into orders of at most _FREEZE_LOTS lots."""
    cap = max(1, _FREEZE_LOTS * max(1, int(lot_size or 1)))
    chunks: List[int] = []
    remaining = int(qty)
    while remaining > 0:
        chunk = min(remaining, cap)
        chunks.append(chunk)
        remaining -= chunk
    return chunks


def _legs_avg(legs: List[Dict[str, Any]], key: str, fallback: Optional[float]) -> Optional[float]:
    """Qty-weighted average of `{key}_price` over legs, using `fallback` for a
    leg whose fill price never came back. None only if nothing is priceable."""
    notional = 0.0
    qty = 0
    for leg in legs:
        q = int(leg.get(f'{key}_qty') or 0)
        p = leg.get(f'{key}_price')
        if p is None:
            p = fallback
        if q <= 0 or p is None:
            continue
        notional += q * float(p)
        qty += q
    return round(notional / qty, 2) if qty else None


_instances: Dict[str, 'EmaConfluenceAlgo'] = {}

# Monthly futures contract tail — 'NSE:NHPC26AUGFUT' / 'NFO:NHPC26AUGFUT'.
# Fyers tokens and Kite tradingsymbols share the {ROOT}{YY}{MMM}FUT shape.
_CONTRACT_RE = re.compile(r'(\d{2})([A-Z]{3})FUT$')


def roll_due_at(expiry: date, sessions: int = _ROLL_SESSIONS_BEFORE_EXPIRY) -> datetime:
    """The moment a contract stops being the one this algo trades: 12:00 on
    the Nth trading day before its expiry.

    Counting in SESSIONS means that day is a trading day by construction, so
    the user's "if that date is a holiday, do it the next trading session"
    is normally satisfied before it can bite. next_trading_day_inclusive is
    the safety net for the case where it can: a year the holiday list doesn't
    cover fails open (see trading_calendar), so a counted day can turn out to
    be a holiday after all. Never later than expiry day itself — a holiday
    cluster must not push the roll past the contract's own death.
    """
    from trading_app.app.utils.trading_calendar import (
        next_trading_day_inclusive, trading_days_before)
    day = min(next_trading_day_inclusive(trading_days_before(expiry, sessions)), expiry)
    return datetime.combine(day, dt_time(_ROLL_HOUR, _ROLL_MINUTE))


def select_contract(now: datetime, contracts: List[Dict[str, Any]],
                    sessions: int = _ROLL_SESSIONS_BEFORE_EXPIRY) -> Optional[Dict[str, Any]]:
    """Which contract should be traded right now — the nearest one that hasn't
    reached its roll moment.

    Deliberately an ABSOLUTE choice, not an offset off whatever is currently
    held, because that one property answers both halves of the roll question
    and dissolves the edge cases: a new entry or an armed setup inside the
    window picks the far month; a position OPENED inside the window gets back
    the very contract it is already on, so it cannot roll twice; an expired
    contract was already dropped from the list by the provider; a single
    listed contract hits the clamp below.
    """
    for c in contracts:
        expiry = c.get('expiry')
        if expiry is None:
            continue       # never roll onto (or off) a contract whose expiry we don't know
        if now < roll_due_at(expiry, sessions):
            return c
    return contracts[-1] if contracts else None


def _is_weekend_signal(signal_date: Any) -> bool:
    """True for a stored signal_date ('YYYY-MM-DD') that lands on a weekend.

    Only state written before the weekend bars were filtered out can hold one;
    it's how an already-armed phantom setup gets recognised and re-scanned.
    """
    try:
        return datetime.strptime(str(signal_date), '%Y-%m-%d').weekday() >= 5
    except (TypeError, ValueError):
        return False


def _parse_iso_date(value: Any) -> Optional[date]:
    """'2026-08-25' -> date(2026, 8, 25); None for anything unparseable, so a
    missing or corrupt state field degrades to "expiry unknown" rather than
    raising inside the tick thread."""
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


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
    """Live EMA Confluence Breakout signal detector + futures executor (paper
    fills by default, real broker orders under EMA_CONFLUENCE_MODE=live)."""

    # Class-level default so an instance that never ran _monitor_loop — a
    # test built with __new__, a REPL poke — is paper, never live.
    _live = False

    def __init__(self, username: str):
        self.username = username
        self.log = _PrefixLogger(logger, {})
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._session_hold_logged = False
        # Re-read from EMA_CONFLUENCE_ROLL_DAYS when the thread starts; the
        # default keeps the pure helpers usable straight off the constructor.
        self._roll_sessions = _ROLL_SESSIONS_BEFORE_EXPIRY
        # Live execution. `_live` is EMA_CONFLUENCE_MODE at thread start;
        # `_broker_list` the (idx, kind, service, lots) sessions built from
        # the BROKER_N_EMA_ACTIVE flags. Both stay empty in paper mode — but
        # see _monitor_loop: a paper-flagged thread still builds sessions when
        # the state file holds live legs, because those must be closed at the
        # broker whatever the flag now says.
        self._live = False
        self._broker_list: List[Dict[str, Any]] = []
        self._last_broker_refresh_ts = 0.0
        self._brokers_refreshed_on: Optional[str] = None
        self._exit_only_sessions: Dict[int, Dict[str, Any]] = {}
        self._order_alerts: Dict[str, str] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name='EmaConfluenceAlgoThread')
        self._thread.start()
        _instances[self.username] = self
        self.log.info(f"Monitoring thread started ({self._mode()} mode)")

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
            # A symbol DROPPED from the universe is retired — see
            # _retire_dropped_symbols. Guarded like the migration below: it
            # books positions out, and a failure in there must degrade to
            # "state loaded unretired", never to the bare `except` at the
            # bottom, which hands back a FRESH state and silently abandons
            # every open position.
            try:
                self._retire_dropped_symbols(state, EMA_SYMBOL_DEFAULTS)
            except Exception as e:
                self.log.error(f"retiring dropped symbols failed, state left as-is: {e}")
            # Guarded separately: a migration failure must degrade to "state
            # loaded unmigrated", never to the bare `except` below, which would
            # hand back a FRESH state and silently abandon every open position.
            try:
                self._migrate_levels_to_spot_scale(state)
            except Exception as e:
                self.log.error(f"spot-scale migration failed, state left as-is: {e}")
            return state
        except Exception:
            return self._fresh_state()

    def _retire_dropped_symbols(self, state: Dict[str, Any], universe: Dict[str, Any]) -> None:
        """Remove every symbol no longer in EMA_SYMBOL_DEFAULTS, whatever phase
        it is in.

        That table is the whole universe, so a symbol removed from it is one
        this strategy no longer trades. Leaving it behind kept it on the Symbol
        Status grid with a blank Allowed/Target — it has no configured
        Direction or Target left to show — and, worse, left an armed trigger
        live: the tick loop drives `watching` symbols off the STATE file, not
        off the table, so a removed stock could still open a position.

        An open position is not simply discarded. It is booked out at its last
        known mark first, so the trade reaches the history file with its P&L
        instead of vanishing unrecorded, and it fires an exit alert precisely
        because no signal asked for it.
        """
        for symbol in [s for s in state['stocks'] if s not in universe]:
            st = state['stocks'][symbol]
            phase = st.get('phase')
            if phase == 'in_position' and st.get('broker_legs'):
                # A LIVE position is a real contract at a broker — booking it
                # on paper here would leave it there, managed by nothing. Keep
                # the symbol, hand it to the tick loop as a decided exit, and
                # let _tick drop it once every leg is flat (this runs at 08:30,
                # when no order can be placed).
                if st.get('exit_pending') != 'UNIVERSE_DROP':
                    self.log.warning(f"{symbol}: dropped from the universe — its LIVE position "
                                     f"will be flattened at the brokers on the first in-session tick")
                st['exit_pending'] = 'UNIVERSE_DROP'
                continue
            if phase == 'in_position':
                mark = st.get('ltp')
                if mark is None:
                    # No price to book at, and inventing one would write a fill
                    # that never traded. Keep it; the next tick marks it to
                    # market and the following start-up retires it.
                    self.log.warning(f"{symbol}: dropped from the universe but its open "
                                     f"position has no last mark to book out at — kept for "
                                     f"now, will retire once it quotes again")
                    continue
                self.log.warning(f"{symbol}: dropped from the universe — closing its open "
                                 f"paper position at the last mark {mark}")
                self._record_exit(symbol, st, float(mark), 'UNIVERSE_DROP')
            elif phase == 'watching':
                self.log.info(f"{symbol}: dropped from the universe — discarding its armed "
                              f"{st.get('direction', '?')} setup "
                              f"(trigger {st.get('trigger_level')}, sl {st.get('sl_level')})")
            state['stocks'].pop(symbol)

    def _migrate_levels_to_spot_scale(self, state: Dict[str, Any]) -> None:
        """One-time repair for positions opened before the Target moved onto
        the underlying's price scale.

        Those were entered at the FUTURE's LTP and had their Target derived from
        it, so the level is off by the basis — and a ROLLED position is off by
        every roll spread since, which is how OIL came to hold a 4.65% target
        that was configured as 8%. Nothing recorded what the underlying was
        quoting at the fill, but `trigger_level` IS that price on the spot
        scale: the backtest fills a breakout at its trigger unless the bar gaps
        through it, so this is the same reconstruction the engine itself makes.

        SL needs no repair — it always came off the signal candle. Neither does
        entry_price: the futures fill is real, and the P&L still runs off it.
        Idempotent — a position that already has spot_entry_price is skipped.
        """
        for symbol, s in state.get('stocks', {}).items():
            if s.get('phase') != 'in_position' or s.get('spot_entry_price') is not None:
                continue
            trigger = s.get('trigger_level')
            if trigger is None:
                self.log.warning(f"{symbol}: open position has no trigger_level to rebuild a spot "
                                 f"entry from — Target left on the futures scale, review it by hand")
                continue
            pct = self._configured_target_pct(symbol, s)
            if pct is None:
                self.log.warning(f"{symbol}: open position has no Target % of its own to "
                                 f"rebuild from (not in EMA_SYMBOL_DEFAULTS) — Target left on "
                                 f"the futures scale, review it by hand")
                continue
            spot_entry = round(float(trigger), 2)
            was = s.get('target_level')
            s['spot_entry_price'] = spot_entry
            s['target_level'] = round(spot_entry * (1 + pct / 100), 2) if s.get('direction') == 'Long' \
                else round(spot_entry * (1 - pct / 100), 2)
            self.log.info(
                f"{symbol}: migrated to spot-scale levels — spot entry {spot_entry} (rebuilt from "
                f"its trigger), target {was} -> {s['target_level']} ({pct}% of the spot entry). "
                f"Futures fill {s.get('entry_price')} untouched; it still carries the P&L."
            )

    def _configured_target_pct(self, symbol: str, s: Dict[str, Any]) -> Optional[float]:
        """This symbol's Target %, from the setup it was armed with or, failing
        that, its own row in EMA_SYMBOL_DEFAULTS.

        None when NEITHER has one — which is the whole point of this method.
        Both callers used to fall back to a bare 5.0, a number belonging to no
        symbol in the table: an entry fired on a state row that had lost its
        target_pct would arm a 5% target and look completely normal doing it.
        EMA_SYMBOL_DEFAULTS is the strategy's whole universe (see that module),
        so a symbol absent from it has no Target this algo is entitled to
        invent, and the caller refuses rather than guesses.
        """
        pct = s.get('target_pct')
        if pct is not None:
            try:
                return float(pct)
            except (TypeError, ValueError):
                pass
        from trading_app.Backtest.ema_symbol_universe import EMA_SYMBOL_DEFAULTS
        cfg = EMA_SYMBOL_DEFAULTS.get(symbol)
        return float(cfg['target_pct']) if cfg else None

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
        return 'live' if self._uvar('EMA_CONFLUENCE_MODE', 'paper').lower() == 'live' else 'paper'

    @staticmethod
    def _mode_of(s: Dict[str, Any]) -> str:
        """The mode a POSITION is in — decided by whether it holds broker legs,
        never by the current flag (see the module docstring)."""
        return 'live' if s.get('broker_legs') else 'paper'

    # ── Live brokers ─────────────────────────────────────────────────────

    def _broker_lots(self, idx: int) -> int:
        for var in (f'BROKER_{idx}_EMA_LOTS', 'EMA_CONFLUENCE_LOTS'):
            raw = self._uvar(var)
            if raw:
                try:
                    return max(1, int(raw))
                except ValueError:
                    self.log.warning(f"{var}={raw!r} is not a lot count — ignored")
        return 1

    def _get_live_brokers(self) -> List[Dict[str, Any]]:
        """One session per BROKER_N_ACTIVE + BROKER_N_EMA_ACTIVE slot:
        {'idx', 'kind', 'name', 'svc', 'lots'}.

        Drops this user's cached env first, for the same reason TMF does: the
        daily Kite login rewrites env/Mine.env while this process runs and
        UserEnvManager caches that file for the life of the process, so a
        rebuild would otherwise hand back the token that just expired.
        """
        from trading_app.app.utils.user_env import UserEnvManager
        UserEnvManager._user_env_cache.pop(self.username, None)

        result: List[Dict[str, Any]] = []
        for i in range(1, 21):
            b_type = self._uvar(f'BROKER_{i}_TYPE', '').lower()
            if not b_type:
                continue
            if self._uvar(f'BROKER_{i}_ACTIVE', 'false').lower() not in ('true', '1', 'yes'):
                continue
            if self._uvar(f'BROKER_{i}_EMA_ACTIVE', 'false').lower() not in ('true', '1', 'yes'):
                continue
            name = self._uvar(f'BROKER_{i}_NAME') or f'broker {i}'
            if b_type not in _LIVE_BROKER_TYPES:
                self.log.error(f"BROKER_{i}_EMA_ACTIVE=true on {name} ({b_type}) but only "
                               f"{'/'.join(_LIVE_BROKER_TYPES)} can carry an NRML futures "
                               f"position here — that account takes no part")
                continue
            b = self._build_broker(i, b_type, name)
            if b:
                result.append(b)
        return result

    def _build_broker(self, i: int, b_type: str, name: str) -> Optional[Dict[str, Any]]:
        """One broker session, or None with the reason logged."""
        try:
            if b_type == 'zerodha':
                from trading_app.service.provider_logic import get_kite
                from trading_app.service.kite_order_services import KiteService, apply_kite_proxy
                kite = get_kite(user=self.username, instance=i)
                if not kite:
                    self.log.warning(f"Broker {i} ({name}): Kite not connected — no session")
                    return None
                if os.getenv('STATIC_IP_KEY', '').strip():
                    apply_kite_proxy(kite)
                svc: Any = KiteService(kite_instance=kite)
            elif b_type == 'fyers':
                from trading_app.service.fyers_order_services import FyersOrderService
                access_token = self._uvar(f'BROKER_{i}_ACCESS_TOKEN')
                if not access_token:
                    self.log.warning(f"Broker {i} ({name}): Fyers not authenticated — no session")
                    return None
                svc = FyersOrderService(app_id=self._uvar(f'BROKER_{i}_APP_ID'),
                                        access_token=access_token)
            else:
                return None
            return {'idx': i, 'kind': b_type, 'name': name, 'svc': svc,
                    'lots': self._broker_lots(i)}
        except Exception as e:
            self.log.error(f"Broker {i} ({b_type}) init failed: {e}")
            return None

    def _refresh_brokers(self, reason: str, force: bool = False) -> bool:
        """Rebuild every broker session from the env as it is right now.
        Returns False when the rate limit swallowed the request."""
        now = time.time()
        if not force and (now - self._last_broker_refresh_ts) < _BROKER_REFRESH_MIN_GAP_SECS:
            return False
        self._last_broker_refresh_ts = now
        self._broker_list = self._get_live_brokers()
        self._exit_only_sessions = {}
        summary = [f"{b['idx']}:{b['kind']}x{b['lots']}" for b in self._broker_list]
        self.log.warning(f"Broker sessions rebuilt from env ({reason}) — live brokers: {summary or 'none'}")
        return True

    def _broker_for(self, idx: int) -> Optional[Dict[str, Any]]:
        """The session for a broker that HOLDS a leg. Normally one of the
        flagged brokers — but a leg outlives its flag: turning
        BROKER_N_EMA_ACTIVE off while that account holds a contract must not
        make the contract unreachable, so the session is built on demand from
        BROKER_N_TYPE alone. It never joins _broker_list, so no NEW entry goes
        to an account that has opted out."""
        for b in self._broker_list:
            if b['idx'] == idx:
                return b
        if idx in self._exit_only_sessions:
            return self._exit_only_sessions[idx]
        b_type = self._uvar(f'BROKER_{idx}_TYPE', '').lower()
        if b_type not in _LIVE_BROKER_TYPES:
            return None
        self.log.warning(f"Broker {idx} holds a leg but is no longer flagged for this algo — "
                         f"building a session for the exit only")
        b = self._build_broker(idx, b_type, self._uvar(f'BROKER_{idx}_NAME') or f'broker {idx}')
        if b:
            self._exit_only_sessions[idx] = b   # until the next _refresh_brokers
        return b

    def _on_broker_error(self, err: Any, where: str) -> None:
        if _is_auth_error(err):
            self._refresh_brokers(f'{where} failed auth')

    def _leg_symbol(self, b: Dict[str, Any], symbol: str, token: Any) -> str:
        """The contract as THIS broker's order API wants it. Kite takes the bare
        tradingsymbol (KiteService picks NFO/BFO off the underlying); Fyers
        wants it exchange-prefixed."""
        bare = _bare_tradingsymbol(token)
        if b['kind'] == 'zerodha':
            return bare
        return f"{'BSE' if symbol in _BSE_UNDERLYINGS else 'NSE'}:{bare}"

    def _place_leg(self, b: Dict[str, Any], symbol: str, tradingsymbol: str,
                   action: str, qty: int) -> Dict[str, Any]:
        """One MARKET carry-forward order at one broker. Never raises."""
        try:
            if b['kind'] == 'zerodha':
                # place_option_order is the NFO/BFO order path with the
                # market-protection fallback; the tradingsymbol override makes
                # it a futures order, and strike/option_type are unused then.
                return b['svc'].place_option_order(
                    symbol=symbol, strike=0, option_type='FUT', transaction_type=action,
                    quantity=int(qty), product='NRML', tradingsymbol=tradingsymbol)
            # Fyers v3: side 1 = BUY, -1 = SELL (the service's SIDE_SELL
            # constant says 2, which is not a Fyers value — the two other
            # sell paths in the app hard-code -1 as well); type 2 = MARKET;
            # MARGIN is the carry-forward product for F&O.
            return b['svc'].place_order(
                symbol=tradingsymbol, side=1 if action == 'BUY' else -1, quantity=int(qty),
                order_type=2, product_type='MARGIN')
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _fill_of(self, b: Dict[str, Any], order_ids: List[str]) -> Tuple[str, int, Optional[float]]:
        """('FILLED' | 'REJECTED' | 'PENDING', filled_qty, avg_price) summed
        over the freeze-limit chunks of one leg. PENDING while any chunk is
        still working or unreadable; REJECTED only when nothing traded."""
        svc = b['svc']
        filled_qty = 0
        notional = 0.0
        pending = rejected = False
        if b['kind'] == 'zerodha':
            for oid in order_ids:
                st = svc.get_order_status(oid)
                if not st.get('success'):
                    pending = True
                    continue
                status = str(st.get('status') or '').upper()
                fq = int(st.get('filled_quantity') or 0)
                ap = float(st.get('average_price') or 0)
                if status == 'COMPLETE':
                    filled_qty += fq
                    notional += fq * ap
                elif status in ('REJECTED', 'CANCELLED'):
                    rejected = True
                    filled_qty += fq            # a cancelled order can still have traded part-way
                    notional += fq * ap
                else:
                    pending = True
        else:
            book = {str(o.get('id')): o for o in ((svc.get_orderbook() or {}).get('orders') or [])}
            for oid in order_ids:
                o = book.get(str(oid))
                if not o:
                    pending = True
                    continue
                # Fyers V3: 6=Pending, 4=Transit, 1=Cancelled, 2=Filled, 5=Rejected
                raw = int(o.get('status') or 0)
                fq = int(o.get('filledQty') or 0)
                ap = float(o.get('tradedPrice') or o.get('avgPrice') or 0)
                if raw == 2:
                    filled_qty += fq
                    notional += fq * ap
                elif raw in (1, 5):
                    rejected = True
                    filled_qty += fq
                    notional += fq * ap
                else:
                    pending = True
        if pending:
            return 'PENDING', filled_qty, (notional / filled_qty if filled_qty else None)
        if filled_qty <= 0:
            return 'REJECTED', 0, None
        return 'FILLED', filled_qty, notional / filled_qty

    def _poll_fills(self, legs: List[Dict[str, Any]], key: str) -> None:
        """Poll each leg's `{key}_order_ids` until terminal or the attempts run
        out. Writes `{key}_status` ('FILLED'/'REJECTED'), `{key}_qty` and
        `{key}_price`; a leg still PENDING at the end is left with no status
        for the caller to decide about."""
        for attempt in range(_FILL_POLL_ATTEMPTS):
            open_legs = [l for l in legs if l.get(f'{key}_order_ids') and l.get(f'{key}_status') is None]
            if not open_legs:
                return
            if attempt and _FILL_POLL_WAIT_SECS:
                time.sleep(_FILL_POLL_WAIT_SECS)
            for leg in open_legs:
                b = self._broker_for(leg['broker_idx'])
                if b is None:
                    continue
                try:
                    state, fq, ap = self._fill_of(b, leg[f'{key}_order_ids'])
                except Exception as e:
                    self.log.warning(f"broker {leg['broker_idx']}: {key} fill lookup failed: {e}")
                    self._on_broker_error(e, f'{key} fill lookup')
                    continue
                if state == 'PENDING':
                    continue
                leg[f'{key}_status'] = state
                leg[f'{key}_qty'] = int(fq)
                leg[f'{key}_price'] = round(float(ap), 2) if ap else None

    def _alert_order_failure(self, symbol: str, what: str, detail: str) -> None:
        """One in-app alert per symbol per kind per day — a dead login fails
        every symbol, and forty identical alerts is how the one that mattered
        gets scrolled past. The log carries every occurrence."""
        key = f'{symbol}:{what}'
        today = date.today().isoformat()
        if self._order_alerts.get(key) == today:
            return
        self._order_alerts[key] = today
        if self._uvar('EMA_CONFLUENCE_NOTIFY', 'true').lower() == 'false':
            return
        try:
            from trading_app.service.notification_service import create_notification
            create_notification(
                category='ema_confluence_order_failed',
                title=f"EMA Confluence — {what.upper()} order failed: {symbol}",
                summary=detail[:200],
                data={'symbol': symbol, 'what': what, 'error': detail},
            )
        except Exception as e:
            self.log.error(f"{symbol}: order-failure notification failed: {e}")
        self._send_telegram(symbol, f"⚠️ EMA Confluence — {what.upper()} ORDER FAILED\n{symbol}\n{detail[:300]}",
                            tag='order_failed')

    def _place_entry_legs(self, symbol: str, s: Dict[str, Any], action: str
                          ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """The entry MARKET order at every live broker. Returns the legs that
        were accepted (order ids, not yet fills) and the errors from the rest."""
        token = s.get('future_token')
        lot_size = int(s.get('lot_size') or 1)
        legs: List[Dict[str, Any]] = []
        errors: List[str] = []
        for b in self._broker_list:
            ts = self._leg_symbol(b, symbol, token)
            qty = b['lots'] * lot_size
            ids: List[str] = []
            placed_qty = 0
            err: Optional[str] = None
            for chunk in _lot_chunks(qty, lot_size):
                r = self._place_leg(b, symbol, ts, action, chunk)
                if r.get('success'):
                    ids.append(str(r.get('order_id')))
                    placed_qty += chunk
                else:
                    err = str(r.get('error'))
                    break
            if ids:
                legs.append({'broker_idx': b['idx'], 'kind': b['kind'], 'lots': b['lots'],
                             'qty': placed_qty, 'tradingsymbol': ts, 'token': token,
                             'entry_order_ids': ids})
                self.log.info(f"[LIVE] {symbol}: entry {action} {ts} x{placed_qty} placed at "
                              f"broker {b['idx']} ({b['kind']}) — order_id={ids}")
            if err:
                errors.append(f"broker {b['idx']} ({b['kind']}): {err}")
                self.log.error(f"[LIVE] {symbol}: entry {action} {ts} FAILED at broker {b['idx']}: {err}")
        return legs, errors

    def _execute_live_entry(self, symbol: str, s: Dict[str, Any], action: str) -> List[Dict[str, Any]]:
        """Enter at every live broker and confirm the fills. Returns the legs
        that HOLD something; [] means every account is flat and the caller
        must not open a position."""
        if not self._broker_list:
            self._refresh_brokers('entry with no broker sessions', force=True)
        if not self._broker_list:
            self._alert_order_failure(symbol, 'entry',
                                      'EMA_CONFLUENCE_MODE=live but no zerodha/fyers broker carries '
                                      'BROKER_N_EMA_ACTIVE=true (or none is logged in) — no order placed')
            return []
        legs, errors = self._place_entry_legs(symbol, s, action)
        if not legs and any(_is_auth_error(e) for e in errors) \
                and self._refresh_brokers('entry failed auth', force=True) and self._broker_list:
            legs, errors = self._place_entry_legs(symbol, s, action)
        if not legs:
            self._alert_order_failure(symbol, 'entry', '; '.join(errors) or 'no broker accepted the order')
            return []
        self._poll_fills(legs, 'entry')
        held: List[Dict[str, Any]] = []
        for leg in legs:
            if leg.get('entry_status') == 'REJECTED':
                self.log.error(f"[LIVE] {symbol}: entry at broker {leg['broker_idx']} was rejected "
                               f"after acceptance (order {leg['entry_order_ids']}) — that account is flat")
                continue
            if leg.get('entry_status') is None:
                # Accepted, but not yet visible as COMPLETE. A MARKET order in
                # a liquid future has filled; carry the leg at its ordered qty,
                # priced off the mark until the order book catches up. It is
                # marked so the audit trail says the fill was never read back.
                leg['entry_status'] = 'UNCONFIRMED'
                leg['entry_qty'] = leg['qty']
                leg['entry_price'] = None
                self.log.warning(f"[LIVE] {symbol}: entry at broker {leg['broker_idx']} accepted but "
                                 f"its fill did not confirm within the poll window — carried at the mark")
            held.append(leg)
        if not held:
            self._alert_order_failure(symbol, 'entry', 'every broker rejected the entry order')
        return held

    def _execute_live_exit(self, symbol: str, s: Dict[str, Any], reason: str) -> bool:
        """Flatten every leg still open at a broker with the opposite MARKET
        order. Returns True when the whole position is flat. A leg whose exit
        is rejected keeps no order ids and is retried next call; one whose
        order is accepted but never confirms is retried a few ticks and then
        assumed filled at the mark (see _EXIT_UNCONFIRMED_TICKS)."""
        legs = s.get('broker_legs') or []
        action = 'SELL' if s['direction'] == 'Long' else 'BUY'
        lot_size = int(s.get('lot_size') or 1)
        if not self._broker_list:
            self._refresh_brokers(f'{reason} exit with no broker sessions', force=True)
        errors: List[str] = []
        to_poll: List[Dict[str, Any]] = []
        for leg in legs:
            if leg.get('exit_status') in ('FILLED', 'UNCONFIRMED'):
                continue
            if leg.get('exit_order_ids'):
                to_poll.append(leg)              # placed on an earlier tick, still confirming
                continue
            b = self._broker_for(leg['broker_idx'])
            if b is None:
                errors.append(f"broker {leg['broker_idx']}: no session")
                continue
            held_qty = int(leg.get('entry_qty') or leg.get('qty') or 0)
            ids: List[str] = []
            err: Optional[str] = None
            for chunk in _lot_chunks(held_qty, lot_size):
                r = self._place_leg(b, symbol, leg['tradingsymbol'], action, chunk)
                if r.get('success'):
                    ids.append(str(r.get('order_id')))
                else:
                    err = str(r.get('error'))
                    break
            if ids:
                leg['exit_order_ids'] = ids
                leg['exit_polls'] = 0
                to_poll.append(leg)
                self.log.info(f"[LIVE] {symbol}: {reason} exit {action} {leg['tradingsymbol']} "
                              f"x{held_qty} placed at broker {b['idx']} — order_id={ids}")
            if err:
                errors.append(f"broker {b['idx']}: {err}")
                self.log.error(f"[LIVE] {symbol}: {reason} exit at broker {b['idx']} FAILED: {err}")
                self._on_broker_error(err, f'{reason} exit')
        self._poll_fills(to_poll, 'exit')
        for leg in to_poll:
            held_qty = int(leg.get('entry_qty') or leg.get('qty') or 0)
            if leg.get('exit_status') == 'FILLED' and int(leg.get('exit_qty') or 0) < held_qty:
                # A MARKET order in a liquid future does not part-fill, so this
                # is a broker-side anomaly (a chunk cancelled by the exchange,
                # say). The leg is treated as closed — the retry loop cannot
                # chase a quantity it has no order for — but it is not quiet.
                errors.append(f"broker {leg['broker_idx']}: exit filled {leg.get('exit_qty')} of "
                              f"{held_qty} — CHECK THE BROKER for the remainder")
                self.log.error(f"[LIVE] {symbol}: exit at broker {leg['broker_idx']} filled only "
                               f"{leg.get('exit_qty')} of {held_qty}; the remainder is NOT managed")
                self._alert_order_failure(symbol, 'exit', errors[-1])
            if leg.get('exit_status') == 'REJECTED':
                errors.append(f"broker {leg['broker_idx']}: exit order {leg['exit_order_ids']} rejected")
                self.log.error(f"[LIVE] {symbol}: exit at broker {leg['broker_idx']} rejected — will retry")
                leg.pop('exit_order_ids', None)
                leg['exit_status'] = None
            elif leg.get('exit_status') is None:
                leg['exit_polls'] = int(leg.get('exit_polls') or 0) + 1
                if leg['exit_polls'] >= _EXIT_UNCONFIRMED_TICKS:
                    leg['exit_status'] = 'UNCONFIRMED'
                    leg['exit_qty'] = int(leg.get('entry_qty') or leg.get('qty') or 0)
                    leg['exit_price'] = None
                    self.log.warning(f"[LIVE] {symbol}: exit at broker {leg['broker_idx']} never "
                                     f"confirmed — assumed filled at the mark; CHECK THE BROKER")
        still_open = [l for l in legs if l.get('exit_status') not in ('FILLED', 'UNCONFIRMED')]
        if still_open:
            if errors:
                self._alert_order_failure(symbol, 'exit', '; '.join(errors))
            return False
        return True

    # ── Data ─────────────────────────────────────────────────────────────

    def _get_provider(self) -> Any:
        from trading_app.service.provider_logic import get_data_provider
        return get_data_provider(user=self.username, context='algo_ema')

    def _underlying_token(self, symbol: str, is_symbol_provider: bool) -> Any:
        if is_symbol_provider:
            return _FYERS_INDICES.get(symbol, f'NSE:{symbol}-EQ')
        kite_indices = {'NIFTY': 256265, 'BANKNIFTY': 260105, 'SENSEX': 265}
        return kite_indices.get(symbol, symbol)

    def _list_contracts(self, provider: Any, is_symbol_provider: bool, symbol: str) -> List[Dict[str, Any]]:
        """Every live monthly FUTURES contract for `symbol`, nearest first:
        [{'symbol': token, 'expiry': date, 'lot_size': int}, ...]. Empty when
        resolution fails — callers keep whatever they already had."""
        try:
            if is_symbol_provider:
                return provider.list_future_contracts(symbol) or []
            if symbol in _BSE_UNDERLYINGS:
                # KiteService resolves futures out of the NFO master only, so a
                # BSE contract would silently come back as a wrong/absent token.
                # Better to skip the symbol outright than trade a bad one.
                self.log.warning(f"{symbol}: BSE futures aren't resolvable on the Kite path — skipping")
                return []
            from trading_app.service.kite_order_services import KiteService
            svc = KiteService(kite_instance=provider)
            return [dict(c, symbol=f"NFO:{c['symbol']}")
                    for c in (svc.list_future_contracts(symbol) or [])]
        except Exception as e:
            self.log.warning(f"{symbol}: future resolution failed: {e}")
            return []

    def _ensure_future_token(self, provider: Any, is_symbol_provider: bool, symbol: str,
                             s: Dict[str, Any], now: Optional[datetime] = None) -> Optional[Any]:
        """The contract this symbol should be quoting against, resolving and
        rolling as needed.

        A cached token names one specific monthly contract, so it goes dead at
        that month's expiry — re-resolve once a day, and again the moment the
        held contract reaches its roll window (see roll_due_at). An OPEN paper
        position is still PINNED to the contract it was entered on: it must
        never be marked against an instrument it wasn't filled on, so a due
        roll only records where it is going (`roll_to_*`) and leaves the swap
        to _roll_position, which books the near leg first.
        """
        now = now or datetime.now()
        today_str = now.date().isoformat()
        token  = s.get('future_token')
        expiry = _parse_iso_date(s.get('future_expiry'))
        # Cheap short-circuit: no list scan on the ~99% of ticks nowhere near a
        # roll window, so the daily-cache behaviour below is unchanged.
        due = expiry is not None and now >= roll_due_at(expiry, self._roll_sessions)
        fresh = s.get('future_resolved_on') == today_str or s.get('phase') == 'in_position'
        # A symbol whose contract expiry we don't know yet must NOT take the
        # short-circuit: for an open position that pins it forever (the daily
        # re-resolve never runs), and for an armed one it would leave the roll
        # check blind until tomorrow. Resolving once is what backfills
        # future_expiry — every symbol carried over from before this field
        # existed goes through here exactly once.
        needs_expiry = token is not None and expiry is None
        if token and fresh and not due and not needs_expiry:
            # Label a token resolved before this field existed (e.g. a position
            # already open when the app restarted) off the token itself — a
            # pinned contract must not be re-resolved just to name its month.
            if not s.get('future_month'):
                s['future_month'] = _contract_month_label(token)
            return token

        contracts = self._list_contracts(provider, is_symbol_provider, symbol)
        target = select_contract(now, contracts, self._roll_sessions)
        if target is None:
            # A failed re-resolution keeps yesterday's token rather than going dark.
            return token

        if s.get('phase') == 'in_position' and token:
            held = next((c for c in contracts if c['symbol'] == token), None)
            # Delisted: the contract we hold is gone from the master, so no
            # quote for it is ever coming again. _roll_position needs to know,
            # or it would wait forever for a near-leg price.
            s['future_delisted'] = held is None
            if held is not None and expiry is None:
                # One-time backfill for a position opened before future_expiry
                # existed (the 5 positions live when this shipped).
                s['future_expiry'] = held['expiry'].isoformat()
            elif held is None:
                self.log.warning(f"{symbol}: held contract {token} is no longer listed — "
                                 f"rolling to {target['symbol']}")
            if target['symbol'] != token:
                s['roll_to_token']    = target['symbol']
                s['roll_to_expiry']   = target['expiry'].isoformat()
                s['roll_to_lot_size'] = int(target['lot_size'] or 1)
            return token

        if token and target['symbol'] != token and s.get('phase') == 'watching':
            self.log.info(f"{symbol}: armed setup moved to the next contract — "
                          f"{_contract_month_label(token)} -> {_contract_month_label(target['symbol'])} "
                          f"(trigger {s.get('trigger_level')} unchanged)")
        s['future_token']       = target['symbol']
        s['lot_size']           = int(target['lot_size'] or 1)
        s['future_month']       = _contract_month_label(target['symbol'])
        s['future_expiry']      = target['expiry'].isoformat()
        s['future_resolved_on'] = today_str
        return target['symbol']

    def _get_ltp_batch(self, provider: Any, tokens: Dict[str, Any]) -> Dict[str, float]:
        """One quote request for every instrument this tick needs — futures
        contracts AND their underlyings, keyed by whatever the caller called
        them. Deduped, so a symbol quoted under two keys costs one token."""
        uniq_tokens = list({t for t in tokens.values() if t})
        if not uniq_tokens:
            return {}
        try:
            try:
                data = provider.ltp(uniq_tokens, max_age=_QUOTE_MAX_AGE_SECS) or {}
            except TypeError:
                # Fyers/Kite take no max_age — same shape as the fallback in
                # Backtest/minute_candle_store._fetch.
                data = provider.ltp(uniq_tokens) or {}
        except Exception as e:
            self.log.warning(f"batch LTP fetch failed: {e}")
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

    def _daily_history(self, provider: Any, is_symbol_provider: bool, symbol: str,
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
        token = self._underlying_token(symbol, is_symbol_provider)
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

    def _scan_one(self, provider: Any, is_symbol_provider: bool, symbol: str, cfg: Dict[str, Any],
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
        df = self._daily_history(provider, is_symbol_provider, symbol, to_date)
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
            # Inside `not already_seen`, so this is the dedupe: a setup that
            # stays armed across days announces itself once, on the scan that
            # first found that signal candle. Guarded — a failed alert must not
            # abort the scan and leave the remaining symbols unscanned.
            try:
                self._notify_signal(symbol, s, age)
            except Exception as e:
                self.log.error(f"{symbol}: signal notification failed: {e}")
        return scanned_candle

    def _run_daily_scan(self, provider: Any, is_symbol_provider: bool, state: Dict[str, Any],
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
                # …unless it was armed off a weekend bar. Those aren't real
                # sessions (see filters/candle_store.drop_weekend_bars) and the
                # skip below would otherwise pin the phantom trigger/SL here
                # forever, since a watching symbol is never re-scanned.
                if _is_weekend_signal(s.get('signal_date')):
                    if s.get('phase') == 'watching':
                        self.log.warning(
                            f"{symbol}: dropping setup armed off non-session candle "
                            f"{s.get('signal_date')} (trigger={s.get('trigger_level')} "
                            f"sl={s.get('sl_level')}) — re-scanning on real candles"
                        )
                        self._reset_for_next_scan(s)
                    else:
                        # An open paper position isn't unwound behind the user's
                        # back — flag it and let them close it from the UI.
                        self.log.warning(
                            f"{symbol}: OPEN paper position came from non-session candle "
                            f"{s.get('signal_date')} — entry {s.get('entry_price')} is off a "
                            f"trigger that never traded in a real session; review it manually"
                        )
                        continue
                else:
                    continue  # one pending/open setup at a time, same as the backtest
            if only_pending and s.get('phase') != 'pending_scan':
                continue
            try:
                candle = self._scan_one(provider, is_symbol_provider, symbol, cfg, s, to_date)
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

    # ── Trade lifecycle ─────────────────────────────────────────────────

    def _fire_paper_entry(self, symbol: str, s: Dict[str, Any], spot: float,
                          fut_ltp: float, lots: int) -> None:
        """The trigger broke on the UNDERLYING at `spot`; the order fills on
        the FUTURE — at `fut_ltp` in paper mode, at the brokers' actual fills
        in live mode (the name predates the live path; it is THE entry).

        Both are stored because they do different jobs for the rest of the
        trade's life. The Target is derived from the SPOT fill — exactly as the
        backtest derives it from the equity entry price, which is what makes a
        target_pct swept in the backtest mean the same thing here. The P&L is
        derived from the FUTURES fill, because that is the instrument held.
        """
        direction   = s['direction']
        target_pct  = self._configured_target_pct(symbol, s)
        if target_pct is None:
            # No Target this symbol owns — do NOT open a position on a guessed
            # one. Leaving it armed costs nothing (the trigger is re-evaluated
            # every tick) and the next daily scan re-arms it with a real one.
            self.log.error(f"{symbol}: trigger broke at spot {spot} but no Target % is "
                           f"configured for it (not in EMA_SYMBOL_DEFAULTS, and none stored "
                           f"on the armed setup) — entry refused, setup left armed")
            return
        spot_entry  = round(float(spot), 2)
        entry_price = round(float(fut_ltp), 2)
        target_level = round(spot_entry * (1 + target_pct / 100), 2) if direction == 'Long' \
            else round(spot_entry * (1 - target_pct / 100), 2)
        lot_size = int(s.get('lot_size', 1) or 1)
        qty = max(1, lots) * lot_size
        legs: List[Dict[str, Any]] = []
        if self._live:
            legs = self._execute_live_entry(symbol, s, 'BUY' if direction == 'Long' else 'SELL')
            if not legs:
                # No account holds anything, so there is no position to manage.
                # Dropped to the next scan rather than left armed: the trigger
                # is still crossed, and an armed setup would fire a fresh order
                # every 15s at whatever is refusing them.
                self.log.error(f"{symbol}: trigger broke at spot {spot} but no broker holds a "
                               f"position — setup dropped, the next scan decides afresh")
                self._reset_for_next_scan(s)
                return
            qty = sum(int(l.get('entry_qty') or 0) for l in legs)
            entry_price = _legs_avg(legs, 'entry', fallback=entry_price) or entry_price
            lots = sum(int(l.get('lots') or 0) for l in legs) or lots

        s['spot_entry_price'] = spot_entry
        s['entry_price']  = entry_price
        s['target_level'] = target_level
        s['qty']           = qty
        s['entry_time']    = datetime.now().isoformat()
        s['phase']         = 'in_position'
        s['ltp']            = entry_price   # marked to market from the next tick on
        s['spot_ltp']       = spot_entry
        s['unrealized_pnl'] = 0.0
        s['mode']           = 'live' if legs else 'paper'
        if legs:
            s['broker_legs'] = legs
        else:
            s.pop('broker_legs', None)
        self.log.info(
            f"[{s['mode'].upper()}] {symbol}: ENTERED {direction.upper()} — trigger {s['trigger_level']} broke at "
            f"spot {spot_entry}; filled {s.get('future_month') or 'FUT'} @ {entry_price}. "
            f"sl={s['sl_level']} tgt={target_level} (both spot-scale) qty={qty}"
            + (f" across brokers {[l['broker_idx'] for l in legs]}" if legs else '')
        )
        self._notify_new_entry(symbol, s, lots)

    # ── New-entry notification ───────────────────────────────────────────
    # A breakout fills at most once per symbol per signal, so this fires only
    # on the transition into a position — never on the marked-to-market ticks
    # that follow. Nothing in here may raise or block: a notification failure
    # must not cost us the paper trade that has already been recorded in
    # state, and the Telegram round trip is slower than the _POLL_SECS tick.

    def _notify_new_entry(self, symbol: str, s: Dict[str, Any], lots: int) -> None:
        try:
            payload = {
                'symbol':        symbol,
                'direction':     'BUY' if s['direction'] == 'Long' else 'SELL',
                'entry_price':   s['entry_price'],
                'spot_entry_price': s.get('spot_entry_price'),
                'sl_price':      s.get('sl_level'),
                'target_price':  s.get('target_level'),
                'target_pct':    s.get('target_pct'),
                'qty':           s['qty'],
                'lots':          max(1, lots),
                'lot_size':      s.get('lot_size'),
                'future_month':  s.get('future_month'),
                'signal_date':   s.get('signal_date'),
                'entry_time':    s.get('entry_time'),
                'mode':          self._mode_of(s),
                'brokers':       [l['broker_idx'] for l in (s.get('broker_legs') or [])],
            }
        except Exception as e:
            self.log.error(f"{symbol}: entry notification payload failed: {e}")
            return

        if self._uvar('EMA_CONFLUENCE_NOTIFY', 'true').lower() != 'false':
            try:
                from trading_app.service.notification_service import create_notification
                create_notification(
                    category='ema_confluence_entry',
                    title=f"EMA Confluence — {payload['direction']} {symbol}",
                    summary=(f"Entry ₹{payload['entry_price']} · SL ₹{payload['sl_price']} · "
                             f"Tgt ₹{payload['target_price']} · qty {payload['qty']}"),
                    data=payload,
                )
            except Exception as e:
                self.log.error(f"{symbol}: in-app entry notification failed: {e}")

        self._send_entry_telegram(symbol, payload)

    def _send_telegram(self, symbol: str, message: str, tag: str = 'entry') -> None:
        """Fire-and-forget Telegram alert. Credentials come from the user's own
        env file; with either unset this is silently a no-op, so the in-app bell
        keeps working on an install that has never set Telegram up."""
        if self._uvar('EMA_CONFLUENCE_TELEGRAM', 'true').lower() == 'false':
            return
        token   = self._uvar('TELEGRAM_BOT_TOKEN')
        chat_id = self._uvar('TELEGRAM_CHAT_ID')
        if not (token and chat_id):
            return

        def _send() -> None:
            try:
                from trading_app.service.telegram_service import TelegramService
                result = TelegramService(token=token, chat_id=chat_id).send_text(message)
                if result.get('success'):
                    self.log.info(f"{symbol}: {tag} Telegram alert sent")
                else:
                    self.log.error(f"{symbol}: {tag} Telegram alert failed: {result.get('error')}")
            except Exception as e:
                self.log.error(f"{symbol}: {tag} Telegram alert failed: {e}")

        # Off-thread: send_text allows a 10s HTTP timeout, which is most of a
        # poll interval, and a batch of simultaneous entries would serialise.
        threading.Thread(target=_send, daemon=True,
                         name=f'EmaConfluenceNotify-{symbol}').start()

    def _send_entry_telegram(self, symbol: str, payload: Dict[str, Any]) -> None:
        tgt_pct = payload.get('target_pct')
        entered = str(payload.get('entry_time') or '')[11:19]
        message = '\n'.join([
            f"📈 EMA Confluence — NEW ENTRY",
            f"{symbol} · {payload['direction']} · {payload.get('future_month') or 'FUT'}",
            f"Entry  ₹{payload['entry_price']} (fut)",
            f"Spot   ₹{payload.get('spot_entry_price')} — SL/Target are on this scale",
            f"SL     ₹{payload['sl_price']}",
            f"Target ₹{payload['target_price']}" + (f" ({tgt_pct}%)" if tgt_pct else ''),
            f"Qty    {payload['qty']} ({payload['lots']} lot)",
            f"Signal {payload.get('signal_date') or '-'} · entered {entered or '-'}",
            self._trade_tag(payload),
        ])
        self._send_telegram(symbol, message, tag='entry')

    @staticmethod
    def _trade_tag(payload: Dict[str, Any]) -> str:
        """The last line of every Telegram alert: which kind of trade this was."""
        if payload.get('mode') == 'live':
            brokers = payload.get('brokers') or []
            return f"(LIVE trade · brokers {brokers})" if brokers else "(LIVE trade)"
        return "(paper trade)"

    # ── Roll notification ────────────────────────────────────────────────
    # Deliberately NOT the new-entry alert: a roll is the same swing trade
    # changing contract, and firing "NEW ENTRY" every month for a long-held
    # position would train the eye to ignore the real ones. Same contract as
    # _notify_new_entry — nothing here may raise or block.

    def _notify_roll(self, symbol: str, s: Dict[str, Any],
                     old_month: Optional[str], near_ltp: float, booked_pnl: Any) -> None:
        if self._uvar('EMA_CONFLUENCE_NOTIFY_ROLL', 'true').lower() == 'false':
            return
        new_month = s.get('future_month') or 'FUT'
        old_month = old_month or 'FUT'
        side = 'BUY' if s['direction'] == 'Long' else 'SELL'
        payload = {
            'symbol': symbol, 'direction': side, 'mode': self._mode_of(s),
            'brokers': [l['broker_idx'] for l in (s.get('broker_legs') or [])],
            'from_month': old_month, 'to_month': new_month,
            'exit_price': round(float(near_ltp), 2), 'booked_pnl': booked_pnl,
            'entry_price': s.get('entry_price'), 'sl_price': s.get('sl_level'),
            'target_price': s.get('target_level'), 'qty': s.get('qty'),
            'lot_size': s.get('lot_size'), 'signal_date': s.get('signal_date'),
            'roll_count': s.get('roll_count'), 'entry_time': s.get('entry_time'),
        }

        if self._uvar('EMA_CONFLUENCE_NOTIFY', 'true').lower() != 'false':
            try:
                from trading_app.service.notification_service import create_notification
                create_notification(
                    category='ema_confluence_roll',
                    title=f"EMA Confluence — ROLLED {symbol} {old_month} → {new_month}",
                    summary=(f"Booked ₹{booked_pnl} on {old_month} @ ₹{payload['exit_price']} · "
                             f"re-entered {side} @ ₹{payload['entry_price']} · qty {payload['qty']}"),
                    data=payload,
                )
            except Exception as e:
                self.log.error(f"{symbol}: in-app roll notification failed: {e}")

        message = '\n'.join([
            f"🔁 EMA Confluence — CONTRACT ROLLED",
            f"{symbol} · {side} · {old_month} → {new_month}",
            f"Booked ₹{booked_pnl} (exit ₹{payload['exit_price']})",
            f"Re-entry ₹{payload['entry_price']}",
            f"SL     ₹{payload['sl_price']}  (unchanged)",
            f"Target ₹{payload['target_price']}  (unchanged)",
            f"Qty    {payload['qty']} · roll #{payload['roll_count']}",
            f"Signal {payload.get('signal_date') or '-'}",
            self._trade_tag(payload),
        ])
        self._send_telegram(symbol, message, tag='roll')

    # ── Exit notification ────────────────────────────────────────────────
    # Fired for SL and TARGET. NOT for ROLL: a roll already gets its own
    # _notify_roll, which explains the contract swap — announcing it a second
    # time as an "exit" would read as the trade having closed when it is still
    # open on the next month.
    #
    # Same contract as _notify_new_entry: this sits on the algo's poll thread,
    # so nothing here may raise or block.
    _EXIT_STYLE = {
        'SL':     ('🛑', 'SL HIT'),
        'TARGET': ('🎯', 'TARGET HIT'),
        # Not a strategy exit: the symbol was removed from EMA_SYMBOL_DEFAULTS,
        # so the position is booked out at its last mark rather than left open
        # in a stock this algo no longer trades. Worth an alert precisely
        # because no signal asked for it.
        'UNIVERSE_DROP': ('🗑', 'CLOSED — REMOVED FROM UNIVERSE'),
    }

    def _notify_exit(self, symbol: str, s: Dict[str, Any], record: Dict[str, Any]) -> None:
        reason = record.get('reason') or 'EXIT'
        emoji, headline = self._EXIT_STYLE.get(reason, ('📕', f'{reason} EXIT'))
        pnl = record.get('pnl') or 0.0
        won = pnl >= 0

        if self._uvar('EMA_CONFLUENCE_NOTIFY', 'true').lower() != 'false':
            try:
                from trading_app.service.notification_service import create_notification
                create_notification(
                    category='ema_confluence_exit',
                    title=f"EMA Confluence — {headline} {symbol}",
                    summary=(f"Exit ₹{record['exit_price']} · entry ₹{record['entry_price']} · "
                             f"P&L {'+' if won else ''}₹{pnl} · qty {record['qty']}"),
                    data=dict(record),
                )
            except Exception as e:
                self.log.error(f"{symbol}: in-app exit notification failed: {e}")

        try:
            exited = str(record.get('exit_time') or '')[11:19]
            message = '\n'.join([
                f"{emoji} EMA Confluence — {headline}",
                f"{symbol} · {record['direction']} · {s.get('future_month') or 'FUT'}",
                f"Entry  ₹{record['entry_price']} (fut)",
                f"Exit   ₹{record['exit_price']} (fut)",
                (f"Hit    spot ₹{record['spot_exit_price']} vs "
                 f"{'SL' if reason == 'SL' else 'target'} ₹"
                 f"{record['sl_price'] if reason == 'SL' else record['target_price']}"),
                f"P&L    {'+' if won else ''}₹{pnl}",
                f"Qty    {record['qty']}",
                f"Signal {record.get('signal_date') or '-'} · exited {exited or '-'}",
                "(paper trade)",
            ])
            self._send_telegram(symbol, message, tag='exit')
        except Exception as e:
            self.log.error(f"{symbol}: exit Telegram alert failed to build: {e}")

    # ── Signal-candle notification ───────────────────────────────────────
    # Fired when a setup is first ARMED — the symbol moves to `watching` with a
    # trigger and SL off the signal candle. Deduped on signal_date by the
    # caller's `already_seen` check, so a setup that stays armed for days
    # announces itself once rather than on every scan.
    def _notify_signal(self, symbol: str, s: Dict[str, Any], age_days: int) -> None:
        payload = {
            'symbol':        symbol,
            'direction':     'BUY' if s.get('direction') == 'Long' else 'SELL',
            'trigger_level': s.get('trigger_level'),
            'sl_price':      s.get('sl_level'),
            'target_pct':    s.get('target_pct'),
            'signal_date':   s.get('signal_date'),
            'future_month':  s.get('future_month'),
            'age_days':      age_days,
            'mode':          'paper',
        }

        if self._uvar('EMA_CONFLUENCE_NOTIFY', 'true').lower() != 'false':
            try:
                from trading_app.service.notification_service import create_notification
                create_notification(
                    category='ema_confluence_signal',
                    title=f"EMA Confluence — {payload['direction']} setup {symbol}",
                    summary=(f"Trigger ₹{payload['trigger_level']} · SL ₹{payload['sl_price']} · "
                             f"signal candle {payload['signal_date']}"),
                    data=payload,
                )
            except Exception as e:
                self.log.error(f"{symbol}: in-app signal notification failed: {e}")

        try:
            tgt_pct = payload.get('target_pct')
            message = '\n'.join([
                f"👀 EMA Confluence — NEW SETUP (watching)",
                f"{symbol} · {payload['direction']} · {payload.get('future_month') or 'FUT'}",
                f"Trigger ₹{payload['trigger_level']}",
                f"SL      ₹{payload['sl_price']}",
                (f"Target  {tgt_pct}% from the spot entry" if tgt_pct else "Target  -"),
                f"Signal candle {payload['signal_date']}"
                + (f" ({age_days}d old)" if age_days else ""),
                "(no order yet — waiting for the trigger)",
            ])
            self._send_telegram(symbol, message, tag='signal')
        except Exception as e:
            self.log.error(f"{symbol}: signal Telegram alert failed to build: {e}")

    def _record_exit(self, symbol: str, s: Dict[str, Any], exit_price: float, reason: str,
                     spot_price: Optional[float] = None) -> None:
        """Book the paper trade out. `exit_price` is always a FUTURES price —
        the contract is what's held, so it is what the P&L comes off.

        `spot_price` is the underlying quote that actually TRIGGERED the exit,
        recorded alongside so a closed trade can be audited on the scale it was
        decided on: `sl_price`/`target_price` are spot-scale levels, and without
        this the history would show them next to a futures fill with no way to
        tell whether the level really broke. None for a ROLL, which no level
        triggered.
        """
        direction   = s['direction']
        entry_price = s['entry_price']
        qty         = s['qty']
        pnl = (exit_price - entry_price) * qty if direction == 'Long' else (entry_price - exit_price) * qty
        legs = s.get('broker_legs') or []
        record = {
            'symbol': symbol, 'direction': 'BUY' if direction == 'Long' else 'SELL',
            'mode': self._mode_of(s), 'qty': qty, 'lot_size': s.get('lot_size'),
            # The audit trail of a live trade: which account held what, and the
            # order ids either side, so a history row can be matched to the
            # broker's own book. Absent on a paper trade.
            **({'broker_legs': [{k: l.get(k) for k in (
                'broker_idx', 'kind', 'tradingsymbol', 'entry_order_ids', 'entry_qty',
                'entry_price', 'entry_status', 'exit_order_ids', 'exit_qty', 'exit_price',
                'exit_status')} for l in legs]} if legs else {}),
            'entry_price': entry_price, 'exit_price': round(exit_price, 2),
            'spot_entry_price': s.get('spot_entry_price'),
            'spot_exit_price': round(float(spot_price), 2) if spot_price is not None else None,
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
        s['last_spot_entry_price'] = record['spot_entry_price']
        s['last_spot_exit_price']  = record['spot_exit_price']
        s['last_exit_time']   = record['exit_time']
        s['last_exit_price']  = record['exit_price']
        s['last_exit_reason'] = reason
        s['last_pnl']         = record['pnl']
        spot_note = f" (spot {record['spot_exit_price']} vs level)" if spot_price is not None else ''
        self.log.info(f"[{record['mode'].upper()}] {symbol}: EXIT ({reason}) @ {exit_price}{spot_note}, "
                      f"P&L ₹{record['pnl']}")
        # ROLL is excluded — _notify_roll covers it with the right wording.
        # Guarded so a notification failure can never lose the exit itself: the
        # history record and state above are already written by this point.
        if reason != 'ROLL':
            try:
                self._notify_exit(symbol, s, record)
            except Exception as e:
                self.log.error(f"{symbol}: exit notification failed: {e}")

    # Survives _reset_for_next_scan: the resolved contract (which doesn't
    # change intraday, so a same-symbol re-arm needn't re-resolve it) and the
    # last closed paper trade's audit trail. The roll_to_* keys deliberately
    # do NOT survive — they describe one pending swap, and a symbol that has
    # gone back to pending_scan has no position left to roll.
    _KEEP_ON_RESET = (
        'future_token', 'lot_size', 'future_month', 'future_resolved_on',
        'future_expiry',
        'last_entry_time', 'last_entry_price', 'last_exit_time',
        'last_exit_price', 'last_exit_reason', 'last_pnl',
        'last_spot_entry_price', 'last_spot_exit_price',
    )

    def _roll_position(self, symbol: str, s: Dict[str, Any],
                       near_ltp: Optional[float], far_ltp: Optional[float],
                       lots: int, held_listed: bool = True) -> bool:
        """Carry an open paper position from the expiring contract to the next
        month: book the near leg, re-enter the same side on the far one.

        The two legs are recorded as two trades because that is what a roll
        actually is — two round trips, two sets of costs, and possibly two
        different lot sizes. What makes them read as ONE setup is that
        everything describing the setup is written in place and left alone:
        direction, trigger_level, sl_level, target_level, spot_entry_price,
        target_pct and signal_date all carry over untouched. They can, because
        every one of them lives on the UNDERLYING's scale, and the underlying
        does not roll — only entry_price/qty/lot_size/future_* are re-stamped,
        and those are exactly the futures-side facts that DID change. In
        particular this must never call _reset_for_next_scan, which would send
        the symbol back to pending_scan and let tomorrow's scan arm a
        completely different setup.

        Returns True when the roll happened.
        """
        far_token = s.get('roll_to_token')
        if not far_token:
            return False
        if far_ltp is None:
            # Never exit the near leg into a hole — a symbol left flat has no
            # position to re-enter and no setup to re-arm. Once a day is enough.
            today_str = date.today().isoformat()
            if s.get('roll_warn_on') != today_str:
                s['roll_warn_on'] = today_str
                self.log.warning(f"{symbol}: roll to {far_token} deferred — no LTP for the "
                                 f"far month; staying on {s.get('future_token')}")
            return False
        if near_ltp is None:
            if held_listed:
                return False        # transient quote failure — retry next tick
            # The held contract is gone from the master: no quote is ever
            # coming, so mark the near leg out at its last known price rather
            # than stranding the position on a dead instrument.
            near_ltp = s.get('ltp')
            if near_ltp is None:
                self.log.warning(f"{symbol}: held contract {s.get('future_token')} is delisted and "
                                 f"has no last known price — cannot roll")
                return False
            self.log.warning(f"{symbol}: held contract {s.get('future_token')} is delisted — "
                             f"booking the near leg at its last mark {near_ltp}")

        old_month = s.get('future_month')
        old_token = s.get('future_token')
        legs = s.get('broker_legs') or []
        if legs:
            # LIVE: the near leg really has to come off at the broker before
            # anything is booked. Partial success (one broker out, another
            # refusing) leaves the refusing legs on the near contract and the
            # whole roll pending — roll_to_token stays set, so the next tick
            # comes straight back here and retries only what is still open.
            if not self._execute_live_exit(symbol, s, 'ROLL'):
                if not s.get('roll_pending'):
                    self.log.error(f"{symbol}: roll deferred — not every broker leg is off "
                                   f"{old_token} yet; retrying each tick")
                s['roll_pending'] = True
                return False
            near_fill = _legs_avg(legs, 'exit', fallback=float(near_ltp))
            if near_fill is not None:
                near_ltp = near_fill
        self._record_exit(symbol, s, float(near_ltp), 'ROLL')
        booked = s.get('last_pnl')
        s.pop('roll_pending', None)

        s['future_token']       = far_token
        s['future_expiry']      = s.get('roll_to_expiry')
        s['lot_size']           = int(s.get('roll_to_lot_size') or s.get('lot_size') or 1)
        s['future_month']       = _contract_month_label(far_token)
        s['future_resolved_on'] = date.today().isoformat()
        for k in ('roll_to_token', 'roll_to_expiry', 'roll_to_lot_size',
                  'roll_warn_on', 'future_delisted'):
            s.pop(k, None)

        entry_price = round(float(far_ltp), 2)
        qty = max(1, lots) * int(s['lot_size'])
        if legs:
            # Re-enter the far month at the same brokers, sized the way they
            # are flagged now. future_token/lot_size already name the far
            # contract, which is what _execute_live_entry orders.
            new_legs = self._execute_live_entry(symbol, s, 'BUY' if s['direction'] == 'Long' else 'SELL')
            if not new_legs:
                # Near leg gone, far leg never opened: every account is flat.
                # There is no position left to carry, and the breakout that
                # opened it is long past — so back to the scan, and say so.
                self.log.error(f"[LIVE] {symbol}: ROLL left every broker FLAT — the near leg "
                               f"{old_token} was closed but no far-month entry was accepted")
                self._alert_order_failure(symbol, 'roll',
                                          f"{symbol}: near month {old_month} closed but the far-month "
                                          f"re-entry failed at every broker — position is flat")
                self._reset_for_next_scan(s)
                return True
            s['broker_legs'] = new_legs
            qty = sum(int(l.get('entry_qty') or 0) for l in new_legs)
            entry_price = _legs_avg(new_legs, 'entry', fallback=entry_price) or entry_price
        s['entry_price']    = entry_price
        s['entry_time']     = datetime.now().isoformat()
        s['qty']            = qty
        s['ltp']            = entry_price
        s['unrealized_pnl'] = 0.0
        s['phase']          = 'in_position'
        s['rolled_from']    = old_month
        s['rolled_at']      = s['entry_time']
        s['roll_count']     = int(s.get('roll_count', 0)) + 1
        # target_level and spot_entry_price are deliberately NOT touched here.

        self.log.info(
            f"[{self._mode_of(s).upper()}] {symbol}: ROLLED {s['direction'].upper()} {old_month} -> {s['future_month']} "
            f"({old_token} @ {near_ltp} -> {far_token} @ {entry_price}), booked ₹{booked}, "
            f"sl={s.get('sl_level')} tgt={s.get('target_level')} qty={s['qty']}"
        )
        self._notify_roll(symbol, s, old_month, float(near_ltp), booked)
        return True

    def _reset_for_next_scan(self, s: Dict[str, Any]) -> None:
        kept = {k: s[k] for k in self._KEEP_ON_RESET if k in s}
        s.clear()
        s['phase'] = 'pending_scan'
        s.update(kept)

    # ── Main loop ────────────────────────────────────────────────────────

    def _in_session(self, now: datetime) -> bool:
        """True only while the market itself is open — 09:15 to 15:30 IST on a
        day the NSE actually holds a session. Gates everything PRICE-driven;
        the daily scan is not gated (it reads only completed daily candles, so
        running it during the pre-open head start is both safe and the point of
        starting early).

        Outside the session a quote request still returns a price — the LAST
        traded one — which is indistinguishable from a live tick here. Without
        this gate the 08:30 tick would fire a paper entry at a price that never
        traded today, and exit an open position on the same stale quote: the
        morning scan arms its trigger off yesterday's CLOSED candle, so any
        setup that closed beyond its own trigger would "break out" the instant
        the thread came up, hours before the market opened.

        Holidays are the same hazard: the scheduler's start job is only
        weekday-gated, so on a mid-week holiday the thread comes up and every
        quote it gets back is the previous session's close. It is also what
        makes the roll's "if that date is a holiday, do it the next trading
        session" true in practice — a roll moment that passes on a shut day
        simply stays due until the next session ticks.
        """
        from trading_app.app.utils.trading_calendar import is_trading_day
        if not is_trading_day(now.date()):   # weekend or NSE holiday
            return False
        now_mins = now.hour * 60 + now.minute
        return _MARKET_OPEN_MIN <= now_mins < _HARD_STOP_MIN

    def _decision_price(self, symbol: str, s: Dict[str, Any],
                        ltps: Dict[str, float]) -> Optional[float]:
        """The UNDERLYING's live price — the only thing a trigger, an SL or a
        Target is ever compared against.

        Returns None when the underlying didn't quote on this tick, and the
        caller then does nothing at all for that symbol. It must NEVER fall back
        to the future's LTP: that substitution IS the bug this split exists to
        remove, and on a strategy that holds for weeks a skipped 15s tick costs
        nothing while a silent ~0.5% basis error costs a fifth of a 3% target.
        """
        spot = ltps.get(f'{symbol}@spot')
        if spot is not None:
            s.pop('spot_gap_on', None)
            return float(spot)
        # Once per day, not once per 15s poll.
        today_str = date.today().isoformat()
        if s.get('spot_gap_on') != today_str:
            s['spot_gap_on'] = today_str
            self.log.warning(f"{symbol}: no underlying quote — trigger/SL/Target left "
                             f"unevaluated (never judged on the future's price)")
        return None

    def _tick(self, provider: Any, is_symbol_provider: bool, state: Dict[str, Any],
              algo_active: bool, lots: int, now: Optional[datetime] = None) -> None:
        now = now or datetime.now()
        today_str = date.today().isoformat()
        if state.get('last_scan_date') != today_str:
            self._run_daily_scan(provider, is_symbol_provider, state)
            state['last_scan_date'] = today_str
        elif any(s.get('phase') == 'pending_scan' for s in state['stocks'].values()):
            # Today's sweep already ran, but EMA_SYMBOL_DEFAULTS has since
            # grown (see _load_state's backfill) — scan the newcomers now
            # instead of leaving them dark until tomorrow morning.
            self._run_daily_scan(provider, is_symbol_provider, state, only_pending=True)

        if not self._in_session(now):
            # Logged once per hold, not once per 15s poll.
            if not self._session_hold_logged:
                self._session_hold_logged = True
                self.log.info("Outside market hours — setups stay armed, but no fills, "
                              "exits or LTP updates until 09:15")
            return
        self._session_hold_logged = False

        stocks = state['stocks']
        watching = {sym: s for sym, s in stocks.items() if s.get('phase') == 'watching'}
        inpos    = {sym: s for sym, s in stocks.items() if s.get('phase') == 'in_position'}
        if not watching and not inpos:
            return

        # First in-session tick of the day rebuilds the broker sessions: the
        # thread starts at 08:30 and the daily Kite login usually lands after
        # that, so the sessions built at start carry yesterday's dead token.
        if (self._live or any(s.get('broker_legs') for s in inpos.values())) \
                and self._brokers_refreshed_on != today_str:
            self._brokers_refreshed_on = today_str
            self._refresh_brokers('first in-session tick', force=True)

        tokens: Dict[str, Any] = {}
        for symbol, s in {**watching, **inpos}.items():
            token = self._ensure_future_token(provider, is_symbol_provider, symbol, s, now)
            if token:
                tokens[symbol] = token
        # Two prices per symbol, on the same tick and in the same request:
        #   {symbol}       — the FUTURE the paper order sits on (the money)
        #   {symbol}@spot  — the UNDERLYING every level is judged on (the logic)
        # A position due to roll adds a third, the far contract, so the near leg
        # can be booked and the far one opened without a second round trip.
        # The synthetic suffixes keep this ONE batched ltp() call ('@' can't
        # occur in an NSE root), so neither the spot quote nor the roll costs an
        # extra request against the app-wide 8 req/s budget.
        quote_map = dict(tokens)
        for symbol in {**watching, **inpos}:
            spot_token = self._underlying_token(symbol, is_symbol_provider)
            if spot_token:
                quote_map[f'{symbol}@spot'] = spot_token
        for symbol, s in inpos.items():
            if s.get('roll_to_token'):
                quote_map[f'{symbol}@roll'] = s['roll_to_token']

        ltps = self._get_ltp_batch(provider, quote_map)

        # Mark every armed/open symbol to market before acting on it, so the
        # status page can show the future's current value and — for an open
        # paper position — its running P&L, exactly like a live trade would.
        # `spot_ltp` rides along so the page can show how far the underlying is
        # from a trigger/SL/Target that is quoted on the underlying's scale;
        # reading distance-to-trigger off the future's value was misleading.
        for symbol, s in {**watching, **inpos}.items():
            spot = ltps.get(f'{symbol}@spot')
            if spot is not None:
                s['spot_ltp'] = round(float(spot), 2)
            ltp = ltps.get(symbol)
            if ltp is None:
                continue
            s['ltp']      = round(float(ltp), 2)
            s['ltp_time'] = datetime.now().isoformat()
            if s.get('phase') == 'in_position' and s.get('entry_price') and s.get('qty'):
                move = (ltp - s['entry_price']) if s['direction'] == 'Long' else (s['entry_price'] - ltp)
                s['unrealized_pnl'] = round(move * s['qty'], 2)

        for symbol, s in watching.items():
            spot = self._decision_price(symbol, s, ltps)
            if spot is None:
                continue
            direction = s['direction']
            trigger = s['trigger_level']
            crossed = (direction == 'Long' and spot >= trigger) or (direction == 'Short' and spot <= trigger)
            if not crossed:
                continue
            fut_ltp = ltps.get(symbol)
            if fut_ltp is None:
                # The level broke on the underlying, but the contract we would
                # fill on has no price this tick. Booking the fill at a stale
                # mark would invent a trade at a price that never traded, so
                # leave the trigger armed — the next tick, 15s later, retries.
                self.log.warning(f"{symbol}: {direction.upper()} trigger hit at spot {spot} but "
                                 f"{s.get('future_token')} did not quote — entry deferred a tick")
                continue
            if algo_active:
                self._fire_paper_entry(symbol, s, spot, fut_ltp, lots)
            else:
                self.log.info(f"{symbol}: {direction.upper()} trigger hit @ spot {spot} — "
                               f"no paper entry (EMA_CONFLUENCE_ACTIVE off)")
                self._reset_for_next_scan(s)

        for symbol, s in inpos.items():
            if s.get('exit_pending'):
                # A live exit already decided on an earlier tick, with legs
                # still open at a broker. The strategy has spoken; the spot
                # price no longer has a say — just keep flattening. A symbol
                # dropped from the universe was kept only for this (see
                # _retire_dropped_symbols) and leaves the state once flat.
                retire = s.get('exit_pending') == 'UNIVERSE_DROP'
                if self._finish_live_exit(symbol, s, ltps) and retire:
                    self.log.info(f"{symbol}: retired from state — not in the universe and now flat")
                    stocks.pop(symbol, None)
                continue
            spot = self._decision_price(symbol, s, ltps)
            if spot is None:
                continue
            direction = s['direction']
            sl, tgt = s['sl_level'], s['target_level']
            exit_reason = None
            if direction == 'Long':
                if spot <= sl:
                    exit_reason = 'SL'
                elif spot >= tgt:
                    exit_reason = 'TARGET'
            else:
                if spot >= sl:
                    exit_reason = 'SL'
                elif spot <= tgt:
                    exit_reason = 'TARGET'
            if exit_reason is None:
                continue
            # DECIDED on the underlying, BOOKED on the contract. The old code
            # booked at the SL/Target level itself, which only worked while the
            # levels were being (wrongly) compared to futures prices; a spot
            # level is not a price this contract can fill at. So the fill is the
            # future's own quote at the moment the level broke.
            # Unlike an entry, a missing quote cannot defer this — the strategy
            # has said the trade is over — so fall back to the last known mark
            # rather than carry a position whose exit has already triggered.
            exit_price = ltps.get(symbol, s.get('ltp'))
            if exit_price is None:
                self.log.warning(f"{symbol}: {exit_reason} hit at spot {spot} but the contract has "
                                 f"neither a quote nor a last mark — exit deferred a tick")
                continue
            if s.get('broker_legs'):
                # LIVE: the fill is whatever the brokers give, not the quote.
                # exit_pending survives the tick, so a leg the broker refused
                # is retried until it is flat (see the top of this loop).
                s['exit_pending']      = exit_reason
                s['exit_pending_spot'] = spot
                self.log.info(f"[LIVE] {symbol}: {exit_reason} hit at spot {spot} — flattening at the brokers")
                self._finish_live_exit(symbol, s, ltps)
                continue
            self._record_exit(symbol, s, float(exit_price), exit_reason, spot_price=spot)
            self._reset_for_next_scan(s)

        # Roll LAST. If SL or Target hit on the near leg this same tick, that
        # is the strategy's own exit and it wins — the trade is over and there
        # is nothing left to carry forward (those symbols are no longer
        # in_position, hence the guard). The rolled leg's own SL/Target is
        # evaluated on the next tick, 15s later; immaterial to a multi-day swing.
        for symbol, s in inpos.items():
            if s.get('phase') != 'in_position' or not s.get('roll_to_token'):
                continue
            self._roll_position(
                symbol, s,
                near_ltp=ltps.get(symbol),
                far_ltp=ltps.get(f'{symbol}@roll'),
                lots=lots,
                held_listed=not s.get('future_delisted'),
            )

    def _finish_live_exit(self, symbol: str, s: Dict[str, Any], ltps: Dict[str, float]) -> bool:
        """Drive a decided live exit to completion: flatten what is still open,
        and once every leg is out book the trade at the qty-weighted fill (a
        leg whose fill never confirmed is priced at the mark). Returns True
        when the position closed."""
        reason = s.get('exit_pending') or 'SL'
        if not self._execute_live_exit(symbol, s, reason):
            return False
        mark = ltps.get(symbol, s.get('ltp'))
        exit_price = _legs_avg(s.get('broker_legs') or [], 'exit', fallback=mark)
        if exit_price is None:
            exit_price = mark if mark is not None else s.get('entry_price')
        self._record_exit(symbol, s, float(exit_price), reason, spot_price=s.get('exit_pending_spot'))
        self._reset_for_next_scan(s)
        return True

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

            # Fyers and ICICI are both addressed by symbol string; only Kite
            # wants a numeric token. ICICI delegates its symbol-master duties
            # to the Fyers master, so it both returns and expects Fyers-shaped
            # tokens — the old hasattr(provider, 'fyers') test called it Kite
            # and fed Breeze a bare 'SBIN', which resolves to nothing, and sent
            # futures resolution down the Kite path it can't serve.
            from trading_app.service.fyers_data_service import FyersDataServiceAdapter
            from trading_app.service.icici_data_service import IciciDataServiceAdapter
            is_symbol_provider = isinstance(
                provider, (FyersDataServiceAdapter, IciciDataServiceAdapter)
            ) or hasattr(provider, 'fyers')
            state = self._load_state()
            self._save_state(state)

            lots = max(1, int(self._uvar('EMA_CONFLUENCE_LOTS', '1') or 1))
            algo_active = self._uvar('EMA_CONFLUENCE_ACTIVE', 'false').lower() == 'true'
            try:
                self._roll_sessions = max(0, int(
                    self._uvar('EMA_CONFLUENCE_ROLL_DAYS', str(_ROLL_SESSIONS_BEFORE_EXPIRY))
                    or _ROLL_SESSIONS_BEFORE_EXPIRY))
            except ValueError:
                self._roll_sessions = _ROLL_SESSIONS_BEFORE_EXPIRY
            if self._roll_sessions != _ROLL_SESSIONS_BEFORE_EXPIRY:
                self.log.warning(f"Rolling contracts {self._roll_sessions} trading days before "
                                 f"expiry (EMA_CONFLUENCE_ROLL_DAYS override)")

            self._live = self._mode() == 'live'
            live_held = sorted(sym for sym, st in state['stocks'].items() if st.get('broker_legs'))
            if self._live or live_held:
                self._refresh_brokers('thread start', force=True)
            if self._live:
                if self._broker_list:
                    self.log.warning(f"LIVE mode — real NRML futures orders at brokers "
                                     f"{[(b['idx'], b['name'], b['lots']) for b in self._broker_list]}"
                                     + (" (entries gated by EMA_CONFLUENCE_ACTIVE=false)" if not algo_active else ''))
                else:
                    self.log.error("EMA_CONFLUENCE_MODE=live but no zerodha/fyers broker carries "
                                   "BROKER_N_EMA_ACTIVE=true (or none is logged in) — every trigger "
                                   "will be refused until one does")
            else:
                self.log.info("PAPER mode — simulated fills, no broker orders")
                if live_held:
                    self.log.warning(f"EMA_CONFLUENCE_MODE is paper but {live_held} hold LIVE broker "
                                     f"legs — those stay managed at the broker until they exit")

            while not self._stop_event.is_set():
                now = datetime.now()
                now_mins = now.hour * 60 + now.minute
                if now_mins >= _HARD_STOP_MIN:
                    break
                try:
                    self._tick(provider, is_symbol_provider, state, algo_active, lots, now)
                except Exception as e:
                    self.log.error(f"tick error: {e}", exc_info=True)
                self._save_state(state)
                self._stop_event.wait(_POLL_SECS)

            self.log.info("Monitor thread exiting for the day")
        except Exception as e:
            self.log.error(f"monitor loop crashed: {e}", exc_info=True)
