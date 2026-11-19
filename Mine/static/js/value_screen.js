/* ================================================================
   Value tab on /scanners — service/value_screen.py.

   GET  /api/scanners/value?universe&min_roe&max_de   scores the cache, never fetches
   POST /api/scanners/value/refresh {universe, force}  fetches stale rows in a thread
   GET  /api/scanners/value/status/<id>                that thread's progress

   The gate boxes and Quality only re-score / re-filter what is cached, so
   they cost nothing. Fetch is the only thing that reaches Yahoo.
   ================================================================ */
(function () {
    'use strict';

    let loaded = false;
    let rows = [];
    let polling = null;
    let token = 0;

    const $ = (id) => document.getElementById(id);
    const num = (dp) => (v) => (v == null ? '—' : Number(v).toFixed(dp));
    const pct = (dp) => (v) => (v == null ? '—' : Number(v).toFixed(dp) + '%');

    function params() {
        const q = new URLSearchParams();
        q.set('universe', $('valueUniverse').value);
        q.set('min_roe', $('valueMinRoe').value || '15');
        q.set('max_de', $('valueMaxDe').value || '0.5');
        q.set('mos', $('valueMos').value || '20');
        return q.toString();
    }

    function ago(ts) {
        if (!ts) return '';
        const h = (Date.now() / 1000 - ts) / 3600;
        return h < 1 ? `${Math.max(1, Math.round(h * 60))} min ago` : `${h.toFixed(h < 10 ? 1 : 0)} h ago`;
    }

    const GATE_LABEL = { roe: 'ROE', de: 'Debt/Equity', cash: 'Cash ≥ profit', growth: 'Earnings growth' };

    function gateTitle(row) {
        return Object.entries(row.gates || {})
            .map(([k, v]) => `${GATE_LABEL[k] || k}: ${v === null ? 'n/a' : v ? 'pass' : 'FAIL'}`)
            .join('\n');
    }

    const FACTOR_LABEL = {
        pe_vs_sector: 'P/E vs peers', pe_vs_own: 'P/E vs own history', ev_vs_sector: 'EV/EBITDA vs peers',
        pb_vs_sector: 'P/B vs peers', fcf_yield: 'FCF yield', peg: 'PEG',
    };

    function scoreTitle(row) {
        return Object.entries(row.pct || {})
            .filter(([, v]) => v != null)
            .map(([k, v]) => `${FACTOR_LABEL[k]}: ${Math.round(v)} / 100`)
            .join('\n') || 'Not enough data';
    }

    const RATING_CLASS = { 5: 'best', 4: 'good', 3: 'fair', 2: 'dear', 1: 'avoid', 0: 'none' };

    const COLUMNS = [
        { label: '#', render: (_v, _r, i) => String(i + 1), align: 'right' },
        {
            key: 'rating_tier', label: 'Ranking', sortable: true,
            sortValue: (r) => r.rating_tier * 1000 + (r.value_score || 0),
            render: (_v, r) => `<span class="value-rating value-rating--${RATING_CLASS[r.rating_tier] || 'none'}">` +
                `${r.rank}. ${DataGrid.escape(r.rating || '')}</span>`,
            title: (_v, r) => (r.rating_why || []).join('\n'),
            thTitle: 'Very Best = score ≥ 70, passes every quality check, price at or under Buy below, 4+ readings.\n' +
                'Good = score ≥ 60, passes quality, price under fair value.\n' +
                'Fair = in between · Expensive = score < 30 or price 20 %+ over fair value ·\n' +
                'Avoid = loss-making or fails 2+ quality checks · Not rated = too little data.\n' +
                'Upside over 150 % is capped at Fair (usually a one-off profit). Hover a cell for why.\n' +
                'A ranking of the numbers, not investment advice.',
        },
        {
            key: 'symbol', label: 'Symbol', sortable: true, strong: true,
            render: (s) => `<a href="https://in.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(s)}" ` +
                `target="_blank" rel="noopener noreferrer" class="symbol-link" ` +
                `data-candle="${DataGrid.escape(s)}">${DataGrid.escape(s)}</a>`,
            title: (_v, r) => `${r.company || ''}\nData: ${r.source === 'screener' ? 'Screener.in' : 'Yahoo (no Screener page)'}`,
        },
        { key: 'sector', label: 'Sector', sortable: true, title: (_v, r) => r.industry || '' },
        { key: 'value_score', label: 'Score', sortable: true, align: 'right', strong: true,
          format: num(0), title: (_v, r) => scoreTitle(r),
          tone: (v) => (v == null ? 'muted' : v >= 70 ? 'pos' : v <= 30 ? 'neg' : ''),
          thTitle: 'Mean percentile of the readings this stock has (100 = cheapest).\nHover a cell for each one.' },
        { key: 'quality', label: 'Quality', sortable: true, align: 'center',
          render: (v) => (v ? '✓' : '✗'), title: (_v, r) => gateTitle(r),
          tone: (v) => (v ? 'pos' : 'muted') },
        { key: 'price', label: 'Price', sortable: true, align: 'right', format: num(2),
          title: (_v, r) => (r.price_date ? `Close of ${r.price_date}` : `As fetched ${ago(r.fetched_at)}`),
          thTitle: "Last close from the app's daily stock file (updated every night);\nthe P/E, P/B, yield and fair-value upside move with it." },
        { key: 'fair_value', label: 'Fair value', sortable: true, align: 'right', format: num(0),
          title: (_v, r) => (r.fair_pe ? `EPS × P/E ${r.fair_pe.toFixed(1)} (lower of peer and own P/E)` : 'Loss-making or no P/E to compare'),
          thTitle: 'EPS × the lower of its peer P/E and its own ~4-year median P/E.' },
        { key: 'buy_below', label: 'Buy below', sortable: true, align: 'right', strong: true, format: num(0),
          tone: (_v, r) => (r.in_buy_zone ? 'pos' : ''),
          thTitle: 'Fair value less the Margin box. Green = the price is already at or under it.' },
        { key: 'upside', label: 'Upside', sortable: true, align: 'right', format: pct(0), tone: DataGrid.sign,
          thTitle: 'Fair value ÷ price − 1.' },
        { key: 'pe', label: 'P/E', sortable: true, align: 'right', format: num(1) },
        { key: 'pe_sector', label: 'Peer P/E', sortable: true, align: 'right', format: num(1),
          title: (_v, r) => (r.peer_group ? `Median of ${r.peer_group}` : 'Fewer than 3 peers'),
          thTitle: 'Median P/E of its industry among the stocks screened (its sector when the\nindustry has fewer than 3). Hover a cell for the group.' },
        { key: 'pe_own', label: 'Own P/E', sortable: true, align: 'right', format: num(1),
          title: (_v, r) => (r.own_pe_years ? `Median over ${r.own_pe_years} years` : 'No reported EPS history'),
          thTitle: "Median of this stock's own daily P/E (close ÷ TTM EPS) over up to ~4 years." },
        { key: 'pb', label: 'P/B', sortable: true, align: 'right', format: num(2) },
        { key: 'ev_ebitda', label: 'EV/EBITDA', sortable: true, align: 'right', format: num(1) },
        { sortValue: (r) => r.factors && r.factors.fcf_yield, label: 'FCF yld', sortable: true, align: 'right',
          render: (_v, r) => pct(1)(r.factors && r.factors.fcf_yield) },
        { sortValue: (r) => r.factors && r.factors.peg, label: 'PEG', sortable: true, align: 'right',
          render: (_v, r) => num(2)(r.factors && r.factors.peg) },
        { key: 'roe', label: 'ROE', sortable: true, align: 'right', format: pct(1) },
        { key: 'roce', label: 'ROCE', sortable: true, align: 'right', format: pct(1),
          thTitle: 'Return on capital employed (Screener). Not a gate.' },
        { key: 'de', label: 'D/E', sortable: true, align: 'right', format: num(2) },
        { key: 'cash_conv', label: 'Cash/Profit', sortable: true, align: 'right', format: num(2),
          thTitle: 'Operating cash flow ÷ net profit. Below ~0.8, profits are not turning into cash.' },
        { key: 'earnings_growth', label: 'Profit gr.', sortable: true, align: 'right', format: pct(1),
          tone: DataGrid.sign,
          title: (_v, r) => ({ '3y': '3-year compounded profit growth', ttm: 'Last 12 months (no 3-year figure)',
                               quarter: "Yahoo's latest quarter, year on year" }[r.growth_basis] || ''),
          thTitle: 'Compounded profit growth over 3 years (Screener); hover a cell for the basis.' },
        { key: 'div_yield', label: 'Div', sortable: true, align: 'right', format: pct(2) },
        { key: 'margin', label: 'Graham MoS', sortable: true, align: 'right', format: pct(0),
          tone: DataGrid.sign, title: (_v, r) => (r.graham ? `Graham number ${r.graham.toFixed(1)}` : 'Loss-making or no book value') },
        { key: 'market_cap_cr', label: 'Mcap ₹Cr', sortable: true, align: 'right',
          format: (v) => (v == null ? '—' : Math.round(v).toLocaleString('en-IN')) },
    ];

    function render() {
        const grid = $('valueGrid');
        let shown = $('valueQualityOnly').checked ? rows.filter((r) => r.quality) : rows;
        if ($('valueBuyOnly').checked) shown = shown.filter((r) => r.in_buy_zone);
        const q = $('valueSearch').value.trim().toUpperCase();
        if (q) {
            shown = shown.filter((r) => r.symbol.includes(q) || (r.company || '').toUpperCase().includes(q));
        }
        $('valueCount').textContent = `(${shown.length})`;
        DataGrid.mountSortable(grid, {
            rows: shown,
            columns: COLUMNS,
            empty: rows.length ? 'Nothing matches these filters — loosen ROE, D/E or the margin.'
                : 'Nothing fetched yet for this list — click 💎 Fetch (one page every ~3 s: NIFTY 500 ≈ 25 min, All ≈ 2 h).',
            rowClass: (r) => (r.quality ? '' : 'value-gate-fail'),
            defaultSort: { key: 'rating_tier', dir: 'desc' },
        });
    }

    function status(text) { $('valueStatus').textContent = text || ''; }

    async function load() {
        const mine = ++token;
        $('valueResults').classList.remove('results-hidden');
        if (!rows.length) {
            $('valueGrid').innerHTML = '<div class="cpr-grid-status"><span class="cpr-grid-spinner"></span>Reading cached fundamentals…</div>';
        }
        try {
            const res = await fetch('/api/scanners/value?' + params());
            const data = await res.json();
            if (mine !== token) return;
            if (!data.success) throw new Error(data.error || 'failed');
            rows = data.rows || [];
            render();
            if (data.task && data.task.status === 'running') {
                watch(data.task.id);
            } else if (!polling) {
                status(data.cached
                    ? `${data.cached} of ${data.total} stocks · oldest ${ago(data.oldest)}`
                    : '');
            }
        } catch (e) {
            if (mine !== token) return;
            $('valueGrid').innerHTML = `<div class="cpr-grid-status cpr-grid-status--error">❌ ${DataGrid.escape(e.message)}</div>`;
        }
    }

    function busy(on) {
        const btn = $('valueRefreshBtn');
        btn.disabled = on;
        btn.classList.toggle('loading', on);
    }

    function watch(taskId) {
        if (polling) return;
        busy(true);
        let ticks = 0;
        polling = setInterval(async () => {
            try {
                const res = await fetch('/api/scanners/value/status/' + encodeURIComponent(taskId));
                const data = await res.json();
                const t = data.task;
                if (!t) throw new Error(data.error || 'lost the task');
                const left = t.done && t.started ? (Date.now() / 1000 - t.started) / t.done * (t.total - t.done) : null;
                status(`Fetching… ${t.done} / ${t.total}` + (t.failed ? ` (${t.failed} failed)` : '') +
                       (left ? ` · ~${Math.ceil(left / 60)} min left` : ''));
                // Re-score every ~15 s so rows appear as they arrive.
                if (++ticks % 5 === 0) load();
                if (t.status !== 'running') {
                    clearInterval(polling);
                    polling = null;
                    busy(false);
                    const done = load();
                    if (t.status === 'error') done.then(() => status('❌ ' + (t.error || 'Fetch failed')));
                }
            } catch (e) {
                clearInterval(polling);
                polling = null;
                busy(false);
                status('❌ ' + e.message);
            }
        }, 3000);
    }

    async function refresh(force) {
        busy(true);
        status('Starting…');
        try {
            const res = await fetch('/api/scanners/value/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ universe: $('valueUniverse').value, force: !!force }),
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'failed');
            if (data.task.status === 'running') {
                watch(data.task.id);
            } else {
                busy(false);
                status('Everything is under a week old (prices are today’s) — shift-click to re-fetch anyway.');
                load();
            }
        } catch (e) {
            busy(false);
            status('❌ ' + e.message);
        }
    }

    function bind() {
        $('valueRefreshBtn').addEventListener('click', (e) => refresh(e.shiftKey));
        $('valueUniverse').addEventListener('change', () => { rows = []; load(); });
        ['valueMinRoe', 'valueMaxDe', 'valueMos'].forEach((id) => $(id).addEventListener('change', load));
        $('valueQualityOnly').addEventListener('change', render);
        $('valueBuyOnly').addEventListener('change', render);
        $('valueSearch').addEventListener('input', render);
        $('valueGrid').addEventListener('click', (e) => {
            const link = e.target.closest('[data-candle]');
            if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
            if (!window.CandleModal) return;
            e.preventDefault();
            window.CandleModal.open(link.dataset.candle, { rows, interval: '1d', single: true, tpo: false });
        });
    }

    window.initValueScreenOnce = function () {
        if (loaded) return;
        loaded = true;
        const go = () => { bind(); load(); };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', go, { once: true });
        } else {
            go();
        }
    };
})();
