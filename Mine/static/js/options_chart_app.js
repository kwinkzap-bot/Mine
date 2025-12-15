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
    // Cached formatted data for hover synchronization
    let ceFormattedData = null;
    let peFormattedData = null;
    // Hover markers
    let ceHoverMarker = [];
    let peHoverMarker = [];
    let countdownInterval = null;
    let countdownValue = 0;
    let timeframeIntervals = {
        '1minute': 60,
        '3minute': 180,
        '5minute': 300,
        '15minute': 900,
        '60minute': 3600,
        '1day': 86400
    };

    // --- DOM Elements cache ---
    const DOM = {};

    // --- Constants ---
    const CONSTANTS = {
        API_ENDPOINTS: {
            UNDERLYING_PRICE: '/api/underlying-price',
            OPTIONS_STRIKES: '/api/options-strikes',
            OPTIONS_DEFAULT_STRIKES: '/api/options-default-strikes',
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
        DOM.apiLoader = document.getElementById('api-loader');
        DOM.symbolSelect = document.getElementById('symbol');
        DOM.ceStrikeSelect = document.getElementById('ceStrike');
        DOM.peStrikeSelect = document.getElementById('peStrike');
        DOM.loadChartBtn = document.getElementById('fetchChartBtn');
        DOM.priceSourceRadios = document.querySelectorAll('input[name="priceSource"]');
        DOM.niftyPriceDisplay = document.getElementById('nifty-ltp');
        DOM.ceStrikeDisplay = document.getElementById('ce-strike-display');
        DOM.peStrikeDisplay = document.getElementById('pe-strike-display');
        DOM.combinedCeStrikeDisplay = document.getElementById('combined-ce-strike-display');
        DOM.combinedPeStrikeDisplay = document.getElementById('combined-pe-strike-display');
        DOM.countdownTimer = document.getElementById('countdown-timer');
    }
    
    /**
     * Shows the API loader.
     */
    function showLoader() {
        if (DOM.apiLoader) {
            DOM.apiLoader.classList.remove('hidden');
        }
    }

    /**
     * Hides the API loader.
     */
    function hideLoader() {
        if (DOM.apiLoader) {
            DOM.apiLoader.classList.add('hidden');
        }
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
     * Backend sends UTC timestamps, we don't adjust them since Lightweight Charts handles timezone display.
     */
    function formatChartData(data) {
        return data.map(item => {
            let timestamp;
            
            // Handle different date formats
            if (typeof item.date === 'number') {
                // If it's already a Unix timestamp in milliseconds
                if (item.date > 10000000000) {
                    timestamp = Math.floor(item.date / 1000); // Convert ms to seconds
                } else {
                    // If it's already in seconds (this is what backend sends)
                    timestamp = item.date;
                }
            } else if (typeof item.date === 'string') {
                // Parse ISO format date string
                const dateObj = new Date(item.date);
                timestamp = Math.floor(dateObj.getTime() / 1000);
            } else {
                timestamp = Math.floor(new Date(item.date).getTime() / 1000);
            }
            
            return {
                // Lightweight Charts expects time in seconds (Unix timestamp)
                // No adjustment needed - backend sends correct UTC timestamps
                time: timestamp,
                open: item.open,
                high: item.high,
                low: item.low,
                close: item.close,
                value: item.close // For line series
            };
        });
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
     * Fetches and updates the underlying index price based on price source.
     */
    async function updateUnderlyingPrice() {
        if (!currentSymbol || !DOM.niftyPriceDisplay) return;

        try {
            // Use the selected price source directly (already in correct format)
            const priceSource = currentPriceSource === 'previous_close' ? 'previous_close' : 'ltp';
            const data = await fetchJson(`${CONSTANTS.API_ENDPOINTS.UNDERLYING_PRICE}?symbol=${currentSymbol}&price_source=${priceSource}`);

            if (data.success) {
                const displayPrice = data.requested_price || data.ltp || 0;
                const sourceLabel = priceSource === 'previous_close' ? ' (Close)' : ' (LTP)';
                DOM.niftyPriceDisplay.textContent = displayPrice.toFixed(2) + sourceLabel;
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
     * Sets the price source and triggers strike reloading, price update, and chart data loading.
     */
    function setPriceSource(source) {
        currentPriceSource = source;
        console.log('Price source changed to:', currentPriceSource);
        // Update the price display
        updateUnderlyingPrice();
        // Reload strikes with the new price source
        loadStrikes();
    }

    /**
     * Sets the timeframe and triggers chart data loading.
     */
    function setTimeframe(timeframe) {
        currentTimeframe = timeframe;
        updateActiveButton(timeframe);
        resetCountdown(); // Reset countdown timer when timeframe changes
        loadChartData();
    }

    /**
     * Resets and starts the countdown timer.
     */
    function resetCountdown() {
        // Clear existing countdown
        if (countdownInterval) clearInterval(countdownInterval);
        
        // Set countdown to the timeframe interval
        const intervalSeconds = timeframeIntervals[currentTimeframe] || 300;
        countdownValue = intervalSeconds;
        updateCountdownDisplay();

        // Start countdown
        countdownInterval = setInterval(() => {
            countdownValue--;
            updateCountdownDisplay();

            // Auto-update charts when countdown reaches zero
            if (countdownValue <= 0) {
                clearInterval(countdownInterval);
                loadChartData(); // Auto-fetch chart data
                resetCountdown(); // Restart countdown
            }
        }, 1000); // Update every 1 second
    }

    /**
     * Formats the timeframe for display (e.g., '5minute' -> '5m').
     */
    function getTimeframeLabel(timeframe) {
        const labels = {
            '1minute': '1m',
            '3minute': '3m',
            '5minute': '5m',
            '15minute': '15m',
            '60minute': '1h',
            '1day': '1D'
        };
        return labels[timeframe] || timeframe;
    }

    /**
     * Updates the countdown display in the UI.
     */
    function updateCountdownDisplay() {
        // Update main countdown timer (if exists)
        if (DOM.countdownTimer) {
            DOM.countdownTimer.textContent = countdownValue;
        }
        
        // Update chart watermark with countdown
        updateChartWatermark(ceChart, currentTimeframe, countdownValue);
        updateChartWatermark(peChart, currentTimeframe, countdownValue);
        updateChartWatermark(combinedChart, currentTimeframe, countdownValue);
    }

    /**
     * Updates the chart watermark to show timeframe and countdown in TradingView style.
     */
    function updateChartWatermark(chart, timeframe, countdown) {
        if (!chart) return;
        
        const timeframeLabel = getTimeframeLabel(timeframe);
        const watermarkText = `⏱ ${timeframeLabel} ${countdown}s`;
        
        chart.applyOptions({
            watermark: {
                color: 'rgba(102, 126, 234, 0.3)',
                visible: true,
                text: watermarkText,
                fontSize: 18,
                horzAlign: 'right',
                vertAlign: 'top'
            }
        });
    }
    
    
    // --- Chart Initialization and Drawing ---

    /**
     * Formats Unix timestamp to IST time string dynamically based on timeframe
     */
    function formatTimeIST(timestamp) {
        // Convert Unix timestamp (seconds) to milliseconds
        const date = new Date(timestamp * 1000);
        
        // Create formatter for IST timezone
        const formatter = new Intl.DateTimeFormat('en-IN', {
            timeZone: 'Asia/Kolkata',
            year: '2-digit',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
        
        return formatter.format(date);
    }

    /**
     * Creates a time formatter for the chart's x-axis based on the current timeframe
     * Note: Lightweight Charts uses this internally for displaying labels
     */
    function createChartTimeFormatter() {
        return (timestamp) => {
            // timestamp is Unix timestamp in seconds
            const date = new Date(timestamp * 1000);
            
            // Format based on timeframe for better readability using IST timezone
            if (currentTimeframe === '1day') {
                // For daily: show date (DD/MM/YY)
                return new Intl.DateTimeFormat('en-IN', { 
                    timeZone: 'Asia/Kolkata',
                    day: '2-digit', 
                    month: '2-digit', 
                    year: '2-digit' 
                }).format(date);
            } else if (currentTimeframe === '60minute') {
                // For hourly: show time (HH:MM)
                return new Intl.DateTimeFormat('en-IN', { 
                    timeZone: 'Asia/Kolkata',
                    hour: '2-digit', 
                    minute: '2-digit', 
                    hour12: false 
                }).format(date);
            } else {
                // For intraday (1m, 3m, 5m, 15m): show time (HH:MM)
                return new Intl.DateTimeFormat('en-IN', { 
                    timeZone: 'Asia/Kolkata',
                    hour: '2-digit', 
                    minute: '2-digit',
                    hour12: false 
                }).format(date);
            }
        };
    }

    /**
     * Initializes the Lightweight Charts objects with proper IST time formatting
     */
function initCharts() {
        if (!window.LightweightCharts) {
            console.error("Lightweight Charts library not loaded.");
            showNotification("Chart library not loaded. Check your HTML head.", "error");
            return;
        }
        const { createChart, CandlestickSeries, LineSeries } = LightweightCharts;

        // Create IST time formatter
        const timeFormatter = createChartTimeFormatter();

        // Light theme configuration with white background and IST time formatting
        const lightTheme = {
            layout: { 
                textColor: '#333333',
                background: { color: '#ffffff', type: 'solid' }
            },
            grid: { 
                vertLines: { color: '#e0e0e0' },
                horzLines: { color: '#e0e0e0' }
            },
            timeScale: {
                textColor: '#333333',
                timeVisible: true,
                secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute'  // Show seconds for intraday
            },
            rightPriceScale: {
                textColor: '#333333'
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,  // Follow mouse exactly, don't snap to candles
            }
        };

        // Initialize CE Chart
        if (ceChart) ceChart.remove();
        ceChart = createChart(document.getElementById('ceChart'), lightTheme);
        ceChart.timeScale().applyOptions({ 
            timeVisible: true,
            secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute'
        });
        ceSeries = ceChart.addSeries(CandlestickSeries, { upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350' });

        // Initialize PE Chart
        if (peChart) peChart.remove();
        peChart = createChart(document.getElementById('peChart'), lightTheme);
        peChart.timeScale().applyOptions({ 
            timeVisible: true,
            secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute'
        });
        peSeries = peChart.addSeries(CandlestickSeries, { upColor: '#2962ff', downColor: '#ef5350', borderVisible: false, wickUpColor: '#2962ff', wickDownColor: '#ef5350' });

        // Initialize Combined Chart
        if (combinedChart) combinedChart.remove();
        combinedChart = createChart(document.getElementById('combinedChart'), lightTheme);
        combinedChart.timeScale().applyOptions({ 
            timeVisible: true,
            secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute'
        });
        combinedCeSeries = combinedChart.addSeries(CandlestickSeries, { upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350', title: 'CE Price' });
        combinedPeSeries = combinedChart.addSeries(CandlestickSeries, { upColor: '#2962ff', downColor: '#ef5350', borderVisible: false, wickUpColor: '#2962ff', wickDownColor: '#ef5350', title: 'PE Price' });
        
        // Note: Lightweight Charts handles crosshair sync natively when multiple charts are on the same page.
        // The native crosshair cursors across charts will synchronize automatically.
    }
    
    /**
     * Renders the individual CE and PE charts.
     */
    function renderIndividualCharts() {
        if (!ceSeries || !peSeries || !ceData || !peData) return;

        // Cache formatted data for hover synchronization
        ceFormattedData = formatChartData(ceData);
        peFormattedData = formatChartData(peData);

        ceSeries.setData(ceFormattedData);
        peSeries.setData(peFormattedData);
        
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
     * Renders the combined CE and PE candlestick chart.
     */
    function renderCombinedChart() {
        if (!combinedCeSeries || !combinedPeSeries || !ceData || !peData) {
            console.log('renderCombinedChart early exit:', { combinedCeSeries: !!combinedCeSeries, combinedPeSeries: !!combinedPeSeries, ceData: !!ceData, peData: !!peData });
            return;
        }
        
        // Always format fresh data (cache is cleared before calling this function)
        ceFormattedData = formatChartData(ceData);
        peFormattedData = formatChartData(peData);
        
        console.log('Setting formatted data:', { ceFormattedData: ceFormattedData?.length, peFormattedData: peFormattedData?.length });
        
        // Set data on chart series
        combinedCeSeries.setData(ceFormattedData);
        combinedPeSeries.setData(peFormattedData);
        
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
        
        // Fit content and apply zoom-in on initial load for larger candles
        if (isInitialLoad) {
            // Fit content first
            ceChart.timeScale().fitContent();
            peChart.timeScale().fitContent();
            combinedChart.timeScale().fitContent();
            
            // Apply stronger zoom-in to make candles larger
            setTimeout(() => {
                applyZoom(ceChart, -0.5);
                applyZoom(peChart, -0.5);
                applyZoom(combinedChart, -0.3);
            }, 100);
            
            isInitialLoad = false;
        }
    }

    /**
     * Applies zoom level to a chart
     * Positive values = zoom out (show more content)
     * Negative values = zoom in (show less content, more detail)
     * Example: 0.5 = 50% zoom out, -0.5 = 50% zoom in
     */
    function applyZoom(chart, zoomPercentage) {
        try {
            const timeScale = chart.timeScale();
            const visibleRange = timeScale.getVisibleLogicalRange();
            
            if (visibleRange) {
                const { from, to } = visibleRange;
                const range = to - from;
                const center = from + range / 2;
                // Zoom: multiply range to zoom out (positive), divide to zoom in (negative)
                const newRange = range * (1 + zoomPercentage);
                const newFrom = center - newRange / 2;
                const newTo = center + newRange / 2;
                
                timeScale.setVisibleLogicalRange({ from: newFrom, to: newTo });
                const zoomType = zoomPercentage < 0 ? 'zoom in' : 'zoom out';
                console.log(`Applied ${zoomType}:`, { percentage: `${Math.abs(zoomPercentage) * 100}%`, newRange });
            }
        } catch (error) {
            console.error('Error applying zoom:', error);
        }
    }

    // --- Data Fetching Logic ---

    /**
     * Fetches strikes and default strikes in a single API call, updates the dropdowns.
     */
    async function loadStrikes() {
        DOM.ceStrikeSelect.innerHTML = '<option value="">Loading...</option>';
        DOM.peStrikeSelect.innerHTML = '<option value="">Loading...</option>';

        const symbol = DOM.symbolSelect.value;
        if (!symbol) return;
        
        showLoader();
        try {
            // Get the selected price source (LTP or Previous Close)
            const priceSource = document.querySelector('input[name="priceSource"]:checked')?.value || 'previous_close';
            
            // Single merged API call that returns both strikes and default strikes
            const data = await fetchJson(`${CONSTANTS.API_ENDPOINTS.OPTIONS_STRIKES}?symbol=${symbol}&price_source=${priceSource}`);

            if (data.success) {
                const strikes = data.strikes.map(s => s.strike.toString());
                populateSelect(DOM.ceStrikeSelect, strikes, 'Select CE Strike');
                populateSelect(DOM.peStrikeSelect, strikes, 'Select PE Strike');

                // Use default strikes from the merged response
                let defaultCeStrike = data.default_ce_strike?.toString();
                let defaultPeStrike = data.default_pe_strike?.toString();
                
                if (defaultCeStrike && defaultPeStrike) {
                    DOM.ceStrikeSelect.value = defaultCeStrike;
                    DOM.peStrikeSelect.value = defaultPeStrike;
                    console.log(`Default strikes for ${symbol} (${priceSource}): CE=${defaultCeStrike}, PE=${defaultPeStrike}`);
                }
                // Fallback: Select first available strike if defaults not available
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
        } finally {
            hideLoader();
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
        showLoader();
        
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
                console.log('Chart data fetched:', { ceDataLength: data.ce_data?.length, peDataLength: data.pe_data?.length });
                ceData = data.ce_data;
                peData = data.pe_data;
                // Clear cached formatted data to force re-formatting
                ceFormattedData = null;
                peFormattedData = null;
                
                // Reinitialize charts with timeframe-aware time formatter
                initCharts();
                
                console.log('Calling renderCombinedChart...');
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
            hideLoader();
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
        if (!currentCeToken || !currentPeToken) {
            console.log('Cannot start auto-update: missing tokens', { currentCeToken, currentPeToken });
            return;
        }
        
        console.log('Starting auto-update interval...');
        
        // Set a 1-second interval for fetching live data
        autoUpdateInterval = setInterval(async () => {
            // Only update if tokens are set AND it's market hours
            if (!currentCeToken || !currentPeToken) {
                console.log('Auto-update stopped: missing tokens');
                clearInterval(autoUpdateInterval);
                return;
            }
            
            // Check if it's within Indian market hours (9:15 AM - 3:30 PM, Monday-Friday)
            if (!isMarketHours()) {
                console.log('Auto-update paused: Outside market hours');
                return;
            }
            
            // Fetch latest chart data (pass tokens for optimized fetch)
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
                    console.log('Auto-update: New data received', { ceDataLength: data.ce_data?.length, peDataLength: data.pe_data?.length });
                    ceData = data.ce_data;
                    peData = data.pe_data;
                    // Clear cached formatted data to force re-formatting
                    ceFormattedData = null;
                    peFormattedData = null;
                    console.log('Rendering updated chart...');
                    renderCombinedChart();
                }
            } catch (error) {
                console.error('Auto-update error:', error);
            }
        }, 1000); // Update every 1 second
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
            // Default to 'ltp'
            const defaultRadio = document.getElementById('ltp');
            if (defaultRadio) {
                defaultRadio.checked = true;
                currentPriceSource = 'ltp';
            }
        }
        
        // Set initial timeframe button active state
        updateActiveButton(currentTimeframe);

        // Initial load of strikes and chart data
        if (currentSymbol) {
            setSymbol(currentSymbol); // This will trigger loadStrikes and then loadChartData
        }
        
        // Start countdown timer
        resetCountdown();
        
        // Load underlying price once on init (will be updated when price source changes)
        updateUnderlyingPrice();
    }

    return {
        init: init
    };
})();

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', OptionsChartApp.init);