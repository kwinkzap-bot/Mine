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
 * Indicator show/hide and drawn Ray lines persist across a page refresh via
 * localStorage (see the "Persistence" block below — oipRSSaveIndicatorState/
 * oipRSRestoreIndicatorState, oipRSAddSavedRay/oipRSRemoveSavedRay/
 * oipRSRestoreSavedRays).
 */

'use strict';

let oipRSChart = null, oipRSCESeries = null, oipRSPESeries = null;
let oipRSVwapCESeries = null, oipRSVwapPESeries = null;
let oipRSCurrentCEStrike = null, oipRSCurrentPEStrike = null;
let oipRSCandleTimer = null, oipRSIsBusy = false;

// ── Persistence (localStorage) — indicator show/hide + drawn rays survive a
// page refresh. Indicator state is restored before charts/series are
// created so initial visibility is correct from the first render; rays are
// restored after the first candle load so they can extend to real data.
const OIP_RS_STORAGE_KEY_INDICATORS = 'oipRS_indicators_v1';
const OIP_RS_STORAGE_KEY_RAYS = 'oipRS_rays_v1';
const OIP_RS_INDICATOR_CHECKBOX_IDS = [
    'oipRSShowVwap',
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
    const color = lineObjRef._color;
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
                    price, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true, title: titles[key]
                });
            } catch (e) {}
        } else if (!shouldShow && lineObjRef[key]) {
            try { series.removePriceLine(lineObjRef[key]); } catch (e) {}
            lineObjRef[key] = null;
        }
    });
}

// Clears the previously-drawn reference lines, caches the new `refs`/color
// on `lineObjRef` (so later visibility-only toggles can recreate lines
// without a full reload), and draws whichever are enabled per `visibility`.
// `lineObjRef` is the persistent {pdh, pdl, open, ...} object (by
// reference) tracking this leg's price-line instances.
function oipRSDrawRefLines(series, lineObjRef, refs, color, visibility) {
    OIP_RS_REF_KEYS.forEach(key => {
        if (lineObjRef[key]) { try { series.removePriceLine(lineObjRef[key]); } catch (e) {} }
        lineObjRef[key] = null;
    });
    lineObjRef._series = series;
    lineObjRef._refs = refs;
    lineObjRef._color = color;
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
        isCombined: true, timeframe: oipInterval, options: { height: 375 },
        onRayDrawn: oipRSRayDisarm,
        onRayRemoved: oipRSRemoveSavedRay
    });
    oipRSCESeries = oipRSChart.ceSeries || oipRSChart.series;
    oipRSPESeries = oipRSChart.peSeries;

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
}

// Fetches today's session-open price + the tradable strike list directly
// (its own call, not borrowed from Opt Prem's state) so the CE/PE default
// is always computed from the day's OPEN — never a live/current price, and
// never racing Opt Prem's own load timing.
async function oipRSFetchOpenAndStrikes() {
    const _daysForInterval = { day: 365, week: 1095, month: 3650 };
    const days = _daysForInterval[oipInterval] ?? 5;
    const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&opt_days=${days}&_t=${Date.now()}`;

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

async function oipRSLoadCandles(resetZoom = false) {
    const ceStrike = document.getElementById('oipRSCEStrikeDropdown')?.value;
    const peStrike = document.getElementById('oipRSPEStrikeDropdown')?.value;
    if (!ceStrike || !peStrike) return;

    const _daysForInterval = { day: 365, week: 1095, month: 3650 };
    const days = _daysForInterval[oipInterval] ?? 5;
    const url = `/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=${days}&opt_days=${days}&ce_strike=${ceStrike}&pe_strike=${peStrike}&_t=${Date.now()}`;

    let data;
    try {
        const res = await fetch(url);
        data = await res.json();
    } catch (e) {
        console.warn('[RoundStrike] fetch error:', e);
        return;
    }
    if (!data.success) return;

    oipRSCurrentCEStrike = ceStrike;
    oipRSCurrentPEStrike = peStrike;

    const ceData = (data.ce_opt_candles || []).map(c => ({ ...c, type: 'CE' }));
    const peData = (data.pe_opt_candles || []).map(c => ({ ...c, type: 'PE' }));

    if (oipRSChart) oipRSChart.update(ceData, peData, resetZoom);

    if (typeof oipCalculateVWAP === 'function') {
        if (oipRSVwapCESeries) oipRSVwapCESeries.setData(oipCalculateVWAP(ceData));
        if (oipRSVwapPESeries) oipRSVwapPESeries.setData(oipCalculateVWAP(peData));
    }

    if (oipRSCESeries) oipRSDrawRefLines(oipRSCESeries, oipRSCeRefLineObjs, oipRSComputeRefLines(ceData), OIP_RS_CE_REF_COLOR, oipRSRefVisibilityFromCheckboxes('Ce'));
    if (oipRSPESeries) oipRSDrawRefLines(oipRSPESeries, oipRSPeRefLineObjs, oipRSComputeRefLines(peData), OIP_RS_PE_REF_COLOR, oipRSRefVisibilityFromCheckboxes('Pe'));

    const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
    setText('oipRSLegendCombinedCE', `${ceStrike} CE`);
    setText('oipRSLegendCombinedPE', `${peStrike} PE`);
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

// Recurring background refresh — mirrors Opt Prem's oipScheduleCandleLoop/
// oipCandleLoop (oi_profile.js) so the Round Strike chart keeps pulling live
// candles the same way Fixed Monthly does (Fixed Monthly gets this for free
// since it rides on Opt Prem's own request; Round Strike uses its own
// strikes so it needs its own loop). Same cadence: 1s while the market's
// open, slower otherwise, paused while the tab is hidden.
function oipRSScheduleLoop(delay) {
    if (window.oipReplayMode) return;
    if (oipRSCandleTimer) clearTimeout(oipRSCandleTimer);
    oipRSCandleTimer = setTimeout(() => {
        if (!document.hidden) oipRSLoop();
        else oipRSScheduleLoop(10000);
    }, delay);
}

async function oipRSLoop() {
    if (oipRSIsBusy) return;
    const isMarketOpen = typeof oipIsMarketOpen === 'function' ? oipIsMarketOpen() : true;
    oipRSIsBusy = true;
    let success = false;
    try {
        await oipRSLoadCandles(false);
        success = true;
    } catch (err) {
        console.warn('[RoundStrike] Candle loop error:', err);
    } finally {
        oipRSIsBusy = false;
        const delay = isMarketOpen ? (success ? 1000 : 2000) : 300000;
        oipRSScheduleLoop(delay);
    }
}

async function oipRSInit() {
    if (!document.getElementById('oipRSCombinedChart')) return; // block not present on this page
    oipRSRestoreIndicatorState(); // before chart/series creation — VWAP's initial visibility reads the checkbox
    oipRSInitCharts();

    const { openPrice, strikes } = await oipRSFetchOpenAndStrikes();
    const { ceStrike, peStrike } = oipRSComputeStrikes(openPrice);

    oipRSPopulateDropdown(document.getElementById('oipRSCEStrikeDropdown'), strikes, ceStrike);
    oipRSPopulateDropdown(document.getElementById('oipRSPEStrikeDropdown'), strikes, peStrike);

    document.getElementById('oipRSCEStrikeDropdown')?.addEventListener('change', () => oipRSLoadCandles(true));
    document.getElementById('oipRSPEStrikeDropdown')?.addEventListener('change', () => oipRSLoadCandles(true));

    oipRSInitOrderButtons();
    oipRSInitRayTool();
    oipRSInitIndicatorsPopup();
    await oipRSLoadCandles(true);
    oipRSRestoreSavedRays(); // after real data loads, so rays extend to it correctly

    oipRSScheduleLoop(typeof oipIsMarketOpen === 'function' && oipIsMarketOpen() ? 1000 : 300000);
}

document.addEventListener('DOMContentLoaded', () => { oipRSInit(); });
