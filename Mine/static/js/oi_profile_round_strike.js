/**
 * Round Strike block — a second, independent option-premium chart block on the
 * OI Profile page (see oi_profile.html "Round Strike" section). Shows a single
 * Combined (CE+PE) chart only — no CE Only / PE Only panels. Unlike Opt
 * Prem's ATM-relative strike selection, this block auto-picks CE/PE strikes
 * from today's session-open price, rounded to the nearest psychologically
 * significant "round" 100 level:
 *
 *   near50 = round(open / 50) * 50
 *   if near50 is itself a multiple of 100  -> CE = near50 - 100, PE = near50 + 100
 *   else (near50 sits at the X50 half-mark) -> CE = near50 - 50,  PE = near50 + 50
 *
 * Examples: open=23907 -> near50=23900 (mult of 100) -> CE=23800, PE=24000
 *           open=23933 -> near50=23950 (half-mark)    -> CE=23900, PE=24000
 *
 * The open price and strike list are fetched by THIS block itself
 * (oipRSFetchOpenAndStrikes), independent of Opt Prem's own load/state —
 * so the default is always the day's actual open, never a live/current
 * price, and never racing Opt Prem's own fetch timing.
 *
 * Reuses shared globals/helpers already defined in oi_profile.js /
 * oi_indicators.js (oipSymbol, oipInterval, _oipTodayCandles,
 * oipCalculateVWAP, showNotification) — all plain top-level declarations in
 * classic <script> tags loaded earlier on this page, so they're visible
 * here too.
 *
 * Order buttons deliberately use their OWN class (.oip-rs-order-btn, not
 * .oip-order-btn) and their own state (oipRSCurrentCEStrike/PEStrike, own
 * order-mode/limit-price inputs) — oi_profile.js wires every .oip-order-btn
 * on the page to Opt Prem's globals via a single page-wide querySelectorAll,
 * so sharing that class would fire orders against the wrong strike/price.
 *
 * Also draws 5 full-width horizontal reference lines per leg (createPriceLine
 * — spans the whole chart, left and right, like a support/resistance level):
 * Previous Day High, Previous Day Low, Current Day Open, and the First
 * 5-Minute (Opening Range, 09:15–09:20 IST) candle's High and Low — CE in
 * green, PE in blue — recomputed from that leg's own candles on every load
 * (see oipRSComputeRefLines / oipRSDrawRefLines).
 *
 * The chart (follows the TF dropdown) stays live, but issues no request of
 * its own: it needs the same symbol/interval/window as Opt Prem's chart and
 * differs only in strike, so its legs ride along on that request as
 * rs_ce_strike/rs_pe_strike and come back as rs_ce_candles/rs_pe_candles —
 * see oipRSStrikeParams (appended by oi_profile.js's oipLoadCandles) and
 * oipRSRenderFromMain (called by it once the response lands).
 *
 * Indicator show/hide and drawn Ray lines persist across a page refresh via
 * localStorage (see the "Persistence" block below — oipRSSaveIndicatorState/
 * oipRSRestoreIndicatorState, oipRSAddSavedRay/oipRSRemoveSavedRay/
 * oipRSRestoreSavedRays).
 */

'use strict';

let oipRSChart = null, oipRSCESeries = null, oipRSPESeries = null;
let oipRSVwapCESeries = null, oipRSVwapPESeries = null;
let oipRSVolumeSeries = null, oipRSBnfVolumeSeries = null;
let oipRSCurrentCEStrike = null, oipRSCurrentPEStrike = null;

// ── Persistence (localStorage) — indicator show/hide + drawn rays survive a
// page refresh. Indicator state is restored before charts/series are
// created so initial visibility is correct from the first render; rays are
// restored after the first candle load so they can extend to real data.
const OIP_RS_STORAGE_KEY_INDICATORS = 'oipRS_indicators_v1';
const OIP_RS_STORAGE_KEY_RAYS = 'oipRS_rays_v1';
const OIP_RS_INDICATOR_CHECKBOX_IDS = [
    'oipRSShowVwap', 'oipRSShowVolume', 'oipRSShowBnfVolume', 'oipRSShow5mClose',
    'oipRSShowCePdh', 'oipRSShowCePdl', 'oipRSShowCeOpen', 'oipRSShowCe5mHi', 'oipRSShowCe5mLo',
    'oipRSShowPePdh', 'oipRSShowPePdl', 'oipRSShowPeOpen', 'oipRSShowPe5mHi', 'oipRSShowPe5mLo'
];

function oipRSSaveIndicatorState() {
    const state = {};
    OIP_RS_INDICATOR_CHECKBOX_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (el) state[id] = el.checked;
    });
    try { localStorage.setItem(OIP_RS_STORAGE_KEY_INDICATORS, JSON.stringify(state)); } catch (e) {}
}

function oipRSRestoreIndicatorState() {
    let state;
    try { state = JSON.parse(localStorage.getItem(OIP_RS_STORAGE_KEY_INDICATORS) || 'null'); } catch (e) { state = null; }
    if (!state) return;
    OIP_RS_INDICATOR_CHECKBOX_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (el && state[id] != null) el.checked = state[id];
    });
}

function oipRSLoadSavedRays() {
    try { return JSON.parse(localStorage.getItem(OIP_RS_STORAGE_KEY_RAYS) || '[]'); } catch (e) { return []; }
}

function oipRSSaveRaysList(rays) {
    try { localStorage.setItem(OIP_RS_STORAGE_KEY_RAYS, JSON.stringify(rays)); } catch (e) {}
}

function oipRSAddSavedRay(rayInfo) {
    if (!rayInfo) return;
    const rays = oipRSLoadSavedRays();
    rays.push(rayInfo);
    oipRSSaveRaysList(rays);
}

function oipRSRemoveSavedRay(rayInfo) {
    if (!rayInfo) return;
    const rays = oipRSLoadSavedRays().filter(r => !(r.time === rayInfo.time && r.price === rayInfo.price));
    oipRSSaveRaysList(rays);
}

function oipRSClearSavedRays() {
    try { localStorage.removeItem(OIP_RS_STORAGE_KEY_RAYS); } catch (e) {}
}

function oipRSRestoreSavedRays() {
    if (!oipRSChart?.addRay) return;
    oipRSLoadSavedRays().forEach(r => {
        try { oipRSChart.addRay(r.time, r.price, { color: r.color, width: r.width, lineStyle: r.lineStyle }); } catch (e) {}
    });
}

// Horizontal reference lines — Previous Day High, Previous Day Low, Current
// Day Open, and the First 5-Minute (Opening Range) candle's High/Low —
// drawn per-leg (CE green, PE blue) as full-width createPriceLine()s,
// recomputed from that leg's own option-premium candles on every load.
// Keyed by name (not an array) so the Indicators popup checkboxes can
// toggle each one individually.
let oipRSCeRefLineObjs = { pdh: null, pdl: null, open: null, fiveMHi: null, fiveMLo: null };
let oipRSPeRefLineObjs = { pdh: null, pdl: null, open: null, fiveMHi: null, fiveMLo: null };
const OIP_RS_CE_REF_COLOR = '#16a34a'; // green
const OIP_RS_PE_REF_COLOR = '#2563eb'; // blue
const OIP_RS_REF_KEYS = ['pdh', 'pdl', 'open', 'fiveMHi', 'fiveMLo'];

// Per-side (CE / PE) reference-line style pickers — one color/width/style
// applies to ALL 5 lines of that side at once (PDH, PDL, Open, 5m Hi, 5m Lo),
// same idea as the Ray tool's style pickers. Persisted alongside indicator
// show/hide state.
const OIP_RS_CE_STYLE_IDS = { color: 'oipRSCeLineColorInp', width: 'oipRSCeLineWidthSel', style: 'oipRSCeLineStyleSel' };
const OIP_RS_PE_STYLE_IDS = { color: 'oipRSPeLineColorInp', width: 'oipRSPeLineWidthSel', style: 'oipRSPeLineStyleSel' };
const OIP_RS_STORAGE_KEY_LINESTYLE = 'oipRS_lineStyle_v1';

function oipRSLineStyleFromPickers(ids, fallbackColor) {
    return {
        color: document.getElementById(ids.color)?.value || fallbackColor,
        width: parseInt(document.getElementById(ids.width)?.value, 10) || 1,
        lineStyle: parseInt(document.getElementById(ids.style)?.value, 10) || 0
    };
}

function oipRSSaveLineStyleState() {
    const state = {
        ce: oipRSLineStyleFromPickers(OIP_RS_CE_STYLE_IDS, OIP_RS_CE_REF_COLOR),
        pe: oipRSLineStyleFromPickers(OIP_RS_PE_STYLE_IDS, OIP_RS_PE_REF_COLOR),
        // 5m Close Border has a colour only (no width/style — see oipRSMark5mCloseBorders).
        fiveMClose: document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.value || OIP_RS_5M_CLOSE_DEFAULT
    };
    try { localStorage.setItem(OIP_RS_STORAGE_KEY_LINESTYLE, JSON.stringify(state)); } catch (e) {}
}

function oipRSRestoreLineStyleState() {
    let state;
    try { state = JSON.parse(localStorage.getItem(OIP_RS_STORAGE_KEY_LINESTYLE) || 'null'); } catch (e) { state = null; }
    if (!state) return;
    const apply = (ids, saved) => {
        if (!saved) return;
        const c = document.getElementById(ids.color); if (c && saved.color) c.value = saved.color;
        const w = document.getElementById(ids.width); if (w && saved.width != null) w.value = saved.width;
        const s = document.getElementById(ids.style); if (s && saved.lineStyle != null) s.value = saved.lineStyle;
    };
    apply(OIP_RS_CE_STYLE_IDS, state.ce);
    apply(OIP_RS_PE_STYLE_IDS, state.pe);
    const f = document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID);
    if (f && state.fiveMClose) f.value = state.fiveMClose;
}

// Reflects the current CE/PE colors onto the reference-line checkbox labels
// in the Indicators popup, so the swatch text always matches what's drawn.
function oipRSUpdateCheckboxSpanColors() {
    const ceColor = document.getElementById(OIP_RS_CE_STYLE_IDS.color)?.value || OIP_RS_CE_REF_COLOR;
    const peColor = document.getElementById(OIP_RS_PE_STYLE_IDS.color)?.value || OIP_RS_PE_REF_COLOR;
    ['oipRSShowCePdh', 'oipRSShowCePdl', 'oipRSShowCeOpen', 'oipRSShowCe5mHi', 'oipRSShowCe5mLo'].forEach(id => {
        const span = document.getElementById(id)?.nextElementSibling;
        if (span) span.style.color = ceColor;
    });
    ['oipRSShowPePdh', 'oipRSShowPePdl', 'oipRSShowPeOpen', 'oipRSShowPe5mHi', 'oipRSShowPe5mLo'].forEach(id => {
        const span = document.getElementById(id)?.nextElementSibling;
        if (span) span.style.color = peColor;
    });
    const fiveMSpan = document.getElementById('oipRSShow5mClose')?.nextElementSibling;
    if (fiveMSpan) fiveMSpan.style.color = document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.value || OIP_RS_5M_CLOSE_DEFAULT;
}

// Live-restyles already-drawn price lines (no data refetch/redraw needed) —
// used when a style picker changes.
function oipRSApplyLineStyleLive(lineObjRef, style) {
    lineObjRef._style = style;
    OIP_RS_REF_KEYS.forEach(key => {
        const line = lineObjRef[key];
        if (line) {
            try { line.applyOptions({ color: style.color, lineWidth: style.width, lineStyle: style.lineStyle }); } catch (e) {}
        }
    });
}

function oipRSOnCeStyleChange() {
    const style = oipRSLineStyleFromPickers(OIP_RS_CE_STYLE_IDS, OIP_RS_CE_REF_COLOR);
    oipRSApplyLineStyleLive(oipRSCeRefLineObjs, style);
    oipRSUpdateCheckboxSpanColors();
    oipRSSaveLineStyleState();
}

function oipRSOnPeStyleChange() {
    const style = oipRSLineStyleFromPickers(OIP_RS_PE_STYLE_IDS, OIP_RS_PE_REF_COLOR);
    oipRSApplyLineStyleLive(oipRSPeRefLineObjs, style);
    oipRSUpdateCheckboxSpanColors();
    oipRSSaveLineStyleState();
}
// Bar duration (minutes) per interval — used to find which loaded bar(s)
// OVERLAP the 09:15–09:20 Opening Range window rather than start exactly
// inside it (matters for coarser intervals like 15m/30m).
const OIP_RS_BAR_MINUTES = { '30second': 0.5, minute: 1, '2minute': 2, '3minute': 3, '5minute': 5, '15minute': 15, '30minute': 30 };

// Session-open price for strike rounding. Walks backward from the most
// recent day with candles: if today has no candle yet (before market open,
// holiday, etc.) or its open is missing/zero, falls back to the nearest
// PREVIOUS day that has a valid open instead of defaulting to 0.
function oipRSOpenPriceWithFallback(candles) {
    if (typeof _oipGroupCandlesByDay !== 'function' || !candles || !candles.length) return 0;
    const { map, order } = _oipGroupCandlesByDay(candles);
    for (let i = order.length - 1; i >= 0; i--) {
        const dayCandles = map[order[i]];
        if (!dayCandles || !dayCandles.length) continue;
        const open = Number(dayCandles[0].open);
        if (!isNaN(open) && open > 0) return open;
    }
    return 0;
}

function oipRSComputeStrikes(openPrice) {
    const near50 = Math.round(openPrice / 50) * 50;
    if (near50 % 100 === 0) {
        return { ceStrike: near50 - 100, peStrike: near50 + 100 };
    }
    return { ceStrike: near50 - 50, peStrike: near50 + 50 };
}

// Computes Previous Day High, Previous Day Low, Current Day Open, and the
// First 5-Minute (Opening Range, 09:15–09:20 IST) candle's High/Low from a
// single leg's own option-premium candles (CE or PE) — grouped by trading
// day via the shared _oipGroupCandlesByDay helper (oi_indicators.js).
function oipRSComputeRefLines(candles) {
    if (!candles || !candles.length || typeof _oipGroupCandlesByDay !== 'function') return null;
    const { map, order } = _oipGroupCandlesByDay(candles);
    if (!order.length) return null;

    const todayKey = order[order.length - 1];
    const todayCandles = map[todayKey] || [];
    const todayOpen = todayCandles.length ? Number(todayCandles[0].open) : null;

    let pdh = null, pdl = null;
    if (order.length >= 2) {
        const prevCandles = map[order[order.length - 2]] || [];
        if (prevCandles.length) {
            pdh = Math.max(...prevCandles.map(c => Number(c.high)));
            pdl = Math.min(...prevCandles.map(c => Number(c.low)));
        }
    }

    // First 5-min candle (09:15–09:20 IST) — the classic Opening Range.
    // "Fake IST Epoch" timestamps, so UTC getHours/getMinutes already read
    // as IST clock time (same convention _oipGroupCandlesByDay relies on).
    const barMin = OIP_RS_BAR_MINUTES[oipInterval] || 1;
    const WIN_START = 9 * 60 + 15, WIN_END = 9 * 60 + 20;
    const w = todayCandles.filter(c => {
        const d = new Date(c.time * 1000);
        const startMin = d.getUTCHours() * 60 + d.getUTCMinutes();
        return startMin < WIN_END && (startMin + barMin) > WIN_START;
    });
    let fiveMHi = null, fiveMLo = null;
    if (w.length) {
        const hi = Math.max(...w.map(c => Number(c.high)));
        const lo = Math.min(...w.map(c => Number(c.low)));
        if (isFinite(hi) && isFinite(lo)) { fiveMHi = hi; fiveMLo = lo; }
    }

    return { pdh, pdl, todayOpen, fiveMHi, fiveMLo };
}

// Creates/removes individual full-width price lines on `lineObjRef` to
// match `vis` ({pdh,pdl,open,fiveMHi,fiveMLo} booleans), using the values/
// series/color cached on it by oipRSDrawRefLines. Uses lightweight-charts'
// native createPriceLine() — spans the whole chart, left and right, like a
// support/resistance level, and is always correctly positioned by the
// library itself (no manual point/index handling needed).
function oipRSApplyRefVisibility(lineObjRef, vis) {
    const series = lineObjRef._series;
    const refs = lineObjRef._refs;
    const style = lineObjRef._style || {};
    const color = style.color;
    const lineWidth = style.width || 1;
    const lineStyle = style.lineStyle ?? LightweightCharts.LineStyle.Solid;
    if (!series || !refs) return;

    const titles = { pdh: 'PDH', pdl: 'PDL', open: 'Open', fiveMHi: '5m H', fiveMLo: '5m L' };
    const values = { pdh: refs.pdh, pdl: refs.pdl, open: refs.todayOpen, fiveMHi: refs.fiveMHi, fiveMLo: refs.fiveMLo };
    OIP_RS_REF_KEYS.forEach(key => {
        const price = values[key];
        const hasValue = price != null && !isNaN(price);
        const shouldShow = (vis[key] !== false) && hasValue;
        if (shouldShow && !lineObjRef[key]) {
            try {
                lineObjRef[key] = series.createPriceLine({
                    price, color, lineWidth, lineStyle,
                    axisLabelVisible: true, title: titles[key]
                });
            } catch (e) {}
        } else if (!shouldShow && lineObjRef[key]) {
            try { series.removePriceLine(lineObjRef[key]); } catch (e) {}
            lineObjRef[key] = null;
        }
    });
}

// Clears the previously-drawn reference lines, caches the new `refs`/style
// on `lineObjRef` (so later visibility-only toggles can recreate lines
// without a full reload), and draws whichever are enabled per `visibility`.
// `lineObjRef` is the persistent {pdh, pdl, open, ...} object (by
// reference) tracking this leg's price-line instances. `style` is
// {color, width, lineStyle} — one setting shared by all 5 lines of this leg.
function oipRSDrawRefLines(series, lineObjRef, refs, style, visibility) {
    OIP_RS_REF_KEYS.forEach(key => {
        if (lineObjRef[key]) { try { series.removePriceLine(lineObjRef[key]); } catch (e) {} }
        lineObjRef[key] = null;
    });
    lineObjRef._series = series;
    lineObjRef._refs = refs;
    lineObjRef._style = style;
    oipRSApplyRefVisibility(lineObjRef, visibility || {});
}

// Reads the Indicators popup's 6 reference-line checkboxes and adds/removes
// price lines to match (does not refetch data).
function oipRSRefVisibilityFromCheckboxes(side) {
    const get = id => document.getElementById(id)?.checked ?? true;
    return {
        pdh: get(`oipRSShow${side}Pdh`),
        pdl: get(`oipRSShow${side}Pdl`),
        open: get(`oipRSShow${side}Open`),
        fiveMHi: get(`oipRSShow${side}5mHi`),
        fiveMLo: get(`oipRSShow${side}5mLo`)
    };
}

function oipRSSyncRefLineVisibility() {
    oipRSApplyRefVisibility(oipRSCeRefLineObjs, oipRSRefVisibilityFromCheckboxes('Ce'));
    oipRSApplyRefVisibility(oipRSPeRefLineObjs, oipRSRefVisibilityFromCheckboxes('Pe'));
}

function oipRSInitCharts() {
    if (typeof TradingViewChart === 'undefined') return;

    oipRSChart = TradingViewChart.create({
        containerId: 'oipRSCombinedChart', data: [], type: 'COMBINED',
        // Getter (not a plain string) — oipInterval can change via the main
        // TF dropdown after this chart is created (see oi_profile.js), and
        // the ray tool's reach needs the CURRENT interval, not the one at
        // attach time (same reasoning as the main OI chart's ray tool).
        isCombined: true, timeframe: () => oipInterval, options: { height: 575 },
        onRayDrawn: oipRSRayDisarm,
        onRayRemoved: oipRSRemoveSavedRay
    });
    oipRSCESeries = oipRSChart.ceSeries || oipRSChart.series;
    oipRSPESeries = oipRSChart.peSeries;

    // Join the shared cross-chart pan/zoom sync web (OI Profile, Opt Prem) —
    // registered here rather than oi_profile_init.js because oipRSChart
    // doesn't exist until this function runs. Reuses that file's
    // window._oipActiveChartId / window._oipSyncRange / window._oipSyncDepth
    // so a pan/zoom started from any of these charts propagates to all of them.
    // Fixed 24000 Monthly is deliberately NOT part of this web — it is always
    // fixed to a 5-minute interval regardless of what the others are doing, so
    // its bar grid won't line up (it still joins the separate crosshair-sync
    // web, which doesn't care about bar grid alignment — see below).
    ['mouseenter', 'touchstart'].forEach(evt => {
        document.getElementById('oipRSCombinedChart')?.addEventListener(evt, () => { window._oipActiveChartId = 'rs'; }, { passive: true });
    });
    if (oipRSChart?.chart && typeof window._oipSyncRange === 'function') {
        const RS_OPTION_ADJ = 15; // CE Only/PE Only use rightOffset=5 vs this chart's default 20
        oipRSChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
            if (window._oipDataRefreshing || window._oipActiveChartId !== 'rs') return;
            window._oipSyncRange(oipRSChart.chart, [
                oipOIChart,
                oipIntrinsicChart?.chart,
                { chart: oipCEChart?.chart, adj: -RS_OPTION_ADJ },
                { chart: oipPEChart?.chart, adj: -RS_OPTION_ADJ }
            ]);
        });
    }
    // Same shared web for crosshair hover — reuses oi_profile_init.js's
    // syncCrosshair via window._oipSyncCrosshair. oipRSCESeries is this
    // chart's anchor series for price lookup (same role oipIntrinsicSeries
    // plays for Intrinsic).
    if (oipRSChart?.chart && typeof window._oipSyncCrosshair === 'function') {
        oipRSChart.chart.subscribeCrosshairMove(param => {
            if (window._oipActiveChartId !== 'rs') return;
            if (oipOIChart && oipOISeries) window._oipSyncCrosshair(oipRSChart.chart, oipOIChart, param, oipOISeries);
            if (oipIntrinsicChart?.chart && oipIntrinsicSeries) window._oipSyncCrosshair(oipRSChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
            if (oipCEChart?.chart && oipCESeries) window._oipSyncCrosshair(oipRSChart.chart, oipCEChart.chart, param, oipCESeries);
            if (oipPEChart?.chart && oipPESeries) window._oipSyncCrosshair(oipRSChart.chart, oipPEChart.chart, param, oipPESeries);
            if (oipFixedChart?.chart && oipFixedCeSeries) window._oipSyncCrosshair(oipRSChart.chart, oipFixedChart.chart, param, oipFixedCeSeries);
        });
    }

    const showVwap = document.getElementById('oipRSShowVwap')?.checked ?? true;
    oipRSVwapCESeries = oipRSChart.chart.addSeries(LightweightCharts.LineSeries, {
        color: '#1b9981', lineWidth: 1, visible: showVwap,
        priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
    });
    oipRSVwapPESeries = oipRSChart.chart.addSeries(LightweightCharts.LineSeries, {
        color: '#8b5cf6', lineWidth: 1, visible: showVwap,
        priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
    });

    document.getElementById('oipRSShowVwap')?.addEventListener('change', (e) => {
        const v = e.target.checked;
        oipRSVwapCESeries?.applyOptions({ visible: v });
        oipRSVwapPESeries?.applyOptions({ visible: v });
        oipRSSaveIndicatorState();
    });

    // Volume histograms — the same future volumes used on the main OI Profile
    // chart, matched to this block's own candle timestamps. Unlike the other
    // blocks, this one splits the two: Nifty stays on the bottom-pinned hidden
    // scale (as everywhere else), while Banknifty gets its own scale pinned to
    // the TOP of the pane (bnfOnTop) so the two read separately instead of
    // blending into each other — see oipAddVolumeSeriesPair in oi_indicators.js.
    const showVolume = document.getElementById('oipRSShowVolume')?.checked ?? true;
    const showBnfVolume = document.getElementById('oipRSShowBnfVolume')?.checked ?? false;
    [oipRSVolumeSeries, oipRSBnfVolumeSeries] = oipAddVolumeSeriesPair(
        oipRSChart.chart, 'oipRSVolume', showVolume, showBnfVolume, true);

    document.getElementById('oipRSShowVolume')?.addEventListener('change', (e) => {
        oipRSVolumeSeries?.applyOptions({ visible: e.target.checked });
        oipRSSaveIndicatorState();
    });

    document.getElementById('oipRSShowBnfVolume')?.addEventListener('change', (e) => {
        oipRSBnfVolumeSeries?.applyOptions({ visible: e.target.checked });
        oipRSSaveIndicatorState();
    });

    // Keep chart width in sync with its wrapper (same pattern as Opt Prem).
    const wrap = document.getElementById('oipRSCombinedChartWrap');
    if (wrap && oipRSChart?.chart) {
        new ResizeObserver(() => {
            if (wrap.clientWidth) oipRSChart.chart.applyOptions({ width: wrap.clientWidth });
        }).observe(wrap);
    }
}

// Disarms the ray tool and resets the toolbar button — called after a ray is
// drawn (single-shot arm, matches Opt Prem's Ray tool behavior). `rayInfo`
// ({time, price, color, width, lineStyle}) is persisted so the ray survives
// a page refresh (see oipRSRestoreSavedRays, called once on init).
function oipRSRayDisarm(rayInfo) {
    oipRSChart?.setRayMode(false);
    document.getElementById('oipRSRayToolBtn')?.classList.remove('oip-btn--armed');
    document.getElementById('oipRSRayOptionsPopup')?.classList.add('hidden');
    oipRSAddSavedRay(rayInfo);
}

// Color/width/style pickers set the look of the NEXT ray only — read fresh
// on each arm, so changing them mid-session doesn't touch rays already drawn.
function oipRSRayStyleFromPickers() {
    return {
        color: document.getElementById('oipRSRayColorInp')?.value || '#f59e0b',
        width: parseInt(document.getElementById('oipRSRayWidthSel')?.value, 10) || 2,
        lineStyle: parseInt(document.getElementById('oipRSRayStyleSel')?.value, 10) ?? 2
    };
}

function oipRSInitRayTool() {
    const rayBtn = document.getElementById('oipRSRayToolBtn');
    const clearBtn = document.getElementById('oipRSRayClearBtn');
    const popup = document.getElementById('oipRSRayOptionsPopup');
    if (rayBtn) {
        rayBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const willArm = !rayBtn.classList.contains('oip-btn--armed');
            oipRSChart?.setRayMode(willArm, willArm ? oipRSRayStyleFromPickers() : undefined);
            rayBtn.classList.toggle('oip-btn--armed', willArm);
            popup?.classList.toggle('hidden', !willArm);
        });
    }
    // Live-restyle the armed (not-yet-placed) ray as the pickers change.
    if (popup) {
        popup.addEventListener('change', () => {
            if (rayBtn?.classList.contains('oip-btn--armed')) {
                oipRSChart?.setRayMode(true, oipRSRayStyleFromPickers());
            }
        });
    }
    // Clicking outside the popup/button while armed cancels ray mode.
    document.addEventListener('click', (e) => {
        if (!rayBtn?.classList.contains('oip-btn--armed')) return;
        if (popup?.contains(e.target) || e.target === rayBtn || rayBtn.contains(e.target)) return;
        oipRSChart?.setRayMode(false);
        rayBtn.classList.remove('oip-btn--armed');
        popup?.classList.add('hidden');
    });
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            oipRSChart?.clearRays();
            oipRSClearSavedRays();
        });
    }
}

function oipRSInitIndicatorsPopup() {
    const btn = document.getElementById('oipRSIndicatorsBtn');
    const popup = document.getElementById('oipRSIndicatorsPopup');
    if (btn && popup) {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            popup.classList.toggle('hidden');
        });
        document.addEventListener('click', e => {
            if (!popup.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
                popup.classList.add('hidden');
            }
        });
    }

    [
        'oipRSShowCePdh', 'oipRSShowCePdl', 'oipRSShowCeOpen', 'oipRSShowCe5mHi', 'oipRSShowCe5mLo',
        'oipRSShowPePdh', 'oipRSShowPePdl', 'oipRSShowPeOpen', 'oipRSShowPe5mHi', 'oipRSShowPe5mLo'
    ].forEach(id => document.getElementById(id)?.addEventListener('change', () => {
        oipRSSyncRefLineVisibility();
        oipRSSaveIndicatorState();
    }));

    [OIP_RS_CE_STYLE_IDS.color, OIP_RS_CE_STYLE_IDS.width, OIP_RS_CE_STYLE_IDS.style].forEach(id => {
        const el = document.getElementById(id);
        el?.addEventListener(el.type === 'color' ? 'input' : 'change', oipRSOnCeStyleChange);
    });
    [OIP_RS_PE_STYLE_IDS.color, OIP_RS_PE_STYLE_IDS.width, OIP_RS_PE_STYLE_IDS.style].forEach(id => {
        const el = document.getElementById(id);
        el?.addEventListener(el.type === 'color' ? 'input' : 'change', oipRSOnPeStyleChange);
    });
    // 5m Close Border — toggle + colour both re-tag the loaded candles in place.
    document.getElementById('oipRSShow5mClose')?.addEventListener('change', oipRSOn5mCloseChange);
    document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.addEventListener('input', oipRSOn5mCloseChange);
}

// Fetches today's session-open price + the tradable strike list directly
// (its own call, not borrowed from Opt Prem's state) so the CE/PE default
// is always computed from the day's OPEN — never a live/current price, and
// never racing Opt Prem's own load timing.
async function oipRSFetchOpenAndStrikes() {
    const _daysForInterval = { day: 365, week: 1095, month: 3650 };
    const days = _daysForInterval[oipInterval] ?? 5;
    const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&opt_days=${days}&include_30s=false&_t=${Date.now()}`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        if (!data.success) return { openPrice: 0, strikes: [] };

        const openPrice = oipRSOpenPriceWithFallback(data.candles || []);

        // strikes come back as {strike: number} objects — extract, dedupe, sort
        // (same shape/handling as Opt Prem's own dropdown population).
        const rawStrikes = data.strikes || [];
        const strikes = [...new Set(rawStrikes.map(s => parseFloat(s.strike)))]
            .filter(n => !isNaN(n))
            .sort((a, b) => a - b);

        return { openPrice, strikes };
    } catch (e) {
        console.warn('[RoundStrike] open-price fetch error:', e);
        return { openPrice: 0, strikes: [] };
    }
}

function oipRSPopulateDropdown(sel, strikes, selected) {
    if (!sel) return;
    sel.innerHTML = '';
    // If the computed target isn't an exact match (e.g. openPrice was
    // unavailable and near50 landed off the real chain), snap to the
    // nearest real strike instead of leaving the <select> to silently
    // default to whichever option happens to be first in the list.
    let snapTarget = Number(selected);
    if (strikes.length && !strikes.some(s => Number(s) === snapTarget)) {
        snapTarget = strikes.reduce((nearest, s) =>
            Math.abs(s - selected) < Math.abs(nearest - selected) ? s : nearest, strikes[0]);
    }
    strikes.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        if (Number(s) === snapTarget) opt.selected = true;
        sel.appendChild(opt);
    });
}

// The strike pair this block wants appended to oi_profile.js's main candle
// request (see oipLoadCandles). Returning '' — no strikes picked yet, or the
// block isn't on this page — makes the backend skip those legs entirely.
function oipRSStrikeParams() {
    const ceStrike = document.getElementById('oipRSCEStrikeDropdown')?.value;
    const peStrike = document.getElementById('oipRSPEStrikeDropdown')?.value;
    if (!ceStrike || !peStrike) return '';
    return `&rs_ce_strike=${ceStrike}&rs_pe_strike=${peStrike}`;
}

// Set by the callers that need the NEXT render to re-fit the chart (initial
// load, strike change). The render itself is driven by oi_profile.js's poll
// loop, which has no idea a strike just changed, so the intent is parked here
// and consumed by the first render that follows.
let oipRSPendingResetZoom = false;
let oipRSFirstRenderDone = false;
// Last rendered candles, kept UNTAGGED (no 5m-close borderColor) so indicator
// changes can re-tag and redraw them without a refetch — see oipRSOn5mCloseChange.
let oipRSLastCeData = null, oipRSLastPeData = null;

// For callers that already trigger their own reload (e.g. the main TF
// dropdown) and just need this block's next render to re-fit.
function oipRSMarkResetZoom() { oipRSPendingResetZoom = true; }

// ── 5m Close Border indicator ────────────────────────────────────────────────
// This block's own instance of the marker shared with the main OI Profile and
// Opt Prem charts — see oipMark5mCloseBorders in oi_indicators.js for what it
// does and why. Only the toggle/colour source differs: this block keeps its
// own local pickers (the OIP_RS_* convention used throughout this file) rather
// than the generic oipGetLineColor store the two popups above use.
//
// There is deliberately NO width or style picker here (unlike the CE/PE Line
// Style sections): lightweight-charts' candlestick renderer only accepts a
// border COLOUR — the outline is always a 1px solid hairline, so a width/style
// control would be a dead knob.
const OIP_RS_5M_CLOSE_COLOR_ID = 'oipRS5mCloseColorInp';
const OIP_RS_5M_CLOSE_DEFAULT = '#fbbf24';  // amber — reads against green/red (CE) and violet/grey (PE)

function oipRSMark5mCloseBorders(candles) {
    return oipMark5mCloseBorders(
        candles,
        document.getElementById('oipRSShow5mClose')?.checked ?? true,
        document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.value || OIP_RS_5M_CLOSE_DEFAULT
    );
}

// Re-applies the marker to the ALREADY-loaded candles so toggling the checkbox
// or dragging the colour picker takes effect immediately, instead of waiting
// for the next tick of oi_profile.js's poll loop. Re-uses the raw (untagged)
// arrays parked by the last render — no refetch. `refresh=false` so the user's
// current pan/zoom survives.
let oipRS5mCloseRedrawPending = false;

function oipRSOn5mCloseChange() {
    oipRSSaveIndicatorState();
    oipRSSaveLineStyleState();
    oipRSUpdateCheckboxSpanColors();
    if (!oipRSChart || !oipRSLastCeData || oipRS5mCloseRedrawPending) return;
    // Coalesced to one redraw per frame — a colour <input> fires continuously
    // while the picker is dragged, and each redraw is a full setData of both legs.
    oipRS5mCloseRedrawPending = true;
    requestAnimationFrame(() => {
        oipRS5mCloseRedrawPending = false;
        window._oipDataRefreshing = true;
        oipRSChart.update(oipRSMark5mCloseBorders(oipRSLastCeData), oipRSMark5mCloseBorders(oipRSLastPeData), false);
        requestAnimationFrame(() => { window._oipDataRefreshing = false; });
    });
}

// Renders this block's main chart from the SHARED candle response fetched by
// oi_profile.js (rs_ce_candles / rs_pe_candles — see oipRSStrikeParams). This
// block deliberately issues no candle request of its own: it needs the same
// symbol, interval and window as the main chart and only differs in strike,
// so a separate fetch would duplicate an identical request every poll tick.
function oipRSRenderFromMain(data) {
    if (!data || !data.success) return;
    const ceStrike = document.getElementById('oipRSCEStrikeDropdown')?.value;
    const peStrike = document.getElementById('oipRSPEStrikeDropdown')?.value;
    if (!ceStrike || !peStrike) return;

    const resetZoom = oipRSPendingResetZoom;
    oipRSPendingResetZoom = false;

    // A leg whose Fyers history call was rate-limited into failure comes back as
    // [] (the adapter returns an empty list rather than raising), which would
    // otherwise setData() the chart to blank on that poll and repaint it on the
    // next one — the "candles disappear" flicker. Keep the last good candles
    // instead, but ONLY while the strike is unchanged: after a strike switch the
    // parked data belongs to a different contract and must not be shown.
    const sameStrikes = (ceStrike === oipRSCurrentCEStrike && peStrike === oipRSCurrentPEStrike);

    oipRSCurrentCEStrike = ceStrike;
    oipRSCurrentPEStrike = peStrike;

    let ceData = (data.rs_ce_candles || []).map(c => ({ ...c, type: 'CE' }));
    let peData = (data.rs_pe_candles || []).map(c => ({ ...c, type: 'PE' }));
    if (sameStrikes) {
        if (!ceData.length && oipRSLastCeData?.length) ceData = oipRSLastCeData;
        if (!peData.length && oipRSLastPeData?.length) peData = oipRSLastPeData;
    }
    // Parked untagged so the 5m Close Border indicator can be re-applied on a
    // checkbox/colour change without waiting for the next poll (oipRSOn5mCloseChange).
    oipRSLastCeData = ceData;
    oipRSLastPeData = peData;

    // Suppress the cross-chart sync listener (see oipRSInitCharts) while this
    // setData call is in flight — same guard oi_profile.js uses for OI/Opt Prem,
    // so a data refresh can't be mistaken for a user-driven pan/zoom.
    window._oipDataRefreshing = true;
    if (oipRSChart) oipRSChart.update(oipRSMark5mCloseBorders(ceData), oipRSMark5mCloseBorders(peData), resetZoom);

    oipSetVolumeBars(oipRSVolumeSeries, data.future_volume, ceData);
    oipSetVolumeBars(oipRSBnfVolumeSeries, data.banknifty_volume, ceData, 'banknifty');
    const volLegendEl = document.getElementById('oipRSVolLegendItem');
    if (volLegendEl) volLegendEl.classList.toggle('hidden', !data.future_symbol);
    const volSymbolEl = document.getElementById('oipRSLegendVolSymbol');
    if (volSymbolEl) volSymbolEl.textContent = data.future_symbol || '--';

    if (typeof oipCalculateVWAP === 'function') {
        if (oipRSVwapCESeries) oipRSVwapCESeries.setData(oipCalculateVWAP(ceData));
        if (oipRSVwapPESeries) oipRSVwapPESeries.setData(oipCalculateVWAP(peData));
    }

    if (oipRSCESeries) oipRSDrawRefLines(oipRSCESeries, oipRSCeRefLineObjs, oipRSComputeRefLines(ceData), oipRSLineStyleFromPickers(OIP_RS_CE_STYLE_IDS, OIP_RS_CE_REF_COLOR), oipRSRefVisibilityFromCheckboxes('Ce'));
    if (oipRSPESeries) oipRSDrawRefLines(oipRSPESeries, oipRSPeRefLineObjs, oipRSComputeRefLines(peData), oipRSLineStyleFromPickers(OIP_RS_PE_STYLE_IDS, OIP_RS_PE_REF_COLOR), oipRSRefVisibilityFromCheckboxes('Pe'));

    const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
    setText('oipRSLegendCombinedCE', `${ceStrike} CE`);
    setText('oipRSLegendCombinedPE', `${peStrike} PE`);

    requestAnimationFrame(() => { window._oipDataRefreshing = false; });

    // Rays are restored once real data exists, so they extend to it correctly.
    if (!oipRSFirstRenderDone) {
        oipRSFirstRenderDone = true;
        oipRSRestoreSavedRays();
    }
}

function oipRSInitOrderButtons() {
    document.querySelectorAll('.oip-rs-order-btn').forEach(btn => {
        if (btn.id === 'oipRSExitAll') {
            btn.addEventListener('click', () => oipRSExitAllOrders(btn));
        } else if (btn.id === 'oipRSSLCE') {
            btn.addEventListener('click', () => oipRSPlaceSLOrders(btn, 'CE'));
        } else if (btn.id === 'oipRSSLPE') {
            btn.addEventListener('click', () => oipRSPlaceSLOrders(btn, 'PE'));
        } else {
            btn.addEventListener('click', () => oipRSPlaceOrder(btn.dataset.side, btn.dataset.action, btn));
        }
    });

    document.getElementById('oipRSLimitPrice')?.addEventListener('input', (e) => {
        const enabled = parseFloat(e.target.value) > 0;
        const ceBtn = document.getElementById('oipRSSLCE');
        const peBtn = document.getElementById('oipRSSLPE');
        if (ceBtn) ceBtn.disabled = !enabled;
        if (peBtn) peBtn.disabled = !enabled;
    });
}

async function oipRSPlaceOrder(side, action, btn) {
    const strike = (side === 'CE') ? oipRSCurrentCEStrike : oipRSCurrentPEStrike;
    if (!strike) { showNotification(`No ${side} strike available.`, 'error'); return; }

    const mode = document.getElementById('oipRSOrderMode')?.value || 'broker';
    const rawLimit = parseFloat(document.getElementById('oipRSLimitPrice')?.value);
    const limitPrice = rawLimit && !isNaN(rawLimit) && rawLimit > 0 ? rawLimit : null;
    const orderType = limitPrice ? 'LIMIT' : 'MARKET';
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;

    btn.disabled = true;
    const ot = btn.title;
    btn.title = 'Placing...';
    try {
        const endpoint = mode === 'mine' ? '/api/mine-orders' : '/api/orders/place';
        const body = {
            symbol: oipSymbol, strike: strike, option_type: side, action: action,
            strategy: 'intrinsic', order_type: orderType, limit_price: limitPrice
        };
        if (mode === 'mine') body.price = limitPrice || 0;

        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify(body)
        });
        const r = await res.json();
        if (r.success) {
            const modeLabel = mode === 'mine' ? 'Mine' : 'Broker';
            if (orderType === 'LIMIT') {
                showNotification(`${modeLabel}: Limit ₹${limitPrice} queued for ${side} ${strike}`, 'success');
            } else {
                showNotification(`${modeLabel}: ${action} ${side} ${strike} dispatched`, 'success');
            }
        } else {
            showNotification(`${r.error || 'Order failed'}`, 'error');
        }
    } catch (e) {
        showNotification(`Order error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.title = ot;
    }
}

async function oipRSPlaceSLOrders(btn, side) {
    const triggerPrice = parseFloat(document.getElementById('oipRSLimitPrice')?.value);
    if (!(triggerPrice > 0)) return;

    const strike = (side === 'CE') ? oipRSCurrentCEStrike : oipRSCurrentPEStrike;
    if (!strike) {
        showNotification('No CE/PE strike loaded — load data first.', 'error');
        return;
    }

    btn.disabled = true;
    const origText = btn.innerText;
    btn.innerText = 'PLACING...';
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;

    try {
        const res = await fetch('/api/order/place-sl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify({ symbol: oipSymbol, strike: strike, option_type: side, trigger_price: triggerPrice })
        });
        const r = await res.json();
        if (r.success) {
            showNotification(`SL placed for ${side} ${strike}.`, 'success');
        } else {
            const brokerErrors = (r.results || []).filter(b => !b.success).map(b => b.error || b.message || 'Unknown error');
            showNotification(`SL failed: ${brokerErrors.length ? brokerErrors.join(', ') : (r.error || 'Unknown error')}`, 'error');
        }
    } catch (e) {
        showNotification(`SL error: ${e.message}`, 'error');
    }

    btn.innerText = origText;
    // Stays disabled — user must re-enter/change price to re-fire (matches Opt Prem's guard against double placement).
}

async function oipRSExitAllOrders(btn) {
    // Double-click confirm: first click arms, second click within 3s executes.
    // NOTE: /api/order/exit-all is account-wide (no strike/strategy scoping),
    // same endpoint Opt Prem's own EXIT button calls — intentional, it's a
    // global panic-button reachable from either block.
    if (!btn._exitArmed) {
        btn._exitArmed = true;
        const prev = btn.innerText;
        btn.innerText = 'CONFIRM?';
        btn.style.background = '#7f1d1d';
        if (btn._exitArmTimer) clearTimeout(btn._exitArmTimer);
        btn._exitArmTimer = setTimeout(() => {
            btn._exitArmed = false;
            btn.innerText = prev;
            btn.style.background = '';
        }, 3000);
        return;
    }

    btn._exitArmed = false;
    if (btn._exitArmTimer) { clearTimeout(btn._exitArmTimer); btn._exitArmTimer = null; }

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
        showNotification(`Exit error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerText = 'EXIT';
        btn.style.opacity = '1';
    }
}

// Asks oi_profile.js to refetch now, so this block's strikes reach the backend
// without waiting for the next poll tick. resetZoom stays false — the main
// chart shouldn't jump because a Round Strike dropdown changed; this block's
// own re-fit rides on oipRSPendingResetZoom instead.
// includeFixed=false: the Fixed Monthly chart has its own Update/Refresh
// buttons and is deliberately kept off routine reloads, so a Round Strike
// dropdown change shouldn't drag its fetch along.
function oipRSRequestReload() {
    oipRSPendingResetZoom = true;
    if (typeof oipLoadCandles === 'function') oipLoadCandles(true, false, false);
}

async function oipRSInit() {
    if (!document.getElementById('oipRSCombinedChart')) return; // block not present on this page
    oipRSRestoreIndicatorState(); // before chart/series creation — VWAP's initial visibility reads the checkbox
    oipRSRestoreLineStyleState(); // before first load — CE/PE ref-line color/width/style pickers
    oipRSUpdateCheckboxSpanColors();
    oipRSInitCharts();

    const { openPrice, strikes } = await oipRSFetchOpenAndStrikes();
    const { ceStrike, peStrike } = oipRSComputeStrikes(openPrice);

    oipRSPopulateDropdown(document.getElementById('oipRSCEStrikeDropdown'), strikes, ceStrike);
    oipRSPopulateDropdown(document.getElementById('oipRSPEStrikeDropdown'), strikes, peStrike);

    // A strike change reloads the chart through the shared request — the new
    // strikes land in the URL oi_profile.js builds.
    const onStrikeChange = () => oipRSRequestReload();
    document.getElementById('oipRSCEStrikeDropdown')?.addEventListener('change', onStrikeChange);
    document.getElementById('oipRSPEStrikeDropdown')?.addEventListener('change', onStrikeChange);

    oipRSInitOrderButtons();
    oipRSInitRayTool();
    oipRSInitIndicatorsPopup();

    // The strikes above were only just resolved, so oi_profile.js's first
    // request went out without them — kick one more off now to pick them up.
    // Rays are restored by the render that follows (see oipRSRenderFromMain).
    oipRSRequestReload();
}

document.addEventListener('DOMContentLoaded', () => { oipRSInit(); });
