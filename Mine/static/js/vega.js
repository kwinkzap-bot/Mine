'use strict';

// ── State ─────────────────────────────────────────────────────────────────
let _vegaChart = null;
let _vegaPriceSeries = null;
let _vegaCallSeries = null;
let _vegaPutSeries = null;
let _vegaRawData = [];
let _vegaInited = false;
let _vegaRangeSyncing = false;

// ── Cleanup ────────────────────────────────────────────────────────────────
function _vegaCleanup() {
    try { if (_vegaChart) _vegaChart.remove(); } catch (e) {}
    _vegaChart = null;
    _vegaPriceSeries = _vegaCallSeries = _vegaPutSeries = null;
    _vegaInited = false;
}

// ── Entry point (called from pcrLoad) ─────────────────────────────────────
function vegaLoad() {
    if (!_vegaInited) {
        try {
            _vegaInitChart();
            _vegaInited = true;
        } catch (e) {
            _vegaCleanup();
            console.warn('[Vega] Chart init failed, retrying in 200ms…', e.message);
            setTimeout(vegaLoad, 200);
            return;
        }
    }
    vegaRefresh();
}

function vegaRefresh() {
    if (!_vegaChart) { vegaLoad(); return; }
    const symbol = document.getElementById('pcrSymbol')?.value || 'NIFTY';
    fetch('/api/vega/history?symbol=' + encodeURIComponent(symbol))
        .then(r => r.json())
        .then(d => {
            if (!d.success) { console.warn('[Vega]', d.error); return; }
            _vegaRawData = d.data || [];
            _vegaRender(_vegaRawData);
        })
        .catch(e => console.error('[Vega] fetch error', e));
}

// ── Chart creation ─────────────────────────────────────────────────────────
function _vegaGetTheme() {
    const name = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || 'ocean';
    const themes = {
        light:  { bg: '#ffffff', text: '#374151', grid: '#f0f0f0' },
        dark:   { bg: '#111827', text: '#94a3b8', grid: 'rgba(255,255,255,0.06)' },
        forest: { bg: '#0a1410', text: '#6ba88f', grid: 'rgba(16,185,129,0.06)' },
        cream:  { bg: '#ffffff', text: '#7c7267', grid: 'rgba(180,83,9,0.05)' },
        ocean:  { bg: '#ffffff', text: '#475569', grid: 'rgba(2,132,199,0.05)' },
    };
    return themes[name] || themes.ocean;
}

function _vegaApplyTheme(name) {
    if (!_vegaChart) return;
    const themes = {
        light:  { bg: '#ffffff', text: '#374151', grid: '#f0f0f0' },
        dark:   { bg: '#111827', text: '#94a3b8', grid: 'rgba(255,255,255,0.06)' },
        forest: { bg: '#0a1410', text: '#6ba88f', grid: 'rgba(16,185,129,0.06)' },
        cream:  { bg: '#ffffff', text: '#7c7267', grid: 'rgba(180,83,9,0.05)' },
        ocean:  { bg: '#ffffff', text: '#475569', grid: 'rgba(2,132,199,0.05)' },
    };
    const cfg = themes[name] || themes.ocean;
    try {
        _vegaChart.applyOptions({
            layout: { textColor: cfg.text, background: { type: 'solid', color: cfg.bg } },
            grid: { vertLines: { color: cfg.grid }, horzLines: { color: cfg.grid } },
            timeScale: { textColor: cfg.text },
            rightPriceScale: { textColor: cfg.text },
            leftPriceScale: { textColor: cfg.text },
        });
    } catch (e) {}
}

function _vegaInitChart() {
    const el = document.getElementById('vegaChart');
    if (!el) throw new Error('vegaChart container not found');
    if (!el.clientWidth) throw new Error('vegaChart not laid out yet (width=0)');

    const cfg = _vegaGetTheme();
    const _fmtPrice = p => p >= 1000 ? (p / 1000).toFixed(1) + 'K' : p.toFixed(0);
    const _fmtVega = v => {
        const sign = v < 0 ? '-' : '';
        const abs = Math.abs(v);
        return sign + abs.toFixed(2);
    };

    _vegaChart = LightweightCharts.createChart(el, {
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
            timeFormatter: t => {
                const d = new Date(t * 1000);
                return String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0');
            },
            timezone: 'Etc/UTC',
        },
        timeScale: {
            timeVisible: true,
            textColor:   '#6b7280',
            borderColor: 'transparent',
            rightOffset: 20,
            barSpacing:  8,
            fixLeftEdge: false,
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
            scaleMargins: { top: 0.15, bottom: 0.05 },
        },
        handleScroll: true,
        handleScale: true,
    });

    // Price (left scale) — dashed gray
    _vegaPriceSeries = _vegaChart.addLineSeries({
        priceScaleId: 'left',
        color: '#94a3b8', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false,
        priceFormat: { type: 'custom', formatter: _fmtPrice, minMove: 0.5 },
    });

    // Put Vega — red baseline series: fills from 0 downward (negative = below zero = red zone)
    // When put_vega is positive (unusual), it fills above zero with a lighter red.
    _vegaPutSeries = _vegaChart.addBaselineSeries({
        priceScaleId: 'right',
        baseValue: { type: 'price', price: 0 },
        topLineColor:    '#ef4444',
        topFillColor1:   'rgba(239,68,68,0.08)',
        topFillColor2:   'rgba(239,68,68,0.02)',
        bottomLineColor: '#ef4444',
        bottomFillColor1:'rgba(239,68,68,0.35)',
        bottomFillColor2:'rgba(239,68,68,0.10)',
        lineWidth: 2,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#ef4444',
        priceFormat: { type: 'custom', formatter: _fmtVega, minMove: 0.01 },
    });

    // Call Vega — green baseline series: fills from 0 upward (positive = above zero = green zone)
    _vegaCallSeries = _vegaChart.addBaselineSeries({
        priceScaleId: 'right',
        baseValue: { type: 'price', price: 0 },
        topLineColor:    '#22c55e',
        topFillColor1:   'rgba(34,197,94,0.35)',
        topFillColor2:   'rgba(34,197,94,0.10)',
        bottomLineColor: '#22c55e',
        bottomFillColor1:'rgba(34,197,94,0.08)',
        bottomFillColor2:'rgba(34,197,94,0.02)',
        lineWidth: 2,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#22c55e',
        priceFormat: { type: 'custom', formatter: _fmtVega, minMove: 0.01 },
    });

    // (no separate band series needed — baseline fill handles the shading)

    // Crosshair → tooltip
    _vegaChart.subscribeCrosshairMove(p => {
        _vegaShowTooltip(p);
        _vegaSyncToPCR(p);
    });

    // Time-scale sync with PCR charts (listen from PCR side via global function)
    _vegaChart.timeScale().subscribeVisibleLogicalRangeChange(r => {
        if (_vegaRangeSyncing) return;
        window._vegaLastRange = r;
    });

    window.addEventListener('themechanged', e => {
        if (e.detail && e.detail.theme) _vegaApplyTheme(e.detail.theme);
    });
}

// ── Render ─────────────────────────────────────────────────────────────────
function _vegaRender(data) {
    if (!data.length || !_vegaPriceSeries) return;

    const seen = new Map();
    data.forEach(d => seen.set(d.time, d));
    data = [...seen.values()].sort((a, b) => a.time - b.time);

    const priceData = data.map(d => ({ time: d.time, value: d.price }));
    const callData  = data.map(d => ({ time: d.time, value: d.call_vega }));
    const putData   = data.map(d => ({ time: d.time, value: d.put_vega }));

    const isEstimated = data.some(d => d.estimated);

    requestAnimationFrame(() => {
        if (!_vegaPriceSeries) return;
        try {
            _vegaPriceSeries.setData(priceData);
            _vegaPutSeries.setData(putData);
            _vegaCallSeries.setData(callData);
        } catch (e) {
            console.error('[Vega] setData error', e);
            return;
        }
        requestAnimationFrame(() => {
            try { if (_vegaChart) _vegaChart.timeScale().fitContent(); } catch (e) {}
            const last = data[data.length - 1];
            const callEl = document.getElementById('vegaCallVal');
            if (callEl) callEl.textContent = last.call_vega.toFixed(2) + 'Cr' + (isEstimated ? '*' : '');
            const putEl = document.getElementById('vegaPutVal');
            if (putEl) putEl.textContent = last.put_vega.toFixed(2) + 'Cr' + (isEstimated ? '*' : '');
            // Mark section title with estimated indicator
            const title = document.querySelector('#vegaSection .pcr-section-title, .pcr-section-title');
            const estNote = document.getElementById('vegaEstNote');
            if (isEstimated && !estNote) {
                const note = document.createElement('span');
                note.id = 'vegaEstNote';
                note.style.cssText = 'font-size:9px;color:#f59e0b;margin-left:6px;font-weight:600';
                note.textContent = '* est. IV';
                const hdr = document.querySelector('[id="vegaChart"]')?.closest('.pcr-section')?.querySelector('.pcr-section-title');
                if (hdr) hdr.after(note);
            }
        });
    });
}

// ── Tooltip ────────────────────────────────────────────────────────────────
function _vegaShowTooltip(param) {
    const tt = document.getElementById('vegaTooltip');
    if (!tt) return;
    if (!param || !param.time) { tt.classList.add('hidden'); return; }
    const d = _vegaRawData.find(x => x.time === param.time);
    if (!d) { tt.classList.add('hidden'); return; }
    const dt = new Date(d.time * 1000);
    const h = dt.getUTCHours(), m = String(dt.getUTCMinutes()).padStart(2, '0');
    const ampm = h >= 12 ? 'PM' : 'AM', h12 = h % 12 || 12;
    const timeStr = h12 + ':' + m + ' ' + ampm;
    tt.innerHTML = '<b>' + timeStr + '</b>'
        + ' &nbsp; Fut: <b>' + d.price.toFixed(2) + '</b>'
        + ' &nbsp; Call Vega: <b style="color:#22c55e">' + d.call_vega.toFixed(2) + 'Cr</b>'
        + ' &nbsp; Put Vega: <b style="color:#ef4444">' + d.put_vega.toFixed(2) + 'Cr</b>'
;
    tt.classList.remove('hidden');
}

// ── Sync vega chart range when PCR charts scroll ───────────────────────────
// Called by pcr.js time-scale subscription hooks (wired below in vegaSyncFromPCR)
function vegaSyncRange(range) {
    if (!_vegaChart || !range) return;
    _vegaRangeSyncing = true;
    try { _vegaChart.timeScale().setVisibleLogicalRange(range); } catch (e) {}
    _vegaRangeSyncing = false;
}

// Receive crosshair position from a PCR chart and mirror it onto the vega chart
function _vegaSyncFromPCR(param) {
    if (!_vegaChart || !_vegaPriceSeries) return;
    try {
        if (!param || !param.point || param.time == null) {
            _vegaChart.clearCrosshairPosition();
            return;
        }
        const price = _vegaPriceSeries.coordinateToPrice(param.point.y);
        if (price != null) _vegaChart.setCrosshairPosition(price, param.time, _vegaPriceSeries);
        else _vegaChart.clearCrosshairPosition();
    } catch (e) {}
}

function _vegaSyncToPCR(param) {
    // Push crosshair position to PCR charts when hovering vega chart
    if (!window._pcrChart1 || !window._pcrChart2 || !window._pcrChart3) return;
    if (!param || !param.point || param.time == null) {
        try { window._pcrChart1.clearCrosshairPosition(); } catch (e) {}
        try { window._pcrChart2.clearCrosshairPosition(); } catch (e) {}
        try { window._pcrChart3.clearCrosshairPosition(); } catch (e) {}
        return;
    }
    [
        [window._pcrChart1, window._pcrPriceSeries1],
        [window._pcrChart2, window._pcrPriceSeries2],
        [window._pcrChart3, window._pcrPriceSeries3],
    ].forEach(([chart, series]) => {
        try {
            if (!chart || !series) return;
            const price = series.coordinateToPrice(param.point.y);
            if (price != null) chart.setCrosshairPosition(price, param.time, series);
            else chart.clearCrosshairPosition();
        } catch (e) {}
    });
}
