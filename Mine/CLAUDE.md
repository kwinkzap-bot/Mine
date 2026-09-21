# Mine — working notes

Live-money intraday trading app. Flask + APScheduler, three live algo threads
(2nd 30s Candle — **paper only since 2026-09-15**, it has no order path —
30-Min Fakeout — live by default, **paper under `TMF_MODE=paper`**, the
Live/Paper toggle on its Algo tab, re-read at every entry so no restart, a
paper leg has `broker_idx=0` and never reaches a broker while a leg already
at the broker is managed there until flat — and EMA Confluence — paper by
default, **live under `EMA_CONFLUENCE_MODE=live`**, see below) plus the Order Placement signal
engine, four brokers. Treat every change as touching real orders.

## Hard rules

**Never call `create_app()` outside the real app.** It runs
`init_extensions` → `init_scheduler` (`app/__init__.py:27` →
`extensions.py:96` → `scheduler.py:978`), which registers 17 cron jobs and
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
re-registers the 17 cron jobs and **restarts the live algos** — the same reason
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

## Order Placement

`/orderplacement` places **one order** at every broker carrying
`BROKER_N_OP_ACTIVE=true`, sized by `BROKER_N_OP_LOTS`, and walks away —
MARKET, LIMIT or SL-M. Every record is `strategy='op'`, which is what the
strip's price box, ✕, reconciliation sweep and Exit all key on.

The hand-armed **Signal mode** (a pasted tip → 3-lot SL/T1/T2 ladder) was
**removed on 2026-09-15** with its `/signal`, `/signal/parse`, `/signals`,
`/signals/<id>/cancel` routes, the `op_signal_start` / `op_signal_watchdog`
jobs and their tests. What remains of it is deliberately still there:
`op_signal_engine.py` is now only the helper library the Telegram-call
engine imports (`_place_stop_leg`, `_cancel_leg`, the session cache, the
`OP_SIGNAL_EXIT_HOUR/MINUTE` cutoff, `lot_chunks`), and `op_signal_store.py`
holds the stage constants and the atomic write. `BROKER_N_OP_SIGNAL_LOTS` in
the env files is dead. The automatic side of the page is the section below.

## Telegram calls (auto-trader)

`app/order_placement/tg_calls_listener.py` watches one Telegram channel and
`tg_call_engine.py` trades what it posts. The channel is a paid one the user
is only a member of, so the **bot cannot read it** — the listener is a
Telethon *user* session on the user's own account:

```
TG_CALLS_ACTIVE=true            # master switch; read per call, never cached
TG_API_ID / TG_API_HASH         # my.telegram.org
TG_CALLS_CHANNEL_ID=3942647299  # the number after '#-' on web.telegram.org/k
TG_ENTRY_LIMIT_PCT=1            # entry limit = trigger × (1 + pct/100), up to the tick
BROKER_N_TG_ACTIVE=true         # per slot, with BROKER_N_ACTIVE; zerodha/fyers ONLY
BROKER_N_TG_LOTS=1              # per slot, the size of EACH of the two legs; no fallback to any OP size
```

The one-time login is `PYTHONPATH=src ../.venv/bin/python scripts/tg_login.py`,
run **with the app booted out** (the SQLite session must not be shared). It
leaves `env/tg_calls.session` — a credential, gitignored, **back it up with
`Mine.env`**. `telethon` is in `pyproject.toml`; install it in the venv.

A message that `parse_signal` reads as a call (`NIFTY 23150 CE / BUY : 81 /
SL : 65 / Target : 95,101,110`) becomes, per broker, **two legs** (since
2026-09-20): each is a **stop-limit BUY** (trigger = BUY price, limit = +1 %)
→ on fill an **SL-M SELL for that leg's whole filled qty** → **its target
watched on LTP**; touching it cancels that leg's stop and sells that leg at
market. Both legs are `BROKER_N_TG_LOTS` (so an account holds twice that
on a fill). The T1 leg exits at target 1; the T3 leg rides through T1 and
exits at the call's **last** target (T3, or T2 on a two-target call; a
one-target call places only the T1 leg). Slots are `brokers["N"]` (T1) and `brokers["N:T3"]`;
every record carries `tg_leg`. Four things are load-bearing:

* **Each stop covers the whole of its leg and targets rest nowhere.** A
  resting LIMIT at a target next to a full-size stop is twice the held
  quantity on the sell side — margined as a fresh short and fillable twice.
  So a target needs the app alive (the LaunchAgent sees to that); the stops
  do not. A T1 exit hands `exit_selected_records` only that leg's records,
  so it nets and sells that leg's qty — the T3 position and its stop stay.
* **A new message is the only way in; edits and deletions follow a call
  already taken.** `NewMessage` takes a call; every id is written to
  `tg_calls.json` (`TgCallStore.mark_seen`) before it is acted on, and
  anything older than `TG_CALLS_MAX_AGE_SECS` (90 s) at receipt is dropped,
  so a reconnect's catch-up cannot re-fire one. `MessageDeleted` on a taken
  call **withdraws it** (`retract_call`: cancel a resting entry, square off a
  held position, booked as `message deleted`). `MessageEdited` on one
  **moves it** (`amend_call`): entry → the resting stop-limit's trigger and
  limit are modified (refused if the new entry is at/below LTP); stop → the
  SL-M trigger is modified, and the tick keeps re-trying a refused move and
  exits at market if the premium is already through the edited level; target
  → the watched level moves. A contract change while resting withdraws and
  re-takes; after a fill the position is kept on the new SL/T1 and alerted. A
  chat line edited *into* a call fires nothing. Follow-ups wait up to 20 s for
  a call whose entry is still being placed.
* **A stop moved by hand on the strip is that leg's level.** The price
  box's PUT calls `note_manual_edit`, which finds the slot owning that SL
  record and writes `stop_level` on it; `slot_stop()` (hand-set level, else
  the call's `stop`) is what the tick re-places, retries and breach-guards
  against — it never moves the order back to the channel's number. The
  other leg's stop is untouched. A later channel edit clears the override.
  Editing a resting ENTRY row moves the call's entry/limit the same way
  (the sibling entry order is not moved).
* **A call the market has run past is skipped, not chased.** A stop BUY
  must sit above the LTP; if it does not, nothing is placed and a
  `tg_call_skipped` alert says why. Same for SELL calls, non-index
  symbols, a back-month expiry, or no eligible broker.
* **Nothing cold on the call's path.** The first live call (2026-09-16
  09:49) was answered 12 s late — Fyers symbol-master download, strike-token
  cache and Kite instrument dump all cold — and skipped against a premium
  that had already moved. `tg_call_engine.prewarm` pays those costs when
  the listener connects and again from the 5-minute watchdog; a call then
  costs one live quote plus the orders, which go to every account in
  parallel. `[TgCall] … chain X s, quote Y s` and `… in Z s from receipt`
  in the log are the numbers to watch.
* **With `TG_CALLS_ACTIVE=false` the listener still runs** and raises
  `tg_call_skipped` for every call it would have taken — that is the dry run
  that proves the parse path on real messages before the first order.

Every leg is a `MineOrderStore` record with `strategy='op'`,
`source='telegram'`, `signal_id='tg-…'`, `leg` in `ENTRY|SL|EXIT`, so the
/orderplacement strip, ✕, reconcile and Exit all work on them unchanged;
the page shows calls as read-only `TG` cards (no arm route, no Stand down). Jobs:
`tg_calls_start` 09:10, `tg_calls_watchdog` every 5 min at :15; startup
recovery reconnects after a restart. Status: `GET /api/order-placement/tg-calls`.

**P&L ledger.** Every market exit gets its own `leg='EXIT'` record (from
`exit_selected_records`' new `exits` list), so the status sweep reads the
fill back; once a flat slot's own sells (`_sell_fills` reads only the ids in
the slot's `legs`, since both legs sell the same contract) cover its entry —
or 90 s have passed — `_book_slot` appends a row **per leg** to
`app/order_placement/tg_trades_history.json` (**tracked**, like the algos'
trade histories) with `leg` (`T1`/`T3`), `pnl_per_lot` (points × lot size)
beside `pnl` (× lots at that account). The engine keeps ticking
until every flat slot is booked (`TgCallStore.get_unbooked`). The 📒 Auto P&L
button on /orderplacement reads `GET /api/order-placement/tg-calls/history`.
Tests: `tests/test_tg_call_engine.py`, `test_tg_calls_listener.py`,
`test_tg_call_store.py`.

## CPR Logic page — option legs

The CPR Manual vs Chart grid lives on **`/cpr-logic`** (`templates/cpr_logic.html`,
`static/js/cpr_logic.js`; moved off the Trend page 2026-09-19, which keeps
only its `.td-*` chrome in `static/css/trend_detection.css`). The sheet
(`service/cpr_backtest_service.py`, `Backtest/cpr_manual/<SYMBOL>.json`) is
written in NIFTY points, but the order that would go out is an option, so
a second call, `GET /api/trend/cpr-backtest/options[&premium=150|200|250|300]`
(`service/cpr_option_service.py`), prices every trade's leg: **CE for a BUY,
PE for a SELL, the weekly expiry running that day**, the strike whose
premium at the entry minute was nearest the page's strike dropdown
(**default ≈200**; "Current strike" is ATM to the entry) (`pick_strike` walks the
ladder from ATM, one Breeze request per strike looked at), read off the
contract's own 1-minute candles.
The index trade is replayed on 1-minute index bars (fill from the bar after
the 5-minute setup candle, then first of target/SL) and the premium is the
option's close at those minutes; a level never reached is an estimate
(`≈`, the entry premium moved by the day's least-squares delta), never a
fill. Only Breeze serves an expired contract, so the columns need an ICICI
login and say `ICICI login` without one. One Breeze request per trade,
disk-cached per settled session (`icici_data_service.historical_option_minutes`),
so the first read of the sheet is ~70 s and later ones are instant. Tests:
`tests/test_cpr_option_service.py`.

## Swing Momentum value graph

The 📈 icons on Live Watch (header = every config, broker row = that slot,
card title = that config) plot **Invested vs Current, one point per day**,
from `algo/swing_momentum/sm_daily_values.csv` — a tracked sheet, one row
per config per day, keyed by `(date, config_id)` and carrying the broker
slot so a removed config keeps its past. The `sm_daily_values_record` job
writes the close at **15:35 IST**; `/signal/<id>` upserts today's row every
time it prices a card (weekdays only), so the last point tracks the market
while the page is open. **Snapshot now** in the popup is the same write, by
hand. Nothing is backfilled: the graph starts the day the sheet did. The
maths (`series()` carries a config missing one day forward inside its own
span, never outside it) is in `sm_value_history.py`, tests in
`tests/test_sm_value_history*.py`; `conftest.py` points every test at a
scratch sheet because `/signal` writes as a side effect.

## EMA Confluence live mode

`algo/ema_confluence/` is a multi-day **futures** swing (NRML, carried
overnight, rolled 3 sessions before expiry). Since 2026-09-15 it places real
orders when:

```
EMA_CONFLUENCE_MODE=live        # default paper — simulated fills at the future's LTP
BROKER_N_EMA_ACTIVE=true        # per account, with BROKER_N_ACTIVE=true; zerodha/fyers ONLY
BROKER_N_EMA_LOTS=1             # per account; falls back to EMA_CONFLUENCE_LOTS
EMA_CONFLUENCE_ACTIVE=true      # the kill-switch still gates every entry
```

A flagged Dhan/Kotak/ICICI slot is refused with an error at thread start —
neither has a futures order path here. The mode is read once, at thread
start (08:30), like the other flags; a change needs the after-hours restart.

Two things are load-bearing:

* **The leg decides, not the flag.** Every live holding is a `broker_legs`
  entry on the symbol's state (broker slot, tradingsymbol, order ids, filled
  qty/price). A position with legs is flattened at the broker even after
  `EMA_CONFLUENCE_MODE` goes back to paper — and a broker whose
  `BROKER_N_EMA_ACTIVE` was turned off while holding a leg still gets a
  session for the exit. A paper position (no legs) is never sold at a broker.
* **An exit, once decided, is finished.** SL/Target set `exit_pending` on
  the symbol and the tick retries any leg the broker refused until every leg
  is flat, whatever spot does meanwhile; the trade is booked only then, at
  the qty-weighted real fill. A roll is the same on the near month, then a
  re-entry on the far one; if the far entry fails the account is flat and
  the symbol goes back to `pending_scan` with an alert.

Order failures raise `ema_confluence_order_failed` (bell + Telegram) once per
symbol per day. Tests: `tests/test_ema_confluence_live.py`.

## Removed algos

The EMA RTP live algo (five timeframe variants, `algo/rtp_railway_track/`)
was removed on 2026-09-15, with its `/api/algo/rtp*` routes, Algo-page tab,
scheduler jobs and tests. Its **backtest** went the same evening:
`Backtest/rtp_backtest_engine.py`, the `/api/backtest/rtp*` routes and the
RTP strategy on `/backtest`. Two things kept the name: the shared optimiser
cache is still `app/utils/rtp_opt_cache.json` (2nd-Candle / Scalp / Pivot
sweeps write it), and `#rtpStatsRow` is still the id of the shared Row-2
stat cards. The per-user `EMA_RTP_*` / `RTP_*_STRIKE_MODE` /
`BROKER_N_RTP_*` variables in `.users.json` are now dead and read by
nothing. EMA Confluence (`algo/ema_confluence/`) was **not**
removed — it is a separate algo and still runs (see "EMA Confluence live
mode" above).
