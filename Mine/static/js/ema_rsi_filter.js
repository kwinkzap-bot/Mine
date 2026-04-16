/**
 * ema_rsi_filter.js
 * Frontend logic for the EMA/RSI Filter page.
 *   - Weekly: EMA 208 / RSI 208 crossing above 51
 *   - Daily:  EMA 88  / RSI 88  crossing above 51
 */

'use strict';

let _emaInFlight = false;
let _emaLastAt = 0;
let _emaSortDir = {};
let _emaRefreshTimer = null;

const EMA_MIN_GAP_MS     = 60 * 1000;       // hard throttle: 1 req/min
const EMA_AUTO_REFRESH_MS = 20 * 60 * 1000; // auto-refresh every 20 min

// ─── Bootstrap ────────────────────────────────────────────────────────────────
window.addEventListener('load', () => {
    // ── Date picker: default to today ────────────────────────────────────────
    const datePicker = document.getElementById('emaDateFilter');
    if (datePicker) {
        const today = new Date().toISOString().split('T')[0];
        datePicker.value = today;

        // Re-fetch immediately when date changes (bypass throttle like CPR filter)
        datePicker.addEventListener('change', () => {
            _emaLastAt = 0;
            loadEmaData();
        });
    }

    setStatus('⏳ Loading initial data...');
    loadEmaData();

    // ── Auto-refresh — skip historical dates (same guard as CPR filter) ────────
    _emaRefreshTimer = setInterval(() => {
        if (document.visibilityState !== 'visible') return;

        const picker   = document.getElementById('emaDateFilter');
        const selected = picker ? picker.value : '';
        const today    = new Date().toISOString().split('T')[0];

        if (selected && selected !== today) return; // don't re-poll past dates

        loadEmaData();
    }, EMA_AUTO_REFRESH_MS);

    // ── Manual refresh button ─────────────────────────────────────────────────
    const btn = document.getElementById('ema-refresh-btn');
    if (btn) {
        btn.addEventListener('click', () => {
            _emaLastAt = 0;
            loadEmaData();
        });
    }

    // ── Sort listeners ────────────────────────────────────────────────────────
    ['weeklyEmaTable', 'dailyEmaTable'].forEach(tid => {
        const tbl = document.getElementById(tid);
        if (!tbl) return;
        tbl.querySelectorAll('th[data-col]').forEach(th => {
            th.addEventListener('click', () => sortTable(tid, th.dataset.col));
        });
    });
});

// Clean up interval on page unload
window.addEventListener('beforeunload', () => {
    if (_emaRefreshTimer) { clearInterval(_emaRefreshTimer); _emaRefreshTimer = null; }
});

// ─── Data Fetch ───────────────────────────────────────────────────────────────
async function loadEmaData() {
    const now = Date.now();
    if (_emaInFlight) return;
    if (now - _emaLastAt < EMA_MIN_GAP_MS) return;

    _emaInFlight = true;
    _emaLastAt   = now;

    const isInitial = (document.getElementById('ema-status-text')?.textContent || '').includes('initial');
    setStatus(isInitial
        ? '⏳ Loading initial data...'
        : `⏳ Refreshing data... (Last: ${new Date().toLocaleTimeString()})`
    );

    const btn = document.getElementById('ema-refresh-btn');
    if (btn) btn.disabled = true;

    try {
        // Build URL with optional ?date= param — identical to CPR filter pattern
        const picker      = document.getElementById('emaDateFilter');
        const selectedDate = picker ? picker.value : null;

        let url = '/api/ema-rsi-filter';
        if (selectedDate) url += `?date=${selectedDate}`;

        const response = await fetchJson(url);

        if (response && response.success) {
            const weekly  = response.weekly_ema       || [];
            const daily   = response.daily_ema        || [];
            const nearest = response.nearest_weekly   || [];

            renderTable('weeklyEma', weekly, 'weekly');
            renderTable('dailyEma',  daily,  'daily');

            // Show nearest-weekly section when there are 0 matches
            renderNearestWeekly(nearest, weekly.length === 0);

            const totalMatches = weekly.length + daily.length;
            const emptyState   = document.getElementById('ema-empty-state');
            if (emptyState) {
                // hide main empty state if there are nearest stocks to show
                emptyState.classList.toggle('ema-hidden', totalMatches > 0 || nearest.length > 0);
            }

            const dateLabel = selectedDate ? ` [${selectedDate}]` : '';
            setStatus(
                `✅ Last update: ${new Date().toLocaleTimeString()}${dateLabel} | ` +
                `Weekly (EMA/RSI 208): ${weekly.length} match${weekly.length !== 1 ? 'es' : ''} | ` +
                `Daily (EMA/RSI 88): ${daily.length} match${daily.length !== 1 ? 'es' : ''}`
            );
        } else if (response && !response.needs_login) {
            setStatus(`❌ Error: ${response.message || response.error || 'Unknown error'}`);
        }
    } catch (err) {
        console.error('EMA filter fetch error:', err);
        setStatus(`❌ Network error: ${err.message}`);
    } finally {
        _emaInFlight = false;
        if (btn) btn.disabled = false;
    }
}


// ─── Render Table ─────────────────────────────────────────────────────────────
function renderTable(type, results, timeframe) {
    const tbody     = document.getElementById(`${type}Body`);
    const container = document.getElementById(`${type}Results`);
    const countEl   = document.getElementById(`${type}Count`);

    if (!tbody || !container || !countEl) return;

    tbody.innerHTML = '';
    countEl.textContent = `(${results.length})`;

    if (results.length === 0) {
        container.classList.add('results-hidden');
        return;
    }

    container.classList.remove('results-hidden');

    results.forEach(stock => {
        const tvUrl   = `https://in.tradingview.com/chart/?symbol=NSE:${stock.symbol}`;
        const symCell = `<a href="${tvUrl}" target="_blank" rel="noopener noreferrer" class="symbol-link">${stock.symbol}</a>`;

        const closeKey = timeframe === 'weekly' ? 'weekly_close' : 'daily_close';
        const closeVal = stock[closeKey]       != null ? Number(stock[closeKey]).toFixed(2)  : '—';
        const emaVal   = stock.ema_208         != null ? Number(stock.ema_208).toFixed(2)    : '—';
        const rsiVal   = stock.rsi_208         != null ? Number(stock.rsi_208).toFixed(2)    : '—';
        const price    = stock.current_price   != null ? Number(stock.current_price).toFixed(2) : '—';

        const rsiClass = stock.rsi_in_range
            ? 'rsi-in-range' : 'rsi-neutral';
        const emaClass = stock.ema_touched ? 'ema-close' : '';

        let triggerHtml = '';
        const t = (stock.trigger || '').toLowerCase();
        if (t.includes('ema') && t.includes('rsi')) {
            triggerHtml = `<span class="trigger-both">⚡ EMA+RSI</span>`;
        } else if (t.includes('ema')) {
            triggerHtml = `<span class="trigger-ema">📉 EMA Touch</span>`;
        } else {
            triggerHtml = `<span class="trigger-rsi">🔥 RSI > 51</span>`;
        }

        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${symCell}</td>
            <td>${price}</td>
            <td>${closeVal}</td>
            <td class="${emaClass}">${emaVal}</td>
            <td class="${rsiClass}">${rsiVal}</td>
            <td>${triggerHtml}</td>
        `;
        tbody.appendChild(row);
    });
}

// ─── Sort ─────────────────────────────────────────────────────────────────────
function sortTable(tableId, colStr) {
    const col   = parseInt(colStr, 10);
    const table = document.getElementById(tableId);
    if (!table) return;
    const tbody = table.querySelector('tbody');
    if (!tbody) return;

    const rows   = Array.from(tbody.querySelectorAll('tr'));
    const header = table.querySelector(`th[data-col="${colStr}"]`);
    if (!header) return;

    if (!_emaSortDir[tableId]) _emaSortDir[tableId] = { col: -1, dir: 'asc' };
    const state = _emaSortDir[tableId];
    const dir   = (state.col === col && state.dir === 'asc') ? 'desc' : 'asc';
    _emaSortDir[tableId] = { col, dir };

    table.querySelectorAll('th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
    header.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');

    rows.sort((a, b) => {
        const aText = (a.cells[col]?.textContent || '').replace(/[₹%,]/g, '').trim();
        const bText = (b.cells[col]?.textContent || '').replace(/[₹%,]/g, '').trim();
        const aNum  = parseFloat(aText);
        const bNum  = parseFloat(bText);
        if (!isNaN(aNum) && !isNaN(bNum) && col >= 1 && col <= 4) {
            return dir === 'asc' ? aNum - bNum : bNum - aNum;
        }
        return dir === 'asc' ? aText.localeCompare(bText) : bText.localeCompare(aText);
    });

    rows.forEach(r => tbody.appendChild(r));
}

// ─── Status helper ────────────────────────────────────────────────────────────
function setStatus(msg) {
    const el = document.getElementById('ema-status-text');
    if (el) el.textContent = msg;
}

// ─── Nearest Weekly (shown when 0 matches) ───────────────────────────────────
function renderNearestWeekly(nearest, show) {
    const container = document.getElementById('nearestWeeklySection');
    if (!container) return;

    if (!show || nearest.length === 0) {
        container.classList.add('ema-hidden');
        return;
    }

    container.classList.remove('ema-hidden');
    const tbody = container.querySelector('tbody');
    const countEl = container.querySelector('.nearest-count');
    if (!tbody) return;

    tbody.innerHTML = '';
    if (countEl) countEl.textContent = `(top ${nearest.length})`;

    nearest.forEach(stock => {
        const tvUrl   = `https://in.tradingview.com/chart/?symbol=NSE:${stock.symbol}`;
        const symCell = `<a href="${tvUrl}" target="_blank" rel="noopener noreferrer" class="symbol-link">${stock.symbol}</a>`;
        const price   = stock.current_price != null ? Number(stock.current_price).toFixed(2) : '—';
        const close   = stock.weekly_close  != null ? Number(stock.weekly_close).toFixed(2)  : '—';
        const ema     = stock.ema_208       != null ? Number(stock.ema_208).toFixed(2)        : '—';
        const rsi     = stock.rsi_208       != null ? Number(stock.rsi_208).toFixed(2)        : '—';
        const dist    = stock.ema_pct_diff  != null ? Number(stock.ema_pct_diff).toFixed(1) + '%' : '—';
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${symCell}</td>
            <td>${price}</td>
            <td>${close}</td>
            <td>${ema}</td>
            <td>${rsi}</td>
            <td class="dist-pct">${dist} away</td>
        `;
        tbody.appendChild(row);
    });
}
