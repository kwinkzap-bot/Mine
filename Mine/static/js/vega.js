'use strict';

// ── State ─────────────────────────────────────────────────────────────────
let _vegaChart = null;
let _vegaPriceSeries = null;
let _vegaCallSeries = null;
let _vegaPutSeries = null;
let _vegaDiffSeries = null;
let _vegaRawData = [];
let _vegaInited = false;
let _vegaRangeSyncing = false;
// Expiry the series is plotted for. null = let the server pick its default
// (nearest expiry with more than a day to run, so expiry day is not 0-DTE).
let _vegaExpiry = null;
let _vegaExpirySymbol = null;

// Which series are drawn. Put-Call Difference starts off — it is a derived
// line and the two vega curves are what the block is read for.
const _vegaVisible = { price: true, call: true, put: true, diff: false };
let _vegaTableOn = true;

// ── Cleanup ────────────────────────────────────────────────────────────────
function _vegaCleanup() {
    try { if (_vegaChart) _vegaChart.remove(); } catch (e) {}
    _vegaChart = null;
    _vegaPriceSeries = _vegaCallSeries = _vegaPutSeries = _vegaDiffSeries = null;
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
    // A symbol switch invalidates the picked expiry — expiries differ per index.
    if (_vegaExpirySymbol && _vegaExpirySymbol !== symbol) _vegaExpiry = null;
    _vegaExpirySymbol = symbol;

    let url = '/api/vega/history?symbol=' + encodeURIComponent(symbol);
    if (_vegaExpiry) url += '&expiry=' + encodeURIComponent(_vegaExpiry);
    fetch(url)
        .then(r => r.json())
        .then(d => {
            if (!d.success) { console.warn('[Vega]', d.error); return; }
            _vegaExpiry = d.expiry || null;
            _vegaFillExpiries(d.expiries || [], d.expiry);
            _vegaRawData = d.data || [];
            _vegaRender(_vegaRawData);
        })
        .catch(e => console.error('[Vega] fetch error', e));
}

// ── Expiry selector ────────────────────────────────────────────────────────
function vegaExpiryChanged() {
    const sel = document.getElementById('vegaExpiry');
    _vegaExpiry = sel && sel.value ? sel.value : null;
    vegaRefresh();
}

function _vegaExpiryLabel(e) {
    const parts = String(e.expiry).split('-');
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const mi = parseInt(parts[1], 10) - 1;
    const label = parts.length === 3 && months[mi] ? (parts[2] + ' ' + months[mi]) : e.expiry;
    return label + ' (' + e.dte + 'd)';
}

function _vegaFillExpiries(expiries, selected) {
    const sel = document.getElementById('vegaExpiry');
    if (!sel) return;
    const wanted = expiries.map(e => e.expiry).join('|');
    if (sel.dataset.keys !== wanted) {
        sel.innerHTML = '';
        expiries.forEach(e => {
            const opt = document.createElement('option');
            opt.value = e.expiry;
            opt.textContent = _vegaExpiryLabel(e);
            sel.appendChild(opt);
        });
        sel.dataset.keys = wanted;
    }
    if (selected) sel.value = selected;
    sel.style.display = expiries.length > 1 ? '' : 'none';
}

// ── Chart creation ─────────────────────────────────────────────────────────
function _vegaGetTheme() {
    const name = window.AppTheme.getActiveTheme();
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
    // Index levels move in tens of points — "24.6K" for every label on the
    // axis says nothing, so print the level itself.
    const _fmtPrice = p => Math.round(p).toLocaleString('en-IN');
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
            timeFormatter: window.lwCrosshairTime,
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
    _vegaPriceSeries = _vegaChart.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'left',
        color: '#94a3b8', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false,
        priceFormat: { type: 'custom', formatter: _fmtPrice, minMove: 0.5 },
    });

    // Put Vega — red baseline series. Put writing reads positive, so the loud
    // fill is the one ABOVE zero; below zero (put unwinding) stays faint.
    _vegaPutSeries = _vegaChart.addSeries(LightweightCharts.BaselineSeries, {
        priceScaleId: 'right',
        baseValue: { type: 'price', price: 0 },
        topLineColor:    '#ef4444',
        topFillColor1:   'rgba(239,68,68,0.35)',
        topFillColor2:   'rgba(239,68,68,0.10)',
        bottomLineColor: '#ef4444',
        bottomFillColor1:'rgba(239,68,68,0.08)',
        bottomFillColor2:'rgba(239,68,68,0.02)',
        lineWidth: 2,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#ef4444',
        priceFormat: { type: 'custom', formatter: _fmtVega, minMove: 0.01 },
    });

    // Call Vega — green baseline series. Call writing reads negative, so the
    // loud fill is the one BELOW zero; above zero (call unwinding) stays faint.
    _vegaCallSeries = _vegaChart.addSeries(LightweightCharts.BaselineSeries, {
        priceScaleId: 'right',
        baseValue: { type: 'price', price: 0 },
        topLineColor:    '#22c55e',
        topFillColor1:   'rgba(34,197,94,0.08)',
        topFillColor2:   'rgba(34,197,94,0.02)',
        bottomLineColor: '#22c55e',
        bottomFillColor1:'rgba(34,197,94,0.35)',
        bottomFillColor2:'rgba(34,197,94,0.10)',
        lineWidth: 2,
        lastValueVisible: true, priceLineVisible: true, priceLineColor: '#22c55e',
        priceFormat: { type: 'custom', formatter: _fmtVega, minMove: 0.01 },
    });

    // Put-Call Difference (Call Vega − Put Vega) — dotted grey line, right scale
    _vegaDiffSeries = _vegaChart.addSeries(LightweightCharts.LineSeries, {
        priceScaleId: 'right',
        color: '#8b8fa3', lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false,
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

    _vegaWireControls();
    _vegaApplyVisibility();
}

// ── Legend switches + table show/hide ─────────────────────────────────────
function _vegaWireControls() {
    document.querySelectorAll('.vega-toggle').forEach(btn => {
        if (btn.dataset.vegaWired) return;
        btn.dataset.vegaWired = '1';
        btn.addEventListener('click', () => {
            const key = btn.dataset.vegaSeries;
            _vegaVisible[key] = !_vegaVisible[key];
            btn.classList.toggle('is-off', !_vegaVisible[key]);
            _vegaApplyVisibility();
        });
    });

    const viewBtn = document.getElementById('vegaViewToggle');
    if (viewBtn && !viewBtn.dataset.vegaWired) {
        viewBtn.dataset.vegaWired = '1';
        viewBtn.addEventListener('click', () => {
            _vegaTableOn = !_vegaTableOn;
            document.getElementById('vegaTablePane')?.classList.toggle('hidden', !_vegaTableOn);
            viewBtn.classList.toggle('is-off', !_vegaTableOn);
            // The chart pane just changed width; autoSize needs a nudge.
            requestAnimationFrame(() => { try { _vegaChart?.timeScale().fitContent(); } catch (e) {} });
        });
    }
}

function _vegaApplyVisibility() {
    const pairs = [
        [_vegaPriceSeries, 'price'],
        [_vegaCallSeries,  'call'],
        [_vegaPutSeries,   'put'],
        [_vegaDiffSeries,  'diff'],
    ];
    pairs.forEach(([series, key]) => {
        try { if (series) series.applyOptions({ visible: _vegaVisible[key] }); } catch (e) {}
    });
}

// ── Render ─────────────────────────────────────────────────────────────────
function _vegaRender(data) {
    if (!_vegaPriceSeries) return;
    if (!data.length) {
        // Selected expiry has no stored chain for this day — blank the chart
        // rather than leaving the previous expiry's curves on screen.
        [_vegaPriceSeries, _vegaPutSeries, _vegaCallSeries, _vegaDiffSeries].forEach(s => {
            try { if (s) s.setData([]); } catch (e) {}
        });
        const callEl = document.getElementById('vegaCallVal');
        if (callEl) callEl.textContent = '--';
        const putEl = document.getElementById('vegaPutVal');
        if (putEl) putEl.textContent = '--';
        return;
    }

    const seen = new Map();
    data.forEach(d => seen.set(d.time, d));
    data = [...seen.values()].sort((a, b) => a.time - b.time);

    const priceData = data.map(d => ({ time: d.time, value: d.price }));
    const callData  = data.map(d => ({ time: d.time, value: d.call_vega }));
    const putData   = data.map(d => ({ time: d.time, value: d.put_vega }));
    const diffData  = data.map(d => ({ time: d.time, value: d.diff != null ? d.diff : (d.put_vega - d.call_vega) }));

    const isEstimated = data.some(d => d.estimated);

    requestAnimationFrame(() => {
        if (!_vegaPriceSeries) return;
        try {
            _vegaPriceSeries.setData(priceData);
            _vegaPutSeries.setData(putData);
            _vegaCallSeries.setData(callData);
            if (_vegaDiffSeries) _vegaDiffSeries.setData(diffData);
        } catch (e) {
            console.error('[Vega] setData error', e);
            return;
        }
        _vegaRenderTable(data);

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

// ── Data table ─────────────────────────────────────────────────────────────
// Same minutes the chart plots, newest first, so the last few prints can be
// read exactly instead of eyeballed off the curve.
function _vegaHHMM(ts) {
    const d = new Date(ts * 1000);
    return String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0');
}

function _vegaRenderTable(data) {
    const host = document.getElementById('vegaTable');
    if (!host || typeof DataGrid === 'undefined') return;

    const latestTs = data.length ? data[data.length - 1].time : null;
    const rows = data.map(d => ({
        time:      d.time,
        call_vega: d.call_vega,
        put_vega:  d.put_vega,
        diff:      d.diff != null ? d.diff : (d.put_vega - d.call_vega),
    }));

    const num = v => (v == null ? '—' : Number(v).toFixed(2));

    DataGrid.mountSortable(host, {
        rows,
        empty: 'No vega data yet',
        defaultSort: { key: 'time', dir: 'desc' },
        rowClass: row => (row.time === latestTs ? 'vega-row-latest' : ''),
        columns: [
            { key: 'time', label: 'Time', sortable: true, format: _vegaHHMM },
            { key: 'call_vega', label: 'Call Vega', sortable: true, align: 'right',
              format: num, cellClass: 'vega-c-call' },
            { key: 'put_vega', label: 'Put Vega', sortable: true, align: 'right',
              format: num, cellClass: 'vega-c-put' },
            { key: 'diff', label: 'Difference', sortable: true, align: 'right',
              format: num, strong: true },
        ],
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
    const diffVal = d.diff != null ? d.diff : (d.put_vega - d.call_vega);
    tt.innerHTML = '<b>' + timeStr + '</b>'
        + ' &nbsp; Fut: <b>' + d.price.toFixed(2) + '</b>'
        + ' &nbsp; Call Vega: <b style="color:#22c55e">' + d.call_vega.toFixed(2) + 'Cr</b>'
        + ' &nbsp; Put Vega: <b style="color:#ef4444">' + d.put_vega.toFixed(2) + 'Cr</b>'
        + ' &nbsp; Diff: <b style="color:#8b8fa3">' + diffVal.toFixed(2) + 'Cr</b>'
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
