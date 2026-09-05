'use strict';

// ── Theme config (matches OIP_CHART_THEMES in oi_profile.js) ──────────────
const PCR_CHART_THEMES = {
    'light':  { bg: '#ffffff', text: '#374151', grid: '#f0f0f0' },
    'dark':   { bg: '#111827', text: '#94a3b8', grid: 'rgba(255,255,255,0.06)' },
    'forest': { bg: '#0a1410', text: '#6ba88f', grid: 'rgba(16,185,129,0.06)' },
    'cream':  { bg: '#ffffff', text: '#7c7267', grid: 'rgba(180,83,9,0.05)' },
    'ocean':  { bg: '#ffffff', text: '#475569', grid: 'rgba(2,132,199,0.05)' },
};

const PCR_CR = 1e7;

// ── State ─────────────────────────────────────────────────────────────────
let _pcrChart1 = null, _pcrChart2 = null, _pcrChart3 = null;
let _pcrPriceSeries1 = null, _pcrRatioSeries = null;
let _pcrPriceSeries2 = null, _pcrCESeries = null, _pcrPESeries = null;
let _pcrPriceSeries3 = null, _pcrCEOISeries = null, _pcrPEOISeries = null;

// Expose chart instances globally for vega.js crosshair sync
Object.defineProperties(window, {
    _pcrChart1:      { get: () => _pcrChart1 },
    _pcrChart2:      { get: () => _pcrChart2 },
    _pcrChart3:      { get: () => _pcrChart3 },
    _pcrPriceSeries1:{ get: () => _pcrPriceSeries1 },
    _pcrPriceSeries2:{ get: () => _pcrPriceSeries2 },
    _pcrPriceSeries3:{ get: () => _pcrPriceSeries3 },
});
let _pcrTimer = null;
let _pcrRawData = [];
let _pcrInited = false;
let _pcrActiveChart = null;   // which chart the user is hovering (prevents sync loop)
let _pcrRangeSyncing = false; // re-entrancy guard for time-scale sync
let _pcrDataReady = false;    // blocks range sync until all charts have data loaded

// ── Cleanup (called before each init attempt to remove partial state) ───────
function _pcrCleanup() {
    _pcrDataReady = false;
    try { if (_pcrChart1) _pcrChart1.remove(); } catch (e) {}
    try { if (_pcrChart2) _pcrChart2.remove(); } catch (e) {}
    try { if (_pcrChart3) _pcrChart3.remove(); } catch (e) {}
    _pcrChart1 = _pcrChart2 = _pcrChart3 = null;
    _pcrPriceSeries1 = _pcrRatioSeries = null;
    _pcrPriceSeries2 = _pcrCESeries = _pcrPESeries = null;
    _pcrPriceSeries3 = _pcrCEOISeries = _pcrPEOISeries = null;
    if (typeof _vegaCleanup === 'function') _vegaCleanup();
}

// ── Entry point ────────────────────────────────────────────────────────────
function pcrLoad() {
    if (!_pcrInited) {
        try {
            _pcrInitCharts();
            _pcrInited = true;
        } catch (e) {
            // Clean up whatever was partially created so retry starts fresh
            _pcrCleanup();
            console.warn('[PCR] Chart init failed, retrying in 200ms…', e.message);
            setTimeout(pcrLoad, 200);
            return;
        }
    }
    pcrRefresh();
    if (typeof vegaLoad === 'function') vegaLoad();
}

function pcrRefresh() {
    if (!_pcrChart1 || !_pcrChart2 || !_pcrChart3) { pcrLoad(); return; }
    const symbol = document.getElementById('pcrSymbol')?.value || 'NIFTY';
    const btn = document.getElementById('pcrRefreshBtn');
    if (btn) btn.textContent = '↻ Loading…';
    fetch('/api/pcr/history?symbol=' + encodeURIComponent(symbol))
        .then(r => r.json())
        .then(d => {
            if (!d.success) { console.warn('[PCR]', d.error); return; }
            _pcrRawData = d.data || [];
            _pcrRender(_pcrRawData);
            const upd = document.getElementById('pcrLastUpd');
            if (upd) upd.textContent = 'Updated ' + new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            clearTimeout(_pcrTimer);
            if (isMarketOpen()) _pcrTimer = setTimeout(pcrRefresh, 5000);
            if (typeof vegaRefresh === 'function') vegaRefresh();
        })
        .catch(e => console.error('[PCR] fetch error', e))
        .finally(() => { if (btn) btn.textContent = '↻ Refresh'; });
}

// ── Theme helpers ──────────────────────────────────────────────────────────
function _pcrGetTheme() {
    const name = window.AppTheme.getActiveTheme();
    return PCR_CHART_THEMES[name] || PCR_CHART_THEMES['ocean'];
}

function _pcrApplyTheme(themeName) {
    const cfg = PCR_CHART_THEMES[themeName] || PCR_CHART_THEMES['ocean'];
    const opts = {
        layout: { textColor: cfg.text, background: { type: 'solid', color: cfg.bg } },
        grid: { vertLines: { color: cfg.grid }, horzLines: { color: cfg.grid } },
        timeScale: { textColor: cfg.text },
        rightPriceScale: { textColor: cfg.text },
        leftPriceScale: { textColor: cfg.text },
    };
    try { if (_pcrChart1) _pcrChart1.applyOptions(opts); } catch (e) {}
    try { if (_pcrChart2) _pcrChart2.applyOptions(opts); } catch (e) {}
    try { if (_pcrChart3) _pcrChart3.applyOptions(opts); } catch (e) {}
}

// ── Chart creation ─────────────────────────────────────────────────────────
function _pcrBuildOpts(cfg) {
    return {
        autoSize: true,
        layout: { textColor: cfg.text, background: { type: 'solid', color: cfg.bg } },
        grid:   { vertLines: { color: cfg.grid }, horzLines: { color: cfg.grid } },
        crosshair: {
            mode: 0,
            vertLine: { color: '#9ca3af', style: 3 },
            horzLine: { color: '#9ca3af', style: 3, labelBackgroundColor: '#0969da' },
        },
        localization: {
            locale: 'en-IN',
            timeFormatter: window.lwCrosshairTime,
            timezone: 'Etc/UTC', // timestamps are fake-UTC (IST hours stored as UTC) matching oi_profile.js
        },
        timeScale: {
            timeVisible: true,
            textColor:   '#6b7280',
            borderColor: 'transparent',
            rightOffset: 20,
            barSpacing:  8,
            fixLeftEdge:  false,
            fixRightEdge: false,
            shiftVisibleRangeOnNewBar: false,
        },
        leftPriceScale: {
            visible: true,
            borderColor: 'transparent',
            textColor: cfg.text,
            width: 70,
            scaleMargins: { top: 0.1, bottom: 0.1 },
        },
        rightPriceScale: {
            visible: true,
            borderColor: 'transparent',
            textColor: cfg.text,
            width: 70,
            autoScale: true,
            scaleMargins: { top: 0.1, bottom: 0.1 },
            entireTextOnly: true,
        },
        handleScroll: true,
        handleScale: true,
    };
}

function _pcrInitCharts() {
    const el1 = document.getElementById('pcrChart1');
    const el2 = document.getElementById('pcrChart2');
    const el3 = document.getElementById('pcrChart3');
    if (!el1 || !el2 || !el3) throw new Error('PCR chart containers not found');
    // If the panel was just made visible the browser may not have reflowed yet
    if (!el1.clientWidth) throw new Error('PCR container not laid out yet (width=0)');

    const cfg = _pcrGetTheme();

    // ── Price formatters ──────────────────────────────────────────────────
    // Left scale: underlying price → "23.4K"
    const _fmtPrice = p => p >= 1000 ? (p / 1000).toFixed(1) + 'K' : p.toFixed(0);

    // Right scale chart 1: PCR ratio → "1.12"
    const _fmtPCR = p => p.toFixed(2);

    // Right scale chart 2: OI change (already in Cr after /1e7) → "1.2Cr" or "50L"
    const _fmtOI = v => {
        const sign = v < 0 ? '-' : '';
        const abs  = Math.abs(v);
        if (abs >= 1)    return sign + abs.toFixed(1) + 'Cr';
        if (abs >= 0.01) return sign + (abs * 100).toFixed(0) + 'L';
        return sign + abs.toFixed(2) + 'Cr';
    };

    _pcrChart1 = LightweightCharts.createChart(el1, _pcrBuildOpts(cfg));
    _pcrPriceSeries1 = _pcrChart1.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'left',
        color: '#94a3b8', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false,
        priceFormat: { type: 'custom', formatter: _fmtPrice, minMove: 0.5 },
    });
    _pcrRatioSeries = _pcrChart1.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'right',
        color: '#2962ff', lineWidth: 3,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#2962ff',
        priceFormat: { type: 'custom', formatter: _fmtPCR, minMove: 0.001 },
    });

    _pcrChart2 = LightweightCharts.createChart(el2, _pcrBuildOpts(cfg));
    _pcrPriceSeries2 = _pcrChart2.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'left',
        color: '#94a3b8', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false,
        priceFormat: { type: 'custom', formatter: _fmtPrice, minMove: 0.5 },
    });
    _pcrCESeries = _pcrChart2.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'right',
        color: '#ef4444', lineWidth: 2,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#ef4444',
        priceFormat: { type: 'custom', formatter: _fmtOI, minMove: 0.01 },
    });
    _pcrPESeries = _pcrChart2.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'right',
        color: '#22c55e', lineWidth: 2,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#22c55e',
        priceFormat: { type: 'custom', formatter: _fmtOI, minMove: 0.01 },
    });

    _pcrChart3 = LightweightCharts.createChart(el3, _pcrBuildOpts(cfg));
    _pcrPriceSeries3 = _pcrChart3.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'left',
        color: '#94a3b8', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false,
        priceFormat: { type: 'custom', formatter: _fmtPrice, minMove: 0.5 },
    });
    _pcrCEOISeries = _pcrChart3.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'right',
        color: '#ef4444', lineWidth: 2,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#ef4444',
        priceFormat: { type: 'custom', formatter: _fmtOI, minMove: 0.01 },
    });
    _pcrPEOISeries = _pcrChart3.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'right',
        color: '#22c55e', lineWidth: 2,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#22c55e',
        priceFormat: { type: 'custom', formatter: _fmtOI, minMove: 0.01 },
    });

    // Track hover source to avoid sync feedback loop
    const setActive = id => { _pcrActiveChart = id; };
    ['mouseenter', 'touchstart'].forEach(ev => {
        el1.addEventListener(ev, () => setActive('chart1'), { passive: true });
        el2.addEventListener(ev, () => setActive('chart2'), { passive: true });
        el3.addEventListener(ev, () => setActive('chart3'), { passive: true });
    });

    // Crosshair sync
    _pcrChart1.subscribeCrosshairMove(p => {
        _pcrShowTooltip(1, p);
        if (_pcrActiveChart === 'chart1') {
            _pcrSyncCross(_pcrChart2, p, _pcrPriceSeries2);
            _pcrSyncCross(_pcrChart3, p, _pcrPriceSeries3);
            if (typeof _vegaSyncFromPCR === 'function') _vegaSyncFromPCR(p);
        }
    });
    _pcrChart2.subscribeCrosshairMove(p => {
        _pcrShowTooltip(2, p);
        if (_pcrActiveChart === 'chart2') {
            _pcrSyncCross(_pcrChart1, p, _pcrPriceSeries1);
            _pcrSyncCross(_pcrChart3, p, _pcrPriceSeries3);
            if (typeof _vegaSyncFromPCR === 'function') _vegaSyncFromPCR(p);
        }
    });
    _pcrChart3.subscribeCrosshairMove(p => {
        _pcrShowTooltip(3, p);
        if (_pcrActiveChart === 'chart3') {
            _pcrSyncCross(_pcrChart1, p, _pcrPriceSeries1);
            _pcrSyncCross(_pcrChart2, p, _pcrPriceSeries2);
            if (typeof _vegaSyncFromPCR === 'function') _vegaSyncFromPCR(p);
        }
    });

    // Time-scale sync (source-guarded + re-entrancy guard)
    const syncRange = (range, targetChart) => {
        if (!range || range.from == null || range.to == null || !targetChart) return;
        try { targetChart.timeScale().setVisibleLogicalRange(range); } catch (e) {}
    };
    _pcrChart1.timeScale().subscribeVisibleLogicalRangeChange(r => {
        if (!_pcrDataReady || _pcrRangeSyncing || _pcrActiveChart !== 'chart1') return;
        _pcrRangeSyncing = true;
        syncRange(r, _pcrChart2);
        syncRange(r, _pcrChart3);
        if (typeof vegaSyncRange === 'function') vegaSyncRange(r);
        _pcrRangeSyncing = false;
    });
    _pcrChart2.timeScale().subscribeVisibleLogicalRangeChange(r => {
        if (!_pcrDataReady || _pcrRangeSyncing || _pcrActiveChart !== 'chart2') return;
        _pcrRangeSyncing = true;
        syncRange(r, _pcrChart1);
        syncRange(r, _pcrChart3);
        if (typeof vegaSyncRange === 'function') vegaSyncRange(r);
        _pcrRangeSyncing = false;
    });
    _pcrChart3.timeScale().subscribeVisibleLogicalRangeChange(r => {
        if (!_pcrDataReady || _pcrRangeSyncing || _pcrActiveChart !== 'chart3') return;
        _pcrRangeSyncing = true;
        syncRange(r, _pcrChart1);
        syncRange(r, _pcrChart2);
        if (typeof vegaSyncRange === 'function') vegaSyncRange(r);
        _pcrRangeSyncing = false;
    });

    // Symbol dropdown
    document.getElementById('pcrSymbol')?.addEventListener('change', pcrRefresh);

    // Listen for global theme changes
    window.addEventListener('themechanged', e => {
        if (e.detail && e.detail.theme) _pcrApplyTheme(e.detail.theme);
    });
}

// ── Render data into charts ────────────────────────────────────────────────
function _pcrRender(data) {
    if (!data.length || !_pcrPriceSeries1) return;
    // Deduplicate by timestamp — LC throws "Value is null" on duplicate times.
    const seen = new Map();
    data.forEach(d => seen.set(d.time, d));
    data = [...seen.values()].sort((a, b) => a.time - b.time);

    const priceData  = data.map(d => ({ time: d.time, value: d.price }));
    const pcrData    = data.map(d => ({ time: d.time, value: d.pcr }));
    const ceData     = data.map(d => ({ time: d.time, value: +(d.ce_change / PCR_CR).toFixed(2) }));
    const peData     = data.map(d => ({ time: d.time, value: +(d.pe_change / PCR_CR).toFixed(2) }));
    const ceOIData   = data.map(d => ({ time: d.time, value: +((d.ce_oi || 0) / PCR_CR).toFixed(2) }));
    const peOIData   = data.map(d => ({ time: d.time, value: +((d.pe_oi || 0) / PCR_CR).toFixed(2) }));

    // RAF-1: deferred past LC's init RAF so left price scale is ready.
    // _pcrDataReady=false blocks range-sync while setData fires internal
    // subscribeVisibleLogicalRangeChange callbacks before chart2 has its data.
    _pcrDataReady = false;
    requestAnimationFrame(() => {
        if (!_pcrPriceSeries1) return;
        try {
            _pcrPriceSeries1.setData(priceData);
            _pcrRatioSeries.setData(pcrData);
            _pcrPriceSeries2.setData(priceData);
            _pcrCESeries.setData(ceData);
            _pcrPESeries.setData(peData);
            _pcrPriceSeries3.setData(priceData);
            _pcrCEOISeries.setData(ceOIData);
            _pcrPEOISeries.setData(peOIData);
        } catch (e) {
            console.error('[PCR] setData error', e);
            return;
        }
        // RAF-2: fitContent fires after LC's data-processing RAFs (registered
        // above) so all charts have a valid data model before sync can fire.
        requestAnimationFrame(() => {
            _pcrDataReady = true;
            try { if (_pcrChart1) _pcrChart1.timeScale().fitContent(); } catch (e) {}
            try { if (_pcrChart2) _pcrChart2.timeScale().fitContent(); } catch (e) {}
            try { if (_pcrChart3) _pcrChart3.timeScale().fitContent(); } catch (e) {}
            const last = data[data.length - 1];
            const lv = document.getElementById('pcrLiveVal');
            if (lv) lv.textContent = 'PCR: ' + last.pcr;
            const ce = document.getElementById('pcrCEVal');
            if (ce) ce.textContent = (last.ce_change / PCR_CR).toFixed(2) + 'Cr';
            const pe = document.getElementById('pcrPEVal');
            if (pe) pe.textContent = (last.pe_change / PCR_CR).toFixed(2) + 'Cr';
            const ceOI = document.getElementById('pcrCEOIVal');
            if (ceOI) ceOI.textContent = ((last.ce_oi || 0) / PCR_CR).toFixed(2) + 'Cr';
            const peOI = document.getElementById('pcrPEOIVal');
            if (peOI) peOI.textContent = ((last.pe_oi || 0) / PCR_CR).toFixed(2) + 'Cr';
        });
    });
}

// ── Crosshair sync helper ─────────────────────────────────────────────────
function _pcrSyncCross(tgtChart, param, tgtSeries) {
    if (!tgtChart || !tgtSeries) return;
    try {
        if (!param || !param.point || param.time == null) {
            tgtChart.clearCrosshairPosition();
            return;
        }
        const price = tgtSeries.coordinateToPrice(param.point.y);
        if (price != null) tgtChart.setCrosshairPosition(price, param.time, tgtSeries);
        else tgtChart.clearCrosshairPosition();
    } catch (e) {}
}

// ── Tooltip ───────────────────────────────────────────────────────────────
function _pcrShowTooltip(chartNum, param) {
    const tt = document.getElementById('pcrTooltip' + chartNum);
    if (!tt) return;
    if (!param || !param.time) { tt.classList.add('hidden'); return; }
    const d = _pcrRawData.find(x => x.time === param.time);
    if (!d) { tt.classList.add('hidden'); return; }
    const dt = new Date(d.time * 1000);
    const h = dt.getUTCHours();
    const m = String(dt.getUTCMinutes()).padStart(2, '0');
    const ampm = h >= 12 ? 'PM' : 'AM';
    const h12 = h % 12 || 12;
    const timeStr = h12 + ':' + m + ' ' + ampm;
    if (chartNum === 1) {
        tt.innerHTML = '<b>' + timeStr + '</b> &nbsp; Fut: <b>' + d.price.toFixed(2) + '</b> &nbsp; PCR: <b style="color:#2962ff">' + d.pcr + '</b>';
    } else if (chartNum === 2) {
        tt.innerHTML = '<b>' + timeStr + '</b> &nbsp; Fut: <b>' + d.price.toFixed(2) + '</b> &nbsp; CE: <b style="color:#ef4444">' + (d.ce_change / PCR_CR).toFixed(2) + 'Cr</b> &nbsp; PE: <b style="color:#22c55e">' + (d.pe_change / PCR_CR).toFixed(2) + 'Cr</b>';
    } else {
        tt.innerHTML = '<b>' + timeStr + '</b> &nbsp; Fut: <b>' + d.price.toFixed(2) + '</b> &nbsp; CE OI: <b style="color:#ef4444">' + ((d.ce_oi || 0) / PCR_CR).toFixed(2) + 'Cr</b> &nbsp; PE OI: <b style="color:#22c55e">' + ((d.pe_oi || 0) / PCR_CR).toFixed(2) + 'Cr</b>';
    }
    tt.classList.remove('hidden');
}
