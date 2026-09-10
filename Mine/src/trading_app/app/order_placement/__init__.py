"""Signal mode for the Order Placement page (``/orderplacement``).

The page's first mode places one order and walks away — "placement only", as
its module docstring puts it. This package is the second mode: a pasted tip
("NIFTY 23500 CE, BUY 193, SL 175, targets 206/213/220") armed as one entry
stop, and then managed to its end by a background engine that attaches the
stop and the targets when the entry fills, cancels the losing siblings, and
ratchets the stop up behind each target.

Three modules, deliberately separate:

* ``signal_text``  — parsing a tip into a plan. Pure; touches nothing.
* ``op_signal_store`` — the plan's durable state, written atomically.
* ``op_signal_engine`` — the state machine, and the only thing here that
  reaches a broker.

Every order this package places is an ordinary ``MineOrderStore`` record
carrying ``strategy='op'``, which is what keeps the page's existing edit,
cancel, reconcile and exit-all paths working on signal legs unchanged.
"""
