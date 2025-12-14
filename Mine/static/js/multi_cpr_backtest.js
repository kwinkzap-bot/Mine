/**
 * multi_cpr_backtest.js
 * Handles the logic for running the Multi-CPR Backtest, loading stocks, 
 * and displaying/sorting results.
 */

// Global state variables
const DOM = {};
let backtestData = [];
let sortDirection = {};

// Helper to format currency (Indian Rupee format)
function formatCurrency(value) {
    if (value === null || value === undefined || isNaN(value)) return '-';
    // Use toLocaleString for Indian Rupee format
    return '₹' + parseFloat(value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Helper to format percentage
function formatPercentage(value) {
    if (value === null || value === undefined || isNaN(value)) return '-';
    // Ensure value is a number, fix to 2 decimal places, and add percent symbol
    return parseFloat(value).toFixed(2) + '%';
}

// Function to cache DOM elements
function cacheDOMElements() {
    DOM.symbolSelect = document.getElementById('symbol');
    DOM.timeframeSelect = document.getElementById('timeframe');
    DOM.fromDateInput = document.getElementById('fromDate');
    DOM.toDateInput = document.getElementById('toDate');
    DOM.backtestBtn = document.getElementById('backtestBtn');
    DOM.loadingSpan = document.getElementById('loading');
    DOM.noDataDiv = document.getElementById('noData');
    DOM.resultsSection = document.getElementById('resultsSection');
    DOM.tradesBody = document.getElementById('tradesBody');
}

/**
 * Populates the stock select dropdown.
 * @param {Array<string>} stocks - List of stock symbols.
 */
function populateStocks(stocks) {
    if (!DOM.symbolSelect) return;
    DOM.symbolSelect.innerHTML = ''; // Clear existing options
    
    // Add default options first
    ['NIFTY', 'BANKNIFTY', 'FINNIFTY'].forEach(symbol => {
        const option = document.createElement('option');
        option.value = symbol;
        option.textContent = symbol;
        DOM.symbolSelect.appendChild(option);
    });

    // Add F&O stocks, excluding the ones already added
    stocks.forEach(symbol => {
        if (!['NIFTY', 'BANKNIFTY', 'FINNIFTY'].includes(symbol)) {
            const option = document.createElement('option');
            option.value = symbol;
            option.textContent = symbol;
            DOM.symbolSelect.appendChild(option);
        }
    });

    // Auto-select NIFTY or the first available option
    DOM.symbolSelect.value = DOM.symbolSelect.querySelector('option[value="NIFTY"]') ? 'NIFTY' : DOM.symbolSelect.options[0]?.value || '';
}

/**
 * Fetches F&O stock list from API, uses cache if available.
 */
async function loadStocks() {
    if (!DOM.symbolSelect) return;

    // Check localStorage first
    const cachedStocks = localStorage.getItem('fo_stocks');
    if (cachedStocks) {
        try {
            const { stocks, timestamp } = JSON.parse(cachedStocks);
            // Cache valid for 1 day
            if (Date.now() - timestamp < 24 * 60 * 60 * 1000) { 
                populateStocks(stocks);
                return;
            }
        } catch (e) {
            console.warn('Invalid F&O stocks cache, fetching new data.');
            localStorage.removeItem('fo_stocks');
        }
    }
    
    // Fetch from API if not in cache or cache is expired/invalid
    DOM.symbolSelect.innerHTML = '<option value="">Loading...</option>';
    try {
        const data = await fetchJson('/api/fo-stocks');
        
        if (data.success) {
            const stocks = data.stocks;
            localStorage.setItem('fo_stocks', JSON.stringify({ stocks, timestamp: Date.now() }));
            populateStocks(stocks);
        } else {
            showNotification(data.message || 'Failed to load F&O stocks.', 'error');
            populateStocks([]); // Populate with just NIFTY/BANKNIFTY/FINNIFTY if API fails
        }
    } catch (error) {
        console.error('Error loading stocks:', error);
        showNotification('Network error while loading stocks.', 'error');
        populateStocks([]);
    }
}

/**
 * Runs the backtest by fetching data from the API.
 */
async function runBacktest() {
    if (!DOM.symbolSelect.value || !DOM.fromDateInput.value || !DOM.toDateInput.value) {
        showNotification('Please select a symbol and enter valid dates.', 'warning');
        return;
    }
    
    DOM.backtestBtn.disabled = true;
    DOM.loadingSpan.style.display = 'inline';
    DOM.resultsSection.style.display = 'none';
    DOM.noDataDiv.style.display = 'none';
    DOM.tradesBody.innerHTML = ''; // Clear table content
    backtestData = [];

    const formData = {
        symbol: DOM.symbolSelect.value,
        timeframe: DOM.timeframeSelect.value,
        from_date: DOM.fromDateInput.value,
        to_date: DOM.toDateInput.value
    };
    
    try {
        const data = await fetchJson('/api/multi-cpr-backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        });

        if (data.success) {
            backtestData = data.results;
            displayResults(backtestData, data.stats);
            showNotification('Backtest completed successfully.', 'success');
        } else {
            DOM.noDataDiv.textContent = data.message || 'Error running backtest.';
            DOM.noDataDiv.style.display = 'block';
            showNotification(data.message || 'Error running backtest.', 'error');
        }
    } catch (error) {
        console.error('Backtest API error:', error);
        DOM.noDataDiv.textContent = 'Network or API error while running backtest.';
        DOM.noDataDiv.style.display = 'block';
    } finally {
        DOM.backtestBtn.disabled = false;
        DOM.loadingSpan.style.display = 'none';
    }
}

/**
 * Displays backtest results and statistics.
 * @param {Array<object>} results - The trade results array.
 * @param {object} stats - The statistics object.
 */
function displayResults(results, stats) {
    if (results.length === 0) {
        DOM.noDataDiv.textContent = 'No trades found for the selected criteria.';
        DOM.noDataDiv.style.display = 'block';
        return;
    }

    DOM.noDataDiv.style.display = 'none';
    DOM.resultsSection.style.display = 'block';

    // Update Stats Cards
    document.getElementById('totalTrades').textContent = stats.total_trades;
    document.getElementById('profitableTrades').textContent = stats.profitable_trades;
    document.getElementById('lossTrades').textContent = stats.loss_trades;
    document.getElementById('winRate').textContent = formatPercentage(stats.win_rate);
    document.getElementById('totalPnL').textContent = formatCurrency(stats.total_pnl);
    document.getElementById('averagePnLPerTrade').textContent = formatCurrency(stats.avg_pnl_per_trade);
    document.getElementById('maxLoss').textContent = formatCurrency(stats.max_loss);
    document.getElementById('maxWin').textContent = formatCurrency(stats.max_win);
    document.getElementById('maxConsecutiveWins').textContent = stats.max_consecutive_wins;
    document.getElementById('maxConsecutiveLosses').textContent = stats.max_consecutive_losses;
    document.getElementById('maxConsecutiveWinsPnl').textContent = formatCurrency(stats.max_consecutive_wins_pnl);
    document.getElementById('maxConsecutiveLossesPnl').textContent = formatCurrency(stats.max_consecutive_losses_pnl);

    // Apply color to Total P&L
    const totalPnLEl = document.getElementById('totalPnL');
    totalPnLEl.style.color = stats.total_pnl >= 0 ? 'green' : 'red';
    
    // Populate Trade Results Table
    results.forEach(trade => {
        const row = DOM.tradesBody.insertRow();
        const pnlClass = trade.pnl > 0 ? 'pnl-positive' : (trade.pnl < 0 ? 'pnl-negative' : '');
        
        row.innerHTML = `
            <td>${trade.entry_date}</td>
            <td>${trade.signal_type}</td>
            <td>${formatCurrency(trade.entry_price).replace('₹', '')}</td>
            <td>${trade.exit_date}</td>
            <td>${formatCurrency(trade.exit_price).replace('₹', '')}</td>
            <td>${trade.exit_type}</td>
            <td class="${pnlClass}">${formatCurrency(trade.pnl)}</td>
            <td class="${pnlClass}">${formatPercentage(trade.pnl_percent)}</td>
        `;
    });
}

/**
 * Sorts the table rows based on the selected column index and data type.
 * This function is exposed globally to be called from the 'onclick' attribute in the HTML.
 * @param {number} columnIndex - The index of the column to sort (0-based).
 * @param {string} type - The data type ('string' or 'number').
 */
function sortTable(columnIndex, type) {
    const table = document.querySelector('table');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const headers = table.querySelectorAll('th');
    const currentHeader = headers[columnIndex];

    if (!currentHeader) return;

    // Determine sort direction
    const currentDirection = sortDirection[columnIndex];
    const newDirection = currentDirection === 'asc' ? 'desc' : 'asc';
    sortDirection[columnIndex] = newDirection;
    
    // Remove sort classes from all headers
    headers.forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
    
    // Add sort class to current header
    currentHeader.classList.add(newDirection === 'asc' ? 'sort-asc' : 'sort-desc');
    
    const isAsc = newDirection === 'asc';

    // Sort rows
    rows.sort((a, b) => {
        let aValue = a.cells[columnIndex].textContent.trim();
        let bValue = b.cells[columnIndex].textContent.trim();
        
        if (type === 'number') {
            // Remove currency symbols, commas, and % for numeric comparison
            aValue = parseFloat(aValue.replace(/[₹,%]/g, '')) || 0;
            bValue = parseFloat(bValue.replace(/[₹,%]/g, '')) || 0;
            
            // Treat non-numeric/empty cells ('-') as lowest value for ascending, highest for descending
            const aIsEmpty = a.cells[columnIndex].textContent.trim() === '-';
            const bIsEmpty = b.cells[columnIndex].textContent.trim() === '-';
            
            if (aIsEmpty && bIsEmpty) return 0;
            if (aIsEmpty) return isAsc ? -1 : 1;
            if (bIsEmpty) return isAsc ? 1 : -1;
            
            return isAsc ? aValue - bValue : bValue - aValue;
        } else {
            // String comparison
            return isAsc ? aValue.localeCompare(bValue) : bValue.localeCompare(aValue);
        }
    });
    
    // Re-append sorted rows to the tbody
    rows.forEach(row => tbody.appendChild(row));
}

// Global exposure for HTML inline onclick
window.runBacktest = runBacktest;
window.sortTable = sortTable;

// Initialization on page load
document.addEventListener('DOMContentLoaded', function() {
    // Check if fetchJson utility is available
    if (typeof fetchJson !== 'function') {
        console.error("fetchJson utility not found. Ensure app.js is loaded first.");
        return;
    }
    
    cacheDOMElements();

    // Set default dates
    const today = new Date();
    // Set 'to date' to today
    DOM.toDateInput.value = today.toISOString().split('T')[0];
    
    // Set 'from date' to 7 days ago
    const oneWeekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
    DOM.fromDateInput.value = oneWeekAgo.toISOString().split('T')[0];
    
    loadStocks();
});