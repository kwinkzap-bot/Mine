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

        return rawData.map((item, index) => {
            try {
                // Handle both 'time' and 'date' field names (backend uses 'date')
                let timestamp = item.time || item.date;
                let time;

                if (typeof timestamp === 'number') {
                    // If timestamp is in seconds (< 10 billion), it's likely seconds since epoch
                    if (timestamp < 10000000000) {
                        time = timestamp;
                    } else {
                        // Already in milliseconds, convert to seconds
                        time = Math.floor(timestamp / 1000);
                    }
                } else if (typeof timestamp === 'string') {
                    // Parse ISO string like "2025-01-08T10:30:00Z"
                    const date = new Date(timestamp);
                    
                    // Validate the date object was created successfully
                    if (isNaN(date.getTime())) {
                        console.warn(`[formatChartData] Invalid date at index ${index}: "${timestamp}". Using current time as fallback.`);
                        time = Math.floor(Date.now() / 1000);
                    } else {
                        time = Math.floor(date.getTime() / 1000);
                    }
                } else {
                    // Fallback: use current time
                    console.warn(`[formatChartData] Unknown timestamp type at index ${index}: ${typeof timestamp}. Using current time.`);
                    time = Math.floor(Date.now() / 1000);
                }

                return {
                    time: time,
                    open: parseFloat(item.open) || item.o || 0,
                    high: parseFloat(item.high) || item.h || 0,
                    low: parseFloat(item.low) || item.l || 0,
                    close: parseFloat(item.close) || item.c || 0,
                    volume: item.volume || 0
                };
            } catch (error) {
                console.error(`[formatChartData] Error processing candle at index ${index}:`, error, item);
                // Return a fallback candle with current time
                return {
                    time: Math.floor(Date.now() / 1000),
                    open: 0,
                    high: 0,
                    low: 0,
                    close: 0,
                    volume: 0
                };
            }
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

            // Add padding to create space on the right side
            container.style.paddingRight = '80px';
            container.style.boxSizing = 'border-box';
            container.style.position = 'relative';

            const height = options.height || 400;
            const width = options.width || '100%';
            const theme = options.theme || 'light';

            // Make pdh and pdl mutable for updatePdhPdl to work
            let mutablePdh = pdh;
            let mutablePdl = pdl;

            // Create chart with light theme and IST timezone formatting
            const chart = LightweightCharts.createChart(container, {
                layout: {
                    textColor: '#374151',           // Dark grey text for readability
                    background: { type: 'solid', color: '#ffffff' }  // White background
                },
                grid: {
                    vertLines: {
                        color: '#f0f0f0',           // Lighter grey vertical grid lines
                        style: 0                     // Solid lines
                    },
                    horzLines: {
                        color: '#f0f0f0',           // Lighter grey horizontal grid lines
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
                    rightOffset: 12,                // Reduced offset - brings candles closer to Y-axis
                    fixLeftEdge: false,             // Allow scrolling on left
                    fixRightEdge: false             // Allow dragging to right side
                },
                rightPriceScale: {
                    textColor: '#6b7280',           // Price scale text color
                    borderColor: 'transparent',     // Hide the border
                    width: 60                       // Set explicit width for price scale
                },
                watermark: {
                    color: '#d1d5db'                // Light grey watermark
                },
                // Apply IST timezone formatter to x-axis
                localizationParameters: createTimeFormatter(timeframe),
                height: height,
                width: window.innerWidth > 600 ? container.offsetWidth : container.offsetWidth
            });

            // Create candlestick series
            // Default colors: Green for up candles, Red for down candles
            const upColor = '#10b981';      // Green for bullish candles
            const downColor = '#ef4444';    // Red for bearish candles
            const borderUpColor = '#10b981';
            const borderDownColor = '#ef4444';
            const wickUpColor = '#10b981';
            const wickDownColor = '#ef4444';

            // For combined charts, create two series (CE and PE)
            let series = null;
            let ceSeries = null;
            let peSeries = null;

            if (type === 'COMBINED' || config.isCombined) {
                // Create CE series (primary series - green and red)
                ceSeries = chart.addCandlestickSeries({
                    upColor: '#10b981',      // Green for CE up
                    downColor: '#ef4444',    // Red for CE down
                    borderUpColor: '#10b981',
                    borderDownColor: '#ef4444',
                    wickUpColor: '#10b981',
                    wickDownColor: '#ef4444',
                    title: 'CE'
                });

                // Create PE series (secondary series - violet/black)
                peSeries = chart.addCandlestickSeries({
                    upColor: '#8b5cf6',      // Violet for PE up
                    downColor: '#1f2937',    // Black for PE down
                    borderUpColor: '#8b5cf6',
                    borderDownColor: '#1f2937',
                    wickUpColor: '#8b5cf6',
                    wickDownColor: '#1f2937',
                    title: 'PE'
                });

                series = ceSeries; // Primary series for backward compatibility
            } else {
                // Single series chart
                series = chart.addCandlestickSeries({
                    upColor: upColor,
                    downColor: downColor,
                    borderUpColor: borderUpColor,
                    borderDownColor: borderDownColor,
                    wickUpColor: wickUpColor,
                    wickDownColor: wickDownColor
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

                        // Apply zoom with minimal rightOffset in applyOptions
                        // The CSS padding handles the visual spacing
                        chart.timeScale().applyOptions({
                            rightOffset: 10,        // Minimal offset - spacing is handled by CSS
                            barSpacing: 8
                        });

                        chart.timeScale().setVisibleRange({
                            from: firstVisibleCandle.time,
                            to: lastCandle.time
                        }, true); // true = animates to the range

                    } catch (error) {
                        console.warn('Error setting visible range:', error);
                    }
                }, 100); // Small delay to let chart render
            }

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

            // Return public interface
            return {
                chart: chart,
                series: series,
                ceSeries: ceSeries,
                peSeries: peSeries,
                priceLinesArray: priceLinesArray,
                data: formattedData,
                isCombined: type === 'COMBINED' || config.isCombined,
                chartType: type,  // Store chart type for status calculation

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

                    // If refresh flag is true, clear existing price lines before updating
                    if (refresh) {
                        console.log('[Chart] Refresh mode: clearing existing price lines');
                        // Remove all existing price lines
                        if (priceLinesArray && priceLinesArray.length > 0) {
                            priceLinesArray.forEach(line => {
                                series ? series.removePriceLine(line) : (ceSeries && ceSeries.removePriceLine(line));
                            });
                            // Clear the array without reassigning (avoid const violation)
                            priceLinesArray.splice(0, priceLinesArray.length);
                        }
                    }

                    if (isCombinedUpdate) {
                        // Combined chart: update both CE and PE series
                        const ceFormattedData = formatChartData(newData);
                        const peFormattedData = formatChartData(referenceOrPeData);

                        if (ceFormattedData.length > 0) {
                            ceSeries.setData(ceFormattedData);
                            this.data = ceFormattedData;
                        }
                        if (peFormattedData.length > 0) {
                            peSeries.setData(peFormattedData);
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

                    // NOTE: Chart zoom and timeScale settings are NOT recalculated on updates
                    // They are only set ONCE during initialization (see create() method)
                    // This prevents the chart from jumping/resetting on every data update
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
        getLatestPrice: getLatestPrice
    };
})();
