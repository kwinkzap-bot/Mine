/**
 * backtest.js
 * Handles the UI logic for the backtester.
 */

document.addEventListener('DOMContentLoaded', function() {
    const symbolSearch = document.getElementById('symbolSearch');
    const symbolList = document.getElementById('symbolList');
    const runBtn = document.getElementById('runBacktestBtn');
    const resultsArea = document.getElementById('resultsArea');
    const loading = document.getElementById('loading');
    
    let allSymbols = [];
    let selectedSymbol = 'NIFTY';

    // Tracks the in-flight RTP optimise so a new/changed run cancels the old one.
    // Declared up top so cancelRtpOptimise() is safe to call during init.
    let _rtpOptRun   = 0;     // generation token — only the latest run is honoured
    let _rtpOptAbort = null;  // AbortController for the in-flight POST request

    // Initialize dates (default to Jan 1st 2017)
    const today = new Date();

    document.getElementById('endDate').value = today.toISOString().split('T')[0];
    document.getElementById('startDate').value = '2017-01-01';

    // Ensure timeframe default is 60m (already set in HTML but double check)
    const intervalSelect = document.getElementById('interval');
    if (intervalSelect) intervalSelect.value = '60minute';

    // 1. Fetch Symbols
    async function fetchSymbols() {
        // Fallback common symbols in case API is slow
        allSymbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'SBIN', 'RELIANCE', 'HDFCBANK'];
        
        try {
            const response = await fetch('/api/backtest/symbols');
            const data = await response.json();
            if (data.success) {
                // Combine indices and symbols, removing duplicates
                const fetched = [...data.indices, ...data.symbols];
                allSymbols = Array.from(new Set([...allSymbols, ...fetched]));
                console.log('Loaded symbols:', allSymbols.length);
            } else {
                if (window.showNotification) window.showNotification('Error loading symbols: ' + data.error, 'error');
            }
        } catch (error) {
            console.error('Failed to fetch symbols:', error);
        }
    }

    fetchSymbols();

    // 2. Searchable Dropdown Logic
    function renderDropdown(filterText = '') {
        const filter = (filterText || '').toUpperCase();
        const matches = allSymbols.filter(s => {
            if (!filter) return true;
            return s.includes(filter);
        }).slice(0, 15);
        
        if (matches.length > 0) {
            symbolList.innerHTML = matches.map(s => `<li class="symbol-item">${s}</li>`).join('');
            symbolList.classList.add('show');
        } else {
            symbolList.classList.remove('show');
        }
    }

    symbolSearch.addEventListener('input', function(e) {
        renderDropdown(e.target.value);
    });

    symbolSearch.addEventListener('focus', function(e) {
        this.value = ''; // Clear on focus per user request
        renderDropdown(''); // Show full list
    });

    symbolSearch.addEventListener('blur', function(e) {
        // Delay hiding to allow click event on list item to fire
        setTimeout(() => {
            symbolList.classList.remove('show');
            if (!this.value.trim()) {
                this.value = selectedSymbol;
            }
        }, 200);
    });

    symbolSearch.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            symbolList.classList.remove('show');
            let val = e.target.value.trim().toUpperCase();
            if (val) {
                selectedSymbol = val;
                this.value = selectedSymbol;
                applyLotValueForSymbol(selectedSymbol);
                cancelRtpOptimise(); // stale: results would be for the old symbol
                this.blur();
            } else {
                this.blur();
            }
        }
    });

    symbolList.addEventListener('click', function(e) {
        if (e.target.classList.contains('symbol-item')) {
            selectedSymbol = e.target.textContent;
            symbolSearch.value = selectedSymbol;
            symbolList.classList.remove('show');
            applyLotValueForSymbol(selectedSymbol);
            cancelRtpOptimise(); // stale: results would be for the old symbol
        }
    });

    // Lot value (₹/pt) per symbol; defaults to NIFTY's value for anything else.
    const LOT_VALUE_BY_SYMBOL = { NIFTY: 65, BANKNIFTY: 30, SENSEX: 20 };
    function lotValueForSymbol(symbol) {
        return LOT_VALUE_BY_SYMBOL[(symbol || '').toUpperCase()] || 65;
    }
    function applyLotValueForSymbol(symbol) {
        const lotValue = lotValueForSymbol(symbol);
        ['rtpLotValue', 'vwapLotValue', 'scLotValue'].forEach(function(id) {
            const el = document.getElementById(id);
            if (el) el.value = lotValue;
        });
    }
    // Seed lot values for the symbol selected on initial page load.
    applyLotValueForSymbol(selectedSymbol);

    // Close dropdown when clicking outside
    document.addEventListener('click', function(e) {
        if (!symbolSearch.contains(e.target) && !symbolList.contains(e.target)) {
            symbolList.classList.remove('show');
        }
    });

    // 2.5 Strategy Selection Logic
    const strategySelect = document.getElementById('strategySelect');
    const rtpParamsRow   = document.getElementById('rtpParamsRow');
    const rtpLotRow      = document.getElementById('rtpLotRow');
    const vwapLotRow     = document.getElementById('vwapLotRow');

    function updateStrategyView() {
        if (!strategySelect) return;

        // Switching strategy abandons any in-flight RTP optimise run.
        if (typeof cancelRtpOptimise === 'function') cancelRtpOptimise();

        const intervalSelect = document.getElementById('interval');
        const startDateInput = document.getElementById('startDate');
        const mainInputsRow  = document.getElementById('mainInputsRow');
        const today = new Date();
        const val   = strategySelect.value;

        // Reset all rows
        if (rtpParamsRow)   rtpParamsRow.style.display   = 'none';
        if (rtpLotRow)      rtpLotRow.style.display      = 'none';
        const smParamsRow   = document.getElementById('swingMomentumParamsRow');
        const vwapParamsRow = document.getElementById('vwapParamsRow');
        const vwapOptPanel  = document.getElementById('vwapOptimisePanel');
        const scParamsRow   = document.getElementById('secondCandleParamsRow');
        const scLotRow      = document.getElementById('secondCandleLotRow');
        const scOptPanel    = document.getElementById('secondCandleOptimisePanel');
        if (smParamsRow)   smParamsRow.style.display   = 'none';
        if (vwapParamsRow) vwapParamsRow.style.display = 'none';
        if (vwapLotRow)    vwapLotRow.style.display    = 'none';
        if (vwapOptPanel)  vwapOptPanel.style.display  = 'none';
        if (scParamsRow)   scParamsRow.style.display   = 'none';
        if (scLotRow)      scLotRow.style.display      = 'none';
        if (scOptPanel)    scOptPanel.style.display    = 'none';
        // Restore symbol/interval visibility (they are hidden for swing_momentum)
        const symFg = document.getElementById('mainSymbolFg');
        const intFg = document.getElementById('mainIntervalFg');
        if (symFg) symFg.style.display = '';
        if (intFg) intFg.style.display = '';

        const optBtn      = document.getElementById('runOptimiseBtn');
        const smGoLiveBtn = document.getElementById('smGoLiveBtn');
        if (optBtn)      optBtn.style.display      = (val === 'rtp' || val === 'swing_momentum' || val === 'vwap' || val === 'second_candle') ? '' : 'none';
        if (smGoLiveBtn) smGoLiveBtn.style.display = (val === 'swing_momentum') ? '' : 'none';

        // Hide optimise result panels when switching strategies
        const rtpOptPanel = document.getElementById('rtpOptimisePanel');
        const smOptPanel  = document.getElementById('smOptimisePanel');
        if (rtpOptPanel) rtpOptPanel.style.display = 'none';
        if (smOptPanel)  smOptPanel.style.display  = 'none';

        if (mainInputsRow) {
            mainInputsRow.classList.remove('form-row-5','form-row-6','form-row-7');
            mainInputsRow.classList.add('form-row-4');
        }

        if (val === 'rtp') {
            if (rtpParamsRow) rtpParamsRow.style.display = 'grid';
            if (rtpLotRow)    rtpLotRow.style.display    = 'grid';
            if (intervalSelect) intervalSelect.value = 'minute';
            if (startDateInput) startDateInput.value = '2017-01-01';

        } else if (val === 'vwap') {
            if (vwapParamsRow) vwapParamsRow.style.display = 'grid';
            if (vwapLotRow)    vwapLotRow.style.display    = 'grid';
            if (intervalSelect) intervalSelect.value = '5minute';
            if (startDateInput) startDateInput.value = '2017-01-01';
            updateVwapInvestment();

        } else if (val === 'second_candle') {
            if (scParamsRow) scParamsRow.style.display = 'grid';
            if (scLotRow)    scLotRow.style.display    = 'grid';
            // Base timeframe is selectable: 30second (~1 month of Fyers history)
            // or 1-minute and above (~10 years).
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '30second';
            if (startDateInput) startDateInput.value = '2017-01-01';
            updateScInvestment();

        } else if (val === 'swing_momentum') {
            if (smParamsRow) smParamsRow.style.display = 'grid';
            if (symFg) symFg.style.display = 'none';
            if (intFg) intFg.style.display = 'none';
            if (startDateInput) startDateInput.value = '2017-01-01';
        }
    }

    if (strategySelect) {
        strategySelect.addEventListener('change', updateStrategyView);
        // Set initial state
        updateStrategyView();
    }

    // Investment display: ₹50,000 per lot
    window.updateRtpInvestment = function() {
        const lots = Math.max(1, parseInt(document.getElementById('rtpLots')?.value || 1));
        const total = lots * 50000;
        const el = document.getElementById('rtpInvestmentDisplay');
        if (el) el.textContent = '₹' + total.toLocaleString('en-IN');
    };

    // VWAP investment display
    window.updateVwapInvestment = function() {
        const lots     = Math.max(1, parseInt(document.getElementById('vwapLots')?.value     || 1));
        const lotValue = Math.max(1, parseFloat(document.getElementById('vwapLotValue')?.value || 65));
        const total    = lots * 50000;
        const el = document.getElementById('vwapInvestmentDisplay');
        if (el) el.textContent = '₹' + total.toLocaleString('en-IN');
    };

    // 2nd 30-Sec Candle investment display
    window.updateScInvestment = function() {
        const lots  = Math.max(1, parseInt(document.getElementById('scLots')?.value || 1));
        const total = lots * 50000;
        const el = document.getElementById('scInvestmentDisplay');
        if (el) el.textContent = '₹' + total.toLocaleString('en-IN');
    };

    // 3. Run Backtest
    runBtn.addEventListener('click', async function() {
        const _strat = strategySelect ? strategySelect.value : 'rtp';
        const symbol = symbolSearch.value.toUpperCase();
        if (_strat !== 'swing_momentum' && !symbol) {
            window.showNotification('Please select a symbol', 'warning');
            return;
        }

        const payload = {
            symbol: symbol,
            start_date: document.getElementById('startDate').value,
            end_date: document.getElementById('endDate').value,
            interval: document.getElementById('interval').value,
        };

        loading.style.display = 'block';
        resultsArea.style.display = 'none';
        const btTradesSec   = document.getElementById('btTradesSection');
        const btPlaceholder = document.getElementById('btRightPlaceholder');
        const periodSec     = document.getElementById('periodBreakdownSection');
        const optPanel      = document.getElementById('rtpOptimisePanel');
        const smOptPanel    = document.getElementById('smOptimisePanel');
        const vwapOptPanel2 = document.getElementById('vwapOptimisePanel');
        const scOptPanel2   = document.getElementById('secondCandleOptimisePanel');
        if (btTradesSec)    btTradesSec.style.display    = 'none';
        if (btPlaceholder)  btPlaceholder.style.display  = '';
        if (periodSec)      periodSec.style.display      = 'none';
        if (optPanel)       optPanel.style.display       = 'none';
        if (smOptPanel)     smOptPanel.style.display     = 'none';
        if (vwapOptPanel2)  vwapOptPanel2.style.display  = 'none';
        if (scOptPanel2)    scOptPanel2.style.display    = 'none';

        try {
            const strat = strategySelect ? strategySelect.value : 'rtp';
            let endpoint = '/api/backtest/rtp';

            // VWAP strategy
            if (strat === 'vwap') {
                endpoint = '/api/backtest/vwap';
                payload.min_gap   = parseFloat(document.getElementById('vwapMinGap')?.value  || 30);
                payload.tp_points = parseFloat(document.getElementById('vwapTP')?.value      || 150);
                payload.sl_points = parseFloat(document.getElementById('vwapSL')?.value      || 50);
            }

            // 2nd 30-Sec Candle breakout
            if (strat === 'second_candle') {
                endpoint = '/api/backtest/second-candle';
                payload.interval     = document.getElementById('interval')?.value || '30second';
                payload.candle_index = parseInt(document.getElementById('scCandleIndex')?.value || 2);
                payload.rr_ratio     = parseFloat(document.getElementById('scRrRatio')?.value || 3);
                const exitTime = (document.getElementById('scExitTime')?.value || '15:25').split(':');
                payload.exit_hour    = parseInt(exitTime[0] || 15);
                payload.exit_minute  = parseInt(exitTime[1] || 25);
                const dir = document.getElementById('scDirection')?.value || 'both';
                payload.enable_long  = dir !== 'short';
                payload.enable_short = dir !== 'long';
            }

            // Swing Momentum: different endpoint + payload
            if (strat === 'swing_momentum') {
                endpoint = '/api/backtest/swing-momentum';
                payload.index          = document.getElementById('smIndex')?.value || 'NIFTY 500';
                payload.rebalance_freq = document.getElementById('smRebalFreq')?.value || 'monthly';
                payload.investment     = parseFloat(document.getElementById('smInvestment')?.value || '100000');
                payload.top_n          = parseInt(document.getElementById('smTopN')?.value || '10');
                payload.exit_rank      = parseInt(document.getElementById('smExitRank')?.value || '50');
                payload.monthly_add    = parseFloat(document.getElementById('smMonthlyAdd')?.value || '0');
            }

            // RTP-specific payload fields
            if (strat === 'rtp') {
                payload.entry_mode = document.getElementById('rtpEntryMode')?.value || 'RTP(20 & 9)';
                payload.use_adx    = document.getElementById('rtpUseAdx')?.checked ?? false;
                payload.adx_thresh = parseFloat(document.getElementById('rtpAdxThresh')?.value || 20);
                const slVal    = document.getElementById('rtpSL')?.value;
                const tgtVal   = document.getElementById('rtpTarget')?.value;
                const trailVal = document.getElementById('rtpTrailSL')?.value;
                if (slVal)    payload.sl_points    = parseFloat(slVal);
                if (tgtVal)   payload.tgt_points   = parseFloat(tgtVal);
                if (trailVal) payload.trail_points = parseFloat(trailVal);
            }

            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (data.success) {
                if (data.warning) window.showNotification(data.warning, 'warning');
                displayResults(data);
            } else {
                window.showNotification(data.error || 'Backtest failed', 'error');
            }
        } catch (error) {
            console.error('Backtest error:', error);
            window.showNotification('An error occurred during backtest', 'error');
        } finally {
            loading.style.display = 'none';
        }
    });

    // Brokerage per round-trip trade (entry + exit) for NIFTY, by lot count
    function calcBrokeragePerTrade(lots) {
        const lookup = { 1: 103, 2: 158, 3: 213, 4: 268, 5: 330 };
        if (lots <= 5) return lookup[Math.max(1, Math.floor(lots))] || 103;
        return 330 + (Math.floor(lots) - 5) * 62;
    }

    let lastData = null;
    let sortConfig = { key: 'exit_time', direction: 'desc' };

    function displayResults(data) {
        lastData = data;
        const { summary } = data;
        const isRtp  = strategySelect && strategySelect.value === 'rtp';
        const isSM   = strategySelect && strategySelect.value === 'swing_momentum';
        const isVwap = strategySelect && strategySelect.value === 'vwap';
        const isSc   = strategySelect && strategySelect.value === 'second_candle';
        // 2nd-candle reuses the VWAP-style ₹ cards, reading its own lot inputs
        const moneyLotsId    = isSc ? 'scLots'     : 'vwapLots';
        const moneyLotValId  = isSc ? 'scLotValue' : 'vwapLotValue';

        if (isSM) {
            lastData.is_swing_momentum = true;
            _displaySwingMomentumResults(data);
            return;
        }

        // Restore any stat card labels that swing_momentum may have changed
        _restoreSmStatLabels();

        // ── Row 1: always-visible cards ────────────────────────────
        document.getElementById('statTotalTrades').textContent = summary.total_trades ?? 0;
        document.getElementById('statWins').textContent        = summary.wins   ?? 0;
        document.getElementById('statLosses').textContent      = summary.losses ?? 0;

        document.getElementById('statWinRate').textContent = summary.total_trades > 0
            ? ((summary.wins / summary.total_trades) * 100).toFixed(1) + '%'
            : '0%';

        const pnl    = summary.total_pnl ?? 0;
        const pnlEl  = document.getElementById('statTotalPnl');
        pnlEl.textContent = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
        pnlEl.className   = 'stat-card__val ' + (pnl >= 0 ? 'stat-val-green' : 'stat-val-red');

        const outcomeEl = document.getElementById('statOutcome');
        outcomeEl.textContent = pnl >= 0 ? 'PROFIT' : 'LOSS';
        outcomeEl.className   = 'stat-card__val ' + (pnl >= 0 ? 'stat-val-green' : 'stat-val-red');

        // ── Row 2: RTP-only cards ──────────────────────────────────
        const rtpRow = document.getElementById('rtpStatsRow');
        if (isRtp && rtpRow) {
            rtpRow.style.display = '';

            // Profit factor
            document.getElementById('statProfitFactor').textContent = summary.profit_factor ?? '—';

            // Avg Win / Avg Loss
            const avgWinEl  = document.getElementById('statAvgWin');
            const avgLossEl = document.getElementById('statAvgLoss');
            if (avgWinEl)  avgWinEl.textContent  = summary.avg_win  != null ? '+' + summary.avg_win.toFixed(1)  + ' pts' : '—';
            if (avgLossEl) avgLossEl.textContent = summary.avg_loss != null ? summary.avg_loss.toFixed(1) + ' pts' : '—';

            // Position sizing inputs
            const lots     = Math.max(1, parseInt(document.getElementById('rtpLots')?.value    || 1));
            const lotValue = Math.max(1, parseFloat(document.getElementById('rtpLotValue')?.value || 75));

            // Max drawdown — pts near label, ₹ as main value
            const dd        = summary.max_drawdown ?? 0;
            const ddPtsEl   = document.getElementById('statMaxDDPts');
            const ddEl      = document.getElementById('statMaxDD');
            if (ddPtsEl) ddPtsEl.textContent = dd.toFixed(1) + ' pts';
            if (ddEl)    ddEl.textContent    = '₹' + Math.round(Math.abs(dd) * lotValue * lots).toLocaleString('en-IN');

            // Drawdown date range subtitle
            const ddDatesEl = document.getElementById('statMaxDDDates');
            if (ddDatesEl) {
                if (summary.max_dd_start && summary.max_dd_end) {
                    const fmt = s => s.replace('T', ' ').slice(0, 16);
                    ddDatesEl.textContent = fmt(summary.max_dd_start) + ' → ' + fmt(summary.max_dd_end);
                } else {
                    ddDatesEl.textContent = '';
                }
            }

            // Net P&L (₹) = gross ₹ − brokerage
            const brokPerTrade   = calcBrokeragePerTrade(lots);
            const totalBrokerage = brokPerTrade * (summary.total_trades || 0);
            const grossRs = pnl * lotValue * lots;
            const netRs   = grossRs - totalBrokerage;
            const netEl   = document.getElementById('statNetRs');
            if (netEl) {
                netEl.textContent = (netRs >= 0 ? '+' : '') + '₹' + Math.round(netRs).toLocaleString('en-IN');
                netEl.className   = 'stat-card__val ' + (netRs >= 0 ? 'stat-val-green' : 'stat-val-red');
            }
            const netSubEl = document.getElementById('statNetRsSub');
            if (netSubEl) netSubEl.textContent = 'brok: ₹' + totalBrokerage.toLocaleString('en-IN');

            // Subtitle with SL / Target / Trail
            if (summary.sl_points != null && summary.tgt_points != null) {
                const subtitle = document.getElementById('btSubtitle');
                if (subtitle) {
                    let info = `SL: ${summary.sl_points} pts  ·  Target: ${summary.tgt_points} pts`;
                    if (summary.trail_points) info += `  ·  Trail: ${summary.trail_points} pts`;
                    subtitle.textContent = info;
                }
            }
        } else if ((isVwap || isSc) && rtpRow) {
            rtpRow.style.display = '';

            document.getElementById('statProfitFactor').textContent =
                summary.profit_factor != null ? summary.profit_factor : '—';

            const avgWinEl  = document.getElementById('statAvgWin');
            const avgLossEl = document.getElementById('statAvgLoss');
            if (avgWinEl)  avgWinEl.textContent  = summary.avg_win  != null ? '+' + Number(summary.avg_win).toFixed(1)  + ' pts' : '—';
            if (avgLossEl) avgLossEl.textContent = summary.avg_loss != null ? '-' + Math.abs(Number(summary.avg_loss)).toFixed(1) + ' pts' : '—';

            const dd      = summary.max_drawdown ?? 0;
            const vLots   = Math.max(1, parseInt(document.getElementById(moneyLotsId)?.value     || 1));
            const vLotVal = Math.max(1, parseFloat(document.getElementById(moneyLotValId)?.value || 65));
            const ddPtsEl = document.getElementById('statMaxDDPts');
            const ddEl    = document.getElementById('statMaxDD');
            if (ddPtsEl) ddPtsEl.textContent = dd.toFixed(1) + ' pts';
            if (ddEl)    ddEl.textContent    = '₹' + Math.round(Math.abs(dd) * vLotVal * vLots).toLocaleString('en-IN');

            const ddDatesEl = document.getElementById('statMaxDDDates');
            if (ddDatesEl) ddDatesEl.textContent = '';

            const brokPerTrade   = calcBrokeragePerTrade(vLots);
            const totalBrokerage = brokPerTrade * (summary.total_trades || 0);
            const grossRs = pnl * vLotVal * vLots;
            const netRs   = grossRs - totalBrokerage;
            const netEl   = document.getElementById('statNetRs');
            if (netEl) {
                netEl.textContent = (netRs >= 0 ? '+' : '') + '₹' + Math.round(netRs).toLocaleString('en-IN');
                netEl.className   = 'stat-card__val ' + (netRs >= 0 ? 'stat-val-green' : 'stat-val-red');
            }
            const netSubEl = document.getElementById('statNetRsSub');
            if (netSubEl) netSubEl.textContent = 'brok: ₹' + totalBrokerage.toLocaleString('en-IN');
        } else {
            if (rtpRow) rtpRow.style.display = 'none';
        }

        // Equity curve + period breakdown
        const lots2     = (isVwap || isSc)
            ? Math.max(1, parseInt(document.getElementById(moneyLotsId)?.value      || 1))
            : Math.max(1, parseInt(document.getElementById('rtpLots')?.value       || 1));
        const lotValue2 = (isVwap || isSc)
            ? Math.max(1, parseFloat(document.getElementById(moneyLotValId)?.value  || 65))
            : Math.max(1, parseFloat(document.getElementById('rtpLotValue')?.value  || 75));
        const isMoney     = isRtp || isVwap || isSc;
        const investment2 = lots2 * 50000;
        renderEquityCurve(data.trades, isMoney, lots2, lotValue2, investment2);

        // Store for period tab re-renders
        _periodTrades   = data.trades;
        _periodIsRtp    = isMoney;
        _periodLots     = lots2;
        _periodLotValue = lotValue2;
        const activePeriod = document.querySelector('.period-tab.active')?.dataset.period || 'monthly';
        renderPeriodBreakdown(data.trades, isMoney, lots2, lotValue2, activePeriod);

        renderTable();
        resultsArea.style.display = 'block';
        const btTradesSec2   = document.getElementById('btTradesSection');
        const btPlaceholder2 = document.getElementById('btRightPlaceholder');
        if (btTradesSec2)   btTradesSec2.style.display   = '';
        if (btPlaceholder2) btPlaceholder2.style.display = 'none';
    }

    // ── Equity Curve ────────────────────────────────────────────────
    let _equityChart = null;

    function renderEquityCurve(trades, isRtp, lots, lotValue, investment) {
        const section = document.getElementById('equityCurveSection');
        if (!section || !trades || trades.length === 0) {
            if (section) section.style.display = 'none';
            return;
        }

        // Sort by entry time chronologically
        const sorted = [...trades].sort((a, b) => new Date(a.entry_time) - new Date(b.entry_time));

        const totalInvestment = investment != null ? investment : lots * 50000;

        // Start point = total investment
        const labels       = ['Start'];
        const chartData    = [totalInvestment];
        const tooltipDates = [''];
        const pointColors  = ['#2962ff'];

        let portfolio = totalInvestment;
        sorted.forEach((t, idx) => {
            const tradeRs = isRtp
                ? Math.round((t.pnl || 0) * lotValue * lots)
                : (t.pnl || 0);
            portfolio += tradeRs;
            labels.push('T' + (idx + 1));
            chartData.push(Math.round(portfolio));
            pointColors.push((t.pnl || 0) >= 0 ? '#00c853' : '#ff1744');
            tooltipDates.push(t.entry_time ? String(t.entry_time).replace('T', ' ').slice(0, 16) : '');
        });

        const finalValue = chartData[chartData.length - 1];
        const diff       = finalValue - totalInvestment;
        const isProfit   = diff >= 0;
        const lineColor  = isProfit ? '#2962ff' : '#ff1744';
        const fillColor  = isProfit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

        // Badge: show net change + return %
        const finalEl = document.getElementById('equityCurveFinalPnl');
        if (finalEl) {
            const pct = ((diff / totalInvestment) * 100).toFixed(1);
            finalEl.textContent =
                (diff >= 0 ? '+' : '') + '₹' + Math.abs(diff).toLocaleString('en-IN') +
                '  (' + (diff >= 0 ? '+' : '') + pct + '%)';
            finalEl.style.color = isProfit ? '#00c853' : '#ff1744';
        }

        // Y-axis: compact ₹ labels (K / L)
        const fmtY = v => {
            if (Math.abs(v) >= 100000) return '₹' + (v / 100000).toFixed(1) + 'L';
            if (Math.abs(v) >= 1000)   return '₹' + (v / 1000).toFixed(0)   + 'K';
            return '₹' + v;
        };

        // Destroy previous chart instance
        if (_equityChart) { _equityChart.destroy(); _equityChart = null; }

        const ctx = document.getElementById('equityCurveChart');
        if (!ctx) return;

        _equityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Portfolio Value',
                    data: chartData,
                    borderColor: lineColor,
                    backgroundColor: fillColor,
                    fill: true,
                    tension: 0.25,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointHoverBackgroundColor: pointColors,
                    pointHoverBorderColor: pointColors,
                    borderWidth: 2,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: (items) => {
                                const i = items[0].dataIndex;
                                if (i === 0) return 'Starting capital';
                                return `Trade ${i}  ·  ${tooltipDates[i]}`;
                            },
                            label: (item) => {
                                const v   = item.raw;
                                const chg = v - totalInvestment;
                                return [
                                    '  Value: ₹' + Math.round(v).toLocaleString('en-IN'),
                                    '  P&L:   ' + (chg >= 0 ? '+' : '') + '₹' + Math.round(chg).toLocaleString('en-IN'),
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: { maxTicksLimit: 15, color: '#999', font: { size: 11 }, autoSkip: true },
                        grid:  { color: 'rgba(0,0,0,0.04)' },
                    },
                    y: {
                        ticks: { color: '#999', font: { size: 11 }, callback: fmtY },
                        grid:  { color: 'rgba(0,0,0,0.05)' },
                    }
                }
            }
        });

        section.style.display = '';
    }

    // ── Period P&L Breakdown ─────────────────────────────────────────
    let _periodChart = null;
    let _periodTrades = [];
    let _periodIsRtp = false;
    let _periodLots = 1;
    let _periodLotValue = 75;

    function getWeekKey(date) {
        const d = new Date(date);
        d.setHours(0, 0, 0, 0);
        d.setDate(d.getDate() - d.getDay() + 1); // Monday
        return d.toISOString().slice(0, 10);
    }

    function groupByPeriod(trades, period) {
        const groups = {};
        trades.forEach(t => {
            const d = new Date(t.entry_time);
            let key;
            if      (period === 'daily')   key = d.toISOString().slice(0, 10);
            else if (period === 'weekly')  key = getWeekKey(d);
            else if (period === 'monthly') key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
            else                           key = `${d.getFullYear()}`;
            if (!groups[key]) groups[key] = { pnl: 0, wins: 0, losses: 0 };
            groups[key].pnl += (t.pnl || 0);
            if ((t.pnl || 0) > 0) groups[key].wins++;
            else                  groups[key].losses++;
        });
        return groups;
    }

    // Compact number formatter for bar labels
    function fmtCompact(v, isRtp) {
        const abs  = Math.abs(v);
        const sign = v >= 0 ? '+' : '−';
        if (isRtp) {
            if (abs >= 100000) return sign + '₹' + (abs / 100000).toFixed(1) + 'L';
            if (abs >= 1000)   return sign + '₹' + (abs / 1000).toFixed(1) + 'K';
            return sign + '₹' + abs;
        }
        return (v >= 0 ? '+' : '') + v.toFixed(1);
    }

    // Custom plugin: draw value labels on top/bottom of each bar
    const barValueLabelPlugin = {
        id: 'barValueLabels',
        afterDatasetsDraw(chart, _, opts) {
            const { ctx, data } = chart;
            const meta = chart.getDatasetMeta(0);
            ctx.save();
            ctx.font = '600 9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
            ctx.textAlign = 'center';
            meta.data.forEach((bar, i) => {
                const v = data.datasets[0].data[i];
                if (v == null) return;
                const text = opts.fmt(v);
                ctx.fillStyle = v >= 0 ? '#16a34a' : '#dc2626';
                if (v >= 0) {
                    ctx.textBaseline = 'bottom';
                    ctx.fillText(text, bar.x, bar.y - 3);
                } else {
                    ctx.textBaseline = 'top';
                    ctx.fillText(text, bar.x, bar.y + 3);
                }
            });
            ctx.restore();
        }
    };

    function renderPeriodBreakdown(trades, isRtp, lots, lotValue, period) {
        const section = document.getElementById('periodBreakdownSection');
        if (!section || !trades || trades.length === 0) {
            if (section) section.style.display = 'none';
            return;
        }

        const groups = groupByPeriod(trades, period);
        const keys   = Object.keys(groups).sort();

        const labels = keys.map(k => {
            if (period === 'monthly') {
                const [y, m] = k.split('-');
                return new Date(+y, +m - 1).toLocaleString('default', { month: 'short', year: '2-digit' });
            }
            if (period === 'weekly')  return 'W ' + k.slice(5);
            if (period === 'daily')   return k.slice(5);   // MM-DD
            return k;
        });

        const values = keys.map(k => {
            const raw = groups[k].pnl;
            return isRtp ? Math.round(raw * lotValue * lots) : Math.round(raw * 100) / 100;
        });

        const meta = keys.map(k => groups[k]);  // { pnl, wins, losses }

        const bgColors  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)'  : 'rgba(239,68,68,.20)');
        const brdColors = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)'  : 'rgba(239,68,68,.90)');

        const canvas = document.getElementById('periodBreakdownChart');
        if (!canvas) return;
        if (_periodChart) { _periodChart.destroy(); _periodChart = null; }

        const fmt = v => fmtCompact(v, isRtp);

        _periodChart = new Chart(canvas.getContext('2d'), {
            type: 'bar',
            plugins: [barValueLabelPlugin],
            data: {
                labels,
                datasets: [{
                    data: values,
                    backgroundColor: bgColors,
                    borderColor:     brdColors,
                    borderWidth:  1.5,
                    borderRadius: 4,
                    borderSkipped: false,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: { top: 18, bottom: 4 } },
                plugins: {
                    legend: { display: false },
                    barValueLabels: { fmt },
                    tooltip: {
                        callbacks: {
                            title: ctx => labels[ctx[0].dataIndex],
                            label: ctx => {
                                const i  = ctx.dataIndex;
                                const v  = values[i];
                                const g  = meta[i];
                                const tr = g.wins + g.losses;
                                const wr = tr > 0 ? ((g.wins / tr) * 100).toFixed(0) : 0;
                                return [
                                    ' P&L: ' + (v >= 0 ? '+' : '') + (isRtp
                                        ? '₹' + Math.abs(v).toLocaleString('en-IN')
                                        : v + ' pts'),
                                    ` Trades: ${tr}  (${g.wins}W / ${g.losses}L)`,
                                    ` Win Rate: ${wr}%`,
                                ];
                            }
                        },
                        padding: 10,
                        displayColors: false,
                    }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { font: { size: 9, weight: '500' }, color: '#94a3b8' }
                    },
                    y: {
                        grid: {
                            color: ctx => ctx.tick.value === 0
                                ? 'rgba(0,0,0,.25)'
                                : 'rgba(0,0,0,.04)',
                            lineWidth: ctx => ctx.tick.value === 0 ? 1.5 : 1,
                        },
                        ticks: {
                            font: { size: 9 }, color: '#94a3b8',
                            callback: v => {
                                if (v === 0) return '0';
                                const abs = Math.abs(v);
                                const s   = v < 0 ? '−' : '';
                                if (isRtp) {
                                    if (abs >= 100000) return s + '₹' + (abs/100000).toFixed(1) + 'L';
                                    if (abs >= 1000)   return s + '₹' + (abs/1000).toFixed(0) + 'K';
                                    return s + '₹' + abs;
                                }
                                return s + abs;
                            }
                        }
                    }
                }
            }
        });
        section.style.display = '';
    }

    // Period tab wiring
    document.querySelectorAll('.period-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.period-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderPeriodBreakdown(_periodTrades, _periodIsRtp, _periodLots, _periodLotValue, btn.dataset.period);
        });
    });

    function renderTable() {
        if (!lastData || !lastData.trades) return;
        if (lastData.is_swing_momentum) { _renderSmTable(lastData.trades); return; }
        // Restore the intraday header if swing momentum rewrote the thead
        // (its headers have no data-sort). Only rebuild when actually mutated
        // so normal runs keep their existing sort listeners.
        const thead = document.querySelector('.trades-table thead tr');
        if (thead && !thead.querySelector('th[data-sort]')) {
            thead.innerHTML = `
                <th data-sort="entry_time">Entry Time</th>
                <th data-sort="type">Type</th>
                <th data-sort="entry_price">Entry Price</th>
                <th data-sort="exit_time">Exit Time</th>
                <th data-sort="exit_price">Exit Price</th>
                <th data-sort="result">Result</th>
                <th data-sort="pnl">P&amp;L</th>`;
            attachSortListeners();
        }
        const trades = [...lastData.trades];
        const tbody = document.getElementById('tradesBody');
        
        // Sort trades based on config
        trades.sort((a, b) => {
            let valA = a[sortConfig.key];
            let valB = b[sortConfig.key];
            
            // Handle dates
            if (sortConfig.key.includes('time')) {
                valA = new Date(valA).getTime();
                valB = new Date(valB).getTime();
            }
            
            if (valA < valB) return sortConfig.direction === 'asc' ? -1 : 1;
            if (valA > valB) return sortConfig.direction === 'asc' ? 1 : -1;
            return 0;
        });

        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 20px;">No trades generated</td></tr>';
        } else {
            tbody.innerHTML = trades.map(t => {
                const dir    = t.direction || t.type || '-';
                const result = t.exit_reason || t.result || '-';
                const isLong = dir === 'BUY' || dir === 'Long' || dir === 'long';
                return `
                <tr>
                    <td>${formatDate(t.entry_time)}</td>
                    <td><span class="badge ${isLong ? 'badge-buy' : 'badge-sell'}">${dir}</span></td>
                    <td>${(t.entry_price||0).toFixed(2)}</td>
                    <td>${formatDate(t.exit_time)}</td>
                    <td>${(t.exit_price||0).toFixed(2)}</td>
                    <td>${result}</td>
                    <td class="${t.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(t.pnl||0).toFixed(2)}</td>
                </tr>`;
            }).join('');
        }

        // Update header indicators
        document.querySelectorAll('.trades-table th').forEach(th => {
            th.classList.remove('sort-asc', 'sort-desc');
            if (th.dataset.sort === sortConfig.key) {
                th.classList.add(sortConfig.direction === 'asc' ? 'sort-asc' : 'sort-desc');
            }
        });
    }

    // Add sort listeners to headers (re-callable after the thead is rebuilt)
    function attachSortListeners() {
        document.querySelectorAll('.trades-table th[data-sort]').forEach(th => {
            th.style.cursor = 'pointer';
            th.addEventListener('click', () => {
                const key = th.dataset.sort;
                if (sortConfig.key === key) {
                    sortConfig.direction = sortConfig.direction === 'asc' ? 'desc' : 'asc';
                } else {
                    sortConfig.key = key;
                    sortConfig.direction = 'desc'; // Default to newest/highest first
                }
                renderTable();
            });
        });
    }
    attachSortListeners();

    function formatDate(dateStr) {
        if (!dateStr) return '--';
        const d = new Date(dateStr);
        return d.toLocaleString();
    }

    // ── Optimise helpers ─────────────────────────────────────────────
    function renderOptResults(data) {
        const panel  = document.getElementById('rtpOptimisePanel');
        const tbody  = document.getElementById('optTableBody');
        const metaEl = document.getElementById('optMeta');
        const recalcBtn = document.getElementById('recalculateOptBtn');

        if (metaEl) {
            let meta = `${data.total_combos_tested} combos · ${data.symbol} · ${data.interval}`;
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        if (tbody) {
            tbody.innerHTML = (data.results || []).map((r, i) => {
                const adxStr = r.use_adx ? `≥${r.adx_thresh}` : 'Off';
                const pnlFmt = (r.net_pnl >= 0 ? '+' : '') + r.net_pnl.toFixed(1) + ' pts';
                const ddFmt  = r.max_drawdown != null ? r.max_drawdown.toFixed(1) : '—';
                const wr     = r.total_trades > 0
                    ? ((r.wins / r.total_trades) * 100).toFixed(0) : '0';
                return `
                <tr class="${i === 0 ? 'opt-best' : ''}">
                    <td>${i + 1}</td>
                    <td style="white-space:nowrap">${r.entry_mode}</td>
                    <td>${r.sl_points}</td>
                    <td>${r.tgt_points}</td>
                    <td>${adxStr}</td>
                    <td>${r.total_trades}</td>
                    <td>${wr}%</td>
                    <td class="${r.net_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${pnlFmt}</td>
                    <td>${(r.profit_factor || 0).toFixed(2)}</td>
                    <td class="pnl-negative">${ddFmt}</td>
                    <td><button class="btn-opt-use" data-idx="${i}">Use</button></td>
                </tr>`;
            }).join('');

            tbody.querySelectorAll('.btn-opt-use').forEach(btn => {
                btn.addEventListener('click', () => {
                    applyOptResult(data.results[parseInt(btn.dataset.idx)]);
                });
            });
        }

        if (panel) panel.style.display = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyOptResult(data.best);
    }

    // Abort any running RTP optimise (pending request + polling loop).
    function cancelRtpOptimise() {
        _rtpOptRun += 1;            // invalidate any active poll
        if (_rtpOptAbort) {
            try { _rtpOptAbort.abort(); } catch (e) { /* noop */ }
            _rtpOptAbort = null;
        }
    }

    async function runOptimise(recalculate) {
        const symbol = symbolSearch.value.trim().toUpperCase();
        if (!symbol) { window.showNotification('Please select a symbol', 'warning'); return; }

        const panel     = document.getElementById('rtpOptimisePanel');
        const recalcBtn = document.getElementById('recalculateOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');

        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';

        // Cancel whatever was running before and claim this generation.
        cancelRtpOptimise();
        const myRun     = _rtpOptRun;
        const controller = new AbortController();
        _rtpOptAbort     = controller;

        try {
            const resp = await fetch('/api/backtest/rtp/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: controller.signal,
                body: JSON.stringify({
                    symbol,
                    start_date:  document.getElementById('startDate').value,
                    end_date:    document.getElementById('endDate').value,
                    interval:    document.getElementById('interval').value,
                    recalculate: recalculate,
                })
            });
            const data = await resp.json();
            if (myRun !== _rtpOptRun) return; // superseded by a newer run
            if (!data.success) {
                window.showNotification(data.error || 'Optimisation failed', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }
            // Served straight from cache → render immediately
            if (data.from_cache) {
                _rtpOptAbort = null;
                renderOptResults(data);
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }
            // Long-running task: poll status endpoint until complete
            _pollRtpOptimise(data.task_id, activeBtn, origText, Date.now(), myRun);
        } catch (err) {
            if (err && err.name === 'AbortError') return; // cancelled on purpose
            console.error('Optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
        }
    }

    function _pollRtpOptimise(taskId, activeBtn, origText, startMs, myRun) {
        const MAX_WAIT_MS = 10 * 60 * 1000; // 10 min hard stop

        function tick() {
            if (myRun !== _rtpOptRun) return; // cancelled / superseded — stop polling

            const elapsed = Math.round((Date.now() - startMs) / 1000);
            if (activeBtn) activeBtn.textContent = `⏳ ${elapsed}s…`;

            if (Date.now() - startMs > MAX_WAIT_MS) {
                window.showNotification('Optimisation timed out — try a shorter date range', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }

            fetch(`/api/backtest/rtp/optimise/status/${taskId}`)
                .then(r => r.json())
                .then(data => {
                    if (myRun !== _rtpOptRun) return; // superseded while awaiting response
                    if (data.status === 'running') {
                        setTimeout(tick, 2000);
                        return;
                    }
                    _rtpOptAbort = null;
                    if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                    if (!data.success || data.status === 'error') {
                        window.showNotification(data.error || 'Optimisation failed', 'error');
                        return;
                    }
                    renderOptResults(data);
                })
                .catch(err => {
                    if (myRun !== _rtpOptRun) return; // cancelled — ignore
                    console.error('RTP poll error:', err);
                    setTimeout(tick, 3000); // retry on transient network error
                });
        }

        setTimeout(tick, 2000); // first check after 2s
    }

    function applyOptResult(r) {
        const entryMode = document.getElementById('rtpEntryMode');
        const useAdx    = document.getElementById('rtpUseAdx');
        const adxThresh = document.getElementById('rtpAdxThresh');
        const sl        = document.getElementById('rtpSL');
        const tgt       = document.getElementById('rtpTarget');
        if (entryMode) entryMode.value = r.entry_mode;
        if (useAdx)    useAdx.checked  = r.use_adx;
        if (adxThresh && r.adx_thresh != null) adxThresh.value = r.adx_thresh;
        if (sl)        sl.value        = r.sl_points;
        if (tgt)       tgt.value       = r.tgt_points;
        if (window.showNotification) {
            window.showNotification(
                `Applied: ${r.entry_mode}  ·  SL ${r.sl_points}  ·  TGT ${r.tgt_points}`, 'success'
            );
        }
    }

    // ── VWAP Optimise ─────────────────────────────────────────────────
    function renderVwapOptResults(data) {
        const panel     = document.getElementById('vwapOptimisePanel');
        const tbody     = document.getElementById('vwapOptTableBody');
        const metaEl    = document.getElementById('vwapOptMeta');
        const recalcBtn = document.getElementById('vwapRecalcOptBtn');

        if (metaEl) {
            let meta = `${data.total_combos_tested} combos · ${data.symbol} · ${data.interval}`;
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        if (tbody) {
            tbody.innerHTML = (data.results || []).map((r, i) => {
                const pnlFmt = (r.total_pnl >= 0 ? '+' : '') + r.total_pnl.toFixed(1) + ' pts';
                const ddFmt  = r.max_drawdown != null ? r.max_drawdown.toFixed(1) : '—';
                const wr     = r.total_trades > 0 ? ((r.wins / r.total_trades) * 100).toFixed(0) : '0';
                return `
                <tr class="${i === 0 ? 'opt-best' : ''}">
                    <td>${i + 1}</td>
                    <td>${r.min_gap}</td>
                    <td>${r.sl_points}</td>
                    <td>${r.tp_points}</td>
                    <td>${r.total_trades}</td>
                    <td>${wr}%</td>
                    <td class="${r.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${pnlFmt}</td>
                    <td>${(r.profit_factor || 0).toFixed(2)}</td>
                    <td class="pnl-negative">${ddFmt}</td>
                    <td><button class="btn-opt-use" data-idx="${i}">Use</button></td>
                </tr>`;
            }).join('');

            tbody.querySelectorAll('.btn-opt-use').forEach(btn => {
                btn.addEventListener('click', () => applyVwapOptResult(data.results[parseInt(btn.dataset.idx)]));
            });
        }

        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyVwapOptResult(data.best);
    }

    async function runVwapOptimise(recalculate) {
        const symbol = symbolSearch.value.trim().toUpperCase();
        if (!symbol) { window.showNotification('Please select a symbol', 'warning'); return; }

        const panel     = document.getElementById('vwapOptimisePanel');
        const recalcBtn = document.getElementById('vwapRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';

        try {
            const resp = await fetch('/api/backtest/vwap/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol,
                    start_date:  '2017-01-01',
                    end_date:    document.getElementById('endDate').value,
                    interval:    document.getElementById('interval').value,
                    recalculate,
                })
            });
            const data = await resp.json();
            if (!data.success) { window.showNotification(data.error || 'Optimisation failed', 'error'); return; }
            renderVwapOptResults(data);
        } catch (err) {
            console.error('VWAP optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
        } finally {
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
        }
    }

    function applyVwapOptResult(r) {
        const minGap = document.getElementById('vwapMinGap');
        const tp     = document.getElementById('vwapTP');
        const sl     = document.getElementById('vwapSL');
        if (minGap) minGap.value = r.min_gap;
        if (tp)     tp.value     = r.tp_points;
        if (sl)     sl.value     = r.sl_points;
        if (window.showNotification) {
            window.showNotification(
                `Applied: Gap ${r.min_gap}  ·  SL ${r.sl_points}  ·  TGT ${r.tp_points}  ·  Win% ${((r.wins / r.total_trades) * 100).toFixed(0)}%`, 'success'
            );
        }
    }

    const vwapRecalcBtn = document.getElementById('vwapRecalcOptBtn');
    if (vwapRecalcBtn) vwapRecalcBtn.addEventListener('click', () => runVwapOptimise(true));

    // ── 2nd 30-Sec Candle Optimise (Find Best Params) ─────────────────
    function renderScOptResults(data) {
        const panel     = document.getElementById('secondCandleOptimisePanel');
        const tbody     = document.getElementById('scOptTableBody');
        const metaEl    = document.getElementById('scOptMeta');
        const recalcBtn = document.getElementById('scRecalcOptBtn');

        if (metaEl) metaEl.textContent = `${data.total_combos_tested} combos · ${data.symbol} · ${data.interval}`;

        const dirLabel = d => d === 'long' ? 'Buy' : (d === 'short' ? 'Sell' : 'Buy & Sell');

        if (tbody) {
            tbody.innerHTML = (data.results || []).map((r, i) => {
                const pnlFmt = (r.total_pnl >= 0 ? '+' : '') + r.total_pnl.toFixed(1) + ' pts';
                const ddFmt  = r.max_drawdown != null ? r.max_drawdown.toFixed(1) : '—';
                const wr     = r.total_trades > 0 ? ((r.wins / r.total_trades) * 100).toFixed(0) : '0';
                return `
                <tr class="${i === 0 ? 'opt-best' : ''}">
                    <td>${i + 1}</td>
                    <td>${r.candle_index}</td>
                    <td>1:${r.rr_ratio}</td>
                    <td>${dirLabel(r.direction)}</td>
                    <td>${r.total_trades}</td>
                    <td>${wr}%</td>
                    <td class="${r.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${pnlFmt}</td>
                    <td>${(r.profit_factor || 0).toFixed(2)}</td>
                    <td class="pnl-negative">${ddFmt}</td>
                    <td><button class="btn-opt-use" data-idx="${i}">Use</button></td>
                </tr>`;
            }).join('');

            tbody.querySelectorAll('.btn-opt-use').forEach(btn => {
                btn.addEventListener('click', () => applyScOptResult(data.results[parseInt(btn.dataset.idx)]));
            });
        }

        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyScOptResult(data.best);
    }

    async function runScOptimise(recalculate) {
        const symbol = symbolSearch.value.trim().toUpperCase();
        if (!symbol) { window.showNotification('Please select a symbol', 'warning'); return; }

        const panel     = document.getElementById('secondCandleOptimisePanel');
        const recalcBtn = document.getElementById('scRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';

        const exitTime = (document.getElementById('scExitTime')?.value || '15:25').split(':');
        try {
            const resp = await fetch('/api/backtest/second-candle/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol,
                    start_date:  document.getElementById('startDate').value,
                    end_date:    document.getElementById('endDate').value,
                    interval:    document.getElementById('interval').value,
                    exit_hour:   parseInt(exitTime[0] || 15),
                    exit_minute: parseInt(exitTime[1] || 25),
                })
            });
            const data = await resp.json();
            if (!data.success) { window.showNotification(data.error || 'Optimisation failed', 'error'); return; }
            renderScOptResults(data);
        } catch (err) {
            console.error('2nd Candle optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
        } finally {
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
        }
    }

    function applyScOptResult(r) {
        const ci  = document.getElementById('scCandleIndex');
        const rr  = document.getElementById('scRrRatio');
        const dir = document.getElementById('scDirection');
        if (ci) ci.value = r.candle_index;
        if (rr) rr.value = r.rr_ratio;
        if (dir) dir.value = r.direction;
        if (window.showNotification) {
            window.showNotification(
                `Applied: Candle ${r.candle_index}  ·  1:${r.rr_ratio}  ·  ${r.direction}  ·  Win% ${((r.wins / r.total_trades) * 100).toFixed(0)}%`, 'success'
            );
        }
    }

    const scRecalcBtn = document.getElementById('scRecalcOptBtn');
    if (scRecalcBtn) scRecalcBtn.addEventListener('click', () => runScOptimise(true));

    const optimiseBtn   = document.getElementById('runOptimiseBtn');
    const recalcOptBtn  = document.getElementById('recalculateOptBtn');
    if (optimiseBtn)  optimiseBtn.addEventListener('click', () => {
        const strat = strategySelect ? strategySelect.value : 'rtp';
        if (strat === 'swing_momentum') _runSmOptimise(false);
        else if (strat === 'vwap')      runVwapOptimise(false);
        else if (strat === 'second_candle') runScOptimise(false);
        else                            runOptimise(false);
    });
    if (recalcOptBtn) recalcOptBtn.addEventListener('click', () => runOptimise(true));

    // ── Swing Momentum Optimise ──────────────────────────────────────────
    const smRecalcOptBtn = document.getElementById('smRecalcOptBtn');
    if (smRecalcOptBtn) smRecalcOptBtn.addEventListener('click', () => _runSmOptimise(true));

    const smGoLiveBtnEl = document.getElementById('smGoLiveBtn');
    if (smGoLiveBtnEl) smGoLiveBtnEl.addEventListener('click', _smGoLiveFromForm);

    async function _runSmOptimise(recalculate) {
        const panel      = document.getElementById('smOptimisePanel');
        const recalcBtn  = document.getElementById('smRecalcOptBtn');
        const activeBtn  = recalculate ? recalcBtn : optimiseBtn;
        const origText   = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ 0s…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';

        try {
            const resp = await fetch('/api/backtest/swing-momentum/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    start_date:     document.getElementById('startDate').value,
                    end_date:       document.getElementById('endDate').value,
                    investment:     parseFloat(document.getElementById('smInvestment')?.value || '100000'),
                    rebalance_freq: document.getElementById('smRebalFreq')?.value || 'monthly',
                    recalculate:    recalculate,
                })
            });
            const data = await resp.json();
            if (!data.success) {
                window.showNotification(data.error || 'Optimisation failed', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }
            // Cached result comes back immediately — render straight away
            if (data.from_cache) {
                _renderSmOptResults(data);
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }
            // Long-running task: poll status endpoint until complete
            _pollSmOptimise(data.task_id, activeBtn, origText, Date.now());
        } catch (err) {
            console.error('SM Optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
        }
    }

    function _pollSmOptimise(taskId, activeBtn, origText, startMs) {
        const MAX_WAIT_MS = 10 * 60 * 1000; // 10 min hard stop

        function tick() {
            const elapsed = Math.round((Date.now() - startMs) / 1000);
            if (activeBtn) activeBtn.textContent = `⏳ ${elapsed}s…`;

            if (Date.now() - startMs > MAX_WAIT_MS) {
                window.showNotification('Optimisation timed out — try a shorter date range', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }

            fetch(`/api/backtest/swing-momentum/optimise/status/${taskId}`)
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'running') {
                        setTimeout(tick, 2000);
                        return;
                    }
                    if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                    if (!data.success || data.status === 'error') {
                        window.showNotification(data.error || 'Optimisation failed', 'error');
                        return;
                    }
                    _renderSmOptResults(data);
                })
                .catch(err => {
                    console.error('SM poll error:', err);
                    setTimeout(tick, 3000); // retry on transient network error
                });
        }

        setTimeout(tick, 2000); // first check after 2s
    }

    function _renderSmOptResults(data) {
        const panel     = document.getElementById('smOptimisePanel');
        const tbody     = document.getElementById('smOptTableBody');
        const metaEl    = document.getElementById('smOptMeta');
        const recalcBtn = document.getElementById('smRecalcOptBtn');
        const rtpPanel  = document.getElementById('rtpOptimisePanel');

        if (rtpPanel) rtpPanel.style.display = 'none';

        const _smIdxShort = {
            'NIFTY 500': 'Nifty 500', 'NIFTY 200': 'Nifty 200',
            'NIFTY SMALLCAP 250': 'SC 250', 'NIFTY SMALLCAP 500': 'SC 500',
            'NIFTY MICROCAP 250': 'MC 250', 'NIFTY LARGEMIDCAP 250': 'LMC 250',
            'NIFTY MIDSMALLCAP 400': 'MSC 400', 'NIFTY MIDSMALLCAP 400': 'MSC 400',
        };

        if (metaEl) {
            const freqLabel = (data.rebalance_freq || 'monthly');
            const freqDisp  = freqLabel.charAt(0).toUpperCase() + freqLabel.slice(1);
            let meta = `3 indices · ${freqDisp} · ${data.total_combos_tested} combos · ${data.start_date} → ${data.end_date}`;
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        if (tbody) {
            tbody.innerHTML = (data.results || []).map((r, i) => {
                const retFmt  = (r.total_return_pct >= 0 ? '+' : '') + r.total_return_pct.toFixed(1) + '%';
                const cagrFmt = (r.cagr_pct >= 0 ? '+' : '') + r.cagr_pct.toFixed(1) + '%';
                const mddFmt  = r.max_drawdown_pct.toFixed(1) + '%';
                const idxLbl  = _smIdxShort[r.index] || r.index;
                return `
                <tr class="${i === 0 ? 'opt-best' : ''}">
                    <td>${i + 1}</td>
                    <td style="white-space:nowrap">${idxLbl}</td>
                    <td>${r.top_n}</td>
                    <td>${r.exit_rank}</td>
                    <td class="${r.total_return_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}">${retFmt}</td>
                    <td class="${r.cagr_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}">${cagrFmt}</td>
                    <td class="pnl-negative">${mddFmt}</td>
                    <td>${r.score.toFixed(2)}</td>
                    <td><button class="btn-opt-use" data-idx="${i}">Use</button></td>
                </tr>`;
            }).join('');

            tbody.querySelectorAll('.btn-opt-use').forEach(btn => {
                btn.addEventListener('click', () => {
                    _applySmOptResult(data.results[parseInt(btn.dataset.idx)]);
                });
            });
        }

        if (panel) panel.style.display = '';
        if (recalcBtn) recalcBtn.style.display = '';
    }

    // Open the Go Live confirmation popup, pre-filled from the params form.
    function _smGoLiveFromForm() {
        const index      = (document.getElementById('smIndex')      || {}).value || 'NIFTY 500';
        const topN       = parseInt((document.getElementById('smTopN')       || {}).value) || 10;
        const exitRank   = parseInt((document.getElementById('smExitRank')   || {}).value) || 50;
        const freq       = (document.getElementById('smRebalFreq')  || {}).value || 'monthly';
        const investment = parseFloat((document.getElementById('smInvestment') || {}).value) || 100000;
        const monthlyAdd = parseFloat((document.getElementById('smMonthlyAdd') || {}).value) || 0;
        _smOpenGoLiveModal({ index, topN, exitRank, freq, investment, monthlyAdd });
    }

    function _smOpenGoLiveModal(p) {
        document.getElementById('smGoLiveModal')?.remove();

        const idxOpts = ['NIFTY 500', 'NIFTY 200', 'NIFTY SMALLCAP 250', 'NIFTY MICROCAP 250',
                         'NIFTY LARGEMIDCAP 250', 'NIFTY MIDSMALLCAP 400']
            .map(v => `<option value="${v}" ${v === p.index ? 'selected' : ''}>${v.replace('NIFTY ', 'Nifty ')}</option>`).join('');
        const freqOpts = ['monthly', 'weekly', 'quarterly']
            .map(v => `<option value="${v}" ${v === p.freq ? 'selected' : ''}>${v[0].toUpperCase() + v.slice(1)}</option>`).join('');

        const modal = document.createElement('div');
        modal.id = 'smGoLiveModal';
        modal.className = 'sm-gl-overlay';
        modal.innerHTML = `
<div class="sm-gl-box">
    <div class="sm-gl-hdr">
        <span class="sm-gl-title">🚀 Go Live — Swing Momentum</span>
        <button class="sm-gl-close" onclick="document.getElementById('smGoLiveModal').remove()">✕</button>
    </div>
    <div class="sm-gl-body">
        <div class="sm-gl-grid">
            <label class="sm-gl-field"><span>Investment (₹)</span>
                <input type="number" id="glInvestment" value="${p.investment}" step="10000" min="10000"></label>
            <label class="sm-gl-field"><span>Monthly SIP (₹)</span>
                <input type="number" id="glMonthlyAdd" value="${p.monthlyAdd}" step="1000" min="0" placeholder="0 = disabled"></label>
            <label class="sm-gl-field"><span>Index</span>
                <select id="glIndex">${idxOpts}</select></label>
            <label class="sm-gl-field"><span>Hold Top N</span>
                <input type="number" id="glTopN" value="${p.topN}" min="1" max="30"></label>
            <label class="sm-gl-field"><span>Exit if Rank &gt;</span>
                <input type="number" id="glExitRank" value="${p.exitRank}" min="11" max="200"></label>
            <label class="sm-gl-field"><span>Rebalance</span>
                <select id="glRebalFreq">${freqOpts}</select></label>
            <label class="sm-gl-field sm-gl-field-wide"><span>Broker (optional)</span>
                <select id="glBroker"><option value="">None — track only (no real orders)</option></select></label>
        </div>
        <div class="sm-gl-note" id="glBrokerNote">
            With no broker, entries are tracked at current ranking prices. Pick a broker to place
            <strong>CNC MARKET</strong> buy orders and record the actual average fill price.
        </div>
        <div class="sm-gl-summary" id="glSummary" style="display:none"></div>
    </div>
    <div class="sm-gl-footer">
        <button class="sm-gl-btn sm-gl-cancel" onclick="document.getElementById('smGoLiveModal').remove()">Cancel</button>
        <button class="sm-gl-btn sm-gl-confirm" id="glConfirmBtn">Confirm &amp; Go Live</button>
    </div>
</div>`;
        modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
        document.body.appendChild(modal);

        // Populate broker dropdown
        fetch('/api/available-brokers').then(r => r.json()).then(d => {
            const sel = document.getElementById('glBroker');
            if (!sel || !d || !d.brokers) return;
            d.brokers.filter(b => b.active !== false).forEach(b => {
                const opt  = document.createElement('option');
                opt.value  = b.instance_num;
                opt.dataset.type = b.broker_type || '';
                opt.dataset.name = b.name || b.broker_type || '';
                const conn = b.is_logged_in ? '' : ' — not connected';
                opt.textContent  = `${b.name || b.broker_type} (${(b.broker_type || '').toUpperCase()})${conn}`;
                opt.disabled = !b.is_logged_in;
                sel.appendChild(opt);
            });
        }).catch(() => {});

        document.getElementById('glConfirmBtn').addEventListener('click', _smSubmitGoLive);
    }

    function _smSubmitGoLive() {
        const index      = document.getElementById('glIndex').value;
        const topN       = parseInt(document.getElementById('glTopN').value) || 10;
        const exitRank   = parseInt(document.getElementById('glExitRank').value) || 50;
        const freq       = document.getElementById('glRebalFreq').value;
        const investment = parseFloat(document.getElementById('glInvestment').value) || 100000;
        const monthlyAdd = parseFloat(document.getElementById('glMonthlyAdd').value) || 0;

        const brokerSel  = document.getElementById('glBroker');
        const brokerOpt  = brokerSel.selectedOptions[0];
        const brokerInst = brokerSel.value;

        const idxLbl  = index.replace('NIFTY ', 'Nifty ');
        const freqLbl = freq.charAt(0).toUpperCase() + freq.slice(1);
        const label   = `${idxLbl} · ${freqLbl} · Top ${topN} · Exit >${exitRank}`;

        const payload = { index, top_n: topN, exit_rank: exitRank, rebalance_freq: freq,
                          investment, monthly_add: monthlyAdd, monthly_add_type: 'static',
                          label, start_date: '2025-01-01' };
        if (brokerInst) {
            payload.broker_instance = brokerInst;
            payload.broker_type     = brokerOpt?.dataset.type || '';
            payload.broker_name     = brokerOpt?.dataset.name || '';
        }

        const cbtn = document.getElementById('glConfirmBtn');
        cbtn.disabled = true;
        cbtn.textContent = brokerInst ? 'Placing orders…' : 'Saving…';

        fetch('/api/algo/swing-momentum/configs', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        })
        .then(r => r.json())
        .then(d => {
            if (!d.success) {
                cbtn.disabled = false; cbtn.textContent = 'Confirm & Go Live';
                window.showNotification('Failed to save config', 'error');
                return;
            }
            const bs = d.broker_summary;
            if (bs) {
                const sumEl = document.getElementById('glSummary');
                const cls   = bs.placed ? 'sm-gl-summary-ok' : 'sm-gl-summary-err';
                sumEl.className = 'sm-gl-summary ' + cls;
                sumEl.style.display = 'block';
                sumEl.innerHTML = bs.placed
                    ? `✅ Placed ${bs.placed} order${bs.placed > 1 ? 's' : ''} on ${bs.broker || 'broker'}` +
                      (bs.failed ? ` · ⚠ ${bs.failed} failed` : '') + '. Redirecting…'
                    : `⚠ ${bs.error || 'Order placement failed'}. Config saved as track-only.`;
            }
            window.showNotification('Saved to Algo → Swing Momentum', 'success');
            setTimeout(() => { window.location.href = '/algo#swing-momentum'; }, bs ? 1400 : 800);
        })
        .catch(() => {
            cbtn.disabled = false; cbtn.textContent = 'Confirm & Go Live';
            window.showNotification('Request failed', 'error');
        });
    }

    function _applySmOptResult(r) {
        if (!r) return;
        const idxEl  = document.getElementById('smIndex');
        const topNEl = document.getElementById('smTopN');
        const rankEl = document.getElementById('smExitRank');
        const freqEl = document.getElementById('smRebalFreq');
        if (idxEl  && r.index)         idxEl.value  = r.index;
        if (topNEl)                    topNEl.value = r.top_n;
        if (rankEl)                    rankEl.value = r.exit_rank;
        if (freqEl)                    freqEl.value = r.rebalance_freq;
        if (window.showNotification) {
            const freqLbl = r.rebalance_freq.charAt(0).toUpperCase() + r.rebalance_freq.slice(1);
            const idxLbl  = (r.index || '').replace('NIFTY ', 'Nifty ');
            window.showNotification(
                `Applied: ${idxLbl}  ·  ${freqLbl}  ·  Top ${r.top_n}  ·  Exit >${r.exit_rank}`, 'success'
            );
        }
    }

    // ── Swing Momentum Portfolio helpers ─────────────────────────────────────

    const _smOrigLabels = {};  // backup of stat card labels replaced by swing_momentum mode

    function _smSetCard(valId, newLbl, newVal, extraCls) {
        const valEl = document.getElementById(valId);
        if (!valEl) return;
        const lblEl = valEl.previousElementSibling;
        if (lblEl && lblEl.classList.contains('stat-card__lbl')) {
            if (!_smOrigLabels[valId]) _smOrigLabels[valId] = lblEl.textContent;
            lblEl.textContent = newLbl;
        }
        valEl.textContent = newVal;
        valEl.className   = 'stat-card__val' + (extraCls ? ' ' + extraCls : '');
    }

    function _restoreSmStatLabels() {
        Object.entries(_smOrigLabels).forEach(([id, lbl]) => {
            const el = document.getElementById(id);
            if (el && el.previousElementSibling) el.previousElementSibling.textContent = lbl;
        });
    }

    function _displaySwingMomentumResults(data) {
        const { summary, portfolio_curve } = data;
        const tr           = summary.total_return_pct;
        const totalInvested = summary.total_invested || summary.start_value;
        const hasSip        = summary.monthly_add > 0;

        // Row 1: repurpose standard stat cards
        _smSetCard('statTotalTrades', 'Rebalances',  summary.rebalance_count,    null);
        _smSetCard('statWins',        'Buy Trades',  summary.total_buy_trades,    null);
        _smSetCard('statLosses',      'Sell Trades', summary.total_sell_trades,   null);
        // Show total invested (= initial + SIP adds) instead of rotations in row-1 slot
        _smSetCard('statWinRate',
            hasSip ? 'Total Invested' : 'Rotations',
            hasSip ? '₹' + Math.round(totalInvested).toLocaleString('en-IN')
                   : String(summary.total_rotations),
            null);
        _smSetCard('statTotalPnl',    '₹ End Value',
            '₹' + Math.round(summary.end_value).toLocaleString('en-IN'),
            tr >= 0 ? 'stat-val-green' : 'stat-val-red');
        _smSetCard('statOutcome',     'Net Outcome', tr >= 0 ? 'PROFIT' : 'LOSS',
            tr >= 0 ? 'stat-val-green' : 'stat-val-red');

        // Row 2 (RTP): hide
        const rtpRow = document.getElementById('rtpStatsRow');
        if (rtpRow) rtpRow.style.display = 'none';

        // Row 3 (SM-specific): show
        const smRow = document.getElementById('smStatsRow');
        if (smRow) smRow.style.display = '';
        const _s = (id, val, cls) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = val;
            if (cls) el.className = 'stat-card__val ' + cls;
        };
        _s('smStatReturn',    (tr >= 0 ? '+' : '') + tr.toFixed(2) + '%',
            tr >= 0 ? 'stat-val-green' : 'stat-val-red');
        _s('smStatCagr',      (summary.cagr_pct >= 0 ? '+' : '') + summary.cagr_pct.toFixed(2) + '%',
            summary.cagr_pct >= 0 ? 'stat-val-green' : 'stat-val-red');
        _s('smStatMdd',       summary.max_drawdown_pct.toFixed(2) + '%', 'stat-val-red');
        // Always show rotations here (row 3) even when row-1 slot is repurposed
        _s('smStatRotations', String(summary.total_rotations), null);
        _s('smStatAvgHold',   summary.avg_holding_days + ' d', null);

        // Update Avg Hold label to show SIP amount when active
        const smAvgHoldLbl = document.querySelector('#smStatsRow .stat-card:last-child .stat-card__lbl');
        if (smAvgHoldLbl) smAvgHoldLbl.textContent = hasSip
            ? `SIP ₹${Math.round(summary.monthly_add).toLocaleString('en-IN')}/mo · Avg Hold`
            : 'Avg Hold (days)';

        // Equity curve from portfolio_curve — pass total_invested as basis
        _renderSmEquityCurve(portfolio_curve, totalInvested);

        // Period breakdown not applicable for SM
        const periodSec = document.getElementById('periodBreakdownSection');
        if (periodSec) periodSec.style.display = 'none';

        // Trades table
        _renderSmTable(data.trades);

        // Show result sections
        resultsArea.style.display = 'block';
        const btTradesSec   = document.getElementById('btTradesSection');
        const btPlaceholder = document.getElementById('btRightPlaceholder');
        if (btTradesSec)   btTradesSec.style.display   = '';
        if (btPlaceholder) btPlaceholder.style.display = 'none';
    }

    function _renderSmEquityCurve(curve, investment) {
        const section = document.getElementById('equityCurveSection');
        if (!section || !curve || !curve.length) {
            if (section) section.style.display = 'none';
            return;
        }

        const labels = curve.map(p => p.date.slice(0, 7));   // YYYY-MM
        const values = curve.map(p => p.value);
        const finalV = values[values.length - 1] || investment;
        const diff   = finalV - investment;
        const isProfit = diff >= 0;

        const finalEl = document.getElementById('equityCurveFinalPnl');
        if (finalEl) {
            const pct = investment ? ((diff / investment) * 100).toFixed(1) : '0';
            finalEl.textContent = (diff >= 0 ? '+' : '') + '₹' + Math.abs(Math.round(diff)).toLocaleString('en-IN') +
                '  (' + (diff >= 0 ? '+' : '') + pct + '%)';
            finalEl.style.color = isProfit ? '#00c853' : '#ff1744';
        }

        const fmtY = v => {
            const abs = Math.abs(v);
            if (abs >= 100000) return '₹' + (v / 100000).toFixed(1) + 'L';
            if (abs >= 1000)   return '₹' + (v / 1000).toFixed(0)   + 'K';
            return '₹' + v;
        };

        if (_equityChart) { _equityChart.destroy(); _equityChart = null; }
        const ctx = document.getElementById('equityCurveChart');
        if (!ctx) return;

        const lineColor = isProfit ? '#2962ff' : '#ff1744';
        const fillColor = isProfit ? 'rgba(41,98,255,0.07)' : 'rgba(255,23,68,0.06)';

        _equityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Portfolio Value',
                    data: values,
                    borderColor: lineColor,
                    backgroundColor: fillColor,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                    pointHoverRadius: 5,
                    pointBackgroundColor: lineColor,
                    borderWidth: 2,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: items => curve[items[0].dataIndex]?.date || '',
                            label: item => {
                                const v   = item.raw;
                                const pt  = curve[item.dataIndex];
                                const inv = (pt && pt.invested != null) ? pt.invested : investment;
                                const chg = v - inv;
                                const lines = [
                                    '  Value:    ₹' + Math.round(v).toLocaleString('en-IN'),
                                    '  P&L:      ' + (chg >= 0 ? '+' : '') + '₹' + Math.round(chg).toLocaleString('en-IN'),
                                ];
                                if (pt && pt.invested != null && pt.invested !== investment) {
                                    lines.splice(1, 0, '  Invested: ₹' + Math.round(inv).toLocaleString('en-IN'));
                                }
                                return lines;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: { maxTicksLimit: 15, color: '#999', font: { size: 11 }, autoSkip: true },
                        grid:  { color: 'rgba(0,0,0,0.04)' },
                    },
                    y: {
                        ticks: { color: '#999', font: { size: 11 }, callback: fmtY },
                        grid:  { color: 'rgba(0,0,0,0.05)' },
                    }
                }
            }
        });

        section.style.display = '';
    }

    function _renderSmTable(trades) {
        const thead = document.querySelector('.trades-table thead tr');
        const tbody = document.getElementById('tradesBody');
        if (thead) {
            thead.innerHTML = `
                <th>Date</th>
                <th>Symbol</th>
                <th>Action</th>
                <th>Qty</th>
                <th>Price</th>
                <th>₹ Value</th>
                <th>Reason</th>
                <th>Rank</th>
                <th>P&amp;L</th>`;
        }
        if (!tbody) return;
        if (!trades || !trades.length) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:20px;">No trades generated</td></tr>';
            return;
        }
        const fmtRs = v => '₹' + Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        tbody.innerHTML = [...trades].map(t => {
            const isBuy  = t.action === 'BUY';
            const rowBg  = isBuy ? 'background:rgba(34,197,94,0.04)' : 'background:rgba(239,68,68,0.04)';
            const pnlHtml = t.pnl != null
                ? `<span class="${t.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${t.pnl >= 0 ? '+' : ''}${fmtRs(t.pnl)}</span>`
                : '—';
            return `
                <tr style="${rowBg}">
                    <td>${t.date || '—'}</td>
                    <td style="font-weight:600">${t.symbol || '—'}</td>
                    <td><span class="badge ${isBuy ? 'badge-buy' : 'badge-sell'}">${t.action}</span></td>
                    <td>${t.qty ?? 0}</td>
                    <td>${fmtRs(t.price || 0)}</td>
                    <td>${fmtRs(t.investment || 0)}</td>
                    <td>${t.reason || '—'}</td>
                    <td>${t.rank != null ? t.rank : '—'}</td>
                    <td>${pnlHtml}</td>
                </tr>`;
        }).join('');
    }
});
