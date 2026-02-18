/**
 * cpr_filter.js
 * Handles fetching, displaying, and sorting CPR filter data.
 */

// Global state to track sort direction for each table
let sortDirection = {};

// Auto-load data when page loads
window.addEventListener('load', function () {
    // Debug: Log all expected elements
    console.log('Checking for required DOM elements...');
    console.log('status-bar:', !!document.getElementById('status-bar'));
    console.log('aboveResults:', !!document.getElementById('aboveResults'));
    console.log('aboveBody:', !!document.getElementById('aboveBody'));
    console.log('aboveCount:', !!document.getElementById('aboveCount'));
    console.log('aboveTable:', !!document.getElementById('aboveTable'));
    console.log('belowResults:', !!document.getElementById('belowResults'));
    console.log('belowBody:', !!document.getElementById('belowBody'));
    console.log('belowCount:', !!document.getElementById('belowCount'));
    console.log('belowTable:', !!document.getElementById('belowTable'));
    console.log('crossAboveResults:', !!document.getElementById('crossAboveResults'));
    console.log('crossAboveBody:', !!document.getElementById('crossAboveBody'));
    console.log('crossAboveCount:', !!document.getElementById('crossAboveCount'));
    console.log('crossAboveTable:', !!document.getElementById('crossAboveTable'));
    console.log('crossBelowResults:', !!document.getElementById('crossBelowResults'));
    console.log('crossBelowBody:', !!document.getElementById('crossBelowBody'));
    console.log('crossBelowCount:', !!document.getElementById('crossBelowCount'));
    console.log('crossBelowTable:', !!document.getElementById('crossBelowTable'));

    let statusBar = document.getElementById('status-text');

    // Robust fallback handling
    if (!statusBar) {
        console.warn('status-text element not found. Checking for status-bar container...');
        const container = document.getElementById('status-bar');

        if (container) {
            // Check if we are in the new layout (has controls) or old layout
            const controls = container.querySelector('.controls');

            if (controls) {
                // New layout but missing span? Create it.
                console.info('Found controls but missing status-text. Creating span.');
                statusBar = document.createElement('span');
                statusBar.id = 'status-text';
                container.insertBefore(statusBar, controls);
            } else {
                // Old layout or empty container -> use container as status target
                console.info('Using status-bar container as fallback status target.');
                statusBar = container;
            }
        } else {
            console.error('status-bar element not found');
            return;
        }
    }

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
            loadCPRData();
        });
    }

    // Avoid double-triggering the API when the scheduler is already running during market hours
    if (schedulerActive && schedulerMarketOpen) {
        statusBar.textContent = '⏳ Scheduler active - waiting for next run...';
    } else {
        statusBar.textContent = '⏳ Loading initial data...';
        loadCPRData();
    }

    // Set interval for continuous refresh - only if scheduler is not already running
    if (!schedulerActive) {
        setInterval(loadCPRData, CONSTANTS.TIMEOUTS.CPR_REFRESH_INTERVAL);
    }

    // Add sort listeners to both tables
    document.querySelectorAll(`#${CONSTANTS.DOM_IDS.ABOVE_TABLE} th`).forEach(header => {
        header.addEventListener('click', () => {
            // columnIndex is stored in data-column-index
            sortTable(CONSTANTS.DOM_IDS.ABOVE_TABLE, header.dataset.columnIndex);
        });
    });
    document.querySelectorAll(`#${CONSTANTS.DOM_IDS.BELOW_TABLE} th`).forEach(header => {
        header.addEventListener('click', () => {
            // columnIndex is stored in data-column-index
            sortTable(CONSTANTS.DOM_IDS.BELOW_TABLE, header.dataset.columnIndex);
        });
    });
    document.querySelectorAll(`#${CONSTANTS.DOM_IDS.CROSS_ABOVE_TABLE} th`).forEach(header => {
        header.addEventListener('click', () => {
            sortTable(CONSTANTS.DOM_IDS.CROSS_ABOVE_TABLE, header.dataset.columnIndex);
        });
    });
    document.querySelectorAll(`#${CONSTANTS.DOM_IDS.CROSS_BELOW_TABLE} th`).forEach(header => {
        header.addEventListener('click', () => {
            sortTable(CONSTANTS.DOM_IDS.CROSS_BELOW_TABLE, header.dataset.columnIndex);
        });
    });

    // Add sort listeners for Reversal tables
    const bullishTable = document.getElementById('bullishReversalTable');
    if (bullishTable) {
        bullishTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable('bullishReversalTable', header.dataset.columnIndex);
            });
        });
    }

    const bearishTable = document.getElementById('bearishReversalTable');
    if (bearishTable) {
        bearishTable.querySelectorAll('th').forEach(header => {
            header.addEventListener('click', () => {
                sortTable('bearishReversalTable', header.dataset.columnIndex);
            });
        });
    }
});

/**
 * Fetches CPR data from the backend API using fetchJson utility.
 */
async function loadCPRData() {
    let statusBar = document.getElementById('status-text');
    // Check if statusBar exists, fallback to status-bar container
    if (!statusBar) {
        statusBar = document.getElementById('status-bar');
    }

    if (!statusBar) {
        console.error('Status bar not found');
        return;
    }

    const isInitialLoad = statusBar.textContent.indexOf('Loading initial data') !== -1;

    statusBar.textContent = isInitialLoad ? '⏳ Loading initial data...' : `⏳ Refreshing data... (Last: ${new Date().toLocaleTimeString()})`;

    try {
        // Get selected date
        const datePicker = document.getElementById('cprDateFilter');
        const selectedDate = datePicker ? datePicker.value : null;

        // Use the global fetchJson utility
        let url = '/api/cpr-filter';
        if (selectedDate) {
            url += `?date=${selectedDate}`;
        }

        const response = await fetchJson(url);

        console.log('CPR Filter API Response:', response);

        if (response && response.success) {
            // Process the API response
            // API returns { success: true, data: [...], weekly_cross: { crossed_above: [...], crossed_below: [...] } }
            const allData = response.data || [];
            const weeklyCross = response.weekly_cross || {};
            const crossAboveResults = weeklyCross.crossed_above || [];
            const crossBelowResults = weeklyCross.crossed_below || [];

            const reversal = response.reversal || {};
            const bullishReversalResults = reversal.bullish || [];
            const bearishReversalResults = reversal.bearish || [];

            // Split data into above and below CPR
            const aboveResults = [];
            const belowResults = [];

            allData.forEach(stock => {
                // Transform API field names to match expected format
                const transformedStock = {
                    symbol: stock.symbol,
                    current_price: stock.current_price,
                    daily_tc: stock.daily_tc,
                    daily_bc: stock.daily_bc,
                    weekly_tc: stock.weekly_tc,
                    weekly_bc: stock.weekly_bc,
                    monthly_tc: stock.monthly_tc,
                    monthly_bc: stock.monthly_bc,
                    status: stock.status,
                    d_gap_percent: stock.d_gap,
                    w_gap_percent: stock.w_gap,
                    m_gap_percent: stock.m_gap
                };

                // Categorize by status (ABOVE or BELOW in status field)
                if (stock.status && stock.status.includes('ABOVE')) {
                    aboveResults.push(transformedStock);
                } else if (stock.status && stock.status.includes('BELOW')) {
                    belowResults.push(transformedStock);
                }
            });

            const aboveCount = aboveResults.length;
            const belowCount = belowResults.length;
            const crossAboveCount = crossAboveResults.length;
            const crossBelowCount = crossBelowResults.length;
            const bullishReversalCount = bullishReversalResults.length;
            const bearishReversalCount = bearishReversalResults.length;

            console.log(`Data loaded - Above: ${aboveCount}, Below: ${belowCount}, Cross Above: ${crossAboveCount}, Cross Below: ${crossBelowCount}, Bullish Rev: ${bullishReversalCount}, Bearish Rev: ${bearishReversalCount}`);

            // Display results
            displayResults('above', aboveResults);
            displayResults('below', belowResults);
            displayResults('crossAbove', crossAboveResults);
            displayResults('crossBelow', crossBelowResults);
            displayResults('bullishReversal', bullishReversalResults);
            displayResults('bearishReversal', bearishReversalResults);

            updateStats(aboveCount, belowCount, crossAboveCount, crossBelowCount, bullishReversalCount, bearishReversalCount);

            // Hide the controls section if we have data to show results
            const controls = document.getElementById('controls');
            if (controls) {
                controls.classList.add('results-hidden');
            }

            // Show/hide above results section
            const aboveResultsDiv = document.getElementById('aboveResults');
            if (aboveResultsDiv) {
                if (aboveCount > 0) {
                    aboveResultsDiv.classList.remove('results-hidden');
                } else {
                    aboveResultsDiv.classList.add('results-hidden');
                }
            }

            // Show/hide below results section
            const belowResultsDiv = document.getElementById('belowResults');
            if (belowResultsDiv) {
                if (belowCount > 0) {
                    // Ensure there is a margin-top if the above results are hidden
                    if (aboveCount === 0) {
                        belowResultsDiv.classList.add('results-margin-top-only');
                    } else {
                        belowResultsDiv.classList.remove('results-margin-top-only');
                    }
                    belowResultsDiv.classList.remove('results-hidden');
                } else {
                    belowResultsDiv.classList.add('results-hidden');
                }
            }

            // Show/hide cross above results section
            const crossAboveDiv = document.getElementById('crossAboveResults');
            if (crossAboveDiv) {
                if (crossAboveCount > 0) {
                    crossAboveDiv.classList.remove('results-hidden');
                } else {
                    crossAboveDiv.classList.add('results-hidden');
                }
            }

            // Show/hide cross below results section
            const crossBelowDiv = document.getElementById('crossBelowResults');
            if (crossBelowDiv) {
                if (crossBelowCount > 0) {
                    crossBelowDiv.classList.remove('results-hidden');
                } else {
                    crossBelowDiv.classList.add('results-hidden');
                }
            }

            // Show/hide bullish reversal results section
            const bullishReversalDiv = document.getElementById('bullishReversalResults');
            if (bullishReversalDiv) {
                if (bullishReversalCount > 0) {
                    bullishReversalDiv.classList.remove('results-hidden');
                } else {
                    bullishReversalDiv.classList.add('results-hidden');
                }
            }

            // Show/hide bearish reversal results section
            const bearishReversalDiv = document.getElementById('bearishReversalResults');
            if (bearishReversalDiv) {
                if (bearishReversalCount > 0) {
                    bearishReversalDiv.classList.remove('results-hidden');
                } else {
                    bearishReversalDiv.classList.add('results-hidden');
                }
            }

            statusBar.textContent = `✅ Last update: ${new Date().toLocaleTimeString()} | Above: ${aboveCount}, Below: ${belowCount}, Cross Above: ${crossAboveCount}, Cross Below: ${crossBelowCount}, Bullish Rev: ${bullishReversalCount}, Bearish Rev: ${bearishReversalCount}`;
        } else if (response && !response.needs_login) {
            // Only show error if it's not a session expiration handled by fetchJson
            const errorMsg = response.message || 'Unknown error';
            statusBar.textContent = `❌ Error loading data: ${errorMsg}`;
            console.error('API Error:', response);
        }
    } catch (error) {
        console.error('Error fetching CPR data:', error);
        statusBar.textContent = `❌ Network Error: ${error.message}`;
    }
}

/**
 * Populates the results table with data.
 * @param {string} type - 'above', 'below', 'crossAbove', 'crossBelow', 'bullishReversal', 'bearishReversal'.
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
        above: { dailyKey: 'daily_tc', weeklyKey: 'weekly_tc', monthlyKey: 'monthly_tc', showGaps: true },
        below: { dailyKey: 'daily_bc', weeklyKey: 'weekly_bc', monthlyKey: 'monthly_bc', showGaps: true },
        crossAbove: { dailyKey: 'daily_tc', weeklyKey: 'weekly_tc', monthlyKey: 'monthly_tc', showGaps: false },
        crossBelow: { dailyKey: 'daily_bc', weeklyKey: 'weekly_bc', monthlyKey: 'monthly_bc', showGaps: false },
        bullishReversal: { col3: 'monthly_tc', col4: 'monthly_s1', col5: 'prev_month_low', showGaps: true },
        bearishReversal: { col3: 'monthly_bc', col4: 'monthly_r1', col5: 'prev_month_high', showGaps: true }
    };
    const config = tableConfig[type] || tableConfig.above;
    const showGaps = config.showGaps;

    results.forEach(stock => {
        let rowHtml = '';

        // Create TradingView link for symbol
        const tradingViewUrl = `https://in.tradingview.com/chart/?symbol=NSE:${stock.symbol}`;
        const symbolCell = `<a href="${tradingViewUrl}" target="_blank" rel="noopener noreferrer" class="symbol-link">${stock.symbol}</a>`;

        if (type === 'bullishReversal' || type === 'bearishReversal') {
            const val3 = Number(stock[config.col3] || 0);
            const val4 = Number(stock[config.col4] || 0);
            const val5 = Number(stock[config.col5] || 0);
            const mGap = stock.m_gap !== undefined ? stock.m_gap : (stock.m_gap_percent || 0);
            const gapClass = mGap > 0 ? 'gap-up' : (mGap < 0 ? 'gap-down' : '');

            rowHtml = `
                <td>${symbolCell}</td>
                <td>${stock.current_price.toFixed(2)}</td>
                <td>${val3.toFixed(2)}</td>
                <td>${val4.toFixed(2)}</td>
                <td>${val5.toFixed(2)}</td>
                <td class="${gapClass}">${mGap.toFixed(2)}%</td>
            `;
        } else {
            // Determine which CPR levels to display based on the table type
            const dailyCpr = Number(stock[config.dailyKey] || 0);
            const weeklyCpr = Number(stock[config.weeklyKey] || 0);
            const monthlyCpr = Number(stock[config.monthlyKey] || 0);

            // Get gap values
            const dGap = stock.d_gap_percent !== undefined ? stock.d_gap_percent : (stock.d_gap || 0);
            const wGap = stock.w_gap_percent !== undefined ? stock.w_gap_percent : (stock.w_gap || 0);
            const mGap = stock.m_gap_percent !== undefined ? stock.m_gap_percent : (stock.m_gap || 0);
            const dGapClass = dGap > 0 ? 'gap-up' : (dGap < 0 ? 'gap-down' : '');

            if (showGaps) {
                rowHtml = `
                    <td>${symbolCell}</td>
                    <td>${stock.current_price.toFixed(2)}</td>
                    <td>${dailyCpr.toFixed(2)}</td>
                    <td>${weeklyCpr.toFixed(2)}</td>
                    <td>${monthlyCpr.toFixed(2)}</td>
                    <td class="${dGapClass}">${dGap.toFixed(2)}%</td>
                    <td>${wGap.toFixed(2)}%</td>
                    <td>${mGap.toFixed(2)}%</td>
                `;
            } else {
                rowHtml = `
                    <td>${symbolCell}</td>
                    <td>${stock.current_price.toFixed(2)}</td>
                    <td>${dailyCpr.toFixed(2)}</td>
                    <td>${weeklyCpr.toFixed(2)}</td>
                    <td>${monthlyCpr.toFixed(2)}</td>
                `;
            }
        }

        const row = document.createElement('tr');
        row.innerHTML = rowHtml;
        tbody.appendChild(row);
    });
}

/**
 * Updates the header counts for above/below CPR.
 * @param {number} aboveCount 
 * @param {number} belowCount 
 */
function updateStats(aboveCount, belowCount, crossAboveCount = 0, crossBelowCount = 0) {
    const aboveCountEl = document.getElementById('aboveCount');
    const belowCountEl = document.getElementById('belowCount');
    const crossAboveCountEl = document.getElementById('crossAboveCount');
    const crossBelowCountEl = document.getElementById('crossBelowCount');

    if (aboveCountEl) {
        aboveCountEl.textContent = `(${aboveCount})`;
    }
    if (belowCountEl) {
        belowCountEl.textContent = `(${belowCount})`;
    }
    if (crossAboveCountEl) {
        crossAboveCountEl.textContent = `(${crossAboveCount})`;
    }
    if (crossBelowCountEl) {
        crossBelowCountEl.textContent = `(${crossBelowCount})`;
    }
}

/**
 * Sorts a table by a given column index.
 * @param {string} tableId - The ID of the table ('aboveTable' or 'belowTable').
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

    // Determine numeric column range based on table type (cross tables have fewer columns and no gaps)
    const isCrossTable = tableId === CONSTANTS.DOM_IDS.CROSS_ABOVE_TABLE || tableId === CONSTANTS.DOM_IDS.CROSS_BELOW_TABLE;
    const numericMaxCol = isCrossTable ? 4 : 7; // indices 1..7 numeric for all tables (no Status column)

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

