/* algo_cpr.js — Algo page: CPR Logic tab (the /cpr-logic rule run live on NIFTY options). */
'use strict';

let _cprTimer = null;
let _cprMode = null;
let _cprBook = 'all';
let _cprHistory = [];

function _cprClearTimers() { clearTimeout(_cprTimer); _cprTimer = null; }

function _cprEsc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const _cprNum = v => v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });

// ── Status ───────────────────────────────────────────────────────────────────

function _cprFetchStatus() {
    _cprClearTimers();
    fetch('/api/algo/cpr-logic/status')
        .then(r => r.json())
        .then(d => { if (d.success) _cprRender(d); })
        .catch(() => {})
        .finally(() => {
            if (document.getElementById('algo-cpr-panel')?.classList.contains('active'))
                _cprTimer = setTimeout(_cprFetchStatus, 5000);
        });
}

function _cprRender(d) {
    const s = d.settings || {};
    _cprMode = s.mode;
    const badge = document.getElementById('cprModeBadge');
    badge.textContent = s.mode === 'live' ? (d.live_armed ? 'LIVE' : 'LIVE — NO ACCOUNT') : 'PAPER';
    badge.className = 'dg-badge ' + (s.mode === 'live' ? (d.live_armed ? 'dg-badge--neg' : 'dg-badge--warn') : 'dg-badge--info');
    document.querySelectorAll('#cprModeToggle .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.mode === s.mode));
    document.querySelectorAll('#cprLotModeToggle .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.lotMode === s.lot_mode));

    const run = document.getElementById('cprRunBadge');
    run.className = 'ag-badge ' + (d.running ? 'active' : 'inactive');
    document.getElementById('cprRunBadgeText').textContent = d.running ? 'Running' : (s.enabled ? 'Idle (runs 09:00–15:30)' : 'Stopped');

    const lots = document.getElementById('cprLots'), max = document.getElementById('cprMaxLots');
    const cap = document.getElementById('cprCapital');
    if (document.activeElement !== lots) lots.value = s.lots;
    if (document.activeElement !== max) max.value = s.max_lots;
    if (document.activeElement !== cap) cap.value = Math.round(s.start_capital);
    max.disabled = cap.disabled = s.lot_mode !== 'compound';
    const nl = d.next_lots || {};
    document.getElementById('cprNextLots').textContent = s.lot_mode === 'compound'
        ? `Next trade: paper ${nl.paper} lot(s) · live ${nl.live} lot(s) — booked paper ₹${_cprNum(d.booked?.paper)}, live ₹${_cprNum(d.booked?.live)}`
        : `Paper trades ${s.lots} lot(s); live accounts their BROKER_N_CPR_LOTS`;

    const acc = d.accounts || [];
    document.getElementById('cprAccounts').innerHTML = acc.length
        ? acc.map(a => `<div class="ag-stat"><div class="ag-stat-label">${_cprEsc(a.name)}</div>
             <div class="ag-stat-value ${a.stoppable && a.lots ? '' : 'ag-neg'}">${a.stoppable ? (a.lots ? a.lots + ' lot(s)' : 'no BROKER_' + a.instance + '_CPR_LOTS') : a.type + ' — not supported'}</div></div>`).join('')
        : `<div class="ag-empty">No account has BROKER_N_CPR_ACTIVE=true — paper only. Live needs one (zerodha / fyers) with BROKER_N_CPR_LOTS.</div>`;

    const st = d.state || {};
    const ch = st.chart;
    document.getElementById('cprLevels').textContent = ch
        ? `${ch.cpr_type} CPR ${_cprNum(ch.levels.bc)}–${_cprNum(ch.levels.tc)} · PDH ${_cprNum(ch.levels.pdh)} / R1 ${_cprNum(ch.levels.r1)} · PDL ${_cprNum(ch.levels.pdl)} / S1 ${_cprNum(ch.levels.s1)} · ${ch.boxes} boxes`
          + (st.spot ? ` · NIFTY ${_cprNum(st.spot)}` : '')
        : (st.day ? 'Waiting for the 09:15 candle to close' : '');
    document.getElementById('cprLastUpd').textContent = st.updated ? 'updated ' + st.updated : '';
    _cprRenderTrades((st.day && st.day.trades) || []);
    const log = (st.log || []).slice(-30).reverse();
    document.getElementById('cprLog').innerHTML = log.map(l => `<div><b>${_cprEsc(l.t)}</b> ${_cprEsc(l.text)}</div>`).join('');
}

const _CPR_STATUS = { armed: ['Waiting for entry', 'warn'], open: ['In trade', 'info'], closed: ['Closed', 'neutral'], expired: ['No fill', 'neutral'] };

function _cprRenderTrades(trades) {
    const rows = trades.map(t => {
        const legs = Object.values(t.legs || {});
        const pnl = legs.reduce((a, l) => a + (l.pnl || 0), 0);
        return Object.assign({}, t, {
            leg: t.option ? `NIFTY ${t.option.strike} ${t.option.type}` : (t.missed ? 'missed' : '—'),
            lots: legs.map(l => `${l.name}: ${l.lots ?? '—'}`).join(', ') || '—',
            prem: legs.length ? legs.map(l => `${_cprNum(l.entry)} → ${l.exit != null ? _cprNum(l.exit) : '…'}`
                                         + (/backstop/.test(l.exit_reason || '') ? ' (backstop SL)' : '')).join(', ') : '—',
            pnl_inr: legs.some(l => l.pnl != null) ? pnl : null,
        });
    });
    document.getElementById('cprTrades').innerHTML = DataGrid.render({
        rows, empty: 'No setup yet today',
        columns: [
            { key: 'n', label: '#' },
            { key: 'status', label: 'Status', format: (v, t) => (t.missed ? 'Missed' : (_CPR_STATUS[v] || [v])[0]),
              badge: (v, t) => t.missed ? 'neg' : (_CPR_STATUS[v] || [, 'neutral'])[1] },
            { key: 'mode', label: 'Book', format: v => v ? v.toUpperCase() : '—', badge: v => v === 'live' ? 'neg' : 'info' },
            { key: 'trade', label: 'Side', tone: v => v === 'SELL' ? 'neg' : 'pos', strong: true },
            { key: 'strategy', label: 'Setup' },
            { key: 'setup_time', label: 'Setup at' },
            { key: 'entry', label: 'Entry', format: _cprNum },
            { key: 'stop', label: 'SL', format: (v, t) => _cprNum(v) + (t.locked ? ' 🔒' : '') },
            { key: 'target', label: 'Target', format: (v, t) => _cprNum(v) + (t.locked_at ? ` · reached ${t.locked_at} 🔒` : '') },
            { key: 'fill', label: 'Index fill', format: (v, t) => v == null ? '—' : `${_cprNum(v)} @ ${t.fill_time}` },
            { key: 'leg', label: 'Option' },
            { key: 'lots', label: 'Lots' },
            { key: 'prem', label: 'Premium' },
            { key: 'pts', label: 'Pts', format: (v, t) => v == null ? '—'
                  : `${v > 0 ? '+' : ''}${_cprNum(v)} (${t.result}, out ${_cprNum(t.exit)} ${t.exit_time})`, tone: DataGrid.sign },
            { key: 'pnl_inr', label: 'P&L', strong: true, format: v => v == null ? '—' : DataGrid.inr(v), tone: DataGrid.sign },
        ],
    });
}

// ── History ──────────────────────────────────────────────────────────────────

function _cprFetchHistory() {
    fetch('/api/algo/cpr-logic/history')
        .then(r => r.json())
        .then(d => { if (d.success) { _cprHistory = d.trades || []; _cprRenderHistory(); } })
        .catch(() => {});
}

function _cprSetBook(book) {
    _cprBook = book;
    document.querySelectorAll('#cprBookTabs .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.book === book));
    _cprRenderHistory();
}

function _cprRenderHistory() {
    const rows = _cprBook === 'all' ? _cprHistory : _cprHistory.filter(t => t.mode === _cprBook);
    const net = rows.reduce((a, t) => a + (t.pnl || 0), 0), wins = rows.filter(t => (t.pnl || 0) > 0).length;
    const sum = document.getElementById('cprHistSummary');
    sum.textContent = rows.length ? `${rows.length} trades · ${Math.round(100 * wins / rows.length)}% won · ${net >= 0 ? '+' : '−'}₹${Math.abs(Math.round(net)).toLocaleString('en-IN')}` : '';
    sum.className = net > 0 ? 'ag-pos' : net < 0 ? 'ag-neg' : '';
    document.getElementById('cprHistBody').innerHTML = DataGrid.render({
        rows, empty: 'No completed trades',
        columns: [
            { key: 'date', label: 'Date' },
            { key: 'mode', label: 'Book', format: v => (v || '').toUpperCase(), badge: v => v === 'live' ? 'neg' : 'info' },
            { key: 'side', label: 'Side', tone: v => v === 'SELL' ? 'neg' : 'pos', strong: true },
            { key: 'strategy', label: 'Setup' },
            { key: 'fill_time', label: 'In' },
            { key: 'exit_time', label: 'Out', format: (v, t) => (v || '—') + (t.locked_at ? ` (target ${t.locked_at} 🔒)` : '') },
            { key: 'index_fill', label: 'Index in', format: _cprNum },
            { key: 'index_exit', label: 'Index out', format: _cprNum },
            { key: 'pts', label: 'Pts', format: v => v == null ? '—' : (v > 0 ? '+' : '') + _cprNum(v), tone: DataGrid.sign },
            { key: 'result', label: 'Result', badge: v => v === 'Target' ? 'pos' : v === 'SL' || v === 'Backstop' ? 'neg' : 'warn',
              title: (_, t) => t.note || Object.values(t.legs || {}).map(l => l.exit_reason).filter(Boolean).join('; ') },
            { key: 'strike', label: 'Option', format: (v, t) => `${v} ${t.option_type}` },
            { key: 'legs', label: 'Lots · premium', format: v => Object.values(v || {}).map(l => `${l.lots}× ${_cprNum(l.entry)}→${_cprNum(l.exit)}`).join(', ') || '—' },
            { key: 'pnl', label: 'P&L', strong: true, format: v => v == null ? '—' : DataGrid.inr(v), tone: DataGrid.sign },
        ],
    });
}

// ── Actions ──────────────────────────────────────────────────────────────────

function _cprSave(body) {
    return fetch('/api/algo/cpr-logic/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(r => r.json()).then(d => {
        if (!d.success) alert('Not saved: ' + (d.error || 'unknown error'));
        _cprFetchStatus();
    }).catch(e => alert('Request failed: ' + e));
}

function _cprSetMode(mode) {
    if (mode === _cprMode) return;
    if (mode === 'live' && !confirm('Switch CPR Logic to LIVE? From the next entry, NIFTY options will be BOUGHT with real orders at every account with BROKER_N_CPR_ACTIVE=true.')) return;
    if (mode === 'paper' && !confirm('Switch CPR Logic to PAPER? New entries are only recorded. A live position already open keeps being managed until it is flat.')) return;
    _cprSave({ mode });
}

function _cprStart() {
    fetch('/api/algo/cpr-logic/start', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Start failed: ' + (d.error || '')); _cprFetchStatus(); });
}

function _cprStop() {
    if (!confirm('Stop CPR Logic? It stays stopped (also after a restart) until Start. A live position is NOT squared off — its backstop SL-M stays at the broker.')) return;
    fetch('/api/algo/cpr-logic/stop', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Stop failed: ' + (d.error || '')); _cprFetchStatus(); });
}
