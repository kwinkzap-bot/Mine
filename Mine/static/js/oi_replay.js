/**
 * OI Replay – Self-contained logic for Replay Mode
 */

'use strict';

/* ── State ────────────────────────────────────────────────── */
let oipOIChart = null;
let oipOISeries = null;
let oipIntrinsicChart = null;
let oipCombinedChart = null;
let oipCombinedSeries = null;
let oipIntrinsicSeries = null;
let oipIntrinsicPeSeries = null;
let oipOIData = null;
let oipOptionData = null;
let oipVwapSeries = null;
let oipVwapIntSeries = null;
let oipVwapIntPeSeries = null;
let oipCombinedVwapSeries = null;
let oipCprSeriesMap = {}; 
let oipRSISeriesObj = null;
let oipSignalMarkers = [];
let oipRSIMarkers = [];
let oipLevelLines = [];
let oipEma9Series = null, oipEma20Series = null, oipEma50Series = null, oipEma100Series = null, oipEma200Series = null;
let oipCEChart = null;
let oipPEChart = null;
let oipCESeries = null;
let oipPESeries = null;
let oipCEEma9Series = null;
let oipCEEma20Series = null;
let oipCEEma50Series = null;
let oipPEEma9Series = null;
let oipPEEma20Series = null;
let oipPEEma50Series = null;
let oipMaxPainLine = null;

const oipPremiumSeries = { entry: null, current: null, t1: null, t2: null };

// Replay State
let oipReplayIndex = 0;
let oipReplayTimer = null;
let oipFullCandles = null;
let oipFullOptionData = null;
window.oipSelectionMode = false;

let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

let oipAllStrikes = [];
let oipCurrentPrice = 0;
let oipAllSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'NIFTY MIDCAP 150', 'NIFTY AUTO', 'NIFTY Smallcap 100', 'NIFTY FMCG', 'NIFTY METAL', 'NIFTY PHARMA', 'NIFTY PSU BANK', 'NIFTY IT'];
let oipSymbol = 'NIFTY';
let oipLotSize = 50, oipStrikeStep = 50;
let oipInterval = 'minute';
let oipMode = 'change';
let oipRafId = null;
let oipOIChartReady = false;
let oipIntChartReady = false;
let oipCEChartReady = false;
let oipPEChartReady = false;
let oipCombChartReady = false;
let oipCustomStrikeSetOnLoad = false;

// DOM Cache
const oipElems = {
    symbolInput: null, symbolList: null, interval: null,
    spotHigh: null, spotLow: null, step: null, multiplier: null,
    view: null, showVwapOI: null, showVwapInt: null,
    showCpr: null, showEMA: null, showRSI: null, autoHL: null, chartWrap: null, canvas: null,
    tooltip: null, refreshIcon: null, itmCE: null, itmPE: null,
    hdrPrice: null, hdrPcr: null, hdrMaxPain: null, hdrCeOI: null,
    hdrCeChg: null, hdrPeOI: null,
    hdrPeChg: null, hdrTrend: null, hdrAtm: null, brokerSelect: null,
    showPremium: null, showSignals: null, first5mATM: null, targetDistance: null, customStrikeCheck: null, customStrikeDropdown: null,
    showEma9: null, showEma20: null, showEma50: null, showEma100: null, showEma200: null,
    exitAll: null, days: null, startDate: null, endDate: null,
    hdrLotSize: null, legendSum: null, combStrikeDisplay: null,
    hdrIVP: null, ivpGaugeBar: null, ivCrushAlert: null
};

/* ── Initialization ────────────────────────────────────────── */
function oipInitElems() {
    oipElems.symbolInput = document.getElementById('symbolSelect');
    oipElems.symbolList = document.getElementById('symbolDropdownList');
    oipElems.interval = document.getElementById('oipInterval');
    oipElems.spotHigh = document.getElementById('oipSpotHigh');
    oipElems.spotLow = document.getElementById('oipSpotLow');
    oipElems.step = document.getElementById('oipStep');
    oipElems.multiplier = document.getElementById('oipMultiplier');
    oipElems.view = document.getElementById('oipIntrinsicView');
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
    oipElems.hdrCeOI = document.getElementById('hdrCeOI');
    oipElems.hdrCeChg = document.getElementById('hdrCeChg');
    oipElems.hdrPeOI = document.getElementById('hdrPeOI');
    oipElems.hdrPeChg = document.getElementById('hdrPeChg');
    oipElems.hdrTrend = document.getElementById('hdrTrend');
    oipElems.hdrAtm = document.getElementById('hdrAtm');
    oipElems.hdrLotSize = document.getElementById('hdrLotSize');
    oipElems.brokerSelect = document.getElementById('oipBrokerSelect');
    oipElems.showPremium = document.getElementById('oipShowPremium');
    oipElems.showSignals = document.getElementById('oipShowSignals');
    oipElems.first5mATM = document.getElementById('oipFirst5mATM');
    oipElems.customStrikeCheck = document.getElementById('oipCustomStrikeCheck');
    oipElems.customStrikeDropdown = document.getElementById('oipCustomStrikeDropdown');
    oipElems.targetDistance = document.getElementById('oipTargetDistance');
    oipElems.showEma9 = document.getElementById('oipShowEma9');
    oipElems.showEma20 = document.getElementById('oipShowEma20');
    oipElems.showEma50 = document.getElementById('oipShowEma50');
    oipElems.showEma100 = document.getElementById('oipShowEma100');
    oipElems.showEma200 = document.getElementById('oipShowEma200');
    oipElems.legendSum = document.getElementById('oipLegendSum');
    oipElems.combStrikeDisplay = document.getElementById('oipCombinedStrikeDisplay');
    oipElems.days = document.getElementById('oipDays');
    oipElems.startDate = document.getElementById('oipStartDate');
    oipElems.endDate = document.getElementById('oipEndDate');
    oipElems.hdrIVP = document.getElementById('hdrIVP');
    oipElems.ivpGaugeBar = document.getElementById('ivpGaugeBar');
    oipElems.ivCrushAlert = document.getElementById('ivCrushAlert');
}

function syncCrosshair(sourceChart, targetChart, param, targetSeries) {
    if (!targetChart || !targetSeries) return;
    try {
        const isValid = param && param.point && param.time != null;
        if (!isValid) {
            targetChart.clearCrosshairPosition();
        } else {
            const price = targetSeries.coordinateToPrice(param.point.y);
            if (price != null) {
                targetChart.setCrosshairPosition(price, param.time, targetSeries);
            } else {
                targetChart.clearCrosshairPosition();
            }
        }
    } catch(e) {}
}

window.oipInitSecondaryCharts = function() {
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
            color: '#1b9981', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipVwapIntPeSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#8b5cf6', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });

        // Individual CE Chart
        oipCEChart = TradingViewChart.create({
            containerId: 'oipCEChart', data: [], type: 'CE',
            timeframe: oipInterval, options: { height: 320 }
        });
        oipCESeries = oipCEChart.series;

        // Individual PE Chart
        oipPEChart = TradingViewChart.create({
            containerId: 'oipPEChart', data: [], type: 'PE',
            timeframe: oipInterval, options: { height: 320 }
        });
        oipPESeries = oipPEChart.series;

        const showEma9 = oipElems.showEma9?.checked ?? false;
        const showEma20 = oipElems.showEma20?.checked ?? false;
        const showEma50 = oipElems.showEma50?.checked ?? false;

        const customAutoscale = (seriesObj, chartInstance) => () => {
            const data = seriesObj.data();
            const range = chartInstance.timeScale().getVisibleLogicalRange();
            if (!data || data.length === 0 || !range) return null;
            let min = Infinity, max = -Infinity;
            const start = Math.max(0, Math.floor(range.from));
            const end = Math.min(data.length - 1, Math.ceil(range.to));
            for (let i = start; i <= end; i++) {
                const c = data[i];
                if (c && c.high !== undefined) {
                    if (c.high > max) max = c.high;
                    if (c.low < min) min = c.low;
                }
            }
            if (min === Infinity) return null;
            if (min === max) { min -= 1; max += 1; }
            const pad = (max - min) * 0.1;
            return { priceRange: { minValue: min - pad, maxValue: max + pad } };
        };

        // No custom autoscale needed for single charts, defaults work best
        // oipCESeries.applyOptions({ autoscaleInfoProvider: customAutoscale(oipCESeries, oipCEChart.chart) });
        // oipPESeries.applyOptions({ autoscaleInfoProvider: customAutoscale(oipPESeries, oipPEChart.chart) });

        oipCEEma9Series = oipCEChart.chart.addLineSeries({ color: '#8b5cf6', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma9, autoscaleInfoProvider: () => null });
        oipCEEma20Series = oipCEChart.chart.addLineSeries({ color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma20, autoscaleInfoProvider: () => null });
        oipCEEma50Series = oipCEChart.chart.addLineSeries({ color: '#000000', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma50, autoscaleInfoProvider: () => null });

        oipPEEma9Series = oipPEChart.chart.addLineSeries({ color: '#8b5cf6', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma9, autoscaleInfoProvider: () => null });
        oipPEEma20Series = oipPEChart.chart.addLineSeries({ color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma20, autoscaleInfoProvider: () => null });
        oipPEEma50Series = oipPEChart.chart.addLineSeries({ color: '#000000', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma50, autoscaleInfoProvider: () => null });

        oipInitPremiumSeries();
        
        oipIntChartReady = true;
        oipCEChartReady = true;
        oipPEChartReady = true;

        let activeChartId = null;
        const setActive = (id) => activeChartId = id;
        ['mouseenter', 'touchstart'].forEach(e => {
            document.getElementById('oipCandleChart')?.addEventListener(e, () => setActive('index'), {passive: true});
            document.getElementById('oipIntrinsicChart')?.addEventListener(e, () => setActive('intrinsic'), {passive: true});
            document.getElementById('oipCEChart')?.addEventListener(e, () => setActive('ce'), {passive: true});
            document.getElementById('oipPEChart')?.addEventListener(e, () => setActive('pe'), {passive: true});
        });

        const syncRange = (sourceChart, targetCharts) => {
            const timeScale = sourceChart.timeScale();
            const range = timeScale.getVisibleLogicalRange();
            const offset = timeScale.options().rightOffset;
            
            if (!range) return;

            targetCharts.forEach(t => {
                if (!t) return;
                try {
                    t.timeScale().applyOptions({ rightOffset: offset });
                    t.timeScale().setVisibleLogicalRange(range);
                } catch(e) {}
            });
        };

        if (oipOIChart && oipIntrinsicChart && oipIntrinsicChart.chart) {
            oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                if (activeChartId !== 'index' || !oipOIChartReady || !oipIntChartReady) return; 
                syncRange(oipOIChart, [oipIntrinsicChart.chart, oipCEChart?.chart, oipPEChart?.chart]);
            });
            oipIntrinsicChart.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                if (activeChartId !== 'intrinsic' || !oipOIChartReady || !oipIntChartReady) return; 
                syncRange(oipIntrinsicChart.chart, [oipOIChart, oipCEChart?.chart, oipPEChart?.chart]);
            });
            oipCEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                if (activeChartId !== 'ce' || !oipOIChartReady || !oipCEChartReady) return; 
                syncRange(oipCEChart.chart, [oipOIChart, oipIntrinsicChart.chart, oipPEChart?.chart]);
            });
            oipPEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                if (activeChartId !== 'pe' || !oipOIChartReady || !oipPEChartReady) return; 
                syncRange(oipPEChart.chart, [oipOIChart, oipIntrinsicChart.chart, oipCEChart?.chart]);
            });
        }

        const syncSize = (chart, wrap) => {
            if (!chart || !wrap) return;
            chart.applyOptions({ width: wrap.clientWidth });
        };

        if (oipIntrinsicChart?.chart) {
            const wrap = document.getElementById('oipIntrinsicChartWrap');
            if (wrap) new ResizeObserver(() => syncSize(oipIntrinsicChart.chart, wrap)).observe(wrap);
        }
        if (oipCEChart?.chart) {
            const wrap = document.getElementById('oipCEChartWrap');
            if (wrap) new ResizeObserver(() => syncSize(oipCEChart.chart, wrap)).observe(wrap);
        }
        if (oipPEChart?.chart) {
            const wrap = document.getElementById('oipPEChartWrap');
            if (wrap) new ResizeObserver(() => syncSize(oipPEChart.chart, wrap)).observe(wrap);
        }

        if (oipOIChart) {
            oipOIChart.subscribeCrosshairMove(param => {
                if (activeChartId !== 'index') return;
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipOIChart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipOIChart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipOIChart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipIntrinsicChart?.chart) {
            oipIntrinsicChart.chart.subscribeCrosshairMove(param => {
                if (activeChartId !== 'intrinsic') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipIntrinsicChart.chart, oipOIChart, param, oipOISeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipIntrinsicChart.chart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipIntrinsicChart.chart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipCEChart?.chart) {
            oipCEChart.chart.subscribeCrosshairMove(param => {
                if (activeChartId !== 'ce') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipCEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipCEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipCEChart.chart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipPEChart?.chart) {
            oipPEChart.chart.subscribeCrosshairMove(param => {
                if (activeChartId !== 'pe') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipPEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipPEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipPEChart.chart, oipCEChart.chart, param, oipCESeries);
            });
        }
    }
};

/* ── Logic Functions ───────────────────────────────────────── */
function oipUpdateEmaVisibility() {
    const s9 = oipElems.showEma9?.checked ?? false;
    const s20 = oipElems.showEma20?.checked ?? false;
    const s50 = oipElems.showEma50?.checked ?? false;
    const s100 = oipElems.showEma100?.checked ?? false;
    const s200 = oipElems.showEma200?.checked ?? false;

    if (oipEma9Series) oipEma9Series.applyOptions({ visible: s9 });
    if (oipEma20Series) oipEma20Series.applyOptions({ visible: s20 });
    if (oipEma50Series) oipEma50Series.applyOptions({ visible: s50 });
    if (oipEma100Series) oipEma100Series.applyOptions({ visible: s100 });
    if (oipEma200Series) oipEma200Series.applyOptions({ visible: s200 });

    if (oipCEEma9Series) oipCEEma9Series.applyOptions({ visible: s9 });
    if (oipCEEma20Series) oipCEEma20Series.applyOptions({ visible: s20 });
    if (oipCEEma50Series) oipCEEma50Series.applyOptions({ visible: s50 });

    if (oipPEEma9Series) oipPEEma9Series.applyOptions({ visible: s9 });
    if (oipPEEma20Series) oipPEEma20Series.applyOptions({ visible: s20 });
    if (oipPEEma50Series) oipPEEma50Series.applyOptions({ visible: s50 });
}

function oipInitCharts() {
    const elOI = document.getElementById('oipCandleChart');
    const wrapOI = oipElems.chartWrap;
    if (elOI && typeof LightweightCharts !== 'undefined') {
        const customAutoscale = () => {
            if (!oipOIChart || !oipOISeries) return null;
            const data = oipOISeries.data();
            const range = oipOIChart.timeScale().getVisibleLogicalRange();
            if (!data || data.length === 0 || !range) return null;
            let min = Infinity, max = -Infinity;
            const start = Math.max(0, Math.floor(range.from));
            const end = Math.min(data.length - 1, Math.ceil(range.to));
            for (let i = start; i <= end; i++) {
                const c = data[i];
                if (c && c.high !== undefined) {
                    if (c.high > max) max = c.high;
                    if (c.low < min) min = c.low;
                }
            }
            if (min === Infinity) return null;
            if (min === max) { min -= 1; max += 1; }
            const pad = (max - min) * 0.1;
            return { priceRange: { minValue: min - pad, maxValue: max + pad } };
        };

        oipOIChart = LightweightCharts.createChart(elOI, {
            width: elOI.clientWidth || 1200, height: 360,
            layout: { textColor: '#374151', background: { type: 'solid', color: '#ffffff' } },
            grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
            crosshair: { mode: 0, vertLine: { color: '#9ca3af', style: 3 }, horzLine: { color: '#9ca3af', style: 3, labelBackgroundColor: '#0969da' } },
            timeScale: { timeVisible: true, textColor: '#6b7280', borderColor: 'transparent', rightOffset: 20, barSpacing: 8, fixLeftEdge: false, fixRightEdge: false, shiftVisibleRangeOnNewBar: false },
            rightPriceScale: { textColor: '#64748b', borderColor: 'transparent', width: 85, autoScale: true, visible: true, scaleMargins: { top: 0, bottom: 0 }, entireTextOnly: true },
            handleScroll: true, handleScale: true,
            localization: { locale: 'en-IN', timeFormatter: t => { const d = new Date(t * 1000); return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`; }, timezone: 'Etc/UTC' }
        });

        oipOISeries = oipOIChart.addCandlestickSeries({ 
            upColor: '#10b981', downColor: '#ef4444', borderUpColor: '#10b981', borderDownColor: '#ef4444', wickUpColor: '#10b981', wickDownColor: '#ef4444',
            autoscaleInfoProvider: customAutoscale
        });
        oipVwapSeries = oipOIChart.addLineSeries({ color: '#f59e0b', lineWidth: 2, visible: false, priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null });
        oipEma9Series = oipOIChart.addLineSeries({ color: '#22c55e', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, autoscaleInfoProvider: () => null });
        oipEma20Series = oipOIChart.addLineSeries({ color: '#f97316', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, autoscaleInfoProvider: () => null });
        oipEma50Series = oipOIChart.addLineSeries({ color: '#ef4444', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, autoscaleInfoProvider: () => null });
        oipEma100Series = oipOIChart.addLineSeries({ color: '#3b82f6', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, autoscaleInfoProvider: () => null });
        oipEma200Series = oipOIChart.addLineSeries({ color: '#000000', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, autoscaleInfoProvider: () => null });

        oipOIChartReady = true;
        
        // Add Scroll to Latest button (Right Arrow)
        if (typeof TradingViewChart !== 'undefined' && TradingViewChart.addScrollButton) {
            TradingViewChart.addScrollButton(oipOIChart, oipOISeries, elOI);
        }

        oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(() => oipRequestDraw());
        oipOIChart.timeScale().subscribeVisibleTimeRangeChange(() => oipRequestDraw());
        if (wrapOI) new ResizeObserver(() => { oipOIChart.applyOptions({ width: wrapOI.clientWidth }); oipRequestDraw(); }).observe(wrapOI);
    }
    if (window.oipInitSecondaryCharts) window.oipInitSecondaryCharts();
}

function oipRequestDraw() { if (!oipRafId) oipRafId = requestAnimationFrame(oipDrawOIBars); }

function oipDrawOIBars() {
    oipRafId = null;
    const canvas = oipElems.canvas; const wrap = oipElems.chartWrap;
    if (!canvas || !wrap || !oipOISeries || !oipAllStrikes.length) return;
    const ctx = canvas.getContext('2d');
    const W = wrap.clientWidth, H = wrap.clientHeight, dpr = window.devicePixelRatio || 1;
    if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = W + 'px'; canvas.style.height = H + 'px'; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    
    const plotRight = W - 70; const MAX_BAR_PX = Math.min(plotRight * 0.18, 140);
    const getCE = (s) => (oipMode === 'total' ? (s.ce_oi || 0) : (s.ce_change_in_oi || 0));
    const getPE = (s) => (oipMode === 'total' ? (s.pe_oi || 0) : (s.pe_change_in_oi || 0));
    
    let maxVal = 1; oipAllStrikes.forEach(s => { maxVal = Math.max(maxVal, Math.abs(getCE(s)), Math.abs(getPE(s))); });
    let barH = 8;
    oipAllStrikes.forEach(s => {
        const y = oipOISeries.priceToCoordinate(s.strike);
        if (y === null || y < 0 || y > H) return;
        const ceW = (Math.abs(getCE(s)) / maxVal) * MAX_BAR_PX, peW = (Math.abs(getPE(s)) / maxVal) * MAX_BAR_PX;
        ctx.fillStyle = 'rgba(239, 68, 68, 0.6)'; ctx.fillRect(plotRight - ceW, y - barH, ceW, barH);
        ctx.fillStyle = 'rgba(16, 185, 129, 0.6)'; ctx.fillRect(plotRight - peW, y, peW, barH);
    });
}

function oipCalculateVWAP(candles) {
    if (!candles || !candles.length) return [];
    let cumPV = 0, cumV = 0, lastDate = null, result = [], lastValidPrice = 0;
    candles.forEach(c => {
        const d = new Date(c.time * 1000), date = d.getUTCDate();
        if (date !== lastDate) { cumPV = 0; cumV = 0; lastDate = date; }
        
        const price = (c.close !== undefined) ? (c.high + c.low + c.close) / 3 : lastValidPrice;
        if (c.close !== undefined) lastValidPrice = price;
        
        const v = c.volume || 1; 
        cumPV += price * v; cumV += v;
        result.push({ time: c.time, value: cumPV / cumV });
    });
    return result;
}

function oipCalculateFixedEMA(data, period) {
    if (!data || !data.length) return [];
    let ema = [], k = 2 / (period + 1), lastValid = data.find(d => d.close !== undefined)?.close || 0;
    let prev = lastValid;
    data.forEach(d => {
        let current = (d.close !== undefined) ? d.close : prev;
        let val = (current * k) + (prev * (1 - k)); 
        ema.push({ time: d.time, value: val }); 
        prev = val;
    });
    return ema;
}

function oipCalculateDynamicCPR(candles) {
    if (!candles || !candles.length) return null;
    const days = []; let currentDay = null;
    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        const ds = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
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
        const pp = (oH + oL + oC) / 3;
        const bc = (oH + oL) / 2;
        const tc = (pp - bc) + pp;
        const range = oH - oL;
        const r1 = (pp * 2) - oL, s1 = (pp * 2) - oH;
        const r2 = pp + range, s2 = pp - range;
        const r3 = r1 + range, s3 = s1 - range;
        const r4 = r3 + (r2 - r1), s4 = s3 - (s1 - s2);
        const cr3 = oC + (range * 1.1) / 4, cs3 = oC - (range * 1.1) / 4;
        daysData.push({
            times: curr.times,
            levels: { prevH: oH, prevL: oL, r1, r2, r3, r4, s1, s2, s3, s4, cr3, cs3, pp, tc, bc },
            boxes: [{ type: 'cpr', min: Math.min(tc, bc), max: Math.max(tc, bc), times: curr.times }]
        });
    }
    return daysData;
}

function oipDrawCpr(candles) {
    if (!oipOIChart) return;
    const show = oipElems.showCpr?.checked;
    Object.values(oipCprSeriesMap).forEach(s => s.setData([]));
    if (!show || !candles || !candles.length) return;
    const daysData = oipCalculateDynamicCPR(candles);
    if (!daysData) return;
    const lineStyles = {
        prevH: { color: '#ef07f9', lineWidth: 1 }, prevL: { color: '#ef07f9', lineWidth: 1 },
        pp: { color: '#3366ff', lineWidth: 1 }, bc: { color: '#3366ff', lineWidth: 1 }, tc: { color: '#3366ff', lineWidth: 1 },
        r1: { color: '#006400', lineWidth: 1 }, r2: { color: '#006400', lineWidth: 1 }, r3: { color: '#006400', lineWidth: 1 }, r4: { color: '#006400', lineWidth: 1 },
        s1: { color: '#ff0000', lineWidth: 1 }, s2: { color: '#ff0000', lineWidth: 1 }, s3: { color: '#ff0000', lineWidth: 1 }, s4: { color: '#ff0000', lineWidth: 1 },
        cr3: { color: '#a020f0', lineWidth: 2 }, cs3: { color: '#a020f0', lineWidth: 2 }
    };
    daysData.forEach((day, dayIdx) => {
        Object.keys(day.levels).forEach(key => {
            const seriesKey = `line_${key}_${dayIdx}`;
            let series = oipCprSeriesMap[seriesKey];
            if (!series) {
                series = oipOIChart.addLineSeries({ ...lineStyles[key], lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
                oipCprSeriesMap[seriesKey] = series;
            }
            series.setData(day.times.map(t => ({ time: t, value: day.levels[key] })));
        });
        day.boxes.forEach((box, boxIdx) => {
            const seriesKey = `box_${box.type}_${dayIdx}_${boxIdx}`;
            let series = oipCprSeriesMap[seriesKey];
            if (!series) {
                const col = 'rgba(51, 102, 255, 0.05)';
                series = oipOIChart.addBaselineSeries({ baseValue: { type: 'price', price: box.min }, topFillColor1: col, topFillColor2: col, topLineColor: 'transparent', bottomFillColor1: col, bottomFillColor2: col, bottomLineColor: 'transparent', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
                oipCprSeriesMap[seriesKey] = series;
            }
            series.applyOptions({ baseValue: { type: 'price', price: box.min } });
            series.setData(day.times.map(t => ({ time: t, value: box.max })));
        });
    });
}

function oipCalculateRSISnR(candles) {
    if (!candles || candles.length < 15) return null;
    const len = 14; const rsi = new Array(candles.length).fill(null);
    let gains = 0, losses = 0;
    for (let i = 1; i <= len; i++) {
        const diff = candles[i].close - candles[i-1].close;
        if (diff >= 0) gains += diff; else losses -= diff;
    }
    let avgG = gains / len, avgL = losses / len;
    rsi[len] = avgL === 0 ? 100 : 100 - (100 / (1 + avgG / avgL));
    for (let i = len + 1; i < candles.length; i++) {
        const diff = candles[i].close - candles[i-1].close;
        avgG = (avgG * (len - 1) + (diff >= 0 ? diff : 0)) / len;
        avgL = (avgL * (len - 1) + (diff < 0 ? -diff : 0)) / len;
        rsi[i] = avgL === 0 ? 100 : 100 - (100 / (1 + avgG / avgL));
    }
    let level_ob = null, level_os = null, level_bull = null, level_bear = null;
    const ob_series = [], os_series = [], bull_series = [], bear_series = [], markers = [];
    const thresh_ob = 70, thresh_bull = 60, thresh_bear = 40, thresh_os = 30;
    let in_short = false, in_long = false, short_sl = null, long_sl = null;
    const L_OB = new Array(candles.length).fill(null), L_OS = new Array(candles.length).fill(null);

    for (let i = 1; i < candles.length; i++) {
        const c = candles[i], r = rsi[i], r_prev = rsi[i - 1];
        if (r === null || r_prev === null) continue;
        const avgHigh = (c.high + c.close) / 2, avgLow = (c.low + c.close) / 2;
        if (r_prev <= thresh_ob && r > thresh_ob) level_ob = avgHigh;
        if (r_prev >= thresh_os && r < thresh_os) level_os = avgLow;
        if ((r_prev <= thresh_bull && r > thresh_bull) || (r_prev >= thresh_bull && r < thresh_bull)) level_bull = avgHigh;
        if ((r_prev <= thresh_bear && r > thresh_bear) || (r_prev >= thresh_bear && r < thresh_bear)) level_bear = avgLow;
        L_OB[i] = level_ob; L_OS[i] = level_os;
        if (level_ob !== null) ob_series.push({ time: c.time, value: level_ob });
        if (level_os !== null) os_series.push({ time: c.time, value: level_os });
        if (level_bull !== null) bull_series.push({ time: c.time, value: level_bull });
        if (level_bear !== null) bear_series.push({ time: c.time, value: level_bear });

        if (i >= 2) {
            const pC = candles[i - 1], ppC = candles[i - 2];
            const pL_OB = L_OB[i - 1], ppL_OB = L_OB[i - 2], pL_OS = L_OS[i - 1], ppL_OS = L_OS[i - 2];
            if (pL_OB !== null && i < candles.length - 1) {
                const setup_ob_rej = (pC.close < pL_OB) && (pC.high >= pL_OB || ppC.close >= ppL_OB);
                if (setup_ob_rej && (c.close < pC.low)) {
                    in_short = true; in_long = false; short_sl = pC.high;
                    markers.push({ time: c.time, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'REJ' });
                }
            }
            if (pL_OS !== null && i < candles.length - 1) {
                const setup_os_rej = (pC.close > pL_OS) && (pC.low <= pL_OS || ppC.close <= ppL_OS);
                if (setup_os_rej && (c.close > pC.high)) {
                    in_long = true; in_short = false; long_sl = pC.low;
                    markers.push({ time: c.time, position: 'belowBar', color: '#14b8a6', shape: 'arrowUp', text: 'REJ' });
                }
            }
            if (in_short && i < candles.length - 1) {
                const tgtHit = level_bear !== null && c.low <= level_bear;
                const slHit = short_sl !== null && c.high >= short_sl;
                if (tgtHit || slHit) { in_short = false; markers.push({ time: c.time, position: 'aboveBar', color: tgtHit ? '#f97316' : '#800000', shape: 'circle', text: tgtHit ? '🎯' : 'SL' }); }
            }
            if (in_long && i < candles.length - 1) {
                const tgtHit = level_bull !== null && c.high >= level_bull;
                const slHit = long_sl !== null && c.low <= long_sl;
                if (tgtHit || slHit) { in_long = false; markers.push({ time: c.time, position: 'belowBar', color: tgtHit ? '#f97316' : '#800000', shape: 'circle', text: tgtHit ? '🎯' : 'SL' }); }
            }
        }
    }
    return { ob_series, os_series, bull_series, bear_series, markers };
}

function oipDrawRSI(candles) {
    if (!oipOIChart) return;
    if (!oipRSISeriesObj) {
        const base = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
        oipRSISeriesObj = {
            ob: oipOIChart.addLineSeries({ ...base, color: '#14b8a6', lineWidth: 2 }),
            bull: oipOIChart.addLineSeries({ ...base, color: '#14b8a6', lineWidth: 1 }),
            os: oipOIChart.addLineSeries({ ...base, color: '#ef4444', lineWidth: 2 }),
            bear: oipOIChart.addLineSeries({ ...base, color: '#ef4444', lineWidth: 1 })
        };
    }
    const show = oipElems.showRSI?.checked;
    Object.values(oipRSISeriesObj).forEach(s => s.applyOptions({ visible: !!show }));
    if (!show || !candles || !candles.length) {
        oipRSIMarkers = [];
        oipUpdateAllMarkers();
        return;
    }
    const res = oipCalculateRSISnR(candles);
    if (res) {
        oipRSISeriesObj.ob.setData(res.ob_series); oipRSISeriesObj.bull.setData(res.bull_series);
        oipRSISeriesObj.os.setData(res.os_series); oipRSISeriesObj.bear.setData(res.bear_series);
        oipRSIMarkers = res.markers || [];
    } else {
        oipRSIMarkers = [];
    }
    oipUpdateAllMarkers();
}

function oipUpdateAllMarkers() {
    if (!oipOISeries) return;
    const combined = [...oipSignalMarkers, ...oipRSIMarkers].sort((a, b) => a.time - b.time);
    oipOISeries.setMarkers(combined);
}

function oipCalculateSignals(candles) {
    if (!candles || candles.length < 20) return [];
    
    // We need EMA maps for the entire candle set
    const ema9 = oipCalculateFixedEMA(candles, 9);
    const ema20 = oipCalculateFixedEMA(candles, 20);
    const ema50 = oipCalculateFixedEMA(candles, 50);
    const e9Map = new Map(ema9.map(d => [d.time, d.value]));
    const e20Map = new Map(ema20.map(d => [d.time, d.value]));
    const e50Map = new Map(ema50.map(d => [d.time, d.value]));
    
    const signals = [];
    let buyState = 'IDLE', sellState = 'IDLE';
    let inBuyTrade = false, inSellTrade = false;

    for (let i = 1; i < candles.length - 1; i++) {
        const c = candles[i], t = c.time;
        const e9 = e9Map.get(t), e20 = e20Map.get(t), e50 = e50Map.get(t);
        if (!e9 || !e20 || !e50) continue;

        if (inBuyTrade && c.close < e50) { signals.push({ time: t, position: 'aboveBar', color: '#800000', shape: 'circle', text: 'B SL' }); inBuyTrade = false; }
        if (inSellTrade && c.close > e50) { signals.push({ time: t, position: 'belowBar', color: '#800000', shape: 'circle', text: 'S SL' }); inSellTrade = false; }

        if (c.close < e9 && c.close < e20) buyState = 'BELOW';
        else if (buyState === 'BELOW' && c.close > e9 && c.close > e20) buyState = 'CROSSED';
        else if (buyState === 'CROSSED' && c.open > e9 && c.open > e20 && c.close > e9 && c.close > e20 && c.high > e9 && c.high > e20 && c.low > e9 && c.low > e20) buyState = 'CONFIRMED';
        else if (buyState === 'CONFIRMED' && !inBuyTrade && c.low <= Math.max(e9, e20) && c.close > e9 && c.close > e20 && c.close > c.open && c.close > e50) {
            signals.push({ time: t, position: 'belowBar', color: '#22c55e', shape: 'arrowUp', text: 'BUY' });
            buyState = 'IDLE'; inBuyTrade = true; inSellTrade = false;
        }

        if (c.close > e9 && c.close > e20) sellState = 'ABOVE';
        else if (sellState === 'ABOVE' && c.close < e9 && c.close < e20) sellState = 'CROSSED';
        else if (sellState === 'CROSSED' && c.open < e9 && c.open < e20 && c.close < e9 && c.close < e20 && c.high < e9 && c.high < e20 && c.low < e9 && c.low < e20) sellState = 'CONFIRMED';
        else if (sellState === 'CONFIRMED' && !inSellTrade && c.high >= Math.min(e9, e20) && c.close < e9 && c.close < e20 && c.close < c.open && c.close < e50) {
            signals.push({ time: t, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'SELL' });
            sellState = 'IDLE'; inSellTrade = true; inBuyTrade = false;
        }
    }
    return signals;
}

function oipDrawSignals(signals, maxTime) {
    if (!oipOIChart || !oipOISeries) return;
    const show = oipElems.showSignals?.checked;
    if (!show) { oipSignalMarkers = []; oipUpdateAllMarkers(); return; }
    oipSignalMarkers = signals.filter(s => s.time <= maxTime);
    oipUpdateAllMarkers();
}

function oipDrawIntrinsicLines(intrinsic, view) {
    if (!oipIntrinsicSeries) return;
    oipLevelLines.forEach(l => { try { oipIntrinsicSeries.removePriceLine(l); if (oipIntrinsicPeSeries) oipIntrinsicPeSeries.removePriceLine(l); } catch(e){} });
    oipLevelLines = [];
    if (!intrinsic || view === 'index') return;
    const { ce_intrinsic, pe_intrinsic } = intrinsic;
    if (ce_intrinsic) oipLevelLines.push(oipIntrinsicSeries.createPriceLine({ price: ce_intrinsic, color: '#10b981', lineWidth: 2, title: 'CE IV' }));
    if (pe_intrinsic) oipLevelLines.push((oipIntrinsicPeSeries || oipIntrinsicSeries).createPriceLine({ price: pe_intrinsic, color: '#8b5cf6', lineWidth: 2, title: 'PE IV' }));
}

function oipDrawPremiumLines(view, index) {
    const show = oipElems.showPremium?.checked;
    if (!show || !oipPremiumSeries.entry) {
        Object.values(oipPremiumSeries).forEach(s => s?.applyOptions({ visible: false }));
        return;
    }
    Object.values(oipPremiumSeries).forEach(s => s?.applyOptions({ visible: true }));
    const p = oipCachedIndicators.premium;
    oipPremiumSeries.entry.setData(p.entry.slice(0, index + 1));
    oipPremiumSeries.current.setData(p.current.slice(0, index + 1));
    oipPremiumSeries.t1.setData(p.t1.slice(0, index + 1));
    oipPremiumSeries.t2.setData(p.t2.slice(0, index + 1));
}

function oipUpdateHeader(data) {
    if (!data || !oipElems.hdrPrice) return;
    oipElems.hdrPrice.textContent = (data.current_price || 0).toLocaleString('en-IN');
    if (oipElems.hdrPcr) oipElems.hdrPcr.textContent = (data.pcr_oi || 0).toFixed(2);
    if (oipElems.hdrAtm) oipElems.hdrAtm.textContent = data.max_pain || '--';
    if (oipElems.hdrCeOI) oipElems.hdrCeOI.textContent = (data.ce_summary?.total_oi || 0).toLocaleString();
    if (oipElems.hdrPeOI) oipElems.hdrPeOI.textContent = (data.pe_summary?.total_oi || 0).toLocaleString();
}

async function oipLoadMetadata() {
    try {
        const metaRes = await fetch(`/api/symbol-metadata?symbol=${oipSymbol}`);
        const metaData = await metaRes.json();
        if (metaData.success) {
            oipLotSize = metaData.lot_size || 0;
            oipStrikeStep = metaData.strike_step || 50;
            if (oipElems.hdrLotSize) oipElems.hdrLotSize.textContent = oipLotSize || '--';
            if (oipElems.step) oipElems.step.value = oipStrikeStep;
        }
    } catch (e) { console.warn('[OIP] Metadata fetch failed:', e); }
}

async function oipLoadCandles(forceFetch = true, resetZoom = false) {
    const btnPause = document.getElementById('oipReplayPause');
    if (btnPause && btnPause.style.display !== 'none') btnPause.click(); // Stop active replay
    await oipLoadMetadata();
    const h = parseFloat(oipElems.spotHigh?.value || 0), l = parseFloat(oipElems.spotLow?.value || 0);
    const s = parseInt(oipElems.step?.value || 50), m = parseInt(oipElems.multiplier?.value || 3);
    const customStrike = (oipElems.customStrikeCheck?.checked && oipElems.customStrikeDropdown?.value) ? oipElems.customStrikeDropdown?.value : '';
    
    let days = parseInt(oipElems.days?.value) || 5;
    let dateRangeParams = '';
    if (oipElems.startDate?.value && oipElems.endDate?.value) {
        dateRangeParams = `&start_date=${oipElems.startDate.value}&end_date=${oipElems.endDate.value}`;
        const start = new Date(oipElems.startDate.value);
        const end = new Date(oipElems.endDate.value);
        const diffDays = Math.ceil((end - start) / (1000 * 60 * 60 * 24)) + 1;
        if (diffDays > 0) {
            days = diffDays;
        }
    }

    const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=true&custom_strike=${customStrike}${dateRangeParams}&_t=${Date.now()}`;
    const res = await fetch(url); const data = await res.json();
    if (!data.success) return;

    // --- RESET STATE FOR NEW LOAD ---
    oipLastRefreshIndex = -1;
    oipFullCeData = [];
    oipFullPeData = [];
    oipCachedIndicators = {
        index: { vwap: [], ema9: [], ema20: [], ema50: [], ema100: [], ema200: [], rsi: null, signals: [] },
        ce: { vwap: [], ema9: [], ema20: [], ema50: [] },
        pe: { vwap: [], ema9: [], ema20: [], ema50: [] },
        premium: { entry: [], current: [], t1: [], t2: [] },
        cpr: null
    };
    
    // Clear all chart series
    if (oipOISeries) oipOISeries.setData([]);
    if (oipVwapSeries) oipVwapSeries.setData([]);
    [oipEma9Series, oipEma20Series, oipEma50Series, oipEma100Series, oipEma200Series].forEach(s => s?.setData([]));
    
    // Clear CPR lines from chart and map
    Object.values(oipCprSeriesMap).forEach(s => { try { oipOIChart.removeSeries(s); } catch(e) {} });
    oipCprSeriesMap = {};

    if (oipIntrinsicChart) {
        oipIntrinsicChart.series.setData([]);
        oipIntrinsicChart.peSeries.setData([]);
    }
    if (oipCEChart) oipCEChart.series.setData([]);
    if (oipPEChart) oipPEChart.series.setData([]);
    // --------------------------------

    if (data.strikes) {
        oipAllStrikes = data.strikes;
        oipUpdateCustomStrikeOptions(data.strikes, data.current_price);
    }

    oipOIData = data;
    oipFullCandles = data.candles || [];
    
    const alignToIndices = (optCandles) => {
        if (!oipFullCandles.length) return optCandles;
        const optMap = new Map(optCandles.map(c => [c.time, c]));
        return oipFullCandles.map(ic => {
            const existing = optMap.get(ic.time);
            return existing ? existing : { time: ic.time };
        });
    };

    const ceRaw = (data.ce_opt_candles || []);
    const peRaw = (data.pe_opt_candles || []);
    
    oipFullOptionData = [
        ...alignToIndices(ceRaw).map(c => ({...c, type:'CE'})), 
        ...alignToIndices(peRaw).map(c => ({...c, type:'PE'}))
    ];
    oipAllStrikes = data.strikes || []; oipCurrentPrice = data.current_price || 0;

    oipPrecalculateIndicators();
    oipSetupReplaySlider();

    // SHOW FULL DATA INITIALLY (Normal Chart Mode)
    if (oipOISeries) oipOISeries.setData(oipFullCandles);
    if (oipVwapSeries) oipVwapSeries.setData(oipCachedIndicators.index.vwap);
    if (oipEma9Series) oipEma9Series.setData(oipCachedIndicators.index.ema9);
    if (oipEma20Series) oipEma20Series.setData(oipCachedIndicators.index.ema20);
    if (oipEma50Series) oipEma50Series.setData(oipCachedIndicators.index.ema50);
    if (oipEma100Series) oipEma100Series.setData(oipCachedIndicators.index.ema100);
    if (oipEma200Series) oipEma200Series.setData(oipCachedIndicators.index.ema200);

    if (oipIntrinsicChart) oipIntrinsicChart.update(oipFullCeData, oipFullPeData, true);
    if (oipCEChart) oipCEChart.update(oipFullCeData, [], true);
    if (oipPEChart) oipPEChart.update(oipFullPeData, [], true);

    // Ensure Replay Toolbar is hidden by default
    const toolbar = document.getElementById('oipReplayToolbar');
    if (toolbar) toolbar.classList.add('hidden');
    
    // Fit content so it looks normal
    if (oipOIChartReady) {
        oipOIChart.timeScale().fitContent();
        
        // Wait a small bit for secondary charts to be fully ready before syncing
        setTimeout(() => {
            if (oipOIChartReady && oipIntChartReady) {
                const ts = oipOIChart.timeScale();
                const range = ts.getVisibleLogicalRange();
                const barSpacing = ts.options().barSpacing;
                const rightOffset = ts.options().rightOffset;

                if (range) {
                    [oipIntrinsicChart?.chart, oipCEChart?.chart, oipPEChart?.chart].forEach(c => {
                        if (c) {
                            c.timeScale().applyOptions({ barSpacing, rightOffset });
                            c.timeScale().setVisibleLogicalRange(range);
                        }
                    });
                }
            }
        }, 100);
    }
}

let oipFullCeData = [];
let oipFullPeData = [];
let oipCachedIndicators = {
    index: { vwap: [], ema9: [], ema20: [], ema50: [], ema100: [], ema200: [], rsi: null, signals: [] },
    ce: { vwap: [], ema9: [], ema20: [], ema50: [] },
    pe: { vwap: [], ema9: [], ema20: [], ema50: [] },
    premium: { entry: [], current: [], t1: [], t2: [] },
    cpr: null
};

function oipPrecalculateIndicators() {
    if (!oipFullCandles || !oipFullCandles.length) return;
    
    oipFullCeData = oipFullOptionData.filter(d => d.type === 'CE');
    oipFullPeData = oipFullOptionData.filter(d => d.type === 'PE');

    oipCachedIndicators.index.vwap = oipCalculateVWAP(oipFullCandles);
    oipCachedIndicators.index.ema9 = oipCalculateFixedEMA(oipFullCandles, 9);
    oipCachedIndicators.index.ema20 = oipCalculateFixedEMA(oipFullCandles, 20);
    oipCachedIndicators.index.ema50 = oipCalculateFixedEMA(oipFullCandles, 50);
    oipCachedIndicators.index.ema100 = oipCalculateFixedEMA(oipFullCandles, 100);
    oipCachedIndicators.index.ema200 = oipCalculateFixedEMA(oipFullCandles, 200);
    oipCachedIndicators.index.rsi = oipCalculateRSISnR(oipFullCandles);
    oipCachedIndicators.index.signals = oipCalculateSignals(oipFullCandles);
    oipCachedIndicators.cpr = oipCalculateDynamicCPR(oipFullCandles);

    oipCachedIndicators.ce.vwap = oipCalculateVWAP(oipFullCeData);
    oipCachedIndicators.ce.ema9 = oipCalculateFixedEMA(oipFullCeData, 9);
    oipCachedIndicators.ce.ema20 = oipCalculateFixedEMA(oipFullCeData, 20);
    oipCachedIndicators.ce.ema50 = oipCalculateFixedEMA(oipFullCeData, 50);

    oipCachedIndicators.pe.vwap = oipCalculateVWAP(oipFullPeData);
    oipCachedIndicators.pe.ema9 = oipCalculateFixedEMA(oipFullPeData, 9);
    oipCachedIndicators.pe.ema20 = oipCalculateFixedEMA(oipFullPeData, 20);
    oipCachedIndicators.pe.ema50 = oipCalculateFixedEMA(oipFullPeData, 50);

    // Premium Lines Pre-calc
    const dist = parseInt(oipElems.targetDistance?.value) || 50;
    const entry = [], current = [];
    oipCachedIndicators.ce.vwap.forEach((v, i) => {
        const pv = oipCachedIndicators.pe.vwap[i];
        if (pv) entry.push({ time: v.time, value: (v.value + pv.value) / 2 });
    });
    oipFullCeData.forEach((v, i) => {
        const pv = oipFullPeData[i];
        if (pv) current.push({ time: v.time, value: (v.close + pv.close) / 2 });
    });
    oipCachedIndicators.premium.entry = entry;
    oipCachedIndicators.premium.current = current;
    oipCachedIndicators.premium.t1 = entry.map(v => ({ time: v.time, value: v.value + dist }));
    oipCachedIndicators.premium.t2 = entry.map(v => ({ time: v.time, value: v.value + (2 * dist) }));
}

let oipLastRefreshIndex = -1;

function oipRefreshLocalView(view, resetZoom, index) {
    if (!oipFullCandles || index < 0) return;
    
    if (!oipFullCeData.length) oipPrecalculateIndicators();

    const isIncremental = index === oipLastRefreshIndex + 1;
    oipLastRefreshIndex = index;

    const updateOrSet = (series, fullData, idx) => {
        if (!series || !fullData) return;
        if (isIncremental) series.update(fullData[idx]);
        else series.setData(fullData.slice(0, idx + 1));
    };

    // 1. Index Chart
    updateOrSet(oipOISeries, oipFullCandles, index);
    updateOrSet(oipVwapSeries, oipCachedIndicators.index.vwap, index);
    updateOrSet(oipEma9Series, oipCachedIndicators.index.ema9, index);
    updateOrSet(oipEma20Series, oipCachedIndicators.index.ema20, index);
    updateOrSet(oipEma50Series, oipCachedIndicators.index.ema50, index);
    updateOrSet(oipEma100Series, oipCachedIndicators.index.ema100, index);
    updateOrSet(oipEma200Series, oipCachedIndicators.index.ema200, index);

    // 2. Indicators (CPR, RSI, Signals)
    const timeAtIdx = oipFullCandles[index].time;
    
    // Optimized RSI/Signals: Only slice the pre-calculated series
    if (oipCachedIndicators.index.rsi) {
        const rsi = oipCachedIndicators.index.rsi;
        const slice = (s, full) => s && s.setData(full.filter(d => d.time <= timeAtIdx));
        if (oipRSISeriesObj) {
            slice(oipRSISeriesObj.ob, rsi.ob_series);
            slice(oipRSISeriesObj.os, rsi.os_series);
            slice(oipRSISeriesObj.bull, rsi.bull_series);
            slice(oipRSISeriesObj.bear, rsi.bear_series);
        }
    }
    
    // Update Markers (RSI + Signals)
    const rsiMarkers = oipCachedIndicators.index.rsi?.markers.filter(m => m.time <= timeAtIdx) || [];
    const stratSignals = oipCachedIndicators.index.signals.filter(s => s.time <= timeAtIdx) || [];
    oipSignalMarkers = [...rsiMarkers, ...stratSignals];
    oipUpdateAllMarkers();

    // CPR Redraw
    if (oipCachedIndicators.cpr) {
        const visibleCpr = oipCachedIndicators.cpr.filter(d => d.times[0] <= timeAtIdx);
        oipRenderPrecalculatedCPR(visibleCpr, timeAtIdx);
    }

    // 3. Option Charts
    if (oipIntrinsicChart) {
        if (isIncremental) {
            oipIntrinsicChart.series.update(TradingViewChart.formatData([oipFullCeData[index]])[0]);
            oipIntrinsicChart.peSeries.update(TradingViewChart.formatData([oipFullPeData[index]])[0]);
        } else {
            oipIntrinsicChart.update(oipFullCeData.slice(0, index + 1), oipFullPeData.slice(0, index + 1), false);
        }
        updateOrSet(oipVwapIntSeries, oipCachedIndicators.ce.vwap, index);
        updateOrSet(oipVwapIntPeSeries, oipCachedIndicators.pe.vwap, index);
    }

    if (oipCEChart) {
        if (isIncremental) {
            const formatted = TradingViewChart.formatData([oipFullCeData[index]])[0];
            if (formatted) oipCEChart.series.update(formatted);
        } else {
            oipCEChart.update(oipFullCeData.slice(0, index + 1), [], false);
        }
        updateOrSet(oipCEEma9Series, oipCachedIndicators.ce.ema9, index);
        updateOrSet(oipCEEma20Series, oipCachedIndicators.ce.ema20, index);
        updateOrSet(oipCEEma50Series, oipCachedIndicators.ce.ema50, index);
    }

    if (oipPEChart) {
        if (isIncremental) {
            const formatted = TradingViewChart.formatData([oipFullPeData[index]])[0];
            if (formatted) oipPEChart.series.update(formatted);
        } else {
            oipPEChart.update(oipFullPeData.slice(0, index + 1), [], false);
        }
        updateOrSet(oipPEEma9Series, oipCachedIndicators.pe.ema9, index);
        updateOrSet(oipPEEma20Series, oipCachedIndicators.pe.ema20, index);
        updateOrSet(oipPEEma50Series, oipCachedIndicators.pe.ema50, index);
    }

    // 4. Legend & Metadata
    const ceVal = oipFullCeData[index]?.close || 0;
    const peVal = oipFullPeData[index]?.close || 0;
    const sumVal = ceVal + peVal;
    const strike = oipElems.customStrikeDropdown?.value || '--';
    const ceStrike = oipOIData?.intrinsic?.itm_ce_strike ?? strike;
    const peStrike = oipOIData?.intrinsic?.itm_pe_strike ?? strike;
    if (document.getElementById('oipLegendStrike')) document.getElementById('oipLegendStrike').textContent = strike;
    if (document.getElementById('oipLegendStrikeCE')) document.getElementById('oipLegendStrikeCE').textContent = ceStrike;
    if (document.getElementById('oipLegendStrikePE')) document.getElementById('oipLegendStrikePE').textContent = peStrike;

    if (document.getElementById('oipLegendCE')) document.getElementById('oipLegendCE').textContent = ceVal ? ceVal.toFixed(2) : '--';
    if (document.getElementById('oipLegendPE')) document.getElementById('oipLegendPE').textContent = peVal ? peVal.toFixed(2) : '--';
    if (document.getElementById('oipLegendSum')) document.getElementById('oipLegendSum').textContent = `SUM: ${sumVal.toFixed(2)}`;

    if (document.getElementById('oipLegendCEOnly')) document.getElementById('oipLegendCEOnly').textContent = ceVal ? ceVal.toFixed(2) : '--';
    if (document.getElementById('oipLegendPEOnly')) document.getElementById('oipLegendPEOnly').textContent = peVal ? peVal.toFixed(2) : '--';

    oipUpdateEmaVisibility();
    
    // Restore missing drawing calls
    if (oipOIData?.intrinsic) oipDrawIntrinsicLines(oipOIData.intrinsic, view);
    oipDrawPremiumLines(view, index);
    oipUpdateHeader(oipOIData);
    oipRequestDraw();

    if (resetZoom && oipOIChartReady) {
        const ts = oipOIChart.timeScale();
        ts.fitContent();
        
        if (oipIntChartReady) {
            const range = ts.getVisibleLogicalRange();
            const barSpacing = ts.options().barSpacing;
            const rightOffset = ts.options().rightOffset;
            
            [oipIntrinsicChart?.chart, oipCEChart?.chart, oipPEChart?.chart].forEach(c => {
                if (c) {
                    c.timeScale().applyOptions({ barSpacing, rightOffset });
                    if (range) c.timeScale().setVisibleLogicalRange(range);
                }
            });
        }
    }
}

function oipRenderPrecalculatedCPR(daysData, maxTime) {
    if (!oipOIChart || !oipElems.showCpr?.checked) return;
    Object.values(oipCprSeriesMap).forEach(s => s.setData([]));
    
    const lineStyles = {
        prevH: { color: '#ef07f9', lineWidth: 1 }, prevL: { color: '#ef07f9', lineWidth: 1 },
        pp: { color: '#3366ff', lineWidth: 1 }, bc: { color: '#3366ff', lineWidth: 1 }, tc: { color: '#3366ff', lineWidth: 1 },
        r1: { color: '#006400', lineWidth: 1 }, r2: { color: '#006400', lineWidth: 1 }, r3: { color: '#006400', lineWidth: 1 }, r4: { color: '#006400', lineWidth: 1 },
        s1: { color: '#ff0000', lineWidth: 1 }, s2: { color: '#ff0000', lineWidth: 1 }, s3: { color: '#ff0000', lineWidth: 1 }, s4: { color: '#ff0000', lineWidth: 1 },
        cr3: { color: '#a020f0', lineWidth: 2 }, cs3: { color: '#a020f0', lineWidth: 2 }
    };

    daysData.forEach((day, dayIdx) => {
        const visibleTimes = day.times.filter(t => t <= maxTime);
        if (!visibleTimes.length) return;

        Object.keys(day.levels).forEach(key => {
            const seriesKey = `line_${key}_${dayIdx}`;
            let s = oipCprSeriesMap[seriesKey];
            if (!s) {
                s = oipOIChart.addLineSeries({ ...lineStyles[key], lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
                oipCprSeriesMap[seriesKey] = s;
            }
            s.setData(visibleTimes.map(t => ({ time: t, value: day.levels[key] })));
        });
        day.boxes.forEach((box, bIdx) => {
            const seriesKey = `box_${box.type}_${dayIdx}_${bIdx}`;
            let s = oipCprSeriesMap[seriesKey];
            if (!s) {
                const col = 'rgba(51, 102, 255, 0.05)';
                s = oipOIChart.addBaselineSeries({ baseValue: { type: 'price', price: box.min }, topFillColor1: col, topFillColor2: col, topLineColor: 'transparent', bottomFillColor1: col, bottomFillColor2: col, bottomLineColor: 'transparent', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
                oipCprSeriesMap[seriesKey] = s;
            }
            s.applyOptions({ baseValue: { type: 'price', price: box.min } });
            s.setData(visibleTimes.map(t => ({ time: t, value: box.max })));
        });
    });
}

/* ── Replay Core ───────────────────────────────────────────── */
function oipResetReplay() { oipFullCandles = null; oipFullOptionData = null; oipReplayIndex = 0; oipLoadCandles(); }

function oipInitReplay() {
    const btnPlay = document.getElementById('oipReplayPlay'), btnPause = document.getElementById('oipReplayPause'), btnNext = document.getElementById('oipReplayNext'), btnJump = document.getElementById('oipJumpReplay');
    const slider = document.getElementById('oipReplaySlider'), speed = document.getElementById('oipReplaySpeed'), timeDisplay = document.getElementById('oipReplayTime');
    if (!btnPlay) return;

    function startTimer() { 
        stopTimer(); 
        const interval = parseInt(speed.value) || 1000;
        oipReplayTimer = setInterval(() => { 
            if (oipReplayIndex < oipFullCandles.length - 1) { 
                oipReplayIndex++; 
                update(); 
            } else {
                btnPause.click(); 
            }
        }, interval); 
    }

    function stopTimer() { 
        if (oipReplayTimer) clearInterval(oipReplayTimer); 
    }

    function step() { 
        if (oipReplayIndex < oipFullCandles.length - 1) { 
            oipReplayIndex++; 
            update(); 
        } 
    }

    function update() { 
        slider.value = oipReplayIndex; 
        const c = oipFullCandles[oipReplayIndex];
        if (c && timeDisplay) timeDisplay.textContent = new Date(c.time * 1000).toLocaleTimeString();
        oipRefreshLocalView('combined', false, oipReplayIndex); 
    }

    btnPlay.onclick = () => { 
        btnPlay.style.display = 'none'; 
        btnPause.style.display = 'inline-block'; 
        startTimer(); 
    };

    btnPause.onclick = () => { 
        btnPause.style.display = 'none'; 
        btnPlay.style.display = 'inline-block'; 
        stopTimer(); 
    };

    btnNext.onclick = () => step();
    slider.oninput = (e) => { 
        oipReplayIndex = parseInt(e.target.value); 
        update(); 
    };

    speed.onchange = () => {
        if (btnPause.style.display === 'inline-block') startTimer();
    };

    if (btnJump) {
        btnJump.onclick = () => {
            window.oipSelectionMode = !window.oipSelectionMode;
            btnJump.style.background = window.oipSelectionMode ? '#ef4444' : '#1e293b';
            if (window.oipSelectionMode) {
                oipOIChart.applyOptions({ handleScroll: false, handleScale: false });
                if (window.notify) notify.info('Click a bar on chart to start from there');
            } else {
                oipOIChart.applyOptions({ handleScroll: true, handleScale: true });
            }
        };
    }

    if (oipOIChart) {
        oipOIChart.subscribeClick(param => {
            if (!window.oipSelectionMode || !param.time || !oipFullCandles) return;
            const idx = oipFullCandles.findIndex(c => c.time === param.time);
            if (idx !== -1) {
                oipReplayIndex = idx;
                update();
                window.oipSelectionMode = false;
                btnJump.style.background = '#1e293b';
                oipOIChart.applyOptions({ handleScroll: true, handleScale: true });
            }
        });
    }
}

function oipSetupReplaySlider() {
    const slider = document.getElementById('oipReplaySlider');
    if (!slider || !oipFullCandles || !oipFullCandles.length) return;
    const lastIdx = oipFullCandles.length - 1;
    slider.max = lastIdx; 
    slider.value = lastIdx; 
    oipReplayIndex = lastIdx;
    const timeDisplay = document.getElementById('oipReplayTime');
    if (timeDisplay) timeDisplay.textContent = new Date(oipFullCandles[lastIdx].time * 1000).toLocaleTimeString();
    oipRefreshLocalView('combined', false, lastIdx);
}

function oipInitPremiumSeries() {
    const chart = oipIntrinsicChart?.chart;
    if (!chart) return;
    const base = { priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false, visible: false };
    oipPremiumSeries.entry = chart.addLineSeries({ ...base, color: '#4caf50', lineWidth: 2 });
    oipPremiumSeries.current = chart.addLineSeries({ ...base, color: '#2196f3', lineWidth: 2 });
    oipPremiumSeries.t1 = chart.addLineSeries({ ...base, color: '#e040fb', lineWidth: 1 });
    oipPremiumSeries.t2 = chart.addLineSeries({ ...base, color: '#f97316', lineWidth: 1 });
}

/* ── Bootstrap ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    window.oipReplayMode = true;
    oipInitElems(); oipInitCharts();
    const today = new Date().toISOString().split('T')[0];
    const prev = new Date(); prev.setDate(prev.getDate() - 30);
    if (oipElems.startDate) oipElems.startDate.value = prev.toISOString().split('T')[0];
    if (oipElems.endDate) oipElems.endDate.value = today;
    
    oipElems.startDate?.addEventListener('change', () => oipResetReplay());

    // Dropdown Logic
    oipElems.symbolInput?.addEventListener('input', (e) => oipRenderDropdown(e.target.value.toUpperCase(), oipElems.symbolList));
    oipElems.symbolInput?.addEventListener('click', function (e) {
        e.stopPropagation();
        if (oipElems.symbolList?.classList.contains('show')) {
            oipElems.symbolList?.classList.remove('show');
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

    oipElems.interval?.addEventListener('change', (e) => {
        oipInterval = e.target.value;
        oipResetReplay();
    });
    oipElems.days?.addEventListener('change', () => oipResetReplay());
    oipElems.customStrikeDropdown?.addEventListener('change', () => oipResetReplay());
    oipElems.customStrikeCheck?.addEventListener('change', () => oipResetReplay());
    oipElems.first5mATM?.addEventListener('change', () => oipResetReplay());
    oipElems.targetDistance?.addEventListener('change', () => {
        if (oipFullCandles) oipRefreshLocalView('combined', false, oipReplayIndex);
    });
    oipInitReplay(); 

    [
        'oipShowEma9', 'oipShowEma20', 'oipShowEma50', 'oipShowEma100', 'oipShowEma200',
        'oipShowCpr', 'oipShowRSI', 'oipShowSignals',
        'oipShowVwapOI', 'oipShowVwapInt', 'oipShowPremium'
    ].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            oipUpdateEmaVisibility();
            if (id === 'oipShowVwapInt' && oipCombinedVwapSeries) {
                oipCombinedVwapSeries.applyOptions({ visible: document.getElementById(id).checked });
            }
            if (oipFullCandles) oipRefreshLocalView('combined', false, oipReplayIndex);
        });
    });

    oipLoadCandles();

    // Toggle Replay Toolbar visibility
    const btnToggleReplay = document.getElementById('oipToggleReplayToolbar');
    const replayToolbar = document.getElementById('oipReplayToolbar');
    if (btnToggleReplay && replayToolbar) {
        replayToolbar.style.display = 'none';
        
        btnToggleReplay.onclick = () => {
            const isHidden = replayToolbar.classList.contains('hidden') || replayToolbar.style.display === 'none';
            if (isHidden) {
                // Opening Replay Mode
                replayToolbar.classList.remove('hidden');
                replayToolbar.style.display = 'flex';
                btnToggleReplay.style.background = '#fef08a';
                
                // Automatically trigger selection mode
                window.oipSelectionMode = true;
                const btnJump = document.getElementById('oipJumpReplay');
                if (btnJump) btnJump.style.background = '#ef4444';
                oipOIChart.applyOptions({ handleScroll: false, handleScale: false });
                if (window.notify) notify.info('Click a bar on chart to start Bar Replay');
            } else {
                // Closing Replay Mode (Restore Full View)
                replayToolbar.classList.add('hidden');
                replayToolbar.style.display = 'none';
                btnToggleReplay.style.background = '#fffbeb';
                window.oipSelectionMode = false;
                oipOIChart.applyOptions({ handleScroll: true, handleScale: true });
                
                // Reset to Full Chart view
                if (oipFullCandles && oipFullCandles.length) {
                    oipReplayIndex = oipFullCandles.length - 1;
                    const slider = document.getElementById('oipReplaySlider');
                    if (slider) slider.value = oipReplayIndex;
                    oipRefreshLocalView('combined', true, oipReplayIndex);
                }
            }
        };
    }
});
function oipUpdateCustomStrikeOptions(strikes, centerPrice = null) {
    if (!oipElems.customStrikeDropdown) return;
    let sortedStrikes = [];
    if (strikes && strikes.length > 0) {
        sortedStrikes = [...new Set(strikes.map(s => parseFloat(s.strike)))].sort((a, b) => a - b);
        const diffs = [];
        for (let i = 1; i < sortedStrikes.length; i++) {
            const d = Math.abs(sortedStrikes[i] - sortedStrikes[i - 1]);
            if (d > 0) diffs.push(d);
        }
        if (diffs.length > 0) {
            const counts = {};
            let maxCount = 0;
            let commonStep = oipStrikeStep;
            diffs.forEach(d => {
                counts[d] = (counts[d] || 0) + 1;
                if (counts[d] > maxCount) { maxCount = counts[d]; commonStep = d; }
            });
            if (commonStep > 0 && commonStep !== oipStrikeStep) {
                oipStrikeStep = commonStep;
                if (oipElems.step) oipElems.step.value = commonStep;
            }
        }
    }
    const refPrice = centerPrice || oipCurrentPrice || 25000;
    const step = oipStrikeStep || 50;
    const atm = Math.round(refPrice / step) * step;
    if (sortedStrikes.length > 20) {
        let atmIndex = sortedStrikes.findIndex(s => s >= refPrice);
        if (atmIndex === -1) atmIndex = sortedStrikes.length - 1;
        let start = Math.max(0, atmIndex - 10);
        let end = Math.min(sortedStrikes.length, start + 20);
        if (end === sortedStrikes.length) start = Math.max(0, end - 20);
        sortedStrikes = sortedStrikes.slice(start, end);
    }
    let opts = '';
    if (sortedStrikes.length > 0) {
        sortedStrikes.forEach(s => { opts += `<option value="${s}">${s}</option>`; });
    } else {
        for (let i = -10; i <= 10; i++) {
            const s = atm + (i * step);
            if (s <= 0) continue;
            opts += `<option value="${s}">${s}</option>`;
        }
    }
    const currentVal = oipElems.customStrikeDropdown.value;
    oipElems.customStrikeDropdown.innerHTML = opts;
    
    let finalStrike = atm;
    const atmStr = atm.toString();

    // Check if atm exists in current options
    const hasAtm = opts.includes(`value="${atmStr}"`);

    if (centerPrice && !oipCustomStrikeSetOnLoad) {
        if (hasAtm) {
            oipElems.customStrikeDropdown.value = atmStr;
        } else {
            // Fallback to first available if ATM not found in slice
            oipElems.customStrikeDropdown.selectedIndex = Math.floor(sortedStrikes.length / 2);
        }
        oipCustomStrikeSetOnLoad = true;
        finalStrike = parseFloat(oipElems.customStrikeDropdown.value);
    } else if (currentVal && opts.includes(`value="${currentVal}"`)) {
        oipElems.customStrikeDropdown.value = currentVal;
        finalStrike = parseFloat(currentVal);
    } else {
        if (hasAtm) oipElems.customStrikeDropdown.value = atmStr;
        else oipElems.customStrikeDropdown.selectedIndex = Math.floor(sortedStrikes.length / 2);
        finalStrike = parseFloat(oipElems.customStrikeDropdown.value);
    }
    return finalStrike;
}

function oipRenderDropdown(filter, list) {
    if (!list) return; list.innerHTML = '';
    const indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'NIFTY MIDCAP 150', 'NIFTY AUTO', 'NIFTY Smallcap 100', 'NIFTY FMCG', 'NIFTY METAL', 'NIFTY PHARMA', 'NIFTY PSU BANK', 'NIFTY IT'], dm = { 'NIFTY': 'NIFTY 50', 'BANKNIFTY': 'NIFTY BANK', 'FINNIFTY': 'NIFTY FIN SERVICE', 'MIDCPNIFTY': 'NIFTY MIDCAP', 'SENSEX': 'SENSEX', 'NIFTY MIDCAP 150': 'NIFTY MIDCAP 150', 'NIFTY AUTO': 'NIFTY AUTO', 'NIFTY Smallcap 100': 'NIFTY Smallcap 100', 'NIFTY SMLCAP 100': 'NIFTY Smallcap 100', 'NIFTY FMCG': 'NIFTY FMCG', 'NIFTY METAL': 'NIFTY METAL', 'NIFTY PHARAMA': 'NIFTY PHARMA', 'NIFTY PHARMA': 'NIFTY PHARMA', 'NIFTY PSU BANK': 'NIFTY PSU BANK', 'NIFTY IT': 'NIFTY IT' };
    const matches = oipAllSymbols.filter(s => !filter || s.includes(filter) || (dm[s] || s).toUpperCase().includes(filter))
        .sort((a, b) => { const ai = indices.indexOf(a), bi = indices.indexOf(b); if (ai !== -1 && bi !== -1) return ai - bi; if (ai !== -1) return -1; if (bi !== -1) return 1; return a.localeCompare(b); });
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

async function oipSelectSymbol(s) {
    oipSymbol = s;
    if (oipElems.symbolInput) oipElems.symbolInput.value = s;
    oipCustomStrikeSetOnLoad = false;
    oipResetReplay();
}
