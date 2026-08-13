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
 * oi_indicators.js (oipSymbol, _oipTodayCandles, oipCalculateVWAP,
 * showNotification) — all plain top-level declarations in classic <script>
 * tags loaded earlier on this page, so they're visible here too. The
 * timeframe is NOT shared: this block has its own dropdown and its own
 * oipRSInterval (see below).
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
 * This block owns ALL of its data end to end: everything it draws — the CE/PE
 * candles, both volume overlays, and every pill in the stats strip above the
 * chart (Price, CE OI, PE OI, ATM, CPR, VWAP Bias, 9:18 Bias, PCR, Lot, Trend,
 * IVP) — comes from ONE request to /api/oi-profile/round-strike, polled every
 * 1 second while the market is open (see oipRSLoadData / oipRSScheduleLoop).
 * It rode along on oi_profile.js's shared candle request before; the stats
 * strip then updated on that file's slower /api/open-interest loop, which is
 * why those pills lagged the chart. Nothing here reads oi_profile.js's request
 * any more, and oi_profile.js in turn leaves the strip alone (it checks
 * window._oipRSOwnsHeader).
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

// This block's OWN timeframe (#oipRSInterval in its header) — deliberately not
// oi_profile.js's oipInterval, which drives every other chart on the page from
// the Opt Prem header. The two are independent: this block is the live 1-second
// view and tends to sit on a fast timeframe, while the rest of the page only
// reloads on Refresh All and defaults to 5m. Must match the <option selected>
// on that dropdown.
let oipRSInterval = 'minute';

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
    const barMin = OIP_RS_BAR_MINUTES[oipRSInterval] || 1;
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
        // Getter (not a plain string) — oipRSInterval changes via this block's
        // own TF dropdown after the chart is created, and the ray tool's reach
        // needs the CURRENT interval, not the one at attach time (same
        // reasoning as the main OI chart's ray tool).
        isCombined: true, timeframe: () => oipRSInterval, options: { height: 575 },
        onRayDrawn: oipRSRayDisarm,
        onRayRemoved: oipRSRemoveSavedRay
    });
    oipRSCESeries = oipRSChart.ceSeries || oipRSChart.series;
    oipRSPESeries = oipRSChart.peSeries;

    // This chart is deliberately NOT part of the shared pan/zoom sync web that
    // the OI Profile and Opt Prem charts form (see oi_profile_init.js). That web
    // syncs LOGICAL ranges — bar indices — which only means the same thing when
    // every chart is on the same bar grid. This block has its own TF dropdown
    // now, so a pan here would scroll the others to the wrong place whenever the
    // two timeframes differ. Same reason Fixed 24000 Monthly stays out of it.
    //
    // It does join the crosshair-sync web below: that one matches on TIME, so it
    // works across mismatched bar grids.
    ['mouseenter', 'touchstart'].forEach(evt => {
        document.getElementById('oipRSCombinedChart')?.addEventListener(evt, () => { window._oipActiveChartId = 'rs'; }, { passive: true });
    });
    // Crosshair hover — reuses oi_profile_init.js's
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
        color: document.getElementById('oipRSRayColorInp')?.value || '#f33968',
        width: parseInt(document.getElementById('oipRSRayWidthSel')?.value, 10) || 2,
        lineStyle: parseInt(document.getElementById('oipRSRayStyleSel')?.value, 10) ?? 1
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

// ── Data layer — /api/oi-profile/round-strike ────────────────────────────────
// One request per tick carries this block's whole picture: CE/PE candles, both
// volume overlays and the stats strip. Strikes are optional on it — the very
// first call goes out without them precisely to LEARN which pair to ask for
// (session_open + strikes), and the option legs come back empty until it does.

function oipRSApiUrl(withStrikes = true) {
    const _daysForInterval = { day: 365, week: 1095, month: 3650 };
    const days = _daysForInterval[oipRSInterval] ?? 5;
    const step = (typeof oipStrikeStep !== 'undefined' && oipStrikeStep) || 50;
    let url = `/api/oi-profile/round-strike?symbol=${oipSymbol}&interval=${oipRSInterval}&days=${days}&step=${step}`;
    if (withStrikes) {
        const ce = document.getElementById('oipRSCEStrikeDropdown')?.value;
        const pe = document.getElementById('oipRSPEStrikeDropdown')?.value;
        if (ce && pe) url += `&ce_strike=${ce}&pe_strike=${pe}`;
    }
    return `${url}&_t=${Date.now()}`;
}

// First call — no strikes yet. Returns the day's OPEN (never a live/current
// price, so the round-strike default is stable through the session) plus the
// tradable strike list for the two dropdowns.
async function oipRSFetchOpenAndStrikes() {
    try {
        const res = await fetch(oipRSApiUrl(false));
        const data = await res.json();
        if (!data.success) return { openPrice: 0, strikes: [] };

        // The stats strip can already be painted from this first response —
        // no reason to leave it on '--' until the strikes resolve.
        oipRSApplyHeader(data.header);

        // strikes come back as {strike: number} objects — extract, dedupe, sort
        // (same shape/handling as Opt Prem's own dropdown population).
        const strikes = [...new Set((data.strikes || []).map(s => parseFloat(s.strike)))]
            .filter(n => !isNaN(n))
            .sort((a, b) => a - b);

        return { openPrice: Number(data.session_open) || 0, strikes };
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

// Set by the callers that need the NEXT render to re-fit the chart (initial
// load, strike change). The render itself is driven by this block's own poll
// loop, which has no idea a strike just changed, so the intent is parked here
// and consumed by the first render that follows.
let oipRSPendingResetZoom = false;
let oipRSFirstRenderDone = false;
// Last rendered candles, kept UNTAGGED (no 5m-close borderColor) so indicator
// changes can re-tag and redraw them without a refetch — see oipRSOn5mCloseChange.
let oipRSLastCeData = null, oipRSLastPeData = null;
// Same idea for the two volume overlays. The candles had this guard from the
// start; the volume bars did not, so a rate-limited future leg arrived as [] and
// oipSetVolumeBars setData([])'d the whole row to nothing for that tick — the
// empty volume the block was reported for. Parked per interval, since bars from
// a different timeframe sit on a different time grid and must not be reused.
let oipRSLastFutVol = null, oipRSLastBnfVol = null, oipRSLastVolInterval = null;

// ── 5m Close Border indicator ────────────────────────────────────────────────
// This block's own instance of the marker shared with the main OI Profile and
// Opt Prem charts — see oipMark5mCloseBorders in oi_indicators.js for what it
// does and why. Two things differ: this block keeps its own toggle/colour
// pickers (the OIP_RS_* convention used throughout this file) rather than the
// generic oipGetLineColor store the two popups above use, and it marks against
// its OWN timeframe — which bar closes a 5-minute block depends on the bar
// width, and oipRSInterval is independent of the page's oipInterval.
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
        document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.value || OIP_RS_5M_CLOSE_DEFAULT,
        oipRSInterval
    );
}

// Re-applies the marker to the ALREADY-loaded candles so toggling the checkbox
// or dragging the colour picker takes effect immediately, instead of waiting
// for the next tick of this block's poll loop. Re-uses the raw (untagged)
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

// Renders this block's chart from its own /api/oi-profile/round-strike
// response (see oipRSLoadData). The stats strip is painted separately by
// oipRSApplyHeader from the `header` object on that same response.
function oipRSRenderChart(data) {
    if (!data || !data.success) return;
    const ceStrike = document.getElementById('oipRSCEStrikeDropdown')?.value;
    const peStrike = document.getElementById('oipRSPEStrikeDropdown')?.value;
    if (!ceStrike || !peStrike) return;
    // A response that raced ahead of a strike change carries the OLD contract's
    // candles — charting them under the new labels would show the wrong strike
    // for a tick. Drop it; the request for the new pair is already in flight.
    if (String(data.ce_strike) !== String(ceStrike) || String(data.pe_strike) !== String(peStrike)) return;

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

    let ceData = (data.ce_candles || []).map(c => ({ ...c, type: 'CE' }));
    let peData = (data.pe_candles || []).map(c => ({ ...c, type: 'PE' }));
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

    // Same anti-flicker rule as the candles above: an empty array means the
    // future leg failed this tick, not that volume went to nothing. Hold the
    // last good bars — but only while the timeframe is unchanged, since bars
    // from another interval sit on a different time grid and oipSetVolumeBars
    // matches them to the candles by exact time key.
    const sameVolInterval = (oipRSLastVolInterval === oipRSInterval);
    let futVol = data.future_volume || [];
    let bnfVol = data.banknifty_volume || [];
    if (sameVolInterval) {
        if (!futVol.length && oipRSLastFutVol?.length) futVol = oipRSLastFutVol;
        if (!bnfVol.length && oipRSLastBnfVol?.length) bnfVol = oipRSLastBnfVol;
    }
    oipRSLastFutVol = futVol;
    oipRSLastBnfVol = bnfVol;
    oipRSLastVolInterval = oipRSInterval;

    oipSetVolumeBars(oipRSVolumeSeries, futVol, ceData);
    // Banknifty deliberately uses the NIFTY colour pair here. Everywhere else the
    // two histograms share one scale and overlap, so Banknifty needs its own
    // colours to stay distinguishable; on this chart it hangs from its own top
    // band (bnfOnTop), so the same up/down pair reads consistently across both
    // bands instead of introducing a second colour language. The Banknifty
    // swatches are omitted from this block's Indicator popup for that reason.
    oipSetVolumeBars(oipRSBnfVolumeSeries, bnfVol, ceData);
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

// ── Stats strip ──────────────────────────────────────────────────────────────
// Every pill above the chart, straight off the `header` object in this block's
// own response. oi_profile.js used to write these from /api/open-interest on a
// 30-second loop; it now stands down (window._oipRSOwnsHeader) so the two can't
// fight over the same nodes with data of different ages.
//
// A missing value LEAVES THE PILL AS IT IS rather than blanking it: the OI
// snapshot behind half of these is written about once a minute, so on the ticks
// in between there is genuinely nothing new to say — and a number that flickers
// to '--' 20 times a minute is worse than one that simply holds.
function oipRSApplyHeader(h) {
    if (!h) return;
    const setVal = (id, text, cls) => {
        const el = document.getElementById(id);
        if (!el || text == null) return;
        el.textContent = text;
        if (cls !== undefined) el.className = 'oip-hdr-val' + (cls ? ' ' + cls : '');
    };
    const fmtOI = (typeof fmtL === 'function') ? fmtL : (v => v == null ? '--' : String(v));

    if (h.price) setVal('hdrPrice', Number(h.price).toLocaleString('en-IN', { minimumFractionDigits: 2 }));
    if (h.ce_oi != null) setVal('hdrCeOI', fmtOI(h.ce_oi), 'red');
    if (h.pe_oi != null) setVal('hdrPeOI', fmtOI(h.pe_oi), 'grn');
    if (h.atm != null) setVal('hdrAtm', h.atm);
    if (h.lot_size) setVal('hdrLotSize', h.lot_size);
    if (h.trend) setVal('hdrTrend', h.trend, h.trend === 'Bullish' ? 'grn' : (h.trend === 'Bearish' ? 'red' : ''));
    if (h.vwap_bias) setVal('hdrVwapBias', h.vwap_bias, h.vwap_bias === 'DOWN' ? 'red' : 'grn');
    if (h.atm_ce_oi_bias) setVal('hdrAtmCeOiBias', h.atm_ce_oi_bias, h.atm_ce_oi_bias === 'DOWN' ? 'red' : 'grn');

    // PCR carries its own card background at the extremes (the same >=1.7 /
    // <=0.7 thresholds the header has always used), so it needs more than setVal.
    if (h.pcr != null) {
        const pcr = Number(h.pcr);
        setVal('hdrPcr', pcr.toFixed(2));
        const card = document.getElementById('hdrPcrCard');
        if (card) {
            const dark = pcr >= 1.7 || pcr <= 0.7;
            card.style.background = pcr >= 1.7 ? '#7f1d1d' : (pcr <= 0.7 ? '#14532d' : '');
            card.querySelectorAll('.oip-hdr-lbl, .oip-hdr-val')
                .forEach(el => { el.style.color = dark ? '#ffffff' : ''; });
        }
    }

    if (typeof h.iv_percentile === 'number') {
        setVal('hdrIVP', h.iv_percentile.toFixed(1) + '%');
        if (typeof oipUpdateIVPGauge === 'function') oipUpdateIVPGauge(h.iv_percentile);
    }
    document.getElementById('ivCrushAlert')?.classList.toggle('hidden', !h.iv_crush_alert);

    // CPR keeps oi_profile.js's renderer: the card's Index/Future toggle lives
    // there and reads the same oipCprData, so feeding it here keeps one code
    // path for both the click and the poll.
    if (h.cpr && (h.cpr.index || h.cpr.future) && typeof oipRenderCprCard === 'function') {
        oipCprData = h.cpr;
        oipRenderCprCard();
    }
}

// ── Poll loop ────────────────────────────────────────────────────────────────
// 1 second while the market is open, 5 minutes when it is closed (nothing
// moves), and paused entirely while the tab is hidden — a background tab
// polling a broker API 60 times a minute earns nothing but rate-limit pressure.
// Self-rescheduling rather than setInterval, so a slow tick can never stack a
// second request on top of the one still in flight: if a tick takes longer than
// the interval, the next one simply starts when it lands.
//
// This is the ONLY live feed on the page. Every other chart here is static
// until the Refresh All button is pressed (see oipFullRefresh in oi_profile.js),
// so the whole page's broker traffic is this loop.
const OIP_RS_POLL_MS = 1000;
const OIP_RS_POLL_MS_CLOSED = 300000;
const OIP_RS_POLL_MS_HIDDEN = 10000;   // re-check for tab visibility, no request
const OIP_RS_POLL_MS_ERROR = 3000;     // back off on failure rather than pile on
let oipRSPollTimer = null;
let oipRSIsLoading = false;
// Set when a reload is asked for while one is already in flight (a strike or TF
// change lands mid-request): that response is about to be discarded as stale,
// so the loop goes straight round again instead of waiting out a full tick.
let oipRSReloadPending = false;

function oipRSScheduleLoop(delay) {
    if (window.oipReplayMode) return;
    if (oipRSPollTimer) clearTimeout(oipRSPollTimer);
    oipRSPollTimer = setTimeout(() => {
        if (document.hidden) { oipRSScheduleLoop(OIP_RS_POLL_MS_HIDDEN); return; }
        oipRSPollTick();
    }, delay);
}

async function oipRSPollTick() {
    // Another tick is still waiting on the network — it schedules the next one
    // itself when it lands, so this one bows out rather than spinning. The flag
    // marks that response as already superseded (this tick was woken by a
    // strike/TF change), which sends the loop straight round again.
    if (oipRSIsLoading) { oipRSReloadPending = true; return; }

    const marketOpen = (typeof oipIsMarketOpen !== 'function') || oipIsMarketOpen();
    let ok = false;
    try {
        ok = await oipRSLoadData();
    } finally {
        if (oipRSReloadPending) {
            oipRSReloadPending = false;
            oipRSScheduleLoop(0);
        } else {
            oipRSScheduleLoop(!marketOpen ? OIP_RS_POLL_MS_CLOSED
                : (ok ? OIP_RS_POLL_MS : OIP_RS_POLL_MS_ERROR));
        }
    }
}

// Shows/hides the "delayed" chip beside the Live 1s badge.
//
// `reason` is the backend's data_stale/fetch_error string, or null when the tick
// was clean. A single throttled tick is routine and self-healing, so the chip
// only appears once the problem has survived a couple of polls — otherwise it
// would blink on and off all session and train the user to ignore it. Recovery
// clears it immediately: good news should never be delayed.
const OIP_RS_STALE_TICKS_BEFORE_WARN = 3;
let oipRSStaleStreak = 0;
function oipRSSetStaleChip(reason) {
    const el = document.getElementById('oipRSStaleChip');
    if (!el) return;
    if (!reason) {
        oipRSStaleStreak = 0;
        el.classList.add('hidden');
        el.title = '';
        return;
    }
    oipRSStaleStreak++;
    if (oipRSStaleStreak < OIP_RS_STALE_TICKS_BEFORE_WARN) return;
    el.textContent = 'Delayed';
    el.title = reason;          // full broker message on hover
    el.classList.remove('hidden');
}

// One request → chart + stats strip. Returns whether it succeeded, so the loop
// above can back off instead of hammering a broker that is already struggling.
async function oipRSLoadData() {
    if (oipRSIsLoading) {          // a tick is still in flight — not an error
        oipRSReloadPending = true;
        return true;
    }
    oipRSIsLoading = true;
    try {
        const res = await fetch(oipRSApiUrl(true));
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'request failed');
        if (data.fetch_error) console.warn('[RoundStrike]', data.fetch_error);
        oipRSApplyHeader(data.header);
        oipRSRenderChart(data);
        oipRSSetStaleChip(data.data_stale || data.fetch_error || null);
        // A tick the broker refused still parses fine, so it used to return true
        // and the loop went straight back round at 1s — piling more requests onto
        // a rate limit we were already over. Report it as a failure so the caller
        // picks OIP_RS_POLL_MS_ERROR and gives the broker room to recover.
        return !data.fetch_error;
    } catch (e) {
        console.warn('[RoundStrike] load error:', e);
        oipRSSetStaleChip('Connection problem — chart not updating');
        return false;
    } finally {
        oipRSIsLoading = false;
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
            // Prefixed with broker + instance: the common failures here are
            // per-account (an expired token on one login), and an unlabelled
            // list gives no clue which account to fix.
            const brokerErrors = (r.results || []).filter(b => !b.success)
                .map(b => `${b.broker || '?'}${b.instance ? ' ' + b.instance : ''}: ${b.error || b.message || 'Unknown error'}`);
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

// Refetches NOW instead of waiting out the rest of the current 1-second tick —
// used when the user changes something this block's request depends on (a
// strike, the TF dropdown). The next render re-fits the chart, since the bars
// it is about to draw belong to a different contract or timeframe.
function oipRSRequestReload() {
    oipRSPendingResetZoom = true;
    oipRSScheduleLoop(0);
}

async function oipRSInit() {
    if (!document.getElementById('oipRSCombinedChart')) return; // block not present on this page
    // Tells oi_profile.js to leave the stats strip alone — this block repaints
    // it from its own request every second (see oipRSApplyHeader).
    window._oipRSOwnsHeader = true;
    oipRSRestoreIndicatorState(); // before chart/series creation — VWAP's initial visibility reads the checkbox
    oipRSRestoreLineStyleState(); // before first load — CE/PE ref-line color/width/style pickers
    oipRSUpdateCheckboxSpanColors();

    // This block's own TF — read before the chart is built (its ray tool and the
    // 5m reference-line window both key off the interval) and independent of the
    // Opt Prem TF dropdown that drives every other chart on the page.
    const tfSel = document.getElementById('oipRSInterval');
    if (tfSel?.value) oipRSInterval = tfSel.value;
    tfSel?.addEventListener('change', e => {
        oipRSInterval = e.target.value;
        oipRSRequestReload();   // re-fit and re-request at the new bar width
    });

    oipRSInitCharts();

    const { openPrice, strikes } = await oipRSFetchOpenAndStrikes();
    const { ceStrike, peStrike } = oipRSComputeStrikes(openPrice);

    oipRSPopulateDropdown(document.getElementById('oipRSCEStrikeDropdown'), strikes, ceStrike);
    oipRSPopulateDropdown(document.getElementById('oipRSPEStrikeDropdown'), strikes, peStrike);

    // A strike change re-aims this block's own request at the new pair.
    const onStrikeChange = () => oipRSRequestReload();
    document.getElementById('oipRSCEStrikeDropdown')?.addEventListener('change', onStrikeChange);
    document.getElementById('oipRSPEStrikeDropdown')?.addEventListener('change', onStrikeChange);

    oipRSInitOrderButtons();
    oipRSInitRayTool();
    oipRSInitIndicatorsPopup();

    // First real tick — the strikes only just resolved, so the call above went
    // out without them. Rays are restored by the render that follows (see
    // oipRSRenderChart), and every tick after this one is scheduled by the loop.
    oipRSPendingResetZoom = true;
    oipRSScheduleLoop(0);

    // A tab that comes back to the foreground should show current data at once
    // rather than up to OIP_RS_POLL_MS_HIDDEN of staleness.
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) oipRSScheduleLoop(0);
    });
}

document.addEventListener('DOMContentLoaded', () => { oipRSInit(); });
