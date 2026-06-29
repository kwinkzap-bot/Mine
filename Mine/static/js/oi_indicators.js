/**
 * oi_indicators.js
 * All indicator state, calculations, draw functions, and popup logic for OI Profile.
 * Loaded before oi_profile.js so global state is available during chart init.
 */

'use strict';

/* ── Indicator series state ───────────────────────────────── */
// EMA series — assigned by oipInitCharts in oi_profile.js, read here
let oipEma9Series = null, oipEma20Series = null, oipEma50Series = null,
    oipEma100Series = null, oipEma200Series = null;
let oipCEEma9Series = null, oipCEEma20Series = null, oipCEEma50Series = null;
let oipPEEma9Series = null, oipPEEma20Series = null, oipPEEma50Series = null;

// CPR, RSI, signal series + markers
let oipCprSeriesObj = null;
let oipCprSeriesMap = {};
let oipMultiCprSeriesMap = {};
let oipRSISeriesObj = null;
let oipSignalMarkers = [];
let oipRSIMarkers = [];

/* ── Indicator state persistence ─────────────────────────── */
const _OIP_IND_IDS = [
    'oipShowOIBars', 'oipShowVwapOI', 'oipShowVwapInt', 'oipShowCVWAP', 'oipShowPVWAP',
    'oipShowCpr', 'oipCprShowPrevHL', 'oipCprShowBand', 'oipCprShowResistance', 'oipCprShowSupport', 'oipCprShowCumR3S3',
    'oipShowSignals', 'oipShowRSI', 'oipShowAtmCeOi',
    'oipShowEma9', 'oipShowEma20', 'oipShowEma50', 'oipShowEma100', 'oipShowEma200',
    'oipShowMaxPain', 'oipShow2ndCandle30s', 'oipShow2nd5mCandle', 'oipShowMondayBox', 'oipShowPremium',
    'oipShow30mReversalLines', 'oipReversal30mCountUp', 'oipReversal30mCountDn', 'oipReversal30mRange',
    'oipShow1DReversalLines',  'oipReversal1DCount',  'oipReversal1DRange',
    'oipShowMultiCpr', 'oipMultiCpr15m', 'oipMultiCpr30m', 'oipMultiCpr1h'
];

function _oipSaveIndicators(key) {
    const state = {};
    _OIP_IND_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        state[id] = el.type === 'checkbox' ? el.checked : el.value;
    });
    try { localStorage.setItem(key, JSON.stringify(state)); } catch(e) {}
}

function _oipRestoreIndicators(key) {
    let state;
    try { state = JSON.parse(localStorage.getItem(key) || 'null'); } catch(e) {}
    if (!state) return;
    _OIP_IND_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (!el || !(id in state)) return;
        if (el.type === 'checkbox') el.checked = state[id];
        else el.value = state[id];
    });
}

/* ── EMA visibility ───────────────────────────────────────── */
function oipUpdateEmaVisibility() {
    const s9   = oipElems.showEma9?.checked   ?? false;
    const s20  = oipElems.showEma20?.checked  ?? false;
    const s50  = oipElems.showEma50?.checked  ?? false;
    const s100 = oipElems.showEma100?.checked ?? false;
    const s200 = oipElems.showEma200?.checked ?? false;

    if (oipEma9Series)   oipEma9Series.applyOptions({ visible: s9 });
    if (oipEma20Series)  oipEma20Series.applyOptions({ visible: s20 });
    if (oipEma50Series)  oipEma50Series.applyOptions({ visible: s50 });
    if (oipEma100Series) oipEma100Series.applyOptions({ visible: s100 });
    if (oipEma200Series) oipEma200Series.applyOptions({ visible: s200 });

    // Defer CE/PE series past their charts' init RAF — applyOptions triggers
    // LC's async render RAF which crashes if the chart isn't yet initialized.
    requestAnimationFrame(() => {
        try { if (oipCEEma9Series)  oipCEEma9Series.applyOptions({ visible: s9 }); } catch(e) {}
        try { if (oipCEEma20Series) oipCEEma20Series.applyOptions({ visible: s20 }); } catch(e) {}
        try { if (oipCEEma50Series) oipCEEma50Series.applyOptions({ visible: s50 }); } catch(e) {}
        try { if (oipPEEma9Series)  oipPEEma9Series.applyOptions({ visible: s9 }); } catch(e) {}
        try { if (oipPEEma20Series) oipPEEma20Series.applyOptions({ visible: s20 }); } catch(e) {}
        try { if (oipPEEma50Series) oipPEEma50Series.applyOptions({ visible: s50 }); } catch(e) {}
    });
}

/* ── Calculation functions ────────────────────────────────── */
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

// CVWAP — alias for the current-session VWAP (resets each trading day).
// Kept as a thin wrapper so the indicator wiring reads CVWAP/PVWAP symmetrically.
function oipCalculateCVWAP(candles) {
    return oipCalculateVWAP(candles);
}

// PVWAP — Previous-session VWAP. For every candle of a given day the value is the
// FINAL (closing) VWAP of the *previous* trading day, drawn as a flat line across
// the current session. Mirrors the "Previous VWAP" plot in the
// "Current & Previous VWAP Strategy" Pine script.
function oipCalculatePVWAP(candles) {
    if (!candles || candles.length === 0) return [];
    const dateOf = (t) => {
        const d = new Date(t * 1000);
        // UTC methods match the 'Fake IST Epoch' the server emits.
        return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    };

    // Pass 1 — final VWAP per day, preserving day order.
    const finalVwap = {};
    const dayOrder = [];
    let cumPV = 0, cumV = 0, lastDate = null, lastVwap = null;
    candles.forEach(c => {
        const date = dateOf(c.time);
        if (date !== lastDate) {
            if (lastDate !== null) finalVwap[lastDate] = lastVwap;
            cumPV = 0; cumV = 0; lastVwap = null; lastDate = date;
            dayOrder.push(date);
        }
        const vol = c.volume || 0;
        if (vol <= 0) return;
        cumPV += ((c.high + c.low + c.close) / 3) * vol;
        cumV += vol;
        const v = cumPV / cumV;
        if (!isNaN(v)) lastVwap = v;
    });
    if (lastDate !== null) finalVwap[lastDate] = lastVwap;

    // Map each day to the previous day's final VWAP.
    const prevDayVwap = {};
    for (let i = 1; i < dayOrder.length; i++) {
        prevDayVwap[dayOrder[i]] = finalVwap[dayOrder[i - 1]];
    }

    // Pass 2 — emit a flat previous-day VWAP line for each candle.
    const result = [];
    candles.forEach(c => {
        const pv = prevDayVwap[dateOf(c.time)];
        if (pv != null && !isNaN(pv)) result.push({ time: c.time, value: pv });
    });
    return result;
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
    else len = 88;
    if (len === null) return [];
    const k = 2 / (len + 1);
    let ema = null;
    return candles.map(c => {
        if (ema === null) ema = c.close;
        else ema = (c.close - ema) * k + ema;
        return { time: c.time, value: ema };
    });
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

/* ── Draw / apply functions ───────────────────────────────── */
function oipUpdateAllMarkers() {
    if (!oipOISeries) return;
    const combined = [...oipSignalMarkers, ...oipRSIMarkers].sort((a, b) => a.time - b.time);
    lwSetMarkers(oipOISeries, combined);
}

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
        pp:    { color: '#00008B', lineWidth: 1 },
        bc:    { color: '#00008B', lineWidth: 1 },
        tc:    { color: '#00008B', lineWidth: 1 },
        r1:    { color: '#006400', lineWidth: 1 },
        r2:    { color: '#006400', lineWidth: 1 },
        r3:    { color: '#006400', lineWidth: 1 },
        r4:    { color: '#006400', lineWidth: 1 },
        s1:    { color: '#ff0000', lineWidth: 1 },
        s2:    { color: '#ff0000', lineWidth: 1 },
        s3:    { color: '#ff0000', lineWidth: 1 },
        s4:    { color: '#ff0000', lineWidth: 1 },
        cr3:   { color: '#a020f0', lineWidth: 2 },
        cs3:   { color: '#a020f0', lineWidth: 2 }
    };

    const boxColors = {
        'cpr':   'rgba(51, 102, 255, 0.2)',   // #3366ff @ 20%
        'r1_r2': 'rgba(0, 204, 102, 0.02)',
        'r2_r3': 'rgba(0, 204, 102, 0.02)',
        'r3_r4': 'rgba(0, 204, 102, 0.02)',
        's1_s2': 'rgba(255, 0, 0, 0.02)',
        's2_s3': 'rgba(255, 0, 0, 0.02)',
        's3_s4': 'rgba(255, 0, 0, 0.02)'
    };

    const keyGroup = {
        prevH: 'oipCprShowPrevHL', prevL: 'oipCprShowPrevHL',
        pp: 'oipCprShowBand',      bc: 'oipCprShowBand',    tc: 'oipCprShowBand',
        r1: 'oipCprShowResistance', r2: 'oipCprShowResistance', r3: 'oipCprShowResistance', r4: 'oipCprShowResistance',
        s1: 'oipCprShowSupport',   s2: 'oipCprShowSupport', s3: 'oipCprShowSupport', s4: 'oipCprShowSupport',
        cr3: 'oipCprShowCumR3S3',  cs3: 'oipCprShowCumR3S3'
    };
    const boxGroup = {
        'cpr':   'oipCprShowBand',
        'r1_r2': 'oipCprShowResistance', 'r2_r3': 'oipCprShowResistance', 'r3_r4': 'oipCprShowResistance',
        's1_s2': 'oipCprShowSupport',    's2_s3': 'oipCprShowSupport',    's3_s4': 'oipCprShowSupport'
    };
    const subChecked = id => document.getElementById(id)?.checked !== false;

    daysData.forEach((day, dayIdx) => {
        // Draw the box fills FIRST so the pivot lines (PP/BC/TC, R/S) render on
        // top of them — otherwise an opaque band fill hides the lines.
        day.boxes.forEach((box, boxIdx) => {
            const seriesKey = `box_${box.type}_${dayIdx}_${boxIdx}`;
            let series = oipCprSeriesMap[seriesKey];
            if (!series) {
                const col = boxColors[box.type];
                series = oipOIChart.addSeries(LightweightCharts.BaselineSeries, {
                    baseValue: { type: 'price', price: box.min },
                    topFillColor1: col, topFillColor2: col, topLineColor: 'transparent',
                    bottomFillColor1: col, bottomFillColor2: col, bottomLineColor: 'transparent',
                    lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null
                });
                oipCprSeriesMap[seriesKey] = series;
            }
            if (!subChecked(boxGroup[box.type])) { series.setData([]); return; }
            series.applyOptions({ baseValue: { type: 'price', price: box.min } });
            series.setData(day.times.map(t => ({ time: t, value: box.max })));
        });

        Object.keys(day.levels).forEach(key => {
            const seriesKey = `line_${key}_${dayIdx}`;
            let series = oipCprSeriesMap[seriesKey];
            if (!series) {
                series = oipOIChart.addSeries(LightweightCharts.LineSeries, {
                    ...lineStyles[key],
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null
                });
                oipCprSeriesMap[seriesKey] = series;
            }
            const val = day.levels[key];
            const visible = subChecked(keyGroup[key]);
            series.setData(visible && val != null && !isNaN(val) ? day.times.map(t => ({ time: t, value: val })) : []);
        });
    });
    oipApplyZOrder();
}

function oipDrawSignals(candles) {
    if (!oipOIChart || !oipOISeries) return;
    const show = oipElems.showSignals?.checked;
    if (!show || !candles || candles.length < 20) {
        oipSignalMarkers = [];
        oipUpdateAllMarkers();
        return;
    }
    const ema9  = oipCalculateFixedEMA(candles, 9);
    const ema20 = oipCalculateFixedEMA(candles, 20);
    const ema50 = oipCalculateFixedEMA(candles, 50);
    const e9Map  = new Map(ema9.map(d => [d.time, d.value]));
    const e20Map = new Map(ema20.map(d => [d.time, d.value]));
    const e50Map = new Map(ema50.map(d => [d.time, d.value]));

    const signals = [];
    let buyState = 'IDLE', sellState = 'IDLE';
    let inBuyTrade = false, inSellTrade = false;

    for (let i = 1; i < candles.length - 1; i++) {
        const c = candles[i], t = c.time;
        const e9 = e9Map.get(t), e20 = e20Map.get(t), e50 = e50Map.get(t);
        if (!e9 || !e20 || !e50) continue;

        if (inBuyTrade && c.close < e50) {
            signals.push({ time: t, position: 'aboveBar', color: '#800000', shape: 'circle', text: 'B SL' });
            inBuyTrade = false;
        }
        if (inSellTrade && c.close > e50) {
            signals.push({ time: t, position: 'belowBar', color: '#800000', shape: 'circle', text: 'S SL' });
            inSellTrade = false;
        }

        if (c.close < e9 && c.close < e20) {
            buyState = 'BELOW';
        } else if (buyState === 'BELOW' && c.close > e9 && c.close > e20) {
            buyState = 'CROSSED';
        } else if (buyState === 'CROSSED') {
            const completelyAbove = c.open > e9 && c.open > e20 && c.close > e9 && c.close > e20 && c.high > e9 && c.high > e20 && c.low > e9 && c.low > e20;
            if (completelyAbove) buyState = 'CONFIRMED';
            else if (c.close < e9 && c.close < e20) buyState = 'BELOW';
        } else if (buyState === 'CONFIRMED' && !inBuyTrade) {
            const touched = (c.low <= e9 || c.low <= e20);
            const closedAboveBoth = (c.close > e9 && c.close > e20);
            if (touched && closedAboveBoth && c.close > c.open && c.close > e50) {
                signals.push({ time: t, position: 'belowBar', color: '#22c55e', shape: 'arrowUp', text: 'BUY' });
                buyState = 'IDLE'; inBuyTrade = true; inSellTrade = false;
            } else if (c.close < e9 && c.close < e20) {
                buyState = 'BELOW';
            }
        }

        if (c.close > e9 && c.close > e20) {
            sellState = 'ABOVE';
        } else if (sellState === 'ABOVE' && c.close < e9 && c.close < e20) {
            sellState = 'CROSSED';
        } else if (sellState === 'CROSSED') {
            const completelyBelow = c.open < e9 && c.open < e20 && c.close < e9 && c.close < e20 && c.high < e9 && c.high < e20 && c.low < e9 && c.low < e20;
            if (completelyBelow) sellState = 'CONFIRMED';
            else if (c.close > e9 && c.close > e20) sellState = 'ABOVE';
        } else if (sellState === 'CONFIRMED' && !inSellTrade) {
            const touched = (c.high >= e9 || c.high >= e20);
            const closedBelowBoth = (c.close < e9 && c.close < e20);
            if (touched && closedBelowBoth && c.close < c.open && c.close < e50) {
                signals.push({ time: t, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'SELL' });
                sellState = 'IDLE'; inSellTrade = true; inBuyTrade = false;
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
        const baseObj = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
        oipRSISeriesObj = {
            ob:   oipOIChart.addSeries(LightweightCharts.LineSeries, { ...baseObj, color: '#14b8a6', lineWidth: 2, lineStyle: 0 }),
            bull: oipOIChart.addSeries(LightweightCharts.LineSeries, { ...baseObj, color: '#14b8a6', lineWidth: 1, lineStyle: 0 }),
            os:   oipOIChart.addSeries(LightweightCharts.LineSeries, { ...baseObj, color: '#ef4444', lineWidth: 2, lineStyle: 0 }),
            bear: oipOIChart.addSeries(LightweightCharts.LineSeries, { ...baseObj, color: '#ef4444', lineWidth: 1, lineStyle: 0 })
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

/* ── Multi CPR ────────────────────────────────────────────── */
function _oipAggregateToNMin(candles, minutes) {
    const MARKET_OPEN = 9 * 60 + 15;
    const buckets = new Map();
    candles.forEach(c => {
        const d     = new Date(c.time * 1000);
        const total = d.getUTCHours() * 60 + d.getUTCMinutes();
        const idx   = Math.floor((total - MARKET_OPEN) / minutes);
        const bMin  = MARKET_OPEN + idx * minutes;
        const bd    = new Date(d);
        bd.setUTCHours(Math.floor(bMin / 60), bMin % 60, 0, 0);
        const key   = Math.floor(bd.getTime() / 1000);
        if (!buckets.has(key)) {
            buckets.set(key, { time: key, open: c.open, high: c.high, low: c.low, close: c.close, times: [c.time] });
        } else {
            const b = buckets.get(key);
            b.high  = Math.max(b.high, c.high);
            b.low   = Math.min(b.low,  c.low);
            b.close = c.close;
            b.times.push(c.time);
        }
    });
    return [...buckets.values()].sort((a, b) => a.time - b.time);
}

function oipDrawMultiCPR(candles) {
    if (!oipOIChart || !oipOISeries) return;

    Object.values(oipMultiCprSeriesMap).forEach(s => { try { s.setData([]); } catch(e) {} });

    const show = document.getElementById('oipShowMultiCpr')?.checked;
    if (!show || !candles?.length) return;

    const shared = {
        lastValueVisible: false, priceLineVisible: false,
        crosshairMarkerVisible: false, autoscaleInfoProvider: () => null
    };

    const configs = [
        { id: 'oipMultiCpr15m', minutes: 15, color: '#f97316', fill: 'rgba(249,115,22,0.07)'  },
        { id: 'oipMultiCpr30m', minutes: 30, color: '#06b6d4', fill: 'rgba(6,182,212,0.07)'   },
        { id: 'oipMultiCpr1h',  minutes: 60, color: '#9c28b0', fill: 'rgba(235, 212, 239, 0.5)'  }  // #ebd4ef @ 50%
    ];

    // Build per-config CONTINUOUS line data (one stepped line spanning all
    // buckets) plus per-bucket fill segments (BaselineSeries needs a per-bucket
    // baseValue, so the band fill stays segmented while the lines run continuous).
    const fillSegs  = [];
    const lineSpecs = [];
    configs.forEach(({ id, minutes, color, fill }) => {
        const enabled = document.getElementById(id)?.checked !== false;
        const bars    = _oipAggregateToNMin(candles, minutes);
        const ppData = [], bcData = [], tcData = [];
        for (let i = 1; i < bars.length; i++) {
            const prev = bars[i - 1], curr = bars[i];
            if (!curr.times.length) continue;
            const oH = prev.high, oL = prev.low, oC = prev.close;
            const pp = (oH + oL + oC) / 3;
            const bc = (oH + oL) / 2;
            const tc = 2 * pp - bc;
            // Append this bucket's times to the continuous line (steps at the boundary).
            for (const t of curr.times) {
                ppData.push({ time: t, value: pp });
                bcData.push({ time: t, value: bc });
                tcData.push({ time: t, value: tc });
            }
            fillSegs.push({ fill, enabled, times: curr.times, tc, bc, fillKey: `mc_fill_${minutes}_${i}` });
        }
        lineSpecs.push({
            color, enabled, ppData, bcData, tcData,
            tcKey: `mc_tc_${minutes}`, ppKey: `mc_pp_${minutes}`, bcKey: `mc_bc_${minutes}`
        });
    });

    // Pass 1 — create + set ALL fills first (lowest in the z-stack).
    fillSegs.forEach(s => {
        if (!oipMultiCprSeriesMap[s.fillKey]) {
            oipMultiCprSeriesMap[s.fillKey] = oipOIChart.addSeries(LightweightCharts.BaselineSeries, {
                baseValue: { type: 'price', price: Math.min(s.tc, s.bc) },
                topFillColor1: s.fill, topFillColor2: s.fill, topLineColor: 'transparent',
                bottomFillColor1: s.fill, bottomFillColor2: s.fill, bottomLineColor: 'transparent',
                lineWidth: 0, ...shared
            });
        }
        if (s.enabled) {
            oipMultiCprSeriesMap[s.fillKey].applyOptions({ baseValue: { type: 'price', price: Math.min(s.tc, s.bc) } });
            oipMultiCprSeriesMap[s.fillKey].setData(s.times.map(t => ({ time: t, value: Math.max(s.tc, s.bc) })));
        } else {
            oipMultiCprSeriesMap[s.fillKey].setData([]);
        }
    });

    // Pass 2 — one continuous PP/BC/TC line per config, drawn above every fill.
    lineSpecs.forEach(s => {
        if (!oipMultiCprSeriesMap[s.tcKey]) {
            oipMultiCprSeriesMap[s.tcKey] = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: s.color, lineWidth: 1, lineStyle: 0, ...shared });
        }
        if (!oipMultiCprSeriesMap[s.ppKey]) {
            oipMultiCprSeriesMap[s.ppKey] = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: s.color, lineWidth: 1, lineStyle: 0, ...shared });
        }
        if (!oipMultiCprSeriesMap[s.bcKey]) {
            oipMultiCprSeriesMap[s.bcKey] = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: s.color, lineWidth: 1, lineStyle: 0, ...shared });
        }
        oipMultiCprSeriesMap[s.tcKey].setData(s.enabled ? s.tcData : []);
        oipMultiCprSeriesMap[s.ppKey].setData(s.enabled ? s.ppData : []);
        oipMultiCprSeriesMap[s.bcKey].setData(s.enabled ? s.bcData : []);
    });
    oipApplyZOrder();
}

/* ── Global z-order policy (main OI pane) ─────────────────────
 * Enforces a single, deterministic stack so no indicator is hidden behind
 * another regardless of which order indicators were toggled on:
 *     band/box FILLS  (bottom)  →  all LINES  →  candles  (top)
 * Uses v5 ISeriesApi.setSeriesOrder with contiguous indices. Safe no-op on v4.
 */
function oipApplyZOrder() {
    if (!oipOISeries || typeof oipOISeries.setSeriesOrder !== 'function') return;

    const fills = [];
    const lines = [];

    // CPR levels: box_* are fills, line_* are lines.
    Object.keys(oipCprSeriesMap).forEach(k => {
        const s = oipCprSeriesMap[k];
        if (s) (k.startsWith('box_') ? fills : lines).push(s);
    });
    // Multi-CPR: mc_fill_* are fills, mc_tc/pp/bc_* are lines.
    Object.keys(oipMultiCprSeriesMap).forEach(k => {
        const s = oipMultiCprSeriesMap[k];
        if (s) (k.startsWith('mc_fill_') ? fills : lines).push(s);
    });
    // EMAs + VWAP variants + max-pain (all lines on the main pane).
    [oipEma9Series, oipEma20Series, oipEma50Series, oipEma100Series, oipEma200Series,
     typeof oipVwapSeries    !== 'undefined' ? oipVwapSeries    : null,
     typeof oipCvwapSeries   !== 'undefined' ? oipCvwapSeries   : null,
     typeof oipPvwapSeries   !== 'undefined' ? oipPvwapSeries   : null,
     typeof oipMaxPainSeries !== 'undefined' ? oipMaxPainSeries : null
    ].forEach(s => { if (s) lines.push(s); });

    // RSI S&R lines (drawn on the price pane).
    if (oipRSISeriesObj) Object.values(oipRSISeriesObj).forEach(s => { if (s) lines.push(s); });

    // Candle boxes (main pane only): fill → fills, top/bottom borders → lines.
    const pushBoxes = arr => (arr || []).forEach(b => {
        if (!b || b.chart !== oipOIChart) return;
        if (b.fill) fills.push(b.fill);
        if (b.top) lines.push(b.top);
        if (b.bottom) lines.push(b.bottom);
    });
    if (typeof oip2ndCandle30sBox !== 'undefined') pushBoxes(oip2ndCandle30sBox.oi);
    if (typeof oip2nd5mCandleBox  !== 'undefined') pushBoxes(oip2nd5mCandleBox.oi);
    if (typeof oipMondayBoxes     !== 'undefined') pushBoxes(oipMondayBoxes);

    // Reversal lines (kept above fills).
    (oip30mReversalSeries || []).forEach(s => { if (s) lines.push(s); });
    if (typeof oip1DReversalSeries !== 'undefined')
        (oip1DReversalSeries || []).forEach(s => { if (s) lines.push(s); });

    let order = 0;
    fills.forEach(s => { try { s.setSeriesOrder(order++); } catch (e) {} });
    lines.forEach(s => { try { s.setSeriesOrder(order++); } catch (e) {} });
    try { oipOISeries.setSeriesOrder(order++); } catch (e) {}  // candles on top

    oipApplyOptionZOrder();
}

// Layer a single option-premium pane: box FILLS (bottom) → EMA/VWAP/box-border
// LINES → candle(s) on top. Used for the CE, PE and combined premium charts.
function _oipLayerPane(candleSeries, lineSeries, boxArrays) {
    const fills = [];
    const lines = [];
    lineSeries.forEach(s => { if (s) lines.push(s); });
    boxArrays.forEach(arr => (arr || []).forEach(b => {
        if (!b) return;
        if (b.fill) fills.push(b.fill);
        if (b.top) lines.push(b.top);
        if (b.bottom) lines.push(b.bottom);
    }));
    let order = 0;
    const set = s => { if (s && typeof s.setSeriesOrder === 'function') { try { s.setSeriesOrder(order++); } catch (e) {} } };
    fills.forEach(set);
    lines.forEach(set);
    candleSeries.forEach(set); // candles on top of their own pane
}

// Keep candles on top across the CE / PE / combined premium panes.
function oipApplyOptionZOrder() {
    const box30 = typeof oip2ndCandle30sBox !== 'undefined' ? oip2ndCandle30sBox : { ce: [], pe: [] };
    const box5m = typeof oip2nd5mCandleBox  !== 'undefined' ? oip2nd5mCandleBox  : { ce: [], pe: [] };

    _oipLayerPane(
        [oipCESeries],
        [oipCEEma9Series, oipCEEma20Series, oipCEEma50Series, oipCECvwapSeries, oipCEPvwapSeries],
        [box30.ce, box5m.ce]
    );
    _oipLayerPane(
        [oipPESeries],
        [oipPEEma9Series, oipPEEma20Series, oipPEEma50Series, oipPECvwapSeries, oipPEPvwapSeries],
        [box30.pe, box5m.pe]
    );
    _oipLayerPane(
        [oipIntrinsicSeries, oipIntrinsicPeSeries],
        [oipVwapIntSeries, oipVwapIntPeSeries, oipCvwapIntSeries, oipCvwapIntPeSeries, oipPvwapIntSeries, oipPvwapIntPeSeries],
        []
    );
}

/* ── Indicators popup ─────────────────────────────────────── */
function oipInitIndicatorsPopup(storageKey) {
    // Wire showEma200 (was declared in oipElems but never initialized)
    oipElems.showEma200 = document.getElementById('oipShowEma200');

    // Restore persisted state before anything is drawn
    if (storageKey) _oipRestoreIndicators(storageKey);

    const btn   = document.getElementById('oipIndicatorsBtn');
    const popup = document.getElementById('oipIndicatorsPopup');

    if (btn && popup) {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            popup.classList.toggle('hidden');
        });
        document.addEventListener('click', e => {
            if (!popup.contains(e.target) && e.target !== btn) {
                popup.classList.add('hidden');
            }
        });
        // Save whenever any checkbox inside the popup changes
        if (storageKey) popup.addEventListener('change', () => _oipSaveIndicators(storageKey));
    } else if (storageKey) {
        // No popup (compact toolbar page): delegate on document for the known IDs
        const indSet = new Set(_OIP_IND_IDS);
        document.addEventListener('change', e => {
            if (e.target.type === 'checkbox' && indSet.has(e.target.id)) {
                _oipSaveIndicators(storageKey);
            }
        }, true);
    }

    // CPR expand / collapse
    const cprExpandBtn = document.getElementById('oipCprExpandBtn');
    const cprSub       = document.getElementById('oipCprSub');
    const cprMaster    = document.getElementById('oipShowCpr');

    if (cprExpandBtn && cprSub) {
        cprExpandBtn.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            const isNowHidden = cprSub.classList.toggle('hidden');
            cprExpandBtn.classList.toggle('expanded', !isNowHidden);
        });
    }

    function _syncCprSubState() {
        if (!cprSub || !cprMaster) return;
        cprSub.classList.toggle('oip-cpr-disabled', !cprMaster.checked);
    }
    if (cprMaster) {
        cprMaster.addEventListener('change', _syncCprSubState);
        _syncCprSubState();
    }

    // Multi CPR expand / collapse
    const multiCprExpandBtn = document.getElementById('oipMultiCprExpandBtn');
    const multiCprSub       = document.getElementById('oipMultiCprSub');
    const multiCprMaster    = document.getElementById('oipShowMultiCpr');

    if (multiCprExpandBtn && multiCprSub) {
        multiCprExpandBtn.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            const isNowHidden = multiCprSub.classList.toggle('hidden');
            multiCprExpandBtn.classList.toggle('expanded', !isNowHidden);
        });
    }

    function _syncMultiCprSubState() {
        if (!multiCprSub || !multiCprMaster) return;
        multiCprSub.classList.toggle('oip-cpr-disabled', !multiCprMaster.checked);
    }
    if (multiCprMaster) {
        multiCprMaster.addEventListener('change', _syncMultiCprSubState);
        _syncMultiCprSubState();
    }

    // Moving Averages (EMA) expand / collapse
    const emaHeader    = document.getElementById('oipEmaHeader');
    const emaExpandBtn = document.getElementById('oipEmaExpandBtn');
    const emaSub       = document.getElementById('oipEmaSub');
    if (emaHeader && emaSub) {
        emaHeader.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            const isNowHidden = emaSub.classList.toggle('hidden');
            if (emaExpandBtn) emaExpandBtn.classList.toggle('expanded', !isNowHidden);
        });
    }
}

/* ── 9:18 ATM CE OI Lines ─────────────────────────────────── */
// Selected at 09:18: the ATM strike plus the adjacent strike chosen by CE OI.
// Drawn as horizontal price lines on the main (underlying) candle series.
let oipAtmCeOiData  = null;   // cached result from /api/oi-profile/atm-ce-oi-strikes
let oipAtmCeOiLines = [];     // price-line handles on oipOISeries

function oipClearAtmCeOiLines() {
    oipAtmCeOiLines.forEach(l => { try { oipOISeries?.removePriceLine(l); } catch (e) {} });
    oipAtmCeOiLines = [];
}

// Fetch + cache the 09:18 ATM CE OI strike selection. Always runs so the data
// is "kept ready" regardless of the checkbox; drawing is gated separately.
// NIFTY only. `dateStr` (YYYY-MM-DD) is passed in replay mode; omit for today.
async function oipFetchAtmCeOiStrikes(symbol, step, dateStr) {
    oipAtmCeOiData = null;
    if ((symbol || '').toUpperCase() !== 'NIFTY') return;
    try {
        let url = `/api/oi-profile/atm-ce-oi-strikes?symbol=${encodeURIComponent(symbol)}&step=${step || 50}`;
        if (dateStr) url += `&date=${dateStr}`;
        const res  = await fetch(url);
        const data = await res.json();
        if (data && data.success) oipAtmCeOiData = data;
        else console.warn('[OIP] ATM CE OI:', data && data.error);
    } catch (e) {
        console.warn('[OIP] ATM CE OI fetch failed:', e);
    }
}

// Draw the two cached strike levels as horizontal price lines. Cheap to call
// repeatedly (clears + recreates 2 lines); gated by the checkbox.
function oipDrawAtmCeOiLines() {
    oipClearAtmCeOiLines();
    if (!oipOISeries) return;
    if (!document.getElementById('oipShowAtmCeOi')?.checked) return;

    const d = oipAtmCeOiData;
    if (!d || !Array.isArray(d.selected)) return;

    d.selected.forEach((s) => {
        if (s.strike == null) return;
        oipAtmCeOiLines.push(oipOISeries.createPriceLine({
            price: s.strike,
            color: '#e11d48',          // same as the indicator label color
            lineWidth: 2,
            lineStyle: 0,              // both lines solid
            axisLabelVisible: false,
            title: ''
        }));
    });
}

/* ── 30-min Reversal Lines ────────────────────────────────── */
let oip30mReversalSeries = [];

function oipClear30mReversalLines() {
    oip30mReversalSeries.forEach(s => { try { oipOIChart.removeSeries(s); } catch(e) {} });
    oip30mReversalSeries = [];
}

// Aggregate arbitrary-interval candles into 30-minute OHLC buckets aligned to
// Indian market open (9:15 AM IST).  Buckets: 9:15, 9:45, 10:15, 10:45 …
// Candles use Fake-IST encoding: UTC hours/minutes == IST hours/minutes.
function _oipAggregateTo30m(candles) {
    const MARKET_OPEN = 9 * 60 + 15; // 555 minutes from midnight IST
    const buckets = new Map();

    candles.forEach(c => {
        const d      = new Date(c.time * 1000);
        const total  = d.getUTCHours() * 60 + d.getUTCMinutes();
        const offset = total - MARKET_OPEN;               // mins since 9:15
        const idx    = Math.floor(offset / 30);           // which 30-min slot
        const bMin   = MARKET_OPEN + idx * 30;            // slot start in mins

        const bd = new Date(d);
        bd.setUTCHours(Math.floor(bMin / 60), bMin % 60, 0, 0);
        const key = Math.floor(bd.getTime() / 1000);

        if (!buckets.has(key)) {
            buckets.set(key, { time: key, open: c.open, high: c.high, low: c.low, close: c.close });
        } else {
            const b = buckets.get(key);
            b.high  = Math.max(b.high, c.high);
            b.low   = Math.min(b.low,  c.low);
            b.close = c.close;
        }
    });
    return [...buckets.values()].sort((a, b) => a.time - b.time);
}

// Generate future timestamps that stay within 9:15 AM – 3:30 PM IST trading sessions.
// Uses Fake-IST encoding: UTC hours/minutes == IST hours/minutes.
function _oipFutureSessionTimes(fromTime, intervalSecs, count) {
    const result = [];
    let t = fromTime;
    const SESSION_START = 9 * 60 + 15;   // 555 min
    const SESSION_END   = 15 * 60 + 30;  // 930 min

    function jumpToNextSession(ts) {
        const d = new Date(ts * 1000);
        d.setUTCDate(d.getUTCDate() + 1);
        while (d.getUTCDay() === 0 || d.getUTCDay() === 6) d.setUTCDate(d.getUTCDate() + 1);
        d.setUTCHours(9, 15, 0, 0);
        return Math.floor(d.getTime() / 1000);
    }

    while (result.length < count) {
        t += intervalSecs;
        const d   = new Date(t * 1000);
        const dow = d.getUTCDay();
        const min = d.getUTCHours() * 60 + d.getUTCMinutes();
        if (dow === 0 || dow === 6 || min < SESSION_START || min > SESSION_END) {
            t = jumpToNextSession(t);
        }
        result.push(t);
    }
    return result;
}

const _OIP_30M_ALLOWED = new Set([
    '30second', 'minute', '2minute', '3minute', '5minute',
    '10minute', '15minute', '30minute'
]);

// Cached deduped reversal levels from the last full signal detection run.
// Only updated when recompute=true (i.e., on 30m candle close during replay, or always in live mode).
let _oip30mLevelCache = null;

// recompute=true (default): detect new signals and update cache — use on 30m close or live mode.
// recompute=false: redraw existing cached levels with updated time endpoints — use mid-bar in replay.
function oipDraw30mReversalLines(candles, recompute = true) {
    oipClear30mReversalLines();
    if (!document.getElementById('oipShow30mReversalLines')?.checked) return;
    // Only valid for 30-min and below; silent no-op for 60m / daily / weekly
    if (typeof oipInterval !== 'undefined' && !_OIP_30M_ALLOWED.has(oipInterval)) return;
    if (!oipOIChart || !oipOISeries || !candles?.length) return;

    const lastTime = candles[candles.length - 1].time;
    const curPrice = candles[candles.length - 1].close;

    // ── Robust interval: mode of last 30 consecutive intra-session gaps ──
    const sample = candles.slice(-31);
    const freq = {};
    for (let i = 1; i < sample.length; i++) {
        const d = sample[i].time - sample[i - 1].time;
        if (d > 0 && d <= 3600) freq[d] = (freq[d] || 0) + 1; // ignore overnight gaps
    }
    const candleInterval = Object.keys(freq).length
        ? parseInt(Object.keys(freq).reduce((a, b) => freq[a] > freq[b] ? a : b))
        : 60;

    // Read user-configured values
    const lineCountUp = Math.max(1, parseInt(document.getElementById('oipReversal30mCountUp')?.value) || 10);
    const lineCountDn = Math.max(1, parseInt(document.getElementById('oipReversal30mCountDn')?.value) || 10);
    const lookback    = Math.max(5, parseInt(document.getElementById('oipReversal30mRange')?.value)  || 50);

    // In replay mode skip future timestamps — they expand the time scale and crash the renderer.
    const FUTURE_BARS = 50;
    const isReplay = typeof oipFullCandles !== 'undefined' && oipFullCandles && candles.length < oipFullCandles.length;
    const futureTimes = isReplay ? [] : _oipFutureSessionTimes(lastTime, candleInterval, FUTURE_BARS);
    window._oipReversalFutureBarsCount = futureTimes.length;

    // ── Signal detection: only runs on 30m close (recompute=true) or first call ──
    if (recompute || _oip30mLevelCache === null) {
        const bars = _oipAggregateTo30m(candles);
        if (bars.length < 2) { _oip30mLevelCache = []; }
        else {
            const GAP_PTS = 10;
            const barsInRange = bars.slice(-lookback);
            const rawLevels = [];
            for (let i = 0; i < barsInRange.length - 1; i++) {
                const c1 = barsInRange[i];
                const c2 = barsInRange[i + 1];
                const c1Bull = c1.close > c1.open;
                const c2Bull = c2.close > c2.open;
                if (c1Bull === c2Bull) continue;
                if (Math.abs(c1.close - c2.open) > GAP_PTS) continue;
                const isBullish = !c1Bull && c2Bull;
                const next2 = barsInRange.slice(i + 2, i + 4);
                if (!next2.length) continue;
                let breakOccurred, closeConfirmed;
                if (isBullish) {
                    breakOccurred  = next2.some(b => b.high  > c2.high);
                    closeConfirmed = next2.some(b => b.close > c2.high);
                } else {
                    breakOccurred  = next2.some(b => b.low   < c2.low);
                    closeConfirmed = next2.some(b => b.close < c2.low);
                }
                if (!breakOccurred || !closeConfirmed) continue;
                rawLevels.push({ level: (c1.close + c2.open) / 2, time: c1.time });
            }
            rawLevels.sort((a, b) => a.level - b.level);
            const deduped = [];
            for (const l of rawLevels) {
                const prev = deduped[deduped.length - 1];
                if (prev && Math.abs(l.level - prev.level) <= 5) {
                    if (l.time > prev.time) deduped[deduped.length - 1] = l;
                } else {
                    deduped.push(l);
                }
            }
            _oip30mLevelCache = deduped;
        }
    }

    const deduped = _oip30mLevelCache;
    if (!deduped?.length) return;

    // ── Pick closest N above/below current price (recomputed every step so lines follow price) ──
    const above = deduped.filter(l => l.level >= curPrice)
                         .sort((a, b) => a.level - b.level)
                         .slice(0, lineCountUp);
    const below = deduped.filter(l => l.level < curPrice)
                         .sort((a, b) => b.level - a.level)
                         .slice(0, lineCountDn);

    // ── Draw lines extended to the current candle's time ────────
    [...above, ...below].forEach(({ level, time }) => {
        const historical = candles
            .filter(c => c.time >= time)
            .map(c => ({ time: c.time, value: level }));
        const future = futureTimes.map(t => ({ time: t, value: level }));
        const s = oipOIChart.addSeries(LightweightCharts.LineSeries, {
            color: '#f97316',
            lineWidth: 1,
            lineStyle: 0,
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
            autoscaleInfoProvider: () => null,
        });
        s.setData([...historical, ...future]);
        oip30mReversalSeries.push(s);
    });
    oipApplyZOrder();
}

/* ── 1-Day Reversal Lines ─────────────────────────────────── */

// Aggregate any-interval candles into 1-Day OHLC bars.
// Each day's canonical time = 9:15 AM IST (Fake-IST encoding).
function _oipAggregateTo1D(candles) {
    const buckets = new Map();
    candles.forEach(c => {
        const d   = new Date(c.time * 1000);
        const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
        if (!buckets.has(key)) {
            const bd = new Date(d);
            bd.setUTCHours(9, 15, 0, 0);
            buckets.set(key, { time: Math.floor(bd.getTime()/1000), open: c.open, high: c.high, low: c.low, close: c.close });
        } else {
            const b = buckets.get(key);
            b.high  = Math.max(b.high, c.high);
            b.low   = Math.min(b.low,  c.low);
            b.close = c.close;
        }
    });
    return [...buckets.values()].sort((a, b) => a.time - b.time);
}

// Generate N future trading-day timestamps (9:15 AM IST, skipping weekends).
function _oipFutureTradingDays(fromTime, count) {
    const result = [];
    const d = new Date(fromTime * 1000);
    while (result.length < count) {
        d.setUTCDate(d.getUTCDate() + 1);
        if (d.getUTCDay() === 0 || d.getUTCDay() === 6) continue;
        d.setUTCHours(9, 15, 0, 0);
        result.push(Math.floor(d.getTime() / 1000));
    }
    return result;
}

let oip1DReversalSeries = [];

function oipClear1DReversalLines() {
    oip1DReversalSeries.forEach(s => { try { oipOIChart.removeSeries(s); } catch(e) {} });
    oip1DReversalSeries = [];
}

function oipDraw1DReversalLines(candles) {
    oipClear1DReversalLines();
    if (!document.getElementById('oipShow1DReversalLines')?.checked) return;
    if (!oipOIChart || !oipOISeries || !candles?.length) return;

    const bars = _oipAggregateTo1D(candles);
    if (bars.length < 2) return;

    const count = Math.max(1, parseInt(document.getElementById('oipReversal1DCount')?.value) || 10);
    const range = Math.max(1, parseInt(document.getElementById('oipReversal1DRange')?.value) || 30);

    // Skip future timestamps in replay mode — same reason as oipDraw30mReversalLines.
    const FUTURE_DAYS = 30;
    const isReplay1D = typeof oipFullCandles !== 'undefined' && oipFullCandles && candles.length < oipFullCandles.length;
    const futureTimes = isReplay1D ? [] : _oipFutureTradingDays(bars[bars.length - 1].time, FUTURE_DAYS);
    window._oipReversalFutureBarsCount = futureTimes.length;

    const shared = {
        lastValueVisible: false, priceLineVisible: false,
        crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
    };

    // ── 1. Scan backwards (skip last incomplete bar) for candles
    //       where |close - open| <= range ──────────────────────────
    const qualifying = [];
    for (let i = bars.length - 2; i >= 0 && qualifying.length < count; i--) {
        const b = bars[i];
        if (Math.abs(b.close - b.open) <= range) qualifying.push(b);
    }

    if (!qualifying.length) return;

    // ── 2. For each qualifying candle draw a body-zone box + center line ──
    qualifying.forEach(b => {
        const top    = Math.max(b.open, b.close);
        const bottom = Math.min(b.open, b.close);
        const center = (b.open + b.close) / 2;
        const isBull = b.close >= b.open;
        const lineColor = isBull ? '#22c55e' : '#ef4444';
        const fillColor = isBull ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';

        // Timeline: from this candle forward through all remaining daily bars + future days
        const allTimes = [
            ...bars.filter(bar => bar.time >= b.time).map(bar => bar.time),
            ...futureTimes,
        ];

        // Top border
        const topS = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: lineColor, lineWidth: 1, lineStyle: 0, ...shared });
        topS.setData(allTimes.map(t => ({ time: t, value: top })));
        oip1DReversalSeries.push(topS);

        // Bottom border
        const botS = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: lineColor, lineWidth: 1, lineStyle: 0, ...shared });
        botS.setData(allTimes.map(t => ({ time: t, value: bottom })));
        oip1DReversalSeries.push(botS);

        // Fill between top and bottom
        const fillS = oipOIChart.addSeries(LightweightCharts.BaselineSeries, {
            baseValue: { type: 'price', price: bottom },
            topFillColor1: fillColor, topFillColor2: fillColor, topLineColor: 'transparent',
            bottomFillColor1: 'transparent', bottomFillColor2: 'transparent', bottomLineColor: 'transparent',
            lineWidth: 0, ...shared,
        });
        fillS.setData(allTimes.map(t => ({ time: t, value: top })));
        oip1DReversalSeries.push(fillS);

        // Center line (dashed)
        const cenS = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: lineColor, lineWidth: 1, lineStyle: 1, ...shared });
        cenS.setData(allTimes.map(t => ({ time: t, value: center })));
        oip1DReversalSeries.push(cenS);
    });
    oipApplyZOrder();
}
