/* algo.js — Algo page: Straddle + EMA RTP tabs */
'use strict';

let _algoTimer         = null;
let _rtpStatusTimer    = null;
let _rtpHistoryTimer   = null;
let _rtpLastEntryTime  = null;  // tracks last seen entry_time to detect trade changes
let _rtpLastActiveFlag = false; // tracks last seen active flag
const _ALGO_TABS = ['straddle', 'rtp'];

function algoLoad() {
    const hash = location.hash.replace('#', '');
    algoSwitch(_ALGO_TABS.includes(hash) ? hash : 'straddle');
}

// ── Tab switching ─────────────────────────────────────────────────────────────

function algoSwitch(tab) {
    _ALGO_TABS.forEach(t => {
        document.getElementById('algo-' + t + '-panel').classList.toggle('active', t === tab);
        document.getElementById('algo-tab-'  + t).classList.toggle('active', t === tab);
    });
    history.replaceState(null, '', '#' + tab);
    clearTimeout(_algoTimer);
    clearTimeout(_rtpStatusTimer);
    clearTimeout(_rtpHistoryTimer);
    if (tab === 'straddle') {
        _fetchStatus();
    } else {
        _rtpFetchStatus();
        _rtpFetchHistory();
    }
}

// ── RTP status ────────────────────────────────────────────────────────────────

function _rtpFetchStatus() {
    fetch('/api/algo/rtp/status')
        .then(r => r.json())
        .then(data => {
            _rtpRenderStatus(data);
            // Detect trade state changes and immediately refresh history so opt
            // entry/exit values reflect the just-completed or just-entered trade
            // rather than waiting up to 30 s for the scheduled history poll.
            const newEntryTime = data.state && data.state.active_trade
                ? data.state.active_trade.entry_time : null;
            const newActive = !!data.active;
            const tradeChanged = newEntryTime !== _rtpLastEntryTime ||
                                 newActive !== _rtpLastActiveFlag;
            _rtpLastEntryTime  = newEntryTime;
            _rtpLastActiveFlag = newActive;
            if (tradeChanged) {
                clearTimeout(_rtpHistoryTimer);
                _rtpFetchHistory();
            }
            clearTimeout(_rtpStatusTimer);
            _rtpStatusTimer = setTimeout(_rtpFetchStatus, newActive ? 5000 : 30000);
        })
        .catch(() => {
            clearTimeout(_rtpStatusTimer);
            _rtpStatusTimer = setTimeout(_rtpFetchStatus, 30000);
        });
}

function _rtpRenderStatus(data) {
    const trade  = data.state && data.state.active_trade;
    const live   = data.live || null;
    const active = !!data.active;

    // Badge
    const badge = document.getElementById('rtpBadge');
    badge.className = 'ag-badge ' + (active ? 'active' : 'inactive');
    document.getElementById('rtpBadgeText').textContent =
        active ? (trade.direction + ' ' + trade.option_type) : 'No Trade';

    // Timestamp
    document.getElementById('rtpLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Active trade grid
    const grid = document.getElementById('rtpActiveGrid');
    if (!active || !trade) {
        grid.innerHTML = '<div class="ag-empty">No active trade</div>';
        return;
    }

    const pnlPts = live ? live.pnl_pts : null;
    const pnlInr = live ? live.pnl_inr_total : null;
    const spotStr = live ? '₹' + _num(live.spot) : '…';

    const tiles = [
        { label: 'Direction',    value: trade.direction,
          cls: trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg' },
        { label: 'Option',       value: trade.option_type + ' ' + trade.strike },
        { label: 'Entry Spot',   value: '₹' + _num(trade.entry_spot) },
        { label: 'Live Spot',    value: spotStr },
        { label: 'SL Level',     value: '₹' + _num(trade.sl_level),     cls: 'ag-warn' },
        { label: 'Target Level', value: '₹' + _num(trade.target_level), cls: 'ag-pos' },
        { label: 'P&L (pts)',    value: _rtpFmtPts(pnlPts),  cls: _rtpPnlCls(pnlPts) },
        { label: 'P&L (₹ est)', value: _rtpFmtInr(pnlInr),  cls: _rtpPnlCls(pnlInr) },
        { label: 'Entry Time',   value: trade.entry_time ? _fmtTime(trade.entry_time) : '—' },
    ];

    grid.innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    // Toggle Force Exit button based on trade state
    const exitBtn = document.getElementById('rtpExitBtn');
    if (exitBtn) exitBtn.disabled = !active;
}

// ── RTP history ───────────────────────────────────────────────────────────────

function _rtpFetchHistory() {
    fetch('/api/algo/rtp/history')
        .then(r => r.json())
        .then(data => {
            _rtpRenderHistory(data.trades || []);
            clearTimeout(_rtpHistoryTimer);
            _rtpHistoryTimer = setTimeout(_rtpFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_rtpHistoryTimer);
            _rtpHistoryTimer = setTimeout(_rtpFetchHistory, 30000);
        });
}

function _rtpRenderHistory(trades) {
    const countEl = document.getElementById('rtpHistCount');
    const body    = document.getElementById('rtpHistBody');

    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No completed trades today</div>';
        return;
    }

    function _fmtOpt(val) {
        if (val == null) return '—';
        return '₹' + Number(val).toFixed(2);
    }
    function _fmtPts(val, suffix) {
        if (val == null) return '—';
        return (val >= 0 ? '+' : '') + Number(val).toFixed(1) + (suffix || '');
    }
    function _fmtInr(val) {
        if (val == null) return '—';
        return (val >= 0 ? '+₹' : '-₹') + Math.abs(Number(val)).toFixed(0);
    }

    body.innerHTML = `
<div class="ag-hist-scroll">
<table class="ag-hist-table">
    <thead>
        <tr>
            <th class="ag-hist-th">Date</th>
            <th class="ag-hist-th">Entry Time</th>
            <th class="ag-hist-th">Exit Time</th>
            <th class="ag-hist-th">Strike</th>
            <th class="ag-hist-th">Opt Entry</th>
            <th class="ag-hist-th">Opt Exit</th>
            <th class="ag-hist-th">N Entry</th>
            <th class="ag-hist-th">N Exit</th>
            <th class="ag-hist-th">N P&amp;L</th>
            <th class="ag-hist-th">Opt P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const dir       = t.direction === 'BUY' ? 'CE BUY' : 'PE BUY';
        const dirCls    = t.direction === 'BUY' ? 'ce' : 'pe';
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const oPnlCls   = (t.opt_pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const dateStr   = t.date || (t.entry_time ? t.entry_time.slice(0, 10) : '—');
        const entryTime = t.entry_time ? _fmtTimeOnly(t.entry_time) : '—';
        const exitTime  = t.exit_time  ? _fmtTimeOnly(t.exit_time)  : '—';
        return `<tr>
            <td class="ag-hist-td">${dateStr}</td>
            <td class="ag-hist-td">${entryTime}</td>
            <td class="ag-hist-td">${exitTime}</td>
            <td class="ag-hist-td">${t.strike ?? '—'} ${t.option_type ?? ''}</td>
            <td class="ag-hist-td">${_fmtOpt(t.opt_entry_price)}</td>
            <td class="ag-hist-td">${_fmtOpt(t.opt_exit_price)}</td>
            <td class="ag-hist-td">₹${_num(t.entry_spot)}</td>
            <td class="ag-hist-td">₹${_num(t.exit_spot)}</td>
            <td class="ag-hist-td ${nPnlCls}" style="font-weight:700">${_fmtPts(t.pnl_pts, ' pts')}</td>
            <td class="ag-hist-td ${t.opt_pnl_inr != null ? oPnlCls : ''}" style="font-weight:700">${_fmtInr(t.opt_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtpFmtReason(t.reason)}</td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

// ── RTP helpers ───────────────────────────────────────────────────────────────

function _rtpFmtPts(pts) {
    if (pts == null) return '…';
    return (pts >= 0 ? '+' : '') + Number(pts).toFixed(1) + ' pts';
}

function _rtpFmtInr(inr) {
    if (inr == null) return '…';
    return (inr >= 0 ? '+₹' : '-₹') + Math.abs(inr).toFixed(0);
}

function _rtpPnlCls(val) {
    if (val == null) return '';
    return val >= 0 ? 'ag-pos' : 'ag-neg';
}

function _rtpFmtReason(reason) {
    const map = {
        TARGET: '<span class="ag-reason-badge target">TARGET</span>',
        SL:     '<span class="ag-reason-badge sl">SL</span>',
        EOD:    '<span class="ag-reason-badge eod">EOD</span>',
        MANUAL: '<span class="ag-reason-badge manual">MANUAL</span>',
    };
    return map[reason] || reason || '—';
}

// ── RTP Force Exit ────────────────────────────────────────────────────────────

function rtpExitNow(btn) {
    if (!confirm('Force-close the active RTP trade? A market SELL order will be sent immediately.')) return;
    _setBusy(btn, 'Exiting…');
    fetch('/api/algo/rtp/exit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Exit failed: ' + (d.error || 'Unknown error')); _unbusy(btn, 'Force Exit'); return; }
            clearTimeout(_rtpStatusTimer);
            clearTimeout(_rtpHistoryTimer);
            _rtpFetchStatus();
            _rtpFetchHistory();
        })
        .catch(e => { alert('Request failed: ' + e); _unbusy(btn, 'Force Exit'); });
}


// ── RTP Delta Strikes ─────────────────────────────────────────────────────────

function rtpFetchDeltaStrikes(btn) {
    const panel = document.getElementById('rtpStrikesResult');
    btn.disabled = true;
    btn.classList.add('busy');
    btn.textContent = 'Loading…';
    panel.style.display = 'none';

    fetch('/api/algo/rtp/delta-strikes')
        .then(r => r.json())
        .then(d => {
            btn.disabled = false;
            btn.classList.remove('busy');
            btn.textContent = 'Δ Strikes';

            if (!d.success) {
                panel.innerHTML = `<span style="color:#c62828;font-size:12px;">⚠ ${d.error || 'Failed to fetch strikes'}</span>`;
                panel.style.display = 'block';
                return;
            }

            const fmt  = v => v != null ? v.toLocaleString('en-IN') : '—';
            const fmtD = v => v != null ? (v > 0 ? '+' : '') + v.toFixed(3) : '—';
            const fmtL = v => v != null ? '₹' + v.toLocaleString('en-IN', {minimumFractionDigits:2}) : '—';

            const ce = d.CE || {};
            const pe = d.PE || {};

            panel.innerHTML = `
              <div style="font-size:11px;color:#7986cb;font-weight:600;margin-bottom:4px;letter-spacing:.4px;">
                NIFTY SPOT: <span style="color:#1a237e;font-size:13px;">${fmt(d.spot)}</span>
                &nbsp;·&nbsp; Expiry: <span style="color:#555;">${d.expiry || '—'}</span>
              </div>
              <div class="rtp-strikes-row">
                <span class="rsr-type ce">CE</span>
                <span class="rsr-strike">${fmt(ce.strike)}</span>
                <span class="rsr-delta">δ ${fmtD(ce.delta)}</span>
                <span class="rsr-ltp">LTP ${fmtL(ce.ltp)}</span>
              </div>
              <div class="rtp-strikes-row">
                <span class="rsr-type pe">PE</span>
                <span class="rsr-strike">${fmt(pe.strike)}</span>
                <span class="rsr-delta">δ ${fmtD(pe.delta)}</span>
                <span class="rsr-ltp">LTP ${fmtL(pe.ltp)}</span>
              </div>`;
            panel.style.display = 'block';
        })
        .catch(e => {
            btn.disabled = false;
            btn.classList.remove('busy');
            btn.textContent = 'Δ Strikes';
            panel.innerHTML = `<span style="color:#c62828;font-size:12px;">⚠ Request failed: ${e}</span>`;
            panel.style.display = 'block';
        });
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

    // Buttons — Preview and Enter both disabled while trade is active
    document.getElementById('algoPreviewBtn').disabled = active;
    document.getElementById('algoEnterBtn').disabled   = active;
    document.getElementById('algoExitBtn').disabled    = !active;
}

function _renderLegs(state, live) {
    const grid = document.getElementById('algoLegsGrid');
    if (!state.active) {
        grid.innerHTML = '<div class="ag-empty">No active trade</div>';
        return;
    }
    const lot_size = state.lot_size || 1;
    const legs = [
        { type: 'CE', cls: 'ce', strike: state.ce_strike, sym: state.ce_kite_tradingsymbol, entry: state.ce_entry_ltp, curr: live ? live.ce_ltp : null },
        { type: 'PE', cls: 'pe', strike: state.pe_strike, sym: state.pe_kite_tradingsymbol, entry: state.pe_entry_ltp, curr: live ? live.pe_ltp : null },
    ];
    grid.innerHTML = legs.map(r => {
        const currStr = r.curr != null ? r.curr.toFixed(2) : '…';
        const pnl     = r.curr != null ? (r.entry - r.curr) * lot_size : null;
        const pnlStr  = pnl != null ? (pnl >= 0 ? '+₹' : '−₹') + Math.abs(pnl).toFixed(2) : '—';
        const pnlCls  = pnl == null ? '' : pnl >= 0 ? 'ag-pos' : 'ag-neg';
        return `<div class="ag-leg-card ${r.cls}-leg">
            <div class="ag-leg-hdr">
                <span class="ag-leg-type ${r.cls}">${r.type}</span>
                <span class="ag-leg-strike">${r.strike ?? '—'}</span>
                <span class="ag-leg-sym">${r.sym || '—'}</span>
            </div>
            <div class="ag-leg-metrics">
                <div class="ag-leg-metric">
                    <span class="ag-leg-lbl">Entry</span>
                    <span class="ag-leg-val">${r.entry != null ? r.entry.toFixed(2) : '—'}</span>
                </div>
                <div class="ag-leg-metric">
                    <span class="ag-leg-lbl">Current</span>
                    <span class="ag-leg-val">${currStr}</span>
                </div>
                <div class="ag-leg-metric">
                    <span class="ag-leg-lbl">P&amp;L / lot</span>
                    <span class="ag-leg-val ${pnlCls}">${pnlStr}</span>
                </div>
            </div>
        </div>`;
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
            _renderPreviewState(d);
        })
        .catch(e => _showMsg('error', 'Preview failed: ' + e))
        .finally(() => { btn.disabled = false; btn.textContent = orig; });
}

function _renderPreviewState(d) {
    // Fabricate a state object that matches the live-state shape so all
    // existing render helpers work without modification.
    const state = {
        active: true,
        ce_strike: d.ce_strike,
        pe_strike: d.pe_strike,
        ce_kite_tradingsymbol: d.ce_kite_tradingsymbol,
        pe_kite_tradingsymbol: d.pe_kite_tradingsymbol,
        ce_entry_ltp: d.ce_ltp,
        pe_entry_ltp: d.pe_ltp,
        combined_entry: d.combined_premium,
        sl_trigger: d.sl_trigger,
        current_expiry: d.current_expiry,
        next_expiry: d.next_expiry,
        underlying_at_entry: d.underlying,
        lots: d.lots,
        lot_size: d.lot_size,
        entry_time: null,
    };
    // At preview time entry == current, so P&L is zero — exactly right for a just-entered position.
    const live = {
        ce_ltp: d.ce_ltp,
        pe_ltp: d.pe_ltp,
        combined_current: d.combined_premium,
        sl_distance: d.sl_trigger - d.combined_premium,
        pnl_per_lot: 0,
        pnl_total: 0,
    };

    // Badge
    const badge = document.getElementById('algoBadge');
    badge.className = 'ag-badge preview';
    document.getElementById('algoBadgeText').textContent = 'Preview';

    _setText('algoLastUpd', new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));

    // Status grid
    _setText('algoStatusVal',  'Preview');
    _setText('algoExpiry',     state.current_expiry  || '—');
    _setText('algoNextExpiry', state.next_expiry      || '—');
    _setText('algoUnderlying', '₹' + _num(state.underlying_at_entry));
    _setText('algoEntryTime',  '—');
    _setText('algoLots',       `${state.lots} × ${state.lot_size}`);

    // Legs table
    _renderLegs(state, live);

    // P&L card
    _setText('agCombEntry',   state.combined_entry.toFixed(2));
    _setText('agCombCurrent', live.combined_current.toFixed(2));
    _setText('agSL',          state.sl_trigger.toFixed(2));

    const dist = live.sl_distance;
    const distEl = document.getElementById('agSLDist');
    distEl.textContent = dist.toFixed(2);
    distEl.className = 'ag-stat-value ' + (dist < 5 ? 'ag-neg' : dist < 15 ? 'ag-warn' : 'ag-pos');

    _setPnl('agPnlLot',   0);
    _setPnl('agPnlTotal', 0);

    // Keep Enter enabled (not yet placed), Exit disabled
    document.getElementById('algoEnterBtn').disabled = false;
    document.getElementById('algoExitBtn').disabled  = true;

    _showMsg('info',
        `Preview — δ ${d.delta_target} | SL cap ₹${Number(d.sl_cap).toLocaleString('en-IN')} | ` +
        `Strikes computed at NIFTY ₹${_num(d.underlying)}`
    );
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

function _fmtTimeOnly(iso) {
    try {
        return new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
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
