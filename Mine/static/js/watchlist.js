/* ================================================================
   Watchlist — tabs of symbols, a fundamentals grid, and a docked chart.

   Three pieces, in this file in this order:

     1. Tabs      create / rename / delete, persisted server-side per user.
     2. Grid      DataGrid.mountSortable over /api/watchlist/rows.
     3. Drilldown a plain inline SVG of the daily close and the derived P/E
                  line, docked to the bottom of the viewport.

   The drilldown deliberately mirrors the OI Crossover scanner's: same dock,
   same ‹ › stepping, same crosshair, same last-value tags. Two panels that
   do the same job should not be two different things to learn — the chart
   code here is that one adapted to a daily series with a shared x-axis and
   two independent y-axes (rupees left, P/E right).

   Prices come from the broker when a session exists and from the delayed
   fundamentals cache when it doesn't; the dot in the topbar says which.
   ================================================================ */

(function (global) {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const API = '/api/watchlist';
    const LAST_TAB_KEY = 'wl.lastTab';
    const REFRESH_MS = 60000;

    const state = {
        tabs: [],
        activeTab: null,
        rows: [],
        // Drilldown
        openSymbol: null,
        chartMode: 'price',
        range: '1y',
        chartData: null,
        hiddenSeries: new Set(),
        timer: null,
        // Type-ahead
        suggestions: [],
        sugIndex: -1,
        sugSeq: 0,
        // The dialog is shared by "new tab" and "rename"; this is which.
        modalTab: null,
    };

    // ── formatting ───────────────────────────────────────────────────

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    const isBlank = (v) => v === null || v === undefined || v === '';

    const money = (v) => isBlank(v) ? '—' :
        '₹' + Number(v).toLocaleString('en-IN', {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        });

    const pct = (v) => isBlank(v) ? '—' :
        (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';

    const ratio = (v) => isBlank(v) ? '—' : Number(v).toFixed(2);

    // Market cap in the unit Indian screens actually use. Anything past a
    // lakh crore reads as "₹17.74 L Cr" rather than fifteen digits.
    function marketCap(v) {
        if (isBlank(v) || !Number(v)) return '—';
        const cr = Number(v) / 1e7;
        if (cr >= 1e5) return '₹' + (cr / 1e5).toFixed(2) + ' L Cr';
        if (cr >= 1) return '₹' + cr.toLocaleString('en-IN', { maximumFractionDigits: 0 }) + ' Cr';
        return '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }

    const dayLabel = (iso) => {
        const d = new Date(iso + 'T00:00:00');
        return `${d.getDate()} ${MONTHS[d.getMonth()]} ${String(d.getFullYear()).slice(2)}`;
    };

    const clockTime = (iso) => {
        const d = new Date(iso);
        const h = d.getHours() % 12 || 12;
        return `${h}:${String(d.getMinutes()).padStart(2, '0')} ` +
               `${d.getHours() < 12 ? 'AM' : 'PM'}`;
    };

    // ── transport ────────────────────────────────────────────────────

    async function getJSON(url) {
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        return res.json();
    }

    async function sendJSON(url, method, body) {
        const res = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
                // CSRF is off in this app's config, but the token costs
                // nothing to send and these are the page's only writes —
                // they should keep working if it is ever switched on.
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '',
            },
            body: body ? JSON.stringify(body) : undefined,
        });
        return res.json();
    }

    const toast = (msg, ok) => {
        if (global.showNotification) global.showNotification(msg, ok ? 'success' : 'error');
        else if (!ok) console.warn(msg);
    };

    // ── tabs ─────────────────────────────────────────────────────────

    function renderTabs() {
        const bar = $('wlTabBar');
        const chips = state.tabs.map((t) => {
            const active = t.id === state.activeTab;
            return `<button class="wl-tab${active ? ' active' : ''}" data-tab="${t.id}">
                        <span>${DataGrid.escape(t.name)}</span>
                        <span class="wl-tab-count">${t.count}</span>
                        ${active ? `
                        <span class="wl-tab-act" data-act="rename" data-tab="${t.id}"
                              role="button" tabindex="0" title="Rename this tab">✎</span>
                        <span class="wl-tab-act" data-act="delete" data-tab="${t.id}"
                              role="button" tabindex="0" title="Delete this tab">✕</span>` : ''}
                    </button>`;
        }).join('');
        bar.innerHTML = chips +
            '<button class="wl-tab-add" id="wlAddTab" title="Create a new tab">＋ New tab</button>';
    }

    async function loadTabs(selectId) {
        const data = await getJSON(`${API}/tabs`);
        if (!data.success) { toast(data.error || 'Could not load tabs', false); return; }
        state.tabs = data.tabs || [];

        // Remembering the last tab is what makes this page usable as a
        // landing page — it is opened to look at one list, not to pick one.
        const remembered = Number(localStorage.getItem(LAST_TAB_KEY));
        const wanted = [selectId, state.activeTab, remembered]
            .find((id) => id && state.tabs.some((t) => t.id === id));
        state.activeTab = wanted || (state.tabs[0] && state.tabs[0].id) || null;
        if (state.activeTab) localStorage.setItem(LAST_TAB_KEY, String(state.activeTab));

        renderTabs();
        $('wlSearch').disabled = !state.activeTab;
        await loadRows();
    }

    function selectTab(id) {
        if (id === state.activeTab) return;
        state.activeTab = id;
        localStorage.setItem(LAST_TAB_KEY, String(id));
        closeDrill();
        renderTabs();
        loadRows();
    }

    // ── dialog (new tab / rename) ────────────────────────────────────

    function openModal(tab) {
        state.modalTab = tab || null;
        $('wlModalTitle').textContent = tab ? 'Rename tab' : 'New tab';
        $('wlModalInput').value = tab ? tab.name : '';
        $('wlModalMsg').textContent = '';
        $('wlModalBack').hidden = false;
        $('wlModalInput').focus();
        $('wlModalInput').select();
    }

    const closeModal = () => { $('wlModalBack').hidden = true; state.modalTab = null; };

    async function saveModal() {
        const name = $('wlModalInput').value.trim();
        if (!name) { $('wlModalMsg').textContent = 'Give the tab a name.'; return; }

        const tab = state.modalTab;
        const result = tab
            ? await sendJSON(`${API}/tabs/${tab.id}`, 'PUT', { name })
            : await sendJSON(`${API}/tabs`, 'POST', { name });

        if (!result.success) { $('wlModalMsg').textContent = result.error || 'Could not save.'; return; }
        closeModal();
        await loadTabs(tab ? tab.id : result.tab.id);
    }

    async function deleteTab(tab) {
        const what = tab.count
            ? `Delete "${tab.name}" and its ${tab.count} symbol${tab.count > 1 ? 's' : ''}?`
            : `Delete "${tab.name}"?`;
        if (!confirm(what)) return;
        const result = await sendJSON(`${API}/tabs/${tab.id}`, 'DELETE');
        if (!result.success) { toast(result.error || 'Could not delete the tab', false); return; }
        if (state.activeTab === tab.id) state.activeTab = null;
        closeDrill();
        await loadTabs();
    }

    // ── type-ahead ───────────────────────────────────────────────────

    function renderSuggestions() {
        const box = $('wlSuggest');
        if (!state.suggestions.length) {
            box.innerHTML = '<div class="wl-sug-empty">No match</div>';
            box.hidden = false;
            return;
        }
        box.innerHTML = state.suggestions.map((s, i) => `
            <button class="wl-sug${i === state.sugIndex ? ' wl-sug-active' : ''}"
                    data-symbol="${DataGrid.escape(s.symbol)}">
                <span class="wl-sug-sym">${DataGrid.escape(s.symbol)}</span>
                <span class="wl-sug-co">${DataGrid.escape(s.company || '')}</span>
                <span class="wl-sug-kind${s.kind === 'INDEX' ? ' wl-kind-index' : ''}">${s.kind}</span>
            </button>`).join('');
        box.hidden = false;
    }

    const hideSuggestions = () => {
        $('wlSuggest').hidden = true;
        state.suggestions = [];
        state.sugIndex = -1;
    };

    async function runSearch(q) {
        if (!q.trim()) { hideSuggestions(); return; }
        // The master is ~10k rows and every keystroke fires a request, so a
        // slow one must never overwrite a newer, faster one.
        const seq = ++state.sugSeq;
        const data = await getJSON(`${API}/search?q=${encodeURIComponent(q)}`);
        if (seq !== state.sugSeq) return;
        state.suggestions = data.results || [];
        state.sugIndex = state.suggestions.length ? 0 : -1;
        renderSuggestions();
    }

    async function addSymbol(symbol) {
        if (!state.activeTab) return;
        const result = await sendJSON(`${API}/tabs/${state.activeTab}/items`, 'POST', { symbol });
        if (!result.success) { toast(result.error || 'Could not add the symbol', false); return; }
        $('wlSearch').value = '';
        hideSuggestions();
        toast(`${symbol} added`, true);
        await loadTabs(state.activeTab);
    }

    async function removeItem(id, symbol) {
        const result = await sendJSON(`${API}/items/${id}`, 'DELETE');
        if (!result.success) { toast(result.error || 'Could not remove the symbol', false); return; }
        if (state.openSymbol === symbol) closeDrill();
        await loadTabs(state.activeTab);
    }

    // ── grid ─────────────────────────────────────────────────────────

    // The 52-week position bar. Rendered rather than formatted because it is
    // a two-part shape (fill to the current position, tick at it), and the
    // read it gives — "near its low", "at its high" — is the reason the row
    // carries a low and a high at all.
    function bandCell(v, row) {
        if (isBlank(v)) return '<span class="dg-muted">—</span>';
        const at = Math.max(0, Math.min(100, Number(v)));
        return `<span class="wl-band" title="${at.toFixed(0)}% of the 52-week range ` +
               `(${money(row.low52)} – ${money(row.high52)})">` +
               `<span class="wl-band-fill" style="width:${at.toFixed(1)}%"></span>` +
               `<span class="wl-band-mark" style="left:calc(${at.toFixed(1)}% - 1px)"></span></span>`;
    }

    const COLUMNS = [
        {
            key: 'symbol', label: 'Symbol', sortable: true, strong: true,
            render: (v, row) => DataGrid.escape(v) +
                (row.kind === 'INDEX' ? ' <span class="wl-sug-kind wl-kind-index">INDEX</span>' : ''),
        },
        {
            key: 'company', label: 'Stock Name', sortable: true,
            cellClass: 'dg-muted', title: (v) => v || '',
            format: (v) => v || '—',
        },
        { key: 'ltp', label: 'LTP', sortable: true, align: 'right', strong: true, format: money },
        {
            key: 'change_pct', label: 'Chg %', sortable: true, align: 'right',
            format: pct, tone: DataGrid.sign,
        },
        { key: 'low52', label: '52W Low', sortable: true, align: 'right', format: money },
        { key: 'high52', label: '52W High', sortable: true, align: 'right', format: money },
        { key: 'band52', label: '52W Range', sortable: true, align: 'center', render: bandCell },
        {
            key: 'from_high', label: 'Off High', sortable: true, align: 'right',
            format: pct, tone: DataGrid.sign,
            title: 'How far the last price sits below the 52-week high',
        },
        {
            key: 'pe', label: 'P/E Ratio', sortable: true, align: 'right', strong: true,
            cellClass: 'wl-pe-cell', format: ratio,
            title: (v, row) => row.kind === 'INDEX'
                ? 'Indices carry no earnings — click for the price history'
                : 'Price to Earnings (TTM) — click for the P/E and price history',
        },
        { key: 'market_cap', label: 'Mkt Cap', sortable: true, align: 'right', format: marketCap },
        {
            key: 'sector', label: 'Sector', sortable: true, cellClass: 'dg-muted',
            format: (v) => v || '—',
        },
        {
            key: 'id', label: '', align: 'center',
            render: (v, row) => `<button class="wl-del" data-remove="${v}" ` +
                `data-symbol="${DataGrid.escape(row.symbol)}" title="Remove from this tab">✕</button>`,
        },
    ];

    function renderGrid() {
        const host = $('wlGrid');
        if (!state.activeTab) {
            host.innerHTML = '<div class="wl-empty">No tabs yet. ' +
                'Create one with <strong>＋ New tab</strong> to start a watchlist.</div>';
            return;
        }
        if (!state.rows.length) {
            host.innerHTML = '<div class="wl-empty">This tab is empty. ' +
                'Search a stock or index above to add it.</div>';
            return;
        }
        DataGrid.mountSortable(host, {
            rows: state.rows,
            columns: COLUMNS,
            empty: 'No symbols in this tab',
            rowAttrs: (row) => `data-symbol="${DataGrid.escape(row.symbol)}"`,
            rowClass: (row) => row.symbol === state.openSymbol ? 'wl-row-open' : '',
            // The drilldown's ‹ › steps through the rows in the order they
            // are on screen, which is the sorted order, not the stored one.
            onSorted: (rows) => { state.rows = rows; },
        });
    }

    async function loadRows(opts) {
        const force = !!(opts && opts.force);
        if (!state.activeTab) { renderGrid(); return; }

        const btn = $('wlRefresh');
        btn.classList.add('wl-refreshing');
        try {
            const data = await getJSON(
                `${API}/rows?tab_id=${state.activeTab}${force ? '&refresh=1' : ''}`);
            if (!data.success) { toast(data.error || 'Could not load the watchlist', false); return; }
            state.rows = data.rows || [];
            $('wlAsOf').textContent = data.as_of ? 'as of ' + clockTime(data.as_of) : '—';
            $('wlDot').classList.toggle('wl-live', !!data.live);
            $('wlDot').title = data.live
                ? 'Live prices from the broker'
                : 'Delayed prices — no broker session, showing the cached last price';
            renderGrid();
            if (state.openSymbol && !state.rows.some((r) => r.symbol === state.openSymbol)) {
                closeDrill();
            } else {
                syncNav();
            }
        } finally {
            btn.classList.remove('wl-refreshing');
        }
    }

    // ── drilldown chart ──────────────────────────────────────────────

    // A plain inline SVG rather than a charting library: two polylines, a
    // grid and a crosshair is the whole requirement.
    //
    // Two independent y-axes. Price sits on the left in rupees and P/E on the
    // right as a multiple — quantities with no common unit, so a shared axis
    // would flatten one of them. Shown together because the question the
    // page exists to answer is whether a move was earnings or re-rating: a
    // price line rising with a flat P/E is growth, the two rising together is
    // the market paying more for the same rupee.
    //
    // Sized in real pixels from the measured container rather than scaled
    // from a fixed viewBox, so the axis labels don't stretch on a wide panel.
    const PAD = { L: 64, R: 56, T: 14, B: 34 };
    const CHART_H = 320;

    const CHART_MODES = {
        price: { label: 'Price', series: ['price'] },
        pe:    { label: 'P/E Ratio', series: ['pe'] },
        both:  { label: 'Price + P/E', series: ['price', 'pe'] },
    };

    const SERIES = {
        price: { key: 'close', cls: 'price', label: 'Close', fmt: money, axis: 'left' },
        pe:    { key: 'pe', cls: 'pe', label: 'P/E (TTM)', fmt: ratio, axis: 'right' },
    };

    // Round tick steps (1/2/2.5/5/10 × powers of ten) so the axis reads
    // 1,400 / 1,450 / 1,500 rather than whatever the extremes divide into.
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

    function padRange(lo, hi, frac) {
        if (hi === lo) { const bump = Math.abs(hi || 1) * 0.01 || 1; return [lo - bump, hi + bump]; }
        const pad = (hi - lo) * frac;
        return [lo - pad, hi + pad];
    }

    const tsToMs = (ts) => new Date(ts + 'T00:00:00').getTime();

    function activeSeries() {
        return CHART_MODES[state.chartMode].series
            .filter((name) => !state.hiddenSeries.has(name))
            .map((name) => ({ name, ...SERIES[name] }));
    }

    function buildGeometry(data, width) {
        const pts = data.points || [];
        if (pts.length < 2) return null;

        const plotW = width - PAD.L - PAD.R;
        const plotH = CHART_H - PAD.T - PAD.B;
        if (plotW < 80) return null;

        const t0 = tsToMs(pts[0].ts);
        const t1 = tsToMs(pts[pts.length - 1].ts);
        const span = Math.max(1, t1 - t0);

        const closes = pts.map((p) => p.close).filter((v) => v != null);
        // The 52-week rules are drawn against the price axis, so they are
        // folded into its range — otherwise a stock sitting well off its
        // high would have the line clipped clean off the frame.
        const refs = [data.high52, data.low52].filter((v) => v != null);
        const [pLo, pHi] = closes.length
            ? padRange(Math.min(...closes, ...refs), Math.max(...closes, ...refs), 0.08)
            : [0, 1];

        const pes = pts.map((p) => p.pe).filter((v) => v != null);
        const [eLo, eHi] = pes.length ? padRange(Math.min(...pes), Math.max(...pes), 0.10) : [0, 1];

        const xMs = (ms) => PAD.L + ((ms - t0) / span) * plotW;
        const x = (ts) => xMs(tsToMs(ts));
        const yP = (v) => PAD.T + (1 - (v - pLo) / (pHi - pLo)) * plotH;
        const yE = (v) => PAD.T + (1 - (v - eLo) / (eHi - eLo)) * plotH;

        return {
            pts, width, plotW, plotH, t0, t1, span, x, xMs, yP, yE,
            pLo, pHi, eLo, eHi,
            hasPe: pes.length > 0,
            refs: refs.length ? { high: data.high52, low: data.low52 } : null,
            y: (name) => (name === 'pe' ? yE : yP),
        };
    }

    // Six or so ticks, snapped to the first trading day of a month (or of a
    // year on the long ranges) so the same chart always rules in the same
    // places whatever the sampling was.
    function timeTicks(geo) {
        const years = geo.span / (365 * 86400000);
        const byYear = years > 2.5;
        const seen = new Set();
        const out = [];
        for (const p of geo.pts) {
            const d = new Date(p.ts + 'T00:00:00');
            const key = byYear ? d.getFullYear() : `${d.getFullYear()}-${d.getMonth()}`;
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({ ms: tsToMs(p.ts), label: byYear ? String(d.getFullYear()) : MONTHS[d.getMonth()] +
                       (d.getMonth() === 0 ? ` ${String(d.getFullYear()).slice(2)}` : '') });
        }
        // Thin to at most eight labels rather than drawing twelve months on
        // a 700px panel and letting them collide.
        const stride = Math.ceil(out.length / 8);
        return out.filter((_, i) => i % stride === 0);
    }

    function renderChart(data, geo) {
        const { pts, width, x, xMs } = geo;
        const right = width - PAD.R;
        const bottom = CHART_H - PAD.B;
        const drawn = activeSeries().filter((s) => s.name !== 'pe' || geo.hasPe);

        const pTicks = niceTicks(geo.pLo, geo.pHi, 7);
        const eTicks = niceTicks(geo.eLo, geo.eHi, 7);
        const tTicks = timeTicks(geo);
        const showPe = drawn.some((s) => s.name === 'pe');
        const showPrice = drawn.some((s) => s.name === 'price');
        // With price hidden, P/E moves to the left axis rather than leaving
        // 64px of empty gutter and hugging the right edge.
        const peLeft = showPe && !showPrice;
        const peAxisX = peLeft ? PAD.L - 8 : right + 8;

        const grid =
            pTicks.map((v) => `<line class="wl-grid" x1="${PAD.L}" y1="${geo.yP(v).toFixed(1)}" ` +
                              `x2="${right}" y2="${geo.yP(v).toFixed(1)}"></line>`).join('') +
            tTicks.map((t) => {
                const px = xMs(t.ms).toFixed(1);
                return `<line class="wl-grid" x1="${px}" y1="${PAD.T}" x2="${px}" y2="${bottom}"></line>`;
            }).join('');

        const axes =
            (showPrice ? pTicks : []).map((v) =>
                `<text class="wl-axis" x="${PAD.L - 8}" y="${(geo.yP(v) + 3).toFixed(1)}" ` +
                `text-anchor="end">${DataGrid.escape(money(v))}</text>`).join('') +
            (showPe ? eTicks : []).map((v) =>
                `<text class="wl-axis" x="${peAxisX}" y="${(geo.yE(v) + 3).toFixed(1)}" ` +
                `text-anchor="${peLeft ? 'end' : 'start'}">${ratio(v)}</text>`).join('') +
            tTicks.map((t) => {
                const px = xMs(t.ms);
                if (px < PAD.L + 12 || px > right - 12) return '';
                return `<text class="wl-axis wl-axis-t" x="${px.toFixed(1)}" y="${CHART_H - 12}" ` +
                       `text-anchor="middle">${DataGrid.escape(t.label)}</text>`;
            }).join('');

        // 52-week high/low rules, only while the price axis is on screen —
        // they are the row's headline numbers, and seeing where the line sat
        // against them is why the chart opens from that row.
        let refs = '';
        if (showPrice && geo.refs) {
            for (const [name, value] of [['high', geo.refs.high], ['low', geo.refs.low]]) {
                if (value == null) continue;
                const y = geo.yP(value);
                if (y < PAD.T || y > bottom) continue;
                refs += `<line class="wl-ref wl-ref-${name}" x1="${PAD.L}" y1="${y.toFixed(1)}" ` +
                        `x2="${right}" y2="${y.toFixed(1)}"></line>` +
                        `<text class="wl-ref-label wl-ref-label-${name}" x="${PAD.L + 6}" ` +
                        `y="${(y - 4).toFixed(1)}">52W ${name === 'high' ? 'High' : 'Low'} ` +
                        `${DataGrid.escape(money(value))}</text>`;
            }
        }

        const area = (showPrice && drawn.length === 1) ? (() => {
            const d = pts.filter((p) => p.close != null)
                .map((p) => `${x(p.ts).toFixed(1)},${geo.yP(p.close).toFixed(1)}`);
            if (!d.length) return '';
            return `<polygon class="wl-area-price" points="${PAD.L},${bottom} ${d.join(' ')} ` +
                   `${right},${bottom}"></polygon>`;
        })() : '';

        const lines = drawn.map((s) => {
            const d = pts.filter((p) => p[s.key] != null)
                .map((p) => `${x(p.ts).toFixed(1)},${geo.y(s.name)(p[s.key]).toFixed(1)}`).join(' ');
            return `<polyline class="wl-line wl-line-${s.cls}" points="${d}"></polyline>`;
        }).join('');

        // Last value: a dot on the line, a rule across to its axis, and a
        // tag with the number — the "where does this sit now" read.
        const last = [...pts].reverse().find((p) => p.close != null) || pts[pts.length - 1];
        const tags = drawn.map((s) => {
            const value = last[s.key];
            if (value == null) return '';
            const yv = geo.y(s.name)(value);
            const onLeft = s.axis === 'left' || (s.name === 'pe' && peLeft);
            const tagX = onLeft ? PAD.L - 58 : right + 2;
            return `<g class="wl-tag wl-tag-${s.cls}">` +
                   `<line class="wl-tag-rule" x1="${PAD.L}" y1="${yv.toFixed(1)}" ` +
                   `x2="${right}" y2="${yv.toFixed(1)}"></line>` +
                   `<circle cx="${x(last.ts).toFixed(1)}" cy="${yv.toFixed(1)}" r="3.5"></circle>` +
                   `<rect x="${tagX}" y="${(yv - 8).toFixed(1)}" width="56" height="16" rx="3"></rect>` +
                   `<text x="${(tagX + 28).toFixed(1)}" y="${(yv + 4).toFixed(1)}" ` +
                   `text-anchor="middle">${DataGrid.escape(s.fmt(value))}</text></g>`;
        }).join('');

        const dots = drawn.map((s) =>
            `<circle class="wl-cross-dot wl-dot-${s.cls}" data-key="${s.name}" r="4" hidden></circle>`
        ).join('');

        return `<svg class="wl-svg" width="${width}" height="${CHART_H}"
                     viewBox="0 0 ${width} ${CHART_H}" role="img"
                     aria-label="${DataGrid.escape(data.symbol)} ${CHART_MODES[state.chartMode].label}">
            ${grid}
            <rect class="wl-frame" x="${PAD.L}" y="${PAD.T}" width="${geo.plotW}"
                  height="${geo.plotH}"></rect>
            ${refs}${axes}${area}${lines}${tags}
            <g class="wl-cross" hidden>
              <line class="wl-cross-line" y1="${PAD.T}" y2="${bottom}"></line>
              ${dots}
            </g>
            <rect class="wl-hit" x="${PAD.L}" y="${PAD.T}" width="${geo.plotW}"
                  height="${geo.plotH}"></rect>
        </svg>
        <div class="wl-tip" hidden></div>`;
    }

    function renderLegend(data) {
        return CHART_MODES[state.chartMode].series.map((name) => {
            const s = SERIES[name];
            const off = state.hiddenSeries.has(name);
            const unavailable = name === 'pe' && !(data.points || []).some((p) => p.pe != null);
            return `<button class="wl-leg wl-leg-${s.cls}${off ? ' wl-leg-off' : ''}" ` +
                   `data-series="${name}" title="Show or hide this line"${unavailable ? ' disabled' : ''}>` +
                   `<span class="wl-eye">${off ? '🚫' : '👁'}</span>` +
                   `<i class="wl-leg-key"></i>${DataGrid.escape(s.label)}` +
                   `${unavailable ? ' (n/a)' : ''}</button>`;
        }).join('');
    }

    function bindCrosshair(body, geo) {
        const svg = body.querySelector('.wl-svg');
        const cross = body.querySelector('.wl-cross');
        const line = body.querySelector('.wl-cross-line');
        const tip = body.querySelector('.wl-tip');
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

        const move = (clientX) => {
            const rect = svg.getBoundingClientRect();
            const p = pts[nearest(clientX - rect.left)];
            const cx = x(p.ts);

            cross.removeAttribute('hidden');
            line.setAttribute('x1', cx.toFixed(1));
            line.setAttribute('x2', cx.toFixed(1));

            const parts = [];
            cross.querySelectorAll('.wl-cross-dot').forEach((dot) => {
                const name = dot.dataset.key;
                const s = SERIES[name];
                const value = p[s.key];
                if (value == null) { dot.setAttribute('hidden', ''); return; }
                dot.removeAttribute('hidden');
                dot.setAttribute('cx', cx.toFixed(1));
                dot.setAttribute('cy', geo.y(name)(value).toFixed(1));
                parts.push(`${s.label} ${s.fmt(value)}`);
            });

            tip.textContent = `${dayLabel(p.ts)}  ·  ${parts.join('  ·  ')}`;
            tip.removeAttribute('hidden');
            const half = tip.offsetWidth / 2;
            tip.style.left = Math.min(Math.max(cx, half), geo.width - half) + 'px';
        };

        svg.addEventListener('mousemove', (ev) => move(ev.clientX));
        // Touch matters here: this dock is the whole point of the chart on a
        // phone, where there is no hover to fall back on.
        svg.addEventListener('touchmove', (ev) => {
            if (!ev.touches.length) return;
            ev.preventDefault();
            move(ev.touches[0].clientX);
        }, { passive: false });

        const clear = () => {
            cross.setAttribute('hidden', '');
            tip.setAttribute('hidden', '');
        };
        svg.addEventListener('mouseleave', clear);
        svg.addEventListener('touchend', clear);
    }

    function chartNote(data) {
        const bits = [`${(data.points || []).length} daily closes`];
        if (data.kind === 'INDEX') {
            bits.push('indices carry no earnings, so no P/E line');
        } else if (data.eps_basis === 'reported') {
            bits.push('P/E is price ÷ the trailing-twelve-month EPS reported at each date');
        } else if (data.eps_basis === 'current') {
            bits.push(`P/E is price ÷ today's TTM EPS (${ratio(data.eps)}) held flat — ` +
                      'no clean quarterly series, so read the shape, not a re-rating');
        } else {
            bits.push('no EPS available, so no P/E line');
        }
        bits.push('prices from Yahoo daily candles');
        return bits.join(' · ');
    }

    function drawChart(body, data, attempt) {
        // The chart is sized in real pixels from the measured panel, and the
        // panel is measured the moment it is un-hidden — which can land
        // before layout has run and report 0, drawing the whole session into
        // a 320px stub. Wait a frame for a real width rather than shipping
        // that; a couple of frames is the most it ever takes.
        const measured = Math.floor(body.clientWidth);
        if (measured < 200 && (attempt || 0) < 5) {
            requestAnimationFrame(() => drawChart(body, data, (attempt || 0) + 1));
            return;
        }
        const width = Math.max(320, measured);
        const geo = buildGeometry(data, width);

        body.innerHTML =
            `<div class="wl-legend">${renderLegend(data)}</div>` +
            (geo ? renderChart(data, geo)
                 : '<div class="wl-drill-empty">Not enough history to draw this view.</div>') +
            `<div class="wl-drill-note">${DataGrid.escape(chartNote(data))}</div>`;

        if (geo) bindCrosshair(body, geo);

        body.querySelectorAll('.wl-leg').forEach((btn) => {
            btn.addEventListener('click', () => {
                const name = btn.dataset.series;
                if (state.hiddenSeries.has(name)) state.hiddenSeries.delete(name);
                else state.hiddenSeries.add(name);
                drawChart(body, data);
            });
        });
    }

    function syncChartTabs(data) {
        $('wlChartTabs').querySelectorAll('button').forEach((b) => {
            b.classList.toggle('active', b.dataset.mode === state.chartMode);
            // A P/E tab that draws nothing is worse than one you can't press.
            const noPe = data && data.kind === 'INDEX';
            b.disabled = noPe && b.dataset.mode !== 'price';
        });
        $('wlRanges').querySelectorAll('button').forEach((b) => {
            b.classList.toggle('active', b.dataset.range === state.range);
        });
    }

    async function openDrill(symbol, mode) {
        if (mode) state.chartMode = mode;
        state.openSymbol = symbol;
        state.hiddenSeries.clear();

        const drill = $('wlDrill');
        const body = $('wlDrillBody');
        drill.hidden = false;
        document.body.classList.add('wl-drill-open');

        const row = state.rows.find((r) => r.symbol === symbol) || {};
        $('wlDrillSym').innerHTML =
            `<i class="wl-avatar">${DataGrid.escape(symbol.slice(0, 1))}</i>` +
            DataGrid.escape(symbol) +
            `<span class="wl-drill-co">${DataGrid.escape(row.company || '')}</span>`;
        syncNav();
        syncChartTabs(null);
        body.innerHTML = '<div class="wl-drill-empty">Loading…</div>';

        try {
            const data = await getJSON(
                `${API}/history?symbol=${encodeURIComponent(symbol)}&range=${state.range}`);
            if (state.openSymbol !== symbol) return;  // stepped away while loading
            if (!data.success) {
                body.innerHTML = `<div class="wl-drill-empty">${DataGrid.escape(data.error ||
                    'No history available')}</div>`;
                return;
            }
            // The 52-week rules come from the row, not the history call —
            // they are the same numbers the grid is showing, and drawing a
            // separately-derived pair would invite them to disagree.
            data.high52 = row.high52;
            data.low52 = row.low52;
            state.chartData = data;
            syncChartTabs(data);
            if (data.kind === 'INDEX' && state.chartMode !== 'price') {
                state.chartMode = 'price';
                syncChartTabs(data);
            }
            drawChart(body, data);
            renderGrid();
        } catch (e) {
            body.innerHTML = `<div class="wl-drill-empty">Could not load ` +
                `${DataGrid.escape(symbol)}: ${DataGrid.escape(e.message)}</div>`;
        }
    }

    function step(delta) {
        const i = state.rows.findIndex((r) => r.symbol === state.openSymbol);
        const next = state.rows[i + delta];
        if (next) openDrill(next.symbol);
    }

    function syncNav() {
        const i = state.rows.findIndex((r) => r.symbol === state.openSymbol);
        $('wlPrev').disabled = i <= 0;
        $('wlNext').disabled = i < 0 || i >= state.rows.length - 1;
    }

    function closeDrill() {
        if ($('wlDrill').hidden) return;
        state.openSymbol = null;
        state.chartData = null;
        $('wlDrill').hidden = true;
        document.body.classList.remove('wl-drill-open');
        renderGrid();
    }

    // ── wiring ───────────────────────────────────────────────────────

    function bind() {
        // Tab bar — one delegated handler; the chips are re-rendered often.
        $('wlTabBar').addEventListener('click', (e) => {
            const act = e.target.closest('.wl-tab-act');
            if (act) {
                e.stopPropagation();
                const tab = state.tabs.find((t) => t.id === Number(act.dataset.tab));
                if (!tab) return;
                if (act.dataset.act === 'rename') openModal(tab);
                else deleteTab(tab);
                return;
            }
            if (e.target.closest('#wlAddTab')) { openModal(null); return; }
            const chip = e.target.closest('.wl-tab');
            if (chip) selectTab(Number(chip.dataset.tab));
        });

        // Grid — remove button, P/E cell, and row click, in that order of
        // specificity so the remove button never also opens the chart.
        $('wlGrid').addEventListener('click', (e) => {
            const del = e.target.closest('[data-remove]');
            if (del) {
                e.stopPropagation();
                removeItem(Number(del.dataset.remove), del.dataset.symbol);
                return;
            }
            const tr = e.target.closest('tr[data-symbol]');
            if (!tr) return;
            const symbol = tr.dataset.symbol;
            if (symbol === state.openSymbol) { closeDrill(); return; }
            openDrill(symbol, e.target.closest('.wl-pe-cell') ? 'pe' : 'price');
        });

        // Type-ahead
        const search = $('wlSearch');
        let debounce = null;
        search.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => runSearch(search.value), 180);
        });
        search.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') { hideSuggestions(); return; }
            if (!state.suggestions.length) return;
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const n = state.suggestions.length;
                state.sugIndex = (state.sugIndex + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
                renderSuggestions();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                const pick = state.suggestions[Math.max(0, state.sugIndex)];
                if (pick) addSymbol(pick.symbol);
            }
        });
        $('wlSuggest').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-symbol]');
            if (btn) addSymbol(btn.dataset.symbol);
        });
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.wl-search-wrap')) hideSuggestions();
        });

        // Refresh forces the fundamentals cache too — the plain 60s poll
        // only re-prices, which is the cheap half.
        $('wlRefresh').addEventListener('click', () => loadRows({ force: true }));

        // Dialog
        $('wlModalSave').addEventListener('click', saveModal);
        $('wlModalCancel').addEventListener('click', closeModal);
        $('wlModalClose').addEventListener('click', closeModal);
        $('wlModalInput').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') saveModal();
            if (e.key === 'Escape') closeModal();
        });
        $('wlModalBack').addEventListener('click', (e) => {
            if (e.target === $('wlModalBack')) closeModal();
        });

        // Drilldown controls
        $('wlDrillClose').addEventListener('click', closeDrill);
        $('wlPrev').addEventListener('click', () => step(-1));
        $('wlNext').addEventListener('click', () => step(1));
        $('wlChartTabs').addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-mode]');
            if (!btn || btn.disabled) return;
            state.chartMode = btn.dataset.mode;
            state.hiddenSeries.clear();
            syncChartTabs(state.chartData);
            if (state.chartData) drawChart($('wlDrillBody'), state.chartData);
        });
        $('wlRanges').addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-range]');
            if (!btn || btn.dataset.range === state.range) return;
            state.range = btn.dataset.range;
            syncChartTabs(state.chartData);
            if (state.openSymbol) openDrill(state.openSymbol);
        });

        document.addEventListener('keydown', (e) => {
            if ($('wlModalBack').hidden === false) return;  // the dialog owns Escape
            if ($('wlDrill').hidden) return;
            if (e.target.matches('input, textarea')) return;
            if (e.key === 'Escape') closeDrill();
            if (e.key === 'ArrowLeft') step(-1);
            if (e.key === 'ArrowRight') step(1);
        });

        // Redraw on resize: the chart is sized in real pixels, so it does
        // not reflow on its own.
        let resizeTimer = null;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                if (state.chartData && !$('wlDrill').hidden) {
                    drawChart($('wlDrillBody'), state.chartData);
                }
            }, 150);
        });

        // Pause the poll when the tab is in the background — nobody is
        // reading it, and it still costs a broker request every minute.
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                clearInterval(state.timer);
                state.timer = null;
            } else if (!state.timer) {
                loadRows();
                state.timer = setInterval(() => loadRows(), REFRESH_MS);
            }
        });
    }

    function init() {
        // The drilldown is portalled to <body> so `position: fixed` is
        // relative to the viewport, not to any transformed ancestor.
        document.body.appendChild($('wlDrill'));
        bind();
        loadTabs();
        state.timer = setInterval(() => loadRows(), REFRESH_MS);
    }

    global.watchlistInit = init;
})(window);
