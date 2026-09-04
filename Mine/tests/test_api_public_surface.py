"""Pin the symbols other modules import out of api.py.

All four are *lazy* imports — they sit inside function bodies at their call
sites, so breaking one raises nothing at startup. The failure surfaces the
first time a mine order actually fires, mid-session, with money on the line.
That is exactly why they get an explicit test.

    _get_cached_strike_token       intraday_option/mine_order_monitor.py:87
    _dispatch_order_to_brokers     intraday_option/mine_order_monitor.py:116
    split_quantity_by_freeze_limit intraday_option/intrinsic_order_manager.py:142
    get_data_provider              app/scheduler.py:503,571

When api.py becomes a package, whichever module ends up owning each of these
must be re-exported from `routes/api/__init__.py` so these imports keep
resolving.
"""

import inspect

import pytest

EXPECTED_SIGNATURES = {
    "_get_cached_strike_token": (
        "(kite_service, data_provider, is_fyers: bool, symbol: str, strike: int, "
        "opt_type: str, expiry_type: str = 'nearest')"
    ),
    "_dispatch_order_to_brokers": (
        "(symbol, strike, option_type, action, strategy, username, session_data, "
        "quantity=None, tradingsymbol_override=None, expiry_override=None, "
        "limit_price=None, sec_id=None)"
    ),
    "split_quantity_by_freeze_limit": "(symbol: str, total_qty: int, provider) -> list",
    "get_data_provider": "(user: Optional[str] = None, context: Optional[str] = None) -> Optional[Any]",
}


@pytest.mark.parametrize("name", sorted(EXPECTED_SIGNATURES))
def test_symbol_is_importable_from_api_package(name):
    import trading_app.app.routes.api as api

    assert hasattr(api, name), (
        f"{name} is no longer importable from trading_app.app.routes.api. "
        "Re-export it from routes/api/__init__.py — a consumer imports it lazily "
        "and will only fail mid-session on a live order path."
    )


@pytest.mark.parametrize("name,expected", sorted(EXPECTED_SIGNATURES.items()))
def test_signature_is_unchanged(name, expected):
    import trading_app.app.routes.api as api

    assert str(inspect.signature(getattr(api, name))) == expected


def test_blueprint_is_still_exported():
    from trading_app.app.routes.api import api_bp

    assert api_bp.name == "api"


def test_consumers_still_reference_these_symbols():
    """Guards the other direction: if a consumer stops importing one of these,
    this test's premise is stale and the entry should be dropped, not kept
    forever out of superstition."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "trading_app"
    consumers = {
        "_get_cached_strike_token": "app/intraday_option/mine_order_monitor.py",
        "_dispatch_order_to_brokers": "app/intraday_option/mine_order_monitor.py",
        "split_quantity_by_freeze_limit": "app/intraday_option/intrinsic_order_manager.py",
        "get_data_provider": "app/scheduler.py",
    }
    for symbol, rel in consumers.items():
        text = (src / rel).read_text()
        assert symbol in text, f"{rel} no longer references {symbol}"
