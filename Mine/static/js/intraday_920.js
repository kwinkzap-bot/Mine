/**
 * Intraday 9:20 Strategy - First 5 Minute High/Low Trading
 * 
 * This script manages the 9:20 strategy which:
 * 1. Gets the first 5-minute candle high and low
 * 2. Finds CE/PE strikes for both high and low
 * 3. Displays live candlestick charts for all four strikes
 */

class Intraday920Tracker {
    constructor() {
        this.symbol = 'NIFTY';
        this.timeFrame = '5minute';
        this.selectedDate = null; // Track selected date for data fetching
        
        // Data storage
        this.strategyData = null;
        this.charts = {};
        this.candleSeries = {};
        
        // API configuration
        this.apiBaseUrl = '/api';
        this.refreshDelay = 5000; // 5 seconds
        
        // Market hours
        this.marketOpenTime = 9 * 60 + 15;    // 9:15 AM
        this.marketCloseTime = 15 * 60 + 20;  // 3:20 PM
        this.isMarketHours = false;
        
        // Polling configuration
        this.autoUpdateInterval = null;
        this.isAutoUpdating = false;
        this.updateCount = 0;
        this.failedUpdateCount = 0;
        
        // Chart update tracking
        this.lastCandles = {
            highCe: [],
            highPe: [],
            lowCe: [],
            lowPe: []
        };
        
        this.initElements();
        this.initCharts();
        this.attachEventListeners();
        this.startMarketHoursMonitoring();
    }

    initElements() {
        this.symbolSelect = document.getElementById('symbolSelect');
        this.timeFrameSelect = document.getElementById('timeFrameSelect');
        this.signalsContainer = document.getElementById('signalsContainer');
        this.dataContainer = document.getElementById('dataContainer');
    }

    /**
     * Initialize TradingView charts for all four strikes
     * Uses TradingViewChart module (like intraday_option.js)
     */
    initCharts() {
        try {
            // Initialize High CE Chart
            this.charts.highCe = TradingViewChart.create({
                containerId: 'highCeChart',
                data: [],
                type: 'CE',
                timeframe: this.timeFrame,
                options: {
                    height: 300
                }
            });

            // Initialize High PE Chart
            this.charts.highPe = TradingViewChart.create({
                containerId: 'highPeChart',
                data: [],
                type: 'PE',
                timeframe: this.timeFrame,
                options: {
                    height: 300
                }
            });

            // Initialize Low CE Chart
            this.charts.lowCe = TradingViewChart.create({
                containerId: 'lowCeChart',
                data: [],
                type: 'CE',
                timeframe: this.timeFrame,
                options: {
                    height: 300
                }
            });

            // Initialize Low PE Chart
            this.charts.lowPe = TradingViewChart.create({
                containerId: 'lowPeChart',
                data: [],
                type: 'PE',
                timeframe: this.timeFrame,
                options: {
                    height: 300
                }
            });

            // Handle window resize for all charts
            const chartIds = ['highCeChart', 'highPeChart', 'lowCeChart', 'lowPeChart'];
            const chartKeys = ['highCe', 'highPe', 'lowCe', 'lowPe'];

            chartIds.forEach((id, index) => {
                const container = document.getElementById(id);
                const key = chartKeys[index];
                if (container && this.charts[key]) {
                    new ResizeObserver(() => {
                        if (this.charts[key]) {
                            this.charts[key].resize();
                        }
                    }).observe(container);
                }
            });

            console.log('[Init] Charts initialized successfully');
        } catch (e) {
            console.error('[Init] Chart initialization error:', e);
            this.addSignal(`Chart initialization error: ${e.message}`, 'ERROR');
        }
    }

    /**
     * Clear all charts by resetting their data
     * Called when date changes to avoid duplicate data from previous date
     */
    clearCharts() {
        try {
            console.log('[Charts] Clearing all chart data');
            
            // Reset the chart series data
            const chartKeys = ['highCe', 'highPe', 'lowCe', 'lowPe'];
            
            chartKeys.forEach((key) => {
                if (this.charts[key]) {
                    this.charts[key].update([], {}); // Clear data and reference lines
                }
            });
            
            // Reset lastCandles tracking
            this.lastCandles = {
                highCe: [],
                highPe: [],
                lowCe: [],
                lowPe: []
            };
            
            console.log('[Charts] All charts cleared successfully');
        } catch (e) {
            console.error('[Charts] Error clearing charts:', e);
        }
    }

    attachEventListeners() {
        // Reload on symbol change (with full refresh)
        if (this.symbolSelect) {
            this.symbolSelect.addEventListener('change', (e) => {
                this.symbol = e.target.value;
                // Force a full data reload on symbol change
                this.loadData(true);
            });
        }

        // Handle date picker changes
        const datePicker = document.getElementById('dataDatePicker');
        if (datePicker) {
            datePicker.addEventListener('change', (e) => {
                this.selectedDate = e.target.value ? new Date(e.target.value) : null;
                console.log('[DatePicker] Selected date:', this.selectedDate);
                // Force a full data reload with the selected date
                this.loadData(true);
            });
        }

        // Load data on page load (initial only)
        this.loadData();
    }

    /**
     * Check if current time is within market hours (9:15 AM to 3:20 PM IST)
     * Excludes weekends (Saturday and Sunday)
     * Uses Intl.DateTimeFormat for accurate IST timezone conversion
     */
    isCurrentlyMarketHours() {
        const now = new Date();
        
        // Check if today is a weekend (0=Sunday, 6=Saturday)
        const day = now.getDay();
        if (day === 0 || day === 6) {
            console.log(`[MarketHours] Today is ${day === 0 ? 'Sunday' : 'Saturday'} - market closed`);
            return false;
        }
        
        // Convert current time to IST (UTC+5:30)
        // Create a date in IST timezone using toLocaleString
        const istFormatter = new Intl.DateTimeFormat('en-US', {
            timeZone: 'Asia/Kolkata',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        });
        
        const istTimeStr = istFormatter.format(now);
        const [hours, minutes] = istTimeStr.split(':').map(Number);
        const currentMinutes = hours * 60 + minutes;

        // Market hours: 9:15 AM (555 minutes) to 3:20 PM (920 minutes)
        const isWithinHours = currentMinutes >= this.marketOpenTime && currentMinutes <= this.marketCloseTime;
        
        console.log(`[MarketHours] IST time: ${hours}:${String(minutes).padStart(2, '0')}, Within hours: ${isWithinHours}`);
        return isWithinHours;
    }

    /**
     * Start monitoring market hours for auto-start/stop
     */
    startMarketHoursMonitoring() {
        console.log('[MarketHours] Starting market hours monitoring');

        this.marketHoursTimer = setInterval(() => {
            const wasMarketHours = this.isMarketHours;
            this.isMarketHours = this.isCurrentlyMarketHours();

            if (!wasMarketHours && this.isMarketHours) {
                console.log('[MarketHours] Market opened - starting auto-update for live charts');
                // Don't reload data - it was already loaded on page load
                // Just start the auto-update for live chart refreshes
                this.startAutoUpdate();
            } else if (wasMarketHours && !this.isMarketHours) {
                console.log('[MarketHours] Market closed - stopping auto-update');
                this.stopAutoUpdate();
            }
        }, 10000); // Check every 10 seconds
    }

    /**
     * Load initial strategy data (called only once on page load)
     */
    async loadData(forceInitial = false) {
        try {
            // Only fetch from /intraday-920/data if:
            // 1. Initial load (no strategyData yet), OR
            // 2. forceInitial flag is true
            if (!this.strategyData || forceInitial) {
                console.log(`[Load] Fetching 9:20 strategy data for ${this.symbol} (initial: ${!this.strategyData})`);
                
                // Clear charts when loading new data (e.g., different date selected)
                this.clearCharts();
                
                // Build URL with optional date parameter
                let url = `${this.apiBaseUrl}/intraday-920/data?symbol=${this.symbol}`;
                if (this.selectedDate) {
                    const dateStr = this.selectedDate.toISOString().split('T')[0]; // Format as YYYY-MM-DD
                    url += `&date=${dateStr}`;
                    console.log(`[Load] Using selected date: ${dateStr}`);
                }
                
                const response = await fetch(url, {
                    credentials: 'include'
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error || `HTTP ${response.status}`);
                }

                const result = await response.json();

                if (!result.success) {
                    throw new Error(result.error || 'Failed to load data');
                }

                this.strategyData = result.data;
                this.updateUI();
                
                // Load chart data for all strikes
                await this.loadChartsData();

                console.log('[Load] Data loaded successfully');
            } else {
                // On subsequent updates, only refresh chart data
                console.log(`[Load] Refreshing chart data for ${this.symbol} (using cached strategy data)`);
                await this.loadChartsData();
            }
        } catch (e) {
            console.error('[Load] Error:', e);
            this.addSignal(`Error loading data: ${e.message}`, 'ERROR');
        }
    }

    /**
     * Update UI with loaded strategy data
     */
    updateUI() {
        if (!this.strategyData) return;

        // Update last update time
        const now = new Date();
        document.getElementById('lastUpdate').textContent = 
            now.toLocaleTimeString('en-IN');

        // Update first 5-minute high/low
        document.getElementById('first5MinHigh').textContent = 
            this.formatPrice(this.strategyData.first_5min_high);
        document.getElementById('first5MinLow').textContent = 
            this.formatPrice(this.strategyData.first_5min_low);

        // Update high strike data
        const highStrike = this.strategyData.high_strike || {};
        if (highStrike.success) {
            document.getElementById('highStrikeLabel').textContent = 
                this.formatPrice(highStrike.strike_price);
            
            document.getElementById('highCeStrike').textContent = highStrike.ce_strike;
            document.getElementById('highCeHigh').textContent = this.formatPrice(highStrike.ce_high);
            document.getElementById('highCeLow').textContent = this.formatPrice(highStrike.ce_low);
            document.getElementById('highCeStrikeChart').textContent = highStrike.ce_strike;
            
            document.getElementById('highPeStrike').textContent = highStrike.pe_strike;
            document.getElementById('highPeHigh').textContent = this.formatPrice(highStrike.pe_high);
            document.getElementById('highPeLow').textContent = this.formatPrice(highStrike.pe_low);
            document.getElementById('highPeStrikeChart').textContent = highStrike.pe_strike;
        }

        // Update low strike data
        const lowStrike = this.strategyData.low_strike || {};
        if (lowStrike.success) {
            document.getElementById('lowStrikeLabel').textContent = 
                this.formatPrice(lowStrike.strike_price);
            
            document.getElementById('lowCeStrike').textContent = lowStrike.ce_strike;
            document.getElementById('lowCeHigh').textContent = this.formatPrice(lowStrike.ce_high);
            document.getElementById('lowCeLow').textContent = this.formatPrice(lowStrike.ce_low);
            document.getElementById('lowCeStrikeChart').textContent = lowStrike.ce_strike;
            
            document.getElementById('lowPeStrike').textContent = lowStrike.pe_strike;
            document.getElementById('lowPeHigh').textContent = this.formatPrice(lowStrike.pe_high);
            document.getElementById('lowPeLow').textContent = this.formatPrice(lowStrike.pe_low);
            document.getElementById('lowPeStrikeChart').textContent = lowStrike.pe_strike;
        }

        this.addSignal('✅ Strategy data loaded', 'SUCCESS');
    }

    /**
     * Fetch chart data from /api/options-chart-data endpoint
     * Similar pattern to intraday_option.js
     * Uses token-based approach for fast data fetching
     * Single call with both CE and PE tokens
     */
    async fetchChartData(ceToken, peToken, label) {
        try {
            console.log(`[fetchChartData] 🚀 Loading data for ${label} (CE: ${ceToken}, PE: ${peToken})`);
            
            // Build request payload with both CE and PE tokens
            const payload = {
                ce_token: ceToken,
                pe_token: peToken,
                timeframe: this.timeFrame,
                live: true
            };
            
            console.log(`[fetchChartData] 📤 Sending payload:`, payload);
            this.addSignal(`📡 Fetching chart data for ${label}...`, 'INFO');
            
            // Call the API endpoint using POST
            const response = await fetch('/api/options-chart-data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(payload)
            });
            
            console.log(`[fetchChartData] ✓ Response received, status: ${response.status}`);
            
            // CRITICAL: Handle 403 Forbidden - token expired or socket pool issue
            if (response.status === 403) {
                console.error(`[fetchChartData] ❌ 403 FORBIDDEN - Token likely expired or socket pool issue`);
                this.addSignal('❌ Access Denied (403) - Your session has expired. Redirecting to login...', 'ERROR');
                
                // Stop auto-updates immediately
                this.stopAutoUpdate();
                
                // Redirect to login after brief delay
                setTimeout(() => {
                    window.location.href = '/auth/login';
                }, 2000);
                
                return { success: false, message: '403 Forbidden - Session expired', ceData: [], peData: [] };
            }
            
            if (!response.ok) {
                let errorMsg = 'Unknown error';
                try {
                    const error = await response.json();
                    errorMsg = error.error || error.message || `HTTP ${response.status}`;
                } catch (e) {
                    errorMsg = `HTTP ${response.status}`;
                }
                console.error(`[fetchChartData] ❌ API Error: ${errorMsg}`);
                return { success: false, message: errorMsg, ceData: [], peData: [] };
            }
            
            // Parse response
            const data = await response.json();
            
            if (data.needs_login) {
                console.error('[fetchChartData] ❌ Login required');
                this.addSignal('❌ Login required - Redirecting...', 'ERROR');
                this.stopAutoUpdate();
                setTimeout(() => {
                    window.location.href = '/auth/login';
                }, 2000);
                return { success: false, message: 'Login required', ceData: [], peData: [] };
            }
            
            if (data.success && data.data && Array.isArray(data.data)) {
                // Separate CE and PE data from merged array (like intraday_option.js does)
                const ceData = data.data.filter(candle => candle.type === 'CE');
                const peData = data.data.filter(candle => candle.type === 'PE');
                
                console.log(`[fetchChartData] ✅ SUCCESS! Got ${ceData.length} CE candles and ${peData.length} PE candles`);
                this.addSignal(`✅ Chart data loaded for ${label} (CE: ${ceData.length}, PE: ${peData.length})`, 'SUCCESS');
                
                return { 
                    success: true, 
                    ceData: ceData,
                    peData: peData,
                    message: ''
                };
            } else {
                const errorMsg = data.message || data.error || 'Failed to load chart data';
                console.error(`[fetchChartData] ❌ ${errorMsg}`);
                return { success: false, message: errorMsg, ceData: [], peData: [] };
            }
            
        } catch (error) {
            console.error('[fetchChartData] 💥 Exception:', error);
            this.addSignal(`Network error: ${error.message}`, 'ERROR');
            return { success: false, message: error.message, ceData: [], peData: [] };
        }
    }

    /**
     * Load candlestick data for all four strikes
     * Makes 2 API calls instead of 4:
     * 1. High Strike: both CE and PE tokens
     * 2. Low Strike: both CE and PE tokens
     */
    async loadChartsData() {
        if (!this.strategyData) {
            console.warn('[loadChartsData] No strategy data available');
            return;
        }

        const highStrike = this.strategyData.high_strike || {};
        const lowStrike = this.strategyData.low_strike || {};
        
        // Check if this is a refresh (date picker was used)
        const isRefresh = this.selectedDate !== null;

        console.log('[loadChartsData] High Strike:', highStrike);
        console.log('[loadChartsData] Low Strike:', lowStrike);
        console.log('[loadChartsData] Refresh mode:', isRefresh);

        // Call 1: Load data for High CE and PE together
        if (highStrike.ce_token && highStrike.pe_token && highStrike.success) {
            await this.updateChartsData(
                highStrike.ce_token,
                highStrike.pe_token,
                'highCe',
                'highPe',
                highStrike.ce_strike,      // CE strike
                highStrike.pe_strike,      // PE strike
                'High Strike',
                isRefresh
            );
        } else {
            console.warn('[loadChartsData] High strike data incomplete:', { 
                ce_token: !!highStrike.ce_token, 
                pe_token: !!highStrike.pe_token, 
                success: highStrike.success 
            });
        }

        // Call 2: Load data for Low CE and PE together
        if (lowStrike.ce_token && lowStrike.pe_token && lowStrike.success) {
            await this.updateChartsData(
                lowStrike.ce_token,
                lowStrike.pe_token,
                'lowCe',
                'lowPe',
                lowStrike.ce_strike,       // CE strike
                lowStrike.pe_strike,       // PE strike
                'Low Strike',
                isRefresh
            );
        } else {
            console.warn('[loadChartsData] Low strike data incomplete:', { 
                ce_token: !!lowStrike.ce_token, 
                pe_token: !!lowStrike.pe_token, 
                success: lowStrike.success 
            });
        }
    }

    /**
     * Update two charts (CE and PE) with data from single API call
     * Passes current high/low as reference lines like intraday_option.js
     */
    async updateChartsData(ceToken, peToken, ceKey, peKey, ceStrike, peStrike, label, refresh = false) {
        try {
            console.log(`[updateChartsData] Starting update for ${label} (refresh: ${refresh})`);
            console.log(`[updateChartsData] Tokens - CE: ${ceToken}, PE: ${peToken}`);
            
            // Single API call with both CE and PE tokens
            const result = await this.fetchChartData(ceToken, peToken, label);

            if (!result.success) {
                console.warn(`[updateChartsData] Failed to load ${label}: ${result.message}`);
                this.addSignal(`❌ Failed to load ${label}: ${result.message}`, 'ERROR');
                return;
            }

            console.log(`[updateChartsData] API returned - CE: ${result.ceData.length} candles, PE: ${result.peData.length} candles`);

            // Get current high/low from strategy data for reference lines
            const strikeType = label.toLowerCase().replace(' strike', '');
            const strikeData = this.strategyData?.[strikeType === 'high' ? 'high_strike' : 'low_strike'];
            
            if (!strikeData) {
                console.warn(`[updateChartsData] No strike data found for ${label}`);
                return;
            }

            const ceHigh = strikeData?.ce_high || 0;
            const ceLow = strikeData?.ce_low || 0;
            const peHigh = strikeData?.pe_high || 0;
            const peLow = strikeData?.pe_low || 0;
            
            console.log(`[updateChartsData] Reference levels for ${label}:`);
            console.log(`  CE High: ${ceHigh}, CE Low: ${ceLow}`);
            console.log(`  PE High: ${peHigh}, PE Low: ${peLow}`);
            
            // CE chart shows PE's high/low as reference (green/red lines)
            const ceReferenceLines = {
                pe_payload_high: peHigh,
                pe_payload_low: peLow
            };
            
            // PE chart shows CE's high/low as reference (green/red lines)
            const peReferenceLines = {
                ce_payload_high: ceHigh,
                ce_payload_low: ceLow
            };

            // Determine if this is an initial load or a polling update
            // Initial load when refresh flag is false (first load)
            // Polling update when refresh flag is true
            const isInitialLoad = !refresh;

            // Update CE chart with reference lines
            if (result.ceData.length > 0) {
                console.log(`[updateChartsData] Updating ${ceKey} with ${result.ceData.length} candles`);
                await this.setChartData(result.ceData, ceKey, `${label} CE`, ceReferenceLines, refresh, isInitialLoad);
            } else {
                console.warn(`[updateChartsData] No CE data for ${label}`);
                this.addSignal(`⚠️ No CE chart data available for ${label}`, 'WARNING');
            }

            // Update PE chart with reference lines
            if (result.peData.length > 0) {
                console.log(`[updateChartsData] Updating ${peKey} with ${result.peData.length} candles`);
                await this.setChartData(result.peData, peKey, `${label} PE`, peReferenceLines, refresh, isInitialLoad);
            } else {
                console.warn(`[updateChartsData] No PE data for ${label}`);
                this.addSignal(`⚠️ No PE chart data available for ${label}`, 'WARNING');
            }

        } catch (e) {
            console.error(`[updateChartsData] 💥 Exception updating ${label}:`, e);
            console.error('[updateChartsData] Stack:', e.stack);
            this.addSignal(`Error updating ${label}: ${e.message}`, 'ERROR');
        }
    }

    /**
     * Format chart data for TradingViewChart
     * Converts backend candle data to proper format with unix timestamps
     * NOTE: TradingViewChart.update() also calls formatChartData internally,
     * so we just pass the raw data directly
     */
    formatChartData(candles) {
        // Pass through - TradingViewChart handles formatting internally
        if (!Array.isArray(candles)) return [];
        return candles;
    }

    /**
     * Set chart data for a specific chart key with optional reference lines
     * Uses TradingViewChart.update() method
     * Reference lines show high/low levels like intraday_option.js
     * 
     * TradingViewChart.update() handles:
     * - Timestamp conversion (seconds, milliseconds, ISO strings)
     * - Data validation and error handling
     * - Chart rendering with proper formatting
     * 
     * IMPORTANT: When updating from /api/options-chart-data:
     * - ONLY candlestick data is updated during polling
     * - Reference lines are drawn on initial load, NOT updated during subsequent updates
     * - This ensures clean candlestick-only updates during polling
     * 
     * @param {Array} candles - Candlestick data
     * @param {string} key - Chart key (e.g., 'highCe', 'lowPe')
     * @param {string} label - Display label
     * @param {Object} referenceLines - Reference line data (high/low values)
     * @param {boolean} refresh - Whether to refresh the chart
     * @param {boolean} isInitialLoad - If true, draw reference lines; if false, only update candles
     */
    setChartData(candles, key, label, referenceLines = null, refresh = false, isInitialLoad = true) {
        try {
            // Get raw candles - TradingViewChart.update() will format them internally
            if (!Array.isArray(candles) || candles.length === 0) {
                console.warn(`[setChartData] No candles provided for ${label}`);
                this.addSignal(`No chart data available for ${label}`, 'WARNING');
                return;
            }

            // Validate chart exists
            if (!this.charts[key]) {
                console.error(`[setChartData] Chart ${key} not found!`);
                this.addSignal(`Chart ${key} not initialized - ${label}`, 'ERROR');
                return;
            }

            if (typeof this.charts[key].update !== 'function') {
                console.error(`[setChartData] Chart ${key} doesn't have update() method!`);
                this.addSignal(`Chart ${key} update method missing - ${label}`, 'ERROR');
                return;
            }

            console.log(`[setChartData] Updating ${label} with ${candles.length} candles (refresh: ${refresh}, isInitialLoad: ${isInitialLoad})`);
            console.log(`[setChartData] Sample candle:`, candles[0]);
            
            // On initial load: update both candlesticks AND reference lines
            // On polling updates: only update candlesticks (pass null to skip reference lines)
            const linesToUse = isInitialLoad ? referenceLines : null;
            
            if (!isInitialLoad) {
                console.log(`[setChartData] Polling update: Updating ONLY candlestick data (NO reference lines)`);
            } else {
                console.log(`[setChartData] Initial load: Updating candlesticks AND reference lines`);
            }
            
            this.charts[key].update(candles, linesToUse, refresh);
            
            // Store for tracking
            this.lastCandles[key] = candles;

            console.log(`[setChartData] ✅ Successfully updated ${label}: ${candles.length} candles`);
            this.addSignal(`✅ Chart updated - ${label} (${candles.length} candles)`, 'SUCCESS');

        } catch (e) {
            console.error(`[setChartData] 💥 Error updating ${label}:`, e);
            console.error('[setChartData] Stack:', e.stack);
            this.addSignal(`Error updating ${label}: ${e.message}`, 'ERROR');
        }
    }

    /**
     * Start auto-update polling
     * Only refreshes chart data, NOT the full strategy data
     */
    startAutoUpdate() {
        if (this.isAutoUpdating) return;

        console.log('[AutoUpdate] Starting polling - will refresh chart data only (NOT full strategy data)');
        this.isAutoUpdating = true;
        this.updateCount = 0;
        this.failedUpdateCount = 0;

        // Don't call loadChartsData immediately - avoid duplicate API calls on page load
        // The initial strategy data and chart data were already loaded by loadData()
        // Only start the periodic polling interval
        
        console.log('[AutoUpdate] Using cached strategy data, only refreshing charts periodically');

        // Set up periodic polling - only refresh chart data
        this.autoUpdateInterval = setInterval(() => {
            if (!this.isCurrentlyMarketHours()) {
                console.log('[AutoUpdate] Market hours ended, stopping');
                this.stopAutoUpdate();
                return;
            }

            // Only refresh chart data, NOT full strategy data
            if (this.strategyData) {
                this.loadChartsData();
            }
            this.updateCount++;
        }, this.refreshDelay);
    }

    /**
     * Stop auto-update polling
     */
    stopAutoUpdate() {
        if (!this.isAutoUpdating) return;

        if (this.autoUpdateInterval) {
            clearInterval(this.autoUpdateInterval);
            this.autoUpdateInterval = null;
        }

        this.isAutoUpdating = false;
        console.log(`[AutoUpdate] Stopped (${this.updateCount} updates)`);
    }

    /**
     * Add a signal message to the UI
     */
    addSignal(message, type = 'INFO') {
        if (!this.signalsContainer) return;

        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-IN');

        const signal = document.createElement('div');
        signal.className = 'signal-entry';
        signal.innerHTML = `
            <span class="time">${timeStr}</span>
            <span class="message">${message}</span>
            <span class="type ${type}">${type}</span>
        `;

        this.signalsContainer.appendChild(signal);

        // Keep only last 20 signals
        const signals = this.signalsContainer.querySelectorAll('.signal-entry');
        if (signals.length > 20) {
            signals[0].remove();
        }
    }

    /**
     * Format price value
     */
    formatPrice(value) {
        if (value === null || value === undefined || value === 0) return '--';
        return parseFloat(value).toLocaleString('en-IN', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }
}

// Initialize tracker when page loads
let intraday920Tracker;
document.addEventListener('DOMContentLoaded', () => {
    console.log('[Init] Initializing Intraday 9:20 Tracker');
    intraday920Tracker = new Intraday920Tracker();
});
