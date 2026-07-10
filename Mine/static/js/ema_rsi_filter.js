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

    const tfPicker = document.getElementById('emaTimeframeFilter');
    if (tfPicker) {
        tfPicker.addEventListener('change', () => {
            _emaLastAt = 0;
            updateTimeframeLabels(tfPicker.value);
            loadEmaData();
        });
        // Set initial labels
        updateTimeframeLabels(tfPicker.value);
    }

    setEmaStatus('⏳ Loading initial data...');
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
            th.addEventListener('click', () => sortEmaTable(tid, th.dataset.col));
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
    setEmaStatus(isInitial
        ? '⏳ Loading initial data...'
        : `⏳ Refreshing data... (Last: ${new Date().toLocaleTimeString()})`
    );

    const btn = document.getElementById('ema-refresh-btn');
    if (btn) btn.disabled = true;

    try {
        // Build URL with optional ?date= and ?timeframe= params
        const picker      = document.getElementById('emaDateFilter');
        const selectedDate = picker ? picker.value : null;
        
        const tfPicker    = document.getElementById('emaTimeframeFilter');
        const tf          = tfPicker ? tfPicker.value : 'daily';

        let url = `/api/ema-rsi-filter?timeframe=${tf}`;
        if (selectedDate) url += `&date=${selectedDate}`;

        // Clear UI tables immediately while waiting for API
        const container = document.getElementById('weeklyEmaResults');
        if (container) container.classList.remove('results-hidden');

        const tbody = document.getElementById('weeklyEmaBody');
        if (tbody) tbody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align: center; padding: 24px; color: var(--scan-th-text); font-weight: 500; background: var(--scan-bg);">
                    <div style="display: inline-block; width: 14px; height: 14px; border: 2px solid var(--scan-th-text); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 8px; vertical-align: middle;"></div>
                    ⚡ Scanning market data and analyzing EMA/RSI crossovers... This may take up to a minute.
                </td>
            </tr>
        `;
        
        const countEl = document.getElementById('weeklyEmaCount');
        if (countEl) countEl.textContent = '(...)';
        
        const nearestTbody = document.getElementById('nearestWeeklyBody');
        if (nearestTbody) nearestTbody.innerHTML = '';
        
        const nearestContainer = document.getElementById('nearest-weekly-container');
        if (nearestContainer) nearestContainer.classList.add('ema-hidden');

        const emptyState = document.getElementById('ema-empty-state');
        if (emptyState) emptyState.classList.add('ema-hidden');

        const response = await fetchJson(url);

        if (response && (response.success || response.results)) {
            const results = response.results || [];
            const nearest = response.nearest || [];

            renderTable('weeklyEma', results);

            // Show nearest section when there are 0 matches
            renderNearestWeekly(nearest, results.length === 0);

            const totalMatches = results.length;
            const emptyState   = document.getElementById('ema-empty-state');
            if (emptyState) {
                // hide main empty state if there are nearest stocks to show
                emptyState.classList.toggle('ema-hidden', totalMatches > 0 || nearest.length > 0);
            }

            const titleStr = document.getElementById('primary-results-title')?.textContent?.split(' Touch')[0] || tf;
            const dateLabel = selectedDate ? ` [${selectedDate}]` : '';
            setEmaStatus(
                `✅ Last update: ${new Date().toLocaleTimeString()}${dateLabel} | ` +
                `${titleStr}: ${results.length} match${results.length !== 1 ? 'es' : ''}`
            );
        } else if (response && !response.needs_login) {
            setEmaStatus(`❌ Error: ${response.message || response.error || 'Unknown error'}`);
            const tbody = document.getElementById('weeklyEmaBody');
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="6" style="text-align: center; padding: 20px; color: #dc2626; font-weight: 500; background: var(--scan-bg);">
                            ❌ Failed to load EMA/RSI crossover data: ${response.message || response.error || 'Unknown error'}
                        </td>
                    </tr>
                `;
            }
        }
    } catch (err) {
        console.error('EMA filter fetch error:', err);
        setEmaStatus(`❌ Network error: ${err.message}`);
        const tbody = document.getElementById('weeklyEmaBody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" style="text-align: center; padding: 20px; color: #dc2626; font-weight: 500; background: var(--scan-bg);">
                        ❌ Network error while loading EMA/RSI Touch data: ${err.message}
                    </td>
                </tr>
            `;
        }
    } finally {
        _emaInFlight = false;
        if (btn) btn.disabled = false;
    }
}


// ─── Render Table ─────────────────────────────────────────────────────────────
function renderTable(type, results) {
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

        const closeVal = stock.close           != null ? Number(stock.close).toFixed(2)      : '—';
        const emaVal   = stock.ema             != null ? Number(stock.ema).toFixed(2)        : '—';
        const rsiVal   = stock.rsi             != null ? Number(stock.rsi).toFixed(2)        : '—';
        const price    = stock.current_price   != null ? Number(stock.current_price).toFixed(2) : '—';

        const rsiClass = stock.rsi_in_range
            ? 'rsi-in-range' : 'rsi-neutral';
        const emaClass = stock.ema_touched ? 'ema-close' : '';

        const triggerHtml = `<span class="trigger-both">⚡ EMA+RSI</span>`;

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
function sortEmaTable(tableId, colStr) {
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
function setEmaStatus(msg) {
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
        const close   = stock.close         != null ? Number(stock.close).toFixed(2)         : '—';
        const ema     = stock.ema           != null ? Number(stock.ema).toFixed(2)           : '—';
        const rsi     = stock.rsi           != null ? Number(stock.rsi).toFixed(2)           : '—';
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

// ─── Update UI Labels ────────────────────────────────────────────────────────
function updateTimeframeLabels(timeframe) {
    let titleStr, badgeStr, colStr, badgeClass;
    
    if (timeframe === '1minute') {
        titleStr = '1Min EMA 60'; badgeStr = '⏱️ 1 Min'; colStr = 'Close'; badgeClass = 'daily-badge';
    } else if (timeframe === '5minute') {
        titleStr = '5Min EMA 75'; badgeStr = '⏱️ 5 Min'; colStr = 'Close'; badgeClass = 'daily-badge';
    } else if (timeframe === '15minute') {
        titleStr = '15Min EMA 125'; badgeStr = '⏱️ 15 Min'; colStr = 'Close'; badgeClass = 'daily-badge';
    } else if (timeframe === '60minute') {
        titleStr = '1Hour EMA 137'; badgeStr = '⏱️ 1 Hour'; colStr = 'Close'; badgeClass = 'daily-badge';
    } else if (timeframe === 'daily') {
        titleStr = 'Daily EMA 88'; badgeStr = '📆 Daily'; colStr = 'Close'; badgeClass = 'daily-badge';
    } else if (timeframe === 'weekly') {
        titleStr = 'Weekly EMA 208'; badgeStr = '📅 Weekly'; colStr = 'Close'; badgeClass = 'weekly-badge';
    } else if (timeframe === 'monthly') {
        titleStr = 'Monthly EMA 48'; badgeStr = '📅 Monthly'; colStr = 'Close'; badgeClass = 'monthly-badge';
    } else {
        titleStr = 'EMA Touch'; badgeStr = '⏱️'; colStr = 'Close'; badgeClass = 'weekly-badge';
    }
    
    const badge = document.getElementById('primary-tf-badge');
    if (badge) {
        badge.textContent = badgeStr;
        badge.className = `ema-tf-badge ${badgeClass}`;
    }
    
    const title = document.getElementById('primary-results-title');
    if (title) {
        title.innerHTML = `📈 ${titleStr} Touch <span id="weeklyEmaCount">(0)</span>`;
    }
    
    const nearestTitle = document.getElementById('nearest-primary-title');
    if (nearestTitle) {
        nearestTitle.innerHTML = `🎯 Nearest to ${titleStr} <span class="nearest-count"></span>
            <small style="font-weight:400;font-size:0.75rem;opacity:0.7;"> — no touch yet, showing closest stocks</small>`;
    }
    
    const primaryCol = document.getElementById('primary-close-col');
    if (primaryCol) primaryCol.textContent = colStr;
    
    const nearestCol = document.getElementById('nearest-close-col');
    if (nearestCol) nearestCol.textContent = colStr;
}
