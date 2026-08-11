/**
 * Orders module — all orders stored server-side in JSON (no IndexedDB).
 * Broker mode: orders placed to all active brokers and saved to server JSON.
 * Mine mode: orders saved to server JSON; backend monitors LIMIT orders.
 */

// ─── Helpers ──────────────────────────────────────────────────────────────────


function _formatTime(epochMs) {
    return new Intl.DateTimeFormat('en-IN', {
        timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    }).format(new Date(epochMs));
}

function _formatDate(epochMs) {
    return new Intl.DateTimeFormat('en-IN', {
        timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short'
    }).format(new Date(epochMs));
}

function _getCsrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

// ─── Server Order API ─────────────────────────────────────────────────────────

// sync=1 has the server reconcile today's resting orders against the broker
// order books first, so the Pending tab lists what is genuinely still open. An
// order the broker filled used to sit here as OPEN indefinitely, offering a
// cancel and a price edit that both had nothing left to act on.
async function _fetchOrders(history = false) {
    try {
        const res = await fetch(`/api/orders?history=${history ? 1 : 0}&sync=1`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.orders || [];
    } catch (_) { return []; }
}

// A cancel or a price edit now travels all the way to every broker the order
// was placed to, so both report what each broker said — silently swallowing the
// response would let the grid show a price or a cancel that never landed.
async function _deleteOrder(id) {
    try {
        const res = await fetch(`/api/orders/${id}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': _getCsrf() }
        });
        return await res.json();
    } catch (e) { return { success: false, error: e.message }; }
}

async function _updateOrderPrice(id, price) {
    try {
        const res = await fetch(`/api/orders/${id}/price`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _getCsrf() },
            body: JSON.stringify({ price })
        });
        return await res.json();
    } catch (e) { return { success: false, error: e.message }; }
}

/** "• ZERODHA 1: OK" per broker leg, for the toast. */
function _brokerLines(r) {
    if (!Array.isArray(r?.summary) || !r.summary.length) return '';
    return '\n' + r.summary.map(s => {
        const name = String(s.broker || '').replace(/_/g, ' ').toUpperCase();
        const msg = s.result?.success ? 'OK' : (s.result?.error || 'Failed');
        return `• ${name}: ${msg}`;
    }).join('\n');
}

// ─── SVG Icons ────────────────────────────────────────────────────────────────

const _SVG = {
    save: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="20 6 9 17 4 12"/>
    </svg>`,
    cancel: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>`,
    trash: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/>
    </svg>`,
};

// ─── Grid Rendering ───────────────────────────────────────────────────────────

let _historyMode = false;

// Unfilled orders — the ones whose price can still be changed. OPEN is a LIMIT
// order resting at the brokers; PENDING/EXECUTING are Mine orders the backend
// monitor still holds.
const _EDITABLE = ['PENDING', 'EXECUTING', 'OPEN'];

async function renderOrdersGrid() {
    const activeTab = document.querySelector('.ord-tab.active')?.dataset.tab || 'pending';
    const allOrders = await _fetchOrders(_historyMode);

    const pendingAll  = allOrders.filter(o => _EDITABLE.includes(o.status));
    const executedAll = allOrders.filter(o => o.status === 'EXECUTED');

    const pEl = document.getElementById('ordStatPendingCount');
    const eEl = document.getElementById('ordStatExecutedCount');
    if (pEl) pEl.textContent = pendingAll.length;
    if (eEl) eEl.textContent = executedAll.length;

    let orders = allOrders;
    if (activeTab === 'pending')  orders = pendingAll;
    else if (activeTab === 'executed') orders = executedAll;

    orders.sort((a, b) => b.created_at - a.created_at);

    const grid  = document.getElementById('ordersGrid');
    const empty = document.getElementById('ordersEmpty');
    if (!grid) return;

    if (!orders.length) {
        grid.innerHTML = '';
        if (empty) empty.classList.remove('hidden');
        return;
    }
    if (empty) empty.classList.add('hidden');

    const esc = DataGrid.escape;
    // Status → shared badge tone. Pending work is amber, a fill is green, a
    // cancel/reject is red; anything unrecognised stays neutral.
    const statusTone = s => ({
        PENDING: 'warn', EXECUTING: 'warn', OPEN: 'warn', EXECUTED: 'pos',
        CANCELLED: 'neg', REJECTED: 'neg',
    })[s] || 'neutral';

    grid.innerHTML = DataGrid.render({
        rows: orders,
        // Left border keys the row to its side; data-* are the click-delegation
        // hooks _attachGridListeners reads.
        rowClass: o => 'ord-row ord-action-' + o.action.toLowerCase(),
        rowAttrs: o => `data-id="${esc(o.id)}" data-mode="${esc(o.mode || 'broker')}"`,
        // render is called (value, row); these columns have no key, so they
        // read the order off the second arg.
        columns: [
            { label: 'Time', cellClass: 'ord-td-time', render: (_, o) => {
                const date = _historyMode
                    ? `<span class="ord-date">${esc(_formatDate(o.created_at))}</span> ` : '';
                const mode = o.mode === 'mine'
                    ? '<span class="ord-src-badge ord-src-mine" title="Mine mode">M</span>'
                    : '<span class="ord-src-badge ord-src-broker" title="Broker mode">B</span>';
                return date + esc(_formatTime(o.created_at)) + mode;
            } },
            { label: 'Type', render: (_, o) => DataGrid.badge(o.type || 'MARKET',
                (o.type || 'MARKET').toUpperCase() === 'LIMIT' ? 'special' : 'neutral') },
            { label: 'Instrument', cellClass: 'ord-td-inst',
              render: (_, o) => esc(`${o.symbol} ${o.strike} ${o.option_type}`) },
            { label: 'Action', render: (_, o) => DataGrid.badge(o.action,
                o.action === 'BUY' ? 'pos' : 'neg') },
            { label: 'Price', cellClass: 'ord-td-price', render: (_, o) =>
                _EDITABLE.includes(o.status)
                    ? `<input type="number" class="ord-price-input" value="${esc(o.price || 0)}" step="0.05" min="0">`
                    : `<span class="ord-price-val">₹${Number(o.price || 0).toFixed(2)}</span>` },
            { label: 'Status', render: (_, o) => DataGrid.badge(o.status, statusTone(o.status)) },
            { label: '', cellClass: 'ord-td-actions', render: (_, o) =>
                _EDITABLE.includes(o.status)
                    ? `<button class="ord-btn save-price" title="Update price">${_SVG.save}</button>` +
                      `<button class="ord-btn cancel-order" title="Cancel order">${_SVG.cancel}</button>`
                    : `<button class="ord-btn delete-order" title="Remove">${_SVG.trash}</button>` },
        ],
    });

    _attachGridListeners();
}

function _attachGridListeners() {
    const grid = document.getElementById('ordersGrid');
    if (!grid) return;

    // Re-render replaces #ordersGrid's innerHTML, not the node, so its listener
    // survives — bind once. cloneNode-swapping the freshly built table would
    // just strip the handler we are adding.
    if (grid.dataset.wired) return;
    grid.dataset.wired = '1';

    grid.addEventListener('click', async (e) => {
        const row = e.target.closest('tr.ord-row');
        if (!row) return;
        const id = row.dataset.id;

        const notify = (msg, tone) => {
            if (typeof showNotification === 'function') showNotification(msg, tone);
        };

        if (e.target.closest('.cancel-order') || e.target.closest('.delete-order')) {
            const r = await _deleteOrder(id);
            if (r?.success) notify(`Order cancelled${_brokerLines(r)}`, 'info');
            // gone = the broker had already finished with it; the server has
            // corrected the record, so the redraw moves it out of Pending.
            else if (r?.gone) notify(r.error || 'Order is no longer open', 'warning');
            else notify(`Cancel failed: ${r?.error || 'Unknown error'}${_brokerLines(r)}`, 'error');
            renderOrdersGrid();
        } else if (e.target.closest('.save-price')) {
            const input = row.querySelector('.ord-price-input');
            const newPrice = parseFloat(input?.value);
            if (isNaN(newPrice) || newPrice <= 0) {
                notify('Enter a valid price', 'error');
                return;
            }
            const r = await _updateOrderPrice(id, newPrice);
            if (r?.success) notify(`Price → ₹${newPrice}${_brokerLines(r)}`, 'success');
            else if (r?.gone) notify(r.error || 'Order is no longer open', 'warning');
            else notify(`Update failed: ${r?.error || 'Unknown error'}${_brokerLines(r)}`, 'error');
            renderOrdersGrid();
        }
    });
}

// ─── Page Initialization ──────────────────────────────────────────────────────

async function initOrdersPage() {
    window._isMarketOpen = isMarketOpen;

    const onOrdersPage = window.location.pathname.startsWith('/orders');
    if (!onOrdersPage) return;

    document.querySelectorAll('.ord-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.ord-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderOrdersGrid();
        });
    });

    const refreshBtn = document.getElementById('ordersRefreshBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            refreshBtn.style.opacity = '0.5';
            refreshBtn.disabled = true;
            await renderOrdersGrid();
            refreshBtn.style.opacity = '';
            refreshBtn.disabled = false;
        });
    }

    const historyBtn = document.getElementById('ordersHistoryBtn');
    if (historyBtn) {
        historyBtn.addEventListener('click', () => {
            _historyMode = !_historyMode;
            historyBtn.classList.toggle('active', _historyMode);
            renderOrdersGrid();
        });
    }

    await renderOrdersGrid();

    // Auto-refresh every 5 s so Mine PENDING orders update when backend executes them
    setInterval(() => {
        if (!document.hidden) renderOrdersGrid();
    }, 5000);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initOrdersPage);
} else {
    initOrdersPage();
}
