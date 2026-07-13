'use strict';

const tdElems = {};

document.addEventListener('DOMContentLoaded', () => {
    tdElems.symbol = document.getElementById('tdSymbol');

    tdElems.mdMeta = document.getElementById('tdMdMeta');
    tdElems.mdBox  = document.getElementById('tdMarketDirection');
    tdElems.hoiBox = document.getElementById('tdHoiPredict');

    tdElems.symbol.addEventListener('change', () => loadMarketDirection());

    loadMarketDirection();
    loadNextSessionOutlook();
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
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">${chips}</div>`;
}
