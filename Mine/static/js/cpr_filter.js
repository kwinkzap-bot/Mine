/**
 * cpr_filter.js
 * Handles fetching, displaying, and sorting CPR filter data.
 */

// Global state to track sort direction for each table
let sortDirection = {}; 

// Auto-load data when page loads
window.addEventListener('load', function() {
    document.getElementById('status-bar').textContent = '⏳ Loading initial data...';
    loadCPRData();
    // Set interval for continuous refresh (5 minutes = 300000 ms)
    setInterval(loadCPRData, 300000); 

    // Add sort listeners to both tables
    document.querySelectorAll('#aboveTable th').forEach(header => {
        header.addEventListener('click', () => {
            // columnIndex is stored in data-column-index
            sortTable('aboveTable', header.dataset.columnIndex);
        });
    });
    document.querySelectorAll('#belowTable th').forEach(header => {
        header.addEventListener('click', () => {
            // columnIndex is stored in data-column-index
            sortTable('belowTable', header.dataset.columnIndex);
        });
    });
});

/**
 * Fetches CPR data from the backend API using fetchJson utility.
 */
async function loadCPRData() {
    const statusBar = document.getElementById('status-bar');
    const isInitialLoad = statusBar.textContent.indexOf('Loading initial data') !== -1;
    
    statusBar.textContent = isInitialLoad ? '⏳ Loading initial data...' : `⏳ Refreshing data... (Last: ${new Date().toLocaleTimeString()})`;
    
    try {
        // Use the global fetchJson utility
        const data = await fetchJson('/api/cpr-filter');
        
        if (data.success) {
            // Display results for both above and below sections
            displayResults('above', data.above_results);
            displayResults('below', data.below_results);
            updateStats(data.above_count, data.below_count);
            
            // Hide the controls section if we have data to show results
            document.getElementById('controls').classList.add('results-hidden');
            
            // Show/hide above results section
            if (data.above_count > 0) {
                document.getElementById('aboveResults').classList.remove('results-hidden');
            } else {
                document.getElementById('aboveResults').classList.add('results-hidden');
            }
            
            // Show/hide below results section
            if (data.below_count > 0) {
                // Ensure there is a margin-top if the above results are hidden
                if (data.above_count === 0) {
                     document.getElementById('belowResults').classList.add('results-margin-top-only');
                } else {
                     document.getElementById('belowResults').classList.remove('results-margin-top-only');
                }
                document.getElementById('belowResults').classList.remove('results-hidden');
            } else {
                document.getElementById('belowResults').classList.add('results-hidden');
            }
            
            statusBar.textContent = `✅ Last update: ${new Date().toLocaleTimeString()}`;
        } else if (!data.needs_login) {
            // Only show error if it's not a session expiration handled by fetchJson
            statusBar.textContent = `❌ Error loading data: ${data.message}`;
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

        const statusClass = stock.status === 'WIDE CPR' ? 'status-wide' : 
                            (stock.status === 'NARROW CPR' ? 'status-narrow' : 
                            '');
        const dGapClass = stock.d_gap_percent > 0 ? 'gap-up' : (stock.d_gap_percent < 0 ? 'gap-down' : '');
        
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${stock.symbol}</td>
            <td>${stock.current_price.toFixed(2)}</td>
            <td>${dailyCpr.toFixed(2)}</td>
            <td>${weeklyCpr.toFixed(2)}</td>
            <td>${monthlyCpr.toFixed(2)}</td>
            <td class="${dGapClass}">${stock.d_gap_percent.toFixed(2)}%</td>
            <td>${(stock.w_gap_percent || 0).toFixed(2)}%</td>
            <td>${(stock.m_gap_percent || 0).toFixed(2)}%</td>
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
    document.getElementById('aboveCount').textContent = `(${aboveCount})`;
    document.getElementById('belowCount').textContent = `(${belowCount})`;
}

/**
 * Sorts a table by a given column index.
 * @param {string} tableId - The ID of the table ('aboveTable' or 'belowTable').
 * @param {string} columnIndexStr - The string column index from data-column-index.
 */
function sortTable(tableId, columnIndexStr) {
    const columnIndex = parseInt(columnIndexStr);
    const table = document.getElementById(tableId);
    const tbody = table.querySelector('tbody');
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