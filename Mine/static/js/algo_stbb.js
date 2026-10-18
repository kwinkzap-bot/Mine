/* algo_stbb.js — Algo page: ST + BB + RSI tab (the Backtest page's strategy run live on NIFTY options). */
'use strict';

let _stbbTimer = null;
let _stbbMode = null;
let _stbbBook = 'all';
let _stbbHistory = [];

function _stbbClearTimers() { clearTimeout(_stbbTimer); _stbbTimer = null; }

function _stbbEsc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const _stbbNum = v => v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
const _stbbInr = v => v == null ? '—' : (v < 0 ? '−' : '') + '₹' + Math.abs(Math.round(v)).toLocaleString('en-IN');

// ── Status ───────────────────────────────────────────────────────────────────

function _stbbFetchStatus() {
    _stbbClearTimers();
    fetch('/api/algo/st-bb-rsi/status')
        .then(r => r.json())
        .then(d => { if (d.success) _stbbRender(d); })
        .catch(() => {})
        .finally(() => {
            if (document.getElementById('algo-stbb-panel')?.classList.contains('active'))
                _stbbTimer = setTimeout(_stbbFetchStatus, 5000);
        });
}

function _stbbRender(d) {
    const s = d.settings || {};
    _stbbMode = s.mode;
    const badge = document.getElementById('stbbModeBadge');
    badge.textContent = s.mode === 'live' ? (d.live_armed ? 'LIVE' : 'LIVE — NO ACCOUNT') : 'PAPER';
    badge.className = 'dg-badge ' + (s.mode === 'live' ? (d.live_armed ? 'dg-badge--neg' : 'dg-badge--warn') : 'dg-badge--info');
    document.querySelectorAll('#stbbModeToggle .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.mode === s.mode));

    const run = document.getElementById('stbbRunBadge');
    run.className = 'ag-badge ' + (d.running ? 'active' : 'inactive');
    document.getElementById('stbbRunBadgeText').textContent = d.running ? 'Running' : (s.enabled ? 'Idle (runs 09:00–15:30)' : 'Stopped');

    const set = (id, v) => { const el = document.getElementById(id); if (el && document.activeElement !== el) el.value = v; };
    set('stbbDirection', s.direction); set('stbbMaxTrades', s.max_trades); set('stbbPremium', Math.round(s.premium));
    document.getElementById('stbbSizing').textContent =
        `Size is the flags: a live order is each account's BROKER_N_STBB_LOTS lots (at accounts with BROKER_N_STBB_ACTIVE=true); a paper trade takes ${d.paper_lots} lot(s).`;
    document.getElementById('stbbRule').textContent =
        `3-minute candles · RSI(${s.rsi_len}) ${s.rsi_level}/${100 - s.rsi_level} · SuperTrend(${s.st_len}, ${s.st_mult}) · BB(${s.bb_len}, ${s.bb_mult}) · R1/S1 of yesterday · `
        + `no target, no stop — exits when SuperTrend turns against the trade, square-off 15:14`
        + (s.backstop_pct ? ` · broker backstop SL-M ${s.backstop_pct}% below the fill` : '');

    const acc = d.accounts || [];
    document.getElementById('stbbAccounts').innerHTML = acc.length
        ? acc.map(a => `<div class="ag-stat"><div class="ag-stat-label">${_stbbEsc(a.name)}</div>
             <div class="ag-stat-value ${a.stoppable && a.lots ? '' : 'ag-neg'}">${a.stoppable ? (a.lots ? a.lots + ' lot(s)' : 'no BROKER_' + a.instance + '_STBB_LOTS') : a.type + ' — not supported'}</div></div>`).join('')
        : `<div class="ag-empty">No account has BROKER_N_STBB_ACTIVE=true — paper only. Live needs one (zerodha / fyers) with BROKER_N_STBB_LOTS.</div>`;

    const st = d.state || {};
    document.getElementById('stbbSpot').textContent = st.spot ? `NIFTY ${_stbbNum(st.spot)}` : (st.day ? 'Waiting for the first 3-minute candle (09:18)' : '');
    document.getElementById('stbbLastUpd').textContent = st.updated ? 'updated ' + st.updated : '';
    _stbbRenderTrades((st.day && st.day.trades) || []);
    const log = (st.log || []).slice(-30).reverse();
    document.getElementById('stbbLog').innerHTML = log.map(l => `<div><b>${_stbbEsc(l.t)}</b> ${_stbbEsc(l.text)}</div>`).join('');
}

const _STBB_STATUS = { open: ['In trade', 'info'], closed: ['Closed', 'neutral'], missed: ['Missed', 'neg'], failed: ['Not filled', 'neg'] };

function _stbbRenderTrades(trades) {
    const rows = trades.map(t => {
        const legs = Object.values(t.legs || {});
        const pnl = legs.reduce((a, l) => a + (l.pnl || 0), 0);
        return Object.assign({}, t, {
            leg: t.option ? `NIFTY ${t.option.strike} ${t.option.type}` : '—',
            lots: legs.map(l => `${l.name}: ${l.lots ?? '—'}`).join(', ') || '—',
            prem: legs.length ? legs.map(l => `${_stbbNum(l.entry)} → ${l.exit != null ? _stbbNum(l.exit) : '…'}`).join(', ') : '—',
            pnl_inr: legs.some(l => l.pnl != null) ? pnl : null,
        });
    });
    document.getElementById('stbbTrades').innerHTML = DataGrid.render({
        rows, empty: 'No signal yet today',
        columns: [
            { key: 'n', label: '#' },
            { key: 'status', label: 'Status', format: v => (_STBB_STATUS[v] || [v])[0], badge: v => (_STBB_STATUS[v] || [, 'neutral'])[1] },
            { key: 'mode', label: 'Book', format: v => v ? v.toUpperCase() : '—', badge: v => v === 'live' ? 'neg' : 'info' },
            { key: 'type', label: 'Side', tone: v => v === 'Short' ? 'neg' : 'pos', strong: true },
            { key: 'signal_bar', label: 'Signal candle', format: v => (v || '').slice(11, 16) },
            { key: 'signal', label: 'RSI · close', format: v => v ? `${v.rsi} · ${_stbbNum(v.close)} (${v.pivot} ${_stbbNum(v.pivot_level)})` : '—' },
            { key: 'index_entry', label: 'Index in', format: (v, t) => v == null ? '—' : `${_stbbNum(v)} @ ${t.entry_time || ''}` },
            { key: 'leg', label: 'Option' },
            { key: 'lots', label: 'Lots' },
            { key: 'prem', label: 'Premium' },
            { key: 'result', label: 'Exit', format: (v, t) => v ? `${v} @ ${t.exit_time || ''} (index ${_stbbNum(t.index_exit)})` : (t.error || '—') },
            { key: 'pnl_inr', label: 'P&L', strong: true, format: v => v == null ? '—' : DataGrid.inr(v), tone: DataGrid.sign },
        ],
    });
}

// ── History ──────────────────────────────────────────────────────────────────

function _stbbFetchHistory() {
    fetch('/api/algo/st-bb-rsi/history')
        .then(r => r.json())
        .then(d => { if (d.success) { _stbbHistory = d.trades || []; _stbbRenderHistory(); } })
        .catch(() => {});
}

function _stbbSetBook(book) {
    _stbbBook = book;
    document.querySelectorAll('#stbbBookTabs .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.book === book));
    _stbbRenderHistory();
}

function _stbbRenderHistory() {
    const rows = _stbbBook === 'all' ? _stbbHistory : _stbbHistory.filter(t => t.mode === _stbbBook);
    const net = rows.reduce((a, t) => a + (t.net != null ? t.net : (t.pnl || 0)), 0);
    const wins = rows.filter(t => (t.net != null ? t.net : (t.pnl || 0)) > 0).length;
    const sum = document.getElementById('stbbHistSummary');
    sum.textContent = rows.length ? `${rows.length} trades · ${Math.round(100 * wins / rows.length)}% won · net ${_stbbInr(net)}` : '';
    sum.className = net > 0 ? 'ag-pos' : net < 0 ? 'ag-neg' : '';
    document.getElementById('stbbHistBody').innerHTML = DataGrid.render({
        rows, empty: 'No completed trades',
        columns: [
            { key: 'date', label: 'Date' },
            { key: 'mode', label: 'Book', format: v => (v || '').toUpperCase(), badge: v => v === 'live' ? 'neg' : 'info' },
            { key: 'side', label: 'Side', tone: v => v === 'Short' ? 'neg' : 'pos', strong: true },
            { key: 'entry_time', label: 'In' },
            { key: 'exit_time', label: 'Out' },
            { key: 'index_entry', label: 'Index in', format: _stbbNum },
            { key: 'index_exit', label: 'Index out', format: _stbbNum },
            { key: 'result', label: 'Exit', badge: v => v === 'Time Exit' ? 'warn' : 'neutral' },
            { key: 'strike', label: 'Option', format: (v, t) => `${v} ${t.option_type}` },
            { key: 'legs', label: 'Lots · premium', format: v => Object.values(v || {}).map(l => `${l.lots}× ${_stbbNum(l.entry)}→${_stbbNum(l.exit)}`).join(', ') || '—' },
            { key: 'pnl', label: 'Gross', format: _stbbInr, tone: DataGrid.sign },
            { key: 'charges', label: 'Charges', format: _stbbInr },
            { key: 'net', label: 'Net P&L', strong: true, format: _stbbInr, tone: DataGrid.sign },
        ],
    });
}

// ── Actions ──────────────────────────────────────────────────────────────────

function _stbbSave(body) {
    return fetch('/api/algo/st-bb-rsi/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(r => r.json()).then(d => {
        if (!d.success) alert('Not saved: ' + (d.error || 'unknown error'));
        _stbbFetchStatus();
    }).catch(e => alert('Request failed: ' + e));
}

function _stbbSetMode(mode) {
    if (mode === _stbbMode) return;
    if (mode === 'live' && !confirm('Switch ST + BB + RSI to LIVE? From the next entry, NIFTY options will be BOUGHT and SOLD with real orders at every account with BROKER_N_STBB_ACTIVE=true. There is no stop-loss: the trade ends only when the SuperTrend turns or at 15:14.')) return;
    if (mode === 'paper' && !confirm('Switch ST + BB + RSI to PAPER? New entries are only recorded. A live position already open keeps being managed until it is flat.')) return;
    _stbbSave({ mode });
}

function _stbbStart() {
    fetch('/api/algo/st-bb-rsi/start', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Start failed: ' + (d.error || '')); _stbbFetchStatus(); });
}

function _stbbStop() {
    if (!confirm('Stop ST + BB + RSI? It stays stopped (also after a restart) until Start. A live position is NOT squared off — close it from the Order Placement strip.')) return;
    fetch('/api/algo/st-bb-rsi/stop', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Stop failed: ' + (d.error || '')); _stbbFetchStatus(); });
}
