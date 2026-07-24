// Disarms the horizontal-ray tool on all 4 Opt Prem charts (Intrinsic/Combined,
// CE Only, PE Only, Fixed 24000) and resets the toolbar button — called after a
// ray is drawn on any one of them, since arming is shared across all four.
function oipRayDisarmAll() {
    [oipIntrinsicChart, oipCEChart, oipPEChart, oipFixedChart].forEach(c => {
        if (c && c.setRayMode) c.setRayMode(false);
    });
    document.getElementById('oipRayToolBtn')?.classList.remove('oip-btn--armed');
    document.getElementById('oipRayOptionsPopup')?.classList.add('hidden');
}

window.oipInitSecondaryCharts = function() {
    const elInt = document.getElementById('oipIntrinsicChart');

    function syncCrosshair(sourceChart, targetChart, param, targetSeries) {
        if (!targetChart || !targetSeries) return;
        try {
            const isValid = param && param.point && param.time != null;
            if (!isValid) {
                // Defer past LC's init RAF — clearCrosshairPosition triggers an
                // async render RAF that crashes if the chart isn't yet initialized.
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

    if (elInt && typeof TradingViewChart !== 'undefined') {
        oipIntrinsicChart = TradingViewChart.create({
            containerId: 'oipIntrinsicChart', data: [], type: 'COMBINED',
            isCombined: true, timeframe: oipInterval, options: { height: 375 },
            onRayDrawn: oipRayDisarmAll,
            reapplyZOrder: () => { if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder(); }
        });
        oipIntrinsicSeries = oipIntrinsicChart.ceSeries || oipIntrinsicChart.series;
        oipIntrinsicPeSeries = oipIntrinsicChart.peSeries;

        // Plain VWAP (green/purple) + CVWAP/PVWAP/3-AVG_VWAP on the Options Premium
        // (Combined) chart — one of the 3 "option charts" controlled by the Opt
        // Indicator popup's own "VWAP" checkbox (see oipSyncVwapVisibility). Both
        // sets share that single checkbox so "VWAP" means the same thing to the user.
        const showOptVwapInt = document.getElementById('oipShowVwapOpt')?.checked ?? false;
        oipVwapIntSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#1b9981', lineWidth: 1, title: '', visible: showOptVwapInt,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipVwapIntPeSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#8b5cf6', lineWidth: 1, title: '', visible: showOptVwapInt,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipCvwapIntSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#3b82f6', lineWidth: 1, title: '', visible: showOptVwapInt,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipCvwapIntPeSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#60a5fa', lineWidth: 1, title: '', visible: showOptVwapInt,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipPvwapIntSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#fdba74', lineWidth: 1, title: '', visible: showOptVwapInt,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipPvwapIntPeSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#fdba74', lineWidth: 1, title: '', visible: showOptVwapInt,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipAvg3VwapIntSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#ef4444', lineWidth: 1, title: '', visible: showOptVwapInt,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipAvg3VwapIntPeSeries = oipIntrinsicChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#f87171', lineWidth: 1, title: '', visible: showOptVwapInt,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });

        // Initialize Individual CE Chart
        oipCEChart = TradingViewChart.create({
            containerId: 'oipCEChart', data: [], type: 'CE',
            timeframe: oipInterval, options: { height: 375, rightOffset: 5 },
            onRayDrawn: oipRayDisarmAll,
            reapplyZOrder: () => { if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder(); }
        });
        oipCESeries = oipCEChart.series;

        // Initialize Individual PE Chart
        oipPEChart = TradingViewChart.create({
            containerId: 'oipPEChart', data: [], type: 'PE',
            timeframe: oipInterval, options: { height: 375, rightOffset: 5 },
            onRayDrawn: oipRayDisarmAll,
            reapplyZOrder: () => { if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder(); }
        });
        oipPESeries = oipPEChart.series;

        // Fixed 24000 strike / monthly expiry combined chart — independent of
        // the ATM-relative strike selection above; not part of the shared
        // zoom/crosshair sync web (different strike+expiry, own time axis).
        oipFixedChart = TradingViewChart.create({
            containerId: 'oipFixed24000Chart', data: [], type: 'COMBINED',
            isCombined: true, timeframe: oipInterval, options: { height: 375 },
            onRayDrawn: oipRayDisarmAll,
            reapplyZOrder: () => { if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder(); }
        });
        oipFixedCeSeries = oipFixedChart.ceSeries || oipFixedChart.series;
        oipFixedPeSeries = oipFixedChart.peSeries;

        // Previous-day reference lines: CE (H+L)/2, PE (H+L)/2, (CE close + PE close)/2 —
        // all flat lines using the PRIOR trading day's values, drawn across the current session.
        // title + lastValueVisible label each line with its name and current value.
        oipFixedCeHL2Series = oipFixedChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#16a34a', lineWidth: 1, title: 'CE Avg',
            priceLineVisible: false, lastValueVisible: true, autoscaleInfoProvider: () => null
        });
        oipFixedPeHL2Series = oipFixedChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#7c3aed', lineWidth: 1, title: 'PE Avg',
            priceLineVisible: false, lastValueVisible: true, autoscaleInfoProvider: () => null
        });
        oipFixedCloseAvgSeries = oipFixedChart.chart.addSeries(LightweightCharts.LineSeries, {
            color: '#000000', lineWidth: 1, title: 'CE & PE Avg',
            priceLineVisible: false, lastValueVisible: true, autoscaleInfoProvider: () => null
        });

        // CE Only / PE Only EMA visibility is controlled independently by the
        // Opt Indicator popup's own EMA checkboxes (see oipUpdateOptEmaVisibility
        // in oi_indicators.js) — NOT the main popup's EMA9/20/50, which only
        // apply to the main chart now.
        const showOptEma9  = document.getElementById('oipShowEma9Opt')?.checked  ?? false;
        const showOptEma20 = document.getElementById('oipShowEma20Opt')?.checked ?? false;
        const showOptEma50 = document.getElementById('oipShowEma50Opt')?.checked ?? false;

        oipCEEma9Series = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showOptEma9, autoscaleInfoProvider: () => null });
        oipCEEma20Series = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showOptEma20, autoscaleInfoProvider: () => null });
        oipCEEma50Series = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showOptEma50, autoscaleInfoProvider: () => null });

        oipPEEma9Series = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showOptEma9, autoscaleInfoProvider: () => null });
        oipPEEma20Series = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showOptEma20, autoscaleInfoProvider: () => null });
        oipPEEma50Series = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showOptEma50, autoscaleInfoProvider: () => null });

        // CVWAP (current-session) / PVWAP (previous-session) / 3-AVG_VWAP on the CE Only & PE
        // Only charts — visibility controlled independently by the Opt Indicator popup's
        // own "VWAP" checkbox, not the main popup's CVWAP/PVWAP/3-AVG_VWAP sub-states.
        const showOptVwap = document.getElementById('oipShowVwapOpt')?.checked ?? false;
        oipCECvwapSeries = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#3b82f6', lineWidth: 1, title: '', visible: showOptVwap, priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null });
        oipCEPvwapSeries = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#fdba74', lineWidth: 1, title: '', visible: showOptVwap, priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null });
        oipCEAvg3VwapSeries = oipCEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, title: '', visible: showOptVwap, priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null });
        oipPECvwapSeries = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#3b82f6', lineWidth: 1, title: '', visible: showOptVwap, priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null });
        oipPEPvwapSeries = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#fdba74', lineWidth: 1, title: '', visible: showOptVwap, priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null });
        oipPEAvg3VwapSeries = oipPEChart.chart.addSeries(LightweightCharts.LineSeries, { color: '#ef4444', lineWidth: 1, title: '', visible: showOptVwap, priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null });

        oipInitPremiumSeries();
        
        let activeChartId = null;
        const setActive = (id) => activeChartId = id;
        ['mouseenter', 'touchstart'].forEach(e => {
            document.getElementById('oipCandleChart')?.addEventListener(e, () => setActive('index'), {passive: true});
            document.getElementById('oipIntrinsicChart')?.addEventListener(e, () => setActive('intrinsic'), {passive: true});
            document.getElementById('oipCEChart')?.addEventListener(e, () => setActive('ce'), {passive: true});
            document.getElementById('oipPEChart')?.addEventListener(e, () => setActive('pe'), {passive: true});
        });

        // Sync zoom level (barSpacing) and scroll position across charts.
        // Using setVisibleRange would pin from/to as hard edges, compressing the chart to
        // exactly that range and eliminating the rightOffset gap between the last candle and
        // the Y-axis. barSpacing + scrollToPosition preserves that gap naturally.
        // _oipSyncDepth is a re-entrancy counter: applyOptions/scrollToPosition fires
        // subscribeVisibleLogicalRangeChange on the target synchronously; the depth check
        // prevents those nested callbacks from triggering a reverse sync.
        // CE-only and PE-only charts have rightOffset=5 vs OI/intrinsic rightOffset=20.
        // scrollToPosition() bypasses rightOffset, so we apply this correction whenever
        // syncing scroll position between the two groups.
        const _OIP_OPTION_RIGHT_ADJ = 15;

        let _oipSyncDepth = 0;
        // targetCharts may be plain chart instances or {chart, adj} wrapper objects.
        // Wrappers are identified by a numeric `adj` property (never present on LC instances).
        // adj is added to scrollPos when calling scrollToPosition on that target.
        const syncRange = (sourceChart, targetCharts) => {
            if (_oipSyncDepth > 0) return; // already inside a sync cycle — skip re-entry
            const ts = sourceChart.timeScale();
            const barSpacing = ts.options().barSpacing;
            const scrollPos  = ts.scrollPosition();
            if (!barSpacing) return;
            _oipSyncDepth++;
            targetCharts.forEach(item => {
                const isWrapped = item !== null && typeof item === 'object' && typeof item.adj === 'number';
                const t   = isWrapped ? item.chart : item;
                const adj = isWrapped ? item.adj   : 0;
                // Guard: must be a real chart instance with a timeScale method
                if (!t || typeof t.timeScale !== 'function') return;
                // Copy both barSpacing (zoom level) and scrollPosition (right-edge offset).
                // scrollPosition alone is insufficient: the same value means a different pixel
                // offset when barSpacings differ, causing visible drift between charts.
                // setVisibleRange is intentionally avoided — it pins hard edges and eliminates
                // the rightOffset gap between the last candle and the Y-axis.
                try {
                    t.timeScale().applyOptions({ barSpacing });
                    t.timeScale().scrollToPosition(scrollPos + adj, false);
                } catch(e) {}
            });
            _oipSyncDepth--;
        };

        // Link charts (synchronize panning and zooming).
        // window._oipDataRefreshing is set true in oi_profile.js while setData calls are
        // in flight; callbacks that fire from those setData calls must not trigger a sync
        // (the chart may be mid-update with an auto-fitted or stale range).
        if (oipOIChart && oipIntrinsicChart && oipIntrinsicChart.chart) {
            oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipDataRefreshing || activeChartId !== 'index' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipOIChart, [
                    oipIntrinsicChart?.chart,
                    { chart: oipCEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ },
                    { chart: oipPEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ }
                ]);
            });

            oipIntrinsicChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipDataRefreshing || activeChartId !== 'intrinsic' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipIntrinsicChart.chart, [
                    oipOIChart,
                    { chart: oipCEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ },
                    { chart: oipPEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ }
                ]);
            });

            oipCEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipDataRefreshing || activeChartId !== 'ce' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipCEChart.chart, [
                    { chart: oipOIChart, adj: _OIP_OPTION_RIGHT_ADJ },
                    { chart: oipIntrinsicChart?.chart, adj: _OIP_OPTION_RIGHT_ADJ },
                    oipPEChart?.chart
                ]);
            });

            oipPEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipDataRefreshing || activeChartId !== 'pe' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipPEChart.chart, [
                    { chart: oipOIChart, adj: _OIP_OPTION_RIGHT_ADJ },
                    { chart: oipIntrinsicChart?.chart, adj: _OIP_OPTION_RIGHT_ADJ },
                    oipCEChart?.chart
                ]);
            });
        }

        // --- Finalize Synchronization (All charts ready) ---
        // Add ResizeObservers for all secondary charts
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
        if (oipFixedChart?.chart) {
            const wrap = document.getElementById('oipFixed24000ChartWrap');
            if (wrap) new ResizeObserver(() => syncSize(oipFixedChart.chart, wrap)).observe(wrap);
        }
        if (oipOIChart) {
            oipOIChart.subscribeCrosshairMove(param => {
                if (activeChartId !== 'index') return;
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipOIChart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipOIChart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipOIChart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipIntrinsicChart?.chart) {
            oipIntrinsicChart.chart.subscribeCrosshairMove(param => {
                if (activeChartId !== 'intrinsic') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipIntrinsicChart.chart, oipOIChart, param, oipOISeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipIntrinsicChart.chart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipIntrinsicChart.chart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipCEChart?.chart) {
            oipCEChart.chart.subscribeCrosshairMove(param => {
                if (activeChartId !== 'ce') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipCEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipCEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipCEChart.chart, oipPEChart.chart, param, oipPESeries);
            });
        }
        if (oipPEChart?.chart) {
            oipPEChart.chart.subscribeCrosshairMove(param => {
                if (activeChartId !== 'pe') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipPEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipPEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipPEChart.chart, oipCEChart.chart, param, oipCESeries);
            });
        }

        // --- Horizontal Ray drawing tool toolbar wiring ---
        // Arming is shared across all 4 Opt Prem charts: click "Ray" once to
        // arm the tool AND open the style popup below the button, then click
        // whichever chart you want the ray on — it auto-disarms and closes
        // the popup after the first ray is drawn (see oipRayDisarmAll, called
        // via onRayDrawn above). Color/width/style pickers set the look of
        // the NEXT ray; changing them while armed re-applies live so the
        // in-progress ray reflects the new choice, without touching rays
        // already drawn.
        const oipRayChartList = [oipIntrinsicChart, oipCEChart, oipPEChart, oipFixedChart].filter(c => c && c.setRayMode);
        const oipRayBtn = document.getElementById('oipRayToolBtn');
        const oipRayClearBtn = document.getElementById('oipRayClearBtn');
        const oipRayPopup = document.getElementById('oipRayOptionsPopup');
        const oipRayStyleFromPickers = () => ({
            color: document.getElementById('oipRayColorInp')?.value || '#f59e0b',
            width: parseInt(document.getElementById('oipRayWidthSel')?.value, 10) || 2,
            lineStyle: parseInt(document.getElementById('oipRayStyleSel')?.value, 10) ?? 2
        });
        if (oipRayBtn) {
            oipRayBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const willArm = !oipRayBtn.classList.contains('oip-btn--armed');
                const style = willArm ? oipRayStyleFromPickers() : undefined;
                oipRayChartList.forEach(c => c.setRayMode(willArm, style));
                oipRayBtn.classList.toggle('oip-btn--armed', willArm);
                oipRayPopup?.classList.toggle('hidden', !willArm);
            });
        }
        // Live-restyle the armed (not-yet-placed) ray as the pickers change.
        if (oipRayPopup) {
            oipRayPopup.addEventListener('change', () => {
                if (oipRayBtn?.classList.contains('oip-btn--armed')) {
                    oipRayChartList.forEach(c => c.setRayMode(true, oipRayStyleFromPickers()));
                }
            });
        }
        // Clicking outside the popup/button while armed cancels ray mode
        // (matches the Indicators popup's outside-click-to-close behavior).
        document.addEventListener('click', (e) => {
            if (!oipRayBtn?.classList.contains('oip-btn--armed')) return;
            if (oipRayPopup?.contains(e.target) || e.target === oipRayBtn || oipRayBtn.contains(e.target)) return;
            oipRayDisarmAll();
        });
        if (oipRayClearBtn) {
            oipRayClearBtn.addEventListener('click', () => {
                oipRayChartList.forEach(c => c.clearRays());
            });
        }
    }
};
