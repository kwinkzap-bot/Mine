"""Guard against the CSS patterns that force horizontal PAGE scroll on a phone.

Target viewport is 375px (iPhone SE / mini). Two patterns caused every real
overflow found in the 2026-08-18 responsive audit:

1. `repeat(auto-fit, minmax(Npx, 1fr))` with N > 375. `auto-fit` cannot shrink a
   track below its stated minimum, so the grid is at least N wide no matter how
   narrow the viewport. Fix: `minmax(min(Npx, 100%), 1fr)`.

2. A hard `width: Npx` with N > 375 on a layout box. Fix: `max-width` plus
   `width: 100%`.

These are cheap textual checks, not a rendering test — the browser sweep is what
actually proves a page is fluid. Their job is to stop a fixed value being
reintroduced later without anyone noticing.
"""

import pathlib
import re

import pytest

CSS_DIR = pathlib.Path(__file__).resolve().parents[1] / "static" / "css"
PHONE_WIDTH = 375

# Values that are deliberately wider than the phone viewport. Keep this list
# short and justified — every entry is a place the guard is switched off.
EXEMPT_WIDTH = {
    # `.container` is a max-width cap on an otherwise fluid shell, and the
    # regex below already skips `max-width`. Nothing else is exempt today.
}


def _css_files():
    return sorted(CSS_DIR.rglob("*.css"))


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _iter_lines(path):
    """Yield (lineno, line) with block comments blanked out.

    Comments are stripped so the explanatory notes in responsive.css — which
    quote the very patterns being banned — don't trip the guard.
    """
    text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    for i, line in enumerate(text.splitlines(), start=1):
        yield i, line


@pytest.mark.parametrize("path", _css_files(), ids=lambda p: p.name)
def test_no_unguarded_autofit_minimum(path):
    """minmax(Npx, ...) with N > 375 and no min() guard overflows a phone."""
    offenders = []
    for lineno, line in _iter_lines(path):
        for m in re.finditer(r"minmax\(\s*(\d+)px", line):
            if int(m.group(1)) > PHONE_WIDTH:
                offenders.append(f"  {path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        f"auto-fit/auto-fill minimum wider than a {PHONE_WIDTH}px viewport.\n"
        "The track cannot shrink below this, so the page scrolls sideways.\n"
        "Fix: minmax(min(Npx, 100%), 1fr)\n" + "\n".join(offenders)
    )


# `width:` but never `max-width:` / `min-width:`.
_WIDTH_RE = re.compile(r"(?<![-\w])width:\s*(\d+)px")
# A max-width in any unit that can shrink below the hard width.
_RELATIVE_MAX_RE = re.compile(r"max-width:\s*[^;}]*(?:vw|%|calc|min\()")


@pytest.mark.parametrize("path", _css_files(), ids=lambda p: p.name)
def test_no_hard_layout_width_wider_than_phone(path):
    """A `width: Npx` over 375px overflows — UNLESS the same rule caps it.

    Block-aware on purpose. The app's modals are written as
    `width: 460px; max-width: 94vw;`, which is correct and must not be
    flagged: the hard width is the desktop size and the relative max-width
    is what actually shrinks it. A line-based check calls all of those
    offenders and trains you to ignore the test.
    """
    text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))

    # Map each rule block back to the line its body starts on, so failures
    # stay clickable.
    offenders = []
    for block in re.finditer(r"\{([^{}]*)\}", text):
        body = block.group(1)
        if _RELATIVE_MAX_RE.search(body):
            continue
        for m in _WIDTH_RE.finditer(body):
            value = int(m.group(1))
            if value > PHONE_WIDTH and value not in EXEMPT_WIDTH:
                lineno = text[: block.start() + 1 + m.start()].count("\n") + 1
                offenders.append(f"  {path.name}:{lineno}: width: {value}px")

    assert not offenders, (
        f"Hard width wider than a {PHONE_WIDTH}px viewport with no relative "
        "max-width in the same rule.\n"
        "Fix: add `max-width: 100%` (or a vw/calc cap), or use "
        "`max-width: Npx; width: 100%;`.\n" + "\n".join(offenders)
    )


def test_canonical_breakpoints_only():
    """Keep the breakpoint set from drifting back to ten ad-hoc values.

    Pre-existing values are grandfathered so this lands green; the set must not
    GROW. Shrink it as pages are migrated, and delete entries from LEGACY as
    they go.
    """
    canonical = {480, 768, 1024, 1280}
    legacy = {400, 600, 640, 720, 860, 900, 1100}

    found = {}
    for path in _css_files():
        for lineno, line in _iter_lines(path):
            if "@media" not in line:
                continue
            for m in re.finditer(r"(?:max|min)-width:\s*(\d+)px", line):
                found.setdefault(int(m.group(1)), []).append(f"{path.name}:{lineno}")

    unexpected = {v: locs for v, locs in found.items() if v not in canonical | legacy}
    assert not unexpected, (
        "New non-canonical breakpoint(s). Use 480 / 768 / 1024 / 1280 "
        "(see static/css/responsive.css):\n"
        + "\n".join(f"  {v}px at {', '.join(locs)}" for v, locs in sorted(unexpected.items()))
    )
