/**
 * mine_orderflow.js — Order Flow (footprint) as a chart-agnostic indicator
 * engine, built to the same contract as mine_cpr.js / mine_tpo.js and drawn
 * beside them.
 *
 * `MineOrderFlow.compute(candles, interval, settings, cache)` is pure once the
 * tape is in hand: it turns one pane's bars into one footprint per bar — the
 * buy/sell volume at every price row, that bar's delta, its intra-bar delta
 * extremes, its point of control and its diagonal imbalances — and
 * `MineOrderFlow.attach(pane, result)` puts the lot on a Lightweight Charts v5
 * pane through ONE canvas primitive, the way the TPO profile is drawn.
 *
 * WHERE THE NUMBERS COME FROM
 * ---------------------------
 * A footprint needs trades, not candles, so this is the only one of the three
 * engines with a data source of its own: `MineOrderFlow.Tape`, which reads
 * /api/time-and-sales — the app's own trade tape (service/time_and_sales.py),
 * live for today and out of the SQLite archive for earlier sessions.
 *
 * That archive is forward-only: it holds the sessions the app was running and
 * watching for, which is why this used to draw today and nothing else. A day
 * it does not hold is now asked for with `root` and `backfill=1`, and the
 * server rebuilds it from ICICI's 1-second history for the contract that was
 * front month THAT day and archives it (tas.rebuild_day) — after which it is
 * an ordinary archived day. A rebuilt day is all 1-second bars, with none of
 * the live socket's true prints, because that feed only exists in the moment.
 *
 * Be honest about what that tape is. Most of it is ICICI's 1-SECOND bars
 * (src='bar'): one row per traded second, carrying that whole second's volume,
 * with the side taken from whether the second closed up or down. It is not a
 * true bid/ask print feed like the one behind Dhan's DEXT panel — a second
 * that traded both sides lands wholly on one of them. What it does account for
 * is essentially all of the volume, at one-second resolution, which is what
 * makes the shape, the delta and the imbalances worth reading. The live
 * websocket rows (src='tick') are true prints and are classified Lee-Ready by
 * the server; they are the minority.
 *
 * So: the row totals are real volume and the split is a one-second
 * approximation. The pane header says which, and `result.coverage` carries the
 * server's own figure for anyone who wants to state it.
 *
 * THE TAPE IS A FUTURES TAPE
 * --------------------------
 * It is collected on the front-month FUTURE, never the index. Drawing those
 * rows against spot candles would put every cell a basis away from the bar it
 * belongs to, so the footprint is only computed for a pane charting the
 * future — the host page is expected to switch its data source, not to shift
 * the prices.
 *
 * Timestamps: tape rows carry a REAL epoch, every bar here is on the app's
 * fake-IST grid (IST wall clock stored as UTC seconds), so one offset converts
 * between them and every date split uses the UTC getters on purpose — same
 * convention as mine_cpr.js.
 *
 * Elements address the x-axis by BAR INDEX into the candle array and carry
 * that bar's TIME beside it, and the primitive resolves the time to the
 * chart's logical index, for the reason the CPR header sets out.
 */
window.MineOrderFlow = (function () {
    'use strict';

    // Real epoch -> the fake-IST grid every bar in this app sits on.
    const IST = 19800;

    /* ── settings ────────────────────────────────────────────────────────── */
    const DEFAULTS = {
        of: false,               // off until asked for — it is the densest thing on the chart
        // Whether the footprint is drawn ON THE CHART at all. Off leaves the
        // candles alone — no cells, no figures, no POC ring, no imbalance
        // marks, and the chart's own candle back at full width — while the
        // bar-stats pane and the per-bar header carry on. The cells are the
        // densest thing here and are not always what is being read; this is
        // how to put them away without losing the delta rows with them.
        ofFootprint: true,
        ofCell: 'bidask',        // what a cell says: sell×buy, delta, or plain volume
        ofRowStep: 0,            // price per row; 0 = auto from the drawn bars' own ranges
        ofRowsTarget: 6,         // rows a bar comes out around when the step is auto
        // Sessions of tape to load and draw, newest back — 0 being EVERY
        // session the chart is showing, which is the default. More than one on
        // purpose: the days before today are drawn out of the archive, and a
        // day the archive never held is rebuilt from 1-second history the
        // first time it is asked for (Tape.fetchArchived). A footprint that
        // stopped at midnight was the single most misleading thing here, and a
        // footprint that stopped three days back was the same complaint again
        // (reported 2026-10-01: "delta only comes for 3 days") — the delta
        // rows are now drawn for as far back as the candles go. The days are
        // asked for newest-first and a rebuild runs one at a time, so a chart
        // full of never-taped sessions fills in from the right instead of
        // firing fifty broker rebuilds at once.
        ofSessions: 0,
        // Never draw more footprints than this, newest first — a guard on the
        // size of the result array, not on the work (every bar of every
        // session asked for is built either way; this only trims what comes
        // back). 0 is no cap, and is the default: it was 200, which is 3h20m
        // of a 1-minute pane — a session crosses it at 12:35 and the morning
        // starts falling off the left a bar at a time, and with more than one
        // session it cut the older ones away entirely before they could be
        // drawn. Any finite number does that again the moment the pane holds
        // more bars than it, which is exactly the hole in the tape it looks
        // like. Set it only to deliberately trim the oldest footprints.
        ofBars: 0,
        ofNumbers: true,         // print the figures; off leaves the heat map alone
        ofPoc: true,             // ring the bar's busiest row
        ofImbalance: true,       // diagonal bid/ask imbalance
        ofImbFactor: 300,        // an imbalance is this percent of the diagonal cell
        ofStack: 3,              // this many in a row is a stacked imbalance, bracketed
        ofHeader: true,          // the figures printed above each bar at all
        // Which of them. Buy and Sell are the two halves the delta is the
        // difference of. DEXT reads them off the cells, column by column — but
        // the cells only exist on a chart that IS the tape's contract, and two
        // of the three pages here chart the index, so a bar's buy and sell were
        // not readable at all. They are their own lines, on by default.
        ofHeadVolume: true,
        ofHeadBuy: true,
        ofHeadSell: true,
        ofHeadDelta: true,
        ofHeadCumDelta: true,
        ofStats: true,           // the bar-stats pane under the chart
        // ...and which rows it carries. Every row is its own switch: a reader
        // watching cumulative delta alone wants one row at full height, not
        // four at a quarter each, and the pane divides its height by however
        // many are on. Buy and Sell are the same two halves as above.
        ofStatBuy: false,
        ofStatSell: false,
        ofStatDelta: true,
        ofStatMaxDelta: true,
        ofStatMinDelta: true,
        ofStatCumDelta: true,
        ofOpacity: 45,           // cell fill, percent — the candles are read through them
        // How the candle is drawn while a footprint is on it. 'narrow' is the
        // footprint idiom and the default: the chart's own candle is hidden
        // and a thin one is drawn down the middle of the cells, in the gap
        // between the sell column and the buy column, so it can never cover a
        // figure. 'full' and 'hollow' are the chart's own candle at full bar
        // width, over the cells.
        ofCandle: 'narrow',

        // ── Delta Extreme Breakout (ΔX) ────────────────────────────────
        // A signal layer read off the two figures this engine already has per
        // bar — Max Delta and Min Delta, the intra-bar running extremes —
        // rather than a second indicator with a data source of its own. What
        // each knob does is set out at buildSignals(); the defaults below are
        // deliberately strict, because the whole point of the thing is that it
        // only speaks when a bar is UNUSUAL.
        ofSig: false,            // off until asked for, like the footprint itself
        ofSigLook: 30,           // bars of the same session an extreme is judged against
        ofSigMult: 2.5,          // unusual = median + this × robust deviation
        ofSigVolPct: 25,         // …and at least this % of the bar's own volume
        // OFF by default since 2026-10-01, asked for against a real bar: the
        // 3.3L Max Delta on 1 Oct printed on a RED candle whose net delta was
        // strongly negative — textbook absorption, and exactly the bar the
        // rule is meant to catch. The filter stays here because the reading is
        // real, but the rule is "unusual Max Delta arms the high", full stop.
        ofSigAbsorb: false,      // drop a bar whose close denies its own extreme
        ofSigFlip: true,         // ...and an opposite unusual extreme closes the trade
        // Whether the candle's OTHER side being taken out first cancels the
        // setup. OFF by default since 2026-10-01, and the 29 Sept 10:05 bar
        // is why: Max Delta 333,190, the biggest of the day by a factor of
        // fifteen, and the very next bar dipped 5 points under its low before
        // 10:25 broke its high — which is the entry that was wanted. A buy
        // STOP resting above that candle does not care that price dipped
        // first; nothing is held until it fills, so there is nothing to stop
        // out. On, the setup is cancelled the moment the other side trades,
        // which is the stricter reading and still available.
        ofSigVoid: false,
        // ── the dynamic exit ────────────────────────────────────────────
        // ON by default since 2026-10-01, asked for against the 1 Oct short:
        // the trade is not closed at a fixed level at all, the STOP follows
        // the price and the trade ends when it is hit. A bar moves the stop
        // only when it BOTH closes against the trade and prints a notable
        // opposing delta — which is what separates the bars that counted on
        // 1 Oct (12:30, a doji with Max Delta 12,415; 13:00, a strong buy
        // candle with 46,020) from the ones that did not (12:50 and 12:55,
        // red continuation bars with 11,180 and 6,500). A delta threshold
        // alone cannot tell 11,180 from 12,415; the close can.
        //
        // With this on the fixed target and the opposite-extreme exit are
        // SUPERSEDED, not stacked: both would have closed the 1 Oct short
        // earlier than the trail did (VAL 22420 at 12:55, the 13:00 extreme
        // at its own close), and the whole point of a trail is that it is the
        // one thing that ends the trade. Turn it off to get the VAH / VAL /
        // POC / ΔX target back.
        ofSigTrail: true,
        ofSigTrailMult: 2,       // how unusual the opposing delta has to be to move the stop
        ofSigBars: 5,            // bars the level stays armed for after the signal
        ofSigConfirm: true,      // cumulative delta must agree at the break
        ofSigRR: 2,              // fallback target in R when no opposite level is in front
        ofSigLines: true,        // draw the entry / stop / target lines, not just the mark
    };

    const CELL_OPTIONS = [
        ['bidask', 'Sell × Buy'], ['delta', 'Delta'], ['volume', 'Volume'],
    ];

    const CANDLE_OPTIONS = [
        ['narrow', 'Narrow, between the columns'],
        ['full', 'Full width, over the cells'],
        ['hollow', 'Full width, hollow'],
    ];

    // The narrow candle, in CSS px: a share of the bar's slot, floored so it
    // stays visible on a tight chart and capped so it never reaches the
    // figures either side of it.
    const CANDLE_W_PCT = 0.2, CANDLE_MIN_W = 3, CANDLE_MAX_W = 9;

    const LIGHT = {
        buy: '#1b9981', sell: '#f23645', flat: '#94a3b8',
        text: '#1f2937', sub: '#64748b',
        poc: '#f59e0b',
        stack: '#7c3aed',
        sig: '#0ea5e9', sigDim: '#94a3b8',
        panel: '#ffffff', panelLine: '#e2e8f0',
    };
    const DARK = {
        buy: '#2dd4a7', sell: '#ff5a6e', flat: '#64748b',
        text: '#e2e8f0', sub: '#94a3b8',
        poc: '#fbbf24',
        stack: '#a78bfa',
        sig: '#38bdf8', sigDim: '#64748b',
        panel: '#111827', panelLine: 'rgba(255,255,255,0.10)',
    };
    const COLORS = LIGHT;

    // Cell geometry, in CSS pixels. Below these the text is dropped and the
    // cell is drawn as a plain heat block — an unreadable smear of digits
    // says less than the colour alone.
    const NUM_PX = 9;                       // fixed at every zoom, like an axis label
    const MIN_CELL_H = NUM_PX + 2;
    const MIN_CELL_W_PAIR = 46;             // two columns of figures
    const MIN_CELL_W_ONE = 26;              // one
    const MARK_MIN_W = 7, MARK_MIN_H = 5;   // below this a cell is heat only, no boxes
    const HEADER_PX = 9;
    const STATS_PX = 9;
    // Every figure the per-bar header can print, in the order it stacks:
    // [field on the bar, settings key]. The header draws the ones switched on.
    const HEADER_LINES = [
        ['volume', 'ofHeadVolume'], ['buy', 'ofHeadBuy'], ['sell', 'ofHeadSell'],
        ['delta', 'ofHeadDelta'], ['cumDelta', 'ofHeadCumDelta'],
    ];
    const headerLines = set => HEADER_LINES.filter(([, key]) => key in set ? !!set[key] : !!DEFAULTS[key]);
    // The same for the bar-stats pane: [field, row name, settings key].
    const STATS_ROWS = [
        ['buy', 'Buy', 'ofStatBuy'], ['sell', 'Sell', 'ofStatSell'],
        ['delta', 'Delta', 'ofStatDelta'], ['maxDelta', 'Max Delta', 'ofStatMaxDelta'],
        ['minDelta', 'Min Delta', 'ofStatMinDelta'], ['cumDelta', 'Cum. Delta', 'ofStatCumDelta'],
    ];
    const statsRows = set => STATS_ROWS.filter(([, , key]) => key in set ? !!set[key] : !!DEFAULTS[key]);
    // The height the stats pane OPENS at: its switched-on rows and a little air,
    // in CSS px. An absolute height rather than a share of the chart, because
    // what it has to fit is a few lines of 9px text however tall the pane above
    // it is — a proportional one opened at a third of the chart for four rows.
    // Set ONCE, when the pane is created: the separator is draggable, and
    // re-applying this on every redraw would pull it back from under the user.
    const STATS_ROW_PX = 21;
    const statsOpenPx = rows => Math.max(1, rows) * STATS_ROW_PX + 6;
    const STATS_MAX_SHARE = 0.35;           // never more of a short chart than this
    const STATS_GAP_PX = 1;                 // air between two value boxes, CSS px
    const MAX_ROWS_PER_BAR = 200;           // a guard on the row loop, never hit by a real bar

    const secondsOf = k => (window.MineCPR && MineCPR.SECONDS[k]) || 300;
    const sessionStart = t => MineCPR.sessionStart(t);
    const dayKey = t => MineCPR.dayKey(t);

    function isDark() {
        try {
            const t = window.AppTheme && window.AppTheme.getActiveTheme();
            return t === 'dark' || t === 'forest';
        } catch (e) { return false; }
    }

    const withAlpha = (hex, alpha) => {
        const n = parseInt(hex.slice(1), 16);
        return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
    };

    // 12,345 -> '12.3K'. A footprint cell is a few characters wide; the exact
    // figure belongs in the tape panel, not here.
    function brief(v) {
        const n = Math.abs(v);
        if (n >= 1e7) return (v / 1e7).toFixed(n >= 1e8 ? 0 : 1) + 'Cr';
        if (n >= 1e5) return (v / 1e5).toFixed(n >= 1e6 ? 0 : 1) + 'L';
        if (n >= 1000) return (v / 1000).toFixed(n >= 10000 ? 0 : 1) + 'K';
        return String(Math.round(v));
    }
    const signed = v => (v > 0 ? '+' : '') + brief(v);

    /* ── where a footprint can be drawn ──────────────────────────────────── */
    // Every intraday timeframe. The footprint is built from the tape's own
    // rows, so the pane only decides how many of them land in one bar: a
    // 1-minute pane gets a handful of seconds per bar, an hourly one gets the
    // whole hour. Daily and up are excluded — the tape is a session's worth of
    // seconds, and one bar per session would collapse it into a single column
    // that the TPO profile already draws better.
    function tfInfo(interval) {
        const secs = secondsOf(interval);
        return { secs, showOf: secs >= 60 && secs < 86400 };
    }

    /* ── the tape ────────────────────────────────────────────────────────── */
    // One store per page, shared by every pane: three panes charting the same
    // symbol read the same rows, so the day is fetched once however many
    // footprints are drawn from it.
    //
    // Today is polled incrementally on the `seq` cursor the endpoint hands
    // back, and its `epoch` — a top-up renumbers every seq after the seam it
    // filled, so a cursor from an earlier epoch points somewhere else now and
    // the answer comes back `truncated`, which is the signal to drop what we
    // hold and take the response whole. Earlier days come out of the archive
    // and never change, so they are fetched once and kept.
    const Tape = (function () {
        const days = new Map();      // 'YYYY-MM-DD' -> { symbol, rows, seq, epoch, state, live }
        const rootDays = new Map();  // root -> { list: [{day, symbol}], at }
        const inflight = new Set();
        let generation = 0;          // bumped whenever rows changed; part of the compute cache key
        let listener = null;
        let lastCoverage = null;

        const ARCHIVE_TTL = 10 * 60 * 1000;   // how long the day list is trusted for
        const ROW_LIMIT = 25000;              // the store's own cap: a whole session in one read
        // With ofSessions at 0 the pane asks for every session on the chart,
        // which on a 15-minute pane is months. Two throttles keep that civil,
        // both newest-first so the days nearest the right edge arrive first:
        // archive reads are cheap (local SQLite) but not free, and a REBUILD
        // is ~25 broker requests over tens of seconds, so only one of those
        // is ever in flight. A deferred day stays 'idle' and the next
        // completed fetch's `changed()` brings the host back round to it.
        const MAX_DAY_READS = 4;              // archive reads in flight at once
        const MAX_BACKFILLS = 1;              // history rebuilds in flight at once
        let backfills = 0;

        function entry(day) {
            let e = days.get(day);
            if (!e) days.set(day, e = { symbol: null, rows: [], seq: 0, epoch: null, state: 'idle', live: false });
            return e;
        }

        function changed() {
            generation++;
            if (listener) { try { listener(); } catch (e) { /* the host's problem, not ours */ } }
        }

        async function getJSON(url) {
            const r = await fetch(url, { credentials: 'same-origin' });
            const body = await r.json();
            if (!r.ok || !body || body.success === false) throw new Error((body && body.error) || `HTTP ${r.status}`);
            return body;
        }

        // The contract each archived day was taped under — the front month
        // THEN, which is not the one trading now. Cheap, cached, and the only
        // way to reach a day whose future has since expired.
        async function archivedDays(root) {
            const hit = rootDays.get(root);
            if (hit && Date.now() - hit.at < ARCHIVE_TTL) return hit.list;
            const key = `days:${root}`;
            if (inflight.has(key)) return hit ? hit.list : [];
            inflight.add(key);
            try {
                const body = await getJSON(`/api/time-and-sales?days=1&root=${encodeURIComponent(root)}`);
                const list = body.days || [];
                rootDays.set(root, { list, at: Date.now() });
                return list;
            } catch (e) {
                rootDays.set(root, { list: hit ? hit.list : [], at: Date.now() });
                return hit ? hit.list : [];
            } finally {
                inflight.delete(key);
            }
        }

        async function fetchLive(day, symbol) {
            const e = entry(day);
            const key = `live:${day}`;
            if (inflight.has(key)) return;
            inflight.add(key);
            try {
                if (e.symbol !== symbol) { e.symbol = symbol; e.rows = []; e.seq = 0; e.epoch = null; }
                const url = `/api/time-and-sales?symbol=${encodeURIComponent(symbol)}`
                    + `&since=${e.seq}&limit=${ROW_LIMIT}`
                    + (e.epoch == null ? '' : `&epoch=${e.epoch}`);
                const body = await getJSON(url);
                const rows = body.rows || [];
                // `truncated` means our cursor no longer means what it meant —
                // the response IS the tape, not an extension of it.
                if (body.truncated) e.rows = rows;
                else if (rows.length) e.rows = e.rows.concat(rows);
                e.epoch = body.epoch == null ? e.epoch : body.epoch;
                e.seq = body.next_seq != null ? body.next_seq - 1
                    : (e.rows.length ? e.rows[e.rows.length - 1].seq : e.seq);
                e.live = true;
                e.state = e.rows.length ? 'ready' : 'empty';
                if (body.coverage != null) lastCoverage = body.coverage;
                if (rows.length || body.truncated) changed();
            } catch (err) {
                e.state = e.rows.length ? 'ready' : 'error';
                e.error = err.message;
            } finally {
                inflight.delete(key);
            }
        }

        // One past session. A day the archive holds is read straight out of
        // it; a day it does not is asked for by ROOT with `backfill=1`, which
        // is the server rebuilding it from Breeze's 1-second history and
        // archiving it (see tas.rebuild_day). That is the whole of "show the
        // footprint on history": the archive is forward-only and only holds
        // sessions this app happened to be watching, so without it the
        // indicator was a today-only indicator on a chart full of yesterdays.
        //
        // The rebuild is ~25 broker requests and takes tens of seconds, so the
        // day sits in 'loading' while it runs — one request per day, never
        // retried in a loop, and the server rate-limits it besides.
        async function fetchArchived(day, root) {
            const e = entry(day);
            const key = `day:${day}`;
            if (inflight.has(key)) return;
            inflight.add(key);
            e.state = 'loading';
            let rebuilding = false;
            try {
                const list = await archivedDays(root);
                const hit = list.find(d => d.day === day);
                if (!hit) {
                    // A day the archive never held: this is the expensive
                    // path, so it waits its turn rather than joining a queue
                    // of fifty at the broker.
                    if (backfills >= MAX_BACKFILLS) { e.state = 'idle'; return; }
                    rebuilding = true;
                    backfills++;
                }
                const url = hit
                    ? `/api/time-and-sales?symbol=${encodeURIComponent(hit.symbol)}&day=${day}`
                    : `/api/time-and-sales?day=${day}&root=${encodeURIComponent(root)}&backfill=1`;
                const body = await getJSON(`${url}&limit=${ROW_LIMIT}`);
                e.symbol = body.symbol || (hit ? hit.symbol : null);
                e.rows = body.rows || [];
                e.live = false;
                e.why = body.backfill_state || null;
                e.state = e.rows.length ? 'ready' : 'empty';
                // A rebuilt day is a day the archive now holds, so the cached
                // day list is a session short.
                if (!hit && e.rows.length) rootDays.delete(root);
                changed();
            } catch (err) {
                e.state = 'error';
                e.error = err.message;
                changed();
            } finally {
                if (rebuilding) backfills--;
                inflight.delete(key);
            }
        }

        // Ask for the days a pane wants drawn. `todayKey`/`todaySymbol` name
        // the session still being taped — the only one polled again once it is
        // held. Everything else is fetched once.
        function want(root, wanted, todayKey, todaySymbol) {
            if (!root) return;
            // `wanted` is newest-first, and so is the budget it spends: the
            // sessions by the right edge are the ones being read.
            let budget = MAX_DAY_READS;
            for (const k of inflight) if (k.startsWith('day:')) budget--;
            for (const day of wanted) {
                const e = entry(day);
                if (day === todayKey && todaySymbol) {
                    fetchLive(day, todaySymbol);
                } else if (e.state === 'idle' || (e.state === 'error' && !e.rows.length && !inflight.has(`day:${day}`))) {
                    if (budget <= 0) continue;
                    budget--;
                    fetchArchived(day, root);
                }
            }
        }

        function forget() {
            days.clear();
            generation++;
        }

        // Today's front-month contract, for a host that has not been handed
        // one. Multichart gets it free on its candles payload (`future_symbol`);
        // the index pages chart spot and never see it, so they ask here. One
        // request per root per session — the picker's own list, first entry.
        const fronts = new Map();
        function front(root) {
            if (!root) return null;
            if (fronts.has(root)) return fronts.get(root);
            const key = `front:${root}`;
            if (!inflight.has(key)) {
                inflight.add(key);
                getJSON(`/api/time-and-sales?contracts=1&root=${encodeURIComponent(root)}`)
                    .then(body => {
                        const first = (body.contracts || [])[0];
                        fronts.set(root, first ? first.symbol : null);
                        changed();
                    })
                    .catch(() => { fronts.set(root, null); })
                    .finally(() => inflight.delete(key));
            }
            return null;                       // until it lands
        }

        return {
            want, forget, front,
            rows: day => (days.get(day) || {}).rows || null,
            state: day => (days.get(day) || {}).state || 'idle',
            why: day => (days.get(day) || {}).why || (days.get(day) || {}).error || null,
            generation: () => generation,
            coverage: () => lastCoverage,
            setListener: fn => { listener = fn; },
        };
    })();

    /* ── row geometry ────────────────────────────────────────────────────── */
    // Rows are absolute — row i spans [i·step, (i+1)·step) — so every bar in
    // the pane lines its cells up on one price grid and a level reads straight
    // across the chart.
    const rowOf = (price, step) => Math.floor(price / step);

    // A "nice" step: 1, 2, 2.5 or 5 times a power of ten, so a NIFTY bar lands
    // on 5-point rows rather than 3.7-point ones.
    function niceStep(raw) {
        if (!(raw > 0)) return 1;
        const p = Math.pow(10, Math.floor(Math.log10(raw)));
        for (const m of [1, 2, 2.5, 5]) if (raw <= m * p) return m * p;
        return 10 * p;
    }

    /* ── signing the tape ────────────────────────────────────────────────── */
    // Turning the tape into buy and sell volume. This is the ONE thing a
    // footprint gets from the exchange for free and we have to estimate, so it
    // is worth saying exactly what it does and why — it was wrong once, by
    // 38% on the day's cumulative delta.
    //
    // Two rules, in order:
    //
    //  1. A row the server classified against the BOOK (`side_rule='quote'`)
    //     is taken as it stands. That is a real print, Lee-Ready against a
    //     real bid and ask, and nothing here improves on it. It is also a
    //     handful of rows a day — the live websocket's share of the tape.
    //
    //  2. Everything else is a 1-second BAR, and gets the tick test on the
    //     close-to-close path: above the previous row's price is buying,
    //     below it selling, and UNCHANGED SPLITS 50/50.
    //
    // Two things that rule deliberately does NOT do, both of which are what
    // was wrong before:
    //
    //  * It does not use the server's own `side` for a bar row. The server
    //    signs a second by its own open-to-close (`_bar_rows`), which is not a
    //    tick test, and it restarts each fetched chunk from `prev_side='flat'`
    //    so the carry is arbitrary at every top-up seam.
    //  * It does not carry the previous side across an unchanged second. A
    //    second that closed where the last one did says nothing about who was
    //    aggressive, and handing it to whoever last moved the price is a pure
    //    directional bias — ~19% of the day's volume, all of it pushed one way.
    //
    // Measured against Dhan's DEXT on 2026-09-29 (real bid/ask), on NIFTY SEP
    // FUT 5-minute bars 14:05-14:20: cumulative delta came out 548,730 against
    // DEXT's 550,000. The old rule gave 756,275, and signing only the server's
    // explicit 'flat' rows gave 624,910. Per-BAR delta is still coarser than
    // DEXT's — a whole second lands on one side where a true feed splits it —
    // so read a single bar's delta as an estimate and the cumulative as sound.
    //
    // Returns rows carrying `buy` and `sell` that always sum to `qty`, so the
    // volume stays exactly what the exchange printed however the split lands.
    function signRows(rows) {
        const out = new Array(rows.length);
        let prevPrice = null;
        for (let i = 0; i < rows.length; i++) {
            const r = rows[i];
            const qty = r.qty || 0;
            let f;                                   // the buy share of this row
            if (r.side_rule === 'quote' && (r.side === 'buy' || r.side === 'sell')) {
                f = r.side === 'buy' ? 1 : 0;
            } else if (prevPrice == null || r.price === prevPrice) {
                f = 0.5;                             // no information — split it
            } else {
                f = r.price > prevPrice ? 1 : 0;
            }
            out[i] = { ts: r.ts, price: r.price, qty, buy: qty * f, sell: qty * (1 - f), src: r.src };
            prevPrice = r.price;
        }
        return out;
    }

    /* ── one session's footprints ────────────────────────────────────────── */
    // `bars` are that session's candles in order, `rows` its signed tape.
    // Returns one footprint per bar that saw a trade, keyed by bar time.
    function buildSession(bars, rows, step, secs, imbFactor, stack) {
        const open = bars.length ? sessionStart(bars[0].time) : 0;
        const byTime = new Map();
        for (const b of bars) byTime.set(b.time, { time: b.time, cells: new Map(), volume: 0, buy: 0, sell: 0, order: [] });

        // Bucket every print into its bar and its price row. The bar key is
        // built the same way the app buckets everything else — 09:15-anchored
        // — so a print lands on the bar the candle chart drew it in.
        for (const r of rows) {
            if (!r.qty) continue;
            const t = r.ts + IST;
            const bar = byTime.get(open + Math.floor((t - open) / secs) * secs);
            if (!bar) continue;                       // a print outside the bars we hold
            const row = rowOf(r.price, step);
            let cell = bar.cells.get(row);
            if (!cell) {
                if (bar.cells.size > MAX_ROWS_PER_BAR) continue;   // a bad print, not a bar
                bar.cells.set(row, cell = { row, buy: 0, sell: 0 });
            }
            // A row carries a buy share and a sell share that sum to its
            // quantity — an unchanged second is half each — so the cells add
            // up to the exchange's own volume whichever way the split landed.
            cell.buy += r.buy; cell.sell += r.sell;
            bar.buy += r.buy; bar.sell += r.sell;
            bar.volume += r.qty;
            // The running delta, in print order, is what the intra-bar
            // extremes are read off — not the cells, which are already summed.
            bar.order.push(r.buy - r.sell);
        }

        // Cumulative delta runs from the session's open, over every bar,
        // whether or not that bar ends up drawn.
        const out = [];
        let cum = 0;
        for (const b of bars) {
            const bar = byTime.get(b.time);
            if (!bar || !bar.volume) { out.push(null); continue; }
            const delta = bar.buy - bar.sell;

            let run = 0, maxDelta = 0, minDelta = 0;
            for (const d of bar.order) {
                run += d;
                if (run > maxDelta) maxDelta = run;
                if (run < minDelta) minDelta = run;
            }
            cum += delta;

            const cells = Array.from(bar.cells.values()).sort((a, b2) => a.row - b2.row);
            let poc = cells[0], widest = -1;
            for (const c of cells) {
                c.total = c.buy + c.sell;
                if (c.total > widest) { widest = c.total; poc = c; }
            }

            // Diagonal imbalance, the standard reading: the BUY volume at a
            // row is compared with the SELL volume one row BELOW it, because
            // that is the pair a resting book would have filled against each
            // other. A side that clears the factor and has something to clear
            // is marked; a run of `stack` of them in a row is the stacked
            // imbalance that tends to hold on a retest.
            const byRow = new Map(cells.map(c => [c.row, c]));
            const lo = cells[0].row, hi = cells[cells.length - 1].row;
            if (imbFactor > 1) {
                for (const c of cells) {
                    // Only against a diagonal INSIDE the bar's own range. The
                    // row below the bar's low never traded in this bar, so
                    // comparing with it is comparing with nothing — every
                    // bar's top and bottom row came out an imbalance, which
                    // is two marks per bar that mean nothing. A row inside the
                    // range with no prints on that side is a real zero, and a
                    // real imbalance.
                    if (c.buy > 0 && c.row - 1 >= lo) {
                        const below = byRow.get(c.row - 1);
                        if (c.buy >= Math.max(1, below ? below.sell : 0) * imbFactor) c.imbBuy = true;
                    }
                    if (c.sell > 0 && c.row + 1 <= hi) {
                        const above = byRow.get(c.row + 1);
                        if (c.sell >= Math.max(1, above ? above.buy : 0) * imbFactor) c.imbSell = true;
                    }
                }
                // Stacks, found on the sorted rows so a gap breaks the run.
                for (const side of ['imbBuy', 'imbSell']) {
                    let i = 0;
                    while (i < cells.length) {
                        if (!cells[i][side]) { i++; continue; }
                        let j = i;
                        while (j + 1 < cells.length && cells[j + 1][side]
                               && cells[j + 1].row === cells[j].row + 1) j++;
                        if (j - i + 1 >= stack) for (let k = i; k <= j; k++) cells[k].stacked = true;
                        i = j + 1;
                    }
                }
            }

            out.push({
                time: b.time, cells, volume: bar.volume, buy: bar.buy, sell: bar.sell,
                // The candle's own levels: the footprint draws it itself when
                // `ofCandle` is 'narrow', because the series cannot be made
                // thinner than a bar and a full-width body covers the figures.
                o: b.open, h: b.high, l: b.low, c: b.close,
                delta, maxDelta, minDelta, cumDelta: cum,
                poc: poc ? poc.row : null, widest,
                minRow: cells[0].row, maxRow: cells[cells.length - 1].row,
            });
        }
        return out;
    }

    /* ── Delta Extreme Breakout (ΔX) ─────────────────────────────────────── */
    // A SIGNAL read off the two figures the footprint already carries per bar:
    // Max Delta and Min Delta, the high and low water marks of the running
    // delta INSIDE the bar. Those two say something a bar's net delta does
    // not — how hard one side pushed while the bar was forming, even if the
    // other side took it all back by the close.
    //
    // The rule, in the order it is applied:
    //
    //  1. UNUSUAL, or nothing. A big Max Delta on a busy bar is not news; the
    //     signal is a bar whose extreme stands out against the session it is
    //     in. "Unusual" is median + `mult` × robust deviation (MAD × 1.4826)
    //     over the previous `look` bars OF THE SAME SESSION — median and MAD,
    //     not mean and standard deviation, because delta is fat-tailed and one
    //     earlier spike drags a mean threshold up far enough to silence the
    //     next hour. A session's first `SIG_MIN_LOOK` bars are never judged:
    //     there is nothing yet to be unusual against.
    //
    //  2. …AND SIZEABLE AGAINST THE BAR ITSELF. `ofSigVolPct`: the extreme has
    //     to be at least that percent of the bar's own volume. A quiet session
    //     makes small numbers unusual; this is what stops the thing firing on
    //     a 40-lot bar at lunchtime.
    //
    //  3. ABSORPTION IS NOT INITIATIVE — the condition worth having that the
    //     plain reading misses. A bar with a huge buy extreme that then CLOSES
    //     in the bottom third of its own range is buyers being absorbed, not
    //     buyers winning: the aggression went in and the price did not follow.
    //     Taken as a long it is the classic way to buy the high of the move.
    //     `ofSigAbsorb` drops those bars (and the mirror for sells). Off, they
    //     are taken as ordinary signals.
    //
    //  4. THE CANDLE IS THE TRIGGER. The signal bar arms a level — its HIGH
    //     for a Max Delta signal, its LOW for a Min Delta one — and nothing is
    //     entered until a later bar breaks it. The level is armed for
    //     `ofSigBars` bars and dies with the session: an unusual bar the
    //     market walked away from is not a setup five hours later.
    //
    //  5. THE OTHER SIDE OF THE CANDLE VOIDS IT. If the bar's own low trades
    //     through before its high breaks, the long is void — the market chose
    //     the other way out of the same candle, and taking the break after
    //     that is taking it with the stop already run.
    //
    //  6. CUMULATIVE DELTA MUST AGREE (`ofSigConfirm`). At the breaking bar the
    //     session's cumulative delta must be at or above where it stood at the
    //     signal bar for a long, at or below for a short. A break into a
    //     falling cumulative delta is a break the flow is not behind.
    //
    //  Stop: the signal candle's other side — its low for a long, its high for
    //  a short. That is the user's rule and it is also the only level the
    //  setup is defined by.
    //
    //  Target: the nearest OPPOSITE unusual-delta level in front of the entry
    //  — for a long, the low or high of an earlier bar that printed an unusual
    //  MIN delta, i.e. where sellers were last aggressive, which is the supply
    //  the move has to eat. Only levels known BEFORE the entry are used; there
    //  is no looking ahead. When the session holds no such level in front, the
    //  target falls back to `ofSigRR` × the risk. A target nearer than a third
    //  of the risk is ignored as not worth the trade.
    //
    //  The outcome is then walked forward bar by bar, stop first when one bar
    //  holds both, so what is drawn is what the rule would actually have done.
    const SIG_MIN_LOOK = 10;
    // Intraday square-off. Nothing is armed at or after this, and anything
    // still on is closed at the last bar BEFORE it — on a 5-minute chart the
    // bar stamped 15:10 is the one that ends at 15:15, so its close IS the
    // 15:15 price. The rule carries nothing past the cut-off and nothing
    // overnight; see the session-end branch, which does the same thing for a
    // day that ends before this.
    const SIG_CUTOFF_MIN = 15 * 60 + 15;        // 15:15 IST
    // Bars sit on the app's fake-IST grid (IST wall clock stored as UTC
    // seconds), so the UTC getters ARE the IST clock here — same convention
    // as dayKey.
    const minOfDay = t => { const d = new Date(t * 1000); return d.getUTCHours() * 60 + d.getUTCMinutes(); };

    function median(a) {
        if (!a.length) return 0;
        const v = a.slice().sort((x, y) => x - y);
        const i = v.length >> 1;
        return v.length % 2 ? v[i] : (v[i - 1] + v[i]) / 2;
    }

    // median + mult × robust deviation, with two floors so a dead-flat window
    // cannot make every bar after it "unusual": a share of the median itself,
    // and one lot.
    function unusualThreshold(vals, mult) {
        const m = median(vals);
        const mad = median(vals.map(v => Math.abs(v - m))) * 1.4826;
        return m + mult * Math.max(mad, m * 0.35, 1);
    }

    // `bars` are the computed footprints in time order, across sessions.
    // Returns one entry per signal, carrying everything the draw needs.
    //
    // ONE SIGNAL PER SIDE AT A TIME. Written as a single pass with an `active`
    // signal per direction rather than a signal-at-a-time walk, because that
    // is the only way a later bar can be read as part of the setup already in
    // progress instead of as a new one. While a long is still in progress —
    // armed and unresolved, or entered and unsettled — a fresh unusual Max
    // Delta does NOT start a second long. It is recorded on the one that owns
    // the side (`repeats`), and while that one is still only ARMED it also
    // re-arms the clock from the repeating bar, keeping the ORIGINAL level and
    // stop. That is what "continue with the previous signal" has to mean to be
    // any use: pressure printing again is a reason to keep waiting for the
    // same break, not a reason to move the level up to the new bar's high and
    // chase it, and not a reason to let the setup expire underneath the very
    // bars that confirm it. The two sides are independent: a short can be in
    // progress while a long is.
    // `profileLevels` is Map(dayKey -> [{price, label}]) — the session's own
    // value area, handed in by the host from the TPO engine (VAH / VAL / POC).
    // A target is the nearest of those or of the earlier opposite extremes,
    // whichever is in front: "VAH or that -17K Min Delta candle, whichever
    // comes first", as asked for on 2026-10-01. Be honest about one thing: a
    // FINISHED session's value area is its final one, so a target read for an
    // entry earlier that day is read with a little hindsight. The levels move
    // slowly and the alternative is rebuilding the developing profile at every
    // bar; the live bar's own VAH is exact either way.
    function buildSignals(bars, set, profileLevels) {
        const g = key => (set && key in set) ? set[key] : DEFAULTS[key];
        if (!g('ofSig') || !bars || bars.length < SIG_MIN_LOOK + 1) return [];

        const look = Math.max(SIG_MIN_LOOK, g('ofSigLook') | 0);
        const mult = Math.max(0.5, +g('ofSigMult') || 2.5);
        const volPct = Math.max(0, +g('ofSigVolPct') || 0) / 100;
        const dropAbsorb = g('ofSigAbsorb') !== false;
        const life = Math.max(1, g('ofSigBars') | 0);
        const confirm = g('ofSigConfirm') !== false;
        const flip = g('ofSigFlip') !== false;
        const voidOnOther = g('ofSigVoid') === true;
        const trail = g('ofSigTrail') !== false;
        const trailMult = Math.max(0.5, +g('ofSigTrailMult') || 1);
        const rr = Math.max(0.5, +g('ofSigRR') || 2);

        const out = [];
        // Per session: the window an extreme is judged against, and the
        // unusual levels already printed that the targets are read off.
        let day = null, win = [], levels = [];
        // The one signal that owns each side while it is in progress.
        const active = { '1': null, '-1': null };

        // The target, fixed at the entry: the nearest opposite unusual level
        // in front of it, else an R multiple. Only levels printed BEFORE the
        // entry are in `levels`, so there is no looking ahead.
        // `at` is the price the target is measured from: the entry once there
        // is one, the armed LEVEL before that — a setup shows where it is
        // aiming from the moment it is found, which is the whole use of it.
        // The armed one is provisional (`armTarget`), drawn dimmer, and is
        // replaced by the real one at the entry, because the entry can be a
        // gap open rather than the level and that moves both the risk and
        // what counts as "in front".
        const pickTarget = (sig, arm) => {
            const from = arm ? 'armTarget' : 'target';
            const fromWhat = arm ? 'armTargetFrom' : 'targetFrom';
            const at = arm ? sig.level : sig.entry;
            const risk = Math.abs(at - sig.stop);
            const floor = Math.max(risk / 3, 0);
            // Every candidate in front of the entry, from both sources, and
            // the NEAREST one wins — that is what "whichever comes first"
            // means for two levels the price has to reach in order.
            const cand = [];
            for (const lv of levels) {
                if (lv.dir === sig.dir) continue;      // the other side's extremes
                cand.push({ price: lv.l, from: 'delta' }, { price: lv.h, from: 'delta' });
            }
            const prof = profileLevels && profileLevels.get ? profileLevels.get(day) : null;
            if (prof) for (const lv of prof) cand.push({ price: lv.price, from: lv.label });
            let best = null;
            for (const c of cand) {
                const ahead = sig.dir > 0 ? c.price - at : at - c.price;
                // A target inside a third of the risk is not worth the trade,
                // and one behind the entry is not a target at all.
                if (!(ahead > floor)) continue;
                if (best === null || ahead < best.ahead) best = { ahead, price: c.price, from: c.from };
            }
            sig[fromWhat] = best ? best.from : 'R';
            sig[from] = best ? best.price
                : (sig.dir > 0 ? at + rr * risk : at - rr * risk);
        };
        const settle = (sig, state, n, price) => {
            sig.state = state;
            if (n) { sig.outX = n.x; sig.outTime = n.time; }
            if (price != null) sig.outPrice = price;
            active[String(sig.dir)] = null;
        };

        for (let i = 0; i < bars.length; i++) {
            const b = bars[i];
            const key = dayKey(b.time);
            if (key !== day) {
                // A setup does not survive the close. Whatever each side was
                // holding ends with the session it was found in.
                for (const d of ['1', '-1']) {
                    const sg = active[d];
                    if (!sg) continue;
                    // A trade that was still running keeps the state it has —
                    // 'live' means entered and never settled, which is also
                    // what the right-hand edge of the chart produces, and the
                    // draw reads it the same way. Only a setup still waiting
                    // for its break expires.
                    //
                    // But it is CLOSED AT THAT SESSION'S LAST BAR, and it has
                    // to say so: `sessionEnd` with the price it ended at.
                    // Without them a reader with no outPrice marks the trade
                    // to the newest bar it holds, which on a multi-session
                    // chart is another day — a 1 Oct short came out +1106.9
                    // points against a 2 Oct close. A position is not carried
                    // overnight by this rule and must never be priced as if
                    // it were.
                    const lastBar = bars[i - 1];
                    if (sg.state === 'live' && lastBar) { sg.sessionEnd = true; sg.outPrice = lastBar.c; }
                    settle(sg, sg.state === 'live' ? 'live' : 'expired', lastBar, null);
                }
                // `levels` and the cumulative-delta comparison are the
                // session's own and reset with it. The JUDGING window does
                // NOT: it is a rolling N bars of recent trade, and resetting
                // it blinded the first ten bars of every session — which is
                // where the 3.3L Max Delta of 1 Oct sat (bar 5), the one bar
                // this indicator most obviously had to call. With more than
                // one session on the chart the morning is now judged against
                // the end of the day before, which is a far better yardstick
                // than nothing at all.
                day = key; levels = [];
            }

            // 1. The cut-off, before anything else this bar could do. A
            //    trade still on is closed at the PREVIOUS bar's close (the
            //    one that ended at 15:15), a setup still waiting expires,
            //    and nothing new is armed for the rest of the session.
            const past = minOfDay(b.time) >= SIG_CUTOFF_MIN;
            if (past) {
                for (const d of ['1', '-1']) {
                    const sg = active[d];
                    if (!sg) continue;
                    const prev = bars[i - 1];
                    if (sg.state === 'live' && prev) {
                        sg.sessionEnd = true; sg.why = '15:15 cut-off'; sg.outPrice = prev.c;
                    }
                    settle(sg, sg.state === 'live' ? 'live' : 'expired', bars[i - 1] || b, null);
                }
                // The window still has to see this bar, or tomorrow morning is
                // judged against a window that skipped every afternoon.
                win.push({ up: Math.max(0, b.maxDelta), dn: Math.max(0, -b.minDelta) });
                if (win.length > look) win.shift();
                continue;
            }

            // 2. Is this bar unusual? Worked out FIRST, because a trade
            //    already running has to be able to read it: an unusual
            //    extreme the OTHER way is what ends it.
            const up = Math.max(0, b.maxDelta);
            const dn = Math.max(0, -b.minDelta);
            const ready = win.length >= SIG_MIN_LOOK;
            const upHit = ready && up >= unusualThreshold(win.map(x => x.up), mult) && up >= b.volume * volPct;
            const dnHit = ready && dn >= unusualThreshold(win.map(x => x.dn), mult) && dn >= b.volume * volPct;
            // A lower bar for the trail than for a signal: this only moves a
            // stop, it does not open anything, so it should notice an
            // opposing bar long before that bar would be a trade of its own.
            // …and the same volume-share guard the signal test uses, at half
            // the bar, for the same reason: a quiet bar makes small numbers
            // look unusual. On 1 Oct the two bars that moved the stop were
            // 18.9% and 25.3% of their own volume; the green bars that did
            // not were 8.7% and 8.2%. Both tests agree, which is why neither
            // is carrying the rule on its own.
            const trailFloor = b.volume * volPct / 2;
            const trailUp = ready && up >= unusualThreshold(win.map(x => x.up), trailMult) && up >= trailFloor;
            const trailDn = ready && dn >= unusualThreshold(win.map(x => x.dn), trailMult) && dn >= trailFloor;
            win.push({ up, dn });
            if (win.length > look) win.shift();

            // 3. This bar against whatever is already in progress, BEFORE it
            //    is allowed to start anything of its own — a signal bar can
            //    never trigger its own entry.
            for (const d of ['1', '-1']) {
                const sg = active[d];
                if (!sg) continue;
                const dir = sg.dir;
                if (sg.state === 'armed') {
                    if (i > sg.armUntil) { settle(sg, 'expired', b, null); continue; }
                    const broke = dir > 0 ? b.h > sg.level : b.l < sg.level;
                    const voided = voidOnOther && (dir > 0 ? b.l < sg.stop : b.h > sg.stop);
                    // Only when asked for (`ofSigVoid`), and then a bar that
                    // does BOTH is read against the setup: inside one bar
                    // there is no way to know which came first, and the honest
                    // reading is the unkind one.
                    if (voided) { settle(sg, 'void', b, null); continue; }
                    if (!broke) continue;
                    // The FIRST break is the break. If the flow does not agree
                    // with it the signal is dropped, not held over: the level
                    // has been taken out either way, and a later bar trading
                    // back through it is not a fresh entry at the same price —
                    // treating it as one buys the level long after the market
                    // left it.
                    if (confirm && (dir > 0 ? b.cumDelta < sg.cumAt : b.cumDelta > sg.cumAt)) {
                        sg.why = 'cum delta'; settle(sg, 'void', b, null); continue;
                    }
                    sg.state = 'live';
                    sg.trigX = b.x; sg.trigTime = b.time;
                    // A bar that OPENED through the level never traded at it,
                    // so the entry is that open, not the level — otherwise a
                    // gap through the setup is drawn as a free entry at the
                    // best price of the move, which flatters every gap day.
                    sg.entry = dir > 0 ? Math.max(sg.level, b.o) : Math.min(sg.level, b.o);
                    // The trail's first step is the stop the trade entered on:
                    // the signal candle's other side, which is the user's rule
                    // and the only level the setup defines.
                    if (trail) sg.trail = [{ x: b.x, time: b.time, price: sg.stop }];
                    pickTarget(sg, false);
                    // …and the breaking bar can settle it, same bar.
                }
                if (sg.state === 'live' && trail) {
                    // The stop is read FIRST, at the level it held coming into
                    // this bar, and only then moved. A bar that moves the stop
                    // to its own high cannot also be the bar that is stopped
                    // out by it — on 1 Oct the 13:00 buy candle set the stop
                    // at its high 22478 and 13:05 took it out at 22488.
                    const hitStop = sg.dir > 0 ? b.l <= sg.stop : b.h >= sg.stop;
                    if (hitStop) { settle(sg, 'stop', b, sg.stop); continue; }
                    // Against the trade on BOTH counts: the close and the
                    // delta. Then the stop goes to that bar's far side, and
                    // only if that tightens it — a stop that loosens is not a
                    // stop, so a later opposing bar further away leaves it be.
                    const against = sg.dir > 0 ? (b.c < b.o && trailDn) : (b.c > b.o && trailUp);
                    if (against) {
                        const to = sg.dir > 0 ? b.l : b.h;
                        if (sg.dir > 0 ? to > sg.stop : to < sg.stop) {
                            sg.stop = to;
                            (sg.trail = sg.trail || []).push({ x: b.x, time: b.time, price: to });
                        }
                    }
                    continue;
                }
                if (sg.state === 'live') {
                    const hitStop = sg.dir > 0 ? b.l <= sg.stop : b.h >= sg.stop;
                    const hitTgt = sg.dir > 0 ? b.h >= sg.target : b.l <= sg.target;
                    // The other side printing an unusual extreme ends the
                    // trade too — "target is VAH or that Min Delta candle,
                    // whichever is first". A price level is TOUCHED inside the
                    // bar; an extreme is only known at its close, so the two
                    // price outcomes are read first and this one books at the
                    // close. The stop stays ahead of both: inside one bar
                    // there is no way to know what came first, and the honest
                    // reading of a trade is the unkind one.
                    const against = sg.dir > 0 ? dnHit : upHit;
                    if (hitStop) settle(sg, 'stop', b, sg.stop);
                    else if (hitTgt) settle(sg, 'target', b, sg.target);
                    else if (flip && against) { sg.why = 'opposite \u0394X'; settle(sg, 'exit', b, b.c); }
                }
            }

            // 4. What this bar is in its own right. One bar can be unusual
            //    both ways — a violent two-sided bar. It
            // is not a long and a short at once, so the bigger extreme owns
            // it; a dead heat is no signal at all.
            let dir = 0;
            if (upHit && dnHit) dir = up > dn ? 1 : dn > up ? -1 : 0;
            else if (upHit) dir = 1;
            else if (dnHit) dir = -1;
            if (!dir) continue;

            // Whatever it turns out to be, it is a level the other side's
            // targets can be read off.
            levels.push({ dir, h: b.h, l: b.l });

            // 5. The side is already spoken for: this bar joins that signal.
            const owner = active[String(dir)];
            if (owner) {
                owner.repeats = (owner.repeats || 0) + 1;
                (owner.repeatBars = owner.repeatBars || []).push({ x: b.x, time: b.time });
                // Still waiting for the break: the clock starts again here,
                // the level and the stop do not move.
                if (owner.state === 'armed') owner.armUntil = i + life;
                continue;
            }

            // 6. A new signal. Where the bar closed in its own range decides
            //    whether that extreme was initiative or absorbed.
            const range = b.h - b.l;
            const pos = range > 0 ? (b.c - b.l) / range : 0.5;
            const absorbed = dir > 0 ? pos <= 0.34 : pos >= 0.66;
            if (absorbed && dropAbsorb) continue;          // a level, not a trade

            const sig = {
                x: b.x, time: b.time, dir, absorbed,
                extreme: dir > 0 ? up : -dn, volume: b.volume, cumAt: b.cumDelta,
                level: dir > 0 ? b.h : b.l,                // the break that enters
                stop: dir > 0 ? b.l : b.h,                 // the candle's other side
                armUntil: i + life,
                repeats: 0,
                state: 'armed',
            };
            active[String(dir)] = sig;
            pickTarget(sig, true);          // where it would aim, from the level
            out.push(sig);
        }
        return out;
    }

    /* ── compute: every drawn bar on the pane ────────────────────────────── */
    // `cache` is the pane's own Map(sessionKey -> {sig, bars}). A finished
    // session never changes, so only today's is rebuilt on a live tick.
    // `opts.cells` is whether the CHART this is drawn on is the instrument the
    // tape was collected on. The tape is a futures tape, so a spot index chart
    // sits a basis away from every price in it — 20-60 points, several rows —
    // and the cells cannot be placed on it. The per-bar figures can: volume,
    // delta and the rest are bucketed by TIME, not by price, so they are exact
    // on any chart of the same underlying. False therefore keeps everything
    // and draws no cells, rather than refusing the indicator outright.
    function compute(candles, interval, settings, cache, opts) {
        const s = key => (settings && key in settings) ? settings[key] : DEFAULTS[key];
        const info = tfInfo(interval);
        const cellsOk = !opts || opts.cells !== false;
        const empty = { bars: [], signals: [], step: 0, sessions: [], missing: [], cellsOk, coverage: Tape.coverage() };
        // The footprint's own switch OR the signal layer's: ΔX reads the
        // bars this builds, so one of the two has to want them. With 'of' off
        // and ΔX on, the tape is still loaded and the bars still built —
        // nothing of the footprint is PAINTED (see `drawOn` in the renderer,
        // and the stats pane below), only the marks.
        if ((!s('of') && !s('ofSig')) || !info.showOf || !candles || !candles.length) return empty;

        const secs = info.secs;
        // 0 = every session on the pane (see DEFAULTS.ofSessions).
        const wanted = Math.max(0, s('ofSessions') | 0);

        // Split into sessions, newest last, and keep only the ones asked for.
        const sessions = [];
        let cur = null;
        for (let i = 0; i < candles.length; i++) {
            const c = candles[i];
            const key = dayKey(c.time);
            if (!cur || cur.key !== key) sessions.push(cur = { key, bars: [], first: i });
            cur.bars.push(c);
            cur.last = i;
        }
        const used = wanted > 0 ? sessions.slice(-wanted) : sessions;
        if (!used.length) return empty;

        // One row step for every bar so the cells share a price grid: the
        // median bar range over the window, cut into ofRowsTarget rows and
        // snapped. A fixed step from the settings wins outright.
        let step = +s('ofRowStep') || 0;
        if (!(step > 0)) {
            const ranges = [];
            for (const sess of used) for (const b of sess.bars) if (b.high > b.low) ranges.push(b.high - b.low);
            if (!ranges.length) return empty;
            ranges.sort((a, b) => a - b);
            const median = ranges[Math.floor(ranges.length / 2)];
            step = niceStep(median / Math.max(2, s('ofRowsTarget') | 0));
        }

        const imbFactor = Math.max(1, (+s('ofImbFactor') || 100) / 100);
        const stack = Math.max(2, s('ofStack') | 0);
        const store = cache instanceof Map ? cache : null;
        const gen = Tape.generation();
        const bars = [];
        const missing = [];

        for (let k = 0; k < used.length; k++) {
            const sess = used[k];
            const rows = Tape.rows(sess.key);
            if (!rows) { missing.push({ day: sess.key, state: Tape.state(sess.key), why: Tape.why(sess.key) }); continue; }
            if (!rows.length) { missing.push({ day: sess.key, state: 'empty', why: Tape.why(sess.key) }); continue; }

            // A finished session is keyed by the settings that shape it, its
            // bar count and the tape's own generation — a top-up that renumbers
            // the day, or an archive read that arrives late, does invalidate it.
            const stamp = `${step}|${secs}|${imbFactor}|${stack}|${sess.bars.length}|${rows.length}|${gen}`;
            let hit = store ? store.get(sess.key) : null;
            let built = hit && hit.sig === stamp ? hit.bars : null;
            if (!built) {
                built = buildSession(sess.bars, signRows(rows), step, secs, imbFactor, stack);
                if (store) store.set(sess.key, { sig: stamp, bars: built });
            }
            for (let i = 0; i < built.length; i++) {
                if (built[i]) bars.push(Object.assign({ x: sess.first + i }, built[i]));
            }
        }

        if (store) {
            const live = new Set(used.map(x => x.key));
            for (const key of Array.from(store.keys())) if (!live.has(key)) store.delete(key);
        }

        // A ceiling on the result array only — every bar of every session asked
        // for has already been built above, and one scrolled off the left is
        // skipped in the draw anyway. So set it to cover the sessions wanted
        // rather than to save work: under-setting it silently deletes the
        // OLDEST bars, which looks exactly like a hole in the tape (reported
        // 2026-10-01 as "9:15 to 9:28 no data" — the cap was 200 on a
        // 214-minute session).
        const cap = s('ofBars') | 0;
        const drawn = cap > 0 && bars.length > cap ? bars.slice(-cap) : bars;
        // Signals are read off the bars that are actually DRAWN, so what the
        // marks claim and what the pane shows can never disagree. The window
        // an extreme is judged against is the session's own bars, so a cap
        // that cut the morning away would move every threshold with it.
        const resolved = Object.fromEntries(Object.keys(DEFAULTS).map(k => [k, s(k)]));
        // The host's TPO profiles, if it drew any, as target candidates keyed
        // by session. MineOrderFlow does not compute a value area of its own:
        // the one on screen is the one the user is reading, and the two must
        // not be allowed to differ. No TPO on the pane simply means ΔX falls
        // back to the opposite extremes and the R multiple.
        const prof = new Map();
        for (const pr of (opts && opts.profiles) || []) {
            if (!pr || !pr.key) continue;
            const rows = [];
            if (pr.vah != null) rows.push({ price: pr.vah, label: 'VAH' });
            if (pr.val != null) rows.push({ price: pr.val, label: 'VAL' });
            if (pr.pocTop != null && pr.pocBottom != null) {
                rows.push({ price: (pr.pocTop + pr.pocBottom) / 2, label: 'POC' });
            }
            if (rows.length) prof.set(pr.key, rows);
        }
        return {
            bars: drawn,
            signals: buildSignals(drawn, resolved, prof),
            step, secs, cellsOk,
            sessions: used.map(x => x.key),
            missing,
            coverage: Tape.coverage(),
            settings: resolved,
        };
    }


    // The ΔX layer is its OWN pane view, and the only reason is z-order: the
    // footprint is drawn UNDER the candles (the cells are a backdrop the price
    // is read through), and a signal's target line put there disappears behind
    // the first candle that trades at it — which is every target that gets
    // hit. Two paneViews off one primitive, the cells at the bottom and the
    // marks on top, is the whole trick; both read the same `state.result`.
    function makeSignalRenderer(state) {
        return {
            draw(target) {
                const { series, chart, result } = state;
                if (!series || !chart || !result.bars || !result.bars.length) return;
                const set = result.settings || DEFAULTS;
                if (!set.ofSig || !(result.signals || []).length) return;
                const C = isDark() ? DARK : LIGHT;
                const ts = chart.timeScale();

                target.useBitmapCoordinateSpace(scope => {
                    const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
                    const W = scope.bitmapSize.width, H = scope.bitmapSize.height;
                    const barSpacing = ts.options().barSpacing || 6;
                    const byTime = typeof ts.timeToIndex === 'function';
                    const logicalOf = (x, t) => {
                        if (byTime && t != null) { const i = ts.timeToIndex(t, true); if (i != null) return i; }
                        return x;
                    };
                    const xOf = (x, t) => {
                        const c = ts.logicalToCoordinate(logicalOf(x, t));
                        return c === null ? null : c * hr;
                    };
                    const yOf = p2 => {
                        const c = series.priceToCoordinate(p2);
                        return c === null ? null : c * vr;
                    };
                    const slot = Math.max(2, barSpacing * hr);

                    ctx.save();
                    ctx.textBaseline = 'middle';
                    // ── Delta Extreme Breakout (ΔX) ──────────────────
                    // Everything this layer paints: the mark on the signal
                    // bar, the level its break is taken on, and — once it has
                    // been taken — the entry, the stop under the signal
                    // candle and the target in front of it, each running to
                    // the bar that settled the trade.
                    const sigs = result.signals;
                    const barAt = new Map(result.bars.map(b => [b.x, b]));
                    const last = result.bars[result.bars.length - 1];
                    const lw = Math.max(1, Math.round(vr));
                    const tri = Math.max(4, Math.min(9, slot * 0.35)) * vr;
                    ctx.font = `${Math.round(HEADER_PX * vr)}px Inter, system-ui, sans-serif`;

                    const seg = (y, xa, xb, color, dash, width) => {
                        if (y === null || y < 0 || y > H) return;
                        ctx.save();
                        ctx.strokeStyle = color;
                        ctx.lineWidth = width;
                        ctx.setLineDash(dash.map(d => d * vr));
                        ctx.beginPath();
                        ctx.moveTo(xa, Math.round(y) + 0.5);
                        ctx.lineTo(xb, Math.round(y) + 0.5);
                        ctx.stroke();
                        ctx.restore();
                    };
                    // A label at the end of a line. It is CLAMPED into the
                    // pane rather than dropped: it used to be skipped whenever
                    // the line ended within ~34px of the right edge, which is
                    // every signal still running on a live chart — the lines
                    // were there and nothing said what they were.
                    const tag = (text, x, y, color) => {
                        if (y === null || y < 0 || y > H || !text) return;
                        ctx.fillStyle = color;
                        const m = ctx.measureText(text);
                        const w = (m && m.width) || text.length * HEADER_PX * 0.62 * hr;
                        const right = x + 3 * hr + w <= W;
                        ctx.textAlign = right ? 'left' : 'right';
                        ctx.fillText(text, right ? x + 3 * hr : Math.min(x, W) - 3 * hr, y);
                    };

                    for (const sg of sigs) {
                        const b = barAt.get(sg.x);
                        const x0 = xOf(sg.x, sg.time);
                        if (x0 === null || !b) continue;
                        const dead = sg.state === 'void' || sg.state === 'expired';
                        const base = dead ? C.sigDim : sg.dir > 0 ? C.buy : C.sell;
                        // Where the drawing stops: the bar that settled it,
                        // else the right-hand edge of what is drawn.
                        const xEndBar = sg.outX != null ? sg.outX : last.x;
                        const xEndT = sg.outTime != null ? sg.outTime : last.time;
                        const xEnd = xOf(xEndBar, xEndT);
                        if (xEnd === null) continue;
                        const xA = x0 - slot / 2, xB = Math.max(xEnd + slot / 2, xA + slot);
                        if (xB < 0 || xA > W) continue;

                        // The mark on the signal bar: a triangle pointing
                        // the way the break would be taken, outside the
                        // candle so it never sits on a figure.
                        const yAnchor = yOf(sg.dir > 0 ? b.l : b.h);
                        if (yAnchor !== null && yAnchor > -40 * vr && yAnchor < H + 40 * vr) {
                            const dirUp = sg.dir > 0;
                            const yT = yAnchor + (dirUp ? 6 * vr : -6 * vr);
                            ctx.fillStyle = withAlpha(base, dead ? 0.45 : 1);
                            ctx.beginPath();
                            ctx.moveTo(x0, yT + (dirUp ? -tri : tri));
                            ctx.lineTo(x0 - tri * 0.7, yT + (dirUp ? tri * 0.2 : -tri * 0.2));
                            ctx.lineTo(x0 + tri * 0.7, yT + (dirUp ? tri * 0.2 : -tri * 0.2));
                            ctx.closePath();
                            ctx.fill();
                            // Absorption bars only reach here when the
                            // user has asked for them, so say which they
                            // are — they are the opposite reading.
                            if (sg.absorbed) {
                                ctx.textAlign = 'center';
                                ctx.fillText('abs', x0, yT + (dirUp ? tri + 6 * vr : -tri - 6 * vr));
                            }
                        }

                        // Every later bar that printed the same unusual
                        // extreme while this signal was in progress: a small
                        // dot under (or over) it, in the signal's own colour.
                        // It says the pressure repeated and that it is the
                        // SAME trade — one mark, one setup, however many bars
                        // shouted — and while the setup was still armed it is
                        // also why the level is still alive this far along.
                        if (sg.repeatBars) for (const rb of sg.repeatBars) {
                            const rbar = barAt.get(rb.x);
                            const rx = xOf(rb.x, rb.time);
                            if (!rbar || rx === null || rx < 0 || rx > W) continue;
                            const ry = yOf(sg.dir > 0 ? rbar.l : rbar.h);
                            if (ry === null || ry < 0 || ry > H) continue;
                            ctx.fillStyle = withAlpha(base, dead ? 0.35 : 0.75);
                            ctx.beginPath();
                            ctx.arc(rx, ry + (sg.dir > 0 ? 6 * vr : -6 * vr),
                                    Math.max(1.5, 2 * vr), 0, Math.PI * 2);
                            ctx.fill();
                        }

                        // The level the break is taken on. Dashed while it
                        // is only armed, solid once it has been taken.
                        const live = sg.state === 'live' || sg.state === 'target'
                                  || sg.state === 'stop' || sg.state === 'exit';
                        const xTrig = live ? xOf(sg.trigX, sg.trigTime) : null;
                        seg(yOf(sg.level), xA, live && xTrig !== null ? xTrig : xB,
                            withAlpha(base, dead ? 0.5 : 0.95), live ? [] : [4, 3], lw);

                        // Where an armed setup is AIMING, before it has been
                        // taken: the same target, measured from the level, and
                        // drawn fainter so it cannot be mistaken for a trade
                        // that is on. Without it a signal that has not
                        // triggered yet shows a level and nothing else, which
                        // reads as "the target is missing".
                        if (set.ofSigLines && !live && !dead && set.ofSigTrail !== false) {
                            // Trailing: there is no fixed target to show, so
                            // an armed setup shows the STOP it would enter on
                            // — the signal candle's other side.
                            seg(yOf(sg.stop), xA, xB, withAlpha(C.sell, 0.45), [2, 4], lw);
                            tag('SL', xB, yOf(sg.stop), withAlpha(C.sell, 0.75));
                        } else if (set.ofSigLines && !live && !dead && sg.armTarget != null) {
                            seg(yOf(sg.armTarget), xA, xB, withAlpha(C.buy, 0.45), [4, 4], lw);
                            tag(sg.armTargetFrom === 'R' ? 'TR'
                                : sg.armTargetFrom === 'delta' ? 'T' : sg.armTargetFrom,
                                xB, yOf(sg.armTarget), withAlpha(C.buy, 0.75));
                        }

                        if (!set.ofSigLines || !live || xTrig === null) continue;

                        // Taken: the stop under the signal candle and the
                        // target in front, from the breaking bar to
                        // whatever settled it.
                        // A trailing stop is a STEP line, not a level: each
                        // step runs from the bar that set it to the bar that
                        // moved it next, with a riser between. Drawing only
                        // its final level would say the trade was always on
                        // that stop, which is the opposite of the point.
                        const trailing = set.ofSigTrail !== false && sg.trail && sg.trail.length > 0;
                        if (trailing) {
                            seg(yOf(sg.entry), xTrig, xB, withAlpha(base, 0.95), [], lw);
                            let prevY = null;
                            for (let i = 0; i < sg.trail.length; i++) {
                                const st = sg.trail[i], nx = sg.trail[i + 1];
                                const sx = xOf(st.x, st.time);
                                if (sx === null) { prevY = null; continue; }
                                const xa = Math.max(xTrig, sx - slot / 2);
                                const nxc = nx ? xOf(nx.x, nx.time) : null;
                                const xb = nx && nxc !== null ? Math.max(xa, nxc - slot / 2) : xB;
                                const y = yOf(st.price);
                                if (y === null) { prevY = null; continue; }
                                seg(y, xa, xb, withAlpha(C.sell, 0.9), [2, 3], lw);
                                // The riser, so the step reads as one line.
                                if (prevY !== null && y >= 0 && y <= H && prevY >= 0 && prevY <= H) {
                                    ctx.save();
                                    ctx.strokeStyle = withAlpha(C.sell, 0.5);
                                    ctx.lineWidth = lw;
                                    ctx.setLineDash([2 * vr, 3 * vr]);
                                    ctx.beginPath();
                                    ctx.moveTo(Math.round(xa) + 0.5, prevY);
                                    ctx.lineTo(Math.round(xa) + 0.5, y);
                                    ctx.stroke();
                                    ctx.restore();
                                }
                                prevY = y;
                            }
                            // Trailed out is not a loss by definition, so the
                            // glyph follows the PRICE, not the word 'stop'.
                            const good = sg.outPrice == null ? null
                                : sg.dir > 0 ? sg.outPrice > sg.entry : sg.outPrice < sg.entry;
                            tag('SL', xB, yOf(sg.stop), C.sell);
                            tag(sg.state === 'live' ? '·' : good ? '✓' : '✗',
                                xB, yOf(sg.entry), withAlpha(base, 0.95));
                            continue;
                        }

                        const yS = yOf(sg.stop), yT2 = yOf(sg.target), yE = yOf(sg.entry);
                        // A trade that settles on the bar that triggered it
                        // spans half a bar, and a [6,3] dash over 17px is one
                        // dash — which looked like no line at all. The pattern
                        // tightens on a short span so the stop and the target
                        // always read as dashed lines.
                        const span = Math.abs(xB - xTrig);
                        const tight = span < 6 * slot;
                        seg(yE, xTrig, xB, withAlpha(base, 0.95), [], lw);
                        seg(yS, xTrig, xB, withAlpha(C.sell, 0.9), tight ? [2, 2] : [2, 3], lw);
                        seg(yT2, xTrig, xB, withAlpha(C.buy, 0.9), tight ? [3, 2] : [6, 3], lw);
                        // A target ABOVE the top of the pane (or below the
                        // bottom) draws nothing, while the stop — inside the
                        // range — draws normally: a trade with an entry and a
                        // stop and no target at all, which is exactly what it
                        // looks like when the chart is zoomed tighter than the
                        // trade. Say so at the edge instead, with an arrow.
                        if (yT2 !== null && (yT2 < 0 || yT2 > H)) {
                            const edge = yT2 < 0 ? HEADER_PX * vr : H - HEADER_PX * vr;
                            seg(edge, xTrig, xB, withAlpha(C.buy, 0.35), [1, 4], lw);
                            tag((yT2 < 0 ? '\u2191 ' : '\u2193 ')
                                + (sg.targetFrom === 'R' ? 'TR'
                                   : sg.targetFrom === 'delta' ? 'T' : sg.targetFrom),
                                xB, edge, withAlpha(C.buy, 0.8));
                        }
                        {
                            tag('SL', xB, yS, C.sell);
                            // The target names its own source: 'T' an earlier
                            // opposite unusual-delta level, 'VAH' / 'VAL' /
                            // 'POC' the session's value area, 'TR' the
                            // R-multiple fallback when nothing was in front.
                            tag(sg.targetFrom === 'R' ? 'TR'
                                : sg.targetFrom === 'delta' ? 'T' : sg.targetFrom, xB, yT2, C.buy);
                            // ✓ target, ✗ stop, • closed on an opposite
                            // unusual extreme, · still running.
                            tag(sg.state === 'target' ? '✓' : sg.state === 'stop' ? '✗'
                                : sg.state === 'exit' ? '•' : '·',
                                xB, yE, withAlpha(base, 0.95));
                        }
                    }

                    ctx.restore();
                });
            },
        };
    }

    /* ── the primitive: every footprint in one draw() ────────────────────── */
    function makePrimitive() {
        const state = { result: { bars: [] }, series: null, chart: null, requestUpdate: null };

        const renderer = {
            draw(target) {
                const { series, chart, result } = state;
                if (!series || !chart || !result.bars || !result.bars.length) return;
                const set = result.settings || DEFAULTS;
                const head = headerLines(set);       // the figures over each bar, in order
                const dark = isDark();
                const C = dark ? DARK : LIGHT;
                const ts = chart.timeScale();

                target.useBitmapCoordinateSpace(scope => {
                    const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
                    const W = scope.bitmapSize.width, H = scope.bitmapSize.height;
                    const barSpacing = ts.options().barSpacing || 6;
                    const byTime = typeof ts.timeToIndex === 'function';

                    const logicalOf = (x, t) => {
                        if (byTime && t != null) { const i = ts.timeToIndex(t, true); if (i != null) return i; }
                        return x;
                    };
                    const xOf = (x, t) => {
                        const c = ts.logicalToCoordinate(logicalOf(x, t));
                        return c === null ? null : c * hr;
                    };
                    const yOf = p => {
                        const c = series.priceToCoordinate(p);
                        return c === null ? null : c * vr;
                    };

                    // A cell fills its bar's own slot, less a hair each side so
                    // neighbouring bars never touch.
                    const slot = Math.max(2, barSpacing * hr);
                    const gap = Math.min(2 * hr, slot * 0.08);
                    const cellW = slot - gap * 2;
                    const alpha = Math.max(5, Math.min(100, set.ofOpacity)) / 100;
                    const pair = set.ofCell === 'bidask';
                    const wantNum = set.ofNumbers
                        && cellW >= (pair ? MIN_CELL_W_PAIR : MIN_CELL_W_ONE) * hr;

                    ctx.save();
                    ctx.textBaseline = 'middle';

                    const numFont = `${Math.round(NUM_PX * vr)}px Inter, system-ui, sans-serif`;

                    for (const bar of result.bars) {
                        const cx = xOf(bar.x, bar.time);
                        if (cx === null) continue;
                        const l = cx - slot / 2 + gap;
                        if (l + cellW < 0 || l > W) continue;          // off screen

                        const yTop = yOf((bar.maxRow + 1) * result.step);
                        const yBot = yOf(bar.minRow * result.step);
                        if (yTop === null || yBot === null) continue;
                        const rowH = Math.abs(yBot - yTop) / (bar.maxRow - bar.minRow + 1);
                        const showNum = wantNum && rowH >= MIN_CELL_H * vr;
                        // Two different conditions, deliberately kept apart.
                        //
                        // `drawOn` is the USER's switch. Off, nothing this
                        // indicator would paint on the candles is painted: not
                        // the cells, not the figures over the bar, and not the
                        // narrow candle (which only exists to sit between the
                        // cells' two columns — the host puts the chart's own
                        // full-width candle back for the same reason).
                        //
                        // `cells` adds whether this CHART can carry price
                        // cells at all — false on a spot index chart, where
                        // the futures tape's prices sit a basis away. That one
                        // must NOT take the header with it: the per-bar
                        // figures are bucketed by time, they are exact there,
                        // and they are the whole reason the indicator is on
                        // those pages.
                        //
                        // The stats pane below the chart is its own setting
                        // and is not touched by either.
                        const drawOn = set.of !== false && set.ofFootprint !== false;
                        const cells = drawOn && result.cellsOk !== false;
                        // A box drawn round a cell only a few pixels across is
                        // the cell, so a zoomed-out pane came out ringed on
                        // every bar and said nothing. Below this the heat is
                        // the whole reading — bar the stacked bracket, which
                        // sits OUTSIDE the cell and stays legible.
                        const marks = cellW >= MARK_MIN_W * hr && rowH >= MARK_MIN_H * vr;
                        if (showNum) ctx.font = numFont;

                        if (cells) for (const cell of bar.cells) {
                            const yr = yOf((cell.row + 1) * result.step);
                            if (yr === null) continue;
                            const top = Math.round(yr);
                            if (top + rowH < 0 || top > H) continue;
                            const h = Math.max(1, Math.round(rowH) - (rowH > 3 * vr ? Math.round(vr) : 0));

                            // The heat: how big this row is against the bar's
                            // own busiest, tinted by which side owned it. The
                            // share is what makes one bar's shape readable
                            // beside another's however different their volume.
                            const share = bar.widest > 0 ? cell.total / bar.widest : 0;
                            const d = cell.buy - cell.sell;
                            const base = d > 0 ? C.buy : d < 0 ? C.sell : C.flat;
                            ctx.fillStyle = withAlpha(base, alpha * (0.22 + 0.78 * share));
                            ctx.fillRect(Math.round(l), top, Math.round(cellW), h);

                            if (showNum) {
                                ctx.textAlign = 'center';
                                if (pair) {
                                    ctx.fillStyle = cell.sell ? C.sell : C.sub;
                                    ctx.fillText(brief(cell.sell), Math.round(l + cellW * 0.27), top + h / 2);
                                    ctx.fillStyle = cell.buy ? C.buy : C.sub;
                                    ctx.fillText(brief(cell.buy), Math.round(l + cellW * 0.73), top + h / 2);
                                } else if (set.ofCell === 'delta') {
                                    ctx.fillStyle = d > 0 ? C.buy : d < 0 ? C.sell : C.sub;
                                    ctx.fillText(signed(d), Math.round(l + cellW / 2), top + h / 2);
                                } else {
                                    ctx.fillStyle = C.text;
                                    ctx.fillText(brief(cell.total), Math.round(l + cellW / 2), top + h / 2);
                                }
                            }

                            // Imbalances: a box on the side that cleared the
                            // factor — on the figure itself when there are
                            // figures, on that half of the cell when there are
                            // not, so the mark survives a zoomed-out pane.
                            if (marks && set.ofImbalance && (cell.imbBuy || cell.imbSell)) {
                                const w = Math.max(1, Math.round(vr));
                                const halfW = pair && showNum ? cellW / 2 : cellW;
                                const x0 = cell.imbBuy && pair && showNum ? l + cellW / 2 : l;
                                ctx.strokeStyle = cell.imbBuy ? C.buy : C.sell;
                                ctx.lineWidth = w;
                                ctx.strokeRect(Math.round(x0) + w / 2, top + w / 2,
                                               Math.round(halfW) - w, Math.max(1, h - w));
                            }
                            // A stacked run gets a bar down the outside, which
                            // is what makes three-in-a-row visible at a glance.
                            if (set.ofImbalance && cell.stacked) {
                                ctx.fillStyle = C.stack;
                                ctx.fillRect(Math.round(l - 3 * hr), top, Math.max(1, Math.round(2 * hr)), h);
                            }

                            // The point of control: the one row this bar spent
                            // the most volume at, ringed rather than recoloured
                            // so the side tint still reads through it.
                            if (marks && set.ofPoc && cell.row === bar.poc) {
                                const w = Math.max(1, Math.round(1.4 * vr));
                                ctx.strokeStyle = C.poc;
                                ctx.lineWidth = w;
                                ctx.strokeRect(Math.round(l) + w / 2, top + w / 2,
                                               Math.round(cellW) - w, Math.max(1, h - w));
                            }
                        }

                        // The candle, drawn down the middle of the cells. The
                        // figures sit at 27% and 73% of the cell's width, so
                        // the middle is the one strip of a footprint that is
                        // always free — which is exactly why footprint charts
                        // put the candle there. The chart's own series is
                        // painted transparent by the host in this mode; it
                        // still carries the data, so the crosshair, the OHLC
                        // readout and the price line are untouched.
                        if (cells && set.ofCandle === 'narrow' && bar.o != null) {
                            const yO = yOf(bar.o), yC = yOf(bar.c), yH = yOf(bar.h), yL = yOf(bar.l);
                            if (yO !== null && yC !== null && yH !== null && yL !== null) {
                                const up = bar.c >= bar.o;
                                const bw = Math.max(CANDLE_MIN_W * hr,
                                                    Math.min(CANDLE_MAX_W * hr, cellW * CANDLE_W_PCT));
                                const mid = l + cellW / 2;
                                ctx.fillStyle = up ? C.buy : C.sell;
                                const wick = Math.max(1, Math.round(hr));
                                ctx.fillRect(Math.round(mid - wick / 2), Math.round(Math.min(yH, yL)),
                                             wick, Math.max(1, Math.round(Math.abs(yL - yH))));
                                ctx.fillRect(Math.round(mid - bw / 2), Math.round(Math.min(yO, yC)),
                                             Math.max(1, Math.round(bw)),
                                             Math.max(Math.round(vr), Math.round(Math.abs(yC - yO))));
                            }
                        }

                        // Volume / buy / sell / delta / cumulative delta,
                        // stacked above the bar's own high the way the
                        // reference chart prints it. Which of the five is the
                        // user's, one switch each; the stack is however many
                        // of them are on, so turning one off closes the gap
                        // rather than leaving a hole in the block.
                        if (drawOn && set.ofHeader && head.length && cellW >= MIN_CELL_W_ONE * hr) {
                            const yHi = cells ? yOf((bar.maxRow + 1) * result.step)
                                              : (bar.h != null ? yOf(bar.h) : null);
                            if (yHi !== null) {
                                ctx.font = `${Math.round(HEADER_PX * vr)}px Inter, system-ui, sans-serif`;
                                ctx.textAlign = 'center';
                                const mid = Math.round(l + cellW / 2);
                                let y = yHi - (head.length + 0.2) * (HEADER_PX + 2) * vr;
                                for (const [field] of head) {
                                    const v = bar[field];
                                    // Volume has no side, buy is always the
                                    // buy colour and sell the sell one; only
                                    // the two deltas are coloured by sign, and
                                    // only they carry a sign in front.
                                    const color = field === 'volume' ? C.sub
                                        : field === 'buy' ? C.buy
                                        : field === 'sell' ? C.sell
                                        : v >= 0 ? C.buy : C.sell;
                                    const text = field === 'volume' || field === 'buy' || field === 'sell'
                                        ? brief(v) : signed(v);
                                    if (y > 0 && y < H) { ctx.fillStyle = color; ctx.fillText(text, mid, y); }
                                    y += (HEADER_PX + 2) * vr;
                                }
                            }
                        }
                    }

                    ctx.restore();
                });
            },
        };

        const paneView = { renderer: () => renderer, zOrder: () => 'bottom', update() {} };
        const sigView = { renderer: () => makeSignalRenderer(state), zOrder: () => 'top', update() {} };
        return {
            attached(p) { state.series = p.series; state.chart = p.chart; state.requestUpdate = p.requestUpdate; },
            detached() { state.series = null; state.chart = null; state.requestUpdate = null; },
            updateAllViews() {},
            paneViews: () => [paneView, sigView],
            // Like the CPR and TPO primitives, a footprint never moves the
            // price scale: the candles decide the range and the cells fit
            // inside it, because every cell is a price the bar traded at.
            autoscaleInfo: () => null,
            setResult(result) { state.result = result || { bars: [] }; if (state.requestUpdate) state.requestUpdate(); },
        };
    }

    /* ── the bar-stats pane ──────────────────────────────────────────────── */
    // Delta / Max Delta / Min Delta / Cum. Delta used to be painted over the
    // bottom of the price pane, where they sat on top of the volume histogram
    // and the two read as one smear. They now live in a pane of their OWN —
    // Lightweight Charts v5 panes, so the separator between them is the
    // library's and the user drags it to whatever height they want.
    //
    // It is a PANE primitive, not a series one: the pane holds no series at
    // all (`setPreserveEmptyPane` keeps it alive without one), so there is no
    // price scale to fight with and the rows simply divide the height.
    function makeStatsPrimitive() {
        const state = { result: { bars: [] }, chart: null, requestUpdate: null };

        const renderer = {
            draw(target) {
                const { chart, result } = state;
                if (!chart) return;
                const dark = isDark();
                const C = dark ? DARK : LIGHT;
                const ts = chart.timeScale();
                const byTime = typeof ts.timeToIndex === 'function';
                // Only the rows the user left on, and the height divided by
                // however many that is — the pane is not four rows, it is the
                // rows it was asked for.
                const rows = statsRows(result.settings || DEFAULTS);
                if (!rows.length) return;

                target.useBitmapCoordinateSpace(scope => {
                    const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
                    const W = scope.bitmapSize.width, H = scope.bitmapSize.height;
                    const rowH = H / rows.length;
                    const bars = result.bars || [];

                    ctx.save();
                    ctx.textBaseline = 'middle';
                    ctx.font = `${Math.round(STATS_PX * vr)}px Inter, system-ui, sans-serif`;

                    // ONE pixel of air between boxes, in both directions
                    // (STATS_GAP_PX). A box therefore fills its bar's whole
                    // slot and its whole row bar that single gutter — the
                    // 2px-a-side inset it had cost four pixels between
                    // columns and read as a gappy grid rather than a strip.
                    // The row rules are drawn AFTER the boxes for the same
                    // reason: at this tightness a rule under them is a rule
                    // nobody sees.
                    const gapX = Math.max(1, Math.round(STATS_GAP_PX * hr));
                    const gapY = Math.max(1, Math.round(STATS_GAP_PX * vr));

                    if (bars.length) {
                        const barSpacing = ts.options().barSpacing || 6;
                        const slot = Math.max(2, barSpacing * hr);
                        const cellW = Math.max(1, slot - gapX);
                        const logicalOf = (x, t) => {
                            if (byTime && t != null) { const i = ts.timeToIndex(t, true); if (i != null) return i; }
                            return x;
                        };
                        const xOf = (x, t) => {
                            const c = ts.logicalToCoordinate(logicalOf(x, t));
                            return c === null ? null : c * hr;
                        };

                        // Every cell is shaded against the biggest figure in
                        // its OWN row, so a row reads as a row and not as four
                        // different scales stacked on top of each other.
                        const peak = {};
                        for (const [key] of rows) {
                            peak[key] = 1;
                            for (const bar of bars) peak[key] = Math.max(peak[key], Math.abs(bar[key]));
                        }
                        const showText = cellW >= 26 * hr && rowH >= (STATS_PX + 3) * vr;
                        for (const bar of bars) {
                            const cx = xOf(bar.x, bar.time);
                            if (cx === null) continue;
                            // Centred on the bar, like the footprint cell
                            // above it, so a column still sits under its
                            // candle now that the box is the width of the slot.
                            const l = cx - slot / 2;
                            if (l + cellW < 0 || l > W) continue;
                            rows.forEach(([key], i) => {
                                const v = bar[key];
                                const y = i * rowH;
                                // Cumulative delta is a running total, so its
                                // SIGN says almost nothing — a whole session
                                // comes out one colour. What a reader wants
                                // from that row is which way it just moved, so
                                // it is coloured by the change from the bar
                                // before: down is red, up or flat is green.
                                // That change IS this bar's delta, which is
                                // also what makes it right at a session
                                // boundary, where the running total resets.
                                // Buy and Sell are both positive numbers, so
                                // their sign says nothing either: a buy row is
                                // the buy colour and a sell row the sell one,
                                // always, and only the size varies.
                                const dir = key === 'cumDelta' ? bar.delta
                                    : key === 'buy' ? 1 : key === 'sell' ? -1 : v;
                                ctx.fillStyle = withAlpha(dir >= 0 ? C.buy : C.sell,
                                                          0.12 + 0.5 * Math.min(1, Math.abs(v) / peak[key]));
                                ctx.fillRect(Math.round(l), Math.round(y),
                                             Math.round(cellW), Math.max(1, Math.round(rowH) - gapY));
                                if (showText) {
                                    ctx.textAlign = 'center';
                                    ctx.fillStyle = C.text;
                                    ctx.fillText(brief(v), Math.round(l + cellW / 2), y + rowH / 2);
                                }
                            });
                        }
                    }

                    // The row rules, so the rows read as rows even where
                    // a bar has no box to fill — over the boxes, and the same
                    // one pixel the boxes leave for them.
                    ctx.fillStyle = C.panelLine;
                    for (let i = 1; i < rows.length; i++) {
                        ctx.fillRect(0, Math.round(i * rowH) - gapY, W, gapY);
                    }

                    // The row names, pinned to the right where the price axis
                    // already draws — they name the rows without costing a
                    // bar's worth of cells. On their own chip, like the
                    // reference chart's: scrolled hard right there are value
                    // boxes underneath them, and bare text on top of a box is
                    // two numbers in one place.
                    ctx.textAlign = 'right';
                    const padX = 5 * hr, chipH = Math.min(rowH - 4 * vr, 15 * vr);
                    rows.forEach(([, label], i) => {
                        const w = ctx.measureText(label).width;
                        const right = W - 4 * hr;
                        const cy = i * rowH + rowH / 2;
                        if (chipH > 6 * vr) {
                            ctx.fillStyle = withAlpha(dark ? '#0f172a' : '#f1f5f9', 0.88);
                            ctx.fillRect(Math.round(right - w - padX * 2), Math.round(cy - chipH / 2),
                                         Math.round(w + padX * 2), Math.round(chipH));
                        }
                        ctx.fillStyle = withAlpha(dark ? '#e2e8f0' : '#1f2937', 0.75);
                        ctx.fillText(label, right - padX, cy);
                    });
                    ctx.restore();
                });
            },
        };

        const paneView = { renderer: () => renderer, zOrder: () => 'top' };
        return {
            attached(p) { state.chart = p.chart; state.requestUpdate = p.requestUpdate; },
            detached() { state.chart = null; state.requestUpdate = null; },
            updateAllViews() {},
            paneViews: () => [paneView],
            setResult(result) { state.result = result || { bars: [] }; if (state.requestUpdate) state.requestUpdate(); },
        };
    }

    // pane = { chart, ofStatsPane, ofStatsPrimitive }
    function attachStats(pane, result) {
        const set = (result && result.settings) || DEFAULTS;
        const rows = statsRows(set).length;
        // No rows left on is the same as the pane switched off: an empty strip
        // under the chart is height taken from the candles for nothing.
        if (set.of === false || !set.ofStats || !rows || !result || !result.bars || !result.bars.length) { detachStats(pane); return; }
        // The opening height is the rows' height, so a change to how many
        // there are re-opens it at the new one — the drag is only the user's
        // until they ask for a different pane.
        if (pane.ofStatsRows !== rows) { pane.ofStatsRows = rows; pane.ofStatsSized = false; }
        if (!pane.ofStatsPane) {
            const p = pane.chart.addPane(true);
            p.setPreserveEmptyPane(true);       // it holds a primitive and no series
            pane.ofStatsPane = p;
            pane.ofStatsSized = false;
            pane.ofStatsPrimitive = makeStatsPrimitive();
            p.attachPrimitive(pane.ofStatsPrimitive);
        }
        scheduleStatsSizing(pane);
        pane.ofStatsPrimitive.setResult(result);
    }

    // Opens the pane at its rows' own height, ONCE — the separator is the
    // user's from then on, so this must never run twice for one row count.
    //
    // Through stretch factors, not `setHeight`: stretch is what the layout is
    // actually built on, and setHeight resolves against whatever height the
    // pane has right now — on a chart that has not been laid out yet that is
    // nothing, and a 90px ask opened at 310px. It is also why this is retried
    // rather than done on creation or on the next frame: the pane is autoSized,
    // so the first honest heights arrive a beat later. Every applyOrderFlow
    // calls in here, and the first one that finds a laid-out chart sizes it.
    // The chart is autoSized inside a grid that is laid out after this runs,
    // so the first honest heights arrive a beat later — and on a page with no
    // live tick (a closed market) there is no second pass to catch them. Hence
    // a short poll rather than a single retry: it stops the moment the size is
    // final, and gives up after a few seconds rather than spinning forever on
    // a pane that is genuinely too short to ever be roomy.
    function scheduleStatsSizing(pane) {
        sizeStatsPane(pane);
        if (pane.ofStatsSized || pane.ofStatsSizing) return;
        const started = Date.now();
        pane.ofStatsSizing = setInterval(() => {
            sizeStatsPane(pane);
            if (pane.ofStatsSized || !pane.ofStatsPane || Date.now() - started > 5000) {
                clearInterval(pane.ofStatsSizing);
                pane.ofStatsSizing = null;
            }
        }, 120);
    }

    function sizeStatsPane(pane) {
        if (pane.ofStatsSized || !pane.ofStatsPane) return;
        try {
            const main = pane.chart.panes()[0];
            const mine = pane.ofStatsPane;
            const total = main.getHeight() + mine.getHeight();
            if (!(total > 0)) return;
            // The cap stops the stats eating a short chart. It is also the
            // signal that the layout is not settled yet: a chart mid-load
            // measures a couple of hundred pixels, the cap fires, and latching
            // THAT left the pane at a third of a chart that then grew to twice
            // the size. So the size is only final once the chart is tall
            // enough to give the rows their own height.
            const open = statsOpenPx(pane.ofStatsRows || STATS_ROWS.length);
            const roomy = total * STATS_MAX_SHARE >= open;
            const want = roomy ? open : total * STATS_MAX_SHARE;
            mine.setStretchFactor(main.getStretchFactor() * want / Math.max(1, total - want));
            if (roomy) pane.ofStatsSized = true;
        } catch (e) { /* try again on the next pass */ }
    }

    function detachStats(pane) {
        if (pane.ofStatsSizing) { clearInterval(pane.ofStatsSizing); pane.ofStatsSizing = null; }
        if (!pane.ofStatsPane) return;
        try { pane.ofStatsPane.detachPrimitive(pane.ofStatsPrimitive); } catch (e) {}
        try { pane.chart.removePane(pane.ofStatsPane.paneIndex()); } catch (e) {}
        pane.ofStatsPane = null;
        pane.ofStatsPrimitive = null;
        pane.ofStatsSized = false;
        pane.ofStatsRows = 0;
    }

    /* ── attach / detach ─────────────────────────────────────────────────── */
    // pane = { chart, series, ofPrimitive }
    function attach(pane, result) {
        if (!pane.ofPrimitive) {
            pane.ofPrimitive = makePrimitive();
            pane.series.attachPrimitive(pane.ofPrimitive);
        }
        pane.ofPrimitive.setResult(result);
    }

    function detach(pane) {
        if (pane.ofPrimitive) {
            try { pane.series.detachPrimitive(pane.ofPrimitive); } catch (e) {}
            pane.ofPrimitive = null;
        }
        detachStats(pane);
    }

    /* ── one call a host page can drive the whole indicator from ─────────── */
    // Three pages draw this now — Multichart, OI Profile and Replay — and only
    // two things differ between them: where the tape's contract comes from,
    // and whether the chart can carry price cells. Everything else (asking the
    // tape for the days on screen, computing, attaching, the stats pane, the
    // wording on the label) is the same, so it lives here rather than three
    // times over.
    //
    // pane: { chart, series, ofCache, ... } — the same object attach() takes.
    // opts: { root, futureSymbol, cells, on }
    //   root         the underlying, e.g. 'NIFTY' — what the archive is keyed by
    //   futureSymbol today's contract if the host already has it; else resolved
    //   cells        false on a chart that is not the tape's own instrument
    //   on           false to clear the indicator off this pane entirely
    // Returns { result, text, title } — the label the host shows, if it has one.
    function apply(pane, candles, interval, settings, opts) {
        const o = opts || {};
        const s = key => (settings && key in settings) ? settings[key] : DEFAULTS[key];
        if ((!s('of') && !s('ofSig')) || o.on === false) {
            attach(pane, { bars: [] });
            detachStats(pane);
            return { result: { bars: [] }, text: '', title: '' };
        }

        const sessions = sessionDays(candles, s('ofSessions'));
        const today = dayKey(Math.floor(Date.now() / 1000) + IST);
        const futureSymbol = o.futureSymbol || Tape.front(o.root);
        Tape.want(o.root, sessions, today, futureSymbol);

        const result = compute(candles, interval, settings, pane.ofCache,
                               { cells: o.cells !== false, profiles: o.profiles });
        attach(pane, result);
        attachStats(pane, result);

        const miss = (result.missing || [])[0];
        if (!result.bars.length && miss) {
            // `why` is the server's own word on a day it could not rebuild —
            // ICICI not connected, no 1-second history, too many days at once.
            // Worth showing: every one of them is actionable, and "no tape"
            // alone reads as a dead end when it usually is not.
            const why = miss.why && /^unavailable:/.test(miss.why) ? miss.why.slice('unavailable:'.length) : null;
            return {
                result,
                text: miss.state === 'empty' ? `OF · no tape ${miss.day}`
                    : miss.state === 'error' ? 'OF · tape error' : 'OF · loading tape…',
                title: miss.state === 'empty'
                    ? (why ? `No tape for ${miss.day}: ${why}. Days the app did not watch are rebuilt from `
                           + 'ICICI 1-second history on demand, which needs an ICICI login.'
                           : `Nothing was taped for ${miss.day}, and it could not be rebuilt from history.`)
                    : 'Reading /api/time-and-sales for the sessions on screen — a day that was never '
                      + 'taped is being rebuilt from 1-second history, which takes a few seconds.',
            };
        }
        if (!result.bars.length) return { result, text: '', title: '' };

        const sigBase = '\u0394X: a bar whose intra-bar Max/Min Delta is unusual for its session arms '
            + 'its own high (buy extreme) or low (sell extreme); the break of that candle is the entry, '
            + 'the other side of the same candle is the stop, which then TRAILS: a bar that closes '
            + 'against the trade and prints a notable opposing delta moves it to that bar\u2019s far side, '
            + 'never loosening it, and the trade ends when it is hit. With trailing off the exit is a '
            + 'fixed target instead \u2014 whichever is nearest in '
            + 'front \u2014 the session\u2019s VAH / VAL / POC (from the TPO profile on this pane) or an earlier '
            + 'opposite unusual-delta level. The trade also closes if the OTHER side prints an unusual '
            + 'extreme first. One signal per side at a time: while a long is still '
            + 'armed or running, another unusual Max Delta joins it (a dot on that bar) and re-arms the '
            + 'wait for the same break rather than starting a second long. Marks are what the rule would have done, stop-first '
            + 'inside a bar that held both \u2014 the bar\u2019s own delta is an estimate, so read them as a '
            + 'reading of the flow, not as fills.';
        const base = 'Order flow from the trade tape. Volume is the exchange\u2019s own. Most rows are '
            + 'ICICI 1-second bars with no bid/ask, so the buy/sell split is the tick test on the '
            + 'second-by-second path, unchanged seconds halved \u2014 the cumulative delta tracks a true '
            + 'bid/ask feed closely, a single bar\u2019s delta is an estimate.';
        // What the label says the indicator is doing. ΔX is counted when it
        // is on, because a signal layer that found nothing today and one that
        // is switched off look identical on the chart otherwise.
        const nSig = s('ofSig') ? (result.signals || []).length : 0;
        const sigTag = s('ofSig') ? ` · ΔX ${nSig}` : '';
        return {
            result,
            text: (result.cellsOk ? `OF · ${result.step} pt · 1s tape` : 'OF · delta only') + sigTag,
            title: (s('ofSig') ? sigBase + ' ' : '') + (result.cellsOk ? base
                : base + ' The tape is collected on the FUTURE, so on this index chart every price in '
                       + 'it sits a basis away from the candles and the price cells are not drawn. The '
                       + 'per-bar figures are bucketed by time, not price, so they are exact here.'),
        };
    }

    // The last `n` session dates a set of candles covers, newest first — the
    // days the tape has to hold for those bars to get a footprint. `n` of 0
    // (the default) is every session the candles cover, however far back they
    // go; Tape.want is what keeps that from becoming a stampede.
    function sessionDays(candles, n) {
        const out = [];
        if (!candles) return out;
        const want = Math.max(0, n | 0) || Infinity;
        for (let i = candles.length - 1; i >= 0 && out.length < want; i--) {
            const key = dayKey(candles[i].time);
            if (out[out.length - 1] !== key) out.push(key);
        }
        return out;
    }

    /* ── settings popup section ──────────────────────────────────────────── */
    // Rendered by MineCPR.renderSettings, so the rows read and behave exactly
    // like the CPR and TPO ones and a page only has to concat this in.
    const SPEC = [
        { title: 'Order Flow (footprint)', gate: 'showOf', gateLabel: 'intraday · futures', items: [
            { key: 'of', label: 'Order flow footprint', color: '#0ea5e9' },
            { key: 'ofFootprint', label: 'Draw the cells on the chart', sub: true,
              gate: 'ofCells', gateLabel: 'futures chart' },
            { key: 'ofCell', type: 'select', label: 'Cell shows', options: CELL_OPTIONS, sub: true },
            { key: 'ofSessions', type: 'number', label: 'Sessions back (tape, 0 = all on chart)', min: 0, max: 400, sub: true },
            { key: 'ofRowStep', type: 'number', label: 'Row size in points (0 = auto)', min: 0, max: 1000, sub: true },
            { key: 'ofRowsTarget', type: 'number', label: 'Rows per bar (when auto)', min: 2, max: 60, sub: true },
            { key: 'ofBars', type: 'number', label: 'Bars drawn (newest, 0 = all)', min: 0, max: 100000, sub: true },
            { key: 'ofOpacity', type: 'number', label: 'Cell opacity %', min: 5, max: 100, sub: true },
            { key: 'ofNumbers', label: 'Print the figures', sub: true },
            { key: 'ofPoc', label: 'Ring each bar’s POC row', color: COLORS.poc, sub: true },
            { key: 'ofImbalance', label: 'Diagonal imbalance', color: COLORS.stack, sub: true },
            { key: 'ofImbFactor', type: 'number', label: 'Imbalance %, of the diagonal', min: 120, max: 2000, sub: true },
            { key: 'ofStack', type: 'number', label: 'Stacked imbalance: rows in a row', min: 2, max: 10, sub: true },
            // Drawn on the candles, so it follows 'Draw the cells on the chart'
            // above: off, this indicator paints nothing over the price pane —
            // the five lines below go with it. The stats pane after them is
            // separate and keeps running.
            { key: 'ofHeader', label: 'Figures over each bar', sub: true },
            { key: 'ofHeadVolume', label: 'Volume', sub: 2 },
            { key: 'ofHeadBuy', label: 'Buy', color: COLORS.buy, sub: 2 },
            { key: 'ofHeadSell', label: 'Sell', color: COLORS.sell, sub: 2 },
            { key: 'ofHeadDelta', label: 'Delta', sub: 2 },
            { key: 'ofHeadCumDelta', label: 'Cum. Delta', sub: 2 },
            { key: 'ofStats', label: 'Bar-stats pane (drag its edge to resize)', sub: true },
            { key: 'ofStatBuy', label: 'Buy', color: COLORS.buy, sub: 2 },
            { key: 'ofStatSell', label: 'Sell', color: COLORS.sell, sub: 2 },
            { key: 'ofStatDelta', label: 'Delta', sub: 2 },
            { key: 'ofStatMaxDelta', label: 'Max Delta', sub: 2 },
            { key: 'ofStatMinDelta', label: 'Min Delta', sub: 2 },
            { key: 'ofStatCumDelta', label: 'Cum. Delta', sub: 2 },
            { key: 'ofCandle', type: 'select', label: 'Candle', options: CANDLE_OPTIONS, sub: true },
        ] },
        // Its own section rather than another sub-row of the footprint: it is
        // a signal read off the bars, it has its own switch, and it is valid
        // on an index chart where the cells are not. It still belongs to this
        // engine — the two numbers it reads are the ones the footprint
        // computes — so it is rendered with it, under it, on all three pages.
        { title: 'Delta Extreme Breakout (ΔX)', gate: 'showOf', gateLabel: 'intraday', items: [
            { key: 'ofSig', label: 'Unusual Max/Min Delta breakout', color: COLORS.sig },
            { key: 'ofSigLook', type: 'number', label: 'Judge against the last N bars', min: 10, max: 200, sub: true },
            { key: 'ofSigMult', type: 'number', label: 'Unusual at × deviation over median', min: 1, max: 10, step: 0.5, sub: true },
            { key: 'ofSigVolPct', type: 'number', label: '…and ≥ this % of the bar’s volume', min: 0, max: 100, sub: true },
            { key: 'ofSigAbsorb', label: 'Drop absorbed bars (close denies the extreme)', sub: true },
            { key: 'ofSigBars', type: 'number', label: 'Bars the level stays armed', min: 1, max: 30, sub: true },
            { key: 'ofSigConfirm', label: 'Cumulative delta must agree at the break', sub: true },
            { key: 'ofSigVoid', label: 'Cancel if the candle\u2019s other side breaks first', sub: true },
            { key: 'ofSigFlip', label: 'Exit on an opposite unusual delta', sub: true },
            { key: 'ofSigRR', type: 'number', label: 'Fallback target in R (no VAH/VAL/ΔX level in front)', min: 1, max: 10, step: 0.5, sub: true },
            { key: 'ofSigLines', label: 'Draw entry / SL / target lines', sub: true },
        ] },
    ];

    return {
        DEFAULTS, COLORS, SPEC, CELL_OPTIONS, CANDLE_OPTIONS, Tape,
        tfInfo, compute, attach, detach, attachStats, detachStats, apply, sessionDays,
        buildSignals, unusualThreshold,
        niceStep, signRows, brief,
    };
})();
