/* algo.js — NIFTY Weekly Straddle UI */
'use strict';

let _algoTimer = null;

function algoLoad() {
    _fetchStatus();
}

// ── Fetch & render ────────────────────────────────────────────────────────────

function _fetchStatus() {
    fetch('/api/algo/straddle/status')
        .then(r => r.json())
        .then(data => {
            _renderState(data);
            clearTimeout(_algoTimer);
            _algoTimer = setTimeout(_fetchStatus, data.active ? 5000 : 30000);
        })
        .catch(() => {
            clearTimeout(_algoTimer);
            _algoTimer = setTimeout(_fetchStatus, 30000);
        });
}

function _renderState(data) {
    const state  = data.state || {};
    const live   = data.live  || null;
    const active = !!data.active;

    // Badge
    const badge = document.getElementById('algoBadge');
    badge.className = 'ag-badge ' + (active ? 'active' : 'inactive');
    document.getElementById('algoBadgeText').textContent = active ? 'Active' : 'Inactive';

    // Last updated
    document.getElementById('algoLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Status grid
    _setText('algoStatusVal',  active ? 'Active' : 'Inactive');
    _setText('algoExpiry',     state.current_expiry  || '—');
    _setText('algoNextExpiry', state.next_expiry      || '—');
    _setText('algoUnderlying', state.underlying_at_entry ? '₹' + _num(state.underlying_at_entry) : '—');
    _setText('algoEntryTime',  state.entry_time ? _fmtTime(state.entry_time) : '—');
    _setText('algoLots',       state.lots && state.lot_size ? `${state.lots} × ${state.lot_size}` : '—');

    // Legs
    _renderLegs(state, live);

    // P&L card
    _setText('agCombEntry',   state.combined_entry != null ? state.combined_entry.toFixed(2) : '—');
    _setText('agCombCurrent', live ? live.combined_current.toFixed(2) : (active ? '…' : '—'));
    _setText('agSL',          state.sl_trigger != null ? state.sl_trigger.toFixed(2) : '—');

    if (live) {
        const dist = live.sl_distance;
        const distEl = document.getElementById('agSLDist');
        distEl.textContent = dist.toFixed(2);
        distEl.className = 'ag-stat-value ' + (dist < 5 ? 'ag-neg' : dist < 15 ? 'ag-warn' : 'ag-pos');
        _setPnl('agPnlLot',   live.pnl_per_lot);
        _setPnl('agPnlTotal', live.pnl_total);
    } else {
        _setText('agSLDist', '—');
        document.getElementById('agSLDist').className = 'ag-stat-value ag-muted';
        _setText('agPnlLot',   '—');
        _setText('agPnlTotal', '—');
    }

    // Buttons
    document.getElementById('algoEnterBtn').disabled = active;
    document.getElementById('algoExitBtn').disabled  = !active;
}

function _renderLegs(state, live) {
    const tbody = document.getElementById('algoLegsBody');
    if (!state.active) {
        tbody.innerHTML = '<tr><td colspan="6" class="ag-empty">No active trade</td></tr>';
        return;
    }
    const lot_size = state.lot_size || 1;
    const rows = [
        { type: 'CE', cls: 'ce-row', strike: state.ce_strike, sym: state.ce_kite_tradingsymbol, entry: state.ce_entry_ltp, curr: live ? live.ce_ltp : null },
        { type: 'PE', cls: 'pe-row', strike: state.pe_strike, sym: state.pe_kite_tradingsymbol, entry: state.pe_entry_ltp, curr: live ? live.pe_ltp : null },
    ];
    tbody.innerHTML = rows.map(r => {
        const currStr = r.curr != null ? r.curr.toFixed(2) : '…';
        const pnl     = r.curr != null ? (r.entry - r.curr) * lot_size : null;
        const pnlStr  = pnl != null ? (pnl >= 0 ? '+₹' : '-₹') + Math.abs(pnl).toFixed(2) : '—';
        const pnlCls  = pnl == null ? '' : pnl >= 0 ? 'ag-pos' : 'ag-neg';
        return `<tr class="${r.cls}">
            <td>${r.type}</td>
            <td>${r.strike ?? '—'}</td>
            <td class="ag-sym">${r.sym || '—'}</td>
            <td>${r.entry != null ? r.entry.toFixed(2) : '—'}</td>
            <td>${currStr}</td>
            <td class="${pnlCls}">${pnlStr}</td>
        </tr>`;
    }).join('');
}

// ── Preview ───────────────────────────────────────────────────────────────────

function algoPreview(btn) {
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = 'Loading…';
    _hideMsg();
    fetch('/api/algo/straddle/preview')
        .then(r => r.json())
        .then(d => {
            if (!d.success) { _showMsg('error', d.error || 'Preview failed'); return; }
            _showMsg('success',
                `Preview — NIFTY ₹${d.underlying?.toFixed(2)} | Expiry: ${d.current_expiry} | ` +
                `CE ${d.ce_strike} @ ₹${d.ce_ltp?.toFixed(2)} | ` +
                `PE ${d.pe_strike} @ ₹${d.pe_ltp?.toFixed(2)} | ` +
                `Combined ₹${d.combined_premium} | SL ₹${d.sl_trigger} | ` +
                `Lot ${d.lot_size} | δ ${d.delta_target}`
            );
        })
        .catch(e => _showMsg('error', 'Preview failed: ' + e))
        .finally(() => { btn.disabled = false; btn.textContent = orig; });
}

// ── Enter ─────────────────────────────────────────────────────────────────────

function algoEnterNow(btn) {
    if (!confirm('Place NIFTY straddle orders now? Orders will be sent to the broker immediately.')) return;
    _setBusy(btn, 'Placing…');
    _hideMsg();
    fetch('/api/algo/straddle/enter', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { _showMsg('error', d.error || 'Entry failed'); _unbusy(btn, 'Enter Straddle Now'); return; }
            _showMsg('success', 'Straddle entered — SL monitor is running');
            clearTimeout(_algoTimer);
            _fetchStatus();
        })
        .catch(e => { _showMsg('error', 'Request failed: ' + e); _unbusy(btn, 'Enter Straddle Now'); });
}

// ── Exit ──────────────────────────────────────────────────────────────────────

function algoExitNow(btn) {
    if (!confirm('Exit the active straddle? Both legs will be squared off at market price.')) return;
    _setBusy(btn, 'Exiting…');
    _hideMsg();
    fetch('/api/algo/straddle/exit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { _showMsg('error', d.error || 'Exit failed'); _unbusy(btn, 'Exit Now'); return; }
            _showMsg('success', 'Straddle exited (' + (d.reason || 'MANUAL') + ')');
            clearTimeout(_algoTimer);
            _fetchStatus();
        })
        .catch(e => { _showMsg('error', 'Request failed: ' + e); _unbusy(btn, 'Exit Now'); });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function _setPnl(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    if (val == null) { el.textContent = '—'; el.className = 'ag-stat-value ag-muted'; return; }
    el.textContent = (val >= 0 ? '+₹' : '-₹') + Math.abs(val).toFixed(2);
    el.className = 'ag-stat-value ' + (val >= 0 ? 'ag-pos' : 'ag-neg');
}

function _num(v) {
    return Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

function _fmtTime(iso) {
    try {
        return new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
}

function _showMsg(type, text) {
    const el = document.getElementById('algoMsg');
    el.className = 'ag-msg ' + type;
    el.textContent = text;
    el.style.display = 'block';
}

function _hideMsg() {
    const el = document.getElementById('algoMsg');
    el.style.display = 'none';
}

function _setBusy(btn, label) {
    btn.disabled = true;
    btn._orig = btn.textContent;
    btn.textContent = label;
}

function _unbusy(btn, label) {
    btn.disabled = false;
    btn.textContent = label || btn._orig || label;
}
