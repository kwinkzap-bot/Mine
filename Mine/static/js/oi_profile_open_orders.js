/**
 * OI Profile — Open Orders strip.
 *
 * An order fired from this page goes to EVERY broker enabled for the intrinsic
 * strategy, so a LIMIT order is one row here and N live orders at the brokers.
 * Editing the price or cancelling from this strip fans back out the same way:
 * /api/orders/<id>/price and DELETE /api/orders/<id> both push the change to
 * every broker leg and report each broker's answer.
 *
 * Renders into every .oip-open-orders container on the page (the Opt Prem bar
 * and the Round Strike bar each have one) — same data, same handlers.
 */

const OIP_OO_POLL_MS = 5000;

// Statuses that are still unfilled and therefore still editable. Must match
// MineOrderStore.EDITABLE_STATUSES on the backend.
const OIP_OO_EDITABLE = ['OPEN', 'PENDING', 'EXECUTING'];

let _oipOOLastSig = null;

function _oipOOCsrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function _oipOONotify(msg, tone) {
    if (typeof showNotification === 'function') showNotification(msg, tone);
}

/** "• ZERODHA 1: OK" per broker leg — what actually happened, per broker. */
function _oipOOBrokerLines(r) {
    if (!Array.isArray(r?.summary) || !r.summary.length) return '';
    return '\n' + r.summary.map(s => {
        const name = String(s.broker || '').replace(/_/g, ' ').toUpperCase();
        return `• ${name}: ${s.result?.success ? 'OK' : (s.result?.error || 'Failed')}`;
    }).join('\n');
}

/** Count of broker legs that actually reached a broker (i.e. carry an order id). */
function _oipOOLegCount(o) {
    return (o.broker_order_ids || []).filter(b => b?.order_id || b?.result?.order_id).length;
}

/**
 * sync=1 makes the server reconcile today's resting orders against the broker
 * order books before answering. Without it the list is only what we *believe*
 * is open: an order the broker filled minutes ago still arrives as OPEN, still
 * draws its ✓ and ×, and both of those can only fail. The server throttles the
 * sweep and skips it when nothing is resting, so this poll stays cheap.
 */
async function oipOOFetch() {
    try {
        const res = await fetch('/api/orders?history=0&sync=1');
        if (!res.ok) return [];
        const data = await res.json();
        return (data.orders || []).filter(o => OIP_OO_EDITABLE.includes(o.status));
    } catch (_) { return []; }
}

const _oipOOEsc = s => String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/**
 * Orders bucketed by the contract they are working, newest bucket first.
 *
 * A five-rung exit ladder is five orders on ONE contract, and printing that
 * contract's name five times is most of the strip's width spent saying the same
 * thing. The name is hoisted to the group instead, and each order is reduced to
 * the two things that differ: its side and its price.
 */
function _oipOOGroup(orders) {
    const symbols = new Set(orders.map(o => o.symbol));
    const groups = new Map();

    for (const o of orders) {
        const key = `${o.symbol}|${o.strike}|${o.option_type}`;
        if (!groups.has(key)) {
            groups.set(key, {
                // The symbol only earns space when more than one is in play —
                // on the usual all-NIFTY day it is noise.
                label: (symbols.size > 1 ? `${o.symbol} ` : '') + `${o.strike}${o.option_type}`,
                title: `${o.symbol} ${o.strike} ${o.option_type}`,
                orders: [],
            });
        }
        groups.get(key).orders.push(o);
    }
    return [...groups.values()];
}

function _oipOOPill(o) {
    const legs = _oipOOLegCount(o);
    const where = o.mode === 'mine' ? 'app' : `${legs} broker${legs === 1 ? '' : 's'}`;
    const isBuy = o.action === 'BUY';
    return `
        <span class="oip-oo-pill ${isBuy ? 'buy' : 'sell'}" data-id="${_oipOOEsc(o.id)}">
            <span class="oip-oo-side" title="${_oipOOEsc(o.action)} — live at ${_oipOOEsc(where)}">${isBuy ? 'B' : 'S'}</span>
            <input type="number" class="oip-oo-price" step="0.05" min="0" value="${_oipOOEsc(o.price || 0)}"
                title="New limit price — applied to all ${_oipOOEsc(where)}">
            <button class="oip-oo-btn save" title="Update price at all brokers">&#10003;</button>
            <button class="oip-oo-btn cancel" title="Cancel this order at all brokers">&times;</button>
        </span>`;
}

async function oipOORender() {
    const hosts = document.querySelectorAll('.oip-open-orders');
    if (!hosts.length) return;

    const orders = await oipOOFetch();
    orders.sort((a, b) => b.created_at - a.created_at);

    // Re-rendering under a half-typed price would throw the edit away, and an
    // unchanged list has nothing to redraw anyway.
    const sig = JSON.stringify(orders.map(o => [o.id, o.status, o.price]));
    const typing = document.activeElement?.classList?.contains('oip-oo-price');
    if (sig === _oipOOLastSig || typing) return;
    _oipOOLastSig = sig;

    const groups = _oipOOGroup(orders);
    // The header owns its own full-width line so that Cancel all sits beside the
    // count no matter how many pills follow. Left in the flow, a wrap could
    // strand it alone on a line of its own below fourteen orders.
    const html = !orders.length ? '' : `
        <span class="oip-oo-hd">
            <span class="oip-oo-title">Open Orders</span>
            <span class="oip-oo-count">${orders.length}</span>
            <button class="oip-oo-cancel-all" type="button"
                title="Cancel every resting order, at every broker">Cancel all</button>
        </span>
        ${groups.map(g => `
            <span class="oip-oo-group">
                <span class="oip-oo-inst" title="${_oipOOEsc(g.title)}">${_oipOOEsc(g.label)}</span>
                ${g.orders.map(_oipOOPill).join('')}
            </span>`).join('')}`;

    hosts.forEach(host => {
        host.innerHTML = html;
        host.classList.toggle('is-empty', !orders.length);
    });
}

async function _oipOOSubmit(row, isCancel) {
    const id = row.dataset.id;
    const input = row.querySelector('.oip-oo-price');
    const price = parseFloat(input?.value);

    if (!isCancel && (isNaN(price) || price <= 0)) {
        _oipOONotify('Enter a valid price', 'error');
        return;
    }

    row.querySelectorAll('.oip-oo-btn').forEach(b => { b.disabled = true; });
    try {
        const res = await fetch(isCancel ? `/api/orders/${id}` : `/api/orders/${id}/price`, {
            method: isCancel ? 'DELETE' : 'PUT',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _oipOOCsrf() },
            body: isCancel ? undefined : JSON.stringify({ price })
        });
        const r = await res.json();
        if (r.success) {
            _oipOONotify(isCancel ? `Order cancelled${_oipOOBrokerLines(r)}`
                                  : `Price → ₹${price}${_oipOOBrokerLines(r)}`, 'success');
        } else if (r.gone) {
            // The order finished at the broker while it was still on the strip.
            // The server has already corrected the record, so the redraw below
            // drops the pill — this is a warning, not a failure to retry.
            row.classList.add('oip-oo-pill--gone');
            _oipOONotify(r.error || 'Order is no longer open', 'warning');
        } else {
            _oipOONotify(`${isCancel ? 'Cancel' : 'Update'} failed: ${r.error || 'Unknown error'}${_oipOOBrokerLines(r)}`, 'error');
        }
    } catch (e) {
        _oipOONotify(`${isCancel ? 'Cancel' : 'Update'} error: ${e.message}`, 'error');
    } finally {
        _oipOOLastSig = null;   // force the next render to redraw
        oipOORender();
    }
}

/**
 * Cancel every resting order, one request each, sequentially.
 *
 * Sequential rather than parallel: each cancel fans out to every broker the
 * order went to, and firing six of those at once is the surest way to trip a
 * broker's rate limit and have half of them fail for a reason that has nothing
 * to do with the orders. One summary toast at the end, not six.
 */
async function _oipOOCancelAll(btn) {
    const ids = [...document.querySelectorAll('.oip-open-orders .oip-oo-pill')]
        .map(p => p.dataset.id);
    const unique = [...new Set(ids)];
    if (!unique.length) return;

    if (!confirm(`Cancel all ${unique.length} resting order${unique.length === 1 ? '' : 's'} `
                 + `at every broker? This cannot be undone.`)) return;

    btn.disabled = true;
    btn.textContent = 'Cancelling…';

    let done = 0, gone = 0;
    const failed = [];
    for (const id of unique) {
        try {
            const res = await fetch(`/api/orders/${id}`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': _oipOOCsrf() }
            });
            const r = await res.json();
            if (r.success) done++;
            else if (r.gone) gone++;          // already finished at the broker
            else failed.push(r.error || 'Unknown error');
        } catch (e) {
            failed.push(e.message);
        }
    }

    const parts = [];
    if (done) parts.push(`${done} cancelled`);
    if (gone) parts.push(`${gone} already done at the broker`);
    if (failed.length) parts.push(`${failed.length} failed`);
    _oipOONotify(parts.join(', ') + (failed.length ? `\n• ${failed[0]}` : ''),
                 failed.length ? 'error' : 'success');

    _oipOOLastSig = null;   // force the next render to redraw
    oipOORender();
}

function oipOOInit() {
    const hosts = document.querySelectorAll('.oip-open-orders');
    if (!hosts.length) return;

    hosts.forEach(host => {
        if (host.dataset.wired) return;
        host.dataset.wired = '1';
        host.addEventListener('click', (e) => {
            const all = e.target.closest('.oip-oo-cancel-all');
            if (all) { _oipOOCancelAll(all); return; }

            const row = e.target.closest('.oip-oo-pill');
            if (!row) return;
            if (e.target.closest('.oip-oo-btn.cancel')) _oipOOSubmit(row, true);
            else if (e.target.closest('.oip-oo-btn.save')) _oipOOSubmit(row, false);
        });
        // Enter in the price box = the tick next to it.
        host.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' || !e.target.classList.contains('oip-oo-price')) return;
            e.preventDefault();
            e.target.blur();
            _oipOOSubmit(e.target.closest('.oip-oo-pill'), false);
        });
    });

    oipOORender();
    setInterval(() => { if (!document.hidden) oipOORender(); }, OIP_OO_POLL_MS);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', oipOOInit);
} else {
    oipOOInit();
}
