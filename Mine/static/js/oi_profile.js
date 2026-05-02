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
let oipEmaSeries = null;

let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

let oipCprSeriesObj = null;
let oipAllStrikes = [];
let oipCurrentPrice = 0;
let oipSymbol = 'NIFTY';
let oipInterval = 'minute';
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
    showCpr: null, showEMA: null, showRSI: null, autoHL: null, chartWrap: null, canvas: null,
    tooltip: null, refreshIcon: null, itmCE: null, itmPE: null,
    hdrPrice: null, hdrPcr: null, hdrMaxPain: null, hdrRes: null,
    hdrSupp: null, hdrCeOI: null, hdrCeChg: null, hdrPeOI: null,
    hdrPeChg: null, hdrTrend: null, hdrAtm: null, brokerSelect: null,
    showPremium: null, showSignals: null, first5mATM: null, targetDistance: null, customStrikeCheck: null, customStrikeDropdown: null
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
    oipElems.showEMA = document.getElementById('oipShowEMA');
    oipElems.showRSI = document.getElementById('oipShowRSI');
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
    oipElems.showPremium   = document.getElementById('oipShowPremium');
    oipElems.showSignals   = document.getElementById('oipShowSignals');
    oipElems.first5mATM    = document.getElementById('oipFirst5mATM');
    oipElems.customStrikeCheck = document.getElementById('oipCustomStrikeCheck');
    oipElems.customStrikeDropdown = document.getElementById('oipCustomStrikeDropdown');
    oipElems.targetDistance = document.getElementById('oipTargetDistance');
    
    if (oipElems.customStrikeDropdown) {
        let opts = '';
        for (let s = 20000; s <= 28000; s += 50) {
            opts += `<option value="${s}">${s}</option>`;
        }
        oipElems.customStrikeDropdown.innerHTML = opts;
    }
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
        if (oipOIData && oipOIData.intrinsic) oipRefreshLocalView(oipElems.view.value, false);
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

    oipElems.showEMA?.addEventListener('change', e => {
        if (oipEmaSeries) oipEmaSeries.applyOptions({ visible: e.target.checked });
    });

    oipElems.showRSI?.addEventListener('change', () => {
        if (oipOIData && oipOIData.candles) oipDrawRSI(oipOIData.candles);
    });

    oipElems.showPremium?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view.value);
    });

    oipElems.showSignals?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view.value);
    });

    oipElems.first5mATM?.addEventListener('change', (e) => {
        if (e.target.checked && oipElems.customStrikeCheck) {
            oipElems.customStrikeCheck.checked = false;
        }
        oipLoadCandles(true, false);
    });

    oipElems.customStrikeCheck?.addEventListener('change', (e) => {
        if (e.target.checked && oipElems.first5mATM) {
            oipElems.first5mATM.checked = false;
            // Always snap to ATM logic disabled here? The user said "While enable the check it should be default ATM Strike" 
            if (oipElems.customStrikeDropdown) {
                const step = parseInt(oipElems.step?.value) || 50;
                let refPrice = oipCurrentPrice;
                if (!refPrice && oipElems.spotHigh?.value && oipElems.spotLow?.value) {
                    refPrice = (parseFloat(oipElems.spotHigh.value) + parseFloat(oipElems.spotLow.value)) / 2;
                }
                if (refPrice > 0) {
                    oipElems.customStrikeDropdown.value = Math.round(refPrice / step) * step;
                }
            }
        }
        oipLoadCandles(true, false);
    });

    oipElems.customStrikeDropdown?.addEventListener('change', () => {
        if (oipElems.customStrikeCheck?.checked) {
            oipLoadCandles(true, false);
        }
    });

    oipElems.targetDistance?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view.value);
    });

    // Order buttons
    document.querySelectorAll('.oip-order-btn').forEach(btn => {
        btn.addEventListener('click', () => oipPlaceOrder(btn.dataset.side, btn.dataset.action, btn));
    });

    // Tooltip Logic
    // Tooltip Logic - Commented out as requested
    /*
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
    */

    oipFullRefresh(true);

    setInterval(() => {
        if (!document.hidden && oipIsMarketOpen()) oipFullRefresh(false);
    }, 1000);
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
        oipEmaSeries = oipOIChart.addLineSeries({
            color: '#3b82f6', lineWidth: 2, title: '',
            visible: oipElems.showEMA?.checked,
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
            color: '#ef4444', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false
        });
        oipVwapIntPeSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#000000', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false
        });
        oipInitPremiumSeries();
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
    ctx.font = 'bold 10px sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'right';
    filtered.forEach(s => {
        const y = oipOISeries.priceToCoordinate(s.strike);
        if (y === null || y < -50 || y > H + 50) return;
        const valCE = getCE(s), valPE = getPE(s);
        const ceW = (Math.abs(valCE) / maxVal) * MAX_BAR_PX, peW = (Math.abs(valPE) / maxVal) * MAX_BAR_PX;
        if (valCE !== 0) {
            ctx.fillStyle = ctx.strokeStyle = (s.strike === ceMaxStr) ? ceColMax : ceCol;
            if (valCE < 0) ctx.strokeRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
            else ctx.fillRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
            
            // Add value on the left side of the Y-axis (Black color, larger font)
            ctx.fillStyle = '#000000';
            ctx.fillText(fmtL(valCE) + ' C', plotRight - 4, y - (barH / 2) - 0.5);
        }
        if (valPE !== 0) {
            ctx.fillStyle = ctx.strokeStyle = (s.strike === peMaxStr) ? peColMax : peCol;
            if (valPE < 0) ctx.strokeRect(plotRight - peW, y + 0.5, peW, barH);
            else ctx.fillRect(plotRight - peW, y + 0.5, peW, barH);
            
            // Add value on the left side of the Y-axis (Black color, larger font)
            ctx.fillStyle = '#000000';
            ctx.fillText(fmtL(valPE) + ' P', plotRight - 4, y + (barH / 2) + 0.5);
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
        const first5m = oipElems.first5mATM?.checked || false;
        const customStrike = (oipElems.customStrikeCheck?.checked && oipElems.customStrikeDropdown?.value) ? oipElems.customStrikeDropdown.value : '';
        if (!forceFetch && oipOIData && !needsOptionData) { oipRefreshLocalView(view, resetZoom); return; }
        const res = await fetch(`/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=3&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=${autoHL}&first_5m_atm=${first5m}&custom_strike=${customStrike}&_t=${Date.now()}`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);
        oipOIData = Object.assign(oipOIData || {}, data);
        const indexCandles = data.candles || [];
        if (oipOISeries && indexCandles.length) {
            oipOISeries.setData(indexCandles);
            if (oipVwapSeries) oipVwapSeries.setData(oipCalculateVWAP(indexCandles));
            if (oipEmaSeries) oipEmaSeries.applyOptions({ visible: oipElems.showEMA?.checked ?? false });
            if (oipEmaSeries) oipEmaSeries.setData(oipCalculateDynamicEMA(indexCandles, oipInterval));
            oipDrawCpr(indexCandles);
            oipDrawRSI(indexCandles);
        }
        if (autoHL && data.intrinsic?.spot_high) {
            oipElems.spotHigh.value = data.intrinsic.spot_high; oipElems.spotLow.value = data.intrinsic.spot_low;
        }
        if (oipIntrinsicChart) {
            if (view === 'index') {
                oipElems.itmCE.textContent = 'NIFTY'; oipElems.itmPE.textContent = 'Index';
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
                    }
                }
            }
            oipRefreshLocalView(view, resetZoom);
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

/* ── Premium Line SERIES (Entry / Current / Target 1 / Target 2) ──
 *  These are TIME-SERIES line plots (like VWAP), NOT static horizontal lines.
 *  Formula: All values = (CE_premium + PE_premium) / 2 per candle.
 */
const oipPremiumSeries = { entry: null, current: null, t1: null, t2: null };

/** Create the 4 premium addLineSeries on the intrinsic chart. Called once in oipInitCharts. */
function oipInitPremiumSeries() {
    const chart = oipIntrinsicChart?.chart;
    if (!chart) return;
    const base = { priceLineVisible: false, lastValueVisible: true,
                   crosshairMarkerVisible: false, visible: false };
    oipPremiumSeries.entry   = chart.addLineSeries({ ...base, color: '#4caf50', lineWidth: 2 });
    oipPremiumSeries.current = chart.addLineSeries({ ...base, color: '#2196f3', lineWidth: 2 });
    oipPremiumSeries.t1      = chart.addLineSeries({ ...base, color: '#e040fb', lineWidth: 1 });
    oipPremiumSeries.t2      = chart.addLineSeries({ ...base, color: '#f97316', lineWidth: 1 });
}

/**
 * Draws 4 premium line series using the combined (CE+PE)/2 formula.
 *
 *   Entry Premium   = (CE_VWAP + PE_VWAP) / 2 at each candle                 [dynamic VWAP curve]
 *   Current Premium = (CE_close + PE_close) / 2 at each candle               [live current average]
 *   Target Premium1 = EntryCurve + step                                        [mirrors entry curve]
 *   Target Premium2 = EntryCurve + 2 × step                                   [mirrors entry curve]
 */
function oipDrawPremiumLines(intrinsic, view = 'index') {
    const show = oipElems.showPremium?.checked && intrinsic && view !== 'index';

    // Hide all if disabled / no data / index view
    if (!show) {
        Object.values(oipPremiumSeries).forEach(s => { try { s?.applyOptions({ visible: false }); } catch(e){} });
        return;
    }

    // Lazy init if series not yet created
    if (!oipPremiumSeries.entry) oipInitPremiumSeries();
    if (!oipPremiumSeries.entry) return;          // chart not ready

    const allCE = oipOIData?.ce_opt_candles || [];
    const allPE = oipOIData?.pe_opt_candles || [];
    const tgtDist = parseInt(oipElems.targetDistance.value) || 50;

    if (!allCE.length && !allPE.length) return;

    // ── Build lookup maps: time → candle
    const ceMap = {}, peMap = {};
    allCE.forEach(c => { ceMap[c.time] = c; });
    allPE.forEach(c => { peMap[c.time] = c; });

    // ── Merged & sorted timestamps
    const allTimes = [...new Set([...Object.keys(ceMap), ...Object.keys(peMap)])]
        .map(Number).sort((a, b) => a - b);

    // ── Build time-series data arrays
    const entryData = [], currentData = [], t1Data = [], t2Data = [];

    let ce_cpv = 0, ce_cv = 0;
    let pe_cpv = 0, pe_cv = 0;
    let lastDay = null;

    allTimes.forEach(t => {
        const key = new Date(t * 1000).toDateString();
        // Reset VWAP sum on new day
        if (key !== lastDay) {
            ce_cpv = 0; ce_cv = 0;
            pe_cpv = 0; pe_cv = 0;
            lastDay = key;
        }

        const ceC = ceMap[t];
        const peC = peMap[t];

        if (ceC) {
            const v = ceC.volume > 0 ? ceC.volume : 1;
            ce_cpv += ceC.close * v;
            ce_cv += v;
        }
        if (peC) {
            const v = peC.volume > 0 ? peC.volume : 1;
            pe_cpv += peC.close * v;
            pe_cv += v;
        }

        let ce_vwap = ce_cv > 0 ? ce_cpv / ce_cv : null;
        let pe_vwap = pe_cv > 0 ? pe_cpv / pe_cv : null;

        let cur = null;
        let entry = null;

        if (view === 'combined') {
            // Current Premium = (CE_close + PE_close) / 2
            let c_c = ceC ? ceC.close : null;
            let p_c = peC ? peC.close : null;
            if (c_c != null && p_c != null) cur = (c_c + p_c) / 2;
            else if (c_c != null)            cur = c_c;
            else if (p_c != null)            cur = p_c;

            // Entry Premium Curve = (CE_VWAP + PE_VWAP) / 2
            if (ce_vwap != null && pe_vwap != null) entry = (ce_vwap + pe_vwap) / 2;
            else if (ce_vwap != null)                entry = ce_vwap;
            else if (pe_vwap != null)                entry = pe_vwap;
        } else if (view === 'ce') {
            cur = ceC ? ceC.close : null;
            entry = ce_vwap;
        } else if (view === 'pe') {
            cur = peC ? peC.close : null;
            entry = pe_vwap;
        }

        if (entry != null) {
            entryData.push  ({ time: t, value: entry });
            t1Data.push     ({ time: t, value: entry + tgtDist });
            t2Data.push     ({ time: t, value: entry + 2 * tgtDist });
        }
        if (cur != null) {
            currentData.push({ time: t, value: cur });
        }
    });

    // ── Push data and make visible
    try { oipPremiumSeries.entry.setData(entryData);     oipPremiumSeries.entry.applyOptions({ visible: true }); } catch(e){}
    try { oipPremiumSeries.current.setData(currentData); oipPremiumSeries.current.applyOptions({ visible: true }); } catch(e){}
    try { oipPremiumSeries.t1.setData(t1Data);           oipPremiumSeries.t1.applyOptions({ visible: true }); } catch(e){}
    try { oipPremiumSeries.t2.setData(t2Data);           oipPremiumSeries.t2.applyOptions({ visible: true }); } catch(e){}
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
        if (oipIntrinsicChart.setMarkers) oipIntrinsicChart.setMarkers([], []);
    } else if (oipOptionData) {
        const ceData = oipOptionData.filter(c => c.type === 'CE'), peData = oipOptionData.filter(c => c.type === 'PE');
        const ce_levels = oipOIData.intrinsic?.ce_levels || [];
        const pe_levels = oipOIData.intrinsic?.pe_levels || [];
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
        // Draw entry/exit signal markers
        oipDrawIntrinsicSignals(view, ceData, peData);
    }
    if (oipOIData.intrinsic) oipDrawIntrinsicLines(oipOIData.intrinsic, view);
    if (oipOIData.intrinsic) oipDrawPremiumLines(oipOIData.intrinsic, view);
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
    let daysData = [];
    for (let i = 1; i < days.length; i++) {
        const prev = days[i - 1], curr = days[i];
        let oH = prev.high, oL = prev.low, oC = prev.close;
        if (oipOIData?.daily_ohlc?.[prev.isoDate]) { const t = oipOIData.daily_ohlc[prev.isoDate]; oH = t.high; oL = t.low; oC = t.close; }
        const pp = (oH + oL + oC) / 3; let bc = (oH + oL) / 2, tc = (2 * pp) - bc; if (bc > tc) [bc, tc] = [tc, bc];
        const range = oH - oL, r1 = (pp * 2) - oL, s1 = (pp * 2) - oH, r0_5 = (pp + r1) / 2, s0_5 = (pp + s1) / 2, r0_25 = r0_5 + (oH - r0_5) / 4, s0_25 = s0_5 - (s0_5 - oL) / 4, cr3 = oC + (range * 1.1) / 4, cs3 = oC - (range * 1.1) / 4;
        
        let boxes = [
            { type: 'r1', min: Math.min(r1, oH), max: Math.max(r1, oH), times: curr.times },
            { type: 's1', min: Math.min(s1, oL), max: Math.max(s1, oL), times: curr.times },
            { type: 'r0_5', min: Math.min(r0_5, r0_25), max: Math.max(r0_5, r0_25), times: curr.times },
            { type: 's0_5', min: Math.min(s0_5, s0_25), max: Math.max(s0_5, s0_25), times: curr.times },
            { type: 'cpr', min: Math.min(tc, bc), max: Math.max(tc, bc), times: curr.times }
        ];
        
        daysData.push({
            times: curr.times,
            levels: { prevH: oH, prevL: oL, r1, s1, r0_5, s0_5, r0_25, s0_25, cr3, cs3, pp, tc, bc },
            boxes: boxes
        });
    }
    return daysData;
}

function oipDrawCpr(candles) {
    if (!oipOIChart || !oipOISeries) return;
    
    if (window.oipCprBoxSeries) {
        window.oipCprBoxSeries.forEach(s => { try { oipOIChart.removeSeries(s); } catch(e){} });
    }
    window.oipCprBoxSeries = [];

    if (window.oipCprLineSeries) {
        window.oipCprLineSeries.forEach(s => { try { oipOIChart.removeSeries(s); } catch(e){} });
    }
    window.oipCprLineSeries = [];

    const show = oipElems.showCpr.checked;
    if (!show || !candles || !candles.length) { 
        return; 
    }
    
    const daysData = oipCalculateDynamicCPR(candles);
    if (!daysData) return;
    
    const lineStyles = {
        prevH: { color: '#eab308', lineWidth: 1 },
        r1: { color: '#eab308', lineWidth: 1 },
        r0_25: { color: '#ef4444', lineWidth: 1 },
        r0_5: { color: '#ef4444', lineWidth: 1 },
        tc: { color: '#26bcd4', lineWidth: 1 },
        cr3: { color: '#a855f7', lineWidth: 2 },
        pp: { color: '#26bcd4', lineWidth: 1 },
        bc: { color: '#26bcd4', lineWidth: 1 },
        s0_5: { color: '#22c55e', lineWidth: 1 },
        s0_25: { color: '#22c55e', lineWidth: 1 },
        prevL: { color: '#eab308', lineWidth: 1 },
        cs3: { color: '#a855f7', lineWidth: 2 },
        s1: { color: '#eab308', lineWidth: 1 }
    };
    
    const boxColors = {
        'r1': 'rgba(234, 179, 8, 0.06)',
        's1': 'rgba(234, 179, 8, 0.06)',
        'r0_5': 'rgba(239, 68, 68, 0.06)',
        's0_5': 'rgba(34, 197, 94, 0.06)',
        'cpr': 'rgba(38, 188, 212, 0.03)'
    };
    
    daysData.forEach(day => {
        Object.keys(day.levels).forEach(key => {
            const style = lineStyles[key];
            if (!style) return;
            const series = oipOIChart.addLineSeries({
                ...style,
                lastValueVisible: false,
                priceLineVisible: false,
                crosshairMarkerVisible: false
            });
            const data = day.times.map(t => ({ time: t, value: day.levels[key] }));
            series.setData(data);
            window.oipCprLineSeries.push(series);
        });
        
        day.boxes.forEach(box => {
            if (box.min === box.max) return;
            const col = boxColors[box.type];
            const boxSeries = oipOIChart.addBaselineSeries({
                baseValue: { type: 'price', price: box.min },
                topFillColor1: col,
                topFillColor2: col,
                topLineColor: 'transparent',
                bottomFillColor1: col,
                bottomFillColor2: col,
                bottomLineColor: 'transparent',
                lineWidth: 1,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false
            });
            const data = box.times.map(t => ({ time: t, value: box.max }));
            boxSeries.setData(data);
            window.oipCprBoxSeries.push(boxSeries);
        });
    });
}

function oipAutoFillHighLow() {
    if (!oipOIData?.candles?.length) return;
    const lastCandle = oipOIData.candles[oipOIData.candles.length - 1];
    if (!lastCandle) return;
    const lastDate = new Date(lastCandle.time * 1000);
    lastDate.setHours(0, 0, 0, 0);
    const sodSeconds = lastDate.getTime() / 1000;
    
    const currentDayCandles = oipOIData.candles.filter(c => c.time >= sodSeconds);
    if (!currentDayCandles.length) return;
    
    const rh = Math.max(...currentDayCandles.map(c => c.high));
    const rl = Math.min(...currentDayCandles.map(c => c.low));
    
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

function oipCalculateDynamicEMA(candles, interval) {
    if (!candles || !candles.length) return [];
    let len = null;
    if (interval === 'minute') len = 60;
    else if (interval === '2minute') len = 187;
    else if (interval === '3minute') len = 125;
    else if (interval === '5minute') len = 75;
    else if (interval === '15minute') len = 125;
    else if (interval === '30minute') len = 125;
    else if (interval === '60minute') len = 137;
    else len = 88; // fallback
    if (len === null) return [];
    const k = 2 / (len + 1);
    let ema = null;
    return candles.map(c => {
        if (ema === null) ema = c.close;
        else ema = (c.close - ema) * k + ema;
        return { time: c.time, value: ema };
    });
}

async function oipPlaceOrder(side, action, btn) {
    const strike = (side === 'CE') ? oipCurrentCEStrike : oipCurrentPEStrike;
    if (!strike) { showNotification(`No ${side} strike available.`, 'error'); return; }
    const broker = oipElems.brokerSelect?.value || 'kotak_neo';
    
    // Add explicitly resolved pre-cached security keys to sidestep backend broker lookups
    const tSymbol = (side === 'CE') ? (oipOIData?.intrinsic?.ce_symbol || '') : (oipOIData?.intrinsic?.pe_symbol || '');
    const sId = (side === 'CE') ? (oipOIData?.intrinsic?.ce_sec_id || '') : (oipOIData?.intrinsic?.pe_sec_id || '');
    
    btn.disabled = true; const ot = btn.title; btn.title = "Placing...";
    try {
        const res = await fetch('/api/intraday-920/place-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content },
            body: JSON.stringify({ 
                symbol: oipSymbol, 
                strike: strike, 
                option_type: side, 
                action: action, 
                broker: broker, 
                strategy: 'intrinsic',
                tradingsymbol: tSymbol,
                sec_id: sId 
            })
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

let oipRSISeriesObj = null;

function oipDrawRSI(candles) {
    if (!oipOIChart || !oipOISeries) return;
    if (!oipRSISeriesObj) {
        let baseObj = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
        oipRSISeriesObj = {
            ob: oipOIChart.addLineSeries({ ...baseObj, color: '#14b8a6', lineWidth: 2, lineStyle: 0 }),
            bull: oipOIChart.addLineSeries({ ...baseObj, color: '#14b8a6', lineWidth: 1, lineStyle: 0 }),
            os: oipOIChart.addLineSeries({ ...baseObj, color: '#ef4444', lineWidth: 2, lineStyle: 0 }),
            bear: oipOIChart.addLineSeries({ ...baseObj, color: '#ef4444', lineWidth: 1, lineStyle: 0 })
        };
    }
    const show = oipElems.showRSI?.checked;
    if (!show || !candles || !candles.length) {
        Object.values(oipRSISeriesObj).forEach(s => s.applyOptions({ visible: false }));
        oipOISeries.setMarkers([]);
        return;
    }
    Object.values(oipRSISeriesObj).forEach(s => s.applyOptions({ visible: true }));
    const rsiData = oipCalculateRSISnR(candles);
    if (rsiData) {
        oipRSISeriesObj.ob.setData(rsiData.ob_series);
        oipRSISeriesObj.bull.setData(rsiData.bull_series);
        oipRSISeriesObj.os.setData(rsiData.os_series);
        oipRSISeriesObj.bear.setData(rsiData.bear_series);
        if(rsiData.markers) {
            rsiData.markers.sort((a,b) => a.time - b.time);
            Object.values(rsiData.markers).forEach(m => m.size = 1);
            oipOISeries.setMarkers(rsiData.markers);
        }
    }
}

function oipCalculateRSISnR(candles) {
    if (!candles || candles.length < 15) return null;
    const len = 14;
    const rsi = new Array(candles.length).fill(null);
    let gains = 0, losses = 0;
    
    for (let i = 1; i <= len; i++) {
        const diff = candles[i].close - candles[i-1].close;
        if (diff >= 0) gains += diff;
        else losses -= diff;
    }
    let avgGain = gains / len;
    let avgLoss = losses / len;
    rsi[len] = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));
    
    for (let i = len + 1; i < candles.length; i++) {
        const diff = candles[i].close - candles[i-1].close;
        const gain = diff >= 0 ? diff : 0;
        const loss = diff < 0 ? -diff : 0;
        avgGain = (avgGain * (len - 1) + gain) / len;
        avgLoss = (avgLoss * (len - 1) + loss) / len;
        rsi[i] = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));
    }
    
    let level_ob = null, level_os = null, level_bull = null, level_bear = null;
    const ob_series = [], os_series = [], bull_series = [], bear_series = [];
    const markers = [];
    
    const thresh_ob = 70, thresh_bull = 60, thresh_bear = 40, thresh_os = 30;
    let in_short = false, in_long = false;
    let short_sl = null, long_sl = null;
    
    const L_OB=new Array(candles.length).fill(null);
    const L_OS=new Array(candles.length).fill(null);
    
    for (let i = 1; i < candles.length; i++) {
        const c = candles[i], r = rsi[i], r_prev = rsi[i-1];
        if (r === null || r_prev === null) continue;
        
        const avgHigh = (c.high + c.close) / 2;
        const avgLow = (c.low + c.close) / 2;
        
        if (r_prev <= thresh_ob && r > thresh_ob) level_ob = avgHigh;
        if (r_prev >= thresh_os && r < thresh_os) level_os = avgLow;
        if ((r_prev <= thresh_bull && r > thresh_bull) || (r_prev >= thresh_bull && r < thresh_bull)) level_bull = avgHigh;
        if ((r_prev <= thresh_bear && r > thresh_bear) || (r_prev >= thresh_bear && r < thresh_bear)) level_bear = avgLow;
        
        L_OB[i] = level_ob;
        L_OS[i] = level_os;
        
        if (level_ob !== null) ob_series.push({ time: c.time, value: level_ob });
        if (level_os !== null) os_series.push({ time: c.time, value: level_os });
        if (level_bull !== null) bull_series.push({ time: c.time, value: level_bull });
        if (level_bear !== null) bear_series.push({ time: c.time, value: level_bear });
        
        if (i >= 2) {
            const pC = candles[i-1], ppC = candles[i-2];
            const pL_OB = L_OB[i-1], ppL_OB = L_OB[i-2], pL_OS = L_OS[i-1], ppL_OS = L_OS[i-2];
            
            if (pL_OB !== null) {
                const setup_ob_rej = (pC.close < pL_OB) && (pC.high >= pL_OB || ppC.close >= ppL_OB);
                const short_entry = setup_ob_rej && (c.close < pC.low);
                if (short_entry) {
                    in_short = true; in_long = false; short_sl = pC.high;
                    markers.push({ time: c.time, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'ENTRY' });
                }
            }
            if (pL_OS !== null) {
                const setup_os_rej = (pC.close > pL_OS) && (pC.low <= pL_OS || ppC.close <= ppL_OS);
                const long_entry = setup_os_rej && (c.close > pC.high);
                if (long_entry) {
                    in_long = true; in_short = false; long_sl = pC.low;
                    markers.push({ time: c.time, position: 'belowBar', color: '#14b8a6', shape: 'arrowUp', text: 'ENTRY' });
                }
            }
            
            if (in_short) {
                const tgtHit = level_bear !== null && c.low <= level_bear;
                const slHit = short_sl !== null && c.high >= short_sl;
                if (tgtHit || slHit) {
                    in_short = false;
                    markers.push({ time: c.time, position: 'aboveBar', color: tgtHit ? '#f97316' : '#800000', shape: tgtHit ? 'circle' : 'circle', text: tgtHit ? '🎯' : 'SL' });
                }
            }
            if (in_long) {
                const tgtHit = level_bull !== null && c.high >= level_bull;
                const slHit = long_sl !== null && c.low <= long_sl;
                if (tgtHit || slHit) {
                    in_long = false;
                    markers.push({ time: c.time, position: 'belowBar', color: tgtHit ? '#f97316' : '#800000', shape: tgtHit ? 'circle' : 'circle', text: tgtHit ? '🎯' : 'SL' });
                }
            }
        }
    }
    return { ob_series, os_series, bull_series, bear_series, markers };
}

/* ── Intrinsic Levels Entry / Exit Signals ──────────────────────
 *  Strategy:
 *  ENTRY  → Price bounces off VWAP from below (candle close > VWAP after prev close ≤ VWAP)
 *           + proximity to intrinsic level (within 1.5× step) gives bonus confidence
 *  EXIT   → T1 = entry + step, T2 = entry + 2×step, SL = entry - SL_DIST
 *  Each trade tracks its own SL/T1/T2. Only 1 trade active per side at a time.
 */
function oipDrawIntrinsicSignals(view, ceData, peData) {
    if (!oipIntrinsicChart || !oipIntrinsicChart.setMarkers) return;

    const showSignals = oipElems.showSignals?.checked;
    if (!showSignals || !oipOIData?.intrinsic) {
        oipIntrinsicChart.setMarkers([], []);
        return;
    }

    const intrinsic = oipOIData.intrinsic;
    const step = parseInt(oipElems.step?.value) || 50;
    const SL_DIST = 30;  // fixed SL distance

    function computeSignals(candles, intrinsicBase) {
        if (!candles || candles.length < 3) return [];

        // Compute VWAP inline
        const vwap = [];
        let cpv = 0, cv = 0, ld = null;
        for (const c of candles) {
            const d = new Date(c.time * 1000).toDateString();
            if (d !== ld) { cpv = 0; cv = 0; ld = d; }
            const v = c.volume > 0 ? c.volume : 1;
            const tp = (c.high + c.low + c.close) / 3;
            cpv += tp * v; cv += v;
            vwap.push(cpv / cv);
        }

        // Build level array: intrinsicBase, intrinsicBase+step, +2*step, etc.
        const levels = [intrinsicBase];
        for (let i = 1; i <= 10; i++) levels.push(intrinsicBase + step * i);

        function nearLevel(price) {
            for (const lvl of levels) {
                if (Math.abs(price - lvl) <= step * 1.5) return true;
            }
            return false;
        }

        const markers = [];
        let inTrade = false;
        let entryPrice = 0;
        let sl = 0;
        let t1 = 0;
        let t2 = 0;
        let t1Hit = false;

        for (let i = 1; i < candles.length; i++) {
            const c = candles[i];
            const prev = candles[i - 1];
            const vCur = vwap[i];
            const vPrev = vwap[i - 1];

            // Filter: only current day candles
            const cDay = new Date(c.time * 1000).toDateString();
            const pDay = new Date(prev.time * 1000).toDateString();

            // Reset trade on new day
            if (cDay !== pDay) {
                inTrade = false;
            }

            if (!inTrade) {
                // ENTRY CONDITIONS:
                // A) VWAP crossover bounce — prev close ≤ VWAP, current close > VWAP
                const prevBelowVwap = prev.close <= vPrev;
                const curAboveVwap = c.close > vCur;
                const greenCandle = c.close > c.open;
                const vwapBounce = prevBelowVwap && curAboveVwap && greenCandle;

                // B) Entry Premium touch — candle dips to/below VWAP (entry premium)
                //    but closes above it with a green body (hammer/reversal at VWAP)
                const lowTouchedVwap = c.low <= vCur;
                const closeAboveVwap = c.close > vCur;
                const premiumTouch = lowTouchedVwap && closeAboveVwap && greenCandle && !prevBelowVwap;

                if (vwapBounce || premiumTouch) {
                    const isNearLevel = nearLevel(c.close);
                    const label = vwapBounce
                        ? (isNearLevel ? 'BUY ★' : 'BUY')
                        : (isNearLevel ? 'EP ★' : 'EP');

                    inTrade = true;
                    entryPrice = c.close;
                    sl = entryPrice - SL_DIST;
                    t1 = entryPrice + step;
                    t2 = entryPrice + 2 * step;
                    t1Hit = false;

                    markers.push({
                        time: c.time,
                        position: 'belowBar',
                        color: isNearLevel ? '#10b981' : '#22c55e',
                        shape: 'arrowUp',
                        text: label,
                        size: isNearLevel ? 2 : 1
                    });
                }
            } else {
                // EXIT checks
                // Check SL
                if (c.low <= sl) {
                    inTrade = false;
                    markers.push({
                        time: c.time,
                        position: 'aboveBar',
                        color: '#ef4444',
                        shape: 'circle',
                        text: 'SL',
                        size: 1
                    });
                    continue;
                }
                // Check T1
                if (!t1Hit && c.high >= t1) {
                    t1Hit = true;
                    // Trail SL to entry (break-even)
                    sl = entryPrice;
                    markers.push({
                        time: c.time,
                        position: 'aboveBar',
                        color: '#f97316',
                        shape: 'circle',
                        text: 'T1',
                        size: 1
                    });
                }
                // Check T2
                if (t1Hit && c.high >= t2) {
                    inTrade = false;
                    markers.push({
                        time: c.time,
                        position: 'aboveBar',
                        color: '#8b5cf6',
                        shape: 'circle',
                        text: 'T2 🎯',
                        size: 2
                    });
                    continue;
                }
                // VWAP breakdown exit (after T1 hit, if price closes below VWAP)
                if (t1Hit && c.close < vCur) {
                    inTrade = false;
                    markers.push({
                        time: c.time,
                        position: 'aboveBar',
                        color: '#6b7280',
                        shape: 'circle',
                        text: 'EXIT',
                        size: 1
                    });
                }
            }
        }
        return markers;
    }

    const ceIntrinsic = intrinsic.ce_intrinsic || 0;
    const peIntrinsic = intrinsic.pe_intrinsic || 0;

    if (view === 'combined') {
        const ceMarkers = computeSignals(ceData, ceIntrinsic);
        const peMarkers = computeSignals(peData, peIntrinsic);
        oipIntrinsicChart.setMarkers(ceMarkers, peMarkers);
    } else if (view === 'ce') {
        const ceMarkers = computeSignals(ceData, ceIntrinsic);
        oipIntrinsicChart.setMarkers(ceMarkers, []);
    } else if (view === 'pe') {
        const peMarkers = computeSignals(peData, peIntrinsic);
        oipIntrinsicChart.setMarkers([], peMarkers);
    } else {
        oipIntrinsicChart.setMarkers([], []);
    }
}
