/**
 * app.js (Pure Vanilla JavaScript - Application Utility)
 * Defines a global utility for fetching data from API endpoints.
 */
(function() {
    "use strict";

    /**
     * Global utility function to fetch data and handle common API concerns:
     * session expiration (401/403), error messages, and JSON parsing.
     * Assumes showNotification is available (from notifications.js).
     * @param {string} url - The API endpoint URL.
     * @param {object} options - Fetch options (e.g., method, headers, body).
     * @returns {Promise<object>} - The parsed JSON response object.
     */
    window.fetchJson = async function(url, options = {}) {
        try {
            const response = await fetch(url, options);

            // Handle session expired / unauthorized
            if (response.status === 401 || response.status === 403) {
                if (typeof showNotification === 'function') {
                    showNotification('Session expired. Redirecting to login...', 'warning');
                }
                setTimeout(() => { window.location.href = '/login'; }, 1000);
                // Return a failure object to stop further processing
                return { success: false, message: 'Unauthorized', needs_login: true };
            }

            // Handle non-OK status codes
            if (!response.ok) {
                const errorText = await response.text();
                let errorData = {};
                try {
                    // Try to parse the error message if it's JSON
                    errorData = JSON.parse(errorText);
                } catch (e) {
                    // Fallback if response is not JSON
                    throw new Error(`Server error: HTTP status ${response.status}`);
                }
                throw new Error(errorData.message || `HTTP error! status: ${response.status}`);
            }
            
            // Return parsed JSON data
            return await response.json();
        } catch (error) {
            console.error('Fetch error:', error);
            // Handle network errors or other exceptions
            if (typeof showNotification === 'function') {
                 showNotification(`Error: ${error.message}`, 'error');
            }
            // Return a standard failure object
            return { success: false, message: error.message };
        }
    };

})();