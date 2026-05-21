'use strict';

const tdElems = {};
const INDEX_SYMS = new Set(['NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY','SENSEX','NIFTYNXT50']);

let _debounceTimer = null;
function scheduleAnalysis(delay) {
    clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(runAnalysis, delay);
}

document.addEventListener('DOMContentLoaded', () => {
    tdElems.symbol    = document.getElementById('tdSymbol');
    tdElems.lookback  = document.getElementById('tdLookback');
    tdElems.date      = document.getElementById('tdDate');
    tdElems.results   = document.getElementById('tdResults');
    tdElems.loading   = document.getElementById('tdLoading');
    tdElems.error     = document.getElementById('tdError');

    tdElems.modePill    = document.getElementById('tdModePill');
    tdElems.metaAtr     = document.getElementById('tdMetaAtr');

    tdElems.regimeWrap  = document.getElementById('tdRegimeWrap');
    tdElems.regimeArrow = document.getElementById('tdRegimeArrow');
    tdElems.regimeName  = document.getElementById('tdRegimeName');
    tdElems.regimeConf  = document.getElementById('tdRegimeConf');
    tdElems.indRows     = document.getElementById('tdIndRows');

    tdElems.projMeta    = document.getElementById('tdProjMeta');
    tdElems.projRows    = document.getElementById('tdProjRows');

    tdElems.sigMeta       = document.getElementById('tdSigMeta');
    tdElems.barsContainer = document.getElementById('tdBarsContainer');

    tdElems.vixSection = document.getElementById('tdVixSection');
    tdElems.vixMeta    = document.getElementById('tdVixMeta');
    tdElems.vixOiRows  = document.getElementById('tdVixOiRows');

    tdElems.date.value = new Date().toISOString().slice(0, 10);

    tdElems.symbol.addEventListener('change',  () => scheduleAnalysis(0));
    tdElems.lookback.addEventListener('change', () => scheduleAnalysis(0));
    tdElems.date.addEventListener('change',     () => scheduleAnalysis(400));

    runAnalysis();
});

// ---- Fetch ----
async function runAnalysis() {
    const symbol   = tdElems.symbol.value;
    const lookback = tdElems.lookback.value;
    const date     = tdElems.date.value;

    setLoading(true);
    hideError();

    const params = new URLSearchParams({ symbol, lookback });
    if (date) params.set('date', date);

    try {
        let data;
        if (typeof window.fetchJson === 'function') {
            data = await window.fetchJson(`/api/trend-detection?${params}`);
        } else {
            const res = await fetch(`/api/trend-detection?${params}`);
            data = await res.json();
        }
        if (!data.success) { showError(data.error || 'Analysis failed'); return; }
        render(data);
    } catch (err) {
        showError('Network error: ' + err.message);
    } finally {
        setLoading(false);
    }
}

// ---- Render ----
function render(d) {
    tdElems.results.style.display = 'block';
    const isIndex = INDEX_SYMS.has(d.symbol);
    const has6    = isIndex && d.india_vix != null;

    // Mode pill
    tdElems.modePill.textContent = has6 ? (d.oi_context ? '6-signal' : '5-signal') : '4-signal';

    // ATR meta in regime card header
    tdElems.metaAtr.textContent = `ATR ${d.atr}`;

    // Regime badge
    const cls = d.regime === 'TREND_UP' ? 'trend-up' : d.regime === 'TREND_DOWN' ? 'trend-down' : 'sideways';
    tdElems.regimeWrap.className    = `td-regime-wrap ${cls}`;
    tdElems.regimeArrow.textContent = d.regime === 'TREND_UP' ? '↑' : d.regime === 'TREND_DOWN' ? '↓' : '↔';
    tdElems.regimeName.textContent  = d.regime.replace('_', ' ');
    tdElems.regimeConf.textContent  = `${d.confidence}% conf`;

    // Indicator quick-stats (inside regime card)
    renderIndRows(d, isIndex, has6);

    // Signal bars
    renderBars(d, isIndex);
    const sign = d.composite > 0 ? '+' : '';
    tdElems.sigMeta.textContent = `composite ${sign}${d.composite}`;

    // Projection
    renderProjection(d);

    // VIX & OI
    if (isIndex && d.india_vix) {
        tdElems.vixSection.style.display = 'block';
        renderVixOi(d);
    } else {
        tdElems.vixSection.style.display = 'none';
    }
}

// ---- Indicator rows (Regime card) ----
function renderIndRows(d, isIndex, has6) {
    const adxHint   = d.adx > 40 ? 'Strong' : d.adx > 25 ? 'Trend' : d.adx > 20 ? 'Trans.' : 'Range';
    const chopHint  = d.chop_index < 38.2 ? 'Trending' : d.chop_index > 61.8 ? 'Choppy' : 'Neutral';
    const slopeSign = d.slope > 0 ? '+' : '';
    const slopeHint = Math.abs(d.slope) > 0.25 ? 'Directional' : Math.abs(d.slope) < 0.1 ? 'Flat' : 'Mild';
    const bullPct   = d.consistency_pct;
    const candleHint = bullPct > 65 ? 'Bullish' : bullPct < 35 ? 'Bearish' : 'Mixed';

    const rows = [
        { label: 'ADX',    val: d.adx,                          hint: adxHint },
        { label: '+/−DI',  val: `${d.plus_di} / ${d.minus_di}`, hint: '' },
        { label: 'CHOP',   val: d.chop_index,                    hint: chopHint },
        { label: 'Slope',  val: `${slopeSign}${d.slope}`,        hint: slopeHint },
        { label: 'Candles',val: `${bullPct}%`,                   hint: candleHint },
    ];

    if (has6 && d.india_vix) {
        rows.push({ label: 'VIX', val: d.india_vix.current.toFixed(1), hint: `${d.india_vix.percentile}th pct` });
    }
    if (has6 && d.oi_context) {
        const pcrHint = d.pcr_score > 0 ? 'Put↑' : 'Call↓';
        rows.push({ label: 'PCR', val: d.oi_context.pcr.toFixed(2), hint: pcrHint });
    }

    tdElems.indRows.innerHTML = rows.map(r => `
        <div class="td-row">
            <div class="td-row-label">${r.label}</div>
            <div class="td-row-data">
                <div class="td-row-val">${r.val}</div>
                ${r.hint ? `<div class="td-row-hint">${r.hint}</div>` : ''}
            </div>
        </div>`).join('');
}

// ---- Signal bars ----
function renderBars(d, isIndex) {
    const dir = d.composite >= 0 ? 'bull' : 'bear';

    const adxNorm    = Math.min(Math.max((d.adx - 20) / 30, 0), 1);
    const chopNorm   = Math.min(Math.max((61.8 - d.chop_index) / (61.8 - 38.2), 0), 1);
    const slopeNorm  = Math.min(Math.abs(d.slope), 1);
    const candleNorm = Math.abs(d.consistency_pct - 50) / 50;
    const slopeSign  = d.slope > 0 ? '+' : '';

    const bars = [
        { label: isIndex ? 'ADX (28%)'     : 'ADX (35%)',     norm: adxNorm,    val: d.adx,                         hint: d.adx > 25 ? 'Trending' : 'Ranging',           cls: dir },
        { label: isIndex ? 'CHOP (22%)'    : 'CHOP (30%)',    norm: chopNorm,   val: d.chop_index,                  hint: d.chop_index < 38.2 ? 'Trending' : 'Choppy',   cls: dir },
        { label: isIndex ? 'Slope (15%)'   : 'Slope (20%)',   norm: slopeNorm,  val: `${slopeSign}${d.slope}`,      hint: Math.abs(d.slope) > 0.25 ? 'Directional' : 'Flat', cls: dir },
        { label: isIndex ? 'Candles (10%)' : 'Candles (15%)', norm: candleNorm, val: `${d.consistency_pct}%`,       hint: d.consistency_pct > 50 ? 'Bullish' : 'Bearish', cls: dir },
    ];

    if (isIndex && d.vix_score != null && d.india_vix) {
        bars.push({
            label: 'VIX (15%)',
            norm:  Math.abs(d.vix_score),
            val:   d.india_vix.current.toFixed(1),
            hint:  `${d.india_vix.percentile}th pct`,
            cls:   dir,
        });
    }
    if (isIndex && d.pcr_score != null && d.oi_context) {
        bars.push({
            label: 'PCR (10%)',
            norm:  Math.abs(d.pcr_score),
            val:   d.oi_context.pcr.toFixed(2),
            hint:  d.pcr_score > 0 ? 'Put↑' : 'Call↓',
            cls:   d.pcr_score >= 0 ? 'bull' : 'bear',
        });
    }

    tdElems.barsContainer.innerHTML = bars.map(b => `
        <div class="td-row">
            <div class="td-row-label">${b.label}</div>
            <div class="td-bar-track">
                <div class="td-bar-fill ${b.cls}" style="width:${Math.round(b.norm * 100)}%"></div>
            </div>
            <div class="td-row-val">${b.val}</div>
            <div class="td-row-hint">${b.hint}</div>
        </div>`).join('');
}

// ---- Projection card ----
function renderProjection(d) {
    const nd   = d.next_day;
    const cls  = nd.direction === 'UP' ? 'up' : nd.direction === 'DOWN' ? 'down' : 'neutral';
    const icon = nd.direction === 'UP' ? '↑ UP' : nd.direction === 'DOWN' ? '↓ DOWN' : '↔ NEUTRAL';
    const fmt  = n => n != null ? Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—';

    // Direction badge in card header
    tdElems.projMeta.innerHTML = `<span class="td-status-badge ${cls}">${icon}</span>`;

    // Mini levels chart: proj_high / close / proj_low as proportional bars
    const close    = d.close;
    const projHigh = nd.proj_high;
    const projLow  = nd.proj_low;
    const minP = Math.min(projLow, close, projHigh);
    const maxP = Math.max(projLow, close, projHigh);
    const rng  = maxP - minP || 1;
    const pct  = p => Math.round(((p - minP) / rng) * 100);

    tdElems.projRows.innerHTML = `
        <div class="td-row">
            <div class="td-row-label" style="color:#a855f7">▲ High</div>
            <div class="td-bar-track">
                <div class="td-bar-fill" style="background:#a855f7;width:${pct(projHigh)}%"></div>
            </div>
            <div class="td-row-val" style="color:#a855f7">${fmt(projHigh)}</div>
            <div class="td-row-hint"></div>
        </div>
        <div class="td-row">
            <div class="td-row-label" style="color:var(--td-accent)">Close</div>
            <div class="td-bar-track">
                <div class="td-bar-fill" style="background:var(--td-accent);width:${pct(close)}%"></div>
            </div>
            <div class="td-row-val" style="color:var(--td-accent)">${fmt(close)}</div>
            <div class="td-row-hint"></div>
        </div>
        <div class="td-row">
            <div class="td-row-label" style="color:#a855f7">▼ Low</div>
            <div class="td-bar-track">
                <div class="td-bar-fill" style="background:#a855f7;width:${pct(projLow)}%"></div>
            </div>
            <div class="td-row-val" style="color:#a855f7">${fmt(projLow)}</div>
            <div class="td-row-hint"></div>
        </div>
        <div class="td-row">
            <div class="td-row-label">Range</div>
            <div class="td-row-data">
                <div class="td-row-val">${fmt(nd.expected_range)} pts</div>
                <div class="td-row-hint">${nd.atr_multiple}× ATR</div>
            </div>
        </div>`;
}

// ---- VIX & OI card ----
function renderVixOi(d) {
    const vix = d.india_vix;
    tdElems.vixMeta.textContent = `${vix.current.toFixed(1)} VIX · ${vix.percentile}th pct`;

    const vixRow = `
        <div class="td-row">
            <div class="td-row-label">VIX Range</div>
            <span style="font-size:10px;color:var(--td-muted);white-space:nowrap">${vix.year_low.toFixed(1)}</span>
            <div class="td-vix-track">
                <div class="td-vix-fill" style="width:${vix.percentile}%"></div>
                <div class="td-vix-marker" style="left:${vix.percentile}%"></div>
            </div>
            <span style="font-size:10px;color:var(--td-muted);white-space:nowrap">${vix.year_high.toFixed(1)}</span>
            <div class="td-row-val" style="min-width:36px;text-align:right">${vix.current.toFixed(2)}</div>
        </div>`;

    let oiHtml = '';
    if (d.oi_context) {
        const oi  = d.oi_context;
        const fmt = n => n != null ? Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—';
        oiHtml = `
            <div class="td-row">
                <div class="td-row-label">PCR</div>
                <div class="td-row-data">
                    <div class="td-row-val">${oi.pcr.toFixed(2)}</div>
                    <div class="td-row-hint">${d.pcr_score > 0 ? 'Put-heavy ↑' : 'Call-heavy ↓'}</div>
                </div>
            </div>
            <div class="td-row">
                <div class="td-row-label">Max Pain</div>
                <div class="td-row-data">
                    <div class="td-row-val">${fmt(oi.max_pain)}</div>
                </div>
            </div>
            <div class="td-row">
                <div class="td-row-label">ATM IV</div>
                <div class="td-row-data">
                    <div class="td-row-val">${oi.atm_iv}%</div>
                    <div class="td-row-hint">IV ${oi.iv_percentile}th pct</div>
                </div>
            </div>`;
    }

    tdElems.vixOiRows.innerHTML = vixRow + oiHtml;
}

// ---- Helpers ----
function setLoading(on) {
    tdElems.loading.style.display = on ? 'flex' : 'none';
    if (on) tdElems.results.style.display = 'none';
}
function showError(msg) { tdElems.error.textContent = msg; tdElems.error.style.display = 'block'; }
function hideError()    { tdElems.error.style.display = 'none'; }
