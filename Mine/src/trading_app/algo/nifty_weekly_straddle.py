"""
Shim: exposes NiftyWeeklyStraddle from the Nifty_straddle_0.16_delta/ subdirectory.

The directory name contains a dot which makes it an invalid Python identifier,
so it cannot be imported with a regular `import` statement. This file loads the
implementation by absolute path at import time, giving Pylance a real module to
resolve while keeping the runtime behaviour identical.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

_IMPL_PATH = os.path.join(
    os.path.dirname(__file__),
    "Nifty_straddle_0.16_delta",
    "nifty_weekly_straddle.py",
)

_spec = importlib.util.spec_from_file_location(
    "trading_app.algo._nifty_straddle_impl", _IMPL_PATH
)
if _spec is not None and _spec.loader is not None:
    _impl_mod = importlib.util.module_from_spec(_spec)
    sys.modules["trading_app.algo._nifty_straddle_impl"] = _impl_mod
    _spec.loader.exec_module(_impl_mod)  # type: ignore[union-attr]
    NiftyWeeklyStraddle: Any = _impl_mod.NiftyWeeklyStraddle
else:
    raise ImportError(f"Could not load NiftyWeeklyStraddle from {_IMPL_PATH}")
