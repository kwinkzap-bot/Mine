/**
 * OI Replay – Self-contained logic for Replay Mode
 */

'use strict';

/* ── State ────────────────────────────────────────────────── */
/* ── Time-scale (zoom / pan) sync helper ──────────────────────────────────────
   The crosshair helper above kept the two charts pointing at the same bar, but
   zooming or panning one left the other where it was. The range subscriptions
   that do this for the Intrinsic / CE / PE panes all sit inside a block guarded
   by `oipIntrinsicChart` — and those panes have no container on Replay, so the
   block is skipped wholesale and the Round Strike chart below was never wired
   for range at all. Only the crosshair ever reached it.

   Two charts on the SAME timeframe are matched bar-for-bar via bar spacing plus
   scroll position: the convention the rest of this page already uses, and it
   keeps the right-edge gap that setVisibleRange would flatten. The two TF
   dropdowns are deliberately independent though, and on different timeframes a
   bar is a different width in TIME — matching bar spacing there would leave the
   two showing different windows, so those fall back to matching the visible
   time range.

   Re-entrancy: applyOptions and scrollToPosition fire the target's own range
   callback synchronously, which would bounce straight back here. The flag makes
   the second hop a no-op. Only defined if absent, so a page that already has
   its own keeps it. */
if (typeof window !== 'undefined' && typeof window._oipSyncTimeScale !== 'function') {
    window._oipSyncTimeScale = (sourceChart, targetChart, sameBarWidth) => {
        if (!sourceChart || !targetChart || window.__oipTsSyncing) return;
        window.__oipTsSyncing = true;
        try {
            const src = sourceChart.timeScale(), dst = targetChart.timeScale();
            if (sameBarWidth) {
                const barSpacing = src.options().barSpacing;
                if (barSpacing) {
                    dst.applyOptions({ barSpacing });
                    dst.scrollToPosition(src.scrollPosition(), false);
                }
            } else {
                const range = src.getVisibleRange();
                if (range && range.from != null && range.to != null) dst.setVisibleRange(range);
            }
        } catch (e) {
        } finally {
            window.__oipTsSyncing = false;
        }
    };
}


/* ── Crosshair sync helper ────────────────────────────────────────────────────
   oi_profile_round_strike.js publishes its hover through window._oipSyncCrosshair
   and skips the whole subscription when that is not a function. The definition
   lives in oi_profile_init.js, which THIS page does not load — so on Replay the
   sync was silently doing nothing.

   Defined at module scope rather than inside oipInitCharts because that runs
   late (deferred behind a resize event when the chart container starts hidden,
   as it does inside the dashboard), and by then the Round Strike chart has
   already made its one check. Only defined if absent, so the OI Profile page
   keeps its own. */
if (typeof window !== 'undefined' && typeof window._oipSyncCrosshair !== 'function') {
    window._oipSyncCrosshair = (sourceChart, targetChart, param, targetSeries) => {
        if (!targetChart || !targetSeries) return;
        try {
            const valid = param && param.point && param.time != null;
            // Deferred past LC's init RAF: clearCrosshairPosition kicks off an
            // async render that throws if the chart is not ready yet.
            if (!valid) {
                requestAnimationFrame(() => { try { targetChart.clearCrosshairPosition(); } catch (e) {} });
                return;
            }
            const price = targetSeries.coordinateToPrice(param.point.y);
            requestAnimationFrame(() => {
                try {
                    if (price != null) targetChart.setCrosshairPosition(price, param.time, targetSeries);
                    else targetChart.clearCrosshairPosition();
                } catch (e) {}
            });
        } catch (e) {}
    };
}


let oipOIChart = null;
let oipOISeries = null;
let oipIntrinsicChart = null;
let oipIntrinsicSeries = null;
let oipIntrinsicPeSeries = null;
let oipOIData = null;
let oipOptionData = null;
// Pine draws Current / Previous / Avg-3 VWAP together, so Replay carries all
// three now. Names match the ones oi_indicators.js reaches for when a line's
// colour or width changes (_oipLineStyleSeriesMap).
let oipCvwapSeries = null;
let oipPvwapSeries = null;
let oipAvg3VwapSeries = null;
let oipVwapIntSeries = null;
let oipVwapIntPeSeries = null;
// oipCprSeriesMap — defined in oi_indicators.js
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
window._oipSuppressRangeSync = false;
let _oip30mLastBucket = -1; // 30m bucket index of the last candle when reversal lines were last drawn

let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

let oipAllStrikes = [];
let oipCurrentPrice = 0;
let oipSymbol = 'NIFTY';
let oipLotSize = 50, oipStrikeStep = 50;
let oipInterval = 'minute';    // matches the TF select's default option
let oipMode = 'change';
let oipRafId = null;
let oipOIChartReady = false;
let oipIntChartReady = false;
let oipCEChartReady = false;
let oipPEChartReady = false;
let oipCustomStrikeSetOnLoad = false;

// DOM Cache
const oipElems = {
    symbolSelect: null, interval: null,
    spotHigh: null, spotLow: null, step: null, multiplier: null,
    view: null, showVwapOI: null, showVwapInt: null,
    showCpr: null, showEMA: null, showOIBars: null, autoHL: null, chartWrap: null, canvas: null,
    tooltip: null, refreshIcon: null, itmCE: null, itmPE: null,
    hdrPrice: null, hdrPcr: null, hdrMaxPain: null, hdrCeOI: null,
    hdrCeChg: null, hdrPeOI: null,
    hdrPeChg: null, hdrTrend: null, hdrAtm: null, brokerSelect: null,
    showPremium: null, first5mATM: null, targetDistance: null, customStrikeCheck: null, customStrikeDropdown: null,
    strikeMode: null, ceStrikeDropdown: null, peStrikeDropdown: null,
    showEma9: null, showEma20: null, showEma50: null, showEma100: null, showEma200: null,
    exitAll: null, days: null, startDate: null, endDate: null, replayDate: null,
    hdrLotSize: null,
    hdrIVP: null, ivpGaugeBar: null, ivCrushAlert: null
};

/* ── Initialization ────────────────────────────────────────── */
function oipInitElems() {
    oipElems.symbolSelect = document.getElementById('oipSymbolSelect');
    oipElems.interval = document.getElementById('oipInterval');
    oipElems.spotHigh = document.getElementById('oipSpotHigh');
    oipElems.spotLow = document.getElementById('oipSpotLow');
    oipElems.step = document.getElementById('oipStep');
    oipElems.multiplier = document.getElementById('oipMultiplier');
    oipElems.view = document.getElementById('oipIntrinsicView');
    oipElems.showOIBars = document.getElementById('oipShowOIBars');
    oipElems.showVwapOI = document.getElementById('oipShowVwapGroup');
    oipElems.showVwapInt = document.getElementById('oipShowVwapInt');
    oipElems.showCpr = document.getElementById('oipShowCpr');
    oipElems.showEMA = document.getElementById('oipShowEMA');
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
    oipElems.replayDate = document.getElementById('oipReplayDate');
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
                if (window._oipSuppressRangeSync || _oipSyncDepth > 0 || activeChartId !== 'index' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipOIChart, [
                    oipIntrinsicChart?.chart,
                    { chart: oipCEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ },
                    { chart: oipPEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ }
                ]);
            });
            oipIntrinsicChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipSuppressRangeSync || _oipSyncDepth > 0 || activeChartId !== 'intrinsic' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipIntrinsicChart.chart, [
                    oipOIChart,
                    { chart: oipCEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ },
                    { chart: oipPEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ }
                ]);
            });
            oipCEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipSuppressRangeSync || _oipSyncDepth > 0 || activeChartId !== 'ce' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipCEChart.chart, [
                    { chart: oipOIChart, adj: _OIP_OPTION_RIGHT_ADJ },
                    { chart: oipIntrinsicChart?.chart, adj: _OIP_OPTION_RIGHT_ADJ },
                    oipPEChart?.chart
                ]);
            });
            oipPEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipSuppressRangeSync || _oipSyncDepth > 0 || activeChartId !== 'pe' || !oipOIChartReady || !oipIntChartReady) return;
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
                if (window._oipSuppressRangeSync || activeChartId !== 'index') return;
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipOIChart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipOIChart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipOIChart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipIntrinsicChart?.chart) {
            oipIntrinsicChart.chart.subscribeCrosshairMove(param => {
                if (window._oipSuppressRangeSync || activeChartId !== 'intrinsic') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipIntrinsicChart.chart, oipOIChart, param, oipOISeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipIntrinsicChart.chart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipIntrinsicChart.chart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipCEChart?.chart) {
            oipCEChart.chart.subscribeCrosshairMove(param => {
                if (window._oipSuppressRangeSync || activeChartId !== 'ce') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipCEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipCEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipCEChart.chart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipPEChart?.chart) {
            oipPEChart.chart.subscribeCrosshairMove(param => {
                if (window._oipSuppressRangeSync || activeChartId !== 'pe') return;
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
            // width 62 (not the 85 default) to match the Round Strike chart below.
            // The two are stacked and read together, and an axis 23px wider here
            // shifted this plot's right edge in by that much — so the same
            // timestamp sat at two different x positions and the synced crosshair
            // looked broken even when it was correct.
            rightPriceScale: { textColor: '#64748b', borderColor: 'transparent', width: 62, autoScale: true, visible: true, scaleMargins: { top: 0, bottom: 0 }, entireTextOnly: true },
            handleScroll: true, handleScale: true,
            localization: { locale: 'en-IN', timeFormatter: window.lwCrosshairTime, timezone: 'Etc/UTC' }
        });

        oipOISeries = oipOIChart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: '#1b9981', downColor: '#f23645', borderUpColor: '#1b9981', borderDownColor: '#f23645', wickUpColor: '#1b9981', wickDownColor: '#f23645',
            autoscaleInfoProvider: customAutoscale
        });
        lwBringToFront(oipOISeries);
        // crosshairMarkerVisible:false on every overlay line — LightweightCharts
        // otherwise parks a filled dot on the line under the crosshair.
        const _vwapBase = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null };
        const _vwapOpts = key => ({ color: oipGetLineColor(key), lineWidth: oipGetLineWidth(key), lineStyle: oipGetLineStyle(key), visible: false, ..._vwapBase });
        oipCvwapSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, _vwapOpts('cvwap'));
        oipPvwapSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, _vwapOpts('pvwap'));
        oipAvg3VwapSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, _vwapOpts('avg3vwap'));
        oipEma9Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#22c55e', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma20Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#f97316', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma50Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma100Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#3b82f6', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        oipEma200Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#000000', lineWidth: 1, visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });

        oipOIChartReady = true;

        // Crosshair sync with the Round Strike chart below (helper at the top of
        // this file). This marks which chart the pointer is actually over —
        // without it the two echo each other's synthetic crosshairs back and forth.
        ['mouseenter', 'touchstart'].forEach(evt => {
            elOI.addEventListener(evt, () => { window._oipActiveChartId = 'oi'; }, { passive: true });
        });
        // This direction (index chart -> Round Strike) had no wiring at all; the
        // reverse already existed in oi_profile_round_strike.js.
        oipOIChart.subscribeCrosshairMove(param => {
            if (window._oipActiveChartId !== 'oi') return;
            if (typeof oipRSChart !== 'undefined' && oipRSChart?.chart && typeof oipRSCESeries !== 'undefined' && oipRSCESeries) {
                window._oipSyncCrosshair(oipOIChart, oipRSChart.chart, param, oipRSCESeries);
            }
        });

        // Zoom / pan, same direction. The Round Strike chart is built later than
        // this one, so the target is resolved per event rather than captured.
        oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(() => {
            if (window._oipSuppressRangeSync || window._oipActiveChartId !== 'oi') return;
            try {
                if (!oipRSChart?.chart) return;
                window._oipSyncTimeScale(oipOIChart, oipRSChart.chart, oipInterval === oipRSInterval);
            } catch (e) {}
        });

        
        // Add Scroll to Latest button (Right Arrow)
        if (typeof TradingViewChart !== 'undefined' && TradingViewChart.addScrollButton) {
            TradingViewChart.addScrollButton(oipOIChart, oipOISeries, elOI);
        }

        oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(() => oipRequestDraw());
        oipOIChart.timeScale().subscribeVisibleTimeRangeChange(() => oipRequestDraw());

        // Pan-left backfill. `from` is a logical bar index, so it goes negative
        // once the user drags past bar 0 — firing while it is still positive is
        // what buys the fetch enough time to land before blank space shows.
        //
        // Two guards keep it from firing on its own. A fresh load ends in
        // fitContent(), which parks `from` at ~0 permanently, so the threshold
        // alone would chain fetches back through the whole history the moment
        // the page opened: it fires only after the user has actually grabbed
        // the chart, and only when `from` is DECREASING (a leftward drag, not
        // the rightward one that put it under the threshold in the first
        // place). Skipped mid-refresh too — series.setData() fires this
        // callback synchronously with a half-applied range.
        ['mousedown', 'touchstart', 'wheel'].forEach(ev =>
            elOI.addEventListener(ev, () => { _oipUserPanned = true; }, { passive: true }));

        oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
            if (window._oipSuppressRangeSync) return;
            if (!range || range.from == null) return;
            const prev = _oipLastVisibleFrom;
            _oipLastVisibleFrom = range.from;
            if (!_oipUserPanned || prev == null) return;
            if (range.from >= prev) return;                      // panning right
            if (range.from > OIP_BACKFILL_TRIGGER_BARS) return;
            oipBackfillOlderCandles();
        });
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

// How far back the page looks from its as-of date, per timeframe.
//
// TRADING days, not calendar days: a number here is that many sessions on the
// chart whatever weekends and holidays the span happens to cross, which is the
// only reading that gives a predictable amount of chart from a predictable
// number. The span sent to the API is widened to cover the non-trading days
// inside it, and the API trims back to whatever actually traded.
//
// The ladder is sized by what each timeframe actually COSTS to fetch, which is
// not proportional to its bar width. ICICI serves history in chunks whose size
// depends on the BASE interval it resamples from (_INTERVAL_MAP / _CHUNK_DAYS in
// icici_data_service.py), and each of those round trips takes several seconds:
//
//   30s        <- 1-second data,  1 calendar day  per request   (by far the worst)
//   1m/2m/3m   <- 1-minute data,  2 calendar days per request
//   5m/15m     <- 5-minute data, 10 calendar days per request
//   30m/1h     <- 30-minute data, 60 calendar days per request
//   1D/1W/1M   <- daily data,    500 calendar days per request
//
// So 2m and 3m cost the same per calendar day as 1m — they are resampled from
// the same 1-minute fetch — while 30m can span months for a single request. The
// windows below are chosen to keep every timeframe to roughly three or four
// round trips on a cold cache; the coarse tiers stay generous because their
// history is nearly free.
const OIP_REPLAY_WINDOW_DAYS = {
    '30second':   2,
    'minute':     5,
    '2minute':    5,
    '3minute':    7,
    '5minute':   15,
    '15minute':  30,
    '30minute':  60,
    '60minute': 120,
    'day':      250,    // ~1 year
    'week':     500,    // ~2 years
    'month':   1000,    // ~4 years
};
const OIP_REPLAY_WINDOW_FALLBACK = 30;

function oipReplayWindowDays() {
    return OIP_REPLAY_WINDOW_DAYS[oipInterval] ?? OIP_REPLAY_WINDOW_FALLBACK;
}

/** YYYY-MM-DD in the LOCAL timezone — toISOString() would shift IST back a day. */
function oipLocalDate(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
        + `-${String(d.getDate()).padStart(2, '0')}`;
}

// Turns the single as-of date into the start/end pair the candles API takes.
// Both stay hidden: they are a detail of that API, not something to keep in
// step by hand.
//
// The start is found by counting weekdays back rather than scaling calendar
// days, so the window does not quietly grow with the weekends it spans. Two
// days of slack cover a holiday inside it — erring long, because coming up
// short would silently drop a session off the far end.
//
// Depends on oipInterval, so it has to re-run when the timeframe changes, not
// only when the date does.
function oipWindowStartBefore(endDateStr) {
    const start = new Date(endDateStr + 'T00:00:00');
    let left = oipReplayWindowDays();
    while (left > 0) {
        start.setDate(start.getDate() - 1);
        if (start.getDay() !== 0 && start.getDay() !== 6) left--;
    }
    start.setDate(start.getDate() - 2);
    return oipLocalDate(start);
}

function oipApplyReplayDate() {
    const picked = oipElems.replayDate?.value;
    if (!picked || !oipElems.startDate || !oipElems.endDate) return;
    oipElems.startDate.value = oipWindowStartBefore(picked);
    oipElems.endDate.value = picked;
    // The window is the user's now, so the year-anchor widener must not fight it.
    oipStartDateTouched = true;
}

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
    // The group's master gates all three; each line then follows its own box.
    const on = oipElems.showVwapOI?.checked ?? false;
    const sub = id => on && (document.getElementById(id)?.checked ?? false);
    const set = (s, v) => { if (s) { try { s.applyOptions({ visible: v }); } catch (e) {} } };
    set(oipCvwapSeries, sub('oipShowCVWAP'));
    set(oipPvwapSeries, sub('oipShowPVWAP'));
    set(oipAvg3VwapSeries, sub('oipShow3AvgVWAP'));
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
// defined in oi_indicators.js

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

/* ── Initial time-scale sync with the Round Strike chart ──────────────────────
   The two charts pair for zoom and pan through window._oipSyncTimeScale, but
   BOTH subscriptions that drive it are gated on window._oipActiveChartId — the
   marker that says which chart the pointer is over. On a fresh load nothing has
   been hovered yet, so that is undefined and neither direction ever fires: the
   index chart sits on its own fitContent() (the whole loaded window) while the
   Round Strike chart sits on Lightweight Charts' default framing of its own,
   much shorter window. They only line up once the user hovers one and drags,
   which is exactly the "sync after drag" behaviour this exists to remove.

   (A second reason the load could never sync itself: oipLoadCandles calls
   fitContent() while _oipSuppressRangeSync is still true — oipRefreshLocalView
   sets it and clears it two frames later — so that range change is dropped even
   when _oipActiveChartId happens to be set from a previous interaction.)

   Round Strike is the source. Its window is the replay date the user picked, at
   a bar spacing you can actually read; the index chart's fitContent() squeezes
   15 sessions of 1-minute bars to about a fifth of a pixel each. Panning left
   from there pulls the older bars in through the backfill above.

   If the Round Strike block never loads (no ICICI session, a failed fetch) the
   poll gives up and the index chart simply keeps the framing Lightweight Charts
   gave it — its own barSpacing:8 default, right-anchored — which is a readable
   view in its own right, not a blank one. */
const OIP_INITIAL_SYNC_TIMEOUT_MS = 15000;
const OIP_INITIAL_SYNC_POLL_MS = 120;

// Bumped by every load so a poll left over from the previous one gives up
// instead of re-framing the chart under the new window.
let _oipInitialSyncToken = 0;

// A chart with no data has no visible range — this is the readiness test for
// both sides, and for the Round Strike block it also covers its series, which
// are populated in the same render pass.
function _oipHasDrawnRange(chart) {
    try { return !!chart && chart.timeScale().getVisibleRange() != null; }
    catch (e) { return false; }
}

function oipSyncIndexToRoundStrikeOnce() {
    const token = ++_oipInitialSyncToken;
    const deadline = Date.now() + OIP_INITIAL_SYNC_TIMEOUT_MS;

    const attempt = () => {
        if (token !== _oipInitialSyncToken) return;           // superseded by a newer load
        if (Date.now() > deadline) return;                    // Round Strike never arrived

        const rs = (typeof oipRSChart !== 'undefined') ? oipRSChart?.chart : null;
        // Wait out the refresh guard too: syncing mid-refresh would read a
        // half-applied range off one chart and stamp it onto the other.
        const ready = !window._oipSuppressRangeSync
            && oipOIChartReady && _oipHasDrawnRange(oipOIChart)
            && _oipHasDrawnRange(rs);

        if (!ready) { setTimeout(attempt, OIP_INITIAL_SYNC_POLL_MS); return; }

        try {
            // Same call the drag path makes, minus the _oipActiveChartId gate —
            // there is no pointer to attribute this to.
            window._oipSyncTimeScale(rs, oipOIChart,
                                     (typeof oipRSInterval !== 'undefined') && oipRSInterval === oipInterval);
        } catch (e) {
            console.warn('[Replay] initial time-scale sync failed:', e);
        }
    };

    setTimeout(attempt, OIP_INITIAL_SYNC_POLL_MS);
}

async function oipLoadCandles(forceFetch = true, resetZoom = false) {
    _oip30mLastBucket = -1; // force redraw on fresh load
    // A new window is a new history — whatever the last one ran out of says
    // nothing about this one (different symbol, timeframe or date).
    oipResetBackfillState();
    _oipCandleLoadInFlight = true;
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

    const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=true&first_5m_atm=${first5m}&custom_strike=${customStrike}&ce_strike=${ceStrike}&pe_strike=${peStrike}${dateRangeParams}${oipCandleLegParams()}&_t=${Date.now()}`;
    let res, data;
    try {
        res = await fetch(url); data = await res.json();
    } catch (e) {
        _oipCandleLoadInFlight = false;
        throw e;
    }
    if (!data.success) {
        _oipCandleLoadInFlight = false;
        console.error('[Replay] API error:', data.error || 'Unknown error');
        if (typeof showNotification === 'function') showNotification(data.error || 'Failed to load candle data. Check broker login.', 'error');
        return;
    }
    if (!data.candles || !data.candles.length) {
        _oipCandleLoadInFlight = false;
        const reason = data.fetch_error ? `Broker error: ${data.fetch_error}` : 'No candle data for the selected date range. Check broker login.';
        if (typeof showNotification === 'function') showNotification(reason, 'error');
        return;
    }

    // --- RESET STATE FOR NEW LOAD ---
    oipLastRefreshIndex = -1;
    oipFullCeData = [];
    oipFullPeData = [];
    oipCachedIndicators = {
        index: { vwap: [], pvwap: [], avg3vwap: [], ema9: [], ema20: [], ema50: [], ema100: [], ema200: [] },
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
    oip2ndCandle1mBox  = { oi: [], ce: [], pe: [] };
    oip2nd5mCandleBox  = { oi: [], ce: [], pe: [] };
    oipClearMondayBoxes();
    oipClear30mReversalLines();
    oipClear1DReversalLines();
    Object.values(oipMultiCprSeriesMap).forEach(s => { try { s.setData([]); } catch(e) {} });
    _oipMcprSeriesCount = -1;
    _oipMcprLastBucket = -1;
    _oipMcprLastTime = 0;

    // Clear all chart series
    try { if (oipOISeries) oipOISeries.setData([]); } catch(e) {}
    [oipCvwapSeries, oipPvwapSeries, oipAvg3VwapSeries].forEach(s => { try { s?.setData([]); } catch(e) {} });
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
    oipFullCandles = oipStripAuctionBars(
        (data.candles || []).filter(
            c => c.open != null && c.high != null && c.low != null && c.close != null
        ), oipInterval
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
    _oipRawCeCandles = ceRaw;
    _oipRawPeCandles = peRaw;
    
    oipOptionData = [
        ...oipStripAuctionBars(ceRaw, oipInterval).map(c => ({...c, type:'CE'})),
        ...oipStripAuctionBars(peRaw, oipInterval).map(c => ({...c, type:'PE'}))
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
    if (oipCvwapSeries) oipCvwapSeries.setData(oipCachedIndicators.index.vwap);
    if (oipPvwapSeries) oipPvwapSeries.setData(oipCachedIndicators.index.pvwap);
    if (oipAvg3VwapSeries) oipAvg3VwapSeries.setData(oipCachedIndicators.index.avg3vwap);
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

    _oipCandleLoadInFlight = false;

    if (oipOIChartReady) {
        // fitContent() squeezes the WHOLE loaded window into the width — 15
        // sessions of 1-minute bars comes out at about a fifth of a pixel each.
        // Where the Round Strike block exists it is the framing authority (see
        // oipSyncIndexToRoundStrikeOnce), so fitting here would only show that
        // squeezed view for a moment and then jump away from it. Skipping it
        // leaves the chart on its own barSpacing:8 default, which right-anchors
        // within a hair of where the sync lands — so the sync reads as a settle,
        // not a jump. Pages with no Round Strike block keep the fit.
        if (!document.getElementById('oipRSCombinedChart')) {
            oipOIChart.timeScale().fitContent();
        }

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

    // Last, so it overrides the fitContent() above once Round Strike has bars
    // to match. Outside the oipOIChartReady branch on purpose: it does its own
    // readiness check and has to keep polling if the chart is not up yet.
    oipSyncIndexToRoundStrikeOnce();
}

let _oipPrecalcDone = false;
let oipFullCeData = [];
let oipFullPeData = [];
let oipCachedIndicators = {
    index: { vwap: [], pvwap: [], avg3vwap: [], ema9: [], ema20: [], ema50: [], ema100: [], ema200: [] },
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
    oipCachedIndicators.index.pvwap = oipCalculatePVWAP(oipFullCandles);
    oipCachedIndicators.index.avg3vwap = oipCalculateAvg3VWAP(oipFullCandles);
    oipCachedIndicators.index.ema9 = oipCalculateFixedEMA(oipFullCandles, 9);
    oipCachedIndicators.index.ema20 = oipCalculateFixedEMA(oipFullCandles, 20);
    oipCachedIndicators.index.ema50 = oipCalculateFixedEMA(oipFullCandles, 50);
    oipCachedIndicators.index.ema100 = oipCalculateFixedEMA(oipFullCandles, 100);
    oipCachedIndicators.index.ema200 = oipCalculateFixedEMA(oipFullCandles, 200);
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

    // Walk the Round Strike chart to the same bar. Done here rather than in each
    // of the play/step/slider/jump handlers because every one of them lands on
    // this function, so this is the single place the replay position is known.
    window.oipRSApplyReplayCutoff?.(oipFullCandles[index]?.time ?? null);

    // Suppress cross-chart range sync for the entire refresh. series.update() and
    // series.setData() fire subscribeVisibleLogicalRangeChange synchronously; without
    // this guard, the active chart's callback runs syncRange mid-update and calls
    // applyOptions on the OI chart while it still has a partially-rendered series →
    // LC Candlestick renderer crashes with "Value is null".
    window._oipSuppressRangeSync = true;

    if (!_oipPrecalcDone) oipPrecalculateIndicators();

    const isIncremental = index === oipLastRefreshIndex + 1;
    oipLastRefreshIndex = index;

    const updateOrSet = (series, fullData, idx) => {
        if (!series || !fullData) return;
        if (isIncremental) { if (fullData[idx] != null) series.update(fullData[idx]); }
        else series.setData(fullData.slice(0, idx + 1));
    };

    const timeAtIdx = oipFullCandles[index].time;

    // PVWAP and 3-AVG VWAP hold the PREVIOUS session's value, so they emit
    // nothing for the sessions with no predecessor loaded and their arrays are
    // shorter than the candle array. Index-matched updates would push the wrong
    // bar's value; match on the timestamp instead.
    const updateOrSetAt = (series, fullData, t) => {
        if (!series || !fullData) return;
        if (isIncremental) { const p = fullData.find(d => d.time === t); if (p) series.update(p); }
        else series.setData(fullData.filter(d => d.time <= t));
    };

    // 1. Index Chart
    updateOrSet(oipOISeries, oipFullCandles, index);
    updateOrSet(oipCvwapSeries, oipCachedIndicators.index.vwap, index);
    updateOrSetAt(oipPvwapSeries, oipCachedIndicators.index.pvwap, timeAtIdx);
    updateOrSetAt(oipAvg3VwapSeries, oipCachedIndicators.index.avg3vwap, timeAtIdx);
    updateOrSet(oipEma9Series, oipCachedIndicators.index.ema9, index);
    updateOrSet(oipEma20Series, oipCachedIndicators.index.ema20, index);
    updateOrSet(oipEma50Series, oipCachedIndicators.index.ema50, index);
    updateOrSet(oipEma100Series, oipCachedIndicators.index.ema100, index);
    updateOrSet(oipEma200Series, oipCachedIndicators.index.ema200, index);

    // 2. CPR Redraw — the renderer clips to timeAtIdx itself.
    if (oipCachedIndicators.cpr) oipRenderPrecalculatedCPR(oipCachedIndicators.cpr, timeAtIdx);

    // Previous session's high/low. Recomputed per step so that during a replay it
    // shows the session before the bar being replayed, not the one before today.
    oipDrawPrevDayHL(oipVisibleCandles(index));

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
            requestAnimationFrame(() => { window._oipSuppressRangeSync = false; });
            return;
        }
        const _vis  = oipVisibleCandles(_idxSnap);
        // 30m reversal lines: always repaint (extend to current candle) but only detect
        // new signals when a 30m candle closes (bucket changes).
        const _tCur  = oipFullCandles[_idxSnap]?.time ?? 0;
        oipDraw2ndCandle30sBox(_vis, _tCur);
        oipDraw2ndCandle1mBox(_vis, _tCur);
        oipDraw2nd5mCandleBox(_vis, _tCur);
        oipDrawMondayBox(_vis);
        const _tPrev = _idxSnap > 0 ? (oipFullCandles[_idxSnap - 1]?.time ?? _tCur) : _tCur;
        const _cur30mBucket = Math.floor(_tCur / 1800);
        const _recompute30m = _oip30mLastBucket < 0 || _cur30mBucket !== Math.floor(_tPrev / 1800);
        if (_recompute30m) _oip30mLastBucket = _cur30mBucket;
        oipDraw30mReversalLines(_vis, _recompute30m);
        oipDraw1DReversalLines(_vis);
        oipRefreshMultiCPR(_vis);
        // Defer the suppress reset by one extra RAF so it runs AFTER the inner RAFs
        // that oipDraw2ndCandle30sBox and oipDraw2nd5mCandleBox schedule for CE/PE
        // series creation. RAF callbacks run FIFO; the inner RAFs were queued first,
        // so they execute before this reset fires.
        requestAnimationFrame(() => { window._oipSuppressRangeSync = false; });
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

/* Replay's Multi CPR step. The checkbox has been in the popup all along but
   nothing ever called the renderer here, so it drew nothing whichever way it was
   set; it is wired now that Pine's default (the group on, 1 Hour alone) is the
   default here too.

   oipDrawMultiCPR derives its buckets from whatever candles it is handed, so the
   playhead prefix is all the clipping this needs. The z-order restack it
   normally ends with is skipped per step and run only when a new bucket adds
   series to the map — see the applyZ argument. */
let _oipMcprSeriesCount = -1;
let _oipMcprLastBucket = -1;
let _oipMcprLastTime = 0;

// Which Multi CPR timeframe is currently the finest one switched on — the rate
// at which its levels can actually change.
function _oipMcprSmallestMinutes() {
    const on = [['oipMultiCpr15m', 15], ['oipMultiCpr30m', 30], ['oipMultiCpr1h', 60]]
        .filter(([id]) => document.getElementById(id)?.checked);
    return on.length ? Math.min(...on.map(([, mins]) => mins)) : 60;
}

function oipRefreshMultiCPR(candles, force = false) {
    if (!oipOIChart) return;
    const on = document.getElementById('oipShowMultiCpr')?.checked === true;
    if (!on) {
        if (_oipMcprSeriesCount === 0) return;         // already blank, nothing to do
        oipDrawMultiCPR(candles, false);
        _oipMcprSeriesCount = 0;
        _oipMcprLastBucket = -1;
        _oipMcprLastTime = 0;
        return;
    }

    /* A bucket's levels come off the PREVIOUS bucket, so they are constant for
       its whole span — there is nothing new to draw until one closes. Rebuilding
       every bar meant re-aggregating the entire loaded window three times and
       re-setting every bucket series on each replay step, which a three-month
       window (~394 hourly buckets) turns from wasteful into unusable.

       The cost is that the band's right edge advances in whole buckets rather
       than bar by bar, so it can trail the playhead by up to one bucket. The
       levels it shows are correct the whole time; only the extension lags. */
    const bucketSec = _oipMcprSmallestMinutes() * 60;
    const last = candles?.length ? candles[candles.length - 1].time : 0;
    const bucket = Math.floor(last / bucketSec);
    // Stepping BACKWARDS shortens the prefix, and the band has to shorten with
    // it — a slider drag inside one bucket would otherwise leave it drawn out to
    // where the playhead used to be.
    const rewound = last < _oipMcprLastTime;
    if (!force && !rewound && bucket === _oipMcprLastBucket) return;
    _oipMcprLastBucket = bucket;
    _oipMcprLastTime = last;

    oipDrawMultiCPR(candles, false);
    const n = Object.keys(oipMultiCprSeriesMap).length;
    if (n !== _oipMcprSeriesCount) {
        _oipMcprSeriesCount = n;
        oipApplyZOrder();
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


/* This page draws the index candles and nothing else — the Intrinsic / CE / PE /
   Fixed panes have no container in oi_replay.html, so oipInitSecondaryCharts
   bails and those chart objects stay null. The endpoint was still resolving and
   fetching all of their legs, which on a historical window means eight or so
   extra chunked Breeze fetches queued behind a 1.5 req/s budget, thrown away the
   moment they arrive. Ask for the index only.

   include_30s=false for the same reason: the 30-second sub-candles exist for the
   option panes. */
function oipCandleLegParams() {
    return '&opt=false&include_30s=false';
}

/* ── Pan-left history backfill ────────────────────────────────────────────────
   The page loads ONE window (oipReplayWindowDays() sessions behind the replay
   date) and nothing beyond it, so dragging the chart left used to run straight
   off the front of the data into blank space. This fetches the window BEFORE
   the earliest loaded bar as that edge comes into view and splices it onto the
   front, in place — no reload, no re-fit, and the bars under the cursor do not
   move.

   Two things make it feel seamless rather than like a refresh:

   * It fires EARLY. The trigger is OIP_BACKFILL_TRIGGER_BARS bars of remaining
     history, not zero, so the fetch is usually finished before the user has
     dragged far enough to see the end of the data.
   * Prepending N bars renumbers every logical index by +N, which would slide
     the view N bars to the right — the visual "jump" that makes a chart feel
     like it reloaded. The visible logical range is captured before the splice
     and re-applied shifted by exactly N, so the same bars stay under the same
     pixels.

   `oipReplayIndex` is a position INTO oipFullCandles, so it is shifted by the
   same N — otherwise the playhead (and the Round Strike cutoff it drives) would
   silently jump back N bars into the newly loaded history. */
const OIP_BACKFILL_TRIGGER_BARS = 15;

let _oipBackfillBusy = false;
// Set once the broker answers an older window with nothing. Intraday history is
// finite (Fyers caps 1-minute history at a few months), so running out is the
// normal end state, not an error — latch it and stop asking on every drag.
let _oipBackfillExhausted = false;
let _oipCandleLoadInFlight = false;
// The raw option legs of the current window, kept so a splice can re-run the
// same index alignment oipLoadCandles does. Replay itself fetches with
// opt=false and these stay empty; the dashboard's Replay tab is the case that
// would otherwise desync CE/PE from the candle array.
let _oipRawCeCandles = [];
let _oipRawPeCandles = [];
// The user has grabbed the chart at least once since the last load, and the
// last `from` seen — together these separate a real leftward drag from the
// programmatic range changes a load/refresh produces.
let _oipUserPanned = false;
let _oipLastVisibleFrom = null;

function oipResetBackfillState() {
    _oipBackfillBusy = false;
    _oipBackfillExhausted = false;
    // A reload ends in fitContent(); forget the drag so that fit is not read
    // as the user asking for more history.
    _oipUserPanned = false;
    _oipLastVisibleFrom = null;
}

function oipBackfillNote(text) {
    const el = document.getElementById('oipBackfillNote');
    if (!el) return;
    if (!text) { el.classList.add('hidden'); return; }
    el.textContent = text;
    el.classList.remove('hidden');
}

// Realigns the option legs to oipFullCandles after it has grown at the front.
// Same mapping oipLoadCandles uses: every index bar gets its option bar, or a
// whitespace placeholder, so the two arrays stay index-for-index.
function oipRealignOptionLegs() {
    const align = (optCandles) => {
        const optMap = new Map((optCandles || []).map(c => [c.time, c]));
        return oipFullCandles.map(ic => optMap.get(ic.time) || { time: ic.time });
    };
    oipFullOptionData = [
        ...align(_oipRawCeCandles).map(c => ({ ...c, type: 'CE' })),
        ...align(_oipRawPeCandles).map(c => ({ ...c, type: 'PE' }))
    ];
}

async function oipBackfillOlderCandles() {
    if (_oipBackfillBusy || _oipBackfillExhausted || _oipCandleLoadInFlight) return;
    if (!oipFullCandles?.length || !oipOIChartReady) return;

    const firstTime = oipFullCandles[0].time;
    // The earliest bar's own date, not the day before it: that first day is
    // usually only partly loaded (the window start lands mid-session), so
    // re-asking for it fills in the morning as well. Overlap is dropped below.
    const endDate = oipLocalDate(new Date(firstTime * 1000));
    const startDate = oipWindowStartBefore(endDate);
    const days = Math.max(
        1, Math.ceil((new Date(endDate) - new Date(startDate)) / 86400000) + 1);

    _oipBackfillBusy = true;
    oipBackfillNote('Loading earlier bars…');
    try {
        const h = parseFloat(oipElems.spotHigh?.value || 0), l = parseFloat(oipElems.spotLow?.value || 0);
        const st = parseInt(oipElems.step?.value || 50), m = parseInt(oipElems.multiplier?.value || 3);
        const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}`
            + `&days=${days}&spot_high=${h}&spot_low=${l}&step=${st}&multiplier=${m}`
            + `&auto_hl=true&start_date=${startDate}&end_date=${endDate}`
            + `${oipCandleLegParams()}&_t=${Date.now()}`;
        const res = await fetch(url);
        const data = await res.json();

        // A failed fetch is NOT exhaustion — a rate-limited or logged-out broker
        // must stay retryable, or one bad tick would disable backfill for the
        // life of the page.
        if (!data.success) {
            console.warn('[Replay] backfill failed:', data.error);
            oipBackfillNote('');
            return;
        }

        const older = oipStripAuctionBars(
            (data.candles || []).filter(
                c => c.open != null && c.high != null && c.low != null && c.close != null
            ), oipInterval
        ).filter(c => c.time < firstTime);

        if (!older.length) {
            _oipBackfillExhausted = true;
            oipBackfillNote('No earlier data');
            setTimeout(() => oipBackfillNote(''), 2000);
            return;
        }

        // The window slid; the last load's window start is no longer what is on
        // screen. Keeping the input honest matters because oipReloadStrikeOnly
        // re-fetches the option legs from these two dates.
        if (oipElems.startDate) oipElems.startDate.value = startDate;

        oipSpliceOlderCandles(older);
    } catch (e) {
        console.warn('[Replay] backfill error:', e);
        oipBackfillNote('');
    } finally {
        _oipBackfillBusy = false;
    }
}

// Splices `older` onto the front of the loaded history and redraws in place.
function oipSpliceOlderCandles(older) {
    const ts = oipOIChart.timeScale();
    const before = ts.getVisibleLogicalRange();
    const n = older.length;

    oipFullCandles = older.concat(oipFullCandles);
    oipRealignOptionLegs();

    // Every index into oipFullCandles moved by n.
    oipReplayIndex += n;
    // -2 rather than a shifted value: the prefix the series holds is now the
    // wrong one, so the next refresh has to be a full setData, not an update().
    oipLastRefreshIndex = -2;

    // Everything derived from the candle array is now stale at the front:
    // indicators are seeded from bar 0, and the box/reversal/CPR caches are
    // keyed by day or bucket and have never seen these days.
    _oipPrecalcDone = false;
    oipInvalidateVisCache();
    _oipDayBoxesClearAll();
    oip2ndCandle30sBox = { oi: [], ce: [], pe: [] };
    oip2ndCandle1mBox  = { oi: [], ce: [], pe: [] };
    oip2nd5mCandleBox  = { oi: [], ce: [], pe: [] };
    oipClearMondayBoxes();
    oipClear30mReversalLines();
    oipClear1DReversalLines();
    _oip30mLastBucket = -1;
    _oipMcprSeriesCount = -1;
    _oipMcprLastBucket = -1;
    _oipMcprLastTime = 0;

    const slider = document.getElementById('oipReplaySlider');
    if (slider) {
        slider.max = oipFullCandles.length - 1;
        slider.value = oipReplayIndex;
    }

    oipRefreshLocalView('combined', false, oipReplayIndex);

    // Re-anchor. The series were re-set synchronously above, so the shifted
    // range can be applied straight away; the second pass on the next frame
    // covers the RAF-deferred box/CPR draws, which touch the time scale.
    const restore = () => {
        if (!before || before.from == null || before.to == null) return;
        // The range subscription is suppressed for the whole refresh, so it
        // never sees this shift — record it here or the next drag would be
        // read as a decrease against the stale pre-splice value.
        _oipLastVisibleFrom = before.from + n;
        try { ts.setVisibleLogicalRange({ from: before.from + n, to: before.to + n }); } catch (e) {}
    };
    restore();
    requestAnimationFrame(() => { restore(); oipBackfillNote(''); });
}

/* ── Previous-day High / Low ──────────────────────────────────────────────────
   The previous SESSION's high and low, drawn as two rose step lines that change
   at each day boundary — so scrolling back through the window shows what the
   prior day's range was at every point, not one flat level taken from the last
   session on screen.

   Deliberately separate from the CPR block's own "Prev H / L": that one is a
   sub-item of CPR Levels (it disappears when CPR is switched off) and comes off
   the CPR payload. This is computed from the candles themselves, which is what
   lets it follow a replay — step back a day and it re-anchors.

   Step lines (lineType 1) rather than price lines: a price line spans the whole
   chart at one value, which is wrong the moment more than one session is
   loaded. */
const OIP_PREV_DAY_HL_COLOR = '#f43f5e';   // rose
let oipPrevDayHighSeries = null, oipPrevDayLowSeries = null;

/** IST calendar day for a bar. Bars are stored pre-shifted so UTC getters read
 *  as IST — the same trick the chart's own time formatter uses. */
function _oipBarDay(t) {
    const d = new Date(t * 1000);
    return `${d.getUTCFullYear()}-${d.getUTCMonth()}-${d.getUTCDate()}`;
}

/** [{time, value}] pairs carrying each bar's PREVIOUS session high and low. */
function oipCalcPrevDayHL(candles) {
    const high = [], low = [];
    if (!candles || !candles.length) return { high, low };

    // One pass to collect each session's range, in order.
    const days = [];
    let cur = null;
    for (const c of candles) {
        const day = _oipBarDay(c.time);
        if (!cur || cur.day !== day) {
            cur = { day, high: c.high, low: c.low };
            days.push(cur);
        } else {
            if (c.high > cur.high) cur.high = c.high;
            if (c.low < cur.low) cur.low = c.low;
        }
    }
    const prevOf = new Map();
    for (let i = 1; i < days.length; i++) prevOf.set(days[i].day, days[i - 1]);

    // The first session on screen has no predecessor loaded, so it plots nothing
    // rather than borrowing its own range.
    for (const c of candles) {
        const prev = prevOf.get(_oipBarDay(c.time));
        if (!prev) continue;
        high.push({ time: c.time, value: prev.high });
        low.push({ time: c.time, value: prev.low });
    }
    return { high, low };
}

function oipDrawPrevDayHL(candles) {
    const on = document.getElementById('oipShowPrevDayHL')?.checked === true;
    if (!oipOIChart) return;

    if (!oipPrevDayHighSeries) {
        const opts = {
            color: OIP_PREV_DAY_HL_COLOR, lineWidth: 1, lineType: 1,   // 1 = with steps
            priceLineVisible: false, lastValueVisible: true,
            crosshairMarkerVisible: false, autoscaleInfoProvider: () => null
        };
        oipPrevDayHighSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, { ...opts, title: 'PDH' });
        oipPrevDayLowSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, { ...opts, title: 'PDL' });
    }
    oipPrevDayHighSeries.applyOptions({ visible: on });
    oipPrevDayLowSeries.applyOptions({ visible: on });
    if (!on) return;

    const { high, low } = oipCalcPrevDayHL(candles);
    oipPrevDayHighSeries.setData(high);
    oipPrevDayLowSeries.setData(low);
}

/* ── Replay Core ───────────────────────────────────────────── */
function oipResetReplay() {
    oipFullCandles = null; oipFullOptionData = null; oipReplayIndex = 0;
    // Otherwise the Round Strike chart stays cut at wherever the slider was.
    window.oipRSApplyReplayCutoff?.(null);
    oipLoadCandles();
}

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
    const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=true&first_5m_atm=${first5m}&custom_strike=${customStrike}&ce_strike=${ceStrike}&pe_strike=${peStrike}${dateRangeParams}${oipCandleLegParams()}&_t=${Date.now()}`;

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
    oipInitIndicatorsPopup('oip-ind-replay-v4');
    // Replay opens on today and looks back OIP_REPLAY_WINDOW_DAYS[interval] sessions.
    // It used to open on the year to date, which made the very first fetch a
    // year of candles; the page now has one date and a fixed window behind it.
    const today = oipLocalDate(new Date());
    if (oipElems.replayDate) {
        if (!oipElems.replayDate.value) oipElems.replayDate.value = today;
        oipElems.replayDate.max = today;      // there is no history for tomorrow
    }
    // The window length is read off the timeframe, so take the dropdown's value
    // before sizing it: a reload can restore a TF the browser remembered, which
    // would otherwise be picked up further down (oipStartCharts) only after the
    // window had already been built from the wrong one.
    oipInterval = oipElems.interval?.value || oipInterval;
    oipApplyReplayDate();

    oipElems.replayDate?.addEventListener('change', () => {
        oipApplyReplayDate();
        oipResetReplay();
        // Same date, same window, one reload: the Round Strike block re-asks
        // which expiries were open then and reloads its legs.
        window.oipRSOnDateChanged?.();
    });
    // The page's single symbol control. oipSelectSymbol reloads the index
    // chart and tells the Round Strike block to follow.
    oipElems.symbolSelect?.addEventListener('change', e => oipSelectSymbol(e.target.value));
    oipElems.showOIBars?.addEventListener('change', () => oipRequestDraw());
    document.getElementById('oipShowPrevDayHL')?.addEventListener('change', () => {
        oipDrawPrevDayHL(oipVisibleCandles(oipReplayIndex));
    });

    oipElems.interval?.addEventListener('change', (e) => {
        oipInterval = e.target.value;
        oipUpdateReplayTfLabel();
        // The window is per-timeframe now, so switching TF resizes it.
        oipApplyReplayDate();
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

    // Virgin CPR recolours and reshapes bands from EARLIER sessions, which the
    // incremental advance path cannot express — blank the CPR state so the next
    // refresh takes the full-render route.
    ['oipCprShowVirgin', 'oipCprVirginExtend'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            oipClearCprSeries();
            if (oipFullCandles) oipRefreshLocalView('combined', false, oipReplayIndex);
        });
    });

    ['oipShowMultiCpr', 'oipMultiCpr15m', 'oipMultiCpr30m', 'oipMultiCpr1h'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (oipFullCandles) oipRefreshMultiCPR(oipVisibleCandles(oipReplayIndex), true);
        });
    });

    [
        'oipShowEma9', 'oipShowEma20', 'oipShowEma50', 'oipShowEma100', 'oipShowEma200',
        'oipShowCpr',
        'oipShowVwapGroup', 'oipShowCVWAP', 'oipShowPVWAP', 'oipShow3AvgVWAP',
        'oipShowVwapInt', 'oipShowPremium',
        'oipCprShowPrevHL', 'oipCprShowBand', 'oipCprShowResistance', 'oipCprShowSupport', 'oipCprShowCumR3S3',
        'oipCprShowVirgin', 'oipCprVirginExtend', 'oipCprShowLabels'
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
    document.getElementById('oipShow2ndCandle1m')?.addEventListener('change', () => {
        if (!oipFullCandles) return;
        oipDraw2ndCandle1mBox(oipVisibleCandles(oipReplayIndex), oipFullCandles[oipReplayIndex]?.time ?? 0);
    });
    document.getElementById('oipShowMondayBox')?.addEventListener('change', () => {
        if (oipFullCandles) oipDrawMondayBox(oipVisibleCandles(oipReplayIndex));
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

async function oipSelectSymbol(s) {
    oipSymbol = s;
    if (oipElems.symbolSelect && oipElems.symbolSelect.value !== s) oipElems.symbolSelect.value = s;
    oipCustomStrikeSetOnLoad = false;
    oipResetReplay();
    // The Round Strike block has no symbol control of its own any more — it
    // reads oipSymbol — so it has to be told the ground moved. Its expiry list
    // is symbol-specific too (NIFTY is weekly, BANKNIFTY monthly).
    window.oipRSOnDateChanged?.();
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

// fillAlpha/lineStyle/lineWidth are optional so the Monday box can ask for a
// border-only rectangle — a week-wide translucent fill swamps the candles.
function _oipDrawCandleBox(chart, hi, lo, times, color, fillAlpha = 0.10, lineStyle = 0, lineWidth = 1) {
    const safeTimes = times.filter(t => t != null && isFinite(t) && t > 0);
    if (!safeTimes.length) return null;
    const fillCol   = _oipColorAlpha(color, fillAlpha);
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

        const top = chart.addSeries(LightweightCharts.LineSeries, { color: borderCol, lineWidth, lineStyle, ...shared });
        top.setData(safeTimes.map(t => ({ time: t, value: hi })));

        const bottom = chart.addSeries(LightweightCharts.LineSeries, { color: borderCol, lineWidth, lineStyle, ...shared });
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

// ── 2nd 1-minute candle box — all days ──────────────────────────────────────
// Pine's "2nd 1-Min Candle Box": the 09:16 bar's range, held across the rest of
// the session. Its 5-minute sibling above is the same idea one timeframe up;
// this one only has a bar to point at on 1-minute and 30-second charts.
let oip2ndCandle1mBox = { oi: [], ce: [], pe: [] };

function oipDraw2ndCandle1mBox(candles, maxTime) {
    const allowedIntervals = ['30second', 'minute'];
    const on = allowedIntervals.includes(oipInterval) && candles && candles.length &&
               document.getElementById('oipShow2ndCandle1m')?.checked;
    if (!on) {
        ['1m:oi', '1m:ce', '1m:pe'].forEach(_oipDayBoxesClear);
        oip2ndCandle1mBox = { oi: [], ce: [], pe: [] };
        return;
    }

    const spec = (dk, day) => {
        const w = day.filter(c => {
            const d = new Date(c.time * 1000);
            return d.getUTCHours() === 9 && d.getUTCMinutes() === 16;
        });
        if (!w.length) return null;
        const hi = Math.max(...w.map(_oipH));
        const lo = Math.min(...w.map(_oipL));
        if (!isFinite(hi) || !isFinite(lo) || hi === lo) return null;
        return { hi, lo, from: w[0].time };
    };

    const col = oipGetLineColor('box1m');
    if (oipOIChart)
        oip2ndCandle1mBox.oi = _oipDayBoxesRender('1m:oi', oipOIChart, candles, spec, col);

    if (!oipCEChart?.chart && !oipPEChart?.chart) return;

    requestAnimationFrame(() => {
        try {
            const raw = oipOptionData || [];
            const ceSource = raw.filter(c => c.type === 'CE' && (!maxTime || c.time <= maxTime));
            const peSource = raw.filter(c => c.type === 'PE' && (!maxTime || c.time <= maxTime));
            if (oipCEChart?.chart && ceSource.length)
                oip2ndCandle1mBox.ce = _oipDayBoxesRender('1m:ce', oipCEChart.chart, ceSource, spec, col);
            if (oipPEChart?.chart && peSource.length)
                oip2ndCandle1mBox.pe = _oipDayBoxesRender('1m:pe', oipPEChart.chart, peSource, spec, col);
        } catch(e) {}
    });
}

// ── Monday High/Low box ─────────────────────────────────────────────────────
// Pine's "Monday H/L Box": each Monday's range carried across the rest of that
// week. Unlike the day boxes above it spans a week, so it can't ride on
// _oipDayBoxesRender — it gets its own per-week cache with the same contract
// (a week is rebuilt only while its bar count is still growing, which during a
// replay is the week under the playhead alone).
let oipMondayBoxes = [];
const _oipMondayBoxCache = {};

function oipClearMondayBoxes() {
    Object.keys(_oipMondayBoxCache).forEach(k => {
        _oipRemoveBoxSeries(_oipMondayBoxCache[k].box);
        delete _oipMondayBoxCache[k];
    });
    oipMondayBoxes = [];
}

function oipDrawMondayBox(candles) {
    if (!oipOIChart) return;
    if (!candles || !candles.length || !document.getElementById('oipShowMondayBox')?.checked) {
        oipClearMondayBoxes();
        return;
    }

    const dayMap = _oipGroupByDay(candles);
    const weeks = {};
    Object.keys(dayMap).sort().forEach(dk => {
        const day = dayMap[dk];
        // Bars are stored pre-shifted so the UTC getters read as IST — day 1 is Monday.
        if (new Date(day[0].time * 1000).getUTCDay() !== 1) return;
        let hi = -Infinity, lo = Infinity;
        day.forEach(c => { hi = Math.max(hi, _oipH(c)); lo = Math.min(lo, _oipL(c)); });
        if (!isFinite(hi) || !isFinite(lo) || hi === lo) return;
        weeks[dk] = { hi, lo, start: day[0].time, end: day[0].time + 7 * 86400 };
    });

    // Weeks that scrolled out of range, or that the playhead stepped back past.
    Object.keys(_oipMondayBoxCache).forEach(wk => {
        if (!weeks[wk]) { _oipRemoveBoxSeries(_oipMondayBoxCache[wk].box); delete _oipMondayBoxCache[wk]; }
    });

    const col   = oipGetLineColor('mondayBox');
    const style = oipGetLineStyle('mondayBox');
    const width = oipGetLineWidth('mondayBox');
    Object.keys(weeks).forEach(wk => {
        const w = weeks[wk];
        const times = candles.filter(c => c.time >= w.start && c.time < w.end).map(c => c.time);
        if (!times.length) return;
        const cached = _oipMondayBoxCache[wk];
        if (cached && cached.n === times.length && cached.hi === w.hi && cached.lo === w.lo) return;
        if (cached) _oipRemoveBoxSeries(cached.box);
        // Border only: a week-wide translucent fill would sit under every candle.
        const box = _oipDrawCandleBox(oipOIChart, w.hi, w.lo, times, col, 0, style, width);
        if (box) _oipMondayBoxCache[wk] = { box, n: times.length, hi: w.hi, lo: w.lo };
    });
    oipMondayBoxes = Object.keys(_oipMondayBoxCache).map(wk => _oipMondayBoxCache[wk].box);
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
