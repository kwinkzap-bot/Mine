/* algo.js — Algo page: Active Trade + EMA RTP (1m/30s/3m/5m) + 2nd Candle + Swing Momentum tabs */
'use strict';

let _rtpStatusTimer    = null;
let _rtpHistoryTimer   = null;
let _rtpLastEntryTime  = null;  // tracks last seen entry_time to detect trade changes
let _rtpLastActiveFlag = false; // tracks last seen active flag
let _rtp30sStatusTimer    = null;
let _rtp30sHistoryTimer   = null;
let _rtp30sLastEntryTime  = null;
let _rtp30sLastActiveFlag = false;
let _rtp2mStatusTimer     = null;
let _rtp2mHistoryTimer    = null;
let _rtp2mLastEntryTime   = null;
let _rtp2mLastActiveFlag  = false;
let _rtp3mStatusTimer     = null;
let _rtp3mHistoryTimer    = null;
let _rtp3mLastEntryTime   = null;
let _rtp3mLastActiveFlag  = false;
let _rtp5mStatusTimer     = null;
let _rtp5mHistoryTimer    = null;
let _rtp5mLastEntryTime   = null;
let _rtp5mLastActiveFlag  = false;
let _scStatusTimer     = null;
let _scHistoryTimer    = null;
let _scLastEntryTime   = null;
let _scLastActiveFlag  = false;
let _intrinsicStatusTimer    = null;
let _intrinsicHistoryTimer   = null;
let _intrinsicLastEntryTime  = null;
let _intrinsicLastActiveFlag = false;
let _activeTimer       = null;
const _ALGO_TABS = ['active', 'rtp', 'rtp30s', 'rtp2m', 'rtp3m', 'rtp5m', 'sc', 'intrinsic', 'swing-momentum'];

// Round-trip brokerage charged per lot (1 lot = 65 qty). The performance
// dashboards express ₹ P&L on a single-lot basis (opt_pnl_pts × lot_size), so
// exactly one lot's brokerage is deducted from each completed trade.
const _ALGO_BROKERAGE_PER_LOT = 135;

// NIFTY lot size — fallback when a trade record has no lot_size field.
const _NIFTY_LOT_SIZE = 65;

// Unrealised ₹ for an open trade: (opt LTP − opt entry) × lot size.
function _activeOpenPnlInr(trade, live) {
    const optEntry = live && live.opt_entry_price != null
        ? live.opt_entry_price : (trade ? trade.opt_entry_price : null);
    const optCur = live ? live.opt_current_price : null;
    if (optCur == null || optEntry == null) return null;
    return (optCur - optEntry) * ((trade && trade.lot_size) ?? _NIFTY_LOT_SIZE);
}

// Option-premium tiles (Opt Entry / Opt LTP / Opt P&L) shared by every algo
// tab's Active Trade grid.
function _algoOptTiles(trade, live) {
    const optEntry = live && live.opt_entry_price != null
        ? live.opt_entry_price : trade.opt_entry_price;
    const optCur = live ? live.opt_current_price : null;
    const optPnl = _activeOpenPnlInr(trade, live);
    return [
        { label: 'Opt Entry',    value: optEntry != null ? '₹' + _num(optEntry) : '—' },
        { label: 'Opt LTP',      value: optCur   != null ? '₹' + _num(optCur)   : '…' },
        { label: 'Opt P&L (₹)', value: optPnl   != null ? _rtpFmtInr(optPnl)   : '…',
          cls: _rtpPnlCls(optPnl) },
    ];
}

// Realised ₹ for a completed trade, net of round-trip brokerage.
function _algoNetInr(t) {
    if (!t || t.opt_pnl_inr == null) return 0;
    return (Number(t.opt_pnl_inr) || 0) - _ALGO_BROKERAGE_PER_LOT;
}

// ── Active Trade tab (all live option algos consolidated) ─────────────────────
// Each source shares the same status shape: { active, state.active_trade, live }.
const _ACTIVE_SOURCES = [
    { label: 'EMA RTP 1m',     url: '/api/algo/rtp/status',    histUrl: '/api/algo/rtp/history',    mode: 'live'  },
    { label: 'EMA RTP 30s',    url: '/api/algo/rtp30s/status', histUrl: '/api/algo/rtp30s/history', mode: 'live'  },
    { label: 'EMA RTP 2m',     url: '/api/algo/rtp2m/status',  histUrl: '/api/algo/rtp2m/history',  mode: 'live'  },
    { label: 'EMA RTP 3m',     url: '/api/algo/rtp3m/status',  histUrl: '/api/algo/rtp3m/history',  mode: 'live'  },
    { label: 'EMA RTP 5m',     url: '/api/algo/rtp5m/status',  histUrl: '/api/algo/rtp5m/history',  mode: 'live'  },
    { label: '2nd 30s Candle', url: '/api/algo/sc/status',     histUrl: '/api/algo/sc/history',     mode: 'live'  },
    { label: 'Intrinsic Range', url: '/api/algo/intrinsic-range/status', histUrl: '/api/algo/intrinsic-range/history', mode: 'paper' },
];

// Small "Live"/"Paper" pill shown per-row in the consolidated Active tab.
function _algoModeChip(mode) {
    const m = (mode || 'live').toLowerCase();
    const label = m === 'paper' ? 'Paper' : 'Live';
    return `<span class="ag-mode-chip ${m}">${label}</span>`;
}

// Local YYYY-MM-DD (matches the `date` field the algos write in IST).
function _activeTodayStr() {
    const d = new Date();
    return d.getFullYear() + '-' +
        String(d.getMonth() + 1).padStart(2, '0') + '-' +
        String(d.getDate()).padStart(2, '0');
}

function _activeFetchAll(btn) {
    if (btn) { btn.disabled = true; btn.classList.add('busy'); }
    const statusReqs = _ACTIVE_SOURCES.map(s =>
        fetch(s.url)
            .then(r => r.json())
            .then(data => ({ src: s, data }))
            .catch(() => ({ src: s, data: null }))
    );
    const histReqs = _ACTIVE_SOURCES.map(s =>
        fetch(s.histUrl)
            .then(r => r.json())
            .then(data => ({ src: s, trades: (data && data.trades) || [] }))
            .catch(() => ({ src: s, trades: [] }))
    );
    Promise.all([Promise.all(statusReqs), Promise.all(histReqs)]).then(([statuses, histories]) => {
        const rows = [];
        statuses.forEach(({ src, data }) => {
            const trade = data && data.active && data.state ? data.state.active_trade : null;
            if (trade) rows.push({ label: src.label, mode: src.mode, trade, live: data.live || null });
        });

        // Completed trades from every algo, today only, newest exit first.
        const today = _activeTodayStr();
        const todayTrades = [];
        histories.forEach(({ src, trades }) => {
            trades.forEach(t => {
                const d = t.date || (t.entry_time ? String(t.entry_time).slice(0, 10) : '');
                if (d === today) todayTrades.push({ label: src.label, mode: src.mode, ...t });
            });
        });
        todayTrades.sort((a, b) =>
            String(b.exit_time || b.entry_time || '').localeCompare(String(a.exit_time || a.entry_time || '')));

        _activeRender(rows);
        _activeRenderHistory(todayTrades);
        _activeRenderPnl(rows, todayTrades);
        document.getElementById('activeLastUpd').textContent =
            new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }).finally(() => {
        if (btn) { btn.disabled = false; btn.classList.remove('busy'); }
        // Auto-refresh only while this tab is still open
        clearTimeout(_activeTimer);
        const panel = document.getElementById('algo-active-panel');
        if (panel && panel.classList.contains('active')) {
            _activeTimer = setTimeout(() => _activeFetchAll(), 15000);
        }
    });
}

function _activeRender(rows) {
    const badge    = document.getElementById('activeBadge');
    const badgeTxt = document.getElementById('activeBadgeText');
    const body     = document.getElementById('activeTradesBody');

    badge.className = 'ag-badge ' + (rows.length ? 'active' : 'inactive');
    badgeTxt.textContent = rows.length
        ? `${rows.length} active trade${rows.length > 1 ? 's' : ''}`
        : 'No active trades';

    if (!rows.length) {
        body.innerHTML = '<div class="ag-empty">No active trades</div>';
        return;
    }

    body.innerHTML = `
<div class="ag-hist-scroll">
<table class="ag-hist-table">
    <thead>
        <tr>
            <th class="ag-hist-th">Logic Type</th>
            <th class="ag-hist-th">Mode</th>
            <th class="ag-hist-th">Direction</th>
            <th class="ag-hist-th">Option</th>
            <th class="ag-hist-th">Entry Spot</th>
            <th class="ag-hist-th">Live Spot</th>
            <th class="ag-hist-th">SL Level</th>
            <th class="ag-hist-th">Target</th>
            <th class="ag-hist-th">Opt Entry ₹</th>
            <th class="ag-hist-th">Opt LTP ₹</th>
            <th class="ag-hist-th">Opt P&amp;L (₹)</th>
            <th class="ag-hist-th">Entry Time</th>
        </tr>
    </thead>
    <tbody>
    ${rows.map(({ label, mode, trade, live }) => {
        const dirCls   = trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg';
        const spotStr  = live ? '₹' + _num(live.spot) : '…';
        const optEntry = live && live.opt_entry_price != null
            ? live.opt_entry_price : trade.opt_entry_price;
        const optCur   = live ? live.opt_current_price : null;
        // Long option position → P&L = (LTP − entry premium) × lot size.
        const optPnlInr = _activeOpenPnlInr(trade, live);
        const optPnlStr = optPnlInr != null ? _rtpFmtInr(optPnlInr) : '…';
        return `<tr>
            <td class="ag-hist-td" style="font-weight:700">${label}</td>
            <td class="ag-hist-td">${_algoModeChip(mode)}</td>
            <td class="ag-hist-td ${dirCls}" style="font-weight:700">${trade.direction ?? '—'}</td>
            <td class="ag-hist-td">${(trade.option_type ?? '') + ' ' + (trade.strike ?? '—')}</td>
            <td class="ag-hist-td">₹${_num(trade.entry_spot)}</td>
            <td class="ag-hist-td">${spotStr}</td>
            <td class="ag-hist-td ag-warn">₹${_num(trade.sl_level)}</td>
            <td class="ag-hist-td ag-pos">₹${_num(trade.target_level)}</td>
            <td class="ag-hist-td">${optEntry != null ? '₹' + _num(optEntry) : '—'}</td>
            <td class="ag-hist-td">${optCur != null ? '₹' + _num(optCur) : '…'}</td>
            <td class="ag-hist-td ${_rtpPnlCls(optPnlInr)}" style="font-weight:700">${optPnlStr}</td>
            <td class="ag-hist-td">${trade.entry_time ? _fmtTime(trade.entry_time) : '—'}</td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

// ── Today's total P&L summary (realised from executed + unrealised from open) ──
function _activeRenderPnl(rows, todayTrades) {
    const statsEl = document.getElementById('activePnlStats');
    const netEl   = document.getElementById('activePnlNet');
    if (!statsEl) return;

    const done = todayTrades.filter(t => t.opt_pnl_inr != null);
    let realised = 0, wins = 0, losses = 0;
    done.forEach(t => {
        const inr = _algoNetInr(t);   // ₹ net of round-trip brokerage
        realised += inr;
        if (inr >= 0) wins++; else losses++;
    });

    // Unrealised ₹ across currently open trades: (LTP − entry) × lot size.
    let unrealised = 0, openWithLive = 0;
    rows.forEach(({ trade, live }) => {
        const inr = _activeOpenPnlInr(trade, live);
        if (inr != null) { unrealised += inr; openWithLive++; }
    });

    const total  = realised + unrealised;
    const inrFmt = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');

    const tiles = [
        { label: 'Total P&L (₹)',      value: inrFmt(total),      cls: _rtpPnlCls(total) },
        { label: 'Realised (₹)',       value: inrFmt(realised),   cls: _rtpPnlCls(realised) },
        { label: 'Unrealised (₹)',     value: openWithLive ? inrFmt(unrealised) : '—', cls: openWithLive ? _rtpPnlCls(unrealised) : '' },
        { label: 'Executed Trades',    value: done.length },
        { label: 'Wins',               value: wins,   cls: 'ag-pos' },
        { label: 'Losses',             value: losses, cls: 'ag-neg' },
        { label: 'Open Trades',        value: rows.length },
        { label: 'Brokerage (₹)',      value: done.length ? '-₹' + (done.length * _ALGO_BROKERAGE_PER_LOT).toLocaleString('en-IN') : '—', cls: done.length ? 'ag-neg' : '' },
    ];

    statsEl.innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    if (netEl) {
        netEl.textContent = inrFmt(total);
        netEl.style.color = total >= 0 ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }
}

// ── Today's executed (completed) trades across all live algos ─────────────────
function _activeRenderHistory(trades) {
    const countEl = document.getElementById('activeHistCount');
    const body    = document.getElementById('activeHistBody');
    if (!body) return;

    if (countEl) countEl.textContent = trades.length
        ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No trades executed today</div>';
        return;
    }

    const fmtOpt = v => v == null ? '—' : '₹' + Number(v).toFixed(2);
    const fmtInr = v => v == null ? '—' : (v >= 0 ? '+₹' : '-₹') + Math.abs(Number(v)).toFixed(0);

    body.innerHTML = `
<div class="ag-hist-scroll">
<table class="ag-hist-table">
    <thead>
        <tr>
            <th class="ag-hist-th">Logic Type</th>
            <th class="ag-hist-th">Mode</th>
            <th class="ag-hist-th">Direction</th>
            <th class="ag-hist-th">Option</th>
            <th class="ag-hist-th">Entry Time</th>
            <th class="ag-hist-th">Exit Time</th>
            <th class="ag-hist-th">Opt Entry</th>
            <th class="ag-hist-th">Opt Exit</th>
            <th class="ag-hist-th">Opt Pts</th>
            <th class="ag-hist-th">Opt P&amp;L (₹)</th>
            <th class="ag-hist-th">Reason</th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const dirCls  = t.direction === 'BUY' ? 'ag-pos' : 'ag-neg';
        const optPts  = t.opt_pnl_pts ?? (t.opt_exit_price != null && t.opt_entry_price != null
                            ? +(t.opt_exit_price - t.opt_entry_price).toFixed(2) : null);
        const pnlCls  = _rtpPnlCls(optPts);
        return `<tr>
            <td class="ag-hist-td" style="font-weight:700">${t.label}</td>
            <td class="ag-hist-td">${_algoModeChip(t.mode)}</td>
            <td class="ag-hist-td ${dirCls}" style="font-weight:700">${t.direction ?? '—'}</td>
            <td class="ag-hist-td">${(t.option_type ?? '') + ' ' + (t.strike ?? '—')}</td>
            <td class="ag-hist-td">${t.entry_time ? _fmtTimeOnly(t.entry_time) : '—'}</td>
            <td class="ag-hist-td">${t.exit_time ? _fmtTimeOnly(t.exit_time) : '—'}</td>
            <td class="ag-hist-td">${fmtOpt(t.opt_entry_price)}</td>
            <td class="ag-hist-td">${fmtOpt(t.opt_exit_price)}</td>
            <td class="ag-hist-td ${optPts != null ? pnlCls : ''}" style="font-weight:700">${optPts != null ? (optPts >= 0 ? '+' : '') + optPts.toFixed(2) : '—'}</td>
            <td class="ag-hist-td ${t.opt_pnl_inr != null ? pnlCls : ''}" style="font-weight:700">${fmtInr(t.opt_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtpFmtReason(t.reason)}</td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

function algoLoad() {
    const hash = location.hash.replace('#', '');
    algoSwitch(_ALGO_TABS.includes(hash) ? hash : 'active');
    _algoLoadStrikeModes();
}

// ── Strike-selection mode (dropdown next to the Δ Strikes button) ─────────────
// 'premium250' (default): strike whose premium is nearest ₹250.
// 'premium':            strike priced inside ₹300–350, nearest ₹300.
// 'delta':              classic ±0.90-delta strike with the ₹500 premium cap.

const _ALGO_STRIKE_MODE_SELECTS = {
    rtp:    'rtpStrikeMode',
    rtp30s: 'rtp30sStrikeMode',
    rtp2m:  'rtp2mStrikeMode',
    rtp3m:  'rtp3mStrikeMode',
    rtp5m:  'rtp5mStrikeMode',
    sc:     'scStrikeMode',
};

function _algoLoadStrikeModes() {
    Object.entries(_ALGO_STRIKE_MODE_SELECTS).forEach(([algo, id]) => {
        const sel = document.getElementById(id);
        if (!sel) return;
        fetch(`/api/algo/${algo}/strike-mode`)
            .then(r => r.json())
            .then(d => { if (d.success && d.mode) sel.value = d.mode; })
            .catch(() => {});
    });
}

function _algoSetStrikeMode(algo, sel) {
    const mode = sel.value;
    sel.disabled = true;
    fetch(`/api/algo/${algo}/strike-mode`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ mode }),
    })
        .then(r => r.json())
        .then(d => {
            sel.disabled = false;
            if (!d.success) {
                alert('Failed to save strike mode: ' + (d.error || 'Unknown error'));
                _algoLoadStrikeModes();   // revert to server value
            }
        })
        .catch(e => {
            sel.disabled = false;
            alert('Request failed: ' + e);
            _algoLoadStrikeModes();
        });
}

// ── Tab switching ─────────────────────────────────────────────────────────────

function algoSwitch(tab) {
    _ALGO_TABS.forEach(t => {
        document.getElementById('algo-' + t + '-panel').classList.toggle('active', t === tab);
        document.getElementById('algo-tab-'  + t).classList.toggle('active', t === tab);
    });
    history.replaceState(null, '', '#' + tab);
    clearTimeout(_rtpStatusTimer);
    clearTimeout(_rtpHistoryTimer);
    clearTimeout(_rtp30sStatusTimer);
    clearTimeout(_rtp30sHistoryTimer);
    clearTimeout(_rtp2mStatusTimer);
    clearTimeout(_rtp2mHistoryTimer);
    clearTimeout(_rtp3mStatusTimer);
    clearTimeout(_rtp3mHistoryTimer);
    clearTimeout(_rtp5mStatusTimer);
    clearTimeout(_rtp5mHistoryTimer);
    clearTimeout(_scStatusTimer);
    clearTimeout(_scHistoryTimer);
    clearTimeout(_intrinsicStatusTimer);
    clearTimeout(_intrinsicHistoryTimer);
    clearTimeout(_activeTimer);
    if (tab === 'active') {
        _activeFetchAll();
    } else if (tab === 'rtp') {
        _rtpFetchStatus();
        _rtpFetchHistory();
    } else if (tab === 'rtp30s') {
        _rtp30sFetchStatus();
        _rtp30sFetchHistory();
    } else if (tab === 'rtp2m') {
        _rtp2mFetchStatus();
        _rtp2mFetchHistory();
    } else if (tab === 'rtp3m') {
        _rtp3mFetchStatus();
        _rtp3mFetchHistory();
    } else if (tab === 'rtp5m') {
        _rtp5mFetchStatus();
        _rtp5mFetchHistory();
    } else if (tab === 'sc') {
        scLoadSettings();
        _scFetchStatus();
        _scFetchHistory();
    } else if (tab === 'intrinsic') {
        _intrinsicFetchStatus();
        _intrinsicFetchHistory();
    } else if (tab === 'swing-momentum') {
        _smLiveFetchConfigs();
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

    const spotStr = live ? '₹' + _num(live.spot) : '…';

    const tiles = [
        { label: 'Direction',    value: trade.direction,
          cls: trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg' },
        { label: 'Option',       value: trade.option_type + ' ' + trade.strike },
        { label: 'Entry Spot',   value: '₹' + _num(trade.entry_spot) },
        { label: 'Live Spot',    value: spotStr },
        { label: 'SL Level',     value: '₹' + _num(trade.sl_level),     cls: 'ag-warn' },
        { label: 'Target Level', value: '₹' + _num(trade.target_level), cls: 'ag-pos' },
        ..._algoOptTiles(trade, live),
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

    // Performance dashboard (cards + charts) built from the same JSON
    _rtpRenderDashboard(trades);

    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No completed trades</div>';
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
            <th class="ag-hist-th">N Pts</th>
            <th class="ag-hist-th">Opt Pts</th>
            <th class="ag-hist-th">Opt P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
            <th class="ag-hist-th"></th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const dir       = t.direction === 'BUY' ? 'CE BUY' : 'PE BUY';
        const dirCls    = t.direction === 'BUY' ? 'ce' : 'pe';
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const optPts    = t.opt_pnl_pts ?? (t.opt_exit_price != null && t.opt_entry_price != null
                              ? +(t.opt_exit_price - t.opt_entry_price).toFixed(2) : null);
        const oPnlCls   = (optPts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const dateStr   = t.date || (t.entry_time ? t.entry_time.slice(0, 10) : '—');
        const entryTime = t.entry_time ? _fmtTimeOnly(t.entry_time) : '—';
        const exitTime  = t.exit_time  ? _fmtTimeOnly(t.exit_time)  : '—';
        const entryKey  = (t.entry_time || '').replace(/"/g, '&quot;');
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
            <td class="ag-hist-td ${optPts != null ? oPnlCls : ''}" style="font-weight:700">${optPts != null ? (optPts >= 0 ? '+' : '') + optPts.toFixed(2) : '—'}</td>
            <td class="ag-hist-td ${t.opt_pnl_inr != null ? oPnlCls : ''}" style="font-weight:700">${_fmtInr(t.opt_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtpFmtReason(t.reason)}</td>
            <td class="ag-hist-td ag-hist-td-del">
                <button class="ag-hist-del-btn" onclick="_rtpDeleteTrade('${entryKey}')" title="Delete record">&#128465;</button>
            </td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

// ── RTP live performance dashboard (cards + charts from history JSON) ──────────
let _rtpEquityChart    = null;
let _rtpBreakdownChart = null;
let _rtpDashTrades     = [];
let _rtpDashPeriod     = 'monthly';

// Plugin: draw +/- ₹ labels above/below each breakdown bar.
const _rtpBarLabelPlugin = {
    id: 'rtpBarLabels',
    afterDatasetsDraw(chart, _, opts) {
        const { ctx, data } = chart;
        const m = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = '600 9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.textAlign = 'center';
        m.data.forEach((bar, i) => {
            const v = data.datasets[0].data[i];
            if (v == null) return;
            ctx.fillStyle = v >= 0 ? '#16a34a' : '#dc2626';
            if (v >= 0) { ctx.textBaseline = 'bottom'; ctx.fillText(opts.fmt(v), bar.x, bar.y - 3); }
            else        { ctx.textBaseline = 'top';    ctx.fillText(opts.fmt(v), bar.x, bar.y + 3); }
        });
        ctx.restore();
    }
};

function _rtpRenderDashboard(trades) {
    const card = document.getElementById('rtpDashCard');
    if (!card) return;

    // Only completed trades carrying a realised ₹ figure feed the dashboard.
    const done = (trades || []).filter(t => t.opt_pnl_inr != null);
    _rtpDashTrades = done;

    if (!done.length) { card.style.display = 'none'; return; }
    card.style.display = '';

    // ── Aggregate stats straight from the JSON ─────────────────
    let wins = 0, losses = 0, grossWin = 0, grossLoss = 0;
    let netInr = 0, netPts = 0, winPts = 0, lossPts = 0;
    let cntEod = 0, cntSl = 0, cntTgt = 0;
    done.forEach(t => {
        const inr = _algoNetInr(t);   // ₹ net of round-trip brokerage
        const pts = Number(t.opt_pnl_pts) || 0;   // option points
        netInr += inr; netPts += pts;
        if (inr >= 0) { wins++;   grossWin  += inr;           winPts  += pts; }
        else          { losses++; grossLoss += Math.abs(inr); lossPts += pts; }
        const reason = String(t.reason || '').toUpperCase();
        if      (reason === 'EOD')    cntEod++;
        else if (reason === 'SL')     cntSl++;
        else if (reason === 'TARGET') cntTgt++;
    });
    const total   = done.length;
    const winRate = total ? (wins / total * 100) : 0;
    const pf      = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? Infinity : 0);
    const avgWin  = wins   ? winPts  / wins   : null;
    const avgLoss = losses ? lossPts / losses : null;
    const maxDD   = _rtpMaxDrawdown(done);   // ₹, ≤ 0
    const brokTot = total * _ALGO_BROKERAGE_PER_LOT;   // ₹ brokerage deducted

    const inrFmt = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');
    const ptsFmt = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + ' pts';

    const tiles = [
        { label: 'Total Trades',  value: total },
        { label: 'Wins',          value: wins,   cls: 'ag-pos' },
        { label: 'Losses',        value: losses, cls: 'ag-neg' },
        { label: 'Win Rate',      value: winRate.toFixed(1) + '%' },
        { label: 'Net P&L (₹)',   value: inrFmt(netInr), cls: _rtpPnlCls(netInr) },
        { label: 'Net Opt Pts',   value: ptsFmt(netPts), cls: _rtpPnlCls(netPts) },
        { label: 'Brokerage (₹)', value: '-₹' + Math.round(brokTot).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Profit Factor', value: pf === Infinity ? '∞' : pf.toFixed(2) },
        { label: 'Avg Win (opt)', value: ptsFmt(avgWin),  cls: 'ag-pos' },
        { label: 'Avg Loss (opt)',value: ptsFmt(avgLoss), cls: 'ag-neg' },
        { label: 'Max Drawdown',  value: '-₹' + Math.abs(Math.round(maxDD)).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Target',        value: cntTgt, cls: 'ag-pos' },
        { label: 'SL',            value: cntSl,  cls: 'ag-neg' },
        { label: 'EOD',           value: cntEod, cls: 'ag-warn' },
    ];

    document.getElementById('rtpDashStats').innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    const netEl = document.getElementById('rtpDashNet');
    if (netEl) {
        netEl.textContent = inrFmt(netInr);
        netEl.style.color = netInr >= 0 ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    _rtpRenderEquity(done);
    _rtpRenderBreakdown(done, _rtpDashPeriod);
}

function _rtpMaxDrawdown(trades) {
    const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    let cum = 0, peak = 0, maxDD = 0;
    sorted.forEach(t => {
        cum += _algoNetInr(t);
        if (cum > peak) peak = cum;
        const dd = cum - peak;
        if (dd < maxDD) maxDD = dd;
    });
    return maxDD;   // ≤ 0
}

function _rtpRenderEquity(trades) {
    const ctx = document.getElementById('rtpEquityChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const sorted  = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    const labels  = ['Start'];
    const dataPts = [0];
    const dates   = [''];
    let cum = 0;
    sorted.forEach((t, i) => {
        cum += _algoNetInr(t);
        labels.push('T' + (i + 1));
        dataPts.push(Math.round(cum));
        dates.push(t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : '');
    });

    const net    = dataPts[dataPts.length - 1];
    const profit = net >= 0;
    const line   = profit ? '#2962ff' : '#ff1744';
    const fill   = profit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

    const meta = document.getElementById('rtpEquityMeta');
    if (meta) {
        meta.textContent = (net >= 0 ? '+₹' : '-₹') + Math.abs(net).toLocaleString('en-IN');
        meta.style.color = profit ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    const fmtY = v => {
        const a = Math.abs(v), s = v < 0 ? '-' : '';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
        return s + '₹' + a;
    };

    if (_rtpEquityChart) { _rtpEquityChart.destroy(); _rtpEquityChart = null; }
    _rtpEquityChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{
            data: dataPts, borderColor: line, backgroundColor: fill,
            fill: true, tension: 0.25, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2,
        }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    title: items => { const i = items[0].dataIndex; return i === 0 ? 'Start' : `Trade ${i} · ${dates[i]}`; },
                    label: item => '  Cum P&L: ' + (item.raw >= 0 ? '+₹' : '-₹') + Math.abs(item.raw).toLocaleString('en-IN'),
                } },
            },
            scales: {
                x: { ticks: { maxTicksLimit: 15, color: '#999', font: { size: 11 }, autoSkip: true }, grid: { color: 'rgba(128,128,128,0.08)' } },
                y: { ticks: { color: '#999', font: { size: 11 }, callback: fmtY }, grid: { color: 'rgba(128,128,128,0.1)' } },
            }
        }
    });
}

function rtpSetPeriod(period) {
    _rtpDashPeriod = period;
    document.querySelectorAll('#rtpPeriodTabs .rtp-period-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.period === period));
    _rtpRenderBreakdown(_rtpDashTrades, period);
}

function _rtpPeriodKey(d, period) {
    if (period === 'daily')  return d.toISOString().slice(0, 10);
    if (period === 'weekly') {
        const x = new Date(d); x.setHours(0, 0, 0, 0);
        x.setDate(x.getDate() - x.getDay() + 1);   // Monday
        return x.toISOString().slice(0, 10);
    }
    if (period === 'monthly') return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    return `${d.getFullYear()}`;
}

function _rtpRenderBreakdown(trades, period) {
    const ctx = document.getElementById('rtpBreakdownChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const groups = {};
    (trades || []).forEach(t => {
        const key = _rtpPeriodKey(new Date(t.entry_time), period);
        if (!groups[key]) groups[key] = { inr: 0, wins: 0, losses: 0 };
        const inr = _algoNetInr(t);
        groups[key].inr += inr;
        if (inr >= 0) groups[key].wins++; else groups[key].losses++;
    });
    const keys   = Object.keys(groups).sort();
    const labels = keys.map(k => {
        if (period === 'monthly') { const [y, m] = k.split('-'); return new Date(+y, +m - 1).toLocaleString('default', { month: 'short', year: '2-digit' }); }
        if (period === 'weekly')  return 'W ' + k.slice(5);
        if (period === 'daily')   return k.slice(5);
        return k;
    });
    const values = keys.map(k => Math.round(groups[k].inr));
    const meta   = keys.map(k => groups[k]);
    const bg  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)' : 'rgba(239,68,68,.20)');
    const brd = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)' : 'rgba(239,68,68,.90)');

    const fmtBar = v => {
        const a = Math.abs(v), s = v >= 0 ? '+' : '−';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(1) + 'K';
        return s + '₹' + a;
    };

    if (_rtpBreakdownChart) { _rtpBreakdownChart.destroy(); _rtpBreakdownChart = null; }
    _rtpBreakdownChart = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        plugins: [_rtpBarLabelPlugin],
        data: { labels, datasets: [{ data: values, backgroundColor: bg, borderColor: brd, borderWidth: 1.5, borderRadius: 4, borderSkipped: false }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            layout: { padding: { top: 18, bottom: 4 } },
            plugins: {
                legend: { display: false },
                rtpBarLabels: { fmt: fmtBar },
                tooltip: { displayColors: false, padding: 10, callbacks: {
                    title: c => labels[c[0].dataIndex],
                    label: c => {
                        const i = c.dataIndex, v = values[i], g = meta[i];
                        const tr = g.wins + g.losses, wr = tr ? Math.round(g.wins / tr * 100) : 0;
                        return [
                            ' P&L: ' + (v >= 0 ? '+₹' : '-₹') + Math.abs(v).toLocaleString('en-IN'),
                            ` Trades: ${tr}  (${g.wins}W / ${g.losses}L)`,
                            ` Win Rate: ${wr}%`,
                        ];
                    },
                } },
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, color: '#94a3b8' } },
                y: {
                    grid: { color: c => c.tick.value === 0 ? 'rgba(128,128,128,.3)' : 'rgba(128,128,128,.08)' },
                    ticks: { font: { size: 9 }, color: '#94a3b8', callback: v => {
                        if (v === 0) return '0';
                        const a = Math.abs(v), s = v < 0 ? '−' : '';
                        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
                        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
                        return s + '₹' + a;
                    } },
                },
            }
        }
    });
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
        TARGET:        '<span class="ag-reason-badge target">TARGET</span>',
        SL:            '<span class="ag-reason-badge sl">SL</span>',
        EOD:           '<span class="ag-reason-badge eod">EOD</span>',
        MANUAL:        '<span class="ag-reason-badge manual">MANUAL</span>',
        RANGE_RECLAIM: '<span class="ag-reason-badge range-reclaim">RANGE RECLAIM</span>',
    };
    return map[reason] || reason || '—';
}

// ── RTP Delete History Record ─────────────────────────────────────────────────

function _rtpDeleteAllTrades() {
    if (!confirm('Delete ALL EMA RTP 1m trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/rtp/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtpHistoryTimer);
            _rtpFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _rtpDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/rtp/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtpHistoryTimer);
            _rtpFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── RTP Force Exit ────────────────────────────────────────────────────────────

// ── RTP Strategy Logic popup ──────────────────────────────────────────────────
// Info button (right of the "EMA RTP 1m — Railway Track" title) opens a modal that
// explains the strategy logic plus the available Entry and Exit options.

function rtpShowLogic() {
    document.getElementById('rtpLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'rtpLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">EMA RTP 1m — Railway Track</span>
            <span class="sm-modal-subtitle">How it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('rtpLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <!-- Timeframe -->
        <div class="rtp-tf"><span class="rtp-tf-lbl">Timeframe</span><span class="rtp-tf-val">1&#8209;min candles</span><span class="rtp-tf-sub">EMA 9 · 20 · 50 · confirm&nbsp;candle&nbsp;2&nbsp;bars</span></div>

        <!-- One-line idea -->
        <p class="rtp-idea">Trade only when EMA&nbsp;20 &amp; 50 run <b>parallel and trending</b> — like two railway tracks. A pullback that touches the track and holds <b>arms</b> the entry; the trade fires only when a candle <b>breaks the signal candle's high/low within 2 bars</b> (unconfirmed signals are dropped).</p>

        <!-- Entry: BUY vs SELL side by side -->
        <div class="rtp-blk-lbl entry">Entry — price touches EMA, then closes beyond it</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY</div>
                <div class="rtp-duo-row">Low <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close above</b> EMA&nbsp;9 &amp; 20</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL</div>
                <div class="rtp-duo-row">High <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close below</b> EMA&nbsp;9 &amp; 20</div>
            </div>
        </div>
        <div class="rtp-mode-note"><code>RTP(50)</code> mode uses EMA&nbsp;50 alone. Entry fires on the <b>confirmation break</b> (signal candle high/low crossed within 2 bars); the break price is the SL/Target reference.</div>

        <!-- Exit: compact chip grid -->
        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target +90 pts</b><span>book profit</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss −30 pts</b><span>fixed, no trail</span></div></div>
            <div class="rtp-chip rtp-chip-wide"><span class="rtp-chip-ic eod">⏱</span><div><b>EOD 3:28 PM</b><span>force exit · no overnight hold</span></div></div>
        </div>
        <div class="rtp-mode-note">Levels are measured in points from the <b>NIFTY entry spot</b>. Stop Loss is <b>fixed</b> — no trailing.</div>

    </div>

</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.addEventListener('keydown', function esc(ev) {
        if (ev.key === 'Escape') { document.getElementById('rtpLogicModal')?.remove(); document.removeEventListener('keydown', esc); }
    });
    document.body.appendChild(modal);
}


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



// ══════════════════════════════════════════════════════════════════════════════
// EMA RTP 30s live algo (same RTP logic on 30-second candles; reuses generic helpers)
// ══════════════════════════════════════════════════════════════════════════════

// ── RTP 30s status ────────────────────────────────────────────────────────────────

function _rtp30sFetchStatus() {
    fetch('/api/algo/rtp30s/status')
        .then(r => r.json())
        .then(data => {
            _rtp30sRenderStatus(data);
            // Detect trade state changes and immediately refresh history so opt
            // entry/exit values reflect the just-completed or just-entered trade
            // rather than waiting up to 30 s for the scheduled history poll.
            const newEntryTime = data.state && data.state.active_trade
                ? data.state.active_trade.entry_time : null;
            const newActive = !!data.active;
            const tradeChanged = newEntryTime !== _rtp30sLastEntryTime ||
                                 newActive !== _rtp30sLastActiveFlag;
            _rtp30sLastEntryTime  = newEntryTime;
            _rtp30sLastActiveFlag = newActive;
            if (tradeChanged) {
                clearTimeout(_rtp30sHistoryTimer);
                _rtp30sFetchHistory();
            }
            clearTimeout(_rtp30sStatusTimer);
            _rtp30sStatusTimer = setTimeout(_rtp30sFetchStatus, newActive ? 5000 : 30000);
        })
        .catch(() => {
            clearTimeout(_rtp30sStatusTimer);
            _rtp30sStatusTimer = setTimeout(_rtp30sFetchStatus, 30000);
        });
}

function _rtp30sRenderStatus(data) {
    const trade  = data.state && data.state.active_trade;
    const live   = data.live || null;
    const active = !!data.active;

    // Badge
    const badge = document.getElementById('rtp30sBadge');
    badge.className = 'ag-badge ' + (active ? 'active' : 'inactive');
    document.getElementById('rtp30sBadgeText').textContent =
        active ? (trade.direction + ' ' + trade.option_type) : 'No Trade';

    // Timestamp
    document.getElementById('rtp30sLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Active trade grid
    const grid = document.getElementById('rtp30sActiveGrid');
    if (!active || !trade) {
        grid.innerHTML = '<div class="ag-empty">No active trade</div>';
        return;
    }

    const spotStr = live ? '₹' + _num(live.spot) : '…';

    const tiles = [
        { label: 'Direction',    value: trade.direction,
          cls: trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg' },
        { label: 'Option',       value: trade.option_type + ' ' + trade.strike },
        { label: 'Entry Spot',   value: '₹' + _num(trade.entry_spot) },
        { label: 'Live Spot',    value: spotStr },
        { label: 'SL Level',     value: '₹' + _num(trade.sl_level),     cls: 'ag-warn' },
        { label: 'Target Level', value: '₹' + _num(trade.target_level), cls: 'ag-pos' },
        ..._algoOptTiles(trade, live),
        { label: 'Entry Time',   value: trade.entry_time ? _fmtTime(trade.entry_time) : '—' },
    ];

    grid.innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    // Toggle Force Exit button based on trade state
    const exitBtn = document.getElementById('rtp30sExitBtn');
    if (exitBtn) exitBtn.disabled = !active;
}

// ── RTP history ───────────────────────────────────────────────────────────────

function _rtp30sFetchHistory() {
    fetch('/api/algo/rtp30s/history')
        .then(r => r.json())
        .then(data => {
            _rtp30sRenderHistory(data.trades || []);
            clearTimeout(_rtp30sHistoryTimer);
            _rtp30sHistoryTimer = setTimeout(_rtp30sFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_rtp30sHistoryTimer);
            _rtp30sHistoryTimer = setTimeout(_rtp30sFetchHistory, 30000);
        });
}

function _rtp30sRenderHistory(trades) {
    const countEl = document.getElementById('rtp30sHistCount');
    const body    = document.getElementById('rtp30sHistBody');

    // Performance dashboard (cards + charts) built from the same JSON
    _rtp30sRenderDashboard(trades);

    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No completed trades</div>';
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
            <th class="ag-hist-th">N Pts</th>
            <th class="ag-hist-th">Opt Pts</th>
            <th class="ag-hist-th">Opt P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
            <th class="ag-hist-th"></th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const dir       = t.direction === 'BUY' ? 'CE BUY' : 'PE BUY';
        const dirCls    = t.direction === 'BUY' ? 'ce' : 'pe';
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const optPts    = t.opt_pnl_pts ?? (t.opt_exit_price != null && t.opt_entry_price != null
                              ? +(t.opt_exit_price - t.opt_entry_price).toFixed(2) : null);
        const oPnlCls   = (optPts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const dateStr   = t.date || (t.entry_time ? t.entry_time.slice(0, 10) : '—');
        const entryTime = t.entry_time ? _fmtTimeOnly(t.entry_time) : '—';
        const exitTime  = t.exit_time  ? _fmtTimeOnly(t.exit_time)  : '—';
        const entryKey  = (t.entry_time || '').replace(/"/g, '&quot;');
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
            <td class="ag-hist-td ${optPts != null ? oPnlCls : ''}" style="font-weight:700">${optPts != null ? (optPts >= 0 ? '+' : '') + optPts.toFixed(2) : '—'}</td>
            <td class="ag-hist-td ${t.opt_pnl_inr != null ? oPnlCls : ''}" style="font-weight:700">${_fmtInr(t.opt_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtp30sFmtReason(t.reason)}</td>
            <td class="ag-hist-td ag-hist-td-del">
                <button class="ag-hist-del-btn" onclick="_rtp30sDeleteTrade('${entryKey}')" title="Delete record">&#128465;</button>
            </td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

// ── RTP live performance dashboard (cards + charts from history JSON) ──────────
let _rtp30sEquityChart    = null;
let _rtp30sBreakdownChart = null;
let _rtp30sDashTrades     = [];
let _rtp30sDashPeriod     = 'monthly';

// Plugin: draw +/- ₹ labels above/below each breakdown bar.
const _rtp30sBarLabelPlugin = {
    id: 'rtp30sBarLabels',
    afterDatasetsDraw(chart, _, opts) {
        const { ctx, data } = chart;
        const m = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = '600 9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.textAlign = 'center';
        m.data.forEach((bar, i) => {
            const v = data.datasets[0].data[i];
            if (v == null) return;
            ctx.fillStyle = v >= 0 ? '#16a34a' : '#dc2626';
            if (v >= 0) { ctx.textBaseline = 'bottom'; ctx.fillText(opts.fmt(v), bar.x, bar.y - 3); }
            else        { ctx.textBaseline = 'top';    ctx.fillText(opts.fmt(v), bar.x, bar.y + 3); }
        });
        ctx.restore();
    }
};

function _rtp30sRenderDashboard(trades) {
    const card = document.getElementById('rtp30sDashCard');
    if (!card) return;

    // Only completed trades carrying a realised ₹ figure feed the dashboard.
    const done = (trades || []).filter(t => t.opt_pnl_inr != null);
    _rtp30sDashTrades = done;

    if (!done.length) { card.style.display = 'none'; return; }
    card.style.display = '';

    // ── Aggregate stats straight from the JSON ─────────────────
    let wins = 0, losses = 0, grossWin = 0, grossLoss = 0;
    let netInr = 0, netPts = 0, winPts = 0, lossPts = 0;
    let cntEod = 0, cntSl = 0, cntTgt = 0;
    done.forEach(t => {
        const inr = _algoNetInr(t);   // ₹ net of round-trip brokerage
        const pts = Number(t.opt_pnl_pts) || 0;   // option points
        netInr += inr; netPts += pts;
        if (inr >= 0) { wins++;   grossWin  += inr;           winPts  += pts; }
        else          { losses++; grossLoss += Math.abs(inr); lossPts += pts; }
        const reason = String(t.reason || '').toUpperCase();
        if      (reason === 'EOD')    cntEod++;
        else if (reason === 'SL')     cntSl++;
        else if (reason === 'TARGET') cntTgt++;
    });
    const total   = done.length;
    const winRate = total ? (wins / total * 100) : 0;
    const pf      = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? Infinity : 0);
    const avgWin  = wins   ? winPts  / wins   : null;
    const avgLoss = losses ? lossPts / losses : null;
    const maxDD   = _rtp30sMaxDrawdown(done);   // ₹, ≤ 0
    const brokTot = total * _ALGO_BROKERAGE_PER_LOT;   // ₹ brokerage deducted

    const inrFmt = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');
    const ptsFmt = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + ' pts';

    const tiles = [
        { label: 'Total Trades',  value: total },
        { label: 'Wins',          value: wins,   cls: 'ag-pos' },
        { label: 'Losses',        value: losses, cls: 'ag-neg' },
        { label: 'Win Rate',      value: winRate.toFixed(1) + '%' },
        { label: 'Net P&L (₹)',   value: inrFmt(netInr), cls: _rtp30sPnlCls(netInr) },
        { label: 'Net Opt Pts',   value: ptsFmt(netPts), cls: _rtp30sPnlCls(netPts) },
        { label: 'Brokerage (₹)', value: '-₹' + Math.round(brokTot).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Profit Factor', value: pf === Infinity ? '∞' : pf.toFixed(2) },
        { label: 'Avg Win (opt)', value: ptsFmt(avgWin),  cls: 'ag-pos' },
        { label: 'Avg Loss (opt)',value: ptsFmt(avgLoss), cls: 'ag-neg' },
        { label: 'Max Drawdown',  value: '-₹' + Math.abs(Math.round(maxDD)).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Target',        value: cntTgt, cls: 'ag-pos' },
        { label: 'SL',            value: cntSl,  cls: 'ag-neg' },
        { label: 'EOD',           value: cntEod, cls: 'ag-warn' },
    ];

    document.getElementById('rtp30sDashStats').innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    const netEl = document.getElementById('rtp30sDashNet');
    if (netEl) {
        netEl.textContent = inrFmt(netInr);
        netEl.style.color = netInr >= 0 ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    _rtp30sRenderEquity(done);
    _rtp30sRenderBreakdown(done, _rtp30sDashPeriod);
}

function _rtp30sMaxDrawdown(trades) {
    const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    let cum = 0, peak = 0, maxDD = 0;
    sorted.forEach(t => {
        cum += _algoNetInr(t);
        if (cum > peak) peak = cum;
        const dd = cum - peak;
        if (dd < maxDD) maxDD = dd;
    });
    return maxDD;   // ≤ 0
}

function _rtp30sRenderEquity(trades) {
    const ctx = document.getElementById('rtp30sEquityChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const sorted  = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    const labels  = ['Start'];
    const dataPts = [0];
    const dates   = [''];
    let cum = 0;
    sorted.forEach((t, i) => {
        cum += _algoNetInr(t);
        labels.push('T' + (i + 1));
        dataPts.push(Math.round(cum));
        dates.push(t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : '');
    });

    const net    = dataPts[dataPts.length - 1];
    const profit = net >= 0;
    const line   = profit ? '#2962ff' : '#ff1744';
    const fill   = profit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

    const meta = document.getElementById('rtp30sEquityMeta');
    if (meta) {
        meta.textContent = (net >= 0 ? '+₹' : '-₹') + Math.abs(net).toLocaleString('en-IN');
        meta.style.color = profit ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    const fmtY = v => {
        const a = Math.abs(v), s = v < 0 ? '-' : '';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
        return s + '₹' + a;
    };

    if (_rtp30sEquityChart) { _rtp30sEquityChart.destroy(); _rtp30sEquityChart = null; }
    _rtp30sEquityChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{
            data: dataPts, borderColor: line, backgroundColor: fill,
            fill: true, tension: 0.25, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2,
        }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    title: items => { const i = items[0].dataIndex; return i === 0 ? 'Start' : `Trade ${i} · ${dates[i]}`; },
                    label: item => '  Cum P&L: ' + (item.raw >= 0 ? '+₹' : '-₹') + Math.abs(item.raw).toLocaleString('en-IN'),
                } },
            },
            scales: {
                x: { ticks: { maxTicksLimit: 15, color: '#999', font: { size: 11 }, autoSkip: true }, grid: { color: 'rgba(128,128,128,0.08)' } },
                y: { ticks: { color: '#999', font: { size: 11 }, callback: fmtY }, grid: { color: 'rgba(128,128,128,0.1)' } },
            }
        }
    });
}

function rtp30sSetPeriod(period) {
    _rtp30sDashPeriod = period;
    document.querySelectorAll('#rtp30sPeriodTabs .rtp-period-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.period === period));
    _rtp30sRenderBreakdown(_rtp30sDashTrades, period);
}

function _rtp30sPeriodKey(d, period) {
    if (period === 'daily')  return d.toISOString().slice(0, 10);
    if (period === 'weekly') {
        const x = new Date(d); x.setHours(0, 0, 0, 0);
        x.setDate(x.getDate() - x.getDay() + 1);   // Monday
        return x.toISOString().slice(0, 10);
    }
    if (period === 'monthly') return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    return `${d.getFullYear()}`;
}

function _rtp30sRenderBreakdown(trades, period) {
    const ctx = document.getElementById('rtp30sBreakdownChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const groups = {};
    (trades || []).forEach(t => {
        const key = _rtp30sPeriodKey(new Date(t.entry_time), period);
        if (!groups[key]) groups[key] = { inr: 0, wins: 0, losses: 0 };
        const inr = _algoNetInr(t);
        groups[key].inr += inr;
        if (inr >= 0) groups[key].wins++; else groups[key].losses++;
    });
    const keys   = Object.keys(groups).sort();
    const labels = keys.map(k => {
        if (period === 'monthly') { const [y, m] = k.split('-'); return new Date(+y, +m - 1).toLocaleString('default', { month: 'short', year: '2-digit' }); }
        if (period === 'weekly')  return 'W ' + k.slice(5);
        if (period === 'daily')   return k.slice(5);
        return k;
    });
    const values = keys.map(k => Math.round(groups[k].inr));
    const meta   = keys.map(k => groups[k]);
    const bg  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)' : 'rgba(239,68,68,.20)');
    const brd = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)' : 'rgba(239,68,68,.90)');

    const fmtBar = v => {
        const a = Math.abs(v), s = v >= 0 ? '+' : '−';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(1) + 'K';
        return s + '₹' + a;
    };

    if (_rtp30sBreakdownChart) { _rtp30sBreakdownChart.destroy(); _rtp30sBreakdownChart = null; }
    _rtp30sBreakdownChart = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        plugins: [_rtp30sBarLabelPlugin],
        data: { labels, datasets: [{ data: values, backgroundColor: bg, borderColor: brd, borderWidth: 1.5, borderRadius: 4, borderSkipped: false }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            layout: { padding: { top: 18, bottom: 4 } },
            plugins: {
                legend: { display: false },
                rtp30sBarLabels: { fmt: fmtBar },
                tooltip: { displayColors: false, padding: 10, callbacks: {
                    title: c => labels[c[0].dataIndex],
                    label: c => {
                        const i = c.dataIndex, v = values[i], g = meta[i];
                        const tr = g.wins + g.losses, wr = tr ? Math.round(g.wins / tr * 100) : 0;
                        return [
                            ' P&L: ' + (v >= 0 ? '+₹' : '-₹') + Math.abs(v).toLocaleString('en-IN'),
                            ` Trades: ${tr}  (${g.wins}W / ${g.losses}L)`,
                            ` Win Rate: ${wr}%`,
                        ];
                    },
                } },
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, color: '#94a3b8' } },
                y: {
                    grid: { color: c => c.tick.value === 0 ? 'rgba(128,128,128,.3)' : 'rgba(128,128,128,.08)' },
                    ticks: { font: { size: 9 }, color: '#94a3b8', callback: v => {
                        if (v === 0) return '0';
                        const a = Math.abs(v), s = v < 0 ? '−' : '';
                        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
                        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
                        return s + '₹' + a;
                    } },
                },
            }
        }
    });
}

// ── RTP helpers ───────────────────────────────────────────────────────────────

function _rtp30sFmtPts(pts) {
    if (pts == null) return '…';
    return (pts >= 0 ? '+' : '') + Number(pts).toFixed(1) + ' pts';
}

function _rtp30sFmtInr(inr) {
    if (inr == null) return '…';
    return (inr >= 0 ? '+₹' : '-₹') + Math.abs(inr).toFixed(0);
}

function _rtp30sPnlCls(val) {
    if (val == null) return '';
    return val >= 0 ? 'ag-pos' : 'ag-neg';
}

function _rtp30sFmtReason(reason) {
    const map = {
        TARGET:        '<span class="ag-reason-badge target">TARGET</span>',
        SL:            '<span class="ag-reason-badge sl">SL</span>',
        EOD:           '<span class="ag-reason-badge eod">EOD</span>',
        MANUAL:        '<span class="ag-reason-badge manual">MANUAL</span>',
        RANGE_RECLAIM: '<span class="ag-reason-badge range-reclaim">RANGE RECLAIM</span>',
    };
    return map[reason] || reason || '—';
}

// ── RTP Delete History Record ─────────────────────────────────────────────────

function _rtp30sDeleteAllTrades() {
    if (!confirm('Delete ALL EMA RTP 30s trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/rtp30s/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtp30sHistoryTimer);
            _rtp30sFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _rtp30sDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/rtp30s/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtp30sHistoryTimer);
            _rtp30sFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── RTP Force Exit ────────────────────────────────────────────────────────────

// ── RTP Strategy Logic popup ──────────────────────────────────────────────────
// Info button (right of the "EMA RTP 30s — Railway Track" title) opens a modal that
// explains the strategy logic plus the available Entry and Exit options.

function rtp30sShowLogic() {
    document.getElementById('rtp30sLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'rtp30sLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">EMA RTP 30s — Railway Track</span>
            <span class="sm-modal-subtitle">How it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('rtp30sLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <!-- Timeframe -->
        <div class="rtp-tf"><span class="rtp-tf-lbl">Timeframe</span><span class="rtp-tf-val">30&#8209;sec candles</span><span class="rtp-tf-sub">EMA 9 · 20 · 50 · ADX&nbsp;≥&nbsp;25 · rail&nbsp;gap&nbsp;≥&nbsp;0.2×ATR</span></div>

        <!-- One-line idea -->
        <p class="rtp-idea">Trade only when EMA&nbsp;20 &amp; 50 run <b>parallel, trending and separated</b> — like two railway tracks — with <b>ADX&nbsp;≥&nbsp;25</b> confirming trend strength and the <b>rail gap ≥ 0.2×ATR</b> rejecting flat, braided-EMA chop. Enter on a pullback that touches the track and holds.</p>

        <!-- Entry: BUY vs SELL side by side -->
        <div class="rtp-blk-lbl entry">Entry — price touches EMA, then closes beyond it</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY</div>
                <div class="rtp-duo-row">Low <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close above</b> EMA&nbsp;9 &amp; 20</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL</div>
                <div class="rtp-duo-row">High <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close below</b> EMA&nbsp;9 &amp; 20</div>
            </div>
        </div>
        <div class="rtp-mode-note"><code>RTP(50)</code> mode uses EMA&nbsp;50 alone. Fill on <b>next candle open</b>.</div>

        <!-- Exit: compact chip grid -->
        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target +30 pts</b><span>book profit</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss −10 pts</b><span>fixed, no trail</span></div></div>
            <div class="rtp-chip rtp-chip-wide"><span class="rtp-chip-ic eod">⏱</span><div><b>EOD 3:28 PM</b><span>force exit · no overnight hold</span></div></div>
        </div>
        <div class="rtp-mode-note">Levels are measured in points from the <b>NIFTY entry spot</b>. Stop Loss is <b>fixed</b> — no trailing.</div>

    </div>

</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.addEventListener('keydown', function esc(ev) {
        if (ev.key === 'Escape') { document.getElementById('rtp30sLogicModal')?.remove(); document.removeEventListener('keydown', esc); }
    });
    document.body.appendChild(modal);
}


function rtp30sExitNow(btn) {
    if (!confirm('Force-close the active RTP trade? A market SELL order will be sent immediately.')) return;
    _setBusy(btn, 'Exiting…');
    fetch('/api/algo/rtp30s/exit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Exit failed: ' + (d.error || 'Unknown error')); _unbusy(btn, 'Force Exit'); return; }
            clearTimeout(_rtp30sStatusTimer);
            clearTimeout(_rtp30sHistoryTimer);
            _rtp30sFetchStatus();
            _rtp30sFetchHistory();
        })
        .catch(e => { alert('Request failed: ' + e); _unbusy(btn, 'Force Exit'); });
}


// ── RTP Delta Strikes ─────────────────────────────────────────────────────────

function rtp30sFetchDeltaStrikes(btn) {
    const panel = document.getElementById('rtp30sStrikesResult');
    btn.disabled = true;
    btn.classList.add('busy');
    btn.textContent = 'Loading…';
    panel.style.display = 'none';

    fetch('/api/algo/rtp30s/delta-strikes')
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




// ══════════════════════════════════════════════════════════════════════════════
// EMA RTP 3m live algo (same RTP logic on 3-minute candles; reuses generic helpers)
// ══════════════════════════════════════════════════════════════════════════════

// ── RTP 3m status ────────────────────────────────────────────────────────────────

function _rtp3mFetchStatus() {
    fetch('/api/algo/rtp3m/status')
        .then(r => r.json())
        .then(data => {
            _rtp3mRenderStatus(data);
            // Detect trade state changes and immediately refresh history so opt
            // entry/exit values reflect the just-completed or just-entered trade
            // rather than waiting up to 30 s for the scheduled history poll.
            const newEntryTime = data.state && data.state.active_trade
                ? data.state.active_trade.entry_time : null;
            const newActive = !!data.active;
            const tradeChanged = newEntryTime !== _rtp3mLastEntryTime ||
                                 newActive !== _rtp3mLastActiveFlag;
            _rtp3mLastEntryTime  = newEntryTime;
            _rtp3mLastActiveFlag = newActive;
            if (tradeChanged) {
                clearTimeout(_rtp3mHistoryTimer);
                _rtp3mFetchHistory();
            }
            clearTimeout(_rtp3mStatusTimer);
            _rtp3mStatusTimer = setTimeout(_rtp3mFetchStatus, newActive ? 5000 : 30000);
        })
        .catch(() => {
            clearTimeout(_rtp3mStatusTimer);
            _rtp3mStatusTimer = setTimeout(_rtp3mFetchStatus, 30000);
        });
}

function _rtp3mRenderStatus(data) {
    const trade  = data.state && data.state.active_trade;
    const live   = data.live || null;
    const active = !!data.active;

    // Badge
    const badge = document.getElementById('rtp3mBadge');
    badge.className = 'ag-badge ' + (active ? 'active' : 'inactive');
    document.getElementById('rtp3mBadgeText').textContent =
        active ? (trade.direction + ' ' + trade.option_type) : 'No Trade';

    // Timestamp
    document.getElementById('rtp3mLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Active trade grid
    const grid = document.getElementById('rtp3mActiveGrid');
    if (!active || !trade) {
        grid.innerHTML = '<div class="ag-empty">No active trade</div>';
        return;
    }

    const spotStr = live ? '₹' + _num(live.spot) : '…';

    const tiles = [
        { label: 'Direction',    value: trade.direction,
          cls: trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg' },
        { label: 'Option',       value: trade.option_type + ' ' + trade.strike },
        { label: 'Entry Spot',   value: '₹' + _num(trade.entry_spot) },
        { label: 'Live Spot',    value: spotStr },
        { label: 'SL Level',     value: '₹' + _num(trade.sl_level),     cls: 'ag-warn' },
        { label: 'Target Level', value: '₹' + _num(trade.target_level), cls: 'ag-pos' },
        ..._algoOptTiles(trade, live),
        { label: 'Entry Time',   value: trade.entry_time ? _fmtTime(trade.entry_time) : '—' },
    ];

    grid.innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    // Toggle Force Exit button based on trade state
    const exitBtn = document.getElementById('rtp3mExitBtn');
    if (exitBtn) exitBtn.disabled = !active;
}

// ── RTP history ───────────────────────────────────────────────────────────────

function _rtp3mFetchHistory() {
    fetch('/api/algo/rtp3m/history')
        .then(r => r.json())
        .then(data => {
            _rtp3mRenderHistory(data.trades || []);
            clearTimeout(_rtp3mHistoryTimer);
            _rtp3mHistoryTimer = setTimeout(_rtp3mFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_rtp3mHistoryTimer);
            _rtp3mHistoryTimer = setTimeout(_rtp3mFetchHistory, 30000);
        });
}

function _rtp3mRenderHistory(trades) {
    const countEl = document.getElementById('rtp3mHistCount');
    const body    = document.getElementById('rtp3mHistBody');

    // Performance dashboard (cards + charts) built from the same JSON
    _rtp3mRenderDashboard(trades);

    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No completed trades</div>';
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
            <th class="ag-hist-th">N Pts</th>
            <th class="ag-hist-th">Opt Pts</th>
            <th class="ag-hist-th">Opt P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
            <th class="ag-hist-th"></th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const dir       = t.direction === 'BUY' ? 'CE BUY' : 'PE BUY';
        const dirCls    = t.direction === 'BUY' ? 'ce' : 'pe';
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const optPts    = t.opt_pnl_pts ?? (t.opt_exit_price != null && t.opt_entry_price != null
                              ? +(t.opt_exit_price - t.opt_entry_price).toFixed(2) : null);
        const oPnlCls   = (optPts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const dateStr   = t.date || (t.entry_time ? t.entry_time.slice(0, 10) : '—');
        const entryTime = t.entry_time ? _fmtTimeOnly(t.entry_time) : '—';
        const exitTime  = t.exit_time  ? _fmtTimeOnly(t.exit_time)  : '—';
        const entryKey  = (t.entry_time || '').replace(/"/g, '&quot;');
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
            <td class="ag-hist-td ${optPts != null ? oPnlCls : ''}" style="font-weight:700">${optPts != null ? (optPts >= 0 ? '+' : '') + optPts.toFixed(2) : '—'}</td>
            <td class="ag-hist-td ${t.opt_pnl_inr != null ? oPnlCls : ''}" style="font-weight:700">${_fmtInr(t.opt_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtp3mFmtReason(t.reason)}</td>
            <td class="ag-hist-td ag-hist-td-del">
                <button class="ag-hist-del-btn" onclick="_rtp3mDeleteTrade('${entryKey}')" title="Delete record">&#128465;</button>
            </td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

// ── RTP live performance dashboard (cards + charts from history JSON) ──────────
let _rtp3mEquityChart    = null;
let _rtp3mBreakdownChart = null;
let _rtp3mDashTrades     = [];
let _rtp3mDashPeriod     = 'monthly';

// Plugin: draw +/- ₹ labels above/below each breakdown bar.
const _rtp3mBarLabelPlugin = {
    id: 'rtp3mBarLabels',
    afterDatasetsDraw(chart, _, opts) {
        const { ctx, data } = chart;
        const m = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = '600 9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.textAlign = 'center';
        m.data.forEach((bar, i) => {
            const v = data.datasets[0].data[i];
            if (v == null) return;
            ctx.fillStyle = v >= 0 ? '#16a34a' : '#dc2626';
            if (v >= 0) { ctx.textBaseline = 'bottom'; ctx.fillText(opts.fmt(v), bar.x, bar.y - 3); }
            else        { ctx.textBaseline = 'top';    ctx.fillText(opts.fmt(v), bar.x, bar.y + 3); }
        });
        ctx.restore();
    }
};

function _rtp3mRenderDashboard(trades) {
    const card = document.getElementById('rtp3mDashCard');
    if (!card) return;

    // Only completed trades carrying a realised ₹ figure feed the dashboard.
    const done = (trades || []).filter(t => t.opt_pnl_inr != null);
    _rtp3mDashTrades = done;

    if (!done.length) { card.style.display = 'none'; return; }
    card.style.display = '';

    // ── Aggregate stats straight from the JSON ─────────────────
    let wins = 0, losses = 0, grossWin = 0, grossLoss = 0;
    let netInr = 0, netPts = 0, winPts = 0, lossPts = 0;
    let cntEod = 0, cntSl = 0, cntTgt = 0;
    done.forEach(t => {
        const inr = _algoNetInr(t);   // ₹ net of round-trip brokerage
        const pts = Number(t.opt_pnl_pts) || 0;   // option points
        netInr += inr; netPts += pts;
        if (inr >= 0) { wins++;   grossWin  += inr;           winPts  += pts; }
        else          { losses++; grossLoss += Math.abs(inr); lossPts += pts; }
        const reason = String(t.reason || '').toUpperCase();
        if      (reason === 'EOD')    cntEod++;
        else if (reason === 'SL')     cntSl++;
        else if (reason === 'TARGET') cntTgt++;
    });
    const total   = done.length;
    const winRate = total ? (wins / total * 100) : 0;
    const pf      = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? Infinity : 0);
    const avgWin  = wins   ? winPts  / wins   : null;
    const avgLoss = losses ? lossPts / losses : null;
    const maxDD   = _rtp3mMaxDrawdown(done);   // ₹, ≤ 0
    const brokTot = total * _ALGO_BROKERAGE_PER_LOT;   // ₹ brokerage deducted

    const inrFmt = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');
    const ptsFmt = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + ' pts';

    const tiles = [
        { label: 'Total Trades',  value: total },
        { label: 'Wins',          value: wins,   cls: 'ag-pos' },
        { label: 'Losses',        value: losses, cls: 'ag-neg' },
        { label: 'Win Rate',      value: winRate.toFixed(1) + '%' },
        { label: 'Net P&L (₹)',   value: inrFmt(netInr), cls: _rtp3mPnlCls(netInr) },
        { label: 'Net Opt Pts',   value: ptsFmt(netPts), cls: _rtp3mPnlCls(netPts) },
        { label: 'Brokerage (₹)', value: '-₹' + Math.round(brokTot).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Profit Factor', value: pf === Infinity ? '∞' : pf.toFixed(2) },
        { label: 'Avg Win (opt)', value: ptsFmt(avgWin),  cls: 'ag-pos' },
        { label: 'Avg Loss (opt)',value: ptsFmt(avgLoss), cls: 'ag-neg' },
        { label: 'Max Drawdown',  value: '-₹' + Math.abs(Math.round(maxDD)).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Target',        value: cntTgt, cls: 'ag-pos' },
        { label: 'SL',            value: cntSl,  cls: 'ag-neg' },
        { label: 'EOD',           value: cntEod, cls: 'ag-warn' },
    ];

    document.getElementById('rtp3mDashStats').innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    const netEl = document.getElementById('rtp3mDashNet');
    if (netEl) {
        netEl.textContent = inrFmt(netInr);
        netEl.style.color = netInr >= 0 ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    _rtp3mRenderEquity(done);
    _rtp3mRenderBreakdown(done, _rtp3mDashPeriod);
}

function _rtp3mMaxDrawdown(trades) {
    const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    let cum = 0, peak = 0, maxDD = 0;
    sorted.forEach(t => {
        cum += _algoNetInr(t);
        if (cum > peak) peak = cum;
        const dd = cum - peak;
        if (dd < maxDD) maxDD = dd;
    });
    return maxDD;   // ≤ 0
}

function _rtp3mRenderEquity(trades) {
    const ctx = document.getElementById('rtp3mEquityChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const sorted  = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    const labels  = ['Start'];
    const dataPts = [0];
    const dates   = [''];
    let cum = 0;
    sorted.forEach((t, i) => {
        cum += _algoNetInr(t);
        labels.push('T' + (i + 1));
        dataPts.push(Math.round(cum));
        dates.push(t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : '');
    });

    const net    = dataPts[dataPts.length - 1];
    const profit = net >= 0;
    const line   = profit ? '#2962ff' : '#ff1744';
    const fill   = profit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

    const meta = document.getElementById('rtp3mEquityMeta');
    if (meta) {
        meta.textContent = (net >= 0 ? '+₹' : '-₹') + Math.abs(net).toLocaleString('en-IN');
        meta.style.color = profit ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    const fmtY = v => {
        const a = Math.abs(v), s = v < 0 ? '-' : '';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
        return s + '₹' + a;
    };

    if (_rtp3mEquityChart) { _rtp3mEquityChart.destroy(); _rtp3mEquityChart = null; }
    _rtp3mEquityChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{
            data: dataPts, borderColor: line, backgroundColor: fill,
            fill: true, tension: 0.25, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2,
        }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    title: items => { const i = items[0].dataIndex; return i === 0 ? 'Start' : `Trade ${i} · ${dates[i]}`; },
                    label: item => '  Cum P&L: ' + (item.raw >= 0 ? '+₹' : '-₹') + Math.abs(item.raw).toLocaleString('en-IN'),
                } },
            },
            scales: {
                x: { ticks: { maxTicksLimit: 15, color: '#999', font: { size: 11 }, autoSkip: true }, grid: { color: 'rgba(128,128,128,0.08)' } },
                y: { ticks: { color: '#999', font: { size: 11 }, callback: fmtY }, grid: { color: 'rgba(128,128,128,0.1)' } },
            }
        }
    });
}

function rtp3mSetPeriod(period) {
    _rtp3mDashPeriod = period;
    document.querySelectorAll('#rtp3mPeriodTabs .rtp-period-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.period === period));
    _rtp3mRenderBreakdown(_rtp3mDashTrades, period);
}

function _rtp3mPeriodKey(d, period) {
    if (period === 'daily')  return d.toISOString().slice(0, 10);
    if (period === 'weekly') {
        const x = new Date(d); x.setHours(0, 0, 0, 0);
        x.setDate(x.getDate() - x.getDay() + 1);   // Monday
        return x.toISOString().slice(0, 10);
    }
    if (period === 'monthly') return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    return `${d.getFullYear()}`;
}

function _rtp3mRenderBreakdown(trades, period) {
    const ctx = document.getElementById('rtp3mBreakdownChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const groups = {};
    (trades || []).forEach(t => {
        const key = _rtp3mPeriodKey(new Date(t.entry_time), period);
        if (!groups[key]) groups[key] = { inr: 0, wins: 0, losses: 0 };
        const inr = _algoNetInr(t);
        groups[key].inr += inr;
        if (inr >= 0) groups[key].wins++; else groups[key].losses++;
    });
    const keys   = Object.keys(groups).sort();
    const labels = keys.map(k => {
        if (period === 'monthly') { const [y, m] = k.split('-'); return new Date(+y, +m - 1).toLocaleString('default', { month: 'short', year: '2-digit' }); }
        if (period === 'weekly')  return 'W ' + k.slice(5);
        if (period === 'daily')   return k.slice(5);
        return k;
    });
    const values = keys.map(k => Math.round(groups[k].inr));
    const meta   = keys.map(k => groups[k]);
    const bg  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)' : 'rgba(239,68,68,.20)');
    const brd = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)' : 'rgba(239,68,68,.90)');

    const fmtBar = v => {
        const a = Math.abs(v), s = v >= 0 ? '+' : '−';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(1) + 'K';
        return s + '₹' + a;
    };

    if (_rtp3mBreakdownChart) { _rtp3mBreakdownChart.destroy(); _rtp3mBreakdownChart = null; }
    _rtp3mBreakdownChart = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        plugins: [_rtp3mBarLabelPlugin],
        data: { labels, datasets: [{ data: values, backgroundColor: bg, borderColor: brd, borderWidth: 1.5, borderRadius: 4, borderSkipped: false }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            layout: { padding: { top: 18, bottom: 4 } },
            plugins: {
                legend: { display: false },
                rtp3mBarLabels: { fmt: fmtBar },
                tooltip: { displayColors: false, padding: 10, callbacks: {
                    title: c => labels[c[0].dataIndex],
                    label: c => {
                        const i = c.dataIndex, v = values[i], g = meta[i];
                        const tr = g.wins + g.losses, wr = tr ? Math.round(g.wins / tr * 100) : 0;
                        return [
                            ' P&L: ' + (v >= 0 ? '+₹' : '-₹') + Math.abs(v).toLocaleString('en-IN'),
                            ` Trades: ${tr}  (${g.wins}W / ${g.losses}L)`,
                            ` Win Rate: ${wr}%`,
                        ];
                    },
                } },
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, color: '#94a3b8' } },
                y: {
                    grid: { color: c => c.tick.value === 0 ? 'rgba(128,128,128,.3)' : 'rgba(128,128,128,.08)' },
                    ticks: { font: { size: 9 }, color: '#94a3b8', callback: v => {
                        if (v === 0) return '0';
                        const a = Math.abs(v), s = v < 0 ? '−' : '';
                        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
                        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
                        return s + '₹' + a;
                    } },
                },
            }
        }
    });
}

// ── RTP helpers ───────────────────────────────────────────────────────────────

function _rtp3mFmtPts(pts) {
    if (pts == null) return '…';
    return (pts >= 0 ? '+' : '') + Number(pts).toFixed(1) + ' pts';
}

function _rtp3mFmtInr(inr) {
    if (inr == null) return '…';
    return (inr >= 0 ? '+₹' : '-₹') + Math.abs(inr).toFixed(0);
}

function _rtp3mPnlCls(val) {
    if (val == null) return '';
    return val >= 0 ? 'ag-pos' : 'ag-neg';
}

function _rtp3mFmtReason(reason) {
    const map = {
        TARGET:        '<span class="ag-reason-badge target">TARGET</span>',
        SL:            '<span class="ag-reason-badge sl">SL</span>',
        EOD:           '<span class="ag-reason-badge eod">EOD</span>',
        MANUAL:        '<span class="ag-reason-badge manual">MANUAL</span>',
        RANGE_RECLAIM: '<span class="ag-reason-badge range-reclaim">RANGE RECLAIM</span>',
    };
    return map[reason] || reason || '—';
}

// ── RTP Delete History Record ─────────────────────────────────────────────────

function _rtp3mDeleteAllTrades() {
    if (!confirm('Delete ALL EMA RTP 3m trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/rtp3m/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtp3mHistoryTimer);
            _rtp3mFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _rtp3mDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/rtp3m/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtp3mHistoryTimer);
            _rtp3mFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── RTP Force Exit ────────────────────────────────────────────────────────────

// ── RTP Strategy Logic popup ──────────────────────────────────────────────────
// Info button (right of the "EMA RTP 3m — Railway Track" title) opens a modal that
// explains the strategy logic plus the available Entry and Exit options.

function rtp3mShowLogic() {
    document.getElementById('rtp3mLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'rtp3mLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">EMA RTP 3m — Railway Track</span>
            <span class="sm-modal-subtitle">How it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('rtp3mLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <!-- Timeframe -->
        <div class="rtp-tf"><span class="rtp-tf-lbl">Timeframe</span><span class="rtp-tf-val">3&#8209;min candles</span><span class="rtp-tf-sub">EMA 9 · 20 · 50 · confirm&nbsp;candle&nbsp;2&nbsp;bars</span></div>

        <!-- One-line idea -->
        <p class="rtp-idea">Trade only when EMA&nbsp;20 &amp; 50 run <b>parallel and trending</b> — like two railway tracks. A pullback that touches the track and holds <b>arms</b> the entry; the trade fires only when a candle <b>breaks the signal candle\u2019s high/low within 2 bars</b> (unconfirmed signals are dropped).</p>

        <!-- Entry: BUY vs SELL side by side -->
        <div class="rtp-blk-lbl entry">Entry — price touches EMA, then closes beyond it</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY</div>
                <div class="rtp-duo-row">Low <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close above</b> EMA&nbsp;9 &amp; 20</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL</div>
                <div class="rtp-duo-row">High <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close below</b> EMA&nbsp;9 &amp; 20</div>
            </div>
        </div>
        <div class="rtp-mode-note"><code>RTP(50)</code> mode uses EMA&nbsp;50 alone. Entry fires on the <b>confirmation break</b> (signal candle high/low crossed within 2 bars); the break price is the SL/Target reference.</div>

        <!-- Exit: compact chip grid -->
        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target +75 pts</b><span>book profit</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss −25 pts</b><span>fixed, no trail</span></div></div>
            <div class="rtp-chip rtp-chip-wide"><span class="rtp-chip-ic eod">⏱</span><div><b>EOD 3:28 PM</b><span>force exit · no overnight hold</span></div></div>
        </div>
        <div class="rtp-mode-note">Levels are measured in points from the <b>NIFTY entry spot</b>. Stop Loss is <b>fixed</b> — no trailing.</div>

    </div>

</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.addEventListener('keydown', function esc(ev) {
        if (ev.key === 'Escape') { document.getElementById('rtp3mLogicModal')?.remove(); document.removeEventListener('keydown', esc); }
    });
    document.body.appendChild(modal);
}


function rtp3mExitNow(btn) {
    if (!confirm('Force-close the active RTP trade? A market SELL order will be sent immediately.')) return;
    _setBusy(btn, 'Exiting…');
    fetch('/api/algo/rtp3m/exit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Exit failed: ' + (d.error || 'Unknown error')); _unbusy(btn, 'Force Exit'); return; }
            clearTimeout(_rtp3mStatusTimer);
            clearTimeout(_rtp3mHistoryTimer);
            _rtp3mFetchStatus();
            _rtp3mFetchHistory();
        })
        .catch(e => { alert('Request failed: ' + e); _unbusy(btn, 'Force Exit'); });
}


// ── RTP Delta Strikes ─────────────────────────────────────────────────────────

function rtp3mFetchDeltaStrikes(btn) {
    const panel = document.getElementById('rtp3mStrikesResult');
    btn.disabled = true;
    btn.classList.add('busy');
    btn.textContent = 'Loading…';
    panel.style.display = 'none';

    fetch('/api/algo/rtp3m/delta-strikes')
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




// ══════════════════════════════════════════════════════════════════════════════
// EMA RTP 2m live algo (same RTP logic on 2-minute candles; reuses generic helpers)
// ══════════════════════════════════════════════════════════════════════════════

// ── RTP 2m status ────────────────────────────────────────────────────────────────

function _rtp2mFetchStatus() {
    fetch('/api/algo/rtp2m/status')
        .then(r => r.json())
        .then(data => {
            _rtp2mRenderStatus(data);
            // Detect trade state changes and immediately refresh history so opt
            // entry/exit values reflect the just-completed or just-entered trade
            // rather than waiting up to 30 s for the scheduled history poll.
            const newEntryTime = data.state && data.state.active_trade
                ? data.state.active_trade.entry_time : null;
            const newActive = !!data.active;
            const tradeChanged = newEntryTime !== _rtp2mLastEntryTime ||
                                 newActive !== _rtp2mLastActiveFlag;
            _rtp2mLastEntryTime  = newEntryTime;
            _rtp2mLastActiveFlag = newActive;
            if (tradeChanged) {
                clearTimeout(_rtp2mHistoryTimer);
                _rtp2mFetchHistory();
            }
            clearTimeout(_rtp2mStatusTimer);
            _rtp2mStatusTimer = setTimeout(_rtp2mFetchStatus, newActive ? 5000 : 30000);
        })
        .catch(() => {
            clearTimeout(_rtp2mStatusTimer);
            _rtp2mStatusTimer = setTimeout(_rtp2mFetchStatus, 30000);
        });
}

function _rtp2mRenderStatus(data) {
    const trade  = data.state && data.state.active_trade;
    const live   = data.live || null;
    const active = !!data.active;

    // Badge
    const badge = document.getElementById('rtp2mBadge');
    badge.className = 'ag-badge ' + (active ? 'active' : 'inactive');
    document.getElementById('rtp2mBadgeText').textContent =
        active ? (trade.direction + ' ' + trade.option_type) : 'No Trade';

    // Timestamp
    document.getElementById('rtp2mLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Active trade grid
    const grid = document.getElementById('rtp2mActiveGrid');
    if (!active || !trade) {
        grid.innerHTML = '<div class="ag-empty">No active trade</div>';
        return;
    }

    const spotStr = live ? '₹' + _num(live.spot) : '…';

    const tiles = [
        { label: 'Direction',    value: trade.direction,
          cls: trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg' },
        { label: 'Option',       value: trade.option_type + ' ' + trade.strike },
        { label: 'Entry Spot',   value: '₹' + _num(trade.entry_spot) },
        { label: 'Live Spot',    value: spotStr },
        { label: 'SL Level',     value: '₹' + _num(trade.sl_level),     cls: 'ag-warn' },
        { label: 'Target Level', value: '₹' + _num(trade.target_level), cls: 'ag-pos' },
        ..._algoOptTiles(trade, live),
        { label: 'Entry Time',   value: trade.entry_time ? _fmtTime(trade.entry_time) : '—' },
    ];

    grid.innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    // Toggle Force Exit button based on trade state
    const exitBtn = document.getElementById('rtp2mExitBtn');
    if (exitBtn) exitBtn.disabled = !active;
}

// ── RTP history ───────────────────────────────────────────────────────────────

function _rtp2mFetchHistory() {
    fetch('/api/algo/rtp2m/history')
        .then(r => r.json())
        .then(data => {
            _rtp2mRenderHistory(data.trades || []);
            clearTimeout(_rtp2mHistoryTimer);
            _rtp2mHistoryTimer = setTimeout(_rtp2mFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_rtp2mHistoryTimer);
            _rtp2mHistoryTimer = setTimeout(_rtp2mFetchHistory, 30000);
        });
}

function _rtp2mRenderHistory(trades) {
    const countEl = document.getElementById('rtp2mHistCount');
    const body    = document.getElementById('rtp2mHistBody');

    // Performance dashboard (cards + charts) built from the same JSON
    _rtp2mRenderDashboard(trades);

    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No completed trades</div>';
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
            <th class="ag-hist-th">N Pts</th>
            <th class="ag-hist-th">Opt Pts</th>
            <th class="ag-hist-th">Opt P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
            <th class="ag-hist-th"></th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const dir       = t.direction === 'BUY' ? 'CE BUY' : 'PE BUY';
        const dirCls    = t.direction === 'BUY' ? 'ce' : 'pe';
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const optPts    = t.opt_pnl_pts ?? (t.opt_exit_price != null && t.opt_entry_price != null
                              ? +(t.opt_exit_price - t.opt_entry_price).toFixed(2) : null);
        const oPnlCls   = (optPts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const dateStr   = t.date || (t.entry_time ? t.entry_time.slice(0, 10) : '—');
        const entryTime = t.entry_time ? _fmtTimeOnly(t.entry_time) : '—';
        const exitTime  = t.exit_time  ? _fmtTimeOnly(t.exit_time)  : '—';
        const entryKey  = (t.entry_time || '').replace(/"/g, '&quot;');
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
            <td class="ag-hist-td ${optPts != null ? oPnlCls : ''}" style="font-weight:700">${optPts != null ? (optPts >= 0 ? '+' : '') + optPts.toFixed(2) : '—'}</td>
            <td class="ag-hist-td ${t.opt_pnl_inr != null ? oPnlCls : ''}" style="font-weight:700">${_fmtInr(t.opt_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtp2mFmtReason(t.reason)}</td>
            <td class="ag-hist-td ag-hist-td-del">
                <button class="ag-hist-del-btn" onclick="_rtp2mDeleteTrade('${entryKey}')" title="Delete record">&#128465;</button>
            </td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

// ── RTP live performance dashboard (cards + charts from history JSON) ──────────
let _rtp2mEquityChart    = null;
let _rtp2mBreakdownChart = null;
let _rtp2mDashTrades     = [];
let _rtp2mDashPeriod     = 'monthly';

// Plugin: draw +/- ₹ labels above/below each breakdown bar.
const _rtp2mBarLabelPlugin = {
    id: 'rtp2mBarLabels',
    afterDatasetsDraw(chart, _, opts) {
        const { ctx, data } = chart;
        const m = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = '600 9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.textAlign = 'center';
        m.data.forEach((bar, i) => {
            const v = data.datasets[0].data[i];
            if (v == null) return;
            ctx.fillStyle = v >= 0 ? '#16a34a' : '#dc2626';
            if (v >= 0) { ctx.textBaseline = 'bottom'; ctx.fillText(opts.fmt(v), bar.x, bar.y - 3); }
            else        { ctx.textBaseline = 'top';    ctx.fillText(opts.fmt(v), bar.x, bar.y + 3); }
        });
        ctx.restore();
    }
};

function _rtp2mRenderDashboard(trades) {
    const card = document.getElementById('rtp2mDashCard');
    if (!card) return;

    // Only completed trades carrying a realised ₹ figure feed the dashboard.
    const done = (trades || []).filter(t => t.opt_pnl_inr != null);
    _rtp2mDashTrades = done;

    if (!done.length) { card.style.display = 'none'; return; }
    card.style.display = '';

    // ── Aggregate stats straight from the JSON ─────────────────
    let wins = 0, losses = 0, grossWin = 0, grossLoss = 0;
    let netInr = 0, netPts = 0, winPts = 0, lossPts = 0;
    let cntEod = 0, cntSl = 0, cntTgt = 0;
    done.forEach(t => {
        const inr = _algoNetInr(t);   // ₹ net of round-trip brokerage
        const pts = Number(t.opt_pnl_pts) || 0;   // option points
        netInr += inr; netPts += pts;
        if (inr >= 0) { wins++;   grossWin  += inr;           winPts  += pts; }
        else          { losses++; grossLoss += Math.abs(inr); lossPts += pts; }
        const reason = String(t.reason || '').toUpperCase();
        if      (reason === 'EOD')    cntEod++;
        else if (reason === 'SL')     cntSl++;
        else if (reason === 'TARGET') cntTgt++;
    });
    const total   = done.length;
    const winRate = total ? (wins / total * 100) : 0;
    const pf      = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? Infinity : 0);
    const avgWin  = wins   ? winPts  / wins   : null;
    const avgLoss = losses ? lossPts / losses : null;
    const maxDD   = _rtp2mMaxDrawdown(done);   // ₹, ≤ 0
    const brokTot = total * _ALGO_BROKERAGE_PER_LOT;   // ₹ brokerage deducted

    const inrFmt = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');
    const ptsFmt = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + ' pts';

    const tiles = [
        { label: 'Total Trades',  value: total },
        { label: 'Wins',          value: wins,   cls: 'ag-pos' },
        { label: 'Losses',        value: losses, cls: 'ag-neg' },
        { label: 'Win Rate',      value: winRate.toFixed(1) + '%' },
        { label: 'Net P&L (₹)',   value: inrFmt(netInr), cls: _rtp2mPnlCls(netInr) },
        { label: 'Net Opt Pts',   value: ptsFmt(netPts), cls: _rtp2mPnlCls(netPts) },
        { label: 'Brokerage (₹)', value: '-₹' + Math.round(brokTot).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Profit Factor', value: pf === Infinity ? '∞' : pf.toFixed(2) },
        { label: 'Avg Win (opt)', value: ptsFmt(avgWin),  cls: 'ag-pos' },
        { label: 'Avg Loss (opt)',value: ptsFmt(avgLoss), cls: 'ag-neg' },
        { label: 'Max Drawdown',  value: '-₹' + Math.abs(Math.round(maxDD)).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Target',        value: cntTgt, cls: 'ag-pos' },
        { label: 'SL',            value: cntSl,  cls: 'ag-neg' },
        { label: 'EOD',           value: cntEod, cls: 'ag-warn' },
    ];

    document.getElementById('rtp2mDashStats').innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    const netEl = document.getElementById('rtp2mDashNet');
    if (netEl) {
        netEl.textContent = inrFmt(netInr);
        netEl.style.color = netInr >= 0 ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    _rtp2mRenderEquity(done);
    _rtp2mRenderBreakdown(done, _rtp2mDashPeriod);
}

function _rtp2mMaxDrawdown(trades) {
    const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    let cum = 0, peak = 0, maxDD = 0;
    sorted.forEach(t => {
        cum += _algoNetInr(t);
        if (cum > peak) peak = cum;
        const dd = cum - peak;
        if (dd < maxDD) maxDD = dd;
    });
    return maxDD;   // ≤ 0
}

function _rtp2mRenderEquity(trades) {
    const ctx = document.getElementById('rtp2mEquityChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const sorted  = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    const labels  = ['Start'];
    const dataPts = [0];
    const dates   = [''];
    let cum = 0;
    sorted.forEach((t, i) => {
        cum += _algoNetInr(t);
        labels.push('T' + (i + 1));
        dataPts.push(Math.round(cum));
        dates.push(t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : '');
    });

    const net    = dataPts[dataPts.length - 1];
    const profit = net >= 0;
    const line   = profit ? '#2962ff' : '#ff1744';
    const fill   = profit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

    const meta = document.getElementById('rtp2mEquityMeta');
    if (meta) {
        meta.textContent = (net >= 0 ? '+₹' : '-₹') + Math.abs(net).toLocaleString('en-IN');
        meta.style.color = profit ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    const fmtY = v => {
        const a = Math.abs(v), s = v < 0 ? '-' : '';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
        return s + '₹' + a;
    };

    if (_rtp2mEquityChart) { _rtp2mEquityChart.destroy(); _rtp2mEquityChart = null; }
    _rtp2mEquityChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{
            data: dataPts, borderColor: line, backgroundColor: fill,
            fill: true, tension: 0.25, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2,
        }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    title: items => { const i = items[0].dataIndex; return i === 0 ? 'Start' : `Trade ${i} · ${dates[i]}`; },
                    label: item => '  Cum P&L: ' + (item.raw >= 0 ? '+₹' : '-₹') + Math.abs(item.raw).toLocaleString('en-IN'),
                } },
            },
            scales: {
                x: { ticks: { maxTicksLimit: 15, color: '#999', font: { size: 11 }, autoSkip: true }, grid: { color: 'rgba(128,128,128,0.08)' } },
                y: { ticks: { color: '#999', font: { size: 11 }, callback: fmtY }, grid: { color: 'rgba(128,128,128,0.1)' } },
            }
        }
    });
}

function rtp2mSetPeriod(period) {
    _rtp2mDashPeriod = period;
    document.querySelectorAll('#rtp2mPeriodTabs .rtp-period-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.period === period));
    _rtp2mRenderBreakdown(_rtp2mDashTrades, period);
}

function _rtp2mPeriodKey(d, period) {
    if (period === 'daily')  return d.toISOString().slice(0, 10);
    if (period === 'weekly') {
        const x = new Date(d); x.setHours(0, 0, 0, 0);
        x.setDate(x.getDate() - x.getDay() + 1);   // Monday
        return x.toISOString().slice(0, 10);
    }
    if (period === 'monthly') return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    return `${d.getFullYear()}`;
}

function _rtp2mRenderBreakdown(trades, period) {
    const ctx = document.getElementById('rtp2mBreakdownChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const groups = {};
    (trades || []).forEach(t => {
        const key = _rtp2mPeriodKey(new Date(t.entry_time), period);
        if (!groups[key]) groups[key] = { inr: 0, wins: 0, losses: 0 };
        const inr = _algoNetInr(t);
        groups[key].inr += inr;
        if (inr >= 0) groups[key].wins++; else groups[key].losses++;
    });
    const keys   = Object.keys(groups).sort();
    const labels = keys.map(k => {
        if (period === 'monthly') { const [y, m] = k.split('-'); return new Date(+y, +m - 1).toLocaleString('default', { month: 'short', year: '2-digit' }); }
        if (period === 'weekly')  return 'W ' + k.slice(5);
        if (period === 'daily')   return k.slice(5);
        return k;
    });
    const values = keys.map(k => Math.round(groups[k].inr));
    const meta   = keys.map(k => groups[k]);
    const bg  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)' : 'rgba(239,68,68,.20)');
    const brd = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)' : 'rgba(239,68,68,.90)');

    const fmtBar = v => {
        const a = Math.abs(v), s = v >= 0 ? '+' : '−';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(1) + 'K';
        return s + '₹' + a;
    };

    if (_rtp2mBreakdownChart) { _rtp2mBreakdownChart.destroy(); _rtp2mBreakdownChart = null; }
    _rtp2mBreakdownChart = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        plugins: [_rtp2mBarLabelPlugin],
        data: { labels, datasets: [{ data: values, backgroundColor: bg, borderColor: brd, borderWidth: 1.5, borderRadius: 4, borderSkipped: false }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            layout: { padding: { top: 18, bottom: 4 } },
            plugins: {
                legend: { display: false },
                rtp2mBarLabels: { fmt: fmtBar },
                tooltip: { displayColors: false, padding: 10, callbacks: {
                    title: c => labels[c[0].dataIndex],
                    label: c => {
                        const i = c.dataIndex, v = values[i], g = meta[i];
                        const tr = g.wins + g.losses, wr = tr ? Math.round(g.wins / tr * 100) : 0;
                        return [
                            ' P&L: ' + (v >= 0 ? '+₹' : '-₹') + Math.abs(v).toLocaleString('en-IN'),
                            ` Trades: ${tr}  (${g.wins}W / ${g.losses}L)`,
                            ` Win Rate: ${wr}%`,
                        ];
                    },
                } },
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, color: '#94a3b8' } },
                y: {
                    grid: { color: c => c.tick.value === 0 ? 'rgba(128,128,128,.3)' : 'rgba(128,128,128,.08)' },
                    ticks: { font: { size: 9 }, color: '#94a3b8', callback: v => {
                        if (v === 0) return '0';
                        const a = Math.abs(v), s = v < 0 ? '−' : '';
                        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
                        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
                        return s + '₹' + a;
                    } },
                },
            }
        }
    });
}

// ── RTP helpers ───────────────────────────────────────────────────────────────

function _rtp2mFmtPts(pts) {
    if (pts == null) return '…';
    return (pts >= 0 ? '+' : '') + Number(pts).toFixed(1) + ' pts';
}

function _rtp2mFmtInr(inr) {
    if (inr == null) return '…';
    return (inr >= 0 ? '+₹' : '-₹') + Math.abs(inr).toFixed(0);
}

function _rtp2mPnlCls(val) {
    if (val == null) return '';
    return val >= 0 ? 'ag-pos' : 'ag-neg';
}

function _rtp2mFmtReason(reason) {
    const map = {
        TARGET:        '<span class="ag-reason-badge target">TARGET</span>',
        SL:            '<span class="ag-reason-badge sl">SL</span>',
        EOD:           '<span class="ag-reason-badge eod">EOD</span>',
        MANUAL:        '<span class="ag-reason-badge manual">MANUAL</span>',
        RANGE_RECLAIM: '<span class="ag-reason-badge range-reclaim">RANGE RECLAIM</span>',
    };
    return map[reason] || reason || '—';
}

// ── RTP Delete History Record ─────────────────────────────────────────────────

function _rtp2mDeleteAllTrades() {
    if (!confirm('Delete ALL EMA RTP 2m trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/rtp2m/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtp2mHistoryTimer);
            _rtp2mFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _rtp2mDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/rtp2m/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtp2mHistoryTimer);
            _rtp2mFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── RTP Force Exit ────────────────────────────────────────────────────────────

// ── RTP Strategy Logic popup ──────────────────────────────────────────────────
// Info button (right of the "EMA RTP 2m — Railway Track" title) opens a modal that
// explains the strategy logic plus the available Entry and Exit options.

function rtp2mShowLogic() {
    document.getElementById('rtp2mLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'rtp2mLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">EMA RTP 2m — Railway Track</span>
            <span class="sm-modal-subtitle">How it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('rtp2mLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <!-- Timeframe -->
        <div class="rtp-tf"><span class="rtp-tf-lbl">Timeframe</span><span class="rtp-tf-val">2&#8209;min candles</span><span class="rtp-tf-sub">EMA 9 · 20 · 50 · confirm&nbsp;candle&nbsp;2&nbsp;bars</span></div>

        <!-- One-line idea -->
        <p class="rtp-idea">Trade only when EMA&nbsp;20 &amp; 50 run <b>parallel and trending</b> — like two railway tracks. A pullback that touches the track and holds <b>arms</b> the entry; the trade fires only when a candle <b>breaks the signal candle\u2019s high/low within 2 bars</b> (unconfirmed signals are dropped).</p>

        <!-- Entry: BUY vs SELL side by side -->
        <div class="rtp-blk-lbl entry">Entry — price touches EMA, then closes beyond it</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY</div>
                <div class="rtp-duo-row">Low <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close above</b> EMA&nbsp;9 &amp; 20</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL</div>
                <div class="rtp-duo-row">High <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close below</b> EMA&nbsp;9 &amp; 20</div>
            </div>
        </div>
        <div class="rtp-mode-note"><code>RTP(50)</code> mode uses EMA&nbsp;50 alone. Entry fires on the <b>confirmation break</b> (signal candle high/low crossed within 2 bars); the break price is the SL/Target reference.</div>

        <!-- Exit: compact chip grid -->
        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target +90 pts</b><span>book profit</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss −30 pts</b><span>fixed, no trail</span></div></div>
            <div class="rtp-chip rtp-chip-wide"><span class="rtp-chip-ic eod">⏱</span><div><b>EOD 3:28 PM</b><span>force exit · no overnight hold</span></div></div>
        </div>
        <div class="rtp-mode-note">Levels are measured in points from the <b>NIFTY entry spot</b>. Stop Loss is <b>fixed</b> — no trailing.</div>

    </div>

</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.addEventListener('keydown', function esc(ev) {
        if (ev.key === 'Escape') { document.getElementById('rtp2mLogicModal')?.remove(); document.removeEventListener('keydown', esc); }
    });
    document.body.appendChild(modal);
}


function rtp2mExitNow(btn) {
    if (!confirm('Force-close the active RTP trade? A market SELL order will be sent immediately.')) return;
    _setBusy(btn, 'Exiting…');
    fetch('/api/algo/rtp2m/exit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Exit failed: ' + (d.error || 'Unknown error')); _unbusy(btn, 'Force Exit'); return; }
            clearTimeout(_rtp2mStatusTimer);
            clearTimeout(_rtp2mHistoryTimer);
            _rtp2mFetchStatus();
            _rtp2mFetchHistory();
        })
        .catch(e => { alert('Request failed: ' + e); _unbusy(btn, 'Force Exit'); });
}


// ── RTP Delta Strikes ─────────────────────────────────────────────────────────

function rtp2mFetchDeltaStrikes(btn) {
    const panel = document.getElementById('rtp2mStrikesResult');
    btn.disabled = true;
    btn.classList.add('busy');
    btn.textContent = 'Loading…';
    panel.style.display = 'none';

    fetch('/api/algo/rtp2m/delta-strikes')
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




// ══════════════════════════════════════════════════════════════════════════════
// EMA RTP 5m live algo (same RTP logic on 5-minute candles; reuses generic helpers)
// ══════════════════════════════════════════════════════════════════════════════

// ── RTP 5m status ────────────────────────────────────────────────────────────────

function _rtp5mFetchStatus() {
    fetch('/api/algo/rtp5m/status')
        .then(r => r.json())
        .then(data => {
            _rtp5mRenderStatus(data);
            // Detect trade state changes and immediately refresh history so opt
            // entry/exit values reflect the just-completed or just-entered trade
            // rather than waiting up to 30 s for the scheduled history poll.
            const newEntryTime = data.state && data.state.active_trade
                ? data.state.active_trade.entry_time : null;
            const newActive = !!data.active;
            const tradeChanged = newEntryTime !== _rtp5mLastEntryTime ||
                                 newActive !== _rtp5mLastActiveFlag;
            _rtp5mLastEntryTime  = newEntryTime;
            _rtp5mLastActiveFlag = newActive;
            if (tradeChanged) {
                clearTimeout(_rtp5mHistoryTimer);
                _rtp5mFetchHistory();
            }
            clearTimeout(_rtp5mStatusTimer);
            _rtp5mStatusTimer = setTimeout(_rtp5mFetchStatus, newActive ? 5000 : 30000);
        })
        .catch(() => {
            clearTimeout(_rtp5mStatusTimer);
            _rtp5mStatusTimer = setTimeout(_rtp5mFetchStatus, 30000);
        });
}

function _rtp5mRenderStatus(data) {
    const trade  = data.state && data.state.active_trade;
    const live   = data.live || null;
    const active = !!data.active;

    // Badge
    const badge = document.getElementById('rtp5mBadge');
    badge.className = 'ag-badge ' + (active ? 'active' : 'inactive');
    document.getElementById('rtp5mBadgeText').textContent =
        active ? (trade.direction + ' ' + trade.option_type) : 'No Trade';

    // Timestamp
    document.getElementById('rtp5mLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Active trade grid
    const grid = document.getElementById('rtp5mActiveGrid');
    if (!active || !trade) {
        grid.innerHTML = '<div class="ag-empty">No active trade</div>';
        return;
    }

    const spotStr = live ? '₹' + _num(live.spot) : '…';

    const tiles = [
        { label: 'Direction',    value: trade.direction,
          cls: trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg' },
        { label: 'Option',       value: trade.option_type + ' ' + trade.strike },
        { label: 'Entry Spot',   value: '₹' + _num(trade.entry_spot) },
        { label: 'Live Spot',    value: spotStr },
        { label: 'SL Level',     value: '₹' + _num(trade.sl_level),     cls: 'ag-warn' },
        { label: 'Target Level', value: '₹' + _num(trade.target_level), cls: 'ag-pos' },
        ..._algoOptTiles(trade, live),
        { label: 'Entry Time',   value: trade.entry_time ? _fmtTime(trade.entry_time) : '—' },
    ];

    grid.innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    // Toggle Force Exit button based on trade state
    const exitBtn = document.getElementById('rtp5mExitBtn');
    if (exitBtn) exitBtn.disabled = !active;
}

// ── RTP history ───────────────────────────────────────────────────────────────

function _rtp5mFetchHistory() {
    fetch('/api/algo/rtp5m/history')
        .then(r => r.json())
        .then(data => {
            _rtp5mRenderHistory(data.trades || []);
            clearTimeout(_rtp5mHistoryTimer);
            _rtp5mHistoryTimer = setTimeout(_rtp5mFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_rtp5mHistoryTimer);
            _rtp5mHistoryTimer = setTimeout(_rtp5mFetchHistory, 30000);
        });
}

function _rtp5mRenderHistory(trades) {
    const countEl = document.getElementById('rtp5mHistCount');
    const body    = document.getElementById('rtp5mHistBody');

    // Performance dashboard (cards + charts) built from the same JSON
    _rtp5mRenderDashboard(trades);

    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No completed trades</div>';
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
            <th class="ag-hist-th">N Pts</th>
            <th class="ag-hist-th">Opt Pts</th>
            <th class="ag-hist-th">Opt P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
            <th class="ag-hist-th"></th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const dir       = t.direction === 'BUY' ? 'CE BUY' : 'PE BUY';
        const dirCls    = t.direction === 'BUY' ? 'ce' : 'pe';
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const optPts    = t.opt_pnl_pts ?? (t.opt_exit_price != null && t.opt_entry_price != null
                              ? +(t.opt_exit_price - t.opt_entry_price).toFixed(2) : null);
        const oPnlCls   = (optPts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const dateStr   = t.date || (t.entry_time ? t.entry_time.slice(0, 10) : '—');
        const entryTime = t.entry_time ? _fmtTimeOnly(t.entry_time) : '—';
        const exitTime  = t.exit_time  ? _fmtTimeOnly(t.exit_time)  : '—';
        const entryKey  = (t.entry_time || '').replace(/"/g, '&quot;');
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
            <td class="ag-hist-td ${optPts != null ? oPnlCls : ''}" style="font-weight:700">${optPts != null ? (optPts >= 0 ? '+' : '') + optPts.toFixed(2) : '—'}</td>
            <td class="ag-hist-td ${t.opt_pnl_inr != null ? oPnlCls : ''}" style="font-weight:700">${_fmtInr(t.opt_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtp5mFmtReason(t.reason)}</td>
            <td class="ag-hist-td ag-hist-td-del">
                <button class="ag-hist-del-btn" onclick="_rtp5mDeleteTrade('${entryKey}')" title="Delete record">&#128465;</button>
            </td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

// ── RTP live performance dashboard (cards + charts from history JSON) ──────────
let _rtp5mEquityChart    = null;
let _rtp5mBreakdownChart = null;
let _rtp5mDashTrades     = [];
let _rtp5mDashPeriod     = 'monthly';

// Plugin: draw +/- ₹ labels above/below each breakdown bar.
const _rtp5mBarLabelPlugin = {
    id: 'rtp5mBarLabels',
    afterDatasetsDraw(chart, _, opts) {
        const { ctx, data } = chart;
        const m = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = '600 9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.textAlign = 'center';
        m.data.forEach((bar, i) => {
            const v = data.datasets[0].data[i];
            if (v == null) return;
            ctx.fillStyle = v >= 0 ? '#16a34a' : '#dc2626';
            if (v >= 0) { ctx.textBaseline = 'bottom'; ctx.fillText(opts.fmt(v), bar.x, bar.y - 3); }
            else        { ctx.textBaseline = 'top';    ctx.fillText(opts.fmt(v), bar.x, bar.y + 3); }
        });
        ctx.restore();
    }
};

function _rtp5mRenderDashboard(trades) {
    const card = document.getElementById('rtp5mDashCard');
    if (!card) return;

    // Only completed trades carrying a realised ₹ figure feed the dashboard.
    const done = (trades || []).filter(t => t.opt_pnl_inr != null);
    _rtp5mDashTrades = done;

    if (!done.length) { card.style.display = 'none'; return; }
    card.style.display = '';

    // ── Aggregate stats straight from the JSON ─────────────────
    let wins = 0, losses = 0, grossWin = 0, grossLoss = 0;
    let netInr = 0, netPts = 0, winPts = 0, lossPts = 0;
    let cntEod = 0, cntSl = 0, cntTgt = 0;
    done.forEach(t => {
        const inr = _algoNetInr(t);   // ₹ net of round-trip brokerage
        const pts = Number(t.opt_pnl_pts) || 0;   // option points
        netInr += inr; netPts += pts;
        if (inr >= 0) { wins++;   grossWin  += inr;           winPts  += pts; }
        else          { losses++; grossLoss += Math.abs(inr); lossPts += pts; }
        const reason = String(t.reason || '').toUpperCase();
        if      (reason === 'EOD')    cntEod++;
        else if (reason === 'SL')     cntSl++;
        else if (reason === 'TARGET') cntTgt++;
    });
    const total   = done.length;
    const winRate = total ? (wins / total * 100) : 0;
    const pf      = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? Infinity : 0);
    const avgWin  = wins   ? winPts  / wins   : null;
    const avgLoss = losses ? lossPts / losses : null;
    const maxDD   = _rtp5mMaxDrawdown(done);   // ₹, ≤ 0
    const brokTot = total * _ALGO_BROKERAGE_PER_LOT;   // ₹ brokerage deducted

    const inrFmt = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');
    const ptsFmt = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + ' pts';

    const tiles = [
        { label: 'Total Trades',  value: total },
        { label: 'Wins',          value: wins,   cls: 'ag-pos' },
        { label: 'Losses',        value: losses, cls: 'ag-neg' },
        { label: 'Win Rate',      value: winRate.toFixed(1) + '%' },
        { label: 'Net P&L (₹)',   value: inrFmt(netInr), cls: _rtp5mPnlCls(netInr) },
        { label: 'Net Opt Pts',   value: ptsFmt(netPts), cls: _rtp5mPnlCls(netPts) },
        { label: 'Brokerage (₹)', value: '-₹' + Math.round(brokTot).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Profit Factor', value: pf === Infinity ? '∞' : pf.toFixed(2) },
        { label: 'Avg Win (opt)', value: ptsFmt(avgWin),  cls: 'ag-pos' },
        { label: 'Avg Loss (opt)',value: ptsFmt(avgLoss), cls: 'ag-neg' },
        { label: 'Max Drawdown',  value: '-₹' + Math.abs(Math.round(maxDD)).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Target',        value: cntTgt, cls: 'ag-pos' },
        { label: 'SL',            value: cntSl,  cls: 'ag-neg' },
        { label: 'EOD',           value: cntEod, cls: 'ag-warn' },
    ];

    document.getElementById('rtp5mDashStats').innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    const netEl = document.getElementById('rtp5mDashNet');
    if (netEl) {
        netEl.textContent = inrFmt(netInr);
        netEl.style.color = netInr >= 0 ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    _rtp5mRenderEquity(done);
    _rtp5mRenderBreakdown(done, _rtp5mDashPeriod);
}

function _rtp5mMaxDrawdown(trades) {
    const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    let cum = 0, peak = 0, maxDD = 0;
    sorted.forEach(t => {
        cum += _algoNetInr(t);
        if (cum > peak) peak = cum;
        const dd = cum - peak;
        if (dd < maxDD) maxDD = dd;
    });
    return maxDD;   // ≤ 0
}

function _rtp5mRenderEquity(trades) {
    const ctx = document.getElementById('rtp5mEquityChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const sorted  = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    const labels  = ['Start'];
    const dataPts = [0];
    const dates   = [''];
    let cum = 0;
    sorted.forEach((t, i) => {
        cum += _algoNetInr(t);
        labels.push('T' + (i + 1));
        dataPts.push(Math.round(cum));
        dates.push(t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : '');
    });

    const net    = dataPts[dataPts.length - 1];
    const profit = net >= 0;
    const line   = profit ? '#2962ff' : '#ff1744';
    const fill   = profit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

    const meta = document.getElementById('rtp5mEquityMeta');
    if (meta) {
        meta.textContent = (net >= 0 ? '+₹' : '-₹') + Math.abs(net).toLocaleString('en-IN');
        meta.style.color = profit ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    const fmtY = v => {
        const a = Math.abs(v), s = v < 0 ? '-' : '';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
        return s + '₹' + a;
    };

    if (_rtp5mEquityChart) { _rtp5mEquityChart.destroy(); _rtp5mEquityChart = null; }
    _rtp5mEquityChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{
            data: dataPts, borderColor: line, backgroundColor: fill,
            fill: true, tension: 0.25, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2,
        }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    title: items => { const i = items[0].dataIndex; return i === 0 ? 'Start' : `Trade ${i} · ${dates[i]}`; },
                    label: item => '  Cum P&L: ' + (item.raw >= 0 ? '+₹' : '-₹') + Math.abs(item.raw).toLocaleString('en-IN'),
                } },
            },
            scales: {
                x: { ticks: { maxTicksLimit: 15, color: '#999', font: { size: 11 }, autoSkip: true }, grid: { color: 'rgba(128,128,128,0.08)' } },
                y: { ticks: { color: '#999', font: { size: 11 }, callback: fmtY }, grid: { color: 'rgba(128,128,128,0.1)' } },
            }
        }
    });
}

function rtp5mSetPeriod(period) {
    _rtp5mDashPeriod = period;
    document.querySelectorAll('#rtp5mPeriodTabs .rtp-period-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.period === period));
    _rtp5mRenderBreakdown(_rtp5mDashTrades, period);
}

function _rtp5mPeriodKey(d, period) {
    if (period === 'daily')  return d.toISOString().slice(0, 10);
    if (period === 'weekly') {
        const x = new Date(d); x.setHours(0, 0, 0, 0);
        x.setDate(x.getDate() - x.getDay() + 1);   // Monday
        return x.toISOString().slice(0, 10);
    }
    if (period === 'monthly') return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    return `${d.getFullYear()}`;
}

function _rtp5mRenderBreakdown(trades, period) {
    const ctx = document.getElementById('rtp5mBreakdownChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const groups = {};
    (trades || []).forEach(t => {
        const key = _rtp5mPeriodKey(new Date(t.entry_time), period);
        if (!groups[key]) groups[key] = { inr: 0, wins: 0, losses: 0 };
        const inr = _algoNetInr(t);
        groups[key].inr += inr;
        if (inr >= 0) groups[key].wins++; else groups[key].losses++;
    });
    const keys   = Object.keys(groups).sort();
    const labels = keys.map(k => {
        if (period === 'monthly') { const [y, m] = k.split('-'); return new Date(+y, +m - 1).toLocaleString('default', { month: 'short', year: '2-digit' }); }
        if (period === 'weekly')  return 'W ' + k.slice(5);
        if (period === 'daily')   return k.slice(5);
        return k;
    });
    const values = keys.map(k => Math.round(groups[k].inr));
    const meta   = keys.map(k => groups[k]);
    const bg  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)' : 'rgba(239,68,68,.20)');
    const brd = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)' : 'rgba(239,68,68,.90)');

    const fmtBar = v => {
        const a = Math.abs(v), s = v >= 0 ? '+' : '−';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(1) + 'K';
        return s + '₹' + a;
    };

    if (_rtp5mBreakdownChart) { _rtp5mBreakdownChart.destroy(); _rtp5mBreakdownChart = null; }
    _rtp5mBreakdownChart = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        plugins: [_rtp5mBarLabelPlugin],
        data: { labels, datasets: [{ data: values, backgroundColor: bg, borderColor: brd, borderWidth: 1.5, borderRadius: 4, borderSkipped: false }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            layout: { padding: { top: 18, bottom: 4 } },
            plugins: {
                legend: { display: false },
                rtp5mBarLabels: { fmt: fmtBar },
                tooltip: { displayColors: false, padding: 10, callbacks: {
                    title: c => labels[c[0].dataIndex],
                    label: c => {
                        const i = c.dataIndex, v = values[i], g = meta[i];
                        const tr = g.wins + g.losses, wr = tr ? Math.round(g.wins / tr * 100) : 0;
                        return [
                            ' P&L: ' + (v >= 0 ? '+₹' : '-₹') + Math.abs(v).toLocaleString('en-IN'),
                            ` Trades: ${tr}  (${g.wins}W / ${g.losses}L)`,
                            ` Win Rate: ${wr}%`,
                        ];
                    },
                } },
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, color: '#94a3b8' } },
                y: {
                    grid: { color: c => c.tick.value === 0 ? 'rgba(128,128,128,.3)' : 'rgba(128,128,128,.08)' },
                    ticks: { font: { size: 9 }, color: '#94a3b8', callback: v => {
                        if (v === 0) return '0';
                        const a = Math.abs(v), s = v < 0 ? '−' : '';
                        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
                        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
                        return s + '₹' + a;
                    } },
                },
            }
        }
    });
}

// ── RTP helpers ───────────────────────────────────────────────────────────────

function _rtp5mFmtPts(pts) {
    if (pts == null) return '…';
    return (pts >= 0 ? '+' : '') + Number(pts).toFixed(1) + ' pts';
}

function _rtp5mFmtInr(inr) {
    if (inr == null) return '…';
    return (inr >= 0 ? '+₹' : '-₹') + Math.abs(inr).toFixed(0);
}

function _rtp5mPnlCls(val) {
    if (val == null) return '';
    return val >= 0 ? 'ag-pos' : 'ag-neg';
}

function _rtp5mFmtReason(reason) {
    const map = {
        TARGET:        '<span class="ag-reason-badge target">TARGET</span>',
        SL:            '<span class="ag-reason-badge sl">SL</span>',
        EOD:           '<span class="ag-reason-badge eod">EOD</span>',
        MANUAL:        '<span class="ag-reason-badge manual">MANUAL</span>',
        RANGE_RECLAIM: '<span class="ag-reason-badge range-reclaim">RANGE RECLAIM</span>',
    };
    return map[reason] || reason || '—';
}

// ── RTP Delete History Record ─────────────────────────────────────────────────

function _rtp5mDeleteAllTrades() {
    if (!confirm('Delete ALL EMA RTP 5m trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/rtp5m/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtp5mHistoryTimer);
            _rtp5mFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _rtp5mDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/rtp5m/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_rtp5mHistoryTimer);
            _rtp5mFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── RTP Force Exit ────────────────────────────────────────────────────────────

// ── RTP Strategy Logic popup ──────────────────────────────────────────────────
// Info button (right of the "EMA RTP 5m — Railway Track" title) opens a modal that
// explains the strategy logic plus the available Entry and Exit options.

function rtp5mShowLogic() {
    document.getElementById('rtp5mLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'rtp5mLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">EMA RTP 5m — Railway Track</span>
            <span class="sm-modal-subtitle">How it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('rtp5mLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <!-- Timeframe -->
        <div class="rtp-tf"><span class="rtp-tf-lbl">Timeframe</span><span class="rtp-tf-val">5&#8209;min candles</span><span class="rtp-tf-sub">EMA 9 · 20 · 50 · confirm&nbsp;candle&nbsp;2&nbsp;bars · rail&nbsp;gap&nbsp;≥&nbsp;0.2×ATR</span></div>

        <!-- One-line idea -->
        <p class="rtp-idea">Trade only when EMA&nbsp;20 &amp; 50 run <b>parallel, trending and separated</b> — like two railway tracks — with the <b>rail gap ≥ 0.2×ATR</b> rejecting flat, braided-EMA chop. A pullback that touches the track and holds <b>arms</b> the entry; the trade fires only when a candle <b>breaks the signal candle\u2019s high/low within 2 bars</b> (unconfirmed signals are dropped).</p>

        <!-- Entry: BUY vs SELL side by side -->
        <div class="rtp-blk-lbl entry">Entry — price touches EMA, then closes beyond it</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY</div>
                <div class="rtp-duo-row">Low <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close above</b> EMA&nbsp;9 &amp; 20</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL</div>
                <div class="rtp-duo-row">High <b>touches</b> EMA&nbsp;20</div>
                <div class="rtp-duo-row"><b>Close below</b> EMA&nbsp;9 &amp; 20</div>
            </div>
        </div>
        <div class="rtp-mode-note"><code>RTP(50)</code> mode uses EMA&nbsp;50 alone. Entry fires on the <b>confirmation break</b> (signal candle high/low crossed within 2 bars); the break price is the SL/Target reference.</div>

        <!-- Exit: compact chip grid -->
        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target +90 pts</b><span>book profit</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss −30 pts</b><span>fixed, no trail</span></div></div>
            <div class="rtp-chip rtp-chip-wide"><span class="rtp-chip-ic eod">⏱</span><div><b>EOD 3:28 PM</b><span>force exit · no overnight hold</span></div></div>
        </div>
        <div class="rtp-mode-note">Levels are measured in points from the <b>NIFTY entry spot</b>. Stop Loss is <b>fixed</b> — no trailing.</div>

    </div>

</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.addEventListener('keydown', function esc(ev) {
        if (ev.key === 'Escape') { document.getElementById('rtp5mLogicModal')?.remove(); document.removeEventListener('keydown', esc); }
    });
    document.body.appendChild(modal);
}


function rtp5mExitNow(btn) {
    if (!confirm('Force-close the active RTP trade? A market SELL order will be sent immediately.')) return;
    _setBusy(btn, 'Exiting…');
    fetch('/api/algo/rtp5m/exit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Exit failed: ' + (d.error || 'Unknown error')); _unbusy(btn, 'Force Exit'); return; }
            clearTimeout(_rtp5mStatusTimer);
            clearTimeout(_rtp5mHistoryTimer);
            _rtp5mFetchStatus();
            _rtp5mFetchHistory();
        })
        .catch(e => { alert('Request failed: ' + e); _unbusy(btn, 'Force Exit'); });
}


// ── RTP Delta Strikes ─────────────────────────────────────────────────────────

function rtp5mFetchDeltaStrikes(btn) {
    const panel = document.getElementById('rtp5mStrikesResult');
    btn.disabled = true;
    btn.classList.add('busy');
    btn.textContent = 'Loading…';
    panel.style.display = 'none';

    fetch('/api/algo/rtp5m/delta-strikes')
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



// ══════════════════════════════════════════════════════════════════════════════
// 2nd 30-Sec Candle live algo (mirrors the RTP tab; reuses RTP's generic helpers)
// ══════════════════════════════════════════════════════════════════════════════

let _scEquityChart    = null;
let _scBreakdownChart = null;
let _scDashTrades     = [];
let _scDashPeriod     = 'monthly';

// ── SC settings ────────────────────────────────────────────────────────────────

function scLoadSettings() {
    fetch('/api/algo/sc/settings')
        .then(r => r.json())
        .then(d => {
            if (!d.success || !d.params) return;
            const p = d.params;
            const ci = document.getElementById('scAlgoCandleIndex');
            const rr = document.getElementById('scAlgoRrRatio');
            const ct = document.getElementById('scAlgoCutoff');
            const dr = document.getElementById('scAlgoDirection');
            if (ci) ci.value = p.candle_index;
            if (rr) rr.value = String(p.rr_ratio);
            if (ct) ct.value = String(p.exit_hour).padStart(2, '0') + ':' + String(p.exit_minute).padStart(2, '0');
            if (dr) dr.value = p.enable_long && p.enable_short ? 'both' : (p.enable_long ? 'long' : 'short');
        })
        .catch(() => {});
}

function scSaveSettings(btn) {
    const ci  = parseInt(document.getElementById('scAlgoCandleIndex')?.value || 2);
    const rr  = parseFloat(document.getElementById('scAlgoRrRatio')?.value || 3);
    const ct  = (document.getElementById('scAlgoCutoff')?.value || '15:25').split(':');
    const dir = document.getElementById('scAlgoDirection')?.value || 'both';
    const payload = {
        candle_index: ci,
        rr_ratio:     rr,
        exit_hour:    parseInt(ct[0] || 15),
        exit_minute:  parseInt(ct[1] || 25),
        enable_long:  dir !== 'short',
        enable_short: dir !== 'long',
    };
    _setBusy(btn, 'Saving…');
    fetch('/api/algo/sc/settings', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
    })
        .then(r => r.json())
        .then(d => {
            _unbusy(btn, 'Save');
            if (!d.success) { alert('Save failed: ' + (d.error || 'Unknown error')); return; }
            const msg = document.getElementById('scSettingsMsg');
            if (msg) { msg.textContent = 'Saved ✓'; setTimeout(() => { msg.textContent = ''; }, 2500); }
        })
        .catch(e => { _unbusy(btn, 'Save'); alert('Request failed: ' + e); });
}

// ── SC status ───────────────────────────────────────────────────────────────

function _scFetchStatus() {
    fetch('/api/algo/sc/status')
        .then(r => r.json())
        .then(data => {
            _scRenderStatus(data);
            const newEntryTime = data.state && data.state.active_trade
                ? data.state.active_trade.entry_time : null;
            const newActive = !!data.active;
            const tradeChanged = newEntryTime !== _scLastEntryTime ||
                                 newActive !== _scLastActiveFlag;
            _scLastEntryTime  = newEntryTime;
            _scLastActiveFlag = newActive;
            if (tradeChanged) {
                clearTimeout(_scHistoryTimer);
                _scFetchHistory();
            }
            clearTimeout(_scStatusTimer);
            _scStatusTimer = setTimeout(_scFetchStatus, newActive ? 5000 : 30000);
        })
        .catch(() => {
            clearTimeout(_scStatusTimer);
            _scStatusTimer = setTimeout(_scFetchStatus, 30000);
        });
}

function _scRenderStatus(data) {
    const trade  = data.state && data.state.active_trade;
    const live   = data.live || null;
    const active = !!data.active;

    const badge = document.getElementById('scBadge');
    badge.className = 'ag-badge ' + (active ? 'active' : 'inactive');
    document.getElementById('scBadgeText').textContent =
        active ? (trade.direction + ' ' + trade.option_type) : 'No Trade';

    document.getElementById('scLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const grid = document.getElementById('scActiveGrid');
    if (!active || !trade) {
        grid.innerHTML = '<div class="ag-empty">No active trade</div>';
        const exitBtn0 = document.getElementById('scExitBtn');
        if (exitBtn0) exitBtn0.disabled = true;
        return;
    }

    const spotStr = live ? '₹' + _num(live.spot) : '…';

    const tiles = [
        { label: 'Direction',    value: trade.direction,
          cls: trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg' },
        { label: 'Option',       value: trade.option_type + ' ' + trade.strike },
        { label: 'Entry Spot',   value: '₹' + _num(trade.entry_spot) },
        { label: 'Live Spot',    value: spotStr },
        { label: 'SL Level',     value: '₹' + _num(trade.sl_level),     cls: 'ag-warn' },
        { label: 'Target Level', value: '₹' + _num(trade.target_level), cls: 'ag-pos' },
        ..._algoOptTiles(trade, live),
        { label: 'Entry Time',   value: trade.entry_time ? _fmtTime(trade.entry_time) : '—' },
    ];

    grid.innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    const exitBtn = document.getElementById('scExitBtn');
    if (exitBtn) exitBtn.disabled = !active;
}

// ── SC history ──────────────────────────────────────────────────────────────

function _scFetchHistory() {
    fetch('/api/algo/sc/history')
        .then(r => r.json())
        .then(data => {
            _scRenderHistory(data.trades || []);
            clearTimeout(_scHistoryTimer);
            _scHistoryTimer = setTimeout(_scFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_scHistoryTimer);
            _scHistoryTimer = setTimeout(_scFetchHistory, 30000);
        });
}

function _scRenderHistory(trades) {
    const countEl = document.getElementById('scHistCount');
    const body    = document.getElementById('scHistBody');

    _scRenderDashboard(trades);

    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No completed trades</div>';
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
            <th class="ag-hist-th">N Pts</th>
            <th class="ag-hist-th">Opt Pts</th>
            <th class="ag-hist-th">Opt P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
            <th class="ag-hist-th"></th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const optPts    = t.opt_pnl_pts ?? (t.opt_exit_price != null && t.opt_entry_price != null
                              ? +(t.opt_exit_price - t.opt_entry_price).toFixed(2) : null);
        const oPnlCls   = (optPts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const dateStr   = t.date || (t.entry_time ? t.entry_time.slice(0, 10) : '—');
        const entryTime = t.entry_time ? _fmtTimeOnly(t.entry_time) : '—';
        const exitTime  = t.exit_time  ? _fmtTimeOnly(t.exit_time)  : '—';
        const entryKey  = (t.entry_time || '').replace(/"/g, '&quot;');
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
            <td class="ag-hist-td ${optPts != null ? oPnlCls : ''}" style="font-weight:700">${optPts != null ? (optPts >= 0 ? '+' : '') + optPts.toFixed(2) : '—'}</td>
            <td class="ag-hist-td ${t.opt_pnl_inr != null ? oPnlCls : ''}" style="font-weight:700">${_fmtInr(t.opt_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtpFmtReason(t.reason)}</td>
            <td class="ag-hist-td ag-hist-td-del">
                <button class="ag-hist-del-btn" onclick="_scDeleteTrade('${entryKey}')" title="Delete record">&#128465;</button>
            </td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

// ── SC dashboard (cards + charts) ─────────────────────────────────────────────

function _scRenderDashboard(trades) {
    const card = document.getElementById('scDashCard');
    if (!card) return;

    const done = (trades || []).filter(t => t.opt_pnl_inr != null);
    _scDashTrades = done;

    if (!done.length) { card.style.display = 'none'; return; }
    card.style.display = '';

    let wins = 0, losses = 0, grossWin = 0, grossLoss = 0;
    let netInr = 0, netPts = 0, winPts = 0, lossPts = 0;
    let cntEod = 0, cntSl = 0, cntTgt = 0;
    done.forEach(t => {
        const inr = _algoNetInr(t);   // ₹ net of round-trip brokerage
        const pts = Number(t.opt_pnl_pts) || 0;
        netInr += inr; netPts += pts;
        if (inr >= 0) { wins++;   grossWin  += inr;           winPts  += pts; }
        else          { losses++; grossLoss += Math.abs(inr); lossPts += pts; }
        const reason = String(t.reason || '').toUpperCase();
        if      (reason === 'EOD')    cntEod++;
        else if (reason === 'SL')     cntSl++;
        else if (reason === 'TARGET') cntTgt++;
    });
    const total   = done.length;
    const winRate = total ? (wins / total * 100) : 0;
    const pf      = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? Infinity : 0);
    const avgWin  = wins   ? winPts  / wins   : null;
    const avgLoss = losses ? lossPts / losses : null;
    const maxDD   = _rtpMaxDrawdown(done);
    const brokTot = total * _ALGO_BROKERAGE_PER_LOT;   // ₹ brokerage deducted

    const inrFmt = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');
    const ptsFmt = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + ' pts';

    const tiles = [
        { label: 'Total Trades',  value: total },
        { label: 'Wins',          value: wins,   cls: 'ag-pos' },
        { label: 'Losses',        value: losses, cls: 'ag-neg' },
        { label: 'Win Rate',      value: winRate.toFixed(1) + '%' },
        { label: 'Net P&L (₹)',   value: inrFmt(netInr), cls: _rtpPnlCls(netInr) },
        { label: 'Net Opt Pts',   value: ptsFmt(netPts), cls: _rtpPnlCls(netPts) },
        { label: 'Brokerage (₹)', value: '-₹' + Math.round(brokTot).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Profit Factor', value: pf === Infinity ? '∞' : pf.toFixed(2) },
        { label: 'Avg Win (opt)', value: ptsFmt(avgWin),  cls: 'ag-pos' },
        { label: 'Avg Loss (opt)',value: ptsFmt(avgLoss), cls: 'ag-neg' },
        { label: 'Max Drawdown',  value: '-₹' + Math.abs(Math.round(maxDD)).toLocaleString('en-IN'), cls: 'ag-neg' },
        { label: 'Target',        value: cntTgt, cls: 'ag-pos' },
        { label: 'SL',            value: cntSl,  cls: 'ag-neg' },
        { label: 'EOD',           value: cntEod, cls: 'ag-warn' },
    ];

    document.getElementById('scDashStats').innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');

    const netEl = document.getElementById('scDashNet');
    if (netEl) {
        netEl.textContent = inrFmt(netInr);
        netEl.style.color = netInr >= 0 ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    _scRenderEquity(done);
    _scRenderBreakdown(done, _scDashPeriod);
}

function _scRenderEquity(trades) {
    const ctx = document.getElementById('scEquityChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const sorted  = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    const labels  = ['Start'];
    const dataPts = [0];
    const dates   = [''];
    let cum = 0;
    sorted.forEach((t, i) => {
        cum += _algoNetInr(t);
        labels.push('T' + (i + 1));
        dataPts.push(Math.round(cum));
        dates.push(t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : '');
    });

    const net    = dataPts[dataPts.length - 1];
    const profit = net >= 0;
    const line   = profit ? '#2962ff' : '#ff1744';
    const fill   = profit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

    const meta = document.getElementById('scEquityMeta');
    if (meta) {
        meta.textContent = (net >= 0 ? '+₹' : '-₹') + Math.abs(net).toLocaleString('en-IN');
        meta.style.color = profit ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }

    const fmtY = v => {
        const a = Math.abs(v), s = v < 0 ? '-' : '';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
        return s + '₹' + a;
    };

    if (_scEquityChart) { _scEquityChart.destroy(); _scEquityChart = null; }
    _scEquityChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{
            data: dataPts, borderColor: line, backgroundColor: fill,
            fill: true, tension: 0.25, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2,
        }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    title: items => { const i = items[0].dataIndex; return i === 0 ? 'Start' : `Trade ${i} · ${dates[i]}`; },
                    label: item => '  Cum P&L: ' + (item.raw >= 0 ? '+₹' : '-₹') + Math.abs(item.raw).toLocaleString('en-IN'),
                } },
            },
            scales: {
                x: { ticks: { maxTicksLimit: 15, color: '#999', font: { size: 11 }, autoSkip: true }, grid: { color: 'rgba(128,128,128,0.08)' } },
                y: { ticks: { color: '#999', font: { size: 11 }, callback: fmtY }, grid: { color: 'rgba(128,128,128,0.1)' } },
            }
        }
    });
}

function scSetPeriod(period) {
    _scDashPeriod = period;
    document.querySelectorAll('#scPeriodTabs .rtp-period-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.period === period));
    _scRenderBreakdown(_scDashTrades, period);
}

function _scRenderBreakdown(trades, period) {
    const ctx = document.getElementById('scBreakdownChart');
    if (!ctx || typeof Chart === 'undefined') return;

    const groups = {};
    (trades || []).forEach(t => {
        const key = _rtpPeriodKey(new Date(t.entry_time), period);
        if (!groups[key]) groups[key] = { inr: 0, wins: 0, losses: 0 };
        const inr = _algoNetInr(t);
        groups[key].inr += inr;
        if (inr >= 0) groups[key].wins++; else groups[key].losses++;
    });
    const keys   = Object.keys(groups).sort();
    const labels = keys.map(k => {
        if (period === 'monthly') { const [y, m] = k.split('-'); return new Date(+y, +m - 1).toLocaleString('default', { month: 'short', year: '2-digit' }); }
        if (period === 'weekly')  return 'W ' + k.slice(5);
        if (period === 'daily')   return k.slice(5);
        return k;
    });
    const values = keys.map(k => Math.round(groups[k].inr));
    const meta   = keys.map(k => groups[k]);
    const bg  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)' : 'rgba(239,68,68,.20)');
    const brd = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)' : 'rgba(239,68,68,.90)');

    const fmtBar = v => {
        const a = Math.abs(v), s = v >= 0 ? '+' : '−';
        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(1) + 'K';
        return s + '₹' + a;
    };

    if (_scBreakdownChart) { _scBreakdownChart.destroy(); _scBreakdownChart = null; }
    _scBreakdownChart = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        plugins: [_rtpBarLabelPlugin],
        data: { labels, datasets: [{ data: values, backgroundColor: bg, borderColor: brd, borderWidth: 1.5, borderRadius: 4, borderSkipped: false }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            layout: { padding: { top: 18, bottom: 4 } },
            plugins: {
                legend: { display: false },
                rtpBarLabels: { fmt: fmtBar },
                tooltip: { displayColors: false, padding: 10, callbacks: {
                    title: c => labels[c[0].dataIndex],
                    label: c => {
                        const i = c.dataIndex, v = values[i], g = meta[i];
                        const tr = g.wins + g.losses, wr = tr ? Math.round(g.wins / tr * 100) : 0;
                        return [
                            ' P&L: ' + (v >= 0 ? '+₹' : '-₹') + Math.abs(v).toLocaleString('en-IN'),
                            ` Trades: ${tr}  (${g.wins}W / ${g.losses}L)`,
                            ` Win Rate: ${wr}%`,
                        ];
                    },
                } },
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, color: '#94a3b8' } },
                y: {
                    grid: { color: c => c.tick.value === 0 ? 'rgba(128,128,128,.3)' : 'rgba(128,128,128,.08)' },
                    ticks: { font: { size: 9 }, color: '#94a3b8', callback: v => {
                        if (v === 0) return '0';
                        const a = Math.abs(v), s = v < 0 ? '−' : '';
                        if (a >= 100000) return s + '₹' + (a / 100000).toFixed(1) + 'L';
                        if (a >= 1000)   return s + '₹' + (a / 1000).toFixed(0) + 'K';
                        return s + '₹' + a;
                    } },
                },
            }
        }
    });
}

// ── SC actions ────────────────────────────────────────────────────────────────

function _scDeleteAllTrades() {
    if (!confirm('Delete ALL 2nd 30s Candle trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/sc/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_scHistoryTimer);
            _scFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _scDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/sc/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_scHistoryTimer);
            _scFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── Candle Breakout Strategy Logic popup ──────────────────────────────────────
// Info button (right of the "Candle Breakout" title) opens a modal
// that explains the strategy logic plus its Entry and Exit rules.

function scShowLogic() {
    document.getElementById('scLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'scLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">Candle Breakout</span>
            <span class="sm-modal-subtitle">How it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('scLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <!-- One-line idea -->
        <p class="rtp-idea">Take the <b>2nd 30-second candle</b> of the day — its High &amp; Low set the range. Trade the <b>first breakout</b> of that range. <b>One trade per day</b>, no re-entry.</p>

        <!-- Entry: BUY vs SELL side by side -->
        <div class="rtp-blk-lbl entry">Entry — first breakout of the range candle</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY</div>
                <div class="rtp-duo-row">Price breaks <b>above</b> range High</div>
                <div class="rtp-duo-row">Buy <b>CE</b> (delta ≈ 0.90)</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL</div>
                <div class="rtp-duo-row">Price breaks <b>below</b> range Low</div>
                <div class="rtp-duo-row">Buy <b>PE</b> (delta ≈ 0.90)</div>
            </div>
        </div>
        <div class="rtp-mode-note">Range = the <b>2nd 30-sec candle</b> (09:15:30–09:16:00). Only the <b>first</b> side to break triggers.</div>

        <!-- Exit: compact chip grid -->
        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target 1:3</b><span>3× the risk</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss</b><span>opposite range end</span></div></div>
            <div class="rtp-chip rtp-chip-wide"><span class="rtp-chip-ic eod">⏱</span><div><b>Cut-off 3:25 PM</b><span>force exit · no overnight hold</span></div></div>
        </div>
        <div class="rtp-mode-note"><b>Risk</b> = range height (High − Low). <b>Target</b> = entry ± 3 × risk (reward-to-risk 1:3).</div>

    </div>

</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.addEventListener('keydown', function esc(ev) {
        if (ev.key === 'Escape') { document.getElementById('scLogicModal')?.remove(); document.removeEventListener('keydown', esc); }
    });
    document.body.appendChild(modal);
}


function scExitNow(btn) {
    if (!confirm('Force-close the active Candle Breakout trade? A market SELL order will be sent immediately.')) return;
    _setBusy(btn, 'Exiting…');
    fetch('/api/algo/sc/exit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Exit failed: ' + (d.error || 'Unknown error')); _unbusy(btn, 'Force Exit'); return; }
            clearTimeout(_scStatusTimer);
            clearTimeout(_scHistoryTimer);
            _scFetchStatus();
            _scFetchHistory();
        })
        .catch(e => { alert('Request failed: ' + e); _unbusy(btn, 'Force Exit'); });
}

function scFetchDeltaStrikes(btn) {
    const panel = document.getElementById('scStrikesResult');
    btn.disabled = true;
    btn.classList.add('busy');
    btn.textContent = 'Loading…';
    panel.style.display = 'none';

    fetch('/api/algo/sc/delta-strikes')
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

// ── Intrinsic ATM Range Breakout (PAPER TRADE) ─────────────────────────────────

function _intrinsicFetchStatus() {
    fetch('/api/algo/intrinsic-range/status')
        .then(r => r.json())
        .then(data => {
            _intrinsicRenderStatus(data);
            const newEntryTime = data.state && data.state.active_trade
                ? data.state.active_trade.entry_time : null;
            const newActive = !!data.active;
            const tradeChanged = newEntryTime !== _intrinsicLastEntryTime ||
                                 newActive !== _intrinsicLastActiveFlag;
            _intrinsicLastEntryTime  = newEntryTime;
            _intrinsicLastActiveFlag = newActive;
            if (tradeChanged) {
                clearTimeout(_intrinsicHistoryTimer);
                _intrinsicFetchHistory();
            }
            clearTimeout(_intrinsicStatusTimer);
            _intrinsicStatusTimer = setTimeout(_intrinsicFetchStatus, newActive ? 5000 : 30000);
        })
        .catch(() => {
            clearTimeout(_intrinsicStatusTimer);
            _intrinsicStatusTimer = setTimeout(_intrinsicFetchStatus, 30000);
        });
}

function _intrinsicRenderStatus(data) {
    const state  = data.state || {};
    const setup  = state.daily_setup;
    const trade  = state.active_trade;
    const live   = data.live || null;
    const active = !!data.active;

    // Badge
    const badge = document.getElementById('intrinsicBadge');
    badge.className = 'ag-badge ' + (active ? 'active' : 'inactive');
    document.getElementById('intrinsicBadgeText').textContent =
        active ? (trade.direction + ' ' + trade.option_type) : 'No Trade';

    document.getElementById('intrinsicLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Today's Range Setup card
    const setupGrid = document.getElementById('intrinsicSetupGrid');
    if (!setup) {
        setupGrid.innerHTML = '<div class="ag-empty">Setup not computed yet — waiting for the daily setup step (after 9:16 AM)</div>';
    } else {
        const setupTiles = [
            { label: 'Prev Close Spot', value: '₹' + _num(setup.prev_close_spot) },
            { label: 'ATM Strike',      value: setup.atm_strike },
            { label: 'CE Prev Close',   value: '₹' + _num(setup.ce_prev_close) },
            { label: 'PE Prev Close',   value: '₹' + _num(setup.pe_prev_close) },
            { label: 'Common ATM',      value: '₹' + _num(setup.common_atm) },
            { label: 'Range Lower',     value: '₹' + _num(setup.lower_bound), cls: 'ag-neg' },
            { label: 'Range Upper',     value: '₹' + _num(setup.upper_bound), cls: 'ag-pos' },
            { label: 'Total Range',     value: _num(setup.total_range) + ' pts' },
        ];
        // Gap-day wide range + boundary-option day-low reclaim levels
        const ar = state.active_range;
        if (ar && ar.range_mult > 1) {
            setupTiles.push({
                label: 'Active Range (gap ' + (ar.gap_side || '?') + ')',
                value: _num(ar.lower_bound) + '–' + _num(ar.upper_bound),
                cls: 'ag-warn',
            });
        }
        if (state.low_reclaim_level != null) {
            setupTiles.push({ label: 'Low Reclaim Lvl',  value: '₹' + _num(state.low_reclaim_level),  cls: 'ag-pos' });
        }
        if (state.high_reclaim_level != null) {
            setupTiles.push({ label: 'High Reclaim Lvl', value: '₹' + _num(state.high_reclaim_level), cls: 'ag-neg' });
        }
        setupGrid.innerHTML = setupTiles.map(t =>
            `<div class="ag-stat">
                <span class="ag-stat-label">${t.label}</span>
                <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
            </div>`
        ).join('');
    }

    // Active trade grid
    const grid = document.getElementById('intrinsicActiveGrid');
    if (!active || !trade) {
        grid.innerHTML = '<div class="ag-empty">No active trade</div>';
        return;
    }

    const spotStr = live ? '₹' + _num(live.spot) : '…';

    const tiles = [
        { label: 'Direction',    value: trade.direction,
          cls: trade.direction === 'BUY' ? 'ag-pos' : 'ag-neg' },
        { label: 'Entry Kind',   value: trade.entry_kind || 'BREAKOUT' },
        { label: 'Option',       value: trade.option_type + ' ' + trade.strike },
        { label: 'Entry Spot',   value: '₹' + _num(trade.entry_spot) },
        { label: 'Live Spot',    value: spotStr },
        { label: 'SL Level',     value: '₹' + _num(trade.sl_level),     cls: 'ag-warn' },
        { label: 'Target Level', value: '₹' + _num(trade.target_level), cls: 'ag-pos' },
        ..._algoOptTiles(trade, live),
        { label: 'Entry Time',   value: trade.entry_time ? _fmtTime(trade.entry_time) : '—' },
    ];

    grid.innerHTML = tiles.map(t =>
        `<div class="ag-stat">
            <span class="ag-stat-label">${t.label}</span>
            <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
        </div>`
    ).join('');
}

// ── Intrinsic Range history ─────────────────────────────────────────────────────

function _intrinsicFetchHistory() {
    fetch('/api/algo/intrinsic-range/history')
        .then(r => r.json())
        .then(data => {
            _intrinsicRenderHistory(data.trades || []);
            clearTimeout(_intrinsicHistoryTimer);
            _intrinsicHistoryTimer = setTimeout(_intrinsicFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_intrinsicHistoryTimer);
            _intrinsicHistoryTimer = setTimeout(_intrinsicFetchHistory, 30000);
        });
}

function _intrinsicRenderHistory(trades) {
    const countEl = document.getElementById('intrinsicHistCount');
    const body    = document.getElementById('intrinsicHistBody');

    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';

    if (!trades.length) {
        body.innerHTML = '<div class="ag-empty">No completed trades</div>';
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
            <th class="ag-hist-th">Premium Entry</th>
            <th class="ag-hist-th">Premium Exit</th>
            <th class="ag-hist-th">Entry Spot</th>
            <th class="ag-hist-th">Exit Spot</th>
            <th class="ag-hist-th">Spot Pts</th>
            <th class="ag-hist-th">Premium Pts</th>
            <th class="ag-hist-th">Premium P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
            <th class="ag-hist-th"></th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const premPts   = t.premium_pnl_pts;
        const pPnlCls   = (premPts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const dateStr   = t.date || (t.entry_time ? t.entry_time.slice(0, 10) : '—');
        const entryTime = t.entry_time ? _fmtTimeOnly(t.entry_time) : '—';
        const exitTime  = t.exit_time  ? _fmtTimeOnly(t.exit_time)  : '—';
        const entryKey  = (t.entry_time || '').replace(/"/g, '&quot;');
        return `<tr>
            <td class="ag-hist-td">${dateStr}</td>
            <td class="ag-hist-td">${entryTime}</td>
            <td class="ag-hist-td">${exitTime}</td>
            <td class="ag-hist-td">${t.strike ?? '—'} ${t.option_type ?? ''}</td>
            <td class="ag-hist-td">${_fmtOpt(t.entry_premium)}</td>
            <td class="ag-hist-td">${_fmtOpt(t.exit_premium)}</td>
            <td class="ag-hist-td">₹${_num(t.entry_spot)}</td>
            <td class="ag-hist-td">₹${_num(t.exit_spot)}</td>
            <td class="ag-hist-td ${nPnlCls}" style="font-weight:700">${_fmtPts(t.pnl_pts, ' pts')}</td>
            <td class="ag-hist-td ${premPts != null ? pPnlCls : ''}" style="font-weight:700">${premPts != null ? (premPts >= 0 ? '+' : '') + premPts.toFixed(2) : '—'}</td>
            <td class="ag-hist-td ${t.premium_pnl_inr != null ? pPnlCls : ''}" style="font-weight:700">${_fmtInr(t.premium_pnl_inr)}</td>
            <td class="ag-hist-td">${_rtpFmtReason(t.reason)}</td>
            <td class="ag-hist-td ag-hist-td-del">
                <button class="ag-hist-del-btn" onclick="_intrinsicDeleteTrade('${entryKey}')" title="Delete record">&#128465;</button>
            </td>
        </tr>`;
    }).join('')}
    </tbody>
</table>
</div>`;
}

function _intrinsicDeleteAllTrades() {
    if (!confirm('Delete ALL Intrinsic Range paper-trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/intrinsic-range/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_intrinsicHistoryTimer);
            _intrinsicFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _intrinsicDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/intrinsic-range/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_intrinsicHistoryTimer);
            _intrinsicFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── Intrinsic Range Strategy Logic popup ───────────────────────────────────────

function intrinsicShowLogic() {
    document.getElementById('intrinsicLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'intrinsicLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">Intrinsic ATM Range Breakout</span>
            <span class="sm-modal-subtitle">Paper trade — how it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('intrinsicLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <div class="rtp-tf"><span class="rtp-tf-lbl">Mode</span><span class="rtp-tf-val">Paper trade only</span><span class="rtp-tf-sub">no broker orders — simulated fills off live LTP</span></div>

        <p class="rtp-idea">Every morning, find the strike where <b>CE and PE premiums (previous close) are closest</b> — the "common ATM". Average those two premiums, round to the nearest strike step, and use it as a <b>symmetric range</b> around that ATM strike — yesterday's intrinsic-value / expected-move zone.</p>

        <div class="rtp-blk-lbl entry">Entry — breakout confirmed 3 ways</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY (CE)</div>
                <div class="rtp-duo-row">Spot closes <b>above upper bound</b></div>
                <div class="rtp-duo-row">Lower-strike CE premium &ge; total range</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL (PE)</div>
                <div class="rtp-duo-row">Spot closes <b>below lower bound</b></div>
                <div class="rtp-duo-row">Upper-strike PE premium &ge; total range</div>
            </div>
        </div>
        <div class="rtp-mode-note">Both directions also require the live common-ATM premium <b>and</b> India VIX to be expanding vs. the day's opening reading — separates a real trend day from range-bound noise. Trade buys the current-spot ATM option in the breakout direction.</div>

        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target</b><span>default: day's range/2</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss</b><span>default: day's range/4</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic eod">↩</span><div><b>Range Reclaim</b><span>spot re-enters the range</span></div></div>
            <div class="rtp-chip rtp-chip-wide"><span class="rtp-chip-ic eod">⏱</span><div><b>EOD 3:20 PM</b><span>force exit · no overnight hold</span></div></div>
        </div>
    </div>
</div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
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

// ── Swing Momentum Live Watch ─────────────────────────────────────────────────

function _smLiveFetchConfigs() {
    fetch('/api/algo/swing-momentum/configs')
        .then(r => r.json())
        .then(d => {
            if (!d.success) return;
            _smLiveRenderConfigs(d.configs || []);
        })
        .catch(() => {});
}

function _smLiveRenderConfigs(configs) {
    // Clear per-config P&L so the header total resets for this render
    Object.keys(_smPnlByConfig).forEach(k => delete _smPnlByConfig[k]);
    _smUpdateTotalPnl();

    const badge      = document.getElementById('smLiveBadge');
    const badgeTxt   = document.getElementById('smLiveBadgeText');
    const empty      = document.getElementById('smLiveEmpty');
    const container  = document.getElementById('smLiveConfigsContainer');

    const watching = configs.filter(c => c.status === 'watching').length;
    badge.className = 'ag-badge ' + (watching > 0 ? 'active' : 'inactive');
    badgeTxt.textContent = configs.length === 0 ? '0 Configs'
        : `${watching} Watching` + (configs.length > watching ? ` / ${configs.length - watching} Paused` : '');

    empty.style.display  = configs.length === 0 ? '' : 'none';

    // ── Group configs by the broker chosen at Go Live ──────────────────────────
    // Each broker becomes its own group; configs with no broker fall into a
    // separate "None — track only" group rendered last.
    Object.keys(_smGroupOfConfig).forEach(k => delete _smGroupOfConfig[k]);
    const groups = new Map();   // key → { gid, label, type, isNone, configs: [] }
    configs.forEach(c => {
        const b   = c.broker;
        const key = b ? `inst-${b.instance}` : '__none__';
        if (!groups.has(key)) {
            groups.set(key, {
                gid:     b ? `inst-${b.instance}` : 'none',
                label:   b ? (b.broker_name || b.broker_type || 'Broker') : 'None',
                type:    b ? (b.broker_type || '') : '',
                isNone:  !b,
                configs: [],
            });
        }
        const g = groups.get(key);
        g.configs.push(c);
        _smGroupOfConfig[c.id] = g.gid;   // remember which group each config feeds
    });

    // Broker groups first (alphabetical), the "None" group always last.
    const groupArr = Array.from(groups.values()).sort((a, b) =>
        a.isNone !== b.isNone ? (a.isNone ? 1 : -1) : a.label.localeCompare(b.label));

    container.innerHTML = groupArr.map(g => {
        const cards   = g.configs.map(c => _smLiveBuildCard(c)).join('');
        const cnt     = g.configs.length;
        const typeStr = g.type ? `<span class="sm-broker-group-type">${g.type.toUpperCase()}</span>` : '';
        const cntLbl  = `${cnt} config${cnt > 1 ? 's' : ''}`;
        return `
<div class="sm-broker-group ${g.isNone ? 'sm-broker-group-none' : 'sm-broker-group-live'}">
    <div class="sm-broker-group-hdr">
        <span class="sm-broker-group-icon">${g.isNone ? '📋' : '🏦'}</span>
        <span class="sm-broker-group-name">${g.isNone ? 'None — track only' : g.label}</span>
        ${typeStr}
        <span class="sm-broker-group-pnl" id="sm-grp-pnl-${g.gid}"></span>
        <span class="sm-broker-group-count">${cntLbl}</span>
    </div>
    <div class="sm-broker-group-cards">${cards}</div>
</div>`;
    }).join('');

    _smUpdateGroupPnls();

    container.querySelectorAll('.sm-live-remove-btn').forEach(btn =>
        btn.addEventListener('click', e => { e.stopPropagation(); _smLiveRemove(btn.dataset.id); }));
    container.querySelectorAll('.sm-live-toggle-btn').forEach(btn =>
        btn.addEventListener('click', e => { e.stopPropagation(); _smLiveToggle(btn.dataset.id, btn); }));
    container.querySelectorAll('.sm-live-refresh-btn').forEach(btn =>
        btn.addEventListener('click', e => {
            e.stopPropagation();
            _smLiveLoadSignal(btn.dataset.id);
        }));
    container.querySelectorAll('.sm-live-reinit-btn').forEach(btn =>
        btn.addEventListener('click', e => { e.stopPropagation(); _smLiveReinit(btn.dataset.id); }));
    container.querySelectorAll('.sm-live-card-hdr').forEach(hdr =>
        hdr.addEventListener('click', () => _smLiveExpandToggle(hdr.dataset.id)));

    document.getElementById('smLiveLastUpd').textContent =
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // Kick off background prefetch for every config (watching AND paused) so
    // data is ready before the user expands a card, and so paused configs still
    // contribute their live P&L to the per-broker group totals.
    configs.forEach(c => _smPrefetch(c.id));
}

// Lazy-load cache: id → { signal: Promise, rankings: Promise, ts: ms }
const _smCache = {};
const _SM_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

// Per-config P&L map for header total
const _smPnlByConfig = {};
// config id → broker-group id (gid), rebuilt on every render
const _smGroupOfConfig = {};

// Aggregate per-config P&L into each broker group's header chip.
function _smUpdateGroupPnls() {
    const sums = {};   // gid → { today, total, has }
    Object.keys(_smPnlByConfig).forEach(id => {
        const gid = _smGroupOfConfig[id];
        if (!gid) return;
        const p = _smPnlByConfig[id];
        const s = sums[gid] || (sums[gid] = { today: 0, total: 0, has: false });
        s.today += p.today || 0;
        s.total += p.total || 0;
        s.has = true;
    });
    const fmtVal = (v) => (v >= 0 ? '+₹' : '-₹') +
        Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    document.querySelectorAll('.sm-broker-group-pnl').forEach(el => {
        const gid = el.id.replace('sm-grp-pnl-', '');
        const s   = sums[gid];
        if (!s || !s.has) { el.className = 'sm-broker-group-pnl'; el.innerHTML = ''; return; }
        el.className = 'sm-broker-group-pnl sm-broker-group-pnl-loaded';
        el.innerHTML = `
            <span class="sm-grp-pnl-item ${s.today >= 0 ? 'sm-tpnl-pos' : 'sm-tpnl-neg'}">
                <span class="sm-grp-pnl-label">Today</span>${fmtVal(s.today)}
            </span>
            <span class="sm-grp-pnl-sep">|</span>
            <span class="sm-grp-pnl-item ${s.total >= 0 ? 'sm-tpnl-pos' : 'sm-tpnl-neg'}">
                <span class="sm-grp-pnl-label">Total</span>${fmtVal(s.total)}
            </span>`;
    });
}

function _smUpdateTotalPnl() {
    const el = document.getElementById('smLiveTotalPnl');
    if (!el) return;
    const entries = Object.values(_smPnlByConfig);
    if (!entries.length) { el.className = 'sm-total-pnl-chip'; el.innerHTML = ''; return; }
    const todaySum = entries.reduce((s, v) => s + (v.today || 0), 0);
    const totalSum = entries.reduce((s, v) => s + (v.total || 0), 0);
    const fmtVal = (v) => {
        const s = v >= 0 ? '+₹' : '-₹';
        return s + Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    };
    el.className = 'sm-total-pnl-chip sm-total-pnl-loaded';
    el.innerHTML = `
        <span class="sm-tp-item ${todaySum >= 0 ? 'sm-tpnl-pos' : 'sm-tpnl-neg'}">
            <span class="sm-tp-label">Today</span>${fmtVal(todaySum)}
        </span>
        <span class="sm-tp-sep">|</span>
        <span class="sm-tp-item ${totalSum >= 0 ? 'sm-tpnl-pos' : 'sm-tpnl-neg'}">
            <span class="sm-tp-label">Total</span>${fmtVal(totalSum)}
        </span>`;
}

function _smLiveBuildCard(c) {
    const isWatching = c.status === 'watching';
    const hdrCls     = isWatching ? 'sm-hdr-watching' : 'sm-hdr-paused';
    const toggleLbl  = isWatching ? 'Pause'               : 'Watch';
    const freqLabel  = { weekly: 'Weekly', monthly: 'Monthly', quarterly: 'Quarterly' }[c.rebalance_freq] || c.rebalance_freq;
    const indexLabel = c.index || '';
    const inv        = Number(c.investment).toLocaleString('en-IN', { maximumFractionDigits: 0 });

    const liveSince = c.live_since || '—';

    return `
<div class="ag-card sm-live-card" id="sm-card-${c.id}">
    <div class="sm-live-card-hdr ${hdrCls}" data-id="${c.id}">
        <span class="sm-chevron" id="sm-chev-${c.id}">&#9654;</span>
        <div class="sm-live-title-block">
            <div class="sm-live-label">${indexLabel}</div>
            <div class="sm-live-subtitle">
                <span>${freqLabel}</span>
                <span class="sm-live-subtitle-sep">·</span>
                <span>Top ${c.top_n}</span>
                <span class="sm-live-subtitle-sep">·</span>
                <span>Exit &gt;${c.exit_rank}</span>
            </div>
        </div>
        <span class="sm-live-inv-chip">₹${inv}</span>
        <div class="sm-live-hdr-actions">
            <button class="ag-btn ag-btn-strikes ag-btn-icon-only sm-live-refresh-btn" data-id="${c.id}" title="Refresh signal">↻</button>
            <button class="ag-btn ag-btn-strikes sm-live-reinit-btn" data-id="${c.id}" title="Re-initialize live entries with today's rankings">⟳ Re-init</button>
            <span class="sm-actions-divider"></span>
            <button class="ag-btn ag-btn-preview sm-live-toggle-btn" data-id="${c.id}">${toggleLbl}</button>
            <button class="ag-btn ag-btn-exit sm-live-remove-btn" data-id="${c.id}">✕</button>
        </div>
    </div>
    <div class="sm-card-meta-row" id="sm-meta-${c.id}">
        <span class="sm-meta-item" id="sm-meta-dep-${c.id}">Deployed —</span>
        <span class="sm-meta-sep">·</span>
        <span class="sm-meta-item sm-meta-idle" id="sm-meta-idle-${c.id}">Unused —</span>
        <span class="sm-meta-sep">·</span>
        <span class="sm-meta-item" id="sm-meta-cur-${c.id}">Current —</span>
        <span class="sm-meta-sep">·</span>
        <span class="sm-meta-item sm-meta-pnl" id="sm-meta-today-${c.id}">Today —</span>
        <span class="sm-meta-sep">·</span>
        <span class="sm-meta-item sm-meta-pnl" id="sm-meta-total-${c.id}">Total —</span>
        <span class="sm-meta-sep">·</span>
        <span class="sm-meta-item sm-meta-pnl" id="sm-meta-cagr-${c.id}">CAGR —</span>
        <span class="sm-meta-sep">·</span>
        <span class="sm-meta-item" id="sm-meta-reb-${c.id}">Rebal —</span>
        <span class="sm-meta-sep">·</span>
        <span class="sm-meta-since">Live since ${liveSince}</span>
    </div>
    <div class="sm-live-card-body" id="sm-body-${c.id}">
        <div id="sm-signal-${c.id}" class="sm-live-signal-panel">
            <div class="sm-signal-loading">Click to expand and load portfolio state…</div>
        </div>
    </div>
</div>`;
}

function _smLiveReinit(id) {
    if (!confirm('Re-initialize live entries with today\'s rankings?\nThis will replace current entry prices and quantities.')) return;
    fetch(`/api/algo/swing-momentum/configs/${id}/go-live`, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.success) {
                _smInvalidateCache(id);
                _smLiveFetchConfigs();
            } else {
                alert('Re-init failed: ' + (d.error || 'Unknown error'));
            }
        })
        .catch(() => alert('Re-init request failed'));
}

function _smLiveRemove(id) {
    if (!confirm('Remove this live config?')) return;
    fetch(`/api/algo/swing-momentum/configs/${id}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(d => { if (d.success) _smLiveFetchConfigs(); })
        .catch(() => {});
}

function _smLiveToggle(id, btn) {
    _setBusy(btn, '…');
    fetch(`/api/algo/swing-momentum/configs/${id}/toggle`, { method: 'POST' })
        .then(r => r.json())
        .then(d => { if (d.success) _smLiveFetchConfigs(); })
        .catch(() => _unbusy(btn));
}

// ── Lazy-load prefetch ────────────────────────────────────────────────────────
// Both API calls start in the background as soon as cards render.
// _smLiveExpandToggle just awaits the already-in-flight promises.

function _smFmtPnl(abs, pct) {
    const sign = abs >= 0 ? '+₹' : '-₹';
    return sign + Math.abs(abs).toLocaleString('en-IN', { maximumFractionDigits: 0 })
         + ' (' + (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%)';
}

function _smUpdateMetaRow(id, d) {
    if (!d?.success) return;
    const dep     = document.getElementById(`sm-meta-dep-${id}`);
    const cur     = document.getElementById(`sm-meta-cur-${id}`);
    const todayEl = document.getElementById(`sm-meta-today-${id}`);
    const totalEl = document.getElementById(`sm-meta-total-${id}`);
    const reb     = document.getElementById(`sm-meta-reb-${id}`);

    if (dep) dep.textContent = 'Deployed ' + _smFmtInr(d.total_invested || 0);
    if (cur) cur.textContent = 'Current '  + _smFmtInr(d.current_port_val || 0);

    const idleEl = document.getElementById(`sm-meta-idle-${id}`);
    if (idleEl) {
        // Unused (idle) cash = total capital (base + SIP − SWP) not yet deployed.
        const capital = (d.configured_investment || 0) + (d.total_sip_added || 0) - (d.total_swp_taken || 0);
        idleEl.textContent = 'Unused ' + _smFmtInr(Math.max(0, capital - (d.total_invested || 0)));
    }

    const todayAbs = d.today_pnl      || 0;
    const todayPct = d.today_pct      || 0;
    const totAbs   = d.unrealised_pnl || 0;
    const totPct   = d.unrealised_pct || 0;

    if (todayEl) {
        todayEl.textContent = 'Today ' + _smFmtPnl(todayAbs, todayPct);
        todayEl.className   = 'sm-meta-item sm-meta-pnl ' + (todayAbs >= 0 ? 'sm-meta-pos' : 'sm-meta-neg');
    }
    if (totalEl) {
        totalEl.textContent = 'Total ' + _smFmtPnl(totAbs, totPct);
        totalEl.className   = 'sm-meta-item sm-meta-pnl ' + (totAbs >= 0 ? 'sm-meta-pos' : 'sm-meta-neg');
    }
    const cagrEl = document.getElementById(`sm-meta-cagr-${id}`);
    if (cagrEl) {
        const cagr = d.cagr_pct || 0;
        cagrEl.textContent = 'CAGR ' + (cagr >= 0 ? '+' : '') + cagr.toFixed(1) + '%';
        cagrEl.className   = 'sm-meta-item sm-meta-pnl ' + (cagr >= 0 ? 'sm-meta-pos' : 'sm-meta-neg');
    }
    if (reb) reb.textContent = 'Rebal ' + (d.next_rebalance || '—');

    _smPnlByConfig[id] = { today: todayAbs, total: totAbs };
    _smUpdateTotalPnl();
    _smUpdateGroupPnls();
}

function _smUpdateHdrPnl(id, d) {
    const el = document.getElementById(`sm-hdr-pnl-${id}`);
    if (!el || !d?.success) return;
    const todayAbs = d.today_pnl      || 0;
    const todayPct = d.today_pct      || 0;
    const totAbs   = d.unrealised_pnl || 0;
    const totPct   = d.unrealised_pct || 0;
    el.className = 'sm-hdr-pnl sm-hdr-pnl-loaded';
    el.innerHTML = `
        <span class="sm-hdr-pnl-row sm-hdr-today ${todayAbs >= 0 ? 'ag-pos' : 'ag-neg'}">
            <span class="sm-hdr-pnl-label">Today</span>
            ${_smFmtPnl(todayAbs, todayPct)}
        </span>
        <span class="sm-hdr-pnl-sep">|</span>
        <span class="sm-hdr-pnl-row sm-hdr-total ${totAbs >= 0 ? 'ag-pos' : 'ag-neg'}">
            <span class="sm-hdr-pnl-label">Total</span>
            ${_smFmtPnl(totAbs, totPct)}
        </span>`;
}

function _smPrefetch(id, force = false) {
    const existing = _smCache[id];
    if (!force && existing && (Date.now() - existing.ts) < _SM_CACHE_TTL_MS) {
        // Cache is fresh — just re-apply the P&L and meta row to the (possibly re-rendered) header
        existing.signal.then(d => { _smUpdateHdrPnl(id, d); _smUpdateMetaRow(id, d); });
        return;
    }

    // Signal (fast: Fyers LTP or yfinance fallback)
    const signalP = fetch(`/api/algo/swing-momentum/signal/${id}`)
        .then(r => r.json())
        .then(d => { _smUpdateHdrPnl(id, d); _smUpdateMetaRow(id, d); return d; })
        .catch(() => null);

    // Rankings (slow: 500 stocks, server-side 15-min cache)
    const rankingsP = fetch(`/api/algo/swing-momentum/signal/${id}/rankings`)
        .then(r => r.json())
        .catch(() => null);

    _smCache[id] = { signal: signalP, rankings: rankingsP, ts: Date.now() };
}

function _smInvalidateCache(id) {
    delete _smCache[id];
}

function _smLiveExpandToggle(id) {
    const card = document.getElementById(`sm-card-${id}`);
    if (!card) return;

    const isOpen = card.classList.toggle('sm-card-open');

    if (!isOpen) return;

    const panel = document.getElementById(`sm-signal-${id}`);
    if (!panel) return;

    const cached = _smCache[id];
    if (!cached) {
        // Not prefetched yet (e.g. paused config) — fetch now
        panel.innerHTML = '<div class="sm-signal-loading">Fetching live prices…</div>';
        _smPrefetch(id);
    }

    // Await signal (may already be resolved)
    const entry = _smCache[id];
    panel.innerHTML = '<div class="sm-signal-loading">Fetching live prices…</div>';
    entry.signal.then(d => {
        if (!d || !d.success) {
            panel.innerHTML = `<div class="sm-signal-error">⚠ ${d?.error || 'Failed to load signal'}</div>`;
            return;
        }
        _smLiveRenderSignal(id, d);

        // Await rankings (may already be resolved)
        entry.rankings.then(r => {
            if (!r || !r.success) return;
            _smApplyRankings(id, r);
        });
    });
}

function _smApplyRankings(id, d) {
    const ranks = d.holding_ranks || {};

    Object.entries(ranks).forEach(([sym, info]) => {
        const row = document.querySelector(`#sm-signal-${id} tr[data-sym="${sym}"]`);
        if (!row) return;
        row.dataset.score = info.momentum_score ?? -9999;
        const rankCell  = row.querySelector('.sm-rank-cell');
        const scoreCell = row.querySelector('.sm-score-cell');
        if (rankCell) rankCell.textContent = info.current_rank ?? '—';
        if (scoreCell) {
            const score = info.momentum_score;
            scoreCell.textContent = score != null
                ? (score >= 0 ? '+' : '') + score.toFixed(1) + '%' : '—';
            scoreCell.className = 'sm-td-score sm-score-cell ' +
                (score != null ? (score >= 0 ? 'sm-score-pos' : 'sm-score-neg') : '');
        }
    });

    // Re-sort by momentum score descending
    const tbody = document.querySelector(`#sm-signal-${id} .sm-holdings-table tbody`);
    if (tbody) {
        const rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort((a, b) => parseFloat(b.dataset.score || -9999) - parseFloat(a.dataset.score || -9999));
        rows.forEach(r => tbody.appendChild(r));
    }

    // Fill rebalance preview
    const wrap = document.getElementById(`sm-rebal-preview-${id}`);
    if (wrap) wrap.innerHTML = _smRebalPreviewHtml(d, id);
}

function _smLiveLoadSignal(id) {
    _smInvalidateCache(id);
    _smPrefetch(id, true);
    if (document.getElementById(`sm-card-${id}`)?.classList.contains('sm-card-open')) {
        // Re-render immediately with fresh fetch
        const panel = document.getElementById(`sm-signal-${id}`);
        if (panel) panel.innerHTML = '<div class="sm-signal-loading">Refreshing…</div>';
        _smCache[id].signal.then(d => {
            if (!d || !d.success) {
                if (panel) panel.innerHTML = `<div class="sm-signal-error">⚠ ${d?.error || 'Failed'}</div>`;
                return;
            }
            _smLiveRenderSignal(id, d);
            _smCache[id].rankings.then(r => { if (r?.success) _smApplyRankings(id, r); });
        });
    }
}

function _smLiveRenderSignal(id, d) {
    const panel = document.getElementById(`sm-signal-${id}`);
    _smRenderLiveMode(id, panel, d);
}

const _smSipLogs = {};   // id → log array (used by popup)

// ── Live-mode: P&L from real entry prices locked on go-live date ──────────────

function _smRenderLiveMode(id, panel, d) {
    const holdings   = d.live_holdings || [];
    const pnlVal     = d.unrealised_pnl || 0;
    const pnlCls     = pnlVal >= 0 ? 'ag-pos' : 'ag-neg';
    const pnlSign    = pnlVal >= 0 ? '+₹' : '-₹';
    const pnlFmt     = pnlSign + Math.abs(pnlVal).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    const pnlPct     = ((d.unrealised_pct || 0) >= 0 ? '+' : '') + (d.unrealised_pct || 0).toFixed(1) + '%';

    const cfgInv     = d.configured_investment || 0;
    const sipLog     = d.monthly_investment_log || [];
    const totalSip   = d.total_sip_added || 0;
    _smSipLogs[id]   = sipLog;

    const holdingsHtml = holdings.length
        ? `<div class="sm-signal-holdings-scroll">
            <table class="sm-signal-table sm-holdings-table">
                <thead><tr>
                    <th class="sm-th-rank">Rank</th>
                    <th class="sm-th-score">Avg (3M+6M+9M)/3</th>
                    <th>Symbol</th><th>Qty</th><th>Entry Date</th>
                    <th>Entry ₹</th><th>Curr ₹</th>
                    <th>Invested</th><th>Curr Value</th>
                    <th class="sm-th-today">Today</th>
                    <th>Total</th>
                    <th class="sm-th-action"></th>
                </tr></thead>
                <tbody>
                ${holdings.map((h) => {
                    const tCls   = (h.today_pct || 0) >= 0 ? 'sm-pos' : 'sm-neg';
                    const tSign  = (h.today_abs || 0) >= 0 ? '+₹' : '-₹';
                    const tPct   = ((h.today_pct || 0) >= 0 ? '+' : '') + (h.today_pct || 0).toFixed(1) + '%';
                    const pCls   = h.pnl_pct >= 0 ? 'sm-pos' : 'sm-neg';
                    const pSign  = h.pnl_abs >= 0 ? '+₹' : '-₹';
                    const pPct   = (h.pnl_pct >= 0 ? '+' : '') + h.pnl_pct.toFixed(1) + '%';
                    return `<tr data-sym="${h.symbol}">
                        <td class="sm-td-rank"><span class="sm-rank-pill sm-rank-cell">—</span></td>
                        <td class="sm-td-score sm-score-cell" style="color:var(--ag-text-3)">…</td>
                        <td class="sm-col-sym"><strong>${h.symbol}</strong></td>
                        <td class="sm-col-num sm-editable" title="Double-click to edit" ondblclick="_smEditHolding(this,'${id}','${h.symbol}','qty')">${h.qty}</td>
                        <td class="sm-td-date sm-editable" title="Double-click to edit" ondblclick="_smEditHolding(this,'${id}','${h.symbol}','entry_date')">${h.entry_date || '—'}</td>
                        <td class="sm-editable" title="Double-click to edit" ondblclick="_smEditHolding(this,'${id}','${h.symbol}','entry_price')">₹${Number(h.entry_price).toFixed(2)}</td>
                        <td>₹${Number(h.current_price).toFixed(2)}</td>
                        <td class="sm-editable" title="Double-click to edit" ondblclick="_smEditHolding(this,'${id}','${h.symbol}','invested')">₹${Number(h.buy_value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</td>
                        <td>₹${Number(h.current_value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</td>
                        <td class="${tCls} sm-td-today">${tSign}${Math.abs(h.today_abs || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })} <span class="sm-cell-pct">(${tPct})</span></td>
                        <td class="${pCls} sm-pnl-abs">${pSign}${Math.abs(h.pnl_abs).toLocaleString('en-IN', { maximumFractionDigits: 0 })} <span class="sm-cell-pct">(${pPct})</span></td>
                        <td class="sm-td-action">
                            <button class="sm-row-menu-btn" title="Buy / Sell" onclick="_smRowMenu(event, '${id}', '${h.symbol}')">⋯</button>
                        </td>
                    </tr>`;
                }).join('')}
                </tbody>
            </table></div>`
        : '<div class="ag-empty">No live holdings</div>';

    // Stash holdings + meta so the SIP/SWP popup can compute allocations client-side
    _smHoldingsData[id] = { holdings, broker: d.broker || null, configuredInvestment: cfgInv };

    const totalSwp = d.total_swp_taken || 0;

    panel.innerHTML = `
<!-- ── Compact action bar: Capital + SIP/SWP ── -->
<div class="sm-action-bar">
    <div class="sm-ab-capital">
        <span class="sm-ab-label">Capital</span>
        <span class="sm-ab-val" id="sm-cfg-inv-${id}">${_smFmtInr(cfgInv)}</span>
        ${totalSip > 0 ? `<span class="sm-ab-sip-added">+${_smFmtInr(totalSip)} SIP</span>` : ''}
        ${totalSwp > 0 ? `<span class="sm-ab-swp-taken">−${_smFmtInr(totalSwp)} SWP</span>` : ''}
    </div>
    <span class="sm-ab-divider"></span>
    <div class="sm-ab-sip">
        <button class="ag-btn ag-btn-enter sm-ab-btn sm-ab-place" onclick="_smOpenPlaceOrders('${id}')">🛒 Place Orders</button>
        <button class="ag-btn ag-btn-preview sm-ab-btn" onclick="_smOpenFlowModal('${id}', 'sip')">＋ SIP</button>
        <button class="ag-btn ag-btn-exit sm-ab-btn" onclick="_smOpenFlowModal('${id}', 'swp')">－ SWP</button>
        <button class="sm-history-btn sm-ab-btn" onclick="_smOpenExitHistory('${id}')">📕 Order History</button>
        ${sipLog.length ? `<button class="sm-history-btn sm-ab-btn" onclick="_smShowSipHistory('${id}')">SIP/SWP Log (${sipLog.length})</button>` : ''}
    </div>
</div>

<div class="sm-signal-section-title">
    <span class="sm-live-dot-xs"></span>
    Live Holdings &mdash; ${holdings.length} stocks &mdash; ${d.live_since || ''}
</div>
${holdingsHtml}

<div id="sm-rebal-preview-${id}" class="sm-rebal-preview-wrap">
    <div class="sm-signal-loading" style="font-size:0.78rem;padding:8px 0">Loading momentum rankings…</div>
</div>`;
}

function _smShowSipHistory(id) {
    const log   = _smSipLogs[id] || [];
    const existing = document.getElementById('sm-sip-history-modal');
    if (existing) existing.remove();

    const total   = log.reduce((s, e) => s + (e.amount || 0), 0);
    const avg     = log.length ? Math.round(total / log.length) : 0;
    const fmtInr  = v => '₹' + Math.round(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });

    const bodyHtml = log.length
        ? [...log].reverse().map((e, i) => {
            const amt   = e.amount || 0;
            const isSwp = amt < 0 || e.type === 'swp';
            const tag   = isSwp ? '<span class="sm-mtag sm-mtag-swp">SWP</span>'
                                : '<span class="sm-mtag sm-mtag-sip">SIP</span>';
            return `
            <tr class="${i % 2 === 0 ? 'sm-mrow-even' : 'sm-mrow-odd'}">
                <td class="sm-mcell sm-mcell-date">
                    <span class="sm-mdate-icon">&#128197;</span>${e.date} ${tag}
                </td>
                <td class="sm-mcell sm-mcell-amt ${isSwp ? 'sm-neg' : 'sm-pos'}">${amt < 0 ? '−' : '+'}${fmtInr(Math.abs(amt))}</td>
                <td class="sm-mcell sm-mcell-note">${e.note || '<span class="sm-mdash">—</span>'}</td>
            </tr>`; }).join('')
        : `<tr><td colspan="3" class="sm-mcell" style="text-align:center;padding:28px 0;color:var(--ag-text-3)">No entries recorded yet</td></tr>`;

    const modal = document.createElement('div');
    modal.id = 'sm-sip-history-modal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">SIP History</span>
            <span class="sm-modal-subtitle">Recorded investments over time</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('sm-sip-history-modal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="sm-modal-stats">
        <div class="sm-mstat">
            <span class="sm-mstat-lbl">Total Invested</span>
            <span class="sm-mstat-val sm-mstat-green">${fmtInr(total)}</span>
        </div>
        <div class="sm-mstat-div"></div>
        <div class="sm-mstat">
            <span class="sm-mstat-lbl">Entries</span>
            <span class="sm-mstat-val">${log.length}</span>
        </div>
        <div class="sm-mstat-div"></div>
        <div class="sm-mstat">
            <span class="sm-mstat-lbl">Avg / Entry</span>
            <span class="sm-mstat-val">${avg ? fmtInr(avg) : '—'}</span>
        </div>
    </div>

    <div class="sm-modal-table-wrap">
        <table class="sm-modal-table">
            <thead>
                <tr>
                    <th class="sm-mth">Date</th>
                    <th class="sm-mth sm-mth-r">Amount</th>
                    <th class="sm-mth">Note</th>
                </tr>
            </thead>
            <tbody>${bodyHtml}</tbody>
        </table>
    </div>

    <div class="sm-modal-footer">
        <span class="sm-mfooter-lbl">Total invested</span>
        <span class="sm-mfooter-val">${fmtInr(total)}</span>
    </div>

</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
}

function _smToggleEditInv(id, currentVal) {
    const form  = document.getElementById(`sm-inv-edit-form-${id}`);
    const input = document.getElementById(`sm-inv-input-${id}`);
    if (!form) return;
    const isOpen = form.style.display !== 'none';
    form.style.display = isOpen ? 'none' : 'flex';
    if (!isOpen && currentVal != null && input) input.value = currentVal;
}

function _smSaveInv(id) {
    const input = document.getElementById(`sm-inv-input-${id}`);
    const val   = parseFloat(input?.value);
    if (!val || val <= 0) return;
    fetch(`/api/algo/swing-momentum/configs/${id}`, {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ investment: val }),
    }).then(r => r.json()).then(d => {
        if (!d.success) return;
        const chip = document.getElementById(`sm-cfg-inv-${id}`);
        if (chip) chip.textContent = _smFmtInr(val);
        // also update header chip
        const hdrChip = document.querySelector(`#sm-card-${id} .sm-live-inv-chip`);
        if (hdrChip) hdrChip.textContent = '₹' + Math.round(val).toLocaleString('en-IN', { maximumFractionDigits: 0 });
        _smToggleEditInv(id, null);
        _smLiveFetchConfigs();
    });
}


// id → { holdings: [...], broker: {...}|null } captured at render time
const _smHoldingsData = {};

// Build the SIP/SWP plan: equal-₹ split across holdings.
// For a SIP, the amount actually split = entered SIP + any idle (undeployed) cash,
// where idle cash = original investment − currently deployed value (clamped ≥ 0).
// This way the leftover base capital that never got deployed is also put to work,
// instead of splitting only the freshly entered SIP amount.
function _smFlowPlan(holdings, mode, amount, configuredInvestment = 0) {
    const n = holdings.length;
    const empty = { allocs: [], sipAmount: amount, idle: 0, deployedBase: 0, splitAmount: 0 };
    if (!n || !(amount > 0)) return empty;

    let idle = 0, deployedBase = 0;
    if (mode === 'sip') {
        // Deployed value = cost basis currently sitting in the holdings.
        deployedBase = holdings.reduce((s, h) => {
            const bv = Number(h.buy_value);
            return s + (Number.isFinite(bv) ? bv
                        : (Number(h.entry_price) || 0) * (Number(h.qty) || 0));
        }, 0);
        idle = Math.max(0, (Number(configuredInvestment) || 0) - deployedBase);
    }

    const splitAmount = amount + idle;
    const perStock    = splitAmount / n;
    const allocs = holdings.map(h => {
        const price = Number(h.current_price) || 0;
        let qty = price > 0 ? Math.floor(perStock / price) : 0;
        if (mode === 'swp') qty = Math.min(qty, Number(h.qty) || 0);
        return { symbol: h.symbol, price, qty, held: Number(h.qty) || 0,
                 entry: Number(h.entry_price) || 0 };
    });
    return { allocs, sipAmount: amount, idle, deployedBase, splitAmount };
}

function _smOpenFlowModal(id, mode) {
    // Open immediately with whatever we already have, then refresh the group's
    // data (holdings, live prices, deployed value) and recompute the split so the
    // popup always reflects the current state — not a stale snapshot.
    _smBuildFlowModal(id, mode);
    _smInvalidateCache(id);
    _smPrefetch(id, true);
    _smCache[id]?.signal.then(d => {
        if (!d || !d.success) return;
        if (!document.getElementById('smFlowModal')) return;   // popup closed meanwhile
        _smHoldingsData[id] = {
            holdings:             d.live_holdings || [],
            broker:               d.broker || (_smHoldingsData[id] || {}).broker || null,
            configuredInvestment: d.configured_investment || 0,
        };
        _smRenderFlowTable(id, mode);
    });
}

function _smBuildFlowModal(id, mode) {
    document.getElementById('smFlowModal')?.remove();
    const data     = _smHoldingsData[id] || { holdings: [], broker: null };
    const holdings = data.holdings || [];
    if (!holdings.length) { window.showNotification && window.showNotification('No holdings loaded yet', 'error'); return; }

    const isSip   = mode === 'sip';
    const title   = isSip ? '＋ SIP — Add & Buy' : '－ SWP — Withdraw & Sell';
    const accent  = isSip ? 'sm-flow-sip' : 'sm-flow-swp';
    const defAmt  = isSip ? 5000 : 5000;

    const modal = document.createElement('div');
    modal.id = 'smFlowModal';
    modal.className = 'sm-gl-overlay';
    modal.innerHTML = `
<div class="sm-gl-box ${accent}">
    <div class="sm-gl-hdr">
        <span class="sm-gl-title">${title}</span>
        <button class="sm-gl-close" onclick="document.getElementById('smFlowModal').remove()">✕</button>
    </div>
    <div class="sm-gl-body">
        <div class="sm-flow-controls">
            <label class="sm-gl-field"><span>${isSip ? 'Invest amount (₹)' : 'Withdraw amount (₹)'}</span>
                <input type="number" id="flowAmount" value="${defAmt}" step="500" min="0"></label>
        </div>
        <div class="sm-flow-table-wrap">
            <table class="sm-flow-table">
                <thead><tr>
                    <th>Symbol</th><th>Price</th><th>Held</th>
                    <th>${isSip ? 'Buy Qty' : 'Sell Qty'}</th>
                    <th>${isSip ? 'New Qty' : 'Left'}</th>
                    <th>${isSip ? 'New Avg' : 'Value'}</th>
                </tr></thead>
                <tbody id="flowTableBody"></tbody>
            </table>
        </div>
        <div class="sm-flow-summary" id="flowSummary"></div>
        <div class="sm-gl-summary" id="flowResult" style="display:none"></div>
    </div>
    <div class="sm-gl-footer">
        <button class="sm-gl-btn sm-gl-cancel" onclick="document.getElementById('smFlowModal').remove()">Cancel</button>
        <button class="sm-gl-btn ${isSip ? 'sm-gl-confirm' : 'sm-flow-confirm-swp'}" id="flowConfirmBtn">
            ${isSip ? 'Confirm & Buy' : 'Confirm & Sell'}</button>
    </div>
</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);


    const recompute = () => _smRenderFlowTable(id, mode);
    document.getElementById('flowAmount').addEventListener('input', recompute);
    document.getElementById('flowConfirmBtn').addEventListener('click', () => _smSubmitFlow(id, mode));
    recompute();
}

function _smRenderFlowTable(id, mode) {
    const data     = _smHoldingsData[id] || {};
    const holdings = data.holdings || [];
    const amount   = parseFloat(document.getElementById('flowAmount').value) || 0;
    const isSip    = mode === 'sip';
    const plan     = _smFlowPlan(holdings, mode, amount, data.configuredInvestment || 0);
    const allocs   = plan.allocs;
    const body     = document.getElementById('flowTableBody');
    const fmt      = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });

    let deployed = 0;
    body.innerHTML = allocs.map(a => {
        let detail;
        if (isSip) {
            const newQty = a.held + a.qty;
            const newAvg = newQty ? (a.held * a.entry + a.qty * a.price) / newQty : a.entry;
            deployed += a.qty * a.price;
            detail = `<td>${newQty}</td><td>₹${newAvg.toFixed(2)}</td>`;
        } else {
            const left = a.held - a.qty;
            deployed += a.qty * a.price;
            detail = `<td>${left}${left === 0 ? ' <span class="sm-flow-out">(exit)</span>' : ''}</td><td>${fmt(a.qty * a.price)}</td>`;
        }
        return `<tr>
            <td class="sm-col-sym"><strong>${a.symbol}</strong></td>
            <td>₹${a.price.toFixed(2)}</td>
            <td>${a.held}</td>
            <td class="${isSip ? 'sm-pos' : 'sm-neg'}"><strong>${a.qty}</strong></td>
            ${detail}
        </tr>`;
    }).join('');

    const splitFor   = isSip ? plan.splitAmount : amount;
    const breakdown  = (isSip && plan.idle > 0)
        ? `<span class="sm-flow-sub"> · SIP ${fmt(amount)} + idle cash ${fmt(plan.idle)} = ${fmt(plan.splitAmount)} to split</span>`
        : '';
    document.getElementById('flowSummary').innerHTML =
        `${isSip ? 'Total to deploy' : 'Total to withdraw'}: <strong>${fmt(deployed)}</strong>` +
        breakdown +
        `<span class="sm-flow-sub"> · ${allocs.filter(a => a.qty > 0).length}/${holdings.length} stocks · idle cash ${fmt(Math.max(0, splitFor - deployed))}</span>`;
}

function _smSubmitFlow(id, mode) {
    const data     = _smHoldingsData[id] || {};
    const holdings = data.holdings || [];
    const amount   = parseFloat(document.getElementById('flowAmount').value) || 0;
    if (!(amount > 0)) { window.showNotification && window.showNotification('Enter a valid amount', 'error'); return; }
    const allocs   = _smFlowPlan(holdings, mode, amount, data.configuredInvestment || 0)
                        .allocs.filter(a => a.qty > 0);
    if (!allocs.length) { window.showNotification && window.showNotification('Amount too small for any share', 'error'); return; }

    // Use this group's stored broker automatically (no picker in the popup).
    const broker = data.broker || null;

    const payload = {
        mode, amount,
        date: new Date().toISOString().split('T')[0],
        allocations: allocs.map(a => ({ symbol: a.symbol, qty: a.qty })),
    };
    if (broker && broker.instance) {
        payload.broker_instance = broker.instance;
        payload.broker_type     = broker.broker_type || '';
        payload.broker_name     = broker.broker_name || '';
    }

    const btn = document.getElementById('flowConfirmBtn');
    btn.disabled = true;
    btn.textContent = (broker && broker.instance) ? 'Placing orders…' : 'Updating…';

    fetch(`/api/algo/swing-momentum/configs/${id}/sip-swp`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
    }).then(r => r.json()).then(d => {
        if (!d.success) {
            btn.disabled = false; btn.textContent = mode === 'sip' ? 'Confirm & Buy' : 'Confirm & Sell';
            window.showNotification && window.showNotification(d.error || 'Failed', 'error');
            return;
        }
        const res = document.getElementById('flowResult');
        const bs  = d.broker_summary;
        let msg = `✅ ${mode === 'sip' ? 'Bought' : 'Sold'} ₹${Math.round(d.deployed).toLocaleString('en-IN')} across stocks.`;
        if (bs) msg += bs.placed ? ` ${bs.placed} order(s) on ${bs.broker}${bs.failed ? `, ${bs.failed} failed` : ''}.`
                                 : ` ⚠ ${bs.error || 'No orders placed'} (list updated).`;
        res.className = 'sm-gl-summary ' + (bs && !bs.placed ? 'sm-gl-summary-err' : 'sm-gl-summary-ok');
        res.style.display = 'block';
        res.textContent = msg;
        window.showNotification && window.showNotification(mode === 'sip' ? 'SIP executed' : 'SWP executed', 'success');
        setTimeout(() => { document.getElementById('smFlowModal')?.remove(); _smLiveLoadSignal(id); }, 1300);
    }).catch(() => {
        btn.disabled = false; btn.textContent = mode === 'sip' ? 'Confirm & Buy' : 'Confirm & Sell';
        window.showNotification && window.showNotification('Request failed', 'error');
    });
}

// ── Inline edit of a holding (Qty / Entry Date / Entry ₹ / Invested) ──────────

function _smEditHolding(td, id, sym, field) {
    if (td.querySelector('input')) return;   // already editing
    const h = ((_smHoldingsData[id] || {}).holdings || []).find(x => x.symbol === sym) || {};
    const cur = field === 'qty'         ? h.qty
              : field === 'entry_price' ? h.entry_price
              : field === 'entry_date'  ? h.entry_date
              : field === 'invested'    ? h.buy_value
              : '';
    const orig  = td.innerHTML;
    const input = document.createElement('input');
    input.className = 'sm-edit-input';
    if (field === 'entry_date') { input.type = 'date'; }
    else { input.type = 'number'; input.step = field === 'qty' ? '1' : '0.01'; input.min = '0'; }
    input.value = cur != null ? cur : '';
    td.innerHTML = '';
    td.appendChild(input);
    input.focus();
    if (input.select) input.select();

    let done = false;
    const finish = (save) => {
        if (done) return; done = true;
        const val = input.value;
        if (!save || val === '' || String(val) === String(cur)) { td.innerHTML = orig; return; }
        _smSaveHolding(id, sym, field, val, td, orig);
    };
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        else if (e.key === 'Escape') { finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
}

function _smSaveHolding(id, sym, field, val, td, orig) {
    fetch(`/api/algo/swing-momentum/configs/${id}/holdings/edit`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ symbol: sym, [field]: val }),
    }).then(r => r.json()).then(d => {
        if (!d.success) {
            td.innerHTML = orig;
            window.showNotification && window.showNotification(d.error || 'Update failed', 'error');
            return;
        }
        window.showNotification && window.showNotification(`${sym} updated`, 'success');
        _smLiveLoadSignal(id);   // reload so derived values (invested, P&L) refresh
    }).catch(() => {
        td.innerHTML = orig;
        window.showNotification && window.showNotification('Request failed', 'error');
    });
}

// ── Per-stock manual Buy/Sell (three-dot row menu) ────────────────────────────

function _smRowMenu(ev, id, sym) {
    ev.stopPropagation();
    document.getElementById('sm-row-menu')?.remove();
    const r = ev.currentTarget.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.id = 'sm-row-menu';
    menu.className = 'sm-row-menu';
    menu.innerHTML = `
        <button class="sm-row-menu-item sm-rm-buy"  onclick="_smOpenStockOrder('${id}','${sym}','BUY')">▲ Buy</button>
        <button class="sm-row-menu-item sm-rm-sell" onclick="_smOpenStockOrder('${id}','${sym}','SELL')">▼ Sell</button>`;
    menu.style.top  = (r.bottom + window.scrollY + 4) + 'px';
    menu.style.left = (r.right  + window.scrollX - 124) + 'px';
    document.body.appendChild(menu);
    setTimeout(() => document.addEventListener('click', _smCloseRowMenu), 0);
}

function _smCloseRowMenu(e) {
    const menu = document.getElementById('sm-row-menu');
    if (menu && !menu.contains(e.target)) {
        menu.remove();
        document.removeEventListener('click', _smCloseRowMenu);
    }
}

function _smOpenStockOrder(id, sym, side) {
    document.getElementById('sm-row-menu')?.remove();
    document.removeEventListener('click', _smCloseRowMenu);
    const data = _smHoldingsData[id] || { holdings: [], broker: null };
    const h    = (data.holdings || []).find(x => x.symbol === sym);
    if (!h) { window.showNotification && window.showNotification('Holding not found', 'error'); return; }

    const isBuy  = side === 'BUY';
    const price  = Number(h.current_price) || 0;
    const held   = Number(h.qty) || 0;
    const defQty = isBuy ? 1 : held;
    const accent = isBuy ? 'sm-flow-sip' : 'sm-flow-swp';

    document.getElementById('smStockOrderModal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'smStockOrderModal';
    modal.className = 'sm-gl-overlay';
    modal.innerHTML = `
<div class="sm-gl-box sm-gl-narrow ${accent}">
    <div class="sm-gl-hdr">
        <span class="sm-gl-title">${isBuy ? '▲ Buy' : '▼ Sell'} &mdash; ${sym}</span>
        <button class="sm-gl-close" onclick="document.getElementById('smStockOrderModal').remove()">✕</button>
    </div>
    <div class="sm-gl-body">
        <div class="sm-so-meta">
            <span>Price <strong>₹${price.toFixed(2)}</strong></span>
            <span>Held <strong>${held}</strong></span>
        </div>
        <div class="sm-flow-controls">
            <label class="sm-gl-field"><span>${isBuy ? 'Buy quantity' : 'Sell quantity'}</span>
                <input type="number" id="soQty" value="${defQty}" step="1" min="1" ${isBuy ? '' : `max="${held}"`}></label>
        </div>
        <div class="sm-so-est" id="soEst"></div>
        <div class="sm-gl-summary" id="soResult" style="display:none"></div>
    </div>
    <div class="sm-gl-footer">
        <button class="sm-gl-btn sm-gl-cancel" onclick="document.getElementById('smStockOrderModal').remove()">Cancel</button>
        <button class="sm-gl-btn ${isBuy ? 'sm-gl-confirm' : 'sm-flow-confirm-swp'}" id="soConfirmBtn">
            ${isBuy ? 'Confirm & Buy' : 'Confirm & Sell'}</button>
    </div>
</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);

    const est = () => {
        const q = parseInt(document.getElementById('soQty').value) || 0;
        document.getElementById('soEst').innerHTML =
            `Order value: <strong>₹${(q * price).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</strong>` +
            (isBuy ? '' : ` · ${Math.max(0, held - q)} left${held - q <= 0 ? ' (exit)' : ''}`);
    };
    document.getElementById('soQty').addEventListener('input', est);
    document.getElementById('soConfirmBtn').addEventListener('click', () => _smSubmitStockOrder(id, sym, side, price));
    est();
}

function _smSubmitStockOrder(id, sym, side, price) {
    const isBuy = side === 'BUY';
    const data  = _smHoldingsData[id] || {};
    const h     = (data.holdings || []).find(x => x.symbol === sym) || {};
    const held  = Number(h.qty) || 0;
    let qty = parseInt(document.getElementById('soQty').value) || 0;
    if (!(qty > 0)) { window.showNotification && window.showNotification('Enter a valid quantity', 'error'); return; }
    if (!isBuy && qty > held) qty = held;

    // Use this group's stored broker automatically (no picker in the popup).
    const broker = data.broker || null;

    const payload = {
        mode: isBuy ? 'sip' : 'swp',
        amount: qty * (Number(price) || 0),
        note: `Manual ${side} ${sym}`,
        allocations: [{ symbol: sym, qty }],
    };
    if (broker && broker.instance) {
        payload.broker_instance = broker.instance;
        payload.broker_type     = broker.broker_type || '';
        payload.broker_name     = broker.broker_name || '';
    }

    const btn = document.getElementById('soConfirmBtn');
    btn.disabled = true;
    btn.textContent = (broker && broker.instance) ? 'Placing order…' : 'Updating…';

    fetch(`/api/algo/swing-momentum/configs/${id}/sip-swp`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
    }).then(r => r.json()).then(d => {
        if (!d.success) {
            btn.disabled = false; btn.textContent = isBuy ? 'Confirm & Buy' : 'Confirm & Sell';
            window.showNotification && window.showNotification(d.error || 'Failed', 'error');
            return;
        }
        const res = document.getElementById('soResult');
        const bs  = d.broker_summary;
        let msg = `✅ ${isBuy ? 'Bought' : 'Sold'} ${qty} ${sym}.`;
        if (bs) msg += bs.placed ? ` Order placed on ${bs.broker}.`
                                 : ` ⚠ ${bs.error || 'No order placed'} (list updated).`;
        res.className = 'sm-gl-summary ' + (bs && !bs.placed ? 'sm-gl-summary-err' : 'sm-gl-summary-ok');
        res.style.display = 'block';
        res.textContent = msg;
        window.showNotification && window.showNotification(`${side} executed`, 'success');
        setTimeout(() => { document.getElementById('smStockOrderModal')?.remove(); _smLiveLoadSignal(id); }, 1300);
    }).catch(() => {
        btn.disabled = false; btn.textContent = isBuy ? 'Confirm & Buy' : 'Confirm & Sell';
        window.showNotification && window.showNotification('Request failed', 'error');
    });
}

// ── Place Orders to broker (Live Algo screen) ─────────────────────────────────

function _smOpenPlaceOrders(id) {
    const data     = _smHoldingsData[id] || { holdings: [], broker: null };
    const holdings = data.holdings || [];
    if (!holdings.length) { window.showNotification && window.showNotification('No holdings to place', 'error'); return; }

    const pending = holdings.filter(h => !h.ordered);
    const already = holdings.length - pending.length;

    const cfgBroker   = data.broker || null;
    const brokerLabel = cfgBroker
        ? `${cfgBroker.broker_name || cfgBroker.broker_type} (${(cfgBroker.broker_type || '').toUpperCase()})`
        : '⚠ No broker set for this group — assign one at Go Live';

    document.getElementById('smPlaceModal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'smPlaceModal';
    modal.className = 'sm-gl-overlay';
    modal.innerHTML = `
<div class="sm-gl-box sm-flow-sip">
    <div class="sm-gl-hdr">
        <span class="sm-gl-title">🛒 Place Orders — Broker</span>
        <button class="sm-gl-close" onclick="document.getElementById('smPlaceModal').remove()">✕</button>
    </div>
    <div class="sm-gl-body">
        <div class="sm-po-broker"><span>Broker</span><strong>${brokerLabel}</strong></div>
        <div class="sm-flow-table-wrap">
            <table class="sm-flow-table">
                <thead><tr><th>Symbol</th><th>Order Qty</th><th>Price</th><th>Order Value</th><th>Status</th></tr></thead>
                <tbody id="poTableBody"></tbody>
            </table>
        </div>
        <div class="sm-flow-summary" id="poSummary"></div>
        <label class="sm-po-force" style="display:${already ? 'flex' : 'none'}">
            <input type="checkbox" id="poForce"> Re-place the ${already} holding(s) that already have orders
        </label>
        <div class="sm-gl-summary" id="poResult" style="display:none"></div>
    </div>
    <div class="sm-gl-footer">
        <button class="sm-gl-btn sm-gl-cancel" onclick="document.getElementById('smPlaceModal').remove()">Cancel</button>
        <button class="sm-gl-btn sm-gl-confirm" id="poConfirmBtn">Confirm & Place Orders</button>
    </div>
</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);

    const fmt = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    const renderTable = () => {
        const force = document.getElementById('poForce')?.checked;
        const rows  = force ? holdings : pending;
        let total = 0;
        document.getElementById('poTableBody').innerHTML = holdings.map(h => {
            const willPlace = force || !h.ordered;
            if (willPlace) total += (Number(h.qty) || 0) * (Number(h.current_price) || 0);
            const status = h.ordered
                ? `<span class="sm-po-badge sm-po-done">${h.order_status || 'placed'}</span>`
                : (willPlace ? '<span class="sm-po-badge sm-po-new">will place</span>' : '');
            return `<tr class="${willPlace ? '' : 'sm-po-skip'}">
                <td class="sm-col-sym"><strong>${h.symbol}</strong></td>
                <td><strong>${h.qty}</strong></td>
                <td>₹${Number(h.current_price).toFixed(2)}</td>
                <td>${fmt((Number(h.qty) || 0) * (Number(h.current_price) || 0))}</td>
                <td>${status}</td>
            </tr>`;
        }).join('');
        const count = (force ? holdings : pending).length;
        document.getElementById('poSummary').innerHTML =
            `Placing <strong>${count}</strong> CNC MARKET order(s) · est. <strong>${fmt(total)}</strong>` +
            (already && !force ? ` · <span class="sm-flow-sub">${already} already placed (skipped)</span>` : '');
    };

    document.getElementById('poForce')?.addEventListener('change', renderTable);
    document.getElementById('poConfirmBtn').addEventListener('click', () => _smSubmitPlaceOrders(id));
    renderTable();
}

function _smSubmitPlaceOrders(id) {
    // Use this group's stored broker automatically (no picker in the popup).
    const broker = (_smHoldingsData[id] || {}).broker || null;
    if (!broker || !broker.instance) {
        window.showNotification && window.showNotification('No broker set for this group. Assign one at Go Live.', 'error');
        return;
    }

    const payload = {
        broker_instance: broker.instance,
        broker_type:     broker.broker_type || '',
        broker_name:     broker.broker_name || '',
        force:           !!document.getElementById('poForce')?.checked,
    };

    const btn = document.getElementById('poConfirmBtn');
    btn.disabled = true;
    btn.textContent = 'Placing orders…';

    fetch(`/api/algo/swing-momentum/configs/${id}/place-orders`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
    }).then(r => r.json()).then(d => {
        if (!d.success) {
            btn.disabled = false; btn.textContent = 'Confirm & Place Orders';
            window.showNotification && window.showNotification(d.error || 'Failed', 'error');
            return;
        }
        const res = document.getElementById('poResult');
        const bs  = d.broker_summary || {};
        const ok  = bs.placed > 0;
        res.className = 'sm-gl-summary ' + (ok ? 'sm-gl-summary-ok' : 'sm-gl-summary-err');
        res.style.display = 'block';
        res.textContent = ok
            ? `✅ Placed ${bs.placed} order(s) on ${bs.broker || 'broker'}` +
              (bs.failed ? ` · ⚠ ${bs.failed} failed` : '') + '. Entry prices updated from fills.'
            : `⚠ ${bs.error || 'No orders placed'}`;
        window.showNotification && window.showNotification(ok ? 'Orders placed' : 'Placement failed', ok ? 'success' : 'error');
        setTimeout(() => { document.getElementById('smPlaceModal')?.remove(); _smLiveLoadSignal(id); }, 1600);
    }).catch(() => {
        btn.disabled = false; btn.textContent = 'Confirm & Place Orders';
        window.showNotification && window.showNotification('Request failed', 'error');
    });
}

// ── Shared: rebalance preview section ─────────────────────────────────────────

function _smRebalPreviewHtml(d, id) {
    const sellList = d.sell_preview || [];
    const buyList  = d.buy_preview  || [];

    const statusCls  = d.rebalance_needed ? 'sm-rebal-status-due' : 'sm-rebal-status-ok';
    const statusTxt  = d.rebalance_needed
        ? `${sellList.length} sell · ${buyList.length} buy`
        : 'No changes expected';

    const sellHtml = sellList.length
        ? sellList.map(s => {
            const sc = s.score != null ? (s.score >= 0 ? '+' : '') + Number(s.score).toFixed(1) + '%' : '—';
            return `<div class="sm-rebal-row sm-rebal-row-sell">
                <span class="sm-rebal-row-arrow">↓</span>
                <span class="sm-rebal-row-sym">${s.symbol}</span>
                <span class="sm-rank-pill sm-rank-pill-sell">${s.current_rank}</span>
                <span class="sm-rebal-row-score sm-neg">${sc}</span>
                <span class="sm-rebal-row-detail">${s.qty} shares</span>
            </div>`;
          }).join('')
        : '<div class="sm-no-action">Nothing to sell</div>';

    const buyHtml = buyList.length
        ? buyList.map(b => {
            const sc = b.score != null ? (b.score >= 0 ? '+' : '') + Number(b.score).toFixed(1) + '%' : '—';
            return `<div class="sm-rebal-row sm-rebal-row-buy">
                <span class="sm-rebal-row-arrow">↑</span>
                <span class="sm-rebal-row-sym">${b.symbol}</span>
                <span class="sm-rank-pill sm-rank-pill-buy">${b.current_rank}</span>
                <span class="sm-rebal-row-score sm-pos">${sc}</span>
                <span class="sm-rebal-row-detail">₹${Number(b.price).toFixed(0)}</span>
            </div>`;
          }).join('')
        : '<div class="sm-no-action">No new entries</div>';

    return `
<div class="sm-rebal-section">
    <div class="sm-rebal-header">
        <div class="sm-rebal-header-left">
            <span class="sm-rebal-header-title">Next Rebalance Preview</span>
            <span class="sm-rebal-header-date">${d.next_rebalance || '—'}</span>
        </div>
        <div class="sm-rebal-header-right">
            <span class="sm-rebal-status ${statusCls}">${statusTxt}</span>
            ${d.rebalance_needed && id ? `
            <button class="ag-btn ag-btn-enter sm-rebal-exec-btn" onclick="_smOpenRebalance('${id}')">
                ⚖ Rebalance
            </button>` : ''}
        </div>
    </div>
    <div class="sm-rebal-grid">
        <div class="sm-rebal-col sm-rebal-col-sell">
            <div class="sm-rebal-col-hdr sm-sell-hdr">
                <span class="sm-rebal-col-icon">↓</span> SELL
                ${sellList.length ? `<span class="sm-rebal-col-count">${sellList.length}</span>` : ''}
            </div>
            <div class="sm-action-list">${sellHtml}</div>
        </div>
        <div class="sm-rebal-col sm-rebal-col-buy">
            <div class="sm-rebal-col-hdr sm-buy-hdr">
                <span class="sm-rebal-col-icon">↑</span> BUY
                ${buyList.length ? `<span class="sm-rebal-col-count">${buyList.length}</span>` : ''}
            </div>
            <div class="sm-action-list">${buyHtml}</div>
        </div>
    </div>
</div>`;
}


function _smOpenRebalance(id) {
    document.getElementById('smRebalModal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'smRebalModal';
    modal.className = 'sm-gl-overlay';
    modal.innerHTML = `
<div class="sm-gl-box">
    <div class="sm-gl-hdr">
        <span class="sm-gl-title">⚖ Rebalance — Review</span>
        <button class="sm-gl-close" onclick="document.getElementById('smRebalModal').remove()">✕</button>
    </div>
    <div class="sm-gl-body">
        <div id="rbBody" class="sm-signal-loading" style="padding:16px 0">Computing rebalance…</div>
        <div class="sm-gl-summary" id="rbResult" style="display:none"></div>
    </div>
    <div class="sm-gl-footer">
        <button class="sm-gl-btn sm-gl-cancel" onclick="document.getElementById('smRebalModal').remove()">Cancel</button>
        <button class="sm-gl-btn sm-gl-confirm" id="rbConfirmBtn" disabled>Confirm &amp; Place Orders</button>
    </div>
</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);

    const fmt = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });

    fetch(`/api/algo/swing-momentum/configs/${id}/rebalance/preview`)
        .then(r => r.json()).then(d => {
            const body = document.getElementById('rbBody');
            if (!d.success) { body.innerHTML = `<div class="ag-empty">⚠ ${d.error || 'Failed'}</div>`; return; }
            const sells = d.sells || [], buys = d.buys || [];
            if (!sells.length) { body.innerHTML = '<div class="ag-empty">No holdings past exit rank — nothing to rebalance.</div>'; return; }

            const sellRows = sells.map(s => `<tr>
                <td class="sm-col-sym"><strong>${s.symbol}</strong></td>
                <td>${s.qty}</td><td>₹${Number(s.price).toFixed(2)}</td>
                <td>${fmt(s.value)}</td><td>#${s.current_rank ?? '—'}</td></tr>`).join('');
            const buyRows = buys.length ? buys.map(b => `<tr>
                <td class="sm-col-sym"><strong>${b.symbol}</strong></td>
                <td>${b.qty}</td><td>₹${Number(b.price).toFixed(2)}</td>
                <td>${fmt(b.value)}</td><td>#${b.current_rank ?? '—'}</td></tr>`).join('')
                : '<tr><td colspan="5" style="text-align:center;color:var(--ag-text-3)">No replacements</td></tr>';

            body.innerHTML = `
                <div class="sm-rb-tag sm-rb-tag-sell">↓ SELL ${sells.length} — proceeds ${fmt(d.proceeds)}</div>
                <div class="sm-flow-table-wrap" style="margin-bottom:10px">
                    <table class="sm-flow-table"><thead><tr>
                        <th>Symbol</th><th>Qty</th><th>Price</th><th>Value</th><th>Rank</th>
                    </tr></thead><tbody>${sellRows}</tbody></table>
                </div>
                <div class="sm-rb-tag sm-rb-tag-buy">↑ BUY ${buys.length} — deploy ${fmt(d.deploy)}</div>
                <div class="sm-flow-table-wrap">
                    <table class="sm-flow-table"><thead><tr>
                        <th>Symbol</th><th>Qty</th><th>Price</th><th>Value</th><th>Rank</th>
                    </tr></thead><tbody>${buyRows}</tbody></table>
                </div>
                <div class="sm-flow-summary" style="margin-top:10px">
                    Broker: <strong>${d.broker ? (d.broker.broker_name || d.broker.broker_type) : '⚠ none set'}</strong>
                </div>`;
            const btn = document.getElementById('rbConfirmBtn');
            if (d.broker && d.broker.instance) btn.disabled = false;
            else { btn.disabled = true; btn.title = 'Assign a broker via Place Orders first'; }
            btn.onclick = () => _smSubmitRebalance(id);
        })
        .catch(() => { document.getElementById('rbBody').innerHTML = '<div class="ag-empty">Request failed</div>'; });
}

function _smSubmitRebalance(id) {
    const btn = document.getElementById('rbConfirmBtn');
    btn.disabled = true; btn.textContent = 'Placing orders…';
    fetch(`/api/algo/swing-momentum/configs/${id}/rebalance`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    }).then(r => r.json()).then(d => {
        const res = document.getElementById('rbResult');
        if (!d.success) {
            btn.disabled = false; btn.textContent = 'Confirm & Place Orders';
            window.showNotification && window.showNotification(d.error || 'Failed', 'error');
            return;
        }
        const s = d.summary || {};
        const ok = (s.sold || s.bought);
        res.className = 'sm-gl-summary ' + (s.failed && !ok ? 'sm-gl-summary-err' : 'sm-gl-summary-ok');
        res.style.display = 'block';
        res.textContent = `✅ Sold ${s.sold || 0}, bought ${s.bought || 0}`
            + (s.failed ? ` · ⚠ ${s.failed} failed` : '') + '. Holdings updated.';
        window.showNotification && window.showNotification('Rebalance executed', 'success');
        setTimeout(() => { document.getElementById('smRebalModal')?.remove(); _smLiveLoadSignal(id); }, 1600);
    }).catch(() => {
        btn.disabled = false; btn.textContent = 'Confirm & Place Orders';
        window.showNotification && window.showNotification('Request failed', 'error');
    });
}

function _smOpenExitHistory(id) {
    document.getElementById('smExitModal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'smExitModal';
    modal.className = 'sm-gl-overlay';
    modal.innerHTML = `
<div class="sm-gl-box sm-gl-wide">
    <div class="sm-gl-hdr">
        <span class="sm-gl-title">📕 Order History — Exited Stocks</span>
        <button class="sm-gl-close" onclick="document.getElementById('smExitModal').remove()">✕</button>
    </div>
    <div class="sm-gl-body">
        <div id="ehBody" class="sm-signal-loading" style="padding:16px 0">Loading…</div>
    </div>
</div>`;
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);

    const fmt = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    fetch(`/api/algo/swing-momentum/configs/${id}/exit-history`).then(r => r.json()).then(d => {
        const body = document.getElementById('ehBody');
        if (!d.success) { body.innerHTML = `<div class="ag-empty">⚠ ${d.error || 'Failed'}</div>`; return; }
        const exits = d.exits || [];
        if (!exits.length) { body.innerHTML = '<div class="ag-empty">No exited stocks yet.</div>'; return; }
        const rows = [...exits].reverse().map(x => {
            const cls  = x.pnl >= 0 ? 'sm-pos' : 'sm-neg';
            const sign = x.pnl >= 0 ? '+₹' : '-₹';
            const pct  = (x.pnl_pct >= 0 ? '+' : '') + Number(x.pnl_pct).toFixed(1) + '%';
            return `<tr>
                <td class="sm-col-sym"><strong>${x.symbol}</strong></td>
                <td>${x.qty}</td>
                <td>${x.entry_date || '—'}</td>
                <td>${x.exit_date || '—'}</td>
                <td>₹${Number(x.entry_price).toFixed(2)}</td>
                <td>₹${Number(x.exit_price).toFixed(2)}</td>
                <td>${fmt(x.invested)}</td>
                <td>${fmt(x.final_value)}</td>
                <td class="${cls}">${sign}${Math.abs(x.pnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })} <span class="sm-cell-pct">(${pct})</span></td>
            </tr>`;
        }).join('');
        const rcls = d.realized_pnl >= 0 ? 'sm-pos' : 'sm-neg';
        body.innerHTML = `
            <div class="sm-flow-summary" style="margin-bottom:8px">
                ${exits.length} exit(s) · Realized P&amp;L:
                <strong class="${rcls}">${d.realized_pnl >= 0 ? '+₹' : '-₹'}${Math.abs(d.realized_pnl).toLocaleString('en-IN')}</strong>
            </div>
            <div class="sm-flow-table-wrap" style="max-height:340px">
                <table class="sm-flow-table"><thead><tr>
                    <th>Symbol</th><th>Qty</th><th>Entry Date</th><th>Exit Date</th>
                    <th>Entry ₹</th><th>Exit ₹</th><th>Invested</th><th>Final</th><th>P&amp;L</th>
                </tr></thead><tbody>${rows}</tbody></table>
            </div>`;
    }).catch(() => { document.getElementById('ehBody').innerHTML = '<div class="ag-empty">Request failed</div>'; });
}

function _smFmtInr(v) {
    if (v == null) return '—';
    return '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}
