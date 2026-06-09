'use strict';

// NSE trading holidays (YYYY-MM-DD, IST date).
// Update this list each year from the NSE circular.
const NSE_HOLIDAYS = new Set([
    // 2026
    '2026-01-26', // Republic Day
    '2026-02-26', // Mahashivratri
    '2026-03-20', // Holi
    '2026-04-02', // Ram Navami
    '2026-04-03', // Good Friday
    '2026-04-14', // Dr. Ambedkar Jayanti
    '2026-05-01', // Maharashtra Day
    '2026-06-06', // Eid ul Adha (Bakri Id)
    '2026-07-31', // Muharram
    '2026-08-15', // Independence Day
    '2026-08-28', // Ganesh Chaturthi
    '2026-10-02', // Gandhi Jayanti
    '2026-10-22', // Dussehra
    '2026-11-11', // Diwali Laxmi Puja
    '2026-11-12', // Diwali Balipratipada
    '2026-11-14', // Gurunanak Jayanti
    '2026-12-25', // Christmas
    // 2025
    '2025-01-26', // Republic Day
    '2025-03-14', // Holi
    '2025-04-10', // Ram Navami
    '2025-04-14', // Dr. Ambedkar Jayanti
    '2025-04-18', // Good Friday
    '2025-05-01', // Maharashtra Day
    '2025-08-15', // Independence Day
    '2025-08-27', // Ganesh Chaturthi
    '2025-10-02', // Gandhi Jayanti / Mahalaya
    '2025-10-02', // Gandhi Jayanti
    '2025-10-20', // Diwali Laxmi Puja
    '2025-10-21', // Diwali Balipratipada
    '2025-11-05', // Gurunanak Jayanti
    '2025-12-25', // Christmas
]);

/**
 * Returns true if the current time falls within NSE market hours:
 * Monday–Friday, 09:15–15:30 IST, excluding NSE holidays.
 * Uses Intl.DateTimeFormat with Asia/Kolkata to be correct regardless
 * of the browser's local timezone.
 */
function isMarketOpen() {
    const now = new Date();
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Kolkata',
        weekday: 'short',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: 'numeric',
        minute: 'numeric',
        hour12: false,
    }).formatToParts(now);
    const get = type => parts.find(p => p.type === type)?.value;
    const day = get('weekday');
    if (day === 'Sat' || day === 'Sun') return false;
    // Build YYYY-MM-DD from the IST date parts
    const dateStr = get('year') + '-' + get('month') + '-' + get('day');
    if (NSE_HOLIDAYS.has(dateStr)) return false;
    const mins = parseInt(get('hour'), 10) * 60 + parseInt(get('minute'), 10);
    return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30;
}
