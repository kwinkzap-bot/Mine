"""Rewrite tests/route_inventory.txt from the current app.

Run this ONLY when the URL surface changed on purpose, and commit the result
on its own — never in the same commit as a route move. A move commit that also
regenerates the golden file proves nothing.

    python tests/regenerate_route_inventory.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from route_app import build_route_app, render_inventory  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(__file__), "route_inventory.txt")

if __name__ == "__main__":
    text = render_inventory(build_route_app())
    with open(GOLDEN, "w") as fh:
        fh.write(text)
    print(f"wrote {GOLDEN} ({len(text.splitlines())} rules)")
