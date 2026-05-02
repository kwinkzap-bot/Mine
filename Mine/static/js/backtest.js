/**
 * backtest.js
 * Handles the UI logic for the Apex Reversal Engine backtester.
 */

document.addEventListener('DOMContentLoaded', function() {
    const symbolSearch = document.getElementById('symbolSearch');
    const symbolList = document.getElementById('symbolList');
    const runBtn = document.getElementById('runBacktestBtn');
    const resultsArea = document.getElementById('resultsArea');
    const loading = document.getElementById('loading');
    
    let allSymbols = [];
    let selectedSymbol = 'NIFTY';

    // Initialize dates (default to Jan 1st of current year)
    const today = new Date();
    const currentYearStart = new Date(today.getFullYear(), 0, 1); // Jan 1st
    
    document.getElementById('endDate').value = today.toISOString().split('T')[0];
    document.getElementById('startDate').value = currentYearStart.toISOString().split('T')[0];

    // Ensure timeframe default is 60m (already set in HTML but double check)
    const intervalSelect = document.getElementById('interval');
    if (intervalSelect) intervalSelect.value = '60minute';

    // 1. Fetch Symbols
    async function fetchSymbols() {
        // Fallback common symbols in case API is slow
        allSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'SBIN', 'RELIANCE', 'HDFCBANK'];
        
        try {
            const response = await fetch('/api/backtest/symbols');
            const data = await response.json();
            if (data.success) {
                // Combine indices and symbols, removing duplicates
                const fetched = [...data.indices, ...data.symbols];
                allSymbols = Array.from(new Set([...allSymbols, ...fetched]));
                console.log('Loaded symbols:', allSymbols.length);
            } else {
                if (window.showNotification) window.showNotification('Error loading symbols: ' + data.error, 'error');
            }
        } catch (error) {
            console.error('Failed to fetch symbols:', error);
        }
    }

    fetchSymbols();

    // 2. Searchable Dropdown Logic
    function renderDropdown(filterText = '') {
        const filter = (filterText || '').toUpperCase();
        const matches = allSymbols.filter(s => {
            if (!filter) return true;
            return s.includes(filter);
        }).slice(0, 15);
        
        if (matches.length > 0) {
            symbolList.innerHTML = matches.map(s => `<li class="symbol-item">${s}</li>`).join('');
            symbolList.classList.add('show');
        } else {
            symbolList.classList.remove('show');
        }
    }

    symbolSearch.addEventListener('input', function(e) {
        renderDropdown(e.target.value);
    });

    symbolSearch.addEventListener('focus', function(e) {
        this.value = ''; // Clear on focus per user request
        renderDropdown(''); // Show full list
    });

    symbolSearch.addEventListener('blur', function(e) {
        // Delay hiding to allow click event on list item to fire
        setTimeout(() => {
            symbolList.classList.remove('show');
            if (!this.value.trim()) {
                this.value = selectedSymbol;
            }
        }, 200);
    });

    symbolSearch.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            symbolList.classList.remove('show');
            let val = e.target.value.trim().toUpperCase();
            if (val) {
                selectedSymbol = val;
                this.value = selectedSymbol;
                this.blur();
            } else {
                this.blur();
            }
        }
    });

    symbolList.addEventListener('click', function(e) {
        if (e.target.classList.contains('symbol-item')) {
            selectedSymbol = e.target.textContent;
            symbolSearch.value = selectedSymbol;
            symbolList.classList.remove('show');
        }
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', function(e) {
        if (!symbolSearch.contains(e.target) && !symbolList.contains(e.target)) {
            symbolList.classList.remove('show');
        }
    });

    // 2.5 Strategy Selection Logic
    const strategySelect = document.getElementById('strategySelect');
    const apexParamsRow = document.getElementById('apexParamsRow');
    const apexOptionsRow = document.getElementById('apexOptionsRow');

    function updateStrategyView() {
        if (!strategySelect || !apexParamsRow || !apexOptionsRow) return;
        
        const intervalSelect = document.getElementById('interval');
        const startDateInput = document.getElementById('startDate');
        const cprTypeContainer = document.getElementById('cprTypeContainer');
        const mainInputsRow = document.getElementById('mainInputsRow');
        const today = new Date();
        
        if (strategySelect.value === 'cpr_gap') {
            apexParamsRow.style.display = 'none';
            // Show options for SL Close
            apexOptionsRow.style.display = 'flex';
            
            const cprInputsRow = document.getElementById('cprInputsRow');
            if (cprInputsRow) cprInputsRow.style.display = 'grid';
            
            if (mainInputsRow) {
                mainInputsRow.classList.remove('form-row-5');
                mainInputsRow.classList.remove('form-row-6');
                mainInputsRow.classList.remove('form-row-7');
                mainInputsRow.classList.add('form-row-4');
            }
            
            // Set CPR Gap defaults (Best Options)
            if (intervalSelect) intervalSelect.value = '5minute';
            if (startDateInput) {
                const currentMonthStart = new Date(today.getFullYear(), today.getMonth(), 1);
                // Adjust for local timezone to avoid off-by-one day issues
                const localDateStr = new Date(currentMonthStart.getTime() - (currentMonthStart.getTimezoneOffset() * 60000)).toISOString().split('T')[0];
                startDateInput.value = localDateStr;
            }

            // Set best-performing dropdown defaults
            const entryTypeSelect = document.getElementById('entryType');
            const slTypeSelect = document.getElementById('slType');
            const slCloseCheckbox = document.getElementById('slClosePrice');
            
            if (entryTypeSelect) entryTypeSelect.value = 'both';
            if (slTypeSelect) slTypeSelect.value = 'both';
            if (slCloseCheckbox) slCloseCheckbox.checked = true;
        } else {
            apexParamsRow.style.display = ''; // Reverts to CSS grid
            apexOptionsRow.style.display = 'flex';
            
            const cprInputsRow = document.getElementById('cprInputsRow');
            if (cprInputsRow) cprInputsRow.style.display = 'none';
            
            if (mainInputsRow) {
                mainInputsRow.classList.remove('form-row-5');
                mainInputsRow.classList.remove('form-row-6');
                mainInputsRow.classList.remove('form-row-7');
                mainInputsRow.classList.add('form-row-4');
            }
            
            // Set Apex Reversal defaults
            if (intervalSelect) intervalSelect.value = '60minute';
            if (startDateInput) {
                const currentYearStart = new Date(today.getFullYear(), 0, 1);
                const localDateStr = new Date(currentYearStart.getTime() - (currentYearStart.getTimezoneOffset() * 60000)).toISOString().split('T')[0];
                startDateInput.value = localDateStr;
            }
        }
    }

    if (strategySelect) {
        strategySelect.addEventListener('change', updateStrategyView);
        // Set initial state
        updateStrategyView();
    }

    // 3. Run Backtest
    runBtn.addEventListener('click', async function() {
        const symbol = symbolSearch.value.toUpperCase();
        if (!symbol) {
            window.showNotification('Please select a symbol', 'warning');
            return;
        }

        const payload = {
            symbol: symbol,
            start_date: document.getElementById('startDate').value,
            end_date: document.getElementById('endDate').value,
            interval: document.getElementById('interval').value,
            cpr_type: document.getElementById('cprType') ? document.getElementById('cprType').value : 's1_r1',
            entry_type: document.getElementById('entryType') ? document.getElementById('entryType').value : 'any',
            sl_type: document.getElementById('slType') ? document.getElementById('slType').value : 'both',
            pivot_strength: document.getElementById('pivotStrength').value,
            rsi_length: document.getElementById('rsiLength').value,
            rsi_overbought: 70,
            rsi_oversold: 30,
            rr_ratio: document.getElementById('rrRatio').value,
            sl_close_price: document.getElementById('slClosePrice').checked,
            trail_candles: parseInt(document.getElementById('trailMode').value)
        };

        loading.style.display = 'block';
        resultsArea.style.display = 'none';

        try {
            let endpoint = '/api/backtest/apex-reversal';
            if (strategySelect && strategySelect.value === 'cpr_gap') {
                endpoint = '/api/backtest/cpr-gap';
            }

            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (data.success) {
                displayResults(data);
            } else {
                window.showNotification(data.error || 'Backtest failed', 'error');
            }
        } catch (error) {
            console.error('Backtest error:', error);
            window.showNotification('An error occurred during backtest', 'error');
        } finally {
            loading.style.display = 'none';
        }
    });

    let lastData = null;
    let sortConfig = { key: 'exit_time', direction: 'desc' };

    function displayResults(data) {
        lastData = data;
        const { summary } = data;
        
        // Update stats
        document.getElementById('statTotalTrades').textContent = summary.total_trades;
        
        const winRate = summary.total_trades > 0 
            ? ((summary.wins / summary.total_trades) * 100).toFixed(1) + '%' 
            : '0%';
        document.getElementById('statWinRate').textContent = winRate;
        
        document.getElementById('statTotalPnl').textContent = summary.total_pnl.toFixed(2);
        document.getElementById('statTotalPnl').className = summary.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        
        const outcome = summary.total_pnl >= 0 ? 'PROFIT' : 'LOSS';
        const outcomeEl = document.getElementById('statOutcome');
        outcomeEl.textContent = outcome;
        outcomeEl.className = summary.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        
        renderTable();
        resultsArea.style.display = 'block';
    }

    function renderTable() {
        if (!lastData || !lastData.trades) return;
        const trades = [...lastData.trades];
        const tbody = document.getElementById('tradesBody');
        
        // Sort trades based on config
        trades.sort((a, b) => {
            let valA = a[sortConfig.key];
            let valB = b[sortConfig.key];
            
            // Handle dates
            if (sortConfig.key.includes('time')) {
                valA = new Date(valA).getTime();
                valB = new Date(valB).getTime();
            }
            
            if (valA < valB) return sortConfig.direction === 'asc' ? -1 : 1;
            if (valA > valB) return sortConfig.direction === 'asc' ? 1 : -1;
            return 0;
        });

        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 20px;">No trades generated</td></tr>';
        } else {
            tbody.innerHTML = trades.map(t => `
                <tr>
                    <td>${formatDate(t.entry_time)}</td>
                    <td><span class="badge ${t.type === 'BUY' ? 'badge-buy' : 'badge-sell'}">${t.type}</span></td>
                    <td>${t.entry_price.toFixed(2)}</td>
                    <td>${formatDate(t.exit_time)}</td>
                    <td>${t.exit_price.toFixed(2)}</td>
                    <td>${t.result}</td>
                    <td class="${t.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${t.pnl.toFixed(2)}</td>
                </tr>
            `).join('');
        }

        // Update header indicators
        document.querySelectorAll('.trades-table th').forEach(th => {
            th.classList.remove('sort-asc', 'sort-desc');
            if (th.dataset.sort === sortConfig.key) {
                th.classList.add(sortConfig.direction === 'asc' ? 'sort-asc' : 'sort-desc');
            }
        });
    }

    // Add sort listeners to headers
    document.querySelectorAll('.trades-table th[data-sort]').forEach(th => {
        th.style.cursor = 'pointer';
        th.addEventListener('click', () => {
            const key = th.dataset.sort;
            if (sortConfig.key === key) {
                sortConfig.direction = sortConfig.direction === 'asc' ? 'desc' : 'asc';
            } else {
                sortConfig.key = key;
                sortConfig.direction = 'desc'; // Default to newest/highest first
            }
            renderTable();
        });
    });

    function formatDate(dateStr) {
        if (!dateStr) return '--';
        const d = new Date(dateStr);
        return d.toLocaleString();
    }
});
