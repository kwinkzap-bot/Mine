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
// CVWAP (current-session) / PVWAP (previous-session) — main index chart + premium chart
let oipCvwapSeries = null, oipPvwapSeries = null;
let oipCvwapIntSeries = null, oipPvwapIntSeries = null;
let oipCvwapIntPeSeries = null, oipPvwapIntPeSeries = null;
// CVWAP / PVWAP on the individual CE Only and PE Only charts
let oipCECvwapSeries = null, oipCEPvwapSeries = null;
let oipPECvwapSeries = null, oipPEPvwapSeries = null;
// EMA/CPR/RSI series state declared in oi_indicators.js
let oipCEChart = null;
let oipPEChart = null;
let oipCESeries = null;
let oipPESeries = null;
let oipMaxPainLine = null;

/* ── Theme Setup ────────────────────────────────────────── */
const OIP_CHART_THEMES = {
    'light': { bg: '#ffffff', text: '#374151', grid: '#f0f0f0' },
    'dark': { bg: '#111827', text: '#94a3b8', grid: 'rgba(255, 255, 255, 0.06)' },
    'forest': { bg: '#0a1410', text: '#6ba88f', grid: 'rgba(16, 185, 129, 0.06)' },
    'cream': { bg: '#ffffff', text: '#7c7267', grid: 'rgba(180, 83, 9, 0.05)' },
    'ocean': { bg: '#ffffff', text: '#475569', grid: 'rgba(2, 132, 199, 0.05)' }
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
                    vertLine: { color: '#9ca3af', style: 3 },
                    horzLine: { color: '#9ca3af', style: 3 }
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
    // 6. Force immediate redraw of custom Canvas elements
    oipRequestDraw();
}

// Replay Data Storage (used in refresh/load logic)
let oipFullCandles = null;
let oipFullOptionData = null;



let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

// Premium Strike (Prem. Str.) mode state
let oipPremiumStrikeData = null;           // Cached result from /api/oi-profile/premium-strikes
const oipPremStrikeLines = { ce: [], pe: [] }; // Price-line handles for cleanup

let oipAllStrikes = [];
let oipCurrentPrice = 0;
let oipSymbol = 'NIFTY';
let oipLotSize = 50, oipStrikeStep = 50;
let oipInterval = '5minute';
let oipStrikeCount = 15;
let oipMode = 'off';
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
let _oipChartsSyncedOnce = false; // true after the first cross-chart range sync
let oipFutureWhitespace = []; // Stores whitespace bars to extend timeline for all charts
let oipAllSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTY MIDCAP 150', 'NIFTY AUTO', 'NIFTY Smallcap 100', 'NIFTY FMCG', 'NIFTY METAL', 'NIFTY PHARMA', 'NIFTY PSU BANK', 'NIFTY IT'];

// DOM Cache for optimized performance
const oipElems = {
    symbolInput: null, symbolList: null, interval: null,
    spotHigh: null, spotLow: null, step: null, multiplier: null,
    view: null, showVwapOI: null, showVwapInt: null, showCVWAP: null, showPVWAP: null,
    showCpr: null, showEMA: null, showRSI: null, showOIBars: null, autoHL: null, chartWrap: null, canvas: null,
    tooltip: null, refreshIcon: null, itmCE: null, itmPE: null,
    hdrPrice: null, hdrPcr: null, hdrPcrCard: null, hdrMaxPain: null, hdrCeOI: null,
    hdrCeChg: null, hdrPeOI: null,
    hdrPeChg: null, hdrTrend: null, hdrAtm: null, brokerSelect: null,
    showPremium: null, showSignals: null, first5mATM: null, targetDistance: null, customStrikeCheck: null, customStrikeDropdown: null,
    strikeMode: null, ceStrikeDropdown: null, peStrikeDropdown: null, premExtra: null,
    showEma9: null, showEma20: null, showEma50: null, showEma100: null, showEma200: null,
    exitAll: null,
    slPrice: null, slCEBtn: null, slPEBtn: null
};



// oipUpdateEmaVisibility — defined in oi_indicators.js

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
    oipElems.showCVWAP = document.getElementById('oipShowCVWAP');
    oipElems.showPVWAP = document.getElementById('oipShowPVWAP');
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
    oipElems.hdrPcrCard = document.getElementById('hdrPcrCard');
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
    oipElems.strikeMode = document.getElementById('oipStrikeMode');
    oipElems.premExtra = document.getElementById('oipPremExtraDropdown');
    oipElems.ceStrikeDropdown = document.getElementById('oipCEStrikeDropdown');
    oipElems.peStrikeDropdown = document.getElementById('oipPEStrikeDropdown');
    oipElems.targetDistance = document.getElementById('oipTargetDistance');
    oipElems.showEma9 = document.getElementById('oipShowEma9');
    oipElems.showEma20 = document.getElementById('oipShowEma20');
    oipElems.showEma50 = document.getElementById('oipShowEma50');
    oipElems.showEma100 = document.getElementById('oipShowEma100');
    oipElems.exitAll = document.getElementById('oipExitAll');
    oipElems.slPrice  = document.getElementById('oipLimitPrice'); // shared with BUY/SELL
    oipElems.slCEBtn  = document.getElementById('oipSLCE');
    oipElems.slPEBtn  = document.getElementById('oipSLPE');
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
// Applies the CVWAP/PVWAP checkbox state to every series across all charts
// (main index, Opt Prem, CE Only, PE Only). Used on init and on toggle.
function oipSyncVwapVisibility() {
    const cv = oipElems.showCVWAP?.checked ?? false;
    const pv = oipElems.showPVWAP?.checked ?? false;
    [oipCvwapSeries, oipCvwapIntSeries, oipCvwapIntPeSeries, oipCECvwapSeries, oipPECvwapSeries]
        .forEach(s => { try { s?.applyOptions({ visible: cv }); } catch (e) {} });
    [oipPvwapSeries, oipPvwapIntSeries, oipPvwapIntPeSeries, oipCEPvwapSeries, oipPEPvwapSeries]
        .forEach(s => { try { s?.applyOptions({ visible: pv }); } catch (e) {} });
}

document.addEventListener('DOMContentLoaded', () => {
    oipInitElems();
    oipInitCharts();
    oipInitIndicatorsPopup('oip-ind-profile');
    // Charts are built before the popup restores persisted checkbox state, so the
    // VWAP/CVWAP/PVWAP series are created with stale (default) visibility. Re-sync now.
    oipSyncVwapVisibility();

    // OI Bar popup — lazy-loads the Open Interest page inside an iframe
    (() => {
        const btn = document.getElementById('oipOIBarBtn');
        const modal = document.getElementById('oipOIBarModal');
        const closeBtn = document.getElementById('oipOIBarModalClose');
        const frame = document.getElementById('oipOIBarFrame');
        if (!btn || !modal) return;

        // .container uses content-visibility:auto, which — like transform/filter —
        // creates a containing block for position:fixed descendants. That made the
        // "fixed" overlay scroll with the page instead of pinning to the viewport.
        // Move it to be a direct child of <body> so it's fixed relative to the viewport.
        document.body.appendChild(modal);

        const open = () => {
            modal.classList.remove('hidden');
            document.body.style.overflow = 'hidden';
            if (frame && !frame.src) frame.src = frame.dataset.src;
        };
        const close = () => {
            modal.classList.add('hidden');
            document.body.style.overflow = '';
        };

        btn.addEventListener('click', open);
        closeBtn?.addEventListener('click', close);
        modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) close();
        });
    })();



    // Dropdown Logic
    oipElems.symbolInput?.addEventListener('input', (e) => oipRenderDropdown(e.target.value.toUpperCase(), oipElems.symbolList));
    oipElems.showOIBars?.addEventListener('change', () => oipRequestDraw());
    oipElems.showSignals?.addEventListener('change', () => { if (oipOIData?.candles) oipDrawSignals(oipOIData.candles); });
    // Toggle only redraws from cached data — the 9:18 selection is computed on load regardless.
    document.getElementById('oipShowAtmCeOi')?.addEventListener('change', () => oipDrawAtmCeOiLines());
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

    oipElems.showCVWAP?.addEventListener('change', () => oipSyncVwapVisibility());
    oipElems.showPVWAP?.addEventListener('change', () => oipSyncVwapVisibility());

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

    ['oipCprShowPrevHL', 'oipCprShowBand', 'oipCprShowResistance', 'oipCprShowSupport', 'oipCprShowCumR3S3'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (oipOIData?.candles) oipDrawCpr(oipOIData.candles);
        });
    });

    ['oipShowMultiCpr', 'oipMultiCpr15m', 'oipMultiCpr30m', 'oipMultiCpr1h'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (oipOIData?.candles) oipDrawMultiCPR(oipOIData.candles);
        });
    });

    document.getElementById('oipShowMaxPain')?.addEventListener('change', () => {
        oipUpdateMaxPainLine(oipCurrentPrice, oipOIData?.max_pain);
    });
    document.getElementById('oipShow2ndCandle30s')?.addEventListener('change', () => {
        if (oipOIData?.candles) oipDraw2ndCandle30sBox(oipOIData.candles);
    });
    document.getElementById('oipShow2nd5mCandle')?.addEventListener('change', () => {
        if (oipOIData?.candles) oipDraw2nd5mCandleBox(oipOIData.candles);
    });
    document.getElementById('oipShowMondayBox')?.addEventListener('change', () => {
        if (oipOIData?.candles) oipDrawMondayBox(oipOIData.candles);
    });
    document.getElementById('oipShow30mReversalLines')?.addEventListener('change', () => {
        if (oipFullCandles) oipDraw30mReversalLines(oipFullCandles);
    });
    ['oipReversal30mCountUp', 'oipReversal30mCountDn', 'oipReversal30mRange'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (oipFullCandles) oipDraw30mReversalLines(oipFullCandles);
        });
    });
    document.getElementById('oipShow1DReversalLines')?.addEventListener('change', () => {
        if (oipFullCandles) oipDraw1DReversalLines(oipFullCandles);
    });
    ['oipReversal1DCount', 'oipReversal1DRange'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (oipFullCandles) oipDraw1DReversalLines(oipFullCandles);
        });
    });

    oipElems.showPremium?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view?.value);
    });

    // Strike mode dropdown — controls which strike input is visible and syncs hidden checkboxes
    function oipApplyStrikeMode(mode) {
        const isAtm    = mode === 'atm';
        const isCustom = mode === 'custom';
        const isCePe   = mode === 'ce_pe';

        // Sync hidden compat checkboxes
        if (oipElems.first5mATM) oipElems.first5mATM.checked = isAtm;
        if (oipElems.customStrikeCheck) oipElems.customStrikeCheck.checked = isCustom;

        // Show / hide strike dropdowns
        if (oipElems.customStrikeDropdown) oipElems.customStrikeDropdown.style.display = isCustom ? '' : 'none';
        // Prem. Str. (atm) and CE & PE modes both show the CE/PE dropdowns
        const showCePe = isCePe || isAtm;
        if (oipElems.ceStrikeDropdown) oipElems.ceStrikeDropdown.style.display = showCePe ? '' : 'none';
        if (oipElems.peStrikeDropdown) oipElems.peStrikeDropdown.style.display = showCePe ? '' : 'none';
        // In Prem. Str. mode the dropdowns are auto-populated; disable manual edits
        if (oipElems.ceStrikeDropdown) oipElems.ceStrikeDropdown.disabled = isAtm;
        if (oipElems.peStrikeDropdown) oipElems.peStrikeDropdown.disabled = isAtm;
        // Extra strike-diff widen dropdown: only relevant in Prem. Str. mode
        if (oipElems.premExtra) oipElems.premExtra.style.display = isAtm ? '' : 'none';

        if (!isAtm) {
            oipClearPremStrikeLines();
        }
    }

    // Apply on page load to match the HTML default (custom selected)
    oipApplyStrikeMode(oipElems.strikeMode?.value || 'custom');

    oipElems.strikeMode?.addEventListener('change', () => {
        const mode = oipElems.strikeMode.value;
        oipApplyStrikeMode(mode);
        if (mode === 'atm') {
            oipFetchAndApplyPremiumStrikes();
        } else {
            oipLoadCandles(true, false);
        }
    });

    oipElems.customStrikeDropdown?.addEventListener('change', () => {
        if (oipElems.strikeMode?.value === 'custom') oipLoadCandles(true, true);
    });

    oipElems.premExtra?.addEventListener('change', () => {
        if (oipElems.strikeMode?.value === 'atm') oipFetchAndApplyPremiumStrikes();
    });

    oipElems.ceStrikeDropdown?.addEventListener('change', () => {
        if (oipElems.strikeMode?.value === 'ce_pe') oipLoadCandles(true, true);
    });

    oipElems.peStrikeDropdown?.addEventListener('change', () => {
        if (oipElems.strikeMode?.value === 'ce_pe') oipLoadCandles(true, true);
    });

    oipElems.showSignals?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view?.value);
    });

    oipElems.targetDistance?.addEventListener('change', () => {
        oipRefreshLocalView(oipElems.view?.value);
    });

    // Order buttons
    document.querySelectorAll('.oip-order-btn').forEach(btn => {
        if (btn.id === 'oipExitAll') {
            btn.addEventListener('click', () => oipExitAllOrders(btn));
        } else if (btn.id === 'oipSLCE') {
            btn.addEventListener('click', () => oipPlaceSLOrders(btn, 'CE'));
        } else if (btn.id === 'oipSLPE') {
            btn.addEventListener('click', () => oipPlaceSLOrders(btn, 'PE'));
        } else {
            btn.addEventListener('click', () => oipPlaceOrder(btn.dataset.side, btn.dataset.action, btn));
        }
    });

    // SL price input — enable/disable SL CE/PE buttons
    oipElems.slPrice?.addEventListener('input', () => {
        const enabled = parseFloat(oipElems.slPrice.value) > 0;
        if (oipElems.slCEBtn) oipElems.slCEBtn.disabled = !enabled;
        if (oipElems.slPEBtn) oipElems.slPEBtn.disabled = !enabled;
    });

    oipOIChart.subscribeCrosshairMove(() => {
        oipElems.tooltip?.classList.add('hidden');
    });

    // Theme setup and handling
    const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'ocean';
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

        oipOISeries = oipOIChart.addSeries(LightweightCharts.CandlestickSeries, {
            ...candleStyle(),
            autoscaleInfoProvider: customAutoscale
        });
        lwBringToFront(oipOISeries);
        oipVwapSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, {
            color: '#f59e0b', lineWidth: 2, title: '',
            visible: oipElems.showVwapOI?.checked ?? false,
            priceLineVisible: false, lastValueVisible: false,
            autoscaleInfoProvider: () => null
        });
        // CVWAP (current-session) + PVWAP (previous-session flat line)
        oipCvwapSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, {
            color: '#3b82f6', lineWidth: 2, title: '',
            visible: oipElems.showCVWAP?.checked ?? false,
            priceLineVisible: false, lastValueVisible: false,
            autoscaleInfoProvider: () => null
        });
        oipPvwapSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, {
            color: '#f97316', lineWidth: 2, title: '',
            visible: oipElems.showPVWAP?.checked ?? false,
            priceLineVisible: false, lastValueVisible: false,
            autoscaleInfoProvider: () => null
        });

        // Fixed EMA series matching Mine CPR Pine script
        oipEma9Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });
        oipEma20Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });
        oipEma50Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });
        oipEma100Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#3b82f6', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });
        oipEma200Series = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: '#000000', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: false, autoscaleInfoProvider: () => null });

        /* 
        oipMaxPainSeries = oipOIChart.addSeries(LightweightCharts.LineSeries, { 
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

        if (typeof TradingViewChart !== 'undefined' && TradingViewChart.addScrollButton) {
            TradingViewChart.addScrollButton(oipOIChart, oipOISeries, elOI);
        }
    }
    if (window.oipInitSecondaryCharts) window.oipInitSecondaryCharts();
}

function creatBaseChart(el) {
    const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'ocean';
    const cfg = OIP_CHART_THEMES[activeTheme] || OIP_CHART_THEMES['dark'];
    return LightweightCharts.createChart(el, {
        width: el.clientWidth || 1200, height: 375,
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
        upColor: '#1b9981', downColor: '#f23645',
        borderUpColor: '#1b9981', borderDownColor: '#f23645',
        wickUpColor: '#1b9981', wickDownColor: '#f23645',
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
    if (!(oipElems.showOIBars?.checked ?? true)) return;
    if (oipMode === 'off') return;
    if (!oipAllStrikes.length) return;

    // Resolve dynamic colors based on active theme
    const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'ocean';
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
    const isLight = (activeTheme === 'light' || activeTheme === 'cream' || activeTheme === 'ocean');
    ctx.font = 'bold 10px sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'right';
    filtered.forEach(s => {
        const y = oipOISeries.priceToCoordinate(s.strike);
        if (y === null || y < -50 || y > H + 50) return;
        const valCE = getCE(s), valPE = getPE(s);
        const ceW = (Math.abs(valCE) / maxVal) * MAX_BAR_PX, peW = (Math.abs(valPE) / maxVal) * MAX_BAR_PX;
        const hasBoth = valCE !== 0 && valPE !== 0;
        if (valCE !== 0) {
            ctx.fillStyle = ctx.strokeStyle = (s.strike === ceMaxStr) ? ceColMax : ceCol;
            if (valCE < 0) ctx.strokeRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
            else ctx.fillRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
            ctx.fillStyle = isLight ? '#000000' : '#ffffff';
            const ceLbl = (hasBoth ? '(' + fmtS(Math.abs(valPE - valCE)) + ')' : '') + fmtL(valCE) + ' C';
            ctx.fillText(ceLbl, plotRight - 4, y - (barH / 2) - 0.5);
        }
        if (valPE !== 0) {
            ctx.fillStyle = ctx.strokeStyle = (s.strike === peMaxStr) ? peColMax : peCol;
            if (valPE < 0) ctx.strokeRect(plotRight - peW, y + 0.5, peW, barH);
            else ctx.fillRect(plotRight - peW, y + 0.5, peW, barH);
            ctx.fillStyle = isLight ? '#000000' : '#ffffff';
            const peLbl = (hasBoth ? '(' + fmtS(Math.abs(valCE - valPE)) + ')' : '') + fmtL(valPE) + ' P';
            ctx.fillText(peLbl, plotRight - 4, y + (barH / 2) + 0.5);
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

// oipCalculateFixedEMA, oipCalculateAllEMAs, oipCalculate3EMAs, oipCalculateVWAP — defined in oi_indicators.js

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
        oipPremiumStrikeData = null; // Reset so strikes are re-computed for new symbol/session
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

        // 4. Load Candles — if Prem. Str. mode is active, compute premium strikes first
        console.log(`[OIP] Loading initial Candles for ${oipSymbol}...`);
        if (oipElems.strikeMode?.value === 'atm' && !oipPremiumStrikeData) {
            await oipFetchAndApplyPremiumStrikes(resetZoom);
            // oipFetchAndApplyPremiumStrikes calls oipLoadCandles internally, so return here
            return;
        }
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

        const view = oipElems.view?.value || 'combined';

        const needsOptionData = (view !== 'index') && !oipOptionData;
        const autoHL = true;
        const strikeMode = oipElems.strikeMode?.value || 'custom';
        let first5m = false;
        let customStrike = '', ceStrike = '', peStrike = '';
        if (strikeMode === 'atm') {
            if (oipPremiumStrikeData) {
                ceStrike = oipPremiumStrikeData.ce_strike;
                peStrike = oipPremiumStrikeData.pe_strike;
            } else {
                first5m = true; // Fallback until premium strikes are fetched
            }
        } else if (strikeMode === 'custom') {
            customStrike = oipElems.customStrikeDropdown?.value || '';
        } else if (strikeMode === 'ce_pe') {
            ceStrike = oipElems.ceStrikeDropdown?.value || '';
            peStrike = oipElems.peStrikeDropdown?.value || '';
        }

        const _daysForInterval = { day: 365, week: 1095, month: 3650 };
        let days    = _daysForInterval[oipInterval] ?? (parseInt(oipElems.days?.value) || 5);
        const optDays = days; // match option candle range to spot range so chart scales stay in sync
        let dateRangeParams = "";
        if (window.oipReplayMode && oipElems.startDate?.value && oipElems.endDate?.value) {
            dateRangeParams = `&start_date=${oipElems.startDate.value}&end_date=${oipElems.endDate.value}`;
        }

        if (!forceFetch && oipOIData && !needsOptionData) { oipRefreshLocalView(view, resetZoom); return; }

        const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&opt_days=${optDays}&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=${autoHL}&first_5m_atm=${first5m}&custom_strike=${customStrike}&ce_strike=${ceStrike}&pe_strike=${peStrike}${dateRangeParams}&_t=${Date.now()}`;

        const res = await fetch(url);
        const data = await res.json();


        if (!data.success) throw new Error(data.error);
        if (data.fetch_error) showNotification(`Data fetch error: ${data.fetch_error}`, 'error');

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

            // Suppress cross-chart sync callbacks while we are pushing new data into all
            // series. Any setData call can fire subscribeVisibleLogicalRangeChange on its
            // chart; if the user happens to be hovering that chart at that instant, the
            // callback would try to sync all other charts to a stale/auto-fitted position.
            window._oipDataRefreshing = true;

            if (validCandles.length) {
                try {
                    oipOISeries.setData(validCandles);
                    oipOIChartReady = true;

                    if (resetZoom) {
                        // Jump to current price range without affecting X-axis zoom (no fitContent)
                        oipOIChart.priceScale('right').applyOptions({ autoScale: true });
                    }

                    if (oipVwapSeries) oipVwapSeries.setData(oipCalculateVWAP(validCandles));
                    if (oipCvwapSeries) oipCvwapSeries.setData(oipCalculateCVWAP(validCandles));
                    if (oipPvwapSeries) oipPvwapSeries.setData(oipCalculatePVWAP(validCandles));
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
            oipDrawMultiCPR(validCandles);
            oipDrawRSI(validCandles);

            // 9:18 ATM CE OI lines — always compute & cache (kept ready);
            // oipDrawAtmCeOiLines() only renders when the checkbox is on.
            const _atmCeOiDate = (window.oipReplayMode && oipElems.startDate?.value) ? oipElems.startDate.value : null;
            await oipFetchAtmCeOiStrikes(oipSymbol, oipStrikeStep, _atmCeOiDate);
            oipDrawAtmCeOiLines();
        }

        if (data.intrinsic?.spot_high && oipElems.spotHigh) {
            if (oipElems.spotHigh) oipElems.spotHigh.value = data.intrinsic.spot_high;
            if (oipElems.spotLow) oipElems.spotLow.value = data.intrinsic.spot_low;
        }

        if (oipIntrinsicChart) {
            if (view === 'index') {
                if (oipElems.itmCE) if (oipElems.itmCE) oipElems.itmCE.textContent = 'NIFTY';
                if (oipElems.itmPE) if (oipElems.itmPE) oipElems.itmPE.textContent = 'Index';
                oip30sSecondCandle.oi = data.second_30s_candle_oi || [];
                oip30sSecondCandle.ce = [];
                oip30sSecondCandle.pe = [];
                // No option data in index view — draw OI-chart boxes only
                oipDraw2ndCandle30sBox(validCandles);
                oipDraw2nd5mCandleBox(validCandles);
                oipDrawMondayBox(validCandles);
                oipDraw30mReversalLines(validCandles);
                oipDraw1DReversalLines(validCandles);
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
                oip30sSecondCandle.oi = data.second_30s_candle_oi || [];
                oip30sSecondCandle.ce = data.second_30s_candle_ce || [];
                oip30sSecondCandle.pe = data.second_30s_candle_pe || [];
                // Draw boxes after oipOptionData is refreshed so CE/PE charts use the new strike's candles
                oipDraw2ndCandle30sBox(validCandles);
                oipDraw2nd5mCandleBox(validCandles);
                oipDrawMondayBox(validCandles);
                oipDraw30mReversalLines(validCandles);
                oipDraw1DReversalLines(validCandles);
            }

            // After every overlay/box has been (re)added, enforce the full z-policy
            // (fills → lines → candles) so nothing hides behind another indicator.
            if (typeof oipApplyZOrder === 'function') oipApplyZOrder();

            oipFullCandles = validCandles;
            oipFullOptionData = oipOptionData;

            if (window.oipReplayMode) {
                if (typeof oipSetupReplaySlider === 'function') oipSetupReplaySlider();
                return;
            }

            oipRefreshLocalView(view, resetZoom);
            oipRequestDraw();
            // Release the data-refresh suppression flag after the current call stack
            // (all setData calls are synchronous; RAF fires after LC's own render pass).
            requestAnimationFrame(() => { window._oipDataRefreshing = false; });
            // Sync secondary charts to OI's visible range only on first load or when the
            // user explicitly resets zoom (symbol/date/timeframe change). Periodic candle
            // refreshes use resetZoom=false and must NOT sync — that would snap the chart
            // back to the right edge every second, fighting the user's manual scroll.
            if (resetZoom || !_oipChartsSyncedOnce) {
                if (resetZoom) _oipChartsSyncedOnce = false;
                setTimeout(() => {
                    if (!oipOIChart || !oipOIChartReady || !oipIntChartReady) return;
                    const ts = oipOIChart.timeScale();
                    const barSpacing = ts.options().barSpacing;
                    const scrollPos  = ts.scrollPosition();
                    if (!barSpacing) return;
                    // OI chart's scrollPos is measured from its last bar (which may be a
                    // future bar from reversal lines). Option charts have no future bars,
                    // so we add the reversal-line future-bar count so their last real candle
                    // sits at the same visual distance from the Y-axis as OI's last real candle.
                    const futureBarsOffset = window._oipReversalFutureBarsCount || 0;
                    // CE/PE-only charts have rightOffset=5 vs OI's 20; subtract 15 so their
                    // last real candle sits closer to the Y-axis, matching the user's preference.
                    const optionRightAdj = 15;
                    [
                        { chart: oipIntrinsicChart?.chart, adj: 0 },
                        { chart: oipCEChart?.chart,        adj: -optionRightAdj },
                        { chart: oipPEChart?.chart,        adj: -optionRightAdj }
                    ].forEach(({ chart: c, adj }) => {
                        if (!c) return;
                        try {
                            c.timeScale().applyOptions({ barSpacing });
                            c.timeScale().scrollToPosition(scrollPos + futureBarsOffset + adj, false);
                        } catch(e) {}
                    });
                    _oipChartsSyncedOnce = true;
                }, 50);
            }
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
    if (oipElems.hdrPcrCard) {
        const pcrDark = pcr >= 1.7 || pcr <= 0.7;
        if (pcr >= 1.7) {
            oipElems.hdrPcrCard.style.background = '#7f1d1d'; // dark red
        } else if (pcr <= 0.7) {
            oipElems.hdrPcrCard.style.background = '#14532d'; // dark green
        } else {
            oipElems.hdrPcrCard.style.background = '';
        }
        oipElems.hdrPcrCard.querySelectorAll('.oip-hdr-lbl, .oip-hdr-val')
            .forEach(el => { el.style.color = pcrDark ? '#ffffff' : ''; });
    }
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
        oipMaxPainLine = null;
    }

    if (mpValue > 0 && document.getElementById('oipShowMaxPain')?.checked !== false) {
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
    oipPremiumSeries.entry = chart.addSeries(LightweightCharts.LineSeries, { ...base, color: '#4caf50', lineWidth: 2 });
    oipPremiumSeries.current = chart.addSeries(LightweightCharts.LineSeries, { ...base, color: '#2196f3', lineWidth: 2 });
    oipPremiumSeries.t1 = chart.addSeries(LightweightCharts.LineSeries, { ...base, color: '#e040fb', lineWidth: 1 });
    oipPremiumSeries.t2 = chart.addSeries(LightweightCharts.LineSeries, { ...base, color: '#f97316', lineWidth: 1 });
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

// Compact formatter for bracket diff labels — no sign, no spaces, 1 decimal
function fmtS(n) {
    if (n == null) return '0';
    const abs = Math.abs(n);
    if (abs >= 10000000) return (abs / 10000000).toFixed(1) + 'Cr';
    if (abs >= 100000) return (abs / 100000).toFixed(1) + 'L';
    if (abs >= 1000) return (abs / 1000).toFixed(0) + 'K';
    return String(Math.round(abs));
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
        const idxCandles = masterData.filter(d => d.open !== undefined);
        if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(idxCandles));
        if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
        if (oipCvwapIntSeries) oipCvwapIntSeries.setData(oipCalculateCVWAP(idxCandles));
        if (oipPvwapIntSeries) oipPvwapIntSeries.setData(oipCalculatePVWAP(idxCandles));
        if (oipCvwapIntPeSeries) oipCvwapIntPeSeries.setData([]);
        if (oipPvwapIntPeSeries) oipPvwapIntPeSeries.setData([]);
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
        // CVWAP (current-session) / PVWAP (previous-session) for CE & PE premiums.
        const ceCvwapData = oipCalculateCVWAP(ceRaw);
        const peCvwapData = oipCalculateCVWAP(peRaw);
        const cePvwapData = oipCalculatePVWAP(ceRaw);
        const pePvwapData = oipCalculatePVWAP(peRaw);

        // Update Individual Premium Chart
        if (view === 'combined') {
            oipIntrinsicChart.update(ceData, peData, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData(ceVwapData);
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(peVwapData);
            if (oipCvwapIntSeries) oipCvwapIntSeries.setData(ceCvwapData);
            if (oipCvwapIntPeSeries) oipCvwapIntPeSeries.setData(peCvwapData);
            if (oipPvwapIntSeries) oipPvwapIntSeries.setData(cePvwapData);
            if (oipPvwapIntPeSeries) oipPvwapIntPeSeries.setData(pePvwapData);
        } else if (view === 'ce') {
            oipIntrinsicChart.update(ceData, null, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData(ceVwapData);
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
            if (oipCvwapIntSeries) oipCvwapIntSeries.setData(ceCvwapData);
            if (oipCvwapIntPeSeries) oipCvwapIntPeSeries.setData([]);
            if (oipPvwapIntSeries) oipPvwapIntSeries.setData(cePvwapData);
            if (oipPvwapIntPeSeries) oipPvwapIntPeSeries.setData([]);
        } else {
            oipIntrinsicChart.update(null, peData, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData([]);
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(peVwapData);
            if (oipCvwapIntSeries) oipCvwapIntSeries.setData([]);
            if (oipCvwapIntPeSeries) oipCvwapIntPeSeries.setData(peCvwapData);
            if (oipPvwapIntSeries) oipPvwapIntSeries.setData([]);
            if (oipPvwapIntPeSeries) oipPvwapIntPeSeries.setData(pePvwapData);
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
            if (oipCECvwapSeries) oipCECvwapSeries.setData(ceCvwapData);
            if (oipCEPvwapSeries) oipCEPvwapSeries.setData(cePvwapData);

            const _sm = oipElems.strikeMode?.value;
            let _ceLbl;
            if (_sm === 'ce_pe') _ceLbl = oipElems.ceStrikeDropdown?.value || '--';
            else if (_sm === 'atm' && oipPremiumStrikeData) _ceLbl = oipPremiumStrikeData.ce_strike;
            else _ceLbl = oipElems.customStrikeDropdown?.value || '--';
            if (document.getElementById('oipLegendCEOnly')) document.getElementById('oipLegendCEOnly').textContent = `${_ceLbl} CE`;
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
            if (oipPECvwapSeries) oipPECvwapSeries.setData(peCvwapData);
            if (oipPEPvwapSeries) oipPEPvwapSeries.setData(pePvwapData);

            const _sm = oipElems.strikeMode?.value;
            let _peLbl;
            if (_sm === 'ce_pe') _peLbl = oipElems.peStrikeDropdown?.value || '--';
            else if (_sm === 'atm' && oipPremiumStrikeData) _peLbl = oipPremiumStrikeData.pe_strike;
            else _peLbl = oipElems.customStrikeDropdown?.value || '--';
            if (document.getElementById('oipLegendPEOnly')) document.getElementById('oipLegendPEOnly').textContent = `${_peLbl} PE`;
        }
    }
    if (oipOIData.intrinsic) oipDrawIntrinsicLines(oipOIData.intrinsic, view);
    if (oipOIData.intrinsic) oipDrawPremiumLines(oipOIData.intrinsic, view);
    oipDrawPremStrikeLines();
    oipDrawAtmCeOiLines();
}




// oipCalculateDynamicCPR, oipDrawCpr — defined in oi_indicators.js

// ── Shared helpers ────────────────────────────────────────────────────────────

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

// Draw a 1px-border + fill box on the given LightweightCharts instance.
// Returns { chart, fill, top, bottom } for later cleanup.
function _oipDrawCandleBox(chart, hi, lo, times, color, fillAlpha = 0.10, borderAlpha = 0.65, borderColor = null) {
    const safeTimes = times.filter(t => t != null && isFinite(t) && t > 0);
    if (!safeTimes.length) return null;
    const fillCol   = _oipColorAlpha(color, fillAlpha);
    const borderCol = _oipColorAlpha(borderColor || color, borderAlpha);
    const shared = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null };
    try {
        // Skip the fill series entirely when the background opacity is zero.
        let fill = null;
        if (fillAlpha > 0) {
            fill = chart.addSeries(LightweightCharts.BaselineSeries, {
                baseValue: { type: 'price', price: lo },
                topFillColor1: fillCol, topFillColor2: fillCol, topLineColor: 'transparent',
                bottomFillColor1: 'transparent', bottomFillColor2: 'transparent', bottomLineColor: 'transparent',
                lineWidth: 1, ...shared
            });
            fill.setData(safeTimes.map(t => ({ time: t, value: hi })));
        }

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

// Group candles by Fake-IST date → returns { dateKey: [sorted candles] } for all days.
function _oipGroupByDay(candles) {
    if (!candles || !candles.length) return {};
    const dayMap = {};
    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        const ds = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
        if (!dayMap[ds]) dayMap[ds] = [];
        dayMap[ds].push(c);
    });
    Object.keys(dayMap).forEach(k => dayMap[k].sort((a, b) => a.time - b.time));
    return dayMap;
}

// Group candles by Fake-IST date and return today's sorted candles.
function _oipTodayCandles(candles) {
    const map = _oipGroupByDay(candles);
    const key = Object.keys(map).sort().pop();
    return key ? map[key] : [];
}

// Normalise high/low from either { high, low } or { h, l } candle shapes.
function _oipH(c) { return parseFloat(c.high ?? c.h); }
function _oipL(c) { return parseFloat(c.low  ?? c.l); }

// ── 2nd 30-second candle box — all days ──────────────────────────────────────
let oip30sSecondCandle = { oi: [], ce: [], pe: [] };
let oip2ndCandle30sBox = { oi: [], ce: [], pe: [] };

function oipDraw2ndCandle30sBox(candles) {
    oip2ndCandle30sBox.oi.forEach(_oipRemoveBoxSeries); oip2ndCandle30sBox.oi = [];
    oip2ndCandle30sBox.ce.forEach(_oipRemoveBoxSeries); oip2ndCandle30sBox.ce = [];
    oip2ndCandle30sBox.pe.forEach(_oipRemoveBoxSeries); oip2ndCandle30sBox.pe = [];

    const _30s_allowed = ['30second', 'minute'];
    if (!_30s_allowed.includes(oipInterval) || !candles || !candles.length) return;
    if (!document.getElementById('oipShow2ndCandle30s')?.checked) return;

    // Build a day-key → candle lookup from the backend-supplied 2nd 30s candles.
    // These are only populated when oipInterval === 'minute'; otherwise empty.
    function _build30sMap(arr) {
        const m = {};
        (arr || []).forEach(c => {
            const d = new Date(c.time * 1000);
            const dk = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
            m[dk] = c;
        });
        return m;
    }

    function _draw30sAllDays(chart, src, map30s) {
        const boxes = [];
        const srcMap = _oipGroupByDay(src);
        Object.keys(srcMap).sort().forEach(dk => {
            const day = srcMap[dk];
            // Use actual 2nd 30s candle H/L when available (1-min timeframe),
            // otherwise fall back to day[1] which is the 2nd bar in src (30s timeframe).
            let c2;
            if (map30s && map30s[dk]) {
                c2 = map30s[dk];
            } else if (day.length >= 2) {
                c2 = day[1];
            } else {
                return;
            }
            const hi = _oipH(c2), lo = _oipL(c2);
            if (!isFinite(hi) || !isFinite(lo) || hi === lo) return;
            const times = day.filter(c => c.time >= c2.time).map(c => c.time);
            if (times.length) boxes.push(_oipDrawCandleBox(chart, hi, lo, times, '#FFC800'));
        });
        return boxes;
    }

    const _oi30sMap  = _build30sMap(oip30sSecondCandle.oi);
    if (oipOIChart)
        oip2ndCandle30sBox.oi = _draw30sAllDays(oipOIChart, candles, _oi30sMap);

    requestAnimationFrame(() => {
        try {
            oip2ndCandle30sBox.ce.forEach(_oipRemoveBoxSeries); oip2ndCandle30sBox.ce = [];
            oip2ndCandle30sBox.pe.forEach(_oipRemoveBoxSeries); oip2ndCandle30sBox.pe = [];
            const _ce30sMap = _build30sMap(oip30sSecondCandle.ce);
            const _pe30sMap = _build30sMap(oip30sSecondCandle.pe);
            if (oipCEChart?.chart && oipOptionData)
                oip2ndCandle30sBox.ce = _draw30sAllDays(oipCEChart.chart, oipOptionData.filter(c => c.type === 'CE'), _ce30sMap);
            if (oipPEChart?.chart && oipOptionData)
                oip2ndCandle30sBox.pe = _draw30sAllDays(oipPEChart.chart, oipOptionData.filter(c => c.type === 'PE'), _pe30sMap);
            if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder();
        } catch(e) {}
    });
    if (typeof oipApplyZOrder === 'function') oipApplyZOrder();
}

// ── 2nd 5-minute candle box (09:20–09:25) — all days, 1m/2m/3m/5m ───────────
let oip2nd5mCandleBox = { oi: [], ce: [], pe: [] };

function oipDraw2nd5mCandleBox(candles) {
    oip2nd5mCandleBox.oi.forEach(_oipRemoveBoxSeries); oip2nd5mCandleBox.oi = [];
    oip2nd5mCandleBox.ce.forEach(_oipRemoveBoxSeries); oip2nd5mCandleBox.ce = [];
    oip2nd5mCandleBox.pe.forEach(_oipRemoveBoxSeries); oip2nd5mCandleBox.pe = [];

    const allowedIntervals = ['minute', '2minute', '3minute', '5minute'];
    if (!allowedIntervals.includes(oipInterval) || !candles || !candles.length) return;
    if (!document.getElementById('oipShow2nd5mCandle')?.checked) return;

    function _draw5mAllDays(chart, src) {
        const boxes = [];
        const map = _oipGroupByDay(src);
        Object.keys(map).sort().forEach(dk => {
            const day = map[dk];
            const w = day.filter(c => {
                const d = new Date(c.time * 1000);
                return d.getUTCHours() === 9 && d.getUTCMinutes() >= 20 && d.getUTCMinutes() < 25;
            });
            if (!w.length) return;
            const hi = Math.max(...w.map(_oipH));
            const lo = Math.min(...w.map(_oipL));
            if (!isFinite(hi) || !isFinite(lo) || hi === lo) return;
            const times = day.filter(c => c.time >= w[0].time).map(c => c.time);
            // #2dd2ff background @ 10% opacity with a solid #2dd2ff border (candles render on top).
            if (times.length) boxes.push(_oipDrawCandleBox(chart, hi, lo, times, '#2dd2ff', 0.1, 1, '#2dd2ff'));
        });
        return boxes;
    }

    if (oipOIChart)
        oip2nd5mCandleBox.oi = _draw5mAllDays(oipOIChart, candles);

    // Defer CE/PE draws past their charts' init RAF — addBaselineSeries triggers
    // LC's async render RAF which crashes if the chart isn't yet initialized.
    requestAnimationFrame(() => {
        try {
            oip2nd5mCandleBox.ce.forEach(_oipRemoveBoxSeries); oip2nd5mCandleBox.ce = [];
            oip2nd5mCandleBox.pe.forEach(_oipRemoveBoxSeries); oip2nd5mCandleBox.pe = [];
            if (oipCEChart?.chart && oipOptionData)
                oip2nd5mCandleBox.ce = _draw5mAllDays(oipCEChart.chart, oipOptionData.filter(c => c.type === 'CE'));
            if (oipPEChart?.chart && oipOptionData)
                oip2nd5mCandleBox.pe = _draw5mAllDays(oipPEChart.chart, oipOptionData.filter(c => c.type === 'PE'));
            if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder();
        } catch(e) {}
    });
    if (typeof oipApplyZOrder === 'function') oipApplyZOrder();
}

// ── Monday High/Low Box — recent weeks ───────────────────────────────────────
// For every Monday present in the loaded candles, draw a box at that Monday's
// high/low extended across the rest of the week (mirrors the Pine "Monday H/L
// Box"). Drawn on the main (underlying) pane only.
let oipMondayBoxes = [];

function oipDrawMondayBox(candles) {
    oipMondayBoxes.forEach(_oipRemoveBoxSeries);
    oipMondayBoxes = [];

    if (!oipOIChart || !candles || !candles.length) return;
    if (!document.getElementById('oipShowMondayBox')?.checked) return;

    const dayMap = _oipGroupByDay(candles);
    Object.keys(dayMap).sort().forEach(dk => {
        const day = dayMap[dk];
        const first = day[0];
        // Fake-IST encoding: getUTCDay() === 1 means Monday.
        if (new Date(first.time * 1000).getUTCDay() !== 1) return;

        let hi = -Infinity, lo = Infinity;
        day.forEach(c => { hi = Math.max(hi, _oipH(c)); lo = Math.min(lo, _oipL(c)); });
        if (!isFinite(hi) || !isFinite(lo) || hi === lo) return;

        // Extend the box horizontally across the week (Monday → just before next Monday).
        const weekStart = first.time;
        const weekEnd   = weekStart + 7 * 86400;
        const times = candles.filter(c => c.time >= weekStart && c.time < weekEnd).map(c => c.time);
        // Solid border lines, zero background opacity.
        if (times.length) oipMondayBoxes.push(_oipDrawCandleBox(oipOIChart, hi, lo, times, '#34ed0b', 0, 1));
    });
    if (typeof oipApplyZOrder === 'function') oipApplyZOrder();
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


// oipCalculateDynamicEMA — defined in oi_indicators.js

let _exitArmTimer = null;

async function oipExitAllOrders(btn) {
    // Double-click confirm: first click arms, second click within 3s executes
    if (!btn._exitArmed) {
        btn._exitArmed = true;
        const prev = btn.innerText;
        btn.innerText = 'CONFIRM?';
        btn.style.background = '#7f1d1d';
        if (_exitArmTimer) clearTimeout(_exitArmTimer);
        _exitArmTimer = setTimeout(() => {
            btn._exitArmed = false;
            btn.innerText = prev;
            btn.style.background = '';
        }, 3000);
        return;
    }

    // Second click — execute
    btn._exitArmed = false;
    if (_exitArmTimer) { clearTimeout(_exitArmTimer); _exitArmTimer = null; }

    const originalText = 'EXIT';
    btn.disabled = true;
    btn.innerText = 'EXITING...';
    btn.style.background = '';
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
            const summary = (r.summary || []).map(s =>
                `${s.broker}_${s.instance}: ${s.cancelled_orders} Cancelled, ${s.exited_positions} Exited`
            ).join(' | ');
            showNotification(`Exit complete: ${summary || 'Done'}`, 'success');
        } else {
            showNotification(`Exit failed: ${r.error || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        console.error('[Exit] Error:', e);
        showNotification(`Exit error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerText = originalText;
        btn.style.opacity = '1';
    }
}

async function oipPlaceSLOrders(btn, side = null) {
    const triggerPrice = parseFloat(oipElems.slPrice?.value);
    if (!(triggerPrice > 0)) return;

    if (!oipCurrentCEStrike && !oipCurrentPEStrike) {
        showNotification('No CE/PE strike loaded — load OI data first.', 'error');
        return;
    }

    btn.disabled = true;
    const origText = btn.innerText;
    btn.innerText = 'PLACING...';
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;

    const legs = [];
    if ((!side || side === 'CE') && oipCurrentCEStrike) legs.push({ option_type: 'CE', strike: oipCurrentCEStrike });
    if ((!side || side === 'PE') && oipCurrentPEStrike) legs.push({ option_type: 'PE', strike: oipCurrentPEStrike });

    const results = await Promise.allSettled(legs.map(leg =>
        fetch('/api/order/place-sl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify({ symbol: oipSymbol, strike: leg.strike, option_type: leg.option_type, trigger_price: triggerPrice })
        }).then(r => r.json())
    ));

    const ok      = results.filter(r => r.status === 'fulfilled' && r.value?.success);
    const failed  = results.filter(r => r.status !== 'fulfilled' || !r.value?.success);

    const extractErr = r => {
        if (r.status === 'rejected') return r.reason?.message || 'Network error';
        // errors are nested inside results[] array from the backend
        const brokerErrors = (r.value?.results || [])
            .filter(b => !b.success)
            .map(b => b.error || b.message || 'Unknown error');
        return brokerErrors.length ? brokerErrors.join(', ') : (r.value?.error || r.value?.message || 'Unknown error');
    };

    if (ok.length > 0 && failed.length === 0) {
        showNotification(`SL placed for ${ok.length} leg(s).`, 'success');
    } else if (ok.length > 0) {
        const errMsgs = failed.map(extractErr).join('; ');
        showNotification(`${ok.length} placed, ${failed.length} failed: ${errMsgs}`, 'warning');
    } else {
        const errMsgs = failed.map(extractErr).join('; ');
        showNotification(`SL failed: ${errMsgs}`, 'error');
    }

    btn.innerText = origText;
    // Stay disabled — user must re-enter/change price to re-fire (prevents accidental double placement)
}

async function oipPlaceOrder(side, action, btn) {
    const strike = (side === 'CE') ? oipCurrentCEStrike : oipCurrentPEStrike;
    if (!strike) { showNotification(`No ${side} strike available.`, 'error'); return; }

    const mode = document.getElementById('oipOrderMode')?.value || 'broker';
    const limitPriceInput = document.getElementById('oipLimitPrice');
    const rawLimit = limitPriceInput ? parseFloat(limitPriceInput.value) : null;
    const limitPrice = rawLimit && !isNaN(rawLimit) && rawLimit > 0 ? rawLimit : null;
    const orderType = limitPrice ? 'LIMIT' : 'MARKET';
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;

    btn.disabled = true; const ot = btn.title; btn.title = 'Placing...';
    try {
        if (mode === 'mine') {
            // Mine mode: store in backend DB; Python monitors and auto-executes LIMIT orders.
            const res = await fetch('/api/mine-orders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                body: JSON.stringify({
                    symbol: oipSymbol,
                    strike: strike,
                    option_type: side,
                    action: action,
                    strategy: 'intrinsic',
                    order_type: orderType,
                    limit_price: limitPrice,
                    price: limitPrice || 0
                })
            });
            const r = await res.json();
            if (r.success) {
                if (orderType === 'LIMIT') {
                    showNotification(`Mine: Limit ₹${limitPrice} queued — backend monitoring`, 'success');
                } else {
                    const details = _oipBrokerDetails(r);
                    showNotification(`Mine: ${action} ${side} ${strike} dispatched${details}`, 'success');
                }
            } else {
                showNotification(`Mine: ${r.error || 'Order failed'}`, 'error');
            }
            return;
        }

        // Broker mode: place directly to all active brokers; backend saves record to JSON.
        const res = await fetch('/api/orders/place', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify({
                symbol: oipSymbol,
                strike: strike,
                option_type: side,
                action: action,
                strategy: 'intrinsic',
                order_type: orderType,
                limit_price: limitPrice
            })
        });
        const r = await res.json();
        if (r.success) {
            if (orderType === 'LIMIT') {
                showNotification(`Broker: Limit ₹${limitPrice} placed to broker`, 'success');
            } else {
                const details = _oipBrokerDetails(r);
                showNotification(`Broker: ${action} ${side} ${strike}${details}`, 'success');
            }
        } else {
            const details = _oipBrokerDetails(r);
            showNotification(`${r.error || 'Order Failed'}${details ? '\n' + details : ''}`, 'error');
        }
    } catch (e) {
        showNotification(`Order error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false; btn.title = ot;
    }
}

function _oipBrokerDetails(r) {
    if (!r.summary || !Array.isArray(r.summary)) return '';
    return '\n' + r.summary.map(s => {
        const broker = s.broker.replace(/_/g, ' ').toUpperCase();
        const msg = s.result?.error || (s.result?.success ? 'OK' : 'Err');
        return `• ${broker}: ${msg}`;
    }).join('\n');
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

// ── Premium Strike (Prem. Str.) helpers ─────────────────────────────────────

/**
 * Fetch computed CE/PE strikes from the backend and auto-populate the dropdowns,
 * then reload the option candles.
 */
async function oipFetchAndApplyPremiumStrikes(resetZoom = true) {
    try {
        const step  = oipStrikeStep || 50;
        const extra = parseInt(oipElems.premExtra?.value, 10) || 0;
        const res   = await fetch(`/api/oi-profile/premium-strikes?symbol=${oipSymbol}&step=${step}&extra=${extra}`);
        const data = await res.json();
        if (!data.success) {
            console.warn('[OIP] Premium strikes:', data.error);
            await oipLoadCandles(true, resetZoom);
            return;
        }

        oipPremiumStrikeData = data;

        // Populate CE/PE dropdowns with the auto-computed strikes
        function setDropdownStrike(el, strike) {
            if (!el) return;
            let found = false;
            for (const opt of el.options) {
                if (parseFloat(opt.value) === strike) { el.value = opt.value; found = true; break; }
            }
            if (!found) {
                const opt = document.createElement('option');
                opt.value = String(strike);
                opt.textContent = String(strike);
                el.appendChild(opt);
                el.value = String(strike);
            }
        }
        setDropdownStrike(oipElems.ceStrikeDropdown, data.ce_strike);
        setDropdownStrike(oipElems.peStrikeDropdown, data.pe_strike);

        await oipLoadCandles(true, resetZoom);
    } catch (e) {
        console.error('[OIP] Premium strikes fetch failed:', e);
        await oipLoadCandles(true, resetZoom);
    }
}

/** Remove all Prem. Str. price lines from CE and PE individual charts. */
function oipClearPremStrikeLines() {
    oipPremStrikeLines.ce.forEach(l => { try { oipCESeries?.removePriceLine(l); } catch (e) {} });
    oipPremStrikeLines.pe.forEach(l => { try { oipPESeries?.removePriceLine(l); } catch (e) {} });
    oipPremStrikeLines.ce = [];
    oipPremStrikeLines.pe = [];
}

/**
 * Draw 3 horizontal price lines on each individual CE and PE chart:
 *   1. Strike diff  = |PE_strike − CE_strike|          [dashed, amber]
 *   2. Prev-day close of the option itself              [solid, cyan/violet]
 *   3. |CE_prev_close − PE_prev_close| at same strike  [dashed, purple]
 *
 * Only active when "Prem. Str." mode is selected and data is available.
 */
function oipDrawPremStrikeLines() {
    oipClearPremStrikeLines();
    const psd = oipPremiumStrikeData;
    if (!psd || oipElems.strikeMode?.value !== 'atm') return;
    if (!oipCESeries || !oipPESeries) return;

    const strikeDiff = Math.abs(psd.pe_strike - psd.ce_strike);
    const r2 = n => (n != null ? Math.round(n * 100) / 100 : null);

    // ── CE chart lines ────────────────────────────────────────────────
    const ced = psd.ce_strike_data;
    if (ced) {
        const cePdc = r2(ced.ce_close);
        const ceDiff = (ced.ce_close != null && ced.pe_close != null)
            ? r2(Math.abs(ced.ce_close - ced.pe_close)) : null;

        if (strikeDiff > 0)
            oipPremStrikeLines.ce.push(oipCESeries.createPriceLine({
                price: strikeDiff, color: '#000000', lineWidth: 2, lineStyle: 0,
                axisLabelVisible: true, title: `Diff ${strikeDiff}`
            }));
        if (cePdc != null)
            oipPremStrikeLines.ce.push(oipCESeries.createPriceLine({
                price: cePdc, color: '#22d3ee', lineWidth: 2, lineStyle: 0,
                axisLabelVisible: true, title: `PDC ${cePdc}`
            }));
        if (ceDiff != null)
            oipPremStrikeLines.ce.push(oipCESeries.createPriceLine({
                price: ceDiff, color: '#a78bfa', lineWidth: 2, lineStyle: 0,
                axisLabelVisible: true, title: `C-P ${ceDiff}`
            }));
    }

    // ── PE chart lines ────────────────────────────────────────────────
    const ped = psd.pe_strike_data;
    if (ped) {
        const pePdc = r2(ped.pe_close);
        const peDiff = (ped.ce_close != null && ped.pe_close != null)
            ? r2(Math.abs(ped.ce_close - ped.pe_close)) : null;

        if (strikeDiff > 0)
            oipPremStrikeLines.pe.push(oipPESeries.createPriceLine({
                price: strikeDiff, color: '#000000', lineWidth: 2, lineStyle: 0,
                axisLabelVisible: true, title: `Diff ${strikeDiff}`
            }));
        if (pePdc != null)
            oipPremStrikeLines.pe.push(oipPESeries.createPriceLine({
                price: pePdc, color: '#c084fc', lineWidth: 2, lineStyle: 0,
                axisLabelVisible: true, title: `PDC ${pePdc}`
            }));
        if (peDiff != null)
            oipPremStrikeLines.pe.push(oipPESeries.createPriceLine({
                price: peDiff, color: '#a78bfa', lineWidth: 2, lineStyle: 0,
                axisLabelVisible: true, title: `C-P ${peDiff}`
            }));
    }
}

// ─────────────────────────────────────────────────────────────────────────────

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

    if (sortedStrikes.length > 30) {
        // Find index closest to ATM
        let atmIndex = sortedStrikes.findIndex(s => s >= refPrice);
        if (atmIndex === -1) atmIndex = sortedStrikes.length - 1;

        let start = Math.max(0, atmIndex - 15);
        let end = Math.min(sortedStrikes.length, start + 30);

        // Adjust start if end hit the boundary to keep 30 items if possible
        if (end === sortedStrikes.length) {
            start = Math.max(0, end - 30);
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
        for (let i = -15; i <= 15; i++) {
            const s = atm + (i * step);
            if (s <= 0) continue;
            opts += `<option value="${s}">${s}</option>`;
        }
    }

    // CE & PE defaults: exactly 100 pts from the nearest round-hundred ATM
    const availStrikes = sortedStrikes.length > 0
        ? sortedStrikes
        : Array.from({ length: 31 }, (_, i) => atm + (i - 15) * step);
    const refBase = Math.round(refPrice / 100) * 100;
    const ceDefaultArr = availStrikes.filter(s => s >= refBase + 100 && s % 100 === 0);
    const peDefaultArr = availStrikes.filter(s => s <= refBase - 100 && s % 100 === 0);
    const ceDefault = ceDefaultArr.length ? Math.min(...ceDefaultArr) : atm;
    const peDefault = peDefaultArr.length ? Math.max(...peDefaultArr) : atm;

    // Populate all three strike dropdowns with the same options
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

    if (centerPrice && !oipCustomStrikeSetOnLoad) oipCustomStrikeSetOnLoad = true;

    return parseFloat(oipElems.customStrikeDropdown.value) || atm;
}

// oipRSISeriesObj, oipSignalMarkers, oipRSIMarkers — declared in oi_indicators.js
// oipUpdateAllMarkers, oipDrawSignals, oipDrawRSI, oipCalculateRSISnR — defined in oi_indicators.js

// oipDrawSignals, oipDrawRSI, oipCalculateRSISnR — defined in oi_indicators.js




