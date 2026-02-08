/**
 * index.js - Pure Vanilla JavaScript for the home page.
 * Handles broker loading and success notifications.
 */
document.addEventListener('DOMContentLoaded', function() {
    'use strict';

    const refreshBtn = document.getElementById('refreshBtn');
    const successNotification = document.getElementById('successNotification');

    // Check if login was successful
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('login_success') === 'True') {
        // Show success notification
        if (successNotification) {
            successNotification.style.display = 'block';
            
            // Auto-hide after 5 seconds
            setTimeout(() => {
                successNotification.style.opacity = '0';
                successNotification.style.transition = 'opacity 0.3s ease-out';
                setTimeout(() => {
                    successNotification.style.display = 'none';
                }, 300);
            }, 5000);
        }
        
        // Remove the query parameter from URL for cleanliness
        const cleanUrl = window.location.pathname;
        window.history.replaceState({}, document.title, cleanUrl);
    }

    // Setup button event listeners
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function(e) {
            e.preventDefault();
            window.location.reload();
        });
    }
});