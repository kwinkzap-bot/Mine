/**
 * app.js (Pure Vanilla JavaScript - Application Utility)
 * Defines a global utility for fetching data from API endpoints.
 * OPTIMIZED: Added request deduplication, caching, and performance utilities.
 */
(function () {
    "use strict";

    // ===================== PERFORMANCE UTILITIES =====================
    
    /**
     * Simple in-memory cache for API responses
     */
    const _responseCache = new Map();
    const _pendingRequests = new Map();
    const CACHE_DEFAULT_TTL = 30000; // 30 seconds default

    /**
     * Debounce utility - prevents rapid repeated calls
     * @param {Function} func - Function to debounce
     * @param {number} wait - Wait time in ms
     * @returns {Function} - Debounced function
     */
    window.debounce = function(func, wait = 300) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    };

    /**
     * Throttle utility - limits function calls to once per interval
     * @param {Function} func - Function to throttle
     * @param {number} limit - Time limit in ms
     * @returns {Function} - Throttled function
     */
    window.throttle = function(func, limit = 1000) {
        let inThrottle;
        return function(...args) {
            if (!inThrottle) {
                func.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    };

    /**
     * Clear cache for a specific URL or all cache
     * @param {string} url - Optional URL to clear, or clear all if omitted
     */
    window.clearFetchCache = function(url) {
        if (url) {
            _responseCache.delete(url);
        } else {
            _responseCache.clear();
        }
    };

    /**
     * Global utility function to fetch data and handle common API concerns:
     * session expiration (401/403), error messages, and JSON parsing.
     * OPTIMIZED: Added request deduplication and optional caching.
     * @param {string} url - The API endpoint URL.
     * @param {object} options - Fetch options (e.g., method, headers, body).
     * @param {number} maxRetries - Maximum retry attempts (default: 3).
     * @param {object} cacheOptions - Optional {enabled: bool, ttl: number in ms}
     * @returns {Promise<object>} - The parsed JSON response object.
     */
    window.fetchJson = async function (url, options = {}, maxRetries = 3, cacheOptions = {}) {
        const cacheEnabled = cacheOptions.enabled || false;
        const cacheTTL = cacheOptions.ttl || CACHE_DEFAULT_TTL;
        const cacheKey = `${options.method || 'GET'}:${url}:${JSON.stringify(options.body || '')}`;
        
        // Check cache first (only for GET requests)
        if (cacheEnabled && (!options.method || options.method === 'GET')) {
            const cached = _responseCache.get(cacheKey);
            if (cached && (Date.now() - cached.timestamp < cacheTTL)) {
                console.debug(`[Cache HIT] ${url}`);
                return cached.data;
            }
        }
        
        // Request deduplication - if same request is in flight, wait for it
        if (_pendingRequests.has(cacheKey)) {
            console.debug(`[Dedup] Waiting for existing request: ${url}`);
            return _pendingRequests.get(cacheKey);
        }
        
        let lastError;
        let lastResponse;

        const requestPromise = (async () => {
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
                
                // Cache successful response if caching is enabled
                if (cacheEnabled && data.success !== false) {
                    _responseCache.set(cacheKey, { data, timestamp: Date.now() });
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
        })();
        
        // Track pending request for deduplication
        _pendingRequests.set(cacheKey, requestPromise);
        
        try {
            return await requestPromise;
        } finally {
            _pendingRequests.delete(cacheKey);
        }
    };

    /**
     * Dedicated utility to get available brokers with built-in deduplication 
     * and caching across ALL app components.
     * Prevents multiple concurrent API calls (e.g., from base.html and child pages).
     */
    let _brokersPromise = null;
    window.getAvailableBrokers = function (forceRefresh = false) {
        if (forceRefresh || !_brokersPromise) {
            console.debug(`[Brokers] Starting fetch (force=${forceRefresh})`);
            _brokersPromise = window.fetchJson('/api/available-brokers', {}, 3, { enabled: true, ttl: 5000 });
        }
        return _brokersPromise;
    };

    /**
     * Show modal to collect Kotak Neo TOTP secret and authenticate
     * @param {string} loginUrl - The URL to post authentication data to (default: /auth/login/kotak)
     */
    window.showKotakLoginModal = function (loginUrl = '/auth/login/kotak') {
        // Create modal HTML
        const modalHtml = `
        <div id="kotakLoginModal" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;">
            <div style="background: white; padding: 30px; border-radius: 8px; max-width: 500px; width: 90%; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                <h2 style="margin-top: 0; color: #333;">Kotak Neo Authentication</h2>
                <p style="color: #666; margin-bottom: 20px;">Please enter your TOTP secret to authenticate with Kotak Neo.</p>
                
                <div style="margin-bottom: 15px;">
                    <label style="display: block; margin-bottom: 5px; color: #333; font-weight: bold;">6-Digit OTP *</label>
                    <input type="text" id="kotakTotpSecret" placeholder="Enter 6-digit OTP from authenticator app" 
                           maxlength="6" pattern="[0-9]{6}"
                           onkeydown="if(event.key==='Enter'){event.preventDefault();submitKotakLogin();}"
                           style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;">
                    <small style="color: #666; display: block; margin-top: 5px;">
                        Enter the 6-digit code from your authenticator app (Google Authenticator, Authy, etc.)<br>
                        The code changes every 30 seconds.
                    </small>
                </div>
                
                <div style="display: flex; gap: 10px; justify-content: flex-end; margin-top: 25px;">
                    <button onclick="closeKotakLoginModal()" 
                            style="padding: 10px 20px; border: 1px solid #ddd; background: white; color: #333; border-radius: 4px; cursor: pointer; font-size: 14px;">
                        Cancel
                    </button>
                    <button onclick="submitKotakLogin()" 
                            style="padding: 10px 20px; border: none; background: #007bff; color: white; border-radius: 4px; cursor: pointer; font-size: 14px;">
                        Authenticate
                    </button>
                </div>
                
                <div id="kotakLoginStatus" style="margin-top: 15px; padding: 10px; border-radius: 4px; display: none;"></div>
            </div>
        </div>
    `;

        // Remove existing modal if present
        const existingModal = document.getElementById('kotakLoginModal');
        if (existingModal) {
            existingModal.remove();
        }

        // Add modal to page
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Store login URL for submit function
        window.kotakLoginUrl = loginUrl;

        // Focus on TOTP input
        setTimeout(() => {
            document.getElementById('kotakTotpSecret')?.focus();
        }, 100);
    };

    /**
     * Close the Kotak login modal
     */
    window.closeKotakLoginModal = function () {
        const modal = document.getElementById('kotakLoginModal');
        if (modal) {
            modal.remove();
        }
    };

    /**
     * Submit Kotak Neo authentication with TOTP secret
     */
    window.submitKotakLogin = async function () {
        const totpSecret = document.getElementById('kotakTotpSecret')?.value?.trim();
        const statusDiv = document.getElementById('kotakLoginStatus');

        if (!totpSecret) {
            if (statusDiv) {
                statusDiv.style.display = 'block';
                statusDiv.style.background = '#fff3cd';
                statusDiv.style.color = '#856404';
                statusDiv.textContent = 'Please enter your 6-digit OTP';
            }
            return;
        }

        // Show loading status
        if (statusDiv) {
            statusDiv.style.display = 'block';
            statusDiv.style.background = '#d1ecf1';
            statusDiv.style.color = '#0c5460';
            statusDiv.textContent = 'Authenticating with Kotak Neo...';
        }

        try {
            const requestBody = {
                totp_secret: totpSecret
            };

            // Set a 60-second frontend timeout to prevent indefinite waiting
            const controller = new AbortController();
            const timeoutId = setTimeout(() => {
                controller.abort();
                if (statusDiv) {
                    statusDiv.style.background = '#f8d7da';
                    statusDiv.style.color = '#721c24';
                    statusDiv.textContent = '✗ Request timeout - Kotak API not responding. Try again.';
                }
            }, 60000);

            const response = await fetch(window.kotakLoginUrl || '/auth/login/kotak', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody),
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            const data = await response.json();

            if (data.success) {
                // Success
                if (statusDiv) {
                    statusDiv.style.background = '#d4edda';
                    statusDiv.style.color = '#155724';
                    statusDiv.textContent = '✓ Authentication successful! You are now logged in.';
                }

                if (typeof showNotification === 'function') {
                    showNotification('Successfully authenticated with Kotak Neo!', 'success');
                }

                // Close modal after 1.5 seconds
                setTimeout(() => {
                    closeKotakLoginModal();
                }, 1500);

            } else {
                // Error
                if (statusDiv) {
                    statusDiv.style.background = '#f8d7da';
                    statusDiv.style.color = '#721c24';
                    statusDiv.textContent = `✗ ${data.error || data.message || 'Authentication failed'}`;
                }

                if (typeof showNotification === 'function') {
                    showNotification(data.error || 'Kotak Neo authentication failed', 'error');
                }
            }

        } catch (error) {
            console.error('Error during Kotak login:', error);
            if (statusDiv) {
                statusDiv.style.background = '#f8d7da';
                statusDiv.style.color = '#721c24';
                if (error.name === 'AbortError') {
                    statusDiv.textContent = '✗ Request timeout - Kotak API not responding. Please try again.';
                } else {
                    statusDiv.textContent = `✗ Error: ${error.message}`;
                }
            }

            if (typeof showNotification === 'function') {
                const msg = error.name === 'AbortError' ? 'Request timeout - please try again' : 'Network error during authentication';
                showNotification(msg, 'error');
            }
        }
    };

    /**
     * Show modal to collect Dhan Access Token and authenticate
     * @param {string} loginUrl - The URL to post authentication data to (default: /auth/login/dhan)
     */
    window.showDhanLoginModal = function (loginUrl = '/auth/login/dhan') {
        // Create modal HTML
        const modalHtml = `
        <div id="dhanLoginModal" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;">
            <div style="background: white; padding: 30px; border-radius: 8px; max-width: 500px; width: 90%; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                <h2 style="margin-top: 0; color: #333;">Dhan Authentication</h2>
                <p style="color: #666; margin-bottom: 20px;">Please enter your Dhan Access Token to authenticate.</p>
                
                <div style="margin-bottom: 15px;">
                    <label style="display: block; margin-bottom: 5px; color: #333; font-weight: bold;">Access Token *</label>
                    <textarea id="dhanAccessToken" placeholder="Enter your JWT access token from web.dhan.co" 
                              rows="4"
                              onkeydown="if(event.key==='Enter'){event.preventDefault();submitDhanLogin();}"
                              style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; font-family: monospace; resize: vertical;"></textarea>
                    <small style="color: #666; display: block; margin-top: 5px;">
                        Get from: <strong>web.dhan.co → My Profile → Access DhanHQ APIs → Generate Access Token</strong><br>
                        Token is valid for 24 hours and can be renewed.
                    </small>
                </div>
                
                <div style="display: flex; gap: 10px; justify-content: flex-end; margin-top: 25px;">
                    <button onclick="closeDhanLoginModal()" 
                            style="padding: 10px 20px; border: 1px solid #ddd; background: white; color: #333; border-radius: 4px; cursor: pointer; font-size: 14px;">
                        Cancel
                    </button>
                    <button onclick="submitDhanLogin()" 
                            style="padding: 10px 20px; border: none; background: #007bff; color: white; border-radius: 4px; cursor: pointer; font-size: 14px;">
                        Authenticate
                    </button>
                </div>
                
                <div id="dhanLoginStatus" style="margin-top: 15px; padding: 10px; border-radius: 4px; display: none;"></div>
            </div>
        </div>
    `;

        // Remove existing modal if present
        const existingModal = document.getElementById('dhanLoginModal');
        if (existingModal) {
            existingModal.remove();
        }

        // Add modal to page
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Store login URL for submit function
        window.dhanLoginUrl = loginUrl;

        // Focus on access token input
        setTimeout(() => {
            document.getElementById('dhanAccessToken')?.focus();
        }, 100);
    };

    /**
     * Close the Dhan login modal
     */
    window.closeDhanLoginModal = function () {
        const modal = document.getElementById('dhanLoginModal');
        if (modal) {
            modal.remove();
        }
    };

    /**
     * Submit Dhan authentication with access token
     */
    window.submitDhanLogin = async function () {
        const accessToken = document.getElementById('dhanAccessToken')?.value?.trim();
        const statusDiv = document.getElementById('dhanLoginStatus');

        if (!accessToken) {
            if (statusDiv) {
                statusDiv.style.display = 'block';
                statusDiv.style.background = '#fff3cd';
                statusDiv.style.color = '#856404';
                statusDiv.textContent = 'Please enter your access token';
            }
            return;
        }

        // Show loading status
        if (statusDiv) {
            statusDiv.style.display = 'block';
            statusDiv.style.background = '#d1ecf1';
            statusDiv.style.color = '#0c5460';
            statusDiv.textContent = 'Authenticating with Dhan...';
        }

        try {
            const requestBody = {
                access_token: accessToken
            };

            const response = await fetch(window.dhanLoginUrl || '/auth/login/dhan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            const data = await response.json();

            if (data.success) {
                // Success
                if (statusDiv) {
                    statusDiv.style.background = '#d4edda';
                    statusDiv.style.color = '#155724';
                    statusDiv.textContent = `✓ Authentication successful! Client ID: ${data.client_id || 'N/A'}`;
                }

                if (typeof showNotification === 'function') {
                    showNotification('Successfully authenticated with Dhan!', 'success');
                }

                // Close modal after 1.5 seconds
                setTimeout(() => {
                    closeDhanLoginModal();
                }, 1500);

            } else {
                // Error
                if (statusDiv) {
                    statusDiv.style.background = '#f8d7da';
                    statusDiv.style.color = '#721c24';
                    statusDiv.textContent = `✗ ${data.error || data.message || 'Authentication failed'}`;
                }

                if (typeof showNotification === 'function') {
                    showNotification(data.error || 'Dhan authentication failed', 'error');
                }
            }

        } catch (error) {
            console.error('Error during Dhan login:', error);
            if (statusDiv) {
                statusDiv.style.background = '#f8d7da';
                statusDiv.style.color = '#721c24';
                statusDiv.textContent = `✗ Error: ${error.message}`;
            }

            if (typeof showNotification === 'function') {
                showNotification('Network error during authentication', 'error');
            }
        }
    };

    /**
     * Show Fyers login - redirects to OAuth (like Kite)
     * Works exactly like Kite: click button → redirect to Fyers OAuth → login → redirect back
     */
    window.showFyersLoginModal = function () {
        console.log('[Fyers Login] Initiating OAuth flow...');

        // Show loading indicator
        if (typeof showNotification === 'function') {
            showNotification('Redirecting to Fyers login...', 'info');
        }

        // Redirect to Fyers OAuth endpoint (like Kite)
        // The backend will handle the OAuth URL generation and redirect
        window.location.href = '/auth/login/fyers';
    };

    /**
     * Close the Fyers login modal (not used in simplified flow)
     */
    window.closeFyersLoginModal = function () {
        // No-op in simplified flow
    };

    /**
     * Submit Fyers authentication (not used in simplified flow)
     */
    window.submitFyersLogin = function () {
        // Redirect to OAuth instead
        window.showFyersLoginModal();
    };

    // ===================== DYNAMIC CUSTOM TOOLTIP SYSTEM =====================
    document.addEventListener('DOMContentLoaded', () => {
        // Create custom tooltip element if it doesn't exist
        let tooltipEl = document.getElementById('global-custom-tooltip');
        if (!tooltipEl) {
            tooltipEl = document.createElement('div');
            tooltipEl.id = 'global-custom-tooltip';
            tooltipEl.className = 'custom-tooltip-el';
            document.body.appendChild(tooltipEl);
        }

        let activeTarget = null;
        let showTimeout = null;

        // Mouseenter event delegation using capture phase for efficiency
        document.addEventListener('mouseenter', (e) => {
            const target = e.target.closest?.('[title], [data-tooltip]');
            if (!target) return;

            // If target has standard title, convert to data-tooltip to prevent double tooltips
            if (target.hasAttribute('title')) {
                const titleVal = target.getAttribute('title');
                if (titleVal) {
                    target.setAttribute('data-tooltip', titleVal);
                    target.removeAttribute('title');
                }
            }

            const tooltipText = target.getAttribute('data-tooltip');
            if (!tooltipText) return;

            // Clear any active hide/show timers
            if (showTimeout) clearTimeout(showTimeout);

            activeTarget = target;

            // Small 80ms delay for premium micro-animation feel
            showTimeout = setTimeout(() => {
                tooltipEl.textContent = tooltipText;
                tooltipEl.classList.add('show');
                positionTooltip(target, tooltipEl);
            }, 80);
        }, true);

        // Hide tooltip functions
        const hideTooltip = () => {
            if (showTimeout) clearTimeout(showTimeout);
            if (tooltipEl) {
                tooltipEl.classList.remove('show');
            }
            activeTarget = null;
        };

        document.addEventListener('mouseleave', (e) => {
            if (activeTarget && (e.target === activeTarget || !activeTarget.contains(e.target))) {
                hideTooltip();
            }
        }, true);

        document.addEventListener('mousedown', hideTooltip, true);
        document.addEventListener('click', hideTooltip, true);
        window.addEventListener('scroll', hideTooltip, true);

        // Calculate best coordinates to keep tooltips on-screen
        function positionTooltip(target, tooltip) {
            const rect = target.getBoundingClientRect();
            const tooltipRect = tooltip.getBoundingClientRect();
            
            // Center above by default
            let top = rect.top - tooltipRect.height - 6;
            let left = rect.left + (rect.width - tooltipRect.width) / 2;

            // Overflow checks
            // 1. Top boundary check (if it goes off screen, place it below target)
            if (top < 6) {
                top = rect.bottom + 6;
            }

            // 2. Left side overflow
            if (left < 6) {
                left = 6;
            }

            // 3. Right side overflow
            const viewportWidth = window.innerWidth;
            if (left + tooltipRect.width > viewportWidth - 6) {
                left = viewportWidth - tooltipRect.width - 6;
            }

            tooltip.style.top = `${top}px`;
            tooltip.style.left = `${left}px`;
        }
    });

})();