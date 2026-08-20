"""Put `src/` on sys.path for the test suite.

`trading_app` is not pip-installed into the venv, so without this the tests
that don't hand-roll their own `sys.path.insert` fail at collection with
ModuleNotFoundError. Living at the rootdir means it also applies if pytest is
ever pointed somewhere other than `tests/`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


# ── No outbound side effects from the test suite ─────────────────────────
# The algos' exit/entry paths now raise an in-app notification and a Telegram
# alert. Several tests drive those paths with real algo objects (e.g. the roll
# tests calling _tick with an open position), so without this the suite writes
# into the LIVE notification store — which it did: four identical "EMA
# Confluence — SL HIT NHPC" alerts appeared on the dashboard, one per suite run,
# quoting the roll fixture's numbers (entry 77.5, qty 5400) for a position that
# was never actually closed.
#
# Autouse so it protects every test, including ones written later that have no
# idea a helper deep in the call graph notifies. Tests that WANT to assert on
# notifications patch the same names themselves; monkeypatch applies theirs
# after this one, so they still win.
import pytest


@pytest.fixture(autouse=True)
def _block_outbound_side_effects(monkeypatch):
    try:
        import trading_app.service.notification_service as ns
        monkeypatch.setattr(ns, "create_notification", lambda *a, **k: 0)
    except Exception:
        pass
    try:
        import trading_app.service.telegram_service as ts
        monkeypatch.setattr(
            ts.TelegramService, "send_text",
            lambda self, *a, **k: {"success": True, "blocked_in_tests": True})
    except Exception:
        pass
