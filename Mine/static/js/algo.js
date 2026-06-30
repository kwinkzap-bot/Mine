/* algo.js — Algo page: Straddle + EMA RTP tabs */
'use strict';

let _algoTimer         = null;
let _rtpStatusTimer    = null;
let _rtpHistoryTimer   = null;
let _rtpLastEntryTime  = null;  // tracks last seen entry_time to detect trade changes
let _rtpLastActiveFlag = false; // tracks last seen active flag
let _scStatusTimer     = null;
let _scHistoryTimer    = null;
let _scLastEntryTime   = null;
let _scLastActiveFlag  = false;
const _ALGO_TABS = ['straddle', 'rtp', 'sc', 'swing-momentum'];

// Round-trip brokerage charged per lot (1 lot = 65 qty). The performance
// dashboards express ₹ P&L on a single-lot basis (opt_pnl_pts × lot_size), so
// exactly one lot's brokerage is deducted from each completed trade.
const _ALGO_BROKERAGE_PER_LOT = 135;

// Realised ₹ for a completed trade, net of round-trip brokerage.
function _algoNetInr(t) {
    if (!t || t.opt_pnl_inr == null) return 0;
    return (Number(t.opt_pnl_inr) || 0) - _ALGO_BROKERAGE_PER_LOT;
}

function algoLoad() {
    const hash = location.hash.replace('#', '');
    algoSwitch(_ALGO_TABS.includes(hash) ? hash : 'swing-momentum');
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
    clearTimeout(_scStatusTimer);
    clearTimeout(_scHistoryTimer);
    if (tab === 'straddle') {
        _fetchStatus();
    } else if (tab === 'rtp') {
        _rtpFetchStatus();
        _rtpFetchHistory();
    } else if (tab === 'sc') {
        scLoadSettings();
        _scFetchStatus();
        _scFetchHistory();
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
            <th class="ag-hist-th">N P&amp;L</th>
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
        const oPnlCls   = (t.opt_pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
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
        TARGET: '<span class="ag-reason-badge target">TARGET</span>',
        SL:     '<span class="ag-reason-badge sl">SL</span>',
        EOD:    '<span class="ag-reason-badge eod">EOD</span>',
        MANUAL: '<span class="ag-reason-badge manual">MANUAL</span>',
    };
    return map[reason] || reason || '—';
}

// ── RTP Delete History Record ─────────────────────────────────────────────────

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
            <th class="ag-hist-th">N P&amp;L</th>
            <th class="ag-hist-th">Opt P&amp;L</th>
            <th class="ag-hist-th">Reason</th>
            <th class="ag-hist-th"></th>
        </tr>
    </thead>
    <tbody>
    ${trades.map(t => {
        const nPnlCls   = (t.pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
        const oPnlCls   = (t.opt_pnl_pts || 0) >= 0 ? 'ag-pos' : 'ag-neg';
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

function scExitNow(btn) {
    if (!confirm('Force-close the active 2nd-candle trade? A market SELL order will be sent immediately.')) return;
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
    if (wrap) wrap.innerHTML = _smRebalPreviewHtml(d);
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
                    <th class="sm-th-today">Today ₹</th><th class="sm-th-today">Today %</th>
                    <th>Total ₹</th><th>Total %</th>
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
                        <td class="sm-col-num">${h.qty}</td>
                        <td class="sm-td-date">${h.entry_date || '—'}</td>
                        <td>₹${Number(h.entry_price).toFixed(2)}</td>
                        <td>₹${Number(h.current_price).toFixed(2)}</td>
                        <td>₹${Number(h.buy_value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</td>
                        <td>₹${Number(h.current_value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</td>
                        <td class="${tCls} sm-td-today">${tSign}${Math.abs(h.today_abs || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</td>
                        <td class="${tCls} sm-td-today">${tPct}</td>
                        <td class="${pCls} sm-pnl-abs">${pSign}${Math.abs(h.pnl_abs).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</td>
                        <td class="${pCls} sm-pnl-pct">${pPct}</td>
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
        <button class="ag-btn ag-btn-preview sm-ab-btn" onclick="_smOpenFlowModal('${id}', 'sip')">＋ SIP</button>
        <button class="ag-btn ag-btn-exit sm-ab-btn" onclick="_smOpenFlowModal('${id}', 'swp')">－ SWP</button>
        ${sipLog.length ? `<button class="sm-history-btn sm-ab-btn" onclick="_smShowSipHistory('${id}')">History (${sipLog.length})</button>` : ''}
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
            <label class="sm-gl-field sm-gl-field-wide"><span>Broker (optional)</span>
                <select id="flowBroker"><option value="">None — update list only (no real orders)</option></select></label>
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

    // Populate broker dropdown (preselect the config's broker if any)
    fetch('/api/available-brokers').then(r => r.json()).then(bd => {
        const sel = document.getElementById('flowBroker');
        if (!sel || !bd || !bd.brokers) return;
        bd.brokers.filter(b => b.active !== false).forEach(b => {
            const opt = document.createElement('option');
            opt.value = b.instance_num;
            opt.dataset.type = b.broker_type || '';
            opt.dataset.name = b.name || b.broker_type || '';
            opt.textContent = `${b.name || b.broker_type} (${(b.broker_type || '').toUpperCase()})` +
                              (b.is_logged_in ? '' : ' — not connected');
            opt.disabled = !b.is_logged_in;
            if (data.broker && Number(data.broker.instance) === Number(b.instance_num)) opt.selected = true;
            sel.appendChild(opt);
        });
    }).catch(() => {});

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

    const brokerSel = document.getElementById('flowBroker');
    const brokerOpt = brokerSel.selectedOptions[0];
    const brokerInst = brokerSel.value;

    const payload = {
        mode, amount,
        date: new Date().toISOString().split('T')[0],
        allocations: allocs.map(a => ({ symbol: a.symbol, qty: a.qty })),
    };
    if (brokerInst) {
        payload.broker_instance = brokerInst;
        payload.broker_type     = brokerOpt?.dataset.type || '';
        payload.broker_name     = brokerOpt?.dataset.name || '';
    }

    const btn = document.getElementById('flowConfirmBtn');
    btn.disabled = true;
    btn.textContent = brokerInst ? 'Placing orders…' : 'Updating…';

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
<div class="sm-gl-box ${accent}">
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
            <label class="sm-gl-field sm-gl-field-wide"><span>Broker (optional)</span>
                <select id="soBroker"><option value="">None — update list only (no real order)</option></select></label>
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

    // Broker dropdown (preselect the config's broker if any)
    fetch('/api/available-brokers').then(r => r.json()).then(bd => {
        const sel = document.getElementById('soBroker');
        if (!sel || !bd || !bd.brokers) return;
        bd.brokers.filter(b => b.active !== false).forEach(b => {
            const opt = document.createElement('option');
            opt.value = b.instance_num;
            opt.dataset.type = b.broker_type || '';
            opt.dataset.name = b.name || b.broker_type || '';
            opt.textContent = `${b.name || b.broker_type} (${(b.broker_type || '').toUpperCase()})` +
                              (b.is_logged_in ? '' : ' — not connected');
            opt.disabled = !b.is_logged_in;
            if (data.broker && Number(data.broker.instance) === Number(b.instance_num)) opt.selected = true;
            sel.appendChild(opt);
        });
    }).catch(() => {});

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

    const brokerSel  = document.getElementById('soBroker');
    const brokerOpt  = brokerSel.selectedOptions[0];
    const brokerInst = brokerSel.value;

    const payload = {
        mode: isBuy ? 'sip' : 'swp',
        amount: qty * (Number(price) || 0),
        note: `Manual ${side} ${sym}`,
        allocations: [{ symbol: sym, qty }],
    };
    if (brokerInst) {
        payload.broker_instance = brokerInst;
        payload.broker_type     = brokerOpt?.dataset.type || '';
        payload.broker_name     = brokerOpt?.dataset.name || '';
    }

    const btn = document.getElementById('soConfirmBtn');
    btn.disabled = true;
    btn.textContent = brokerInst ? 'Placing order…' : 'Updating…';

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

// ── Shared: rebalance preview section ─────────────────────────────────────────

function _smRebalPreviewHtml(d) {
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
        <span class="sm-rebal-status ${statusCls}">${statusTxt}</span>
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


function _smFmtInr(v) {
    if (v == null) return '—';
    return '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}
