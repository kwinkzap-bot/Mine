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
            isCombined: true, timeframe: oipInterval, options: { height: 575 },
            onRayDrawn: oipRayDisarmAll,
            reapplyZOrder: () => { if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder(); }
        });
        oipIntrinsicSeries = oipIntrinsicChart.ceSeries || oipIntrinsicChart.series;
        oipIntrinsicPeSeries = oipIntrinsicChart.peSeries;

        // Future-volume histograms — one of the 4 Opt Prem charts, driven by the
        // Opt Indicator popup's "Nifty Vol Fut" / "Banknifty Vol Fut" checkboxes
        // (see oipSyncOptVolumeVisibility in oi_profile.js). Both overlays share
        // one hidden price scale pinned to the bottom of the pane — same recipe
        // as the main chart's pair (see oipAddVolumeSeriesPair).
        const showOptVolumeInt = document.getElementById('oipShowVolumeOpt')?.checked ?? true;
        const showOptBnfVolumeInt = document.getElementById('oipShowBnfVolumeOpt')?.checked ?? false;
        [oipIntrinsicVolumeSeries, oipIntrinsicBnfVolumeSeries] = oipAddVolumeSeriesPair(
            oipIntrinsicChart.chart, 'oipIntVolume', showOptVolumeInt, showOptBnfVolumeInt);

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
            timeframe: oipInterval, options: { height: 575, rightOffset: 5 },
            onRayDrawn: oipRayDisarmAll,
            reapplyZOrder: () => { if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder(); }
        });
        oipCESeries = oipCEChart.series;

        const showOptVolumeCE = document.getElementById('oipShowVolumeOpt')?.checked ?? true;
        const showOptBnfVolumeCE = document.getElementById('oipShowBnfVolumeOpt')?.checked ?? false;
        [oipCEVolumeSeries, oipCEBnfVolumeSeries] = oipAddVolumeSeriesPair(
            oipCEChart.chart, 'oipCEVolume', showOptVolumeCE, showOptBnfVolumeCE);

        // Initialize Individual PE Chart
        oipPEChart = TradingViewChart.create({
            containerId: 'oipPEChart', data: [], type: 'PE',
            timeframe: oipInterval, options: { height: 575, rightOffset: 5 },
            onRayDrawn: oipRayDisarmAll,
            reapplyZOrder: () => { if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder(); }
        });
        oipPESeries = oipPEChart.series;

        const showOptVolumePE = document.getElementById('oipShowVolumeOpt')?.checked ?? true;
        const showOptBnfVolumePE = document.getElementById('oipShowBnfVolumeOpt')?.checked ?? false;
        [oipPEVolumeSeries, oipPEBnfVolumeSeries] = oipAddVolumeSeriesPair(
            oipPEChart.chart, 'oipPEVolume', showOptVolumePE, showOptBnfVolumePE);

        // Fixed 24000 strike / monthly expiry combined chart — independent of
        // the ATM-relative strike selection above; not part of the shared
        // zoom/crosshair sync web (different strike+expiry, own time axis).
        oipFixedChart = TradingViewChart.create({
            containerId: 'oipFixed24000Chart', data: [], type: 'COMBINED',
            isCombined: true, timeframe: oipInterval, options: { height: 575 },
            onRayDrawn: oipRayDisarmAll,
            reapplyZOrder: () => { if (typeof oipApplyOptionZOrder === 'function') oipApplyOptionZOrder(); }
        });
        oipFixedCeSeries = oipFixedChart.ceSeries || oipFixedChart.series;
        oipFixedPeSeries = oipFixedChart.peSeries;

        const showOptVolumeFixed = document.getElementById('oipShowVolumeOpt')?.checked ?? true;
        const showOptBnfVolumeFixed = document.getElementById('oipShowBnfVolumeOpt')?.checked ?? false;
        [oipFixedVolumeSeries, oipFixedBnfVolumeSeries] = oipAddVolumeSeriesPair(
            oipFixedChart.chart, 'oipFixedVolume', showOptVolumeFixed, showOptBnfVolumeFixed);

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
        
        // Active-chart id lives on window (not a local closure var) so charts
        // created later by a different script — Round Strike's main chart, in
        // oi_profile_round_strike.js — can join the same hover-tracked sync web.
        // See oipRSInitCharts() there for its own mouseenter wiring + listener.
        const setActive = (id) => { window._oipActiveChartId = id; };
        ['mouseenter', 'touchstart'].forEach(e => {
            document.getElementById('oipCandleChart')?.addEventListener(e, () => setActive('index'), {passive: true});
            document.getElementById('oipIntrinsicChart')?.addEventListener(e, () => setActive('intrinsic'), {passive: true});
            document.getElementById('oipCEChart')?.addEventListener(e, () => setActive('ce'), {passive: true});
            document.getElementById('oipPEChart')?.addEventListener(e, () => setActive('pe'), {passive: true});
            document.getElementById('oipFixed24000Chart')?.addEventListener(e, () => setActive('fixed'), {passive: true});
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

        window._oipSyncDepth = window._oipSyncDepth || 0;
        // targetCharts may be plain chart instances or {chart, adj} wrapper objects.
        // Wrappers are identified by a numeric `adj` property (never present on LC instances).
        // adj is added to scrollPos when calling scrollToPosition on that target.
        const syncRange = (sourceChart, targetCharts) => {
            if (window._oipSyncDepth > 0) return; // already inside a sync cycle — skip re-entry
            const ts = sourceChart.timeScale();
            const barSpacing = ts.options().barSpacing;
            const scrollPos  = ts.scrollPosition();
            if (!barSpacing) return;
            window._oipSyncDepth++;
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
            window._oipSyncDepth--;
        };

        // Link charts (synchronize panning and zooming).
        // window._oipDataRefreshing is set true in oi_profile.js while setData calls are
        // in flight; callbacks that fire from those setData calls must not trigger a sync
        // (the chart may be mid-update with an auto-fitted or stale range).
        if (oipOIChart && oipIntrinsicChart && oipIntrinsicChart.chart) {
            // "20-group" (default rightOffset=20): OI, Intrinsic — adj=0 between
            // any pair of these. CE Only/PE Only ("5-group", rightOffset=5) need
            // ± _OIP_OPTION_RIGHT_ADJ crossing groups.
            //
            // Two charts are deliberately NOT in this pan/zoom web, for the same
            // reason: it syncs LOGICAL ranges (bar indices), which only line up
            // when every chart shares a bar grid. Fixed 24000 Monthly is always
            // fixed_interval (5-minute), and Round Strike has its own TF dropdown
            // (oipRSInterval) independent of the oipInterval these four follow.
            // Both still join the crosshair-sync web below, which matches on TIME
            // and so works across mismatched bar grids.
            oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipDataRefreshing || window._oipActiveChartId !== 'index' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipOIChart, [
                    oipIntrinsicChart?.chart,
                    { chart: oipCEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ },
                    { chart: oipPEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ }
                ]);
            });

            oipIntrinsicChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipDataRefreshing || window._oipActiveChartId !== 'intrinsic' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipIntrinsicChart.chart, [
                    oipOIChart,
                    { chart: oipCEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ },
                    { chart: oipPEChart?.chart, adj: -_OIP_OPTION_RIGHT_ADJ }
                ]);
            });

            oipCEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipDataRefreshing || window._oipActiveChartId !== 'ce' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(oipCEChart.chart, [
                    { chart: oipOIChart, adj: _OIP_OPTION_RIGHT_ADJ },
                    { chart: oipIntrinsicChart?.chart, adj: _OIP_OPTION_RIGHT_ADJ },
                    oipPEChart?.chart
                ]);
            });

            oipPEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(_range => {
                if (window._oipDataRefreshing || window._oipActiveChartId !== 'pe' || !oipOIChartReady || !oipIntChartReady) return;
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
        // Exposed so Round Strike's own crosshair listener (registered later,
        // from oi_profile_round_strike.js once oipRSChart exists) can reuse
        // the exact same routine — see oipRSInitCharts() in that file.
        window._oipSyncCrosshair = syncCrosshair;

        // oipFixedCeSeries (Fixed 24000 Monthly) and oipRSCESeries (Round
        // Strike) are used as each chart's "anchor" series for crosshair
        // price lookup — same role oipIntrinsicSeries plays for Intrinsic.
        // oipRSChart is declared in oi_profile_round_strike.js (loaded after
        // this file) but these callbacks only run on real mouse movement,
        // well after that script has assigned it.
        if (oipOIChart) {
            oipOIChart.subscribeCrosshairMove(param => {
                if (window._oipActiveChartId !== 'index') return;
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipOIChart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipOIChart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipOIChart, oipPEChart.chart, param, oipPESeries);
                if (oipFixedChart?.chart && oipFixedCeSeries) syncCrosshair(oipOIChart, oipFixedChart.chart, param, oipFixedCeSeries);
                if (oipRSChart?.chart && oipRSCESeries) syncCrosshair(oipOIChart, oipRSChart.chart, param, oipRSCESeries);
            });
        }
        if (oipIntrinsicChart?.chart) {
            oipIntrinsicChart.chart.subscribeCrosshairMove(param => {
                if (window._oipActiveChartId !== 'intrinsic') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipIntrinsicChart.chart, oipOIChart, param, oipOISeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipIntrinsicChart.chart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipIntrinsicChart.chart, oipPEChart.chart, param, oipPESeries);
                if (oipFixedChart?.chart && oipFixedCeSeries) syncCrosshair(oipIntrinsicChart.chart, oipFixedChart.chart, param, oipFixedCeSeries);
                if (oipRSChart?.chart && oipRSCESeries) syncCrosshair(oipIntrinsicChart.chart, oipRSChart.chart, param, oipRSCESeries);
            });
        }
        if (oipCEChart?.chart) {
            oipCEChart.chart.subscribeCrosshairMove(param => {
                if (window._oipActiveChartId !== 'ce') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipCEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipCEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipCEChart.chart, oipPEChart.chart, param, oipPESeries);
                if (oipFixedChart?.chart && oipFixedCeSeries) syncCrosshair(oipCEChart.chart, oipFixedChart.chart, param, oipFixedCeSeries);
                if (oipRSChart?.chart && oipRSCESeries) syncCrosshair(oipCEChart.chart, oipRSChart.chart, param, oipRSCESeries);
            });
        }
        if (oipPEChart?.chart) {
            oipPEChart.chart.subscribeCrosshairMove(param => {
                if (window._oipActiveChartId !== 'pe') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipPEChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipPEChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipPEChart.chart, oipCEChart.chart, param, oipCESeries);
                if (oipFixedChart?.chart && oipFixedCeSeries) syncCrosshair(oipPEChart.chart, oipFixedChart.chart, param, oipFixedCeSeries);
                if (oipRSChart?.chart && oipRSCESeries) syncCrosshair(oipPEChart.chart, oipRSChart.chart, param, oipRSCESeries);
            });
        }
        if (oipFixedChart?.chart) {
            oipFixedChart.chart.subscribeCrosshairMove(param => {
                if (window._oipActiveChartId !== 'fixed') return;
                if (oipOIChart && oipOISeries) syncCrosshair(oipFixedChart.chart, oipOIChart, param, oipOISeries);
                if (oipIntrinsicChart?.chart && oipIntrinsicSeries) syncCrosshair(oipFixedChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
                if (oipCEChart?.chart && oipCESeries) syncCrosshair(oipFixedChart.chart, oipCEChart.chart, param, oipCESeries);
                if (oipPEChart?.chart && oipPESeries) syncCrosshair(oipFixedChart.chart, oipPEChart.chart, param, oipPESeries);
                if (oipRSChart?.chart && oipRSCESeries) syncCrosshair(oipFixedChart.chart, oipRSChart.chart, param, oipRSCESeries);
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
            color: document.getElementById('oipRayColorInp')?.value || '#f33968',
            width: parseInt(document.getElementById('oipRayWidthSel')?.value, 10) || 2,
            lineStyle: parseInt(document.getElementById('oipRayStyleSel')?.value, 10) ?? 1
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
