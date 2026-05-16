'use strict';

/**
 * Multi-Timeframe Chart Page Logic
 */

let charts = {}; // Store chart instances: { interval: { chartObj, series, indicators } }
let currentSymbol = 'NIFTY';
let allSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];
const intervals = ['minute', '3minute', '5minute', '60minute']; // Commented: '15minute', 'day', 'week', 'month'
const intervalLabels = {
    'minute': '1m',
    '3minute': '3m',
    '5minute': '5m',
    '15minute': '15m',
    '60minute': '1h',
    'day': '1D',
    'week': '1W',
    'month': '1M'
};
let liveTimer = null;

// DOM Elements
const elems = {
    symbolInput: document.getElementById('symbolSelect'),
    symbolList: document.getElementById('symbolDropdownList'),
    chartGrid: document.getElementById('chartGrid'),
    showCpr: document.getElementById('showCpr'),
    showEma9: document.getElementById('showEma9'),
    showEma20: document.getElementById('showEma20'),
    showEma50: document.getElementById('showEma50'),
    showEma200: document.getElementById('showEma200'),
    refreshBtn: document.getElementById('refreshAll')
};

async function init() {
    initSymbolDropdown();
    createCharts();
    await loadAllData();
    startLiveUpdates();

    elems.refreshBtn?.addEventListener('click', () => loadAllData());

    // Global toggles
    [elems.showCpr, elems.showEma9, elems.showEma20, elems.showEma50, elems.showEma200].forEach(el => {
        el?.addEventListener('change', updateAllIndicators);
    });
}

function startLiveUpdates() {
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = setInterval(pollLiveData, 5000); // 5-second live refresh (Polite)
}

function isMarketOpen() {
    const now = new Date();
    const day = now.getDay();
    if (day === 0 || day === 6) return false; // Weekend
    const hour = now.getHours();
    const min = now.getMinutes();
    const time = hour * 100 + min;
    return time >= 915 && time <= 1530; // 9:15 AM to 3:30 PM
}

async function pollLiveData() {
    // Only fetch live data during market hours to save resources
    const marketOpen = isMarketOpen();
    console.log(`[LivePoll] Tick - MarketOpen: ${marketOpen}, Symbol: ${currentSymbol}`);
    
    if (!marketOpen) {
        console.log('[LivePoll] Skipping poll: Market is closed.');
        return;
    }

    try {
        const url = `/api/chart/multi-live?symbol=${currentSymbol}&_t=${Date.now()}`;
        const res = await fetch(url);
        const data = await res.json();
        
        if (data.success && data.data) {
            console.log('[LivePoll] Data received:', Object.keys(data.data));
            let updatedAny = false;

            for (const [interval, candles] of Object.entries(data.data)) {
                const chartInfo = charts[interval];
                if (candles && candles.length > 0 && chartInfo && chartInfo.chartObj) {
                    let formatted = TradingViewChart.formatData(candles).filter(c => c !== null);
                    const chartObj = chartInfo.chartObj;
                    
                    if (formatted.length > 0) {
                        // Sort by time ascending
                        formatted.sort((a, b) => a.time - b.time);

                        formatted.forEach(c => {
                            if (!chartObj.candles) chartObj.candles = [];
                            
                            const lastTime = chartObj.candles.length > 0 
                                ? chartObj.candles[chartObj.candles.length - 1].time 
                                : 0;

                            if (c.time >= lastTime) {
                                try {
                                    chartObj.series.update(c);
                                    console.log(`[LivePoll] ${interval} tick: ${c.close} at ${new Date(c.time * 1000).toLocaleTimeString()}`);
                                    
                                    if (chartObj.candles.length > 0) {
                                        const last = chartObj.candles[chartObj.candles.length - 1];
                                        if (last.time === c.time) {
                                            chartObj.candles[chartObj.candles.length - 1] = c;
                                        } else {
                                            chartObj.candles.push(c);
                                            // New bar added - flag for indicator update
                                            updatedAny = true;
                                        }
                                    } else {
                                        chartObj.candles.push(c);
                                        updatedAny = true;
                                    }
                                } catch (e) {
                                    console.warn(`[LivePoll] ${interval} update failed:`, e);
                                }
                            }
                        });
                    }
                }
            }

            if (updatedAny) {
                console.log('[LivePoll] New bar detected, updating indicators...');
                updateAllIndicators();
            }
            
            console.log(`[LivePoll] Cycle complete at ${new Date().toLocaleTimeString()}`);
        }
    } catch (e) {
        console.error('[LivePoll] Error:', e);
    }
}

function initSymbolDropdown() {
    fetch('/api/symbols')
        .then(r => r.json())
        .then(d => { if (d.success) allSymbols = d.symbols; })
        .catch(console.warn);

    elems.symbolInput?.addEventListener('input', (e) => renderDropdown(e.target.value.toUpperCase()));
    elems.symbolInput?.addEventListener('click', function (e) {
        e.stopPropagation();
        if (elems.symbolList.classList.contains('show')) {
            elems.symbolList.classList.remove('show');
            elems.symbolList.classList.add('hidden');
        } else {
            this.value = '';
            renderDropdown('');
        }
    });

    elems.symbolInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const val = e.target.value.trim().toUpperCase();
            if (val) selectSymbol(val);
            elems.symbolInput.blur();
        }
    });

    document.addEventListener('click', (e) => {
        if (!elems.symbolInput.contains(e.target) && !elems.symbolList.contains(e.target)) {
            elems.symbolList.classList.remove('show');
            elems.symbolList.classList.add('hidden');
        }
    });
}

function renderDropdown(query) {
    const filtered = allSymbols.filter(s => s.includes(query)).slice(0, 50);
    elems.symbolList.innerHTML = filtered.map(s => `<li>${s}</li>`).join('');
    elems.symbolList.classList.toggle('show', filtered.length > 0);
    elems.symbolList.classList.toggle('hidden', filtered.length === 0);

    elems.symbolList.querySelectorAll('li').forEach(li => {
        li.addEventListener('click', () => selectSymbol(li.textContent));
    });
}

function selectSymbol(s) {
    currentSymbol = s;
    elems.symbolInput.value = s;
    elems.symbolList.classList.add('hidden');
    loadAllData();
}

function createCharts() {
    elems.chartGrid.innerHTML = '';
    intervals.forEach(interval => {
        const item = document.createElement('div');
        item.className = 'chart-item';
        item.innerHTML = `
            <div class="chart-item-header">
                <div class="chart-item-title">${intervalLabels[interval]} - ${currentSymbol}</div>
            </div>
            <div class="chart-container" id="container-${interval}"></div>
        `;
        elems.chartGrid.appendChild(item);

        const chartObj = TradingViewChart.create({
            containerId: `container-${interval}`,
            options: { height: 200 }
        });
        chartObj.chart.subscribeCrosshairMove(param => {
            if (!param.time || param.point === undefined) return;
            syncCrosshairs(interval, param.time);
        });

        charts[interval] = {
            chartObj: chartObj,
            indicators: { cpr: [], ema9: null, ema20: null, ema50: null, ema200: null }
        };
    });
}

function syncCrosshairs(sourceInterval, time) {
    intervals.forEach(interval => {
        if (interval === sourceInterval) return;
        const chartObj = charts[interval]?.chartObj;
        // Only sync if the target chart has a valid series and data loaded
        if (!chartObj || !chartObj.series || !chartObj.chart) return;
        
        try {
            chartObj.chart.setCrosshairPosition(0, time, chartObj.series);
        } catch (e) {
            // Silently ignore sync errors for charts that might still be loading
        }
    });
}

async function loadAllData() {
    // Update titles
    intervals.forEach(interval => {
        const titleEl = document.querySelector(`#container-${interval}`).parentElement.querySelector('.chart-item-title');
        if (titleEl) titleEl.textContent = `${intervalLabels[interval]} - ${currentSymbol}`;
    });

    // Fetch data for each interval sequentially with delay to avoid OOM (Exit Code 247)
    for (const interval of intervals) {
        console.log(`[Init] Loading history for ${interval}...`);
        await fetchIntervalData(interval);
        // Stagger requests to give server a breather
        await new Promise(r => setTimeout(r, 1000));
    }
    console.log('[Init] All history loaded.');
}

async function fetchIntervalData(interval) {
    try {
        let days = 2; // Reduced from 5 to prevent OOM
        if (interval === '15minute') days = 5;
        else if (interval === '60minute') days = 10;
        else if (interval === 'day') days = 200;
        else if (interval === 'week') days = 500;
        else if (interval === 'month') days = 1000;

        const url = `/api/oi-profile/candles?symbol=${currentSymbol}&interval=${interval}&days=${days}&auto_hl=false&_t=${Date.now()}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.success && data.candles) {
            const formatted = TradingViewChart.formatData(data.candles);
            updateChart(interval, formatted);
        }
    } catch (e) {
        console.error(`Error fetching ${interval}:`, e);
    }
}

function updateChart(interval, candles) {
    const chart = charts[interval].chartObj;
    chart.update(candles, false);

    // Store candles on the chart object for global indicator toggles
    chart.candles = candles;

    // Center the price scale around the last candle using margins (Compact)
    chart.chart.priceScale('right').applyOptions({
        autoScale: true,
        scaleMargins: { top: 0.02, bottom: 0.02 },
        entireTextOnly: true
    });

    // Standardize X-axis zoom for all charts (show last 50 candles with right space)
    if (candles.length > 50) {
        chart.chart.timeScale().setVisibleLogicalRange({
            from: candles.length - 50,
            to: candles.length + 2 // Further reduced extra bars for right-side space
        });
    } else {
        chart.chart.timeScale().fitContent();
    }

    applyIndicators(interval, candles);
}

function applyIndicators(interval, candles) {
    const chartObj = charts[interval].chartObj;
    const inds = charts[interval].indicators;

    // 1. CPR & Pivots
    if (elems.showCpr.checked) {
        const cprData = calculateDynamicCPR(candles, interval);
        // Clear old CPR lines and boxes
        inds.cpr.forEach(s => chartObj.chart.removeSeries(s));
        inds.cpr = [];

        if (cprData && cprData.buckets) {
            const lineConfigs = [
                { key: 'pp', color: '#3366ff' },
                { key: 'tc', color: '#3366ff' },
                { key: 'bc', color: '#3366ff' },
                { key: 'r1', color: '#006400' },
                { key: 'r2', color: '#006400' },
                { key: 'r3', color: '#006400' },
                { key: 'r4', color: '#006400' },
                { key: 's1', color: '#ff0000' },
                { key: 's2', color: '#ff0000' },
                { key: 's3', color: '#ff0000' },
                { key: 's4', color: '#ff0000' },
                { key: 'cr3', color: '#a020f0', width: 2 },
                { key: 'cs3', color: '#a020f0', width: 2 },
                { key: 'prevH', color: '#ef07f9' },
                { key: 'prevL', color: '#ef07f9' }
            ];

            cprData.buckets.forEach(bucket => {
                if (!bucket.levels) return;

                const boxConfigs = [
                    { k1: 'tc', k2: 'bc', color: 'rgba(51, 102, 255, 0.1)' },
                    { k1: 'r1', k2: 'r2', color: 'rgba(0, 204, 102, 0.02)' },
                    { k1: 'r2', k2: 'r3', color: 'rgba(0, 204, 102, 0.04)' },
                    { k1: 'r3', k2: 'r4', color: 'rgba(0, 204, 102, 0.06)' },
                    { k1: 's1', k2: 's2', color: 'rgba(255, 0, 0, 0.02)' },
                    { k1: 's2', k2: 's3', color: 'rgba(255, 0, 0, 0.04)' },
                    { k1: 's3', k2: 's4', color: 'rgba(255, 0, 0, 0.06)' }
                ];

                boxConfigs.forEach(conf => {
                    const v1 = bucket.levels[conf.k1], v2 = bucket.levels[conf.k2];
                    if (v1 == null || v2 == null) return;

                    const minV = Math.min(v1, v2);
                    const maxV = Math.max(v1, v2);

                    const box = chartObj.chart.addBaselineSeries({
                        baseValue: { type: 'price', price: minV },
                        topFillColor1: conf.color, topFillColor2: conf.color,
                        topLineColor: 'transparent', bottomLineColor: 'transparent',
                        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
                        autoscaleInfoProvider: () => null
                    });
                    box.setData(bucket.times.map(t => ({ time: t, value: maxV })));
                    inds.cpr.push(box);
                });

                // Draw lines for each level in this bucket (prevents continuous lines)
                lineConfigs.forEach(conf => {
                    const val = bucket.levels[conf.key];
                    if (val == null) return;

                    const line = chartObj.chart.addLineSeries({
                        color: conf.color,
                        lineWidth: conf.width || 1,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                        autoscaleInfoProvider: () => null
                    });
                    line.setData(bucket.times.map(t => ({ time: t, value: val })));
                    inds.cpr.push(line);
                });
            });
        }
    } else {
        inds.cpr.forEach(s => s.applyOptions({ visible: false }));
    }

    // 2. EMAs
    const emaConfigs = [
        { id: 'showEma9', period: 9, color: '#22c55e', key: 'ema9' },
        { id: 'showEma20', period: 20, color: '#f97316', key: 'ema20' },
        { id: 'showEma50', period: 50, color: '#ef4444', key: 'ema50' },
        { id: 'showEma200', period: 200, color: '#000000', key: 'ema200' }
    ];

    emaConfigs.forEach(conf => {
        const checkbox = document.getElementById(conf.id);
        if (checkbox && checkbox.checked) {
            const emaData = calculateEMA(candles, conf.period);
            if (!inds[conf.key]) {
                inds[conf.key] = chartObj.chart.addLineSeries({
                    color: conf.color,
                    lineWidth: 1,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false
                });
            }
            inds[conf.key].setData(emaData);
            inds[conf.key].applyOptions({ visible: true });
        } else if (inds[conf.key]) {
            inds[conf.key].applyOptions({ visible: false });
        }
    });
}

function updateAllIndicators() {
    intervals.forEach(interval => {
        const chartObj = charts[interval].chartObj;
        if (chartObj.candles && chartObj.candles.length > 0) {
            applyIndicators(interval, chartObj.candles);
        }
    });
}

/** ── Calculation Utilities (Mirrored from OI Profile) ── */

function calculateEMA(data, period) {
    if (data.length < period) return [];
    const k = 2 / (period + 1);
    const ema = [];
    let prevEma = data[0].close;
    data.forEach(d => {
        const val = (d.close * k) + (prevEma * (1 - k));
        ema.push({ time: d.time, value: val });
        prevEma = val;
    });
    return ema;
}

function calculateDynamicCPR(candles, chartInterval) {
    if (candles.length < 2) return null;
    const resultBuckets = [];
    const rawBuckets = [];
    let currentBucket = null;

    // Define grouping logic based on chart timeframe
    const getBucketKey = (date, interval) => {
        const y = date.getUTCFullYear();
        const m = date.getUTCMonth();
        if (interval === 'month') return `${y}-${m}`;
        if (interval === 'week') {
            const firstDayOfYear = new Date(Date.UTC(y, 0, 1));
            const pastDaysOfYear = (date - firstDayOfYear) / 86400000;
            const weekNum = Math.ceil((pastDaysOfYear + firstDayOfYear.getUTCDay() + 1) / 7);
            return `${y}-W${weekNum}`;
        }
        if (interval === '6month') return `${y}-H${m < 6 ? 1 : 2}`;
        if (interval === 'year') return `${y}`;
        return `${y}-${m + 1}-${date.getUTCDate()}`;
    };

    let targetInterval = 'day';
    if (chartInterval === '60minute') targetInterval = 'week';
    else if (chartInterval === 'day') targetInterval = 'month';
    else if (chartInterval === 'week') targetInterval = '6month';
    else if (chartInterval === 'month') targetInterval = 'year';

    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        const bk = getBucketKey(d, targetInterval);
        if (!currentBucket || currentBucket.key !== bk) {
            if (currentBucket) rawBuckets.push(currentBucket);
            currentBucket = { key: bk, high: c.high, low: c.low, close: c.close, times: [] };
        }
        currentBucket.high = Math.max(currentBucket.high, c.high);
        currentBucket.low = Math.min(currentBucket.low, c.low);
        currentBucket.close = c.close;
        currentBucket.times.push(c.time);
    });
    if (currentBucket) rawBuckets.push(currentBucket);

    for (let i = 1; i < rawBuckets.length; i++) {
        const prev = rawBuckets[i - 1], curr = rawBuckets[i];
        const oH = prev.high, oL = prev.low, oC = prev.close;
        const range = oH - oL;
        const pp = (oH + oL + oC) / 3;
        const bc = (oH + oL) / 2;
        const tc = (pp - bc) + pp;
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

        resultBuckets.push({
            times: curr.times,
            levels: {
                pp, tc, bc,
                r1, r2, r3, r4,
                s1, s2, s3, s4,
                cr3, cs3,
                prevH: oH, prevL: oL
            }
        });
    }
    return { buckets: resultBuckets };
}

// Start
document.addEventListener('DOMContentLoaded', init);
