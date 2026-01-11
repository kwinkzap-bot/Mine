/**
 * index.js - Pure Vanilla JavaScript for the home page.
 * Handles login, token status checking, and error detection.
 */
document.addEventListener('DOMContentLoaded', function() {
    'use strict';

    const statusMessage = document.getElementById('statusMessage');
    const loginBtn = document.getElementById('loginZerodhaBtn');
    const refreshBtn = document.getElementById('refreshBtn');

    // Setup button event listeners
    if (loginBtn) {
        loginBtn.addEventListener('click', function(e) {
            e.preventDefault();
            window.location.href = '/auth/login';
        });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', function(e) {
            e.preventDefault();
            window.location.reload();
        });
    }

    // Check token status on page load
    checkTokenStatus();

    function checkTokenStatus() {
        if (!statusMessage) return;

        fetch('/debug/status')
            .then(response => {
                if (response.status === 403) {
                    showError('Your session has expired or token is invalid. Please login to continue.');
                    return;
                }
                return response.json();
            })
            .then(data => {
                if (!data) return;
                
                if (data.session_token || data.env_token) {
                    statusMessage.innerHTML = `
                        <div style="color: #28a745; padding: 15px; background: #f0fff4; border-left: 4px solid #28a745; border-radius: 4px;">
                            <strong>✓ Authentication Status:</strong> Token is available
                            <br><small style="color: #666;">You can access all features</small>
                        </div>
                    `;
                } else {
                    statusMessage.innerHTML = `
                        <div style="color: #dc3545; padding: 15px; background: #fff5f5; border-left: 4px solid #dc3545; border-radius: 4px;">
                            <strong>⚠ Not Authenticated:</strong> Please login with Zerodha
                            <br><small style="color: #666;">Click the "Login to Zerodha" button above to get started</small>
                        </div>
                    `;
                }
            })
            .catch(error => {
                console.error('Error checking token status:', error);
                showError('Could not verify authentication status. Please try refreshing.');
            });
    }

    function showError(message) {
        if (!statusMessage) return;
        
        statusMessage.innerHTML = `
            <div style="color: #dc3545; padding: 15px; background: #fff5f5; border-left: 4px solid #dc3545; border-radius: 4px;">
                <strong>❌ ${message}</strong>
                <br><small style="color: #666; margin-top: 5px; display: block;">Click the "Login to Zerodha" button above</small>
            </div>
        `;
    }
});