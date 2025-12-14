/**
 * options_chart_app.js
 * Pure Vanilla JavaScript module for the Options Chart Viewer.
 * It uses the Lightweight Charts library.
 */

const OptionsChartApp = (function() {
    // --- Global Chart State ---
    // These variables are kept private to the module (closure)
    let ceChart = null;
    let peChart = null;
    let combinedChart = null;
    let ceSeries = null;
    let peSeries = null;
    let combinedCeSeries = null;
    let combinedPeSeries = null;
    let ceData = null;
    let peData = null;
    let currentCeToken = null; // Token for auto-update
    let currentPeToken = null; // Token for auto-update
    let currentTimeframe = '5minute';
    let autoUpdateInterval = null;
    let currentSymbol = 'NIFTY'; 
    let currentPriceSource = 'current_close'; 
    let cePriceLines = []; // Price lines for individual charts
    let pePriceLines = []; // Price lines for individual charts
    let ceTimerPriceLine = null; // Price line for combined chart (CE)
    let peTimerPriceLine = null; // Price line for combined chart (PE)
    let isInitialLoad = true;


    // --- DOM Elements cache ---
    const DOM = {};

    // --- Constants ---
    const CONSTANTS = {
        API_ENDPOINTS: {
            UNDERLYING_PRICE: '/api/underlying-price',
            OPTIONS_STRIKES: '/api/options-strikes',
            OPTIONS_CHART_DATA: '/api/options-chart-data'
        },
        CSS_CLASSES: {
            TIMEFRAME_BTN: 'timeframe-btn',
            ACTIVE: 'active'
        },
        CHART_CONFIG: {
            CE_COLOR: '#00c853', // Green
            PE_COLOR: '#2962ff'  // Blue
        }
    };

    // --- Utility Functions ---

    /**
     * Caches all required DOM elements.
     */
    function cacheDomElements() {
        DOM.optionsChartApp = document.getElementById('options-chart-app');
        DOM.symbolSelect = document.getElementById('symbol');
        DOM.ceStrikeSelect = document.getElementById('ceStrike');
        DOM.peStrikeSelect = document.getElementById('peStrike');
        DOM.loadChartBtn = document.getElementById('fetchChartBtn');
        DOM.priceSourceRadios = document.querySelectorAll('input[name="priceSource"]');
        DOM.niftyPriceDisplay = document.getElementById('nifty-price');
        DOM.ceStrikeDisplay = document.getElementById('ce-strike-display');
        DOM.peStrikeDisplay = document.getElementById('pe-strike-display');
        DOM.combinedCeStrikeDisplay = document.getElementById('combined-ce-strike-display');
        DOM.combinedPeStrikeDisplay = document.getElementById('combined-pe-strike-display');
    }
    
    /**
     * Populates a select element with options.
     */
    function populateSelect(selectElement, options, defaultMessage = 'Select...') {
        if (!selectElement) return;

        selectElement.innerHTML = `<option value="">${defaultMessage}</option>`;
        options.forEach(optionValue => {
            const option = document.createElement('option');
            option.value = optionValue;
            option.textContent = optionValue;
            selectElement.appendChild(option);
        });
    }

    /**
     * Checks if the current time is within Indian market hours (9:15 AM to 3:30 PM) on a weekday.
     * @returns {boolean}
     */
    function isMarketHours() {
        const now = new Date();
        const hours = now.getHours();
        const minutes = now.getMinutes();
        const timeInMinutes = hours * 60 + minutes;
        const day = now.getDay(); // 0 = Sunday, 6 = Saturday

        // 9:15 AM = 555 minutes
        // 3:30 PM = 930 minutes
        const marketOpen = 555;
        const marketClose = 930;

        // Check if it's a weekday (Monday=1 to Friday=5) and within time range
        return day >= 1 && day <= 5 && timeInMinutes >= marketOpen && timeInMinutes <= marketClose;
    }

    /**
     * Converts raw data to the Lightweight Charts format.
     */
    function formatChartData(data) {
        return data.map(item => ({
            // Lightweight Charts expects time in seconds (timestamp) or 'yyyy-mm-dd' string.
            // Using timestamp in seconds is generally more reliable.
            time: new Date(item.date).getTime() / 1000,
            open: item.open,
            high: item.high,
            low: item.low,
            close: item.close,
            value: item.close // For line series
        }));
    }
    
    /**
     * Gets the latest close price from the chart data.
     */
    function getLatestPrice(data) {
        if (!data || data.length === 0) return null;
        return data[data.length - 1].close;
    }

    // --- UI/State Management Functions ---

    /**
     * Updates the active state of the timeframe buttons.
     */
    function updateActiveButton(activeTimeframe) {
        document.querySelectorAll(`.${CONSTANTS.CSS_CLASSES.TIMEFRAME_BTN}`).forEach(btn => {
            btn.classList.remove(CONSTANTS.CSS_CLASSES.ACTIVE);
        });
        const activeButton = document.querySelector(`.${CONSTANTS.CSS_CLASSES.TIMEFRAME_BTN}[data-timeframe="${activeTimeframe}"]`);
        if (activeButton) {
            activeButton.classList.add(CONSTANTS.CSS_CLASSES.ACTIVE);
        }
    }

    /**
     * Fetches and updates the underlying index price.
     */
    async function updateUnderlyingPrice() {
        if (!currentSymbol || !DOM.niftyPriceDisplay) return;

        try {
            const data = await fetchJson(`${CONSTANTS.API_ENDPOINTS.UNDERLYING_PRICE}?symbol=${currentSymbol}`);

            if (data.success && data.ltp) {
                DOM.niftyPriceDisplay.textContent = data.ltp.toFixed(2);
            }
        } catch (error) {
            console.error('Error fetching underlying price:', error);
        }
    }
    
    /**
     * Sets the current symbol and triggers strike loading.
     */
    function setSymbol(symbol) {
        currentSymbol = symbol;
        loadStrikes();
    }
    
    /**
     * Sets the price source and triggers chart data loading.
     */
    function setPriceSource(source) {
        currentPriceSource = source;
        loadChartData();
    }

    /**
     * Sets the timeframe and triggers chart data loading.
     */
    function setTimeframe(timeframe) {
        currentTimeframe = timeframe;
        updateActiveButton(timeframe);
        loadChartData();
    }
    
    // --- Chart Initialization and Drawing ---

    /**
     * Initializes the Lightweight Charts objects.
     */
function initCharts() {
        if (!window.LightweightCharts) {
            console.error("Lightweight Charts library not loaded.");
            showNotification("Chart library not loaded. Check your HTML head.", "error");
            return;
        }
        const { createChart, CandlestickSeries, LineSeries } = LightweightCharts;

        // Initialize CE Chart
        if (ceChart) ceChart.remove();
        ceChart = createChart(document.getElementById('ceChart'));
        ceSeries = ceChart.addSeries(CandlestickSeries, { upColor: CONSTANTS.CHART_CONFIG.CE_COLOR, downColor: 'red', borderVisible: false, wickUpColor: CONSTANTS.CHART_CONFIG.CE_COLOR, wickDownColor: 'red' });

        // Initialize PE Chart
        if (peChart) peChart.remove();
        peChart = createChart(document.getElementById('peChart'));
        peSeries = ceChart.addSeries(CandlestickSeries, { upColor: CONSTANTS.CHART_CONFIG.PE_COLOR, downColor: '#ef5350', borderVisible: false, wickUpColor: CONSTANTS.CHART_CONFIG.PE_COLOR, wickDownColor: '#ef5350' });

        // Initialize Combined Chart
        if (combinedChart) combinedChart.remove();
        combinedChart = createChart(document.getElementById('combinedChart'));
        combinedCeSeries = combinedChart.addSeries(LineSeries, { color: CONSTANTS.CHART_CONFIG.CE_COLOR, lineWidth: 2, title: 'CE Price' });
        combinedPeSeries = combinedChart.addSeries(LineSeries, { color: CONSTANTS.CHART_CONFIG.PE_COLOR, lineWidth: 2, title: 'PE Price' });
        
        // Apply consistent layout options
        const commonOptions = {
            layout: { textColor: '#d1d4dc', background: { color: '#1f2937' } },
            grid: { vertLines: { color: '#374151' }, horzLines: { color: '#374151' } }
        };
        ceChart.applyOptions(commonOptions);
        peChart.applyOptions(commonOptions);
        combinedChart.applyOptions(commonOptions);
    }
    
    /**
     * Renders the individual CE and PE charts.
     */
    function renderIndividualCharts() {
        if (!ceSeries || !peSeries || !ceData || !peData) return;

        ceSeries.setData(formatChartData(ceData));
        peSeries.setData(formatChartData(peData));
        
        // Remove old price lines
        cePriceLines.forEach(line => ceSeries.removePriceLine(line));
        pePriceLines.forEach(line => peSeries.removePriceLine(line));
        cePriceLines = [];
        pePriceLines = [];

        // Add latest price line if fetching current price and in market hours
        const latestCePrice = getLatestPrice(ceData);
        const latestPePrice = getLatestPrice(peData);

        if (latestCePrice !== null && currentPriceSource === 'current_close' && isMarketHours()) {
            cePriceLines.push(ceSeries.createPriceLine({
                price: latestCePrice,
                color: 'purple',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: 'LTP'
            }));
        }
        
        if (latestPePrice !== null && currentPriceSource === 'current_close' && isMarketHours()) {
            pePriceLines.push(peSeries.createPriceLine({
                price: latestPePrice,
                color: 'purple',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: 'LTP'
            }));
        }
    }
    
    /**
     * Renders the combined CE and PE line chart.
     */
    function renderCombinedChart() {
        if (!combinedCeSeries || !combinedPeSeries || !ceData || !peData) return;
        
        // Map candlestick data to line data using 'close' price
        combinedCeSeries.setData(formatChartData(ceData).map(d => ({ time: d.time, value: d.close })));
        combinedPeSeries.setData(formatChartData(peData).map(d => ({ time: d.time, value: d.close })));
        
        // Remove old timer lines
        if (ceTimerPriceLine) combinedCeSeries.removePriceLine(ceTimerPriceLine);
        if (peTimerPriceLine) combinedPeSeries.removePriceLine(peTimerPriceLine);
        
        const latestCePrice = getLatestPrice(ceData);
        const latestPePrice = getLatestPrice(peData);

        // Add price lines for the last traded price if in market hours
        if (latestCePrice !== null && currentPriceSource === 'current_close' && isMarketHours()) {
            ceTimerPriceLine = combinedCeSeries.createPriceLine({
                price: latestCePrice,
                color: CONSTANTS.CHART_CONFIG.CE_COLOR,
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: `CE: ${latestCePrice.toFixed(2)}`
            });
        }
        if (latestPePrice !== null && currentPriceSource === 'current_close' && isMarketHours()) {
            peTimerPriceLine = combinedPeSeries.createPriceLine({
                price: latestPePrice,
                color: CONSTANTS.CHART_CONFIG.PE_COLOR,
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: `PE: ${latestPePrice.toFixed(2)}`
            });
        }
        
        // Rerender individual charts
        renderIndividualCharts();
        
        // Fit content on the main combined chart on initial load
        if (isInitialLoad) {
            ceChart.timeScale().fitContent();
            peChart.timeScale().fitContent();
            combinedChart.timeScale().fitContent();
            isInitialLoad = false;
        }
    }

    // --- Data Fetching Logic ---

    /**
     * Fetches strikes and updates the dropdowns.
     */
    async function loadStrikes() {
        DOM.ceStrikeSelect.innerHTML = '<option value="">Loading...</option>';
        DOM.peStrikeSelect.innerHTML = '<option value="">Loading...</option>';

        const symbol = DOM.symbolSelect.value;
        if (!symbol) return;
        
        try {
            const data = await fetchJson(`${CONSTANTS.API_ENDPOINTS.OPTIONS_STRIKES}?symbol=${symbol}`);

            if (data.success) {
                const strikes = data.strikes.map(s => s.strike.toString());
                populateSelect(DOM.ceStrikeSelect, strikes, 'Select CE Strike');
                populateSelect(DOM.peStrikeSelect, strikes, 'Select PE Strike');

                // Auto-select the default strikes
                if (data.default_ce_strike && data.default_pe_strike) {
                    DOM.ceStrikeSelect.value = data.default_ce_strike.toString();
                    DOM.peStrikeSelect.value = data.default_pe_strike.toString();
                } 
                // Fallback to the first strike
                else if (strikes.length > 0) {
                    DOM.ceStrikeSelect.value = strikes[0];
                    DOM.peStrikeSelect.value = strikes[0];
                }
                
                // Update strike displays
                DOM.ceStrikeDisplay.textContent = DOM.ceStrikeSelect.value ? `(${DOM.ceStrikeSelect.value})` : '';
                DOM.peStrikeDisplay.textContent = DOM.peStrikeSelect.value ? `(${DOM.peStrikeSelect.value})` : '';
                DOM.combinedCeStrikeDisplay.textContent = DOM.ceStrikeSelect.value ? `CE: ${DOM.ceStrikeSelect.value}` : '';
                DOM.combinedPeStrikeDisplay.textContent = DOM.peStrikeSelect.value ? `PE: ${DOM.peStrikeSelect.value}` : '';
                
                // Load initial chart data
                loadChartData();

            } else {
                showNotification(data.message || 'Failed to load strikes.', 'error');
            }
        } catch (error) {
            console.error('Error loading strikes:', error);
        }
    }

    /**
     * Fetches and displays the chart data.
     */
    async function loadChartData() {
        const ceStrike = DOM.ceStrikeSelect.value;
        const peStrike = DOM.peStrikeSelect.value;
        
        if (!ceStrike || !peStrike) {
            // Only show warning if one is selected but not the other
            if (ceStrike || peStrike) {
                showNotification('Please select both CE and PE strikes.', 'warning');
            }
            return;
        }
        
        DOM.loadChartBtn.disabled = true;
        
        try {
            // Pass strikes, symbol, timeframe, and price source to backend
            const data = await fetchJson(CONSTANTS.API_ENDPOINTS.OPTIONS_CHART_DATA, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: currentSymbol,
                    ce_strike: ceStrike,
                    pe_strike: peStrike,
                    price_source: currentPriceSource,
                    timeframe: currentTimeframe
                })
            });

            if (data.success) {
                ceData = data.ce_data;
                peData = data.pe_data;
                renderCombinedChart();
                
                // Set tokens for auto-update using the latest fetched data tokens
                currentCeToken = data.ce_token;
                currentPeToken = data.pe_token;
                startAutoUpdate(); // Restart auto-update with new data

                showNotification('Chart data loaded successfully.', 'success');
            } else {
                showNotification(data.message || 'Failed to load chart data.', 'error');
            }
        } catch (error) {
            console.error('Error fetching chart data:', error);
        } finally {
            DOM.loadChartBtn.disabled = false;
        }
    }
    
    // --- Auto-Update Logic ---
    
    /**
     * Starts the auto-update interval for live price updates.
     */
    function startAutoUpdate() {
        // Clear any existing interval
        if (autoUpdateInterval) clearInterval(autoUpdateInterval);
        
        // Only start if we have tokens for live updates
        if (!currentCeToken || !currentPeToken) return;
        
        // Set a 2-second interval for fetching live data
        autoUpdateInterval = setInterval(async () => {
            // Only update if tokens are set and it's market hours AND currentPriceSource is current_close
            if (!currentCeToken || !currentPeToken || !isMarketHours() || currentPriceSource !== 'current_close') return;
            
            // 1. Update Underlying Price
            updateUnderlyingPrice();

            // 2. Fetch latest chart data (pass tokens for optimized fetch)
            try {
                 const data = await fetchJson(CONSTANTS.API_ENDPOINTS.OPTIONS_CHART_DATA, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ce_token: currentCeToken,
                        pe_token: currentPeToken,
                        timeframe: currentTimeframe
                        // NOTE: Backend logic should be smart enough to use these tokens to fetch the latest data efficiently
                    })
                });

                if (data.needs_login) {
                    clearInterval(autoUpdateInterval);
                    return; // fetchJson handles the redirect/notification
                }

                if (data.success) {
                    // Assuming the backend returns the full, updated data for the timeframe
                    ceData = data.ce_data;
                    peData = data.pe_data;
                    renderCombinedChart();
                }
            } catch (error) {
                console.error('Auto-update error:', error);
            }
        }, 2000); // Update every 2 seconds
    }


    // --- Event Listeners and Initialization ---

    /**
     * Attaches all necessary event listeners to DOM elements.
     */
    function attachEventListeners() {
        // Event delegation for the main container (change events for select/radio)
        DOM.optionsChartApp.addEventListener('change', (event) => {
            const target = event.target;
            if (target === DOM.symbolSelect) {
                setSymbol(target.value);
            } else if (Array.from(DOM.priceSourceRadios).includes(target)) {
                setPriceSource(target.value);
            } else if (target === DOM.ceStrikeSelect || target === DOM.peStrikeSelect) {
                // Update strike displays instantly
                DOM.ceStrikeDisplay.textContent = DOM.ceStrikeSelect.value ? `(${DOM.ceStrikeSelect.value})` : '';
                DOM.peStrikeDisplay.textContent = DOM.peStrikeSelect.value ? `(${DOM.peStrikeSelect.value})` : '';
                DOM.combinedCeStrikeDisplay.textContent = DOM.ceStrikeSelect.value ? `CE: ${DOM.ceStrikeSelect.value}` : '';
                DOM.combinedPeStrikeDisplay.textContent = DOM.peStrikeSelect.value ? `PE: ${DOM.peStrikeSelect.value}` : '';
            }
        });

        // Event delegation for the main container (click events for buttons)
        DOM.optionsChartApp.addEventListener('click', (event) => {
            const target = event.target;
            if (target.id === 'fetchChartBtn') {
                loadChartData();
            } else if (target.classList.contains(CONSTANTS.CSS_CLASSES.TIMEFRAME_BTN)) {
                const timeframe = target.dataset.timeframe;
                setTimeframe(timeframe);
            }
        });
        
        // Handle window resize for chart responsiveness using ResizeObserver
        const ceContainer = document.getElementById('ceChart');
        const peContainer = document.getElementById('peChart');
        const combinedContainer = document.getElementById('combinedChart');

        if (ceContainer && ceChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== ceContainer) { return; }
                const newRect = entries[0].contentRect;
                ceChart.applyOptions({ height: newRect.height, width: newRect.width });
            }).observe(ceContainer);
        }

        if (peContainer && peChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== peContainer) { return; }
                const newRect = entries[0].contentRect;
                peChart.applyOptions({ height: newRect.height, width: newRect.width });
            }).observe(peContainer);
        }

        if (combinedContainer && combinedChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== combinedContainer) { return; }
                const newRect = entries[0].contentRect;
                combinedChart.applyOptions({ height: newRect.height, width: newRect.width });
            }).observe(combinedContainer);
        }
    }

    /**
     * Main initialization function.
     */
    function init() {
        // Ensure fetchJson utility is available
        if (typeof fetchJson !== 'function') {
            console.error("fetchJson utility not found. Ensure app.js is loaded first.");
            return;
        }

        cacheDomElements();
        initCharts(); // Initialize charts first
        attachEventListeners();

        // Set initial state based on HTML defaults
        currentSymbol = DOM.symbolSelect?.value || 'NIFTY';
        const selectedPriceSourceRadio = document.querySelector('input[name="priceSource"]:checked');
        if (selectedPriceSourceRadio) {
            currentPriceSource = selectedPriceSourceRadio.value;
        } else {
            // Default to 'current_close'
            const defaultRadio = document.getElementById('current_close');
            if (defaultRadio) {
                defaultRadio.checked = true;
                currentPriceSource = 'current_close';
            }
        }
        
        // Set initial timeframe button active state
        updateActiveButton(currentTimeframe);

        // Initial load of strikes and chart data
        if (currentSymbol) {
            setSymbol(currentSymbol); // This will trigger loadStrikes and then loadChartData
        }
        
        // Start live price update for the underlying symbol
        updateUnderlyingPrice();
        setInterval(updateUnderlyingPrice, 5000); // Update every 5 seconds
    }

    return {
        init: init
    };
})();

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', OptionsChartApp.init);