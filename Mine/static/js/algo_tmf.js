/* algo_tmf.js — 30-Min Opening Fakeout Algo tab: its own file, separate from
   algo.js (which drives every other Live Algo tab) and separate from
   backtest.js (the Backtest page). algo.js only calls four small hooks
   here — _tmfFetchStatus, _tmfFetchHistory, _tmfClearTimers, and the
   typeof-guarded call in algoSwitch — everything else about this tab
   (rendering, start/stop, delete) lives in this file. */
'use strict';

let _tmfStatusTimer  = null;
let _tmfHistoryTimer = null;
let _tmfStocksData   = {};   // last-fetched {symbol: {phase, direction, ...}}
let _tmfCapitalPerTrade = 100000;   // equity-curve starting value, from TMF_CAPITAL_PER_TRADE
let _tmfHistoryTrades   = [];       // last-fetched trade list, kept for period-tab redraws

const _TMF_PHASE_LABEL = {
    pending_scan:  'Not scanned yet',
    no_setup:      'No setup',
    watching:      'Watching',
    pending_entry: 'Entry pending',
    in_position:   'In position',
    done:          'Done',
};
const _TMF_PHASE_TONE = {
    pending_scan:  'neutral',
    no_setup:      'neutral',
    watching:      'warn',
    pending_entry: 'info',
    in_position:   'pos',
    done:          'neutral',
};
// Phases hidden by default (the bulk of the 143-stock universe before a
// setup is found) — the "Show all 143 stocks" checkbox reveals them.
const _TMF_QUIET_PHASES = new Set(['pending_scan', 'no_setup']);

function _tmfClearTimers() {
    clearTimeout(_tmfStatusTimer);
    clearTimeout(_tmfHistoryTimer);
}

// ── Status (summary + per-stock) ────────────────────────────────────────────

function _tmfFetchStatus() {
    fetch('/api/algo/thirty-min-fakeout/status')
        .then(r => r.json())
        .then(data => {
            _tmfRenderStatus(data);
            clearTimeout(_tmfStatusTimer);
            _tmfStatusTimer = setTimeout(_tmfFetchStatus, 10000);
        })
        .catch(() => {
            clearTimeout(_tmfStatusTimer);
            _tmfStatusTimer = setTimeout(_tmfFetchStatus, 15000);
        });
}

function _tmfRenderStatus(data) {
    if (!data || !data.success) return;

    const runBadge = document.getElementById('tmfRunBadge');
    const runText  = document.getElementById('tmfRunBadgeText');
    if (runBadge && runText) {
        runBadge.className = 'ag-badge ' + (data.running ? 'active' : 'inactive');
        // `enabled === false` means the user clicked Stop: the scheduler will
        // leave it alone until Start. Plain "Stopped" means it is still armed
        // and simply outside market hours (or between restarts).
        runText.textContent = data.running ? 'Running'
                            : (data.enabled === false ? 'Stopped (manual)' : 'Stopped');
    }

    const modeBadge = document.getElementById('tmfModeBadge');
    if (modeBadge) {
        if (data.live_armed) {
            modeBadge.className = 'dg-badge dg-badge--neg';
            modeBadge.textContent = 'LIVE ORDERS';
        } else {
            modeBadge.className = 'dg-badge dg-badge--warn';
            modeBadge.textContent = 'SCAN ONLY';
        }
    }

    const upd = document.getElementById('tmfLastUpd');
    if (upd) upd.textContent = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    if (data.capital_per_trade) _tmfCapitalPerTrade = data.capital_per_trade;

    const summary = data.summary || {};
    const summaryGrid = document.getElementById('tmfSummaryGrid');
    if (summaryGrid) {
        const tiles = [
            { label: 'Watching',      value: summary.watching || 0 },
            { label: 'Entry Pending', value: summary.pending_entry || 0 },
            { label: 'In Position',   value: summary.in_position || 0, cls: (summary.in_position || 0) > 0 ? 'ag-warn' : '' },
            { label: 'Done Today',    value: summary.done || 0 },
            { label: 'No Setup',      value: summary.no_setup || 0 },
            { label: 'Not Scanned',   value: summary.pending_scan || 0 },
        ];
        summaryGrid.innerHTML = tiles.map(t =>
            `<div class="ag-stat">
                <span class="ag-stat-label">${t.label}</span>
                <span class="ag-stat-value ${t.cls || ''}">${t.value}</span>
            </div>`
        ).join('');
    }

    _tmfStocksData = data.stocks || {};
    _tmfRenderStocks();
}

function _tmfRenderStocks() {
    const body = document.getElementById('tmfStocksBody');
    if (!body) return;
    const showAll = document.getElementById('tmfShowAllStocks')?.checked;

    let rows = Object.entries(_tmfStocksData).map(([symbol, s]) => ({ symbol, ...s }));
    if (!showAll) {
        rows = rows.filter(r => !_TMF_QUIET_PHASES.has(r.phase));
    }
    // Most "active" phases first, alphabetical within a phase.
    const order = { in_position: 0, pending_entry: 1, watching: 2, done: 3, no_setup: 4, pending_scan: 5 };
    rows.sort((a, b) => (order[a.phase] ?? 9) - (order[b.phase] ?? 9) || a.symbol.localeCompare(b.symbol));

    body.innerHTML = DataGrid.render({
        rows,
        empty: showAll ? 'No stocks yet' : 'No stock has a signal today yet — waiting for candle 3 (10:45 AM) or check "Show all"',
        columns: [
            { key: 'symbol', label: 'Symbol', strong: true },
            { key: 'phase', label: 'Status',
              badge: v => _TMF_PHASE_TONE[v] || 'neutral',
              format: v => _TMF_PHASE_LABEL[v] || v },
            { key: 'direction', label: 'Direction',
              format: v => v ? (v === 'short' ? 'SELL' : 'BUY') : '—',
              tone: v => v === 'short' ? 'neg' : (v === 'long' ? 'pos' : undefined) },
            { key: 'trigger', label: 'Trigger', format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'sl_level', label: 'SL', format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'entry_price', label: 'Entry', format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'target_level', label: 'Target', format: v => v == null ? '—' : '₹' + Number(v).toFixed(2) },
            { key: 'qty', label: 'Qty', align: 'right', format: v => v ?? '—' },
            { key: 'exit_reason', label: 'Reason', format: v => v || '—' },
        ],
    });
}

// ── History ──────────────────────────────────────────────────────────────────

function _tmfFetchHistory() {
    fetch('/api/algo/thirty-min-fakeout/history')
        .then(r => r.json())
        .then(data => {
            _tmfRenderHistory(data.trades || []);
            clearTimeout(_tmfHistoryTimer);
            _tmfHistoryTimer = setTimeout(_tmfFetchHistory, 30000);
        })
        .catch(() => {
            clearTimeout(_tmfHistoryTimer);
            _tmfHistoryTimer = setTimeout(_tmfFetchHistory, 30000);
        });
}

const _TMF_REASON_TONE = {
    'Target Hit': 'pos',
    'SL Hit':     'neg',
    'Time Exit':  'neutral',
};

function _tmfRenderHistory(trades) {
    _tmfHistoryTrades = trades || [];
    _tmfRenderEquityCurve(_tmfHistoryTrades);
    const activePeriod = document.querySelector('#tmfPeriodTabs .period-tab.active')?.dataset.period || 'monthly';
    _tmfRenderPeriodBreakdown(_tmfHistoryTrades, activePeriod);

    const countEl = document.getElementById('tmfHistCount');
    const body    = document.getElementById('tmfHistBody');
    if (countEl) countEl.textContent = trades.length ? trades.length + ' trade' + (trades.length > 1 ? 's' : '') : '';
    if (!body) return;

    body.innerHTML = DataGrid.render({
        rows: trades,
        empty: 'No completed trades',
        columns: [
            { key: 'date', label: 'Date' },
            { key: 'symbol', label: 'Symbol', strong: true },
            { key: 'direction', label: 'Direction', tone: v => v === 'SELL' ? 'neg' : 'pos' },
            { key: 'entry_time', label: 'Entry Time', format: v => v ? _tmfFmtTime(v) : '—' },
            { key: 'exit_time', label: 'Exit Time', format: v => v ? _tmfFmtTime(v) : '—' },
            { key: 'qty', label: 'Qty', align: 'right' },
            { key: 'entry_price', label: 'Entry', format: DataGrid.rupees },
            { key: 'exit_price', label: 'Exit', format: DataGrid.rupees },
            { key: 'sl_price', label: 'SL', format: DataGrid.rupees },
            { key: 'target_price', label: 'Target', format: v => v == null ? '—' : DataGrid.rupees(v) },
            { key: 'pnl', label: 'P&L (₹)', strong: true, format: DataGrid.inr, tone: DataGrid.sign },
            { key: 'reason', label: 'Reason', badge: v => _TMF_REASON_TONE[v] || 'neutral' },
            { label: '', cellClass: 'ag-hist-td-del',
              render: (_, t) => `<button class="ag-hist-del-btn" title="Delete record"` +
                  ` onclick="_tmfDeleteTrade('${(t.entry_time || '').replace(/"/g, '&quot;')}')">&#128465;</button>` },
        ],
    });
}

function _tmfFmtTime(iso) {
    try {
        return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return iso;
    }
}

// ── Equity Curve + P&L Breakdown ────────────────────────────────────────────
// Same Chart.js approach as the Backtest page's equity curve / period
// breakdown (static/js/backtest.js), adapted for TMF: every trade is
// already a real ₹ equity fill (no isRtp/lots/lot-value branching needed).

const _TMF_CHART_THEME = {
    light:  { tick: '#374151', grid: 'rgba(15, 23, 42, 0.05)',   gridZero: 'rgba(15, 23, 42, 0.25)' },
    dark:   { tick: '#94a3b8', grid: 'rgba(255, 255, 255, 0.06)', gridZero: 'rgba(255, 255, 255, 0.25)' },
    forest: { tick: '#6ba88f', grid: 'rgba(16, 185, 129, 0.08)', gridZero: 'rgba(16, 185, 129, 0.3)' },
    cream:  { tick: '#7c7267', grid: 'rgba(180, 83, 9, 0.06)',   gridZero: 'rgba(180, 83, 9, 0.3)' },
    ocean:  { tick: '#475569', grid: 'rgba(2, 132, 199, 0.06)',  gridZero: 'rgba(2, 132, 199, 0.3)' },
};
function _tmfChartColors() {
    return (window.AppTheme && _TMF_CHART_THEME[window.AppTheme.getActiveTheme()]) || _TMF_CHART_THEME.ocean;
}

let _tmfEquityChart = null;

function _tmfRenderEquityCurve(trades) {
    const section = document.getElementById('tmfEquityCurveSection');
    if (!section || !trades || trades.length === 0) {
        if (section) section.style.display = 'none';
        return;
    }
    const chartColors = _tmfChartColors();
    const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));

    const labels       = ['Start'];
    const chartData    = [_tmfCapitalPerTrade];
    const tooltipMeta  = [''];
    const pointColors  = ['#2962ff'];

    let portfolio = _tmfCapitalPerTrade;
    sorted.forEach((t, idx) => {
        const pnl = t.pnl || 0;
        portfolio += pnl;
        labels.push('T' + (idx + 1));
        chartData.push(Math.round(portfolio));
        pointColors.push(pnl >= 0 ? '#00c853' : '#ff1744');
        tooltipMeta.push(t.symbol ? `${t.symbol} · ${t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : ''}` : '');
    });

    const finalValue = chartData[chartData.length - 1];
    const diff       = finalValue - _tmfCapitalPerTrade;
    const isProfit   = diff >= 0;
    const lineColor  = isProfit ? '#2962ff' : '#ff1744';
    const fillColor  = isProfit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

    const finalEl = document.getElementById('tmfEquityCurveFinalPnl');
    if (finalEl) {
        const pct = _tmfCapitalPerTrade ? ((diff / _tmfCapitalPerTrade) * 100).toFixed(1) : '0.0';
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

    if (_tmfEquityChart) { _tmfEquityChart.destroy(); _tmfEquityChart = null; }
    const ctx = document.getElementById('tmfEquityCurveChart');
    if (!ctx) return;

    _tmfEquityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Portfolio Value',
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
                            if (i === 0) return 'Starting capital';
                            return `Trade ${i}  ·  ${tooltipMeta[i]}`;
                        },
                        label: (item) => {
                            const v   = item.raw;
                            const chg = v - _tmfCapitalPerTrade;
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

let _tmfPeriodChart = null;

function _tmfGetWeekKey(date) {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - d.getDay() + 1); // Monday
    return d.toISOString().slice(0, 10);
}

function _tmfGroupByPeriod(trades, period) {
    const groups = {};
    trades.forEach(t => {
        const d = new Date(t.entry_time);
        let key;
        if      (period === 'daily')   key = d.toISOString().slice(0, 10);
        else if (period === 'weekly')  key = _tmfGetWeekKey(d);
        else if (period === 'monthly') key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
        else if (period === 'quarterly')  key = `${d.getFullYear()}-Q${Math.floor(d.getMonth() / 3) + 1}`;
        else if (period === 'halfyearly') key = `${d.getFullYear()}-H${d.getMonth() < 6 ? 1 : 2}`;
        else                            key = `${d.getFullYear()}`;
        if (!groups[key]) groups[key] = { pnl: 0, wins: 0, losses: 0 };
        groups[key].pnl += (t.pnl || 0);
        if ((t.pnl || 0) > 0) groups[key].wins++;
        else                  groups[key].losses++;
    });
    return groups;
}

function _tmfFmtCompact(v) {
    const abs  = Math.abs(v);
    const sign = v >= 0 ? '+' : '−';
    if (abs >= 100000) return sign + '₹' + (abs / 100000).toFixed(1) + 'L';
    if (abs >= 1000)   return sign + '₹' + (abs / 1000).toFixed(1) + 'K';
    return sign + '₹' + abs;
}

const _tmfBarValueLabelPlugin = {
    id: 'tmfBarValueLabels',
    afterDatasetsDraw(chart) {
        const { ctx, data } = chart;
        const meta = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = '600 9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.textAlign = 'center';
        meta.data.forEach((bar, i) => {
            const v = data.datasets[0].data[i];
            if (v == null) return;
            const text = _tmfFmtCompact(v);
            ctx.fillStyle = v >= 0 ? '#16a34a' : '#dc2626';
            if (v >= 0) { ctx.textBaseline = 'bottom'; ctx.fillText(text, bar.x, bar.y - 3); }
            else        { ctx.textBaseline = 'top';    ctx.fillText(text, bar.x, bar.y + 3); }
        });
        ctx.restore();
    }
};

function _tmfRenderPeriodBreakdown(trades, period) {
    const section = document.getElementById('tmfPeriodBreakdownSection');
    const chartColors = _tmfChartColors();
    if (!section || !trades || trades.length === 0) {
        if (section) section.style.display = 'none';
        return;
    }

    const groups = _tmfGroupByPeriod(trades, period);
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

    const canvas = document.getElementById('tmfPeriodBreakdownChart');
    if (!canvas) return;
    if (_tmfPeriodChart) { _tmfPeriodChart.destroy(); _tmfPeriodChart = null; }

    const inner = document.getElementById('tmfPeriodChartInner');
    if (inner) {
        const MIN_BAR_PX = 34;
        const wrapWidth  = inner.parentElement.clientWidth;
        inner.style.minWidth = Math.max(wrapWidth, keys.length * MIN_BAR_PX) + 'px';
    }

    _tmfPeriodChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        plugins: [_tmfBarValueLabelPlugin],
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

document.querySelectorAll('#tmfPeriodTabs .period-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#tmfPeriodTabs .period-tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _tmfRenderPeriodBreakdown(_tmfHistoryTrades, btn.dataset.period);
    });
});

window.addEventListener('themechanged', () => {
    if (_tmfHistoryTrades.length) {
        _tmfRenderEquityCurve(_tmfHistoryTrades);
        const activePeriod = document.querySelector('#tmfPeriodTabs .period-tab.active')?.dataset.period || 'monthly';
        _tmfRenderPeriodBreakdown(_tmfHistoryTrades, activePeriod);
    }
});

// Minimal version of backtest.js's initCollapsibles — click a
// [data-collapse] header to toggle its target's visibility (chevron ▾/▸).
function _tmfInitCollapsibles() {
    document.querySelectorAll('#algo-tmf-panel [data-collapse]').forEach(h => {
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
_tmfInitCollapsibles();

function _tmfDeleteAllTrades() {
    if (!confirm('Delete ALL 30-Min Fakeout trade history records? This clears the entire Executed Trade History and cannot be undone.')) return;
    fetch('/api/algo/thirty-min-fakeout/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ all: true }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_tmfHistoryTimer);
            _tmfFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _tmfDeleteTrade(entryTime) {
    if (!confirm('Delete this trade record? This cannot be undone.')) return;
    fetch('/api/algo/thirty-min-fakeout/history', {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ entry_time: entryTime }),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Delete failed: ' + (d.error || 'Unknown error')); return; }
            clearTimeout(_tmfHistoryTimer);
            _tmfFetchHistory();
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── Logic modal ──────────────────────────────────────────────────────────────

function tmfShowLogic() {
    document.getElementById('tmfLogicModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'tmfLogicModal';
    modal.className = 'sm-modal-overlay';
    modal.innerHTML = `
<div class="sm-modal-box rtp-logic-modal">

    <div class="sm-modal-hdr">
        <div class="sm-modal-icon-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19h16"/><polyline points="4 15 9 9 13 13 20 5"/></svg>
        </div>
        <div class="sm-modal-hdr-text">
            <span class="sm-modal-title">30-Min Opening Fakeout</span>
            <span class="sm-modal-subtitle">How it enters &amp; exits</span>
        </div>
        <button class="sm-modal-close" onclick="document.getElementById('tmfLogicModal').remove()" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>

    <div class="rtp-logic-body">

        <div class="rtp-tf"><span class="rtp-tf-lbl">Mode</span><span class="rtp-tf-val">Real Intraday (MIS) equity orders</span><span class="rtp-tf-sub">Zerodha only, right now &middot; gated by the TMF_ALGO_ACTIVE kill-switch</span></div>

        <p class="rtp-idea">Scans <b>every F&amp;O stock</b> (indices excluded) using 30-min candles from 09:15. Candle 2 breaks Candle 1's range and closes beyond it; Candle 3 fakes back through Candle 2's level and closes back inside — that reversal is the entry trigger. <b>One setup per stock per day.</b></p>

        <div class="rtp-blk-lbl entry">Entry — first breakout of the range candle</div>
        <div class="rtp-duo">
            <div class="rtp-duo-card buy">
                <div class="rtp-duo-hd">▲ BUY (breakout)</div>
                <div class="rtp-duo-row">C1 red · C2 red, closes below C1 Low</div>
                <div class="rtp-duo-row">C3 dips below C2 Low but closes back above it</div>
            </div>
            <div class="rtp-duo-card sell">
                <div class="rtp-duo-hd">▼ SELL (breakdown)</div>
                <div class="rtp-duo-row">C1 green · C2 green, closes above C1 High</div>
                <div class="rtp-duo-row">C3 pokes above C2 High but closes back below it</div>
            </div>
        </div>
        <div class="rtp-mode-note">Trigger = Candle 3's High/Low &plusmn; a small buffer. Each stock uses its <b>own</b> best-performing filter combo (Entry Buffer / Body Filter / C2-Close) found by backtest optimisation — not a single global toggle like the Backtest page.</div>

        <div class="rtp-blk-lbl exit">Exit — whichever hits first</div>
        <div class="rtp-chips">
            <div class="rtp-chip"><span class="rtp-chip-ic tgt">◎</span><div><b>Target</b><span>session Low/High so far, clamped past entry</span></div></div>
            <div class="rtp-chip"><span class="rtp-chip-ic sl">✕</span><div><b>Stop Loss</b><span>Candle 3's real High/Low</span></div></div>
            <div class="rtp-chip rtp-chip-wide"><span class="rtp-chip-ic eod">⏱</span><div><b>Cut-off 3:18 PM</b><span>force exit · no overnight hold</span></div></div>
        </div>
        <div class="rtp-mode-note">Unlike the Backtest's hindsight target (the full day's session Low/High), the live target can only use the session Low/High <b>known so far at entry fill</b> — it's skipped if that clamp leaves no room. SL and Target are both placed as real broker orders (OCO managed in software); qty is sized once at trigger time (capital &divide; price &times; 5x leverage) and reused for entry, SL and Target.</div>

    </div>

</div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.addEventListener('keydown', function esc(ev) {
        if (ev.key === 'Escape') { document.getElementById('tmfLogicModal')?.remove(); document.removeEventListener('keydown', esc); }
    });
}

// ── Start / Stop. The algo runs by itself every trading day (9:15 AM job
//    + 5-min watchdog). Stop is durable — it persists TMF_ALGO_ENABLED=false
//    so it stays down across days and restarts, and Start re-arms that daily
//    schedule as well as launching the thread now. ───────────────────────

function _tmfStart() {
    fetch('/api/algo/thirty-min-fakeout/start', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Start failed: ' + (d.error || 'Unknown error')); return; }
            _tmfFetchStatus();
        })
        .catch(e => alert('Request failed: ' + e));
}

function _tmfStop() {
    if (!confirm('Stop the 30-Min Fakeout algo? It will stay stopped on every following day too — until you click Start. Any open positions will NOT be auto-managed meanwhile.')) return;
    fetch('/api/algo/thirty-min-fakeout/stop', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { alert('Stop failed: ' + (d.error || 'Unknown error')); return; }
            _tmfFetchStatus();
        })
        .catch(e => alert('Request failed: ' + e));
}
