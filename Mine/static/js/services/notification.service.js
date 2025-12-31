/**
 * notification.service.js
 * Thin wrapper around the global showNotification utility to keep a single service entry point.
 */
const NotificationService = (function() {
    'use strict';

    function notify(message, type = 'info') {
        if (typeof window.showNotification === 'function') {
            window.showNotification(message, type);
        } else {
            console.warn('showNotification is not available');
        }
    }

    function success(message) {
        notify(message, 'success');
    }

    function error(message) {
        notify(message, 'error');
    }

    function warning(message) {
        notify(message, 'warning');
    }

    function info(message) {
        notify(message, 'info');
    }

    return {
        notify,
        success,
        error,
        warning,
        info
    };
})();

// Expose on window for other modules
window.NotificationService = NotificationService;
