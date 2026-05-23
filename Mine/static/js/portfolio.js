/* ================================================================
   Portfolio — broker dropdown at top, per-broker positions & holdings
================================================================ */
(function () {
    'use strict';

    let _activeBroker = null;
    let _activeTab = 'positions';
    let _sortState = {};   // { tableId: { col, dir } }
    let _lastSortTh = {};  // { tableId: th element }

    const $ = id => document.getElementById(id);

    // ── Boot ───────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', () => {
        setupTabs();
        $('pfRefreshBtn').addEventListener('click', () => loadPortfolio(true));
        attachSortListeners();
        setupOrderModal();
        loadBrokers();
    });

    // ── Tabs ───────────────────────────────────────────────────────
    function setupTabs() {
        document.querySelectorAll('.pf-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                _activeTab = btn.dataset.tab;
                document.querySelectorAll('.pf-tab').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                $('pfPanelPositions').classList.toggle('hidden', _activeTab !== 'positions');
                $('pfPanelHoldings').classList.toggle('hidden', _activeTab !== 'holdings');
            });
        });
    }

    // ── Sort ───────────────────────────────────────────────────────
    function attachSortListeners() {
        ['pfPositionsTable', 'pfHoldingsTable'].forEach(id => {
            const tbl = $(id);
            if (!tbl) return;
            tbl.querySelectorAll('th[data-col]').forEach(th => {
                const icon = document.createElement('span');
                icon.className = 'pf-sort-icon';
                icon.textContent = '⇅';
                th.appendChild(icon);
            });
            tbl.addEventListener('click', e => {
                const th = e.target.closest('th[data-col]');
                if (th) sortTable(id, parseInt(th.dataset.col, 10));
            });
        });
    }

    function sortTable(tableId, colIdx) {
        const tbl = $(tableId);
        if (!tbl) return;
        const tbody = tbl.querySelector('tbody');
        const th = tbl.querySelector(`th[data-col="${colIdx}"]`);
        if (!th || !tbody) return;

        const prev = _sortState[tableId] || { col: -1, dir: 'none' };
        const next = prev.col === colIdx && prev.dir === 'asc' ? 'desc' : 'asc';
        _sortState[tableId] = { col: colIdx, dir: next };

        const prevTh = _lastSortTh[tableId];
        if (prevTh && prevTh !== th) {
            prevTh.classList.remove('sort-asc', 'sort-desc');
            const pi = prevTh.querySelector('.pf-sort-icon');
            if (pi) pi.textContent = '⇅';
        }
        th.classList.remove('sort-asc', 'sort-desc');
        th.classList.add(next === 'asc' ? 'sort-asc' : 'sort-desc');
        const icon = th.querySelector('.pf-sort-icon');
        if (icon) icon.textContent = next === 'asc' ? '↑' : '↓';
        _lastSortTh[tableId] = th;

        const rows = Array.from(tbody.querySelectorAll('tr:not(.pf-error-row)'));
        rows.sort((a, b) => {
            const aText = a.cells[colIdx]?.textContent.replace(/[₹%,+]/g, '').trim() || '';
            const bText = b.cells[colIdx]?.textContent.replace(/[₹%,+]/g, '').trim() || '';
            const aNum = parseFloat(aText);
            const bNum = parseFloat(bText);
            if (!isNaN(aNum) && !isNaN(bNum) && colIdx >= 1) {
                return next === 'asc' ? aNum - bNum : bNum - aNum;
            }
            return next === 'asc' ? aText.localeCompare(bText) : bText.localeCompare(aText);
        });
        rows.forEach(r => tbody.appendChild(r));
    }

    // ── Load broker list ───────────────────────────────────────────
    async function loadBrokers() {
        try {
            const res = await fetch('/api/portfolio/brokers');
            const json = await res.json();
            if (!json.success || !json.brokers.length) {
                showError('No active brokers configured.');
                return;
            }

            const sel = $('pfBrokerSelect');
            sel.innerHTML = '';
            json.brokers.forEach(b => {
                const opt = document.createElement('option');
                opt.value = b.broker_id;
                opt.textContent = `${b.icon} ${b.broker_name}`;
                sel.appendChild(opt);
            });

            _activeBroker = json.brokers[0].broker_id;
            await loadPortfolio();

            sel.addEventListener('change', () => {
                _activeBroker = sel.value;
                loadPortfolio();
            });
        } catch (err) {
            showError('Failed to load brokers: ' + err.message);
        }
    }

    // ── Load portfolio for active broker ───────────────────────────
    async function loadPortfolio(flash = false) {
        if (!_activeBroker) return;
        setLoading(true);
        if (flash) {
            const btn = $('pfRefreshBtn');
            if (btn) { btn.style.opacity = '0.4'; btn.disabled = true; }
        }
        try {
            const res = await fetch(`/api/portfolio/all?broker_id=${encodeURIComponent(_activeBroker)}`);
            const json = await res.json();
            if (!json.success) throw new Error(json.error || 'API error');
            renderAll(json);
        } catch (err) {
            showError(err.message);
        } finally {
            setLoading(false);
            if (flash) {
                const btn = $('pfRefreshBtn');
                if (btn) { btn.style.opacity = ''; btn.disabled = false; }
            }
        }
    }

    // ── Render ─────────────────────────────────────────────────────
    function renderAll(data) {
        // API returns arrays of broker objects — take the first (filtered) one
        const posEntry = (data.positions || [])[0];
        const hldEntry = (data.holdings || [])[0];

        const positions = posEntry?.data || [];
        const posError  = posEntry?.error || null;
        const holdings  = hldEntry?.data || [];
        const hldError  = hldEntry?.error || null;

        renderPositions(positions, posError);
        renderHoldings(holdings, hldError);
        updateStats(positions, holdings);
    }

    // ── Positions ──────────────────────────────────────────────────
    function renderPositions(rows, error) {
        const tbody = $('pfPositionsBody');
        tbody.innerHTML = '';

        if (error && !rows.length) {
            tbody.appendChild(makeErrorRow(7, error));
            $('pfPositionsEmpty').classList.add('hidden');
            $('pfPositionsTable').classList.remove('hidden');
            return;
        }

        rows.forEach(pos => {
            const pnl = parseFloat(pos.pnl) || 0;
            const avg = parseFloat(pos.avg_price) || 0;
            const ltp = parseFloat(pos.ltp) || 0;
            const chg = avg ? (ltp - avg) / avg * 100 : 0;

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>
                    <span class="pf-sym">${pos.symbol}</span>
                    ${pos.exchange ? `<span class="pf-exch">${pos.exchange}</span>` : ''}
                </td>
                <td><span class="pf-product">${pos.product || '—'}</span></td>
                <td class="col-r">${fmt(pos.qty, 0)}</td>
                <td class="col-r">${fmt(avg)}</td>
                <td class="col-r">${fmt(ltp)}</td>
                <td class="col-r">${chgCell(chg)}</td>
                <td class="col-r">${pnlCell(pnl)}</td>
            `;
            tbody.appendChild(tr);
        });

        $('pfPositionsEmpty').classList.toggle('hidden', rows.length > 0);
        $('pfPositionsTable').classList.toggle('hidden', rows.length === 0 && !error);
    }

    // ── Holdings ───────────────────────────────────────────────────
    function renderHoldings(rows, error) {
        const tbody = $('pfHoldingsBody');
        tbody.innerHTML = '';

        if (error && !rows.length) {
            tbody.appendChild(makeErrorRow(8, error));
            $('pfHoldingsEmpty').classList.add('hidden');
            $('pfHoldingsTable').classList.remove('hidden');
            return;
        }

        rows.forEach(h => {
            const pnl = parseFloat(h.pnl) || 0;
            const dc  = parseFloat(h.day_change) || 0;
            const dcp = parseFloat(h.day_change_pct) || 0;

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><span class="pf-sym">${h.symbol}</span></td>
                <td class="col-r">${fmt(h.qty, 0)}</td>
                <td class="col-r">${fmt(h.avg_price)}</td>
                <td class="col-r">${fmt(h.ltp)}</td>
                <td class="col-r">₹${fmt(h.current_value)}</td>
                <td class="col-r">${pnlCell(pnl)}</td>
                <td class="col-r">${pnlPctCell(h.pnl_pct)}</td>
                <td class="col-r">${dayChgCell(dc, dcp)}</td>
                <td class="col-action"></td>
            `;
            const actionTd = tr.lastElementChild;
            const buyBtn   = document.createElement('button');
            buyBtn.className = 'pf-buy-btn';
            buyBtn.title     = `Buy ${h.symbol}`;
            buyBtn.innerHTML = '+';
            buyBtn.addEventListener('click', () => openOrderModal(h));
            actionTd.appendChild(buyBtn);
            tbody.appendChild(tr);
        });

        $('pfHoldingsEmpty').classList.toggle('hidden', rows.length > 0);
        $('pfHoldingsTable').classList.toggle('hidden', rows.length === 0 && !error);
    }

    // ── Stats bar ──────────────────────────────────────────────────
    function updateStats(positions, holdings) {
        $('pfStatPositions').textContent = positions.length;
        $('pfStatHoldings').textContent = holdings.length;

        const dayPnl = positions.reduce((s, p) => s + (parseFloat(p.pnl) || 0), 0);
        const el = $('pfDayPnl');
        el.textContent = signedFmt(dayPnl);
        el.className = 'pf-pnl-inline ' + (dayPnl > 0 ? 'pos' : dayPnl < 0 ? 'neg' : '');
    }

    // ── Loading / error ────────────────────────────────────────────
    function setLoading(on) {
        $('pfLoading').classList.toggle('hidden', !on);
        if (on) {
            $('pfPositionsBody').innerHTML = '';
            $('pfHoldingsBody').innerHTML = '';
        }
    }

    function showError(msg) {
        $('pfPositionsBody').appendChild(makeErrorRow(7, msg));
        $('pfPositionsEmpty').classList.add('hidden');
        $('pfPositionsTable').classList.remove('hidden');
    }

    function makeErrorRow(cols, msg) {
        const tr = document.createElement('tr');
        tr.className = 'pf-error-row';
        tr.innerHTML = `<td colspan="${cols}">${msg}</td>`;
        return tr;
    }

    // ── Formatters ─────────────────────────────────────────────────
    function fmt(val, dec = 2) {
        const n = parseFloat(val);
        if (isNaN(n)) return '—';
        return n.toLocaleString('en-IN', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    }

    function signedFmt(n) {
        if (isNaN(n)) return '—';
        const abs = Math.abs(n);
        const sign = n >= 0 ? '+₹' : '-₹';
        if (abs >= 1e5) return sign + fmt(abs / 1e5) + 'L';
        if (abs >= 1e3) return sign + fmt(abs / 1e3) + 'K';
        return sign + fmt(abs);
    }

    function pnlCell(n) {
        if (isNaN(n)) return '<span class="pf-pnl neu">—</span>';
        const cls = n > 0 ? 'pos' : n < 0 ? 'neg' : 'neu';
        const sign = n > 0 ? '+' : '';
        return `<span class="pf-pnl ${cls}">${sign}${fmt(n)}</span>`;
    }

    function chgCell(n) {
        if (isNaN(n) || n === 0) return '<span class="pf-chg">—</span>';
        const cls = n > 0 ? 'pos' : 'neg';
        const sign = n > 0 ? '+' : '';
        return `<span class="pf-chg ${cls}">${sign}${fmt(n)}%</span>`;
    }

    function dayChgCell(dc, dcp) {
        if (!dc && !dcp) return '<span class="pf-chg">—</span>';
        const cls = dc > 0 ? 'pos' : dc < 0 ? 'neg' : '';
        const sign = dcp > 0 ? '+' : '';
        return `<span class="pf-chg ${cls}">${sign}${fmt(dcp)}%</span>`;
    }

    function pnlPctCell(val) {
        const n = parseFloat(val);
        if (isNaN(n)) return '<span class="pf-pnl-pct neu">—</span>';
        const cls = n > 0 ? 'pos' : n < 0 ? 'neg' : 'neu';
        const sign = n > 0 ? '+' : '';
        return `<span class="pf-pnl-pct ${cls}">${sign}${fmt(n)}%</span>`;
    }

    // ── Order Modal ────────────────────────────────────────────────
    let _orderContext = null;   // { symbol, exchange, sec_id, ltp }

    function setupOrderModal() {
        $('pfOrderClose').addEventListener('click', closeOrderModal);
        $('pfOrderCancel').addEventListener('click', closeOrderModal);
        $('pfOrderOverlay').addEventListener('click', e => {
            if (e.target === $('pfOrderOverlay')) closeOrderModal();
        });

        document.querySelectorAll('.pf-ot-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.pf-ot-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const isLimit = btn.dataset.ot === 'LIMIT';
                $('pfPriceField').style.display = isLimit ? '' : 'none';
            });
        });

        $('pfOrderSubmit').addEventListener('click', submitCncOrder);
    }

    function openOrderModal(holding) {
        _orderContext = holding;
        $('pfOrderSymbol').textContent = holding.symbol;
        $('pfOrderQty').value   = 1;
        $('pfOrderPrice').value = holding.ltp ? parseFloat(holding.ltp).toFixed(2) : '';

        // Reset to LIMIT mode
        document.querySelectorAll('.pf-ot-btn').forEach(b => b.classList.remove('active'));
        document.querySelector('.pf-ot-btn[data-ot="LIMIT"]').classList.add('active');
        $('pfPriceField').style.display = '';

        setModalStatus('', '');
        $('pfOrderSubmit').disabled = false;
        $('pfOrderOverlay').classList.remove('hidden');
        $('pfOrderQty').focus();
    }

    function closeOrderModal() {
        $('pfOrderOverlay').classList.add('hidden');
        _orderContext = null;
    }

    function setModalStatus(msg, type) {
        const el = $('pfOrderStatus');
        el.textContent = msg;
        el.className = 'pf-modal-status' + (msg ? ` ${type}` : ' hidden');
    }

    async function submitCncOrder() {
        if (!_orderContext || !_activeBroker) return;
        const qty   = parseInt($('pfOrderQty').value, 10);
        const price = parseFloat($('pfOrderPrice').value) || 0;
        const activeOt = document.querySelector('.pf-ot-btn.active');
        const orderType = activeOt ? activeOt.dataset.ot : 'LIMIT';

        if (!qty || qty <= 0) {
            setModalStatus('Enter a valid quantity.', 'err'); return;
        }
        if (orderType === 'LIMIT' && price <= 0) {
            setModalStatus('Enter a valid limit price.', 'err'); return;
        }

        const btn = $('pfOrderSubmit');
        btn.disabled = true;
        setModalStatus('Placing order…', '');

        try {
            const res  = await fetch('/api/portfolio/cnc-order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    broker_id:  _activeBroker,
                    symbol:     _orderContext.symbol,
                    exchange:   _orderContext.exchange || 'NSE',
                    qty,
                    price:      orderType === 'LIMIT' ? price : 0,
                    order_type: orderType,
                    sec_id:     _orderContext.sec_id || '',
                }),
            });
            const json = await res.json();
            if (json.success) {
                setModalStatus(`Order placed! ID: ${json.order_id}`, 'ok');
                setTimeout(closeOrderModal, 2000);
            } else {
                setModalStatus(json.error || 'Order failed', 'err');
                btn.disabled = false;
            }
        } catch (err) {
            setModalStatus('Network error: ' + err.message, 'err');
            btn.disabled = false;
        }
    }
})();
