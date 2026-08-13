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

// v5 z-order helper — lifts the candle series above overlay indicators so it
// renders on top (v5 added ISeriesApi.setSeriesOrder; higher index = on top).
// A large index is clamped to the current top of the pane's series collection.
window.lwBringToFront = window.lwBringToFront || function (series) {
    try { if (series && typeof series.setSeriesOrder === 'function') series.setSeriesOrder(1e6); } catch (e) {}
};

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
        const RAY_INTERVAL_SECONDS = {
            '30second': 30, minute: 60, '2minute': 120, '3minute': 180, '5minute': 300,
            '15minute': 900, '30minute': 1800, '60minute': 3600, day: 86400, week: 604800, month: 2592000
        };
        function resolveTimeframe() {
            return typeof opts.timeframe === 'function' ? opts.timeframe() : opts.timeframe;
        }
        function buildRayPoints(startTime, price, lastRealTime) {
            const step = RAY_INTERVAL_SECONDS[resolveTimeframe()] || 60;
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
                crosshair: {
                    mode: 0,                        // Normal mode - follows cursor exactly (not snapping to candle)
                    vertLine: {
                        color: '#9ca3af',           // Lighter grey crosshair vertical
                        width: 1,                   // Slightly thicker for better visibility
                        style: 3                     // Large dashed line style
                    },
                    horzLine: {
                        color: '#9ca3af',           // Lighter grey crosshair horizontal
                        width: 1,                   // Slightly thicker for better visibility
                        style: 3                     // Large dashed line style
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
                    width: 85,
                    autoScale: true,
                    visible: true,
                    scaleMargins: { top: 0, bottom: 0 },
                    entireTextOnly: true
                },
                // (v5: the watermark create-option was removed; it was text-less here anyway.)
                // Apply IST timezone formatter to x-axis
                localization: {
                    locale: 'en-IN',
                    priceFormatter: val => val.toFixed(2),
                    timeFormatter: t => {
                        const d = new Date(t * 1000);
                        const h = String(d.getUTCHours()).padStart(2, '0');
                        const m = String(d.getUTCMinutes()).padStart(2, '0');
                        return `${h}:${m}`;
                    },
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
        attachRayTool: createRayTool
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
