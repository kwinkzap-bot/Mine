let ceChart = null;
let peChart = null;
let combinedChart = null;
let ceSeries = null;
let peSeries = null;
let combinedCeSeries = null;
let combinedPeSeries = null;
let ceData = null;
let peData = null;
let currentCeToken = null;
let currentPeToken = null;
let currentTimeframe = '5minute';
let autoUpdateInterval = null;
let countdownInterval = null;
let cePriceLines = [];
let pePriceLines = [];
let ceTimerPriceLine = null;
let peTimerPriceLine = null;
let isInitialLoad = true;
let hasUserInteractedWithChart = false;
let defaultVisibleBarCount = 0;

function updateActiveButton(activeTimeframe) {
    document.querySelectorAll('.timeframe-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    const activeButton = document.querySelector(`.timeframe-btn[data-timeframe="${activeTimeframe}"]`);
    if (activeButton) {
        activeButton.classList.add('active');
    }
}

async function updateUnderlyingPrice() {
    const symbol = document.getElementById('symbol').value;
    if (!symbol) return;

    try {
        const response = await fetch(`/api/underlying-price?symbol=${symbol}`);
        const data = await response.json();

        if (data.success) {
            document.getElementById('nifty-price').textContent = data.ltp.toFixed(2);
        }
    } catch (error) {
        console.error('Error fetching underlying price:', error);
    }
}

function handleSymbolChange() {
    const symbol = document.getElementById('symbol').value;
    document.getElementById('nifty-symbol').textContent = symbol;
    loadStrikes();
}

window.addEventListener('DOMContentLoaded', function() {
    updateActiveButton(currentTimeframe);

    document.getElementById('symbol').addEventListener('change', handleSymbolChange);
    document.getElementById('load-chart-btn').addEventListener('click', loadChartData);
    
    document.querySelectorAll('.price-source-radio').forEach(radio => {
        radio.addEventListener('change', selectStrikesByLogic);
    });

    document.querySelectorAll('.timeframe-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const timeframe = btn.getAttribute('data-timeframe');
            changeTimeframe(timeframe);
        });
    });

    setTimeout(function() {
        const symbol = document.getElementById('symbol').value;
        if (symbol) {
            handleSymbolChange();
        }
    }, 500);
});

function getBarSpacing(timeframe) {
    const spacing = {
        'minute': 3,
        '3minute': 5,
        '5minute': 6,
        '15minute': 8,
        '60minute': 10,
        'day': 12
    };
    return spacing[timeframe] || 6;
}

function getTimeframeSeconds(timeframe) {
    const seconds = {
        'minute': 60,
        '3minute': 180,
        '5minute': 300,
        '15minute': 900,
        '60minute': 3600,
        'day': 86400
    };
    return seconds[timeframe] || 300;
}

function startCountdown() {
    if (countdownInterval) clearInterval(countdownInterval);

    countdownInterval = setInterval(() => {
        if (!isMarketHours() || !ceSeries || !peSeries || !ceData || !peData || ceData.length === 0 || peData.length === 0) {
            if (ceTimerPriceLine) {
                ceSeries.removePriceLine(ceTimerPriceLine);
                ceTimerPriceLine = null;
            }
            if (peTimerPriceLine) {
                peSeries.removePriceLine(peTimerPriceLine);
                peTimerPriceLine = null;
            }
            return;
        }

        const now = new Date();
        const timeframeSeconds = getTimeframeSeconds(currentTimeframe);
        const currentSeconds = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
        const secondsUntilClose = timeframeSeconds - (currentSeconds % timeframeSeconds);
        const minutes = Math.floor(secondsUntilClose / 60);
        const seconds = secondsUntilClose % 60;
        const countdownText = `⏳ ${minutes}:${seconds.toString().padStart(2, '0')}`;

        // Update CE Timer Line
        const lastCePrice = ceData[ceData.length - 1].close;
        if (ceTimerPriceLine) {
            ceSeries.removePriceLine(ceTimerPriceLine);
        }
        ceTimerPriceLine = ceSeries.createPriceLine({
            price: lastCePrice,
            color: '#667eea',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: false,
            title: countdownText,
        });

        // Update PE Timer Line
        const lastPePrice = peData[peData.length - 1].close;
        if (peTimerPriceLine) {
            peSeries.removePriceLine(peTimerPriceLine);
        }
        peTimerPriceLine = peSeries.createPriceLine({
            price: lastPePrice,
            color: '#667eea',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: false,
            title: countdownText,
        });

    }, 1000);
}

async function loadStrikes() {
    const symbol = document.getElementById('symbol').value;
    if (!symbol) return;
    
    updateUnderlyingPrice();

    const ceSelect = document.getElementById('ceStrike');
    const peSelect = document.getElementById('peStrike');
    
    ceSelect.innerHTML = '<option value="">Loading...</option>';
    peSelect.innerHTML = '<option value="">Loading...</option>';

    try {
        const response = await fetch(`/api/options-strikes?symbol=${symbol}`);
        const data = await response.json();

        if (data.needs_login) {
            showNotification('Session expired. Redirecting to login...', 'warning');
            window.location.href = '/login';
            return;
        }

        if (data.success) {
            ceSelect.innerHTML = '<option value="">Select CE Strike</option>';
            peSelect.innerHTML = '<option value="">Select PE Strike</option>';

            data.strikes.forEach(strike => {
                const ceOption = document.createElement('option');
                ceOption.value = strike.ce_token;
                ceOption.textContent = `${strike.strike} CE`;
                ceSelect.appendChild(ceOption);

                const peOption = document.createElement('option');
                peOption.value = strike.pe_token;
                peOption.textContent = `${strike.strike} PE`;
                peSelect.appendChild(peOption);
            });
            
            await selectStrikesByLogic();

        } else {
            ceSelect.innerHTML = '<option value="">Error loading strikes</option>';
            peSelect.innerHTML = '<option value="">Error loading strikes</option>';
            showNotification('Error: ' + data.error, 'error');
        }
    } catch (error) {
        ceSelect.innerHTML = '<option value="">Error loading strikes</option>';
        peSelect.innerHTML = '<option value="">Error loading strikes</option>';
        showNotification('Error loading strikes: ' + error.message, 'error');
    }
}

async function selectStrikesByLogic() {
    const symbol = document.getElementById('symbol').value;
    const priceSource = document.querySelector('input[name="priceSource"]:checked').value;
    const ceSelect = document.getElementById('ceStrike');
    const peSelect = document.getElementById('peStrike');

    let apiUrl;
    if (priceSource === 'prev_day_close') {
        apiUrl = `/api/get_PreviousDay_close_price?symbol=${symbol}`;
    } else {
        apiUrl = `/api/underlying-price?symbol=${symbol}`;
    }

    try {
        const response = await fetch(apiUrl);
        const data = await response.json();

        if (!data.success) {
            showNotification(data.error || 'Could not fetch price.', 'error');
            return;
        }

        const price = (priceSource === 'prev_day_close') ? data.prev_close : data.ltp;
        document.getElementById('nifty-price').textContent = price.toFixed(2);
        
        const strikeResponse = await fetch(`/api/get-strike-prices?prev_close=${price}`);
        const strikeData = await strikeResponse.json();

        if (!strikeData.success) {
            showNotification(strikeData.error || 'Could not calculate strikes.', 'error');
            return;
        }

        const ceStrikeValue = strikeData.ce_strike;
        const peStrikeValue = strikeData.pe_strike;

        let ceFound = false;
        for (let option of ceSelect.options) {
            if (option.text.startsWith(ceStrikeValue + ' CE')) {
                option.selected = true;
                ceFound = true;
            }
        }
        
        let peFound = false;
        for (let option of peSelect.options) {
            if (option.text.startsWith(peStrikeValue + ' PE')) {
                option.selected = true;
                peFound = true;
            }
        }

        if (ceFound && peFound) {
            loadChartData();
        } else {
            showNotification('Could not automatically select default strikes based on the logic.', 'warning');
        }

    } catch (error) {
        showNotification('Error selecting strikes by logic: ' + error.message, 'error');
    }
}

async function loadChartData() {
    const symbol = document.getElementById('symbol').value;
    const ceStrike = document.getElementById('ceStrike').value;
    const peStrike = document.getElementById('peStrike').value;
    const timeframe = currentTimeframe;

    if (!symbol || !ceStrike || !peStrike) {
        showNotification('Please select all fields', 'warning');
        return;
    }

    const loading = document.getElementById('loading');
    const chartContainer = document.getElementById('chartContainer');
    loading.classList.remove('hidden');

    try {
        const response = await fetch('/api/options-chart-data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ce_token: ceStrike,
                pe_token: peStrike,
                timeframe: timeframe
            })
        });

        const data = await response.json();

        if (data.needs_login) {
            showNotification('Session expired. Redirecting to login...', 'warning');
            window.location.href = '/login';
            return;
        }

        if (data.success) {
            isInitialLoad = true;
            hasUserInteractedWithChart = false;
            ceData = data.ce_data;
            peData = data.pe_data;
            currentCeToken = ceStrike;
            currentPeToken = peStrike;
            chartContainer.classList.remove('hidden');
            renderCombinedChart();
            startAutoUpdate();
            startCountdown();
        } else {
            showNotification('Error: ' + data.error, 'error');
        }
    } catch (error) {
        showNotification('Error loading chart data: ' + error.message, 'error');
    } finally {
        loading.classList.add('hidden');
    }
}

function renderCombinedChart() {
    try {
        const ceSelect = document.getElementById('ceStrike');
        if (ceSelect.selectedIndex >= 0) {
            const ceStrikeText = ceSelect.options[ceSelect.selectedIndex].text;
            document.getElementById('ce-strike-display').innerText = `(${ceStrikeText})`;
            document.getElementById('combined-ce-strike-display').innerText = `CE: ${ceStrikeText}`;
        }

        const peSelect = document.getElementById('peStrike');
        if (peSelect.selectedIndex >= 0) {
            const peStrikeText = peSelect.options[peSelect.selectedIndex].text;
            document.getElementById('pe-strike-display').innerText = `(${peStrikeText})`;
            document.getElementById('combined-pe-strike-display').innerText = `PE: ${peStrikeText}`;
        }

        const ceContainer = document.getElementById('ceChart');
        const peContainer = document.getElementById('peChart');

        if (!ceChart) {
            ceChart = window.LightweightCharts.createChart(ceContainer, { width: ceContainer.clientWidth, height: 400, layout: { background: { color: '#ffffff' }, textColor: '#333' }, grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } }, timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 5 }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal } });
            peChart = window.LightweightCharts.createChart(peContainer, { width: peContainer.clientWidth, height: 400, layout: { background: { color: '#ffffff' }, textColor: '#333' }, grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } }, timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 5 }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal } });
            ceSeries = ceChart.addCandlestickSeries({ priceFormat: { type: 'price', precision: 2, minMove: 0.01 }, upColor: 'green', downColor: 'red', borderUpColor: 'green', borderDownColor: 'red', wickUpColor: 'green', wickDownColor: 'red' });
            peSeries = peChart.addCandlestickSeries({ priceFormat: { type: 'price', precision: 2, minMove: 0.01 }, upColor: 'green', downColor: 'red', borderUpColor: 'green', borderDownColor: 'red', wickUpColor: 'green', wickDownColor: 'red' });
            window.addEventListener('resize', () => { ceChart.applyOptions({ width: ceContainer.clientWidth }); peChart.applyOptions({ width: peContainer.clientWidth }); });

            // Subscribe to visible time range changes to detect user interaction
            ceChart.timeScale().subscribeVisibleTimeRangeChange(() => {
                hasUserInteractedWithChart = true;
            });
            peChart.timeScale().subscribeVisibleTimeRangeChange(() => {
                hasUserInteractedWithChart = true;
            });
        }
        
        ceChart.timeScale().applyOptions({ barSpacing: getBarSpacing(currentTimeframe) });
        peChart.timeScale().applyOptions({ barSpacing: getBarSpacing(currentTimeframe) });

        cePriceLines.forEach(line => ceSeries.removePriceLine(line));
        pePriceLines.forEach(line => peSeries.removePriceLine(line));
        cePriceLines = [];
        pePriceLines = [];

        const filterData = (data) => {
            if (currentTimeframe === 'day') return data;
            const filtered = data.filter(d => { const date = new Date(d.date); const hours = date.getHours(); const minutes = date.getMinutes(); const time = hours * 60 + minutes; return time >= 555 && time <= 930; });
            return filtered.length > 0 ? filtered : data;
        };

        const filteredCeData = filterData(ceData);
        const filteredPeData = filterData(peData);

        if (!filteredCeData.length || !filteredPeData.length) {
            showNotification('No data available for trading hours', 'info');
            return;
        }

        const formatData = (data) => data.filter(d => { return d && d.open != null && d.high != null && d.low != null && d.close != null && !isNaN(d.open) && !isNaN(d.high) && !isNaN(d.low) && !isNaN(d.close) && d.open > 0 && d.high > 0 && d.low > 0 && d.close > 0; }).map(d => { const date = new Date(d.date); const istOffset = 5.5 * 60 * 60 * 1000; const istTime = new Date(date.getTime() + istOffset); return { time: Math.floor(istTime.getTime() / 1000), open: d.open, high: d.high, low: d.low, close: d.close }; });
        
        const formattedCeData = formatData(filteredCeData);
        const formattedPeData = formatData(filteredPeData);
        
        if (formattedCeData.length === 0 || formattedPeData.length === 0) {
            showNotification('No valid chart data. Try a different timeframe or strikes.', 'warning');
            return;
        }
        
        ceSeries.setData(formattedCeData);
        peSeries.setData(formattedPeData);

        // Store current visible logical ranges BEFORE setting new data
        let ceVisibleLogicalRange = null;
        let peVisibleLogicalRange = null;
        let combinedVisibleLogicalRange = null;

        if (!isInitialLoad && hasUserInteractedWithChart) {
            ceVisibleLogicalRange = ceChart.timeScale().getVisibleLogicalRange();
            peVisibleLogicalRange = peChart.timeScale().getVisibleLogicalRange();
            if (combinedChart) { // combinedChart might not be initialized yet
                combinedVisibleLogicalRange = combinedChart.timeScale().getVisibleLogicalRange();
            }
        }

        let cePrevHigh, cePrevLow, pePrevHigh, pePrevLow;
    
        try {
            const ceDates = filteredCeData.map(d => new Date(d.date).toDateString());
            const uniqueDates = [...new Set(ceDates)];
            if (uniqueDates.length > 1) {
                const prevDate = uniqueDates[uniqueDates.length - 2];
                const prevDayData = filteredCeData.filter(d => new Date(d.date).toDateString() === prevDate);
                cePrevHigh = Math.max(...prevDayData.map(d => d.high));
                cePrevLow = Math.min(...prevDayData.map(d => d.low));
            }
        } catch (e) { console.error('Error calculating CE prev day:', e); }
        
        try {
            const peDates = filteredPeData.map(d => new Date(d.date).toDateString());
            const uniqueDates = [...new Set(peDates)];
            if (uniqueDates.length > 1) {
                const prevDate = uniqueDates[uniqueDates.length - 2];
                const prevDayData = filteredPeData.filter(d => new Date(d.date).toDateString() === prevDate);
                pePrevHigh = Math.max(...prevDayData.map(d => d.high));
                pePrevLow = Math.min(...prevDayData.map(d => d.low));
            }
        } catch (e) { console.error('Error calculating PE prev day:', e); }
        
        // Draw lines on CE Chart
        if (cePrevHigh) {
            cePriceLines.push(ceSeries.createPriceLine({ price: cePrevHigh, color: 'black', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'CE PDH' }));
        }
        if (cePrevLow) {
            cePriceLines.push(ceSeries.createPriceLine({ price: cePrevLow, color: 'black', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'CE PDL' }));
        }
        if (pePrevHigh) {
            cePriceLines.push(ceSeries.createPriceLine({ price: pePrevHigh, color: 'green', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'PE PDH' }));
        }
        if (pePrevLow) {
            cePriceLines.push(ceSeries.createPriceLine({ price: pePrevLow, color: 'red', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'PE PDL' }));
        }

        // Draw lines on PE Chart
        if (pePrevHigh) {
            pePriceLines.push(peSeries.createPriceLine({ price: pePrevHigh, color: 'black', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'PE PDH' }));
        }
        if (pePrevLow) {
            pePriceLines.push(peSeries.createPriceLine({ price: pePrevLow, color: 'black', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'PE PDL' }));
        }
        if (cePrevHigh) {
            pePriceLines.push(peSeries.createPriceLine({ price: cePrevHigh, color: 'green', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'CE PDH' }));
        }
        if (cePrevLow) {
            pePriceLines.push(peSeries.createPriceLine({ price: cePrevLow, color: 'red', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'CE PDL' }));
        }

        const combinedContainer = document.getElementById('combinedChart');
        if (!combinedChart) {
            combinedChart = window.LightweightCharts.createChart(combinedContainer, {
                width: combinedContainer.clientWidth,
                height: 400,
                layout: { background: { color: '#ffffff' }, textColor: '#333' },
                grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
                timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 5 },
                rightPriceScale: {
                    scaleMargins: {
                        top: 0.2,
                        bottom: 0.2,
                    },
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                },
            });
            combinedCeSeries = combinedChart.addCandlestickSeries({ title: 'CE', upColor: 'green', downColor: 'red', borderUpColor: 'green', borderDownColor: 'red', wickUpColor: 'green', wickDownColor: 'red' });
            combinedPeSeries = combinedChart.addCandlestickSeries({ title: 'PE', upColor: 'blue', downColor: 'white', borderUpColor: 'blue', borderDownColor: 'blue', wickUpColor: 'blue', wickDownColor: 'blue' });
             window.addEventListener('resize', () => {
                combinedChart.applyOptions({ width: combinedContainer.clientWidth });
            });
            // Subscribe to visible time range changes for combined chart
            combinedChart.timeScale().subscribeVisibleTimeRangeChange(() => {
                hasUserInteractedWithChart = true;
            });
        }

        combinedChart.timeScale().applyOptions({ barSpacing: getBarSpacing(currentTimeframe) });

        const formattedCombinedCeData = formatData(filteredCeData);
        const formattedCombinedPeData = formatData(filteredPeData);

        if (formattedCombinedCeData.length > 0) {
            combinedCeSeries.setData(formattedCombinedCeData);
        }
        if (formattedCombinedPeData.length > 0) {
            combinedPeSeries.setData(formattedCombinedPeData);
        }
        
        // Apply scaling logic AFTER setting data
        if (isInitialLoad) {
            ceChart.timeScale().fitContent();
            peChart.timeScale().fitContent();
            combinedChart.timeScale().fitContent();
            
            const logicalRange = combinedChart.timeScale().getVisibleLogicalRange();
            if (logicalRange) {
                defaultVisibleBarCount = logicalRange.to - logicalRange.from;
            }

            isInitialLoad = false;
            hasUserInteractedWithChart = false; // Reset interaction flag on initial full load
        } else if (hasUserInteractedWithChart) {
            // Restore visible range if user interacted
            if (ceVisibleLogicalRange) ceChart.timeScale().setVisibleLogicalRange(ceVisibleLogicalRange.from, ceVisibleLogicalRange.to);
            if (peVisibleLogicalRange) peChart.timeScale().setVisibleLogicalRange(peVisibleLogicalRange.from, peVisibleLogicalRange.to);
            if (combinedVisibleLogicalRange) combinedChart.timeScale().setVisibleLogicalRange(combinedVisibleLogicalRange.from, combinedVisibleLogicalRange.to);
        } else {
            // If no user interaction, auto-follow the latest data
            if (defaultVisibleBarCount > 0 && formattedCombinedCeData.length > 0) {
                const latestBarIndex = formattedCombinedCeData.length - 1;
                const fromBarIndex = Math.max(0, latestBarIndex - defaultVisibleBarCount);

                ceChart.timeScale().setVisibleLogicalRange(fromBarIndex, latestBarIndex);
                peChart.timeScale().setVisibleLogicalRange(fromBarIndex, latestBarIndex);
                combinedChart.timeScale().setVisibleLogicalRange(fromBarIndex, latestBarIndex);
            } else {
                // Fallback if defaultVisibleBarCount is not yet set or data is empty
                ceChart.timeScale().fitContent();
                peChart.timeScale().fitContent();
                combinedChart.timeScale().fitContent();
            }
        }

    } catch (error) {
        console.error('Error rendering chart:', error);
        showNotification('Error rendering chart. Please try again.', 'error');
    }
}

async function changeTimeframe(newTimeframe) {
    if (!currentCeToken || !currentPeToken) return;

    updateActiveButton(newTimeframe);

    const loading = document.getElementById('loading');
    loading.classList.remove('hidden');

    try {
        const response = await fetch('/api/options-chart-data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ce_token: currentCeToken, pe_token: currentPeToken, timeframe: newTimeframe })
        });

        const data = await response.json();

        if (data.needs_login) {
            showNotification('Session expired. Redirecting to login...', 'warning');
            window.location.href = '/login';
            return;
        }

        if (data.success) {
            isInitialLoad = true;
            hasUserInteractedWithChart = false;
            currentTimeframe = newTimeframe;
            ceData = data.ce_data;
            peData = data.pe_data;
            renderCombinedChart();
            startCountdown();
        } else {
            showNotification('Error: ' + data.error, 'error');
        }
    } catch (error) {
        showNotification('Error changing timeframe: ' + error.message, 'error');
    } finally {
        loading.classList.add('hidden');
    }
}

function isMarketHours() {
    const now = new Date();
    const hours = now.getHours();
    const minutes = now.getMinutes();
    const time = hours * 60 + minutes;
    const day = now.getDay();
    return day >= 1 && day <= 5 && time <= 930;
}

function startAutoUpdate() {
    if (autoUpdateInterval) clearInterval(autoUpdateInterval);
    
    autoUpdateInterval = setInterval(async () => {
        if (!currentCeToken || !currentPeToken || !isMarketHours()) return;
        
        updateUnderlyingPrice();

        try {
            const response = await fetch('/api/options-chart-data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ce_token: currentCeToken,
                    pe_token: currentPeToken,
                    timeframe: currentTimeframe
                })
            });

            const data = await response.json();

            if (data.needs_login) {
                clearInterval(autoUpdateInterval);
                showNotification('Session expired. Redirecting to login...', 'warning');
                window.location.href = '/login';
                return;
            }

            if (data.success) {
                ceData = data.ce_data;
                peData = data.pe_data;
                renderCombinedChart();
            }
        } catch (error) {
            console.error('Auto-update error:', error);
        }
    }, 2000);
}

