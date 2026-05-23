/**
 * multi_strike.js
 * Handles multi-strike options display with PDH/PDL lines using TradingView Lightweight Charts
 */

let multiStrikeChart = null;
let multiStrikeSeries = [];
let currentData = null;
let currentChartType = 'line'; // Track current chart type (default: line)
let autoRefreshInterval = null; // Store interval ID for auto-refresh
let cachedPdhPdl = {}; // Cache PDH/PDL data to avoid re-fetching on chart type switch

// Market hours configuration (IST)
const MARKET_OPEN_HOUR = 9;
const MARKET_OPEN_MINUTE = 15;
const MARKET_CLOSE_HOUR = 15;
const MARKET_CLOSE_MINUTE = 0;
const AUTO_REFRESH_INTERVAL = 15000; // 15 seconds in milliseconds

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    const fetchBtn = document.getElementById('fetchBtn');
    const symbolSelect = document.getElementById('symbolSelect');
    const numStrikes = document.getElementById('numStrikes');
    const chartTypeSelect = document.getElementById('chartTypeSelect');

    // Fetch on page load
    fetchMultiStrikeData();

    // Fetch on button click
    fetchBtn.addEventListener('click', (e) => {
        e.preventDefault();
        fetchMultiStrikeData();
    });

    // Fetch on symbol change
    symbolSelect.addEventListener('change', () => {
        fetchMultiStrikeData();
    });

    // Fetch on strikes change
    numStrikes.addEventListener('change', () => {
        fetchMultiStrikeData();
    });

    // Redraw charts when chart type changes
    if (chartTypeSelect) {
        chartTypeSelect.addEventListener('change', (e) => {
            currentChartType = e.target.value;
            if (currentData) {
                // Redraw with new chart type (pass true to skip PDH/PDL refetch)
                waitForTradingView(() => {
                    displayCharts(currentData, true);
                });
            }
        });
    }

    // Start auto-refresh if during market hours
    startAutoRefresh();
});

/**
 * Check if current time is within market hours (IST)
 */
function isMarketHours() {
    const now = new Date();
    const day = now.getDay(); // 0=Sun, 6=Sat
    
    // Check if it's a weekday (Monday-Friday)
    if (day === 0 || day === 6) {
        console.log('[Auto-Refresh] Weekend detected, skipping auto-refresh');
        return false;
    }
    
    const currentHour = now.getHours();
    const currentMinute = now.getMinutes();
    const currentTimeInMinutes = currentHour * 60 + currentMinute;
    
    const marketOpenInMinutes = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE;
    const marketCloseInMinutes = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE;
    
    const isWithinHours = currentTimeInMinutes >= marketOpenInMinutes && currentTimeInMinutes < marketCloseInMinutes;
    
    if (!isWithinHours) {
        console.log(`[Auto-Refresh] Outside market hours (${currentHour}:${String(currentMinute).padStart(2, '0')}). Market: ${MARKET_OPEN_HOUR}:${String(MARKET_OPEN_MINUTE).padStart(2, '0')} - ${MARKET_CLOSE_HOUR}:${String(MARKET_CLOSE_MINUTE).padStart(2, '0')}`);
    }
    
    return isWithinHours;
}

/**
 * Start auto-refresh timer for multi-strike data during market hours
 */
function startAutoRefresh() {
    // Stop any existing interval
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
    
    console.log('[Auto-Refresh] Starting auto-refresh scheduler...');
    
    // Create interval that checks every second
    autoRefreshInterval = setInterval(() => {
        if (isMarketHours()) {
            console.log('[Auto-Refresh] Fetching data at', new Date().toLocaleTimeString());
            fetchMultiStrikeData(true); // true = silent mode (no loader messages)
        }
    }, AUTO_REFRESH_INTERVAL);
    
    console.log('[Auto-Refresh] Auto-refresh started - will fetch every 15 seconds during market hours');
}

/**
 * Stop auto-refresh timer
 */
function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
        console.log('[Auto-Refresh] Auto-refresh stopped');
    }
}

/**
 * Wait for TradingView library to load
 */
function waitForTradingView(callback, maxWait = 5000) {
    const startTime = Date.now();
    
    function check() {
        if (typeof LightweightCharts !== 'undefined') {
            console.log('TradingView LightweightCharts library is loaded');
            callback();
        } else if (Date.now() - startTime < maxWait) {
            setTimeout(check, 100);
        } else {
            console.error('TradingView Lightweight Charts library failed to load');
        }
    }
    
    check();
}

/**
 * Fetch PDH/PDL data from dedicated API endpoint
 */
async function fetchPdhPdlData(ceToken, peToken, symbol) {
    try {
        const response = await fetch('/api/options-pdh-pdl', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                ce_token: ceToken,
                pe_token: peToken,
                symbol: symbol
            })
        });

        if (!response.ok) {
            const error = await response.json();
            console.error('PDH/PDL API error:', error);
            return null;
        }

        const result = await response.json();
        if (result.success) {
            console.log('PDH/PDL data received:', {
                pdh: result.pdh,
                pdl: result.pdl,
                ce_pdh: result.pdh_pdl?.ce_pdh,
                ce_pdl: result.pdh_pdl?.ce_pdl,
                pe_pdh: result.pdh_pdl?.pe_pdh,
                pe_pdl: result.pdh_pdl?.pe_pdl
            });
            return {
                pdh: result.pdh,
                pdl: result.pdl,
                ce_pdh: result.pdh_pdl?.ce_pdh,
                ce_pdl: result.pdh_pdl?.ce_pdl,
                pe_pdh: result.pdh_pdl?.pe_pdh,
                pe_pdl: result.pdh_pdl?.pe_pdl
            };
        } else {
            console.error('Failed to fetch PDH/PDL data:', result.error);
            return null;
        }
    } catch (error) {
        console.error('Error fetching PDH/PDL:', error);
        return null;
    }
}

/**
 * Fetch multi-strike data from API
 */
async function fetchMultiStrikeData(silentMode = false) {
    const symbol = document.getElementById('symbolSelect').value;
    const numStrikes = parseInt(document.getElementById('numStrikes').value) || 0;
    const loader = document.getElementById('loaderContainer');
    const dataContainer = document.getElementById('dataContainer');

    // Only show loader if not in silent mode
    if (!silentMode) {
        dataContainer.classList.add('hidden');
        loader.classList.remove('hidden');
        loader.style.display = 'flex';
    }

    try {
        // First, fetch the multi-strike data with pricing
        const response = await fetch(`/api/multi-strike?symbol=${symbol}&num_strikes=${numStrikes}`);
        
        if (!response.ok) {
            const error = await response.json();
            if (!silentMode) {
                loader.style.display = 'none';
                loader.classList.add('hidden');
                alert(`Error: ${error.error || 'Failed to fetch data'}`);
            }
            console.error('API error:', error);
            return;
        }

        const result = await response.json();

        if (!result.success) {
            if (!silentMode) {
                loader.style.display = 'none';
                loader.classList.add('hidden');
                alert(`Error: ${result.error || 'Failed to get multi-strike data'}`);
            }
            console.error('API returned unsuccessful response:', result);
            return;
        }

        console.log('[Auto-Refresh] Multi-strike data received at', new Date().toLocaleTimeString());
        console.log('Strikes data length:', result.strikes_data.length);

        // Fetch chart data for each strike using options-chart-data API
        console.log('Fetching chart data for each strike...');
        const strikeCharts = {};
        
        for (const strike of result.strikes_data) {
            try {
                console.log(`Fetching chart for strike ${strike.strike}: CE token=${strike.ce_token}, PE token=${strike.pe_token}`);
                
                const chartResponse = await fetch('/api/options-chart-data', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        ce_token: strike.ce_token,
                        pe_token: strike.pe_token,
                        timeframe: '5minute'
                    })
                });
                
                if (!chartResponse.ok) {
                    console.warn(`Failed to fetch chart for strike ${strike.strike}`);
                    strikeCharts[String(Math.floor(strike.strike))] = {
                        ce_chart: [],
                        pe_chart: []
                    };
                    continue;
                }
                
                const chartData = await chartResponse.json();
                
                if (chartData.success && chartData.data) {
                    // Format chart data - same as options_chart_app.js
                    // Backend returns timestamps adjusted for IST (adds 5.5 hour offset)
                    // so Lightweight Charts displays IST time correctly
                    const formatData = (data) => {
                        return data.map(item => {
                            let timestamp;
                            if (typeof item.date === 'number') {
                                // Backend returns Unix timestamp (already IST-adjusted by backend)
                                timestamp = item.date;
                            } else if (typeof item.date === 'string') {
                                // Parse ISO string date to Unix timestamp
                                const dateObj = new Date(item.date);
                                timestamp = Math.floor(dateObj.getTime() / 1000);
                            } else {
                                // Fallback for other date formats
                                timestamp = Math.floor(new Date(item.date).getTime() / 1000);
                            }
                            return {
                                time: timestamp,
                                open: item.open,
                                high: item.high,
                                low: item.low,
                                close: item.close,
                                volume: item.volume || 0
                            };
                        });
                    };
                    
                    // Split combined data into CE and PE and format both
                    const ceData = formatData(chartData.data.filter(c => c.type === 'CE'));
                    const peData = formatData(chartData.data.filter(c => c.type === 'PE'));
                    
                    strikeCharts[String(Math.floor(strike.strike))] = {
                        ce_chart: ceData,
                        pe_chart: peData
                    };
                    
                    console.log(`Chart data fetched for strike ${strike.strike}: CE=${ceData.length}, PE=${peData.length} candles`);
                } else {
                    console.warn(`No chart data for strike ${strike.strike}`);
                    strikeCharts[String(Math.floor(strike.strike))] = {
                        ce_chart: [],
                        pe_chart: []
                    };
                }
            } catch (chartError) {
                console.error(`Error fetching chart for strike ${strike.strike}:`, chartError);
                strikeCharts[String(Math.floor(strike.strike))] = {
                    ce_chart: [],
                    pe_chart: []
                };
            }
        }
        
        console.log('All chart data fetched. Strike charts keys:', Object.keys(strikeCharts));
        
        // Merge chart data into result
        result.strike_charts = strikeCharts;

        currentData = result;
        displayMultiStrikeData(result);
        
        // Only update UI if not in silent mode
        if (!silentMode) {
            loader.style.display = 'none';
            loader.classList.add('hidden');
            setTimeout(() => {
                dataContainer.classList.remove('hidden');
            }, 50);
        }

    } catch (error) {
        console.error('Error fetching multi-strike data:', error);
        if (!silentMode) {
            loader.style.display = 'none';
            loader.classList.add('hidden');
            alert(`Error: ${error.message}`);
        }
    }
}

/**
 * Display multi-strike data with chart and table
 */
function displayMultiStrikeData(data) {
    // Store current data for chart type switching
    currentData = data;
    
    // Update header info
    document.getElementById('niftyClose').textContent = `₹${data.nifty_close.toFixed(2)}`;
    document.getElementById('pdh').textContent = `₹${data.pdh.toFixed(2)}`;
    document.getElementById('pdl').textContent = `₹${data.pdl.toFixed(2)}`;
    document.getElementById('atmStrike').textContent = `₹${data.closest_strike.toFixed(2)}`;

    // Display table
    displayStrikeTable(data.strikes_data);

    // Display chart (wait for TradingView to load)
    waitForTradingView(() => {
        displayCharts(data);
    });
}

/**
 * Display charts after TradingView library is loaded
 */
function displayCharts(data, isChartTypeSwitch = false) {
    const strikeCharts = data.strike_charts || {};
    
    console.log('=== CHART DATA DEBUG ===');
    console.log('Strike charts keys:', Object.keys(strikeCharts));
    console.log('Strike charts keys count:', Object.keys(strikeCharts).length);
    console.log('Current chart type:', currentChartType);
    console.log('Is chart type switch:', isChartTypeSwitch);

    // Use currentChartType to decide which chart to display
    if (currentChartType === 'candlestick') {
        console.log('Displaying candlestick chart');
        displayCandlestickChartsInternal(data, isChartTypeSwitch);
    } else if (currentChartType === 'line') {
        console.log('Displaying line series chart');
        displayLineChartsInternal(data, isChartTypeSwitch);
    } else {
        // Fallback to candlestick
        console.log('Unknown chart type, falling back to candlestick');
        displayCandlestickChartsInternal(data, isChartTypeSwitch);
    }
}

/**
 * Display strike prices in table
 */
function displayStrikeTable(strikesData) {
    const tbody = document.getElementById('strikeBody');
    tbody.innerHTML = '';

    strikesData.forEach(strike => {
        const row = document.createElement('tr');
        if (strike.is_atm) {
            row.classList.add('atm-row');
        }

        const total = strike.ce_price + strike.pe_price;
        const diff = strike.ce_price - strike.pe_price;

        row.innerHTML = `
            <td>${strike.strike.toFixed(2)}</td>
            <td>${strike.ce_price.toFixed(2)}</td>
            <td>${strike.pe_price.toFixed(2)}</td>
            <td>${total.toFixed(2)}</td>
            <td>${diff > 0 ? '+' : ''}${diff.toFixed(2)}</td>
        `;
        tbody.appendChild(row);
    });
}

/**
 * Display line chart using close prices from candlestick data
 */
async function displayLineChartsInternal(data, skipPdhPdlFetch = false) {
    console.log('=== displayLineChartsInternal called ===');
    const chartsContainer = document.getElementById('strikeChartsContainer');
    if (!chartsContainer) {
        console.error('Charts container not found');
        return;
    }
    
    const strikes = data.strikes_data;
    const strikeCharts = data.strike_charts || {};
    const numStrikes = parseInt(document.getElementById('numStrikes').value) || 0;
    
    console.log('PDH/PDL from data object - PDH:', data.pdh, 'PDL:', data.pdl);
    
    // Find ATM strike
    const atmStrike = strikes.find(s => s.is_atm);
    if (!atmStrike) {
        console.warn('No ATM strike found');
        chartsContainer.innerHTML = '<p style="padding: 20px; text-align: center; color: #666;">No ATM strike found.</p>';
        return;
    }
    
    console.log('Total strikes:', strikes.length);
    console.log('Displaying strikes:', numStrikes);
    
    // Determine which strikes to display
    const atmIndex = strikes.indexOf(atmStrike);
    let strikeIndices = [atmIndex];
    
    if (numStrikes > 0) {
        for (let i = 1; i <= numStrikes; i++) {
            if (atmIndex + i < strikes.length) {
                strikeIndices.push(atmIndex + i);
            }
        }
        for (let i = 1; i <= numStrikes; i++) {
            if (atmIndex - i >= 0) {
                strikeIndices.unshift(atmIndex - i);
            }
        }
    }
    
    const strikesToDisplay = strikeIndices.map(idx => strikes[idx]);
    console.log('Strikes to display:', strikesToDisplay.length);
    
    // Fetch PDH/PDL for each strike (or use cached data if switching chart type)
    let strikePdhPdl = {};
    
    if (skipPdhPdlFetch && Object.keys(cachedPdhPdl).length > 0) {
        console.log('=== Using cached PDH/PDL data (chart type switch) ===');
        strikePdhPdl = cachedPdhPdl;
    } else {
        console.log('=== Fetching PDH/PDL for all strikes ===');
        for (const strike of strikesToDisplay) {
            try {
                const pdhPdlData = await fetchPdhPdlData(strike.ce_token, strike.pe_token, data.symbol || 'NIFTY');
                if (pdhPdlData) {
                    strikePdhPdl[String(Math.floor(strike.strike))] = pdhPdlData;
                    console.log(`PDH/PDL fetched for strike ${strike.strike}:`, pdhPdlData);
                }
            } catch (error) {
                console.error(`Error fetching PDH/PDL for strike ${strike.strike}:`, error);
            }
        }
        // Cache the fetched data
        cachedPdhPdl = strikePdhPdl;
    }
    
    // Destructure LightweightCharts
    const { createChart, LineSeries } = LightweightCharts;
    
    // Use distinct colors for CE and PE lines
    const CE_COLOR = '#3b82f6';    // Blue for CE
    const PE_COLOR = '#f97316';    // Orange for PE
    const PDH_COLOR = '#10b981';   // Green for PDH (high)
    const PDL_COLOR = '#ef4444';   // Red for PDL (low)
    
    // Function to generate random color
    const generateRandomColor = () => {
        const letters = '0123456789ABCDEF';
        let color = '#';
        for (let i = 0; i < 6; i++) {
            color += letters[Math.floor(Math.random() * 16)];
        }
        return color;
    };
    
    // Generate random colors for each CE and PE strike
    const ceColors = {};
    const peColors = {};
    strikesToDisplay.forEach((strike) => {
        const strikeKey = String(Math.floor(strike.strike));
        ceColors[strikeKey] = generateRandomColor();
        peColors[strikeKey] = generateRandomColor();
    });
    
    // Light theme configuration
    const lightTheme = {
        layout: {
            textColor: '#1f2937',
            background: { color: '#ffffff', type: 'solid' }
        },
        grid: {
            vertLines: { color: '#f0f0f0' },
            horzLines: { color: '#f0f0f0' }
        },
        timeScale: {
            textColor: '#6b7280',
            borderColor: '#e5e7eb',
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 50
        },
        rightPriceScale: {
            textColor: '#6b7280',
            borderColor: '#e5e7eb',
            autoScale: true
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: '#9ca3af', width: 1, style: 3 },
            horzLine: { color: '#9ca3af', width: 1, style: 3 },
        }
    };
    
    // Clear container
    chartsContainer.innerHTML = '';
    
    // Create main wrapper
    const mainWrapper = document.createElement('div');
    mainWrapper.className = 'chart-wrapper';
    mainWrapper.style.gridColumn = '1 / -1';
    
    // Create header container with title and legend side by side
    const headerContainer = document.createElement('div');
    headerContainer.style.display = 'flex';
    headerContainer.style.justifyContent = 'space-between';
    headerContainer.style.alignItems = 'flex-start';
    headerContainer.style.padding = '5px';
    headerContainer.style.backgroundColor = '#ffffff';
    headerContainer.style.borderBottom = '1px solid #e5e7eb';
    headerContainer.style.gap = '20px';
    
    const header = document.createElement('div');
    header.className = 'chart-header';
    header.style.margin = '0';
    header.style.flex = '0 0 auto';
    header.innerHTML = `<h3 style="margin: 0;">All Strikes - Combined Chart</h3>`;
    headerContainer.appendChild(header);
    
    // Create legend container (side by side with title)
    const legendDiv = document.createElement('div');
    legendDiv.id = 'chart-legend';
    legendDiv.style.display = 'flex';
    legendDiv.style.flexWrap = 'wrap';
    legendDiv.style.gap = '12px';
    legendDiv.style.flex = '1 1 auto';
    legendDiv.style.justifyContent = 'flex-end';
    
    headerContainer.appendChild(legendDiv);
    mainWrapper.appendChild(headerContainer);
    
    // Create chart container
    const chartDiv = document.createElement('div');
    chartDiv.id = 'combined_chart';
    chartDiv.className = 'chart-element';
    chartDiv.style.width = '100%';
    chartDiv.style.height = '450px';
    chartDiv.style.minHeight = '450px';
    chartDiv.style.display = 'block';
    chartDiv.style.boxSizing = 'border-box';
    
    mainWrapper.appendChild(chartDiv);
    chartsContainer.appendChild(mainWrapper);
    
    // Create chart after DOM is ready
    setTimeout(() => {
        try {
            console.log('Creating single combined chart with all CE and PE data');
            
            // Collect all CE and PE data
            const chartSeries = [];
            const seriesMap = {}; // Map to track which series are CE vs PE for each strike
            
            strikesToDisplay.forEach((strike, idx) => {
                const strikeKey = String(Math.floor(strike.strike));
                let foundData = strikeCharts[strikeKey];
                
                if (!foundData) {
                    foundData = strikeCharts[strike.strike.toFixed(1)] || strikeCharts[strike.strike.toFixed(2)];
                }
                
                if (foundData) {
                    const ceCandles = foundData.ce_chart || [];
                    const peCandles = foundData.pe_chart || [];
                    
                    // Add CE series with random color
                    if (ceCandles.length > 0) {
                        chartSeries.push({
                            label: `CE ${strike.strike.toFixed(0)}`,
                            data: ceCandles,
                            color: ceColors[strikeKey],
                            type: 'CE',
                            strikePrice: strike.strike,
                            strikeIndex: idx
                        });
                    }
                    
                    // Add PE series with random color
                    if (peCandles.length > 0) {
                        chartSeries.push({
                            label: `PE ${strike.strike.toFixed(0)}`,
                            data: peCandles,
                            color: peColors[strikeKey],
                            type: 'PE',
                            strikePrice: strike.strike,
                            strikeIndex: idx
                        });
                    }
                }
            });
            
            if (chartSeries.length === 0) {
                chartDiv.innerHTML = '<div style="padding: 40px 20px; text-align: center; color: #999;">No chart data available</div>';
                return;
            }
            
            // Create combined chart
            const combinedChart = createChart(chartDiv, lightTheme);
            
            // Add line series (using close prices from candlestick data) and store references for price lines
            const seriesReferences = [];
            
            chartSeries.forEach((series, seriesIdx) => {
                // Convert candlestick data to line data using close prices
                const lineData = series.data.map(candle => ({
                    time: candle.time,
                    value: candle.close
                }));
                
                const lineSeries = combinedChart.addSeries(LineSeries, {
                    color: series.color,
                    lineWidth: 2,
                    crosshairMarkerVisible: true,
                    title: series.label
                });
                lineSeries.setData(lineData);
                
                seriesReferences.push({
                    series: lineSeries,
                    type: series.type,
                    strikePrice: series.strikePrice,
                    strikeIndex: series.strikeIndex,
                    color: series.color,
                    seriesIndex: seriesIdx
                });
            });
            
            // Build and populate legend (near title)
            const chartLegendDiv = document.getElementById('chart-legend');
            chartLegendDiv.innerHTML = '';
            
            strikesToDisplay.forEach((strike, idx) => {
                const strikeKey = String(Math.floor(strike.strike));
                const ceColor = ceColors[strikeKey];
                const peColor = peColors[strikeKey];
                
                // Strike container
                const strikeContainer = document.createElement('div');
                strikeContainer.style.display = 'flex';
                strikeContainer.style.gap = '8px';
                strikeContainer.style.alignItems = 'center';
                strikeContainer.style.padding = '4px 8px';
                strikeContainer.style.backgroundColor = '#f9fafb';
                strikeContainer.style.borderRadius = '3px';
                strikeContainer.style.border = '1px solid #e5e7eb';
                strikeContainer.style.fontSize = '12px';
                
                // Strike label
                const strikeLabel = document.createElement('span');
                strikeLabel.style.fontWeight = 'bold';
                strikeLabel.style.minWidth = '45px';
                strikeLabel.style.fontSize = '12px';
                strikeLabel.textContent = `₹${strike.strike.toFixed(0)}${strike.is_atm ? ' ★' : ''}`;
                strikeContainer.appendChild(strikeLabel);
                
                // CE legend item
                const ceLegend = document.createElement('div');
                ceLegend.style.display = 'flex';
                ceLegend.style.alignItems = 'center';
                ceLegend.style.gap = '3px';
                ceLegend.style.fontSize = '11px';
                
                const ceColor_div = document.createElement('div');
                ceColor_div.style.width = '10px';
                ceColor_div.style.height = '2px';
                ceColor_div.style.backgroundColor = ceColor;
                ceLegend.appendChild(ceColor_div);
                
                const ceText = document.createElement('span');
                ceText.textContent = `CE: ₹${strike.ce_price.toFixed(2)}`;
                ceLegend.appendChild(ceText);
                strikeContainer.appendChild(ceLegend);
                
                // PE legend item
                const peLegend = document.createElement('div');
                peLegend.style.display = 'flex';
                peLegend.style.alignItems = 'center';
                peLegend.style.gap = '3px';
                peLegend.style.fontSize = '11px';
                
                const peColor_div = document.createElement('div');
                peColor_div.style.width = '10px';
                peColor_div.style.height = '2px';
                peColor_div.style.backgroundColor = peColor;
                peLegend.appendChild(peColor_div);
                
                const peText = document.createElement('span');
                peText.textContent = `PE: ₹${strike.pe_price.toFixed(2)}`;
                peLegend.appendChild(peText);
                strikeContainer.appendChild(peLegend);
                
                chartLegendDiv.appendChild(strikeContainer);
            });
            
            // Add PDH/PDL price lines for each strike
            console.log('=== Adding PDH/PDL Lines for All Strikes ===');
            console.log('strikePdhPdl data:', strikePdhPdl);
            
            seriesReferences.forEach((ref, idx) => {
                const strikeKey = String(Math.floor(ref.strikePrice));
                const pdhPdlData = strikePdhPdl[strikeKey];
                
                if (pdhPdlData) {
                    console.log(`Adding PDH/PDL lines for strike ${ref.strikePrice} (${ref.type}):`, pdhPdlData);
                    
                    if (ref.type === 'CE') {
                        // Add CE PDH line
                        if (pdhPdlData.ce_pdh !== null && pdhPdlData.ce_pdh !== undefined) {
                            try {
                                ref.series.createPriceLine({
                                    price: pdhPdlData.ce_pdh,
                                    color: ref.color,
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `${ref.strikePrice} CE PDH`
                                });
                                console.log(`✓ Added CE PDH line at ${pdhPdlData.ce_pdh} for strike ${ref.strikePrice}`);
                            } catch (err) {
                                console.error(`Error adding CE PDH line for strike ${ref.strikePrice}:`, err);
                            }
                        }
                        
                        // Add CE PDL line
                        if (pdhPdlData.ce_pdl !== null && pdhPdlData.ce_pdl !== undefined) {
                            try {
                                ref.series.createPriceLine({
                                    price: pdhPdlData.ce_pdl,
                                    color: ref.color,
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `${ref.strikePrice} CE PDL`
                                });
                                console.log(`✓ Added CE PDL line at ${pdhPdlData.ce_pdl} for strike ${ref.strikePrice}`);
                            } catch (err) {
                                console.error(`Error adding CE PDL line for strike ${ref.strikePrice}:`, err);
                            }
                        }
                    } else if (ref.type === 'PE') {
                        // Add PE PDH line
                        if (pdhPdlData.pe_pdh !== null && pdhPdlData.pe_pdh !== undefined) {
                            try {
                                ref.series.createPriceLine({
                                    price: pdhPdlData.pe_pdh,
                                    color: ref.color,
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `${ref.strikePrice} PE PDH`
                                });
                                console.log(`✓ Added PE PDH line at ${pdhPdlData.pe_pdh} for strike ${ref.strikePrice}`);
                            } catch (err) {
                                console.error(`Error adding PE PDH line for strike ${ref.strikePrice}:`, err);
                            }
                        }
                        
                        // Add PE PDL line
                        if (pdhPdlData.pe_pdl !== null && pdhPdlData.pe_pdl !== undefined) {
                            try {
                                ref.series.createPriceLine({
                                    price: pdhPdlData.pe_pdl,
                                    color: ref.color,
                                    lineWidth: 2,
                                    lineStyle: LightweightCharts.LineStyle.Solid,
                                    axisLabelVisible: true,
                                    title: `${ref.strikePrice} PE PDL`
                                });
                                console.log(`✓ Added PE PDL line at ${pdhPdlData.pe_pdl} for strike ${ref.strikePrice}`);
                            } catch (err) {
                                console.error(`Error adding PE PDL line for strike ${ref.strikePrice}:`, err);
                            }
                        }
                    }
                } else {
                    console.warn(`No PDH/PDL data found for strike ${ref.strikePrice}`);
                }
            });
            
            combinedChart.timeScale().fitContent();
            
            const resizeObserver = new ResizeObserver(() => {
                if (chartDiv && chartDiv.parentElement) {
                    const newWidth = chartDiv.offsetWidth;
                    if (newWidth > 0) {
                        combinedChart.applyOptions({ width: newWidth });
                    }
                }
            });
            resizeObserver.observe(chartDiv);
            
            console.log('Combined chart created with', chartSeries.length, 'series and PDH/PDL lines for all strikes');
            
        } catch (err) {
            console.error('Error creating combined chart:', err);
            const errorDiv = document.createElement('div');
            errorDiv.style.padding = '20px';
            errorDiv.style.color = '#d32f2f';
            errorDiv.style.textAlign = 'center';
            errorDiv.innerHTML = `Error loading chart: ${err.message}`;
            mainWrapper.appendChild(errorDiv);
        }
    }, 200);
}

/**
 * Display candlestick chart with OHLC data
 */
async function displayCandlestickChartsInternal(data, skipPdhPdlFetch = false) {
    console.log('=== displayCandlestickChartsInternal called ===');
    const chartsContainer = document.getElementById('strikeChartsContainer');
    
    if (!chartsContainer) {
        console.error('Charts container not found');
        return;
    }

    try {
        // Get strike data from API response
        const strikes = data.strikes_data || [];
        const strikeCharts = data.strike_charts || {};
        
        console.log('Candlestick chart - strikes_data length:', strikes.length);
        console.log('Candlestick chart - strikes_data sample:', strikes.slice(0, 1));
        console.log('Candlestick chart - full data keys:', Object.keys(data));
        
        if (strikes.length === 0) {
            console.warn('No strike data available');
            chartsContainer.innerHTML = '<div style="padding: 20px; text-align: center;">No data available</div>';
            return;
        }

        // Clean up previous chart and series
        if (multiStrikeChart) {
            try {
                multiStrikeChart.remove();
            } catch (e) {
                console.warn('Error removing previous chart:', e);
            }
        }
        multiStrikeSeries = [];

        chartsContainer.innerHTML = '';

        // Create chart container
        const mainWrapper = document.createElement('div');
        mainWrapper.className = 'charts-wrapper';
        mainWrapper.style.width = '100%';
        mainWrapper.style.marginBottom = '20px';
        mainWrapper.style.borderRadius = '6px';
        mainWrapper.style.boxShadow = '0 2px 4px rgba(0,0,0,0.1)';
        mainWrapper.style.overflow = 'hidden';

        const chartDiv = document.createElement('div');
        chartDiv.id = 'multiStrikeChart';
        chartDiv.style.width = '100%';
        chartDiv.style.height = '500px';
        chartDiv.style.backgroundColor = '#ffffff';

        mainWrapper.appendChild(chartDiv);
        chartsContainer.appendChild(mainWrapper);

        // Ensure TradingView library is loaded before creating chart
        const initializeCandlestickChart = async () => {
            try {
                const { createChart, CandlestickSeries } = window.LightweightCharts;

                // Create the chart
                const chartWidth = chartDiv.clientWidth || 1000;
                multiStrikeChart = createChart(chartDiv, {
                    layout: { textColor: '#333', background: { color: '#fafafa' } },
                    width: chartWidth,
                    height: 500,
                    timeScale: {
                        timeVisible: false,
                        secondsVisible: false,
                    },
                    crosshair: {
                        mode: 1,
                        vertLine: { color: '#9ca3af', width: 1, style: 3 },
                        horzLine: { color: '#9ca3af', width: 1, style: 3 },
                    },
                });

                const pricePrecision = 2;
                const chartSeries = [];

                // Fetch PDH/PDL data
                let pdhPdlData = {};
                if (skipPdhPdlFetch && Object.keys(cachedPdhPdl).length > 0) {
                    console.log('=== Using cached PDH/PDL data for candlestick (chart type switch) ===');
                    pdhPdlData = cachedPdhPdl;
                } else {
                    console.log('=== Fetching PDH/PDL for candlestick chart ===');
                    for (const strike of strikes) {
                        try {
                            const pdData = await fetchPdhPdlData(strike.ce_token, strike.pe_token, data.symbol || 'NIFTY');
                            if (pdData) {
                                pdhPdlData[String(Math.floor(strike.strike))] = pdData;
                                console.log(`PDH/PDL fetched for strike ${strike.strike}:`, pdData);
                            }
                        } catch (error) {
                            console.error(`Error fetching PDH/PDL for strike ${strike.strike}:`, error);
                        }
                    }
                    // Cache the fetched data
                    cachedPdhPdl = pdhPdlData;
                }

                // Create candlestick series for each strike (CE and PE)
                strikes.forEach((strike, strikeIdx) => {
                    const strikeKey = strike.strike;
                    
                    // CE Candlestick Series - Green
                    const ceSeries = multiStrikeChart.addSeries(CandlestickSeries, {
                        title: `${strikeKey}CE`,
                        priceFormat: { type: 'price', precision: pricePrecision },
                        upColor: '#22ab94',
                        downColor: '#f23645',
                        borderUpColor: '#22ab94',
                        borderDownColor: '#f23645',
                        wickUpColor: '#22ab94',
                        wickDownColor: '#f23645',
                        crosshairMarkerVisible: true,
                    });
                    chartSeries.push(ceSeries);
                    multiStrikeSeries.push(ceSeries);

                    // PE Candlestick Series - Red
                    const peSeries = multiStrikeChart.addSeries(CandlestickSeries, {
                        title: `${strikeKey}PE`,
                        priceFormat: { type: 'price', precision: pricePrecision },
                        upColor: '#e01e5a',
                        downColor: '#ff6b6b',
                        borderUpColor: '#e01e5a',
                        borderDownColor: '#ff6b6b',
                        wickUpColor: '#e01e5a',
                        wickDownColor: '#ff6b6b',
                        crosshairMarkerVisible: true,
                    });
                    chartSeries.push(peSeries);
                    multiStrikeSeries.push(peSeries);

                    // Set candlestick data for CE
                    if (strike.ce_chart_data && Array.isArray(strike.ce_chart_data)) {
                        const ceData = strike.ce_chart_data.map((candle, idx) => ({
                            time: idx + 1,
                            open: candle.open,
                            high: candle.high,
                            low: candle.low,
                            close: candle.close,
                        }));
                        ceSeries.setData(ceData);
                    }

                    // Set candlestick data for PE
                    if (strike.pe_chart_data && Array.isArray(strike.pe_chart_data)) {
                        const peData = strike.pe_chart_data.map((candle, idx) => ({
                            time: idx + 1,
                            open: candle.open,
                            high: candle.high,
                            low: candle.low,
                            close: candle.close,
                        }));
                        peSeries.setData(peData);
                    }
                });

                // Add PDH/PDL lines
                strikes.forEach((strike) => {
                    const strikeKey = String(Math.floor(strike.strike));
                    const ceIdx = chartSeries.findIndex(s => s.title === `${strikeKey}CE`);
                    const peIdx = chartSeries.findIndex(s => s.title === `${strikeKey}PE`);

                    if (ceIdx !== -1 && pdhPdlData[strikeKey]) {
                        const pdh = pdhPdlData[strikeKey].ce_pdh;
                        const pdl = pdhPdlData[strikeKey].ce_pdl;
                        const ceSeries = chartSeries[ceIdx];

                        if (pdh) {
                            // PDH line
                            ceSeries.createPriceLine({
                                price: pdh,
                                color: '#4CAF50',
                                lineWidth: 1,
                                lineStyle: 2,
                                axisLabelVisible: true,
                                title: 'CE PDH',
                            });
                        }

                        if (pdl) {
                            // PDL line
                            ceSeries.createPriceLine({
                                price: pdl,
                                color: '#f44336',
                                lineWidth: 1,
                                lineStyle: 2,
                                axisLabelVisible: true,
                                title: 'CE PDL',
                            });
                        }
                    }

                    if (peIdx !== -1 && pdhPdlData[strikeKey]) {
                        const pdh = pdhPdlData[strikeKey].pe_pdh;
                        const pdl = pdhPdlData[strikeKey].pe_pdl;
                        const peSeries = chartSeries[peIdx];

                        if (pdh) {
                            // PDH line
                            peSeries.createPriceLine({
                                price: pdh,
                                color: '#4CAF50',
                                lineWidth: 1,
                                lineStyle: 2,
                                axisLabelVisible: true,
                                title: 'PE PDH',
                            });
                        }

                        if (pdl) {
                            // PDL line
                            peSeries.createPriceLine({
                                price: pdl,
                                color: '#f44336',
                                lineWidth: 1,
                                lineStyle: 2,
                                axisLabelVisible: true,
                                title: 'PE PDL',
                            });
                        }
                    }
                });

                // Set up time scale and fit content
                const timeScale = multiStrikeChart.timeScale();
                timeScale.applyOptions({
                    rightMargin: 12,
                    barSpacing: 50,
                    minBarSpacing: 20,
                    timeVisible: false,
                });

                setTimeout(() => {
                    try {
                        timeScale.fitContent();
                        console.log('Candlestick chart content fitted');
                    } catch (e) {
                        console.warn('fitContent failed:', e);
                    }
                }, 100);

                // Handle window resize
                const resizeHandler = () => {
                    if (multiStrikeChart && chartDiv && chartDiv.parentElement) {
                        const newWidth = chartDiv.clientWidth || chartDiv.offsetWidth;
                        if (newWidth > 0) {
                            multiStrikeChart.applyOptions({ width: newWidth });
                        }
                    }
                };

                window.removeEventListener('resize', resizeHandler);
                window.addEventListener('resize', resizeHandler);

                console.log('✓ Candlestick chart rendered successfully with', strikes.length, 'strikes and', chartSeries.length, 'series');

            } catch (err) {
                console.error('Error creating candlestick chart:', err);
                console.error('Error stack:', err.stack);
                chartsContainer.innerHTML = `<div style="padding: 40px 20px; text-align: center; color: #d32f2f;"><strong>Error loading chart:</strong><br>${err.message}</div>`;
            }
        };

        // Call the initialization function via waitForTradingView
        waitForTradingView(initializeCandlestickChart);
    } catch (err) {
        console.error('Error in displayCandlestickChartsInternal:', err);
        chartsContainer.innerHTML = `<div style="padding: 40px 20px; text-align: center; color: #d32f2f;"><strong>Error loading chart:</strong><br>${err.message}</div>`;
    }
}
