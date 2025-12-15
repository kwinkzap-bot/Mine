// options_chart.service.js (Vanilla JS)

const optionsChartService = (() => {
    'use strict';

    const API_BASE_URL = ''; // Assuming API calls are relative to the current origin

    /**
     * Fetches strike prices for a given symbol.
     * @param {string} symbol - The trading symbol.
     * @returns {Promise<Object>} A promise that resolves to the strike data.
     */
    async function getStrikes(symbol) {
        const url = `${API_BASE_URL}/api/options-strikes?symbol=${encodeURIComponent(symbol)}`;
        const response = await fetch(url);
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
        }
        return response.json();
    }

    /**
     * Fetches chart data for options with error handling for 429 status.
     * @param {string} ceToken - Call option token.
     * @param {string} peToken - Put option token.
     * @param {string} timeframe - The desired timeframe for the chart data.
     * @returns {Promise<Object>} A promise that resolves to the chart data.
     */
    async function getChartData(ceToken, peToken, timeframe) {
        const url = `${API_BASE_URL}/api/options-chart-data`;
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                ce_token: ceToken,
                pe_token: peToken,
                timeframe: timeframe
            }),
        });
        if (!response.ok) {
            const errorText = await response.text();
            const status = response.status;
            if (status === 429) {
                throw new Error(`HTTP 429: Too Many Requests - ${errorText}`);
            }
            throw new Error(`HTTP error! status: ${status}, message: ${errorText}`);
        }
        return response.json();
    }

    // Live polling support with request deduplication and exponential backoff
    let _pollerId = null;
    let _wasMarketOpen = null;
    let _pendingRequest = null; // Track pending request to prevent duplicates
    let _retryCount = 0;
    let _currentBackoff = 0; // Current backoff milliseconds

    function isMarketOpen() {
        // Determine current time in IST (UTC+5:30)
        const now = new Date();
        const ist = new Date(now.getTime() + 330 * 60000); // 330 minutes = 5.5 hours
        const day = ist.getDay(); // 0 = Sunday, 6 = Saturday
        if (day === 0 || day === 6) return false;
        const hours = ist.getHours();
        const minutes = ist.getMinutes();
        const totalMinutes = hours * 60 + minutes;
        const openMinutes = 9 * 60 + 15; // 09:15 IST
        const closeMinutes = 15 * 60 + 30; // 15:30 IST
        return totalMinutes >= openMinutes && totalMinutes <= closeMinutes;
    }

    function stopLiveUpdates() {
        if (_pollerId) {
            clearInterval(_pollerId);
            _pollerId = null;
        }
        _pendingRequest = null;
        _retryCount = 0;
        _currentBackoff = 0;
    }

    /**
     * Starts live polling for option chart data with request deduplication and exponential backoff.
     * Prevents 429 Too Many Requests errors by:
     * 1. Skipping requests if previous request still pending
     * 2. Increasing interval by 3x when 429 errors occur
     * 3. Default interval is 3 seconds (20 requests/min) instead of 2 seconds
     * 
     * @param {string|number} ceToken
     * @param {string|number} peToken
     * @param {string} timeframe
     * @param {function} onUpdate - called with parsed JSON data on success
     * @param {function} onError - called with Error on failure
     * @param {number} intervalMs - polling interval in milliseconds (default 3000)
     * @returns {{stop: function}} stopper object
     */
    function startLiveUpdates(ceToken, peToken, timeframe, onUpdate, onError, intervalMs = 3000) {
        stopLiveUpdates();
        _wasMarketOpen = null;
        _retryCount = 0;
        _currentBackoff = 0;
        
        _pollerId = setInterval(async () => {
            try {
                const marketOpen = isMarketOpen();
                // Notify once if market is closed when starting
                if (!_wasMarketOpen && !marketOpen) {
                    _wasMarketOpen = false;
                    if (typeof onError === 'function') {
                        try { onError(new Error('Market is closed. Live updates suspended.')); } catch (_) {}
                    }
                    return; // skip fetch while market closed
                }

                // If market just opened, reset backoff and clear the flag
                if (_wasMarketOpen === false && marketOpen) {
                    _wasMarketOpen = true;
                    _retryCount = 0;
                    _currentBackoff = 0;
                }

                if (!marketOpen) return; // skip fetches outside market hours

                // Request deduplication: skip if previous request still pending
                if (_pendingRequest) {
                    console.debug('Skipping request - previous request still pending');
                    return;
                }

                _pendingRequest = getChartData(ceToken, peToken, timeframe)
                    .then(data => {
                        // Success: reset backoff on successful request
                        if (_retryCount > 0) {
                            console.log('Rate limiting recovered. Resuming normal polling.');
                            _retryCount = 0;
                            _currentBackoff = 0;
                        }
                        if (typeof onUpdate === 'function') onUpdate(data);
                    })
                    .catch(err => {
                        // Handle 429 (Too Many Requests) with exponential backoff
                        if (err.message && err.message.includes('429')) {
                            _retryCount++;
                            _currentBackoff = intervalMs * Math.pow(3, _retryCount - 1);
                            console.warn(`Rate limited (429). Retry count: ${_retryCount}. Next request in ${(_currentBackoff / 1000).toFixed(1)}s`);
                            
                            // Notify error but continue polling
                            if (typeof onError === 'function' && _retryCount === 1) {
                                try { onError(new Error('Rate limited (429). Increasing polling interval...')); } catch (_) {}
                            }
                        } else {
                            if (typeof onError === 'function') onError(err);
                        }
                    })
                    .finally(() => {
                        _pendingRequest = null;
                    });
            } catch (err) {
                if (typeof onError === 'function') onError(err);
                _pendingRequest = null;
            }
        }, intervalMs + _currentBackoff);

        return { stop: stopLiveUpdates };
    }

    return {
        getStrikes,
        getChartData
        , startLiveUpdates, stopLiveUpdates
    };
})();

// If this project uses ES Modules, you would typically export it like this:
// export const optionsChartService = { getStrikes, getChartData };
// For now, it's wrapped in an IIFE to maintain a similar scope pattern to the original.