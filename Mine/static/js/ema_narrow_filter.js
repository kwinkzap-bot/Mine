/**
 * ema_narrow_filter.js
 * Frontend logic for the EMA Narrow scanner tab.
 *   Lists ALL NSE equity stocks where EMA 20 / 50 / 100 / 200 are compressed within a tight
 *   % band of price on EVERY timeframe of the selected group:
 *     mwd = Month + Week + Day (default), mw = Month + Week, wd = Week + Day.
 *   Spread % per timeframe = (maxEMA - minEMA) / close * 100.
 */

'use strict';

let _narrowInFlight = false;
let _narrowLastAt = 0;
let _narrowInitDone = false;
let _narrowPollTimer = null;

const NARROW_MIN_GAP_MS  = 60 * 1000; // hard throttle: 1 req/min (user actions)
const NARROW_POLL_MS     = 15 * 1000; // status poll while a scan is running

// ─── Bootstrap (called lazily on first tab open) ─────────────────────────────
function initEmaNarrowOnce() {
    if (_narrowInitDone) return;
    _narrowInitDone = true;

    const datePicker = document.getElementById('emaNarrowDateFilter');
    if (datePicker) {
        datePicker.value = new Date().toISOString().split('T')[0];
        datePicker.addEventListener('change', () => { _narrowLastAt = 0; loadEmaNarrowData(); });
    }

    const groupPicker = document.getElementById('emaNarrowGroup');
    if (groupPicker) {
        groupPicker.addEventListener('change', () => {
            _narrowLastAt = 0;
            updateNarrowBadge(groupPicker.value);
            loadEmaNarrowData();
        });
        updateNarrowBadge(groupPicker.value);
    }

    const thPicker = document.getElementById('emaNarrowThreshold');
    if (thPicker) {
        thPicker.addEventListener('change', () => { _narrowLastAt = 0; loadEmaNarrowData(); });
    }

    const emaSetPicker = document.getElementById('emaNarrowEmaSet');
    if (emaSetPicker) {
        emaSetPicker.addEventListener('change', () => {
            _narrowLastAt = 0;
            updateNarrowSectionTitle(emaSetPicker.value);
            loadEmaNarrowData();
        });
        updateNarrowSectionTitle(emaSetPicker.value);
    }

    const btn = document.getElementById('ema-narrow-refresh-btn');
    if (btn) {
        // Manual refresh forces a fresh scan (bypasses the 6h server cache)
        btn.addEventListener('click', () => { _narrowLastAt = 0; loadEmaNarrowData(true); });
    }

    // Click-to-sort is wired by DataGrid.mountSortable itself, on first render.

    loadEmaNarrowData();
}

// ─── Data Fetch ───────────────────────────────────────────────────────────────
// The backend runs the scan as a background job: first call answers
// {status:'running', progress:{done,total}} and we poll until {status:'done'}.
async function loadEmaNarrowData(forceRefresh = false, isPoll = false) {
    const now = Date.now();
    if (_narrowInFlight) return;
    if (!isPoll && now - _narrowLastAt < NARROW_MIN_GAP_MS) return;

    _narrowInFlight = true;
    if (!isPoll) _narrowLastAt = now;

    if (_narrowPollTimer) { clearTimeout(_narrowPollTimer); _narrowPollTimer = null; }

    const btn = document.getElementById('ema-narrow-refresh-btn');
    if (btn) btn.disabled = true;

    const group     = document.getElementById('emaNarrowGroup')?.value || 'wd';
    const threshold = document.getElementById('emaNarrowThreshold')?.value || '3';
    const emaSet    = document.getElementById('emaNarrowEmaSet')?.value || '20_50_100';
    const date      = document.getElementById('emaNarrowDateFilter')?.value || '';

    const thLabel = document.getElementById('emaNarrowThresholdLabel');
    if (thLabel) thLabel.textContent = `${threshold}%`;

    let url = `/api/ema-narrow-filter?group=${group}&threshold=${threshold}&emas=${emaSet}`;
    if (date) url += `&date=${date}`;
    if (forceRefresh) url += `&refresh=1`;

    const container = document.getElementById('emaNarrowResults');
    if (container) container.classList.remove('results-hidden');

    if (!isPoll) {
        renderNarrowProgress(null); // generic "starting" row
        const countEl = document.getElementById('emaNarrowCount');
        if (countEl) countEl.textContent = '(...)';
        const nearestSection = document.getElementById('emaNarrowNearestSection');
        if (nearestSection) nearestSection.classList.add('ema-hidden');
        const emptyState = document.getElementById('ema-narrow-empty-state');
        if (emptyState) emptyState.classList.add('ema-hidden');
    }

    try {
        const response = await fetchJson(url);

        if (response && response.status === 'running') {
            // Scan in progress — render whatever has been found so far, show
            // the progress line below the grid (its own slot now, not a table
            // row), then poll again shortly.
            const partial = response.results || [];
            if (partial.length > 0) {
                renderNarrowTable(partial);
                const countEl = document.getElementById('emaNarrowCount');
                if (countEl) countEl.textContent = `(${partial.length} so far…)`;
            } else {
                const grid = document.getElementById('emaNarrowGrid');
                if (grid) grid.innerHTML = '';
            }
            renderNarrowProgress(response.progress);
            _narrowPollTimer = setTimeout(() => loadEmaNarrowData(false, true), NARROW_POLL_MS);
        } else if (response && (response.success || response.results)) {
            const results = response.results || [];
            const nearest = response.nearest || [];

            clearNarrowProgress();
            renderNarrowTable(results);
            renderNarrowNearest(nearest, results.length === 0);

            const emptyState = document.getElementById('ema-narrow-empty-state');
            if (emptyState) {
                emptyState.classList.toggle('ema-hidden', results.length > 0 || nearest.length > 0);
            }
        } else if (response && !response.needs_login) {
            renderNarrowError(response.message || response.error || 'Unknown error');
        }
    } catch (err) {
        console.error('EMA Narrow fetch error:', err);
        renderNarrowError(err.message);
    } finally {
        _narrowInFlight = false;
        if (btn) btn.disabled = false;
    }
}

function _narrowProgressDetail(progress) {
    if (progress && progress.total > 0) {
        const pct = Math.round(progress.done / progress.total * 100);
        return `${progress.done} / ${progress.total} stocks scanned (${pct}%)`;
    }
    return 'Starting scan...';
}

// Progress lives in its own slot (#emaNarrowLoader) beside the grid now,
// rather than as a fake <tr> spliced into — and pinned at the bottom of —
// the row list on every sort. DataGrid.mountSortable owns the row list
// exclusively; this never touches it.
function renderNarrowProgress(progress) {
    const el = document.getElementById('emaNarrowLoader');
    if (!el) return;
    el.innerHTML = `<div class="cpr-grid-status">
        <span class="cpr-grid-spinner"></span>
        🧲 Scanning all NSE equity stocks for EMA compression — ${DataGrid.escape(_narrowProgressDetail(progress))}
    </div>`;
}
function clearNarrowProgress() {
    const el = document.getElementById('emaNarrowLoader');
    if (el) el.innerHTML = '';
}

// ─── Render ───────────────────────────────────────────────────────────────────
function _narrowEmaTitle(stock, tf) {
    const d = stock.tfs?.[tf];
    if (!d) return '';
    return [20, 50, 100, 200]
        .filter(p => d[`ema${p}`] != null)
        .map(p => `EMA${p} ${Number(d[`ema${p}`]).toFixed(2)}`)
        .join(' | ');
}

function _narrowZoneLabel(stock) {
    const anchorTf = stock.tfs?.daily || stock.tfs?.weekly || stock.tfs?.monthly || {};
    const anchorEma = anchorTf.ema200 ?? anchorTf.ema100 ?? 0;
    if (stock.price_inside) return 'Inside';
    return stock.close > anchorEma ? 'Above' : 'Below';
}
function _narrowZoneHtml(stock) {
    const label = _narrowZoneLabel(stock);
    if (label === 'Inside') return '<span class="trigger-both">🎯 Inside</span>';
    return label === 'Above'
        ? '<span class="trigger-ema">⬆ Above</span>'
        : '<span class="trigger-rsi">⬇ Below</span>';
}

// A spread column reads {daily,weekly,monthly}, with the underlying per-EMA
// breakdown as its hover title — same shape, three timeframes.
function _narrowSpreadColumn(tf, key, label) {
    return {
        key, label, sortable: true, align: 'right',
        render: (v, row) => v == null ? '—' :
            `<span class="dist-pct" title="${DataGrid.escape(_narrowEmaTitle(row, tf))}">` +
            `${Number(v).toFixed(2)}%</span>`,
    };
}

function _narrowColumns(withZone) {
    const cols = [
        { key: 'symbol', label: 'Symbol', sortable: true, strong: true,
          render: (symbol) => `<a href="https://in.tradingview.com/chart/?symbol=NSE:` +
              `${encodeURIComponent(symbol)}" target="_blank" rel="noopener noreferrer" ` +
              `class="symbol-link">${DataGrid.escape(symbol)}</a>` },
        { key: 'current_price', label: 'Price', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        { key: 'close', label: 'Close', sortable: true, align: 'right',
          format: v => v != null ? Number(v).toFixed(2) : '—' },
        _narrowSpreadColumn('daily', 'spread_daily', 'Day Spread %'),
        _narrowSpreadColumn('weekly', 'spread_weekly', 'Week Spread %'),
        _narrowSpreadColumn('monthly', 'spread_monthly', 'Month Spread %'),
        { key: 'max_spread_pct', label: 'Max Spread %', sortable: true, align: 'right',
          cellClass: 'dist-pct', format: v => v != null ? Number(v).toFixed(2) + '%' : '—' },
    ];
    if (withZone) {
        cols.push({ label: 'Price Zone', sortable: true, sortValue: _narrowZoneLabel,
            render: (_, row) => _narrowZoneHtml(row) });
    }
    return cols;
}

function renderNarrowTable(results) {
    const grid      = document.getElementById('emaNarrowGrid');
    const container = document.getElementById('emaNarrowResults');
    const countEl   = document.getElementById('emaNarrowCount');
    if (!grid || !container || !countEl) return;

    countEl.textContent = `(${results.length})`;

    if (results.length === 0) {
        grid.innerHTML = '';
        container.classList.add('results-hidden');
        return;
    }

    container.classList.remove('results-hidden');
    DataGrid.mountSortable(grid, { rows: results, columns: _narrowColumns(true), empty: 'No matches.' });
}

function renderNarrowNearest(nearest, show) {
    const section = document.getElementById('emaNarrowNearestSection');
    if (!section) return;

    if (!show || nearest.length === 0) {
        section.classList.add('ema-hidden');
        return;
    }

    section.classList.remove('ema-hidden');
    const grid    = document.getElementById('emaNarrowNearestGrid');
    const countEl = section.querySelector('.nearest-count');
    if (!grid) return;

    if (countEl) countEl.textContent = `(top ${nearest.length})`;
    DataGrid.mountSortable(grid, { rows: nearest, columns: _narrowColumns(false), empty: 'No matches.' });
}

function renderNarrowError(msg) {
    clearNarrowProgress();
    const grid = document.getElementById('emaNarrowGrid');
    if (grid) {
        grid.innerHTML = `<div class="cpr-grid-status cpr-grid-status--error">` +
            `❌ Failed to load EMA Narrow data: ${DataGrid.escape(msg)}</div>`;
    }
    const countEl = document.getElementById('emaNarrowCount');
    if (countEl) countEl.textContent = '(0)';
}

// ─── Section title (EMA set) ─────────────────────────────────────────────────
function updateNarrowSectionTitle(emaSet) {
    const el = document.getElementById('ema-narrow-section-title');
    if (!el) return;
    const label = emaSet === '20_50_100' ? 'EMA 20 / 50 / 100' : 'EMA 20 / 50 / 100 / 200';
    el.textContent = `EMA Narrow — ${label} compressed on ALL selected timeframes`;
}

// ─── Badge ────────────────────────────────────────────────────────────────────
function updateNarrowBadge(group) {
    const badge = document.getElementById('narrow-tf-badge');
    if (!badge) return;
    const map = {
        'mwd': { text: '📅 Month / Week / Day', cls: 'monthly-badge' },
        'mw':  { text: '📅 Month / Week',       cls: 'monthly-badge' },
        'wd':  { text: '📅 Week / Day',         cls: 'weekly-badge' },
        'd':   { text: '📆 Day',                cls: 'daily-badge' },
        'w':   { text: '📅 Week',               cls: 'weekly-badge' },
        'm':   { text: '📅 Month',              cls: 'monthly-badge' },
    };
    const cfg = map[group] || map['wd'];
    badge.textContent = cfg.text;
    badge.className = `ema-tf-badge ${cfg.cls}`;
}
