/**
 * cpr_filter.js
 * Handles fetching, displaying, and sorting CPR filter data.
 */

// Global state to track sort direction for each table
let sortDirection = {}; 

// Auto-load data when page loads
window.addEventListener('load', function() {
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
    
    const statusBar = document.getElementById(CONSTANTS.DOM_IDS.STATUS_BAR);
    if (!statusBar) {
        console.error('status-bar element not found');
        return;
    }
    
    statusBar.textContent = '⏳ Loading initial data...';
    loadCPRData();
    // Set interval for continuous refresh
    setInterval(loadCPRData, CONSTANTS.TIMEOUTS.CPR_REFRESH_INTERVAL); 

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
});

/**
 * Fetches CPR data from the backend API using fetchJson utility.
 */
async function loadCPRData() {
    const statusBar = document.getElementById('status-bar');
    
    // Check if statusBar exists
    if (!statusBar) {
        console.error('Status bar not found');
        return;
    }
    
    const isInitialLoad = statusBar.textContent.indexOf('Loading initial data') !== -1;
    
    statusBar.textContent = isInitialLoad ? '⏳ Loading initial data...' : `⏳ Refreshing data... (Last: ${new Date().toLocaleTimeString()})`;
    
    try {
        // Use the global fetchJson utility
        const response = await fetchJson('/api/cpr-filter');
        
        console.log('CPR Filter API Response:', response);
        
        if (response && response.success) {
            // Process the API response
            // API returns { success: true, data: [...] }
            const allData = response.data || [];
            
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
            
            console.log(`Data loaded - Above: ${aboveCount}, Below: ${belowCount}`);
            
            // Display results
            displayResults('above', aboveResults);
            displayResults('below', belowResults);
            updateStats(aboveCount, belowCount);
            
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
            
            statusBar.textContent = `✅ Last update: ${new Date().toLocaleTimeString()} | Above: ${aboveCount}, Below: ${belowCount}`;
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
 * @param {string} type - 'above' or 'below'.
 * @param {Array<Object>} results - The list of stock objects.
 */
function displayResults(type, results) {
    const tbody = document.getElementById(`${type}Body`);
    const container = document.getElementById(`${type}Results`);
    const countSpan = document.getElementById(`${type}Count`);
    
    // Check if all required elements exist
    if (!tbody || !container || !countSpan) {
        console.error(`Missing elements for type '${type}':`, { tbody: !!tbody, container: !!container, countSpan: !!countSpan });
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

    results.forEach(stock => {
        // Determine which CPR levels to display based on the table type
        const dailyCpr = (type === 'above' ? stock.daily_tc : stock.daily_bc) || 0;
        const weeklyCpr = (type === 'above' ? stock.weekly_tc : stock.weekly_bc) || 0;
        const monthlyCpr = (type === 'above' ? stock.monthly_tc : stock.monthly_bc) || 0;

        // Get gap values (handle both new and old field names)
        const dGap = stock.d_gap_percent !== undefined ? stock.d_gap_percent : (stock.d_gap || 0);
        const wGap = stock.w_gap_percent !== undefined ? stock.w_gap_percent : (stock.w_gap || 0);
        const mGap = stock.m_gap_percent !== undefined ? stock.m_gap_percent : (stock.m_gap || 0);

        const statusClass = stock.status === 'WIDE CPR' ? 'status-wide' : 
                            (stock.status === 'NARROW CPR' ? 'status-narrow' : 
                            '');
        const dGapClass = dGap > 0 ? 'gap-up' : (dGap < 0 ? 'gap-down' : '');
        
        const row = document.createElement('tr');
        
        // Create TradingView link for symbol
        const tradingViewUrl = `https://in.tradingview.com/chart/?symbol=NSE:${stock.symbol}`;
        const watchlistUrl = `https://in.tradingview.com/watchlist/`;
        
        // Create symbol cell with chart link and watchlist button
        const symbolCell = `
            <a href="${tradingViewUrl}" target="_blank" rel="noopener noreferrer" style="color: #667eea; text-decoration: none; cursor: pointer; font-weight: 500;">${stock.symbol}</a>
            <button class="watchlist-btn" data-symbol="${stock.symbol}" title="Add to TradingView Watchlist" style="margin-left: 8px; padding: 2px 6px; background: #667eea; color: white; border: none; border-radius: 3px; cursor: pointer; font-size: 11px; font-weight: bold;">+</button>
        `;
        
        row.innerHTML = `
            <td>${symbolCell}</td>
            <td>${stock.current_price.toFixed(2)}</td>
            <td>${dailyCpr.toFixed(2)}</td>
            <td>${weeklyCpr.toFixed(2)}</td>
            <td>${monthlyCpr.toFixed(2)}</td>
            <td class="${dGapClass}">${dGap.toFixed(2)}%</td>
            <td>${wGap.toFixed(2)}%</td>
            <td>${mGap.toFixed(2)}%</td>
            <td class="${statusClass}">${stock.status}</td>
        `;
        tbody.appendChild(row);
    });
}

/**
 * Updates the header counts for above/below CPR.
 * @param {number} aboveCount 
 * @param {number} belowCount 
 */
function updateStats(aboveCount, belowCount) {
    const aboveCountEl = document.getElementById('aboveCount');
    const belowCountEl = document.getElementById('belowCount');
    
    if (aboveCountEl) {
        aboveCountEl.textContent = `(${aboveCount})`;
    }
    if (belowCountEl) {
        belowCountEl.textContent = `(${belowCount})`;
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
    
    // Sort rows
    rows.sort((a, b) => {
        // Remove currency symbols, commas, and % for numeric comparison
        const aCell = a.cells[columnIndex].textContent.replace(/[₹%,]/g, '').trim();
        const bCell = b.cells[columnIndex].textContent.replace(/[₹%,]/g, '').trim();
        
        const aNum = parseFloat(aCell);
        const bNum = parseFloat(bCell);
        
        // Check if both are numbers (for price and percentage columns)
        // Column indices 1 to 7 are numeric columns based on cpr_filter.html
        if (!isNaN(aNum) && !isNaN(bNum) && columnIndex >= 1 && columnIndex <= 7) {
            return isAsc ? aNum - bNum : bNum - aNum;
        } else {
            // String comparison (for Symbol and Status columns)
            return isAsc ? aCell.localeCompare(bCell) : bCell.localeCompare(aCell);
        }
    });
    
    // Re-append sorted rows to the tbody
    rows.forEach(row => tbody.appendChild(row));
}

/**
 * Delegate click handler for watchlist buttons
 * Uses event delegation to handle dynamically added buttons
 */
document.addEventListener('click', function(e) {
    if (e.target && e.target.classList.contains('watchlist-btn')) {
        const symbol = e.target.dataset.symbol;
        handleWatchlistClick(symbol, e.target);
    }
});

/**
 * Handles adding symbol to watchlist
 * Opens TradingView watchlist and shows feedback to user
 * @param {string} symbol - The stock symbol to add
 * @param {HTMLElement} button - The button element that was clicked
 */
function handleWatchlistClick(symbol, button) {
    // Save to local storage for user's watchlist tracker
    saveToLocalWatchlist(symbol);
    
    // Open TradingView watchlist in new tab
    const watchlistUrl = `https://in.tradingview.com/watchlist/`;
    window.open(watchlistUrl, '_blank');
    
    // Visual feedback: change button color briefly
    const originalBg = button.style.background;
    const originalText = button.textContent;
    
    button.style.background = '#4caf50';
    button.textContent = '✓';
    button.disabled = true;
    
    setTimeout(() => {
        button.style.background = originalBg;
        button.textContent = originalText;
        button.disabled = false;
    }, 1500);
    
    // Show notification
    showWatchlistNotification(symbol);
}

/**
 * Saves symbol to browser's local storage for personal tracking
 * @param {string} symbol - The stock symbol to save
 */
function saveToLocalWatchlist(symbol) {
    try {
        let watchlist = JSON.parse(localStorage.getItem('cprWatchlist') || '[]');
        
        // Add symbol if not already present
        if (!watchlist.includes(symbol)) {
            watchlist.unshift(symbol); // Add to beginning
            
            // Keep only last 50 symbols
            if (watchlist.length > 50) {
                watchlist = watchlist.slice(0, 50);
            }
            
            localStorage.setItem('cprWatchlist', JSON.stringify(watchlist));
        }
    } catch (error) {
        console.error('Error saving to local watchlist:', error);
    }
}

/**
 * Shows a notification when symbol is added to watchlist
 * @param {string} symbol - The symbol that was added
 */
function showWatchlistNotification(symbol) {
    // Create a simple notification element
    const notification = document.createElement('div');
    notification.className = 'watchlist-notification';
    notification.textContent = `📌 ${symbol} added to watchlist! Opening TradingView...`;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: #4caf50;
        color: white;
        padding: 12px 20px;
        border-radius: 4px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        z-index: 10000;
        font-size: 14px;
        animation: slideIn 0.3s ease-out;
    `;
    
    document.body.appendChild(notification);
    
    // Remove notification after 3 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}