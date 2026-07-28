/**
 * ema_touch_all_filter.js
 * Frontend logic for the EMA Touch All scanner tab.
 *   Runs over the fixed futures watchlist (indices + F&O stocks, defined by
 *   TOUCH_ALL_SYMBOLS in filters/ema_rsi_filter.py) and lists the instruments
 *   whose LATEST DAILY CANDLE touches every one of EMA 20 / 50 / 100 / 200 —
 *   i.e. low <= EMA <= high for all four, one bar sweeping the whole EMA stack.
 *   Strict wick containment, no % tolerance.
 *   Status column is the candle's own direction: UP (close >= open) / DOWN.
 *
 * Same background-job contract as the EMA Narrow tab: the first request starts
 * the scan and answers {status:'running', progress:{done,total}}; we poll until
 * {status:'done'} and render partial results as they stream in.
 */

'use strict';

let _touchAllInFlight = false;
let _touchAllLastAt = 0;
let _touchAllInitDone = false;
let _touchAllPollTimer = null;

const TOUCH_ALL_MIN_GAP_MS = 60 * 1000; // hard throttle: 1 req/min (user actions)
const TOUCH_ALL_POLL_MS    = 15 * 1000; // status poll while a scan is running

const TOUCH_ALL_DIR_LABEL = { BUY: 'BUY Only', SELL: 'Sell Only', BOTH: 'Both' };

// Last payload rendered, kept so the Show filter can re-render without
// re-hitting the scan endpoint — every row already carries its `allowed` flag.
let _touchAllRows = [];

// ─── Bootstrap (called lazily on first tab open) ─────────────────────────────
function initEmaTouchAllOnce() {
    if (_touchAllInitDone) return;
    _touchAllInitDone = true;

    const datePicker = document.getElementById('emaTouchAllDateFilter');
    if (datePicker) {
        datePicker.value = new Date().toISOString().split('T')[0];
        datePicker.addEventListener('change', () => { _touchAllLastAt = 0; loadEmaTouchAllData(); });
    }

    const dirFilter = document.getElementById('emaTouchAllDirFilter');
    if (dirFilter) {
        // Pure re-render of rows already in hand — no scan, no throttle.
        dirFilter.addEventListener('change', () => renderTouchAllTable(_touchAllRows));
    }

    const btn = document.getElementById('ema-touch-all-refresh-btn');
    if (btn) {
        // Manual refresh forces a fresh scan (bypasses the 6h server cache)
        btn.addEventListener('click', () => { _touchAllLastAt = 0; loadEmaTouchAllData(true); });
    }

    // Click-to-sort is wired by DataGrid.mountSortable itself, on first render.

    loadEmaTouchAllData();
}

// ─── Data Fetch ───────────────────────────────────────────────────────────────
async function loadEmaTouchAllData(forceRefresh = false, isPoll = false) {
    const now = Date.now();
    if (_touchAllInFlight) return;
    if (!isPoll && now - _touchAllLastAt < TOUCH_ALL_MIN_GAP_MS) return;

    _touchAllInFlight = true;
    if (!isPoll) _touchAllLastAt = now;

    if (_touchAllPollTimer) { clearTimeout(_touchAllPollTimer); _touchAllPollTimer = null; }

    const btn = document.getElementById('ema-touch-all-refresh-btn');
    if (btn) btn.disabled = true;

    const date = document.getElementById('emaTouchAllDateFilter')?.value || '';

    let url = '/api/ema-touch-all-filter?';
    if (date) url += `date=${date}&`;
    if (forceRefresh) url += 'refresh=1';

    const container = document.getElementById('emaTouchAllResults');
    if (container) container.classList.remove('results-hidden');

    if (!isPoll) {
        renderTouchAllProgress(null); // generic "starting" line
        const countEl = document.getElementById('emaTouchAllCount');
        if (countEl) countEl.textContent = '(...)';
        const emptyState = document.getElementById('ema-touch-all-empty-state');
        if (emptyState) emptyState.classList.add('ema-hidden');
        const footnote = document.getElementById('emaTouchAllFootnote');
        if (footnote) footnote.innerHTML = '';
    }

    try {
        const response = await fetchJson(url);

        if (response && response.status === 'running') {
            const partial = response.results || [];
            if (partial.length > 0) {
                renderTouchAllTable(partial);
                const countEl = document.getElementById('emaTouchAllCount');
                if (countEl) countEl.textContent = `(${partial.length} so far…)`;
            } else {
                const grid = document.getElementById('emaTouchAllGrid');
                if (grid) grid.innerHTML = '';
            }
            renderTouchAllProgress(response.progress);
            _touchAllPollTimer = setTimeout(() => loadEmaTouchAllData(false, true), TOUCH_ALL_POLL_MS);
        } else if (response && (response.success || response.results)) {
            const results = response.results || [];

            clearTouchAllProgress();
            renderTouchAllTable(results);
            renderTouchAllFootnote(response);

            const emptyState = document.getElementById('ema-touch-all-empty-state');
            if (emptyState) emptyState.classList.toggle('ema-hidden', results.length > 0);
        } else if (response && !response.needs_login) {
            renderTouchAllError(response.message || response.error || 'Unknown error');
        }
    } catch (err) {
        console.error('EMA Touch All fetch error:', err);
        renderTouchAllError(err.message);
    } finally {
        _touchAllInFlight = false;
        if (btn) btn.disabled = false;
    }
}

function _touchAllProgressDetail(progress) {
    if (progress && progress.total > 0) {
        const pct = Math.round(progress.done / progress.total * 100);
        return `${progress.done} / ${progress.total} stocks scanned (${pct}%)`;
    }
    return 'Starting scan...';
}

// Progress lives in its own slot beside the grid — DataGrid.mountSortable owns
// the row list exclusively, so this never splices a fake row into it.
function renderTouchAllProgress(progress) {
    const el = document.getElementById('emaTouchAllLoader');
    if (!el) return;
    el.innerHTML = `<div class="cpr-grid-status">
        <span class="cpr-grid-spinner"></span>
        🎯 Scanning all NSE equity stocks for a daily candle touching EMA 20/50/100/200 —
        ${DataGrid.escape(_touchAllProgressDetail(progress))}
    </div>`;
}
function clearTouchAllProgress() {
    const el = document.getElementById('emaTouchAllLoader');
    if (el) el.innerHTML = '';
}

// How much of the fixed watchlist actually got scanned. A symbol the broker
// can't resolve (renamed/delisted ticker) or one without ~200 daily bars is
// reported here, so a typo in the list doesn't quietly read as "no match".
function renderTouchAllFootnote(response) {
    const el = document.getElementById('emaTouchAllFootnote');
    if (!el) return;

    const scanned = response.scanned;
    const total   = response.total;
    const skipped = response.skipped || [];

    if (scanned == null || !total) { el.innerHTML = ''; return; }

    let html = `Scanned ${scanned} of ${total} watchlist symbols.`;
    if (skipped.length) {
        html += ` <span class="dg-warn">Skipped ${skipped.length}</span>` +
                ` (no data or not enough history): ` +
                `${DataGrid.escape(skipped.join(', '))}`;
    }
    el.innerHTML = `<div class="touch-all-footnote">${html}</div>`;
}

// ─── Render ───────────────────────────────────────────────────────────────────
// An EMA column shows the value and marks whether this candle actually
// contained it — the whole point of the scan, visible per EMA.
function _touchAllEmaColumn(period) {
    const key = `ema${period}`;
    return {
        key, label: `EMA ${period}`, sortable: true, align: 'right',
        render: (v, row) => {
            if (v == null) return '—';
            const hit = row.touched?.[String(period)];
            const cls = hit ? 'trigger-ema' : 'dist-pct';
            return `<span class="${cls}" title="${hit ? 'Inside candle range' : 'Outside candle range'}">` +
                   `${Number(v).toFixed(2)}</span>`;
        },
    };
}

function _touchAllColumns() {
    return [
        { key: 'symbol', label: 'Symbol', sortable: true, strong: true,
          render: (symbol) => `<a href="https://in.tradingview.com/chart/?symbol=NSE:` +
              `${encodeURIComponent(symbol)}" target="_blank" rel="noopener noreferrer" ` +
              `class="symbol-link">${DataGrid.escape(symbol)}</a>` },
        { key: 'status', label: 'Status', sortable: true, strong: true,
          format: v => v === 'UP' ? '⬆ UP' : '⬇ DOWN',
          badge:  v => v === 'UP' ? 'pos' : 'neg' },
        // The candle direction IS the signal; `direction` is what the symbol is
        // configured to take, so a row can be a match yet not be actionable.
        { key: 'direction', label: 'Allowed Dir.', sortable: true,
          format: v => TOUCH_ALL_DIR_LABEL[v] || v || '—',
          badge:  v => v === 'BOTH' ? 'info' : (v === 'BUY' ? 'pos' : 'neg') },
        { key: 'signal', label: 'Signal', sortable: true, strong: true,
          sortValue: row => `${row.allowed ? 0 : 1}${row.signal}`,
          render: (v, row) => {
            if (!v) return '—';
            const cls = row.allowed ? (v === 'BUY' ? 'trigger-ema' : 'trigger-rsi') : 'dg-muted';
            const mark = row.allowed ? '' : ' 🚫';
            const tip = row.allowed
                ? `Actionable — ${DataGrid.escape(v)} is allowed for this symbol`
                : `Not actionable — this symbol is configured ${DataGrid.escape(row.direction)} only`;
            return `<span class="${cls}" title="${tip}">${DataGrid.escape(v)}${mark}</span>`;
          } },
        { key: 'target_pct', label: 'Target %', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) + '%' : '—' },
        { key: 'target_price', label: 'Target', sortable: true, align: 'right',
          cellClass: 'ema-close',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'current_price', label: 'Price', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'open', label: 'Open', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'high', label: 'High', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'low', label: 'Low', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'close', label: 'Close', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'change_pct', label: 'Change %', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) + '%' : '—',
          tone:   v => v == null ? 'muted' : (v >= 0 ? 'pos' : 'neg') },
        _touchAllEmaColumn(20),
        _touchAllEmaColumn(50),
        _touchAllEmaColumn(100),
        _touchAllEmaColumn(200),
        { key: 'range_pct', label: 'Candle Range %', sortable: true, align: 'right',
          cellClass: 'dist-pct', format: v => v != null ? Number(v).toFixed(2) + '%' : '—' },
        { key: 'candle_date', label: 'Candle Date', sortable: true },
    ];
}

// "Allowed only" (default) keeps just the matches whose signal direction the
// symbol is actually configured to trade; "All" shows every match with the
// blocked ones greyed out. Filtering is client-side — the scan payload already
// carries `allowed` per row, so toggling never re-runs the scan.
function _touchAllVisibleRows(results) {
    const mode = document.getElementById('emaTouchAllDirFilter')?.value || 'allowed';
    return mode === 'all' ? results : results.filter(r => r.allowed);
}

function renderTouchAllTable(results) {
    const grid      = document.getElementById('emaTouchAllGrid');
    const container = document.getElementById('emaTouchAllResults');
    const countEl   = document.getElementById('emaTouchAllCount');
    if (!grid || !container || !countEl) return;

    _touchAllRows = results;
    const rows = _touchAllVisibleRows(results);
    const hidden = results.length - rows.length;

    countEl.textContent = hidden > 0
        ? `(${rows.length} of ${results.length})`
        : `(${rows.length})`;

    if (rows.length === 0) {
        grid.innerHTML = '';
        container.classList.add('results-hidden');
        return;
    }

    container.classList.remove('results-hidden');
    DataGrid.mountSortable(grid, {
        rows,
        columns: _touchAllColumns(),
        empty: 'No matches.',
        defaultSort: { key: 'range_pct', dir: 'desc' },
        rowClass: row => row.allowed ? '' : 'touch-all-blocked',
    });
}

function renderTouchAllError(msg) {
    clearTouchAllProgress();
    const footnote = document.getElementById('emaTouchAllFootnote');
    if (footnote) footnote.innerHTML = '';
    const grid = document.getElementById('emaTouchAllGrid');
    if (grid) {
        grid.innerHTML = `<div class="cpr-grid-status cpr-grid-status--error">` +
            `❌ Failed to load EMA Touch All data: ${DataGrid.escape(msg)}</div>`;
    }
    const countEl = document.getElementById('emaTouchAllCount');
    if (countEl) countEl.textContent = '(0)';
}
