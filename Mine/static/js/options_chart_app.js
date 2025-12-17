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
    let ceSeries = null;
    let peSeries = null;
    let combinedChart = null;
    let combinedCeSeries = null;
    let combinedPeSeries = null;
    let ceData = null;
    let peData = null;
    let currentCeToken = null; // Token for auto-update
    let currentPeToken = null; // Token for auto-update
    let currentTimeframe = '5minute';
    let autoUpdateInterval = null;
    let currentSymbol = 'NIFTY';
    let currentPriceSource = 'previous_close';
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
    // Previous day high/low data from backend
    let currentPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
    // Countdown state for price-line badges
    let countdownInterval = null;
    let countdownValue = 0;
    let ceCountdownLine = null;
    let peCountdownLine = null;
    const timeframeIntervals = {
        '1minute': 60,
        '3minute': 180,
        '5minute': 300,
        '15minute': 900,
        '60minute': 3600,
        '1day': 86400
    };

    // --- DOM Elements cache ---
    const DOM = {};

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
    }

    /**
     * Shows the API loader overlay/spinner if present.
     */
    function showLoader() {
        if (DOM.apiLoader) DOM.apiLoader.classList.remove('hidden');
    }

    /**
     * Hides the API loader overlay/spinner if present.
     */
    function hideLoader() {
        if (DOM.apiLoader) DOM.apiLoader.classList.add('hidden');
    }

    /**
     * Populate a select element with options.
     */
    function populateSelect(selectElement, options, defaultMessage = 'Select...') {
        if (!selectElement) return;
        selectElement.innerHTML = `<option value="">${defaultMessage}</option>`;
        options.forEach(val => {
            const opt = document.createElement('option');
            opt.value = val;
            opt.textContent = val;
            selectElement.appendChild(opt);
        });
    }

    /**
     * Check if current time is within Indian market hours (Mon-Fri 09:15-15:30 IST).
     */
    function isMarketHours() {
        const now = new Date();
        const day = now.getDay(); // 0=Sun, 6=Sat
        if (day === 0 || day === 6) return false;
        const minutes = now.getHours() * 60 + now.getMinutes();
        const open = 9 * 60 + 15;   // 555
        const close = 15 * 60 + 30; // 930
        return minutes >= open && minutes <= close;
    }

    // --- Constants ---
    const CONSTANTS = {
        API_ENDPOINTS: {
            OPTIONS_INIT: '/api/options-init',
            UNDERLYING_PRICE: '/api/underlying-price',
            OPTIONS_STRIKES: '/api/options-strikes',
            OPTIONS_DEFAULT_STRIKES: '/api/options-default-strikes',
            OPTIONS_CHART_DATA: '/api/options-chart-data',
            OPTIONS_PDH_PDL: '/api/options-pdh-pdl'
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
     * Converts raw data to the Lightweight Charts format.
     * Backend sends UTC timestamps.
     */
    function formatChartData(data) {
        return data.map(item => {
            let timestamp;

            if (typeof item.date === 'number') {
                if (item.date > 10000000000) {
                    timestamp = Math.floor(item.date / 1000);
                } else {
                    timestamp = item.date;
                }
            } else if (typeof item.date === 'string') {
                const dateObj = new Date(item.date);
                timestamp = Math.floor(dateObj.getTime() / 1000);
            } else {
                timestamp = Math.floor(new Date(item.date).getTime() / 1000);
            }

            return {
                time: timestamp,
                open: item.open,
                high: item.high,
                low: item.low,
                close: item.close,
                value: item.close
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

    /**
     * Gets previous day's high and low from chart data.
     * Uses the second-to-last candle (yesterday's data, not today's partial)
     */
    function getPreviousDayHighLow(data) {
        if (!data || data.length < 2) return { high: null, low: null };
        // Use second-to-last candle (yesterday's complete data)
        const previousDayCandle = data[data.length - 2];
        return {
            high: previousDayCandle.high || null,
            low: previousDayCandle.low || null
        };
    }

    /**
     * Adds previous day high/low price lines to a chart
     * Returns array of created price lines for later removal
     */
    function addPreviousDayLines(series, cePdh, cePdl, pePdh, pePdl, isForCeChart) {
        const lines = [];
        
        if (isForCeChart) {
            // CE Chart lines
            if (cePdh !== null) {
                lines.push(series.createPriceLine({
                    price: cePdh,
                    color: '#000000',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'CE PDH'
                }));
            }
            if (cePdl !== null) {
                lines.push(series.createPriceLine({
                    price: cePdl,
                    color: '#000000',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'CE PDL'
                }));
            }
            if (pePdh !== null) {
                lines.push(series.createPriceLine({
                    price: pePdh,
                    color: '#10b981',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'PE PDH'
                }));
            }
            if (pePdl !== null) {
                lines.push(series.createPriceLine({
                    price: pePdl,
                    color: '#ef4444',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'PE PDL'
                }));
            }
        } else {
            // PE Chart lines
            if (pePdh !== null) {
                lines.push(series.createPriceLine({
                    price: pePdh,
                    color: '#000000',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'PE PDH'
                }));
            }
            if (pePdl !== null) {
                lines.push(series.createPriceLine({
                    price: pePdl,
                    color: '#000000',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'PE PDL'
                }));
            }
            if (cePdh !== null) {
                lines.push(series.createPriceLine({
                    price: cePdh,
                    color: '#10b981',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'CE PDH'
                }));
            }
            if (cePdl !== null) {
                lines.push(series.createPriceLine({
                    price: cePdl,
                    color: '#ef4444',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'CE PDL'
                }));
            }
        }
        
        return lines;
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
     * Now uses cached data from loadStrikes() to avoid redundant API calls.
     */
    async function updateUnderlyingPrice() {
        if (!currentSymbol || !DOM.niftyPriceDisplay) return;

        // If we have cached underlying price data from loadStrikes(), use it
        if (window._cachedUnderlyingPrice && window._cachedUnderlyingPrice.symbol === currentSymbol) {
            const data = window._cachedUnderlyingPrice;
            const sourceLabel = data.source_label || '';
            DOM.niftyPriceDisplay.textContent = (data.requested_price || 0).toFixed(2) + sourceLabel;
            return;
        }

        // Fallback: refetch from merged init endpoint if no cached data
        try {
            const priceSource = currentPriceSource === 'previous_close' ? 'previous_close' : 'ltp';
            const data = await fetchJson(`${CONSTANTS.API_ENDPOINTS.OPTIONS_INIT}?symbol=${currentSymbol}&price_source=${priceSource}`);

            if (data.success && data.underlying_price) {
                const sourceLabel = data.underlying_price.source_label || '';
                DOM.niftyPriceDisplay.textContent = (data.underlying_price.requested_price || 0).toFixed(2) + sourceLabel;
                // Update cache
                window._cachedUnderlyingPrice = {
                    symbol: currentSymbol,
                    requested_price: data.underlying_price.requested_price,
                    source_label: data.underlying_price.source_label
                };
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
        loadChartData();
        // Restart chart countdown badges to match new timeframe
        resetCountdown();
    }

    /**
     * Resets and starts the countdown timer.
     */
    // Countdown removed
    function resetCountdown() {
        if (countdownInterval) clearInterval(countdownInterval);
        const intervalSeconds = timeframeIntervals[currentTimeframe] || 300;
        countdownValue = intervalSeconds;
        // Start ticking and updating badge labels
        countdownInterval = setInterval(() => {
            countdownValue = Math.max(0, countdownValue - 1);
            updateCountdownPriceLines(countdownValue);
            if (countdownValue <= 0) {
                clearInterval(countdownInterval);
                // Auto-fetch fresh data, then restart countdown
                loadChartData();
                resetCountdown();
            }
        }, 1000);
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
    // Countdown UI removed

    /**
     * Creates/updates price-line badges on CE/PE charts showing remaining seconds (TradingView-style)
     */
    // Countdown price-line badges removed
    function updateCountdownPriceLines(secondsRemaining) {
        if (!ceSeries || !peSeries || !ceData || !peData) return;

        const latestCePrice = getLatestPrice(ceData);
        const latestPePrice = getLatestPrice(peData);
        const label = formatCountdownLabel(secondsRemaining);

        // CE chart badge
        if (ceCountdownLine) {
            try { ceSeries.removePriceLine(ceCountdownLine); } catch (e) {}
            ceCountdownLine = null;
        }
        if (latestCePrice !== null) {
            ceCountdownLine = ceSeries.createPriceLine({
                price: latestCePrice,
                color: CONSTANTS.CHART_CONFIG.CE_COLOR,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: label
            });
        }

        // PE chart badge
        if (peCountdownLine) {
            try { peSeries.removePriceLine(peCountdownLine); } catch (e) {}
            peCountdownLine = null;
        }
        if (latestPePrice !== null) {
            peCountdownLine = peSeries.createPriceLine({
                price: latestPePrice,
                color: CONSTANTS.CHART_CONFIG.PE_COLOR,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: label
            });
        }
    }

    // Formats countdown seconds to mm:ss (e.g., 06:10)
    // Countdown label removed
    function formatCountdownLabel(totalSeconds) {
        const secs = Math.max(0, Math.floor(totalSeconds));
        const m = Math.floor(secs / 60).toString().padStart(2, '0');
        const s = (secs % 60).toString().padStart(2, '0');
        return `${m}:${s}`;
    }

    /**
     * Updates the chart watermark to show timeframe and countdown in TradingView style.
     */
    // Watermark countdown removed
    
    
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
                    textColor: '#1f2937',
                    background: { color: '#ffffff', type: 'solid' }
                },
                grid: {
                    vertLines: { color: '#f0f0f0' },
                    horzLines: { color: '#f0f0f0' }
                },
                timeScale: {
                    textColor: '#6b7280',
                    borderColor: '#e5e7eb',
                    timeVisible: true,
                    secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute' // Show seconds for intraday
                },
                rightPriceScale: {
                    textColor: '#6b7280',
                    borderColor: '#e5e7eb'
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal, // Follow mouse exactly, don't snap to candles
                    vertLine: {
                        color: '#d1d5db',
                        width: 1,
                        style: 0
                    },
                    horzLine: {
                        color: '#d1d5db',
                        width: 1,
                        style: 0
                    }
                }
            };

        // Initialize CE Chart
        if (ceChart) ceChart.remove();
        ceChart = createChart(document.getElementById('ceChart'), lightTheme);
        ceChart.timeScale().applyOptions({ 
            timeVisible: true,
            secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute'
        });
        ceSeries = ceChart.addSeries(CandlestickSeries, { upColor: '#10b981', downColor: '#ef4444', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#ef4444' });

        // Initialize PE Chart
        if (peChart) peChart.remove();
        peChart = createChart(document.getElementById('peChart'), lightTheme);
        peChart.timeScale().applyOptions({ 
            timeVisible: true,
            secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute'
        });
        peSeries = peChart.addSeries(CandlestickSeries, {
            upColor: '#3b82f6',
            downColor: '#000000',
            borderVisible: false,
            borderColor: '#3b82f6',
            wickUpColor: '#3b82f6',
            wickDownColor: '#111827'
        });

        // Initialize Combined Chart
        if (combinedChart) combinedChart.remove();
        combinedChart = createChart(document.getElementById('combinedChart'), lightTheme);
        combinedChart.timeScale().applyOptions({ 
            timeVisible: true,
            secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute'
        });
        combinedCeSeries = combinedChart.addSeries(CandlestickSeries, {
            upColor: '#10b981',
            downColor: '#ef4444',
            borderVisible: false,
            wickUpColor: '#10b981',
            wickDownColor: '#ef4444',
            title: 'CE Price'
        });
        combinedPeSeries = combinedChart.addSeries(CandlestickSeries, {
            upColor: '#3b82f6',
            downColor: '#000000',
            borderVisible: false,
            borderColor: '#3b82f6',
            wickUpColor: '#3b82f6',
            wickDownColor: '#111827',
            title: 'PE Price'
        });
        
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

        // Use PDH/PDL from backend if available, otherwise fallback to chart data
        let cePdh = currentPdhPdl.ce_pdh;
        let cePdl = currentPdhPdl.ce_pdl;
        let pePdh = currentPdhPdl.pe_pdh;
        let pePdl = currentPdhPdl.pe_pdl;
        
        if (cePdh === null || cePdl === null || pePdh === null || pePdl === null) {
            // Fallback to chart data if backend values not available
            const cePdh_pdl = getPreviousDayHighLow(ceData);
            const pePdh_pdl = getPreviousDayHighLow(peData);
            cePdh = cePdh || cePdh_pdl.high;
            cePdl = cePdl || cePdh_pdl.low;
            pePdh = pePdh || pePdh_pdl.high;
            pePdl = pePdl || pePdh_pdl.low;
        }

        // Add PDH/PDL lines to CE Chart
        cePriceLines = cePriceLines.concat(
            addPreviousDayLines(ceSeries, cePdh, cePdl, pePdh, pePdl, true)
        );

        // Add PDH/PDL lines to PE Chart
        pePriceLines = pePriceLines.concat(
            addPreviousDayLines(peSeries, cePdh, cePdl, pePdh, pePdl, false)
        );

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
        // Refresh countdown badges after rendering, using current countdown value
        updateCountdownPriceLines(countdownValue);
    }
    
    /**
     * Renders the combined CE and PE candlestick chart.
     */
    function renderCombinedChart(cePdh, cePdl, pePdh, pePdl) {
        if (!combinedCeSeries || !combinedPeSeries || !ceData || !peData) {
            console.log('renderCombinedChart early exit:', { combinedCeSeries: !!combinedCeSeries, combinedPeSeries: !!combinedPeSeries, ceData: !!ceData, peData: !!peData });
            return;
        }
        
        // Use provided PDH/PDL if passed, else fallback to cached values
        currentPdhPdl = {
            ce_pdh: cePdh !== undefined ? cePdh : currentPdhPdl.ce_pdh,
            ce_pdl: cePdl !== undefined ? cePdl : currentPdhPdl.ce_pdl,
            pe_pdh: pePdh !== undefined ? pePdh : currentPdhPdl.pe_pdh,
            pe_pdl: pePdl !== undefined ? pePdl : currentPdhPdl.pe_pdl
        };
        
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
        ceTimerPriceLine = null;
        peTimerPriceLine = null;
        
        const latestCePrice = getLatestPrice(ceData);
        const latestPePrice = getLatestPrice(peData);

        // Add price lines for the last traded price if in market hours (no PDH/PDL lines in combined chart)
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
            // Set visible range to show only last 2 days
            setTimeout(() => {
                setVisibleRangeToDays(ceChart, 2);
                setVisibleRangeToDays(peChart, 2);
                setVisibleRangeToDays(combinedChart, 2);
            }, 100);
            
            isInitialLoad = false;
        }
    }

    /**
     * Sets the visible time range to show only the specified number of days from the end
     */
    function setVisibleRangeToDays(chart, days) {
        try {
            const timeScale = chart.timeScale();
            const visibleRange = timeScale.getVisibleLogicalRange();
            
            if (visibleRange && ceFormattedData && ceFormattedData.length > 0) {
                const totalBars = ceFormattedData.length;
                // Estimate bars per day based on timeframe
                const barsPerDay = {
                    '1minute': 375,    // 6.25 hours * 60 minutes
                    '3minute': 125,    // 6.25 hours * 20
                    '5minute': 75,     // 6.25 hours * 12
                    '15minute': 25,    // 6.25 hours * 4
                    '60minute': 7,     // ~6-7 hours
                    '1day': 1
                };
                
                const barsToShow = (barsPerDay[currentTimeframe] || 75) * days;
                const from = Math.max(0, totalBars - barsToShow);
                const to = totalBars;
                
                timeScale.setVisibleLogicalRange({ from, to });
                console.log(`Set visible range to ${days} days:`, { from, to, totalBars, barsToShow });
            }
        } catch (error) {
            console.error('Error setting visible range:', error);
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
     * Fetches strikes, underlying price, and PDH/PDL in a single merged API call.
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
            
            // Single merged API call that returns strikes, underlying price, and PDH/PDL
            const data = await fetchJson(`${CONSTANTS.API_ENDPOINTS.OPTIONS_INIT}?symbol=${symbol}&price_source=${priceSource}`);

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
                
                // Cache underlying price data
                if (data.underlying_price) {
                    window._cachedUnderlyingPrice = {
                        symbol: symbol,
                        requested_price: data.underlying_price.requested_price,
                        source_label: data.underlying_price.source_label
                    };
                }
                
                // Update tokens for auto-update
                if (data.default_ce_token) currentCeToken = data.default_ce_token;
                if (data.default_pe_token) currentPeToken = data.default_pe_token;
                
                // Update underlying price display
                updateUnderlyingPrice();
                
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
            // PDH/PDL is now cached from loadStrikes(), but refetch if strikes changed
            if (!currentPdhPdl.ce_pdh && !currentPdhPdl.ce_pdl && currentCeToken && currentPeToken) {
                const pdhPayload = {
                    symbol: currentSymbol,
                    ce_strike: ceStrike,
                    pe_strike: peStrike,
                    ce_token: currentCeToken,
                    pe_token: currentPeToken
                };
                const pdhResp = await fetchJson(CONSTANTS.API_ENDPOINTS.OPTIONS_PDH_PDL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(pdhPayload)
                });
                if (pdhResp?.success) {
                    currentPdhPdl = pdhResp.pdh_pdl || { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
                    if (pdhResp.ce_token) currentCeToken = pdhResp.ce_token;
                    if (pdhResp.pe_token) currentPeToken = pdhResp.pe_token;
                }
            }

            // Pass tokens to backend for FAST PATH (no token lookup needed)
            const data = await fetchJson(CONSTANTS.API_ENDPOINTS.OPTIONS_CHART_DATA, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ce_token: currentCeToken,
                    pe_token: currentPeToken,
                    timeframe: currentTimeframe
                })
            });

            if (data.success && data.data) {
                // Parse merged data array and separate CE/PE by type field
                ceData = data.data.filter(candle => candle.type === 'CE');
                peData = data.data.filter(candle => candle.type === 'PE');
                
                console.log('Chart data fetched:', { ceDataLength: ceData.length, peDataLength: peData.length });
                
                // Clear cached formatted data to force re-formatting
                ceFormattedData = null;
                peFormattedData = null;
                
                // Reinitialize charts with timeframe-aware time formatter
                initCharts();
                
                console.log('Calling renderCombinedChart with PDH/PDL:', currentPdhPdl);
                renderCombinedChart(currentPdhPdl.ce_pdh, currentPdhPdl.ce_pdl, currentPdhPdl.pe_pdh, currentPdhPdl.pe_pdl);
                
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
        
        // Set a 10-second interval for fetching live data (reduced API pressure)
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
                        timeframe: currentTimeframe,
                        live: true
                    })
                });

                if (data.needs_login) {
                    clearInterval(autoUpdateInterval);
                    return; // fetchJson handles the redirect/notification
                }

                if (data.success && data.data) {
                    // Parse merged data array and separate CE/PE by type field
                    ceData = data.data.filter(candle => candle.type === 'CE');
                    peData = data.data.filter(candle => candle.type === 'PE');
                    
                    console.log('Auto-update: New data received', { ceDataLength: ceData.length, peDataLength: peData.length });
                    // Clear cached formatted data to force re-formatting
                    ceFormattedData = null;
                    peFormattedData = null;
                    console.log('Rendering updated chart...');
                    renderCombinedChart();
                }
            } catch (error) {
                console.error('Auto-update error:', error);
            }
        }, 10000); // Update every 10 seconds
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

        // Force default to 'previous_close' on load (override any stale browser state)
        const ltpRadio = document.getElementById('ltp');
        const pdcRadio = document.getElementById('previous_close');
        if (pdcRadio) {
            pdcRadio.checked = true;
            currentPriceSource = 'previous_close';
            if (ltpRadio) ltpRadio.checked = false;
        } else {
            const selectedPriceSourceRadio = document.querySelector('input[name="priceSource"]:checked');
            if (selectedPriceSourceRadio) {
                currentPriceSource = selectedPriceSourceRadio.value;
            }
        }
        
        // Set initial timeframe button active state
        updateActiveButton(currentTimeframe);

        // Initial load of strikes and chart data
        if (currentSymbol) {
            setSymbol(currentSymbol); // This will trigger loadStrikes (which caches underlying price) and then loadChartData
        }
        
        // Start chart countdown badges
        resetCountdown();
        
        // Note: updateUnderlyingPrice() is NOT called here to avoid duplicate API call
        // loadStrikes() already caches underlying price data from the merged /api/options-init endpoint
    }

    return {
        init: init
    };
})();

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', OptionsChartApp.init);