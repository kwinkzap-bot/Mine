'use strict';

// CPR Logic page: the hand-analysed CPR sheet (scripts/import_cpr_manual.py)
// beside the chart's reading of the same sessions, from
// /api/trend/cpr-backtest, and the option leg behind every trade from
// /api/trend/cpr-backtest/options. Sits on the Trend page's .td-* chrome
// and keeps its element names (tdElems / tdCpr*) — the two scripts never
// share a page.

const tdElems = {};

document.addEventListener('DOMContentLoaded', () => {
    tdElems.symbol     = document.getElementById('tdSymbol');
    tdElems.cprMeta    = document.getElementById('tdCprMeta');
    tdElems.cprSummary = document.getElementById('tdCprSummary');
    tdElems.cprGrid    = document.getElementById('tdCprBacktest');
    tdElems.cprNote    = document.getElementById('tdCprNote');
    tdElems.cprUpdate  = document.getElementById('tdCprUpdate');
    tdElems.cprStrike  = document.getElementById('tdCprStrike');
    tdElems.year        = document.getElementById('tdCprYear');
    tdElems.year.addEventListener('change', () => {
        _tdCprState.year = tdElems.year.value;
        try { localStorage.setItem('cpr-logic-year', _tdCprState.year); } catch (e) { /* no storage */ }
        if (_tdCprState.data) renderCprBacktest(_tdCprState.data);
    });
    try { _tdCprState.year = localStorage.getItem('cpr-logic-year') || ''; } catch (e) { /* no storage */ }
    tdElems.filterBtn   = document.getElementById('tdCprStrategyFilterBtn');
    tdElems.filterPanel = document.getElementById('tdCprStrategyFilterPanel');
    tdElems.filterBtn.addEventListener('click', e => { e.stopPropagation(); tdElems.filterPanel.classList.toggle('hidden'); });
    tdElems.filterPanel.addEventListener('click', e => e.stopPropagation());
    document.addEventListener('click', () => tdElems.filterPanel.classList.add('hidden'));
    try { _tdCprState.filter = new Set(JSON.parse(localStorage.getItem('cpr-logic-strategy-filter') || '[]')); } catch (e) { /* no storage */ }
    tdElems.strategyModal = document.getElementById('tdCprStrategyModal');
    tdElems.strategyGrid  = document.getElementById('tdCprStrategyGrid');
    tdElems.strategyMeta  = document.getElementById('tdCprStrategyMeta');
    document.getElementById('tdCprStrategies').addEventListener('click', showCprStrategies);
    document.getElementById('tdCprStrategyClose').addEventListener('click', hideCprStrategies);
    document.getElementById('tdCprStrategyRefresh').addEventListener('click', refreshCprStrategies);
    document.getElementById('tdCprStrategySort').addEventListener('change', renderCprStrategyCards);
    document.getElementById('tdCprStrategySearch').addEventListener('input', renderCprStrategyCards);
    tdElems.strategyModal.addEventListener('click', e => { if (e.target === tdElems.strategyModal) hideCprStrategies(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') hideCprStrategies(); });
    tdElems.cprUpdate.addEventListener('click', updateCprBacktest);
    tdElems.symbol.addEventListener('change', loadCprBacktest);
    // A new strike choice only re-reads the option legs; the sheet stays.
    tdElems.cprStrike.addEventListener('change', () => {
        if (!_tdCprState.data) return;
        _tdCprState.options = { status: 'loading' };
        renderCprBacktest(_tdCprState.data);
        loadCprOptions(tdElems.symbol.value);
    });
    loadCprBacktest();
});

function _tdEsc(s) {
    return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ---- CPR Manual vs Chart ----
// Each analysis column stacks the sheet's word over the chart's and tints
// the line by agreement; the numbers behind the chart's verdict sit underneath.
async function loadCprBacktest() {
    const box = tdElems.cprGrid;
    if (!box) return;
    const symbol = tdElems.symbol.value;
    tdElems.cprMeta.textContent = '';
    tdElems.cprSummary.innerHTML = '';
    tdElems.cprNote.textContent = '';
    box.innerHTML = '<span style="font-size:11px;color:var(--td-muted)">Reading the sheet and the chart…</span>';
    try {
        const res = await fetch(`/api/trend/cpr-backtest?symbol=${encodeURIComponent(symbol)}`);
        const data = await res.json();
        if (!data || !data.success) {
            const msg = data && data.no_manual
                ? `No manual CPR sheet imported for ${symbol} — run scripts/import_cpr_manual.py`
                : (data && data.error) || 'CPR comparison unavailable';
            box.innerHTML = `<div class="ac-ana-unavailable">${_tdEsc(msg)}</div>`;
            return;
        }
        _tdCprState.data = data;
        _tdCprState.options = { status: 'loading' };
        renderCprBacktest(data);
        loadCprOptions(symbol);
    } catch (err) {
        box.innerHTML = `<div class="ac-ana-unavailable">Network error: ${_tdEsc(err.message)}</div>`;
    }
}

// ---- Strategy list ----
// The setups the rule takes, from summary.strategies, as cards: a stat
// strip per setup (uses, win/loss, win rate, option P&L) and, opened on
// click, the setup in words — when, entry, stop, target, example.
const _tdStratOpen = new Set();                 // cards left open across re-renders

function _tdCprStrategyRows() {
    const d = _tdCprState.data;
    const list = ((d.summary && d.summary.strategies) || []).map(s => Object.assign({}, s, { opt_pnl: 0, opt_n: 0 }));
    const o = _tdCprState.options;
    const ready = o && o.status === 'ready';
    const byName = Object.fromEntries(list.map(s => [s.name, s]));
    for (const s of list) s.days = [];
    // Every trade of every setup, with its option leg when the legs are in.
    for (const r of d.rows) r.trades.forEach((t, i) => {
        const s = byName[t.strategy];
        if (!s) return;
        const leg = ready ? _tdCprLeg(r.date, i) : null;
        if (leg && leg.pnl != null) { s.opt_pnl += leg.pnl; s.opt_n += 1; }
        s.days.push({ date: r.date, i, m: t.manual, c: t.chart, leg });
    });
    for (const s of list) { s.opt_avg = s.opt_n ? s.opt_pnl / s.opt_n : 0; s.days.sort((a, b) => b.date.localeCompare(a.date)); }
    return { list, ready, lot: ready ? o.summary.lot : null, rule: ready ? (o.premium ? `≈${o.premium} strike` : 'ATM') : null };
}

function showCprStrategies() {
    const d = _tdCprState.data;
    if (!d) return;
    const { list, ready, lot, rule } = _tdCprStrategyRows();
    const total = list.reduce((a, s) => a + s.trades, 0);
    tdElems.strategyMeta.textContent = `${d.symbol} · ${list.length} strategies · ${total} trades` + (rule ? ` · option P&L at ${rule}` : '');

    // Totals strip
    const wins = list.reduce((a, s) => a + s.wins, 0), losses = list.reduce((a, s) => a + s.losses, 0);
    const optPnl = list.reduce((a, s) => a + s.opt_pnl, 0), idxPnl = list.reduce((a, s) => a + s.pnl, 0);
    const best = list.filter(s => s.opt_n).sort((a, b) => b.opt_pnl - a.opt_pnl)[0];
    const cls = v => v > 0 ? 'dg-pos' : v < 0 ? 'dg-neg' : '';
    const tile = (l, v, s = '') => `<div class="td-strat-total"><div class="l">${l}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ''}</div>`;
    document.getElementById('tdCprStrategyTotals').innerHTML =
        tile('Trades', total, `${list.length} setups`) +
        tile('Win / loss', `<span class="dg-pos">${wins}</span> / <span class="dg-neg">${losses}</span>`, wins + losses ? `${Math.round(100 * wins / (wins + losses))}% win rate` : '') +
        tile('Option P&L', ready ? `<span class="${cls(optPnl)}">${_tdNum(optPnl, 0)}</span> pts` : 'reading…', ready && lot ? `₹${_tdNum(optPnl * lot, 0)} per lot` : '') +
        tile('Index P&L', `<span class="${cls(idxPnl)}">${_tdNum(idxPnl, 0)}</span> pts`, 'the sheet\'s figure') +
        tile('Best setup', best ? _tdEsc(best.name) : '—', best ? `<span class="${cls(best.opt_pnl)}">${_tdNum(best.opt_pnl, 0)}</span> option pts` : '');

    renderCprStrategyCards();
    tdElems.strategyModal.classList.remove('hidden');
    document.body.classList.add('td-modal-open');       // the page stops scrolling behind the popup
}

function renderCprStrategyCards() {
    const { list, ready, lot } = _tdCprStrategyRows();
    const sortKey = document.getElementById('tdCprStrategySort').value;
    const q = document.getElementById('tdCprStrategySearch').value.trim().toLowerCase();
    const text = s => [s.name, s.how && s.how.setup, s.how && s.how.entry, s.how && s.how.target].join(' ').toLowerCase();
    const rows = list.filter(s => !q || text(s).includes(q));
    const desc = k => (a, b) => (b[k] ?? -Infinity) - (a[k] ?? -Infinity) || b.trades - a.trades;
    const sorters = { trades: desc('trades'), opt_pnl: desc('opt_pnl'), win_pct: desc('win_pct'), opt_avg: desc('opt_avg'),
                      last: (a, b) => (b.last || '').localeCompare(a.last || ''), name: (a, b) => a.name.localeCompare(b.name) };
    rows.sort(sorters[sortKey] || sorters.trades);

    const cls = v => v > 0 ? 'dg-pos' : v < 0 ? 'dg-neg' : '';
    const stat = (l, v, s = '') => `<div class="td-strat-stat"><span class="l">${l}</span><span class="v">${v}</span>${s ? `<span class="s">${s}</span>` : ''}</div>`;
    const opt = (s, v, fmt) => !ready ? '<span class="td-cpr-opt-wait">reading…</span>' : s.opt_n ? `<span class="${cls(v)}">${fmt(v)}</span>` : '<span class="dg-muted">—</span>';
    const row = (k, label, v, extra = '') => `<div class="td-strat-row ${extra}"><span class="k ${k}">${label}</span><span>${_tdEsc(v || '—')}</span></div>`;
    const html = rows.map(s => {
        const wl = s.wins + s.losses;
        const bar = wl ? `<div class="td-strat-bar"><i class="w" style="width:${100 * s.wins / wl}%"></i><i class="x" style="width:${100 * s.losses / wl}%"></i></div>` : '';
        const how = s.how || {};
        return `<div class="td-strat-card${_tdStratOpen.has(s.name) ? ' open' : ''}" data-name="${_tdEsc(s.name)}">
            <div class="td-strat-head">
                <div class="td-strat-name">${_tdEsc(s.name)}<small>${_tdEsc((how.setup || '').split(/\.\s|\s—\s/)[0])}</small>${bar}</div>
                ${stat('Used', s.trades, s.last ? `last ${s.last}` : '')}
                ${stat('Win / loss', `<span class="dg-pos">${s.wins}</span> / <span class="dg-neg">${s.losses}</span>`, s.flat ? `${s.flat} flat` : '')}
                ${stat('Win rate', s.win_pct == null ? '—' : `${_tdNum(s.win_pct, 0)}%`, `T ${s.targets} · SL ${s.stops} · EOD ${s.eod}`)}
                ${stat('Option P&L', opt(s, s.opt_pnl, x => _tdNum(x, 0)), ready && lot && s.opt_n ? `₹${_tdNum(s.opt_pnl * lot, 0)}/lot` : '')}
                ${stat('Per trade', opt(s, s.opt_avg, x => _tdNum(x, 1)), 'option pts')}
                ${stat('Index', `<span class="${cls(s.pnl)}">${_tdNum(s.pnl, 0)}</span>`, 'pts')}
                <div class="td-strat-chev">▶</div>
            </div>
            <div class="td-strat-body">
                ${row('setup', 'Setup', how.setup, 'setup')}
                ${row('entry', 'Entry', how.entry)}
                ${row('stop', 'Stop', how.stop)}
                ${row('target', 'Target', how.target)}
                ${how.example ? row('example', 'Example', how.example, 'example') : ''}
                ${_tdCprStrategyDays(s, ready, lot)}
            </div>
        </div>`;
    }).join('');
    tdElems.strategyGrid.innerHTML = html || `<div class="td-strat-empty">No setup matches “${_tdEsc(q)}”</div>`;
    tdElems.strategyGrid.querySelectorAll('.td-strat-head').forEach(h => h.addEventListener('click', () => {
        const card = h.parentElement, name = card.dataset.name;
        card.classList.toggle('open');
        if (card.classList.contains('open')) _tdStratOpen.add(name); else _tdStratOpen.delete(name);
    }));
}

// The days a setup traded, newest first: the index trade and its result,
// and the option leg's P&L when the legs are in.
function _tdCprStrategyDays(s, ready, lot) {
    if (!s.days.length) return '';
    const cls = v => v > 0 ? 'dg-pos' : v < 0 ? 'dg-neg' : 'dg-muted';
    const res = r => r === 'Target' ? 'dg-pos' : r === 'SL' ? 'dg-neg' : '';
    const rows = s.days.map(({ date, m, c, leg }) => {
        const opt = !ready ? '<span class="td-cpr-opt-wait">reading…</span>'
                  : leg && leg.pnl != null ? `<span class="${cls(leg.pnl)}">${_tdNum(leg.pnl, 2)}</span><small>₹${_tdNum(leg.pnl * (lot || 0), 0)}</small>`
                  : '<span class="dg-muted">—</span>';
        const contract = leg && leg.strike ? `${leg.strike} ${leg.option_type}` : '';
        const when = c && c.entry_time ? `${c.entry_time}${c.exit_time ? ' → ' + c.exit_time : ''}` : '';
        return `<tr>
            <td class="d">${date}</td>
            <td><b class="${m.trade === 'BUY' ? 'dg-pos' : 'dg-neg'}">${m.trade}</b></td>
            <td class="n">${_tdNum(m.entry, 0)}</td>
            <td class="n">${_tdNum(m.sl, 0)}</td>
            <td class="n">${_tdNum(m.target, 0)}</td>
            <td><span class="${res(m.result)}">${_tdEsc(m.result || '—')}</span><small>${_tdEsc(when)}</small></td>
            <td class="n"><span class="${cls(m.pnl)}">${m.pnl == null ? '—' : _tdNum(m.pnl, 0)}</span></td>
            <td class="n">${opt}<small>${_tdEsc(contract)}</small></td>
        </tr>`;
    }).join('');
    const idx = s.days.reduce((a, x) => a + (x.m.pnl || 0), 0);
    return `<div class="td-strat-days">
        <div class="td-strat-days-hd">Days traded <span>${s.days.length} · index ${_tdNum(idx, 0)} pts${ready && s.opt_n ? ` · option ${_tdNum(s.opt_pnl, 2)} pts` : ''}</span></div>
        <table class="td-strat-tbl">
            <thead><tr><th>Date</th><th>Side</th><th class="n">Entry</th><th class="n">SL</th><th class="n">Target</th><th>Result</th><th class="n">Index pts</th><th class="n">Option pts</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
    </div>`;
}

function hideCprStrategies() {
    tdElems.strategyModal.classList.add('hidden');
    document.body.classList.remove('td-modal-open');
}

// The popup's Update: re-read the sheet and the chart (the same call the
// page loads with), redraw the main grid from it, and recount the
// strategies — so a rule change or a sheet edit shows without a reload.
async function refreshCprStrategies() {
    const btn = document.getElementById('tdCprStrategyRefresh');
    const symbol = tdElems.symbol.value;
    btn.disabled = true;
    btn.textContent = 'Updating…';
    try {
        const res = await fetch(`/api/trend/cpr-backtest?symbol=${encodeURIComponent(symbol)}`);
        const data = await res.json();
        if (!data || !data.success) {
            tdElems.strategyMeta.textContent = `Update failed: ${(data && data.error) || res.status}`;
            return;
        }
        _tdCprState.data = data;
        renderCprBacktest(data);          // keeps the option legs already loaded
        showCprStrategies();
        tdElems.strategyMeta.textContent += ` · updated ${new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}`;
    } catch (err) {
        tdElems.strategyMeta.textContent = `Update failed: ${err.message}`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Update';
    }
}

// The grid's last payload and the option legs behind its trades. The legs
// come from a second call — one Breeze request per trade, so a cold read
// of a long sheet takes a minute — and the grid re-renders when they land.
const _tdCprState = { data: null, options: null, filter: new Set(), year: '' };   // filter: the ticked strategy names (empty = all); year: '' = all

// ---- Year filter ----
// The years the sheet's dates span, newest first; picking one shows that
// year's sessions only. The choice is kept across reloads.
function renderCprYearFilter() {
    const d = _tdCprState.data;
    if (!d) return;
    const years = [...new Set(d.rows.map(r => r.date.slice(0, 4)))].sort().reverse();
    if (_tdCprState.year && !years.includes(_tdCprState.year)) _tdCprState.year = '';
    tdElems.year.innerHTML = `<option value="">All years</option>` + years.map(y => {
        const n = d.rows.filter(r => r.date.startsWith(y)).length;
        return `<option value="${y}"${y === _tdCprState.year ? ' selected' : ''}>${y} (${n})</option>`;
    }).join('');
    tdElems.year.classList.toggle('td-msel-on', !!_tdCprState.year);
}

function _tdCprYearMatches(r) {
    return !_tdCprState.year || r.date.startsWith(_tdCprState.year);
}

// ---- Strategy filter ----
// The dropdown next to the symbol: one checkbox per setup on the sheet,
// with its trade count and index P&L. Ticked setups filter the grid to
// the sessions they traded; nothing ticked shows every session.
function _tdCprFilterActive() {
    return _tdCprState.filter.size > 0;
}

function _tdCprRowMatches(r) {
    return !_tdCprFilterActive() || r.trades.some(t => _tdCprState.filter.has(t.strategy));
}

function renderCprStrategyFilter() {
    const d = _tdCprState.data;
    if (!d) return;
    const list = ((d.summary && d.summary.strategies) || []).slice().sort((a, b) => b.trades - a.trades);
    const names = new Set(list.map(s => s.name));
    for (const n of [..._tdCprState.filter]) if (!names.has(n)) _tdCprState.filter.delete(n);   // a setup no longer on the sheet
    const cls = v => v > 0 ? 'dg-pos' : v < 0 ? 'dg-neg' : 'dg-muted';
    tdElems.filterPanel.innerHTML =
        `<div class="td-msel-hd"><button type="button" class="td-btn" data-act="all">All</button><button type="button" class="td-btn" data-act="none">None</button></div>` +
        list.map(s => `<label class="td-msel-row"><input type="checkbox" value="${_tdEsc(s.name)}"${_tdCprState.filter.has(s.name) ? ' checked' : ''}>`
            + `<span>${_tdEsc(s.name)}</span><span class="n">${s.trades}</span><span class="pnl ${cls(s.pnl)}">${_tdNum(s.pnl, 0)}</span></label>`).join('');
    tdElems.filterPanel.querySelectorAll('input').forEach(cb => cb.addEventListener('change', () => {
        if (cb.checked) _tdCprState.filter.add(cb.value); else _tdCprState.filter.delete(cb.value);
        _tdCprFilterChanged();
    }));
    tdElems.filterPanel.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => {
        _tdCprState.filter = b.dataset.act === 'all' ? new Set(list.map(s => s.name)) : new Set();
        renderCprStrategyFilter();
        _tdCprFilterChanged();
    }));
    const n = _tdCprState.filter.size;
    tdElems.filterBtn.textContent = !n ? 'All strategies ▾'
        : n === 1 ? `${[..._tdCprState.filter][0]} ▾` : `${n} strategies ▾`;
    tdElems.filterBtn.classList.toggle('td-msel-on', n > 0);
}

function _tdCprFilterChanged() {
    try { localStorage.setItem('cpr-logic-strategy-filter', JSON.stringify([..._tdCprState.filter])); } catch (e) { /* no storage */ }
    renderCprStrategyFilter();
    if (_tdCprState.data) renderCprBacktest(_tdCprState.data);
}

// The strike choice: '' for ATM, else the premium the strike is picked by.
function _tdCprPremium() {
    return tdElems.cprStrike ? tdElems.cprStrike.value : '';
}

async function loadCprOptions(symbol) {
    const premium = _tdCprPremium();
    try {
        const qs = new URLSearchParams({ symbol });
        if (premium) qs.set('premium', premium);
        const res = await fetch(`/api/trend/cpr-backtest/options?${qs}`);
        const data = await res.json();
        // The symbol or the strike choice changed meanwhile: this answer is stale.
        if (!_tdCprState.data || _tdCprState.data.symbol !== symbol.toUpperCase() || _tdCprPremium() !== premium) return;
        _tdCprState.options = data && data.success
            ? { status: 'ready', legs: data.legs || {}, summary: data.summary || {}, premium: data.premium || null }
            : { status: 'error', error: (data && data.error) || `HTTP ${res.status}`,
                icici: !!(data && data.icici_required) };
    } catch (err) {
        _tdCprState.options = { status: 'error', error: err.message };
    }
    renderCprBacktest(_tdCprState.data);
}

// The option leg of trade `i` on `date`, or null before the legs arrive.
function _tdCprLeg(date, i) {
    const o = _tdCprState.options;
    return o && o.status === 'ready' && o.legs[date] ? o.legs[date][i] || null : null;
}

// The Update button: POST /api/trend/cpr-backtest/update appends every
// complete session after the sheet's last date (chart-read analysis, the
// rule's trade), then the grid is reloaded. Nothing to add is a normal
// answer — the button just says so in the meta line.
async function updateCprBacktest() {
    const btn = tdElems.cprUpdate;
    const symbol = tdElems.symbol.value;
    btn.disabled = true;
    btn.textContent = 'Updating…';
    try {
        const res = await fetch('/api/trend/cpr-backtest/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol }),
        });
        const data = await res.json();
        if (!data || !data.success) {
            tdElems.cprNote.textContent = `Update failed: ${(data && data.error) || res.status}`;
            return;
        }
        const added = data.added || [];
        if (!added.length) {
            tdElems.cprNote.textContent = `Up to date — the sheet already ends at ${data.last} and no complete session follows it`
                + (data.skipped && data.skipped.length ? ` (${data.skipped.map(x => `${x.date}: ${x.error}`).join('; ')})` : '');
            return;
        }
        await loadCprBacktest();
        const trades = (data.trades || []).map(t => `${t.date} ${t.trade} ${_tdNum(t.entry, 0)} → ${t.result}${t.pnl != null ? ` (${t.pnl > 0 ? '+' : ''}${_tdNum(t.pnl, 0)})` : ''}`);
        tdElems.cprNote.textContent = `Added ${added.length} session${added.length > 1 ? 's' : ''} from the chart: ${added.join(', ')}`
            + (trades.length ? ` · ${trades.join(' · ')}` : ' · no trade by rule');
    } catch (err) {
        tdElems.cprNote.textContent = `Update failed: ${err.message}`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Update';
    }
}

// Tints that read the value, not the agreement: Above / green candle are
// bullish (green), Below / red candle bearish (red), anything else plain.
// The sheet's word is what is tinted; a disagreement still shows both.
const _tdCprSideClass = v => ({ above: 'td-cpr-asc', below: 'td-cpr-dec' })[String(v || '').trim().toLowerCase()] || '';
function _tdCprCandleClass(text) {
    const s = String(text || '').toLowerCase();
    if (s.includes('doji') || s.includes('decision')) return '';
    return s.includes('green') ? 'td-cpr-asc' : s.includes('red') ? 'td-cpr-dec' : '';
}

const _tdCprCols = [
    { key: 'price_vs_daily',  label: 'Price vs Daily CPR',  short: 'Daily',  chart: c => c.price_vs_daily,
      cellClass: (v, r) => _tdCprSideClass(r.manual.price_vs_daily),
      why: c => c.price_in_daily_cpr ? 'close inside CPR' : '' },
    { key: 'price_vs_hourly', label: 'Price vs Hourly CPR', short: 'Hourly', chart: c => c.price_vs_hourly,
      cellClass: (v, r) => _tdCprSideClass(r.manual.price_vs_hourly),
      why: c => c.price_in_hourly_cpr ? 'close inside weekly CPR' : '' },
    // No agree/disagree tint on CPR Type: the app reads it against a
    // 10-session average while the sheet reads it by eye, so a difference
    // there is a difference of scale, not an error.
    { key: 'cpr_type',        label: 'CPR Type',            short: 'Type',   chart: c => c.cpr_type, noTint: true,
      why: c => `${c.width_pct}%` + (c.width_ratio != null ? ` · ${c.width_ratio}x avg` : '') },
    // Tinted by the direction itself (Asc green, Dec red, Inside plain),
    // not by agreement — the direction is what the eye wants at a glance.
    // The sheet and the service both say Asc / Dec; the grid spells them out.
    { key: 'cpr_direction',   label: 'CPR Direction',       short: 'Dir',    chart: c => c.cpr_direction,
      show: _tdCprDirWord,
      cellClass: (v, r) => ({ asc: 'td-cpr-asc', dec: 'td-cpr-dec' })[String(r.manual.cpr_direction || '').toLowerCase()] || '',
      why: () => '' },
    { key: 'boxes',           label: 'Boxes',               short: 'Boxes',  chart: c => c.boxes,
      why: c => `${c.box_pts} pts` },
    // Just the reading — the candle's OHLC lives in the detail row.
    { key: 'first_candle',    label: '1st 5-min candle',    short: '1st 5m', chart: c => c.first_candle ? c.first_candle.text : null,
      cellClass: (v, r) => _tdCprCandleClass(r.manual.first_candle),
      why: () => '' },
];

// The grid shows the six readings as two columns: the two price-vs-CPR
// readings and the first candle in one, and the CPR's type / direction /
// boxes in the other. Each reading is its own line inside the cell,
// labelled and tinted on its own, so the agree/disagree tint still reads
// per reading.
const _tdCprGroups = [
    { key: 'price_vs_cpr', label: 'Price vs CPR · 1st candle', parts: ['price_vs_daily', 'price_vs_hourly', 'first_candle'] },
    { key: 'cpr',          label: 'CPR',                       parts: ['cpr_type', 'cpr_direction', 'boxes'] },
].map(g => Object.assign(g, { cols: g.parts.map(k => _tdCprCols.find(c => c.key === k)) }));

function _tdCprPartClass(col, r) {
    return col.cellClass ? col.cellClass(null, r) : col.noTint ? '' : _tdCprMatchClass(r.match[col.key]);
}

function _tdCprDirWord(v) {
    return ({ asc: 'Ascending', dec: 'Descending' })[String(v ?? '').trim().toLowerCase()] || v;
}

function _tdCprGroupCell(g, r) {
    return `<div class="td-cpr-group">` + g.cols.map(col => {
        const cls = _tdCprPartClass(col, r);
        const show = col.show || (v => v);
        return `<div class="td-cpr-part ${cls}"><span class="td-cpr-part-label">${_tdEsc(col.short)}</span>`
             + _tdCprCell(r.manual, r.chart, r.match[col.key], show(r.manual[col.key]),
                          r.chart ? show(col.chart(r.chart)) : null, r.chart ? col.why(r.chart) : '')
             + `</div>`;
    }).join('') + `</div>`;
}

// One value when the sheet and the chart agree; the two stacked (sheet on
// top, chart under it) only where they differ or one side is missing.
function _tdCprCell(m, c, match, manualText, chartText, why) {
    const same = c && manualText != null && chartText != null
        && String(manualText).trim().toLowerCase() === String(chartText).trim().toLowerCase();
    const lines = same
        ? `<span class="td-cpr-manual">${_tdEsc(manualText)}</span>`
        : `<span class="td-cpr-manual">${_tdEsc(manualText ?? '—')}</span>
           <span class="td-cpr-chart">${c ? _tdEsc(chartText ?? '—') : '<i>no bars</i>'}</span>`;
    return `<div class="td-cpr-cell">${lines}${why ? `<span class="td-cpr-why">${_tdEsc(why)}</span>` : ''}</div>`;
}

function _tdCprMatchClass(match) {
    return match === true ? 'td-cpr-ok' : match === false ? 'td-cpr-bad' : '';
}

function _tdNum(v, dp = 2) {
    return v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function renderCprBacktest(d) {
    const rows = d.rows || [];
    const s = d.summary || {};
    const ag = s.agreement || {};

    const lastDate = rows.reduce((m, r) => (r.date > m ? r.date : m), '') || null;
    tdElems.cprMeta.textContent = `${d.symbol} · ${s.sessions || 0} sessions · ${s.rows || 0} rows · ${d.source || ''}`
        + (lastDate ? ` · to ${lastDate}` : '') + (d.extended_at ? ` · extended ${d.extended_at}` : '');

    const pct = k => ag[k] && ag[k].pct != null ? `${ag[k].match}/${ag[k].total} (${ag[k].pct}%)` : '—';
    const pnlCls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
    tdElems.cprSummary.innerHTML = [
        ['Daily CPR', 'price_vs_daily'], ['Hourly CPR', 'price_vs_hourly'], ['Type', 'cpr_type'],
        ['Direction', 'cpr_direction'], ['Boxes', 'boxes'], ['1st candle', 'first_candle'], ['Result', 'result'],
    ].map(([l, k]) => `<span class="td-cpr-chip">${l} agree <b>${pct(k)}</b></span>`).join('') +
    `<span class="td-cpr-chip">P&amp;L <b class="${pnlCls(s.manual?.pnl)}">${_tdNum(s.manual?.pnl, 0)}</b> pts · ${s.manual?.wins ?? 0}/${s.manual?.trades ?? 0} wins</span>` +
    _tdCprOptionChip();

    // The Trade column reads the day's single trade inline — side on top,
    // then Entry / Target / SL with the replay's reach time beside each; a
    // multi-trade day shows a count here and lists its trades in the
    // sub-grid below.
    const one = r => r.trades.length === 1 ? r.trades[0] : null;

    const columns = [
        { key: 'date', label: 'Date', strong: true, sortable: true,
          format: (v, r) => r.chart ? v : `${v} ⚠`, title: (v, r) => r.error || '' },
        ..._tdCprGroups.map(g => ({
            key: g.key, label: g.label, sortable: true,
            thTitle: g.cols.map(c => c.label).join(' · '),
            sortValue: r => g.cols.map(c => (r.manual[c.key] || '') + '|' + (r.chart ? c.chart(r.chart) : '')).join(' / '),
            render: (v, r) => _tdCprGroupCell(g, r),
        })),
        { key: 'trade', label: 'Trade', sortable: true,
          thTitle: 'Side, then Entry · Target · SL from the sheet, with the time the chart replay reached each level',
          sortValue: r => r.trades.length === 1 ? r.trades[0].manual.trade : r.trades.length ? 'MULTI' : '',
          render: (v, r) => {
              const t = one(r);
              if (t) return _tdCprTradeCell(t);
              return r.trades.length ? `<span class="td-cpr-multi">${r.trades.length} trades ▾</span>` : '<span class="dg-muted">—</span>';
          } },
        { key: 'option', label: 'Option', sortable: true,
          thTitle: 'The order that would actually be placed: the ATM CALL for a BUY, the ATM PUT for a SELL, '
                 + 'on the weekly expiry. Entry is the premium at the minute NIFTY crossed the entry; the level that '
                 + 'closed the trade is the premium at that minute; a level never reached is an estimate (≈) from '
                 + 'the day\'s own delta. Read from the contract\'s recorded 1-minute candles (ICICI).',
          sortValue: r => { const l = _tdCprLeg(r.date, 0); return l && l.strike ? `${l.strike} ${l.option_type}` : ''; },
          render: (v, r) => {
              if (!r.trades.length) return '—';
              if (r.trades.length > 1) return `<span class="td-cpr-multi">${r.trades.length} trades ▾</span>`;
              return _tdCprOptionCell(_tdCprLeg(r.date, 0));
          } },
        { key: 'opt_pnl', label: 'P&L (Opt)', align: 'right', sortable: true,
          thTitle: 'Premium points made on the option leg, and rupees for one lot',
          sortValue: r => r.trades.reduce((a, t, i) => a + ((_tdCprLeg(r.date, i) || {}).pnl || 0), 0),
          render: (v, r) => {
              if (!r.trades.length) return '—';
              const legs = r.trades.map((t, i) => _tdCprLeg(r.date, i));
              if (_tdCprState.options && _tdCprState.options.status !== 'ready') return _tdCprOptionPending();
              if (legs.every(l => !l || l.pnl == null)) return '<span class="dg-muted">—</span>';
              const pnl = legs.reduce((a, l) => a + (l && l.pnl || 0), 0);
              const lot = _tdCprState.options.summary.lot;
              return _tdCprOptPnl(pnl, lot, r.trades.length > 1 ? 'net' : '');
          } },
        { key: 'result', label: 'Result', sortable: true,
          sortValue: r => r.trades.map(t => t.manual.result || '').join(','),
          render: (v, r) => {
              const t = one(r);
              if (t) return _tdCprResult(t, r.chart);
              if (!r.trades.length) return '—';
              const hits = r.trades.filter(t => t.manual.result === 'Target').length;
              return `<span class="td-cpr-multi">${hits}/${r.trades.length} target ▾</span>`;
          } },
        { key: 'pnl', label: 'P&L (pts)', align: 'right', sortable: true,
          sortValue: r => r.trades.reduce((a, t) => a + (t.manual.pnl || 0), 0),
          render: (v, r) => {
              if (!r.trades.length) return '—';
              const mp = r.trades.reduce((a, t) => a + (t.manual.pnl || 0), 0);
              const cp = r.trades.every(t => !t.chart || t.chart.pnl == null) ? null
                       : r.trades.reduce((a, t) => a + (t.chart && t.chart.pnl || 0), 0);
              return _tdCprPnl(mp, cp, r.trades.length > 1 ? 'net' : '');
          } },
        { key: 'reason', label: 'Reason',
          thTitle: 'Why the trade sits where it does, read off the chart\'s ladder — daily CPR and floor pivots, '
                 + 'Cam R3/S3, PDH/PDL, weekly CPR, the 09:15 candle\'s high/low and the day\'s open. '
                 + 'Entry: the level it broke or bounced from. Target: the next level in the trade\'s direction. '
                 + 'SL: the level the stop hides behind. Your own note from the sheet, when there is one, is on top.',
          render: (v, r) => {
              const t = one(r);
              if (t) return _tdCprReason(t);
              return r.trades.length ? `<span class="td-cpr-multi">see trades ▾</span>` : '';
          } },
    ];

    // Row-level fields the grid reads by key (sort, format) come from the
    // manual side; the chart side is reached through `r.chart`.
    renderCprYearFilter();
    const shown = rows.filter(r => _tdCprYearMatches(r) && _tdCprRowMatches(r));
    const gridRows = shown.map(r => Object.assign({}, r.manual, r));
    if (_tdCprFilterActive() || _tdCprState.year) {
        // The filtered tally: only the trades of the ticked setups, and their option legs.
        const trades = shown.flatMap(r => r.trades.map((t, i) => ({ t, leg: _tdCprLeg(r.date, i) })).filter(x => !_tdCprFilterActive() || _tdCprState.filter.has(x.t.strategy)));
        const pnl = trades.reduce((a, x) => a + (x.t.manual.pnl || 0), 0);
        const wins = trades.filter(x => (x.t.manual.pnl || 0) > 0).length;
        const opt = trades.reduce((a, x) => a + (x.leg && x.leg.pnl != null ? x.leg.pnl : 0), 0);
        const ready = _tdCprState.options && _tdCprState.options.status === 'ready';
        const pcls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
        const label = [_tdCprState.year || '', _tdCprFilterActive() ? `${_tdCprState.filter.size} strateg${_tdCprState.filter.size === 1 ? 'y' : 'ies'}` : ''].filter(Boolean).join(' · ');
        tdElems.cprSummary.innerHTML += `<span class="td-cpr-chip td-cpr-chip-filter">${_tdEsc(label)}: <b>${shown.length}</b> sessions · <b>${trades.length}</b> trades · `
            + `<b class="${pcls(pnl)}">${_tdNum(pnl, 0)}</b> pts · ${wins}/${trades.length} wins`
            + (ready ? ` · option <b class="${pcls(opt)}">${_tdNum(opt, 2)}</b> pts` : '') + `</span>`;
    }
    renderCprStrategyFilter();

    DataGrid.mountSortable(tdElems.cprGrid, {
        rows: gridRows,
        columns,
        empty: _tdCprFilterActive() || _tdCprState.year ? 'No session matches the year / strategy filter' : 'The sheet has no analysed sessions yet',
        defaultSort: { key: 'date', dir: 'desc' },   // newest session on top
        rowClass: r => (r.chart ? '' : 'td-cpr-row-nodata') + (r.trades.length > 1 ? ' td-cpr-row-multi' : ''),
        detail: r => r.trades.length > 1 ? _tdCprTradesGrid(r) : '',   // only multi-trade days expand
    });

    const rules = d.rules || {};
    tdElems.cprNote.innerHTML =
        `A cell shows one value when the sheet and the chart agree, and both (sheet on top, chart under it) when they differ; ` +
        `a line is green when the sheet's reading is bullish (Above, Ascending, a green candle) and red when bearish, plain otherwise. One row per session; a day with several trades shows the count ` +
        `and lists them in the sub-grid when the row is opened. <b>Hourly CPR</b> is the CPR the 1-hour chart draws — the ` +
        `previous week's, as the Pine script's AUTO pivot timeframe is weekly above 15 minutes. <b>Price vs CPR</b> ` +
        `reads the 09:15 candle's close against the pivot. <b>CPR Type</b>: ${_tdEsc(rules.cpr_type || '')}. ` +
        `<b>Boxes</b> are the R1↔PDH and S1↔PDL bands (both the same height): ${_tdEsc(rules.boxes || '')}. ` +
        `<b>1st candle</b>: ${_tdEsc(rules.first_candle || '')}. <b>Result</b> fills at the first 5-minute bar after ` +
        `09:15 that trades through the entry, then whichever of target / SL a later bar reaches first; a bar reaching ` +
        `both is "Both". <b>Option</b> is the leg an order would actually go on — CE for a BUY, PE for a SELL, weekly expiry, ` +
        `the strike ATM to the entry or, with a premium picked in the dropdown, the strike whose premium at the entry minute ` +
        `was nearest it — priced off the contract's own 1-minute candles at the minutes NIFTY crossed each level; ` +
        `a level never reached shows ≈ the entry premium moved by the day's delta. <b>P&amp;L (Opt)</b> is premium ` +
        `points and rupees for one lot.`;
}

// The time the chart replay reached a level — the fill time for the entry,
// the exit time for whichever of target / SL ended the trade. Empty for a
// level that was never hit.
function _tdCprReachTime(t, key) {
    const c = t.chart || {};
    if (key === 'entry') return c.entry_time || '';
    if (key === 'target' && (c.result === 'Target' || c.result === 'Both')) return c.exit_time || '';
    if (key === 'sl' && (c.result === 'SL' || c.result === 'Both')) return c.exit_time || '';
    return '';
}

// Entry / Target / SL price with the reach time underneath (sub-grid).
function _tdCprPrice(t, key) {
    const when = _tdCprReachTime(t, key);
    return `<div class="td-cpr-cell" style="align-items:flex-end">
        <span>${_tdEsc(_tdNum(t.manual[key], 0))}</span>
        ${when ? `<span class="td-cpr-why">${_tdEsc(when)}</span>` : ''}</div>`;
}

// ── the option leg ────────────────────────────────────────────────────────

function _tdCprOptionPending() {
    const o = _tdCprState.options;
    if (!o || o.status === 'loading') {
        return `<span class="td-cpr-opt-wait" title="One Breeze request per strike looked at; the first read of a premium target takes a few minutes, later ones are instant">reading${_tdCprPremium() ? ' ladder' : ''}…</span>`;
    }
    return `<span class="td-cpr-opt-wait" title="${_tdEsc(o.error || '')}">${o.icici ? 'ICICI login' : 'unavailable'}</span>`;
}

function _tdCprExpiry(iso) {
    if (!iso) return '';
    const d = new Date(`${iso}T00:00:00`);
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }).replace(/ /g, '-');
}

// Contract on top, then Entry / Target / SL premiums — the reach time
// beside a level that printed, ≈ before one that is the day's-delta estimate.
function _tdCprOptionCell(leg) {
    if (!_tdCprState.options || _tdCprState.options.status !== 'ready' || !leg) return _tdCprOptionPending();
    const away = leg.atm != null && leg.atm !== leg.strike ? ` · ATM ${leg.atm}` : '';
    const head = leg.strike
        ? `<span class="td-cpr-side ${leg.option_type === 'CE' ? 'dg-pos' : 'dg-neg'}">${leg.strike} ${leg.option_type}</span>`
          + `<span class="td-cpr-why">${_tdEsc(_tdCprExpiry(leg.expiry))}${away}${leg.delta != null ? ` · δ ${leg.delta}` : ''}</span>`
        : '';
    if (leg.error || leg.entry == null) {
        const why = leg.error || (leg.result === 'No fill' ? 'no fill' : '—');
        return `<div class="td-cpr-group">${head}<span class="td-cpr-opt-wait">${_tdEsc(why)}</span></div>`;
    }
    const est = k => (leg.estimated || []).includes(k);
    const time = k => k === 'entry' ? leg.entry_time
               : (k === 'target' && leg.result === 'Target') || (k === 'sl' && leg.result === 'SL') ? leg.exit_time : '';
    const lines = [['entry', 'Entry'], ['target', 'Target'], ['sl', 'SL']].map(([k, label]) =>
        `<div class="td-cpr-part td-cpr-level"><span class="td-cpr-part-label">${label}</span>`
        + `<span class="td-cpr-price${est(k) ? ' td-cpr-est' : ''}">${est(k) ? '≈ ' : ''}${_tdEsc(_tdNum(leg[k], 2))}</span>`
        + (time(k) ? `<span class="td-cpr-why">${_tdEsc(time(k))}</span>` : '') + `</div>`).join('');
    const eod = leg.result === 'EOD' && leg.exit != null
        ? `<div class="td-cpr-part td-cpr-level"><span class="td-cpr-part-label">EOD</span><span class="td-cpr-price">${_tdEsc(_tdNum(leg.exit, 2))}</span><span class="td-cpr-why">${_tdEsc(leg.exit_time || '')}</span></div>`
        : '';
    return `<div class="td-cpr-group">${head}${lines}${eod}</div>`;
}

function _tdCprOptPnl(pnl, lot, tag) {
    const cls = x => x > 0 ? 'dg-pos' : x < 0 ? 'dg-neg' : '';
    const rs = lot ? `₹${_tdNum(pnl * lot, 0)}/lot` : '';
    return `<div class="td-cpr-cell" style="align-items:flex-end">
        <span class="td-cpr-manual ${cls(pnl)}">${_tdNum(pnl, 2)}</span>
        <span class="td-cpr-why">${[rs, tag].filter(Boolean).join(' · ')}</span></div>`;
}

function _tdCprOptionChip() {
    const o = _tdCprState.options;
    if (!o) return '';
    if (o.status !== 'ready') return `<span class="td-cpr-chip">Options P&amp;L <b>${o.status === 'loading' ? '…' : (o.icici ? 'ICICI login' : 'n/a')}</b></span>`;
    const s = o.summary || {};
    const cls = s.pnl > 0 ? 'pos' : s.pnl < 0 ? 'neg' : '';
    const rule = o.premium ? `≈${o.premium} strike` : 'ATM';
    return `<span class="td-cpr-chip">Options P&amp;L (${rule}) <b class="${cls}">${_tdNum(s.pnl, 2)}</b> pts · ₹${_tdNum(s.pnl_lot, 0)}/lot · ${s.wins ?? 0}/${s.trades ?? 0} wins`
         + (s.missing ? ` · ${s.missing} no data` : '') + `</span>`;
}

// The main grid's one Trade cell: BUY / SELL on top, then a labelled line
// per level with its price and, beside it, the reach time.
function _tdCprTradeCell(t) {
    const side = t.manual.trade;
    const lines = [['entry', 'Entry'], ['target', 'Target'], ['sl', 'SL']].map(([key, label]) => {
        const when = _tdCprReachTime(t, key);
        return `<div class="td-cpr-part td-cpr-level"><span class="td-cpr-part-label">${label}</span>`
             + `<span class="td-cpr-price">${_tdEsc(_tdNum(t.manual[key], 0))}</span>`
             + (when ? `<span class="td-cpr-why">${_tdEsc(when)}</span>` : '') + `</div>`;
    }).join('');
    return `<div class="td-cpr-group">
        <span class="td-cpr-side ${side === 'BUY' ? 'dg-pos' : 'dg-neg'}">${_tdEsc(side)}</span>${lines}</div>`;
}

// Result and P&L show the sheet's figure only — one value, SL or Target —
// with the replay's fill -> exit times underneath. The chart's own verdict
// is not repeated here; the reach times under Entry / Target / SL and the
// Result agreement chip carry it.
function _tdCprResult(t, chart) {
    const c = t.chart;
    const when = c && c.entry_time ? `${c.entry_time}${c.exit_time ? ' → ' + c.exit_time : ''}` : '';
    const res = t.manual.result;
    const cls = res === 'Target' ? 'dg-pos' : res === 'SL' ? 'dg-neg' : '';
    return `<div class="td-cpr-cell">
        <span class="td-cpr-manual ${cls}">${_tdEsc(res ?? '—')}</span>
        ${when ? `<span class="td-cpr-why">${_tdEsc(when)}</span>` : ''}</div>`;
}

function _tdCprPnl(mp, cp, tag) {
    const cls = x => x > 0 ? 'dg-pos' : x < 0 ? 'dg-neg' : '';
    return `<div class="td-cpr-cell" style="align-items:flex-end">
        <span class="td-cpr-manual ${cls(mp)}">${_tdNum(mp, 0)}</span>
        ${tag ? `<span class="td-cpr-why">${_tdEsc(tag)}</span>` : ''}</div>`;
}

function _tdCprReason(t) {
    const rs = t.chart && t.chart.reasons;
    const own = t.manual.reason ? `<span class="td-cpr-manual">${_tdEsc(t.manual.reason)}</span>` : '';
    if (!rs) return `<div class="td-cpr-cell td-cpr-reason">${own}</div>`;
    const rr = rs.rr != null ? `1:${rs.rr}` : '—';
    return `<div class="td-cpr-cell td-cpr-reason">${own}
        <span><b>In</b> ${_tdEsc(rs.entry)}</span>
        <span><b>Target</b> ${_tdEsc(rs.target.replace(/^Target [\d,]+ — /, ''))}</span>
        <span><b>SL</b> ${_tdEsc(rs.sl.replace(/^SL [\d,]+ — /, ''))}</span>
        <span class="td-cpr-why">risk ${rs.risk} · reward ${rs.reward} · R:R ${rr}</span></div>`;
}

// Sub-grid for a multi-trade session: the same trade columns, one row per
// trade, rendered with DataGrid so it looks like the parent.
function _tdCprTradesGrid(r) {
    const cls = x => x > 0 ? 'dg-pos' : x < 0 ? 'dg-neg' : '';
    return `<div class="td-cpr-subgrid">` + DataGrid.render({
        rows: r.trades,
        columns: [
            { key: 'n', label: '#', align: 'center', render: (v, t, i) => String(i + 1) },
            { key: 'trade', label: 'Trade', strong: true,
              render: (v, t) => `<span class="${t.manual.trade === 'BUY' ? 'dg-pos' : 'dg-neg'}">${_tdEsc(t.manual.trade)}</span>` },
            { key: 'entry',  label: 'Entry',  align: 'right', render: (v, t) => _tdCprPrice(t, 'entry') },
            { key: 'target', label: 'Target', align: 'right', render: (v, t) => _tdCprPrice(t, 'target') },
            { key: 'sl',     label: 'SL',     align: 'right', render: (v, t) => _tdCprPrice(t, 'sl') },
            { key: 'option', label: 'Option', render: (v, t, i) => _tdCprOptionCell(_tdCprLeg(r.date, i)) },
            { key: 'opt_pnl', label: 'P&L (Opt)', align: 'right',
              render: (v, t, i) => {
                  const l = _tdCprLeg(r.date, i);
                  if (_tdCprState.options && _tdCprState.options.status !== 'ready') return _tdCprOptionPending();
                  return l && l.pnl != null ? _tdCprOptPnl(l.pnl, _tdCprState.options.summary.lot, '') : '<span class="dg-muted">—</span>';
              } },
            { key: 'result', label: 'Result', render: (v, t) => _tdCprResult(t, r.chart) },
            { key: 'pnl', label: 'P&L (pts)', align: 'right',
              render: (v, t) => _tdCprPnl(t.manual.pnl, t.chart ? t.chart.pnl : null, '') },
            { key: 'reason', label: 'Reason', render: (v, t) => _tdCprReason(t) },
        ],
    }) + `</div>`;
}

