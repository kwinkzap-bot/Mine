/* ================================================================
   Finance tab — renders the 'Mine' Google Sheet.

   Charts are deliberately single-series. The sheet's "Profit" column
   is exactly 12.5% of "Amount Collected" on every row, so plotting it
   as a second series would draw the same shape twice against a second
   y-axis. It's shown in the tooltip as the derived figure it is.
   ================================================================ */

window._finLoaded = false;
window._finData = null;
window._finCharts = {};
window._finSubtab = 'overview';

const _FIN_PROFIT_RATE = 0.125;  // the flat rate the sheet's Merged Data applies

// ── Formatting ────────────────────────────────────────────────────
function _finFmt(n, dash = '—') {
    if (n === null || n === undefined || isNaN(n)) return dash;
    return '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function _finFmtCompact(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const a = Math.abs(n);
    if (a >= 1e7) return '₹' + (n / 1e7).toFixed(2) + ' Cr';
    if (a >= 1e5) return '₹' + (n / 1e5).toFixed(2) + ' L';
    if (a >= 1e3) return '₹' + (n / 1e3).toFixed(1) + 'k';
    return '₹' + Number(n).toFixed(0);
}

function _finCssVar(name, fallback) {
    const wrap = document.querySelector('.fin-wrap');
    if (!wrap) return fallback;
    const v = getComputedStyle(wrap).getPropertyValue(name).trim();
    return v || fallback;
}

// ── Colour helpers ────────────────────────────────────────────────
// The app ships five themes (light, cream, ocean, dark, forest), each with
// its own --oip-accent. Deriving the series colour from that token instead
// of hard-coding one hue keeps the charts in each theme's own language, and
// any theme added later is picked up for free.

function _finRgb(color) {
    const m = String(color).match(/rgba?\(([^)]+)\)/);
    if (m) {
        const p = m[1].split(',').map(s => parseFloat(s));
        return [p[0], p[1], p[2]];
    }
    let h = String(color).trim().replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    if (h.length < 6) return [42, 120, 214];
    return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
}

/** Relative luminance (WCAG) — decides whether a surface is light or dark. */
function _finLuminance(color) {
    const srgb = _finRgb(color).map(v => {
        const c = v / 255;
        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * srgb[0] + 0.7152 * srgb[1] + 0.0722 * srgb[2];
}

/** Mix `color` toward black (pct>0 on light) or white, returning rgb(). */
function _finMix(color, pct, towardWhite) {
    const [r, g, b] = _finRgb(color);
    const t = towardWhite ? 255 : 0;
    const m = (c) => Math.round(c + (t - c) * pct);
    return `rgb(${m(r)}, ${m(g)}, ${m(b)})`;
}

/**
 * Emphasis colour for the peak bar: the same hue pushed away from the
 * surface — darker on a light theme, lighter on a dark one. One hue, so it
 * reads as "more of the same measure" rather than a second category, and it
 * always gains contrast against whatever surface it sits on.
 */
function _finPeakColor(accent, surface) {
    return _finMix(accent, 0.42, _finLuminance(surface) < 0.4);
}

function _finEsc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
}

// Sheet dates are DD/MM/YYYY — parse explicitly rather than trusting
// Date(), which reads 07/08/2026 as 7 August in some locales and
// 8 July in others.
function _finParseDMY(s) {
    if (!s) return null;
    const m = String(s).trim().match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (!m) return null;
    const d = new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
    return isNaN(d.getTime()) ? null : d;
}

/** Today at midnight — sheet dates carry no time, so compare day to day. */
function _finToday() {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
}

/**
 * True when the loan's term has already ended. Strictly before today, so a
 * loan ending today is not yet past. An unparseable or blank end date is
 * never flagged — a missing date isn't evidence of anything.
 */
function _finIsPastEnd(endDate) {
    const e = _finParseDMY(endDate);
    return !!e && e < _finToday();
}

// ── Load ──────────────────────────────────────────────────────────
async function financeLoad(force = false) {
    const btn = document.getElementById('finRefreshBtn');
    const body = document.getElementById('finBody');
    if (btn) { btn.disabled = true; btn.textContent = '⟳ Loading…'; }
    if (!window._finLoaded && body) {
        body.innerHTML = '<div class="fin-msg">Loading finance data from Google Sheets…</div>';
    }

    try {
        const res = await fetch('/api/finance/summary' + (force ? '?force=1' : ''));
        const data = await res.json();

        if (!data.success) {
            _finRenderError(data, res.status);
            return;
        }

        window._finData = data;
        window._finLoaded = true;
        _finRender();
    } catch (e) {
        _finRenderError({ error: e.message }, 0);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '⟳ Refresh'; }
    }
}

function _finRenderError(data, status) {
    const body = document.getElementById('finBody');
    if (!body) return;
    const setup = data.config_error || status === 503;
    body.innerHTML = `
      <div class="fin-error">
        <h4>${setup ? 'Finance sheet not configured' : 'Could not load finance data'}</h4>
        <div>${_finEsc(data.error || 'Unknown error')}</div>
        ${setup ? `
        <div style="margin-top:8px">Check <code>env/Mine.env</code> has
          <code>GOOGLE_SHEETS_CREDENTIALS_PATH</code> and <code>FINANCE_SHEET_ID</code>,
          that the key file exists, and that the sheet is shared (Viewer) with the
          service account address.
        </div>` : ''}
      </div>`;
}

// ── Render ────────────────────────────────────────────────────────
function _finRender() {
    const d = window._finData;
    if (!d) return;

    const upd = document.getElementById('finUpdated');
    if (upd) {
        upd.textContent = `${d.sheet_title} · ${d.fetched_at}` + (d.cached ? ' (cached)' : '');
    }

    document.getElementById('finBody').innerHTML = `
      ${_finKpisHtml(d)}
      ${_finPendingHtml(d)}
      <div class="fin-subtabs" id="finSubtabs">
        <button class="fin-subtab" data-sub="overview">Overview</button>
        <button class="fin-subtab" data-sub="track">Daily / Weekly Loans (${d.counts.track_loans})</button>
        <button class="fin-subtab" data-sub="monthly">Monthly Loans (${d.counts.monthly_loans})</button>
        <button class="fin-subtab" data-sub="withdraw">Withdrawals (${d.counts.withdrawals})</button>
        <button class="fin-subtab" data-sub="collections">Collections (${d.counts.collection_blocks})</button>
      </div>
      <div id="finSubBody"></div>`;

    document.querySelectorAll('#finSubtabs .fin-subtab').forEach(b => {
        b.onclick = () => _finSwitchSub(b.dataset.sub);
    });
    _finSwitchSub(window._finSubtab);
}

// ── Pending accordions ────────────────────────────────────────────
//
// Both lists answer "what still needs collecting", so both are keyed off the
// sheet's own 'Is Complpeted' flag rather than the amount columns.
//
// Two literal readings were rejected against the data (2026-07-15):
//   · Daily/Weekly "has missing EMI" — Missing EMI > 0 matches 13 loans, all
//     of them already Completed, so the list would hold nothing pending.
//   · Monthly "red rows" — End date < today matches 38, but 36 are Completed
//     loans whose term has simply elapsed.
// Confirmed with the owner: in-progress for Daily/Weekly, in-progress AND
// past end date for Monthly.

/** Soonest end date first — the most overdue rises to the top. */
function _finByEndDate(a, b) {
    const da = _finParseDMY(a.end_date), db = _finParseDMY(b.end_date);
    if (!da && !db) return 0;
    if (!da) return 1;
    if (!db) return -1;
    return da - db;
}

function _finPendingHtml(d) {
    const trackAll = (d.track_loans || []).filter(r => r.is_completed === false);
    // Owner's rule: only rows whose Missing EMI has a non-zero first value.
    // Negatives are kept and shown as-is (-28/100) — they are the bulk of it.
    // A blank is not a zero but carries no value to match on, so it is out too.
    const track = trackAll
        .filter(r => r.missing_emi != null && r.missing_emi !== 0)
        .sort(_finByEndDate);
    const monthly = (d.monthly_loans || [])
        .filter(r => r.is_completed === false && _finIsPastEnd(r.end_date))
        .sort(_finByEndDate);

    const sumRming = rows => rows.reduce((a, r) => a + (r.remaining_amt || 0), 0);
    const hidden = trackAll.length - track.length;
    const hiddenAmt = sumRming(trackAll) - sumRming(track);

    // Money owed reads as a loss when it has gone negative, which is what the
    // grid's `neg` tone already means — no finance-specific cell class needed.
    // _finFmt's 2nd parameter is its dash fallback, so it must not be passed by
    // reference: the grid calls format(value, row) and the row would become it.
    const amount = { format: v => _finFmt(v), tone: v => (v != null && v < 0) ? 'neg' : '' };

    const table = (rows, withType, emptyMsg) => DataGrid.render({
        rows,
        empty: emptyMsg,
        rowClass: r => _finIsPastEnd(r.end_date) ? 'is-overdue' : '',
        columns: [
            { key: 'name', label: 'Name', strong: true },
            ...(withType ? [
                { key: 'missing_emi', label: 'Miss EMI' },
                { key: 'loan_type',   label: 'Type' },
            ] : []),
            { key: 'remaining_amt',         label: 'Rming Amt to colt', ...amount },
            { key: 'collected',             label: 'Collected',         ...amount },
            { key: 'total_collection_amt',  label: 'To Collect',        ...amount },
            { key: 'end_date', label: 'End',
              render: (v, r) => DataGrid.escape(v ?? '—') +
                  (_finIsPastEnd(r.end_date) ? ' <span class="fin-late">late</span>' : '') },
        ],
    });

    // The shared .dg-card shell, but as <details> so the native accordion
    // survives: expanded by default, keyboard accessible, no JS.
    return `<div class="fin-accs">
      <details class="dg-card" open>
        <summary class="dg-card-hdr">
          <span class="dg-card-title">Daily / Weekly Loans — Pending</span>
          <span class="fin-acc-meta">${track.length} loans · ${_finFmt(sumRming(track))} to collect</span>
        </summary>
        <div class="fin-acc-body">
          ${table(track, true, 'Nothing pending — every daily/weekly loan is marked completed.')}
        </div>
      </details>
      <details class="dg-card" open>
        <summary class="dg-card-hdr">
          <span class="dg-card-title">Monthly Loans — Pending</span>
          <span class="fin-acc-meta">${monthly.length} overdue · ${_finFmt(sumRming(monthly))} to collect</span>
        </summary>
        <div class="fin-acc-body">
          ${table(monthly, false, 'Nothing overdue — no in-progress monthly loan is past its end date.')}
        </div>
      </details>
    </div>`;
}

// The Invested tab's by-type rows. 'Dialy' is the sheet's spelling and it is
// the daily/weekly book — the same loans the Track tab lists.
function _finByType(s, type) {
    const row = (s.by_type || []).find(b =>
        String(b.type).trim().toLowerCase() === type);
    return row || {};
}

function _finKpisHtml(d) {
    const s = d.summary;
    const tally = (s.tally || '').toUpperCase();
    const tallyOk = tally === 'PASS';
    const track = _finByType(s, 'dialy');
    const mon = _finByType(s, 'monthly');

    // TT Amt in Line + Amt in Hand. Computed rather than read straight from
    // the sheet's own "Total Amount in Line" cell, so the card always equals
    // the two tiles it claims to add. The sheet's cell is then a cross-check:
    // if the two ever disagree, say so instead of quietly picking one.
    const inLine = s.total_in_line;
    const inHand = s.amt_in_hand;
    const grand = (inLine == null || inHand == null) ? null : inLine + inHand;
    const sheetGrand = s.total_amount_in_line;
    const grandMismatch = (grand != null && sheetGrand != null &&
                           Math.abs(grand - sheetGrand) > 0.5);

    const kpi = (label, value, sub, cls) => `
      <div class="fin-kpi">
        <div class="fin-kpi-label">${label}</div>
        <div class="fin-kpi-value ${cls || ''}">${value}</div>
        ${sub ? `<div class="fin-kpi-sub">${sub}</div>` : ''}
      </div>`;

    return `<div class="fin-kpis">
      ${kpi('Total Invested', _finFmtCompact(s.total_invested), _finFmt(s.total_invested))}
      ${kpi('TT Amt in Line Track', _finFmtCompact(track.in_line), _finFmt(track.in_line))}
      ${kpi('TT Amt in Line Monthly', _finFmtCompact(mon.in_line), _finFmt(mon.in_line))}
      ${kpi('Amt in Hand', _finFmtCompact(s.amt_in_hand), _finFmt(s.amt_in_hand))}
      ${kpi('TT Amt in Line', _finFmtCompact(s.total_in_line), 'Track + Monthly')}
      ${kpi('Total Amount in Line', _finFmtCompact(grand),
            grandMismatch
              ? `<span class="fin-pill warn">! sheet says ${_finFmtCompact(sheetGrand)}</span>`
              : 'TT Amt in Line + In Hand')}
      ${kpi('Total Taken Out', _finFmtCompact(s.taken_out), _finFmt(s.taken_out))}
      ${kpi('Missed Amount', _finFmtCompact(s.missed_amt), _finFmt(s.missed_amt),
            (s.missed_amt || 0) > 0 ? 'is-neg' : '')}
      ${kpi('Cash Tally', tally
            ? `<span class="fin-pill ${tallyOk ? 'ok' : 'bad'}">${tallyOk ? '✓' : '✕'} ${_finEsc(tally)}</span>`
            : '—',
            'Sheet\'s cash count')}
    </div>`;
}

function _finSwitchSub(sub) {
    window._finSubtab = sub;
    document.querySelectorAll('#finSubtabs .fin-subtab').forEach(b =>
        b.classList.toggle('active', b.dataset.sub === sub));

    const d = window._finData;
    const box = document.getElementById('finSubBody');
    if (!d || !box) return;

    if (sub === 'overview') {
        box.innerHTML = `
          <div class="fin-card">
            <div class="fin-card-head">
              <span class="fin-card-title">Monthly Collections</span>
              <span class="fin-card-sub">${d.monthly_series.length} months · highest month highlighted</span>
            </div>
            <div class="fin-chart-box tall"><canvas id="finMonthlyChart"></canvas></div>
            <div class="fin-note">
              The sheet's <strong>Profit</strong> column is exactly 12.5% of Amount Collected
              on every row, so it's shown in the tooltip rather than as a second series —
              as its own line it would be this same shape rescaled.
            </div>
          </div>
          <div class="fin-card">
            <div class="fin-card-head">
              <span class="fin-card-title">Yearly Collections</span>
              <span class="fin-card-sub">${d.yearly_series.length} years · highest year highlighted</span>
            </div>
            <div class="fin-chart-box"><canvas id="finYearlyChart"></canvas></div>
          </div>`;
        // setTimeout, not requestAnimationFrame: rAF is suspended while the
        // page is hidden (backgrounded window), which would leave the canvases
        // permanently blank. Same 50ms layout delay the PCR tab uses.
        setTimeout(() => {
            _finBarChart('finMonthlyChart', 'monthly', d.monthly_series);
            _finBarChart('finYearlyChart', 'yearly', d.yearly_series);
        }, 50);
    } else if (sub === 'track') {
        _finRenderTrack(box, d.track_loans);
    } else if (sub === 'monthly') {
        _finRenderMonthly(box, d.monthly_loans);
    } else if (sub === 'withdraw') {
        _finRenderWithdraw(box, d.withdrawals);
    } else if (sub === 'collections') {
        _finRenderCollections(box, d.collections);
    }
}

// ── Charts (single series, one y-axis) ────────────────────────────

/**
 * Draws the value above the highlighted peak bar. Selective by design —
 * a number on every bar would be noise at 44 points; the peak is the one
 * the eye is looking for, and labelling it means the highlight carries
 * meaning without relying on colour perception.
 */
const _finPeakLabelPlugin = {
    id: 'finPeakLabel',
    afterDatasetsDraw(chart, _args, opts) {
        if (!opts || opts.index == null || opts.index < 0) return;
        const meta = chart.getDatasetMeta(0);
        const bar = meta.data[opts.index];
        if (!bar) return;
        const ctx = chart.ctx;
        ctx.save();
        ctx.font = '700 9px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
        ctx.fillStyle = opts.color;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        // Clamp into the plot area so a tall peak can't clip off the top.
        const y = Math.max(bar.y - 5, chart.chartArea.top + 9);
        ctx.fillText(opts.text, bar.x, y);
        ctx.restore();
    },
};

function _finBarChart(canvasId, key, series) {
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined' || !series.length) return;

    // The previous canvas is already detached by the sub-tab re-render, so a
    // failing destroy() must not stop us rebuilding the new one.
    if (window._finCharts[key]) {
        try { window._finCharts[key].destroy(); } catch (e) { /* detached */ }
        delete window._finCharts[key];
    }

    const accent = _finCssVar('--fin-series-1', '#2a78d6');
    const muted = _finCssVar('--oip-metric-lbl', '#64748b');
    const grid = _finCssVar('--oip-grid', 'rgba(0,0,0,0.06)');
    const surface = _finCssVar('--oip-card-bg', '#ffffff');

    // Emphasis: the single highest month/year gets the stronger step of the
    // same hue. Ties keep the earliest — indexOf on the max — so the
    // highlight never flickers between equal bars on re-render.
    const values = series.map(p => p.collected);
    const peakVal = Math.max(...values.filter(v => v != null));
    const peakIdx = values.indexOf(peakVal);
    const peakColor = _finPeakColor(accent, surface);

    window._finCharts[key] = new Chart(el.getContext('2d'), {
        type: 'bar',
        plugins: [_finPeakLabelPlugin],
        data: {
            labels: series.map(p => p.label),
            datasets: [{
                data: values,
                backgroundColor: values.map((_, i) => i === peakIdx ? peakColor : accent),
                // 4px rounded data-end, anchored to the baseline
                borderRadius: { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 },
                borderSkipped: 'bottom',
                // 2px surface gap between adjacent bars
                borderColor: surface,
                borderWidth: { top: 0, right: 1, bottom: 0, left: 1 },
                maxBarThickness: 34,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            // Chart.js drives its enter-animation off requestAnimationFrame,
            // which browsers suspend on a hidden page — the bars would sit at
            // height 0 until something forced a redraw. Drawing straight to
            // final state is both correct-on-arrival and less noise on a
            // 44-bar dashboard.
            animation: false,
            // Single series — the card title names it, so no legend box.
            plugins: {
                legend: { display: false },
                // Direct-labelling the peak means the emphasis is never
                // carried by colour alone.
                finPeakLabel: {
                    index: peakIdx,
                    text: _finFmtCompact(peakVal),
                    color: peakColor,
                },
                tooltip: {
                    displayColors: false,
                    callbacks: {
                        label: (ctx) => {
                            const row = series[ctx.dataIndex] || {};
                            const out = ['Collected: ' + _finFmt(ctx.parsed.y)];
                            if (row.profit != null) {
                                out.push(`Profit: ${_finFmt(row.profit)}  (12.5% of collected)`);
                            }
                            if (ctx.dataIndex === peakIdx) out.push('★ Highest');
                            return out;
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: muted, font: { size: 9 }, maxRotation: 60, minRotation: 0 },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: grid, drawBorder: false },
                    ticks: { color: muted, font: { size: 9 }, callback: (v) => _finFmtCompact(v) },
                },
            },
        },
    });
}

// ── Tables ────────────────────────────────────────────────────────

/**
 * Status comes straight from the sheet's own 'Is Complpeted' column, on both
 * the Track and Monthly books. Never inferred from the amounts — the owner
 * marks loans complete even when short (closed at a loss), which no
 * amount-based rule would get right.
 */
function _finLoanStatus(r) {
    if (r.is_completed == null) return 'unknown';
    return r.is_completed ? 'completed' : 'in-progress';
}

function _finStatusPill(st) {
    const label = st === 'completed' ? '✓ Completed'
                : st === 'unknown' ? '? Unknown'
                : '● In-progress';
    return DataGrid.badge(label, st === 'completed' ? 'pos' : 'warn');
}

/** Defaults to In-progress: the open book is what needs attention. */
function _finStatusSelect(id) {
    return `<select class="fin-select" id="${id}">
      <option value="both">Both</option>
      <option value="completed">Completed</option>
      <option value="in-progress" selected>In-progress</option>
    </select>`;
}

/**
 * Wire name + optional type + status over whichever loan table is rendered.
 * Scoped to '#finSubBody .dg-table' so it never touches the Pending accordions.
 * Runs once immediately — the status default is In-progress, so the rows and
 * the count must start filtered rather than showing everything.
 */
function _finWireFilters({ nameId, typeId, statusId, countId }) {
    const name = document.getElementById(nameId);
    const type = typeId ? document.getElementById(typeId) : null;
    const status = document.getElementById(statusId);
    const count = document.getElementById(countId);
    if (!name || !status) return;

    const apply = () => {
        const q = name.value.trim().toLowerCase();
        const t = type ? type.value : 'all';
        const s = status.value;
        let shown = 0;
        // Scope to the sub-tab body only. The Pending accordions above it are
        // now .dg-table too, so a bare '.dg-table tbody tr' would also grab (and
        // hide) their rows — the old '.fin-table' selector was safe purely
        // because the accordions never used that class.
        document.querySelectorAll('#finSubBody .dg-table tbody tr').forEach(tr => {
            const hit = (!q || (tr.dataset.name || '').includes(q))
                     && (t === 'all' || tr.dataset.type === t)
                     && (s === 'both' || tr.dataset.status === s);
            tr.style.display = hit ? '' : 'none';
            if (hit) shown++;
        });
        if (count) count.textContent = `${shown} loan${shown === 1 ? '' : 's'}`;
    };
    name.oninput = apply;
    if (type) type.onchange = apply;
    status.onchange = apply;
    apply();
}

// A rupee column. Money owed reads as a loss once negative, which is what the
// grid's `neg` tone already means. `format: v => _finFmt(v)` and not
// `format: _finFmt` — the grid calls format(value, row) and _finFmt's 2nd
// parameter is its dash fallback, so a bare reference makes the row that dash.
const _FIN_AMOUNT = {
    align: 'right',
    format: v => _finFmt(v),
    tone: v => (v != null && v < 0) ? 'neg' : '',
};

// Name / type / status hooks the filter reads. Values are escaped because they
// go straight into an attribute.
const _finRowAttrs = (r, withType) =>
    `data-name="${_finEsc((r.name || '').toLowerCase())}"` +
    (withType ? ` data-type="${_finEsc((r.loan_type || '').toLowerCase())}"` : '') +
    ` data-status="${_finLoanStatus(r)}"`;

function _finRenderTrack(box, rows) {
    box.innerHTML = `<div class="fin-card">
      <div class="fin-filter-row">
        <input type="text" class="fin-input" id="finTrackFilter" placeholder="Filter by name…" autocomplete="off">
        <select class="fin-select" id="finTrackType">
          <option value="all" selected>All</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
        </select>
        ${_finStatusSelect('finTrackStatus')}
        <span class="fin-count" id="finTrackCount"></span>
      </div>
      <div class="fin-table-wrap">${DataGrid.render({
          rows,
          empty: 'No loans in this book.',
          rowAttrs: r => _finRowAttrs(r, true),
          // Column order follows the Monthly table's convention — the dates a
          // loan runs between, then the money against it. The old markup listed
          // the money headers first while the cells emitted the dates first, so
          // "Given Amt"/"To Collect"/"Collected" were printing dates and
          // "Given"/"Start"/"End" were printing rupees. Declaring the header and
          // its cell together is what makes that class of bug unrepresentable.
          columns: [
              { key: 'name', label: 'Name', strong: true },
              { label: 'Missing EMI', align: 'right',
                render: (_, r) => `<span title="Missing EMI / Total EMI, as recorded in the sheet">` +
                    `${DataGrid.escape(r.missing_emi ?? '—')} / ${DataGrid.escape(r.total_emi ?? '—')}</span>` },
              { key: 'remaining_amt', label: 'Rming Amt to colt', ..._FIN_AMOUNT },
              { label: 'Status', render: (_, r) => _finStatusPill(_finLoanStatus(r)) },
              { key: 'loan_type',  label: 'Type' },
              { key: 'given_date', label: 'Given' },
              { key: 'start_date', label: 'Start' },
              { key: 'end_date',   label: 'End' },
              { key: 'given_amt',            label: 'Given Amt',  ..._FIN_AMOUNT },
              { key: 'total_collection_amt', label: 'To Collect', ..._FIN_AMOUNT },
              { key: 'collected',            label: 'Collected',  ..._FIN_AMOUNT },
              { key: 'missing_amt',          label: 'Missing',    ..._FIN_AMOUNT },
              { key: 'percentage', label: 'Rate' },
          ],
      })}</div>
    </div>`;
    _finWireFilters({
        nameId: 'finTrackFilter',
        typeId: 'finTrackType',
        statusId: 'finTrackStatus',
        countId: 'finTrackCount',
    });
}

function _finRenderMonthly(box, rows) {
    // No Type dropdown here: every row in this book is a monthly
    // interest-only loan, so the control would have one option.
    box.innerHTML = `<div class="fin-card">
      <div class="fin-filter-row">
        <input type="text" class="fin-input" id="finMonFilter" placeholder="Filter by name…" autocomplete="off">
        ${_finStatusSelect('finMonStatus')}
        <span class="fin-count" id="finMonCount"></span>
      </div>
      <div class="fin-table-wrap">${DataGrid.render({
          rows,
          empty: 'No monthly loans.',
          // The grid has no zebra, so the red wash is the only thing tinting a
          // row and can never be misread as striping.
          rowClass: r => _finIsPastEnd(r.end_date) ? 'is-overdue' : '',
          rowAttrs: r => _finRowAttrs(r, false) +
              (_finIsPastEnd(r.end_date) ? ' title="End date has passed"' : ''),
          columns: [
              { key: 'name', label: 'Name', strong: true },
              { key: 'remaining_amt', label: 'Rming Amt to colt', ..._FIN_AMOUNT },
              { label: 'Status', render: (_, r) => _finStatusPill(_finLoanStatus(r)) },
              { key: 'contact',    label: 'Contact' },
              { key: 'given_date', label: 'Given' },
              { key: 'end_date',   label: 'End' },
              { key: 'given_amt',            label: 'Given Amt',  ..._FIN_AMOUNT },
              { key: 'total_collection_amt', label: 'To Collect', ..._FIN_AMOUNT },
              { key: 'collected',            label: 'Collected',  ..._FIN_AMOUNT },
              { key: 'missing_amt',          label: 'Missing',    ..._FIN_AMOUNT },
              { key: 'inst_amt',             label: 'Inst Amt',   ..._FIN_AMOUNT },
              { key: 'percentage', label: 'Rate' },
          ],
      })}</div>
    </div>`;
    _finWireFilters({
        nameId: 'finMonFilter',
        statusId: 'finMonStatus',
        countId: 'finMonCount',
    });
}

function _finRenderWithdraw(box, rows) {
    // Reason options are built from the data, so new reasons appear on their
    // own. Grouped case-insensitively — the sheet holds both 'School fees'
    // and 'school fees', which are one reason, not two.
    const groups = new Map();  // lowercase key -> first-seen spelling
    rows.forEach(r => {
        const raw = (r.reason || '').trim();
        if (raw && !groups.has(raw.toLowerCase())) groups.set(raw.toLowerCase(), raw);
    });
    const counts = new Map();
    rows.forEach(r => {
        const k = (r.reason || '').trim().toLowerCase();
        if (k) counts.set(k, (counts.get(k) || 0) + 1);
    });
    const opts = [...groups.entries()]
        .sort((a, b) => a[1].localeCompare(b[1]))
        .map(([k, label]) =>
            `<option value="${_finEsc(k)}">${_finEsc(label)} (${counts.get(k)})</option>`)
        .join('');
    // 40 of 57 rows have no reason — without an explicit option for them,
    // most of the table would be unreachable from this filter.
    const blanks = rows.filter(r => !(r.reason || '').trim()).length;

    box.innerHTML = `<div class="fin-card">
      <div class="fin-filter-row">
        <select class="fin-select" id="finWdReason">
          <option value="all" selected>All reasons</option>
          ${blanks ? `<option value="__none__">(No reason) (${blanks})</option>` : ''}
          ${opts}
        </select>
        <select class="fin-select" id="finWdSort">
          <option value="newest" selected>Date: newest first</option>
          <option value="oldest">Date: oldest first</option>
        </select>
        <span class="fin-count" id="finWdCount"></span>
      </div>
      <div class="fin-table-wrap" id="finWdTable"></div>
    </div>`;

    // Re-rendered rather than show/hidden: sorting has to reorder the rows,
    // which display toggling can't do.
    const draw = () => {
        const reason = document.getElementById('finWdReason').value;
        const dir = document.getElementById('finWdSort').value;

        const list = rows.filter(r => {
            const raw = (r.reason || '').trim();
            if (reason === 'all') return true;
            if (reason === '__none__') return !raw;
            return raw.toLowerCase() === reason;
        }).sort((a, b) => {
            const da = _finParseDMY(a.date), db = _finParseDMY(b.date);
            // Unparseable dates sink to the bottom either way rather than
            // scrambling the order around them.
            if (!da && !db) return 0;
            if (!da) return 1;
            if (!db) return -1;
            return dir === 'newest' ? db - da : da - db;
        });

        // Whole grid, not just the rows: sorting reorders them, and the header
        // is cheap to reprint alongside.
        document.getElementById('finWdTable').innerHTML = DataGrid.render({
            rows: list,
            empty: 'No withdrawals match this filter.',
            columns: [
                { key: 'date', label: 'Date' },
                { key: 'amount', label: 'Amount', ..._FIN_AMOUNT },
                { key: 'reason', label: 'Reason',
                  render: v => (v && String(v).trim())
                      ? DataGrid.escape(v) : '<span style="opacity:.45">—</span>' },
            ],
        });

        const total = list.reduce((a, r) => a + (r.amount || 0), 0);
        document.getElementById('finWdCount').textContent =
            `${list.length} of ${rows.length} · ${_finFmt(total)}`;
    };
    document.getElementById('finWdReason').onchange = draw;
    document.getElementById('finWdSort').onchange = draw;
    draw();
}

/**
 * Collections: one row per loan cycle "block" (name + status + a dated
 * amounts series). The same customer has many blocks over the years, so
 * this is a flat list rather than grouped — the name filter is what narrows
 * it down to one customer's history.
 */
function _finCollStatusBadge(status) {
    const s = (status || '').trim();
    const tone = s.toLowerCase() === 'yes' ? 'pos' : s.toLowerCase() === 'no' ? 'warn' : 'neutral';
    return s ? DataGrid.badge(s, tone) : '—';
}

function _finRenderCollections(box, collections) {
    const blocks = (collections && collections.blocks) || [];
    const skipped = (collections && collections.skipped_blocks) || 0;

    box.innerHTML = `<div class="fin-card">
      <div class="fin-filter-row">
        <input type="text" class="fin-input" id="finCollFilter" placeholder="Filter by customer…" autocomplete="off">
        <select class="fin-select" id="finCollStatus">
          <option value="all" selected>All statuses</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
        <select class="fin-select" id="finCollSort">
          <option value="total-desc" selected>Total: highest first</option>
          <option value="total-asc">Total: lowest first</option>
          <option value="name">Name: A–Z</option>
        </select>
        <span class="fin-count" id="finCollCount"></span>
      </div>
      <div class="fin-table-wrap" id="finCollTable"></div>
      ${skipped ? `<div class="fin-note">${skipped} malformed block${skipped === 1 ? '' : 's'}
          in the Collections sheet ${skipped === 1 ? 'was' : 'were'} skipped rather than shown.</div>` : ''}
    </div>`;

    const draw = () => {
        const q = document.getElementById('finCollFilter').value.trim().toLowerCase();
        const status = document.getElementById('finCollStatus').value;
        const sort = document.getElementById('finCollSort').value;

        const list = blocks.filter(b => {
            const hitName = !q || (b.name || '').toLowerCase().includes(q);
            const hitStatus = status === 'all' || (b.status || '').trim().toLowerCase() === status;
            return hitName && hitStatus;
        }).sort((a, b) => {
            if (sort === 'name') return (a.name || '').localeCompare(b.name || '');
            const diff = (a.total || 0) - (b.total || 0);
            return sort === 'total-asc' ? diff : -diff;
        });

        document.getElementById('finCollTable').innerHTML = DataGrid.render({
            rows: list,
            empty: 'No collection blocks match this filter.',
            columns: [
                { key: 'name', label: 'Name', strong: true },
                { key: 'status', label: 'Status', render: (v) => _finCollStatusBadge(v) },
                { key: 'entry_count', label: 'Entries', align: 'right' },
                { key: 'paid_count', label: 'Paid', align: 'right' },
                { key: 'total', label: 'Total', ..._FIN_AMOUNT },
                { label: 'Last Entry', align: 'right',
                  render: (_, r) => {
                      const last = r.entries && r.entries.length ? r.entries[r.entries.length - 1] : null;
                      return last ? DataGrid.escape(last.date) : '—';
                  } },
            ],
        });

        const total = list.reduce((a, r) => a + (r.total || 0), 0);
        document.getElementById('finCollCount').textContent =
            `${list.length} of ${blocks.length} · ${_finFmt(total)}`;
    };
    document.getElementById('finCollFilter').oninput = draw;
    document.getElementById('finCollStatus').onchange = draw;
    document.getElementById('finCollSort').onchange = draw;
    draw();
}
