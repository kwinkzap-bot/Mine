# Swing Momentum — Backtest → Go Live → Broker Order Flow

This document explains how the **Swing Momentum Portfolio** strategy goes from a backtest to a
live config, and **how stock orders are actually placed to the brokers**.

> **Key point:** order placement to brokers already exists and is fully wired end-to-end.
> Selecting a connected broker in the **Go Live** popup is what makes the app place real
> **CNC MARKET BUY** orders on Fyers / Dhan / Zerodha / Kotak. With no broker selected, the
> config is saved as **track-only** (no orders are placed).

---

## 1. End-to-end flow

```
Backtest page (templates/backtest.html)
  └─ strategy = "📊 Swing Momentum Portfolio"
  └─ POST /api/backtest/swing-momentum  → SwingMomentumEngine.run()   [backtest only]
  └─ "🚀 Go Live" button (#smGoLiveBtn, shown only for swing_momentum)
        │  static/js/backtest.js
        ▼ _smGoLiveFromForm()  (reads form params)
        ▼ _smOpenGoLiveModal() → popup #smGoLiveModal
        │     • editable: investment, monthly SIP, index, top N, exit rank, rebalance
        │     • Broker dropdown #glBroker ← GET /api/available-brokers
        │       (only is_logged_in brokers are selectable)
        ▼ "Confirm & Go Live" (#glConfirmBtn) → _smSubmitGoLive()
        ▼ POST /api/algo/swing-momentum/configs
              { …params, broker_instance?, broker_type?, broker_name? }
              │  src/trading_app/app/routes/api.py → sm_live_configs_add()  (line 8137)
              ├─ _sm_compute_today_rankings(index)  → top-N momentum ranking (avg of 3M/6M/9M return)
              ├─ allocate equal ₹ per slot → build live_entries[{symbol, entry_price, qty, entry_date}]
              ├─ IF broker_instance present:
              │     _sm_place_portfolio_orders(...)   ← PLACES REAL ORDERS
              ├─ append new config (8-char id) to sm_live_configs.json   ← "one more live entry"
              └─ return { success, config, broker_summary }
        ▼ FE shows "✅ Placed N orders on <broker>" then redirects to /algo#swing-momentum
```

### Relevant files
| Concern | Location |
|---------|----------|
| Backtest engine | `src/trading_app/Backtest/swing_momentum_engine.py` |
| UI + popup + submit | `static/js/backtest.js` — `_smGoLiveFromForm` (:1444), `_smOpenGoLiveModal` (:1454), `_smSubmitGoLive` (:1522) |
| Go Live button markup | `templates/backtest.html:28` |
| Backend create route | `src/trading_app/app/routes/api.py` — `sm_live_configs_add()` (:8137) |
| Persisted config store | `src/trading_app/algo/swing_momentum/sm_live_configs.json` |

---

## 2. How the stock order is placed to the broker

When a broker is selected, the backend (`src/trading_app/app/routes/api.py`) runs this chain:

### a. `_sm_place_portfolio_orders(username, instance_num, broker_type, broker_name, live_entries)` — :7943
Builds a broker client, loops over every holding, places one **CNC MARKET BUY** each, waits ~2s,
reads back the average fill price, and writes `entry_price` + an `order` record onto each holding.
Returns a `broker_summary` like `{placed, failed, broker, error}`.

### b. `_sm_build_order_service(username, instance_num, broker_type)` — :7841 (credential resolution)
Reads per-broker credentials via `UserEnvManager.get_user_var(username, "BROKER_{n}_<FIELD>")`
plus session tokens, and returns the broker client (or `None` if creds/token missing →
"not connected"):

- **Fyers** → `FyersOrderService(app_id=BROKER_n_APP_ID, access_token=session['fyers_{n}_access_token'] or BROKER_n_ACCESS_TOKEN)`
- **Dhan** → `DhanOrderService(access_token=BROKER_n_ACCESS_TOKEN, client_id=BROKER_n_CLIENT_ID)`
- **Zerodha** → `KiteConnect(api_key=BROKER_n_API_KEY)` + `set_access_token(...)` (raw kite client)
- **Kotak** → `KotakOrderService(consumer_key=BROKER_n_CONSUMER_KEY, ucc=BROKER_n_UCC)`

### c. `_sm_place_equity_order(broker_type, svc, symbol, qty, side='BUY')` — :7885 (the actual order call)

| Broker  | Order call | Notes |
|---------|------------|-------|
| Fyers   | `svc.place_order(symbol="NSE:{SYM}-EQ", side=1, quantity=qty, order_type=2, product_type="CNC")` | `order_type=2` = MARKET; `side=1` BUY / `-1` SELL |
| Dhan    | `svc.place_order(security_id=<symbol master>, transaction_type="BUY", quantity, order_type="MARKET", product_type="CNC", exchange_segment="NSE_EQ")` | needs `_symbol_master_data[symbol]` |
| Zerodha | `kite.place_order(variety=REGULAR, exchange=NSE, tradingsymbol=SYM, transaction_type=BUY, quantity, product=CNC, order_type=MARKET)` | raw KiteConnect |
| Kotak   | `svc.place_order(tradingsymbol=SYM, transaction_type="BUY", price=0.0, quantity, exchange_segment="nse_cm", product="CNC", order_type="MKT")` | MKT |

### d. `_sm_avg_fill_price(broker_type, svc, order_id)` — :7921
Polls each broker's orderbook for `tradedPrice` / `avgPrice` / `average_price` and sets the
holding's real entry price.

> The same helpers back **SIP/SWP top-ups** (`sm_live_sip_swp`, :8017) and **re-init**
> (`/configs/<id>/go-live`, :8265), so one code path covers all live order placement.

---

## 3. What you must have in place to place a *real* order

1. **A connected broker** — log the broker in (`src/trading_app/app/routes/auth.py`), which
   stores the access token in session and/or env. Only `is_logged_in` brokers are selectable
   in the popup.
2. **Credentials stored** under `BROKER_{n}_*` user-env vars (managed by `UserEnvManager`):
   `BROKER_1_ACCESS_TOKEN`, and per broker `BROKER_1_APP_ID` / `API_KEY` / `CLIENT_ID` /
   `CONSUMER_KEY` / `UCC`.
3. **Select that broker** in the Go Live popup before clicking **Confirm & Go Live**. Leaving
   it on *"None — track only"* saves the config but places **no** orders.
4. **Market hours / funds** — these are MARKET orders; they fill only when NSE is open and
   margin/funds are sufficient.
5. **Symbol tradeable on that broker** — Dhan requires the symbol in its symbol master; special
   tickers (e.g. `GVT&D`) may not map and are reported as failed in `broker_summary`.

---

## 4. Good-to-know gaps

- **No automated rebalancing/exit engine.** The APScheduler (`src/trading_app/app/scheduler.py`)
  runs only the RTP and Second-Candle intraday algos — **not** swing momentum. Exits
  (rank > `exit_rank`), monthly SIP, and rebalances are **manual**, via the SIP/SWP and re-init
  endpoints.
- Live configs are stored in a single JSON file, not a database.
- "Portfolio Filter" in the strategy name refers to the momentum-ranking + exit-rank rotation
  logic itself; there is no separate filter module.

---

## 5. How to verify

- Open the Backtest page → choose Swing Momentum → run a backtest → click **Go Live**; confirm
  the popup lists your brokers from `/api/available-brokers`.
- Select a connected broker → **Confirm**; check the returned `broker_summary`
  ("✅ Placed N orders") and that `sm_live_configs.json` gained a new config whose
  `live_entries[].order` records carry real `order_id` / `avg_price`.
- Cross-check the broker's own orderbook (and `fyersApi.log` / `fyersRequests.log` at repo root
  for Fyers) to confirm the order reached the broker.
