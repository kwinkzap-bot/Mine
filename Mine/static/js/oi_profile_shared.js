/**
 * Shared by the two option-order toolbars — Opt Prem (oi_profile.js) and Round
 * Strike (oi_profile_round_strike.js) — and loaded before both.
 *
 * These used to live in oi_profile.js, which Round Strike could rely on because
 * the only page carrying that block also carried oi_profile.js. Replay now
 * carries the Round Strike block too (see templates/oi_replay.html), and there
 * oi_profile.js cannot be loaded at all: it and oi_replay.js declare the same
 * top-level `let`s (oipOIChart, oipSymbol, …), so a page with both throws
 * "Identifier has already been declared" and neither file runs.
 *
 * So the helpers Round Strike reaches for moved here rather than being copied:
 * the stop-order path in particular is live-money code that must have exactly
 * one definition. The two header renderers read the DOM directly instead of
 * either page's oipElems cache, which is the only behavioural change — same
 * elements, looked up by id.
 */

'use strict';

/** IST session clock — Round Strike's poll drops from 1s to 5min outside it. */
function oipIsMarketOpen() {
    const n = new Date(); if (n.getDay() === 0 || n.getDay() === 6) return false;
    const ist = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(n);
    const [h, m] = ist.split(':').map(Number); const mins = h * 60 + m;
    // 9:15 AM (555 mins) to 3:30 PM (930 mins)
    return mins >= 555 && mins <= 930;
}

/** Lakh/crore short form for the OI pills — "+1.25 L", "-2.30 Cr". */
function fmtL(n) {
    if (n == null) return '--';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '+';
    if (abs >= 10000000) return sign + (abs / 10000000).toFixed(2) + ' Cr';
    if (abs >= 100000) return sign + (abs / 100000).toFixed(2) + ' L';
    return n.toLocaleString('en-IN');
}

// CPR-width card: { index: {pp,bc,tc,width_pct,type}, future: {...}, future_symbol } — refetched
// once per symbol switch (previous-day OHLC doesn't change intraday, so no poll needed).
let oipCprData = null;
// Which DAY the CPR card is showing, not which instrument. Both views are the
// INDEX: 'today' is the CPR in force now (from the last settled session) and
// 'next' is the one that will be in force tomorrow (from today's forming bar).
// Clicking the card flips between them.
let oipCprDay = 'today';

/**
 * Why a stop entry needs a direction check the other order types don't.
 *
 * A stop rests inactive until the price TOUCHES its trigger, then goes to
 * market. Which means a BUY stop only waits if its trigger is ABOVE the current
 * premium, and a SELL stop only waits if its trigger is BELOW it. Get it the
 * wrong way round and the trigger is already satisfied the moment the order
 * reaches the exchange: it fires immediately, at market, for the full position
 * size — the exact thing the user was trying to avoid by not pressing MARKET.
 *
 * The broker will not refuse it (a triggered stop is a legitimate order), so
 * this is the only place it can be caught. Returns an error string, or null.
 *
 * lastPrice may legitimately be unknown — the chart feed can be empty on a
 * fresh load or after a data-provider hiccup. Unknown means no check: refusing
 * to place because we cannot see a price would be worse than placing, and the
 * broker still enforces its own tick and range rules.
 */
function oipStopDirectionError(action, trigger, lastPrice) {
    if (!(lastPrice > 0)) return null;
    if (action === 'BUY' && trigger <= lastPrice) {
        return `A BUY stop must sit ABOVE the market — ₹${trigger} is at or below the last price of ₹${lastPrice}, `
             + `so it would trigger instantly. Use MKT to buy now, or raise the trigger.`;
    }
    if (action === 'SELL' && trigger >= lastPrice) {
        return `A SELL stop must sit BELOW the market — ₹${trigger} is at or above the last price of ₹${lastPrice}, `
             + `so it would trigger instantly. Use MKT to sell now, or lower the trigger.`;
    }
    return null;
}

/**
 * Place one resting stop order at every broker enabled for this panel.
 *
 * Shared by both order toolbars (Opt Prem and Round Strike) — same endpoint the
 * SL CE / SL PE buttons use, which now carries a side. The order rests at the
 * exchange rather than in this app, so it still triggers with the browser shut
 * and the Flask process down.
 *
 * Returns the parsed response so the caller can word its own notification.
 */
async function oipPlaceStopOrder({ symbol, strike, side, action, trigger }) {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
    const res = await fetch('/api/order/place-sl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({
            symbol: symbol, strike: strike, option_type: side,
            trigger_price: trigger, action: action
        })
    });
    return res.json();
}

/** Per-broker failure lines out of a /api/order/place-sl response. */
function oipStopErrorText(r) {
    const legs = (r?.results || []).filter(b => !b.success)
        .map(b => `${b.broker || '?'}${b.instance ? ' ' + b.instance : ''}: ${b.error || b.message || 'Unknown error'}`);
    return legs.length ? legs.join(', ') : (r?.error || r?.message || 'Unknown error');
}

function oipUpdateIVPGauge(val) {
    const bar = document.getElementById('ivpGaugeBar');
    if (!bar) return;
    bar.style.width = val + '%';
    bar.classList.remove('cheap', 'neutral', 'expensive');
    if (val < 30) bar.classList.add('cheap');
    else if (val < 70) bar.classList.add('neutral');
    else bar.classList.add('expensive');
}

function oipRenderCprCard() {
    const hdrCpr = document.getElementById('hdrCpr');
    const hdrCprSrc = document.getElementById('hdrCprSrc');
    const hdrCprCard = document.getElementById('hdrCprCard');
    if (!hdrCpr) return;
    const isNext = oipCprDay === 'next';
    const band = oipCprData ? (isNext ? oipCprData.index_next : oipCprData.index) : null;
    // Always IDX — both views are the index, they differ only by day.
    if (hdrCprSrc) hdrCprSrc.textContent = isNext ? 'IDX · NEXT' : 'IDX · TODAY';
    if (!band) {
        hdrCpr.textContent = '--';
        hdrCpr.className = 'oip-hdr-val';
        hdrCprCard?.setAttribute('title',
            isNext
                // Before the open, and on a holiday, there is no forming bar to
                // build tomorrow's CPR from yet.
                ? "Next-day CPR needs today's daily bar, which doesn't exist until the session opens — click to go back to today"
                : 'CPR day-range type (Narrow/Medium/Wide) — click to toggle today vs next day');
        return;
    }
    hdrCpr.textContent = band.type;
    const colorClass = band.type === 'Narrow' ? 'grn' : (band.type === 'Wide' ? 'red' : 'amber');
    hdrCpr.className = 'oip-hdr-val ' + colorClass;
    // Spell out WHY it says what it says. The label is relative to this
    // instrument's own recent CPR widths, so the bare width % on its own never
    // explained the verdict — 0.30% is wide for an index and narrow for a
    // volatile midcap.
    const scale = band.width_ratio
        ? `width ${band.width_pct}% = ${band.width_ratio}x its ${band.history_days}-day average of ${band.avg_width_pct}%`
        : `width ${band.width_pct}% (absolute scale — only ${band.history_days || 0} days of history)`;
    hdrCprCard?.setAttribute('title',
        `Index CPR — ${isNext ? 'NEXT DAY' : 'TODAY'}: PP ${band.pp} / BC ${band.bc} / TC ${band.tc} — ${scale}`
        + (isNext
            // Say it plainly: this one is not settled. It is derived from a bar
            // that is still moving, so it can change until the close.
            ? " — provisional: built from today's still-forming bar, so it moves"
              + ' until the close. Click to go back to today.'
            : ' — click for next day')); 
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('hdrCprCard')?.addEventListener('click', () => {
        oipCprDay = oipCprDay === 'next' ? 'today' : 'next';
        oipRenderCprCard();
    });
});
