/* ================================================================
   ZerodhaCharges — what a round trip actually costs, per trade.

   Every live-algo tab used to deduct a flat figure per trade (₹135 a
   lot in algo.js, nothing at all elsewhere), which is wrong in both
   directions: a 44-qty ₹1,600 equity trade is nowhere near a
   3,114-qty one, and an option round trip is a different rate card
   from a cash-market one. This is the one place that knows the rates,
   so a P&L shown anywhere in the app is the money that actually
   lands.

   Rates are Zerodha's slabs per segment. Components and their
   rounding follow the brokerage calculator, so a figure here
   reconciles with the contract note rather than approximating it.

   The option rates are calibrated against Zerodha's sample contract
   note — NSE F&O options, buy 240.70, sell 277.05, qty 65 — which
   itemises brokerage 40.00, txn 11.96, STT 27.00, GST 9.36, stamp
   0.47, SEBI 0.03, total 88.82. Every component below is a rate on
   buy/sell/qty, not a copied figure, so any other premium and lot
   size scales off the same card. Note that the sample's STT and txn
   are heavier than Zerodha's currently published option rates (0.1%
   of sell premium and 0.03503% of turnover, which would total 79.15
   on that row) — these follow the note.

   The equity_intraday rates are calibrated the same way, against
   Zerodha's sample intraday-equity note — NSE, buy 5519.58, sell
   5651.30, qty 88 — which itemises brokerage 40.00, txn 30.18, stamp
   14.57, STT 124.00, GST 12.81, SEBI 0.98, total 222.54. This is the
   card the 30-Min Fakeout trades on: cash-market MIS, not options, so
   brokerage is 0.03% capped at ₹20 an order rather than a flat ₹20,
   and STT is 0.025% of the sell leg rather than 0.15% of premium.

       ZerodhaCharges.roundTrip('option', buyValue, sellValue)
           → { brokerage, stt, txn, sebi, stamp, gst, total }

       ZerodhaCharges.forTrade({ segment, entry, exit, qty, short })
           → total ₹, with the buy/sell legs worked out from
             `short` (true when the position was entered by selling)

       ZerodhaCharges.breakdownForTrade({ … same … })
           → the full component split for that trade

   `buyValue` / `sellValue` are turnover on each leg — price × qty —
   not the price. A long option bought at ₹250 and sold at ₹224 on a
   65-lot is buyValue 16,250 and sellValue 14,560.
   ================================================================ */

(function (global) {
    'use strict';

    const SEGMENTS = {
        // NSE cash market, intraday (MIS) — the 30-Min Fakeout's real orders.
        equity_intraday: {
            brokeragePct: 0.0003,     // 0.03% of the leg…
            brokerageCap: 20,         // …capped at ₹20 per executed order
            sttPct:       0.00025,    // 0.025% — sell leg only
            // 0.00307% of turnover. Zerodha's published NSE cash txn charge is
            // 0.00297%, but their calculator's "Exchange txn charge" line folds
            // the ₹10-per-crore SEBI turnover fee (0.0001%) into it while still
            // listing SEBI separately — 0.00297 + 0.0001 = 0.00307. Following
            // the note here, otherwise this line, GST and the total all read
            // light against a real contract note.
            txnPct:       0.0000307,
            sebiPct:      0.000001,   // ₹10 per crore
            stampPct:     0.00003,    // 0.003% — buy leg only
            gstPct:       0.18,
        },
        // NSE F&O options — flat ₹20 an order, and much heavier STT/txn than
        // the cash market, which is why the old flat per-lot figure drifted.
        option: {
            brokerageFlat: 20,        // ₹20 per executed order, no percentage
            sttPct:       0.0015,     // 0.15% of premium — sell leg only
            txnPct:       0.0003554,  // 0.03554% of premium
            sebiPct:      0.000001,
            stampPct:     0.00003,    // 0.003% — buy leg only
            gstPct:       0.18,
        },
        // NSE F&O futures — index/stock futures (EMA Confluence).
        future: {
            brokeragePct: 0.0003,
            brokerageCap: 20,
            sttPct:       0.0002,     // 0.02% — sell leg only
            txnPct:       0.0000173,  // NSE 0.00173%
            sebiPct:      0.000001,
            stampPct:     0.00002,    // 0.002% — buy leg only
            gstPct:       0.18,
        },
    };

    // Brokerage on one leg: flat per order where the segment says so, else the
    // percentage under its cap.
    function legBrokerage(cfg, value) {
        if (cfg.brokerageFlat != null) return cfg.brokerageFlat;
        return Math.min(value * cfg.brokeragePct, cfg.brokerageCap);
    }

    // Full round trip (entry + exit) given the turnover on each leg. Direction
    // doesn't matter here — the caller decides which price was the buy.
    function roundTrip(segment, buyValue, sellValue) {
        const cfg = SEGMENTS[segment];
        const zero = { brokerage: 0, stt: 0, txn: 0, sebi: 0, stamp: 0, gst: 0, total: 0 };
        if (!cfg || !(buyValue > 0) || !(sellValue > 0)) return zero;

        const turnover  = buyValue + sellValue;
        const brokerage = legBrokerage(cfg, buyValue) + legBrokerage(cfg, sellValue);
        const txn       = turnover * cfg.txnPct;
        const sebi      = turnover * cfg.sebiPct;
        const gst       = (brokerage + txn + sebi) * cfg.gstPct;
        // STT is levied in whole rupees. Stamp duty is not — the contract note
        // carries it to paise (₹0.47 on a ₹15,645 buy leg), so rounding it the
        // same way was quietly zeroing it on anything under ₹16,700 of premium.
        const stt       = Math.round(sellValue * cfg.sttPct);
        const stamp     = buyValue * cfg.stampPct;

        return {
            brokerage, stt, txn, sebi, stamp, gst,
            total: brokerage + stt + txn + sebi + stamp + gst,
        };
    }

    // Convenience for a trade record: entry/exit price + qty. `short` flips
    // which leg was the buy, so STT (sell) and stamp duty (buy) land correctly
    // on a sold-first position.
    function breakdownForTrade({ segment, entry, exit, qty, short = false }) {
        const e = Number(entry), x = Number(exit), q = Number(qty);
        const zero = { brokerage: 0, stt: 0, txn: 0, sebi: 0, stamp: 0, gst: 0, total: 0 };
        if (!(q > 0) || !(e > 0) || !(x > 0)) return zero;
        const buyValue  = q * (short ? x : e);
        const sellValue = q * (short ? e : x);
        return roundTrip(segment, buyValue, sellValue);
    }

    function forTrade(trade) {
        return breakdownForTrade(trade).total;
    }

    global.ZerodhaCharges = { roundTrip, forTrade, breakdownForTrade, SEGMENTS };
})(window);
