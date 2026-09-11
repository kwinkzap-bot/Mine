/**
 * TradingView Lightweight Charts - Reusable Module
 * Provides a clean API for creating and managing candlestick charts
 * Used across multiple pages: options_chart.html, oi_profile.html, etc.
 */

// v5 markers helper — manages a createSeriesMarkers primitive cached on the
// series instance (v5 removed series.setMarkers). Falls back to v4 if present.
// Defined globally so oi_indicators.js / oi_replay.js can use it too.
window.lwSetMarkers = window.lwSetMarkers || function (series, markers) {
    if (!series) return;
    markers = markers || [];
    if (typeof LightweightCharts !== 'undefined' && LightweightCharts.createSeriesMarkers) {
        if (series.__lwMarkers) series.__lwMarkers.setMarkers(markers);
        else series.__lwMarkers = LightweightCharts.createSeriesMarkers(series, markers);
    } else if (typeof series.setMarkers === 'function') {
        series.setMarkers(markers);
    }
};

// The crosshair's time label — the black pill under the time axis. Every chart
// in the app showed a bare "12:30", which on a multi-day window says nothing
// about WHICH day the cursor is on. This is TradingView's own shape,
// "Wed 02 Sep '26  12:30".
//
// Timestamps are fake-UTC (IST clock values stored as UTC) everywhere in this
// app, so the UTC getters are what read correctly — see the `timezone: 'Etc/UTC'`
// that accompanies each localization block.
//
// Seconds only appear when a bar actually carries them (30-second charts),
// and a bar sitting exactly on midnight — a daily/weekly/monthly candle — drops
// the clock entirely rather than printing a meaningless 00:00.
// Defined globally so every page's chart can share one format.
window.lwCrosshairTime = window.lwCrosshairTime || function (t) {
    const d = new Date(t * 1000);
    if (isNaN(d)) return '';
    const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()];
    const mo = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()];
    const pad = n => String(n).padStart(2, '0');
    const date = `${wd} ${pad(d.getUTCDate())} ${mo} '${String(d.getUTCFullYear()).slice(-2)}`;
    const h = d.getUTCHours(), m = d.getUTCMinutes(), sec = d.getUTCSeconds();
    if (!h && !m && !sec) return date;
    return `${date}  ${pad(h)}:${pad(m)}` + (sec ? `:${pad(sec)}` : '');
};

// v5 z-order helper — lifts the candle series above overlay indicators so it
// renders on top (v5 added ISeriesApi.setSeriesOrder; higher index = on top).
// A large index is clamped to the current top of the pane's series collection.
window.lwBringToFront = window.lwBringToFront || function (series) {
    try { if (series && typeof series.setSeriesOrder === 'function') series.setSeriesOrder(1e6); } catch (e) {}
};

// Compact price-axis ticks, Indian convention, for panes whose values run to
// lakhs — the Round Strike ΔOI histograms sit at ±10^4..10^6 contracts and
// rendered as "-2500000.00", six digits of noise on a 100px axis.
//
// Only magnitudes at or above one lakh are abbreviated. Everything below keeps
// the exact 2dp it had, which covers every price these charts actually plot:
// option premiums (~10^2), NIFTY (~2.4x10^4), even SENSEX (~8x10^4). So turning
// this on for a chart changes its DeltaOI axis and leaves its price axis alone.
function _tvCompactPrice(val) {
    const v = Number(val) || 0;
    const abs = Math.abs(v);
    const sign = v < 0 ? '-' : '';
    if (abs >= 1e7) return sign + (abs / 1e7).toFixed(2).replace(/\.?0+$/, '') + 'Cr';
    if (abs >= 1e5) return sign + (abs / 1e5).toFixed(2).replace(/\.?0+$/, '') + 'L';
    // Bare '0' rather than '0.00': on a DeltaOI axis reading -20L / -10L / 0,
    // two decimal places on the zero line is the only thing carrying them.
    if (v === 0) return '0';
    return v.toFixed(2);
}

window.TradingViewChart = (function () {
    'use strict';

    // ---- Horizontal Ray drawing tool ----
    // Lightweight Charts has no built-in drawing-tool API, so a "ray" is
    // implemented as a 2-point LineSeries: start at the clicked bar, end a
    // little past the most recently loaded candle (using the caller's
    // `rightOffset` margin so it visibly reaches the Y-axis instead of
    // stopping at the last candle). Anchoring the end to real data plus a
    // SMALL fixed bar count — not a synthetic far-future timestamp, and not
    // thousands of filler points — introduces at most one new logical index
    // on the chart's shared time axis, so it can't reshuffle other series'
    // positions or disturb the user's pan/zoom — tried both of those first;
    // a single far-future point only ever lands ~1 index past the last real
    // bar (renders a few px past it, however large the time jump — LWC
    // positions numeric-timestamp data by rank order, not real elapsed
    // time), and filling thousands of points to compensate corrupted the
    // shared index when the ray started well before the most recent data.
    // Trade-off: the ray only reaches whatever was loaded at draw time, not
    // literally forever; it grows further on the next data reload.
    //
    // Standalone (not tied to TradingViewChart.create) so ANY chart+series
    // pair can get the ray tool — including charts built directly against
    // the raw LightweightCharts API (e.g. the main OI Profile candlestick
    // chart in oi_profile.js), not just ones created via .create() below.
    // `opts.timeframe` may be a plain string or a zero-arg function; charts
    // whose interval can change after creation (a TF dropdown) should pass a
    // function so the ray's reach uses the CURRENT interval, not the one at
    // attach time.
    function createRayTool(chart, series, container, opts) {
        opts = opts || {};
        const rightOffset = opts.rightOffset != null ? opts.rightOffset : 20;
        function buildRayPoints(startTime, price, lastRealTime) {
            const step = intervalSeconds(opts.timeframe);
            const base = (lastRealTime != null && lastRealTime > startTime) ? lastRealTime : startTime;
            const end = base + (rightOffset + 5) * step;
            return [{ time: startTime, value: price }, { time: end, value: price }];
        }

        let rayModeActive = false;
        const rayLines = [];
        // Style applied to the NEXT ray drawn — set via setRayMode(active, style)
        // when the caller's toolbar (color/width/style pickers) arms the tool, so
        // each new ray can use different settings without touching rays already drawn.
        let rayStyle = {
            color: opts.rayColor || '#f33968',
            width: 2,
            lineStyle: LightweightCharts.LineStyle.Dotted
        };

        // Creates one ray line series and tracks it. Shared by the click
        // handler (new ray, current rayStyle) and the public addRay() method
        // (restoring a saved ray with its own saved style) so both paths stay
        // in sync. Stashes {time, price, color, width, lineStyle} on the
        // series itself so callers can read back what was actually drawn —
        // needed for onRayDrawn/onRayRemoved to hand the caller enough to
        // persist and later restore it.
        function createRayLine(startTime, price, style) {
            const s = style || rayStyle;
            const raySeries = chart.addSeries(LightweightCharts.LineSeries, {
                color: s.color,
                lineWidth: s.width,
                lineStyle: s.lineStyle,
                title: 'Ray',
                lastValueVisible: true,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                autoscaleInfoProvider: () => null
            });
            const priceData = series ? series.data() : [];
            const lastRealTime = priceData.length ? priceData[priceData.length - 1].time : null;
            raySeries.setData(buildRayPoints(startTime, price, lastRealTime));
            raySeries._rayInfo = { time: startTime, price, color: s.color, width: s.width, lineStyle: s.lineStyle };
            rayLines.push(raySeries);
            // New series render on top by default — pull the candles back to
            // the front so rays (like other indicator lines) sit behind them.
            // Panes with more than one candle series (e.g. the CE+PE Combined
            // chart) need their full app-level z-order policy re-applied, not
            // just this one anchor series, or bringing only it forward would
            // reorder it ahead of the OTHER candle series sharing the pane.
            if (typeof opts.reapplyZOrder === 'function') opts.reapplyZOrder();
            else if (typeof window.lwBringToFront === 'function') window.lwBringToFront(series);
            return raySeries;
        }

        chart.subscribeClick((param) => {
            if (!rayModeActive || !series) return;
            if (!param || !param.point || param.time == null) return;
            const price = series.coordinateToPrice(param.point.y);
            if (price == null) return;

            const raySeries = createRayLine(param.time, price, rayStyle);

            rayModeActive = false;
            container.style.cursor = '';
            if (typeof opts.onRayDrawn === 'function') {
                try { opts.onRayDrawn(raySeries._rayInfo); } catch (e) {}
            }
        });

        // Right-click near a ray removes it (does not block the browser menu elsewhere).
        container.addEventListener('contextmenu', (e) => {
            if (rayLines.length === 0) return;
            const rect = container.getBoundingClientRect();
            const y = e.clientY - rect.top;
            let closestIdx = -1, closestDist = 8;
            rayLines.forEach((rs, idx) => {
                try {
                    const rdata = rs.data();
                    if (!rdata || !rdata.length) return;
                    const yCoord = rs.priceToCoordinate(rdata[0].value);
                    if (yCoord == null) return;
                    const dist = Math.abs(yCoord - y);
                    if (dist < closestDist) { closestDist = dist; closestIdx = idx; }
                } catch (err) {}
            });
            if (closestIdx >= 0) {
                e.preventDefault();
                const removed = rayLines[closestIdx];
                try { chart.removeSeries(removed); } catch (err) {}
                rayLines.splice(closestIdx, 1);
                if (typeof opts.onRayRemoved === 'function') {
                    try { opts.onRayRemoved(removed._rayInfo); } catch (err) {}
                }
            }
        });

        return {
            /**
             * Arms/disarms the horizontal-ray draw tool. While armed, the next
             * click on this chart drops a ray and auto-disarms.
             * @param {boolean} active
             * @param {{color?: string, width?: number, lineStyle?: number}} [style] -
             *   Overrides the style used for the NEXT ray only; rays already drawn
             *   are unaffected. Any field omitted keeps its previous value.
             */
            setRayMode: function (active, style) {
                rayModeActive = !!active;
                container.style.cursor = rayModeActive ? 'crosshair' : '';
                if (style) {
                    if (style.color != null) rayStyle.color = style.color;
                    if (style.width != null) rayStyle.width = style.width;
                    if (style.lineStyle != null) rayStyle.lineStyle = style.lineStyle;
                }
            },
            isRayModeActive: function () {
                return rayModeActive;
            },
            /**
             * Programmatically draws a ray without arming/clicking — used to
             * restore rays a caller persisted (e.g. to localStorage) on page
             * load. `style` defaults to the tool's current style if omitted.
             * @param {number} time
             * @param {number} price
             * @param {{color?: string, width?: number, lineStyle?: number}} [style]
             */
            addRay: function (time, price, style) {
                if (time == null || price == null) return null;
                const merged = Object.assign({}, rayStyle, style || {});
                return createRayLine(time, price, merged);
            },
            /**
             * Removes all ray lines drawn on this chart.
             */
            clearRays: function () {
                rayLines.forEach(rs => { try { chart.removeSeries(rs); } catch (e) {} });
                rayLines.length = 0;
            },
            /**
             * Re-anchors every drawn ray's end point to the latest loaded
             * candle — call after each data update so rays keep pace with
             * new candles instead of stopping wherever they were when drawn
             * (see the createRayTool trade-off note above).
             */
            extendRays: function () {
                if (!series || !rayLines.length) return;
                const priceData = series.data();
                const lastRealTime = priceData.length ? priceData[priceData.length - 1].time : null;
                if (lastRealTime == null) return;
                rayLines.forEach(rs => {
                    const info = rs._rayInfo;
                    if (!info) return;
                    try { rs.setData(buildRayPoints(info.time, info.price, lastRealTime)); } catch (e) {}
                });
            }
        };
    }

    // Private helper functions
    /**
     * Formats raw candlestick data for Lightweight Charts
     * Converts timestamps and ensures proper data structure
     */
    function formatChartData(rawData) {
        if (!rawData || !Array.isArray(rawData)) {
            return [];
        }

        const seen = new Set();
        return rawData.map((item, index) => {
            try {
                // Handle both 'time' and 'date' field names (backend uses 'date')
                let timestamp = item.time || item.date;
                let time;

                if (typeof timestamp === 'number') {
                    time = timestamp < 10000000000 ? timestamp : Math.floor(timestamp / 1000);
                } else if (typeof timestamp === 'string') {
                    const date = new Date(timestamp);
                    if (isNaN(date.getTime())) {
                        return null;  // Drop candle with unparseable timestamp
                    }
                    time = Math.floor(date.getTime() / 1000);
                } else {
                    return null;  // Drop candle with unknown timestamp type
                }

                const open = parseFloat(item.open ?? item.o);
                const high = parseFloat(item.high ?? item.h);
                const low = parseFloat(item.low ?? item.l);
                const close = parseFloat(item.close ?? item.c);

                // If all OHLC values are missing or invalid, treat as whitespace (only time)
                if (!isFinite(open) || !isFinite(high) || !isFinite(low) || !isFinite(close) ||
                    open <= 0 || high <= 0 || low <= 0 || close <= 0) {
                    return { time };
                }

                return {
                    time, open, high, low, close,
                    volume: item.volume || 0,
                    ...(item.color && { color: item.color }),
                    ...(item.borderColor && { borderColor: item.borderColor }),
                    ...(item.wickColor && { wickColor: item.wickColor })
                };
            } catch (error) {
                console.error(`[formatChartData] Error processing candle at index ${index}:`, error, item);
                return null;
            }
        })
            // Remove nulls, sort ascending by time, deduplicate timestamps
            .filter(c => c !== null)
            .sort((a, b) => a.time - b.time)
            .filter(c => {
                if (seen.has(c.time)) return false;
                seen.add(c.time);
                return true;
            });
    }


    /**
     * Creates a time formatter based on timeframe with IST timezone
     * Used for x-axis labels on the chart (matching options_chart_app.js approach)
     */
    function createTimeFormatter(timeframe) {
        return {
            time: (businessDayOrTimestamp) => {
                // Convert timestamp to readable format in IST timezone
                const date = new Date(businessDayOrTimestamp * 1000);

                // Format in IST timezone using Intl.DateTimeFormat
                const formatter = new Intl.DateTimeFormat('en-IN', {
                    timeZone: 'Asia/Kolkata',
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: false
                });

                return formatter.format(date);
            }
        };
    }

    /**
     * Evaluates status based on current price relative to PDH/PDL
     */
    function evaluateStatus(currentPrice, pdh, pdl) {
        if (currentPrice === null || currentPrice === undefined || pdh === null || pdl === null) {
            return { text: '--', className: 'status-na' };
        }

        if (currentPrice > pdh) {
            return { text: 'WIN', className: 'status-win' };
        }
        if (currentPrice < pdl) {
            return { text: 'LOSS', className: 'status-loss' };
        }
        return { text: 'SIDEWAY', className: 'status-sideway' };
    }

    /**
     * Gets the latest close price from formatted data
     */
    function getLatestPrice(data) {
        if (!data || data.length === 0) {
            return null;
        }
        return data[data.length - 1].close;
    }

    /**
     * Calculates EMA for given period from formatted candlestick data
     */
    function calculateEMA(data, period) {
        if (!data || data.length < period) return [];

        const k = 2 / (period + 1);
        const result = [];

        // Seed: SMA of first `period` closes
        let sum = 0;
        let seedCount = 0;
        for (let i = 0; i < period; i++) {
            if (data[i].close !== undefined) { sum += data[i].close; seedCount++; }
        }
        if (seedCount === 0) return [];

        let ema = sum / seedCount;
        result.push({ time: data[period - 1].time, value: ema });

        for (let i = period; i < data.length; i++) {
            if (data[i].close !== undefined) {
                ema = data[i].close * k + ema * (1 - k);
                result.push({ time: data[i].time, value: ema });
            }
        }

        return result;
    }

    /**
     * Adds all 4 PDH/PDL price lines to chart
     * For CE Charts: CE PDH/PDL (dark grey), PE PDH (green), PE PDL (red)
     * For PE Charts: PE PDH/PDL (dark grey), CE PDH (green), CE PDL (red)
     */
    function addPdhlLines(series, cePdh, cePdl, pePdh, pePdl, chartType = 'CE') {
        const lines = [];

        const isCeChart = chartType === 'CE';

        // Add PDH line for primary chart type (dark grey)
        if (isCeChart) {
            // CE Chart: CE PDH is primary (dark grey)
            if (cePdh !== null && cePdh !== undefined) {
                try {
                    const cePdhLine = series.createPriceLine({
                        price: cePdh,
                        color: '#5a6470',  // Dark grey
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: 'CE PDH'
                    });
                    lines.push(cePdhLine);
                } catch (e) {
                    console.warn('[Chart] Failed to add CE PDH line:', e);
                }
            }
        } else {
            // PE Chart: PE PDH is primary (dark grey)
            if (pePdh !== null && pePdh !== undefined) {
                try {
                    const pePdhLine = series.createPriceLine({
                        price: pePdh,
                        color: '#5a6470',  // Dark grey
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: 'PE PDH'
                    });
                    lines.push(pePdhLine);
                } catch (e) {
                    console.warn('[Chart] Failed to add PE PDH line:', e);
                }
            }
        }

        // Add PDL line for primary chart type (dark grey)
        if (isCeChart) {
            // CE Chart: CE PDL is primary (dark grey)
            if (cePdl !== null && cePdl !== undefined) {
                try {
                    const cePdlLine = series.createPriceLine({
                        price: cePdl,
                        color: '#5a6470',  // Dark grey
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: 'CE PDL'
                    });
                    lines.push(cePdlLine);
                } catch (e) {
                    console.warn('[Chart] Failed to add CE PDL line:', e);
                }
            }
        } else {
            // PE Chart: PE PDL is primary (dark grey)
            if (pePdl !== null && pePdl !== undefined) {
                try {
                    const pePdlLine = series.createPriceLine({
                        price: pePdl,
                        color: '#5a6470',  // Dark grey
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: 'PE PDL'
                    });
                    lines.push(pePdlLine);
                } catch (e) {
                    console.warn('[Chart] Failed to add PE PDL line:', e);
                }
            }
        }

        // Add comparison chart PDH line (green for opposite type)
        if (isCeChart) {
            // CE Chart: PE PDH comparison (green)
            if (pePdh !== null && pePdh !== undefined) {
                try {
                    const pePdhLine = series.createPriceLine({
                        price: pePdh,
                        color: '#10b981',  // Green
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: 'PE PDH'
                    });
                    lines.push(pePdhLine);
                } catch (e) {
                    console.warn('[Chart] Failed to add PE PDH line:', e);
                }
            }
        } else {
            // PE Chart: CE PDH comparison (green)
            if (cePdh !== null && cePdh !== undefined) {
                try {
                    const cePdhLine = series.createPriceLine({
                        price: cePdh,
                        color: '#10b981',  // Green
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: 'CE PDH'
                    });
                    lines.push(cePdhLine);
                } catch (e) {
                    console.warn('[Chart] Failed to add CE PDH line:', e);
                }
            }
        }

        // Add comparison chart PDL line (red for opposite type)
        if (isCeChart) {
            // CE Chart: PE PDL comparison (red)
            if (pePdl !== null && pePdl !== undefined) {
                try {
                    const pePdlLine = series.createPriceLine({
                        price: pePdl,
                        color: '#ef4444',  // Red
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: 'PE PDL'
                    });
                    lines.push(pePdlLine);
                } catch (e) {
                    console.warn('[Chart] Failed to add PE PDL line:', e);
                }
            }
        } else {
            // PE Chart: CE PDL comparison (red)
            if (cePdl !== null && cePdl !== undefined) {
                try {
                    const cePdlLine = series.createPriceLine({
                        price: cePdl,
                        color: '#ef4444',  // Red
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: 'CE PDL'
                    });
                    lines.push(cePdlLine);
                } catch (e) {
                    console.warn('[Chart] Failed to add CE PDL line:', e);
                }
            }
        }

        return lines;
    }

    /* ── interval vocabulary ─────────────────────────────────────────────── */
    // The app-wide timeframe keys ('5minute', 'day', …) in seconds. Shared by
    // the ray tool, the bar-close countdown and MineCPR.
    const INTERVAL_SECONDS = {
        '30second': 30, minute: 60, '2minute': 120, '3minute': 180, '5minute': 300,
        '10minute': 600, '15minute': 900, '30minute': 1800, '60minute': 3600,
        day: 86400, week: 604800, month: 2592000
    };
    function intervalSeconds(iv) {
        if (typeof iv === 'function') iv = iv();
        if (typeof iv === 'number' && isFinite(iv) && iv > 0) return iv;
        return INTERVAL_SECONDS[iv] || 60;
    }
    const SESSION_OPEN_S = 9 * 3600 + 15 * 60, SESSION_CLOSE_S = 15 * 3600 + 30 * 60;
    // Bars are on the app's fake-IST grid (IST clock stored as UTC seconds).
    const nowIst = () => Math.floor(Date.now() / 1000) + 19800;

    // Black or white, whichever reads on `color` — for text on a coloured tag.
    function contrastText(color) {
        const m = typeof color === 'string' && (color.match(/^#([0-9a-f]{6})/i) || color.match(/[\d.]+/g));
        if (!m) return '#ffffff';
        let r, g, b;
        if (color[0] === '#') { const n = parseInt(m[1], 16); r = n >> 16 & 255; g = n >> 8 & 255; b = n & 255; }
        else { r = +m[0]; g = +m[1]; b = +m[2]; }
        return (0.299 * r + 0.587 * g + 0.114 * b) > 170 ? '#111111' : '#ffffff';
    }

    /* ── crisp: device-pixel-snapped drawing kit ─────────────────────────── */
    // Lightweight Charts' own renderers snap to the bitmap grid; a 1px line
    // drawn by a primitive at a fractional y straddles two physical pixels and
    // anti-aliases into a smeared 2px — the blur that set our overlays apart
    // from TradingView. Everything here takes DEVICE pixels (already
    // multiplied by the pixel ratio) and integer widths. Solid lines are
    // filled rectangles, crisp by construction; only dashes need a stroke,
    // centred on a half pixel for odd widths.
    const LINE_DASH = {
        // LineStyle.Solid | Dotted | Dashed | LargeDashed | SparseDotted — the
        // patterns LWC itself uses, scaled by the device-pixel line width.
        0: () => [], 1: w => [w, w], 2: w => [2 * w, 2 * w], 3: w => [6 * w, 6 * w], 4: w => [w, 4 * w]
    };
    const crisp = {
        width: (cssWidth, ratio) => Math.max(1, Math.round((cssWidth || 1) * ratio)),
        dash: (style, w) => (LINE_DASH[style] || LINE_DASH[0])(w),
        // Horizontal line from x1 to x2 at integer y, w device px thick.
        hline(ctx, x1, x2, y, w, color, dash) {
            if (x2 <= x1) return;
            if (dash && dash.length) {
                ctx.strokeStyle = color; ctx.lineWidth = w; ctx.lineCap = 'butt';
                ctx.setLineDash(dash);
                const yy = y + (w % 2 ? 0.5 : 0);
                ctx.beginPath(); ctx.moveTo(x1, yy); ctx.lineTo(x2, yy); ctx.stroke();
                ctx.setLineDash([]);
            } else {
                ctx.fillStyle = color;
                ctx.fillRect(x1, y - Math.floor(w / 2), x2 - x1, w);
            }
        },
        // Sloped segment — nothing to snap, so it is an ordinary stroke.
        segment(ctx, x1, y1, x2, y2, w, color, dash) {
            ctx.strokeStyle = color; ctx.lineWidth = w; ctx.lineCap = 'butt';
            ctx.setLineDash(dash && dash.length ? dash : []);
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
            ctx.setLineDash([]);
        },
        // Box border, w device px, drawn as four filled bars; `openRight`
        // leaves the right edge off for a box that runs to the pane edge.
        border(ctx, left, top, right, bottom, w, color, openRight) {
            const h = Math.max(1, bottom - top);
            ctx.fillStyle = color;
            ctx.fillRect(left, top, right - left, w);
            ctx.fillRect(left, bottom - w, right - left, w);
            ctx.fillRect(left, top, w, h);
            if (!openRight) ctx.fillRect(right - w, top, w, h);
        }
    };

    /* ── crisp line "series" ─────────────────────────────────────────────── */
    // A LineSeries stand-in for level lines — CPR, reversal levels, box
    // borders, previous-session VWAP: anything flat. Lightweight Charts draws
    // a LineSeries at fractional coordinates, so a level ends up two or three
    // pixels of smear; these are drawn by one primitive per chart through the
    // kit above, so every flat run lands on one row of pixels. Sloped runs
    // (the odd joining diagonal) are stroked normally.
    //
    // The handle speaks the ISeriesApi subset the pages already use —
    // setData / update / applyOptions / options / data / setSeriesOrder /
    // priceToCoordinate — so a caller swaps `chart.addSeries(LineSeries, o)`
    // for `TradingViewChart.addCrispLine(chart, o)` and nothing else moves.
    // Two things it cannot be: an argument to chart.removeSeries (use
    // TradingViewChart.removeSeries, which takes either) or a crosshair
    // source (there is no marker and no seriesData entry).
    //
    // One anchor LineSeries per chart carries every handle's timestamps as
    // whitespace, so a line that runs past the last candle still stretches
    // the time scale exactly as the real series did; the primitive hangs off
    // it, and setSeriesOrder on any handle moves the anchor — the whole layer
    // — in the z-stack.
    const _crispLayers = new WeakMap();   // chart -> layer

    function crispLayer(chart) {
        let L = _crispLayers.get(chart);
        if (L) return L;
        const anchor = chart.addSeries(LightweightCharts.LineSeries, {
            color: 'rgba(0,0,0,0)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
            crosshairMarkerVisible: false, autoscaleInfoProvider: () => null
        });
        L = { chart, anchor, handles: new Set(), times: new Set(), maxTime: -Infinity, ref: null,
              pendingFull: false, pendingTimes: [], flushQueued: false, requestUpdate: null, axisViews: [] };

        const lowerBound = (arr, key, x) => { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (key(arr[m]) < x) lo = m + 1; else hi = m; } return lo; };

        // price -> y. The anchor carries no values, and a series without a
        // first value cannot convert (LWC returns null), so the conversion
        // borrows any valued series on the same price scale — the candles,
        // normally. Re-resolved whenever the borrowed one stops answering.
        const scaleId = () => (anchor.options() || {}).priceScaleId || 'right';
        function findRef() {
            try {
                // The anchor's own pane only — a chart with sub-panes (the
                // Round Strike ΔOI histograms) has other 'right' scales there.
                let panes = [];
                if (anchor.getPane) panes = [anchor.getPane()];
                else if (chart.panes) panes = chart.panes().slice(0, 1);
                for (const pane of panes) for (const s of pane.getSeries()) {
                    if (s === anchor || s.__crisp) continue;
                    if (((s.options() || {}).priceScaleId || 'right') !== scaleId()) continue;
                    const d = s.data();
                    if (d.length && d.some(p => p && (p.close != null || p.value != null))) return s;
                }
            } catch (e) { /* older API */ }
            return null;
        }
        const yOf = price => {
            if (price == null || !isFinite(price)) return null;
            let c = null;
            try { c = L.ref ? L.ref.priceToCoordinate(price) : null; } catch (e) { c = null; }
            if (c == null) { L.ref = findRef(); try { c = L.ref ? L.ref.priceToCoordinate(price) : anchor.priceToCoordinate(price); } catch (e) { c = null; } }
            return c == null ? null : c;
        };

        function lastValueOf(h) {
            for (let i = h._pts.length - 1; i >= 0; i--) if (h._pts[i].value != null) return h._pts[i].value;
            return null;
        }

        const linesView = {
            zOrder: () => 'normal', update() {},
            renderer: () => ({
                draw(target) {
                    const ts = chart.timeScale();
                    const vr = ts.getVisibleRange();
                    if (!vr || !L.handles.size) return;
                    const from = typeof vr.from === 'number' ? vr.from : -Infinity;
                    const to = typeof vr.to === 'number' ? vr.to : Infinity;
                    target.useBitmapCoordinateSpace(scope => {
                        const ctx = scope.context, hr = scope.horizontalPixelRatio, vpr = scope.verticalPixelRatio;
                        ctx.save();
                        for (const h of L.handles) {
                            const o = h._o;
                            if (!o.visible || !h._pts.length) continue;
                            const runs = h._runList();
                            const w = crisp.width(o.lineWidth, vpr), dash = crisp.dash(o.lineStyle, w);
                            for (let i = lowerBound(runs, r => r.t2, from); i < runs.length && runs[i].t1 <= to; i++) {
                                const r = runs[i];
                                const cx1 = ts.timeToCoordinate(r.t1), cx2 = ts.timeToCoordinate(r.t2);
                                if (cx1 == null || cx2 == null) continue;
                                const x1 = Math.round(cx1 * hr), x2 = Math.round(cx2 * hr);
                                if (r.seg) {
                                    const y1 = yOf(r.v1), y2 = yOf(r.v2);
                                    if (y1 == null || y2 == null) continue;
                                    crisp.segment(ctx, x1, y1 * vpr, x2, y2 * vpr, w, o.color, dash);
                                } else {
                                    const y = yOf(r.v);
                                    if (y == null) continue;
                                    crisp.hline(ctx, x1, x2, Math.round(y * vpr), w, o.color, dash);
                                }
                            }
                        }
                        ctx.restore();
                    });
                }
            })
        };
        // The level's name, in a tag at the pane's right edge on the line —
        // TradingView's shape; the price itself sits on the axis (axisViews).
        const labelsView = {
            zOrder: () => 'top', update() {},
            renderer: () => ({
                draw(target) {
                    if (!L.handles.size) return;
                    target.useBitmapCoordinateSpace(scope => {
                        const ctx = scope.context, hr = scope.horizontalPixelRatio, vpr = scope.verticalPixelRatio;
                        const W = scope.bitmapSize.width;
                        ctx.save();
                        ctx.font = `${Math.round(10 * vpr)}px -apple-system, system-ui, sans-serif`;
                        ctx.textBaseline = 'middle';
                        for (const h of L.handles) {
                            const o = h._o;
                            if (!o.visible || !o.lastValueVisible || !o.title) continue;
                            const v = lastValueOf(h);
                            const y = yOf(v);
                            if (y == null) continue;
                            const tw = ctx.measureText(o.title).width, pad = 4 * hr, bh = Math.round(14 * vpr);
                            const bw = Math.round(tw + 2 * pad), x = W - bw, cy = Math.round(y * vpr);
                            ctx.fillStyle = o.color;
                            ctx.fillRect(x, cy - (bh >> 1), bw, bh);
                            ctx.fillStyle = contrastText(o.color);
                            ctx.fillText(o.title, x + pad, cy);
                        }
                        ctx.restore();
                    });
                }
            })
        };
        const primitive = {
            attached(p) { L.requestUpdate = p.requestUpdate; },
            detached() { L.requestUpdate = null; },
            updateAllViews() {
                const views = [];
                for (const h of L.handles) {
                    const o = h._o;
                    if (!o.visible || !o.lastValueVisible) continue;
                    const v = lastValueOf(h);
                    if (v == null) continue;
                    const text = anchor.priceFormatter().format(v);
                    views.push({
                        coordinate: () => { const c = yOf(v); return c == null ? -100 : c; },
                        text: () => text, textColor: () => contrastText(o.color), backColor: () => o.color,
                        visible: () => true, tickVisible: () => true
                    });
                }
                L.axisViews = views;
            },
            paneViews: () => [linesView, labelsView],
            priceAxisViews: () => L.axisViews,
            autoscaleInfo: () => null
        };
        anchor.attachPrimitive(primitive);

        // Time-scale bookkeeping: a batch of setData calls — fifteen CPR
        // levels in one redraw — becomes one anchor setData, queued on a
        // microtask; an update() with a fresh, later timestamp is one cheap
        // anchor.update() instead.
        function flush() {
            L.flushQueued = false;
            if (L.pendingFull) {
                const set = new Set();
                for (const h of L.handles) for (const p of h._pts) set.add(p.time);
                const times = Array.from(set).sort((a, b) => a - b);
                L.times = set; L.maxTime = times.length ? times[times.length - 1] : -Infinity;
                try { anchor.setData(times.map(t => ({ time: t }))); } catch (e) { /* chart being torn down */ }
            } else if (L.pendingTimes.length) {
                const add = L.pendingTimes.filter(t => !L.times.has(t)).sort((a, b) => a - b);
                for (const t of add) {
                    if (t <= L.maxTime) { L.pendingFull = true; L.pendingTimes = []; return flush(); }
                    try { anchor.update({ time: t }); } catch (e) { L.pendingFull = true; L.pendingTimes = []; return flush(); }
                    L.times.add(t); L.maxTime = t;
                }
            }
            L.pendingFull = false; L.pendingTimes = [];
            if (L.requestUpdate) L.requestUpdate();
        }
        L.yOf = yOf;
        L.touch = (full, time) => {
            if (full) L.pendingFull = true; else if (time != null) L.pendingTimes.push(time);
            if (!L.flushQueued) { L.flushQueued = true; queueMicrotask(flush); }
        };
        _crispLayers.set(chart, L);
        return L;
    }

    // Points → sorted, de-duplicated {time[, value]} — lenient where LWC throws.
    function normalisePoints(pts) {
        const out = [];
        for (const p of (pts || [])) {
            if (!p || typeof p.time !== 'number' || !isFinite(p.time)) continue;
            const v = p.value;
            out.push(v == null || !isFinite(v) ? { time: p.time } : { time: p.time, value: +v });
        }
        out.sort((a, b) => a.time - b.time);
        let w = 0;
        for (let i = 0; i < out.length; i++) { if (w && out[w - 1].time === out[i].time) out[w - 1] = out[i]; else out[w++] = out[i]; }
        out.length = w;
        return out;
    }

    function addCrispLine(chart, options) {
        const L = crispLayer(chart);
        const h = {
            __crisp: true,
            _o: Object.assign({ color: '#2962ff', lineWidth: 1, lineStyle: 0, visible: true, title: '', lastValueVisible: false }, options || {}),
            _pts: [], _runs: null,
            // Maximal flat runs {t1, t2, v} and sloped joins {seg, t1, v1, t2, v2},
            // in time order, broken at whitespace — computed once per data change.
            _runList() {
                if (this._runs) return this._runs;
                const runs = [], pts = this._pts;
                let cur = null;
                for (let i = 0; i < pts.length; i++) {
                    const p = pts[i];
                    if (p.value == null) { cur = null; continue; }
                    const prev = i ? pts[i - 1] : null;
                    if (!prev || prev.value == null || !cur) { cur = { t1: p.time, t2: p.time, v: p.value }; runs.push(cur); continue; }
                    if (prev.value === p.value) { cur.t2 = p.time; continue; }
                    runs.push({ seg: true, t1: prev.time, v1: prev.value, t2: p.time, v2: p.value });
                    cur = { t1: p.time, t2: p.time, v: p.value }; runs.push(cur);
                }
                return (this._runs = runs);
            },
            setData(pts) { this._pts = normalisePoints(pts); this._runs = null; L.touch(true); },
            update(p) {
                if (!p || typeof p.time !== 'number') return;
                const np = normalisePoints([p])[0];
                if (!np) return;
                const pts = this._pts, n = pts.length;
                if (!n || pts[n - 1].time < np.time) pts.push(np);
                else if (pts[n - 1].time === np.time) pts[n - 1] = np;
                else {
                    let lo = 0, hi = n;
                    while (lo < hi) { const m = (lo + hi) >> 1; if (pts[m].time < np.time) lo = m + 1; else hi = m; }
                    if (lo < n && pts[lo].time === np.time) pts[lo] = np; else pts.splice(lo, 0, np);
                }
                this._runs = null;
                L.touch(false, np.time);
            },
            data() { return this._pts; },
            applyOptions(o) { Object.assign(this._o, o || {}); if (L.requestUpdate) L.requestUpdate(); },
            options() { return Object.assign({}, this._o); },
            seriesType() { return 'Line'; },
            setSeriesOrder(n) { try { L.anchor.setSeriesOrder(n); } catch (e) {} },
            seriesOrder() { try { return L.anchor.seriesOrder(); } catch (e) { return 0; } },
            priceFormatter() { return L.anchor.priceFormatter(); },
            priceToCoordinate(p) { return L.yOf(p); },
            coordinateToPrice(c) { try { return (L.ref || L.anchor).coordinateToPrice(c); } catch (e) { return null; } },
            priceScale() { return L.anchor.priceScale(); },
            createPriceLine(o) { return L.anchor.createPriceLine(o); },
            removePriceLine(l) { return L.anchor.removePriceLine(l); },
            attachPrimitive(p) { return L.anchor.attachPrimitive(p); },
            detachPrimitive(p) { return L.anchor.detachPrimitive(p); },
            remove() { L.handles.delete(h); L.touch(true); }
        };
        L.handles.add(h);
        return h;
    }

    // chart.removeSeries for real series and crisp handles alike.
    function removeSeries(chart, s) {
        if (!s) return;
        if (s.__crisp) s.remove();
        else chart.removeSeries(s);
    }

    /* ── bar-close countdown on the price axis ───────────────────────────── */
    // TradingView's "00:25" under the last price: seconds until the forming
    // bar closes. A v5 series primitive can hand the price axis labels of its
    // own, so the series' built-in last-value tag is switched off and both
    // rows — price, countdown — are drawn here in one colour and one width,
    // reading as a single block. The countdown is padded with figure spaces
    // (digit-width) to the price's digit count so its box matches. Refreshed
    // once a second while the bar is live; blank between sessions, and blank
    // on a replayed or historical bar, since its close is already past.
    //
    // opts: interval (key | seconds | fn) — required for the countdown;
    //       lastBar (fn) — defaults to the series' own last OHLC bar;
    //       countdown (bool | fn) — the countdown row, on by default;
    //       upColor / downColor — default to the series' candle colours.
    const _countdowns = new Set();
    let _countdownTimer = null;

    function barCloseAt(bar, secs) {
        if (!bar || !isFinite(secs) || secs > 86400) return null;
        const day = bar.time - (bar.time % 86400);
        if (secs >= 86400) return day + SESSION_CLOSE_S;
        return Math.min(bar.time + secs, day + SESSION_CLOSE_S);
    }

    function countdownText(bar, secs) {
        const close = barCloseAt(bar, secs), now = nowIst();
        if (!bar || close == null || now < bar.time || now >= close) return '';
        const left = close - now;
        const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
        const p2 = n => String(n).padStart(2, '0');
        return h ? `${h}:${p2(m)}:${p2(s)}` : `${p2(m)}:${p2(s)}`;
    }

    function attachCountdown(chart, series, opts) {
        opts = opts || {};
        if (!chart || !series) return null;
        try { series.applyOptions({ lastValueVisible: false }); } catch (e) {}
        const flag = (v, dflt) => v == null ? dflt : (typeof v === 'function' ? !!v() : !!v);
        const lastBar = () => {
            if (opts.lastBar) return opts.lastBar();
            const d = series.data();
            for (let i = d.length - 1; i >= 0; i--) if (d[i] && d[i].close != null) return d[i];
            return null;
        };
        const back = () => {
            const b = lastBar(), o = series.options() || {};
            const up = opts.upColor || o.upColor || '#26a69a', down = opts.downColor || o.downColor || '#ef5350';
            return b && b.close < b.open ? down : up;
        };
        let requestUpdate = null, price = '', text = '', lastText = null;
        const y = () => { const b = lastBar(); const c = b && series.priceToCoordinate(b.close); return c == null ? -100 : c; };
        const priceView = {
            coordinate: y, text: () => price, textColor: () => contrastText(back()), backColor: back,
            visible: () => !!price, tickVisible: () => true
        };
        const countView = {
            coordinate: () => y() + 16, text: () => text, textColor: () => contrastText(back()), backColor: back,
            visible: () => !!text, tickVisible: () => false
        };
        const compute = () => {
            const b = lastBar();
            price = b ? series.priceFormatter().format(b.close) : '';
            const cd = (b && flag(opts.countdown, true) && opts.interval != null) ? countdownText(b, intervalSeconds(opts.interval)) : '';
            const pad = Math.max(0, price.length - cd.length);
            text = cd ? '\u2007'.repeat(Math.ceil(pad / 2)) + cd + '\u2007'.repeat(Math.floor(pad / 2)) : '';
        };
        const primitive = {
            attached(p) { requestUpdate = p.requestUpdate; },
            detached() { requestUpdate = null; },
            updateAllViews() { compute(); lastText = text; },
            priceAxisViews: () => [priceView, countView],
            paneViews: () => [],
            // Only redraw when the countdown row actually changed — five
            // charts on one page, one repaint a second each, adds up.
            tick() { compute(); if (text !== lastText && requestUpdate) requestUpdate(); },
            refresh() { if (requestUpdate) requestUpdate(); }
        };
        series.attachPrimitive(primitive);
        const handle = {
            refresh: () => primitive.refresh(),
            detach() {
                _countdowns.delete(primitive);
                try { series.detachPrimitive(primitive); } catch (e) {}
                if (!_countdowns.size && _countdownTimer) { clearInterval(_countdownTimer); _countdownTimer = null; }
            }
        };
        _countdowns.add(primitive);
        if (!_countdownTimer) {
            _countdownTimer = setInterval(() => {
                if (document.hidden) return;
                for (const p of _countdowns) { try { p.tick(); } catch (e) {} }
            }, 1000);
        }
        return handle;
    }

    /**
     * Public API
     */
    return {
        /**
         * Creates a new chart instance
         * @param {Object} config - Configuration object
         * @param {string} config.containerId - ID of container div
         * @param {Array} config.data - Array of candlestick data {time, open, high, low, close}
         * @param {number} config.pdh - Previous day high
         * @param {number} config.pdl - Previous day low
         * @param {number} config.currentPrice - Current price (for status)
         * @param {string} config.type - 'CE' or 'PE' (for styling)
         * @param {string} config.timeframe - '1minute', '5minute', '15minute', '60minute'
         * @param {Object} config.options - Optional: { height: 400, width: '100%', theme: 'light' }
         * @param {string} config.ceColor - Optional: Color for CE candlesticks (default: '#10b981')
         * @param {string} config.peColor - Optional: Color for PE candlesticks (default: '#ef4444')
         * @returns {Object} Chart instance with methods
         */
        create: function (config) {
            const {
                containerId,
                data = [],
                pdh = null,
                pdl = null,
                cePdh = null,
                cePdl = null,
                pePdh = null,
                pePdl = null,
                currentPrice = null,
                type = 'CE',
                timeframe = '5minute',
                options = {},
                ceColor = '#10b981',
                peColor = '#ef4444'
            } = config;

            if (!containerId) {
                console.error('[Chart] Container ID is required');
                return null;
            }

            const container = document.getElementById(containerId);
            if (!container) {
                console.error(`[Chart] Container #${containerId} not found`);
                return null;
            }

            // Remove manual padding that might hide the price scale
            container.style.paddingRight = '0px';
            container.style.boxSizing = 'border-box';
            container.style.position = 'relative';

            const height = options.height || 400;
            const width = options.width || '100%';
            const theme = options.theme || 'light';
            const rightOffset = options.rightOffset != null ? options.rightOffset : 20;

            // Make pdh and pdl mutable for updatePdhPdl to work
            let mutablePdh = pdh;
            let mutablePdl = pdl;

            // Create chart with dynamic theme and IST timezone formatting
            const OIP_CHART_THEMES = {
                'light': { bg: '#ffffff', text: '#374151', grid: '#f0f0f0' },
                'dark': { bg: '#111827', text: '#94a3b8', grid: 'rgba(255, 255, 255, 0.06)' },
                'forest': { bg: '#0a1410', text: '#6ba88f', grid: 'rgba(16, 185, 129, 0.06)' },
                'cream': { bg: '#ffffff', text: '#7c7267', grid: 'rgba(180, 83, 9, 0.05)' },
                'ocean': { bg: '#ffffff', text: '#475569', grid: 'rgba(2, 132, 199, 0.05)' }
            };
            const activeTheme = window.AppTheme.getActiveTheme();
            const themeCfg = OIP_CHART_THEMES[activeTheme] || OIP_CHART_THEMES['dark'];

            const chart = LightweightCharts.createChart(container, {
                layout: {
                    textColor: themeCfg.text,           // Dynamic text
                    background: { type: 'solid', color: themeCfg.bg }  // Dynamic background
                },
                grid: {
                    vertLines: {
                        color: themeCfg.grid,           // Dynamic grid lines
                        style: 0                     // Solid lines
                    },
                    horzLines: {
                        color: themeCfg.grid,           // Dynamic grid lines
                        style: 0                     // Solid lines
                    }
                },
                // Both arms of the crosshair, and both readable.
                //
                // They were always both enabled, but at style 3 (LargeDashed) and
                // a fixed '#9ca3af' the horizontal arm was effectively invisible:
                // long dashes with long gaps, drawn straight across the candle
                // bodies and the horizontal grid lines it runs parallel to. The
                // vertical arm got away with the same styling because it crosses
                // mostly empty background and is anchored by a bold time label,
                // so the chart read as having only one arm.
                //
                // Style 2 (Dashed) keeps the dashed look with a tighter period,
                // and taking the colour from the active theme's text — already
                // tuned to be legible on that theme's background — fixes the
                // light/cream themes, where a mid-grey on white was faint even
                // vertically.
                crosshair: {
                    mode: 0,                        // Normal mode - follows cursor exactly (not snapping to candle)
                    vertLine: {
                        color: themeCfg.text,
                        width: 1,
                        style: 2,                   // Dashed
                        labelVisible: true
                    },
                    horzLine: {
                        color: themeCfg.text,
                        width: 1,
                        style: 2,                   // Dashed
                        labelVisible: true          // the price under the cursor, on the axis
                    }
                },
                timeScale: {
                    timeVisible: true,
                    secondsVisible: false,
                    textColor: '#6b7280',           // Medium grey text on timeScale
                    borderColor: 'transparent',     // Hide the border
                    rightOffset: rightOffset,        // Matched with OI Profile chart (overridable via options)
                    barSpacing: 4,                 // Half of OI chart spacing for compact option view
                    fixLeftEdge: false,             // Allow scrolling on left
                    fixRightEdge: false,            // Allow dragging to right side
                    shiftVisibleRangeOnNewBar: true
                },
                rightPriceScale: {
                    textColor: '#64748b',
                    borderColor: 'transparent',
                    // 85 is sized for the widest label the default 2dp formatter
                    // can produce. A chart using compactPriceAxis prints "-25L"
                    // instead of "-2500000.00" and doesn't need the room, so it
                    // can claim it back for the plot. Opt-in per chart for the
                    // same reason as the formatter — nothing else should move.
                    width: config.priceAxisWidth || 85,
                    autoScale: true,
                    visible: true,
                    scaleMargins: { top: 0, bottom: 0 },
                    entireTextOnly: true
                },
                // (v5: the watermark create-option was removed; it was text-less here anyway.)
                // Apply IST timezone formatter to x-axis
                localization: {
                    locale: 'en-IN',
                    // Chart-level, and it WINS over any per-series priceFormat —
                    // including type:'volume' and type:'custom'. Verified against
                    // lightweight-charts 5.2.1: a histogram declaring
                    // priceFormat:{type:'volume'} still renders its axis through
                    // this function, even though series.priceFormatter() reports
                    // the volume one. A pane wanting compact ticks therefore
                    // cannot get them from its series — it has to come from here.
                    //
                    // Opt-in per chart so nothing else moves: a chart that does
                    // not ask keeps the plain 2dp it has always had.
                    priceFormatter: config.compactPriceAxis
                        ? _tvCompactPrice
                        : val => val.toFixed(2),
                    timeFormatter: window.lwCrosshairTime,
                    timezone: 'Etc/UTC' // Use UTC to prevent double-shifting of already IST-shifted timestamps
                },
                height: height,
                width: (container.offsetWidth || 600)
            });

            // Create candlestick series
            // Default colors: Green for up candles, Red for down candles
            let upColor = '#1b9981';      // Green for bullish candles
            let downColor = '#f23645';    // Red for bearish candles

            const isLightTheme = (activeTheme === 'light' || activeTheme === 'cream' || activeTheme === 'ocean');

            if (type === 'PE') {
                upColor = '#8b5cf6';      // Violet for PE up
                downColor = isLightTheme ? '#1f2937' : '#6b7280'; // Black for light themes, Grey for dark themes
            }

            const borderUpColor = upColor;
            const borderDownColor = downColor;
            const wickUpColor = upColor;
            const wickDownColor = downColor;

            // For combined charts, create two series (CE and PE)
            let series = null;
            let ceSeries = null;
            let peSeries = null;

            if (type === 'COMBINED' || config.isCombined) {
                const customAutoscale = (seriesObj) => () => {
                    const data = seriesObj.data();
                    const range = chart.timeScale().getVisibleLogicalRange();
                    if (!data || data.length === 0 || !range) return null;
                    let min = Infinity, max = -Infinity;
                    const start = Math.max(0, Math.floor(range.from));
                    const end = Math.min(data.length - 1, Math.ceil(range.to));
                    for (let i = start; i <= end; i++) {
                        const c = data[i];
                        if (c && c.high !== undefined) {
                            if (c.high > max) max = c.high;
                            if (c.low < min) min = c.low;
                        }
                    }
                    if (min === Infinity) return null;
                    const pad = (max - min) * 0.1;
                    return { priceRange: { minValue: min - pad, maxValue: max + pad } };
                };

                // Create CE series (primary series - green and red)
                ceSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
                    upColor: '#1b9981',      // Green for CE up
                    downColor: '#f23645',    // Red for CE down
                    borderUpColor: '#1b9981',
                    borderDownColor: '#f23645',
                    wickUpColor: '#1b9981',
                    wickDownColor: '#f23645',
                    title: 'CE',
                    priceLineStyle: 1, // Dotted
                    priceLineWidth: 1
                });
                ceSeries.applyOptions({ autoscaleInfoProvider: customAutoscale(ceSeries) });
                lwBringToFront(ceSeries);

                // Create PE series (secondary series - violet/black or grey based on theme)
                const peDownColor = isLightTheme ? '#1f2937' : '#6b7280';
                peSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
                    upColor: '#8b5cf6',      // Violet for PE up
                    downColor: peDownColor,
                    borderUpColor: '#8b5cf6',
                    borderDownColor: peDownColor,
                    wickUpColor: '#8b5cf6',
                    wickDownColor: peDownColor,
                    title: 'PE',
                    priceLineStyle: 1, // Dotted
                    priceLineWidth: 1
                });
                peSeries.applyOptions({ autoscaleInfoProvider: customAutoscale(peSeries) });
                lwBringToFront(peSeries);

                // Create Sum Series (Line series for total premium)
                const sumSeries = chart.addSeries(LightweightCharts.LineSeries, {
                    color: '#6366f1', // Indigo for combined
                    lineWidth: 2,
                    title: 'TOTAL',
                    visible: false, // Hidden by default
                    priceLineVisible: true,
                    lastValueVisible: true
                });

                series = ceSeries; // Primary series for backward compatibility
                // Assign to instance later
            } else if (type === 'LINE') {
                // Single line series chart (for OI)
                const customAutoscaleLine = (seriesObj) => () => {
                    const data = seriesObj.data();
                    const range = chart.timeScale().getVisibleLogicalRange();
                    if (!data || data.length === 0 || !range) return null;
                    let min = Infinity, max = -Infinity;
                    const start = Math.max(0, Math.floor(range.from));
                    const end = Math.min(data.length - 1, Math.ceil(range.to));
                    for (let i = start; i <= end; i++) {
                        const c = data[i];
                        if (c && c.value !== undefined) {
                            if (c.value > max) max = c.value;
                            if (c.value < min) min = c.value;
                        }
                    }
                    if (min === Infinity) return null;
                    if (min === max) { min -= 1; max += 1; }
                    const pad = (max - min) * 0.1;
                    return { priceRange: { minValue: min - pad, maxValue: max + pad } };
                };

                series = chart.addSeries(LightweightCharts.LineSeries, {
                    color: config.lineColor || '#2962ff',
                    lineWidth: 2,
                    crosshairMarkerVisible: true
                });
                series.applyOptions({ autoscaleInfoProvider: customAutoscaleLine(series) });
            } else {
                series = chart.addSeries(LightweightCharts.CandlestickSeries, {
                    upColor: upColor,
                    downColor: downColor,
                    borderUpColor: borderUpColor,
                    borderDownColor: borderDownColor,
                    wickUpColor: wickUpColor,
                    wickDownColor: wickDownColor,
                    priceLineStyle: 1,
                    priceLineWidth: 1
                });
                lwBringToFront(series);
            }

            // Format and set data; strip whitespace for CandlestickSeries (LINE keeps them)
            const formattedData = formatChartData(data);
            const initialData = (type === 'LINE') ? formattedData : formattedData.filter(c => c.open !== undefined);
            if (initialData.length > 0) {
                series.setData(initialData);
            }

            // Invisible alignment series: carries ALL timestamps (including positions where
            // the option had no trade) so scrollToPosition sync lands at the same clock time
            // as the OI chart. CandlestickSeries is OHLC-only (no whitespace) to prevent
            // LC v4.1.1 Candlestick renderer crashes; this LineSeries fills the gap.
            let alignSeries = null;
            if (type !== 'LINE') {
                alignSeries = chart.addSeries(LightweightCharts.LineSeries, {
                    visible: false,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null
                });
                if (formattedData.length > 0) {
                    try { alignSeries.setData(formattedData.map(c => ({ time: c.time, value: 0 }))); } catch(e) {}
                }
            }

            // Price lines array to store reference lines (no PDH/PDL lines)
            const priceLinesArray = [];

            // Horizontal Ray drawing tool — see createRayTool() above for the
            // implementation notes (2-point LineSeries, reach vs. pan/zoom trade-off).
            const rayPriceSeries = ceSeries || series;
            const rayTool = createRayTool(chart, rayPriceSeries, container, {
                timeframe: timeframe,
                rightOffset: rightOffset,
                rayColor: config.rayColor,
                onRayDrawn: config.onRayDrawn,
                onRayRemoved: config.onRayRemoved,
                reapplyZOrder: config.reapplyZOrder
            });

            // Store PDH/PDL values for status calculation
            let storedCePdh = null;
            let storedCePdl = null;
            let storedPePdh = null;
            let storedPePdl = null;

            // Set visible range to show recent data (increased x-axis scaling/zoom)
            // Use a Promise to ensure the data is fully set before applying zoom
            if (formattedData.length > 0) {
                // Schedule the zoom after the chart has rendered the data
                setTimeout(() => {
                    try {
                        const lastCandle = formattedData[formattedData.length - 1];
                        // Use 150 candles for combined charts, 100 for others
                        const candleCount = (type === 'COMBINED' || config.isCombined) ? 150 : 100;
                        const firstVisibleIndex = Math.max(0, formattedData.length - candleCount);
                        const firstVisibleCandle = formattedData[firstVisibleIndex];

                        // Global scale options apply automatically

                        chart.timeScale().setVisibleRange({
                            from: firstVisibleCandle.time,
                            to: lastCandle.time
                        }, true); // true = animates to the range

                    } catch (error) {
                        console.warn('Error setting visible range:', error);
                    }
                }, 100); // Small delay to let chart render
            }

            // Add "Scroll to Latest" button
            addScrollButton(chart, series, container);

            // Price + bar-close countdown on the axis. A combined chart's PE
            // series gets the same drawn tag (one look for both) but only the
            // CE row counts down — the bar is the same bar.
            const countdownHandles = [];
            if (type !== 'LINE') {
                countdownHandles.push(attachCountdown(chart, series, { interval: timeframe }));
                if (peSeries) countdownHandles.push(attachCountdown(chart, peSeries, { interval: timeframe, countdown: false }));
            }

            // Add cursor change on hover over candles/lines

            // Add cursor change on hover over candles/lines
            // When hovering over candles or reference lines, show pointer cursor (clickable)
            // Otherwise show crosshair cursor
            chart.subscribeClick((param) => {
                // On click, check if cursor is over a candle or line
                if (param && param.time) {
                    container.style.cursor = 'pointer';
                }
            });

            // Subscribe to cursor movement to change cursor based on what's under it
            chart.subscribeCrosshairMove((param) => {
                if (!param || !param.point) {
                    // No data at cursor, show default crosshair
                    container.style.cursor = 'crosshair';
                    return;
                }

                // Check if hovering over series data (candles)
                if (param.seriesPrices && param.seriesPrices.size > 0) {
                    // Hovering over a candle - show pointer
                    container.style.cursor = 'pointer';
                } else {
                    // Not hovering over candle - show crosshair
                    container.style.cursor = 'crosshair';
                }
            });

            // Track if initialization has been done (to prevent re-initialization on updates)
            let isInitialized = false;

            // Listen for theme changes dynamically to update candle colors in real time!
            window.addEventListener('themechanged', function (e) {
                const newTheme = e.detail.theme;
                const isLight = (newTheme === 'light' || newTheme === 'cream' || newTheme === 'ocean');
                const peDownCol = isLight ? '#1f2937' : '#6b7280';
                
                if (type === 'PE' && series) {
                    series.applyOptions({
                        downColor: peDownCol,
                        borderDownColor: peDownCol,
                        wickDownColor: peDownCol
                    });
                }
                
                if (peSeries) {
                    peSeries.applyOptions({
                        downColor: peDownCol,
                        borderDownColor: peDownCol,
                        wickDownColor: peDownCol
                    });
                }
            });

            // Return public interface
            return {
                chart: chart,
                series: series,
                ceSeries: ceSeries,
                peSeries: peSeries,
                sumSeries: typeof sumSeries !== 'undefined' ? sumSeries : null,
                alignSeries: alignSeries,
                priceLinesArray: priceLinesArray,
                data: formattedData,
                isCombined: type === 'COMBINED' || config.isCombined,
                chartType: type,  // Store chart type for status calculation

                /**
                 * Controls visibility of series
                 */
                setVisibleSeries: function (ceVisible, peVisible) {
                    if (this.ceSeries) this.ceSeries.applyOptions({ visible: ceVisible });
                    if (this.peSeries) this.peSeries.applyOptions({ visible: peVisible });
                    // If not separate series, handle the main series
                    if (!this.ceSeries && this.series) {
                        this.series.applyOptions({ visible: ceVisible || peVisible });
                    }
                },

                /**
                 * Sets markers (signals) on the chart series
                 */
                setMarkers: function (ceMarkers, peMarkers = []) {
                    try {
                        if (this.isCombined) {
                            if (this.ceSeries) lwSetMarkers(this.ceSeries, ceMarkers || []);
                            if (this.peSeries) lwSetMarkers(this.peSeries, peMarkers || []);
                        } else if (this.series) {
                            lwSetMarkers(this.series, ceMarkers || peMarkers || []);
                        }
                    } catch (e) {
                        console.warn('[Chart] Error setting markers:', e);
                    }
                },

                /**
                 * Updates chart with new data
                 * For combined charts: update(ceData, peData)
                 * For single charts: update(data)
                 *
                 * NOTE: Chart settings (zoom, timeScale, etc.) are initialized ONCE on creation
                 * Subsequent updates only modify the data
                 */
                update: function (newData, referenceOrPeData = null, refresh = false) {
                    // Check if this is a combined chart with PE data (array of candles)
                    const isCombinedUpdate = this.isCombined && referenceOrPeData && Array.isArray(referenceOrPeData);

                    // If refresh flag is true, clear existing price lines and reset Y-scale
                    if (refresh) {
                        console.log('[Chart] Refresh mode: resetting price scale');
                        
                        // Reset Y-axis only to jump to new price levels
                        try {
                            chart.priceScale('right').applyOptions({ autoScale: true });
                        } catch (e) { console.warn('[Chart] Reset scale err:', e); }

                        // Remove all existing price lines
                        if (priceLinesArray && priceLinesArray.length > 0) {
                            priceLinesArray.forEach(line => {
                                try { if (series) series.removePriceLine(line); } catch (e) { }
                                try { if (ceSeries) ceSeries.removePriceLine(line); } catch (e) { }
                                try { if (peSeries) peSeries.removePriceLine(line); } catch (e) { }
                            });
                            // Clear the array without reassigning (avoid const violation)
                            priceLinesArray.splice(0, priceLinesArray.length);
                        }
                    }

                    if (this.isCombined) {
                        // Combined chart: update both CE and PE series.
                        // Strip whitespace ({time}-only) entries before setData — LC v4.1.1 crashes
                        // in its Candlestick renderer when open === undefined. The alignSeries
                        // (invisible LineSeries) carries all timestamps so scrollToPosition sync
                        // still lands at the correct clock time on every chart.
                        const ceRawAll = newData ? formatChartData(newData) : [];
                        const peRawAll = (referenceOrPeData && Array.isArray(referenceOrPeData)) ? formatChartData(referenceOrPeData) : [];
                        const ceFormatted = ceRawAll.filter(c => c.open !== undefined);
                        const peFormatted = peRawAll.filter(c => c.open !== undefined);

                        if (ceSeries && ceFormatted.length) {
                            try { ceSeries.setData(ceFormatted); } catch(e) {}
                            this.data = ceFormatted;
                        }
                        if (peSeries && peFormatted.length) {
                            try { peSeries.setData(peFormatted); } catch(e) {}
                        }
                        // Keep alignment series in step — use CE timestamps (CE and PE share the
                        // same OI-aligned timestamps so either set works).
                        if (alignSeries && ceRawAll.length) {
                            try { alignSeries.setData(ceRawAll.map(c => ({ time: c.time, value: 0 }))); } catch(e) {}
                        }

                    } else if (type === 'LINE') {
                        // Line chart update - handle both value data and whitespace
                        const lineData = newData.map(item => {
                            let time = item.time;
                            if (!time && item.timestamp) {
                                time = Math.floor(new Date(item.timestamp).getTime() / 1000);
                            }

                            // Handle whitespace (no value)
                            if (item.value === undefined) {
                                return { time };
                            }

                            return {
                                time,
                                value: parseFloat(item.value),
                                ...(item.color && { color: item.color })
                            };
                        }).filter(item => item && !isNaN(item.time)).sort((a, b) => a.time - b.time);

                        if (lineData.length > 0) {
                            series.setData(lineData);
                            this.data = lineData;
                        }
                    } else {
                        // Single Candlestick series chart.
                        // Strip whitespace — same rationale as the COMBINED path above.
                        // alignSeries receives all timestamps so bar-count alignment is preserved.
                        const allFormatted = formatChartData(newData);
                        const updatedData = allFormatted.filter(c => c.open !== undefined);
                        if (updatedData.length) {
                            try { series.setData(updatedData); } catch(e) {}
                            this.data = updatedData;
                        }
                        if (alignSeries && allFormatted.length) {
                            try { alignSeries.setData(allFormatted.map(c => ({ time: c.time, value: 0 }))); } catch(e) {}
                        }
                    }

                    // Keep drawn rays reaching the newest candle instead of
                    // stopping wherever they were when drawn.
                    try { rayTool.extendRays(); } catch (e) {}

                    // Recalculate zoom and timeScale if refresh is requested (e.g. on symbol switch)
                    // Recalculate zoom and timeScale if refresh is requested (e.g. on symbol switch)
                    if (refresh) {
                        // Scaling and zoom levels are now handled centrally by the master dashboard logic
                        // to prevent race conditions during multi-chart synchronization.
                    }
                },

                /**
                 * Gets latest price from chart data
                 */
                getLatestPrice: function () {
                    return getLatestPrice(this.data);
                },

                /**
                 * Gets status (WIN/LOSS/SIDEWAY) based on latest price vs PDH/PDL
                 * Status logic:
                 * - CE chart: Compare CE price with PE PDH/PDL
                 *   - Price > PE PDH = WIN
                 *   - PE PDH >= Price >= PE PDL = SIDEWAY
                 *   - Price < PE PDL = LOSS
                 * - PE chart: Compare PE price with CE PDH/PDL
                 *   - Price > CE PDH = WIN
                 *   - CE PDH >= Price >= CE PDL = SIDEWAY
                 *   - Price < CE PDL = LOSS
                 */
                getStatus: function () {
                    const latestPrice = this.getLatestPrice();

                    if (latestPrice === null || latestPrice === undefined) {
                        return { text: '--', className: 'status-na' };
                    }

                    // Get the comparison PDH/PDL based on chart type
                    let comparisonPdh = null;
                    let comparisonPdl = null;

                    if (type === 'CE') {
                        // CE chart: use PE PDH/PDL for comparison
                        comparisonPdh = storedPePdh;
                        comparisonPdl = storedPePdl;
                    } else if (type === 'PE') {
                        // PE chart: use CE PDH/PDL for comparison
                        comparisonPdh = storedCePdh;
                        comparisonPdl = storedCePdl;
                    } else {
                        // Unknown chart type
                        return { text: '--', className: 'status-na' };
                    }

                    // Check if we have valid PDH/PDL values
                    if (comparisonPdh === null || comparisonPdl === null ||
                        comparisonPdh === undefined || comparisonPdl === undefined ||
                        comparisonPdh <= 0 || comparisonPdl <= 0) {
                        return { text: '--', className: 'status-na' };
                    }

                    // Calculate status based on price vs PDH/PDL
                    let status = 'sideway';
                    let className = 'status-sideway';

                    if (latestPrice > comparisonPdh) {
                        status = 'WIN';
                        className = 'status-win';
                    } else if (latestPrice < comparisonPdl) {
                        status = 'LOSS';
                        className = 'status-loss';
                    } else {
                        // Between PDL and PDH
                        status = 'SIDEWAY';
                        className = 'status-sideway';
                    }

                    return { text: status, className: className };
                },

                /**
                 * Adds a price line to the chart
                 */
                addPriceLine: function (price, color, label) {
                    try {
                        const line = series.createPriceLine({
                            price: price,
                            color: color,
                            lineWidth: 1,
                            lineStyle: LightweightCharts.LineStyle.Solid,
                            axisLabelVisible: true,
                            title: label || price.toFixed(2)
                        });
                        this.priceLinesArray.push(line);
                        return line;
                    } catch (e) {
                        console.warn('[Chart] Failed to add price line:', e);
                        return null;
                    }
                },

                /**
                 * Removes a specific price line
                 */
                removePriceLine: function (line) {
                    try {
                        series.removePriceLine(line);
                        this.priceLinesArray = this.priceLinesArray.filter(pl => pl !== line);
                    } catch (e) {
                        console.warn('[Chart] Failed to remove price line:', e);
                    }
                },

                /**
                 * Clears all price lines
                 */
                clearPriceLines: function () {
                    this.priceLinesArray.forEach(line => {
                        try {
                            series.removePriceLine(line);
                        } catch (e) { }
                    });
                    this.priceLinesArray = [];
                },

                /**
                 * Arms/disarms the horizontal-ray draw tool. While armed, the next
                 * click on this chart drops a ray and auto-disarms.
                 * @param {boolean} active
                 * @param {{color?: string, width?: number, lineStyle?: number}} [style] -
                 *   Overrides the style used for the NEXT ray only; rays already drawn
                 *   are unaffected. Any field omitted keeps its previous value.
                 */
                setRayMode: function (active, style) {
                    rayTool.setRayMode(active, style);
                },

                isRayModeActive: function () {
                    return rayTool.isRayModeActive();
                },

                /**
                 * Programmatically draws a ray without arming/clicking — used to
                 * restore rays a caller persisted (e.g. to localStorage) on page
                 * load. `style` defaults to the tool's current style if omitted.
                 * @param {number} time
                 * @param {number} price
                 * @param {{color?: string, width?: number, lineStyle?: number}} [style]
                 */
                addRay: function (time, price, style) {
                    return rayTool.addRay(time, price, style);
                },

                /**
                 * Removes all ray lines drawn on this chart.
                 */
                clearRays: function () {
                    rayTool.clearRays();
                },

                /**
                 * Re-anchors every drawn ray's end point to the latest loaded
                 * candle. Called automatically at the end of update().
                 */
                extendRays: function () {
                    rayTool.extendRays();
                },

                /**
                 * Resizes chart to fit container
                 */
                resize: function () {
                    if (container && container.offsetWidth > 0) {
                        chart.applyOptions({
                            width: container.offsetWidth
                        });
                    }
                },

                /**
                 * Sets the zoom level of the chart
                 * @param {number} candleCount - Number of candles to display (default: 100)
                 */
                setZoom: function (candleCount = 100) {
                    try {
                        const allData = this.data || [];
                        if (allData.length === 0) {
                            console.warn('[Chart] No data available to zoom');
                            return;
                        }

                        const lastCandle = allData[allData.length - 1];
                        const firstVisibleIndex = Math.max(0, allData.length - candleCount);
                        const firstVisibleCandle = allData[firstVisibleIndex];

                        // Apply zoom with proper timing
                        setTimeout(() => {
                            chart.timeScale().setVisibleRange({
                                from: firstVisibleCandle.time,
                                to: lastCandle.time
                            }, true); // true = animate to range
                        }, 50);
                    } catch (error) {
                        console.warn('[Chart] Error setting zoom:', error);
                    }
                },

                /**
                 * Resets chart to show all data (removes zoom)
                 */
                resetZoom: function () {
                    try {
                        chart.timeScale().fitContent();
                    } catch (error) {
                        console.warn('[Chart] Error resetting zoom:', error);
                    }
                },

                /**
                 * Updates PDH/PDL reference lines on the chart
                 * Used by options_chart_app.js to show previous day high/low levels
                 * @param {number} cePdh - CE Previous Day High
                 * @param {number} cePdl - CE Previous Day Low
                 * @param {number} pePdh - PE Previous Day High
                 * @param {number} pePdl - PE Previous Day Low
                 */
                updatePdhPdl: function (cePdh, cePdl, pePdh, pePdl) {
                    try {
                        // Store PDH/PDL values for getStatus() calculation
                        storedCePdh = cePdh;
                        storedCePdl = cePdl;
                        storedPePdh = pePdh;
                        storedPePdl = pePdl;

                        // Remove existing PDH/PDL lines (first 4 indices)
                        if (this.priceLinesArray && this.priceLinesArray.length > 0) {
                            for (let i = Math.min(3, this.priceLinesArray.length - 1); i >= 0; i--) {
                                try {
                                    series.removePriceLine(this.priceLinesArray[i]);
                                } catch (e) {
                                    console.warn('[updatePdhPdl] Error removing line:', e);
                                }
                            }
                            // Keep only reference lines (remove first 4 PDH/PDL lines)
                            this.priceLinesArray = this.priceLinesArray.slice(4);
                        }

                        console.log('[updatePdhPdl] Adding PDH/PDL lines:', { cePdh, cePdl, pePdh, pePdl });

                        // Add new PDH/PDL lines based on chart type
                        if (type === 'CE') {
                            // CE chart: show CE PDH and PDL
                            if (cePdh && cePdh > 0) {
                                const cePdhLine = series.createPriceLine({
                                    price: cePdh,
                                    color: '#9ca3af',  // Grey
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `CE PDH`
                                });
                                this.priceLinesArray.unshift(cePdhLine);
                            }
                            if (cePdl && cePdl > 0) {
                                const cePdlLine = series.createPriceLine({
                                    price: cePdl,
                                    color: '#9ca3af',  // Grey
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `CE PDL`
                                });
                                this.priceLinesArray.unshift(cePdlLine);
                            }
                            if (pePdh && pePdh > 0) {
                                const pePdhLine = series.createPriceLine({
                                    price: pePdh,
                                    color: '#10b981',  // Green
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `PE PDH`
                                });
                                this.priceLinesArray.unshift(pePdhLine);
                            }
                            if (pePdl && pePdl > 0) {
                                const pePdlLine = series.createPriceLine({
                                    price: pePdl,
                                    color: '#ef4444',  // Red
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `PE PDL`
                                });
                                this.priceLinesArray.unshift(pePdlLine);
                            }
                            console.log('[CE Chart] Updated PDH/PDL lines');
                        } else if (type === 'PE') {
                            // PE chart: show PE PDH and PDL
                            if (pePdh && pePdh > 0) {
                                const pePdhLine = series.createPriceLine({
                                    price: pePdh,
                                    color: '#9ca3af',  // Grey
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `PE PDH`
                                });
                                this.priceLinesArray.unshift(pePdhLine);
                            }
                            if (pePdl && pePdl > 0) {
                                const pePdlLine = series.createPriceLine({
                                    price: pePdl,
                                    color: '#9ca3af',  // Grey
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `PE PDL`
                                });
                                this.priceLinesArray.unshift(pePdlLine);
                            }
                            if (cePdh && cePdh > 0) {
                                const cePdhLine = series.createPriceLine({
                                    price: cePdh,
                                    color: '#10b981',  // Green
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `CE PDH`
                                });
                                this.priceLinesArray.unshift(cePdhLine);
                            }
                            if (cePdl && cePdl > 0) {
                                const cePdlLine = series.createPriceLine({
                                    price: cePdl,
                                    color: '#ef4444',  // Red
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `CE PDL`
                                });
                                this.priceLinesArray.unshift(cePdlLine);
                            }
                            console.log('[PE Chart] Updated PDH/PDL lines');
                        }
                    } catch (e) {
                        console.warn('[updatePdhPdl] Error updating PDH/PDL lines:', e);
                    }
                },

                /**
                 * Destroys chart and cleans up
                 */
                destroy: function () {
                    try {
                        this.clearPriceLines();
                        this.clearRays();
                        countdownHandles.forEach(h => { try { h && h.detach(); } catch (e) {} });
                        chart.remove();
                    } catch (e) {
                        console.warn('[Chart] Error during cleanup:', e);
                    }
                }
            };
        },

        /**
         * Utility: Format raw data
         */
        formatData: formatChartData,

        /**
         * Utility: Evaluate status
         */
        evaluateStatus: evaluateStatus,

        /**
         * Utility: Get latest price
         */
        getLatestPrice: getLatestPrice,

        /**
         * Utility: Add Scroll to Right button
         */
        addScrollButton: addScrollButton,

        /**
         * Utility: Attach the horizontal-ray draw tool to any chart+series pair,
         * including charts NOT created via TradingViewChart.create() — e.g. the
         * main OI Profile candlestick chart, built directly against the raw
         * LightweightCharts API. Returns { setRayMode, isRayModeActive, addRay, clearRays }.
         * @param {Object} chart - LightweightCharts chart instance
         * @param {Object} series - price series used to convert click Y-coordinate to price
         * @param {HTMLElement} container - chart's container element (for cursor + right-click removal)
         * @param {Object} [opts] - { timeframe, rightOffset, rayColor, onRayDrawn, onRayRemoved }
         */
        attachRayTool: createRayTool,

        /**
         * Utility: timeframe key -> seconds ('5minute' -> 300), shared vocabulary.
         */
        INTERVAL_SECONDS: INTERVAL_SECONDS,
        intervalSeconds: intervalSeconds,

        /**
         * Utility: device-pixel-snapped canvas drawing kit for primitives —
         * hline / segment / border / width / dash. See `crisp` above.
         */
        crisp: crisp,
        contrastText: contrastText,

        /**
         * Utility: a pixel-snapped LineSeries stand-in for flat level lines.
         * Returns a handle with the ISeriesApi subset the pages use; remove it
         * with TradingViewChart.removeSeries(chart, handle).
         */
        addCrispLine: addCrispLine,
        removeSeries: removeSeries,

        /**
         * Utility: price + bar-close countdown block on the price axis for any
         * chart+series pair. Returns { refresh, detach }.
         * @param {Object} opts - { interval, lastBar, countdown, upColor, downColor }
         */
        attachCountdown: attachCountdown
    };

    /**
     * Internal helper to add the "Scroll to Latest" button to a chart
     */
    function addScrollButton(chart, series, container) {
        if (!chart || !container) return;

        const existingPos = window.getComputedStyle(container).position;
        if (!existingPos || existingPos === 'static') {
            container.style.position = 'relative';
        }

        const scrollBtn = document.createElement('div');
        scrollBtn.className = 'tv-chart-scroll-btn';
        // Official TradingView SVG icon (14×14 viewBox, matches JSFiddle reference)
        scrollBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14" width="14" height="14"><path fill="currentColor" d="M4.438 11.375L8.813 7 4.438 2.625l1.124-1.125L11.063 7l-5.5 5.5z"/></svg>`;
        container.appendChild(scrollBtn);

        scrollBtn.onclick = (e) => {
            e.stopPropagation();
            chart.timeScale().scrollToRealTime();
        };

        // Official pattern from TradingView JSFiddle:
        // scrollPosition() is the rightOffset — negative means user scrolled left
        // (latest bar is off-screen to the right), so show the "go to realtime" button.
        const updateVisibility = () => {
            try {
                const visible = chart.timeScale().scrollPosition() < 0;
                scrollBtn.classList.toggle('show', visible);
            } catch (_) {}
        };

        chart.timeScale().subscribeVisibleLogicalRangeChange(updateVisibility);
    }
})();
