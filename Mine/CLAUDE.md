# Mine — working notes

Live-money intraday trading app. Flask + APScheduler, eight live algos, four
brokers. Treat every change as touching real orders.

## Hard rules

**Never call `create_app()` outside the real app.** It runs
`init_extensions` → `init_scheduler` (`app/__init__.py:27` →
`extensions.py:83` → `scheduler.py:1186`), which registers 24 cron jobs and
immediately restarts the live algos. A test or REPL that imports it during
market hours places real orders. Tests build a bare `Flask()` instead — see
`tests/route_app.py`.

**No merges, restarts or branch switches during 09:00–15:45 IST.** Check
first:

```bash
TZ=Asia/Kolkata date "+%H:%M"; pgrep -fl main.py
grep -l '"active_trade": {' src/trading_app/algo/*/*state*.json
```

**Untracked runtime files are deleted by branch switches.** `env/Mine.env`,
`.users.json`, `algo/**/*_state*.json` and `app/utils/{mine_orders,bot_orders,
rtp_opt_cache,sm_opt_cache}.json` are gitignored but load-bearing. They were
tracked until 2026-08-18; any `git checkout` to a commit at or before
`prerefactor-2026-08-18` re-materialises them, and switching back **deletes
the working-tree copy**. This bit once already.

Back them up before any branch work, and restore with:

```bash
for f in env/Mine.env .users.json; do git show prerefactor-2026-08-18:Mine/$f > $f; done
```

`env/Mine.env` holds the broker credentials — losing it means the app cannot
place orders on its next restart.

## Running the server

The app is not started by hand — the **`com.mine.livealgo` LaunchAgent** owns
it (`~/Library/LaunchAgents/com.mine.livealgo.plist`), starting it at login and
respawning it within ~15s if it dies. `start_live.sh` runs the same command in
the foreground and is only for driving it manually; running both at once
fights over port 5000.

```bash
launchctl kickstart -k gui/$(id -u)/com.mine.livealgo   # restart
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mine.livealgo.plist
launchctl bootout gui/$(id -u)/com.mine.livealgo        # stop
```

`launchctl stop` does **not** stop it: `KeepAlive` is true, so it comes back a
few seconds later. Booting it out is the real stop, and `bootstrap` puts it
back.

A restart takes ~8s to serve again and re-runs `init_scheduler`, so it
re-registers the 24 cron jobs and **restarts the live algos** — the same reason
`create_app()` is dangerous. Verify afterwards:

```bash
launchctl list | grep com.mine.livealgo    # PID, and last exit status
lsof -nP -iTCP:5000 -sTCP:LISTEN           # actually listening
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/   # 302 = healthy
tail -n 30 logs/launchd_live.log
```

`302` is the normal answer at `/` — it redirects to `/auth/user-login`.

## Tests

```bash
PYTHONPATH=src ../.venv/bin/python -m pytest -q
```

`trading_app` is not pip-installed; the rootdir `conftest.py` puts `src/` on
the path.

`tests/route_inventory.txt` is the golden URL surface — 213 rules with their
endpoints, methods and `strict_slashes`. **If a refactor commit's `git diff`
touches it, the public API moved.** Regenerate only deliberately, with
`python tests/regenerate_route_inventory.py`, in its own commit.

## Known issues

**RTP live logic has diverged from its backtest engine since 2026-07-09**
(commit `91d8c45`). `tests/test_rtp_live_vs_backtest.py` is
`xfail(strict=True)` with the bisect in its marker. Backtest-derived
parameters do not describe live behaviour. Deciding which side is correct is
a strategy call, not a refactor.

Related but separate and **deliberate**: the backtest fills at the next bar's
open, the live algo at the signal bar's close. Documented on both sides. Do
not unify them while deduping — it changes live fill prices.
