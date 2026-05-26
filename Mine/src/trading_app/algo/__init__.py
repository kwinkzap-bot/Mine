"""
algo package — registers strategy sub-modules.

Nifty_straddle_0.16_delta contains a dot, making it an invalid Python
identifier. We register the module manually so that the rest of the app
can continue to use:
    from trading_app.algo.nifty_weekly_straddle import NiftyWeeklyStraddle
"""
import importlib.util
import os
import sys

_straddle_path = os.path.join(
    os.path.dirname(__file__),
    "Nifty_straddle_0.16_delta",
    "nifty_weekly_straddle.py",
)

_spec = importlib.util.spec_from_file_location(
    "trading_app.algo.nifty_weekly_straddle",
    _straddle_path,
)
if _spec is not None and _spec.loader is not None:
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["trading_app.algo.nifty_weekly_straddle"] = _mod
    try:
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    except Exception:
        # Remove broken stub so the next import attempt starts clean
        sys.modules.pop("trading_app.algo.nifty_weekly_straddle", None)
        raise
