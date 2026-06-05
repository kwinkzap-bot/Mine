'use strict';

/**
 * Returns true if the current time falls within NSE market hours:
 * Monday–Friday, 09:15–15:30 IST.
 * Uses Intl.DateTimeFormat with Asia/Kolkata to be correct regardless
 * of the browser's local timezone.
 */
function isMarketOpen() {
    const now = new Date();
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Kolkata',
        weekday: 'short',
        hour: 'numeric',
        minute: 'numeric',
        hour12: false,
    }).formatToParts(now);
    const get = type => parts.find(p => p.type === type)?.value;
    const day = get('weekday');
    if (day === 'Sat' || day === 'Sun') return false;
    const mins = parseInt(get('hour'), 10) * 60 + parseInt(get('minute'), 10);
    return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30;
}
