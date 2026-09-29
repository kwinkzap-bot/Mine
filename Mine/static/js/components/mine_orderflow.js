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
        ofSessions: 1,           // sessions of tape to load and draw, newest back
        ofBars: 200,             // never footprint more bars than this, whatever is loaded
        ofNumbers: true,         // print the figures; off leaves the heat map alone
        ofPoc: true,             // ring the bar's busiest row
        ofImbalance: true,       // diagonal bid/ask imbalance
        ofImbFactor: 300,        // an imbalance is this percent of the diagonal cell
        ofStack: 3,              // this many in a row is a stacked imbalance, bracketed
        ofHeader: true,          // volume / delta / cumulative delta above each bar
        ofStats: true,           // the bar-stats pane under the chart
        ofOpacity: 45,           // cell fill, percent — the candles are read through them
        // How the candle is drawn while a footprint is on it. 'narrow' is the
        // footprint idiom and the default: the chart's own candle is hidden
        // and a thin one is drawn down the middle of the cells, in the gap
        // between the sell column and the buy column, so it can never cover a
        // figure. 'full' and 'hollow' are the chart's own candle at full bar
        // width, over the cells.
        ofCandle: 'narrow',
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
        panel: '#ffffff', panelLine: '#e2e8f0',
    };
    const DARK = {
        buy: '#2dd4a7', sell: '#ff5a6e', flat: '#64748b',
        text: '#e2e8f0', sub: '#94a3b8',
        poc: '#fbbf24',
        stack: '#a78bfa',
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
    const HEADER_PX = 9, HEADER_LINES = 3;
    const STATS_PX = 9;
    const STATS_ROWS = [
        ['delta', 'Delta'], ['maxDelta', 'Max Delta'], ['minDelta', 'Min Delta'], ['cumDelta', 'Cum. Delta'],
    ];
    // The height the stats pane OPENS at: its four rows and a little air,
    // in CSS px. An absolute height rather than a share of the chart, because
    // what it has to fit is four lines of 9px text however tall the pane above
    // it is — a proportional one opened at a third of the chart for four rows.
    // Set ONCE, when the pane is created: the separator is draggable, and
    // re-applying this on every redraw would pull it back from under the user.
    const STATS_ROW_PX = 21;
    const STATS_OPEN_PX = STATS_ROWS.length * STATS_ROW_PX + 6;
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

        async function fetchArchived(day, root) {
            const e = entry(day);
            const key = `day:${day}`;
            if (inflight.has(key)) return;
            inflight.add(key);
            e.state = 'loading';
            try {
                const list = await archivedDays(root);
                const hit = list.find(d => d.day === day);
                if (!hit) { e.state = 'empty'; changed(); return; }
                const body = await getJSON(`/api/time-and-sales?symbol=${encodeURIComponent(hit.symbol)}`
                    + `&day=${day}&limit=${ROW_LIMIT}`);
                e.symbol = hit.symbol;
                e.rows = body.rows || [];
                e.live = false;
                e.state = e.rows.length ? 'ready' : 'empty';
                changed();
            } catch (err) {
                e.state = 'error';
                e.error = err.message;
                changed();
            } finally {
                inflight.delete(key);
            }
        }

        // Ask for the days a pane wants drawn. `todayKey`/`todaySymbol` name
        // the session still being taped — the only one polled again once it is
        // held. Everything else is fetched once.
        function want(root, wanted, todayKey, todaySymbol) {
            if (!root) return;
            for (const day of wanted) {
                const e = entry(day);
                if (day === todayKey && todaySymbol) {
                    fetchLive(day, todaySymbol);
                } else if (e.state === 'idle' || (e.state === 'error' && !e.rows.length && !inflight.has(`day:${day}`))) {
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
        const empty = { bars: [], step: 0, sessions: [], missing: [], cellsOk, coverage: Tape.coverage() };
        if (!s('of') || !info.showOf || !candles || !candles.length) return empty;

        const secs = info.secs;
        const wanted = Math.max(1, s('ofSessions') | 0);

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
        const used = sessions.slice(-wanted);
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
            if (!rows) { missing.push({ day: sess.key, state: Tape.state(sess.key) }); continue; }
            if (!rows.length) { missing.push({ day: sess.key, state: 'empty' }); continue; }

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

        // The newest bars are the ones anyone reads; an older one off the left
        // of the screen is skipped in the draw anyway, but capping here keeps
        // the array itself small on a 1-minute pane with several sessions.
        const cap = Math.max(10, s('ofBars') | 0);
        return {
            bars: bars.length > cap ? bars.slice(-cap) : bars,
            step, secs, cellsOk,
            sessions: used.map(x => x.key),
            missing,
            coverage: Tape.coverage(),
            settings: Object.fromEntries(Object.keys(DEFAULTS).map(k => [k, s(k)])),
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
                        const drawOn = set.ofFootprint !== false;
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

                        // Volume / delta / cumulative delta, stacked above the
                        // bar's own high the way the reference chart prints it.
                        if (drawOn && set.ofHeader && cellW >= MIN_CELL_W_ONE * hr) {
                            const yHi = cells ? yOf((bar.maxRow + 1) * result.step)
                                              : (bar.h != null ? yOf(bar.h) : null);
                            if (yHi !== null) {
                                ctx.font = `${Math.round(HEADER_PX * vr)}px Inter, system-ui, sans-serif`;
                                ctx.textAlign = 'center';
                                const mid = Math.round(l + cellW / 2);
                                const lines = [
                                    [brief(bar.volume), C.sub],
                                    [signed(bar.delta), bar.delta >= 0 ? C.buy : C.sell],
                                    [signed(bar.cumDelta), bar.cumDelta >= 0 ? C.buy : C.sell],
                                ];
                                let y = yHi - (HEADER_LINES + 0.2) * (HEADER_PX + 2) * vr;
                                for (const [text, color] of lines) {
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
        return {
            attached(p) { state.series = p.series; state.chart = p.chart; state.requestUpdate = p.requestUpdate; },
            detached() { state.series = null; state.chart = null; state.requestUpdate = null; },
            updateAllViews() {},
            paneViews: () => [paneView],
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
    // price scale to fight with and the four rows simply divide the height.
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

                target.useBitmapCoordinateSpace(scope => {
                    const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
                    const W = scope.bitmapSize.width, H = scope.bitmapSize.height;
                    const rowH = H / STATS_ROWS.length;
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
                        for (const [key] of STATS_ROWS) {
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
                            STATS_ROWS.forEach(([key], i) => {
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
                                const dir = key === 'cumDelta' ? bar.delta : v;
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

                    // The row rules, so four rows read as four rows even where
                    // a bar has no box to fill — over the boxes, and the same
                    // one pixel the boxes leave for them.
                    ctx.fillStyle = C.panelLine;
                    for (let i = 1; i < STATS_ROWS.length; i++) {
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
                    STATS_ROWS.forEach(([, label], i) => {
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
        if (!set.ofStats || !result || !result.bars || !result.bars.length) { detachStats(pane); return; }
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

    // Opens the pane at its four rows, ONCE — the separator is the user's from
    // then on, so this must never run twice.
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
            // enough to give the four rows their own height.
            const roomy = total * STATS_MAX_SHARE >= STATS_OPEN_PX;
            const want = roomy ? STATS_OPEN_PX : total * STATS_MAX_SHARE;
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
        if (!s('of') || o.on === false) {
            attach(pane, { bars: [] });
            detachStats(pane);
            return { result: { bars: [] }, text: '', title: '' };
        }

        const sessions = sessionDays(candles, s('ofSessions'));
        const today = dayKey(Math.floor(Date.now() / 1000) + IST);
        const futureSymbol = o.futureSymbol || Tape.front(o.root);
        Tape.want(o.root, sessions, today, futureSymbol);

        const result = compute(candles, interval, settings, pane.ofCache, { cells: o.cells !== false });
        attach(pane, result);
        attachStats(pane, result);

        const miss = (result.missing || [])[0];
        if (!result.bars.length && miss) {
            return {
                result,
                text: miss.state === 'empty' ? `OF · no tape ${miss.day}`
                    : miss.state === 'error' ? 'OF · tape error' : 'OF · loading tape…',
                title: miss.state === 'empty'
                    ? `Nothing was taped for ${miss.day} — the archive only holds sessions the app watched.`
                    : 'Reading /api/time-and-sales for the sessions on screen.',
            };
        }
        if (!result.bars.length) return { result, text: '', title: '' };

        const base = 'Order flow from the trade tape. Volume is the exchange\u2019s own. Most rows are '
            + 'ICICI 1-second bars with no bid/ask, so the buy/sell split is the tick test on the '
            + 'second-by-second path, unchanged seconds halved \u2014 the cumulative delta tracks a true '
            + 'bid/ask feed closely, a single bar\u2019s delta is an estimate.';
        return {
            result,
            text: result.cellsOk ? `OF · ${result.step} pt · 1s tape` : 'OF · delta only',
            title: result.cellsOk ? base
                : base + ' The tape is collected on the FUTURE, so on this index chart every price in '
                       + 'it sits a basis away from the candles and the price cells are not drawn. The '
                       + 'per-bar figures are bucketed by time, not price, so they are exact here.',
        };
    }

    // The last `n` session dates a set of candles covers, newest first — the
    // days the tape has to hold for those bars to get a footprint.
    function sessionDays(candles, n) {
        const out = [];
        if (!candles) return out;
        for (let i = candles.length - 1; i >= 0 && out.length < Math.max(1, n | 0); i--) {
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
            { key: 'ofSessions', type: 'number', label: 'Sessions back (tape)', min: 1, max: 20, sub: true },
            { key: 'ofRowStep', type: 'number', label: 'Row size in points (0 = auto)', min: 0, max: 1000, sub: true },
            { key: 'ofRowsTarget', type: 'number', label: 'Rows per bar (when auto)', min: 2, max: 60, sub: true },
            { key: 'ofBars', type: 'number', label: 'Bars drawn (newest)', min: 10, max: 2000, sub: true },
            { key: 'ofOpacity', type: 'number', label: 'Cell opacity %', min: 5, max: 100, sub: true },
            { key: 'ofNumbers', label: 'Print the figures', sub: true },
            { key: 'ofPoc', label: 'Ring each bar’s POC row', color: COLORS.poc, sub: true },
            { key: 'ofImbalance', label: 'Diagonal imbalance', color: COLORS.stack, sub: true },
            { key: 'ofImbFactor', type: 'number', label: 'Imbalance %, of the diagonal', min: 120, max: 2000, sub: true },
            { key: 'ofStack', type: 'number', label: 'Stacked imbalance: rows in a row', min: 2, max: 10, sub: true },
            // Drawn on the candles, so it follows 'Draw the cells on the chart'
            // above: off, this indicator paints nothing over the price pane.
            // The stats pane below is separate and keeps running.
            { key: 'ofHeader', label: 'Volume / delta / cum Δ over each bar', sub: true },
            { key: 'ofStats', label: 'Bar-stats pane (drag its edge to resize)', sub: true },
            { key: 'ofCandle', type: 'select', label: 'Candle', options: CANDLE_OPTIONS, sub: true },
        ] },
    ];

    return {
        DEFAULTS, COLORS, SPEC, CELL_OPTIONS, CANDLE_OPTIONS, Tape,
        tfInfo, compute, attach, detach, attachStats, detachStats, apply, sessionDays,
        niceStep, signRows, brief,
    };
})();
