/**
 * Notification bell: polls /api/notifications, renders the dropdown list,
 * and opens a detail popup (with the full scan payload) on click.
 */
(function () {
    const POLL_INTERVAL_MS = 60000;
    let notifications = [];
    let pollTimer = null;

    function fmtTime(isoLike) {
        try {
            const d = new Date(isoLike.replace(' ', 'T') + 'Z');
            if (isNaN(d.getTime())) return isoLike;
            return d.toLocaleString();
        } catch (e) {
            return isoLike;
        }
    }

    async function fetchNotifications() {
        try {
            const res = await fetch('/api/notifications?limit=50', { credentials: 'same-origin' });
            const data = await res.json();
            if (!data.success) return;
            notifications = data.notifications || [];
            renderBadge(data.unread_count || 0);
            renderList();
        } catch (e) {
            console.error('Error fetching notifications:', e);
        }
    }

    function renderBadge(count) {
        const badge = document.getElementById('notifBadge');
        if (!badge) return;
        if (count > 0) {
            badge.textContent = count > 99 ? '99+' : String(count);
            badge.style.display = 'block';
        } else {
            badge.style.display = 'none';
        }
    }

    function renderList() {
        const list = document.getElementById('notifDropdownList');
        if (!list) return;

        if (!notifications.length) {
            list.innerHTML = '<div class="notif-empty">No notifications yet</div>';
            return;
        }

        list.innerHTML = notifications.map(n => `
            <button class="notif-item ${n.is_read ? '' : 'unread'}" data-id="${n.id}">
                <span class="notif-item-title">${escapeHtml(n.title)}</span>
                ${n.summary ? `<span class="notif-item-summary">${escapeHtml(n.summary)}</span>` : ''}
                <span class="notif-item-time">${fmtTime(n.created_at)}</span>
            </button>
        `).join('');

        list.querySelectorAll('.notif-item').forEach(btn => {
            btn.addEventListener('click', () => openDetail(parseInt(btn.dataset.id, 10)));
        });
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str == null ? '' : String(str);
        return div.innerHTML;
    }

    function renderSignalTable(title, signals) {
        if (!signals || !signals.length) return '';
        const grid = DataGrid.render({
            rows: signals,
            columns: [
                { key: 'symbol', label: 'Symbol', strong: true },
                { key: 'direction', label: 'Dir',
                  tone: d => d === 'SELL' ? 'neg' : 'pos' },
                { key: 'current_price', label: 'Price', align: 'right' },
                { key: 'expiry_high', label: 'Exp High', align: 'right' },
                { key: 'expiry_low',  label: 'Exp Low',  align: 'right' },
                { key: 'expiry_date', label: 'Expiry' },
            ],
        });
        return `<div class="notif-modal-section-title">${escapeHtml(title)} (${signals.length})</div>${grid}`;
    }

    // A single paper entry, rendered as a label/value sheet rather than a grid
    // — one row of six columns reads far worse than the trade card below.
    function renderEmaEntry(payload) {
        const tone = payload.direction === 'SELL' ? 'neg' : 'pos';
        const money = v => (v === null || v === undefined || v === '') ? '—' : `₹${escapeHtml(v)}`;
        // Every value below is already-escaped HTML.
        const rows = [
            ['Direction', `<span class="notif-detail-${tone}">${escapeHtml(payload.direction)}</span>`],
            ['Entry', money(payload.entry_price)],
            ['Stop loss', money(payload.sl_price)],
            ['Target', money(payload.target_price) +
                (payload.target_pct ? ` (${escapeHtml(payload.target_pct)}%)` : '')],
            ['Quantity', escapeHtml(payload.qty) +
                (payload.lot_size ? ` (${escapeHtml(payload.lots)} × ${escapeHtml(payload.lot_size)})` : '')],
            ['Contract', escapeHtml(payload.future_month || '—')],
            ['Signal date', escapeHtml(payload.signal_date || '—')],
            // entry_time is a naive LOCAL isoformat() from the algo, so fmtTime
            // (which tags its input as UTC) would shift it — show it as sent.
            ['Entered at', escapeHtml((payload.entry_time || '—').replace('T', ' ').slice(0, 19))],
            ['Mode', escapeHtml(payload.mode || 'paper')],
        ];
        return `
            <div class="notif-modal-section-title">${escapeHtml(payload.symbol)}</div>
            <table class="notif-detail-table">
                ${rows.map(([label, valueHtml]) => `
                    <tr>
                        <td class="notif-detail-label">${escapeHtml(label)}</td>
                        <td class="notif-detail-value">${valueHtml}</td>
                    </tr>`).join('')}
            </table>`;
    }

    async function openDetail(id) {
        closeDropdown();
        try {
            const res = await fetch(`/api/notifications/${id}`, { credentials: 'same-origin' });
            const data = await res.json();
            if (!data.success) return;
            const n = data.notification;

            fetch(`/api/notifications/${id}/read`, { method: 'POST', credentials: 'same-origin' }).catch(() => {});
            const localItem = notifications.find(x => x.id === id);
            if (localItem) localItem.is_read = 1;
            renderList();
            renderBadge(notifications.filter(x => !x.is_read).length);

            const body = document.getElementById('notifModalBody');
            const payload = n.data || {};
            let html = '';
            if (n.category === 'ema_confluence_entry') {
                html = renderEmaEntry(payload);
            } else {
                html += renderSignalTable('BUY signals', payload.buy);
                html += renderSignalTable('SELL signals', payload.sell);
                if (!payload.buy?.length && !payload.sell?.length) {
                    html += '<div class="notif-empty">No breakout signals in this scan.</div>';
                }
            }
            body.innerHTML = html;
            document.getElementById('notifModalTitle').textContent = n.title;
            document.getElementById('notifModalOverlay').classList.add('show');
        } catch (e) {
            console.error('Error opening notification detail:', e);
        }
    }

    function closeDetail() {
        document.getElementById('notifModalOverlay').classList.remove('show');
    }

    function toggleDropdown(e) {
        e.preventDefault();
        e.stopPropagation();
        document.getElementById('notifDropdown').classList.toggle('show');
    }

    function closeDropdown() {
        const dd = document.getElementById('notifDropdown');
        if (dd) dd.classList.remove('show');
    }

    document.addEventListener('DOMContentLoaded', () => {
        const bellBtn = document.getElementById('notifBellBtn');
        if (!bellBtn) return;

        bellBtn.addEventListener('click', toggleDropdown);

        document.addEventListener('click', (e) => {
            const wrapper = document.getElementById('notifBellWrapper');
            if (wrapper && !wrapper.contains(e.target)) closeDropdown();
        });

        const closeBtn = document.getElementById('notifModalClose');
        if (closeBtn) closeBtn.addEventListener('click', closeDetail);
        const overlay = document.getElementById('notifModalOverlay');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) closeDetail();
            });
        }

        fetchNotifications();
        pollTimer = setInterval(fetchNotifications, POLL_INTERVAL_MS);
    });
})();
