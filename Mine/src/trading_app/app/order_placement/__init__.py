"""The Order Placement page's automatic side (``/orderplacement``).

The page itself places one order and walks away — "placement only", as its
module docstring puts it. This package is the trade it does not place by
hand: a call posted to a Telegram channel, read as it lands, entered as a
stop-limit and managed to its end by a background engine.

Modules, deliberately separate:

* ``signal_text``       — parsing a tip into a plan. Pure; touches nothing.
* ``tg_calls_listener`` — the Telethon channel watcher. Reads, never trades.
* ``tg_call_engine``    — the state machine, and the only thing here that
  reaches a broker.
* ``tg_call_store``     — the plan's durable state and the P&L ledger.
* ``op_signal_store`` / ``op_signal_engine`` — the shared constants and
  broker helpers the engine is built on (what remains of the hand-armed
  signal mode, removed 2026-09-15).

Every order this package places is an ordinary ``MineOrderStore`` record
carrying ``strategy='op'``, which is what keeps the page's existing edit,
cancel, reconcile and exit-all paths working on its legs unchanged.
"""
