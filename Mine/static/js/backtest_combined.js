/* The combined book's report — cards, equity curve, P&L breakdown, month by month and the trade
 * history — drawn from normalised trade rows. Shared by the Simple Trader "Combined" popup below and
 * the Find Best Params "Combined" popup (backtest.js), so both read the same way.
 *
 *   BtCombinedReport({ box, rows() → trades in entry order, groups() → [{id, name, color}] present in
 *                      rows, basis() → 'gross' | 'net', empty() → html, meta() → text,
 *                      groupLabel: 'Strategy', csvName }) → { paint() }
 *
 * A trade row: {sid, sname, color, symbol, side, strike, entry, exit, ep, xp, lots, reason, gross, brok,
 * nb (net), net (gross or net, per basis)}.
 */
window.BtCombinedReport = function (o) {
    'use strict';
    const box = o.box;
    const basis = o.basis || (() => 'net');
    o.groupLabel = o.groupLabel || 'Strategy';
    let equityChart = null, page = 0;
    const PAGE = 25;
    const inr = (v, sign) => (v < 0 ? '-' : (sign && v > 0 ? '+' : '')) + '₹' + Math.round(Math.abs(v)).toLocaleString('en-IN');
    // Compact ₹ for the breakdown panels: 2.53L, 12.4k, 850.
    const inrc = (v, sign) => {
        const a = Math.abs(v), sg = v < 0 ? '-' : (sign && v > 0 ? '+' : '');
        return sg + '₹' + (a >= 1e5 ? (a / 1e5).toFixed(2) + 'L' : a >= 1e3 ? (a / 1e3).toFixed(1) + 'k' : Math.round(a));
    };
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const cls = (v) => v > 0 ? 'bc-pos' : (v < 0 ? 'bc-neg' : '');

    function stats(rows) {
        const s = { n: rows.length, wins: 0, losses: 0, net: 0, gross: 0, brok: 0, gp: 0, gl: 0,
                    peak: 0, dd: 0, ddDays: 0, winStreak: 0, lossStreak: 0, best: null, worst: null };
        let w = 0, l = 0, cum = 0, peakIdx = -1;
        rows.forEach((r, i) => {
            s.net += r.net; s.gross += r.gross; s.brok += r.brok;
            if (r.net > 0) { s.wins++; s.gp += r.net; w++; l = 0; }
            else if (r.net < 0) { s.losses++; s.gl += -r.net; l++; w = 0; }
            else { w = l = 0; }
            s.winStreak = Math.max(s.winStreak, w); s.lossStreak = Math.max(s.lossStreak, l);
            cum += r.net;
            if (cum > s.peak) { s.peak = cum; peakIdx = i; }
            if (s.peak - cum > s.dd) {
                s.dd = s.peak - cum;
                s.ddDays = new Set(rows.slice(peakIdx + 1, i + 1).map(x => x.entry.slice(0, 10))).size;
            }
            if (!s.best || r.net > s.best.net) s.best = r;
            if (!s.worst || r.net < s.worst.net) s.worst = r;
        });
        s.winRate = s.n ? s.wins / s.n * 100 : 0;
        s.pf = s.gl > 0 ? s.gp / s.gl : (s.gp > 0 ? Infinity : 0);
        s.avgWin = s.wins ? s.gp / s.wins : 0;
        s.avgLoss = s.losses ? -s.gl / s.losses : 0;
        return s;
    }

    // ── Results ─────────────────────────────────────────────────────────────────────────────
    function card(label, value, sub, c) {
        return `<div class="bc-card"><div class="bc-card-l">${label}</div><div class="bc-card-v ${c || ''}">${value}</div>${sub ? `<div class="bc-card-s">${sub}</div>` : ''}</div>`;
    }

    function groupBy(rows, keyFn) {
        const m = new Map();
        rows.forEach(r => {
            const k = keyFn(r);
            if (!m.has(k)) m.set(k, []);
            m.get(k).push(r);
        });
        return m;
    }

    // ── Sortable grid: one renderer for every table in the popup ──────────────────────────
    // cols: {k, label, get(row) → sort value, html(row) → cell html, cls(row)?, right?}
    const sorts = {};      // grid id → {k, dir}
    function sortRows(id, cols, data, dflt) {
        const so = sorts[id] || dflt;
        const col = so && cols.find(c => c.k === so.k);
        if (!col) return data;
        const sgn = so.dir === 'asc' ? 1 : -1;
        return data.slice().sort((x, y) => {
            const a = col.get(x), b = col.get(y);
            return (typeof a === 'string' || typeof b === 'string'
                ? String(a).localeCompare(String(b), undefined, { numeric: true })
                : (a - b)) * sgn;
        });
    }
    function gridHead(id, cols, dflt) {
        const so = sorts[id] || dflt || {};
        return '<thead><tr>' + cols.map(c => {
            const on = so.k === c.k;
            return `<th class="bc-sortable${c.right ? ' r' : ''}${on ? ' on ' + so.dir : ''}" data-grid="${id}" data-k="${c.k}">${c.label}<em></em></th>`;
        }).join('') + '</tr></thead>';
    }

    function breakdown(id, entries, firstCol) {
        const max = Math.max(1, ...entries.map(e => Math.abs(e.s.net)));
        const cols = [
            { k: 'name', label: firstCol, get: e => e.plain || e.label, html: e => e.label },
            { k: 'n', label: 'Trades', right: true, get: e => e.s.n, html: e => e.s.n },
            { k: 'win', label: 'Win%', right: true, get: e => e.s.winRate, html: e => e.s.winRate.toFixed(0) + '%' },
            { k: 'net', label: basis() === 'gross' ? 'Gross' : 'Net', right: true, get: e => e.s.net,
              html: e => `${inrc(e.s.net, true)}<span class="bc-bar ${e.s.net < 0 ? 'neg' : ''}" style="width:${Math.abs(e.s.net) / max * 100}%"></span>`,
              cls: e => cls(e.s.net) },
            { k: 'avg', label: 'Avg', right: true, get: e => e.s.n ? e.s.net / e.s.n : 0, html: e => inrc(e.s.n ? e.s.net / e.s.n : 0, true), cls: e => cls(e.s.net) },
        ];
        const body = sortRows(id, cols, entries).map(e => '<tr>' + cols.map(c =>
            `<td class="${c.right ? 'r ' : ''}${c.cls ? c.cls(e) : ''}">${c.html(e)}</td>`).join('') + '</tr>').join('');
        return `<table class="bc-tbl">${gridHead(id, cols)}<tbody>${body}</tbody></table>`;
    }

    const HIST_COLS = [
        { k: 'no', label: '#', get: r => r.no, html: r => r.no },
        { k: 'strat', label: o.groupLabel, get: r => r.sname, html: r => `<i class="bc-dot" style="background:${r.color}"></i>${esc(r.sname)}` },
        { k: 'inst', label: 'Instrument', get: r => r.symbol + r.side, html: r => esc(r.symbol) + ' ' + esc(r.side) },
        { k: 'entry', label: 'Entry', get: r => r.entry, html: r => esc(r.entry.slice(0, 16)) },
        { k: 'exit', label: 'Exit', get: r => r.exit, html: r => esc(r.exit.slice(0, 16)) },
        { k: 'strike', label: 'Strike', right: true, get: r => Number(r.strike) || 0, html: r => esc(r.strike ?? '—') },
        { k: 'ep', label: 'Entry ₹', right: true, get: r => Number(r.ep) || 0, html: r => r.ep != null ? Number(r.ep).toFixed(2) : '—' },
        { k: 'xp', label: 'Exit ₹', right: true, get: r => Number(r.xp) || 0, html: r => r.xp != null ? Number(r.xp).toFixed(2) : '—' },
        { k: 'lots', label: 'Lots', right: true, get: r => r.lots, html: r => r.lots },
        { k: 'why', label: 'Exit reason', get: r => r.reason, html: r => esc(r.reason) },
        { k: 'net', label: 'P&amp;L', right: true, get: r => r.net, html: r => inr(r.net, true), cls: r => cls(r.net) },
        { k: 'cum', label: 'Cumulative', right: true, get: r => r.cum, html: r => inr(r.cum) },
    ];
    const HIST_DEFAULT = { k: 'no', dir: 'desc' };

    function heat(rows) {
        const byYm = groupBy(rows, r => r.entry.slice(0, 7));
        const years = [...new Set(rows.map(r => r.entry.slice(0, 4)))].sort();
        let max = 1;
        byYm.forEach(v => { max = Math.max(max, Math.abs(v.reduce((a, r) => a + r.net, 0))); });
        const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        let h = '<table class="bc-heat"><thead><tr><th></th>' + mon.map(m => `<th>${m}</th>`).join('') + '<th>Year</th></tr></thead><tbody>';
        years.forEach(y => {
            let yt = 0;
            h += `<tr><th>${y}</th>`;
            for (let m = 1; m <= 12; m++) {
                const v = byYm.get(y + '-' + String(m).padStart(2, '0'));
                if (!v) { h += '<td></td>'; continue; }
                const t = v.reduce((a, r) => a + r.net, 0); yt += t;
                const a = 0.15 + 0.75 * Math.min(1, Math.abs(t) / max);
                h += `<td style="background:${t >= 0 ? `rgba(22,163,74,${a})` : `rgba(220,38,38,${a})`}" title="${inr(t, true)} · ${v.length} trades">${Math.abs(t) >= 1000 ? (t / 1000).toFixed(1) + 'k' : Math.round(t)}</td>`;
            }
            h += `<td class="${cls(yt)}"><b>${inr(yt, true)}</b></td></tr>`;
        });
        return h + '</tbody></table>';
    }

    function paint() {
        const rows = o.rows();
        if (!rows.length) {
            if (equityChart) { equityChart.destroy(); equityChart = null; }
            box.innerHTML = `<div class="bc-empty">${o.empty()}</div>`;
            return;
        }
        const s = stats(rows);
        const first = rows[0].entry.slice(0, 10), last = rows[rows.length - 1].entry.slice(0, 10);

        const dows = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
        const byDow = groupBy(rows, r => (new Date(r.entry.slice(0, 10) + 'T00:00:00').getDay() + 6) % 7);
        const dowEntries = dows.map((n, i) => byDow.has(i) ? { label: n, s: stats(byDow.get(i)) } : null).filter(Boolean);
        const byYear = [...groupBy(rows, r => r.entry.slice(0, 4))].sort().map(([k, v]) => ({ label: k, s: stats(v) }));
        const bySym = [...groupBy(rows, r => r.symbol)].map(([k, v]) => ({ label: k, s: stats(v) }));
        const bySide = [...groupBy(rows, r => r.side === 'PE' ? 'Put Option Long' : 'Call Option Long')].map(([k, v]) => ({ label: k, s: stats(v) }));
        const byStrat = o.groups().map(g => ({ label: `<i class="bc-dot" style="background:${g.color}"></i>${esc(g.name)}`,
                                                plain: g.name, s: stats(rows.filter(r => r.sid === g.id)) }));
        const byWhy = [...groupBy(rows, r => r.reason || 'unknown')].map(([k, v]) => ({ label: esc(k), s: stats(v) }));

        const pages = Math.ceil(rows.length / PAGE);
        if (page >= pages) page = pages - 1;
        let cum = 0;
        rows.forEach((r, i) => { cum += r.net; r.no = i + 1; r.cum = cum; });
        const hist = sortRows('hist', HIST_COLS, rows, HIST_DEFAULT).slice(page * PAGE, page * PAGE + PAGE);

        box.innerHTML = `
          <div class="bc-meta">${esc(first)} → ${esc(last)} · ${esc(o.meta())}</div>
          <div class="bc-cards">
            ${card(basis() === 'gross' ? 'Gross P&amp;L' : 'Net P&amp;L', inr(s.net, true), basis() === 'gross' ? 'before brokerage (as Quantman)' : 'after brokerage', cls(s.net))}
            ${card(basis() === 'gross' ? 'Net P&amp;L' : 'Gross P&amp;L', inr(basis() === 'gross' ? s.gross - s.brok : s.gross, true), basis() === 'gross' ? 'after brokerage' : 'before brokerage')}
            ${card('Brokerage', inr(-s.brok), 'all trades')}
            ${card('Trades', s.n, s.wins + ' wins · ' + s.losses + ' losses')}
            ${card('Win rate', s.winRate.toFixed(2) + '%', 'win:loss ' + s.wins + ':' + s.losses)}
            ${card('Profit factor', isFinite(s.pf) ? s.pf.toFixed(2) : '∞', 'gross win ÷ gross loss')}
            ${card('Max drawdown', inr(-s.dd), s.ddDays + ' drawdown day' + (s.ddDays === 1 ? '' : 's'), 'bc-neg')}
            ${card('Avg win / loss', inr(s.avgWin, true) + ' / ' + inr(s.avgLoss), 'per trade')}
            ${card('Streak', s.winStreak + ' W / ' + s.lossStreak + ' L', 'unbroken, best / worst')}
            ${card('Best trade', inr(s.best.net, true), esc(s.best.entry.slice(0, 10)) + ' · ' + esc(s.best.sname), 'bc-pos')}
            ${card('Worst trade', inr(s.worst.net), esc(s.worst.entry.slice(0, 10)) + ' · ' + esc(s.worst.sname), 'bc-neg')}
            ${card('Return / drawdown', s.dd > 0 ? (s.net / s.dd).toFixed(2) : '—', 'P&amp;L ÷ max drawdown')}
          </div>

          <div class="bc-sec">Equity curve <small>combined ${basis() === 'gross' ? 'gross' : 'net'} ₹ by trade date; thin lines are each ${o.groupLabel.toLowerCase()} alone</small></div>
          <div class="bc-chart"><canvas class="bc-equity"></canvas></div>

          <div class="bc-sec">P&amp;L breakdown</div>
          <div class="bc-grid3">
            <section class="bc-panel"><h4>By ${o.groupLabel.toLowerCase()}</h4>${breakdown('g-strat', byStrat, o.groupLabel)}</section>
            <section class="bc-panel"><h4>By year</h4>${breakdown('g-year', byYear, 'Year')}</section>
            <section class="bc-panel"><h4>By day of week (entry)</h4>${breakdown('g-dow', dowEntries, 'Day')}</section>
            <section class="bc-panel"><h4>By instrument</h4>${breakdown('g-sym', bySym, 'Instrument')}</section>
            <section class="bc-panel"><h4>By trade type</h4>${breakdown('g-side', bySide, 'Type')}</section>
            <section class="bc-panel"><h4>By exit reason</h4>${breakdown('g-why', byWhy, 'Reason')}</section>
          </div>
          <section class="bc-panel bc-wide"><h4>Month by month</h4>
          <div class="bc-scroll">${heat(rows)}</div></section>

          <div class="bc-sec">Trade history <small>${rows.length} trades · click a header to sort</small>
            <button type="button" class="bc-csv" data-csv="1">Download CSV</button></div>
          <div class="bc-scroll"><table class="bc-tbl bc-hist">${gridHead('hist', HIST_COLS, HIST_DEFAULT)}<tbody>${
            hist.map(r => '<tr>' + HIST_COLS.map(c =>
                `<td class="${c.right ? 'r ' : ''}${c.cls ? c.cls(r) : ''}">${c.html(r)}</td>`).join('') + '</tr>').join('')
          }</tbody></table></div>
          <div class="bc-pager">${pager(page, pages)}</div>`;

        drawEquity(rows);
    }

    function pager(p, n) {
        if (n <= 1) return '';
        const set = new Set([0, 1, p - 1, p, p + 1, n - 2, n - 1].filter(x => x >= 0 && x < n));
        let out = '', prev = -1;
        [...set].sort((a, b) => a - b).forEach(x => {
            if (prev >= 0 && x - prev > 1) out += '<span>…</span>';
            out += `<button type="button" data-page="${x}" class="${x === p ? 'on' : ''}">${x + 1}</button>`;
            prev = x;
        });
        return out;
    }

    function drawEquity(rows) {
        const cv = box.querySelector('canvas.bc-equity');
        if (!cv || typeof Chart === 'undefined') return;
        if (equityChart) { equityChart.destroy(); equityChart = null; }
        const days = [...new Set(rows.map(r => r.entry.slice(0, 10)))].sort();
        const series = (list) => {
            const perDay = new Map();
            list.forEach(r => { const d = r.entry.slice(0, 10); perDay.set(d, (perDay.get(d) || 0) + r.net); });
            let c = 0;
            return days.map(d => { c += perDay.get(d) || 0; return Math.round(c); });
        };
        const css = getComputedStyle(document.documentElement);
        const grid = css.getPropertyValue('--oip-border').trim() || '#e5e7eb';
        const datasets = [{
            label: 'Combined', data: series(rows), borderColor: '#16a34a', backgroundColor: 'rgba(22,163,74,.14)',
            fill: true, borderWidth: 2, pointRadius: 0, tension: 0.15, order: 0,
        }];
        o.groups().forEach(g => {
            datasets.push({ label: g.name, data: series(rows.filter(r => r.sid === g.id)), borderColor: g.color,
                borderWidth: 1, pointRadius: 0, fill: false, tension: 0.15, order: 1, hidden: false });
        });
        equityChart = new Chart(cv, {
            type: 'line', data: { labels: days, datasets },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: { mode: 'index', intersect: false },
                plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 } } },
                           tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${inr(c.parsed.y, true)}` } } },
                scales: {
                    x: { ticks: { maxTicksLimit: 10, font: { size: 10 } }, grid: { display: false } },
                    y: { ticks: { callback: v => inr(v), font: { size: 10 } }, grid: { color: grid } },
                },
            },
        });
    }

    function downloadCsv() {
        const rows = o.rows();
        const head = [o.groupLabel, 'Instrument', 'Type', 'Strike', 'Entry', 'Exit', 'Entry premium', 'Exit premium', 'Lots', 'Exit reason', 'Gross', 'Brokerage', 'Net'];
        const q = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const csv = [head.map(q).join(',')].concat(rows.map(r => [r.sname, r.symbol, r.side, r.strike, r.entry, r.exit, r.ep, r.xp, r.lots,
            r.reason, r.gross.toFixed(2), r.brok.toFixed(2), r.nb.toFixed(2)].map(q).join(','))).join('\n');
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
        a.download = o.csvName || 'combined.csv';
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }

    box.addEventListener('click', (e) => {
        const pg = e.target.closest('[data-page]');
        if (pg) { page = parseInt(pg.dataset.page, 10); paint(); return; }
        if (e.target.closest('[data-csv]')) { downloadCsv(); return; }
        const th = e.target.closest('th[data-grid]');
        if (th) {
            const id = th.dataset.grid, k = th.dataset.k;
            const cur = sorts[id] || (id === 'hist' ? HIST_DEFAULT : null);
            sorts[id] = { k, dir: cur && cur.k === k && cur.dir === 'desc' ? 'asc' : 'desc' };
            if (id === 'hist') page = 0;
            const sc = box.closest('.bt-modal-body') || box, top = sc.scrollTop;
            paint();
            sc.scrollTop = top;
        }
    });
    return { paint, resetPage: () => { page = 0; } };
};

/* Backtest page — "Combined" popups for the Simple Trader group and the 🧲 ST Touch Break group.
 *
 * Both are one popup, made twice (groupCombined): Simple Trader's chips are its seven strategies, ST Touch
 * Break's are its rows (window.MINE_LIVE_TABS — the /algo sub-tabs, each BACKTESTed with that row's own
 * timeframe and numbers, NIFTY, premium 250, no premium stop, exactly as picking the row fills the form).
 *
 * Runs every Simple Trader strategy (each on its own /api/backtest/* endpoint, with the
 * parameters on its own form — strategy 1 with its Quantman preset), then merges the trades into
 * one book: cards, equity curve, P&L breakdowns and the trade history. Ticking / unticking a
 * strategy re-aggregates at once. ₹ figures are the server's (pnl_rupees = net of brokerage,
 * gross_pnl_rupees before it); a run whose options could not be priced falls back to index
 * points × lot value, and says so.
 */
(function () {
    'use strict';

    const btn = document.getElementById('btCombinedBtn');
    if (!btn) return;

    const $ = (id) => document.getElementById(id);
    const val = (id, d) => { const el = $(id); return el && el.value !== '' ? el.value : d; };
    const num = (id, d) => { const v = parseFloat(val(id, NaN)); return Number.isFinite(v) ? v : d; };
    const chk = (id, d) => { const el = $(id); return el ? el.checked : d; };
    const lots = () => Math.max(1, parseInt(val('pcLots', 1), 10) || 1);

    // Fields every Simple Trader form shares, read off its own id prefix.
    function common(p, o) {
        return {
            premium: num(p + 'Premium', o.premium),
            price_options: chk(p + 'PriceOptions', true),
            direction: o.direction,
            sl_pct: num(p + 'SlPct', 0), tsl_x: num(p + 'TslX', 0), tsl_y: num(p + 'TslY', 0),
            entry_start: val(p + 'EntryStart', o.entryStart || '09:15'),
            entry_end: val(p + 'EntryEnd', '15:14'),
            exit_cutoff: val(p + 'ExitCutoff', '15:14'),
            max_trades_per_day: parseInt(val(p + 'MaxTrades', 5), 10),
            lots: lots(), lot_value: o.lot,
            compounding: chk(p + 'Compound', false),
            start_capital: num(p + 'Capital', 25000), per_lot: num(p + 'PerLot', 0),
        };
    }

    // One entry per Simple Trader strategy — keep in step with the strategy dropdown's 🎓 options.
    const SIMPLE_DEFS = [
        { id: 'simpler_trader', name: '1. BB PP ST RSI WR', ep: 'st-bb-rsi', symbol: 'NIFTY', interval: '3minute', lot: 65, side: 'CE',
          // Quantman's run, fixed here exactly as applySimplerTraderPreset() sets the form.
          build: () => ({
              premium: 250, price_options: chk('sbrPriceOptions', true), direction: 'long', htf: 'off',
              use_rsi: true, use_bb: true, use_st: true, use_pivot: true, use_wr: true, require_green: false,
              rsi_level: 70, rsi_lower: 30, rsi_period: 14, st_period: 10, st_mult: 2,
              bb_period: 15, bb_mult: 3, bb_offset: 0, wr_period: 14, wr_level: -30,
              sl_pct: 13, tsl_x: 15, tsl_y: 10,
              entry_start: '09:15', entry_end: '15:14', exit_cutoff: '15:14', max_trades_per_day: 1,
              lots: lots(), lot_value: 65, compounding: false, start_capital: 25000, per_lot: 0,
          }) },
        { id: 'st_ema_rsi_cci', name: '2. ST EMA RSI CCI WR CPR BB', ep: 'st-ema-rsi-cci', symbol: 'NIFTY', interval: '3minute', lot: 65, side: 'PE',
          build: () => ({
              ...common('sec', { premium: 250, direction: val('secDirection', 'short'), lot: 65 }),
              st_period: num('secStLen', 10), st_mult: num('secStFactor', 2), ema_period: num('secEmaLen', 200),
              rsi_period: num('secRsiLen', 20), rsi_entry: num('secRsiEntry', 30), rsi_exit: num('secRsiExit', 45),
              cci_period: num('secCciLen', 20), cci_level: num('secCciLevel', -200),
              wr_period: num('secWrLen', 14), wr_level: num('secWrLevel', -85),
              bb_period: num('secBbLen', 20), bb_mult: num('secBbMult', 2),
              cpr_narrow: num('secCprNarrow', 0.2), use_cpr_narrow: chk('secUseCprNarrow', true),
          }) },
        { id: 'st_rsi_pp_bb', name: '3. ST RSI PP BB', ep: 'st-rsi-pp-bb', symbol: 'NIFTY', interval: '3minute', lot: 65, side: 'CE',
          build: () => ({
              ...common('srp', { premium: 250, direction: 'long', entryStart: '09:31', lot: 65 }),
              st_period: num('srpStLen', 10), st_mult: num('srpStFactor', 2.3),
              rsi_period: num('srpRsiLen', 14), rsi_level: num('srpRsiLevel', 70),
              bb_period: num('srpBbLen', 15), bb_mult: num('srpBbMult', 3),
          }) },
        { id: 'st_rsi_pp_bb_fut', name: '4. ST RSI PP BB Fut', ep: 'st-rsi-pp-bb-fut', symbol: 'NIFTY', interval: '5minute', lot: 65, side: 'CE',
          build: () => ({
              ...common('srf', { premium: 250, direction: 'long', lot: 65 }),
              require_green: chk('srfGreen', true),
              st_period: num('srfStLen', 10), st_mult: num('srfStFactor', 1.8),
              rsi_period: num('srfRsiLen', 20), rsi_level: num('srfRsiLevel', 68),
              bb_period: num('srfBbLen', 20), bb_mult: num('srfBbMult', 3.3),
          }) },
        { id: 'st_rsi_cci_wr_pp_bb_fut', name: '5. ST RSI CCI WR PP BB Fut', ep: 'st-rsi-cci-wr-pp-bb-fut', symbol: 'NIFTY', interval: '3minute', lot: 65, side: 'CE',
          build: () => ({
              ...common('scw', { premium: 250, direction: 'long', lot: 65 }),
              st_period: num('scwStLen', 10), st_mult: num('scwStFactor', 2),
              rsi_period: num('scwRsiLen', 14), rsi_level: num('scwRsiLevel', 78),
              cci_period: num('scwCciLen', 20), cci_level: num('scwCciLevel', 280),
              wr_period: num('scwWrLen', 14), wr_level: num('scwWrLevel', -15),
              bb_period: num('scwBbLen', 20), bb_mult: num('scwBbMult', 3),
          }) },
        { id: 'st_rsi_cpr_bb_sx', name: '6. ST RSI CPR BB Sensex', ep: 'st-rsi-cpr-bb-sx', symbol: 'SENSEX', interval: '5minute', lot: 20, side: 'CE', minStart: '2023-10-24',
          build: () => ({
              ...common('scx', { premium: 500, direction: 'long', lot: 20 }),
              st_period: num('scxStLen', 10), st_mult: num('scxStFactor', 1.5),
              rsi_period: num('scxRsiLen', 14), rsi_level: num('scxRsiLevel', 70),
              bb_period: num('scxBbLen', 20), bb_mult: num('scxBbMult', 2.5),
              cpr_wide: num('scxCprWide', 0.35),
          }) },
        { id: 'st_rsi_cpr_bb_wr_sx', name: '7. ST RSI CPR BB WR SX2', ep: 'st-rsi-cpr-bb-wr-sx', symbol: 'SENSEX', interval: '5minute', lot: 20, side: 'CE', minStart: '2023-10-24',
          build: () => ({
              ...common('swx', { premium: 500, direction: 'long', lot: 20 }),
              st_period: num('swxStLen', 10), st_mult: num('swxStFactor', 1.5),
              rsi_period: num('swxRsiLen', 14), rsi_level: num('swxRsiLevel', 68), rsi_cap: num('swxRsiCap', 80),
              wr_period: num('swxWrLen', 14), wr_level: num('swxWrLevel', -10),
              bb_period: num('swxBbLen', 20), bb_mult: num('swxBbMult', 2.5),
              cpr_narrow: num('swxCprNarrow', 0.2),
          }) },
    ];
    // One entry per 🧲 ST Touch Break row — the same list (and serial) as the strategy dropdown and /algo's sub-tabs.
    // The payload is what stbApplyRow() + the BACKTEST button send for that row; only OPT P&L and Lots are the page's.
    const STB_DEFS = (window.MINE_LIVE_TABS || []).map((t, i) => ({
        id: 'stb_' + t.n, name: `${i + 1}. ${t.tab}`, ep: 'st-touch-break', symbol: 'NIFTY', interval: t.timeframe, lot: 65,
        side: t.direction === 'short' ? 'PE' : 'CE',
        build: () => ({
            direction: t.direction, modes: t.modes, stop_mode: t.stop_mode, outside_bar: t.outside_bar,
            st_period: t.st_period, st_mult: t.st_mult, touch_tol_pct: t.touch_tol_pct,
            min_wick_pct: t.min_wick_pct, min_body_pct: t.min_body_pct,
            entry_start: t.entry_start, entry_end: t.entry_end, exit_cutoff: t.exit_cutoff,
            max_trades_per_day: t.max_trades_per_day,
            premium: 250, price_options: chk('stbPriceOptions', true), sl_pct: 0, tsl_x: 0, tsl_y: 0,
            lots: lots(), lot_value: 65, compounding: false, start_capital: 25000, per_lot: 0,
        }),
    }));

    const SENSEX_OPTIONS_FROM = '27 May 2024';
    const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4f46e5', '#0d9488', '#be123c'];

    // cfg: { prefix (element ids), defs, title, subtitle, csvName, basis ('gross'|'net'), from (default From date) }
    function groupCombined(cfg) {
    const P = cfg.prefix, DEFS = cfg.defs;
    DEFS.forEach((d, i) => { d.color = COLORS[i % COLORS.length]; });

    // id → {status:'idle'|'running'|'done'|'error', trades, msg, progress, warning, unpriced}
    const state = {};
    DEFS.forEach(d => { state[d.id] = { status: 'idle', trades: [] }; });
    const selected = new Set(DEFS.map(d => d.id));
    let running = false, cancelled = false;
    // Simple Trader: Quantman books gross (before brokerage), so Gross is its default for comparing with it.
    let basis = cfg.basis || 'gross';
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    let modal = null, report = null;

    const todayIso = () => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };

    // ── Normalise one server trade into the combined book's row ──────────────────────────────
    function norm(def, t) {
        const lv = def.lot * (t.lots || lots());
        const gross = t.gross_pnl_rupees != null ? Number(t.gross_pnl_rupees) : Number(t.pnl || 0) * lv;
        const net = t.pnl_rupees != null ? Number(t.pnl_rupees) : gross;
        return {
            sid: def.id, sname: def.name, color: def.color, symbol: def.symbol,
            side: t.option_type || def.side,
            strike: t.strike, entry: String(t.entry_time || ''), exit: String(t.exit_time || ''),
            ep: t.entry_premium, xp: t.exit_premium, lots: t.lots || lots(), prem: t.entry_premium, lotSize: def.lot,
            gross, brok: gross - net, nb: net, net: basis === 'gross' ? gross : net, reason: t.exit_reason || '',
        };
    }

    function applyBasis() {
        DEFS.forEach(d => state[d.id].trades.forEach(r => { r.net = basis === 'gross' ? r.gross : r.nb; }));
    }

    function book() {
        const rows = [];
        DEFS.forEach(d => { if (selected.has(d.id)) state[d.id].trades.forEach(r => rows.push(r)); });
        rows.sort((a, b) => a.entry.localeCompare(b.entry) || a.sid.localeCompare(b.sid));
        return rows;
    }

    // ── Running ─────────────────────────────────────────────────────────────────────────────
    async function runOne(def, from, to) {
        const st = state[def.id];
        st.status = 'running'; st.trades = []; st.msg = ''; st.progress = null; st.warning = '';
        paintStatus();
        const start = def.minStart && from < def.minStart ? def.minStart : from;
        const payload = { symbol: def.symbol, interval: def.interval, start_date: start, end_date: to, ...def.build() };
        try {
            const res = await fetch('/api/backtest/' + def.ep, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            let data = await res.json();
            if (data.success && data.task_id) {
                while (!cancelled) {
                    await new Promise(r => setTimeout(r, 2000));
                    const p = await (await fetch('/api/backtest/st-bb-rsi/status/' + data.task_id)).json();
                    if (p.status === 'running') {
                        st.progress = p.progress || null; paintStatus();
                        continue;
                    }
                    data = p; break;
                }
                if (cancelled) { st.status = 'idle'; return; }
            }
            if (!data.success) throw new Error(data.error || 'Backtest failed');
            st.trades = (data.trades || []).filter(t => t.pnl != null).map(t => norm(def, t));
            st.warning = data.warning || '';
            st.unpriced = !(data.option || (data.summary && data.summary.pnl_basis === 'option'));
            st.from = start;
            st.range = from + '|' + to;
            st.status = 'done';
        } catch (e) {
            st.status = 'error'; st.msg = e.message || String(e);
        }
        paintStatus(); paintResults();
    }

    async function runAll(list) {
        if (running) return false;
        running = true; cancelled = false;
        const from = $(P + 'From').value || cfg.from || '2019-04-01', to = $(P + 'To').value || todayIso();
        $(P + 'Run').textContent = 'Stop'; $(P + 'Best').disabled = true;
        const todo = list || DEFS.filter(d => selected.has(d.id));
        for (const d of todo) { if (cancelled) break; await runOne(d, from, to); }
        running = false;
        $(P + 'Run').textContent = 'Run again'; $(P + 'Best').disabled = false;
        paintStatus();
        return !cancelled;
    }

    // ⚡ Optimize: every strategy not yet run is run, then the Combined — Best Params search ticks the set.
    let bestNote = '';
    async function findBest() {
        const from = $(P + 'From').value || cfg.from || '2019-04-01', to = $(P + 'To').value || todayIso();
        const key = from + '|' + to;
        if (!(await runAll(DEFS.filter(d => !(state[d.id].status === 'done' && state[d.id].range === key))))) return;
        const items = DEFS.filter(d => state[d.id].status === 'done').map(d => ({ id: d.id, rows: state[d.id].trades }));
        const best = window.BtCombinedOptimize(items);
        bestNote = window.BtCombinedOptimize.note(best, items.length, 'strategy').replace(/strategys/g, 'strategies');
        if (best.ids.length) { selected.clear(); best.ids.forEach(id => selected.add(id)); }
        report.resetPage(); paintStatus(); paintResults();
    }

    // ── DOM ─────────────────────────────────────────────────────────────────────────────────
    function build() {
        modal = document.createElement('div');
        modal.className = 'bt-modal-overlay bc-overlay';
        modal.style.display = 'none';
        modal.innerHTML = `
          <div class="bt-modal-box bc-box">
            <div class="bt-modal-hdr">
              <div class="bt-modal-hdr-text">
                <div class="bt-modal-title">${esc(cfg.title)}</div>
                <div class="bt-modal-subtitle">${esc(cfg.subtitle)}</div>
              </div>
              <div class="bc-dates">
                <label>From <input type="date" id="${P}From"></label>
                <label>To <input type="date" id="${P}To"></label>
                <label>P&amp;L <select id="${P}Basis"><option value="gross"${basis === 'gross' ? ' selected' : ''}>Gross</option><option value="net"${basis === 'net' ? ' selected' : ''}>Net of brokerage</option></select></label>
                <button type="button" id="${P}Best" class="bc-run" title="Run every strategy, then search for the set whose combined book has the best net P&amp;L ÷ max drawdown, and tick only those">⚡ Optimize</button>
                <button type="button" id="${P}CapBtn" class="bc-run bc-run-alt" title="The money needed to run the ticked strategies live: the most premium ever open at once, plus the combined book's worst drawdown — and why">💰 Capital needed</button>
                <button type="button" id="${P}Run" class="bc-run">Run combined</button>
              </div>
              <button class="bt-modal-close" id="${P}Close" type="button">✕</button>
            </div>
            <div class="bt-modal-body bc-body">
              <div class="bc-strats" id="${P}Strats"></div>
              <div id="${P}Note" class="bc-note"></div>
              <div id="${P}Cap" class="bc-cap" style="display:none"></div>
              <div id="${P}Results"></div>
            </div>
          </div>`;
        document.body.appendChild(modal);
        $(P + 'From').value = cfg.from || '2019-04-01';
        $(P + 'To').value = todayIso();
        $(P + 'Close').addEventListener('click', close);
        report = window.BtCombinedReport({
            box: $(P + 'Results'), rows: book, basis: () => basis, csvName: cfg.csvName,
            groups: () => DEFS.filter(d => selected.has(d.id) && state[d.id].trades.length),
            meta: () => `${selected.size} of ${DEFS.length} strategies`,
            empty: () => running ? 'Running the strategies — results appear as each one finishes…'
                : (DEFS.some(d => state[d.id].status === 'done') ? 'No trades for the selected strategies.' : 'Pick strategies above and press Run combined.'),
        });
        $(P + 'Basis').addEventListener('change', (e) => { basis = e.target.value; applyBasis(); report.resetPage(); paintResults(); });
        modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && modal.style.display !== 'none') close(); });
        $(P + 'Run').addEventListener('click', () => {
            if (running) { cancelled = true; $(P + 'Run').textContent = 'Stopping…'; return; }
            runAll();
        });
        $(P + 'Strats').addEventListener('change', (e) => {
            const id = e.target.dataset && e.target.dataset.sid;
            if (!id) return;
            e.target.checked ? selected.add(id) : selected.delete(id);
            bestNote = '';
            report.resetPage(); paintStatus(); paintResults();
        });
        $(P + 'Best').addEventListener('click', findBest);
        $(P + 'CapBtn').addEventListener('click', () => {
            capOpen = !capOpen;
            $(P + 'CapBtn').classList.toggle('on', capOpen);
            $(P + 'Cap').style.display = capOpen ? '' : 'none';
            paintCap();
        });
        paintStatus(); paintResults();
    }

    let capOpen = false;
    function paintCap() {
        if (!capOpen || !modal) return;
        window.BtCombinedCapital($(P + 'Cap'), DEFS.filter(d => selected.has(d.id) && state[d.id].trades.length)
            .map(d => ({ name: d.name, color: d.color, rows: state[d.id].trades })));
    }
    function paintResults() { if (report) report.paint(); paintCap(); }

    function open() {
        if (!modal) build();
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        // First open runs the combined book; afterwards the finished one is just shown.
        if (!running && DEFS.every(d => state[d.id].status === 'idle')) runAll();
    }
    function close() {
        modal.style.display = 'none';
        document.body.style.overflow = '';
    }

    function paintStatus() {
        if (!modal) return;
        $(P + 'Strats').innerHTML = DEFS.map(d => {
            const s = state[d.id];
            let tag = '';
            if (s.status === 'running') tag = s.progress ? `pricing ${s.progress.done}/${s.progress.total}` : 'running…';
            else if (s.status === 'done') tag = s.trades.length + ' trades';
            else if (s.status === 'error') tag = 'failed';
            else tag = 'waiting';
            return `<label class="bc-chip ${s.status}" title="${esc(s.msg || s.warning || d.symbol + ' ' + d.side)}">
                <input type="checkbox" data-sid="${d.id}" ${selected.has(d.id) ? 'checked' : ''}>
                <i style="background:${d.color}"></i><b>${esc(d.name)}</b><span>${esc(tag)}</span></label>`;
        }).join('');
        const notes = [];
        if (bestNote) notes.push(`<b>${esc(bestNote)}</b>`);
        DEFS.forEach(d => {
            const s = state[d.id];
            if (s.status === 'error') notes.push(`<b>${esc(d.name)}</b>: ${esc(s.msg)}`);
            else if (s.status === 'done' && s.unpriced) notes.push(`<b>${esc(d.name)}</b>: option premiums unavailable — showing index points × lot value.`);
            else if (s.status === 'done' && s.warning) notes.push(`<b>${esc(d.name)}</b>: ${esc(s.warning)}`);
        });
        // ICICI Breeze serves no SENSEX (BFO) option candles before this date, at any interval, so those
        // signals cannot be priced and are left out of the totals rather than guessed.
        const sx = DEFS.filter(d => d.symbol === 'SENSEX' && selected.has(d.id) && state[d.id].status === 'done');
        if (sx.length) {
            notes.unshift(`<span class="bc-warn">SENSEX trades before ${SENSEX_OPTIONS_FROM} are excluded: no option data exists for them `
                + `(the broker feed serves SENSEX option candles only from ${SENSEX_OPTIONS_FROM}). `
                + `${sx.length > 1 ? 'Strategies' : 'Strategy'} ${esc(sx.map(d => d.name.split('.')[0]).join(' and '))} therefore cover that date onwards only, so the combined totals are lower than a full-history run would be.</span>`);
        }
        $(P + 'Note').innerHTML = notes.join('<br>');
    }

    return { open };
    }

    const simple = groupCombined({
        prefix: 'bc', defs: SIMPLE_DEFS, title: 'Combined — Simple Trader', basis: 'gross', from: '2019-04-01',
        subtitle: 'All selected strategies merged into one book. ₹ = premium points × lots × lot size, net of brokerage.',
        csvName: 'combined_simpler_trader.csv',
    });
    const stb = STB_DEFS.length ? groupCombined({
        prefix: 'bcs', defs: STB_DEFS, title: 'Combined — ST Touch Break', basis: 'net', from: '2026-11-01',
        subtitle: 'Every ticked ST Touch Break row merged into one book, each BACKTESTed with its own timeframe and numbers (NIFTY, premium ≈250, no premium stop). ₹ = premium points × lots × lot size.',
        csvName: 'combined_st_touch_break.csv',
    }) : null;

    // ── Header button: the Simple Trader group, the ST Touch Break group, and Candle Breakout (its Best Params rows) ──
    const group = $('strategyStatusSelect'), strat = $('strategySelect');
    const isSc = () => !!strat && strat.value === 'second_candle';
    const isStb = () => !!stb && !!group && group.value === 'st_touch_break';
    function syncVisibility() {
        btn.style.display = !group || group.value === 'simpler' || isStb() || isSc() ? '' : 'none';
        btn.title = isSc() ? 'Merge Candle Breakout Best Params rows into one book — and find the set that combines best'
                  : isStb() ? 'Run every ST Touch Break row and see them merged into one book'
                           : 'Run every Simple Trader strategy and see them merged into one book';
    }
    // The group change re-fills the strategy list in its own handler, so read it a tick later.
    if (group) group.addEventListener('change', () => setTimeout(syncVisibility, 0));
    if (strat) strat.addEventListener('change', () => setTimeout(syncVisibility, 0));
    syncVisibility();
    btn.addEventListener('click', () => (isSc() && window.BtScCombined ? window.BtScCombined.open()
        : isStb() ? stb.open() : simple.open()));
})();

/* ⚡ Optimize for a Combined popup — the same search as the ⚡ Optimize of "Combined — Best Params"
 * (backtest.js, wireSbrCombined → optimize): the books merged trade by trade in entry order, always on
 * NET ₹ (whatever the P&L box shows); start from the best single profitable book by net ÷ max drawdown,
 * keep adding the one that raises net ÷ max drawdown most, stop when none does.
 *
 *   BtCombinedOptimize(items) → { ids, net, dd, score, single } | { ids: [], msg }
 *     items: [{id, rows: [{entry, nb}]}] — every candidate's finished trades (nb = net ₹)
 *   BtCombinedOptimize.note(result, n, noun) → the line the popup shows, worded as backtest.js words it.
 */
window.BtCombinedOptimize = function (items) {
    'use strict';
    const books = items.map(it => it.rows.map(r => ({ e: r.entry, n: Number.isFinite(r.nb) ? r.nb : 0 })));
    function bookStats(ks) {
        const all = [].concat(...ks.map(k => books[k])).sort((a, b) => a.e < b.e ? -1 : a.e > b.e ? 1 : 0);
        let cum = 0, peak = 0, dd = 0;
        all.forEach(x => { cum += x.n; peak = Math.max(peak, cum); dd = Math.max(dd, peak - cum); });
        return { net: cum, dd, score: dd > 0 ? cum / dd : (cum > 0 ? Infinity : 0) };
    }
    let chosen = [], best = null;
    items.forEach((it, k) => { const st = bookStats([k]); if (st.net > 0 && (!best || st.score > best.score)) { best = st; chosen = [k]; } });
    if (!best) return { ids: [], msg: 'No row made a net profit — nothing to combine.' };
    const single = best;
    for (;;) {
        let step = null;
        items.forEach((it, k) => {
            if (chosen.includes(k)) return;
            const st = bookStats([...chosen, k]);
            if (st.score > (step ? step.st.score : best.score) + 1e-9) step = { k, st };
        });
        if (!step) break;
        chosen.push(step.k); best = step.st;
    }
    return { ids: chosen.map(k => items[k].id), ...best, single };
};
window.BtCombinedOptimize.note = function (r, n, noun) {
    if (!r.ids.length) return r.msg;
    const inr0 = (v) => '₹' + Math.round(v).toLocaleString('en-IN');
    const c = r.ids.length;
    return `⚡ Optimize searched ${n} ${noun}s: ${c} ${noun}${c > 1 ? 's' : ''} `
        + `give net ${inr0(r.net)} on a max drawdown of ${inr0(r.dd)} (net ÷ DD ${isFinite(r.score) ? r.score.toFixed(2) : '∞'}`
        + (c > 1 ? `; the best single ${noun} alone: ${isFinite(r.single.score) ? r.single.score.toFixed(2) : '∞'}` : `; no ${noun} added to it improved that`)
        + '). Only those are ticked.';
};

/* 💰 Capital needed for a Combined popup — the same reading as "Combined — Best Params"
 * (backtest.js, wireSbrCombined → capitalNeeded / paintCapital): an option BUY is paid in full, and every
 * ticked row runs as its own algo, so overlapping trades hold their premiums together. Need = the most
 * premium ever open at once (entry premium × lots × lot size) + the merged NET book's worst drawdown.
 * The one difference: each trade carries its own lot size (`lotSize`), since a popup may mix NIFTY and
 * SENSEX rows; that popup reads one Lot value box for all.
 *
 *   BtCombinedCapital(box, list) — list: [{name, color, rows: [{entry, exit, prem, lots, lotSize, nb}]}]
 */
window.BtCombinedCapital = function (box, list) {
    'use strict';
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    if (!box) return;
    if (!list.length) { box.innerHTML = '<div class="bc-meta">Tick at least one row to see the capital it needs.</div>'; return; }
    const ev = [], perRow = [], days = new Map();
    let unpriced = 0;
    list.forEach(it => {
        let max = 0, maxLots = 0;
        const mine = new Map();
        it.rows.forEach(e => {
            const ep = Number(e.prem), lots = Math.max(1, Number(e.lots) || 1);
            if (!(ep > 0) || !e.entry) { unpriced++; return; }
            const cost = ep * lots * (e.lotSize || 1);
            max = Math.max(max, cost); maxLots = Math.max(maxLots, lots);
            ev.push({ t: e.entry, d: cost, it, open: true });
            ev.push({ t: e.exit || e.entry, d: -cost, it, open: false });
            const day = String(e.entry).slice(0, 10);
            mine.set(day, (mine.get(day) || 0) + 1);
            const dd = days.get(day) || { n: 0, rows: new Map() };
            dd.n++; dd.rows.set(it, (dd.rows.get(it) || 0) + 1);
            days.set(day, dd);
        });
        let rowDay = 0, rowDayAt = '';
        mine.forEach((n, d) => { if (n > rowDay) { rowDay = n; rowDayAt = d; } });
        perRow.push({ it, max, maxLots, trades: [...mine.values()].reduce((a, n) => a + n, 0), days: mine.size, rowDay, rowDayAt });
    });
    const hist = [0, 0, 0, 0];
    let busy = null;
    days.forEach((v, d) => {
        hist[Math.min(v.n, 4) - 1]++;
        if (!busy || v.n > busy.n || (v.n === busy.n && d > busy.day)) busy = { day: d, ...v };
    });
    const pd = { days: days.size, trades: [...days.values()].reduce((a, v) => a + v.n, 0), hist, busy,
                 multi: [...days.values()].filter(v => v.rows.size > 1).length };
    // At one minute an exit settles before an entry: the sale's premium is usable again intraday.
    ev.sort((a, b) => a.t < b.t ? -1 : a.t > b.t ? 1 : (a.open === b.open ? 0 : a.open ? 1 : -1));
    const open = new Map();
    let cur = 0, peak = 0, at = '', who = [], nOpen = 0, maxOpen = 0, maxOpenAt = '';
    ev.forEach(x => {
        cur += x.d;
        nOpen += x.open ? 1 : -1;
        if (nOpen > maxOpen) { maxOpen = nOpen; maxOpenAt = x.t; }
        open.set(x.it, (open.get(x.it) || 0) + (x.open ? 1 : -1));
        if (x.open && cur > peak + 1e-6) { peak = cur; at = x.t; who = [...open].filter(([, n]) => n > 0).map(([k]) => k); }
    });
    // The merged book on NET ₹, in entry order.
    const all = [].concat(...list.map(it => it.rows)).sort((a, b) => a.entry < b.entry ? -1 : a.entry > b.entry ? 1 : 0);
    let cum = 0, hi = 0, ddv = 0;
    all.forEach(e => { cum += Number(e.nb) || 0; hi = Math.max(hi, cum); ddv = Math.max(ddv, hi - cum); });

    const inr = (v) => (v < 0 ? '−₹' : '₹') + Math.abs(Math.round(v)).toLocaleString('en-IN');
    const total = peak + ddv;
    const sumMax = perRow.reduce((a, p) => a + p.max, 0);
    const stamp = at ? String(at).replace('T', ' ') : '—';
    const grows = perRow.some(p => p.maxLots > 1);
    const sizes = [...new Set([].concat(...list.map(it => it.rows.map(e => e.lotSize))).filter(Boolean))];
    const card = (l, v, sub, cls = '') => `<div class="bc-card ${cls}"><div class="bc-card-l">${l}</div>`
        + `<div class="bc-card-v">${v}</div><div class="bc-card-s">${sub}</div></div>`;
    box.innerHTML = `<h4>💰 Capital needed to run these ${list.length} row${list.length > 1 ? 's' : ''} live</h4>
        <div class="bc-cards">
            ${card('Total capital needed', inr(total), 'premium peak + drawdown buffer', 'bc-total')}
            ${card('① Premium open at once (peak)', inr(peak), `${esc(stamp)} · ${who.length} position${who.length === 1 ? '' : 's'} open`)}
            ${card('② Drawdown buffer', inr(ddv), 'worst fall of the merged net book')}
            ${card('Net P&L of the book', `<span class="${cum >= 0 ? 'bc-pos' : 'bc-neg'}">${inr(cum)}</span>`,
                   total > 0 ? `${(cum / total * 100).toFixed(0)} % on the capital needed` : '')}
        </div>
        ${unpriced ? `<div class="bc-note" style="margin-top:8px">${unpriced} trade${unpriced > 1 ? 's have' : ' has'} no option premium (index points only), so ${unpriced > 1 ? 'they are' : 'it is'} left out of ①. Tick OPT P&amp;L and run again for the full figure.</div>` : ''}
        <ul class="bc-cap-why">
            <li><b>Why ①:</b> an option buy is paid in full when it is placed — the broker blocks the whole premium
                (entry premium × lots × lot size ${esc(sizes.join(' / ') || '—')}). Each ticked row runs as its own algo, so when their trades
                overlap they all hold premium together. Replaying every trade of the ticked rows, the most open at one
                moment was <b>${inr(peak)}</b> at ${esc(stamp)}${who.length ? ` (${who.map(it => esc(it.name)).join(', ')})` : ''}.
                Without that much free cash one of those entries is rejected for margin and the live book is no longer the backtest's.</li>
            <li><b>Why ②:</b> the merged book once fell <b>${inr(ddv)}</b> from a high before recovering (net of brokerage).
                Money lost in that run is money no longer in the account, so the peak premium must still be affordable
                after it — hence ① + ②.</li>
            <li><b>Not just the sum:</b> the largest single trade of every ticked row added together is ${inr(sumMax)};
                ① is ${sumMax > peak ? 'smaller because those rows never all held their biggest trade at the same moment'
                : 'the same because those trades did overlap'}.</li>
            <li><b>Assumes</b> today's lot size${sizes.length > 1 ? 's' : ''} (${esc(sizes.join(' / ') || '—')}) and the lots each trade was booked with${grows
                ? ' — some rows were booked with more than 1 lot, and those trades are counted at their booked size' : ' (1 lot)'};
                a brokerage/charges allowance is already inside ② as the book is net. Option buying needs no SPAN margin
                beyond the premium.</li>
        </ul>
        <h4 style="margin-top:14px">📅 Trades on the same day</h4>
        <div class="bc-cards">
            ${card('Days with a trade', pd.days.toLocaleString('en-IN'), `${pd.trades.toLocaleString('en-IN')} trades · ${pd.days ? (pd.trades / pd.days).toFixed(2) : '0'} a day on average`)}
            ${card('Busiest day', pd.busy ? `${pd.busy.n} trades` : '—', pd.busy ? esc(pd.busy.day) : '')}
            ${card('Most open at once', `${maxOpen}`, maxOpenAt ? esc(String(maxOpenAt).replace('T', ' ')) : '')}
            ${card('Days 2+ rows traded', pd.multi.toLocaleString('en-IN'), pd.days ? `${(pd.multi / pd.days * 100).toFixed(0)} % of trading days` : '')}
        </div>
        <div class="bc-scroll" style="margin-top:8px"><table class="bc-tbl">
            <thead><tr><th>Trades that day</th><th>Days</th><th>Share of days</th></tr></thead>
            <tbody>${['1', '2', '3', '4 or more'].map((l, i) => `<tr><td>${l}</td><td>${pd.hist[i].toLocaleString('en-IN')}</td>`
                + `<td>${pd.days ? (pd.hist[i] / pd.days * 100).toFixed(1) : '0'} %</td></tr>`).join('')}</tbody>
        </table></div>
        ${pd.busy ? `<div class="bc-meta">Busiest day ${esc(pd.busy.day)}: ${[...pd.busy.rows].map(([it, n]) => `${esc(it.name)} × ${n}`).join(', ')}.
            Trades on one day are not all open together — a row takes its next trade only after the last one closed —
            so the capital follows "Most open at once", not the day's count.</div>` : ''}
        <div class="bc-scroll" style="margin-top:8px"><table class="bc-tbl">
            <thead><tr><th>Row</th><th>Trades</th><th>Days traded</th><th>Most trades in a day</th><th>Biggest single trade</th><th>Max lots</th><th>Open at the peak</th></tr></thead>
            <tbody>${perRow.map(p => `<tr><td><i class="bc-dot" style="background:${p.it.color}"></i>${esc(p.it.name)}</td>`
                + `<td>${p.trades}</td><td>${p.days}</td><td>${p.rowDay}${p.rowDayAt ? ` <small>(${esc(p.rowDayAt)})</small>` : ''}</td>`
                + `<td>${inr(p.max)}</td><td>${p.maxLots}</td><td>${who.includes(p.it) ? 'yes' : '—'}</td></tr>`).join('')}</tbody>
        </table></div>`;
};

/* Backtest page — "Combined" popup for Candle Breakout (🔴 Live group).
 *
 * Its chips are the rows of the ⚡ Candle Breakout Best Params boards (30s / 1m / 2m / 3m / 5m).
 * Each ticked row is BACKTESTed on its own (/api/backtest/second-candle, that row's timeframe,
 * candle, direction and SL:Target, the page's exit time, OPT P&L and Lots), and the trades are
 * merged into one book. So the 1m-5m rows are booked on real option candles here, not on the
 * board's Black-Scholes premiums, and their figures can differ from the board.
 * ⚡ Optimize backtests every row still missing, then ticks the set the Combined — Best Params search picks.
 */
(function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const inr = (v, sign) => (v < 0 ? '-' : (sign && v > 0 ? '+' : '')) + '₹' + Math.round(Math.abs(v)).toLocaleString('en-IN');
    const brokerage = (lots) => lots <= 5 ? ({ 1: 103, 2: 158, 3: 213, 4: 268, 5: 330 }[Math.max(1, Math.floor(lots))] || 103) : 330 + (Math.floor(lots) - 5) * 62;
    const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4f46e5', '#0d9488', '#be123c'];
    const DIRS = { both: [true, true], long: [true, false], short: [false, true] };

    let defs = [], boardKey = '';          // one per board row
    const state = {};                       // def id → {status, trades, msg, progress, warning, unpriced}
    const selected = new Set();
    let running = false, cancelled = false, basis = 'net', modal = null, report = null, bestNote = '';

    const lots = () => Math.max(1, parseInt(($('scLots') || {}).value || 1, 10) || 1);
    const lotValue = () => Math.max(1, parseFloat(($('scLotValue') || {}).value || 65) || 65);
    const optionPnl = () => !!($('scOptionPnl') && $('scOptionPnl').checked);

    // ── Board → chip definitions ──────────────────────────────────────────────────────────
    function syncDefs() {
        const b = window.BtScBoard;
        if (!b || !b.data) return false;
        const key = JSON.stringify([b.data.symbol, b.data.cached_at, (b.data.timeframes || []).map(g => (g.results || []).length)]);
        if (key === boardKey) return true;
        boardKey = key;
        defs = [];
        (b.data.timeframes || []).forEach(g => (g.results || []).forEach((r, i) => {
            defs.push({
                id: `${g.tf_label}|${r.candle_index}|${r.direction}|${r.rr_ratio}`,
                tf: g.tf_label, rank: i + 1, interval: r.interval, candle: r.candle_index, dir: r.direction, rr: r.rr_ratio,
                name: `${g.tf_label} · C${r.candle_index} · ${r.direction} · 1:${r.rr_ratio}`,
                symbol: b.data.symbol || 'NIFTY',
            });
        }));
        defs.forEach((d, i) => { d.color = COLORS[i % COLORS.length]; if (!state[d.id]) state[d.id] = { status: 'idle', trades: [] }; });
        selected.clear();
        // Start with each board's leader ticked.
        defs.filter(d => d.rank === 1).forEach(d => selected.add(d.id));
        bestNote = '';
        return true;
    }

    function norm(def, t) {
        const n = t.lots || lots();
        let gross, net;
        if (t.gross_pnl_rupees != null) { gross = Number(t.gross_pnl_rupees); net = t.pnl_rupees != null ? Number(t.pnl_rupees) : gross; }
        else { gross = Number(t.pnl || 0) * lotValue() * n; net = gross - brokerage(n); }
        return {
            sid: def.id, sname: def.name, color: def.color, symbol: def.symbol,
            side: t.option_type || (t.type === 'Short' ? 'PE' : 'CE'), strike: t.strike,
            entry: String(t.entry_time || ''), exit: String(t.exit_time || ''),
            ep: t.entry_premium != null ? t.entry_premium : t.entry_price, xp: t.exit_premium != null ? t.exit_premium : t.exit_price,
            lots: n, prem: t.entry_premium, lotSize: lotValue(),
            gross, brok: gross - net, nb: net, net: basis === 'gross' ? gross : net, reason: t.exit_reason || '',
        };
    }
    function applyBasis() { defs.forEach(d => state[d.id].trades.forEach(r => { r.net = basis === 'gross' ? r.gross : r.nb; })); }
    function book() {
        const rows = [];
        defs.forEach(d => { if (selected.has(d.id)) state[d.id].trades.forEach(r => rows.push(r)); });
        rows.sort((a, b) => a.entry.localeCompare(b.entry) || a.sid.localeCompare(b.sid));
        return rows;
    }

    // ── Running ───────────────────────────────────────────────────────────────────────────
    async function runOne(def, from, to) {
        const st = state[def.id];
        st.status = 'running'; st.trades = []; st.msg = ''; st.progress = null; st.warning = '';
        paintStatus();
        const exit = (window.BtScBoard && window.BtScBoard.exit || '15:25').split(':');
        const [el, es] = DIRS[def.dir] || DIRS.both;
        const payload = {
            symbol: def.symbol, interval: def.interval, start_date: from, end_date: to,
            candle_index: def.candle, rr_ratio: def.rr, exit_hour: parseInt(exit[0] || 15, 10), exit_minute: parseInt(exit[1] || 25, 10),
            enable_long: el, enable_short: es, option_pnl: optionPnl(), premium: 250,
            lots: lots(), lot_value: lotValue(), compounding: false,
        };
        try {
            const res = await fetch('/api/backtest/second-candle', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            let data = await res.json();
            if (data.success && data.task_id) {
                while (!cancelled) {
                    await new Promise(r => setTimeout(r, 2000));
                    const p = await (await fetch('/api/backtest/st-bb-rsi/status/' + data.task_id)).json();
                    if (p.status === 'running') { st.progress = p.progress || null; paintStatus(); continue; }
                    data = p; break;
                }
                if (cancelled) { st.status = 'idle'; paintStatus(); return; }
            }
            if (!data.success) throw new Error(data.error || 'Backtest failed');
            st.trades = (data.trades || []).filter(t => t.pnl != null).map(t => norm(def, t));
            st.warning = data.warning || '';
            st.unpriced = optionPnl() && !(data.option || (data.summary && data.summary.pnl_basis === 'option'));
            st.range = from + '|' + to + '|' + optionPnl() + '|' + lots() + '|' + lotValue();
            st.status = 'done';
        } catch (e) {
            st.status = 'error'; st.msg = e.message || String(e);
        }
        paintStatus(); paintResults();
    }

    // Rows already run on these dates/settings are kept; the rest are run, one at a time.
    async function runSet(list) {
        if (running) return false;
        running = true; cancelled = false;
        const from = $('bccFrom').value, to = $('bccTo').value;
        const key = from + '|' + to + '|' + optionPnl() + '|' + lots() + '|' + lotValue();
        setButtons();
        for (const d of list) {
            if (cancelled) break;
            if (state[d.id].status === 'done' && state[d.id].range === key) continue;
            await runOne(d, from, to);
        }
        running = false;
        setButtons(); paintStatus();
        return !cancelled;
    }

    async function findBest() {
        const ok = await runSet(defs);
        if (!ok) return;
        const items = defs.filter(d => state[d.id].status === 'done').map(d => ({ id: d.id, rows: state[d.id].trades }));
        if (!items.length) { bestNote = 'No row finished, so nothing to search.'; paintStatus(); return; }
        const best = window.BtCombinedOptimize(items);
        bestNote = window.BtCombinedOptimize.note(best, items.length, 'row');
        if (best.ids.length) { selected.clear(); best.ids.forEach(id => selected.add(id)); }
        report.resetPage(); paintStatus(); paintResults();
    }

    // ── DOM ───────────────────────────────────────────────────────────────────────────────
    function setButtons() {
        if (!modal) return;
        $('bccRun').textContent = running ? 'Stop' : 'Run combined';
        $('bccBest').disabled = running;
    }

    function build() {
        modal = document.createElement('div');
        modal.className = 'bt-modal-overlay bc-overlay';
        modal.style.display = 'none';
        modal.innerHTML = `
          <div class="bt-modal-box bc-box">
            <div class="bt-modal-hdr">
              <div class="bt-modal-hdr-text">
                <div class="bt-modal-title">Combined — Candle Breakout</div>
                <div class="bt-modal-subtitle">Ticked Best Params rows merged into one book, each BACKTESTed on its own timeframe. ₹ = premium points × lots × lot size.</div>
              </div>
              <div class="bc-dates">
                <label>From <input type="date" id="bccFrom"></label>
                <label>To <input type="date" id="bccTo"></label>
                <label>P&amp;L <select id="bccBasis"><option value="net">Net of brokerage</option><option value="gross">Gross</option></select></label>
                <button type="button" id="bccBest" class="bc-run" title="Backtest every row, then search for the set whose combined book has the best net P&amp;L ÷ max drawdown, and tick only those">⚡ Optimize</button>
                <button type="button" id="bccCapBtn" class="bc-run bc-run-alt" title="The money needed to run the ticked rows live: the most premium ever open at once, plus the combined book's worst drawdown — and why">💰 Capital needed</button>
                <button type="button" id="bccRun" class="bc-run">Run combined</button>
              </div>
              <button class="bt-modal-close" id="bccClose" type="button">✕</button>
            </div>
            <div class="bt-modal-body bc-body">
              <div id="bccStrats"></div>
              <div id="bccNote" class="bc-note"></div>
              <div id="bccCap" class="bc-cap" style="display:none"></div>
              <div id="bccResults"></div>
            </div>
          </div>`;
        document.body.appendChild(modal);
        const b = window.BtScBoard || {};
        $('bccFrom').value = ($('startDate') || {}).value || b.start || '2019-04-01';
        $('bccTo').value = ($('endDate') || {}).value || b.end || '';
        $('bccClose').addEventListener('click', close);
        report = window.BtCombinedReport({
            box: $('bccResults'), rows: book, basis: () => basis, csvName: 'combined_candle_breakout.csv', groupLabel: 'Row',
            groups: () => defs.filter(d => selected.has(d.id) && state[d.id].trades.length),
            meta: () => `${selected.size} of ${defs.length} board rows · ${optionPnl() ? 'option P&L (real candles)' : 'index points × lot value'} · ${lots()} lot each`,
            empty: () => running ? 'Running the ticked rows — results appear as each one finishes…'
                : 'Tick rows above and press Run combined, or ⚡ Optimize to pick the set for you.',
        });
        $('bccBasis').addEventListener('change', (e) => { basis = e.target.value; applyBasis(); report.resetPage(); paintResults(); });
        modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && modal.style.display !== 'none') close(); });
        $('bccRun').addEventListener('click', () => {
            if (running) { cancelled = true; $('bccRun').textContent = 'Stopping…'; return; }
            runSet(defs.filter(d => selected.has(d.id)));
        });
        $('bccBest').addEventListener('click', () => {
            const todo = defs.filter(d => state[d.id].status !== 'done').length;
            if (todo > 12 && !confirm(`⚡ Optimize backtests every board row first: ${todo} still to run, one at a time` +
                (optionPnl() ? ', each pricing its option legs on Breeze (cached after)' : '') + '. Carry on?')) return;
            findBest();
        });
        $('bccCapBtn').addEventListener('click', () => {
            capOpen = !capOpen;
            $('bccCapBtn').classList.toggle('on', capOpen);
            $('bccCap').style.display = capOpen ? '' : 'none';
            paintCap();
        });
        $('bccStrats').addEventListener('change', (e) => {
            const id = e.target.dataset && e.target.dataset.sid;
            if (id) { e.target.checked ? selected.add(id) : selected.delete(id); }
            const tf = e.target.dataset && e.target.dataset.tf;
            if (tf) defs.filter(d => d.tf === tf).forEach(d => { e.target.checked ? selected.add(d.id) : selected.delete(d.id); });
            bestNote = '';
            report.resetPage(); paintStatus(); paintResults();
        });
    }

    let capOpen = false;
    function paintCap() {
        if (!capOpen || !modal) return;
        window.BtCombinedCapital($('bccCap'), defs.filter(d => selected.has(d.id) && state[d.id].trades.length)
            .map(d => ({ name: d.name, color: d.color, rows: state[d.id].trades })));
    }
    function paintResults() { if (report) report.paint(); paintCap(); }

    function paintStatus() {
        if (!modal) return;
        const tfs = [...new Set(defs.map(d => d.tf))];
        $('bccStrats').innerHTML = tfs.map(tf => {
            const rows = defs.filter(d => d.tf === tf);
            const allOn = rows.every(d => selected.has(d.id));
            return `<div class="bc-strats"><label class="bc-chip" title="Tick or untick every ${esc(tf)} row"><input type="checkbox" data-tf="${esc(tf)}" ${allOn ? 'checked' : ''}><b>${esc(tf)}</b></label>` +
                rows.map(d => {
                    const s = state[d.id];
                    let tag = '';
                    if (s.status === 'running') tag = s.progress ? `pricing ${s.progress.done}/${s.progress.total}` : 'running…';
                    else if (s.status === 'done') tag = s.trades.length + ' trades';
                    else if (s.status === 'error') tag = 'failed';
                    else tag = '#' + d.rank;
                    return `<label class="bc-chip ${s.status}" title="${esc(s.msg || s.warning || 'Board row #' + d.rank + ' · ' + d.interval)}">
                        <input type="checkbox" data-sid="${esc(d.id)}" ${selected.has(d.id) ? 'checked' : ''}>
                        <i style="background:${d.color}"></i><b>${esc(d.name.slice(d.tf.length + 3))}</b><span>${esc(tag)}</span></label>`;
                }).join('') + '</div>';
        }).join('');
        const notes = [];
        if (bestNote) notes.push(`<b>${esc(bestNote)}</b>`);
        defs.forEach(d => {
            const s = state[d.id];
            if (s.status === 'error') notes.push(`<b>${esc(d.name)}</b>: ${esc(s.msg)}`);
            else if (s.status === 'done' && s.unpriced) notes.push(`<b>${esc(d.name)}</b>: option premiums unavailable — index points × lot value.`);
            else if (s.status === 'done' && s.warning && selected.has(d.id)) notes.push(`<b>${esc(d.name)}</b>: ${esc(s.warning)}`);
        });
        $('bccNote').innerHTML = notes.join('<br>');
    }

    function open() {
        if (!syncDefs()) {
            if (window.showNotification) window.showNotification('Run ⚡ Find Best Params first — Combined merges the rows of its boards.', 'warning');
            return;
        }
        if (!modal) build();
        setButtons(); paintStatus(); paintResults();
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }
    function close() {
        modal.style.display = 'none';
        document.body.style.overflow = '';
    }

    window.BtScCombined = { open };
})();
