/**
 * Open Interest Chart - Real-time open interest visualization
 * Fetches data every 30 seconds and displays in chart format
 */

let coiChartInstance = null;
let combinedChartInstance = null;
// Shared state for combinedChart tooltip callbacks — avoids closure-over-param stale data.
let _combinedChartState = { ceOI: [], peOI: [], ceCOI: [], peCOI: [] };
let autoRefreshInterval = null;
let currentSymbol = 'NIFTY';
let strikeRangeCount = 15; // Default strike range
let cachedData = null; // Store fetched data to avoid repeated API calls
let oiHistoryData = []; // Store OI history for the table (keep last 20 entries)
let allSymbols = []; // Store all available symbols for custom dropdown
const MAX_HISTORY_ROWS = 20;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function () {
    console.log('Open Interest Chart - Initializing...');

    // Event listeners
    const symbolInput = document.getElementById('symbolSelect');
    const symbolList = document.getElementById('symbolDropdownList');

    // Input: Filter list
    symbolInput.addEventListener('input', function (e) {
        renderDropdown(e.target.value);
    });

    // Focus: Clear and/or Show list
    symbolInput.addEventListener('focus', function (e) {
        // Clear value on click/focus per user request
        this.value = '';
        renderDropdown(''); // Show full list
    });

    // Blur: Restore if empty & Hide list
    symbolInput.addEventListener('blur', function (e) {
        // Delay hiding to allow click event on list item to fire
        setTimeout(() => {
            symbolList.classList.remove('show');
            if (!this.value.trim()) {
                this.value = currentSymbol;
            }
        }, 200);
    });

    // Keydown: Handle Enter for custom selection or blur
    symbolInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            // If list is visible and has items, select first?
            // Or just trigger refresh with current text?
            // Let's stick to current text if valid, or just blur to trigger restore/refresh
            symbolList.classList.remove('show');

            let val = e.target.value.trim().toUpperCase();
            if (val) {
                // Check if valid symbol?
                if (allSymbols.includes(val) || val === 'NIFTY' || val === 'BANKNIFTY') {
                    selectSymbol(val);
                } else {
                    // Just try it?
                    selectSymbol(val);
                }
            } else {
                e.target.blur();
            }
        }
    });

    // Click outside to close
    document.addEventListener('click', function (e) {
        if (!symbolInput.contains(e.target) && !symbolList.contains(e.target)) {
            symbolList.classList.remove('show');
        }
    });

    document.getElementById('refreshNowBtn').addEventListener('click', refreshData);

    // Strike range button listeners - only manipulate cached data, no API call
    document.querySelectorAll('.strike-btn[data-strikes]').forEach(btn => {
        btn.addEventListener('click', onStrikeRangeChange);
    });

    // Fetch symbols first, then load data
    fetchSymbols().then(() => {
        // Initial load
        refreshData();

        // Start auto-refresh on every 30 seconds during market hours
        startAutoRefresh();
    });

    // Bootstrap dynamic theme from localStorage
    const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'dark';
    updateOpenInterestTheme(activeTheme);

    // Event listener for global theme changes
    window.addEventListener('themechanged', function (e) {
        updateOpenInterestTheme(e.detail.theme);
    });
});

/**
 * Fetch available symbols (Indices + F&O Stocks)
 */
/**
 * Fetch available symbols (Indices + F&O Stocks)
 */
async function fetchSymbols() {
    try {
        const response = await fetch('/api/symbols');
        if (!response.ok) throw new Error('Failed to fetch symbols');

        const data = await response.json();
        if (data.success && data.symbols && data.symbols.length > 0) {
            allSymbols = data.symbols;
            console.log(`Loaded ${allSymbols.length} symbols`);
        }
    } catch (error) {
        console.error('Error fetching symbols:', error);
    }
}

/**
 * Render custom dropdown list
 */
function renderDropdown(filterText = '') {
    const list = document.getElementById('symbolDropdownList');
    list.innerHTML = '';

    // Create map for display text
    const displayMap = {
        'NIFTY': 'NIFTY 50',
        'BANKNIFTY': 'NIFTY BANK',
        'FINNIFTY': 'NIFTY FIN SERVICE',
        'SENSEX': 'SENSEX'
    };

    const filter = filterText.toUpperCase();

    // Filter symbols
    const matches = allSymbols.filter(s => {
        if (!filter) return true; // Show all if no filter
        return s.includes(filter) || (displayMap[s] && displayMap[s].includes(filter));
    });

    if (matches.length === 0) {
        list.classList.remove('show');
        return;
    }

    matches.forEach(symbol => {
        const li = document.createElement('li');
        const displayName = displayMap[symbol] || symbol;

        li.textContent = displayName;
        if (displayName !== symbol) {
            li.innerHTML = `<strong>${displayName}</strong> <span style="font-size:0.8em; color:#888">(${symbol})</span>`;
        }

        if (symbol === currentSymbol) {
            li.classList.add('highlighted');
        }

        li.addEventListener('click', () => {
            selectSymbol(symbol);
        });

        list.appendChild(li);
    });

    list.classList.add('show');
}

/**
 * Handle symbol selection
 */
function selectSymbol(symbol) {
    const input = document.getElementById('symbolSelect');
    const list = document.getElementById('symbolDropdownList');

    currentSymbol = symbol;
    input.value = symbol;

    list.classList.remove('show');

    console.log(`Symbol selected: ${currentSymbol}`);
    refreshData(true);
}




/**
 * Handle strike range change - only updates charts with cached data, no API call
 */
function onStrikeRangeChange(e) {
    const strikesAttr = e.target.getAttribute('data-strikes');
    if (!strikesAttr) return; // Prevent collapse if button doesn't have data-strikes attribute (e.g. theme toggle button)
    const newCount = parseInt(strikesAttr);
    if (isNaN(newCount)) return;
    strikeRangeCount = newCount;
    console.log(`Strike range changed to: ${strikeRangeCount}`);

    // Update active button styling
    document.querySelectorAll('.strike-btn[data-strikes]').forEach(btn => {
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
            refreshData(false);  // No loader animation on auto-refresh, but update all blocks
        } else {
            console.log('Market hours closed. Auto-refresh paused.');
        }
    }, 60000); // 1 minute
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
async function refreshData(showLoader = true, updateSummary = true) {
    const errorEl = document.getElementById('oiError');
    const refreshBtn = document.getElementById('refreshNowBtn');

    // Add spinning animation to refresh button (only for manual refresh)
    if (showLoader) {
        refreshBtn.classList.add('refreshing');
    }

    try {
        errorEl.classList.add('hidden');

        console.log(`Fetching OI data for ${currentSymbol}...`);

        // Add cache-busting timestamp to ensure fresh data
        const timestamp = new Date().getTime();

        const response = await fetch('/api/open-interest', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            },
            body: JSON.stringify({
                symbol: currentSymbol,
                _t: timestamp  // Cache-busting parameter
            }),
            cache: 'no-store'  // Disable browser caching
        });

        if (!response.ok) {
            throw new Error(`API Error: ${response.statusText}`);
        }

        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || 'Failed to fetch open interest data');
        }

        console.log(`✅ OI data received for ${currentSymbol}:`, data);

        // Log server timestamp for debugging data freshness
        if (data.server_timestamp) {
            console.log(`Server timestamp: ${data.server_timestamp}`);
            const serverTime = new Date(data.server_timestamp);
            console.log(`Latest OI values - CE: ${data.ce_summary?.total_oi}, PE: ${data.pe_summary?.total_oi}, Server time: ${serverTime.toLocaleTimeString()}`);
        }

        // Cache the data for later use when strike range changes
        cachedData = data;

        // Always update all UI blocks — Market Metrics, charts, and summary table
        updateSummaryStats(data);
        updateCharts(data);
        updateLastUpdateTime(data.server_timestamp);

        // Remove spinning animation from refresh button
        if (showLoader) {
            refreshBtn.classList.remove('refreshing');
        }

    } catch (error) {
        console.error('Error fetching OI data:', error);
        errorEl.classList.remove('hidden');
        document.getElementById('errorMessage').textContent = `Error: ${error.message}`;

        // Remove spinning animation from refresh button
        if (showLoader) {
            refreshBtn.classList.remove('refreshing');
        }
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
    const ceMaxStrike = ceStats.max_oi_strike || 0;
    document.getElementById('ceMaxOIStrike').textContent = ceMaxStrike || '--';
    document.getElementById('ceMaxOIValue').textContent = formatNumber(ceStats.max_oi_value);

    // PE Summary
    document.getElementById('peOITotal').textContent = formatNumber(peStats.total_oi);
    document.getElementById('peCOI').textContent = formatNumber(peStats.change_in_oi);
    const peMaxStrike = peStats.max_oi_strike || 0;
    document.getElementById('peMaxOIStrike').textContent = peMaxStrike || '--';
    document.getElementById('peMaxOIValue').textContent = formatNumber(peStats.max_oi_value);

    // Market Metrics
    const pcrValue = data.pcr_oi || 0;
    const maxPain = data.max_pain || 0;
    const currentPrice = data.current_price || 0;
    // --- Straddle / ATM Strike ---
    // --- Straddle / ATM Strike ---
    const strikes = data.strikes || [];
    let straddleStrike = 0;

    // Logic: Find strike where both CE and PE are substantial (> 50L) and comparable
    // Heuristic: Maximize min(CE, PE)
    // This naturally finds the strike where *both* sides are strongest.

    const STRADDLE_THRESHOLD = 5000000; // 50 Lakhs
    let maxMinOI = 0;

    strikes.forEach(s => {
        const ceOI = s.ce_oi || 0;
        const peOI = s.pe_oi || 0;

        if (ceOI > STRADDLE_THRESHOLD && peOI > STRADDLE_THRESHOLD) {
            const minOI = Math.min(ceOI, peOI);
            if (minOI > maxMinOI) {
                maxMinOI = minOI;
                straddleStrike = s.strike;
            }
        }
    });

    // Fallback to ATM if no straddle criteria met
    if (straddleStrike === 0 && currentPrice > 0 && strikes.length > 0) {
        let minDiff = Number.MAX_VALUE;
        strikes.forEach(s => {
            const diff = Math.abs(s.strike - currentPrice);
            if (diff < minDiff) {
                minDiff = diff;
                straddleStrike = s.strike;
            }
        });
    }

    const straddleElement = document.getElementById('straddleStrike');
    if (straddleElement) {
        if (maxMinOI > 0) {
            straddleElement.textContent = straddleStrike + ' (Active)';
            straddleElement.className = 'value active-straddle'; // Add a class for styling
            straddleElement.style.color = 'var(--oip-accent)'; // Dynamic theme accent color for Active Straddle
            straddleElement.style.fontWeight = 'bold';
        } else {
            straddleElement.textContent = straddleStrike + ' (ATM)';
            straddleElement.className = 'value';
            straddleElement.style.color = 'var(--oip-text)'; // Dynamic theme text color for ATM fallback
            straddleElement.style.fontWeight = 'normal';
        }
    }

    // --- Support & Resistance (Advanced Logic) ---
    // Criteria: PE/CE OI > 1 Cr (10,000,000) AND Opposite Leg OI < 50% of Dominant Leg
    const THRESHOLD_OI = 10000000; // 1 Crore

    // Find Support (Highest PE OI below or at ATM meeting criteria)
    // UPDATE: User requested "Nearest" Support. So we find the *largest* strike <= Straddle that meets criteria.
    // Iterating downwards from Straddle would be best, but filter sorts by Default? No, Filter keeps original order.
    // Strikes are usually sorted ascending.

    let validSupport = 0;

    // Create a reversed copy of strikes <= straddle to iterate downwards from ATM
    const supportCandidates = strikes.filter(s => s.strike <= straddleStrike + 50)
        .sort((a, b) => b.strike - a.strike); // Descending order

    for (const s of supportCandidates) {
        const peOI = s.pe_oi || 0;
        const ceOI = s.ce_oi || 0;

        if (peOI > THRESHOLD_OI && ceOI < (peOI * 0.5)) {
            validSupport = s.strike;
            break; // Found the nearest one!
        }
    }

    // Find Resistance (Highest CE OI above or at ATM meeting criteria)
    // UPDATE: User requested "Nearest" Resistance. So we find the *smallest* strike >= Straddle that meets criteria.
    let validResistance = 0;

    const resistanceCandidates = strikes.filter(s => s.strike >= straddleStrike - 50)
        .sort((a, b) => a.strike - b.strike); // Ascending order

    for (const s of resistanceCandidates) {
        const ceOI = s.ce_oi || 0;
        const peOI = s.pe_oi || 0;

        if (ceOI > THRESHOLD_OI && peOI < (ceOI * 0.5)) {
            validResistance = s.strike;
            break; // Found the nearest one!
        }
    }

    // Fallback: If advanced criteria not met, show just Max OI but mark as weak? 
    // Or strictly follow user request. User said "Nearest support... means value should be..." 
    // implying strict definition. I will show "--" or "Weak" if criteria failing, 
    // but to be safe, let's show the Max OI but maybe color it differently or just show it.
    // Actually, let's prioritize the "Valid" ones. If no valid one found, we return the Max OI from summary stats but maybe add a note?
    // Let's stick to the Valid one if found, else fall back to the generic Max OI but maybe with a visual cue.

    const supportElement = document.getElementById('supportLevel');
    if (supportElement) {
        if (validSupport > 0) {
            supportElement.textContent = validSupport;
            supportElement.className = 'value color-green';
        } else {
            // Fallback to generic max but neutral color
            supportElement.textContent = (peMaxStrike || '--') + ' (Weak)';
            supportElement.className = 'value';
        }
    }

    // UPDATE Summary Cards with Calculated S/R
    // PE Summary (Support)
    // UPDATE Summary Cards with Absolute Max OI (as requested by user)
    // PE Summary (Max PE OI Strike)
    const peMaxOIStrikeElem = document.getElementById('peMaxOIStrike');
    if (peMaxOIStrikeElem) {
        peMaxOIStrikeElem.textContent = peMaxStrike || '--';
        peMaxOIStrikeElem.style.fontWeight = 'normal';
        peMaxOIStrikeElem.title = 'Absolute Max OI Strike';
    }

    // Resistance Logic (Restored) for Market Metrics
    const resistanceElement = document.getElementById('resistanceLevel');
    if (resistanceElement) {
        if (validResistance > 0) {
            resistanceElement.textContent = validResistance;
            resistanceElement.className = 'value color-red';
        } else {
            resistanceElement.textContent = (ceMaxStrike || '--') + ' (Weak)';
            resistanceElement.className = 'value';
        }
    }

    // CE Summary (Max CE OI Strike)
    const ceMaxOIStrikeElem = document.getElementById('ceMaxOIStrike');
    if (ceMaxOIStrikeElem) {
        ceMaxOIStrikeElem.textContent = ceMaxStrike || '--';
        ceMaxOIStrikeElem.style.fontWeight = 'normal';
        ceMaxOIStrikeElem.title = 'Absolute Max OI Strike';
    }

    // --- Trend Determination ---
    // Logic: 
    // PCR > 1.0 = Bullish
    // PCR < 0.7 = Bearish
    // Range 0.7 - 1.0 = Neutral/Sideways
    // Confirmation from Change in OI Difference
    const trendElement = document.getElementById('marketTrend');
    let trendText = 'Neutral';
    let trendClass = 'status-sideway'; // class from CSS (we might need to add these classes or use existing color classes)

    const oiChangeDiff = (peStats.change_in_oi || 0) - (ceStats.change_in_oi || 0);

    if (pcrValue >= 1.0) {
        trendText = 'BULLISH';
        trendClass = 'color-green';
        if (pcrValue > 1.2) trendText = 'STRONG BULLISH';
    } else if (pcrValue <= 0.7) {
        trendText = 'BEARISH';
        trendClass = 'color-red';
        if (pcrValue < 0.5) trendText = 'STRONG BEARISH';
    } else {
        // Between 0.7 and 1.0 - check momentum
        if (oiChangeDiff > 100000) { // Significant PE writing
            trendText = 'MILD BULLISH';
            trendClass = 'color-green';
        } else if (oiChangeDiff < -100000) { // Significant CE writing
            trendText = 'MILD BEARISH';
            trendClass = 'color-red';
        } else {
            trendText = 'SIDEWAYS';
            trendClass = '';
        }
    }

    if (trendElement) {
        trendElement.textContent = trendText;
        trendElement.className = 'value ' + trendClass; // Reset class and add new one
        trendElement.style.fontWeight = 'bold';
    }

    const pcrElement = document.getElementById('pcrOI');
    pcrElement.textContent = pcrValue.toFixed(2);
    // PCR: Below 1 = Red (bearish), Above 1 = Green (bullish)
    pcrElement.classList.remove('color-red', 'color-green');
    if (pcrValue < 0.8) {
        pcrElement.classList.add('color-red');
    } else if (pcrValue > 1) {
        pcrElement.classList.add('color-green');
    }

    const maxPainElement = document.getElementById('maxPain');
    maxPainElement.textContent = maxPain;
    // // Max Pain: Below current price = Red, Above current price = Green
    // maxPainElement.classList.remove('color-red', 'color-green');
    // if (maxPain < currentPrice) {
    //     maxPainElement.classList.add('color-red');
    // } else if (maxPain > currentPrice) {
    //     maxPainElement.classList.add('color-green');
    // }

    // IV Percentile
    const ivPercentile = data.iv_percentile !== undefined ? data.iv_percentile : null;
    const ivElement = document.getElementById('ivPercentile');

    if (ivPercentile !== null) {
        // Determine label and color based on percentile value
        let ivLabel = '';
        let ivClass = '';

        if (ivPercentile >= 80) {
            ivLabel = 'Very High Premium';
            ivClass = 'color-red';
        } else if (ivPercentile >= 70) {
            ivLabel = 'High Premium';
            ivClass = 'color-orange';
        } else if (ivPercentile >= 30) {
            ivLabel = 'Medium';
            ivClass = ''; // Default color
        } else if (ivPercentile >= 20) {
            ivLabel = 'Low Premium';
            ivClass = 'color-green';
        } else {
            ivLabel = 'Very Low Premium';
            ivClass = 'color-bright-green';
        }

        ivElement.textContent = `${ivPercentile.toFixed(1)}% - ${ivLabel}`;

        // Remove all color classes first
        ivElement.classList.remove('color-red', 'color-orange', 'color-green', 'color-bright-green');

        // Add applicable class if any
        if (ivClass) {
            ivElement.classList.add(ivClass);
        }
    } else {
        ivElement.textContent = '--';
    }

    // Update OI Summary Table with current data
    updateOISummaryTable(peStats, ceStats);
}

/**
 * Update OI Summary Table with historical data
 */
function updateOISummaryTable(peStats, ceStats) {
    try {
        // Get current time
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: true
        });

        // Calculate differences
        const totalPeOI = peStats.total_oi || 0;
        const totalCeOI = ceStats.total_oi || 0;
        const peCOI = peStats.change_in_oi || 0;
        const ceCOI = ceStats.change_in_oi || 0;

        const oiDifferent = totalPeOI - totalCeOI;
        const oiChangeDifferent = peCOI - ceCOI;

        // Create history entry
        const historyEntry = {
            time: timeStr,
            totalPeOI: totalPeOI,
            peCOI: peCOI,
            totalCeOI: totalCeOI,
            ceCOI: ceCOI,
            oiDifferent: oiDifferent,
            oiChangeDifferent: oiChangeDifferent
        };

        // Add to beginning of array (latest first)
        oiHistoryData.unshift(historyEntry);

        // Keep only last 20 entries
        if (oiHistoryData.length > MAX_HISTORY_ROWS) {
            oiHistoryData.pop();
        }

        // Render table
        renderOISummaryTable();
    } catch (error) {
        console.error('Error updating OI summary table:', error);
    }
}

/**
 * Render OI Summary Table
 */
function renderOISummaryTable() {
    try {
        const tbody = document.getElementById('oiSummaryTableBody');
        if (!tbody) return;

        // Clear existing rows
        tbody.innerHTML = '';

        // If no data, show loading message
        if (oiHistoryData.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; padding: 20px;">No data available</td></tr>';
            return;
        }

        // Render each history entry
        oiHistoryData.forEach((entry, index) => {
            const row = document.createElement('tr');

            // Format PE OI Change with color
            const peCOIClass = entry.peCOI >= 0 ? '' : 'negative';
            const peCOISign = entry.peCOI >= 0 ? '+' : '';

            // Format CE OI Change with color
            const ceCOIClass = entry.ceCOI >= 0 ? '' : 'negative';
            const ceCOISign = entry.ceCOI >= 0 ? '+' : '';

            // Calculate Diff: PE Change - CE Change (Net Sentiment)
            const diffValue = entry.oiChangeDifferent;
            const diffClass = diffValue > 0 ? 'positive' : (diffValue < 0 ? 'negative' : '');
            const diffSign = diffValue > 0 ? '+' : '';

            // Calculate Desc (Momentum): Previous Diff - Current Diff
            // Since array is sorted descending (newest first), "Previous" time is at index + 1
            let descValue = 0;
            let descClass = '';
            let descSign = '';

            if (index < oiHistoryData.length - 1) {
                const prevEntry = oiHistoryData[index + 1];
                // Formula: Previous Time's Diff - Current Time's Diff
                // Note: The user requested "Previous - Current", where "Previous" implies t-1.
                // Let's verify: If Prev(t-1) was 100 and Curr(t) is 150, sentiment increased.
                // Formula: 100 - 150 = -50.
                // If Prev(t-1) was 150 and Curr(t) is 100, sentiment decreased.
                // Formula: 150 - 100 = +50.

                // Wait, if sentiment *improved* (100 -> 150), we usually want Green (+50).
                // But the user specific "Previous - Current".
                // Let's stick strictly to the user's requested formula: "Previous OI CHG DIFF - Current OI CHG DIFF"
                descValue = prevEntry.oiChangeDifferent - diffValue;

                descClass = descValue > 0 ? 'positive' : (descValue < 0 ? 'negative' : '');
                descSign = descValue > 0 ? '+' : '';
            }

            row.innerHTML = `
                <td>${entry.time}</td>
                <td><span class="oi-pe-oi">${formatNumberForGrid(entry.totalPeOI)}</span></td>
                <td><span class="oi-pe-chg ${peCOIClass}">${peCOISign}${formatNumberForGrid(entry.peCOI)}</span></td>
                <td><span class="oi-ce-oi">${formatNumberForGrid(entry.totalCeOI)}</span></td>
                <td><span class="oi-ce-chg ${ceCOIClass}">${ceCOISign}${formatNumberForGrid(entry.ceCOI)}</span></td>
                <td><span class="oi-change-diff ${diffClass}">${diffSign}${formatNumberForGrid(diffValue)}</span></td>
                <td><span class="oi-change-diff ${descClass}">${descSign}${formatNumberForGrid(descValue)}</span></td>
            `;

            tbody.appendChild(row);
        });
    } catch (error) {
        console.error('Error rendering OI summary table:', error);
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
            const activeTheme = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'dark';
            const isLightBg = ['light', 'cream', 'ocean'].includes(activeTheme);
            // Draw vertical dashed line
            ctx.save();
            ctx.strokeStyle = isLightBg ? 'rgba(0, 0, 0, 0.5)' : 'rgba(255, 255, 255, 0.5)';
            ctx.lineWidth = 2;
            ctx.setLineDash([5, 5]);

            ctx.beginPath();
            ctx.moveTo(xPixel, yAxis.top);
            ctx.lineTo(xPixel, yAxis.bottom);
            ctx.stroke();

            // Draw label text only (no background or border)
            ctx.font = 'bold 11px Arial';
            ctx.textAlign = 'center';
            ctx.fillStyle = isLightBg ? 'rgba(0, 0, 0, 0.8)' : 'rgba(255, 255, 255, 0.8)';

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
    // PE (Put OI Change) = Green, CE (Call OI Change) = Red
    const peBackgroundColors = peData.map(val => val >= 0 ? 'rgba(144, 238, 144, 0.8)' : 'rgba(144, 238, 144, 0.4)');
    const peBorderColors = peData.map(val => val >= 0 ? 'rgba(34, 139, 34, 1)' : 'rgba(34, 139, 34, 0.6)');
    const ceBackgroundColors = ceData.map(val => val >= 0 ? 'rgba(255, 127, 127, 0.8)' : 'rgba(255, 127, 127, 0.4)');
    const ceBorderColors = ceData.map(val => val >= 0 ? 'rgba(220, 20, 60, 1)' : 'rgba(220, 20, 60, 0.6)');

    const themeCfg = getActiveChartTheme();

    const chartConfig = {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Put OI chg',
                    data: peData,
                    backgroundColor: peBackgroundColors,
                    borderColor: peBorderColors,
                    borderWidth: 1,
                    borderRadius: 0,
                    yAxisID: 'y'
                },
                {
                    label: 'Call OI chg',
                    data: ceData,
                    backgroundColor: ceBackgroundColors,
                    borderColor: ceBorderColors,
                    borderWidth: 1,
                    borderRadius: 0,
                    yAxisID: 'y'
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
                    align: 'center',
                    labels: {
                        padding: 15,
                        color: themeCfg.text,
                        font: {
                            size: 12,
                            weight: 'bold'
                        },
                        boxWidth: 15,
                        boxHeight: 15,
                        generateLabels: function (chart) {
                            const datasets = chart.data.datasets;
                            const labels = [];

                            datasets.forEach((dataset, index) => {
                                // Calculate total change for this dataset
                                const total = dataset.data.reduce((sum, val) => sum + val, 0);
                                const indicator = total > 0 ? '+' : total < 0 ? '' : '';
                                // Convert to Lakhs and force 2 decimal places
                                const inLakhs = Math.abs(total) / 100000;
                                let decimalValue = inLakhs.toFixed(2);

                                labels.push({
                                    text: `${dataset.label}  ${indicator}${decimalValue}L`,
                                    fillStyle: dataset.backgroundColor[0] || '#999',
                                    hidden: false,
                                    index: index,
                                    fontColor: themeCfg.text
                                });
                            });

                            return labels;
                        }
                    },
                    border: {
                        color: themeCfg.grid,
                        width: 1
                    },
                    backgroundColor: themeCfg.tooltipBg
                },
                tooltip: {
                    backgroundColor: themeCfg.tooltipBg,
                    padding: 12,
                    titleFont: {
                        size: 13,
                        weight: 'bold'
                    },
                    bodyFont: {
                        size: 12,
                        lineHeight: 1.5
                    },
                    titleColor: themeCfg.tooltipText,
                    bodyColor: themeCfg.tooltipText,
                    borderColor: themeCfg.tooltipBorder,
                    borderWidth: 1,
                    displayColors: true,
                    boxPadding: 8,
                    callbacks: {
                        title: function (context) {
                            return `Strike: ${context[0].label}`;
                        },
                        label: function (context) {
                            const datasetLabel = context.dataset.label;
                            const value = context.parsed.y;
                            const changeIndicator = value > 0 ? '↑' : value < 0 ? '↓' : '→';

                            return `${datasetLabel}: ${changeIndicator} ${formatNumber(value)}`;
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
                    title: {
                        display: false
                    },
                    grid: {
                        color: themeCfg.grid,
                        drawBorder: true,
                        borderColor: themeCfg.grid
                    },
                    ticks: {
                        color: themeCfg.text,
                        font: {
                            size: 10
                        },
                        callback: function (value) {
                            return formatNumber(value);
                        }
                    }
                },
                x: {
                    stacked: false,
                    title: {
                        display: false
                    },
                    grid: {
                        color: themeCfg.vgrid || themeCfg.grid,
                        drawBorder: true,
                        borderColor: themeCfg.grid
                    },
                    ticks: {
                        color: themeCfg.text,
                        font: {
                            size: 10
                        }
                    }
                }
            }
        },
        plugins: [currentPriceLinePlugin]
    };

    if (coiChartInstance) {
        // Fast update path — avoids DOM teardown on every 60s poll.
        coiChartInstance.data.labels = labels;
        coiChartInstance.data.datasets[0].data = peData;
        coiChartInstance.data.datasets[0].backgroundColor = peBackgroundColors;
        coiChartInstance.data.datasets[0].borderColor = peBorderColors;
        coiChartInstance.data.datasets[1].data = ceData;
        coiChartInstance.data.datasets[1].backgroundColor = ceBackgroundColors;
        coiChartInstance.data.datasets[1].borderColor = ceBorderColors;
        coiChartInstance.options.plugins.currentPriceLine.price = currentPrice;
        coiChartInstance.options.plugins.legend.labels.color = themeCfg.text;
        coiChartInstance.options.plugins.tooltip.backgroundColor = themeCfg.tooltipBg;
        coiChartInstance.options.plugins.tooltip.titleColor = themeCfg.tooltipText;
        coiChartInstance.options.plugins.tooltip.bodyColor = themeCfg.tooltipText;
        coiChartInstance.options.plugins.tooltip.borderColor = themeCfg.tooltipBorder;
        coiChartInstance.options.scales.y.grid.color = themeCfg.grid;
        coiChartInstance.options.scales.y.ticks.color = themeCfg.text;
        coiChartInstance.options.scales.x.grid.color = themeCfg.vgrid || themeCfg.grid;
        coiChartInstance.options.scales.x.ticks.color = themeCfg.text;
        coiChartInstance.update('none');
        return;
    }
    coiChartInstance = new Chart(ctx, chartConfig);
}

/**
 * Update last update time
 */
function updateLastUpdateTime(serverTimestamp = null) {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-IN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true
    });
    document.getElementById('lastUpdateTime').textContent = timeStr;

    // Display server timestamp if provided
    if (serverTimestamp) {
        const serverTime = new Date(serverTimestamp);
        const serverTimeStr = serverTime.toLocaleTimeString('en-IN', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: true
        });
        document.getElementById('serverTimestamp').textContent = `(Server: ${serverTimeStr})`;
    }
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
 * Format number for grid display - only use K (thousands)
 */
function formatNumberForGrid(num) {
    if (!num && num !== 0) return '--';

    const absNum = Math.abs(num);

    // Format with Thousand (K) only
    if (absNum >= 1000) {
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
    // Update shared state so tooltip callbacks always read the latest data.
    _combinedChartState.ceOI = ceOI;
    _combinedChartState.peOI = peOI;
    _combinedChartState.ceCOI = ceCOI;
    _combinedChartState.peCOI = peCOI;

    try {
        // Get fresh canvas context each time
        const canvasElement = document.getElementById('combinedChart');
        if (!canvasElement) {
            console.error('Combined chart canvas element not found');
            return;
        }

        const ctx = canvasElement.getContext('2d');
        if (!ctx) {
            console.error('Failed to get 2D context from combined chart canvas');
            return;
        }

        // Recalculate totals for legend labels
        const totalPeCOI = peCOI.reduce((sum, val) => sum + val, 0);
        const totalCeCOI = ceCOI.reduce((sum, val) => sum + val, 0);
        const peCOILabel = totalPeCOI >= 0 ? `Put OI Change: +${formatNumber(totalPeCOI)}` : `Put OI Change: ${formatNumber(totalPeCOI)}`;
        const ceCOILabel = totalCeCOI >= 0 ? `Call OI Change: +${formatNumber(totalCeCOI)}` : `Call OI Change: ${formatNumber(totalCeCOI)}`;

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
        const peCoiColors = peCOI.map(val => {
            if (val >= 0) {
                return 'rgba(144, 238, 144, 0.8)'; // Light green for positive change
            } else {
                return 'rgba(255, 255, 255, 0.8)'; // White for negative change
            }
        });
        const peCoiBorders = peCOI.map(val =>
            val >= 0 ? 'rgba(34, 139, 34, 1)' : 'rgba(34, 139, 34, 1)'
        );

        const ceCoiColors = ceCOI.map(val =>
            val >= 0 ? 'rgba(255, 127, 127, 0.8)' : 'rgba(255, 127, 127, 0.4)'
        );
        const ceCoiBorders = ceCOI.map(val =>
            val >= 0 ? 'rgba(220, 20, 60, 1)' : 'rgba(220, 20, 60, 1)'
        );

        const themeCfg = getActiveChartTheme();

        const chartConfig = {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Put OI',
                        data: peOI.map((val, idx) => {
                            const change = peCOI[idx];
                            // If change is positive, we stack it on top of Opening to reach Current
                            // Logic: Base = Current - Change (Opening)
                            if (change > 0) return val - change;
                            // If change is negative, it plots downwards. Base should be Current.
                            return val;
                        }),
                        backgroundColor: peColors,
                        borderColor: peBorders,
                        borderWidth: 2,
                        yAxisID: 'y',
                        stack: 'pe'
                    },
                    {
                        label: peCOILabel,
                        data: peCOI,
                        backgroundColor: peCoiColors,
                        borderColor: peCoiBorders,
                        borderWidth: 2,
                        yAxisID: 'y',
                        stack: 'pe'
                    },
                    {
                        label: 'Call OI',
                        data: ceOI.map((val, idx) => {
                            const change = ceCOI[idx];
                            // If change is positive, we stack it on top of Opening to reach Current
                            if (change > 0) return val - change;
                            // If change is negative, it plots downwards. Base should be Current.
                            return val;
                        }),
                        backgroundColor: ceColors,
                        borderColor: ceBorders,
                        borderWidth: 2,
                        yAxisID: 'y',
                        stack: 'ce'
                    },
                    {
                        label: ceCOILabel,
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
                            color: themeCfg.text,
                            font: {
                                size: 12,
                                weight: 'bold'
                            }
                        }
                    },
                    tooltip: {
                        backgroundColor: themeCfg.tooltipBg,
                        padding: 15,
                        titleFont: {
                            size: 12,
                            weight: 'bold'
                        },
                        bodyFont: {
                            size: 10,
                            family: 'Arial, sans-serif'
                        },
                        titleColor: themeCfg.tooltipText,
                        bodyColor: themeCfg.tooltipText,
                        borderColor: themeCfg.tooltipBorder,
                        borderWidth: 1,
                        displayColors: true,
                        boxPadding: 8,
                        callbacks: {
                            title: function (context) {
                                return `Strike ${context[0].label}`;
                            },
                            label: function (context) {
                                const idx = context.dataIndex;
                                const { ceOI: ceOIData, peOI: peOIData, ceCOI: ceCOIData, peCOI: peCOIData } = _combinedChartState;

                                // Calculate OI at 9:15 AM (opening OI)
                                const peOIOpening = peOIData[idx] - peCOIData[idx];
                                const ceOIOpening = ceOIData[idx] - ceCOIData[idx];

                                // Format based on dataset
                                if (context.datasetIndex === 0) {
                                    // Put OI
                                    return `Put OI at 9:15 AM   ${formatNumber(peOIOpening)}`;
                                } else if (context.datasetIndex === 1) {
                                    // Put OI Change
                                    const changeIndicator = peCOIData[idx] >= 0 ? '+' : '';
                                    return `Put OI chg          ${changeIndicator}${formatNumber(peCOIData[idx])}`;
                                } else if (context.datasetIndex === 2) {
                                    // Call OI
                                    return `Call OI at 9:15 AM  ${formatNumber(ceOIOpening)}`;
                                } else if (context.datasetIndex === 3) {
                                    // Call OI Change
                                    const changeIndicator = ceCOIData[idx] >= 0 ? '+' : '';
                                    return `Call OI chg         ${changeIndicator}${formatNumber(ceCOIData[idx])}`;
                                }

                                return '';
                            },
                            afterLabel: function (context) {
                                const idx = context.dataIndex;
                                const { ceOI: ceOIData, peOI: peOIData } = _combinedChartState;

                                // Get current time in HH:MM AM/PM format
                                const now = new Date();
                                const hours = now.getHours();
                                const minutes = now.getMinutes();
                                const ampm = hours >= 12 ? 'PM' : 'AM';
                                const displayHours = hours % 12 || 12;
                                const displayMinutes = minutes.toString().padStart(2, '0');
                                const currentTime = `${displayHours}:${displayMinutes} ${ampm}`;

                                // Show current OI at current time
                                if (context.datasetIndex === 1) {
                                    // After Put OI Change, show Put OI at current time
                                    return `Put OI at ${currentTime}   ${formatNumber(peOIData[idx])}`;
                                } else if (context.datasetIndex === 3) {
                                    // After Call OI Change, show Call OI at current time
                                    return `Call OI at ${currentTime}  ${formatNumber(ceOIData[idx])}`;
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
                            display: false
                        },
                        grid: {
                            color: themeCfg.grid,
                            drawBorder: true,
                            borderColor: themeCfg.grid
                        },
                        ticks: {
                            color: themeCfg.text,
                            font: {
                                size: 10
                            },
                            callback: function (value) {
                                return formatNumber(value);
                            }
                        }
                    },
                    x: {
                        stacked: true,
                        title: {
                            display: false
                        },
                        grid: {
                            color: themeCfg.vgrid || themeCfg.grid,
                            drawBorder: true,
                            borderColor: themeCfg.grid
                        },
                        ticks: {
                            color: themeCfg.text,
                            font: {
                                size: 10
                            }
                        }
                    }
                }
            },
            plugins: [currentPriceLinePlugin]
        };

        if (combinedChartInstance) {
            // Fast update path — avoids DOM teardown on every 60s poll.
            combinedChartInstance.data.labels = labels;
            combinedChartInstance.data.datasets[0].data = peOI.map((val, idx) => { const c = peCOI[idx]; return c > 0 ? val - c : val; });
            combinedChartInstance.data.datasets[0].backgroundColor = peColors;
            combinedChartInstance.data.datasets[0].borderColor = peBorders;
            combinedChartInstance.data.datasets[1].label = peCOILabel;
            combinedChartInstance.data.datasets[1].data = peCOI;
            combinedChartInstance.data.datasets[1].backgroundColor = peCoiColors;
            combinedChartInstance.data.datasets[1].borderColor = peCoiBorders;
            combinedChartInstance.data.datasets[2].data = ceOI.map((val, idx) => { const c = ceCOI[idx]; return c > 0 ? val - c : val; });
            combinedChartInstance.data.datasets[2].backgroundColor = ceColors;
            combinedChartInstance.data.datasets[2].borderColor = ceBorders;
            combinedChartInstance.data.datasets[3].label = ceCOILabel;
            combinedChartInstance.data.datasets[3].data = ceCOI;
            combinedChartInstance.data.datasets[3].backgroundColor = ceCoiColors;
            combinedChartInstance.data.datasets[3].borderColor = ceCoiBorders;
            combinedChartInstance.options.plugins.currentPriceLine.price = currentPrice;
            combinedChartInstance.options.plugins.legend.labels.color = themeCfg.text;
            combinedChartInstance.options.plugins.tooltip.backgroundColor = themeCfg.tooltipBg;
            combinedChartInstance.options.plugins.tooltip.titleColor = themeCfg.tooltipText;
            combinedChartInstance.options.plugins.tooltip.bodyColor = themeCfg.tooltipText;
            combinedChartInstance.options.plugins.tooltip.borderColor = themeCfg.tooltipBorder;
            combinedChartInstance.options.scales.y.grid.color = themeCfg.grid;
            combinedChartInstance.options.scales.y.ticks.color = themeCfg.text;
            combinedChartInstance.options.scales.x.grid.color = themeCfg.vgrid || themeCfg.grid;
            combinedChartInstance.options.scales.x.ticks.color = themeCfg.text;
            combinedChartInstance.update('none');
            return;
        }

        combinedChartInstance = new Chart(ctx, chartConfig);

    } catch (error) {
        console.error('Error in updateCombinedChart:', error);
        combinedChartInstance = null;
    }
}

/**
 * Initialize Historical Chart
 */


/**
 * Fetch and render OI history
 */
/**
 * Fetch and render OI history (Chart + Grid)
 */


// Cleanup on page unload
window.addEventListener('beforeunload', function () {
    stopAutoRefresh();

    if (coiChartInstance) {
        coiChartInstance.destroy();
    }
    if (combinedChartInstance) {
        combinedChartInstance.destroy();
    }
});

/**
 * Dynamic theme setup and switcher for Open Interest Route
 */
function updateOpenInterestTheme(themeName) {
    const oiContainer = document.querySelector('.oi-container');
    if (!oiContainer) return;

    // 1. Remove all old theme classes
    oiContainer.classList.remove('light-theme', 'dark-theme', 'forest-theme', 'cream-theme', 'ocean-theme');
    // 2. Add new theme class
    oiContainer.classList.add(`${themeName}-theme`);

    // Also apply to document.body for styling the main template / navigation bar
    document.body.classList.remove('light-theme', 'dark-theme', 'forest-theme', 'cream-theme', 'ocean-theme');
    document.body.classList.add(`${themeName}-theme`);

    // 3. Save to localStorage
    localStorage.setItem('app-theme', themeName);
    localStorage.setItem('oip-theme', themeName);

    // 4. Force update of charts if they are already loaded
    if (cachedData) {
        updateCharts(cachedData);
    }
}

function getActiveChartTheme() {
    const themeName = localStorage.getItem('app-theme') || localStorage.getItem('oip-theme') || localStorage.getItem('mkt-theme') || 'dark';
    const themes = {
        'light': { text: '#374151', grid: 'rgba(0, 0, 0, 0.06)', vgrid: 'rgba(0, 0, 0, 0.15)', tooltipBg: 'rgba(255, 255, 255, 0.95)', tooltipBorder: 'rgba(0, 0, 0, 0.2)', tooltipText: '#000000' },
        'dark': { text: '#94a3b8', grid: 'rgba(255, 255, 255, 0.08)', vgrid: 'rgba(255, 255, 255, 0.08)', tooltipBg: 'rgba(17, 24, 39, 0.95)', tooltipBorder: 'rgba(255, 255, 255, 0.2)', tooltipText: '#ffffff' },
        'forest': { text: '#6ba88f', grid: 'rgba(16, 185, 129, 0.08)', vgrid: 'rgba(16, 185, 129, 0.08)', tooltipBg: 'rgba(10, 20, 16, 0.95)', tooltipBorder: 'rgba(16, 185, 129, 0.2)', tooltipText: '#e8f7f0' },
        'cream': { text: '#7c7267', grid: 'rgba(180, 83, 9, 0.05)', vgrid: 'rgba(0, 0, 0, 0.15)', tooltipBg: '#fdfbf7', tooltipBorder: '#e6dfd3', tooltipText: '#433c35' },
        'ocean': { text: '#475569', grid: 'rgba(2, 132, 199, 0.05)', vgrid: 'rgba(0, 0, 0, 0.15)', tooltipBg: '#f0f6fc', tooltipBorder: '#dbebfa', tooltipText: '#1e293b' }
    };
    return themes[themeName] || themes['dark'];
}
