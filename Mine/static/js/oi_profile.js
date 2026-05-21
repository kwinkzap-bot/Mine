/**
 * OI Profile – WHITE THEME logic
 * Full-width Chart with In-Chart OI Bar Overlay
 */

'use strict';

/* ── State ────────────────────────────────────────────────── */
let oipOIChart = null;
let oipOISeries = null;
let oipIntrinsicChart = null;
let oipCombinedChart = null;
let oipCombinedSeries = null;
let oipCombinedSlSeries = null;
let oipIntrinsicSeries = null;
let oipIntrinsicPeSeries = null;
let oipOIData = null;
let oipOptionData = null;
let oipVwapSeries = null;
let oipVwapIntSeries = null;
let oipVwapIntPeSeries = null;
let oipCombinedVwapSeries = null;
let oipCprSeriesObj = null;
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

/* ── Theme Setup ────────────────────────────────────────── */
const OIP_CHART_THEMES = {
    'light': { bg: '#ffffff', text: '#374151', grid: '#f0f0f0' },
    'dark': { bg: '#111827', text: '#94a3b8', grid: 'rgba(255, 255, 255, 0.06)' },
    'forest': { bg: '#0a1410', text: '#6ba88f', grid: 'rgba(16, 185, 129, 0.06)' },
    'cream': { bg: '#fdfbf7', text: '#7c7267', grid: 'rgba(180, 83, 9, 0.05)' },
    'ocean': { bg: '#f3f8fc', text: '#475569', grid: 'rgba(2, 132, 199, 0.05)' }
};

function updateOIProfileTheme(themeName) {
    const oipPage = document.querySelector('.oip-page');
    if (!oipPage) return;

    // 1. Remove all old theme classes
    oipPage.classList.remove('light-theme', 'dark-theme', 'forest-theme', 'cream-theme', 'ocean-theme');
    // 2. Add new theme class
    oipPage.classList.add(`${themeName}-theme`);

    // Also apply to document.body for styling the main template / navigation bar
    document.body.classList.remove('light-theme', 'dark-theme', 'forest-theme', 'cream-theme', 'ocean-theme');
    document.body.classList.add(`${themeName}-theme`);

    // 3. Save to localStorage
    localStorage.setItem('app-theme', themeName);
    localStorage.setItem('oip-theme', themeName);

    // 4. Update the theme toggle button label/icon
    const themeBtn = document.getElementById('oip-theme-toggle-btn');
    if (themeBtn) {
        let label = '☀️ Light';
        if (themeName === 'dark') label = '🌌 Dark';
        else if (themeName === 'forest') label = '🌲 Forest';
        else if (themeName === 'cream') label = '📜 Cream';
        else if (themeName === 'ocean') label = '🌊 Ocean';
        themeBtn.textContent = label;
    }

    // 5. Update lightweight chart colors dynamically
    const cfg = OIP_CHART_THEMES[themeName] || OIP_CHART_THEMES['light'];

    const applyToChart = (chartInstance) => {
        if (!chartInstance) return;
        try {
            chartInstance.applyOptions({
                layout: {
                    textColor: cfg.text,
                    background: { type: 'solid', color: cfg.bg }
                },
                grid: {
                    vertLines: { color: cfg.grid },
                    horzLines: { color: cfg.grid }
                },
                crosshair: {
                    vertLine: { color: cfg.text },
                    horzLine: { color: cfg.text }
                },
                timeScale: {
                    textColor: cfg.text
                },
                rightPriceScale: {
                    textColor: cfg.text
                }
            });
        } catch (e) {
            console.error('Error applying theme options to chart:', e);
        }
    };

    // Apply to oipOIChart (direct lightweight chart instance)
    applyToChart(oipOIChart);

    // Apply to others (TradingViewChart wrapper instances containing .chart)
    if (oipIntrinsicChart && oipIntrinsicChart.chart) applyToChart(oipIntrinsicChart.chart);
    if (oipCEChart && oipCEChart.chart) applyToChart(oipCEChart.chart);
    if (oipPEChart && oipPEChart.chart) applyToChart(oipPEChart.chart);
    if (oipCombinedChart && oipCombinedChart.chart) applyToChart(oipCombinedChart.chart);

    // 6. Force immediate redraw of custom Canvas elements
    oipRequestDraw();
}

// Replay Data Storage (used in refresh/load logic)
let oipFullCandles = null;
let oipFullOptionData = null;



let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

let oipAllStrikes = [];
let oipCurrentPrice = 0;
let oipSymbol = 'NIFTY';
let oipLotSize = 50, oipStrikeStep = 50;
let oipInterval = 'minute';
let oipStrikeCount = 15;
let oipMode = 'change';
let oipIsBusy = false;
let oipIsBusyCandles = false;
let oipIsBusyOI = false;
let oipRafId = null;
let oipCandleTimer = null;
let oipOITimer = null;
let oipHasLoadedCandles = false;
let oipHasLoadedOI = false;
let oipCustomStrikeSetOnLoad = false;
let oipOIChartReady = false;   // true after OI chart receives first data
let oipIntChartReady = false;  // true after Intrinsic chart receives first data
let oipCEChartReady = false;   // true after CE chart receives first data
let oipPEChartReady = false;   // true after PE chart receives first data
let oipCombChartReady = false; // true after Combined chart receives first data
let oipFutureWhitespace = []; // Stores whitespace bars to extend timeline for all charts
let oipAllSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTY MIDCAP 150', 'NIFTY AUTO', 'NIFTY Smallcap 100', 'NIFTY FMCG', 'NIFTY METAL', 'NIFTY PHARMA', 'NIFTY PSU BANK', 'NIFTY IT'];

// DOM Cache for optimized performance
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
    exitAll: null
};



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

    // Individual CE
    if (oipCEEma9Series) oipCEEma9Series.applyOptions({ visible: s9 });
    if (oipCEEma20Series) oipCEEma20Series.applyOptions({ visible: s20 });
    if (oipCEEma50Series) oipCEEma50Series.applyOptions({ visible: s50 });

    // Individual PE
    if (oipPEEma9Series) oipPEEma9Series.applyOptions({ visible: s9 });
    if (oipPEEma20Series) oipPEEma20Series.applyOptions({ visible: s20 });
    if (oipPEEma50Series) oipPEEma50Series.applyOptions({ visible: s50 });
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
    oipElems.exitAll = document.getElementById('oipExitAll');
    oipElems.legendSum = document.getElementById('oipLegendSum');
    oipElems.combStrikeDisplay = document.getElementById('oipCombinedStrikeDisplay');
    oipElems.combSL = document.getElementById('oipCombinedSL');
    oipElems.days = document.getElementById('oipDays');
    oipElems.startDate = document.getElementById('oipStartDate');
    oipElems.endDate = document.getElementById('oipEndDate');
    oipElems.fetchRange = document.getElementById('oipFetchRange');

    // IVP & Alerts
    oipElems.hdrIVP = document.getElementById('hdrIVP');
    oipElems.ivpGaugeBar = document.getElementById('ivpGaugeBar');
    oipElems.ivCrushAlert = document.getElementById('ivCrushAlert');

    // Initial population for custom strikes (will be refined on first load)
    oipUpdateCustomStrikeOptions(50, 25000);
}

/* ── Bootstrap ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    oipInitElems();
    oipInitCharts();



    // Dropdown Logic
    oipElems.symbolInput?.addEventListener('input', (e) => oipRenderDropdown(e.target.value.toUpperCase(), oipElems.symbolList));
    oipElems.showSignals?.addEventListener('change', () => { if (oipOIData?.candles) oipDrawSignals(oipOIData.candles); });
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
        if (window.oipReplayMode) oipResetReplay();
        else oipLoadCandles();
    });

    oipElems.days?.addEventListener('change', () => {
        if (window.oipReplayMode) oipResetReplay();
        else oipLoadCandles();
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

    oipElems.customStrikeDropdown?.addEventListener('change', () => {
        if (oipElems.customStrikeCheck?.checked) {
            console.log(`[OIP] Custom strike changed to: ${oipElems.customStrikeDropdown.value}`);
            oipLoadCandles(true, true);
        }
    });

    oipElems.showSignals?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view?.value);
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
            if (oipElems.customStrikeDropdown) {
                const step = parseInt(oipElems.step?.value) || 50;
                let refPrice = oipCurrentPrice;
                if (!refPrice && oipElems.spotHigh?.value && oipElems.spotLow?.value) {
                    refPrice = (parseFloat(oipElems.spotHigh?.value) + parseFloat(oipElems.spotLow?.value)) / 2;
                }
                if (refPrice > 0) {
                    oipElems.customStrikeDropdown.value = Math.round(refPrice / step) * step;
                }
            }
        }
        oipLoadCandles(true, false);
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

    oipOIChart.subscribeCrosshairMove(param => {
        if (window.oipReplayMode) {
            oipElems.tooltip?.classList.add('hidden');
            return;
        }
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

        // Fair Pricing (Current IV) vs Theory (90d Avg IV)
        const ceFair = nearest.ce_fair || 0, peFair = nearest.pe_fair || 0;
        const ceTheory = nearest.ce_theory || 0, peTheory = nearest.pe_theory || 0;
        const ceIV = nearest.ce_iv ? (nearest.ce_iv * 100).toFixed(1) : '--';
        const peIV = nearest.pe_iv ? (nearest.pe_iv * 100).toFixed(1) : '--';

        if (oipElems.tooltip) oipElems.tooltip.innerHTML = `
            <div class="oip-tt-box">
                <div class="tt-header">STRIKE: ${nearest.strike}</div>
                <div class="tt-grid">
                    <div class="tt-col ce">
                        <div class="type">CALLS (CE)</div>
                        <div class="metric"><span class="l">Fair:</span> <span class="v">₹${ceFair.toFixed(1)}</span></div>
                        <div class="metric"><span class="l">IV:</span> <span class="v">${ceIV}%</span></div>
                    </div>
                    <div class="tt-col pe">
                        <div class="type">PUTS (PE)</div>
                        <div class="metric"><span class="l">Fair:</span> <span class="v">₹${peFair.toFixed(1)}</span></div>
                        <div class="metric"><span class="l">IV:</span> <span class="v">${peIV}%</span></div>
                    </div>
                </div>
            </div>
        `;
        oipElems.tooltip?.classList.remove('hidden');
        if (oipElems.tooltip) {
            oipElems.tooltip.style.left = (param.point.x - oipElems.tooltip.offsetWidth - 15) + 'px';
            oipElems.tooltip.style.top = (param.point.y - oipElems.tooltip.offsetHeight / 2) + 'px';
        }
    });

    // Theme setup and handling
    const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'dark';
    updateOIProfileTheme(activeTheme);

    // Event listener for global theme changes
    window.addEventListener('themechanged', function (e) {
        updateOIProfileTheme(e.detail.theme);
    });

    oipSelectSymbol(oipSymbol);
});

/* ── Lightweight Charts Initialization ──────────────────────── */
function oipInitCharts() {
    const elOI = document.getElementById('oipCandleChart');
    const wrapOI = oipElems.chartWrap;
    if (elOI && typeof LightweightCharts !== 'undefined') {
        oipOIChart = creatBaseChart(elOI);

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
            const pad = (max - min) * 0.1;
            return { priceRange: { minValue: min - pad, maxValue: max + pad } };
        };

        oipOISeries = oipOIChart.addCandlestickSeries({
            ...candleStyle(),
            autoscaleInfoProvider: customAutoscale
        });
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

        /* 
        oipMaxPainSeries = oipOIChart.addLineSeries({ 
            color: '#2563eb', lineWidth: 2, 
            lineStyle: 2, // Dashed
            title: 'Max Pain History',
            lastValueVisible: false,
            priceLineVisible: false,
            autoscaleInfoProvider: () => null
        });
        */



        oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(() => oipRequestDraw());
        oipOIChart.timeScale().subscribeVisibleTimeRangeChange(() => oipRequestDraw());
        const ps = oipOIChart.priceScale('right');
        if (ps && typeof ps.subscribePriceRangeChange === 'function') {
            ps.subscribePriceRangeChange(() => oipRequestDraw());
        }
        oipOIChart.subscribeCrosshairMove(() => oipRequestDraw());
        new ResizeObserver(() => { syncSize(oipOIChart, wrapOI); oipRequestDraw(); }).observe(wrapOI);
    }
    if (window.oipInitSecondaryCharts) window.oipInitSecondaryCharts();
}

function creatBaseChart(el) {
    const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'dark';
    const cfg = OIP_CHART_THEMES[activeTheme] || OIP_CHART_THEMES['dark'];
    return LightweightCharts.createChart(el, {
        width: el.clientWidth || 1200, height: 360,
        layout: { textColor: cfg.text, background: { type: 'solid', color: cfg.bg } },
        grid: { vertLines: { color: cfg.grid }, horzLines: { color: cfg.grid } },
        crosshair: { mode: 0, vertLine: { color: '#9ca3af', style: 3 }, horzLine: { color: '#9ca3af', style: 3, labelBackgroundColor: '#0969da' } },
        timeScale: {
            timeVisible: true,
            textColor: '#6b7280',
            borderColor: 'transparent',
            rightOffset: 20,                 // Minimized space between current candle and Y-axis
            barSpacing: 8,                 // Increased zoom to match user preference
            fixLeftEdge: false,
            fixRightEdge: false,
            shiftVisibleRangeOnNewBar: false
        },
        rightPriceScale: {
            textColor: '#64748b',
            borderColor: 'transparent',
            width: 85,
            autoScale: true,
            visible: true,
            scaleMargins: { top: 0, bottom: 0 },
            entireTextOnly: true
        },
        handleScroll: true, handleScale: true,
        localization: {
            locale: 'en-IN',
            timeFormatter: t => {
                const d = new Date(t * 1000);
                const h = String(d.getUTCHours()).padStart(2, '0');
                const m = String(d.getUTCMinutes()).padStart(2, '0');
                return `${h}:${m}`;
            },
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
    // Cancel any pending frame so we always draw with the latest chart state.
    if (oipRafId) cancelAnimationFrame(oipRafId);
    oipRafId = requestAnimationFrame(oipDrawOIBars);
}

/* ── Canvas OI overlay ────────────────────────────────────── */
let oipLastW = 0, oipLastH = 0;
function oipDrawOIBars() {
    oipRafId = null;
    const canvas = oipElems.canvas;
    const wrap = oipElems.chartWrap;
    if (!canvas || !wrap || !oipOISeries) return;

    const W = wrap.clientWidth;
    const H = wrap.clientHeight;
    const dpr = window.devicePixelRatio || 1;

    if (W !== oipLastW || H !== oipLastH) {
        canvas.width = W * dpr; canvas.height = H * dpr;
        canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
        oipLastW = W; oipLastH = H;
    }

    const ctx = canvas.getContext('2d', { alpha: true });
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!oipAllStrikes.length) return;

    // Resolve dynamic colors based on active theme
    const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'dark';
    const cfg = OIP_CHART_THEMES[activeTheme] || OIP_CHART_THEMES['dark'];
    const lblColor = activeTheme === 'light' ? '#000000' : cfg.text;
    const borderCol = activeTheme === 'light' ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)';

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
    const MAX_BAR_PX = Math.min(plotRight * 0.18, 140);
    const getCE = (s) => (oipMode === 'total' ? (s.ce_oi || 0) : (s.ce_change_in_oi || 0));
    const getPE = (s) => (oipMode === 'total' ? (s.pe_oi || 0) : (s.pe_change_in_oi || 0));

    let maxVal = 1;
    for (let i = 0; i < filtered.length; i++) {
        const s = filtered[i];
        const vC = Math.abs(getCE(s));
        const vP = Math.abs(getPE(s));
        if (vC > maxVal) maxVal = vC;
        if (vP > maxVal) maxVal = vP;
    }
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
        const isLight = (activeTheme === 'light' || activeTheme === 'cream' || activeTheme === 'ocean');
        if (valCE !== 0) {
            ctx.fillStyle = ctx.strokeStyle = (s.strike === ceMaxStr) ? ceColMax : ceCol;
            if (valCE < 0) ctx.strokeRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
            else ctx.fillRect(plotRight - ceW, y - barH - 0.5, ceW, barH);

            // Reverted label positioning back to the right-aligned Y-axis position
            const textStr = fmtL(valCE) + ' C';
            ctx.fillStyle = isLight ? '#000000' : '#ffffff';
            ctx.fillText(textStr, plotRight - 4, y - (barH / 2) - 0.5);
        }
        if (valPE !== 0) {
            ctx.fillStyle = ctx.strokeStyle = (s.strike === peMaxStr) ? peColMax : peCol;
            if (valPE < 0) ctx.strokeRect(plotRight - peW, y + 0.5, peW, barH);
            else ctx.fillRect(plotRight - peW, y + 0.5, peW, barH);

            // Reverted label positioning back to the right-aligned Y-axis position
            const textStr = fmtL(valPE) + ' P';
            ctx.fillStyle = isLight ? '#000000' : '#ffffff';
            ctx.fillText(textStr, plotRight - 4, y + (barH / 2) + 0.5);
        }
    });
    ctx.strokeStyle = borderCol; ctx.beginPath(); ctx.moveTo(plotRight, 0); ctx.lineTo(plotRight, H); ctx.stroke();
}

function oipFilterStrikes(strikes, price, n) {
    if (!strikes || !strikes.length || !price || n >= 999) return strikes;
    let atmI = 0, mindI = Infinity;
    for (let i = 0; i < strikes.length; i++) {
        const d = Math.abs(strikes[i].strike - price);
        if (d < mindI) { mindI = d; atmI = i; }
    }
    return strikes.slice(Math.max(0, atmI - n), Math.min(strikes.length, atmI + n + 1));
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

// Computes EMA9/20/50/100/200 in a single pass — 5x fewer iterations than calling oipCalculateFixedEMA five times.
function oipCalculateAllEMAs(data) {
    if (!data || data.length === 0) return { ema9: [], ema20: [], ema50: [], ema100: [], ema200: [] };
    const periods = [9, 20, 50, 100, 200];
    const k = periods.map(p => 2 / (p + 1));
    const emas = periods.map(() => []);
    let prev = periods.map(() => data[0].close);
    data.forEach(d => {
        if (d.close == null || isNaN(d.close)) return;
        for (let i = 0; i < 5; i++) {
            const val = d.close * k[i] + prev[i] * (1 - k[i]);
            if (!isNaN(val)) { emas[i].push({ time: d.time, value: val }); prev[i] = val; }
        }
    });
    return { ema9: emas[0], ema20: emas[1], ema50: emas[2], ema100: emas[3], ema200: emas[4] };
}

// Computes EMA9/20/50 in a single pass for CE/PE option charts.
function oipCalculate3EMAs(data) {
    if (!data || data.length === 0) return { ema9: [], ema20: [], ema50: [] };
    const periods = [9, 20, 50];
    const k = periods.map(p => 2 / (p + 1));
    const emas = periods.map(() => []);
    let prev = periods.map(() => data[0].close);
    data.forEach(d => {
        if (d.close == null || isNaN(d.close)) return;
        for (let i = 0; i < 3; i++) {
            const val = d.close * k[i] + prev[i] * (1 - k[i]);
            if (!isNaN(val)) { emas[i].push({ time: d.time, value: val }); prev[i] = val; }
        }
    });
    return { ema9: emas[0], ema20: emas[1], ema50: emas[2] };
}

function oipCalculateVWAP(candles) {
    if (!candles || candles.length === 0) return [];
    let cumPV = 0, cumV = 0, lastDate = null;
    const result = [];
    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        // Use UTC methods to match the 'Fake IST Epoch' from server
        const date = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
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
async function oipFullRefresh(resetZoom = false) {
    console.log(`[OIP] Starting Full Refresh (resetZoom=${resetZoom})...`);

    // 0. Cleanup existing timers
    if (oipCandleTimer) clearTimeout(oipCandleTimer);
    if (oipOITimer) clearTimeout(oipOITimer);

    if (resetZoom) {
        oipHasLoadedCandles = false;
        oipHasLoadedOI = false;
        oipCustomStrikeSetOnLoad = false;
    }

    try {
        // 1. Fetch symbols if missing
        if (!oipAllSymbols || !oipAllSymbols.length) {
            try {
                const symRes = await fetch('/api/symbols');
                const symData = await symRes.json();
                if (symData.success) oipAllSymbols = symData.symbols;
            } catch (e) { console.warn('[OIP] Symbol fetch failed:', e); }
        }

        // 2. Fetch metadata for current symbol
        console.log(`[OIP] Fetching metadata for ${oipSymbol}...`);
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

        // 3. Load OI (Blocks until success to get ATM strike)
        console.log(`[OIP] Loading initial OI for ${oipSymbol}...`);
        await oipLoadOI();

        // 4. Load Candles (Blocks until success)
        console.log(`[OIP] Loading initial Candles for ${oipSymbol}...`);
        await oipLoadCandles(true, resetZoom);

    } catch (err) {
        console.error('[OIP] Full Refresh Initialization Err:', err);
    } finally {
        // 5. Start recurring background loops
        console.log('[OIP] Initial load complete. Starting background timers.');
        oipScheduleOILoop(30000);
        oipScheduleCandleLoop(oipIsMarketOpen() ? 1000 : 300000);
    }
}

function oipScheduleOILoop(delay) {
    if (window.oipReplayMode) return;
    if (oipOITimer) clearTimeout(oipOITimer);
    oipOITimer = setTimeout(() => {
        if (!document.hidden) oipOILoop();
        else oipScheduleOILoop(10000);
    }, delay);
}

function oipScheduleCandleLoop(delay) {
    if (window.oipReplayMode) return;
    if (oipCandleTimer) clearTimeout(oipCandleTimer);
    oipCandleTimer = setTimeout(() => {
        if (!document.hidden) oipCandleLoop();
        else oipScheduleCandleLoop(10000);
    }, delay);
}

async function oipCandleLoop() {
    if (oipIsBusyCandles) return;

    const isMarketOpen = oipIsMarketOpen();
    if (oipHasLoadedCandles && !isMarketOpen) {
        oipScheduleCandleLoop(60000);
        return;
    }

    oipIsBusyCandles = true;
    setRefreshBtn(true);
    let success = false;
    try {
        await oipLoadCandles(true, false);
        success = true;
    } catch (err) { console.error('[OIP] Candle Loop Err:', err); }
    finally {
        oipIsBusyCandles = false;
        if (!oipIsBusyOI) setRefreshBtn(false);
        const delay = isMarketOpen ? (success ? 1000 : 2000) : 300000;
        oipScheduleCandleLoop(delay);
    }
}

async function oipOILoop() {
    if (oipIsBusyOI) return;

    const isMarketOpen = oipIsMarketOpen();
    if (oipHasLoadedOI && !isMarketOpen) {
        oipScheduleOILoop(300000);
        return;
    }

    oipIsBusyOI = true;
    let success = false;
    try {
        await oipLoadOI();
        success = true;
    } catch (err) { console.error('[OIP] OI Loop Err:', err); }
    finally {
        oipIsBusyOI = false;
        const delay = isMarketOpen ? (success ? 30000 : 2000) : 300000;
        oipScheduleOILoop(delay);
    }
}

async function oipLoadOI() {
    if (window.oipReplayMode) return;
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

        // Update custom strikes using actual strikes from the option chain
        let resolvedStrike = 0;
        if (oipAllStrikes.length > 0) {
            resolvedStrike = oipUpdateCustomStrikeOptions(oipAllStrikes, oipCurrentPrice);
        }

        // Auto-initialize custom strike to ATM if Custom is checked on first load
        if (!oipCustomStrikeSetOnLoad && resolvedStrike > 0) {
            if (oipElems.customStrikeDropdown) {
                oipElems.customStrikeDropdown.value = resolvedStrike;
            }
            oipCustomStrikeSetOnLoad = true; // Mark as initialized
        }

        oipUpdateHeader(data);
        oipUpdateMaxPainLine(oipCurrentPrice, data.max_pain);
        oipRequestDraw();

    } catch (e) { console.warn('[OIP] OI Load Err:', e); }
}

async function oipLoadCandles(forceFetch = true, resetZoom = false) {
    try {

        const h = parseFloat(oipElems.spotHigh?.value || 0);
        const l = parseFloat(oipElems.spotLow?.value || 0);
        const s = parseInt(oipElems.step?.value || 50);
        const m = parseInt(oipElems.multiplier?.value || 3);

        // Always reset readiness flags to prevent sync jumps between old and new data updates
        oipOIChartReady = false;
        oipIntChartReady = false;
        oipCombChartReady = false;

        const view = oipElems.view?.value || 'combined';

        const needsOptionData = (view !== 'index') && !oipOptionData;
        const autoHL = true; // Favored default for the current template
        const first5m = oipElems.first5mATM?.checked || false;
        const customStrike = (oipElems.customStrikeCheck?.checked && oipElems.customStrikeDropdown?.value) ? oipElems.customStrikeDropdown?.value : '';
        
        let days = parseInt(oipElems.days?.value) || 5;
        let dateRangeParams = "";
        if (window.oipReplayMode && oipElems.startDate?.value && oipElems.endDate?.value) {
            dateRangeParams = `&start_date=${oipElems.startDate.value}&end_date=${oipElems.endDate.value}`;
        }

        if (!forceFetch && oipOIData && !needsOptionData) { oipRefreshLocalView(view, resetZoom); return; }

        const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=${autoHL}&first_5m_atm=${first5m}&custom_strike=${customStrike}${dateRangeParams}&_t=${Date.now()}`;

        const res = await fetch(url);
        const data = await res.json();


        if (!data.success) throw new Error(data.error);

        oipOIData = Object.assign(oipOIData || {}, data);
        const indexCandles = data.candles || [];
        let validCandles = [];

        if (oipOISeries && indexCandles.length) {
            // Hard filter to prevent "Value is null" crash in Candlestick series
            // Explicitly cast to Number, filter NaNs, sort, and deduplicate to guarantee valid schema
            const uniqueTimes = new Set();
            validCandles = indexCandles.map(c => ({
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
                    oipOISeries.setData(validCandles);
                    oipOIChartReady = true;

                    if (resetZoom) {
                        // Jump to current price range without affecting X-axis zoom (no fitContent)
                        oipOIChart.priceScale('right').applyOptions({ autoScale: true });
                    }

                    if (oipVwapSeries) oipVwapSeries.setData(oipCalculateVWAP(validCandles));
                    oipDrawSignals(validCandles);
                } catch (e) { console.warn('[OIP] SetData Err:', e); }
            }

            // Fixed EMAs — single-pass over candles for all 5 periods
            if (oipEma9Series || oipEma20Series || oipEma50Series || oipEma100Series || oipEma200Series) {
                const allEmas = oipCalculateAllEMAs(validCandles);
                if (oipEma9Series) oipEma9Series.setData(allEmas.ema9);
                if (oipEma20Series) oipEma20Series.setData(allEmas.ema20);
                if (oipEma50Series) oipEma50Series.setData(allEmas.ema50);
                if (oipEma100Series) oipEma100Series.setData(allEmas.ema100);
                if (oipEma200Series) oipEma200Series.setData(allEmas.ema200);
            }

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

            oipFullCandles = validCandles;
            oipFullOptionData = oipOptionData;

            if (window.oipReplayMode) {
                if (typeof oipSetupReplaySlider === 'function') oipSetupReplaySlider();
                return;
            }

            oipRefreshLocalView(view, resetZoom);
            oipRequestDraw();
        }
    } catch (e) { console.error('[OIP] Refresh Err:', e); }
}

function oipUpdateHeader(data) {
    const p = data.current_price || 0, pcr = data.pcr_oi || 0, mp = data.max_pain || '--';
    const ivp = data.iv_percentile != null ? data.iv_percentile : '--';
    const ce = data.ce_summary || {}, pe = data.pe_summary || {}, strikes = data.strikes || [];
    const ceSorted = [...strikes].sort((a, b) => (b.ce_oi || 0) - (a.ce_oi || 0));
    const peSorted = [...strikes].sort((a, b) => (b.pe_oi || 0) - (a.pe_oi || 0));

    if (oipElems.hdrPrice) oipElems.hdrPrice.textContent = p.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    if (oipElems.hdrPcr) oipElems.hdrPcr.textContent = pcr.toFixed(2);
    if (oipElems.hdrAtm) oipElems.hdrAtm.textContent = mp;
    if (oipElems.hdrLotSize) oipElems.hdrLotSize.textContent = oipLotSize || '--';

    // Update IVP
    if (oipElems.hdrIVP) oipElems.hdrIVP.textContent = (typeof ivp === 'number') ? ivp.toFixed(1) + '%' : ivp;
    if (typeof ivp === 'number') oipUpdateIVPGauge(ivp);

    // Handle IV Crush Alert
    if (data.iv_crush_alert) {
        if (oipElems.ivCrushAlert) oipElems.ivCrushAlert.classList.remove('hidden');
    } else {
        if (oipElems.ivCrushAlert) oipElems.ivCrushAlert.classList.add('hidden');
    }


    // Mark candles as loaded at least once AFTER all initialization logic (like zoom) is done
    if (!oipHasLoadedCandles) {
        oipHasLoadedCandles = true;
        console.log('[OIP] Candles first load complete');
    }
    if (oipElems.hdrCeOI) oipElems.hdrCeOI.textContent = fmtL(ce.total_oi);
    if (oipElems.hdrCeChg) oipElems.hdrCeChg.textContent = fmtL(ce.change_in_oi);
    if (oipElems.hdrPeOI) oipElems.hdrPeOI.textContent = fmtL(pe.total_oi);
    if (oipElems.hdrPeChg) oipElems.hdrPeChg.textContent = fmtL(pe.change_in_oi);

    if (pcr >= 1.25) {
        if (oipElems.hdrTrend) { oipElems.hdrTrend.textContent = 'Bullish'; oipElems.hdrTrend.className = 'oip-hdr-val grn'; }
    }
    else if (pcr <= 0.6) {
        if (oipElems.hdrTrend) { oipElems.hdrTrend.textContent = 'Bearish'; oipElems.hdrTrend.className = 'oip-hdr-val red'; }
    }
    else {
        if (oipElems.hdrTrend) { oipElems.hdrTrend.textContent = 'Neutral'; oipElems.hdrTrend.className = 'oip-hdr-val'; }
    }

    let atm = '--', mind = Infinity;
    strikes.forEach(s => { const d = Math.abs(s.strike - p); if (d < mind) { mind = d; atm = s.strike; } });
    if (oipElems.hdrAtm) oipElems.hdrAtm.textContent = atm;
}

function oipUpdateIVPGauge(val) {
    if (!oipElems.ivpGaugeBar) return;
    const bar = oipElems.ivpGaugeBar;
    bar.style.width = val + '%';
    bar.classList.remove('cheap', 'neutral', 'expensive');
    if (val < 30) bar.classList.add('cheap');
    else if (val < 70) bar.classList.add('neutral');
    else bar.classList.add('expensive');
}




function oipUpdateMaxPainLine(currentPrice, maxPain) {
    if (window.oipReplayMode) return;
    if (!oipOISeries || !maxPain || maxPain === '--') return;
    const mpValue = parseFloat(maxPain);
    if (isNaN(mpValue)) return;

    if (oipMaxPainLine) {
        try { oipOISeries.removePriceLine(oipMaxPainLine); } catch (e) { }
    }

    if (mpValue > 0) {
        oipMaxPainLine = oipOISeries.createPriceLine({
            price: mpValue,
            color: '#2563eb',
            lineWidth: 2,
            lineStyle: 2, // Dashed
            axisLabelVisible: true,
            title: '',
        });
    }

}


let oipLevelLines = [];
function oipDrawIntrinsicLines(intrinsic, view = 'index') {
    if (!oipIntrinsicChart || !oipIntrinsicSeries) return;
    oipLevelLines.forEach(l => {
        try { oipIntrinsicSeries.removePriceLine(l); if (oipIntrinsicPeSeries) oipIntrinsicPeSeries.removePriceLine(l); } catch (e) { }
    });
    oipLevelLines = [];
    if (!oipElems.showLevels?.checked || !intrinsic) return;
    const { ce_intrinsic, pe_intrinsic } = intrinsic;
    const step = parseInt(oipElems.step?.value) || 50, mult = parseInt(oipElems.multiplier?.value) || 12;
    const candlesCE = (intrinsic.ce_opt_candles || oipOIData?.ce_opt_candles || []);
    const candlesPE = (intrinsic.pe_opt_candles || oipOIData?.pe_opt_candles || []);
    let highest = 0;
    const last = candlesCE[candlesCE.length - 1] || candlesPE[candlesPE.length - 1];
    if (last) {
        const sod = new Date(last.time * 1000).setHours(0, 0, 0, 0) / 1000;
        const curCE = candlesCE.filter(c => c.time >= sod), curPE = candlesPE.filter(c => c.time >= sod);
        if (view === 'ce') highest = Math.max(...curCE.map(c => c.high), 0);
        else if (view === 'pe') highest = Math.max(...curPE.map(c => c.high), 0);
        else highest = Math.max(...curCE.map(c => c.high), ...curPE.map(c => c.high), 0);
    }
    const ceLevels = [], peLevels = [];
    for (let i = 1; i <= mult || (ce_intrinsic + step * i) < highest + (2 * step); i++) { ceLevels.push(ce_intrinsic + step * i); if (i > 60) break; }
    for (let i = 1; i <= mult || (pe_intrinsic + step * i) < highest + (2 * step); i++) { peLevels.push(pe_intrinsic + step * i); if (i > 60) break; }
    if ((view === 'ce' || view === 'combined' || view === 'index') && ce_intrinsic > 0) {
        oipLevelLines.push(oipIntrinsicSeries.createPriceLine({ price: ce_intrinsic, color: '#10b981', lineWidth: 2, title: 'CE IV' }));
        ceLevels.forEach(lvl => {
            if (lvl > 0) oipLevelLines.push(oipIntrinsicSeries.createPriceLine({ price: lvl, color: '#10b981', lineWidth: 1, title: '' }));
        });
    }
    if ((view === 'pe' || view === 'combined' || view === 'index') && pe_intrinsic > 0) {
        const s = oipIntrinsicPeSeries || oipIntrinsicSeries;
        oipLevelLines.push(s.createPriceLine({ price: pe_intrinsic, color: '#8b5cf6', lineWidth: 2, title: 'PE IV' }));
        peLevels.forEach(lvl => {
            if (lvl > 0) oipLevelLines.push(s.createPriceLine({ price: lvl, color: '#8b5cf6', lineWidth: 1, title: '' }));
        });
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
    const base = {
        priceLineVisible: false, lastValueVisible: true,
        crosshairMarkerVisible: false, visible: false
    };
    oipPremiumSeries.entry = chart.addLineSeries({ ...base, color: '#4caf50', lineWidth: 2 });
    oipPremiumSeries.current = chart.addLineSeries({ ...base, color: '#2196f3', lineWidth: 2 });
    oipPremiumSeries.t1 = chart.addLineSeries({ ...base, color: '#e040fb', lineWidth: 1 });
    oipPremiumSeries.t2 = chart.addLineSeries({ ...base, color: '#f97316', lineWidth: 1 });
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
        Object.values(oipPremiumSeries).forEach(s => { try { s?.applyOptions({ visible: false }); } catch (e) { } });
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
            else if (c_c != null) cur = c_c;
            else if (p_c != null) cur = p_c;

            // Entry Premium Curve = (CE_VWAP + PE_VWAP) / 2
            if (ce_vwap != null && pe_vwap != null) entry = (ce_vwap + pe_vwap) / 2;
            else if (ce_vwap != null) entry = ce_vwap;
            else if (pe_vwap != null) entry = pe_vwap;
        } else if (view === 'ce') {
            cur = ceC ? ceC.close : null;
            entry = ce_vwap;
        } else if (view === 'pe') {
            cur = peC ? peC.close : null;
            entry = pe_vwap;
        }

        if (entry != null && !isNaN(entry)) {
            entryData.push({ time: t, value: entry });
            t1Data.push({ time: t, value: entry + tgtDist });
            t2Data.push({ time: t, value: entry + 2 * tgtDist });
        }
        if (cur != null && !isNaN(cur)) {
            currentData.push({ time: t, value: cur });
        }
    });

    // ── Push data and make visible
    try { oipPremiumSeries.entry.setData(entryData); oipPremiumSeries.entry.applyOptions({ visible: true }); } catch (e) { }
    try { oipPremiumSeries.current.setData(currentData); oipPremiumSeries.current.applyOptions({ visible: true }); } catch (e) { }
    try { oipPremiumSeries.t1.setData(t1Data); oipPremiumSeries.t1.applyOptions({ visible: true }); } catch (e) { }
    try { oipPremiumSeries.t2.setData(t2Data); oipPremiumSeries.t2.applyOptions({ visible: true }); } catch (e) { }
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
    // 9:15 AM (555 mins) to 3:30 PM (930 mins)
    return mins >= 555 && mins <= 930;
}

/**
 * Calculates the sum of CE and PE premiums for Straddle/Strangle tracking.
 * Aligns data by timestamp.
 */
function oipCalculateCombinedPremium(ceData, peData) {
    if (!ceData || !peData || ceData.length === 0 || peData.length === 0) return [];

    // Map PE data by time for fast lookup (handling both raw and formatted data)
    const peMap = new Map();
    peData.forEach(c => {
        const t = c.time || (c.date ? Math.floor(new Date(c.date).getTime() / 1000) : null);
        if (t) peMap.set(t, c.close || c.c);
    });

    const combined = [];
    ceData.forEach(c => {
        const t = c.time || (c.date ? Math.floor(new Date(c.date).getTime() / 1000) : null);
        if (t && peMap.has(t)) {
            const cePrice = c.close || c.c;
            const pePrice = peMap.get(t);
            combined.push({
                time: t,
                value: parseFloat((cePrice + pePrice).toFixed(2))
            });
        }
    });

    return combined.sort((a, b) => a.time - b.time);
}

function oipRefreshLocalView(view, resetZoom = false, endIndex = null) {
    if (!oipOIData || !oipIntrinsicChart) return;

    // Use full cached data if in replay mode
    let masterData = (typeof oipOISeries !== 'undefined' && oipOISeries) ? oipOISeries.data() : [];
    if (window.oipReplayMode && oipFullCandles) {
        masterData = oipFullCandles;
    }

    if (!masterData.length) return; // Prevent rendering if main chart is not ready

    // Slice master data if endIndex provided
    if (endIndex !== null) {
        masterData = masterData.slice(0, endIndex + 1);
    }

    const getSec = (t) => {
        if (typeof t === 'number') return t < 10000000000 ? t : Math.floor(t / 1000);
        if (typeof t === 'string') return Math.floor(new Date(t).getTime() / 1000);
        return null;
    };

    if (view === 'index') {
        oipIntrinsicChart.update(masterData, null, resetZoom);
        oipIntChartReady = true;  // Intrinsic chart now has data — safe to sync
        if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(masterData.filter(d => d.open !== undefined)));
        if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
        if (oipIntrinsicChart.setMarkers) oipIntrinsicChart.setMarkers([], []);
    } else {
        let optionData = oipOptionData || [];
        if (window.oipReplayMode && oipFullOptionData) {
            optionData = oipFullOptionData;
        }

        if (endIndex !== null) {
            const lastTime = masterData[masterData.length - 1].time;
            optionData = optionData.filter(d => getSec(d.time || d.date) <= lastTime);
        }

        const ceRaw = optionData.filter(c => c.type === 'CE');
        const peRaw = optionData.filter(c => c.type === 'PE');

        // --- MASTER TIMELINE ALIGNMENT ---
        // Map all option data EXACTLY to the main chart's timeline (which already includes whitespace).
        const alignToMaster = (rawData) => {
            const dataMap = new Map();
            let firstValidPrice = null;
            rawData.forEach(c => {
                const t = getSec(c.time || c.date);
                if (t) {
                    dataMap.set(t, c);
                    if (firstValidPrice === null) firstValidPrice = parseFloat(c.close || c.c);
                }
            });
            const anchorPrice = firstValidPrice || 100;

            return masterData.map((mc, index) => {
                const optCandle = dataMap.get(mc.time);
                if (optCandle) {
                    return {
                        time: mc.time,
                        open: parseFloat(optCandle.open || optCandle.o),
                        high: parseFloat(optCandle.high || optCandle.h),
                        low: parseFloat(optCandle.low || optCandle.l),
                        close: parseFloat(optCandle.close || optCandle.c),
                        volume: parseFloat(optCandle.volume || 0)
                    };
                }

                // INVISIBLE ANCHOR: Prevent Lightweight Charts from trimming leading whitespace
                if (index === 0) {
                    return {
                        time: mc.time,
                        open: anchorPrice, high: anchorPrice, low: anchorPrice, close: anchorPrice,
                        color: 'transparent', borderColor: 'transparent', wickColor: 'transparent'
                    };
                }

                return { time: mc.time }; // Insert whitespace to maintain exact alignment
            });
        };

        // Align to Master Timeline
        const ceData = alignToMaster(ceRaw);
        const peData = alignToMaster(peRaw);

        const ce_levels = oipOIData.intrinsic?.ce_levels || [];
        const pe_levels = oipOIData.intrinsic?.pe_levels || [];

        // Compute both VWAPs once — reused by individual series and combined VWAP below.
        const ceVwapData = oipCalculateVWAP(ceRaw);
        const peVwapData = oipCalculateVWAP(peRaw);

        // Update Individual Premium Chart
        if (view === 'combined') {
            oipIntrinsicChart.update(ceData, peData, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData(ceVwapData);
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(peVwapData);
        } else if (view === 'ce') {
            oipIntrinsicChart.update(ceData, null, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData(ceVwapData);
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
        } else {
            oipIntrinsicChart.update(null, peData, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData([]);
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(peVwapData);
        }

        // Update NEW Combined Premium Chart
        if (oipCombinedChart && ceRaw.length > 0 && peRaw.length > 0) {
            const sumData = oipCalculateCombinedPremium(ceRaw, peRaw);

            // Align combined data to Master Timeline
            const sumDataMap = new Map();
            let firstSumValue = null;
            sumData.forEach(c => {
                const t = getSec(c.time || c.date);
                if (t) {
                    sumDataMap.set(t, c);
                    if (firstSumValue === null) firstSumValue = c.value;
                }
            });
            const sumAnchor = firstSumValue || 100;

            const sumAlignedRaw = masterData.map((mc, index) => {
                const sCandle = sumDataMap.get(mc.time);
                if (sCandle) return { time: mc.time, value: sCandle.value };

                if (index === 0) return { time: mc.time, value: sumAnchor, color: 'transparent' };
                return { time: mc.time };
            });
            const sumAligned = sumAlignedRaw;

            oipCombinedChart.update(sumAligned, null, resetZoom);
            oipCombChartReady = true;

            // Update the UI legend immediately
            if (sumData && sumData.length > 0) {
                const strike = oipElems.customStrikeDropdown?.value || '--';
                const lastSum = sumData[sumData.length - 1].value;
                if (document.getElementById('oipCombinedStrikeDisplay')) {
                    document.getElementById('oipCombinedStrikeDisplay').textContent = `${strike} CE + PE`;
                }
                if (document.getElementById('oipLegendSum')) {
                    document.getElementById('oipLegendSum').textContent = `SUM: ${lastSum.toFixed(2)}`;
                }
            }

            // Combined VWAP — reuse ceVwapData/peVwapData already computed above (no extra O(n) passes).
            if (oipCombinedVwapSeries) {
                const combinedVwapData = [];
                const peMap = new Map(peVwapData.map(d => [d.time, d.value]));
                ceVwapData.forEach(ceV => {
                    const peVal = peMap.get(ceV.time);
                    if (peVal !== undefined) combinedVwapData.push({ time: ceV.time, value: ceV.value + peVal });
                });
                oipCombinedVwapSeries.setData(combinedVwapData);
            }
            // Update Sum & Strike Legend
            if (oipElems.legendSum && sumData.length > 0) {
                oipElems.legendSum.innerText = `SUM: ₹${sumData[sumData.length - 1].value.toFixed(1)}`;
            }
            if (oipElems.combStrikeDisplay) {
                const strike = oipElems.customStrikeDropdown?.value || '--';
                oipElems.combStrikeDisplay.innerText = `STRIKE: ${strike}`;
            }
        }

        oipIntChartReady = true;
        oipCEChartReady = true;
        oipPEChartReady = true;
        
        // Clear signals from Intrinsic chart
        if (oipIntrinsicChart) oipIntrinsicChart.setMarkers([], []);

        // Update Individual CE Only Chart
        if (oipCEChart) {
            oipCEChart.update(ceData, null, resetZoom);
            // EMAs — single pass for all 3 CE periods
            if (oipCEEma9Series || oipCEEma20Series || oipCEEma50Series) {
                const ceEmas = oipCalculate3EMAs(ceRaw);
                if (oipCEEma9Series) oipCEEma9Series.setData(ceEmas.ema9);
                if (oipCEEma20Series) oipCEEma20Series.setData(ceEmas.ema20);
                if (oipCEEma50Series) oipCEEma50Series.setData(ceEmas.ema50);
            }

            const strike = oipElems.customStrikeDropdown?.value || '--';
            if (document.getElementById('oipLegendCEOnly')) document.getElementById('oipLegendCEOnly').textContent = `${strike} CE`;
        }

        // Update Individual PE Only Chart
        if (oipPEChart) {
            oipPEChart.update(peData, null, resetZoom);
            // EMAs — single pass for all 3 PE periods
            if (oipPEEma9Series || oipPEEma20Series || oipPEEma50Series) {
                const peEmas = oipCalculate3EMAs(peRaw);
                if (oipPEEma9Series) oipPEEma9Series.setData(peEmas.ema9);
                if (oipPEEma20Series) oipPEEma20Series.setData(peEmas.ema20);
                if (oipPEEma50Series) oipPEEma50Series.setData(peEmas.ema50);
            }

            const strike = oipElems.customStrikeDropdown?.value || '--';
            if (document.getElementById('oipLegendPEOnly')) document.getElementById('oipLegendPEOnly').textContent = `${strike} PE`;
        }
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
            { type: 'cpr', min: Math.min(tc, bc), max: Math.max(tc, bc), times: curr.times }
        ];

        daysData.push({
            times: curr.times,
            levels: { prevH: oH, prevL: oL, r1, r2, r3, r4, s1, s2, s3, s4, cr3, cs3, pp, tc, bc },
            boxes: boxes
        });
    }
    return daysData;
}

let oipCprSeriesMap = {}; // Reuse series instead of recreate
function oipDrawCpr(candles) {
    if (!oipOIChart || !oipOISeries) return;

    const show = oipElems.showCpr?.checked;
    Object.values(oipCprSeriesMap).forEach(s => s.setData([]));
    if (!show || !candles || !candles.length) return;

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

    daysData.forEach((day, dayIdx) => {
        Object.keys(day.levels).forEach(key => {
            const seriesKey = `line_${key}_${dayIdx}`;
            let series = oipCprSeriesMap[seriesKey];
            if (!series) {
                series = oipOIChart.addLineSeries({
                    ...lineStyles[key],
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null
                });
                oipCprSeriesMap[seriesKey] = series;
            }
            const val = day.levels[key];
            series.setData(val != null && !isNaN(val) ? day.times.map(t => ({ time: t, value: val })) : []);
        });

        day.boxes.forEach((box, boxIdx) => {
            const seriesKey = `box_${box.type}_${dayIdx}_${boxIdx}`;
            let series = oipCprSeriesMap[seriesKey];
            if (!series) {
                const col = boxColors[box.type];
                series = oipOIChart.addBaselineSeries({
                    baseValue: { type: 'price', price: box.min },
                    topFillColor1: col, topFillColor2: col, topLineColor: 'transparent',
                    bottomFillColor1: col, bottomFillColor2: col, bottomLineColor: 'transparent',
                    lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null
                });
                oipCprSeriesMap[seriesKey] = series;
            }
            series.applyOptions({ baseValue: { type: 'price', price: box.min } });
            series.setData(day.times.map(t => ({ time: t, value: box.max })));
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

    if (oipElems.spotHigh) oipElems.spotHigh.value = Math.round(rh * 100) / 100; if (oipElems.spotLow) oipElems.spotLow.value = Math.floor(rl * 100) / 100;
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
        if (r.success) {
            showNotification(`✅ Success! ${action} ${side} ${strike}`, 'success');
        } else {
            // Build detailed error summary from broker results
            let details = '';
            if (r.summary && Array.isArray(r.summary)) {
                details = r.summary.map(s => {
                    const broker = s.broker.replace(/_/g, ' ').toUpperCase();
                    const msg = s.result?.error || (s.result?.success ? 'OK' : 'Err');
                    return `• ${broker}: ${msg}`;
                }).join('\n');
            }
            const mainErr = r.error || 'Order Failed';
            showNotification(`❌ ${mainErr}${details ? '\n' + details : ''}`, 'error');
        }
    } catch (e) {
        showNotification(`Order error: ${e.message}`, 'error');
    }
    finally { btn.disabled = false; btn.title = ot; }
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

    // Reset flags for symbol switch
    oipCustomStrikeSetOnLoad = false;

    oipFullRefresh(true);
}

function oipUpdateCustomStrikeOptions(strikes, centerPrice = null) {
    if (!oipElems.customStrikeDropdown) return;

    let sortedStrikes = [];
    if (strikes && strikes.length > 0) {
        // Extract unique strike prices and sort them
        sortedStrikes = [...new Set(strikes.map(s => parseFloat(s.strike)))].sort((a, b) => a - b);

        // Calculate the most common strike difference (step) from the actual chain
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
                if (counts[d] > maxCount) {
                    maxCount = counts[d];
                    commonStep = d;
                }
            });

            if (commonStep > 0 && commonStep !== oipStrikeStep) {
                oipStrikeStep = commonStep;
                if (oipElems.step) oipElems.step.value = commonStep;
                console.log(`[OIP] Calculated strike step from chain: ${commonStep}`);
            }
        }
    }

    const refPrice = centerPrice || oipCurrentPrice || 25000;
    const step = oipStrikeStep || 50;
    const atm = Math.round(refPrice / step) * step;

    if (sortedStrikes.length > 20) {
        // Find index closest to ATM
        let atmIndex = sortedStrikes.findIndex(s => s >= refPrice);
        if (atmIndex === -1) atmIndex = sortedStrikes.length - 1;

        let start = Math.max(0, atmIndex - 10);
        let end = Math.min(sortedStrikes.length, start + 20);

        // Adjust start if end hit the boundary to keep 20 items if possible
        if (end === sortedStrikes.length) {
            start = Math.max(0, end - 20);
        }

        sortedStrikes = sortedStrikes.slice(start, end);
    }

    let opts = '';
    if (sortedStrikes.length > 0) {
        // Use actual strikes from the chain
        sortedStrikes.forEach(s => {
            opts += `<option value="${s}">${s}</option>`;
        });
    } else {
        // Fallback: Generate strikes if chain not available yet
        for (let i = -10; i <= 10; i++) {
            const s = atm + (i * step);
            if (s <= 0) continue;
            opts += `<option value="${s}">${s}</option>`;
        }
    }

    const currentVal = oipElems.customStrikeDropdown.value;
    oipElems.customStrikeDropdown.innerHTML = opts;

    let finalStrike = atm;
    // Snap to ATM if this is a fresh load/symbol change
    if (centerPrice && !oipCustomStrikeSetOnLoad) {
        oipElems.customStrikeDropdown.value = atm;
        oipCustomStrikeSetOnLoad = true;
        finalStrike = atm;
    } else if (currentVal && opts.includes(`value="${currentVal}"`)) {
        oipElems.customStrikeDropdown.value = currentVal;
        finalStrike = parseFloat(currentVal);
    } else {
        oipElems.customStrikeDropdown.value = atm;
        finalStrike = atm;
    }
    return finalStrike;
}

let oipRSISeriesObj = null;
let oipSignalMarkers = [];
let oipRSIMarkers = [];

function oipUpdateAllMarkers() {
    if (!oipOISeries) return;
    const combined = [...oipSignalMarkers, ...oipRSIMarkers].sort((a, b) => a.time - b.time);
    oipOISeries.setMarkers(combined);
}

function oipDrawSignals(candles) {
    if (!oipOIChart || !oipOISeries) return;
    const show = oipElems.showSignals?.checked;
    if (!show || !candles || candles.length < 20) {
        oipSignalMarkers = [];
        oipUpdateAllMarkers();
        return;
    }
    const ema9 = oipCalculateFixedEMA(candles, 9);
    const ema20 = oipCalculateFixedEMA(candles, 20);
    const ema50 = oipCalculateFixedEMA(candles, 50);
    const e9Map = new Map(ema9.map(d => [d.time, d.value]));
    const e20Map = new Map(ema20.map(d => [d.time, d.value]));
    const e50Map = new Map(ema50.map(d => [d.time, d.value]));
    
    const signals = [];
    let buyState = 'IDLE'; // IDLE, BELOW, CROSSED, CONFIRMED
    let sellState = 'IDLE'; // IDLE, ABOVE, CROSSED, CONFIRMED
    let inBuyTrade = false;
    let inSellTrade = false;

    for (let i = 1; i < candles.length - 1; i++) {
        const c = candles[i], t = c.time;
        const e9 = e9Map.get(t), e20 = e20Map.get(t), e50 = e50Map.get(t);
        if (!e9 || !e20 || !e50) continue;

        // --- SL LOGIC ---
        if (inBuyTrade && c.close < e50) {
            signals.push({ time: t, position: 'aboveBar', color: '#800000', shape: 'circle', text: 'B SL' });
            inBuyTrade = false;
        }
        if (inSellTrade && c.close > e50) {
            signals.push({ time: t, position: 'belowBar', color: '#800000', shape: 'circle', text: 'S SL' });
            inSellTrade = false;
        }

        // --- BUY LOGIC ---
        // 1. Preparation: Below both EMAs
        if (c.close < e9 && c.close < e20) {
            buyState = 'BELOW';
        } 
        // 2. Crossing: Price crosses above both EMAs
        else if (buyState === 'BELOW' && c.close > e9 && c.close > e20) {
            buyState = 'CROSSED';
        }
        // 3. Confirmation: One candle completely above both EMAs (not touching)
        else if (buyState === 'CROSSED') {
            const completelyAbove = c.open > e9 && c.open > e20 && 
                                  c.close > e9 && c.close > e20 && 
                                  c.high > e9 && c.high > e20 && 
                                  c.low > e9 && c.low > e20;
            if (completelyAbove) {
                buyState = 'CONFIRMED';
            } else if (c.close < e9 && c.close < e20) {
                buyState = 'BELOW'; // Reset if it fails and goes back below
            }
        }
        // 4. Entry: Touches 9 or 20 EMA and closes above BOTH + above EMA 50
        else if (buyState === 'CONFIRMED' && !inBuyTrade) {
            const touched = (c.low <= e9 || c.low <= e20);
            const closedAboveBoth = (c.close > e9 && c.close > e20);
            const aboveEma50 = c.close > e50;
            const isGreen = c.close > c.open;
            
            if (touched && closedAboveBoth && isGreen && aboveEma50) {
                signals.push({ 
                    time: t, 
                    position: 'belowBar', 
                    color: '#22c55e', 
                    shape: 'arrowUp', 
                    text: 'BUY' 
                });
                buyState = 'IDLE'; 
                inBuyTrade = true;
                inSellTrade = false; // Reset other side
            } else if (c.close < e9 && c.close < e20) {
                buyState = 'BELOW'; 
            }
        }

        // --- SELL LOGIC ---
        // 1. Preparation: Above both EMAs
        if (c.close > e9 && c.close > e20) {
            sellState = 'ABOVE';
        }
        // 2. Crossing: Price crosses below both EMAs
        else if (sellState === 'ABOVE' && c.close < e9 && c.close < e20) {
            sellState = 'CROSSED';
        }
        // 3. Confirmation: One candle completely below both EMAs (not touching)
        else if (sellState === 'CROSSED') {
            const completelyBelow = c.open < e9 && c.open < e20 && 
                                  c.close < e9 && c.close < e20 && 
                                  c.high < e9 && c.high < e20 && 
                                  c.low < e9 && c.low < e20;
            if (completelyBelow) {
                sellState = 'CONFIRMED';
            } else if (c.close > e9 && c.close > e20) {
                sellState = 'ABOVE'; // Reset if it fails and goes back above
            }
        }
        // 4. Entry: Touches 9 or 20 EMA and closes below BOTH + below EMA 50
        else if (sellState === 'CONFIRMED' && !inSellTrade) {
            const touched = (c.high >= e9 || c.high >= e20);
            const closedBelowBoth = (c.close < e9 && c.close < e20);
            const belowEma50 = c.close < e50;
            const isRed = c.close < c.open;

            if (touched && closedBelowBoth && isRed && belowEma50) {
                signals.push({ 
                    time: t, 
                    position: 'aboveBar', 
                    color: '#ef4444', 
                    shape: 'arrowDown', 
                    text: 'SELL' 
                });
                sellState = 'IDLE';
                inSellTrade = true;
                inBuyTrade = false; // Reset other side
            } else if (c.close > e9 && c.close > e20) {
                sellState = 'ABOVE'; 
            }
        }
    }
    oipSignalMarkers = signals;
    oipUpdateAllMarkers();
}

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
        oipRSIMarkers = [];
        oipUpdateAllMarkers();
        return;
    }
    Object.values(oipRSISeriesObj).forEach(s => s.applyOptions({ visible: true }));
    const rsiData = oipCalculateRSISnR(candles);
    if (rsiData) {
        oipRSISeriesObj.ob.setData(rsiData.ob_series);
        oipRSISeriesObj.bull.setData(rsiData.bull_series);
        oipRSISeriesObj.os.setData(rsiData.os_series);
        oipRSISeriesObj.bear.setData(rsiData.bear_series);
        if (rsiData.markers) {
            rsiData.markers.forEach(m => m.size = 1);
            oipRSIMarkers = rsiData.markers;
            oipUpdateAllMarkers();
        }
    }
}

function oipCalculateRSISnR(candles) {
    if (!candles || candles.length < 15) return null;
    const len = 14;
    const rsi = new Array(candles.length).fill(null);
    let gains = 0, losses = 0;

    for (let i = 1; i <= len; i++) {
        const diff = candles[i].close - candles[i - 1].close;
        if (diff >= 0) gains += diff;
        else losses -= diff;
    }
    let avgGain = gains / len;
    let avgLoss = losses / len;
    rsi[len] = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));

    for (let i = len + 1; i < candles.length; i++) {
        const diff = candles[i].close - candles[i - 1].close;
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

    const L_OB = new Array(candles.length).fill(null);
    const L_OS = new Array(candles.length).fill(null);

    for (let i = 1; i < candles.length; i++) {
        const c = candles[i], r = rsi[i], r_prev = rsi[i - 1];
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
            const pC = candles[i - 1], ppC = candles[i - 2];
            const pL_OB = L_OB[i - 1], ppL_OB = L_OB[i - 2], pL_OS = L_OS[i - 1], ppL_OS = L_OS[i - 2];

            if (pL_OB !== null && i < candles.length - 1) {
                const setup_ob_rej = (pC.close < pL_OB) && (pC.high >= pL_OB || ppC.close >= ppL_OB);
                const short_entry = setup_ob_rej && (c.close < pC.low);
                if (short_entry) {
                    in_short = true; in_long = false; short_sl = pC.high;
                    markers.push({ time: c.time, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'ENTRY' });
                }
            }
            if (pL_OS !== null && i < candles.length - 1) {
                const setup_os_rej = (pC.close > pL_OS) && (pC.low <= pL_OS || ppC.close <= ppL_OS);
                const long_entry = setup_os_rej && (c.close > pC.high);
                if (long_entry) {
                    in_long = true; in_short = false; long_sl = pC.low;
                    markers.push({ time: c.time, position: 'belowBar', color: '#14b8a6', shape: 'arrowUp', text: 'ENTRY' });
                }
            }

            if (in_short && i < candles.length - 1) {
                const tgtHit = level_bear !== null && c.low <= level_bear;
                const slHit = short_sl !== null && c.high >= short_sl;
                if (tgtHit || slHit) {
                    in_short = false;
                    markers.push({ time: c.time, position: 'aboveBar', color: tgtHit ? '#f97316' : '#800000', shape: tgtHit ? 'circle' : 'circle', text: tgtHit ? '🎯' : 'S SL' });
                }
            }
            if (in_long && i < candles.length - 1) {
                const tgtHit = level_bull !== null && c.high >= level_bull;
                const slHit = long_sl !== null && c.low <= long_sl;
                if (tgtHit || slHit) {
                    in_long = false;
                    markers.push({ time: c.time, position: 'belowBar', color: tgtHit ? '#f97316' : '#800000', shape: tgtHit ? 'circle' : 'circle', text: tgtHit ? '🎯' : 'B SL' });
                }
            }
        }
    }
    return { ob_series, os_series, bull_series, bear_series, markers };
}




