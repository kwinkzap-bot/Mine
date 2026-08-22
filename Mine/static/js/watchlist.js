/* ================================================================
   Watchlist — tabs of symbols, a fundamentals grid, and a docked chart.

   Three pieces, in this file in this order:

     1. Tabs      create / rename / delete, persisted server-side per user.
     2. Grid      DataGrid.mountSortable over /api/watchlist/rows.
     3. Drilldown a plain inline SVG of the daily close and the derived P/E
                  line, docked to the bottom of the viewport.

   The drilldown deliberately mirrors the OI Crossover scanner's: same dock,
   same ‹ › stepping, same crosshair, same last-value tags. Two panels that
   do the same job should not be two different things to learn — the chart
   code here is that one adapted to a daily series with a shared x-axis and
   two independent y-axes (rupees left, P/E right).

   Prices come from the broker when a session exists and from the delayed
   fundamentals cache when it doesn't; the dot in the topbar says which.
   ================================================================ */

(function (global) {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const API = '/api/watchlist';
    const LAST_TAB_KEY = 'wl.lastTab';
    const REFRESH_MS = 60000;

    const state = {
        tabs: [],
        activeTab: null,
        rows: [],
        // Drilldown
        openSymbol: null,
        // Opens on Both: price alone answers "what did it do", and the pair
        // answers "was that earnings or re-rating", which is what a P/E
        // column is being clicked to ask.
        chartMode: 'both',
        range: '1y',
        chartData: null,
        hiddenSeries: new Set(),
        // symbol|range -> payload, and the in-flight promises for the same
        // keys. See fetchHistory / prefetchNeighbours.
        history: new Map(),
        inflight: new Map(),
        timer: null,
        // Type-ahead
        suggestions: [],
        sugIndex: -1,
        sugSeq: 0,
        // The dialog is shared by "new tab" and "rename"; this is which.
        modalTab: null,
        // Order ticket: the row it was opened for, and the broker list.
        ticket: null,
        brokers: [],
    };

    // ── formatting ───────────────────────────────────────────────────

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    const isBlank = (v) => v === null || v === undefined || v === '';

    const money = (v) => isBlank(v) ? '—' :
        '₹' + Number(v).toLocaleString('en-IN', {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        });

    const pct = (v) => isBlank(v) ? '—' :
        (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';

    const ratio = (v) => isBlank(v) ? '—' : Number(v).toFixed(2);

    // Market cap in the unit Indian screens actually use. Anything past a
    // lakh crore reads as "₹17.74 L Cr" rather than fifteen digits.
    function marketCap(v) {
        if (isBlank(v) || !Number(v)) return '—';
        const cr = Number(v) / 1e7;
        if (cr >= 1e5) return '₹' + (cr / 1e5).toFixed(2) + ' L Cr';
        if (cr >= 1) return '₹' + cr.toLocaleString('en-IN', { maximumFractionDigits: 0 }) + ' Cr';
        return '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }

    const dayLabel = (iso) => {
        const d = new Date(iso + 'T00:00:00');
        return `${d.getDate()} ${MONTHS[d.getMonth()]} ${String(d.getFullYear()).slice(2)}`;
    };

    const clockTime = (iso) => {
        const d = new Date(iso);
        const h = d.getHours() % 12 || 12;
        return `${h}:${String(d.getMinutes()).padStart(2, '0')} ` +
               `${d.getHours() < 12 ? 'AM' : 'PM'}`;
    };

    // ── transport ────────────────────────────────────────────────────

    async function getJSON(url) {
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        return res.json();
    }

    async function sendJSON(url, method, body) {
        const res = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
                // CSRF is off in this app's config, but the token costs
                // nothing to send and these are the page's only writes —
                // they should keep working if it is ever switched on.
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '',
            },
            body: body ? JSON.stringify(body) : undefined,
        });
        return res.json();
    }

    const toast = (msg, ok) => {
        if (global.showNotification) global.showNotification(msg, ok ? 'success' : 'error');
        else if (!ok) console.warn(msg);
    };

    // ── tabs ─────────────────────────────────────────────────────────

    function renderTabs() {
        const bar = $('wlTabBar');
        const chips = state.tabs.map((t) => {
            const active = t.id === state.activeTab;
            return `<button class="wl-tab${active ? ' active' : ''}" data-tab="${t.id}">
                        <span>${DataGrid.escape(t.name)}</span>
                        <span class="wl-tab-count">${t.count}</span>
                        ${active ? `
                        <span class="wl-tab-act" data-act="rename" data-tab="${t.id}"
                              role="button" tabindex="0" title="Rename this tab">✎</span>
                        <span class="wl-tab-act" data-act="delete" data-tab="${t.id}"
                              role="button" tabindex="0" title="Delete this tab">✕</span>` : ''}
                    </button>`;
        }).join('');
        bar.innerHTML = chips +
            '<button class="wl-tab-add" id="wlAddTab" title="Create a new tab">＋</button>';
    }

    async function loadTabs(selectId) {
        const data = await getJSON(`${API}/tabs`);
        if (!data.success) { toast(data.error || 'Could not load tabs', false); return; }
        state.tabs = data.tabs || [];

        // Remembering the last tab is what makes this page usable as a
        // landing page — it is opened to look at one list, not to pick one.
        const remembered = Number(localStorage.getItem(LAST_TAB_KEY));
        const wanted = [selectId, state.activeTab, remembered]
            .find((id) => id && state.tabs.some((t) => t.id === id));
        state.activeTab = wanted || (state.tabs[0] && state.tabs[0].id) || null;
        if (state.activeTab) localStorage.setItem(LAST_TAB_KEY, String(state.activeTab));

        renderTabs();
        $('wlSearch').disabled = !state.activeTab;
        await loadRows();
    }

    function selectTab(id) {
        if (id === state.activeTab) return;
        state.activeTab = id;
        localStorage.setItem(LAST_TAB_KEY, String(id));
        closeDrill();
        renderTabs();
        loadRows();
    }

    // ── dialog (new tab / rename) ────────────────────────────────────

    function openModal(tab) {
        state.modalTab = tab || null;
        $('wlModalTitle').textContent = tab ? 'Rename tab' : 'New tab';
        $('wlModalInput').value = tab ? tab.name : '';
        $('wlModalMsg').textContent = '';
        $('wlModalBack').hidden = false;
        $('wlModalInput').focus();
        $('wlModalInput').select();
    }

    const closeModal = () => { $('wlModalBack').hidden = true; state.modalTab = null; };

    async function saveModal() {
        const name = $('wlModalInput').value.trim();
        if (!name) { $('wlModalMsg').textContent = 'Give the tab a name.'; return; }

        const tab = state.modalTab;
        const result = tab
            ? await sendJSON(`${API}/tabs/${tab.id}`, 'PUT', { name })
            : await sendJSON(`${API}/tabs`, 'POST', { name });

        if (!result.success) { $('wlModalMsg').textContent = result.error || 'Could not save.'; return; }
        closeModal();
        await loadTabs(tab ? tab.id : result.tab.id);
    }

    async function deleteTab(tab) {
        const what = tab.count
            ? `Delete "${tab.name}" and its ${tab.count} symbol${tab.count > 1 ? 's' : ''}?`
            : `Delete "${tab.name}"?`;
        if (!confirm(what)) return;
        const result = await sendJSON(`${API}/tabs/${tab.id}`, 'DELETE');
        if (!result.success) { toast(result.error || 'Could not delete the tab', false); return; }
        if (state.activeTab === tab.id) state.activeTab = null;
        closeDrill();
        await loadTabs();
    }

    // ── type-ahead ───────────────────────────────────────────────────

    function renderSuggestions() {
        const box = $('wlSuggest');
        if (!state.suggestions.length) {
            box.innerHTML = '<div class="wl-sug-empty">No match</div>';
            box.hidden = false;
            return;
        }
        box.innerHTML = state.suggestions.map((s, i) => `
            <button class="wl-sug${i === state.sugIndex ? ' wl-sug-active' : ''}"
                    data-symbol="${DataGrid.escape(s.symbol)}">
                <span class="wl-sug-sym">${DataGrid.escape(s.symbol)}</span>
                <span class="wl-sug-co">${DataGrid.escape(s.company || '')}</span>
                <span class="wl-sug-kind${s.kind === 'INDEX' ? ' wl-kind-index' : ''}">${s.kind}</span>
            </button>`).join('');
        box.hidden = false;
    }

    const hideSuggestions = () => {
        $('wlSuggest').hidden = true;
        state.suggestions = [];
        state.sugIndex = -1;
    };

    async function runSearch(q) {
        if (!q.trim()) { hideSuggestions(); return; }
        // The master is ~10k rows and every keystroke fires a request, so a
        // slow one must never overwrite a newer, faster one.
        const seq = ++state.sugSeq;
        const data = await getJSON(`${API}/search?q=${encodeURIComponent(q)}`);
        if (seq !== state.sugSeq) return;
        state.suggestions = data.results || [];
        state.sugIndex = state.suggestions.length ? 0 : -1;
        renderSuggestions();
    }

    async function addSymbol(symbol) {
        if (!state.activeTab) return;
        const result = await sendJSON(`${API}/tabs/${state.activeTab}/items`, 'POST', { symbol });
        if (!result.success) { toast(result.error || 'Could not add the symbol', false); return; }
        $('wlSearch').value = '';
        hideSuggestions();
        toast(`${symbol} added`, true);
        await loadTabs(state.activeTab);
    }

    async function removeItem(id, symbol) {
        const result = await sendJSON(`${API}/items/${id}`, 'DELETE');
        if (!result.success) { toast(result.error || 'Could not remove the symbol', false); return; }
        if (state.openSymbol === symbol) closeDrill();
        await loadTabs(state.activeTab);
    }

    // ── order ticket ─────────────────────────────────────────────────
    //
    // The order is built from the ticket's own fields at send time, never
    // from the row that opened it: the grid re-prices every 60s, and an
    // order must be the one that was on screen when the button was pressed.

    const PRODUCTS = [['CNC', 'Delivery (CNC)'], ['MIS', 'Intraday (MIS)']];
    const ORDER_TYPES = [['MARKET', 'Market'], ['LIMIT', 'Limit']];

    // What an MIS order actually blocks. Delivery is the full value; intraday
    // is that divided by the leverage the broker allows. This is a house
    // estimate, not a quote — the real multiple is per-stock and per-broker,
    // and only the broker's own margin call knows it. Change it here.
    const MIS_LEVERAGE = 5;

    async function loadBrokers() {
        try {
            const data = await getJSON(`${API}/brokers`);
            state.brokers = data.success ? (data.brokers || []) : [];
        } catch (e) {
            state.brokers = [];
        }
    }

    function ticketBody(row, side) {
        const price = row.ltp != null ? Number(row.ltp).toFixed(2) : '';
        const brokers = state.brokers.length
            ? state.brokers.map((b) => `<option value="${b.instance}">` +
                `${DataGrid.escape(b.name)} · ${DataGrid.escape(b.type)}</option>`).join('')
            : '<option value="">No broker configured</option>';
        return `
          <div class="wl-ticket-head">
            <span class="wl-ticket-side wl-side-${side.toLowerCase()}">${side}</span>
            <strong>${DataGrid.escape(row.symbol)}</strong>
            <span class="wl-ticket-co">${DataGrid.escape(row.company || '')}</span>
            <span class="wl-ticket-ltp">LTP ${DataGrid.escape(money(row.ltp))}</span>
          </div>
          <div class="wl-ticket-grid">
            <label for="wlTicketQty">Quantity</label>
            <input type="number" id="wlTicketQty" min="1" step="1" value="1">

            <label for="wlTicketType">Order type</label>
            <select id="wlTicketType">
              ${ORDER_TYPES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}
            </select>

            <label for="wlTicketPrice">Limit price</label>
            <input type="number" id="wlTicketPrice" step="0.05" min="0.05" value="${price}" disabled>

            <label for="wlTicketProduct">Product</label>
            <select id="wlTicketProduct">
              ${PRODUCTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}
            </select>

            <label for="wlTicketBroker">Broker</label>
            <select id="wlTicketBroker">${brokers}</select>
          </div>
          <dl class="wl-ticket-cost" id="wlTicketCost"></dl>
          <p class="wl-ticket-note" id="wlTicketNote"></p>`;
    }

    // The one-line restatement of what pressing the button will actually
    // send. It is rebuilt on every field change, because the thing being
    // confirmed is the order, not the form.
    function syncTicket() {
        const t = state.ticket;
        if (!t) return;
        const type = $('wlTicketType').value;
        const qty = Number($('wlTicketQty').value || 0);
        const price = $('wlTicketPrice');
        price.disabled = type !== 'LIMIT';

        // The money the order ties up, priced off whatever the order would
        // actually go out at: the typed limit, or the last traded price for a
        // market order. A market order fills where it fills — this is the
        // size of the position, not a promise about the fill.
        const product = $('wlTicketProduct').value;
        const basis = type === 'LIMIT' ? Number(price.value || 0) : Number(t.row.ltp || 0);
        const value = qty > 0 && basis > 0 ? qty * basis : null;
        const margin = value === null ? null
            : (product === 'MIS' ? value / MIS_LEVERAGE : value);
        $('wlTicketCost').innerHTML =
            `<dt>Order value</dt><dd>${DataGrid.escape(money(value))}</dd>` +
            `<dt>Margin</dt><dd>${DataGrid.escape(money(margin))}` +
            `<span class="wl-ticket-basis">${product === 'MIS'
                ? `MIS · approx. ${MIS_LEVERAGE}× intraday`
                : 'CNC · full value'}</span></dd>`;

        const at = type === 'LIMIT'
            ? `@ ${money(price.value)} limit`
            : '@ market';
        const broker = state.brokers.find(
            (b) => String(b.instance) === $('wlTicketBroker').value);
        $('wlTicketNote').textContent = qty > 0 && broker
            ? `${t.side} ${qty} ${t.row.symbol} ${at} · ` +
              `${product} · ${broker.name}`
            : 'Fill in a quantity and choose a broker.';
        $('wlTicketSend').disabled = !(qty > 0 && broker);
    }

    function openTicket(symbol, side) {
        const row = state.rows.find((r) => r.symbol === symbol);
        if (!row || row.kind === 'INDEX') return;

        state.ticket = { row, side };
        $('wlTicketTitle').textContent = `${side} ${row.symbol}`;
        $('wlTicketBody').innerHTML = ticketBody(row, side);
        $('wlTicketMsg').textContent = '';
        $('wlTicketMsg').className = 'wl-ticket-msg';
        $('wlTicketSend').textContent = 'Place order';
        $('wlTicketSend').className = `wl-btn wl-btn-primary wl-send-${side.toLowerCase()}`;
        $('wlTicketBack').hidden = false;

        ['wlTicketQty', 'wlTicketType', 'wlTicketPrice', 'wlTicketProduct', 'wlTicketBroker']
            .forEach((id) => {
                $(id).addEventListener('input', syncTicket);
                $(id).addEventListener('change', syncTicket);
            });
        syncTicket();
        $('wlTicketQty').focus();
        $('wlTicketQty').select();
    }

    function closeTicket() {
        state.ticket = null;
        $('wlTicketBack').hidden = true;
    }

    async function sendOrder() {
        const t = state.ticket;
        if (!t) return;
        const send = $('wlTicketSend');
        const msg = $('wlTicketMsg');
        send.disabled = true;
        send.textContent = 'Placing…';
        msg.textContent = '';
        msg.className = 'wl-ticket-msg';

        const body = {
            symbol: t.row.symbol,
            side: t.side,
            qty: Number($('wlTicketQty').value || 0),
            order_type: $('wlTicketType').value,
            product: $('wlTicketProduct').value,
            broker: Number($('wlTicketBroker').value || 0),
            ltp: t.row.ltp,
        };
        if (body.order_type === 'LIMIT') body.limit_price = Number($('wlTicketPrice').value || 0);

        let result;
        try {
            result = await sendJSON(`${API}/order`, 'POST', body);
        } catch (e) {
            result = { success: false, error: e.message };
        }

        if (!result.success) {
            msg.textContent = result.error || 'The broker rejected the order';
            msg.className = 'wl-ticket-msg wl-ticket-err';
            send.disabled = false;
            send.textContent = 'Place order';
            return;
        }
        // Left on screen with the order id rather than closed on success: the
        // id is the only thing that ties this back to the broker's order book.
        msg.textContent = `Placed · order ${result.order_id} at ${result.broker}`;
        msg.className = 'wl-ticket-msg wl-ticket-ok';
        send.textContent = 'Placed';
        toast(`${body.side} ${body.qty} ${body.symbol} placed`, true);
    }

    async function moveItem(id, symbol, tabId) {
        const result = await sendJSON(`${API}/items/${id}/tab`, 'PUT', { tab_id: tabId });
        if (!result.success) { toast(result.error || 'Could not move the symbol', false); return; }
        const target = state.tabs.find((t) => t.id === tabId);
        if (state.openSymbol === symbol) closeDrill();
        toast(`${symbol} moved to ${target ? target.name : 'the other tab'}`, true);
        await loadTabs(state.activeTab);
    }

    // Target-tab picker. A popup rather than a select in every row: the list
    // is the same for all of them, and a row of dropdowns is a wall of chrome
    // for something clicked once in a while.
    function openMoveMenu(button) {
        closeMoveMenu();
        const id = Number(button.dataset.move);
        const symbol = button.dataset.symbol;
        const others = state.tabs.filter((t) => t.id !== state.activeTab);
        if (!others.length) return;

        const menu = document.createElement('div');
        menu.className = 'wl-menu';
        menu.id = 'wlMoveMenu';
        menu.innerHTML = `<div class="wl-menu-hdr">Move ${DataGrid.escape(symbol)} to</div>` +
            others.map((t) => `<button class="wl-menu-item" data-tab="${t.id}">` +
                `${DataGrid.escape(t.name)}<span class="wl-tab-count">${t.count}</span>` +
                `</button>`).join('');
        document.body.appendChild(menu);

        // Anchored to the button, then pulled back inside the viewport — the
        // action column sits at the right edge, so a menu placed naively
        // hangs off the page on a narrow screen.
        const rect = button.getBoundingClientRect();
        const width = menu.offsetWidth;
        menu.style.top = `${window.scrollY + rect.bottom + 4}px`;
        menu.style.left =
            `${Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))}px`;

        menu.addEventListener('click', (e) => {
            const pick = e.target.closest('[data-tab]');
            if (!pick) return;
            closeMoveMenu();
            moveItem(id, symbol, Number(pick.dataset.tab));
        });
    }

    function closeMoveMenu() {
        const menu = $('wlMoveMenu');
        if (menu) menu.remove();
    }

    // ── grid ─────────────────────────────────────────────────────────

    // The 52-week position bar. Rendered rather than formatted because it is
    // a two-part shape (fill to the current position, tick at it), and the
    // read it gives — "near its low", "at its high" — is the reason the row
    // carries a low and a high at all.
    function bandCell(v, row) {
        if (isBlank(v)) return '<span class="dg-muted">—</span>';
        const at = Math.max(0, Math.min(100, Number(v)));
        return `<span class="wl-band" title="${at.toFixed(0)}% of the 52-week range ` +
               `(${money(row.low52)} – ${money(row.high52)})">` +
               `<span class="wl-band-fill" style="width:${at.toFixed(1)}%"></span>` +
               `<span class="wl-band-mark" style="left:calc(${at.toFixed(1)}% - 1px)"></span></span>`;
    }

    const COLUMNS = [
        {
            // The company name is the cell's tooltip rather than a column of
            // its own: it is the widest thing in the grid and the one field
            // nobody scans down. It still leads the drilldown header, which
            // is where "which company is this" actually gets asked.
            key: 'symbol', label: 'Symbol', sortable: true, strong: true,
            title: (v, row) => row.company || v,
            render: (v, row) => DataGrid.escape(v) +
                (row.kind === 'INDEX' ? ' <span class="wl-sug-kind wl-kind-index">INDEX</span>' : ''),
        },
        { key: 'ltp', label: 'LTP', sortable: true, align: 'right', strong: true, format: money },
        {
            key: 'change_pct', label: 'Chg %', sortable: true, align: 'right',
            format: pct, tone: DataGrid.sign,
        },
        // The two raw 52-week numbers are gone from the grid; the range bar
        // is the read they existed for, and it carries both of them in its
        // tooltip. They still label the reference lines on the chart.
        { key: 'band52', label: '52W Range', sortable: true, align: 'center', render: bandCell },
        {
            key: 'from_high', label: 'Off High', sortable: true, align: 'right',
            format: pct, tone: DataGrid.sign,
            title: 'How far the last price sits below the 52-week high',
        },
        {
            key: 'pe', label: 'P/E Ratio', sortable: true, align: 'right', strong: true,
            cellClass: 'wl-pe-cell', format: ratio,
            title: (v, row) => row.kind === 'INDEX'
                ? 'Indices carry no earnings — click for the price history'
                : 'Price to Earnings (TTM) — click for the P/E and price history',
        },
        { key: 'market_cap', label: 'Mkt Cap', sortable: true, align: 'right', format: marketCap },
        {
            key: 'sector', label: 'Sector', sortable: true, cellClass: 'dg-muted',
            format: (v) => v || '—',
        },
        {
            key: 'id', label: '', align: 'center',
            render: (v, row) => {
                const sym = DataGrid.escape(row.symbol);
                const alone = state.tabs.length < 2;
                // An index has no cash-market instrument to buy, so it gets no
                // order buttons at all rather than buttons that always fail.
                const trade = row.kind === 'INDEX' ? '' :
                    `<button class="wl-act wl-buy" data-order="BUY" data-symbol="${sym}" ` +
                    `title="Buy ${sym}">B</button>` +
                    `<button class="wl-act wl-sell" data-order="SELL" data-symbol="${sym}" ` +
                    `title="Sell ${sym}">S</button>`;
                return trade +
                       `<button class="wl-act" data-move="${v}" data-symbol="${sym}"` +
                       `${alone ? ' disabled' : ''} title="${alone
                            ? 'Create another tab to move this into'
                            : 'Move to another watchlist'}">⇄</button>` +
                       `<button class="wl-act wl-del" data-remove="${v}" data-symbol="${sym}" ` +
                       `title="Remove from this tab">✕</button>`;
            },
        },
    ];

    function renderGrid() {
        closeMoveMenu();   // its anchor row is about to be replaced
        const host = $('wlGrid');
        if (!state.activeTab) {
            host.innerHTML = '<div class="wl-empty">No tabs yet. ' +
                'Create one with <strong>＋</strong> to start a watchlist.</div>';
            return;
        }
        if (!state.rows.length) {
            host.innerHTML = '<div class="wl-empty">This tab is empty. ' +
                'Search a stock or index above to add it.</div>';
            return;
        }
        DataGrid.mountSortable(host, {
            rows: state.rows,
            columns: COLUMNS,
            empty: 'No symbols in this tab',
            rowAttrs: (row) => `data-symbol="${DataGrid.escape(row.symbol)}"`,
            rowClass: (row) => row.symbol === state.openSymbol ? 'wl-row-open' : '',
            // The drilldown's ‹ › steps through the rows in the order they
            // are on screen, which is the sorted order, not the stored one.
            onSorted: (rows) => { state.rows = rows; },
        });
    }

    async function loadRows(opts) {
        const force = !!(opts && opts.force);
        if (!state.activeTab) { renderGrid(); return; }

        const btn = $('wlRefresh');
        btn.classList.add('wl-refreshing');
        try {
            const data = await getJSON(
                `${API}/rows?tab_id=${state.activeTab}${force ? '&refresh=1' : ''}`);
            if (!data.success) { toast(data.error || 'Could not load the watchlist', false); return; }
            state.rows = data.rows || [];
            $('wlAsOf').textContent = data.as_of ? 'as of ' + clockTime(data.as_of) : '—';
            $('wlDot').classList.toggle('wl-live', !!data.live);
            $('wlDot').title = data.live
                ? 'Live prices from the broker'
                : 'Delayed prices — no broker session, showing the cached last price';
            renderGrid();
            if (state.openSymbol && !state.rows.some((r) => r.symbol === state.openSymbol)) {
                closeDrill();
            } else {
                syncNav();
            }
        } finally {
            btn.classList.remove('wl-refreshing');
        }
    }

    // ── drilldown chart ──────────────────────────────────────────────

    // A plain inline SVG rather than a charting library: two polylines, a
    // grid and a crosshair is the whole requirement.
    //
    // Two independent y-axes. Price sits on the left in rupees and P/E on the
    // right as a multiple — quantities with no common unit, so a shared axis
    // would flatten one of them. Shown together because the question the
    // page exists to answer is whether a move was earnings or re-rating: a
    // price line rising with a flat P/E is growth, the two rising together is
    // the market paying more for the same rupee.
    //
    // Sized in real pixels from the measured container rather than scaled
    // from a fixed viewBox, so the axis labels don't stretch on a wide panel.
    const PAD = { L: 64, R: 56, T: 14, B: 34 };
    const CHART_H = 320;

    const CHART_MODES = {
        price: { label: 'Price', series: ['price'] },
        pe:    { label: 'P/E Ratio', series: ['pe'] },
        both:  { label: 'Price + P/E', series: ['price', 'pe'] },
    };

    const SERIES = {
        price: { key: 'close', cls: 'price', label: 'Close', fmt: money, axis: 'left' },
        pe:    { key: 'pe', cls: 'pe', label: 'P/E (TTM)', fmt: ratio, axis: 'right' },
    };

    // Round tick steps (1/2/2.5/5/10 × powers of ten) so the axis reads
    // 1,400 / 1,450 / 1,500 rather than whatever the extremes divide into.
    function niceTicks(lo, hi, target) {
        const span = hi - lo;
        if (!(span > 0)) return [lo];
        const raw = span / target;
        const mag = Math.pow(10, Math.floor(Math.log10(raw)));
        const norm = raw / mag;
        const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5
                    : norm <= 5 ? 5 : 10) * mag;
        const out = [];
        for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
            out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
        }
        return out;
    }

    function padRange(lo, hi, frac) {
        if (hi === lo) { const bump = Math.abs(hi || 1) * 0.01 || 1; return [lo - bump, hi + bump]; }
        const pad = (hi - lo) * frac;
        return [lo - pad, hi + pad];
    }

    const tsToMs = (ts) => new Date(ts + 'T00:00:00').getTime();

    function activeSeries() {
        return CHART_MODES[state.chartMode].series
            .filter((name) => !state.hiddenSeries.has(name))
            .map((name) => ({ name, ...SERIES[name] }));
    }

    function buildGeometry(data, width) {
        const pts = data.points || [];
        if (pts.length < 2) return null;

        const plotW = width - PAD.L - PAD.R;
        const plotH = CHART_H - PAD.T - PAD.B;
        if (plotW < 80) return null;

        const t0 = tsToMs(pts[0].ts);
        const t1 = tsToMs(pts[pts.length - 1].ts);
        const span = Math.max(1, t1 - t0);

        const closes = pts.map((p) => p.close).filter((v) => v != null);
        // The 52-week rules are drawn against the price axis, so they are
        // folded into its range — otherwise a stock sitting well off its
        // high would have the line clipped clean off the frame.
        const refs = [data.high52, data.low52].filter((v) => v != null);
        const [pLo, pHi] = closes.length
            ? padRange(Math.min(...closes, ...refs), Math.max(...closes, ...refs), 0.08)
            : [0, 1];

        const pes = pts.map((p) => p.pe).filter((v) => v != null);
        const [eLo, eHi] = pes.length ? padRange(Math.min(...pes), Math.max(...pes), 0.10) : [0, 1];

        const xMs = (ms) => PAD.L + ((ms - t0) / span) * plotW;
        const x = (ts) => xMs(tsToMs(ts));
        const yP = (v) => PAD.T + (1 - (v - pLo) / (pHi - pLo)) * plotH;
        const yE = (v) => PAD.T + (1 - (v - eLo) / (eHi - eLo)) * plotH;

        return {
            pts, width, plotW, plotH, t0, t1, span, x, xMs, yP, yE,
            pLo, pHi, eLo, eHi,
            hasPe: pes.length > 0,
            refs: refs.length ? { high: data.high52, low: data.low52 } : null,
            y: (name) => (name === 'pe' ? yE : yP),
        };
    }

    // Six or so ticks, snapped to the first trading day of a month (or of a
    // year on the long ranges) so the same chart always rules in the same
    // places whatever the sampling was.
    function timeTicks(geo) {
        const years = geo.span / (365 * 86400000);
        const byYear = years > 2.5;
        const seen = new Set();
        const out = [];
        for (const p of geo.pts) {
            const d = new Date(p.ts + 'T00:00:00');
            const key = byYear ? d.getFullYear() : `${d.getFullYear()}-${d.getMonth()}`;
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({ ms: tsToMs(p.ts), label: byYear ? String(d.getFullYear()) : MONTHS[d.getMonth()] +
                       (d.getMonth() === 0 ? ` ${String(d.getFullYear()).slice(2)}` : '') });
        }
        // Thin to at most eight labels rather than drawing twelve months on
        // a 700px panel and letting them collide.
        const stride = Math.ceil(out.length / 8);
        return out.filter((_, i) => i % stride === 0);
    }

    function renderChart(data, geo) {
        const { pts, width, x, xMs } = geo;
        const right = width - PAD.R;
        const bottom = CHART_H - PAD.B;
        const drawn = activeSeries().filter((s) => s.name !== 'pe' || geo.hasPe);

        const pTicks = niceTicks(geo.pLo, geo.pHi, 7);
        const eTicks = niceTicks(geo.eLo, geo.eHi, 7);
        const tTicks = timeTicks(geo);
        const showPe = drawn.some((s) => s.name === 'pe');
        const showPrice = drawn.some((s) => s.name === 'price');
        // With price hidden, P/E moves to the left axis rather than leaving
        // 64px of empty gutter and hugging the right edge.
        const peLeft = showPe && !showPrice;
        const peAxisX = peLeft ? PAD.L - 8 : right + 8;

        const grid =
            pTicks.map((v) => `<line class="wl-grid" x1="${PAD.L}" y1="${geo.yP(v).toFixed(1)}" ` +
                              `x2="${right}" y2="${geo.yP(v).toFixed(1)}"></line>`).join('') +
            tTicks.map((t) => {
                const px = xMs(t.ms).toFixed(1);
                return `<line class="wl-grid" x1="${px}" y1="${PAD.T}" x2="${px}" y2="${bottom}"></line>`;
            }).join('');

        const axes =
            (showPrice ? pTicks : []).map((v) =>
                `<text class="wl-axis" x="${PAD.L - 8}" y="${(geo.yP(v) + 3).toFixed(1)}" ` +
                `text-anchor="end">${DataGrid.escape(money(v))}</text>`).join('') +
            (showPe ? eTicks : []).map((v) =>
                `<text class="wl-axis" x="${peAxisX}" y="${(geo.yE(v) + 3).toFixed(1)}" ` +
                `text-anchor="${peLeft ? 'end' : 'start'}">${ratio(v)}</text>`).join('') +
            tTicks.map((t) => {
                const px = xMs(t.ms);
                if (px < PAD.L + 12 || px > right - 12) return '';
                return `<text class="wl-axis wl-axis-t" x="${px.toFixed(1)}" y="${CHART_H - 12}" ` +
                       `text-anchor="middle">${DataGrid.escape(t.label)}</text>`;
            }).join('');

        // 52-week high/low rules, only while the price axis is on screen —
        // they are the row's headline numbers, and seeing where the line sat
        // against them is why the chart opens from that row.
        let refs = '';
        if (showPrice && geo.refs) {
            for (const [name, value] of [['high', geo.refs.high], ['low', geo.refs.low]]) {
                if (value == null) continue;
                const y = geo.yP(value);
                if (y < PAD.T || y > bottom) continue;
                refs += `<line class="wl-ref wl-ref-${name}" x1="${PAD.L}" y1="${y.toFixed(1)}" ` +
                        `x2="${right}" y2="${y.toFixed(1)}"></line>` +
                        `<text class="wl-ref-label wl-ref-label-${name}" x="${PAD.L + 6}" ` +
                        `y="${(y - 4).toFixed(1)}">52W ${name === 'high' ? 'High' : 'Low'} ` +
                        `${DataGrid.escape(money(value))}</text>`;
            }
        }

        const area = (showPrice && drawn.length === 1) ? (() => {
            const d = pts.filter((p) => p.close != null)
                .map((p) => `${x(p.ts).toFixed(1)},${geo.yP(p.close).toFixed(1)}`);
            if (!d.length) return '';
            return `<polygon class="wl-area-price" points="${PAD.L},${bottom} ${d.join(' ')} ` +
                   `${right},${bottom}"></polygon>`;
        })() : '';

        const lines = drawn.map((s) => {
            const d = pts.filter((p) => p[s.key] != null)
                .map((p) => `${x(p.ts).toFixed(1)},${geo.y(s.name)(p[s.key]).toFixed(1)}`).join(' ');
            return `<polyline class="wl-line wl-line-${s.cls}" points="${d}"></polyline>`;
        }).join('');

        // Last value: a dot on the line, a rule across to its axis, and a
        // tag with the number — the "where does this sit now" read.
        const last = [...pts].reverse().find((p) => p.close != null) || pts[pts.length - 1];
        const tags = drawn.map((s) => {
            const value = last[s.key];
            if (value == null) return '';
            const yv = geo.y(s.name)(value);
            const onLeft = s.axis === 'left' || (s.name === 'pe' && peLeft);
            const tagX = onLeft ? PAD.L - 58 : right + 2;
            return `<g class="wl-tag wl-tag-${s.cls}">` +
                   `<line class="wl-tag-rule" x1="${PAD.L}" y1="${yv.toFixed(1)}" ` +
                   `x2="${right}" y2="${yv.toFixed(1)}"></line>` +
                   `<circle cx="${x(last.ts).toFixed(1)}" cy="${yv.toFixed(1)}" r="3.5"></circle>` +
                   `<rect x="${tagX}" y="${(yv - 8).toFixed(1)}" width="56" height="16" rx="3"></rect>` +
                   `<text x="${(tagX + 28).toFixed(1)}" y="${(yv + 4).toFixed(1)}" ` +
                   `text-anchor="middle">${DataGrid.escape(s.fmt(value))}</text></g>`;
        }).join('');

        const dots = drawn.map((s) =>
            `<circle class="wl-cross-dot wl-dot-${s.cls}" data-key="${s.name}" r="4" hidden></circle>`
        ).join('');

        return `<svg class="wl-svg" width="${width}" height="${CHART_H}"
                     viewBox="0 0 ${width} ${CHART_H}" role="img"
                     aria-label="${DataGrid.escape(data.symbol)} ${CHART_MODES[state.chartMode].label}">
            ${grid}
            <rect class="wl-frame" x="${PAD.L}" y="${PAD.T}" width="${geo.plotW}"
                  height="${geo.plotH}"></rect>
            ${refs}${axes}${area}${lines}${tags}
            <g class="wl-cross" hidden>
              <line class="wl-cross-line" y1="${PAD.T}" y2="${bottom}"></line>
              ${dots}
            </g>
            <rect class="wl-hit" x="${PAD.L}" y="${PAD.T}" width="${geo.plotW}"
                  height="${geo.plotH}"></rect>
        </svg>
        <div class="wl-tip" hidden></div>`;   // filled by the crosshair
    }

    function renderLegend(data) {
        return CHART_MODES[state.chartMode].series.map((name) => {
            const s = SERIES[name];
            const off = state.hiddenSeries.has(name);
            const unavailable = name === 'pe' && !(data.points || []).some((p) => p.pe != null);
            return `<button class="wl-leg wl-leg-${s.cls}${off ? ' wl-leg-off' : ''}" ` +
                   `data-series="${name}" title="Show or hide this line"${unavailable ? ' disabled' : ''}>` +
                   `<span class="wl-eye">${off ? '🚫' : '👁'}</span>` +
                   `<i class="wl-leg-key"></i>${DataGrid.escape(s.label)}` +
                   `${unavailable ? ' (n/a)' : ''}</button>`;
        }).join('');
    }

    function bindCrosshair(body, geo) {
        const svg = body.querySelector('.wl-svg');
        const cross = body.querySelector('.wl-cross');
        const line = body.querySelector('.wl-cross-line');
        const tip = body.querySelector('.wl-tip');
        if (!svg || !cross) return;

        const { pts, x } = geo;
        const nearest = (px) => {
            let best = 0, bestD = Infinity;
            for (let i = 0; i < pts.length; i++) {
                const d = Math.abs(x(pts[i].ts) - px);
                if (d < bestD) { bestD = d; best = i; }
            }
            return best;
        };

        const move = (clientX) => {
            const rect = svg.getBoundingClientRect();
            const p = pts[nearest(clientX - rect.left)];
            const cx = x(p.ts);

            cross.removeAttribute('hidden');
            line.setAttribute('x1', cx.toFixed(1));
            line.setAttribute('x2', cx.toFixed(1));

            const rows = [];
            cross.querySelectorAll('.wl-cross-dot').forEach((dot) => {
                const name = dot.dataset.key;
                const s = SERIES[name];
                const value = p[s.key];
                if (value == null) { dot.setAttribute('hidden', ''); return; }
                dot.removeAttribute('hidden');
                dot.setAttribute('cx', cx.toFixed(1));
                dot.setAttribute('cy', geo.y(name)(value).toFixed(1));
                rows.push(`<span class="wl-tip-row">` +
                          `<i class="wl-tip-key wl-key-${s.cls}"></i>` +
                          `<span class="wl-tip-label">${DataGrid.escape(s.label)}</span>` +
                          `<b>${DataGrid.escape(s.fmt(value))}</b></span>`);
            });

            tip.innerHTML = `<span class="wl-tip-date">${DataGrid.escape(dayLabel(p.ts))}</span>` +
                            rows.join('');
            tip.removeAttribute('hidden');

            // Rides the crosshair, flipping to its left near the right edge
            // so the card never runs off the panel or covers the line it is
            // reading. Vertically pinned near the top of the plot, out of the
            // way of both series.
            const width = tip.offsetWidth;
            const flip = cx + 14 + width > geo.width - 4;
            tip.style.left = Math.max(4, flip ? cx - 14 - width : cx + 14) + 'px';
        };

        svg.addEventListener('mousemove', (ev) => move(ev.clientX));
        // Touch matters here: this dock is the whole point of the chart on a
        // phone, where there is no hover to fall back on.
        svg.addEventListener('touchmove', (ev) => {
            if (!ev.touches.length) return;
            ev.preventDefault();
            move(ev.touches[0].clientX);
        }, { passive: false });

        const clear = () => {
            cross.setAttribute('hidden', '');
            tip.setAttribute('hidden', '');
        };
        svg.addEventListener('mouseleave', clear);
        svg.addEventListener('touchend', clear);
    }

    function chartNote(data) {
        const bits = [`${(data.points || []).length} daily closes`];
        if (data.kind === 'INDEX') {
            bits.push('indices carry no earnings, so no P/E line');
        } else if (data.eps_basis === 'reported') {
            bits.push('P/E is price ÷ the trailing-twelve-month EPS reported at each date');
        } else if (data.eps_basis === 'current') {
            bits.push(`P/E is price ÷ today's TTM EPS (${ratio(data.eps)}) held flat — ` +
                      'no clean quarterly series, so read the shape, not a re-rating');
        } else {
            bits.push('no EPS available, so no P/E line');
        }
        bits.push('prices from Yahoo daily candles');
        return bits.join(' · ');
    }

    function drawChart(body, data, attempt) {
        // The chart is sized in real pixels from the measured panel, and the
        // panel is measured the moment it is un-hidden — which can land
        // before layout has run and report 0, drawing the whole session into
        // a 320px stub. Wait a frame for a real width rather than shipping
        // that; a couple of frames is the most it ever takes.
        const measured = Math.floor(body.clientWidth);
        if (measured < 200 && (attempt || 0) < 5) {
            requestAnimationFrame(() => drawChart(body, data, (attempt || 0) + 1));
            return;
        }
        const width = Math.max(320, measured);
        const geo = buildGeometry(data, width);

        $('wlLegend').innerHTML = renderLegend(data);
        body.innerHTML =
            (geo ? renderChart(data, geo)
                 : '<div class="wl-drill-empty">Not enough history to draw this view.</div>') +
            `<div class="wl-drill-note">${DataGrid.escape(chartNote(data))}</div>`;

        if (geo) bindCrosshair(body, geo);
    }

    function syncChartTabs(data) {
        $('wlChartTabs').querySelectorAll('button').forEach((b) => {
            b.classList.toggle('active', b.dataset.mode === state.chartMode);
            // A P/E tab that draws nothing is worse than one you can't press.
            const noPe = data && data.kind === 'INDEX';
            b.disabled = noPe && b.dataset.mode !== 'price';
        });
        $('wlRanges').querySelectorAll('button').forEach((b) => {
            b.classList.toggle('active', b.dataset.range === state.range);
        });
    }

    // Daily closes for a symbol do not change while the panel is open, so a
    // series is fetched once per sitting. Without this, every ‹ › press paid
    // a round trip plus a yfinance download for a series it had already had
    // on screen a moment earlier — seconds of a blank panel to redraw the
    // chart the user just stepped away from.
    const HISTORY_CACHE_MAX = 40;

    function fetchHistory(symbol, range) {
        const key = `${symbol}|${range}`;
        const hit = state.history.get(key);
        if (hit) return Promise.resolve(hit);
        // Stepping quickly can ask for the same series twice before the first
        // answer lands;;both callers should wait on the one request.
        const pending = state.inflight.get(key);
        if (pending) return pending;

        const request = getJSON(
            `${API}/history?symbol=${encodeURIComponent(symbol)}&range=${range}`)
            .then((data) => {
                if (data && data.success) {
                    // Oldest-first eviction, so a long session on one tab
                    // cannot grow this without bound.
                    if (state.history.size >= HISTORY_CACHE_MAX) {
                        state.history.delete(state.history.keys().next().value);
                    }
                    state.history.set(key, data);
                }
                return data;
            })
            .finally(() => state.inflight.delete(key));

        state.inflight.set(key, request);
        return request;
    }

    // The two symbols ‹ › would land on, fetched quietly once the current one
    // is drawn. It turns the *first* press into a cache hit too, which is the
    // press that used to hurt.
    function prefetchNeighbours(symbol) {
        const i = state.rows.findIndex((r) => r.symbol === symbol);
        if (i < 0) return;
        [state.rows[i - 1], state.rows[i + 1]]
            .filter(Boolean)
            .forEach((row) => { fetchHistory(row.symbol, state.range).catch(() => {}); });
    }

    async function openDrill(symbol, mode) {
        if (mode) state.chartMode = mode;
        state.openSymbol = symbol;
        state.hiddenSeries.clear();

        const drill = $('wlDrill');
        const body = $('wlDrillBody');
        drill.hidden = false;
        document.body.classList.add('wl-drill-open');

        const row = state.rows.find((r) => r.symbol === symbol) || {};
        $('wlDrillSym').innerHTML =
            `<i class="wl-avatar">${DataGrid.escape(symbol.slice(0, 1))}</i>` +
            DataGrid.escape(symbol) +
            `<span class="wl-drill-co">${DataGrid.escape(row.company || '')}</span>`;
        syncNav();

        const cached = state.history.get(`${symbol}|${state.range}`);
        if (cached) {
            // Straight to the chart, no loading state at all — this is the
            // path every ‹ › press takes once the neighbours are prefetched.
            paintChart(body, cached, row);
            prefetchNeighbours(symbol);
            return;
        }

        // Nothing cached yet. Keep whatever chart is already on screen and
        // just mark it stale: replacing it with a "Loading…" box collapsed
        // the panel to a small bubble and back on every step, which read as
        // slower than the fetch actually was.
        if (!body.querySelector('.wl-svg')) {
            body.innerHTML = '<div class="wl-drill-empty">Loading…</div>';
        }
        drill.classList.add('wl-drill-loading');
        syncChartTabs(null);

        let data;
        try {
            data = await fetchHistory(symbol, state.range);
        } catch (e) {
            data = { success: false, error: e.message };
        }
        if (state.openSymbol !== symbol) return;   // stepped away while loading
        drill.classList.remove('wl-drill-loading');

        if (!data || !data.success) {
            body.innerHTML = `<div class="wl-drill-empty">${DataGrid.escape(
                (data && data.error) || 'No history available')}</div>`;
            return;
        }
        paintChart(body, data, row);
        prefetchNeighbours(symbol);
    }

    // Everything between "the series is in hand" and "it is on screen".
    function paintChart(body, payload, row) {
        // The 52-week rules come from the row, not the history call — they are
        // the same numbers the grid is showing, and drawing a separately
        // derived pair would invite them to disagree. Copied onto a shallow
        // clone rather than the cached payload, which outlives this row.
        const data = { ...payload, high52: row.high52, low52: row.low52 };
        state.chartData = data;
        if (data.kind === 'INDEX' && state.chartMode !== 'price') state.chartMode = 'price';
        syncChartTabs(data);
        drawChart(body, data);
        renderGrid();
    }

    function step(delta) {
        const i = state.rows.findIndex((r) => r.symbol === state.openSymbol);
        const next = state.rows[i + delta];
        if (next) openDrill(next.symbol);
    }

    function syncNav() {
        const i = state.rows.findIndex((r) => r.symbol === state.openSymbol);
        $('wlPrev').disabled = i <= 0;
        $('wlNext').disabled = i < 0 || i >= state.rows.length - 1;
    }

    function closeDrill() {
        if ($('wlDrill').hidden) return;
        state.openSymbol = null;
        state.chartData = null;
        $('wlDrill').hidden = true;
        $('wlLegend').innerHTML = '';
        document.body.classList.remove('wl-drill-open');
        renderGrid();
    }

    // ── wiring ───────────────────────────────────────────────────────

    function bind() {
        // Tab bar — one delegated handler; the chips are re-rendered often.
        $('wlTabBar').addEventListener('click', (e) => {
            const act = e.target.closest('.wl-tab-act');
            if (act) {
                e.stopPropagation();
                const tab = state.tabs.find((t) => t.id === Number(act.dataset.tab));
                if (!tab) return;
                if (act.dataset.act === 'rename') openModal(tab);
                else deleteTab(tab);
                return;
            }
            if (e.target.closest('#wlAddTab')) { openModal(null); return; }
            const chip = e.target.closest('.wl-tab');
            if (chip) selectTab(Number(chip.dataset.tab));
        });

        // Grid — the row's own buttons, then the P/E cell, then the row, in
        // that order of specificity so a button never also opens the chart.
        $('wlGrid').addEventListener('click', (e) => {
            const order = e.target.closest('[data-order]');
            if (order) {
                e.stopPropagation();
                openTicket(order.dataset.symbol, order.dataset.order);
                return;
            }
            const del = e.target.closest('[data-remove]');
            if (del) {
                e.stopPropagation();
                removeItem(Number(del.dataset.remove), del.dataset.symbol);
                return;
            }
            const move = e.target.closest('[data-move]');
            if (move) {
                e.stopPropagation();
                if (!move.disabled) openMoveMenu(move);
                return;
            }
            const tr = e.target.closest('tr[data-symbol]');
            if (!tr) return;
            const symbol = tr.dataset.symbol;
            if (symbol === state.openSymbol) { closeDrill(); return; }
            openDrill(symbol, e.target.closest('.wl-pe-cell') ? 'pe' : 'both');
        });

        // Type-ahead
        const search = $('wlSearch');
        let debounce = null;
        search.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => runSearch(search.value), 180);
        });
        search.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') { hideSuggestions(); return; }
            if (!state.suggestions.length) return;
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const n = state.suggestions.length;
                state.sugIndex = (state.sugIndex + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
                renderSuggestions();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                const pick = state.suggestions[Math.max(0, state.sugIndex)];
                if (pick) addSymbol(pick.symbol);
            }
        });
        $('wlSuggest').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-symbol]');
            if (btn) addSymbol(btn.dataset.symbol);
        });
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.wl-search-wrap')) hideSuggestions();
            if (!e.target.closest('#wlMoveMenu, [data-move]')) closeMoveMenu();
        });

        // Refresh forces the fundamentals cache too — the plain 60s poll
        // only re-prices, which is the cheap half.
        $('wlRefresh').addEventListener('click', () => loadRows({ force: true }));

        // Dialog
        $('wlModalSave').addEventListener('click', saveModal);
        $('wlModalCancel').addEventListener('click', closeModal);
        $('wlModalClose').addEventListener('click', closeModal);
        $('wlModalInput').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') saveModal();
            if (e.key === 'Escape') closeModal();
        });
        $('wlModalBack').addEventListener('click', (e) => {
            if (e.target === $('wlModalBack')) closeModal();
        });

        // Order ticket. The confirm button is the only path to /order.
        $('wlTicketSend').addEventListener('click', sendOrder);
        $('wlTicketCancel').addEventListener('click', closeTicket);
        $('wlTicketClose').addEventListener('click', closeTicket);
        $('wlTicketBack').addEventListener('click', (e) => {
            if (e.target === $('wlTicketBack')) closeTicket();
        });

        // Drilldown controls
        $('wlDrillClose').addEventListener('click', closeDrill);
        $('wlPrev').addEventListener('click', () => step(-1));
        $('wlNext').addEventListener('click', () => step(1));
        $('wlLegend').addEventListener('click', (e) => {
            const btn = e.target.closest('.wl-leg');
            if (!btn || btn.disabled || !state.chartData) return;
            const name = btn.dataset.series;
            if (state.hiddenSeries.has(name)) state.hiddenSeries.delete(name);
            else state.hiddenSeries.add(name);
            drawChart($('wlDrillBody'), state.chartData);
        });
        $('wlChartTabs').addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-mode]');
            if (!btn || btn.disabled) return;
            state.chartMode = btn.dataset.mode;
            state.hiddenSeries.clear();
            syncChartTabs(state.chartData);
            if (state.chartData) drawChart($('wlDrillBody'), state.chartData);
        });
        $('wlRanges').addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-range]');
            if (!btn || btn.dataset.range === state.range) return;
            state.range = btn.dataset.range;
            syncChartTabs(state.chartData);
            // openDrill re-reads state.range, so this both redraws the open
            // symbol and re-primes ‹ › for the range just chosen.
            if (state.openSymbol) openDrill(state.openSymbol);
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && $('wlMoveMenu')) { closeMoveMenu(); return; }
            // Escape backs out of the order, not out of the panel behind it —
            // and never places one, whatever has focus.
            if (!$('wlTicketBack').hidden) {
                if (e.key === 'Escape') closeTicket();
                return;
            }
            if ($('wlModalBack').hidden === false) return;  // the dialog owns Escape
            if ($('wlDrill').hidden) return;
            if (e.target.matches('input, textarea')) return;
            if (e.key === 'Escape') closeDrill();
            if (e.key === 'ArrowLeft') step(-1);
            if (e.key === 'ArrowRight') step(1);
        });

        // Redraw on resize: the chart is sized in real pixels, so it does
        // not reflow on its own.
        let resizeTimer = null;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                if (state.chartData && !$('wlDrill').hidden) {
                    drawChart($('wlDrillBody'), state.chartData);
                }
            }, 150);
        });

        // Pause the poll when the tab is in the background — nobody is
        // reading it, and it still costs a broker request every minute.
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                clearInterval(state.timer);
                state.timer = null;
            } else if (!state.timer) {
                loadRows();
                state.timer = setInterval(() => loadRows(), REFRESH_MS);
            }
        });
    }

    function init() {
        // Both of these are `position: fixed` and both must be portalled out
        // of .container, which carries `content-visibility: auto` — that
        // implies paint containment, which makes .container the containing
        // block for fixed descendants. Left inside it, the dock and the
        // dialog size and centre themselves against the whole scrolling page
        // instead of the viewport: with 36 rows on screen the "New tab"
        // dialog opened level with row 24, a third of the way down the list.
        document.body.appendChild($('wlDrill'));
        document.body.appendChild($('wlModalBack'));
        document.body.appendChild($('wlTicketBack'));
        bind();
        loadBrokers();
        loadTabs();
        state.timer = setInterval(() => loadRows(), REFRESH_MS);
    }

    global.watchlistInit = init;
})(window);
