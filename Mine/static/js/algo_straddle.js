/* algo_straddle.js — Algo page: ATM Straddle tab (the Backtest page's straddle run live). */
'use strict';

let _strTimer = null;
let _strMode = null;
let _strBook = 'all';
let _strHistory = [];
let _strSettings = {};

const _STR_ENTRY_DAYS = {
    monthly: [['last_monday', 'Last Monday of month'], ['last_tuesday', 'Last Tuesday of month'],
              ['last_wednesday', 'Last Wednesday of month'], ['last_thursday', 'Last Thursday of month'],
              ['last_friday', 'Last Friday of month'], ['after_expiry', 'Day after monthly expiry']],
    weekly:  [['monday', 'Monday'], ['tuesday', 'Tuesday'], ['wednesday', 'Wednesday'],
              ['thursday', 'Thursday'], ['friday', 'Friday'], ['after_expiry', 'Day after weekly expiry']],
};

function _strClearTimers() { clearTimeout(_strTimer); _strTimer = null; }

function _strEsc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const _strNum = v => v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });

// ── Status ───────────────────────────────────────────────────────────────────

function _strFetchStatus() {
    _strClearTimers();
    fetch('/api/algo/straddle/status')
        .then(r => r.json())
        .then(d => { if (d.success) _strRender(d); })
        .catch(() => {})
        .finally(() => {
            if (document.getElementById('algo-straddle-panel')?.classList.contains('active'))
                _strTimer = setTimeout(_strFetchStatus, 5000);
        });
}

function _strSetBox(id, v) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = v;
}

function _strFillEntryDays(cadence, value) {
    const sel = document.getElementById('strEntryDay');
    const choices = _STR_ENTRY_DAYS[cadence] || _STR_ENTRY_DAYS.monthly;
    if (sel.dataset.cadence !== cadence) {
        sel.innerHTML = choices.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
        sel.dataset.cadence = cadence;
    }
    if (document.activeElement !== sel) sel.value = value;
}

function _strRender(d) {
    const s = _strSettings = d.settings || {};
    _strMode = s.mode;
    const badge = document.getElementById('strModeBadge');
    badge.textContent = s.mode === 'live' ? (d.live_armed ? 'LIVE' : 'LIVE — NO ACCOUNT') : 'PAPER';
    badge.className = 'dg-badge ' + (s.mode === 'live' ? (d.live_armed ? 'dg-badge--neg' : 'dg-badge--warn') : 'dg-badge--info');
    document.querySelectorAll('#strModeToggle .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.mode === s.mode));
    const run = document.getElementById('strRunBadge');
    run.className = 'ag-badge ' + (d.running ? 'active' : 'inactive');
    document.getElementById('strRunBadgeText').textContent = d.running
        ? (s.enabled ? 'Running' : 'Managing — no new entries')
        : (s.enabled ? 'Idle (runs 09:00–15:30)' : 'Stopped');

    _strSetBox('strCadence', s.cadence);
    _strFillEntryDays(s.cadence, s.entry_day);
    _strSetBox('strSkip', String(!!s.skip_one_night));
    document.getElementById('strSkip').disabled = s.cadence !== 'weekly';
    _strSetBox('strEntryTime', s.entry_time);
    _strSetBox('strTarget', s.target_pct);
    _strSetBox('strStop', s.stop_pct);
    _strSetBox('strAdjust', s.adjust);
    _strSetBox('strAdjustPts', s.adjust_pts);
    _strSetBox('strMaxAdjust', s.max_adjust);
    _strSetBox('strWings', s.wings);
    _strSetBox('strLockAt', s.lock_at);
    _strSetBox('strLockTo', s.lock_to);
    _strSetBox('strLots', s.lots);
    _strSetBox('strCompound', String(!!s.compounding));
    _strSetBox('strCapital', Math.round(s.start_capital));
    _strSetBox('strPerLot', Math.round(s.per_lot));

    const warn = [];
    if (s.cadence === 'weekly' && s.entry_day === 'monday' && !s.skip_one_night)
        warn.push('<b>Monday + weekly is a one-night straddle:</b> weeklies expire on Tuesday, so each trade holds a small premium through the whole overnight gap. 3-Feb-2026 opened 713 points up and this setup lost 4.6× its premium in that one night; no stop can act on a gap.');
    warn.push('<b>Nothing rests at the broker.</b> Target, stop and lock are watched by this app, so a position is only managed while the app runs and Kite is logged in. Log in to Kite every morning a position is open.');
    document.getElementById('strWarn').innerHTML = warn.map(w => `<div>⚠️ ${w}</div>`).join('');
    document.getElementById('strWarnCard').style.display = '';

    const nl = d.next_lots || {};
    const acc = d.accounts || [];
    const paperCard = `<div class="ag-stat"><div class="ag-stat-label">Paper — next trade</div>
        <div class="ag-stat-value">${nl.paper ?? '—'} lot(s) · booked ₹${_strNum(d.booked?.paper)}</div></div>`;
    document.getElementById('strAccounts').innerHTML = paperCard + (acc.length
        ? acc.map(a => `<div class="ag-stat"><div class="ag-stat-label">${_strEsc(a.name)}</div>
             <div class="ag-stat-value ${a.tradable ? '' : 'ag-neg'}">${a.tradable
                ? `${nl[a.instance] ?? '—'} lot(s) · booked ₹${_strNum(d.booked?.[a.instance])}` : _strEsc(a.why)}</div></div>`).join('')
        : `<div class="ag-empty">No account has BROKER_N_STR_ACTIVE=true — paper only. Live needs a zerodha account with it${s.compounding ? '' : ' and BROKER_N_STR_LOTS'}.</div>`);

    const st = d.state || {};
    _strRenderPosition(st);
    document.getElementById('strLastUpd').textContent = st.updated ? 'updated ' + st.updated : '';
    const log = (st.log || []).slice(-30).reverse();
    document.getElementById('strLog').innerHTML = log.map(l => `<div><b>${_strEsc(l.t)}</b> ${_strEsc(l.text)}</div>`).join('');
}

function _strRenderPosition(st) {
    const pos = st.position, sum = document.getElementById('strPosSummary');
    document.getElementById('strSquareOff').disabled = !pos;
    if (!pos) {
        sum.textContent = '';
        document.getElementById('strLegs').innerHTML = '<div class="ag-empty">No open position</div>';
        return;
    }
    const p = pos.params || {}, prem = pos.premium, m = st.mtm;
    const pct = m != null && prem ? (100 * m / prem) : null;
    sum.innerHTML = `${_strEsc((pos.mode || '').toUpperCase())} · ${_strEsc(pos.cadence)} · exp ${_strEsc(pos.expiry)} · premium ${_strNum(prem)}`
        + ` · target +${p.target_pct}% · stop −${p.stop_pct}%`
        + (p.lock_at ? ` · lock ${p.lock_at}→${p.lock_to}%${pos.lock_armed ? ' 🔒 armed' : ''}` : '')
        + (st.spot ? ` · NIFTY ${_strNum(st.spot)}` : '')
        + (m != null ? ` · <b class="${m >= 0 ? 'ag-pos' : 'ag-neg'}">${m >= 0 ? '+' : ''}${_strNum(m)} pts (${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)</b>` : '')
        + (pos.exit ? ` · <b class="ag-neg">EXITING: ${_strEsc(pos.exit.reason)}</b>` : '');
    const marks = st.marks || {};
    const rows = (pos.legs || []).map(lg => Object.assign({}, lg, { mark: lg.closed_at ? lg.exit : marks[lg.tsym] }));
    document.getElementById('strLegs').innerHTML = DataGrid.render({
        rows, empty: 'No legs',
        columns: [
            { key: 'side', label: 'Side', format: v => v < 0 ? 'SELL' : 'BUY', tone: v => v < 0 ? 'neg' : 'pos', strong: true },
            { key: 'tsym', label: 'Contract' },
            { key: 'entry', label: 'Entry', format: _strNum },
            { key: 'mark', label: 'Now / exit', format: _strNum },
            { key: 'closed_at', label: 'State', format: v => v ? 'closed ' + v.slice(5, 16) : 'open' },
            { key: 'pts', label: 'Pts', format: (_, r) => r.mark == null ? '—' : _strNum(r.side * (r.mark - r.entry)),
              tone: (_, r) => r.mark == null ? '' : (r.side * (r.mark - r.entry) >= 0 ? 'pos' : 'neg') },
        ],
    }) + `<div style="margin-top:6px;font-size:11px;color:var(--ag-text-3)">`
       + Object.values(pos.accounts || {}).map(a => `${_strEsc(a.name)}: ${a.lots} lot(s)${a.failed ? ' — ENTRY FAILED' : ''}${a.unconfirmed ? ' — check the broker' : ''}`).join(' · ')
       + `</div>`;
}

// ── History ──────────────────────────────────────────────────────────────────

function _strFetchHistory() {
    fetch('/api/algo/straddle/history')
        .then(r => r.json())
        .then(d => { if (d.success) { _strHistory = d.trades || []; _strRenderHistory(); } })
        .catch(() => {});
}

function _strSetBook(book) {
    _strBook = book;
    document.querySelectorAll('#strBookTabs .rtp-period-tab').forEach(b => b.classList.toggle('active', b.dataset.book === book));
    _strRenderHistory();
}

function _strRenderHistory() {
    const rows = _strBook === 'all' ? _strHistory : _strHistory.filter(t => t.mode === _strBook);
    const net = rows.reduce((a, t) => a + (t.pnl || 0), 0), wins = rows.filter(t => (t.pnl || 0) > 0).length;
    const sum = document.getElementById('strHistSummary');
    sum.textContent = rows.length ? `${rows.length} trades · ${Math.round(100 * wins / rows.length)}% won · ${net >= 0 ? '+' : '−'}₹${Math.abs(Math.round(net)).toLocaleString('en-IN')}` : '';
    sum.className = net > 0 ? 'ag-pos' : net < 0 ? 'ag-neg' : '';
    document.getElementById('strHistBody').innerHTML = DataGrid.render({
        rows, empty: 'No completed trades',
        columns: [
            { key: 'date', label: 'Entry' },
            { key: 'mode', label: 'Book', format: v => (v || '').toUpperCase(), badge: v => v === 'live' ? 'neg' : 'info' },
            { key: 'cadence', label: 'Expiry', format: (v, t) => `${v || ''} ${t.expiry || ''}` },
            { key: 'strike', label: 'Strike' },
            { key: 'premium', label: 'Premium', format: _strNum },
            { key: 'exit_time', label: 'Out', format: v => (v || '').slice(5, 16) },
            { key: 'reason', label: 'Result', badge: v => /Target/.test(v) ? 'pos' : /Stop|failed|unattended/i.test(v) ? 'neg' : 'warn' },
            { key: 'adjusts', label: 'Adj', format: v => (v || []).length || '—' },
            { key: 'points', label: 'Pts', format: v => v == null ? '—' : (v > 0 ? '+' : '') + _strNum(v), tone: DataGrid.sign },
            { key: 'pct', label: '% prem', format: v => v == null ? '—' : (v > 0 ? '+' : '') + _strNum(v) + '%', tone: DataGrid.sign },
            { key: 'accounts', label: 'Lots', format: v => Object.values(v || {}).map(a => `${a.name}: ${a.lots}${a.unconfirmed ? ' ⚠' : ''}`).join(', ') || '—' },
            { key: 'pnl', label: 'P&L', strong: true, format: v => v == null ? '—' : DataGrid.inr(v), tone: DataGrid.sign },
        ],
    });
}

// ── Actions ──────────────────────────────────────────────────────────────────

function _strSave(body) {
    return fetch('/api/algo/straddle/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(r => r.json()).then(d => {
        if (!d.success) alert('Not saved: ' + (d.error || 'unknown error'));
        _strFetchStatus();
    }).catch(e => alert('Request failed: ' + e));
}

function _strCadenceChanged() {
    const cadence = document.getElementById('strCadence').value;
    const entry_day = cadence === 'weekly' ? 'friday' : 'last_friday';
    _strFillEntryDays(cadence, entry_day);
    _strSave({ cadence, entry_day, skip_one_night: false });
}

function _strSetMode(mode) {
    if (mode === _strMode) return;
    if (mode === 'live' && !confirm('Switch the ATM Straddle to LIVE?\n\nFrom the next entry, NIFTY options will be SOLD short with real NRML orders at every zerodha account with BROKER_N_STR_ACTIVE=true, and carried overnight.\n\nNothing rests at the broker: target and stop need this app running and Kite logged in. An overnight gap cannot be stopped.')) return;
    if (mode === 'paper' && !confirm('Switch the ATM Straddle to PAPER? New entries are only recorded. A live position already open keeps being managed at the broker until it is flat.')) return;
    _strSave({ mode });
}

function _strStart() {
    fetch('/api/algo/straddle/start', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Start failed: ' + (d.error || '')); _strFetchStatus(); });
}

function _strStop() {
    if (!confirm('Stop new ATM Straddle entries? It stays stopped (also after a restart) until Start. An open position keeps being managed until it is flat; use Square off to close it now.')) return;
    fetch('/api/algo/straddle/stop', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Stop failed: ' + (d.error || '')); _strFetchStatus(); });
}

function _strSquareOff() {
    if (!confirm('Square off the open straddle NOW, at market?')) return;
    fetch('/api/algo/straddle/square-off', { method: 'POST' }).then(r => r.json())
        .then(d => { if (!d.success) alert('Square off failed: ' + (d.error || '')); _strFetchStatus(); });
}
