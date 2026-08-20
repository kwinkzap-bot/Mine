"""Build the app's URL map without starting anything.

`create_app()` is off limits here: it calls `init_extensions` ->
`init_scheduler`, which registers 24 cron jobs and immediately restarts the
eight live algos (`app/__init__.py:27` -> `extensions.py:83` ->
`scheduler.py:1186`). Importing this module during market hours would place
real orders.

A bare `Flask()` with the four blueprints registered at their real prefixes
produces an identical URL map, plus the `before_request` hooks and blueprint
error handlers, and starts no threads. It mirrors
`trading_app/app/routes/__init__.py:register_blueprints` — keep the two in
sync.
"""

from flask import Flask


def build_route_app():
    """A Flask app carrying the real URL map and nothing else."""
    from trading_app.app.routes.api import api_bp
    from trading_app.app.routes.auth import auth_bp
    from trading_app.app.routes.oi_crossover_api import oi_crossover_bp
    from trading_app.app.routes.pages import pages_bp
    from trading_app.app.routes.watchlist_api import watchlist_bp

    app = Flask(__name__)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(oi_crossover_bp, url_prefix="/api/oi-crossover")
    app.register_blueprint(watchlist_bp, url_prefix="/api/watchlist")
    return app


def render_inventory(app):
    """One sorted line per URL rule.

    `strict_slashes` is part of the contract, not decoration: 22 rules rely on
    it being False, and `str(rule)` alone does not capture it.
    """
    lines = []
    for rule in app.url_map.iter_rules():
        methods = ",".join(sorted(rule.methods))
        lines.append(f"{rule.rule}\t{rule.endpoint}\t{methods}\t{rule.strict_slashes}")
    return "\n".join(sorted(lines)) + "\n"
