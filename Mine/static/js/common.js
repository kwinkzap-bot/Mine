'use strict';

// NSE trading holidays (YYYY-MM-DD, IST date).
// Update this list each year from the NSE circular — and keep it in step with
// NSE_HOLIDAYS in src/trading_app/app/utils/trading_calendar.py, which the
// backend uses to count trading sessions. tests/test_trading_calendar.py
// asserts the two lists are identical.
const NSE_HOLIDAYS = new Set([
    // 2026 — NSE/CMTR/71775 (12 Dec 2025) + addendum NSE/CMTR/72260 (12 Jan 2026)
    '2026-01-15', // Municipal Corporation Election — Maharashtra
    '2026-01-26', // Republic Day
    '2026-03-03', // Holi
    '2026-03-26', // Shri Ram Navami
    '2026-03-31', // Shri Mahavir Jayanti
    '2026-04-03', // Good Friday
    '2026-04-14', // Dr. Baba Saheb Ambedkar Jayanti
    '2026-05-01', // Maharashtra Day
    '2026-05-28', // Bakri Id
    '2026-06-26', // Muharram
    '2026-09-14', // Ganesh Chaturthi
    '2026-10-02', // Mahatma Gandhi Jayanti
    '2026-10-20', // Dussehra
    '2026-11-10', // Diwali Balipratipada
    '2026-11-24', // Prakash Gurpurb Sri Guru Nanak Dev
    '2026-12-25', // Christmas
    // 2026-11-08 (Diwali Laxmi Pujan) is a SUNDAY carrying only the Muhurat
    // session — the weekend rule already shuts it, so it is not listed here.
    // 2025
    '2025-01-26', // Republic Day (Sunday)
    '2025-02-26', // Mahashivratri
    '2025-03-14', // Holi
    '2025-03-31', // Id-ul-Fitr (Ramzan Id)
    '2025-04-06', // Shri Ram Navami (Sunday)
    '2025-04-10', // Shri Mahavir Jayanti
    '2025-04-14', // Dr. Baba Saheb Ambedkar Jayanti
    '2025-04-18', // Good Friday
    '2025-05-01', // Maharashtra Day
    '2025-06-07', // Bakri Id (Saturday)
    '2025-07-06', // Muharram (Sunday)
    '2025-08-15', // Independence Day
    '2025-08-27', // Ganesh Chaturthi
    '2025-10-02', // Dussehra / Mahatma Gandhi Jayanti
    '2025-10-21', // Diwali Laxmi Pujan (Muhurat session only)
    '2025-10-22', // Diwali Balipratipada
    '2025-11-05', // Prakash Gurpurb Sri Guru Nanak Dev
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
