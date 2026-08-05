/**
 * candlestick_filter.js
 * Frontend logic for the Candlesticks scanner tab.
 *   Runs over the fixed futures watchlist (indices + F&O stocks, defined by
 *   TOUCH_ALL_CONFIG in filters/ema_rsi_filter.py) and lists the instruments
 *   whose latest candle on the selected timeframe (Daily by default, Weekly or
 *   Monthly) engulfs the previous candle's body:
 *     Bullish Engulfing — down candle followed by a bigger up candle
 *     Bearish Engulfing — up candle followed by a bigger down candle
 *
 * Same background-job contract as the EMA Narrow / EMA Touch All tabs: the
 * first request starts the scan and answers {status:'running',
 * progress:{done,total}}; we poll until {status:'done'} and render partial
 * results as they stream in.
 */

'use strict';

let _candleInFlight = false;
let _candleLastAt = 0;
let _candleInitDone = false;
let _candlePollTimer = null;

const CANDLE_MIN_GAP_MS = 60 * 1000; // hard throttle: 1 req/min (user actions)
const CANDLE_POLL_MS    = 15 * 1000; // status poll while a scan is running

const CANDLE_TF_LABEL = { daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly' };

// Last payload rendered, kept so the Pattern dropdown can re-render without
// re-hitting the scan endpoint — every row already carries its `pattern`.
let _candleRows = [];

// ─── Bootstrap (called lazily on first tab open) ─────────────────────────────
function initCandlestickOnce() {
    if (_candleInitDone) return;
    _candleInitDone = true;

    const datePicker = document.getElementById('candleDateFilter');
    if (datePicker) {
        datePicker.value = new Date().toISOString().split('T')[0];
        datePicker.addEventListener('change', () => { _candleLastAt = 0; loadCandlestickData(); });
    }

    // Timeframe changes the scan itself (weekly/monthly are resampled server
    // side), so it re-runs; the throttle is reset so the change is immediate.
    const tf = document.getElementById('candleTimeframe');
    if (tf) {
        tf.addEventListener('change', () => {
            _candleLastAt = 0;
            updateCandleSectionLabel();
            loadCandlestickData();
        });
    }

    // Pattern is a pure re-render of rows already in hand — no scan, no throttle.
    const pattern = document.getElementById('candlePattern');
    if (pattern) {
        pattern.addEventListener('change', () => renderCandleTable(_candleRows));
    }

    const btn = document.getElementById('candlestick-refresh-btn');
    if (btn) {
        // Manual refresh forces a fresh scan (bypasses the 6h server cache)
        btn.addEventListener('click', () => { _candleLastAt = 0; loadCandlestickData(true); });
    }

    updateCandleSectionLabel();
    loadCandlestickData();
}

function _candleTimeframe() {
    return document.getElementById('candleTimeframe')?.value || 'daily';
}

function updateCandleSectionLabel() {
    const label = CANDLE_TF_LABEL[_candleTimeframe()] || 'Daily';
    const badge = document.getElementById('candle-tf-badge');
    if (badge) badge.textContent = `📆 ${label}`;
    const title = document.getElementById('candleCardTitle');
    if (title) title.textContent = `🕯️ ${label} Engulfing Candles`;
}

// ─── Data Fetch ───────────────────────────────────────────────────────────────
async function loadCandlestickData(forceRefresh = false, isPoll = false) {
    const now = Date.now();
    if (_candleInFlight) return;
    if (!isPoll && now - _candleLastAt < CANDLE_MIN_GAP_MS) return;

    _candleInFlight = true;
    if (!isPoll) _candleLastAt = now;

    if (_candlePollTimer) { clearTimeout(_candlePollTimer); _candlePollTimer = null; }

    const btn = document.getElementById('candlestick-refresh-btn');
    if (btn) btn.disabled = true;

    const date = document.getElementById('candleDateFilter')?.value || '';

    let url = `/api/candlestick-filter?timeframe=${encodeURIComponent(_candleTimeframe())}&`;
    if (date) url += `date=${date}&`;
    if (forceRefresh) url += 'refresh=1';

    const container = document.getElementById('candlestickResults');
    if (container) container.classList.remove('results-hidden');

    if (!isPoll) {
        renderCandleProgress(null); // generic "starting" line
        const countEl = document.getElementById('candlestickCount');
        if (countEl) countEl.textContent = '(...)';
        const emptyState = document.getElementById('candlestick-empty-state');
        if (emptyState) emptyState.classList.add('ema-hidden');
        const footnote = document.getElementById('candlestickFootnote');
        if (footnote) footnote.innerHTML = '';
    }

    try {
        const response = await fetchJson(url);

        if (response && response.status === 'running') {
            const partial = response.results || [];
            if (partial.length > 0) {
                renderCandleTable(partial);
                const countEl = document.getElementById('candlestickCount');
                if (countEl) countEl.textContent = `(${partial.length} so far…)`;
            } else {
                const grid = document.getElementById('candlestickGrid');
                if (grid) grid.innerHTML = '';
            }
            renderCandleProgress(response.progress);
            _candlePollTimer = setTimeout(() => loadCandlestickData(false, true), CANDLE_POLL_MS);
        } else if (response && (response.success || response.results)) {
            const results = response.results || [];

            clearCandleProgress();
            renderCandleTable(results);
            renderCandleFootnote(response);

            const emptyState = document.getElementById('candlestick-empty-state');
            if (emptyState) emptyState.classList.toggle('ema-hidden', results.length > 0);
        } else if (response && !response.needs_login) {
            renderCandleError(response.message || response.error || 'Unknown error');
        }
    } catch (err) {
        console.error('Candlestick fetch error:', err);
        renderCandleError(err.message);
    } finally {
        _candleInFlight = false;
        if (btn) btn.disabled = false;
    }
}

function _candleProgressDetail(progress) {
    if (progress && progress.total > 0) {
        const pct = Math.round(progress.done / progress.total * 100);
        return `${progress.done} / ${progress.total} stocks scanned (${pct}%)`;
    }
    return 'Starting scan...';
}

// Progress lives in its own slot beside the grid — DataGrid.mountSortable owns
// the row list exclusively, so this never splices a fake row into it.
function renderCandleProgress(progress) {
    const el = document.getElementById('candlestickLoader');
    if (!el) return;
    const label = CANDLE_TF_LABEL[_candleTimeframe()] || 'Daily';
    el.innerHTML = `<div class="cpr-grid-status">
        <span class="cpr-grid-spinner"></span>
        🕯️ Scanning the futures watchlist for ${DataGrid.escape(label.toLowerCase())}
        engulfing candles — ${DataGrid.escape(_candleProgressDetail(progress))}
    </div>`;
}
function clearCandleProgress() {
    const el = document.getElementById('candlestickLoader');
    if (el) el.innerHTML = '';
}

// How much of the fixed watchlist actually got scanned. A symbol the broker
// can't resolve (renamed/delisted ticker) or one without two candles on the
// timeframe is reported here, so a typo doesn't quietly read as "no match".
function renderCandleFootnote(response) {
    const el = document.getElementById('candlestickFootnote');
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
function _candleColumns() {
    return [
        { key: 'symbol', label: 'Symbol', sortable: true, strong: true,
          render: (symbol) => `<a href="https://in.tradingview.com/chart/?symbol=NSE:` +
              `${encodeURIComponent(symbol)}" target="_blank" rel="noopener noreferrer" ` +
              `class="symbol-link">${DataGrid.escape(symbol)}</a>` },
        { key: 'pattern_label', label: 'Pattern', sortable: true, strong: true,
          format: v => v ? (v.startsWith('Bullish') ? '🟢 ' + v : '🔴 ' + v) : '—',
          badge:  (v, row) => row.pattern === 'bullish_engulfing' ? 'pos' : 'neg' },
        { key: 'signal', label: 'Signal', sortable: true, strong: true,
          format: v => v || '—',
          badge:  v => v === 'BUY' ? 'pos' : 'neg' },
        { key: 'current_price', label: 'Price', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'open', label: 'Open', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'high', label: 'High', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'low', label: 'Low', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'close', label: 'Close', sortable: true, align: 'right',
          cellClass: 'ema-close',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'change_pct', label: 'Change %', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) + '%' : '—',
          tone:   v => v == null ? 'muted' : (v >= 0 ? 'pos' : 'neg') },
        // The engulfed candle, so the pattern can be eyeballed without a chart.
        { key: 'prev_open', label: 'Prev Open', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'prev_close', label: 'Prev Close', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        // How much bigger this body is than the one it swallowed — the strength
        // of the pattern, and what the grid opens sorted on.
        { key: 'engulf_ratio', label: 'Engulf ×', sortable: true, align: 'right',
          strong: true, format: v => v != null ? Number(v).toFixed(2) + '×' : '—' },
        { key: 'body_pct', label: 'Body %', sortable: true, align: 'right',
          cellClass: 'dist-pct', format: v => v != null ? Number(v).toFixed(2) + '%' : '—' },
        { key: 'range_pct', label: 'Candle Range %', sortable: true, align: 'right',
          cellClass: 'dist-pct', format: v => v != null ? Number(v).toFixed(2) + '%' : '—' },
        { key: 'volume_ratio', label: 'Vol ×', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) + '×' : '—' },
        { key: 'candle_date', label: 'Candle Date', sortable: true },
    ];
}

// Pattern dropdown: "all" (default) shows both, or just one of them. Filtering
// is client-side — the scan payload carries `pattern` per row, so switching
// never re-runs the scan.
function _candleVisibleRows(results) {
    const mode = document.getElementById('candlePattern')?.value || 'all';
    return mode === 'all' ? results : results.filter(r => r.pattern === mode);
}

function renderCandleTable(results) {
    const grid      = document.getElementById('candlestickGrid');
    const container = document.getElementById('candlestickResults');
    const countEl   = document.getElementById('candlestickCount');
    if (!grid || !container || !countEl) return;

    _candleRows = results;
    const rows = _candleVisibleRows(results);
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
        columns: _candleColumns(),
        empty: 'No matches.',
        defaultSort: { key: 'engulf_ratio', dir: 'desc' },
    });
}

function renderCandleError(msg) {
    clearCandleProgress();
    const footnote = document.getElementById('candlestickFootnote');
    if (footnote) footnote.innerHTML = '';
    const grid = document.getElementById('candlestickGrid');
    if (grid) {
        grid.innerHTML = `<div class="cpr-grid-status cpr-grid-status--error">` +
            `❌ Failed to load Candlestick data: ${DataGrid.escape(msg)}</div>`;
    }
    const countEl = document.getElementById('candlestickCount');
    if (countEl) countEl.textContent = '(0)';
}
