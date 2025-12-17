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

        // Handle session expired / unauthorized (401/403)
        if (response.status === 401 || response.status === 403) {
            try {
                const data = await response.json();
                const errorMsg = data.error || 'Your session has expired or you are not authorized. Please login again.';
                if (typeof showNotification === 'function') {
                    showNotification(errorMsg, 'error');
                }
            } catch (e) {
                if (typeof showNotification === 'function') {
                    showNotification('Authentication error. Redirecting to login...', 'error');
                }
            }
            // Redirect to login after showing notification
            setTimeout(() => { window.location.href = '/auth/login'; }, 1500);
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
        
        // Parse the response
        const data = await response.json();
        
        // Check if response contains auth_error flag (even with 200 status)
        if (data && data.auth_error === true) {
            const errorMsg = data.error || 'Authentication required. Redirecting to login...';
            if (typeof showNotification === 'function') {
                showNotification(errorMsg, 'error');
            }
            setTimeout(() => { window.location.href = '/auth/login'; }, 1500);
            return { success: false, message: 'Unauthorized', needs_login: true };
        }
        
        // Return parsed JSON data
        return data;
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