/**
 * cpr_filter.js
 * Handles fetching, displaying, and sorting CPR filter data.
 */

// Global state to track sort direction for each table
let sortDirection = {};
let cprRefreshIntervalId = null;
let cprRequestInFlight = false;
let cprLastRequestAt = 0;

const CPR_MIN_REQUEST_GAP_MS = 60 * 1000; // hard throttle: 1 request/minute
const CPR_AUTO_REFRESH_MS = 15 * 60 * 1000; // auto refresh every 15 minutes

// Auto-load data when page loads
window.addEventListener('load', function () {
    // Debug: Log all expected elements
    console.log('Checking for required DOM elements...');
    console.log('status-bar:', !!document.getElementById('status-bar'));

    let statusBar = document.getElementById('status-text');



    const scheduler = window.CPRFilterScheduler;
    const schedulerActive = scheduler && typeof scheduler.isActive === 'function' && scheduler.isActive();
    const schedulerMarketOpen = scheduler && typeof scheduler.isMarketOpen === 'function' && scheduler.isMarketOpen();

    // Initialize date picker with today's date
    const datePicker = document.getElementById('cprDateFilter');
    if (datePicker) {
        const today = new Date().toISOString().split('T')[0];
        datePicker.value = today;

        // Add change listener
        datePicker.addEventListener('change', () => {
            loadCPRData(true);
        });
    }

    // Initialize Refresh Button
    const refreshBtn = document.getElementById('cprRefreshBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadCPRData(true);
        });
    }

    // Initialize High IV Refresh Button
    const highIvRefreshBtn = document.getElementById('highIvRefreshBtn');
    if (highIvRefreshBtn) {
        highIvRefreshBtn.addEventListener('click', () => {
            const datePicker = document.getElementById('cprDateFilter');
            const selectedDate = datePicker ? datePicker.value : null;
            loadHighIVData(selectedDate, true);
        });
    }

    // Avoid double-triggering the API when the scheduler is already running during market hours
    if (schedulerActive && schedulerMarketOpen) {
        if (statusBar) statusBar.textContent = '⏳ Scheduler active - waiting for next run...';
    } else {
        if (statusBar) statusBar.textContent = '⏳ Loading initial data...';
        loadCPRData(false);
        const selectedDate = datePicker ? datePicker.value : null;
        loadHighIVData(selectedDate, false);
    }

    // Set interval for controlled refresh - only if scheduler is not already running
    // NOTE: Keep this conservative to avoid API call loops.
    if (!schedulerActive) {
        if (cprRefreshIntervalId) {
            clearInterval(cprRefreshIntervalId);
            cprRefreshIntervalId = null;
        }

        cprRefreshIntervalId = setInterval(() => {
            if (document.visibilityState !== 'visible') {
                return;
            }

            const picker = document.getElementById('cprDateFilter');
            const selectedDate = picker ? picker.value : '';
            const today = new Date().toISOString().split('T')[0];

            // Do not poll historical dates repeatedly.
            if (selectedDate && selectedDate !== today) {
                return;
            }

            loadCPRData();
        }, CPR_AUTO_REFRESH_MS);
    }



    // Add sort listeners for High IV Percentile table
    const highIvTable = document.getElementById('highIvTable');
    if (highIvTable) {
        highIvTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable('highIvTable', header.dataset.columnIndex);
            });
        });
    }

    // Add sort listeners for D-RSI tables
    const drsiBullTable = document.getElementById(CONSTANTS.DOM_IDS.DRSI_BULLISH_TABLE);
    if (drsiBullTable) {
        drsiBullTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable(CONSTANTS.DOM_IDS.DRSI_BULLISH_TABLE, header.dataset.columnIndex);
            });
        });
    }
    const drsiBearTable = document.getElementById(CONSTANTS.DOM_IDS.DRSI_BEARISH_TABLE);
    if (drsiBearTable) {
        drsiBearTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable(CONSTANTS.DOM_IDS.DRSI_BEARISH_TABLE, header.dataset.columnIndex);
            });
        });
    }

    // Add sort listeners for D-RSI Reversal tables
    const drsiRevBullTable = document.getElementById(CONSTANTS.DOM_IDS.DRSI_REVERSAL_BULLISH_TABLE);
    if (drsiRevBullTable) {
        drsiRevBullTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable(CONSTANTS.DOM_IDS.DRSI_REVERSAL_BULLISH_TABLE, header.dataset.columnIndex);
            });
        });
    }
    const drsiRevBearTable = document.getElementById(CONSTANTS.DOM_IDS.DRSI_REVERSAL_BEARISH_TABLE);
    if (drsiRevBearTable) {
        drsiRevBearTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable(CONSTANTS.DOM_IDS.DRSI_REVERSAL_BEARISH_TABLE, header.dataset.columnIndex);
            });
        });
    }

    // Add sort listeners for Camarilla CPR Reversal tables
    const camCprRevBullTable = document.getElementById(CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BULLISH_TABLE);
    if (camCprRevBullTable) {
        camCprRevBullTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable(CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BULLISH_TABLE, header.dataset.columnIndex);
            });
        });
    }
    const camCprRevBearTable = document.getElementById(CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BEARISH_TABLE);
    if (camCprRevBearTable) {
        camCprRevBearTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable(CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BEARISH_TABLE, header.dataset.columnIndex);
            });
        });
    }
});

/**
 * Fetches High IV Percentile data from the backend API separately.
 */
async function loadHighIVData(selectedDate, refresh = false) {
    const highIvBody = document.getElementById('highIvBody');
    const highIvResultsDiv = document.getElementById('highIvResults');
    const highIvCountSpan = document.getElementById('highIvCount');

    if (!highIvBody || !highIvResultsDiv || !highIvCountSpan) {
        return;
    }

    const highIvRefreshBtn = document.getElementById('highIvRefreshBtn');
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
 * Sets all result tables to a beautiful loading/scanning state.
 */
function showGridLoadingState() {
    const types = [
        { id: 'camarillaCprReversalBullish', cols: 6, label: 'Camarilla Monthly S3 inside Monthly CPR Reversals (Bullish)' },
        { id: 'camarillaCprReversalBearish', cols: 6, label: 'Camarilla Monthly R3 inside Monthly CPR Reversals (Bearish)' },
        { id: 'drsiReversalBullish', cols: 6, label: 'Delta-RSI bullish crossovers' },
        { id: 'drsiReversalBearish', cols: 6, label: 'Delta-RSI bearish crossovers' }
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

    let statusBar = document.getElementById('status-text');

    // Toggle button state
    const refreshBtn = document.getElementById('cprRefreshBtn');
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
        const datePicker = document.getElementById('cprDateFilter');
        const selectedDate = datePicker ? datePicker.value : null;

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
            // Process the API response
            const drsiFilter = response.drsi_filter || {};
            const drsiReversalBullishResults = drsiFilter.reversal_bullish || [];
            const drsiReversalBearishResults = drsiFilter.reversal_bearish || [];

            const camarillaCprReversal = response.camarilla_cpr_reversal || {};
            const camarillaCprReversalBullishResults = camarillaCprReversal.bullish || [];
            const camarillaCprReversalBearishResults = camarillaCprReversal.bearish || [];

            const drsiReversalBullishCount = drsiReversalBullishResults.length;
            const drsiReversalBearishCount = drsiReversalBearishResults.length;
            const camarillaCprReversalBullishCount = camarillaCprReversalBullishResults.length;
            const camarillaCprReversalBearishCount = camarillaCprReversalBearishResults.length;

            console.log(`Data loaded - Camarilla Bull: ${camarillaCprReversalBullishCount}, Camarilla Bear: ${camarillaCprReversalBearishCount}, D-RSI Rev Bull: ${drsiReversalBullishCount}, D-RSI Rev Bear: ${drsiReversalBearishCount}`);

            displayResults('camarillaCprReversalBullish', camarillaCprReversalBullishResults);
            displayResults('camarillaCprReversalBearish', camarillaCprReversalBearishResults);
            displayResults('drsiReversalBullish', drsiReversalBullishResults);
            displayResults('drsiReversalBearish', drsiReversalBearishResults);

            updateStats(drsiReversalBullishCount, drsiReversalBearishCount);

            // Hide the controls section if we have data to show results
            const controls = document.getElementById('controls');
            if (controls) {
                controls.classList.add('results-hidden');
            }

            // Show/hide D-RSI results
            // const drsiBullDiv = document.getElementById(CONSTANTS.DOM_IDS.DRSI_BULLISH_RESULTS);
            // if (drsiBullDiv) {
            //     if (drsiBullishCount > 0) {
            //         drsiBullDiv.classList.remove('results-hidden');
            //     } else {
            //         drsiBullDiv.classList.add('results-hidden');
            //     }
            // }
            // const drsiBearDiv = document.getElementById(CONSTANTS.DOM_IDS.DRSI_BEARISH_RESULTS);
            // if (drsiBearDiv) {
            //     if (drsiBearishCount > 0) {
            //         drsiBearDiv.classList.remove('results-hidden');
            //     } else {
            //         drsiBearDiv.classList.add('results-hidden');
            //     }
            // }
            const drsiRevBullDiv = document.getElementById(CONSTANTS.DOM_IDS.DRSI_REVERSAL_BULLISH_RESULTS);
            if (drsiRevBullDiv) {
                if (drsiReversalBullishCount > 0) {
                    drsiRevBullDiv.classList.remove('results-hidden');
                } else {
                    drsiRevBullDiv.classList.add('results-hidden');
                }
            }
            const drsiRevBearDiv = document.getElementById(CONSTANTS.DOM_IDS.DRSI_REVERSAL_BEARISH_RESULTS);
            if (drsiRevBearDiv) {
                if (drsiReversalBearishCount > 0) {
                    drsiRevBearDiv.classList.remove('results-hidden');
                } else {
                    drsiRevBearDiv.classList.add('results-hidden');
                }
            }

            const camCprRevBullDiv = document.getElementById(CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BULLISH_RESULTS);
            if (camCprRevBullDiv) {
                if (camarillaCprReversalBullishCount > 0) {
                    camCprRevBullDiv.classList.remove('results-hidden');
                } else {
                    camCprRevBullDiv.classList.add('results-hidden');
                }
            }
            const camCprRevBearDiv = document.getElementById(CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BEARISH_RESULTS);
            if (camCprRevBearDiv) {
                if (camarillaCprReversalBearishCount > 0) {
                    camCprRevBearDiv.classList.remove('results-hidden');
                } else {
                    camCprRevBearDiv.classList.add('results-hidden');
                }
            }
            if (statusBar) {
                statusBar.textContent = `✅ Last update: ${new Date().toLocaleTimeString()} | S3 Rev↑: ${camarillaCprReversalBullishCount}, R3 Rev↓: ${camarillaCprReversalBearishCount} | D-RSI Flip↑: ${drsiReversalBullishCount}, D-RSI Flip↓: ${drsiReversalBearishCount}`;
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
        const refreshBtn = document.getElementById('cprRefreshBtn');
        if (refreshBtn) {
            refreshBtn.classList.remove('loading');
            refreshBtn.disabled = false;
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
 * Populates the results table with data.
 * @param {string} type - The table type identifier.
 * @param {Array<Object>} results - The list of stock objects.
 */
function displayResults(type, results) {
    const tbody = document.getElementById(`${type}Body`);
    const container = document.getElementById(`${type}Results`);
    const countSpan = document.getElementById(`${type}Count`);

    // Check if all required elements exist
    if (!tbody || !container || !countSpan) {
        // console.error(`Missing elements for type '${type}':`, { tbody: !!tbody, container: !!container, countSpan: !!countSpan });
        return;
    }

    // Check if results is valid
    if (!results || !Array.isArray(results)) {
        console.error(`Invalid results for type '${type}':`, results);
        tbody.innerHTML = '';
        container.classList.add('results-hidden');
        countSpan.textContent = '(0)';
        return;
    }

    tbody.innerHTML = ''; // Clear existing rows

    if (results.length === 0) {
        container.classList.add('results-hidden');
        countSpan.textContent = '(0)';
        return;
    }

    container.classList.remove('results-hidden');
    countSpan.textContent = `(${results.length})`;

    const tableConfig = {
        // drsiBullish: { isDrsi: true },
        // drsiBearish: { isDrsi: true },
        drsiReversalBullish: { isDrsi: true },
        drsiReversalBearish: { isDrsi: true },
        highIv: { isHighIv: true },
        camarillaCprReversalBullish: { isCamarilla: true, isBullish: true },
        camarillaCprReversalBearish: { isCamarilla: true, isBullish: false }
    };
    const config = tableConfig[type] || { showGaps: false };
    const showGaps = config.showGaps;

    results.forEach(stock => {
        let rowHtml = '';

        // Create TradingView link for symbol
        const tradingViewUrl = `https://in.tradingview.com/chart/?symbol=NSE:${stock.symbol}`;
        const symbolCell = `<a href="${tradingViewUrl}" target="_blank" rel="noopener noreferrer" class="symbol-link">${stock.symbol}</a>`;

        if (config.isHighIv) {
            // High IV Percentile table - 9 columns
            const ivPercentile = Number(stock.iv_percentile || 0);
            const ivClass = ivPercentile >= 90 ? 'iv-very-high' : 'iv-high';
            const atmIv = Number(stock.atm_iv || 0);
            const dayChangePct = Number(stock.day_change_pct || 0);
            const volume = stock.volume || 0;
            const oiChangePct = Number(stock.oi_change_pct || 0);
            const pcr = Number(stock.pcr || 0);
            const maxPain = Number(stock.max_pain || 0);

            // Format volume with K/L/Cr suffixes
            const formatVolume = (v) => {
                if (v >= 10000000) return (v / 10000000).toFixed(1) + 'Cr';
                if (v >= 100000) return (v / 100000).toFixed(1) + 'L';
                if (v >= 1000) return (v / 1000).toFixed(1) + 'K';
                return v.toString();
            };

            // OI% change color
            const oiClass = oiChangePct > 0 ? 'gap-up' : (oiChangePct < 0 ? 'gap-down' : '');
            // Day change color
            const dayChgClass = dayChangePct > 0 ? 'gap-up' : (dayChangePct < 0 ? 'gap-down' : '');
            // PCR color
            const pcrClass = pcr > 1 ? 'gap-up' : (pcr < 0.7 ? 'gap-down' : '');

            rowHtml = `
                <td>${symbolCell}</td>
                <td>${stock.current_price.toFixed(2)}</td>
                <td class="${ivClass}">${ivPercentile.toFixed(1)}%</td>
                <td>${atmIv.toFixed(1)}%</td>
                <td class="${dayChgClass}">${dayChangePct.toFixed(2)}%</td>
                <td>${formatVolume(volume)}</td>
                <td class="${oiClass}">${oiChangePct.toFixed(1)}%</td>
                <td class="${pcrClass}">${pcr.toFixed(2)}</td>
                <td>${maxPain.toFixed(0)}</td>
            `;
        } else if (config.isDrsi) {
            // Delta-RSI table - 6 columns
            const rsi = Number(stock.rsi || 0);
            const drsi = Number(stock.drsi || 0);
            const signal = Number(stock.signal || 0);
            const trigger = stock.trigger || "";

            const triggerHtml = `<span style="color: #e11d48; font-weight: 700;">${trigger}</span>`;

            rowHtml = `
                <td>${symbolCell}</td>
                <td>${stock.current_price.toFixed(2)}</td>
                <td>${rsi.toFixed(1)}</td>
                <td>${drsi.toFixed(4)}</td>
                <td>${signal.toFixed(4)}</td>
                <td>${triggerHtml}</td>
            `;
        } else if (config.isCamarilla) {
            // Camarilla Reversal table - 6 columns: Symbol, Price, Camarilla Level (C-S3/C-R3), Monthly TC, Monthly BC, Monthly PP
            const levelVal = config.isBullish ? Number(stock.monthly_cam_s3 || 0) : Number(stock.monthly_cam_r3 || 0);
            const monthlyTc = Number(stock.monthly_tc || 0);
            const monthlyBc = Number(stock.monthly_bc || 0);
            const monthlyPp = Number(stock.monthly_pp || 0);

            rowHtml = `
                <td>${symbolCell}</td>
                <td>${stock.current_price.toFixed(2)}</td>
                <td style="font-weight: bold; color: ${config.isBullish ? '#10b981' : '#ef4444'};">${levelVal.toFixed(2)}</td>
                <td>${monthlyTc.toFixed(2)}</td>
                <td>${monthlyBc.toFixed(2)}</td>
                <td>${monthlyPp.toFixed(2)}</td>
            `;
        }

        const row = document.createElement('tr');
        row.innerHTML = rowHtml;
        tbody.appendChild(row);
    });
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

    // Update header classes for visual feedback
    table.querySelectorAll('th').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
    });
    header.classList.add(newDirection === 'asc' ? 'sort-asc' : 'sort-desc');

    const isAsc = newDirection === 'asc';

    // Determine numeric column range based on table type (small/Camarilla tables have fewer columns)
    const isSmallOrCamarillaTable = tableId === CONSTANTS.DOM_IDS.DRSI_BULLISH_TABLE || tableId === CONSTANTS.DOM_IDS.DRSI_BEARISH_TABLE ||
        tableId === CONSTANTS.DOM_IDS.DRSI_REVERSAL_BULLISH_TABLE || tableId === CONSTANTS.DOM_IDS.DRSI_REVERSAL_BEARISH_TABLE ||
        tableId === CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BULLISH_TABLE || tableId === CONSTANTS.DOM_IDS.CAMARILLA_CPR_REVERSAL_BEARISH_TABLE;
    const numericMaxCol = isSmallOrCamarillaTable ? 5 : 7; // indices 1..5 for small/Camarilla tables, 1..7 for large ones

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

