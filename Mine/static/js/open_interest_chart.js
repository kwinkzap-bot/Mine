/**
 * Open Interest Chart - Real-time open interest visualization
 * Fetches data every 30 seconds and displays in chart format
 */

let oiChartInstance = null;
let coiChartInstance = null;
let autoRefreshInterval = null;
let currentSymbol = 'NIFTY';

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('Open Interest Chart - Initializing...');
    
    // Event listeners
    document.getElementById('symbolSelect').addEventListener('change', onSymbolChange);
    document.getElementById('refreshNowBtn').addEventListener('click', refreshData);
    document.getElementById('autoRefreshToggle').addEventListener('change', onAutoRefreshToggle);
    
    // Initial load
    refreshData();
    
    // Start auto-refresh if checked
    if (document.getElementById('autoRefreshToggle').checked) {
        startAutoRefresh();
    }
});

/**
 * Handle symbol change
 */
function onSymbolChange(e) {
    currentSymbol = e.target.value;
    console.log(`Symbol changed to: ${currentSymbol}`);
    refreshData();
}

/**
 * Handle auto-refresh toggle
 */
function onAutoRefreshToggle(e) {
    if (e.target.checked) {
        console.log('Auto-refresh enabled');
        startAutoRefresh();
    } else {
        console.log('Auto-refresh disabled');
        stopAutoRefresh();
    }
}

/**
 * Start auto-refresh every 30 seconds
 */
function startAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
    }
    
    autoRefreshInterval = setInterval(() => {
        console.log(`Auto-refreshing OI data for ${currentSymbol}...`);
        refreshData();
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
async function refreshData() {
    const loaderEl = document.getElementById('oiLoader');
    const errorEl = document.getElementById('oiError');
    const contentEl = document.querySelector('.oi-content');
    
    try {
        loaderEl.classList.remove('hidden');
        errorEl.classList.add('hidden');
        contentEl.style.opacity = '0.5';
        
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
        
        // Update UI with data
        updateSummaryStats(data);
        updateCharts(data);
        updateTable(data);
        updateLastUpdateTime();
        
        loaderEl.classList.add('hidden');
        contentEl.style.opacity = '1';
        
    } catch (error) {
        console.error('Error fetching OI data:', error);
        loaderEl.classList.add('hidden');
        errorEl.classList.remove('hidden');
        document.getElementById('errorMessage').textContent = `Error: ${error.message}`;
        contentEl.style.opacity = '1';
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
}

/**
 * Update charts
 */
function updateCharts(data) {
    const strikes = data.strikes || [];
    const ceOI = strikes.map(s => s.ce_oi || 0);
    const peOI = strikes.map(s => s.pe_oi || 0);
    const ceCOI = strikes.map(s => s.ce_change_in_oi || 0);
    const peCOI = strikes.map(s => s.pe_change_in_oi || 0);
    const strikeLabels = strikes.map(s => s.strike);
    
    // Update OI Chart
    updateOIChart(strikeLabels, ceOI, peOI);
    
    // Update Change in OI Chart
    updateCOIChart(strikeLabels, ceCOI, peCOI);
}

/**
 * Update Open Interest Bar Chart
 */
function updateOIChart(labels, ceData, peData) {
    const ctx = document.getElementById('oiChart').getContext('2d');
    
    if (oiChartInstance) {
        oiChartInstance.destroy();
    }
    
    oiChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'CE Open Interest',
                    data: ceData,
                    backgroundColor: 'rgba(39, 174, 96, 0.7)',
                    borderColor: 'rgba(39, 174, 96, 1)',
                    borderWidth: 1,
                    yAxisID: 'y'
                },
                {
                    label: 'PE Open Interest',
                    data: peData,
                    backgroundColor: 'rgba(231, 76, 60, 0.7)',
                    borderColor: 'rgba(231, 76, 60, 1)',
                    borderWidth: 1,
                    yAxisID: 'y'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return `${context.dataset.label}: ${formatNumber(context.parsed.y)}`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'Open Interest',
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
                    title: {
                        display: true,
                        text: 'Strike Price',
                        font: {
                            weight: 'bold'
                        }
                    }
                }
            }
        }
    });
}

/**
 * Update Change in OI Line Chart
 */
function updateCOIChart(labels, ceData, peData) {
    const ctx = document.getElementById('coiChart').getContext('2d');
    
    if (coiChartInstance) {
        coiChartInstance.destroy();
    }
    
    coiChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'CE Change in OI',
                    data: ceData,
                    borderColor: 'rgba(39, 174, 96, 1)',
                    backgroundColor: 'rgba(39, 174, 96, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 4,
                    pointBackgroundColor: 'rgba(39, 174, 96, 1)',
                    pointBorderColor: 'white',
                    pointBorderWidth: 2,
                    yAxisID: 'y'
                },
                {
                    label: 'PE Change in OI',
                    data: peData,
                    borderColor: 'rgba(231, 76, 60, 1)',
                    backgroundColor: 'rgba(231, 76, 60, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 4,
                    pointBackgroundColor: 'rgba(231, 76, 60, 1)',
                    pointBorderColor: 'white',
                    pointBorderWidth: 2,
                    yAxisID: 'y'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const value = context.parsed.y;
                            const sign = value >= 0 ? '+' : '';
                            return `${context.dataset.label}: ${sign}${formatNumber(value)}`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'Change in OI',
                        font: {
                            weight: 'bold'
                        }
                    },
                    ticks: {
                        callback: function(value) {
                            const sign = value >= 0 ? '+' : '';
                            return `${sign}${formatNumber(value)}`;
                        }
                    }
                },
                x: {
                    title: {
                        display: true,
                        text: 'Strike Price',
                        font: {
                            weight: 'bold'
                        }
                    }
                }
            }
        }
    });
}

/**
 * Update table with strike data
 */
function updateTable(data) {
    const tableBody = document.getElementById('oiTableBody');
    tableBody.innerHTML = '';
    
    const strikes = data.strikes || [];
    
    strikes.forEach(strike => {
        const row = document.createElement('tr');
        
        const ceCoiColor = strike.ce_change_in_oi >= 0 ? 'positive' : 'negative';
        const peCoiColor = strike.pe_change_in_oi >= 0 ? 'positive' : 'negative';
        
        row.innerHTML = `
            <td>${strike.strike}</td>
            <td class="ce-data">${formatNumber(strike.ce_oi)}</td>
            <td class="ce-data ${ceCoiColor}">${formatNumber(strike.ce_change_in_oi)}</td>
            <td class="ce-data">${(strike.ce_iv || 0).toFixed(2)}</td>
            <td class="pe-data">${formatNumber(strike.pe_oi)}</td>
            <td class="pe-data ${peCoiColor}">${formatNumber(strike.pe_change_in_oi)}</td>
            <td class="pe-data">${(strike.pe_iv || 0).toFixed(2)}</td>
        `;
        
        tableBody.appendChild(row);
    });
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
    
    if (absNum >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    } else if (absNum >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    
    return num.toLocaleString('en-IN', {
        maximumFractionDigits: 0
    });
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
});
