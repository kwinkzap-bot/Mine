/**
 * Intraday Option Trading Page
 * Real-time monitoring of intraday options with PDH/PDL crossing strategy
 * Uses TradingView Lightweight Charts for candlestick visualization
 */

class IntradayOptionTracker {
    constructor() {
        this.ceChart = null;
        this.peChart = null;
        this.ceCandleSeries = null;
        this.peCandleSeries = null;
        this.ceData = [];
        this.peData = [];
        this.timeIndex = 1;
        this.maxDataPoints = 50;

        // Store CE and PE strikes - REQUIRED for all API calls
        this.currentCeStrike = null;
        this.currentPeStrike = null;
        
        // Store CE and PE tokens - USED FOR DATA FETCHING (prevents re-calculation)
        this.currentCeToken = null;
        this.currentPeToken = null;
        
        // Track if strikes were manually selected (don't reset on symbol-payload updates)
        this.manuallySelectedStrikes = false;

        // Store CE/PE levels from symbol payload (for drawing reference lines)
        this.symbolPayloadCELevel = { high: 0, low: 0 };
        this.symbolPayloadPELevel = { high: 0, low: 0 };

        // Auto-update polling configuration (like options_chart_app.js)
        this.autoUpdateInterval = null;
        this.autoUpdateDelay = 5000; // 5 seconds (configurable)
        this.isAutoUpdating = false;
        this.lastUpdateTime = null;
        this.updateCount = 0;
        this.failedUpdateCount = 0;

        // Market hours configuration (9:15 AM to 3:20 PM IST)
        this.marketOpenTime = 9 * 60 + 15;    // 9:15 AM in minutes
        this.marketCloseTime = 15 * 60 + 20;  // 3:20 PM in minutes

        this.initElements();
        this.initCharts();
        this.attachEventListeners();
    }

    initElements() {
        this.symbolSelect = document.getElementById('symbolSelect');
        this.timeFrameSelect = document.getElementById('timeFrameSelect');
        this.refreshInterval = document.getElementById('refreshInterval');
        this.dataContainer = document.getElementById('dataContainer');
        this.signalsContainer = document.getElementById('signalsContainer');
        this.daysBackInput = document.getElementById('daysBackInput') || null;  // Optional historical data days

        // Show data container by default
        if (this.dataContainer) {
            this.dataContainer.classList.remove('hidden');
            // Show loading indicator
            this.showLoadingState();
        }

        // Add debug button if it exists
        this.debugBtn = document.getElementById('debugBtn');
        if (this.debugBtn) {
            this.debugBtn.addEventListener('click', () => this.showDebugPanel());
        }
    }

    /**
     * Check if current time is within market hours (9:15 AM to 3:20 PM IST)
     * In dev mode, this always returns true for testing outside market hours
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

    showLoadingState() {
        // Display loading message in data container
        if (this.signalsContainer) {
            this.signalsContainer.innerHTML = `
                <div class="signal-entry" style="justify-content: center; padding: 20px;">
                    <span class="message" style="flex: none;">🔄 Initializing...</span>
                </div>
            `;
        }
    }

    initCharts() {
        // Initialize CE Chart using TradingViewChart module
        this.ceChart = TradingViewChart.create({
            containerId: 'ceChart',
            data: [],
            type: 'CE',
            timeframe: this.timeFrameSelect.value || '5minute',
            options: {
                height: 300
            }
        });

        // Initialize PE Chart using TradingViewChart module
        this.peChart = TradingViewChart.create({
            containerId: 'peChart',
            data: [],
            type: 'PE',
            timeframe: this.timeFrameSelect.value || '5minute',
            options: {
                height: 300
            }
        });

        // Handle window resize
        const ceChartContainer = document.getElementById('ceChart');
        const peChartContainer = document.getElementById('peChart');

        if (ceChartContainer) {
            new ResizeObserver(() => {
                if (this.ceChart) {
                    this.ceChart.resize();
                }
            }).observe(ceChartContainer);
        }

        if (peChartContainer) {
            new ResizeObserver(() => {
                if (this.peChart) {
                    this.peChart.resize();
                }
            }).observe(peChartContainer);
        }

        console.log('[Charts] Initialized CE and PE charts using TradingViewChart module');
    }

    attachEventListeners() {
        // Fetch symbol payload when symbol is selected
        this.symbolSelect.addEventListener('change', () => {
            // Continue auto-update, just fetch new symbol data
            this.fetchSymbolPayloadAndOption();
        });

        // Change timeframe handler
        this.timeFrameSelect.addEventListener('change', () => {
            // Fetch data with new timeframe, auto-update continues
            this.fetch();
        });

        // Fetch on page load
        this.fetchSymbolPayloadAndOption();
    }

    /**
     * Fetch chart data from /api/options-chart-data endpoint
     * Similar pattern to options_chart_app.js
     * 
     * Prefers token-based (fast) path, falls back to strike-based (legacy) path
     */
    async fetchChartData(symbol, ceStrike, peStrike, timeFrame, ceToken, peToken, daysBack = null) {
        try {
            // Build request payload - prefer tokens for fast path
            const payload = {
                timeframe: timeFrame,
                live: true  // Request live data
            };
            
            let pathDescription = '';
            
            // Use tokens if available (FAST PATH - <2 seconds)
            if (ceToken && peToken) {
                payload.ce_token = ceToken;
                payload.pe_token = peToken;
                pathDescription = `TOKENS (CE: ${ceToken}, PE: ${peToken})`;
                console.log(`[fetchChartData] 🚀 Using FAST PATH with tokens`);
            } else {
                // Fall back to strikes (LEGACY PATH - 3-5 seconds)
                payload.symbol = symbol;
                payload.ce_strike = ceStrike;
                payload.pe_strike = peStrike;
                pathDescription = `STRIKES (Symbol: ${symbol}, CE: ${ceStrike}, PE: ${peStrike})`;
                console.log(`[fetchChartData] 🚀 Using LEGACY PATH with strikes`);
            }
            
            // Add optional historical data days
            if (daysBack && daysBack > 0) {
                payload.days_back = daysBack;
            }
            
            console.log(`[fetchChartData] 📤 Sending payload:`, payload);
            this.addSignal(`📡 Calling /api/options-chart-data (${pathDescription})`, 'INFO');
            
            // Call the API endpoint
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
            
            if (data.success && data.data) {
                // Separate CE and PE data from merged array (like options_chart_app.js does)
                const ceData = data.data.filter(candle => candle.type === 'CE');
                const peData = data.data.filter(candle => candle.type === 'PE');
                
                console.log(`[fetchChartData] ✅ SUCCESS! CE: ${ceData.length} candles, PE: ${peData.length} candles`);
                this.addSignal(`✅ Chart data loaded - CE: ${ceData.length}, PE: ${peData.length} candles`, 'SUCCESS');
                
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

    async fetchSymbolPayloadAndOption() {
        // Step 1: Fetch symbol payload to get CE and PE strikes
        // Step 2: Then fetch option data using those strikes
        try {
            const symbol = this.symbolSelect.value;

            // Step 1: Fetch symbol payload
            let payloadResponse;
            try {
                console.log(`[API CALL 1] 🚀 Calling /api/intraday-option/symbol-payload?symbol=${symbol}`);
                this.addSignal(`📡 API: /api/intraday-option/symbol-payload`, 'INFO');
                payloadResponse = await fetch(`/api/intraday-option/symbol-payload?symbol=${symbol}`, {
                    credentials: 'include'
                });
                console.log('[API CALL 1] ✓ Response received, status:', payloadResponse.status);
            } catch (fetchError) {
                this.addSignal(`Network error: ${fetchError.message}`, 'ERROR');
                return;
            }

            if (!payloadResponse.ok) {
                let errorMsg = 'Unknown error';
                try {
                    const error = await payloadResponse.json();
                    errorMsg = error.error || error.message || errorMsg;
                } catch (e) {
                    errorMsg = `HTTP ${payloadResponse.status}`;
                }
                this.addSignal(`Failed to fetch symbol data: ${errorMsg}`, 'ERROR');
                return;
            }

            let payloadResult;
            try {
                payloadResult = await payloadResponse.json();
            } catch (parseError) {
                this.addSignal(`Invalid response from server: ${parseError.message}`, 'ERROR');
                console.error('Parse error:', parseError, 'Response:', payloadResponse);
                return;
            }

            console.log('Symbol Payload Response:', payloadResult);

            if (!payloadResult.success) {
                this.addSignal(`Error: ${payloadResult.error || 'Failed to fetch symbol data'}`, 'ERROR');
                return;
            }

            // Update display with symbol payload
            const data = payloadResult.data;
            console.log('Symbol Payload Data:', data);

            if (!data) {
                this.addSignal('No data received in symbol payload response', 'ERROR');
                return;
            }

            // Ensure data container is visible
            if (this.dataContainer) {
                console.log('Making dataContainer visible');
                this.dataContainer.classList.remove('hidden');
            } else {
                console.error('dataContainer element not found!');
            }

            // Display current price from symbol payload
            const currentPrice = data.current_price || 0;
            const pdh = data.pdh || 0;
            const pdl = data.pdl || 0;
            const dayHigh = data.day_high || 0;
            const dayLow = data.day_low || 0;
            const ceHigh = data.ce_high || 0;
            const ceLow = data.ce_low || 0;
            const peHigh = data.pe_high || 0;
            const peLow = data.pe_low || 0;

            // Capture tokens from symbol-payload response for fast API calls
            if (data.ce_token) this.currentCeToken = data.ce_token;
            if (data.pe_token) this.currentPeToken = data.pe_token;
            console.log(`[SymbolPayload] Tokens captured - CE: ${data.ce_token || 'N/A'}, PE: ${data.pe_token || 'N/A'}`);

            console.log('Updating elements:', {
                currentPrice: document.getElementById('currentPrice') ? 'FOUND' : 'MISSING',
                pdhValue: document.getElementById('pdhValue') ? 'FOUND' : 'MISSING',
                pdlValue: document.getElementById('pdlValue') ? 'FOUND' : 'MISSING',
                dayHigh: document.getElementById('dayHigh') ? 'FOUND' : 'MISSING',
                dayLow: document.getElementById('dayLow') ? 'FOUND' : 'MISSING',
                ceStrike: document.getElementById('ceStrike') ? 'FOUND' : 'MISSING',
                peStrike: document.getElementById('peStrike') ? 'FOUND' : 'MISSING'
            });

            document.getElementById('currentPrice').textContent = currentPrice.toFixed(2);
            document.getElementById('pdhValue').textContent = pdh.toFixed(2);
            document.getElementById('pdlValue').textContent = pdl.toFixed(2);
            document.getElementById('dayHigh').textContent = dayHigh.toFixed(2);
            document.getElementById('dayLow').textContent = dayLow.toFixed(2);
            document.getElementById('ceStrike').textContent =
                data.ce_strike ? `${data.ce_strike}` : '--';
            document.getElementById('peStrike').textContent =
                data.pe_strike ? `${data.pe_strike}` : '--';
            

            // Update display values with real data
            document.getElementById('ceStrikeChart').textContent =
                data.ce_strike ? `${data.ce_strike}` : '--';
            document.getElementById('peStrikeChart').textContent =
                data.pe_strike ? `${data.pe_strike}` : '--';
            
            // Store CE/PE levels for chart reference lines
            this.symbolPayloadCELevel = { high: ceHigh, low: ceLow };
            this.symbolPayloadPELevel = { high: peHigh, low: peLow };
            
            console.log('[SymbolPayload] CE Level:', this.symbolPayloadCELevel);
            console.log('[SymbolPayload] PE Level:', this.symbolPayloadPELevel);

            console.log('UI Updated - Current Price:', currentPrice, 'PDH:', pdh, 'PDL:', pdl);
            console.log('HTML Elements Updated Successfully');

            // Add signal showing symbol loaded
            this.addSignal(`✓ ${data.symbol || 'Symbol'} loaded - Price: ${currentPrice.toFixed(2)}, PDH: ${pdh.toFixed(2)}, PDL: ${pdl.toFixed(2)}`, 'SUCCESS');

            // Step 2: Now fetch option data using the CE and PE strikes obtained from symbol payload
            if (data.ce_strike && data.pe_strike) {
                const ceStrike = data.ce_strike;
                const peStrike = data.pe_strike;
                const timeFrame = this.timeFrameSelect.value;

                // Store the strikes for all subsequent API calls (only if not manually selected)
                if (!this.manuallySelectedStrikes) {
                    this.currentCeStrike = ceStrike;
                    this.currentPeStrike = peStrike;
                } else {
                    console.log(`[fetchSymbolPayloadAndOption] Keeping manually selected strikes: CE=${this.currentCeStrike}, PE=${this.currentPeStrike}`);
                }

                // Reset auto-update statistics
                this.updateCount = 0;
                this.failedUpdateCount = 0;

                this.addSignal(`Fetching option data for CE ${ceStrike} / PE ${peStrike}...`, 'INFO');

                // Get historical data days if available
                let daysBack = null;
                if (this.daysBackInput && this.daysBackInput.value) {
                    daysBack = parseInt(this.daysBackInput.value);
                    if (daysBack > 0) {
                        this.addSignal(`Fetching ${daysBack} days of historical data...`, 'INFO');
                    }
                }

                // Use the new helper function to fetch chart data
                const chartData = await this.fetchChartData(
                    symbol,
                    ceStrike,
                    peStrike,
                    timeFrame,
                    this.currentCeToken,
                    this.currentPeToken,
                    daysBack
                );

                if (!chartData.success) {
                    this.addSignal(`❌ Error: ${chartData.message}`, 'ERROR');
                    
                    // If strikes not found, show available strikes
                    if (chartData.message.includes('not found')) {
                        this.addSignal(`Analyzing available strikes...`, 'INFO');
                        await this.debugAvailableStrikes(symbol);
                    }
                    return;
                }

                // Transform and update charts with the fetched data
                const transformedData = {
                    ce_data: { candles: chartData.ceData },
                    pe_data: { candles: chartData.peData },
                    ce_pdh: this.symbolPayloadCELevel.high,
                    ce_pdl: this.symbolPayloadCELevel.low,
                    pe_pdh: this.symbolPayloadPELevel.high,
                    pe_pdl: this.symbolPayloadPELevel.low,
                    timestamp: new Date().toISOString()
                };
                
                console.log('[fetchSymbolPayloadAndOption] 📊 Updating charts with transformed data...');
                await this.updateWithRealData(transformedData);
                console.log('[fetchSymbolPayloadAndOption] 🎉 Charts updated successfully!');

                this.addSignal(`✓ Option data fetched for CE ${ceStrike} / PE ${peStrike}`, 'SUCCESS');

                // Start auto-update polling after successful initial load
                // Only start if we're in market hours or dev mode
                if (this.isCurrentlyMarketHours()) {
                    console.log('[fetchSymbolPayloadAndOption] Starting auto-update...');
                    this.startAutoUpdate();
                } else {
                    console.log('[fetchSymbolPayloadAndOption] Outside market hours, auto-update will start at 9:15 AM IST');
                    this.addSignal('⏳ Outside market hours. Auto-update will start at 9:15 AM IST', 'INFO');
                }

            } else {
                this.addSignal('Error: CE or PE strike not found in symbol payload', 'ERROR');
            }

        } catch (error) {
            console.error('Error fetching symbol payload and option data:', error);
            this.addSignal(`Unexpected error: ${error.message}`, 'ERROR');
        }
    }

    async showDebugPanel() {
        // Fetch available strikes and show them
        try {
            const symbol = this.symbolSelect.value;
            this.addSignal(`Fetching available strikes for ${symbol}...`, 'INFO');

            const response = await fetch(`/api/intraday-option/debug-strikes?symbol=${symbol}`, {
                credentials: 'include'
            });

            if (!response.ok) {
                const error = await response.json();
                this.addSignal(`Debug Error: ${error.error || 'Unknown error'}`, 'ERROR');
                return;
            }

            const result = await response.json();

            if (!result.success) {
                this.addSignal(`${result.error}`, 'ERROR');
                if (result.diagnostic_info) {
                    this.addSignal(`Diagnostic: ${result.diagnostic_info.message}`, 'INFO');
                    result.diagnostic_info.reasons.forEach(reason => {
                        this.addSignal(`  - ${reason}`, 'WARNING');
                    });
                }
                return;
            }

            // Display available strikes
            this.addSignal(`Found ${result.available_strikes_count} available strikes for ${symbol}`, 'SUCCESS');
            this.addSignal(`Current underlying price: ${result.underlying_price}`, 'INFO');
            this.addSignal(`Recommended strike: ${result.recommended_strike}`, 'INFO');

            // Show sample strikes
            const sampleStrikes = result.sample_ce_strikes.slice(0, 5).join(', ');
            this.addSignal(`Sample available CE strikes: ${sampleStrikes}...`, 'INFO');

            // Create a strike selector modal
            this.showStrikeSelector(result.available_strikes, symbol);

        } catch (error) {
            console.error('Error showing debug panel:', error);
            this.addSignal(`Error: ${error.message}`, 'ERROR');
        }
    }

    showStrikeSelector(strikes, symbol) {
        // Create a modal to select strikes
        if (document.getElementById('strikeModal')) {
            document.getElementById('strikeModal').remove();
        }

        const modal = document.createElement('div');
        modal.id = 'strikeModal';
        modal.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #1e293b;
            border: 2px solid #3b82f6;
            border-radius: 8px;
            padding: 20px;
            z-index: 10000;
            max-height: 80vh;
            overflow-y: auto;
            min-width: 400px;
            color: #e2e8f0;
            box-shadow: 0 0 50px rgba(0,0,0,0.9);
        `;

        let content = `
            <h3 style="margin-top: 0; color: #60a5fa;">Available Strikes for ${symbol}</h3>
            <p>Select a strike to start monitoring:</p>
            <div style="max-height: 400px; overflow-y: auto;">
        `;

        strikes.forEach(strike => {
            content += `
                <div style="
                    padding: 8px;
                    margin: 5px 0;
                    background: #0f172a;
                    border: 1px solid #475569;
                    border-radius: 4px;
                    cursor: pointer;
                    transition: all 0.2s;
                " 
                onmouseover="this.style.background='#1e3a8a'; this.style.borderColor='#3b82f6';"
                onmouseout="this.style.background='#0f172a'; this.style.borderColor='#475569';"
                onclick="window.intradayTracker.selectStrike(${strike.strike}, ${strike.strike})">
                    Strike: ${strike.strike} | CE: ${strike.ce_symbol} | PE: ${strike.pe_symbol}
                </div>
            `;
        });

        content += `
            </div>
            <button onclick="document.getElementById('strikeModal').remove()" style="
                margin-top: 15px;
                padding: 10px 20px;
                background: #6366f1;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
            ">Close</button>
        `;

        modal.innerHTML = content;
        document.body.appendChild(modal);

        // Store reference for onclick handlers
        window.intradayTracker = this;
    }

    selectStrike(ceStrike, peStrike) {
        // Manually set and fetch strikes
        this.currentCeStrike = ceStrike;
        this.currentPeStrike = peStrike;
        this.manuallySelectedStrikes = true;  // Mark as manually selected

        const symbol = this.symbolSelect.value;
        const timeFrame = this.timeFrameSelect.value;

        this.addSignal(`Fetching option data for custom strikes: CE ${ceStrike}, PE ${peStrike}...`, 'INFO');

        // Fetch with custom strikes using POST endpoint
        const requestBody = {
            symbol: symbol,
            timeframe: timeFrame,
            ce_strike: ceStrike,
            pe_strike: peStrike
        };
        
        fetch('/api/options-chart-data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(requestBody)
        })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(error => {
                        throw new Error(error.error || `HTTP ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(result => {
                if (result.success) {
                    // Transform merged API response into CE/PE separated format
                    const ceCandles = result.data.filter(c => c.type === 'CE');
                    const peCandles = result.data.filter(c => c.type === 'PE');
                    
                    console.log(`[selectStrike] Separated candles - CE: ${ceCandles.length}, PE: ${peCandles.length}`);
                    
                    // Map to expected format with current symbol payload levels
                    const transformedData = {
                        ce_data: { candles: ceCandles },
                        pe_data: { candles: peCandles },
                        ce_pdh: this.symbolPayloadCELevel.high,
                        ce_pdl: this.symbolPayloadCELevel.low,
                        pe_pdh: this.symbolPayloadPELevel.high,
                        pe_pdl: this.symbolPayloadPELevel.low,
                        timestamp: new Date().toISOString()
                    };
                    
                    this.updateWithRealData(transformedData);
                    // Don't need to store tokens here - they were used to fetch the data
                    console.log(`[selectStrike] Successfully updated charts with ${ceCandles.length} CE and ${peCandles.length} PE candles`);
                    this.addSignal(`Successfully loaded CE ${ceStrike} and PE ${peStrike}`, 'SUCCESS');
                } else {
                    this.addSignal(`Error: ${result.error}`, 'ERROR');
                }
                document.getElementById('strikeModal')?.remove();
            })
            .catch(error => {
                this.addSignal(`Error: ${error.message}`, 'ERROR');
            });
    }

    async fetch() {
        try {
            // REQUIRED: CE and PE strikes must be loaded before making any API calls
            if (!this.currentCeStrike || !this.currentPeStrike) {
                console.warn('[Fetch] CE/PE strikes not loaded');
                this.addSignal('Error: CE and PE strikes not loaded. Cannot fetch data.', 'ERROR');
                return;
            }

            const symbol = this.symbolSelect.value;
            const timeFrame = this.timeFrameSelect.value;
            const fetchStartTime = Date.now();
            
            // KEY FIX: Use stored strikes (which persist even if symbols reset)
            const ceStrikeToUse = this.currentCeStrike;
            const peStrikeToUse = this.currentPeStrike;
            
            console.log(`[Fetch] Using strikes - CE: ${ceStrikeToUse}, PE: ${peStrikeToUse} (manually selected: ${this.manuallySelectedStrikes})`);

            // Build POST request body - prefer tokens for faster response
            const requestBody = { timeframe: timeFrame };
            if (this.currentCeToken && this.currentPeToken) {
                requestBody.ce_token = this.currentCeToken;
                requestBody.pe_token = this.currentPeToken;
                console.log(`[Fetch] Using tokens - CE: ${this.currentCeToken}, PE: ${this.currentPeToken}`);
            } else {
                requestBody.symbol = symbol;
                requestBody.ce_strike = ceStrikeToUse;
                requestBody.pe_strike = peStrikeToUse;
                console.log(`[Fetch] Using strikes - CE: ${ceStrikeToUse}, PE: ${peStrikeToUse}`);
            }

            // Fetch with timeout handling
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 15000); // 15 second timeout

            let response;
            try {
                response = await fetch('/api/options-chart-data', { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify(requestBody),
                    signal: controller.signal
                });
            } finally {
                clearTimeout(timeoutId);
            }

            if (!response.ok) {
                let errorMsg = 'Unknown error';
                try {
                    const error = await response.json();
                    errorMsg = error.error || errorMsg;
                } catch (e) {
                    errorMsg = `HTTP ${response.status}`;
                }
                console.error(`[Fetch] API error: ${errorMsg}`);
                this.failedUpdateCount++;
                if (this.isAutoUpdating) {
                    console.log(`[AutoUpdate] Failed update #${this.failedUpdateCount}`);
                } else {
                    this.addSignal(`API Error: ${errorMsg}`, 'ERROR');
                }
                return;
            }

            let result;
            try {
                result = await response.json();
            } catch (parseError) {
                console.error('[Fetch] Failed to parse response:', parseError);
                this.failedUpdateCount++;
                if (!this.isAutoUpdating) {
                    this.addSignal('Error: Invalid response format', 'ERROR');
                }
                return;
            }

            if (!result.success) {
                console.error('[Fetch] API returned error:', result.error);
                this.failedUpdateCount++;
                if (!this.isAutoUpdating) {
                    this.addSignal(`Error: ${result.error || 'Failed to fetch data'}`, 'ERROR');
                }
                return;
            }

            const fetchDuration = Date.now() - fetchStartTime;
            
            // Transform merged API response into CE/PE separated format
            const ceCandles = result.data.filter(c => c.type === 'CE');
            const peCandles = result.data.filter(c => c.type === 'PE');
            
            console.log(`[Fetch] Separated candles - CE: ${ceCandles.length}, PE: ${peCandles.length} (${fetchDuration}ms)`);
            
            // Map to expected format with current symbol payload levels
            const transformedData = {
                ce_data: { candles: ceCandles },
                pe_data: { candles: peCandles },
                ce_pdh: this.symbolPayloadCELevel.high,
                ce_pdl: this.symbolPayloadCELevel.low,
                pe_pdh: this.symbolPayloadPELevel.high,
                pe_pdl: this.symbolPayloadPELevel.low,
                timestamp: new Date().toISOString()
            };
            
            // Update UI with real data
            await this.updateWithRealData(transformedData);

            // Track update statistics for auto-update
            if (this.isAutoUpdating) {
                this.updateCount++;
                this.lastUpdateTime = new Date();
                console.log(`[AutoUpdate] Update #${this.updateCount} completed in ${fetchDuration}ms`);
            } else {
                console.log(`[Fetch] Success (${fetchDuration}ms) - Updating charts...`);
            }

        } catch (error) {
            if (error.name === 'AbortError') {
                console.warn('[Fetch] Request timeout');
                this.addSignal('⚠️ Request timeout - retrying...', 'WARNING');
            } else {
                console.error('[Fetch] Error:', error);
                this.addSignal(`Error: ${error.message}`, 'ERROR');
            }
        }
    }

    async updateWithRealData(data) {
        // Update UI with real data from backend
        // Use formatChartData() for proper timestamp handling and chart population
        
        if (!data) {
            console.error('[UpdateChart] No data provided');
            this.addSignal('Error: No data received from server', 'ERROR');
            return;
        }

        console.log('[UpdateChart] Processing new data update...');

        // Validate chart objects exist
        if (!this.ceChart || !this.peChart) {
            console.error('[UpdateChart] Charts not initialized');
            this.addSignal('Error: Charts not initialized', 'ERROR');
            return;
        }

        let ceCandles = [];
        let peCandles = [];
        let chartUpdateCount = 0;

        // Extract cross-leg reference levels for charting (from symbol-payload API)
        // CE Chart shows: PE High (green), PE Low (red) from symbol-payload
        // PE Chart shows: CE High (green), CE Low (red) from symbol-payload
        const ceReferenceLines = {
            pe_payload_high: this.symbolPayloadPELevel.high,
            pe_payload_low: this.symbolPayloadPELevel.low
        };

        const peReferenceLines = {
            ce_payload_high: this.symbolPayloadCELevel.high,
            ce_payload_low: this.symbolPayloadCELevel.low
        };

        console.log('[UpdateChart] CE Reference Lines:', ceReferenceLines);
        console.log('[UpdateChart] PE Reference Lines:', peReferenceLines);
        console.log('[UpdateChart] Symbol Payload CE Level:', this.symbolPayloadCELevel);
        console.log('[UpdateChart] Symbol Payload PE Level:', this.symbolPayloadPELevel);

        // Update CE Chart using TradingViewChart API
        if (data.ce_data && data.ce_data.candles && data.ce_data.candles.length > 0) {
            ceCandles = this.formatChartData(data.ce_data.candles);
            if (ceCandles.length > 0) {
                try {
                    // Pass cross-leg reference lines to chart
                    // CE chart shows PE's high/low from symbol-payload as reference lines
                    this.ceChart.update(ceCandles, ceReferenceLines);
                    this.ceData = ceCandles;
                    chartUpdateCount++;

                    const lastCandle = ceCandles[ceCandles.length - 1];
                    const lastTime = new Date(lastCandle.time * 1000);
                    console.log(`[CE Chart] Updated with ${ceCandles.length} candles (Latest: ${lastTime.toLocaleTimeString()})`);
                    console.log(`[CE Chart] Reference levels - PE High: ${ceReferenceLines.pe_payload_high}, PE Low: ${ceReferenceLines.pe_payload_low}`);
                } catch (error) {
                    console.error('[CE Chart] Error updating candles:', error);
                    this.addSignal(`Error updating CE chart: ${error.message}`, 'ERROR');
                }
            }
        }

        // Update PE Chart using TradingViewChart API
        if (data.pe_data && data.pe_data.candles && data.pe_data.candles.length > 0) {
            peCandles = this.formatChartData(data.pe_data.candles);
            if (peCandles.length > 0) {
                try {
                    // Pass cross-leg reference lines to chart
                    // PE chart shows CE's high/low from symbol-payload as reference lines
                    this.peChart.update(peCandles, peReferenceLines);
                    this.peData = peCandles;
                    chartUpdateCount++;

                    const lastCandle = peCandles[peCandles.length - 1];
                    const lastTime = new Date(lastCandle.time * 1000);
                    console.log(`[PE Chart] Updated with ${peCandles.length} candles (Latest: ${lastTime.toLocaleTimeString()})`);
                    console.log(`[PE Chart] Reference levels - CE High: ${peReferenceLines.ce_payload_high}, CE Low: ${peReferenceLines.ce_payload_low}`);
                } catch (error) {
                    console.error('[PE Chart] Error updating candles:', error);
                    this.addSignal(`Error updating PE chart: ${error.message}`, 'ERROR');
                }
            }
        }

        // Update last update timestamp
        if (data.timestamp) {
            const updateTime = new Date(data.timestamp);
            const timeStr = updateTime.toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: true
            });
            document.getElementById('lastUpdate').textContent = timeStr;
            console.log(`[UpdateChart] Last update: ${timeStr}`);
        }

        // Log update summary with live prices
        if (chartUpdateCount > 0) {
            const ceCandleCount = ceCandles.length;
            const peCandleCount = peCandles.length;
            
            // Get latest candle info with prices
            let updateMsg = `📊 Live Update - `;
            
            if (ceCandleCount > 0) {
                const ceLast = ceCandles[ceCandleCount - 1];
                const ceTime = new Date(ceLast.time * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
                updateMsg += `CE: ${ceLast.close.toFixed(2)} (O:${ceLast.open.toFixed(2)}) [${ceTime}] | `;
            }
            
            if (peCandleCount > 0) {
                const peLast = peCandles[peCandleCount - 1];
                const peTime = new Date(peLast.time * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
                updateMsg += `PE: ${peLast.close.toFixed(2)} (O:${peLast.open.toFixed(2)}) [${peTime}]`;
            }
            
            console.log('[UpdateChart] ' + updateMsg);
        } else {
            console.warn('[UpdateChart] No candle data received');
            this.addSignal('⚠️ No candle data received. Market may be closed.', 'WARNING');
        }

        // Check for trading signals
        if (data.signal) {
            const signalType = data.signal.type === 'BUY_CE' || data.signal.type === 'BUY_PE' ? 'SUCCESS' : 'WARNING';
            const signalMsg = `🎯 ${data.signal.message}`;
            console.log('[Signal] ' + signalMsg);
            this.addSignal(signalMsg, signalType);
        }
    }

    /**
     * Format candlestick data for TradingView Lightweight Charts
     * Handles multiple timestamp formats and converts to Unix timestamp
     * 
     * @param {Array} candles - Raw candle data from API
     * @returns {Array} Formatted candles with time, open, high, low, close
     */
    formatChartData(candles) {
        if (!candles || !Array.isArray(candles)) {
            console.warn('[formatChartData] Invalid candles data:', candles);
            return [];
        }

        return candles.map((candle, idx) => {
            try {
                let timestamp;

                // Handle Unix timestamp (number) - Priority 1: 'time' field
                if (typeof candle.time === 'number') {
                    // Validate it's a reasonable timestamp
                    if (candle.time < 10000000000) {
                        // Seconds since epoch
                        timestamp = candle.time;
                    } else {
                        // Milliseconds since epoch
                        timestamp = Math.floor(candle.time / 1000);
                    }
                }
                // Priority 2: 'date' field (can be number or string)
                else if (typeof candle.date === 'number') {
                    // API returns date as Unix timestamp (seconds)
                    if (candle.date < 10000000000) {
                        timestamp = candle.date;
                    } else {
                        timestamp = Math.floor(candle.date / 1000);
                    }
                }
                else if (typeof candle.date === 'string') {
                    const dateObj = new Date(candle.date);
                    const timeMs = dateObj.getTime();
                    if (isNaN(timeMs)) {
                        console.warn(`[formatChartData] Invalid date string at index ${idx}: "${candle.date}"`);
                        return null;
                    }
                    timestamp = Math.floor(timeMs / 1000);
                }
                // Priority 3: 'timestamp' field
                else if (typeof candle.timestamp === 'number') {
                    if (candle.timestamp < 10000000000) {
                        timestamp = candle.timestamp;
                    } else {
                        timestamp = Math.floor(candle.timestamp / 1000);
                    }
                }
                else if (typeof candle.timestamp === 'string') {
                    const dateObj = new Date(candle.timestamp);
                    const timeMs = dateObj.getTime();
                    if (isNaN(timeMs)) {
                        console.warn(`[formatChartData] Invalid timestamp string at index ${idx}: "${candle.timestamp}"`);
                        return null;
                    }
                    timestamp = Math.floor(timeMs / 1000);
                }
                // Fallback: No valid timestamp found
                else {
                    console.warn(`[formatChartData] No valid timestamp at index ${idx}, skipping candle`, candle);
                    return null;
                }

                // Parse and validate OHLC values
                const open = parseFloat(candle.open) || 0;
                const high = parseFloat(candle.high) || 0;
                const low = parseFloat(candle.low) || 0;
                const close = parseFloat(candle.close) || 0;

                // Validate that we have valid prices
                if (isNaN(open) || isNaN(high) || isNaN(low) || isNaN(close)) {
                    console.warn(`[formatChartData] Invalid OHLC at index ${idx}:`, candle);
                    return null;  // Skip invalid candles
                }

                return {
                    time: timestamp,
                    open: open,
                    high: high,
                    low: low,
                    close: close
                };
            } catch (error) {
                console.error(`[formatChartData] Error processing candle at index ${idx}:`, error, candle);
                return null;
            }
        }).filter(candle => candle !== null);  // Remove invalid candles
    }

    evaluateStatus(currentPrice, pdh, pdl) {
        /**
         * Evaluate trading status based on price levels
         * Matches pattern from options_chart_app.js
         */
        if (currentPrice === null || currentPrice === undefined || pdh === null || pdl === null) {
            return { text: 'NO_DATA', className: 'status-no-data' };
        }

        if (currentPrice > pdh) {
            return { text: 'BULLISH', className: 'status-bullish' };
        }
        if (currentPrice < pdl) {
            return { text: 'BEARISH', className: 'status-bearish' };
        }
        return { text: 'SIDEWAY', className: 'status-sideway' };
    }

    updateUI() {
        // Charts are automatically updated via TradingViewChart.update()
        // No need for manual fitContent() or priceScale updates - the module handles all zoom and spacing
        console.log('[UpdateUI] Chart updated via TradingViewChart module');
    }

    updateCandleIfNew(candleData, type = 'CE') {
        /**
         * Update a single candle or add new candle if it's for a new time period
         * Used for real-time updates during continuous monitoring
         */
        try {
            if (!candleData || !candleData.time) return;

            // Format the candle
            let time = candleData.time;
            if (typeof time === 'string' && isNaN(parseInt(time))) {
                // Parse date string
                const date = new Date(candleData.date || candleData.time);
                time = Math.floor(date.getTime() / 1000);
            } else {
                time = parseInt(time);
            }

            const formattedCandle = {
                time: time,
                open: parseFloat(candleData.open),
                high: parseFloat(candleData.high),
                low: parseFloat(candleData.low),
                close: parseFloat(candleData.close)
            };

            // Get the appropriate series
            const series = type === 'CE' ? this.ceCandleSeries : this.peCandleSeries;
            const dataArray = type === 'CE' ? this.ceData : this.peData;

            // Check if candle with this time already exists
            const existingIndex = dataArray.findIndex(c => c.time === time);

            if (existingIndex >= 0) {
                // Update existing candle (OHLC update within same 5-min candle)
                series.update(formattedCandle);
                dataArray[existingIndex] = formattedCandle;
            } else {
                // New candle (new time period)
                series.update(formattedCandle);
                dataArray.push(formattedCandle);

                // Keep only last 50 candles
                if (dataArray.length > 50) {
                    dataArray.shift();
                    // Redraw all data
                    series.setData(dataArray);
                }
            }

            // Auto-fit content
            const chart = type === 'CE' ? this.ceChart : this.peChart;
            chart.timeScale().fitContent();

        } catch (error) {
            console.warn(`Error updating ${type} candle:`, error);
        }
    }

    async debugAvailableStrikes(symbol) {
        try {
            const response = await fetch(`/api/intraday-option/debug-strikes?symbol=${symbol}`, {
                credentials: 'include'
            });
            const result = await response.json();

            if (result.success) {
                this.addSignal(`📊 DEBUG INFO: Underlying price: ${result.underlying_price}`, 'INFO');
                
                if (result.available_strikes_count > 0) {
                    this.addSignal(`📊 Found ${result.available_strikes_count} available strikes`, 'INFO');
                    
                    // Show sample strikes
                    if (result.sample_ce_strikes && result.sample_ce_strikes.length > 0) {
                        const strikesList = result.sample_ce_strikes.slice(0, 10).join(', ');
                        this.addSignal(`✓ Sample strikes: ${strikesList}`, 'SUCCESS');
                    }
                    
                    // Show recommended strike
                    if (result.recommended_strike) {
                        this.addSignal(`💡 Recommended ATM strike: ${result.recommended_strike}`, 'SUCCESS');
                    }
                } else {
                    this.addSignal(`✗ No available strikes found for ${symbol}`, 'WARNING');
                    this.addSignal(`  • Market may be closed or options not available`, 'INFO');
                    this.addSignal(`  • Try a different symbol (BANKNIFTY, FINNIFTY)`, 'INFO');
                }

            } else {
                this.addSignal(`❌ Debug Error: ${result.error}`, 'ERROR');
                if (result.diagnostic_info) {
                    this.addSignal(`Diagnostic: ${result.diagnostic_info.message}`, 'INFO');
                    if (result.diagnostic_info.reasons) {
                        result.diagnostic_info.reasons.forEach(reason => {
                            this.addSignal(`  • ${reason}`, 'INFO');
                        });
                    }
                }
            }
        } catch (error) {
            console.error('Error fetching debug strikes:', error);
            this.addSignal(`❌ Debug Error: ${error.message}`, 'ERROR');
        }
    }

    /**
     * Fetch chart candlestick data only (for auto-update)
     * Similar to fetchChartDataFromApi in options_chart_app.js
     * Does NOT update symbol payload or strikes - only chart data
     * 
     * Returns: { success: bool, ceData: [], peData: [], message: string }
     */
    async fetchChartDataOnly() {
        try {
            // Need tokens to fetch data
            if (!this.currentCeToken || !this.currentPeToken) {
                console.log('[fetchChartDataOnly] Missing tokens, cannot fetch');
                return { success: false, message: 'Missing tokens' };
            }

            const timeFrame = this.timeFrameSelect.value;
            const payload = {
                ce_token: this.currentCeToken,
                pe_token: this.currentPeToken,
                timeframe: timeFrame,
                live: true
            };

            console.log('[fetchChartDataOnly] Fetching with payload:', payload);

            const response = await fetch('/api/options-chart-data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                let errorMsg = 'Unknown error';
                try {
                    const error = await response.json();
                    errorMsg = error.error || errorMsg;
                } catch (e) {
                    errorMsg = `HTTP ${response.status}`;
                }
                console.error('[fetchChartDataOnly] API error:', errorMsg);
                return { success: false, message: errorMsg };
            }

            const result = await response.json();

            if (result.success && result.data) {
                const ceData = result.data.filter(c => c.type === 'CE');
                const peData = result.data.filter(c => c.type === 'PE');
                console.log('[fetchChartDataOnly] Success - CE:', ceData.length, 'PE:', peData.length);
                return { success: true, ceData: ceData, peData: peData, message: '' };
            } else {
                const errorMsg = result.error || result.message || 'Failed to fetch chart data';
                console.error('[fetchChartDataOnly] API error:', errorMsg);
                return { success: false, message: errorMsg };
            }

        } catch (error) {
            console.error('[fetchChartDataOnly] Exception:', error);
            return { success: false, message: error.message };
        }
    }

    /**
     * Update charts with new candlestick data (for auto-update)
     * Similar to renderCombinedChart in options_chart_app.js
     * Only updates candlestick data, does NOT update reference lines
     */
    async updateChartsWithNewData(ceData, peData) {
        try {
            if (!this.ceChart || !this.peChart) {
                console.error('[updateChartsWithNewData] Charts not initialized');
                return;
            }

            if (!ceData || !peData || ceData.length === 0 || peData.length === 0) {
                console.warn('[updateChartsWithNewData] No data provided');
                return;
            }

            // Update CE Chart
            try {
                this.ceChart.update(ceData);
                this.ceData = ceData;
                console.log('[updateChartsWithNewData] CE chart updated with', ceData.length, 'candles');
            } catch (error) {
                console.error('[updateChartsWithNewData] Error updating CE chart:', error);
            }

            // Update PE Chart
            try {
                this.peChart.update(peData);
                this.peData = peData;
                console.log('[updateChartsWithNewData] PE chart updated with', peData.length, 'candles');
            } catch (error) {
                console.error('[updateChartsWithNewData] Error updating PE chart:', error);
            }

            // Update timestamp
            try {
                const updateTime = new Date();
                const timeStr = updateTime.toLocaleTimeString('en-US', {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: true
                });
                const lastUpdateEl = document.getElementById('lastUpdate');
                if (lastUpdateEl) {
                    lastUpdateEl.textContent = timeStr;
                }
            } catch (e) {
                // Silently ignore timestamp update failures
            }

        } catch (error) {
            console.error('[updateChartsWithNewData] Error:', error);
        }
    }

    /**
     * Start automatic polling for live data updates
     * Similar to setAutoUpdateTimeout() in options_chart_app.js
     * Only updates candlestick data during market hours (does NOT update reference lines)
     */
    startAutoUpdate() {
        if (this.isAutoUpdating) {
            console.log('[AutoUpdate] Already running, skipping...');
            return;
        }

        // Don't start without tokens (which are obtained from symbol-payload)
        if (!this.currentCeToken || !this.currentPeToken) {
            console.warn('[AutoUpdate] Cannot start - missing CE/PE tokens');
            return;
        }

        // Check market hours before starting (unless in dev mode)
        const isInMarketHours = this.isCurrentlyMarketHours();
        const shouldStart = isInMarketHours;
        
        console.log('[AutoUpdate] Market hours check:', {
            currentTime: new Date().toLocaleTimeString('en-US'),
            isInMarketHours: isInMarketHours,
            shouldStart: shouldStart
        });
        
        if (!shouldStart) {
            console.warn('[AutoUpdate] Deferring start until market opens (9:15 AM IST)');
            this.addSignal('⏳ Market closed. Auto-update will start at 9:15 AM IST', 'WARNING');
            return;
        }
        this.isAutoUpdating = true;
        this.updateCount = 0;
        this.failedUpdateCount = 0;

        // Set up periodic polling (every 5 seconds)
        this.autoUpdateInterval = setInterval(async () => {

            // Check if it's within Indian market hours (9:15 AM - 3:30 PM, Monday-Friday)
            if (!this.isCurrentlyMarketHours()) {
                console.log('Auto-update paused: Outside market hours');
                this.stopAutoUpdate();
                return;
            }

            // Verify tokens still exist
            if (!this.currentCeToken || !this.currentPeToken) {
                console.log('[AutoUpdate] 🛑 Tokens lost, stopping auto-update');
                this.stopAutoUpdate();
                return;
            }

            // Fetch only chart data (not symbol payload)
            try {
                const result = await this.fetchChartDataOnly();
                
                if (result.success && result.ceData && result.peData) {
                    // Update charts with new candlestick data (does NOT update reference lines)
                    await this.updateChartsWithNewData(result.ceData, result.peData);
                    
                    this.updateCount++;
                    this.lastUpdateTime = new Date();
                    console.log(`[AutoUpdate] Update #${this.updateCount} completed`);
                } else {
                    this.failedUpdateCount++;
                    console.warn(`[AutoUpdate] Failed update #${this.failedUpdateCount}: ${result.message}`);
                }
            } catch (error) {
                this.failedUpdateCount++;
                console.error('[AutoUpdate] Exception:', error);
            }
        }, this.autoUpdateDelay); // 5 seconds
    }

    /**
     * Stop automatic polling
     * Similar to clearAutoUpdateTimeout() in options_chart_app.js
     */
    stopAutoUpdate() {
        if (!this.isAutoUpdating) {
            return;
        }

        if (this.autoUpdateInterval) {
            clearInterval(this.autoUpdateInterval);
            this.autoUpdateInterval = null;
        }

        this.isAutoUpdating = false;
        const successRate = this.updateCount > 0 ? Math.round(((this.updateCount - this.failedUpdateCount) / this.updateCount) * 100) : 0;
        console.log(`[AutoUpdate] 🛑 STOPPED - ${this.updateCount} updates (${successRate}% success, ${this.failedUpdateCount} failures)`);
        this.addSignal(`⏹️ Auto-update stopped - ${this.updateCount} successful updates, ${this.failedUpdateCount} failures`, 'WARNING');
    }

    /**
     * Get current auto-update status and statistics
     */
    getAutoUpdateStatus() {
        return {
            isRunning: this.isAutoUpdating,
            updateCount: this.updateCount,
            failedCount: this.failedUpdateCount,
            lastUpdateTime: this.lastUpdateTime,
            interval: this.autoUpdateDelay,
            marketHoursActive: this.isCurrentlyMarketHours()
        };
    }

    addSignal(message, type = 'INFO') {
        const entry = document.createElement('div');
        entry.className = 'signal-entry';

        const time = new Date().toLocaleTimeString();
        entry.innerHTML = `
            <span class="time">${time}</span>
            <span class="message">${message}</span>
            <span class="type ${type}">${type}</span>
        `;

        // Add to top of signals log
        this.signalsContainer.insertBefore(entry, this.signalsContainer.firstChild);

        // Keep only last 50 signals
        while (this.signalsContainer.children.length > 50) {
            this.signalsContainer.removeChild(this.signalsContainer.lastChild);
        }

        // Auto-scroll to top
        this.signalsContainer.scrollTop = 0;
    }
}

// ===========================
// INITIALIZATION
// ===========================
// Create global instance when DOM is ready
let intradayTracker = null;

// Check if DOM is already loaded
if (document.readyState === 'loading') {
    // DOM is still loading, wait for it
    document.addEventListener('DOMContentLoaded', () => {
        console.log('[Init] DOM loaded, creating IntradayOptionTracker...');
        intradayTracker = new IntradayOptionTracker();
        console.log('[Init] IntradayOptionTracker instance created');
    });
} else {
    // DOM is already loaded
    console.log('[Init] DOM ready, creating IntradayOptionTracker...');
    intradayTracker = new IntradayOptionTracker();
    console.log('[Init] IntradayOptionTracker instance created');
}