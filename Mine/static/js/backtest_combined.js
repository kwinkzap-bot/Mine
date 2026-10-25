/* Backtest page — "Combined" popup for the Simple Trader group.
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
    const DEFS = [
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
    const SENSEX_OPTIONS_FROM = '27 May 2024';
    const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2', '#db2777'];
    DEFS.forEach((d, i) => { d.color = COLORS[i]; });

    // id → {status:'idle'|'running'|'done'|'error', trades, msg, progress, warning, unpriced}
    const state = {};
    DEFS.forEach(d => { state[d.id] = { status: 'idle', trades: [] }; });
    const selected = new Set(DEFS.map(d => d.id));
    let running = false, cancelled = false;
    // Quantman books gross (before brokerage), so Gross is the default for comparing with it.
    let basis = 'gross';
    let modal = null, equityChart = null, page = 0;
    const PAGE = 25;

    const inr = (v, sign) => (v < 0 ? '-' : (sign && v > 0 ? '+' : '')) + '₹' + Math.round(Math.abs(v)).toLocaleString('en-IN');
    // Compact ₹ for the breakdown panels: 2.53L, 12.4k, 850.
    const inrc = (v, sign) => {
        const a = Math.abs(v), sg = v < 0 ? '-' : (sign && v > 0 ? '+' : '');
        return sg + '₹' + (a >= 1e5 ? (a / 1e5).toFixed(2) + 'L' : a >= 1e3 ? (a / 1e3).toFixed(1) + 'k' : Math.round(a));
    };
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const cls = (v) => v > 0 ? 'bc-pos' : (v < 0 ? 'bc-neg' : '');
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
            ep: t.entry_premium, xp: t.exit_premium, lots: t.lots || lots(),
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
            st.status = 'done';
        } catch (e) {
            st.status = 'error'; st.msg = e.message || String(e);
        }
        paintStatus(); paintResults();
    }

    async function runAll() {
        if (running) return;
        running = true; cancelled = false;
        const from = $('bcFrom').value || '2019-04-01', to = $('bcTo').value || todayIso();
        $('bcRun').textContent = 'Stop';
        const todo = DEFS.filter(d => selected.has(d.id));
        for (const d of todo) { if (cancelled) break; await runOne(d, from, to); }
        running = false;
        $('bcRun').textContent = 'Run again';
        paintStatus();
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
                <div class="bt-modal-title">Combined — Simple Trader</div>
                <div class="bt-modal-subtitle">All selected strategies merged into one book. ₹ = premium points × lots × lot size, net of brokerage.</div>
              </div>
              <div class="bc-dates">
                <label>From <input type="date" id="bcFrom"></label>
                <label>To <input type="date" id="bcTo"></label>
                <label>P&amp;L <select id="bcBasis"><option value="gross">Gross</option><option value="net">Net of brokerage</option></select></label>
                <button type="button" id="bcRun" class="bc-run">Run combined</button>
              </div>
              <button class="bt-modal-close" id="bcClose" type="button">✕</button>
            </div>
            <div class="bt-modal-body bc-body">
              <div class="bc-strats" id="bcStrats"></div>
              <div id="bcNote" class="bc-note"></div>
              <div id="bcResults"></div>
            </div>
          </div>`;
        document.body.appendChild(modal);
        $('bcFrom').value = '2019-04-01';
        $('bcTo').value = todayIso();
        $('bcClose').addEventListener('click', close);
        $('bcBasis').addEventListener('change', (e) => { basis = e.target.value; applyBasis(); page = 0; paintResults(); });
        modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && modal.style.display !== 'none') close(); });
        $('bcRun').addEventListener('click', () => {
            if (running) { cancelled = true; $('bcRun').textContent = 'Stopping…'; return; }
            runAll();
        });
        $('bcStrats').addEventListener('change', (e) => {
            const id = e.target.dataset && e.target.dataset.sid;
            if (!id) return;
            e.target.checked ? selected.add(id) : selected.delete(id);
            page = 0; paintResults();
        });
        $('bcResults').addEventListener('click', (e) => {
            const pg = e.target.closest('[data-page]');
            if (pg) { page = parseInt(pg.dataset.page, 10); paintResults(true); return; }
            if (e.target.id === 'bcCsv') { downloadCsv(); return; }
            const th = e.target.closest('th[data-grid]');
            if (th) {
                const id = th.dataset.grid, k = th.dataset.k;
                const cur = sorts[id] || (id === 'hist' ? HIST_DEFAULT : null);
                sorts[id] = { k, dir: cur && cur.k === k && cur.dir === 'desc' ? 'asc' : 'desc' };
                if (id === 'hist') page = 0;
                const body = modal.querySelector('.bc-body'), top = body.scrollTop;
                paintResults();
                body.scrollTop = top;
            }
        });
        paintStatus(); paintResults();
    }

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
        $('bcStrats').innerHTML = DEFS.map(d => {
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
        $('bcNote').innerHTML = notes.join('<br>');
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
            { k: 'net', label: basis === 'gross' ? 'Gross' : 'Net', right: true, get: e => e.s.net,
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
        { k: 'strat', label: 'Strategy', get: r => r.sname, html: r => `<i class="bc-dot" style="background:${r.color}"></i>${esc(r.sname)}` },
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

    function paintResults(keepChart) {
        if (!modal) return;
        const rows = book();
        const box = $('bcResults');
        const anyDone = DEFS.some(d => state[d.id].status === 'done');
        if (!rows.length) {
            if (equityChart) { equityChart.destroy(); equityChart = null; }
            box.innerHTML = `<div class="bc-empty">${running ? 'Running the strategies — results appear as each one finishes…' : (anyDone ? 'No trades for the selected strategies.' : 'Pick strategies above and press Run combined.')}</div>`;
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
        const byStrat = DEFS.filter(d => selected.has(d.id) && state[d.id].trades.length)
            .map(d => ({ label: `<i class="bc-dot" style="background:${d.color}"></i>${esc(d.name)}`, plain: d.name, s: stats(state[d.id].trades) }));
        const byWhy = [...groupBy(rows, r => r.reason || 'unknown')].map(([k, v]) => ({ label: esc(k), s: stats(v) }));

        const pages = Math.ceil(rows.length / PAGE);
        if (page >= pages) page = pages - 1;
        let cum = 0;
        rows.forEach((r, i) => { cum += r.net; r.no = i + 1; r.cum = cum; });
        const hist = sortRows('hist', HIST_COLS, rows, HIST_DEFAULT).slice(page * PAGE, page * PAGE + PAGE);

        box.innerHTML = `
          <div class="bc-meta">${esc(first)} → ${esc(last)} · ${selected.size} of ${DEFS.length} strategies</div>
          <div class="bc-cards">
            ${card(basis === 'gross' ? 'Gross P&amp;L' : 'Net P&amp;L', inr(s.net, true), basis === 'gross' ? 'before brokerage (as Quantman)' : 'after brokerage', cls(s.net))}
            ${card(basis === 'gross' ? 'Net P&amp;L' : 'Gross P&amp;L', inr(basis === 'gross' ? s.gross - s.brok : s.gross, true), basis === 'gross' ? 'after brokerage' : 'before brokerage')}
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

          <div class="bc-sec">Equity curve <small>combined net ₹ by trade date; thin lines are each strategy alone</small></div>
          <div class="bc-chart"><canvas id="bcEquity"></canvas></div>

          <div class="bc-sec">P&amp;L breakdown</div>
          <div class="bc-grid3">
            <section class="bc-panel"><h4>By strategy</h4>${breakdown('g-strat', byStrat, 'Strategy')}</section>
            <section class="bc-panel"><h4>By year</h4>${breakdown('g-year', byYear, 'Year')}</section>
            <section class="bc-panel"><h4>By day of week (entry)</h4>${breakdown('g-dow', dowEntries, 'Day')}</section>
            <section class="bc-panel"><h4>By instrument</h4>${breakdown('g-sym', bySym, 'Instrument')}</section>
            <section class="bc-panel"><h4>By trade type</h4>${breakdown('g-side', bySide, 'Type')}</section>
            <section class="bc-panel"><h4>By exit reason</h4>${breakdown('g-why', byWhy, 'Reason')}</section>
          </div>
          <section class="bc-panel bc-wide"><h4>Month by month</h4>
          <div class="bc-scroll">${heat(rows)}</div></section>

          <div class="bc-sec">Trade history <small>${rows.length} trades · click a header to sort</small>
            <button type="button" id="bcCsv" class="bc-csv">Download CSV</button></div>
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
        const cv = $('bcEquity');
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
        DEFS.forEach(d => {
            if (!selected.has(d.id) || !state[d.id].trades.length) return;
            datasets.push({ label: d.name, data: series(rows.filter(r => r.sid === d.id)), borderColor: d.color,
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
        const rows = book();
        const head = ['Strategy', 'Instrument', 'Type', 'Strike', 'Entry', 'Exit', 'Entry premium', 'Exit premium', 'Lots', 'Exit reason', 'Gross', 'Brokerage', 'Net'];
        const q = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const csv = [head.map(q).join(',')].concat(rows.map(r => [r.sname, r.symbol, r.side, r.strike, r.entry, r.exit, r.ep, r.xp, r.lots,
            r.reason, r.gross.toFixed(2), r.brok.toFixed(2), r.nb.toFixed(2)].map(q).join(','))).join('\n');
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
        a.download = 'combined_simpler_trader.csv';
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }

    // ── Header button: only for the Simple Trader group ───────────────────────────────────
    const group = $('strategyStatusSelect');
    function syncVisibility() { btn.style.display = !group || group.value === 'simpler' ? '' : 'none'; }
    if (group) group.addEventListener('change', syncVisibility);
    syncVisibility();
    btn.addEventListener('click', open);
})();
