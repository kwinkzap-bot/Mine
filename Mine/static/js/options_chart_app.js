/**
 * options_chart_app.js
 * Pure Vanilla JavaScript module for the Options Chart Viewer.
 * It uses the Lightweight Charts library.
 */

const OptionsChartApp = (function () {
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
    // Previous day high/low data from backend
    let currentPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
    // Cache for OPTIONS_INIT response to avoid duplicate calls
    let cachedInitResponse = null;
    let cachedInitSymbol = null;
    let cachedInitPriceSource = null;
    // // Countdown state for price-line badges (COMMENTED OUT)
    // let countdownInterval = null;
    // let countdownValue = 0;
    // let ceCountdownLine = null;
    // let peCountdownLine = null;
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

        DOM.ceStrikeSelect = document.getElementById('ceStrike');
        DOM.peStrikeSelect = document.getElementById('peStrike');
        DOM.loadChartBtn = document.getElementById('fetchChartBtn');
        DOM.buyPeBtn = document.getElementById('buyPeBtn');
        DOM.buyCeBtn = document.getElementById('buyCeBtn');
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

    // --- Use global CONSTANTS from constants.js ---
    // The following are now available globally:
    // - CONSTANTS.API_ENDPOINTS
    // - CONSTANTS.CSS_CLASSES
    // - CONSTANTS.CHART_CONFIG

    // --- Utility Functions ---

    /**
     * Converts raw data to the Lightweight Charts format.
     * Backend sends UTC timestamps.
     */
    function formatChartData(data) {
        // Backend provides UTC timestamps - Lightweight Charts displays them in browser's timezone
        return data.map(item => {
            let timestamp;

            if (typeof item.date === 'number') {
                // Backend returns UTC Unix timestamp
                timestamp = item.date;
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
     * Sets the price source and triggers strike reloading, price update, and chart data loading.
     */
    function setPriceSource(source) {
        currentPriceSource = source;
        console.log('Price source changed to:', currentPriceSource);
        // Invalidate cached OPTIONS_INIT response since price source changed
        cachedInitResponse = null;
        cachedInitSymbol = null;
        cachedInitPriceSource = null;
        // Update the price display
        updateUnderlyingPrice();
        // Reload strikes with the new price source
        loadStrikes();
    }

    /**
     * Sets the timeframe and fetches chart data for new timeframe only.
     * Uses existing tokens - no need to refetch tokens or PDH/PDL as they don't change with timeframe.
     */
    function setTimeframe(timeframe) {
        currentTimeframe = timeframe;
        updateActiveButton(timeframe);
        fetchChartDataOnlyForTimeframe();
        // // Restart chart countdown badges to match new timeframe (COMMENTED OUT)
        // resetCountdown();
    }

    /**
     * Fetches ONLY chart data for the current timeframe without refetching tokens or PDH/PDL.
     * Optimized for timeframe changes where tokens and PDH/PDL remain constant.
     */
    async function fetchChartDataOnlyForTimeframe() {
        if (!currentCeToken || !currentPeToken) {
            console.warn('Tokens not available, cannot fetch chart data for timeframe change');
            return;
        }

        showLoader();

        try {
            // Fetch only chart data using existing tokens
            const result = await fetchChartDataFromApi(currentCeToken, currentPeToken);

            if (result.success && result.ceData && result.peData) {
                ceData = result.ceData;
                peData = result.peData;

                console.log('Chart data loaded for timeframe:', { timeframe: currentTimeframe, ceDataLength: ceData.length, peDataLength: peData.length });

                // Clear cached formatted data to force re-formatting
                ceFormattedData = null;
                peFormattedData = null;

                // Render chart with existing PDH/PDL
                console.log('Rendering chart for timeframe change with existing PDH/PDL:', currentPdhPdl);
                renderCombinedChart(currentPdhPdl.ce_pdh, currentPdhPdl.ce_pdl, currentPdhPdl.pe_pdh, currentPdhPdl.pe_pdl);

                showNotification(`Chart data loaded for ${currentTimeframe} timeframe.`, 'success');
            } else {
                showNotification(result.message || 'Failed to load chart data.', 'error');
            }
        } catch (error) {
            console.error('Error fetching chart data for timeframe:', error);
            showNotification('Error loading chart data.', 'error');
        } finally {
            hideLoader();
        }
    }

    /**
     * Resets and starts the countdown timer.
     */
    // COMMENTED OUT: Countdown functionality disabled
    /* 
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
                // loadChartData();
                resetCountdown();
            }
        }, 1000);
    }
    */

    /**
     * Updates the countdown display in the UI.
     */
    // Countdown UI removed

    /**
     * Creates/updates price-line badges on CE/PE charts showing remaining seconds (TradingView-style)
     */
    // COMMENTED OUT: Countdown functionality disabled
    /*
    function updateCountdownPriceLines(secondsRemaining) {
        if (!ceSeries || !peSeries || !ceData || !peData) return;

        const latestCePrice = getLatestPrice(ceData);
        const latestPePrice = getLatestPrice(peData);
        const label = formatCountdownLabel(secondsRemaining);

        // CE chart countdown badge (only label, no price line)
        if (ceCountdownLine) {
            try { ceSeries.removePriceLine(ceCountdownLine); } catch (e) { }
            ceCountdownLine = null;
        }
        if (latestCePrice !== null) {
            ceCountdownLine = ceSeries.createPriceLine({
                price: latestCePrice,
                color: CONSTANTS.CHART_CONFIG.CE_COLOR,
                lineWidth: 0,  // Hide the line itself
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,  // Show countdown timer
                title: label  // Display countdown timer
            });
        }

        // PE chart countdown badge (only label, no price line)
        if (peCountdownLine) {
            try { peSeries.removePriceLine(peCountdownLine); } catch (e) { }
            peCountdownLine = null;
        }
        if (latestPePrice !== null) {
            peCountdownLine = peSeries.createPriceLine({
                price: latestPePrice,
                color: CONSTANTS.CHART_CONFIG.PE_COLOR,
                lineWidth: 0,  // Hide the line itself
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,  // Show countdown timer
                title: label  // Display countdown timer
            });
        }
    }
    */

    // COMMENTED OUT: Countdown functionality disabled
    /*
    function formatCountdownLabel(totalSeconds) {
        const secs = Math.max(0, Math.floor(totalSeconds));
        const m = Math.floor(secs / 60).toString().padStart(2, '0');
        const s = (secs % 60).toString().padStart(2, '0');
        return `${m}:${s}`;
    }
    */

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
        const { createChart, CandlestickSeries } = LightweightCharts;

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
                secondsVisible: currentTimeframe !== '1day' && currentTimeframe !== '60minute', // Show seconds for intraday
                rightOffset: 250
            },
            rightPriceScale: {
                textColor: '#6b7280',
                borderColor: '#e5e7eb',
                // scaleMargins: { top: 0.1, bottom: 0.1 }
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal, // Follow mouse exactly, don't snap to candles
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
        peSeries = peChart.addSeries(CandlestickSeries, { upColor: '#10b981', downColor: '#ef4444', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#ef4444' });

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
            upColor: '#00bcd4',
            downColor: '#000000',
            borderVisible: false,
            borderColor: '#00bcd4',
            wickUpColor: '#00bcd4',
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

        // Preserve visible ranges before updating data
        const ceTimeScale = ceChart?.timeScale();
        const peTimeScale = peChart?.timeScale();
        const cePreservedRange = ceTimeScale?.getVisibleLogicalRange();
        const pePreservedRange = peTimeScale?.getVisibleLogicalRange();

        // Cache formatted data for hover synchronization
        ceFormattedData = formatChartData(ceData);
        peFormattedData = formatChartData(peData);

        ceSeries.setData(ceFormattedData);
        peSeries.setData(peFormattedData);

        // Restore visible ranges after data update
        if (cePreservedRange && ceTimeScale && !isInitialLoad) {
            setTimeout(() => {
                ceTimeScale.setVisibleLogicalRange(cePreservedRange);
            }, 0);
        }
        if (pePreservedRange && peTimeScale && !isInitialLoad) {
            setTimeout(() => {
                peTimeScale.setVisibleLogicalRange(pePreservedRange);
            }, 0);
        }

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
            // LTP line removed - showing only countdown timer
        }

        if (latestPePrice !== null && currentPriceSource === 'current_close' && isMarketHours()) {
            // LTP line removed - showing only countdown timer
        }
        // // Refresh countdown badges after rendering, using current countdown value (COMMENTED OUT)
        // updateCountdownPriceLines(countdownValue);
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

        // Preserve visible range before updating data
        const ceTimeScale = combinedChart?.timeScale();
        const preservedRange = ceTimeScale?.getVisibleLogicalRange();

        // Set data on chart series
        combinedCeSeries.setData(ceFormattedData);
        combinedPeSeries.setData(peFormattedData);

        // Restore visible range after data update (prevents resetting to all data)
        if (preservedRange && ceTimeScale && !isInitialLoad) {
            setTimeout(() => {
                ceTimeScale.setVisibleLogicalRange(preservedRange);
            }, 0);
        }

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

        // Show 2 days of data on initial load
        if (isInitialLoad) {
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

    // --- Data Fetching Logic ---

    /**
     * Fetches chart data from the API endpoint and returns separated CE/PE data.
     * @param {string} ceToken - CE instrument token
     * @param {string} peToken - PE instrument token
     * @returns {Promise<Object>} { success: boolean, ceData: array, peData: array, message: string }
     */
    async function fetchChartDataFromApi(ceToken, peToken) {
        try {
            console.log('fetchChartDataFromApi called with tokens:', { ceToken, peToken, timeframe: currentTimeframe });
            
            if (!ceToken || !peToken) {
                console.error('Missing tokens for chart data fetch:', { ceToken, peToken });
                return { success: false, ceData: null, peData: null, message: 'Missing CE or PE token' };
            }
            
            const payload = {
                ce_token: ceToken,
                pe_token: peToken,
                timeframe: currentTimeframe,
                live: true
            };
            
            console.log('Sending payload to OPTIONS_CHART_DATA:', payload);
            
            const data = await fetchJson(CONSTANTS.API_ENDPOINTS.OPTIONS_CHART_DATA, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (data.needs_login) {
                return { success: false, ceData: null, peData: null, message: 'Login required', needsLogin: true };
            }

            if (data.success && data.data) {
                // Parse merged data array and separate CE/PE by type field
                const ceChartData = data.data.filter(candle => candle.type === 'CE');
                const peChartData = data.data.filter(candle => candle.type === 'PE');

                console.log('Chart data fetched from API:', { ceDataLength: ceChartData.length, peDataLength: peChartData.length });
                return { success: true, ceData: ceChartData, peData: peChartData, message: '' };
            } else {
                return { success: false, ceData: null, peData: null, message: data.message || 'Failed to load chart data.' };
            }
        } catch (error) {
            console.error('Error fetching chart data from API:', error);
            return { success: false, ceData: null, peData: null, message: error.message };
        }
    }

    /**
     * Fetches strikes, underlying price, and PDH/PDL in a single merged API call.
     */
    async function loadStrikes() {
        DOM.ceStrikeSelect.innerHTML = '<option value="">Loading...</option>';
        DOM.peStrikeSelect.innerHTML = '<option value="">Loading...</option>';

        const symbol = 'NIFTY';  // NIFTY 50 only

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

                // IMPORTANT: Cache the full OPTIONS_INIT response to avoid duplicate calls in loadChartData()
                cachedInitResponse = data;
                cachedInitSymbol = symbol;
                cachedInitPriceSource = priceSource;

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
     * Only updates the data without reinitializing the charts (they're initialized once in init()).
     * Fetches tokens for the currently selected strikes to ensure fresh data.
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
            // IMPORTANT: Fetch tokens for the CURRENTLY SELECTED strikes, not the cached default ones
            // This ensures we get fresh tokens when user manually changes strikes
            let ceTokenToUse = null;
            let peTokenToUse = null;
            let pdhToUse = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };

            // Step 1: Get tokens for the selected strikes
            // Try to use cached OPTIONS_INIT response first to avoid duplicate API calls
            try {
                const priceSource = currentPriceSource === 'previous_close' ? 'previous_close' : 'ltp';
                
                // Check if we have a valid cached response for this symbol and price source
                let initResp = null;
                if (cachedInitResponse && cachedInitSymbol === currentSymbol && cachedInitPriceSource === priceSource) {
                    console.log('Using cached OPTIONS_INIT response');
                    initResp = cachedInitResponse;
                } else {
                    console.log('Fetching fresh OPTIONS_INIT response (cache miss or params changed)');
                    initResp = await fetchJson(`${CONSTANTS.API_ENDPOINTS.OPTIONS_INIT}?symbol=${currentSymbol}&price_source=${priceSource}`);
                    // Update cache
                    if (initResp?.success) {
                        cachedInitResponse = initResp;
                        cachedInitSymbol = currentSymbol;
                        cachedInitPriceSource = priceSource;
                    }
                }

                if (initResp?.success) {
                    // Get the strikes data to find tokens for currently selected strikes
                    const strikes = initResp.strikes || [];
                    console.log(`Looking for strikes in OPTIONS_INIT response: CE strike=${ceStrike}, PE strike=${peStrike}`);
                    console.log('Available strikes:', strikes.map(s => `${s.strike} (CE: ${s.ce_token}, PE: ${s.pe_token})`));
                    
                    // Find strike objects for CE and PE (normalize to strings for comparison)
                    const ceStrikeStr = ceStrike.toString();
                    const peStrikeStr = peStrike.toString();
                    const ceStrikeObj = strikes.find(s => {
                        const strikeStr = s.strike.toString();
                        return strikeStr === ceStrikeStr;
                    });
                    const peStrikeObj = strikes.find(s => {
                        const strikeStr = s.strike.toString();
                        return strikeStr === peStrikeStr;
                    });
                    
                    console.log('Found strike objects:', { ceStrikeObj, peStrikeObj });
                    
                    if (ceStrikeObj && peStrikeObj) {
                        ceTokenToUse = ceStrikeObj.ce_token;
                        peTokenToUse = peStrikeObj.pe_token;
                        if (!ceTokenToUse || !peTokenToUse) {
                            console.warn('Strike objects found but missing tokens:', { ceStrikeObj, peStrikeObj });
                            // Fallback to cached tokens
                            ceTokenToUse = currentCeToken;
                            peTokenToUse = currentPeToken;
                        }
                        console.log(`Got tokens for selected strikes CE=${ceStrike}, PE=${peStrike}:`, { ceTokenToUse, peTokenToUse });
                    } else {
                        // Fallback to cached tokens if strikes not found in response
                        ceTokenToUse = currentCeToken;
                        peTokenToUse = currentPeToken;
                        console.warn('Selected strikes not found in OPTIONS_INIT response, using cached tokens');
                    }
                } else {
                    ceTokenToUse = currentCeToken;
                    peTokenToUse = currentPeToken;
                    console.warn('Failed to get OPTIONS_INIT data, using cached tokens');
                }
            } catch (error) {
                console.error('Error getting tokens from OPTIONS_INIT:', error);
                ceTokenToUse = currentCeToken;
                peTokenToUse = currentPeToken;
            }

            // Step 2: Call OPTIONS_PDH_PDL endpoint with TOKENS (not strikes)
            try {
                const pdhPayload = {
                    ce_token: ceTokenToUse,
                    pe_token: peTokenToUse
                };
                
                const pdhResp = await fetchJson(CONSTANTS.API_ENDPOINTS.OPTIONS_PDH_PDL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(pdhPayload)
                });

                if (pdhResp?.success) {
                    pdhToUse = pdhResp.pdh_pdl || { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
                    console.log('Fetched PDH/PDL using tokens:', pdhToUse);
                } else {
                    console.warn('Failed to fetch PDH/PDL:', pdhResp?.error);
                }
            } catch (error) {
                console.error('Error fetching PDH/PDL with tokens:', error);
            }

            // Step 3: Use tokens for chart data fetch
            const result = await fetchChartDataFromApi(ceTokenToUse, peTokenToUse);

            if (result.success && result.ceData && result.peData) {
                ceData = result.ceData;
                peData = result.peData;

                // Update global tokens for auto-update
                currentCeToken = ceTokenToUse;
                currentPeToken = peTokenToUse;
                currentPdhPdl = pdhToUse;

                console.log('Chart data loaded:', { ceDataLength: ceData.length, peDataLength: peData.length });

                // Clear cached formatted data to force re-formatting
                ceFormattedData = null;
                peFormattedData = null;

                // IMPORTANT: Do NOT reinitialize charts here - they're already initialized in init()
                // Only call renderCombinedChart() to update the data on existing chart objects
                console.log('Calling renderCombinedChart with PDH/PDL:', currentPdhPdl);
                renderCombinedChart(currentPdhPdl.ce_pdh, currentPdhPdl.ce_pdl, currentPdhPdl.pe_pdh, currentPdhPdl.pe_pdl);

                startAutoUpdate(); // Restart auto-update with new data and tokens

                showNotification('Chart data loaded successfully.', 'success');
            } else {
                showNotification(result.message, 'error');
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
                const result = await fetchChartDataFromApi(currentCeToken, currentPeToken);

                if (result.needsLogin) {
                    clearInterval(autoUpdateInterval);
                    return; // fetchJson handles the redirect/notification
                }

                if (result.success && result.ceData && result.peData) {
                    ceData = result.ceData;
                    peData = result.peData;

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
        }, 3000); // Update every 3 seconds
    }


    // --- Order Placement Functions ---

    /**
     * Place an order for a given option type (CE or PE)
     * @param {string} optionType - 'CE' or 'PE'
     */
    async function placeOrder(optionType) {
        const strike = optionType === 'CE' ? DOM.ceStrikeSelect.value : DOM.peStrikeSelect.value;
        
        if (!strike) {
            alert(`Please select a ${optionType} strike first`);
            return;
        }

        const button = optionType === 'CE' ? DOM.buyCeBtn : DOM.buyPeBtn;
        const originalText = button.textContent;
        
        try {
            button.disabled = true;
            button.textContent = 'Placing...';
            
            // Call the API to place the order
            const response = await fetch('/api/place-live-order', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({
                    option_type: optionType,
                    strike: parseInt(strike),
                    symbol: currentSymbol
                })
            });

            const data = await response.json();

            if (data.success) {
                button.textContent = '✅ Order Placed!';
                showNotification(`${optionType} Order placed successfully! Order ID: ${data.order_id}`, 'success');
                setTimeout(() => {
                    button.textContent = originalText;
                    button.disabled = false;
                }, 3000);
            } else {
                button.textContent = originalText;
                button.disabled = false;
                showNotification(`Error: ${data.error || 'Failed to place order'}`, 'error');
            }
        } catch (error) {
            button.textContent = originalText;
            button.disabled = false;
            console.error(`Error placing ${optionType} order:`, error);
            showNotification(`Error: ${error.message}`, 'error');
        }
    }

    /**
     * Get CSRF token from the page
     */
    function getCSRFToken() {
        const name = 'csrf_token';
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + '=')) {
                return cookie.substring(name.length + 1);
            }
        }
        // Try to get from meta tag if not in cookies
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        return csrfMeta ? csrfMeta.getAttribute('content') : '';
    }

    /**
     * Show notification to user
     */
    function showNotification(message, type = 'info') {
        if (typeof window.showNotification === 'function') {
            window.showNotification(message, type);
        } else {
            alert(message);
        }
    }

    // --- Event Listeners and Initialization ---

    /**
     * Attaches all necessary event listeners to DOM elements.
     */
    function attachEventListeners() {
        // Event delegation for the main container (change events for select/radio)
        DOM.optionsChartApp.addEventListener('change', (event) => {
            const target = event.target;
            if (Array.from(DOM.priceSourceRadios).includes(target)) {
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
            } else if (target.id === 'buyPeBtn') {
                placeOrder('PE');
            } else if (target.id === 'buyCeBtn') {
                placeOrder('CE');
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

        // Set NIFTY as the only symbol
        currentSymbol = 'NIFTY';

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

        // Initial load of strikes and chart data for NIFTY
        loadStrikes();

        // // Start chart countdown badges (COMMENTED OUT)
        // resetCountdown();

        // Note: updateUnderlyingPrice() is NOT called here to avoid duplicate API call
        // loadStrikes() already caches underlying price data from the merged /api/options-init endpoint
    }

    return {
        init: init
    };
})();

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', OptionsChartApp.init);