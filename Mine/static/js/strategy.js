/**
 * strategy.app.js - Pure Vanilla JavaScript application logic for the Strategy Backtest page.
 * Replaces the AngularJS StrategyController.
 * Requires strategy.service.js and notifications.js.
 */
document.addEventListener('DOMContentLoaded', function() {
    'use strict';

    // --- DOM Element Caching ---
    // Note: This relies on strategy.html being updated to use proper IDs (see instructions below)
    const symbolSelect = document.getElementById('symbol');
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');
    const backtestForm = document.getElementById('backtestForm');
    const statusDiv = document.getElementById('status-message'); // New ID for the status element
    const resultsTable = document.getElementById('resultsTable'); // Assuming the table is wrapped in an element with this ID
    const resultsBody = document.getElementById('tradesBody');

    // --- Helper Functions ---

    /** Formats a Date object to 'YYYY-MM-DD' string. */
    function formatDate(date) {
        const d = new Date(date);
        let month = '' + (d.getMonth() + 1);
        let day = '' + d.getDate();
        const year = d.getFullYear();

        if (month.length < 2) month = '0' + month;
        if (day.length < 2) day = '0' + day;

        return [year, month, day].join('-');
    }
    
    /** Formats a number to Indian Rupee currency string. */
    function formatCurrency(value) {
        if (value === null || value === undefined || isNaN(value)) return '-';
        return parseFloat(value).toLocaleString('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 });
    }

    /** Formats a value to 2 decimal places or returns '-' */
    const toFixedOrDash = (val) => val !== null && val !== undefined && !isNaN(val) ? parseFloat(val).toFixed(2) : '-';
    
    /** Returns value or '-' if null/undefined */
    const valOrDash = (val) => val !== null && val !== undefined ? val : '-';

    /** Calculates and formats backtest statistics. */
    function calculateStats(results) {
        const trades = results.filter(row => row.signal !== 'NO SIGNAL' && row.pnl !== null);
        const totalTrades = trades.length;
        const profitableTrades = trades.filter(row => row.pnl > 0).length;
        const lossTrades = trades.filter(row => row.pnl < 0).length;
        const totalPnL = trades.reduce((sum, row) => sum + (row.pnl || 0), 0);
        
        return { totalTrades, profitableTrades, lossTrades, totalPnL };
    }

    /** Updates the status message div. */
    function updateStatus(type, message) {
        if (!statusDiv) return;
        statusDiv.className = `status ${type}`; // Use classes for styling: 'success', 'error', 'loading', 'info'
        statusDiv.innerHTML = message;
    }

    // --- UI Rendering Functions ---

    /** Renders the trade statistics summary. */
    function renderStats(stats) {
        const pnlColor = stats.totalPnL >= 0 ? '#155724' : '#721c24'; // Green for profit, Red for loss
        const winRate = stats.totalTrades > 0 ? ((stats.profitableTrades / stats.totalTrades) * 100).toFixed(2) : 0;
        
        const message = `
            <strong>Total Trades: ${stats.totalTrades}</strong> | 
            <strong style="color: #155724">Profitable: ${stats.profitableTrades}</strong> | 
            <strong style="color: #721c24">Losses: ${stats.lossTrades}</strong> | 
            <strong>Win Rate: ${winRate}%</strong>
        `;

        const pnlMessage = `<span style="color: ${pnlColor}">Net P&L: ${formatCurrency(stats.totalPnL)}</span>`;
        
        updateStatus('success', `${message} | ${pnlMessage}`);
    }

    /** Renders the trade results table rows. */
    function renderResults(results) {
        if (!resultsBody) return;
        resultsBody.innerHTML = ''; // Clear previous results

        results.forEach(row => {
            const tr = document.createElement('tr');
            
            // Set row class based on signal type
            let signalClass = '';
            if (row.signal === 'BUY CE') signalClass = 'signal-buy-ce';
            else if (row.signal === 'BUY PE') signalClass = 'signal-buy-pe';
            else if (row.signal === 'NO SIGNAL') signalClass = 'signal-no';
            tr.classList.add(signalClass);

            // Set P&L cell class
            let pnlClass = '';
            if (row.pnl > 0) pnlClass = 'signal-buy-ce'; 
            else if (row.pnl < 0) pnlClass = 'signal-buy-pe';
            
            tr.innerHTML = `
                <td>${valOrDash(row.date)}</td>
                <td>${toFixedOrDash(row.nifty_close)}</td>
                <td>${toFixedOrDash(row.ce_strike)}</td>
                <td>${toFixedOrDash(row.ce_prev_high)}</td>
                <td>${toFixedOrDash(row.ce_prev_low)}</td>
                <td>${toFixedOrDash(row.pe_strike)}</td>
                <td>${toFixedOrDash(row.pe_prev_high)}</td>
                <td>${toFixedOrDash(row.pe_prev_low)}</td>
                <td>${valOrDash(row.signal)}</td>
                <td>${valOrDash(row.expiry_date)}</td>
                <td>${toFixedOrDash(row.buy_price)}</td>
                <td>${toFixedOrDash(row.target)}</td>
                <td>${toFixedOrDash(row.stop_loss)}</td>
                <td>${toFixedOrDash(row.exit_price)}</td>
                <td>${valOrDash(row.entry_time)}</td>
                <td>${valOrDash(row.exit_time)}</td>
                <td class="${pnlClass}">${toFixedOrDash(row.pnl)}</td>
            `;
            resultsBody.appendChild(tr);
        });
        
        // Show the table and results section
        const resultsSection = document.getElementById('resultsSection');
        if(resultsSection) resultsSection.style.display = 'block';
    }

    // --- Main Logic ---

    /** Event handler for form submission. */
    async function runBacktest(event) {
        event.preventDefault(); // Stop default form submission

        const symbol = symbolSelect.value;
        const startDate = startDateInput.value;
        const endDate = endDateInput.value;

        if (!symbol || !startDate || !endDate) {
            updateStatus('error', 'Please select a symbol, start date, and end date.');
            return;
        }

        // Clear previous state and results
        updateStatus('loading', 'Running backtest...');
        if (resultsBody) resultsBody.innerHTML = '';
        const resultsSection = document.getElementById('resultsSection');
        if (resultsSection) resultsSection.style.display = 'none';

        try {
            const data = await StrategyService.runBacktest(symbol, startDate, endDate);

            if (data.needs_login) {
                // fetchJson utility handles notification and redirect
                return;
            }

            if (data.status === 'success') {
                const results = data.data;
                const trades = results.filter(row => row.signal !== 'NO SIGNAL');
                
                if (trades.length > 0) {
                    const stats = calculateStats(results);
                    renderStats(stats);
                    renderResults(results);
                } else {
                    updateStatus('info', 'Backtest complete, but no trades were generated for the selected period/strategy.');
                    document.getElementById('noData').style.display = 'block';
                }
            } else {
                updateStatus('error', data.message || 'An unknown error occurred during backtest.');
            }
        } catch (error) {
            updateStatus('error', 'An unexpected error occurred during backtest.');
            console.error('Backtest error:', error);
        }
    }

    // --- Initialization ---

    function init() {
        // 1. Initialize form values to the AngularJS controller's defaults
        const today = new Date();
        const oneWeekAgo = new Date(today);
        oneWeekAgo.setDate(today.getDate() - 7);
        startDateInput.value = formatDate(oneWeekAgo);
        endDateInput.value = formatDate(today);
        
        // 2. Attach event listener to form submission
        if (backtestForm) {
            backtestForm.addEventListener('submit', runBacktest);
        } else {
            console.error('Backtest form not found. Check HTML.');
        }
    }

    init();
});