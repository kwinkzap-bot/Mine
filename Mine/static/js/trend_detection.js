'use strict';

const tdElems = {};

document.addEventListener('DOMContentLoaded', () => {
    tdElems.symbol = document.getElementById('tdSymbol');

    tdElems.mdMeta = document.getElementById('tdMdMeta');
    tdElems.mdBox  = document.getElementById('tdMarketDirection');
    tdElems.hoiBox = document.getElementById('tdHoiPredict');

    tdElems.cprMeta    = document.getElementById('tdCprMeta');
    tdElems.cprSummary = document.getElementById('tdCprSummary');
    tdElems.cprGrid    = document.getElementById('tdCprBacktest');
    tdElems.cprNote    = document.getElementById('tdCprNote');

    tdElems.symbol.addEventListener('change', () => { loadMarketDirection(); loadCprBacktest(); });

    loadMarketDirection();
    loadNextSessionOutlook();
    loadCprBacktest();
});

// ---- Market Direction (mirrors active_contracts.js renderAnalysis) ----
function _tdFmtNum(n, dec) {
    if (n == null || n === 0) return '—';
    return Number(n).toLocaleString('en-IN', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}
function _tdEsc(s) {
    return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function loadMarketDirection() {
    const box = tdElems.mdBox;
    if (!box) return;
    const symbol = tdElems.symbol.value;

    try {
        const params = new URLSearchParams({ underlying: symbol, type: 'all' });
        let data;
        if (typeof window.fetchJson === 'function') {
            data = await window.fetchJson(`/api/active-contracts?${params}`);
        } else {
            const res = await fetch(`/api/active-contracts?${params}`);
            data = await res.json();
        }
        if (!data || !data.success) {
            box.classList.remove('hidden');
            box.innerHTML = `<div class="ac-ana-unavailable">${_tdEsc(data && data.error ? data.error : 'Market direction unavailable')}</div>`;
            return;
        }
        renderMarketDirection(data.analysis, data.spot, data.underlying);
    } catch (err) {
        box.classList.remove('hidden');
        box.innerHTML = `<div class="ac-ana-unavailable">Network error: ${_tdEsc(err.message)}</div>`;
    }
}

function renderMarketDirection(a, spot, underlying) {
    const box = tdElems.mdBox;
    if (!box) return;

    if (!a) { box.classList.add('hidden'); box.innerHTML = ''; return; }
    box.classList.remove('hidden');

    if (!a.available) {
        box.innerHTML = `<div class="ac-ana-unavailable">${_tdEsc(a.reason || 'Direction analysis unavailable')}</div>`;
        return;
    }

    const dirClass = a.direction === 'UP' ? 'up' : a.direction === 'DOWN' ? 'down' : 'side';
    const dirIcon  = a.direction === 'UP' ? '▲' : a.direction === 'DOWN' ? '▼' : '◆';
    const dirLabel = a.direction === 'SIDEWAYS' ? 'SIDEWAYS' : 'MARKET ' + a.direction;

    let spotChip = '';
    if (spot && spot.last) {
        const sCls  = spot.pct_change > 0 ? 'ac-pos' : spot.pct_change < 0 ? 'ac-neg' : '';
        const sSign = spot.pct_change > 0 ? '+' : '';
        spotChip = `<span class="ac-ana-chip ac-ana-spot">${_tdEsc(underlying || '')} <b>${_tdFmtNum(spot.last, 2)}</b>
            <span class="${sCls}">${sSign}${_tdFmtNum(Math.abs(spot.pct_change), 2)}%</span></span>`;
    }

    const chips = [
        `<span class="ac-ana-chip">Vol PCR <b>${a.volume_pcr}</b></span>`,
        `<span class="ac-ana-chip">CE Move <b class="${a.ce_move_pct > 0 ? 'ac-pos' : a.ce_move_pct < 0 ? 'ac-neg' : ''}">${a.ce_move_pct > 0 ? '+' : ''}${a.ce_move_pct}%</b></span>`,
        `<span class="ac-ana-chip">PE Move <b class="${a.pe_move_pct > 0 ? 'ac-pos' : a.pe_move_pct < 0 ? 'ac-neg' : ''}">${a.pe_move_pct > 0 ? '+' : ''}${a.pe_move_pct}%</b></span>`,
        a.support    ? `<span class="ac-ana-chip">Support <b>${_tdFmtNum(a.support, 0)}</b></span>`    : '',
        a.resistance ? `<span class="ac-ana-chip">Resistance <b>${_tdFmtNum(a.resistance, 0)}</b></span>` : '',
    ].join('');

    const sigRows = (a.signals || []).map(s =>
        `<li class="ac-sig-row"><span class="ac-sig-dot ${s.verdict}"></span>
         <span class="ac-sig-name">${_tdEsc(s.name)}</span>
         <span class="ac-sig-detail">${_tdEsc(s.detail)}</span></li>`
    ).join('');

    if (tdElems.mdMeta) tdElems.mdMeta.textContent = `${a.confidence_pct}% strength`;

    box.innerHTML = `
        <div class="ac-ana-head">
            <span class="ac-dir-badge ${dirClass}">${dirIcon} ${dirLabel}</span>
            ${spotChip}
            <span class="ac-ana-chips">${chips}</span>
        </div>
        <ul class="ac-sig-list">${sigRows}</ul>`;
}

// ---- Next Session Outlook (mirrors historic_oi.js _hoiRenderPrediction) ----
function loadNextSessionOutlook() {
    fetch('/api/oi-historic/predict')
        .then(r => r.json())
        .then(renderNextSessionOutlook)
        .catch(() => renderNextSessionOutlook(null));
}

function renderNextSessionOutlook(d) {
    const el = tdElems.hoiBox;
    if (!el) return;
    if (!d || !d.success) {
        el.innerHTML = `<span style="font-size:11px;color:var(--pf-text-3)">Prediction unavailable — ${d && d.error ? d.error : 'analysis failed'}</span>`;
        return;
    }
    const col = d.prediction === 'BULLISH' ? 'var(--pf-pos)'
              : d.prediction === 'BEARISH' ? 'var(--pf-neg)'
              : 'var(--pf-text-2)';
    const icon = d.prediction === 'BULLISH' ? '▲' : d.prediction === 'BEARISH' ? '▼' : '◆';
    const mv   = d.expected_move_pct;
    const mvTxt = (mv > 0 ? '+' : '') + mv.toFixed(2) + '%';

    // Open-vs-Prev-Avg-3-VWAP read: computed server-side (analyze_and_predict)
    // so this matches the Historic OI page's Open-column tint exactly — kept
    // as its own chip, separate from the 5-year blended signals below.
    const openSig = d.open_vs_prev_avg3vwap;
    const openSigChip = openSig ? (() => {
        const c   = openSig.upside ? 'var(--pf-pos)' : 'var(--pf-neg)';
        const fmt = n => Number(n).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
        return `<span class="hoi-sig-chip">
                  <span style="color:${c};font-size:9px">●</span>Open <b style="color:var(--pf-text-1);font-variant-numeric:tabular-nums">${fmt(openSig.open)}</b>
                  vs Prev Avg3 VWAP <b style="color:var(--pf-text-1);font-variant-numeric:tabular-nums">${fmt(openSig.prev_avg3_vwap)}</b>
                  <b style="color:${c};font-variant-numeric:tabular-nums">${openSig.upside ? 'Upside' : 'Downside'}</b></span>`;
    })() : '';

    const chips = (d.reasons || []).map(r => {
        const short = r.name.split('(')[0].trim();
        const c = r.bullish ? 'var(--pf-pos)' : 'var(--pf-neg)';
        return `<span class="hoi-sig-chip"><span style="color:${c};font-size:9px">●</span>${short}
                  <b style="color:var(--pf-text-1);font-variant-numeric:tabular-nums">${r.value}</b></span>`;
    }).join('');

    const rows = (d.reasons || []).map(r => `
        <div style="display:flex;gap:8px;align-items:baseline;padding:3px 0;">
          <span style="color:${r.bullish ? 'var(--pf-pos)' : 'var(--pf-neg)'};font-size:10px">●</span>
          <span style="font-size:11.5px;color:var(--pf-text-1);font-weight:600;white-space:nowrap">${r.name}: ${r.value}</span>
          <span style="font-size:11px;color:var(--pf-text-2)">${r.bucket} — ${r.why}.
            <b class="${r.prob_up_pct >= d.base_rate_pct ? 'hoi-up' : 'hoi-down'}">${r.prob_up_pct}%</b>
            of similar days closed higher next session
            (avg ${r.avg_ret_pct > 0 ? '+' : ''}${r.avg_ret_pct}%, n=${r.n})</span>
        </div>`).join('');
    const popup = `
      <div style="font-size:11px;font-weight:700;color:var(--pf-text-2);margin-bottom:6px">
        Signal breakdown · up-probability <b style="color:${col}">${d.prob_up_pct}%</b> vs 5-yr base rate ${d.base_rate_pct}%
      </div>
      ${rows}
      <div style="font-size:10px;color:var(--pf-text-3);margin-top:8px;border-top:1px solid var(--pf-border-sub);padding-top:6px">
        model hit-rate ${d.backtest_hit_pct != null ? d.backtest_hit_pct + '%' : '—'} over ${d.sample_days.toLocaleString('en-IN')} days (${d.from_date} → ${d.to_date}).
        Statistical tendencies from this grid's own 5-year history (in-sample) — not financial advice.
      </div>`;

    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-size:11px;font-weight:700;letter-spacing:.05em;color:var(--pf-text-3)">NEXT SESSION OUTLOOK · ${d.next_session}</span>
        <span style="font-size:15px;font-weight:800;color:${col}">${icon} ${d.prediction}</span>
        <span style="font-size:11.5px;color:var(--pf-text-2)">
          <b style="color:${col}">${d.prob_up_pct}%</b> up · move <b style="color:${col}">${mvTxt}</b>
        </span>
        <span class="hoi-info-wrap" style="margin-left:auto">
          <span class="hoi-info-btn" tabindex="0" aria-label="Show full signal breakdown">i</span>
          <div class="hoi-info-pop">${popup}</div>
        </span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">${openSigChip}${chips}</div>`;
}


// ---- CPR Manual vs Chart ----
// The hand-analysed CPR sheet (scripts/import_cpr_manual.py) beside the
// chart's reading of the same sessions, from /api/trend/cpr-backtest. Each
// analysis column stacks the sheet's word over the chart's and tints the
// cell by agreement; the numbers behind the chart's verdict sit underneath.
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
        renderCprBacktest(data);
    } catch (err) {
        box.innerHTML = `<div class="ac-ana-unavailable">Network error: ${_tdEsc(err.message)}</div>`;
    }
}

const _tdCprCols = [
    { key: 'price_vs_daily',  label: 'Price vs Daily CPR',  chart: c => c.price_vs_daily,
      why: c => c.price_in_daily_cpr ? 'close inside CPR' : '' },
    { key: 'price_vs_hourly', label: 'Price vs Hourly CPR', chart: c => c.price_vs_hourly,
      why: c => c.price_in_hourly_cpr ? 'close inside weekly CPR' : '' },
    // No agree/disagree tint on CPR Type: the app reads it against a
    // 10-session average while the sheet reads it by eye, so a difference
    // there is a difference of scale, not an error.
    { key: 'cpr_type',        label: 'CPR Type',            chart: c => c.cpr_type, noTint: true,
      why: c => `${c.width_pct}%` + (c.width_ratio != null ? ` · ${c.width_ratio}x avg` : '') },
    // Tinted by the direction itself (Asc green, Dec red, Inside plain),
    // not by agreement — the direction is what the eye wants at a glance.
    { key: 'cpr_direction',   label: 'CPR Direction',       chart: c => c.cpr_direction,
      cellClass: (v, r) => ({ asc: 'td-cpr-asc', dec: 'td-cpr-dec' })[String(r.manual.cpr_direction || '').toLowerCase()] || '',
      why: () => '' },
    { key: 'boxes',           label: 'Boxes',               chart: c => c.boxes,
      why: c => `${c.box_pts} pts` },
];

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

    tdElems.cprMeta.textContent = `${d.symbol} · ${s.sessions || 0} sessions · ${s.rows || 0} rows · ${d.source || ''}`;

    const pct = k => ag[k] && ag[k].pct != null ? `${ag[k].match}/${ag[k].total} (${ag[k].pct}%)` : '—';
    const pnlCls = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
    tdElems.cprSummary.innerHTML = [
        ['Daily CPR', 'price_vs_daily'], ['Hourly CPR', 'price_vs_hourly'], ['Type', 'cpr_type'],
        ['Direction', 'cpr_direction'], ['Boxes', 'boxes'], ['1st candle', 'first_candle'], ['Result', 'result'],
    ].map(([l, k]) => `<span class="td-cpr-chip">${l} agree <b>${pct(k)}</b></span>`).join('') +
    `<span class="td-cpr-chip">P&amp;L <b class="${pnlCls(s.manual?.pnl)}">${_tdNum(s.manual?.pnl, 0)}</b> pts · ${s.manual?.wins ?? 0}/${s.manual?.trades ?? 0} wins</span>` +
    '';

    // Trade columns read the day's single trade inline; a multi-trade day
    // shows a count here and lists its trades in the sub-grid below.
    const one = r => r.trades.length === 1 ? r.trades[0] : null;
    const tradeCol = (key, label, opts) => Object.assign({
        key, label, align: 'right',
        render: (v, r) => {
            const t = one(r);
            if (t) return _tdCprPrice(t, key);
            return r.trades.length ? `<span class="td-cpr-multi">${r.trades.length} trades ▾</span>` : '—';
        },
    }, opts || {});

    const columns = [
        { key: 'date', label: 'Date', strong: true, sortable: true,
          format: (v, r) => r.chart ? v : `${v} ⚠`, title: (v, r) => r.error || '' },
        ..._tdCprCols.map(col => ({
            key: col.key, label: col.label, sortable: true,
            sortValue: r => (r.manual[col.key] || '') + '|' + (r.chart ? col.chart(r.chart) : ''),
            cellClass: (v, r) => col.cellClass ? col.cellClass(v, r)
                               : col.noTint ? '' : _tdCprMatchClass(r.match[col.key]),
            render: (v, r) => _tdCprCell(r.manual, r.chart, r.match[col.key], r.manual[col.key],
                                         r.chart ? col.chart(r.chart) : null, r.chart ? col.why(r.chart) : ''),
        })),
        { key: 'first_candle', label: '1st 5-min candle',
          cellClass: (v, r) => _tdCprMatchClass(r.match.first_candle),
          render: (v, r) => {
              const fc = r.chart && r.chart.first_candle;
              // Just the reading — the OHLC lives in the detail row.
              return _tdCprCell(r.manual, r.chart, r.match.first_candle, r.manual.first_candle,
                                fc ? fc.text : null, '');
          } },
        { key: 'trade', label: 'Trade', strong: true, sortable: true,
          sortValue: r => r.trades.length === 1 ? r.trades[0].manual.trade : r.trades.length ? 'MULTI' : '',
          render: (v, r) => {
              const t = one(r);
              if (t) return `<span class="${t.manual.trade === 'BUY' ? 'dg-pos' : 'dg-neg'}">${_tdEsc(t.manual.trade)}</span>`;
              return r.trades.length ? `<span class="td-cpr-multi">${r.trades.length} trades ▾</span>` : '<span class="dg-muted">—</span>';
          } },
        tradeCol('entry', 'Entry'),
        tradeCol('target', 'Target'),
        tradeCol('sl', 'SL'),
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
        `A cell shows one value when the sheet and the chart agree, and both (sheet on top, chart under it) when they differ. One row per session; a day with several trades shows the count ` +
        `and lists them in the sub-grid when the row is opened. <b>Hourly CPR</b> is the CPR the 1-hour chart draws — the ` +
        `previous week's, as the Pine script's AUTO pivot timeframe is weekly above 15 minutes. <b>Price vs CPR</b> ` +
        `reads the 09:15 candle's close against the pivot. <b>CPR Type</b>: ${_tdEsc(rules.cpr_type || '')}. ` +
        `<b>Boxes</b> are the R1↔PDH and S1↔PDL bands (both the same height): ${_tdEsc(rules.boxes || '')}. ` +
        `<b>1st candle</b>: ${_tdEsc(rules.first_candle || '')}. <b>Result</b> fills at the first 5-minute bar after ` +
        `09:15 that trades through the entry, then whichever of target / SL a later bar reaches first; a bar reaching ` +
        `both is "Both".`;
}

// Entry / Target / SL price with, underneath, the time the chart replay
// reached it — the fill time for the entry, the exit time for whichever of
// target / SL ended the trade. Nothing under a level that was never hit.
function _tdCprPrice(t, key) {
    const c = t.chart || {};
    let when = '';
    if (key === 'entry') when = c.entry_time || '';
    else if (key === 'target' && (c.result === 'Target' || c.result === 'Both')) when = c.exit_time || '';
    else if (key === 'sl' && (c.result === 'SL' || c.result === 'Both')) when = c.exit_time || '';
    return `<div class="td-cpr-cell" style="align-items:flex-end">
        <span>${_tdEsc(_tdNum(t.manual[key], 0))}</span>
        ${when ? `<span class="td-cpr-why">${_tdEsc(when)}</span>` : ''}</div>`;
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
            { key: 'result', label: 'Result', render: (v, t) => _tdCprResult(t, r.chart) },
            { key: 'pnl', label: 'P&L (pts)', align: 'right',
              render: (v, t) => _tdCprPnl(t.manual.pnl, t.chart ? t.chart.pnl : null, '') },
            { key: 'reason', label: 'Reason', render: (v, t) => _tdCprReason(t) },
        ],
    }) + `</div>`;
}

