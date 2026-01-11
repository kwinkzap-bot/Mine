/**
 * options_chart_app.js
 * Pure Vanilla JavaScript module for the Options Chart Viewer.
 * It uses the Lightweight Charts library.
 */

const OptionsChartApp = (function () {
    // --- Chart State (Now managed by TradingViewChart module) ---
    let ceChart = null;          // TradingViewChart instance
    let peChart = null;          // TradingViewChart instance
    let combinedChart = null;    // TradingViewChart instance (manages 2 series internally)
    let cePairCeChart = null;    // TradingViewChart instance
    let cePairPeChart = null;    // TradingViewChart instance
    let pePairCeChart = null;    // TradingViewChart instance
    let pePairPeChart = null;    // TradingViewChart instance
    
    // Data storage
    let ceData = null;
    let peData = null;
    let cePairData = { ce: null, pe: null };
    let pePairData = { ce: null, pe: null };
    let cePairPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
    let pePairPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
    
    // Tokens and state
    let currentCeToken = null;
    let currentPeToken = null;
    let cePairTokens = { ce: null, pe: null };
    let pePairTokens = { ce: null, pe: null };
    let currentTimeframe = '5minute';
    let autoUpdateInterval = null;
    let currentSymbol = 'NIFTY';
    let currentPriceSource = 'previous_close';
    let isInitialLoad = true;
    let selectedDate = null;  // Track selected date for data fetching
    
    // Previous day high/low data from backend
    let currentPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
    
    // Trend and Entry/Exit tracking
    let currentTrend = 'NEUTRAL';
    let previousTrend = 'NEUTRAL';
    let entrySignal = null;
    let currentDayLow = null;
    let currentDayHigh = null;
    
    // Cache for OPTIONS_INIT response
    let cachedInitResponse = null;
    let cachedInitSymbol = null;
    let cachedInitPriceSource = null;
    let lastFetchedInitResponse = null;  // Store response from loadStrikes() to reuse in loadChartData()
    
    const timeframeIntervals = {
        '1minute': 60,
        '3minute': 180,
        '5minute': 300,
        '15minute': 900,
        '60minute': 3600
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
        DOM.datePicker = document.getElementById('optionsChartDatePicker');
        DOM.priceSourceRadios = document.querySelectorAll('input[name="priceSource"]');
        DOM.niftyPriceDisplay = document.getElementById('nifty-ltp');
        DOM.trendDisplay = document.getElementById('trend-display');
        DOM.entrySignalBox = document.getElementById('entry-signal-box');
        DOM.entrySignalContent = document.getElementById('entry-signal-content');
        DOM.ceStrikeDisplay = document.getElementById('ce-strike-display');
        DOM.peStrikeDisplay = document.getElementById('pe-strike-display');
        DOM.combinedCeStrikeDisplay = document.getElementById('combined-ce-strike-display');
        DOM.combinedCePrice = document.getElementById('combined-ce-price');
        DOM.combinedPeStrikeDisplay = document.getElementById('combined-pe-strike-display');
        DOM.combinedPePrice = document.getElementById('combined-pe-price');
        DOM.cePairCeStrikeDisplay = document.getElementById('ce-pair-ce-strike-display');
        DOM.cePairPeStrikeDisplay = document.getElementById('ce-pair-pe-strike-display');
        DOM.pePairCeStrikeDisplay = document.getElementById('pe-pair-ce-strike-display');
        DOM.pePairPeStrikeDisplay = document.getElementById('pe-pair-pe-strike-display');
        DOM.ceStatus = document.getElementById('ce-status');
        DOM.peStatus = document.getElementById('pe-status');
        DOM.combinedStatus = document.getElementById('combined-status');
        DOM.cePairCeStatus = document.getElementById('ce-pair-ce-status');
        DOM.cePairPeStatus = document.getElementById('ce-pair-pe-status');
        DOM.pePairCeStatus = document.getElementById('pe-pair-ce-status');
        DOM.pePairPeStatus = document.getElementById('pe-pair-pe-status');
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

    /**
     * Converts raw data to the Lightweight Charts format.
     * Backend sends UTC timestamps.
     */
    /**
     * Applies status text and styling to a badge element.
     */
    function updateStatusBadge(el, status) {
        if (!el || !status) return;
        const statusClasses = ['status-na', 'status-sideway', 'status-win', 'status-loss'];
        statusClasses.forEach(cls => el.classList.remove(cls));
        el.classList.add(status.className || 'status-na');
        el.textContent = status.text || '--';
    }

    /**
     * Updates combined chart badge using CE and PE statuses.
     */
    function updateCombinedStatusBadge(ceStatus, peStatus) {
        if (!DOM.combinedStatus) return;
        const priorityClass = (ceStatus.className === 'status-loss' || peStatus.className === 'status-loss')
            ? 'status-loss'
            : (ceStatus.className === 'status-win' || peStatus.className === 'status-win')
                ? 'status-win'
                : (ceStatus.className === 'status-sideway' || peStatus.className === 'status-sideway')
                    ? 'status-sideway'
                    : 'status-na';
        updateStatusBadge(DOM.combinedStatus, {
            text: `CE: ${ceStatus.text} | PE: ${peStatus.text}`,
            className: priorityClass
        });
    }

    /**
     * Calculates and updates the overall trend based on exact logic from strike pair statuses.
     * Also determines entry and exit signals based on trend and price action.
     */
    function calculateAndUpdateTrend() {
        if (!DOM.trendDisplay) return;

        // Get statuses from comparison charts
        const cePrice = DOM.ceStatus?.textContent || '--'; // 26200 CE
        const pePrice = DOM.peStatus?.textContent || '--'; // 26500 PE
        const cePairCe = DOM.cePairCeStatus?.textContent || '--'; // 26200 CE chart
        const cePairPe = DOM.cePairPeStatus?.textContent || '--'; // 26200 PE chart
        const pePairCe = DOM.pePairCeStatus?.textContent || '--'; // 26500 CE chart
        const pePairPe = DOM.pePairPeStatus?.textContent || '--'; // 26500 PE chart

        let trend = 'NEUTRAL';
        let trendClass = 'trend-na';

        // STRONG BUY: All CE charts are WIN
        if (cePrice === 'WIN' && cePairCe === 'WIN' && pePairCe === 'WIN') {
            trend = 'STRONG BUY';
            trendClass = 'trend-strong-buy';
        }
        // BUY: CE charts are mostly WIN (cePairPe is SIDEWAY/LOSS, others are WIN)
        else if ((pePairCe === 'SIDEWAY' || pePairCe === 'LOSS') && cePrice === 'WIN' && cePairCe === 'WIN') {
            trend = 'BUY';
            trendClass = 'trend-buy';
        }
        // SIDEWAY: At least one CE chart is SIDEWAY
        else if (cePrice === 'SIDEWAY' || pePrice === 'SIDEWAY') {
            trend = 'SIDEWAY';
            trendClass = 'trend-sideway';
        }
        // STRONG SELL: All PE charts are WIN
        else if (cePairPe === 'WIN' && pePrice === 'WIN' && pePairPe === 'WIN') {
            trend = 'STRONG SELL';
            trendClass = 'trend-strong-sell';
        }
        // SELL: PE charts are mostly WIN (pePairPe is LOSS/SIDEWAY, others are WIN)
        else if (pePairPe === 'WIN' && pePrice === 'WIN' && (cePairPe === 'LOSS' || cePairPe === 'SIDEWAY')) {
            trend = 'SELL';
            trendClass = 'trend-sell';
        }

        // Update the trend display
        DOM.trendDisplay.textContent = trend;
        const trendClasses = ['trend-na', 'trend-strong-buy', 'trend-buy', 'trend-sideway', 'trend-sell', 'trend-strong-sell'];
        trendClasses.forEach(cls => DOM.trendDisplay.classList.remove(cls));
        DOM.trendDisplay.classList.add(trendClass);

        // Detect trend change and send notifications
        if (trend !== previousTrend && previousTrend !== 'NEUTRAL') {
            const timestamp = new Date().toLocaleTimeString('en-IN');
            const message = `🚀 Trend Changed: ${previousTrend} → ${trend} (${timestamp})`;
            
            // Send browser notification
            sendBrowserNotification('Trend Alert', message);
            
            // Send mobile app message via WhatsApp/API
            sendMobileNotification(message);
            
            console.log('Trend change detected:', { from: previousTrend, to: trend });
        }

        // Store current trend for entry/exit logic
        previousTrend = currentTrend; // Save previous trend before updating
        currentTrend = trend;
        
        // Calculate entry and exit signals
        calculateEntryExitSignals();

        console.log('Trend calculated:', { trend, statuses: { cePairCe, cePairPe, pePairCe, pePairPe }, entrySignal });
    }

    /**
     * Calculates entry and exit signals based on current trend and price levels.
     * Entry Logic:
     * - BUY/STRONG BUY: Entry when CE price closes above PE PDH + 10
     *   Target: CE PDH | SL: Current Day Low
     * - SELL/STRONG SELL: Entry when PE price closes above CE PDH + 10
     *   Target: PE PDH | SL: Current Day Low
     */
    function calculateEntryExitSignals() {
        entrySignal = null;
        
        // Get current prices and levels
        const ceCurrentPrice = ceData && ceData.length > 0 ? ceData[ceData.length - 1].close : null;
        const peCurrentPrice = peData && peData.length > 0 ? peData[peData.length - 1].close : null;
        const ceLow = ceData && ceData.length > 0 ? ceData[ceData.length - 1].low : null;
        const peHigh = peData && peData.length > 0 ? peData[peData.length - 1].high : null;
        const peLow = peData && peData.length > 0 ? peData[peData.length - 1].low : null;
        const ceHigh = ceData && ceData.length > 0 ? ceData[ceData.length - 1].high : null;

        // Update current day high/low
        if (ceData && ceData.length > 0) {
            currentDayLow = ceData.reduce((min, candle) => Math.min(min, candle.low), ceData[0].low);
            currentDayHigh = ceData.reduce((max, candle) => Math.max(max, candle.high), ceData[0].high);
        }

        // BUY or STRONG BUY: Entry on CE when price closes above PE PDH + 10
        if ((currentTrend === 'BUY' || currentTrend === 'STRONG BUY') && ceCurrentPrice && currentPdhPdl.pe_pdh) {
            const entryLevel = currentPdhPdl.pe_pdh + 10;
            const targetLevel = currentPdhPdl.ce_pdh;
            if (ceCurrentPrice > entryLevel && ceLow <= entryLevel) {
                entrySignal = {
                    type: 'BUY',
                    entry: entryLevel,
                    target: targetLevel
                };
            }
        }
        
        // SELL or STRONG SELL: Entry on PE when price closes above CE PDH + 10
        if ((currentTrend === 'SELL' || currentTrend === 'STRONG SELL') && peCurrentPrice && currentPdhPdl.ce_pdh) {
            const entryLevel = currentPdhPdl.ce_pdh + 10;
            const targetLevel = currentPdhPdl.pe_pdh;
            if (peCurrentPrice > entryLevel && peHigh >= entryLevel) {
                entrySignal = {
                    type: 'SELL',
                    entry: entryLevel,
                    target: targetLevel
                };
            }
        }
        
        // Display entry signal
        displayEntrySignal();
    }

    /**
     * Displays the entry signal on the UI with trade details.
     */
    function displayEntrySignal() {
        if (!DOM.entrySignalBox || !DOM.entrySignalContent) return;

        if (!entrySignal) {
            DOM.entrySignalBox.classList.add('hidden');
            return;
        }

        const { type, entry, target } = entrySignal;
        
        // Build HTML for entry signal - single line near trend
        let html = `<span class="entry-value">Entry: ₹${entry.toFixed(2)}</span> | <span class="exit-value">Exit: ₹${target.toFixed(2)}</span>`;

        DOM.entrySignalContent.innerHTML = html;
        DOM.entrySignalBox.classList.remove('hidden');
        DOM.entrySignalBox.classList.remove('buy-signal', 'sell-signal');
        DOM.entrySignalBox.classList.add(type.toLowerCase() === 'buy' ? 'buy-signal' : 'sell-signal');
    }

    /**
     * Requests browser notification permission on user gesture.
     * Must be called from a user interaction (click, load, etc.)
     */
    function requestNotificationPermission() {
        if (!('Notification' in window)) {
            console.log('Browser notifications not supported');
            return;
        }

        if (Notification.permission === 'granted') {
            console.log('Notification permission already granted');
            return;
        }

        if (Notification.permission !== 'denied') {
            // Only request if not already denied - must be from user gesture
            Notification.requestPermission().then(permission => {
                console.log('Notification permission result:', permission);
            });
        }
    }

    /**
     * Send a browser notification when trend changes
     * Note: Permission must be already granted (request it separately on user gesture)
     */
    function sendBrowserNotification(title, message) {
        if (!('Notification' in window)) {
            console.log('Browser notifications not supported');
            return;
        }

        // Only send if permission is already granted
        if (Notification.permission === 'granted') {
            new Notification(title, {
                body: message,
                icon: '/static/images/trend-icon.png',
                tag: 'trend-alert',
                requireInteraction: false
            });
        } else {
            // Permission not granted - log for debugging
            console.log('Notification permission not granted. Current permission:', Notification.permission);
        }
    }

    /**
     * Send trend change notification to mobile via WhatsApp or app API
     */
    async function sendMobileNotification(message) {
        try {
            // Check if WhatsApp service is available (from whatsapp_service.js)
            if (window.WhatsAppService && typeof window.WhatsAppService.sendMessage === 'function') {
                await window.WhatsAppService.sendMessage({
                    title: 'Options Chart - Trend Alert',
                    message: message
                });
                console.log('Mobile notification sent via WhatsApp');
            } else {
                // Fallback: Send via custom API endpoint
                await fetch('/api/send-notification', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        type: 'trend_alert',
                        message: message,
                        timestamp: new Date().toISOString()
                    })
                }).then(res => {
                    if (res.ok) console.log('Mobile notification sent via API');
                }).catch(err => console.error('Error sending mobile notification:', err));
            }
        } catch (error) {
            console.error('Error in sendMobileNotification:', error);
        }
    }

    // --- UI/State Management Functions ---

    /**
     * TEST: Manually trigger trend change notifications for testing
     * Call this in browser console: OptionsChartApp.testNotifications()
     */
    function testNotifications() {
        console.log('🧪 Testing Notification System...');
        
        // Test 1: Browser Notification
        console.log('Test 1: Browser Notification');
        sendBrowserNotification('Test Alert', '🚀 Trend Changed: BUY → SELL (Test)');
        
        // Test 2: Mobile Notification
        console.log('Test 2: Mobile Notification via API');
        sendMobileNotification('🧪 Test Message: Trend Changed from BUY to SELL');
        
        console.log('✅ Notification tests triggered. Check console and your phone.');
    }

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
     * @param {string} targetDate - Optional date in YYYY-MM-DD format
     */
    async function updateUnderlyingPrice(targetDate = null) {
        if (!currentSymbol || !DOM.niftyPriceDisplay) return;

        // If we have cached underlying price data from loadStrikes(), use it
        if (window._cachedUnderlyingPrice && window._cachedUnderlyingPrice.symbol === currentSymbol && !targetDate) {
            const data = window._cachedUnderlyingPrice;
            const sourceLabel = data.source_label || '';
            DOM.niftyPriceDisplay.textContent = (data.requested_price || 0).toFixed(2) + sourceLabel;
            return;
        }

        // Fallback: refetch from merged init endpoint if no cached data
        try {
            const priceSource = currentPriceSource === 'previous_close' ? 'previous_close' : 'ltp';
            let apiUrl = `${CONSTANTS.API_ENDPOINTS.OPTIONS_INIT}?symbol=${currentSymbol}&price_source=${priceSource}`;
            if (targetDate) {
                apiUrl += `&date=${targetDate}`;
            }
            const data = await fetchJson(apiUrl);

            if (data.success && data.underlying_price) {
                const sourceLabel = data.underlying_price.source_label || '';
                DOM.niftyPriceDisplay.textContent = (data.underlying_price.requested_price || 0).toFixed(2) + sourceLabel;
                // Update cache only if no targetDate
                if (!targetDate) {
                    window._cachedUnderlyingPrice = {
                        symbol: currentSymbol,
                        requested_price: data.underlying_price.requested_price,
                        source_label: data.underlying_price.source_label
                    };
                }
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
            if (currentTimeframe === '60minute') {
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
    /**
     * Initializes all charts using the reusable TradingViewChart module.
     * This replaces ~500 lines of duplicate chart setup code with concise module calls.
     */
    function initCharts() {
        if (!window.TradingViewChart) {
            console.error("TradingViewChart module not loaded.");
            showNotification("Chart module not loaded. Check your HTML head.", "error");
            return;
        }

        // Get colors from CONSTANTS
        const ceColor = CONSTANTS.CHART_CONFIG.CE_COLOR || '#00c853';
        const peColor = CONSTANTS.CHART_CONFIG.PE_COLOR || '#2962ff';

        // Initialize CE Chart
        ceChart = TradingViewChart.create({
            containerId: 'ceChart',
            data: [],
            pdh: null,
            pdl: null,
            type: 'CE',
            timeframe: currentTimeframe,
            ceColor: ceColor,
            peColor: peColor
        });

        // Initialize PE Chart
        peChart = TradingViewChart.create({
            containerId: 'peChart',
            data: [],
            pdh: null,
            pdl: null,
            type: 'PE',
            timeframe: currentTimeframe,
            ceColor: ceColor,
            peColor: peColor
        });

        // Initialize Combined Chart (handles both CE and PE series internally)
        combinedChart = TradingViewChart.create({
            containerId: 'combinedChart',
            data: [],
            pdh: null,
            pdl: null,
            type: 'COMBINED',
            timeframe: currentTimeframe,
            isCombined: true,
            ceColor: ceColor,
            peColor: peColor
        });

        // Initialize CE Pair Charts (CE strike with both CE and PE charts)
        cePairCeChart = TradingViewChart.create({
            containerId: 'cePairCeChart',
            data: [],
            pdh: null,
            pdl: null,
            type: 'CE',
            timeframe: currentTimeframe,
            ceColor: ceColor,
            peColor: peColor
        });

        cePairPeChart = TradingViewChart.create({
            containerId: 'cePairPeChart',
            data: [],
            pdh: null,
            pdl: null,
            type: 'PE',
            timeframe: currentTimeframe,
            ceColor: ceColor,
            peColor: peColor
        });

        // Initialize PE Pair Charts (PE strike with both CE and PE charts)
        pePairCeChart = TradingViewChart.create({
            containerId: 'pePairCeChart',
            data: [],
            pdh: null,
            pdl: null,
            type: 'CE',
            timeframe: currentTimeframe,
            ceColor: ceColor,
            peColor: peColor
        });

        pePairPeChart = TradingViewChart.create({
            containerId: 'pePairPeChart',
            data: [],
            pdh: null,
            pdl: null,
            type: 'PE',
            timeframe: currentTimeframe,
            ceColor: ceColor,
            peColor: peColor
        });

        console.log('All charts initialized using TradingViewChart module', { ceColor, peColor });
    }

    /**
     * Renders the individual CE and PE charts using TradingViewChart module.
     */
    function renderIndividualCharts() {
        if (!ceData || !peData || !ceChart || !peChart) {
            const naStatus = { text: '--', className: 'status-na' };
            updateStatusBadge(DOM.ceStatus, naStatus);
            updateStatusBadge(DOM.peStatus, naStatus);
            updateCombinedStatusBadge(naStatus, naStatus);
            return;
        }

        // Use PDH/PDL from backend if available
        let cePdh = currentPdhPdl.ce_pdh;
        let cePdl = currentPdhPdl.ce_pdl;
        let pePdh = currentPdhPdl.pe_pdh;
        let pePdl = currentPdhPdl.pe_pdl;

        // Update CE chart - pass all 4 values (CE PDH, CE PDL, PE PDH, PE PDL)
        ceChart.update(ceData, null, null);
        ceChart.updatePdhPdl(cePdh, cePdl, pePdh, pePdl);
        let ceStatus = ceChart.getStatus();
        updateStatusBadge(DOM.ceStatus, ceStatus);

        // Update PE chart - pass all 4 values (CE PDH, CE PDL, PE PDH, PE PDL)
        peChart.update(peData, null, null);
        peChart.updatePdhPdl(cePdh, cePdl, pePdh, pePdl);
        let peStatus = peChart.getStatus();
        updateStatusBadge(DOM.peStatus, peStatus);

        updateCombinedStatusBadge(ceStatus, peStatus);

        // Update combined chart price displays
        const ceLatestPrice = ceChart.getLatestPrice();
        const peLatestPrice = peChart.getLatestPrice();
        if (DOM.combinedCePrice && ceLatestPrice !== null) {
            DOM.combinedCePrice.textContent = `₹${ceLatestPrice.toFixed(2)}`;
        }
        if (DOM.combinedPePrice && peLatestPrice !== null) {
            DOM.combinedPePrice.textContent = `₹${peLatestPrice.toFixed(2)}`;
        }
    }

    /**
     * Renders the combined CE and PE candlestick chart using TradingViewChart module.
     */
    function renderCombinedChart(cePdh, cePdl, pePdh, pePdl) {
        if (!combinedChart || !ceData || !peData) {
            console.log('renderCombinedChart early exit');
            return;
        }

        // Update cache with provided values
        currentPdhPdl = {
            ce_pdh: cePdh !== undefined ? cePdh : currentPdhPdl.ce_pdh,
            ce_pdl: cePdl !== undefined ? cePdl : currentPdhPdl.ce_pdl,
            pe_pdh: pePdh !== undefined ? pePdh : currentPdhPdl.pe_pdh,
            pe_pdl: pePdl !== undefined ? pePdl : currentPdhPdl.pe_pdl
        };

        // Combined chart: update with CE data and PE data (second parameter for combined chart)
        combinedChart.update(ceData, peData);

        // Render individual charts
        renderIndividualCharts();

        // Render comparison charts
        renderComparisonCharts();

        // Show 2 days of data on initial load
        if (isInitialLoad) {
            isInitialLoad = false;
        }
    }

    /**
     * Renders both comparison charts where CE and PE use the same strike (using module).
     */
    function renderComparisonCharts() {
        const naStatus = { text: '--', className: 'status-na' };

        if (cePairData?.ce && cePairCeChart) {
            renderPairSingleChart(cePairCeChart, cePairData.ce, cePairPdhPdl, true, DOM.cePairCeStatus);
        } else {
            updateStatusBadge(DOM.cePairCeStatus, naStatus);
        }

        if (cePairData?.pe && cePairPeChart) {
            renderPairSingleChart(cePairPeChart, cePairData.pe, cePairPdhPdl, false, DOM.cePairPeStatus);
        } else {
            updateStatusBadge(DOM.cePairPeStatus, naStatus);
        }

        if (pePairData?.ce && pePairCeChart) {
            renderPairSingleChart(pePairCeChart, pePairData.ce, pePairPdhPdl, true, DOM.pePairCeStatus);
        } else {
            updateStatusBadge(DOM.pePairCeStatus, naStatus);
        }

        if (pePairData?.pe && pePairPeChart) {
            renderPairSingleChart(pePairPeChart, pePairData.pe, pePairPdhPdl, false, DOM.pePairPeStatus);
        } else {
            updateStatusBadge(DOM.pePairPeStatus, naStatus);
        }

        // Calculate and update overall trend based on all statuses
        calculateAndUpdateTrend();
    }

    /**
     * Renders a single comparison chart using TradingViewChart module.
     */
    function renderPairSingleChart(chart, data, pdhPdl, isCeChart, statusEl) {
        if (!chart || !data) return;

        // Update chart with data
        chart.update(data, null, null);
        
        // Update with all 4 PDH/PDL values (CE PDH, CE PDL, PE PDH, PE PDL)
        chart.updatePdhPdl(pdhPdl.ce_pdh, pdhPdl.ce_pdl, pdhPdl.pe_pdh, pdhPdl.pe_pdl);
        
        // Get status from chart module
        let status = chart.getStatus();
        updateStatusBadge(statusEl, status);
    }

    /**
     * Sets the visible time range to show only the specified number of days from the end
     */
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
    async function loadStrikes(targetDate = null) {
        DOM.ceStrikeSelect.innerHTML = '<option value="">Loading...</option>';
        DOM.peStrikeSelect.innerHTML = '<option value="">Loading...</option>';

        const symbol = 'NIFTY';  // NIFTY 50 only

        showLoader();
        try {
            // Get the selected price source (LTP or Previous Close)
            const priceSource = document.querySelector('input[name="priceSource"]:checked')?.value || 'previous_close';
            console.log('[loadStrikes] Using price source:', priceSource, 'currentPriceSource:', currentPriceSource);

            // Single merged API call that returns strikes, underlying price, and PDH/PDL
            let apiUrl = `${CONSTANTS.API_ENDPOINTS.OPTIONS_INIT}?symbol=${symbol}&price_source=${priceSource}`;
            if (targetDate) {
                apiUrl += `&date=${targetDate}`;
            }
            const data = await fetchJson(apiUrl);

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
                DOM.cePairCeStrikeDisplay.textContent = DOM.ceStrikeSelect.value ? `CE ${DOM.ceStrikeSelect.value}` : '';
                DOM.cePairPeStrikeDisplay.textContent = DOM.ceStrikeSelect.value ? `PE ${DOM.ceStrikeSelect.value}` : '';
                DOM.pePairCeStrikeDisplay.textContent = DOM.peStrikeSelect.value ? `CE ${DOM.peStrikeSelect.value}` : '';
                DOM.pePairPeStrikeDisplay.textContent = DOM.peStrikeSelect.value ? `PE ${DOM.peStrikeSelect.value}` : '';

                // Update underlying price display directly from the response
                if (data.underlying_price) {
                    const sourceLabel = data.underlying_price.source_label || '';
                    DOM.niftyPriceDisplay.textContent = (data.underlying_price.requested_price || 0).toFixed(2) + sourceLabel;
                    
                    // Cache underlying price data only if no targetDate (to avoid caching historical data)
                    if (!targetDate) {
                        window._cachedUnderlyingPrice = {
                            symbol: symbol,
                            requested_price: data.underlying_price.requested_price,
                            source_label: data.underlying_price.source_label
                        };
                    }
                }

                // Update tokens for auto-update
                if (data.default_ce_token) currentCeToken = data.default_ce_token;
                if (data.default_pe_token) currentPeToken = data.default_pe_token;

                // IMPORTANT: Cache the full OPTIONS_INIT response to avoid duplicate calls in loadChartData()
                // But only cache if no targetDate (historical data shouldn't be cached)
                if (!targetDate) {
                    cachedInitResponse = data;
                    cachedInitSymbol = symbol;
                    cachedInitPriceSource = priceSource;
                }
                
                // Store the fetched response to reuse in loadChartData() to avoid duplicate API calls
                lastFetchedInitResponse = data;

                // Load initial chart data with the targetDate
                loadChartData(targetDate);

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
    async function loadChartData(targetDate = null) {
        const ceStrike = DOM.ceStrikeSelect.value;
        const peStrike = DOM.peStrikeSelect.value;
        let ceStrikeObj = null;
        let peStrikeObj = null;

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
                // IMPORTANT: Use lastFetchedInitResponse if available (set by loadStrikes) to avoid duplicate API calls
                if (lastFetchedInitResponse && lastFetchedInitResponse.success) {
                    console.log('Using lastFetched OPTIONS_INIT response (avoid duplicate API call)');
                    initResp = lastFetchedInitResponse;
                    lastFetchedInitResponse = null;  // Clear it so next time we use cache or fetch
                } else if (cachedInitResponse && cachedInitSymbol === currentSymbol && cachedInitPriceSource === priceSource && !targetDate) {
                    console.log('Using cached OPTIONS_INIT response');
                    initResp = cachedInitResponse;
                } else {
                    console.log('Fetching fresh OPTIONS_INIT response (cache miss or params changed)');
                    let apiUrl = `${CONSTANTS.API_ENDPOINTS.OPTIONS_INIT}?symbol=${currentSymbol}&price_source=${priceSource}`;
                    if (targetDate) {
                        apiUrl += `&date=${targetDate}`;
                    }
                    initResp = await fetchJson(apiUrl);
                    // Update cache only if no targetDate
                    if (initResp?.success && !targetDate) {
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
                    ceStrikeObj = strikes.find(s => {
                        const strikeStr = s.strike.toString();
                        return strikeStr === ceStrikeStr;
                    });
                    peStrikeObj = strikes.find(s => {
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

            // Store tokens for comparison charts (same-strike CE/PE pairs)
            cePairTokens = {
                ce: ceStrikeObj?.ce_token || null,
                pe: ceStrikeObj?.pe_token || null
            };
            pePairTokens = {
                ce: peStrikeObj?.ce_token || null,
                pe: peStrikeObj?.pe_token || null
            };

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

            // Fetch PDH/PDL for CE strike pair tokens
            try {
                if (cePairTokens.ce && cePairTokens.pe) {
                    const pdhRespCePair = await fetchJson(CONSTANTS.API_ENDPOINTS.OPTIONS_PDH_PDL, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ce_token: cePairTokens.ce, pe_token: cePairTokens.pe })
                    });
                    if (pdhRespCePair?.success) {
                        cePairPdhPdl = pdhRespCePair.pdh_pdl || { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
                    } else {
                        cePairPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
                    }
                }
            } catch (error) {
                console.error('Error fetching PDH/PDL for CE pair tokens:', error);
                cePairPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
            }

            // Fetch PDH/PDL for PE strike pair tokens
            try {
                if (pePairTokens.ce && pePairTokens.pe) {
                    const pdhRespPePair = await fetchJson(CONSTANTS.API_ENDPOINTS.OPTIONS_PDH_PDL, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ce_token: pePairTokens.ce, pe_token: pePairTokens.pe })
                    });
                    if (pdhRespPePair?.success) {
                        pePairPdhPdl = pdhRespPePair.pdh_pdl || { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
                    } else {
                        pePairPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
                    }
                }
            } catch (error) {
                console.error('Error fetching PDH/PDL for PE pair tokens:', error);
                pePairPdhPdl = { ce_pdh: null, ce_pdl: null, pe_pdh: null, pe_pdl: null };
            }

            // Step 3: Use tokens for chart data fetch (primary + comparison pairs)
            const chartDataPromises = [
                fetchChartDataFromApi(ceTokenToUse, peTokenToUse),
                cePairTokens.ce && cePairTokens.pe ? fetchChartDataFromApi(cePairTokens.ce, cePairTokens.pe) : Promise.resolve(null),
                pePairTokens.ce && pePairTokens.pe ? fetchChartDataFromApi(pePairTokens.ce, pePairTokens.pe) : Promise.resolve(null)
            ];

            const [result, cePairResult, pePairResult] = await Promise.all(chartDataPromises);

            if (result.success && result.ceData && result.peData) {
                ceData = result.ceData;
                peData = result.peData;

                // Store comparison chart data when available
                if (cePairResult?.success && cePairResult.ceData && cePairResult.peData) {
                    cePairData = { ce: cePairResult.ceData, pe: cePairResult.peData };
                } else {
                    cePairData = { ce: null, pe: null };
                }
                if (pePairResult?.success && pePairResult.ceData && pePairResult.peData) {
                    pePairData = { ce: pePairResult.ceData, pe: pePairResult.peData };
                } else {
                    pePairData = { ce: null, pe: null };
                }

                // Update global tokens for auto-update
                currentCeToken = ceTokenToUse;
                currentPeToken = peTokenToUse;
                currentPdhPdl = pdhToUse;

                console.log('Chart data loaded:', { ceDataLength: ceData.length, peDataLength: peData.length });

                // IMPORTANT: Do NOT reinitialize charts here - they're already initialized in init()
                // Only call renderCombinedChart() to update the data on existing chart objects
                console.log('Calling renderCombinedChart with PDH/PDL:', currentPdhPdl);
                renderCombinedChart(currentPdhPdl.ce_pdh, currentPdhPdl.ce_pdl, currentPdhPdl.pe_pdh, currentPdhPdl.pe_pdl);
                renderComparisonCharts();

                startAutoUpdate(); // Restart auto-update with new data and tokens

                // Request notification permission only after first successful user interaction
                // This is a user gesture (form submission) so browser will allow the request
                if (isInitialLoad) {
                    requestNotificationPermission();
                    isInitialLoad = false;
                }

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
                const updatePromises = [
                    fetchChartDataFromApi(currentCeToken, currentPeToken),
                    cePairTokens.ce && cePairTokens.pe ? fetchChartDataFromApi(cePairTokens.ce, cePairTokens.pe) : Promise.resolve(null),
                    pePairTokens.ce && pePairTokens.pe ? fetchChartDataFromApi(pePairTokens.ce, pePairTokens.pe) : Promise.resolve(null)
                ];

                const [result, cePairResult, pePairResult] = await Promise.all(updatePromises);

                if (result.needsLogin) {
                    clearInterval(autoUpdateInterval);
                    return; // fetchJson handles the redirect/notification
                }

                if (result.success && result.ceData && result.peData) {
                    ceData = result.ceData;
                    peData = result.peData;

                    console.log('Auto-update: New data received', { ceDataLength: ceData.length, peDataLength: peData.length });
                    // Clear cached formatted data to force re-formatting
                    console.log('Rendering updated chart...');
                    renderCombinedChart();
                }

                if (cePairResult?.success && cePairResult.ceData && cePairResult.peData) {
                    cePairData = { ce: cePairResult.ceData, pe: cePairResult.peData };
                }

                if (pePairResult?.success && pePairResult.ceData && pePairResult.peData) {
                    pePairData = { ce: pePairResult.ceData, pe: pePairResult.peData };
                }

                renderComparisonCharts();
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
                DOM.cePairCeStrikeDisplay.textContent = DOM.ceStrikeSelect.value ? `CE ${DOM.ceStrikeSelect.value}` : '';
                DOM.cePairPeStrikeDisplay.textContent = DOM.ceStrikeSelect.value ? `PE ${DOM.ceStrikeSelect.value}` : '';
                DOM.pePairCeStrikeDisplay.textContent = DOM.peStrikeSelect.value ? `CE ${DOM.peStrikeSelect.value}` : '';
                DOM.pePairPeStrikeDisplay.textContent = DOM.peStrikeSelect.value ? `PE ${DOM.peStrikeSelect.value}` : '';
            } else if (target === DOM.datePicker) {
                // Handle date picker change
                selectedDate = target.value ? new Date(target.value) : null;
                console.log('[DatePicker] Selected date:', selectedDate);
                // Reload strikes and update price with selected date
                if (selectedDate) {
                    const dateStr = selectedDate.toISOString().split('T')[0]; // Format as YYYY-MM-DD
                    loadStrikes(dateStr);
                } else {
                    loadStrikes(); // Reload with current date
                }
            }
        });

        // Event delegation for the main container (click events for buttons)
        DOM.optionsChartApp.addEventListener('click', (event) => {
            const target = event.target;
            if (target.id === 'fetchChartBtn') {
                // Pass selected date to loadChartData if available
                if (selectedDate) {
                    const dateStr = selectedDate.toISOString().split('T')[0]; // Format as YYYY-MM-DD
                    loadChartData(dateStr);
                } else {
                    loadChartData();
                }
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
        const cePairCeContainer = document.getElementById('cePairCeChart');
        const cePairPeContainer = document.getElementById('cePairPeChart');
        const pePairCeContainer = document.getElementById('pePairCeChart');
        const pePairPeContainer = document.getElementById('pePairPeChart');

        if (ceContainer && ceChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== ceContainer) { return; }
                ceChart.resize();
            }).observe(ceContainer);
        }

        if (peContainer && peChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== peContainer) { return; }
                peChart.resize();
            }).observe(peContainer);
        }

        if (combinedContainer && combinedChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== combinedContainer) { return; }
                combinedChart.resize();
            }).observe(combinedContainer);
        }

        if (cePairCeContainer && cePairCeChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== cePairCeContainer) { return; }
                cePairCeChart.resize();
            }).observe(cePairCeContainer);
        }

        if (cePairPeContainer && cePairPeChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== cePairPeContainer) { return; }
                cePairPeChart.resize();
            }).observe(cePairPeContainer);
        }

        if (pePairCeContainer && pePairCeChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== pePairCeContainer) { return; }
                pePairCeChart.resize();
            }).observe(pePairCeContainer);
        }

        if (pePairPeContainer && pePairPeChart) {
            new ResizeObserver(entries => {
                if (entries.length === 0 || entries[0].target !== pePairPeContainer) { return; }
                pePairPeChart.resize();
            }).observe(pePairPeContainer);
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
        init: init,
        // Expose notification functions for testing and manual triggering
        requestNotificationPermission: requestNotificationPermission,
        testNotifications: testNotifications,
        sendBrowserNotification: sendBrowserNotification,
        sendMobileNotification: sendMobileNotification
    };
})();

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', OptionsChartApp.init);