/* algo_stbb.js — Algo page: the three ST + BB + RSI tabs (NIFTY, BANKNIFTY, SENSEX): the Backtest
   page's strategy run live on index options. One template, one set of functions; every function
   takes the tab key. */
'use strict';

// key → DOM id prefix (p), API base, index and env-flag prefix. The state is per tab.
const _STBB = {
    'stbb':    { p: 'stbb',   api: '/api/algo/st-bb-rsi',            sym: 'NIFTY',     pfx: 'STBB' },
    'stbb-bn': { p: 'stbbbn', api: '/api/algo/st-bb-rsi-banknifty', sym: 'BANKNIFTY', pfx: 'STBB_BN' },
    'stbb-sx': { p: 'stbbsx', api: '/api/algo/st-bb-rsi-sensex',    sym: 'SENSEX',    pfx: 'STBB_SX' },
};
Object.values(_STBB).forEach(c => Object.assign(c, { timer: null, mode: null, book: 'all', history: [] }));

const _stbbEl = (c, id) => document.getElementById(c.p + id);

function _stbbClearTimers() { Object.values(_STBB).forEach(c => { clearTimeout(c.timer); c.timer = null; }); }

// Called by algoSwitch for any of the three tabs.
function _stbbActivate(key) { _stbbFetchStatus(key); _stbbFetchHistory(key); }

function _stbbEsc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const _stbbNum = v => v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
const _stbbInr = v => v == null ? '—' : (v < 0 ? '−' : '') + '₹' + Math.abs(Math.round(v)).toLocaleString('en-IN');

// ── Status ───────────────────────────────────────────────────────────────────

function _stbbFetchStatus(key) {
    const c = _STBB[key];
    clearTimeout(c.timer);
    fetch(c.api + '/status')
        .then(r => r.json())
        .then(d => { if (d.success) _stbbRender(c, key, d); })
        .catch(() => {})
        .finally(() => {
            if (document.getElementById('algo-' + key + '-panel')?.classList.contains('active'))
                c.timer = setTimeout(() => _stbbFetchStatus(key), 5000);
        });
}

function _stbbRender(c, key, d) {
    const s = d.settings || {};
    c.mode = s.mode;
    const badge = _stbbEl(c, 'ModeBadge');
    badge.textContent = s.mode === 'live' ? (d.live_armed ? 'LIVE' : 'LIVE — NO ACCOUNT') : 'PAPER';
    badge.className = 'dg-badge ' + (s.mode === 'live' ? (d.live_armed ? 'dg-badge--neg' : 'dg-badge--warn') : 'dg-badge--info');
    document.querySelectorAll('#' + c.p + 'ModeToggle .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.mode === s.mode));

    const run = _stbbEl(c, 'RunBadge');
    run.className = 'ag-badge ' + (d.running ? 'active' : 'inactive');
    _stbbEl(c, 'RunBadgeText').textContent = d.running ? 'Running' : (s.enabled ? 'Idle (runs 09:00–15:30)' : 'Stopped');

    const set = (id, v) => { const el = document.getElementById(id); if (el && document.activeElement !== el) el.value = v; };
    set(c.p + 'Direction', s.direction); set(c.p + 'MaxTrades', s.max_trades); set(c.p + 'Premium', Math.round(s.premium));
    _stbbEl(c, 'Sizing').textContent =
        `Size is the flags: a live order is each account's BROKER_N_${c.pfx}_LOTS lots (at accounts with BROKER_N_${c.pfx}_ACTIVE=true); a paper trade takes ${d.paper_lots} lot(s).`;
    _stbbEl(c, 'Rule').textContent =
        `3-minute candles · RSI(${s.rsi_len}) ${s.rsi_level}/${100 - s.rsi_level} · SuperTrend(${s.st_len}, ${s.st_mult}) · BB(${s.bb_len}, ${s.bb_mult}) · R1/S1 of yesterday · `
        + `no target, no stop — exits when SuperTrend turns against the trade, square-off 15:14`
        + (s.backstop_pct ? ` · broker backstop SL-M ${s.backstop_pct}% below the fill` : '');

    const acc = d.accounts || [];
    _stbbEl(c, 'Accounts').innerHTML = acc.length
        ? acc.map(a => `<div class="ag-stat"><div class="ag-stat-label">${_stbbEsc(a.name)}</div>
             <div class="ag-stat-value ${a.stoppable && a.lots ? '' : 'ag-neg'}">${a.stoppable ? (a.lots ? a.lots + ' lot(s)' : 'no BROKER_' + a.instance + '_' + c.pfx + '_LOTS') : a.type + ' — not supported'}</div></div>`).join('')
        : `<div class="ag-empty">No account has BROKER_N_${c.pfx}_ACTIVE=true — paper only. Live needs one (zerodha / fyers) with BROKER_N_${c.pfx}_LOTS.</div>`;

    const st = d.state || {};
    _stbbEl(c, 'Spot').textContent = st.spot ? `${c.sym} ${_stbbNum(st.spot)}` : (st.day ? 'Waiting for the first 3-minute candle (09:18)' : '');
    _stbbEl(c, 'LastUpd').textContent = st.updated ? 'updated ' + st.updated : '';
    _stbbRenderTrades(c, (st.day && st.day.trades) || []);
    const log = (st.log || []).slice(-30).reverse();
    _stbbEl(c, 'Log').innerHTML = log.map(l => `<div><b>${_stbbEsc(l.t)}</b> ${_stbbEsc(l.text)}</div>`).join('');
}

const _STBB_STATUS = { open: ['In trade', 'info'], closed: ['Closed', 'neutral'], missed: ['Missed', 'neg'], failed: ['Not filled', 'neg'] };

function _stbbRenderTrades(c, trades) {
    const rows = trades.map(t => {
        const legs = Object.values(t.legs || {});
        const pnl = legs.reduce((a, l) => a + (l.pnl || 0), 0);
        return Object.assign({}, t, {
            leg: t.option ? `${c.sym} ${t.option.strike} ${t.option.type}` : '—',
            lots: legs.map(l => `${l.name}: ${l.lots ?? '—'}`).join(', ') || '—',
            prem: legs.length ? legs.map(l => `${_stbbNum(l.entry)} → ${l.exit != null ? _stbbNum(l.exit) : '…'}`).join(', ') : '—',
            pnl_inr: legs.some(l => l.pnl != null) ? pnl : null,
        });
    });
    _stbbEl(c, 'Trades').innerHTML = DataGrid.render({
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

function _stbbFetchHistory(key) {
    const c = _STBB[key];
    fetch(c.api + '/history')
        .then(r => r.json())
        .then(d => { if (d.success) { c.history = d.trades || []; _stbbRenderHistory(c); } })
        .catch(() => {});
}

function _stbbSetBook(key, book) {
    const c = _STBB[key];
    c.book = book;
    document.querySelectorAll('#' + c.p + 'BookTabs .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.book === book));
    _stbbRenderHistory(c);
}

function _stbbRenderHistory(c) {
    const rows = c.book === 'all' ? c.history : c.history.filter(t => t.mode === c.book);
    const net = rows.reduce((a, t) => a + (t.net != null ? t.net : (t.pnl || 0)), 0);
    const wins = rows.filter(t => (t.net != null ? t.net : (t.pnl || 0)) > 0).length;
    const sum = _stbbEl(c, 'HistSummary');
    sum.textContent = rows.length ? `${rows.length} trades · ${Math.round(100 * wins / rows.length)}% won · net ${_stbbInr(net)}` : '';
    sum.className = net > 0 ? 'ag-pos' : net < 0 ? 'ag-neg' : '';
    _stbbEl(c, 'HistBody').innerHTML = DataGrid.render({
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

function _stbbSave(key, body) {
    const c = _STBB[key];
    return fetch(c.api + '/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(r => r.json()).then(d => {
        if (!d.success) alert('Not saved: ' + (d.error || 'unknown error'));
        _stbbFetchStatus(key);
    }).catch(e => alert('Request failed: ' + e));
}

function _stbbSetMode(key, mode) {
    const c = _STBB[key];
    if (mode === c.mode) return;
    if (mode === 'live' && !confirm(`Switch ST + BB + RSI ${c.sym} to LIVE? From the next entry, ${c.sym} options will be BOUGHT and SOLD with real orders at every account with BROKER_N_${c.pfx}_ACTIVE=true. There is no stop-loss: the trade ends only when the SuperTrend turns or at 15:14.`)) return;
    if (mode === 'paper' && !confirm(`Switch ST + BB + RSI ${c.sym} to PAPER? New entries are only recorded. A live position already open keeps being managed until it is flat.`)) return;
    _stbbSave(key, { mode });
}

function _stbbStart(key) {
    fetch(_STBB[key].api + '/start', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Start failed: ' + (d.error || '')); _stbbFetchStatus(key); });
}

function _stbbStop(key) {
    if (!confirm(`Stop ST + BB + RSI ${_STBB[key].sym}? It stays stopped (also after a restart) until Start. A live position is NOT squared off — close it from the Order Placement strip.`)) return;
    fetch(_STBB[key].api + '/stop', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Stop failed: ' + (d.error || '')); _stbbFetchStatus(key); });
}
