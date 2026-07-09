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
let _narrowSortDir = {};
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

    const btn = document.getElementById('ema-narrow-refresh-btn');
    if (btn) {
        // Manual refresh forces a fresh scan (bypasses the 6h server cache)
        btn.addEventListener('click', () => { _narrowLastAt = 0; loadEmaNarrowData(true); });
    }

    const tbl = document.getElementById('emaNarrowTable');
    if (tbl) {
        tbl.querySelectorAll('th[data-col]').forEach(th => {
            th.addEventListener('click', () => sortNarrowTable('emaNarrowTable', th.dataset.col));
        });
    }

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

    const group     = document.getElementById('emaNarrowGroup')?.value || 'mwd';
    const threshold = document.getElementById('emaNarrowThreshold')?.value || '1.5';
    const date      = document.getElementById('emaNarrowDateFilter')?.value || '';

    const thLabel = document.getElementById('emaNarrowThresholdLabel');
    if (thLabel) thLabel.textContent = `${threshold}%`;

    let url = `/api/ema-narrow-filter?group=${group}&threshold=${threshold}`;
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
            // Scan in progress — render whatever has been found so far and keep
            // the loader row below the last record, then poll again shortly
            const partial = response.results || [];
            if (partial.length > 0) {
                renderNarrowTable(partial);
                const countEl = document.getElementById('emaNarrowCount');
                if (countEl) countEl.textContent = `(${partial.length} so far…)`;
                appendNarrowLoaderRow(response.progress);
            } else {
                renderNarrowProgress(response.progress);
            }
            _narrowPollTimer = setTimeout(() => loadEmaNarrowData(false, true), NARROW_POLL_MS);
        } else if (response && (response.success || response.results)) {
            const results = response.results || [];
            const nearest = response.nearest || [];

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

function _narrowLoaderRowHtml(progress) {
    return `
        <td colspan="8" style="text-align: center; padding: 18px; color: var(--scan-th-text); font-weight: 500; background: var(--scan-bg);">
            <div style="display: inline-block; width: 14px; height: 14px; border: 2px solid var(--scan-th-text); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 8px; vertical-align: middle;"></div>
            🧲 Scanning all NSE equity stocks for EMA compression — ${_narrowProgressDetail(progress)}
        </td>
    `;
}

// Loader shown when there are no matches yet — replaces the table body
function renderNarrowProgress(progress) {
    const tbody = document.getElementById('emaNarrowBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr>${_narrowLoaderRowHtml(progress)}</tr>`;
}

// Loader appended BELOW the last partial-result record while the scan runs
function appendNarrowLoaderRow(progress) {
    const tbody = document.getElementById('emaNarrowBody');
    if (!tbody) return;
    const row = document.createElement('tr');
    row.id = 'emaNarrowLoaderRow';
    row.innerHTML = _narrowLoaderRowHtml(progress);
    tbody.appendChild(row);
}

// ─── Render ───────────────────────────────────────────────────────────────────
function _narrowEmaTitle(stock, tf) {
    const d = stock.tfs?.[tf];
    if (!d) return '';
    const f = v => v != null ? Number(v).toFixed(2) : '—';
    return `EMA20 ${f(d.ema20)} | EMA50 ${f(d.ema50)} | EMA100 ${f(d.ema100)} | EMA200 ${f(d.ema200)}`;
}

function _narrowSpreadCell(stock, tf, key) {
    const v = stock[key];
    if (v == null) return '<td>—</td>';
    return `<td class="dist-pct" title="${_narrowEmaTitle(stock, tf)}">${Number(v).toFixed(2)}%</td>`;
}

function _narrowRow(stock, withZone) {
    const tvUrl   = `https://in.tradingview.com/chart/?symbol=NSE:${stock.symbol}`;
    const symCell = `<a href="${tvUrl}" target="_blank" rel="noopener noreferrer" class="symbol-link">${stock.symbol}</a>`;
    const fmt = v => v != null ? Number(v).toFixed(2) : '—';

    const maxSpread = stock.max_spread_pct != null ? `${Number(stock.max_spread_pct).toFixed(2)}%` : '—';
    const zone = stock.price_inside
        ? '<span class="trigger-both">🎯 Inside</span>'
        : (stock.close > (stock.tfs?.daily?.ema200 ?? stock.tfs?.weekly?.ema200 ?? 0)
            ? '<span class="trigger-ema">⬆ Above</span>'
            : '<span class="trigger-rsi">⬇ Below</span>');

    const row = document.createElement('tr');
    row.innerHTML = `
        <td>${symCell}</td>
        <td>${fmt(stock.current_price)}</td>
        <td>${fmt(stock.close)}</td>
        ${_narrowSpreadCell(stock, 'daily',   'spread_daily')}
        ${_narrowSpreadCell(stock, 'weekly',  'spread_weekly')}
        ${_narrowSpreadCell(stock, 'monthly', 'spread_monthly')}
        <td class="dist-pct">${maxSpread}</td>
        ${withZone ? `<td>${zone}</td>` : ''}
    `;
    return row;
}

function renderNarrowTable(results) {
    const tbody     = document.getElementById('emaNarrowBody');
    const container = document.getElementById('emaNarrowResults');
    const countEl   = document.getElementById('emaNarrowCount');
    if (!tbody || !container || !countEl) return;

    tbody.innerHTML = '';
    countEl.textContent = `(${results.length})`;

    if (results.length === 0) {
        container.classList.add('results-hidden');
        return;
    }

    container.classList.remove('results-hidden');
    results.forEach(stock => tbody.appendChild(_narrowRow(stock, true)));
}

function renderNarrowNearest(nearest, show) {
    const section = document.getElementById('emaNarrowNearestSection');
    if (!section) return;

    if (!show || nearest.length === 0) {
        section.classList.add('ema-hidden');
        return;
    }

    section.classList.remove('ema-hidden');
    const tbody   = document.getElementById('emaNarrowNearestBody');
    const countEl = section.querySelector('.nearest-count');
    if (!tbody) return;

    tbody.innerHTML = '';
    if (countEl) countEl.textContent = `(top ${nearest.length})`;
    nearest.forEach(stock => tbody.appendChild(_narrowRow(stock, false)));
}

function renderNarrowError(msg) {
    const tbody = document.getElementById('emaNarrowBody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; padding: 20px; color: #dc2626; font-weight: 500; background: var(--scan-bg);">
                    ❌ Failed to load EMA Narrow data: ${msg}
                </td>
            </tr>
        `;
    }
    const countEl = document.getElementById('emaNarrowCount');
    if (countEl) countEl.textContent = '(0)';
}

// ─── Sort ─────────────────────────────────────────────────────────────────────
function sortNarrowTable(tableId, colStr) {
    const col   = parseInt(colStr, 10);
    const table = document.getElementById(tableId);
    if (!table) return;
    const tbody = table.querySelector('tbody');
    if (!tbody) return;

    const rows   = Array.from(tbody.querySelectorAll('tr'))
        .filter(r => r.id !== 'emaNarrowLoaderRow'); // keep loader pinned at bottom
    const header = table.querySelector(`th[data-col="${colStr}"]`);
    if (!header) return;

    if (!_narrowSortDir[tableId]) _narrowSortDir[tableId] = { col: -1, dir: 'asc' };
    const state = _narrowSortDir[tableId];
    const dir   = (state.col === col && state.dir === 'asc') ? 'desc' : 'asc';
    _narrowSortDir[tableId] = { col, dir };

    table.querySelectorAll('th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
    header.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');

    rows.sort((a, b) => {
        const aText = (a.cells[col]?.textContent || '').replace(/[₹%,]/g, '').trim();
        const bText = (b.cells[col]?.textContent || '').replace(/[₹%,]/g, '').trim();
        const aNum  = parseFloat(aText);
        const bNum  = parseFloat(bText);
        if (!isNaN(aNum) && !isNaN(bNum) && col >= 1 && col <= 6) {
            return dir === 'asc' ? aNum - bNum : bNum - aNum;
        }
        return dir === 'asc' ? aText.localeCompare(bText) : bText.localeCompare(aText);
    });

    rows.forEach(r => tbody.appendChild(r));
    const loader = document.getElementById('emaNarrowLoaderRow');
    if (loader) tbody.appendChild(loader);
}

// ─── Badge ────────────────────────────────────────────────────────────────────
function updateNarrowBadge(group) {
    const badge = document.getElementById('narrow-tf-badge');
    if (!badge) return;
    const map = {
        'mwd': { text: '📅 Month / Week / Day', cls: 'monthly-badge' },
        'mw':  { text: '📅 Month / Week',       cls: 'monthly-badge' },
        'wd':  { text: '📅 Week / Day',         cls: 'weekly-badge' },
    };
    const cfg = map[group] || map['mwd'];
    badge.textContent = cfg.text;
    badge.className = `ema-tf-badge ${cfg.cls}`;
}
