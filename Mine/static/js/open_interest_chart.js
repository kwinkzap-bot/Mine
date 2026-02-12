/**
 * Open Interest Chart - Real-time open interest visualization
 * Fetches data every 30 seconds and displays in chart format
 */

let coiChartInstance = null;
let combinedChartInstance = null;
let autoRefreshInterval = null;
let currentSymbol = 'NIFTY';
let strikeRangeCount = 15; // Default strike range
let cachedData = null; // Store fetched data to avoid repeated API calls

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('Open Interest Chart - Initializing...');
    
    // Event listeners
    document.getElementById('symbolSelect').addEventListener('change', onSymbolChange);
    document.getElementById('refreshNowBtn').addEventListener('click', refreshData);
    
    // Strike range button listeners - only manipulate cached data, no API call
    document.querySelectorAll('.strike-btn').forEach(btn => {
        btn.addEventListener('click', onStrikeRangeChange);
    });
    
    // Initial load
    refreshData();
    
    // Start auto-refresh on every 30 seconds during market hours
    startAutoRefresh();
});

/**
 * Handle symbol change
 */
function onSymbolChange(e) {
    currentSymbol = e.target.value;
    console.log(`Symbol changed to: ${currentSymbol}`);
    refreshData(true);  // true = show loader (manual refresh)
}

/**
 * Handle strike range change - only updates charts with cached data, no API call
 */
function onStrikeRangeChange(e) {
    const newCount = parseInt(e.target.getAttribute('data-strikes'));
    strikeRangeCount = newCount;
    console.log(`Strike range changed to: ${strikeRangeCount}`);
    
    // Update active button styling
    document.querySelectorAll('.strike-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    e.target.classList.add('active');
    
    // Use cached data to update charts without API call
    if (cachedData) {
        updateChartsWithCachedData(cachedData);
    }
}

/**
 * Update charts using cached data (no API call)
 */
function updateChartsWithCachedData(data) {
    try {
        const strikes = data.strikes || [];
        const currentPrice = data.current_price || 0;
        
        // Filter strikes to show strikeRangeCount above and below current price
        const filteredStrikes = filterStrikesByCurrentPrice(strikes, currentPrice, strikeRangeCount);
        
        const ceOI = filteredStrikes.map(s => s.ce_oi || 0);
        const peOI = filteredStrikes.map(s => s.pe_oi || 0);
        const ceCOI = filteredStrikes.map(s => s.ce_change_in_oi || 0);
        const peCOI = filteredStrikes.map(s => s.pe_change_in_oi || 0);
        const strikeLabels = filteredStrikes.map(s => s.strike);
        
        // Update Change in OI Chart
        updateCOIChart(strikeLabels, ceCOI, peCOI, currentPrice);
        
        // Update Combined Chart
        updateCombinedChart(strikeLabels, ceOI, peOI, ceCOI, peCOI, currentPrice);
        
        updateLastUpdateTime();
        
    } catch (error) {
        console.error('Error updating charts with cached data:', error);
    }
}

/**
 * Check if current time is within market hours (9:15 AM to 3:30 PM)
 */
function isMarketOpen() {
    const now = new Date();
    const hours = now.getHours();
    const minutes = now.getMinutes();
    const currentTime = hours * 60 + minutes; // Convert to minutes since midnight
    
    const marketOpenTime = 9 * 60 + 15;  // 9:15 AM in minutes
    const marketCloseTime = 15 * 60 + 30; // 3:30 PM in minutes
    
    // Check if it's a weekday (Monday = 1, Friday = 5)
    const dayOfWeek = now.getDay();
    const isWeekday = dayOfWeek >= 1 && dayOfWeek <= 5;
    
    return isWeekday && currentTime >= marketOpenTime && currentTime <= marketCloseTime;
}

/**
 * Start auto-refresh every 30 seconds (only during market hours)
 */
function startAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
    }
    
    autoRefreshInterval = setInterval(() => {
        if (isMarketOpen()) {
            console.log(`Auto-refreshing OI data for ${currentSymbol}...`);
            refreshData(false);  // false = don't show loader (auto-refresh)
        } else {
            console.log('Market hours closed. Auto-refresh paused.');
        }
    }, 30000); // 30 seconds
}

/**
 * Stop auto-refresh
 */
function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
}

/**
 * Fetch and display open interest data
 */
async function refreshData(showLoader = true) {
    const errorEl = document.getElementById('oiError');
    const refreshBtn = document.getElementById('refreshNowBtn');
    
    // Add spinning animation to refresh button
    refreshBtn.classList.add('refreshing');
    
    try {
        errorEl.classList.add('hidden');
        
        console.log(`Fetching OI data for ${currentSymbol}...`);
        
        const response = await fetch('/api/open-interest', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                symbol: currentSymbol
            })
        });
        
        if (!response.ok) {
            throw new Error(`API Error: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        if (!data.success) {
            throw new Error(data.error || 'Failed to fetch open interest data');
        }
        
        console.log(`✅ OI data received for ${currentSymbol}:`, data);
        
        // Cache the data for later use when strike range changes
        cachedData = data;
        
        // Update UI with data
        updateSummaryStats(data);
        updateCharts(data);
        updateLastUpdateTime();
        
        // Remove spinning animation from refresh button
        refreshBtn.classList.remove('refreshing');
        
    } catch (error) {
        console.error('Error fetching OI data:', error);
        errorEl.classList.remove('hidden');
        document.getElementById('errorMessage').textContent = `Error: ${error.message}`;
        
        // Remove spinning animation from refresh button
        refreshBtn.classList.remove('refreshing');
    }
}

/**
 * Update summary statistics
 */
function updateSummaryStats(data) {
    const ceStats = data.ce_summary || {};
    const peStats = data.pe_summary || {};
    
    // CE Summary
    document.getElementById('ceOITotal').textContent = formatNumber(ceStats.total_oi);
    document.getElementById('ceCOI').textContent = formatNumber(ceStats.change_in_oi);
    document.getElementById('ceMaxOIStrike').textContent = ceStats.max_oi_strike || '--';
    document.getElementById('ceMaxOIValue').textContent = formatNumber(ceStats.max_oi_value);
    
    // PE Summary
    document.getElementById('peOITotal').textContent = formatNumber(peStats.total_oi);
    document.getElementById('peCOI').textContent = formatNumber(peStats.change_in_oi);
    document.getElementById('peMaxOIStrike').textContent = peStats.max_oi_strike || '--';
    document.getElementById('peMaxOIValue').textContent = formatNumber(peStats.max_oi_value);
    
    // Market Metrics
    const pcrValue = data.pcr_oi || 0;
    const maxPain = data.max_pain || 0;
    const currentPrice = data.current_price || 0;
    
    const pcrElement = document.getElementById('pcrOI');
    pcrElement.textContent = pcrValue.toFixed(2);
    // PCR: Below 1 = Red (bearish), Above 1 = Green (bullish)
    pcrElement.classList.remove('color-red', 'color-green');
    if (pcrValue < 1) {
        pcrElement.classList.add('color-red');
    } else if (pcrValue > 1) {
        pcrElement.classList.add('color-green');
    }
    
    const maxPainElement = document.getElementById('maxPain');
    maxPainElement.textContent = maxPain;
    // Max Pain: Below current price = Red, Above current price = Green
    maxPainElement.classList.remove('color-red', 'color-green');
    if (maxPain < currentPrice) {
        maxPainElement.classList.add('color-red');
    } else if (maxPain > currentPrice) {
        maxPainElement.classList.add('color-green');
    }
}

/**
 * Plugin to draw vertical line at current price
 */
const currentPriceLinePlugin = {
    id: 'currentPriceLine',
    afterDatasetsDraw(chart) {
        const currentPrice = chart.options.plugins?.currentPriceLine?.price;
        if (!currentPrice) return;
        
        const ctx = chart.ctx;
        const xAxis = chart.scales.x;
        const yAxis = chart.scales.y;
        
        if (!xAxis || !yAxis) return;
        
        // Find the index of the current price in the labels
        const labels = chart.data.labels || [];
        let xPixel = null;
        let closestIndex = -1;
        let closestDiff = Infinity;
        
        // Find closest strike to current price
        for (let i = 0; i < labels.length; i++) {
            const strikePrice = parseFloat(labels[i]);
            const diff = Math.abs(strikePrice - currentPrice);
            if (diff < closestDiff) {
                closestDiff = diff;
                closestIndex = i;
            }
        }
        
        if (closestIndex >= 0) {
            // Get pixel position for this strike
            const meta = chart.getDatasetMeta(0);
            if (meta && meta.data[closestIndex]) {
                xPixel = meta.data[closestIndex].x;
            }
        }
        
        if (xPixel !== null) {
            // Draw vertical dashed line
            ctx.save();
            ctx.strokeStyle = 'rgba(0, 0, 0, 0.5)';
            ctx.lineWidth = 2;
            ctx.setLineDash([5, 5]);
            
            ctx.beginPath();
            ctx.moveTo(xPixel, yAxis.top);
            ctx.lineTo(xPixel, yAxis.bottom);
            ctx.stroke();
            
            // Draw label text only (no background or border)
            ctx.font = 'bold 11px Arial';
            ctx.textAlign = 'center';
            ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
            
            const labelText = `${currentPrice.toFixed(2)}`;
            const labelY = yAxis.top + 15;
            const labelX = xPixel;
            
            // Draw text
            ctx.fillText(labelText, labelX, labelY);
            
            ctx.restore();
        }
    }
};

/**
 * Update charts
 */
function updateCharts(data) {
    const strikes = data.strikes || [];
    const currentPrice = data.current_price || 0;
    
    // Filter strikes to show strikeRangeCount above and below current price
    const filteredStrikes = filterStrikesByCurrentPrice(strikes, currentPrice, strikeRangeCount);
    
    const ceOI = filteredStrikes.map(s => s.ce_oi || 0);
    const peOI = filteredStrikes.map(s => s.pe_oi || 0);
    const ceCOI = filteredStrikes.map(s => s.ce_change_in_oi || 0);
    const peCOI = filteredStrikes.map(s => s.pe_change_in_oi || 0);
    const strikeLabels = filteredStrikes.map(s => s.strike);
    
    // Update Change in OI Chart
    updateCOIChart(strikeLabels, ceCOI, peCOI, currentPrice);
    
    // Update Combined Chart
    updateCombinedChart(strikeLabels, ceOI, peOI, ceCOI, peCOI, currentPrice);
}

/**
 * Filter strikes to show N strikes above and N strikes below current price
 */
function filterStrikesByCurrentPrice(strikes, currentPrice, count = 10) {
    if (!strikes || strikes.length === 0) {
        return [];
    }
    
    // Separate strikes into above and below current price
    const below = strikes.filter(s => s.strike < currentPrice);
    const above = strikes.filter(s => s.strike > currentPrice);
    const atPrice = strikes.filter(s => s.strike === currentPrice);
    
    // Get the last 'count' strikes below and first 'count' strikes above
    const selectedBelow = below.slice(Math.max(0, below.length - count));
    const selectedAbove = above.slice(0, count);
    
    // Combine: below + at price + above
    const filtered = [...selectedBelow, ...atPrice, ...selectedAbove];
    
    // Sort by strike price
    return filtered.sort((a, b) => a.strike - b.strike);
}

/**
 * Update Open Interest Bar Chart
 */
/**
 * Update Change in OI Signal Chart - Shows OI with Increase/Decrease indicator
 */
function updateCOIChart(labels, ceData, peData, currentPrice) {
    const ctx = document.getElementById('coiChart').getContext('2d');
    
    // Create dynamic colors based on positive/negative values - Light colors for Change in OI
    const ceBackgroundColors = ceData.map(val => val >= 0 ? 'rgba(255, 127, 127, 0.8)' : 'rgba(255, 127, 127, 0.4)');
    const ceBorderColors = ceData.map(val => val >= 0 ? 'rgba(220, 20, 60, 1)' : 'rgba(220, 20, 60, 0.6)');
    const peBackgroundColors = peData.map(val => val >= 0 ? 'rgba(144, 238, 144, 0.8)' : 'rgba(144, 238, 144, 0.4)');
    const peBorderColors = peData.map(val => val >= 0 ? 'rgba(34, 139, 34, 1)' : 'rgba(34, 139, 34, 0.6)');
    
    const chartConfig = {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Call OI Change',
                    data: ceData,
                    backgroundColor: ceBackgroundColors,
                    borderColor: ceBorderColors,
                    borderWidth: 1,
                    borderRadius: 0,
                    yAxisID: 'y'
                },
                {
                    label: 'Put OI Change',
                    data: peData,
                    backgroundColor: peBackgroundColors,
                    borderColor: peBorderColors,
                    borderWidth: 1,
                    borderRadius: 0,
                    yAxisID: 'y'
                }
            ]
        },
        options: {
            indexAxis: undefined,
            responsive: true,
            maintainAspectRatio: true,
            animation: {
                duration: 300
            },
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                currentPriceLine: {
                    price: currentPrice
                },
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        padding: 15,
                        font: {
                            size: 12,
                            weight: 'bold'
                        },
                        generateLabels: function(chart) {
                            const data = chart.data;
                            return data.datasets.map((dataset, index) => ({
                                text: dataset.label,
                                fillStyle: dataset.backgroundColor[0] || 'rgba(0,0,0,0)',
                                strokeStyle: dataset.borderColor[0] || 'rgba(0,0,0,1)',
                                lineWidth: dataset.borderWidth || 1,
                                hidden: !chart.isDatasetVisible(index),
                                index: index
                            }));
                        }
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    padding: 12,
                    titleFont: {
                        size: 14,
                        weight: 'bold'
                    },
                    bodyFont: {
                        size: 12
                    },
                    callbacks: {
                        title: function(context) {
                            return `Strike: ${context[0].label}`;
                        },
                        label: function(context) {
                            const datasetLabel = context.dataset.label;
                            const value = context.parsed.y;
                            const absValue = formatNumber(Math.abs(value));
                            const changeIndicator = value > 0 ? '↑ Increase' : value < 0 ? '↓ Decrease' : '→ Unchanged';
                            
                            return `${datasetLabel}: ${formatNumber(value)} (${changeIndicator}: ${absValue})`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    beginAtZero: true,
                    stacked: false,
                    title: {
                        display: true,
                        text: 'Change in Open Interest',
                        font: {
                            weight: 'bold'
                        }
                    },
                    ticks: {
                        callback: function(value) {
                            return formatNumber(value);
                        }
                    },
                    grace: '10%',
                    afterBuildTicks: function(scale) {
                        // Ensure scale includes negative values
                        const minValue = Math.min(...ceData, ...peData);
                        const maxValue = Math.max(...ceData, ...peData);
                        
                        if (minValue < 0) {
                            scale.min = minValue;
                        }
                        if (maxValue > 0) {
                            scale.max = maxValue;
                        }
                    }
                },
                x: {
                    stacked: false,
                    title: {
                        display: true,
                        text: 'Strike Price',
                        font: {
                            weight: 'bold'
                        }
                    }
                }
            }
        },
        plugins: [currentPriceLinePlugin]
    };
    
    if (coiChartInstance) {
        coiChartInstance.data.labels = labels;
        coiChartInstance.data.datasets[0].data = ceData;
        coiChartInstance.data.datasets[0].backgroundColor = ceBackgroundColors;
        coiChartInstance.data.datasets[0].borderColor = ceBorderColors;
        coiChartInstance.data.datasets[1].data = peData;
        coiChartInstance.data.datasets[1].backgroundColor = peBackgroundColors;
        coiChartInstance.data.datasets[1].borderColor = peBorderColors;
        coiChartInstance.options.plugins.currentPriceLine.price = currentPrice;
        coiChartInstance.update('active');
    } else {
        coiChartInstance = new Chart(ctx, chartConfig);
    }
}

/**
 * Update last update time
 */
function updateLastUpdateTime() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-IN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true
    });
    document.getElementById('lastUpdateTime').textContent = timeStr;
}

/**
 * Format number with commas and abbreviations
 */
function formatNumber(num) {
    if (!num && num !== 0) return '--';
    
    const absNum = Math.abs(num);
    
    // Indian numbering system: K (thousand), L (lakh), C (crore)
    if (absNum >= 10000000) {
        return (num / 10000000).toFixed(1) + 'Cr';  // Crore
    } else if (absNum >= 100000) {
        return (num / 100000).toFixed(1) + 'L';   // Lakh
    } else if (absNum >= 1000) {
        return (num / 1000).toFixed(1) + 'K';     // Thousand
    }
    
    return num.toLocaleString('en-IN', {
        maximumFractionDigits: 0
    });
}

/**
 * Update Combined Stacked Chart - Shows OI with Change direction overlay
 */
function updateCombinedChart(labels, ceOI, peOI, ceCOI, peCOI, currentPrice) {
    const ctx = document.getElementById('combinedChart').getContext('2d');
    
    // OI colors: Dark green for PE, Dark red for CE
    const peColors = peOI.map((val, idx) => {
        const change = peCOI[idx];
        if (change > 0) {
            return 'rgba(22, 128, 67, 0.9)'; // Dark green for increase
        } else if (change < 0) {
            return 'rgba(22, 128, 67, 0.5)'; // Light dark green for decrease
        }
        return 'rgba(22, 128, 67, 0.9)'; // Dark green for no change
    });
    
    const ceColors = ceOI.map((val, idx) => {
        const change = ceCOI[idx];
        if (change > 0) {
            return 'rgba(153, 27, 27, 0.9)'; // Dark red for increase
        } else if (change < 0) {
            return 'rgba(153, 27, 27, 0.5)'; // Light dark red for decrease
        }
        return 'rgba(153, 27, 27, 0.9)'; // Dark red for no change
    });
    
    const peBorders = peCOI.map(change => 
        change > 0 ? 'rgba(22, 128, 67, 1)' : 'rgba(22, 128, 67, 0.7)'
    );
    
    const ceBorders = ceCOI.map(change => 
        change > 0 ? 'rgba(153, 27, 27, 1)' : 'rgba(153, 27, 27, 0.7)'
    );
    
    // Change in OI colors: Light red for Call, Light green for Put
    const peCoiColors = peCOI.map(val => 
        val >= 0 ? 'rgba(144, 238, 144, 0.8)' : 'rgba(144, 238, 144, 0.4)'
    );
    const peCoiBorders = peCOI.map(val => 
        val >= 0 ? 'rgba(34, 139, 34, 1)' : 'rgba(34, 139, 34, 0.6)'
    );
    
    const ceCoiColors = ceCOI.map(val => 
        val >= 0 ? 'rgba(255, 127, 127, 0.8)' : 'rgba(255, 127, 127, 0.4)'
    );
    const ceCoiBorders = ceCOI.map(val => 
        val >= 0 ? 'rgba(220, 20, 60, 1)' : 'rgba(220, 20, 60, 0.6)'
    );
    
    const chartConfig = {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Put OI',
                    data: peOI.map((val, idx) => val - peCOI[idx]),
                    backgroundColor: peColors,
                    borderColor: peBorders,
                    borderWidth: 2,
                    yAxisID: 'y',
                    stack: 'pe'
                },
                {
                    label: 'Put OI Change',
                    data: peCOI,
                    backgroundColor: peCoiColors,
                    borderColor: peCoiBorders,
                    borderWidth: 2,
                    yAxisID: 'y',
                    stack: 'pe'
                },
                {
                    label: 'Call OI',
                    data: ceOI.map((val, idx) => val - ceCOI[idx]),
                    backgroundColor: ceColors,
                    borderColor: ceBorders,
                    borderWidth: 2,
                    yAxisID: 'y',
                    stack: 'ce'
                },
                {
                    label: 'Call OI Change',
                    data: ceCOI,
                    backgroundColor: ceCoiColors,
                    borderColor: ceCoiBorders,
                    borderWidth: 2,
                    yAxisID: 'y',
                    stack: 'ce'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: {
                duration: 300
            },
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                currentPriceLine: {
                    price: currentPrice
                },
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        padding: 15,
                        font: {
                            size: 12,
                            weight: 'bold'
                        }
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    padding: 15,
                    titleFont: {
                        size: 12,
                        weight: 'bold'
                    },
                    bodyFont: {
                        size: 10,
                        family: 'Arial, sans-serif'
                    },
                    titleColor: '#000000',
                    bodyColor: '#000000',
                    borderColor: 'rgba(0, 0, 0, 0.3)',
                    borderWidth: 1,
                    displayColors: true,
                    boxPadding: 8,
                    callbacks: {
                        title: function(context) {
                            return `Strike ${context[0].label}`;
                        },
                        label: function(context) {
                            const idx = context.dataIndex;
                            
                            // Calculate OI at 9:15 AM (opening OI)
                            const peOIOpening = peOI[idx] - peCOI[idx];
                            const ceOIOpening = ceOI[idx] - ceCOI[idx];
                            
                            // Format based on dataset
                            if (context.datasetIndex === 0) {
                                // Put OI
                                return `Put OI at 9:15 AM   ${formatNumber(peOIOpening)}`;
                            } else if (context.datasetIndex === 1) {
                                // Put OI Change
                                const changeIndicator = peCOI[idx] >= 0 ? '+' : '';
                                return `Put OI chg          ${changeIndicator}${formatNumber(peCOI[idx])}`;
                            } else if (context.datasetIndex === 2) {
                                // Call OI
                                return `Call OI at 9:15 AM  ${formatNumber(ceOIOpening)}`;
                            } else if (context.datasetIndex === 3) {
                                // Call OI Change
                                const changeIndicator = ceCOI[idx] >= 0 ? '+' : '';
                                return `Call OI chg         ${changeIndicator}${formatNumber(ceCOI[idx])}`;
                            }
                            
                            return '';
                        },
                        afterLabel: function(context) {
                            const idx = context.dataIndex;
                            
                            // Show current OI at 3:30 PM (or current time)
                            if (context.datasetIndex === 1) {
                                // After Put OI Change, show Put OI at 3:30 PM
                                return `Put OI at 3:30 PM   ${formatNumber(peOI[idx])}`;
                            } else if (context.datasetIndex === 3) {
                                // After Call OI Change, show Call OI at 3:30 PM
                                return `Call OI at 3:30 PM  ${formatNumber(ceOI[idx])}`;
                            }
                            
                            return '';
                        }
                    }
                }
            },
            scales: {
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    stacked: true,
                    title: {
                        display: true,
                        text: 'Open Interest & Change in OI',
                        font: {
                            weight: 'bold'
                        }
                    },
                    ticks: {
                        callback: function(value) {
                            return formatNumber(value);
                        }
                    }
                },
                x: {
                    stacked: true,
                    title: {
                        display: true,
                        text: 'Strike Price',
                        font: {
                            weight: 'bold'
                        }
                    }
                }
            }
        },
        plugins: [currentPriceLinePlugin]
    };
    
    if (combinedChartInstance) {
        combinedChartInstance.data.labels = labels;
        combinedChartInstance.data.datasets[0].data = peOI.map((val, idx) => val - peCOI[idx]);
        combinedChartInstance.data.datasets[0].backgroundColor = peColors;
        combinedChartInstance.data.datasets[0].borderColor = peBorders;
        combinedChartInstance.data.datasets[1].data = peCOI;
        combinedChartInstance.data.datasets[1].backgroundColor = peCoiColors;
        combinedChartInstance.data.datasets[1].borderColor = peCoiBorders;
        combinedChartInstance.data.datasets[2].data = ceOI.map((val, idx) => val - ceCOI[idx]);
        combinedChartInstance.data.datasets[2].backgroundColor = ceColors;
        combinedChartInstance.data.datasets[2].borderColor = ceBorders;
        combinedChartInstance.data.datasets[3].data = ceCOI;
        combinedChartInstance.data.datasets[3].backgroundColor = ceCoiColors;
        combinedChartInstance.data.datasets[3].borderColor = ceCoiBorders;
        combinedChartInstance.options.plugins.currentPriceLine.price = currentPrice;
        combinedChartInstance.update('active');
    } else {
        combinedChartInstance = new Chart(ctx, chartConfig);
    }
}

// Cleanup on page unload
window.addEventListener('beforeunload', function() {
    stopAutoRefresh();
    if (oiChartInstance) {
        oiChartInstance.destroy();
    }
    if (coiChartInstance) {
        coiChartInstance.destroy();
    }
    if (combinedChartInstance) {
        combinedChartInstance.destroy();
    }
});
