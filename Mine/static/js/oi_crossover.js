/* ================================================================
   OI Crossover scanner.

   The table is the shared DataGrid (click-to-sort comes free); this file
   owns the filter bar, the auto-refresh, and the row drilldown that draws
   the two OI-change lines whose crossing is the whole signal.

   Filtering happens here rather than server-side: the API returns one row
   per symbol that crossed today — a few hundred at most — so shipping the
   whole set once and narrowing it in the browser keeps every dropdown
   instant and costs one request per refresh instead of one per keystroke.

   Lives as a tab on the Algo page, so it exposes window.OIX.activate() /
   .deactivate() and does nothing until the tab is opened — a hidden tab
   must not hold a 60s poll open. algoSwitch() in algo.js drives both.
   ================================================================ */

(function () {
    'use strict';

    const API = '/api/oi-crossover';
    const REFRESH_MS = 60_000;

    const state = {
        mode: 'live',
        date: null,
        rows: [],
        scans: 0,
        symbols: 0,
        lastRun: null,
        // Defaults mirror the `selected` options in the template; init() reads
        // them back off the selects so the two can never drift, and so a soft
        // reload (which restores the user's last dropdown values) doesn't leave
        // the state saying one thing and the filter bar showing another.
        filters: { search: '', quality: 'strong', crossCount: 0, oiChg: 0,
                   separation: 0, status: 'all' },
        openSymbol: null,
        // The drilldown chart is per symbol, but the table is one row per
        // cross, so a symbol can own several rows. Navigation and the open-row
        // highlight track the row; only the chart fetch uses the symbol.
        openId: null,
        hiddenSeries: new Set(),
        chartMode: 'oi_change',
        chartData: null,
        displayRows: [],
        timer: null,
        // symbol -> {strike, ce_ltp, pe_ltp}, filled in by a batch call for
        // the rows on screen. null means "asked, and the chain had no answer",
        // which is why it is distinguished from undefined ("not asked yet").
        atm: {},
        atmPending: false,
        atmAt: 0,
        // symbol -> {strike, option_type, qty, entry, ltp, pnl} for today's
        // filled orders from this screen. Empty for every row never traded.
        positions: {},
        started: false,
        // The row the open ticket is for. The order itself is built at send
        // time, so the price the user typed is the price that goes out.
        ticketRow: null,
        ticketCloseTimer: null,
    };

    const $ = (id) => document.getElementById(id);

    // ── formatting ───────────────────────────────────────────────────

    // 35700000 → "3.57 Cr". Indian market convention: crore, lakh, thousand.
    function compact(v) {
        if (v === null || v === undefined || v === '') return '—';
        const n = Number(v);
        const abs = Math.abs(n);
        const sign = n < 0 ? '-' : '';
        if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)} Cr`;
        if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(2)} L`;
        if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(2)} K`;
        return `${sign}${abs}`;
    }

    // Price axis: no currency symbol (the axis is unambiguous) and a decimal
    // only below ₹1000, where a rupee of movement is worth seeing.
    function money(v) {
        if (v === null || v === undefined || v === '') return '—';
        const n = Number(v);
        return n.toLocaleString('en-IN', {
            minimumFractionDigits: Math.abs(n) < 1000 ? 2 : 1,
            maximumFractionDigits: Math.abs(n) < 1000 ? 2 : 1,
        });
    }

    // Strikes come back as floats; Number() drops the trailing ".0" on the
    // whole-rupee ones and leaves the half-rupee strikes intact.
    const strikeText = (v) => String(Number(v));

    const hhmm = (ts) => (ts || '').slice(11, 16);

    const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    const to12 = (h, m) =>
        `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;

    // "15:40" → "3:40 PM". Timestamps are naive local (IST) strings, so the
    // clock is read straight off the string — never via Date.toISOString(),
    // which would shift them to UTC and put the session at 4 AM.
    function clock12(ts) {
        const t = hhmm(ts);
        if (!t) return '—';
        const [h, m] = t.split(':').map(Number);
        return to12(h, m);
    }

    const clock12Date = (d) => to12(d.getHours(), d.getMinutes());

    // ── filtering ────────────────────────────────────────────────────

    // Every gate below reads a field frozen at the instant the cross fired,
    // never the live one. That is the whole point: the row used to be judged
    // against the newest 3-minute scan, so a signal that had not changed drifted
    // in and out of the table and could not be traded from. Membership is now
    // decided once, when the cross happens, and never moves again — the live
    // numbers still ride along on the row, but only as the Status column's
    // decay reading. The one exception is Status itself, which the user is
    // explicitly asking to filter on.
    function visibleRows() {
        const f = state.filters;
        const needle = f.search.trim().toUpperCase();
        return state.rows.filter((r) => {
            if (needle && !r.symbol.includes(needle)) return false;
            if (f.quality !== 'all' && r.quality !== f.quality) return false;
            if (f.crossCount && r.cross_seq > f.crossCount) return false;
            if (f.oiChg && (r.oi_chg_pct === null || r.oi_chg_pct < f.oiChg)) return false;
            if (f.separation && (r.separation === null || r.separation < f.separation)) return false;
            if (!statusAllowed(r.status, f.status)) return false;
            return true;
        });
    }

    // 'open' is the default: what has not yet been contradicted. A flipped
    // cross stays reachable rather than vanishing, because knowing a signal
    // you took has been invalidated matters more than tidying it away.
    function statusAllowed(status, want) {
        if (want === 'all') return true;
        if (want === 'open') return status === 'LIVE' || status === 'PENDING';
        return status === want;
    }

    // ── table ────────────────────────────────────────────────────────

    // The cross direction picks the leg: a BULL buys the call, a BEAR the put.
    // One definition, used by the two ATM columns, the Buy button and the
    // ticket, so they can never disagree about which contract is meant.
    const sideOf = (row) => (row.direction === 'BULL' ? 'CE' : 'PE');

    // The resolved quote, or null while it is still pending or unavailable —
    // callers that only care "do we have a strike" read this.
    const atmOf = (row) => state.atm[row.symbol] || null;

    // Live spot from the chain, falling back to the one the scan recorded.
    // `live` says which, so the cell can dim a scan-age price — and so the
    // column sorts on exactly the number it is showing.
    const spotOf = (row) => {
        const q = atmOf(row);
        if (q && q.spot != null) return { value: q.spot, live: true };
        if (row.spot != null) return { value: row.spot, live: false };
        return null;
    };

    const atmPrice = (row) => {
        const q = atmOf(row);
        if (!q) return null;
        const ltp = sideOf(row) === 'CE' ? q.ce_ltp : q.pe_ltp;
        return (ltp === null || ltp === undefined) ? null : ltp;
    };

    // ── crossover-time prices ────────────────────────────────────────

    // The row's headline numbers are the market at the moment the lines
    // crossed, not the market now: a 9:30 cross is a statement about 9:30.
    // The live price stays on screen underneath it, because the decision the
    // row invites is "is this still worth taking", which needs both.
    const num = (v) => (v === null || v === undefined ? null : Number(v));

    // Percent move from the cross price to now — the number that says whether
    // the signal has already been paid out. Null unless both ends are real.
    function moveSince(from, to) {
        if (from === null || to === null || !from) return null;
        return (to - from) / Math.abs(from) * 100;
    }

    const signed = (pct) => `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;

    // The second line under a cross-time price: what it is worth now, and how
    // far it has come since. Historical mode has no live feed at all, so it
    // gets nothing rather than a permanent "…".
    function liveSub(row, liveValue, from, prefix) {
        if (state.mode === 'historical') return '';
        const wrap = (inner, cls) =>
            `<span class="oix-live-sub${cls ? ' ' + cls : ''}">${inner}</span>`;
        if (state.atm[row.symbol] === undefined) return wrap('…', 'oix-atm-wait');
        if (liveValue === null) return wrap('—', 'oix-atm-wait');
        const pct = moveSince(from, liveValue);
        const move = pct === null ? ''
            : ` <span class="oix-move oix-move-${pct >= 0 ? 'pos' : 'neg'}">` +
              `${DataGrid.escape(signed(pct))}</span>`;
        return wrap(`${prefix}${DataGrid.escape(money(liveValue))}${move}`);
    }

    const COLUMNS = [
        {
            key: 'symbol', label: 'Symbol', sortable: true, strong: true,
            render: (v) => `<span class="oix-sym">${DataGrid.escape(v)}` +
                           `<i class="oix-chart-hint" title="Show OI change lines">📈</i></span>`,
        },
        {
            key: 'direction', label: 'Crossover', sortable: true, align: 'center',
            badge: (v) => (v === 'BULL' ? 'pos' : 'neg'),
        },
        {
            // The underlying where it was when the lines crossed, with the
            // live price under it. Sorting follows the headline number, so
            // the column orders by the price the cross actually happened at.
            label: 'Spot', sortable: true, align: 'right', sortKey: 'spot_cross',
            sortValue: (r) => {
                const s = num(r.cross_spot);
                return s === null ? -Infinity : s;
            },
            render: (_v, r) => {
                const cross = num(r.cross_spot);
                const live = spotOf(r);
                const liveValue = live ? live.value : null;
                if (cross === null) {
                    // No spot recorded on the cross's own scan — fall back to
                    // the live/scan price, dimmed, rather than an empty cell.
                    if (liveValue === null) return '<span class="oix-atm-wait">—</span>';
                    return `<span class="oix-spot-stale" title="No spot recorded at the ` +
                           `crossover — this is the current price">` +
                           `${DataGrid.escape(money(liveValue))}</span>`;
                }
                return `<span class="oix-spot" title="Spot at the ` +
                       `${DataGrid.escape(clock12(r.cross_time))} crossover">` +
                       `${DataGrid.escape(money(cross))}</span>` +
                       liveSub(r, liveValue, cross, '');
            },
        },
        {
            // Resolved from the live chain, so it is the strike a broker will
            // actually accept — not spot rounded to a step we guessed. Sorts
            // on the number even while other rows are still resolving.
            label: 'ATM Strike', sortable: true, align: 'right', sortKey: 'atm',
            sortValue: (r) => atmOf(r) ? atmOf(r).strike : -Infinity,
            render: (_v, r) => {
                const q = state.atm[r.symbol];
                // Historical rows have no live chain, so the cross's own
                // strike is all there is — and all that is meaningful.
                if (state.mode === 'historical' || q === undefined || q === null) {
                    const at = num(r.cross_strike);
                    if (at !== null) {
                        return `<span class="oix-atm" title="ATM strike at the ` +
                               `${DataGrid.escape(clock12(r.cross_time))} crossover">` +
                               `${DataGrid.escape(strikeText(at))}</span>` +
                               `<span class="oix-atm-side">${sideOf(r)}</span>`;
                    }
                    if (q === undefined) return '<span class="oix-atm-wait">…</span>';
                    return '<span class="oix-atm-wait" ' +
                           'title="No option chain for this symbol">—</span>';
                }
                // Live strike stays the headline: it is the contract the Buy
                // button sends. The cross's strike only appears when spot has
                // drifted far enough to move it, since that is exactly when
                // the premium beside it belongs to a different contract.
                const at = num(r.cross_strike);
                const drifted = at !== null && at !== Number(q.strike);
                return `<span class="oix-atm">${DataGrid.escape(strikeText(q.strike))}</span>` +
                       `<span class="oix-atm-side">${sideOf(r)}</span>` +
                       (drifted
                           ? `<span class="oix-live-sub" title="ATM strike when the ` +
                             `crossover fired — spot has moved since">was ` +
                             `${DataGrid.escape(strikeText(at))}</span>`
                           : '');
            },
        },
        {
            // The exchange lot for the symbol — one Buy sends lots × this many
            // contracts, so it is the difference between a ₹24 premium costing
            // ₹6,000 and costing ₹60,000.
            label: 'Lot Size', sortable: true, align: 'right', sortKey: 'lot_size',
            sortValue: (r) => {
                const q = atmOf(r);
                return q && q.lot_size ? q.lot_size : -Infinity;
            },
            render: (_v, r) => {
                const q = state.atm[r.symbol];
                if (q === undefined) return '<span class="oix-atm-wait">…</span>';
                if (!q || !q.lot_size) {
                    return '<span class="oix-atm-wait" title="Lot size not in the ' +
                           'instrument dump">—</span>';
                }
                return `<span class="oix-lot">${DataGrid.escape(String(q.lot_size))}</span>`;
            },
        },
        {
            // The premium of the contract the Buy button would actually send —
            // the CE on a BULL, the PE on a BEAR. Same chain call as the
            // strike, so it costs nothing extra, and it is what turns the row
            // into a decision: a 12% OI move behind a ₹4 option is not the
            // same trade as one behind a ₹180 option.
            label: 'ATM Price', sortable: true, align: 'right', sortKey: 'atm_ltp_cross',
            sortValue: (r) => {
                const at = num(r.cross_ltp);
                return at === null ? -Infinity : at;
            },
            render: (_v, r) => {
                const at = num(r.cross_ltp);
                const live = atmPrice(r);
                if (at === null) {
                    // Crosses recorded before the scanner started storing the
                    // premium. Showing the live price here would read as the
                    // cross price, so the cell says so instead of guessing.
                    if (state.mode === 'historical') {
                        return '<span class="oix-atm-wait" title="This crossover was ' +
                               'recorded before the crossover-time premium was stored">—</span>';
                    }
                    const q = state.atm[r.symbol];
                    if (q === undefined) return '<span class="oix-atm-wait">…</span>';
                    return '<span class="oix-atm-wait" title="No premium recorded at the ' +
                           'crossover — only the live price below is known">—</span>' +
                           liveSub(r, live, null, '₹');
                }
                return `<span class="oix-atm-ltp" title="Premium of the ` +
                       `${DataGrid.escape(strikeText(r.cross_strike))} ${sideOf(r)} at the ` +
                       `${DataGrid.escape(clock12(r.cross_time))} crossover">` +
                       `₹${DataGrid.escape(money(at))}</span>` +
                       liveSub(r, live, at, '₹');
            },
        },
        { key: 'ce_oi', label: 'Call OI', sortable: true, align: 'right', format: compact },
        { key: 'pe_oi', label: 'Put OI', sortable: true, align: 'right', format: compact },
        {
            key: 'pcr', label: 'PCR', sortable: true, align: 'right',
            format: (v) => (v == null ? '—' : Number(v).toFixed(2)),
        },
        {
            key: 'ce_chg', label: 'Call OI Chg', sortable: true, align: 'right',
            format: compact, tone: DataGrid.sign,
        },
        {
            key: 'pe_chg', label: 'Put OI Chg', sortable: true, align: 'right',
            format: compact, tone: DataGrid.sign,
        },
        {
            // How far apart the two change-lines were when they crossed, as a
            // share of the flow behind them. Unlike OI Chg % this is complete
            // the moment the cross fires, which is what makes it the strength
            // gate the screen filters on.
            key: 'separation', label: 'Separation', sortable: true, align: 'right',
            format: (v) => (v == null ? '—' : `${Number(v).toFixed(0)}%`),
            tone: (v) => (v == null ? 'muted' : v >= 30 ? 'pos' : v < 5 ? 'muted' : null),
            title: (v) => (v == null ? 'Recorded before the rating was stored'
                : v < 5 ? 'The lines were on top of each other — this is chop, not a signal'
                : ''),
        },
        {
            // Frozen at the cross. The live reading sits underneath, so a row
            // that has decayed says so instead of disappearing.
            key: 'quality', label: 'Quality', sortable: true, align: 'center',
            // 'weak' is the leftover bucket — the winning leg didn't add OI and
            // the losing leg didn't shed any, so the cross is drift, not flow.
            render: (v, r) => {
                if (!v) return '<span class="oix-atm-wait" title="Recorded before the ' +
                               'rating was stored with the cross">—</span>';
                const head = DataGrid.badge(
                    v, { strong: 'pos', aggressive: 'info', covering: 'warn' }[v] || 'neutral');
                if (state.mode === 'historical' || !r.live_quality
                        || r.live_quality === v) return head;
                return head + `<span class="oix-live-sub" title="What the legs are ` +
                       `doing on the latest scan">now ${DataGrid.escape(r.live_quality)}</span>`;
            },
        },
        {
            key: 'cross_time', label: 'Crossover Time', sortable: true, align: 'center',
            format: clock12,
        },
        {
            // Whether the cross is still standing. This column exists so decay
            // is something the row *reports* rather than something that removes
            // it from the table — a signal you have a position in must stay
            // visible precisely when it stops working.
            key: 'status', label: 'Status', sortable: true, align: 'center',
            title: (_v, r) => r.status_note || '',
            cellClass: (v) => `oix-status-${String(v || '').toLowerCase()}`,
            render: (v) => DataGrid.badge(
                v || '—', { LIVE: 'pos', PENDING: 'info', FADED: 'warn' }[v] || 'neutral'),
        },
        {
            // Which of the symbol's crosses this row is. A symbol crossing five
            // times is chop, and the later crosses inherit that doubt — but
            // each one keeps its own row and its own entry price now, so the
            // count no longer stands in for crosses you cannot see.
            key: 'cross_seq', label: 'Seq', sortable: true, align: 'right',
            title: (_v, r) => (r.cross_total >= 10
                ? `${r.cross_total} crosses today — choppy, treat the direction with care`
                : ''),
            render: (v, r) => `<span class="oix-seq${r.cross_total >= 10 ? ' oix-seq-choppy' : ''}">` +
                              `${DataGrid.escape(String(v))}` +
                              `<span class="oix-seq-of"> of ${DataGrid.escape(String(r.cross_total))}</span></span>`,
        },
        {
            // Only rows you have actually traded from this screen carry a
            // number; everything else is blank rather than a zero, because a
            // zero P&L and no position are not the same statement. Keyed by the
            // cross that was traded, so a symbol that crossed five times shows
            // the position against the one signal it was taken on.
            label: 'P&L', sortable: true, align: 'right', sortKey: 'pnl',
            sortValue: (r) => {
                const p = state.positions[r.id];
                return p && p.pnl !== null && p.pnl !== undefined ? p.pnl : -Infinity;
            },
            render: (_v, r) => {
                const p = state.positions[r.id];
                if (!p) return '<span class="oix-pnl-none"></span>';
                const held = `${p.qty} @ ₹${money(p.entry)} · ` +
                             `${strikeText(p.strike)} ${p.option_type}`;
                if (p.pnl === null || p.pnl === undefined) {
                    return `<span class="oix-atm-wait" title="Holding ${DataGrid.escape(held)} — ` +
                           `no live price for that strike">—</span>`;
                }
                const tone = p.pnl >= 0 ? 'pos' : 'neg';
                return `<span class="oix-pnl oix-pnl-${tone}" ` +
                       `title="${DataGrid.escape(held)}, now ₹${DataGrid.escape(money(p.ltp))}">` +
                       `${DataGrid.escape(DataGrid.inr(p.pnl))}</span>`;
            },
        },
        {
            label: 'Order', align: 'center',
            // Disabled until the ATM strike is known, because the strike is
            // the one field of the order that cannot be inferred from the row.
            // Historical mode never offers it: those crosses are days old and
            // the strike shown is today's.
            render: (_v, r) => {
                const q = atmOf(r);
                const ltp = atmPrice(r);
                if (state.mode === 'historical') {
                    return '<span class="oix-order-na" title="Live mode only">—</span>';
                }
                // Orders are limit-only, so a row without a premium has no
                // price to start from and cannot be ordered here.
                if (!q || ltp === null) {
                    return '<button class="oix-order-btn" disabled ' +
                           `title="${q ? 'No traded price for this contract'
                                       : 'Waiting for the ATM strike'}">Buy</button>`;
                }
                const side = sideOf(r);
                return `<button class="oix-order-btn oix-order-${side.toLowerCase()}" ` +
                       `data-order="${DataGrid.escape(String(r.id))}" ` +
                       `title="Buy ${DataGrid.escape(strikeText(q.strike))} ${side} ` +
                       `at limit ₹${DataGrid.escape(money(toTick(ltp)))}">` +
                       `Buy ${side}</button>`;
            },
        },
    ];

    // An empty grid has several quite different causes, and "no data" tells
    // the user nothing about which one they're looking at. A failed scan is
    // the important one: it leaves the table exactly as empty as a market
    // that hasn't opened, and it needs the user to go and fix something.
    function emptyMessage() {
        if (state.rows.length) return 'No crossovers match these filters';
        const run = state.lastRun;
        if (run && run.error) {
            return `Last scan failed at ${clock12(run.ts)} — ${run.error}`;
        }
        if (!state.scans) {
            return state.mode === 'historical'
                ? 'No scans recorded for this date'
                : 'No scan has run yet today — the scanner starts at 9:15 AM';
        }
        if (state.scans === 1) {
            return `${state.symbols} symbols scanned once. A crossover is two lines ` +
                   `swapping places, so it takes at least two scans to see one — ` +
                   `the next is within 3 minutes.`;
        }
        return `${state.symbols} symbols scanned ${state.scans}× — none have crossed yet today`;
    }

    // ── ATM quotes ───────────────────────────────────────────────────

    // One chain call per symbol on the server, so this asks only for the rows
    // the filters left on screen. The endpoint caps a request at 40 symbols;
    // a wider filter than that resolves the first 40 and picks up the rest on
    // the next render.
    const ATM_BATCH = 40;

    // The strike alone could be fetched once and kept, but the premium beside
    // it goes stale in seconds, so the whole quote is re-pulled on roughly the
    // grid's own cadence. Slightly under REFRESH_MS so a refresh doesn't
    // regularly just miss the window and leave the price a cycle behind.
    const ATM_STALE_MS = 45_000;

    function fetchAtm(rows) {
        if (state.mode === 'historical' || state.atmPending) return;

        // Re-ask for everything on screen once the quotes age out; in between,
        // only for symbols a widened filter has newly brought into view. This
        // is also what stops renderTable -> fetchAtm -> renderTable from
        // looping: a fresh batch leaves nothing stale and nothing unknown.
        const stale = Date.now() - state.atmAt > ATM_STALE_MS;
        const want = rows.map((r) => r.symbol)
            .filter((s) => stale || state.atm[s] === undefined)
            .slice(0, ATM_BATCH);
        if (!want.length) return;

        state.atmPending = true;
        fetch(`${API}/atm?symbols=${encodeURIComponent(want.join(','))}`)
            .then((res) => res.json())
            .then((data) => {
                if (!data.success) throw new Error(data.error || 'ATM lookup failed');
                Object.assign(state.atm, data.atm || {});
                // Anything the server didn't answer for is marked resolved-as-
                // unknown, so the next render doesn't ask for it all over again.
                for (const s of want) {
                    if (state.atm[s] === undefined) state.atm[s] = null;
                }
                state.atmAt = Date.now();
                state.atmPending = false;
                renderTable();
            })
            .catch((e) => {
                // Left as-is rather than blanked: the previous quote with its
                // age showing beats an empty column while the feed hiccups.
                state.atmPending = false;
                console.error('[OIX] atm', e);
            });
    }

    function renderTable() {
        const rows = visibleRows();
        fetchAtm(rows);
        // Coverage matters as much as the count here: a session recorded
        // before the full-universe scanner existed holds only the three index
        // chains, and three rows would otherwise look like a quiet day rather
        // than a narrow one.
        $('oixCount').textContent = rows.length
            ? `${rows.length} of ${state.rows.length} crossovers · ` +
              `${state.symbols} symbols scanned`
            : '';
        DataGrid.mountSortable('oixGrid', {
            rows,
            columns: COLUMNS,
            empty: emptyMessage(),
            // Earliest cross first: a cross that fired at 9:45 has had the
            // session to prove itself, and the late ones are usually unwinds.
            defaultSort: { key: 'cross_time', dir: 'asc' },
            rowClass: (r) => `oix-row oix-${r.direction.toLowerCase()}` +
                             (r.id === state.openId ? ' oix-row-open' : ''),
            rowAttrs: (r) => `data-symbol="${DataGrid.escape(r.symbol)}" ` +
                             `data-event-id="${DataGrid.escape(String(r.id))}"`,
            // Prev/next in the chart panel step through this exact order.
            onSorted: (sorted) => { state.displayRows = sorted; },
        });
        if (state.openId !== null) syncNav();
    }

    // ── drilldown chart ──────────────────────────────────────────────

    // A plain inline SVG rather than a charting library: a handful of
    // polylines, a grid and a crosshair is the whole requirement.
    //
    // Two independent y-axes. Price sits on the left in rupees and the OI
    // series on the right in contracts — quantities with no common unit or
    // magnitude, so a shared axis would flatten one of them into a straight
    // line. The point of showing them together is the *shape* agreement: a
    // put-led crossover with price rising is the setup, and the same
    // crossover with price falling is noise.
    //
    // Sized in real pixels from the measured container rather than scaled
    // from a fixed viewBox. A viewBox stretched to a wide panel scales the
    // axis labels horizontally with it, and the distortion is obvious once
    // there are thirty of them.
    const PAD = { L: 60, R: 62, T: 14, B: 34 };
    const CHART_H = 340;

    // The three views StockMojo offers over the same stored series. Every one
    // of them is already in oi_crossover_series, so switching tabs is a
    // re-render rather than another request.
    const CHART_MODES = {
        pcr: {
            label: 'PCR',
            fmt: (v) => Number(v).toFixed(2),
            series: [{ key: 'pcr', label: 'PCR', cls: 'pcr' }],
        },
        oi_change: {
            label: 'OI Change',
            fmt: compact,
            series: [
                { key: 'ce_chg', label: 'Call OI Change', cls: 'ce' },
                { key: 'pe_chg', label: 'Put OI Change', cls: 'pe' },
            ],
        },
        total_oi: {
            label: 'Total OI',
            fmt: compact,
            series: [
                { key: 'ce_oi', label: 'Call OI', cls: 'ce' },
                { key: 'pe_oi', label: 'Put OI', cls: 'pe' },
            ],
        },
    };

    const PRICE_SERIES = { key: 'price', cls: 'price' };

    // Round tick steps (1/2/2.5/5/10 x powers of ten) so the axis reads
    // 1,434 / 1,436 / 1,438 rather than whatever the data extremes happen to
    // divide into.
    function niceTicks(lo, hi, target) {
        const span = hi - lo;
        if (!(span > 0)) return [lo];
        const raw = span / target;
        const mag = Math.pow(10, Math.floor(Math.log10(raw)));
        const norm = raw / mag;
        const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5
                    : norm <= 5 ? 5 : 10) * mag;
        const out = [];
        for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
            out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
        }
        return out;
    }

    // Pad a min/max pair so a line never runs along the frame, and never
    // collapse to zero height when a series is flat all session.
    function padRange(lo, hi, frac) {
        if (hi === lo) { const bump = Math.abs(hi || 1) * 0.01 || 1; return [lo - bump, hi + bump]; }
        const pad = (hi - lo) * frac;
        return [lo - pad, hi + pad];
    }

    const tsToMs = (ts) => new Date(ts).getTime();

    function buildGeometry(data, width) {
        const mode = CHART_MODES[state.chartMode];
        const keys = mode.series.map((s) => s.key);
        const pts = (data.points || []).filter(
            (p) => keys.every((k) => p[k] !== null && p[k] !== undefined));
        if (pts.length < 2) return null;

        const plotW = width - PAD.L - PAD.R;
        const plotH = CHART_H - PAD.T - PAD.B;
        if (plotW < 80) return null;

        const t0 = tsToMs(pts[0].ts);
        const t1 = tsToMs(pts[pts.length - 1].ts);
        const span = Math.max(1, t1 - t0);

        const vals = pts.flatMap((p) => keys.map((k) => p[k]));
        // PCR has no meaningful zero to anchor to; the OI views do, and
        // including it keeps the zero line on screen where crossings read
        // against it.
        const anchor = state.chartMode === 'pcr' ? [] : [0];
        const [vLo, vHi] = padRange(Math.min(...vals, ...anchor),
                                    Math.max(...vals, ...anchor), 0.08);

        const withPrice = pts.filter((p) => p.price != null);
        const [pLo, pHi] = withPrice.length
            ? padRange(Math.min(...withPrice.map((p) => p.price)),
                       Math.max(...withPrice.map((p) => p.price)), 0.12)
            : [0, 1];

        const xMs = (ms) => PAD.L + ((ms - t0) / span) * plotW;
        const x   = (ts) => xMs(tsToMs(ts));
        const yV = (v) => PAD.T + (1 - (v - vLo) / (vHi - vLo)) * plotH;
        const yP = (v) => PAD.T + (1 - (v - pLo) / (pHi - pLo)) * plotH;

        return { pts, mode, width, plotW, plotH, t0, t1, span, x, xMs, yV, yP,
                 vLo, vHi, pLo, pHi, hasPrice: withPrice.length > 0 };
    }

    // Vertical gridlines land on clean half-hour boundaries, so the same
    // session always rules at the same places whatever the scan cadence was.
    function timeTicks(geo) {
        const STEP = 30 * 60 * 1000;
        const out = [];
        for (let t = Math.ceil(geo.t0 / STEP) * STEP; t <= geo.t1; t += STEP) {
            out.push(t);
        }
        return out;
    }

    function renderChart(data, geo) {
        const { pts, mode, width, x, xMs, yV, yP } = geo;
        const hidden = state.hiddenSeries;
        const right = width - PAD.R;
        const bottom = CHART_H - PAD.B;

        const vTicks = niceTicks(geo.vLo, geo.vHi, 8);
        const pTicks = niceTicks(geo.pLo, geo.pHi, 10);
        const tTicks = timeTicks(geo);

        const grid =
            vTicks.map((v) => `<line class="oix-grid" x1="${PAD.L}" y1="${yV(v).toFixed(1)}" ` +
                              `x2="${right}" y2="${yV(v).toFixed(1)}"></line>`).join('') +
            tTicks.map((t) => {
                const px = xMs(t).toFixed(1);
                return `<line class="oix-grid" x1="${px}" y1="${PAD.T}" x2="${px}" y2="${bottom}"></line>`;
            }).join('');

        const axes =
            pTicks.map((v) => `<text class="oix-axis" x="${PAD.L - 8}" y="${(yP(v) + 3).toFixed(1)}" ` +
                              `text-anchor="end">${money(v)}</text>`).join('') +
            vTicks.map((v) => `<text class="oix-axis" x="${right + 8}" y="${(yV(v) + 3).toFixed(1)}" ` +
                              `text-anchor="start">${mode.fmt(v)}</text>`).join('') +
            tTicks.map((t) => {
                const px = xMs(t);
                if (px < PAD.L + 12 || px > right - 12) return '';
                return `<text class="oix-axis oix-axis-t" x="${px.toFixed(1)}" ` +
                       `y="${CHART_H - 12}" text-anchor="middle">` +
                       `${clock12Date(new Date(t))}</text>`;
            }).join('');

        // Crossover markers sit under the lines so they never obscure one.
        const marks = (data.events || []).map((e) => {
            const px = x(e.ts);
            if (!(px >= PAD.L && px <= right)) return '';
            const cls = e.direction === 'BULL' ? 'oix-mark-bull' : 'oix-mark-bear';
            return `<line class="oix-mark ${cls}" x1="${px.toFixed(1)}" y1="${PAD.T}" ` +
                   `x2="${px.toFixed(1)}" y2="${bottom}"></line>`;
        }).join('');

        const drawn = [];
        if (geo.hasPrice && !hidden.has('price')) {
            drawn.push({ ...PRICE_SERIES, y: yP, fmt: money });
        }
        for (const s of mode.series) {
            if (!hidden.has(s.key)) drawn.push({ ...s, y: yV, fmt: mode.fmt });
        }

        const lines = drawn.map((s) => {
            const d = pts.filter((p) => p[s.key] != null)
                .map((p) => `${x(p.ts).toFixed(1)},${s.y(p[s.key]).toFixed(1)}`).join(' ');
            return `<polyline class="oix-line oix-line-${s.cls}" points="${d}"></polyline>`;
        }).join('');

        // Last value: a dot on the line, a horizontal rule to its axis, and a
        // tag with the number — the "where does this sit right now" read.
        const last = pts[pts.length - 1];
        const tags = drawn.map((s) => {
            const yv = s.y(last[s.key]);
            const onLeft = s.key === 'price';
            const ax = onLeft ? PAD.L : right;
            const tagX = onLeft ? PAD.L - 56 : right + 2;
            return `<g class="oix-tag oix-tag-${s.cls}">` +
                   `<line class="oix-tag-rule" x1="${PAD.L}" y1="${yv.toFixed(1)}" ` +
                   `x2="${right}" y2="${yv.toFixed(1)}"></line>` +
                   `<circle cx="${x(last.ts).toFixed(1)}" cy="${yv.toFixed(1)}" r="3.5"></circle>` +
                   `<rect x="${tagX}" y="${(yv - 8).toFixed(1)}" width="54" height="16" rx="3"></rect>` +
                   `<text x="${(tagX + 27).toFixed(1)}" y="${(yv + 4).toFixed(1)}" ` +
                   `text-anchor="middle">${DataGrid.escape(s.fmt(last[s.key]))}</text></g>`;
        }).join('');

        const dots = drawn.map((s) =>
            `<circle class="oix-cross-dot oix-dot-${s.cls}" data-key="${s.key}" r="4" hidden></circle>`
        ).join('');

        return `<svg class="oix-svg" width="${width}" height="${CHART_H}"
                     viewBox="0 0 ${width} ${CHART_H}" role="img"
                     aria-label="${DataGrid.escape(mode.label)} with future price">
            ${grid}
            <rect class="oix-frame" x="${PAD.L}" y="${PAD.T}" width="${geo.plotW}"
                  height="${geo.plotH}"></rect>
            ${marks}${axes}${lines}${tags}
            <g class="oix-cross" hidden>
              <line class="oix-cross-line" y1="${PAD.T}" y2="${bottom}"></line>
              ${dots}
            </g>
            <rect class="oix-hit" x="${PAD.L}" y="${PAD.T}" width="${geo.plotW}"
                  height="${geo.plotH}"></rect>
        </svg>
        <div class="oix-tip" hidden></div>`;
    }

    function renderLegend(data) {
        const mode = CHART_MODES[state.chartMode];
        const items = [];
        if (data.points && data.points.some((p) => p.price != null)) {
            items.push({ key: 'price', cls: 'price', label: data.price_source || 'Price' });
        }
        items.push(...mode.series);
        return items.map((s) => {
            const off = state.hiddenSeries.has(s.key);
            return `<button class="oix-leg oix-leg-${s.cls}${off ? ' oix-leg-off' : ''}" ` +
                   `data-series="${s.key}" title="Show or hide this line">` +
                   `<span class="oix-eye">${off ? '🚫' : '👁'}</span>` +
                   `<i class="oix-leg-key"></i>${DataGrid.escape(s.label)}</button>`;
        }).join('');
    }

    // Crosshair. Bound to the body rather than the SVG's children because the
    // SVG is replaced on every tab switch, legend toggle and resize.
    function bindCrosshair(body, geo) {
        const svg = body.querySelector('.oix-svg');
        const cross = body.querySelector('.oix-cross');
        const line = body.querySelector('.oix-cross-line');
        const tip = body.querySelector('.oix-tip');
        if (!svg || !cross) return;

        const { pts, x } = geo;

        const nearest = (px) => {
            let best = 0, bestD = Infinity;
            for (let i = 0; i < pts.length; i++) {
                const d = Math.abs(x(pts[i].ts) - px);
                if (d < bestD) { bestD = d; best = i; }
            }
            return best;
        };

        svg.addEventListener('mousemove', (ev) => {
            const rect = svg.getBoundingClientRect();
            const px = ev.clientX - rect.left;
            const p = pts[nearest(px)];
            const cx = x(p.ts);

            cross.removeAttribute('hidden');
            line.setAttribute('x1', cx.toFixed(1));
            line.setAttribute('x2', cx.toFixed(1));

            cross.querySelectorAll('.oix-cross-dot').forEach((dot) => {
                const key = dot.dataset.key;
                const val = p[key];
                if (val == null) { dot.setAttribute('hidden', ''); return; }
                dot.removeAttribute('hidden');
                dot.setAttribute('cx', cx.toFixed(1));
                dot.setAttribute('cy', (key === 'price' ? geo.yP(val) : geo.yV(val)).toFixed(1));
            });

            const d = new Date(p.ts);
            // Built from parts rather than toLocaleDateString: the locale form
            // comma-separates every field ("Wed, 5 Aug, 26").
            tip.textContent =
                `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]} ` +
                `${String(d.getFullYear()).slice(2)}  ${clock12(p.ts)}`;
            tip.removeAttribute('hidden');
            // Clamped so the tag never runs off either end of the panel.
            const half = tip.offsetWidth / 2;
            tip.style.left = Math.min(Math.max(cx, half), geo.width - half) + 'px';
        });

        svg.addEventListener('mouseleave', () => {
            cross.setAttribute('hidden', '');
            tip.setAttribute('hidden', '');
        });
    }

    function drawSeries(body, data) {
        const width = Math.max(320, Math.floor(body.clientWidth));
        const geo = buildGeometry(data, width);

        body.innerHTML =
            `<div class="oix-legend">${renderLegend(data)}</div>` +
            (geo ? renderChart(data, geo)
                 : '<div class="oix-drill-empty">Not enough scan points yet to draw ' +
                   'this view.</div>') +
            `<div class="oix-drill-note">${(data.events || []).length} cross(es) marked · ` +
            `${(data.points || []).length} scan points · ` +
            (data.price_source === 'Future'
                ? 'price line is the nearest-expiry future'
                : 'price line is spot — this session predates futures capture') +
            `</div>`;

        if (geo) bindCrosshair(body, geo);

        body.querySelectorAll('.oix-leg').forEach((btn) => {
            btn.addEventListener('click', () => {
                const key = btn.dataset.series;
                if (state.hiddenSeries.has(key)) state.hiddenSeries.delete(key);
                else state.hiddenSeries.add(key);
                drawSeries(body, data);
            });
        });
    }

    // Redraw on resize: the SVG is laid out in real pixels, so unlike a
    // viewBox it does not rescale itself when the panel width changes.
    function watchResize(body) {
        if (body.dataset.oixResize) return;
        body.dataset.oixResize = '1';
        let raf = null;
        new ResizeObserver(() => {
            if (raf) cancelAnimationFrame(raf);
            raf = requestAnimationFrame(() => {
                if (state.chartData && !$('oixDrill').hidden) drawSeries(body, state.chartData);
            });
        }).observe(body);
    }
    async function openDrill(symbol, eventId) {
        state.openSymbol = symbol;
        state.openId = eventId === undefined ? null : eventId;
        renderTable();
        const drill = $('oixDrill');
        const body = $('oixDrillBody');
        drill.hidden = false;
        document.body.classList.add('oix-drill-open');
        $('oixDrillSym').innerHTML =
            `<i class="oix-avatar">${DataGrid.escape(symbol.slice(0, 1))}</i>` +
            DataGrid.escape(symbol);
        syncNav();
        body.innerHTML = '<div class="oix-drill-empty">Loading…</div>';

        const qs = new URLSearchParams({ symbol });
        if (state.mode === 'historical' && state.date) qs.set('date', state.date);

        try {
            const res = await fetch(`${API}/series?${qs}`);
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Failed to load series');
            state.chartData = data;
            watchResize(body);
            drawSeries(body, data);
        } catch (e) {
            state.chartData = null;
            body.innerHTML =
                `<div class="oix-drill-empty">Could not load ${DataGrid.escape(symbol)}: ` +
                `${DataGrid.escape(e.message)}</div>`;
        }
    }

    // Prev/next walk the table in the order it is currently displayed, so
    // they follow whatever sort and filters the user has applied rather than
    // some hidden canonical order.
    function stepSymbol(delta) {
        const rows = state.displayRows.length ? state.displayRows : visibleRows();
        const i = rows.findIndex((r) => r.id === state.openId);
        if (i < 0) return;
        const next = rows[i + delta];
        if (next) openDrill(next.symbol, next.id);
    }

    function syncNav() {
        const rows = state.displayRows.length ? state.displayRows : visibleRows();
        const i = rows.findIndex((r) => r.id === state.openId);
        $('oixPrev').disabled = i <= 0;
        $('oixNext').disabled = i < 0 || i >= rows.length - 1;
    }

    function closeDrill() {
        state.openSymbol = null;
        state.openId = null;
        state.chartData = null;
        $('oixDrill').hidden = true;
        document.body.classList.remove('oix-drill-open');
        renderTable();
    }

    // ── order ticket ─────────────────────────────────────────────────

    // NSE options move in 5-paise steps and the broker rejects a limit price
    // that isn't on one, so every price this screen sends is snapped to the
    // tick — including one typed by hand.
    const TICK = 0.05;
    const toTick = (v) => Math.round(Number(v) / TICK) * TICK;

    // A cross is a signal about direction, so the order follows the direction:
    // BULL buys the ATM call, BEAR buys the ATM put. Buying premium rather
    // than writing the other side keeps the risk to the debit paid, which is
    // the right default for a scanner signal fired by hand.
    //
    // Always LIMIT: a market order on a stock option walks whatever spread is
    // sitting there, and on the thin strikes this scanner surfaces that spread
    // is the trade. The price starts at the ATM premium shown on the row and
    // is editable in the ticket.
    const orderFor = (row, limitPrice) => ({
        symbol: row.symbol,
        strike: atmOf(row).strike,
        option_type: sideOf(row),
        action: 'BUY',
        order_type: 'LIMIT',
        limit_price: toTick(limitPrice),
        strategy: 'oix',
        // Which cross this was taken on, so the P&L lands on that row and not
        // on every other cross the symbol happens to have made today.
        signal_id: row.id,
    });

    function openTicket(eventId) {
        // By cross, not by symbol: a symbol owns several rows and the ticket
        // must belong to the one whose Buy button was pressed, so the order
        // can be stamped with the signal it was taken on.
        const row = state.rows.find((r) => String(r.id) === String(eventId));
        if (!row) return;
        const symbol = row.symbol;
        const quote = atmOf(row);
        if (!quote) return;
        if (state.mode === 'historical') return;

        // A limit order needs a price, and the only one this screen has is the
        // ATM premium — so a contract with no traded price can't be ordered
        // from here at all. The row's button is disabled for the same reason.
        const ltp = atmPrice(row);
        if (ltp === null) return;

        // A successful order closes the ticket on a short delay so the "Order
        // placed" line is readable. Reopening inside that window must cancel
        // the pending close, or it would shut the ticket the user just opened
        // for a different row.
        clearTimeout(state.ticketCloseTimer);
        state.ticketCloseTimer = null;
        state.ticketRow = row;

        $('oixTicketBody').innerHTML =
            `<div class="oix-tk-line oix-tk-headline">` +
              `<b>BUY</b> ${DataGrid.escape(symbol)} ` +
              `${DataGrid.escape(strikeText(quote.strike))} ` +
              `<b>${sideOf(row)}</b> — limit` +
            `</div>` +
            `<div class="oix-tk-price">` +
              `<label for="oixTicketPrice">Limit price</label>` +
              `<div class="oix-tk-price-box">` +
                `<span>₹</span>` +
                `<input type="number" id="oixTicketPrice" step="${TICK}" min="${TICK}" ` +
                       `value="${DataGrid.escape(money(toTick(ltp)))}" ` +
                       `inputmode="decimal" autocomplete="off">` +
              `</div>` +
              // Says where the number came from, so a stale prefill is
              // recognisable as one rather than read as a live quote.
              `<span class="oix-tk-price-hint">last traded ₹${
                    DataGrid.escape(money(ltp))}</span>` +
            `</div>` +
            `<div class="oix-tk-grid">` +
              `<span>Crossover</span><span class="oix-tk-${row.direction.toLowerCase()}">` +
                `${DataGrid.escape(row.direction)}</span>` +
              `<span>Quality</span><span>${DataGrid.escape(row.quality)}</span>` +
              `<span>Cross time</span><span>${DataGrid.escape(clock12(row.cross_time))}</span>` +
              `<span>Cross</span><span>${DataGrid.escape(String(row.cross_seq))} of ` +
                `${DataGrid.escape(String(row.cross_total))}</span>` +
              `<span>Separation</span><span>${row.separation === null ? '—'
                    : DataGrid.escape(Number(row.separation).toFixed(0)) + '%'}</span>` +
              `<span>Status</span><span>${DataGrid.escape(row.status || '—')}</span>` +
            `</div>` +
            // Size and destination are server-side config, not a field here —
            // saying so is better than showing a quantity box that the .env
            // would override anyway.
            `<div class="oix-tk-note">Limit order to every broker with ` +
              `<code>BROKER_N_OIX_ACTIVE=true</code>, sized by ` +
              `<code>BROKER_N_OIX_LOTS</code>. No stop-loss is attached.</div>`;

        $('oixTicketMsg').textContent = '';
        $('oixTicketMsg').className = 'oix-ticket-msg';
        $('oixTicketSend').disabled = false;
        $('oixTicketSend').textContent = 'Place order';
        $('oixTicketBack').hidden = false;
        $('oixTicketPrice').focus();
        $('oixTicketPrice').select();
    }

    function closeTicket() {
        clearTimeout(state.ticketCloseTimer);
        state.ticketCloseTimer = null;
        state.ticketRow = null;
        $('oixTicketBack').hidden = true;
    }

    async function sendTicket() {
        const row = state.ticketRow;
        if (!row) return;
        const send = $('oixTicketSend');
        const msg = $('oixTicketMsg');

        // Validated here rather than trusted from the input: number inputs
        // hand back an empty string for "12x", and a zero or negative limit is
        // a rejection at the broker at best.
        const typed = Number($('oixTicketPrice').value);
        if (!Number.isFinite(typed) || typed <= 0) {
            msg.className = 'oix-ticket-msg oix-tk-err';
            msg.textContent = 'Enter a limit price above zero';
            return;
        }
        const order = orderFor(row, typed);
        // Show what actually goes out when the tick snap moved it.
        $('oixTicketPrice').value = money(order.limit_price);
        // Guarded rather than merely styled: a double-click on a market order
        // is two positions, and the button stays dead until the reply lands.
        send.disabled = true;
        send.textContent = 'Placing…';
        msg.className = 'oix-ticket-msg';
        msg.textContent = '';

        try {
            const res = await fetch('/api/orders/place', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    // /api/orders/place is not CSRF-exempt — without this the
                    // order is rejected before it reaches the dispatcher.
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content,
                },
                body: JSON.stringify(order),
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Order rejected');
            msg.className = 'oix-ticket-msg oix-tk-ok';
            msg.textContent = 'Order placed';
            send.textContent = 'Placed';
            state.ticketCloseTimer = setTimeout(closeTicket, 1200);
        } catch (e) {
            // Left open on failure: the reason matters more than the dialog,
            // and re-opening it means finding the row again.
            msg.className = 'oix-ticket-msg oix-tk-err';
            msg.textContent = e.message;
            send.disabled = false;
            send.textContent = 'Retry';
        }
    }

    // ── data ─────────────────────────────────────────────────────────

    async function loadSnapshot() {
        const qs = new URLSearchParams();
        if (state.mode === 'historical' && state.date) qs.set('date', state.date);
        try {
            const res = await fetch(`${API}/snapshot?${qs}`);
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Snapshot failed');
            state.rows = data.rows || [];
            state.scans = data.scans || 0;
            state.symbols = data.symbols || 0;
            state.lastRun = data.last_run || null;
            $('oixAsOf').textContent = data.as_of
                ? `🕐 ${new Date(data.as_of).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}, ${clock12(data.as_of)}`
                : `🕐 ${data.trade_date} — no scans`;
            if (state.lastRun && state.lastRun.error) setDot('err');
            else setDot(state.rows.length ? 'ok' : 'idle');
        } catch (e) {
            state.rows = [];
            state.scans = 0;
            state.symbols = 0;
            state.lastRun = null;
            $('oixAsOf').textContent = 'Failed to load';
            setDot('err');
            console.error('[OIX]', e);
        }
        // Alongside the rows, so a refresh re-values open positions with the
        // same click that re-prices the grid.
        await loadPositions();
        renderTable();
        // A refresh that drops the open symbol (filters changed, new session)
        // should close the panel rather than leave a stale chart on screen.
        if (state.openId !== null && !state.rows.some((r) => r.id === state.openId)) closeDrill();
    }

    // The ↻ button. Distinct from the 60s poll in one way that matters: it
    // also drops the quote cache, so prices are re-pulled even when the last
    // batch is still inside its staleness window — a refresh that reloaded
    // the rows but left the premiums untouched would be misleading on the one
    // screen where the premium is what gets traded.
    async function manualRefresh() {
        const btn = $('oixRefresh');
        if (btn.disabled) return;
        btn.disabled = true;
        btn.classList.add('oix-refreshing');
        state.atmAt = 0;
        try {
            await loadSnapshot();
        } finally {
            btn.disabled = false;
            btn.classList.remove('oix-refreshing');
        }
    }

    // Positions are only worth pricing in Live mode, and only cost anything
    // when one exists — the endpoint hits the chain once per held symbol and
    // not at all when nothing has been traded.
    async function loadPositions() {
        if (state.mode === 'historical') { state.positions = {}; return; }
        try {
            const res = await fetch(`${API}/positions`);
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Positions failed');
            state.positions = data.positions || {};
        } catch (e) {
            // Kept as they were: a dropped poll shouldn't blank a P&L that was
            // on screen a second ago.
            console.error('[OIX] positions', e);
        }
    }

    function setDot(status) {
        const dot = $('oixDot');
        dot.className = `oix-dot oix-dot-${status}`;
        dot.title = { ok: 'Scanner reporting', idle: 'No crossovers yet', err: 'Scan unavailable' }[status];
    }

    async function loadMeta() {
        try {
            const res = await fetch(`${API}/meta`);
            const data = await res.json();
            if (!data.success) return;

            // Only the historical date list is read off /meta now — the sector
            // filter is gone, so data.sectors is ignored.
            const dateSel = $('oixDate');
            dateSel.innerHTML = (data.dates || [])
                .map((d) => `<option value="${DataGrid.escape(d)}">${DataGrid.escape(d)}</option>`)
                .join('') || '<option value="">No recorded sessions</option>';
        } catch (e) {
            console.error('[OIX] meta', e);
        }
    }

    // ── logic sheet ──────────────────────────────────────────────────

    // Built on demand and thrown away on close, in the sm-modal/rtp-logic
    // markup every other Logic button on the Algo page uses — the styling is
    // already loaded here, and a shared shell means the panels can't drift
    // apart. Deliberately short: the filter bar is the thing being explained,
    // not the strategy behind it.
    const LOGIC_HTML = `
<div class="sm-modal-box rtp-logic-modal">
    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">OI Crossover</span>
            <span class="sm-modal-subtitle">How it reads &amp; what each filter does</span>
        </div>
        <button class="sm-modal-close" data-logic-close aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <div class="rtp-tf"><span class="rtp-tf-lbl">Signal</span><span class="rtp-tf-val">Call-OI vs Put-OI change</span><span class="rtp-tf-sub">from 9:15</span></div>

        <p class="rtp-idea">Two lines per symbol, both measured from the open. A <b>crossover</b> is the moment they swap: puts ahead is <b class="oix-bull">BULL</b>, calls ahead is <b class="oix-bear">BEAR</b>. Not a PCR read — levels say where the book already was, these lines say where today's money went.</p>

        <p class="rtp-idea">One row per <b>cross</b>, rated at the instant it fired and never re-rated. Every filter below reads that frozen rating, so a row that appears stays for the session — it can decay, but it cannot vanish. The <b>Status</b> column is where decay shows up.</p>

        <div class="rtp-blk-lbl entry">Filters &middot; all AND-ed &middot; instant, no refetch</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card">
                <div class="rtp-duo-hd">Quality <i>— at the cross</i></div>
                <div class="rtp-duo-row"><b>Strong</b> both legs agree &middot; default</div>
                <div class="rtp-duo-row"><b>Aggressive</b> winner OI up only</div>
                <div class="rtp-duo-row"><b>Covering</b> loser OI out only</div>
                <div class="rtp-duo-row"><b>All</b> adds the weak crosses</div>
            </div>
            <div class="rtp-duo-card">
                <div class="rtp-duo-hd">Separation <i>— a floor</i></div>
                <div class="rtp-duo-row">Gap between the legs &divide; flow behind them</div>
                <div class="rtp-duo-row"><b>Strong is always 100%</b> — opposed legs force it, so this does nothing until Quality is widened</div>
                <div class="rtp-duo-row">Under 5% the lines sat on top of each other — chop, not signal</div>
            </div>
            <div class="rtp-duo-card">
                <div class="rtp-duo-hd">Status <i>— is it still standing</i></div>
                <div class="rtp-duo-row"><b>Live</b> latest scan still agrees</div>
                <div class="rtp-duo-row"><b>Pending</b> awaiting the next scan</div>
                <div class="rtp-duo-row"><b>Faded</b> same side, weaker flow</div>
                <div class="rtp-duo-row"><b>Flipped</b> the lines crossed back</div>
            </div>
            <div class="rtp-duo-card">
                <div class="rtp-duo-hd">Seq <i>— a ceiling</i></div>
                <div class="rtp-duo-row"><b>Any</b> no cap &middot; default</div>
                <div class="rtp-duo-row"><b>First only</b> the day's opening turn</div>
                <div class="rtp-duo-row"><b>Max 2 / 3</b> keeps 1–2 / 1–3</div>
                <div class="rtp-duo-row">Double digits = chop</div>
            </div>
            <div class="rtp-duo-card">
                <div class="rtp-duo-hd">OI Chg <i>— off by default</i></div>
                <div class="rtp-duo-row">Bigger leg's move vs its own open</div>
                <div class="rtp-duo-row">Grows all session no matter what the symbol does, so a floor here acts as a clock — it hides the morning's crosses until the afternoon</div>
            </div>
            <div class="rtp-duo-card">
                <div class="rtp-duo-hd">Mode &amp; Date</div>
                <div class="rtp-duo-row"><b>Live</b> re-reads every 60s</div>
                <div class="rtp-duo-row"><b>Historical</b> frozen past session</div>
                <div class="rtp-duo-row">Dates from <b>01-08-2026</b> on</div>
            </div>
        </div>
        <div class="rtp-mode-note">Search is a plain symbol substring. The count at the right of the bar is what survived. <b>Strong</b> + <b>&gt;10%</b> returning nothing on a quiet morning is the session, not a fault. <b>CSV</b> ignores all of it and exports the whole day.</div>

        <div class="rtp-blk-lbl exit">Order</div>
        <div class="rtp-mode-note" style="margin-top:0">Buys the ATM option in the cross's direction — CE on BULL, PE on BEAR. Always a <b>limit</b> at the live ATM premium, editable, snapped to the 5-paise tick. No stop-loss is attached.</div>

    </div>
</div>`;

    function openLogic() {
        closeLogic();
        const back = document.createElement('div');
        back.id = 'oixLogicModal';
        back.className = 'sm-modal-overlay';
        back.innerHTML = LOGIC_HTML;
        back.addEventListener('click', (e) => {
            // Backdrop or the ✕ — a click on the sheet itself must not close it.
            if (e.target === back || e.target.closest('[data-logic-close]')) closeLogic();
        });
        document.body.appendChild(back);
    }

    function closeLogic() {
        document.getElementById('oixLogicModal')?.remove();
    }

    const logicOpen = () => !!document.getElementById('oixLogicModal');

    // ── wiring ───────────────────────────────────────────────────────

    function setMode(mode) {
        state.mode = mode;
        document.querySelectorAll('.oix-mode-btn').forEach((b) => {
            b.classList.toggle('active', b.dataset.mode === mode);
        });
        $('oixDateWrap').hidden = mode !== 'historical';

        if (mode === 'historical') {
            clearInterval(state.timer);
            state.timer = null;
            state.date = $('oixDate').value || null;
        } else {
            state.date = null;
            if (!state.timer) state.timer = setInterval(loadSnapshot, REFRESH_MS);
        }
        closeDrill();
        loadSnapshot();
    }

    function init() {
        const f = state.filters;

        // Portal the docked panel and the order ticket to <body>. Left inside
        // .sw-wrap their position:fixed resolved against an ancestor box
        // rather than the viewport, docking the panel partway up the page
        // instead of at the bottom. A body-level overlay also can't be
        // affected by whatever containing block a future page-chrome change
        // introduces — and on the Algo page the panel they sat in is itself
        // toggled with `display`, which would have hidden them outright.
        document.body.appendChild($('oixDrill'));
        document.body.appendChild($('oixTicketBack'));

        f.quality    = $('oixQuality').value;
        f.crossCount = Number($('oixCrossCount').value);
        f.oiChg      = Number($('oixOiChg').value);
        f.separation = Number($('oixSeparation').value);
        f.status     = $('oixStatus').value;

        $('oixSearch').addEventListener('input', (e) => {
            f.search = e.target.value;
            renderTable();
        });
        $('oixQuality').addEventListener('change', (e) => { f.quality = e.target.value; renderTable(); });
        $('oixCrossCount').addEventListener('change', (e) => { f.crossCount = Number(e.target.value); renderTable(); });
        $('oixOiChg').addEventListener('change', (e) => { f.oiChg = Number(e.target.value); renderTable(); });
        $('oixSeparation').addEventListener('change', (e) => { f.separation = Number(e.target.value); renderTable(); });
        $('oixStatus').addEventListener('change', (e) => { f.status = e.target.value; renderTable(); });

        document.querySelectorAll('.oix-mode-btn').forEach((b) => {
            b.addEventListener('click', () => setMode(b.dataset.mode));
        });
        $('oixDate').addEventListener('change', (e) => {
            state.date = e.target.value || null;
            closeDrill();
            loadSnapshot();
        });

        $('oixRefresh').addEventListener('click', manualRefresh);

        $('oixExport').addEventListener('click', () => {
            const qs = new URLSearchParams();
            if (state.mode === 'historical' && state.date) qs.set('date', state.date);
            window.location = `${API}/export?${qs}`;
        });

        $('oixDrillClose').addEventListener('click', closeDrill);
        $('oixPrev').addEventListener('click', () => stepSymbol(-1));
        $('oixNext').addEventListener('click', () => stepSymbol(1));

        $('oixTabs').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-mode]');
            if (!btn || btn.dataset.mode === state.chartMode) return;
            state.chartMode = btn.dataset.mode;
            // Visibility is per-series and the modes share no series, so a
            // line hidden in one view must not come back hidden in another.
            state.hiddenSeries.clear();
            $('oixTabs').querySelectorAll('[data-mode]').forEach((b) =>
                b.classList.toggle('active', b.dataset.mode === state.chartMode));
            if (state.chartData) drawSeries($('oixDrillBody'), state.chartData);
        });

        document.addEventListener('keydown', (e) => {
            // Topmost overlay first, so Escape always backs out of the thing
            // being looked at rather than something underneath it.
            if (logicOpen()) {
                if (e.key === 'Escape') closeLogic();
                return;
            }
            // The ticket is modal, so it takes the key before the chart panel
            // does — Escape backs out of the order, not out of the drilldown
            // sitting behind it.
            if (!$('oixTicketBack').hidden) {
                if (e.key === 'Escape') closeTicket();
                return;
            }
            if ($('oixDrill').hidden) return;
            if (e.key === 'Escape') closeDrill();
            else if (e.key === 'ArrowLeft') stepSymbol(-1);
            else if (e.key === 'ArrowRight') stepSymbol(1);
        });

        // Delegated: the grid re-renders on every sort, filter and refresh,
        // so per-row handlers would need rebinding each time.
        $('oixGrid').addEventListener('click', (e) => {
            // The order button sits inside the row, and opening a chart is not
            // what someone clicking "Buy CE" is asking for.
            const order = e.target.closest('[data-order]');
            if (order) {
                openTicket(order.dataset.order);
                return;
            }
            const tr = e.target.closest('tr[data-event-id]');
            if (!tr) return;
            const id = Number(tr.dataset.eventId);
            if (id === state.openId) closeDrill();
            else openDrill(tr.dataset.symbol, id);
        });

        $('oixLogicBtn').addEventListener('click', openLogic);

        $('oixTicketClose').addEventListener('click', closeTicket);
        $('oixTicketCancel').addEventListener('click', closeTicket);
        $('oixTicketSend').addEventListener('click', sendTicket);
        // Backdrop click closes; a click inside the dialog must not.
        $('oixTicketBack').addEventListener('click', (e) => {
            if (e.target === $('oixTicketBack')) closeTicket();
        });

    }

    // ── lifecycle ────────────────────────────────────────────────────

    // Wiring happens once, on the first activation, and the poll runs only
    // while the tab is on screen. Nothing here fires at page load: the Algo
    // page loads this file for a tab the user may never open, and a scanner
    // that polls regardless would cost a request a minute for nothing.
    function activate() {
        if (!$('oixGrid')) return;          // panel not on this page
        if (!state.started) {
            state.started = true;
            init();
            loadMeta().then(loadSnapshot);
        } else {
            loadSnapshot();
        }
        if (!state.timer && state.mode === 'live') {
            state.timer = setInterval(loadSnapshot, REFRESH_MS);
        }
    }

    function deactivate() {
        clearInterval(state.timer);
        state.timer = null;
        // A chart, a half-filled ticket or the logic sheet left floating over
        // another tab is the one thing a body-level overlay makes possible;
        // close all three.
        if (state.started) {
            if (!$('oixDrill').hidden) closeDrill();
            closeTicket();
            closeLogic();
        }
    }

    window.OIX = { activate, deactivate };
})();
