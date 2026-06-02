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
let oipRSISeriesObj = null;
let oipSignalMarkers = [];
let oipRSIMarkers = [];

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

    if (oipCEEma9Series)  oipCEEma9Series.applyOptions({ visible: s9 });
    if (oipCEEma20Series) oipCEEma20Series.applyOptions({ visible: s20 });
    if (oipCEEma50Series) oipCEEma50Series.applyOptions({ visible: s50 });

    if (oipPEEma9Series)  oipPEEma9Series.applyOptions({ visible: s9 });
    if (oipPEEma20Series) oipPEEma20Series.applyOptions({ visible: s20 });
    if (oipPEEma50Series) oipPEEma50Series.applyOptions({ visible: s50 });
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
    oipOISeries.setMarkers(combined);
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
        pp:    { color: '#3366ff', lineWidth: 1 },
        bc:    { color: '#3366ff', lineWidth: 1 },
        tc:    { color: '#3366ff', lineWidth: 1 },
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
        'cpr':   'rgba(51, 102, 255, 0.05)',
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
            ob:   oipOIChart.addLineSeries({ ...baseObj, color: '#14b8a6', lineWidth: 2, lineStyle: 0 }),
            bull: oipOIChart.addLineSeries({ ...baseObj, color: '#14b8a6', lineWidth: 1, lineStyle: 0 }),
            os:   oipOIChart.addLineSeries({ ...baseObj, color: '#ef4444', lineWidth: 2, lineStyle: 0 }),
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

/* ── Indicators popup ─────────────────────────────────────── */
function oipInitIndicatorsPopup() {
    // Wire showEma200 (was declared in oipElems but never initialized)
    oipElems.showEma200 = document.getElementById('oipShowEma200');

    const btn   = document.getElementById('oipIndicatorsBtn');
    const popup = document.getElementById('oipIndicatorsPopup');
    if (!btn || !popup) return;

    btn.addEventListener('click', e => {
        e.stopPropagation();
        popup.classList.toggle('hidden');
    });
    document.addEventListener('click', e => {
        if (!popup.contains(e.target) && e.target !== btn) {
            popup.classList.add('hidden');
        }
    });
}
