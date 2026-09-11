# Mine — working notes

Live-money intraday trading app. Flask + APScheduler, eight live algos, four
brokers. Treat every change as touching real orders.

## Hard rules

**Never call `create_app()` outside the real app.** It runs
`init_extensions` → `init_scheduler` (`app/__init__.py:27` →
`extensions.py:83` → `scheduler.py:1186`), which registers 26 cron jobs and
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
re-registers the 26 cron jobs and **restarts the live algos** — the same reason
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

## Order Placement signal mode

`/orderplacement` has two modes. **Single** places one order at every broker
carrying `BROKER_N_OP_ACTIVE=true` and walks away, as it always did.
**Signal** takes a pasted tip and manages the whole trade:

```
NIFTY 23500 CE          entry  SL-M BUY, 3x lots, trigger 193
BUY : 193               on fill  SL-M SELL 1 lot @175 + LIMIT 1 lot @206
SL : 175                                            + LIMIT 1 lot @213
Target : 206,213,220    T1 fills  stop trigger -> the actual entry fill
                        T2 fills  stop trigger -> T1's actual fill
                        SL fills  cancel the targets, market-exit the rest
                        220 touched  cancel the stop, exit at market
```

**The stop is one leg's worth, not the whole position**, and **target 3 has no
order at all** — its lot is the one the stop is holding. Three lots held means
three resting sell orders of one lot each, so working sell quantity equals the
position exactly. Two consequences:

* No short-option margin is ever asked for, and the race that makes emulated
  OCO ladders unsafe — a stop covering more than is held, briefly — cannot
  arise, because the stop never covers more than its own lot.
* A stop hit is not the whole exit. The stop sells its lot at the exchange;
  the engine cancels the targets and sells the rest at market a tick later.
  T3 likewise needs the app alive. The stop does not.

The stop is allocated **before** the targets, so a short fill loses a target
rather than its protection.

Sizing is `BROKER_N_OP_SIGNAL_LOTS` — **lots per target leg**, so `=1` is a
three-lot entry. There is no fallback to `BROKER_N_OP_LOTS`: that number sizes
a whole single-mode order, and reading it as a per-target size would treble the
position. A broker without the variable takes no part in a signal.

Any leg over the 27-lot exchange freeze limit is **split** by `lot_chunks` —
a 30-lot entry is 27 + 3, two orders at one account on one store record.
Neither shared dispatcher splits by that cap (`split_quantity_by_freeze_limit`
is only reached on the way out, by `_place_exit_leg`), so the engine does it.
Two consequences that are easy to break:

* `leg_fills` **sums** a broker's chunks and calls them finished only when
  every chunk is terminal. Overwriting instead arms a ladder over 27 lots and
  leaves the other 3 filling behind it, managed by nothing.
* `_trail_stop` sends the trigger and **never a quantity**. A stop over the cap
  is several orders; handing `_modify_order_at_brokers` a quantity would put
  the whole figure on every chunk.

Only Zerodha and Fyers can hold an SL-M (`dispatch_stop_to_brokers`), so a
signal **refuses to arm at all** if any OP-enabled broker is Dhan or Kotak —
a ladder whose stop cannot be placed is worse than no ladder.

The engine (`app/order_placement/op_signal_engine.py`) is a daemon thread on a
3s tick, started by the arm route and resurrected by `op_signal_watchdog`. It
stands itself down when the last signal closes. Its state is
`app/order_placement/op_signals.json` — gitignored, and load-bearing in exactly
the way the other runtime files are.

Two things in it are load-bearing and easy to break:

* **The stop's quantity is never modified** — only its trigger. It was placed
  at one leg's worth and stays there, which is what keeps working sell
  quantity equal to the position through every target fill.
* **`_reconcile_orphans` every 30s** flattens any account left on the wrong
  side of the contract. Nothing should be able to get there now, which is
  exactly why it stays: it is the check that the invariant above still holds.

Every leg is a normal `MineOrderStore` record with `strategy='op'` plus
`signal_id` and `leg`, which is what keeps the price box, the ✕, the
reconciliation sweep and Exit all working on signal legs with no special case.
Do not "tidy" that into a store of its own.

## Known issues

**RTP live logic has diverged from its backtest engine since 2026-07-09**
(commit `91d8c45`). `tests/test_rtp_live_vs_backtest.py` is
`xfail(strict=True)` with the bisect in its marker. Backtest-derived
parameters do not describe live behaviour. Deciding which side is correct is
a strategy call, not a refactor.

Related but separate and **deliberate**: the backtest fills at the next bar's
open, the live algo at the signal bar's close. Documented on both sides. Do
not unify them while deduping — it changes live fill prices.
