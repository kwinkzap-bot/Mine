"""Big-print alerts — the bell and the Telegram message behind a blue bar.

A Round Strike volume bar paints blue when a single Time & Sales print at or
above a threshold landed in it. This module rings for that print AT THE MOMENT
IT ENTERS THE TAPE (time_and_sales._note_row calls `observe` for every new
row), which is the only place the event actually happens once.

WHY HERE AND NOT ON THE CHART'S POLL
------------------------------------
The alert used to ride on /api/oi-profile/round-strike: every 1-second poll
re-tagged the bars and rang for any tag that was still "fresh". Measured on
2026-09-15 that missed 5 of the day's 22 prints and rang 3 times for one:

  * the top-up can lay a print down later than the poll's freshness window,
    so a slow Breeze pass meant no alert at all (15:29:41, 13,455 contracts);
  * the poll rang once PER BAR, so a second, bigger print in the same minute
    was silent (14:48:50, 15,145 after 9,165 had already rung);
  * the dedup lived in the process, so a restart re-rang the last bar
    (15:01, three times across three restarts);
  * nothing rang while the page was closed, because nothing polled.

The store sees each print exactly once, whichever path brought it in (socket
tick, opening backfill, or a top-up's Σ bar), so ringing from there fixes the
first three outright. The fourth is the standing watch in time_and_sales
(RS_BIG_PRINT_WATCH), which keeps the front future taped all session so the
store fills whether or not a tab is open.

WHAT RINGS
----------
  qty >= threshold, print no older than FRESH_SEC, not rung before today.

The freshness cap is what stops a tab opened at 14:20 — whose backfill lays
down the whole morning in one go — from sending twenty messages about prints
hours old. Dedup is by (contract, exchange second): a live tick and the Σ bar
that later replaces it are the same trade(s) seen twice. The rung set is kept
in a small JSON ledger beside the tape so a restart cannot ring twice.

THRESHOLD
---------
  RS_BIG_PRINT_QTY in the user's env file, when set, is the figure and the
  chart's box does not move it. Unset, the alert follows the size the LIVE
  Round Strike chart last asked for (the box in its Indicators popup, sent
  as `big_qty` and pushed here by the route) — "what rings is what the chart
  shows" — remembered in the ledger across restarts, 8,000 until a chart has
  ever said otherwise.

FLAGS (all read from the monitoring user's env file, default on)
  RS_BIG_PRINT_NOTIFY    in-app notification
  RS_BIG_PRINT_TELEGRAM  Telegram message (needs TELEGRAM_BOT_TOKEN/CHAT_ID)

Nothing here can reach an order path: the module imports the notification and
Telegram services lazily and nothing else from the app.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from time import monotonic, time as _now
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_QTY = 8000
# A print older than this when it reaches the store is history, not news.
# The top-up normally lands a second within ~65s of it trading; ten minutes
# leaves room for a Breeze pass that had to catch up first.
FRESH_SEC = 600
# Env reads are cached this long, so a flag flipped in the file takes effect
# without a restart but a top-up laying down 200 rows does not read it 200x.
_SETTINGS_TTL = 30.0

_LEDGER_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'tape', 'big_print_alerts.json')

_FUT_ROOT_RE = re.compile(r'^(?:NSE|BSE):([A-Z&-]+?)\d{2}[A-Z]{3}FUT$')

_lock = threading.Lock()
_day: Optional[str] = None
_rung: Set[Tuple[str, int]] = set()
_chart_qty: Optional[int] = None          # what the live chart last asked for
_loaded = False
_settings: Dict[str, Any] = {}
_settings_at = 0.0


# ── settings ────────────────────────────────────────────────────────────────

def _uvar(key: str, default: str = '') -> str:
    """One value from the monitoring user's env file — the same file the
    scheduler's jobs and the OI Crossover scanner read their flags from."""
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        user = os.getenv('MONITORING_USERNAME', 'Mine')
        return (UserEnvManager.get_user_var(user, key, default) or '').strip()
    except Exception:
        return default


def flag(key: str) -> bool:
    """An on/off env flag, on unless it reads exactly 'false'."""
    return _uvar(key, 'true').lower() != 'false'


def settings() -> Dict[str, Any]:
    """The threshold and the two channel flags, re-read every _SETTINGS_TTL."""
    global _settings, _settings_at
    now = monotonic()
    if _settings and now - _settings_at < _SETTINGS_TTL:
        return _settings
    env_qty = _parse_qty(_uvar('RS_BIG_PRINT_QTY'))
    with _lock:
        _load_ledger_locked()
        chart_qty = _chart_qty
    _settings = {
        'qty': env_qty or chart_qty or DEFAULT_QTY,
        'pinned': env_qty is not None,      # env wins over the chart's box
        'notify': flag('RS_BIG_PRINT_NOTIFY'),
        'telegram': flag('RS_BIG_PRINT_TELEGRAM'),
        'token': _uvar('TELEGRAM_BOT_TOKEN'),
        'chat_id': _uvar('TELEGRAM_CHAT_ID'),
    }
    _settings_at = now
    return _settings


def _parse_qty(raw: Any) -> Optional[int]:
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def threshold() -> int:
    return int(settings()['qty'])


def note_chart_threshold(qty: Any) -> None:
    """The live chart's box, as sent with its poll. Remembered in the ledger
    so the figure survives a restart; ignored while RS_BIG_PRINT_QTY pins it."""
    global _chart_qty, _settings_at
    n = _parse_qty(qty)
    if n is None:
        return
    with _lock:
        _load_ledger_locked()
        if _chart_qty == n:
            return
        _chart_qty = n
        _save_ledger_locked()
    _settings_at = 0.0                      # re-read on the next observe


# ── ledger ──────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(IST).date().isoformat()


def _load_ledger_locked() -> None:
    """Restore today's rung set (and the chart's figure) once per process."""
    global _loaded, _day, _rung, _chart_qty
    if _loaded:
        return
    _loaded = True
    try:
        with open(_LEDGER_PATH) as fh:
            blob = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as e:
        logger.debug(f"[BigPrint] ledger read failed: {e}")
        return
    _chart_qty = _parse_qty(blob.get('chart_qty'))
    if blob.get('day') == _today():
        _day = blob['day']
        _rung = {(str(s), int(t)) for s, t in (blob.get('rung') or [])}


def _save_ledger_locked() -> None:
    try:
        os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
        tmp = f"{_LEDGER_PATH}.tmp"
        with open(tmp, 'w') as fh:
            json.dump({'day': _day or _today(), 'chart_qty': _chart_qty,
                       'rung': sorted(_rung)}, fh)
        os.replace(tmp, _LEDGER_PATH)
    except Exception as e:
        logger.debug(f"[BigPrint] ledger write failed: {e}")


def _roll_day_locked() -> None:
    global _day, _rung
    today = _today()
    if _day != today:
        _day = today
        _rung = set()


# ── the hook ────────────────────────────────────────────────────────────────

def observe(symbol: str, row: Dict[str, Any], now_ts: Optional[int] = None) -> bool:
    """One NEW tape row. Rings if it qualifies; returns whether it did.

    Called under the tape's lock for every row, so the common path is one
    integer compare. Never raises — an alert that fails must not cost the
    tape a print.
    """
    try:
        qty = int(row.get('qty') or 0)
        if qty <= 0:
            return False
        cfg = settings()
        if qty < cfg['qty'] or not (cfg['notify'] or cfg['telegram']):
            return False
        ts = int(row['ts'])
        now_ts = int(now_ts if now_ts is not None else _now())
        if now_ts - ts > FRESH_SEC:
            return False
        key = (symbol, ts)
        with _lock:
            _load_ledger_locked()
            _roll_day_locked()
            if key in _rung:
                return False
            _rung.add(key)
            _save_ledger_locked()
        _spawn(_send, (_payload(symbol, row, cfg['qty']), cfg))
        return True
    except Exception as e:
        logger.error(f"[BigPrint] alert failed: {e}")
        return False


def _spawn(target, args) -> None:
    """The send runs on its own daemon thread. One seam, so a test can make
    it synchronous without touching threading.Thread for the whole process."""
    threading.Thread(target=target, args=args, daemon=True, name='BigPrintAlert').start()


def _payload(symbol: str, row: Dict[str, Any], min_qty: int) -> Dict[str, Any]:
    m = _FUT_ROOT_RE.match(symbol or '')
    root = m.group(1) if m else (symbol or '')
    side = str(row.get('side') or '').upper()
    return {
        'symbol': root,
        'contract': symbol,
        'print_time': datetime.fromtimestamp(int(row['ts']), IST).strftime('%H:%M:%S'),
        'qty': int(row.get('qty') or 0),
        'price': float(row.get('price') or 0),
        'side': side if side in ('BUY', 'SELL') else '',
        'source': row.get('src'),
        'threshold': int(min_qty),
    }


def _send(p: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    """Off the tape's thread: the bell is a SQLite write and Telegram allows a
    10s HTTP timeout."""
    side_txt = f" {p['side']}" if p['side'] else ''
    what = '1s bar' if p['source'] == 'bar' else 'print'
    title = f"Big print — {p['symbol']} {p['qty']:,} @ ₹{p['price']:,.2f}{side_txt}"
    summary = f"{p['print_time']} {what} · threshold {p['threshold']:,} · {p['contract']}"

    if cfg['notify']:
        try:
            from trading_app.service.notification_service import create_notification
            create_notification(category='rs_big_print', title=title, summary=summary, data=p)
        except Exception as e:
            logger.error(f"[BigPrint] in-app alert failed: {e}")

    if not (cfg['telegram'] and cfg['token'] and cfg['chat_id']):
        return
    message = '\n'.join([
        f"🔵 Big print — {p['symbol']} FUT",
        f"{p['qty']:,} contracts @ ₹{p['price']:,.2f}{side_txt} at {p['print_time']}",
        f"{what} · threshold {p['threshold']:,}",
        p['contract'],
    ])
    try:
        from trading_app.service.telegram_service import TelegramService
        result = TelegramService(token=cfg['token'], chat_id=cfg['chat_id']).send_text(message)
        if not result.get('success'):
            logger.error(f"[BigPrint] Telegram alert failed: {result.get('error')}")
    except Exception as e:
        logger.error(f"[BigPrint] Telegram alert failed: {e}")


def reset() -> None:
    """Forget everything, including the ledger's in-memory copy. Tests."""
    global _day, _rung, _chart_qty, _loaded, _settings, _settings_at
    with _lock:
        _day = None
        _rung = set()
        _chart_qty = None
        _loaded = False
    _settings = {}
    _settings_at = 0.0
