"""Put `src/` on sys.path for the test suite.

`trading_app` is not pip-installed into the venv, so without this the tests
that don't hand-roll their own `sys.path.insert` fail at collection with
ModuleNotFoundError. Living at the rootdir means it also applies if pytest is
ever pointed somewhere other than `tests/`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
