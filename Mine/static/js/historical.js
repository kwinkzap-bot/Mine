/**
 * historical.js
 * Handles the logic for fetching and displaying historical data from the API.
 */

// Global state variables
let currentInstruments = [];
let selectedInstrumentToken = null;
let isManualTokenMode = false;

// Set default dates to current day
const today = new Date().toISOString().split('T')[0];
document.getElementById('toDate').value = today;
document.getElementById('fromDate').value = today;

// Initial setup
document.addEventListener('DOMContentLoaded', function() {
    loadSymbols();
    toggleOptionFields(); // Initial toggle based on default selection
    addEventListeners();
});

function addEventListeners() {
    document.getElementById('instrumentType').addEventListener('change', loadSymbols);
    document.getElementById('symbolSelect').addEventListener('change', handleSymbolOrFnoChange);
    document.getElementById('fnoType').addEventListener('change', handleSymbolOrFnoChange);
    document.getElementById('expiryDate').addEventListener('change', loadStrikePrices);
    document.getElementById('strikePrice').addEventListener('change', loadInstruments);
    document.getElementById('historicalForm').addEventListener('submit', handleFormSubmission);
    document.getElementById('manual-token-btn').addEventListener('click', toggleManualTokenMode);
}

/**
 * Toggles the visibility of Option fields (Expiry, Strike) based on F&O Type.
 */
function toggleOptionFields() {
    const fnoType = document.getElementById('fnoType').value;
    const instrumentType = document.getElementById('instrumentType').value;
    const optionFields = document.getElementById('optionFields');
    
    // Option fields are only relevant for F&O -> Options
    if (instrumentType === 'equity' && fnoType === 'options') {
        optionFields.style.display = 'flex';
    } else {
        optionFields.style.display = 'none';
    }
}

/**
 * Handles the change of Symbol or F&O Type.
 * Triggers loading of expiry dates or instruments.
 */
function handleSymbolOrFnoChange() {
    toggleOptionFields(); // Re-evaluate visibility

    const symbol = document.getElementById('symbolSelect').value;
    const fnoType = document.getElementById('fnoType').value;
    
    if (!symbol) return;

    if (fnoType === 'options') {
        loadExpiryDates(symbol);
    } else {
        // Futures or Equity
        // Clear option fields
        document.getElementById('expiryDate').innerHTML = '<option value="">Select expiry</option>';
        document.getElementById('strikePrice').innerHTML = '<option value="">Select strike</option>';
        loadInstruments(symbol);
    }
}

/**
 * Toggles between auto-selection and manual instrument token input.
 */
function toggleManualTokenMode() {
    isManualTokenMode = !isManualTokenMode;
    const tokenContainer = document.getElementById('manualTokenContainer'); // Assuming a container with this ID
    const formFields = document.querySelector('#historicalForm .form-row:not(#manualTokenContainer)');
    const button = document.getElementById('manual-token-btn');

    if (isManualTokenMode) {
        // Create manual input if it doesn't exist
        if (!tokenContainer) {
             const html = `
                <div class="form-row" id="manualTokenContainer">
                    <div class="form-group">
                        <label for="instrumentTokenInput">Manual Instrument Token:</label>
                        <input type="number" id="instrumentTokenInput" placeholder="Enter Token">
                    </div>
                </div>`;
            document.getElementById('historicalForm').insertAdjacentHTML('beforeend', html);
        } else {
             tokenContainer.style.display = 'flex';
        }
        formFields.style.display = 'none'; // Hide auto-selection fields
        button.textContent = 'Use Auto Selection';
    } else {
        // Hide manual input
        if (tokenContainer) {
            tokenContainer.style.display = 'none';
        }
        formFields.style.display = 'flex'; // Show auto-selection fields
        button.textContent = 'Use Manual Token';
    }
}

/**
 * Fetches the list of F&O symbols (NIFTY, BANKNIFTY, etc.) and populates the Symbol select.
 */
async function loadSymbols() {
    const symbolSelect = document.getElementById('symbolSelect');
    symbolSelect.innerHTML = '<option value="">Loading symbols...</option>';
    
    try {
        // Use the global fetchJson utility
        const data = await fetchJson('/api/fo-stocks'); // Assumed API endpoint
        
        if (data.success) {
            symbolSelect.innerHTML = '';
            data.stocks.forEach(symbol => {
                const option = document.createElement('option');
                option.value = symbol;
                option.textContent = symbol;
                symbolSelect.appendChild(option);
            });
            // Trigger instrument loading for the first item
            handleSymbolOrFnoChange();
        } else {
            symbolSelect.innerHTML = '<option value="">Error loading symbols</option>';
        }
    } catch (error) {
        symbolSelect.innerHTML = '<option value="">Network Error</option>';
    }
}

/**
 * Fetches the available expiry dates for the selected symbol (for options).
 * @param {string} symbol
 */
async function loadExpiryDates(symbol) {
    const expirySelect = document.getElementById('expiryDate');
    expirySelect.innerHTML = '<option value="">Loading expiries...</option>';
    
    try {
        const data = await fetchJson(`/api/historical/expiry?symbol=${symbol}`);
        
        if (data.success) {
            expirySelect.innerHTML = '<option value="">Select expiry</option>';
            data.expiries.forEach(expiry => {
                const option = document.createElement('option');
                option.value = expiry;
                option.textContent = expiry;
                expirySelect.appendChild(option);
            });
            
            // Try to pre-select and load strikes if there's only one expiry or a default one
            if (data.expiries.length > 0) {
                expirySelect.value = data.expiries[0];
                loadStrikePrices(); 
            }
        } else {
            expirySelect.innerHTML = '<option value="">Error loading expiries</option>';
        }
    } catch (error) {
        expirySelect.innerHTML = '<option value="">Network Error</option>';
    }
}

/**
 * Fetches the available strike prices for the selected symbol and expiry (for options).
 */
async function loadStrikePrices() {
    const symbol = document.getElementById('symbolSelect').value;
    const expiry = document.getElementById('expiryDate').value;
    const strikeSelect = document.getElementById('strikePrice');
    strikeSelect.innerHTML = '<option value="">Loading strikes...</option>';

    if (!symbol || !expiry) {
        strikeSelect.innerHTML = '<option value="">Select expiry first</option>';
        return;
    }

    try {
        const data = await fetchJson(`/api/historical/strikes?symbol=${symbol}&expiry=${expiry}`);
        
        if (data.success) {
            currentInstruments = data.instruments; // Store all instrument data
            strikeSelect.innerHTML = '<option value="">Select strike</option>';
            data.strikes.forEach(strike => {
                const option = document.createElement('option');
                option.value = strike;
                option.textContent = strike;
                strikeSelect.appendChild(option);
            });

            // Try to pre-select a strike and load instrument token
            if (data.strikes.length > 0) {
                strikeSelect.value = data.strikes[Math.floor(data.strikes.length / 2)]; // Select middle strike
                loadInstruments(); 
            }
        } else {
            strikeSelect.innerHTML = '<option value="">Error loading strikes</option>';
        }
    } catch (error) {
        strikeSelect.innerHTML = '<option value="">Network Error</option>';
    }
}

/**
 * Sets the final instrument token based on selections, or fetches it for Futures/Equity.
 * This function also sets the token in a hidden field for form submission.
 */
async function loadInstruments() {
    const statusDiv = document.getElementById('status');
    const instrumentType = document.getElementById('instrumentType').value;
    const fnoType = document.getElementById('fnoType').value;
    const symbol = document.getElementById('symbolSelect').value;
    const expiry = document.getElementById('expiryDate').value;
    const strike = document.getElementById('strikePrice').value;

    selectedInstrumentToken = null; // Reset token

    // 1. Handle Options: token should be in currentInstruments
    if (instrumentType === 'equity' && fnoType === 'options' && symbol && expiry && strike && currentInstruments.length > 0) {
        const selectedInstrument = currentInstruments.find(inst => inst.strike == strike); // Assuming 'strike' is the field
        if (selectedInstrument) {
            selectedInstrumentToken = selectedInstrument.token; // Assuming 'token' is the field
            statusDiv.innerHTML = `<div class="status info">Token found: ${selectedInstrumentToken}</div>`;
            return;
        }
    }

    // 2. Handle Futures/Equity: need an API call for a single token
    if (symbol) {
        statusDiv.innerHTML = '<div class="status loading">Finding instrument token...</div>';
        try {
            const data = await fetchJson(`/api/historical/instrument-token?symbol=${symbol}&type=${instrumentType}&fno_type=${fnoType}`);
             if (data.success && data.instrument_token) {
                selectedInstrumentToken = data.instrument_token;
                statusDiv.innerHTML = `<div class="status info">Token found: ${selectedInstrumentToken}</div>`;
                return;
            }
        } catch (e) {
            // Error handled by fetchJson
        }
    }

    statusDiv.innerHTML = '<div class="status error">Could not determine instrument token.</div>';
}

/**
 * Handles the form submission to fetch historical data.
 */
async function handleFormSubmission(e) {
    e.preventDefault();
    
    const table = document.getElementById('dataTable');
    const tbody = document.getElementById('dataBody');
    const statusDiv = document.getElementById('status');
    
    tbody.innerHTML = '';
    table.classList.add('hidden');
    statusDiv.innerHTML = '<div class="status loading">⏳ Fetching historical data...</div>';
    
    let instrumentToken = isManualTokenMode 
        ? document.getElementById('instrumentTokenInput')?.value
        : selectedInstrumentToken;

    if (!instrumentToken) {
        statusDiv.innerHTML = '<div class="status error">Please select a symbol/instrument or enter a manual token.</div>';
        return;
    }
    
    const formData = {
        instrument_token: instrumentToken,
        from_date: document.getElementById('fromDate').value,
        to_date: document.getElementById('toDate').value,
        interval: document.getElementById('interval').value
    };
    
    // Use the global fetchJson utility
    const data = await fetchJson('/api/historical', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
    });

    if (data.success && data.data) {
        statusDiv.innerHTML = '<div class="status success">Data fetched successfully!</div>';
        
        // Populate table
        data.data.forEach(row => {
            const tr = document.createElement('tr');
            // Assuming data.data format: { date, open, high, low, close, volume }
            tr.innerHTML = `
                <td>${new Date(row.date).toLocaleString()}</td>
                <td>${row.open.toFixed(2)}</td>
                <td>${row.high.toFixed(2)}</td>
                <td>${row.low.toFixed(2)}</td>
                <td>${row.close.toFixed(2)}</td>
                <td>${row.volume}</td>
            `;
            tbody.appendChild(tr);
        });
        
        table.classList.remove('hidden');
        // Add sorting functionality to the table headers
        document.querySelectorAll('#dataTable th').forEach((header, index) => {
             header.dataset.columnIndex = index;
             header.dataset.type = index === 0 ? 'string' : 'number';
             header.onclick = () => sortTable(index, header.dataset.type);
        });

    } else if (!data.needs_login) {
        statusDiv.innerHTML = `<div class="status error">Error fetching data: ${data.message}</div>`;
    }
}

/**
 * Simple table sorting function for historical data.
 * @param {number} columnIndex - The index of the column to sort.
 * @param {string} type - 'string' or 'number'.
 */
let historicalSortDirection = {};
function sortTable(columnIndex, type) {
    const table = document.getElementById('dataTable');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const currentHeader = table.querySelector(`th[data-column-index="${columnIndex}"]`);
    
    // Toggle sort direction
    if (!historicalSortDirection[columnIndex]) historicalSortDirection[columnIndex] = 'asc';
    else historicalSortDirection[columnIndex] = historicalSortDirection[columnIndex] === 'asc' ? 'desc' : 'asc';
    
    const newDirection = historicalSortDirection[columnIndex];

    // Remove sort classes from all headers
    table.querySelectorAll('th').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
    
    // Add sort class to current header
    currentHeader.classList.add(newDirection === 'asc' ? 'sort-asc' : 'sort-desc');
    
    const isAsc = newDirection === 'asc';

    // Sort rows
    rows.sort((a, b) => {
        let aValue = a.cells[columnIndex].textContent.trim();
        let bValue = b.cells[columnIndex].textContent.trim();
        
        if (type === 'number') {
            // Remove symbols and commas for numeric comparison
            aValue = parseFloat(aValue.replace(/[₹,%]/g, '')) || 0;
            bValue = parseFloat(bValue.replace(/[₹,%]/g, '')) || 0;
            
            return isAsc ? aValue - bValue : bValue - aValue;
        } else {
            // String comparison
            return isAsc ? aValue.localeCompare(bValue) : bValue.localeCompare(aValue);
        }
    });
    
    // Re-append sorted rows to the tbody
    rows.forEach(row => tbody.appendChild(row));
}