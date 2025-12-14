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
     * Fetches chart data for options.
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
            throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
        }
        return response.json();
    }

    return {
        getStrikes,
        getChartData
    };
})();

// If this project uses ES Modules, you would typically export it like this:
// export const optionsChartService = { getStrikes, getChartData };
// For now, it's wrapped in an IIFE to maintain a similar scope pattern to the original.