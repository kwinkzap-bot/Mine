"""The contract for the api.py refactor: the URL surface must not move.

Every route move is verified by this one test. If a refactor commit's
`git diff` touches `tests/route_inventory.txt`, the public surface changed and
the commit needs an explanation before it merges.

Deliberately NOT asserted: `csrf._exempt_views` and `limiter._route_exemptions`.
Both are keyed by dotted module path, so moving a handler to a new module
churns every key. They are also inert (`config.py` disables CSRF and rate
limiting, `extensions.py` disables them again), so asserting on them would
produce noise that trains you to ignore this file.
"""

import difflib
import os

from route_app import build_route_app, render_inventory

GOLDEN = os.path.join(os.path.dirname(__file__), "route_inventory.txt")


def test_url_map_matches_golden():
    actual = render_inventory(build_route_app())
    with open(GOLDEN) as fh:
        expected = fh.read()

    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                fromfile="route_inventory.txt (golden)", tofile="current app", lineterm="",
            )
        )
        raise AssertionError(
            "The URL surface changed. If this was intentional, regenerate with\n"
            "  python tests/regenerate_route_inventory.py\n"
            "in its own commit, separate from any code move.\n\n" + diff
        )


def test_blueprint_before_request_hook_is_the_auth_gate():
    app = build_route_app()
    names = [f.__name__ for f in app.before_request_funcs.get("api", [])]
    assert names == ["check_user_authentication"]


def test_app_level_before_request_hook_survives():
    """`_ensure_mine_monitor` is an app-level hook buried in api.py's orders
    block. Splitting that block without carrying it over drops the mine-order
    monitor silently — nothing raises, orders just stop being monitored."""
    app = build_route_app()
    names = [f.__name__ for f in app.before_request_funcs.get(None, [])]
    assert names == ["_ensure_mine_monitor"]


def test_blueprint_error_handlers_survive():
    """The 404 handler is unreachable (routing 404s aren't attributed to a
    blueprint) but the 500 handler is live — it shapes the error body for
    unhandled exceptions on all 167 api routes. Don't delete it as dead code."""
    app = build_route_app()
    spec = app.error_handler_spec.get("api", {})
    assert set(spec) == {404, 500}
    assert {e.__name__: h.__name__ for e, h in spec[404].items()} == {"NotFound": "not_found"}
    assert {e.__name__: h.__name__ for e, h in spec[500].items()} == {
        "InternalServerError": "server_error"
    }


def test_route_counts_per_blueprint():
    """A coarse tripwire that reads better than a 213-line diff when a whole
    blueprint fails to register."""
    app = build_route_app()
    counts = {}
    for rule in app.url_map.iter_rules():
        prefix = rule.endpoint.split(".")[0]
        counts[prefix] = counts.get(prefix, 0) + 1
    assert counts == {"api": 172, "pages": 24, "auth": 16, "oi_crossover": 10,
                      "order_placement": 6, "watchlist": 16, "static": 1}


def test_icici_callback_bounces_a_cross_site_post_to_a_get():
    """ICICI posts its login callback cross-site, and a SameSite=Lax cookie is
    never sent on a cross-site POST — so that request has no session and the
    page it renders shows the user logged out. The POST must therefore answer
    with a redirect to a same-site GET, which does carry the cookie. Rendering
    the result directly from the POST is the bug this guards against."""
    app = build_route_app()
    client = app.test_client()

    resp = client.post("/auth/callback/icici")          # no API_Session
    assert resp.status_code == 303
    # Either registered alias of icici_callback is fine — what matters is that
    # it is a GET of that view carrying the outcome.
    location = resp.headers["Location"]
    assert "icici" in location and location.endswith("?status=no-session")

    # And the GET half renders the page itself rather than bouncing again.
    resp = client.get("/auth/callback/icici?status=ok")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "ICICI Direct connected" in body
    assert "&#39;" not in body          # Jinja must not escape quotes into the JS
