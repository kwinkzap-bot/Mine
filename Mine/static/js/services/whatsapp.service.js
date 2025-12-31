/**
 * whatsapp.service.js
 * Client-side helper to trigger WhatsApp notifications via backend endpoint.
 */
const WhatsAppService = (function() {
    'use strict';

    const API_ENDPOINT = '/api/notify-whatsapp';

    async function send(message, to = '') {
        try {
            const body = { message };
            if (to && to.trim()) {
                body.to = to.trim();
            }

            const response = await window.fetchJson(API_ENDPOINT, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            if (response && response.success) {
                if (window.NotificationService && typeof window.NotificationService.success === 'function') {
                    window.NotificationService.success('WhatsApp message sent');
                }
                return true;
            }

            const errorMsg = response && response.error ? response.error : 'WhatsApp send failed';
            if (window.NotificationService && typeof window.NotificationService.error === 'function') {
                window.NotificationService.error(errorMsg);
            }
            return false;
        } catch (err) {
            console.error('WhatsApp send error:', err);
            if (window.NotificationService && typeof window.NotificationService.error === 'function') {
                window.NotificationService.error(err.message || 'WhatsApp send error');
            }
            return false;
        }
    }

    return {
        send
    };
})();
