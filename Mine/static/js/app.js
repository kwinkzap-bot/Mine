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

/**
 * Show modal to collect Kotak Neo TOTP secret and authenticate
 * @param {string} loginUrl - The URL to post authentication data to (default: /auth/login/kotak)
 */
window.showKotakLoginModal = function(loginUrl = '/auth/login/kotak') {
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
window.closeKotakLoginModal = function() {
    const modal = document.getElementById('kotakLoginModal');
    if (modal) {
        modal.remove();
    }
};

/**
 * Submit Kotak Neo authentication with TOTP secret
 */
window.submitKotakLogin = async function() {
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
window.showDhanLoginModal = function(loginUrl = '/auth/login/dhan') {
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
window.closeDhanLoginModal = function() {
    const modal = document.getElementById('dhanLoginModal');
    if (modal) {
        modal.remove();
    }
};

/**
 * Submit Dhan authentication with access token
 */
window.submitDhanLogin = async function() {
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
 * Show modal to login to Fyers (OAuth or Direct Token)
 * @param {string} loginUrl - The URL to post authentication data to (default: /auth/login/fyers)
 */
window.showFyersLoginModal = function(loginUrl = '/auth/login/fyers') {
    // Create modal HTML with OAuth and direct token options
    const modalHtml = `
        <div id="fyersLoginModal" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 10000;">
            <div style="background: white; padding: 30px; border-radius: 8px; max-width: 550px; width: 90%; box-shadow: 0 4px 6px rgba(0,0,0,0.1); max-height: 90vh; overflow-y: auto;">
                <h2 style="margin-top: 0; color: #333;">Fyers Authentication</h2>
                
                <!-- OAuth Flow Tab -->
                <div id="fyersOAuthTab" style="display: none;">
                    <p style="color: #666; margin-bottom: 20px;">
                        <strong>OAuth Method (Recommended):</strong> More secure - you authorize in Fyers app
                    </p>
                    
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 4px; margin-bottom: 15px;">
                        <p style="margin: 0 0 10px 0; color: #333; font-weight: bold;">How it works:</p>
                        <ol style="margin: 0; padding-left: 20px; color: #666; font-size: 14px;">
                            <li>Click "Open Fyers Login" button below</li>
                            <li>A new window opens with Fyers login</li>
                            <li>Enter your Fyers credentials and authorize</li>
                            <li>You'll be automatically authenticated</li>
                        </ol>
                    </div>
                    
                    <button onclick="initiateFyersOAuth()" 
                            style="width: 100%; padding: 12px; margin-bottom: 15px; border: none; background: #007bff; color: white; border-radius: 4px; cursor: pointer; font-size: 16px; font-weight: bold;">
                        🔒 Open Fyers Login
                    </button>
                    
                    <p style="text-align: center; color: #999; margin: 20px 0;">Or use direct token:</p>
                </div>
                
                <!-- Direct Token Tab -->
                <div id="fyersTokenTab">
                    <p style="color: #666; margin-bottom: 20px;">
                        <strong>Direct Token Method:</strong> Enter your access token directly
                    </p>
                    
                    <div style="margin-bottom: 15px;">
                        <label style="display: block; margin-bottom: 5px; color: #333; font-weight: bold;">Access Token *</label>
                        <textarea id="fyersAccessToken" placeholder="Enter your access token (format: APPID:token or just token)" 
                                  rows="4"
                                  style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; font-family: monospace; resize: vertical; box-sizing: border-box;"></textarea>
                        <small style="color: #666; display: block; margin-top: 5px;">
                            Get from: <strong>https://myapi.fyers.in/dashboard/</strong> → Your Profile → Generate Token
                        </small>
                    </div>
                </div>
                
                <div style="display: flex; gap: 10px; justify-content: flex-end; margin-top: 25px;">
                    <button onclick="closeFyersLoginModal()" 
                            style="padding: 10px 20px; border: 1px solid #ddd; background: white; color: #333; border-radius: 4px; cursor: pointer; font-size: 14px;">
                        Cancel
                    </button>
                    <button onclick="submitFyersLogin()" 
                            style="padding: 10px 20px; border: none; background: #28a745; color: white; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">
                        Authenticate
                    </button>
                </div>
                
                <div id="fyersLoginStatus" style="margin-top: 15px; padding: 10px; border-radius: 4px; display: none;"></div>
            </div>
        </div>
    `;
    
    // Remove existing modal if present
    const existingModal = document.getElementById('fyersLoginModal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // Add modal to page
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Store login URL for submit function
    window.fyersLoginUrl = loginUrl;
    
    // Show OAuth tab by default
    document.getElementById('fyersOAuthTab').style.display = 'block';
    document.getElementById('fyersTokenTab').style.display = 'block';
    
    // Focus on access token input
    setTimeout(() => {
        document.getElementById('fyersAccessToken')?.focus();
    }, 100);
    
    // Setup message listener for OAuth callback
    window.addEventListener('message', window.handleFyersOAuthMessage);
};

/**
 * Handle message from Fyers OAuth callback window
 */
window.handleFyersOAuthMessage = function(event) {
    if (event.data && event.data.type === 'fyers_auth_success') {
        console.log('[Fyers OAuth] Authentication successful:', event.data.message);
        
        const statusDiv = document.getElementById('fyersLoginStatus');
        if (statusDiv) {
            statusDiv.style.display = 'block';
            statusDiv.style.background = '#d4edda';
            statusDiv.style.color = '#155724';
            statusDiv.textContent = '✓ Authentication successful!';
        }
        
        if (typeof showNotification === 'function') {
            showNotification('Successfully authenticated with Fyers!', 'success');
        }
        
        // Close modal after 1.5 seconds
        setTimeout(() => {
            closeFyersLoginModal();
            location.reload();
        }, 1500);
    }
};

/**
 * Initiate Fyers OAuth flow
 */
window.initiateFyersOAuth = async function() {
    const statusDiv = document.getElementById('fyersLoginStatus');
    
    // Show loading status
    if (statusDiv) {
        statusDiv.style.display = 'block';
        statusDiv.style.background = '#d1ecf1';
        statusDiv.style.color = '#0c5460';
        statusDiv.textContent = 'Getting OAuth URL from server...';
    }
    
    try {
        // Request OAuth URL from backend
        const response = await fetch(window.fyersLoginUrl || '/auth/login/fyers', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });
        
        const data = await response.json();
        
        if (data.success && data.auth_url) {
            // Open Fyers login in new window
            const oauthWindow = window.open(data.auth_url, 'fyers_oauth', 'width=600,height=700,toolbar=no,location=no,status=no,menubar=no');
            
            if (!oauthWindow) {
                if (statusDiv) {
                    statusDiv.style.background = '#f8d7da';
                    statusDiv.style.color = '#721c24';
                    statusDiv.textContent = '✗ Failed to open OAuth window. Please check browser popup settings.';
                }
                return;
            }
            
            if (statusDiv) {
                statusDiv.style.display = 'block';
                statusDiv.style.background = '#d1ecf1';
                statusDiv.style.color = '#0c5460';
                statusDiv.textContent = 'Opening Fyers login... Please complete authorization in the new window.';
            }
            
        } else {
            if (statusDiv) {
                statusDiv.style.background = '#f8d7da';
                statusDiv.style.color = '#721c24';
                statusDiv.textContent = `✗ ${data.error || 'Failed to get OAuth URL'}`;
            }
        }
        
    } catch (error) {
        console.error('Error initiating Fyers OAuth:', error);
        if (statusDiv) {
            statusDiv.style.background = '#f8d7da';
            statusDiv.style.color = '#721c24';
            statusDiv.textContent = `✗ Error: ${error.message}`;
        }
    }
};

/**
 * Close the Fyers login modal
 */
window.closeFyersLoginModal = function() {
    const modal = document.getElementById('fyersLoginModal');
    if (modal) {
        modal.remove();
    }
    window.removeEventListener('message', window.handleFyersOAuthMessage);
};

/**
 * Submit Fyers authentication with direct access token
 */
window.submitFyersLogin = async function() {
    const accessToken = document.getElementById('fyersAccessToken')?.value?.trim();
    const statusDiv = document.getElementById('fyersLoginStatus');
    
    if (!accessToken) {
        if (statusDiv) {
            statusDiv.style.display = 'block';
            statusDiv.style.background = '#fff3cd';
            statusDiv.style.color = '#856404';
            statusDiv.textContent = 'Please enter your access token or use OAuth login';
        }
        return;
    }
    
    // Show loading status
    if (statusDiv) {
        statusDiv.style.display = 'block';
        statusDiv.style.background = '#d1ecf1';
        statusDiv.style.color = '#0c5460';
        statusDiv.textContent = 'Authenticating with Fyers...';
    }
    
    try {
        const requestBody = {
            access_token: accessToken
        };
        
        const response = await fetch(window.fyersLoginUrl || '/auth/login/fyers', {
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
                statusDiv.textContent = '✓ Authentication successful!';
            }
            
            if (typeof showNotification === 'function') {
                showNotification('Successfully authenticated with Fyers!', 'success');
            }
            
            // Close modal after 1.5 seconds
            setTimeout(() => {
                closeFyersLoginModal();
                location.reload();
            }, 1500);
            
        } else {
            // Error
            if (statusDiv) {
                statusDiv.style.background = '#f8d7da';
                statusDiv.style.color = '#721c24';
                statusDiv.textContent = `✗ ${data.error || data.message || 'Authentication failed'}`;
            }
            
            if (typeof showNotification === 'function') {
                showNotification(data.error || 'Fyers authentication failed', 'error');
            }
        }
        
    } catch (error) {
        console.error('Error during Fyers login:', error);
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

})();