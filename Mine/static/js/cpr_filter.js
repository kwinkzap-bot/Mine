/**
 * cpr_filter.js
 * Handles fetching, displaying, and sorting CPR filter data.
 */

let cprRefreshIntervalId = null;
let cprRequestInFlight = false;
let cprLastRequestAt = 0;

// Cached DOM references (populated on DOMContentLoaded)
const cprElems = {};

// Debounce timer for date picker (unused now — only fed the disabled loadCPRData change listener)
// let _dateDebounceTid = null;

const CPR_MIN_REQUEST_GAP_MS = 60 * 1000; // hard throttle: 1 request/minute
const CPR_AUTO_REFRESH_MS = 15 * 60 * 1000; // auto refresh every 15 minutes

// Auto-load data when page loads
window.addEventListener('load', function () {
    // Cache frequently-used DOM refs once (avoids repeated getElementById calls)
    cprElems.statusBar   = document.getElementById('status-text');
    cprElems.datePicker  = document.getElementById('cprDateFilter');
    cprElems.refreshBtn  = document.getElementById('cprRefreshBtn');
    cprElems.highIvRefreshBtn = document.getElementById('highIvRefreshBtn');
    cprElems.highIvResults = document.getElementById('highIvResults');
    cprElems.highIvCount = document.getElementById('highIvCount');
    cprElems.controls    = document.getElementById('controls');
    cprElems.expiryHlTimeframe  = document.getElementById('expiryHlTimeframe');
    cprElems.expiryHlRefreshBtn = document.getElementById('expiryHlRefreshBtn');

    const scheduler = window.CPRFilterScheduler;
    const schedulerActive = scheduler && typeof scheduler.isActive === 'function' && scheduler.isActive();
    const schedulerMarketOpen = scheduler && typeof scheduler.isMarketOpen === 'function' && scheduler.isMarketOpen();

    // Initialize date picker with today's date
    // NOTE: cprElems.datePicker itself is still used (loadHighIVData / loadExpiryHlBreakoutData
    // read its .value), only the change listener below is dead since it exclusively
    // triggered the now-disabled loadCPRData.
    if (cprElems.datePicker) {
        const today = new Date().toISOString().split('T')[0];
        cprElems.datePicker.value = today;

        // Debounced change listener — avoids concurrent requests on rapid keyboard navigation
        // cprElems.datePicker.addEventListener('change', () => {
        //     clearTimeout(_dateDebounceTid);
        //     _dateDebounceTid = setTimeout(() => loadCPRData(true), 300);
        // });
    }

    // Initialize Refresh Button
    if (cprElems.refreshBtn) {
        cprElems.refreshBtn.addEventListener('click', () => {
            loadCPRData(true);
        });
    }

    // Initialize High IV Refresh Button
    if (cprElems.highIvRefreshBtn) {
        cprElems.highIvRefreshBtn.addEventListener('click', () => {
            const selectedDate = cprElems.datePicker ? cprElems.datePicker.value : null;
            loadHighIVData(selectedDate, true);
        });
    }

    // Initialize Expiry High/Low Breakout timeframe + refresh
    if (cprElems.expiryHlRefreshBtn) {
        cprElems.expiryHlRefreshBtn.addEventListener('click', () => {
            const timeframe = cprElems.expiryHlTimeframe ? cprElems.expiryHlTimeframe.value : '60minute';
            loadExpiryHlBreakoutData(timeframe, true);
        });
    }
    if (cprElems.expiryHlTimeframe) {
        cprElems.expiryHlTimeframe.addEventListener('change', () => {
            loadExpiryHlBreakoutData(cprElems.expiryHlTimeframe.value, true);
        });
    }

    // Avoid double-triggering the API when the scheduler is already running during market hours
    if (schedulerActive && schedulerMarketOpen) {
        if (cprElems.statusBar) cprElems.statusBar.textContent = '⏳ Scheduler active - waiting for next run...';
    } else {
        if (cprElems.statusBar) cprElems.statusBar.textContent = '⏳ Loading initial data...';
        loadCPRData(false);
        const selectedDate = cprElems.datePicker ? cprElems.datePicker.value : null;
        loadHighIVData(selectedDate, false);
        loadExpiryHlBreakoutData(cprElems.expiryHlTimeframe ? cprElems.expiryHlTimeframe.value : '60minute', false);
    }

    // Set interval for controlled refresh - only if scheduler is not already running
    if (!schedulerActive) {
        if (cprRefreshIntervalId) {
            clearInterval(cprRefreshIntervalId);
            cprRefreshIntervalId = null;
        }

        cprRefreshIntervalId = setInterval(() => {
            if (document.visibilityState !== 'visible') return;
            const selectedDate = cprElems.datePicker ? cprElems.datePicker.value : '';
            const today = new Date().toISOString().split('T')[0];
            if (selectedDate && selectedDate !== today) return;
            loadCPRData();
        }, CPR_AUTO_REFRESH_MS);
    }

    // Click-to-sort is owned by DataGrid.mountSortable now — see displayResults().
});

// A grid container (a plain <div>, not a <table>) shows its own loading /
// error state directly — no fake colspan <tr> needed once the table itself
// is built by DataGrid.
function _gridLoadingHtml(label) {
    return `<div class="cpr-grid-status">
        <span class="cpr-grid-spinner"></span>${DataGrid.escape(label)}
    </div>`;
}
function _gridErrorHtml(label) {
    return `<div class="cpr-grid-status cpr-grid-status--error">❌ ${DataGrid.escape(label)}</div>`;
}

/**
 * Fetches High IV Percentile data from the backend API separately.
 */
async function loadHighIVData(selectedDate, refresh = false) {
    const highIvGrid = document.getElementById('highIvGrid');
    const highIvResultsDiv = cprElems.highIvResults || document.getElementById('highIvResults');
    const highIvCountSpan = cprElems.highIvCount || document.getElementById('highIvCount');

    if (!highIvGrid || !highIvResultsDiv || !highIvCountSpan) {
        return;
    }

    const highIvRefreshBtn = cprElems.highIvRefreshBtn;
    if (highIvRefreshBtn) {
        highIvRefreshBtn.classList.add('loading');
        highIvRefreshBtn.disabled = true;
    }

    // Show High IV block and set loading indicator
    highIvResultsDiv.classList.remove('results-hidden');
    highIvCountSpan.textContent = '...';
    highIvGrid.innerHTML = _gridLoadingHtml(
        '⚡ Scanning option chains and computing 1-year Historical Volatility percentile rankings...');

    try {
        let url = '/api/cpr-filter/high-iv';
        const params = [];
        if (selectedDate) {
            params.push(`date=${selectedDate}`);
        }
        if (refresh) {
            params.push('refresh=true');
        }
        if (params.length > 0) {
            url += `?${params.join('&')}`;
        }

        const response = await fetchJson(url);
        if (response && response.success) {
            const highIvStocks = response.high_iv_stocks || [];
            displayResults('highIv', highIvStocks);
        } else {
            highIvGrid.innerHTML = _gridErrorHtml('Failed to load High IV percentile data.');
            highIvCountSpan.textContent = '(0)';
        }
    } catch (error) {
        console.error('Error fetching High IV data:', error);
        highIvGrid.innerHTML = _gridErrorHtml('Error: ' + error.message);
        highIvCountSpan.textContent = '(0)';
    } finally {
        if (highIvRefreshBtn) {
            highIvRefreshBtn.classList.remove('loading');
            highIvRefreshBtn.disabled = false;
        }
    }
}

/**
 * Fetches the Expiry High/Low breakout scan (BUY/SELL) for the selected timeframe.
 */
async function loadExpiryHlBreakoutData(timeframe, refresh = false) {
    timeframe = timeframe === 'day' ? 'day' : '60minute';

    const buyGrid  = document.getElementById('expiryHlBuyGrid');
    const buyDiv   = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_BUY_RESULTS);
    const buyCount = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_BUY_COUNT);
    const sellGrid  = document.getElementById('expiryHlSellGrid');
    const sellDiv   = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_SELL_RESULTS);
    const sellCount = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_SELL_COUNT);

    if (!buyGrid || !sellGrid) return;

    const refreshBtn = cprElems.expiryHlRefreshBtn;
    if (refreshBtn) {
        refreshBtn.classList.add('loading');
        refreshBtn.disabled = true;
    }

    const loadingHtml = _gridLoadingHtml(
        `Scanning Expiry High/Low breakouts (${timeframe === 'day' ? '1 Day' : '1 Hour'})...`);
    if (buyDiv) buyDiv.classList.remove('results-hidden');
    if (sellDiv) sellDiv.classList.remove('results-hidden');
    if (buyCount) buyCount.textContent = '(...)';
    if (sellCount) sellCount.textContent = '(...)';
    buyGrid.innerHTML = loadingHtml;
    sellGrid.innerHTML = loadingHtml;

    try {
        const selectedDate = cprElems.datePicker ? cprElems.datePicker.value : null;
        let url = `/api/cpr-filter/expiry-hl-breakout?timeframe=${timeframe}`;
        if (selectedDate) url += `&date=${selectedDate}`;
        if (refresh) url += '&refresh=true';

        const response = await fetchJson(url);
        if (response && response.success) {
            displayResults('expiryHlBuy', response.buy || []);
            displayResults('expiryHlSell', response.sell || []);
        } else {
            const errorHtml = _gridErrorHtml('Failed to load Expiry High/Low breakout data.');
            buyGrid.innerHTML = errorHtml;
            sellGrid.innerHTML = errorHtml;
            if (buyCount) buyCount.textContent = '(0)';
            if (sellCount) sellCount.textContent = '(0)';
        }
    } catch (error) {
        console.error('Error fetching Expiry High/Low breakout data:', error);
        const errorHtml = _gridErrorHtml('Error: ' + error.message);
        buyGrid.innerHTML = errorHtml;
        sellGrid.innerHTML = errorHtml;
    } finally {
        if (refreshBtn) {
            refreshBtn.classList.remove('loading');
            refreshBtn.disabled = false;
        }
    }
}

/**
 * Sets all result tables to a beautiful loading/scanning state.
 */
function showGridLoadingState() {
    const types = [
        // Camarilla / D-RSI reversal scanners — commented out
        // { id: 'camarillaCprReversalBullish', cols: 6, label: 'Camarilla Monthly S3 inside Monthly CPR Reversals (Bullish)' },
        // { id: 'camarillaCprReversalBearish', cols: 6, label: 'Camarilla Monthly R3 inside Monthly CPR Reversals (Bearish)' },
        // { id: 'drsiReversalBullish', cols: 6, label: 'Delta-RSI bullish crossovers' },
        // { id: 'drsiReversalBearish', cols: 6, label: 'Delta-RSI bearish crossovers' }
    ];

    types.forEach(t => {
        const tbody = document.getElementById(`${t.id}Body`);
        const container = document.getElementById(`${t.id}Results`);
        const countSpan = document.getElementById(`${t.id}Count`);

        if (tbody && container && countSpan) {
            container.classList.remove('results-hidden');
            countSpan.textContent = '(...)';
            tbody.innerHTML = `
                <tr>
                    <td colspan="${t.cols}" style="text-align: center; padding: 24px; color: var(--scan-th-text); font-weight: 500; background: var(--scan-bg);">
                        <div style="display: inline-block; width: 14px; height: 14px; border: 2px solid var(--scan-th-text); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 8px; vertical-align: middle;"></div>
                        Scanning ${t.label}...
                    </td>
                </tr>
            `;
        }
    });
}

/**
 * Fetches CPR data from the backend API using fetchJson utility.
 */
async function loadCPRData(refresh = false) {
    // Disabled: /api/cpr-filter only fed the Camarilla / D-RSI reversal tables,
    // which are commented out above and no longer rendered on this page.
    return;

    /*
    const now = Date.now();
    if (cprRequestInFlight) {
        return;
    }
    if (!refresh && (now - cprLastRequestAt < CPR_MIN_REQUEST_GAP_MS)) {
        return;
    }

    cprRequestInFlight = true;
    cprLastRequestAt = now;

    const statusBar = cprElems.statusBar;

    // Toggle button state
    const refreshBtn = cprElems.refreshBtn;
    if (refreshBtn) {
        refreshBtn.classList.add('loading');
        refreshBtn.disabled = true;
    }

    // Activate loading indicators inside all grid results
    showGridLoadingState();

    const isInitialLoad = statusBar ? (statusBar.textContent.indexOf('Loading initial data') !== -1) : false;

    if (statusBar) {
        statusBar.textContent = isInitialLoad ? '⏳ Loading initial data...' : `⏳ Refreshing data... (Last: ${new Date().toLocaleTimeString()})`;
    }

    try {
        // Get selected date
        const selectedDate = cprElems.datePicker ? cprElems.datePicker.value : null;

        // Use the global fetchJson utility
        let url = '/api/cpr-filter';
        const params = [];
        if (selectedDate) {
            params.push(`date=${selectedDate}`);
        }
        if (refresh) {
            params.push('refresh=true');
        }
        if (params.length > 0) {
            url += '?' + params.join('&');
        }

        const response = await fetchJson(url);

        console.log('CPR Filter API Response:', response);

        if (response && response.success) {
            // Camarilla / D-RSI reversal processing & display — commented out
            // const drsiFilter = response.drsi_filter || {};
            // const drsiReversalBullishResults = drsiFilter.reversal_bullish || [];
            // const drsiReversalBearishResults = drsiFilter.reversal_bearish || [];

            // const camarillaCprReversal = response.camarilla_cpr_reversal || {};
            // const camarillaCprReversalBullishResults = camarillaCprReversal.bullish || [];
            // const camarillaCprReversalBearishResults = camarillaCprReversal.bearish || [];

            // const drsiReversalBullishCount = drsiReversalBullishResults.length;
            // const drsiReversalBearishCount = drsiReversalBearishResults.length;
            // const camarillaCprReversalBullishCount = camarillaCprReversalBullishResults.length;
            // const camarillaCprReversalBearishCount = camarillaCprReversalBearishResults.length;

            // console.log(`Data loaded - Camarilla Bull: ${camarillaCprReversalBullishCount}, Camarilla Bear: ${camarillaCprReversalBearishCount}, D-RSI Rev Bull: ${drsiReversalBullishCount}, D-RSI Rev Bear: ${drsiReversalBearishCount}`);

            // displayResults('camarillaCprReversalBullish', camarillaCprReversalBullishResults);
            // displayResults('camarillaCprReversalBearish', camarillaCprReversalBearishResults);
            // displayResults('drsiReversalBullish', drsiReversalBullishResults);
            // displayResults('drsiReversalBearish', drsiReversalBearishResults);

            // updateStats(drsiReversalBullishCount, drsiReversalBearishCount);

            // Hide the controls section if we have data to show results
            if (cprElems.controls) cprElems.controls.classList.add('results-hidden');

            // Batch show/hide result sections — Camarilla / D-RSI entries commented out
            toggleResultSections({
                // [CONSTANTS.DOM_IDS.DRSI_REVERSAL_BULLISH_RESULTS]:        drsiReversalBullishCount,
                // [CONSTANTS.DOM_IDS.DRSI_REVERSAL_BEARISH_RESULTS]:        drsiReversalBearishCount,
                // [CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BULLISH_RESULTS]: camarillaCprReversalBullishCount,
                // [CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BEARISH_RESULTS]: camarillaCprReversalBearishCount,
            });
            if (statusBar) {
                statusBar.textContent = `✅ Last update: ${new Date().toLocaleTimeString()}`;
            }
        } else if (response && !response.needs_login) {
            // Only show error if it's not a session expiration handled by fetchJson
            const errorMsg = response.message || 'Unknown error';
            if (statusBar) statusBar.textContent = `❌ Error loading data: ${errorMsg}`;
            console.error('API Error:', response);
        }
    } catch (error) {
        console.error('Error fetching CPR data:', error);
        if (statusBar) statusBar.textContent = `❌ Network Error: ${error.message}`;
    } finally {
        cprRequestInFlight = false;
        if (cprElems.refreshBtn) {
            cprElems.refreshBtn.classList.remove('loading');
            cprElems.refreshBtn.disabled = false;
        }
    }
    */
}

window.addEventListener('beforeunload', function () {
    if (cprRefreshIntervalId) {
        clearInterval(cprRefreshIntervalId);
        cprRefreshIntervalId = null;
    }
});

/**
 * Shows or hides result sections based on result counts.
 * @param {Object} counts - Map of DOM ID -> count
 */
function toggleResultSections(counts) {
    Object.entries(counts).forEach(([id, count]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('results-hidden', count === 0);
    });
}

// Symbol column — a TradingView link, shared by every scanner grid on this page.
function _fmtVol(v) {
    v = Number(v || 0);
    if (v >= 10000000) return (v / 10000000).toFixed(1) + 'Cr';
    if (v >= 100000)   return (v / 100000).toFixed(1) + 'L';
    if (v >= 1000)     return (v / 1000).toFixed(1) + 'K';
    return v.toString();
}
function _symbolColumn() {
    return {
        key: 'symbol', label: 'Symbol', sortable: true, strong: true,
        render: (symbol) => `<a href="https://in.tradingview.com/chart/?symbol=NSE:` +
            `${encodeURIComponent(symbol)}" target="_blank" rel="noopener noreferrer" ` +
            `class="symbol-link">${DataGrid.escape(symbol)}</a>`,
    };
}
// gap-up/gap-down are this page's own theme-aware up/down colours (kept as
// page CSS, not the grid's dg-pos/dg-neg, so nothing here drifts from the
// rest of the scanner's palette).
const _upDownClass = (v) => Number(v || 0) > 0 ? 'gap-up' : Number(v || 0) < 0 ? 'gap-down' : '';

// ── Per-table column configs ────────────────────────────────────────
const _SCANNER_COLUMNS = {
    highIv: () => [
        _symbolColumn(),
        { key: 'current_price', label: 'Price', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'iv_percentile', label: 'IV Pctl %', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(1) + '%',
          cellClass: v => Number(v || 0) >= 90 ? 'iv-very-high' : 'iv-high' },
        { key: 'atm_iv', label: 'ATM IV %', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(1) + '%' },
        { key: 'day_change_pct', label: 'Day Chg %', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) + '%', cellClass: _upDownClass },
        { key: 'volume', label: 'Volume', sortable: true, align: 'right', format: _fmtVol },
        { key: 'oi_change_pct', label: 'OI% Chg', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(1) + '%', cellClass: _upDownClass },
        { key: 'pcr', label: 'PCR', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2),
          cellClass: v => Number(v || 0) > 1 ? 'gap-up' : Number(v || 0) < 0.7 ? 'gap-down' : '' },
        { key: 'max_pain', label: 'Max Pain', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(0) },
    ],
    // Expiry High/Low breakout — Buy and Sell are the same shape; only the
    // Price column's tone differs (every row in Buy reads as up, Sell as down).
    expiryHl: (isBuy) => [
        _symbolColumn(),
        { key: 'current_price', label: 'Price', sortable: true, align: 'right', strong: true,
          format: v => Number(v || 0).toFixed(2), tone: isBuy ? 'pos' : 'neg' },
        { key: 'expiry_high', label: 'Expiry High', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'expiry_low', label: 'Expiry Low', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'expiry_date', label: 'Expiry Date', sortable: true },
    ],
};

/**
 * Populates a scanner grid with data.
 * @param {string} type - The grid type identifier ('highIv', 'expiryHlBuy', 'expiryHlSell').
 * @param {Array<Object>} results - The list of stock objects.
 */
function displayResults(type, results) {
    const grid = document.getElementById(`${type}Grid`);
    const container = document.getElementById(`${type}Results`);
    const countSpan = document.getElementById(`${type}Count`);

    if (!grid || !container || !countSpan) return;

    if (!Array.isArray(results)) {
        console.error(`Invalid results for type '${type}':`, results);
        results = [];
    }

    if (results.length === 0) {
        grid.innerHTML = '';
        container.classList.add('results-hidden');
        countSpan.textContent = '(0)';
        return;
    }

    const columns = type === 'highIv' ? _SCANNER_COLUMNS.highIv()
        : type === 'expiryHlBuy'  ? _SCANNER_COLUMNS.expiryHl(true)
        : type === 'expiryHlSell' ? _SCANNER_COLUMNS.expiryHl(false)
        : null;
    if (!columns) return;

    DataGrid.mountSortable(grid, { rows: results, columns, empty: 'No matches.' });

    container.classList.remove('results-hidden');
    countSpan.textContent = `(${results.length})`;
}

/**
 * Updates the header counts.
 */
function updateStats(drsiReversalBullishCount = 0, drsiReversalBearishCount = 0) {
    const drsiRevBullishCountEl = document.getElementById(CONSTANTS.DOM_IDS.DRSI_REVERSAL_BULLISH_COUNT);
    const drsiRevBearishCountEl = document.getElementById(CONSTANTS.DOM_IDS.DRSI_REVERSAL_BEARISH_COUNT);
    if (drsiRevBullishCountEl) drsiRevBullishCountEl.textContent = `(${drsiReversalBullishCount})`;
    if (drsiRevBearishCountEl) drsiRevBearishCountEl.textContent = `(${drsiReversalBearishCount})`;
}

// Click-to-sort is DataGrid.mountSortable's job now (see displayResults()) —
// the bespoke column-index/text-scraping sorter that used to live here is gone.

