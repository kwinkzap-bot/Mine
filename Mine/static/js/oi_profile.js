/**
 * OI Profile – WHITE THEME logic
 * Full-width Chart with In-Chart OI Bar Overlay
 */

'use strict';

/* ── State ────────────────────────────────────────────────── */
let oipOIChart = null;
let oipOISeries = null;
let oipIntrinsicChart = null;
let oipIntrinsicSeries = null;
let oipIntrinsicPeSeries = null;
let oipOIData = null;
let oipOptionData = null;
let oipVwapSeries = null;
let oipVwapIntSeries = null;
let oipVwapIntPeSeries = null;

let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

let oipCprSeriesObj = null;
let oipAllStrikes = [];
let oipCurrentPrice = 0;
let oipSymbol = 'NIFTY';
let oipInterval = '5minute';
let oipStrikeCount = 15;
let oipMode = 'change';
let oipIsBusy = false;
let oipRafId = null;
let oipIsFirstLoad = true;
let oipAllSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];

// DOM Cache for optimized performance
const oipElems = {
    symbolInput: null, symbolList: null, interval: null, 
    spotHigh: null, spotLow: null, step: null, multiplier: null,
    view: null, showLevels: null, showVwapOI: null, showVwapInt: null,
    showCpr: null, autoHL: null, chartWrap: null, canvas: null,
    tooltip: null, refreshIcon: null, itmCE: null, itmPE: null,
    hdrPrice: null, hdrPcr: null, hdrMaxPain: null, hdrRes: null,
    hdrSupp: null, hdrCeOI: null, hdrCeChg: null, hdrPeOI: null,
    hdrPeChg: null, hdrTrend: null, hdrAtm: null, brokerSelect: null
};

function oipInitElems() {
    oipElems.symbolInput = document.getElementById('symbolSelect');
    oipElems.symbolList = document.getElementById('symbolDropdownList');
    oipElems.interval = document.getElementById('oipInterval');
    oipElems.spotHigh = document.getElementById('oipSpotHigh');
    oipElems.spotLow = document.getElementById('oipSpotLow');
    oipElems.step = document.getElementById('oipStep');
    oipElems.multiplier = document.getElementById('oipMultiplier');
    oipElems.view = document.getElementById('oipIntrinsicView');
    oipElems.showLevels = document.getElementById('oipShowLevels');
    oipElems.showVwapOI = document.getElementById('oipShowVwapOI');
    oipElems.showVwapInt = document.getElementById('oipShowVwapInt');
    oipElems.showCpr = document.getElementById('oipShowCpr');
    oipElems.autoHL = document.getElementById('oipAutoHL');
    oipElems.chartWrap = document.getElementById('oipChartWrap');
    oipElems.canvas = document.getElementById('oipOICanvas');
    oipElems.tooltip = document.getElementById('oipTooltip');
    oipElems.refreshIcon = document.getElementById('oipRefreshIcon');
    oipElems.itmCE = document.getElementById('oipItmCE');
    oipElems.itmPE = document.getElementById('oipItmPE');
    oipElems.hdrPrice = document.getElementById('hdrPrice');
    oipElems.hdrPcr = document.getElementById('hdrPcr');
    oipElems.hdrMaxPain = document.getElementById('hdrMaxPain');
    oipElems.hdrRes = document.getElementById('hdrRes');
    oipElems.hdrSupp = document.getElementById('hdrSupp');
    oipElems.hdrCeOI = document.getElementById('hdrCeOI');
    oipElems.hdrCeChg = document.getElementById('hdrCeChg');
    oipElems.hdrPeOI = document.getElementById('hdrPeOI');
    oipElems.hdrPeChg = document.getElementById('hdrPeChg');
    oipElems.hdrTrend = document.getElementById('hdrTrend');
    oipElems.hdrAtm = document.getElementById('hdrAtm');
    oipElems.brokerSelect = document.getElementById('oipBrokerSelect');
}

/* ── Bootstrap ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    oipInitElems();
    oipInitCharts();

    // Dropdown Logic
    oipElems.symbolInput.addEventListener('input', (e) => oipRenderDropdown(e.target.value.toUpperCase(), oipElems.symbolList));
    oipElems.symbolInput.addEventListener('click', function (e) {
        e.stopPropagation();
        if (oipElems.symbolList.classList.contains('show')) {
            oipElems.symbolList.classList.remove('show');
            oipElems.symbolList.classList.add('hidden');
        } else {
            this.value = '';
            oipRenderDropdown('', oipElems.symbolList);
        }
    });
    oipElems.symbolInput.addEventListener('blur', () => {
        setTimeout(() => {
            oipElems.symbolList.classList.remove('show');
            if (!oipElems.symbolInput.value.trim()) oipElems.symbolInput.value = oipSymbol;
        }, 200);
    });
    oipElems.symbolInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const val = e.target.value.trim().toUpperCase();
            if (val) oipSelectSymbol(val);
            oipElems.symbolInput.blur();
        }
    });
    document.addEventListener('click', (e) => {
        if (oipElems.symbolInput && oipElems.symbolList && !oipElems.symbolInput.contains(e.target) && !oipElems.symbolList.contains(e.target)) {
            oipElems.symbolList.classList.remove('show');
        }
    });

    fetch('/api/symbols').then(r => r.json()).then(d => { if (d.success) oipAllSymbols = d.symbols; }).catch(console.warn);

    // Toolbar Listeners
    oipElems.interval.addEventListener('change', e => {
        oipInterval = e.target.value;
        oipLoadCandles();
    });

    document.querySelectorAll('input[name="oipMode"]').forEach(radio => {
        radio.addEventListener('change', e => {
            oipMode = e.target.value;
            oipRequestDraw();
        });
    });

    [oipElems.spotHigh, oipElems.spotLow].forEach(el => {
        el?.addEventListener('change', () => oipLoadCandles(true));
    });

    oipElems.autoHL?.addEventListener('click', () => {
        oipAutoFillHighLow();
        oipLoadCandles(true);
    });

    [oipElems.step, oipElems.multiplier, oipElems.view].forEach(el => {
        el?.addEventListener('change', () => {
            const view = oipElems.view.value;
            const rescaled = (el === oipElems.view);
            const needsOptionData = (view !== 'index') && !oipOptionData;
            if (oipOIData && !needsOptionData) oipLoadCandles(false, rescaled);
            else oipLoadCandles(true, rescaled);
        });
    });

    oipElems.showLevels.addEventListener('change', () => {
        if (oipOIData && oipOIData.intrinsic) oipDrawIntrinsicLines(oipOIData.intrinsic, oipElems.view.value);
    });

    oipElems.showVwapOI.addEventListener('change', e => {
        if (oipVwapSeries) oipVwapSeries.applyOptions({ visible: e.target.checked });
    });

    oipElems.showVwapInt.addEventListener('change', e => {
        const show = e.target.checked;
        if (oipVwapIntSeries) oipVwapIntSeries.applyOptions({ visible: show });
        if (oipVwapIntPeSeries) oipVwapIntPeSeries.applyOptions({ visible: show });
    });

    oipElems.showCpr.addEventListener('change', () => {
        if (oipOIData && oipOIData.candles) oipDrawCpr(oipOIData.candles);
    });

    // Order buttons
    document.querySelectorAll('.oip-order-btn').forEach(btn => {
        btn.addEventListener('click', () => oipPlaceOrder(btn.dataset.side, btn.dataset.action, btn));
    });

    // Tooltip Logic
    oipOIChart.subscribeCrosshairMove(param => {
        if (!param.point || !oipAllStrikes.length) {
            oipElems.tooltip.classList.add('hidden');
            return;
        }
        const W = oipElems.canvas.width / window.devicePixelRatio;
        const plotRight = W - 70;
        const MAX_BAR_PX = Math.min(plotRight * 0.35, 300);
        if (param.point.x < (plotRight - MAX_BAR_PX - 20) || param.point.x > plotRight + 10) {
            oipElems.tooltip.classList.add('hidden');
            return;
        }
        const price = oipOISeries.coordinateToPrice(param.point.y);
        if (!price) return;
        let nearest = oipAllStrikes[0];
        let minDist = Math.abs(oipAllStrikes[0].strike - price);
        for (let i = 0; i < oipAllStrikes.length; i++) {
            const s = oipAllStrikes[i];
            const d = Math.abs(s.strike - price);
            if (d < minDist) { minDist = d; nearest = s; }
        }
        if (minDist > 100) { oipElems.tooltip.classList.add('hidden'); return; }
        const isChg = (oipMode === 'change');
        oipElems.tooltip.innerHTML = `
            <div class="strike">Strike: ${nearest.strike}</div>
            <div class="row"><div class="dot ce"></div><div class="lbl">Call:</div><div class="val">${fmtL(isChg ? nearest.ce_change_in_oi : nearest.ce_oi)}</div></div>
            <div class="row"><div class="dot pe"></div><div class="lbl">Put:</div><div class="val">${fmtL(isChg ? nearest.pe_change_in_oi : nearest.pe_oi)}</div></div>
        `;
        oipElems.tooltip.classList.remove('hidden');
        oipElems.tooltip.style.left = (param.point.x - oipElems.tooltip.offsetWidth - 15) + 'px';
        oipElems.tooltip.style.top = (param.point.y - oipElems.tooltip.offsetHeight / 2) + 'px';
    });

    oipFullRefresh(true);

    setInterval(() => {
        if (!document.hidden && oipIsMarketOpen()) oipFullRefresh(false);
    }, 5000);
});

/* ── Lightweight Charts Initialization ──────────────────────── */
function oipInitCharts() {
    const elOI = document.getElementById('oipCandleChart');
    const wrapOI = oipElems.chartWrap;
    if (elOI && typeof LightweightCharts !== 'undefined') {
        oipOIChart = creatBaseChart(elOI);
        oipOISeries = oipOIChart.addCandlestickSeries(candleStyle());
        oipVwapSeries = oipOIChart.addLineSeries({
            color: '#f59e0b', lineWidth: 2, title: '',
            visible: oipElems.showVwapOI.checked,
            priceLineVisible: false, lastValueVisible: false
        });
        oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(() => oipRequestDraw());
        oipOIChart.timeScale().subscribeVisibleTimeRangeChange(() => oipRequestDraw());
        const ps = oipOIChart.priceScale('right');
        if (ps && typeof ps.subscribePriceRangeChange === 'function') {
            ps.subscribePriceRangeChange(() => oipRequestDraw());
        }
        oipOIChart.subscribeCrosshairMove(() => oipRequestDraw());
        new ResizeObserver(() => { syncSize(oipOIChart, wrapOI); oipRequestDraw(); }).observe(wrapOI);
    }
    const elInt = document.getElementById('oipIntrinsicChart');
    if (elInt && typeof TradingViewChart !== 'undefined') {
        oipIntrinsicChart = TradingViewChart.create({
            containerId: 'oipIntrinsicChart', data: [], type: 'COMBINED',
            isCombined: true, timeframe: oipInterval, options: { height: 300 }
        });
        oipIntrinsicSeries = oipIntrinsicChart.ceSeries || oipIntrinsicChart.series;
        oipIntrinsicPeSeries = oipIntrinsicChart.peSeries;
        const showV = oipElems.showVwapInt.checked;
        oipVwapIntSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#10b981', lineWidth: 2, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false
        });
        oipVwapIntPeSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#8b5cf6', lineWidth: 2, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false
        });
    }
}

function creatBaseChart(el) {
    return LightweightCharts.createChart(el, {
        width: el.clientWidth || 1200, height: 300,
        layout: { textColor: '#374151', background: { type: 'solid', color: '#ffffff' } },
        grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
        crosshair: { mode: 0, vertLine: { color: '#9ca3af', style: 3 }, horzLine: { color: '#9ca3af', style: 3, labelBackgroundColor: '#0969da' } },
        timeScale: { timeVisible: true, textColor: '#6b7280', borderColor: 'transparent', rightOffset: 60 },
        rightPriceScale: { textColor: '#6b7280', borderColor: 'transparent', width: 70, autoScale: true, scaleMargins: { top: 0.05, bottom: 0.05 } },
        handleScroll: true, handleScale: true, localization: { locale: 'en-IN' }
    });
}

function candleStyle() {
    return {
        upColor: '#10b981', downColor: '#ef4444',
        borderUpColor: '#10b981', borderDownColor: '#ef4444',
        wickUpColor: '#10b981', wickDownColor: '#ef4444',
        priceLineStyle: 1, priceLineWidth: 1
    };
}

function syncSize(chart, wrap) {
    const w = wrap.clientWidth;
    const h = wrap.clientHeight;
    if (chart && w > 0 && h > 0) chart.applyOptions({ width: w, height: h });
}

function oipRequestDraw() {
    if (!oipRafId) oipRafId = requestAnimationFrame(oipDrawOIBars);
}

/* ── Canvas OI overlay ────────────────────────────────────── */
function oipDrawOIBars() {
    oipRafId = null;
    const canvas = oipElems.canvas;
    const wrap = oipElems.chartWrap;
    if (!canvas || !wrap || !oipOISeries) return;
    const W = wrap.clientWidth;
    const H = wrap.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
        canvas.width = W * dpr; canvas.height = H * dpr;
        canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!oipAllStrikes.length) return;
    const priceTop = oipOISeries.coordinateToPrice(0);
    const priceBottom = oipOISeries.coordinateToPrice(H);
    let filtered = [];
    if (priceTop !== null && priceBottom !== null) {
        const minP = Math.min(priceTop, priceBottom);
        const maxP = Math.max(priceTop, priceBottom);
        const pad = (maxP - minP) * 0.1;
        filtered = oipAllStrikes.filter(s => s.strike >= (minP - pad) && s.strike <= (maxP + pad));
    } else filtered = oipFilterStrikes(oipAllStrikes, oipCurrentPrice, oipStrikeCount);
    if (filtered.length > 1) filtered.sort((a, b) => a.strike - b.strike);
    if (!filtered.length) return;
    const plotRight = W - 70;
    const MAX_BAR_PX = Math.min(plotRight * 0.35, 300);
    const getCE = (s) => (oipMode === 'total' ? (s.ce_oi || 0) : (s.ce_change_in_oi || 0));
    const getPE = (s) => (oipMode === 'total' ? (s.pe_oi || 0) : (s.pe_change_in_oi || 0));
    const maxVal = Math.max(...filtered.flatMap(s => [Math.abs(getCE(s)), Math.abs(getPE(s))]), 1);
    let barH = 8;
    if (filtered.length >= 2) {
        const y0 = oipOISeries.priceToCoordinate(filtered[0].strike);
        const y1 = oipOISeries.priceToCoordinate(filtered[1].strike);
        if (y0 !== null && y1 !== null) barH = Math.max(1, Math.min(25, Math.abs(y1 - y0) * 0.38));
    }
    const ceCol = 'rgba(239, 68, 68, 0.6)', peCol = 'rgba(16, 185, 129, 0.6)', ceColMax = 'rgba(239, 68, 68, 0.95)', peColMax = 'rgba(16, 185, 129, 0.95)';
    let ceMaxStr = 0, peMaxStr = 0, maxCEV = -1, maxPEV = -1;
    filtered.forEach(s => {
        const c = Math.abs(getCE(s)), p = Math.abs(getPE(s));
        if (c > maxCEV) { maxCEV = c; ceMaxStr = s.strike; }
        if (p > maxPEV) { maxPEV = p; peMaxStr = s.strike; }
    });
    filtered.forEach(s => {
        const y = oipOISeries.priceToCoordinate(s.strike);
        if (y === null || y < -50 || y > H + 50) return;
        const valCE = getCE(s), valPE = getPE(s);
        const ceW = (Math.abs(valCE) / maxVal) * MAX_BAR_PX, peW = (Math.abs(valPE) / maxVal) * MAX_BAR_PX;
        if (valCE !== 0) {
            ctx.fillStyle = ctx.strokeStyle = (s.strike === ceMaxStr) ? ceColMax : ceCol;
            if (valCE < 0) ctx.strokeRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
            else ctx.fillRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
        }
        if (valPE !== 0) {
            ctx.fillStyle = ctx.strokeStyle = (s.strike === peMaxStr) ? peColMax : peCol;
            if (valPE < 0) ctx.strokeRect(plotRight - peW, y + 0.5, peW, barH);
            else ctx.fillRect(plotRight - peW, y + 0.5, peW, barH);
        }
    });
    ctx.strokeStyle = 'rgba(0,0,0,0.1)'; ctx.beginPath(); ctx.moveTo(plotRight, 0); ctx.lineTo(plotRight, H); ctx.stroke();
}

function oipFilterStrikes(strikes, price, n) {
    if (!strikes.length) return [];
    const sorted = [...strikes].sort((a, b) => a.strike - b.strike);
    if (!price || n >= 999) return sorted;
    let atmI = 0, mindI = Infinity;
    sorted.forEach((s, i) => { const d = Math.abs(s.strike - price); if (d < mindI) { mindI = d; atmI = i; } });
    return sorted.slice(Math.max(0, atmI - n), Math.min(sorted.length - 1, atmI + n) + 1);
}

/* ── Refresh Logic ────────────────────────────────────────── */
async function oipFullRefresh(resetZoom = false) {
    if (oipIsBusy) return;
    oipIsBusy = true; if (resetZoom) oipIsFirstLoad = true;
    setRefreshBtn(true);
    try { await Promise.allSettled([oipLoadOI(), oipLoadCandles(true, resetZoom)]); } 
    catch (err) { console.error('[OIP] Refresh Err:', err); } 
    finally { oipIsBusy = false; setRefreshBtn(false); }
}

async function oipLoadOI() {
    try {
        const res = await fetch('/api/open-interest', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: oipSymbol })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error);
        oipOIData = Object.assign(oipOIData || {}, data);
        oipAllStrikes = data.strikes || [];
        oipCurrentPrice = data.current_price || 0;
        oipUpdateHeader(data);
        oipRequestDraw();
    } catch (e) { console.warn('[OIP] OI Load Err:', e); }
}

async function oipLoadCandles(forceFetch = true, resetZoom = false) {
    try {
        const h = parseFloat(oipElems.spotHigh.value), l = parseFloat(oipElems.spotLow.value);
        const s = parseInt(oipElems.step.value), m = parseInt(oipElems.multiplier.value);
        const view = oipElems.view.value;
        const needsOptionData = (view !== 'index') && !oipOptionData;
        const autoHL = oipIsFirstLoad;
        if (!forceFetch && oipOIData && !needsOptionData) { oipRefreshLocalView(view, resetZoom); return; }
        const res = await fetch(`/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=3&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=${autoHL}&_t=${Date.now()}`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);
        oipOIData = Object.assign(oipOIData || {}, data);
        const indexCandles = data.candles || [];
        if (oipOISeries && indexCandles.length) {
            oipOISeries.setData(indexCandles);
            if (oipVwapSeries) oipVwapSeries.setData(oipCalculateVWAP(indexCandles));
            oipDrawCpr(indexCandles);
        }
        if (autoHL && data.intrinsic?.spot_high) {
            oipElems.spotHigh.value = data.intrinsic.spot_high; oipElems.spotLow.value = data.intrinsic.spot_low;
        }
        if (oipIntrinsicChart) {
            if (view === 'index') {
                oipIntrinsicChart.update(indexCandles, null, true);
                oipElems.itmCE.textContent = 'NIFTY'; oipElems.itmPE.textContent = 'Index';
                if (data.intrinsic) oipDrawIntrinsicLines(data.intrinsic, view);
            } else {
                const ceStrike = data.intrinsic?.itm_ce_strike, peStrike = data.intrinsic?.itm_pe_strike;
                oipCurrentCEStrike = ceStrike; oipCurrentPEStrike = peStrike;
                if (ceStrike && peStrike) {
                    let ceData = [], peData = [];
                    if (data.ce_opt_candles && data.pe_opt_candles) {
                        ceData = data.ce_opt_candles.map(c => ({ ...c, type: 'CE' }));
                        peData = data.pe_opt_candles.map(c => ({ ...c, type: 'PE' }));
                        oipOptionData = [...ceData, ...peData];
                    }
                    if (ceData.length || peData.length) {
                        oipElems.itmCE.textContent = `${ceStrike} CE`; oipElems.itmPE.textContent = `${peStrike} PE`;
                        if (view === 'combined') {
                            oipIntrinsicChart.setVisibleSeries(true, true); oipIntrinsicChart.update(ceData, peData, true);
                            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
                            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));
                            oipIntrinsicChart.setMarkers(oipCalculatePineSignals(ceData, data.intrinsic.ce_levels, 'CE'), oipCalculatePineSignals(peData, data.intrinsic.pe_levels, 'PE'));
                        } else if (view === 'ce') {
                            oipIntrinsicChart.setVisibleSeries(true, false); oipIntrinsicChart.update(ceData, null, true);
                            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
                            oipIntrinsicChart.setMarkers(oipCalculatePineSignals(ceData, data.intrinsic.ce_levels, 'CE'), []);
                        } else {
                            oipIntrinsicChart.setVisibleSeries(false, true); oipIntrinsicChart.update(null, peData, true);
                            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));
                            oipIntrinsicChart.setMarkers([], oipCalculatePineSignals(peData, data.intrinsic.pe_levels, 'PE'));
                        }
                    }
                }
                if (data.intrinsic) oipDrawIntrinsicLines(data.intrinsic, view);
            }
            oipRequestDraw();
        }
        if (oipIsFirstLoad && indexCandles.length) {
            const visibleLen = Math.min(indexCandles.length, 90);
            oipOIChart.timeScale().setVisibleLogicalRange({ from: indexCandles.length - visibleLen, to: indexCandles.length + 30 });
            oipIsFirstLoad = false;
        }
    } catch (e) { console.error('[OIP] Refresh Err:', e); }
}

function oipUpdateHeader(data) {
    const p = data.current_price || 0, pcr = data.pcr_oi || 0, mp = data.max_pain || '--';
    const ce = data.ce_summary || {}, pe = data.pe_summary || {}, strikes = data.strikes || [];
    const ceSorted = [...strikes].sort((a, b) => (b.ce_oi || 0) - (a.ce_oi || 0));
    const peSorted = [...strikes].sort((a, b) => (b.pe_oi || 0) - (a.pe_oi || 0));
    oipElems.hdrPrice.textContent = p.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    oipElems.hdrPcr.textContent = pcr.toFixed(2); oipElems.hdrMaxPain.textContent = mp;
    oipElems.hdrRes.textContent = ceSorted[0]?.strike || '--'; oipElems.hdrSupp.textContent = peSorted[0]?.strike || '--';
    oipElems.hdrCeOI.textContent = fmtL(ce.total_oi); oipElems.hdrCeChg.textContent = fmtL(ce.change_in_oi);
    oipElems.hdrPeOI.textContent = fmtL(pe.total_oi); oipElems.hdrPeChg.textContent = fmtL(pe.change_in_oi);
    if (pcr >= 1.25) { oipElems.hdrTrend.textContent = 'Bullish'; oipElems.hdrTrend.className = 'oip-hdr-val grn'; }
    else if (pcr <= 0.6) { oipElems.hdrTrend.textContent = 'Bearish'; oipElems.hdrTrend.className = 'oip-hdr-val red'; }
    else { oipElems.hdrTrend.textContent = 'Neutral'; oipElems.hdrTrend.className = 'oip-hdr-val'; }
    let atm = '--', mind = Infinity;
    strikes.forEach(s => { const d = Math.abs(s.strike - p); if (d < mind) { mind = d; atm = s.strike; } });
    oipElems.hdrAtm.textContent = atm;
}

let oipLevelLines = [];
function oipDrawIntrinsicLines(intrinsic, view = 'index') {
    if (!oipIntrinsicChart || !oipIntrinsicSeries) return;
    oipLevelLines.forEach(l => { 
        try { oipIntrinsicSeries.removePriceLine(l); if(oipIntrinsicPeSeries) oipIntrinsicPeSeries.removePriceLine(l); } catch(e){} 
    });
    oipLevelLines = [];
    if (!oipElems.showLevels.checked || !intrinsic) return;
    const { ce_intrinsic, pe_intrinsic } = intrinsic;
    const step = parseInt(oipElems.step.value) || 50, mult = parseInt(oipElems.multiplier.value) || 12;
    const candlesCE = (intrinsic.ce_opt_candles || oipOIData?.ce_opt_candles || []);
    const candlesPE = (intrinsic.pe_opt_candles || oipOIData?.pe_opt_candles || []);
    let highest = 0;
    const last = candlesCE[candlesCE.length-1] || candlesPE[candlesPE.length-1];
    if (last) {
        const sod = new Date(last.time * 1000).setHours(0,0,0,0)/1000;
        const curCE = candlesCE.filter(c => c.time >= sod), curPE = candlesPE.filter(c => c.time >= sod);
        if (view === 'ce') highest = Math.max(...curCE.map(c => c.high), 0);
        else if (view === 'pe') highest = Math.max(...curPE.map(c => c.high), 0);
        else highest = Math.max(...curCE.map(c => c.high), ...curPE.map(c => c.high), 0);
    }
    const ceLevels = [], peLevels = [];
    for (let i=1; i<=mult || (ce_intrinsic+step*i) < highest+(2*step); i++) { ceLevels.push(ce_intrinsic+step*i); if(i>60) break; }
    for (let i=1; i<=mult || (pe_intrinsic+step*i) < highest+(2*step); i++) { peLevels.push(pe_intrinsic+step*i); if(i>60) break; }
    if (view === 'ce' || view === 'combined' || view === 'index') {
        oipLevelLines.push(oipIntrinsicSeries.createPriceLine({ price: ce_intrinsic, color: '#10b981', lineWidth: 2, title: 'CE IV' }));
        ceLevels.forEach(lvl => oipLevelLines.push(oipIntrinsicSeries.createPriceLine({ price: lvl, color: '#10b981', lineWidth: 1, title: '' })));
    }
    if (view === 'pe' || view === 'combined' || view === 'index') {
        const s = oipIntrinsicPeSeries || oipIntrinsicSeries;
        oipLevelLines.push(s.createPriceLine({ price: pe_intrinsic, color: '#8b5cf6', lineWidth: 2, title: 'PE IV' }));
        peLevels.forEach(lvl => oipLevelLines.push(s.createPriceLine({ price: lvl, color: '#8b5cf6', lineWidth: 1, title: '' })));
    }
}

function fmtL(n) {
    if (n == null) return '--';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '+';
    if (abs >= 10000000) return sign + (abs / 10000000).toFixed(2) + ' Cr';
    if (abs >= 100000) return sign + (abs / 100000).toFixed(2) + ' L';
    return n.toLocaleString('en-IN');
}

function setRefreshBtn(l) { oipElems.refreshIcon?.classList.toggle('spin', l); }

function oipIsMarketOpen() {
    const n = new Date(); if (n.getDay() === 0 || n.getDay() === 6) return false;
    const ist = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(n);
    const [h, m] = ist.split(':').map(Number); const mins = h * 60 + m;
    return mins >= 555 && mins <= 930;
}

function oipRefreshLocalView(view, resetZoom = false) {
    if (!oipOIData || !oipIntrinsicChart) return;
    const indexCandles = oipOIData.candles || [];
    if (view === 'index') {
        oipIntrinsicChart.update(indexCandles, null, resetZoom);
        if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(indexCandles));
        if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
    } else if (oipOptionData) {
        const ceData = oipOptionData.filter(c => c.type === 'CE'), peData = oipOptionData.filter(c => c.type === 'PE');
        if (view === 'combined') {
            oipIntrinsicChart.update(ceData, peData, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));
        } else if (view === 'ce') {
            oipIntrinsicChart.update(ceData, null, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
        } else {
            oipIntrinsicChart.update(null, peData, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData([]);
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));
        }
    }
    if (oipOIData.intrinsic) oipDrawIntrinsicLines(oipOIData.intrinsic, view);
}

function oipCalculatePineSignals(candles, levels, type) {
    if (!candles || candles.length < 2 || !levels) return [];
    const markers = []; let tradeActive = false, activeSL = null, activeTarget = null;
    for (let i = 1; i < candles.length; i++) {
        const c = candles[i], p = candles[i - 1];
        if (tradeActive) {
            if (activeTarget && c.high >= activeTarget) { 
                markers.push({ time: c.time, position: 'aboveBar', color: type === 'CE' ? '#10b981' : '#8b5cf6', shape: 'circle', text: '✔' }); 
                tradeActive = false; continue; 
            }
            if (activeSL && c.low <= activeSL) { 
                markers.push({ time: c.time, position: 'belowBar', color: type === 'CE' ? '#ef4444' : '#000000', shape: 'circle', text: '✖' }); 
                tradeActive = false; continue; 
            }
        }
        if (!tradeActive) {
            let trgIdx = -1;
            for (let j = 0; j < levels.length; j++) { if (p.close <= levels[j] && c.close > levels[j]) { trgIdx = j; break; } }
            if (trgIdx !== -1) {
                markers.push({ time: c.time, position: 'belowBar', color: type === 'CE' ? '#10b981' : '#8b5cf6', shape: 'arrowUp', text: '' });
                tradeActive = true; activeSL = p.low; activeTarget = (trgIdx + 1 < levels.length) ? levels[trgIdx + 1] : null;
            }
        }
    }
    return markers;
}

function oipCalculateDynamicCPR(candles) {
    if (!candles || !candles.length) return null;
    const days = []; let currentDay = null;
    for (const c of candles) {
        const ds = new Date(c.time * 1000).toDateString();
        if (!currentDay || currentDay.date !== ds) {
            if (currentDay) days.push(currentDay);
            const d = new Date(c.time * 1000);
            currentDay = { date: ds, isoDate: `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`, high: c.high, low: c.low, close: c.close, times: [], closes: [] };
        }
        currentDay.high = Math.max(currentDay.high, c.high); currentDay.low = Math.min(currentDay.low, c.low); currentDay.close = c.close;
        currentDay.times.push(c.time); currentDay.closes.push(c.close);
    }
    if (currentDay) days.push(currentDay);
    let res = { pp: [], tc: [], bc: [], prevH: [], prevL: [], r1: [], s1: [], r0_5: [], s0_5: [], r0_25: [], s0_25: [], cr3: [], cs3: [], r1_box: [], s1_box: [], r0_5_box: [], s0_5_box: [] };
    for (let i = 1; i < days.length; i++) {
        const prev = days[i - 1], curr = days[i];
        let oH = prev.high, oL = prev.low, oC = prev.close;
        if (oipOIData?.daily_ohlc?.[prev.isoDate]) { const t = oipOIData.daily_ohlc[prev.isoDate]; oH = t.high; oL = t.low; oC = t.close; }
        const pp = (oH + oL + oC) / 3; let bc = (oH + oL) / 2, tc = (2 * pp) - bc; if (bc > tc) [bc, tc] = [tc, bc];
        const range = oH - oL, r1 = (pp * 2) - oL, s1 = (pp * 2) - oH, r0_5 = (pp + r1) / 2, s0_5 = (pp + s1) / 2, r0_25 = r0_5 + (oH - r0_5) / 4, s0_25 = s0_5 - (s0_5 - oL) / 4, cr3 = oC + (range * 1.1) / 4, cs3 = oC - (range * 1.1) / 4;
        for (const t of curr.times) {
            res.pp.push({ time: t, value: pp }); res.tc.push({ time: t, value: tc }); res.bc.push({ time: t, value: bc });
            res.prevH.push({ time: t, value: oH }); res.prevL.push({ time: t, value: oL }); res.r1.push({ time: t, value: r1 }); res.s1.push({ time: t, value: s1 });
            res.r0_5.push({ time: t, value: r0_5 }); res.s0_5.push({ time: t, value: s0_5 }); res.r0_25.push({ time: t, value: r0_25 }); res.s0_25.push({ time: t, value: s0_25 });
            res.cr3.push({ time: t, value: cr3 }); res.cs3.push({ time: t, value: cs3 });
            res.r1_box.push({ time: t, open: r1, close: oH, high: Math.max(r1, oH), low: Math.min(r1, oH) });
            res.s1_box.push({ time: t, open: s1, close: oL, high: Math.max(s1, oL), low: Math.min(s1, oL) });
            res.r0_5_box.push({ time: t, open: r0_5, close: r0_25, high: Math.max(r0_5, r0_25), low: Math.min(r0_5, r0_25) });
            res.s0_5_box.push({ time: t, open: s0_5, close: s0_25, high: Math.max(s0_5, s0_25), low: Math.min(s0_5, s0_25) });
        }
    }
    return res;
}

function oipDrawCpr(candles) {
    if (!oipOIChart || !oipOISeries) return;
    if (!oipCprSeriesObj) {
        const boxStyle = { upColor: 'rgba(234, 179, 8, 0.05)', downColor: 'rgba(234, 179, 8, 0.05)', borderVisible: false, wickVisible: false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
        oipCprSeriesObj = {
            r1_box: oipOIChart.addCandlestickSeries(boxStyle), s1_box: oipOIChart.addCandlestickSeries(boxStyle), 
            r0_5_box: oipOIChart.addCandlestickSeries({...boxStyle, upColor: 'rgba(239, 68, 68, 0.05)', downColor: 'rgba(239, 68, 68, 0.05)'}),
            s0_5_box: oipOIChart.addCandlestickSeries({...boxStyle, upColor: 'rgba(34, 197, 94, 0.05)', downColor: 'rgba(34, 197, 94, 0.05)'}),
            prevH: oipOIChart.addLineSeries({ color: '#000000', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            r1: oipOIChart.addLineSeries({ color: '#eab308', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            r0_25: oipOIChart.addLineSeries({ color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            r0_5: oipOIChart.addLineSeries({ color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            tc: oipOIChart.addLineSeries({ color: '#06b6d4', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            cr3: oipOIChart.addLineSeries({ color: '#a855f7', lineWidth: 2, lastValueVisible: false, priceLineVisible: false }),
            pp: oipOIChart.addLineSeries({ color: '#06b6d4', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            bc: oipOIChart.addLineSeries({ color: '#06b6d4', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            s0_5: oipOIChart.addLineSeries({ color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            s0_25: oipOIChart.addLineSeries({ color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            prevL: oipOIChart.addLineSeries({ color: '#000000', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
            cs3: oipOIChart.addLineSeries({ color: '#a855f7', lineWidth: 2, lastValueVisible: false, priceLineVisible: false }),
            s1: oipOIChart.addLineSeries({ color: '#eab308', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }),
        };
    }
    const show = oipElems.showCpr.checked;
    if (!show || !candles || !candles.length) { Object.values(oipCprSeriesObj).forEach(s => s.applyOptions({ visible: false })); return; }
    Object.values(oipCprSeriesObj).forEach(s => s.applyOptions({ visible: true }));
    const cprData = oipCalculateDynamicCPR(candles);
    if (cprData) { Object.keys(oipCprSeriesObj).forEach(k => oipCprSeriesObj[k].setData(cprData[k])); }
}

function oipAutoFillHighLow() {
    if (!oipOIData?.candles?.length) return;
    const subset = oipOIData.candles.slice(-12); if (!subset.length) return;
    const rh = Math.max(...subset.map(c => c.high)), rl = Math.min(...subset.map(c => c.low));
    oipElems.spotHigh.value = Math.round(rh*100)/100; oipElems.spotLow.value = Math.floor(rl*100)/100;
    oipLoadCandles(true, false);
}

function oipCalculateVWAP(candles) {
    if (!candles || !candles.length) return [];
    let cpv = 0, cv = 0, ld = null;
    return candles.map(c => {
        const d = new Date(c.time * 1000).toDateString();
        if (d !== ld) { cpv = 0; cv = 0; ld = d; }
        const v = c.volume > 0 ? c.volume : 1, tp = (c.high + c.low + c.close) / 3;
        cpv += tp * v; cv += v;
        return { time: c.time, value: cpv / cv };
    });
}

async function oipPlaceOrder(side, action, btn) {
    const strike = (side === 'CE') ? oipCurrentCEStrike : oipCurrentPEStrike;
    if (!strike) { showNotification(`No ${side} strike available.`, 'error'); return; }
    const broker = oipElems.brokerSelect?.value || 'kotak_neo';
    btn.disabled = true; const ot = btn.title; btn.title = "Placing...";
    try {
        const res = await fetch('/api/intraday-920/place-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content },
            body: JSON.stringify({ symbol: oipSymbol, strike: strike, option_type: side, action: action, broker: broker })
        });
        const r = await res.json();
        if (r.success) showNotification(`✅ Success! ${action} ${side} ${strike}`, 'success');
        else showNotification(`❌ ${r.error || 'Failed'}`, 'error');
    } catch (e) { showNotification(`Order error: ${e.message}`, 'error'); } 
    finally { btn.disabled = false; btn.title = ot; }
}

function oipRenderDropdown(filter, list) {
    if (!list) return; list.innerHTML = '';
    const indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX'], dm = { 'NIFTY': 'NIFTY 50', 'BANKNIFTY': 'NIFTY BANK', 'FINNIFTY': 'NIFTY FIN SERVICE', 'MIDCPNIFTY': 'NIFTY MIDCAP', 'SENSEX': 'SENSEX' };
    const matches = oipAllSymbols.filter(s => !filter || s.includes(filter) || (dm[s] || s).toUpperCase().includes(filter))
        .sort((a, b) => { const ai = indices.indexOf(a), bi = indices.indexOf(b); if (ai!==-1 && bi!==-1) return ai-bi; if (ai!==-1) return -1; if (bi!==-1) return 1; return a.localeCompare(b); });
    if (!matches.length) { list.classList.remove('show'); return; }
    matches.forEach(s => {
        const li = document.createElement('li'), d = dm[s] || s;
        li.innerHTML = `<strong>${d}</strong> ${d !== s ? `<span style="font-size:0.8em; color:#888;">(${s})</span>` : ''}`;
        if (s === oipSymbol) li.classList.add('highlighted');
        li.addEventListener('click', () => { oipSelectSymbol(s); list.classList.remove('show'); });
        list.appendChild(li);
    });
    list.classList.add('show');
}

function oipSelectSymbol(s) {
    oipSymbol = s; oipElems.symbolInput.value = s;
    const se = oipElems.step;
    if (se) { if(s==='BANKNIFTY'||s==='SENSEX') se.value='100'; else if(s==='MIDCPNIFTY') se.value='25'; else se.value='50'; }
    oipFullRefresh(true);
}
