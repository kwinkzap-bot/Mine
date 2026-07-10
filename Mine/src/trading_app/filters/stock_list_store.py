"""
Local file-backed store for NSE stock lists.

Fetches the symbol master from the data provider (Fyers/Kite) ONCE and saves the
plain symbol list to a local JSON file, so scanners reuse the saved list instead
of re-downloading the symbol master on every run.

Files (kept beside the historic-candle disk cache in trading_app/.cache/):
  - nse_equity_stocks.json : all NSE equity-series stocks (EMA Narrow scanner)
  - nse_fo_stocks.json     : F&O stocks (names with futures, minus indexes)

Lists refresh automatically once the file is older than STOCK_LIST_MAX_AGE_DAYS.
If the provider fetch fails, a stale file is still used as fallback.
"""
import json
import logging
import os
import threading
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
EQUITY_LIST_FILE = os.path.join(_CACHE_DIR, "nse_equity_stocks.json")
FO_LIST_FILE     = os.path.join(_CACHE_DIR, "nse_fo_stocks.json")

STOCK_LIST_MAX_AGE_DAYS = 3

# Bump when the extraction rules change so already-saved lists regenerate
STOCK_LIST_VERSION = 3

_store_lock = threading.Lock()

INDEX_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "NIFTY MIDCAP 150", "NIFTY AUTO", "NIFTY Smallcap 100", "NIFTY SMLCAP 100",
    "NIFTY FMCG", "NIFTY METAL", "NIFTY PHARAMA", "NIFTY PHARMA",
    "NIFTY PSU BANK", "NIFTY IT",
}


# ── File helpers ──────────────────────────────────────────────────────────────

def _read_file(path: str) -> Optional[dict]:
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data.get("symbols"), list) or not data["symbols"]:
            return None
        if data.get("version") != STOCK_LIST_VERSION:
            logger.info(f"Stock list {os.path.basename(path)} is an old version — regenerating")
            return None
        return data
    except Exception as e:
        logger.warning(f"Stock list read failed ({path}): {e}")
        return None


def _is_fresh(data: dict) -> bool:
    try:
        generated = datetime.strptime(data["generated_at"], "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - generated).days < STOCK_LIST_MAX_AGE_DAYS
    except Exception:
        return False


def _write_file(path: str, symbols: List[str], source: str):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version":      STOCK_LIST_VERSION,
            "source":       source,
            "count":        len(symbols),
            "symbols":      symbols,
        }
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=1)
        os.replace(tmp, path)
        logger.info(f"Stock list saved: {os.path.basename(path)} ({len(symbols)} symbols)")
    except Exception as e:
        logger.warning(f"Stock list save failed ({path}): {e}")


# ── Provider fetchers ─────────────────────────────────────────────────────────

# Substrings (in tradingsymbol or instrument name) that mark fund instruments:
# ETFs (NIFTYBEES, SILVERETF, MON100...), liquid funds (LIQUIDCASE, LIQUIDBEES,
# ABSLLIQUID...), mutual-fund schemes and fund-of-funds.
_FUND_KEYWORDS = ("BEES", "ETF", "LIQUID", "MUTUAL FUND", "EXCHANGE TRADED FUND", "FOF")


def _is_fund(symbol: str, name: str) -> bool:
    """True for non-company fund instruments: ETFs, liquid funds, mutual funds."""
    s = symbol.upper()
    n = (name or "").upper()
    return any(kw in s or kw in n for kw in _FUND_KEYWORDS)


def _fetch_equity_symbols(kite) -> List[str]:
    """All NSE equity-series stocks as plain symbols (e.g. 'RELIANCE').
    Excludes fund instruments (ETFs, liquid funds, mutual funds) — the scanners
    should only see operating companies."""
    instruments = kite.instruments("NSE")
    symbols = set()
    for inst in instruments:
        if inst.get("instrument_type") != "EQ":
            continue
        ts = (inst.get("tradingsymbol") or "").strip()
        if not ts:
            continue
        if ts.endswith("-EQ"):          # Fyers style: RELIANCE-EQ
            ts = ts[:-3]
        elif "-" in ts:                 # skip BE/BZ/SM etc. series
            continue
        if ts in INDEX_SYMBOLS or " " in ts:
            continue
        if _is_fund(ts, inst.get("name") or ""):
            continue
        symbols.add(ts)
    return sorted(symbols)


def _fetch_fo_symbols(kite) -> List[str]:
    """F&O stock names (instruments with futures), excluding indexes."""
    nfo = kite.instruments("NFO")
    fo_set = {
        inst["name"]
        for inst in nfo
        if inst.get("instrument_type") == "FUT"
        and inst.get("name")
        and inst["name"] not in INDEX_SYMBOLS
    }
    return sorted(fo_set)


# ── Public API ────────────────────────────────────────────────────────────────

def _get_list(kite, path: str, fetcher, label: str, force_refresh: bool) -> List[str]:
    with _store_lock:
        data = _read_file(path)
        if data and not force_refresh and _is_fresh(data):
            logger.info(f"{label} list loaded from file ({data['count']} symbols, "
                        f"generated {data['generated_at']})")
            return data["symbols"]

        try:
            symbols = fetcher(kite)
        except Exception as e:
            logger.error(f"{label} list fetch failed: {e}")
            symbols = []

        if symbols:
            _write_file(path, symbols, kite.__class__.__name__)
            return symbols

        if data:  # provider failed — fall back to stale file
            logger.warning(f"{label} list fetch empty/failed — using stale file "
                           f"from {data['generated_at']}")
            return data["symbols"]
        return []


def get_equity_stocks(kite, force_refresh: bool = False) -> List[str]:
    """All NSE equity stocks, file-cached (refreshed every {MAX_AGE} days)."""
    return _get_list(kite, EQUITY_LIST_FILE, _fetch_equity_symbols, "Equity", force_refresh)


def get_fo_stocks(kite, force_refresh: bool = False) -> List[str]:
    """F&O stocks, file-cached (refreshed every {MAX_AGE} days)."""
    return _get_list(kite, FO_LIST_FILE, _fetch_fo_symbols, "F&O", force_refresh)
