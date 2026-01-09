/**
 * app.js (Pure Vanilla JavaScript - Application Utility)
 * Defines a global utility for fetching data from API endpoints.
 */
(function() {
    "use strict";

/**
 * Global utility function to fetch data and handle common API concerns:
 * session expiration (401/403), error messages, and JSON parsing.
 * Includes automatic retry logic for transient 403 errors.
 * Assumes showNotification is available (from notifications.js).
 * @param {string} url - The API endpoint URL.
 * @param {object} options - Fetch options (e.g., method, headers, body).
 * @param {number} maxRetries - Maximum retry attempts (default: 3).
 * @returns {Promise<object>} - The parsed JSON response object.
 */
window.fetchJson = async function(url, options = {}, maxRetries = 3) {
    let lastError;
    let lastResponse;
    
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            const response = await fetch(url, options);
            lastResponse = response;

            // Handle session expired / unauthorized (401 - permanent)
            if (response.status === 401) {
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

            // Handle 403 Forbidden - retry up to maxRetries times with delay
            if (response.status === 403) {
                if (attempt < maxRetries) {
                    console.warn(`[Attempt ${attempt}/${maxRetries}] Got 403 Forbidden. Retrying in ${attempt * 500}ms...`);
                    // Exponential backoff: 500ms, 1000ms, 1500ms
                    await new Promise(resolve => setTimeout(resolve, attempt * 500));
                    continue; // Retry the request
                } else {
                    // Final attempt failed, show error
                    try {
                        const data = await response.json();
                        const errorMsg = data.error || 'Access forbidden. Please check your permissions and try again.';
                        if (typeof showNotification === 'function') {
                            showNotification(errorMsg, 'error');
                        }
                    } catch (e) {
                        if (typeof showNotification === 'function') {
                            showNotification('Access denied (403). Please refresh the page and try again.', 'error');
                        }
                    }
                    return { success: false, message: 'Forbidden', statusCode: 403 };
                }
            }

            // Handle non-OK status codes (other than 401/403)
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
            
            // Success! Return parsed JSON data
            if (attempt > 1) {
                console.log(`[Success] Request succeeded after ${attempt} attempts`);
            }
            return data;
        } catch (error) {
            lastError = error;
            console.error(`[Attempt ${attempt}/${maxRetries}] Fetch error:`, error);
            
            // For network errors, retry if we haven't exceeded max retries
            if (attempt < maxRetries) {
                console.warn(`Retrying in ${attempt * 500}ms...`);
                await new Promise(resolve => setTimeout(resolve, attempt * 500));
                continue;
            }
        }
    }
    
    // All retries exhausted
    console.error('Fetch failed after all retry attempts');
    if (typeof showNotification === 'function') {
        showNotification(`Failed to load data. ${lastError?.message || 'Please refresh the page.'}`, 'error');
    }
    return { success: false, message: lastError?.message || 'Request failed' };
};

})();