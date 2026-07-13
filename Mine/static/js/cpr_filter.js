/**
 * cpr_filter.js
 * Handles fetching, displaying, and sorting CPR filter data.
 */

// Global state to track sort direction for each table
let sortDirection = {};
let cprRefreshIntervalId = null;
let cprRequestInFlight = false;
let cprLastRequestAt = 0;

// Cached DOM references (populated on DOMContentLoaded)
const cprElems = {};

// Cached sort header references per table (avoids full querySelectorAll on every sort click)
const _lastSortedTh = {};

// Debounce timer for date picker
let _dateDebounceTid = null;

const CPR_MIN_REQUEST_GAP_MS = 60 * 1000; // hard throttle: 1 request/minute
const CPR_AUTO_REFRESH_MS = 15 * 60 * 1000; // auto refresh every 15 minutes

// Auto-load data when page loads
window.addEventListener('load', function () {
    // Cache frequently-used DOM refs once (avoids repeated getElementById calls)
    cprElems.statusBar   = document.getElementById('status-text');
    cprElems.datePicker  = document.getElementById('cprDateFilter');
    cprElems.refreshBtn  = document.getElementById('cprRefreshBtn');
    cprElems.highIvRefreshBtn = document.getElementById('highIvRefreshBtn');
    cprElems.highIvBody  = document.getElementById('highIvBody');
    cprElems.highIvResults = document.getElementById('highIvResults');
    cprElems.highIvCount = document.getElementById('highIvCount');
    cprElems.controls    = document.getElementById('controls');
    cprElems.expiryHlTimeframe  = document.getElementById('expiryHlTimeframe');
    cprElems.expiryHlRefreshBtn = document.getElementById('expiryHlRefreshBtn');

    const scheduler = window.CPRFilterScheduler;
    const schedulerActive = scheduler && typeof scheduler.isActive === 'function' && scheduler.isActive();
    const schedulerMarketOpen = scheduler && typeof scheduler.isMarketOpen === 'function' && scheduler.isMarketOpen();

    // Initialize date picker with today's date
    if (cprElems.datePicker) {
        const today = new Date().toISOString().split('T')[0];
        cprElems.datePicker.value = today;

        // Debounced change listener — avoids concurrent requests on rapid keyboard navigation
        cprElems.datePicker.addEventListener('change', () => {
            clearTimeout(_dateDebounceTid);
            _dateDebounceTid = setTimeout(() => loadCPRData(true), 300);
        });
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

    // Event delegation for sort — one listener per table, survives innerHTML re-renders
    const sortableTableIds = [
        'highIvTable',
        CONSTANTS.DOM_IDS.EXPIRY_HL_BUY_TABLE,
        CONSTANTS.DOM_IDS.EXPIRY_HL_SELL_TABLE,
        // Camarilla / D-RSI reversal tables — commented out
        // CONSTANTS.DOM_IDS.DRSI_BULLISH_TABLE,
        // CONSTANTS.DOM_IDS.DRSI_BEARISH_TABLE,
        // CONSTANTS.DOM_IDS.DRSI_REVERSAL_BULLISH_TABLE,
        // CONSTANTS.DOM_IDS.DRSI_REVERSAL_BEARISH_TABLE,
        // CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BULLISH_TABLE,
        // CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BEARISH_TABLE,
    ];
    sortableTableIds.forEach(tableId => {
        const table = document.getElementById(tableId);
        if (table) {
            table.addEventListener('click', e => {
                const th = e.target.closest('th[data-column-index]');
                if (th) sortTable(tableId, th.dataset.columnIndex);
            });
        }
    });
});

/**
 * Fetches High IV Percentile data from the backend API separately.
 */
async function loadHighIVData(selectedDate, refresh = false) {
    const highIvBody = cprElems.highIvBody || document.getElementById('highIvBody');
    const highIvResultsDiv = cprElems.highIvResults || document.getElementById('highIvResults');
    const highIvCountSpan = cprElems.highIvCount || document.getElementById('highIvCount');

    if (!highIvBody || !highIvResultsDiv || !highIvCountSpan) {
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
    highIvBody.innerHTML = `
        <tr>
            <td colspan="9" style="text-align: center; padding: 24px; color: var(--scan-th-text); font-weight: 500; background: var(--scan-bg);">
                <div style="display: inline-block; width: 14px; height: 14px; border: 2px solid var(--scan-th-text); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 8px; vertical-align: middle;"></div>
                ⚡ Scanning option chains and computing 1-year Historical Volatility percentile rankings...
            </td>
        </tr>
    `;

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
            highIvBody.innerHTML = `
                <tr>
                    <td colspan="9" style="text-align: center; padding: 20px; color: #dc2626; font-weight: 500;">
                        ❌ Failed to load High IV percentile data.
                    </td>
                </tr>
            `;
            highIvCountSpan.textContent = '(0)';
        }
    } catch (error) {
        console.error('Error fetching High IV data:', error);
        highIvBody.innerHTML = `
            <tr>
                <td colspan="9" style="text-align: center; padding: 20px; color: #dc2626; font-weight: 500;">
                    ❌ Error: ${error.message}
                </td>
            </tr>
        `;
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

    const buyBody  = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_BUY_BODY);
    const buyDiv   = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_BUY_RESULTS);
    const buyCount = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_BUY_COUNT);
    const sellBody  = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_SELL_BODY);
    const sellDiv   = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_SELL_RESULTS);
    const sellCount = document.getElementById(CONSTANTS.DOM_IDS.EXPIRY_HL_SELL_COUNT);

    if (!buyBody || !sellBody) return;

    const refreshBtn = cprElems.expiryHlRefreshBtn;
    if (refreshBtn) {
        refreshBtn.classList.add('loading');
        refreshBtn.disabled = true;
    }

    const loadingRow = `
        <tr>
            <td colspan="5" style="text-align: center; padding: 24px; color: var(--scan-th-text); font-weight: 500; background: var(--scan-bg);">
                <div style="display: inline-block; width: 14px; height: 14px; border: 2px solid var(--scan-th-text); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 8px; vertical-align: middle;"></div>
                Scanning Expiry High/Low breakouts (${timeframe === 'day' ? '1 Day' : '1 Hour'})...
            </td>
        </tr>
    `;
    if (buyDiv) buyDiv.classList.remove('results-hidden');
    if (sellDiv) sellDiv.classList.remove('results-hidden');
    if (buyCount) buyCount.textContent = '(...)';
    if (sellCount) sellCount.textContent = '(...)';
    buyBody.innerHTML = loadingRow;
    sellBody.innerHTML = loadingRow;

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
            const errorRow = `
                <tr>
                    <td colspan="5" style="text-align: center; padding: 20px; color: #dc2626; font-weight: 500;">
                        ❌ Failed to load Expiry High/Low breakout data.
                    </td>
                </tr>
            `;
            buyBody.innerHTML = errorRow;
            sellBody.innerHTML = errorRow;
            if (buyCount) buyCount.textContent = '(0)';
            if (sellCount) sellCount.textContent = '(0)';
        }
    } catch (error) {
        console.error('Error fetching Expiry High/Low breakout data:', error);
        const errorRow = `
            <tr>
                <td colspan="5" style="text-align: center; padding: 20px; color: #dc2626; font-weight: 500;">
                    ❌ Error: ${error.message}
                </td>
            </tr>
        `;
        buyBody.innerHTML = errorRow;
        sellBody.innerHTML = errorRow;
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

/**
 * Builds the inner HTML string for a single result row.
 */
function _buildRowHTML(stock, config) {
    const tradingViewUrl = `https://in.tradingview.com/chart/?symbol=NSE:${stock.symbol}`;
    const symbolCell = `<a href="${tradingViewUrl}" target="_blank" rel="noopener noreferrer" class="symbol-link">${stock.symbol}</a>`;

    if (config.isHighIv) {
        const ivPercentile = Number(stock.iv_percentile || 0);
        const ivClass = ivPercentile >= 90 ? 'iv-very-high' : 'iv-high';
        const atmIv = Number(stock.atm_iv || 0);
        const dayChangePct = Number(stock.day_change_pct || 0);
        const volume = stock.volume || 0;
        const oiChangePct = Number(stock.oi_change_pct || 0);
        const pcr = Number(stock.pcr || 0);
        const maxPain = Number(stock.max_pain || 0);

        const fmtVol = (v) => {
            if (v >= 10000000) return (v / 10000000).toFixed(1) + 'Cr';
            if (v >= 100000)   return (v / 100000).toFixed(1) + 'L';
            if (v >= 1000)     return (v / 1000).toFixed(1) + 'K';
            return v.toString();
        };

        const oiClass      = oiChangePct > 0 ? 'gap-up' : (oiChangePct < 0 ? 'gap-down' : '');
        const dayChgClass  = dayChangePct > 0 ? 'gap-up' : (dayChangePct < 0 ? 'gap-down' : '');
        const pcrClass     = pcr > 1 ? 'gap-up' : (pcr < 0.7 ? 'gap-down' : '');

        return `<tr>
            <td>${symbolCell}</td>
            <td>${stock.current_price.toFixed(2)}</td>
            <td class="${ivClass}">${ivPercentile.toFixed(1)}%</td>
            <td>${atmIv.toFixed(1)}%</td>
            <td class="${dayChgClass}">${dayChangePct.toFixed(2)}%</td>
            <td>${fmtVol(volume)}</td>
            <td class="${oiClass}">${oiChangePct.toFixed(1)}%</td>
            <td class="${pcrClass}">${pcr.toFixed(2)}</td>
            <td>${maxPain.toFixed(0)}</td>
        </tr>`;
    }

    if (config.isDrsi) {
        const rsi     = Number(stock.rsi || 0);
        const drsi    = Number(stock.drsi || 0);
        const signal  = Number(stock.signal || 0);
        const trigger = stock.trigger || '';
        return `<tr>
            <td>${symbolCell}</td>
            <td>${stock.current_price.toFixed(2)}</td>
            <td>${rsi.toFixed(1)}</td>
            <td>${drsi.toFixed(4)}</td>
            <td>${signal.toFixed(4)}</td>
            <td><span style="color:#e11d48;font-weight:700;">${trigger}</span></td>
        </tr>`;
    }

    if (config.isCamarilla) {
        const levelVal  = config.isBullish ? Number(stock.monthly_cam_s3 || 0) : Number(stock.monthly_cam_r3 || 0);
        const monthlyTc = Number(stock.monthly_tc || 0);
        const monthlyBc = Number(stock.monthly_bc || 0);
        const monthlyPp = Number(stock.monthly_pp || 0);
        const color     = config.isBullish ? '#10b981' : '#ef4444';
        return `<tr>
            <td>${symbolCell}</td>
            <td>${stock.current_price.toFixed(2)}</td>
            <td style="font-weight:bold;color:${color};">${levelVal.toFixed(2)}</td>
            <td>${monthlyTc.toFixed(2)}</td>
            <td>${monthlyBc.toFixed(2)}</td>
            <td>${monthlyPp.toFixed(2)}</td>
        </tr>`;
    }

    if (config.isExpiryHl) {
        const expHigh = Number(stock.expiry_high || 0);
        const expLow  = Number(stock.expiry_low || 0);
        const color   = config.isBuy ? '#10b981' : '#ef4444';
        return `<tr>
            <td>${symbolCell}</td>
            <td style="font-weight:bold;color:${color};">${stock.current_price.toFixed(2)}</td>
            <td>${expHigh.toFixed(2)}</td>
            <td>${expLow.toFixed(2)}</td>
            <td>${stock.expiry_date || ''}</td>
        </tr>`;
    }

    return '';
}

/**
 * Populates the results table with data.
 * @param {string} type - The table type identifier.
 * @param {Array<Object>} results - The list of stock objects.
 */
function displayResults(type, results) {
    const tbody = document.getElementById(`${type}Body`);
    const container = document.getElementById(`${type}Results`);
    const countSpan = document.getElementById(`${type}Count`);

    if (!tbody || !container || !countSpan) return;

    if (!results || !Array.isArray(results)) {
        console.error(`Invalid results for type '${type}':`, results);
        tbody.innerHTML = '';
        container.classList.add('results-hidden');
        countSpan.textContent = '(0)';
        return;
    }

    if (results.length === 0) {
        tbody.innerHTML = '';
        container.classList.add('results-hidden');
        countSpan.textContent = '(0)';
        return;
    }

    const tableConfig = {
        drsiReversalBullish: { isDrsi: true },
        drsiReversalBearish: { isDrsi: true },
        highIv: { isHighIv: true },
        camarillaCprReversalBullish: { isCamarilla: true, isBullish: true },
        camarillaCprReversalBearish: { isCamarilla: true, isBullish: false },
        expiryHlBuy: { isExpiryHl: true, isBuy: true },
        expiryHlSell: { isExpiryHl: true, isBuy: false }
    };
    const config = tableConfig[type] || {};

    // Build all rows as a single HTML string — one DOM mutation instead of N
    tbody.innerHTML = results.map(stock => _buildRowHTML(stock, config)).join('');

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

/**
 * Sorts a table by a given column index.
 * @param {string} tableId - The ID of the table.
 * @param {string} columnIndexStr - The string column index from data-column-index.
 */
function sortTable(tableId, columnIndexStr) {
    const columnIndex = parseInt(columnIndexStr);
    const table = document.getElementById(tableId);

    if (!table) {
        console.error(`Table with id '${tableId}' not found`);
        return;
    }

    const tbody = table.querySelector('tbody');

    if (!tbody) {
        console.error(`Tbody not found in table '${tableId}'`);
        return;
    }
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const header = table.querySelector(`th[data-column-index="${columnIndexStr}"]`);
    if (!header) return;

    // Initialize or update sort direction state
    if (!sortDirection[tableId]) {
        sortDirection[tableId] = { index: -1, direction: 'none' };
    }

    // Determine sort direction and update state
    const currentDirection = sortDirection[tableId].index === columnIndex ? sortDirection[tableId].direction : 'none';
    const newDirection = currentDirection === 'asc' ? 'desc' : (currentDirection === 'desc' ? 'asc' : 'asc');

    sortDirection[tableId] = { index: columnIndex, direction: newDirection };

    // Update header classes for visual feedback — only touch the previously sorted th
    const prevTh = _lastSortedTh[tableId];
    if (prevTh && prevTh !== header) prevTh.classList.remove('sort-asc', 'sort-desc');
    header.classList.remove('sort-asc', 'sort-desc');
    header.classList.add(newDirection === 'asc' ? 'sort-asc' : 'sort-desc');
    _lastSortedTh[tableId] = header;

    const isAsc = newDirection === 'asc';

    // Determine numeric column range based on table type (small/Camarilla tables have fewer columns)
    const isSmallOrCamarillaTable = tableId === CONSTANTS.DOM_IDS.DRSI_BULLISH_TABLE || tableId === CONSTANTS.DOM_IDS.DRSI_BEARISH_TABLE ||
        tableId === CONSTANTS.DOM_IDS.DRSI_REVERSAL_BULLISH_TABLE || tableId === CONSTANTS.DOM_IDS.DRSI_REVERSAL_BEARISH_TABLE ||
        tableId === CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BULLISH_TABLE || tableId === CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BEARISH_TABLE;
    const isExpiryHlTable = tableId === CONSTANTS.DOM_IDS.EXPIRY_HL_BUY_TABLE || tableId === CONSTANTS.DOM_IDS.EXPIRY_HL_SELL_TABLE;
    const numericMaxCol = isExpiryHlTable ? 3 : (isSmallOrCamarillaTable ? 5 : 7); // indices 1..3 for Expiry H/L (col 4 is a date string), 1..5 for small/Camarilla, 1..7 for large ones

    // Sort rows
    rows.sort((a, b) => {
        // Remove currency symbols, commas, and % for numeric comparison
        const aCell = a.cells[columnIndex].textContent.replace(/[₹%,]/g, '').trim();
        const bCell = b.cells[columnIndex].textContent.replace(/[₹%,]/g, '').trim();

        const aNum = parseFloat(aCell);
        const bNum = parseFloat(bCell);

        // Check if both are numbers (for price and percentage columns)
        const maxNumericIndex = numericMaxCol;
        if (!isNaN(aNum) && !isNaN(bNum) && columnIndex >= 1 && columnIndex <= maxNumericIndex) {
            return isAsc ? aNum - bNum : bNum - aNum;
        } else {
            // String comparison (for Symbol and Status columns)
            return isAsc ? aCell.localeCompare(bCell) : bCell.localeCompare(aCell);
        }
    });

    // Re-append sorted rows to the tbody
    rows.forEach(row => tbody.appendChild(row));
}

