/**
 * OI Profile – WHITE THEME logic
 * Full-width Chart with In-Chart OI Bar Overlay
 *
 * Improvements in this version:
 *  • Precise White Theme (matched to /intraday-920)
 *  • NO auto-rescale on live updates – keeps user zoom/pan intact
 *  • Correct strike alignment even under zoom
 *  • Dashboard-style container box
 *  • 3s auto-refresh
 */

'use strict';

/* ── State ────────────────────────────────────────────────── */
let oipOIChart = null;
let oipOISeries = null;
let oipIntrinsicChart = null; // TradingViewChart instance for bottom chart
let oipIntrinsicSeries = null;
let oipIntrinsicPeSeries = null; // For combined view
let oipOIData = null;           // Cache for main OI and intrinsic data
let oipOptionData = null;       // Cache for option candles
let oipVwapSeries = null;       // VWAP for top chart
let oipVwapIntSeries = null;    // VWAP for bottom chart (Primary/CE)
let oipVwapIntPeSeries = null;  // VWAP for bottom chart (PE)

// Strike tracking for orders
let oipCurrentCEStrike = null;
let oipCurrentPEStrike = null;

let oipCprSeriesObj = null;          // Tracker for CPR Line Series 
let oipAllStrikes = [];         // [{strike, ce_oi, pe_oi}, …]
let oipCurrentPrice = 0;
let oipSymbol = 'NIFTY';
let oipInterval = '5minute';
let oipStrikeCount = 15;
let oipMode = 'change';   // 'total' or 'change'
let oipIsBusy = false;
let oipRafId = null;
let oipIsFirstLoad = true;       // flag to control auto-rescaling

/* ── Bootstrap ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('oipSymbol').addEventListener('change', e => {
        oipSymbol = e.target.value;
        oipFullRefresh(true);
    });
    document.getElementById('oipInterval').addEventListener('change', e => {
        oipInterval = e.target.value;
        oipLoadCandles();
    });
    document.getElementById('oipStrikes').addEventListener('change', e => {
        oipStrikeCount = parseInt(e.target.value);
        oipRequestDraw();
    });

    // OI vs CHG mode listener
    document.querySelectorAll('input[name="oipMode"]').forEach(radio => {
        radio.addEventListener('change', e => {
            oipMode = e.target.value;
            oipRequestDraw();
        });
    });

    // Spot High/Low changes: Need to fetch NEW intrinsic strikes and option data
    ['oipSpotHigh', 'oipSpotLow'].forEach(id => {
        document.getElementById(id).addEventListener('change', () => oipLoadCandles(true));
    });

    // Step/Multiplier/View changes: Only need local re-render (instant)
    ['oipStep', 'oipMultiplier', 'oipIntrinsicView'].forEach(id => {
        document.getElementById(id).addEventListener('change', (e) => {
            const view = document.getElementById('oipIntrinsicView').value;
            const rescaled = (id === 'oipIntrinsicView'); // Auto-rescale only on view change

            // If switching to option view but data is missing, we must fetch
            const needsOptionData = (view !== 'index') && !oipOptionData;

            if (oipOIData && !needsOptionData) oipLoadCandles(false, rescaled);
            else oipLoadCandles(true, rescaled);
        });
    });
    document.getElementById('oipShowLevels').addEventListener('change', () => {
        if (oipOIData && oipOIData.intrinsic) {
            oipDrawIntrinsicLines(oipOIData.intrinsic, document.getElementById('oipIntrinsicView').value);
        }
    });

    document.getElementById('oipShowVwapOI').addEventListener('change', e => {
        if (oipVwapSeries) oipVwapSeries.applyOptions({ visible: e.target.checked });
    });
    document.getElementById('oipShowVwapInt').addEventListener('change', e => {
        const show = e.target.checked;
        if (oipVwapIntSeries) oipVwapIntSeries.applyOptions({ visible: show });
        if (oipVwapIntPeSeries) oipVwapIntPeSeries.applyOptions({ visible: show });
    });
    document.getElementById('oipShowCpr').addEventListener('change', () => {
        if (oipOIData && oipOIData.candles) oipDrawCpr(oipOIData.candles);
    });

    document.getElementById('oipAutoHL')?.addEventListener('click', oipAutoFillHighLow);

    // Order button listeners
    document.querySelectorAll('.oip-order-btn').forEach(btn => {
        btn.addEventListener('click', e => {
            const side = btn.dataset.side;
            const action = btn.dataset.action;
            oipPlaceOrder(side, action, btn);
        });
    });

    oipInitCharts();

    oipOIChart.subscribeCrosshairMove(param => {
        const tooltip = document.getElementById('oipTooltip');
        if (!param.point || !oipAllStrikes.length) {
            tooltip.classList.add('hidden');
            return;
        }

        const W = document.getElementById('oipOICanvas').width / window.devicePixelRatio;
        const plotRight = W - 70; // Matches fixed axis width
        const MAX_BAR_WIDTH_RATIO = 0.35;
        const MAX_BAR_PX = Math.min(plotRight * MAX_BAR_WIDTH_RATIO, 300);

        const inZone = param.point.x > (plotRight - MAX_BAR_PX - 20) && param.point.x < plotRight + 10;
        if (!inZone) { tooltip.classList.add('hidden'); return; }

        const price = oipOISeries.coordinateToPrice(param.point.y);
        if (!price) { tooltip.classList.add('hidden'); return; }

        let nearest = oipAllStrikes[0];
        let minDist = Math.abs(oipAllStrikes[0].strike - price);
        for (let s of oipAllStrikes) {
            const d = Math.abs(s.strike - price);
            if (d < minDist) { minDist = d; nearest = s; }
        }
        if (minDist > 100) { tooltip.classList.add('hidden'); return; }

        const isChg = (oipMode === 'change');
        const ceVal = isChg ? (nearest.ce_change_in_oi || 0) : (nearest.ce_oi || 0);
        const peVal = isChg ? (nearest.pe_change_in_oi || 0) : (nearest.pe_oi || 0);
        const lblExt = isChg ? ' Chg' : ' OI';

        tooltip.innerHTML = `
            <div class="strike">Strike: ${nearest.strike}</div>
            <div class="row">
                <div class="lbl-wrap"><div class="dot ce"></div><div class="lbl">Call${lblExt}:</div></div>
                <div class="val">${fmtL(ceVal)}</div>
            </div>
            <div class="row">
                <div class="lbl-wrap"><div class="dot pe"></div><div class="lbl">Put${lblExt}:</div></div>
                <div class="val">${fmtL(peVal)}</div>
            </div>
        `;
        tooltip.classList.remove('hidden');
        const ttW = tooltip.offsetWidth;
        const ttH = tooltip.offsetHeight;
        let left = param.point.x - ttW - 15;
        let top = param.point.y - ttH / 2;
        if (left < 10) left = param.point.x + 15;
        tooltip.style.left = left + 'px';
        tooltip.style.top = top + 'px';
    });

    oipFullRefresh(true);

    setInterval(() => {
        if (oipIsMarketOpen()) oipFullRefresh(false);
    }, 2000); // 2-second auto-refresh for live data
});

/* ── Lightweight Charts Initialization ──────────────────────── */
function oipInitCharts() {
    // 1. OI Profile Chart
    const elOI = document.getElementById('oipCandleChart');
    const wrapOI = document.getElementById('oipChartWrap');
    if (elOI && typeof LightweightCharts !== 'undefined') {
        oipOIChart = creatBaseChart(elOI);
        oipOISeries = oipOIChart.addCandlestickSeries(candleStyle());

        // VWAP for top chart (Index) - Use Gold to avoid conflict with CE/PE
        oipVwapSeries = oipOIChart.addLineSeries({
            color: '#f59e0b', lineWidth: 2, title: '',
            visible: document.getElementById('oipShowVwapOI').checked,
            priceLineVisible: false, lastValueVisible: false
        });

        oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(() => oipRequestDraw());
        oipOIChart.timeScale().subscribeVisibleTimeRangeChange(() => oipRequestDraw());
        const ps = oipOIChart.priceScale('right');
        if (ps && typeof ps.subscribePriceRangeChange === 'function') {
            ps.subscribePriceRangeChange(() => oipRequestDraw());
        }
        oipOIChart.subscribeCrosshairMove(() => oipRequestDraw());

        new ResizeObserver(() => {
            syncSize(oipOIChart, wrapOI);
            oipRequestDraw();
        }).observe(wrapOI);
    }

    // 2. Intrinsic Levels Chart - Use shared TradingViewChart component
    const elInt = document.getElementById('oipIntrinsicChart');
    if (elInt && typeof TradingViewChart !== 'undefined') {
        oipIntrinsicChart = TradingViewChart.create({
            containerId: 'oipIntrinsicChart',
            data: [],
            type: 'COMBINED',
            isCombined: true,
            timeframe: oipInterval,
            options: { height: 300 }
        });

        // Expose series for easier manipulation if needed, 
        // though TradingViewChart.update() is the preferred way.
        oipIntrinsicSeries = oipIntrinsicChart.ceSeries || oipIntrinsicChart.series;
        oipIntrinsicPeSeries = oipIntrinsicChart.peSeries;

        const showV = document.getElementById('oipShowVwapInt').checked;
        // VWAP lines for bottom chart - Only keep labels, remove horizontal price lines
        oipVwapIntSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#10b981', lineWidth: 2, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false
        });
        oipVwapIntPeSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#8b5cf6', lineWidth: 2, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false
        });
    }
}

function creatBaseChart(el) {
    return LightweightCharts.createChart(el, {
        width: el.clientWidth || 1200,
        height: 300,
        layout: { textColor: '#374151', background: { type: 'solid', color: '#ffffff' } },
        grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
        crosshair: {
            mode: 0,
            vertLine: { color: '#9ca3af', style: 3 },
            horzLine: { color: '#9ca3af', style: 3, labelBackgroundColor: '#0969da' }
        },
        timeScale: { timeVisible: true, textColor: '#6b7280', borderColor: 'transparent', rightOffset: 60 },
        rightPriceScale: { textColor: '#6b7280', borderColor: 'transparent', width: 70, autoScale: true, scaleMargins: { top: 0.05, bottom: 0.05 } },
        handleScroll: true, handleScale: true, localization: { locale: 'en-IN' }
    });
}

function candleStyle() {
    return {
        upColor: '#10b981', downColor: '#ef4444',
        borderUpColor: '#10b981', borderDownColor: '#ef4444',
        wickUpColor: '#10b981', wickDownColor: '#ef4444',
        priceLineStyle: 1, // Dotted
        priceLineWidth: 1
    };
}

function syncSize(chart, wrap) {
    const w = wrap.clientWidth;
    const h = wrap.clientHeight;
    if (chart && w > 0 && h > 0) chart.applyOptions({ width: w, height: h });
}

function oipRequestDraw() {
    if (!oipRafId) {
        oipRafId = requestAnimationFrame(oipDrawOIBars);
    }
}

/* ── Canvas OI overlay ────────────────────────────────────── */
function oipDrawOIBars() {
    oipRafId = null;
    const canvas = document.getElementById('oipOICanvas');
    const wrap = document.getElementById('oipChartWrap');
    if (!canvas || !wrap || !oipOISeries) return;

    const W = wrap.clientWidth;
    const H = wrap.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        canvas.style.width = W + 'px';
        canvas.style.height = H + 'px';
    }

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!oipAllStrikes.length) return;

    const priceTop = oipOISeries.coordinateToPrice(0);
    const priceBottom = oipOISeries.coordinateToPrice(H);

    let filtered = [];
    if (priceTop !== null && priceBottom !== null && oipAllStrikes.length) {
        const minP = Math.min(priceTop, priceBottom);
        const maxP = Math.max(priceTop, priceBottom);
        const pad = (maxP - minP) * 0.1;
        filtered = oipAllStrikes.filter(s =>
            s.strike >= (minP - pad) &&
            s.strike <= (maxP + pad)
        );
    } else {
        filtered = oipFilterStrikes(oipAllStrikes, oipCurrentPrice, oipStrikeCount);
    }

    if (filtered.length > 1) filtered.sort((a, b) => a.strike - b.strike);
    if (!filtered.length) return;

    const MAX_BAR_WIDTH_RATIO = 0.35;
    const plotRight = W - 70;
    const MAX_BAR_PX = Math.min(plotRight * MAX_BAR_WIDTH_RATIO, 300);

    const getCE = (s) => (oipMode === 'total' ? (s.ce_oi || 0) : (s.ce_change_in_oi || 0));
    const getPE = (s) => (oipMode === 'total' ? (s.pe_oi || 0) : (s.pe_change_in_oi || 0));

    const maxVal = Math.max(...filtered.flatMap(s => [Math.abs(getCE(s)), Math.abs(getPE(s))]), 1);

    let barH = 8;
    if (filtered.length >= 2) {
        const y0 = oipOISeries.priceToCoordinate(filtered[0].strike);
        const y1 = oipOISeries.priceToCoordinate(filtered[1].strike);
        if (y0 !== null && y1 !== null) {
            const pxDist = Math.abs(y1 - y0);
            barH = Math.max(1, Math.min(25, pxDist * 0.38));
        }
    }

    const ceCol = 'rgba(239, 68, 68, 0.6)';
    const peCol = 'rgba(16, 185, 129, 0.6)';
    const ceColMax = 'rgba(239, 68, 68, 0.95)';
    const peColMax = 'rgba(16, 185, 129, 0.95)';

    let ceMaxStr = 0, peMaxStr = 0;
    let maxCEV = -1, maxPEV = -1;
    filtered.forEach(s => {
        if (getCE(s) > maxCEV) { maxCEV = getCE(s); ceMaxStr = s.strike; }
        if (getPE(s) > maxPEV) { maxPEV = getPE(s); peMaxStr = s.strike; }
    });

    filtered.forEach(s => {
        const y = oipOISeries.priceToCoordinate(s.strike);
        if (y === null || y < -50 || y > H + 50) return;

        const valCE = getCE(s);
        const valPE = getPE(s);
        const ceW = (Math.abs(valCE) / maxVal) * MAX_BAR_PX;
        const peW = (Math.abs(valPE) / maxVal) * MAX_BAR_PX;

        if (valCE < 0) {
            ctx.strokeStyle = (s.strike === ceMaxStr) ? ceColMax : ceCol;
            ctx.lineWidth = 1.5;
            ctx.strokeRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
        } else if (valCE > 0) {
            ctx.fillStyle = (s.strike === ceMaxStr) ? ceColMax : ceCol;
            ctx.fillRect(plotRight - ceW, y - barH - 0.5, ceW, barH);
        }

        if (valPE < 0) {
            ctx.strokeStyle = (s.strike === peMaxStr) ? peColMax : peCol;
            ctx.lineWidth = 1.5;
            ctx.strokeRect(plotRight - peW, y + 0.5, peW, barH);
        } else if (valPE > 0) {
            ctx.fillStyle = (s.strike === peMaxStr) ? peColMax : peCol;
            ctx.fillRect(plotRight - peW, y + 0.5, peW, barH);
        }
    });

    ctx.strokeStyle = 'rgba(0,0,0,0.1)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(plotRight, 0);
    ctx.lineTo(plotRight, H);
    ctx.stroke();
}

/** ±N strikes around price */
function oipFilterStrikes(strikes, price, n) {
    if (!strikes.length) return [];
    const sorted = [...strikes].sort((a, b) => a.strike - b.strike);
    if (!price || n >= 999) return sorted;
    let atmI = 0, mindI = Infinity;
    sorted.forEach((s, i) => { const d = Math.abs(s.strike - price); if (d < mindI) { mindI = d; atmI = i; } });
    const lo = Math.max(0, atmI - n);
    const hi = Math.min(sorted.length - 1, atmI + n);
    return sorted.slice(lo, hi + 1);
}

/* ── Refresh Logic ────────────────────────────────────────── */
async function oipFullRefresh(resetZoom = false) {
    if (oipIsBusy) return;
    oipIsBusy = true;
    if (resetZoom) oipIsFirstLoad = true;
    setRefreshBtn(true);

    try {
        await Promise.allSettled([
            oipLoadOI(),
            oipLoadCandles(true, resetZoom)
        ]);
    } catch (err) {
        console.error('[OIP] Refresh Err:', err);
    } finally {
        oipIsBusy = false;
        setRefreshBtn(false);
    }
}

async function oipLoadOI() {
    try {
        const res = await fetch('/api/open-interest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: oipSymbol })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        // Merge OI data but prioritize keeping the large candle arrays from oipLoadCandles
        oipOIData = Object.assign(oipOIData || {}, data);
        oipAllStrikes = data.strikes || [];
        oipCurrentPrice = data.current_price || 0;

        oipUpdateHeader(data);
        oipRequestDraw();
    } catch (e) { console.warn('[OIP] OI Load Err:', e); }
}

/** Load Chart Data and Render */
async function oipLoadCandles(forceFetch = true, resetZoom = false) {
    try {
        const h = parseFloat(document.getElementById('oipSpotHigh').value);
        const l = parseFloat(document.getElementById('oipSpotLow').value);
        const s = parseInt(document.getElementById('oipStep').value);
        const m = parseInt(document.getElementById('oipMultiplier').value);
        const view = document.getElementById('oipIntrinsicView').value;

        // If switching to options but we don't have them, we must fetch
        const needsOptionData = (view !== 'index') && !oipOptionData;

        const autoHL = oipIsFirstLoad; // Offload auto calculation to backend purely on first load

        // Skip fetch if forceFetch is false and we already have all required data 
        if (!forceFetch && oipOIData && !needsOptionData) {
            oipRefreshLocalView(view, resetZoom);
            return;
        }

        // 1. Fetch main OI and Index data
        const res = await fetch(`/api/oi-profile/candles?symbol=${oipSymbol}&interval=${oipInterval}&days=3&spot_high=${h}&spot_low=${l}&step=${s}&multiplier=${m}&auto_hl=${autoHL}&_t=${Date.now()}`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        // IMPORTANT: Merge with existing data so we don't lose candles when oipLoadOI runs
        oipOIData = Object.assign(oipOIData || {}, data);
        const indexCandles = data.candles || [];

        if (oipOISeries && indexCandles.length) {
            oipOISeries.setData(indexCandles);
            if (oipVwapSeries) oipVwapSeries.setData(oipCalculateVWAP(indexCandles));
            oipDrawCpr(indexCandles);
        }

        if (autoHL && data.intrinsic && data.intrinsic.spot_high) {
            console.log(`[OIP] Backend Auto-detected H/L: ${data.intrinsic.spot_high} - ${data.intrinsic.spot_low}`);
            document.getElementById('oipSpotHigh').value = data.intrinsic.spot_high;
            document.getElementById('oipSpotLow').value = data.intrinsic.spot_low;
        }

        if (oipIntrinsicChart) {
            if (view === 'index') {
                oipIntrinsicChart.update(indexCandles, null, true);
                oipIntrinsicChart.setMarkers([], []); // Clear signals for index
                const itmCE = document.getElementById('oipItmCE');
                const itmPE = document.getElementById('oipItmPE');
                if (itmCE) itmCE.textContent = 'NIFTY';
                if (itmPE) itmPE.textContent = 'Index';

                if (data.intrinsic) {
                    oipDrawIntrinsicLines(data.intrinsic, view);
                }
            } else {
                // 3. Handle Option data (Avoid redundant fetch if already in first API response)
                const ceStrike = data.intrinsic?.itm_ce_strike;
                const peStrike = data.intrinsic?.itm_pe_strike;

                // Store for orders
                oipCurrentCEStrike = ceStrike;
                oipCurrentPEStrike = peStrike;

                if (ceStrike && peStrike) {
                    let ceData = [], peData = [];

                    // Try using candles from the first primary fetch (Optimization)
                    if (data.ce_opt_candles && data.pe_opt_candles && data.ce_opt_candles.length) {
                        console.log('[OIP] API 1 already had option candles. Skipping API 2.');
                        ceData = data.ce_opt_candles.map(c => ({ ...c, type: 'CE' }));
                        peData = data.pe_opt_candles.map(c => ({ ...c, type: 'PE' }));
                        oipOptionData = [...ceData, ...peData]; // Cache for local refresh
                    } else {
                        // Fallback: Redundant fetch only if backend did not return them in API 1
                        console.log('[OIP] Backend missing option candles. Falling back to API 2 fetch.');
                        const optRes = await fetch('/api/options-chart-data', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ symbol: oipSymbol, ce_strike: ceStrike, pe_strike: peStrike, timeframe: oipInterval })
                        });
                        const optData = await optRes.json();
                        if (optData.success) {
                            oipOptionData = optData.data;
                            ceData = oipOptionData.filter(c => c.type === 'CE');
                            peData = oipOptionData.filter(c => c.type === 'PE');
                        }
                    }

                    if (ceData.length || peData.length) {
                        const ceStrikeStr = ceStrike;
                        const peStrikeStr = peStrike;

                        // Update ITM Dashboard badges
                        const itmCE = document.getElementById('oipItmCE');
                        const itmPE = document.getElementById('oipItmPE');
                        if (itmCE) itmCE.textContent = `${ceStrikeStr} CE`;
                        if (itmPE) itmPE.textContent = `${peStrikeStr} PE`;

                        if (view === 'combined') {
                            console.log(`[OIP] Setting Combined view with ${ceData.length} CE / ${peData.length} PE candles`);
                            oipIntrinsicChart.update(ceData, peData, true);
                            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
                            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));

                            // Calculate and set Pine Signals (Markers)
                            const ceSignals = oipCalculatePineSignals(ceData, data.intrinsic.ce_levels, 'CE');
                            const peSignals = oipCalculatePineSignals(peData, data.intrinsic.pe_levels, 'PE');
                            oipIntrinsicChart.setMarkers(ceSignals, peSignals);
                        } else if (view === 'ce') {
                            console.log(`[OIP] Setting CE view with ${ceData.length} candles`);
                            oipIntrinsicChart.update(ceData, null, true);
                            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
                            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);

                            const ceSignals = oipCalculatePineSignals(ceData, data.intrinsic.ce_levels, 'CE');
                            oipIntrinsicChart.setMarkers(ceSignals, []);
                        } else {
                            console.log(`[OIP] Setting PE view with ${peData.length} candles`);
                            oipIntrinsicChart.update(null, peData, true);
                            if (oipVwapIntSeries) oipVwapIntSeries.setData([]);
                            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));

                            const peSignals = oipCalculatePineSignals(peData, data.intrinsic.pe_levels, 'PE');
                        }
                    }
                } // End if (ceStrike && peStrike)

                // Draw intrinsic levels AFTER the chart updates from fetch
                if (data.intrinsic) {
                    oipDrawIntrinsicLines(data.intrinsic, view);
                }
            } // End else
            oipRequestDraw();
        }

        if (data.intrinsic) {
            // oipUpdateItmDash(data.intrinsic); (Removed as requested)
        }

        if (oipIsFirstLoad && indexCandles.length) {
            if (oipOIChart) {
                oipOIChart.priceScale('right').applyOptions({ autoScale: true });
                const visibleLen = Math.min(indexCandles.length, 90);
                oipOIChart.timeScale().setVisibleLogicalRange({
                    from: indexCandles.length - visibleLen,
                    to: indexCandles.length + 30
                });
                oipIsFirstLoad = false;
            }
        }
    } catch (e) { console.error('[OIP] Refresh Err:', e); }
}

/* ── UI Logic ────────────────────────────────────────────── */
function oipUpdateHeader(data) {
    const price = data.current_price || 0;
    const pcr = data.pcr_oi || 0;
    const maxP = data.max_pain || '--';
    const ce = data.ce_summary || {};
    const pe = data.pe_summary || {};
    const strikes = data.strikes || [];

    const ceSorted = [...strikes].sort((a, b) => (b.ce_oi || 0) - (a.ce_oi || 0));
    const peSorted = [...strikes].sort((a, b) => (b.pe_oi || 0) - (a.pe_oi || 0));

    document.getElementById('hdrPrice').textContent = price.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    document.getElementById('hdrPcr').textContent = pcr.toFixed(2);
    document.getElementById('hdrMaxPain').textContent = maxP;

    // Res / Supp
    document.getElementById('hdrRes').textContent = ceSorted[0]?.strike || '--';
    document.getElementById('hdrSupp').textContent = peSorted[0]?.strike || '--';

    // OI Totals & Change
    document.getElementById('hdrCeOI').textContent = fmtL(ce.total_oi);
    document.getElementById('hdrCeChg').textContent = fmtL(ce.change_in_oi);
    document.getElementById('hdrPeOI').textContent = fmtL(pe.total_oi);
    document.getElementById('hdrPeChg').textContent = fmtL(pe.change_in_oi);

    // Trend label
    const trendEl = document.getElementById('hdrTrend');
    if (pcr >= 1.25) { trendEl.textContent = 'Bullish'; trendEl.className = 'oip-hdr-val grn'; }
    else if (pcr >= 1.0) { trendEl.textContent = 'M-Bullish'; trendEl.className = 'oip-hdr-val grn'; }
    else if (pcr <= 0.6) { trendEl.textContent = 'Bearish'; trendEl.className = 'oip-hdr-val red'; }
    else if (pcr <= 0.8) { trendEl.textContent = 'M-Bearish'; trendEl.className = 'oip-hdr-val red'; }
    else { trendEl.textContent = 'Neutral'; trendEl.className = 'oip-hdr-val'; }

    // ATM
    let atm = '--', mind = Infinity;
    strikes.forEach(s => { const d = Math.abs(s.strike - price); if (d < mind) { mind = d; atm = s.strike; } });
    document.getElementById('hdrAtm').textContent = atm;
}

/** Draw Intrinsic Price Lines */
let oipLevelLines = [];
function oipDrawIntrinsicLines(intrinsic, view = 'index') {
    if (!oipIntrinsicChart || !oipIntrinsicSeries) return;

    // Clear old lines from intrinsic chart safely
    oipLevelLines.forEach(l => {
        try { if (oipIntrinsicSeries) oipIntrinsicSeries.removePriceLine(l); } catch (e) { }
        try { if (oipIntrinsicPeSeries) oipIntrinsicPeSeries.removePriceLine(l); } catch (e) { }
    });
    oipLevelLines = [];

    const show = document.getElementById('oipShowLevels').checked;
    if (!show || !intrinsic) return;

    const { ce_intrinsic, pe_intrinsic } = intrinsic;
    const step = parseInt(document.getElementById('oipStep').value) || 50;
    const mult = parseInt(document.getElementById('oipMultiplier').value) || 10;

    // Calculate levels locally to allow manual adding without API fetch
    const ceLevels = [];
    const peLevels = [];
    for (let i = 1; i <= mult; i++) {
        ceLevels.push(ce_intrinsic + step * i);
        peLevels.push(pe_intrinsic + step * i);
    }

    // Filter levels based on current view
    const showCE = (view === 'index' || view === 'ce' || view === 'combined');
    const showPE = (view === 'index' || view === 'pe' || view === 'combined');

    // CE Lines
    if (showCE) {
        const ceBase = oipIntrinsicSeries.createPriceLine({
            price: ce_intrinsic, color: '#10b981', lineWidth: 2, lineStyle: 0,
            axisLabelVisible: true, title: 'CE IV'
        });
        oipLevelLines.push(ceBase);

        ceLevels.forEach((lvl, i) => {
            const line = oipIntrinsicSeries.createPriceLine({
                price: lvl, color: '#10b981', lineWidth: 1, lineStyle: 0,
                axisLabelVisible: true, title: `CE L${i + 1}`
            });
            oipLevelLines.push(line);
        });
    }

    // PE Lines
    if (showPE) {
        const peS = oipIntrinsicPeSeries || oipIntrinsicSeries;
        const peBase = peS.createPriceLine({
            price: pe_intrinsic, color: '#8b5cf6', lineWidth: 2, lineStyle: 0,
            axisLabelVisible: true, title: 'PE IV'
        });
        oipLevelLines.push(peBase);

        peLevels.forEach((lvl, i) => {
            const line = peS.createPriceLine({
                price: lvl, color: '#8b5cf6', lineWidth: 1, lineStyle: 0,
                axisLabelVisible: true, title: `PE L${i + 1}`
            });
            oipLevelLines.push(line);
        });
    }
}




function fmtL(n) {
    if (n == null) return '--';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '+';
    if (abs >= 10000000) return sign + (abs / 10000000).toFixed(2) + ' Cr';
    if (abs >= 100000) return sign + (abs / 100000).toFixed(2) + ' L';
    return n.toLocaleString('en-IN');
}


function setRefreshBtn(l) {
    const icon = document.getElementById('oipRefreshIcon');
    icon?.classList.toggle('spin', l);
}

function oipIsMarketOpen() {
    const n = new Date();
    if (n.getDay() === 0 || n.getDay() === 6) return false;
    const ist = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(n);
    const [h, m] = ist.split(':').map(Number);
    const mins = h * 60 + m;
    return mins >= 555 && mins <= 930;
}
/** Refresh chart view from cached data without fetching */
function oipRefreshLocalView(view, resetZoom = false) {
    if (!oipOIData || !oipIntrinsicChart) return;

    const indexCandles = oipOIData.candles || [];
    if (view === 'index') {
        const badge = document.getElementById('oipCurrentStrike');
        if (badge) badge.textContent = 'NIFTY Index';
        oipIntrinsicChart.update(indexCandles, null, resetZoom);
        if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(indexCandles));
        if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
    } else if (oipOptionData) {
        const ceData = oipOptionData.filter(c => c.type === 'CE');
        const peData = oipOptionData.filter(c => c.type === 'PE');

        const badge = document.getElementById('oipCurrentStrike');
        if (oipOIData.intrinsic && badge) {
            badge.textContent = `ITM: ${oipOIData.intrinsic.itm_ce_strike} CE / ${oipOIData.intrinsic.itm_pe_strike} PE`;
        }

        if (view === 'combined') {
            oipIntrinsicChart.update(ceData, peData, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));
        } else if (view === 'ce') {
            oipIntrinsicChart.update(ceData, null, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData(oipCalculateVWAP(ceData));
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData([]);
        } else {
            oipIntrinsicChart.update(null, peData, resetZoom);
            if (oipVwapIntSeries) oipVwapIntSeries.setData([]);
            if (oipVwapIntPeSeries) oipVwapIntPeSeries.setData(oipCalculateVWAP(peData));
        }
    }

    if (oipOIData.intrinsic) {
        oipDrawIntrinsicLines(oipOIData.intrinsic, view);
    }
}

/** 
 * Calculate Signals identical to Pine Script Logic 
 * Crossover of Level Entry | Target Exit | SL Exit
 */
function oipCalculatePineSignals(candles, levels, type) {
    if (!candles || candles.length < 2 || !levels || levels.length === 0) return [];

    console.log(`[OIP Signals] Calculating ${type} signals for ${candles.length} candles using ${levels.length} levels`);

    const markers = [];
    let tradeActive = false;
    let activeSL = null;
    let activeTarget = null;

    // Scan candles for transitions
    for (let i = 1; i < candles.length; i++) {
        const c = candles[i];
        const p = candles[i - 1];

        // EXIT LOGIC (Checked first to avoid same-bar entry/exit conflicts)
        if (tradeActive) {
            // Target Hit (Arrow Down, Purple)
            if (activeTarget && c.high >= activeTarget) {
                markers.push({
                    time: c.time,
                    position: 'aboveBar',
                    color: '#8b5cf6', // Purple ITM / Target
                    shape: 'arrowDown',
                    text: '🎯 TARGET HIT'
                });
                tradeActive = false;
                activeSL = null;
                activeTarget = null;
                continue;
            }
            // SL Hit (Arrow Up, Red/Orange)
            if (activeSL && c.low <= activeSL) {
                markers.push({
                    time: c.time,
                    position: 'belowBar',
                    color: '#f97316', // Orange SL
                    shape: 'arrowUp',
                    text: '❌ SL HIT'
                });
                tradeActive = false;
                activeSL = null;
                activeTarget = null;
                continue;
            }
        }

        // ENTRY LOGIC (Crossover)
        if (!tradeActive) {
            let triggeredIdx = -1;

            // Check levels L1 to L5 (levels is array [L1, L2, L3, L4, L5])
            for (let j = 0; j < levels.length; j++) {
                const lvl = levels[j];
                if (p.close <= lvl && c.close > lvl) {
                    triggeredIdx = j;
                    break;
                }
            }

            if (triggeredIdx !== -1) {
                const entryLevel = levels[triggeredIdx];
                const targetLvl = (triggeredIdx + 1 < levels.length) ? levels[triggeredIdx + 1] : null;
                const priceStr = c.close.toFixed(1);

                markers.push({
                    time: c.time,
                    position: 'belowBar',
                    color: type === 'CE' ? '#10b981' : '#ef4444', // Match Script: Green CE / Red PE
                    shape: 'arrowUp',
                    text: `${type} @ ${priceStr} (L${triggeredIdx + 1})`
                });

                tradeActive = true;
                activeSL = p.low; // Pine script logic: slPrice = low[1]
                activeTarget = targetLvl;
            }
        }
    }

    return markers;
}

/** Auto-calculate Daily CPR from Intraday Candles */
function oipCalculateDynamicCPR(candles) {
    if (!candles || !candles.length) return null;

    // Group by trading day
    const days = [];
    let currentDayStr = null;
    let currentDayData = null;

    for (const c of candles) {
        const t = c.time || c.date;
        const dateStr = new Date(t * 1000).toDateString();
        if (currentDayStr !== dateStr) {
            if (currentDayData) days.push(currentDayData);
            currentDayStr = dateStr;
            const d = new Date(t * 1000);
            const isoStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
            currentDayData = { date: dateStr, isoDate: isoStr, high: c.high, low: c.low, close: c.close, times: [], closes: [] };
        }
        currentDayData.high = Math.max(currentDayData.high, c.high);
        currentDayData.low = Math.min(currentDayData.low, c.low);
        currentDayData.close = c.close; // continuously updated to last close
        currentDayData.times.push(t);
        currentDayData.closes.push(c.close);
    }
    if (currentDayData) days.push(currentDayData);

    let res = {
        pp: [], tc: [], bc: [],
        prevH: [], prevL: [], r1: [], s1: [],
        r0_5: [], s0_5: [], r0_25: [], s0_25: [],
        cr3: [], cs3: [],
        r1_box: [], s1_box: [], r0_5_box: [], s0_5_box: []
    };

    // Start from i=1 because CPR needs previous day's HLC.
    for (let i = 1; i < days.length; i++) {
        const prev = days[i - 1];
        const curr = days[i];

        // 100% Match with TradingView: Force Official Settlement True High/Low/Close if available.
        let official_H = prev.high;
        let official_L = prev.low;
        let official_C = prev.close;

        if (oipOIData && oipOIData.daily_ohlc && oipOIData.daily_ohlc[prev.isoDate]) {
            const trueOHLC = oipOIData.daily_ohlc[prev.isoDate];
            official_H = trueOHLC.high;
            official_L = trueOHLC.low;
            official_C = trueOHLC.close;
        } else {
            // Fallback approximate settlement if the dict failed or disconnected.
            let numClosingCandles = 6;
            if (prev.times.length >= 70) numClosingCandles = 30;
            else if (prev.times.length < 15) numClosingCandles = 1;
            let sum = 0;
            const cl = prev.closes;
            const take = Math.min(cl.length, numClosingCandles);
            for (let k = cl.length - take; k < cl.length; k++) sum += cl[k];
            official_C = sum / take;
        }

        const pp = (official_H + official_L + official_C) / 3;
        let bc = (official_H + official_L) / 2;
        let tc = (2 * pp) - bc;
        if (bc > tc) { const temp = bc; bc = tc; tc = temp; }

        const range = official_H - official_L;
        const r1 = (pp * 2) - official_L;
        const s1 = (pp * 2) - official_H;
        const r0_5 = (pp + r1) / 2;
        const s0_5 = (pp + s1) / 2;
        const r0_25 = r0_5 + (official_H - r0_5) / 4;
        const s0_25 = s0_5 - (s0_5 - official_L) / 4;

        // Camarilla
        const cr3 = official_C + (range * 1.1) / 4;
        const cs3 = official_C - (range * 1.1) / 4;

        const prevH = official_H;
        const prevL = official_L;

        // Push exact values for every intraday candle in the current day
        for (const t of curr.times) {
            res.pp.push({ time: t, value: pp });
            res.tc.push({ time: t, value: tc });
            res.bc.push({ time: t, value: bc });

            res.prevH.push({ time: t, value: prevH });
            res.prevL.push({ time: t, value: prevL });
            res.r1.push({ time: t, value: r1 });
            res.s1.push({ time: t, value: s1 });

            res.r0_5.push({ time: t, value: r0_5 });
            res.s0_5.push({ time: t, value: s0_5 });
            res.r0_25.push({ time: t, value: r0_25 });
            res.s0_25.push({ time: t, value: s0_25 });

            res.cr3.push({ time: t, value: cr3 });
            res.cs3.push({ time: t, value: cs3 });

            res.r1_box.push({ time: t, open: r1, close: prevH, high: Math.max(r1, prevH), low: Math.min(r1, prevH) });
            res.s1_box.push({ time: t, open: s1, close: prevL, high: Math.max(s1, prevL), low: Math.min(s1, prevL) });

            res.r0_5_box.push({ time: t, open: r0_5, close: r0_25, high: Math.max(r0_5, r0_25), low: Math.min(r0_5, r0_25) });
            res.s0_5_box.push({ time: t, open: s0_5, close: s0_25, high: Math.max(s0_5, s0_25), low: Math.min(s0_5, s0_25) });
        }
    }

    return res;
}

/** Draw Dynamic CPR series on top chart */
function oipDrawCpr(candles) {
    if (!oipOIChart || !oipOISeries) return;

    // Init lightweight chart series if they don't exist
    if (!oipCprSeriesObj) {
        oipCprSeriesObj = {
            // Boxes (Backdrop) - Reduced Alpha for lighter background
            r1_box: oipOIChart.addCandlestickSeries({ upColor: 'rgba(234, 179, 8, 0.05)', downColor: 'rgba(234, 179, 8, 0.05)', borderVisible: false, wickVisible: false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),
            s1_box: oipOIChart.addCandlestickSeries({ upColor: 'rgba(234, 179, 8, 0.05)', downColor: 'rgba(234, 179, 8, 0.05)', borderVisible: false, wickVisible: false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),
            r0_5_box: oipOIChart.addCandlestickSeries({ upColor: 'rgba(239, 68, 68, 0.05)', downColor: 'rgba(239, 68, 68, 0.05)', borderVisible: false, wickVisible: false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),
            s0_5_box: oipOIChart.addCandlestickSeries({ upColor: 'rgba(34, 197, 94, 0.05)', downColor: 'rgba(34, 197, 94, 0.05)', borderVisible: false, wickVisible: false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),

            // Boundary Lines & Labels
            prevH: oipOIChart.addLineSeries({ color: '#000000', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            r1: oipOIChart.addLineSeries({ color: '#eab308', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            r0_25: oipOIChart.addLineSeries({ color: '#ef4444', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            r0_5: oipOIChart.addLineSeries({ color: '#ef4444', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),

            tc: oipOIChart.addLineSeries({ color: '#06b6d4', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            cr3: oipOIChart.addLineSeries({ color: '#a855f7', lineWidth: 2, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            pp: oipOIChart.addLineSeries({ color: '#06b6d4', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            bc: oipOIChart.addLineSeries({ color: '#06b6d4', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),

            s0_5: oipOIChart.addLineSeries({ color: '#22c55e', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            s0_25: oipOIChart.addLineSeries({ color: '#22c55e', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            prevL: oipOIChart.addLineSeries({ color: '#000000', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            cs3: oipOIChart.addLineSeries({ color: '#a855f7', lineWidth: 2, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
            s1: oipOIChart.addLineSeries({ color: '#eab308', lineWidth: 1, lineStyle: 0, title: '', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }),
        };
    }

    const show = document.getElementById('oipShowCpr').checked;

    // Show/hide based on checkbox
    if (!show || !candles || !candles.length) {
        Object.values(oipCprSeriesObj).forEach(s => s.applyOptions({ visible: false }));
        return;
    }

    Object.values(oipCprSeriesObj).forEach(s => s.applyOptions({ visible: true }));

    const cprData = oipCalculateDynamicCPR(candles);
    if (cprData) {
        oipCprSeriesObj.r1_box.setData(cprData.r1_box);
        oipCprSeriesObj.s1_box.setData(cprData.s1_box);
        oipCprSeriesObj.r0_5_box.setData(cprData.r0_5_box);
        oipCprSeriesObj.s0_5_box.setData(cprData.s0_5_box);

        oipCprSeriesObj.prevH.setData(cprData.prevH);
        oipCprSeriesObj.r1.setData(cprData.r1);
        oipCprSeriesObj.r0_25.setData(cprData.r0_25);
        oipCprSeriesObj.r0_5.setData(cprData.r0_5);
        oipCprSeriesObj.tc.setData(cprData.tc);
        oipCprSeriesObj.cr3.setData(cprData.cr3);
        oipCprSeriesObj.pp.setData(cprData.pp);
        oipCprSeriesObj.bc.setData(cprData.bc);
        oipCprSeriesObj.s0_5.setData(cprData.s0_5);
        oipCprSeriesObj.s0_25.setData(cprData.s0_25);
        oipCprSeriesObj.prevL.setData(cprData.prevL);
        oipCprSeriesObj.cs3.setData(cprData.cs3);
        oipCprSeriesObj.s1.setData(cprData.s1);
    }
}

/** Auto-detect Recent/Minor High & Low from Index Candles */
function oipAutoFillHighLow() {
    if (!oipOIData || !oipOIData.candles || !oipOIData.candles.length) {
        console.warn('[OIP] No candle data for H/L detect');
        return;
    }

    const candles = oipOIData.candles;

    // Scale lookback based on timeframe to capture roughly the last hour of action
    let lookback = 12;
    if (oipInterval === 'minute') lookback = 60;
    else if (oipInterval === '2minute') lookback = 30;
    else if (oipInterval === '3minute') lookback = 20;
    else if (oipInterval === '5minute') lookback = 12;
    else if (oipInterval === '15minute') lookback = 4;
    else if (oipInterval === '30minute') lookback = 2;
    else lookback = 10; // Fallback for larger timeframes

    const subset = candles.slice(-lookback);

    if (!subset.length) return;

    let recentHigh = -Infinity;
    let recentLow = Infinity;

    subset.forEach(c => {
        if (c.high > recentHigh) recentHigh = c.high;
        if (c.low < recentLow) recentLow = c.low;
    });

    if (recentHigh === -Infinity || recentLow === Infinity) return;

    // Use exact values for precision
    const spotHigh = Math.round(recentHigh * 100) / 100;
    const spotLow = Math.floor(recentLow * 100) / 100;

    document.getElementById('oipSpotHigh').value = spotHigh;
    document.getElementById('oipSpotLow').value = spotLow;

    // Trigger full fetch with new strikes
    oipLoadCandles(true, false);
}

/** Day-resetting VWAP Calculation */
/** Day-resetting VWAP Calculation */
function oipCalculateVWAP(candles) {
    if (!candles || !candles.length) return [];
    let cumulativePV = 0;
    let cumulativeVol = 0;
    let lastDate = null;

    return candles.map(c => {
        const t = c.time || c.date;
        const date = new Date(t * 1000).toDateString();

        // Reset VWAP at the start of each trading session
        if (date !== lastDate) {
            cumulativePV = 0;
            cumulativeVol = 0;
            lastDate = date;
        }

        const volume = c.volume || 0;
        const typPrice = (c.high + c.low + c.close) / 3;

        // LOGIC FIX: For Index data (like NIFTY Spot), volume is often reported as zero by Zerodha.
        // To make VWAP work for these instruments, we use a minimal proxy volume of 1.
        // This makes the VWAP behave like a session-based Cumulative Moving Average (CMA),
        // which is the standard expected behavior for Spot VWAP.
        const calcVol = (volume > 0) ? volume : 1;

        cumulativePV += typPrice * calcVol;
        cumulativeVol += calcVol;

        // use average.
        const vwapValue = (cumulativeVol > 0) ? (cumulativePV / cumulativeVol) : c.close;

        return {
            time: t,
            value: vwapValue
        };
    });
}
/**
 * Place order via API (Reference: intraday_920.js)
 */
async function oipPlaceOrder(side, action, buttonElement) {
    const strike = (side === 'CE') ? oipCurrentCEStrike : oipCurrentPEStrike;

    if (!strike) {
        showNotification(`No ${side} strike available. Please wait for chart data.`, 'error');
        return;
    }

    const broker = document.getElementById('oipBrokerSelect')?.value || 'kotak_neo';

    // UI Feedback
    buttonElement.disabled = true;
    const originalTitle = buttonElement.title;
    buttonElement.title = "Placing order...";

    try {
        console.log(`[OIP Order] Placing ${action} order: ${side} ${strike} via ${broker}`);

        const response = await fetch('/api/intraday-920/place-order', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content
            },
            body: JSON.stringify({
                symbol: oipSymbol,
                strike: strike,
                option_type: side,
                action: action,
                broker: broker
            })
        });

        const result = await response.json();

        if (result.success) {
            if (result.summary) {
                const successfulCount = result.summary.filter(r => r.result.success).length;
                const totalCount = result.summary.length;
                const msg = `✅ Orders placed on ${successfulCount}/${totalCount} accounts.`;
                showNotification(msg, 'success');

                // If some failed in the summary but overall success is True
                const failed = result.summary.filter(r => !r.result.success);
                if (failed.length > 0) {
                    const failedNames = failed.map(r => r.broker).join(', ');
                    setTimeout(() => showNotification(`⚠️ Failed on: ${failedNames}`, 'error'), 2000);
                }
            } else {
                const msg = `✅ ${action} ${side} ${strike} Successful! ID: ${result.order_id || "N/A"}`;
                showNotification(msg, 'success');
            }
        } else {
            // Check for summary inside result even if success is false
            if (result.summary) {
                const failedStrs = result.summary.map(r => `${r.broker.toUpperCase()}: ${r.result.error || 'Connection Failed'}`);
                showNotification(`❌ Execution Failed on All Accounts:\n${failedStrs.join('\n')}`, 'error');
            } else {
                showNotification(`❌ ${result.error || 'Unknown Error'}`, 'error');
            }
        }
    } catch (error) {
        console.error('[OIP Order] Error:', error);
        showNotification(`Order error: ${error.message}`, 'error');
    } finally {
        buttonElement.disabled = false;
        buttonElement.title = originalTitle;
    }
}
