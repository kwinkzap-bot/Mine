/**
 * OI Replay – Self-contained logic for Replay Mode
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
// oipCprSeriesMap, oipRSISeriesObj, oipSignalMarkers, oipRSIMarkers — defined in oi_indicators.js
// oipEma9-200Series, oipCEEma9-50Series, oipPEEma9-50Series — defined in oi_indicators.js
let oipLevelLines = [];
let oipCEChart = null;
let oipPEChart = null;
let oipCESeries = null;
let oipPESeries = null;
let oipMaxPainLine = null;

const oipPremiumSeries = { entry: null, current: null, t1: null, t2: null };

// Replay State
let oipReplayIndex = 0;
let oipReplayTimer = null;
let oipFullCandles = null;
let oipFullOptionData = null;
window.oipSelectionMode = false;
let _oipSuppressRangeSync = false;
let _oip30mLastBucket = -1; // 30m bucket index of the last candle when reversal lines were last drawn

let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

let oipAllStrikes = [];
let oipCurrentPrice = 0;
let oipAllSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'NIFTY MIDCAP 150', 'NIFTY AUTO', 'NIFTY Smallcap 100', 'NIFTY FMCG', 'NIFTY METAL', 'NIFTY PHARMA', 'NIFTY PSU BANK', 'NIFTY IT'];
let oipSymbol = 'NIFTY';
let oipLotSize = 50, oipStrikeStep = 50;
let oipInterval = '5minute';   // matches the TF select's default option
let oipMode = 'change';
let oipRafId = null;
let oipOIChartReady = false;
let oipIntChartReady = false;
let oipCEChartReady = false;
let oipPEChartReady = false;
let oipCustomStrikeSetOnLoad = false;

// DOM Cache
const oipElems = {
    symbolInput: null, symbolList: null, interval: null,
    spotHigh: null, spotLow: null, step: null, multiplier: null,
    view: null, showVwapOI: null, showVwapInt: null,
    showCpr: null, showEMA: null, showRSI: null, showOIBars: null, autoHL: null, chartWrap: null, canvas: null,
    tooltip: null, refreshIcon: null, itmCE: null, itmPE: null,
    hdrPrice: null, hdrPcr: null, hdrMaxPain: null, hdrCeOI: null,
    hdrCeChg: null, hdrPeOI: null,
    hdrPeChg: null, hdrTrend: null, hdrAtm: null, brokerSelect: null,
    showPremium: null, first5mATM: null, targetDistance: null, customStrikeCheck: null, customStrikeDropdown: null,
    strikeMode: null, ceStrikeDropdown: null, peStrikeDropdown: null,
    showEma9: null, showEma20: null, showEma50: null, showEma100: null, showEma200: null,
    exitAll: null, days: null, startDate: null, endDate: null,
    hdrLotSize: null,
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
    oipElems.showOIBars = document.getElementById('oipShowOIBars');
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
    oipElems.first5mATM = document.getElementById('oipFirst5mATM');
    oipElems.customStrikeCheck = document.getElementById('oipCustomStrikeCheck');
    oipElems.customStrikeDropdown = document.getElementById('oipCustomStrikeDropdown');
    oipElems.targetDistance = document.getElementById('oipTargetDistance');
    oipElems.showEma9 = document.getElementById('oipShowEma9');
    oipElems.showEma20 = document.getElementById('oipShowEma20');
    oipElems.showEma50 = document.getElementById('oipShowEma50');
    oipElems.showEma100 = document.getElementById('oipShowEma100');
    oipElems.showEma200 = document.getElementById('oipShowEma200');
    oipElems.strikeMode = document.getElementById('oipStrikeMode');
    oipElems.ceStrikeDropdown = document.getElementById('oipCEStrikeDropdown');
    oipElems.peStrikeDropdown = document.getElementById('oipPEStrikeDropdown');
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
            requestAnimationFrame(() => { try { targetChart.clearCrosshairPosition(); } catch(e) {} });
        } else {
            const price = targetSeries.coordinateToPrice(param.point.y);
            if (price != null) {
                requestAnimationFrame(() => { try { targetChart.setCrosshairPosition(price, param.time, targetSeries); } catch(e) {} });
            } else {
                requestAnimationFrame(() => { try { targetChart.clearCrosshairPosition(); } catch(e) {} });
            }
        }
    } catch(e) {}
}

window.oipInitSecondaryCharts = function() {
    const elInt = document.getElementById('oipIntrinsicChart');

    if (elInt && typeof TradingViewChart !== 'undefined') {
        oipIntrinsicChart = TradingViewChart.create({
            containerId: 'oipIntrinsicChart', data: [], type: 'COMBINED',
            isCombined: true, timeframe: oipInterval, options: { height: 375 }
        });
        oipIntrinsicSeries = oipIntrinsicChart.ceSeries || oipIntrinsicChart.series;
        oipIntrinsicPeSeries = oipIntrinsicChart.peSeries;
        const showV = oipElems.showVwapInt?.checked;
        oipVwapIntSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#1b9981', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null
        });
        oipVwapIntPeSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#8b5cf6', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null
        });

        // Individual CE Chart
        oipCEChart = TradingViewChart.create({
            containerId: 'oipCEChart', data: [], type: 'CE',
            timeframe: oipInterval, options: { height: 375, rightOffset: 5 }
        });
        oipCESeries = oipCEChart.series;

        // Individual PE Chart
        oipPEChart = TradingViewChart.create({
            containerId: 'oipPEChart', data: [], type: 'PE',
            timeframe: oipInterval, options: { height: 375, rightOffset: 5 }
        });
        oipPESeries = oipPEChart.series;

        const showEma9 = oipElems.showEma9?.checked ?? false;
        const showEma20 = oipElems.showEma20?.checked ?? false;
        const showEma50 = oipElems.showEma50?.checked ?? false;

        oipCEEma9Series = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma9, autoscaleInfoProvider: () => null });
        oipCEEma20Series = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma20, autoscaleInfoProvider: () => null });
        oipCEEma50Series = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma50, autoscaleInfoProvider: () => null });

        oipPEEma9Series = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma9, autoscaleInfoProvider: () => null });
        oipPEEma20Series = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma20, autoscaleInfoProvider: () => null });
        oipPEEma50Series = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma50, autoscaleInfoProvider: () => null });

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

        // Sync both barSpacing (zoom level) and scrollPosition (right-edge offset) across charts.
        // scrollPosition alone is insufficient: the same value means a different pixel offset
        // when charts have different barSpacings, causing the charts to visually drift apart.
        // setVisibleRange is intentionally avoided — it pins from/to as hard edges and
        // eliminates the rightOffset gap between the last candle and the Y-axis.
        // _oipSyncDepth prevents the target's subscribeVisibleLogicalRangeChange (fired
        // synchronously by applyOptions/scrollToPosition) from triggering a reverse sync.
        // CE/PE-only charts have rightOffset=5 vs OI/intrinsic rightOffset=20; subtract 15
        // when syncing TO them and add 15 when syncing FROM them.
        const _OIP_OPTION_RIGHT_ADJ = 15;

        let _oipSyncDepth = 0;
        // targetCharts may be plain chart instances or {chart, adj} wrapper objects.
        // Wrappers are identified by a numeric `adj` property (never present on LC instances).
        const syncRange = (sourceChart, targetCharts) => {
            if (_oipSyncDepth > 0) return;
            const ts = sourceChart.timeScale();
            const barSpacing = ts.options().barSpacing;
            const scrollPos  = ts.scrollPosition();
            if (!barSpacing) return;
            _oipSyncDepth++;
            targetCharts.forEach(item => {
                const isWrapped = item !== null && typeof item === 'object' && typeof item.adj === 'number';
                const t   = isWrapped ? item.chart : item;
                const adj = isWrapped ? item.adj   : 0;
                if (!t || typeof t.timeScale !== 'function') return;
                try {
                    t.timeScale().applyOptions({ barSpacing });
                    t.timeScale().scrollToPosition(scrollPos + adj, false);
                } catch(e) {}
            });
            _oipSyncDepth--;
        };

        if (oipOIChart && oipIntrinsicChart && oipIntrinsicChart.chart) {
            oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (_oipSuppressRangeSync || _oipSyncDepth > 0 || activeChartId !== 'index' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipOIChart, [
                    oipIntrinsicChart?.chart,
                    { chart: oipCEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ },
                    { chart: oipPEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ }
                ]);
            });
            oipIntrinsicChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (_oipSuppressRangeSync || _oipSyncDepth > 0 || activeChartId !== 'intrinsic' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipIntrinsicChart.chart, [
                    oipOIChart,
                    { chart: oipCEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ },
                    { chart: oipPEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ }
                ]);
            });
            oipCEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (_oipSuppressRangeSync || _oipSyncDepth > 0 || activeChartId !== 'ce' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipCEChart.chart, [
                    { chart: oipOIChart, adj: _OIP_OPTION_RIGHT_ADJ },
                    { chart: oipIntrinsicChart?.chart, adj: _OIP_OPTION_RIGHT_ADJ },
                    oipPEChart?.chart
                ]);
            });
            oipPEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (_oipSuppressRangeSync || _oipSyncDepth > 0 || activeChartId !== 'pe' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipPEChart.chart, [
                    { chart: oipOIChart, adj: _OIP_OPTION_RIGHT_ADJ },
                    { chart: oipIntrinsicChart?.chart, adj: _OIP_OPTION_RIGHT_ADJ },
                    oipCEChart?.chart
                ]);
            });
        }

        const syncSize = (chart, wrap) => {
            if (!chart || !wrap || !wrap.clientWidth) return;
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
                if (_oipSuppressRangeSync || activeChartId !== 'index') return;
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipOIChart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipOIChart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipOIChart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipIntrinsicChart?.chart) {
            oipIntrinsicChart.chart.subscribeCrosshairMove(param => {
                if (_oipSuppressRangeSync || activeChartId !== 'intrinsic') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipIntrinsicChart.chart, oipOIChart, param, oipOISeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipIntrinsicChart.chart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipIntrinsicChart.chart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipCEChart?.chart) {
            oipCEChart.chart.subscribeCrosshairMove(param => {
                if (_oipSuppressRangeSync || activeChartId !== 'ce') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipCEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipCEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipCEChart.chart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipPEChart?.chart) {
            oipPEChart.chart.subscribeCrosshairMove(param => {
                if (_oipSuppressRangeSync || activeChartId !== 'pe') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipPEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipPEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipPEChart.chart, oipCEChart.chart, param, oipCESeries);
            });
        }
    }
};

/* ── Logic Functions ───────────────────────────────────────── */
// oipUpdateEmaVisibility — defined in oi_indicators.js

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
            // Height comes from the wrapper (.oip-chart-wrap), which the Replay tab
            // sizes to the viewport — a hardcoded 375 left dead space inside the
            // taller block and had to be edited in two places to change.
            width: elOI.clientWidth || 1200, height: elOI.clientHeight || 575,
            layout: { textColor: '#374151', background: { type: 'solid', color: '#ffffff' } },
            grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
            crosshair: { mode: 0, vertLine: { color: '#9ca3af', style: 3 }, horzLine: { color: '#9ca3af', style: 3, labelBackgroundColor: '#0969da' } },
            timeScale: { timeVisible: true, textColor: '#6b7280', borderColor: 'transparent', rightOffset: 20, barSpacing: 8, fixLeftEdge: false, fixRightEdge: false, shiftVisibleRangeOnNewBar: false },
            rightPriceScale: { textColor: '#64748b', borderColor: 'transparent', width: 85, autoScale: true, visible: true, scaleMargins: { top: 0, bottom: 0 }, entireTextOnly: true },
            handleScroll: true, handleScale: true,
            localization: { locale: 'en-IN', timeFormatter: t => { const d = new Date(t * 1000); return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`; }, timezone: 'Etc/UTC' }
        });

        oipOISeries = oipOIChart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: '#1b9981', downColor: '#f23645', borderUpColor: '#1b9981', borderDownColor: '#f23645', wickUpColor: '#1b9981', wickDownColor: '#f23645',
            autoscaleInfoProvider: customAutoscale
        });
        lwBringToFront(oipOISeries);
        // crosshairMarkerVisible:false on every overlay line — LightweightCharts
        // otherwise parks a filled dot on the line under the crosshair.
        oipVwapSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#f59e0b', lineWidth: 2, visible: oipElems.showVwapOI?.checked ?? false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma9Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#22c55e', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma20Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#f97316', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma50Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma100Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#3b82f6', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma200Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#000000', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });

        oipOIChartReady = true;
        
        // Add Scroll to Latest button (Right Arrow)
        if (typeof TradingViewChart !== 'undefined' && TradingViewChart.addScrollButton) {
            TradingViewChart.addScrollButton(oipOIChart, oipOISeries, elOI);
        }

        oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(() => oipRequestDraw());
        oipOIChart.timeScale().subscribeVisibleTimeRangeChange(() => oipRequestDraw());
        if (wrapOI) new ResizeObserver(() => {
            // Both dimensions: the observer used to track width only, so a taller
            // wrapper just added blank space under the chart.
            if (wrapOI.clientWidth > 0) oipOIChart.applyOptions({ width: wrapOI.clientWidth, height: wrapOI.clientHeight });
            oipRequestDraw();
        }).observe(wrapOI);
    }
    if (window.oipInitSecondaryCharts) window.oipInitSecondaryCharts();
}

// 1W and 1M draw YEARLY pivots (see oipAnchorPeriod in oi_indicators.js), which
// need a completed prior calendar year inside the loaded range — the default
// From date is 1 Jan of THIS year, so those two timeframes would come up with
// no CPR at all. Widen the range for them, but never over a From date the user
// picked themselves.
let oipStartDateTouched = false;

function oipEnsureRangeForAnchor() {
    if (oipStartDateTouched || !oipElems.startDate) return;
    const anchor = typeof oipAnchorPeriod === 'function' ? oipAnchorPeriod(oipInterval) : 'day';
    const now = new Date();
    const firstYear = anchor === 'year' ? now.getFullYear() - 1 : now.getFullYear();
    const want = new Date(Date.UTC(firstYear, 0, 1)).toISOString().split('T')[0];
    if (oipElems.startDate.value !== want) oipElems.startDate.value = want;
}

// The Bar Replay button doubles as the mode prompt ("Select Bar On Chart"),
// so its text is set from JS rather than baked into the template.
function oipSetToggleLabel(text) {
    const btn = document.getElementById('oipToggleReplayToolbar');
    if (!btn) return;
    const node = [...btn.childNodes].reverse().find(n => n.nodeType === 3 && n.textContent.trim());
    if (node) node.textContent = ' ' + text;
    btn.title = text === 'Bar Replay' ? 'Toggle Bar Replay' : 'Select a bar on the chart to replay from';
}

// "Replay Mode (5m)" — the pill names the timeframe being replayed.
function oipUpdateReplayTfLabel() {
    const el = document.getElementById('oipReplayTfLabel');
    if (!el) return;
    const sel = oipElems.interval;
    const label = sel?.options?.[sel.selectedIndex]?.textContent?.trim();
    el.textContent = label ? `(${label})` : '';
}

// The index chart's VWAP toggle. oipUpdateEmaVisibility (oi_indicators.js)
// covers the EMAs for both pages; VWAP is wired differently on OI Profile, so
// Replay's own switch lives here — it was previously unwired altogether and the
// series stayed hidden whatever the checkbox said.
function oipUpdateVwapVisibility() {
    if (oipVwapSeries) oipVwapSeries.applyOptions({ visible: oipElems.showVwapOI?.checked ?? false });
}

// oipFullCandles.slice(0, i+1) on every replay step copied the whole loaded
// history (a year of 5m bars is ~18k entries) just to hand the box/reversal
// drawers a prefix. The prefix is rebuilt only when the playhead moves
// anywhere other than one bar forward.
let _oipVisCache = null, _oipVisCacheIdx = -1;

function oipVisibleCandles(index) {
    if (!oipFullCandles) return [];
    if (_oipVisCache && _oipVisCacheIdx === index) return _oipVisCache;
    if (_oipVisCache && _oipVisCacheIdx === index - 1 && _oipVisCache.length === index) {
        _oipVisCache.push(oipFullCandles[index]);
        _oipVisCacheIdx = index;
        return _oipVisCache;
    }
    _oipVisCache = oipFullCandles.slice(0, index + 1);
    _oipVisCacheIdx = index;
    return _oipVisCache;
}

function oipInvalidateVisCache() { _oipVisCache = null; _oipVisCacheIdx = -1; }

function oipRequestDraw() { if (!oipRafId) oipRafId = requestAnimationFrame(oipDrawOIBars); }

function oipDrawOIBars() {
    oipRafId = null;
    const canvas = oipElems.canvas; const wrap = oipElems.chartWrap;
    if (!canvas || !wrap || !oipOISeries || !oipAllStrikes.length) return;
    const ctx = canvas.getContext('2d');
    const W = wrap.clientWidth, H = wrap.clientHeight, dpr = window.devicePixelRatio || 1;
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = W + 'px'; canvas.style.height = H + 'px'; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    if (!(oipElems.showOIBars?.checked ?? true)) return;

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

// oipCalculateVWAP, oipCalculateFixedEMA, oipCalculateDynamicCPR, oipDrawCpr,
// oipCalculateRSISnR, oipDrawRSI, oipUpdateAllMarkers — defined in oi_indicators.js

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
    _oip30mLastBucket = -1; // force redraw on fresh load
    const btnPause = document.getElementById('oipReplayPause');
    if (btnPause && btnPause.style.display !== 'none') btnPause.click(); // Stop active replay
    await oipLoadMetadata();
    const h = parseFloat(oipElems.spotHigh?.value || 0), l = parseFloat(oipElems.spotLow?.value || 0);
    const s = parseInt(oipElems.step?.value || 50), m = parseInt(oipElems.multiplier?.value || 3);

    const strikeMode = oipElems.strikeMode?.value || 'ce_pe';
    const first5m = strikeMode === 'atm';
    const customStrike = strikeMode === 'custom' ? (oipElems.customStrikeDropdown?.value || '') : '';
    const ceStrike = strikeMode === 'ce_pe' ? (oipElems.ceStrikeDropdown?.value || '') : '';
    const peStrike = strikeMode === 'ce_pe' ? (oipElems.peStrikeDropdown?.value || '') : '';

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

    const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=true&first_5m_atm=${first5m}&custom_strike=${customStrike}&ce_strike=${ceStrike}&pe_strike=${peStrike}${dateRangeParams}&_t=${Date.now()}`;
    const res = await fetch(url); const data = await res.json();
    if (!data.success) {
        console.error('[Replay] API error:', data.error || 'Unknown error');
        if (typeof showNotification === 'function') showNotification(data.error || 'Failed to load candle data. Check broker login.', 'error');
        return;
    }
    if (!data.candles || !data.candles.length) {
        const reason = data.fetch_error ? `Broker error: ${data.fetch_error}` : 'No candle data for the selected date range. Check broker login.';
        if (typeof showNotification === 'function') showNotification(reason, 'error');
        return;
    }

    // --- RESET STATE FOR NEW LOAD ---
    oipLastRefreshIndex = -1;
    oipFullCeData = [];
    oipFullPeData = [];
    oipCachedIndicators = {
        index: { vwap: [], ema9: [], ema20: [], ema50: [], ema100: [], ema200: [], rsi: null },
        ce: { vwap: [], ema9: [], ema20: [], ema50: [] },
        pe: { vwap: [], ema9: [], ema20: [], ema50: [] },
        premium: { entry: [], current: [], t1: [], t2: [] },
        cpr: null
    };
    
    // Remove ALL Baseline series before any setData triggers a render — LC renders every
    // attached series on each setData call, and uninitialized/stale Baseline renderers
    // will throw "Value is null".
    Object.values(oipCprSeriesMap).forEach(s => { try { oipOIChart.removeSeries(s); } catch(e) {} });
    oipCprSeriesMap = {};
    _oipPrecalcDone = false;
    oipInvalidateVisCache();
    _oipDayBoxesClearAll();
    oip2ndCandle30sBox = { oi: [], ce: [], pe: [] };
    oip2nd5mCandleBox  = { oi: [], ce: [], pe: [] };
    oipClear30mReversalLines();
    oipClear1DReversalLines();

    // Clear all chart series
    try { if (oipOISeries) oipOISeries.setData([]); } catch(e) {}
    try { if (oipVwapSeries) oipVwapSeries.setData([]); } catch(e) {}
    [oipEma9Series, oipEma20Series, oipEma50Series, oipEma100Series, oipEma200Series].forEach(s => { try { s?.setData([]); } catch(e) {} });

    // Clear secondary chart series synchronously — all Baseline series were removed above,
    // so rendering these empty candlestick series is safe. RAF-deferral caused a race where
    // fast fetches resulted in real data being overwritten by a delayed setData([]).
    try { if (oipVwapIntSeries) oipVwapIntSeries.setData([]); } catch(e) {}
    try { if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]); } catch(e) {}
    if (oipIntrinsicChart) {
        try { oipIntrinsicChart.series?.setData([]); } catch(e) {}
        try { oipIntrinsicChart.peSeries?.setData([]); } catch(e) {}
    }
    if (oipCEChart) { try { oipCEChart.series?.setData([]); } catch(e) {} }
    if (oipPEChart) { try { oipPEChart.series?.setData([]); } catch(e) {} }
    // --------------------------------

    if (data.strikes) {
        oipAllStrikes = data.strikes;
        oipUpdateCustomStrikeOptions(data.strikes, data.current_price);
    }

    oipOIData = data;
    oipFullCandles = (data.candles || []).filter(
        c => c.open != null && c.high != null && c.low != null && c.close != null
    );
    
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
    
    oipOptionData = [
        ...ceRaw.map(c => ({...c, type:'CE'})),
        ...peRaw.map(c => ({...c, type:'PE'}))
    ];
    oip30sSecondCandle.oi = data.second_30s_candle_oi || [];
    oip30sSecondCandle.ce = data.second_30s_candle_ce || [];
    oip30sSecondCandle.pe = data.second_30s_candle_pe || [];
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

    if (oipOIChartReady) {
        oipOIChart.timeScale().fitContent();

        setTimeout(() => {
            if (oipOIChartReady && oipIntChartReady) {
                const ts = oipOIChart.timeScale();
                const barSpacing = ts.options().barSpacing;
                const scrollPos  = ts.scrollPosition();
                if (!barSpacing) return;
                const optAdj = 15;
                [
                    { chart: oipIntrinsicChart?.chart, adj: 0 },
                    { chart: oipCEChart?.chart,        adj: -optAdj },
                    { chart: oipPEChart?.chart,        adj: -optAdj }
                ].forEach(({ chart: c, adj }) => {
                    if (!c) return;
                    try {
                        c.timeScale().applyOptions({ barSpacing });
                        c.timeScale().scrollToPosition(scrollPos + adj, false);
                    } catch(e) {}
                });
            }
        }, 100);
    }
}

let _oipPrecalcDone = false;
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
    _oipPrecalcDone = true;

    // The dashboard's Replay tab has no Option Premium charts, so none of the
    // CE/PE work below is ever drawn. Skipping it saves splitting and running
    // VWAP/EMA over two more full-length series on every load.
    const wantOptions = !!(oipIntrinsicChart || oipCEChart || oipPEChart);
    oipFullCeData = wantOptions ? oipFullOptionData.filter(d => d.type === 'CE') : [];
    oipFullPeData = wantOptions ? oipFullOptionData.filter(d => d.type === 'PE') : [];

    oipCachedIndicators.index.vwap = oipCalculateVWAP(oipFullCandles);
    oipCachedIndicators.index.ema9 = oipCalculateFixedEMA(oipFullCandles, 9);
    oipCachedIndicators.index.ema20 = oipCalculateFixedEMA(oipFullCandles, 20);
    oipCachedIndicators.index.ema50 = oipCalculateFixedEMA(oipFullCandles, 50);
    oipCachedIndicators.index.ema100 = oipCalculateFixedEMA(oipFullCandles, 100);
    oipCachedIndicators.index.ema200 = oipCalculateFixedEMA(oipFullCandles, 200);
    oipCachedIndicators.index.rsi = oipCalculateRSISnR(oipFullCandles);
    oipCachedIndicators.cpr = oipCalculateDynamicCPR(oipFullCandles);

    if (!wantOptions) return;

    // Strip master-timeline whitespace bars (close=undefined) before computing
    // EMAs/VWAP — a whitespace first-bar seeds prevEma=NaN, making all values NaN.
    const ceReal = oipFullCeData.filter(d => d.close != null && isFinite(d.close));
    const peReal = oipFullPeData.filter(d => d.close != null && isFinite(d.close));

    oipCachedIndicators.ce.vwap = oipCalculateVWAP(ceReal);
    oipCachedIndicators.ce.ema9 = oipCalculateFixedEMA(ceReal, 9);
    oipCachedIndicators.ce.ema20 = oipCalculateFixedEMA(ceReal, 20);
    oipCachedIndicators.ce.ema50 = oipCalculateFixedEMA(ceReal, 50);

    oipCachedIndicators.pe.vwap = oipCalculateVWAP(peReal);
    oipCachedIndicators.pe.ema9 = oipCalculateFixedEMA(peReal, 9);
    oipCachedIndicators.pe.ema20 = oipCalculateFixedEMA(peReal, 20);
    oipCachedIndicators.pe.ema50 = oipCalculateFixedEMA(peReal, 50);

    // Premium Lines Pre-calc
    const dist = parseInt(oipElems.targetDistance?.value) || 50;
    const entry = [], current = [];
    oipCachedIndicators.ce.vwap.forEach((v, i) => {
        const pv = oipCachedIndicators.pe.vwap[i];
        if (pv && isFinite(v.value) && isFinite(pv.value))
            entry.push({ time: v.time, value: (v.value + pv.value) / 2 });
    });
    oipFullCeData.forEach((v, i) => {
        const pv = oipFullPeData[i];
        if (pv && isFinite(v.close) && isFinite(pv.close))
            current.push({ time: v.time, value: (v.close + pv.close) / 2 });
    });
    oipCachedIndicators.premium.entry = entry;
    oipCachedIndicators.premium.current = current;
    oipCachedIndicators.premium.t1 = entry.map(v => ({ time: v.time, value: v.value + dist }));
    oipCachedIndicators.premium.t2 = entry.map(v => ({ time: v.time, value: v.value + (2 * dist) }));
}

let oipLastRefreshIndex = -2; // -2 so first replay call (index=0) is always non-incremental

function oipRefreshLocalView(view, resetZoom, index) {
    if (!oipFullCandles || index < 0) return;

    // Suppress cross-chart range sync for the entire refresh. series.update() and
    // series.setData() fire subscribeVisibleLogicalRangeChange synchronously; without
    // this guard, the active chart's callback runs syncRange mid-update and calls
    // applyOptions on the OI chart while it still has a partially-rendered series →
    // LC Candlestick renderer crashes with "Value is null".
    _oipSuppressRangeSync = true;

    if (!_oipPrecalcDone) oipPrecalculateIndicators();

    const isIncremental = index === oipLastRefreshIndex + 1;
    oipLastRefreshIndex = index;

    const updateOrSet = (series, fullData, idx) => {
        if (!series || !fullData) return;
        if (isIncremental) { if (fullData[idx] != null) series.update(fullData[idx]); }
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
    
    oipSignalMarkers = [];
    if (oipOISeries) lwSetMarkers(oipOISeries, []);

    // CPR Redraw — the renderer clips to timeAtIdx itself.
    if (oipCachedIndicators.cpr) oipRenderPrecalculatedCPR(oipCachedIndicators.cpr, timeAtIdx);

    // 3. Option Charts
    const hasOHLC = (d) => d && d.open != null;
    // For incremental steps, advance each chart's invisible alignment series to the
    // current OI timestamp so scrollToPosition sync lands at the same clock time.
    const oiAlignTime = isIncremental ? (oipFullCandles[index]?.time ?? null) : null;

    if (oipIntrinsicChart) {
        if (isIncremental) {
            const ceFormatted = TradingViewChart.formatData([oipFullCeData[index]])[0];
            const peFormatted = TradingViewChart.formatData([oipFullPeData[index]])[0];
            if (hasOHLC(ceFormatted)) oipIntrinsicChart.series.update(ceFormatted);
            if (hasOHLC(peFormatted)) oipIntrinsicChart.peSeries.update(peFormatted);
            if (oiAlignTime) try { oipIntrinsicChart.alignSeries?.update({ time: oiAlignTime, value: 0 }); } catch(e) {}
        } else {
            oipIntrinsicChart.update(oipFullCeData.slice(0, index + 1), oipFullPeData.slice(0, index + 1), false);
        }
        updateOrSet(oipVwapIntSeries, oipCachedIndicators.ce.vwap, index);
        updateOrSet(oipVwapIntPeSeries, oipCachedIndicators.pe.vwap, index);
    }

    if (oipCEChart) {
        if (isIncremental) {
            const formatted = TradingViewChart.formatData([oipFullCeData[index]])[0];
            if (hasOHLC(formatted)) oipCEChart.series.update(formatted);
            if (oiAlignTime) try { oipCEChart.alignSeries?.update({ time: oiAlignTime, value: 0 }); } catch(e) {}
            // Incremental: push only the new EMA point for this timestamp (avoids full setData redraw)
            const ce9  = oipCachedIndicators.ce.ema9.find(d => d.time === timeAtIdx);
            const ce20 = oipCachedIndicators.ce.ema20.find(d => d.time === timeAtIdx);
            const ce50 = oipCachedIndicators.ce.ema50.find(d => d.time === timeAtIdx);
            if (oipCEEma9Series  && ce9)  oipCEEma9Series.update(ce9);
            if (oipCEEma20Series && ce20) oipCEEma20Series.update(ce20);
            if (oipCEEma50Series && ce50) oipCEEma50Series.update(ce50);
        } else {
            oipCEChart.update(oipFullCeData.slice(0, index + 1), [], false);
            const ceEmaT = d => d.time <= timeAtIdx;
            if (oipCEEma9Series) oipCEEma9Series.setData(oipCachedIndicators.ce.ema9.filter(ceEmaT));
            if (oipCEEma20Series) oipCEEma20Series.setData(oipCachedIndicators.ce.ema20.filter(ceEmaT));
            if (oipCEEma50Series) oipCEEma50Series.setData(oipCachedIndicators.ce.ema50.filter(ceEmaT));
        }
    }

    if (oipPEChart) {
        if (isIncremental) {
            const formatted = TradingViewChart.formatData([oipFullPeData[index]])[0];
            if (hasOHLC(formatted)) oipPEChart.series.update(formatted);
            if (oiAlignTime) try { oipPEChart.alignSeries?.update({ time: oiAlignTime, value: 0 }); } catch(e) {}
            // Incremental: push only the new EMA point for this timestamp
            const pe9  = oipCachedIndicators.pe.ema9.find(d => d.time === timeAtIdx);
            const pe20 = oipCachedIndicators.pe.ema20.find(d => d.time === timeAtIdx);
            const pe50 = oipCachedIndicators.pe.ema50.find(d => d.time === timeAtIdx);
            if (oipPEEma9Series  && pe9)  oipPEEma9Series.update(pe9);
            if (oipPEEma20Series && pe20) oipPEEma20Series.update(pe20);
            if (oipPEEma50Series && pe50) oipPEEma50Series.update(pe50);
        } else {
            oipPEChart.update(oipFullPeData.slice(0, index + 1), [], false);
            const peEmaT = d => d.time <= timeAtIdx;
            if (oipPEEma9Series) oipPEEma9Series.setData(oipCachedIndicators.pe.ema9.filter(peEmaT));
            if (oipPEEma20Series) oipPEEma20Series.setData(oipCachedIndicators.pe.ema20.filter(peEmaT));
            if (oipPEEma50Series) oipPEEma50Series.setData(oipCachedIndicators.pe.ema50.filter(peEmaT));
        }
    }

    // 4. Legend & Metadata
    const isCePeMode = oipElems.strikeMode?.value === 'ce_pe';
    const customStrikeVal = oipElems.customStrikeDropdown?.value || '--';
    const ceStrikeVal = isCePeMode ? (oipElems.ceStrikeDropdown?.value || '--') : customStrikeVal;
    const peStrikeVal = isCePeMode ? (oipElems.peStrikeDropdown?.value || '--') : customStrikeVal;

    if (oipElems.itmCE) oipElems.itmCE.textContent = `${ceStrikeVal} CE`;
    if (oipElems.itmPE) oipElems.itmPE.textContent = `${peStrikeVal} PE`;
    if (document.getElementById('oipLegendCEOnly')) document.getElementById('oipLegendCEOnly').textContent = `${ceStrikeVal} CE`;
    if (document.getElementById('oipLegendPEOnly')) document.getElementById('oipLegendPEOnly').textContent = `${peStrikeVal} PE`;

    oipUpdateEmaVisibility();

    // Restore missing drawing calls
    if (oipOIData?.intrinsic) oipDrawIntrinsicLines(oipOIData.intrinsic, view);
    oipDrawPremiumLines(view, index);
    oipUpdateHeader(oipOIData);
    oipRequestDraw();

    // Defer box/reversal indicator redraws to the next animation frame so they don't
    // interfere with the render RAF already queued by series.update() calls above.
    // Reversal line functions previously added future timestamps that expanded the time
    // scale and caused "Value is null" crashes — that is now prevented at the source
    // (oipDraw30mReversalLines / oipDraw1DReversalLines skip future timestamps in replay
    // mode), so no save/restore of the visible range is required here.
    const _idxSnap = index;
    requestAnimationFrame(() => {
        if (!oipFullCandles || _idxSnap >= oipFullCandles.length) {
            requestAnimationFrame(() => { _oipSuppressRangeSync = false; });
            return;
        }
        const _vis  = oipVisibleCandles(_idxSnap);
        // 30m reversal lines: always repaint (extend to current candle) but only detect
        // new signals when a 30m candle closes (bucket changes).
        const _tCur  = oipFullCandles[_idxSnap]?.time ?? 0;
        oipDraw2ndCandle30sBox(_vis, _tCur);
        oipDraw2nd5mCandleBox(_vis, _tCur);
        const _tPrev = _idxSnap > 0 ? (oipFullCandles[_idxSnap - 1]?.time ?? _tCur) : _tCur;
        const _cur30mBucket = Math.floor(_tCur / 1800);
        const _recompute30m = _oip30mLastBucket < 0 || _cur30mBucket !== Math.floor(_tPrev / 1800);
        if (_recompute30m) _oip30mLastBucket = _cur30mBucket;
        oipDraw30mReversalLines(_vis, _recompute30m);
        oipDraw1DReversalLines(_vis);
        // Defer the suppress reset by one extra RAF so it runs AFTER the inner RAFs
        // that oipDraw2ndCandle30sBox and oipDraw2nd5mCandleBox schedule for CE/PE
        // series creation. RAF callbacks run FIFO; the inner RAFs were queued first,
        // so they execute before this reset fires.
        requestAnimationFrame(() => { _oipSuppressRangeSync = false; });
    });

    if (resetZoom && oipOIChartReady) {
        oipOIChart.timeScale().fitContent();

        if (oipIntChartReady) {
            const ts = oipOIChart.timeScale();
            const barSpacing = ts.options().barSpacing;
            const scrollPos  = ts.scrollPosition();
            if (barSpacing) {
                const optAdj = 15;
                [
                    { chart: oipIntrinsicChart?.chart, adj: 0 },
                    { chart: oipCEChart?.chart,        adj: -optAdj },
                    { chart: oipPEChart?.chart,        adj: -optAdj }
                ].forEach(({ chart: c, adj }) => {
                    if (!c) return;
                    try {
                        c.timeScale().applyOptions({ barSpacing });
                        c.timeScale().scrollToPosition(scrollPos + adj, false);
                    } catch(e) {}
                });
            }
        }
    }
}

// Replay's CPR step. The heavy lifting lives in oi_indicators.js so the static
// chart and the replay share one renderer — this used to be a near-copy that
// rebuilt every period's series on every step (and drew them in its own
// hardcoded colours, ignoring the per-line settings).
function oipRenderPrecalculatedCPR(daysData, maxTime) {
    if (!oipOIChart || !oipElems.showCpr?.checked) return;
    if (!oipAdvanceCprLevels(daysData, maxTime)) oipRenderCprLevels(daysData, maxTime);
}

/* ── Replay Core ───────────────────────────────────────────── */
function oipResetReplay() { oipFullCandles = null; oipFullOptionData = null; oipReplayIndex = 0; oipLoadCandles(); }

async function oipReloadStrikeOnly() {
    const toolbar = document.getElementById('oipReplayToolbar');
    const replayActive = toolbar && !toolbar.classList.contains('hidden') && oipReplayIndex > 0 && oipFullCandles?.length;
    if (!replayActive) { oipResetReplay(); return; }

    const h = parseFloat(oipElems.spotHigh?.value || 0), l = parseFloat(oipElems.spotLow?.value || 0);
    const s = parseInt(oipElems.step?.value || 50), m = parseInt(oipElems.multiplier?.value || 3);
    const strikeMode = oipElems.strikeMode?.value || 'ce_pe';
    const first5m = strikeMode === 'atm';
    const customStrike = strikeMode === 'custom' ? (oipElems.customStrikeDropdown?.value || '') : '';
    const ceStrike = strikeMode === 'ce_pe' ? (oipElems.ceStrikeDropdown?.value || '') : '';
    const peStrike = strikeMode === 'ce_pe' ? (oipElems.peStrikeDropdown?.value || '') : '';
    let days = parseInt(oipElems.days?.value) || 5;
    let dateRangeParams = '';
    if (oipElems.startDate?.value && oipElems.endDate?.value) {
        dateRangeParams = `&start_date=${oipElems.startDate.value}&end_date=${oipElems.endDate.value}`;
        const diffDays = Math.ceil((new Date(oipElems.endDate.value) - new Date(oipElems.startDate.value)) / 86400000) + 1;
        if (diffDays > 0) days = diffDays;
    }
    const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=true&first_5m_atm=${first5m}&custom_strike=${customStrike}&ce_strike=${ceStrike}&pe_strike=${peStrike}${dateRangeParams}&_t=${Date.now()}`;

    try {
        const res = await fetch(url); const data = await res.json();
        if (!data.success) return;

        const alignToIndices = (optCandles) => {
            const optMap = new Map(optCandles.map(c => [c.time, c]));
            return oipFullCandles.map(ic => optMap.get(ic.time) || { time: ic.time });
        };
        const ceRaw = data.ce_opt_candles || [], peRaw = data.pe_opt_candles || [];
        oipFullOptionData = [
            ...alignToIndices(ceRaw).map(c => ({...c, type:'CE'})),
            ...alignToIndices(peRaw).map(c => ({...c, type:'PE'}))
        ];

        oipFullCeData = oipFullOptionData.filter(d => d.type === 'CE');
        oipFullPeData = oipFullOptionData.filter(d => d.type === 'PE');
        const ceReal2 = oipFullCeData.filter(d => d.close != null && isFinite(d.close));
        const peReal2 = oipFullPeData.filter(d => d.close != null && isFinite(d.close));
        oipCachedIndicators.ce.vwap = oipCalculateVWAP(ceReal2);
        oipCachedIndicators.ce.ema9  = oipCalculateFixedEMA(ceReal2, 9);
        oipCachedIndicators.ce.ema20 = oipCalculateFixedEMA(ceReal2, 20);
        oipCachedIndicators.ce.ema50 = oipCalculateFixedEMA(ceReal2, 50);
        oipCachedIndicators.pe.vwap  = oipCalculateVWAP(peReal2);
        oipCachedIndicators.pe.ema9  = oipCalculateFixedEMA(peReal2, 9);
        oipCachedIndicators.pe.ema20 = oipCalculateFixedEMA(peReal2, 20);
        oipCachedIndicators.pe.ema50 = oipCalculateFixedEMA(peReal2, 50);
        const dist = parseInt(oipElems.targetDistance?.value) || 50;
        const entry = [], current = [];
        oipCachedIndicators.ce.vwap.forEach((v, i) => { const pv = oipCachedIndicators.pe.vwap[i]; if (pv && isFinite(v.value) && isFinite(pv.value)) entry.push({ time: v.time, value: (v.value + pv.value) / 2 }); });
        oipFullCeData.forEach((v, i) => { const pv = oipFullPeData[i]; if (pv && isFinite(v.close) && isFinite(pv.close)) current.push({ time: v.time, value: (v.close + pv.close) / 2 }); });
        oipCachedIndicators.premium = { entry, current, t1: entry.map(v => ({time: v.time, value: v.value + dist})), t2: entry.map(v => ({time: v.time, value: v.value + dist * 2})) };

        if (oipIntrinsicChart) { oipIntrinsicChart.series.setData([]); oipIntrinsicChart.peSeries.setData([]); }
        if (oipCEChart) oipCEChart.series.setData([]);
        if (oipPEChart) oipPEChart.series.setData([]);
        oipLastRefreshIndex = -1;
        oipRefreshLocalView('combined', false, oipReplayIndex);
    } catch(e) { console.error('[OIP] Strike reload failed:', e); }
}

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
        _oip30mLastBucket = -1; // position jumped — force reversal redraw
        update();
    };

    speed.onchange = () => {
        if (btnPause.style.display === 'inline-block') startTimer();
    };

    if (btnJump) {
        btnJump.onclick = () => {
            window.oipSelectionMode = !window.oipSelectionMode;
            btnJump.classList.toggle('oip-btn--armed', window.oipSelectionMode);
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
                btnJump.classList.remove('oip-btn--armed');
                oipOIChart.applyOptions({ handleScroll: true, handleScale: true });
                // A bar was picked — this is where the control pill appears.
                window.oipReplayShowControls?.();
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
    oipPremiumSeries.entry = chart.addSeries(LightweightCharts.LineSeries, { ...base, color: '#4caf50', lineWidth: 2 });
    oipPremiumSeries.current = chart.addSeries(LightweightCharts.LineSeries, { ...base, color: '#2196f3', lineWidth: 2 });
    oipPremiumSeries.t1 = chart.addSeries(LightweightCharts.LineSeries, { ...base, color: '#e040fb', lineWidth: 1 });
    oipPremiumSeries.t2 = chart.addSeries(LightweightCharts.LineSeries, { ...base, color: '#f97316', lineWidth: 1 });
}

/* ── Bootstrap ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    window.oipReplayMode = true;
    oipInitElems();
    oipInitIndicatorsPopup('oip-ind-replay-v3');
    // Replay opens on the year to date — 1 January of the current year through
    // today (it used to be a rolling 30 days). The range drives the API's `days`
    // parameter, so this is a much bigger candle fetch than a month was.
    const now = new Date();
    const today = now.toISOString().split('T')[0];
    const yearStart = new Date(Date.UTC(now.getFullYear(), 0, 1));
    if (oipElems.startDate) oipElems.startDate.value = yearStart.toISOString().split('T')[0];
    if (oipElems.endDate) oipElems.endDate.value = today;
    
    oipElems.startDate?.addEventListener('change', () => { oipStartDateTouched = true; oipResetReplay(); });
    oipElems.showOIBars?.addEventListener('change', () => oipRequestDraw());

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
        oipUpdateReplayTfLabel();
        oipEnsureRangeForAnchor();
        oipResetReplay();
    });
    oipElems.days?.addEventListener('change', () => oipResetReplay());
    oipElems.targetDistance?.addEventListener('change', () => {
        if (oipFullCandles) oipRefreshLocalView('combined', false, oipReplayIndex);
    });

    // Strike mode dropdown — controls which strike input is visible
    function oipApplyStrikeMode(mode) {
        const isAtm    = mode === 'atm';
        const isCustom = mode === 'custom';
        const isCePe   = mode === 'ce_pe';

        if (oipElems.first5mATM) oipElems.first5mATM.checked = isAtm;
        if (oipElems.customStrikeCheck) oipElems.customStrikeCheck.checked = isCustom;

        if (oipElems.customStrikeDropdown) oipElems.customStrikeDropdown.style.display = isCustom ? '' : 'none';
        if (oipElems.ceStrikeDropdown) oipElems.ceStrikeDropdown.style.display = isCePe ? '' : 'none';
        if (oipElems.peStrikeDropdown) oipElems.peStrikeDropdown.style.display = isCePe ? '' : 'none';
    }

    document.querySelectorAll('input[name="oipMode"]').forEach(radio => {
        radio.addEventListener('change', e => {
            oipMode = e.target.value;
            oipRequestDraw();
        });
    });

    oipApplyStrikeMode(oipElems.strikeMode?.value || 'ce_pe');

    oipElems.strikeMode?.addEventListener('change', () => {
        oipApplyStrikeMode(oipElems.strikeMode.value);
        oipReloadStrikeOnly();
    });
    oipElems.customStrikeDropdown?.addEventListener('change', () => {
        if (oipElems.strikeMode?.value === 'custom') oipReloadStrikeOnly();
    });
    oipElems.ceStrikeDropdown?.addEventListener('change', () => {
        if (oipElems.strikeMode?.value === 'ce_pe') oipReloadStrikeOnly();
    });
    oipElems.peStrikeDropdown?.addEventListener('change', () => {
        if (oipElems.strikeMode?.value === 'ce_pe') oipReloadStrikeOnly();
    });

    [
        'oipShowEma9', 'oipShowEma20', 'oipShowEma50', 'oipShowEma100', 'oipShowEma200',
        'oipShowCpr', 'oipShowRSI',
        'oipShowVwapOI', 'oipShowVwapInt', 'oipShowPremium',
        'oipCprShowPrevHL', 'oipCprShowBand', 'oipCprShowResistance', 'oipCprShowSupport', 'oipCprShowCumR3S3',
        'oipCprShowLabels'
    ].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            oipUpdateEmaVisibility();
            oipUpdateVwapVisibility();
            if (oipFullCandles) oipRefreshLocalView('combined', false, oipReplayIndex);
        });
    });

    document.getElementById('oipShow2ndCandle30s')?.addEventListener('change', () => {
        if (!oipFullCandles) return;
        oipDraw2ndCandle30sBox(oipVisibleCandles(oipReplayIndex), oipFullCandles[oipReplayIndex]?.time ?? 0);
    });
    document.getElementById('oipShow2nd5mCandle')?.addEventListener('change', () => {
        if (!oipFullCandles) return;
        // Second argument is the playhead TIME (it used to be handed the CE/PE
        // arrays, so the CE/PE boxes silently never drew from this path).
        oipDraw2nd5mCandleBox(oipVisibleCandles(oipReplayIndex), oipFullCandles[oipReplayIndex]?.time ?? 0);
    });
    document.getElementById('oipShow30mReversalLines')?.addEventListener('change', () => {
        if (oipFullCandles) oipDraw30mReversalLines(oipFullCandles.slice(0, oipReplayIndex + 1));
    });
    ['oipReversal30mCountUp', 'oipReversal30mCountDn', 'oipReversal30mRange'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (oipFullCandles) oipDraw30mReversalLines(oipFullCandles.slice(0, oipReplayIndex + 1));
        });
    });
    document.getElementById('oipShow1DReversalLines')?.addEventListener('change', () => {
        if (oipFullCandles) oipDraw1DReversalLines(oipFullCandles.slice(0, oipReplayIndex + 1));
    });
    ['oipReversal1DCount', 'oipReversal1DRange'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (oipFullCandles) oipDraw1DReversalLines(oipFullCandles.slice(0, oipReplayIndex + 1));
        });
    });

    // Toggle Replay Toolbar visibility
    /* Replay mode, in the shape stockmojo's historical chart uses:
       "Bar Replay" arms bar selection and the button itself says so; the
       control pill only appears once a bar has been picked; and the pill
       carries its own Exit. */
    const btnToggleReplay = document.getElementById('oipToggleReplayToolbar');
    const replayToolbar = document.getElementById('oipReplayToolbar');
    const btnReplayExit = document.getElementById('oipReplayExit');

    function oipReplayArm() {
        window.oipSelectionMode = true;
        btnToggleReplay?.classList.add('oip-btn--armed');
        oipSetToggleLabel('Select Bar On Chart');
        document.getElementById('oipJumpReplay')?.classList.add('oip-btn--armed');
        try { oipOIChart.applyOptions({ handleScroll: false, handleScale: false }); } catch (e) {}
        if (window.notify) notify.info('Click a bar on the chart to start Bar Replay');
    }

    // Called from the chart's click handler once a bar is chosen.
    window.oipReplayShowControls = function () {
        if (!replayToolbar) return;
        replayToolbar.classList.remove('hidden');
        replayToolbar.style.display = 'flex';
        oipSetToggleLabel('Bar Replay');
        btnToggleReplay?.classList.add('oip-btn--armed');
        oipUpdateReplayTfLabel();
    };

    window.oipReplayExitMode = function () {
        if (replayToolbar) {
            replayToolbar.classList.add('hidden');
            replayToolbar.style.display = 'none';
        }
        btnToggleReplay?.classList.remove('oip-btn--armed');
        document.getElementById('oipJumpReplay')?.classList.remove('oip-btn--armed');
        oipSetToggleLabel('Bar Replay');
        window.oipSelectionMode = false;
        document.getElementById('oipReplayPause')?.click();      // stop the timer
        try { oipOIChart.applyOptions({ handleScroll: true, handleScale: true }); } catch (e) {}
        // Back to the full chart
        if (oipFullCandles && oipFullCandles.length) {
            oipReplayIndex = oipFullCandles.length - 1;
            const slider = document.getElementById('oipReplaySlider');
            if (slider) slider.value = oipReplayIndex;
            oipRefreshLocalView('combined', true, oipReplayIndex);
        }
    };

    if (btnToggleReplay) {
        if (replayToolbar) replayToolbar.style.display = 'none';
        btnToggleReplay.onclick = () => {
            const live = replayToolbar && replayToolbar.style.display !== 'none'
                         && !replayToolbar.classList.contains('hidden');
            if (live || window.oipSelectionMode) oipReplayExitMode();
            else oipReplayArm();
        };
    }
    btnReplayExit?.addEventListener('click', () => oipReplayExitMode());

    // Defer chart creation and initial data load until the panel is visible.
    // On the standalone /replay page the panel is visible immediately.
    // On the dashboard the replay panel starts as display:none — creating LC charts
    // inside a hidden container causes the Baseline renderer to receive null values
    // and crash. dashSwitch('replay') dispatches a resize event, so we use that as
    // the trigger to initialize once the panel actually has layout.
    function oipStartCharts() {
        // The browser restores a <select>'s value across a soft reload, so read
        // the TF back rather than trusting oipInterval's initial value.
        oipInterval = oipElems.interval?.value || oipInterval;
        oipEnsureRangeForAnchor();
        oipInitCharts();
        oipInitReplay();
        oipUpdateEmaVisibility();
        oipUpdateVwapVisibility();
        oipLoadCandles();
    }

    const oipChartWrap = document.getElementById('oipChartWrap');
    if (!oipChartWrap || oipChartWrap.offsetParent !== null) {
        oipStartCharts();
    } else {
        const onReplayShow = () => {
            if (document.getElementById('oipChartWrap')?.offsetParent !== null) {
                window.removeEventListener('resize', onReplayShow);
                oipStartCharts();
            }
        };
        window.addEventListener('resize', onReplayShow);
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
    if (sortedStrikes.length > 30) {
        let atmIndex = sortedStrikes.findIndex(s => s >= refPrice);
        if (atmIndex === -1) atmIndex = sortedStrikes.length - 1;
        let start = Math.max(0, atmIndex - 15);
        let end = Math.min(sortedStrikes.length, start + 30);
        if (end === sortedStrikes.length) start = Math.max(0, end - 30);
        sortedStrikes = sortedStrikes.slice(start, end);
    }
    let opts = '';
    if (sortedStrikes.length > 0) {
        sortedStrikes.forEach(s => { opts += `<option value="${s}">${s}</option>`; });
    } else {
        for (let i = -15; i <= 15; i++) {
            const s = atm + (i * step);
            if (s <= 0) continue;
            opts += `<option value="${s}">${s}</option>`;
        }
    }

    // CE & PE defaults: 100 pts from nearest round-hundred ATM
    const availStrikes = sortedStrikes.length > 0
        ? sortedStrikes
        : Array.from({ length: 31 }, (_, i) => atm + (i - 15) * step);
    const refBase = Math.round(refPrice / 100) * 100;
    const ceDefaultArr = availStrikes.filter(s => s >= refBase + 100 && s % 100 === 0);
    const peDefaultArr = availStrikes.filter(s => s <= refBase - 100 && s % 100 === 0);
    const ceDefault = ceDefaultArr.length ? Math.min(...ceDefaultArr) : atm;
    const peDefault = peDefaultArr.length ? Math.max(...peDefaultArr) : atm;

    function syncDropdown(el, prevVal, defaultVal = atm) {
        if (!el) return;
        el.innerHTML = opts;
        const defStr = String(defaultVal);
        if (centerPrice && !oipCustomStrikeSetOnLoad) {
            el.value = defStr;
            if (!opts.includes(`value="${defStr}"`)) el.value = String(atm);
        } else if (prevVal && opts.includes(`value="${prevVal}"`)) {
            el.value = prevVal;
        } else {
            el.value = defStr;
            if (!opts.includes(`value="${defStr}"`)) el.value = String(atm);
        }
    }

    const prevCustom = oipElems.customStrikeDropdown.value;
    const prevCE = oipElems.ceStrikeDropdown?.value;
    const prevPE = oipElems.peStrikeDropdown?.value;

    syncDropdown(oipElems.customStrikeDropdown, prevCustom);
    syncDropdown(oipElems.ceStrikeDropdown, prevCE, peDefault);
    syncDropdown(oipElems.peStrikeDropdown, prevPE, ceDefault);

    if (centerPrice && !oipCustomStrikeSetOnLoad) {
        oipCustomStrikeSetOnLoad = true;
        // Strikes were just auto-selected for the first time — reload so the API
        // is called again with the now-populated CE/PE strike dropdowns.
        requestAnimationFrame(() => oipResetReplay());
    }

    return parseFloat(oipElems.customStrikeDropdown.value) || atm;
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

// ── 2nd candle box shared helpers ─────────────────────────────────────────────

function _oipColorAlpha(color, alpha) {
    if (color.startsWith('#')) {
        const r = parseInt(color.slice(1, 3), 16);
        const g = parseInt(color.slice(3, 5), 16);
        const b = parseInt(color.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }
    const m = color.match(/\d+/g);
    if (m && m.length >= 3) return `rgba(${m[0]},${m[1]},${m[2]},${alpha})`;
    return color;
}

function _oipDrawCandleBox(chart, hi, lo, times, color) {
    const safeTimes = times.filter(t => t != null && isFinite(t) && t > 0);
    if (!safeTimes.length) return null;
    const fillCol   = _oipColorAlpha(color, 0.10);
    const borderCol = _oipColorAlpha(color, 0.65);
    const shared = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null };
    try {
        const fill = chart.addSeries(LightweightCharts.BaselineSeries, {
            baseValue: { type: 'price', price: lo },
            topFillColor1: fillCol, topFillColor2: fillCol, topLineColor: 'transparent',
            bottomFillColor1: 'transparent', bottomFillColor2: 'transparent', bottomLineColor: 'transparent',
            lineWidth: 1, ...shared
        });
        fill.setData(safeTimes.map(t => ({ time: t, value: hi })));

        const top = chart.addSeries(LightweightCharts.LineSeries, { color: borderCol, lineWidth: 1, lineStyle: 0, ...shared });
        top.setData(safeTimes.map(t => ({ time: t, value: hi })));

        const bottom = chart.addSeries(LightweightCharts.LineSeries, { color: borderCol, lineWidth: 1, lineStyle: 0, ...shared });
        bottom.setData(safeTimes.map(t => ({ time: t, value: lo })));

        return { chart, fill, top, bottom };
    } catch (e) {
        console.warn('[CandleBox] skipped box hi=%s lo=%s:', hi, lo, e.message);
        return null;
    }
}

function _oipRemoveBoxSeries(box) {
    if (!box) return;
    ['fill', 'top', 'bottom'].forEach(k => {
        if (box[k]) { try { box.chart.removeSeries(box[k]); } catch (_) {} }
    });
}

/* Day boxes (2nd 30s / 2nd 5m) are static once their day has finished — only
   the day under the playhead can still grow. The draw functions below used to
   destroy and rebuild a box (3 series each) for EVERY loaded day on EVERY
   replay step; with a year of data that is ~700 series churned per step, and
   LightweightCharts charges ~2 ms per series created. Boxes are now cached per
   day and rebuilt only when that day's bar count changes. */
const _oipDayBoxCache = {};

function _oipDayBoxesClear(cacheId) {
    const cache = _oipDayBoxCache[cacheId];
    if (!cache) return;
    Object.keys(cache).forEach(dk => { _oipRemoveBoxSeries(cache[dk].box); delete cache[dk]; });
}

function _oipDayBoxesClearAll() { Object.keys(_oipDayBoxCache).forEach(_oipDayBoxesClear); }

// specFor(dayKey, dayCandles) -> { hi, lo, from } | null
function _oipDayBoxesRender(cacheId, chart, src, specFor, color) {
    const cache = _oipDayBoxCache[cacheId] || (_oipDayBoxCache[cacheId] = {});
    const map = _oipGroupByDay(src);
    // Days that dropped out of range (reload, or the playhead jumped backwards).
    Object.keys(cache).forEach(dk => {
        if (!map[dk]) { _oipRemoveBoxSeries(cache[dk].box); delete cache[dk]; }
    });
    Object.keys(map).sort().forEach(dk => {
        const day = map[dk];
        const cached = cache[dk];
        if (cached && cached.n === day.length) return;          // nothing new in this day
        const spec = specFor(dk, day);
        if (!spec) { if (cached) { _oipRemoveBoxSeries(cached.box); delete cache[dk]; } return; }
        const times = day.filter(c => c.time >= spec.from).map(c => c.time);
        if (!times.length) return;
        if (cached) _oipRemoveBoxSeries(cached.box);
        const box = _oipDrawCandleBox(chart, spec.hi, spec.lo, times, color);
        if (box) cache[dk] = { box, n: day.length };
    });
    return Object.keys(cache).map(dk => cache[dk].box);
}

function _oipGroupByDay(candles) {
    if (!candles || !candles.length) return {};
    const dayMap = {};
    candles.forEach(c => {
        if (c.time == null || !isFinite(c.time) || c.time <= 0) return;
        const d = new Date(c.time * 1000);
        const ds = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
        if (!dayMap[ds]) dayMap[ds] = [];
        dayMap[ds].push(c);
    });
    Object.keys(dayMap).forEach(k => dayMap[k].sort((a, b) => a.time - b.time));
    return dayMap;
}

function _oipH(c) { return parseFloat(c.high ?? c.h); }
function _oipL(c) { return parseFloat(c.low  ?? c.l); }

// ── 2nd 30-second candle box — all days ──────────────────────────────────────
let oip30sSecondCandle = { oi: [], ce: [], pe: [] };
let oip2ndCandle30sBox = { oi: [], ce: [], pe: [] };

function oipDraw2ndCandle30sBox(candles, maxTime) {
    const _30s_allowed = ['30second', 'minute'];
    const on = _30s_allowed.includes(oipInterval) && candles && candles.length &&
               document.getElementById('oipShow2ndCandle30s')?.checked;
    if (!on) {
        ['30s:oi', '30s:ce', '30s:pe'].forEach(_oipDayBoxesClear);
        oip2ndCandle30sBox = { oi: [], ce: [], pe: [] };
        return;
    }

    function _build30sMap(arr) {
        const m = {};
        (arr || []).forEach(c => {
            const d = new Date(c.time * 1000);
            const dk = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
            m[dk] = c;
        });
        return m;
    }
    const specWith = (map30s) => (dk, day) => {
        const c2 = (map30s && map30s[dk]) || (day.length >= 2 ? day[1] : null);
        if (!c2) return null;
        const hi = _oipH(c2), lo = _oipL(c2);
        if (!isFinite(hi) || !isFinite(lo) || hi === lo) return null;
        return { hi, lo, from: c2.time };
    };

    if (oipOIChart)
        oip2ndCandle30sBox.oi = _oipDayBoxesRender('30s:oi', oipOIChart, candles, specWith(_build30sMap(oip30sSecondCandle.oi)), '#FFC800');

    // CE/PE only exist where the Option Premium charts do — skip the filtering
    // entirely on the dashboard's Replay tab, which has none.
    if (!oipCEChart?.chart && !oipPEChart?.chart) return;

    // Defer CE/PE draws — addBaselineSeries triggers LC's async render RAF.
    // Use raw oipOptionData (actual trades only, no alignment gaps) filtered to
    // current replay time — same source as oi_profile.js.
    requestAnimationFrame(() => {
        try {
            const raw = oipOptionData || [];
            const ceSource = raw.filter(c => c.type === 'CE' && (!maxTime || c.time <= maxTime));
            const peSource = raw.filter(c => c.type === 'PE' && (!maxTime || c.time <= maxTime));
            if (oipCEChart?.chart && ceSource.length)
                oip2ndCandle30sBox.ce = _oipDayBoxesRender('30s:ce', oipCEChart.chart, ceSource, specWith(_build30sMap(oip30sSecondCandle.ce)), '#FFC800');
            if (oipPEChart?.chart && peSource.length)
                oip2ndCandle30sBox.pe = _oipDayBoxesRender('30s:pe', oipPEChart.chart, peSource, specWith(_build30sMap(oip30sSecondCandle.pe)), '#FFC800');
        } catch(e) {}
    });
}

// ── 2nd 5-minute candle box (09:20–09:25) — all days, 1m/2m/3m/5m ───────────
let oip2nd5mCandleBox = { oi: [], ce: [], pe: [] };

function oipDraw2nd5mCandleBox(candles, maxTime) {
    const allowedIntervals = ['minute', '2minute', '3minute', '5minute'];
    const on = allowedIntervals.includes(oipInterval) && candles && candles.length &&
               document.getElementById('oipShow2nd5mCandle')?.checked;
    if (!on) {
        ['5m:oi', '5m:ce', '5m:pe'].forEach(_oipDayBoxesClear);
        oip2nd5mCandleBox = { oi: [], ce: [], pe: [] };
        return;
    }

    const spec = (dk, day) => {
        const w = day.filter(c => {
            const d = new Date(c.time * 1000);
            return d.getUTCHours() === 9 && d.getUTCMinutes() >= 20 && d.getUTCMinutes() < 25;
        });
        if (!w.length) return null;
        const hi = Math.max(...w.map(_oipH));
        const lo = Math.min(...w.map(_oipL));
        if (!isFinite(hi) || !isFinite(lo) || hi === lo) return null;
        return { hi, lo, from: w[0].time };
    };

    if (oipOIChart)
        oip2nd5mCandleBox.oi = _oipDayBoxesRender('5m:oi', oipOIChart, candles, spec, '#00D2FF');

    if (!oipCEChart?.chart && !oipPEChart?.chart) return;

    requestAnimationFrame(() => {
        try {
            const raw = oipOptionData || [];
            const ceSource = raw.filter(c => c.type === 'CE' && (!maxTime || c.time <= maxTime));
            const peSource = raw.filter(c => c.type === 'PE' && (!maxTime || c.time <= maxTime));
            if (oipCEChart?.chart && ceSource.length)
                oip2nd5mCandleBox.ce = _oipDayBoxesRender('5m:ce', oipCEChart.chart, ceSource, spec, '#00D2FF');
            if (oipPEChart?.chart && peSource.length)
                oip2nd5mCandleBox.pe = _oipDayBoxesRender('5m:pe', oipPEChart.chart, peSource, spec, '#00D2FF');
        } catch(e) {}
    });
}
