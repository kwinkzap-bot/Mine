/**
 * TradingView Lightweight Charts - Reusable Module
 * Provides a clean API for creating and managing candlestick charts
 * Used across multiple pages: options_chart.html, intraday_option.html, etc.
 */

window.TradingViewChart = (function () {
    'use strict';

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
         * @param {number} config.ce_high - Optional: CE intraday high (from symbol-payload)
         * @param {number} config.ce_low - Optional: CE intraday low (from symbol-payload)
         * @param {number} config.pe_high - Optional: PE intraday high (from symbol-payload)
         * @param {number} config.pe_low - Optional: PE intraday low (from symbol-payload)
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
                peColor = '#ef4444',
                ce_high = null,
                ce_low = null,
                pe_high = null,
                pe_low = null
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
            const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'ocean';
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
                    rightOffset: 20,                 // Matched with OI Profile chart
                    barSpacing: 8,                 // Increased zoom to match user preference
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
                watermark: {
                    color: '#d1d5db'                // Light grey watermark
                },
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
                width: window.innerWidth > 600 ? container.offsetWidth : container.offsetWidth
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
                ceSeries = chart.addCandlestickSeries({
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

                // Create PE series (secondary series - violet/black or grey based on theme)
                const peDownColor = isLightTheme ? '#1f2937' : '#6b7280';
                peSeries = chart.addCandlestickSeries({
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

                // Create Sum Series (Line series for total premium)
                const sumSeries = chart.addLineSeries({
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

                series = chart.addLineSeries({
                    color: config.lineColor || '#2962ff',
                    lineWidth: 2,
                    crosshairMarkerVisible: true
                });
                series.applyOptions({ autoscaleInfoProvider: customAutoscaleLine(series) });
            } else {
                series = chart.addCandlestickSeries({
                    upColor: upColor,
                    downColor: downColor,
                    borderUpColor: borderUpColor,
                    borderDownColor: borderDownColor,
                    wickUpColor: wickUpColor,
                    wickDownColor: wickDownColor,
                    priceLineStyle: 1,
                    priceLineWidth: 1
                });
            }

            // Format and set data
            const formattedData = formatChartData(data);
            if (formattedData.length > 0) {
                series.setData(formattedData);
            }

            // Price lines array to store reference lines (no PDH/PDL lines)
            const priceLinesArray = [];

            // Store PDH/PDL values for status calculation
            let storedCePdh = null;
            let storedCePdl = null;
            let storedPePdh = null;
            let storedPePdl = null;

            // Add initial CE/PE high/low lines if provided
            // CE High/Low lines (solid, dotted for differentiation)
            if (ce_high && ce_high > 0) {
                try {
                    const ceHighLine = series.createPriceLine({
                        price: ce_high,
                        color: '#6366f1',  // Indigo for CE High
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: `CE High: ${ce_high.toFixed(2)}`
                    });
                    priceLinesArray.push(ceHighLine);
                    console.log(`[Chart Init] Added CE High: ${ce_high}`);
                } catch (e) {
                    console.warn('[Chart] Failed to add CE High line:', e);
                }
            }

            if (ce_low && ce_low > 0) {
                try {
                    const ceLowLine = series.createPriceLine({
                        price: ce_low,
                        color: '#8b5cf6',  // Purple for CE Low
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: true,
                        title: `CE Low: ${ce_low.toFixed(2)}`
                    });
                    priceLinesArray.push(ceLowLine);
                    console.log(`[Chart Init] Added CE Low: ${ce_low}`);
                } catch (e) {
                    console.warn('[Chart] Failed to add CE Low line:', e);
                }
            }

            // PE High/Low lines
            if (pe_high && pe_high > 0) {
                try {
                    const peHighLine = series.createPriceLine({
                        price: pe_high,
                        color: '#06b6d4',  // Cyan for PE High
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: `PE High: ${pe_high.toFixed(2)}`
                    });
                    priceLinesArray.push(peHighLine);
                    console.log(`[Chart Init] Added PE High: ${pe_high}`);
                } catch (e) {
                    console.warn('[Chart] Failed to add PE High line:', e);
                }
            }

            if (pe_low && pe_low > 0) {
                try {
                    const peLowLine = series.createPriceLine({
                        price: pe_low,
                        color: '#14b8a6',  // Teal for PE Low
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Dotted,
                        axisLabelVisible: true,
                        title: `PE Low: ${pe_low.toFixed(2)}`
                    });
                    priceLinesArray.push(peLowLine);
                    console.log(`[Chart Init] Added PE Low: ${pe_low}`);
                } catch (e) {
                    console.warn('[Chart] Failed to add PE Low line:', e);
                }
            }

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
                            if (this.ceSeries) this.ceSeries.setMarkers(ceMarkers || []);
                            if (this.peSeries) this.peSeries.setMarkers(peMarkers || []);
                        } else if (this.series) {
                            this.series.setMarkers(ceMarkers || peMarkers || []);
                        }
                    } catch (e) {
                        console.warn('[Chart] Error setting markers:', e);
                    }
                },

                /**
                 * Updates chart with new data and reference lines
                 * For combined charts: update(ceData, peData)
                 * For single charts: update(data, referenceLines)
                 *   where referenceLines = { ce_payload_high, ce_payload_low, pe_payload_high, pe_payload_low }
                 * 
                 * NOTE: Chart settings (zoom, timeScale, etc.) are initialized ONCE on creation
                 * Subsequent updates only modify the data and reference lines
                 */
                update: function (newData, referenceOrPeData = null, refresh = false) {
                    // Check if this is a combined chart with PE data (array of candles)
                    const isCombinedUpdate = this.isCombined && referenceOrPeData && Array.isArray(referenceOrPeData);
                    // Or check if it's a reference object (has price-level properties)
                    const isReferenceUpdate = referenceOrPeData && typeof referenceOrPeData === 'object' && !Array.isArray(referenceOrPeData) &&
                        (referenceOrPeData.ce_payload_high !== undefined || referenceOrPeData.pe_payload_high !== undefined ||
                            referenceOrPeData.ce_payload_low !== undefined || referenceOrPeData.pe_payload_low !== undefined);

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
                        // Combined chart: update both CE and PE series
                        const ceFormatted = newData ? formatChartData(newData) : [];
                        const peFormatted = (referenceOrPeData && Array.isArray(referenceOrPeData)) ? formatChartData(referenceOrPeData) : [];

                        if (ceSeries) {
                            ceSeries.setData(ceFormatted);
                            if (ceFormatted.length > 0) this.data = ceFormatted;
                        }
                        if (peSeries) {
                            peSeries.setData(peFormatted);
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
                        // Single series chart
                        const updatedData = formatChartData(newData);
                        if (updatedData.length > 0) {
                            series.setData(updatedData);
                            this.data = updatedData;
                        }
                    }

                    // Add cross-leg reference lines if provided (for intraday option charts)
                    if (isReferenceUpdate) {
                        this.addReferenceLines(referenceOrPeData);
                    }

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
                 * Adds cross-leg reference lines to the chart
                 * For CE chart: adds PE day high (green) and PE day low (red)
                 * For PE chart: adds CE day high (green) and CE day low (red)
                 * @param {Object} references - { ce_day_high, ce_day_low, pe_day_high, pe_day_low, ... }
                 */
                addReferenceLines: function (references) {
                    if (!references || typeof references !== 'object') {
                        console.log('[addReferenceLines] Skipped - no valid reference object:', references);
                        return;
                    }

                    console.log('[addReferenceLines] Called with:', references);

                    try {
                        // Clear previous reference lines (keep PDH/PDL lines, remove only reference lines)
                        // Expected structure: PDH/PDL lines are at indices 0-3 (ce_pdh, ce_pdl, pe_pdh, pe_pdl)
                        // Reference lines (PE High/Low or CE High/Low) are added after that
                        if (this.priceLinesArray && this.priceLinesArray.length > 4) {
                            // Remove all lines starting from index 4 (reference lines only)
                            for (let i = this.priceLinesArray.length - 1; i >= 4; i--) {
                                try {
                                    series.removePriceLine(this.priceLinesArray[i]);
                                    this.priceLinesArray.pop();  // Remove from end to avoid index issues
                                } catch (e) {
                                    console.warn('[addReferenceLines] Error removing line at index', i, ':', e);
                                }
                            }
                        }

                        console.log(`[addReferenceLines] Cleared previous reference lines, keeping ${this.priceLinesArray.length} PDH/PDL lines`);

                        // Determine which type of reference lines to add based on chart type
                        if (type === 'CE') {
                            // CE chart: show PE High and PE Low (from symbol payload)
                            if (references.pe_payload_high && references.pe_payload_high > 0) {
                                const peHighLine = series.createPriceLine({
                                    price: references.pe_payload_high,
                                    color: '#10b981',  // Green
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: 'PE High'
                                });
                                this.priceLinesArray.push(peHighLine);
                                console.log(`[CE Chart] Added PE High: ${references.pe_payload_high}`);
                            }
                            if (references.pe_payload_low && references.pe_payload_low > 0) {
                                const peLowLine = series.createPriceLine({
                                    price: references.pe_payload_low,
                                    color: '#ef4444',  // Red
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: 'PE Low'
                                });
                                this.priceLinesArray.push(peLowLine);
                                console.log(`[CE Chart] Added PE Low: ${references.pe_payload_low}`);
                            }
                        } else if (type === 'PE') {
                            // PE chart: show CE High and CE Low (from symbol payload)
                            if (references.ce_payload_high && references.ce_payload_high > 0) {
                                const ceHighLine = series.createPriceLine({
                                    price: references.ce_payload_high,
                                    color: '#10b981',  // Green
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: 'CE High'
                                });
                                this.priceLinesArray.push(ceHighLine);
                                console.log(`[PE Chart] Added CE High: ${references.ce_payload_high}`);
                            }
                            if (references.ce_payload_low && references.ce_payload_low > 0) {
                                const ceLowLine = series.createPriceLine({
                                    price: references.ce_payload_low,
                                    color: '#ef4444',  // Red
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: 'CE Low'
                                });
                                this.priceLinesArray.push(ceLowLine);
                                console.log(`[PE Chart] Added CE Low: ${references.ce_payload_low}`);
                            }
                        }
                    } catch (e) {
                        console.warn('[Chart] Error adding reference lines:', e);
                    }
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
        addScrollButton: addScrollButton
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
