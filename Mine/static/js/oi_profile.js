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
let oipCprSeriesObj = null;
let oipEma9Series = null, oipEma20Series = null, oipEma50Series = null, oipEma100Series = null, oipEma200Series = null;

let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

let oipAllStrikes = [];
let oipCurrentPrice = 0;
let oipSymbol = 'NIFTY';
let oipInterval = 'minute';
let oipStrikeCount = 15;
let oipMode = 'change';
let oipIsBusy = false;
let oipRafId = null;
let oipIsFirstLoad = true;
let oipCustomStrikeSetOnLoad = false;
let oipOIChartReady = false;   // true after OI chart receives first data
let oipIntChartReady = false;  // true after Intrinsic chart receives first data
let oipFutureWhitespace = []; // Stores whitespace bars to extend timeline for all charts
let oipAllSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];

// DOM Cache for optimized performance
const oipElems = {
    symbolInput: null, symbolList: null, interval: null, 
    spotHigh: null, spotLow: null, step: null, multiplier: null,
    view: null, showLevels: null, showVwapOI: null, showVwapInt: null,
    showCpr: null, showFutureCpr: null, showEMA: null, showRSI: null, autoHL: null, chartWrap: null, canvas: null,
    tooltip: null, refreshIcon: null, itmCE: null, itmPE: null,
    hdrPrice: null, hdrPcr: null, hdrMaxPain: null, hdrRes: null,
    hdrSupp: null, hdrCeOI: null, hdrCeChg: null, hdrPeOI: null,
    hdrPeChg: null, hdrTrend: null, hdrAtm: null, brokerSelect: null,
    showPremium: null, showSignals: null, first5mATM: null, targetDistance: null, customStrikeCheck: null, customStrikeDropdown: null,
    showEma9: null, showEma20: null, showEma50: null, showEma100: null, showEma200: null,
    exitAll: null
};

function oipUpdateEmaVisibility() {
    if (oipEma9Series) oipEma9Series.applyOptions({ visible: oipElems.showEma9?.checked ?? false });
    if (oipEma20Series) oipEma20Series.applyOptions({ visible: oipElems.showEma20?.checked ?? false });
    if (oipEma50Series) oipEma50Series.applyOptions({ visible: oipElems.showEma50?.checked ?? false });
    if (oipEma100Series) oipEma100Series.applyOptions({ visible: oipElems.showEma100?.checked ?? false });
    if (oipEma200Series) oipEma200Series.applyOptions({ visible: oipElems.showEma200?.checked ?? false });
}

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
    oipElems.itmCE = document.getElementById('oipLegendCE');
    oipElems.itmPE = document.getElementById('oipLegendPE');
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
    oipElems.showEma9 = document.getElementById('oipShowEma9');
    oipElems.showEma20 = document.getElementById('oipShowEma20');
    oipElems.showEma50 = document.getElementById('oipShowEma50');
    oipElems.showEma100 = document.getElementById('oipShowEma100');
    oipElems.showEma200 = document.getElementById('oipShowEma200');
    oipElems.showFutureCpr = document.getElementById('oipShowFutureCpr');
    oipElems.exitAll = document.getElementById('oipExitAll');
    
    if (oipElems.customStrikeDropdown) {
        let opts = '';
        for (let s = 20000; s <= 28000; s += 50) {
            opts += `<option value="${s}">${s}</option>`;
        }
        if (oipElems.customStrikeDropdown) oipElems.customStrikeDropdown.innerHTML = opts;
    }
}

/* ── Bootstrap ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    oipInitElems();
    oipInitCharts();

    // Dropdown Logic
    oipElems.symbolInput?.addEventListener('input', (e) => oipRenderDropdown(e.target.value.toUpperCase(), oipElems.symbolList));
    oipElems.symbolInput?.addEventListener('click', function (e) {
        e.stopPropagation();
        if (oipElems.symbolList?.classList.contains('show')) {
            oipElems.symbolList?.classList.remove('show');
            oipElems.symbolList?.classList.add('hidden');
        } else {
            this.value = '';
            oipRenderDropdown('', oipElems.symbolList);
        }
    });
    oipElems.symbolInput?.addEventListener('blur', () => {
        setTimeout(() => {
            oipElems.symbolList?.classList.remove('show');
            if (!oipElems.symbolInput?.value.trim()) if (oipElems.symbolInput) oipElems.symbolInput.value = oipSymbol;
        }, 200);
    });
    oipElems.symbolInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const val = e.target.value.trim().toUpperCase();
            if (val) oipSelectSymbol(val);
            oipElems.symbolInput?.blur();
        }
    });
    document.addEventListener('click', (e) => {
        if (oipElems.symbolInput && oipElems.symbolList && !oipElems.symbolInput?.contains(e.target) && !oipElems.symbolList?.contains(e.target)) {
            oipElems.symbolList?.classList.remove('show');
        }
    });

    fetch('/api/symbols').then(r => r.json()).then(d => { if (d.success) oipAllSymbols = d.symbols; }).catch(console.warn);

    // Toolbar Listeners
    oipElems.interval?.addEventListener('change', e => {
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
            const view = oipElems.view?.value;
            const rescaled = (el === oipElems.view);
            const needsOptionData = (view !== 'index') && !oipOptionData;
            if (oipOIData && !needsOptionData) oipLoadCandles(false, rescaled);
            else oipLoadCandles(true, rescaled);
        });
    });

    oipElems.showVwapOI?.addEventListener('change', e => {
        if (oipVwapSeries) oipVwapSeries.applyOptions({ visible: e.target.checked });
    });

    oipElems.showVwapInt?.addEventListener('change', e => {
        const show = e.target.checked;
        if (oipVwapIntSeries) oipVwapIntSeries.applyOptions({ visible: show });
        if (oipVwapIntPeSeries) oipVwapIntPeSeries.applyOptions({ visible: show });
    });

    oipElems.showCpr?.addEventListener('change', () => {
        if (oipOIData && oipOIData.candles) oipDrawCpr(oipOIData.candles);
    });

    oipElems.showFutureCpr?.addEventListener('change', () => {
        if (oipOIData && oipOIData.candles) oipDrawCpr(oipOIData.candles);
    });

    [oipElems.showEma9, oipElems.showEma20, oipElems.showEma50, oipElems.showEma100, oipElems.showEma200].forEach(el => {
        el?.addEventListener('change', () => oipUpdateEmaVisibility());
    });

    oipElems.showEMA?.addEventListener('change', e => {
        oipUpdateEmaVisibility();
    });

    oipElems.showRSI?.addEventListener('change', () => {
        if (oipOIData && oipOIData.candles) oipDrawRSI(oipOIData.candles);
    });

    oipElems.showPremium?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view?.value);
    });

    oipElems.showSignals?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view?.value);
    });

    oipElems.first5mATM?.addEventListener('change', (e) => {
        if (e.target.checked && oipElems.customStrikeCheck) {
            if (oipElems.customStrikeCheck) oipElems.customStrikeCheck.checked = false;
        }
        oipLoadCandles(true, false);
    });

    oipElems.customStrikeCheck?.addEventListener('change', (e) => {
        if (e.target.checked && oipElems.first5mATM) {
            if (oipElems.first5mATM) oipElems.first5mATM.checked = false;
            // Always snap to ATM logic disabled here? The user said "While enable the check it should be default ATM Strike" 
            if (oipElems.customStrikeDropdown) {
                const step = parseInt(oipElems.step?.value) || 50;
                let refPrice = oipCurrentPrice;
                if (!refPrice && oipElems.spotHigh?.value && oipElems.spotLow?.value) {
                    refPrice = (parseFloat(oipElems.spotHigh?.value) + parseFloat(oipElems.spotLow?.value)) / 2;
                }
                if (refPrice > 0) {
                    if (oipElems.customStrikeDropdown) oipElems.customStrikeDropdown.value = Math.round(refPrice / step) * step;
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
        oipRefreshLocalView(oipElems.view?.value);
    });

    // Order buttons
    document.querySelectorAll('.oip-order-btn').forEach(btn => {
        if (btn.id === 'oipExitAll') {
            btn.addEventListener('click', () => oipExitAllOrders(btn));
        } else {
            btn.addEventListener('click', () => oipPlaceOrder(btn.dataset.side, btn.dataset.action, btn));
        }
    });

    // Tooltip Logic
    // Tooltip Logic - Commented out as requested
    /*
    oipOIChart.subscribeCrosshairMove(param => {
        if (!param.point || !oipAllStrikes.length) {
            oipElems.tooltip?.classList.add('hidden');
            return;
        }
        const W = oipElems.canvas?.width / window.devicePixelRatio;
        const plotRight = W - 70;
        const MAX_BAR_PX = Math.min(plotRight * 0.35, 300);
        if (param.point.x < (plotRight - MAX_BAR_PX - 20) || param.point.x > plotRight + 10) {
            oipElems.tooltip?.classList.add('hidden');
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
        if (minDist > 100) { oipElems.tooltip?.classList.add('hidden'); return; }
        const isChg = (oipMode === 'change');
        if (oipElems.tooltip) oipElems.tooltip.innerHTML = `
            <div class="strike">Strike: ${nearest.strike}</div>
            <div class="row"><div class="dot ce"></div><div class="lbl">Call:</div><div class="val">${fmtL(isChg ? nearest.ce_change_in_oi : nearest.ce_oi)}</div></div>
            <div class="row"><div class="dot pe"></div><div class="lbl">Put:</div><div class="val">${fmtL(isChg ? nearest.pe_change_in_oi : nearest.pe_oi)}</div></div>
        `;
        oipElems.tooltip?.classList.remove('hidden');
        if (oipElems.tooltip) oipElems.tooltipstyle.left = (param.point.x - oipElems.tooltip?.offsetWidth - 15) + 'px';
        if (oipElems.tooltip) oipElems.tooltipstyle.top = (param.point.y - oipElems.tooltip?.offsetHeight / 2) + 'px';
    });
    */

    oipFullRefresh(true);
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
            visible: oipElems.showVwapOI?.checked ?? false,
            priceLineVisible: false, lastValueVisible: false,
            autoscaleInfoProvider: () => null
        });

        // Fixed EMA series matching Mine CPR Pine script
        oipEma9Series = oipOIChart.addLineSeries({ color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });
        oipEma20Series = oipOIChart.addLineSeries({ color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });
        oipEma50Series = oipOIChart.addLineSeries({ color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });
        oipEma100Series = oipOIChart.addLineSeries({ color: '#3b82f6', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });
        oipEma200Series = oipOIChart.addLineSeries({ color: '#000000', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });

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
            isCombined: true, timeframe: oipInterval, options: { height: 360 }
        });
        oipIntrinsicSeries = oipIntrinsicChart.ceSeries || oipIntrinsicChart.series;
        oipIntrinsicPeSeries = oipIntrinsicChart.peSeries;
        const showV = oipElems.showVwapInt?.checked;
        oipVwapIntSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#ef4444', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipVwapIntPeSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#000000', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipInitPremiumSeries();
        
        // Link charts (synchronize panning and zooming)
        if (oipOIChart && oipIntrinsicChart && oipIntrinsicChart.chart) {
            let isSyncingRange = false;
            
            oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
                if (!range || range.from == null || range.to == null || isSyncingRange) return;
                if (!oipOIChartReady || !oipIntChartReady) return;  // Wait until both charts have data
                isSyncingRange = true;
                try { oipIntrinsicChart.chart.timeScale().setVisibleLogicalRange(range); } catch(e) {}
                isSyncingRange = false;
            });
            
            oipIntrinsicChart.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
                if (!range || range.from == null || range.to == null || isSyncingRange) return;
                if (!oipOIChartReady || !oipIntChartReady) return;  // Wait until both charts have data
                isSyncingRange = true;
                try { oipOIChart.timeScale().setVisibleLogicalRange(range); } catch(e) {}
                isSyncingRange = false;
            });
            
            // Sync Crosshair
            let isSyncingCrosshair = false;
            
            function syncCrosshair(sourceChart, targetChart, param, targetSeries) {
                if (isSyncingCrosshair) return;
                isSyncingCrosshair = true;
                try {
                    const isValid = param.point !== undefined && param.time !== undefined && 
                                  param.point.x >= 0 && param.point.y >= 0;
                    if (!isValid) {
                        targetChart.clearCrosshairPosition();
                    } else {
                        // Map the Y pixel coordinate to the target chart's price scale
                        const price = targetSeries.coordinateToPrice(param.point.y);
                        if (price !== null) {
                            targetChart.setCrosshairPosition(price, param.time, targetSeries);
                        } else {
                            targetChart.clearCrosshairPosition();
                        }
                    }
                } catch(e) {}
                isSyncingCrosshair = false;
            }

            oipOIChart.subscribeCrosshairMove(param => {
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) {
                    syncCrosshair(oipOIChart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                }
            });
            
            oipIntrinsicChart.chart.subscribeCrosshairMove(param => {
                if (oipOIChart && oipOISeries) {
                    syncCrosshair(oipIntrinsicChart.chart, oipOIChart, param, oipOISeries);
                }
            });
        }
    }
}

function creatBaseChart(el) {
    return LightweightCharts.createChart(el, {
        width: el.clientWidth || 1200, height: 360,
        layout: { textColor: '#374151', background: { type: 'solid', color: '#ffffff' } },
        grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
        crosshair: { mode: 0, vertLine: { color: '#9ca3af', style: 3 }, horzLine: { color: '#9ca3af', style: 3, labelBackgroundColor: '#0969da' } },
        timeScale: { 
            timeVisible: true, 
            textColor: '#6b7280', 
            borderColor: 'transparent', 
            rightOffset: 500, 
            barSpacing: 20,
            fixRightEdge: false
        },
        rightPriceScale: { textColor: '#6b7280', borderColor: 'transparent', width: 70, autoScale: true, scaleMargins: { top: 0.02, bottom: 0.02 } },
        handleScroll: true, handleScale: true, 
        localization: { 
            locale: 'en-IN',
            timezone: 'Etc/UTC' // Use UTC to prevent double-shifting of already IST-shifted timestamps
        }
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
        if (y0 !== null && y1 !== null) barH = Math.max(2, Math.min(25, Math.abs(y1 - y0) * 0.45));
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

function oipCalculateFixedEMA(data, period) {
    if (!data || data.length === 0) return [];
    const ema = [];
    const k = 2 / (period + 1);
    let prevEma = data[0].close;
    
    data.forEach(d => {
        if (d.close == null || isNaN(d.close)) return;
        const val = (d.close * k) + (prevEma * (1 - k));
        if (!isNaN(val)) {
            ema.push({ time: d.time, value: val });
            prevEma = val;
        }
    });
    return ema;
}

function oipCalculateVWAP(candles) {
    if (!candles || candles.length === 0) return [];
    let cumPV = 0, cumV = 0, lastDate = null;
    const result = [];
    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        // Use UTC methods to match the 'Fake IST Epoch' from server
        const date = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
        if (date !== lastDate) { cumPV = 0; cumV = 0; lastDate = date; }
        const vol = c.volume || 0;
        if (vol <= 0) return;
        cumPV += ((c.high + c.low + c.close) / 3) * vol;
        cumV += vol;
        const vwapVal = cumPV / cumV;
        if (!isNaN(vwapVal)) {
            result.push({ time: c.time, value: vwapVal });
        }
    });
    return result;
}

/* ── Refresh Logic ────────────────────────────────────────── */
let oipCandleTimer = null;
let oipOITimer = null;
let oipIsBusyCandles = false;
let oipIsBusyOI = false;

async function oipFullRefresh(resetZoom = false) {
    if (oipCandleTimer) clearTimeout(oipCandleTimer);
    if (oipOITimer) clearTimeout(oipOITimer);
    if (resetZoom) {
        oipIsFirstLoad = true;
        oipCustomStrikeSetOnLoad = false; // Reset to allow ATM snap on first data
    }
    oipCandleLoop(resetZoom);
    setTimeout(() => oipOILoop(), 500); 
}

async function oipCandleLoop(resetZoom = false) {
    if (oipIsBusyCandles) return;
    oipIsBusyCandles = true;
    setRefreshBtn(true);
    let success = false;
    try {
        await oipLoadCandles(true, resetZoom);
        success = true;
    } catch (err) { console.error('[OIP] Candle Loop Err:', err); }
    finally {
        oipIsBusyCandles = false;
        if (!oipIsBusyOI) setRefreshBtn(false);
        const delay = oipIsMarketOpen() ? (success ? 1000 : 2000) : 60000;
        if (oipCandleTimer) clearTimeout(oipCandleTimer);
        oipCandleTimer = setTimeout(() => {
            if (!document.hidden) oipCandleLoop(false);
            else oipCandleTimer = setTimeout(() => oipCandleLoop(false), 10000);
        }, delay);
    }
}

async function oipOILoop() {
    if (oipIsBusyOI) return;
    oipIsBusyOI = true;
    let success = false;
    try {
        await oipLoadOI();
        success = true;
    } catch (err) { console.error('[OIP] OI Loop Err:', err); }
    finally {
        oipIsBusyOI = false;
        const delay = oipIsMarketOpen() ? (success ? 30000 : 2000) : 60000;
        if (oipOITimer) clearTimeout(oipOITimer);
        oipOITimer = setTimeout(() => {
            if (!document.hidden) oipOILoop();
            else oipOITimer = setTimeout(() => oipOILoop(), 10000);
        }, delay);
    }
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
        
        // Auto-initialize custom strike to ATM if Custom is checked on first load
        if (!oipCustomStrikeSetOnLoad && oipElems.customStrikeCheck?.checked && oipCurrentPrice > 0) {
            const step = parseInt(oipElems.step?.value || 50);
            const atmStrike = Math.round(oipCurrentPrice / step) * step;
            if (oipElems.customStrikeDropdown) {
                oipElems.customStrikeDropdown.value = atmStrike;
                oipCustomStrikeSetOnLoad = true; // Mark as initialized
                // Re-fetch candles now that we have a valid custom strike
                oipLoadCandles(true);
            }
        }

        oipUpdateHeader(data);
        oipRequestDraw();
    } catch (e) { console.warn('[OIP] OI Load Err:', e); }
}

async function oipLoadCandles(forceFetch = true, resetZoom = false) {
    try {
        const h = parseFloat(oipElems.spotHigh?.value || 0);
        const l = parseFloat(oipElems.spotLow?.value || 0);
        const s = parseInt(oipElems.step?.value || 50);
        const m = parseInt(oipElems.multiplier?.value || 3);
        const view = oipElems.view?.value || 'combined';
        
        const needsOptionData = (view !== 'index') && !oipOptionData;
        const autoHL = true; // Favored default for the current template
        const first5m = oipElems.first5mATM?.checked || false;
        const customStrike = (oipElems.customStrikeCheck?.checked && oipElems.customStrikeDropdown?.value) ? oipElems.customStrikeDropdown?.value : '';
        
        if (!forceFetch && oipOIData && !needsOptionData) { oipRefreshLocalView(view, resetZoom); return; }
        
        const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=5&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=${autoHL}&first_5m_atm=${first5m}&custom_strike=${customStrike}&_t=${Date.now()}`;
        
        const res = await fetch(url);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);
        
        oipOIData = Object.assign(oipOIData || {}, data);
        const indexCandles = data.candles || [];
        
        if (oipOISeries && indexCandles.length) {
            // Hard filter to prevent "Value is null" crash in Candlestick series
            // Explicitly cast to Number, filter NaNs, sort, and deduplicate to guarantee valid schema
            const uniqueTimes = new Set();
            const validCandles = indexCandles.map(c => ({
                time: Number(c.time),
                open: Number(c.open),
                high: Number(c.high),
                low: Number(c.low),
                close: Number(c.close),
                volume: Number(c.volume || 0)
            })).filter(c => 
                !isNaN(c.time) && !isNaN(c.open) && !isNaN(c.high) && !isNaN(c.low) && !isNaN(c.close)
            ).sort((a, b) => a.time - b.time).filter(c => {
                if (uniqueTimes.has(c.time)) return false;
                uniqueTimes.add(c.time);
                return true;
            });
            
            if (validCandles.length) {
                try {
                    // Generate whitespace candles to force x-axis labels for future dates
                    const lastTime = validCandles[validCandles.length - 1].time;
                    const intervalMins = oipInterval === 'day' ? 1440 : (parseInt(oipInterval.replace('minute', '')) || 1);
                    const intervalSecs = intervalMins * 60;
                    const whitespace = [];
                    let currT = lastTime;
                    let added = 0;
                    
                    // Generate exactly 1000 bars of market-hour whitespace
                    while (added < 1000) {
                        currT += intervalSecs;
                        const d = new Date(currT * 1000);
                        const day = d.getUTCDay();
                        const hour = d.getUTCHours(), min = d.getUTCMinutes();
                        const timeVal = hour * 100 + min;
                        
                        if (oipInterval !== 'day') {
                            if (timeVal > 1530 || timeVal < 915) continue; // Skip overnight
                            if (day === 0 || day === 6) continue; // Skip weekends
                        }
                        whitespace.push({ time: currT });
                        added++;
                    }
                    
                    oipFutureWhitespace = whitespace; // Store globally for other charts
                    
                    oipOISeries.setData([...validCandles, ...oipFutureWhitespace]);
                    // Also add whitespace to the intrinsic/option chart if it exists
                    if (oipIntrinsicSeries) {
                        const existingData = oipIntrinsicSeries.data ? [...oipIntrinsicSeries.data()] : [];
                        if (existingData.length) {
                             oipIntrinsicSeries.setData([...existingData, ...oipFutureWhitespace]);
                        }
                    }
                    oipOIChartReady = true;  
                    if (oipVwapSeries) oipVwapSeries.setData(oipCalculateVWAP(validCandles));
                } catch (e) { console.warn('[OIP] SetData Err:', e); }
            }
            
            // Fixed EMAs
            if (oipEma9Series) oipEma9Series.setData(oipCalculateFixedEMA(validCandles, 9));
            if (oipEma20Series) oipEma20Series.setData(oipCalculateFixedEMA(validCandles, 20));
            if (oipEma50Series) oipEma50Series.setData(oipCalculateFixedEMA(validCandles, 50));
            if (oipEma100Series) oipEma100Series.setData(oipCalculateFixedEMA(validCandles, 100));
            if (oipEma200Series) oipEma200Series.setData(oipCalculateFixedEMA(validCandles, 200));
            
            oipUpdateEmaVisibility();
            oipDrawCpr(validCandles);
            oipDrawRSI(validCandles);
        }
        
        if (data.intrinsic?.spot_high && oipElems.spotHigh) {
            if (oipElems.spotHigh) oipElems.spotHigh.value = data.intrinsic.spot_high;
            if (oipElems.spotLow) oipElems.spotLow.value = data.intrinsic.spot_low;
        }
        
        if (oipIntrinsicChart) {
            if (view === 'index') {
                if (oipElems.itmCE) if (oipElems.itmCE) oipElems.itmCE.textContent = 'NIFTY';
                if (oipElems.itmPE) if (oipElems.itmPE) oipElems.itmPE.textContent = 'Index';
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
                        if (oipElems.itmCE) if (oipElems.itmCE) oipElems.itmCE.textContent = `${ceStrike} CE`;
                        if (oipElems.itmPE) if (oipElems.itmPE) oipElems.itmPE.textContent = `${peStrike} PE`;
                    }
                }
            }
            oipRefreshLocalView(view, resetZoom);
            oipRequestDraw();
        }
    } catch (e) { console.error('[OIP] Refresh Err:', e); }
}

function oipUpdateHeader(data) {
    const p = data.current_price || 0, pcr = data.pcr_oi || 0, mp = data.max_pain || '--';
    const ce = data.ce_summary || {}, pe = data.pe_summary || {}, strikes = data.strikes || [];
    const ceSorted = [...strikes].sort((a, b) => (b.ce_oi || 0) - (a.ce_oi || 0));
    const peSorted = [...strikes].sort((a, b) => (b.pe_oi || 0) - (a.pe_oi || 0));
    if (oipElems.hdrPrice) oipElems.hdrPrice.textContent = p.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    if (oipElems.hdrPcr) oipElems.hdrPcr.textContent = pcr.toFixed(2); if (oipElems.hdrMaxPain) oipElems.hdrMaxPain.textContent = mp;
    if (oipElems.hdrRes) oipElems.hdrRes.textContent = ceSorted[0]?.strike || '--'; if (oipElems.hdrSupp) oipElems.hdrSupp.textContent = peSorted[0]?.strike || '--';
    if (oipElems.hdrCeOI) oipElems.hdrCeOI.textContent = fmtL(ce.total_oi); if (oipElems.hdrCeChg) oipElems.hdrCeChg.textContent = fmtL(ce.change_in_oi);
    if (oipElems.hdrPeOI) oipElems.hdrPeOI.textContent = fmtL(pe.total_oi); if (oipElems.hdrPeChg) oipElems.hdrPeChg.textContent = fmtL(pe.change_in_oi);
    if (pcr >= 1.25) { if (oipElems.hdrTrend) oipElems.hdrTrend.textContent = 'Bullish'; if (oipElems.hdrTrend) oipElems.hdrTrend.className = 'oip-hdr-val grn'; }
    else if (pcr <= 0.6) { if (oipElems.hdrTrend) oipElems.hdrTrend.textContent = 'Bearish'; if (oipElems.hdrTrend) oipElems.hdrTrend.className = 'oip-hdr-val red'; }
    else { if (oipElems.hdrTrend) oipElems.hdrTrend.textContent = 'Neutral'; if (oipElems.hdrTrend) oipElems.hdrTrend.className = 'oip-hdr-val'; }
    let atm = '--', mind = Infinity;
    strikes.forEach(s => { const d = Math.abs(s.strike - p); if (d < mind) { mind = d; atm = s.strike; } });
    if (oipElems.hdrAtm) oipElems.hdrAtm.textContent = atm;
}

let oipLevelLines = [];
function oipDrawIntrinsicLines(intrinsic, view = 'index') {
    if (!oipIntrinsicChart || !oipIntrinsicSeries) return;
    oipLevelLines.forEach(l => { 
        try { oipIntrinsicSeries.removePriceLine(l); if(oipIntrinsicPeSeries) oipIntrinsicPeSeries.removePriceLine(l); } catch(e){} 
    });
    oipLevelLines = [];
    if (!oipElems.showLevels?.checked || !intrinsic) return;
    const { ce_intrinsic, pe_intrinsic } = intrinsic;
    const step = parseInt(oipElems.step?.value) || 50, mult = parseInt(oipElems.multiplier?.value) || 12;
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
    const tgtDist = parseInt(oipElems.targetDistance?.value) || 50;

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
        const d = new Date(t * 1000);
        const key = `${d.getUTCFullYear()}-${d.getUTCMonth()}-${d.getUTCDate()}`;
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

        if (entry != null && !isNaN(entry)) {
            entryData.push  ({ time: t, value: entry });
            t1Data.push     ({ time: t, value: entry + tgtDist });
            t2Data.push     ({ time: t, value: entry + 2 * tgtDist });
        }
        if (cur != null && !isNaN(cur)) {
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
        oipIntChartReady = true;  // Intrinsic chart now has data — safe to sync
        if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(indexCandles));
        if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
        if (oipIntrinsicChart.setMarkers) oipIntrinsicChart.setMarkers([], []);
    } else if (oipOptionData) {
        const ceRaw = oipOptionData.filter(c => c.type === 'CE'), peRaw = oipOptionData.filter(c => c.type === 'PE');
        // Append whitespace to option data to force future x-axis labels
        const ceData = [...ceRaw, ...oipFutureWhitespace];
        const peData = [...peRaw, ...oipFutureWhitespace];
        
        const ce_levels = oipOIData.intrinsic?.ce_levels || [];
        const pe_levels = oipOIData.intrinsic?.pe_levels || [];
        if (view === 'combined') {
            oipIntrinsicChart.update(ceData, peData, resetZoom);
            oipIntChartReady = true;  // Intrinsic chart now has data — safe to sync
            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));
        } else if (view === 'ce') {
            oipIntrinsicChart.update(ceData, null, resetZoom);
            oipIntChartReady = true;
            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
        } else {
            oipIntrinsicChart.update(null, peData, resetZoom);
            oipIntChartReady = true;
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
    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        // Use UTC methods to match the 'Fake IST Epoch' from server
        const ds = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
        if (!currentDay || currentDay.date !== ds) {
            if (currentDay) days.push(currentDay);
            currentDay = { date: ds, isoDate: ds, high: c.high, low: c.low, close: c.close, times: [], closes: [] };
        }
        currentDay.high = Math.max(currentDay.high, c.high); 
        currentDay.low = Math.min(currentDay.low, c.low); 
        currentDay.close = c.close;
        currentDay.times.push(c.time); 
        currentDay.closes.push(c.close);
    });
    if (currentDay) days.push(currentDay);
    let daysData = [];
    for (let i = 1; i < days.length; i++) {
        const prev = days[i - 1], curr = days[i];
        let oH = prev.high, oL = prev.low, oC = prev.close;
        if (oipOIData?.daily_ohlc?.[prev.isoDate]) { const t = oipOIData.daily_ohlc[prev.isoDate]; oH = t.high; oL = t.low; oC = t.close; }
        const pp = (oH + oL + oC) / 3; 
        const bc = (oH + oL) / 2;
        const tc = (pp - bc) + pp; 
        
        const range = oH - oL;
        const r1 = (pp * 2) - oL;
        const s1 = (pp * 2) - oH;
        const r2 = pp + range;
        const s2 = pp - range;
        const r3 = r1 + range;
        const s3 = s1 - range;
        const r4 = r3 + (r2 - r1);
        const s4 = s3 - (s1 - s2);
        
        const cr3 = oC + (range * 1.1) / 4;
        const cs3 = oC - (range * 1.1) / 4;
        
        let boxes = [
            { type: 'cpr', min: Math.min(tc, bc), max: Math.max(tc, bc), times: curr.times },
            { type: 'r1_r2', min: Math.min(r1, r2), max: Math.max(r1, r2), times: curr.times },
            { type: 'r2_r3', min: Math.min(r2, r3), max: Math.max(r2, r3), times: curr.times },
            { type: 'r3_r4', min: Math.min(r3, r4), max: Math.max(r3, r4), times: curr.times },
            { type: 's1_s2', min: Math.min(s1, s2), max: Math.max(s1, s2), times: curr.times },
            { type: 's2_s3', min: Math.min(s2, s3), max: Math.max(s2, s3), times: curr.times },
            { type: 's3_s4', min: Math.min(s3, s4), max: Math.max(s3, s4), times: curr.times }
        ];
        
        daysData.push({
            times: curr.times,
            levels: { prevH: oH, prevL: oL, r1, r2, r3, r4, s1, s2, s3, s4, cr3, cs3, pp, tc, bc },
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

    const show = oipElems.showCpr?.checked;
    if (!show || !candles || !candles.length) { 
        oipDrawFutureCpr(candles);   // still run so it can clear its own series
        return; 
    }
    
    const daysData = oipCalculateDynamicCPR(candles);
    if (!daysData) return;
    
    const lineStyles = {
        prevH: { color: '#ef07f9', lineWidth: 1 },
        prevL: { color: '#ef07f9', lineWidth: 1 },
        pp: { color: '#3366ff', lineWidth: 1 },
        bc: { color: '#3366ff', lineWidth: 1 },
        tc: { color: '#3366ff', lineWidth: 1 },
        r1: { color: '#006400', lineWidth: 1 },
        r2: { color: '#006400', lineWidth: 1 },
        r3: { color: '#006400', lineWidth: 1 },
        r4: { color: '#006400', lineWidth: 1 },
        s1: { color: '#ff0000', lineWidth: 1 },
        s2: { color: '#ff0000', lineWidth: 1 },
        s3: { color: '#ff0000', lineWidth: 1 },
        s4: { color: '#ff0000', lineWidth: 1 },
        cr3: { color: '#a020f0', lineWidth: 2 },
        cs3: { color: '#a020f0', lineWidth: 2 }
    };
    
    const boxColors = {
        'cpr': 'rgba(51, 102, 255, 0.05)',
        'r1_r2': 'rgba(0, 204, 102, 0.02)',
        'r2_r3': 'rgba(0, 204, 102, 0.02)',
        'r3_r4': 'rgba(0, 204, 102, 0.02)',
        's1_s2': 'rgba(255, 0, 0, 0.02)',
        's2_s3': 'rgba(255, 0, 0, 0.02)',
        's3_s4': 'rgba(255, 0, 0, 0.02)'
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
                // Removed autoscaleInfoProvider: () => null to ensure visibility
            });
            const val = day.levels[key];
            if (val != null && !isNaN(val)) {
                const data = day.times.map(t => ({ time: t, value: val }));
                series.setData(data);
                window.oipCprLineSeries.push(series);
            }
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
                crosshairMarkerVisible: false,
                autoscaleInfoProvider: () => null
            });
            const val = box.max;
            if (val != null && !isNaN(val)) {
                const data = box.times.map(t => ({ time: t, value: val }));
                boxSeries.setData(data);
                window.oipCprBoxSeries.push(boxSeries);
            }
        });
    });

    // Draw the Future CPR (next session projection) — dashed lines
    oipDrawFutureCpr(candles);
}

/**
 * Draw Future CPR — the CPR projected for tomorrow, calculated from the
 * current (today's) day OHLC. Drawn as dashed lines extending one full
 * session width beyond the last candle, matching the Mine CPR Pine Script.
 */
function oipDrawFutureCpr(candles) {
    // Cleanup previous future CPR series
    if (window.oipFutureCprSeries) {
        window.oipFutureCprSeries.forEach(s => { try { oipOIChart.removeSeries(s); } catch(e){} });
    }
    window.oipFutureCprSeries = [];

    const show = oipElems.showFutureCpr?.checked;
    if (!show || !candles || !candles.length) {
        // Clear series if not showing
        if (window.oipFutureCprSeries) {
            window.oipFutureCprSeries.forEach(s => { try { oipOIChart.removeSeries(s); } catch(e){} });
            window.oipFutureCprSeries = [];
        }
        return;
    }

    // Build per-day OHLC from candles using UTC methods
    const days = [];
    let currentDay = null;
    for (const c of candles) {
        const d = new Date(c.time * 1000);
        const ds = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
        if (!currentDay || currentDay.date !== ds) {
            if (currentDay) days.push(currentDay);
            currentDay = { date: ds, startTime: c.time, endTime: c.time, high: c.high, low: c.low, close: c.close };
        }
        currentDay.high = Math.max(currentDay.high, c.high);
        currentDay.low  = Math.min(currentDay.low,  c.low);
        currentDay.close = c.close;
        currentDay.endTime = c.time;
    }
    if (currentDay) days.push(currentDay);

    // Need at least the current day to project
    if (days.length < 1) return;

    // Use the LAST (today's) day as the source of the Future CPR
    const today = days[days.length - 1];

    // Allow override from daily_ohlc if available
    let oH = today.high, oL = today.low, oC = today.close;
    if (oipOIData?.daily_ohlc) {
        const d = new Date(today.startTime * 1000);
        const isoDate = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
        const override = oipOIData.daily_ohlc[isoDate];
        if (override) { oH = override.high; oL = override.low; oC = override.close; }
    }

    // Calculate Future CPR levels from today's OHLC
    const range = oH - oL;
    const pp  = (oH + oL + oC) / 3;
    const bc  = (oH + oL) / 2;
    const tc  = (pp - bc) + pp;
    const r1  = (pp * 2) - oL;
    const r2  = pp + range;
    const r3  = r1 + range;
    const r4  = r3 + (r2 - r1);
    const s1  = (pp * 2) - oH;
    const s2  = pp - range;
    const s3  = s1 - range;
    const s4  = s3 - (s1 - s2);
    // Project forward from the last candle to the start of the next session
    // Find next market start (09:15 IST) — handling weekends and gaps
    const lastD = new Date(today.endTime * 1000);
    const nextD = new Date(lastD);
    nextD.setUTCDate(lastD.getUTCDate() + 1);
    const dayOfWeek = nextD.getUTCDay();
    if (dayOfWeek === 6) nextD.setUTCDate(nextD.getUTCDate() + 2); // Sat -> Mon
    else if (dayOfWeek === 0) nextD.setUTCDate(nextD.getUTCDate() + 1); // Sun -> Mon
    nextD.setUTCHours(9, 15, 0, 0);
    
    const nextStart = oipInterval === 'day' ? (today.endTime + 86400) : (nextD.getTime() / 1000);
    // Project for 1 full session (375 mins) or 7 days for daily
    const sessionWidth = oipInterval === 'day' ? (7 * 86400) : (375 * 60); 
    const nextEnd = nextStart + sessionWidth;

    // Future CPR levels share same styling as current CPR but drawn dashed (lineStyle: 2)
    const futureLevels = [
        { value: pp,  color: '#3366ff', lineWidth: 1, label: 'F-PP' },
        { value: bc,  color: '#3366ff', lineWidth: 1, label: 'F-BC' },
        { value: tc,  color: '#3366ff', lineWidth: 1, label: 'F-TC' },
        { value: r1,  color: '#006400', lineWidth: 1, label: 'F-R1' },
        { value: r2,  color: '#006400', lineWidth: 1, label: 'F-R2' },
        { value: r3,  color: '#006400', lineWidth: 1, label: 'F-R3' },
        { value: r4,  color: '#006400', lineWidth: 1, label: 'F-R4' },
        { value: s1,  color: '#ff0000', lineWidth: 1, label: 'F-S1' },
        { value: s2,  color: '#ff0000', lineWidth: 1, label: 'F-S2' },
        { value: s3,  color: '#ff0000', lineWidth: 1, label: 'F-S3' },
        { value: s4,  color: '#ff0000', lineWidth: 1, label: 'F-S4' },
        { value: oH,  color: '#ef07f9', lineWidth: 1, label: 'F-PDH' },
        { value: oL,  color: '#ef07f9', lineWidth: 1, label: 'F-PDL' }
    ];

    futureLevels.forEach(({ value, color, lineWidth = 1 }) => {
        if (!isFinite(value) || value <= 0) return;
        const series = oipOIChart.addLineSeries({
            color,
            lineWidth,
            lineStyle: 2,          // dashed — matches Pine Script style=line.style_dashed
            lastValueVisible: true, // Show price labels on axis for future projections
            priceLineVisible: false,
            crosshairMarkerVisible: false
            // Removed autoscaleInfoProvider: () => null to ensure visibility
        });
        // Three-point line: anchor at last candle, then start of next session, then end of next session
        // Using three points ensures the line appears attached to the current chart data
        series.setData([
            { time: today.endTime, value },
            { time: nextStart,     value },
            { time: nextEnd,       value }
        ]);
        window.oipFutureCprSeries.push(series);
    });

    // Ensure the x-axis shows future dates on first load by adjusting the range AFTER data is set
    if (oipIsFirstLoad && candles.length) {
        setTimeout(() => {
            const visibleLen = Math.min(candles.length, 100);
            // Projecting significantly into the future logical space to force x-axis labels
            oipOIChart.timeScale().setVisibleLogicalRange({ 
                from: candles.length - visibleLen, 
                to: candles.length + 600 
            });
            oipIsFirstLoad = false;
        }, 100);
    }
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
    
    if (oipElems.spotHigh) oipElems.spotHigh.value = Math.round(rh*100)/100; if (oipElems.spotLow) oipElems.spotLow.value = Math.floor(rl*100)/100;
    oipLoadCandles(true, false);
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

async function oipExitAllOrders(btn) {
    if (!confirm("⚠️ CANCEL all pending orders and EXIT all positions at MARKET price?")) return;
    
    const originalText = btn.innerText;
    btn.disabled = true;
    btn.innerText = 'EXITING...';
    btn.style.opacity = '0.7';
    
    try {
        const res = await fetch('/api/order/exit-all', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content
            }
        });
        const r = await res.json();
        if (r.success) {
            const summary = r.summary.map(s => 
                `${s.broker}_${s.instance}: 🛑 ${s.cancelled_orders} Cancelled, ⚡ ${s.exited_positions} Exited`
            ).join('\n');
            showNotification(`✅ Global Exit Complete!\n${summary}`, 'success');
        } else {
            showNotification(`❌ Exit Failed: ${r.error || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        showNotification(`Exit error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerText = originalText;
        btn.style.opacity = '1';
    }
}

async function oipPlaceOrder(side, action, btn) {
    const strike = (side === 'CE') ? oipCurrentCEStrike : oipCurrentPEStrike;
    if (!strike) { showNotification(`No ${side} strike available.`, 'error'); return; }
    
    const limitPriceInput = document.getElementById('oipLimitPrice');
    const limitPrice = limitPriceInput ? parseFloat(limitPriceInput.value) : null;
    
    btn.disabled = true; const ot = btn.title; btn.title = "Placing...";
    try {
        const res = await fetch('/api/intraday-920/place-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content },
            body: JSON.stringify({ 
                symbol: oipSymbol, 
                strike: strike, 
                option_type: side, 
                action: action, 
                strategy: 'intrinsic',
                limit_price: limitPrice && !isNaN(limitPrice) ? limitPrice : null
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
    oipSymbol = s; if (oipElems.symbolInput) oipElems.symbolInput.value = s;
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

