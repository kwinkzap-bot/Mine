/* ================================================================
   OI Crossover scanner.

   The table is the shared DataGrid (click-to-sort comes free); this file
   owns the filter bar, the auto-refresh, and the row drilldown that draws
   the two OI-change lines whose crossing is the whole signal.

   Filtering happens here rather than server-side: the API returns one row
   per symbol that crossed today — a few hundred at most — so shipping the
   whole set once and narrowing it in the browser keeps every dropdown
   instant and costs one request per refresh instead of one per keystroke.
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
        filters: { search: '', quality: 'all', crossCount: 0, oiChg: 0, sector: 'all' },
        openSymbol: null,
        hiddenSeries: new Set(),
        chartMode: 'oi_change',
        chartData: null,
        displayRows: [],
        timer: null,
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

    function visibleRows() {
        const f = state.filters;
        const needle = f.search.trim().toUpperCase();
        return state.rows.filter((r) => {
            if (needle && !r.symbol.includes(needle)) return false;
            if (f.quality !== 'all' && r.quality !== f.quality) return false;
            if (f.crossCount && r.cross_count > f.crossCount) return false;
            if (f.oiChg && r.oi_chg_pct < f.oiChg) return false;
            if (f.sector !== 'all' && !(r.sectors || []).includes(f.sector)) return false;
            return true;
        });
    }

    // ── table ────────────────────────────────────────────────────────

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
            key: 'quality', label: 'Quality', sortable: true, align: 'center',
            // 'weak' is the leftover bucket — the winning leg didn't add OI and
            // the losing leg didn't shed any, so the cross is drift, not flow.
            badge: (v) => ({ strong: 'pos', aggressive: 'info', covering: 'warn' }[v] || 'neutral'),
        },
        {
            key: 'cross_time', label: 'Crossover Time', sortable: true, align: 'center',
            format: clock12,
        },
        {
            key: 'cross_count', label: 'Cross Count', sortable: true, align: 'right',
            // Double-digit counts mean the lines are chopping around each
            // other, so the direction tag on that row is close to noise.
            tone: (v) => (v >= 10 ? 'muted' : null),
            title: (v) => (v >= 10 ? `${v} crosses today — choppy, treat the tag with care` : ''),
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

    function renderTable() {
        const rows = visibleRows();
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
            defaultSort: { key: 'cross_time', dir: 'desc' },
            rowClass: (r) => `oix-row oix-${r.direction.toLowerCase()}` +
                             (r.symbol === state.openSymbol ? ' oix-row-open' : ''),
            rowAttrs: (r) => `data-symbol="${DataGrid.escape(r.symbol)}"`,
            // Prev/next in the chart panel step through this exact order.
            onSorted: (sorted) => { state.displayRows = sorted; },
        });
        if (state.openSymbol) syncNav();
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
    async function openDrill(symbol) {
        state.openSymbol = symbol;
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
        const i = rows.findIndex((r) => r.symbol === state.openSymbol);
        if (i < 0) return;
        const next = rows[i + delta];
        if (next) openDrill(next.symbol);
    }

    function syncNav() {
        const rows = state.displayRows.length ? state.displayRows : visibleRows();
        const i = rows.findIndex((r) => r.symbol === state.openSymbol);
        $('oixPrev').disabled = i <= 0;
        $('oixNext').disabled = i < 0 || i >= rows.length - 1;
    }

    function closeDrill() {
        state.openSymbol = null;
        state.chartData = null;
        $('oixDrill').hidden = true;
        document.body.classList.remove('oix-drill-open');
        renderTable();
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
        renderTable();
        // A refresh that drops the open symbol (filters changed, new session)
        // should close the panel rather than leave a stale chart on screen.
        if (state.openSymbol && !state.rows.some((r) => r.symbol === state.openSymbol)) closeDrill();
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

            const sector = $('oixSector');
            for (const s of data.sectors || []) {
                sector.insertAdjacentHTML('beforeend',
                    `<option value="${DataGrid.escape(s)}">${DataGrid.escape(s)}</option>`);
            }
            const dateSel = $('oixDate');
            dateSel.innerHTML = (data.dates || [])
                .map((d) => `<option value="${DataGrid.escape(d)}">${DataGrid.escape(d)}</option>`)
                .join('') || '<option value="">No recorded sessions</option>';
        } catch (e) {
            console.error('[OIX] meta', e);
        }
    }

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

        // Portal the docked panel to <body>. Left inside .sw-wrap its
        // position:fixed resolved against an ancestor box rather than the
        // viewport, docking it partway up the page instead of at the bottom.
        // A body-level overlay also can't be affected by whatever containing
        // block a future page-chrome change introduces.
        document.body.appendChild($('oixDrill'));

        $('oixSearch').addEventListener('input', (e) => {
            f.search = e.target.value;
            renderTable();
        });
        $('oixQuality').addEventListener('change', (e) => { f.quality = e.target.value; renderTable(); });
        $('oixCrossCount').addEventListener('change', (e) => { f.crossCount = Number(e.target.value); renderTable(); });
        $('oixOiChg').addEventListener('change', (e) => { f.oiChg = Number(e.target.value); renderTable(); });
        $('oixSector').addEventListener('change', (e) => { f.sector = e.target.value; renderTable(); });

        document.querySelectorAll('.oix-mode-btn').forEach((b) => {
            b.addEventListener('click', () => setMode(b.dataset.mode));
        });
        $('oixDate').addEventListener('change', (e) => {
            state.date = e.target.value || null;
            closeDrill();
            loadSnapshot();
        });

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
            if ($('oixDrill').hidden) return;
            if (e.key === 'Escape') closeDrill();
            else if (e.key === 'ArrowLeft') stepSymbol(-1);
            else if (e.key === 'ArrowRight') stepSymbol(1);
        });

        // Delegated: the grid re-renders on every sort, filter and refresh,
        // so per-row handlers would need rebinding each time.
        $('oixGrid').addEventListener('click', (e) => {
            const tr = e.target.closest('tr[data-symbol]');
            if (!tr) return;
            const symbol = tr.dataset.symbol;
            if (symbol === state.openSymbol) closeDrill();
            else openDrill(symbol);
        });

        loadMeta().then(loadSnapshot);
        state.timer = setInterval(loadSnapshot, REFRESH_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
