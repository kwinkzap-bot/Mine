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

// The grid's last payload and the option legs behind its trades. The legs
// come from a second call — one Breeze request per trade, so a cold read
// of a long sheet takes a minute — and the grid re-renders when they land.
const _tdCprState = { data: null, options: null };

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
    const gridRows = rows.map(r => Object.assign({}, r.manual, r));

    DataGrid.mountSortable(tdElems.cprGrid, {
        rows: gridRows,
        columns,
        empty: 'The sheet has no analysed sessions yet',
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

