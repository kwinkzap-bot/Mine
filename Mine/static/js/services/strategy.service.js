/**
 * strategy.service.js - Pure Vanilla JavaScript API service.
 * Handles communication with the backend for the backtest feature.
 * Assumes window.fetchJson is available from app.js.
 */
const StrategyService = (function() {
    'use strict';

    const API_ENDPOINT = '/api/strategy-backtest';

    /**
     * Runs the backtest by calling the backend API.
     * @param {string} symbol - The trading symbol (e.g., 'NIFTY').
     * @param {string} startDate - The start date in 'YYYY-MM-DD' format.
     * @param {string} endDate - The end date in 'YYYY-MM-DD' format.
     * @returns {Promise<object>} - The API response data object.
     */
    async function runBacktest(symbol, startDate, endDate) {
        const options = {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                symbol: symbol,
                start_date: startDate,
                end_date: endDate
            })
        };

        // Use the global fetchJson utility (from app.js)
        const data = await window.fetchJson(API_ENDPOINT, options);
        return data;
    }

    // Expose public methods
    return {
        runBacktest: runBacktest
    };
})();