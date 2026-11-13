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
`extensions.py:96` → `scheduler.py:978`), which registers 22 cron jobs and
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
re-registers the 22 cron jobs and **restarts the live algos** — the same reason
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

`tests/route_inventory.txt` is the golden URL surface — 231 rules with their
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
TG_TARGET_NEAR_PCT=2            # inside this % of a target the tick polls 1s and skips the sweep
TG_LEG_PLAN=1-3                 # single | 1-2 | 1-3 | 1-2-3 — the Legs dropdown on the page
TG_BREAKEVEN_AFTER_T1=true      # T1 banked → every other leg's stop lifts to its own entry fill
BROKER_N_TG_ACTIVE=true         # per slot, with BROKER_N_ACTIVE; zerodha/fyers ONLY
BROKER_N_TG_LOTS=1              # per slot, the size of EACH leg — the entry order is twice this
```

The one-time login is `PYTHONPATH=src ../.venv/bin/python scripts/tg_login.py`,
run **with the app booted out** (the SQLite session must not be shared). It
leaves `env/tg_calls.session` — a credential, gitignored, **back it up with
`Mine.env`**. `telethon` is in `pyproject.toml`; install it in the venv.

A message that `parse_signal` reads as a call (`NIFTY 23150 CE / BUY : 81 /
SL : 65 / Target : 95,101,110`) becomes, per broker, **one order that
becomes N legs** (legs since 2026-09-20, one order since 2026-09-23, the
count chosen on the page since 2026-10-06): a single **stop-limit BUY** for
`BROKER_N_TG_LOTS × legs` (trigger = BUY price, limit = +1 %) → on fill it
is **split into one leg of `BROKER_N_TG_LOTS` per plan entry**, and each
gets an **SL-M SELL for its own qty** and **its own target watched**;
touching it cancels that leg's stop and sells that leg alone at market. One
line at the broker and one row on the strip, not N orders competing for the
same strike — only the exchange's 27-lot freeze limit splits it further.

**The Legs dropdown** on /orderplacement (`TG_LEG_PLAN`, saved by
`POST /api/order-placement/tg-calls/leg-plan`, read at the moment a call is
taken so it needs no restart) picks the shape:

| Dropdown | `TG_LEG_PLAN` | Legs | Exits at |
|---|---|---|---|
| Single | `single` | 1 | T1 |
| Double 1 2 | `1-2` | 2 | T1, T2 |
| Double 1 3 | `1-3` (default) | 2 | T1, T3 |
| Thrice | `1-2-3` | 3 | T1, T2, T3 |

`resolve_leg_plan` settles it against the call that actually arrived: an
index **past the end rides to the last target the call lists** (so `1-3` on a
two-target call is T1+T2 — exactly what every call traded before the
dropdown existed), and **two indices landing on one level are one leg**, so a
one-target call is one leg under any plan rather than the same leg stacked at
three times the size. The label is the target's real position, so that far leg
is named `T2`, not `T3`. Slots are `brokers["N"]` (the first leg) and
`brokers["N:T2"]` / `brokers["N:T3"]`, sharing one `entry_record_id`; the
SL/EXIT records carry `tg_leg`, the shared entry carries `T1+T2+T3`. A call
already on the board keeps the plan it was taken under — a dropdown does not
place or un-place an order. Six things are load-bearing:

* **A leg's exit is capped at what that leg still owns.** The shared entry
  record's quantity is the *pair's*, so the net it produces is too big by
  the other leg's holding — and a leg whose stop has already filled still
  nets positive against it and would be sold a second time, which is a
  fresh short. So `_flatten` passes `exit_selected_records(max_qty=…)`:
  `open_qty` (the leg's share of the fill, `leg_share()` — T1 filled first
  on a part fill) less what `_sell_fills` says the leg has already sold.
  Nothing may infer a leg's size from the entry record.
* **Each stop covers the whole of its leg and targets rest nowhere.** A
  resting LIMIT at a target next to a full-size stop is twice the held
  quantity on the sell side — margined as a fresh short and fillable twice.
  So a target needs the app alive (the LaunchAgent sees to that); the stops
  do not. Two legs at one broker are two stops, never one for the sum: one
  stop for the pair would have to be re-sized the moment either leg exits,
  and a stop being modified is a stop that is briefly not there.
* **A target is watched on the session high, not only on the last price.**
  Until 2026-10-06 the tick compared one LTP sample against the level every
  ~3 s (longer when the quote path was loaded), so a spike through a target
  between two polls was invisible. NIFTY 22800 PE that day wicked through
  its 196 T1, every poll read below it, and both legs rode to the 167 stop.
  `option_quote` now carries the session high and low beside the last price
  — free, since every adapter's `ltp()` is `quote()` with the range thrown
  away — and `touched_above` takes a high **strictly above** the high
  recorded at the fill (`high_at_fill`) as proof of a trade since the fill.
  Strictly above, because the day's high may predate the entry. With no
  baseline the high is not consulted at all. `touched_below` is the mirror,
  on the low, for the stop-breach guard that runs when nothing is resting.
  Because that witness can only fire *late*, never falsely, the exit is at
  market as soon as it fires — so within `TG_TARGET_NEAR_PCT` (default 2 %)
  of a watched level the loop polls every second and skips the order sweep
  (`tick(light=True)`), with a full tick still running at the ordinary
  cadence underneath so a filled stop is still seen.
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
* **A stop moved by hand on the strip is that leg's level, and so is a
  target moved on the card.** The price box's PUT calls `note_manual_edit`,
  which finds the slot owning that SL record and writes `stop_level` on it;
  `slot_stop()` (hand-set level, else the call's `stop`) is what the tick
  re-places, retries and breach-guards against — it never moves the order
  back to the channel's number. A target is not an order, so it has no strip
  row: the card's own box PUTs `/tg-calls/<id>/target` →
  `note_manual_target` → `target_level` on that leg, which `slot_target()`
  prefers (refused if it is below the stop/entry or already through the
  market). Both are per leg — the other leg is untouched — and a later
  channel edit clears both overrides. Editing the resting ENTRY row moves
  the call's entry/limit the same way — and there is one such row per
  account, covering both legs, so a channel edit modifies it once.
* **T1 banked lifts every other leg to its own entry.**
  (`TG_BREAKEVEN_AFTER_T1`, default on.) Only the plan's *first* leg arms it
  — a later leg paying has nothing left behind it to protect — and it is
  armed on the CALL while the level each leg moves to is its own account's
  `entry_fill`, since fills differ per broker. `slot_stop` returns
  `max(call stop, entry_fill)`, so it is a floor and never moves a stop
  DOWN, and a level set by hand on the strip still outranks it. No order is
  sent by the breakeven path itself: the tick already reconciles a resting
  stop that is not at `slot_stop`, so the lift is a **modify**, not a cancel
  and re-place — a stop being replaced is a stop that is briefly not there.
  It is armed on the in-memory call too, so the legs still to be walked in
  that same tick move at once rather than waiting for the next one.
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

**Exit all** (`stop_all_calls`) is handed `route_scoped_exit`'s own result,
and reads two things from it. A leg the broker **refused** to square off
(margin, on 2026-09-21) stays `LIVE` and keeps being managed — the page
cancelled its stop on the way out and the tick puts it back — because a
refusal means the position is still held; it alerts `tg_call_order_failed`
and the call is not stood down. For the legs that did exit, the market
orders the page sent are written back as `leg='EXIT'` records (split across
the legs, oldest first), so the sweep reads the fill and the ledger books a
real price instead of `None`.

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

## CPR Logic live algo

The /cpr-logic rule, run live on NIFTY options — the **CPR Logic** tab on
/algo (`templates/algo_cpr.html`, `static/js/algo_cpr.js`), the thread
`algo/cpr_logic/cpr_algo.py`, the brain `service/cpr_live_engine.py`, routes
`/api/algo/cpr-logic/{status,settings,history,start,stop}`, jobs
`cpr_logic_start` 09:05 + `cpr_logic_watchdog` (and startup recovery).
Added 2026-10-12, **paper by default**:

```
CPR_LOGIC_MODE=paper            # live = real orders; the tab's toggle, read at each ENTRY
CPR_LOGIC_ENABLED=true          # the tab's Start/Stop (scheduler honours it)
CPR_LOT_MODE=fixed              # compound = 1 lot per CPR_PER_LOT of CPR_START_CAPITAL + the mode's booked P&L
CPR_LOTS=1                      # paper's fixed size
CPR_START_CAPITAL / CPR_PER_LOT / CPR_MAX_LOTS   # 20000 / 20000 / 27 — compound starts at 1 lot
BROKER_N_CPR_ACTIVE=true        # per account, with BROKER_N_ACTIVE; zerodha/fyers only
BROKER_N_CPR_LOTS=1             # per account; an account without it is refused, never guessed
```

Load-bearing:

* **One rule, two drivers.** The engine calls `cpr_trade_rule.propose_all` on
  the closed candles so far (and `second_trade`, factored out of
  `_propose_all_core` for it) — never a copy of the rule. Every decision is
  final once made. Replayed that way over 2018-26 it earns what the sheet
  does (618 trades, 54.9 %, +10,956 pts vs 606 / 54.5 % / +10,802). It
  decides at the 09:15 close on one candle where the sheet would have waited
  for 09:20 — kept, because waiting earned less (+10,302).
* **The trade is on the index; the order is an option.** Entry, stop and
  target are NIFTY levels watched on the index (price polled every 5 s only
  while a trade is armed/open; the closed candle's extremes are the net for
  missed wicks). On the entry the ≈`CPR_PREMIUM` (200) front-weekly strike is
  bought at market (`_dispatch_order_to_brokers(strategy='cpr')`, records
  `strategy='op'` / `source='cpr'` so the /orderplacement strip shows them);
  exits sell it at market via `exit_selected_records`. Live, an SL-M
  **backstop** rests on the premium at `fill − CPR_BACKSTOP_X (1.5) × index
  risk` (0 = none) — the floor if the app is down, not the exit. It was 0.75×
  until 2026-10-13, when an ITM CE hit it at 11:15 (−₹3,208) while NIFTY was
  clear of its stop and the index trade went on to +175 pts.
* **The books are the broker's.** Entry/exit prices are read back from the
  order book (`leg_fills`) every 15 s; a backstop (or hand square-off) filled
  at the broker closes the leg at its fill; history is written once fills are
  in (`_book_if_settled`). Before this the tab booked LTP quotes.
* **`strategy='cpr'` is opted out of the dispatcher's automatic `entry − 20`
  stop** and routes on `BROKER_N_CPR_ACTIVE` only; it refuses to size itself.
* **A fill read off a candle that is not the latest is never traded** — a
  restart catching up the morning marks it `missed`.
* Target lock and close-back exits are the sheet's (`simulate_trade`).

Tests: `tests/test_cpr_live.py`.

## ATM Straddle live algo

The Backtest page's ⚖️ straddle run live — the **ATM Straddle** tab on /algo (`templates/algo_straddle.html`,
`static/js/algo_straddle.js`), the thread `algo/straddle/straddle_algo.py`, the brain
`service/straddle_live_engine.py`, routes `/api/algo/straddle/{status,settings,history,start,stop,square-off}`, jobs
`straddle_start` 09:05 + `straddle_watchdog` (and startup recovery). Added 2026-11-04, **paper by default; the live
order path has never been exercised**. The tab's boxes are the Backtest form's, defaulting to the row it was chosen on
(weekly · Monday · 15:15 · T50/S25 · re-centre 200 ×2 · lock 40→30 · compounding ₹2.5 L / ₹2.5 L). The boxes are locked
until the Settings card's Enable button is clicked, and lock again on every page load (page-side only; the route does not check it).

```
STR_MODE=paper              # live = real orders; the tab's toggle, read at each ENTRY
STR_ENABLED=true            # the tab's Start/Stop — new entries only, never an open position
STR_CADENCE STR_ENTRY_DAY STR_SKIP_ONE_NIGHT STR_ENTRY_TIME STR_TARGET_PCT STR_STOP_PCT STR_ADJUST STR_ADJUST_PTS
STR_MAX_ADJUST STR_WINGS STR_LOCK_AT STR_LOCK_TO STR_LOTS STR_COMPOUNDING STR_START_CAPITAL STR_PER_LOT
BROKER_N_STR_ACTIVE=true    # per account, with BROKER_N_ACTIVE; zerodha ONLY
BROKER_N_STR_LOTS=1         # per account, when compounding is off; an account without it is refused, never guessed
```

Load-bearing:

* **One rule, two drivers.** `adjust_action`, `lots_for_capital`, `entry_days`, `weekly_expiry` and `contract_expiry` are
  the backtest's own; a test holds every adjustment type booking the backtest's points and every entry rule picking its
  dates. An expiry the exchange does not list moves to the listed one in its week or is refused (Tue 2026-11-10 is a
  holiday: Mon the 9th sells the 17th).
* **Where live must differ** (the engine's docstring): target/stop/lock read on every 5 s poll and exit at market, so a
  spike between two closes can trip it where the backtest's closes would not; the 5-minute "close" is the first poll after
  the boundary; the expiry exit is 15:25, not settlement; one position at a time (a monthly still open is not doubled).
* **It does not go through `_dispatch_order_to_brokers` or `MineOrderStore`.** The dispatcher picks the nearest expiry; every
  order here is NRML on a named tradingsymbol, and the Order Placement strip's Exit (written for bought options) must not
  reach a short. So it is not on that strip: the tab's **Square off** is the way out, or the broker.
* **Nothing rests at the broker.** A position is only managed while the app runs and Kite is logged in (the token dies every
  morning); no quotes on an open position raises `straddle_order_failed`. An overnight gap cannot be stopped: 2026-02-03.
* **The leg decides, not the flag.** `held` per account is what is managed; Stop, a Paper toggle or a cleared flag never orphan
  it, and `ensure_running` starts the thread for an open position whatever `STR_ENABLED` says.
* **Buys first**, then sells. A short refused at entry (Kite accepts, then rejects on margin — fills are read back) unwinds
  that account; a failed buy-back stops that account's new short. **An exit is retried every poll until flat**, capped at the
  broker's NRML net (unreadable positions hold the exit rather than send blind); `_reconcile` compares each minute.
* **Compounding is per book:** each account (and paper) on its own start + booked P&L. The history row carries each account's
  gross and net; net uses the backtest's ₹100 per leg so the two compare, not Zerodha's real charges.

Tests: `tests/test_straddle_live.py` (a fake broker; nothing there can reach a real one). Contract lookup was checked read-only
against real Kite on 2026-11-04; live quotes and positions were not (no login yet that morning).

## ST Touch Break live algo (Mine → NIFTY)

The Backtest page's **ST Touch Break** (Mine group) run live on NIFTY options — the **Mine** tab on
/algo with one sub-tab, **NIFTY - Both - 3m** (renamed 2026-10-28; `templates/algo_simple.html` with `group='mine'`, `static/js/algo_stbb.js`
config `'mine-nifty'`). Added 2026-10-27, **paper by default**. It is *strategy 8 of the Simple Trader
chassis* (`algo/simple_trader/simple_trader_algo.py`, `PROFILES[8]`, flags `ST8_*`, routes
`/api/algo/simple-trader-8/{status,settings,history,start,stop}`, state `simple_trader_8_*.json`), not a
copy of it: thread, paper/live legs, broker fills, history and alerts are the Simple Trader ones. The
Simple Trader tab and its Combined view are still 1-7.

```
ST8_MODE=paper              # live = real orders; the tab's toggle, read at each ENTRY
ST8_ENABLED=true            # the tab's Start/Stop (scheduler honours it, job simple_trader_start)
ST8_DIRECTION=both          # both (the default since 2026-10-28) | long (BUY, CE) | short (PE); the tab's Direction box
ST8_ST_PERIOD=7  ST8_ST_MULT=1.5  ST8_STOP_TOUCH=1  ST8_MIN_WICK_PCT=40  ST8_TOUCH_TOL_PCT=0   # the Backtest form of 2026-10-28
ST8_MIN_BODY_PCT=50         # break candle's body share of its range, closing the way it broke (0 = off)
ST8_OUTSIDE_BAR=close       # close = an outside break candle goes the side it closes on | first = side crossed first
ST8_PREMIUM=250  ST8_MAX_TRADES=5  ST8_SL_PCT=0  ST8_TSL_X=0  ST8_TSL_Y=0     # premium stop off, as the backtest
BROKER_N_ST8_ACTIVE=true    # per account, with BROKER_N_ACTIVE; zerodha/fyers ONLY
BROKER_N_ST8_LOTS=1         # per account; an account without it is refused, never guessed
```

Load-bearing:

* **The rule is the backtest's, and only what can be known live.** `service/st_touch_break_live_engine.py`
  builds `StTouchBreakEngine` on the closed candles and reads its own `indicators()` / `touches()` — never a
  copy. A touch candle that has just closed **arms** the next candle (nothing is ordered); the order goes out
  the moment the index trades through the touch candle's high (low for a short) inside that candle; if the
  candle closes without it the setup is `expired`. **With `ST8_MIN_BODY_PCT` above 0 or `ST8_OUTSIDE_BAR=close`
  (both are the defaults since 2026-10-28) the break is judged at the break candle's CLOSE instead** — both read
  that close — and the order goes out then, at the close, not at the level: a later fill than the backtest's,
  which fills at the level with the close already known, so the backtest is this setting's ceiling. Entries
  from 10:00 (`Spec.entry_start`). A close a restart is catching up on is `missed`, not chased. Exit is the index **trading through** the previous
  candle's SuperTrend line (`ST8_STOP_TOUCH=1`, the default; not on the entry candle) or, with
  `ST8_STOP_TOUCH=0`, a candle **closing** past it — or 15:14.
  Tab 8 is Bounce only; 9-15 may take Through setups (below).
* **A touch stop the 5 s poll missed is caught at the close** (since 2026-10-30). The backtest exits on any
  candle whose low (high) trades through the previous candle's line, even if it closes back; a spike that
  reverses between two polls is invisible live, so `on_bar` also checks the closed candle's low/high against
  `stop_line` and exits at that close (`SuperTrend stop`) — same decision as the backtest, a few seconds late.
* **Price is read only while something waits on it** — an armed setup's break candle, or an open trade
  under a `touch` stop (`_price_events`, polled at the chassis's 5 s). The chassis gained two hooks for
  this, `_price_events` and `_signal_text`, both no-ops for the other seven.
* **A level the index has already run past is not chased.** If the first read is more than
  `MAX_CHASE_RISK_FRAC` (0.5) × the setup's risk (level to the SuperTrend line) beyond the level, the
  setup is `missed` and nothing is sent. A restart catching up the morning arms old candles whose break
  windows are long gone: they can never fill (`on_price` keys on the clock) and expire on the next candle.
* **Order path = Simple Trader's `_enter_live`** (`strategy='st8'` in `_dispatch_order_to_brokers`, which
  routes on `BROKER_N_ST8_ACTIVE`, takes no automatic `entry − 20` stop and refuses to size itself —
  `_STBB_FLAGS` was widened to 1-8). With `ST8_SL_PCT` above 0 an SL-M rests at the broker at the initial
  stop; the trail and the SuperTrend exit are managed here, so with SL 0 the **only** protection if this
  app is down is the Order Placement strip. **The live order path has never been exercised**: run paper for
  a day first and read the log lines (`touch candle … waiting for the index to trade above …`).
* The sweep behind the Backtest page's Find Best Params does not reach here: the tab's boxes are the
  numbers it searches (SuperTrend length/factor, direction, stop, min wick) plus min body and the outside-bar side, set by hand.

**Mine sub-tabs 9-15 (2026-10-30, paper; replaced the 2026-10-29 rows 9-16).** The Backtest page's ★ Saved
rows (the eighth, `Both ★ Default`, is 8) are strategies 9-15 of the same chassis, one sub-tab, thread,
`ST<n>_*` flag set, `BROKER_N_ST<n>_*` and `simple_trader_<n>_*.json` each: `_touch_break()` in
`simple_trader_live_engine.py` builds them from the saved row's numbers (timeframe · ST len/factor · setups ·
stop · wick/tol/body · outside bar · from/until · square-off · trades a day), `MINE_TABS` lists 8-15 for the
page. Tab names end in the saved row's label: `NIFTY - Sell - 5m - #15` (9), `- Sell - 5m - #9` (10),
`- Sell - 5m - #8` (11), `- Sell - 3m - #6` (12), `- Sell - 3m - #2` (13), `- Buy - 3m - #8` (14),
`- Buy - 5m - #2` (15). They briefly lived at 17-23 the same evening. The old rows' `ST9-16_MODE` (all
`live`) / `BROKER_1_ST9-16_*` flags were deleted first and their `simple_trader_9-16_*.json` renamed
`*.removed-20261030.json` (the 10/12/13/15 histories hold that day's real trades), so the new rows start
paper with empty books (env backups `scratch/Mine.env.bak-20261030-*`). Candles, window, square-off and
setups are fixed in the spec (`fixed['modes'|'entry_end'|'exit_cutoff']`), not settings.
* **Through setups trade live since 2026-10-30** (9, 14; 12/13 take Bounce + Through on the SELL side). A
  Through breaks THROUGH the line (uptrend touch → short under the touch low); it needs no SuperTrend flip to
  enter — if the break candle closes with the trend still against it, it is out at that close, as the
  backtest's `SuperTrend flip`. One side is armed per touch (every row is one-sided or Bounce only). The
  chase guard measures a Through against the touch candle's range, since its level sits on the line.
* **The backtest fills a close-judged break at the close** (`StTouchBreakEngine.confirm_at_close`: body > 0
  or outside bar `close`), as live does. Until 2026-10-30 it filled at the level with the close known —
  look-ahead worth half to two-thirds of every such row's points; boards and saved rows from before then
  overstate them.
**The 🧲 ST Touch Break group on /backtest (2026-11-05)** lists those eight rows (`MINE_LIVE_TABS` from
`pages._mine_live_tabs`, so a new `MINE_TABS` row appears by itself). Every option's value is `st_touch_break`, told apart
by `data-stb-row`, so the form, Find Best Params and the run are the one strategy's; picking one fills the form (still
editable) with NIFTY, premium 250, no premium stop, start date 2026-11-01. It is the strategy's only entry: the plain `ST Touch Break (SuperTrend wick + break)` option in the Mine group was removed on 2026-11-06 (form-level only; engine, routes and sweep stay, since the rows and the live algos use them), and a Saved Touch Break row's USE now lands here on its saved start date. **The top tab on /algo reads ST Touch
Break too, and both lists show the same serial, 1-8** (the row's place in `MINE_TABS`, so 1 = spec 8 … 8 = spec 15). That is
display only: flags, routes, files and page keys keep the spec number (`ST8_*`, `mine-nifty`). Bell and Telegram alerts carry the same title (`ST Touch Break · 1. NIFTY - Both - 3m`).
The api blueprint is at 262 rules (`tests/route_inventory.txt`): the ATM Straddle live algo added six (above), the Bi-weekly Iron Condor backtest three and the Monthly RSI Breakout four (below, now with Find Best Params).

Tests: `tests/test_st_touch_break_live.py` (engine, paper thread, settings, that every candle the
backtest trades is armed live, and the same per row for 9-16).

## RSI 60-65 Breakout backtest — Day / Week / Month (stocks, the /backtest default; `monthly_rsi_breakout`)

Added 2026-11-13 in the Mine group, **the default strategy on /backtest** since that day (it took over from the Bi-weekly Iron Condor), from a video (Darshan Rathod, "hedge fund / mutual fund strategy"; transcript via youtube-transcript-api).
`Backtest/monthly_rsi_breakout_engine.py` (pure), routes `/api/backtest/monthly-rsi-breakout{,/status/<id>}`, params row
`#monthlyRsiBreakoutParamsRow` (`mrb*` ids), results in the shared `#crResults` (`#crStats`, `#bicYearGrid` as the per-year table, `#crGrid`),
JS `runMonthlyRsiBreakout` / `renderMonthlyRsiBreakout`.

* **Rule**, on calendar-month candles folded from daily bars: a month closes with RSI(14) (Wilder, TradingView's) in 60-65 → next month only,
  buy at that month's high (a gap over it fills at the open). Out when a month CLOSES below its 20 EMA and a later day trades below that
  candle's low (filled at the low, or the open on a gap); a close back above disarms; consecutive closes below re-arm at the LATEST candle's low
  (the video does not say which). ≤ 5 new stocks a month; no new entries while NIFTY's monthly RSI is < 40. EMA/RSI need 20 months of history.
* **Not in the video, so boxes**: one cash portfolio (₹1 L since 2026-11-13 at the user's ask — that pot and what it earns pays for every buy,
  nothing is added; `min_cash` in the result proves it never went below 0; it was ₹10 L; a buy the cash cannot
  pay for in full is skipped, never bought smaller — until that rule the book filled up with ₹54 positions), a liquidity floor (₹1 Cr average daily
  turnover in the scan month), 0.15 % charges a side plus ₹20 brokerage an order (each buy and sell; `brokerage`, the
  Brokerage ₹ per Order box, added the same day). Same-day entries go most-liquid first.
* **Position Size: Fixed ₹ (default) or Risk %** (`size_mode`, added the same day after a run, at the user's ask —
  "all the stocks is should be same amount of investment"). Fixed buys every new stock with the same `fixed_amount`
  (₹10,000 default — the "Amount per Stock" box); Risk % is the original video sizing, qty = equity × `risk_pct` ÷
  (entry − 20 EMA), capped at `max_pos_pct` of equity — only those two boxes are live under Risk %, and only
  Amount per Stock is live under Fixed (`syncMrbSizeMode` toggles them). Either way the cash-must-cover-the-whole-
  buy rule above still applies. At ₹1 L / ₹10,000 F&O 2015-26: 190 trades (was 250), CAGR 16.4 % (was 20.4 %), max
  DD 25.5 % (was 19 %), up to 50 holdings at once (was 37) — smaller, more numerous, more equal-sized positions;
  read as one more draw from the same path-dependent pot, not a verdict on which sizing is "better". `simulate()`'s
  sizing block now picks `q_target` (fixed_amount/px, or min(q_risk, q_cap) under Risk %) and only then floors it —
  the cash check (`qty > q_cash` → skipped) stays a SEPARATE step after the floor, not folded into it: folding cash
  into the floor silently shrinks a position instead of skipping it, which is the one thing this engine must never
  do (caught by `test_a_buy_must_cover_its_brokerage`). Find Best Params (below) collapses `risk_pct` in its grid
  to one value under Fixed sizing, since it cannot change the result there — sweeping it anyway would only triple
  the combo count for identical rows.
* **Data**: only the per-stock daily pickles under `data/stocks/` (`candle_store._load_disk`, the plain-symbol key the nightly EMA Narrow prewarm
  fills) — no broker call, so it is safe in market hours; all ~2,600 NSE stocks load in ~10 s. Universe is today's NSE / F&O list (survivorship).
  ~220 files carry a day opening 40 %+ from the last close (unadjusted splits, demergers, bad prints): `clean_daily` starts that stock's history at
  its last such day. NIFTY is `NIFTY.pkl` (from 2013).
* **First real run** (2015-01 → 2026-11-13, defaults, all NSE): 145 stocks bought, 41.6 % win, PF 2.76, CAGR 13.5 % vs NIFTY 9.3 %, max DD 32 %
  (NIFTY 29 %); F&O only: 114 trades, CAGR 12.5 %. Cash is the binding limit (~6,900 breakouts skipped for it), so WHICH breakouts get taken is
  luck of timing — so much so that the brokerage box alone moves CAGR 10.4-14.0 % with no trend (₹0 13.5 %, ₹5 14.0 %,
  ₹20 10.4 %, ₹40 13.9 %) while costing only ~₹6 k in 12 years: a few rupees less cash buys a different stock and the path
  forks. Read one run as one draw, ~10-14 % a year. Every breakout equally (7,448): 40.5 % win, median −9.2 %, mean +27.4 % — the edge is the fat right tail. Video checks: Phoenix
  Mills Aug 2021 entry 447.48 (video ≈447), still held; Yes Bank exit 162 (video ≈168). BSE Jun 2023 reads RSI 56.9 here vs the video's 61.

* **Timeframe box** (`timeframe`: month — the video's, default — / week / day; added the same day): the same rule on Mon-Sun weeks or days
  (`period_bars`; signals are `signal_period`, labelled `YYYY-MM` / the Monday / the date). RSI, EMA, the next-candle entry, the exit and NIFTY's
  RSI filter all move to that timeframe; the 5-a-month cap and the equity curve stay per calendar month. The day path is vectorised (128 k
  breakouts over 2015-26, ~20 s for the run). 2015-26, all NSE, ₹20 brokerage: month 147 trades CAGR 10.4 %; week 396 trades, 34 % win, PF 1.16,
  CAGR 3.7 %, DD 37 %; day 697 trades, 32 % win, PF 0.89, CAGR −2.7 %, DD 47 % (₹2.6 L of costs on ₹10 L; the 5-a-month cap leaves it mostly in cash,
  1-6 stocks held). NIFTY made 9.3 %. Only the video's monthly reading beats the index.
  At the ₹1 L default (same run): month 156 trades, CAGR 8.5 %, DD 36 %, ₹6.2 k brokerage = 6 % of the capital; week 3.0 %; day −3.8 %
  (₹1 L → ₹63 k). Month at ₹1 L across brokerage ₹0 / 10 / 20 / 30: 12.4 / 10.5 / 8.5 / 14.9 % — the same path noise, so ~9-15 %, about NIFTY's 9.3 %.

* **Defaults are the swept best since 2026-11-13** (the user asked for "better profit every year"): F&O universe, RSI 65-75, a 10 EMA
  (`ema_len`), 0.5 % risk — the rest as before (₹1 L, 5 a month, 20 % max, NIFTY RSI 40, low-break exit, ₹1 Cr, 0.15 % + ₹20). The video's
  60-65 / 20 EMA / 1 % stay in the boxes' notes. Sweep: 504 universe × band × EMA × exit × turnover × NIFTY-filter combos, then 256 sizing
  combos, each on 5 brokerage-jittered paths (median), judged also per half (2015-20 / 2021-26). 65-75 was the best band in every EMA /
  exit / universe and in both halves; EMA 10 > 20 > 30; F&O > all; 0.5 % risk > 1 % (more, smaller holdings: up to ~37); the NIFTY EMA
  filter (`nifty_ema`, box, 0 = off) and the `exit_rule='close'` exit (box) did not help on average. Result at ₹1 L, 2015-26: 250 trades,
  51 % win, PF 4.97, CAGR 20.4 % (was 9.75 %), max DD 19 % (was 35 %), better in 10 of 12 years; losing years 2015 −7.5 %, 2018 −11.1 %
  (were 5: also 2022 −15 %, 2025 −4 %, 2026 −0.1 %). Started in 2016 / 2018 / 2020 / 2022 it still beats the old settings (24 / 22 / 26 /
  21 % vs 17 / 13 / 16 / 15 %). Caveats: chosen on the same data it is scored on, and the F&O list is TODAY's — stocks that grew into it —
  which flatters it more than the all-NSE list does. Every year positive was not reachable: 2015 and 2018 lose under every setting tried.
  `simulate` marks holdings with a numpy search now (0.87 s → 0.05 s a run).

* **⚡ Find Best Params (added 2026-11-13).** Routes `/api/backtest/monthly-rsi-breakout/optimise{,/status/<id>}`,
  panel `#monthlyRsiBreakoutOptimisePanel` / `#mrbOptGrid`, JS `runMrbOptimise` / `applyMrbOptResult`. No cache, no
  Breeze — same disk-only stock files as the backtest, so it is safe in market hours; a run just takes longer.
  `SWEEP_GRID` is RSI band (8) × exit EMA (4) × exit rule (2) × risk % (3) × new-stocks cap (3) × NIFTY RSI floor
  (2) = 1,152 combos; universe/timeframe/capital/max position %/costs/turnover/NIFTY trend EMA come from the
  page's own boxes and are **not** swept (sweeping them too would multiply the grid for little gain — the first
  sweep already showed universe and the trend filter settle one way). `candidates()` grew `keep_raw` (an
  unrounded `_rsi` on each candidate) so one call per (EMA, exit rule) pair on the **widest** band in the grid
  serves every RSI-band combo by slicing with `in_band()` — the band is the cheapest axis to re-check and the
  only one `simulate()` can't be handed pre-filtered any other way. `score()` runs each combo on
  `SWEEP_PATHS` (capital nudged ±0.1 %, since this portfolio is cash-bound and forks on a few rupees — see
  above) and takes the median; `board()` keeps the union of the top 150 by CAGR, by the **weaker** half's CAGR
  (steadiness) and by CAGR ÷ max drawdown, among combos with ≥10 trades, so a row that only wins because of one
  lucky half doesn't crowd out a steadier one. A test (`test_sweep_row_matches_a_direct_simulate_of_its_own_params`)
  holds one sweep row equal to a direct `simulate()`+`summarise()` of that row's own settings — the whole point
  of candidate-sharing is that it must never change a result, only how fast it is computed. Real cost: ~245 s for
  the full F&O 2015-26 grid (3.6 s building 8 candidate lists, ~0.2 s/combo scoring); a shorter range or All-NSE
  universe scales with it. Route test fakes the stock files and shrinks the grid, the usual pattern.
* **Equity Curve + P&L Breakdown (added same day, at the user's ask).** The two shared charts every other
  backtest uses (`renderEquityCurve` / `renderPeriodBreakdown` in `static/js/backtest.js`) now also render
  under this strategy's own `#crResults` panel, fed `pnl_rupees` directly (`entry_date` is mapped onto
  `entry_time` for them, since every other caller's trades carry that field). Those two chart sections live
  inside `#resultsArea`, a sibling of `#crResults` that this strategy otherwise leaves hidden, so it is shown
  too — `#resultsRow1` (its one piece not already behind its own `display:none`) is hidden by hand instead, to
  avoid a second, empty "Total Trades" row sitting under `#crResults`' own stat cards; every other strategy's
  own render (`_displayResultsCore`) now unconditionally restores `#resultsRow1` first, so it never stays
  hidden once a normal backtest runs. Both charts are built only AFTER `#resultsArea` is shown, not before —
  Chart.js measures a canvas's size at construction, and a canvas under a still-hidden ancestor measures 0×0
  and stays blank even once that ancestor is revealed a moment later (caught visually on the harness: the
  P&L Breakdown bars were empty until the order was fixed).
* **Max Amount Needed (added same day)**: `summarise`'s `max_invested` = `capital − min_cash` — the deepest
  the cash pot ever dipped, i.e. the most ever actually tied up in stock at one time. Since every buy is paid
  from cash in hand and nothing is added, this can never exceed `capital`; when it sits well under it, the
  Capital box is bigger than the run ever needed. No new engine logic — `min_cash` already existed (the "₹1 L
  never overspent" rule) — just a second reading of the same number.
* **Compounding (added same day, Fixed ₹ mode only)**: the `Compounding` checkbox beside `Amount per Stock`
  (`mrbCompound`, visible only under Fixed ₹ — `syncMrbSizeMode` hides it under Risk %). Off (default): every
  new stock is bought with the plain `fixed_amount`. On: that amount scales with `capital + realized`, where
  `realized` is the net ₹ of trades already CLOSED by that entry — never an open position's mark-to-market
  gain, the same rule the ATM Straddle / Option Selling backtests use, so a position still open when the next
  one is bought has not "paid" yet. `simulate()` tracks `realized` as a running total, updated only in
  `close_out`, and stamps each position with its own `buy_cost` at purchase so the exact P&L of that trade can
  be added back on exit without re-deriving it from `cost_pct`/`brokerage` a second time. Risk % sizing already
  scales with mark-to-market equity on its own (`equity_at`), so `compounding` is a no-op there by design — a
  test holds a risk-mode run identical with the flag on or off. Real effect on the full F&O 2015-26 run (₹1 L,
  ₹10,000 base): position sizes grow from ~₹10,000 in 2015 to ~₹54,000 by 2026 as profits compound, net P&L
  ₹6.55 L vs ₹7.6 L flat (same number of trades, bigger ones), CAGR 18.6% vs 20.4% flat on this particular run
  — not a verdict either way, just a different sizing rule on the same path. Not swept by Find Best Params
  (taken from the page like size_mode/fixed_amount).
* **Max Stocks at Once (added same day, at the user's ask — "keep maximum of 10 stocks in progress at a
  time"): `max_positions` (default 10, 0 = off).** A HARD cap on how many stocks are held concurrently, on
  top of whatever cash and `max_new` (New Stocks a Month) already allow — a breakout is skipped for want of a
  free slot (`skipped['slots']` / `skipped_slots`) even with cash in hand and room under the monthly cap; it
  is its own skip reason precisely because `held`/`cash`/`month_cap` were not catching this case before. 10 is
  not arbitrary: it is exactly what the default ₹1,00,000 capital ÷ ₹10,000 per stock already implied, so the
  default run's numbers are unchanged by its addition (verified: `max_holdings` in the curve never exceeds the
  cap, checked on real F&O 2015-26 data). **With `compounding` also on** (Fixed ₹ mode only), the sizing formula
  changes: instead of `fixed_amount * (capital + realized) / capital`, the position size is the realised
  balance split evenly across the `max_positions` slots — `(capital + realized) / max_positions` — so `Amount
  per Stock` is NOT read in this combination (it only happens to match the default because ₹1,00,000 ÷ 10 =
  ₹10,000). This is "compounding using the other stocks": a stock that exits with a profit grows the balance,
  and the very next stock bought into its now-freed slot is sized off that larger balance — on real data,
  position sizes grow from ~₹10,000 (2015) to ~₹39,000 (2026) under this combination. With `max_positions=0`
  (off), compounding falls back to the plain capital-wide ratio exactly as before this feature existed — a
  test (`test_compounding_without_a_slot_cap_falls_back_to_the_capital_ratio`) pins that exact backward
  compatibility. `sweep()` also takes `max_positions` (not swept, taken from the page like the other sizing
  params) so Find Best Params honours whatever cap the page is set to.

Tests: `tests/test_monthly_rsi_breakout_engine.py` (synthetic months, days and weeks, the sweep/board unit tests, and both routes end to end on fake files).

## NIFTY Bi-weekly Iron Condor backtest (Mine group)

Added 2026-11-10 in the Mine group, the default strategy on /backtest until 2026-11-13 (Monthly RSI Breakout, above), from a Hindi video by its author
(rule-based, no chart or OI reading). `Backtest/biweekly_iron_condor_engine.py` (pure), routes
`/api/backtest/biweekly-iron-condor{,/optimise,/status/<id>}` (run on `_cr_start` like the call ratio),
params `#biweeklyIcParamsRow` (`bic*` ids), results in the call ratio's `#crResults` plus `#bicYears`, sweep panel
`#biweeklyIcOptimisePanel`, JS `runBiweeklyIc` / `runBicOptimise`.

* **Rule.** Friday 15:15 (Thursday on a Friday holiday) while India VIX < 13: SELL a CE and a PE at ≈0.20 delta,
  100-step strikes only, BUY each 200 further out, on the **bi-weekly** = the second weekly expiry after the Friday
  (Tue 11 days out; Thu 13 days out before 2025-09-01). Skip if net credit × qty < 4 % of capital. Out at ±1 % of
  capital (5-minute closes, booked at that close) or the next week's last session at 15:00. At 15:15 a profit
  ≥ 0.8 % / loss ≥ 0.9 % is closed rather than carried overnight (the author's habit; boxes, 0 = off).
* **Adjustments**, on 5-minute closes, a side = its short + hedge closed and re-opened together: premium doubled,
  delta ≥ 0.30, or one side's delta > 2× the other's → that side moves OUT to ≈0.20; a short decayed 50 % is rolled
  IN to ≈0.20 matching the other side's premium. Rule 3 with the high side already at ≈0.20 rolls the low side in
  instead (the author: "it is 0.2 anyway"); an OUT move is only taken if it lands nearer 0.20, so nothing churns.
  Brokerage `_OS_BROKERAGE_PER_LEG` (₹100) × every leg opened, adjustments included.
* **Delta** is Black-76 off the leg's own premium; the forward is put-call parity at the entry ATM, then NIFTY +
  that basis shrunk with time. Entry pair: nearest delta each side, unless the premiums are > 20 % apart; then the
  side nearer 0.20 stays and only the OTHER side moves to the strike priced nearest it (the video's 0.23 CE at 57
  against his 0.20 PE at 58). Until the evening of 2026-11-10 it took the closest-premium pair of all four strikes,
  which walked BOTH sides in (0.24 / 0.22 on 2026-06-19) and let thin weeks clear the 4 % floor by selling nearer.
  On one close a target/stop outranks the day-end rule it also trips (2026-01-16 read "Day-end loss" at −1.20 %).
* **The adjustments read prices only, never the P&L**, so one path per (delta, gap, adjust) serves every
  target / stop / day-end combo: `score_week` builds it once, and a test holds every sweep row equal to
  `run_trade`. `SWEEP_GRID` is 9,216 combos since the calm key (VIX ceiling has no "off", so a calm-filter week costs 2 contracts).
* **India VIX** is Fyers minutes via `minute_candle_store` (`NSE:INDIAVIX-INDEX`, file `fyers_INDIAVIX.pkl`,
  fetched on first run); no VIX with Max VIX above 0 is an error.
* **Contract window.** Thursday-era weeklies are sold 13 days out, past `cre.contract_window`'s 12 days, so
  `bic.contract_window` widens those to 16 (Tuesday weeklies and monthlies keep the shared window and its cache).
  `_cr_start`'s `frame_for` grew a `window=` argument for it, and its `breeze_requests` counter (the sweep budget,
  removed with Matrix Calendar) is back.
* **Capital is ₹ per NIFTY unit** (`capital_per_unit`, ₹1,280 = the author's ₹9.6 L on 10 lots of 75), × the lot size
  × sets; no historical SPAN. Per unit since 2026-11-10: it was ₹96,000 per set on today's 65 lot, which priced
  every credit 13 % low against capital and failed 19 of 22 calm Fridays on the 4 % floor (2 trades in the year).
  **Sets default to 10** (the author's): brokerage is ₹100 a leg at any size, and at 1 set the 10-14 legs of an
  adjusted week outweigh the 1 % target. An ATM with no candle at the entry falls back ±200 (`ATM_FALLBACK`).
  First real run (Nov 2025 – Nov 2026): 29 of 51 Fridays VIX ≥ 13 — that rule, not a bug, is the main skip.
* **3 % a month is out of reach** (checked the night of 2026-11-10 at the user's ask): over every board combo on cached
  2020-26 the best average month was 0.71 % (1.69 % in 2024-26 alone, in-sample), median ≈ 0. It trades ~1.5 Fridays a
  month at +1-1.5 % a win, so ~2 % is the ceiling even with no losses (the author's own claim: 2-2.5 %). The Per Month
  card (`summarise`: `avg_month_pct`, `months_3pct`, `losing_months`; idle months count as 0) shows it per run.
* **Amount needed** (2026-11-10 night): the form's description (`#bicNeed`, `updateBicNeed`, live off Sets / Capital % /
  Hedge Gap / Stop) states the capital at NIFTY's 2020-26 high (26,400 × 5 % × qty: ₹8.58 L at 10 sets) and that one
  trade can lose at most the hedge width × qty; after a run the Max Amount Needed card is `summarise`'s
  `amount_needed` = the largest traded week's capital + max drawdown (and `max_trade_loss`).
* **Calm-market filter** (`max_rv` 10 / `max_week_move` 2, boxes "Max NIFTY 10-Day Vol %" / "5-Day Move %", 0 = off;
  `calm_reading` / `calm_skip`; NOT in the video, added the night of 2026-11-10): NIFTY's 10-session realised vol and
  5-session move, each session read at the entry time, today's included. Why: on cached 2020-26 the MEDIAN sweep combo
  lost money (net ÷ DD −0.34); with the filter the share in profit went 16 → 62 % (2020-23) and 48 → 77 % (2024-26), the
  only filter tried that helped both halves (VIX change, realised vol alone, 20-day drawdown helped 2024-26 only).
  Through the engine (5-minute bars, real cache, 5 % capital): author's defaults 83 trades ₹25 k DD ₹57 k → 54 trades
  ₹88 k DD ₹29 k (2021 still −₹20 k); VIX 14 · Δ0.15 · hedge 300 · adjust on · credit 3 · T1.5/S0.75 · no day-end + calm:
  71 trades ₹1.97 L DD ₹27 k, every traded year positive (2021 only +₹0.4 k). Borderline weeks flip with how the
  daily price is read (an analysis on 1-minute closes had shown ₹1.11 L / DD ₹20 k), so the edge is real but not sharp. The sweep has a `calm` key (on/off), so the grid is 9,216 combos; a week the filter
  drops has no "on" row, and `_KEYS` shares one key tuple per combo across Fridays to keep that board's memory down.
* **Capital follows NIFTY** (`capital_pct`, the page's "Capital, % of NIFTY" = 5, since 2026-11-10 night;
  `week_capital`): each Friday's capital = that % of NIFTY at the entry × qty, so the 4 % credit floor and the ±1 %
  exits are on the margin of that week's index. The author's ₹9.6 L / 750 units is ≈5 % of NIFTY 25,700. With the
  fixed ₹1,280 a unit (still there: % = 0) 2023's NIFTY ~18,000 failed the floor on 46 of 52 Fridays. Cached
  2020-26, VIX 16 · Δ0.15 · hedge 300 · no adjust · T1.5/S2: 61 → 133 trades, ₹3.27 L → ₹4.90 L, DD ₹67 k → ₹93 k;
  the defaults 49 → 84 trades. VIX (the author's rule) is then the main skip: 2020-22 sat above 16 most weeks.
* **The sweep's Day-end "On" is `sweep_base`**: the page's two boxes, or 0.8 / 0.9 where a box is 0 (what applying
  a Day-end Off row leaves), and every board row carries the levels it ran with so applying it writes them back.
  Until 2026-11-10 night an Off row's zeros made the next sweep's "On" rows run with no day-end rule, and the board
  no longer matched the backtest. On cached 2020-26 data, 25 random combos × 350 Fridays: board = backtest.
* **Breeze history goes over one pooled keep-alive session** (`icici_data_service._HistoryTransport`, since
  2026-11-10 night): the SDK's `get_historical_data_v2` was a fresh connection per request with no timeout, and
  ~11 connects every 10 minutes hung for the OS's 75 s, so a 2020-26 condor sweep ran ~6.6 s a request. Only the
  `historicalcharts` GET is re-routed (5 s connect / 30 s read, 2 connect retries); orders keep the SDK's path. A
  sweep also waits (`_cr_yield_to_foreground`) while any ordinary backtest runs.
* **Cost:** ~6 Breeze contracts a traded week plus 2-3 per adjustment, cached after. Nov 2025 – Nov 2026 at 10 sets
  (real data): 12 trades of 51 Fridays, 7 W, +₹2.2 k, DD ₹28.2 k, PF 1.05 — wins ≈ +₹6.1-7.9 k, stops −₹8.9-10.7 k.
* **A target/stop crossed inside a session is booked AT its level** (`booked`, since 2026-11-10 night; the straddle's
  `exit_fill` rule), with the legs held before that close; a session's first close past it is booked at that close,
  `Target (gap)` / `Stop loss (gap)`. Booking the close had shown stops at −1.18 / −1.23 %. P&L % still sits below
  −1 % by the brokerage (₹100 × legs: 24 legs on 2025-12-05 = −1.29 %); Worst shows the −1.00 % booked. Tests: `tests/test_biweekly_iron_condor_engine.py`
  (a synthetic Black-76 market).

## NIFTY Monthly Call Ratio backtest ("No-Brainer")

Added 2026-11-03 under 🧪 Testing on /backtest, from an interview transcript (the author trades it by hand,
zero adjustment). `Backtest/call_ratio_engine.py` (pure), routes `/api/backtest/call-ratio{,/optimise,/status/<id>}`,
panels `#callRatioParamsRow` / `#crResults` / `#callRatioOptimisePanel`, JS `runCallRatio` / `runCrOptimise`. Last
Friday of the month (or the day after the monthly expiry) at 15:16, on **next** month's expiry: BUY 1 CE at
ATM(100-step) + D, SELL 2 CE 300 above, BUY 1 CE 1000 above the sold strike. D starts at 300 and walks out in
100s while the downside credit (2·sell − buy − hedge) is above 1 % of capital; a downside debit above 1 % is
not traded. Exits on mark-to-market: +2.5 % / −3 % of capital, NIFTY closing at/above the bought strike, or 5
sessions before expiry. **Capital is the page's ₹ per set** (no historical SPAN; the author's live 5 sets ≈
₹6.6 L), so every % is on that guess.

* **Data is 5-minute bars, one fetch per contract over its whole life** (`contract_window`: a few days
  before the previous monthly expiry → the expiry), via `IciciAdapter.historical_option_range` — 2-3 Breeze
  requests a contract in its own 17-day windows (`_RANGE_CHUNK_DAYS`: ≤13 sessions = 975 bars, under the
  1,000-row cap; the shared 10-day grid took 4-5), disk-cached whole once the expiry has passed. A range
  Breeze answered **empty** (`{'Success': [], 'Status': 200, 'Error': None}` — logged as "error: 200") is
  cached as `[]`; a range with a **failed** chunk is never cached, empty or not. The first version read 1-minute bars a
  session at a time (~60 requests a month) and crawled: on 2026-11-03 in market hours a year sat at 2/12
  months after 5 minutes. The window is canonical so the backtest and the sweep share one cache entry.
* **The backtest and Find Best Params book a trade through the same `build_path` + `first_exit`**, and a
  test holds a sweep row equal to the backtest of its params. The sweep (`SWEEP_GRID`, 34,560 combos: entry
  day × min distance × sell gap × hedge gap × max credit × target × stop × exit-before × spot exit) walks
  each expiry's strike structures, builds one path per distinct strike set and replays every exit combo on it
  (~1 s CPU a month). Its cost is Breeze: ~22-30 contracts a month cold (≈800 requests a year out of the
  shared 5,000/day) — run it after hours; a re-run is cache-only. Breeze is latency-bound (the first
  year-long sweep, 2026-11-03 in market hours: 1,116 requests in 60 min on 3 threads), so `_cr_workers` runs
  3 threads 09:00-15:45 IST (the live algos share the 1.5 req/s pacer) and 8 outside.
* **A month still to expire is priced on the listed expiry** (`contract_expiry(..., listed)`, from the
  ICICI master): 2026-11-24 is Guru Nanak Jayanti, the contract expires the 23rd, and asking Breeze for the
  24th came back empty for every strike. Settled months keep the date their sessions give them.

Tests: `tests/test_call_ratio_engine.py`.

## NIFTY ATM Straddle backtest — monthly or weekly (was the weekly ATM Straddle / Option Selling)

The ⚖️ straddle in the Mine group on /backtest. **Monthly since 2026-11-03**: once a month on the Entry Day box
(`entry_day`: last Friday by default, last Mon–Thu, or `after_expiry` — `call_ratio_engine.entry_days`) at
15:15, SELL the ATM CE + PE (50-step) of NEXT month's expiry; both legs out together at +`target_pct` /
−`stop_pct` (5 / 5) **% of the premium collected**, marked on 5-minute closes (the backtest also reads seconds, below). **A position trade: no exit time**
(dropped the evening of 2026-11-03, with the exit-before-expiry box) — only a contract that reaches its expiry
untouched is closed, at that day's last 5-minute close, reason `Expiry`. Until then it was a weekly 09:20 → 15:14 intraday straddle with a ₹
stop per leg; the strangle that preceded it (OTM / SL % / re-entry) went 2026-11-01. Both left their names:
`Backtest/option_selling_engine.py`, the `/api/backtest/option-selling*` routes and the `#optionSellingParamsRow`
/ `os*` ids.

* **Exit Check since 2026-11-05: 1 minute · 3 minutes · 5 minutes** (`CHECK_SECS`, `fine_exit`, `check_steps`: a span's close stamped at its
  last second). 1-second and 30-second went the same evening: their data was only ever there for part of a run (a row needed ~800-1,500 requests,
  half its bars read closes meanwhile). Old values, and Saved rows carrying them, read 1 minute (`OLD_CHECKS`); the 1-second slices are still on disk, unused.
  A bar's highs/lows bound what the MTM can have reached (`mtm_bounds`), and only a bar that could reach a level is read on the finer chart. The exit books
  that close's real prices, not the level. A leg yet to print today does not count at the open. A bar without data reads its 5-minute close and is counted (`bars_unchecked`); a backtest may spend
  `_cr_second_budget()` requests (400 in market hours, else 1,500) and a re-run carries on from the cache. Adjustments and the entry stay on 5-minute closes.
  **A session's first `open_settle_secs` (60, the Open Settle box, 0-300) are not read, but the close that ends them is** (until the evening of 2026-11-05 the first candle's stamp, a second short, kept it out): 2026-02-03's 25100 CE printed 1049 / 1242 / 921 / 800 / 732 in its first 5 s with ~700 of intrinsic. At 1 minute it exits 09:16:00 on CE 603.60 / PE 20.85. StockMojo's "09:16" row (CE 655.15 / PE 13.15) is the candle NAMED 09:16, which closes at 09:17:00.
  **Exit times are the close's real time** (`_STAMP_TO_CLOSE`): inside the engine a close keeps its span's last second so it stays in its own 5-minute bar, and the time shown is one second on: 09:16:00, 09:18:00 at 3 minutes, 09:20:00 at 5.
  **The live straddle does not wait 60 s**: its first read is 09:15:05-09 (`_OPEN`). Kept on purpose (decided 2026-11-05): of 25 straddles through a 25 % stop at the first poll (461 cached openings), 22 were still through at 60 s and 1 stayed back inside for 15 min; nothing rests at the broker during a wait.
  Same weekly row: 1m ₹1,18,014 · 3m ₹1,09,538 · 5m ₹92,848. A coarser check is not a safer one: it books later prices, not truer ones.
* **Find Best Params sweeps each Exit Check on its own chart** (`SWEEP_CHECKS`: 5m, 1m, 3m; since the evening of 2026-11-05). The whole grid, its own `Fold` and its own `board`, one chart at a time, so a chart's best rows are not the 5-minute winners re-read. It was a re-check of the 5-minute board until then. `fine_sweep` puts a path's MTM on every close of the chart and decides every combo at once (`_first_hits`, shared with `first_exits`), capped at each bar's own high/low (`up` / `down`) because `fine_exit` does not read a bar a level cannot reach; a test holds it equal to `run_trade` combo for combo, and on real data 4,800 sampled combos differed in none. ~17 ms a path, ~74,000 paths: ~20 min of CPU a chart, on top of 5 minutes' ~4, so run it after hours.
  **Its cost is 1-minute data: 56,754 contract-days for 2019-26** (weekly 16,769, of which 13,341 on disk on 2026-11-05; monthly 42,137, 5,844 on disk; 3m re-uses them), one request each. `_cr_sweep_budget()` spends 400 in market hours, else 3,500, weeks first. A row with a bar short of data shows ≈ (`complete`, `bars_unchecked`; it counts every bar up to the exit, so it can be cautious) and its figure moves as data arrives: Recalculate carries on from the cache. `rank_board` lists complete rows by net, then the part-read, then the 5-minute rows last whatever they earn; set to 5 minutes the box runs that chart only.
* **A part-read figure (≈) is not final, and that is the only way a row and its backtest differ.** Same data: board, sweep and backtest agree to the rupee. Rewinding the 1-second cache by `slices.saved` reproduced a Saved ₹5,75,010 / PF 2.15 exactly. Each run's fetches move the next read. Saved and the backtest result carry the ≈.
* **It runs on the Monthly Call Ratio's machinery** (below): `call_ratio_engine`'s calendar (`entry_days`,
  `contract_expiry`, `contract_window`, `exit_day`), and the routes go through `_cr_start` (`parse=_os_parse`,
  `frame_for(expiry, strike, right)`), so tasks live in `_cr_tasks` and the option-selling status routes
  delegate to `call_ratio_status`. Two contracts a month, cached like the call ratio's.
* **A price "at" a time is the close of the 5-minute bar that has finished by then** (15:10 bar for 15:15),
  for the entry and the ATM spot. **Target/stop fill AT the level** (`exit_fill`, legs
  interpolated between marks) — until the evening of 2026-11-03 they booked the crossing bar's close, −6 to −9 % on
  a −5 % stop; a session that OPENS past the level fills at that open, `Stop loss (gap)`.
* **`contract_window` starts 10 days before the previous expiry** (was 3) — a last Friday that falls before
  its own month's expiry (Fri 09-25 vs Tue 09-29) opens the month after, and 5 of 12 entries in 2025-26 sat
  outside the old window as "No ICICI premium at entry". The Call Ratio had the same hole; its cache keys moved.
* **Adjustments** (added 2026-11-03, `adjust` / `adjust_pts` / `max_adjust`, default none / 200 / 2): when a
  NIFTY 5-minute close is `adjust_pts` from the centre (the sold strike, then the last adjustment's ATM), at that
  close `roll_untested` buys back the profit leg and sells that side at the new ATM, `recenter` re-sells both
  there, `hedge` BUYS the losing side there (once a side); never on the expiry day, and a new strike with no
  premium at that bar waits for the next one. The path is a list of legs (`build_path` → `_adjust` →
  `_assemble`; closed legs freeze at their closing mark), target/stop stay % of the FIRST premium, brokerage is
  ₹100 per leg traded (`legs_traded`), and a stop/target on the adjustment's own bar wins (it is a level fill
  inside the bar; the adjustment is at the close).
* **Weekly cadence** (`cadence='weekly'`, the page's **Expiry** box, added the evening of 2026-11-03): one trade a
  WEEK on `entry_day` Monday … Friday (the session before it in the same week on a holiday) or the session after
  each weekly expiry, selling the ATM CE + PE of the first weekly expiry AFTER the entry (`weekly_expiry`); NIFTY
  weeklies exist from 2019-02-11 (`WEEKLY_SINCE`, earlier weeks skip). The sweep's period is the ISO week
  (`entries_by_month(..., cadence='weekly')`). A weekly-only expiry is fetched over its own fortnight
  (`call_ratio_engine.contract_window` → `is_weekly_expiry`, judged on the expiry WEEKDAY of its week so a
  holiday-moved monthly stays monthly); a monthly expiry keeps its window, so both cadences share that cache.
* **🔖 Save** (2026-11-04): the Best Params board's 🔖 column saves a row to the page's 🔖 Saved list
  (`window.BtSaved`, localStorage `bt_saved_strategies_v1` — this browser only), keyed on every swept setting
  plus the range the board ran on; the Saved list's USE fills the straddle form (`applyStraddleOptResult`).
* **Compounding** (backtest only, added 2026-11-04; `compounding` / `start_capital` / `capital_per_lot`, ₹2.5 L / ₹2.5 L since 2026-11-04 — ₹2 L before):
  `compound()` re-sizes each trade to ⌊balance ÷ per lot⌋ lots (min 1, max `MAX_LOTS` 27 = the freeze size),
  the balance being the start plus the net ₹ of trades already CLOSED at that entry (a monthly trade still open
  when the next is sold has not paid yet); ≤ 0 stops with 'Capital exhausted'. Brokerage is per leg, so it does
  not scale. Find Best Params still ranks at a fixed lot.
* **Skip one-night trades** (`skip_one_night`, weekly only, added 2026-11-04; a sweep key, On rows reuse the Off
  rows and blank the weeks whose expiry is the next session — no extra paths or contracts). Why: Mon 2026-02-02
  sold the 25100 straddle for 188.95 expiring Tue 02-03; NIFTY opened +713 (25,097 → 25,810) and it lost
  −₹56.8 k (−462 %), all of that month's −₹57 k. Since the 2025-09-01 Tuesday expiry, EVERY Monday weekly entry
  is a one-night straddle, so a 7-year board topped by Monday rows is mostly Thursday-expiry history — "Monday +
  skip" has not traded since then. Selling the week after instead (tried) was worse: the 8-day straddle took
  the same gap (−213 %). Under Tuesday expiries (Sep 2025 – Nov 2026) the best weekly was Thursday · hedge_roll 100
  · T40/S12 · lock 15→0: ₹1.50 L, DD ₹6.7 k, worst month −₹2.9 k. The gap fill books the first bar's OPEN, and
  that 09:15 CE print was 1,050 against ~710 intrinsic (bar close 699) — about ₹23 k of the −₹56.8 k is the
  auction print; left as is (filling at the bar close helped Monday rows and hurt Wed/Fri ones).
* **`hedge_roll`** (added the evening of 2026-11-03) does `hedge` and `roll_untested` at the same trigger —
  the same strikes, so it costs no extra contracts.
* **Profit lock** (`lock_at` / `lock_to`, % of premium, 0 = off): from the bar after a 5-minute CLOSE at/above
  `lock_at`, a stop rests at `lock_to` (filled at the level, at the open on a gap), reason `Profit lock`;
  it counts only while above the ordinary stop. Pure exit logic — no contracts.
* **Wings** (`wings`, points, 0 = none): BUY the CE `wings` above / the PE `wings` below at entry (iron fly);
  the % are then of the NET credit (`premium` = sold − wings, `sold_premium` kept beside it); a missing wing
  skips the month. **A backtest box only, not in the sweep**: over Dec 2019 – Nov 2026 ±300 / ±500 never beat
  the same setups without them on net or net ÷ DD, and they cost ~2 contracts per entry day.
* **Find Best Params** sweeps **both Expiry choices** (`CADENCES`, the first `SWEEP_KEYS` field; the page's own
  Expiry box is not read) × entry day × skip one-night (weekly) × `WINGS` (0) × `ADJUST_VARIANTS` (none + 4 types × 7 trigger points) ×
  target × stop × `LOCKS` (off + 7; a lock at/above the target is not a combo) = 289,710 combos, one path per
  (period, entry day, wings, adjustment) (`score_month` → `score_expiry`). Each cadence is folded oldest first
  on its own and needs half of ITS periods closed (`Fold.rows({cadence: n})`; weeks before 2019-02-11 are
  not periods). A weekly year is ~950 contracts cold (~10 min); Nov 2025 – Nov 2026 is warm.
  **Cached, its CPU is ~0.3 s a month / 0.2 s a week** (was ~2 s / 1.2 s until the night of 2026-11-03):
  `score_expiry` keeps one `cache` per entry (the bar grid, each contract on the grid, each leg's arrays)
  across the 29 adjustment variants, `first_exits` decides every target/stop/lock combo of a path at once
  (running max/min + searchsorted; a test holds it equal to `first_exit` combo for combo), and each distinct
  (bar, reason, level) is booked once. 1.5 M real combos and the whole 83-month board came out identical. Months are folded oldest first as
  they finish (`Fold`, the shared rank's scoring plus `net_dd` = net ÷ max DD) so a long range never holds
  every month's combos; the board (`board`) is the top 100 by net plus the top 100 by net ÷ DD among combos
  earning ≥ ¼ of the best net. A test holds sweep rows equal to the backtest of their params.
  **Its cost is the adjustment strikes**: ~36 contracts a month cold (every 50-strike NIFTY crossed, both
  sides), ~7 s each at Breeze's pace — a year is ~430 contracts / ~1 h; 2020-26 (~3,000) is over a day's
  5,000-request quota, so warm it a year at a time after hours. Dec 2019 – Nov 2026 is warm (2,694
  contracts, plus the ±300/±500 wings); cached, the 83-month sweep is ~4 min of CPU.
* **What the 2019-26 sweep said** (lot 65): best net Last Wed · roll 100 · T50/S20 ₹4.71 L (DD ₹54.5 k), and
  with a 30→10 lock ₹4.50 L (DD ₹50.8 k); steadiest Last Tue · hedge 100 · T20/S8 · lock 15→0 ₹2.66 L with
  DD ₹12.5 k — ₹1.26 L / ₹1.40 L in each half (Dec 2019 – May 2023 / Jun 2023 – Nov 2026) at the same DD.
  A lock cut DD 30-40 % on most roll / re-centre rows for a few % of net.

Tests: `tests/test_option_selling_engine.py`.

## Heikin-Ashi + SuperTrend backtest — removed

Removed 2026-11-01 (it sat under 🧪 Testing on /backtest): `Backtest/ha_supertrend_engine.py`, the two
`/api/backtest/ha-supertrend{,/status/<task_id>}` routes, its option, params row (`#haSupertrendParamsRow`),
logic card, JS and both test files. It was a daily many-stock run that borrowed the Weekly CPR Breakout ₹
cards (`isWcb`); those are Weekly CPR Breakout's alone again.

## Pivot Confluence backtest — removed

Removed 2026-11-01, after Scalp Pullback (both sat under 🧪 Testing on /backtest):
`Backtest/pivot_confluence_engine.py`, the three `/api/backtest/pivot-confluence{,/optimise,/optimise/status/<id>}`
routes, its option, params row, Best Params panel and JS. **Kept on purpose: the Lots / Lot Value row**
(`#pivotConfluenceLotRow`, `pcLots`, `pcLotValue`, `updatePcInvestment`, the `isPc` ₹-card switch) — about ten
other backtests (ST Touch Break, Simple Trader, MA HTF, the Quantman ones) "share the Pivot Confluence Lots
boxes" and still do, under the old ids. Its `*_pc_multiTF_*` sweeps are dead entries in `rtp_opt_cache.json`.

## 2-Min EMA Scalp Pullback backtest — removed

Removed 2026-11-01 (it sat under 🧪 Testing on /backtest): `Backtest/scalp_pullback_engine.py`, the
three `/api/backtest/scalp-pullback{,/optimise,/optimise/status/<id>}` routes, its option, params/lot rows,
Best Params panel and JS. Kept: `_sp_instrument_token` in `api.py` is the index → token map about fifteen
other backtests call, and still carries the name. Its old sweeps (`*_sp_multiTF_*` keys) are dead entries in
the shared `rtp_opt_cache.json`.

## NIFTY Matrix Calendar backtest — removed

Added 2026-11-06 (Mine group, the default on /backtest) and removed 2026-11-07 at the user's request; the commit that added it is `adee85a`.
`Backtest/matrix_calendar_engine.py`, the three `/api/backtest/matrix-calendar*` routes, its option, panels, JS and tests went, and `st_all_indicators` was the default again until the Bi-weekly Iron Condor (above) took it on 2026-11-10.
With its Min IV 20 it traded 62 of 338 Mondays (2020-26), +₹16,961, three-quarters of that from seven 2026 trades, and none in 2023. Its cached Breeze contracts stay on disk, as any cached contract does.

## RSI Credit Spread / Iron Condor backtests — removed

Both added 2026-11-01 and removed the same day: `Backtest/rsi_credit_spread_engine.py`,
`Backtest/rsi_iron_condor_engine.py`, the four `/api/backtest/rsi-{credit-spread,iron-condor}*` routes,
their /backtest options, panels and JS, `icici_data_service.historical_option_daily` (Iron Condor was its
only caller) and both test files. `Pine script/RSI Iron Condor 1-1 Strategy.pine` is a TradingView script,
not part of the app, and was left.

## The env file is read by threads and rewritten whole

`env/<user>.env` holds the broker credentials and every per-account flag.
Every broker login and the Algo tabs' toggles rewrite it in full, while the
algo threads, the Telegram listener and every request read it through
`UserEnvManager`'s process-wide cache.

Until 2026-09-23 the write truncated in place and the read was an ordinary
`open()`, so a reader that arrived mid-write parsed a **prefix** and cached
it as the whole file — every flag below the cut then read as unset for the
life of the process, until an unrelated save happened to clear the cache.
That silently skipped four live Telegram calls with "no broker has
`BROKER_N_TG_ACTIVE=true`" on 2026-09-21 (12:08, 13:06) and 09-22 (09:18,
09:21): the prefix reached `BROKER_1_TYPE` but stopped before
`BROKER_1_TG_ACTIVE`. Now:

* `_atomic_write_lines` replaces the file (temp, fsync, `os.replace`), so a
  reader sees either the old file or the new one, never half of one;
* `_read_env_file` takes the size before and after and only trusts a read
  that accounts for the whole file — three failures return `None` and the
  caller **caches nothing** rather than caching a prefix;
* the cache is only ever replaced wholesale under `_cache_lock`;
* `take_call` treats "no eligible broker" as suspect: it clears the cache,
  re-reads the file and only then skips the call.

The cache is still only invalidated by a save, so a **hand edit** of
`env/Mine.env` needs a restart to be picked up reliably.

Tests: `tests/test_user_env_reads.py`.

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

## ST + BB + RSI live algo — removed

The three **ST + BB + RSI** tabs (NIFTY / BANKNIFTY / SENSEX) were removed on 2026-10-25: tab
and `algo_stbb.html`, the 15 `/api/algo/st-bb-rsi*` routes, the `stbb` / `stbb_bn` / `stbb_sx`
dispatcher strategies and their `_auto_sl` line, and the start of their three threads. The
**Backtest** strategy (`Backtest/st_bb_rsi_engine.py`, `/api/backtest/st-bb-rsi*`) stays.
Deliberately still there because Simple Trader subclasses it: `algo/st_bb_rsi/st_bb_rsi_algo.py`
(`StBbRsiAlgo`, `Profile`; `NIFTY` is only its default profile), `service/st_bb_rsi_live_engine.py`,
`_register_stbb_routes` (Simple Trader's routes), `static/js/algo_stbb.js` and the `stbb_start` /
`stbb_watchdog` jobs (now Simple Trader's). The `STBB_*` / `BROKER_N_STBB_*` env variables and the
`st_bb_rsi*_state/_trades_history.json` files are dead but untouched. Tests that still cover the
base: `tests/test_st_bb_rsi_live.py`, `tests/test_st_bb_rsi_engine.py`.

## Removed algos

The EMA RTP live algo (five timeframe variants, `algo/rtp_railway_track/`)
was removed on 2026-09-15, with its `/api/algo/rtp*` routes, Algo-page tab,
scheduler jobs and tests. Its **backtest** went the same evening:
`Backtest/rtp_backtest_engine.py`, the `/api/backtest/rtp*` routes and the
RTP strategy on `/backtest`. Two things kept the name: the shared optimiser
cache is still `app/utils/rtp_opt_cache.json` (2nd-Candle and other
sweeps write it; Scalp / Pivot did until 2026-11-01), and `#rtpStatsRow` is still the id of the shared Row-2
stat cards. The per-user `EMA_RTP_*` / `RTP_*_STRIKE_MODE` /
`BROKER_N_RTP_*` variables were dropped from `env/Mine.env` on 2026-10-20
(backup `scratch/Mine.env.bak-20261020-rtp`); nothing reads them. EMA Confluence (`algo/ema_confluence/`) was **not**
removed — it is a separate algo and still runs (see "EMA Confluence live
mode" above).
