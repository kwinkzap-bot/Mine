/* algo_ema_confluence.js — EMA Confluence Breakout Algo tab: own file, separate
   from algo.js (which drives every other Live Algo tab) and separate from
   backtest.js (the Backtest page). algo.js only calls the small hooks
   _emacFetchStatus, _emacFetchHistory, _emacClearTimers, and the
   typeof-guarded call in algoSwitch — everything else about this tab
   (rendering, start/stop, delete) lives in this file. PAPER TRADE ONLY. */
'use strict';

let _emacStatusTimer  = null;
let _emacHistoryTimer = null;
let _emacStocksData   = {};   // last-fetched {symbol: {phase, direction, ...}}
let _emacDefaults     = {};   // EMA_SYMBOL_DEFAULTS: {symbol: {direction, target_pct}}
let _emacStartingEquity = 100000;  // equity-curve baseline (lot-sized paper P&L, not a real capital pool)
let _emacHistoryTrades  = [];      // last-fetched trade list, kept for period-tab redraws

const _EMAC_PHASE_LABEL = {
    pending_scan: 'Not scanned yet',
    no_setup:     'No setup',
    watching:     'Watching',
    in_position:  'In position',
};
const _EMAC_PHASE_TONE = {
    pending_scan: 'neutral',
    no_setup:     'neutral',
    watching:     'warn',
    in_position:  'pos',
};
// Phases hidden by default (the bulk of the universe before a setup is
// found) — the "Show all N symbols" checkbox reveals them. N comes from the
// status payload's universe_count, so growing/shrinking EMA_SYMBOL_DEFAULTS
// never leaves a stale number baked into the page.
const _EMAC_QUIET_PHASES = new Set(['pending_scan', 'no_setup']);

// 'long'/'short'/'both' as the user writes them in the symbol table.
const _EMAC_CFG_DIR_LABEL = { long: 'BUY Only', short: 'Sell Only', both: 'Both' };
const _EMAC_CFG_DIR_TONE  = { long: 'pos', short: 'neg' };

// Local (IST) date as YYYY-MM-DD — matched against the local-time ISO
// timestamps the algo writes, so toISOString()'s UTC shift is not usable here.
function _emacTodayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// A token the algo couldn't parse a contract month out of still says more than
// a dash — show it without its exchange prefix.
function _emacFutureFallback(token) {
    return token ? String(token).split(':').pop() : '—';
}

function _emacClearTimers() {
    clearTimeout(_emacStatusTimer);
    clearTimeout(_emacHistoryTimer);
}

// ── Status (summary + per-symbol) ───────────────────────────────────────────

function _emacFetchStatus() {
    fetch('/api/algo/ema-confluence/status')
        .then(r => r.json())
        .then(data => {
            _emacRenderStatus(data);
            clearTimeout(_emacStatusTimer);
            _emacStatusTimer = setTimeout(_emacFetchStatus, 15000);
        })
        .catch(() => {
            clearTimeout(_emacStatusTimer);
            _emacStatusTimer = setTimeout(_emacFetchStatus, 20000);
        });
}

function _emacRenderStatus(data) {
    if (!data || !data.success) return;

    const runBadge = document.getElementById('emacRunBadge');
    const runText  = document.getElementById('emacRunBadgeText');
    if (runBadge && runText) {
        runBadge.className = 'ag-badge ' + (data.running ? 'active' : 'inactive');
        // `enabled === false` means the user clicked Stop: the scheduler will
        // leave it alone until Start. Plain "Stopped" means it is still armed
        // and simply outside market hours (or between restarts).
        runText.textContent = data.running ? 'Running'
                            : (data.enabled === false ? 'Stopped (manual)' : 'Stopped');
    }

    const upd = document.getElementById('emacLastUpd');
    if (upd) upd.textContent = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const summary = data.summary || {};
    const summaryGrid = document.getElementById('emacSummaryGrid');
    if (summaryGrid) {
        // Money at risk right now, from the open paper legs only. Investment is
        // what the entries were marked at, Current Value the same lots at the
        // latest tick — for a Short the two move apart in the opposite direction
        // to the P&L, so Open P&L is taken from the algo's own unrealised figure
        // rather than derived from the difference.
        const openLegs = Object.values(data.stocks || {}).filter(s => s && s.phase === 'in_position');
        let invested = 0, curVal = 0, openPnl = 0;
        openLegs.forEach(s => {
            const qty = Number(s.qty) || 0;
            const ent = Number(s.entry_price);
            const ltp = Number(s.ltp);
            if (qty && isFinite(ent)) invested += ent * qty;
            if (qty) curVal += (isFinite(ltp) ? ltp : (isFinite(ent) ? ent : 0)) * qty;
            openPnl += Number(s.unrealized_pnl) || 0;
        });
        const rupee  = v => '₹' + Math.round(v).toLocaleString('en-IN');
        const signed = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');

        const tiles = [
            { label: 'Watching',    value: summary.watching || 0 },
            { label: 'In Position', value: summary.in_position || 0, cls: (summary.in_position || 0) > 0 ? 'ag-warn' : '' },
            { label: 'No Setup',    value: summary.no_setup || 0 },
            { label: 'Not Scanned', value: summary.pending_scan || 0 },
            { label: 'Investment',  value: openLegs.length ? rupee(invested) : '—' },
            { label: 'Current Value', value: openLegs.length ? rupee(curVal) : '—' },
            { label: 'Open P&L',    value: openLegs.length ? signed(openPnl) : '—',
              cls: openLegs.length ? (openPnl >= 0 ? 'ag-pos' : 'ag-neg') : '' },
            { label: 'Lots',        value: data.lots || 1 },
            { label: 'Last Scan',   value: data.last_scan_date || '—' },
        ];
        summaryGrid.innerHTML = tiles.map(t =>
            `<div class="ag-stat">
                <span class="ag-stat-label">${t.label}</span>
                <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
            </div>`
        ).join('');
    }

    _emacStocksData = data.stocks || {};
    _emacDefaults   = data.defaults || {};

    const uniCount = document.getElementById('emacUniverseCount');
    if (uniCount) uniCount.textContent = data.universe_count ?? Object.keys(_emacDefaults).length;

    _emacRenderStocks();
}

function _emacRenderStocks() {
    const body = document.getElementById('emacStocksBody');
    if (!body) return;
    const showAll = document.getElementById('emacShowAllStocks')?.checked;

    // Join each symbol's configured Direction/Target% (EMA_SYMBOL_DEFAULTS)
    // onto its live state. A symbol dropped from the table but held open to
    // its exit has no config left — those render as '—'.
    const todayStr = _emacTodayStr();
    let rows = Object.entries(_emacStocksData).map(([symbol, s]) => {
        const cfg   = _emacDefaults[symbol] || {};
        const open  = s.phase === 'in_position';
        const armed = s.phase === 'watching';
        // A symbol that has been in and out keeps showing that round trip
        // instead of blanking out the moment it closes — but only while it has
        // no live setup of its own. Once it re-arms, the row describes the NEW
        // setup; mixing the finished trade's entry/P&L into it would read as
        // one trade when it is two (the closed one is in the history grid).
        const showLast = !open && !armed;
        return {
            symbol, ...s,
            cfg_direction: cfg.direction, cfg_target_pct: cfg.target_pct,
            show_last:        showLast,
            entry_time_disp:  s.entry_time || (showLast ? s.last_entry_time : null),
            exit_time_disp:   open ? null : (showLast ? s.last_exit_time : null),
            entry_price_disp: s.entry_price ?? (showLast ? s.last_entry_price : null),
            // Live mark while the future is being tracked; on a finished row,
            // the price the trade was closed at — so entry, value and P&L
            // there still reconcile with each other.
            value_disp: s.ltp ?? (showLast ? s.last_exit_price : null),
            // What the "Current P&L" column reads: unrealised while the paper
            // position is open, the realised figure once it has closed.
            live_pnl: open ? s.unrealized_pnl : (showLast ? s.last_pnl : null),
            closed_today: !!(s.last_exit_time && String(s.last_exit_time).slice(0, 10) === todayStr),
        };
    });
    if (!showAll) {
        // Today's closed paper trades stay visible even though the symbol has
        // already fallen back to a quiet phase — otherwise a round trip would
        // vanish from view the instant it completed.
        rows = rows.filter(r => !_EMAC_QUIET_PHASES.has(r.phase) || r.closed_today);
    }
    // Most "active" phases first, alphabetical within a phase.
    const order = { in_position: 0, watching: 1, no_setup: 2, pending_scan: 3 };
    rows.sort((a, b) => (order[a.phase] ?? 9) - (order[b.phase] ?? 9) || a.symbol.localeCompare(b.symbol));

    body.innerHTML = DataGrid.render({
        rows,
        empty: showAll ? 'No symbols yet' : 'No symbol has a signal today yet — waiting for the daily scan, or check "Show all"',
        columns: [
            { key: 'symbol', label: 'Symbol', strong: true },
            { key: 'cfg_direction', label: 'Allowed',
              format: v => _EMAC_CFG_DIR_LABEL[v] || '—',
              tone: v => _EMAC_CFG_DIR_TONE[v] },
            { key: 'cfg_target_pct', label: 'Target %', align: 'right',
              format: v => v == null ? '—' : Number(v) + '%' },
            // A row that closed a paper trade today has fallen back to a quiet
            // phase, but "No setup" alone would make its entry/exit/P&L cells
            // look unexplained — say the trade closed, and how.
            { key: 'phase', label: 'Status',
              badge: (v, r) => r.closed_today && r.show_last
                  ? (Number(r.last_pnl) >= 0 ? 'pos' : 'neg')
                  : (_EMAC_PHASE_TONE[v] || 'neutral'),
              format: (v, r) => r.closed_today && r.show_last
                  ? 'Closed' + (r.last_exit_reason ? ' · ' + r.last_exit_reason : '')
                  : (_EMAC_PHASE_LABEL[v] || v) },
            { key: 'direction', label: 'Signal',
              format: v => v ? (v === 'Short' ? 'SELL' : 'BUY') : '—',
              tone: v => v === 'Short' ? 'neg' : (v === 'Long' ? 'pos' : undefined) },
            // Which monthly contract the paper trade is on — resolved by the
            // algo, so it's the same future the fills are marked against.
            { key: 'future_month', label: 'Future',
              format: (v, r) => v || _emacFutureFallback(r.future_token),
              // A month changing mid-trade is a contract roll, not a new
              // trade — say so, otherwise the column looks like it drifted.
              title: (_, r) => r.roll_count
                  ? `rolled ${r.roll_count}× · from ${r.rolled_from || '—'}`
                  : (r.future_expiry ? `expires ${r.future_expiry}` : undefined) },
            { key: 'trigger_level', label: 'Trigger', format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'sl_level', label: 'SL', format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'entry_price_disp', label: 'Entry', format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'target_level', label: 'Target', format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'value_disp', label: 'Current Value', align: 'right',
              format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'live_pnl', label: 'Current P&L', align: 'right', strong: true,
              format: v => v == null ? '—' : DataGrid.inr(v), tone: DataGrid.sign },
            { key: 'qty', label: 'Qty', align: 'right', format: v => v ?? '—' },
            { key: 'entry_time_disp', label: 'Entry Time', format: v => v ? _emacFmtDateTime(v) : '—' },
            { key: 'exit_time_disp', label: 'Exit Time',
              format: (v, r) => v ? _emacFmtDateTime(v) : (r.phase === 'in_position' ? 'Open' : '—'),
              tone: (v, r) => r.phase === 'in_position' ? 'warn' : undefined },
            { key: 'signal_date', label: 'Signal Candle', format: v => v || '—' },
        ],
    });
}

// ── History ──────────────────────────────────────────────────────────────────

function _emacFetchHistory() {
    fetch('/api/algo/ema-confluence/history')
        .then(r => r.json())
        .then(data => {
            _emacRenderHistory(data.trades || []);
            clearTimeout(_emacHistoryTimer);
            _emacHistoryTimer = setTimeout(_emacFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_emacHistoryTimer);
            _emacHistoryTimer = setTimeout(_emacFetchHistory, 30000);
        });
}

const _EMAC_REASON_TONE = {
    'TARGET': 'pos',
    'SL':     'neg',
    // Not a win or a loss — the leg was closed only because its contract was
    // about to expire, and the same trade reopened on the next month.
    'ROLL':   'warn',
};

// ── Brokerage — flat per round trip ─────────────────────────────────────────
// These are paper fills on a futures contract, so the P&L is only meaningful
// net of what the round trip would cost. This logic uses a flat default of
// ₹1,000 per completed trade (entry + exit) rather than the itemised Zerodha
// slab in algo_charges.js — a deliberately conservative single number that
// stands in for brokerage plus statutory charges.
const _EMAC_BROKERAGE_PER_TRADE = 1000;

function _emacCharges(t) {
    return _EMAC_BROKERAGE_PER_TRADE;
}

// What the trade is worth after brokerage — used by the grid, the equity
// curve, the P&L breakdown and the summary alike.
function _emacNetPnl(t) {
    return (Number(t.pnl) || 0) - _emacCharges(t);
}

function _emacPnlTip(t) {
    const chg = _emacCharges(t);
    if (!chg) return '';
    return `Gross ${DataGrid.inr(t.pnl)} − brokerage ₹${chg.toFixed(2)} ` +
           `(flat per round trip)`;
}

function _emacRenderHistory(trades) {
    _emacHistoryTrades = trades || [];
    _emacRenderSummary(_emacHistoryTrades);
    _emacRenderEquityCurve(_emacHistoryTrades);
    const activePeriod = document.querySelector('#emacPeriodTabs .period-tab.active')?.dataset.period || 'monthly';
    _emacRenderPeriodBreakdown(_emacHistoryTrades, activePeriod);

    const countEl = document.getElementById('emacHistCount');
    const body    = document.getElementById('emacHistBody');
    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';
    if (!body) return;

    body.innerHTML = DataGrid.render({
        rows: trades,
        empty: 'No completed trades',
        columns: [
            { key: 'date', label: 'Date' },
            { key: 'symbol', label: 'Symbol', strong: true },
            { key: 'direction', label: 'Direction', tone: v => v === 'SELL' ? 'neg' : 'pos' },
            { key: 'entry_time', label: 'Entry Time', format: v => v ? _emacFmtDateTime(v) : '—' },
            { key: 'exit_time', label: 'Exit Time', format: v => v ? _emacFmtDateTime(v) : '—' },
            { key: 'qty', label: 'Qty', align: 'right' },
            { key: 'entry_price', label: 'Entry', format: DataGrid.rupees },
            { key: 'exit_price', label: 'Exit', format: DataGrid.rupees },
            { key: 'sl_price', label: 'SL', format: v => v == null ? '—' : DataGrid.rupees(v) },
            { key: 'target_price', label: 'Target', format: v => v == null ? '—' : DataGrid.rupees(v) },
            // Net of brokerage, with the brokerage in brackets after it —
            // no separate brokerage column.
            { key: 'pnl', label: 'NET P&L (Bro)', strong: true,
              format: (_, t) => {
                  const chg = _emacCharges(t);
                  return DataGrid.inr(_emacNetPnl(t)) +
                         (chg ? ` (-₹${Math.round(chg).toLocaleString('en-IN')})` : '');
              },
              tone:  (_, t) => DataGrid.sign(_emacNetPnl(t)),
              title: (_, t) => _emacPnlTip(t) },
            { key: 'reason', label: 'Reason', badge: v => _EMAC_REASON_TONE[v] || 'neutral' },
            { label: '', cellClass: 'ag-hist-td-del',
              render: (_, t) => `<button class="ag-hist-del-btn" title="Delete record"` +
                  ` onclick="_emacDeleteTrade('${(t.entry_time || '').replace(/"/g, '&quot;')}')">&#128465;</button>` },
        ],
    });
}

// ── Trade History Summary ────────────────────────────────────────────────────
// Aggregates the rows the Executed Trade History grid shows, on the same
// net-of-brokerage basis — so Wins / Win Rate / Profit Factor describe money
// kept, not price moves.

function _emacRenderSummary(trades) {
    const card = document.getElementById('emacPerfCard');
    if (!card) return;
    const done = (trades || []).filter(t => t.pnl != null);
    if (!done.length) { card.style.display = 'none'; return; }
    card.style.display = '';

    let wins = 0, losses = 0, winSum = 0, lossSum = 0;
    let gross = 0, charges = 0, net = 0;
    let best = -Infinity, worst = Infinity;
    const reasons = {};
    const days = new Set();

    done.forEach(t => {
        const n = _emacNetPnl(t);
        gross   += Number(t.pnl) || 0;
        charges += _emacCharges(t);
        net     += n;
        if (n >= 0) { wins++;   winSum  += n; }
        else        { losses++; lossSum += Math.abs(n); }
        if (n > best)  best  = n;
        if (n < worst) worst = n;
        const r = String(t.reason || '').toUpperCase();
        if (r) reasons[r] = (reasons[r] || 0) + 1;
        const day = t.date || (t.entry_time ? String(t.entry_time).slice(0, 10) : null);
        if (day) days.add(day);
    });

    const total   = done.length;
    const winRate = total ? (wins / total * 100) : 0;
    const pf      = lossSum > 0 ? (winSum / lossSum) : (winSum > 0 ? Infinity : 0);
    const maxDD   = _emacMaxDrawdown(done);

    const cls  = v => v >= 0 ? 'ag-pos' : 'ag-neg';
    const inrF = v => (v >= 0 ? '+₹' : '-₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');
    const negF = v => '-₹' + Math.abs(Math.round(v)).toLocaleString('en-IN');

    const tiles = [
        { label: 'Total Trades',  value: total },
        { label: 'Wins',          value: wins,   cls: 'ag-pos' },
        { label: 'Losses',        value: losses, cls: 'ag-neg' },
        { label: 'Win Rate',      value: winRate.toFixed(1) + '%' },
        { label: 'Net P&L (₹)',   value: inrF(net),   cls: cls(net) },
        { label: 'Gross P&L (₹)', value: inrF(gross), cls: cls(gross) },
        { label: 'Brokerage (₹)', value: negF(charges), cls: 'ag-neg' },
        { label: 'Profit Factor', value: pf === Infinity ? '∞' : pf.toFixed(2) },
        { label: 'Avg Win (₹)',   value: wins   ? inrF(winSum / wins)    : '—', cls: wins   ? 'ag-pos' : '' },
        { label: 'Avg Loss (₹)',  value: losses ? negF(lossSum / losses) : '—', cls: losses ? 'ag-neg' : '' },
        { label: 'Best Trade',    value: inrF(best),  cls: cls(best) },
        { label: 'Worst Trade',   value: inrF(worst), cls: cls(worst) },
        { label: 'Max Drawdown',  value: negF(maxDD), cls: 'ag-neg' },
        { label: 'Trading Days',  value: days.size || '—' },
        ...Object.keys(reasons).sort().map(r => ({
            label: r.replace(/_/g, ' '),
            value: reasons[r],
            cls: _EMAC_REASON_TONE[r] === 'pos' ? 'ag-pos'
               : _EMAC_REASON_TONE[r] === 'neg' ? 'ag-neg' : '',
        })),
    ];

    const stats = document.getElementById('emacPerfStats');
    if (stats) {
        stats.innerHTML = tiles.map(t =>
            `<div class="ag-stat">
                <span class="ag-stat-label">${t.label}</span>
                <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
            </div>`
        ).join('');
    }

    const metaEl = document.getElementById('emacPerfMeta');
    if (metaEl) metaEl.textContent = `${total} trade${total > 1 ? 's' : ''} · net of ₹${Math.round(charges).toLocaleString('en-IN')} brokerage`;

    const netEl = document.getElementById('emacPerfNet');
    if (netEl) {
        netEl.textContent = inrF(net);
        netEl.style.color = net >= 0 ? 'var(--ag-pos)' : 'var(--ag-neg)';
    }
}

// Deepest peak-to-trough dip of the running net P&L, in entry order (≤ 0).
function _emacMaxDrawdown(trades) {
    const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));
    let cum = 0, peak = 0, maxDD = 0;
    sorted.forEach(t => {
        cum += _emacNetPnl(t);
        if (cum > peak) peak = cum;
        if (cum - peak < maxDD) maxDD = cum - peak;
    });
    return maxDD;
}

function _emacFmtDateTime(iso) {
    try {
        return new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return iso;
    }
}

// ── Equity Curve + P&L Breakdown ────────────────────────────────────────────
// Same Chart.js approach as the Backtest page's equity curve / period
// breakdown (static/js/backtest.js), adapted for EMA Confluence: every
// trade is already a real ₹ paper P&L (qty × price move on the future).

const _EMAC_CHART_THEME = {
    light:  { tick: '#374151', grid: 'rgba(15, 23, 42, 0.05)',   gridZero: 'rgba(15, 23, 42, 0.25)' },
    dark:   { tick: '#94a3b8', grid: 'rgba(255, 255, 255, 0.06)', gridZero: 'rgba(255, 255, 255, 0.25)' },
    forest: { tick: '#6ba88f', grid: 'rgba(16, 185, 129, 0.08)', gridZero: 'rgba(16, 185, 129, 0.3)' },
    cream:  { tick: '#7c7267', grid: 'rgba(180, 83, 9, 0.06)',   gridZero: 'rgba(180, 83, 9, 0.3)' },
    ocean:  { tick: '#475569', grid: 'rgba(2, 132, 199, 0.06)',  gridZero: 'rgba(2, 132, 199, 0.3)' },
};
function _emacChartColors() {
    return (window.AppTheme && _EMAC_CHART_THEME[window.AppTheme.getActiveTheme()]) || _EMAC_CHART_THEME.ocean;
}

let _emacEquityChart = null;

function _emacRenderEquityCurve(trades) {
    const section = document.getElementById('emacEquityCurveSection');
    if (!section || !trades || trades.length === 0) {
        if (section) section.style.display = 'none';
        return;
    }
    const chartColors = _emacChartColors();
    const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));

    const labels       = ['Start'];
    const chartData    = [_emacStartingEquity];
    const tooltipMeta  = [''];
    const pointColors  = ['#2962ff'];

    let portfolio = _emacStartingEquity;
    sorted.forEach((t, idx) => {
        const pnl = _emacNetPnl(t);   // net of brokerage, same as the grid
        portfolio += pnl;
        labels.push('T' + (idx + 1));
        chartData.push(Math.round(portfolio));
        pointColors.push(pnl >= 0 ? '#00c853' : '#ff1744');
        tooltipMeta.push(t.symbol ? `${t.symbol} · ${t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : ''}` : '');
    });

    const finalValue = chartData[chartData.length - 1];
    const diff       = finalValue - _emacStartingEquity;
    const isProfit   = diff >= 0;
    const lineColor  = isProfit ? '#2962ff' : '#ff1744';
    const fillColor  = isProfit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

    const finalEl = document.getElementById('emacEquityCurveFinalPnl');
    if (finalEl) {
        const pct = _emacStartingEquity ? ((diff / _emacStartingEquity) * 100).toFixed(1) : '0.0';
        finalEl.textContent =
            (diff >= 0 ? '+' : '') + '₹' + Math.abs(diff).toLocaleString('en-IN') +
            '  (' + (diff >= 0 ? '+' : '') + pct + '%)';
        finalEl.style.color = isProfit ? '#00c853' : '#ff1744';
    }

    const fmtY = v => {
        if (Math.abs(v) >= 100000) return '₹' + (v / 100000).toFixed(1) + 'L';
        if (Math.abs(v) >= 1000)   return '₹' + (v / 1000).toFixed(0)   + 'K';
        return '₹' + v;
    };

    if (_emacEquityChart) { _emacEquityChart.destroy(); _emacEquityChart = null; }
    const ctx = document.getElementById('emacEquityCurveChart');
    if (!ctx) return;

    _emacEquityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Portfolio Value (Paper)',
                data: chartData,
                borderColor: lineColor,
                backgroundColor: fillColor,
                fill: true,
                tension: 0.25,
                pointRadius: 0,
                pointHoverRadius: 4,
                pointHoverBackgroundColor: pointColors,
                pointHoverBorderColor: pointColors,
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => {
                            const i = items[0].dataIndex;
                            if (i === 0) return 'Starting value';
                            return `Trade ${i}  ·  ${tooltipMeta[i]}`;
                        },
                        label: (item) => {
                            const v   = item.raw;
                            const chg = v - _emacStartingEquity;
                            return [
                                '  Value: ₹' + Math.round(v).toLocaleString('en-IN'),
                                '  P&L:   ' + (chg >= 0 ? '+' : '') + '₹' + Math.round(chg).toLocaleString('en-IN'),
                            ];
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: { maxTicksLimit: 15, color: chartColors.tick, font: { size: 11 }, autoSkip: true },
                    grid:  { color: chartColors.grid },
                },
                y: {
                    ticks: { color: chartColors.tick, font: { size: 11 }, callback: fmtY },
                    grid:  { color: chartColors.grid },
                }
            }
        }
    });

    section.style.display = '';
}

let _emacPeriodChart = null;

function _emacGetWeekKey(date) {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - d.getDay() + 1); // Monday
    return d.toISOString().slice(0, 10);
}

function _emacGroupByPeriod(trades, period) {
    const groups = {};
    trades.forEach(t => {
        const d = new Date(t.entry_time);
        let key;
        if      (period === 'daily')   key = d.toISOString().slice(0, 10);
        else if (period === 'weekly')  key = _emacGetWeekKey(d);
        else if (period === 'monthly') key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
        else if (period === 'quarterly')  key = `${d.getFullYear()}-Q${Math.floor(d.getMonth() / 3) + 1}`;
        else if (period === 'halfyearly') key = `${d.getFullYear()}-H${d.getMonth() < 6 ? 1 : 2}`;
        else                            key = `${d.getFullYear()}`;
        if (!groups[key]) groups[key] = { pnl: 0, wins: 0, losses: 0 };
        // Net P&L throughout — a trade that only won before brokerage is a loss.
        const net = _emacNetPnl(t);
        groups[key].pnl += net;
        if (net > 0) groups[key].wins++;
        else         groups[key].losses++;
    });
    return groups;
}

function _emacFmtCompact(v) {
    const abs  = Math.abs(v);
    const sign = v >= 0 ? '+' : '−';
    if (abs >= 100000) return sign + '₹' + (abs / 100000).toFixed(1) + 'L';
    if (abs >= 1000)   return sign + '₹' + (abs / 1000).toFixed(1) + 'K';
    return sign + '₹' + abs;
}

const _emacBarValueLabelPlugin = {
    id: 'emacBarValueLabels',
    afterDatasetsDraw(chart) {
        const { ctx, data } = chart;
        const meta = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = '600 9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.textAlign = 'center';
        meta.data.forEach((bar, i) => {
            const v = data.datasets[0].data[i];
            if (v == null) return;
            const text = _emacFmtCompact(v);
            ctx.fillStyle = v >= 0 ? '#16a34a' : '#dc2626';
            if (v >= 0) { ctx.textBaseline = 'bottom'; ctx.fillText(text, bar.x, bar.y - 3); }
            else        { ctx.textBaseline = 'top';    ctx.fillText(text, bar.x, bar.y + 3); }
        });
        ctx.restore();
    }
};

function _emacRenderPeriodBreakdown(trades, period) {
    const section = document.getElementById('emacPeriodBreakdownSection');
    const chartColors = _emacChartColors();
    if (!section || !trades || trades.length === 0) {
        if (section) section.style.display = 'none';
        return;
    }

    const groups = _emacGroupByPeriod(trades, period);
    const keys   = Object.keys(groups).sort();

    const labels = keys.map(k => {
        if (period === 'monthly') {
            const [y, m] = k.split('-');
            return new Date(+y, +m - 1).toLocaleString('default', { month: 'short', year: '2-digit' });
        }
        if (period === 'weekly')  return 'W ' + k.slice(5);
        if (period === 'daily')   return k.slice(5);
        if (period === 'quarterly' || period === 'halfyearly') {
            const [y, p] = k.split('-');
            return `${p} '${y.slice(2)}`;
        }
        return k;
    });

    const values = keys.map(k => Math.round(groups[k].pnl));
    const meta   = keys.map(k => groups[k]);

    const bgColors  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)'  : 'rgba(239,68,68,.20)');
    const brdColors = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)'  : 'rgba(239,68,68,.90)');

    const canvas = document.getElementById('emacPeriodBreakdownChart');
    if (!canvas) return;
    if (_emacPeriodChart) { _emacPeriodChart.destroy(); _emacPeriodChart = null; }

    const inner = document.getElementById('emacPeriodChartInner');
    if (inner) {
        const MIN_BAR_PX = 34;
        const wrapWidth  = inner.parentElement.clientWidth;
        inner.style.minWidth = Math.max(wrapWidth, keys.length * MIN_BAR_PX) + 'px';
    }

    _emacPeriodChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        plugins: [_emacBarValueLabelPlugin],
        data: {
            labels,
            datasets: [{
                data: values,
                backgroundColor: bgColors,
                borderColor:     brdColors,
                borderWidth:  1.5,
                borderRadius: 4,
                borderSkipped: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            layout: { padding: { top: 18, bottom: 4 } },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: ctx => labels[ctx[0].dataIndex],
                        label: ctx => {
                            const i  = ctx.dataIndex;
                            const v  = values[i];
                            const g  = meta[i];
                            const tr = g.wins + g.losses;
                            const wr = tr > 0 ? ((g.wins / tr) * 100).toFixed(0) : 0;
                            return [
                                ' P&L: ' + (v >= 0 ? '+' : '') + '₹' + Math.abs(v).toLocaleString('en-IN'),
                                ` Trades: ${tr}  (${g.wins}W / ${g.losses}L)`,
                                ` Win Rate: ${wr}%`,
                            ];
                        }
                    },
                    padding: 10,
                    displayColors: false,
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 9, weight: '500' }, color: chartColors.tick }
                },
                y: {
                    grid: {
                        color: ctx => ctx.tick.value === 0 ? chartColors.gridZero : chartColors.grid,
                        lineWidth: ctx => ctx.tick.value === 0 ? 1.5 : 1,
                    },
                    ticks: {
                        font: { size: 9 }, color: chartColors.tick,
                        callback: v => {
                            if (v === 0) return '0';
                            const abs = Math.abs(v);
                            const s   = v < 0 ? '−' : '';
                            if (abs >= 100000) return s + '₹' + (abs/100000).toFixed(1) + 'L';
                            if (abs >= 1000)   return s + '₹' + (abs/1000).toFixed(0) + 'K';
                            return s + '₹' + abs;
                        }
                    }
                }
            }
        }
    });
    section.style.display = '';
}

document.querySelectorAll('#emacPeriodTabs .period-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#emacPeriodTabs .period-tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _emacRenderPeriodBreakdown(_emacHistoryTrades, btn.dataset.period);
    });
});

window.addEventListener('themechanged', () => {
    if (_emacHistoryTrades.length) {
        _emacRenderEquityCurve(_emacHistoryTrades);
        const activePeriod = document.querySelector('#emacPeriodTabs .period-tab.active')?.dataset.period || 'monthly';
        _emacRenderPeriodBreakdown(_emacHistoryTrades, activePeriod);
    }
});

// Minimal version of backtest.js's initCollapsibles — click a
// [data-collapse] header to toggle its target's visibility (chevron ▾/▸).
function _emacInitCollapsibles() {
    document.querySelectorAll('#algo-ema-confluence-panel [data-collapse]').forEach(h => {
        if (h._collapseWired) return;
        h._collapseWired = true;
        const sel = h.dataset.collapse;
        const target = (h.parentElement && h.parentElement.querySelector(sel)) || document.querySelector(sel);
        const chev = document.createElement('span');
        chev.className = 'collapse-chev';
        chev.style.marginRight = '6px';
        chev.textContent = '▾';
        const anchor = h.querySelector('h2, h3, h4, h5') || h;
        anchor.insertBefore(chev, anchor.firstChild);
        h.addEventListener('click', (e) => {
            if (e.target.closest('button, a, input, select')) return;
            if (!target) return;
            const collapsed = target.classList.toggle('collapsed-hide');
            chev.textContent = collapsed ? '▸' : '▾';
        });
    });
}
_emacInitCollapsibles();

function _emacDeleteAllTrades() {
    if (!confirm('Delete ALL EMA Confluence Breakout trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/ema-confluence/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_emacHistoryTimer);
            _emacFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _emacDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/ema-confluence/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_emacHistoryTimer);
            _emacFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── Logic modal ──────────────────────────────────────────────────────────────

function emacShowLogic() {
    document.getElementById('emacLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'emacLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">EMA Confluence Breakout</span>
            <span class="sm-modal-subtitle">How it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('emacLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <div class="rtp-tf"><span class="rtp-tf-lbl">Mode</span><span class="rtp-tf-val">Simulated (paper) fills on the FUTURES contract</span><span class="rtp-tf-sub">No broker orders — gated by the EMA_CONFLUENCE_ACTIVE kill-switch</span></div>

        <p class="rtp-idea">Scans <b>every symbol</b> in the Backtest page's EMA Confluence symbol table, each using its <b>own</b> default Direction/Target%. Daily EMA 20/50/100/200 — a signal candle's range must touch all four at once; its own colour (red/green) fixes the direction. <b>One setup per symbol at a time</b> — a new signal is ignored while one is still watching or in position.</p>

        <div class="rtp-blk-lbl entry">Entry — breakout of the signal candle, no expiry</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY</div>
                <div class="rtp-duo-row">Green signal candle — armed on its own High</div>
                <div class="rtp-duo-row">Fills whenever the future's LTP trades &ge; that High, any day after</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL</div>
                <div class="rtp-duo-row">Red signal candle — armed on its own Low</div>
                <div class="rtp-duo-row">Fills whenever the future's LTP trades &le; that Low, any day after</div>
            </div>
        </div>
        <div class="rtp-mode-note">The daily scan runs once each morning off the most recently <b>completed</b> daily candle (index/equity — futures don't carry enough history for a 200-day EMA). The armed trigger/SL stay on the equity/index price scale and are compared directly against the future's live LTP — a known approximation, same spirit as other approximations already used across this app.</div>

        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target</b><span>symbol's own Target% of the entry price</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss</b><span>signal candle's opposite extreme</span></div></div>
        </div>
        <div class="rtp-mode-note">This is a multi-day <b>swing</b> strategy, not intraday — there is no time-based square-off. A position can stay open across any number of days until SL or Target is hit. Qty is Lots (EMA_CONFLUENCE_LOTS) &times; the future's own lot size.</div>

    </div>

</div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.addEventListener('keydown', function esc(ev) {
        if (ev.key === 'Escape') { document.getElementById('emacLogicModal')?.remove(); document.removeEventListener('keydown', esc); }
    });
}

// ── Start / Stop. The algo runs by itself every trading day (9:15 AM job
//    + 5-min watchdog). Stop is durable — it persists EMA_CONFLUENCE_ENABLED
//    =false so it stays down across days and restarts, and Start re-arms
//    that daily schedule as well as launching the thread now. ─────────────

function _emacStart() {
    fetch('/api/algo/ema-confluence/start', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Start failed: ' + (d.error || 'Unknown error')); return; }
            _emacFetchStatus();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _emacStop() {
    if (!confirm('Stop the EMA Confluence Breakout algo? It will stay stopped on every following day too — until you click Start. Open paper positions will NOT be tracked meanwhile.')) return;
    fetch('/api/algo/ema-confluence/stop', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Stop failed: ' + (d.error || 'Unknown error')); return; }
            _emacFetchStatus();
        })
        .catch(e => alert('Request failed: ' + e));
}
