window.oipInitSecondaryCharts = function() {
    const elInt = document.getElementById('oipIntrinsicChart');

    function syncCrosshair(sourceChart, targetChart, param, targetSeries) {
        if (!targetChart || !targetSeries) return;
        try {
            const isValid = param && param.point && param.time != null;
            if (!isValid) {
                targetChart.clearCrosshairPosition();
            } else {
                const price = targetSeries.coordinateToPrice(param.point.y);
                if (price != null) {
                    targetChart.setCrosshairPosition(price, param.time, targetSeries);
                } else {
                    targetChart.clearCrosshairPosition();
                }
            }
        } catch(e) {}
    }

    if (elInt && typeof TradingViewChart !== 'undefined') {
        oipIntrinsicChart = TradingViewChart.create({
            containerId: 'oipIntrinsicChart', data: [], type: 'COMBINED',
            isCombined: true, timeframe: oipInterval, options: { height: 310 }
        });
        oipIntrinsicSeries = oipIntrinsicChart.ceSeries || oipIntrinsicChart.series;
        oipIntrinsicPeSeries = oipIntrinsicChart.peSeries;
        const showV = oipElems.showVwapInt?.checked;
        oipVwapIntSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#1b9981', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });
        oipVwapIntPeSeries = oipIntrinsicChart.chart.addLineSeries({
            color: '#8b5cf6', lineWidth: 1, title: '', visible: showV,
            priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null
        });

        // Initialize Individual CE Chart
        oipCEChart = TradingViewChart.create({
            containerId: 'oipCEChart', data: [], type: 'CE',
            timeframe: oipInterval, options: { height: 310 }
        });
        oipCESeries = oipCEChart.series;

        // Initialize Individual PE Chart
        oipPEChart = TradingViewChart.create({
            containerId: 'oipPEChart', data: [], type: 'PE',
            timeframe: oipInterval, options: { height: 310 }
        });
        oipPESeries = oipPEChart.series;

        const showEma9 = oipElems.showEma9?.checked ?? false;
        const showEma20 = oipElems.showEma20?.checked ?? false;
        const showEma50 = oipElems.showEma50?.checked ?? false;

        oipCEEma9Series = oipCEChart.chart.addLineSeries({ color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma9, autoscaleInfoProvider: () => null });
        oipCEEma20Series = oipCEChart.chart.addLineSeries({ color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma20, autoscaleInfoProvider: () => null });
        oipCEEma50Series = oipCEChart.chart.addLineSeries({ color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma50, autoscaleInfoProvider: () => null });

        oipPEEma9Series = oipPEChart.chart.addLineSeries({ color: '#22c55e', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma9, autoscaleInfoProvider: () => null });
        oipPEEma20Series = oipPEChart.chart.addLineSeries({ color: '#f97316', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma20, autoscaleInfoProvider: () => null });
        oipPEEma50Series = oipPEChart.chart.addLineSeries({ color: '#ef4444', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible: showEma50, autoscaleInfoProvider: () => null });

        oipInitPremiumSeries();
        
        let activeChartId = null;
        const setActive = (id) => activeChartId = id;
        ['mouseenter', 'touchstart'].forEach(e => {
            document.getElementById('oipCandleChart')?.addEventListener(e, () => setActive('index'), {passive: true});
            document.getElementById('oipIntrinsicChart')?.addEventListener(e, () => setActive('intrinsic'), {passive: true});
            document.getElementById('oipCEChart')?.addEventListener(e, () => setActive('ce'), {passive: true});
            document.getElementById('oipPEChart')?.addEventListener(e, () => setActive('pe'), {passive: true});
        });

        const syncRange = (range, targetCharts) => {
            if (!range || range.from == null || range.to == null) return;
            targetCharts.forEach(t => {
                if (!t) return;
                try { t.timeScale().setVisibleLogicalRange(range); } catch(e) {}
            });
        };

        // Link charts (synchronize panning and zooming)
        if (oipOIChart && oipIntrinsicChart && oipIntrinsicChart.chart) {
            oipOIChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
                if (activeChartId !== 'index' || !oipOIChartReady || !oipIntChartReady) return; 
                syncRange(range, [oipIntrinsicChart?.chart, oipCEChart?.chart, oipPEChart?.chart]);
            });
            
            oipIntrinsicChart.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
                if (activeChartId !== 'intrinsic' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(range, [oipOIChart, oipCEChart?.chart, oipPEChart?.chart]);
            });

            oipCEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
                if (activeChartId !== 'ce' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(range, [oipOIChart, oipIntrinsicChart?.chart, oipPEChart?.chart]);
            });

            oipPEChart.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
                if (activeChartId !== 'pe' || !oipOIChartReady || !oipIntChartReady) return;
                syncRange(range, [oipOIChart, oipIntrinsicChart?.chart, oipCEChart?.chart]);
            });
        }

        // --- Finalize Synchronization (All charts ready) ---
        // Add ResizeObservers for all secondary charts
        const syncSize = (chart, wrap) => {
            if (!chart || !wrap) return;
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
    }
};
